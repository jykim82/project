"""트렌드 계열 인텐트 핸들러 — 2단계 2차 이관 (본문 로직 인라인 분기에서 그대로)."""
from __future__ import annotations

import asyncio
import logging

from .base import IntentContext, IntentHandler, intent_handler

logger = logging.getLogger(__name__)


def _is_night_min_flow_q(intent: str, question: str) -> bool:
    return intent == "FACILITY_TREND" and "야간최소유량" in question.replace(" ", "")


@intent_handler
class FacilityTrendHandler(IntentHandler):
    """시설 트렌드 — 야간최소유량 fast-path + answer_template 오버라이드."""
    intents = ("FACILITY_TREND",)

    async def post_process(self, ctx: IntentContext, processed_data: dict) -> None:
        _ft = ctx.params.get("from_ts", "")
        _tt = ctx.params.get("to_ts", "")
        if _ft and _tt:
            processed_data["period_desc"] = f"{_ft} ~ {_tt}"

    async def prepare(self, ctx: IntentContext) -> None:
        # 야간최소유량 + sitename 미추출 → '전체' 기본값
        if ("야간최소유량" in ctx.question.replace(" ", "")
                and not ctx.params.get("sitename")):
            ctx.params["sitename"] = "전체"
            logger.info("[SSE] 야간최소유량 sitename 미추출 → '전체' 기본값 적용")

    async def pre_sql(self, ctx: IntentContext) -> None:
        if _is_night_min_flow_q(ctx.intent, ctx.question):
            await self._night_min_flow_fast_path(ctx)
            return
        _site = ctx.params.get("sitename", "")
        _ftype = ctx.params.get("facilitytype", "")
        _dinfo = ctx.params.get("datainfo", "")
        _user_period = ctx.params.get("user_specified_period", False)
        _ft = ctx.params.get("from_ts", "")
        _tt = ctx.params.get("to_ts", "")
        if _user_period:
            _period_line = f"{_ft} ~ {_tt} 기간의 데이터를 표출합니다."
        else:
            _period_line = "기간 설정이 없는 경우는 최근 7일간 데이터를 표출합니다."
        ctx.answer_template = {
            "summary": f"{_period_line}\n{_site} {_ftype} {_dinfo} 트렌드는 다음과 같습니다.",
            "recommend_questions": {
                "title": "다음은 추천질의입니다.",
                "items": [
                    {"prefix": "1.", "text": f"한달간 {_site} {_ftype} {_dinfo} 트렌드를 보여줘"},
                    {"prefix": "2.", "text": f"최근 3개월 {_site} {_ftype} {_dinfo} 트렌드를 보여줘"},
                    {"prefix": "3.", "text": f"{_site} {_ftype} {_dinfo} 트렌드 그래프를 보여줘"},
                ]
            }
        }

    async def _night_min_flow_fast_path(self, ctx: IntentContext) -> None:
        """야간최소유량: 기간 1년 보정 + 전용 템플릿 + 사전집계 직접 조회 (23s→<0.5s)."""
        from datetime import datetime, timedelta

        from sql_executor import _execute_night_min_flow_query

        # 기본 기간: 1년 (from_ts/to_ts 가 기본 7일로 설정된 경우 오버라이드)
        _ft = ctx.params.get("from_ts", "")
        _tt = ctx.params.get("to_ts", "")
        if _ft and _tt:
            try:
                _ft_date = datetime.strptime(_ft.strip("'"), "%Y-%m-%d")
                _tt_date = datetime.strptime(_tt.strip("'"), "%Y-%m-%d")
                if (_tt_date - _ft_date).days <= 7:
                    _tt_date = datetime.now()
                    _ft_date = _tt_date - timedelta(days=365)
                    ctx.params["from_ts"] = _ft_date.strftime("%Y-%m-%d")
                    ctx.params["to_ts"] = _tt_date.strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                pass
        # '%%' 와일드카드 대신 '전체' 사용
        if ctx.params.get("sitename") == "%%":
            ctx.params["sitename"] = "전체"
        _site = ctx.params.get("sitename", "")
        _ftype_t = ctx.params.get("facilitytype", "소블록")
        ctx.answer_template = {
            "summary": "기간 설정이 없는 경우는 최근 1년 기준으로 1달 단위 데이터를 표출합니다.\n{sitename} {facilitytype} 야간최소유량 트렌드는 다음과 같습니다.",
            "detail": [
                {"prefix": "•", "text": "야간 최소유량은 60분 단위 이동평균 계산법을 적용하여 계산됩니다."}
            ],
            "recommend_questions": {
                "title": "다음은 추천 질의입니다.",
                "items": [
                    {"prefix": "1.", "text": f"{_site} {_ftype_t} 야간최소유량 트렌드 그래프를 보여줘"},
                    {"prefix": "2.", "text": f"{_site} {_ftype_t} 야간최소유량 표준편차분석을 통해 이상여부를 확인해줘"},
                    {"prefix": "3.", "text": f"{_site} {_ftype_t} 데이터 결측분석결과를 알려줘"},
                ]
            }
        }

        # 야간최소유량 시계열에는 범용 트렌드 비교(평소 대비/향후 전망)를
        # 붙이지 않는다 — 일별 야간최소(예: 20)를 원시 시간평균 기준선
        # (예: 64)과 비교해 무의미한 편차(-68%)에 '정상' 배지가 붙던 결함
        # (2026-09-01 남산1 실사). NMF 이상 판정은 누수 CUSUM 이 전담.
        # E-037: 턴 파생 params 는 `_` 접두.
        ctx.params["_nmf_series"] = True

        _nmf_sn = ctx.params.get("sitename", "전체")
        _nmf_ft = ctx.params.get("facilitytype", "소블록")
        _nmf_from = ctx.params.get("from_ts", "")
        _nmf_to = ctx.params.get("to_ts", "")
        try:
            _site_list = [_nmf_sn]
            if ctx.extra_sitenames:
                _site_list.extend(ctx.extra_sitenames)
                ctx.extra_sitenames = None  # 이중 처리 방지
            _all_rows: list = []
            _cols_ref = None
            for _sn_i in _site_list:
                _r_i, _c_i = await asyncio.to_thread(
                    _execute_night_min_flow_query, _sn_i, _nmf_ft, _nmf_from, _nmf_to
                )
                if _r_i:
                    _all_rows.extend(_r_i)
                    if _cols_ref is None:
                        _cols_ref = _c_i
            ctx.rows = _all_rows
            ctx.columns = _cols_ref or []
            if len(_site_list) > 1:
                ctx.params["sitename"] = ", ".join(_site_list)
            logger.info(f"[SSE] 야간최소유량 사전집계 조회 완료: {len(ctx.rows)}행")
        except Exception as e:
            logger.warning(f"[SSE] 야간최소유량 테이블 조회 실패: {e}")
            ctx.rows, ctx.columns = [], []


class _TrendVarsMixin:
    """트렌드 계열 공통 — 렌더링 템플릿 변수 보충."""

    async def post_process(self, ctx: IntentContext, processed_data: dict) -> None:
        _ft = ctx.params.get("from_ts", "")
        _tt = ctx.params.get("to_ts", "")
        if _ft and _tt:
            processed_data["period_desc"] = f"{_ft} ~ {_tt}"
        if ctx.intent == "FACILITY_MIXED_TREND":
            processed_data["digital_label"] = ctx.params.get("digital_datainfo") or "밸브"
            processed_data["analog_label"] = ctx.params.get("analog_datainfo") or "유량"


@intent_handler
class MixedTrendHandler(_TrendVarsMixin, IntentHandler):
    """아날로그+디지털 혼합 트렌드 — answer_template 오버라이드."""
    intents = ("FACILITY_MIXED_TREND",)

    async def pre_sql(self, ctx: IntentContext) -> None:
        _site = ctx.params.get("sitename", "")
        _ftype = ctx.params.get("facilitytype", "")
        _analog = ctx.params.get("analog_datainfo") or "유량"
        _digital = ctx.params.get("digital_datainfo") or "밸브"
        _user_period = ctx.params.get("user_specified_period", False)
        _ft = ctx.params.get("from_ts", "")
        _tt = ctx.params.get("to_ts", "")
        if _user_period:
            _period_line = f"{_ft} ~ {_tt} 기간의 데이터를 표출합니다."
        else:
            _period_line = "기간 설정이 없는 경우는 최근 7일간 데이터를 표출합니다."
        ctx.answer_template = {
            "summary": f"{_period_line}\n{_site} {_ftype}의 {_digital} 가동 상태와 {_analog} 데이터 트렌드는 다음과 같습니다.",
            "recommend_questions": {
                "title": "다음은 추천질의입니다.",
                "items": [
                    {"prefix": "1.", "text": f"한달간 {_site} {_ftype} {_digital} 가동상태와 {_analog}을 함께 트렌드로 보여줘"},
                    {"prefix": "2.", "text": f"최근 3개월 {_site} {_ftype} {_analog} 트렌드를 보여줘"},
                    {"prefix": "3.", "text": f"{_site} {_ftype} {_digital} 가동상태와 {_analog}을 함께 트렌드로 보여줘"},
                ]
            }
        }


@intent_handler
class CatalogTrendHandler(IntentHandler):
    """카탈로그 트렌드 표 — 2단계 청크 직접 쿼리 (성능 최적화)."""
    intents = ("FACILITY_CATALOG_TREND_TABLE",)

    async def pre_sql(self, ctx: IntentContext) -> None:
        from sql_executor import _execute_catalog_trend_query, _get_catalog_trend_filter

        _ft = ctx.params.get("facilitytype", "배수지")
        _sn = ctx.params.get("sitename", "%%")
        _di = ctx.params.get("datainfo", "")
        _from = ctx.params.get("from_ts", "")
        _to = ctx.params.get("to_ts", "")

        trend_name_filter, label_pattern, display_name = _get_catalog_trend_filter(ctx.question, _di)
        ctx.params["datainfo"] = display_name
        logger.info(f"FACILITY_CATALOG_TREND_TABLE SQL: ft={_ft}, sn={_sn}, tn={trend_name_filter}, lbl={label_pattern}")

        try:
            _cat_rows, _cat_cols = await asyncio.to_thread(
                _execute_catalog_trend_query,
                _ft, _sn, trend_name_filter, label_pattern, _from, _to,
            )
            if _cat_rows:
                ctx.rows = _cat_rows
                ctx.columns = _cat_cols
        except Exception as e:
            logger.error(f"[SSE] FACILITY_CATALOG_TREND_TABLE 쿼리 실패: {e}")
