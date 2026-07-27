"""
교대 인수인계 브리핑 API — docs/shift-handover-spec.md

- GET /shift/handover — 직전(또는 현재) 근무 구간의 인계 브리핑

수운영은 3교대인데 인계 화면이 없어, 교대할 때 알람·장애·메모·일정을 각각
열어보고 구두로 넘긴다. 데이터는 이미 전부 DB 에 있고 모으는 화면만 없다.

알람·작업·메모·일정을 가로지르므로 기존 모듈 어디에도 속하지 않아 별도 모듈.
ai_server.py에서 분리된 모듈 — init()으로 DB 커넥션 함수를 주입받아 사용.
"""

import logging
from datetime import datetime, time, timedelta

from fastapi import APIRouter, Query

logger = logging.getLogger("slm")

router = APIRouter(tags=["shift-handover"])

# ai_server.py에서 주입
_get_db_connection = None


def init(get_db_connection_fn):
    """ai_server.py에서 DB 커넥션 팩토리 함수를 주입받는다."""
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


# ---------------------------------------------------------------------------
# 근무 구간 계산
# ---------------------------------------------------------------------------

# 교대 시각은 고객마다 다르므로 SITE_SETTING 으로 분리한다(하드코딩 금지).
# 값은 "하루를 자르는 경계 시각" 목록이며 N 개면 N 교대.
_SHIFT_SETTING_KEY = "SHIFT_BOUNDARIES"
_DEFAULT_BOUNDARIES = "08:00,16:00,00:00"

# 목록 응답 상한. 인계 화면은 "읽고 넘기는" 것이라 전량을 쏟으면 안 읽힌다.
_MAX_ITEMS = 30
_REPEAT_TOP = 3


def _parse_boundaries(raw: str | None) -> list[time]:
    """"08:00,16:00,00:00" → [time(0,0), time(8,0), time(16,0)] (정렬).

    파싱 실패·빈 값이면 빈 목록 — 호출자가 24시간 단일 근무로 처리한다.
    교대 개념이 없는 현장도 화면은 동작해야 한다.
    """
    if not raw:
        return []
    out: list[time] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            hh, mm = part.split(":")
            out.append(time(int(hh), int(mm)))
        except (ValueError, TypeError):
            logger.warning("SHIFT_BOUNDARIES 파싱 실패: %r", part)
            return []
    return sorted(set(out))


def _shift_window(
    now: datetime, boundaries: list[time], previous: bool
) -> tuple[datetime, datetime]:
    """now 가 속한(또는 직전) 근무 구간의 [시작, 끝) 을 돌려준다.

    경계가 없으면 24시간 단일 구간으로 본다.
    자정을 넘는 구간(16:00~08:00)도 경계 목록을 하루 단위로 펼쳐 자연히 처리된다.
    """
    if not boundaries:
        end = now.replace(minute=0, second=0, microsecond=0)
        start = end - timedelta(days=1)
        return (start - timedelta(days=1), start) if previous else (start, end)

    # 어제~내일 경계를 시간순으로 펼쳐 now 를 감싸는 구간을 찾는다
    marks: list[datetime] = []
    for day_offset in (-1, 0, 1):
        day = (now + timedelta(days=day_offset)).date()
        marks.extend(datetime.combine(day, b, tzinfo=now.tzinfo) for b in boundaries)
    marks.sort()

    idx = 0
    for i in range(len(marks) - 1):
        if marks[i] <= now < marks[i + 1]:
            idx = i
            break
    else:
        # now 가 범위 밖 — 있을 수 없지만 방어적으로 마지막 구간
        idx = len(marks) - 2

    if previous:
        idx = max(0, idx - 1)
    return marks[idx], marks[idx + 1]


def _label(start: datetime, end: datetime) -> str:
    """"야간 (00:00~08:00)" 처럼 사람이 읽는 구간 이름."""
    h = start.hour
    if 5 <= h < 12:
        name = "주간"
    elif 12 <= h < 18:
        name = "오후"
    elif 18 <= h < 23:
        name = "야간"
    else:
        name = "심야"
    return f"{name} ({start:%H:%M}~{end:%H:%M})"


# ---------------------------------------------------------------------------
# 엔드포인트
# ---------------------------------------------------------------------------


@router.get("/shift/handover")
def get_shift_handover(
    shift: str = Query("prev", pattern="^(prev|current)$"),
    hours: int | None = Query(None, ge=1, le=168),
    region: str = Query("R01"),
):
    """교대 인수인계 브리핑.

    동기 def — 내부가 전부 블로킹 psycopg2 라 async 로 두면 event loop 를
    막는다(memory/feedback_fastapi_blocking_endpoint). FastAPI 가 threadpool
    에서 실행하게 둔다.
    """
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()

        # ── 구간 계산 ──
        cur.execute("SELECT now()")
        now = cur.fetchone()[0]

        if hours:
            end, start, mode = now, now - timedelta(hours=hours), "hours"
            label = f"최근 {hours}시간"
        else:
            cur.execute(
                "SELECT comm_val FROM tb_comm_code "
                "WHERE region=%s AND grp_cd='SITE_SETTING' AND comm_cd=%s",
                (region, _SHIFT_SETTING_KEY),
            )
            row = cur.fetchone()
            raw = row[0] if row and row[0] else _DEFAULT_BOUNDARIES
            start, end = _shift_window(
                now, _parse_boundaries(raw), previous=(shift == "prev")
            )
            mode, label = "shift", _label(start, end)

        w = (start, end)

        # ── 경보 ──
        cur.execute(
            """
            SELECT
              count(*) FILTER (WHERE alarm_start_time >= %s AND alarm_start_time < %s),
              count(*) FILTER (WHERE alarm_end_time  >= %s AND alarm_end_time  < %s),
              -- 이월: "구간 내"가 아니라 구간 끝 시점의 상태다. 인수자가 알아야
              -- 할 건 "지금 뭘 넘겨받는가"이지 지난 8시간의 통계가 아니다.
              count(*) FILTER (WHERE alarm_start_time < %s
                               AND (alarm_end_time IS NULL OR alarm_end_time >= %s)),
              count(*) FILTER (WHERE alarm_start_time < %s
                               AND (alarm_end_time IS NULL OR alarm_end_time >= %s)
                               AND coalesce(alarm_confirm_yn,'N') <> 'Y')
            FROM tb_equipment_alarm_report
            """,
            (*w, *w, end, end, end, end),
        )
        opened, resolved, carried, unconfirmed = (int(v or 0) for v in cur.fetchone())

        cur.execute(
            """
            SELECT coalesce(alarm_category,'미분류'), count(*)
            FROM tb_equipment_alarm_report
            WHERE alarm_start_time >= %s AND alarm_start_time < %s
            GROUP BY 1 ORDER BY 2 DESC
            """,
            w,
        )
        by_category = [{"category": r[0], "count": int(r[1])} for r in cur.fetchall()]

        # 목록은 (현장, 메시지) 로 접는다. 접지 않으면 채터링 한 건이 목록을
        # 통째로 먹는다 — 실측에서 상위 10건 중 9건이 죽동 탁도계였다.
        # 인계 화면은 30초 안에 읽는 것이라 이러면 기능이 성립하지 않는다.
        # (docs/alarm-chattering-spec.md 와 같은 처방, 원본 행은 그대로 둔다)
        cur.execute(
            f"""
            SELECT coalesce(sitename,''), coalesce(alarm_msg,''),
                   count(*) AS cnt,
                   TO_CHAR(max(alarm_start_time),'YYYY-MM-DD HH24:MI:SS'),
                   min(coalesce(facilitytype,'')), min(coalesce(alarm_category,'')),
                   -- 그룹 대표 심각도는 가장 높은 것 (한 건이라도 경고면 경고)
                   min(CASE coalesce(alarm_severity,'')
                         WHEN '경고' THEN 0 WHEN '주의' THEN 1 ELSE 2 END),
                   count(*) FILTER (WHERE coalesce(alarm_confirm_yn,'N') <> 'Y'),
                   min(tagsn)
            FROM tb_equipment_alarm_report
            WHERE alarm_start_time >= %s AND alarm_start_time < %s
            GROUP BY sitename, alarm_msg
            ORDER BY 7 ASC, max(alarm_start_time) DESC
            LIMIT {_MAX_ITEMS}
            """,
            w,
        )
        _SEV = {0: "경고", 1: "주의", 2: "정상"}
        alarm_items = [
            {
                "sitename": r[0], "alarm_msg": r[1],
                "count": int(r[2]),               # 구간 내 반복 횟수
                "last_at": r[3],                  # 마지막 발생
                "facilitytype": r[4], "alarm_category": r[5],
                "alarm_severity": _SEV.get(int(r[6]), ""),
                "unconfirmed": int(r[7]),         # 그룹 내 미확인 건수
                "tagsn": r[8],
            }
            for r in cur.fetchall()
        ]

        # ── 장애 조치 (tb_task_master, 고장보고) ──
        def _tasks(where: str, params: tuple) -> list[dict]:
            cur.execute(
                f"""
                SELECT task_id, coalesce(sitename,''), coalesce(facilitytype,''),
                       coalesce(equipmenttype,''), coalesce(fault_category,''),
                       coalesce(task_content,''), status,
                       TO_CHAR(task_start_time,'YYYY-MM-DD HH24:MI:SS'),
                       TO_CHAR(resolved_at,'YYYY-MM-DD HH24:MI:SS'),
                       coalesce(recorded_by,''), coalesce(resolved_by,'')
                FROM tb_task_master
                WHERE task_category = '고장보고' AND {where}
                ORDER BY task_start_time DESC
                LIMIT {_MAX_ITEMS}
                """,
                params,
            )
            return [
                {
                    "task_id": r[0], "sitename": r[1], "facilitytype": r[2],
                    "equipmenttype": r[3], "fault_category": r[4],
                    "task_content": r[5], "status": r[6],
                    "task_start_time": r[7], "resolved_at": r[8],
                    "recorded_by": r[9], "resolved_by": r[10],
                }
                for r in cur.fetchall()
            ]

        faults = {
            "opened": _tasks("task_start_time >= %s AND task_start_time < %s", w),
            "resolved": _tasks("resolved_at >= %s AND resolved_at < %s", w),
            "ongoing": _tasks(
                "status = '진행중' AND task_start_time < %s", (end,)
            ),
        }

        # ── 메모 ──
        cur.execute(
            f"""
            SELECT memo_idn, title, left(content, 300),
                   created_by, TO_CHAR(created_at,'YYYY-MM-DD HH24:MI:SS')
            FROM tb_memo
            WHERE region=%s AND use_yn='Y' AND created_at >= %s AND created_at < %s
            ORDER BY created_at DESC LIMIT {_MAX_ITEMS}
            """,
            (region, *w),
        )
        memos = [
            {"memo_idn": r[0], "title": r[1], "content": r[2],
             "created_by": r[3], "created_at": r[4]}
            for r in cur.fetchall()
        ]

        # ── 다음 근무 예정 일정 ──
        # 일주일치를 쏟으면 안 읽힌다. 인수자 근무 중에 할 일만 넘긴다.
        next_end = end + (end - start)
        cur.execute(
            f"""
            SELECT schedule_idn, title, left(content, 200),
                   TO_CHAR(alarm_at,'YYYY-MM-DD HH24:MI:SS'), created_by,
                   (acked_at IS NOT NULL)
            FROM tb_user_schedule
            WHERE region=%s AND use_yn='Y' AND alarm_at >= %s AND alarm_at < %s
            ORDER BY alarm_at LIMIT {_MAX_ITEMS}
            """,
            (region, end, next_end),
        )
        upcoming = [
            {"schedule_idn": r[0], "title": r[1], "content": r[2],
             "alarm_at": r[3], "created_by": r[4], "acked": r[5]}
            for r in cur.fetchall()
        ]

        # ── 반복 경보 상위 (구간이 아니라 최근 30일) ──
        # 반복은 한 근무 안에서 판단할 수 없다 — 몇 달째 반복되는 것이 대상이다.
        cur.execute(
            f"""
            SELECT sitename, alarm_msg, count(*) AS cnt,
                   round(avg(EXTRACT(epoch FROM (
                       coalesce(alarm_end_time, now()) - alarm_start_time)) / 60.0
                   )::numeric, 1)
            FROM tb_equipment_alarm_report
            WHERE alarm_start_time > now() - interval '30 days'
            GROUP BY sitename, alarm_msg
            HAVING count(*) >= 10
            ORDER BY cnt DESC LIMIT {_REPEAT_TOP}
            """
        )
        repeat_top = [
            {"sitename": r[0], "alarm_msg": r[1], "count": int(r[2]),
             "avg_duration_min": float(r[3] or 0)}
            for r in cur.fetchall()
        ]

        cur.close()
        return {
            "status": "OK",
            "window": {
                "start": start.strftime("%Y-%m-%d %H:%M:%S"),
                "end": end.strftime("%Y-%m-%d %H:%M:%S"),
                "label": label,
                "mode": mode,
            },
            "alarms": {
                "opened": opened, "resolved": resolved,
                "carried_over": carried, "unconfirmed": unconfirmed,
                "by_category": by_category, "items": alarm_items,
            },
            "faults": faults,
            "memos": memos,
            "upcoming": upcoming,
            "repeat_top": repeat_top,
        }

    except Exception as e:
        logger.error(f"교대 인수인계 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()
