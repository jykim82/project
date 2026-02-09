"""
param_extractor.py
하이브리드 파라미터 추출기

Phase 1 (프로그래밍적): 기존 ai_server.py 추출 로직 재사용
Phase 2 (SLM): 날짜 추출 — TREND/TIMESERIES 계열 INTENT에만 적용

- 파라미터를 추론하지 않는다
- 추출 실패 시 None으로 남긴다
"""

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Optional

from ollama_client import OllamaClient, OllamaConnectionError

logger = logging.getLogger(__name__)

# 날짜 추출이 필요한 INTENT 목록
DATE_REQUIRED_INTENTS = {
    "FACILITY_TREND",
    "FACILITY_MIXED_TREND",
    "FACILITY_ANALOG_TIMESERIES_TABLE",
    "FACILITY_DIGITAL_TIMESERIES_TABLE",
    "FACILITY_FLOW_CURRENT_TABLE",
    "FACILITY_VALVE_CURRENT_TABLE",
    "NIGHT_MIN_FLOW_STATUS",
    "NIGHT_MIN_FLOW_SUMMARY_TABLE",
    "FACILITY_ABNORMAL_STATUS_SUMMARY",
    "TAG_DAILY_MISSING_SUMMARY",
}

# SLM 날짜 추출 프롬프트
_DATE_EXTRACT_PROMPT = (
    "오늘: {today}. 질문에서 날짜 범위를 추출하세요.\n"
    '"최근 7일" = 7일 전~오늘, "한달간" = 30일 전~오늘, "이번주" = 월요일~오늘\n'
    "없으면 NONE.\n"
    '질문: "{question}"\n'
    'JSON만 출력: {{"from_ts": "YYYY-MM-DD 또는 NONE", "to_ts": "YYYY-MM-DD 또는 NONE"}}'
)


class ParamExtractor:
    def __init__(
        self,
        known_sitenames: list,
        known_block_levels: list,
        ollama: Optional[OllamaClient] = None,
    ):
        self._sitenames = known_sitenames
        self._block_levels = known_block_levels
        self._ollama = ollama

    def extract_all(self, question: str, intent_name: Optional[str] = None) -> dict:
        """
        질문에서 모든 파라미터를 추출한다.

        반환:
        {
            "sitename": str or None,
            "facilitytype": str or None,
            "block_level": str or None,
            "datainfo": str or None,
            "limit": int,
            "alarm_msg": str or None,
            "from_ts": str or None,
            "to_ts": str or None,
            "datakey": str or None,
            "analog_datainfo": str or None,
            "digital_datainfo": str or None,
        }
        """
        # Phase 1: 프로그래밍적 추출
        block_level = self._extract_block_level(question)
        sitename = self._extract_sitename(question)
        facilitytype = self._extract_facilitytype(question, block_level)
        datainfo = self._extract_datainfo(question)
        limit_val = self._extract_limit(question)
        alarm_msg = self._extract_alarm_msg(question)
        datakey = self._extract_datakey(question)
        analog_datainfo = self._extract_analog_datainfo(question)
        digital_datainfo = self._extract_digital_datainfo(question)

        # Phase 2: SLM 날짜 추출 (해당 INTENT에만)
        from_ts = None
        to_ts = None

        # 프로그래밍적 날짜 추출 시도
        from_ts, to_ts = self._extract_date_programmatic(question)

        # 프로그래밍적으로 실패하고, 날짜 필요 INTENT이면 SLM 시도
        if (from_ts is None or to_ts is None) and intent_name in DATE_REQUIRED_INTENTS:
            if self._ollama:
                slm_from, slm_to = self._extract_date_slm(question)
                if from_ts is None and slm_from:
                    from_ts = slm_from
                if to_ts is None and slm_to:
                    to_ts = slm_to

        return {
            "sitename": sitename,
            "facilitytype": facilitytype,
            "block_level": block_level,
            "datainfo": datainfo,
            "limit": limit_val,
            "alarm_msg": alarm_msg,
            "from_ts": from_ts,
            "to_ts": to_ts,
            "datakey": datakey,
            "analog_datainfo": analog_datainfo,
            "digital_datainfo": digital_datainfo,
        }

    # =========================================================================
    # Phase 1: 프로그래밍적 추출 (기존 로직 재사용)
    # =========================================================================

    def _extract_sitename(self, question: str) -> Optional[str]:
        for site in self._sitenames:
            if site in question:
                return site
        return None

    def _extract_block_level(self, question: str) -> Optional[str]:
        for level in self._block_levels:
            if level in question:
                return level
        return None

    def _extract_facilitytype(
        self, question: str, block_level: Optional[str]
    ) -> Optional[str]:
        if block_level:
            return block_level
        if "배수지" in question:
            return "배수지"
        if "가압장" in question:
            return "가압장"
        if "감압시설" in question or "감압설비" in question or "감압밸브" in question:
            return "감압시설"
        return None

    def _extract_datainfo(self, question: str) -> Optional[str]:
        if "압력" in question:
            return "압력"
        if "유량" in question:
            return "유량"
        if "수위" in question:
            return "수위"
        return None

    def _extract_limit(self, question: str) -> int:
        match = re.search(r"TOP\s*(\d+)", question, re.IGNORECASE)
        if match:
            return int(match.group(1))
        match = re.search(r"상위\s*(\d+)", question)
        if match:
            return int(match.group(1))
        return 10

    def _extract_alarm_msg(self, question: str) -> Optional[str]:
        alarm_keywords = ["펌프", "수위", "압력", "통신", "유량", "고장"]
        for keyword in alarm_keywords:
            if keyword in question:
                return keyword
        return None

    def _extract_datakey(self, question: str) -> Optional[str]:
        # datakey는 태그 조회에서 사용 — 질문에서 직접 추출 어려움
        # 현재는 None 반환, 세션 병합으로 이전 턴에서 채워짐
        return None

    def _extract_analog_datainfo(self, question: str) -> Optional[str]:
        # FACILITY_MIXED_TREND용
        analog_keywords = ["압력", "유량", "수위"]
        for kw in analog_keywords:
            if kw in question:
                return kw
        return None

    def _extract_digital_datainfo(self, question: str) -> Optional[str]:
        # FACILITY_MIXED_TREND용
        digital_keywords = ["펌프", "밸브"]
        for kw in digital_keywords:
            if kw in question:
                return kw
        return None

    # =========================================================================
    # Phase 1.5: 프로그래밍적 날짜 추출
    # =========================================================================

    def _extract_date_programmatic(self, question: str) -> tuple:
        """
        질문에서 날짜를 프로그래밍적으로 추출한다.
        반환: (from_ts, to_ts) — str "YYYY-MM-DD" 또는 None
        """
        today = datetime.now()
        today_str = today.strftime("%Y-%m-%d")

        # "최근 N일"
        match = re.search(r"최근\s*(\d+)\s*일", question)
        if match:
            days = int(match.group(1))
            from_date = today - timedelta(days=days)
            return from_date.strftime("%Y-%m-%d"), today_str

        # "최근 N주"
        match = re.search(r"최근\s*(\d+)\s*주", question)
        if match:
            weeks = int(match.group(1))
            from_date = today - timedelta(weeks=weeks)
            return from_date.strftime("%Y-%m-%d"), today_str

        # "한달간", "최근 한달", "한 달"
        if re.search(r"(한\s*달|1개월|최근\s*한\s*달)", question):
            from_date = today - timedelta(days=30)
            return from_date.strftime("%Y-%m-%d"), today_str

        # "이번주"
        if "이번주" in question or "이번 주" in question:
            monday = today - timedelta(days=today.weekday())
            return monday.strftime("%Y-%m-%d"), today_str

        # "오늘", "금일"
        if "오늘" in question or "금일" in question:
            return today_str, today_str

        # "YYYY-MM-DD~YYYY-MM-DD" 직접 지정
        match = re.search(
            r"(\d{4}-\d{2}-\d{2})\s*[~\-]\s*(\d{4}-\d{2}-\d{2})", question
        )
        if match:
            return match.group(1), match.group(2)

        return None, None

    # =========================================================================
    # Phase 2: SLM 날짜 추출
    # =========================================================================

    def _extract_date_slm(self, question: str) -> tuple:
        """
        SLM으로 날짜 범위를 추출한다.
        반환: (from_ts, to_ts) — str "YYYY-MM-DD" 또는 None
        """
        today_str = datetime.now().strftime("%Y-%m-%d")

        try:
            prompt = _DATE_EXTRACT_PROMPT.format(today=today_str, question=question)
            response = self._ollama.generate(prompt)
            logger.info(f"SLM 날짜 추출 응답: '{response}'")

            # JSON 파싱 시도
            # 응답에서 JSON 부분만 추출
            json_match = re.search(r"\{[^}]+\}", response)
            if not json_match:
                return None, None

            data = json.loads(json_match.group())
            from_ts = data.get("from_ts")
            to_ts = data.get("to_ts")

            # "NONE" 문자열 처리
            if from_ts and from_ts.upper() == "NONE":
                from_ts = None
            if to_ts and to_ts.upper() == "NONE":
                to_ts = None

            # 날짜 형식 검증
            if from_ts:
                datetime.strptime(from_ts, "%Y-%m-%d")
            if to_ts:
                datetime.strptime(to_ts, "%Y-%m-%d")

            return from_ts, to_ts

        except OllamaConnectionError as e:
            logger.warning(f"SLM 날짜 추출 실패 (연결): {e}")
            return None, None
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning(f"SLM 날짜 추출 실패 (파싱): {e}")
            return None, None
