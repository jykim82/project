"""
업무 메모 CRUD API — tb_memo (docs/memo-schedule-spec.md)

검색: 작성일 기간 + 제목/내용/작성자 부분일치. 전체 열람, 수정·삭제는 작성자만.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger("slm")

router = APIRouter(prefix="/memo", tags=["memo"])

_get_db_connection = None


def init(get_db_connection_fn):
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


def _get_conn():
    if _get_db_connection is None:
        raise RuntimeError("memo not initialized")
    return _get_db_connection()


class MemoCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field("", max_length=20000)
    created_by: str = Field(..., max_length=50)


class MemoUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    content: Optional[str] = Field(None, max_length=20000)
    user_id: str = Field(..., max_length=50, description="요청자 — 작성자 본인 검증")


def _row_to_dict(row) -> dict:
    return {
        "memo_idn": row[0],
        "title": row[1],
        "content": row[2],
        "created_by": row[3],
        "created_by_name": row[4],
        "created_at": row[5].isoformat() if row[5] else None,
        "updated_at": row[6].isoformat() if row[6] else None,
    }


@router.get("/list")
def list_memos(
    region: str = "R01",
    date_from: Optional[str] = Query(None, description="작성일 시작 YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="작성일 끝 YYYY-MM-DD"),
    title: Optional[str] = Query(None, description="제목 부분일치"),
    content: Optional[str] = Query(None, description="내용 부분일치"),
    created_by: Optional[str] = Query(None, description="작성자 id/이름 부분일치"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    conn = _get_conn()
    where = ["m.region = %s", "m.use_yn = 'Y'"]
    params: list = [region]
    if date_from:
        where.append("m.created_at >= %s::date")
        params.append(date_from)
    if date_to:
        # 끝 날짜 당일 포함 (exclusive 상한)
        where.append("m.created_at < (%s::date + interval '1 day')")
        params.append(date_to)
    if title:
        where.append("m.title ILIKE %s")
        params.append(f"%{title}%")
    if content:
        where.append("m.content ILIKE %s")
        params.append(f"%{content}%")
    if created_by:
        where.append("(m.created_by ILIKE %s OR u.user_nm ILIKE %s)")
        params.extend([f"%{created_by}%", f"%{created_by}%"])
    where_sql = " AND ".join(where)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT count(*) FROM tb_memo m "
                f"LEFT JOIN tb_user u ON u.region = m.region AND u.user_id = m.created_by "
                f"WHERE {where_sql}",
                params,
            )
            total = cur.fetchone()[0]
            cur.execute(
                f"SELECT m.memo_idn, m.title, m.content, m.created_by, "
                f"       COALESCE(u.user_nm, m.created_by), m.created_at, m.updated_at "
                f"FROM tb_memo m "
                f"LEFT JOIN tb_user u ON u.region = m.region AND u.user_id = m.created_by "
                f"WHERE {where_sql} "
                f"ORDER BY m.created_at DESC LIMIT %s OFFSET %s",
                params + [page_size, (page - 1) * page_size],
            )
            return {
                "status": "OK",
                "total": total,
                "page": page,
                "page_size": page_size,
                "data": [_row_to_dict(r) for r in cur.fetchall()],
            }
    except Exception as e:
        logger.error("memo list error: %s", e)
        raise HTTPException(status_code=500, detail="메모 조회 실패")
    finally:
        conn.close()


@router.post("")
def create_memo(body: MemoCreate, region: str = "R01"):
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tb_memo (region, title, content, created_by) "
                "VALUES (%s, %s, %s, %s) RETURNING memo_idn",
                (region, body.title, body.content, body.created_by),
            )
            memo_idn = cur.fetchone()[0]
        conn.commit()
        return {"status": "OK", "memo_idn": memo_idn}
    except Exception as e:
        conn.rollback()
        logger.error("memo create error: %s", e)
        raise HTTPException(status_code=500, detail="메모 저장 실패")
    finally:
        conn.close()


def _is_master(cur, region: str, user_id: str) -> bool:
    """마스터 권한 서버측 확인 — 클라이언트 플래그를 신뢰하지 않는다."""
    cur.execute(
        "SELECT user_auth FROM tb_user WHERE region=%s AND user_id=%s AND use_yn='Y'",
        (region, user_id),
    )
    row = cur.fetchone()
    return bool(row and row[0] == "MASTER")


def _check_owner(cur, region: str, memo_idn: int, user_id: str, allow_master: bool = False):
    cur.execute(
        "SELECT created_by FROM tb_memo WHERE region=%s AND memo_idn=%s AND use_yn='Y'",
        (region, memo_idn),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="메모가 없습니다")
    if row[0] == user_id:
        return
    # 마스터는 타인 메모 삭제 허용 (수정은 작성자 본인만 — 내용 위변조 방지)
    if allow_master and _is_master(cur, region, user_id):
        return
    raise HTTPException(status_code=403, detail="작성자만 수정/삭제할 수 있습니다")


@router.put("/{memo_idn}")
def update_memo(memo_idn: int, body: MemoUpdate, region: str = "R01"):
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            _check_owner(cur, region, memo_idn, body.user_id)
            sets, params = ["updated_at = now()"], []
            if body.title is not None:
                sets.append("title = %s")
                params.append(body.title)
            if body.content is not None:
                sets.append("content = %s")
                params.append(body.content)
            cur.execute(
                f"UPDATE tb_memo SET {', '.join(sets)} WHERE region=%s AND memo_idn=%s",
                params + [region, memo_idn],
            )
        conn.commit()
        return {"status": "OK"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.error("memo update error: %s", e)
        raise HTTPException(status_code=500, detail="메모 수정 실패")
    finally:
        conn.close()


@router.delete("/{memo_idn}")
def delete_memo(memo_idn: int, user_id: str, region: str = "R01"):
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            _check_owner(cur, region, memo_idn, user_id, allow_master=True)
            cur.execute(
                "UPDATE tb_memo SET use_yn='N', updated_at=now() "
                "WHERE region=%s AND memo_idn=%s",
                (region, memo_idn),
            )
        conn.commit()
        return {"status": "OK"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.error("memo delete error: %s", e)
        raise HTTPException(status_code=500, detail="메모 삭제 실패")
    finally:
        conn.close()
