"""야간최소유량·누수 계열 인텐트 핸들러 — 2단계 2·3차 이관."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from .base import IntentContext, IntentHandler, intent_handler, service

logger = logging.getLogger(__name__)


@intent_handler
class NightMinFlowSummaryHandler(IntentHandler):
    """야간최소유량 표 — 기본 1년 기간 + answer_template 오버라이드."""
    intents = ("NIGHT_MIN_FLOW_SUMMARY_TABLE",)

    async def pre_sql(self, ctx: IntentContext) -> None:
        from intent_matching import KNOWN_SITENAMES

        _user_period = ctx.params.get("user_specified_period", False)
        if not _user_period:
            _tt_date = datetime.now()
            _ft_date = _tt_date - timedelta(days=365)
            ctx.params["from_ts"] = _ft_date.strftime("%Y-%m-%d")
            ctx.params["to_ts"] = _tt_date.strftime("%Y-%m-%d")
        # fn_night_min_flow_summary 는 = 비교이므로 '%%' 와일드카드 대신 '전체' 사용
        _site = ctx.params.get("sitename", "")
        if _site == "%%":
            ctx.params["sitename"] = "전체"
            _site = "전체"
        _ftype = ctx.params.get("facilitytype", "소블록")
        _display_site = "전체" if _site == "%%" or _site == "전체" else _site
        _ft = ctx.params.get("from_ts", "")
        _tt = ctx.params.get("to_ts", "")
        if _user_period:
            _period_line = f"{_ft} ~ {_tt} 기간의 데이터를 표출합니다."
        else:
            _period_line = "기간 설정이 없는 경우는 최근 1년 기준으로 1달 단위 데이터를 표출합니다."
        _subject = f"{_display_site} {_ftype}" if _display_site != "전체" else _ftype
        _sample_site = _display_site
        if _display_site == "전체" and KNOWN_SITENAMES:
            _sample_site = KNOWN_SITENAMES[0]
        ctx.answer_template = {
            "summary": f"{_period_line} {_subject} 야간최소유량은 다음과 같습니다.",
            "detail": [
                {"prefix": "ㆍ", "text": "야간 최소유량은 60분 단위 이동평균 계산법을 적용하여 계산됩니다."}
            ],
            "recommend_questions": {
                "title": "다음은 추천질의입니다.",
                "items": [
                    {"prefix": "1.", "text": f"{_sample_site} {_ftype} 야간최소유량을 표로 보여줘"},
                    {"prefix": "2.", "text": f"전체 {_ftype} 야간최소유량을 표로 보여줘"},
                    {"prefix": "3.", "text": f"최근 한달간 {_ftype} 야간최소유량을 표로 보여줘"},
                ]
            }
        }
        # 청크 직접 쿼리 (fn_night_min_flow_summary 대체) — 성공 시 early-return
        await self._chunk_query_early_return(ctx)

    async def _chunk_query_early_return(self, ctx: IntentContext) -> None:
        from response_builder import MAX_TABLE_ROWS, build_success_response
        from sql_executor import _execute_night_min_flow_query

        _nmf_sn = ctx.params.get("sitename", "전체")
        _nmf_ft = ctx.params.get("facilitytype", "소블록")
        _nmf_from = ctx.params.get("from_ts", "")
        _nmf_to = ctx.params.get("to_ts", "")
        try:
            _nmf_rows, _nmf_cols = await asyncio.to_thread(
                _execute_night_min_flow_query,
                _nmf_sn, _nmf_ft, _nmf_from, _nmf_to,
            )
            if _nmf_rows:
                save_csv = service("save_csv")
                stratified_sample = service("stratified_sample")
                csv_fn = save_csv(_nmf_rows, _nmf_cols, ctx.intent, ctx.session_id)
                _total = len(_nmf_rows)
                if _total > MAX_TABLE_ROWS:
                    _sampled = stratified_sample(_nmf_rows, _nmf_cols, MAX_TABLE_ROWS)
                    _resp_data = [dict(zip(_nmf_cols, r)) for r in _sampled]
                    _trunc = True
                else:
                    _resp_data = [dict(zip(_nmf_cols, r)) for r in _nmf_rows]
                    _trunc = False
                _nmf_rendered = ctx.answer_template if isinstance(ctx.answer_template, dict) else {}
                ctx.final_response = build_success_response(
                    intent=ctx.intent, answer=_nmf_rendered, graph_type=ctx.graph_type,
                    data=_resp_data, columns=_nmf_cols, csv_file=csv_fn,
                    session_id=ctx.session_id, intent_candidates=ctx.intent_candidates,
                    total_rows=_total, data_truncated=_trunc,
                )
        except Exception as e:
            logger.warning(f"SSE 야간최소유량 청크 쿼리 실패, 원본 함수 폴백: {e}")


@intent_handler
class StddevAnalysisHandler(IntentHandler):
    """야간최소유량 표준편차 분석 — 템플릿 + 청크 직접 쿼리 early-return (53s→~10s)."""
    intents = ("FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS",)

    async def pre_sql(self, ctx: IntentContext) -> None:
        self._override_template(ctx)
        await self._chunk_query_early_return(ctx)

    async def _chunk_query_early_return(self, ctx: IntentContext) -> None:
        from response_builder import (
            _extract_stddev_stats,
            build_success_response,
            render_answer_template,
        )
        from sql_executor import _execute_night_min_flow_stddev_query

        _sd_sn = ctx.params.get("sitename", "")
        _sd_ft = ctx.params.get("facilitytype", "소블록")
        try:
            _sd_rows, _sd_cols, _sd_stats_list = await asyncio.to_thread(
                _execute_night_min_flow_stddev_query, _sd_sn, _sd_ft,
            )
            if not _sd_rows:
                return
            _sd_data = [dict(zip(_sd_cols, r)) for r in _sd_rows]
            _sd_kwargs: dict = {}
            if len(_sd_rows) == 1:
                _sd_kwargs["stddev_stats"] = _extract_stddev_stats(_sd_data[0])
                _sd_rendered = render_answer_template(ctx.answer_template, ctx.params) if isinstance(ctx.answer_template, dict) else {}
            elif _sd_stats_list:
                _sd_kwargs["stddev_stats_list"] = _sd_stats_list
                _sd_ft_label = ctx.params.get("facilitytype", "소블록")
                _raw_sn = ctx.params.get("sitename", "")
                _sd_sn_label = "전체" if (not _raw_sn or _raw_sn == "%%") else _raw_sn
                _n_normal = sum(1 for s in _sd_stats_list if (s.get("excess") or 0) <= 0)
                _n_exceed = len(_sd_stats_list) - _n_normal
                _sd_rendered = {
                    "summary": f"{_sd_sn_label} {_sd_ft_label} 야간최소유량 표준편차 분석 결과입니다.",
                    "detail": [
                        {"prefix": "ㆍ", "text": f"총 {len(_sd_stats_list)}개 시설 분석 — 정상 {_n_normal}개, 신뢰구간 초과 {_n_exceed}개"},
                    ],
                }
            else:
                _sd_rendered = render_answer_template(ctx.answer_template, ctx.params) if isinstance(ctx.answer_template, dict) else {}
            ctx.final_response = build_success_response(
                intent=ctx.intent, answer=_sd_rendered, graph_type=ctx.graph_type,
                data=_sd_data, columns=_sd_cols,
                session_id=ctx.session_id, intent_candidates=ctx.intent_candidates,
                total_rows=len(_sd_rows),
                **_sd_kwargs,
            )
        except Exception as e:
            logger.warning(f"SSE 표준편차분석 청크 쿼리 실패, 원본 함수 폴백: {e}")

    def _override_template(self, ctx: IntentContext) -> None:
        _site = ctx.params.get("sitename", "")
        _ftype = ctx.params.get("facilitytype", "")
        ctx.answer_template = {
            "summary": f"{_site} {_ftype}의 야간최소유량 표준편차분석은 다음과 같습니다.",
            "detail": [
                {"prefix": "ㆍ", "text": f"현재 {_site} {_ftype} 소블록 야간최소유량과 한달 및 일년 표준편차분석 결과입니다."},
                {"prefix": "ㆍ", "text": "분석결과(표)"},
            ],
            "reference": {
                "title": "다음 참고자료입니다.",
                "items": [
                    {"prefix": "1.", "text": f"{_site} {_ftype} 소블록 평균 야간최소유량"},
                    {"prefix": "ㆍ", "text": "금월 야간최소유량 평균은 {avg_month}{unit}, 금년 야간최소유량 평균은 {avg_year}{unit} 입니다."},
                ]
            },
            "recommend_questions": {
                "title": "다음은 추천질의입니다.",
                "items": [
                    {"prefix": "1.", "text": f"{_site} {_ftype} 야간최소유량 트렌드 그래프를 보여줘"},
                    {"prefix": "2.", "text": f"{_site} {_ftype} 야간최소유량 표준편차분석을 통해 이상여부를 확인해줘"},
                    {"prefix": "3.", "text": f"{_site} {_ftype} 데이터 결측분석결과를 알려줘"},
                ]
            }
        }


@intent_handler
class LeakCusumHandler(IntentHandler):
    """CUSUM 누수 추정 — 기본 파라미터 + 청크 조달 (CUSUM 계산은 process_sql_result)."""
    intents = ("LEAK_CUSUM_ANALYSIS",)

    async def prepare(self, ctx: IntentContext) -> None:
        if not ctx.params.get("facilitytype"):
            ctx.params["facilitytype"] = "소블록"
        if not ctx.params.get("sitename"):
            ctx.params["sitename"] = "전체"
        if not ctx.params.get("from_ts"):
            ctx.params["from_ts"] = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        if not ctx.params.get("to_ts"):
            ctx.params["to_ts"] = datetime.now().strftime("%Y-%m-%d")
        logger.info(f"[SSE] LEAK_CUSUM defaults: site={ctx.params['sitename']}, "
                     f"ft={ctx.params['facilitytype']}, "
                     f"from={ctx.params['from_ts']}, to={ctx.params['to_ts']}")

    async def pre_sql(self, ctx: IntentContext) -> None:
        from sql_executor import _execute_night_min_flow_query

        _lc_sn = ctx.params.get("sitename", "전체")
        _lc_ft = ctx.params.get("facilitytype", "소블록")
        _lc_from = ctx.params.get("from_ts", "")
        _lc_to = ctx.params.get("to_ts", "")
        try:
            _lc_rows, _lc_cols = await asyncio.to_thread(
                _execute_night_min_flow_query,
                _lc_sn, _lc_ft, _lc_from, _lc_to,
            )
            if _lc_rows:
                ctx.rows = _lc_rows
                ctx.columns = _lc_cols
                logger.info(f"SSE LEAK_CUSUM 청크 쿼리: {len(_lc_rows)}행")
        except Exception as e:
            logger.warning(f"SSE LEAK_CUSUM 청크 쿼리 실패, 원본 함수 폴백: {e}")


@intent_handler
class NightMinFlowStatusHandler(IntentHandler):
    """야간최소유량 현황 — 다중 시설 지원 커스텀 조달 (당일/월/년 평균)."""
    intents = ("NIGHT_MIN_FLOW_STATUS",)

    async def pre_sql(self, ctx: IntentContext) -> None:
        from collections import defaultdict

        from sql_executor import _execute_night_min_flow_query

        _nfs_ft = ctx.params.get("facilitytype", "소블록")
        _nfs_site_list = [ctx.params.get("sitename", "")]
        if ctx.extra_sitenames:
            _nfs_site_list.extend(ctx.extra_sitenames)
            ctx.extra_sitenames = None
        _nfs_result_cols = ["sitename", "facilitytype", "label", "datadesc",
                            "current_val", "unit", "log_time", "avg_month", "avg_year"]
        _all_nfs_rows: list = []
        for _nfs_sn in _nfs_site_list:
            if not _nfs_sn:
                continue
            try:
                _now = datetime.now()
                _cur_rows, _cur_cols = await asyncio.to_thread(
                    _execute_night_min_flow_query,
                    _nfs_sn, _nfs_ft,
                    (_now - timedelta(days=1)).strftime("%Y-%m-%d"),
                    _now.strftime("%Y-%m-%d"),
                )
                if not _cur_rows:
                    continue
                _mon_rows, _ = await asyncio.to_thread(
                    _execute_night_min_flow_query,
                    _nfs_sn, _nfs_ft,
                    (_now - timedelta(days=30)).strftime("%Y-%m-%d"),
                    _now.strftime("%Y-%m-%d"),
                )
                _yr_rows, _ = await asyncio.to_thread(
                    _execute_night_min_flow_query,
                    _nfs_sn, _nfs_ft,
                    (_now - timedelta(days=365)).strftime("%Y-%m-%d"),
                    _now.strftime("%Y-%m-%d"),
                )
                _tag_vals_m: dict = defaultdict(list)
                _tag_vals_y: dict = defaultdict(list)
                for r in _mon_rows:
                    rd = dict(zip(_cur_cols, r))
                    _tag_vals_m[rd.get("tagsn", "")].append(float(rd.get("val") or 0))
                for r in _yr_rows:
                    rd = dict(zip(_cur_cols, r))
                    _tag_vals_y[rd.get("tagsn", "")].append(float(rd.get("val") or 0))
                _seen: set = set()
                for r in reversed(_cur_rows):
                    rd = dict(zip(_cur_cols, r))
                    _tsn = rd.get("tagsn", "")
                    if _tsn in _seen:
                        continue
                    _seen.add(_tsn)
                    _cv = float(rd.get("val") or 0)
                    _mv = _tag_vals_m.get(_tsn, [])
                    _yv = _tag_vals_y.get(_tsn, [])
                    _all_nfs_rows.append((
                        rd.get("sitename", ""), rd.get("facilitytype", ""),
                        rd.get("label", rd.get("datainfo", "")), rd.get("datadesc", ""),
                        round(_cv, 2), rd.get("unit") or "", rd.get("log_time", ""),
                        round(sum(_mv) / len(_mv), 2) if _mv else None,
                        round(sum(_yv) / len(_yv), 2) if _yv else None,
                    ))
                logger.info(f"SSE NIGHT_MIN_FLOW_STATUS '{_nfs_sn}': {len(_all_nfs_rows)}행 누적")
            except Exception as e:
                logger.warning(f"SSE NIGHT_MIN_FLOW_STATUS '{_nfs_sn}' 커스텀 실패, 폴백: {e}")
        if _all_nfs_rows:
            ctx.sql = "-- custom handler"
            ctx.rows = _all_nfs_rows
            ctx.columns = _nfs_result_cols
            if len([s for s in _nfs_site_list if s]) > 1:
                ctx.params["sitename"] = ", ".join(s for s in _nfs_site_list if s)
            logger.info(f"SSE NIGHT_MIN_FLOW_STATUS 커스텀 완료: {len(_all_nfs_rows)}행")


@intent_handler
class TagDailyMissingHandler(IntentHandler):
    """태그 결측 분석 — 청크 직접 쿼리 early-return (fn_tag_daily_summary 대체)."""
    intents = ("TAG_DAILY_MISSING_SUMMARY",)

    async def pre_sql(self, ctx: IntentContext) -> None:
        from response_builder import (
            apply_corrections_to_answer,
            build_success_response,
            render_answer_template,
        )
        from sql_executor import _execute_tag_daily_summary_query

        _tdm_sn = ctx.params.get("sitename", "")
        _tdm_ft = ctx.params.get("facilitytype", "")
        _tdm_from = ctx.params.get("from_ts", "")
        _tdm_to = ctx.params.get("to_ts", "")
        _tdm_di = ctx.params.get("datainfo", "")
        try:
            _tdm_rows, _tdm_cols = await asyncio.to_thread(
                _execute_tag_daily_summary_query,
                _tdm_from, _tdm_to, _tdm_sn, _tdm_ft, _tdm_di,
            )
            if _tdm_rows:
                save_csv = service("save_csv")
                csv_fn = save_csv(_tdm_rows, _tdm_cols, ctx.intent, ctx.session_id)
                _total = len(_tdm_rows)
                _resp_data = [dict(zip(_tdm_cols, r)) for r in _tdm_rows]
                _tdm_rendered = (
                    render_answer_template(ctx.answer_template, {**ctx.params, "total_count": str(_total)})
                    if isinstance(ctx.answer_template, dict) else {}
                )
                _tdm_rendered = apply_corrections_to_answer(_tdm_rendered, ctx.params)
                ctx.final_response = build_success_response(
                    intent=ctx.intent, answer=_tdm_rendered, graph_type=ctx.graph_type,
                    data=_resp_data, columns=_tdm_cols, csv_file=csv_fn,
                    session_id=ctx.session_id, intent_candidates=ctx.intent_candidates,
                    total_rows=_total,
                )
        except Exception as e:
            logger.warning(f"SSE 결측분석 청크 쿼리 실패, 원본 함수 폴백: {e}")
