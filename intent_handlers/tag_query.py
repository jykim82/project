"""태그 조회 계열 인텐트 핸들러 — 2단계 1·3차 이관.

- tagtype 필터: datakey 기반 (수위/압력/유량 → Analog Input, 밸브 → Digital
  Input, 설정 → Analog Output)
- TIMESERIES 청크 직접 쿼리: 그룹 기반 + JOIN 플래너 우회 (성공 시 rows 대체)
"""
from __future__ import annotations

import asyncio
import logging

from .base import IntentContext, IntentHandler, intent_handler

logger = logging.getLogger(__name__)


def _tagtype_for(intent: str, params: dict) -> str:
    if intent == "FACILITY_DIGITAL_STATUS_TIMESERIES_TABLE":
        return "Digital Input"
    _dk = params.get("datakey") or params.get("datainfo") or ""
    if intent in ("FACILITY_TAG_LATEST_VALUE", "FACILITY_TAG_DATA_TABLE"):
        if "밸브" in _dk:
            return "Digital Input"
        if "설정" in _dk:
            return "Analog Output"
    return "Analog Input"


async def _timeseries_chunk_fetch(ctx: IntentContext) -> None:
    """TIMESERIES 청크 직접 쿼리 — 성공 시 ctx.rows 로 SQL 실행 대체."""
    from sql_executor import _execute_timeseries_query

    _sn = ctx.params.get("sitename", "%%")
    _ft_ts = ctx.params.get("facilitytype", "%%")
    _from = ctx.params.get("from_ts", "")
    _to = ctx.params.get("to_ts", "")
    _tagtype = _tagtype_for(ctx.intent, ctx.params)

    if ctx.intent == "FACILITY_FLOW_INSTANT_TIMESERIES_TABLE":
        _di_pat = "(유량.*순시|순시.*유량)"
    elif ctx.intent == "FACILITY_FLOW_ACCUMULATED_TIMESERIES_TABLE":
        _di_pat = "유량.*적산"
    else:
        _di_pat = ctx.params.get("datainfo", ".*")

    _group = ctx.params.get("group_code")
    if ctx.intent == "FACILITY_FLOW_INSTANT_TIMESERIES_TABLE":
        _group = _group or "FLOW_INSTANT"
    elif ctx.intent == "FACILITY_FLOW_ACCUMULATED_TIMESERIES_TABLE":
        _group = _group or "FLOW_CUMULATIVE"

    logger.info(f"[SSE] TIMESERIES 쿼리 시작: intent={ctx.intent}, site={_sn}, ft={_ft_ts}, tagtype={_tagtype}, di_pat={_di_pat}, group={_group}, from={_from}, to={_to}")
    try:
        _ts_rows, _ts_cols = await asyncio.to_thread(
            _execute_timeseries_query,
            _sn, _ft_ts, _tagtype, _di_pat, _from, _to,
            _group,
        )
        logger.info(f"[SSE] TIMESERIES 쿼리 완료: {len(_ts_rows)}행")
        if _ts_rows:
            ctx.rows = _ts_rows
            ctx.columns = _ts_cols
    except Exception as e:
        logger.error(f"[SSE] TIMESERIES 청크 쿼리 실패 ({ctx.intent}): {e}")


@intent_handler
class TagTypeFilterHandler(IntentHandler):
    intents = ("FACILITY_TAG_LATEST_VALUE", "FACILITY_TAG_DATA_TABLE")

    async def pre_sql(self, ctx: IntentContext) -> None:
        _tagtype = _tagtype_for(ctx.intent, ctx.params)
        if ctx.intent == "FACILITY_TAG_LATEST_VALUE":
            # ORDER BY 앞(WHERE 마지막)에 tagtype 조건 주입
            # (템플릿이 LATERAL top-1 구조 — 2026-07-14 perf)
            ctx.sql = ctx.sql.replace(
                "ORDER BY l.tagsn",
                f"  AND i.tagtype = '{_tagtype}'\nORDER BY l.tagsn",
            )
            return
        # FACILITY_TAG_DATA_TABLE: 하드코딩된 tagtype 을 동적으로 교체 + 청크 조달
        if "AND i.tagtype = 'Analog Input'" in ctx.sql:
            ctx.sql = ctx.sql.replace(
                "AND i.tagtype = 'Analog Input'",
                f"AND i.tagtype = '{_tagtype}'",
            )
        await _timeseries_chunk_fetch(ctx)


@intent_handler
class TimeseriesChunkHandler(IntentHandler):
    """시계열 표 계열 — 청크 직접 쿼리 (FACILITY_TAG_DATA_TABLE 은 위 핸들러가 겸임)."""
    intents = (
        "FACILITY_ANALOG_TIMESERIES_TABLE",
        "FACILITY_FLOW_INSTANT_TIMESERIES_TABLE",
        "FACILITY_FLOW_ACCUMULATED_TIMESERIES_TABLE",
        "FACILITY_DIGITAL_STATUS_TIMESERIES_TABLE",
    )

    async def pre_sql(self, ctx: IntentContext) -> None:
        await _timeseries_chunk_fetch(ctx)
