"""이상감지 계열 인텐트 핸들러 — 2단계 1차 이관 (기존 event_generator 인라인 분기).

본문 로직은 ai_server 인라인 분기에서 그대로 이관 (동작 무변경).
ai_server 전역(_CAUSAL_INDEX/_FLOW_BALANCE_CACHE 등)은 services getter 로 접근.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from .base import IntentContext, IntentHandler, intent_handler, service

logger = logging.getLogger(__name__)


@intent_handler
class CrossFacilityHandler(IntentHandler):
    """시설간 교차 검증 — SQL 미사용, causal index 기반 불일치 계산."""
    intents = ("ANOMALY_CROSS_FACILITY",)

    async def pre_sql(self, ctx: IntentContext) -> None:
        try:
            from anomaly_detector import cross_facility_check_all
            from sql_executor import _query_recent_values

            causal_index = (service("get_causal_index") or (lambda: None))()
            mismatches = await asyncio.to_thread(
                cross_facility_check_all, _query_recent_values, causal_index,
            )
            ctx.params["_cross_facility_mismatches"] = mismatches
            ctx.rows = [["cross_facility_done"]]
            ctx.columns = ["status"]
            logger.info(f"[SSE] ANOMALY_CROSS_FACILITY: {len(mismatches)}건 불일치")
        except Exception as e:
            logger.error(f"[SSE] ANOMALY_CROSS_FACILITY 실패: {e}")
            ctx.params["_cross_facility_mismatches"] = []
            ctx.rows = [["cross_facility_error"]]
            ctx.columns = ["status"]


@intent_handler
class EquipmentFaultHandler(IntentHandler):
    """설비 장애 전용 — ANOMALY_SCAN_ALL 캐시의 DI 고장 재사용."""
    intents = ("EQUIPMENT_FAULT_STATUS",)

    async def pre_sql(self, ctx: IntentContext) -> None:
        scan_cache = (service("get_scan_cache") or (lambda: None))() or {}
        cache = scan_cache.get("processed_data", {})
        impacts = cache.get("equipment_failure_impacts") or []
        ctx.params["_equipment_failure_impacts"] = impacts
        ctx.rows = [["equipment_fault_done"]]
        ctx.columns = ["status"]
        logger.info(f"[SSE] EQUIPMENT_FAULT_STATUS: 설비 장애 {len(impacts)}건 (스캔 캐시)")


@intent_handler
class FlowBalanceHandler(IntentHandler):
    """물 수지 검증 — 30분 캐시 우선, 미스 시 즉시 계산 (SQL 미사용)."""
    intents = ("ANOMALY_FLOW_BALANCE",)

    async def pre_sql(self, ctx: IntentContext) -> None:
        _filter_edges = service("filter_flow_balance_edges")
        _fb_sitename = ctx.params.get("sitename")
        cache = (service("get_flow_balance_cache") or (lambda: (None, None, 0)))()
        fb_cache, fb_time, fb_ttl = cache
        try:
            if fb_cache and fb_time:
                cache_age = (datetime.now() - fb_time).total_seconds()
                if cache_age < fb_ttl:
                    ctx.params["_flow_balance_edges"] = _filter_edges(fb_cache, _fb_sitename)
                    ctx.rows = [["flow_balance_cached"]]
                    ctx.columns = ["status"]
                    logger.info(f"[SSE] ANOMALY_FLOW_BALANCE 캐시 히트 ({cache_age:.0f}초 전), sitename={_fb_sitename}")
                else:
                    raise ValueError("cache expired")
            else:
                raise ValueError("no cache")
        except (ValueError, Exception):
            try:
                from flow_balance import compute_flow_balance_all
                from sql_executor import _get_tag_datainfo_cache, _query_flow_timeseries

                causal_index = (service("get_causal_index") or (lambda: None))()
                tag_info = await asyncio.to_thread(_get_tag_datainfo_cache)
                edges = await asyncio.to_thread(
                    compute_flow_balance_all,
                    _query_flow_timeseries, causal_index, tag_info,
                )
                ctx.params["_flow_balance_edges"] = _filter_edges(edges, _fb_sitename)
                ctx.rows = [["flow_balance_done"]]
                ctx.columns = ["status"]
                logger.info(f"[SSE] ANOMALY_FLOW_BALANCE: {len(edges)}엣지, sitename={_fb_sitename}")
            except Exception as e2:
                logger.error(f"[SSE] ANOMALY_FLOW_BALANCE 실패: {e2}")
                ctx.params["_flow_balance_edges"] = []
                ctx.rows = [["flow_balance_error"]]
                ctx.columns = ["status"]
