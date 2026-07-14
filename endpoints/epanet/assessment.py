"""EPANET API — 교체 후보·관망 노후도·수질(체류시간) 평가."""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from epanet import is_enabled, is_wntr_available, get_db
from epanet.shp_reader import scan_shp
from epanet.inp_converter import convert_pipes_to_inp, validate_with_wntr
from epanet.simulator import run_steady_state, run_what_if



from .common import _ensure_enabled, _latest_artifact_inp, _latest_sim_data, router

logger = logging.getLogger(__name__)

# ===========================================================================
# E9) 블록 교체 후보 (Phase 5-1) — 헤드손실 z + 매핑 차이 + length 가중
# ===========================================================================

@router.get("/replacement-candidates")
def get_replacement_candidates(
    region: str = "R01",
    top: int = 30,
) -> dict:
    """교체 우선순위 점수 = headloss z-score + leak diff + length 가중."""
    _ensure_enabled(region)
    # 헤드손실 분석 결과 활용
    headloss_resp = get_headloss_anomaly(region=region, z_threshold=1.0)
    candidates: list = []
    for it in headloss_resp.get("items", []):
        z = abs(it.get("z_score") or 0)
        length = it.get("length") or 0
        # 점수: z 기여 0~5, length 기여 0~3 (3000m 이상 → 3)
        score = min(5.0, z) + min(3.0, length / 1000.0)
        candidates.append({
            **it,
            "score": round(score, 2),
        })
    candidates.sort(key=lambda c: -c["score"])
    return {
        "items": candidates[:top],
        "total_evaluated": len(candidates),
        "ranking_method": "headloss z-score + length 가중",
    }


# ===========================================================================
# E10) 관망 노후도 (Phase 3.3d) — 매핑별 편차 시계열 (월별)
# ===========================================================================

@router.get("/network-aging")
def get_network_aging(
    region: str = "R01",
    months: int = 3,
) -> dict:
    """월별 매핑별 |실측 평균 - 시뮬 압력| 편차 추이.

    데이터 누적 부족 시 단일 시점 (현재 시뮬) 만 표시.
    """
    _ensure_enabled(region)
    sim = _latest_sim_data(region)
    if not sim:
        return {"warning": "성공한 시뮬이 없습니다.", "items": [], "months": months}

    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT map_id, tag_sn, x, y, calibration_offset_m, label "
            "FROM tb_epanet_meter_map WHERE region = %s",
            (region,),
        )
        maps = cur.fetchall()
        if not maps:
            cur.close()
            return {"warning": "센서 매핑이 없습니다.", "items": [], "months": months}

        # 월별 평균 (TimescaleDB time_bucket)
        tag_sns = [r[1] for r in maps]
        cur.execute(
            """
            SELECT tagsn,
                   date_trunc('month', logtime) AS m,
                   AVG(val) AS avg_v,
                   COUNT(*) AS cnt
              FROM tb_tag_raw_data
             WHERE tagsn = ANY(%s)
               AND logtime > NOW() - (%s || ' months')::interval
             GROUP BY tagsn, m
             ORDER BY tagsn, m
            """,
            (tag_sns, str(months)),
        )
        monthly = {}
        for row in cur.fetchall():
            monthly.setdefault(row[0], []).append({
                "month": row[1].strftime("%Y-%m"),
                "avg_observed": float(row[2]) if row[2] is not None else None,
                "samples": int(row[3]),
            })
        cur.close()
    finally:
        conn.close()

    # KNN — 매핑별 가장 가까운 sim 노드
    sim_junctions = sim["result_data"].get("junctions") or []
    items: list = []
    for map_id, tag_sn, x, y, offset, label in maps:
        x = float(x); y = float(y); offset = float(offset)
        nearest = None
        nd2 = float("inf")
        for j in sim_junctions:
            jx = j.get("x"); jy = j.get("y")
            if jx is None or jy is None:
                continue
            d2 = (x - jx) ** 2 + (y - jy) ** 2
            if d2 < nd2:
                nd2 = d2; nearest = j
        sim_p = nearest.get("pressure_m") if nearest else None
        ts = monthly.get(tag_sn) or []
        series = []
        for pt in ts:
            obs = (pt["avg_observed"] + offset) if pt["avg_observed"] is not None else None
            diff = (abs(obs - sim_p) if obs is not None and sim_p is not None else None)
            series.append({
                "month": pt["month"],
                "observed_m": round(obs, 2) if obs is not None else None,
                "diff_m": round(diff, 2) if diff is not None else None,
                "samples": pt["samples"],
            })
        # 추세: 마지막 vs 첫 diff
        trend = "stable"
        if len(series) >= 2:
            d_first = series[0].get("diff_m")
            d_last = series[-1].get("diff_m")
            if d_first is not None and d_last is not None:
                if d_last > d_first * 1.2:
                    trend = "worsening"
                elif d_last < d_first * 0.8:
                    trend = "improving"
        items.append({
            "map_id": map_id, "tag_sn": tag_sn, "label": label,
            "sim_pressure_m": round(sim_p, 2) if sim_p is not None else None,
            "series": series,
            "trend": trend,
            "current_diff_m": series[-1].get("diff_m") if series else None,
        })
    items.sort(key=lambda i: -(i.get("current_diff_m") or 0))
    return {
        "items": items,
        "months": months,
        "sim_id": sim["sim_id"],
        "total_mapped": len(maps),
    }


# ===========================================================================
# E11) 수질·체류시간 (Phase 6) — 합성 잔류염소 시뮬
# ===========================================================================

@router.get("/water-quality")
def get_water_quality(
    region: str = "R01",
    initial_mg_l: float = 0.5,
    kbulk_per_day: float = -0.5,
) -> dict:
    """6시간 EPS 시뮬로 노드별 잔류염소 농도 + 체류시간 추정.

    체류시간 ≈ -ln(C/C0) / k (1차 반응)
    """
    _ensure_enabled(region)
    inp = _latest_artifact_inp(region)
    if not inp:
        return {"warning": "INP 파일이 없습니다.", "items": []}

    res = run_what_if(inp, quality_initial=initial_mg_l, quality_kbulk=kbulk_per_day)
    if not res.success:
        return {"error": res.error, "items": []}

    import math
    items: list = []
    for j in res.junctions:
        c = j.get("quality_mg_l")
        if c is None or c <= 0:
            continue
        # 체류시간 추정 (시간 단위)
        try:
            ratio = c / initial_mg_l
            if ratio > 0 and ratio < 1.0 and kbulk_per_day != 0:
                hours = -math.log(ratio) / abs(kbulk_per_day) * 24
            else:
                hours = 0
        except Exception:
            hours = 0
        items.append({
            "id": j["id"], "lng": j.get("lng"), "lat": j.get("lat"),
            "chlorine_mg_l": c,
            "residence_hours": round(hours, 2),
            "low_quality": bool(c < initial_mg_l * 0.4),
        })
    items.sort(key=lambda i: i["chlorine_mg_l"])
    low_count = sum(1 for i in items if i["low_quality"])
    return {
        "synthetic": True,
        "initial_mg_l": initial_mg_l,
        "kbulk_per_day": kbulk_per_day,
        "total_nodes": len(items),
        "low_quality_count": low_count,
        "items": items[:50],
    }


