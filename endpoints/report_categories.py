"""
endpoints/report_categories.py — 보고서 카테고리 (장애 시스템 / 장애 장비) CRUD

사양: docs/report-spec.md §3.5 (관리 메뉴에서 옵션 CRUD)
관련: tb_report_category (Migration 0060)

엔드포인트:
  GET    /report-categories?category_type=system    — 목록
  POST   /report-categories                         — 추가
  PATCH  /report-categories/{category_id}           — 수정 (label/sort_order/use_yn)
  DELETE /report-categories/{category_id}           — 영구 삭제
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/report-categories", tags=["report-categories"])

_get_db_connection = None


def init(get_db_connection_fn):
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


def _get_conn():
    if _get_db_connection is None:
        raise HTTPException(status_code=500, detail="DB 커넥션 미초기화")
    return _get_db_connection()


def _row_to_dict(cur, row) -> dict:
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


# ============================================================================
# Pydantic
# ============================================================================

class CreateCategoryRequest(BaseModel):
    category_type: str  # 'system' or 'equipment'
    code: str = Field(..., min_length=1, max_length=50)
    label: Optional[str] = None
    sort_order: int = 100


class PatchCategoryRequest(BaseModel):
    label: Optional[str] = None
    sort_order: Optional[int] = None
    use_yn: Optional[str] = None  # 'Y' or 'N'


# ============================================================================
# 조회
# ============================================================================

@router.get("")
def list_categories(
    category_type: Optional[str] = Query(None, description="'system' / 'equipment' / None=전체"),
    include_disabled: bool = Query(False, description="use_yn='N' 항목 포함 여부"),
):
    if category_type and category_type not in (
        "system", "equipment", "inspection_system", "inspection_equipment"
    ):
        raise HTTPException(status_code=400, detail="category_type 은 system/equipment/inspection_system/inspection_equipment")
    conn = _get_conn()
    try:
        cur = conn.cursor()
        sql = ["SELECT category_id, category_type, code, label, sort_order, use_yn FROM tb_report_category WHERE 1=1"]
        params: list = []
        if category_type:
            sql.append("AND category_type = %s")
            params.append(category_type)
        if not include_disabled:
            sql.append("AND use_yn = 'Y'")
        sql.append("ORDER BY category_type, sort_order, category_id")
        cur.execute(" ".join(sql), tuple(params))
        rows = [_row_to_dict(cur, r) for r in cur.fetchall()]
        return {"categories": rows, "total": len(rows)}
    finally:
        conn.close()


# ============================================================================
# 생성 / 수정 / 삭제
# ============================================================================

@router.post("")
def create_category(req: CreateCategoryRequest):
    if req.category_type not in (
        "system", "equipment", "inspection_system", "inspection_equipment"
    ):
        raise HTTPException(status_code=400, detail="category_type 은 system/equipment/inspection_system/inspection_equipment")
    code = req.code.strip()
    if not code:
        raise HTTPException(status_code=400, detail="code 는 비워둘 수 없음")
    label = (req.label or code).strip()
    conn = _get_conn()
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO tb_report_category (category_type, code, label, sort_order, use_yn)
                VALUES (%s, %s, %s, %s, 'Y')
                RETURNING category_id, category_type, code, label, sort_order, use_yn
                """,
                (req.category_type, code, label, req.sort_order),
            )
        except Exception as e:
            conn.rollback()
            if "uq_report_category" in str(e) or "duplicate key" in str(e).lower():
                raise HTTPException(status_code=409, detail=f"이미 등록된 항목 — {req.category_type} / {code}")
            raise
        row = _row_to_dict(cur, cur.fetchone())
        conn.commit()
        return row
    finally:
        conn.close()


@router.patch("/{category_id}")
def patch_category(category_id: int, req: PatchCategoryRequest):
    sets, params = [], []
    if req.label is not None:
        sets.append("label = %s")
        params.append(req.label.strip())
    if req.sort_order is not None:
        sets.append("sort_order = %s")
        params.append(req.sort_order)
    if req.use_yn is not None:
        if req.use_yn not in ("Y", "N"):
            raise HTTPException(status_code=400, detail="use_yn 은 'Y' / 'N'")
        sets.append("use_yn = %s")
        params.append(req.use_yn)
    if not sets:
        raise HTTPException(status_code=400, detail="변경할 필드가 없음")
    sets.append("updated_at = now()")
    params.append(category_id)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            UPDATE tb_report_category SET {', '.join(sets)}
             WHERE category_id = %s
             RETURNING category_id, category_type, code, label, sort_order, use_yn
            """,
            tuple(params),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="카테고리를 찾을 수 없음")
        out = _row_to_dict(cur, row)
        conn.commit()
        return out
    finally:
        conn.close()


@router.delete("/{category_id}")
def delete_category(category_id: int):
    """완전 삭제. 사용 안 함만 원하면 PATCH use_yn='N' 권장."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM tb_report_category WHERE category_id = %s RETURNING category_id",
            (category_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="카테고리를 찾을 수 없음")
        conn.commit()
        return {"deleted": True, "category_id": category_id}
    finally:
        conn.close()
