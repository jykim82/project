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

from korean_fuzzy import find_best_match
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
    "FACILITY_CATALOG_TREND_TABLE",
}

# SLM 날짜 추출 프롬프트
_DATE_EXTRACT_PROMPT = (
    "오늘: {today}. 질문에서 날짜 범위를 추출하세요.\n"
    '"최근 7일" = 7일 전~오늘, "한달간" = 30일 전~오늘, "이번주" = 월요일~오늘\n'
    "없으면 NONE.\n"
    '질문: "{question}"\n'
    'JSON만 출력: {{"from_ts": "YYYY-MM-DD 또는 NONE", "to_ts": "YYYY-MM-DD 또는 NONE"}}'
)


# 하드코딩 키워드 목록 — fuzzy 매칭 후보
_FACILITYTYPE_CANDIDATES = ["배수지", "가압장", "감압시설", "소블록", "소소블록", "수압계실", "유량계실"]
# fuzzy 매칭 후보 → (후보, 정규값) — 정규값이 None이면 후보 자체가 반환값
_DATAINFO_CANDIDATES = [
    "유량순시", "순시유량", "유량적산", "적산유량", "적산",
    "유량", "수위", "압력", "밸브", "펌프",
    "가압펌프", "배수펌프", "송수펌프", "유입밸브", "유출밸브",
]

# fuzzy 매칭 결과 → 정규 datainfo 매핑
_DATAINFO_CANONICAL = {
    "순시유량": "유량순시",
    "적산유량": "유량적산",
    "적산": "유량적산",
}

# fuzzy 매칭 임계값
_FUZZY_THRESHOLD_SITE = 0.6       # 현장명 (2~3글자, 임계값 낮게)
_FUZZY_THRESHOLD_KEYWORD = 0.65   # 시설유형/데이터 키워드

# sitename fuzzy 매칭에서 제외할 일반 단어 (false positive 방지)
_SITENAME_FUZZY_STOPWORDS = {
    # 시설유형/블록
    "배수지", "가압장", "감압", "소블록", "중블록", "대블록",
    # 데이터 키워드
    "유량", "압력", "수위", "밸브", "펌프", "가압", "유입", "유출", "적산", "순시",
    # 일반 동사/명사
    "출력", "데이터", "야간", "최소", "트렌드", "그래프", "현황", "상태",
    "분석", "결과", "기간", "전체", "최근", "한달", "결측", "이상",
    "알람", "진행", "요약", "표준", "편차", "합계", "평균", "건수",
    # 시설 관련 일반 용어
    "전단", "후단", "가동", "계통", "설비", "통신", "네트워크", "매뉴얼",
    # 동사형 (fuzzy에서 데이터 키워드로 오인 방지)
    "알려", "보여", "해줘", "줘요", "일반", "운영", "정보", "얼마",
    # 이상감지 키워드 (fuzzy에서 제외)
    "이력", "히스토리", "기록", "예측", "예상", "전망", "비교", "대비",
    "패턴", "시간대", "주기", "스캔", "감지", "점검", "진단", "정밀",
    "위험",
}


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
        self._corrections: list[dict] = []  # 오타 보정 이력

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
        # 세션 상태 초기화
        self._month_only = None
        self._corrections = []

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

        # 사용자가 기간을 명시했는지 추적 (프로그래밍적 추출 기준, SLM 추출 전)
        user_specified_period = from_ts is not None or to_ts is not None

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

        # 날짜 필요 INTENT인데 from_ts/to_ts 모두 없으면 기본 7일 적용
        if intent_name in DATE_REQUIRED_INTENTS and from_ts is None and to_ts is None:
            today = datetime.now()
            from_ts = (today - timedelta(days=7)).strftime("%Y-%m-%d")
            to_ts = today.strftime("%Y-%m-%d")

        # FACILITY_TREND: datainfo 미추출 시 시설유형별 기본값 자동 지정
        if intent_name in ("FACILITY_TREND",) and not datainfo:
            _DEFAULT_DATAINFO = {
                "배수지": "수위",
                "가압장": "압력",
                "감압시설": "압력",
                "소블록": "유량",
                "소소블록": "유량",
                "중블록": "유량",
                "대블록": "유량",
            }
            _ft = facilitytype or block_level
            if _ft and _ft in _DEFAULT_DATAINFO:
                datainfo = _DEFAULT_DATAINFO[_ft]
                logger.info(f"datainfo 기본값 적용: {_ft} → {datainfo}")
            else:
                # 시설유형도 없으면 수위를 기본으로
                datainfo = "수위"
                logger.info("datainfo 기본값 적용: (미지정) → 수위")

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
        if self._corrections:
            result["_corrections"] = self._corrections
        if self._month_only is not None:
            result["_month_only"] = self._month_only

        # 다중 sitename 추출 (위치순, 메인 sitename 외 추가분)
        all_sites = self._extract_all_sitenames(question)
        if len(all_sites) > 1:
            # 위치순 첫 번째를 메인 sitename으로 사용
            result["sitename"] = all_sites[0]
            result["_extra_sitenames"] = all_sites[1:]

            # 시설별 datainfo 페어링 (MIXED_TREND 등에서 시설마다 다른 데이터 요청 시)
            facility_pairs = self._extract_facility_datainfo_pairs(question)
            if facility_pairs:
                result["_facility_pairs"] = facility_pairs

        return result

    # =========================================================================
    # Phase 1: 프로그래밍적 추출 (기존 로직 재사용)
    # =========================================================================

    def _extract_sitename(self, question: str) -> Optional[str]:
        # 실제 sitename을 먼저 찾고, 없으면 "전체" → 와일드카드
        # 공백 제거 버전도 함께 검색 ("합덕 일반산단" → "합덕일반산단")
        q_nospace = question.replace(" ", "")
        for site in self._sitenames:
            for q in (question, q_nospace):
                idx = q.find(site)
                if idx >= 0:
                    # 경계 검사: 매칭된 이름 뒤에 연속 문자(-, 숫자)가 있으면 부분 매칭 → 건너뜀
                    end_idx = idx + len(site)
                    if end_idx < len(q):
                        next_char = q[end_idx]
                        if next_char == '-' or next_char.isdigit():
                            continue
                    return site
        if "전체" in question:
            return "%%"

        # fuzzy fallback: 질문에서 2~4글자 한글 토큰을 추출하여 현장명 후보와 비교
        tokens = re.findall(r"[가-힣]{2,4}", question)
        for token in tokens:
            if token in _SITENAME_FUZZY_STOPWORDS:
                continue
            match = find_best_match(token, self._sitenames, _FUZZY_THRESHOLD_SITE)
            if match:
                corrected, score = match
                if corrected != token:  # exact match가 아닌 경우만 보정 기록
                    self._corrections.append({
                        "field": "sitename",
                        "original": token,
                        "corrected": corrected,
                        "score": round(score, 3),
                    })
                    logger.info(f"sitename fuzzy 보정: '{token}' → '{corrected}' (score={score:.3f})")
                return corrected

        return None

    def _extract_all_sitenames(self, question: str) -> list:
        """질문에서 매칭되는 모든 sitename을 추출한다 (긴 이름 우선, 위치순 반환)."""
        found = []
        used_ranges = []
        # 공백 제거 버전도 시도 ("합덕 일반산단" → "합덕일반산단")
        q_nospace = question.replace(" ", "")
        for site in self._sitenames:  # longest-first 정렬 상태
            start = 0
            # 원본 question에서 먼저 검색
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
            # 공백 제거 버전에서 추가 검색 (원본에서 못 찾은 경우)
            if not any(site == s for _, s in found):
                idx = q_nospace.find(site)
                if idx >= 0:
                    end_idx = idx + len(site)
                    if end_idx < len(q_nospace):
                        next_char = q_nospace[end_idx]
                        if next_char == '-' or next_char.isdigit():
                            continue
                    overlap = any(site == s for _, s in found)
                    if not overlap:
                        found.append((idx, site))
                        used_ranges.append((idx, end_idx))
        found.sort(key=lambda x: x[0])
        result = [site for _, site in found]
        # 실제 sitename 없으면 "전체" → 와일드카드
        if not result and "전체" in question:
            return ["%%"]
        return result

    def _extract_facility_datainfo_pairs(self, question: str) -> list:
        """
        질문에서 시설별 (sitename, facilitytype, datainfo) 페어를 추출한다.
        텍스트를 sitename 위치 기준으로 분할하여 각 시설에 어떤 데이터가 요청되었는지 파악.

        예: "신평 배수지 수위와 천의리 가압장 가동펌프상태"
        → [
            {"sitename":"신평", "facilitytype":"배수지", "analog_datainfo":"수위", "digital_datainfo":None, "data_type":"analog"},
            {"sitename":"천의리", "facilitytype":"가압장", "analog_datainfo":None, "digital_datainfo":"펌프", "data_type":"digital"},
          ]
        """
        # 위치 기반 sitename 검색 (공백 제거 매칭 포함)
        found: list[tuple[int, str]] = []
        used_ranges: list[tuple[int, int]] = []
        q_nospace = question.replace(" ", "")
        for site in self._sitenames:
            start = 0
            matched = False
            while True:
                idx = question.find(site, start)
                if idx < 0:
                    break
                end_idx = idx + len(site)
                if end_idx < len(question):
                    next_char = question[end_idx]
                    if next_char == '-' or next_char.isdigit():
                        start = end_idx
                        continue
                overlap = any(idx < ue and end_idx > us for us, ue in used_ranges)
                if not overlap:
                    found.append((idx, site))
                    used_ranges.append((idx, end_idx))
                    matched = True
                start = end_idx
            # 공백 제거 버전에서 추가 검색
            if not matched:
                idx = q_nospace.find(site)
                if idx >= 0:
                    end_idx = idx + len(site)
                    ok = True
                    if end_idx < len(q_nospace):
                        next_char = q_nospace[end_idx]
                        if next_char == '-' or next_char.isdigit():
                            ok = False
                    if ok and not any(site == s for _, s in found):
                        found.append((idx, site))
                        used_ranges.append((idx, end_idx))
        found.sort(key=lambda x: x[0])

        if len(found) < 2:
            return []  # 단일 시설이면 기존 로직으로 충분

        # 텍스트를 sitename 위치 기준으로 세그먼트 분할
        pairs = []
        for i, (pos, sitename) in enumerate(found):
            seg_start = pos
            seg_end = found[i + 1][0] if i + 1 < len(found) else len(question)
            segment = question[seg_start:seg_end]

            # 세그먼트에서 facilitytype 추출
            facilitytype = None
            if "배수지" in segment:
                facilitytype = "배수지"
            elif "가압장" in segment:
                facilitytype = "가압장"
            elif "감압시설" in segment or "감압설비" in segment:
                facilitytype = "감압시설"

            # 세그먼트에서 analog/digital datainfo 추출
            analog = self._extract_analog_datainfo(segment)
            digital = self._extract_digital_datainfo(segment)

            if analog and digital:
                data_type = "mixed"
            elif analog:
                data_type = "analog"
            elif digital:
                data_type = "digital"
            else:
                data_type = "unknown"

            pairs.append({
                "sitename": sitename,
                "facilitytype": facilitytype,
                "analog_datainfo": analog,
                "digital_datainfo": digital,
                "data_type": data_type,
            })

        # 모든 시설이 동일 data_type이면 페어링 불필요 (기존 로직으로 충분)
        types = {p["data_type"] for p in pairs}
        if len(types) == 1 and "unknown" not in types:
            return []

        logger.info(f"시설별 datainfo 페어링: {pairs}")
        return pairs

    def _extract_block_level(self, question: str) -> Optional[str]:
        for level in self._block_levels:
            if level in question:
                # "소소블록"에서 "소블록" 부분 매칭 방지
                idx = question.index(level)
                if idx > 0 and question[idx - 1] == "소" and level == "소블록":
                    continue
                return level
        return None

    def _extract_facilitytype(
        self, question: str, block_level: Optional[str]
    ) -> Optional[str]:
        if block_level:
            return block_level
        # 긴 키워드 먼저 매칭 (소소블록 → 소블록 오인 방지)
        if "소소블록" in question:
            return "소소블록"
        if "소블록" in question:
            return "소블록"
        if "수압계실" in question:
            return "수압계실"
        if "유량계실" in question:
            return "유량계실"
        if "배수지" in question:
            return "배수지"
        if "가압장" in question:
            return "가압장"
        if "감압시설" in question or "감압설비" in question or "감압밸브" in question:
            return "감압시설"

        # fuzzy fallback: 2~4글자 한글 토큰에서 시설유형 매칭
        tokens = re.findall(r"[가-힣]{2,4}", question)
        for token in tokens:
            match = find_best_match(token, _FACILITYTYPE_CANDIDATES, _FUZZY_THRESHOLD_KEYWORD)
            if match:
                corrected, score = match
                if corrected != token:
                    self._corrections.append({
                        "field": "facilitytype",
                        "original": token,
                        "corrected": corrected,
                        "score": round(score, 3),
                    })
                    logger.info(f"facilitytype fuzzy 보정: '{token}' → '{corrected}' (score={score:.3f})")
                return corrected

        return None

    def _extract_datainfo(self, question: str) -> Optional[str]:
        # 복합 키워드 우선 매칭 (analog + digital 공통)
        _compound_keywords = [
            "유입유량", "유출유량", "유량순시", "순시유량", "유량적산", "적산유량",
            "유입압력", "유출압력", "배수지수위",
            "유입밸브", "유출밸브", "가압펌프", "배수펌프", "송수펌프",
        ]
        _compound_canonical = {
            "순시유량": "유량순시",
            "적산유량": "유량적산",
        }
        for kw in _compound_keywords:
            if kw in question:
                return _compound_canonical.get(kw, kw)

        # 단순 키워드 매칭
        if "압력" in question:
            return "압력"
        if "유량" in question:
            return "유량"
        if "수위" in question:
            return "수위"
        if "적산" in question:
            return "유량적산"
        if "밸브" in question:
            return "밸브"
        if "펌프" in question:
            return "펌프"

        # fuzzy fallback: 2~4글자 한글 토큰에서 데이터 키워드 매칭
        tokens = re.findall(r"[가-힣]{2,4}", question)
        for token in tokens:
            if token in _SITENAME_FUZZY_STOPWORDS:
                continue
            match = find_best_match(token, _DATAINFO_CANDIDATES, _FUZZY_THRESHOLD_KEYWORD)
            if match:
                corrected, score = match
                # 정규값 매핑 (적산→유량적산, 순시유량→유량순시 등)
                canonical = _DATAINFO_CANONICAL.get(corrected, corrected)
                if corrected != token:
                    self._corrections.append({
                        "field": "datainfo",
                        "original": token,
                        "corrected": canonical,
                        "score": round(score, 3),
                    })
                    logger.info(f"datainfo fuzzy 보정: '{token}' → '{canonical}' (score={score:.3f})")
                return canonical

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
        # FACILITY_MIXED_TREND용 — DB trend_name 일치 우선 (ex: "유입밸브상태")
        # 1) "상태" 접미사 포함 키워드 우선 (DB trend_name 정확 일치)
        full_keywords = [
            "유입밸브상태", "유출밸브상태", "가압펌프상태", "배수펌프상태", "송수펌프상태",
        ]
        for kw in full_keywords:
            if kw in question:
                return kw
        # 2) "상태" 없는 경우 → 접미사 자동 보정
        short_keywords = [
            ("유입밸브", "유입밸브상태"),
            ("유출밸브", "유출밸브상태"),
            ("가압펌프", "가압펌프상태"),
            ("배수펌프", "배수펌프상태"),
            ("송수펌프", "송수펌프상태"),
        ]
        for short, full in short_keywords:
            if short in question:
                return full
        # 3) 단순 키워드 → 디폴트 "상태" 접미사
        if "펌프" in question:
            return "가압펌프상태"
        if "밸브" in question:
            return "유입밸브상태"
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
