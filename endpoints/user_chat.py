"""
운영자 간 1:1 채팅 P1 — tb_user_chat_message / tb_user_chat_read
(docs/realtime-comm-spec.md §5.1)

REST + 짧은 폴링 방식 (HTTPS 혼합 콘텐츠·프록시 제약으로 WS 는 P3 에서 wss 도입).
room_id: 'dm:<a>|<b>' (user_id 정렬) 또는 'all' (전체 채널).
"""

import logging
import os
import uuid
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

logger = logging.getLogger("slm")

router = APIRouter(prefix="/userchat", tags=["userchat"])

_get_db_connection = None

ALL_ROOM = "all"

# 첨부 저장소 — chat_attachments 와 동일 볼륨의 messenger 하위 (P2)
MESSENGER_DIR = os.environ.get(
    "MESSENGER_ATTACHMENT_DIR",
    os.path.join(
        os.path.dirname(os.environ.get("CHAT_ATTACHMENT_DIR", "/data/files/chat_attachments")),
        "messenger",
    ),
)

# 첨부 유형별 허용 확장자·크기 상한 (docs/realtime-comm-spec.md §5.2)
ATTACH_POLICY = {
    "image": {"exts": {".jpg", ".jpeg", ".png", ".webp", ".gif"}, "max": 10 * 1024 * 1024},
    "video": {"exts": {".mp4", ".webm", ".mov"}, "max": 100 * 1024 * 1024},
    "audio": {"exts": {".webm", ".m4a", ".mp3", ".wav", ".ogg"}, "max": 20 * 1024 * 1024},
}


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


def _group_id_of(room_id: str):
    """'grp:<id>' → int id. 비정형은 None."""
    if room_id.startswith("grp:") and room_id[4:].isdigit():
        return int(room_id[4:])
    return None


def _check_access(room_id: str, user_id: str, cur=None, region: str = "R01"):
    """방 멤버십 검증 — dm 참여자 / 전체 채널 / 그룹 멤버(cur 필요)."""
    if room_id == ALL_ROOM:
        return
    members = _room_members(room_id)
    if members and user_id in members:
        return
    gid = _group_id_of(room_id)
    if gid is not None and cur is not None:
        cur.execute(
            "SELECT 1 FROM tb_chat_group_member "
            "WHERE region=%s AND group_id=%s AND user_id=%s",
            (region, gid, user_id),
        )
        if cur.fetchone():
            return
    raise HTTPException(status_code=403, detail="접근할 수 없는 대화방입니다")


class SendBody(BaseModel):
    room_id: str = Field(..., max_length=120)
    sender_id: str = Field(..., max_length=50)
    content: str = Field("", max_length=4000)
    attach_url: Optional[str] = Field(None, max_length=500)
    attach_type: Optional[str] = Field(None, pattern="^(image|video|audio)$")
    attach_name: Optional[str] = Field(None, max_length=200)


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
                  UNION SELECT 'grp:' || g.group_id::text
                    FROM tb_chat_group_member g
                    WHERE g.region=%(region)s AND g.user_id=%(uid)s
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
            # dm 상대 표시명 + 그룹 제목 일괄 조회
            peer_ids = set()
            group_ids = set()
            for r in rows:
                members = _room_members(r[0])
                if members:
                    peer_ids.add(members[0] if members[1] == user_id else members[1])
                gid = _group_id_of(r[0])
                if gid is not None:
                    group_ids.add(gid)
            names = {}
            if peer_ids:
                cur.execute(
                    "SELECT user_id, COALESCE(user_nm, user_id) FROM tb_user "
                    "WHERE region=%s AND user_id = ANY(%s)",
                    (region, list(peer_ids)),
                )
                names = dict(cur.fetchall())
            group_titles = {}
            if group_ids:
                cur.execute(
                    "SELECT group_id, title FROM tb_chat_group "
                    "WHERE region=%s AND group_id = ANY(%s)",
                    (region, list(group_ids)),
                )
                group_titles = dict(cur.fetchall())
            data = []
            for room_id, content, sender_id, created_at, unread in rows:
                members = _room_members(room_id)
                peer = None
                if members:
                    peer = members[0] if members[1] == user_id else members[1]
                gid = _group_id_of(room_id)
                if room_id == ALL_ROOM:
                    label = "전체 채널"
                elif gid is not None:
                    label = group_titles.get(gid, f"그룹 {gid}")
                else:
                    label = names.get(peer, peer)
                data.append({
                    "room_id": room_id,
                    "label": label,
                    "peer_id": peer,
                    "is_group": gid is not None,
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
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            _check_access(room_id, user_id, cur, region)
            if after_idn > 0:
                cur.execute(
                    "SELECT m.msg_idn, m.sender_id, COALESCE(u.user_nm, m.sender_id), "
                    "       m.content, m.created_at, m.attach_url, m.attach_type, m.attach_name "
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
                    "         m.content, m.created_at, m.attach_url, m.attach_type, m.attach_name "
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
                        "attach_url": r[5],
                        "attach_type": r[6],
                        "attach_name": r[7],
                    }
                    for r in rows
                ],
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("userchat messages error: %s", e)
        raise HTTPException(status_code=500, detail="메시지 조회 실패")
    finally:
        conn.close()


@router.post("/send")
def send_message(body: SendBody, region: str = "R01"):
    if not body.content.strip() and not body.attach_url:
        raise HTTPException(status_code=400, detail="내용 또는 첨부가 필요합니다")
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            _check_access(body.room_id, body.sender_id, cur, region)
            cur.execute(
                "INSERT INTO tb_user_chat_message "
                "(region, room_id, sender_id, content, attach_url, attach_type, attach_name) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING msg_idn, created_at",
                (region, body.room_id, body.sender_id, body.content,
                 body.attach_url, body.attach_type, body.attach_name),
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
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.error("userchat send error: %s", e)
        raise HTTPException(status_code=500, detail="메시지 전송 실패")
    finally:
        conn.close()


@router.post("/read")
def mark_read(body: ReadBody, region: str = "R01"):
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            _check_access(body.room_id, body.user_id, cur, region)
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
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.error("userchat read error: %s", e)
        raise HTTPException(status_code=500, detail="읽음 처리 실패")
    finally:
        conn.close()


@router.post("/upload")
async def upload_attachment(
    user_id: str,
    attach_type: str = Query(..., pattern="^(image|video|audio)$"),
    file: UploadFile = File(...),
):
    """메신저 첨부 업로드 — 유형별 확장자·크기 검증 후 URL 반환.

    반환 URL 을 /userchat/send 의 attach_url 로 전달해 메시지와 함께 영속.
    """
    policy = ATTACH_POLICY[attach_type]
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in policy["exts"]:
        raise HTTPException(400, f"지원하지 않는 {attach_type} 형식: {ext or '(없음)'}")
    content = await file.read()
    if not content:
        raise HTTPException(400, "빈 파일")
    if len(content) > policy["max"]:
        raise HTTPException(413, f"파일 크기 상한 초과 ({policy['max'] // (1024 * 1024)}MB)")

    os.makedirs(MESSENGER_DIR, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{ext}"
    with open(os.path.join(MESSENGER_DIR, stored_name), "wb") as f:
        f.write(content)
    logger.info("userchat upload: user=%s type=%s size=%d", user_id, attach_type, len(content))
    return {
        "status": "OK",
        "attach_url": f"/api/files/messenger/{stored_name}",
        "attach_type": attach_type,
        "attach_name": file.filename or stored_name,
    }


class GroupCreateBody(BaseModel):
    creator_id: str = Field(..., max_length=50)
    member_ids: list[str] = Field(..., min_length=1, max_length=20)
    title: Optional[str] = Field(None, max_length=100)


@router.post("/group/create")
def create_group(body: GroupCreateBody, region: str = "R01"):
    """그룹 대화방 생성 — 제목 미지정 시 참가자 이름으로 자동 구성."""
    members = [m for m in dict.fromkeys(body.member_ids) if m != body.creator_id]
    if not members:
        raise HTTPException(status_code=400, detail="초대할 상대를 선택하세요")
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            all_ids = [body.creator_id] + members
            title = (body.title or "").strip()
            if not title:
                cur.execute(
                    "SELECT COALESCE(user_nm, user_id) FROM tb_user "
                    "WHERE region=%s AND user_id = ANY(%s) ORDER BY user_nm",
                    (region, all_ids),
                )
                title = ", ".join(r[0] for r in cur.fetchall())[:95] or "그룹 대화"
            cur.execute(
                "INSERT INTO tb_chat_group (region, title, created_by) "
                "VALUES (%s, %s, %s) RETURNING group_id",
                (region, title, body.creator_id),
            )
            gid = cur.fetchone()[0]
            for uid in all_ids:
                cur.execute(
                    "INSERT INTO tb_chat_group_member (region, group_id, user_id) "
                    "VALUES (%s, %s, %s)",
                    (region, gid, uid),
                )
        conn.commit()
        logger.info("chat group create: by=%s members=%s gid=%s", body.creator_id, members, gid)
        return {"status": "OK", "room_id": f"grp:{gid}", "title": title}
    except Exception as e:
        conn.rollback()
        logger.error("chat group create error: %s", e)
        raise HTTPException(status_code=500, detail="그룹 생성 실패")
    finally:
        conn.close()


@router.get("/group/members")
def group_members(room_id: str, user_id: str, region: str = "R01"):
    """그룹 참여자 목록 — 스레드 헤더 표시용."""
    gid = _group_id_of(room_id)
    if gid is None:
        raise HTTPException(status_code=400, detail="그룹 대화방이 아닙니다")
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            _check_access(room_id, user_id, cur, region)
            cur.execute(
                "SELECT m.user_id, COALESCE(u.user_nm, m.user_id) "
                "FROM tb_chat_group_member m "
                "LEFT JOIN tb_user u ON u.region=m.region AND u.user_id=m.user_id "
                "WHERE m.region=%s AND m.group_id=%s ORDER BY u.user_nm",
                (region, gid),
            )
            return {
                "status": "OK",
                "members": [{"user_id": r[0], "user_nm": r[1]} for r in cur.fetchall()],
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("chat group members error: %s", e)
        raise HTTPException(status_code=500, detail="그룹 멤버 조회 실패")
    finally:
        conn.close()
