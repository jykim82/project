"""
시설명 약칭 매핑 CRUD API
tb_facility_alias 관리 + 런타임 ParamExtractor 리로드

ParamExtractor가 프로세스 시작 시 한 번만 alias를 로드하므로, CRUD 후
매핑을 즉시 반영하려면 reload_fn을 통해 ai_server.py의 param_extractor
인스턴스 내부 상태를 갱신한다.
"""

import logging
from typing import Callable, Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/facility-alias", tags=["facility-alias"])

_get_db_connection: Optional[Callable] = None
_reload_aliases_fn: Optional[Callable] = None


def init(get_db_connection_fn, reload_aliases_fn):
    """
    reload_aliases_fn: 인자 없이 호출되며 DB에서 alias를 다시 읽어
    param_extractor_instance에 주입한다.
    """
    global _get_db_connection, _reload_aliases_fn
    _get_db_connection = get_db_connection_fn
    _reload_aliases_fn = reload_aliases_fn


def _get_conn():
    if _get_db_connection is None:
        raise RuntimeError("facility_alias not initialized")
    return _get_db_connection()


class AliasCreate(BaseModel):
    region: str = Field(..., max_length=10)
    alias: str = Field(..., min_length=1, max_length=100)
    sitename: str = Field(..., min_length=1, max_length=50)
    priority: int = Field(0, ge=0, le=1000)
    note: Optional[str] = Field(None, max_length=500)


class AliasUpdate(BaseModel):
    alias: Optional[str] = Field(None, min_length=1, max_length=100)
    sitename: Optional[str] = Field(None, min_length=1, max_length=50)
    priority: Optional[int] = Field(None, ge=0, le=1000)
    note: Optional[str] = Field(None, max_length=500)
    use_yn: Optional[str] = Field(None, pattern="^[YN]$")


_SELECT_COLUMNS = (
    "alias_id, region, alias, sitename, priority, note, use_yn, "
    "created_at, updated_at"
)


def _row_to_dict(row) -> dict:
    return {
        "alias_id": row[0],
        "region": row[1],
        "alias": row[2],
        "sitename": row[3],
        "priority": row[4],
        "note": row[5],
        "use_yn": row[6],
        "created_at": row[7].isoformat() if row[7] else None,
        "updated_at": row[8].isoformat() if row[8] else None,
    }


def _reload():
    if _reload_aliases_fn:
        try:
            _reload_aliases_fn()
        except Exception as e:
            logger.warning(f"alias 리로드 실패: {e}")


@router.get("")
def list_aliases(region: str = Query(...), include_disabled: bool = False):
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            if include_disabled:
                cur.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM tb_facility_alias "
                    "WHERE region = %s "
                    "ORDER BY sitename, priority DESC, alias",
                    (region,),
                )
            else:
                cur.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM tb_facility_alias "
                    "WHERE region = %s AND use_yn = 'Y' "
                    "ORDER BY sitename, priority DESC, alias",
                    (region,),
                )
            return [_row_to_dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error("list_aliases error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("", status_code=status.HTTP_201_CREATED)
def create_alias(body: AliasCreate):
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tb_facility_alias "
                "(region, alias, sitename, priority, note) "
                "VALUES (%s, %s, %s, %s, %s) "
                f"RETURNING {_SELECT_COLUMNS}",
                (body.region, body.alias, body.sitename, body.priority, body.note),
            )
            row = cur.fetchone()
        conn.commit()
        _reload()
        return _row_to_dict(row)
    except Exception as e:
        conn.rollback()
        logger.error("create_alias error: %s", e)
        if "uq_facility_alias_region_alias" in str(e):
            raise HTTPException(status_code=409, detail="이미 등록된 alias입니다")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{alias_id}")
def update_alias(alias_id: int, body: AliasUpdate):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="수정할 항목이 없습니다")

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM tb_facility_alias WHERE alias_id = %s", (alias_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="alias not found")

            set_clauses = ", ".join(f"{k} = %s" for k in updates)
            values = list(updates.values()) + [alias_id]
            cur.execute(
                f"UPDATE tb_facility_alias SET {set_clauses}, updated_at = now() "
                "WHERE alias_id = %s "
                f"RETURNING {_SELECT_COLUMNS}",
                values,
            )
            row = cur.fetchone()
        conn.commit()
        _reload()
        return _row_to_dict(row)
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        logger.error("update_alias error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{alias_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_alias(alias_id: int):
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM tb_facility_alias WHERE alias_id = %s RETURNING alias_id",
                (alias_id,),
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="alias not found")
        conn.commit()
        _reload()
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        logger.error("delete_alias error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
