"""
LLM 자연어 서술 호출 통계 조회 엔드포인트

- GET /admin/llm-narrative/stats?days=7 — 최근 N일 집계

운영자가 LLM 통과율·할루시네이션 거부율·평균 응답 시간을 즉시 확인할 수 있음.
"""

import logging
from fastapi import APIRouter, Query

logger = logging.getLogger("slm")

router = APIRouter()


def init():
    """no dependencies"""
    pass


@router.get("/admin/llm-narrative/stats")
def get_narrative_stats(days: int = Query(7, ge=1, le=90)):
    """최근 N일 LLM 서술 호출 집계.

    반환:
      total, by_source, by_endpoint, context_mode, llm_pass_rate, rejected_rate
    """
    from llm_narrative_log import aggregate_last_n_days
    stats = aggregate_last_n_days(days=days)
    return {
        "status": "OK",
        "period_days": days,
        **stats,
    }
