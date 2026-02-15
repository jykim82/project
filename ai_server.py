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

import asyncio
import csv
import glob as glob_module
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Optional

import psycopg2
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from slm_config import ENABLE_KEYWORD_FALLBACK, get_model, set_model
from ollama_client import OllamaClient, OllamaConnectionError
from intent_index import IntentIndex
from intent_classifier import IntentClassifier
from param_extractor import ParamExtractor
from query_validator import QueryValidator, CORRECTION_TEMPLATES, CORRECTION_HINTS
from session_manager import SessionManager

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
# SLM 모듈 인스턴스 (서버 시작 시 초기화)
# =============================================================================
ollama_client = OllamaClient()
intent_index = IntentIndex()
session_manager = SessionManager()
# intent_classifier, param_extractor, query_validator는 startup에서 초기화
intent_classifier: Optional[IntentClassifier] = None
param_extractor_instance: Optional[ParamExtractor] = None
query_validator: Optional[QueryValidator] = None

_cleanup_task: Optional[asyncio.Task] = None

# =============================================================================
# CSV 내보내기 설정
# =============================================================================
MAX_TABLE_ROWS = 1000  # JSON 응답 최대 행 수 (테이블)
MAX_GRAPH_ROWS = 5000  # JSON 응답 최대 행 수 (그래프/차트)
CSV_EXPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "csv_exports")
CSV_MAX_AGE_SECONDS = 3600  # CSV 파일 보존 기간 (1시간)


def save_csv(rows: list, columns: list, intent: str, session_id: str) -> str:
    """SQL 결과를 CSV로 저장하고 파일명을 반환한다."""
    os.makedirs(CSV_EXPORT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{intent}_{session_id}_{timestamp}.csv"
    filepath = os.path.join(CSV_EXPORT_DIR, filename)

    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        writer.writerows(rows)

    logger.info(f"CSV 저장: {filename} ({len(rows)}행)")
    return filename


def downsample_rows(rows: list, max_rows: int) -> list:
    """시계열 데이터를 다운샘플링한다. 첫 행과 마지막 행은 보존."""
    if len(rows) <= max_rows:
        return rows
    step = len(rows) / max_rows
    sampled = []
    for i in range(max_rows - 1):
        idx = int(i * step)
        sampled.append(rows[idx])
    sampled.append(rows[-1])  # 마지막 행 보존
    return sampled


def stratified_sample(rows: list, columns: list, max_rows: int) -> list:
    """sitename 컬럼 기준으로 균등 샘플링한다.

    sitename이 여러 개인 경우 각 sitename에서 비례 배분하여 추출.
    sitename 컬럼이 없거나 단일 sitename이면 앞에서 자른다.
    """
    if len(rows) <= max_rows:
        return rows
    if "sitename" not in columns:
        return rows[:max_rows]

    site_idx = columns.index("sitename")
    # sitename별 그룹핑
    groups = {}
    for row in rows:
        sn = row[site_idx] if isinstance(row, (list, tuple)) else row.get("sitename", "")
        groups.setdefault(sn, []).append(row)

    if len(groups) <= 1:
        return rows[:max_rows]

    # 비례 배분 (최소 1행 보장)
    sampled = []
    remaining = max_rows
    site_list = sorted(groups.keys())
    per_site = max(1, max_rows // len(site_list))

    for sn in site_list:
        site_rows = groups[sn]
        take = min(per_site, len(site_rows), remaining)
        # 균일 간격 샘플링
        if take >= len(site_rows):
            sampled.extend(site_rows)
        else:
            step = len(site_rows) / take
            for i in range(take):
                sampled.append(site_rows[int(i * step)])
        remaining -= take
        if remaining <= 0:
            break

    return sampled


def cleanup_old_csv_files():
    """CSV_MAX_AGE_SECONDS 이상 경과한 CSV 파일을 삭제한다."""
    if not os.path.exists(CSV_EXPORT_DIR):
        return
    now = datetime.now().timestamp()
    count = 0
    for filepath in glob_module.glob(os.path.join(CSV_EXPORT_DIR, "*.csv")):
        if now - os.path.getmtime(filepath) > CSV_MAX_AGE_SECONDS:
            try:
                os.remove(filepath)
                count += 1
            except OSError:
                pass
    if count:
        logger.info(f"CSV 파일 정리: {count}개 삭제")


async def _session_cleanup_loop():
    """백그라운드: 60초마다 만료 세션 정리 + CSV 파일 정리"""
    while True:
        await asyncio.sleep(60)
        session_manager.cleanup_expired()
        cleanup_old_csv_files()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작/종료 시 실행되는 lifespan 이벤트"""
    global intent_classifier, param_extractor_instance, query_validator, _cleanup_task

    # CSV 내보내기 디렉토리 생성
    os.makedirs(CSV_EXPORT_DIR, exist_ok=True)

    # Intent 인덱스 빌드
    intent_index.build(INTENT_DEFINITIONS)

    # 분류기, 추출기, 검증기 초기화
    intent_classifier = IntentClassifier(ollama_client, intent_index)
    param_extractor_instance = ParamExtractor(
        known_sitenames=KNOWN_SITENAMES,
        known_block_levels=KNOWN_BLOCK_LEVELS,
        ollama=ollama_client,
    )
    query_validator = QueryValidator(intent_index, KNOWN_SITENAMES, SITENAME_FACILITY_MAP)

    # Ollama 연결 상태 로그
    if ollama_client.health_check():
        logger.info(f"Ollama 연결 성공: {get_model()}")
    else:
        logger.warning("Ollama 연결 실패 — 키워드 매칭 폴백 모드로 동작")

    # 세션 정리 백그라운드 태스크
    _cleanup_task = asyncio.create_task(_session_cleanup_loop())

    yield

    # shutdown
    if _cleanup_task:
        _cleanup_task.cancel()
        try:
            await _cleanup_task
        except asyncio.CancelledError:
            pass


# =============================================================================
# FastAPI 앱 생성
# =============================================================================
app = FastAPI(
    title="SLM 운영 질의 응답 서버",
    description="자연어 질의를 정의된 정책에 따라 해석하고 SQL 실행 결과를 설명하는 서버",
    lifespan=lifespan,
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
    session_id: Optional[str] = None


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
    """
    result = re.sub(r"\s+", " ", question.strip())
    # 한글 뒤 공백 + 숫자 → 공백 제거 (예: "행정 1-1" → "행정1-1", "남산 1" → "남산1")
    result = re.sub(r"([\uac00-\ud7a3])\s+(\d)", r"\1\2", result)
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
    """
    normalized_user = normalize_for_matching(user_question)
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
            normalized_q = normalize_for_matching(q)
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


def _split_sql_statements(sql: str) -> list:
    """세미콜론으로 SQL을 분리하되, 문자열 리터럴('...')안의 세미콜론은 무시한다."""
    statements = []
    current = []
    in_quote = False
    for char in sql:
        if char == "'" and not in_quote:
            in_quote = True
            current.append(char)
        elif char == "'" and in_quote:
            in_quote = False
            current.append(char)
        elif char == ";" and not in_quote:
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
        else:
            current.append(char)
    # 마지막 statement
    stmt = "".join(current).strip()
    if stmt:
        statements.append(stmt)
    return statements


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
    # from_ts, to_ts는 SQL에서 따옴표 없이 사용되므로 여기서 감싸줘야 한다
    _QUOTE_PARAMS = {"from_ts", "to_ts"}
    sql = sql_template
    for key, value in params.items():
        placeholder = "{" + key + "}"
        if placeholder in sql:
            if value is not None:
                # SQL 인젝션 방지를 위한 이스케이프
                escaped_value = str(value).replace("'", "''")
                if key in _QUOTE_PARAMS:
                    escaped_value = f"'{escaped_value}'"
                sql = sql.replace(placeholder, escaped_value)

    # 세미콜론으로 구분된 다중 SQL인 경우 개별 실행 후 결과 병합
    # psycopg2는 nextset()을 지원하지 않으므로 수동 분리 필요
    # 문자열 리터럴('...')안의 세미콜론은 무시한다
    statements = _split_sql_statements(sql)

    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        all_rows = []
        all_columns = []

        for stmt in statements:
            cur.execute(stmt)
            if cur.description:
                cols = [desc[0] for desc in cur.description]
                rows = cur.fetchall()
                if not all_columns:
                    # 첫 번째 결과셋: 기본 columns/rows
                    all_columns = cols
                    all_rows = rows
                else:
                    # 후속 결과셋: 첫 행 데이터를 첫 번째 결과에 병합
                    # (첫 번째 결과가 1행 메타 + 두 번째가 다중행 상세인 패턴)
                    if all_rows and len(all_rows) == 1:
                        # 첫 번째 결과가 1행이면 → 메타 데이터로 취급
                        # 두 번째 결과를 메인으로 교체, 첫 번째 행 데이터를 extra에 저장
                        first_row_data = dict(zip(all_columns, all_rows[0]))
                        all_columns = cols + [f"_extra_{k}" for k in first_row_data]
                        all_rows = [
                            tuple(list(row) + list(first_row_data.values()))
                            for row in rows
                        ]
                    else:
                        # 두 번째 결과 컬럼을 추가 (행 수가 같을 때)
                        all_columns = all_columns + cols
                        if len(rows) == len(all_rows):
                            all_rows = [
                                tuple(list(a) + list(b))
                                for a, b in zip(all_rows, rows)
                            ]

        return all_rows, all_columns
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


def render_template_line(line, data: dict):
    """
    템플릿 라인의 placeholder를 치환한다.
    placeholder 치환 값이 null/None/빈 문자열인 경우 None 반환

    line이 dict인 경우 {"prefix": "•", "text": "..."} 구조를 처리하여
    {"prefix": "•", "text": "치환된 텍스트"} 형태로 반환한다.
    line이 str인 경우 기존처럼 치환된 문자열을 반환한다.
    """
    if isinstance(line, dict):
        prefix = line.get("prefix", "")
        text = line.get("text", "")
        rendered_text = _render_text(text, data)
        if rendered_text is None:
            return None
        return {"prefix": prefix, "text": rendered_text}

    return _render_text(line, data)


_UNIT_PLACEHOLDER_NAMES = {
    "unit", "water_level_unit", "pressure_unit",
    "avg_outflow_unit", "avg_inflow_unit", "usage_unit",
}


def _render_text(text: str, data: dict) -> Optional[str]:
    """문자열 내 placeholder를 치환한다. null 값이 있으면 None 반환.
    단, unit 계열 placeholder는 null이어도 빈 문자열로 치환한다."""
    # {sitename}{facilitytype} 붙어있으면 공백 삽입
    text = text.replace("{sitename}{facilitytype}", "{sitename} {facilitytype}")

    placeholders = re.findall(r"\{(\w+)\}", text)

    if not placeholders:
        return text

    for placeholder in placeholders:
        if placeholder in _UNIT_PLACEHOLDER_NAMES:
            continue
        value = data.get(placeholder)
        if is_null_or_empty(value):
            return None

    result = text
    for placeholder in placeholders:
        value = data.get(placeholder)
        if value is None and placeholder in _UNIT_PLACEHOLDER_NAMES:
            value = ""
        # sitename/facilitytype이 "%%"(전체 조회)이면 "전체"로 표시
        if placeholder in ("sitename", "facilitytype") and value == "%%":
            value = "전체"
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
    session_id: Optional[str] = None,
) -> dict:
    """
    오류 응답을 생성한다.
    docs/ai_server_plan.md 6절 참조
    """
    response = {
        "status": "ERROR",
        "message": message,
    }
    if session_id:
        response["session_id"] = session_id
    if recommend_questions:
        response["answer"] = {
            "recommend_questions": {
                "title": "추가로 추천 질의입니다.",
                "items": recommend_questions,
            }
        }
    return response


def build_correction_response(
    message: str,
    session_id: str,
    correction_hints: Optional[list] = None,
    intent: Optional[str] = None,
) -> dict:
    """
    NEED_CORRECTION 응답을 생성한다.
    질의 검증 실패 시 정정 요청 응답.
    """
    hints = correction_hints or []
    recommend_items = [
        {"prefix": f"{i+1}.", "text": h} for i, h in enumerate(hints[:5])
    ]
    response = {
        "status": "NEED_CORRECTION",
        "session_id": session_id,
        "message": message,
        "correction_hints": hints,
        "answer": {
            "recommend_questions": {
                "title": "다음과 같이 질문해 보세요.",
                "items": recommend_items,
            }
        },
    }
    if intent:
        response["intent"] = intent
    return response


def build_success_response(
    intent: str,
    answer: dict,
    graph_type: str,
    data: Optional[list] = None,
    table_columns: Optional[list] = None,
    table_type: Optional[str] = None,
    session_id: Optional[str] = None,
    csv_url: Optional[str] = None,
    total_rows: Optional[int] = None,
    data_truncated: bool = False,
    chart_data_type: Optional[str] = None,
    plot_type: Optional[str] = None,
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
    if session_id:
        response["session_id"] = session_id
    if data is not None:
        response["data"] = data
    if table_columns is not None:
        response["table_columns"] = table_columns
    if table_type is not None:
        response["table_type"] = table_type
    if csv_url is not None:
        response["csv_url"] = csv_url
    if total_rows is not None:
        response["total_rows"] = total_rows
    if data_truncated:
        response["data_truncated"] = True
    if chart_data_type:
        response["chart_data_type"] = chart_data_type
    if plot_type:
        response["plot_type"] = plot_type
    return response


def classify_chart_data_type(rows: list, columns: list) -> str:
    """
    트렌드 데이터의 tagtype 분포를 분석하여 차트 데이터 타입을 반환한다.
    - "analog": Analog Input/Output만 포함
    - "digital": Digital Input/Output만 포함
    - "mixed": 아나로그 + 디지털 혼합
    """
    if not rows or "tagtype" not in columns:
        return "analog"
    tag_idx = columns.index("tagtype")
    tagtypes = set()
    for row in rows:
        tt = row[tag_idx] if isinstance(row, (list, tuple)) else row.get("tagtype")
        if tt:
            tagtypes.add(tt)
    has_analog = any("Analog" in t for t in tagtypes)
    has_digital = any("Digital" in t for t in tagtypes)
    if has_analog and has_digital:
        return "mixed"
    elif has_digital:
        return "digital"
    return "analog"


def build_no_data_response(
    intent: str,
    answer_template: dict,
    params: dict = None,
    session_id: Optional[str] = None,
) -> dict:
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
        import copy
        rq = copy.deepcopy(answer_template["recommend_questions"])
        # recommend_questions 내 플레이스홀더 치환
        if params and "items" in rq:
            for item in rq["items"]:
                text = item.get("text", "")
                for key, val in params.items():
                    if val is not None:
                        text = text.replace("{" + key + "}", str(val))
                item["text"] = text
        result["recommend_questions"] = rq

    response = {
        "status": "OK",
        "message": "조회된 데이터가 없습니다.",
        "intent": intent,
        "answer": result,
    }
    if session_id:
        response["session_id"] = session_id
    return response


# =============================================================================
# 데이터 후처리 함수
# =============================================================================


def build_hunting_result_block(rows: list, columns: list) -> list:
    """
    v_reservoir_status_variance_5min 다중 행을 헌팅 점검 결과 리스트로 조립한다.
    반환 컬럼: datainfo, variance_status, diff_percent
    반환: [{"prefix": "-", "text": "수위(m) 계측1: 정상 (변동률 1.1%)"}, ...]
    """
    items = []
    for row in rows:
        row_dict = dict(zip(columns, row))
        datainfo = row_dict.get("datainfo", "").strip()
        status = row_dict.get("variance_status", "")
        diff_pct = row_dict.get("diff_percent")
        if datainfo and status:
            if diff_pct is not None:
                items.append({"prefix": "-", "text": f"{datainfo}: {status} (변동률 {diff_pct}%)"})
            else:
                items.append({"prefix": "-", "text": f"{datainfo}: {status}"})
    return items


def build_level_detail_block(rows: list, columns: list) -> list:
    """
    fn_reservoir_level_summary() 다중 행을 개별 수위 항목 리스트로 조립한다.
    반환 컬럼: log_time, out_sitename, out_facilitytype, out_datainfo,
              avg_latest, latest_val, avg_month, avg_year, unit
    반환: [{"prefix": "-", "text": "1지 수위: 3.45m"}, ...]
    """
    items = []
    for row in rows:
        row_dict = dict(zip(columns, row))
        datainfo = row_dict.get("out_datainfo", "")
        latest_val = row_dict.get("latest_val")
        unit = row_dict.get("unit", "")
        if latest_val is not None:
            items.append({"prefix": "-", "text": f"{datainfo}: {latest_val}{unit}"})
    return items


def build_today_flow_detail_block(rows: list, columns: list) -> list:
    """
    fn_today_outflow() 다중 행을 금일 적산 항목 리스트로 조립한다.
    반환 컬럼: sitename, facilitytype, datainfo, unit, today_outflow
    반환: [{"prefix": "-", "text": "유출적산: 1234.5m3"}, ...]
    """
    items = []
    for row in rows:
        row_dict = dict(zip(columns, row))
        datainfo = row_dict.get("datainfo", "")
        today_outflow = row_dict.get("today_outflow")
        unit = row_dict.get("unit", "")
        if today_outflow is not None:
            items.append({"prefix": "-", "text": f"{datainfo}: {today_outflow}{unit}"})
    return items


def build_outflow_detail_block(rows: list, columns: list) -> list:
    """
    fn_today_outflow_all() 다중 행을 전체 배수지 유출 현황 항목 리스트로 조립한다.
    반환 컬럼: result_sitename, result_facilitytype, result_today_outflow, result_is_total
    반환: [{"prefix": "-", "text": "신평 배수지: 1234.5㎥"}, ...]
    """
    items = []
    for row in rows:
        row_dict = dict(zip(columns, row))
        sitename = row_dict.get("result_sitename", "")
        facilitytype = row_dict.get("result_facilitytype", "")
        outflow = row_dict.get("result_today_outflow")
        is_total = row_dict.get("result_is_total", 0)
        if is_total == 1:
            continue  # 합계 행은 별도 처리 (total_outflow)
        if outflow is not None:
            items.append({"prefix": "-", "text": f"{sitename} {facilitytype}: {outflow}㎥"})
    return items


def build_alarm_list_block(rows: list, columns: list) -> list:
    """
    FACILITY_RECENT_ALARM 다중 행을 알람 목록 리스트로 조립한다.
    반환 컬럼: alarm_start_time, alarm_msg
    """
    items = []
    for row in rows:
        row_dict = dict(zip(columns, row))
        alarm_time = row_dict.get("alarm_start_time", "")
        alarm_msg = row_dict.get("alarm_msg", "")
        if alarm_time and alarm_msg:
            items.append({
                "prefix": "-",
                "text": f"알람 발생 시각 : {alarm_time}, 발생알람 : {alarm_msg}"
            })
    return items


def build_latest_value_list_block(rows: list, columns: list) -> list:
    """
    FACILITY_TAG_LATEST_VALUE 다중 행을 최신값 리스트로 조립한다.
    반환 컬럼: datadesc, latest_val, latest_time
    """
    items = []
    for row in rows:
        row_dict = dict(zip(columns, row))
        datadesc = row_dict.get("datadesc", "")
        latest_val = row_dict.get("latest_val", "")
        latest_time = row_dict.get("latest_time", "")
        if datadesc and latest_val is not None:
            items.append({
                "prefix": "-",
                "text": f"{datadesc}: {latest_val} ({latest_time})"
            })
    return items


def build_alarm_rank_block(rows: list, columns: list) -> list:
    """
    FACILITY_ALARM_TOP_COUNT 다중 행을 알람 누적건수 순위 리스트로 조립한다.
    반환 컬럼: alarm_msg, alarm_count
    마지막 항목에 '순으로 발생하였습니다.' 를 붙인다.
    """
    items = []
    for idx, row in enumerate(rows):
        row_dict = dict(zip(columns, row))
        alarm_msg = row_dict.get("alarm_msg", "")
        alarm_count = row_dict.get("alarm_count", 0)
        if alarm_msg:
            text = f"{alarm_msg} {alarm_count}건"
            if idx == len(rows) - 1:
                text += " 순으로 발생하였습니다."
            items.append({"prefix": "-", "text": text})
    return items


def build_pressure_detail_block(rows: list, columns: list) -> list:
    """
    FACILITY_PRESSURE_STATUS 다중 행을 압력 항목 리스트로 조립한다.
    반환 컬럼: datainfo, pressure_val, log_time, unit
    - 설정압력(Analog Output 개념) 제외
    - 동일 datainfo 중복 제거
    """
    items = []
    seen = set()
    for row in rows:
        row_dict = dict(zip(columns, row))
        datainfo = row_dict.get("datainfo", "").strip()
        if "설정" in datainfo:
            continue
        val = row_dict.get("pressure_val")
        log_time = row_dict.get("log_time", "")
        unit = row_dict.get("unit", "")
        dedup_key = (datainfo, val, log_time)
        if val is not None and dedup_key not in seen:
            seen.add(dedup_key)
            items.append({"prefix": "•", "text": f"{log_time} 기준 {datainfo}: {val}{unit}"})
    return items


def build_pressure_reference_block(rows: list, columns: list) -> list:
    """
    FACILITY_PRESSURE_STATUS 다중 행을 평균 압력 참고 자료로 조립한다.
    반환 컬럼: datainfo, month_avg, year_avg, unit
    - 설정압력(Analog Output 개념) 제외
    - 동일 datainfo 중복 제거
    """
    items = []
    seen = set()
    for row in rows:
        row_dict = dict(zip(columns, row))
        datainfo = row_dict.get("datainfo", "").strip()
        if "설정" in datainfo:
            continue
        month_avg = row_dict.get("month_avg")
        year_avg = row_dict.get("year_avg")
        unit = row_dict.get("unit", "")
        dedup_key = (datainfo, month_avg, year_avg)
        if dedup_key not in seen:
            seen.add(dedup_key)
            if month_avg is not None:
                items.append({"prefix": "-", "text": f"{datainfo} 금월 평균: {month_avg}{unit}"})
            if year_avg is not None:
                items.append({"prefix": "-", "text": f"{datainfo} 금년 평균: {year_avg}{unit}"})
    return items


def build_network_hop_detail_block(rows: list, columns: list) -> list:
    """
    fn_network_path_hop_detail() 다중 행을 source_sitename 기준 그룹핑하여 조립한다.
    반환 컬럼: source_sitename, source_facilitytype, source_equipmenttype,
              target_equipmenttype, link_device_interface, link_protocol
    반환: [{"prefix": "1.", "text": "당진시청의 연결 구간별 ..."}, {"prefix": "-", "text": "..."}, ...]
    """
    from collections import OrderedDict
    groups = OrderedDict()
    for row in rows:
        row_dict = dict(zip(columns, row))
        site = row_dict.get("source_sitename", "")
        ftype = row_dict.get("source_facilitytype", "")
        key = f"{site} {ftype}".strip() if site else ftype
        if key not in groups:
            groups[key] = []
        groups[key].append(row_dict)

    items = []
    for idx, (group_name, hops) in enumerate(groups.items(), 1):
        items.append({
            "prefix": f"{idx}.",
            "text": f"{group_name}의 연결 구간별 통신 방식 및 프로토콜 정보"
        })
        for hop in hops:
            src = hop.get("source_equipmenttype", "")
            tgt = hop.get("target_equipmenttype", "")
            interface = hop.get("link_device_interface", "")
            protocol = hop.get("link_protocol", "")
            items.append({
                "prefix": "-",
                "text": f"{src} - {tgt} : {interface} 통신, 프로토콜 : {protocol}"
            })
    return items


def build_network_status_block(rows: list, columns: list) -> list:
    """
    fn_network_path_trace_with_status() 다중 행을 sitename 기준 그룹핑하여 조립한다.
    반환 컬럼: pos, sitename, facilitytype, equipmenttype, status_code, rtt_ms, error_message
    """
    from collections import OrderedDict
    groups = OrderedDict()
    for row in rows:
        row_dict = dict(zip(columns, row))
        site = row_dict.get("sitename", "")
        ftype = row_dict.get("facilitytype", "")
        key = f"{site} {ftype}".strip() if site else ftype
        if key not in groups:
            groups[key] = []
        groups[key].append(row_dict)

    items = []
    items.append({
        "prefix": "•",
        "text": "네트워크 상태는 상위 네트워크 장비부터 현장까지 구간을 확인합니다."
    })
    for group_name, hops in groups.items():
        items.append({"prefix": "•", "text": group_name})
        for hop in hops:
            equip = hop.get("equipmenttype", "")
            status = hop.get("status_code")
            rtt = hop.get("rtt_ms")
            error = hop.get("error_message")
            if status and rtt is not None:
                rtt_val = int(float(str(rtt))) if rtt else 0
                items.append({
                    "prefix": "-",
                    "text": f"{equip} : {status}, 응답속도 : {rtt_val}ms"
                })
            elif status and error:
                items.append({
                    "prefix": "-",
                    "text": f"{equip} : {status}, {error}"
                })
            elif status:
                items.append({
                    "prefix": "-",
                    "text": f"{equip} : {status}"
                })
            else:
                items.append({
                    "prefix": "-",
                    "text": f"{equip} : 상태없음"
                })
    return items


def build_avg_usage_detail_block(rows: list, columns: list) -> list:
    """
    v_reservoir_info_status 다중 행을 개별 배수지 평균사용량 항목 리스트로 조립한다.
    반환 컬럼: sitename, avg_usage, usage_unit, ..., is_avg_usage_null
    반환: [{"prefix": "-", "text": "신평: 12.34m³/h"}, ...]
    """
    items = []
    for row in rows:
        row_dict = dict(zip(columns, row))
        sitename = row_dict.get("sitename", "")
        avg_usage = row_dict.get("avg_usage")
        usage_unit = row_dict.get("usage_unit", "")
        is_null = row_dict.get("is_avg_usage_null", "N")
        if is_null == "Y" or avg_usage is None:
            continue
        items.append({"prefix": "-", "text": f"{sitename}: {avg_usage}{usage_unit}"})
    return items


def build_equipment_table(meta: Any) -> list:
    """
    meta JSONB를 설비현황 테이블 데이터로 변환한다.

    meta 구조 예시:
    - dict 형태: 키가 설비 카테고리, 값이 설비 목록 배열
      {"펌프": [{"datadesc": "...", "status": "..."}, ...], ...}
    - list 형태: 각 항목이 설비 정보 객체
      [{"equipmenttype": "펌프", "datadesc": "...", "status": "..."}, ...]

    반환: [{"category": "...", "name": "...", "key": "value", ...}, ...]
    """
    if meta is None:
        return []

    if isinstance(meta, str):
        meta = json.loads(meta)

    result = []

    if isinstance(meta, dict):
        for category, items in meta.items():
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        row = {"category": category}
                        row.update(item)
                        result.append(row)
            elif isinstance(items, dict):
                row = {"category": category}
                row.update(items)
                result.append(row)
    elif isinstance(meta, list):
        for item in meta:
            if isinstance(item, dict):
                result.append(item)

    return result


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
    # INTENT별 다중 행 → detail_block 조립
    # example3.json 정책에서 {level_detail_block} 등의 placeholder는
    # SQL 결과 다중 행을 {"prefix", "text"} 리스트로 조립한다.
    # data에는 마커 문자열("__EXPAND__")을 넣어 render_answer_template이
    # 해당 라인을 제거하지 않도록 하고, _detail_blocks에 실제 리스트를 저장한다.
    # =============================================================================
    intent = intent_def.get("intent")
    data["_detail_blocks"] = {}
    _EXPAND_MARKER = "__EXPAND__"

    # -------------------------------------------------
    # 배수지 수위 현황: 개별 수위 데이터 조립
    # -------------------------------------------------
    if intent == "RESERVOIR_LEVEL_STATUS":
        data["level_detail_block"] = _EXPAND_MARKER
        data["_detail_blocks"]["level_detail_block"] = build_level_detail_block(rows, columns)

    # -------------------------------------------------
    # 금일 적산 현황: 개별 적산 데이터 조립
    # -------------------------------------------------
    if intent == "TODAY_FLOW_ACCUMULATION":
        data["today_flow_detail_block"] = _EXPAND_MARKER
        data["_detail_blocks"]["today_flow_detail_block"] = build_today_flow_detail_block(rows, columns)

    # -------------------------------------------------
    # 금일 전체 배수지 유출 현황: 개별 배수지 유출 데이터 조립
    # -------------------------------------------------
    if intent == "TODAY_OUTFLOW_ALL_STATUS":
        data["outflow_detail_block"] = _EXPAND_MARKER
        data["_detail_blocks"]["outflow_detail_block"] = build_outflow_detail_block(rows, columns)
        # total_outflow: is_total=1인 행에서 추출
        for row in rows:
            row_dict_tmp = dict(zip(columns, row))
            if row_dict_tmp.get("result_is_total") == 1:
                data["total_outflow"] = row_dict_tmp.get("result_today_outflow")
                break

    # -------------------------------------------------
    # 금일 전체 배수지 평균사용량: 개별 배수지 사용량 + 전체 평균 + 데이터 없음 목록
    # -------------------------------------------------
    if intent == "TODAY_RESERVOIR_AVG_USAGE_ALL":
        data["avg_usage_detail_block"] = _EXPAND_MARKER
        data["_detail_blocks"]["avg_usage_detail_block"] = build_avg_usage_detail_block(rows, columns)
        # total_avg_usage: avg_usage가 있는 행들의 평균
        usage_values = []
        no_data_sites = []
        unit = ""
        for row in rows:
            row_dict_tmp = dict(zip(columns, row))
            is_null = row_dict_tmp.get("is_avg_usage_null", "N")
            if is_null == "Y" or row_dict_tmp.get("avg_usage") is None:
                no_data_sites.append(row_dict_tmp.get("sitename", ""))
            else:
                usage_values.append(float(row_dict_tmp["avg_usage"]))
                if not unit:
                    unit = row_dict_tmp.get("usage_unit", "")
        if usage_values:
            data["total_avg_usage"] = round(sum(usage_values) / len(usage_values), 2)
        data["usage_unit"] = unit
        data["no_data_sitename_list"] = ", ".join(no_data_sites) if no_data_sites else None

    # -------------------------------------------------
    # 진행중 알람: 다중 행 결과를 카테고리 요약 + 상세 리스트로 조립
    # -------------------------------------------------
    if intent == "ONGOING_ALARM_STATUS":
        from collections import Counter
        cat_counts = Counter()
        detail_items = []
        for row in rows:
            rd = dict(zip(columns, row))
            site = rd.get("sitename", "")
            ftype = rd.get("facilitytype", "")
            msg = rd.get("alarm_msg", "")
            cat = rd.get("alarm_category", "")
            atime = rd.get("alarm_start_time", "")
            if cat:
                cat_counts[cat] += 1
            if msg:
                detail_items.append({
                    "prefix": "-",
                    "text": f"{site} {ftype}: {msg} ({atime})"
                })

        data["total_alarm_count"] = len(rows)
        if cat_counts:
            data["category_summary"] = ", ".join(
                f"{k} {v}건" for k, v in cat_counts.items()
            )
        else:
            data["category_summary"] = "없음"

        if detail_items:
            data["ongoing_alarm_detail_block"] = _EXPAND_MARKER
            data["_detail_blocks"]["ongoing_alarm_detail_block"] = detail_items

    # -------------------------------------------------
    # 통신 구성: 홉 상세 데이터 조립
    # -------------------------------------------------
    if intent == "FACILITY_COMMUNICATION_TOPOLOGY":
        data["network_hop_detail_block"] = _EXPAND_MARKER
        data["_detail_blocks"]["network_hop_detail_block"] = build_network_hop_detail_block(rows, columns)
        # 첫 번째 SQL 결과(type_path)가 _extra_ 접두사로 병합됨
        if "_extra_type_path" in data:
            data["type_path"] = data["_extra_type_path"]

    # -------------------------------------------------
    # 통신 상태: sitename 기준 그룹핑
    # -------------------------------------------------
    if intent == "FACILITY_COMMUNICATION_STATUS":
        data["network_status_block"] = _EXPAND_MARKER
        data["_detail_blocks"]["network_status_block"] = build_network_status_block(rows, columns)

    # -------------------------------------------------
    # 태그 최신값: 다중 행 최신값 리스트 조립
    # -------------------------------------------------
    if intent == "FACILITY_TAG_LATEST_VALUE":
        data["latest_value_list_block"] = _EXPAND_MARKER
        data["_detail_blocks"]["latest_value_list_block"] = build_latest_value_list_block(rows, columns)

    # -------------------------------------------------
    # 최근 알람: 다중 행 알람 목록 조립
    # -------------------------------------------------
    if intent == "FACILITY_RECENT_ALARM":
        data["alarm_list_block"] = _EXPAND_MARKER
        data["_detail_blocks"]["alarm_list_block"] = build_alarm_list_block(rows, columns)

    # -------------------------------------------------
    # 알람 누적건수 TOP: 다중 행 순위 조립
    # -------------------------------------------------
    if intent == "FACILITY_ALARM_TOP_COUNT":
        data["alarm_rank_block"] = _EXPAND_MARKER
        data["_detail_blocks"]["alarm_rank_block"] = build_alarm_rank_block(rows, columns)

    # -------------------------------------------------
    # 압력 현황: 복수 압력 포인트 결과 조립
    # -------------------------------------------------
    if intent == "FACILITY_PRESSURE_STATUS":
        data["pressure_detail_block"] = _EXPAND_MARKER
        data["_detail_blocks"]["pressure_detail_block"] = build_pressure_detail_block(rows, columns)
        data["pressure_reference_block"] = _EXPAND_MARKER
        data["_detail_blocks"]["pressure_reference_block"] = build_pressure_reference_block(rows, columns)

    # -------------------------------------------------
    # 수위계 헌팅 점검: 복수 계측 포인트 결과 조립
    # -------------------------------------------------
    if intent == "RESERVOIR_LEVEL_HUNTING_CHECK":
        data["hunting_result_block"] = _EXPAND_MARKER
        data["_detail_blocks"]["hunting_result_block"] = build_hunting_result_block(rows, columns)

    # -------------------------------------------------
    # 설비현황 (배수지 / 가압장 / 감압시설 공통)
    # meta JSONB → 테이블 데이터 변환
    # -------------------------------------------------
    if intent in [
        "RESERVOIR_EQUIPMENT_STATUS",
        "BOOSTER_STATION_EQUIPMENT_STATUS",
        "PRESSURE_REDUCING_FACILITY_EQUIPMENT_STATUS",
    ]:
        data["equipment_table"] = build_equipment_table(data.get("meta"))

    # -------------------------------------------------
    # 주소 (배수지 / 가압장 / 블록 / 감압시설 공통)
    # detail에 {location_image_block} → site_photo_url 기반 이미지 블록 삽입
    # -------------------------------------------------
    _UI_BLOCK_PREFIX = "__UI_BLOCK__"

    if intent in [
        "FACILITY_ADDRESS_INFO_RESERVOIR",
        "FACILITY_ADDRESS_INFO_BOOSTER",
        "FACILITY_ADDRESS_INFO_BLOCK",
        "FACILITY_ADDRESS_INFO_PRESSURE",
        "BLOCK_LOCATION",
        "RESERVOIR_LOCATION",
        "BOOSTER_STATION_LOCATION",
        "PRESSURE_REDUCING_FACILITY_LOCATION",
    ]:
        site_photo_url = data.get("site_photo_url")
        if site_photo_url:
            data["_ui_blocks"] = data.get("_ui_blocks", {})
            data["_ui_blocks"]["location_image_block"] = {
                "type": "image",
                "url": site_photo_url,
                "title": f"{params.get('sitename')} 현장 위치 사진"
            }
            data["location_image_block"] = f"{_UI_BLOCK_PREFIX}location_image_block"
            logger.info(f"site_photo_url={site_photo_url}")

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
            data["_ui_blocks"] = data.get("_ui_blocks", {})
            data["_ui_blocks"]["manual_block"] = {
                "type": "image",
                "url": manual_url,
                "title": f"{params.get('sitename')} 초동대응 매뉴얼"
            }
            data["manual_block"] = f"{_UI_BLOCK_PREFIX}manual_block"
            logger.info(f"manual_url={manual_url}")
    # -------------------------------------------------
    # 계통도 (배수지 / 가압장 / 블록 / 감압시설 공통)
    # detail에 {system_diagram_block} → system_diagram_url 기반 이미지 블록 삽입
    # -------------------------------------------------
    if intent in [
        "RESERVOIR_NETWORK_DIAGRAM",
        "BOOSTER_STATION_NETWORK_DIAGRAM",
        "BLOCK_NETWORK_DIAGRAM",
        "PRESSURE_REDUCING_FACILITY_NETWORK_DIAGRAM",
    ]:
        diagram_url = data.get("system_diagram_url")
        if diagram_url:
            data["_ui_blocks"] = data.get("_ui_blocks", {})
            data["_ui_blocks"]["system_diagram_block"] = {
                "type": "image",
                "url": diagram_url,
                "title": f"{params.get('sitename')} 계통도"
            }
            data["system_diagram_block"] = f"{_UI_BLOCK_PREFIX}system_diagram_block"
            logger.info(f"system_diagram_url={diagram_url}")

    return data


# =============================================================================
# 메인 엔드포인트
# docs/ai_server_task.md 참조
# =============================================================================

@app.post("/ask")
async def ask(request: AskRequest):
    """
    POST /ask 엔드포인트

    요청: { "user_question": "...", "session_id": "..." (선택) }
    응답: example3.json 및 ai_server_plan.md에서 정의된 구조를 따른다.

    신규 흐름:
    1. 세션 로드/생성
    2. 정정 턴 단축 체크
    3. SLM Intent 분류 (2단계) — 폴백: 기존 match_intent()
    4. 하이브리드 파라미터 추출
    5. 세션 파라미터 병합 (multi-turn)
    6. 질의 검증 → NEED_CORRECTION이면 정정 응답
    7. 기존 파이프라인: SQL 실행 → 템플릿 렌더링 → 응답
    """
    user_question = normalize_question(request.user_question)
    logger.info(f"질의 수신: {user_question}")

    # 1. 세션 로드/생성
    session = session_manager.get_or_create(request.session_id)
    sid = session.session_id

    # 최대 턴 수 체크
    if session_manager.is_max_turns(session):
        logger.warning(f"세션 최대 턴 초과: {sid}")
        return build_correction_response(
            message="대화 턴 수가 초과되었습니다. 새로운 질문을 시작해 주세요.",
            session_id=sid,
            correction_hints=["새 질문을 입력해 주세요."],
        )

    # 2. 정정 턴 단축 체크
    is_correction = session_manager.is_correction_turn(session, user_question)

    # 3. SLM Intent 분류
    if is_correction and session.last_intent:
        # 정정 턴: 이전 INTENT 재사용
        intent_name = session.last_intent
        intent_def = intent_index.get_definition(intent_name)
        category = "정정"
        classify_method = "correction_reuse"
        logger.info(f"정정 턴 단축: intent={intent_name}")
    else:
        # 신규 분류 (SLM 호출 가능 → 별도 스레드에서 실행)
        classification = await asyncio.to_thread(
            intent_classifier.classify,
            question=user_question,
            keyword_fallback_fn=match_intent,
        )
        intent_name = classification["intent_name"]
        intent_def = classification["intent_def"]
        category = classification["category"]
        classify_method = classification["method"]
        logger.info(
            f"Intent 분류: name={intent_name}, category={category}, method={classify_method}"
        )

    # 4. 하이브리드 파라미터 추출
    new_params = await asyncio.to_thread(
        param_extractor_instance.extract_all, user_question, intent_name
    )

    # 4.4. 다중 sitename 추출
    extra_sitenames = new_params.pop("_extra_sitenames", None)

    # 4.5. 월 단독 지정 시 년도 확인 요청
    month_only = new_params.pop("_month_only", None)
    if month_only is not None:
        month_name = f"{month_only}월"
        session_manager.update_session(
            session,
            intent_name=intent_name,
            params=new_params,
            status="NEED_CORRECTION",
            pending_corrections=["from_ts", "to_ts"],
        )
        return build_correction_response(
            message=f"'{month_name}' 조회 시 년도를 명시해 주세요. 예: '2025년 {month_name}'",
            session_id=sid,
            correction_hints=[
                f"2025년 {month_name}",
                f"2024년 {month_name}",
                f"2026년 {month_name}",
            ],
            intent=intent_name,
        )

    # 4.6. 테이블/시계열 인텐트에서 sitename 없고 facilitytype 있으면 전체 조회
    _TABLE_INTENTS_ALLOW_ALL = {
        "FACILITY_FLOW_ACCUMULATED_TIMESERIES_TABLE",
        "FACILITY_FLOW_INSTANT_TIMESERIES_TABLE",
        "FACILITY_ANALOG_TIMESERIES_TABLE",
        "FACILITY_DIGITAL_STATUS_TIMESERIES_TABLE",
        "FACILITY_FLOW_CURRENT_TABLE",
        "FACILITY_VALVE_STATUS_CURRENT_TABLE",
        "FACILITY_TAG_DATA_TABLE",
        "FACILITY_ALARM_TOP_COUNT",
        "NIGHT_MIN_FLOW_SUMMARY_TABLE",
        "FACILITY_ABNORMAL_STATUS_SUMMARY",
    }
    if (intent_name in _TABLE_INTENTS_ALLOW_ALL
            and not new_params.get("sitename")
            and new_params.get("facilitytype")):
        new_params["sitename"] = "%%"

    # 4.7. facilitytype 미추출 시 sitename에서 자동 해소
    if not new_params.get("facilitytype") and not new_params.get("block_level"):
        _site = new_params.get("sitename")
        if _site and _site != "%%" and SITENAME_FACILITY_MAP:
            _available_types = SITENAME_FACILITY_MAP.get(_site)
            if _available_types and len(_available_types) == 1:
                resolved_ft = next(iter(_available_types))
                new_params["facilitytype"] = resolved_ft
                if resolved_ft in ("소블록", "중블록", "대블록"):
                    new_params["block_level"] = resolved_ft
                logger.info(f"facilitytype 자동 해소: '{_site}' → '{resolved_ft}'")

    # 5. 세션 파라미터 병합
    params = session_manager.get_merged_params(session, new_params)

    # 6. 질의 검증
    validation = query_validator.validate(
        category=category,
        intent_name=intent_name,
        intent_def=intent_def,
        params=params,
    )

    if not validation.is_valid:
        # 세션 업데이트 (NEED_CORRECTION)
        session_manager.update_session(
            session,
            intent_name=intent_name,
            params=new_params,
            status="NEED_CORRECTION",
            pending_corrections=validation.missing_params,
        )
        logger.info(
            f"검증 실패: type={validation.error_type}, missing={validation.missing_params}"
        )
        return build_correction_response(
            message=validation.message,
            session_id=sid,
            correction_hints=validation.hints,
            intent=intent_name,
        )

    # 검증 통과 — 기존 파이프라인 진행
    intent = intent_name
    sql_template = intent_def.get("sql", "")
    answer_template = intent_def.get("answer_template", {})
    graph_type = intent_def.get("graph_type", "none")
    table_columns = intent_def.get("table_columns")
    table_type = intent_def.get("table_type")

    logger.info(f"INTENT 확정: {intent} (method={classify_method})")

    # 세션 업데이트 (OK)
    session_manager.update_session(
        session,
        intent_name=intent,
        params=new_params,
        status="OK",
    )

    # SQL 실행
    # sql_template은 string 또는 list (FACILITY_MIXED_TREND: 리스트를 join하여 단일 SQL로)
    if isinstance(sql_template, list):
        sql_combined = "\n".join(sql_template)
    else:
        sql_combined = sql_template or ""

    # 빈 SQL 체크
    if not sql_combined or not sql_combined.strip():
        rendered_answer = render_answer_template(answer_template, params)
        return build_success_response(
            intent=intent,
            answer=rendered_answer,
            graph_type=graph_type,
            session_id=sid,
        )

    # FACILITY_TREND + 야간최소유량: fn_night_min_flow_summary 사용
    if intent == "FACILITY_TREND" and "야간최소유량" in user_question:
        sql_combined = (
            "SELECT * FROM fn_night_min_flow_summary("
            "'{sitename}', '{facilitytype}', {from_ts}, {to_ts}"
            ") ORDER BY log_time ASC;"
        )

    # ONGOING_ALARM_STATUS: tb_equipment_alarm_report에서 alarm_status='진행중' 직접 조회
    if intent == "ONGOING_ALARM_STATUS":
        where_parts = ["alarm_status = '진행중'"]
        _site = params.get("sitename")
        _category = params.get("datainfo")
        if _site:
            _site_esc = _site.replace("'", "''")
            where_parts.append(f"sitename = '{_site_esc}'")
        if _category:
            _cat_esc = _category.replace("'", "''")
            where_parts.append(f"alarm_category = '{_cat_esc}'")
        where_clause = " AND ".join(where_parts)
        sql_combined = (
            f"SELECT sitename, facilitytype, alarm_msg, alarm_category,"
            f" TO_CHAR(alarm_start_time, 'YYYY-MM-DD HH24:MI:SS') AS alarm_start_time"
            f" FROM tb_equipment_alarm_report"
            f" WHERE {where_clause}"
            f" ORDER BY alarm_start_time DESC;"
        )

    # alarm_msg가 None이면 전체 알람 조회 (LIKE '%%')
    if params.get("alarm_msg") is None and "{alarm_msg}" in sql_combined:
        params["alarm_msg"] = ""

    # FACILITY_TAG_LATEST_VALUE / FACILITY_TAG_DATA_TABLE: datakey 기반 tagtype 필터 주입
    # 수위/압력/유량 → Analog Input, 밸브 → Digital Input, 설정 → Analog Output
    if intent in ("FACILITY_TAG_LATEST_VALUE", "FACILITY_TAG_DATA_TABLE"):
        _dk = params.get("datakey") or params.get("datainfo") or ""
        if "밸브" in _dk:
            _tagtype = "Digital Input"
        elif "설정" in _dk:
            _tagtype = "Analog Output"
        else:
            _tagtype = "Analog Input"
        if intent == "FACILITY_TAG_LATEST_VALUE":
            # GROUP BY 앞에 tagtype 조건 주입
            sql_combined = sql_combined.replace(
                "GROUP BY",
                f"  AND i.tagtype = '{_tagtype}'\nGROUP BY",
            )
        elif "AND i.tagtype = 'Analog Input'" in sql_combined:
            # FACILITY_TAG_DATA_TABLE: 하드코딩된 tagtype을 동적으로 교체
            sql_combined = sql_combined.replace(
                "AND i.tagtype = 'Analog Input'",
                f"AND i.tagtype = '{_tagtype}'",
            )

    # FACILITY_ABNORMAL_STATUS_SUMMARY: fn_realtime_missing_summary는 빈 문자열 = 전체
    if intent == "FACILITY_ABNORMAL_STATUS_SUMMARY":
        if params.get("sitename") in (None, "%%"):
            params["sitename"] = ""
        if params.get("facilitytype") in (None, "%%"):
            params["facilitytype"] = ""
        if params.get("datainfo") in (None, "%%"):
            params["datainfo"] = ""

    # sitename이 "%%"(전체)인 경우: SQL의 = 비교를 LIKE로 변경
    if params.get("sitename") == "%%":
        sql_combined = sql_combined.replace(
            "sitename = '{sitename}'", "sitename LIKE '{sitename}'"
        )
        # summary에서 "%%" 대신 "전체"로 표시
        params["sitename"] = "%%"

    # FACILITY_TAG_DATA_TABLE: from_ts == to_ts(금일 등)이면 to_ts를 다음날로 보정
    if intent in ("FACILITY_TAG_DATA_TABLE", "FACILITY_ANALOG_TIMESERIES_TABLE",
                   "FACILITY_DIGITAL_STATUS_TIMESERIES_TABLE",
                   "FACILITY_FLOW_CURRENT_TABLE",
                   "FACILITY_FLOW_ACCUMULATED_TIMESERIES_TABLE",
                   "FACILITY_FLOW_INSTANT_TIMESERIES_TABLE",
                   "FACILITY_VALVE_STATUS_CURRENT_TABLE"):
        _ft = params.get("from_ts")
        _tt = params.get("to_ts")
        if _ft and _tt and len(_tt) == 10 and _ft == _tt:
            # 날짜만 있고 같은 날인 경우 to_ts를 다음날로 (< 비교이므로)
            try:
                to_date = datetime.strptime(_tt, "%Y-%m-%d")
                params["to_ts"] = (to_date + timedelta(days=1)).strftime("%Y-%m-%d")
            except ValueError:
                pass

    try:
        rows, columns = await asyncio.to_thread(execute_sql, sql_combined, params)
    except psycopg2.OperationalError as e:
        logger.error(f"DB 접속 오류: {e}")
        return build_error_response(
            message="데이터베이스 연결 오류가 발생했습니다.",
            session_id=sid,
        )
    except psycopg2.Error as e:
        logger.error(f"SQL 실행 오류: {e}")
        return build_error_response(
            message="데이터베이스 연결 오류가 발생했습니다.",
            session_id=sid,
        )

    # 다중 sitename: 추가 sitename에 대해 SQL 실행 후 결과 병합
    if extra_sitenames:
        all_rows = list(rows) if rows else []
        for extra_site in extra_sitenames:
            extra_p = dict(params)
            extra_p["sitename"] = extra_site
            if SITENAME_FACILITY_MAP:
                ft_set = SITENAME_FACILITY_MAP.get(extra_site)
                if ft_set and len(ft_set) == 1:
                    resolved = next(iter(ft_set))
                    extra_p["facilitytype"] = resolved
                    if resolved in ("소블록", "중블록", "대블록"):
                        extra_p["block_level"] = resolved
            try:
                extra_rows, _ = await asyncio.to_thread(execute_sql, sql_combined, extra_p)
                if extra_rows:
                    all_rows.extend(extra_rows)
                    logger.info(f"추가 sitename '{extra_site}': {len(extra_rows)}행 병합")
            except Exception as e:
                logger.warning(f"추가 sitename '{extra_site}' SQL 실행 실패: {e}")
        rows = all_rows
        # 렌더링용 sitename을 모든 현장명으로 업데이트
        all_site_names = [params.get("sitename", "")] + list(extra_sitenames)
        params["sitename"] = ", ".join(all_site_names)

    # 결과 확인
    if not rows:
        logger.info(f"조회 결과 없음: {intent}, params={params}")
        return build_no_data_response(intent, answer_template, params=params, session_id=sid)

    # FACILITY_ABNORMAL_STATUS_SUMMARY: SQL 실행 후 빈 문자열을 렌더링용 "전체"로 변환
    if intent == "FACILITY_ABNORMAL_STATUS_SUMMARY":
        if not params.get("datainfo"):
            params["datainfo"] = "전체"

    # 데이터 후처리
    try:
        processed_data = process_sql_result(rows, columns, intent_def, params)
    except JsonbSchemaViolation as e:
        logger.error(f"JSONB 스키마 위반: {e.message}, path: {e.path}")
        return build_error_response(
            message="데이터 구조 오류가 발생했습니다.",
            session_id=sid,
        )

    # 트렌드 인텐트: 템플릿 변수 보충
    if intent in ("FACILITY_TREND", "FACILITY_MIXED_TREND"):
        _ft = params.get("from_ts", "")
        _tt = params.get("to_ts", "")
        if _ft and _tt:
            processed_data["period_desc"] = f"{_ft} ~ {_tt}"
        if intent == "FACILITY_MIXED_TREND":
            processed_data["digital_label"] = params.get("digital_datainfo") or "밸브"
            processed_data["analog_label"] = params.get("analog_datainfo") or "유량"

    # answer_template 렌더링
    rendered_answer = render_answer_template(answer_template, processed_data)

    # detail/reference에서 __EXPAND__ 마커 expand
    detail_blocks = processed_data.get("_detail_blocks", {})

    def _expand_section(section_items: list) -> list:
        """섹션 내 __EXPAND__ 마커를 _detail_blocks의 해당 리스트로 치환한다."""
        expanded = []
        for item in section_items:
            if not isinstance(item, dict):
                expanded.append(item)
                continue
            text = item.get("text", "")
            if text == "__EXPAND__":
                # prefix가 비어있는 __EXPAND__는 순서대로 매칭
                matched = False
                for bk, bv in detail_blocks.items():
                    if isinstance(bv, list) and bv:
                        expanded.extend(bv)
                        detail_blocks[bk] = []  # 사용한 블록은 비움
                        matched = True
                        break
                if not matched:
                    expanded.append(item)
            else:
                expanded.append(item)
        return expanded

    if detail_blocks and "detail" in rendered_answer:
        rendered_answer["detail"] = _expand_section(rendered_answer["detail"])

    # expand 후 소비되지 않은 detail_block이 있으면 detail에 추가
    for bk, bv in detail_blocks.items():
        if isinstance(bv, list) and bv:
            if "detail" not in rendered_answer:
                rendered_answer["detail"] = []
            rendered_answer["detail"].extend(bv)
            detail_blocks[bk] = []

    if detail_blocks and "reference" in rendered_answer and "items" in rendered_answer["reference"]:
        rendered_answer["reference"]["items"] = _expand_section(rendered_answer["reference"]["items"])

    # detail에 UI 블록 삽입
    ui_blocks = processed_data.get("_ui_blocks", {})
    _UI_BLOCK_PREFIX = "__UI_BLOCK__"

    if ui_blocks and "detail" in rendered_answer:
        replaced_detail = []
        for item in rendered_answer["detail"]:
            replaced = False
            if isinstance(item, dict):
                text = item.get("text", "")
                if isinstance(text, str) and text.startswith(_UI_BLOCK_PREFIX):
                    block_key = text[len(_UI_BLOCK_PREFIX):]
                    if block_key in ui_blocks:
                        replaced_detail.append(ui_blocks[block_key])
                        replaced = True
            if not replaced:
                replaced_detail.append(item)
        rendered_answer["detail"] = replaced_detail

    # 응답 생성
    response_data = None
    csv_url = None
    total_rows = None
    data_truncated = False

    if table_type == "equipment" and "equipment_table" in processed_data:
        response_data = processed_data["equipment_table"]
    elif table_type == "summary" and rows and columns:
        # summary 테이블: CSV 저장 + 행 제한
        csv_filename = save_csv(rows, columns, intent, sid)
        csv_url = f"/csv/{csv_filename}"
        total_rows = len(rows)

        if len(rows) > MAX_TABLE_ROWS:
            sampled = stratified_sample(rows, columns, MAX_TABLE_ROWS)
            response_data = [dict(zip(columns, row)) for row in sampled]
            data_truncated = True
        else:
            response_data = [dict(zip(columns, row)) for row in rows]
    elif graph_type and graph_type != "none" and rows and columns:
        # 그래프용 데이터 (plot, bar 등): 행 수 제한 + 다운샘플링
        total_rows = len(rows)
        if len(rows) > MAX_GRAPH_ROWS:
            sampled = downsample_rows(rows, MAX_GRAPH_ROWS)
            response_data = [dict(zip(columns, row)) for row in sampled]
            data_truncated = True
            logger.info(f"그래프 데이터 다운샘플링: {total_rows}행 → {len(sampled)}행")
        else:
            response_data = [dict(zip(columns, row)) for row in rows]

    # 트렌드 차트: chart_data_type (analog/digital/mixed) + plot_type
    _chart_data_type = None
    _plot_type = intent_def.get("plot_type") if intent_def else None
    if graph_type == "plot" and rows and columns:
        _chart_data_type = classify_chart_data_type(rows, columns)
        # plot_type 기본값: intent_def에 없으면 chart_data_type 기반 설정
        if not _plot_type and _chart_data_type:
            _PLOT_TYPE_DEFAULTS = {"analog": "line", "digital": "step", "mixed": "multi_axis_line"}
            _plot_type = _PLOT_TYPE_DEFAULTS.get(_chart_data_type, "line")

    return build_success_response(
        intent=intent,
        answer=rendered_answer,
        graph_type=graph_type,
        data=response_data,
        table_columns=table_columns,
        table_type=table_type,
        session_id=sid,
        csv_url=csv_url,
        total_rows=total_rows,
        data_truncated=data_truncated,
        chart_data_type=_chart_data_type,
        plot_type=_plot_type,
    )



# =============================================================================
# SSE 스트리밍 엔드포인트
# =============================================================================

def _sse_event(event: str, data: dict) -> str:
    """SSE 이벤트 문자열을 생성한다."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/ask/stream")
async def ask_stream(request: AskRequest):
    """
    POST /ask/stream 엔드포인트 (Server-Sent Events)

    /ask와 동일한 처리를 수행하되, 각 단계별 진행 상황을 SSE로 스트리밍한다.

    SSE 이벤트 유형:
    - event: progress  → 진행 상황 메시지 (UI에 표시)
    - event: result    → 최종 응답 (기존 /ask 응답과 동일 구조)
    - event: error     → 오류 발생

    요청: { "user_question": "...", "session_id": "..." (선택) }
    """
    async def event_generator():
        user_question = normalize_question(request.user_question)
        logger.info(f"[SSE] 질의 수신: {user_question}")

        # --- 진행 1: 질의 분석 ---
        yield _sse_event("progress", {
            "step": "classify",
            "message": "질의를 분석 중입니다...",
        })
        await asyncio.sleep(0)  # yield 즉시 전송

        # 1. 세션 로드/생성
        session = session_manager.get_or_create(request.session_id)
        sid = session.session_id

        # 최대 턴 수 체크
        if session_manager.is_max_turns(session):
            logger.warning(f"[SSE] 세션 최대 턴 초과: {sid}")
            yield _sse_event("result", build_correction_response(
                message="대화 턴 수가 초과되었습니다. 새로운 질문을 시작해 주세요.",
                session_id=sid,
                correction_hints=["새 질문을 입력해 주세요."],
            ))
            return

        # 2. 정정 턴 단축 체크
        is_correction = session_manager.is_correction_turn(session, user_question)

        # 3. SLM Intent 분류
        if is_correction and session.last_intent:
            intent_name = session.last_intent
            intent_def = intent_index.get_definition(intent_name)
            category = "정정"
            classify_method = "correction_reuse"
            logger.info(f"[SSE] 정정 턴 단축: intent={intent_name}")
        else:
            # 신규 분류 (SLM 호출 가능 → 별도 스레드에서 실행)
            classification = await asyncio.to_thread(
                intent_classifier.classify,
                question=user_question,
                keyword_fallback_fn=match_intent,
            )
            intent_name = classification["intent_name"]
            intent_def = classification["intent_def"]
            category = classification["category"]
            classify_method = classification["method"]
            logger.info(
                f"[SSE] Intent 분류: name={intent_name}, category={category}, method={classify_method}"
            )

        # --- 진행 2: 파라미터 추출 ---
        yield _sse_event("progress", {
            "step": "extract",
            "message": "파라미터를 추출 중입니다...",
            "intent": intent_name,
        })
        await asyncio.sleep(0)

        # 4. 하이브리드 파라미터 추출
        new_params = await asyncio.to_thread(
            param_extractor_instance.extract_all, user_question, intent_name
        )

        # 4.4. 다중 sitename 추출
        extra_sitenames = new_params.pop("_extra_sitenames", None)

        # 4.5. 월 단독 지정 시 년도 확인 요청
        month_only = new_params.pop("_month_only", None)
        if month_only is not None:
            month_name = f"{month_only}월"
            session_manager.update_session(
                session,
                intent_name=intent_name,
                params=new_params,
                status="NEED_CORRECTION",
                pending_corrections=["from_ts", "to_ts"],
            )
            yield _sse_event("result", build_correction_response(
                message=f"'{month_name}' 조회 시 년도를 명시해 주세요. 예: '2025년 {month_name}'",
                session_id=sid,
                correction_hints=[
                    f"2025년 {month_name}",
                    f"2024년 {month_name}",
                    f"2026년 {month_name}",
                ],
                intent=intent_name,
            ))
            return

        # 4.6. 테이블/시계열 인텐트에서 sitename 없고 facilitytype 있으면 전체 조회
        if (intent_name in _TABLE_INTENTS_ALLOW_ALL
                and not new_params.get("sitename")
                and new_params.get("facilitytype")):
            new_params["sitename"] = "%%"

        # 4.7. facilitytype 미추출 시 sitename에서 자동 해소
        if not new_params.get("facilitytype") and not new_params.get("block_level"):
            _site = new_params.get("sitename")
            if _site and _site != "%%" and SITENAME_FACILITY_MAP:
                _available_types = SITENAME_FACILITY_MAP.get(_site)
                if _available_types and len(_available_types) == 1:
                    resolved_ft = next(iter(_available_types))
                    new_params["facilitytype"] = resolved_ft
                    if resolved_ft in ("소블록", "중블록", "대블록"):
                        new_params["block_level"] = resolved_ft
                    logger.info(f"facilitytype 자동 해소: '{_site}' → '{resolved_ft}'")

        # 5. 세션 파라미터 병합
        params = session_manager.get_merged_params(session, new_params)

        # 6. 질의 검증
        validation = query_validator.validate(
            category=category,
            intent_name=intent_name,
            intent_def=intent_def,
            params=params,
        )

        if not validation.is_valid:
            session_manager.update_session(
                session,
                intent_name=intent_name,
                params=new_params,
                status="NEED_CORRECTION",
                pending_corrections=validation.missing_params,
            )
            logger.info(
                f"[SSE] 검증 실패: type={validation.error_type}, missing={validation.missing_params}"
            )
            yield _sse_event("result", build_correction_response(
                message=validation.message,
                session_id=sid,
                correction_hints=validation.hints,
                intent=intent_name,
            ))
            return

        # 검증 통과
        intent = intent_name
        sql_template = intent_def.get("sql", "")
        answer_template = intent_def.get("answer_template", {})
        graph_type = intent_def.get("graph_type", "none")
        table_columns = intent_def.get("table_columns")
        table_type = intent_def.get("table_type")

        logger.info(f"[SSE] INTENT 확정: {intent} (method={classify_method})")

        session_manager.update_session(
            session,
            intent_name=intent,
            params=new_params,
            status="OK",
        )

        # SQL 준비
        if isinstance(sql_template, list):
            sql_combined = "\n".join(sql_template)
        else:
            sql_combined = sql_template or ""

        # 빈 SQL 체크
        if not sql_combined or not sql_combined.strip():
            rendered_answer = render_answer_template(answer_template, params)
            yield _sse_event("result", build_success_response(
                intent=intent,
                answer=rendered_answer,
                graph_type=graph_type,
                session_id=sid,
            ))
            return

        # FACILITY_TREND + 야간최소유량: fn_night_min_flow_summary 사용
        if intent == "FACILITY_TREND" and "야간최소유량" in user_question:
            sql_combined = (
                "SELECT * FROM fn_night_min_flow_summary("
                "'{sitename}', '{facilitytype}', {from_ts}, {to_ts}"
                ") ORDER BY log_time ASC;"
            )

        # ONGOING_ALARM_STATUS: tb_equipment_alarm_report에서 alarm_status='진행중' 직접 조회
        if intent == "ONGOING_ALARM_STATUS":
            where_parts = ["alarm_status = '진행중'"]
            _site = params.get("sitename")
            _category = params.get("datainfo")
            if _site:
                _site_esc = _site.replace("'", "''")
                where_parts.append(f"sitename = '{_site_esc}'")
            if _category:
                _cat_esc = _category.replace("'", "''")
                where_parts.append(f"alarm_category = '{_cat_esc}'")
            where_clause = " AND ".join(where_parts)
            sql_combined = (
                f"SELECT sitename, facilitytype, alarm_msg, alarm_category,"
                f" TO_CHAR(alarm_start_time, 'YYYY-MM-DD HH24:MI:SS') AS alarm_start_time"
                f" FROM tb_equipment_alarm_report"
                f" WHERE {where_clause}"
                f" ORDER BY alarm_start_time DESC;"
            )

        # alarm_msg 기본값
        if params.get("alarm_msg") is None and "{alarm_msg}" in sql_combined:
            params["alarm_msg"] = ""

        # tagtype 필터 주입
        if intent in ("FACILITY_TAG_LATEST_VALUE", "FACILITY_TAG_DATA_TABLE"):
            _dk = params.get("datakey") or params.get("datainfo") or ""
            if "밸브" in _dk:
                _tagtype = "Digital Input"
            elif "설정" in _dk:
                _tagtype = "Analog Output"
            else:
                _tagtype = "Analog Input"
            if intent == "FACILITY_TAG_LATEST_VALUE":
                sql_combined = sql_combined.replace(
                    "GROUP BY",
                    f"  AND i.tagtype = '{_tagtype}'\nGROUP BY",
                )
            elif "AND i.tagtype = 'Analog Input'" in sql_combined:
                sql_combined = sql_combined.replace(
                    "AND i.tagtype = 'Analog Input'",
                    f"AND i.tagtype = '{_tagtype}'",
                )

        # FACILITY_ABNORMAL_STATUS_SUMMARY: fn_realtime_missing_summary는 빈 문자열 = 전체
        if intent == "FACILITY_ABNORMAL_STATUS_SUMMARY":
            if params.get("sitename") in (None, "%%"):
                params["sitename"] = ""
            if params.get("facilitytype") in (None, "%%"):
                params["facilitytype"] = ""
            if params.get("datainfo") in (None, "%%"):
                params["datainfo"] = ""

        # 전체 조회 LIKE 변환
        if params.get("sitename") == "%%":
            sql_combined = sql_combined.replace(
                "sitename = '{sitename}'", "sitename LIKE '{sitename}'"
            )
            params["sitename"] = "%%"

        # from_ts == to_ts 보정
        if intent in ("FACILITY_TAG_DATA_TABLE", "FACILITY_ANALOG_TIMESERIES_TABLE",
                       "FACILITY_DIGITAL_STATUS_TIMESERIES_TABLE"):
            _ft = params.get("from_ts")
            _tt = params.get("to_ts")
            if _ft and _tt and len(_tt) == 10 and _ft == _tt:
                try:
                    to_date = datetime.strptime(_tt, "%Y-%m-%d")
                    params["to_ts"] = (to_date + timedelta(days=1)).strftime("%Y-%m-%d")
                except ValueError:
                    pass

        # --- 진행 3: 데이터 조회 ---
        yield _sse_event("progress", {
            "step": "query",
            "message": "데이터를 조회 중입니다...",
        })
        await asyncio.sleep(0)

        try:
            rows, columns = await asyncio.to_thread(execute_sql, sql_combined, params)
        except psycopg2.OperationalError as e:
            logger.error(f"[SSE] DB 접속 오류: {e}")
            yield _sse_event("error", {
                "status": "ERROR",
                "message": "DB 장애 발생으로 점검이 필요합니다.",
                "session_id": sid,
            })
            return
        except psycopg2.Error as e:
            logger.error(f"[SSE] SQL 실행 오류: {e}")
            yield _sse_event("error", {
                "status": "ERROR",
                "message": "데이터베이스 연결 오류가 발생했습니다.",
                "session_id": sid,
            })
            return

        # 다중 sitename: 추가 sitename에 대해 SQL 실행 후 결과 병합
        if extra_sitenames:
            all_rows = list(rows) if rows else []
            for extra_site in extra_sitenames:
                extra_p = dict(params)
                extra_p["sitename"] = extra_site
                if SITENAME_FACILITY_MAP:
                    ft_set = SITENAME_FACILITY_MAP.get(extra_site)
                    if ft_set and len(ft_set) == 1:
                        resolved = next(iter(ft_set))
                        extra_p["facilitytype"] = resolved
                        if resolved in ("소블록", "중블록", "대블록"):
                            extra_p["block_level"] = resolved
                try:
                    extra_rows, _ = await asyncio.to_thread(execute_sql, sql_combined, extra_p)
                    if extra_rows:
                        all_rows.extend(extra_rows)
                        logger.info(f"[SSE] 추가 sitename '{extra_site}': {len(extra_rows)}행 병합")
                except Exception as e:
                    logger.warning(f"[SSE] 추가 sitename '{extra_site}' SQL 실행 실패: {e}")
            rows = all_rows
            # 렌더링용 sitename을 모든 현장명으로 업데이트
            all_site_names = [params.get("sitename", "")] + list(extra_sitenames)
            params["sitename"] = ", ".join(all_site_names)

        # 결과 확인
        if not rows:
            logger.info(f"[SSE] 조회 결과 없음: {intent}, params={params}")
            yield _sse_event("result", build_no_data_response(
                intent, answer_template, params=params, session_id=sid,
            ))
            return

        # --- 진행 4: 결과 처리 ---
        yield _sse_event("progress", {
            "step": "render",
            "message": "응답을 생성 중입니다...",
        })
        await asyncio.sleep(0)

        # FACILITY_ABNORMAL_STATUS_SUMMARY: SQL 실행 후 빈 문자열을 렌더링용 "전체"로 변환
        if intent == "FACILITY_ABNORMAL_STATUS_SUMMARY":
            if not params.get("datainfo"):
                params["datainfo"] = "전체"

        # 데이터 후처리
        try:
            processed_data = process_sql_result(rows, columns, intent_def, params)
        except JsonbSchemaViolation as e:
            logger.error(f"[SSE] JSONB 스키마 위반: {e.message}, path: {e.path}")
            yield _sse_event("error", {
                "status": "ERROR",
                "message": "데이터 구조 오류가 발생했습니다.",
                "session_id": sid,
            })
            return

        # 트렌드 인텐트: 템플릿 변수 보충
        if intent in ("FACILITY_TREND", "FACILITY_MIXED_TREND"):
            _ft = params.get("from_ts", "")
            _tt = params.get("to_ts", "")
            if _ft and _tt:
                processed_data["period_desc"] = f"{_ft} ~ {_tt}"
            if intent == "FACILITY_MIXED_TREND":
                processed_data["digital_label"] = params.get("digital_datainfo") or "밸브"
                processed_data["analog_label"] = params.get("analog_datainfo") or "유량"

        # answer_template 렌더링
        rendered_answer = render_answer_template(answer_template, processed_data)

        # __EXPAND__ 마커 처리
        detail_blocks = processed_data.get("_detail_blocks", {})

        def _expand_section(section_items: list) -> list:
            expanded = []
            for item in section_items:
                if not isinstance(item, dict):
                    expanded.append(item)
                    continue
                text = item.get("text", "")
                if text == "__EXPAND__":
                    matched = False
                    for bk, bv in detail_blocks.items():
                        if isinstance(bv, list) and bv:
                            expanded.extend(bv)
                            detail_blocks[bk] = []
                            matched = True
                            break
                    if not matched:
                        expanded.append(item)
                else:
                    expanded.append(item)
            return expanded

        if detail_blocks and "detail" in rendered_answer:
            rendered_answer["detail"] = _expand_section(rendered_answer["detail"])

        if detail_blocks and "reference" in rendered_answer and "items" in rendered_answer["reference"]:
            rendered_answer["reference"]["items"] = _expand_section(rendered_answer["reference"]["items"])

        # UI 블록 삽입
        ui_blocks = processed_data.get("_ui_blocks", {})
        _UI_BLOCK_PREFIX = "__UI_BLOCK__"

        if ui_blocks and "detail" in rendered_answer:
            replaced_detail = []
            for item in rendered_answer["detail"]:
                replaced = False
                if isinstance(item, dict):
                    text = item.get("text", "")
                    if isinstance(text, str) and text.startswith(_UI_BLOCK_PREFIX):
                        block_key = text[len(_UI_BLOCK_PREFIX):]
                        if block_key in ui_blocks:
                            replaced_detail.append(ui_blocks[block_key])
                            replaced = True
                if not replaced:
                    replaced_detail.append(item)
            rendered_answer["detail"] = replaced_detail

        # 응답 생성
        response_data = None
        csv_url = None
        total_rows = None
        data_truncated = False

        if table_type == "equipment" and "equipment_table" in processed_data:
            response_data = processed_data["equipment_table"]
        elif table_type == "summary" and rows and columns:
            csv_filename = save_csv(rows, columns, intent, sid)
            csv_url = f"/csv/{csv_filename}"
            total_rows = len(rows)

            if len(rows) > MAX_TABLE_ROWS:
                response_data = [dict(zip(columns, row)) for row in rows[:MAX_TABLE_ROWS]]
                data_truncated = True
            else:
                response_data = [dict(zip(columns, row)) for row in rows]
        elif graph_type and graph_type != "none" and rows and columns:
            # 그래프용 데이터 (plot, bar 등): 행 수 제한 + 다운샘플링
            total_rows = len(rows)
            if len(rows) > MAX_GRAPH_ROWS:
                sampled = downsample_rows(rows, MAX_GRAPH_ROWS)
                response_data = [dict(zip(columns, row)) for row in sampled]
                data_truncated = True
                logger.info(f"그래프 데이터 다운샘플링: {total_rows}행 → {len(sampled)}행")
            else:
                response_data = [dict(zip(columns, row)) for row in rows]

        # 트렌드 차트: chart_data_type (analog/digital/mixed) + plot_type
        _chart_data_type = None
        _plot_type = intent_def.get("plot_type") if intent_def else None
        if graph_type == "plot" and rows and columns:
            _chart_data_type = classify_chart_data_type(rows, columns)
            if not _plot_type and _chart_data_type:
                _PLOT_TYPE_DEFAULTS = {"analog": "line", "digital": "step", "mixed": "multi_axis_line"}
                _plot_type = _PLOT_TYPE_DEFAULTS.get(_chart_data_type, "line")

        final_response = build_success_response(
            intent=intent,
            answer=rendered_answer,
            graph_type=graph_type,
            data=response_data,
            table_columns=table_columns,
            table_type=table_type,
            session_id=sid,
            csv_url=csv_url,
            total_rows=total_rows,
            data_truncated=data_truncated,
            chart_data_type=_chart_data_type,
            plot_type=_plot_type,
        )

        yield _sse_event("result", final_response)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# =============================================================================
# CSV 다운로드 엔드포인트
# =============================================================================

@app.get("/csv/{filename}")
async def download_csv(filename: str):
    """CSV 파일 다운로드 엔드포인트"""
    # 경로 조작 방지
    if "/" in filename or "\\" in filename or ".." in filename:
        return {"status": "ERROR", "message": "잘못된 파일명입니다."}

    filepath = os.path.join(CSV_EXPORT_DIR, filename)
    if not os.path.exists(filepath):
        return {"status": "ERROR", "message": "파일을 찾을 수 없습니다."}

    return FileResponse(
        path=filepath,
        media_type="text/csv; charset=utf-8-sig",
        filename=filename,
    )


# =============================================================================
# 헬스 체크 엔드포인트
# =============================================================================

@app.get("/health")
async def health_check():
    """서버 상태 확인용 엔드포인트"""
    ollama_ok = ollama_client.health_check()
    return {
        "status": "ok",
        "ollama_available": ollama_ok,
        "current_model": get_model(),
        "active_sessions": session_manager.active_session_count(),
    }


# =============================================================================
# 모델 관리 엔드포인트
# =============================================================================

@app.get("/models")
async def list_models():
    """Ollama에 설치된 모델 목록을 반환한다."""
    models = ollama_client.list_models()
    current = get_model()
    return {
        "current_model": current,
        "available_models": models,
    }


class ModelSelectRequest(BaseModel):
    model_name: str


@app.post("/models/select")
async def select_model(request: ModelSelectRequest):
    """Ollama 모델을 런타임에 변경한다."""
    model_name = request.model_name

    # 설치된 모델 목록에서 확인
    models = ollama_client.list_models()
    installed_names = [m["name"] for m in models]

    if models and model_name not in installed_names:
        return {
            "status": "ERROR",
            "message": f"'{model_name}'은(는) 설치되지 않은 모델입니다.",
            "available_models": installed_names,
        }

    set_model(model_name)
    logger.info(f"모델 변경: {model_name}")
    return {
        "status": "OK",
        "current_model": model_name,
    }


# =============================================================================
# 메인 실행
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
