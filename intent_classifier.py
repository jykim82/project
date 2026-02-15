"""
intent_classifier.py
2단계 SLM Intent 분류기

Stage 1: 시설 유형 분류 (키워드 단축 + SLM 폴백)
Stage 2: 카테고리 내 구체적 INTENT 분류 (SLM)
최종 폴백: 기존 match_intent() 키워드 매칭
"""

import logging
import re
from typing import Optional

from ollama_client import OllamaClient, OllamaConnectionError
from intent_index import IntentIndex
from slm_config import ENABLE_KEYWORD_FALLBACK

logger = logging.getLogger(__name__)

# Stage 1 키워드 → 카테고리 매핑 (프로그래밍적 단축)
_KEYWORD_TO_CATEGORY = {
    "배수지": "배수지",
    "가압장": "가압장",
    "감압시설": "감압시설",
    "감압설비": "감압시설",
    "감압밸브": "감압시설",
    "소블록": "블록",
    "중블록": "블록",
    "대블록": "블록",
    "소소블록": "블록",
    "블록": "블록",
    "트렌드": "트렌드",
    "트랜드": "트렌드",
    "그래프": "트렌드",
}

# Stage 1 SLM 프롬프트
_STAGE1_PROMPT_TEMPLATE = (
    "당신은 상수도 운영 시스템 질의 분류기입니다.\n"
    "사용자의 질문을 아래 카테고리 중 하나로 분류하세요.\n"
    "카테고리: 배수지, 가압장, 감압시설, 블록, 공통, 트렌드, 범위외\n"
    '질문: "{question}"\n'
    "카테고리 이름만 출력:"
)

# Stage 2 SLM 프롬프트
_STAGE2_PROMPT_TEMPLATE = (
    "{category} 관련 의도 목록:\n"
    "{intent_list}\n"
    '질문: "{question}"\n'
    "의도 이름만 출력:"
)


class IntentClassifier:
    def __init__(self, ollama: OllamaClient, index: IntentIndex):
        self._ollama = ollama
        self._index = index

    def classify(
        self,
        question: str,
        keyword_fallback_fn=None,
    ) -> dict:
        """
        질문을 분류하여 결과를 반환한다.

        반환:
        {
            "intent_name": str or None,
            "category": str,           # "배수지", "블록", "범위외" 등
            "intent_def": dict or None, # example3.json 원본
            "method": str,              # "keyword", "slm", "fallback"
        }
        """
        # Stage 1: 시설 유형 분류
        category, stage1_method = self._classify_category(question)

        if category == "범위외":
            return {
                "intent_name": None,
                "category": "범위외",
                "intent_def": None,
                "method": stage1_method,
            }

        # Stage 2: 구체적 INTENT 분류
        intent_name, stage2_method = self._classify_intent(question, category)
        slm_failed = (stage2_method == "slm_error")

        if intent_name:
            intent_def = self._index.get_definition(intent_name)
            if intent_def:
                return {
                    "intent_name": intent_name,
                    "category": category,
                    "intent_def": intent_def,
                    "method": stage2_method,
                }

        # Stage 2 실패 시: 다른 카테고리도 시도
        # - "공통"이면 시설별 카테고리 시도
        # - 시설별 카테고리이면 "공통" 카테고리 시도
        fallback_categories = []
        if category == "공통":
            fallback_categories = [c for c in self._index.get_categories() if c != "공통"]
        else:
            fallback_categories = ["공통"]

        for alt_category in fallback_categories:
            alt_name, alt_method = self._classify_intent(question, alt_category, skip_slm=slm_failed)
            if alt_name:
                alt_def = self._index.get_definition(alt_name)
                if alt_def:
                    return {
                        "intent_name": alt_name,
                        "category": alt_category,
                        "intent_def": alt_def,
                        "method": alt_method,
                    }

        # 최종 폴백: 기존 키워드 매칭
        if ENABLE_KEYWORD_FALLBACK and keyword_fallback_fn:
            logger.info("SLM 분류 실패 → 기존 키워드 매칭 폴백")
            fallback_def = keyword_fallback_fn(question)
            if fallback_def:
                return {
                    "intent_name": fallback_def.get("intent"),
                    "category": category,
                    "intent_def": fallback_def,
                    "method": "fallback",
                }

        return {
            "intent_name": None,
            "category": category,
            "intent_def": None,
            "method": stage1_method,
        }

    def _classify_category(self, question: str) -> tuple:
        """
        Stage 1: 시설 유형 분류
        반환: (category, method)

        우선순위: 공통 키워드 → 시설 키워드 → SLM
        "야간최소유량", "알람" 등 공통 INTENT 키워드가 시설명보다 우선한다.
        """
        # "트렌드"/"트랜드"/"그래프"는 다른 공통 키워드보다 우선 (야간최소유량 트렌드 등)
        if "트렌드" in question or "트랜드" in question or "그래프" in question:
            logger.info("Stage1 키워드 매칭: '트렌드/그래프' → 트렌드")
            return "트렌드", "keyword"

        # 공통 키워드 체크 (시설 키워드보다 우선)
        common_keywords = [
            "야간최소유량", "야간 최소유량", "최소유량", "야간",
            "알람", "경보", "알림", "태그", "통신",
            "결측", "진행중",
            "표로",
            "이상",
            "표준편차",
            "주소",
        ]
        for kw in common_keywords:
            if kw in question:
                logger.info(f"Stage1 키워드 매칭: '{kw}' → 공통")
                return "공통", "keyword"

        # 시설 키워드 단축 (~70% 케이스)
        for keyword, category in _KEYWORD_TO_CATEGORY.items():
            if keyword in question:
                logger.info(f"Stage1 키워드 매칭: '{keyword}' → {category}")
                return category, "keyword"

        # SLM 호출
        try:
            prompt = _STAGE1_PROMPT_TEMPLATE.format(question=question)
            response = self._ollama.generate(prompt)
            response = response.strip().strip('"').strip("'")
            logger.info(f"Stage1 SLM 응답: '{response}'")

            valid_categories = list(self._index.get_categories()) + ["범위외"]
            for cat in valid_categories:
                if cat in response:
                    return cat, "slm"

            logger.warning(f"Stage1 SLM 응답이 유효하지 않음: '{response}'")
            return "공통", "slm"

        except OllamaConnectionError as e:
            logger.warning(f"Stage1 Ollama 연결 실패: {e}")
            return "공통", "keyword"

    def _classify_intent(self, question: str, category: str, skip_slm: bool = False) -> tuple:
        """
        Stage 2: 카테고리 내 구체적 INTENT 분류
        반환: (intent_name or None, method)
        """
        # 키워드 단축: 기간 키워드 + 데이터 키워드 → FACILITY_TAG_DATA_TABLE (cross-category)
        # 금일/오늘/최근/N분/N시간/N일 등 기간이 있으면 테이블 형식
        # 유량은 전용 인텐트(적산/순시 등)가 많으므로 제외
        _has_period = (
            "금일" in question or "오늘" in question
            or re.search(r"\d+\s*분", question)
            or re.search(r"\d+\s*시간", question)
            or re.search(r"\d+\s*일", question)
        )
        _TAG_DATA_EXCLUDE = ["적산", "순시", "표로"]
        if _has_period and not any(ek in question for ek in _TAG_DATA_EXCLUDE):
            for dkw in ["수위", "압력", "유량", "밸브"]:
                if dkw in question:
                    return "FACILITY_TAG_DATA_TABLE", "keyword"

        # 키워드 단축: "트렌드" 카테고리
        if category == "트렌드":
            if "함께" in question or "혼합" in question:
                return "FACILITY_MIXED_TREND", "keyword"
            return "FACILITY_TREND", "keyword"

        # 키워드 단축: 주소 → 시설별 ADDRESS_INFO
        if "주소" in question:
            if "배수지" in question:
                return "FACILITY_ADDRESS_INFO_RESERVOIR", "keyword"
            if "가압장" in question:
                return "FACILITY_ADDRESS_INFO_BOOSTER", "keyword"
            if any(kw in question for kw in ["소블록", "중블록", "대블록", "블록"]):
                return "FACILITY_ADDRESS_INFO_BLOCK", "keyword"
            if "감압" in question:
                return "FACILITY_ADDRESS_INFO_PRESSURE", "keyword"

        # 키워드 단축: 압력 현황
        if "압력" in question and ("현황" in question or "현항" in question):
            return "FACILITY_PRESSURE_STATUS", "keyword"

        # 키워드 단축: 알람 누적건수 / TOP / 순서
        if ("누적" in question or "순서" in question or "TOP" in question.upper()) and \
           ("알람" in question or "경보" in question or "알림" in question):
            return "FACILITY_ALARM_TOP_COUNT", "keyword"

        # 키워드 단축: 최근 알람
        if "최근" in question and ("알람" in question or "경보" in question or "알림" in question):
            return "FACILITY_RECENT_ALARM", "keyword"

        # 키워드 단축: 수위/압력 단독 → 태그 최신값 (다른 INTENT 키워드 제외)
        _TAG_LATEST_EXCLUDE = [
            "현황", "현항", "헌팅", "표", "적산", "순시",
            "알람", "경보", "알림", "통신", "주소", "이상",
            "결측", "표준편차", "밸브", "트렌드",
        ]
        if not any(ek in question for ek in _TAG_LATEST_EXCLUDE):
            for dkw in ["수위", "압력"]:
                if dkw in question:
                    return "FACILITY_TAG_LATEST_VALUE", "keyword"

        # 키워드 단축: 현재 + 유량 → 태그 최신값 (표/현황 제외)
        if "현재" in question and "표" not in question and "현황" not in question:
            if "유량" in question:
                return "FACILITY_TAG_LATEST_VALUE", "keyword"

        # 키워드 단축: 전체 평균사용량
        if "전체" in question and "평균사용량" in question:
            return "TODAY_RESERVOIR_AVG_USAGE_ALL", "keyword"

        # 키워드 단축: "공통" 카테고리
        if "결측" in question:
            return "TAG_DAILY_MISSING_SUMMARY", "keyword"
        if ("이상" in question or "고장" in question) and ("설비" in question or "현황" in question):
            return "FACILITY_ABNORMAL_STATUS_SUMMARY", "keyword"
        if "표준편차" in question:
            return "FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS", "keyword"
        if "진행중" in question or "진행 중" in question:
            return "ONGOING_ALARM_STATUS", "keyword"

        # 키워드 단축: 적산 관련
        if "적산" in question:
            if "표" in question or "보여" in question:
                return "FACILITY_FLOW_ACCUMULATED_TIMESERIES_TABLE", "keyword"
            return "TODAY_FLOW_ACCUMULATION", "keyword"

        # 키워드 단축: 순시 관련
        if "순시" in question and "유량" in question:
            return "FACILITY_FLOW_INSTANT_TIMESERIES_TABLE", "keyword"

        intent_list_str = self._index.build_category_prompt_segment(category)
        if not intent_list_str:
            return None, "keyword"

        if skip_slm:
            return None, "keyword"

        # SLM 호출
        try:
            prompt = _STAGE2_PROMPT_TEMPLATE.format(
                category=category,
                intent_list=intent_list_str,
                question=question,
            )
            response = self._ollama.generate(prompt)
            response = response.strip().strip('"').strip("'")
            logger.info(f"Stage2 SLM 응답: '{response}' (category={category})")

            # 응답에서 INTENT 이름 추출
            all_names = self._index.get_all_intent_names()
            for name in all_names:
                if name in response:
                    return name, "slm"

            logger.warning(f"Stage2 SLM 응답에서 유효한 INTENT를 찾지 못함: '{response}'")
            return None, "slm"

        except OllamaConnectionError as e:
            logger.warning(f"Stage2 Ollama 연결 실패: {e}")
            return None, "slm_error"
