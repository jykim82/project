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
    "FACILITY_DIGITAL_STATUS_TIMESERIES_TABLE",
    "FACILITY_FLOW_CURRENT_TABLE",
    "FACILITY_FLOW_INSTANT_TIMESERIES_TABLE",
    "FACILITY_FLOW_ACCUMULATED_TIMESERIES_TABLE",
    "FACILITY_VALVE_CURRENT_TABLE",
    "FACILITY_VALVE_STATUS_CURRENT_TABLE",
    "NIGHT_MIN_FLOW_STATUS",
    "NIGHT_MIN_FLOW_SUMMARY_TABLE",
    "FACILITY_ABNORMAL_STATUS_SUMMARY",
    "FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS",
    "TAG_DAILY_MISSING_SUMMARY",
    "FACILITY_ALARM_TOP_COUNT",
    "FACILITY_TAG_DATA_TABLE",
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
        # 월 단독 지정 플래그 초기화
        self._month_only = None

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

        # SLM이 from_ts > to_ts를 반환한 경우 무효화
        if from_ts and to_ts:
            try:
                # timestamp 형식("YYYY-MM-DD HH:MM:SS")과 date 형식("YYYY-MM-DD") 모두 지원
                fmt = "%Y-%m-%d %H:%M:%S" if " " in from_ts else "%Y-%m-%d"
                fmt2 = "%Y-%m-%d %H:%M:%S" if " " in to_ts else "%Y-%m-%d"
                if datetime.strptime(from_ts, fmt) > datetime.strptime(to_ts, fmt2):
                    logger.warning(f"SLM 날짜 무효: from_ts={from_ts} > to_ts={to_ts}, 초기화")
                    from_ts = None
                    to_ts = None
            except ValueError:
                from_ts = None
                to_ts = None

        # 사용자가 기간을 명시했는지 추적
        user_specified_period = from_ts is not None or to_ts is not None

        # 날짜 필요 INTENT인데 from_ts/to_ts 모두 없으면 기본 7일 적용
        if intent_name in DATE_REQUIRED_INTENTS and from_ts is None and to_ts is None:
            today = datetime.now()
            from_ts = (today - timedelta(days=7)).strftime("%Y-%m-%d")
            to_ts = today.strftime("%Y-%m-%d")

        # FACILITY_ABNORMAL_STATUS_SUMMARY: fn_realtime_missing_summary는 빈 문자열 = 전체
        if intent_name == "FACILITY_ABNORMAL_STATUS_SUMMARY":
            if not datainfo:
                datainfo = ""
            if not facilitytype:
                facilitytype = ""
            if not sitename:
                sitename = ""

        result = {
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
            "user_specified_period": user_specified_period,
        }
        if self._month_only is not None:
            result["_month_only"] = self._month_only

        # 다중 sitename 추출 (위치순, 메인 sitename 외 추가분)
        all_sites = self._extract_all_sitenames(question)
        if len(all_sites) > 1:
            # 위치순 첫 번째를 메인 sitename으로 사용
            result["sitename"] = all_sites[0]
            result["_extra_sitenames"] = all_sites[1:]

        return result

    # =========================================================================
    # Phase 1: 프로그래밍적 추출 (기존 로직 재사용)
    # =========================================================================

    def _extract_sitename(self, question: str) -> Optional[str]:
        # 실제 sitename을 먼저 찾고, 없으면 "전체" → 와일드카드
        for site in self._sitenames:
            idx = question.find(site)
            if idx >= 0:
                # 경계 검사: 매칭된 이름 뒤에 연속 문자(-, 숫자)가 있으면 부분 매칭 → 건너뜀
                # 예: "남산1"이 "남산1-1"에 매칭되면 안 됨
                end_idx = idx + len(site)
                if end_idx < len(question):
                    next_char = question[end_idx]
                    if next_char == '-' or next_char.isdigit():
                        continue
                return site
        # 실제 sitename 없으면 "전체" → 와일드카드
        if "전체" in question:
            return "%%"
        return None

    def _extract_all_sitenames(self, question: str) -> list:
        """질문에서 매칭되는 모든 sitename을 추출한다 (긴 이름 우선, 위치순 반환)."""
        found = []
        used_ranges = []
        for site in self._sitenames:  # longest-first 정렬 상태
            start = 0
            while True:
                idx = question.find(site, start)
                if idx < 0:
                    break
                end_idx = idx + len(site)
                # 경계 검사
                if end_idx < len(question):
                    next_char = question[end_idx]
                    if next_char == '-' or next_char.isdigit():
                        start = end_idx
                        continue
                # 중복 범위 검사
                overlap = any(idx < ue and end_idx > us for us, ue in used_ranges)
                if not overlap:
                    found.append((idx, site))
                    used_ranges.append((idx, end_idx))
                start = end_idx
        found.sort(key=lambda x: x[0])
        result = [site for _, site in found]
        # 실제 sitename 없으면 "전체" → 와일드카드
        if not result and "전체" in question:
            return ["%%"]
        return result

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
        # 유량 세분화: 유량순시/순시유량, 유량적산/적산유량 구분
        if "유량순시" in question or "순시유량" in question:
            return "유량순시"
        if "유량적산" in question or "적산유량" in question:
            return "유량적산"
        if "유량" in question:
            return "유량"
        if "수위" in question:
            return "수위"
        if "적산" in question:
            return "유량적산"
        if "밸브" in question:
            return "밸브"
        return None

    def _extract_limit(self, question: str) -> int:
        match = re.search(r"TOP\s*(\d+)", question, re.IGNORECASE)
        if match:
            return int(match.group(1))
        match = re.search(r"상위\s*(\d+)", question)
        if match:
            return int(match.group(1))
        match = re.search(r"(\d+)\s*개", question)
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
        """태그 조회용 datakey 추출 (수위, 압력, 유량 등)"""
        datakey_keywords = ["수위", "압력", "유량", "밸브", "펌프", "탁도", "염소", "온도", "전력"]
        for kw in datakey_keywords:
            if kw in question:
                return kw
        return None

    def _extract_analog_datainfo(self, question: str) -> Optional[str]:
        # FACILITY_MIXED_TREND용 — 복합 키워드 우선 매칭
        compound_keywords = [
            "유입유량", "유출유량", "유량순시", "순시유량", "유량적산", "적산유량",
            "유입압력", "유출압력", "배수지수위",
        ]
        for kw in compound_keywords:
            if kw in question:
                return kw
        simple_keywords = ["압력", "유량", "수위"]
        for kw in simple_keywords:
            if kw in question:
                return kw
        return None

    def _extract_digital_datainfo(self, question: str) -> Optional[str]:
        # FACILITY_MIXED_TREND용 — 복합 키워드 우선 매칭
        compound_keywords = [
            "유입밸브", "유출밸브", "가압펌프", "배수펌프", "송수펌프",
        ]
        for kw in compound_keywords:
            if kw in question:
                return kw
        simple_keywords = ["펌프", "밸브"]
        for kw in simple_keywords:
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

        # "최근 N분" (e.g., "최근 10분", "10분간")
        match = re.search(r"(\d+)\s*분", question)
        if match:
            minutes = int(match.group(1))
            from_dt = today - timedelta(minutes=minutes)
            return from_dt.strftime("%Y-%m-%d %H:%M:%S"), today.strftime("%Y-%m-%d %H:%M:%S")

        # "최근 N시간"
        match = re.search(r"(\d+)\s*시간", question)
        if match:
            hours = int(match.group(1))
            from_dt = today - timedelta(hours=hours)
            return from_dt.strftime("%Y-%m-%d %H:%M:%S"), today.strftime("%Y-%m-%d %H:%M:%S")

        # "최근 N일"
        match = re.search(r"최근\s*(\d+)\s*일", question)
        if match:
            days = int(match.group(1))
            from_date = today - timedelta(days=days)
            return from_date.strftime("%Y-%m-%d"), today_str

        # "N일간" (e.g., "7일간", "30일간") — "최근" 접두사 없이
        match = re.search(r"(\d+)\s*일간", question)
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

        # "YYYY년 M월 D일 ~ YYYY년 M월 D일" (부터/까지, ~, -, 에서)
        match = re.search(
            r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일\s*(?:부터|에서)?\s*"
            r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일\s*(?:까지)?",
            question,
        )
        if match:
            y1, m1, d1 = int(match.group(1)), int(match.group(2)), int(match.group(3))
            y2, m2, d2 = int(match.group(4)), int(match.group(5)), int(match.group(6))
            try:
                from_date = datetime(y1, m1, d1)
                to_date = datetime(y2, m2, d2)
                return from_date.strftime("%Y-%m-%d"), to_date.strftime("%Y-%m-%d")
            except ValueError:
                pass

        # "YYYY년 M월 D일" (단일 날짜)
        match = re.search(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", question)
        if match:
            year = int(match.group(1))
            month = int(match.group(2))
            day = int(match.group(3))
            try:
                target = datetime(year, month, day)
                return target.strftime("%Y-%m-%d"), target.strftime("%Y-%m-%d")
            except ValueError:
                pass

        # "YYYY년 N월" (e.g., "2025년 12월") — 년도 명시된 월 기간
        match = re.search(r"(\d{4})\s*년\s*(\d{1,2})\s*월", question)
        if match:
            year = int(match.group(1))
            month = int(match.group(2))
            if 1 <= month <= 12:
                from_date = datetime(year, month, 1)
                if month == 12:
                    to_date = datetime(year + 1, 1, 1)
                else:
                    to_date = datetime(year, month + 1, 1)
                last_day = to_date - timedelta(days=1)
                return from_date.strftime("%Y-%m-%d"), last_day.strftime("%Y-%m-%d")

        # "N월" (년도 없음) — 년도 확인 필요 플래그 설정
        match = re.search(r"(\d{1,2})\s*월", question)
        if match:
            month = int(match.group(1))
            if 1 <= month <= 12:
                self._month_only = month  # ai_server에서 확인용
                return None, None

        # "오늘", "금일", "현재"
        if "오늘" in question or "금일" in question or "현재" in question:
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
