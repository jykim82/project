"""
단일 태그 최신값 AI 해석 API

- POST /tag/latest/explain
  입력: tagsn, current_value (필수) + tag_name (선택)
  → 30일 baseline·HH/LL 임계값 컨텍스트를 조회해 LLM이 현재값을 해석 서술

C안 패턴 (trend/explain에서 공통 유틸 재사용). 프런트 대시보드·FacilityCards에서
개별 센서의 현재값 옆에 "AI 해석" 버튼을 달기 위한 엔드포인트.
"""

import asyncio
import logging
import time
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from llm_narrative_log import log_narrative
# trend.py의 내부 유틸 재사용 (모듈 경계 넘지만 중복 방지를 위해 임시 허용)
from endpoints.trend import (
    _fetch_tag_meta,
    _fetch_trend_context,
    _fetch_water_level_thresholds,
    _is_context_enabled,
    _validate_summary_numbers,
)

logger = logging.getLogger("slm")

router = APIRouter()

_get_db_connection = None
_ollama_client = None


def init(get_db_connection_fn, ollama_client=None):
    global _get_db_connection, _ollama_client
    _get_db_connection = get_db_connection_fn
    _ollama_client = ollama_client


# ── 요청/응답 ──────────────────────────────────────────────────────────────

class TagLatestExplainRequest(BaseModel):
    tagsn: str = Field(..., min_length=1, max_length=100)
    current_value: float
    tag_name: Optional[str] = None
    unit: Optional[str] = None


def _fallback_summary(
    tag_name: str, unit: str, value: float, context: dict,
) -> str:
    unit_str = f" {unit}" if unit else ""
    parts = [f"{tag_name}의 현재값은 {value:.3g}{unit_str}입니다."]
    if "baseline_avg_30d" in context:
        parts.append(
            f"지난 30일 평균은 {context['baseline_avg_30d']:.3g}{unit_str}입니다."
        )
    if "hh_threshold" in context:
        parts.append(f"HH 임계값은 {context['hh_threshold']:.3g}{unit_str}입니다.")
    return " ".join(parts)


@router.post("/tag/latest/explain")
async def explain_tag_latest(req: TagLatestExplainRequest):
    """단일 태그의 현재값을 baseline·임계값 컨텍스트로 해석 서술."""
    _context_mode = "on" if _is_context_enabled("SITE_SETTING", "TREND_EXPLAIN_CONTEXT") else "off"
    _ctx_t0 = time.perf_counter()

    meta = _fetch_tag_meta(req.tagsn) if req.tagsn else {}
    display_name = req.tag_name or meta.get("datainfo") or req.tagsn
    unit = req.unit or ""
    sitename = meta.get("sitename", "")
    facilitytype = meta.get("facilitytype", "")
    datainfo = meta.get("datainfo", "")

    context: dict = {}
    if _context_mode == "on":
        context = _fetch_trend_context(req.tagsn) or {}
        is_level = "수위" in (req.tag_name or "") or "수위" in datainfo
        if sitename and is_level:
            th = _fetch_water_level_thresholds(sitename, facilitytype or "배수지")
            if th:
                context.update(th)

    _context_fetch_ms = int((time.perf_counter() - _ctx_t0) * 1000)
    _context_used: list[str] = []
    if "baseline_avg_30d" in context:
        _context_used.append("baseline_30d")
    if "hh_threshold" in context or "ll_threshold" in context:
        _context_used.append("thresholds")

    # 허용 수치
    allowed_numbers = [float(req.current_value), 0.0, 30.0]
    for key in ("baseline_min_30d", "baseline_avg_30d", "baseline_max_30d",
                "hh_threshold", "ll_threshold"):
        if key in context:
            allowed_numbers.append(float(context[key]))

    context_lines = []
    if "baseline_avg_30d" in context:
        context_lines.append(f"- 지난 30일 최소: {context['baseline_min_30d']:.3g}")
        context_lines.append(f"- 지난 30일 평균: {context['baseline_avg_30d']:.3g}")
        context_lines.append(f"- 지난 30일 최대: {context['baseline_max_30d']:.3g}")
    if "hh_threshold" in context:
        context_lines.append(f"- HH 임계값: {context['hh_threshold']:.3g}")
    if "ll_threshold" in context:
        context_lines.append(f"- LL 임계값: {context['ll_threshold']:.3g}")
    context_block = (
        "\n\n## Baseline·임계값 (이 값들도 서술에 사용 가능)\n" + "\n".join(context_lines)
    ) if context_lines else ""

    unit_str = f" ({unit})" if unit else ""
    prompt = (
        "다음 센서의 현재값을 분석하여 1~2문장으로 해석 서술하라.\n\n"
        "## 절대 규칙\n"
        "1. 아래 제공된 수치만 사용하라 (current, baseline, 임계값).\n"
        "2. 제공되지 않은 숫자는 절대 언급하지 마라.\n"
        "3. 권고·조치·원인 추측은 포함하지 마라 (상태 서술에 집중).\n"
        "4. 외부 지식이나 일반 기준은 추가하지 마라.\n"
        "5. 존댓말 '~습니다' 종결.\n"
        "6. Baseline이 있으면 현재값이 지난 30일 평균 대비 어떤 수준인지 비교 서술.\n"
        "7. HH/LL 임계값이 있으면 현재값이 임계값 대비 어느 수준인지 비교 서술.\n\n"
        f"## 태그\n{display_name}{unit_str}\n\n"
        f"## 현재값\n- {req.current_value:.3g}"
        f"{context_block}\n\n"
        "해석 서술 (1~2문장, 위 수치만 사용, 존댓말):"
    )

    if not _ollama_client:
        return {
            "summary": _fallback_summary(display_name, unit, req.current_value, context),
            "source": "fallback",
            "context_used": _context_used,
            "context_fetch_ms": _context_fetch_ms,
            "context_mode": _context_mode,
            "llm_generate_ms": 0,
            "allowed_numbers_count": len(allowed_numbers),
        }

    _t0 = time.perf_counter()
    try:
        text = await asyncio.to_thread(
            _ollama_client.generate,
            prompt,
            None, None, None,
            60.0,  # 단일 값이라 짧은 프롬프트 → timeout 더 작게
            3,
        )
    except Exception as e:
        logger.warning(f"tag_latest_explain LLM 호출 실패: {e}")
        log_narrative(
            endpoint="tag/latest/explain",
            params={"tagsn": req.tagsn},
            source="fallback",
            context_mode=_context_mode,
            context_used=_context_used,
            context_fetch_ms=_context_fetch_ms,
            llm_generate_ms=int((time.perf_counter() - _t0) * 1000),
            llm_rejected=False,
            allowed_count=len(allowed_numbers),
        )
        return {
            "summary": _fallback_summary(display_name, unit, req.current_value, context),
            "source": "fallback",
            "error": str(e),
            "context_used": _context_used,
            "context_fetch_ms": _context_fetch_ms,
            "context_mode": _context_mode,
            "llm_generate_ms": int((time.perf_counter() - _t0) * 1000),
            "allowed_numbers_count": len(allowed_numbers),
        }

    _llm_ms = int((time.perf_counter() - _t0) * 1000)
    text = (text or "").strip()

    ok, violations = _validate_summary_numbers(text, allowed_numbers)
    if not ok or not text:
        logger.warning(
            f"tag_latest_explain 할루시네이션 → 폴백: "
            f"위반={violations}, 허용={allowed_numbers}"
        )
        log_narrative(
            endpoint="tag/latest/explain",
            params={"tagsn": req.tagsn},
            source="fallback",
            context_mode=_context_mode,
            context_used=_context_used,
            context_fetch_ms=_context_fetch_ms,
            llm_generate_ms=_llm_ms,
            llm_rejected=True,
            violations=violations,
            allowed_count=len(allowed_numbers),
        )
        return {
            "summary": _fallback_summary(display_name, unit, req.current_value, context),
            "source": "fallback",
            "llm_rejected": True,
            "violations": violations,
            "context_used": _context_used,
            "context_fetch_ms": _context_fetch_ms,
            "context_mode": _context_mode,
            "llm_generate_ms": _llm_ms,
            "allowed_numbers_count": len(allowed_numbers),
        }

    logger.info(
        f"tag_latest_explain: {display_name} value={req.current_value} "
        f"⏱ context={_context_fetch_ms}ms, llm={_llm_ms}ms, ctx={_context_used}"
    )
    log_narrative(
        endpoint="tag/latest/explain",
        params={"tagsn": req.tagsn, "value": req.current_value},
        source="llm",
        context_mode=_context_mode,
        context_used=_context_used,
        context_fetch_ms=_context_fetch_ms,
        llm_generate_ms=_llm_ms,
        llm_rejected=False,
        allowed_count=len(allowed_numbers),
    )
    return {
        "summary": text,
        "source": "llm",
        "context_used": _context_used,
        "context_fetch_ms": _context_fetch_ms,
        "context_mode": _context_mode,
        "llm_generate_ms": _llm_ms,
        "allowed_numbers_count": len(allowed_numbers),
    }
