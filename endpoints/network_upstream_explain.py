"""
NETWORK_UPSTREAM_FAULT_ANALYSIS LLM 원인 추정 (P2.8)

- POST /network/upstream-fault/explain
  입력: 없음 — `tb_network_status` + `tb_network_link` 기반 실시간 조회
  → 상위 장비(SSLVPN/UTM) 장애가 하위 LTE 모뎀(현장) 통신이상의 원인인지
    연쇄 분석을 LLM이 자연어로 서술.

할루시네이션 방어: 쿼리가 반환한 숫자·시설명만 allowed_numbers/strip에 포함.
"""

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter

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


# ai_server.py의 NETWORK_UPSTREAM_FAULT_ANALYSIS와 동일 SQL.
# 현재 시점 SSLVPN 그룹별 LTE 하위 통계 + UTM 상태 + 전체 LTE 통계를 1회 조회.
_UPSTREAM_SQL = """
WITH latest AS (
    SELECT equipment_id, MAX(check_time) AS mt
    FROM tb_network_status
    GROUP BY equipment_id
),
current_status AS (
    SELECT ns.equipment_id, ns.is_alive
    FROM tb_network_status ns
    JOIN latest ON latest.equipment_id = ns.equipment_id AND latest.mt = ns.check_time
),
sslvpn_summary AS (
    SELECT
        ei_t.sitename || ' ' || ei_t.equipmenttype AS sslvpn_id,
        COUNT(*)                                                            AS total_lte,
        COUNT(*) FILTER (WHERE NOT COALESCE(cs_s.is_alive, false))         AS down_lte,
        bool_or(cs_t.is_alive)                                             AS sslvpn_alive,
        array_agg(ei_s.sitename ORDER BY ei_s.sitename)
            FILTER (WHERE NOT COALESCE(cs_s.is_alive, false))              AS down_sites
    FROM tb_network_link nl
    JOIN tb_equipment_info ei_s ON ei_s.equipment_id = nl.source_equipment_id
    JOIN tb_equipment_info ei_t ON ei_t.equipment_id = nl.target_equipment_id
    LEFT JOIN current_status cs_s ON cs_s.equipment_id = nl.source_equipment_id
    LEFT JOIN current_status cs_t ON cs_t.equipment_id = nl.target_equipment_id
    WHERE ei_s.equipmenttype = 'LTE 모뎀'
      AND ei_t.equipmenttype = 'SSLVPN'
    GROUP BY ei_t.sitename, ei_t.equipmenttype
),
utm_info AS (
    SELECT
        COUNT(*)                                                            AS total_utm,
        COUNT(*) FILTER (WHERE NOT COALESCE(cs.is_alive, true))            AS down_utm
    FROM tb_equipment_info ei
    LEFT JOIN current_status cs ON cs.equipment_id = ei.equipment_id
    WHERE ei.equipmenttype IN ('UTM', 'FA망 현대화사업소 UTM')
),
total_lte AS (
    SELECT
        COUNT(*)                                                            AS global_lte_total,
        COUNT(*) FILTER (WHERE NOT COALESCE(cs.is_alive, false))           AS global_lte_down
    FROM tb_equipment_info ei
    LEFT JOIN current_status cs ON cs.equipment_id = ei.equipment_id
    WHERE ei.equipmenttype = 'LTE 모뎀'
)
SELECT
    ss.sslvpn_id,
    ss.total_lte::int,
    ss.down_lte::int,
    ss.sslvpn_alive,
    ss.down_sites,
    ui.total_utm::int,
    ui.down_utm::int,
    tl.global_lte_total::int,
    tl.global_lte_down::int
FROM sslvpn_summary ss
CROSS JOIN utm_info ui
CROSS JOIN total_lte tl
ORDER BY ss.down_lte DESC, ss.sslvpn_id
"""


def _fetch_upstream_state() -> tuple[list[dict], dict[str, Any]]:
    """현재 상위 장비 장애 상태를 조회.

    반환:
      (sslvpn_rows, global_stats)
        sslvpn_rows  : 각 SSLVPN 그룹의 {sslvpn_id, total_lte, down_lte, sslvpn_alive, down_sites, down_pct}
        global_stats : {total_utm, down_utm, global_lte_total, global_lte_down, global_lte_down_pct}
    """
    if _get_db_connection is None:
        return [], {}

    conn = None
    try:
        conn = _get_db_connection()
        with conn.cursor() as cur:
            cur.execute(_UPSTREAM_SQL)
            rows = cur.fetchall() or []
            columns = [desc[0] for desc in cur.description]
    except Exception as e:
        logger.warning(f"upstream_fault SQL 실행 실패: {e}")
        return [], {}
    finally:
        if conn:
            conn.close()

    if not rows:
        return [], {}

    # 공통 글로벌 지표 (모든 row가 동일 값)
    first = dict(zip(columns, rows[0]))
    total_utm = int(first.get("total_utm") or 0)
    down_utm = int(first.get("down_utm") or 0)
    global_lte_total = int(first.get("global_lte_total") or 0)
    global_lte_down = int(first.get("global_lte_down") or 0)
    global_lte_down_pct = (
        round(global_lte_down / global_lte_total * 100, 1)
        if global_lte_total else 0.0
    )

    sslvpn_rows: list[dict] = []
    for row in rows:
        rd = dict(zip(columns, row))
        total_lte = int(rd.get("total_lte") or 0)
        down_lte = int(rd.get("down_lte") or 0)
        if total_lte == 0:
            continue
        down_sites = rd.get("down_sites") or []
        if isinstance(down_sites, str):
            import ast
            try:
                down_sites = ast.literal_eval(down_sites)
            except Exception:
                down_sites = []
        sslvpn_rows.append({
            "sslvpn_id": rd.get("sslvpn_id", ""),
            "total_lte": total_lte,
            "down_lte": down_lte,
            "sslvpn_alive": rd.get("sslvpn_alive"),
            "down_sites": list(down_sites) if down_sites else [],
            "down_pct": round(down_lte / total_lte * 100, 1),
        })

    global_stats = {
        "total_utm": total_utm,
        "down_utm": down_utm,
        "global_lte_total": global_lte_total,
        "global_lte_down": global_lte_down,
        "global_lte_down_pct": global_lte_down_pct,
    }
    return sslvpn_rows, global_stats


def _fallback_summary(sslvpn_rows: list[dict], global_stats: dict) -> str:
    """LLM 사용 불가·할루시네이션 검출 시 결정적 템플릿."""
    if not sslvpn_rows and not global_stats:
        return "네트워크 상태 데이터가 없습니다."
    gt = global_stats.get("global_lte_total", 0)
    gd = global_stats.get("global_lte_down", 0)
    ut = global_stats.get("total_utm", 0)
    ud = global_stats.get("down_utm", 0)
    if ut > 0 and ud == ut:
        return (
            f"UTM {ut}대 전부 응답 없음으로 상위 장비 전체 장애가 의심됩니다. "
            f"전체 LTE 모뎀 {gt}대 중 {gd}대가 통신이상 상태입니다."
        )
    parts = [
        f"전체 LTE 모뎀 {gt}대 중 {gd}대 ({global_stats.get('global_lte_down_pct', 0)}%)가 통신이상입니다."
    ]
    for r in sslvpn_rows[:3]:
        parts.append(
            f"{r['sslvpn_id']}는 하위 LTE {r['total_lte']}대 중 "
            f"{r['down_lte']}대 ({r['down_pct']}%)가 다운 상태입니다."
        )
    return " ".join(parts)


def _build_context_block(sslvpn_rows: list[dict], global_stats: dict) -> str:
    """LLM 프롬프트에 들어갈 구조화 컨텍스트."""
    lines: list[str] = []
    lines.append("## UTM (인터넷 방화벽) 상태")
    lines.append(
        f"- 전체: {global_stats['total_utm']}대 / 다운: {global_stats['down_utm']}대"
    )
    lines.append("")
    lines.append("## 전체 LTE 모뎀 (현장 라우터) 상태")
    lines.append(
        f"- 전체: {global_stats['global_lte_total']}대 / "
        f"다운: {global_stats['global_lte_down']}대 "
        f"({global_stats['global_lte_down_pct']}%)"
    )
    lines.append("")
    lines.append("## SSLVPN 그룹별 하위 LTE 통계 (down_lte 내림차순)")
    for r in sslvpn_rows[:10]:
        alive_str = (
            "정상" if r["sslvpn_alive"] is True
            else "응답없음" if r["sslvpn_alive"] is False
            else "상태없음"
        )
        lines.append(
            f"- {r['sslvpn_id']} [{alive_str}]: "
            f"하위 LTE {r['total_lte']}대 중 {r['down_lte']}대 "
            f"({r['down_pct']}%) 다운"
        )
        if r["down_sites"]:
            # 이상 현장 최대 10개
            sites = r["down_sites"][:10]
            lines.append(f"  · 이상 현장: {', '.join(sites)}")
    return "\n".join(lines)


@router.post("/network/upstream-fault/explain")
async def explain_upstream_fault():
    """SSLVPN/UTM 계층 장애 → 하위 LTE 통신이상 연쇄를 AI가 자연어로 서술."""
    _context_mode = (
        "on" if _is_context_enabled("SITE_SETTING", "TREND_EXPLAIN_CONTEXT") else "off"
    )
    _ctx_t0 = time.perf_counter()

    sslvpn_rows, global_stats = await asyncio.to_thread(_fetch_upstream_state)
    _context_fetch_ms = int((time.perf_counter() - _ctx_t0) * 1000)
    _context_used = ["network_status_latest", "network_link"]

    if not sslvpn_rows and not global_stats:
        return {
            "summary": "네트워크 상태 데이터를 조회할 수 없습니다.",
            "source": "fallback",
            "context_used": [],
            "context_fetch_ms": _context_fetch_ms,
            "context_mode": _context_mode,
            "llm_generate_ms": 0,
            "allowed_numbers_count": 0,
        }

    # 허용 수치 whitelist 구성.
    # 프롬프트 규칙에 등장하는 임계값 상수(10% 미만, 80% 이상)도 allow 해야
    # LLM이 해당 문구를 인용할 때 할루시네이션으로 오판되지 않는다.
    allowed_numbers: list[float] = [
        float(global_stats["total_utm"]),
        float(global_stats["down_utm"]),
        float(global_stats["global_lte_total"]),
        float(global_stats["global_lte_down"]),
        float(global_stats["global_lte_down_pct"]),
        0.0, 10.0, 80.0, 100.0,  # 프롬프트 규칙 상수
    ]
    for r in sslvpn_rows:
        allowed_numbers.append(float(r["total_lte"]))
        allowed_numbers.append(float(r["down_lte"]))
        allowed_numbers.append(float(r["down_pct"]))

    # 식별자 strip (시설명·sslvpn_id가 숫자 포함 가능: "성북2", "석문2산단")
    strip_strings: list[str] = []
    for r in sslvpn_rows:
        if r.get("sslvpn_id"):
            strip_strings.append(r["sslvpn_id"])
        for site in r.get("down_sites", []):
            if site:
                strip_strings.append(site)

    context_block = _build_context_block(sslvpn_rows, global_stats)

    prompt = (
        "다음은 현재 상위 네트워크 장비(UTM/SSLVPN)와 하위 LTE 모뎀(현장 라우터)의 "
        "통신 상태 스냅샷이다. 이 정보를 바탕으로 2~4문장으로 원인 연쇄를 서술하라.\n\n"
        "## 절대 규칙\n"
        "1. 아래 '상태' 섹션의 수치·시설명·그룹명만 사용하라.\n"
        "2. 제공되지 않은 숫자·시설·장비는 절대 언급하지 마라.\n"
        "3. 권고 조치나 수리 방법은 포함하지 마라 (원인 추정에 집중).\n"
        "4. 외부 지식이나 일반적 네트워크 기준은 추가하지 마라.\n"
        "5. 존댓말 '~습니다' 종결.\n"
        "6. UTM 전체 장애(down_utm == total_utm) 시 '상위 전 구간 장애'로 해석.\n"
        "7. 특정 SSLVPN 하위 80% 이상 다운 시 '해당 SSLVPN 장애로 인한 집단 통신이상' 가능성 언급.\n"
        "8. 전체 LTE 다운율이 10% 미만이면 '상위 장비는 정상이고 개별 현장 이상' 해석.\n\n"
        f"{context_block}\n\n"
        "원인 연쇄 서술 (2~4문장, 위 수치·명칭만 사용, 존댓말):"
    )

    if not _ollama_client:
        return {
            "summary": _fallback_summary(sslvpn_rows, global_stats),
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
            180.0, 3,
        )
    except Exception as e:
        logger.warning(f"upstream_fault_explain LLM 실패: {e}")
        log_narrative(
            endpoint="network/upstream-fault/explain",
            params={},
            source="fallback",
            context_mode=_context_mode,
            context_used=_context_used,
            context_fetch_ms=_context_fetch_ms,
            llm_generate_ms=int((time.perf_counter() - _t0) * 1000),
            llm_rejected=False,
            allowed_count=len(allowed_numbers),
        )
        return {
            "summary": _fallback_summary(sslvpn_rows, global_stats),
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

    # 수치 검증 + 식별자 strip 후 재검증 (오탐 방지)
    ok, violations = _validate_summary_numbers(text, allowed_numbers)
    if (not ok) and text and strip_strings:
        cleaned = text
        for s in sorted(set(strip_strings), key=len, reverse=True):
            cleaned = cleaned.replace(s, " ")
        ok, violations = _validate_summary_numbers(cleaned, allowed_numbers)

    if not ok or not text:
        logger.warning(
            f"upstream_fault_explain 할루시네이션 → 폴백: 위반={violations}"
        )
        log_narrative(
            endpoint="network/upstream-fault/explain",
            params={},
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
            "summary": _fallback_summary(sslvpn_rows, global_stats),
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
        f"upstream_fault_explain: sslvpn_rows={len(sslvpn_rows)} "
        f"global_down={global_stats['global_lte_down']}/{global_stats['global_lte_total']} "
        f"⏱ ctx={_context_fetch_ms}ms, llm={_llm_ms}ms"
    )
    log_narrative(
        endpoint="network/upstream-fault/explain",
        params={
            "sslvpn_rows": len(sslvpn_rows),
            "global_lte_down": global_stats["global_lte_down"],
            "global_lte_total": global_stats["global_lte_total"],
        },
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
        "sslvpn_count": len(sslvpn_rows),
        "global_lte_down_pct": global_stats["global_lte_down_pct"],
    }
