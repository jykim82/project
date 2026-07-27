"""
설비 점검 도래 API — docs/inspection-cycle-spec.md

- GET /inspection/due — 점검 주기 도래·경과 설비 목록

주기 마스터(tb_inspection_cycle)에 유형이 정의된 설비만 대상으로,
마지막 점검 기록(tb_task_master 점검·정비·청소)에서 다음 도래를 계산한다.
조회 시점 계산 — cron·배치 없음 (설비 295대 규모, 집계 SQL 한 번이면 된다).

기록이 없는 설비는 'never' 로 그대로 보여준다 — 숨기면 "점검이 안 도는
설비"라는 신호를 잃는다.

ai_server.py에서 분리된 모듈 — init()으로 DB 커넥션 함수를 주입받아 사용.
"""

import logging

from fastapi import APIRouter, Query

logger = logging.getLogger("slm")

router = APIRouter(tags=["inspection-due"])

# ai_server.py에서 주입
_get_db_connection = None


def init(get_db_connection_fn):
    """ai_server.py에서 DB 커넥션 팩토리 함수를 주입받는다."""
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


# 도래 임박 판정 창 (일). 예정일까지 이 이하로 남으면 due_soon.
_DUE_SOON_DAYS = 7


@router.get("/inspection/due")
def get_inspection_due(
    region: str = Query("R01"),
    include_ok: bool = Query(False),
):
    """점검 도래 설비 목록.

    동기 def — 블로킹 psycopg2 (memory/feedback_fastapi_blocking_endpoint).
    """
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()

        # 점검 매칭: equipment_id 직접 매칭이 정확하지만 채팅 기록은
        # equipment_id 없이 sitename+equipmenttype 만 있는 경우가 있어
        # 둘 다 인정한다 (OR). 과매칭 위험보다 "점검했는데 경과로 뜨는"
        # 오탐이 더 해롭다.
        cur.execute(
            f"""
            SELECT e.equipment_id, e.sitename, e.facilitytype, e.equipmenttype,
                   c.cycle_days,
                   TO_CHAR(t.last_at, 'YYYY-MM-DD') AS last_at,
                   TO_CHAR(t.last_at + (c.cycle_days || ' days')::interval,
                           'YYYY-MM-DD') AS due_at,
                   CASE
                     WHEN t.last_at IS NULL THEN 'never'
                     WHEN t.last_at + (c.cycle_days || ' days')::interval < now()
                       THEN 'overdue'
                     WHEN t.last_at + (c.cycle_days || ' days')::interval
                          < now() + interval '{_DUE_SOON_DAYS} days'
                       THEN 'due_soon'
                     ELSE 'ok'
                   END AS state,
                   GREATEST(0, EXTRACT(day FROM
                     now() - (t.last_at + (c.cycle_days || ' days')::interval)
                   ))::int AS overdue_days
            FROM tb_equipment_info e
            JOIN tb_inspection_cycle c
              ON c.region = %s AND c.equipmenttype = e.equipmenttype
            LEFT JOIN LATERAL (
                SELECT max(tm.task_start_time) AS last_at
                FROM tb_task_master tm
                WHERE tm.task_category IN ('점검', '정비', '청소')
                  AND (tm.equipment_id = e.equipment_id
                       OR (tm.sitename = e.sitename
                           AND tm.equipmenttype = e.equipmenttype))
            ) t ON true
            ORDER BY
              CASE
                WHEN t.last_at IS NULL THEN 2
                WHEN t.last_at + (c.cycle_days || ' days')::interval < now() THEN 0
                WHEN t.last_at + (c.cycle_days || ' days')::interval
                     < now() + interval '{_DUE_SOON_DAYS} days' THEN 1
                ELSE 3
              END,
              9 DESC,
              e.sitename, e.equipmenttype
            """,
            (region,),
        )
        rows = cur.fetchall()
        cur.close()

        items = [
            {
                "equipment_id": r[0], "sitename": r[1], "facilitytype": r[2],
                "equipmenttype": r[3], "cycle_days": int(r[4]),
                "last_at": r[5], "due_at": r[6],
                "state": r[7], "overdue_days": int(r[8] or 0),
            }
            for r in rows
            if include_ok or r[7] != "ok"
        ]

        counts = {"overdue": 0, "due_soon": 0, "never": 0, "ok": 0}
        for r in rows:
            counts[r[7]] = counts.get(r[7], 0) + 1

        return {
            "status": "OK",
            "due_soon_days": _DUE_SOON_DAYS,
            "counts": counts,
            "items": items,
        }

    except Exception as e:
        logger.error(f"점검 도래 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e), "items": []}
    finally:
        if conn:
            conn.close()
