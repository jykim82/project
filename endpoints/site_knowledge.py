"""
현장 지식 카드 API — docs/site-knowledge-spec.md (로드맵 B P1)

- GET    /site-knowledge            — 목록 (status·k_type 필터)
- POST   /site-knowledge            — 등록
- PUT    /site-knowledge/{id}       — 수정 (revision 선기록)
- DELETE /site-knowledge/{id}       — 삭제 (revision 선기록 — 이력은 남는다)
- GET    /site-knowledge/match      — 경보 문맥에 지금 보여줄 카드
- POST   /site-knowledge/preview    — 영향 미리보기 (지난 30일 매칭 경보)

P1 은 조회·기록 계층만이다 — 알람 생성·해제 어디에도 개입하지 않는다.
억제 반영은 통합 억제 로그 설계 후 P2 (roadmap 부록 A.5), 그때도 안전
임계(HH/LL)는 카드로 끌 수 없다 (부록 B.2 — 안전 신호 불가침).

ai_server.py에서 분리된 모듈 — init()으로 DB 커넥션 함수를 주입받아 사용.
"""

import json
import logging

from fastapi import APIRouter, Query, Request

logger = logging.getLogger("slm")

router = APIRouter(tags=["site-knowledge"])

# ai_server.py에서 주입
_get_db_connection = None


def init(get_db_connection_fn):
    """ai_server.py에서 DB 커넥션 팩토리 함수를 주입받는다."""
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


_VALID_TYPES = {"periodic", "trait", "exception", "procedure"}
_VALID_STATUS = {"draft", "active", "retired"}
_TARGET_KEYS = ("sitenames", "facilitytypes", "tagsns", "alarm_categories")


def _validate_card(body: dict) -> str | None:
    """등록·수정 공통 검증. 문제 없으면 None, 있으면 메시지."""
    if body.get("k_type") not in _VALID_TYPES:
        return f"k_type 은 {sorted(_VALID_TYPES)} 중 하나"
    if not (body.get("title") or "").strip():
        return "title 필수"
    status = body.get("status", "draft")
    if status not in _VALID_STATUS:
        return f"status 는 {sorted(_VALID_STATUS)} 중 하나"
    targets = body.get("target_refs") or {}
    # 전역 카드 금지 — 무관 지점 지식 혼입이 §3.B 가 지목한 오답 원인이다
    if not any(targets.get(k) for k in _TARGET_KEYS):
        return "target_refs 에 최소 1개 대상(현장/시설유형/태그/분류) 필요"
    return None


def _row_to_card(r) -> dict:
    return {
        "knowledge_id": r[0],
        "k_type": r[1],
        "title": r[2],
        "target_refs": r[3] or {},
        "conditions": r[4] or {},
        "description": r[5] or "",
        "valid_from": r[6].strftime("%Y-%m-%d") if r[6] else None,
        "valid_until": r[7].strftime("%Y-%m-%d") if r[7] else None,
        "status": r[8],
        "created_by": r[9],
        "created_at": r[10].strftime("%Y-%m-%d %H:%M") if r[10] else "",
        "updated_by": r[11],
        "updated_at": r[12].strftime("%Y-%m-%d %H:%M") if r[12] else "",
    }


_SELECT_COLS = """
    knowledge_id, k_type, title, target_refs, conditions, description,
    valid_from, valid_until, status, created_by, created_at,
    updated_by, updated_at
"""


@router.get("/site-knowledge")
def list_knowledge(
    region: str = Query("R01"),
    status: str = Query(""),
    k_type: str = Query(""),
):
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        where = ["region = %s"]
        params: list = [region]
        if status in _VALID_STATUS:
            where.append("status = %s")
            params.append(status)
        if k_type in _VALID_TYPES:
            where.append("k_type = %s")
            params.append(k_type)
        cur.execute(
            f"SELECT {_SELECT_COLS} FROM tb_site_knowledge "
            f"WHERE {' AND '.join(where)} ORDER BY updated_at DESC",
            params,
        )
        items = [_row_to_card(r) for r in cur.fetchall()]
        cur.close()
        return {"status": "OK", "items": items}
    except Exception as e:
        logger.error(f"지식 카드 목록 실패: {e}")
        return {"status": "ERROR", "message": str(e), "items": []}
    finally:
        if conn:
            conn.close()


@router.post("/site-knowledge")
async def create_knowledge(request: Request):
    conn = None
    try:
        body = await request.json()
        err = _validate_card(body)
        if err:
            return {"status": "error", "message": err}
        region = body.get("region") or "R01"
        user_id = (body.get("user_id") or "").strip() or "unknown"

        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO tb_site_knowledge
                (region, k_type, title, target_refs, conditions, description,
                 valid_from, valid_until, status, created_by, updated_by)
            VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s,
                    %s::timestamptz, %s::timestamptz, %s, %s, %s)
            RETURNING knowledge_id
            """,
            [
                region, body["k_type"], body["title"].strip(),
                json.dumps(body.get("target_refs") or {}),
                json.dumps(body.get("conditions") or {}),
                body.get("description") or "",
                body.get("valid_from") or None,
                body.get("valid_until") or None,
                body.get("status", "draft"), user_id, user_id,
            ],
        )
        knowledge_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        return {"status": "OK", "knowledge_id": knowledge_id}
    except Exception as e:
        logger.error(f"지식 카드 등록 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


def _snapshot_revision(cur, region: str, knowledge_id: int,
                       action: str, user_id: str) -> bool:
    """변경 직전 카드 전체를 revision 으로 남긴다. 카드 없으면 False."""
    cur.execute(
        "SELECT row_to_json(t) FROM tb_site_knowledge t "
        "WHERE region = %s AND knowledge_id = %s",
        [region, knowledge_id],
    )
    row = cur.fetchone()
    if not row:
        return False
    cur.execute(
        """
        INSERT INTO tb_site_knowledge_revision
            (region, knowledge_id, action, snapshot, changed_by)
        VALUES (%s, %s, %s, %s::jsonb, %s)
        """,
        [region, knowledge_id, action, json.dumps(row[0], default=str), user_id],
    )
    return True


@router.put("/site-knowledge/{knowledge_id}")
async def update_knowledge(knowledge_id: int, request: Request):
    conn = None
    try:
        body = await request.json()
        err = _validate_card(body)
        if err:
            return {"status": "error", "message": err}
        region = body.get("region") or "R01"
        user_id = (body.get("user_id") or "").strip() or "unknown"

        conn = _get_db_connection()
        cur = conn.cursor()
        if not _snapshot_revision(cur, region, knowledge_id, "update", user_id):
            return {"status": "error", "message": "카드를 찾을 수 없습니다"}
        cur.execute(
            """
            UPDATE tb_site_knowledge
            SET k_type = %s, title = %s, target_refs = %s::jsonb,
                conditions = %s::jsonb, description = %s,
                valid_from = %s::timestamptz, valid_until = %s::timestamptz,
                status = %s, updated_by = %s, updated_at = now()
            WHERE region = %s AND knowledge_id = %s
            """,
            [
                body["k_type"], body["title"].strip(),
                json.dumps(body.get("target_refs") or {}),
                json.dumps(body.get("conditions") or {}),
                body.get("description") or "",
                body.get("valid_from") or None,
                body.get("valid_until") or None,
                body.get("status", "draft"), user_id,
                region, knowledge_id,
            ],
        )
        conn.commit()
        cur.close()
        return {"status": "OK"}
    except Exception as e:
        logger.error(f"지식 카드 수정 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.delete("/site-knowledge/{knowledge_id}")
async def delete_knowledge(knowledge_id: int, request: Request):
    conn = None
    try:
        body = await request.json() if await request.body() else {}
        region = body.get("region") or "R01"
        user_id = (body.get("user_id") or "").strip() or "unknown"

        conn = _get_db_connection()
        cur = conn.cursor()
        # 삭제도 revision 을 남긴다 — "있었는데 지워진" 지식도 감사 대상
        if not _snapshot_revision(cur, region, knowledge_id, "delete", user_id):
            return {"status": "error", "message": "카드를 찾을 수 없습니다"}
        cur.execute(
            "DELETE FROM tb_site_knowledge WHERE region = %s AND knowledge_id = %s",
            [region, knowledge_id],
        )
        conn.commit()
        cur.close()
        return {"status": "OK"}
    except Exception as e:
        logger.error(f"지식 카드 삭제 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# 매칭 — 카드가 이 문맥(현장·분류·시각)에 해당하는가
# ---------------------------------------------------------------------------
# SQL 로 target/유효기간을 거르고, 요일·시간대(conditions)는 파이썬에서
# 판정한다 — jsonb 배열 검사를 SQL 로 다 밀면 읽기 어려워지고, active 카드
# 수는 현장당 수십 장 수준이라 후단 필터 비용이 무시할 만하다.

def _match_conditions(cond: dict, at) -> bool:
    """conditions(요일·시간대·월)에 at 시각이 드는가. 빈 조건=상시."""
    if not cond:
        return True
    weekdays = cond.get("weekdays") or []
    if weekdays and at.isoweekday() not in weekdays:
        return False
    months = cond.get("months") or []
    if months and at.month not in months:
        return False
    t_from, t_to = cond.get("time_from"), cond.get("time_to")
    if t_from and t_to:
        hhmm = at.strftime("%H:%M")
        if t_from <= t_to:
            if not (t_from <= hhmm <= t_to):
                return False
        # 자정 걸침 (22:00~06:00)
        elif not (hhmm >= t_from or hhmm <= t_to):
            return False
    return True


def _match_targets(targets: dict, sitename: str, facilitytype: str,
                   tagsn: str, category: str) -> bool:
    """target_refs 매칭 — 지정 필드 간 AND, 배열 내 OR."""
    checks = [
        (targets.get("sitenames"), sitename),
        (targets.get("facilitytypes"), facilitytype),
        (targets.get("tagsns"), tagsn),
        (targets.get("alarm_categories"), category),
    ]
    for allowed, value in checks:
        if allowed and value not in allowed:
            return False
    return True


def find_matching_cards(conn, sitename: str, facilitytype: str = "",
                        tagsn: str = "", category: str = "",
                        at: str | None = None,
                        region: str = "R01") -> list[dict]:
    """문맥에 매칭되는 active 카드 — 엔드포인트·evidence_agent 공용 코어."""
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            SELECT {_SELECT_COLS}, now() FROM tb_site_knowledge
            WHERE region = %s AND status = 'active'
              AND (valid_from  IS NULL OR valid_from  <= COALESCE(%s::timestamptz, now()))
              AND (valid_until IS NULL OR valid_until >= COALESCE(%s::timestamptz, now()))
            """,
            [region, at or None, at or None],
        )
        rows = cur.fetchall()
        cur.execute("SELECT COALESCE(%s::timestamptz, now())", [at or None])
        at_ts = cur.fetchone()[0]
    finally:
        cur.close()

    items = []
    for r in rows:
        targets = r[3] or {}
        if not _match_targets(targets, sitename, facilitytype, tagsn, category):
            continue
        if not _match_conditions(r[4] or {}, at_ts):
            continue
        items.append(_row_to_card(r))
    return items


@router.get("/site-knowledge/match")
def match_knowledge(
    region: str = Query("R01"),
    sitename: str = Query(""),
    facilitytype: str = Query(""),
    tagsn: str = Query(""),
    category: str = Query(""),
    at: str = Query(""),  # 경보 발생 시각 "YYYY-MM-DD HH:MM:SS" — 없으면 now
):
    conn = None
    try:
        conn = _get_db_connection()
        items = find_matching_cards(
            conn, sitename, facilitytype, tagsn, category, at or None, region,
        )
        return {"status": "OK", "items": items}
    except Exception as e:
        logger.error(f"지식 카드 매칭 실패: {e}")
        return {"status": "ERROR", "message": str(e), "items": []}
    finally:
        if conn:
            conn.close()


@router.post("/site-knowledge/preview")
async def preview_knowledge(request: Request):
    """영향 미리보기 — 이 조건이면 지난 30일 어떤 경보에 표시됐을까.

    P1 은 억제가 없으므로 "억제 예정"이 아니라 "표시 대상"이다. 등록 전에
    조건 실수(요일 착오·대상 오타)를 눈으로 잡는 안전장치 (§3.B ①).
    """
    conn = None
    try:
        body = await request.json()
        targets = body.get("target_refs") or {}
        cond = body.get("conditions") or {}
        days = min(int(body.get("days") or 30), 90)
        if not any(targets.get(k) for k in _TARGET_KEYS):
            return {"status": "error", "message": "대상을 1개 이상 지정하세요"}

        conn = _get_db_connection()
        cur = conn.cursor()
        # E-056: tb_equipment_alarm_report 는 일반 테이블이라 하한 규칙
        # 대상은 아니지만, 기간을 못박아 스캔을 좁힌다
        cur.execute(
            """
            SELECT sitename, facilitytype, tagsn, alarm_category,
                   alarm_msg, alarm_start_time
            FROM tb_equipment_alarm_report
            WHERE alarm_start_time > now() - (%s || ' days')::interval
            """,
            [days],
        )
        rows = cur.fetchall()
        cur.close()

        matched = []
        for r in rows:
            if not _match_targets(targets, r[0] or "", r[1] or "",
                                  r[2] or "", r[3] or ""):
                continue
            if not _match_conditions(cond, r[5]):
                continue
            matched.append({
                "sitename": r[0] or "",
                "alarm_msg": r[4] or "",
                "alarm_start_time": r[5].strftime("%Y-%m-%d %H:%M"),
            })

        matched.sort(key=lambda m: m["alarm_start_time"], reverse=True)
        return {
            "status": "OK",
            "days": days,
            "total_alarms": len(rows),
            "matched_count": len(matched),
            "samples": matched[:20],
        }
    except Exception as e:
        logger.error(f"지식 카드 미리보기 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()
