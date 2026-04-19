"""
[P5-rev] 설비 상태 기반 교체 후보 분석 엔드포인트

기존 1:1 매칭 관점(linked_alarm)은 네트워크 LTE 모뎀처럼 짧게 발생·해제가
반복되는 설비에서 의미가 없음. 대신 **설비 단위 집계**로 관점 전환:

  - 기간 내 알람 발생 횟수 / 누적 지속시간
  - 고장 보고(tb_task_master, category='고장보고') 건수 / 조치 완료 시각
  - 조치 이후 재발 수·재발률

을 종합해 설비 상태를 4단계로 분류:

  needs_action          — 알람 빈번, 현장 확인·보고 전무 (조치 필요)
  in_progress           — 보고는 있으나 조치 미완료
  replacement_candidate — 조치 후에도 재발 지속 (교체 후보, 핵심 목표)
  resolved              — 조치 후 거의 재발 없음 (정상 조치)

원칙: 리포트 전용. 자동 해제·상태 변경 없음.
      `memory/feedback_no_auto_alarm_link.md` 사양 유지.

엔드포인트:
  GET /monitoring/alarm-fault-correlation/equipment-status
       ?days=90 &min_alarm=10 &recurrence_rate=0.2 &recurrence_cnt=3
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


_DEFAULT_DAYS = 90
_MAX_DAYS = 365


# ─────────────────────────────────────────────────────────────────────
# Response 모델
# ─────────────────────────────────────────────────────────────────────

class EquipmentRow(BaseModel):
    sitename: str
    facilitytype: str
    equipmenttype: str
    alarm_cnt: int
    total_duration_hours: float
    last_alarm_at: Optional[str]
    fault_cnt: int
    last_resolved_at: Optional[str]
    in_progress_cnt: int
    alarm_after_resolved: int
    recurrence_rate: Optional[float]  # alarm_after_resolved / alarm_cnt
    status: str  # needs_action | in_progress | replacement_candidate | resolved


class StatusSummary(BaseModel):
    total: int
    needs_action: int
    in_progress: int
    replacement_candidate: int
    resolved: int


class EquipmentStatusResponse(BaseModel):
    period_days: int
    min_alarm: int
    recurrence_cnt_threshold: int
    recurrence_rate_threshold: float
    summary: StatusSummary
    rows: list[EquipmentRow]


def _classify(
    alarm_cnt: int,
    fault_cnt: int,
    in_progress_cnt: int,
    last_resolved_at,
    alarm_after_resolved: int,
    recurrence_cnt_thr: int,
    recurrence_rate_thr: float,
) -> str:
    """알람·보고·조치 집계 → 상태 분류."""
    if fault_cnt == 0:
        return "needs_action"
    if in_progress_cnt > 0 or last_resolved_at is None:
        return "in_progress"
    # 조치 완료 — 재발 여부로 교체 후보/정상 조치 판정
    rate = (alarm_after_resolved / alarm_cnt) if alarm_cnt else 0.0
    if alarm_after_resolved >= recurrence_cnt_thr or rate >= recurrence_rate_thr:
        return "replacement_candidate"
    return "resolved"


# ─────────────────────────────────────────────────────────────────────
# /equipment-status — 설비 그룹별 집계 + 상태 분류
# ─────────────────────────────────────────────────────────────────────

@router.get("/equipment-status", response_model=EquipmentStatusResponse)
def equipment_status(
    days: int = Query(_DEFAULT_DAYS, ge=1, le=_MAX_DAYS),
    min_alarm: int = Query(10, ge=1, description="이 미만 알람 설비는 제외"),
    recurrence_cnt: int = Query(3, ge=1, description="조치 후 이 이상 재발이면 교체 후보"),
    recurrence_rate: float = Query(0.2, ge=0.0, le=1.0, description="재발률 이 이상이면 교체 후보"),
):
    """설비 그룹(sitename+facilitytype+equipmenttype) 단위 알람/보고/조치 집계 + 상태 분류.

    min_alarm 미만 설비는 분석 제외 (노이즈 억제).
    """
    days = max(1, min(days, _MAX_DAYS))
    from_sql = f"now() - interval '{days} days'"

    sql = f"""
    WITH alarm_agg AS (
      SELECT
        COALESCE(sitename, '(미상)')     AS sitename,
        COALESCE(facilitytype, '(미상)') AS facilitytype,
        COALESCE(equipmenttype, '(미상)') AS equipmenttype,
        COUNT(*) AS alarm_cnt,
        SUM(CASE WHEN alarm_end_time IS NOT NULL
                 THEN EXTRACT(EPOCH FROM (alarm_end_time - alarm_start_time)) / 3600.0
                 ELSE 0 END) AS total_duration_hours,
        MAX(alarm_start_time) AS last_alarm_at
      FROM tb_equipment_alarm_report
      WHERE alarm_start_time >= {from_sql}
      GROUP BY 1, 2, 3
    ),
    fault_agg AS (
      SELECT
        COALESCE(sitename, '(미상)')     AS sitename,
        COALESCE(facilitytype, '(미상)') AS facilitytype,
        COALESCE(equipmenttype, '(미상)') AS equipmenttype,
        COUNT(*) AS fault_cnt,
        COUNT(*) FILTER (WHERE resolved_at IS NULL) AS in_progress_cnt,
        MAX(resolved_at) AS last_resolved_at
      FROM tb_task_master
      WHERE task_category = '고장보고'
        AND task_start_time >= {from_sql}
      GROUP BY 1, 2, 3
    )
    SELECT
      a.sitename, a.facilitytype, a.equipmenttype,
      a.alarm_cnt, a.total_duration_hours, a.last_alarm_at,
      COALESCE(f.fault_cnt, 0)       AS fault_cnt,
      COALESCE(f.in_progress_cnt, 0) AS in_progress_cnt,
      f.last_resolved_at,
      -- 조치 이후 알람 (last_resolved_at 있을 때만)
      COALESCE((
        SELECT COUNT(*) FROM tb_equipment_alarm_report a2
         WHERE COALESCE(a2.sitename, '(미상)')     = a.sitename
           AND COALESCE(a2.facilitytype, '(미상)') = a.facilitytype
           AND COALESCE(a2.equipmenttype, '(미상)') = a.equipmenttype
           AND f.last_resolved_at IS NOT NULL
           AND a2.alarm_start_time > f.last_resolved_at
           AND a2.alarm_start_time >= {from_sql}
      ), 0) AS alarm_after_resolved
    FROM alarm_agg a
    LEFT JOIN fault_agg f
      ON a.sitename = f.sitename
     AND a.facilitytype = f.facilitytype
     AND a.equipmenttype = f.equipmenttype
    WHERE a.alarm_cnt >= %s
    ORDER BY a.alarm_cnt DESC
    """

    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql, (min_alarm,))
        raw = cur.fetchall()
        cur.close()
    finally:
        conn.close()

    rows: list[EquipmentRow] = []
    counts = {"needs_action": 0, "in_progress": 0, "replacement_candidate": 0, "resolved": 0}
    for r in raw:
        (sitename, facilitytype, equipmenttype, alarm_cnt, dur_h, last_alarm_at,
         fault_cnt, in_progress_cnt, last_resolved_at, a_after) = r
        alarm_cnt = int(alarm_cnt or 0)
        fault_cnt = int(fault_cnt or 0)
        in_progress_cnt = int(in_progress_cnt or 0)
        a_after = int(a_after or 0)
        dur_h = float(dur_h or 0.0)
        status = _classify(
            alarm_cnt, fault_cnt, in_progress_cnt, last_resolved_at, a_after,
            recurrence_cnt, recurrence_rate,
        )
        counts[status] = counts.get(status, 0) + 1
        rate = (a_after / alarm_cnt) if alarm_cnt else None
        rows.append(EquipmentRow(
            sitename=sitename,
            facilitytype=facilitytype,
            equipmenttype=equipmenttype,
            alarm_cnt=alarm_cnt,
            total_duration_hours=round(dur_h, 2),
            last_alarm_at=last_alarm_at.isoformat() if last_alarm_at else None,
            fault_cnt=fault_cnt,
            last_resolved_at=last_resolved_at.isoformat() if last_resolved_at else None,
            in_progress_cnt=in_progress_cnt,
            alarm_after_resolved=a_after,
            recurrence_rate=round(rate, 4) if rate is not None else None,
            status=status,
        ))

    # 정렬: 교체 후보 최상위 → 조치 필요 → 진행중 → 정상 조치 (각 그룹 내 알람수↓)
    priority = {
        "replacement_candidate": 0, "needs_action": 1, "in_progress": 2, "resolved": 3,
    }
    rows.sort(key=lambda x: (priority.get(x.status, 99), -x.alarm_cnt))

    summary = StatusSummary(
        total=len(rows),
        needs_action=counts["needs_action"],
        in_progress=counts["in_progress"],
        replacement_candidate=counts["replacement_candidate"],
        resolved=counts["resolved"],
    )
    return EquipmentStatusResponse(
        period_days=days,
        min_alarm=min_alarm,
        recurrence_cnt_threshold=recurrence_cnt,
        recurrence_rate_threshold=recurrence_rate,
        summary=summary,
        rows=rows,
    )


# ─────────────────────────────────────────────────────────────────────
# /equipment-timeline — 특정 설비 통합 이력 (알람 + 고장/조치)
# ─────────────────────────────────────────────────────────────────────

class TimelineEvent(BaseModel):
    kind: str                 # alarm | fault_report | fault_resolved
    time: str                 # ISO
    title: str                # 요약 한 줄
    detail: dict              # 상세 필드 (kind별 상이)


class TimelineResponse(BaseModel):
    sitename: str
    facilitytype: str
    equipmenttype: str
    period_days: int
    alarm_count: int
    fault_count: int
    ongoing_count: int
    events: list[TimelineEvent]  # 최신 → 과거 순


@router.get("/equipment-timeline", response_model=TimelineResponse)
def equipment_timeline(
    sitename: str = Query(...),
    facilitytype: str = Query(...),
    equipmenttype: str = Query(...),
    days: int = Query(_DEFAULT_DAYS, ge=1, le=_MAX_DAYS),
    limit_alarm: int = Query(50, ge=1, le=500),
):
    """해당 (sitename, facilitytype, equipmenttype) 의 알람 + 고장 보고 +
    조치 완료 이벤트를 시계열로 통합. 최신순 정렬.
    """
    from_sql = f"now() - interval '{days} days'"
    conn = _get_conn()
    try:
        cur = conn.cursor()

        # 알람 (최근 limit_alarm 건)
        cur.execute(f"""
            SELECT alarm_start_time, alarm_end_time, tagsn,
                   alarm_category, alarm_severity, alarm_msg, diagnosed_cause,
                   CASE WHEN alarm_end_time IS NOT NULL
                        THEN EXTRACT(EPOCH FROM (alarm_end_time - alarm_start_time))/3600.0
                        ELSE NULL END AS duration_h
            FROM tb_equipment_alarm_report
            WHERE sitename = %s AND facilitytype = %s AND equipmenttype = %s
              AND alarm_start_time >= {from_sql}
            ORDER BY alarm_start_time DESC
            LIMIT %s
        """, (sitename, facilitytype, equipmenttype, limit_alarm))
        alarm_rows = cur.fetchall()

        # 전체 알람 카운트
        cur.execute(f"""
            SELECT COUNT(*) FROM tb_equipment_alarm_report
            WHERE sitename=%s AND facilitytype=%s AND equipmenttype=%s
              AND alarm_start_time >= {from_sql}
        """, (sitename, facilitytype, equipmenttype))
        alarm_total = int(cur.fetchone()[0] or 0)

        # 고장 보고 + 조치 이벤트
        cur.execute(f"""
            SELECT task_id, task_start_time, resolved_at, resolved_by,
                   fault_category, severity, task_content, resolution_note,
                   recorded_by, status, photo_urls
            FROM tb_task_master
            WHERE task_category='고장보고'
              AND sitename=%s AND facilitytype=%s AND equipmenttype=%s
              AND task_start_time >= {from_sql}
            ORDER BY task_start_time DESC
        """, (sitename, facilitytype, equipmenttype))
        fault_rows = cur.fetchall()
        cur.close()

        events: list[TimelineEvent] = []
        for r in alarm_rows:
            events.append(TimelineEvent(
                kind="alarm",
                time=r[0].isoformat(),
                title=f"[알람] {r[3] or ''} · {r[5] or r[2]}",
                detail={
                    "tagsn": r[2], "alarm_category": r[3],
                    "alarm_severity": r[4], "alarm_msg": r[5],
                    "diagnosed_cause": r[6],
                    "alarm_end_time": r[1].isoformat() if r[1] else None,
                    "duration_hours": round(float(r[7]), 2) if r[7] is not None else None,
                },
            ))

        ongoing = 0
        for r in fault_rows:
            task_id, tst, rat, rby, fc, sev, content, note, recorded_by, status, photo_urls = r
            if status != "완료":
                ongoing += 1
            events.append(TimelineEvent(
                kind="fault_report",
                time=tst.isoformat() if tst else "",
                title=f"[고장 보고 #{task_id}] {fc or ''}{(' · ' + sev) if sev else ''}",
                detail={
                    "task_id": int(task_id),
                    "fault_category": fc, "severity": sev,
                    "content": content, "recorded_by": recorded_by,
                    "status": status,
                    "photo_urls": photo_urls or [],
                    "resolved_at": rat.isoformat() if rat else None,
                    "resolved_by": rby,
                    "resolution_note": note,
                },
            ))
            if rat is not None:
                events.append(TimelineEvent(
                    kind="fault_resolved",
                    time=rat.isoformat(),
                    title=f"[조치 완료 #{task_id}] {rby or ''}",
                    detail={
                        "task_id": int(task_id),
                        "resolution_note": note,
                        "resolved_by": rby,
                    },
                ))

        # 최신 → 과거
        events.sort(key=lambda e: e.time, reverse=True)

        return TimelineResponse(
            sitename=sitename,
            facilitytype=facilitytype,
            equipmenttype=equipmenttype,
            period_days=days,
            alarm_count=alarm_total,
            fault_count=len(fault_rows),
            ongoing_count=ongoing,
            events=events,
        )
    finally:
        conn.close()
