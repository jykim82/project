"""태그 조회 계열 인텐트 핸들러 — 2단계 1차 이관.

FACILITY_TAG_LATEST_VALUE / FACILITY_TAG_DATA_TABLE:
datakey 기반 tagtype 필터를 SQL 에 주입 (수위/압력/유량 → Analog Input,
밸브 → Digital Input, 설정 → Analog Output).
"""
from __future__ import annotations

import logging

from .base import IntentContext, IntentHandler, intent_handler

logger = logging.getLogger(__name__)


@intent_handler
class TagTypeFilterHandler(IntentHandler):
    intents = ("FACILITY_TAG_LATEST_VALUE", "FACILITY_TAG_DATA_TABLE")

    async def pre_sql(self, ctx: IntentContext) -> None:
        _dk = ctx.params.get("datakey") or ctx.params.get("datainfo") or ""
        if "밸브" in _dk:
            _tagtype = "Digital Input"
        elif "설정" in _dk:
            _tagtype = "Analog Output"
        else:
            _tagtype = "Analog Input"
        if ctx.intent == "FACILITY_TAG_LATEST_VALUE":
            # ORDER BY 앞(WHERE 마지막)에 tagtype 조건 주입
            # (템플릿이 LATERAL top-1 구조 — 2026-07-14 perf)
            ctx.sql = ctx.sql.replace(
                "ORDER BY l.tagsn",
                f"  AND i.tagtype = '{_tagtype}'\nORDER BY l.tagsn",
            )
        elif "AND i.tagtype = 'Analog Input'" in ctx.sql:
            # FACILITY_TAG_DATA_TABLE: 하드코딩된 tagtype 을 동적으로 교체
            ctx.sql = ctx.sql.replace(
                "AND i.tagtype = 'Analog Input'",
                f"AND i.tagtype = '{_tagtype}'",
            )
