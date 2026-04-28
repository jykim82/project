"""
root_cause_classifier.py — 보고서 항목 자유 텍스트 → 근본원인 코드 사후 분류

설계: 사용자 입력 부담 X. LLM 이 보고서 항목의 자유 텍스트
(occurred_text/resolved_text/symptom/cause/key_issues) 를 입력으로
tb_root_cause_taxonomy 의 코드 0~3개를 추론한다.

호출 시점:
- 항목 수동 [재분류] 버튼 (즉시)
- 야간 배치 (recently 분류 안 된 항목 일괄)
- 새 항목 생성 시 후속 비동기 (선택)

원칙:
- 명백히 부합하는 코드만 (확신 없으면 빈 배열 또는 UNKNOWN)
- 새로운 사실 생성 절대 금지
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


_SYSTEM_PROMPT = """당신은 상수도 운영 보고서 항목을 사전정의된 근본원인 코드로 분류하는 분류기입니다.

규칙:
1. 입력 텍스트에 **명백히 부합**하는 코드만 선택 (0~3개).
2. 텍스트 사실에 없는 코드는 절대 추가 금지.
3. 같은 근본원인이 여러 코드에 매칭되면 가장 구체적인 1개만.
4. 매칭이 약하면 빈 배열 [] (UNKNOWN 도 가능).
5. 출력은 JSON 한 줄: {"codes": ["CODE1","CODE2"]}.
   다른 설명·코드블록·마크다운 절대 금지.
"""


def _build_prompt(taxonomy: list[dict], item_text: str) -> str:
    lines = []
    lines.append("[근본원인 코드 후보]")
    for tx in taxonomy:
        hint = f" (힌트: {tx['hint']})" if tx.get("hint") else ""
        lines.append(f"  - {tx['code']}: {tx['label']}{hint}")
    lines.append("")
    lines.append("[보고서 항목 자유 텍스트]")
    lines.append(item_text)
    lines.append("")
    lines.append("위 텍스트에 명백히 부합하는 코드 0~3개를 JSON 으로 출력하세요.")
    return "\n".join(lines)


def _format_item_text(item: dict) -> str:
    """tb_report_item 행에서 분류기 입력 텍스트 조립."""
    parts = []
    site = item.get("site_name") or ""
    facility = item.get("facility_type") or ""
    eq = item.get("equipment_name") or ""
    cat = item.get("fault_category") or ""
    parts.append(f"시설: {site} ({facility})")
    parts.append(f"설비: {eq}")
    parts.append(f"분류: {cat}")
    if item.get("occurred_text"):
        parts.append(f"발생: {item['occurred_text']}")
    if item.get("symptom"):
        parts.append(f"현상: {item['symptom']}")
    if item.get("cause"):
        parts.append(f"원인: {item['cause']}")
    if item.get("resolved_text"):
        parts.append(f"조치: {item['resolved_text']}")
    if item.get("key_issues"):
        parts.append(f"주요사항: {item['key_issues']}")
    return "\n".join(parts)


def _parse_codes(raw: str, valid_codes: set[str]) -> list[str]:
    if not raw:
        return []
    m = re.search(r"\{.*?\}", raw, re.DOTALL)
    if not m:
        return []
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    codes = obj.get("codes")
    if not isinstance(codes, list):
        return []
    out: list[str] = []
    for c in codes:
        if isinstance(c, str) and c in valid_codes and c not in out:
            out.append(c)
        if len(out) >= 3:
            break
    return out


def classify_item(
    *,
    item: dict,
    taxonomy: list[dict],
    model: Optional[str] = None,
    timeout: float = 60.0,
) -> dict:
    """단일 보고서 항목을 근본원인 코드로 분류.

    반환: {"codes": ["CODE1", ...], "model": "...", "fallback": bool}
    LLM 호출 실패 시 빈 codes 반환 (절대 잘못된 코드 부여 X).
    """
    valid_codes = {tx["code"] for tx in taxonomy}
    text = _format_item_text(item)

    # 텍스트가 거의 비어있으면 분류 시도 자체 X
    if len(text.strip()) < 20:
        return {"codes": [], "model": None, "fallback": True}

    prompt = _SYSTEM_PROMPT + "\n---\n" + _build_prompt(taxonomy, text)
    use_model = model or get_model()

    try:
        raw = _client.generate(
            prompt=prompt,
            model=use_model,
            num_predict=200,
            timeout=timeout,
        )
    except OllamaConnectionError as e:
        logger.warning(f"classify_item: Ollama 연결 실패 ({e})")
        return {"codes": [], "model": use_model, "fallback": True}
    except Exception as e:
        logger.warning(f"classify_item: 예외 ({e})")
        return {"codes": [], "model": use_model, "fallback": True}

    if not raw or not raw.strip():
        # Ollama 빈 응답 — 모델 부적합 또는 컨텍스트 처리 문제
        logger.warning(f"classify_item: 빈 응답 (model={use_model})")
        return {"codes": [], "model": use_model, "fallback": True}

    codes = _parse_codes(raw, valid_codes)
    return {"codes": codes, "model": use_model, "fallback": False}
