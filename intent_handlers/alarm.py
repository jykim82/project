"""알람 계열 인텐트 핸들러 — 2단계 2차 이관 (본문 로직 인라인 분기에서 그대로)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from .base import IntentContext, IntentHandler, intent_handler

logger = logging.getLogger(__name__)


@intent_handler
class OngoingAlarmHandler(IntentHandler):
    """진행중 알람 — tb_equipment_alarm_report 직접 조회 SQL 생성."""
    intents = ("ONGOING_ALARM_STATUS",)

    async def pre_sql(self, ctx: IntentContext) -> None:
        from response_builder import _sql_escape_literal

        where_parts = ["alarm_status = '진행중'"]
        _site = ctx.params.get("sitename")
        _category = ctx.params.get("datainfo")
        if _site:
            where_parts.append(f"sitename = '{_sql_escape_literal(_site)}'")
        if _category:
            where_parts.append(f"alarm_category = '{_sql_escape_literal(_category)}'")
        where_clause = " AND ".join(where_parts)
        ctx.sql = (
            f"SELECT sitename, facilitytype, alarm_msg, alarm_category,"
            f" TO_CHAR(alarm_start_time, 'YYYY-MM-DD HH24:MI:SS') AS alarm_start_time"
            f" FROM tb_equipment_alarm_report"
            f" WHERE {where_clause}"
            f" ORDER BY alarm_start_time DESC;"
        )


@intent_handler
class AlarmAbnormalLocationsHandler(IntentHandler):
    """경보 이상 발생 지점 — 동적 필터 SQL + answer_template 오버라이드."""
    intents = ("ALARM_ABNORMAL_LOCATIONS",)

    async def pre_sql(self, ctx: IntentContext) -> None:
        from response_builder import _sql_escape_literal
        from sql_executor import _extract_alarm_filter, _extract_alarm_level

        alarm_filter_clause, alarm_label = _extract_alarm_filter(ctx.question)
        alarm_level_clause, alarm_level_label = _extract_alarm_level(ctx.question)
        _ftype = ctx.params.get("facilitytype", "")

        where_parts = ["alarm_status = '진행중'"]
        _ftype_esc = ""
        if _ftype:
            _ftype_esc = _sql_escape_literal(_ftype)
            where_parts.append(f"facilitytype = '{_ftype_esc}'")
        where_base = " AND ".join(where_parts)
        if alarm_filter_clause:
            where_base += f" {alarm_filter_clause}"
        if alarm_level_clause:
            where_base += f" {alarm_level_clause}"

        ctx.sql = (
            f"SELECT sitename, facilitytype, alarm_msg, alarm_category,"
            f" TO_CHAR(alarm_start_time, 'YYYY-MM-DD HH24:MI:SS') AS alarm_start_time,"
            f" alarm_status"
            f" FROM tb_equipment_alarm_report"
            f" WHERE {where_base}"
            f" ORDER BY alarm_start_time DESC"
            f" LIMIT 100;"
        )
        # 폴백용 필터 정보를 params 에 저장
        ctx.params["_alarm_where_filter"] = alarm_filter_clause
        ctx.params["_alarm_where_level"] = alarm_level_clause
        ctx.params["_alarm_where_ftype"] = f"facilitytype = '{_ftype_esc}'" if _ftype else ""
        ctx.params["_alarm_label"] = alarm_label
        ctx.params["_alarm_level_label"] = alarm_level_label

        _filter_desc = " ".join(p for p in [_ftype, alarm_label, alarm_level_label] if p)
        _subject = f"{_filter_desc} 경보" if _filter_desc else "경보"
        ctx.answer_template = {
            "summary": _subject + " 발생 지점은 다음과 같습니다. (총 {total_alarm_count}건)",
            "detail": [
                {"prefix": "•", "text": "{category_summary}"},
                {"prefix": "", "text": "{alarm_location_detail_block}"},
            ],
            "recommend_questions": {
                "title": "다음은 추천질의입니다.",
                "items": [
                    {"prefix": "1.", "text": "현재 진행중인 알람은?"},
                    {"prefix": "2.", "text": "경보 발생원인 진단 순위를 알려줘"},
                    {"prefix": "3.", "text": "전체 이상 스캔해줘"},
                ]
            }
        }


@intent_handler
class AlarmPieHandler(IntentHandler):
    """경보 순위 — sitename/facilitytype 선택적 + 카테고리 필터 + 기본값.

    사용자 메시지에 시설 명시가 없으면 세션 컨텍스트를 무시한다 (2026-05-10 fix).
    """
    intents = ("FACILITY_ALARM_CAUSE_DIAGNOSIS_RANK", "FACILITY_ALARM_TOP_COUNT")

    async def pre_sql(self, ctx: IntentContext) -> None:
        from intent_matching import extract_facility_type_from_question, extract_limit, extract_sitename
        from sql_executor import _extract_alarm_filter

        _u_sitename = extract_sitename(ctx.raw_question)
        _u_facilitytype = extract_facility_type_from_question(ctx.raw_question)
        if not _u_sitename or not ctx.params.get("sitename") or ctx.params.get("sitename") == "%%":
            ctx.params["sitename"] = ""
        if not _u_facilitytype or not ctx.params.get("facilitytype") or ctx.params.get("facilitytype") == "%%":
            ctx.params["facilitytype"] = ""
        clause, label = _extract_alarm_filter(ctx.raw_question)
        ctx.params["alarm_filter_clause"] = clause
        ctx.params["alarm_label"] = label
        if not ctx.params.get("limit"):
            ctx.params["limit"] = str(extract_limit(ctx.raw_question))
        if not ctx.params.get("from_ts"):
            ctx.params["from_ts"] = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        if not ctx.params.get("to_ts"):
            ctx.params["to_ts"] = datetime.now().strftime("%Y-%m-%d")
