"""
운영자 간 1:1 채팅 P1 — tb_user_chat_message / tb_user_chat_read
(docs/realtime-comm-spec.md §5.1)

REST + 짧은 폴링 방식 (HTTPS 혼합 콘텐츠·프록시 제약으로 WS 는 P3 에서 wss 도입).
room_id: 'dm:<a>|<b>' (user_id 정렬) 또는 'all' (전체 채널).
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger("slm")

router = APIRouter(prefix="/userchat", tags=["userchat"])

_get_db_connection = None

ALL_ROOM = "all"


def init(get_db_connection_fn):
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


def _get_conn():
    if _get_db_connection is None:
        raise RuntimeError("user_chat not initialized")
    return _get_db_connection()


def dm_room_id(a: str, b: str) -> str:
    """1:1 방 ID — 참여자 순서 무관 동일 방."""
    x, y = sorted([a, b])
    return f"dm:{x}|{y}"


def _room_members(room_id: str) -> Optional[list]:
    """dm 방 참여자. 전체 채널·비정형은 None."""
    if room_id.startswith("dm:") and "|" in room_id[3:]:
        return room_id[3:].split("|", 1)
    return None


def _check_access(room_id: str, user_id: str):
    """방 멤버십 검증 — dm 참여자 또는 전체 채널만 접근."""
    if room_id == ALL_ROOM:
        return
    members = _room_members(room_id)
    if members and user_id in members:
        return
    raise HTTPException(status_code=403, detail="접근할 수 없는 대화방입니다")


class SendBody(BaseModel):
    room_id: str = Field(..., max_length=120)
    sender_id: str = Field(..., max_length=50)
    content: str = Field(..., min_length=1, max_length=4000)


class ReadBody(BaseModel):
    room_id: str = Field(..., max_length=120)
    user_id: str = Field(..., max_length=50)
    last_read_idn: int = Field(..., ge=0)


@router.get("/users")
def list_users(user_id: str, region: str = "R01"):
    """대화 상대 목록 — 본인 제외 활성 사용자."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, COALESCE(user_nm, user_id) FROM tb_user "
                "WHERE region=%s AND use_yn='Y' AND user_id <> %s ORDER BY user_nm",
                (region, user_id),
            )
            return {
                "status": "OK",
                "data": [{"user_id": r[0], "user_nm": r[1]} for r in cur.fetchall()],
            }
    except Exception as e:
        logger.error("userchat users error: %s", e)
        raise HTTPException(status_code=500, detail="사용자 목록 조회 실패")
    finally:
        conn.close()


@router.get("/rooms")
def list_rooms(user_id: str, region: str = "R01"):
    """내 대화방 목록 — 전체 채널 + 참여 dm. 마지막 메시지·unread 포함."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH my_rooms AS (
                  SELECT DISTINCT room_id FROM tb_user_chat_message
                  WHERE region=%(region)s AND use_yn='Y'
                    AND (room_id=%(all)s
                         OR room_id LIKE 'dm:' || %(uid)s || '|%%'
                         OR room_id LIKE 'dm:%%|' || %(uid)s)
                  UNION SELECT %(all)s
                ),
                last_msg AS (
                  SELECT DISTINCT ON (m.room_id) m.room_id, m.msg_idn, m.content,
                         m.sender_id, m.created_at
                  FROM tb_user_chat_message m
                  JOIN my_rooms r ON r.room_id = m.room_id
                  WHERE m.region=%(region)s AND m.use_yn='Y'
                  ORDER BY m.room_id, m.msg_idn DESC
                )
                SELECT r.room_id, l.content, l.sender_id, l.created_at,
                       COALESCE((
                         SELECT count(*) FROM tb_user_chat_message u
                         WHERE u.region=%(region)s AND u.room_id=r.room_id AND u.use_yn='Y'
                           AND u.sender_id <> %(uid)s
                           AND u.msg_idn > COALESCE((
                             SELECT last_read_idn FROM tb_user_chat_read t
                             WHERE t.region=%(region)s AND t.room_id=r.room_id
                               AND t.user_id=%(uid)s), 0)
                       ), 0) AS unread
                FROM my_rooms r
                LEFT JOIN last_msg l ON l.room_id = r.room_id
                ORDER BY l.msg_idn DESC NULLS LAST
                """,
                {"region": region, "uid": user_id, "all": ALL_ROOM},
            )
            rows = cur.fetchall()
            # dm 상대 표시명 일괄 조회
            peer_ids = set()
            for r in rows:
                members = _room_members(r[0])
                if members:
                    peer_ids.add(members[0] if members[1] == user_id else members[1])
            names = {}
            if peer_ids:
                cur.execute(
                    "SELECT user_id, COALESCE(user_nm, user_id) FROM tb_user "
                    "WHERE region=%s AND user_id = ANY(%s)",
                    (region, list(peer_ids)),
                )
                names = dict(cur.fetchall())
            data = []
            for room_id, content, sender_id, created_at, unread in rows:
                members = _room_members(room_id)
                peer = None
                if members:
                    peer = members[0] if members[1] == user_id else members[1]
                data.append({
                    "room_id": room_id,
                    "label": "전체 채널" if room_id == ALL_ROOM else names.get(peer, peer),
                    "peer_id": peer,
                    "last_content": content,
                    "last_sender_id": sender_id,
                    "last_at": created_at.isoformat() if created_at else None,
                    "unread": unread,
                })
            return {"status": "OK", "data": data}
    except Exception as e:
        logger.error("userchat rooms error: %s", e)
        raise HTTPException(status_code=500, detail="대화방 목록 조회 실패")
    finally:
        conn.close()


@router.get("/messages")
def list_messages(
    room_id: str,
    user_id: str,
    after_idn: int = Query(0, ge=0, description="증분 폴링 — 이 idn 초과분만"),
    limit: int = Query(50, ge=1, le=200),
    region: str = "R01",
):
    """방 메시지 — after_idn=0 이면 최신 limit 건, 아니면 증분."""
    _check_access(room_id, user_id)
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            if after_idn > 0:
                cur.execute(
                    "SELECT m.msg_idn, m.sender_id, COALESCE(u.user_nm, m.sender_id), "
                    "       m.content, m.created_at "
                    "FROM tb_user_chat_message m "
                    "LEFT JOIN tb_user u ON u.region=m.region AND u.user_id=m.sender_id "
                    "WHERE m.region=%s AND m.room_id=%s AND m.use_yn='Y' AND m.msg_idn > %s "
                    "ORDER BY m.msg_idn LIMIT %s",
                    (region, room_id, after_idn, limit),
                )
                rows = cur.fetchall()
            else:
                cur.execute(
                    "SELECT * FROM ("
                    "  SELECT m.msg_idn, m.sender_id, COALESCE(u.user_nm, m.sender_id), "
                    "         m.content, m.created_at "
                    "  FROM tb_user_chat_message m "
                    "  LEFT JOIN tb_user u ON u.region=m.region AND u.user_id=m.sender_id "
                    "  WHERE m.region=%s AND m.room_id=%s AND m.use_yn='Y' "
                    "  ORDER BY m.msg_idn DESC LIMIT %s"
                    ") t ORDER BY msg_idn",
                    (region, room_id, limit),
                )
                rows = cur.fetchall()
            return {
                "status": "OK",
                "data": [
                    {
                        "msg_idn": r[0],
                        "sender_id": r[1],
                        "sender_nm": r[2],
                        "content": r[3],
                        "created_at": r[4].isoformat() if r[4] else None,
                    }
                    for r in rows
                ],
            }
    except Exception as e:
        logger.error("userchat messages error: %s", e)
        raise HTTPException(status_code=500, detail="메시지 조회 실패")
    finally:
        conn.close()


@router.post("/send")
def send_message(body: SendBody, region: str = "R01"):
    _check_access(body.room_id, body.sender_id)
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tb_user_chat_message (region, room_id, sender_id, content) "
                "VALUES (%s, %s, %s, %s) RETURNING msg_idn, created_at",
                (region, body.room_id, body.sender_id, body.content),
            )
            msg_idn, created_at = cur.fetchone()
            # 본인 읽음 위치 동기 갱신 — 자기 메시지가 unread 로 잡히지 않게
            cur.execute(
                "INSERT INTO tb_user_chat_read (region, room_id, user_id, last_read_idn) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (region, room_id, user_id) DO UPDATE "
                "SET last_read_idn = GREATEST(tb_user_chat_read.last_read_idn, EXCLUDED.last_read_idn), "
                "    updated_at = now()",
                (region, body.room_id, body.sender_id, msg_idn),
            )
        conn.commit()
        return {"status": "OK", "msg_idn": msg_idn, "created_at": created_at.isoformat()}
    except Exception as e:
        conn.rollback()
        logger.error("userchat send error: %s", e)
        raise HTTPException(status_code=500, detail="메시지 전송 실패")
    finally:
        conn.close()


@router.post("/read")
def mark_read(body: ReadBody, region: str = "R01"):
    _check_access(body.room_id, body.user_id)
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tb_user_chat_read (region, room_id, user_id, last_read_idn) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (region, room_id, user_id) DO UPDATE "
                "SET last_read_idn = GREATEST(tb_user_chat_read.last_read_idn, EXCLUDED.last_read_idn), "
                "    updated_at = now()",
                (region, body.room_id, body.user_id, body.last_read_idn),
            )
        conn.commit()
        return {"status": "OK"}
    except Exception as e:
        conn.rollback()
        logger.error("userchat read error: %s", e)
        raise HTTPException(status_code=500, detail="읽음 처리 실패")
    finally:
        conn.close()
