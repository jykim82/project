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


@router.get("/summary")
def summary(months: int = Query(12, ge=1, le=60, description="최근 N개월 범위")):
    """KPI 요약: 총 건수 / 진행중 / 완료 / 평균 조치시간 / 영향받은 설비 수"""
    if _get_db_connection is None:
        raise HTTPException(500, "DB 미초기화")
    conn = _get_db_connection()
    try:
        cur = conn.cursor()
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
            """
        )
        row = cur.fetchone()
        cols = [d[0] for d in cur.description]
        data = dict(zip(cols, row))
        cur.close()
        return {"status": "OK", "months": months, "data": data}
    finally:
        conn.close()


@router.get("/monthly")
def monthly(months: int = Query(12, ge=1, le=60)):
    """월별 장애 추이 (v_equipment_fault_monthly)"""
    if _get_db_connection is None:
        raise HTTPException(500, "DB 미초기화")
    conn = _get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT
              TO_CHAR(month, 'YYYY-MM') AS month,
              fault_category,
              equipmenttype,
              cnt
            FROM v_equipment_fault_monthly
            WHERE month >= (date_trunc('month', now() - interval '{months - 1} months'))::date
            ORDER BY month, fault_category
            """
        )
        data = _rows_to_dicts(cur)
        cur.close()
        return {"status": "OK", "data": data}
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
def ranking(limit: int = Query(20, ge=1, le=100)):
    """시설별 장애 Top N (v_site_fault_ranking)"""
    if _get_db_connection is None:
        raise HTTPException(500, "DB 미초기화")
    conn = _get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT sitename, facilitytype, total_faults, last_30d_cnt, last_1y_cnt, affected_equipments
            FROM v_site_fault_ranking
            ORDER BY total_faults DESC
            LIMIT %s
            """,
            (limit,),
        )
        data = _rows_to_dicts(cur)
        cur.close()
        return {"status": "OK", "data": data}
    finally:
        conn.close()
