"""교체 우선순위 인텐트 핸들러 — 설비 건강성 3신호 융합 재사용.

REPLACEMENT_PRIORITY_QUERY: "교체해야 할 설비 알려줘" →
endpoints/replacement_priority.replacement_priority() 를 직접 호출해
표 rows 를 조달 (판정 로직 이원화 방지 — 개요 카드와 동일 결과 보장).
docs/equipment-health-priority-spec.md §향후 후보 이행 (2026-07-16).
"""
from __future__ import annotations

import asyncio
import logging

from .base import IntentContext, IntentHandler, intent_handler

logger = logging.getLogger(__name__)

_TOP_N = 5
_PERIOD_DAYS = 90


@intent_handler
class ReplacementPriorityHandler(IntentHandler):
    """3신호 융합 우선순위 → 채팅 표. rows 조달로 SQL 실행 대체."""
    intents = ("REPLACEMENT_PRIORITY_QUERY",)

    async def pre_sql(self, ctx: IntentContext) -> None:
        from endpoints.replacement_priority import replacement_priority

        try:
            data = await asyncio.to_thread(
                replacement_priority, limit=_TOP_N, days=_PERIOD_DAYS
            )
        except Exception as e:
            logger.error("교체 우선순위 조회 실패: %s", e)
            return  # rows 미설정 → 파이프라인 no-data 경로

        rows = []
        for i, r in enumerate(data.get("rows", []), start=1):
            equipment = r["equipmenttype"]
            if r.get("equipment_id"):
                equipment += f" ({r['equipment_id']})"
            reasons = " · ".join(x["label"] for x in r.get("reasons", []))
            rows.append((
                str(i),
                f"{r['sitename']} {r['facilitytype']}",
                equipment,
                r["level"],
                reasons,
            ))
        ctx.columns = ["순위", "시설", "설비", "우선순위", "사유"]
        ctx.rows = rows
