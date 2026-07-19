"""
일정 알림 API — tb_user_schedule (docs/memo-schedule-spec.md)

개인 스케줄 리마인더 — SCADA 알람(tb_equipment_alarm_report)과 무관.
due 조회는 30초 폴링 전제: alarm_at 경과 + 미확인(acked_at IS NULL) 건 반환.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger("slm")

router = APIRouter(prefix="/schedule", tags=["schedule"])

_get_db_connection = None


def init(get_db_connection_fn):
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


def _get_conn():
    if _get_db_connection is None:
        raise RuntimeError("user_schedule not initialized")
    return _get_db_connection()


class ScheduleCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field("", max_length=5000)
    alarm_at: str = Field(..., description="ISO 8601 알림 시각")
    created_by: str = Field(..., max_length=50)


class ScheduleUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    content: Optional[str] = Field(None, max_length=5000)
    alarm_at: Optional[str] = None
    user_id: str = Field(..., max_length=50)


def _row_to_dict(row) -> dict:
    return {
        "schedule_idn": row[0],
        "title": row[1],
        "content": row[2],
        "alarm_at": row[3].isoformat() if row[3] else None,
        "created_by": row[4],
        "acked_at": row[5].isoformat() if row[5] else None,
    }


@router.get("/list")
def list_schedules(
    user_id: str,
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$", description="YYYY-MM"),
    region: str = "R01",
):
    """월 단위 본인 일정 조회 (달력 렌더링용)."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT schedule_idn, title, content, alarm_at, created_by, acked_at "
                "FROM tb_user_schedule "
                "WHERE region=%s AND created_by=%s AND use_yn='Y' "
                "  AND alarm_at >= %s::date "
                "  AND alarm_at < (%s::date + interval '1 month') "
                "ORDER BY alarm_at",
                (region, user_id, f"{month}-01", f"{month}-01"),
            )
            return {"status": "OK", "data": [_row_to_dict(r) for r in cur.fetchall()]}
    except Exception as e:
        logger.error("schedule list error: %s", e)
        raise HTTPException(status_code=500, detail="일정 조회 실패")
    finally:
        conn.close()


@router.get("/due")
def due_schedules(user_id: str, region: str = "R01"):
    """알림 시각 경과 + 미확인 일정. 최초 반환 시 notified_at 기록 (관측용)."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT schedule_idn, title, content, alarm_at, created_by, acked_at "
                "FROM tb_user_schedule "
                "WHERE region=%s AND created_by=%s AND use_yn='Y' "
                "  AND alarm_at <= now() AND acked_at IS NULL "
                "ORDER BY alarm_at",
                (region, user_id),
            )
            rows = cur.fetchall()
            if rows:
                cur.execute(
                    "UPDATE tb_user_schedule SET notified_at = now() "
                    "WHERE region=%s AND schedule_idn = ANY(%s) AND notified_at IS NULL",
                    (region, [r[0] for r in rows]),
                )
        conn.commit()
        return {"status": "OK", "data": [_row_to_dict(r) for r in rows]}
    except Exception as e:
        conn.rollback()
        logger.error("schedule due error: %s", e)
        raise HTTPException(status_code=500, detail="일정 알림 조회 실패")
    finally:
        conn.close()


@router.post("")
def create_schedule(body: ScheduleCreate, region: str = "R01"):
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tb_user_schedule (region, title, content, alarm_at, created_by) "
                "VALUES (%s, %s, %s, %s::timestamptz, %s) RETURNING schedule_idn",
                (region, body.title, body.content, body.alarm_at, body.created_by),
            )
            idn = cur.fetchone()[0]
        conn.commit()
        return {"status": "OK", "schedule_idn": idn}
    except Exception as e:
        conn.rollback()
        logger.error("schedule create error: %s", e)
        raise HTTPException(status_code=500, detail="일정 등록 실패")
    finally:
        conn.close()


def _check_owner(cur, region: str, idn: int, user_id: str):
    cur.execute(
        "SELECT created_by FROM tb_user_schedule "
        "WHERE region=%s AND schedule_idn=%s AND use_yn='Y'",
        (region, idn),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="일정이 없습니다")
    if row[0] != user_id:
        raise HTTPException(status_code=403, detail="본인 일정만 변경할 수 있습니다")


@router.post("/{idn}/ack")
def ack_schedule(idn: int, user_id: str, region: str = "R01"):
    """팝업 확인 — 이후 due 에서 제외."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            _check_owner(cur, region, idn, user_id)
            cur.execute(
                "UPDATE tb_user_schedule SET acked_at = now() "
                "WHERE region=%s AND schedule_idn=%s",
                (region, idn),
            )
        conn.commit()
        return {"status": "OK"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.error("schedule ack error: %s", e)
        raise HTTPException(status_code=500, detail="일정 확인 실패")
    finally:
        conn.close()


@router.put("/{idn}")
def update_schedule(idn: int, body: ScheduleUpdate, region: str = "R01"):
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            _check_owner(cur, region, idn, body.user_id)
            sets, params = [], []
            if body.title is not None:
                sets.append("title = %s")
                params.append(body.title)
            if body.content is not None:
                sets.append("content = %s")
                params.append(body.content)
            if body.alarm_at is not None:
                # 시각 변경 시 알림 상태 리셋 — 새 시각에 다시 팝업
                sets.append("alarm_at = %s::timestamptz")
                params.append(body.alarm_at)
                sets.append("notified_at = NULL")
                sets.append("acked_at = NULL")
            if not sets:
                return {"status": "OK"}
            cur.execute(
                f"UPDATE tb_user_schedule SET {', '.join(sets)} "
                f"WHERE region=%s AND schedule_idn=%s",
                params + [region, idn],
            )
        conn.commit()
        return {"status": "OK"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.error("schedule update error: %s", e)
        raise HTTPException(status_code=500, detail="일정 수정 실패")
    finally:
        conn.close()


@router.delete("/{idn}")
def delete_schedule(idn: int, user_id: str, region: str = "R01"):
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            _check_owner(cur, region, idn, user_id)
            cur.execute(
                "UPDATE tb_user_schedule SET use_yn='N' "
                "WHERE region=%s AND schedule_idn=%s",
                (region, idn),
            )
        conn.commit()
        return {"status": "OK"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.error("schedule delete error: %s", e)
        raise HTTPException(status_code=500, detail="일정 삭제 실패")
    finally:
        conn.close()
