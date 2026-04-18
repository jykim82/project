"""
채팅 봇 오분류 피드백 수집 API
tb_ai_chat_feedback 테이블 관리 (수동 검토 게이트)

현재 채팅 히스토리는 클라이언트 localStorage에만 저장되므로 self-contained
구조로 저장한다 (질문/답변/인텐트 복사본 포함). 추후 채팅 영속화 구현 시
ask_seq FK 컬럼을 추가해 확장할 수 있다.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat/feedback", tags=["chat-feedback"])

_get_db_connection = None

_ALLOWED_TYPES = {"wrong_answer", "misclassified", "incomplete", "other", "positive"}


def init(get_db_connection_fn):
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


def _get_conn():
    if _get_db_connection is None:
        raise RuntimeError("chat_feedback not initialized")
    return _get_db_connection()


# ── 요청/응답 스키마 ────────────────────────────────────────────────────────

class FeedbackCreate(BaseModel):
    region: str = Field(..., max_length=10)
    user_id: str = Field(..., max_length=45)
    user_question: str = Field(..., min_length=1, max_length=4000)
    bot_answer: Optional[str] = Field(None, max_length=8000)
    intent_name: Optional[str] = Field(None, max_length=80)
    feedback_type: str = Field("wrong_answer", max_length=20)
    comment: Optional[str] = Field(None, max_length=2000)


class FeedbackReview(BaseModel):
    reviewed_by: str = Field(..., max_length=45)
    correct_intent: Optional[str] = Field(None, max_length=80)
    suggested_question: Optional[str] = Field(None, max_length=4000)


def _row_to_dict(row) -> dict:
    return {
        "feedback_id": row[0],
        "region": row[1],
        "user_id": row[2],
        "user_question": row[3],
        "bot_answer": row[4],
        "intent_name": row[5],
        "feedback_type": row[6],
        "comment": row[7],
        "reviewed": row[8],
        "reviewed_at": row[9].isoformat() if row[9] else None,
        "reviewed_by": row[10],
        "created_at": row[11].isoformat() if row[11] else None,
        "correct_intent": row[12],
        "suggested_question": row[13],
        "applied_to_index": row[14],
        "applied_at": row[15].isoformat() if row[15] else None,
    }


_SELECT_COLUMNS = (
    "feedback_id, region, user_id, user_question, bot_answer, intent_name, "
    "feedback_type, comment, reviewed, reviewed_at, reviewed_by, created_at, "
    "correct_intent, suggested_question, applied_to_index, applied_at"
)


# ── 엔드포인트 ───────────────────────────────────────────────────────────────

@router.post("", status_code=status.HTTP_201_CREATED)
def create_feedback(body: FeedbackCreate):
    """피드백 등록 — 사용자가 '원하는 답이 아닌가요?' 클릭 시 호출"""
    if body.feedback_type not in _ALLOWED_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"feedback_type must be one of {sorted(_ALLOWED_TYPES)}",
        )

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tb_ai_chat_feedback "
                "(region, user_id, user_question, bot_answer, intent_name, "
                " feedback_type, comment) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                f"RETURNING {_SELECT_COLUMNS}",
                (
                    body.region,
                    body.user_id,
                    body.user_question,
                    body.bot_answer,
                    body.intent_name,
                    body.feedback_type,
                    body.comment,
                ),
            )
            row = cur.fetchone()
        conn.commit()
        return _row_to_dict(row)
    except Exception as e:
        conn.rollback()
        logger.error("create_feedback error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("")
def list_feedback(
    region: str = Query(...),
    reviewed: Optional[bool] = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    """피드백 목록 조회 — 관리자 수동 검토용"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            if reviewed is None:
                cur.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM tb_ai_chat_feedback "
                    "WHERE region = %s ORDER BY created_at DESC LIMIT %s",
                    (region, limit),
                )
            else:
                cur.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM tb_ai_chat_feedback "
                    "WHERE region = %s AND reviewed = %s "
                    "ORDER BY created_at DESC LIMIT %s",
                    (region, reviewed, limit),
                )
            return [_row_to_dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error("list_feedback error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{feedback_id}/review")
def mark_reviewed(feedback_id: int, body: FeedbackReview):
    """수동 검토 완료 — 운영자가 정답 intent/유사 질문 지정 가능.

    correct_intent 지정 시 classifier 재학습 대상(applied_to_index=false)으로
    플래그. suggested_question이 None이면 user_question 그대로 샘플로 사용.
    """
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tb_ai_chat_feedback "
                "SET reviewed = true, reviewed_at = now(), reviewed_by = %s, "
                "    correct_intent = COALESCE(%s, correct_intent), "
                "    suggested_question = COALESCE(%s, suggested_question) "
                "WHERE feedback_id = %s "
                f"RETURNING {_SELECT_COLUMNS}",
                (
                    body.reviewed_by,
                    body.correct_intent,
                    body.suggested_question,
                    feedback_id,
                ),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="feedback not found")
        conn.commit()
        return _row_to_dict(row)
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        logger.error("mark_reviewed error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── C1: 정답 intent 지정 지원 ─────────────────────────────────────────────

# intent_index (example3.json 메타) + embedding_index (런타임 임베딩)
_intent_index_ref = None
_embedding_index_ref = None


def init_intent_index(intent_index, embedding_index=None):
    """ai_server.py에서 IntentIndex + (선택) IntentEmbeddingIndex 주입.

    IntentIndex는 /intents 목록 조회에 사용,
    IntentEmbeddingIndex는 /reindex로 런타임 샘플 추가에 사용.
    """
    global _intent_index_ref, _embedding_index_ref
    _intent_index_ref = intent_index
    _embedding_index_ref = embedding_index


@router.get("/intents")
def list_known_intents():
    """등록된 intent 목록 (관리자 드롭다운용). example3.json 기반."""
    if _intent_index_ref is None:
        return {"intents": []}
    try:
        intents = []
        names = _intent_index_ref.get_all_intent_names() if hasattr(_intent_index_ref, "get_all_intent_names") else []
        for intent_name in sorted(names):
            summary = _intent_index_ref.get_intent_summary(intent_name) or {}
            desc = summary.get("description") or intent_name
            intents.append({"intent": intent_name, "description": desc})
        return {"intents": intents}
    except Exception as e:
        logger.warning("list_known_intents 실패: %s", e)
        return {"intents": []}


# ── C2: 재학습 (피드백 샘플을 embedding_index에 주입) ──────────────────────

@router.post("/reindex")
def reindex_from_feedback():
    """검토 완료되고 correct_intent 있는 피드백을 임베딩 인덱스에 주입.

    applied_to_index=false인 항목만 대상. 성공 시 플래그 세팅 +
    persist_cache()로 디스크 반영. 재시작 없이 즉시 분류에 반영됨.
    """
    if _embedding_index_ref is None:
        raise HTTPException(
            status_code=503,
            detail="embedding_index 미초기화. ai_server.py에서 init_intent_index(..., embedding_index) 호출 필요",
        )
    add_method = getattr(_embedding_index_ref, "add_sample", None)
    if add_method is None:
        raise HTTPException(
            status_code=501,
            detail="IntentEmbeddingIndex.add_sample 미구현",
        )

    conn = _get_conn()
    applied_ids: list[int] = []
    samples = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT feedback_id, user_question, suggested_question, correct_intent
                FROM tb_ai_chat_feedback
                WHERE reviewed = true
                  AND correct_intent IS NOT NULL
                  AND applied_to_index = false
                ORDER BY feedback_id ASC
                """
            )
            samples = cur.fetchall()

        for feedback_id, user_q, suggested_q, correct_intent in samples:
            question = (suggested_q or user_q or "").strip()
            if not question:
                continue
            try:
                ok = add_method(correct_intent, question)
                if ok:
                    applied_ids.append(feedback_id)
            except Exception as e:
                logger.warning("reindex 샘플 추가 실패 (fid=%s): %s", feedback_id, e)

        if applied_ids:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tb_ai_chat_feedback "
                    "SET applied_to_index = true, applied_at = now() "
                    "WHERE feedback_id = ANY(%s)",
                    (applied_ids,),
                )
            conn.commit()

        persisted = False
        persist = getattr(_embedding_index_ref, "persist_cache", None)
        if callable(persist) and applied_ids:
            try:
                persisted = persist()
            except Exception as e:
                logger.warning("persist_cache 실패: %s", e)

        return {
            "applied_count": len(applied_ids),
            "applied_ids": applied_ids,
            "total_pending": len(samples),
            "cache_persisted": persisted,
        }
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        logger.error("reindex_from_feedback error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
