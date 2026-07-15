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
    """전체 이상 스캔 — prepare 필터 주입 + stale-while-revalidate 캐시 반환."""
    intents = ("ANOMALY_SCAN_ALL",)

    async def pre_sql(self, ctx: IntentContext) -> None:
        from datetime import datetime

        from response_builder import (
            MAX_TABLE_ROWS,
            _filter_anomaly_cache_rows,
            _filter_by_sitename,
            _filter_cross_mismatches,
            _filter_flow_balance,
            apply_corrections_to_answer,
            build_anomaly_scope_label,
            build_error_response,
            build_success_response,
            render_answer_template,
        )

        scan_cache = (service("get_scan_cache") or (lambda: None))()
        if not scan_cache:
            ctx.final_response = build_error_response(
                "전체 센서 점검 데이터를 준비 중입니다 (서버 시작 후 약 1~2분 소요). 잠시 후 다시 질문해 주세요.",
                session_id=ctx.session_id,
            )
            return
        cache_time = (service("get_scan_cache_time") or (lambda: None))()
        cache_age = (datetime.now() - cache_time).total_seconds() if cache_time else 0
        logger.info(f"[SSE] ANOMALY_SCAN_ALL 캐시 반환 ({cache_age:.0f}초 전)")
        ctx.progress_message = ("cache_hit", "이상감지 결과를 반환합니다...")
        _c = scan_cache
        _c_rows = _c["rows"]
        _c_cols = _c["columns"]
        _c_data = dict(_c["processed_data"])
        _c_tmpl = _c["answer_template"]

        # facilitytype / group_code 필터 적용
        _c_rows = _filter_anomaly_cache_rows(_c_rows, _c_cols, ctx.params)
        _scope = build_anomaly_scope_label(ctx.params)
        if _scope != "전체":
            _c_data["total_tag_count"] = len(_c_rows)
            from anomaly_detector import count_anomaly_levels
            _fc = count_anomaly_levels(_c_rows, _c_cols)
            _c_data["error_count"] = _fc["이상"]
            _c_data["warn_count"] = _fc["주의"]
            _c_data["ok_count"] = _fc["정상"]
            # verdict 기반 교차이상 카운트 재계산
            _vd_idx = _c_cols.index("verdict") if "verdict" in _c_cols else None
            if _vd_idx is not None:
                _c_data["cross_anomaly_count"] = sum(
                    1 for r in _c_rows if r[_vd_idx] in ("교차이상", "교차주의", "복합이상")
                )

        ctx.params["total_count"] = str(len(_c_rows))
        rendered = render_answer_template(_c_tmpl, _c_data)
        rendered = apply_corrections_to_answer(rendered, ctx.params)

        save_csv = service("save_csv")
        stratified_sample = service("stratified_sample")
        csv_fn = save_csv(_c_rows, _c_cols, ctx.intent, ctx.session_id)
        _total = len(_c_rows)
        if _total > MAX_TABLE_ROWS:
            _sampled = stratified_sample(_c_rows, _c_cols, MAX_TABLE_ROWS)
            _resp_data = [dict(zip(_c_cols, r)) for r in _sampled]
            _trunc = True
        else:
            _resp_data = [dict(zip(_c_cols, r)) for r in _c_rows]
            _trunc = False

        _sn = ctx.params.get("sitename")
        ctx.final_response = build_success_response(
            intent=ctx.intent,
            answer=rendered,
            graph_type=ctx.graph_type,
            data=_resp_data,
            table_columns=ctx.table_columns,
            table_type=ctx.table_type,
            session_id=ctx.session_id,
            csv_url=f"/csv/{csv_fn}",
            total_rows=_total,
            data_truncated=_trunc,
            intent_candidates=ctx.intent_candidates,
            site_group_distribution=_c_data.get("site_group_distribution"),
            cross_anomaly_count=_c_data.get("cross_anomaly_count"),
            cross_facility_mismatches=_filter_cross_mismatches(_c_data.get("cross_facility_mismatches"), _sn),
            cross_facility_mismatch_count=len(_filter_cross_mismatches(_c_data.get("cross_facility_mismatches"), _sn) or []),
            data_quality_issues=_filter_by_sitename(_c_data.get("data_quality_issues"), _sn),
            equipment_failure_impacts=_filter_by_sitename(_c_data.get("equipment_failure_impacts"), _sn),
            equipment_failure_count=len(_filter_by_sitename(_c_data.get("equipment_failure_impacts"), _sn) or []),
            flow_balance_summary=_filter_flow_balance(_c_data.get("flow_balance_summary"), _sn),
            # [Phase 5] ML 지표 — 동기 경로에는 있었으나 SSE 에 누락돼 있던 격차 해소
            ml_model_count=_c_data.get("ml_model_count"),
            ml_anomaly_count=_c_data.get("ml_anomaly_count"),
            ml_agree_count=_c_data.get("ml_agree_count"),
            ml_tier1_count=_c_data.get("ml_tier1_count"),
            ml_tier2_count=_c_data.get("ml_tier2_count"),
        )


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
