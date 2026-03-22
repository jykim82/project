"""
설비 CRUD 엔드포인트 모듈

설비(Equipment), 배수지(Reservoir), 가압장(Booster), 감압시설(Pressure Reducing),
블록(Block) 관련 CRUD API를 제공한다.

ai_server.py에서 분리된 모듈 — init()으로 DB 커넥션 함수를 주입받아 사용.
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

logger = logging.getLogger("slm")

router = APIRouter()

# ai_server.py에서 주입
_get_db_connection = None


def init(get_db_connection_fn):
    """ai_server.py에서 DB 커넥션 팩토리 함수를 주입받는다."""
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


# =============================================================================
# 설비 관리 API (tb_equipment_info CRUD)
# =============================================================================

class EquipmentCreateRequest(BaseModel):
    prefix: str                           # equipment_id 접두사 (예: "booster_pump")
    sitename: str
    facilitytype: str
    equipmenttype: str
    status: str = "operational"
    commissioned_at: Optional[str] = None  # "YYYY-MM-DD" or None
    decommissioned_at: Optional[str] = None
    description: Optional[str] = None
    meta: Optional[dict] = None


class EquipmentUpdateRequest(BaseModel):
    sitename: Optional[str] = None
    facilitytype: Optional[str] = None
    equipmenttype: Optional[str] = None
    status: Optional[str] = None
    commissioned_at: Optional[str] = None
    decommissioned_at: Optional[str] = None
    description: Optional[str] = None
    meta: Optional[dict] = None


def _next_equipment_number(cur, prefix: str) -> int:
    """주어진 접두사의 다음 순번 계산."""
    cur.execute("""
        SELECT COALESCE(MAX(
            CAST(SUBSTRING(equipment_id FROM LENGTH(%s) + 2) AS INTEGER)
        ), 0) + 1
        FROM tb_equipment_info
        WHERE LEFT(equipment_id, LENGTH(%s) + 1) = %s || '_'
          AND SUBSTRING(equipment_id FROM LENGTH(%s) + 2) ~ '^\\d+$'
    """, (prefix, prefix, prefix, prefix))
    return cur.fetchone()[0]


def _serialize_equipment_row(r) -> dict:
    """설비 행을 JSON 직렬화."""
    return {
        "equipment_id": r[0],
        "sitename": r[1],
        "facilitytype": r[2],
        "equipmenttype": r[3],
        "status": r[4],
        "commissioned_at": r[5].isoformat() if r[5] else None,
        "decommissioned_at": r[6].isoformat() if r[6] else None,
        "description": r[7],
        "meta": r[8] if isinstance(r[8], dict) else (json.loads(r[8]) if r[8] else {}),
        "created_at": r[9].isoformat() if r[9] else None,
        "updated_at": r[10].isoformat() if r[10] else None,
    }


@router.get("/equipments")
async def get_equipments(
    sitename: Optional[str] = Query(None),
    facilitytype: Optional[str] = Query(None),
    equipmenttype: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
):
    """설비 목록 조회 (페이징+필터)."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()

        wheres, params = [], []
        if sitename:
            wheres.append("sitename = %s")
            params.append(sitename)
        if facilitytype:
            wheres.append("facilitytype = %s")
            params.append(facilitytype)
        if equipmenttype:
            wheres.append("equipmenttype = %s")
            params.append(equipmenttype)
        if keyword:
            wheres.append("(equipment_id ILIKE %s OR description ILIKE %s OR meta::text ILIKE %s)")
            kw = f"%{keyword}%"
            params.extend([kw, kw, kw])

        where_sql = " AND ".join(wheres) if wheres else "TRUE"

        # 총 건수
        cur.execute(f"SELECT COUNT(*) FROM tb_equipment_info WHERE {where_sql}", params)
        total = cur.fetchone()[0]

        # 데이터 조회
        offset = (page - 1) * page_size
        cur.execute(f"""
            SELECT equipment_id, sitename, facilitytype, equipmenttype, status,
                   commissioned_at, decommissioned_at, description, meta,
                   created_at, updated_at
            FROM tb_equipment_info
            WHERE {where_sql}
            ORDER BY sitename, facilitytype, equipment_id
            LIMIT %s OFFSET %s
        """, params + [page_size, offset])
        rows = cur.fetchall()
        cur.close()

        data = [_serialize_equipment_row(r) for r in rows]
        return {"status": "OK", "data": data, "total": total, "page": page, "page_size": page_size}
    except Exception as e:
        logger.error(f"설비 목록 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.get("/equipments/filters")
async def get_equipment_filters():
    """설비 필터 옵션 조회 (distinct 값)."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        result = {}
        for col in ["sitename", "facilitytype", "equipmenttype"]:
            cur.execute(f"SELECT DISTINCT {col} FROM tb_equipment_info WHERE {col} IS NOT NULL ORDER BY {col}")
            result[col] = [r[0] for r in cur.fetchall()]

        # status 참조 테이블
        cur.execute("SELECT code, display_name FROM tb_equipment_status ORDER BY code")
        result["status"] = [{"value": r[0], "label": r[1]} for r in cur.fetchall()]
        cur.close()
        return {"status": "OK", "data": result}
    except Exception as e:
        logger.error(f"설비 필터 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.get("/equipments/next-id")
async def get_next_equipment_id(prefix: str = Query(..., min_length=1)):
    """다음 설비 ID 조회 (접두사 기준 다음 순번)."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        next_num = _next_equipment_number(cur, prefix.strip())
        cur.close()
        next_id = f"{prefix.strip()}_{next_num}"
        return {"status": "OK", "next_id": next_id, "next_number": next_num}
    except Exception as e:
        logger.error(f"다음 설비 ID 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.post("/equipments")
async def create_equipment(req: EquipmentCreateRequest):
    """설비 추가 (equipment_id 자동 생성)."""
    conn = None
    try:
        prefix = req.prefix.strip()
        if not prefix:
            return {"status": "ERROR", "message": "접두사(prefix)가 비어 있습니다."}

        conn = _get_db_connection()
        cur = conn.cursor()

        next_num = _next_equipment_number(cur, prefix)
        equipment_id = f"{prefix}_{next_num}"

        meta_json = json.dumps(req.meta or {}, ensure_ascii=False)

        cur.execute("""
            INSERT INTO tb_equipment_info
                (equipment_id, sitename, facilitytype, equipmenttype, status,
                 commissioned_at, decommissioned_at, description, meta)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            equipment_id, req.sitename.strip(), req.facilitytype,
            req.equipmenttype, req.status,
            req.commissioned_at or None, req.decommissioned_at or None,
            req.description or None, meta_json,
        ))
        conn.commit()
        cur.close()
        return {"status": "OK", "equipment_id": equipment_id}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"설비 추가 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.put("/equipments/{equipment_id}")
async def update_equipment(equipment_id: str, req: EquipmentUpdateRequest):
    """설비 수정 (equipment_id는 불변)."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()

        set_parts, params = [], []
        if req.sitename is not None:
            set_parts.append("sitename = %s")
            params.append(req.sitename.strip())
        if req.facilitytype is not None:
            set_parts.append("facilitytype = %s")
            params.append(req.facilitytype)
        if req.equipmenttype is not None:
            set_parts.append("equipmenttype = %s")
            params.append(req.equipmenttype)
        if req.status is not None:
            set_parts.append("status = %s")
            params.append(req.status)
        if req.commissioned_at is not None:
            set_parts.append("commissioned_at = %s")
            params.append(req.commissioned_at if req.commissioned_at else None)
        if req.decommissioned_at is not None:
            set_parts.append("decommissioned_at = %s")
            params.append(req.decommissioned_at if req.decommissioned_at else None)
        if req.description is not None:
            set_parts.append("description = %s")
            params.append(req.description if req.description else None)
        if req.meta is not None:
            set_parts.append("meta = %s")
            params.append(json.dumps(req.meta, ensure_ascii=False))

        if not set_parts:
            return {"status": "OK", "updated": 0, "message": "변경할 항목이 없습니다."}

        params.append(equipment_id)
        cur.execute(
            f"UPDATE tb_equipment_info SET {', '.join(set_parts)} WHERE equipment_id = %s",
            params,
        )
        conn.commit()
        updated = cur.rowcount
        cur.close()
        return {"status": "OK", "updated": updated}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"설비 수정 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.delete("/equipments/{equipment_id}")
async def delete_equipment(equipment_id: str, dry_run: bool = Query(False)):
    """설비 삭제 (dry_run=true → cascade 영향만 확인)."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()

        # cascade 영향 확인
        cur.execute("SELECT COUNT(*) FROM tb_network_info WHERE equipment_id = %s", (equipment_id,))
        net_info_cnt = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM tb_network_link WHERE source_equipment_id = %s OR target_equipment_id = %s",
            (equipment_id, equipment_id),
        )
        net_link_cnt = cur.fetchone()[0]

        cascade = {"network_info": net_info_cnt, "network_link": net_link_cnt}

        if dry_run:
            cur.close()
            return {"status": "OK", "dry_run": True, "cascade": cascade}

        cur.execute("DELETE FROM tb_equipment_info WHERE equipment_id = %s", (equipment_id,))
        conn.commit()
        deleted = cur.rowcount
        cur.close()
        return {"status": "OK", "deleted": deleted, "cascade": cascade}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"설비 삭제 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


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
async def create_reservoir(request: Request):
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
        return {"status": "OK", "sitename": sitename}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"배수지 추가 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.put("/reservoirs/{sitename}")
async def update_reservoir(sitename: str, request: Request):
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
        return {"status": "OK", "updated": 1}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"배수지 수정 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.delete("/reservoirs/{sitename}")
async def delete_reservoir(sitename: str, dry_run: bool = Query(False)):
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
async def create_booster(request: Request):
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
        return {"status": "OK", "sitename": sitename}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"가압장 추가 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.put("/boosters/{sitename}")
async def update_booster(sitename: str, request: Request):
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
        return {"status": "OK", "updated": 1}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"가압장 수정 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.delete("/boosters/{sitename}")
async def delete_booster(sitename: str, dry_run: bool = Query(False)):
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
async def create_pressure(request: Request):
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
        return {"status": "OK", "sitename": sitename}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"감압시설 추가 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.put("/pressure-reducing/{sitename}")
async def update_pressure(sitename: str, request: Request):
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
        return {"status": "OK", "updated": 1}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"감압시설 수정 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.delete("/pressure-reducing/{sitename}")
async def delete_pressure(sitename: str, dry_run: bool = Query(False)):
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
async def create_block(request: Request):
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
        return {"status": "OK", "sitename": sitename}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"블록 추가 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.put("/blocks/{sitename}")
async def update_block(sitename: str, request: Request):
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
        return {"status": "OK", "updated": 1}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"블록 수정 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.delete("/blocks/{sitename}")
async def delete_block(sitename: str, dry_run: bool = Query(False)):
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
        return {"status": "OK", "deleted": deleted}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"블록 삭제 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()
