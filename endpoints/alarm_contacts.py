"""
경보 유형별 비상연락처 CRUD API
tb_alarm_contact 테이블 관리
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/crisis/alarm-contacts", tags=["alarm-contacts"])

# auth_crud와 동일한 패턴으로 DB 연결 함수 주입
_get_db_connection = None


def init(get_db_connection_fn):
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


def _get_conn():
    if _get_db_connection is None:
        raise RuntimeError("alarm_contacts not initialized")
    return _get_db_connection()


# ── 요청/응답 스키마 ────────────────────────────────────────────────────────

class AlarmContactCreate(BaseModel):
    category: str = Field(..., max_length=100, description="알람 카테고리 (UPS, 통신이상, 밸브 등)")
    company: str = Field(..., max_length=100, description="업체/기관명")
    phone: str = Field(..., max_length=50, description="전화번호")
    description: Optional[str] = Field(None, max_length=200, description="역할/설명")
    sort_order: int = Field(0, ge=0, description="카테고리 내 표시 순서")


class AlarmContactUpdate(BaseModel):
    category: Optional[str] = Field(None, max_length=100)
    company: Optional[str] = Field(None, max_length=100)
    phone: Optional[str] = Field(None, max_length=50)
    description: Optional[str] = Field(None, max_length=200)
    sort_order: Optional[int] = Field(None, ge=0)


# ── 헬퍼 ────────────────────────────────────────────────────────────────────

def _row_to_dict(row) -> dict:
    return {
        "contact_id": row[0],
        "region": row[1],
        "category": row[2],
        "company": row[3],
        "phone": row[4],
        "description": row[5],
        "sort_order": row[6],
        "created_at": row[7].isoformat() if row[7] else None,
        "updated_at": row[8].isoformat() if row[8] else None,
    }


# ── 엔드포인트 ───────────────────────────────────────────────────────────────

@router.get("/categories")
def list_categories(region: str = "R01"):
    """등록된 카테고리 목록 조회"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT category FROM tb_alarm_contact "
                "WHERE region = %s ORDER BY category",
                (region,),
            )
            return [r[0] for r in cur.fetchall()]
    except Exception as e:
        logger.error("list_categories error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@router.get("")
def list_contacts(region: str = "R01", category: Optional[str] = None):
    """연락처 목록 조회 (category 미지정 시 전체)"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            if category:
                cur.execute(
                    "SELECT contact_id,region,category,company,phone,description,sort_order,created_at,updated_at "
                    "FROM tb_alarm_contact WHERE region=%s AND category=%s "
                    "ORDER BY category, sort_order",
                    (region, category),
                )
            else:
                cur.execute(
                    "SELECT contact_id,region,category,company,phone,description,sort_order,created_at,updated_at "
                    "FROM tb_alarm_contact WHERE region=%s "
                    "ORDER BY category, sort_order",
                    (region,),
                )
            return [_row_to_dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error("list_contacts error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@router.post("", status_code=status.HTTP_201_CREATED)
def create_contact(body: AlarmContactCreate, region: str = "R01"):
    """연락처 추가"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tb_alarm_contact (region,category,company,phone,description,sort_order) "
                "VALUES (%s,%s,%s,%s,%s,%s) "
                "RETURNING contact_id,region,category,company,phone,description,sort_order,created_at,updated_at",
                (region, body.category, body.company, body.phone, body.description, body.sort_order),
            )
            row = cur.fetchone()
        conn.commit()
        return _row_to_dict(row)
    except Exception as e:
        conn.rollback()
        logger.error("create_contact error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@router.put("/{contact_id}")
def update_contact(contact_id: int, body: AlarmContactUpdate):
    """연락처 수정"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM tb_alarm_contact WHERE contact_id=%s", (contact_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="연락처를 찾을 수 없습니다")

            updates = {k: v for k, v in body.model_dump().items() if v is not None}
            if not updates:
                raise HTTPException(status_code=400, detail="수정할 항목이 없습니다")

            set_clauses = ", ".join(f"{k} = %s" for k in updates)
            values = list(updates.values()) + [contact_id]
            cur.execute(
                f"UPDATE tb_alarm_contact SET {set_clauses}, updated_at=now() "
                "WHERE contact_id=%s "
                "RETURNING contact_id,region,category,company,phone,description,sort_order,created_at,updated_at",
                values,
            )
            row = cur.fetchone()
        conn.commit()
        return _row_to_dict(row)
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        logger.error("update_contact error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@router.delete("/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_contact(contact_id: int):
    """연락처 삭제"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM tb_alarm_contact WHERE contact_id=%s RETURNING contact_id",
                (contact_id,),
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="연락처를 찾을 수 없습니다")
        conn.commit()
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        logger.error("delete_contact error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()
