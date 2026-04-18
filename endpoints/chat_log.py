"""
채팅 전체 질의 로그 조회/집계 API
tb_ai_chat_log 리스트 + 인텐트별 통계 + 오답률(피드백과 JOIN)
"""

import logging
from typing import Optional

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat/log", tags=["chat-log"])

_get_db_connection = None


def init(get_db_connection_fn):
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


def _get_conn():
    if _get_db_connection is None:
        raise RuntimeError("chat_log not initialized")
    return _get_db_connection()


@router.get("")
def list_chat_log(
    limit: int = Query(200, ge=1, le=1000),
    intent: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    days: int = Query(7, ge=1, le=365, description="최근 N일"),
):
    """채팅 로그 목록 — 최신 순. intent/user_id/기간 필터."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            where = ["asked_at >= NOW() - INTERVAL %s"]
            params = [f"{days} days"]
            if intent:
                where.append("intent_name = %s")
                params.append(intent)
            if user_id:
                where.append("user_id = %s")
                params.append(user_id)
            where_sql = " AND ".join(where)
            cur.execute(
                f"""
                SELECT log_id, region, user_id, user_question, intent_name,
                       intent_confidence, graph_type, response_time_ms,
                       bot_summary, total_rows, has_visual, is_multimodal,
                       error, asked_at
                FROM tb_ai_chat_log
                WHERE {where_sql}
                ORDER BY asked_at DESC
                LIMIT %s
                """,
                (*params, limit),
            )
            rows = cur.fetchall()
        return [
            {
                "log_id": r[0],
                "region": r[1],
                "user_id": r[2],
                "user_question": r[3],
                "intent_name": r[4],
                "intent_confidence": float(r[5]) if r[5] is not None else None,
                "graph_type": r[6],
                "response_time_ms": r[7],
                "bot_summary": r[8],
                "total_rows": r[9],
                "has_visual": r[10],
                "is_multimodal": r[11],
                "error": r[12],
                "asked_at": r[13].isoformat() if r[13] else None,
            }
            for r in rows
        ]
    finally:
        conn.close()


@router.get("/stats")
def chat_log_stats(days: int = Query(7, ge=1, le=365)):
    """인텐트별 집계 + 오답률 (피드백 JOIN).

    반환: {
      overall: { total, avg_response_ms, error_rate, multimodal_count },
      by_intent: [ { intent_name, count, avg_response_ms, wrong_count, wrong_rate } ]
    }
    """
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            # 전체 요약
            cur.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    ROUND(AVG(response_time_ms))::int AS avg_ms,
                    SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) AS err_cnt,
                    SUM(CASE WHEN is_multimodal THEN 1 ELSE 0 END) AS mm_cnt
                FROM tb_ai_chat_log
                WHERE asked_at >= NOW() - INTERVAL %s
                """,
                (f"{days} days",),
            )
            ov = cur.fetchone()
            total = ov[0] or 0
            overall = {
                "total": total,
                "avg_response_ms": int(ov[1]) if ov[1] is not None else 0,
                "error_count": ov[2] or 0,
                "error_rate": round((ov[2] or 0) / total * 100, 1) if total else 0.0,
                "multimodal_count": ov[3] or 0,
            }

            # 인텐트별 + wrong_answer 피드백 JOIN (같은 user_id + 시간 근접 match는 복잡하니
            # 간단히 intent_name 기준 집계)
            cur.execute(
                """
                WITH logs AS (
                    SELECT intent_name, COUNT(*) AS cnt,
                           ROUND(AVG(response_time_ms))::int AS avg_ms
                    FROM tb_ai_chat_log
                    WHERE asked_at >= NOW() - INTERVAL %s
                    GROUP BY intent_name
                ),
                fb AS (
                    SELECT intent_name,
                           SUM(CASE WHEN feedback_type = 'wrong_answer' THEN 1 ELSE 0 END) AS wrong,
                           SUM(CASE WHEN feedback_type = 'positive' THEN 1 ELSE 0 END) AS pos
                    FROM tb_ai_chat_feedback
                    WHERE created_at >= NOW() - INTERVAL %s
                    GROUP BY intent_name
                )
                SELECT l.intent_name, l.cnt, l.avg_ms,
                       COALESCE(fb.wrong, 0) AS wrong,
                       COALESCE(fb.pos, 0) AS pos
                FROM logs l
                LEFT JOIN fb ON l.intent_name = fb.intent_name
                ORDER BY l.cnt DESC
                """,
                (f"{days} days", f"{days} days"),
            )
            by_intent = [
                {
                    "intent_name": r[0] or "(unknown)",
                    "count": r[1],
                    "avg_response_ms": r[2],
                    "wrong_count": r[3],
                    "positive_count": r[4],
                    "wrong_rate": round(r[3] / r[1] * 100, 1) if r[1] else 0.0,
                }
                for r in cur.fetchall()
            ]

        return {"overall": overall, "by_intent": by_intent, "days": days}
    finally:
        conn.close()
