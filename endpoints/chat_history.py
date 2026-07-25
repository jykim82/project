"""
AI 채팅 대화 목록·메시지 서버 영속화 (docs/chat-history-server-spec.md)

localStorage 기반이던 대화 그룹/메시지를 계정(DB) 기반으로 전환.
- tb_ai_chat_group  : 그룹 목록 (소프트 삭제 del_yn, 드래그 정렬 sort_order)
- tb_ai_chat_message: 메시지 — user/bot 페이로드 jsonb 통짜 저장
  (카드 유형이 늘어나는 개방 구조라 컬럼 정규화 대신 jsonb — 렌더링 경로 불변)

ai_server 의 메모리 세션(session_manager — 인텐트 문맥)과는 별개 축.
"""

import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger("slm")

router = APIRouter(prefix="/chat/history", tags=["chat-history"])

_get_db_connection = None

MAX_IMPORT_GROUPS = 200
MAX_IMPORT_MESSAGES_PER_GROUP = 500


def init(get_db_connection_fn):
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


def _get_conn():
    if _get_db_connection is None:
        raise RuntimeError("chat_history not initialized")
    return _get_db_connection()


# ── 모델 ──────────────────────────────────────────────────────────────


class GroupCreateBody(BaseModel):
    region: str
    user_id: str
    group_id: Optional[str] = None  # 프런트 생성 id 수용 (오프라인 낙관 생성)
    group_title: str = "새 대화"


class GroupPatchBody(BaseModel):
    region: str
    group_title: str


class ReorderBody(BaseModel):
    region: str
    user_id: str
    ordered_ids: list[str] = Field(default_factory=list)


class MessagePutBody(BaseModel):
    region: str
    user: dict
    bot: dict


class ImportGroup(BaseModel):
    group_id: str
    group_title: str = "새 대화"
    last_at: Optional[str] = None
    messages: list[dict] = Field(default_factory=list)


class ImportBody(BaseModel):
    region: str
    user_id: str
    groups: list[ImportGroup] = Field(default_factory=list)


# ── 그룹 ──────────────────────────────────────────────────────────────


@router.get("/groups")
def list_groups(region: str = Query(...), user_id: str = Query(...)):
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT group_id, region, user_id, group_title,
                       to_char(last_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
                       del_yn
                FROM tb_ai_chat_group
                WHERE region = %s AND user_id = %s AND del_yn = 'N'
                ORDER BY sort_order ASC, last_at DESC
                """,
                (region, user_id),
            )
            groups = [
                {
                    "group_id": r[0], "region": r[1], "user_id": r[2],
                    "group_title": r[3], "last_at": r[4], "del_yn": r[5],
                }
                for r in cur.fetchall()
            ]
        return {"status": "OK", "groups": groups}
    finally:
        conn.close()


@router.post("/groups")
def create_group(body: GroupCreateBody):
    group_id = body.group_id or f"g_{uuid.uuid4().hex[:16]}"
    if len(group_id) > 40:
        raise HTTPException(400, "group_id too long")
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            # 낙관 생성 재전송에 안전하도록 upsert
            cur.execute(
                """
                INSERT INTO tb_ai_chat_group (region, group_id, user_id, group_title)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (region, group_id) DO UPDATE
                    SET last_at = now(), del_yn = 'N'
                """,
                (body.region, group_id, body.user_id, body.group_title[:200]),
            )
        conn.commit()
        return {"status": "OK", "group_id": group_id}
    finally:
        conn.close()


@router.patch("/groups/{group_id}")
def rename_group(group_id: str, body: GroupPatchBody):
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE tb_ai_chat_group
                SET group_title = %s, last_at = now()
                WHERE region = %s AND group_id = %s
                """,
                (body.group_title[:200], body.region, group_id),
            )
            updated = cur.rowcount
        conn.commit()
        if updated == 0:
            raise HTTPException(404, "group not found")
        return {"status": "OK"}
    finally:
        conn.close()


@router.delete("/groups/{group_id}")
def delete_group(group_id: str, region: str = Query(...)):
    """소프트 삭제 — 메시지는 보존 (감사·복구 여지, 물리 삭제는 P2 정책)."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tb_ai_chat_group SET del_yn = 'Y' WHERE region = %s AND group_id = %s",
                (region, group_id),
            )
        conn.commit()
        return {"status": "OK"}
    finally:
        conn.close()


@router.put("/groups/reorder")
def reorder_groups(body: ReorderBody):
    """드래그앤드롭 정렬 — ordered_ids 순서대로 sort_order 재부여."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            for idx, gid in enumerate(body.ordered_ids):
                cur.execute(
                    """
                    UPDATE tb_ai_chat_group SET sort_order = %s
                    WHERE region = %s AND user_id = %s AND group_id = %s
                    """,
                    (idx, body.region, body.user_id, gid),
                )
        conn.commit()
        return {"status": "OK"}
    finally:
        conn.close()


# ── 메시지 ────────────────────────────────────────────────────────────


@router.get("/groups/{group_id}/messages")
def list_messages(group_id: str, region: str = Query(...)):
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ask_seq, user_payload, bot_payload
                FROM tb_ai_chat_message
                WHERE region = %s AND group_id = %s
                ORDER BY ask_seq ASC
                """,
                (region, group_id),
            )
            messages = [
                {"ask_seq": r[0], "user": r[1], "bot": r[2]}
                for r in cur.fetchall()
            ]
        return {"status": "OK", "messages": messages}
    finally:
        conn.close()


@router.put("/groups/{group_id}/messages/{ask_seq}")
def upsert_message(group_id: str, ask_seq: int, body: MessagePutBody):
    """신규 메시지 저장·피드백 플래그 갱신 공용 upsert + 그룹 last_at 갱신."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tb_ai_chat_message
                    (region, group_id, ask_seq, user_payload, bot_payload)
                VALUES (%s, %s, %s, %s::jsonb, %s::jsonb)
                ON CONFLICT (region, group_id, ask_seq) DO UPDATE
                    SET user_payload = EXCLUDED.user_payload,
                        bot_payload = EXCLUDED.bot_payload
                """,
                (body.region, group_id, ask_seq,
                 json.dumps(body.user, ensure_ascii=False),
                 json.dumps(body.bot, ensure_ascii=False)),
            )
            cur.execute(
                "UPDATE tb_ai_chat_group SET last_at = now() WHERE region = %s AND group_id = %s",
                (body.region, group_id),
            )
        conn.commit()
        return {"status": "OK"}
    finally:
        conn.close()


# ── 일회성 이관 (localStorage → 서버) ─────────────────────────────────


@router.post("/import")
def import_local(body: ImportBody):
    """
    localStorage 이관 — 멱등: 이미 존재하는 (region, group_id) 그룹은 통째로
    스킵 (부분 병합 없음 — 서버가 정본이 된 그룹에 구 로컬본을 덮지 않는다).
    """
    if len(body.groups) > MAX_IMPORT_GROUPS:
        raise HTTPException(400, f"too many groups (max {MAX_IMPORT_GROUPS})")

    imported_groups = 0
    imported_messages = 0
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            for g in body.groups:
                if len(g.group_id) > 40:
                    continue
                cur.execute(
                    "SELECT 1 FROM tb_ai_chat_group WHERE region = %s AND group_id = %s",
                    (body.region, g.group_id),
                )
                if cur.fetchone():
                    continue
                cur.execute(
                    """
                    INSERT INTO tb_ai_chat_group
                        (region, group_id, user_id, group_title, last_at)
                    VALUES (%s, %s, %s, %s,
                            COALESCE(%s::timestamptz, now()))
                    """,
                    (body.region, g.group_id, body.user_id,
                     (g.group_title or "새 대화")[:200], g.last_at),
                )
                imported_groups += 1
                for m in g.messages[:MAX_IMPORT_MESSAGES_PER_GROUP]:
                    ask_seq = m.get("ask_seq")
                    if not isinstance(ask_seq, int):
                        continue
                    cur.execute(
                        """
                        INSERT INTO tb_ai_chat_message
                            (region, group_id, ask_seq, user_payload, bot_payload)
                        VALUES (%s, %s, %s, %s::jsonb, %s::jsonb)
                        ON CONFLICT (region, group_id, ask_seq) DO NOTHING
                        """,
                        (body.region, g.group_id, ask_seq,
                         json.dumps(m.get("user") or {}, ensure_ascii=False),
                         json.dumps(m.get("bot") or {}, ensure_ascii=False)),
                    )
                    imported_messages += 1
        conn.commit()
        logger.info(
            "[chat-history] import user=%s groups=%d messages=%d",
            body.user_id, imported_groups, imported_messages,
        )
        return {
            "status": "OK",
            "imported_groups": imported_groups,
            "imported_messages": imported_messages,
        }
    finally:
        conn.close()
