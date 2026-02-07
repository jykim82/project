"""
ai_server.py
SLM 기반 운영 질의 응답 서버

=============================================================================
설계 철학 (docs/ai_server_plan.md 참조)
=============================================================================
- 이 서버는 판단 시스템이 아니다.
- 이 서버는 이상/정상 판단, 예측/추론/보정, 정책 결정을 수행하지 않는다.
- 결정 가능한 것은 시스템이 하고, 판단이 필요한 것은 사람에게 남긴다.
- SLM은 답을 결정하지 않으며, 이미 결정된 결과를 설명한다.
- 정책(example3.json)과 실행(ai_server.py)은 분리된다.

=============================================================================
문서 간 역할 분리
=============================================================================
| 문서                                      | 역할                       |
|-------------------------------------------|----------------------------|
| example3.json                             | 정책 / 의도 / 구조 정의    |
| ai_server.py                              | 실행 / 조립 / 검증         |
| docs/db_jsonb_schema.md                   | DB JSONB 컬럼 구조 정의    |
| docs/python_jsonb_implementation_guide.md | Python JSONB 구현 규약     |
| docs/example3_policy.md                   | 질의(INTENT) 정책 정의     |
| docs/ai_server_plan.md                    | 서버 설계 의도 및 규칙     |

=============================================================================
"""

import json
import logging
import os
import re
from typing import Any, Optional

import psycopg2
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# =============================================================================
# 로깅 설정
# docs/ai_server_plan.md 6절 참조: 로그 기록 규칙
# =============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# =============================================================================
# FastAPI 앱 생성
# =============================================================================
app = FastAPI(
    title="SLM 운영 질의 응답 서버",
    description="자연어 질의를 정의된 정책에 따라 해석하고 SQL 실행 결과를 설명하는 서버",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# =============================================================================
# 환경변수 기반 DB 접속 정보
# docs/ai_server_task.md 참조: 접속 정보는 환경변수 기반으로 처리
# =============================================================================
DB_HOST = os.environ.get("DB_HOST", "112.166.183.65")
DB_PORT = os.environ.get("DB_PORT", "25479")
DB_NAME = os.environ.get("DB_NAME", "postgres")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "DJpost0827///")


# =============================================================================
# 요청 모델
# =============================================================================
class AskRequest(BaseModel):
    user_question: str


# =============================================================================
# 예외 클래스
# docs/ai_server_plan.md 6.5절 참조: JSONB 스키마 불일치
# =============================================================================
class JsonbSchemaViolation(Exception):
    """JSONB 스키마 불일치 예외"""

    def __init__(self, message: str, path: str = ""):
        self.message = message
        self.path = path
        super().__init__(self.message)


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
    """
    sitenames = set()

    queries = [
        "SELECT DISTINCT sitename FROM tb_tag_info WHERE sitename IS NOT NULL",
        "SELECT DISTINCT sitename FROM tb_block_info WHERE sitename IS NOT NULL",
        "SELECT DISTINCT sitename FROM tb_service_reservoir_info WHERE sitename IS NOT NULL",
        "SELECT DISTINCT sitename FROM tb_service_booster_station_info WHERE sitename IS NOT NULL",
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


# 서버 시작 시 DB에서 로드
KNOWN_SITENAMES = load_sitenames_from_db()
KNOWN_BLOCK_LEVELS = load_block_levels_from_db()


# =============================================================================
# 질문 정규화 함수
# =============================================================================
def normalize_question(question: str) -> str:
    """
    질문 문자열을 정규화한다.
    - 연속 공백을 단일 공백으로
    - 앞뒤 공백 제거
    """
    return re.sub(r"\s+", " ", question.strip())


def normalize_for_matching(text: str) -> str:
    """
    매칭용 정규화: 조사 제거, 공백 제거, 소문자 변환
    """
    text = text.lower()
    text = re.sub(r"\s+", "", text)
    text = re.sub(
        r"(의|은|는|이|가|을|를|에|에서|으로|와|과|도|만|까지|부터)",
        "",
        text
    )
    text = re.sub(r"[?!.]", "", text)
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
    "배수지": ["RESERVOIR_", "TODAY_FLOW_", "TODAY_OUTFLOW_"],
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
    "NIGHT_MIN_FLOW_",
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
    3. 가장 높은 점수의 INTENT 선택
    """
    normalized_user = normalize_for_matching(user_question)
    facility_type = extract_facility_type_from_question(user_question)

    best_match = None
    best_score = 0.0

    for intent_def in INTENT_DEFINITIONS:
        intent_name = intent_def.get("intent", "")

        # 시설 타입 필터링
        if not is_intent_for_facility(intent_name, facility_type):
            continue

        for q in intent_def.get("questions", []):
            normalized_q = normalize_for_matching(q)
            score = calculate_match_score(normalized_user, normalized_q, intent_name, user_question, q)

            if score > best_score:
                best_score = score
                best_match = intent_def

    return best_match


# =============================================================================
# DB 연결 및 SQL 실행 함수
# docs/ai_server_task.md 참조:
# - psycopg2 사용
# - SQL 템플릿이 빈 문자열이면 실행하지 않는다
# =============================================================================
def get_db_connection():
    """
    psycopg2를 사용하여 DB 연결을 반환한다.
    docs/ai_server_plan.md 6.1절 참조: DB 접속 오류 처리
    """
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )


def execute_sql(sql_template: str, params: dict) -> tuple:
    """
    SQL 템플릿에 파라미터를 치환하고 실행한다.
    반환: (rows, columns)

    docs/ai_server_task.md 참조:
    - SQL 템플릿이 빈 문자열이면 실행하지 않는다
    - 템플릿 변수 치환은 정책에 정의된 항목만 허용
    """
    if not sql_template or not sql_template.strip():
        return [], []

    # 템플릿 변수 치환
    sql = sql_template
    for key, value in params.items():
        placeholder = "{" + key + "}"
        if placeholder in sql:
            if value is not None:
                # SQL 인젝션 방지를 위한 이스케이프
                escaped_value = str(value).replace("'", "''")
                sql = sql.replace(placeholder, escaped_value)

    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(sql)
        if cur.description:
            columns = [desc[0] for desc in cur.description]
            rows = cur.fetchall()
            return rows, columns
        return [], []
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


# =============================================================================
# JSONB 파서 함수
# docs/python_jsonb_implementation_guide.md 참조:
# - JSONB 컬럼은 Python에서 dict로 처리한다
# - dict.get().get() 체인 금지
# - KeyError를 무시하는 try/except 금지
# - 기본값으로 구조 오류를 숨기는 코드 금지
# =============================================================================

def parse_reservoir_general_overview(jsonb: dict) -> dict:
    """
    배수지 general_overview JSONB 파싱
    JSONB schema reference: docs/db_jsonb_schema.md 3.2절
    """
    result = {}

    if "install_location" in jsonb:
        result["install_location"] = jsonb["install_location"]

    if "operating_status" in jsonb:
        result["operating_status"] = jsonb["operating_status"]

    if "supply_population" in jsonb:
        result["supply_population"] = jsonb["supply_population"]

    if "facility_capacity_m3" in jsonb:
        result["facility_capacity_m3"] = jsonb["facility_capacity_m3"]

    if "reservoir_spec" in jsonb:
        spec = jsonb["reservoir_spec"]
        if not isinstance(spec, dict):
            raise JsonbSchemaViolation(
                "reservoir_spec은 객체여야 합니다.",
                path="general_overview.reservoir_spec"
            )
        if "count" in spec:
            result["reservoir_count"] = spec["count"]
        if "H.W.L" in spec:
            result["hwl"] = spec["H.W.L"]
        if "L.W.L" in spec:
            result["lwl"] = spec["L.W.L"]

    if "emergency_water_plan" in jsonb:
        plan = jsonb["emergency_water_plan"]
        if not isinstance(plan, list):
            raise JsonbSchemaViolation(
                "emergency_water_plan은 배열이어야 합니다.",
                path="general_overview.emergency_water_plan"
            )
        result["emergency_water_plan"] = plan

    if "water_truck_accessible" in jsonb:
        result["water_truck_accessible"] = jsonb["water_truck_accessible"]

    if "water_truck_turning_possible" in jsonb:
        result["water_truck_turning_possible"] = jsonb["water_truck_turning_possible"]

    return result


def parse_booster_general_overview(jsonb: dict) -> dict:
    """
    가압장 general_overview JSONB 파싱
    JSONB schema reference: docs/db_jsonb_schema.md 4.2절
    """
    result = {}

    if "pump" in jsonb:
        pump = jsonb["pump"]
        if not isinstance(pump, dict):
            raise JsonbSchemaViolation(
                "pump은 객체여야 합니다.",
                path="general_overview.pump"
            )
        if "count" in pump:
            result["pump_count"] = pump["count"]
        if "head_m" in pump:
            result["pump_head_m"] = pump["head_m"]
        if "contractor" in pump:
            result["pump_contractor"] = pump["contractor"]
        if "manufacturer" in pump:
            result["pump_manufacturer"] = pump["manufacturer"]
        if "reservoir_linked" in pump:
            result["reservoir_linked"] = pump["reservoir_linked"]
        if "linked_reservoirs" in pump:
            result["linked_reservoirs"] = pump["linked_reservoirs"]

    if "booster_type" in jsonb:
        result["booster_type"] = jsonb["booster_type"]

    if "install_year" in jsonb:
        result["install_year"] = jsonb["install_year"]

    if "install_location" in jsonb:
        result["install_location"] = jsonb["install_location"]

    if "operating_status" in jsonb:
        result["operating_status"] = jsonb["operating_status"]

    if "facility_capacity_m3" in jsonb:
        result["facility_capacity_m3"] = jsonb["facility_capacity_m3"]

    return result


def parse_block_general_overview(jsonb: dict) -> dict:
    """
    블록 general_overview JSONB 파싱
    JSONB schema reference: docs/db_jsonb_schema.md 5.2절
    """
    result = {}

    if "customer_count" in jsonb:
        result["customer_count"] = jsonb["customer_count"]

    if "install_location" in jsonb:
        result["install_location"] = jsonb["install_location"]

    if "non_revenue_water_rate" in jsonb:
        result["non_revenue_water_rate"] = jsonb["non_revenue_water_rate"]

    if "pipeline_length" in jsonb:
        pl = jsonb["pipeline_length"]
        if not isinstance(pl, dict):
            raise JsonbSchemaViolation(
                "pipeline_length은 객체여야 합니다.",
                path="general_overview.pipeline_length"
            )
        if "total" in pl:
            result["pipeline_total"] = pl["total"]
        if "old" in pl:
            result["pipeline_old"] = pl["old"]

    if "large_customer_status" in jsonb:
        lcs = jsonb["large_customer_status"]
        if not isinstance(lcs, dict):
            raise JsonbSchemaViolation(
                "large_customer_status는 객체여야 합니다.",
                path="general_overview.large_customer_status"
            )
        if "count" in lcs:
            result["large_customer_count"] = lcs["count"]
        if "base_month_usage" in lcs:
            result["large_customer_base_month_usage"] = lcs["base_month_usage"]

    return result


def parse_pressure_reducing_general_overview(jsonb: dict) -> dict:
    """
    감압시설 general_overview JSONB 파싱
    JSONB schema reference: docs/db_jsonb_schema.md 6.2절
    """
    result = {}

    if "install_location" in jsonb:
        result["install_location"] = jsonb["install_location"]

    if "operating_status" in jsonb:
        result["operating_status"] = jsonb["operating_status"]

    if "pressure_reducing_valve" in jsonb:
        prv = jsonb["pressure_reducing_valve"]
        if not isinstance(prv, dict):
            raise JsonbSchemaViolation(
                "pressure_reducing_valve은 객체여야 합니다.",
                path="general_overview.pressure_reducing_valve"
            )
        if "manufacturer" in prv:
            result["manufacturer"] = prv["manufacturer"]
        if "pipe_diameter" in prv:
            result["pipe_diameter"] = prv["pipe_diameter"]
        if "control_method" in prv:
            result["control_method"] = prv["control_method"]

    return result


def parse_general_overview(jsonb: Any, facility_type: Optional[str]) -> dict:
    """
    시설 타입에 따라 적절한 JSONB 파서를 선택하여 파싱한다.
    docs/python_jsonb_implementation_guide.md 참조
    """
    if jsonb is None:
        return {}

    if isinstance(jsonb, str):
        jsonb = json.loads(jsonb)

    if not isinstance(jsonb, dict):
        raise JsonbSchemaViolation(
            "general_overview는 객체여야 합니다.",
            path="general_overview"
        )

    if facility_type == "배수지":
        return parse_reservoir_general_overview(jsonb)
    elif facility_type == "가압장":
        return parse_booster_general_overview(jsonb)
    elif facility_type in ["소블록", "중블록", "대블록"]:
        return parse_block_general_overview(jsonb)
    elif facility_type in ["감압시설", "감압설비"]:
        return parse_pressure_reducing_general_overview(jsonb)
    else:
        return dict(jsonb)


# =============================================================================
# answer_template 처리 함수
# docs/ai_server_task.md 참조:
# - placeholder 치환 값이 null/None/빈 문자열인 경우 해당 문장 라인은 출력에서 완전히 제외
# - "null입니다", "없음", "미정" 등의 문구를 생성하지 않는다
# - 하나의 문장에 여러 placeholder가 포함된 경우 그 중 하나라도 null이면 해당 문장은 제외
# =============================================================================

def is_null_or_empty(value: Any) -> bool:
    """값이 null, None, 또는 빈 문자열인지 확인한다."""
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def render_template_line(line: str, data: dict) -> Optional[str]:
    """
    템플릿 라인의 placeholder를 치환한다.
    placeholder 치환 값이 null/None/빈 문자열인 경우 None 반환
    """
    placeholders = re.findall(r"\{(\w+)\}", line)

    if not placeholders:
        return line

    for placeholder in placeholders:
        value = data.get(placeholder)
        if is_null_or_empty(value):
            return None

    result = line
    for placeholder in placeholders:
        value = data.get(placeholder)
        result = result.replace("{" + placeholder + "}", str(value))

    return result


def render_answer_template(template: dict, data: dict) -> dict:
    """
    answer_template을 데이터로 렌더링한다.
    docs/ai_server_task.md 참조
    """
    result = {}

    # summary 렌더링
    if "summary" in template:
        rendered = render_template_line(template["summary"], data)
        if rendered:
            result["summary"] = rendered

    # detail 렌더링
    if "detail" in template:
        detail_lines = []
        for line in template["detail"]:
            rendered = render_template_line(line, data)
            if rendered:
                detail_lines.append(rendered)
        if detail_lines:
            result["detail"] = detail_lines

    # reference 렌더링
    if "reference" in template:
        ref = template["reference"]
        ref_result = {}
        if "title" in ref:
            ref_result["title"] = ref["title"]
        if "items" in ref:
            ref_items = []
            for item in ref["items"]:
                rendered = render_template_line(item, data)
                if rendered:
                    ref_items.append(rendered)
            if ref_items:
                ref_result["items"] = ref_items
        if ref_result:
            result["reference"] = ref_result

    # recommend_questions 렌더링
    if "recommend_questions" in template:
        rec = template["recommend_questions"]
        rec_result = {}
        if "title" in rec:
            rec_result["title"] = rec["title"]
        if "items" in rec:
            rec_items = []
            for item in rec["items"]:
                rendered = render_template_line(item, data)
                if rendered:
                    rec_items.append(rendered)
            if rec_items:
                rec_result["items"] = rec_items
        if rec_result:
            result["recommend_questions"] = rec_result

    return result


# =============================================================================
# 응답 생성 함수
# docs/ai_server_plan.md 6절, 7절 참조
# =============================================================================

def build_error_response(
    message: str,
    recommend_questions: Optional[list] = None,
) -> dict:
    """
    오류 응답을 생성한다.
    docs/ai_server_plan.md 6절 참조
    """
    response = {
        "status": "ERROR",
        "message": message,
    }
    if recommend_questions:
        response["answer"] = {
            "recommend_questions": {
                "title": "추가로 추천 질의입니다.",
                "items": recommend_questions,
            }
        }
    return response


def build_success_response(
    intent: str,
    answer: dict,
    graph_type: str,
    data: Optional[list] = None,
    table_columns: Optional[list] = None,
    table_type: Optional[str] = None,
) -> dict:
    """
    성공 응답을 생성한다.
    docs/ai_server_plan.md 7절 참조
    """
    response = {
        "status": "OK",
        "intent": intent,
        "answer": answer,
        "graph_type": graph_type,
    }
    if data is not None:
        response["data"] = data
    if table_columns is not None:
        response["table_columns"] = table_columns
    if table_type is not None:
        response["table_type"] = table_type
    return response


def build_no_data_response(intent: str, answer_template: dict) -> dict:
    """
    조회 결과 없음 응답을 생성한다.
    docs/ai_server_plan.md 6.3절 참조:
    - 상태: OK
    - 메시지: "조회된 데이터가 없습니다."
    """
    result = {
        "summary": "조회된 데이터가 없습니다.",
    }

    if "recommend_questions" in answer_template:
        result["recommend_questions"] = answer_template["recommend_questions"]

    return {
        "status": "OK",
        "message": "조회된 데이터가 없습니다.",
        "intent": intent,
        "answer": result,
    }


# =============================================================================
# 데이터 후처리 함수
# =============================================================================

def process_sql_result(
    rows: list,
    columns: list,
    intent_def: dict,
    params: dict,
) -> dict:
    """
    SQL 실행 결과를 후처리하여 응답 데이터를 생성한다.
    - JSONB 컬럼은 Python 파서로 해석
    """
    if not rows:
        return {}

    # 첫 번째 행을 기본 데이터로 사용
    row_dict = dict(zip(columns, rows[0]))
    data = dict(row_dict)

    # 파라미터도 데이터에 병합
    data.update(params)

    # general_overview JSONB 파싱
    if "general_overview" in data and data["general_overview"]:
        facility_type = params.get("facilitytype") or params.get("block_level")
        parsed = parse_general_overview(data["general_overview"], facility_type)
        data.update(parsed)

    # meta JSONB는 그대로 보존
    if "meta" in data and data["meta"]:
        if isinstance(data["meta"], str):
            data["meta"] = json.loads(data["meta"])

    
    # =============================================================================
    # INTENT별 매뉴얼 / 다이어그램 / 이미지 블록 후처리
    # =============================================================================
    intent = intent_def.get("intent")

    # -------------------------------------------------
    # 초동대응 매뉴얼 (배수지 / 가압장 / 블록 / 감압시설 공통)
    # -------------------------------------------------
    if intent in [
        "RESERVOIR_INITIAL_RESPONSE_MANUAL",
        "BOOSTER_STATION_INITIAL_RESPONSE_MANUAL",
        "BLOCK_INITIAL_RESPONSE_MANUAL",
        "PRESSURE_REDUCING_INITIAL_RESPONSE_MANUAL",
    ]:
        manual_url = data.get("manual_url")
        if manual_url:
            data["manual_image_block"] = {
                "type": "image",
                "url": manual_url,
                "title": f"{params.get('sitename')} 초동대응 매뉴얼"
            }
            logger.info(f"manual_url={manual_url}")
    # -------------------------------------------------
    # 시스템 / 계통도 (있을 경우)
    # -------------------------------------------------
    if intent in [
        "RESERVOIR_SYSTEM_DIAGRAM",
        "BOOSTER_STATION_SYSTEM_DIAGRAM",
        "BLOCK_SYSTEM_DIAGRAM",
        "PRESSURE_REDUCING_SYSTEM_DIAGRAM",
    ]:
        diagram_url = data.get("system_diagram_url")
        if diagram_url:
            data["system_diagram_block"] = {
                "type": "diagram",
                "url": diagram_url,
                "title": f"{params.get('sitename')} 시스템 계통도"
            }

    return data


# =============================================================================
# 메인 엔드포인트
# docs/ai_server_task.md 참조
# =============================================================================

@app.post("/ask")
async def ask(request: AskRequest):
    """
    POST /ask 엔드포인트

    요청: { "user_question": "..." }
    응답: example3.json 및 ai_server_plan.md에서 정의된 구조를 따른다.
    """
    user_question = request.user_question
    logger.info(f"질의 수신: {user_question}")

    # 1. INTENT 매칭
    intent_def = match_intent(user_question)

    if not intent_def:
        # docs/ai_server_plan.md 5.2절: 매칭 실패
        logger.warning(f"INTENT 매칭 실패: {user_question}")
        return build_error_response(
            message="질문을 이해하지 못했습니다. 다른 방식으로 질문해 주세요.",
            recommend_questions=[
                "1. 배수지 일반현황은?",
                "2. 가압장 운영현황은?",
                "3. 소블록 압력 현황은?",
            ],
        )

    intent = intent_def["intent"]
    sql_template = intent_def.get("sql", "")
    answer_template = intent_def.get("answer_template", {})
    graph_type = intent_def.get("graph_type", "none")
    table_columns = intent_def.get("table_columns")
    table_type = intent_def.get("table_type")

    logger.info(f"INTENT 매칭 성공: {intent}")

    # 2. 파라미터 추출
    block_level = extract_block_level(user_question)
    sitename = extract_sitename(user_question)
    facilitytype = extract_facilitytype(user_question, block_level)
    datainfo = extract_datainfo(user_question)
    limit_val = extract_limit(user_question)
    alarm_msg = extract_alarm_msg(user_question)

    params = {
        "sitename": sitename,
        "facilitytype": facilitytype,
        "block_level": block_level,
        "datainfo": datainfo,
        "limit": limit_val,
        "alarm_msg": alarm_msg,
    }

    # 3. sitename 검증
    # docs/ai_server_task.md: sitename 누락 시 SQL 실행 금지
    if "{sitename}" in sql_template and not sitename:
        logger.warning(f"sitename 누락: {user_question}")
        return build_error_response(
            message="현장명(sitename)을 명시해 주세요. 예: '신평 배수지 일반현황은?'",
            recommend_questions=[
                "1. 신평 배수지 일반현황은?",
                "2. 행정 가압장 운영현황은?",
                "3. 합덕3 소블록 압력 현황은?",
            ],
        )

    # 4. facilitytype 검증
    if "{facilitytype}" in sql_template and not facilitytype:
        logger.warning(f"facilitytype 누락: {user_question}")
        return build_error_response(
            message="시설 유형을 명시해 주세요. (배수지, 가압장, 감압시설, 소블록/중블록/대블록)",
            recommend_questions=[
                "1. 신평 배수지 수위 현황은?",
                "2. 행정 가압장 압력 현황은?",
                "3. 합덕3 소블록 유량 현황은?",
            ],
        )

    # 5. block_level 검증
    if "{block_level}" in sql_template and not block_level:
        logger.warning(f"block_level 누락: {user_question}")
        return build_error_response(
            message="블록 유형을 명시해 주세요. (소블록, 중블록, 대블록)",
            recommend_questions=[
                "1. 합덕3 소블록 일반현황은?",
                "2. 행정1-1 중블록 운영현황은?",
            ],
        )

    # 6. SQL 실행
    if not sql_template or not sql_template.strip():
        rendered_answer = render_answer_template(answer_template, params)
        return build_success_response(
            intent=intent,
            answer=rendered_answer,
            graph_type=graph_type,
        )

    try:
        rows, columns = execute_sql(sql_template, params)
    except psycopg2.OperationalError as e:
        # docs/ai_server_plan.md 6.1절: DB 접속 오류
        logger.error(f"DB 접속 오류: {e}")
        return build_error_response(
            message="데이터베이스 연결 오류가 발생했습니다.",
        )
    except psycopg2.Error as e:
        # docs/ai_server_plan.md 6.2절: SQL 실행 오류
        logger.error(f"SQL 실행 오류: {e}")
        return build_error_response(
            message="데이터베이스 연결 오류가 발생했습니다.",
        )

    # 7. 결과 확인
    if not rows:
        # docs/ai_server_plan.md 6.3절: 조회 결과 없음
        logger.info(f"조회 결과 없음: {intent}, params={params}")
        return build_no_data_response(intent, answer_template)

    # 8. 데이터 후처리
    try:
        processed_data = process_sql_result(rows, columns, intent_def, params)
    except JsonbSchemaViolation as e:
        # docs/ai_server_plan.md 6.5절: JSONB 스키마 불일치
        logger.error(f"JSONB 스키마 위반: {e.message}, path: {e.path}")
        return build_error_response(
            message="데이터 구조 오류가 발생했습니다.",
        )
    # 9. answer_template 렌더링
    rendered_answer = render_answer_template(answer_template, processed_data)

    # -------------------------------------------------
    # detail에 UI 블록 삽입 (공통)
    # -------------------------------------------------
    detail = rendered_answer.get("detail", [])

    if "manual_image_block" in processed_data:
        detail.append(processed_data["manual_image_block"])

    if "system_diagram_block" in processed_data:
        detail.append(processed_data["system_diagram_block"])

    if detail:
        rendered_answer["detail"] = detail

    # 10. 응답 생성 (data 항목 제거)
    return build_success_response(
        intent=intent,
        answer=rendered_answer,
        graph_type=graph_type,
    )



# =============================================================================
# 헬스 체크 엔드포인트
# =============================================================================

@app.get("/health")
async def health_check():
    """서버 상태 확인용 엔드포인트"""
    return {"status": "ok"}


# =============================================================================
# 메인 실행
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
