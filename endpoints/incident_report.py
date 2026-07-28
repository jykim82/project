"""
상황보고 1보/2보 API — docs/incident-report-spec.md (로드맵 D P1)

- POST  /incident/draft        — 초안 자동 생성 (1보 또는 후속보)
- GET   /incident              — 목록 (사고 체인 그룹)
- GET   /incident/{id}         — 단건
- PATCH /incident/{id}         — 섹션 편집·확정
- DELETE /incident/{id}        — 초안 삭제 (final 은 불가)

자동 채움은 **생성 시점 1회**다 — 이후엔 담당자 편집이 정본이고, 시스템이
덮어쓰지 않는다 (공문은 사람이 책임지는 문서다. 완전 자동 발송 없음).

LLM 서술을 쓰지 않는 것도 의도다 — 사고 시점의 즉시성이 우선이고,
데이터 기반 템플릿이면 초안으로 충분하다 (교대 인수인계와 같은 판단).

ai_server.py에서 분리된 모듈 — init()으로 DB 커넥션 함수를 주입받아 사용.
"""

import logging

from fastapi import APIRouter, Query, Request

logger = logging.getLogger("slm")

router = APIRouter(tags=["incident-report"])

# ai_server.py에서 주입
_get_db_connection = None


def init(get_db_connection_fn):
    """ai_server.py에서 DB 커넥션 팩토리 함수를 주입받는다."""
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


_SECTION_KEYS = {"overview", "situation", "actions", "impact", "outlook", "contact"}

# 1보 자동 수집 창 — 발생 시각 앞 1시간의 경보까지 "전조"로 포함
_PRE_WINDOW_MIN = 60


def _fetch_context(cur, sitename: str, since, until=None) -> dict:
    """자동 채움 재료 — 해당 시설의 경보·조치·현재 상태.

    since/until 은 timestamptz. until=None 이면 now().
    """
    until_sql = "now()" if until is None else "%s::timestamptz"
    until_params = [] if until is None else [until]

    # 경보 (반복은 (메시지) 로 접어 요약 — 잠식 방지 원칙)
    cur.execute(
        f"""
        SELECT coalesce(alarm_msg,''), count(*),
               TO_CHAR(min(alarm_start_time),'MM-DD HH24:MI'),
               TO_CHAR(max(alarm_start_time),'MM-DD HH24:MI'),
               coalesce(min(alarm_severity),'')
        FROM tb_equipment_alarm_report
        WHERE sitename = %s
          AND alarm_start_time >= %s::timestamptz
          AND alarm_start_time <= {until_sql}
        GROUP BY alarm_msg ORDER BY min(alarm_start_time)
        LIMIT 20
        """,
        [sitename, since, *until_params],
    )
    alarms = [
        {"msg": r[0], "count": int(r[1]), "first": r[2], "last": r[3], "severity": r[4]}
        for r in cur.fetchall()
    ]

    # 조치 이력 (tb_task_master)
    cur.execute(
        f"""
        SELECT TO_CHAR(task_start_time,'MM-DD HH24:MI'), coalesce(task_category,''),
               coalesce(equipmenttype,''), coalesce(task_content,''),
               coalesce(status,''), coalesce(recorded_by,'')
        FROM tb_task_master
        WHERE sitename = %s
          AND task_start_time >= %s::timestamptz
          AND task_start_time <= {until_sql}
        ORDER BY task_start_time LIMIT 20
        """,
        [sitename, since, *until_params],
    )
    actions = [
        {"at": r[0], "category": r[1], "equipmenttype": r[2],
         "content": r[3], "status": r[4], "by": r[5]}
        for r in cur.fetchall()
    ]

    # 현재 상태 — 진행중 경보
    cur.execute(
        """
        SELECT count(*),
               count(*) FILTER (WHERE coalesce(alarm_severity,'') = '경고')
        FROM tb_equipment_alarm_report
        WHERE sitename = %s AND alarm_status = '진행중'
        """,
        [sitename],
    )
    ongoing, critical = cur.fetchone()
    return {
        "alarms": alarms, "actions": actions,
        "ongoing": int(ongoing or 0), "critical": int(critical or 0),
    }


def _compose_sections(ctx: dict, bulletin_no: int, prev_sections: dict | None) -> dict:
    """수집 재료 → 서술 섹션 초안. 수치·사실만 쓴다 (추정 문구 금지)."""
    alarm_lines = [
        f"- {a['first']} {a['msg']}"
        + (f" (반복 {a['count']}회, ~{a['last']})" if a["count"] > 1 else "")
        for a in ctx["alarms"]
    ]
    action_lines = [
        f"- {t['at']} [{t['category']}{'/' + t['equipmenttype'] if t['equipmenttype'] else ''}] "
        f"{t['content']}" + (f" ({t['status']})" if t["status"] else "")
        for t in ctx["actions"]
    ]
    situation = "\n".join(alarm_lines) if alarm_lines else "(수집된 경보 없음)"
    actions = "\n".join(action_lines) if action_lines else "(기록된 조치 없음)"
    outlook_now = (
        f"현재 진행중 경보 {ctx['ongoing']}건"
        + (f" (경고 {ctx['critical']}건)" if ctx["critical"] else "")
        + ". 추가 조치·복구 계획 기재 요망."
    )

    if bulletin_no == 1 or not prev_sections:
        return {
            "overview": "",  # 사고 개요는 담당자 서술 — 시스템이 추정하지 않는다
            "situation": situation,
            "actions": actions,
            "impact": "(영향 범위·세대수는 담당자 확인 후 기입)",
            "outlook": outlook_now,
            "contact": "",
        }
    # 후속보: 개요·영향은 직전 보에서 이월, 경과·조치는 이번 구간 신규
    return {
        "overview": prev_sections.get("overview", ""),
        "situation": situation,
        "actions": actions,
        "impact": prev_sections.get("impact", ""),
        "outlook": outlook_now,
        "contact": prev_sections.get("contact", ""),
    }


@router.post("/incident/draft")
async def create_incident_draft(request: Request):
    """초안 자동 생성.

    Body:
      1보:   { sitename, facilitytype?, occurred_at?, title?, user_id, region? }
             occurred_at 생략 시 그 시설의 최근 24h 첫 경보 시각, 없으면 now.
      후속보: { parent_id, user_id, region? } — 직전 보 이후 구간만 수집.
    """
    conn = None
    try:
        body = await request.json()
        region = body.get("region") or "R01"
        user_id = (body.get("user_id") or "").strip()
        parent_id = body.get("parent_id")
        if not user_id:
            return {"status": "error", "message": "user_id 필수"}

        conn = _get_db_connection()
        cur = conn.cursor()

        if parent_id:
            cur.execute(
                """
                SELECT incident_id, bulletin_no, title, sitename, facilitytype,
                       occurred_at, sections, created_at
                FROM tb_incident_report
                WHERE region = %s AND incident_id = %s
                """,
                [region, parent_id],
            )
            prev = cur.fetchone()
            if not prev:
                return {"status": "error", "message": "직전 보를 찾을 수 없습니다"}
            (_, prev_no, title, sitename, facilitytype,
             occurred_at, prev_sections, prev_created) = prev
            bulletin_no = int(prev_no) + 1
            # 후속보는 직전 보 작성 시점 이후 구간만 수집 — 같은 내용 반복 방지
            ctx = _fetch_context(cur, sitename, prev_created)
        else:
            sitename = (body.get("sitename") or "").strip()
            if not sitename:
                return {"status": "error", "message": "sitename 필수"}
            facilitytype = (body.get("facilitytype") or "").strip()
            occurred_at = body.get("occurred_at")
            if not occurred_at:
                # 최근 24h 첫 경보 = 사고 시작 후보. 없으면 지금
                cur.execute(
                    """
                    SELECT min(alarm_start_time) FROM tb_equipment_alarm_report
                    WHERE sitename = %s AND alarm_start_time > now() - interval '24 hours'
                    """,
                    [sitename],
                )
                row = cur.fetchone()
                occurred_at = row[0] if row and row[0] else None
            if occurred_at:
                cur.execute("SELECT %s::timestamptz", [occurred_at])
            else:
                cur.execute("SELECT now()")
            occurred_at = cur.fetchone()[0]
            bulletin_no = 1
            prev_sections = None
            title = (body.get("title") or "").strip() or f"{sitename} 상황보고"
            cur.execute(
                f"SELECT %s::timestamptz - interval '{_PRE_WINDOW_MIN} minutes'",
                [occurred_at],
            )
            since = cur.fetchone()[0]
            ctx = _fetch_context(cur, sitename, since)

        sections = _compose_sections(ctx, bulletin_no, prev_sections)

        cur.execute(
            """
            INSERT INTO tb_incident_report
                (region, parent_id, bulletin_no, title, sitename, facilitytype,
                 occurred_at, sections, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            RETURNING incident_id
            """,
            [region, parent_id, bulletin_no, title, sitename, facilitytype,
             occurred_at, __import__("json").dumps(sections, ensure_ascii=False),
             user_id],
        )
        incident_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        return {"status": "OK", "incident_id": incident_id, "bulletin_no": bulletin_no}
    except Exception as e:
        logger.error(f"상황보고 초안 생성 실패: {e}")
        if conn:
            conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        if conn:
            conn.close()


def _row_to_dict(r) -> dict:
    return {
        "incident_id": r[0], "parent_id": r[1], "bulletin_no": r[2],
        "title": r[3], "sitename": r[4], "facilitytype": r[5],
        "occurred_at": r[6], "sections": r[7] or {}, "status": r[8],
        "created_by": r[9], "created_at": r[10], "finalized_at": r[11],
    }


_SELECT_COLS = """
    incident_id, parent_id, bulletin_no, title, sitename, facilitytype,
    TO_CHAR(occurred_at,'YYYY-MM-DD HH24:MI'), sections, status,
    created_by, TO_CHAR(created_at,'YYYY-MM-DD HH24:MI'),
    TO_CHAR(finalized_at,'YYYY-MM-DD HH24:MI')
"""


@router.get("/incident")
def list_incidents(region: str = Query("R01"), limit: int = Query(50, ge=1, le=200)):
    """목록 — 최신순. 체인 그룹핑은 프런트가 parent_id 로 한다."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute(
            f"SELECT {_SELECT_COLS} FROM tb_incident_report "
            "WHERE region = %s ORDER BY created_at DESC LIMIT %s",
            [region, limit],
        )
        items = [_row_to_dict(r) for r in cur.fetchall()]
        cur.close()
        return {"status": "OK", "items": items}
    except Exception as e:
        logger.error(f"상황보고 목록 실패: {e}")
        return {"status": "ERROR", "message": str(e), "items": []}
    finally:
        if conn:
            conn.close()


@router.get("/incident/{incident_id}")
def get_incident(incident_id: int, region: str = Query("R01")):
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute(
            f"SELECT {_SELECT_COLS} FROM tb_incident_report "
            "WHERE region = %s AND incident_id = %s",
            [region, incident_id],
        )
        row = cur.fetchone()
        cur.close()
        if not row:
            return {"status": "NOT_FOUND"}
        return {"status": "OK", "item": _row_to_dict(row)}
    except Exception as e:
        logger.error(f"상황보고 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.patch("/incident/{incident_id}")
async def patch_incident(incident_id: int, request: Request):
    """섹션 편집·확정. final 이후 편집 불가 (공문 확정본 보호)."""
    conn = None
    try:
        body = await request.json()
        region = body.get("region") or "R01"
        sections = body.get("sections")
        title = body.get("title")
        finalize = bool(body.get("finalize"))

        if sections is not None:
            bad = set(sections.keys()) - _SECTION_KEYS
            if bad:
                return {"status": "error", "message": f"알 수 없는 섹션: {bad}"}

        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT status FROM tb_incident_report WHERE region=%s AND incident_id=%s",
            [region, incident_id],
        )
        row = cur.fetchone()
        if not row:
            return {"status": "error", "message": "대상 없음"}
        if row[0] == "final":
            return {"status": "error", "message": "확정된 보고는 수정할 수 없습니다"}

        sets, params = ["updated_at = now()"], []
        if sections is not None:
            sets.append("sections = sections || %s::jsonb")
            params.append(__import__("json").dumps(sections, ensure_ascii=False))
        if title:
            sets.append("title = %s")
            params.append(title)
        if finalize:
            sets.append("status = 'final'")
            sets.append("finalized_at = now()")
        cur.execute(
            f"UPDATE tb_incident_report SET {', '.join(sets)} "
            "WHERE region = %s AND incident_id = %s",
            [*params, region, incident_id],
        )
        conn.commit()
        cur.close()
        return {"status": "OK"}
    except Exception as e:
        logger.error(f"상황보고 수정 실패: {e}")
        if conn:
            conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.delete("/incident/{incident_id}")
def delete_incident(incident_id: int, region: str = Query("R01")):
    """초안만 삭제 가능. 후속보가 달린 보고는 삭제 불가 (체인 보호)."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM tb_incident_report WHERE region=%s AND parent_id=%s LIMIT 1",
            [region, incident_id],
        )
        if cur.fetchone():
            return {"status": "error", "message": "후속보가 있는 보고는 삭제할 수 없습니다"}
        cur.execute(
            "DELETE FROM tb_incident_report "
            "WHERE region=%s AND incident_id=%s AND status='draft'",
            [region, incident_id],
        )
        deleted = cur.rowcount
        conn.commit()
        cur.close()
        if deleted == 0:
            return {"status": "error", "message": "초안만 삭제할 수 있습니다"}
        return {"status": "OK"}
    except Exception as e:
        logger.error(f"상황보고 삭제 실패: {e}")
        if conn:
            conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        if conn:
            conn.close()
