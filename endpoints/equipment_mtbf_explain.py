"""
설비 신뢰성 리포트 AI 해석 (P2.7)

- POST /equipment-mtbf/explain
  입력: {days?, sitename?, facilitytype?, top_n?}
  → /admin/equipment-mtbf의 상위 N개 최악 설비를 LLM이 자연어로 서술
     (노후 패턴, 설비 유형별 집중, 우선 조치 필요 항목 식별)

할루시네이션 방어: 반환된 수치만 allowed_numbers에 포함.
"""

import asyncio
import logging
import time
from collections import Counter
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from llm_narrative_log import log_narrative
from endpoints.trend import _is_context_enabled, _validate_summary_numbers

logger = logging.getLogger("slm")

router = APIRouter()

_get_db_connection = None
_ollama_client = None


def init(get_db_connection_fn, ollama_client=None):
    global _get_db_connection, _ollama_client
    _get_db_connection = get_db_connection_fn
    _ollama_client = ollama_client


class MtbfExplainRequest(BaseModel):
    days: int = Field(90, ge=7, le=365)
    sitename: Optional[str] = None
    facilitytype: Optional[str] = None
    top_n: int = Field(5, ge=1, le=20)


def _fetch_mtbf_top(days: int, sitename: Optional[str],
                    facilitytype: Optional[str], top_n: int) -> list[dict]:
    """equipment_mtbf 쿼리 재사용해 최악 top_n 반환."""
    if _get_db_connection is None:
        return []
    where = ["r.alarm_start_time >= NOW() - (%s || ' days')::interval",
             "r.alarm_start_time < NOW()"]
    params: list = [days]
    if sitename:
        where.append("e.sitename = %s")
        params.append(sitename)
    if facilitytype:
        where.append("e.facilitytype = %s")
        params.append(facilitytype)
    params += [days, days, top_n]
    sql = f"""
    WITH alarms AS (
        SELECT m.equipment_id, e.equipmenttype, e.sitename, e.facilitytype,
               LEAST(EXTRACT(EPOCH FROM COALESCE(r.alarm_end_time, NOW())
                             - r.alarm_start_time), 86400.0) AS dur
        FROM tb_equipment_alarm_report r
        JOIN tb_equipment_tag_map m ON m.tagsn = r.tagsn
        JOIN tb_equipment_info e ON e.equipment_id = m.equipment_id
        WHERE {' AND '.join(where)}
    )
    SELECT equipment_id, equipmenttype, sitename, facilitytype,
           COUNT(*) AS fault_count,
           ROUND((SUM(dur)/3600.0)::numeric, 1) AS downtime_h,
           ROUND((AVG(dur)/3600.0)::numeric, 2) AS mttr_h,
           GREATEST(0, ROUND(((%s*24 - SUM(dur)/3600.0) / NULLIF(COUNT(*),0))::numeric, 1)) AS mtbf_h,
           GREATEST(0, LEAST(100, ROUND((100.0*(1 - SUM(dur)/(%s*86400.0)))::numeric, 2))) AS avail_pct
    FROM alarms
    GROUP BY equipment_id, equipmenttype, sitename, facilitytype
    HAVING COUNT(*) > 0
    ORDER BY avail_pct ASC, fault_count DESC
    LIMIT %s
    """
    conn = None
    try:
        conn = _get_db_connection()
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [
                {
                    "equipment_id": r[0], "equipmenttype": r[1],
                    "sitename": r[2], "facilitytype": r[3],
                    "fault_count": int(r[4]) if r[4] is not None else 0,
                    "downtime_h": float(r[5]) if r[5] is not None else 0.0,
                    "mttr_h": float(r[6]) if r[6] is not None else 0.0,
                    "mtbf_h": float(r[7]) if r[7] is not None else 0.0,
                    "avail_pct": float(r[8]) if r[8] is not None else 0.0,
                }
                for r in cur.fetchall()
            ]
    except Exception as e:
        logger.warning(f"mtbf top 조회 실패: {e}")
        return []
    finally:
        if conn:
            conn.close()


def _fallback_summary(rows: list[dict], days: int) -> str:
    if not rows:
        return f"지난 {days}일간 고장 이력이 없어 평가할 설비가 없습니다."
    worst = rows[0]
    parts = [
        f"지난 {days}일간 설비 신뢰성 분석 결과, 가장 주의가 필요한 설비는 "
        f"{worst['sitename']} {worst['facilitytype']}의 {worst['equipmenttype']}"
        f"({worst['equipment_id']})입니다. 고장 {worst['fault_count']}회, "
        f"가동률 {worst['avail_pct']:.2f}%로 확인되었습니다.",
    ]
    if len(rows) > 1:
        types = Counter(r["equipmenttype"] for r in rows)
        top_type = types.most_common(1)[0]
        parts.append(
            f"상위 {len(rows)}개 설비 중 {top_type[0]} 계열이 {top_type[1]}개로 가장 많습니다."
        )
    return " ".join(parts)


@router.post("/equipment-mtbf/explain")
async def explain_mtbf(req: MtbfExplainRequest = MtbfExplainRequest()):
    """최악 설비 top_n개를 LLM이 자연어로 해석 서술."""
    _context_mode = "on" if _is_context_enabled("SITE_SETTING", "TREND_EXPLAIN_CONTEXT") else "off"
    _ctx_t0 = time.perf_counter()

    top_rows = _fetch_mtbf_top(req.days, req.sitename, req.facilitytype, req.top_n)
    _context_fetch_ms = int((time.perf_counter() - _ctx_t0) * 1000)

    if not top_rows:
        return {
            "summary": f"지난 {req.days}일간 고장 이력이 없어 평가할 설비가 없습니다.",
            "source": "fallback",
            "context_used": ["mtbf"],
            "context_fetch_ms": _context_fetch_ms,
            "context_mode": _context_mode,
            "llm_generate_ms": 0,
            "allowed_numbers_count": 0,
        }

    # 타입별 집계 통계
    types = Counter(r["equipmenttype"] for r in top_rows)
    types_summary = ", ".join(f"{t} {c}개" for t, c in types.most_common(3))

    _context_used = ["mtbf"]

    # 허용 수치: 모든 행의 숫자 필드 + req.days, top_n
    allowed_numbers: list[float] = [float(req.days), float(req.top_n), 0.0, 100.0]
    for r in top_rows:
        for key in ("fault_count", "downtime_h", "mttr_h", "mtbf_h", "avail_pct"):
            v = r.get(key)
            if v is not None:
                try:
                    allowed_numbers.append(float(v))
                except (TypeError, ValueError):
                    pass
    for _, c in types.most_common():
        allowed_numbers.append(float(c))

    # 프롬프트 구성
    lines = []
    for i, r in enumerate(top_rows, 1):
        lines.append(
            f"{i}) {r['sitename']} {r['facilitytype']} · {r['equipmenttype']}"
            f"({r['equipment_id']}) — 고장 {r['fault_count']}회, "
            f"MTTR {r['mttr_h']}h, MTBF {r['mtbf_h']}h, 가동률 {r['avail_pct']}%"
        )
    top_block = "\n".join(lines)

    prompt = (
        f"다음은 지난 {req.days}일간 설비 신뢰성 분석 결과의 하위 {len(top_rows)}개 설비입니다. "
        "2~4문장으로 현황을 자연어 요약하라.\n\n"
        "## 절대 규칙\n"
        "1. 아래 'Top 목록'과 '분포'의 수치·명칭만 사용하라.\n"
        "2. 제공되지 않은 숫자는 절대 언급하지 마라.\n"
        "3. 권고 사항, 조치 지시, 구체 기간 예측은 포함하지 마라 (현황 서술에 집중).\n"
        "4. 외부 지식이나 일반 기준은 추가하지 마라.\n"
        "5. 존댓말 '~습니다' 종결.\n"
        "6. 가장 심각한 1~2건은 시설명과 함께 구체 수치(가동률·고장 횟수)를 언급하라.\n"
        "7. 설비 유형별 분포가 특정 타입에 치우치면 그 사실을 서술하라.\n\n"
        f"## 분포\n- 상위 {len(top_rows)}개 설비 유형 분포: {types_summary}\n\n"
        f"## Top {len(top_rows)} 목록\n{top_block}\n\n"
        "요약 (2~4문장, 위 수치·명칭만 사용, 존댓말):"
    )

    if not _ollama_client:
        return {
            "summary": _fallback_summary(top_rows, req.days),
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
            prompt, None, None, None, 90.0, 3,
        )
    except Exception as e:
        logger.warning(f"mtbf_explain LLM 실패: {e}")
        return {
            "summary": _fallback_summary(top_rows, req.days),
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

    # 식별자 strip — 시설명·설비ID에 포함된 숫자 오탐 방지
    strip_strings: list[str] = []
    for r in top_rows:
        for key in ("sitename", "facilitytype", "equipmenttype", "equipment_id"):
            v = r.get(key)
            if v:
                strip_strings.append(str(v))

    import re as _re
    cleaned = text
    for s in sorted(set(strip_strings), key=len, reverse=True):
        cleaned = cleaned.replace(s, " ")
    ok, violations = _validate_summary_numbers(cleaned, allowed_numbers)

    if not ok or not text:
        logger.warning(f"mtbf_explain 할루시네이션 → 폴백: 위반={violations}")
        log_narrative(
            endpoint="equipment-mtbf/explain",
            params={"days": req.days, "top_n": req.top_n},
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
            "summary": _fallback_summary(top_rows, req.days),
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
        f"mtbf_explain: days={req.days} top_n={req.top_n} "
        f"⏱ ctx={_context_fetch_ms}ms, llm={_llm_ms}ms"
    )
    log_narrative(
        endpoint="equipment-mtbf/explain",
        params={"days": req.days, "top_n": req.top_n, "top_count": len(top_rows)},
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
        "top_count": len(top_rows),
    }
