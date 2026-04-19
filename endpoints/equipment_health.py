"""
설비 건강성 통계 API — migration 0045 뷰 활용

엔드포인트:
- GET /monitoring/equipment-health/summary: KPI 요약
- GET /monitoring/equipment-health/monthly: 월별 추이
- GET /monitoring/equipment-health/stats: 시설·설비·분류별 통계
- GET /monitoring/equipment-health/mtbf: 설비별 MTBF (집중관리 대상)
- GET /monitoring/equipment-health/ranking: 시설별 장애 Top N
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/monitoring/equipment-health", tags=["equipment-health"])

_get_db_connection = None


def init(get_db_connection_fn):
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


def _rows_to_dicts(cur):
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _build_filter_clause(
    sitename: Optional[str],
    facilitytype: Optional[str],
    equipmenttype: Optional[str],
    equipment_id: Optional[str],
    keyword: Optional[str],
) -> tuple[str, list]:
    """장애 이력 공통 필터 — WHERE 절 추가 조건 생성."""
    conds, params = [], []
    if sitename:
        conds.append("sitename ILIKE %s")
        params.append(f"%{sitename}%")
    if facilitytype:
        conds.append("facilitytype = %s")
        params.append(facilitytype)
    if equipmenttype:
        conds.append("equipmenttype = %s")
        params.append(equipmenttype)
    if equipment_id:
        conds.append("equipment_id = %s")
        params.append(equipment_id)
    if keyword:
        conds.append("(task_content ILIKE %s OR sitename ILIKE %s OR equipmenttype ILIKE %s)")
        kw = f"%{keyword}%"
        params.extend([kw, kw, kw])
    clause = (" AND " + " AND ".join(conds)) if conds else ""
    return clause, params


@router.get("/summary")
def summary(
    months: int = Query(12, ge=1, le=60, description="최근 N개월 범위"),
    sitename: Optional[str] = Query(None),
    facilitytype: Optional[str] = Query(None),
    equipmenttype: Optional[str] = Query(None),
    equipment_id: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
):
    """KPI 요약: 총 건수 / 진행중 / 완료 / 평균 조치시간 / 영향받은 설비 수 (필터 지원)"""
    if _get_db_connection is None:
        raise HTTPException(500, "DB 미초기화")
    conn = _get_db_connection()
    try:
        cur = conn.cursor()
        f_clause, f_params = _build_filter_clause(sitename, facilitytype, equipmenttype, equipment_id, keyword)
        cur.execute(
            f"""
            SELECT
              COUNT(*) AS total_cnt,
              COUNT(*) FILTER (WHERE status = '진행중') AS ongoing_cnt,
              COUNT(*) FILTER (WHERE status = '완료')   AS resolved_cnt,
              ROUND(
                (AVG(EXTRACT(EPOCH FROM (resolved_at - task_start_time)) / 3600)
                  FILTER (WHERE resolved_at IS NOT NULL))::numeric, 2
              ) AS avg_resolve_hours,
              COUNT(DISTINCT equipment_id) FILTER (WHERE equipment_id IS NOT NULL) AS affected_equipments,
              COUNT(*) FILTER (WHERE fault_category = '고장') AS cat_fault,
              COUNT(*) FILTER (WHERE fault_category = '이상') AS cat_abnormal,
              COUNT(*) FILTER (WHERE fault_category = '교체') AS cat_replace,
              COUNT(*) FILTER (WHERE fault_category = '점검') AS cat_inspect
            FROM tb_task_master
            WHERE task_category = '고장보고'
              AND task_start_time >= now() - interval '{months} months'
              {f_clause}
            """,
            f_params,
        )
        row = cur.fetchone()
        cols = [d[0] for d in cur.description]
        data = dict(zip(cols, row))
        cur.close()
        return {"status": "OK", "months": months, "data": data}
    finally:
        conn.close()


@router.get("/monthly")
def monthly(
    months: int = Query(12, ge=1, le=60),
    sitename: Optional[str] = Query(None),
    facilitytype: Optional[str] = Query(None),
    equipmenttype: Optional[str] = Query(None),
    equipment_id: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
):
    """월별 장애 추이 (tb_task_master 직접 집계 + 필터)"""
    if _get_db_connection is None:
        raise HTTPException(500, "DB 미초기화")
    conn = _get_db_connection()
    try:
        cur = conn.cursor()
        f_clause, f_params = _build_filter_clause(sitename, facilitytype, equipmenttype, equipment_id, keyword)
        cur.execute(
            f"""
            SELECT
              TO_CHAR(date_trunc('month', task_start_time), 'YYYY-MM') AS month,
              fault_category,
              equipmenttype,
              COUNT(*) AS cnt
            FROM tb_task_master
            WHERE task_category = '고장보고'
              AND task_start_time >= date_trunc('month', now() - interval '{months - 1} months')
              {f_clause}
            GROUP BY 1, fault_category, equipmenttype
            ORDER BY 1, fault_category
            """,
            f_params,
        )
        data = _rows_to_dicts(cur)
        cur.close()
        return {"status": "OK", "data": data}
    finally:
        conn.close()


@router.get("/tasks")
def tasks(
    status: Optional[str] = Query(None, description="진행중 | 완료"),
    fault_category: Optional[str] = Query(None, description="고장 | 이상 | 교체 | 점검"),
    months: int = Query(12, ge=1, le=60),
    limit: int = Query(200, ge=1, le=1000),
):
    """[P6 KPI 드릴다운] tb_task_master 목록 — 상태·분류 필터 지원."""
    if _get_db_connection is None:
        raise HTTPException(500, "DB 미초기화")
    where = ["task_category='고장보고'",
             f"task_start_time >= now() - interval '{months} months'"]
    params: list = []
    if status:
        where.append("status = %s")
        params.append(status)
    if fault_category:
        where.append("fault_category = %s")
        params.append(fault_category)
    sql = (
        "SELECT task_id, sitename, facilitytype, equipmenttype, fault_category, severity, "
        "TO_CHAR(task_start_time, 'YYYY-MM-DD\"T\"HH24:MI:SS') AS task_start_time, "
        "TO_CHAR(resolved_at, 'YYYY-MM-DD\"T\"HH24:MI:SS') AS resolved_at, "
        "resolved_by, status, task_content, resolution_note, recorded_by, photo_urls, "
        "replacement_info "
        f"FROM tb_task_master WHERE {' AND '.join(where)} "
        "ORDER BY task_start_time DESC LIMIT %s"
    )
    params.append(limit)
    conn = _get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        data = _rows_to_dicts(cur)
        cur.close()
        return {"status": "OK", "data": data, "total": len(data)}
    finally:
        conn.close()


@router.get("/stats")
def stats():
    """시설·설비·분류별 집계 (v_equipment_fault_stats)"""
    if _get_db_connection is None:
        raise HTTPException(500, "DB 미초기화")
    conn = _get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
              sitename, facilitytype, equipmenttype, fault_category,
              total_cnt, ongoing_cnt, resolved_cnt, avg_resolve_hours,
              TO_CHAR(first_occurred, 'YYYY-MM-DD') AS first_occurred,
              TO_CHAR(last_occurred, 'YYYY-MM-DD') AS last_occurred
            FROM v_equipment_fault_stats
            ORDER BY total_cnt DESC
            LIMIT 200
            """
        )
        data = _rows_to_dicts(cur)
        cur.close()
        return {"status": "OK", "data": data}
    finally:
        conn.close()


@router.get("/mtbf")
def mtbf(min_cnt: int = Query(2, ge=1, description="최소 고장 건수 (MTBF 계산 유효성)")):
    """설비별 MTBF (v_equipment_mtbf)"""
    if _get_db_connection is None:
        raise HTTPException(500, "DB 미초기화")
    conn = _get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
              equipment_id, sitename, facilitytype, equipmenttype,
              fault_cnt, mtbf_days, min_gap_days,
              TO_CHAR(last_failed_at, 'YYYY-MM-DD') AS last_failed_at
            FROM v_equipment_mtbf
            WHERE fault_cnt >= %s
            ORDER BY mtbf_days ASC NULLS LAST
            LIMIT 100
            """,
            (min_cnt,),
        )
        data = _rows_to_dicts(cur)
        cur.close()
        return {"status": "OK", "data": data}
    finally:
        conn.close()


@router.get("/ranking")
def ranking(
    limit: int = Query(20, ge=1, le=100),
    sitename: Optional[str] = Query(None),
    facilitytype: Optional[str] = Query(None),
    equipmenttype: Optional[str] = Query(None),
    equipment_id: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
):
    """시설별 장애 Top N (tb_task_master 직접 집계 + 필터)"""
    if _get_db_connection is None:
        raise HTTPException(500, "DB 미초기화")
    conn = _get_db_connection()
    try:
        cur = conn.cursor()
        f_clause, f_params = _build_filter_clause(sitename, facilitytype, equipmenttype, equipment_id, keyword)
        cur.execute(
            f"""
            SELECT
              sitename,
              facilitytype,
              COUNT(*) AS total_faults,
              COUNT(*) FILTER (WHERE task_start_time >= now() - interval '30 days')  AS last_30d_cnt,
              COUNT(*) FILTER (WHERE task_start_time >= now() - interval '365 days') AS last_1y_cnt,
              COUNT(DISTINCT equipment_id) AS affected_equipments
            FROM tb_task_master
            WHERE task_category = '고장보고'
              {f_clause}
            GROUP BY sitename, facilitytype
            ORDER BY total_faults DESC
            LIMIT %s
            """,
            f_params + [limit],
        )
        data = _rows_to_dicts(cur)
        cur.close()
        return {"status": "OK", "data": data}
    finally:
        conn.close()
