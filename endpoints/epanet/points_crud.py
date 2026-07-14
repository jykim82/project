"""EPANET API — 고도(elevations)·수요(demands)·계측기(meters) 포인트 CRUD."""

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



from .common import _ensure_enabled, _get_user_id, router

logger = logging.getLogger(__name__)

# ===========================================================================
# E1) 표고 입력 CRUD (Phase 3.1)
# ===========================================================================

class ElevationPointIn(BaseModel):
    region: str = "R01"
    x: float
    y: float
    elevation_m: float
    label: Optional[str] = None
    notes: Optional[str] = None


@router.get("/elevations")
def list_elevation_points(region: str = "R01", limit: int = 500) -> dict:
    _ensure_enabled(region)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT point_id, region, x, y, elevation_m, source, label, notes,
                   created_at, created_by
              FROM tb_epanet_elevation_point
             WHERE region = %s
             ORDER BY created_at DESC
             LIMIT %s
            """,
            (region, limit),
        )
        rows = cur.fetchall()
        cur.close()
        items = [{
            "point_id": r[0], "region": r[1],
            "x": float(r[2]), "y": float(r[3]),
            "elevation_m": float(r[4]),
            "source": r[5], "label": r[6], "notes": r[7],
            "created_at": r[8].isoformat() if r[8] else None,
            "created_by": r[9],
        } for r in rows]
        return {"items": items, "total": len(items)}
    finally:
        conn.close()


@router.post("/elevations")
def add_elevation_point(req: ElevationPointIn, request: Request) -> dict:
    _ensure_enabled(req.region)
    user_id = _get_user_id(request)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO tb_epanet_elevation_point
                (region, x, y, elevation_m, source, label, notes, created_by)
            VALUES (%s, %s, %s, %s, 'manual', %s, %s, %s)
            RETURNING point_id
            """,
            (req.region, req.x, req.y, req.elevation_m, req.label, req.notes, user_id),
        )
        pid = cur.fetchone()[0]
        conn.commit()
        cur.close()
        return {"point_id": pid}
    finally:
        conn.close()


@router.post("/elevations/bulk-csv")
async def upload_elevations_csv(request: Request) -> dict:
    """CSV 본문 업로드 (text/plain 또는 multipart). 컬럼: x,y,elevation_m[,label,notes]"""
    region = request.query_params.get("region", "R01")
    _ensure_enabled(region)
    user_id = _get_user_id(request)
    body = (await request.body()).decode("utf-8", errors="ignore")
    if not body.strip():
        raise HTTPException(400, detail="빈 CSV")

    import csv
    from io import StringIO
    reader = csv.reader(StringIO(body))
    rows = list(reader)
    if not rows:
        raise HTTPException(400, detail="CSV 파싱 실패")

    # 첫 행이 헤더인지 추론 (숫자 변환 가능 여부)
    def _is_header(r: list) -> bool:
        try:
            float(r[0]); float(r[1]); float(r[2])
            return False
        except (ValueError, IndexError):
            return True
    data_rows = rows[1:] if rows and _is_header(rows[0]) else rows

    inserted = 0
    errors: list = []
    conn = get_db()
    try:
        cur = conn.cursor()
        for i, r in enumerate(data_rows, start=1):
            try:
                if len(r) < 3:
                    errors.append(f"행 {i}: 컬럼 부족 ({len(r)})")
                    continue
                x = float(r[0]); y = float(r[1]); z = float(r[2])
                label = r[3].strip() if len(r) > 3 and r[3].strip() else None
                notes = r[4].strip() if len(r) > 4 and r[4].strip() else None
                cur.execute(
                    """
                    INSERT INTO tb_epanet_elevation_point
                        (region, x, y, elevation_m, source, label, notes, created_by)
                    VALUES (%s, %s, %s, %s, 'csv', %s, %s, %s)
                    """,
                    (region, x, y, z, label, notes, user_id),
                )
                inserted += 1
            except Exception as e:
                errors.append(f"행 {i}: {e}")
        conn.commit()
        cur.close()
    finally:
        conn.close()
    return {"inserted": inserted, "errors": errors[:20], "total_errors": len(errors)}


@router.delete("/elevations/{point_id}")
def delete_elevation_point(point_id: int, region: str = "R01") -> dict:
    _ensure_enabled(region)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM tb_epanet_elevation_point WHERE point_id = %s AND region = %s",
            (point_id, region),
        )
        conn.commit()
        cur.close()
        return {"deleted": point_id}
    finally:
        conn.close()


@router.delete("/elevations")
def delete_all_elevation_points(region: str = "R01") -> dict:
    _ensure_enabled(region)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM tb_epanet_elevation_point WHERE region = %s", (region,))
        n = cur.rowcount
        conn.commit()
        cur.close()
        return {"deleted_count": n}
    finally:
        conn.close()


# ===========================================================================
# E2) 수요 입력 CRUD (Phase 3.2)
# ===========================================================================

class DemandPointIn(BaseModel):
    region: str = "R01"
    x: float
    y: float
    demand_lps: float
    label: Optional[str] = None
    notes: Optional[str] = None


@router.get("/demands")
def list_demand_points(region: str = "R01", limit: int = 500) -> dict:
    _ensure_enabled(region)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT point_id, region, x, y, demand_lps, source, label, notes,
                   created_at, created_by
              FROM tb_epanet_demand_point
             WHERE region = %s
             ORDER BY created_at DESC
             LIMIT %s
            """,
            (region, limit),
        )
        rows = cur.fetchall()
        cur.close()
        items = [{
            "point_id": r[0], "region": r[1],
            "x": float(r[2]), "y": float(r[3]),
            "demand_lps": float(r[4]),
            "source": r[5], "label": r[6], "notes": r[7],
            "created_at": r[8].isoformat() if r[8] else None,
            "created_by": r[9],
        } for r in rows]
        return {"items": items, "total": len(items)}
    finally:
        conn.close()


@router.post("/demands")
def add_demand_point(req: DemandPointIn, request: Request) -> dict:
    _ensure_enabled(req.region)
    user_id = _get_user_id(request)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO tb_epanet_demand_point
                (region, x, y, demand_lps, source, label, notes, created_by)
            VALUES (%s, %s, %s, %s, 'manual', %s, %s, %s)
            RETURNING point_id
            """,
            (req.region, req.x, req.y, req.demand_lps, req.label, req.notes, user_id),
        )
        pid = cur.fetchone()[0]
        conn.commit()
        cur.close()
        return {"point_id": pid}
    finally:
        conn.close()


@router.post("/demands/bulk-csv")
async def upload_demands_csv(request: Request) -> dict:
    """CSV 본문 업로드. 컬럼: x,y,demand_lps[,label,notes]"""
    region = request.query_params.get("region", "R01")
    _ensure_enabled(region)
    user_id = _get_user_id(request)
    body = (await request.body()).decode("utf-8", errors="ignore")
    if not body.strip():
        raise HTTPException(400, detail="빈 CSV")

    import csv
    from io import StringIO
    reader = csv.reader(StringIO(body))
    rows = list(reader)
    if not rows:
        raise HTTPException(400, detail="CSV 파싱 실패")

    def _is_header(r: list) -> bool:
        try:
            float(r[0]); float(r[1]); float(r[2])
            return False
        except (ValueError, IndexError):
            return True
    data_rows = rows[1:] if rows and _is_header(rows[0]) else rows

    inserted = 0
    errors: list = []
    conn = get_db()
    try:
        cur = conn.cursor()
        for i, r in enumerate(data_rows, start=1):
            try:
                if len(r) < 3:
                    errors.append(f"행 {i}: 컬럼 부족 ({len(r)})")
                    continue
                x = float(r[0]); y = float(r[1]); d = float(r[2])
                label = r[3].strip() if len(r) > 3 and r[3].strip() else None
                notes = r[4].strip() if len(r) > 4 and r[4].strip() else None
                cur.execute(
                    """
                    INSERT INTO tb_epanet_demand_point
                        (region, x, y, demand_lps, source, label, notes, created_by)
                    VALUES (%s, %s, %s, %s, 'csv', %s, %s, %s)
                    """,
                    (region, x, y, d, label, notes, user_id),
                )
                inserted += 1
            except Exception as e:
                errors.append(f"행 {i}: {e}")
        conn.commit()
        cur.close()
    finally:
        conn.close()
    return {"inserted": inserted, "errors": errors[:20], "total_errors": len(errors)}


@router.delete("/demands/{point_id}")
def delete_demand_point(point_id: int, region: str = "R01") -> dict:
    _ensure_enabled(region)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM tb_epanet_demand_point WHERE point_id = %s AND region = %s",
            (point_id, region),
        )
        conn.commit()
        cur.close()
        return {"deleted": point_id}
    finally:
        conn.close()


@router.delete("/demands")
def delete_all_demand_points(region: str = "R01") -> dict:
    _ensure_enabled(region)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM tb_epanet_demand_point WHERE region = %s", (region,))
        n = cur.rowcount
        conn.commit()
        cur.close()
        return {"deleted_count": n}
    finally:
        conn.close()


# ===========================================================================
# E3) 센서 매핑 CRUD (Phase 3.3a)
# ===========================================================================

class MeterMapIn(BaseModel):
    region: str = "R01"
    tag_sn: str
    x: float
    y: float
    calibration_offset_m: float = 0.0
    label: Optional[str] = None
    notes: Optional[str] = None


@router.get("/meters")
def list_meter_maps(region: str = "R01", limit: int = 500) -> dict:
    _ensure_enabled(region)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT map_id, region, tag_sn, x, y, calibration_offset_m,
                   label, notes, created_at, created_by
              FROM tb_epanet_meter_map
             WHERE region = %s
             ORDER BY created_at DESC
             LIMIT %s
            """,
            (region, limit),
        )
        rows = cur.fetchall()
        cur.close()
        items = [{
            "map_id": r[0], "region": r[1], "tag_sn": r[2],
            "x": float(r[3]), "y": float(r[4]),
            "calibration_offset_m": float(r[5]),
            "label": r[6], "notes": r[7],
            "created_at": r[8].isoformat() if r[8] else None,
            "created_by": r[9],
        } for r in rows]
        return {"items": items, "total": len(items)}
    finally:
        conn.close()


@router.post("/meters")
def add_meter_map(req: MeterMapIn, request: Request) -> dict:
    _ensure_enabled(req.region)
    user_id = _get_user_id(request)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO tb_epanet_meter_map
                (region, tag_sn, x, y, calibration_offset_m, label, notes, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (region, tag_sn) DO UPDATE
               SET x = EXCLUDED.x, y = EXCLUDED.y,
                   calibration_offset_m = EXCLUDED.calibration_offset_m,
                   label = EXCLUDED.label, notes = EXCLUDED.notes
            RETURNING map_id
            """,
            (req.region, req.tag_sn, req.x, req.y, req.calibration_offset_m,
             req.label, req.notes, user_id),
        )
        mid = cur.fetchone()[0]
        conn.commit()
        cur.close()
        return {"map_id": mid}
    finally:
        conn.close()


@router.post("/meters/bulk-csv")
async def upload_meters_csv(request: Request) -> dict:
    """CSV 본문 업로드. 컬럼: tag_sn,x,y[,offset_m,label,notes]"""
    region = request.query_params.get("region", "R01")
    _ensure_enabled(region)
    user_id = _get_user_id(request)
    body = (await request.body()).decode("utf-8", errors="ignore")
    if not body.strip():
        raise HTTPException(400, detail="빈 CSV")

    import csv
    from io import StringIO
    reader = csv.reader(StringIO(body))
    rows = list(reader)
    if not rows:
        raise HTTPException(400, detail="CSV 파싱 실패")

    def _is_header(r: list) -> bool:
        # 첫 컬럼이 tag_sn(텍스트)이고, 두번째가 숫자 변환 가능한지로 판정.
        # 첫 행의 tag_sn 후보가 정확히 'tag_sn' 이거나 두 번째 컬럼이 float 못되면 헤더로 본다.
        if not r:
            return False
        first = r[0].strip().lower()
        if first in ("tag_sn", "tag", "sn"):
            return True
        try:
            float(r[1]); float(r[2])
            return False
        except (ValueError, IndexError):
            return True
    data_rows = rows[1:] if rows and _is_header(rows[0]) else rows

    inserted = 0
    errors: list = []
    conn = get_db()
    try:
        cur = conn.cursor()
        for i, r in enumerate(data_rows, start=1):
            try:
                if len(r) < 3:
                    errors.append(f"행 {i}: 컬럼 부족 ({len(r)})")
                    continue
                tag_sn = r[0].strip()
                x = float(r[1]); y = float(r[2])
                offset = float(r[3]) if len(r) > 3 and r[3].strip() else 0.0
                label = r[4].strip() if len(r) > 4 and r[4].strip() else None
                notes = r[5].strip() if len(r) > 5 and r[5].strip() else None
                cur.execute(
                    """
                    INSERT INTO tb_epanet_meter_map
                        (region, tag_sn, x, y, calibration_offset_m, label, notes, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (region, tag_sn) DO UPDATE
                       SET x = EXCLUDED.x, y = EXCLUDED.y,
                           calibration_offset_m = EXCLUDED.calibration_offset_m,
                           label = EXCLUDED.label, notes = EXCLUDED.notes
                    """,
                    (region, tag_sn, x, y, offset, label, notes, user_id),
                )
                inserted += 1
            except Exception as e:
                errors.append(f"행 {i}: {e}")
        conn.commit()
        cur.close()
    finally:
        conn.close()
    return {"inserted": inserted, "errors": errors[:20], "total_errors": len(errors)}


@router.delete("/meters/{map_id}")
def delete_meter_map(map_id: int, region: str = "R01") -> dict:
    _ensure_enabled(region)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM tb_epanet_meter_map WHERE map_id = %s AND region = %s",
            (map_id, region),
        )
        conn.commit()
        cur.close()
        return {"deleted": map_id}
    finally:
        conn.close()


@router.delete("/meters")
def delete_all_meter_maps(region: str = "R01") -> dict:
    _ensure_enabled(region)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM tb_epanet_meter_map WHERE region = %s", (region,))
        n = cur.rowcount
        conn.commit()
        cur.close()
        return {"deleted_count": n}
    finally:
        conn.close()


