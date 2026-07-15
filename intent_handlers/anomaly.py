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


class AnomalyFilterPrepareMixin:
    """ANOMALY 계열 공통 prepare — 선택적 시설 필터 + 범위 라벨 주입."""

    async def prepare(self, ctx: IntentContext) -> None:
        from response_builder import build_anomaly_facility_filter, build_anomaly_scope_label

        ctx.params["anomaly_facility_filter"] = build_anomaly_facility_filter(
            ctx.intent, ctx.params
        )
        ctx.params["anomaly_scope"] = build_anomaly_scope_label(ctx.params)
        logger.info(f"[SSE] ANOMALY filter: intent={ctx.intent}, "
                     f"scope={ctx.params['anomaly_scope']}, "
                     f"filter={ctx.params['anomaly_facility_filter']!r}")


@intent_handler
class AnomalyScanAllHandler(AnomalyFilterPrepareMixin, IntentHandler):
    """전체 이상 스캔 — prepare 필터 주입 (캐시 반환은 아직 인라인, 3차 이관)."""
    intents = ("ANOMALY_SCAN_ALL",)


@intent_handler
class AnomalyFilterOnlyHandler(AnomalyFilterPrepareMixin, IntentHandler):
    """예측/패턴/이력 — prepare 필터 주입만 필요한 ANOMALY 계열."""
    intents = ("ANOMALY_PREDICT", "ANOMALY_PATTERN", "ANOMALY_HISTORY")


@intent_handler
class AnomalyFacilityDetailHandler(IntentHandler):
    """시설 상세 이상 — stale 데이터 대응 시간창 조정 (max bucket 기준)."""
    intents = ("ANOMALY_FACILITY_DETAIL",)

    async def pre_sql(self, ctx: IntentContext) -> None:
        if not ctx.sql:
            return
        try:
            from anomaly_scan import adjust_sql_time_window_to_max_bucket

            execute_sql = service("execute_sql")
            _mb_rows, _ = execute_sql("SELECT max(bucket) FROM cagg_5min_raw_stats_ai", {})
            if _mb_rows and _mb_rows[0][0]:
                ctx.sql = adjust_sql_time_window_to_max_bucket(
                    ctx.sql, _mb_rows[0][0], label="[SSE] FACILITY_DETAIL",
                )
        except Exception as _e:
            logger.warning(f"[SSE] FACILITY_DETAIL: max(bucket) 확인 실패: {_e}")


@intent_handler
class AbnormalStatusSummaryHandler(IntentHandler):
    """실시간 결측 요약 — fn_realtime_missing_summary 는 빈 문자열 = 전체."""
    intents = ("FACILITY_ABNORMAL_STATUS_SUMMARY",)

    async def pre_sql(self, ctx: IntentContext) -> None:
        if ctx.params.get("sitename") in (None, "%%"):
            ctx.params["sitename"] = ""
        if ctx.params.get("facilitytype") in (None, "%%"):
            ctx.params["facilitytype"] = ""
        if ctx.params.get("datainfo") in (None, "%%"):
            ctx.params["datainfo"] = ""


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
