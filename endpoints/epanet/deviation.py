"""EPANET API — 시뮬 vs 실측 유량 차이 분석 (B-2: flow-deviation + timeseries)."""

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



from .common import _ensure_enabled, _to_lps, router

logger = logging.getLogger(__name__)

# ===========================================================================
# E_FLOW_DEV) 시뮬 vs 실측 유량 차이 (B-2)
# 사양: docs/epanet-flow-deviation-spec.md §3
# ===========================================================================

@router.get("/flow-deviation")
def get_flow_deviation(
    region: str = "R01",
    hours: int = 1,
    threshold_pct: float = 10.0,
    min_flow_lps: float = 1.0,
) -> dict:
    """매핑된 시설별로 실측 유량 vs 시뮬 link 유량 차이를 계산.

    누수의심 (압력 기반) 의 자매 분석. 시뮬 link 매칭은 KNN (좌표 → 가장
    가까운 link 의 중점). 거리 임계 50m 초과 시 sim_flow_lps=null + 경고.
    """
    _ensure_enabled(region)
    conn = get_db()
    try:
        cur = conn.cursor()
        # 1) 시설 매핑
        cur.execute(
            """
            SELECT map_id, sitename, facilitytype, role, tagsn, unit, scale, x, y
              FROM tb_epanet_facility_flow_map
             WHERE region = %s AND enabled = 'Y'
            """,
            (region,),
        )
        maps = cur.fetchall()
        if not maps:
            cur.close()
            return {
                "items": [], "total_mapped": 0, "suspicious_count": 0,
                "threshold_pct": threshold_pct, "hours": hours,
                "warning": "시설 유량 매핑이 없습니다. /admin/epanet 에서 매핑 추가 후 다시 시도.",
            }

        # 2) 가장 최근 시뮬 결과
        cur.execute(
            """
            SELECT sim_id, result_data, created_at
              FROM tb_epanet_simulation_result
             WHERE region = %s AND status = 'success'
             ORDER BY created_at DESC LIMIT 1
            """,
            (region,),
        )
        sim_row = cur.fetchone()
        if not sim_row or not sim_row[1]:
            cur.close()
            return {
                "items": [], "total_mapped": len(maps), "suspicious_count": 0,
                "threshold_pct": threshold_pct, "hours": hours,
                "warning": "성공한 시뮬이 없습니다.",
            }
        sim_id = sim_row[0]
        sim_created_at = sim_row[2].isoformat() if sim_row[2] else None
        rd = sim_row[1]
        sim_pipes = rd.get("pipes") or []
        sim_junctions = rd.get("junctions") or []
        node_xy = {j["id"]: (j.get("x"), j.get("y"), j.get("lng"), j.get("lat"))
                   for j in sim_junctions if j.get("x") is not None}

        # 3) 매핑별 실측 평균
        tagsns = [m[4] for m in maps]
        cur.execute(
            """
            SELECT tagsn, AVG(val) AS avg_v, COUNT(*) AS cnt
              FROM tb_tag_raw_data
             WHERE tagsn = ANY(%s)
               AND logtime > NOW() - (%s || ' hours')::interval
             GROUP BY tagsn
            """,
            (tagsns, str(max(1, min(24, hours)))),
        )
        observed = {r[0]: (float(r[1]) if r[1] is not None else None, int(r[2]))
                    for r in cur.fetchall()}
        cur.close()
    finally:
        conn.close()

    # 4) link 중점 좌표 사전 계산 (lng/lat 도 함께)
    # simulator.py 는 'start'/'end' 키 사용 (start_node/end_node 아님)
    link_centers = []
    for p in sim_pipes:
        sxy = node_xy.get(p.get("start") or "")
        exy = node_xy.get(p.get("end") or "")
        if not sxy or not exy:
            continue
        sx, sy, slng, slat = sxy
        ex, ey, elng, elat = exy
        if sx is None or ex is None:
            continue
        cx = (sx + ex) / 2.0
        cy = (sy + ey) / 2.0
        clng = ((slng + elng) / 2.0) if (slng is not None and elng is not None) else None
        clat = ((slat + elat) / 2.0) if (slat is not None and elat is not None) else None
        link_centers.append({
            "id": p.get("id"), "cx": cx, "cy": cy,
            "lng": clng, "lat": clat,
            "flow_lps": abs(float(p.get("flow_lps") or 0)),
        })

    # 5) 매핑별 KNN link 매칭 + diff
    items: list = []
    for map_id, sitename, facilitytype, role, tagsn, unit, scale, x, y in maps:
        x = float(x); y = float(y); scale = float(scale)
        nearest = None; nearest_d2 = float("inf")
        for lc in link_centers:
            d2 = (x - lc["cx"]) ** 2 + (y - lc["cy"]) ** 2
            if d2 < nearest_d2:
                nearest_d2 = d2
                nearest = lc
        dist = nearest_d2 ** 0.5 if nearest else None
        sim_flow = nearest["flow_lps"] if (nearest and dist is not None and dist <= 50.0) else None
        sim_link_id = nearest["id"] if nearest else None

        avg, cnt = observed.get(tagsn, (None, 0))
        observed_lps = _to_lps(avg, unit, scale) if (avg is not None and cnt >= 3) else None

        diff_lps = (abs(observed_lps - sim_flow)
                    if observed_lps is not None and sim_flow is not None else None)
        diff_pct = None
        direction = None
        suspicious = False
        if diff_lps is not None and observed_lps is not None and sim_flow is not None:
            denom = max(observed_lps, sim_flow, 0.001)
            diff_pct = round(diff_lps / denom * 100, 2)
            if abs(observed_lps - sim_flow) <= 0.01:
                direction = "match"
            elif observed_lps > sim_flow:
                direction = "observed_higher"
            else:
                direction = "sim_higher"
            if max(observed_lps, sim_flow) >= min_flow_lps and diff_pct > threshold_pct:
                suspicious = True

        items.append({
            "map_id": map_id,
            "sitename": sitename,
            "facilitytype": facilitytype,
            "role": role,
            "tagsn": tagsn,
            "x": x, "y": y,
            "lng": (nearest.get("lng") if nearest else None),
            "lat": (nearest.get("lat") if nearest else None),
            "observed_lps": round(observed_lps, 2) if observed_lps is not None else None,
            "observed_count": cnt,
            "sim_link_id": sim_link_id,
            "sim_flow_lps": round(sim_flow, 2) if sim_flow is not None else None,
            "dist_to_link_m": round(dist, 1) if dist is not None else None,
            "diff_lps": round(diff_lps, 2) if diff_lps is not None else None,
            "diff_pct": diff_pct,
            "direction": direction,
            "suspicious": suspicious,
        })

    items.sort(key=lambda i: (
        -1 if i["suspicious"] else 0,
        -(i["diff_pct"] or 0),
    ))
    return {
        "items": items,
        "total_mapped": len(maps),
        "suspicious_count": sum(1 for i in items if i["suspicious"]),
        "threshold_pct": threshold_pct,
        "min_flow_lps": min_flow_lps,
        "hours": hours,
        "sim_id": sim_id,
        "sim_created_at": sim_created_at,
    }


@router.get("/flow-deviation/timeseries")
def get_flow_deviation_timeseries(
    region: str = "R01",
    map_id: int = 0,
    hours: int = 24,
) -> dict:
    """단건 시설의 24시간 실측 시계열 + 시뮬 baseline (상수)."""
    _ensure_enabled(region)
    if map_id <= 0:
        raise HTTPException(400, detail="map_id 필수")
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT sitename, facilitytype, role, tagsn, unit, scale, x, y
              FROM tb_epanet_facility_flow_map
             WHERE map_id = %s AND region = %s
            """,
            (map_id, region),
        )
        m = cur.fetchone()
        if not m:
            cur.close()
            raise HTTPException(404, detail="map_id 없음")
        sitename, facilitytype, role, tagsn, unit, scale, x, y = m
        scale = float(scale); x = float(x); y = float(y)

        # 시간별 평균 (실측)
        cur.execute(
            """
            SELECT date_trunc('hour', logtime) AS h, AVG(val) AS v, COUNT(*) AS cnt
              FROM tb_tag_raw_data
             WHERE tagsn = %s
               AND logtime > NOW() - (%s || ' hours')::interval
             GROUP BY h ORDER BY h
            """,
            (tagsn, str(max(1, min(48, hours)))),
        )
        observed = [
            {"t": r[0].isoformat(),
             "lps": round(_to_lps(float(r[1]), unit, scale), 2) if r[1] is not None else None}
            for r in cur.fetchall()
        ]

        # 시뮬 baseline (KNN link)
        cur.execute(
            """
            SELECT sim_id, result_data, created_at
              FROM tb_epanet_simulation_result
             WHERE region = %s AND status = 'success'
             ORDER BY created_at DESC LIMIT 1
            """,
            (region,),
        )
        sim_row = cur.fetchone()
        cur.close()
    finally:
        conn.close()

    sim_flow = None
    sim_id = None
    sim_created_at = None
    if sim_row and sim_row[1]:
        sim_id = sim_row[0]
        sim_created_at = sim_row[2].isoformat() if sim_row[2] else None
        rd = sim_row[1]
        sim_pipes = rd.get("pipes") or []
        sim_junctions = rd.get("junctions") or []
        node_xy = {j["id"]: (j.get("x"), j.get("y"))
                   for j in sim_junctions if j.get("x") is not None}
        nearest_d2 = float("inf")
        for p in sim_pipes:
            sxy = node_xy.get(p.get("start") or "")
            exy = node_xy.get(p.get("end") or "")
            if not sxy or not exy:
                continue
            cx = (sxy[0] + exy[0]) / 2.0
            cy = (sxy[1] + exy[1]) / 2.0
            d2 = (x - cx) ** 2 + (y - cy) ** 2
            if d2 < nearest_d2:
                nearest_d2 = d2
                sim_flow = abs(float(p.get("flow_lps") or 0))

    return {
        "map_id": map_id,
        "sitename": sitename,
        "facilitytype": facilitytype,
        "role": role,
        "tagsn": tagsn,
        "unit": unit,
        "x": x, "y": y,
        "sim_flow_lps": round(sim_flow, 2) if sim_flow is not None else None,
        "sim_id": sim_id,
        "sim_created_at": sim_created_at,
        "observed": observed,
    }


