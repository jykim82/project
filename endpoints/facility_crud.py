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
    # [E-025 P7] 비전 등록용 옵셔널 필드
    equipment_photo_url: Optional[str] = None
    nameplate_photo_url: Optional[str] = None
    vision_session_id: Optional[int] = None


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
                 commissioned_at, decommissioned_at, description, meta,
                 equipment_photo_url, nameplate_photo_url)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            equipment_id, req.sitename.strip(), req.facilitytype,
            req.equipmenttype, req.status,
            req.commissioned_at or None, req.decommissioned_at or None,
            req.description or None, meta_json,
            req.equipment_photo_url, req.nameplate_photo_url,
        ))
        # [E-025 P7] vision_session 연결 + 사진이 있으면 tb_equipment_image 기록
        if req.vision_session_id:
            try:
                cur.execute(
                    "UPDATE tb_vision_session SET linked_equipment_id = %s WHERE vision_session_id = %s",
                    (equipment_id, req.vision_session_id),
                )
            except Exception as e:
                logger.warning(f"tb_vision_session.linked_equipment_id 업데이트 실패: {e}")
        for url, kind in (
            (req.nameplate_photo_url, "nameplate"),
            (req.equipment_photo_url, "exterior"),
        ):
            if not url:
                continue
            try:
                cur.execute("""
                    INSERT INTO tb_equipment_image
                        (equipment_id, sitename, facilitytype, image_url, image_kind)
                    VALUES (%s, %s, %s, %s, %s)
                """, (equipment_id, req.sitename.strip(), req.facilitytype, url, kind))
            except Exception as e:
                logger.warning(f"tb_equipment_image INSERT 실패 ({kind}): {e}")
        conn.commit()
        cur.close()
        return {
            "status": "OK",
            "equipment_id": equipment_id,
            "vision_session_id": req.vision_session_id,
        }
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




# 배수지/가압장/감압/블록 CRUD → facility_types_crud.py로 분리됨
