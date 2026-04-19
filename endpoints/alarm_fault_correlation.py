"""
[P5] 알람 ↔ 장애 매칭 분석 엔드포인트

tb_equipment_alarm_report (시스템 자동 감지 알람) 와
tb_task_master (현장 작업자 등록 장애 기록, task_category='고장보고') 를
linked_alarm_start + linked_alarm_tagsn 로 조인하여 매칭 지표 산출.

주의: 알람 자동 해제/연계는 설계상 금지. 본 모듈은 **분석·리포트 전용**.
     "매칭 안 됨" 은 사용자 판단 지표 (오탐/미검지 후보) 로만 제공.

엔드포인트:
  GET /monitoring/alarm-fault-correlation/summary      — 상단 KPI
  GET /monitoring/alarm-fault-correlation/matrix       — 설비유형별 교차표
  GET /monitoring/alarm-fault-correlation/lag          — 탐지 지연 히스토그램
  GET /monitoring/alarm-fault-correlation/unmatched    — 미매칭 Top (alarm|fault)
"""

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/monitoring/alarm-fault-correlation", tags=["alarm-fault-correlation"])

_get_db_connection = None


def init(get_db_connection_fn):
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


def _get_conn():
    if _get_db_connection is None:
        raise HTTPException(500, "DB 커넥션 미초기화")
    return _get_db_connection()


# 기간 (일) 파라미터 상수화
_DEFAULT_DAYS = 90
_MAX_DAYS = 365


def _period_sql(days: int) -> str:
    """SQL interval 표현식 반환. 서버측 now() 기준."""
    days = max(1, min(days, _MAX_DAYS))
    return f"now() - interval '{days} days'"


# ─────────────────────────────────────────────────────────────────────
# Response 모델
# ─────────────────────────────────────────────────────────────────────

class CorrelationSummary(BaseModel):
    period_days: int
    alarm_total: int
    alarm_closed: int
    fault_total: int
    matched: int
    unmatched_alarm: int  # 종료된 알람 중 연결 없는 것 (오탐 후보)
    unmatched_fault: int  # 장애 중 알람 연결 없는 것 (미검지 후보)
    alarm_precision: Optional[float]  # matched / alarm_closed (0.0~1.0)
    fault_coverage: Optional[float]   # matched / fault_total


class MatrixRow(BaseModel):
    equipmenttype: str
    alarm_cnt: int
    fault_cnt: int
    matched_cnt: int


class LagBin(BaseModel):
    label: str          # "≤1h", "1-6h", "6-24h", "1-3d", "≥3d"
    count: int


class LagResponse(BaseModel):
    period_days: int
    total_matched: int
    bins: list[LagBin]
    p50_hours: Optional[float]
    p95_hours: Optional[float]
    avg_hours: Optional[float]


class UnmatchedAlarm(BaseModel):
    alarm_start_time: str
    alarm_end_time: Optional[str]
    tagsn: str
    sitename: Optional[str]
    facilitytype: Optional[str]
    equipmenttype: Optional[str]
    alarm_category: Optional[str]
    alarm_severity: Optional[str]
    alarm_msg: Optional[str]
    duration_hours: Optional[float]


class UnmatchedFault(BaseModel):
    task_id: int
    task_start_time: str
    sitename: Optional[str]
    facilitytype: Optional[str]
    equipmenttype: Optional[str]
    fault_category: Optional[str]
    severity: Optional[str]
    task_content: Optional[str]
    recorded_by: Optional[str]


# ─────────────────────────────────────────────────────────────────────
# /summary — 상단 KPI
# ─────────────────────────────────────────────────────────────────────

@router.get("/summary", response_model=CorrelationSummary)
def summary(days: int = Query(_DEFAULT_DAYS, ge=1, le=_MAX_DAYS)):
    from_sql = _period_sql(days)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT
              (SELECT count(*) FROM tb_equipment_alarm_report
                 WHERE alarm_start_time >= {from_sql}) AS alarm_total,
              (SELECT count(*) FROM tb_equipment_alarm_report
                 WHERE alarm_start_time >= {from_sql}
                   AND alarm_end_time IS NOT NULL) AS alarm_closed,
              (SELECT count(*) FROM tb_task_master
                 WHERE task_category='고장보고' AND task_start_time >= {from_sql}) AS fault_total,
              (SELECT count(*) FROM tb_task_master
                 WHERE task_category='고장보고' AND task_start_time >= {from_sql}
                   AND linked_alarm_start IS NOT NULL
                   AND linked_alarm_tagsn  IS NOT NULL) AS matched,
              (SELECT count(*) FROM tb_equipment_alarm_report a
                 WHERE a.alarm_start_time >= {from_sql}
                   AND a.alarm_end_time IS NOT NULL
                   AND NOT EXISTS (
                     SELECT 1 FROM tb_task_master t
                      WHERE t.task_category='고장보고'
                        AND t.linked_alarm_start = a.alarm_start_time
                        AND t.linked_alarm_tagsn = a.tagsn
                   )) AS unmatched_alarm,
              (SELECT count(*) FROM tb_task_master
                 WHERE task_category='고장보고' AND task_start_time >= {from_sql}
                   AND (linked_alarm_start IS NULL OR linked_alarm_tagsn IS NULL)) AS unmatched_fault
        """)
        row = cur.fetchone()
        cur.close()
        alarm_total, alarm_closed, fault_total, matched, unmatched_alarm, unmatched_fault = row
        precision = (matched / alarm_closed) if alarm_closed else None
        coverage = (matched / fault_total) if fault_total else None
        return CorrelationSummary(
            period_days=days,
            alarm_total=int(alarm_total or 0),
            alarm_closed=int(alarm_closed or 0),
            fault_total=int(fault_total or 0),
            matched=int(matched or 0),
            unmatched_alarm=int(unmatched_alarm or 0),
            unmatched_fault=int(unmatched_fault or 0),
            alarm_precision=round(precision, 4) if precision is not None else None,
            fault_coverage=round(coverage, 4) if coverage is not None else None,
        )
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────
# /matrix — 설비유형별 교차표
# ─────────────────────────────────────────────────────────────────────

@router.get("/matrix", response_model=list[MatrixRow])
def matrix(days: int = Query(_DEFAULT_DAYS, ge=1, le=_MAX_DAYS)):
    from_sql = _period_sql(days)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"""
            WITH a AS (
              SELECT COALESCE(equipmenttype, '(미상)') AS equipmenttype,
                     count(*) AS alarm_cnt
              FROM tb_equipment_alarm_report
              WHERE alarm_start_time >= {from_sql}
              GROUP BY 1
            ),
            f AS (
              SELECT COALESCE(equipmenttype, '(미상)') AS equipmenttype,
                     count(*) AS fault_cnt,
                     SUM((linked_alarm_start IS NOT NULL AND linked_alarm_tagsn IS NOT NULL)::int)
                       AS matched_cnt
              FROM tb_task_master
              WHERE task_category='고장보고' AND task_start_time >= {from_sql}
              GROUP BY 1
            )
            SELECT COALESCE(a.equipmenttype, f.equipmenttype) AS equipmenttype,
                   COALESCE(a.alarm_cnt,   0),
                   COALESCE(f.fault_cnt,   0),
                   COALESCE(f.matched_cnt, 0)
            FROM a FULL OUTER JOIN f USING (equipmenttype)
            ORDER BY (COALESCE(a.alarm_cnt,0)+COALESCE(f.fault_cnt,0)) DESC, equipmenttype
        """)
        rows = cur.fetchall()
        cur.close()
        return [MatrixRow(equipmenttype=r[0], alarm_cnt=int(r[1]), fault_cnt=int(r[2]), matched_cnt=int(r[3])) for r in rows]
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────
# /lag — 탐지 지연 (알람 발생 → 장애 기록)
# ─────────────────────────────────────────────────────────────────────

_LAG_BINS = [
    ("≤1h",   0.0,  1.0),
    ("1-6h",  1.0,  6.0),
    ("6-24h", 6.0,  24.0),
    ("1-3d",  24.0, 72.0),
    ("≥3d",   72.0, None),
]


@router.get("/lag", response_model=LagResponse)
def lag(days: int = Query(_DEFAULT_DAYS, ge=1, le=_MAX_DAYS)):
    from_sql = _period_sql(days)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT EXTRACT(EPOCH FROM (task_start_time - linked_alarm_start)) / 3600.0
            FROM tb_task_master
            WHERE task_category='고장보고'
              AND task_start_time >= {from_sql}
              AND linked_alarm_start IS NOT NULL
              AND linked_alarm_tagsn IS NOT NULL
        """)
        hours_list = [float(r[0]) for r in cur.fetchall() if r[0] is not None and float(r[0]) >= 0]
        cur.close()

        total = len(hours_list)
        bins: list[LagBin] = []
        for label, lo, hi in _LAG_BINS:
            if hi is None:
                cnt = sum(1 for h in hours_list if h >= lo)
            else:
                cnt = sum(1 for h in hours_list if lo <= h < hi)
            bins.append(LagBin(label=label, count=cnt))

        sorted_hours = sorted(hours_list)
        p50 = sorted_hours[int(total * 0.5)] if total else None
        p95 = sorted_hours[int(total * 0.95)] if total else None
        avg = (sum(sorted_hours) / total) if total else None

        return LagResponse(
            period_days=days,
            total_matched=total,
            bins=bins,
            p50_hours=round(p50, 2) if p50 is not None else None,
            p95_hours=round(p95, 2) if p95 is not None else None,
            avg_hours=round(avg, 2) if avg is not None else None,
        )
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────
# /unmatched — 미매칭 Top 목록
# ─────────────────────────────────────────────────────────────────────

@router.get("/unmatched")
def unmatched(
    kind: str = Query("alarm", description="alarm | fault"),
    days: int = Query(_DEFAULT_DAYS, ge=1, le=_MAX_DAYS),
    limit: int = Query(20, ge=1, le=200),
) -> dict:
    if kind not in ("alarm", "fault"):
        raise HTTPException(400, "kind 는 alarm 또는 fault")
    from_sql = _period_sql(days)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        if kind == "alarm":
            cur.execute(f"""
                SELECT a.alarm_start_time, a.alarm_end_time, a.tagsn, a.sitename,
                       a.facilitytype, a.equipmenttype, a.alarm_category,
                       a.alarm_severity, a.alarm_msg,
                       CASE WHEN a.alarm_end_time IS NOT NULL
                            THEN EXTRACT(EPOCH FROM (a.alarm_end_time - a.alarm_start_time))/3600.0
                            ELSE NULL END AS duration_h
                FROM tb_equipment_alarm_report a
                WHERE a.alarm_start_time >= {from_sql}
                  AND a.alarm_end_time IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM tb_task_master t
                     WHERE t.task_category='고장보고'
                       AND t.linked_alarm_start = a.alarm_start_time
                       AND t.linked_alarm_tagsn = a.tagsn
                  )
                ORDER BY a.alarm_start_time DESC
                LIMIT %s
            """, (limit,))
            rows = cur.fetchall()
            cur.close()
            items = [
                UnmatchedAlarm(
                    alarm_start_time=r[0].isoformat() if r[0] else "",
                    alarm_end_time=r[1].isoformat() if r[1] else None,
                    tagsn=r[2] or "",
                    sitename=r[3], facilitytype=r[4], equipmenttype=r[5],
                    alarm_category=r[6], alarm_severity=r[7], alarm_msg=r[8],
                    duration_hours=round(float(r[9]), 2) if r[9] is not None else None,
                ).model_dump()
                for r in rows
            ]
        else:
            cur.execute(f"""
                SELECT task_id, task_start_time, sitename, facilitytype, equipmenttype,
                       fault_category, severity, task_content, recorded_by
                FROM tb_task_master
                WHERE task_category='고장보고'
                  AND task_start_time >= {from_sql}
                  AND (linked_alarm_start IS NULL OR linked_alarm_tagsn IS NULL)
                ORDER BY task_start_time DESC
                LIMIT %s
            """, (limit,))
            rows = cur.fetchall()
            cur.close()
            items = [
                UnmatchedFault(
                    task_id=int(r[0]),
                    task_start_time=r[1].isoformat() if r[1] else "",
                    sitename=r[2], facilitytype=r[3], equipmenttype=r[4],
                    fault_category=r[5], severity=r[6], task_content=r[7],
                    recorded_by=r[8],
                ).model_dump()
                for r in rows
            ]
        return {"kind": kind, "period_days": days, "items": items, "total": len(items)}
    finally:
        conn.close()
