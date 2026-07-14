"""질의 텍스트 정규화·인텐트 매칭 — ai_server Phase 4 분리 (2026-07-14).

포함:
- example3.json 인텐트 정의 로딩 (INTENT_DEFINITIONS)
- DB 동적 로더: sitename / block_level / sitename→facility / facility alias
- 질문 정규화·파라미터 추출 (normalize_* / extract_*)
- 키워드 인텐트 매칭 (calculate_match_score / match_intent)

주의: import 시점에 DB 로드가 실행된다 (기존 ai_server 와 동일 시맨틱).
FACILITY_ALIAS_MAP 전역은 CRUD 리로드 재바인딩 때문에 ai_server 에 유지 —
여기서는 로더(load_facility_aliases_from_db)만 제공한다.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

import psycopg2

logger = logging.getLogger(__name__)

# DB 접속 정보 (ai_server 와 동일 env 기반)
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5433")
DB_NAME = os.environ.get("DB_NAME", "slm")
DB_USER = os.environ.get("DB_USER", "slm_dev")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")

# =============================================================================
# example3.json 로딩
# docs/example3_policy.md 참조: example3.json은 운영 정책 선언 파일이다
# =============================================================================
def load_intent_definitions() -> list:
    """example3.json을 로딩하여 INTENT 정의 목록을 반환한다."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    json_path = os.path.join(script_dir, "example3.json")
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)


INTENT_DEFINITIONS = load_intent_definitions()


# =============================================================================
# 인텐트 메타 파생 (아키텍처 1단계, 2026-07-15)
#
# 인텐트 추가 시 example3.json 한 곳에만 선언하면 되도록, 분산돼 있던
# 등록 지점(dynamic-SQL 세트·분류 키워드)을 정의에서 파생 생성한다.
#   - "dynamic_sql": true          → SQL 템플릿 없이 커스텀 핸들러가 rows 를
#                                     채우는 인텐트 (ai_server SSE 게이트)
#   - "classify_keywords": {"stage1": [...]}
#                                   → 질의에 포함되면 Stage1 '공통' 카테고리로
#                                     단축 (SLM 폴백 ~10s 회피)
# 과거 회귀: _DYNAMIC_SQL_INTENTS 2곳 중 1곳 누락 → 빈 응답 (07-11),
#            Stage1 키워드 누락 → 분류 10초 지연 (07-11)
# =============================================================================

def dynamic_sql_intents() -> frozenset:
    """SQL 템플릿 없이(또는 무시하고) 커스텀 핸들러가 처리하는 인텐트 집합."""
    return frozenset(
        d["intent"] for d in INTENT_DEFINITIONS if d.get("dynamic_sql")
    )


def stage1_keywords_from_definitions() -> list:
    """인텐트 정의의 stage1 분류 키워드를 모아 반환 (선언 순서 유지)."""
    out = []
    for d in INTENT_DEFINITIONS:
        for kw in (d.get("classify_keywords") or {}).get("stage1", []):
            if kw not in out:
                out.append(kw)
    return out


# =============================================================================
# DB에서 sitename, block_level 목록 동적 로딩
# =============================================================================
def load_sitenames_from_db() -> list:
    """
    DB에서 sitename 목록을 동적으로 로드한다.
    조회 테이블:
    - tb_tag_info (전체 태그)
    - tb_block_info (블록)
    - tb_service_reservoir_info (배수지)
    - tb_service_booster_station_info (가압장)
    - tb_pressure_reducing_facility_info (감압시설)
    """
    sitenames = set()

    queries = [
        "SELECT DISTINCT sitename FROM tb_tag_info WHERE sitename IS NOT NULL",
        "SELECT DISTINCT sitename FROM tb_block_info WHERE sitename IS NOT NULL",
        "SELECT DISTINCT sitename FROM tb_service_reservoir_info WHERE sitename IS NOT NULL",
        "SELECT DISTINCT sitename FROM tb_service_booster_station_info WHERE sitename IS NOT NULL",
        "SELECT DISTINCT sitename FROM tb_pressure_reducing_facility_info WHERE sitename IS NOT NULL",
    ]

    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
        )
        with conn.cursor() as cur:
            for query in queries:
                try:
                    cur.execute(query)
                    rows = cur.fetchall()
                    for row in rows:
                        if row[0]:
                            sitenames.add(row[0])
                except psycopg2.Error as e:
                    logger.warning(f"sitename 조회 실패: {query[:50]}... - {e}")
                    continue
    except psycopg2.Error as e:
        logger.error(f"DB 연결 실패 (sitename 로딩): {e}")
        # 실패 시 기본값 반환
        return ["신평", "송악1", "송악2", "행정", "합덕", "순성", "고대리", "남산1",
                "우강", "삼봉", "천의리", "합덕3", "행정1-1", "행정1-2"]
    finally:
        if conn:
            conn.close()

    # 긴 이름부터 매칭하도록 정렬 (예: "행정1-1"이 "행정"보다 먼저 매칭)
    result = sorted(list(sitenames), key=len, reverse=True)
    logger.info(f"DB에서 {len(result)}개의 sitename 로드 완료")
    return result


def load_block_levels_from_db() -> list:
    """
    DB에서 block_level 목록을 동적으로 로드한다.
    조회 테이블: tb_block_info
    예: '소블록', '소소블록', '중블록', '대블록' 등
    """
    block_levels = set()

    query = "SELECT DISTINCT block_level FROM tb_block_info WHERE block_level IS NOT NULL"

    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
        )
        with conn.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchall()
            for row in rows:
                if row[0]:
                    block_levels.add(row[0])
    except psycopg2.Error as e:
        logger.error(f"DB 연결 실패 (block_level 로딩): {e}")
        # 실패 시 기본값 반환
        return ["소블록", "소소블록", "중블록", "대블록"]
    finally:
        if conn:
            conn.close()

    # 긴 이름부터 매칭하도록 정렬 (예: "소소블록"이 "소블록"보다 먼저 매칭)
    result = sorted(list(block_levels), key=len, reverse=True)
    logger.info(f"DB에서 {len(result)}개의 block_level 로드 완료")
    return result


def load_sitename_facility_map() -> dict:
    """
    DB에서 sitename → facilitytype 집합 매핑을 로드한다.
    블록 계열(소블록/중블록/대블록)은 facilitytype을 block_level로 대체한다.
    반환: {"신평": {"배수지"}, "고대리": {"가압장"}, "남산1": {"소블록"}, ...}
    """
    mapping = {}

    queries = [
        ("SELECT DISTINCT sitename FROM tb_service_reservoir_info WHERE sitename IS NOT NULL", "배수지"),
        ("SELECT DISTINCT sitename FROM tb_service_booster_station_info WHERE sitename IS NOT NULL", "가압장"),
        ("SELECT DISTINCT sitename FROM tb_pressure_reducing_facility_info WHERE sitename IS NOT NULL", "감압시설"),
    ]
    block_query = "SELECT DISTINCT sitename, block_level FROM tb_block_info WHERE sitename IS NOT NULL AND block_level IS NOT NULL"

    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
        with conn.cursor() as cur:
            for query, ftype in queries:
                try:
                    cur.execute(query)
                    for row in cur.fetchall():
                        if row[0]:
                            mapping.setdefault(row[0], set()).add(ftype)
                except psycopg2.Error:
                    continue

            try:
                cur.execute(block_query)
                for row in cur.fetchall():
                    if row[0] and row[1]:
                        mapping.setdefault(row[0], set()).add(row[1])
            except psycopg2.Error:
                pass
    except psycopg2.Error as e:
        logger.error(f"DB 연결 실패 (sitename-facility 매핑 로딩): {e}")
        return {}
    finally:
        if conn:
            conn.close()

    logger.info(f"DB에서 {len(mapping)}개의 sitename-facility 매핑 로드 완료")
    return mapping


def load_facility_aliases_from_db() -> dict:
    """
    tb_facility_alias에서 약칭 → 정식 sitename 매핑을 로드한다.
    반환: {"합일": "합덕일반", "죽배": "죽동", ...}

    중복 alias는 priority DESC로 정렬하여 우선순위 높은 것이 덮어쓴다.
    """
    aliases: dict = {}
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT alias, sitename FROM tb_facility_alias "
                "WHERE use_yn = 'Y' "
                "ORDER BY priority ASC"
            )
            for alias, sitename in cur.fetchall():
                if alias and sitename:
                    aliases[alias] = sitename
    except psycopg2.Error as e:
        logger.warning(f"facility alias 로드 실패: {e}")
    finally:
        if conn:
            conn.close()

    if aliases:
        logger.info(f"DB에서 {len(aliases)}개의 facility alias 로드 완료")
    return aliases


# 서버 시작 시 DB에서 로드
KNOWN_SITENAMES = load_sitenames_from_db()
KNOWN_BLOCK_LEVELS = load_block_levels_from_db()
SITENAME_FACILITY_MAP = load_sitename_facility_map()

# =============================================================================
# 질문 정규화 함수
# =============================================================================
def normalize_question(question: str) -> str:
    """
    질문 문자열을 정규화한다.
    - 연속 공백을 단일 공백으로
    - 앞뒤 공백 제거
    - 한글과 숫자 사이 공백 제거 (띄어쓰기 오류 보정: "행정 1-1" → "행정1-1")
    - 한글 숫자 기간 표현 → 숫자 변환 ("한달간" → "30일간")
    - 도메인 오타 정규화 ("트랜드" → "트렌드")
    """
    result = re.sub(r"\s+", " ", question.strip())
    # 한글 뒤 공백 + 숫자 → 공백 제거 (예: "행정 1-1" → "행정1-1", "남산 1" → "남산1")
    result = re.sub(r"([\uac00-\ud7a3])\s+(\d)", r"\1\2", result)

    # 한글 숫자 기간 표현 → 숫자 변환 (긴 표현부터 매칭)
    _KOREAN_PERIOD = [
        ("삼주일", "21일"), ("이주일", "14일"), ("일주일", "7일"), ("한주일", "7일"), ("한주", "7일"),
        ("십이개월", "365일"), ("십일개월", "330일"), ("십개월", "300일"),
        ("구개월", "270일"), ("팔개월", "240일"), ("칠개월", "210일"),
        ("육개월", "180일"), ("오개월", "150일"), ("사개월", "120일"),
        ("삼개월", "90일"), ("이개월", "60일"), ("일개월", "30일"),
        ("여섯달", "180일"), ("다섯달", "150일"), ("넉달", "120일"),
        ("세달", "90일"), ("석달", "90일"), ("두달", "60일"), ("한달", "30일"),
        ("삼년", "1095일"), ("이년", "730일"), ("일년", "365일"),
        ("세해", "1095일"), ("두해", "730일"), ("한해", "365일"),
    ]
    for kor, num in _KOREAN_PERIOD:
        if kor in result:
            result = result.replace(kor, num)

    # 숫자+달 기간 표현 변환 (예: "3달동안" → "90일동안", "6달간" → "180일간")
    # 한글 숫자 변환 후 남은 숫자+달 패턴 처리 (1~12달만)
    result = re.sub(r'(\d{1,2})\s*달', lambda m: str(int(m.group(1)) * 30) + '일', result)

    # 숫자+개년 기간 표현 변환 (예: "3개년간" → "1095일간")
    # 주의: "2025년" 같은 연도는 "개년" 형태가 아니므로 안전
    result = re.sub(r'(\d+)\s*개년', lambda m: str(int(m.group(1)) * 365) + '일', result)

    # 도메인 오타 정규화
    result = result.replace("작산", "적산")   # "적산"의 오타 ("10월 작산" → "10월 적산")
    result = result.replace("트트렌드", "트렌드")
    result = result.replace("트랜트", "트렌드")
    result = result.replace("트랜드", "트렌드")

    return result

def normalize_for_matching(text: str) -> str:
    """
    매칭용 정규화: 조사 제거, 공백 제거, 소문자 변환

    핵심 규칙:
    - 구두점을 먼저 제거한다.
    - 공백 기준으로 단어를 분리한 뒤, 각 단어 끝의 조사만 제거한다.
    - 이렇게 하면 "가압장" → "가압장" (보존), "배수지의" → "배수지" (조사 제거)
    - 공백 제거 전에 조사를 제거하여 단어 경계의 '가', '이' 등이 오인되는 것을 방지한다.
    """
    text = text.lower()
    # 구두점 먼저 제거
    text = re.sub(r"[?!.]", "", text)
    # 공백 기준으로 단어를 분리하고, 각 단어 끝의 조사 제거
    words = text.split()
    cleaned_words = []
    for word in words:
        # 단어 끝에서 조사 패턴 제거 (긴 조사부터 매칭)
        word = re.sub(r"(에서|으로|까지|부터|은|는|이|가|을|를|에|의|와|과|도|만)$", "", word)
        cleaned_words.append(word)
    text = "".join(cleaned_words)
    return text


def remove_sitename_for_matching(text: str) -> str:
    """
    매칭 전 sitename을 제거하여 패턴 매칭 정확도를 높인다.
    예: "금일 신평 배수지 적산은?" → "금일 배수지 적산은?"
    KNOWN_SITENAMES는 DB에서 동적으로 로드된 목록을 사용한다.
    """
    result = text
    for site in KNOWN_SITENAMES:
        result = result.replace(site, "")
    # 연속 공백 정리
    result = re.sub(r"\s+", " ", result).strip()
    return result


# =============================================================================
# 파라미터 추출 함수
# docs/ai_server_task.md 참조
# =============================================================================


def extract_sitename(question: str) -> Optional[str]:
    """
    질문에서 sitename을 추출한다.
    docs/ai_server_task.md 참조: sitename 누락 시 SQL 실행 금지
    """
    for site in KNOWN_SITENAMES:
        if site in question:
            return site
    return None


def extract_block_level(question: str) -> Optional[str]:
    """
    질문에서 block_level을 추출한다.
    KNOWN_BLOCK_LEVELS는 DB에서 동적으로 로드된 목록을 사용한다.
    (예: '소블록', '소소블록', '중블록', '대블록' 등)
    """
    for level in KNOWN_BLOCK_LEVELS:
        if level in question:
            return level
    return None


def extract_facilitytype(question: str, block_level: Optional[str]) -> Optional[str]:
    """
    질문에서 facilitytype을 추출한다.
    docs/ai_server_task.md 참조:
    - facilitytype은 추론하지 않는다
    - block_level이 있으면 facilitytype = block_level
    - 없으면 배수지/가압장/감압시설 키워드만 허용
    """
    if block_level:
        return block_level

    if "배수지" in question:
        return "배수지"
    if "가압장" in question:
        return "가압장"
    if "감압시설" in question or "감압설비" in question or "감압밸브" in question:
        return "감압시설"

    return None


def extract_datainfo(question: str) -> Optional[str]:
    """
    질문에서 datainfo를 추출한다.
    docs/ai_server_task.md 참조: datainfo는 압력/유량/수위 키워드 기반으로만 추출
    """
    if "압력" in question:
        return "압력"
    if "유량" in question:
        return "유량"
    if "수위" in question:
        return "수위"
    if "적산" in question:
        return "유량"
    if "밸브" in question:
        return "밸브"
    return None


def extract_limit(question: str) -> int:
    """질문에서 limit 숫자를 추출한다. 기본값: 10"""
    match = re.search(r"TOP\s*(\d+)", question, re.IGNORECASE)
    if match:
        return int(match.group(1))

    match = re.search(r"상위\s*(\d+)", question)
    if match:
        return int(match.group(1))

    return 10


def extract_alarm_msg(question: str) -> Optional[str]:
    """질문에서 alarm_msg 키워드를 추출한다."""
    alarm_keywords = ["펌프", "수위", "압력", "통신", "유량", "고장"]
    for keyword in alarm_keywords:
        if keyword in question:
            return keyword
    return None


# =============================================================================
# INTENT 매칭 함수
# docs/ai_server_task.md 참조:
# - 질문 정규화 후 example3.json.questions와 부분 포함 매칭으로 INTENT 결정
# - 질문 문구 단위로 로직을 분기하지 않는다
# - 반드시 INTENT(의미 단위) 기준으로 구현한다
# =============================================================================

# 시설 타입별 INTENT 접두사 매핑
FACILITY_INTENT_PREFIX = {
    "배수지": ["RESERVOIR_", "TODAY_FLOW_", "TODAY_OUTFLOW_", "TODAY_RESERVOIR_"],
    "가압장": ["BOOSTER_STATION_"],
    "감압시설": ["PRESSURE_REDUCING_"],
    "감압설비": ["PRESSURE_REDUCING_"],
    "감압밸브": ["PRESSURE_REDUCING_"],
    "소블록": ["BLOCK_"],
    "중블록": ["BLOCK_"],
    "대블록": ["BLOCK_"],
}

# 공통 INTENT (시설 타입 무관)
COMMON_INTENT_PREFIXES = [
    "FACILITY_PRESSURE_",
    "FACILITY_COMMUNICATION_",
    "FACILITY_ADDRESS_",
    "FACILITY_RECENT_ALARM",
    "FACILITY_ALARM_",
    "FACILITY_TAG_",
    "FACILITY_FLOW_",
    "FACILITY_VALVE_",
    "FACILITY_ANALOG_",
    "FACILITY_DIGITAL_",
    "FACILITY_ABNORMAL_",
    "FACILITY_NIGHT_MIN_FLOW_",
    "FACILITY_TREND",
    "FACILITY_MIXED_TREND",
    "NIGHT_MIN_FLOW_",
    "TAG_DAILY_",
    "ONGOING_",
]


def extract_facility_type_from_question(question: str) -> Optional[str]:
    """
    질문에서 시설 타입 키워드를 추출한다.
    KNOWN_BLOCK_LEVELS는 DB에서 동적으로 로드된 목록을 사용한다.
    """
    # 우선순위: 블록 레벨은 KNOWN_BLOCK_LEVELS에서 먼저 확인 (긴 이름부터 정렬되어 있음)
    for level in KNOWN_BLOCK_LEVELS:
        if level in question:
            return level
    if "가압장" in question:
        return "가압장"
    if "감압시설" in question or "감압설비" in question or "감압밸브" in question:
        return "감압시설"
    if "배수지" in question:
        return "배수지"
    return None


def is_intent_for_facility(intent_name: str, facility_type: Optional[str]) -> bool:
    """INTENT가 해당 시설 타입에 적합한지 확인한다."""
    # 공통 INTENT는 모든 시설에 적용
    for prefix in COMMON_INTENT_PREFIXES:
        if intent_name.startswith(prefix):
            return True

    # 시설 타입이 없으면 모든 INTENT 허용
    if not facility_type:
        return True

    # 시설 타입별 INTENT 확인
    prefixes = FACILITY_INTENT_PREFIX.get(facility_type, [])
    for prefix in prefixes:
        if intent_name.startswith(prefix):
            return True

    return False


def calculate_match_score(user_normalized: str, pattern_normalized: str, intent_name: str, user_question: str, pattern_question: str) -> float:
    """
    매칭 점수를 계산한다.
    - 패턴 길이가 길수록 높은 점수 (더 구체적인 매칭)
    - 핵심 키워드 일치 시 보너스 점수
    - sitename 제거 후에도 매칭 시도
    """
    # 1차: 직접 매칭
    matched = pattern_normalized in user_normalized

    # 2차: sitename 제거 후 매칭 (예: "금일신평배수지적산" → "금일배수지적산")
    if not matched:
        user_without_site = normalize_for_matching(remove_sitename_for_matching(user_question))
        matched = pattern_normalized in user_without_site

    # 3차: 양쪽 모두 sitename 제거 후 매칭 (패턴의 sitename도 제거)
    if not matched:
        user_without_site = normalize_for_matching(remove_sitename_for_matching(user_question))
        pattern_without_site = normalize_for_matching(remove_sitename_for_matching(pattern_question))
        matched = pattern_without_site in user_without_site or user_without_site in pattern_without_site

    if not matched:
        return 0.0

    # 기본 점수: 패턴 길이
    score = len(pattern_normalized)

    # 핵심 키워드 보너스
    keyword_bonus = {
        "현재": 10,
        "금일": 10,
        "오늘": 10,
        "적산": 15,
        "주소": 15,
        "위치도": 10,
        "계통도": 10,
        "초동대응": 15,
        "매뉴얼": 10,
        "헌팅": 15,
        "통신": 10,
        "알람": 10,
        "경보": 10,
        "트렌드": 10,
        "표": 10,
    }

    for keyword, bonus in keyword_bonus.items():
        if keyword in user_question:
            score += bonus

    # 시설 타입 일치 보너스
    facility_in_question = extract_facility_type_from_question(user_question)
    if facility_in_question:
        if facility_in_question == "가압장" and "BOOSTER" in intent_name:
            score += 20
        elif facility_in_question == "배수지" and "RESERVOIR" in intent_name:
            score += 20
        elif facility_in_question in ["감압시설", "감압설비"] and "PRESSURE_REDUCING" in intent_name:
            score += 20
        elif facility_in_question in ["소블록", "중블록", "대블록"] and "BLOCK" in intent_name:
            score += 20

    # 주소 관련 INTENT 특별 처리
    if "주소" in user_question:
        if "가압장" in user_question and "BOOSTER" in intent_name:
            score += 30
        elif "배수지" in user_question and "RESERVOIR" in intent_name:
            score += 30
        elif "감압" in user_question and "PRESSURE" in intent_name:
            score += 30
        elif ("소블록" in user_question or "중블록" in user_question or "대블록" in user_question) and "BLOCK" in intent_name:
            score += 30

    return score


def match_intent(user_question: str) -> Optional[dict]:
    """
    정규화된 질문을 example3.json의 questions와 부분 포함 매칭하여 INTENT를 결정한다.
    개선된 알고리즘:
    1. 시설 타입 기반 필터링
    2. 매칭 점수 계산 (패턴 길이 + 키워드 보너스)
    3. sitename 유무와 SQL 템플릿의 {sitename} 유무 정합성 보너스/패널티
    4. 가장 높은 점수의 INTENT 선택
    우선 규칙: "트렌드" 포함 → FACILITY_TREND 강제 반환 (기간 표현 정규화 후 판단)
    """
    # normalize_question 적용 (기간 표현 통일: "한달간"→"30일간", "트랜드"→"트렌드")
    normalized_question = normalize_question(user_question)

    # 우선 규칙: "수위" + ("이유"|"원인"|"왜") → RESERVOIR_LEVEL_CAUSE_ANALYSIS 강제 반환
    _cause_keywords = ("이유", "원인", "왜")
    if "수위" in normalized_question and any(kw in normalized_question for kw in _cause_keywords):
        cause_intent = next(
            (d for d in INTENT_DEFINITIONS if d.get("intent") == "RESERVOIR_LEVEL_CAUSE_ANALYSIS"),
            None,
        )
        if cause_intent is not None:
            logger.debug("[match_intent] '수위' + '이유/원인/왜' 감지 → RESERVOIR_LEVEL_CAUSE_ANALYSIS 강제 반환")
            return cause_intent

    # 우선 규칙: "트렌드"가 포함된 질문은 항상 FACILITY_TREND로 분류
    if "트렌드" in normalized_question:
        trend_intent = next(
            (d for d in INTENT_DEFINITIONS if d.get("intent") == "FACILITY_TREND"),
            None,
        )
        if trend_intent is not None:
            logger.debug(f"[match_intent] '트렌드' 키워드 감지 → FACILITY_TREND 강제 반환")
            return trend_intent

    normalized_user = normalize_for_matching(normalized_question)
    facility_type = extract_facility_type_from_question(user_question)
    has_sitename = extract_sitename(user_question) is not None

    best_match = None
    best_score = 0.0

    for intent_def in INTENT_DEFINITIONS:
        intent_name = intent_def.get("intent", "")

        # 시설 타입 필터링
        if not is_intent_for_facility(intent_name, facility_type):
            continue

        for q in intent_def.get("questions", []):
            # example3.json의 질문도 normalize_question 적용 후 비교 (기간 표현 통일)
            normalized_q = normalize_for_matching(normalize_question(q))
            score = calculate_match_score(normalized_user, normalized_q, intent_name, user_question, q)

            if score <= 0:
                continue

            # sitename 유무와 SQL 템플릿 정합성 보정
            sql_template = intent_def.get("sql", "")
            sql_needs_sitename = "{sitename}" in sql_template

            if has_sitename and sql_needs_sitename:
                # sitename 있고, SQL도 sitename 필요 → 보너스
                score += 25
            elif has_sitename and not sql_needs_sitename:
                # sitename 있지만, SQL은 전체 조회용 → 패널티
                score -= 25
            elif not has_sitename and not sql_needs_sitename:
                # sitename 없고, SQL도 전체 조회용 → 보너스
                score += 25

            if score > best_score:
                best_score = score
                best_match = intent_def

    return best_match
