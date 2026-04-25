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
        out.update({"model": use_model, "fallback": True})
        return out
    except Exception as e:
        logger.warning(f"summarize_task: 예외 → fallback ({e})")
        out = _fallback_summary(task_content, resolution_note)
        out.update({"model": use_model, "fallback": True})
        return out

    parsed = _parse_json_loose(raw)
    occ = (parsed.get("occurred_text") or "").strip()
    res = (parsed.get("resolved_text") or "").strip()

    if not occ and not res:
        logger.warning(f"summarize_task: 응답 파싱 실패 → fallback. raw={raw[:200]!r}")
        out = _fallback_summary(task_content, resolution_note)
        out.update({"model": use_model, "fallback": True})
        return out

    return {
        "occurred_text": occ or _fallback_summary(task_content, None)["occurred_text"],
        "resolved_text": res or _fallback_summary(None, resolution_note)["resolved_text"],
        "model": use_model,
        "fallback": False,
    }
