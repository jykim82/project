"""야간최소유량·누수 계열 인텐트 핸들러 — 2단계 2차 이관."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from .base import IntentContext, IntentHandler, intent_handler

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


@intent_handler
class StddevAnalysisHandler(IntentHandler):
    """야간최소유량 표준편차 분석 — answer_template 오버라이드."""
    intents = ("FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS",)

    async def pre_sql(self, ctx: IntentContext) -> None:
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
    """CUSUM 누수 추정 — 기본 파라미터(전체/소블록/90일) 설정."""
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
