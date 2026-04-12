"""
전체 이상 스캔 요약 AI 서술 (P2.6)

- POST /anomaly/scan-all/explain
  입력 없음 (_ANOMALY_SCAN_CACHE에서 직접 읽음)
  → 가장 심각한 이상/주의 태그 Top 3~5를 LLM이 자연어로 요약

운영자가 전체 현황판을 열고 "오늘 가장 큰 문제가 뭐야?" 라는 질문에 답하는 단문 서술.
할루시네이션 방어: Top-N 태그의 수치만 allowed_numbers에 포함.
"""

import asyncio
import logging
import time
from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from llm_narrative_log import log_narrative
from endpoints.trend import _is_context_enabled, _validate_summary_numbers

logger = logging.getLogger("slm")

router = APIRouter()

_get_scan_cache: Optional[Callable[[], tuple]] = None
_ollama_client = None


def init(get_scan_cache_fn, ollama_client=None):
    global _get_scan_cache, _ollama_client
    _get_scan_cache = get_scan_cache_fn
    _ollama_client = ollama_client


class ScanAllExplainRequest(BaseModel):
    top_n: int = Field(3, ge=1, le=10, description="요약에 포함할 최악 태그 수")


def _build_fallback(top_rows: list[dict], top_n: int) -> str:
    if not top_rows:
        return "현재 이상 탐지된 태그가 없습니다. 전체 시스템이 정상 범위에서 동작 중입니다."
    parts = [f"현재 이상 탐지된 태그 상위 {min(top_n, len(top_rows))}건입니다."]
    for i, r in enumerate(top_rows[:top_n], 1):
        parts.append(
            f"{i}) {r.get('sitename', '?')} {r.get('facilitytype', '?')} · "
            f"{r.get('datainfo', '?')} (z={r.get('z_score', 0):.2f})"
        )
    return " ".join(parts)


@router.post("/anomaly/scan-all/explain")
async def explain_scan_all(req: ScanAllExplainRequest = ScanAllExplainRequest()):
    """전체 이상 스캔 캐시에서 Top-N 심각 항목을 AI 자연어로 요약."""
    _context_mode = "on" if _is_context_enabled("SITE_SETTING", "TREND_EXPLAIN_CONTEXT") else "off"
    _ctx_t0 = time.perf_counter()

    if _get_scan_cache is None:
        raise HTTPException(500, "scan_all_explain not initialized")
    cache, cache_ts = _get_scan_cache()
    # 캐시 미스 시 동기 계산으로 폴백 (서버 시작 직후 사용자 기다리지 않도록)
    if not cache:
        try:
            from anomaly_scan import _compute_anomaly_scan_all
            logger.info("scan_all_explain: 캐시 미스 → 동기 계산 시작")
            cache = await asyncio.to_thread(_compute_anomaly_scan_all)
        except Exception as e:
            logger.warning(f"scan_all_explain: 동기 계산 실패: {e}")
            cache = None
    if not cache:
        return {
            "summary": "이상감지 데이터가 아직 준비되지 않았습니다.",
            "source": "fallback",
            "context_used": [],
            "context_fetch_ms": 0,
            "context_mode": _context_mode,
            "llm_generate_ms": 0,
            "allowed_numbers_count": 0,
        }

    raw_rows = cache.get("rows") or []
    columns = cache.get("columns") or []
    if not raw_rows or not columns:
        return {
            "summary": "이상감지 데이터가 비어 있습니다.",
            "source": "fallback",
            "context_used": [],
            "context_fetch_ms": 0,
            "context_mode": _context_mode,
            "llm_generate_ms": 0,
            "allowed_numbers_count": 0,
        }

    # 캐시 rows는 list[list] 형태 — columns와 zip해 dict로 변환
    col_idx = {c: i for i, c in enumerate(columns)}

    def _as_dict(r) -> dict:
        if isinstance(r, dict):
            return r
        return {c: r[i] if i < len(r) else None for i, c in enumerate(columns)}

    rows: list[dict] = [_as_dict(r) for r in raw_rows]

    # Top-N: verdict='이상' 우선, 그다음 '주의' 보충. z_score 절댓값 내림차순
    anoms = [r for r in rows if r.get("verdict") == "이상"]
    warns = [r for r in rows if r.get("verdict") == "주의"]
    anoms.sort(key=lambda r: abs(float(r.get("z_score") or 0)), reverse=True)
    warns.sort(key=lambda r: abs(float(r.get("z_score") or 0)), reverse=True)

    top_rows: list[dict] = anoms[: req.top_n]
    if len(top_rows) < req.top_n:
        top_rows += warns[: req.top_n - len(top_rows)]

    total_anomaly = len(anoms)
    total_warn = len(warns)
    total_rows = len(rows)

    _context_fetch_ms = int((time.perf_counter() - _ctx_t0) * 1000)
    _context_used = ["scan_cache"]

    # 허용 수치: top_rows의 z_score, deviation_pct, current_val, mean_30d 등 + 총계
    # 프롬프트 상수 (30일 baseline 언급 가능)도 포함
    allowed_numbers: list[float] = [
        float(total_anomaly), float(total_warn), float(total_rows),
        float(req.top_n),
        0.0, 30.0,  # "0건", "30일 평균" 등 프롬프트 내 상수
    ]
    for r in top_rows:
        for key in ("z_score", "deviation_pct", "current_val", "mean_30d"):
            v = r.get(key)
            if v is not None:
                try:
                    allowed_numbers.append(float(v))
                except (TypeError, ValueError):
                    pass

    # 프롬프트 구성
    lines = []
    for i, r in enumerate(top_rows, 1):
        site = r.get("sitename", "?")
        ft = r.get("facilitytype", "?")
        di = (r.get("datainfo") or "?")[:40]
        z = r.get("z_score") or 0
        dev = r.get("deviation_pct") or 0
        curr = r.get("current_val") or 0
        mean = r.get("mean_30d") or 0
        verdict = r.get("verdict") or "?"
        equip_fail = r.get("equip_failure") or ""
        extra = f", 장애={equip_fail}" if equip_fail else ""
        lines.append(
            f"{i}) [{verdict}] {site} {ft} {di} "
            f"— 현재 {curr:.3g}, 30일평균 {mean:.3g}, "
            f"편차 {dev:.1f}%, z={z:.2f}{extra}"
        )
    top_block = "\n".join(lines) if lines else "(이상 없음)"

    prompt = (
        "다음은 전체 센서 이상 스캔 결과의 상위 심각 항목입니다. "
        "2~4문장으로 현황을 자연어 요약하라.\n\n"
        "## 절대 규칙\n"
        "1. 아래 '통계'와 'Top 목록'의 수치·명칭만 사용하라.\n"
        "2. 제공되지 않은 숫자·시설·태그는 절대 언급하지 마라.\n"
        "3. 권고 사항, 조치 지시, 원인 추측은 포함하지 마라 (현황 서술에 집중).\n"
        "4. 외부 지식이나 일반 기준은 추가하지 마라.\n"
        "5. 존댓말 '~습니다' 종결.\n"
        "6. 가장 심각한 1~2건은 시설명과 함께 구체 수치를 언급하라.\n\n"
        "## 통계\n"
        f"- 총 스캔 태그: {total_rows}건\n"
        f"- 이상 판정: {total_anomaly}건\n"
        f"- 주의 판정: {total_warn}건\n\n"
        f"## Top {len(top_rows)} 목록\n{top_block}\n\n"
        "현황 요약 (2~4문장, 위 수치·명칭만 사용, 존댓말):"
    )

    if not _ollama_client:
        return {
            "summary": _build_fallback(top_rows, req.top_n),
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
            90.0, 3,
        )
    except Exception as e:
        logger.warning(f"scan_all_explain LLM 실패: {e}")
        return {
            "summary": _build_fallback(top_rows, req.top_n),
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

    # 식별자(시설명·태그명) strip 목록
    strip_strings: list[str] = []
    for r in top_rows:
        for key in ("sitename", "facilitytype", "datainfo", "tagsn"):
            v = r.get(key)
            if v:
                strip_strings.append(str(v))

    ok, violations = _validate_summary_numbers(text, allowed_numbers)
    if not ok or not text:
        # 식별자 내부 숫자 오탐 가능성 — strip 후 재검증
        import re
        cleaned = text
        for s in sorted(set(strip_strings), key=len, reverse=True):
            cleaned = cleaned.replace(s, " ")
        ok, violations = _validate_summary_numbers(cleaned, allowed_numbers)

    if not ok or not text:
        logger.warning(
            f"scan_all_explain 할루시네이션 → 폴백: 위반={violations}"
        )
        log_narrative(
            endpoint="anomaly/scan-all/explain",
            params={"top_n": req.top_n},
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
            "summary": _build_fallback(top_rows, req.top_n),
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
        f"scan_all_explain: top_n={req.top_n} "
        f"⏱ ctx={_context_fetch_ms}ms, llm={_llm_ms}ms"
    )
    log_narrative(
        endpoint="anomaly/scan-all/explain",
        params={"top_n": req.top_n, "total_anomaly": total_anomaly, "total_warn": total_warn},
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
        "total_anomaly": total_anomaly,
        "total_warn": total_warn,
        "top_rows_count": len(top_rows),
    }
