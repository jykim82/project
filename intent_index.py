"""
intent_index.py
example3.json으로부터 SLM 분류용 인덱스를 빌드한다.

- 서버 시작 시 한 번 실행
- example3.json을 읽기 전용으로 참조 (수정 금지)
- 시설 카테고리별 INTENT 그룹핑
- INTENT별 요약 정보 (한글 설명, 대표 질문, 필수 파라미터)
"""

import json
import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)

# =============================================================================
# 시설 카테고리 → INTENT 접두사 매핑
# =============================================================================
FACILITY_CATEGORIES = {
    "배수지": [
        "RESERVOIR_",
        "TODAY_FLOW_",
        "TODAY_OUTFLOW_",
        "TODAY_RESERVOIR_",
    ],
    "가압장": [
        "BOOSTER_STATION_",
    ],
    "감압시설": [
        "PRESSURE_REDUCING_",
    ],
    "블록": [
        "BLOCK_",
    ],
    "공통": [
        "FACILITY_",
        "ONGOING_",
        "NIGHT_MIN_",
        "TAG_",
        "ANOMALY_",
        "ALARM_",
    ],
    "트렌드": [
        "FACILITY_TREND",
        "FACILITY_MIXED_TREND",
    ],
}

# INTENT 이름 → 한글 설명 (분류 프롬프트에 사용)
INTENT_DESCRIPTIONS = {
    "RESERVOIR_OVERVIEW": "배수지 일반현황",
    "RESERVOIR_EQUIPMENT_STATUS": "배수지 설비현황",
    "RESERVOIR_INITIAL_RESPONSE_MANUAL": "배수지 초동대응 매뉴얼",
    "RESERVOIR_OPERATION_STATUS": "배수지 운영현황",
    "RESERVOIR_FACILITY_CAPACITY": "배수지 시설용량",
    "RESERVOIR_LEVEL_STATUS": "배수지 수위현황",
    "RESERVOIR_LEVEL_HUNTING_CHECK": "배수지 수위 헌팅 점검",
    "RESERVOIR_LOCATION": "배수지 주소/위치",
    "RESERVOIR_NETWORK_DIAGRAM": "배수지 계통도",
    "RESERVOIR_SUPPLY_AVAILABLE_HOURS": "배수지 용수공급가능시간",
    "TODAY_RESERVOIR_AVG_USAGE": "배수지 평균사용량(개별)",
    "TODAY_RESERVOIR_AVG_USAGE_ALL": "전체 배수지 평균사용량",
    "TODAY_FLOW_ACCUMULATION": "금일 적산 현황",
    "TODAY_OUTFLOW_ALL_STATUS": "전체 배수지 유출 현황",
    "BOOSTER_STATION_OVERVIEW": "가압장 일반현황",
    "BOOSTER_STATION_OPERATION_STATUS": "가압장 운영현황",
    "BOOSTER_STATION_EQUIPMENT_STATUS": "가압장 설비현황",
    "BOOSTER_STATION_INITIAL_RESPONSE_MANUAL": "가압장 초동대응 매뉴얼",
    "BOOSTER_STATION_LOCATION": "가압장 주소/위치",
    "BOOSTER_STATION_NETWORK_DIAGRAM": "가압장 계통도",
    "PRESSURE_REDUCING_FACILITY_OVERVIEW": "감압시설 일반현황",
    "PRESSURE_REDUCING_FACILITY_OPERATION_STATUS": "감압시설 운영현황",
    "PRESSURE_REDUCING_FACILITY_EQUIPMENT_STATUS": "감압시설 설비현황",
    "PRESSURE_REDUCING_FACILITY_INITIAL_RESPONSE_MANUAL": "감압시설 초동대응 매뉴얼",
    "PRESSURE_REDUCING_FACILITY_CRITERIA": "감압시설 설정기준",
    "PRESSURE_REDUCING_FACILITY_LOCATION": "감압시설 주소/위치",
    "PRESSURE_REDUCING_FACILITY_NETWORK_DIAGRAM": "감압시설 계통도",
    "BLOCK_OVERVIEW": "블록 일반현황",
    "BLOCK_LOCATION": "블록 주소/위치",
    "BLOCK_NETWORK_DIAGRAM": "블록 계통도",
    "BLOCK_OPERATION_STATUS": "블록 운영현황",
    "FACILITY_PRESSURE_STATUS": "시설 압력 현황",
    "FACILITY_COMMUNICATION_TOPOLOGY": "시설 통신 구성",
    "FACILITY_ADDRESS_INFO_RESERVOIR": "배수지 주소 정보",
    "FACILITY_ADDRESS_INFO_BOOSTER": "가압장 주소 정보",
    "FACILITY_ADDRESS_INFO_BLOCK": "블록 주소 정보",
    "FACILITY_ADDRESS_INFO_PRESSURE": "감압시설 주소 정보",
    "FACILITY_RECENT_ALARM": "최근 알람 이력",
    "FACILITY_ALARM_TOP_COUNT": "알람 건수 상위 시설",
    "FACILITY_ALARM_CAUSE_DIAGNOSIS_RANK": "알람 원인 진단 순위",
    "FACILITY_TAG_LATEST_VALUE": "태그 최신값",
    "FACILITY_FLOW_CURRENT_TABLE": "유량 현황 표",
    "FACILITY_VALVE_CURRENT_TABLE": "밸브 현황 표",
    "FACILITY_ANALOG_TIMESERIES_TABLE": "아날로그 시계열 표",
    "FACILITY_DIGITAL_TIMESERIES_TABLE": "디지털 시계열 표",
    "FACILITY_TREND": "시설 트렌드",
    "FACILITY_MIXED_TREND": "아날로그+디지털 혼합 트렌드",
    "FACILITY_ABNORMAL_STATUS_SUMMARY": "이상현황 요약",
    "NIGHT_MIN_FLOW_STATUS": "야간최소유량 현황",
    "NIGHT_MIN_FLOW_SUMMARY_TABLE": "야간최소유량 요약 표",
    "TAG_DAILY_MISSING_SUMMARY": "태그 일간 결측 요약",
    "ONGOING_ALARM_STATUS": "진행중 알람 현황",
    "ALARM_ABNORMAL_LOCATIONS": "경보 이상 발생 지점",
    "ANOMALY_SCAN_ALL": "전체 센서 이상 스캔",
    "ANOMALY_FACILITY_DETAIL": "시설 이상 정밀 진단",
    "ANOMALY_HISTORY": "이상 이력 조회",
    "ANOMALY_PREDICT": "위험 예측 분석",
    "ANOMALY_COMPARE": "시설간 이상 비교",
    "ANOMALY_PATTERN": "시간대 이상 패턴",
    "ANOMALY_CROSS_FACILITY": "시설간 교차 검증",
    "ANOMALY_FLOW_BALANCE": "물 수지 검증",
    "LEAK_CUSUM_ANALYSIS": "야간최소유량 CUSUM 누수추정",
    "FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS": "야간최소유량 표준편차분석",
    "FACILITY_COMMUNICATION_STATUS": "시설 통신 상태",
    "FACILITY_DIGITAL_STATUS_TIMESERIES_TABLE": "디지털 상태 시계열 표",
    "FACILITY_FLOW_ACCUMULATED_TIMESERIES_TABLE": "적산유량 시계열 표",
    "FACILITY_FLOW_INSTANT_TIMESERIES_TABLE": "순시유량 시계열 표",
    "FACILITY_TAG_DATA_TABLE": "태그 데이터 표",
    "FACILITY_VALVE_STATUS_CURRENT_TABLE": "밸브 상태 현황 표",
    "FACILITY_CATALOG_TREND_TABLE": "시설 카탈로그 트렌드 표",
}


def _extract_required_params(sql) -> list:
    """SQL 템플릿에서 {param} 플레이스홀더를 추출한다."""
    if isinstance(sql, list):
        sql = " ".join(sql)
    if not sql:
        return []
    return sorted(set(re.findall(r"\{(\w+)\}", sql)))


def _categorize_intent(intent_name: str) -> str:
    """INTENT 이름으로 시설 카테고리를 결정한다."""
    # 트렌드는 정확 매칭 우선
    if intent_name in ("FACILITY_TREND", "FACILITY_MIXED_TREND"):
        return "트렌드"

    for category, prefixes in FACILITY_CATEGORIES.items():
        if category == "트렌드":
            continue
        for prefix in prefixes:
            if intent_name.startswith(prefix):
                return category
    return "공통"


class IntentIndex:
    """
    example3.json 기반 분류용 인덱스.
    서버 시작 시 build()로 한 번 생성한다.
    """

    def __init__(self):
        # intent_name → IntentSummary
        self.intents: dict = {}
        # category → [intent_name, ...]
        self.category_intents: dict = {}
        # intent_name → full definition (example3.json 원본)
        self.definitions: dict = {}

    def build(self, intent_definitions: list) -> None:
        """example3.json 리스트로부터 인덱스를 빌드한다."""
        self.intents = {}
        self.category_intents = {}
        self.definitions = {}

        for intent_def in intent_definitions:
            intent_name = intent_def.get("intent", "")
            if not intent_name:
                continue

            sql = intent_def.get("sql", "")
            questions = intent_def.get("questions", [])
            required_params = _extract_required_params(sql)
            category = _categorize_intent(intent_name)
            description = INTENT_DESCRIPTIONS.get(intent_name, intent_name)
            example_questions = questions[:2]

            self.intents[intent_name] = {
                "name": intent_name,
                "category": category,
                "description": description,
                "example_questions": example_questions,
                "required_params": required_params,
            }

            if category not in self.category_intents:
                self.category_intents[category] = []
            self.category_intents[category].append(intent_name)

            self.definitions[intent_name] = intent_def

        logger.info(
            f"IntentIndex 빌드 완료: {len(self.intents)}개 INTENT, "
            f"{len(self.category_intents)}개 카테고리"
        )

    def get_intents_for_category(self, category: str) -> list:
        """특정 카테고리의 INTENT 요약 리스트를 반환한다."""
        names = self.category_intents.get(category, [])
        return [self.intents[n] for n in names if n in self.intents]

    def get_intent_summary(self, intent_name: str) -> Optional[dict]:
        """특정 INTENT의 요약 정보를 반환한다."""
        return self.intents.get(intent_name)

    def get_definition(self, intent_name: str) -> Optional[dict]:
        """특정 INTENT의 원본 정의(example3.json)를 반환한다."""
        return self.definitions.get(intent_name)

    def get_all_intent_names(self) -> list:
        """전체 INTENT 이름 리스트를 반환한다."""
        return list(self.intents.keys())

    def get_categories(self) -> list:
        """카테고리 목록을 반환한다."""
        return list(self.category_intents.keys())

    def build_category_prompt_segment(self, category: str) -> str:
        """
        특정 카테고리의 INTENT 목록을 SLM 프롬프트용 문자열로 생성한다.
        예:
          1. RESERVOIR_OVERVIEW - 배수지 일반현황 (예: "배수지 일반현황은?")
          2. RESERVOIR_EQUIPMENT_STATUS - 배수지 설비현황 (예: "배수지 설비현황은?")
        """
        intents = self.get_intents_for_category(category)
        lines = []
        for i, info in enumerate(intents, 1):
            example = info["example_questions"][0] if info["example_questions"] else ""
            line = f'{i}. {info["name"]} - {info["description"]}'
            if example:
                line += f' (예: "{example}")'
            lines.append(line)
        return "\n".join(lines)
