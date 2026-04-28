"""
report_summarizer.py — 보고서 항목 요약 모듈

장애·점검 이력 1건을 입력받아 보고서 본문용 발생/조치 요약을 반환한다.
- ollama_client.OllamaClient.generate() 재사용
- 모델: get_model() (tb_comm_code SITE_SETTING/AI_MODEL 값)
- Zero-Hallucination 원칙 — 입력 사실만 추출, 추측·일반론 금지

사양: docs/report-spec.md §6
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from ollama_client import OllamaClient, OllamaConnectionError
from slm_config import get_model

logger = logging.getLogger(__name__)

_client = OllamaClient()


# ============================================================================
# 약식 정제 (rule-based light format)
#   - 구어체 → 보고서체 단어 치환 (예: "기록해줘" 제거, "났네" → "발생.")
#   - 다중 공백 정리
#   - 마침표·줄바꿈 단위로 문장 분리 후 앞에 "• " 기호 부여
#
# LLM 호출 결과·사용자 입력·fallback 모두에 동일하게 후처리되어
# 보고서 본문이 일관된 양식으로 출력된다.
# ============================================================================

_REPORT_BULLET = "• "

# (정규식 패턴, 치환문) — 순서대로 적용. 보고용 단어 치환.
_PHRASE_NORMALIZATIONS: list[tuple[str, str]] = [
    # 1) 명령형 어미 제거 — 끝부분의 "기록해줘"/"등록해줘"
    (r"\s*(?:기록|등록)\s*해\s*줘\.?\s*$", ""),
    (r"(?:기록|등록)\s*해\s*줘\.?", ""),
    (r"\s*기록\.?\s*$", ""),
    # 2) 종결어미 보고체 변환
    (r"했어요\.?", "함."),
    (r"했어\.?", "함."),
    (r"했네\.?", "함."),
    (r"했지\.?", "함."),
    (r"했음", "함"),  # 이미 보고체에 가까움
    (r"났어요\.?", "발생함."),
    (r"났어\.?", "발생함."),
    (r"났네\.?", "발생함."),
    (r"이야\.?", "임."),
    (r"\s야\.?", "임."),
    (r"이지\.?", "임."),
    (r"라네\.?", "임."),
    # 3) 구어체 → 보고서체 (연결어미/종결어미 분리)
    (r"안\s*돌아감\.?", "동작 불가."),
    (r"안\s*돌아가\.?", "동작 불가."),
    (r"안\s*돼서", "동작하지 않아"),     # 연결어미
    (r"안\s*돼요\.?", "동작 불가."),
    (r"안\s*돼\.", "동작 불가."),
    (r"먹통", "동작 불가"),
]


def apply_phrase_normalization(text: str) -> str:
    """구어체 → 보고서체 단어 치환 + 공백 정리."""
    if not text:
        return ""
    out = text
    for pat, rep in _PHRASE_NORMALIZATIONS:
        out = re.sub(pat, rep, out)
    # 다중 공백 → 단일 (탭 포함)
    out = re.sub(r"[ \t]+", " ", out)
    # 다중 빈 줄 → 1개
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _split_sentences(text: str) -> list[str]:
    """마침표/물음표/느낌표 뒤 공백 또는 명시적 줄바꿈으로 문장 분리."""
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in parts if p.strip()]


def apply_bullet_format(text: str) -> str:
    """각 문장 앞에 '• ' 부여. 이미 기호로 시작하면 유지."""
    if not text:
        return ""
    sentences = _split_sentences(text)
    out = []
    for s in sentences:
        if s.startswith(("•", "·", "○", "▶", "※", "-", "*", "1)", "2)", "3)", "1.", "2.", "3.")):
            out.append(s)
        else:
            out.append(f"{_REPORT_BULLET}{s}")
    return "\n".join(out)


def light_format_report_text(text: str | None, *, normalize_phrases: bool = True) -> str:
    """약식 정제 — 단어 치환 + 줄바꿈 + 기호 부여를 한 번에 적용.

    LLM 응답·사용자 입력·fallback 모두에 동일 적용하여 보고서 일관성 확보.
    """
    if not text:
        return ""
    if normalize_phrases:
        text = apply_phrase_normalization(text)
    return apply_bullet_format(text)


_SYSTEM_PROMPT = """당신은 상수도 운영 보고서 작성을 돕는 보조 도우미입니다.
입력으로 주어진 사실(시설·설비·분류·발생 원문·조치 원문)만으로 두 단락의
보고서 본문을 한국어로 작성합니다.

규칙:
1. 입력에 없는 시간·수치·고유명사·인물명은 절대 만들어내지 않습니다.
2. 일반론·교과서적 설명·예방 권고는 출력하지 않습니다.
3. 발생 단락은 50~120자, 조치 단락은 50~120자.
4. 조치 원문이 비어있으면 조치 단락은 "조치 정보 없음"으로 출력합니다.
5. 출력은 반드시 JSON 한 줄로만 응답합니다:
   {"occurred_text": "<발생 단락>", "resolved_text": "<조치 단락>"}
   다른 설명·코드블록·마크다운 절대 금지.
"""


def _build_user_prompt(
    *,
    site_name: str | None,
    facility_type: str | None,
    equipment_name: str | None,
    fault_category: str | None,
    inspection_type: str | None,
    task_content: str | None,
    resolution_note: str | None,
) -> str:
    lines = []
    lines.append(f"시설: {site_name or '-'} ({facility_type or '-'})")
    lines.append(f"설비: {equipment_name or '-'}")
    cat = fault_category or "-"
    if inspection_type:
        cat = f"{cat} / {inspection_type}"
    lines.append(f"분류: {cat}")
    lines.append("")
    lines.append("[발생 원문]")
    lines.append((task_content or "").strip() or "(없음)")
    lines.append("")
    lines.append("[조치 원문]")
    lines.append((resolution_note or "").strip() or "(없음)")
    lines.append("")
    lines.append("위 사실만으로 발생/조치 두 단락을 JSON 으로 출력하세요.")
    return "\n".join(lines)


def _parse_json_loose(text: str) -> dict:
    """모델 응답에서 JSON 객체 한 개만 추출. 코드블록·잡음 허용."""
    if not text:
        return {}
    # 가장 먼저 등장하는 { ... } 블록 추출 (간이)
    m = re.search(r"\{.*?\}", text, re.DOTALL)
    if not m:
        return {}
    raw = m.group(0)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _fallback_summary(task_content: str | None, resolution_note: str | None) -> dict[str, str]:
    """LLM 호출 실패·파싱 실패 시 원문을 그대로 단락 분할."""
    occ = (task_content or "").strip()
    res = (resolution_note or "").strip()
    return {
        "occurred_text": occ[:200] if occ else "발생 정보 없음",
        "resolved_text": res[:200] if res else "조치 정보 없음",
    }


_REFINE_SYSTEM_PROMPT = """당신은 상수도 운영 보고서 본문을 다듬는 보조 도우미입니다.
입력으로 주어진 [현재 발생 단락] 과 [현재 조치 단락] 은 사용자가 직접 작성·편집한
사실입니다. 정보를 잃지 않고, 표현만 자연스러운 한국어로 정리합니다.

규칙:
1. 입력에 있는 시간·수치·고유명사·장비명·인물명을 절대 누락하지 않습니다.
2. 입력에 없는 내용은 절대 추가하지 않습니다 (추측·일반론·예방 권고 금지).
3. 사용자가 추가한 단편 (예: "LTE모뎀도 통신이상 — 제조사 의뢰중") 은 반드시 포함합니다.
4. 발생 단락과 조치 단락 각 50~250자.
5. 조치 입력이 비어있으면 조치 단락은 "조치 정보 없음".
6. 출력은 JSON 한 줄: {"occurred_text": "...", "resolved_text": "..."}
   다른 설명·코드블록·마크다운 절대 금지.
"""


def _build_refine_prompt(
    *,
    site_name: str | None,
    facility_type: str | None,
    equipment_name: str | None,
    fault_category: str | None,
    inspection_type: str | None,
    current_occurred: str | None,
    current_resolved: str | None,
    original_text: str | None,
) -> str:
    lines = []
    lines.append(f"시설: {site_name or '-'} ({facility_type or '-'})")
    lines.append(f"설비: {equipment_name or '-'}")
    cat = fault_category or "-"
    if inspection_type:
        cat = f"{cat} / {inspection_type}"
    lines.append(f"분류: {cat}")
    lines.append("")
    if original_text:
        lines.append("[원문 (참고용 — 절대 새로 추가하지 말 것)]")
        lines.append(original_text.strip())
        lines.append("")
    lines.append("[현재 발생 단락 — 사용자가 보강·편집한 사실]")
    lines.append((current_occurred or "").strip() or "(없음)")
    lines.append("")
    lines.append("[현재 조치 단락 — 사용자가 보강·편집한 사실]")
    lines.append((current_resolved or "").strip() or "(없음)")
    lines.append("")
    lines.append(
        "위 [현재 ...] 단락의 사실을 모두 보존하면서 자연스러운 한국어로 다듬어 JSON 으로 출력하세요."
    )
    return "\n".join(lines)


def refine_item_summary(
    *,
    current_occurred: str | None,
    current_resolved: str | None,
    original_text: str | None,
    site_name: str | None = None,
    facility_type: str | None = None,
    equipment_name: str | None = None,
    fault_category: str | None = None,
    inspection_type: str | None = None,
    model: Optional[str] = None,
    timeout: float = 60.0,
) -> dict[str, str]:
    """현재 항목 본문(사용자 편집 포함)을 LLM 으로 정제. 사용자 입력 사실은 보존.

    LLM 호출 실패 시 사용자 입력을 그대로 반환 (절대 클리어하지 않음).
    """
    prompt = (
        _REFINE_SYSTEM_PROMPT
        + "\n---\n"
        + _build_refine_prompt(
            site_name=site_name,
            facility_type=facility_type,
            equipment_name=equipment_name,
            fault_category=fault_category,
            inspection_type=inspection_type,
            current_occurred=current_occurred,
            current_resolved=current_resolved,
            original_text=original_text,
        )
    )
    use_model = model or get_model()

    occ_in = (current_occurred or "").strip()
    res_in = (current_resolved or "").strip()

    def _safe_fallback() -> dict[str, str]:
        # 사용자 입력 그대로 반환 — 단, 보고서 일관성 위해 약식 정제는 적용
        return {
            "occurred_text": light_format_report_text(occ_in or "발생 정보 없음"),
            "resolved_text": light_format_report_text(res_in or "조치 정보 없음"),
            "model": use_model,
            "fallback": True,
        }

    try:
        raw = _client.generate(prompt=prompt, model=use_model, num_predict=500, timeout=timeout)
    except OllamaConnectionError as e:
        logger.warning(f"refine_item_summary: Ollama 연결 실패 → 사용자 입력 보존 ({e})")
        return _safe_fallback()
    except Exception as e:
        logger.warning(f"refine_item_summary: 예외 → 사용자 입력 보존 ({e})")
        return _safe_fallback()

    parsed = _parse_json_loose(raw)
    occ_out = (parsed.get("occurred_text") or "").strip()
    res_out = (parsed.get("resolved_text") or "").strip()

    # LLM 응답이 비었거나 사용자 입력 사실을 잃었을 가능성 점검 — 핵심 문구가 누락됐는지
    # (간단 휴리스틱: 입력 텍스트가 길어졌을 때 응답이 입력보다 짧으면 fallback)
    if not occ_out and not res_out:
        logger.warning(f"refine_item_summary: 응답 파싱 실패 → 사용자 입력 보존. raw={raw[:200]!r}")
        return _safe_fallback()

    # 정상 케이스: LLM 결과로 교체. 단 비어있는 칸은 사용자 입력으로 보강 (덮어쓰기 금지)
    final_occ = occ_out or occ_in or "발생 정보 없음"
    final_res = res_out or res_in or "조치 정보 없음"
    return {
        "occurred_text": light_format_report_text(final_occ),
        "resolved_text": light_format_report_text(final_res),
        "model": use_model,
        "fallback": False,
    }


def summarize_task(
    *,
    task_content: str | None,
    resolution_note: str | None = None,
    site_name: str | None = None,
    facility_type: str | None = None,
    equipment_name: str | None = None,
    fault_category: str | None = None,
    inspection_type: str | None = None,
    model: Optional[str] = None,
    timeout: float = 60.0,
) -> dict[str, str]:
    """장애·점검 이력 1건을 발생/조치 두 단락으로 요약한다.

    반환: {"occurred_text": "...", "resolved_text": "...", "model": "...", "fallback": bool}

    LLM 호출이 실패하면 원문을 그대로 잘라 fallback (서비스 정상 진행 보장).
    """
    prompt = (
        _SYSTEM_PROMPT
        + "\n---\n"
        + _build_user_prompt(
            site_name=site_name,
            facility_type=facility_type,
            equipment_name=equipment_name,
            fault_category=fault_category,
            inspection_type=inspection_type,
            task_content=task_content,
            resolution_note=resolution_note,
        )
    )

    use_model = model or get_model()
    try:
        raw = _client.generate(
            prompt=prompt,
            model=use_model,
            num_predict=400,
            timeout=timeout,
        )
    except OllamaConnectionError as e:
        logger.warning(f"summarize_task: Ollama 연결 실패 → fallback ({e})")
        out = _fallback_summary(task_content, resolution_note)
        out["occurred_text"] = light_format_report_text(out["occurred_text"])
        out["resolved_text"] = light_format_report_text(out["resolved_text"])
        out.update({"model": use_model, "fallback": True})
        return out
    except Exception as e:
        logger.warning(f"summarize_task: 예외 → fallback ({e})")
        out = _fallback_summary(task_content, resolution_note)
        out["occurred_text"] = light_format_report_text(out["occurred_text"])
        out["resolved_text"] = light_format_report_text(out["resolved_text"])
        out.update({"model": use_model, "fallback": True})
        return out

    parsed = _parse_json_loose(raw)
    occ = (parsed.get("occurred_text") or "").strip()
    res = (parsed.get("resolved_text") or "").strip()

    if not occ and not res:
        logger.warning(f"summarize_task: 응답 파싱 실패 → fallback. raw={raw[:200]!r}")
        out = _fallback_summary(task_content, resolution_note)
        out["occurred_text"] = light_format_report_text(out["occurred_text"])
        out["resolved_text"] = light_format_report_text(out["resolved_text"])
        out.update({"model": use_model, "fallback": True})
        return out

    return {
        "occurred_text": light_format_report_text(occ or _fallback_summary(task_content, None)["occurred_text"]),
        "resolved_text": light_format_report_text(res or _fallback_summary(None, resolution_note)["resolved_text"]),
        "model": use_model,
        "fallback": False,
    }
