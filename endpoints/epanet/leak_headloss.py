"""EPANET API — 누수 의심 구간(leak-suspicious) + 손실수두 이상(headloss-anomaly)."""

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



from .common import _ensure_enabled, router

logger = logging.getLogger(__name__)

# ===========================================================================
# E4) 누수 의심 구간 분석 (Phase 3.3b)
# ===========================================================================

@router.get("/leak-suspicious")
def get_leak_suspicious(
    region: str = "R01",
    threshold_m: float = 5.0,
    hours: int = 1,
) -> dict:
    """매핑된 센서별로 실측 압력 vs 시뮬 압력 차이를 계산.

    - 실측: tb_tag_raw_data 최근 `hours` 시간 평균 + calibration_offset_m
    - 시뮬: 가장 최근 success 시뮬의 가장 가까운 노드 (KNN, 좌표 기준)
    - 의심: |실측 - 시뮬| > threshold_m

    응답 items 정렬: 의심 → 차이 큰 순.
    """
    _ensure_enabled(region)
    conn = get_db()
    try:
        cur = conn.cursor()
        # 1) 매핑 조회
        cur.execute(
            """
            SELECT map_id, tag_sn, x, y, calibration_offset_m, label
              FROM tb_epanet_meter_map
             WHERE region = %s
            """,
            (region,),
        )
        maps = cur.fetchall()
        if not maps:
            return {
                "items": [], "suspicious_count": 0, "total_mapped": 0,
                "threshold_m": threshold_m, "hours": hours,
                "warning": "센서 매핑이 없습니다. /admin/epanet 에서 매핑 추가 후 다시 시도.",
            }

        # 2) 가장 최근 시뮬 결과
        cur.execute(
            """
            SELECT sim_id, result_data
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
                "items": [], "suspicious_count": 0, "total_mapped": len(maps),
                "threshold_m": threshold_m, "hours": hours,
                "warning": "성공한 시뮬이 없습니다. /admin/epanet 에서 [시뮬] 실행 후 다시 시도.",
            }
        sim_id = sim_row[0]
        rd = sim_row[1]
        sim_junctions = rd.get("junctions") or []

        # 3) 매핑별 실측 평균
        tag_sns = [r[1] for r in maps]
        cur.execute(
            """
            SELECT tagsn, AVG(val) AS avg_v, COUNT(*) AS cnt
              FROM tb_tag_raw_data
             WHERE tagsn = ANY(%s)
               AND logtime > NOW() - (%s || ' hours')::interval
             GROUP BY tagsn
            """,
            (tag_sns, str(hours)),
        )
        observed = {row[0]: (float(row[1]) if row[1] is not None else None, int(row[2]))
                    for row in cur.fetchall()}
        cur.close()
    finally:
        conn.close()

    # 4) 매핑별 KNN 노드 매칭 + diff 계산
    items: list = []
    for map_id, tag_sn, x, y, offset, label in maps:
        x = float(x); y = float(y); offset = float(offset)
        # KNN — 가장 가까운 sim junction
        nearest = None
        nearest_d2 = float("inf")
        for j in sim_junctions:
            jx = j.get("x"); jy = j.get("y")
            if jx is None or jy is None:
                continue
            d2 = (x - jx) ** 2 + (y - jy) ** 2
            if d2 < nearest_d2:
                nearest_d2 = d2
                nearest = j
        sim_p = nearest.get("pressure_m") if nearest else None
        sim_node_id = nearest.get("id") if nearest else None
        dist_m = nearest_d2 ** 0.5 if nearest_d2 != float("inf") else None

        raw_avg, sample_count = observed.get(tag_sn, (None, 0))
        observed_m = (raw_avg + offset) if raw_avg is not None else None
        diff_m = (abs(observed_m - sim_p)
                  if observed_m is not None and sim_p is not None else None)
        suspicious = bool(diff_m is not None and diff_m > threshold_m)

        items.append({
            "map_id": map_id,
            "tag_sn": tag_sn,
            "label": label,
            "x": x, "y": y,
            "lng": (nearest.get("lng") if nearest else None),
            "lat": (nearest.get("lat") if nearest else None),
            "sim_node_id": sim_node_id,
            "dist_to_node_m": round(dist_m, 1) if dist_m is not None else None,
            "observed_m": round(observed_m, 2) if observed_m is not None else None,
            "observed_count": sample_count,
            "calibration_offset_m": offset,
            "sim_pressure_m": round(sim_p, 2) if sim_p is not None else None,
            "diff_m": round(diff_m, 2) if diff_m is not None else None,
            "suspicious": suspicious,
        })

    # 의심 → 차이 큰 순 정렬
    items.sort(key=lambda i: (
        -1 if i["suspicious"] else 0,
        -(i["diff_m"] or 0),
    ))
    suspicious_count = sum(1 for i in items if i["suspicious"])
    return {
        "items": items,
        "total_mapped": len(maps),
        "suspicious_count": suspicious_count,
        "threshold_m": threshold_m,
        "hours": hours,
        "sim_id": sim_id,
    }



# ===========================================================================
# E5) 헤드손실 이상 구간 분석 (Phase 3.3c)
# ===========================================================================

@router.get("/headloss-anomaly")
def get_headloss_anomaly(
    region: str = "R01",
    z_threshold: float = 2.0,
) -> dict:
    """파이프별 단위길이당 headloss 분포에서 z-score 가 임계값 초과인 이상 구간.

    - 단위 손실 (m/100m) = headloss_m / length_m * 100
    - 그룹: diameter (구경) 군집 — 같은 구경끼리 평균 비교
    - z = (단위손실 - 그룹 평균) / 그룹 stddev
    - |z| > z_threshold 면 이상

    응답: items[{pipe_id, diameter, length, headloss, unit_loss, group_mean, z_score, anomaly}]
    """
    _ensure_enabled(region)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT sim_id, result_data, file_path
              FROM tb_epanet_simulation_result s
              JOIN tb_epanet_artifact a ON a.artifact_id = s.artifact_id
             WHERE s.region = %s AND s.status = 'success'
             ORDER BY s.created_at DESC LIMIT 1
            """,
            (region,),
        )
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()

    if not row or not row[1]:
        return {
            "items": [], "anomaly_count": 0, "total_pipes": 0,
            "z_threshold": z_threshold,
            "warning": "성공한 시뮬이 없습니다. /admin/epanet 에서 [시뮬] 실행 후 다시 시도.",
        }
    sim_id = row[0]
    rd = row[1]
    inp_path = row[2]
    pipes_sim = rd.get("pipes") or []

    # INP 파일에서 파이프별 length / diameter / roughness 파싱
    pipe_meta: dict = {}  # id → {length, diameter, roughness}
    try:
        if inp_path and os.path.exists(inp_path):
            text = open(inp_path, "r", encoding="utf-8", errors="ignore").read()
            in_pipes = False
            for line in text.splitlines():
                ls = line.strip()
                if ls.startswith("[") and ls.endswith("]"):
                    in_pipes = (ls.upper() == "[PIPES]")
                    continue
                if in_pipes and ls and not ls.startswith(";"):
                    parts = ls.split()
                    if len(parts) >= 6:
                        try:
                            pipe_meta[parts[0]] = {
                                "length": float(parts[3]),
                                "diameter": float(parts[4]),
                                "roughness": float(parts[5]),
                            }
                        except ValueError:
                            pass
    except Exception as e:
        logger.warning(f"INP 파싱 실패 (headloss-anomaly): {e}")

    # 단위 손실 + 그룹화 (diameter 기준)
    # WNTRSimulator 가 headloss 를 안 채우는 경우 — Hazen-Williams 공식 즉석 계산
    rows: list = []
    by_dia: dict = {}
    for p in pipes_sim:
        pid = p.get("id")
        meta = pipe_meta.get(pid)
        if meta is None:
            continue
        length = meta["length"]
        if length <= 0:
            continue
        headloss = p.get("headloss_m")
        if headloss is None or headloss == 0:
            # Hazen-Williams: HL = 10.67 * Q^1.852 / (C^1.852 * D^4.87) * L
            #   Q (m³/s), D (m), L (m), C (HW coeff)
            flow_lps = p.get("flow_lps") or 0
            q = abs(float(flow_lps)) / 1000.0  # LPS → m³/s
            d_m = meta["diameter"] / 1000.0    # mm → m
            c = meta["roughness"]
            if q > 0 and d_m > 0 and c > 0:
                headloss = 10.67 * (q ** 1.852) / (c ** 1.852 * d_m ** 4.87) * length
            else:
                headloss = 0.0
        if headloss <= 0:
            continue
        unit_loss = abs(float(headloss)) / length * 100.0  # m / 100m
        dia = meta["diameter"]
        # 50mm 단위로 그룹화 (현실적 그룹 수)
        dia_bucket = round(dia / 50.0) * 50.0
        rows.append({
            "id": pid,
            "diameter": dia,
            "diameter_bucket": dia_bucket,
            "length": round(length, 1),
            "headloss_m": round(float(headloss), 3),
            "unit_loss_m_per_100m": round(unit_loss, 4),
            "flow_lps": p.get("flow_lps"),
            "velocity_mps": p.get("velocity_mps"),
        })
        by_dia.setdefault(dia_bucket, []).append(unit_loss)

    # 그룹별 평균/stddev
    group_stats: dict = {}
    for d, vals in by_dia.items():
        if len(vals) < 2:
            group_stats[d] = (sum(vals) / len(vals), 0.0)
            continue
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)
        group_stats[d] = (mean, var ** 0.5)

    # z-score 계산
    items: list = []
    for r in rows:
        mean, std = group_stats.get(r["diameter_bucket"], (0.0, 0.0))
        z = ((r["unit_loss_m_per_100m"] - mean) / std) if std > 0 else 0.0
        items.append({
            **r,
            "group_mean_unit_loss": round(mean, 4),
            "z_score": round(z, 2),
            "anomaly": bool(abs(z) > z_threshold),
        })
    items.sort(key=lambda i: -abs(i["z_score"]))
    anomaly_count = sum(1 for i in items if i["anomaly"])
    return {
        "items": items,
        "total_pipes": len(items),
        "anomaly_count": anomaly_count,
        "z_threshold": z_threshold,
        "sim_id": sim_id,
        "diameter_buckets": sorted(by_dia.keys()),
    }


