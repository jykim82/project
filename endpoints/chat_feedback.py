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
    }


_SELECT_COLUMNS = (
    "feedback_id, region, user_id, user_question, bot_answer, intent_name, "
    "feedback_type, comment, reviewed, reviewed_at, reviewed_by, created_at"
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
    """수동 검토 완료 표시 — example3.json 등에 반영 후 호출"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tb_ai_chat_feedback "
                "SET reviewed = true, reviewed_at = now(), reviewed_by = %s "
                "WHERE feedback_id = %s "
                f"RETURNING {_SELECT_COLUMNS}",
                (body.reviewed_by, feedback_id),
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
