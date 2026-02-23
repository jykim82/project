"""
intent_classifier.py
3단계 SLM Intent 분류기

Stage 0: 키워드 규칙 (프로그래밍적 단축 — ~70% 즉시 확정)
Stage 1: 인메모리 벡터 유사도 검색 (numpy cosine — ~1ms)
Stage 2: SLM 분류 (Phi-4-mini — 폴백)
최종 폴백: 기존 match_intent() 키워드 매칭
"""

import logging
import re
from typing import Optional

from ollama_client import OllamaClient, OllamaConnectionError
from intent_index import IntentIndex
from intent_embeddings import (
    IntentEmbeddingIndex,
    embed_query,
    VECTOR_THRESHOLD,
    VECTOR_CANDIDATE_THRESHOLD,
)
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
    def __init__(
        self,
        ollama: OllamaClient,
        index: IntentIndex,
        embedding_index: Optional[IntentEmbeddingIndex] = None,
    ):
        self._ollama = ollama
        self._index = index
        self._embedding_index = embedding_index

    def classify(
        self,
        question: str,
        keyword_fallback_fn=None,
    ) -> dict:
        """
        질문을 분류하여 결과를 반환한다.

        분류 순서:
        1. 키워드 규칙 (Stage 0: _classify_category + _classify_intent)
        2. 벡터 유사도 검색 (Stage 1: cosine ≥ 0.75 즉시 확정)
        3. SLM 분류 (Stage 2: Phi-4-mini 폴백)

        반환:
        {
            "intent_name": str or None,
            "category": str,
            "intent_def": dict or None,
            "method": str,  # "keyword", "vector", "slm", "fallback"
            "vector_score": float or None,  # 벡터 검색 점수 (있을 때만)
            "vector_candidates": list or None,  # 벡터 후보 목록 (SLM 폴백 시)
        }
        """
        # Stage 0: 키워드 기반 카테고리 + 인텐트 분류
        category, stage0_method = self._classify_category(question)

        # 벡터 후보 미리 조회 (사후 보정 UI용)
        vector_candidates = self._get_vector_candidates(question)

        def _with_candidates(result: dict) -> dict:
            """확정된 인텐트를 제외한 벡터 후보를 결과에 첨부."""
            chosen = result.get("intent_name")
            alts = [c for c in vector_candidates if c["intent"] != chosen]
            # 중복 인텐트 제거 (같은 인텐트의 다른 질문이 여러 개일 수 있음)
            seen = set()
            unique_alts = []
            for c in alts:
                if c["intent"] not in seen:
                    seen.add(c["intent"])
                    unique_alts.append(c)
            result["intent_candidates"] = unique_alts[:3]
            return result

        # 키워드로 인텐트 확정 시도 (범위외가 아닌 경우에만)
        intent_name = None
        if category != "범위외":
            intent_name, keyword_method = self._classify_intent(question, category)
            if intent_name and keyword_method == "keyword":
                intent_def = self._index.get_definition(intent_name)
                if intent_def:
                    return _with_candidates({
                        "intent_name": intent_name,
                        "category": category,
                        "intent_def": intent_def,
                        "method": "keyword",
                    })

        # Stage 1: 벡터 유사도 검색 (범위외 포함)
        vector_result = self._classify_by_vector(question)
        if vector_result:
            return _with_candidates(vector_result)

        # 범위외 판정 후 벡터도 미확정이면 종료
        if category == "범위외":
            return _with_candidates({
                "intent_name": None,
                "category": "범위외",
                "intent_def": None,
                "method": stage0_method,
            })

        # Stage 2: SLM 분류 (기존 로직)
        if not intent_name:
            intent_name, stage2_method = self._classify_intent_slm(question, category)
            slm_failed = (stage2_method == "slm_error")
        else:
            stage2_method = "keyword"
            slm_failed = False

        if intent_name:
            intent_def = self._index.get_definition(intent_name)
            if intent_def:
                return _with_candidates({
                    "intent_name": intent_name,
                    "category": category,
                    "intent_def": intent_def,
                    "method": stage2_method,
                })

        # Stage 2 실패 시: 다른 카테고리도 시도
        fallback_categories = []
        if category == "공통":
            fallback_categories = [c for c in self._index.get_categories() if c != "공통"]
        else:
            fallback_categories = ["공통"]

        for alt_category in fallback_categories:
            alt_name, alt_method = self._classify_intent(question, alt_category, skip_slm=True)
            if alt_name:
                alt_def = self._index.get_definition(alt_name)
                if alt_def:
                    return _with_candidates({
                        "intent_name": alt_name,
                        "category": alt_category,
                        "intent_def": alt_def,
                        "method": alt_method,
                    })

        # 최종 폴백: 기존 키워드 매칭
        if ENABLE_KEYWORD_FALLBACK and keyword_fallback_fn:
            logger.info("SLM 분류 실패 → 기존 키워드 매칭 폴백")
            fallback_def = keyword_fallback_fn(question)
            if fallback_def:
                return _with_candidates({
                    "intent_name": fallback_def.get("intent"),
                    "category": category,
                    "intent_def": fallback_def,
                    "method": "fallback",
                })

        return _with_candidates({
            "intent_name": None,
            "category": category,
            "intent_def": None,
            "method": stage0_method,
        })

    def _classify_by_vector(self, question: str) -> Optional[dict]:
        """
        Stage 1: 인메모리 벡터 유사도 검색.
        cosine ≥ VECTOR_THRESHOLD(0.75) → 즉시 확정.
        cosine ≥ VECTOR_CANDIDATE_THRESHOLD(0.60) → 벡터 후보 로깅 (SLM에 위임).
        반환: 확정 시 분류 결과 dict, 미확정 시 None.
        """
        if not self._embedding_index or not self._embedding_index.ready:
            return None

        query_vec = embed_query(question)
        if query_vec is None:
            logger.warning("벡터 임베딩 실패 → SLM 폴백")
            return None

        results = self._embedding_index.search(query_vec, top_k=5)
        if not results:
            return None

        top = results[0]
        logger.info(
            f"벡터 검색 top: intent={top['intent_name']}, "
            f"score={top['score']:.4f}, matched_q='{top['question']}'"
        )

        if top["score"] >= VECTOR_THRESHOLD:
            intent_def = self._index.get_definition(top["intent_name"])
            if intent_def:
                logger.info(
                    f"벡터 확정: {top['intent_name']} "
                    f"(score={top['score']:.4f} ≥ {VECTOR_THRESHOLD})"
                )
                return {
                    "intent_name": top["intent_name"],
                    "category": self._get_category_for_intent(top["intent_name"]),
                    "intent_def": intent_def,
                    "method": "vector",
                    "vector_score": top["score"],
                }

        # 후보 로깅 (SLM 폴백 시 참고용)
        candidates = [r for r in results if r["score"] >= VECTOR_CANDIDATE_THRESHOLD]
        if candidates:
            logger.info(
                f"벡터 후보 ({len(candidates)}개): "
                + ", ".join(
                    f"{c['intent_name']}({c['score']:.3f})"
                    for c in candidates[:3]
                )
            )

        return None

    def _get_vector_candidates(self, question: str) -> list[dict]:
        """
        벡터 검색으로 상위 후보 인텐트 목록을 반환한다 (사후 보정 UI용).
        반환: [{"intent": str, "description": str, "score": float}, ...]
        """
        if not self._embedding_index or not self._embedding_index.ready:
            return []

        query_vec = embed_query(question)
        if query_vec is None:
            return []

        results = self._embedding_index.search(query_vec, top_k=10)
        candidates = []
        seen_intents = set()
        for r in results:
            if r["score"] < VECTOR_CANDIDATE_THRESHOLD:
                break
            if r["intent_name"] in seen_intents:
                continue
            seen_intents.add(r["intent_name"])
            summary = self._index.get_intent_summary(r["intent_name"])
            desc = summary.get("description", r["intent_name"]) if summary else r["intent_name"]
            candidates.append({
                "intent": r["intent_name"],
                "description": desc,
                "score": r["score"],
            })
            if len(candidates) >= 5:
                break
        return candidates

    def _get_category_for_intent(self, intent_name: str) -> str:
        """인텐트명으로 카테고리를 역조회."""
        summary = self._index.get_intent_summary(intent_name)
        if summary:
            return summary.get("category", "공통")
        return "공통"

    def _classify_category(self, question: str) -> tuple:
        """
        Stage 1: 시설 유형 분류
        반환: (category, method)

        우선순위: 공통 키워드 → 시설 키워드 → SLM
        "야간최소유량", "알람" 등 공통 INTENT 키워드가 시설명보다 우선한다.
        """
        # "트렌드"/"트랜드"/"그래프"/"추이"/"같이 보"는 다른 공통 키워드보다 우선
        if ("트렌드" in question or "트랜드" in question or "그래프" in question
                or "추이" in question
                or "같이 보" in question or "함께 보" in question):
            logger.info("Stage1 키워드 매칭: '트렌드/그래프/추이/같이' → 트렌드")
            return "트렌드", "keyword"

        # 공통 키워드 체크 (시설 키워드보다 우선)
        common_keywords = [
            "야간최소유량", "야간 최소유량", "최소유량", "야간",
            "알람", "경보", "알림", "태그", "통신",
            "결측", "진행중",
            "표로",
            "이상", "위험", "예측", "이상감지",
            "누수", "CUSUM", "cusum",
            "표준편차",
            "주소",
            "위치도", "계통도", "초동대응", "매뉴얼",
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
            # 아날로그+디지털 키워드 동시 존재 → MIXED_TREND 자동 감지
            _analog_kws = ["수위", "유량", "압력"]
            _digital_kws = ["밸브", "펌프"]
            _has_analog = any(kw in question for kw in _analog_kws)
            _has_digital = any(kw in question for kw in _digital_kws)
            if (_has_analog and _has_digital) or "함께" in question or "혼합" in question or "같이" in question:
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

        # 키워드 단축: 위치도 → 시설별 LOCATION (facilitytype 기반 분기)
        if "위치도" in question:
            if "가압장" in question:
                return "BOOSTER_STATION_LOCATION", "keyword"
            if any(kw in question for kw in ["소블록", "중블록", "대블록", "블록"]):
                return "BLOCK_LOCATION", "keyword"
            if "감압" in question:
                return "PRESSURE_REDUCING_FACILITY_LOCATION", "keyword"
            if "배수지" in question:
                return "RESERVOIR_LOCATION", "keyword"
            # facilitytype 키워드 없음 → SITENAME_FACILITY_MAP 기반 자동 해소 후 Stage 2 SLM에 위임
            # (query_validator에서 facilitytype 재질의 발생)

        # 키워드 단축: 계통도 → 시설별 SYSTEM_DIAGRAM
        if "계통도" in question:
            if "가압장" in question:
                return "BOOSTER_STATION_SYSTEM_DIAGRAM", "keyword"
            if any(kw in question for kw in ["소블록", "중블록", "대블록", "블록"]):
                return "BLOCK_SYSTEM_DIAGRAM", "keyword"
            if "배수지" in question:
                return "RESERVOIR_SYSTEM_DIAGRAM", "keyword"

        # 키워드 단축: 초동대응 매뉴얼
        if "초동대응" in question or "매뉴얼" in question:
            if "가압장" in question:
                return "BOOSTER_STATION_INITIAL_RESPONSE_MANUAL", "keyword"
            if any(kw in question for kw in ["소블록", "중블록", "대블록", "블록"]):
                return "BLOCK_INITIAL_RESPONSE_MANUAL", "keyword"
            if "감압" in question:
                return "PRESSURE_REDUCING_INITIAL_RESPONSE_MANUAL", "keyword"
            # 배수지 또는 기본값
            return "RESERVOIR_INITIAL_RESPONSE_MANUAL", "keyword"

        # 키워드 단축: 압력 현황
        if "압력" in question and ("현황" in question or "현항" in question):
            return "FACILITY_PRESSURE_STATUS", "keyword"

        # 키워드 단축: 알람 누적건수 / TOP / 순서 / 다발 / 빈번
        if ("누적" in question or "순서" in question or "TOP" in question.upper()
                or "다발" in question or "빈번" in question) and \
           ("알람" in question or "경보" in question or "알림" in question):
            return "FACILITY_ALARM_TOP_COUNT", "keyword"

        # 키워드 단축: 최근 알람
        if "최근" in question and ("알람" in question or "경보" in question or "알림" in question):
            return "FACILITY_RECENT_ALARM", "keyword"

        # 키워드 단축: 수위/압력 단독 → 태그 최신값 (다른 INTENT 키워드 제외)
        _TAG_LATEST_EXCLUDE = [
            "현황", "현항", "헌팅", "표", "적산", "순시",
            "알람", "경보", "알림", "통신", "주소", "이상",
            "결측", "표준편차", "밸브", "트렌드", "추이", "같이",
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
        # 누수추정 CUSUM 분석 (야간최소유량 + 누수)
        if "누수" in question or any(kw in question for kw in ["CUSUM", "cusum"]):
            return "LEAK_CUSUM_ANALYSIS", "keyword"
        # 이상감지 인텐트 ("이상" 없이도 매칭되는 키워드 우선)
        if any(kw in question for kw in ["위험 예측", "이상 예측", "센서 예측"]):
            return "ANOMALY_PREDICT", "keyword"
        # 표준편차 분석 — 이상감지보다 우선 (질문에 "이상"+"표준편차" 동시 포함 시 표준편차 우선)
        if "표준편차" in question:
            return "FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS", "keyword"
        # "고장"/"장애" 계열 — 일상 용어로 이상탐지 접근
        if any(kw in question for kw in ["고장 진단", "장애 진단", "고장 점검", "장애 점검"]):
            return "ANOMALY_SCAN_ALL", "keyword"
        if any(kw in question for kw in ["센서 점검", "설비 점검", "센서 상태", "설비 상태"]):
            return "ANOMALY_SCAN_ALL", "keyword"
        if "이상" in question or "고장" in question:
            if any(kw in question for kw in ["스캔", "감지", "점검", "이상치", "전체"]):
                return "ANOMALY_SCAN_ALL", "keyword"
            if any(kw in question for kw in ["진단", "정밀", "분석"]):
                return "ANOMALY_FACILITY_DETAIL", "keyword"
            if any(kw in question for kw in ["이력", "히스토리", "기록"]):
                return "ANOMALY_HISTORY", "keyword"
            if any(kw in question for kw in ["예측", "예상", "전망"]):
                return "ANOMALY_PREDICT", "keyword"
            if any(kw in question for kw in ["비교", "대비"]):
                return "ANOMALY_COMPARE", "keyword"
            if any(kw in question for kw in ["패턴", "시간대", "주기"]):
                return "ANOMALY_PATTERN", "keyword"
            if any(kw in question for kw in ["설비", "현황"]):
                return "FACILITY_ABNORMAL_STATUS_SUMMARY", "keyword"
            # 일상 표현: "이상 있어?", "이상 없나?", "이상인가?" 등 → 이상감지 상세
            if any(kw in question for kw in ["있어", "있나", "없어", "없나", "있는", "없는", "인가", "인지"]):
                return "ANOMALY_FACILITY_DETAIL", "keyword"
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

        return None, "keyword"

    def _classify_intent_slm(self, question: str, category: str) -> tuple:
        """
        Stage 2: SLM(Phi-4-mini)으로 인텐트 분류.
        키워드 매칭 실패 + 벡터 검색 미확정 시 호출.
        반환: (intent_name or None, method)
        """
        intent_list_str = self._index.build_category_prompt_segment(category)
        if not intent_list_str:
            return None, "slm"

        try:
            prompt = _STAGE2_PROMPT_TEMPLATE.format(
                category=category,
                intent_list=intent_list_str,
                question=question,
            )
            response = self._ollama.generate(prompt)
            response = response.strip().strip('"').strip("'")
            logger.info(f"Stage2 SLM 응답: '{response}' (category={category})")

            all_names = self._index.get_all_intent_names()
            for name in all_names:
                if name in response:
                    return name, "slm"

            logger.warning(f"Stage2 SLM 응답에서 유효한 INTENT를 찾지 못함: '{response}'")
            return None, "slm"

        except OllamaConnectionError as e:
            logger.warning(f"Stage2 Ollama 연결 실패: {e}")
            return None, "slm_error"
