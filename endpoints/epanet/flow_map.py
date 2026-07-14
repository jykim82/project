"""EPANET API — 시설↔EPANET 노드 유량 매핑 CRUD + auto-suggest + 실측 수요 주입(B-1)."""

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



from .common import _UNIT_TO_LPS, _ensure_enabled, _get_user_id, _to_lps, router

logger = logging.getLogger(__name__)

# ===========================================================================
# E3.5) 시설 ↔ 실측 유량 태그 매핑 (B-1: Live demand injection)
# 사양: docs/epanet-flow-injection-spec.md §3
# ===========================================================================

class FacilityFlowMapIn(BaseModel):
    region: str = "R01"
    sitename: str
    facilitytype: str
    role: str            # 'outflow' | 'inflow'
    tagsn: str
    unit: str            # 'cmh' | 'lps' | 'm3h' | 'lpm' | 'm3s'
    scale: float = 1.0
    x: float
    y: float
    enabled: Optional[str] = "Y"
    notes: Optional[str] = None




@router.get("/facility-flow-map")
def list_facility_flow_map(region: str = "R01", limit: int = 500) -> dict:
    _ensure_enabled(region)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT map_id, region, sitename, facilitytype, role, tagsn, unit,
                   scale, x, y, enabled, notes, created_at, updated_at, created_by
              FROM tb_epanet_facility_flow_map
             WHERE region = %s
             ORDER BY facilitytype, sitename, role
             LIMIT %s
            """,
            (region, limit),
        )
        rows = cur.fetchall()
        cur.close()
        items = [{
            "map_id": r[0], "region": r[1],
            "sitename": r[2], "facilitytype": r[3], "role": r[4],
            "tagsn": r[5], "unit": r[6], "scale": float(r[7]),
            "x": float(r[8]), "y": float(r[9]),
            "enabled": (r[10] == "Y"), "notes": r[11],
            "created_at": r[12].isoformat() if r[12] else None,
            "updated_at": r[13].isoformat() if r[13] else None,
            "created_by": r[14],
        } for r in rows]
        return {"items": items, "total": len(items)}
    finally:
        conn.close()


@router.post("/facility-flow-map")
def add_facility_flow_map(req: FacilityFlowMapIn, request: Request) -> dict:
    _ensure_enabled(req.region)
    if req.role not in ("outflow", "inflow"):
        raise HTTPException(400, detail="role 은 'outflow' 또는 'inflow'")
    if req.unit not in _UNIT_TO_LPS:
        raise HTTPException(400, detail=f"unit 은 {list(_UNIT_TO_LPS.keys())} 중 하나")
    user_id = _get_user_id(request)
    enabled = "Y" if (req.enabled or "Y").upper() == "Y" else "N"
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO tb_epanet_facility_flow_map
                (region, sitename, facilitytype, role, tagsn, unit, scale,
                 x, y, enabled, notes, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (region, sitename, facilitytype, role) DO UPDATE
               SET tagsn = EXCLUDED.tagsn, unit = EXCLUDED.unit,
                   scale = EXCLUDED.scale, x = EXCLUDED.x, y = EXCLUDED.y,
                   enabled = EXCLUDED.enabled, notes = EXCLUDED.notes,
                   updated_at = NOW()
            RETURNING map_id
            """,
            (req.region, req.sitename, req.facilitytype, req.role, req.tagsn,
             req.unit, req.scale, req.x, req.y, enabled, req.notes, user_id),
        )
        mid = cur.fetchone()[0]
        conn.commit()
        cur.close()
        return {"map_id": mid}
    finally:
        conn.close()


@router.delete("/facility-flow-map/{map_id}")
def delete_facility_flow_map(map_id: int, region: str = "R01") -> dict:
    _ensure_enabled(region)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM tb_epanet_facility_flow_map "
            "WHERE map_id = %s AND region = %s",
            (map_id, region),
        )
        conn.commit()
        cur.close()
        return {"deleted": map_id}
    finally:
        conn.close()


@router.delete("/facility-flow-map")
def delete_all_facility_flow_map(region: str = "R01") -> dict:
    _ensure_enabled(region)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM tb_epanet_facility_flow_map WHERE region = %s", (region,)
        )
        n = cur.rowcount
        conn.commit()
        cur.close()
        return {"deleted_count": n}
    finally:
        conn.close()


@router.post("/facility-flow-map/auto-suggest")
def auto_suggest_facility_flow_map(region: str = "R01") -> dict:
    """tb_tag_info + tb_facility_flow_map 결합으로 정밀 매핑 후보 제안.

    신뢰도 (confidence):
    - verified: tb_facility_flow_map 에 등장 + 상하류 짝의 일관성 검증됨
    - probable: tb_facility_flow_map 에 등장 (단방향만 매칭)
    - weak    : tb_facility_flow_map 에 미등장 (datainfo 패턴만)

    응답:
    - items: 후보 + confidence + verification 메타
    - unmapped_facilities: tb_facility_flow_map 에 등장하지만 outflow/inflow
      태그가 발견 안 된 시설 (운영자 수동 입력 필요)
    """
    _ensure_enabled(region)
    conn = get_db()
    try:
        cur = conn.cursor()
        # 1) datainfo 패턴 매칭 후보 (tagsn 별)
        cur.execute(
            """
            SELECT t.tagsn, t.sitename, t.facilitytype, t.datainfo, t.unit
              FROM tb_tag_info t
             WHERE (t.datainfo ILIKE '%유출유량순시%'
                 OR t.datainfo ILIKE '%유량순시유량%'
                 OR t.datainfo ILIKE '%토출유량%'
                 OR (t.datainfo ILIKE '%유입유량순시%'
                     AND t.facilitytype = '배수지'))
               AND t.tagtype = 'Analog Input'
               AND COALESCE(t.alarm_tag_yn, 0) = 0
               AND t.facilitytype IN ('배수지','가압장','소블록','소소블록')
               AND t.sitename IS NOT NULL
             ORDER BY t.facilitytype, t.sitename
            """
        )
        rows = cur.fetchall()

        # 2) tb_facility_flow_map 상하류 관계
        cur.execute(
            """
            SELECT upstream_sitename, upstream_facilitytype,
                   downstream_sitename, downstream_facilitytype
              FROM tb_facility_flow_map
            """
        )
        flow_pairs = cur.fetchall()

        # 3) 기존 매핑 (재제안 방지)
        cur.execute(
            "SELECT sitename, facilitytype, role FROM tb_epanet_facility_flow_map "
            "WHERE region = %s",
            (region,),
        )
        existing = {(r[0], r[1], r[2]) for r in cur.fetchall()}
        cur.close()
    finally:
        conn.close()

    # 4) 시설 in/out 등장 집합
    out_facilities = {(p[0], p[1]) for p in flow_pairs}        # upstream
    in_facilities  = {(p[2], p[3]) for p in flow_pairs}        # downstream
    in_flow_map    = out_facilities | in_facilities

    # 5) datainfo 매칭 결과 → 시설별 그룹
    by_facility: dict = {}  # (sitename, facilitytype) → {"outflow":[...], "inflow":[...]}
    for tagsn, sitename, facilitytype, datainfo, raw_unit in rows:
        if not sitename or not facilitytype:
            continue
        is_inflow = "유입" in (datainfo or "")
        role = "inflow" if is_inflow else "outflow"
        unit = "cmh"
        if raw_unit and "L/min" in raw_unit:
            unit = "lpm"
        elif raw_unit and "lps" in (raw_unit or "").lower():
            unit = "lps"
        key = (sitename, facilitytype)
        by_facility.setdefault(key, {"outflow": [], "inflow": []})[role].append({
            "tagsn": tagsn, "unit": unit, "datainfo": datainfo,
        })

    # 6) 신뢰도 + verification 계산
    suggestions: list = []
    for (sitename, facilitytype), roles in by_facility.items():
        # 시설이 tb_facility_flow_map 에 등장하는지
        in_map = (sitename, facilitytype) in in_flow_map
        # outflow 짝 검증 — 하류 시설의 inflow 와 unit 일관성
        downstream = [(p[2], p[3]) for p in flow_pairs
                      if p[0] == sitename and p[1] == facilitytype]
        upstream = [(p[0], p[1]) for p in flow_pairs
                    if p[2] == sitename and p[3] == facilitytype]

        for role in ("outflow", "inflow"):
            tags = roles[role]
            if not tags:
                continue
            if (sitename, facilitytype, role) in existing:
                continue
            # 한 시설에 같은 role 태그가 여러 개면 첫 번째 (datainfo 가 가장 단순한 것
            # 우선) 사용 — 운영자가 다이얼로그에서 수정 가능
            tag = tags[0]

            # 검증
            verification: dict = {"in_flow_map": in_map}
            if role == "outflow":
                if downstream:
                    matches = [
                        f"{ds[0]}({ds[1]})" for ds in downstream
                        if (ds[0], ds[1]) in by_facility
                        and by_facility[(ds[0], ds[1])]["inflow"]
                    ]
                    verification["downstream_pairs"] = len(downstream)
                    verification["downstream_inflow_tagged"] = len(matches)
                    if matches:
                        verification["downstream_sample"] = matches[0]
            else:  # inflow
                if upstream:
                    matches = [
                        f"{us[0]}({us[1]})" for us in upstream
                        if (us[0], us[1]) in by_facility
                        and by_facility[(us[0], us[1])]["outflow"]
                    ]
                    verification["upstream_pairs"] = len(upstream)
                    verification["upstream_outflow_tagged"] = len(matches)
                    if matches:
                        verification["upstream_sample"] = matches[0]

            # 신뢰도
            paired = (verification.get("downstream_inflow_tagged", 0)
                      + verification.get("upstream_outflow_tagged", 0)) > 0
            if in_map and paired:
                confidence = "verified"
            elif in_map:
                confidence = "probable"
            else:
                confidence = "weak"

            suggestions.append({
                "sitename": sitename,
                "facilitytype": facilitytype,
                "role": role,
                "tagsn": tag["tagsn"],
                "unit": tag["unit"],
                "scale": 1.0,
                "x": 0.0, "y": 0.0,                 # 좌표는 프런트에서 채움
                "datainfo": tag["datainfo"],
                "confidence": confidence,
                "verification": verification,
                "needs_coord": True,                # 프런트가 채움 (gis-facility-coords.json)
            })

    # 7) 누락 시설 — tb_facility_flow_map 에 있는데 outflow/inflow 매칭 0건
    unmapped: list = []
    for site, ftype in in_flow_map:
        # facilitytype 정규화 — flow_map 은 "정수장" 도 포함되지만 EPANET demand
        # 대상이 아니므로 스킵
        if ftype not in ("배수지", "가압장", "소블록", "소소블록"):
            continue
        if (site, ftype, "outflow") in existing:
            continue
        cur_tags = by_facility.get((site, ftype), {"outflow": [], "inflow": []})
        if not cur_tags["outflow"] and not cur_tags["inflow"]:
            unmapped.append({
                "sitename": site, "facilitytype": ftype,
                "reason": "tb_facility_flow_map 에 등장하지만 datainfo 매칭 안 됨",
            })

    # 정렬: confidence 높은 순 → facilitytype/sitename
    confidence_order = {"verified": 0, "probable": 1, "weak": 2}
    suggestions.sort(key=lambda s: (
        confidence_order.get(s["confidence"], 9),
        s["facilitytype"], s["sitename"], s["role"],
    ))

    return {
        "items": suggestions,
        "total": len(suggestions),
        "unmapped_facilities": unmapped,
        "stats": {
            "verified": sum(1 for s in suggestions if s["confidence"] == "verified"),
            "probable": sum(1 for s in suggestions if s["confidence"] == "probable"),
            "weak":     sum(1 for s in suggestions if s["confidence"] == "weak"),
        },
    }


def _compute_live_demands(region: str, hours: int = 1) -> list[tuple]:
    """매핑된 시설별 실측 평균 → demand_points (LPS).

    Returns: list of (x, y, demand_lps, source_label).
    배수지(reservoir) 는 EPANET 모델에서 reservoir/source 라 junction demand 로
    주입 안 함. 가압장·블록의 outflow 만 주입.
    """
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT map_id, sitename, facilitytype, role, tagsn, unit, scale, x, y
              FROM tb_epanet_facility_flow_map
             WHERE region = %s AND enabled = 'Y'
               AND facilitytype IN ('가압장','소블록','소소블록')
               AND role = 'outflow'
            """,
            (region,),
        )
        maps = cur.fetchall()
        if not maps:
            cur.close()
            return []
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

    out: list = []
    for map_id, sitename, facilitytype, role, tagsn, unit, scale, x, y in maps:
        avg, cnt = observed.get(tagsn, (None, 0))
        if avg is None or cnt < 3:
            continue  # 데이터 부족 — 스킵
        lps = _to_lps(avg, unit, float(scale))
        if lps <= 0:
            continue
        out.append((float(x), float(y), float(lps),
                    f"live:{sitename}:{role}"))
    return out


