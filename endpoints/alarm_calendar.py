"""
알람 발생 캘린더·히트맵 API

- GET /alarm/calendar — 일자·시간 bucket 알람 발생 건수

용도: ECharts heatmap (요일×시간) 또는 calendar (일자별) 시각화 입력.
사용자가 "이 시설은 새벽 3시에 자주 알람이 난다" 같은 패턴을 발견할 수 있게 함.
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


@router.get("/alarm/calendar")
def alarm_calendar(
    days: int = Query(30, ge=1, le=365, description="조회 기간 (일)"),
    sitename: Optional[str] = Query(None, description="현장명 필터"),
    facilitytype: Optional[str] = Query(None, description="시설유형 필터"),
    alarm_category: Optional[str] = Query(None, description="경보 카테고리 필터"),
):
    """알람 이력을 (날짜, 시간) 버킷으로 집계해 히트맵 입력 데이터를 반환한다.

    응답 구조:
      {
        "status": "OK",
        "period_days": 30,
        "total_alarms": 1234,
        "by_day_hour": [{"date": "2026-04-11", "hour": 14, "count": 5}, ...],
        "by_weekday_hour": [{"weekday": 0, "hour": 14, "count": 38}, ...],
          // weekday: 0=월 ... 6=일 (PostgreSQL ISODOW 1~7 → 0~6 변환)
        "by_category": [{"category": "통신", "count": 42}, ...]
      }
    """
    if _get_db_connection is None:
        raise HTTPException(500, "alarm_calendar not initialized")

    where_clauses = ["alarm_start_time >= NOW() - (%s || ' days')::interval"]
    params_common: list = [days]
    if sitename:
        where_clauses.append("sitename = %s")
        params_common.append(sitename)
    if facilitytype:
        where_clauses.append("facilitytype = %s")
        params_common.append(facilitytype)
    if alarm_category:
        where_clauses.append("alarm_category = %s")
        params_common.append(alarm_category)
    where_sql = " AND ".join(where_clauses)

    conn = None
    try:
        conn = _get_db_connection()
        with conn.cursor() as cur:
            # 1. 일자·시간 집계
            cur.execute(
                f"""
                SELECT TO_CHAR(alarm_start_time, 'YYYY-MM-DD') AS d,
                       EXTRACT(HOUR FROM alarm_start_time)::int AS h,
                       COUNT(*) AS cnt
                FROM tb_equipment_alarm_report
                WHERE {where_sql}
                GROUP BY d, h
                ORDER BY d, h
                """,
                params_common,
            )
            by_day_hour = [
                {"date": r[0], "hour": int(r[1]), "count": int(r[2])}
                for r in cur.fetchall()
            ]

            # 2. 요일·시간 집계 (패턴 감지)
            cur.execute(
                f"""
                SELECT (EXTRACT(ISODOW FROM alarm_start_time)::int - 1) AS wd,
                       EXTRACT(HOUR FROM alarm_start_time)::int AS h,
                       COUNT(*) AS cnt
                FROM tb_equipment_alarm_report
                WHERE {where_sql}
                GROUP BY wd, h
                ORDER BY wd, h
                """,
                params_common,
            )
            by_weekday_hour = [
                {"weekday": int(r[0]), "hour": int(r[1]), "count": int(r[2])}
                for r in cur.fetchall()
            ]

            # 3. 카테고리 분포
            cur.execute(
                f"""
                SELECT COALESCE(alarm_category, '(미분류)') AS cat, COUNT(*) AS cnt
                FROM tb_equipment_alarm_report
                WHERE {where_sql}
                GROUP BY cat
                ORDER BY cnt DESC
                """,
                params_common,
            )
            by_category = [
                {"category": r[0], "count": int(r[1])} for r in cur.fetchall()
            ]

        total = sum(item["count"] for item in by_day_hour)
        return {
            "status": "OK",
            "period_days": days,
            "filters": {
                "sitename": sitename,
                "facilitytype": facilitytype,
                "alarm_category": alarm_category,
            },
            "total_alarms": total,
            "by_day_hour": by_day_hour,
            "by_weekday_hour": by_weekday_hour,
            "by_category": by_category,
        }
    except Exception as e:
        logger.error(f"alarm_calendar 조회 실패: {e}")
        raise HTTPException(500, f"조회 실패: {e}")
    finally:
        if conn:
            conn.close()
