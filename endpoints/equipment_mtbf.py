"""
설비 신뢰성 리포트 API (MTBF/MTTR/Availability)

- GET /admin/equipment-mtbf — 설비별 고장 통계 집계

계산식
  MTBF (평균 고장 간격, Mean Time Between Failures)
      = (총 가동시간 - 총 다운타임) / 고장 횟수
  MTTR (평균 복구 시간, Mean Time To Repair)
      = 총 다운타임 / 고장 횟수
  Availability = MTBF / (MTBF + MTTR) × 100%

데이터 소스
  tb_equipment_alarm_report : 알람 이력 (alarm_start_time, alarm_end_time)
  tb_equipment_tag_map       : tagsn → equipment_id 매핑
  tb_equipment_info          : 설비 메타정보

주의
  단일 알람 duration은 24시간으로 캡 (데이터 이상치 · 진행 중 알람 대응).
  같은 tagsn이 복수 설비에 매핑된 경우(예: PLC + 가압펌프가 동일 압력 태그
  공유) 양쪽 설비에 카운트된다 — 이는 의도된 동작.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger("slm")

router = APIRouter()

_get_db_connection = None


def init(get_db_connection_fn):
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


_MTBF_SQL = """
WITH alarms AS (
    SELECT
        m.equipment_id,
        e.equipmenttype,
        e.sitename,
        e.facilitytype,
        LEAST(
            EXTRACT(EPOCH FROM COALESCE(r.alarm_end_time, NOW()) - r.alarm_start_time),
            86400.0
        ) AS dur_sec
    FROM tb_equipment_alarm_report r
    JOIN tb_equipment_tag_map m ON m.tagsn = r.tagsn
    JOIN tb_equipment_info e ON e.equipment_id = m.equipment_id
    WHERE r.alarm_start_time >= NOW() - (%s || ' days')::interval
      AND r.alarm_start_time < NOW()
      {where_site}
      {where_ftype}
)
SELECT
    equipment_id, equipmenttype, sitename, facilitytype,
    COUNT(*) AS fault_count,
    ROUND((SUM(dur_sec) / 3600.0)::numeric, 1) AS downtime_hours,
    ROUND((AVG(dur_sec) / 3600.0)::numeric, 2) AS mttr_hours,
    GREATEST(
        0,
        ROUND(
            ((%s * 24 - SUM(dur_sec) / 3600.0) / NULLIF(COUNT(*), 0))::numeric,
            1
        )
    ) AS mtbf_hours,
    GREATEST(
        0,
        LEAST(
            100,
            ROUND(
                (100.0 * (1 - SUM(dur_sec) / (%s * 86400.0)))::numeric,
                2
            )
        )
    ) AS availability_pct
FROM alarms
GROUP BY equipment_id, equipmenttype, sitename, facilitytype
HAVING COUNT(*) > 0
ORDER BY fault_count DESC, mttr_hours DESC
LIMIT %s
"""


@router.get("/admin/equipment-mtbf")
def list_equipment_mtbf(
    days: int = Query(90, ge=1, le=365, description="집계 기간 (일)"),
    sitename: Optional[str] = Query(None, description="현장명 필터"),
    facilitytype: Optional[str] = Query(None, description="시설유형 필터"),
    limit: int = Query(200, ge=1, le=1000),
):
    """설비별 MTBF/MTTR/가동률 집계 리스트.

    반환 필드 (rows):
      equipment_id, equipmenttype, sitename, facilitytype,
      fault_count, downtime_hours, mttr_hours, mtbf_hours, availability_pct
    """
    if _get_db_connection is None:
        raise HTTPException(500, "equipment_mtbf not initialized")

    where_site = ""
    where_ftype = ""
    params: list = [days]
    if sitename:
        where_site = "AND e.sitename = %s"
        params.append(sitename)
    if facilitytype:
        where_ftype = "AND e.facilitytype = %s"
        params.append(facilitytype)
    params += [days, days, limit]

    sql = _MTBF_SQL.format(where_site=where_site, where_ftype=where_ftype)

    conn = None
    try:
        conn = _get_db_connection()
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = [
                {
                    "equipment_id": r[0],
                    "equipmenttype": r[1],
                    "sitename": r[2],
                    "facilitytype": r[3],
                    "fault_count": int(r[4]) if r[4] is not None else 0,
                    "downtime_hours": float(r[5]) if r[5] is not None else 0.0,
                    "mttr_hours": float(r[6]) if r[6] is not None else 0.0,
                    "mtbf_hours": float(r[7]) if r[7] is not None else 0.0,
                    "availability_pct": float(r[8]) if r[8] is not None else 0.0,
                }
                for r in cur.fetchall()
            ]
        return {
            "status": "OK",
            "period_days": days,
            "filters": {"sitename": sitename, "facilitytype": facilitytype},
            "total": len(rows),
            "data": rows,
        }
    except Exception as e:
        logger.error(f"equipment_mtbf 조회 실패: {e}")
        raise HTTPException(500, f"조회 실패: {e}")
    finally:
        if conn:
            conn.close()
