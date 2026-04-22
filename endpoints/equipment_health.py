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
        "replacement_info, resolution_photo_urls "
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


# ─────────────────────────────────────────────────────────────────────
# 내용연수 기반 교체 권고 (migration 0053)
# ─────────────────────────────────────────────────────────────────────

@router.get("/lifespan")
def equipment_lifespan(
    only_categorized: bool = Query(False, description="카테고리 매핑된 설비만 반환"),
):
    """설비별 설치 경과 연수 + 내용연수 기준 교체 권고 상태.

    응답:
      categories: [{category, years_recommended, years_tax, eol_note, remarks}]
      equipments: [{equipment_id, sitename, facilitytype, equipmenttype,
                    category, commissioned_at, years_used,
                    years_recommended, years_tax, status}]
        status ∈ {no_data, no_category, normal, approaching, overdue}
    """
    if _get_db_connection is None:
        raise HTTPException(500, "DB 미초기화")
    conn = _get_db_connection()
    try:
        cur = conn.cursor()

        # 기준 테이블
        cur.execute("""
            SELECT category, years_recommended, years_tax, eol_note, remarks
            FROM tb_equipment_lifespan
            ORDER BY years_recommended
        """)
        categories = _rows_to_dicts(cur)

        # 설비 + 카테고리 매핑 + 경과 연수 계산
        cur.execute("""
            SELECT
              e.equipment_id,
              e.sitename,
              e.facilitytype,
              e.equipmenttype,
              m.category,
              e.commissioned_at,
              CASE
                WHEN e.commissioned_at IS NOT NULL
                THEN EXTRACT(YEAR FROM age(now()::date, e.commissioned_at))::int
                ELSE NULL
              END AS years_used,
              l.years_recommended,
              l.years_tax,
              CASE
                WHEN m.category IS NULL                       THEN 'no_category'
                WHEN e.commissioned_at IS NULL                THEN 'no_data'
                WHEN EXTRACT(YEAR FROM age(now()::date, e.commissioned_at))
                     >= l.years_recommended                   THEN 'overdue'
                WHEN EXTRACT(YEAR FROM age(now()::date, e.commissioned_at))
                     >= l.years_recommended - 1               THEN 'approaching'
                ELSE                                               'normal'
              END AS status
            FROM tb_equipment_info e
            LEFT JOIN tb_equipment_category_map m ON m.equipmenttype = e.equipmenttype
            LEFT JOIN tb_equipment_lifespan    l ON l.category      = m.category
            WHERE e.status IN ('운영중', 'operational')
              AND (NOT %s OR m.category IS NOT NULL)
            ORDER BY
              CASE WHEN e.commissioned_at IS NULL THEN 1 ELSE 0 END,
              (e.commissioned_at) ASC NULLS LAST,
              e.sitename, e.facilitytype, e.equipmenttype
        """, (only_categorized,))
        equipments = _rows_to_dicts(cur)

        # 상태별 집계
        counts = {"overdue": 0, "approaching": 0, "normal": 0,
                  "no_data": 0, "no_category": 0}
        for r in equipments:
            counts[r["status"]] = counts.get(r["status"], 0) + 1

        # commissioned_at ISO 문자열 변환
        for r in equipments:
            if r.get("commissioned_at"):
                r["commissioned_at"] = r["commissioned_at"].isoformat()

        cur.close()
        return {
            "status": "OK",
            "categories": categories,
            "equipments": equipments,
            "summary": {
                "total": len(equipments),
                **counts,
            },
        }
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────
# [중기 확장 #3] 설비 교체 실적 (P7 replacement_info)
# ─────────────────────────────────────────────────────────────────────

@router.get("/replacement-history")
def replacement_history(months: int = Query(24, ge=1, le=120)):
    """최근 교체(fault_category='교체') 실적 집계.

    응답:
      summary: {total, monthly, by_category, by_sitename_top}
      rows:    [{task_id, sitename, facilitytype, equipmenttype, category,
                 task_start_time, replacement_info, recorded_by}]
    """
    if _get_db_connection is None:
        raise HTTPException(500, "DB 미초기화")
    conn = _get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"""
          SELECT
            t.task_id,
            t.sitename,
            t.facilitytype,
            t.equipmenttype,
            m.category,
            to_char(t.task_start_time, 'YYYY-MM-DD HH24:MI:SS') AS task_start_time,
            to_char(t.task_start_time, 'YYYY-MM') AS month,
            t.replacement_info,
            t.recorded_by,
            t.task_content
          FROM tb_task_master t
          LEFT JOIN tb_equipment_category_map m ON m.equipmenttype = t.equipmenttype
          WHERE t.task_category = '고장보고'
            AND t.fault_category = '교체'
            AND t.task_start_time >= now() - interval '{months} months'
          ORDER BY t.task_start_time DESC
        """)
        rows = _rows_to_dicts(cur)

        monthly = {}
        by_category = {}
        by_sitename = {}
        for r in rows:
            m = r.get("month")
            if m:
                monthly[m] = monthly.get(m, 0) + 1
            c = r.get("category") or "(미분류)"
            by_category[c] = by_category.get(c, 0) + 1
            s = r.get("sitename") or "(미상)"
            by_sitename[s] = by_sitename.get(s, 0) + 1

        top_sites = sorted(by_sitename.items(), key=lambda x: -x[1])[:10]

        # month 필드는 요약용이라 row 에서 제거 (중복)
        for r in rows:
            r.pop("month", None)

        cur.close()
        return {
            "status": "OK",
            "summary": {
                "total": len(rows),
                "monthly": [{"month": k, "cnt": v} for k, v in sorted(monthly.items())],
                "by_category": [{"category": k, "cnt": v} for k, v in sorted(by_category.items(), key=lambda x: -x[1])],
                "by_sitename_top": [{"sitename": k, "cnt": v} for k, v in top_sites],
            },
            "rows": rows,
        }
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────
# [중기 확장 #4] 카테고리별 연도별 고장률 추이 (내용연수 근거)
# ─────────────────────────────────────────────────────────────────────

@router.get("/fault-trend-by-category")
def fault_trend_by_category(
    granularity: str = Query("year", pattern="^(year|month)$"),
    years: int = Query(5, ge=1, le=10),
    months: int = Query(24, ge=3, le=60),
):
    """카테고리별 시간별 고장 보고 건수 추이 + 카테고리별 설비 수.

    파라미터:
      granularity: 'year' (연도별) | 'month' (월별)
      years:       granularity='year' 시 기간 (1~10)
      months:      granularity='month' 시 기간 (3~60)

    응답:
      granularity: 'year' | 'month'
      labels:      ['2021', '2022', ...] 또는 ['2025-03', '2025-04', ...]
      categories: [{
        category,
        equipment_count,  # 해당 카테고리 전체 설비 수 (고장률 계산용)
        series: [cnt_label1, cnt_label2, ...]
      }]
    """
    if _get_db_connection is None:
        raise HTTPException(500, "DB 미초기화")
    conn = _get_db_connection()
    try:
        cur = conn.cursor()

        # 카테고리별 설비 수 (정규화용)
        cur.execute("""
          SELECT COALESCE(m.category, '(미분류)') AS category, COUNT(*)
          FROM tb_equipment_info e
          LEFT JOIN tb_equipment_category_map m ON m.equipmenttype = e.equipmenttype
          GROUP BY 1
        """)
        equip_counts = {r[0]: int(r[1]) for r in cur.fetchall()}

        # 고장 추이 집계
        if granularity == "year":
            trunc_expr = "EXTRACT(YEAR FROM t.task_start_time)::int"
            where_time = f"t.task_start_time >= now() - interval '{years} years'"
            label_fmt = lambda v: str(v)
        else:
            trunc_expr = "to_char(date_trunc('month', t.task_start_time), 'YYYY-MM')"
            where_time = f"t.task_start_time >= now() - interval '{months} months'"
            label_fmt = lambda v: str(v)

        cur.execute(f"""
          SELECT
            COALESCE(m.category, '(미분류)') AS category,
            {trunc_expr} AS label,
            COUNT(*) AS cnt
          FROM tb_task_master t
          LEFT JOIN tb_equipment_category_map m ON m.equipmenttype = t.equipmenttype
          WHERE t.task_category = '고장보고'
            AND {where_time}
          GROUP BY 1, 2
          ORDER BY 2, 1
        """)
        raw = cur.fetchall()
        # (category, label, cnt)
        labels_set = sorted({label_fmt(r[1]) for r in raw})
        cats_set = sorted({r[0] for r in raw})
        table = {(r[0], label_fmt(r[1])): int(r[2]) for r in raw}
        categories = []
        for c in cats_set:
            categories.append({
                "category": c,
                "equipment_count": equip_counts.get(c, 0),
                "series": [table.get((c, lb), 0) for lb in labels_set],
            })
        cur.close()
        return {
            "status": "OK",
            "granularity": granularity,
            "labels": labels_set,
            "categories": categories,
        }
    finally:
        conn.close()
