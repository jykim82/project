"""endpoints/epanet.py — EPANET 수리 시뮬레이션 API (Phase 1).

활성화: tb_comm_code (region, 'SITE_SETTING', 'EPANET_ENABLED')='Y'
SHP 위치: 환경변수 EPANET_SHP_BASE_DIR (기본 /data/files/gis/shp)
산출물: /data/files/epanet/{region}_{ts}.inp + tb_epanet_artifact 행

엔드포인트:
- GET    /admin/epanet/status                — 활성화·환경 상태
- POST   /admin/epanet/scan                  — SHP 메타 스캔 (변환 전 검증)
- POST   /admin/epanet/inp/generate          — SHP→.inp 변환 실행
- GET    /admin/epanet/inp/list              — 산출물 목록
- GET    /admin/epanet/inp/{artifact_id}/download — .inp 파일 다운로드
- DELETE /admin/epanet/inp/{artifact_id}     — 산출물 삭제

비활성(default) 시: status 는 응답, 나머지는 503.
"""

from __future__ import annotations

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
from epanet.simulator import run_steady_state


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/epanet", tags=["epanet"])


SHP_BASE_DIR = os.environ.get("EPANET_SHP_BASE_DIR", "/data/files/gis/shp")
INP_OUTPUT_DIR = os.environ.get(
    "EPANET_INP_OUTPUT_DIR", "/data/files/epanet"
)

PIPE_SHP_HINTS = (
    "SAA003",  # 송수관
    "SAA004",  # 배수관
)
RESERVOIR_SHP_HINT = "SA114"  # 배수지


def _ensure_enabled(region: str) -> None:
    if not is_enabled(region):
        raise HTTPException(
            status_code=503,
            detail="EPANET 모듈이 비활성 상태입니다. 사이트 설정에서 활성화하세요.",
        )


def _list_shp(base_dir: str) -> list[Path]:
    if not os.path.isdir(base_dir):
        return []
    return sorted(Path(base_dir).glob("*.shp"))


def _classify_shp(paths: list[Path]) -> dict:
    """파일명으로 송수관/배수관/배수지/기타 분류."""
    pipes: list[Path] = []
    reservoirs: list[Path] = []
    others: list[Path] = []
    for p in paths:
        name = p.name
        if any(hint in name for hint in PIPE_SHP_HINTS):
            pipes.append(p)
        elif RESERVOIR_SHP_HINT in name:
            reservoirs.append(p)
        else:
            others.append(p)
    return {"pipes": pipes, "reservoirs": reservoirs, "others": others}


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


# ===========================================================================
# E_MENU) 메뉴 활성/비활성 토글 (Phase 3.3 후속)
# ===========================================================================

class MenuSettingIn(BaseModel):
    region: str = "R01"
    menu_key: str
    enabled: bool


@router.get("/menu-settings")
def list_menu_settings(region: str = "R01") -> dict:
    """region 의 EPANET 표현 메뉴별 활성/비활성 상태."""
    _ensure_enabled(region)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT menu_key, label, enabled, updated_at, updated_by
              FROM tb_epanet_menu_setting
             WHERE region = %s
             ORDER BY menu_key
            """,
            (region,),
        )
        rows = cur.fetchall()
        cur.close()
        items = [{
            "menu_key": r[0],
            "label": r[1],
            "enabled": (r[2] == "Y"),
            "updated_at": r[3].isoformat() if r[3] else None,
            "updated_by": r[4],
        } for r in rows]
        return {"items": items}
    finally:
        conn.close()


@router.put("/menu-settings")
def update_menu_setting(req: MenuSettingIn, request: Request) -> dict:
    """단건 토글 변경. enabled 'Y'/'N' 으로 UPSERT."""
    _ensure_enabled(req.region)
    user_id = _get_user_id(request)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE tb_epanet_menu_setting
               SET enabled = %s, updated_at = NOW(), updated_by = %s
             WHERE region = %s AND menu_key = %s
            """,
            ("Y" if req.enabled else "N", user_id, req.region, req.menu_key),
        )
        rc = cur.rowcount
        conn.commit()
        cur.close()
        if rc == 0:
            raise HTTPException(404, detail=f"menu_key 없음: {req.menu_key}")
        return {"status": "OK", "menu_key": req.menu_key, "enabled": req.enabled}
    finally:
        conn.close()


def _menus_disabled(region: str) -> set:
    """enabled='N' 인 메뉴 키 집합."""
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT menu_key FROM tb_epanet_menu_setting "
            "WHERE region = %s AND enabled = 'N'",
            (region,),
        )
        rows = cur.fetchall()
        cur.close()
        return {r[0] for r in rows}
    except Exception:
        return set()
    finally:
        conn.close()


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


# ===========================================================================
# 0) GET /admin/epanet/data-quality — 메뉴별 데이터 품질 게이트
# ===========================================================================

# 메뉴별 필수·권장 데이터 매트릭스 (사양: docs/epanet-menu-spec.md §2.2)
_MENU_REQUIREMENTS = {
    "gis-flow":               {"required": ["HAS_PIPE_NETWORK"],
                               "recommended": ["HAS_ELEVATION", "HAS_DEMAND_PROFILE"]},
    "leak-suspicious":        {"required": ["HAS_PIPE_NETWORK", "HAS_METER_MAPPING"],
                               "recommended": ["HAS_ELEVATION", "HAS_DEMAND_PROFILE"]},
    "headloss-anomaly":       {"required": ["HAS_PIPE_NETWORK"],
                               "recommended": ["HAS_ELEVATION", "HAS_DEMAND_PROFILE",
                                               "HAS_METER_MAPPING"]},
    "valve-impact":           {"required": ["HAS_PIPE_NETWORK", "HAS_VALVE_DATA"],
                               "recommended": ["HAS_ELEVATION", "HAS_DEMAND_PROFILE"]},
    "pipe-break":             {"required": ["HAS_PIPE_NETWORK"],
                               "recommended": ["HAS_ELEVATION", "HAS_DEMAND_PROFILE"]},
    "pump-control":           {"required": ["HAS_PIPE_NETWORK", "HAS_PUMP_DATA"],
                               "recommended": ["HAS_ELEVATION", "HAS_DEMAND_PROFILE",
                                               "HAS_TIME_PATTERN"]},
    "scenario-diff":          {"required": ["HAS_PIPE_NETWORK"],
                               "recommended": ["HAS_ELEVATION", "HAS_DEMAND_PROFILE"]},
    "replacement-candidates": {"required": ["HAS_PIPE_NETWORK"],
                               "recommended": ["HAS_ELEVATION", "HAS_DEMAND_PROFILE"]},
    "network-aging":          {"required": ["HAS_PIPE_NETWORK", "HAS_METER_MAPPING"],
                               "recommended": []},
    "water-quality":          {"required": ["HAS_PIPE_NETWORK", "HAS_WATER_QUALITY_MODEL"],
                               "recommended": []},
}


def _check_data_quality(region: str) -> dict:
    """8 항목 데이터 품질 체크. 각 항목 ok=bool + detail=설명."""
    checks: dict = {}
    conn = get_db()
    try:
        cur = conn.cursor()
        # 1) HAS_PIPE_NETWORK
        cur.execute(
            "SELECT COUNT(*), MAX(link_count) FROM tb_epanet_artifact "
            "WHERE region = %s AND status = 'success'",
            (region,),
        )
        row = cur.fetchone()
        if row and row[0] > 0:
            checks["HAS_PIPE_NETWORK"] = {
                "ok": True,
                "detail": f"성공 산출물 {row[0]}건, 최대 {row[1]} links",
            }
        else:
            checks["HAS_PIPE_NETWORK"] = {
                "ok": False,
                "detail": "/admin/epanet 에서 INP 생성 필요",
            }

        # 가장 최근 시뮬에서 추가 항목 추출
        cur.execute(
            "SELECT result_data FROM tb_epanet_simulation_result "
            "WHERE region = %s AND status = 'success' "
            "ORDER BY created_at DESC LIMIT 1",
            (region,),
        )
        sim_row = cur.fetchone()
        cur.close()
    finally:
        conn.close()

    # 2~5) 시뮬 결과 기반 항목 (RESERVOIR_HEAD / ELEVATION / DEMAND / PATTERN)
    if not sim_row or not sim_row[0]:
        checks["HAS_RESERVOIR_HEAD"] = {"ok": False, "detail": "시뮬 결과 없음"}
        checks["HAS_ELEVATION"]      = {"ok": False, "detail": "시뮬 결과 없음"}
        checks["HAS_DEMAND_PROFILE"] = {"ok": False, "detail": "시뮬 결과 없음"}
    else:
        rd = sim_row[0]
        # head 다양성
        reservoirs = rd.get("reservoirs") or []
        heads = [r.get("head_m") for r in reservoirs if r.get("head_m") is not None]
        if heads and len(set(heads)) > 1:
            checks["HAS_RESERVOIR_HEAD"] = {
                "ok": True,
                "detail": f"{len(heads)} reservoir, head 분포 {min(heads):.1f}~{max(heads):.1f}m",
            }
        else:
            checks["HAS_RESERVOIR_HEAD"] = {
                "ok": False,
                "detail": "모든 reservoir head 동일 (default 가능성)",
            }

        # elevation — 시뮬 결과의 elevation_m 우선, 없으면 head_m - pressure_m 추정
        junctions = rd.get("junctions") or []
        elevs = []
        for j in junctions:
            ev = j.get("elevation_m")
            if ev is not None:
                elevs.append(float(ev))
            else:
                h = j.get("head_m")
                p = j.get("pressure_m")
                if h is not None and p is not None:
                    elevs.append(h - p)
        if elevs:
            stddev = (sum((e - sum(elevs) / len(elevs)) ** 2 for e in elevs) / len(elevs)) ** 0.5
            if stddev > 0.5:
                checks["HAS_ELEVATION"] = {
                    "ok": True,
                    "detail": f"표고 분포 {min(elevs):.1f}~{max(elevs):.1f}m (σ={stddev:.2f})",
                }
            else:
                checks["HAS_ELEVATION"] = {
                    "ok": False,
                    "detail": "모든 junction 표고 동일 (default 0m)",
                }
        else:
            checks["HAS_ELEVATION"] = {"ok": False, "detail": "표고 데이터 없음"}

        # demand
        demands = [j.get("demand_lps") for j in junctions if j.get("demand_lps") is not None]
        if demands and len(set(d for d in demands if d > 0)) > 1:
            checks["HAS_DEMAND_PROFILE"] = {
                "ok": True,
                "detail": f"수요 분포 {min(demands):.2f}~{max(demands):.2f} LPS",
            }
        else:
            checks["HAS_DEMAND_PROFILE"] = {
                "ok": False,
                "detail": "균등 demand (default 0.1 LPS) — 노드별 차이 없음",
            }

    # 6) HAS_TIME_PATTERN — 별도 테이블 미구현 (Phase 3.4 예정)
    checks["HAS_TIME_PATTERN"] = {
        "ok": False,
        "detail": "tb_epanet_time_pattern 미구현 (Phase 3.4)",
    }
    # 7) HAS_METER_MAPPING — Phase 3.3a tb_epanet_meter_map 카운트
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM tb_epanet_meter_map WHERE region = %s",
            (region,),
        )
        meter_count = cur.fetchone()[0] or 0
        cur.close()
        conn.close()
    except Exception:
        meter_count = 0
    if meter_count > 0:
        checks["HAS_METER_MAPPING"] = {
            "ok": True,
            "detail": f"센서 매핑 {meter_count}건",
        }
    else:
        checks["HAS_METER_MAPPING"] = {
            "ok": False,
            "detail": "tb_epanet_meter_map 비어있음 — /admin/epanet 에서 매핑 추가",
        }
    # 8) HAS_VALVE_DATA — INP 의 [VALVES] 섹션 검사 (가장 최근 artifact)
    # 9) HAS_PUMP_DATA — INP 의 [PUMPS] 섹션 검사
    valve_ok = False
    pump_ok = False
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT file_path FROM tb_epanet_artifact "
            "WHERE region = %s AND status = 'success' "
            "ORDER BY created_at DESC LIMIT 1",
            (region,),
        )
        r = cur.fetchone()
        cur.close()
        conn.close()
        if r and r[0] and os.path.exists(r[0]):
            text = open(r[0], "r", encoding="utf-8", errors="ignore").read()
            valve_ok = _section_has_data(text, "[VALVES]")
            pump_ok = _section_has_data(text, "[PUMPS]")
    except Exception:
        pass
    checks["HAS_VALVE_DATA"] = {
        "ok": valve_ok,
        "detail": "INP [VALVES] 섹션에 데이터" if valve_ok else "INP [VALVES] 비어있음 (밸브 SHP 미반영)",
    }
    checks["HAS_PUMP_DATA"] = {
        "ok": pump_ok,
        "detail": "INP [PUMPS] 섹션에 데이터" if pump_ok else "INP [PUMPS] 비어있음 (펌프 SHP 미반영)",
    }
    # 10) HAS_WATER_QUALITY_MODEL — Phase 6 별도 모델 입력 (현재 미구현)
    checks["HAS_WATER_QUALITY_MODEL"] = {
        "ok": False,
        "detail": "잔류염소·체류시간 모델 입력 미구현 (Phase 6)",
    }

    # 운영자가 명시적으로 비활성화한 메뉴
    disabled = _menus_disabled(region)

    # 메뉴별 ready/warning/blocked/disabled 분류
    menus_ready: list = []
    menus_warning: list = []
    menus_blocked: list = []
    menus_disabled_list: list = []
    for menu_key, req in _MENU_REQUIREMENTS.items():
        if menu_key in disabled:
            menus_disabled_list.append(menu_key)
            continue
        required_ok = all(checks.get(k, {}).get("ok", False) for k in req["required"])
        recommended_ok = all(checks.get(k, {}).get("ok", False) for k in req["recommended"])
        if not required_ok:
            menus_blocked.append(menu_key)
        elif not recommended_ok:
            menus_warning.append(menu_key)
        else:
            menus_ready.append(menu_key)

    return {
        "checks": checks,
        "menus_ready": menus_ready,
        "menus_warning": menus_warning,
        "menus_blocked": menus_blocked,
        "menus_disabled": menus_disabled_list,
    }


def _section_has_data(inp_text: str, section_name: str) -> bool:
    """INP 의 특정 섹션이 ; 외에 실제 데이터 행을 가지는지."""
    try:
        idx = inp_text.index(section_name)
    except ValueError:
        return False
    # 다음 섹션까지 스캔
    rest = inp_text[idx + len(section_name):]
    next_section = rest.find("\n[")
    block = rest[:next_section] if next_section >= 0 else rest
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        return True
    return False


@router.get("/data-quality")
def get_data_quality(region: str = "R01") -> dict:
    """8 항목 데이터 품질 + 메뉴별 ready/warning/blocked.

    토글 OFF 여도 항상 200 응답 (사이드바가 비활성 상태도 표시).
    """
    return _check_data_quality(region)


# ===========================================================================
# 1) GET /admin/epanet/status
# ===========================================================================

@router.get("/status")
def get_status(region: str = "R01") -> dict:
    """모듈 활성화·환경 상태 — 토글 OFF 여도 항상 200 응답."""
    enabled = is_enabled(region)
    wntr_ok = is_wntr_available()
    shp_files = _list_shp(SHP_BASE_DIR)
    classified = _classify_shp(shp_files)
    return {
        "enabled": enabled,
        "wntr_available": wntr_ok,
        "shp_base_dir": SHP_BASE_DIR,
        "inp_output_dir": INP_OUTPUT_DIR,
        "shp_total_count": len(shp_files),
        "shp_pipes": [p.name for p in classified["pipes"]],
        "shp_reservoirs": [p.name for p in classified["reservoirs"]],
        "shp_others_count": len(classified["others"]),
    }


# ===========================================================================
# 2) POST /admin/epanet/scan — 변환 전 검증
# ===========================================================================

class ScanRequest(BaseModel):
    region: str = "R01"
    only_pipes: bool = True


@router.post("/scan")
def scan_shp_files(req: ScanRequest) -> dict:
    _ensure_enabled(req.region)
    shp_files = _list_shp(SHP_BASE_DIR)
    if not shp_files:
        return {"results": [], "warning": f"SHP 파일이 없습니다: {SHP_BASE_DIR}"}

    classified = _classify_shp(shp_files)
    targets = classified["pipes"] + classified["reservoirs"]
    if not req.only_pipes:
        targets += classified["others"]

    results = []
    for path in targets:
        r = scan_shp(path)
        results.append({
            "file_name": r.file_name,
            "record_count": r.record_count,
            "geometry_type": r.geometry_type,
            "field_names": r.field_names,
            "encoding": r.encoding,
            "bbox": list(r.bbox) if r.bbox else None,
            "sample": r.sample,
            "error": r.error,
        })
    return {"results": results}


# ===========================================================================
# 3) POST /admin/epanet/inp/generate — 실제 변환
# ===========================================================================

class GenerateRequest(BaseModel):
    region: str = "R01"
    title: Optional[str] = None
    pipe_files: Optional[list[str]] = None        # 파일명만 (SHP_BASE_DIR 기준), None 이면 자동 분류
    reservoir_file: Optional[str] = None
    default_diameter_mm: float = 100.0
    default_roughness_c: float = 120.0
    default_demand_lps: float = 0.1               # 0 이면 flow 가 모두 0 → 화살표 방향 무의미
    use_elevation_points: bool = True             # tb_epanet_elevation_point 의 입력 표고를 IDW 보간
    use_synthetic_elevation: bool = False         # 시연용 합성 표고 (운영자 입력 전 미리보기)
    use_demand_points: bool = True                # tb_epanet_demand_point 의 입력 수요를 IDW 보간
    use_synthetic_demand: bool = False            # 시연용 합성 demand (도심 고/외곽 저)


@router.post("/inp/generate")
def generate_inp(req: GenerateRequest, request: Request) -> dict:
    _ensure_enabled(req.region)

    shp_files = _list_shp(SHP_BASE_DIR)
    if not shp_files:
        raise HTTPException(
            status_code=400,
            detail=f"SHP 파일이 없습니다: {SHP_BASE_DIR}",
        )

    if req.pipe_files:
        pipe_paths = [Path(SHP_BASE_DIR) / n for n in req.pipe_files]
        missing = [p.name for p in pipe_paths if not p.exists()]
        if missing:
            raise HTTPException(400, detail=f"SHP 파일 없음: {missing}")
    else:
        pipe_paths = _classify_shp(shp_files)["pipes"]
        if not pipe_paths:
            raise HTTPException(400, detail="송수관/배수관 SHP 가 없습니다.")

    if req.reservoir_file:
        reservoir_path: Optional[Path] = Path(SHP_BASE_DIR) / req.reservoir_file
        if not reservoir_path.exists():
            raise HTTPException(400, detail=f"배수지 SHP 없음: {req.reservoir_file}")
    else:
        reservoirs = _classify_shp(shp_files)["reservoirs"]
        reservoir_path = reservoirs[0] if reservoirs else None

    user_id = _get_user_id(request)
    title = req.title or f"SLM EPANET {req.region}"

    os.makedirs(INP_OUTPUT_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_name = f"{req.region}_{ts}.inp"
    out_path = os.path.join(INP_OUTPUT_DIR, out_name)

    # 산출물 행 — pending 으로 먼저 INSERT
    artifact_id = _insert_artifact(
        region=req.region,
        file_path=out_path,
        file_name=out_name,
        source_shp=", ".join([p.name for p in pipe_paths]
                             + ([reservoir_path.name] if reservoir_path else [])),
        created_by=user_id,
    )

    try:
        # 표고·수요 입력 조회 (운영자 입력 점들 → IDW 보간 입력)
        elevation_points: list = []
        demand_points: list = []
        if req.use_elevation_points or req.use_demand_points:
            conn_e = get_db()
            try:
                cur = conn_e.cursor()
                if req.use_elevation_points:
                    cur.execute(
                        "SELECT x, y, elevation_m FROM tb_epanet_elevation_point WHERE region = %s",
                        (req.region,),
                    )
                    elevation_points = [(float(r[0]), float(r[1]), float(r[2])) for r in cur.fetchall()]
                if req.use_demand_points:
                    cur.execute(
                        "SELECT x, y, demand_lps FROM tb_epanet_demand_point WHERE region = %s",
                        (req.region,),
                    )
                    demand_points = [(float(r[0]), float(r[1]), float(r[2])) for r in cur.fetchall()]
                cur.close()
            finally:
                conn_e.close()

        result = convert_pipes_to_inp(
            pipe_shp_paths=pipe_paths,
            reservoir_shp_path=reservoir_path,
            network_title=title,
            default_diameter_mm=req.default_diameter_mm,
            default_roughness_c=req.default_roughness_c,
            default_demand_lps=req.default_demand_lps,
            elevation_points=elevation_points if elevation_points else None,
            use_synthetic_elevation=req.use_synthetic_elevation,
            demand_points=demand_points if demand_points else None,
            use_synthetic_demand=req.use_synthetic_demand,
        )
        Path(out_path).write_text(result.inp_text, encoding="utf-8")
        size = os.path.getsize(out_path)

        validation = (
            validate_with_wntr(out_path) if is_wntr_available() else
            {"valid": None, "error": "wntr 미설치 — 검증 스킵"}
        )

        _update_artifact_success(
            artifact_id=artifact_id,
            node_count=result.node_count,
            link_count=result.link_count,
            file_size_bytes=size,
        )
        return {
            "artifact_id": artifact_id,
            "file_name": out_name,
            "file_path": out_path,
            "file_size_bytes": size,
            "node_count": result.node_count,
            "link_count": result.link_count,
            "reservoir_count": result.reservoir_count,
            "skipped_records": result.skipped_records,
            "warnings": result.warnings,
            "wntr_validation": validation,
        }
    except Exception as e:
        logger.exception("EPANET .inp 변환 실패")
        _update_artifact_failed(artifact_id=artifact_id, error_message=str(e))
        raise HTTPException(500, detail=f"변환 실패: {e}")


# ===========================================================================
# 4) GET /admin/epanet/inp/list
# ===========================================================================

@router.get("/inp/list")
def list_artifacts(region: str = "R01", limit: int = 50) -> dict:
    _ensure_enabled(region)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT artifact_id, region, file_name, source_shp,
                   node_count, link_count, status, file_size_bytes,
                   error_message, created_at, created_by
              FROM tb_epanet_artifact
             WHERE region = %s
             ORDER BY created_at DESC
             LIMIT %s
            """,
            (region, limit),
        )
        rows = cur.fetchall()
        cur.close()
        items = [{
            "artifact_id": r[0], "region": r[1], "file_name": r[2],
            "source_shp": r[3], "node_count": r[4], "link_count": r[5],
            "status": r[6], "file_size_bytes": r[7], "error_message": r[8],
            "created_at": r[9].isoformat() if r[9] else None,
            "created_by": r[10],
        } for r in rows]
        return {"items": items, "total": len(items)}
    finally:
        conn.close()


# ===========================================================================
# 5) GET /admin/epanet/inp/{id}/download
# ===========================================================================

@router.get("/inp/{artifact_id}/download")
def download_inp(artifact_id: int, region: str = "R01"):
    _ensure_enabled(region)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT file_path, file_name, status FROM tb_epanet_artifact "
            "WHERE artifact_id = %s AND region = %s",
            (artifact_id, region),
        )
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, detail="산출물 없음")
    file_path, file_name, status = row
    if status != "success":
        raise HTTPException(409, detail=f"상태가 success 가 아닙니다: {status}")
    if not os.path.exists(file_path):
        raise HTTPException(410, detail="파일이 디스크에 없습니다.")
    return FileResponse(
        file_path,
        filename=file_name,
        media_type="application/octet-stream",
    )


# ===========================================================================
# 6-A) POST /admin/epanet/inp/{id}/simulate — 정상상태 시뮬레이션 실행
# ===========================================================================

@router.post("/inp/{artifact_id}/simulate")
def simulate_inp(artifact_id: int, request: Request, region: str = "R01") -> dict:
    _ensure_enabled(region)
    if not is_wntr_available():
        raise HTTPException(
            status_code=501,
            detail="wntr 라이브러리가 설치되지 않았습니다. Docker 이미지를 재빌드하세요.",
        )

    # artifact 조회
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT file_path, status FROM tb_epanet_artifact "
            "WHERE artifact_id = %s AND region = %s",
            (artifact_id, region),
        )
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, detail="산출물 없음")
    file_path, art_status = row
    if art_status != "success":
        raise HTTPException(409, detail=f"INP 산출물 상태가 success 가 아닙니다: {art_status}")
    if not os.path.exists(file_path):
        raise HTTPException(410, detail="INP 파일이 디스크에 없습니다.")

    user_id = _get_user_id(request)
    sim_id = _insert_simulation_pending(
        artifact_id=artifact_id, region=region, sim_type="steady",
        created_by=user_id,
    )
    result = run_steady_state(file_path)

    if not result.success:
        _update_simulation_failed(
            sim_id=sim_id,
            duration_ms=result.duration_ms,
            error_message=result.error or "unknown",
        )
        raise HTTPException(500, detail=f"시뮬레이션 실패: {result.error}")

    _update_simulation_success(sim_id=sim_id, result=result)
    return {
        "sim_id": sim_id,
        "artifact_id": artifact_id,
        "status": "success",
        "node_count": result.node_count,
        "link_count": result.link_count,
        "min_pressure_m": result.min_pressure_m,
        "max_pressure_m": result.max_pressure_m,
        "avg_pressure_m": result.avg_pressure_m,
        "min_flow_lps": result.min_flow_lps,
        "max_flow_lps": result.max_flow_lps,
        "duration_ms": result.duration_ms,
        "bbox": list(result.bbox) if result.bbox else None,
        "bbox_lnglat": list(result.bbox_lnglat) if result.bbox_lnglat else None,
        "junctions": result.junctions,
        "pipes": result.pipes,
        "reservoirs": result.reservoirs,
    }


# ===========================================================================
# 6-B) GET /admin/epanet/inp/{id}/simulations — 산출물별 시뮬레이션 이력
# ===========================================================================

@router.get("/inp/{artifact_id}/simulations")
def list_simulations(artifact_id: int, region: str = "R01", limit: int = 20) -> dict:
    _ensure_enabled(region)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT sim_id, sim_type, status,
                   node_count, link_count,
                   min_pressure_m, max_pressure_m, avg_pressure_m,
                   min_flow_lps, max_flow_lps,
                   duration_ms, error_message, created_at, created_by
              FROM tb_epanet_simulation_result
             WHERE artifact_id = %s AND region = %s
             ORDER BY created_at DESC
             LIMIT %s
            """,
            (artifact_id, region, limit),
        )
        rows = cur.fetchall()
        cur.close()
        items = [{
            "sim_id": r[0], "sim_type": r[1], "status": r[2],
            "node_count": r[3], "link_count": r[4],
            "min_pressure_m": r[5], "max_pressure_m": r[6], "avg_pressure_m": r[7],
            "min_flow_lps": r[8], "max_flow_lps": r[9],
            "duration_ms": r[10], "error_message": r[11],
            "created_at": r[12].isoformat() if r[12] else None,
            "created_by": r[13],
        } for r in rows]
        return {"items": items, "total": len(items)}
    finally:
        conn.close()


# ===========================================================================
# 6-D) GET /admin/epanet/sim/latest — region 별 가장 최근 success 시뮬
# ===========================================================================

@router.get("/sim/latest")
def get_latest_simulation(region: str = "R01") -> dict:
    """region 의 가장 최근 success 시뮬 상세 (artifact 무관). 없으면 404."""
    _ensure_enabled(region)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT sim_id, artifact_id, sim_type, status,
                   node_count, link_count,
                   min_pressure_m, max_pressure_m, avg_pressure_m,
                   min_flow_lps, max_flow_lps,
                   result_data, duration_ms, error_message,
                   created_at, created_by
              FROM tb_epanet_simulation_result
             WHERE region = %s AND status = 'success'
             ORDER BY created_at DESC
             LIMIT 1
            """,
            (region,),
        )
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, detail="성공한 시뮬레이션이 없습니다.")
    return {
        "sim_id": row[0], "artifact_id": row[1], "sim_type": row[2], "status": row[3],
        "node_count": row[4], "link_count": row[5],
        "min_pressure_m": row[6], "max_pressure_m": row[7], "avg_pressure_m": row[8],
        "min_flow_lps": row[9], "max_flow_lps": row[10],
        "result_data": row[11], "duration_ms": row[12], "error_message": row[13],
        "created_at": row[14].isoformat() if row[14] else None,
        "created_by": row[15],
    }


# ===========================================================================
# 6-C) GET /admin/epanet/sim/{sim_id} — 시뮬레이션 상세 (result_data 포함)
# ===========================================================================

@router.get("/sim/{sim_id}")
def get_simulation(sim_id: int, region: str = "R01") -> dict:
    _ensure_enabled(region)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT sim_id, artifact_id, sim_type, status,
                   node_count, link_count,
                   min_pressure_m, max_pressure_m, avg_pressure_m,
                   min_flow_lps, max_flow_lps,
                   result_data, duration_ms, error_message,
                   created_at, created_by
              FROM tb_epanet_simulation_result
             WHERE sim_id = %s AND region = %s
            """,
            (sim_id, region),
        )
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, detail="시뮬레이션 결과 없음")
    return {
        "sim_id": row[0], "artifact_id": row[1], "sim_type": row[2], "status": row[3],
        "node_count": row[4], "link_count": row[5],
        "min_pressure_m": row[6], "max_pressure_m": row[7], "avg_pressure_m": row[8],
        "min_flow_lps": row[9], "max_flow_lps": row[10],
        "result_data": row[11], "duration_ms": row[12], "error_message": row[13],
        "created_at": row[14].isoformat() if row[14] else None,
        "created_by": row[15],
    }


# ===========================================================================
# 6) DELETE /admin/epanet/inp/{id}
# ===========================================================================

@router.delete("/inp/{artifact_id}")
def delete_artifact(artifact_id: int, region: str = "R01") -> dict:
    _ensure_enabled(region)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT file_path FROM tb_epanet_artifact "
            "WHERE artifact_id = %s AND region = %s",
            (artifact_id, region),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, detail="산출물 없음")
        file_path = row[0]
        cur.execute(
            "DELETE FROM tb_epanet_artifact WHERE artifact_id = %s AND region = %s",
            (artifact_id, region),
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()
    try:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
    except Exception as e:
        logger.warning(f"산출물 파일 삭제 실패 (DB 행은 삭제됨): {e}")
    return {"deleted": artifact_id}


# ===========================================================================
# 내부 헬퍼
# ===========================================================================

def _get_user_id(request: Request) -> str:
    """JWT 미들웨어가 request.state.user 에 채운 user_id 사용 (없으면 'system')."""
    user = getattr(request.state, "user", None)
    if isinstance(user, dict):
        return user.get("user_id") or user.get("sub") or "system"
    return "system"


def _insert_artifact(*, region: str, file_path: str, file_name: str,
                     source_shp: str, created_by: str) -> int:
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO tb_epanet_artifact
                (region, file_path, file_name, source_shp, status, created_by)
            VALUES (%s, %s, %s, %s, 'pending', %s)
            RETURNING artifact_id
            """,
            (region, file_path, file_name, source_shp, created_by),
        )
        artifact_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        return artifact_id
    finally:
        conn.close()


def _update_artifact_success(*, artifact_id: int, node_count: int,
                             link_count: int, file_size_bytes: int) -> None:
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE tb_epanet_artifact
               SET status = 'success', node_count = %s, link_count = %s,
                   file_size_bytes = %s, error_message = NULL
             WHERE artifact_id = %s
            """,
            (node_count, link_count, file_size_bytes, artifact_id),
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()


def _update_artifact_failed(*, artifact_id: int, error_message: str) -> None:
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE tb_epanet_artifact SET status = 'failed', error_message = %s "
            "WHERE artifact_id = %s",
            (error_message[:1000], artifact_id),
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()


def _insert_simulation_pending(*, artifact_id: int, region: str, sim_type: str,
                               created_by: str) -> int:
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO tb_epanet_simulation_result
                (artifact_id, region, sim_type, status, created_by)
            VALUES (%s, %s, %s, 'pending', %s)
            RETURNING sim_id
            """,
            (artifact_id, region, sim_type, created_by),
        )
        sim_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        return sim_id
    finally:
        conn.close()


def _update_simulation_success(*, sim_id: int, result) -> None:
    """SimulationResult 객체로 결과 행을 갱신."""
    import json
    payload = {"junctions": result.junctions, "pipes": result.pipes}
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE tb_epanet_simulation_result
               SET status = 'success',
                   node_count = %s, link_count = %s,
                   min_pressure_m = %s, max_pressure_m = %s, avg_pressure_m = %s,
                   min_flow_lps = %s, max_flow_lps = %s,
                   result_data = %s::jsonb,
                   duration_ms = %s, error_message = NULL
             WHERE sim_id = %s
            """,
            (result.node_count, result.link_count,
             result.min_pressure_m, result.max_pressure_m, result.avg_pressure_m,
             result.min_flow_lps, result.max_flow_lps,
             json.dumps(payload, ensure_ascii=False),
             result.duration_ms, sim_id),
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()


def _update_simulation_failed(*, sim_id: int, duration_ms: int,
                              error_message: str) -> None:
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE tb_epanet_simulation_result
               SET status = 'failed', duration_ms = %s, error_message = %s
             WHERE sim_id = %s
            """,
            (duration_ms, error_message[:1000], sim_id),
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()
