"""
시설유형별 CRUD 엔드포인트 모듈

배수지(Reservoir), 가압장(Booster), 감압시설(Pressure Reducing), 블록(Block) CRUD.
facility_crud.py에서 분리 — init()으로 DB 커넥션 함수를 주입받아 사용.
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from endpoints.audit import get_actor, write_audit
from pydantic import BaseModel

logger = logging.getLogger("slm")

router = APIRouter()

_get_db_connection = None


def init(get_db_connection_fn):
    """DB 커넥션 팩토리 함수를 주입받는다."""
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


# =============================================================================
# 배수지 관리 API
# =============================================================================

def _serialize_reservoir_info(r: tuple) -> dict:
    """tb_service_reservoir_info row → dict (general_overview flat 변환).
    SELECT 순서: sitename, general_overview, install_year, service_area,
                 zone_count, zone_1_area..zone_5_height (10 cols)
    """
    go = r[1] if isinstance(r[1], dict) else (json.loads(r[1]) if r[1] else {})
    spec = go.get("reservoir_spec", {}) if isinstance(go.get("reservoir_spec"), dict) else {}
    return {
        "sitename": r[0],
        "install_year": r[2],
        "service_area": r[3],
        "zone_count": r[4],
        "zone_1_area": float(r[5]) if r[5] is not None else None,
        "zone_1_height": float(r[6]) if r[6] is not None else None,
        "zone_2_area": float(r[7]) if r[7] is not None else None,
        "zone_2_height": float(r[8]) if r[8] is not None else None,
        "zone_3_area": float(r[9]) if r[9] is not None else None,
        "zone_3_height": float(r[10]) if r[10] is not None else None,
        "zone_4_area": float(r[11]) if r[11] is not None else None,
        "zone_4_height": float(r[12]) if r[12] is not None else None,
        "zone_5_area": float(r[13]) if r[13] is not None else None,
        "zone_5_height": float(r[14]) if r[14] is not None else None,
        # general_overview flat
        "install_location": go.get("install_location"),
        "operating_status": go.get("operating_status"),
        "supply_population": go.get("supply_population"),
        "facility_capacity_m3": go.get("facility_capacity_m3"),
        "reservoir_count": spec.get("count"),
        "hwl": spec.get("H.W.L"),
        "lwl": spec.get("L.W.L"),
        "emergency_water_plan": go.get("emergency_water_plan"),
        "water_truck_accessible": go.get("water_truck_accessible"),
        "water_truck_turning_possible": go.get("water_truck_turning_possible"),
        "pump_required": go.get("pump_required"),
        "supply_position": go.get("supply_position"),
        "supply_time_hours": go.get("supply_time_hours"),
    }


def _serialize_reservoir_status(r: tuple) -> dict:
    """tb_service_reservoir_status row → dict.
    SELECT 순서: sitename, total_supply_time, supply_time_status,
                 supply_time_reason, meta
    """
    meta_raw = r[4]
    if isinstance(meta_raw, str):
        meta_raw = json.loads(meta_raw)
    meta = meta_raw if isinstance(meta_raw, list) else []
    return {
        "sitename": r[0],
        "total_supply_time": float(r[1]) if r[1] is not None else None,
        "supply_time_status": r[2],
        "supply_time_reason": r[3],
        "equipment_meta": meta,
    }


_RESERVOIR_GO_KEYS = (
    "install_location", "operating_status", "supply_population",
    "facility_capacity_m3", "pump_required", "supply_position",
    "supply_time_hours",
)


def _build_reservoir_general_overview(body: dict) -> dict:
    """프론트엔드 flat 필드 → general_overview JSONB 조립."""
    go: dict = {}
    for key in _RESERVOIR_GO_KEYS:
        val = body.get(key)
        if val is not None:
            go[key] = val
    spec: dict = {}
    if body.get("reservoir_count") is not None:
        spec["count"] = body["reservoir_count"]
    if body.get("hwl") is not None:
        spec["H.W.L"] = body["hwl"]
    if body.get("lwl") is not None:
        spec["L.W.L"] = body["lwl"]
    if spec:
        go["reservoir_spec"] = spec
    if body.get("emergency_water_plan") is not None:
        go["emergency_water_plan"] = body["emergency_water_plan"]
    if body.get("water_truck_accessible") is not None:
        go["water_truck_accessible"] = body["water_truck_accessible"]
    if body.get("water_truck_turning_possible") is not None:
        go["water_truck_turning_possible"] = body["water_truck_turning_possible"]
    return go


def _friendly_error(e: Exception) -> str:
    """DB 원문 대신 운영자가 이해할 수 있는 메시지.

    현장명 중복은 구축 중 가장 흔한 실패인데, 원문(duplicate key value violates
    unique constraint ...)을 그대로 띄우면 무엇을 고쳐야 할지 알 수 없다.
    """
    text = str(e)
    if "duplicate key" in text or "unique constraint" in text:
        return "이미 등록된 현장명입니다. 다른 이름을 쓰거나 기존 시설을 수정하세요."
    return text


@router.get("/reservoirs")
async def get_reservoirs(
    keyword: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
):
    """배수지 목록 조회 (페이징+키워드 검색)."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()

        wheres, params = [], []
        if keyword:
            wheres.append("(i.sitename ILIKE %s OR i.service_area ILIKE %s)")
            kw = f"%{keyword}%"
            params.extend([kw, kw])

        where_sql = " AND ".join(wheres) if wheres else "TRUE"

        cur.execute(f"SELECT COUNT(*) FROM tb_service_reservoir_info i WHERE {where_sql}", params)
        total = cur.fetchone()[0]

        offset = (page - 1) * page_size
        cur.execute(f"""
            SELECT i.sitename, i.general_overview, i.install_year, i.service_area,
                   i.zone_count, i.zone_1_area, i.zone_1_height,
                   i.zone_2_area, i.zone_2_height, i.zone_3_area, i.zone_3_height,
                   i.zone_4_area, i.zone_4_height, i.zone_5_area, i.zone_5_height
            FROM tb_service_reservoir_info i
            WHERE {where_sql}
            ORDER BY i.sitename
            LIMIT %s OFFSET %s
        """, params + [page_size, offset])
        rows = cur.fetchall()
        cur.close()

        data = [_serialize_reservoir_info(r) for r in rows]
        return {"status": "OK", "data": data, "total": total, "page": page, "page_size": page_size}
    except Exception as e:
        logger.error(f"배수지 목록 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.get("/reservoirs/{sitename}")
async def get_reservoir_detail(sitename: str):
    """배수지 상세 조회 (info + status)."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT sitename, general_overview, install_year, service_area,
                   zone_count, zone_1_area, zone_1_height,
                   zone_2_area, zone_2_height, zone_3_area, zone_3_height,
                   zone_4_area, zone_4_height, zone_5_area, zone_5_height
            FROM tb_service_reservoir_info
            WHERE sitename = %s
        """, (sitename,))
        info_row = cur.fetchone()
        if not info_row:
            cur.close()
            return {"status": "ERROR", "message": f"'{sitename}' 배수지를 찾을 수 없습니다."}

        cur.execute("""
            SELECT sitename, total_supply_time, supply_time_status,
                   supply_time_reason, meta
            FROM tb_service_reservoir_status
            WHERE sitename = %s
        """, (sitename,))
        status_row = cur.fetchone()
        cur.close()

        info = _serialize_reservoir_info(info_row)
        status = _serialize_reservoir_status(status_row) if status_row else None
        return {"status": "OK", "info": info, "reservoir_status": status}
    except Exception as e:
        logger.error(f"배수지 상세 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.post("/reservoirs")
async def create_reservoir(request: Request, actor: dict = Depends(get_actor)):
    """배수지 추가 (info + status 양쪽 INSERT)."""
    conn = None
    try:
        body = await request.json()
        sitename = body.get("sitename", "").strip()
        if not sitename:
            return {"status": "ERROR", "message": "현장명은 필수입니다."}

        conn = _get_db_connection()
        cur = conn.cursor()

        cur.execute("SELECT 1 FROM tb_service_reservoir_info WHERE sitename = %s", (sitename,))
        if cur.fetchone():
            cur.close()
            return {"status": "ERROR", "message": f"'{sitename}' 배수지가 이미 존재합니다."}

        go = _build_reservoir_general_overview(body)

        cur.execute("""
            INSERT INTO tb_service_reservoir_info
                (sitename, general_overview, install_year, service_area, zone_count,
                 zone_1_area, zone_1_height, zone_2_area, zone_2_height,
                 zone_3_area, zone_3_height, zone_4_area, zone_4_height,
                 zone_5_area, zone_5_height,
                 water_level_unit, reservoir_area_unit)
            VALUES (%s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    'm', '㎥')
        """, (
            sitename, json.dumps(go, ensure_ascii=False),
            body.get("install_year"), body.get("service_area"), body.get("zone_count"),
            body.get("zone_1_area"), body.get("zone_1_height"),
            body.get("zone_2_area"), body.get("zone_2_height"),
            body.get("zone_3_area"), body.get("zone_3_height"),
            body.get("zone_4_area"), body.get("zone_4_height"),
            body.get("zone_5_area"), body.get("zone_5_height"),
        ))

        # status INSERT (equipment_meta 배열)
        eq_meta = body.get("equipment_meta", [])
        cur.execute("""
            INSERT INTO tb_service_reservoir_status
                (sitename, total_supply_time, water_level_unit, meta)
            VALUES (%s, %s, 'm', %s)
        """, (
            sitename,
            body.get("total_supply_time"),
            json.dumps(eq_meta, ensure_ascii=False),
        ))

        conn.commit()
        cur.close()
        write_audit(conn, actor=actor, action="create", target_type="reservoir",
                    target_key=sitename, summary=f"배수지 추가 {sitename}", request=request)
        return {"status": "OK", "sitename": sitename}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"배수지 추가 실패: {e}")
        return {"status": "ERROR", "message": _friendly_error(e)}
    finally:
        if conn:
            conn.close()


@router.put("/reservoirs/{sitename}")
async def update_reservoir(sitename: str, request: Request, actor: dict = Depends(get_actor)):
    """배수지 수정 (info UPDATE + status UPSERT)."""
    conn = None
    try:
        body = await request.json()
        conn = _get_db_connection()
        cur = conn.cursor()

        cur.execute("SELECT 1 FROM tb_service_reservoir_info WHERE sitename = %s", (sitename,))
        if not cur.fetchone():
            cur.close()
            return {"status": "ERROR", "message": f"'{sitename}' 배수지를 찾을 수 없습니다."}

        go = _build_reservoir_general_overview(body)

        cur.execute("""
            UPDATE tb_service_reservoir_info SET
                general_overview = %s, install_year = %s, service_area = %s,
                zone_count = %s,
                zone_1_area = %s, zone_1_height = %s,
                zone_2_area = %s, zone_2_height = %s,
                zone_3_area = %s, zone_3_height = %s,
                zone_4_area = %s, zone_4_height = %s,
                zone_5_area = %s, zone_5_height = %s
            WHERE sitename = %s
        """, (
            json.dumps(go, ensure_ascii=False),
            body.get("install_year"), body.get("service_area"), body.get("zone_count"),
            body.get("zone_1_area"), body.get("zone_1_height"),
            body.get("zone_2_area"), body.get("zone_2_height"),
            body.get("zone_3_area"), body.get("zone_3_height"),
            body.get("zone_4_area"), body.get("zone_4_height"),
            body.get("zone_5_area"), body.get("zone_5_height"),
            sitename,
        ))

        # status UPSERT (equipment_meta 배열 + total_supply_time)
        eq_meta = body.get("equipment_meta", [])
        cur.execute("""
            INSERT INTO tb_service_reservoir_status
                (sitename, total_supply_time, water_level_unit, meta)
            VALUES (%s, %s, 'm', %s)
            ON CONFLICT (sitename) DO UPDATE SET
                total_supply_time = EXCLUDED.total_supply_time,
                meta = EXCLUDED.meta
        """, (
            sitename,
            body.get("total_supply_time"),
            json.dumps(eq_meta, ensure_ascii=False),
        ))

        conn.commit()
        cur.close()
        write_audit(conn, actor=actor, action="update", target_type="reservoir",
                    target_key=sitename, summary=f"배수지 수정 {sitename}", request=request)
        return {"status": "OK", "updated": 1}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"배수지 수정 실패: {e}")
        return {"status": "ERROR", "message": _friendly_error(e)}
    finally:
        if conn:
            conn.close()


@router.delete("/reservoirs/{sitename}")
async def delete_reservoir(sitename: str, request: Request, dry_run: bool = Query(False), actor: dict = Depends(get_actor)):
    """배수지 삭제 (dry_run=true → 영향만 확인)."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM tb_service_reservoir_status WHERE sitename = %s", (sitename,))
        status_cnt = cur.fetchone()[0]

        if dry_run:
            cur.close()
            return {"status": "OK", "dry_run": True, "related": {"status_rows": status_cnt}}

        cur.execute("DELETE FROM tb_service_reservoir_status WHERE sitename = %s", (sitename,))
        cur.execute("DELETE FROM tb_service_reservoir_info WHERE sitename = %s", (sitename,))
        conn.commit()
        deleted = cur.rowcount
        cur.close()
        if deleted:
            write_audit(conn, actor=actor, action="delete", target_type="reservoir",
                        target_key=sitename, summary=f"배수지 삭제 {sitename}", request=request)
        return {"status": "OK", "deleted": deleted}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"배수지 삭제 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


# =============================================================================
# 가압장 관리 (Booster Station)
# =============================================================================


def _serialize_booster_info(r: tuple) -> dict:
    """tb_service_booster_station_info row → flat dict."""
    go = r[1] if isinstance(r[1], dict) else (json.loads(r[1]) if r[1] else {})
    pump = go.get("pump", {}) if isinstance(go.get("pump"), dict) else {}
    return {
        "sitename": r[0],
        "install_location": go.get("install_location"),
        "operating_status": go.get("operating_status"),
        "facility_capacity_m3": go.get("facility_capacity_m3"),
        "booster_type": go.get("booster_type"),
        "install_year": go.get("install_year"),
        "pump_count": pump.get("count"),
        "pump_head_m": pump.get("head_m"),
        "pump_contractor": pump.get("contractor"),
        "pump_manufacturer": pump.get("manufacturer"),
        "reservoir_linked": pump.get("reservoir_linked"),
        "linked_reservoirs": pump.get("linked_reservoirs"),
        "infiltration_well_count": r[2],
        "zone_1_area": float(r[3]) if r[3] is not None else None,
        "zone_1_height": float(r[4]) if r[4] is not None else None,
        "zone_2_area": float(r[5]) if r[5] is not None else None,
        "zone_2_height": float(r[6]) if r[6] is not None else None,
        "pump_type": r[9],
        "normal_operation_pump_count": r[10],
    }


def _serialize_booster_status(r: tuple) -> dict:
    """tb_service_booster_station_status row → dict."""
    meta_raw = r[11]
    if isinstance(meta_raw, str):
        meta_raw = json.loads(meta_raw)
    meta = meta_raw if isinstance(meta_raw, list) else []
    return {
        "sitename": r[0],
        "pump_control_mode": r[1],
        "pump_start_threshold": float(r[2]) if r[2] is not None else None,
        "pump_stop_threshold": float(r[3]) if r[3] is not None else None,
        "pump_start_pressure": float(r[4]) if r[4] is not None else None,
        "booster_inlet_pressure": float(r[5]) if r[5] is not None else None,
        "booster_outlet_pressure": float(r[6]) if r[6] is not None else None,
        "booster_avg_pressure": float(r[7]) if r[7] is not None else None,
        "level_unit": r[8],
        "pressure_unit": r[9],
        "running_pump_count": r[10],
        "equipment_meta": meta,
        "linked_reservoir_name": r[12],
        "normal_operation_pump_count": r[13],
    }


_BOOSTER_GO_KEYS = (
    "install_location", "operating_status", "facility_capacity_m3",
    "booster_type", "install_year",
)


def _build_booster_general_overview(body: dict) -> dict:
    """flat 필드 → general_overview JSONB 조립."""
    go: dict = {}
    for key in _BOOSTER_GO_KEYS:
        val = body.get(key)
        if val is not None:
            go[key] = val
    pump: dict = {}
    for pk in ("pump_count", "pump_head_m", "pump_contractor",
               "pump_manufacturer", "reservoir_linked", "linked_reservoirs"):
        val = body.get(pk)
        if val is not None:
            # remove "pump_" prefix for JSONB key
            jk = pk.replace("pump_", "") if pk.startswith("pump_") else pk
            pump[jk] = val
    if pump:
        go["pump"] = pump
    return go


_BOOSTER_INFO_COLS = (
    "sitename, general_overview, infiltration_well_count, "
    "zone_1_area, zone_1_height, zone_2_area, zone_2_height, "
    "infiltration_well_unit, infiltration_well_level_unit, "
    "pump_type, normal_operation_pump_count"
)

_BOOSTER_STATUS_COLS = (
    "sitename, pump_control_mode, pump_start_threshold, pump_stop_threshold, "
    "pump_start_pressure, booster_inlet_pressure, booster_outlet_pressure, "
    "booster_avg_pressure, level_unit, pressure_unit, running_pump_count, "
    "meta, linked_reservoir_name, normal_operation_pump_count"
)


@router.get("/boosters")
async def get_boosters(
    keyword: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
):
    """가압장 목록 조회."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        wheres, params = [], []
        if keyword:
            wheres.append("(sitename ILIKE %s)")
            params.append(f"%{keyword}%")
        where_sql = " AND ".join(wheres) if wheres else "TRUE"
        cur.execute(f"SELECT COUNT(*) FROM tb_service_booster_station_info WHERE {where_sql}", params)
        total = cur.fetchone()[0]
        offset = (page - 1) * page_size
        cur.execute(
            f"SELECT {_BOOSTER_INFO_COLS} FROM tb_service_booster_station_info "
            f"WHERE {where_sql} ORDER BY sitename LIMIT %s OFFSET %s",
            params + [page_size, offset],
        )
        data = [_serialize_booster_info(r) for r in cur.fetchall()]
        cur.close()
        return {"status": "OK", "data": data, "total": total, "page": page, "page_size": page_size}
    except Exception as e:
        logger.error(f"가압장 목록 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.get("/boosters/{sitename}")
async def get_booster_detail(sitename: str):
    """가압장 상세 조회 (info + status)."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute(f"SELECT {_BOOSTER_INFO_COLS} FROM tb_service_booster_station_info WHERE sitename = %s", (sitename,))
        info_row = cur.fetchone()
        if not info_row:
            cur.close()
            return {"status": "ERROR", "message": f"'{sitename}' 가압장을 찾을 수 없습니다."}
        cur.execute(f"SELECT {_BOOSTER_STATUS_COLS} FROM tb_service_booster_station_status WHERE sitename = %s", (sitename,))
        status_row = cur.fetchone()
        cur.close()
        return {
            "status": "OK",
            "info": _serialize_booster_info(info_row),
            "booster_status": _serialize_booster_status(status_row) if status_row else None,
        }
    except Exception as e:
        logger.error(f"가압장 상세 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.post("/boosters")
async def create_booster(request: Request, actor: dict = Depends(get_actor)):
    """가압장 추가."""
    conn = None
    try:
        body = await request.json()
        sitename = body.get("sitename", "").strip()
        if not sitename:
            return {"status": "ERROR", "message": "현장명은 필수입니다."}
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM tb_service_booster_station_info WHERE sitename = %s", (sitename,))
        if cur.fetchone():
            cur.close()
            return {"status": "ERROR", "message": f"'{sitename}' 가압장이 이미 존재합니다."}
        go = _build_booster_general_overview(body)
        cur.execute("""
            INSERT INTO tb_service_booster_station_info
                (sitename, general_overview, infiltration_well_count,
                 zone_1_area, zone_1_height, zone_2_area, zone_2_height,
                 pump_type, normal_operation_pump_count)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            sitename, json.dumps(go, ensure_ascii=False),
            body.get("infiltration_well_count"),
            body.get("zone_1_area"), body.get("zone_1_height"),
            body.get("zone_2_area"), body.get("zone_2_height"),
            body.get("pump_type"), body.get("normal_operation_pump_count"),
        ))
        eq_meta = body.get("equipment_meta", [])
        cur.execute("""
            INSERT INTO tb_service_booster_station_status
                (sitename, pump_control_mode, pump_start_threshold, pump_stop_threshold,
                 pump_start_pressure, booster_inlet_pressure, booster_outlet_pressure,
                 booster_avg_pressure, running_pump_count, linked_reservoir_name,
                 normal_operation_pump_count, meta)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            sitename, body.get("pump_control_mode"),
            body.get("pump_start_threshold"), body.get("pump_stop_threshold"),
            body.get("pump_start_pressure"),
            body.get("booster_inlet_pressure"), body.get("booster_outlet_pressure"),
            body.get("booster_avg_pressure"), body.get("running_pump_count"),
            body.get("linked_reservoir_name"), body.get("normal_operation_pump_count"),
            json.dumps(eq_meta, ensure_ascii=False),
        ))
        conn.commit()
        cur.close()
        write_audit(conn, actor=actor, action="create", target_type="booster",
                    target_key=sitename, summary=f"가압장 추가 {sitename}", request=request)
        return {"status": "OK", "sitename": sitename}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"가압장 추가 실패: {e}")
        return {"status": "ERROR", "message": _friendly_error(e)}
    finally:
        if conn:
            conn.close()


@router.put("/boosters/{sitename}")
async def update_booster(sitename: str, request: Request, actor: dict = Depends(get_actor)):
    """가압장 수정."""
    conn = None
    try:
        body = await request.json()
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM tb_service_booster_station_info WHERE sitename = %s", (sitename,))
        if not cur.fetchone():
            cur.close()
            return {"status": "ERROR", "message": f"'{sitename}' 가압장을 찾을 수 없습니다."}
        go = _build_booster_general_overview(body)
        cur.execute("""
            UPDATE tb_service_booster_station_info SET
                general_overview = %s, infiltration_well_count = %s,
                zone_1_area = %s, zone_1_height = %s,
                zone_2_area = %s, zone_2_height = %s,
                pump_type = %s, normal_operation_pump_count = %s
            WHERE sitename = %s
        """, (
            json.dumps(go, ensure_ascii=False), body.get("infiltration_well_count"),
            body.get("zone_1_area"), body.get("zone_1_height"),
            body.get("zone_2_area"), body.get("zone_2_height"),
            body.get("pump_type"), body.get("normal_operation_pump_count"),
            sitename,
        ))
        eq_meta = body.get("equipment_meta", [])
        cur.execute("""
            INSERT INTO tb_service_booster_station_status
                (sitename, pump_control_mode, pump_start_threshold, pump_stop_threshold,
                 pump_start_pressure, booster_inlet_pressure, booster_outlet_pressure,
                 booster_avg_pressure, running_pump_count, linked_reservoir_name,
                 normal_operation_pump_count, meta)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (sitename) DO UPDATE SET
                pump_control_mode = EXCLUDED.pump_control_mode,
                pump_start_threshold = EXCLUDED.pump_start_threshold,
                pump_stop_threshold = EXCLUDED.pump_stop_threshold,
                pump_start_pressure = EXCLUDED.pump_start_pressure,
                booster_inlet_pressure = EXCLUDED.booster_inlet_pressure,
                booster_outlet_pressure = EXCLUDED.booster_outlet_pressure,
                booster_avg_pressure = EXCLUDED.booster_avg_pressure,
                running_pump_count = EXCLUDED.running_pump_count,
                linked_reservoir_name = EXCLUDED.linked_reservoir_name,
                normal_operation_pump_count = EXCLUDED.normal_operation_pump_count,
                meta = EXCLUDED.meta
        """, (
            sitename, body.get("pump_control_mode"),
            body.get("pump_start_threshold"), body.get("pump_stop_threshold"),
            body.get("pump_start_pressure"),
            body.get("booster_inlet_pressure"), body.get("booster_outlet_pressure"),
            body.get("booster_avg_pressure"), body.get("running_pump_count"),
            body.get("linked_reservoir_name"), body.get("normal_operation_pump_count"),
            json.dumps(eq_meta, ensure_ascii=False),
        ))
        conn.commit()
        cur.close()
        write_audit(conn, actor=actor, action="update", target_type="booster",
                    target_key=sitename, summary=f"가압장 수정 {sitename}", request=request)
        return {"status": "OK", "updated": 1}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"가압장 수정 실패: {e}")
        return {"status": "ERROR", "message": _friendly_error(e)}
    finally:
        if conn:
            conn.close()


@router.delete("/boosters/{sitename}")
async def delete_booster(sitename: str, request: Request, dry_run: bool = Query(False), actor: dict = Depends(get_actor)):
    """가압장 삭제."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM tb_service_booster_station_status WHERE sitename = %s", (sitename,))
        status_cnt = cur.fetchone()[0]
        if dry_run:
            cur.close()
            return {"status": "OK", "dry_run": True, "related": {"status_rows": status_cnt}}
        cur.execute("DELETE FROM tb_service_booster_station_status WHERE sitename = %s", (sitename,))
        cur.execute("DELETE FROM tb_service_booster_station_info WHERE sitename = %s", (sitename,))
        conn.commit()
        deleted = cur.rowcount
        cur.close()
        if deleted:
            write_audit(conn, actor=actor, action="delete", target_type="booster",
                        target_key=sitename, summary=f"가압장 삭제 {sitename}", request=request)
        return {"status": "OK", "deleted": deleted}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"가압장 삭제 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


# =============================================================================
# 감압시설 관리 (Pressure Reducing Facility)
# =============================================================================


def _serialize_pressure_info(r: tuple) -> dict:
    """tb_pressure_reducing_facility_info row → flat dict."""
    go = r[1] if isinstance(r[1], dict) else (json.loads(r[1]) if r[1] else {})
    prv = go.get("pressure_reducing_valve", {}) if isinstance(go.get("pressure_reducing_valve"), dict) else {}
    return {
        "sitename": r[0],
        "install_location": go.get("install_location"),
        "operating_status": go.get("operating_status"),
        "prv_manufacturer": prv.get("manufacturer"),
        "prv_pipe_diameter": prv.get("pipe_diameter"),
        "prv_control_method": prv.get("control_method"),
        "pressure_unit": r[2],
        "pressure_reduction_pattern": r[3],
        "pressure_reduction_criteria": r[4],
    }


def _serialize_pressure_status(r: tuple) -> dict:
    """tb_pressure_reducing_facility_status row → dict."""
    meta_raw = r[7]
    if isinstance(meta_raw, str):
        meta_raw = json.loads(meta_raw)
    meta = meta_raw if isinstance(meta_raw, list) else []
    return {
        "sitename": r[0],
        "avg_inlet_pressure": float(r[1]) if r[1] is not None else None,
        "avg_outlet_pressure": float(r[2]) if r[2] is not None else None,
        "avg_pressure_reduction": float(r[3]) if r[3] is not None else None,
        "pressure_unit": r[4],
        "status": r[6],
        "equipment_meta": meta,
    }


_PRESSURE_GO_KEYS = ("install_location", "operating_status")


def _build_pressure_general_overview(body: dict) -> dict:
    """flat 필드 → general_overview JSONB 조립."""
    go: dict = {}
    for key in _PRESSURE_GO_KEYS:
        val = body.get(key)
        if val is not None:
            go[key] = val
    prv: dict = {}
    for pk in ("prv_manufacturer", "prv_pipe_diameter", "prv_control_method"):
        val = body.get(pk)
        if val is not None:
            prv[pk.replace("prv_", "")] = val
    if prv:
        go["pressure_reducing_valve"] = prv
    return go


_PRESSURE_INFO_COLS = (
    "sitename, general_overview, pressure_unit, "
    "pressure_reduction_pattern, pressure_reduction_criteria"
)

_PRESSURE_STATUS_COLS = (
    "sitename, avg_inlet_pressure, avg_outlet_pressure, "
    "avg_pressure_reduction, pressure_unit, updated_at, status, meta"
)


@router.get("/pressure-reducing")
async def get_pressure_facilities(
    keyword: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
):
    """감압시설 목록 조회."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        wheres, params = [], []
        if keyword:
            wheres.append("(sitename ILIKE %s)")
            params.append(f"%{keyword}%")
        where_sql = " AND ".join(wheres) if wheres else "TRUE"
        cur.execute(f"SELECT COUNT(*) FROM tb_pressure_reducing_facility_info WHERE {where_sql}", params)
        total = cur.fetchone()[0]
        offset = (page - 1) * page_size
        cur.execute(
            f"SELECT {_PRESSURE_INFO_COLS} FROM tb_pressure_reducing_facility_info "
            f"WHERE {where_sql} ORDER BY sitename LIMIT %s OFFSET %s",
            params + [page_size, offset],
        )
        data = [_serialize_pressure_info(r) for r in cur.fetchall()]
        cur.close()
        return {"status": "OK", "data": data, "total": total, "page": page, "page_size": page_size}
    except Exception as e:
        logger.error(f"감압시설 목록 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.get("/pressure-reducing/{sitename}")
async def get_pressure_detail(sitename: str):
    """감압시설 상세 조회."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute(f"SELECT {_PRESSURE_INFO_COLS} FROM tb_pressure_reducing_facility_info WHERE sitename = %s", (sitename,))
        info_row = cur.fetchone()
        if not info_row:
            cur.close()
            return {"status": "ERROR", "message": f"'{sitename}' 감압시설을 찾을 수 없습니다."}
        cur.execute(f"SELECT {_PRESSURE_STATUS_COLS} FROM tb_pressure_reducing_facility_status WHERE sitename = %s", (sitename,))
        status_row = cur.fetchone()
        cur.close()
        return {
            "status": "OK",
            "info": _serialize_pressure_info(info_row),
            "pressure_status": _serialize_pressure_status(status_row) if status_row else None,
        }
    except Exception as e:
        logger.error(f"감압시설 상세 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.post("/pressure-reducing")
async def create_pressure(request: Request, actor: dict = Depends(get_actor)):
    """감압시설 추가."""
    conn = None
    try:
        body = await request.json()
        sitename = body.get("sitename", "").strip()
        if not sitename:
            return {"status": "ERROR", "message": "현장명은 필수입니다."}
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM tb_pressure_reducing_facility_info WHERE sitename = %s", (sitename,))
        if cur.fetchone():
            cur.close()
            return {"status": "ERROR", "message": f"'{sitename}' 감압시설이 이미 존재합니다."}
        go = _build_pressure_general_overview(body)
        cur.execute("""
            INSERT INTO tb_pressure_reducing_facility_info
                (sitename, general_overview, pressure_unit,
                 pressure_reduction_pattern, pressure_reduction_criteria)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            sitename, json.dumps(go, ensure_ascii=False),
            body.get("pressure_unit", "kgf/㎠"),
            body.get("pressure_reduction_pattern"),
            body.get("pressure_reduction_criteria"),
        ))
        eq_meta = body.get("equipment_meta", [])
        cur.execute("""
            INSERT INTO tb_pressure_reducing_facility_status
                (sitename, avg_inlet_pressure, avg_outlet_pressure,
                 avg_pressure_reduction, pressure_unit, status, meta)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            sitename, body.get("avg_inlet_pressure"), body.get("avg_outlet_pressure"),
            body.get("avg_pressure_reduction"), body.get("pressure_unit", "kgf/㎠"),
            body.get("status"), json.dumps(eq_meta, ensure_ascii=False),
        ))
        conn.commit()
        cur.close()
        write_audit(conn, actor=actor, action="create", target_type="pressure_reducing",
                    target_key=sitename, summary=f"감압시설 추가 {sitename}", request=request)
        return {"status": "OK", "sitename": sitename}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"감압시설 추가 실패: {e}")
        return {"status": "ERROR", "message": _friendly_error(e)}
    finally:
        if conn:
            conn.close()


@router.put("/pressure-reducing/{sitename}")
async def update_pressure(sitename: str, request: Request, actor: dict = Depends(get_actor)):
    """감압시설 수정."""
    conn = None
    try:
        body = await request.json()
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM tb_pressure_reducing_facility_info WHERE sitename = %s", (sitename,))
        if not cur.fetchone():
            cur.close()
            return {"status": "ERROR", "message": f"'{sitename}' 감압시설을 찾을 수 없습니다."}
        go = _build_pressure_general_overview(body)
        cur.execute("""
            UPDATE tb_pressure_reducing_facility_info SET
                general_overview = %s, pressure_unit = %s,
                pressure_reduction_pattern = %s, pressure_reduction_criteria = %s
            WHERE sitename = %s
        """, (
            json.dumps(go, ensure_ascii=False), body.get("pressure_unit"),
            body.get("pressure_reduction_pattern"), body.get("pressure_reduction_criteria"),
            sitename,
        ))
        eq_meta = body.get("equipment_meta", [])
        cur.execute("""
            INSERT INTO tb_pressure_reducing_facility_status
                (sitename, avg_inlet_pressure, avg_outlet_pressure,
                 avg_pressure_reduction, pressure_unit, status, meta)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (sitename) DO UPDATE SET
                avg_inlet_pressure = EXCLUDED.avg_inlet_pressure,
                avg_outlet_pressure = EXCLUDED.avg_outlet_pressure,
                avg_pressure_reduction = EXCLUDED.avg_pressure_reduction,
                pressure_unit = EXCLUDED.pressure_unit,
                status = EXCLUDED.status,
                meta = EXCLUDED.meta
        """, (
            sitename, body.get("avg_inlet_pressure"), body.get("avg_outlet_pressure"),
            body.get("avg_pressure_reduction"), body.get("pressure_unit"),
            body.get("status"), json.dumps(eq_meta, ensure_ascii=False),
        ))
        conn.commit()
        cur.close()
        write_audit(conn, actor=actor, action="update", target_type="pressure_reducing",
                    target_key=sitename, summary=f"감압시설 수정 {sitename}", request=request)
        return {"status": "OK", "updated": 1}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"감압시설 수정 실패: {e}")
        return {"status": "ERROR", "message": _friendly_error(e)}
    finally:
        if conn:
            conn.close()


@router.delete("/pressure-reducing/{sitename}")
async def delete_pressure(sitename: str, request: Request, dry_run: bool = Query(False), actor: dict = Depends(get_actor)):
    """감압시설 삭제."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM tb_pressure_reducing_facility_status WHERE sitename = %s", (sitename,))
        status_cnt = cur.fetchone()[0]
        if dry_run:
            cur.close()
            return {"status": "OK", "dry_run": True, "related": {"status_rows": status_cnt}}
        cur.execute("DELETE FROM tb_pressure_reducing_facility_status WHERE sitename = %s", (sitename,))
        cur.execute("DELETE FROM tb_pressure_reducing_facility_info WHERE sitename = %s", (sitename,))
        conn.commit()
        deleted = cur.rowcount
        cur.close()
        if deleted:
            write_audit(conn, actor=actor, action="delete", target_type="pressure_reducing",
                        target_key=sitename, summary=f"감압시설 삭제 {sitename}", request=request)
        return {"status": "OK", "deleted": deleted}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"감압시설 삭제 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


# =============================================================================
# 블록 관리 (Block)
# =============================================================================


def _serialize_block_info(r: tuple) -> dict:
    """tb_block_info row → flat dict."""
    go = r[1] if isinstance(r[1], dict) else (json.loads(r[1]) if r[1] else {})
    pl = go.get("pipeline_length", {}) if isinstance(go.get("pipeline_length"), dict) else {}
    lcs = go.get("large_customer_status", {}) if isinstance(go.get("large_customer_status"), dict) else {}
    return {
        "sitename": r[0],
        "install_location": go.get("install_location"),
        "customer_count": go.get("customer_count"),
        "non_revenue_water_rate": go.get("non_revenue_water_rate"),
        "pipeline_total": pl.get("total"),
        "pipeline_old": pl.get("old"),
        "large_customer_count": lcs.get("count"),
        "large_customer_base_month_usage": lcs.get("base_month_usage"),
        "critical_pressure": float(r[2]) if r[2] is not None else None,
        "pressure_unit": r[3],
        "block_level": r[7],
    }


def _serialize_block_status(r: tuple) -> dict:
    """tb_block_status row → dict."""
    meta_raw = r[13]
    if isinstance(meta_raw, str):
        meta_raw = json.loads(meta_raw)
    meta = meta_raw if isinstance(meta_raw, list) else []
    return {
        "sitename": r[0],
        "flow_missing_rate": float(r[1]) if r[1] is not None else None,
        "flow_missing_rate_unit": r[2],
        "block_avg_daily_flow": float(r[3]) if r[3] is not None else None,
        "block_avg_daily_flow_ratio": float(r[4]) if r[4] is not None else None,
        "block_pressure": float(r[5]) if r[5] is not None else None,
        "block_inlet_pressure": float(r[6]) if r[6] is not None else None,
        "block_outlet_pressure": float(r[7]) if r[7] is not None else None,
        "inlet_pressure_variation_rate": float(r[8]) if r[8] is not None else None,
        "outlet_pressure_variation_rate": float(r[9]) if r[9] is not None else None,
        "flow_unit": r[10],
        "pressure_unit": r[11],
        "block_level": r[14],
        "equipment_meta": meta,
    }


def _build_block_general_overview(body: dict) -> dict:
    """flat 필드 → general_overview JSONB 조립."""
    go: dict = {}
    for key in ("install_location", "customer_count", "non_revenue_water_rate"):
        val = body.get(key)
        if val is not None:
            go[key] = val
    pl: dict = {}
    if body.get("pipeline_total") is not None:
        pl["total"] = body["pipeline_total"]
    if body.get("pipeline_old") is not None:
        pl["old"] = body["pipeline_old"]
    if pl:
        go["pipeline_length"] = pl
    lcs: dict = {}
    if body.get("large_customer_count") is not None:
        lcs["count"] = body["large_customer_count"]
    if body.get("large_customer_base_month_usage") is not None:
        lcs["base_month_usage"] = body["large_customer_base_month_usage"]
    if lcs:
        go["large_customer_status"] = lcs
    return go


_BLOCK_INFO_COLS = (
    "sitename, general_overview, critical_pressure, pressure_unit, "
    "site_photo_url, manual_url, system_diagram_url, block_level"
)

_BLOCK_STATUS_COLS = (
    "sitename, flow_missing_rate, flow_missing_rate_unit, "
    "block_avg_daily_flow, block_avg_daily_flow_ratio, block_pressure, "
    "block_inlet_pressure, block_outlet_pressure, "
    "inlet_pressure_variation_rate, outlet_pressure_variation_rate, "
    "flow_unit, pressure_unit, updated_at, meta, block_level"
)


@router.get("/blocks")
async def get_blocks(
    keyword: Optional[str] = Query(None),
    block_level: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
):
    """블록 목록 조회."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        wheres, params = [], []
        if keyword:
            wheres.append("(sitename ILIKE %s)")
            params.append(f"%{keyword}%")
        if block_level:
            wheres.append("(block_level = %s)")
            params.append(block_level)
        where_sql = " AND ".join(wheres) if wheres else "TRUE"
        cur.execute(f"SELECT COUNT(*) FROM tb_block_info WHERE {where_sql}", params)
        total = cur.fetchone()[0]
        offset = (page - 1) * page_size
        cur.execute(
            f"SELECT {_BLOCK_INFO_COLS} FROM tb_block_info "
            f"WHERE {where_sql} ORDER BY sitename LIMIT %s OFFSET %s",
            params + [page_size, offset],
        )
        data = [_serialize_block_info(r) for r in cur.fetchall()]
        cur.close()
        return {"status": "OK", "data": data, "total": total, "page": page, "page_size": page_size}
    except Exception as e:
        logger.error(f"블록 목록 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.get("/blocks/{sitename}")
async def get_block_detail(sitename: str):
    """블록 상세 조회."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute(f"SELECT {_BLOCK_INFO_COLS} FROM tb_block_info WHERE sitename = %s", (sitename,))
        info_row = cur.fetchone()
        if not info_row:
            cur.close()
            return {"status": "ERROR", "message": f"'{sitename}' 블록을 찾을 수 없습니다."}
        cur.execute(f"SELECT {_BLOCK_STATUS_COLS} FROM tb_block_status WHERE sitename = %s", (sitename,))
        status_row = cur.fetchone()
        cur.close()
        return {
            "status": "OK",
            "info": _serialize_block_info(info_row),
            "block_status": _serialize_block_status(status_row) if status_row else None,
        }
    except Exception as e:
        logger.error(f"블록 상세 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.post("/blocks")
async def create_block(request: Request, actor: dict = Depends(get_actor)):
    """블록 추가."""
    conn = None
    try:
        body = await request.json()
        sitename = body.get("sitename", "").strip()
        if not sitename:
            return {"status": "ERROR", "message": "블록명은 필수입니다."}
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM tb_block_info WHERE sitename = %s", (sitename,))
        if cur.fetchone():
            cur.close()
            return {"status": "ERROR", "message": f"'{sitename}' 블록이 이미 존재합니다."}
        go = _build_block_general_overview(body)
        block_level = body.get("block_level", "소블록")
        cur.execute("""
            INSERT INTO tb_block_info
                (sitename, general_overview, critical_pressure,
                 pressure_unit, block_level)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            sitename, json.dumps(go, ensure_ascii=False),
            body.get("critical_pressure"),
            body.get("pressure_unit", "kg/cm2"), block_level,
        ))
        eq_meta = body.get("equipment_meta", [])
        cur.execute("""
            INSERT INTO tb_block_status
                (sitename, flow_missing_rate, block_avg_daily_flow,
                 block_avg_daily_flow_ratio, block_pressure,
                 block_inlet_pressure, block_outlet_pressure,
                 inlet_pressure_variation_rate, outlet_pressure_variation_rate,
                 block_level, meta)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            sitename, body.get("flow_missing_rate"),
            body.get("block_avg_daily_flow"), body.get("block_avg_daily_flow_ratio"),
            body.get("block_pressure"),
            body.get("block_inlet_pressure"), body.get("block_outlet_pressure"),
            body.get("inlet_pressure_variation_rate"), body.get("outlet_pressure_variation_rate"),
            block_level, json.dumps(eq_meta, ensure_ascii=False),
        ))
        conn.commit()
        cur.close()
        write_audit(conn, actor=actor, action="create", target_type="block",
                    target_key=sitename, summary=f"블록 추가 {sitename}", request=request)
        return {"status": "OK", "sitename": sitename}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"블록 추가 실패: {e}")
        return {"status": "ERROR", "message": _friendly_error(e)}
    finally:
        if conn:
            conn.close()


@router.put("/blocks/{sitename}")
async def update_block(sitename: str, request: Request, actor: dict = Depends(get_actor)):
    """블록 수정."""
    conn = None
    try:
        body = await request.json()
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM tb_block_info WHERE sitename = %s", (sitename,))
        if not cur.fetchone():
            cur.close()
            return {"status": "ERROR", "message": f"'{sitename}' 블록을 찾을 수 없습니다."}
        go = _build_block_general_overview(body)
        block_level = body.get("block_level", "소블록")
        cur.execute("""
            UPDATE tb_block_info SET
                general_overview = %s, critical_pressure = %s,
                pressure_unit = %s, block_level = %s
            WHERE sitename = %s
        """, (
            json.dumps(go, ensure_ascii=False), body.get("critical_pressure"),
            body.get("pressure_unit"), block_level, sitename,
        ))
        eq_meta = body.get("equipment_meta", [])
        cur.execute("""
            INSERT INTO tb_block_status
                (sitename, flow_missing_rate, block_avg_daily_flow,
                 block_avg_daily_flow_ratio, block_pressure,
                 block_inlet_pressure, block_outlet_pressure,
                 inlet_pressure_variation_rate, outlet_pressure_variation_rate,
                 block_level, meta)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (sitename) DO UPDATE SET
                flow_missing_rate = EXCLUDED.flow_missing_rate,
                block_avg_daily_flow = EXCLUDED.block_avg_daily_flow,
                block_avg_daily_flow_ratio = EXCLUDED.block_avg_daily_flow_ratio,
                block_pressure = EXCLUDED.block_pressure,
                block_inlet_pressure = EXCLUDED.block_inlet_pressure,
                block_outlet_pressure = EXCLUDED.block_outlet_pressure,
                inlet_pressure_variation_rate = EXCLUDED.inlet_pressure_variation_rate,
                outlet_pressure_variation_rate = EXCLUDED.outlet_pressure_variation_rate,
                block_level = EXCLUDED.block_level,
                meta = EXCLUDED.meta
        """, (
            sitename, body.get("flow_missing_rate"),
            body.get("block_avg_daily_flow"), body.get("block_avg_daily_flow_ratio"),
            body.get("block_pressure"),
            body.get("block_inlet_pressure"), body.get("block_outlet_pressure"),
            body.get("inlet_pressure_variation_rate"), body.get("outlet_pressure_variation_rate"),
            block_level, json.dumps(eq_meta, ensure_ascii=False),
        ))
        conn.commit()
        cur.close()
        write_audit(conn, actor=actor, action="update", target_type="block",
                    target_key=sitename, summary=f"블록 수정 {sitename}", request=request)
        return {"status": "OK", "updated": 1}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"블록 수정 실패: {e}")
        return {"status": "ERROR", "message": _friendly_error(e)}
    finally:
        if conn:
            conn.close()


@router.delete("/blocks/{sitename}")
async def delete_block(sitename: str, request: Request, dry_run: bool = Query(False), actor: dict = Depends(get_actor)):
    """블록 삭제."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM tb_block_status WHERE sitename = %s", (sitename,))
        status_cnt = cur.fetchone()[0]
        if dry_run:
            cur.close()
            return {"status": "OK", "dry_run": True, "related": {"status_rows": status_cnt}}
        cur.execute("DELETE FROM tb_block_status WHERE sitename = %s", (sitename,))
        cur.execute("DELETE FROM tb_block_info WHERE sitename = %s", (sitename,))
        conn.commit()
        deleted = cur.rowcount
        cur.close()
        if deleted:
            write_audit(conn, actor=actor, action="delete", target_type="block",
                        target_key=sitename, summary=f"블록 삭제 {sitename}", request=request)
        return {"status": "OK", "deleted": deleted}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"블록 삭제 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()
