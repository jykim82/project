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
from fastapi import FastAPI, Request, UploadFile, File, Form, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from slm_config import ENABLE_KEYWORD_FALLBACK, get_model, set_model
from ollama_client import OllamaClient, OllamaConnectionError
from intent_index import IntentIndex
from intent_classifier import IntentClassifier
from intent_embeddings import IntentEmbeddingIndex
from param_extractor import ParamExtractor
from query_validator import QueryValidator, CORRECTION_TEMPLATES, CORRECTION_HINTS
from session_manager import SessionManager
from anomaly_detector import (
    count_anomaly_levels,
    count_alarm_severity,
    count_comm_error_sites,
    build_anomaly_scan_detail_block,
    build_anomaly_facility_detail_block,
    build_anomaly_history_detail_block,
    build_anomaly_predict_detail_block,
    build_anomaly_compare_detail_block,
    build_anomaly_pattern_detail_block,
    classify_z_level_by_group,
    analyze_level_pattern,
    get_hh_ll_for_site,
    compute_cusum_for_tags,
    count_cusum_status,
    build_cusum_summary_table,
    build_leak_cusum_detail_block,
)
from anomaly_iforest import IForestManager
from site_profiler import SiteProfiler

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
embedding_index = IntentEmbeddingIndex()
session_manager = SessionManager()
iforest_manager = IForestManager()
site_profiler: Optional[SiteProfiler] = None  # startup에서 초기화 (get_db_connection 정의 이후)
# intent_classifier, param_extractor, query_validator는 startup에서 초기화
intent_classifier: Optional[IntentClassifier] = None
param_extractor_instance: Optional[ParamExtractor] = None
query_validator: Optional[QueryValidator] = None

_cleanup_task: Optional[asyncio.Task] = None
_profiling_task: Optional[asyncio.Task] = None

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


async def _site_profiling_loop():
    """백그라운드: 서버 시작 60초 후 첫 실행, 이후 24시간마다 현장 프로파일링"""
    await asyncio.sleep(60)
    while True:
        try:
            logger.info("현장 프로파일링 시작...")
            await asyncio.to_thread(site_profiler.run_daily_profiling)
            profile_count = len(site_profiler.profiles)
            logger.info(f"현장 프로파일링 완료: {profile_count}개 현장 분류")
        except Exception as e:
            logger.error(f"현장 프로파일링 실패: {e}")
        await asyncio.sleep(86400)


async def _session_cleanup_loop():
    """백그라운드: 60초마다 만료 세션 정리 + CSV 파일 정리"""
    while True:
        await asyncio.sleep(60)
        session_manager.cleanup_expired()
        cleanup_old_csv_files()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작/종료 시 실행되는 lifespan 이벤트"""
    global intent_classifier, param_extractor_instance, query_validator, _cleanup_task, _profiling_task, site_profiler

    # site_profiler 초기화 (get_db_connection 정의 후)
    site_profiler = SiteProfiler(get_db_connection)

    # CSV 내보내기 디렉토리 생성
    os.makedirs(CSV_EXPORT_DIR, exist_ok=True)

    # Intent 인덱스 빌드
    intent_index.build(INTENT_DEFINITIONS)

    # 임베딩 인덱스 로드/빌드 (인메모리 벡터 검색용)
    example3_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "example3.json")
    try:
        embedding_index.load_or_build(example3_path)
    except Exception as e:
        logger.warning(f"임베딩 인덱스 초기화 실패 (키워드+SLM 폴백 모드): {e}")

    # 분류기, 추출기, 검증기 초기화
    intent_classifier = IntentClassifier(
        ollama_client, intent_index,
        embedding_index=embedding_index if embedding_index.ready else None,
    )
    param_extractor_instance = ParamExtractor(
        known_sitenames=KNOWN_SITENAMES,
        known_block_levels=KNOWN_BLOCK_LEVELS,
        ollama=ollama_client,
    )
    query_validator = QueryValidator(intent_index, KNOWN_SITENAMES, SITENAME_FACILITY_MAP)

    # Ollama 연결 상태 로그
    if ollama_client.health_check():
        logger.info(f"Ollama 연결 성공: {get_model()}")
        if embedding_index.ready:
            logger.info(f"벡터 검색 활성화: {embedding_index.size}벡터")
    else:
        logger.warning("Ollama 연결 실패 — 키워드 매칭 폴백 모드로 동작")

    # 세션 정리 백그라운드 태스크
    _cleanup_task = asyncio.create_task(_session_cleanup_loop())

    # 현장 프로파일링 백그라운드 태스크 (기존 DB 프로파일 로드 후 시작)
    try:
        site_profiler.load_from_db()
        if site_profiler.profiles:
            logger.info(f"기존 현장 프로파일 로드: {len(site_profiler.profiles)}개")
    except Exception as e:
        logger.warning(f"기존 프로파일 로드 실패 (서버 시작 후 재생성): {e}")
    _profiling_task = asyncio.create_task(_site_profiling_loop())

    yield

    # shutdown
    for task in (_cleanup_task, _profiling_task):
        if task:
            task.cancel()
            try:
                await task
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
    force_intent: Optional[str] = None


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

    # 사전 치환: 이미 완성된 SQL 조각 (이스케이프 불필요)
    _RAW_SQL_PARAMS = {"anomaly_facility_filter", "anomaly_scope", "alarm_filter_clause"}
    sql = sql_template
    for raw_key in _RAW_SQL_PARAMS:
        placeholder = "{" + raw_key + "}"
        if placeholder in sql:
            sql = sql.replace(placeholder, str(params.get(raw_key, "")))

    # 템플릿 변수 치환
    # from_ts, to_ts는 SQL에서 따옴표 없이 사용되므로 여기서 감싸줘야 한다
    _QUOTE_PARAMS = {"from_ts", "to_ts"}
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
    "alarm_label",
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

    # 빈 placeholder 치환으로 생긴 이중 공백 정리
    result = re.sub(r" {2,}", " ", result).strip()
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

    return _dedup_units_in_answer(result)


# 중복 단위 제거 패턴: "2000년년" → "2000년", "1234명명" → "1234명"
_DEDUP_UNIT_RE = re.compile(r"(년|명|개소|대|km|m|톤|%|개|건|초|분|시간|일|월|원|세대|호|곳|회)(\1)+")

# 천 단위 콤마: 4자리 이상 정수에 콤마 삽입 (소수점 포함 숫자, 날짜/시간, 태그ID 제외)
_COMMA_NUM_RE = re.compile(r"(?<!\d[.\-:/])(?<!\.)(\d{1,3}(?:,\d{3})*|\d+)(\.\d+)?(?![.\-:/]\d)")


def _add_thousands_comma(text: str) -> str:
    """텍스트 내 4자리 이상 정수에 천 단위 콤마를 삽입한다.
    이미 콤마가 있거나, 소수점/날짜/시간 패턴은 건드리지 않는다."""
    def _fmt(m: re.Match) -> str:
        integer_part = m.group(1).replace(",", "")  # 이미 콤마 있으면 제거 후 재포맷
        decimal_part = m.group(2) or ""
        if len(integer_part) < 4:
            return integer_part + decimal_part
        return f"{int(integer_part):,}" + decimal_part

    # 날짜/시간 패턴(2026-02-21, 12:30:00 등)은 보호
    parts = re.split(r"(\d{4}[-/]\d{2}[-/]\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?|\d{1,2}:\d{2}(?::\d{2})?)", text)
    result = []
    for i, part in enumerate(parts):
        if i % 2 == 0:  # 날짜/시간이 아닌 구간만 처리
            result.append(_COMMA_NUM_RE.sub(_fmt, part))
        else:
            result.append(part)  # 날짜/시간은 그대로
    return "".join(result)


def _dedup_unit_text(text: str) -> str:
    """텍스트 내 중복 단위 접미사를 제거한다."""
    return _DEDUP_UNIT_RE.sub(r"\1", text)


def _postprocess_text(text: str) -> str:
    """렌더링된 텍스트에 후처리를 적용한다 (중복단위 제거 + 천단위 콤마)."""
    text = _dedup_unit_text(text)
    text = _add_thousands_comma(text)
    return text


def _dedup_units_in_answer(answer: dict) -> dict:
    """렌더링된 answer dict 내 모든 텍스트를 후처리한다."""
    if "summary" in answer:
        answer["summary"] = _postprocess_text(answer["summary"])

    if "detail" in answer:
        for i, line in enumerate(answer["detail"]):
            if isinstance(line, dict) and "text" in line:
                line["text"] = _postprocess_text(line["text"])
            elif isinstance(line, str):
                answer["detail"][i] = _postprocess_text(line)

    if "reference" in answer and "items" in answer["reference"]:
        for i, item in enumerate(answer["reference"]["items"]):
            if isinstance(item, dict) and "text" in item:
                item["text"] = _postprocess_text(item["text"])
            elif isinstance(item, str):
                answer["reference"]["items"][i] = _postprocess_text(item)

    return answer


def apply_corrections_to_answer(rendered_answer: dict, params: dict) -> dict:
    """파라미터 보정 이력이 있으면 답변 summary에 보정 안내를 추가한다."""
    corrections = params.get("_corrections")
    if not corrections:
        return rendered_answer

    notices = []
    for c in corrections:
        notices.append(f"'{c['original']}'→'{c['corrected']}'")
    notice_text = "* 입력 보정: " + ", ".join(notices)

    if "summary" in rendered_answer:
        rendered_answer["summary"] = notice_text + "\n" + rendered_answer["summary"]
    else:
        rendered_answer["summary"] = notice_text

    return rendered_answer


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


# ── ANOMALY 인텐트 시설 필터 생성 ────────────────────────────
_ANOMALY_FILTER_INTENTS = {
    "ANOMALY_SCAN_ALL": "ti",
    "ANOMALY_PREDICT": "ti",
    "ANOMALY_PATTERN": "ti",
    "ANOMALY_HISTORY": "ar",
}


def build_anomaly_facility_filter(intent_name: str, params: dict) -> str:
    """ANOMALY 인텐트에 대해 선택적 sitename/facilitytype WHERE 절 생성."""
    alias = _ANOMALY_FILTER_INTENTS.get(intent_name)
    if not alias:
        return ""
    parts = []
    site = params.get("sitename", "")
    ftype = params.get("facilitytype", "")
    if site and site not in ("전체", "%%", ""):
        parts.append(f"AND {alias}.sitename = '{site}'")
    if ftype and ftype not in ("전체", "%%", ""):
        parts.append(f"AND {alias}.facilitytype = '{ftype}'")
    return "\n    ".join(parts)


def build_anomaly_scope_label(params: dict) -> str:
    """ANOMALY 인텐트 답변 summary에 쓸 범위 표시 문자열 생성."""
    site = params.get("sitename", "")
    ftype = params.get("facilitytype", "")
    if site and site not in ("전체", "%%", ""):
        if ftype:
            return f"{site} {ftype}"
        return site
    if ftype:
        return ftype
    return "전체"


def build_correction_response(
    message: str,
    session_id: str,
    correction_hints: Optional[list] = None,
    intent: Optional[str] = None,
    intent_candidates: Optional[list] = None,
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
    if intent_candidates:
        response["intent_candidates"] = intent_candidates
    return response


def compute_anomaly_zones(
    rows: list,
    columns: list,
    region: str,
    conn,
) -> Optional[list]:
    """
    트렌드 데이터에서 Z-Score 기반 이상 구간을 추출한다.
    cagg_5min_raw_stats_ai 30일 baseline으로 각 포인트를 평가하고
    연속 이상 포인트를 구간(zone)으로 병합하여 반환한다.
    """
    if not rows or not columns:
        return None

    # 컬럼 인덱스 매핑
    col_idx = {c: i for i, c in enumerate(columns)}
    tagsn_i = col_idx.get("tagsn")
    time_i = col_idx.get("log_time") or col_idx.get("time") or col_idx.get("timestamp")
    val_i = col_idx.get("val") or col_idx.get("value")
    label_i = col_idx.get("label") or col_idx.get("datadesc")
    tagtype_i = col_idx.get("tagtype")

    if tagsn_i is None or time_i is None or val_i is None:
        return None

    # 아날로그 태그만 수집 (digital 제외)
    tag_data = {}  # tagsn → [(time_str, val)]
    tag_labels = {}  # tagsn → label
    for row in rows:
        if tagtype_i is not None:
            tt = str(row[tagtype_i] or "")
            if "digital" in tt.lower():
                continue
        tsn = str(row[tagsn_i])
        v = row[val_i]
        if v is None:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        t = str(row[time_i])
        tag_data.setdefault(tsn, []).append((t, fv))
        if label_i is not None and tsn not in tag_labels:
            tag_labels[tsn] = str(row[label_i] or tsn)

    if not tag_data:
        return None

    # 30일 baseline 조회 (cagg_5min_raw_stats_ai)
    tagsn_list = list(tag_data.keys())
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT tagsn,
                   AVG((min_val + max_val) / 2.0) AS mean_val,
                   STDDEV((min_val + max_val) / 2.0) AS stddev_val
            FROM cagg_5min_raw_stats_ai
            WHERE region = %s
              AND tagsn = ANY(%s)
              AND bucket >= NOW() - INTERVAL '30 days'
              AND (min_val + max_val) / 2.0 > 0.001
            GROUP BY tagsn
            HAVING COUNT(*) >= 50
        """, (region, tagsn_list))
        baseline = {str(r[0]): (float(r[1]), float(r[2])) for r in cur.fetchall() if r[2] and r[2] > 0}
        cur.close()
    except Exception as e:
        logger.warning(f"Anomaly zone baseline query failed: {e}")
        return None

    if not baseline:
        return None

    # Z-Score 계산 + 연속 이상 포인트를 구간으로 병합
    all_zones = []
    for tsn, points in tag_data.items():
        if tsn not in baseline:
            continue
        mean, stddev = baseline[tsn]
        label = tag_labels.get(tsn, tsn)

        anomaly_points = []
        for t, v in points:
            z = abs(v - mean) / stddev
            if z >= 3.0:
                anomaly_points.append((t, "error"))
            elif z >= 2.0:
                anomaly_points.append((t, "warn"))

        if not anomaly_points:
            continue

        # 연속 포인트 병합
        current = {"start": anomaly_points[0][0], "end": anomaly_points[0][0], "sev": anomaly_points[0][1]}
        for i in range(1, len(anomaly_points)):
            t, sev = anomaly_points[i]
            # 바로 다음 포인트이면 병합 (인덱스 연속성)
            prev_t = anomaly_points[i - 1][0]
            # 5분 이내 간격이면 연속으로 간주
            try:
                from datetime import datetime
                dt_prev = datetime.fromisoformat(prev_t.replace("Z", "+00:00") if "Z" in prev_t else prev_t)
                dt_cur = datetime.fromisoformat(t.replace("Z", "+00:00") if "Z" in t else t)
                gap_minutes = abs((dt_cur - dt_prev).total_seconds()) / 60
            except Exception:
                gap_minutes = 999

            if gap_minutes <= 15:  # 15분 이내 → 연속 구간
                current["end"] = t
                if sev == "error":
                    current["sev"] = "error"
            else:
                all_zones.append({
                    "start_time": current["start"],
                    "end_time": current["end"],
                    "severity": current["sev"],
                    "tag_name": label,
                })
                current = {"start": t, "end": t, "sev": sev}

        all_zones.append({
            "start_time": current["start"],
            "end_time": current["end"],
            "severity": current["sev"],
            "tag_name": label,
        })

    return all_zones if all_zones else None


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
    **kwargs,
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
    if kwargs.get("stddev_stats"):
        response["stddev_stats"] = kwargs["stddev_stats"]
    if kwargs.get("cusum_chart_data"):
        response["cusum_chart_data"] = kwargs["cusum_chart_data"]
    if kwargs.get("anomaly_zones"):
        response["anomaly_zones"] = kwargs["anomaly_zones"]
    if kwargs.get("intent_candidates"):
        response["intent_candidates"] = kwargs["intent_candidates"]
    if kwargs.get("site_group_distribution"):
        response["site_group_distribution"] = kwargs["site_group_distribution"]
    if kwargs.get("site_group"):
        response["site_group"] = kwargs["site_group"]
    if kwargs.get("pattern_analysis"):
        response["pattern_analysis"] = kwargs["pattern_analysis"]
    return response


def _extract_stddev_stats(data_row: dict) -> Optional[dict]:
    """stats_report JSONB에서 표준편차 분석 통계를 구조화된 dict로 추출한다."""
    stats = data_row.get("stats_report")
    if not isinstance(stats, list) or len(stats) < 4:
        return None

    result = {
        "unit": data_row.get("unit", ""),
        "avg_month": data_row.get("avg_month"),
        "avg_year": data_row.get("avg_year"),
    }

    for item in stats:
        label = item.get("구분", "")
        val_365 = item.get("365일기준")
        val_30 = item.get("1년전 30일기준")

        if label == "평균":
            result["mean"] = val_365
            result["mean_30d"] = val_30
        elif label == "표준편차":
            result["stddev"] = val_365
            result["stddev_30d"] = val_30
        elif label == "신뢰구간":
            if isinstance(val_365, str) and "~" in val_365:
                parts = val_365.split("~")
                try:
                    result["ci_lower"] = float(parts[0].strip())
                    result["ci_upper"] = float(parts[1].strip())
                except ValueError:
                    pass
            if isinstance(val_30, str) and "~" in str(val_30):
                parts = str(val_30).split("~")
                try:
                    result["ci_lower_30d"] = float(parts[0].strip())
                    result["ci_upper_30d"] = float(parts[1].strip())
                except ValueError:
                    pass
        elif "초과" in label:
            result["excess"] = val_365
            result["excess_30d"] = val_30

    # today_value = ci_upper + excess (초과량이 양수인 경우)
    ci_upper = result.get("ci_upper")
    excess = result.get("excess")
    if ci_upper is not None and isinstance(excess, (int, float)):
        result["today_value"] = round(ci_upper + excess, 2)

    return result


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


_NO_DATA_HINTS: dict[tuple[str, str], str] = {
    ("FACILITY_PRESSURE_STATUS", "배수지"): "배수지에는 압력 계측 태그가 등록되어 있지 않습니다. 가압장 또는 소블록으로 조회해 보세요.",
    ("FACILITY_PRESSURE_STATUS", ""): "해당 시설에 압력 계측 태그가 등록되어 있지 않습니다.",
}


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
    ft = (params or {}).get("facilitytype", "")
    hint = _NO_DATA_HINTS.get((intent, ft)) or _NO_DATA_HINTS.get((intent, ""))
    summary = hint if hint else "조회된 데이터가 없습니다."
    result = {
        "summary": summary,
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
        "message": summary,
        "intent": intent,
        "answer": result,
    }
    if session_id:
        response["session_id"] = session_id
    return response


# =============================================================================
# 상태 시맨틱 마커
# =============================================================================

# 상태 텍스트 → 마커 레벨 매핑 (우선순위: 긴 키워드 먼저)
_STATUS_MARKER_MAP = [
    ("고장", "error"),
    ("이상", "error"),
    ("경고", "warn"),
    ("주의", "warn"),
    ("정상", "ok"),
    ("양호", "ok"),
    ("가동", "ok"),
    ("정지", "warn"),
]


def wrap_status_marker(text: str) -> str:
    """상태 텍스트를 시맨틱 마커로 감싼다. 예: '정상' → '<<ok:정상>>'"""
    for keyword, level in _STATUS_MARKER_MAP:
        if keyword in text:
            return f"<<{level}:{text}>>"
    return text


# 알람 카테고리 → 심각도 매핑
_ALARM_CATEGORY_SEVERITY = {
    "수위": "error",
    "압력": "error",
    "펌프": "error",
    "네트워크": "error",
    "유량": "warn",
    "밸브": "warn",
    "UPS": "warn",
}


def _alarm_category_marker(category: str) -> str:
    """알람 카테고리를 시맨틱 마커로 감싼다."""
    level = _ALARM_CATEGORY_SEVERITY.get(category, "warn")
    return f"<<{level}:{category}>>"


def _alarm_msg_marker(msg: str) -> str:
    """알람 메시지에서 키워드를 감지하여 시맨틱 마커를 적용한다."""
    error_kw = ("상한", "초과", "고장", "통신장애", "미수신")
    warn_kw = ("하한", "미달", "저하", "약간")
    ok_kw = ("정상", "복구", "해제")
    for kw in error_kw:
        if kw in msg:
            return f"<<error:{msg}>>"
    for kw in warn_kw:
        if kw in msg:
            return f"<<warn:{msg}>>"
    for kw in ok_kw:
        if kw in msg:
            return f"<<ok:{msg}>>"
    return f"<<warn:{msg}>>"


# =============================================================================
# 데이터 후처리 함수
# =============================================================================


def build_hunting_result_block(rows: list, columns: list) -> list:
    """
    v_reservoir_status_variance_5min 다중 행을 헌팅 점검 결과 리스트로 조립한다.
    반환 컬럼: datainfo, variance_status, diff_percent
    반환: [{"prefix": "-", "text": "수위(m) 계측1: <<ok:정상>> (변동률 1.1%)"}, ...]
    """
    items = []
    for row in rows:
        row_dict = dict(zip(columns, row))
        datainfo = row_dict.get("datainfo", "").strip()
        status = row_dict.get("variance_status", "")
        diff_pct = row_dict.get("diff_percent")
        if datainfo and status:
            marked_status = wrap_status_marker(status)
            if diff_pct is not None:
                items.append({"prefix": "-", "text": f"{datainfo}: {marked_status} (변동률 {diff_pct}%)"})
            else:
                items.append({"prefix": "-", "text": f"{datainfo}: {marked_status}"})
    return items


def build_level_detail_block(rows: list, columns: list) -> list:
    """
    fn_reservoir_level_summary() 다중 행을 개별 수위 항목 리스트로 조립한다.
    반환 컬럼: log_time, out_sitename, out_facilitytype, out_datainfo,
              avg_latest, latest_val, avg_month, avg_year, unit
    """
    items = []
    for row in rows:
        row_dict = dict(zip(columns, row))
        datainfo = row_dict.get("out_datainfo", "")
        latest_val = row_dict.get("latest_val")
        avg_month = row_dict.get("avg_month")
        unit = row_dict.get("unit", "")
        if latest_val is None:
            continue
        # 월평균 대비 편차 기반 마커
        marker_text = f"{latest_val}{unit}"
        try:
            lv = float(latest_val)
            am = float(avg_month) if avg_month is not None else None
            if am and am > 0:
                dev_pct = abs(lv - am) / am * 100
                if dev_pct >= 30:
                    marker_text = f"<<error:{latest_val}{unit}>> (월평균 대비 {dev_pct:+.0f}%)"
                elif dev_pct >= 15:
                    marker_text = f"<<warn:{latest_val}{unit}>> (월평균 대비 {dev_pct:+.0f}%)"
                else:
                    marker_text = f"<<ok:{latest_val}{unit}>>"
        except (ValueError, TypeError):
            pass
        items.append({"prefix": "-", "text": f"{datainfo}: {marker_text}"})
    return items


def build_today_flow_detail_block(rows: list, columns: list) -> list:
    """
    fn_today_outflow() 다중 행을 금일 적산 항목 리스트로 조립한다.
    반환 컬럼: sitename, facilitytype, datainfo, unit, today_outflow
    """
    items = []
    for row in rows:
        row_dict = dict(zip(columns, row))
        datainfo = row_dict.get("datainfo", "")
        today_outflow = row_dict.get("today_outflow")
        unit = row_dict.get("unit", "")
        if today_outflow is None:
            continue
        try:
            val = float(today_outflow)
            if val == 0:
                val_text = f"<<warn:0{unit}>>"
            else:
                val_text = f"<<ok:{today_outflow}{unit}>>"
        except (ValueError, TypeError):
            val_text = f"{today_outflow}{unit}"
        items.append({"prefix": "-", "text": f"{datainfo}: {val_text}"})
    return items


def build_outflow_detail_block(rows: list, columns: list) -> list:
    """
    fn_today_outflow_all() 다중 행을 전체 배수지 유출 현황 항목 리스트로 조립한다.
    반환 컬럼: result_sitename, result_facilitytype, result_today_outflow, result_is_total
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
        if outflow is None:
            continue
        try:
            val = float(outflow)
            if val == 0:
                val_text = f"<<error:0㎥>>"
            else:
                val_text = f"<<ok:{outflow}㎥>>"
        except (ValueError, TypeError):
            val_text = f"{outflow}㎥"
        items.append({"prefix": "-", "text": f"{sitename} {facilitytype}: {val_text}"})
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
            marked_msg = _alarm_msg_marker(alarm_msg)
            items.append({
                "prefix": "-",
                "text": f"{alarm_time} — {marked_msg}"
            })
    return items


def _format_latest_value(datadesc: str, val: Any) -> str:
    """최신값을 datadesc 기반으로 시맨틱 마커 포맷팅한다."""
    _DIGITAL_KEYWORDS = ("밸브", "펌프", "FAULT", "CLOSE", "OPEN", "자동")
    is_digital = any(kw in datadesc for kw in _DIGITAL_KEYWORDS)
    if is_digital:
        try:
            v = float(val)
            if v >= 1:
                return "<<ok:가동>>"
            return "<<warn:정지>>"
        except (ValueError, TypeError):
            return wrap_status_marker(str(val))
    return str(val)


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
            formatted_val = _format_latest_value(datadesc, latest_val)
            items.append({
                "prefix": "-",
                "text": f"{datadesc}: {formatted_val} ({latest_time})"
            })
    return items


def build_alarm_rank_block(rows: list, columns: list) -> list:
    """
    FACILITY_ALARM_TOP_COUNT 다중 행을 알람 누적건수 순위 리스트로 조립한다.
    반환 컬럼: alarm_msg, alarm_count
    건수 기반 심각도: 상위 3건 error, 4~6위 warn, 나머지 ok
    """
    items = []
    for idx, row in enumerate(rows):
        row_dict = dict(zip(columns, row))
        alarm_msg = row_dict.get("alarm_msg", "")
        alarm_count = row_dict.get("alarm_count", 0)
        if alarm_msg:
            try:
                cnt = int(alarm_count)
            except (ValueError, TypeError):
                cnt = 0
            if idx < 3:
                level = "error"
            elif idx < 6:
                level = "warn"
            else:
                level = "ok"
            count_marker = f"<<{level}:{alarm_count}건>>"
            text = f"{alarm_msg} {count_marker}"
            if idx == len(rows) - 1:
                text += " 순으로 발생하였습니다."
            items.append({"prefix": f"{idx + 1}.", "text": text})
    return items


# -----------------------------------------------------------------------------
# 경보 카테고리 필터 매핑
# (질문 키워드, DB alarm_category 값, alarm_msg ILIKE 키워드, 표시 라벨)
# -----------------------------------------------------------------------------
_ALARM_FILTER_RULES: list[tuple[list[str], list[str], list[str], str]] = [
    # (질문 키워드,  alarm_category 정확매칭,  alarm_msg ILIKE 폴백,  표시라벨)
    # alarm_category가 DB에 존재하는 항목 → category만 사용 (msg 폴백 없음)
    (["수위"],              ["수위"],       [],                 "수위"),
    (["압력"],              ["압력"],       [],                 "압력"),
    (["전원", "ups"],       ["UPS"],        [],                 "전원"),
    (["펌프"],              ["펌프"],       [],                 "펌프"),
    (["밸브"],              ["밸브"],       [],                 "밸브"),
    (["유량"],              ["유량"],       [],                 "유량"),
    # 통신: category='네트워크' + msg ILIKE '%통신%' 병용
    (["통신", "네트워크"],  ["네트워크"],   ["통신"],           "통신"),
    # 수질: alarm_category 없음 → msg ILIKE만
    (["수질", "탁도"],      [],             ["수질", "탁도"],   "수질"),
]


def _execute_catalog_trend_query(
    facilitytype: str,
    sitename: str,
    trend_name_filter: str,
    label_pattern: str,
    from_ts: str,
    to_ts: str,
) -> tuple[list, list]:
    """tb_trend_catalog + tb_tag_raw_data 2단계 청크 직접 쿼리.

    TimescaleDB ChunkAppend가 (tagsn, logtime) 인덱스를 사용하지 못하는 문제를
    우회하기 위해, 카탈로그에서 tagsn 리스트를 먼저 추출한 후
    각 청크를 직접 쿼리하여 인덱스 스캔을 강제한다.

    Returns:
        (rows, columns) — columns: ["현장명","항목","날짜","평균","최대","최소","단위"]
    """
    conn = get_db_connection()
    try:
        return _execute_catalog_trend_query_inner(
            conn, facilitytype, sitename, trend_name_filter, label_pattern, from_ts, to_ts,
        )
    finally:
        conn.close()


def _execute_catalog_trend_query_inner(
    conn,
    facilitytype: str,
    sitename: str,
    trend_name_filter: str,
    label_pattern: str,
    from_ts: str,
    to_ts: str,
) -> tuple[list, list]:
    cur = conn.cursor()

    # Step1: 카탈로그에서 태그 + 메타 추출
    sn_clause = f"tc.sitename = '{sitename}'" if sitename and sitename != "%%" else "1=1"
    tn_clause = f"tc.trend_name = '{trend_name_filter}'" if trend_name_filter and trend_name_filter != "%%" else "1=1"
    lbl_clause = f"(i->>'label') LIKE '{label_pattern}'" if label_pattern and label_pattern != "%%" else "1=1"

    cur.execute(f"""
        SELECT tc.sitename,
            (i->>'tagsn')::text AS tagsn,
            (i->>'label')::text AS label,
            COALESCE(i->>'unit', '') AS unit
        FROM tb_trend_catalog tc
        CROSS JOIN LATERAL jsonb_array_elements(tc.meta->'items') AS i
        WHERE {sn_clause}
            AND tc.facilitytype = '{facilitytype}'
            AND {tn_clause}
            AND {lbl_clause}
    """)
    catalog_rows = cur.fetchall()
    if not catalog_rows:
        return [], []

    tag_meta: dict[str, tuple[str, str, str]] = {}
    for sn, tagsn, label, unit in catalog_rows:
        if tagsn not in tag_meta:
            tag_meta[tagsn] = (sn, label, unit)
    tagsn_list = list(tag_meta.keys())
    logger.info(f"카탈로그 태그: {len(tagsn_list)}개 (facilitytype={facilitytype})")

    # Step2: 해당 시간 범위의 청크 목록
    cur.execute("""
        SELECT c.schema_name || '.' || c.table_name
        FROM _timescaledb_catalog.chunk c
        JOIN _timescaledb_catalog.hypertable h ON c.hypertable_id = h.id
        JOIN _timescaledb_catalog.chunk_constraint cc ON cc.chunk_id = c.id
        JOIN _timescaledb_catalog.dimension_slice ds ON ds.id = cc.dimension_slice_id
        WHERE h.table_name = 'tb_tag_raw_data'
          AND ds.range_start <= extract(epoch from %s::timestamptz) * 1000000
          AND ds.range_end > extract(epoch from %s::timestamptz) * 1000000
        ORDER BY ds.range_start
    """, (to_ts, from_ts))
    chunks = [r[0] for r in cur.fetchall()]
    if not chunks:
        return [], []
    logger.info(f"대상 청크: {len(chunks)}개")

    # Step3: 각 청크에서 tagsn 직접 인덱스 스캔
    all_raw: list[tuple] = []
    for chunk_name in chunks:
        cur.execute(f"""
            SELECT tagsn,
                time_bucket('1 day', logtime) AS bucket,
                SUM(val) AS sum_val,
                COUNT(*) AS cnt,
                MAX(val) AS max_val,
                MIN(val) AS min_val
            FROM {chunk_name}
            WHERE tagsn = ANY(%s)
              AND logtime >= %s::timestamptz AND logtime < %s::timestamptz
            GROUP BY tagsn, time_bucket('1 day', logtime)
        """, (tagsn_list, from_ts, to_ts))
        all_raw.extend(cur.fetchall())

    if not all_raw:
        return [], []

    # Step4: 청크간 재집계 (같은 tagsn+bucket이 여러 청크에 걸칠 수 있음)
    from collections import defaultdict
    agg: dict[tuple[str, object], list] = defaultdict(list)
    for tagsn, bucket, sum_val, cnt, max_val, min_val in all_raw:
        agg[(tagsn, bucket)].append((float(sum_val), int(cnt), float(max_val), float(min_val)))

    columns = ["현장명", "항목", "날짜", "평균", "최대", "최소", "단위"]
    rows = []
    for (tagsn, bucket), vals in sorted(agg.items(), key=lambda x: (tag_meta.get(x[0][0], ("",))[0], tag_meta.get(x[0][0], ("",))[1], x[0][1])):
        meta = tag_meta.get(tagsn)
        if not meta:
            continue
        total_sum = sum(v[0] for v in vals)
        total_cnt = sum(v[1] for v in vals)
        avg_val = round(total_sum / total_cnt, 2) if total_cnt > 0 else 0
        max_val = round(max(v[2] for v in vals), 2)
        min_val = round(min(v[3] for v in vals), 2)
        date_str = bucket.strftime("%Y-%m-%d") if hasattr(bucket, "strftime") else str(bucket)[:10]
        rows.append((meta[0], meta[1], date_str, avg_val, max_val, min_val, meta[2]))

    return rows, columns


def _get_catalog_trend_filter(question: str, datainfo: str) -> tuple[str, str, str]:
    """질문에서 카탈로그 필터 (trend_name, label_pattern, display_name)를 추출한다.

    compound 키워드(유출유량, 유입유량) 우선 매칭 후 단순 키워드 폴백.
    Returns:
        (trend_name_filter, label_pattern, display_name)
    """
    _COMPOUND = [
        ("유출유량", "유량", "%%유출%%적산%%", "유출유량"),
        ("유출 유량", "유량", "%%유출%%적산%%", "유출유량"),
        ("유입유량", "유량", "%%유입%%", "유입유량"),
        ("유입 유량", "유량", "%%유입%%", "유입유량"),
    ]
    for kw, tn, lp, dn in _COMPOUND:
        if kw in question:
            return tn, lp, dn

    _SIMPLE = {
        "수위": ("수위", "%%", "수위"),
        "압력": ("압력", "%%", "압력"),
        "유량": ("유량", "%%", "유량"),
        "밸브": ("%%", "%%밸브%%", "밸브"),
        "펌프": ("%%", "%%펌프%%", "펌프"),
    }
    if datainfo in _SIMPLE:
        return _SIMPLE[datainfo]
    return ("%%", "%%", datainfo or "전체")


def _extract_alarm_filter(question: str) -> tuple[str, str]:
    """질문에서 경보 카테고리 필터 SQL 절과 라벨을 추출한다.

    Returns:
        (alarm_filter_clause, alarm_label)
        - alarm_filter_clause: "AND (...)" SQL 절 또는 빈 문자열
        - alarm_label: "통신", "수위" 등 표시용 또는 빈 문자열
    """
    q = question.lower()
    for q_keywords, categories, msg_keywords, label in _ALARM_FILTER_RULES:
        if any(kw in q for kw in q_keywords):
            conditions: list[str] = []
            for cat in categories:
                conditions.append(f"alarm_category = '{cat}'")
            for kw in msg_keywords:
                conditions.append(f"alarm_msg ILIKE '%{kw}%'")
            clause = "AND (" + " OR ".join(conditions) + ")"
            return clause, label
    return "", ""


def _extract_alarm_level(question: str) -> tuple[str, str]:
    """질문에서 알람 수준(HH/LL/FAULT) SQL 절과 라벨을 추출한다.

    Returns:
        (alarm_level_clause, alarm_level_label)
    """
    q = question.upper()
    if "HH" in q:
        return "AND alarm_msg ILIKE '%HH%'", "HH"
    if "LL" in q:
        return "AND alarm_msg ILIKE '%LL%'", "LL"
    q_lower = question.lower()
    if "fault" in q_lower or "고장" in question:
        return "AND (alarm_msg ILIKE '%FAULT%' OR alarm_msg ILIKE '%고장%')", "FAULT/고장"
    return "", ""


def build_alarm_cause_rank_block(rows: list, columns: list) -> list:
    """
    FACILITY_ALARM_CAUSE_DIAGNOSIS_RANK 다중 행을 발생원인 순위 리스트로 조립한다.
    반환 컬럼: alarm_msg, alarm_count, diagnosed_causes
    """
    items = []
    for idx, row in enumerate(rows):
        row_dict = dict(zip(columns, row))
        alarm_msg = row_dict.get("alarm_msg", "")
        alarm_count = row_dict.get("alarm_count", 0)
        diagnosed_causes = row_dict.get("diagnosed_causes", "")
        if alarm_msg:
            try:
                cnt = int(alarm_count)
            except (ValueError, TypeError):
                cnt = 0
            if idx < 3:
                level = "error"
            elif idx < 6:
                level = "warn"
            else:
                level = "ok"
            count_marker = f"<<{level}:{alarm_count}건>>"
            text = f"{alarm_msg} {count_marker}"
            if diagnosed_causes:
                text += f" (진단: {diagnosed_causes})"
            items.append({"prefix": f"{idx + 1}.", "text": text})
    return items


def build_pressure_detail_block(rows: list, columns: list) -> list:
    """
    FACILITY_PRESSURE_STATUS 다중 행을 압력 항목 리스트로 조립한다.
    반환 컬럼: datainfo, pressure_val, log_time, month_avg, year_avg, unit
    - 설정압력(Analog Output 개념) 제외
    - 동일 datainfo 중복 제거
    - 월평균 대비 편차 시맨틱 마커 (±30% 이상 error, ±15% warn)
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
        month_avg = row_dict.get("month_avg")
        unit = row_dict.get("unit", "")
        dedup_key = (datainfo, val, log_time)
        if val is not None and dedup_key not in seen:
            seen.add(dedup_key)
            # 월평균 대비 편차 마커
            status_tag = ""
            try:
                v = float(val)
                m = float(month_avg) if month_avg else None
                if m and abs(m) > 0.001:
                    dev_pct = abs(v - m) / abs(m) * 100
                    if dev_pct >= 30:
                        status_tag = f" <<error:편차 {dev_pct:.0f}%>>"
                    elif dev_pct >= 15:
                        status_tag = f" <<warn:편차 {dev_pct:.0f}%>>"
                    else:
                        status_tag = f" <<ok:정상 범위>>"
            except (ValueError, TypeError):
                pass
            items.append({"prefix": "•", "text": f"{log_time} 기준 {datainfo}: {val}{unit}{status_tag}"})
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


def build_abnormal_summary_detail_block(rows: list, columns: list) -> list:
    """
    FACILITY_ABNORMAL_STATUS_SUMMARY 다중 행을 시설유형별 이상 현황으로 조립한다.
    반환 컬럼: facilitytype, cnt, missing_cnt, missing_sites
    결측 시설 유무에 따라 시맨틱 마커 적용.
    """
    items = []
    for row in rows:
        rd = dict(zip(columns, row))
        ftype = rd.get("facilitytype", "")
        cnt = rd.get("cnt", 0)
        missing_cnt = rd.get("missing_cnt", 0)
        missing_sites = rd.get("missing_sites", "")
        try:
            m = int(missing_cnt)
        except (ValueError, TypeError):
            m = 0
        if m > 0:
            level = "error" if m >= 3 else "warn"
            marker = f"<<{level}:이상 {m}건>>"
            site_info = f" ({missing_sites})" if missing_sites else ""
            items.append({
                "prefix": "•",
                "text": f"{ftype}: 전체 {cnt}개 중 {marker}{site_info}"
            })
        else:
            items.append({
                "prefix": "•",
                "text": f"{ftype}: 전체 {cnt}개 <<ok:모두 정상>>"
            })
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
            marked_status = wrap_status_marker(status) if status else "<<warn:상태없음>>"
            if status and rtt is not None:
                rtt_val = int(float(str(rtt))) if rtt else 0
                items.append({
                    "prefix": "-",
                    "text": f"{equip} : {marked_status}, 응답속도 : {rtt_val}ms"
                })
            elif status and error:
                items.append({
                    "prefix": "-",
                    "text": f"{equip} : {marked_status}, {error}"
                })
            elif status:
                items.append({
                    "prefix": "-",
                    "text": f"{equip} : {marked_status}"
                })
            else:
                items.append({
                    "prefix": "-",
                    "text": f"{equip} : <<warn:상태없음>>"
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


def _mark_equipment_status(row: dict) -> dict:
    """설비 행의 status 필드에 시맨틱 마커를 적용한다."""
    _STATUS_KEYS = ("status", "상태", "운영상태", "status_code")
    for key in _STATUS_KEYS:
        if key in row and row[key]:
            row[key] = wrap_status_marker(str(row[key]))
    return row


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
                        result.append(_mark_equipment_status(row))
            elif isinstance(items, dict):
                row = {"category": category}
                row.update(items)
                result.append(_mark_equipment_status(row))
    elif isinstance(meta, list):
        for item in meta:
            if isinstance(item, dict):
                result.append(_mark_equipment_status(item))

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
                marked_msg = _alarm_msg_marker(msg)
                cat_tag = f" [{_alarm_category_marker(cat)}]" if cat else ""
                detail_items.append({
                    "prefix": "-",
                    "text": f"{site} {ftype}: {marked_msg}{cat_tag} ({atime})"
                })

        data["total_alarm_count"] = len(rows)
        if cat_counts:
            # 카테고리별 건수에 시맨틱 마커 적용
            cat_parts = []
            for k, v in cat_counts.items():
                level = _ALARM_CATEGORY_SEVERITY.get(k, "warn")
                cat_parts.append(f"<<{level}:{k} {v}건>>")
            data["category_summary"] = ", ".join(cat_parts)
        else:
            data["category_summary"] = "<<ok:없음>>"

        if detail_items:
            data["ongoing_alarm_detail_block"] = _EXPAND_MARKER
            data["_detail_blocks"]["ongoing_alarm_detail_block"] = detail_items

        # 테이블 데이터 alarm_msg에도 마커 적용 (DataTable 셀 색상용)
        if columns and "alarm_msg" in columns:
            msg_idx = columns.index("alarm_msg")
            rows[:] = [
                tuple(
                    _alarm_msg_marker(str(v)) if i == msg_idx and v else v
                    for i, v in enumerate(row)
                )
                for row in rows
            ]

    # -------------------------------------------------
    # 경보 이상 발생 지점: 카테고리 요약 + 상세 리스트 조립
    # -------------------------------------------------
    if intent == "ALARM_ABNORMAL_LOCATIONS":
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
            status = rd.get("alarm_status", "")
            if cat:
                cat_counts[cat] += 1
            if msg:
                marked_msg = _alarm_msg_marker(msg)
                cat_tag = f" [{_alarm_category_marker(cat)}]" if cat else ""
                status_marker = " <<ok:해제>>" if status == "알람해제" else ""
                detail_items.append({
                    "prefix": "-",
                    "text": f"{site} {ftype}: {marked_msg}{cat_tag}{status_marker} ({atime})"
                })

        data["total_alarm_count"] = len(rows)

        if cat_counts:
            cat_parts = []
            for k, v in cat_counts.items():
                level = _ALARM_CATEGORY_SEVERITY.get(k, "warn")
                cat_parts.append(f"<<{level}:{k} {v}건>>")
            data["category_summary"] = ", ".join(cat_parts)
        else:
            data["category_summary"] = "<<ok:없음>>"

        if detail_items:
            data["alarm_location_detail_block"] = _EXPAND_MARKER
            data["_detail_blocks"]["alarm_location_detail_block"] = detail_items

        # 테이블 데이터 alarm_msg에도 마커 적용
        if columns and "alarm_msg" in columns:
            msg_idx = columns.index("alarm_msg")
            rows[:] = [
                tuple(
                    _alarm_msg_marker(str(v)) if i == msg_idx and v else v
                    for i, v in enumerate(row)
                )
                for row in rows
            ]

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
        # sitename/facilitytype 미추출 시 "전체" 기본값
        if not data.get("sitename"):
            data["sitename"] = "전체"
        if not data.get("facilitytype"):
            data["facilitytype"] = "시설"
        if not data.get("alarm_label"):
            data["alarm_label"] = ""
        data["alarm_rank_block"] = _EXPAND_MARKER
        data["_detail_blocks"]["alarm_rank_block"] = build_alarm_rank_block(rows, columns)
        # 파이차트용 블록도 함께 조립
        data["alarm_cause_rank_block"] = _EXPAND_MARKER
        data["_detail_blocks"]["alarm_cause_rank_block"] = build_alarm_cause_rank_block(rows, columns)

    # -------------------------------------------------
    # 경보 발생원인 진단 순위: 다중 행 원인별 순위 조립
    # -------------------------------------------------
    if intent == "FACILITY_ALARM_CAUSE_DIAGNOSIS_RANK":
        # sitename/facilitytype 미추출 시 "전체" 기본값
        if not data.get("sitename"):
            data["sitename"] = "전체"
        if not data.get("facilitytype"):
            data["facilitytype"] = "시설"
        # alarm_label 기본값 (통신 필터 여부)
        if not data.get("alarm_label"):
            data["alarm_label"] = ""
        data["alarm_cause_rank_block"] = _EXPAND_MARKER
        data["_detail_blocks"]["alarm_cause_rank_block"] = build_alarm_cause_rank_block(rows, columns)

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
    # 이상 스캔: Z-Score + Isolation Forest 기반 전체 스캔
    # -------------------------------------------------
    if intent == "ANOMALY_SCAN_ALL":
        _profiles = site_profiler.profiles if site_profiler.profiles else None
        counts = count_anomaly_levels(rows, columns)
        data["total_tag_count"] = len(rows)
        data["error_count"] = counts["이상"]
        data["warn_count"] = counts["주의"]
        data["ok_count"] = counts["정상"]
        data["comm_error_sites"] = count_comm_error_sites(rows, columns)

        # Isolation Forest ML 보강
        try:
            iforest_manager.ensure_trained(get_db_connection, site_profiles=_profiles)
            if_result = iforest_manager.predict_for_rows(rows, columns)
            if if_result:
                data["ml_model_count"] = iforest_manager.model_count
                data["ml_anomaly_count"] = if_result.get("if_anomaly_count", 0)
                data["ml_agree_count"] = if_result.get("z_and_if_agree", 0)
        except Exception as e:
            logger.warning(f"IForest enrichment 실패: {e}")

        # 그룹 분포 요약 (프론트엔드 표시용)
        if _profiles:
            group_dist = {"A": 0, "B": 0, "C": 0, "D": 0}
            for p in _profiles.values():
                g = p.get("site_group", "B")
                group_dist[g] = group_dist.get(g, 0) + 1
            data["site_group_distribution"] = group_dist

        data["anomaly_scan_detail_block"] = _EXPAND_MARKER
        data["_detail_blocks"]["anomaly_scan_detail_block"] = \
            build_anomaly_scan_detail_block(rows, columns, site_profiles=_profiles)

    # -------------------------------------------------
    # 시설 정밀 진단: Z-Score + 방향전환 + IF 복합 판정
    # -------------------------------------------------
    if intent == "ANOMALY_FACILITY_DETAIL":
        _profiles = site_profiler.profiles if site_profiler.profiles else None
        _site = params.get("sitename", "")
        _ft = params.get("facilitytype", "")
        counts = count_anomaly_levels(rows, columns)
        data["total_tag_count"] = len(rows)
        data["error_count"] = counts["이상"]
        data["warn_count"] = counts["주의"]
        data["ok_count"] = counts["정상"]

        # 그룹 정보 표시
        _group = "B"
        if _profiles:
            _profile = _profiles.get((_site, _ft))
            if _profile:
                _group = _profile.get("site_group", "B")
                data["site_group"] = _group

        # Isolation Forest ML 보강
        try:
            iforest_manager.ensure_trained(get_db_connection, site_profiles=_profiles)
            if_result = iforest_manager.predict_for_rows(rows, columns)
            if if_result:
                data["ml_anomaly_count"] = if_result.get("if_anomaly_count", 0)
        except Exception as e:
            logger.warning(f"IForest enrichment 실패: {e}")

        # C그룹 패턴 분석 (수위 태그 대상)
        pattern_result = None
        if _group == "C" and _profiles:
            try:
                col_map = {c: i for i, c in enumerate(columns)}
                tagsn_idx = col_map.get("tagsn")
                if tagsn_idx is not None:
                    conn = get_db_connection()
                    cur = conn.cursor()
                    # 수위 태그 중 하나에 대해 6시간 시계열 조회
                    for row in rows:
                        tagsn = row[tagsn_idx]
                        datainfo = row[col_map.get("datainfo", 0)] or ""
                        if "수위" not in datainfo:
                            continue
                        hh, ll = get_hh_ll_for_site(conn, _site, _ft, tagsn, _profiles)
                        if hh is None and ll is None:
                            continue
                        cur.execute("""
                            SELECT bucket::text, (min_val + max_val) / 2.0
                            FROM cagg_5min_raw_stats_ai
                            WHERE tagsn = %s AND bucket >= now() - interval '6 hours'
                            ORDER BY bucket
                        """, (tagsn,))
                        series = [(r[0], float(r[1])) for r in cur.fetchall()]
                        if len(series) >= 6:
                            pattern_result = analyze_level_pattern(series, hh, ll)
                            if pattern_result.get("has_critical_pattern"):
                                data["pattern_analysis"] = pattern_result
                                break
                    cur.close()
                    conn.close()
            except Exception as e:
                logger.warning(f"C그룹 패턴 분석 실패: {e}")

        data["anomaly_facility_detail_block"] = _EXPAND_MARKER
        data["_detail_blocks"]["anomaly_facility_detail_block"] = \
            build_anomaly_facility_detail_block(
                rows, columns,
                site_profiles=_profiles,
                sitename=_site,
                facilitytype=_ft,
                pattern_result=pattern_result,
            )

    # -------------------------------------------------
    # 이상 이력: alarm_severity별 집계 + detail 블록
    # -------------------------------------------------
    if intent == "ANOMALY_HISTORY":
        sev_counts = count_alarm_severity(rows, columns)
        data["total_alarm_count"] = len(rows)
        parts = []
        if sev_counts["경고"]:
            parts.append(f"<<error:경고>> {sev_counts['경고']}건")
        if sev_counts["주의"]:
            parts.append(f"<<warn:주의>> {sev_counts['주의']}건")
        if sev_counts["정상"]:
            parts.append(f"<<ok:정상>> {sev_counts['정상']}건")
        if sev_counts["미분류"]:
            parts.append(f"미분류 {sev_counts['미분류']}건")
        data["alarm_severity_summary"] = ", ".join(parts) if parts else "알람 없음"
        data["anomaly_history_detail_block"] = _EXPAND_MARKER
        data["_detail_blocks"]["anomaly_history_detail_block"] = \
            build_anomaly_history_detail_block(rows, columns)

    # -------------------------------------------------
    # 위험 예측: 선형 회귀 기반 예측 결과
    # -------------------------------------------------
    if intent == "ANOMALY_PREDICT":
        counts = count_anomaly_levels(rows, columns, z_col="predicted_z")
        data["predict_error_count"] = counts["이상"]
        data["predict_warn_count"] = counts["주의"]
        data["anomaly_predict_detail_block"] = _EXPAND_MARKER
        data["_detail_blocks"]["anomaly_predict_detail_block"] = \
            build_anomaly_predict_detail_block(rows, columns)

    # -------------------------------------------------
    # 시설간 비교: 사이트별 건강도 비교
    # -------------------------------------------------
    if intent == "ANOMALY_COMPARE":
        data["compare_site_count"] = len(rows)
        col_map = {c: i for i, c in enumerate(columns)}
        total_idx = col_map.get("total_sensors")
        data["compare_total_sensors"] = sum(
            int(r[total_idx] or 0) for r in rows
        ) if total_idx is not None else 0
        data["anomaly_compare_detail_block"] = _EXPAND_MARKER
        data["_detail_blocks"]["anomaly_compare_detail_block"] = \
            build_anomaly_compare_detail_block(rows, columns)

    # -------------------------------------------------
    # 시간대 패턴: 현재 시간대 이상 센서
    # -------------------------------------------------
    if intent == "ANOMALY_PATTERN":
        data["pattern_anomaly_count"] = len(rows)
        col_map = {c: i for i, c in enumerate(columns)}
        hour_idx = col_map.get("current_hour")
        data["current_hour"] = int(rows[0][hour_idx]) if rows and hour_idx is not None else ""
        data["anomaly_pattern_detail_block"] = _EXPAND_MARKER
        data["_detail_blocks"]["anomaly_pattern_detail_block"] = \
            build_anomaly_pattern_detail_block(rows, columns)

    # -------------------------------------------------
    # CUSUM + MNF 누수추정: fn_night_min_flow_summary 결과를 CUSUM 분석
    # -------------------------------------------------
    if intent == "LEAK_CUSUM_ANALYSIS":
        cusum_results = compute_cusum_for_tags(rows, columns)
        cusum_counts = count_cusum_status(cusum_results)

        data["cusum_tag_count"] = len(cusum_results)
        data["cusum_alarm_count"] = cusum_counts.get("누수의심", 0)
        data["cusum_warn_count"] = cusum_counts.get("주의", 0)
        data["cusum_ok_count"] = cusum_counts.get("정상", 0)

        # 테이블 데이터를 CUSUM 결과 테이블로 교체
        cusum_table_rows, cusum_table_cols = build_cusum_summary_table(cusum_results)
        data["_cusum_results"] = cusum_results
        data["_cusum_table_rows"] = cusum_table_rows
        data["_cusum_table_columns"] = cusum_table_cols

        data["leak_cusum_detail_block"] = _EXPAND_MARKER
        data["_detail_blocks"]["leak_cusum_detail_block"] = \
            build_leak_cusum_detail_block(cusum_results)

    # -------------------------------------------------
    # 이상 설비 현황: 결측률 기반 시맨틱 마커 상세
    # -------------------------------------------------
    if intent == "FACILITY_ABNORMAL_STATUS_SUMMARY":
        total_types = len(rows)
        abnormal_types = sum(
            1 for r in rows
            if int(dict(zip(columns, r)).get("missing_cnt", 0) or 0) > 0
        )
        data["abnormal_type_count"] = abnormal_types
        data["total_type_count"] = total_types
        data["abnormal_summary_block"] = _EXPAND_MARKER
        data["_detail_blocks"]["abnormal_summary_block"] = \
            build_abnormal_summary_detail_block(rows, columns)

    # -------------------------------------------------
    # 야간최소유량 현황: 월평균 대비 편차 시맨틱 마커
    # -------------------------------------------------
    if intent == "NIGHT_MIN_FLOW_STATUS" and rows:
        rd = dict(zip(columns, rows[0]))
        try:
            curr = float(rd.get("current_val") or 0)
            avg_m = float(rd.get("avg_month") or 0)
            if abs(avg_m) > 0.001:
                dev_pct = (curr - avg_m) / abs(avg_m) * 100
                if abs(dev_pct) >= 50:
                    data["nmf_status"] = f"<<error:월평균 대비 {dev_pct:+.0f}%>>"
                elif abs(dev_pct) >= 20:
                    data["nmf_status"] = f"<<warn:월평균 대비 {dev_pct:+.0f}%>>"
                else:
                    data["nmf_status"] = "<<ok:정상 범위>>"
            else:
                data["nmf_status"] = ""
        except (ValueError, TypeError):
            data["nmf_status"] = ""

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
    intent_candidates = []
    if request.force_intent:
        # 사후 보정: 프론트엔드에서 지정한 인텐트 강제 사용
        intent_name = request.force_intent
        intent_def = intent_index.get_definition(intent_name)
        category = intent_classifier._get_category_for_intent(intent_name) or "기타"
        classify_method = "force_intent"
        logger.info(f"강제 인텐트 지정: intent={intent_name}")
    elif is_correction and session.last_intent:
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
        intent_candidates = classification.get("intent_candidates", [])
        logger.info(
            f"Intent 분류: name={intent_name}, category={category}, method={classify_method}"
        )

    # 4. 하이브리드 파라미터 추출
    new_params = await asyncio.to_thread(
        param_extractor_instance.extract_all, user_question, intent_name
    )

    # 4.4. 다중 sitename 추출
    extra_sitenames = new_params.pop("_extra_sitenames", None)
    facility_pairs = new_params.pop("_facility_pairs", None)

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
        "ALARM_ABNORMAL_LOCATIONS",
        "FACILITY_CATALOG_TREND_TABLE",
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

    # 4.7.1. 위치도/계통도: facilitytype 자동 해소 후 인텐트 재매핑
    _LOCATION_REMAP = {
        "배수지": "RESERVOIR_LOCATION",
        "가압장": "BOOSTER_STATION_LOCATION",
        "감압시설": "PRESSURE_REDUCING_FACILITY_LOCATION",
        "소블록": "BLOCK_LOCATION", "중블록": "BLOCK_LOCATION", "대블록": "BLOCK_LOCATION",
    }
    _DIAGRAM_REMAP = {
        "배수지": "RESERVOIR_SYSTEM_DIAGRAM",
        "가압장": "BOOSTER_STATION_SYSTEM_DIAGRAM",
        "소블록": "BLOCK_SYSTEM_DIAGRAM", "중블록": "BLOCK_SYSTEM_DIAGRAM", "대블록": "BLOCK_SYSTEM_DIAGRAM",
    }
    _q_nospace = user_question.replace(" ", "")
    _resolved_ft = new_params.get("facilitytype") or new_params.get("block_level")
    if _resolved_ft:
        if "위치도" in _q_nospace and _resolved_ft in _LOCATION_REMAP:
            _new_intent = _LOCATION_REMAP[_resolved_ft]
            if intent_name != _new_intent:
                logger.info(f"위치도 인텐트 재매핑: {intent_name} → {_new_intent} (ft={_resolved_ft})")
                intent_name = _new_intent
                intent_def = intent_index.get_definition(intent_name)
        elif "계통도" in _q_nospace and _resolved_ft in _DIAGRAM_REMAP:
            _new_intent = _DIAGRAM_REMAP[_resolved_ft]
            if intent_name != _new_intent:
                logger.info(f"계통도 인텐트 재매핑: {intent_name} → {_new_intent} (ft={_resolved_ft})")
                intent_name = _new_intent
                intent_def = intent_index.get_definition(intent_name)

    # 4.8. FACILITY_TREND + 야간최소유량: sitename 미추출 시 '전체' 기본값
    #       fn_night_min_flow_summary('전체', ...) 로 전체 조회 가능
    _q_pre_check = user_question.replace(" ", "")
    if (intent_name == "FACILITY_TREND"
            and "야간최소유량" in _q_pre_check
            and not new_params.get("sitename")):
        new_params["sitename"] = "전체"
        logger.info("야간최소유량 sitename 미추출 → '전체' 기본값 적용")

    # 4.9. LEAK_CUSUM_ANALYSIS: 기본 파라미터 설정
    if intent_name == "LEAK_CUSUM_ANALYSIS":
        if not new_params.get("facilitytype"):
            new_params["facilitytype"] = "소블록"
        if not new_params.get("sitename"):
            new_params["sitename"] = "전체"
        if not new_params.get("from_ts"):
            new_params["from_ts"] = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        if not new_params.get("to_ts"):
            new_params["to_ts"] = datetime.now().strftime("%Y-%m-%d")
        logger.info(f"LEAK_CUSUM defaults: site={new_params['sitename']}, "
                     f"ft={new_params['facilitytype']}, "
                     f"from={new_params['from_ts']}, to={new_params['to_ts']}")

    # 4.10. ANOMALY 인텐트: 선택적 시설 필터 + 범위 라벨 설정
    if intent_name in _ANOMALY_FILTER_INTENTS:
        new_params["anomaly_facility_filter"] = build_anomaly_facility_filter(
            intent_name, new_params
        )
        new_params["anomaly_scope"] = build_anomaly_scope_label(new_params)
        logger.info(f"ANOMALY filter: intent={intent_name}, "
                     f"scope={new_params['anomaly_scope']}, "
                     f"filter={new_params['anomaly_facility_filter']!r}")

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
            intent_candidates=intent_candidates,
        )

    # 검증 통과 — 기존 파이프라인 진행
    intent = intent_name
    sql_template = intent_def.get("sql", "")
    answer_template = intent_def.get("answer_template", {})
    graph_type = intent_def.get("graph_type", "none")
    table_columns = intent_def.get("table_columns")
    table_type = intent_def.get("table_type")

    logger.info(f"INTENT 확정: {intent} (method={classify_method})")

    # 경보 순위 인텐트: sitename/facilitytype 선택적 + 카테고리 필터
    _ALARM_PIE_INTENTS = {"FACILITY_ALARM_CAUSE_DIAGNOSIS_RANK", "FACILITY_ALARM_TOP_COUNT"}
    if intent in _ALARM_PIE_INTENTS:
        if not params.get("sitename") or params.get("sitename") == "%%":
            params["sitename"] = ""
        if not params.get("facilitytype") or params.get("facilitytype") == "%%":
            params["facilitytype"] = ""
        # 경보 카테고리 필터 (수위/압력/통신/전원/펌프/밸브/수질/유량)
        clause, label = _extract_alarm_filter(request.user_question or "")
        params["alarm_filter_clause"] = clause
        params["alarm_label"] = label

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

    # 빈 SQL 체크 (동적 SQL 생성 인텐트는 커스텀 핸들러에서 sql_combined 설정)
    _DYNAMIC_SQL_INTENTS = {"ALARM_ABNORMAL_LOCATIONS", "FACILITY_CATALOG_TREND_TABLE"}
    if intent not in _DYNAMIC_SQL_INTENTS and (not sql_combined or not sql_combined.strip()):
        rendered_answer = render_answer_template(answer_template, params)
        rendered_answer = apply_corrections_to_answer(rendered_answer, params)
        return build_success_response(
            intent=intent,
            answer=rendered_answer,
            graph_type=graph_type,
            session_id=sid,
        )

    # FACILITY_TREND + 야간최소유량: fn_night_min_flow_summary 사용
    _q_no_space = user_question.replace(" ", "")
    _is_night_min_flow = intent == "FACILITY_TREND" and "야간최소유량" in _q_no_space
    if _is_night_min_flow:
        # 기본 기간: 1년 (from_ts/to_ts가 기본 7일로 설정된 경우 오버라이드)
        _ft = params.get("from_ts", "")
        _tt = params.get("to_ts", "")
        if _ft and _tt:
            try:
                _ft_date = datetime.strptime(_ft.strip("'"), "%Y-%m-%d")
                _tt_date = datetime.strptime(_tt.strip("'"), "%Y-%m-%d")
                if (_tt_date - _ft_date).days <= 7:
                    # 기본 7일이 적용된 경우 → 1년으로 확장
                    _tt_date = datetime.now()
                    _ft_date = _tt_date - timedelta(days=365)
                    params["from_ts"] = _ft_date.strftime("%Y-%m-%d")
                    params["to_ts"] = _tt_date.strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                pass
        # fn_night_min_flow_summary는 = 비교이므로 '%%' 와일드카드 대신 '전체' 사용
        if params.get("sitename") == "%%":
            params["sitename"] = "전체"
        sql_combined = (
            "SELECT * FROM fn_night_min_flow_summary("
            "'{sitename}', '{facilitytype}', {from_ts}, {to_ts}"
            ") ORDER BY log_time ASC;"
        )
        # answer_template 오버라이드
        _site = params.get("sitename", "")
        _ftype = params.get("facilitytype", "소블록")
        answer_template = {
            "summary": "기간 설정이 없는 경우는 최근 1년 기준으로 1달 단위 데이터를 표출합니다.\n{sitename} {facilitytype} 야간최소유량 트렌드는 다음과 같습니다.",
            "detail": [
                {"prefix": "•", "text": "야간 최소유량은 60분 단위 이동평균 계산법을 적용하여 계산됩니다."}
            ],
            "recommend_questions": {
                "title": "다음은 추천 질의입니다.",
                "items": [
                    {"prefix": "1.", "text": f"{_site} {_ftype} 야간최소유량 트렌드 그래프를 보여줘"},
                    {"prefix": "2.", "text": f"{_site} {_ftype} 야간최소유량 표준편차분석을 통해 이상여부를 확인해줘"},
                    {"prefix": "3.", "text": f"{_site} {_ftype} 데이터 결측분석결과를 알려줘"},
                ]
            }
        }

    # FACILITY_TREND (일반): answer_template 오버라이드
    if intent == "FACILITY_TREND" and not _is_night_min_flow:
        _site = params.get("sitename", "")
        _ftype = params.get("facilitytype", "")
        _dinfo = params.get("datainfo", "")
        _user_period = params.get("user_specified_period", False)
        _ft = params.get("from_ts", "")
        _tt = params.get("to_ts", "")
        if _user_period:
            _period_line = f"{_ft} ~ {_tt} 기간의 데이터를 표출합니다."
        else:
            _period_line = "기간 설정이 없는 경우는 최근 7일간 데이터를 표출합니다."
        answer_template = {
            "summary": f"{_period_line}\n{_site} {_ftype} {_dinfo} 트렌드는 다음과 같습니다.",
            "recommend_questions": {
                "title": "다음은 추천질의입니다.",
                "items": [
                    {"prefix": "1.", "text": f"한달간 {_site} {_ftype} {_dinfo} 트렌드를 보여줘"},
                    {"prefix": "2.", "text": f"최근 3개월 {_site} {_ftype} {_dinfo} 트렌드를 보여줘"},
                    {"prefix": "3.", "text": f"{_site} {_ftype} {_dinfo} 트렌드 그래프를 보여줘"},
                ]
            }
        }

    # FACILITY_MIXED_TREND: answer_template 오버라이드
    if intent == "FACILITY_MIXED_TREND":
        _site = params.get("sitename", "")
        _ftype = params.get("facilitytype", "")
        _analog = params.get("analog_datainfo") or "유량"
        _digital = params.get("digital_datainfo") or "밸브"
        _user_period = params.get("user_specified_period", False)
        _ft = params.get("from_ts", "")
        _tt = params.get("to_ts", "")
        if _user_period:
            _period_line = f"{_ft} ~ {_tt} 기간의 데이터를 표출합니다."
        else:
            _period_line = "기간 설정이 없는 경우는 최근 7일간 데이터를 표출합니다."
        answer_template = {
            "summary": f"{_period_line}\n{_site} {_ftype}의 {_digital} 가동 상태와 {_analog} 데이터 트렌드는 다음과 같습니다.",
            "recommend_questions": {
                "title": "다음은 추천질의입니다.",
                "items": [
                    {"prefix": "1.", "text": f"한달간 {_site} {_ftype} {_digital} 가동상태와 {_analog}을 함께 트렌드로 보여줘"},
                    {"prefix": "2.", "text": f"최근 3개월 {_site} {_ftype} {_analog} 트렌드를 보여줘"},
                    {"prefix": "3.", "text": f"{_site} {_ftype} {_digital} 가동상태와 {_analog}을 함께 트렌드로 보여줘"},
                ]
            }
        }

    # NIGHT_MIN_FLOW_SUMMARY_TABLE: 기본 1년 기간 + answer_template 오버라이드
    if intent == "NIGHT_MIN_FLOW_SUMMARY_TABLE":
        _user_period = params.get("user_specified_period", False)
        if not _user_period:
            # 기본 기간: 1년 (기본 7일 대신 오버라이드)
            _tt_date = datetime.now()
            _ft_date = _tt_date - timedelta(days=365)
            params["from_ts"] = _ft_date.strftime("%Y-%m-%d")
            params["to_ts"] = _tt_date.strftime("%Y-%m-%d")
        # fn_night_min_flow_summary는 = 비교이므로 '%%' 와일드카드 대신 '전체' 사용
        _site = params.get("sitename", "")
        if _site == "%%":
            params["sitename"] = "전체"
            _site = "전체"
        _ftype = params.get("facilitytype", "소블록")
        _display_site = "전체" if _site == "%%" or _site == "전체" else _site
        _ft = params.get("from_ts", "")
        _tt = params.get("to_ts", "")
        if _user_period:
            _period_line = f"{_ft} ~ {_tt} 기간의 데이터를 표출합니다."
        else:
            _period_line = "기간 설정이 없는 경우는 최근 1년 기준으로 1달 단위 데이터를 표출합니다."
        _subject = f"{_display_site} {_ftype}" if _display_site != "전체" else _ftype
        # 추천질의용 대표 sitename
        _sample_site = _display_site
        if _display_site == "전체" and KNOWN_SITENAMES:
            _sample_site = KNOWN_SITENAMES[0]
        answer_template = {
            "summary": f"{_period_line} {_subject} 야간최소유량은 다음과 같습니다.",
            "detail": [
                {"prefix": "ㆍ", "text": "야간 최소유량은 60분 단위 이동평균 계산법을 적용하여 계산됩니다."}
            ],
            "recommend_questions": {
                "title": "다음은 추천질의입니다.",
                "items": [
                    {"prefix": "1.", "text": f"{_sample_site} {_ftype} 야간최소유량을 표로 보여줘"},
                    {"prefix": "2.", "text": f"전체 {_ftype} 야간최소유량을 표로 보여줘"},
                    {"prefix": "3.", "text": f"최근 한달간 {_ftype} 야간최소유량을 표로 보여줘"},
                ]
            }
        }

    # FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS: answer_template 오버라이드
    if intent == "FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS":
        _site = params.get("sitename", "")
        _ftype = params.get("facilitytype", "")
        answer_template = {
            "summary": f"{_site} {_ftype}의 야간최소유량 표준편차분석은 다음과 같습니다.",
            "detail": [
                {"prefix": "ㆍ", "text": f"현재 {_site} {_ftype} 소블록 야간최소유량과 한달 및 일년 표준편차분석 결과입니다."},
                {"prefix": "ㆍ", "text": "분석결과(표)"},
            ],
            "reference": {
                "title": "다음 참고자료입니다.",
                "items": [
                    {"prefix": "1.", "text": f"{_site} {_ftype} 소블록 평균 야간최소유량"},
                    {"prefix": "ㆍ", "text": "금월 야간최소유량 평균은 {avg_month}{unit}, 금년 야간최소유량 평균은 {avg_year}{unit} 입니다."},
                ]
            },
            "recommend_questions": {
                "title": "다음은 추천질의입니다.",
                "items": [
                    {"prefix": "1.", "text": f"{_site} {_ftype} 야간최소유량 트렌드 그래프를 보여줘"},
                    {"prefix": "2.", "text": f"{_site} {_ftype} 야간최소유량 표준편차분석을 통해 이상여부를 확인해줘"},
                    {"prefix": "3.", "text": f"{_site} {_ftype} 데이터 결측분석결과를 알려줘"},
                ]
            }
        }

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

    # ALARM_ABNORMAL_LOCATIONS: 경보 이상 발생 지점 (동적 필터)
    if intent == "ALARM_ABNORMAL_LOCATIONS":
        alarm_filter_clause, alarm_label = _extract_alarm_filter(user_question)
        alarm_level_clause, alarm_level_label = _extract_alarm_level(user_question)
        _ftype = params.get("facilitytype", "")

        where_parts = ["alarm_status = '진행중'"]
        if _ftype:
            _ftype_esc = _ftype.replace("'", "''")
            where_parts.append(f"facilitytype = '{_ftype_esc}'")
        where_base = " AND ".join(where_parts)
        if alarm_filter_clause:
            where_base += f" {alarm_filter_clause}"
        if alarm_level_clause:
            where_base += f" {alarm_level_clause}"

        sql_combined = (
            f"SELECT sitename, facilitytype, alarm_msg, alarm_category,"
            f" TO_CHAR(alarm_start_time, 'YYYY-MM-DD HH24:MI:SS') AS alarm_start_time,"
            f" alarm_status"
            f" FROM tb_equipment_alarm_report"
            f" WHERE {where_base}"
            f" ORDER BY alarm_start_time DESC"
            f" LIMIT 100;"
        )
        # 폴백용 필터 정보를 params에 저장
        params["_alarm_where_filter"] = alarm_filter_clause
        params["_alarm_where_level"] = alarm_level_clause
        params["_alarm_where_ftype"] = f"facilitytype = '{_ftype_esc}'" if _ftype else ""
        params["_alarm_label"] = alarm_label
        params["_alarm_level_label"] = alarm_level_label

        # answer_template 오버라이드 (빈 필터 placeholder 문제 방지)
        _filter_desc = " ".join(p for p in [_ftype, alarm_label, alarm_level_label] if p)
        _subject = f"{_filter_desc} 경보" if _filter_desc else "경보"
        answer_template = {
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

    # FACILITY_CATALOG_TREND_TABLE: 2단계 청크 직접 쿼리 (성능 최적화)
    if intent == "FACILITY_CATALOG_TREND_TABLE":
        _ft = params.get("facilitytype", "배수지")
        _sn = params.get("sitename", "%%")
        _di = params.get("datainfo", "")
        _from = params.get("from_ts", "")
        _to = params.get("to_ts", "")

        trend_name_filter, label_pattern, display_name = _get_catalog_trend_filter(user_question, _di)
        params["datainfo"] = display_name
        logger.info(f"FACILITY_CATALOG_TREND_TABLE SQL: ft={_ft}, sn={_sn}, tn={trend_name_filter}, lbl={label_pattern}")

        try:
            _cat_rows, _cat_cols = await asyncio.to_thread(
                _execute_catalog_trend_query,
                _ft, _sn, trend_name_filter, label_pattern, _from, _to,
            )
            if _cat_rows:
                rows = _cat_rows
                columns = _cat_cols
        except Exception as e:
            logger.error(f"FACILITY_CATALOG_TREND_TABLE 쿼리 실패: {e}")
        # sql_combined은 빈 상태 — 아래 execute_sql 단계를 건너뜀

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

    # 커스텀 핸들러에서 rows/columns가 이미 채워진 경우 SQL 실행 건너뜀
    if not rows:
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

    # 다중 sitename: 시설별 datainfo 페어링이 있으면 개별 SQL 실행
    if facility_pairs and len(facility_pairs) > 1:
        all_rows = list(rows) if rows else []
        # 첫 번째 시설은 이미 위에서 실행됨 — 단, 페어링된 datainfo와 불일치할 수 있으므로 재실행
        all_rows = []
        _TREND_SQL = "SELECT * FROM fn_trend_period_summary('{sitename}', '{facilitytype}', '{datainfo}', {from_ts}, {to_ts}) ORDER BY log_time ASC"
        for pair in facility_pairs:
            fp = dict(params)
            fp["sitename"] = pair["sitename"]
            if pair.get("facilitytype"):
                fp["facilitytype"] = pair["facilitytype"]
            elif SITENAME_FACILITY_MAP:
                ft_set = SITENAME_FACILITY_MAP.get(pair["sitename"])
                if ft_set and len(ft_set) == 1:
                    resolved = next(iter(ft_set))
                    fp["facilitytype"] = resolved
                    if resolved in ("소블록", "중블록", "대블록"):
                        fp["block_level"] = resolved

            # data_type에 따라 analog/digital/mixed SQL 실행
            sqls_to_run = []
            if pair["data_type"] in ("analog", "mixed") and pair.get("analog_datainfo"):
                p_a = dict(fp)
                p_a["datainfo"] = pair["analog_datainfo"]
                sqls_to_run.append((_TREND_SQL, p_a, "analog"))
            if pair["data_type"] in ("digital", "mixed") and pair.get("digital_datainfo"):
                p_d = dict(fp)
                p_d["datainfo"] = pair["digital_datainfo"]
                sqls_to_run.append((_TREND_SQL, p_d, "digital"))
            if not sqls_to_run:
                # fallback: 원본 SQL 그대로
                sqls_to_run.append((sql_combined, fp, "fallback"))

            for sql_i, params_i, label in sqls_to_run:
                try:
                    r, _ = await asyncio.to_thread(execute_sql, sql_i, params_i)
                    if r:
                        all_rows.extend(r)
                        logger.info(f"시설 '{pair['sitename']}' {label}: {len(r)}행")
                except Exception as e:
                    logger.warning(f"시설 '{pair['sitename']}' {label} SQL 실행 실패: {e}")

        rows = all_rows
        # 렌더링용 sitename 업데이트
        all_site_names = [p["sitename"] for p in facility_pairs]
        params["sitename"] = ", ".join(all_site_names)

    elif extra_sitenames:
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

    # ALARM_ABNORMAL_LOCATIONS: 진행중 0건 → 최근 7일 폴백
    if intent == "ALARM_ABNORMAL_LOCATIONS" and not rows:
        fb_where_parts = ["alarm_start_time >= NOW() - INTERVAL '7 days'"]
        _fb_ftype = params.get("_alarm_where_ftype", "")
        if _fb_ftype:
            fb_where_parts.append(_fb_ftype)
        fb_where = " AND ".join(fb_where_parts)
        _fb_filter = params.get("_alarm_where_filter", "")
        _fb_level = params.get("_alarm_where_level", "")
        if _fb_filter:
            fb_where += f" {_fb_filter}"
        if _fb_level:
            fb_where += f" {_fb_level}"
        fb_sql = (
            f"SELECT sitename, facilitytype, alarm_msg, alarm_category,"
            f" TO_CHAR(alarm_start_time, 'YYYY-MM-DD HH24:MI:SS') AS alarm_start_time,"
            f" alarm_status"
            f" FROM tb_equipment_alarm_report"
            f" WHERE {fb_where}"
            f" ORDER BY alarm_start_time DESC"
            f" LIMIT 100;"
        )
        try:
            rows, columns = await asyncio.to_thread(execute_sql, fb_sql, {})
            if rows:
                params["_alarm_fallback"] = True
                answer_template["summary"] = "현재 진행중인 해당 알람이 없어 최근 7일 이력을 표시합니다. ({total_alarm_count}건)"
                logger.info(f"ALARM_ABNORMAL_LOCATIONS 폴백: 최근 7일 {len(rows)}건")
        except Exception as e:
            logger.warning(f"ALARM_ABNORMAL_LOCATIONS 폴백 SQL 실행 실패: {e}")

    # 결과 확인
    if not rows:
        logger.info(f"조회 결과 없음: {intent}, params={params}")
        return build_no_data_response(intent, answer_template, params=params, session_id=sid)

    # FACILITY_ABNORMAL_STATUS_SUMMARY: SQL 실행 후 빈 문자열을 렌더링용 "전체"로 변환
    if intent == "FACILITY_ABNORMAL_STATUS_SUMMARY":
        if not params.get("datainfo"):
            params["datainfo"] = "전체"

    # total_count: 템플릿 {total_count} 렌더링용
    params["total_count"] = str(len(rows))

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
    rendered_answer = apply_corrections_to_answer(rendered_answer, params)

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
        _PLOT_TYPE_DEFAULTS = {"analog": "line", "digital": "step", "mixed": "multi_axis_line"}
        if _chart_data_type == "mixed":
            # mixed 데이터는 항상 듀얼 Y축 (analog+digital 혼합)
            _plot_type = "multi_axis_line"
        elif not _plot_type and _chart_data_type:
            _plot_type = _PLOT_TYPE_DEFAULTS.get(_chart_data_type, "line")

    # STDDEV 분석: stddev_stats 추출
    _stddev_stats = None
    if intent == "FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS" and response_data:
        _stddev_stats = _extract_stddev_stats(response_data[0])

    # CUSUM 누수추정: 응답 데이터를 CUSUM 요약 테이블로 교체 + cusum_chart_data
    _cusum_chart_data = None
    if intent == "LEAK_CUSUM_ANALYSIS" and processed_data.get("_cusum_results"):
        cusum_table_rows = processed_data["_cusum_table_rows"]
        cusum_table_cols = processed_data["_cusum_table_columns"]
        response_data = [dict(zip(cusum_table_cols, r)) for r in cusum_table_rows]
        table_columns = cusum_table_cols
        total_rows = len(cusum_table_rows)
        data_truncated = False
        # CUSUM 시계열 차트 데이터 (프론트에서 차트 렌더링에 사용)
        _cusum_chart_data = {}
        for tagsn, cr in processed_data["_cusum_results"].items():
            _cusum_chart_data[cr.get("label", tagsn)] = {
                "series": cr["cusum_series"],  # [(log_time, val, cusum)]
                "threshold_h": cr["threshold_h"],
                "baseline_mean": cr["baseline_mean"],
                "baseline_stddev": cr["baseline_stddev"],
                "leak_status": cr["leak_status"],
            }

    # 트렌드 이상구간 강조: Z-Score 기반 anomaly zones
    _anomaly_zones = None
    if (graph_type == "plot"
            and intent in ("FACILITY_TREND", "FACILITY_MIXED_TREND")
            and rows and columns):
        try:
            _anomaly_zones = compute_anomaly_zones(rows, columns, region, conn)
        except Exception as e:
            logger.warning(f"Anomaly zone computation failed: {e}")

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
        stddev_stats=_stddev_stats,
        cusum_chart_data=_cusum_chart_data,
        anomaly_zones=_anomaly_zones,
        intent_candidates=intent_candidates,
        site_group_distribution=processed_data.get("site_group_distribution"),
        site_group=processed_data.get("site_group"),
        pattern_analysis=processed_data.get("pattern_analysis"),
    )



# =============================================================================
# SSE 스트리밍 엔드포인트
# =============================================================================

def _sse_event(event: str, data: dict) -> str:
    """SSE 이벤트 문자열을 생성한다."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


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
        intent_candidates = []
        if request.force_intent:
            # 사후 보정: 프론트엔드에서 지정한 인텐트 강제 사용
            intent_name = request.force_intent
            intent_def = intent_index.get_definition(intent_name)
            category = intent_classifier._get_category_for_intent(intent_name) or "기타"
            classify_method = "force_intent"
            logger.info(f"[SSE] 강제 인텐트 지정: intent={intent_name}")
        elif is_correction and session.last_intent:
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
            intent_candidates = classification.get("intent_candidates", [])
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
        facility_pairs = new_params.pop("_facility_pairs", None)

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
        _TABLE_INTENTS_ALLOW_ALL_STREAM = {
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
            "ALARM_ABNORMAL_LOCATIONS",
            "FACILITY_CATALOG_TREND_TABLE",
        }
        if (intent_name in _TABLE_INTENTS_ALLOW_ALL_STREAM
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

        # 4.7.1. 위치도/계통도: facilitytype 자동 해소 후 인텐트 재매핑
        _LOCATION_REMAP_S = {
            "배수지": "RESERVOIR_LOCATION",
            "가압장": "BOOSTER_STATION_LOCATION",
            "감압시설": "PRESSURE_REDUCING_FACILITY_LOCATION",
            "소블록": "BLOCK_LOCATION", "중블록": "BLOCK_LOCATION", "대블록": "BLOCK_LOCATION",
        }
        _DIAGRAM_REMAP_S = {
            "배수지": "RESERVOIR_SYSTEM_DIAGRAM",
            "가압장": "BOOSTER_STATION_SYSTEM_DIAGRAM",
            "소블록": "BLOCK_SYSTEM_DIAGRAM", "중블록": "BLOCK_SYSTEM_DIAGRAM", "대블록": "BLOCK_SYSTEM_DIAGRAM",
        }
        _q_nospace_s = user_question.replace(" ", "")
        _resolved_ft_s = new_params.get("facilitytype") or new_params.get("block_level")
        if _resolved_ft_s:
            if "위치도" in _q_nospace_s and _resolved_ft_s in _LOCATION_REMAP_S:
                _new_intent_s = _LOCATION_REMAP_S[_resolved_ft_s]
                if intent_name != _new_intent_s:
                    logger.info(f"[SSE] 위치도 인텐트 재매핑: {intent_name} → {_new_intent_s} (ft={_resolved_ft_s})")
                    intent_name = _new_intent_s
                    intent_def = intent_index.get_definition(intent_name)
            elif "계통도" in _q_nospace_s and _resolved_ft_s in _DIAGRAM_REMAP_S:
                _new_intent_s = _DIAGRAM_REMAP_S[_resolved_ft_s]
                if intent_name != _new_intent_s:
                    logger.info(f"[SSE] 계통도 인텐트 재매핑: {intent_name} → {_new_intent_s} (ft={_resolved_ft_s})")
                    intent_name = _new_intent_s
                    intent_def = intent_index.get_definition(intent_name)

        # 4.8. FACILITY_TREND + 야간최소유량: sitename 미추출 시 '전체' 기본값
        _q_pre_check_s = user_question.replace(" ", "")
        if (intent_name == "FACILITY_TREND"
                and "야간최소유량" in _q_pre_check_s
                and not new_params.get("sitename")):
            new_params["sitename"] = "전체"
            logger.info("[SSE] 야간최소유량 sitename 미추출 → '전체' 기본값 적용")

        # 4.9. LEAK_CUSUM_ANALYSIS: 기본 파라미터 설정
        if intent_name == "LEAK_CUSUM_ANALYSIS":
            if not new_params.get("facilitytype"):
                new_params["facilitytype"] = "소블록"
            if not new_params.get("sitename"):
                new_params["sitename"] = "전체"
            if not new_params.get("from_ts"):
                new_params["from_ts"] = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
            if not new_params.get("to_ts"):
                new_params["to_ts"] = datetime.now().strftime("%Y-%m-%d")
            logger.info(f"[SSE] LEAK_CUSUM defaults: site={new_params['sitename']}, "
                         f"ft={new_params['facilitytype']}, "
                         f"from={new_params['from_ts']}, to={new_params['to_ts']}")

        # 4.10. ANOMALY 인텐트: 선택적 시설 필터 + 범위 라벨 설정
        if intent_name in _ANOMALY_FILTER_INTENTS:
            new_params["anomaly_facility_filter"] = build_anomaly_facility_filter(
                intent_name, new_params
            )
            new_params["anomaly_scope"] = build_anomaly_scope_label(new_params)
            logger.info(f"[SSE] ANOMALY filter: intent={intent_name}, "
                         f"scope={new_params['anomaly_scope']}, "
                         f"filter={new_params['anomaly_facility_filter']!r}")

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
                intent_candidates=intent_candidates,
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

        # 경보 순위 인텐트: sitename/facilitytype 선택적 + 카테고리 필터
        _ALARM_PIE_INTENTS_SSE = {"FACILITY_ALARM_CAUSE_DIAGNOSIS_RANK", "FACILITY_ALARM_TOP_COUNT"}
        if intent in _ALARM_PIE_INTENTS_SSE:
            if not params.get("sitename") or params.get("sitename") == "%%":
                params["sitename"] = ""
            if not params.get("facilitytype") or params.get("facilitytype") == "%%":
                params["facilitytype"] = ""
            # 경보 카테고리 필터 (수위/압력/통신/전원/펌프/밸브/수질/유량)
            clause, label = _extract_alarm_filter(request.user_question or "")
            params["alarm_filter_clause"] = clause
            params["alarm_label"] = label

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

        # 빈 SQL 체크 (동적 SQL 생성 인텐트는 커스텀 핸들러에서 sql_combined 설정)
        _DYNAMIC_SQL_INTENTS_STREAM = {"ALARM_ABNORMAL_LOCATIONS", "FACILITY_CATALOG_TREND_TABLE"}
        if intent not in _DYNAMIC_SQL_INTENTS_STREAM and (not sql_combined or not sql_combined.strip()):
            rendered_answer = render_answer_template(answer_template, params)
            rendered_answer = apply_corrections_to_answer(rendered_answer, params)
            yield _sse_event("result", build_success_response(
                intent=intent,
                answer=rendered_answer,
                graph_type=graph_type,
                session_id=sid,
            ))
            return

        # FACILITY_TREND + 야간최소유량: fn_night_min_flow_summary 사용
        _q_no_space = user_question.replace(" ", "")
        _is_night_min_flow = intent == "FACILITY_TREND" and "야간최소유량" in _q_no_space
        if _is_night_min_flow:
            # 기본 기간: 1년 (from_ts/to_ts가 기본 7일로 설정된 경우 오버라이드)
            _ft = params.get("from_ts", "")
            _tt = params.get("to_ts", "")
            if _ft and _tt:
                try:
                    _ft_date = datetime.strptime(_ft.strip("'"), "%Y-%m-%d")
                    _tt_date = datetime.strptime(_tt.strip("'"), "%Y-%m-%d")
                    if (_tt_date - _ft_date).days <= 7:
                        _tt_date = datetime.now()
                        _ft_date = _tt_date - timedelta(days=365)
                        params["from_ts"] = _ft_date.strftime("%Y-%m-%d")
                        params["to_ts"] = _tt_date.strftime("%Y-%m-%d")
                except (ValueError, TypeError):
                    pass
            # fn_night_min_flow_summary는 = 비교이므로 '%%' 와일드카드 대신 '전체' 사용
            if params.get("sitename") == "%%":
                params["sitename"] = "전체"
            sql_combined = (
                "SELECT * FROM fn_night_min_flow_summary("
                "'{sitename}', '{facilitytype}', {from_ts}, {to_ts}"
                ") ORDER BY log_time ASC;"
            )
            # answer_template 오버라이드
            _site = params.get("sitename", "")
            _ftype = params.get("facilitytype", "소블록")
            answer_template = {
                "summary": "기간 설정이 없는 경우는 최근 1년 기준으로 1달 단위 데이터를 표출합니다.\n{sitename} {facilitytype} 야간최소유량 트렌드는 다음과 같습니다.",
                "detail": [
                    {"prefix": "•", "text": "야간 최소유량은 60분 단위 이동평균 계산법을 적용하여 계산됩니다."}
                ],
                "recommend_questions": {
                    "title": "다음은 추천 질의입니다.",
                    "items": [
                        {"prefix": "1.", "text": f"{_site} {_ftype} 야간최소유량 트렌드 그래프를 보여줘"},
                        {"prefix": "2.", "text": f"{_site} {_ftype} 야간최소유량 표준편차분석을 통해 이상여부를 확인해줘"},
                        {"prefix": "3.", "text": f"{_site} {_ftype} 데이터 결측분석결과를 알려줘"},
                    ]
                }
            }

        # FACILITY_TREND (일반): answer_template 오버라이드
        if intent == "FACILITY_TREND" and not _is_night_min_flow:
            _site = params.get("sitename", "")
            _ftype = params.get("facilitytype", "")
            _dinfo = params.get("datainfo", "")
            _user_period = params.get("user_specified_period", False)
            _ft = params.get("from_ts", "")
            _tt = params.get("to_ts", "")
            if _user_period:
                _period_line = f"{_ft} ~ {_tt} 기간의 데이터를 표출합니다."
            else:
                _period_line = "기간 설정이 없는 경우는 최근 7일간 데이터를 표출합니다."
            answer_template = {
                "summary": f"{_period_line}\n{_site} {_ftype} {_dinfo} 트렌드는 다음과 같습니다.",
                "recommend_questions": {
                    "title": "다음은 추천질의입니다.",
                    "items": [
                        {"prefix": "1.", "text": f"한달간 {_site} {_ftype} {_dinfo} 트렌드를 보여줘"},
                        {"prefix": "2.", "text": f"최근 3개월 {_site} {_ftype} {_dinfo} 트렌드를 보여줘"},
                        {"prefix": "3.", "text": f"{_site} {_ftype} {_dinfo} 트렌드 그래프를 보여줘"},
                    ]
                }
            }

        # FACILITY_MIXED_TREND: answer_template 오버라이드
        if intent == "FACILITY_MIXED_TREND":
            _site = params.get("sitename", "")
            _ftype = params.get("facilitytype", "")
            _analog = params.get("analog_datainfo") or "유량"
            _digital = params.get("digital_datainfo") or "밸브"
            _user_period = params.get("user_specified_period", False)
            _ft = params.get("from_ts", "")
            _tt = params.get("to_ts", "")
            if _user_period:
                _period_line = f"{_ft} ~ {_tt} 기간의 데이터를 표출합니다."
            else:
                _period_line = "기간 설정이 없는 경우는 최근 7일간 데이터를 표출합니다."
            answer_template = {
                "summary": f"{_period_line}\n{_site} {_ftype}의 {_digital} 가동 상태와 {_analog} 데이터 트렌드는 다음과 같습니다.",
                "recommend_questions": {
                    "title": "다음은 추천질의입니다.",
                    "items": [
                        {"prefix": "1.", "text": f"한달간 {_site} {_ftype} {_digital} 가동상태와 {_analog}을 함께 트렌드로 보여줘"},
                        {"prefix": "2.", "text": f"최근 3개월 {_site} {_ftype} {_analog} 트렌드를 보여줘"},
                        {"prefix": "3.", "text": f"{_site} {_ftype} {_digital} 가동상태와 {_analog}을 함께 트렌드로 보여줘"},
                    ]
                }
            }

        # NIGHT_MIN_FLOW_SUMMARY_TABLE: 기본 1년 기간 + answer_template 오버라이드
        if intent == "NIGHT_MIN_FLOW_SUMMARY_TABLE":
            _user_period = params.get("user_specified_period", False)
            if not _user_period:
                _tt_date = datetime.now()
                _ft_date = _tt_date - timedelta(days=365)
                params["from_ts"] = _ft_date.strftime("%Y-%m-%d")
                params["to_ts"] = _tt_date.strftime("%Y-%m-%d")
            # fn_night_min_flow_summary는 = 비교이므로 '%%' 와일드카드 대신 '전체' 사용
            _site = params.get("sitename", "")
            if _site == "%%":
                params["sitename"] = "전체"
                _site = "전체"
            _ftype = params.get("facilitytype", "소블록")
            _display_site = "전체" if _site == "%%" or _site == "전체" else _site
            _ft = params.get("from_ts", "")
            _tt = params.get("to_ts", "")
            if _user_period:
                _period_line = f"{_ft} ~ {_tt} 기간의 데이터를 표출합니다."
            else:
                _period_line = "기간 설정이 없는 경우는 최근 1년 기준으로 1달 단위 데이터를 표출합니다."
            _subject = f"{_display_site} {_ftype}" if _display_site != "전체" else _ftype
            _sample_site = _display_site
            if _display_site == "전체" and KNOWN_SITENAMES:
                _sample_site = KNOWN_SITENAMES[0]
            answer_template = {
                "summary": f"{_period_line} {_subject} 야간최소유량은 다음과 같습니다.",
                "detail": [
                    {"prefix": "ㆍ", "text": "야간 최소유량은 60분 단위 이동평균 계산법을 적용하여 계산됩니다."}
                ],
                "recommend_questions": {
                    "title": "다음은 추천질의입니다.",
                    "items": [
                        {"prefix": "1.", "text": f"{_sample_site} {_ftype} 야간최소유량을 표로 보여줘"},
                        {"prefix": "2.", "text": f"전체 {_ftype} 야간최소유량을 표로 보여줘"},
                        {"prefix": "3.", "text": f"최근 한달간 {_ftype} 야간최소유량을 표로 보여줘"},
                    ]
                }
            }

        # FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS: answer_template 오버라이드
        if intent == "FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS":
            _site = params.get("sitename", "")
            _ftype = params.get("facilitytype", "")
            answer_template = {
                "summary": f"{_site} {_ftype}의 야간최소유량 표준편차분석은 다음과 같습니다.",
                "detail": [
                    {"prefix": "ㆍ", "text": f"현재 {_site} {_ftype} 소블록 야간최소유량과 한달 및 일년 표준편차분석 결과입니다."},
                    {"prefix": "ㆍ", "text": "분석결과(표)"},
                ],
                "reference": {
                    "title": "다음 참고자료입니다.",
                    "items": [
                        {"prefix": "1.", "text": f"{_site} {_ftype} 소블록 평균 야간최소유량"},
                        {"prefix": "ㆍ", "text": "금월 야간최소유량 평균은 {avg_month}{unit}, 금년 야간최소유량 평균은 {avg_year}{unit} 입니다."},
                    ]
                },
                "recommend_questions": {
                    "title": "다음은 추천질의입니다.",
                    "items": [
                        {"prefix": "1.", "text": f"{_site} {_ftype} 야간최소유량 트렌드 그래프를 보여줘"},
                        {"prefix": "2.", "text": f"{_site} {_ftype} 야간최소유량 표준편차분석을 통해 이상여부를 확인해줘"},
                        {"prefix": "3.", "text": f"{_site} {_ftype} 데이터 결측분석결과를 알려줘"},
                    ]
                }
            }

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

        # ALARM_ABNORMAL_LOCATIONS: 경보 이상 발생 지점 (동적 필터)
        if intent == "ALARM_ABNORMAL_LOCATIONS":
            alarm_filter_clause, alarm_label = _extract_alarm_filter(user_question)
            alarm_level_clause, alarm_level_label = _extract_alarm_level(user_question)
            _ftype = params.get("facilitytype", "")

            where_parts = ["alarm_status = '진행중'"]
            if _ftype:
                _ftype_esc = _ftype.replace("'", "''")
                where_parts.append(f"facilitytype = '{_ftype_esc}'")
            where_base = " AND ".join(where_parts)
            if alarm_filter_clause:
                where_base += f" {alarm_filter_clause}"
            if alarm_level_clause:
                where_base += f" {alarm_level_clause}"

            sql_combined = (
                f"SELECT sitename, facilitytype, alarm_msg, alarm_category,"
                f" TO_CHAR(alarm_start_time, 'YYYY-MM-DD HH24:MI:SS') AS alarm_start_time,"
                f" alarm_status"
                f" FROM tb_equipment_alarm_report"
                f" WHERE {where_base}"
                f" ORDER BY alarm_start_time DESC"
                f" LIMIT 100;"
            )
            # 폴백용 필터 정보를 params에 저장
            params["_alarm_where_filter"] = alarm_filter_clause
            params["_alarm_where_level"] = alarm_level_clause
            params["_alarm_where_ftype"] = f"facilitytype = '{_ftype_esc}'" if _ftype else ""
            params["_alarm_label"] = alarm_label
            params["_alarm_level_label"] = alarm_level_label

            # answer_template 오버라이드
            _filter_desc = " ".join(p for p in [_ftype, alarm_label, alarm_level_label] if p)
            _subject = f"{_filter_desc} 경보" if _filter_desc else "경보"
            answer_template = {
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

        # FACILITY_CATALOG_TREND_TABLE: 2단계 청크 직접 쿼리 (성능 최적화)
        if intent == "FACILITY_CATALOG_TREND_TABLE":
            _ft = params.get("facilitytype", "배수지")
            _sn = params.get("sitename", "%%")
            _di = params.get("datainfo", "")
            _from = params.get("from_ts", "")
            _to = params.get("to_ts", "")

            trend_name_filter, label_pattern, display_name = _get_catalog_trend_filter(user_question, _di)
            params["datainfo"] = display_name
            logger.info(f"FACILITY_CATALOG_TREND_TABLE SQL: ft={_ft}, sn={_sn}, tn={trend_name_filter}, lbl={label_pattern}")

            try:
                _cat_rows, _cat_cols = await asyncio.to_thread(
                    _execute_catalog_trend_query,
                    conn, _ft, _sn, trend_name_filter, label_pattern, _from, _to,
                )
                if _cat_rows:
                    rows = _cat_rows
                    columns = _cat_cols
            except Exception as e:
                logger.error(f"[SSE] FACILITY_CATALOG_TREND_TABLE 쿼리 실패: {e}")

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

        # 커스텀 핸들러에서 rows/columns가 이미 채워진 경우 SQL 실행 건너뜀
        if not rows:
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

        # 다중 sitename: 시설별 datainfo 페어링이 있으면 개별 SQL 실행
        if facility_pairs and len(facility_pairs) > 1:
            all_rows = []
            _TREND_SQL = "SELECT * FROM fn_trend_period_summary('{sitename}', '{facilitytype}', '{datainfo}', {from_ts}, {to_ts}) ORDER BY log_time ASC"
            for pair in facility_pairs:
                fp = dict(params)
                fp["sitename"] = pair["sitename"]
                if pair.get("facilitytype"):
                    fp["facilitytype"] = pair["facilitytype"]
                elif SITENAME_FACILITY_MAP:
                    ft_set = SITENAME_FACILITY_MAP.get(pair["sitename"])
                    if ft_set and len(ft_set) == 1:
                        resolved = next(iter(ft_set))
                        fp["facilitytype"] = resolved
                        if resolved in ("소블록", "중블록", "대블록"):
                            fp["block_level"] = resolved

                sqls_to_run = []
                if pair["data_type"] in ("analog", "mixed") and pair.get("analog_datainfo"):
                    p_a = dict(fp)
                    p_a["datainfo"] = pair["analog_datainfo"]
                    sqls_to_run.append((_TREND_SQL, p_a, "analog"))
                if pair["data_type"] in ("digital", "mixed") and pair.get("digital_datainfo"):
                    p_d = dict(fp)
                    p_d["datainfo"] = pair["digital_datainfo"]
                    sqls_to_run.append((_TREND_SQL, p_d, "digital"))
                if not sqls_to_run:
                    sqls_to_run.append((sql_combined, fp, "fallback"))

                for sql_i, params_i, label in sqls_to_run:
                    try:
                        r, _ = await asyncio.to_thread(execute_sql, sql_i, params_i)
                        if r:
                            all_rows.extend(r)
                            logger.info(f"[SSE] 시설 '{pair['sitename']}' {label}: {len(r)}행")
                    except Exception as e:
                        logger.warning(f"[SSE] 시설 '{pair['sitename']}' {label} SQL 실행 실패: {e}")

            rows = all_rows
            all_site_names = [p["sitename"] for p in facility_pairs]
            params["sitename"] = ", ".join(all_site_names)

        elif extra_sitenames:
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

        # ALARM_ABNORMAL_LOCATIONS: 진행중 0건 → 최근 7일 폴백
        if intent == "ALARM_ABNORMAL_LOCATIONS" and not rows:
            fb_where_parts = ["alarm_start_time >= NOW() - INTERVAL '7 days'"]
            _fb_ftype = params.get("_alarm_where_ftype", "")
            if _fb_ftype:
                fb_where_parts.append(_fb_ftype)
            fb_where = " AND ".join(fb_where_parts)
            _fb_filter = params.get("_alarm_where_filter", "")
            _fb_level = params.get("_alarm_where_level", "")
            if _fb_filter:
                fb_where += f" {_fb_filter}"
            if _fb_level:
                fb_where += f" {_fb_level}"
            fb_sql = (
                f"SELECT sitename, facilitytype, alarm_msg, alarm_category,"
                f" TO_CHAR(alarm_start_time, 'YYYY-MM-DD HH24:MI:SS') AS alarm_start_time,"
                f" alarm_status"
                f" FROM tb_equipment_alarm_report"
                f" WHERE {fb_where}"
                f" ORDER BY alarm_start_time DESC"
                f" LIMIT 100;"
            )
            try:
                rows, columns = await asyncio.to_thread(execute_sql, fb_sql, {})
                if rows:
                    params["_alarm_fallback"] = True
                    answer_template["summary"] = "현재 진행중인 해당 알람이 없어 최근 7일 이력을 표시합니다. ({total_alarm_count}건)"
                    logger.info(f"[SSE] ALARM_ABNORMAL_LOCATIONS 폴백: 최근 7일 {len(rows)}건")
            except Exception as e:
                logger.warning(f"[SSE] ALARM_ABNORMAL_LOCATIONS 폴백 SQL 실행 실패: {e}")

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

        # total_count: 템플릿 {total_count} 렌더링용
        params["total_count"] = str(len(rows))

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
        rendered_answer = apply_corrections_to_answer(rendered_answer, params)

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
            _PLOT_TYPE_DEFAULTS = {"analog": "line", "digital": "step", "mixed": "multi_axis_line"}
            if _chart_data_type == "mixed":
                # mixed 데이터는 항상 듀얼 Y축 (analog+digital 혼합)
                _plot_type = "multi_axis_line"
            elif not _plot_type and _chart_data_type:
                _plot_type = _PLOT_TYPE_DEFAULTS.get(_chart_data_type, "line")

        # STDDEV 분석: stddev_stats 추출
        _stddev_stats = None
        if intent == "FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS" and response_data:
            _stddev_stats = _extract_stddev_stats(response_data[0])

        # CUSUM 누수추정: 응답 데이터를 CUSUM 요약 테이블로 교체
        _cusum_chart_data = None
        if intent == "LEAK_CUSUM_ANALYSIS" and processed_data.get("_cusum_results"):
            cusum_table_rows = processed_data["_cusum_table_rows"]
            cusum_table_cols = processed_data["_cusum_table_columns"]
            response_data = [dict(zip(cusum_table_cols, r)) for r in cusum_table_rows]
            table_columns = cusum_table_cols
            total_rows = len(cusum_table_rows)
            data_truncated = False
            _cusum_chart_data = {}
            for tagsn, cr in processed_data["_cusum_results"].items():
                _cusum_chart_data[cr.get("label", tagsn)] = {
                    "series": cr["cusum_series"],
                    "threshold_h": cr["threshold_h"],
                    "baseline_mean": cr["baseline_mean"],
                    "baseline_stddev": cr["baseline_stddev"],
                    "leak_status": cr["leak_status"],
                }

        # 트렌드 이상구간 강조: Z-Score 기반 anomaly zones
        _anomaly_zones = None
        if (graph_type == "plot"
                and intent in ("FACILITY_TREND", "FACILITY_MIXED_TREND")
                and rows and columns):
            try:
                _anomaly_zones = compute_anomaly_zones(rows, columns, region, conn)
            except Exception as e:
                logger.warning(f"Anomaly zone computation failed (SSE): {e}")

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
            stddev_stats=_stddev_stats,
            cusum_chart_data=_cusum_chart_data,
            anomaly_zones=_anomaly_zones,
            intent_candidates=intent_candidates,
            site_group_distribution=processed_data.get("site_group_distribution"),
            site_group=processed_data.get("site_group"),
            pattern_analysis=processed_data.get("pattern_analysis"),
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
# 현장 프로파일 조회 엔드포인트
# =============================================================================

@app.get("/anomaly/profiles")
async def get_anomaly_profiles():
    """현재 현장 프로파일링 결과를 반환한다 (디버깅/모니터링용)."""
    profiles = site_profiler.profiles
    result = []
    for (sitename, ft), p in sorted(profiles.items()):
        result.append({
            "sitename": sitename,
            "facilitytype": ft,
            "site_group": p.get("site_group", "B"),
            "avg_outflow_7d": p.get("avg_outflow_7d"),
            "alarm_freq_30d": p.get("alarm_freq_30d", 0),
            "p95_level": p.get("p95_level"),
            "p05_level": p.get("p05_level"),
            "info_count_7d": p.get("info_count_7d", 0),
        })
    group_dist = {"A": 0, "B": 0, "C": 0, "D": 0}
    for p in profiles.values():
        g = p.get("site_group", "B")
        group_dist[g] = group_dist.get(g, 0) + 1
    return {
        "total": len(profiles),
        "group_distribution": group_dist,
        "profiles": result,
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
# 관리자 API: 시설 파일 관리 (위치도, 계통도, 초동대응 매뉴얼)
# =============================================================================

FACILITY_FILE_BASE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "web", "files", "facility"
)
FACILITY_FILE_ALLOWED_TYPES = {"site_photo", "system_diagram", "manual"}
FACILITY_FILE_MAX_SIZE = 10 * 1024 * 1024  # 10MB
FACILITY_FILE_ALLOWED_MIME = {
    "image/jpeg", "image/png", "image/webp", "image/svg+xml", "application/pdf",
}

# 시설 테이블 → URL 컬럼 매핑 (원격 DB 호환)
_FACILITY_TABLE_MAP = {
    "배수지": "tb_service_reservoir_info",
    "가압장": "tb_service_booster_station_info",
    "감압시설": "tb_pressure_reducing_facility_info",
    "블록": None,  # 블록은 별도 처리 (block_level)
}
_FILE_TYPE_TO_COLUMN = {
    "site_photo": "site_photo_url",
    "system_diagram": "system_diagram_url",
    "manual": "manual_url",
}


@app.post("/admin/facility-files/upload")
async def upload_facility_file(
    file: UploadFile = File(...),
    region: str = Form("R01"),
    sitename: str = Form(...),
    file_type: str = Form(...),
):
    """시설 파일 업로드 (위치도/계통도/매뉴얼)"""
    import pathlib
    import shutil
    import uuid

    # 유효성 검증
    if file_type not in FACILITY_FILE_ALLOWED_TYPES:
        return {"status": "ERROR", "message": f"허용되지 않는 파일 유형: {file_type}"}

    if file.content_type and file.content_type not in FACILITY_FILE_ALLOWED_MIME:
        return {"status": "ERROR", "message": f"허용되지 않는 MIME 타입: {file.content_type}"}

    # 파일 크기 확인 (읽어서 체크)
    contents = await file.read()
    if len(contents) > FACILITY_FILE_MAX_SIZE:
        return {"status": "ERROR", "message": "파일 크기가 10MB를 초과합니다."}

    # UUID 기반 저장 파일명
    ext = pathlib.Path(file.filename or "file").suffix.lower() or ".bin"
    stored_name = f"{uuid.uuid4().hex}{ext}"
    sub_dir = os.path.join(FACILITY_FILE_BASE_DIR, file_type)
    os.makedirs(sub_dir, exist_ok=True)
    file_path = os.path.join(sub_dir, stored_name)

    # 파일 저장
    try:
        with open(file_path, "wb") as f:
            f.write(contents)
    except OSError as e:
        logger.error(f"파일 저장 실패: {e}")
        return {"status": "ERROR", "message": "파일 저장에 실패했습니다."}

    file_url = f"/api/files/facility/{file_type}/{stored_name}"
    file_size = len(contents)

    # DB 저장
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
        conn.autocommit = False
        cur = conn.cursor()

        # 기존 파일 확인
        cur.execute(
            "SELECT ff.file_id, fs.stored_name, fs.file_url "
            "FROM tb_facility_file ff JOIN tb_file_storage fs ON ff.file_id = fs.file_id "
            "WHERE ff.region = %s AND ff.sitename = %s AND ff.file_type = %s",
            (region, sitename, file_type),
        )
        old_row = cur.fetchone()

        # tb_file_storage INSERT
        cur.execute(
            "INSERT INTO tb_file_storage "
            "(region, file_category, original_name, stored_name, file_path, file_url, mime_type, file_size, uploaded_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING file_id",
            (region, "facility", file.filename, stored_name,
             f"facility/{file_type}/{stored_name}", file_url,
             file.content_type, file_size, "admin"),
        )
        new_file_id = cur.fetchone()[0]

        # tb_facility_file UPSERT
        cur.execute(
            "INSERT INTO tb_facility_file (region, sitename, file_type, file_id) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (region, sitename, file_type) "
            "DO UPDATE SET file_id = EXCLUDED.file_id, updated_at = now()",
            (region, sitename, file_type, new_file_id),
        )

        # 시설 테이블 URL 컬럼 업데이트 (원격 DB 호환)
        col_name = _FILE_TYPE_TO_COLUMN.get(file_type)
        if col_name:
            _update_facility_url(cur, region, sitename, col_name, file_url)

        conn.commit()

        # 이전 파일 삭제 (디스크 + DB)
        if old_row:
            old_file_id, old_stored_name, _old_url = old_row
            try:
                cur.execute("DELETE FROM tb_file_storage WHERE file_id = %s", (old_file_id,))
                conn.commit()
            except Exception:
                pass
            old_path = os.path.join(FACILITY_FILE_BASE_DIR, file_type, old_stored_name)
            if os.path.exists(old_path):
                os.remove(old_path)

        cur.close()
        logger.info(f"시설 파일 업로드 완료: {sitename}/{file_type}/{stored_name}")
        return {
            "status": "OK",
            "facility_file_id": new_file_id,
            "file_url": file_url,
            "original_name": file.filename,
        }

    except psycopg2.Error as e:
        if conn:
            conn.rollback()
        logger.error(f"시설 파일 DB 저장 실패: {e}")
        # 롤백 시 파일도 삭제
        if os.path.exists(file_path):
            os.remove(file_path)
        return {"status": "ERROR", "message": "DB 저장에 실패했습니다."}
    finally:
        if conn:
            conn.close()


def _update_facility_url(cur, region: str, sitename: str, col_name: str, file_url: str):
    """시설 테이블의 URL 컬럼을 업데이트한다 (원격 DB 호환)."""
    tables = [
        "tb_service_reservoir_info",
        "tb_service_booster_station_info",
        "tb_pressure_reducing_facility_info",
        "tb_block_info",
    ]
    for table in tables:
        try:
            # 컬럼 존재 확인 후 업데이트
            cur.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = %s AND column_name = %s",
                (table, col_name),
            )
            if cur.fetchone():
                cur.execute(
                    f"UPDATE {table} SET {col_name} = %s WHERE sitename = %s",  # noqa: S608
                    (file_url, sitename),
                )
                if cur.rowcount > 0:
                    return
        except psycopg2.Error:
            continue


@app.get("/admin/facility-files")
async def list_facility_files(
    region: str = Query("R01"),
    sitename: str = Query(None),
):
    """시설별 파일 목록 조회"""
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
        cur = conn.cursor()
        if sitename:
            cur.execute(
                "SELECT ff.facility_file_id, ff.region, ff.sitename, ff.file_type, "
                "fs.file_url, fs.original_name, fs.mime_type, fs.file_size, ff.created_at "
                "FROM tb_facility_file ff "
                "JOIN tb_file_storage fs ON ff.file_id = fs.file_id "
                "WHERE ff.region = %s AND ff.sitename = %s "
                "ORDER BY ff.file_type",
                (region, sitename),
            )
        else:
            cur.execute(
                "SELECT ff.facility_file_id, ff.region, ff.sitename, ff.file_type, "
                "fs.file_url, fs.original_name, fs.mime_type, fs.file_size, ff.created_at "
                "FROM tb_facility_file ff "
                "JOIN tb_file_storage fs ON ff.file_id = fs.file_id "
                "WHERE ff.region = %s "
                "ORDER BY ff.sitename, ff.file_type",
                (region,),
            )
        cols = ["facility_file_id", "region", "sitename", "file_type",
                "file_url", "original_name", "mime_type", "file_size", "created_at"]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        # datetime → string
        for row in rows:
            if row["created_at"]:
                row["created_at"] = row["created_at"].isoformat()
        cur.close()
        return {"status": "OK", "data": rows}
    except psycopg2.Error as e:
        logger.error(f"시설 파일 목록 조회 실패: {e}")
        return {"status": "ERROR", "message": "조회에 실패했습니다.", "data": []}
    finally:
        if conn:
            conn.close()


@app.get("/admin/facilities-summary")
async def get_facilities_summary(region: str = Query("R01")):
    """전체 시설 목록 + 파일 등록 현황"""
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
        cur = conn.cursor()
        cur.execute("""
            WITH facilities AS (
                SELECT sitename, '배수지' AS facilitytype FROM tb_service_reservoir_info
                UNION ALL
                SELECT sitename, '가압장' FROM tb_service_booster_station_info
                UNION ALL
                SELECT sitename, '감압시설' FROM tb_pressure_reducing_facility_info
                UNION ALL
                SELECT sitename, '블록' FROM tb_block_info
            )
            SELECT
                f.sitename,
                f.facilitytype,
                bool_or(ff.file_type = 'site_photo')      AS has_site_photo,
                bool_or(ff.file_type = 'system_diagram')   AS has_system_diagram,
                bool_or(ff.file_type = 'manual')           AS has_manual
            FROM facilities f
            LEFT JOIN tb_facility_file ff
                ON f.sitename = ff.sitename
            GROUP BY f.sitename, f.facilitytype
            ORDER BY f.facilitytype, f.sitename
        """)
        cols = ["sitename", "facilitytype", "has_site_photo", "has_system_diagram", "has_manual"]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        # None → False, region 추가
        for row in rows:
            row["region"] = region
            for k in ["has_site_photo", "has_system_diagram", "has_manual"]:
                row[k] = bool(row[k])
        cur.close()
        return {"status": "OK", "data": rows}
    except psycopg2.Error as e:
        logger.error(f"시설 요약 조회 실패: {e}")
        return {"status": "ERROR", "message": "조회에 실패했습니다.", "data": []}
    finally:
        if conn:
            conn.close()


@app.delete("/admin/facility-files/{facility_file_id}")
async def delete_facility_file(facility_file_id: int):
    """시설 파일 링크 삭제"""
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
        conn.autocommit = False
        cur = conn.cursor()

        # 기존 정보 조회
        cur.execute(
            "SELECT ff.region, ff.sitename, ff.file_type, fs.stored_name, fs.file_id "
            "FROM tb_facility_file ff "
            "JOIN tb_file_storage fs ON ff.file_id = fs.file_id "
            "WHERE ff.facility_file_id = %s",
            (facility_file_id,),
        )
        row = cur.fetchone()
        if not row:
            return {"status": "ERROR", "message": "파일을 찾을 수 없습니다."}

        _region, _sitename, _file_type, stored_name, file_id = row

        # DB 삭제
        cur.execute("DELETE FROM tb_facility_file WHERE facility_file_id = %s", (facility_file_id,))
        cur.execute("DELETE FROM tb_file_storage WHERE file_id = %s", (file_id,))

        # 시설 테이블 URL 컬럼 초기화
        col_name = _FILE_TYPE_TO_COLUMN.get(_file_type)
        if col_name:
            _update_facility_url(cur, _region, _sitename, col_name, None)

        conn.commit()

        # 물리 파일 삭제
        file_path = os.path.join(FACILITY_FILE_BASE_DIR, _file_type, stored_name)
        if os.path.exists(file_path):
            os.remove(file_path)

        cur.close()
        logger.info(f"시설 파일 삭제: {_sitename}/{_file_type}/{stored_name}")
        return {"status": "OK", "message": "삭제되었습니다."}

    except psycopg2.Error as e:
        if conn:
            conn.rollback()
        logger.error(f"시설 파일 삭제 실패: {e}")
        return {"status": "ERROR", "message": "삭제에 실패했습니다."}
    finally:
        if conn:
            conn.close()


# =============================================================================
# 사이트 설정 API (관리자용)
# =============================================================================

@app.get("/admin/site-settings")
async def get_site_settings():
    """사이트 설정 조회 (랜딩 페이지 활성화 등)"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT comm_cd, use_yn FROM tb_comm_code "
            "WHERE region = 'R01' AND grp_cd = 'SITE_SETTING'"
        )
        rows = cur.fetchall()
        cur.close()

        settings = {}
        for comm_cd, use_yn in rows:
            if comm_cd == "LANDING_ENABLED":
                settings["landing_enabled"] = use_yn == "Y"

        # 레코드가 없으면 기본값
        if "landing_enabled" not in settings:
            settings["landing_enabled"] = True

        return settings

    except Exception as e:
        logger.error(f"사이트 설정 조회 실패: {e}")
        return {"landing_enabled": True}
    finally:
        if conn:
            conn.close()


@app.put("/admin/site-settings")
async def update_site_settings(request: Request):
    """사이트 설정 업데이트"""
    conn = None
    try:
        body = await request.json()
        conn = get_db_connection()
        cur = conn.cursor()

        if "landing_enabled" in body:
            use_yn = "Y" if body["landing_enabled"] else "N"
            # UPSERT: 있으면 UPDATE, 없으면 INSERT
            cur.execute(
                """
                INSERT INTO tb_comm_code (region, grp_cd, comm_cd, comm_nm, use_yn, create_dt)
                VALUES ('R01', 'SITE_SETTING', 'LANDING_ENABLED', '랜딩 페이지 활성화', %s, NOW())
                ON CONFLICT (region, grp_cd, comm_cd)
                DO UPDATE SET use_yn = %s, update_dt = NOW()
                """,
                (use_yn, use_yn),
            )
            conn.commit()

        cur.close()
        return {"status": "OK", "landing_enabled": body.get("landing_enabled", True)}

    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"사이트 설정 업데이트 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


# (기존 대시보드 API는 아래 /monitoring/dashboard로 이전됨)


@app.get("/monitoring/alarm-notifications")
async def get_alarm_notifications():
    """헤더 알람 벨용: 진행중 알람 건수 + 최근 5건"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT COUNT(*) FROM tb_equipment_alarm_report
            WHERE alarm_status = '진행중'
        """)
        ongoing_count = cur.fetchone()[0] or 0

        cur.execute("""
            SELECT ar.tagsn,
                   ar.alarm_start_time,
                   COALESCE(ti.sitename, '알 수 없음') AS sitename,
                   COALESCE(ti.facilitytype, '') AS facilitytype,
                   COALESCE(ar.alarm_severity, '정상') AS severity,
                   COALESCE(ar.alarm_msg, ar.alarm_category || ' 알람') AS message
            FROM tb_equipment_alarm_report ar
            LEFT JOIN tb_tag_info ti ON ar.tagsn = ti.tagsn
            WHERE ar.alarm_status = '진행중'
            ORDER BY ar.alarm_start_time DESC
            LIMIT 5
        """)
        cols = ["tagsn", "alarm_start_time", "sitename", "facilitytype", "severity", "message"]
        items = [dict(zip(cols, row)) for row in cur.fetchall()]
        for item in items:
            if item["alarm_start_time"]:
                item["alarm_start_time"] = item["alarm_start_time"].isoformat()

        cur.close()
        return {"status": "OK", "data": {"ongoingCount": ongoing_count, "items": items}}
    except psycopg2.Error as e:
        logger.error(f"알람 알림 조회 실패: {e}")
        return {"status": "OK", "data": {"ongoingCount": 0, "items": []}}
    finally:
        if conn:
            conn.close()


# =============================================================================
# 네트워크 모니터링 API
# =============================================================================


@app.get("/network/devices")
async def get_network_devices():
    """네트워크 장비 목록 + 최신 통신 상태 조회 (IP 보유 장비만)"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # 최신 check_time을 먼저 구한 뒤 해당 시간 데이터만 조인 (PK 인덱스 활용)
        cur.execute("""
            WITH latest_time AS (
                SELECT MAX(check_time) AS ct FROM tb_network_status
            )
            SELECT
                e.equipment_id,
                e.sitename,
                e.facilitytype,
                e.equipmenttype,
                n.ip_address,
                ns.is_alive,
                ns.status_code,
                ns.rtt_ms,
                ns.check_time,
                ns.error_message
            FROM tb_equipment_info e
            JOIN tb_network_info n ON e.equipment_id = n.equipment_id
            LEFT JOIN tb_network_status ns
                ON ns.equipment_id = e.equipment_id
                AND ns.check_time = (SELECT ct FROM latest_time)
            ORDER BY e.sitename, e.equipmenttype
        """)
        cols = [
            "equipment_id", "sitename", "facilitytype", "equipmenttype",
            "ip_address", "is_alive", "status_code", "rtt_ms",
            "check_time", "error_message",
        ]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        for row in rows:
            if row["check_time"]:
                row["check_time"] = row["check_time"].isoformat()
            if row["rtt_ms"] is not None:
                row["rtt_ms"] = float(row["rtt_ms"])
        cur.close()
        return {"status": "OK", "data": rows}
    except psycopg2.Error as e:
        logger.error(f"네트워크 장비 조회 실패: {e}")
        return {"status": "ERROR", "message": "조회에 실패했습니다.", "data": []}
    finally:
        if conn:
            conn.close()


@app.get("/network/topology")
async def get_network_topology():
    """토폴로지 그래프 데이터 (nodes + edges) 조회"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # 노드: 전체 장비 (최신 상태 포함, MAX(check_time) 기반 최적화)
        cur.execute("""
            WITH latest_time AS (
                SELECT MAX(check_time) AS ct FROM tb_network_status
            )
            SELECT
                e.equipment_id,
                COALESCE(e.equipmenttype, '') || ' (' || e.sitename || ')' AS name,
                e.facilitytype AS category,
                e.equipmenttype,
                e.sitename,
                n.ip_address,
                ns.status_code,
                (n.ip_address IS NOT NULL) AS has_ip,
                ns.rtt_ms,
                TO_CHAR(ns.check_time, 'HH24:MI:SS') AS check_time
            FROM tb_equipment_info e
            LEFT JOIN tb_network_info n ON e.equipment_id = n.equipment_id
            LEFT JOIN tb_network_status ns
                ON ns.equipment_id = e.equipment_id
                AND ns.check_time = (SELECT ct FROM latest_time)
            ORDER BY e.sitename, e.equipmenttype
        """)
        node_cols = [
            "id", "name", "category", "equipmenttype",
            "sitename", "ip_address", "status", "has_ip",
            "rtt_ms", "check_time",
        ]
        nodes = [dict(zip(node_cols, row)) for row in cur.fetchall()]
        for node in nodes:
            if node.get("rtt_ms") is not None:
                node["rtt_ms"] = float(node["rtt_ms"])

        # 엣지: 연결 관계
        cur.execute("""
            SELECT
                l.source_equipment_id AS source,
                l.target_equipment_id AS target,
                l.link_protocol,
                l.link_device_interface
            FROM tb_network_link l
            ORDER BY l.source_equipment_id
        """)
        edge_cols = ["source", "target", "link_protocol", "link_device_interface"]
        edges = [dict(zip(edge_cols, row)) for row in cur.fetchall()]

        cur.close()
        return {
            "status": "OK",
            "data": {"nodes": nodes, "edges": edges},
        }
    except psycopg2.Error as e:
        logger.error(f"네트워크 토폴로지 조회 실패: {e}")
        return {"status": "ERROR", "message": "조회에 실패했습니다.", "data": {"nodes": [], "edges": []}}
    finally:
        if conn:
            conn.close()


@app.get("/network/status/summary")
async def get_network_status_summary():
    """네트워크 통신 상태 요약 통계"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # MAX(check_time) 기반 최적화 — PK 인덱스 활용
        cur.execute("""
            WITH latest_time AS (
                SELECT MAX(check_time) AS ct FROM tb_network_status
            )
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE status_code = '정상') AS normal,
                COUNT(*) FILTER (WHERE status_code = '이상') AS error,
                COUNT(*) FILTER (WHERE status_code NOT IN ('정상', '이상') OR status_code IS NULL) AS warning,
                (SELECT ct FROM latest_time) AS last_check_time
            FROM tb_network_status
            WHERE check_time = (SELECT ct FROM latest_time)
        """)
        row = cur.fetchone()
        cur.close()
        result = {
            "total": row[0] or 0,
            "normal": row[1] or 0,
            "error": row[2] or 0,
            "warning": row[3] or 0,
            "lastCheckTime": row[4].isoformat() if row[4] else None,
        }
        return {"status": "OK", "data": result}
    except psycopg2.Error as e:
        logger.error(f"네트워크 상태 요약 조회 실패: {e}")
        return {"status": "ERROR", "message": "조회에 실패했습니다.", "data": {}}
    finally:
        if conn:
            conn.close()


# =============================================================================
# 네트워크 관리 CRUD API (구축 > 네트워크 관리)
# =============================================================================

@app.get("/network/infos")
async def get_network_infos(
    page: int = 1, page_size: int = 50,
    sitename: str = "", facilitytype: str = "",
    equipmenttype: str = "", keyword: str = "",
):
    """네트워크 장비 목록 (tb_network_info + tb_equipment_info JOIN)"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        where, params = ["1=1"], []
        if sitename:
            where.append("e.sitename = %s"); params.append(sitename)
        if facilitytype:
            where.append("e.facilitytype = %s"); params.append(facilitytype)
        if equipmenttype:
            where.append("e.equipmenttype = %s"); params.append(equipmenttype)
        if keyword:
            where.append("(n.equipment_id ILIKE %s OR n.ip_address ILIKE %s OR e.sitename ILIKE %s)")
            kw = f"%{keyword}%"; params.extend([kw, kw, kw])
        w = " AND ".join(where)
        cur.execute(f"SELECT COUNT(*) FROM tb_network_info n JOIN tb_equipment_info e ON n.equipment_id = e.equipment_id WHERE {w}", params)
        total = cur.fetchone()[0]
        offset = (page - 1) * page_size
        cur.execute(f"""
            SELECT n.equipment_id, n.ip_address, n.description, n.meta,
                   n.created_at, n.updated_at,
                   e.sitename, e.facilitytype, e.equipmenttype
            FROM tb_network_info n
            JOIN tb_equipment_info e ON n.equipment_id = e.equipment_id
            WHERE {w}
            ORDER BY e.sitename, e.facilitytype, n.equipment_id
            LIMIT %s OFFSET %s
        """, params + [page_size, offset])
        rows = cur.fetchall()
        cur.close()
        data = []
        for r in rows:
            data.append({
                "equipment_id": r[0], "ip_address": r[1], "description": r[2],
                "meta": r[3] or {},
                "created_at": r[4].isoformat() if r[4] else None,
                "updated_at": r[5].isoformat() if r[5] else None,
                "sitename": r[6], "facilitytype": r[7], "equipmenttype": r[8],
            })
        return {"status": "OK", "data": data, "total": total, "page": page, "page_size": page_size}
    except Exception as e:
        logger.error(f"네트워크 장비 목록 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn: conn.close()


@app.get("/network/infos/filters")
async def get_network_info_filters():
    """네트워크 장비 필터 옵션"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT e.sitename FROM tb_network_info n
            JOIN tb_equipment_info e ON n.equipment_id = e.equipment_id ORDER BY 1
        """)
        sitenames = [r[0] for r in cur.fetchall()]
        cur.execute("""
            SELECT DISTINCT e.facilitytype FROM tb_network_info n
            JOIN tb_equipment_info e ON n.equipment_id = e.equipment_id ORDER BY 1
        """)
        facilitytypes = [r[0] for r in cur.fetchall()]
        cur.execute("""
            SELECT DISTINCT e.equipmenttype FROM tb_network_info n
            JOIN tb_equipment_info e ON n.equipment_id = e.equipment_id ORDER BY 1
        """)
        equipmenttypes = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT DISTINCT COALESCE(meta->>'network_role', '미지정') FROM tb_network_info ORDER BY 1")
        roles = [r[0] for r in cur.fetchall()]
        cur.close()
        return {"status": "OK", "data": {
            "sitenames": sitenames, "facilitytypes": facilitytypes,
            "equipmenttypes": equipmenttypes, "roles": roles,
        }}
    except Exception as e:
        logger.error(f"네트워크 필터 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn: conn.close()


@app.post("/network/infos")
async def create_network_info(req: dict = Body(...)):
    """네트워크 장비 추가 (equipment_id 필수, tb_equipment_info에 존재해야 함)"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO tb_network_info (equipment_id, ip_address, description, meta)
            VALUES (%s, %s, %s, %s::jsonb)
        """, [req["equipment_id"], req.get("ip_address"), req.get("description"),
              json.dumps(req.get("meta", {}))])
        conn.commit()
        cur.close()
        return {"status": "OK", "equipment_id": req["equipment_id"]}
    except Exception as e:
        if conn: conn.rollback()
        logger.error(f"네트워크 장비 추가 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn: conn.close()


@app.put("/network/infos/{equipment_id}")
async def update_network_info(equipment_id: str, req: dict = Body(...)):
    """네트워크 장비 수정"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE tb_network_info SET ip_address=%s, description=%s, meta=%s::jsonb
            WHERE equipment_id=%s
        """, [req.get("ip_address"), req.get("description"),
              json.dumps(req.get("meta", {})), equipment_id])
        conn.commit()
        affected = cur.rowcount
        cur.close()
        if affected == 0:
            return {"status": "ERROR", "message": "해당 장비를 찾을 수 없습니다."}
        return {"status": "OK"}
    except Exception as e:
        if conn: conn.rollback()
        logger.error(f"네트워크 장비 수정 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn: conn.close()


@app.delete("/network/infos/{equipment_id}")
async def delete_network_info(equipment_id: str):
    """네트워크 장비 삭제 (tb_network_link CASCADE 삭제)"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM tb_network_info WHERE equipment_id=%s", [equipment_id])
        conn.commit()
        affected = cur.rowcount
        cur.close()
        if affected == 0:
            return {"status": "ERROR", "message": "해당 장비를 찾을 수 없습니다."}
        return {"status": "OK"}
    except Exception as e:
        if conn: conn.rollback()
        logger.error(f"네트워크 장비 삭제 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn: conn.close()


@app.get("/network/links")
async def get_network_links(
    page: int = 1, page_size: int = 50,
    protocol: str = "", keyword: str = "",
):
    """네트워크 연결 목록 (tb_network_link + 양쪽 장비 JOIN)"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        where, params = ["1=1"], []
        if protocol:
            where.append("l.link_protocol = %s"); params.append(protocol)
        if keyword:
            where.append("""(l.source_equipment_id ILIKE %s OR l.target_equipment_id ILIKE %s
                OR l.link_role ILIKE %s
                OR COALESCE(se.sitename,'') ILIKE %s OR COALESCE(te.sitename,'') ILIKE %s)""")
            kw = f"%{keyword}%"; params.extend([kw, kw, kw, kw, kw])
        w = " AND ".join(where)
        cur.execute(f"""SELECT COUNT(*) FROM tb_network_link l
            LEFT JOIN tb_equipment_info se ON l.source_equipment_id = se.equipment_id
            LEFT JOIN tb_equipment_info te ON l.target_equipment_id = te.equipment_id
            WHERE {w}""", params)
        total = cur.fetchone()[0]
        offset = (page - 1) * page_size
        cur.execute(f"""
            SELECT l.source_equipment_id, l.target_equipment_id,
                   l.link_protocol, l.link_port, l.link_device_interface,
                   l.link_role, l.description, l.meta,
                   COALESCE(se.sitename,'') || ' ' || COALESCE(se.equipmenttype,'') AS source_name,
                   COALESCE(te.sitename,'') || ' ' || COALESCE(te.equipmenttype,'') AS target_name
            FROM tb_network_link l
            LEFT JOIN tb_equipment_info se ON l.source_equipment_id = se.equipment_id
            LEFT JOIN tb_equipment_info te ON l.target_equipment_id = te.equipment_id
            WHERE {w}
            ORDER BY l.source_equipment_id, l.target_equipment_id
            LIMIT %s OFFSET %s
        """, params + [page_size, offset])
        rows = cur.fetchall()
        cur.close()
        data = []
        for r in rows:
            data.append({
                "source_equipment_id": r[0], "target_equipment_id": r[1],
                "link_protocol": r[2], "link_port": r[3],
                "link_device_interface": r[4], "link_role": r[5],
                "description": r[6], "meta": r[7] or {},
                "source_name": r[8], "target_name": r[9],
            })
        return {"status": "OK", "data": data, "total": total, "page": page, "page_size": page_size}
    except Exception as e:
        logger.error(f"네트워크 연결 목록 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn: conn.close()


@app.get("/network/links/protocols")
async def get_link_protocols():
    """프로토콜 마스터 목록 (tb_protocol_lookup)"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT protocol_code, display_name, protocol_type, description FROM tb_protocol_lookup ORDER BY display_name")
        rows = cur.fetchall()
        cur.close()
        return {"status": "OK", "data": [
            {"protocol_code": r[0], "display_name": r[1], "protocol_type": r[2], "description": r[3]}
            for r in rows
        ]}
    except Exception as e:
        logger.error(f"프로토콜 목록 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn: conn.close()


@app.post("/network/links")
async def create_network_link(req: dict = Body(...)):
    """네트워크 연결 추가"""
    conn = None
    try:
        src = req.get("source_equipment_id", "").strip()
        tgt = req.get("target_equipment_id", "").strip()
        proto = req.get("link_protocol", "").strip()
        iface = req.get("link_device_interface", "").strip()
        if not src or not tgt or not proto or not iface:
            return {"status": "ERROR", "message": "출발장비, 도착장비, 프로토콜, 인터페이스는 필수입니다."}
        if src == tgt:
            return {"status": "ERROR", "message": "출발장비와 도착장비가 동일합니다."}
        conn = get_db_connection()
        cur = conn.cursor()
        # FK 검증
        cur.execute("SELECT equipment_id FROM tb_equipment_info WHERE equipment_id IN (%s, %s)", [src, tgt])
        found = {r[0] for r in cur.fetchall()}
        if src not in found:
            return {"status": "ERROR", "message": f"출발장비 '{src}'가 설비 마스터에 없습니다."}
        if tgt not in found:
            return {"status": "ERROR", "message": f"도착장비 '{tgt}'가 설비 마스터에 없습니다."}
        port = req.get("link_port")
        role = req.get("link_role", "").strip() or None
        desc = req.get("description", "").strip() or None
        meta = req.get("meta") or {}
        cur.execute("""
            INSERT INTO tb_network_link
                (source_equipment_id, target_equipment_id, link_protocol, link_device_interface,
                 link_port, link_role, description, meta)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """, [src, tgt, proto, iface, port, role, desc, json.dumps(meta)])
        conn.commit()
        cur.close()
        return {"status": "OK"}
    except Exception as e:
        if conn: conn.rollback()
        msg = str(e)
        if "duplicate key" in msg or "already exists" in msg:
            return {"status": "ERROR", "message": "이미 동일한 연결이 존재합니다."}
        logger.error(f"네트워크 연결 추가 실패: {e}")
        return {"status": "ERROR", "message": msg}
    finally:
        if conn: conn.close()


@app.put("/network/links/{source}/{target}")
async def update_network_link(source: str, target: str, req: dict = Body(...)):
    """네트워크 연결 수정 (PK 변경 불가)"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        sets, vals = [], []
        for col in ["link_protocol", "link_device_interface", "link_port", "link_role", "description"]:
            if col in req:
                sets.append(f"{col} = %s")
                v = req[col]
                vals.append(v.strip() if isinstance(v, str) else v)
        if "meta" in req:
            sets.append("meta = %s::jsonb")
            vals.append(json.dumps(req["meta"] or {}))
        if not sets:
            return {"status": "ERROR", "message": "수정할 항목이 없습니다."}
        sets.append("updated_at = NOW()")
        vals.extend([source, target])
        cur.execute(f"""
            UPDATE tb_network_link SET {', '.join(sets)}
            WHERE source_equipment_id = %s AND target_equipment_id = %s
        """, vals)
        if cur.rowcount == 0:
            return {"status": "ERROR", "message": "해당 연결을 찾을 수 없습니다."}
        conn.commit()
        cur.close()
        return {"status": "OK"}
    except Exception as e:
        if conn: conn.rollback()
        logger.error(f"네트워크 연결 수정 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn: conn.close()


@app.delete("/network/links/{source}/{target}")
async def delete_network_link(source: str, target: str):
    """네트워크 연결 삭제"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            DELETE FROM tb_network_link
            WHERE source_equipment_id = %s AND target_equipment_id = %s
        """, [source, target])
        if cur.rowcount == 0:
            return {"status": "ERROR", "message": "해당 연결을 찾을 수 없습니다."}
        conn.commit()
        cur.close()
        return {"status": "OK"}
    except Exception as e:
        if conn: conn.rollback()
        logger.error(f"네트워크 연결 삭제 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn: conn.close()


@app.get("/network/links/equipment-search")
async def search_link_equipment(q: str = ""):
    """연결 폼 장비 검색용 자동완성 (tb_equipment_info)"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        if q.strip():
            kw = f"%{q.strip()}%"
            cur.execute("""
                SELECT equipment_id, sitename, facilitytype, equipmenttype
                FROM tb_equipment_info
                WHERE equipment_id ILIKE %s OR sitename ILIKE %s OR equipmenttype ILIKE %s
                ORDER BY sitename, equipmenttype, equipment_id
                LIMIT 30
            """, [kw, kw, kw])
        else:
            cur.execute("""
                SELECT equipment_id, sitename, facilitytype, equipmenttype
                FROM tb_equipment_info
                ORDER BY sitename, equipmenttype, equipment_id
                LIMIT 30
            """)
        rows = cur.fetchall()
        cur.close()
        return {"status": "OK", "data": [
            {"equipment_id": r[0], "sitename": r[1], "facilitytype": r[2], "equipmenttype": r[3]}
            for r in rows
        ]}
    except Exception as e:
        logger.error(f"장비 검색 실패: {e}")
        return {"status": "ERROR", "data": []}
    finally:
        if conn: conn.close()


# =============================================================================
# 자동완성 후보 API
# =============================================================================

@app.get("/autocomplete/candidates")
async def get_autocomplete_candidates():
    """자동완성 후보 목록 반환 (현장명, 시설유형, 데이터항목, 블록구분, 질의 템플릿)"""
    from param_extractor import _FACILITYTYPE_CANDIDATES, _DATAINFO_CANDIDATES
    from intent_index import INTENT_DESCRIPTIONS

    facility_map = {
        site: sorted(ftypes)
        for site, ftypes in SITENAME_FACILITY_MAP.items()
    }

    # 질의 템플릿: example3.json의 질문 목록을 인텐트별로 추출
    query_templates = []
    for intent_def in INTENT_DEFINITIONS:
        intent_name = intent_def.get("intent", "")
        questions = intent_def.get("questions", [])
        if not intent_name or not questions:
            continue
        desc = INTENT_DESCRIPTIONS.get(intent_name, intent_name)
        for q in questions:
            query_templates.append({
                "text": q,
                "intent": intent_name,
                "description": desc,
            })

    return {
        "status": "OK",
        "data": {
            "sitenames": KNOWN_SITENAMES,
            "facility_types": _FACILITYTYPE_CANDIDATES,
            "data_info": _DATAINFO_CANDIDATES,
            "block_levels": KNOWN_BLOCK_LEVELS,
            "facility_map": facility_map,
            "query_templates": query_templates,
        },
    }


# =============================================================================
# 위기대응 (경보) API
# =============================================================================

@app.get("/crisis/alarm-reports")
async def get_alarm_reports(
    date_from: str = "",
    date_to: str = "",
    sitename: str = "",
    alarm_status: str = "",
    alarm_severity: str = "",
    alarm_category: str = "",
):
    """경보발생이력 목록 조회 (tb_equipment_alarm_report — 직접 컬럼)"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        conditions = []
        params_list: list = []

        if date_from:
            conditions.append("alarm_start_time >= %s::timestamp")
            params_list.append(f"{date_from} 00:00:00")
        if date_to:
            conditions.append("alarm_start_time <= %s::timestamp")
            params_list.append(f"{date_to} 23:59:59")
        if sitename:
            conditions.append("sitename LIKE %s")
            params_list.append(f"%{sitename}%")
        if alarm_status:
            conditions.append("alarm_status = %s")
            params_list.append(alarm_status)
        if alarm_severity:
            conditions.append("alarm_severity = %s")
            params_list.append(alarm_severity)
        if alarm_category:
            conditions.append("alarm_category = %s")
            params_list.append(alarm_category)

        where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        sql = f"""
            SELECT
                TO_CHAR(alarm_start_time, 'YYYY-MM-DD HH24:MI:SS') AS alarm_start_time,
                TO_CHAR(alarm_end_time, 'YYYY-MM-DD HH24:MI:SS') AS alarm_end_time,
                tagsn,
                COALESCE(sitename, '') AS sitename,
                COALESCE(facilitytype, '') AS facilitytype,
                COALESCE(equipmenttype, '') AS equipmenttype,
                COALESCE(equipment_id, '') AS equipment_id,
                alarm_category,
                alarm_msg,
                alarm_value,
                alarm_status,
                alarm_severity,
                diagnosed_cause,
                action_plan,
                user_cause_description,
                meta,
                COALESCE(alarm_confirm_yn, 'N') AS alarm_confirm_yn,
                countermeasure,
                COALESCE(off_alarm_confirm_yn, 'N') AS off_alarm_confirm_yn,
                is_false_alarm,
                false_alarm_notes,
                info_updated,
                COALESCE(tagtype, '') AS tagtype,
                stat
            FROM tb_equipment_alarm_report
            {where_clause}
            ORDER BY alarm_start_time DESC
            LIMIT 500
        """
        cur.execute(sql, params_list)
        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        cur.close()

        results = []
        for row in rows:
            rec = {}
            for i, col in enumerate(columns):
                val = row[i]
                if col == "meta" and val is not None:
                    rec[col] = val if isinstance(val, dict) else {}
                else:
                    rec[col] = val
            results.append(rec)

        return results

    except psycopg2.Error as e:
        logger.error(f"경보발생이력 조회 실패: {e}")
        return []
    finally:
        if conn:
            conn.close()


@app.get("/crisis/alarm-analysis")
async def get_alarm_analysis():
    """경보분석용 알람 목록 (diagnosed_msg 포함, 최근 30일)"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT
                TO_CHAR(alarm_start_time, 'YYYY-MM-DD HH24:MI:SS') AS alarm_start_time,
                TO_CHAR(alarm_end_time, 'YYYY-MM-DD HH24:MI:SS') AS alarm_end_time,
                tagsn,
                COALESCE(sitename, '') AS sitename,
                COALESCE(facilitytype, '') AS facilitytype,
                COALESCE(equipmenttype, '') AS equipmenttype,
                COALESCE(equipment_id, '') AS equipment_id,
                alarm_category,
                alarm_msg,
                alarm_value,
                alarm_status,
                alarm_severity,
                diagnosed_cause,
                action_plan,
                user_cause_description,
                meta,
                COALESCE(alarm_confirm_yn, 'N') AS alarm_confirm_yn,
                countermeasure,
                COALESCE(off_alarm_confirm_yn, 'N') AS off_alarm_confirm_yn,
                is_false_alarm,
                false_alarm_notes,
                info_updated,
                COALESCE(tagtype, '') AS tagtype,
                stat,
                diagnosed_msg
            FROM tb_equipment_alarm_report
            WHERE alarm_start_time >= NOW() - INTERVAL '30 days'
            ORDER BY alarm_start_time DESC
            LIMIT 500
        """)
        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        cur.close()

        results = []
        for row in rows:
            rec = {}
            for i, col in enumerate(columns):
                val = row[i]
                if col == "meta" and val is not None:
                    rec[col] = val if isinstance(val, dict) else {}
                else:
                    rec[col] = val
            results.append(rec)

        return results

    except psycopg2.Error as e:
        logger.error(f"경보분석 조회 실패: {e}")
        return []
    finally:
        if conn:
            conn.close()


@app.get("/crisis/alarm-dashboard")
async def get_alarm_dashboard_summary():
    """경보관리현황 대시보드 요약 (진행중 알람 집계)"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # 진행중 알람 요약
        cur.execute("""
            SELECT
                COUNT(*) AS total_ongoing,
                COUNT(*) FILTER (WHERE alarm_severity = '경고') AS critical_cnt,
                COUNT(*) FILTER (WHERE alarm_severity = '주의') AS warning_cnt,
                COUNT(*) FILTER (WHERE alarm_severity = '정상' OR alarm_severity IS NULL) AS caution_cnt
            FROM tb_equipment_alarm_report
            WHERE alarm_status = '진행중'
        """)
        r = cur.fetchone()

        # 카테고리별 집계
        cur.execute("""
            SELECT COALESCE(alarm_category, '기타') AS category, COUNT(*) AS cnt
            FROM tb_equipment_alarm_report
            WHERE alarm_status = '진행중'
            GROUP BY alarm_category
            ORDER BY cnt DESC
        """)
        category_summary = [
            {"category": row[0], "count": row[1]}
            for row in cur.fetchall()
        ]

        # 시설별 집계 (tb_equipment_alarm_report에 sitename/facilitytype 직접 존재)
        cur.execute("""
            SELECT COALESCE(sitename, '알 수 없음') AS sitename,
                   COALESCE(facilitytype, '') AS facilitytype,
                   COUNT(*) AS cnt
            FROM tb_equipment_alarm_report
            WHERE alarm_status = '진행중'
            GROUP BY sitename, facilitytype
            ORDER BY cnt DESC
        """)
        facility_summary = [
            {"sitename": row[0], "facilitytype": row[1], "count": row[2]}
            for row in cur.fetchall()
        ]

        cur.close()

        return {
            "totalOngoing": r[0] or 0,
            "criticalCount": r[1] or 0,
            "warningCount": r[2] or 0,
            "cautionCount": r[3] or 0,
            "categorySummary": category_summary,
            "facilitySummary": facility_summary,
        }

    except psycopg2.Error as e:
        logger.error(f"경보관리현황 요약 조회 실패: {e}")
        return {
            "totalOngoing": 0, "criticalCount": 0, "warningCount": 0, "cautionCount": 0,
            "categorySummary": [], "facilitySummary": [],
        }
    finally:
        if conn:
            conn.close()


@app.put("/crisis/alarm-reports/confirm")
async def confirm_alarm_report_api(request: Request):
    """경보 확인 처리 (alarm_confirm_yn = 'Y')"""
    conn = None
    try:
        body = await request.json()
        tagsn = body.get("tagsn", "")
        alarm_start_time = body.get("alarm_start_time", "")
        if not tagsn or not alarm_start_time:
            return {"status": "error", "message": "tagsn, alarm_start_time 필수"}

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE tb_equipment_alarm_report
            SET alarm_confirm_yn = 'Y', info_updated = TO_CHAR(NOW(), 'YYYY-MM-DD HH24:MI:SS')
            WHERE tagsn = %s AND alarm_start_time = %s::timestamp
        """, [tagsn, alarm_start_time])
        conn.commit()
        cur.close()
        return {"status": "OK"}
    except psycopg2.Error as e:
        logger.error(f"경보 확인 처리 실패: {e}")
        if conn:
            conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        if conn:
            conn.close()


# =============================================================================
# 태그 마스터 API
# =============================================================================

@app.get("/tags")
async def get_tags(
    sitename: str = Query("", description="현장명 필터"),
    facilitytype: str = Query("", description="시설유형 필터"),
    tagtype: str = Query("", description="태그유형 필터"),
    keyword: str = Query("", description="태그SN/설명 검색"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """tb_tag_info 태그 마스터 목록 조회 (페이징+필터)"""
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
        cur = conn.cursor()

        where_clauses = []
        params: list = []

        if sitename:
            where_clauses.append("sitename = %s")
            params.append(sitename)
        if facilitytype:
            where_clauses.append("facilitytype = %s")
            params.append(facilitytype)
        if tagtype:
            where_clauses.append("tagtype = %s")
            params.append(tagtype)
        if keyword:
            where_clauses.append("(tagsn ILIKE %s OR datadesc ILIKE %s OR datainfo ILIKE %s)")
            kw = f"%{keyword}%"
            params.extend([kw, kw, kw])

        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        # 전체 건수
        cur.execute(f"SELECT count(*) FROM tb_tag_info{where_sql}", params)
        total = cur.fetchone()[0]

        # 페이징 데이터
        offset = (page - 1) * page_size
        cur.execute(
            f"""SELECT tagsn, tagtype, sitename, facilitytype, equipmenttype,
                       datainfo, datadesc, unit, alarm_tag_yn
                  FROM tb_tag_info{where_sql}
                 ORDER BY sitename, facilitytype, tagsn
                 LIMIT %s OFFSET %s""",
            params + [page_size, offset],
        )
        cols = ["tagsn", "tagtype", "sitename", "facilitytype", "equipmenttype",
                "datainfo", "datadesc", "unit", "alarm_tag_yn"]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]

        # alarm_tag_yn: Decimal → int
        for row in rows:
            if row["alarm_tag_yn"] is not None:
                row["alarm_tag_yn"] = int(row["alarm_tag_yn"])

        cur.close()
        return {
            "status": "OK",
            "data": rows,
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    except psycopg2.Error as e:
        logger.error(f"태그 목록 조회 실패: {e}")
        return {"status": "ERROR", "message": "조회에 실패했습니다.", "data": [], "total": 0}
    finally:
        if conn:
            conn.close()


@app.get("/tags/filters")
async def get_tag_filters():
    """태그 마스터 필터 옵션 (현장명/시설유형/태그유형/장비유형 목록)"""
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
        cur = conn.cursor()
        result = {}
        for col_name in ["sitename", "facilitytype", "tagtype", "equipmenttype"]:
            cur.execute(
                f"SELECT DISTINCT {col_name} FROM tb_tag_info "
                f"WHERE {col_name} IS NOT NULL ORDER BY {col_name}"
            )
            result[col_name] = [r[0] for r in cur.fetchall()]
        cur.close()
        return {"status": "OK", "data": result}
    except psycopg2.Error as e:
        logger.error(f"태그 필터 조회 실패: {e}")
        return {"status": "ERROR", "message": "조회에 실패했습니다.", "data": {}}
    finally:
        if conn:
            conn.close()


# =============================================================================
# 트렌드 시계열 데이터 조회
# =============================================================================

class TrendDataRequest(BaseModel):
    tag_ids: list
    from_ts: str
    to_ts: str
    max_points: Optional[int] = 2000


@app.post("/trend/data")
async def get_trend_data(req: TrendDataRequest):
    """트렌드 시계열 데이터 조회 — time_bucket 집계"""
    if not req.tag_ids or len(req.tag_ids) > 15:
        return {"status": "ERROR", "message": "태그는 1~15개 선택 가능합니다."}

    conn = None
    try:
        from datetime import datetime as dt_parse

        # 시간범위 파싱
        from_ts = req.from_ts.replace("T", " ").replace("Z", "")[:19]
        to_ts = req.to_ts.replace("T", " ").replace("Z", "")[:19]

        # 시간범위(분) 계산 → 버킷 크기 결정
        t_from = dt_parse.strptime(from_ts, "%Y-%m-%d %H:%M:%S")
        t_to = dt_parse.strptime(to_ts, "%Y-%m-%d %H:%M:%S")
        total_minutes = max(1, int((t_to - t_from).total_seconds() / 60))
        max_pts = min(max(req.max_points or 2000, 100), 5000)
        bucket_mins = max(1, total_minutes // max_pts)

        # 디지털 태그 목록 조회 (ROUND 처리용)
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
        cur = conn.cursor()
        cur.execute(
            "SELECT tagsn FROM tb_tag_info WHERE tagsn = ANY(%s) "
            "AND tagtype = 'Digital Input'",
            (req.tag_ids,)
        )
        digital_tags = {r[0] for r in cur.fetchall()}

        # time_bucket 집계 쿼리
        bucket_interval = f"{bucket_mins} minutes"
        cur.execute(
            """
            SELECT
                to_char(time_bucket(%s::interval, logtime), 'YYYY-MM-DD HH24:MI') AS ts,
                tagsn,
                AVG(val) AS val
            FROM tb_tag_raw_data
            WHERE tagsn = ANY(%s)
              AND logtime >= %s::timestamp
              AND logtime < %s::timestamp
            GROUP BY ts, tagsn
            ORDER BY ts, tagsn
            """,
            (bucket_interval, req.tag_ids, from_ts, to_ts)
        )
        rows = cur.fetchall()
        cur.close()

        # 후처리: 공통 times + tagsn별 values 배열
        from collections import OrderedDict
        time_set = OrderedDict()
        tag_data = {}
        for ts, tagsn, val in rows:
            time_set[ts] = True
            if tagsn not in tag_data:
                tag_data[tagsn] = {}
            # 디지털 태그 → 0/1 반올림
            if val is not None:
                v = round(float(val)) if tagsn in digital_tags else round(float(val), 4)
            else:
                v = None
            tag_data[tagsn][ts] = v

        times = list(time_set.keys())
        series = {}
        for tag_id in req.tag_ids:
            td = tag_data.get(tag_id, {})
            series[tag_id] = [td.get(t) for t in times]

        return {
            "status": "OK",
            "data": {"times": times, "series": series},
            "bucket_mins": bucket_mins,
            "total_points": len(times),
        }

    except Exception as e:
        logger.error(f"트렌드 데이터 조회 실패: {e}")
        return {"status": "ERROR", "message": f"조회에 실패했습니다: {str(e)}"}
    finally:
        if conn:
            conn.close()


# =============================================================================
# 대시보드 요약 API
# =============================================================================

@app.get("/monitoring/dashboard")
async def get_dashboard_summary():
    """대시보드 요약 정보 (시설현황, 알람, 태그, 배수지 수위, 알람추세)"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # 1) 시설유형별 현장 수
        cur.execute("""
            SELECT facilitytype, COUNT(DISTINCT sitename) as cnt
            FROM tb_trend_catalog
            GROUP BY facilitytype
        """)
        facility_counts = {}
        total_sites = 0
        for row in cur.fetchall():
            ft, cnt = row[0], row[1]
            facility_counts[ft] = cnt
            total_sites += cnt
        reservoir_cnt = facility_counts.get("배수지", 0)
        booster_cnt = facility_counts.get("가압장", 0)
        block_cnt = facility_counts.get("소블록", 0) + facility_counts.get("소소블록", 0)

        # 2) 진행중 알람
        cur.execute("""
            SELECT alarm_severity, COUNT(*) as cnt
            FROM tb_equipment_alarm_report
            WHERE alarm_status = '진행중'
            GROUP BY alarm_severity
        """)
        alarm_counts = {}
        total_alarms = 0
        for row in cur.fetchall():
            sev = row[0] or "기타"
            cnt = row[1]
            alarm_counts[sev] = cnt
            total_alarms += cnt
        alarm_desc_parts = [f"{k}: {v}" for k, v in sorted(alarm_counts.items(), key=lambda x: -x[1])]

        # 3) 센서 태그 수
        cur.execute("SELECT COUNT(*) FROM tb_tag_info")
        tag_total = cur.fetchone()[0]

        # 4) 관리 장비 수
        cur.execute("SELECT COUNT(DISTINCT equipment_id) FROM tb_network_status")
        equip_cnt = cur.fetchone()[0]

        # 요약 카드 구성
        summary_cards = [
            {
                "title": "관리 현장",
                "value": str(total_sites),
                "description": f"배수지 {reservoir_cnt} / 가압장 {booster_cnt} / 블록 {block_cnt}",
            },
            {
                "title": "진행중 알람",
                "value": str(total_alarms),
                "description": " / ".join(alarm_desc_parts) if alarm_desc_parts else "없음",
            },
            {
                "title": "센서 태그",
                "value": f"{tag_total:,}",
                "description": f"관리 장비 {equip_cnt}대",
            },
            {
                "title": "시스템 상태",
                "value": "정상",
                "description": "AI 서버 + DB 연결 정상",
            },
        ]

        # 5) 배수지 수위 현황 — 현장별 대표 수위 태그 최신값
        cur.execute("""
            WITH level_tags AS (
                SELECT tagsn, sitename, datadesc
                FROM tb_tag_info
                WHERE facilitytype = '배수지'
                  AND tagtype = 'Analog Input'
                  AND datainfo LIKE '%%수위%%'
                  AND datadesc NOT LIKE '%%설정%%'
                  AND datadesc NOT LIKE '%%HH%%'
                  AND datadesc NOT LIKE '%%LL%%'
                  AND datadesc NOT LIKE '%%H설정%%'
                  AND datadesc NOT LIKE '%%염소%%'
            ),
            latest AS (
                SELECT DISTINCT ON (tagsn) tagsn, val, logtime
                FROM tb_tag_raw_data
                WHERE tagsn IN (SELECT tagsn FROM level_tags)
                  AND logtime >= NOW() - INTERVAL '1 day'
                ORDER BY tagsn, logtime DESC
            ),
            site_avg AS (
                SELECT lt.sitename,
                       ROUND(AVG(l.val)::numeric, 2) as avg_level,
                       COUNT(*) as tag_cnt
                FROM level_tags lt
                JOIN latest l ON lt.tagsn = l.tagsn
                WHERE l.val IS NOT NULL AND l.val > 0
                GROUP BY lt.sitename
            )
            SELECT sitename, avg_level, tag_cnt FROM site_avg ORDER BY sitename
        """)
        reservoir_summaries = []
        for row in cur.fetchall():
            reservoir_summaries.append({
                "name": row[0],
                "currentLevel": float(row[1]),
                "maxCapacity": 5.0,
            })

        # 6) 7일 알람 추세
        cur.execute("""
            SELECT alarm_start_time::date as d, COALESCE(alarm_severity, '기타') as sev, COUNT(*) as cnt
            FROM tb_equipment_alarm_report
            WHERE alarm_start_time >= NOW() - INTERVAL '7 days'
            GROUP BY d, sev
            ORDER BY d
        """)
        alarm_trend = []
        for row in cur.fetchall():
            alarm_trend.append({
                "date": row[0].strftime("%m-%d"),
                "severity": row[1],
                "count": row[2],
            })

        # 7) 최근 알람 (24시간, 최대 20건)
        cur.execute("""
            SELECT r.alarm_start_time, r.tagsn, r.alarm_severity, r.alarm_status,
                   t.sitename, t.datadesc
            FROM tb_equipment_alarm_report r
            LEFT JOIN tb_tag_info t ON r.tagsn = t.tagsn
            WHERE r.alarm_start_time >= NOW() - INTERVAL '24 hours'
            ORDER BY r.alarm_start_time DESC
            LIMIT 20
        """)
        recent_alarms = []
        for i, row in enumerate(cur.fetchall()):
            alarm_time = row[0]
            tagsn = row[1]
            severity = row[2] or "기타"
            status = row[3]
            sitename = row[4] or ""
            datadesc = row[5] or tagsn
            recent_alarms.append({
                "id": i + 1,
                "time": alarm_time.strftime("%Y-%m-%d %H:%M"),
                "facility": sitename,
                "level": severity,
                "message": f"{datadesc} ({status})",
            })

        return {
            "summaryCards": summary_cards,
            "reservoirSummaries": reservoir_summaries,
            "recentAlarms": recent_alarms,
            "alarmTrend": alarm_trend,
        }

    except Exception as e:
        logger.error(f"대시보드 요약 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


# =============================================================================
# 모니터링 카탈로그 CRUD
# =============================================================================

@app.get("/monitoring/catalogs/sites")
async def get_monitoring_catalog_sites(facilitytype: str = ""):
    """시설유형별 DISTINCT 사이트 목록 반환 (tb_monitoring_catalog)"""
    if not facilitytype:
        return {"status": "ERROR", "message": "facilitytype 필수"}
    ftypes = [f.strip() for f in facilitytype.split(",") if f.strip()]
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        placeholders = ",".join(["%s"] * len(ftypes))
        cur.execute(
            f"SELECT DISTINCT sitename FROM tb_monitoring_catalog WHERE facilitytype IN ({placeholders}) ORDER BY sitename",
            ftypes,
        )
        sites = [r[0] for r in cur.fetchall()]
        return {"status": "OK", "sites": sites}
    except Exception as e:
        logger.error(f"모니터링 사이트 목록 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@app.get("/monitoring/catalogs")
async def get_monitoring_catalogs(
    facilitytype: str = "",
    sitename: str = "",
):
    """모니터링 카탈로그 목록 조회 (tb_monitoring_catalog)"""
    if not facilitytype:
        return {"status": "ERROR", "message": "facilitytype 필수"}
    ftypes = [f.strip() for f in facilitytype.split(",") if f.strip()]
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        conditions = [f"facilitytype IN ({','.join(['%s'] * len(ftypes))})"]
        params = list(ftypes)
        if sitename:
            conditions.append("sitename = %s")
            params.append(sitename)
        where = " AND ".join(conditions)
        cols = ["catalog_id", "sitename", "facilitytype", "catalog_name", "display_order", "items", "description", "created_at", "updated_at"]
        cur.execute(
            f"SELECT {', '.join(cols)} FROM tb_monitoring_catalog WHERE {where} "
            f"ORDER BY sitename, display_order, catalog_name",
            params,
        )
        rows = cur.fetchall()
        data = []
        for r in rows:
            item = dict(zip(cols, r))
            if item.get("created_at"):
                item["created_at"] = str(item["created_at"])
            if item.get("updated_at"):
                item["updated_at"] = str(item["updated_at"])
            data.append(item)
        sites = sorted(set(item["sitename"] for item in data))
        return {"status": "OK", "data": data, "sites": sites}
    except Exception as e:
        logger.error(f"모니터링 카탈로그 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@app.post("/monitoring/catalogs")
async def create_monitoring_catalog(request: Request):
    """모니터링 카탈로그 생성 (tb_monitoring_catalog) — 이름 충돌 시 자동 접미사"""
    body = await request.json()
    sitename = body.get("sitename", "")
    facilitytype = body.get("facilitytype", "")
    catalog_name = body.get("catalog_name", "")
    display_order = body.get("display_order", 999)
    items = body.get("items", [])
    description = body.get("description", "")
    if not sitename or not facilitytype or not catalog_name:
        return {"status": "ERROR", "message": "sitename, facilitytype, catalog_name 필수"}
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # 이름 충돌 확인 → 자동 접미사 부여
        final_name = catalog_name
        cur.execute(
            "SELECT catalog_name FROM tb_monitoring_catalog "
            "WHERE sitename = %s AND facilitytype = %s AND catalog_name LIKE %s",
            (sitename, facilitytype, catalog_name + "%"),
        )
        existing_names = {r[0] for r in cur.fetchall()}
        if final_name in existing_names:
            for i in range(2, 100):
                candidate = f"{catalog_name}({i})"
                if candidate not in existing_names:
                    final_name = candidate
                    break
        items_json = json.dumps(items, ensure_ascii=False)
        cur.execute(
            "INSERT INTO tb_monitoring_catalog (sitename, facilitytype, catalog_name, display_order, items, description) "
            "VALUES (%s, %s, %s, %s, %s::jsonb, %s) RETURNING catalog_id",
            (sitename, facilitytype, final_name, display_order, items_json, description),
        )
        catalog_id = cur.fetchone()[0]
        conn.commit()
        return {"status": "OK", "catalog_id": catalog_id, "catalog_name": final_name}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"모니터링 카탈로그 생성 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@app.put("/monitoring/catalogs/{catalog_id}")
async def update_monitoring_catalog(catalog_id: int, request: Request):
    """모니터링 카탈로그 수정 (tb_monitoring_catalog)"""
    body = await request.json()
    fields, params = [], []
    if "catalog_name" in body:
        fields.append("catalog_name = %s")
        params.append(body["catalog_name"])
    if "display_order" in body:
        fields.append("display_order = %s")
        params.append(body["display_order"])
    if "items" in body:
        fields.append("items = %s::jsonb")
        params.append(json.dumps(body["items"], ensure_ascii=False))
    if "description" in body:
        fields.append("description = %s")
        params.append(body["description"])
    if not fields:
        return {"status": "ERROR", "message": "수정할 필드 없음"}
    params.append(catalog_id)
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            f"UPDATE tb_monitoring_catalog SET {', '.join(fields)} WHERE catalog_id = %s",
            params,
        )
        conn.commit()
        return {"status": "OK"}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"모니터링 카탈로그 수정 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@app.delete("/monitoring/catalogs/{catalog_id}")
async def delete_monitoring_catalog(catalog_id: int):
    """모니터링 카탈로그 삭제 (tb_monitoring_catalog)"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM tb_monitoring_catalog WHERE catalog_id = %s", (catalog_id,))
        conn.commit()
        return {"status": "OK"}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"모니터링 카탈로그 삭제 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@app.get("/monitoring/catalogs/reference")
async def get_monitoring_catalog_reference(
    facilitytype: str = "",
    sitename: str = "",
):
    """기존 트렌드 카탈로그(tb_trend_catalog) 참조 조회 — 모니터링 설정에서 태그 가져오기용"""
    if not facilitytype:
        return {"status": "ERROR", "message": "facilitytype 필수"}
    ftypes = [f.strip() for f in facilitytype.split(",") if f.strip()]
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        conditions = [f"facilitytype IN ({','.join(['%s'] * len(ftypes))})"]
        params = list(ftypes)
        if sitename:
            conditions.append("sitename = %s")
            params.append(sitename)
        where = " AND ".join(conditions)
        cols = ["trend_id", "sitename", "facilitytype", "trend_name", "meta", "description"]
        cur.execute(
            f"SELECT {', '.join(cols)} FROM tb_trend_catalog WHERE {where} ORDER BY sitename, trend_name",
            params,
        )
        rows = cur.fetchall()
        data = [dict(zip(cols, r)) for r in rows]
        return {"status": "OK", "data": data}
    except Exception as e:
        logger.error(f"카탈로그 참조 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


# =============================================================================
# 용수 흐름 관리 (tb_facility_flow_map)
# =============================================================================


@app.get("/flow-map")
async def get_flow_maps():
    """용수 흐름 전체 조회."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT upstream_sitename, upstream_facilitytype,
                   downstream_sitename, downstream_facilitytype,
                   relation_type, description
            FROM tb_facility_flow_map
            ORDER BY upstream_facilitytype, upstream_sitename,
                     downstream_facilitytype, downstream_sitename
        """)
        rows = cur.fetchall()
        cur.close()
        data = [
            {
                "upstream_sitename": r[0],
                "upstream_facilitytype": r[1],
                "downstream_sitename": r[2],
                "downstream_facilitytype": r[3],
                "relation_type": r[4],
                "description": r[5],
            }
            for r in rows
        ]
        return {"status": "OK", "data": data, "total": len(data)}
    except Exception as e:
        logger.error(f"용수 흐름 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@app.get("/flow-map/roots")
async def get_flow_map_roots():
    """최상위 노드 목록 (상류에만 존재하고 하류에는 없는 노드)."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT upstream_sitename, upstream_facilitytype
            FROM tb_facility_flow_map
            WHERE (upstream_sitename, upstream_facilitytype) NOT IN (
                SELECT downstream_sitename, downstream_facilitytype
                FROM tb_facility_flow_map
            )
            ORDER BY upstream_facilitytype, upstream_sitename
        """)
        rows = cur.fetchall()
        cur.close()
        data = [
            {"sitename": r[0], "facilitytype": r[1]}
            for r in rows
        ]
        return {"status": "OK", "data": data}
    except Exception as e:
        logger.error(f"용수 흐름 루트 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@app.get("/flow-map/downstream")
async def get_flow_map_downstream(sitename: str, facilitytype: str):
    """특정 노드의 하류 전체 (재귀 CTE)."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            WITH RECURSIVE downstream AS (
                SELECT upstream_sitename, upstream_facilitytype,
                       downstream_sitename, downstream_facilitytype,
                       relation_type, description
                FROM tb_facility_flow_map
                WHERE upstream_sitename = %s AND upstream_facilitytype = %s
                UNION
                SELECT f.upstream_sitename, f.upstream_facilitytype,
                       f.downstream_sitename, f.downstream_facilitytype,
                       f.relation_type, f.description
                FROM tb_facility_flow_map f
                JOIN downstream d
                  ON f.upstream_sitename = d.downstream_sitename
                 AND f.upstream_facilitytype = d.downstream_facilitytype
            )
            SELECT * FROM downstream
            ORDER BY upstream_facilitytype, upstream_sitename
        """, (sitename, facilitytype))
        rows = cur.fetchall()
        cur.close()
        data = [
            {
                "upstream_sitename": r[0],
                "upstream_facilitytype": r[1],
                "downstream_sitename": r[2],
                "downstream_facilitytype": r[3],
                "relation_type": r[4],
                "description": r[5],
            }
            for r in rows
        ]
        return {"status": "OK", "data": data}
    except Exception as e:
        logger.error(f"용수 흐름 하류 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@app.post("/flow-map")
async def create_flow_map(req: dict):
    """용수 흐름 연결 추가."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO tb_facility_flow_map
                (upstream_sitename, upstream_facilitytype,
                 downstream_sitename, downstream_facilitytype,
                 relation_type, description)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (upstream_sitename, upstream_facilitytype,
                         downstream_sitename, downstream_facilitytype)
            DO UPDATE SET
                relation_type = EXCLUDED.relation_type,
                description = EXCLUDED.description
        """, (
            req["upstream_sitename"], req["upstream_facilitytype"],
            req["downstream_sitename"], req["downstream_facilitytype"],
            req.get("relation_type", "수계"),
            req.get("description"),
        ))
        conn.commit()
        cur.close()
        return {"status": "OK"}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"용수 흐름 추가 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@app.delete("/flow-map")
async def delete_flow_map(
    upstream_sitename: str,
    upstream_facilitytype: str,
    downstream_sitename: str,
    downstream_facilitytype: str,
):
    """용수 흐름 연결 삭제."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            DELETE FROM tb_facility_flow_map
            WHERE upstream_sitename = %s AND upstream_facilitytype = %s
              AND downstream_sitename = %s AND downstream_facilitytype = %s
        """, (
            upstream_sitename, upstream_facilitytype,
            downstream_sitename, downstream_facilitytype,
        ))
        deleted = cur.rowcount
        conn.commit()
        cur.close()
        return {"status": "OK", "deleted": deleted}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"용수 흐름 삭제 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@app.get("/flow-map/export/csv")
async def export_flow_map_csv():
    """용수 흐름 CSV 다운로드."""
    import io
    import csv as csv_mod
    from starlette.responses import StreamingResponse

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT upstream_sitename, upstream_facilitytype,
                   downstream_sitename, downstream_facilitytype,
                   relation_type, COALESCE(description, '')
            FROM tb_facility_flow_map
            ORDER BY upstream_facilitytype, upstream_sitename
        """)
        rows = cur.fetchall()
        cur.close()

        buf = io.StringIO()
        writer = csv_mod.writer(buf)
        writer.writerow([
            "상류현장명", "상류시설유형",
            "하류현장명", "하류시설유형",
            "관계유형", "설명",
        ])
        for r in rows:
            writer.writerow(r)
        buf.seek(0)

        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv; charset=utf-8-sig",
            headers={
                "Content-Disposition":
                    "attachment; filename=flow_map.csv"
            },
        )
    except Exception as e:
        logger.error(f"용수 흐름 CSV 내보내기 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@app.post("/flow-map/import/csv")
async def import_flow_map_csv(file: UploadFile):
    """용수 흐름 CSV 업로드 (일괄 입력)."""
    import io
    import csv as csv_mod

    conn = None
    try:
        content = await file.read()
        text = content.decode("utf-8-sig")
        reader = csv_mod.reader(io.StringIO(text))
        header = next(reader, None)
        if not header or len(header) < 4:
            return {"status": "ERROR", "message": "CSV 헤더 부족 (최소 4컬럼)"}

        conn = get_db_connection()
        cur = conn.cursor()
        created = 0
        skipped = 0

        for row in reader:
            if len(row) < 4:
                skipped += 1
                continue
            up_sn = row[0].strip()
            up_ft = row[1].strip()
            dn_sn = row[2].strip()
            dn_ft = row[3].strip()
            rel = row[4].strip() if len(row) > 4 and row[4].strip() else "수계"
            desc = row[5].strip() if len(row) > 5 else None

            if not up_sn or not up_ft or not dn_sn or not dn_ft:
                skipped += 1
                continue

            cur.execute("""
                INSERT INTO tb_facility_flow_map
                    (upstream_sitename, upstream_facilitytype,
                     downstream_sitename, downstream_facilitytype,
                     relation_type, description)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (upstream_sitename, upstream_facilitytype,
                             downstream_sitename, downstream_facilitytype)
                DO UPDATE SET
                    relation_type = EXCLUDED.relation_type,
                    description = EXCLUDED.description
            """, (up_sn, up_ft, dn_sn, dn_ft, rel, desc))
            created += 1

        conn.commit()
        cur.close()
        return {"status": "OK", "created": created, "skipped": skipped}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"용수 흐름 CSV 가져오기 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


# =============================================================================
# 설비 관리 API (tb_equipment_info CRUD)
# =============================================================================

class EquipmentCreateRequest(BaseModel):
    prefix: str                           # equipment_id 접두사 (예: "booster_pump")
    sitename: str
    facilitytype: str
    equipmenttype: str
    status: str = "operational"
    commissioned_at: Optional[str] = None  # "YYYY-MM-DD" or None
    decommissioned_at: Optional[str] = None
    description: Optional[str] = None
    meta: Optional[dict] = None


class EquipmentUpdateRequest(BaseModel):
    sitename: Optional[str] = None
    facilitytype: Optional[str] = None
    equipmenttype: Optional[str] = None
    status: Optional[str] = None
    commissioned_at: Optional[str] = None
    decommissioned_at: Optional[str] = None
    description: Optional[str] = None
    meta: Optional[dict] = None


def _next_equipment_number(cur, prefix: str) -> int:
    """주어진 접두사의 다음 순번 계산."""
    cur.execute("""
        SELECT COALESCE(MAX(
            CAST(SUBSTRING(equipment_id FROM LENGTH(%s) + 2) AS INTEGER)
        ), 0) + 1
        FROM tb_equipment_info
        WHERE LEFT(equipment_id, LENGTH(%s) + 1) = %s || '_'
          AND SUBSTRING(equipment_id FROM LENGTH(%s) + 2) ~ '^\\d+$'
    """, (prefix, prefix, prefix, prefix))
    return cur.fetchone()[0]


def _serialize_equipment_row(r) -> dict:
    """설비 행을 JSON 직렬화."""
    return {
        "equipment_id": r[0],
        "sitename": r[1],
        "facilitytype": r[2],
        "equipmenttype": r[3],
        "status": r[4],
        "commissioned_at": r[5].isoformat() if r[5] else None,
        "decommissioned_at": r[6].isoformat() if r[6] else None,
        "description": r[7],
        "meta": r[8] if isinstance(r[8], dict) else (json.loads(r[8]) if r[8] else {}),
        "created_at": r[9].isoformat() if r[9] else None,
        "updated_at": r[10].isoformat() if r[10] else None,
    }


@app.get("/equipments")
async def get_equipments(
    sitename: Optional[str] = Query(None),
    facilitytype: Optional[str] = Query(None),
    equipmenttype: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
):
    """설비 목록 조회 (페이징+필터)."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        wheres, params = [], []
        if sitename:
            wheres.append("sitename = %s")
            params.append(sitename)
        if facilitytype:
            wheres.append("facilitytype = %s")
            params.append(facilitytype)
        if equipmenttype:
            wheres.append("equipmenttype = %s")
            params.append(equipmenttype)
        if keyword:
            wheres.append("(equipment_id ILIKE %s OR description ILIKE %s OR meta::text ILIKE %s)")
            kw = f"%{keyword}%"
            params.extend([kw, kw, kw])

        where_sql = " AND ".join(wheres) if wheres else "TRUE"

        # 총 건수
        cur.execute(f"SELECT COUNT(*) FROM tb_equipment_info WHERE {where_sql}", params)
        total = cur.fetchone()[0]

        # 데이터 조회
        offset = (page - 1) * page_size
        cur.execute(f"""
            SELECT equipment_id, sitename, facilitytype, equipmenttype, status,
                   commissioned_at, decommissioned_at, description, meta,
                   created_at, updated_at
            FROM tb_equipment_info
            WHERE {where_sql}
            ORDER BY sitename, facilitytype, equipment_id
            LIMIT %s OFFSET %s
        """, params + [page_size, offset])
        rows = cur.fetchall()
        cur.close()

        data = [_serialize_equipment_row(r) for r in rows]
        return {"status": "OK", "data": data, "total": total, "page": page, "page_size": page_size}
    except Exception as e:
        logger.error(f"설비 목록 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@app.get("/equipments/filters")
async def get_equipment_filters():
    """설비 필터 옵션 조회 (distinct 값)."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        result = {}
        for col in ["sitename", "facilitytype", "equipmenttype"]:
            cur.execute(f"SELECT DISTINCT {col} FROM tb_equipment_info WHERE {col} IS NOT NULL ORDER BY {col}")
            result[col] = [r[0] for r in cur.fetchall()]

        # status 참조 테이블
        cur.execute("SELECT code, display_name FROM tb_equipment_status ORDER BY code")
        result["status"] = [{"value": r[0], "label": r[1]} for r in cur.fetchall()]
        cur.close()
        return {"status": "OK", "data": result}
    except Exception as e:
        logger.error(f"설비 필터 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@app.get("/equipments/next-id")
async def get_next_equipment_id(prefix: str = Query(..., min_length=1)):
    """다음 설비 ID 조회 (접두사 기준 다음 순번)."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        next_num = _next_equipment_number(cur, prefix.strip())
        cur.close()
        next_id = f"{prefix.strip()}_{next_num}"
        return {"status": "OK", "next_id": next_id, "next_number": next_num}
    except Exception as e:
        logger.error(f"다음 설비 ID 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@app.post("/equipments")
async def create_equipment(req: EquipmentCreateRequest):
    """설비 추가 (equipment_id 자동 생성)."""
    conn = None
    try:
        prefix = req.prefix.strip()
        if not prefix:
            return {"status": "ERROR", "message": "접두사(prefix)가 비어 있습니다."}

        conn = get_db_connection()
        cur = conn.cursor()

        next_num = _next_equipment_number(cur, prefix)
        equipment_id = f"{prefix}_{next_num}"

        meta_json = json.dumps(req.meta or {}, ensure_ascii=False)

        cur.execute("""
            INSERT INTO tb_equipment_info
                (equipment_id, sitename, facilitytype, equipmenttype, status,
                 commissioned_at, decommissioned_at, description, meta)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            equipment_id, req.sitename.strip(), req.facilitytype,
            req.equipmenttype, req.status,
            req.commissioned_at or None, req.decommissioned_at or None,
            req.description or None, meta_json,
        ))
        conn.commit()
        cur.close()
        return {"status": "OK", "equipment_id": equipment_id}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"설비 추가 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@app.put("/equipments/{equipment_id}")
async def update_equipment(equipment_id: str, req: EquipmentUpdateRequest):
    """설비 수정 (equipment_id는 불변)."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        set_parts, params = [], []
        if req.sitename is not None:
            set_parts.append("sitename = %s")
            params.append(req.sitename.strip())
        if req.facilitytype is not None:
            set_parts.append("facilitytype = %s")
            params.append(req.facilitytype)
        if req.equipmenttype is not None:
            set_parts.append("equipmenttype = %s")
            params.append(req.equipmenttype)
        if req.status is not None:
            set_parts.append("status = %s")
            params.append(req.status)
        if req.commissioned_at is not None:
            set_parts.append("commissioned_at = %s")
            params.append(req.commissioned_at if req.commissioned_at else None)
        if req.decommissioned_at is not None:
            set_parts.append("decommissioned_at = %s")
            params.append(req.decommissioned_at if req.decommissioned_at else None)
        if req.description is not None:
            set_parts.append("description = %s")
            params.append(req.description if req.description else None)
        if req.meta is not None:
            set_parts.append("meta = %s")
            params.append(json.dumps(req.meta, ensure_ascii=False))

        if not set_parts:
            return {"status": "OK", "updated": 0, "message": "변경할 항목이 없습니다."}

        params.append(equipment_id)
        cur.execute(
            f"UPDATE tb_equipment_info SET {', '.join(set_parts)} WHERE equipment_id = %s",
            params,
        )
        conn.commit()
        updated = cur.rowcount
        cur.close()
        return {"status": "OK", "updated": updated}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"설비 수정 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@app.delete("/equipments/{equipment_id}")
async def delete_equipment(equipment_id: str, dry_run: bool = Query(False)):
    """설비 삭제 (dry_run=true → cascade 영향만 확인)."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # cascade 영향 확인
        cur.execute("SELECT COUNT(*) FROM tb_network_info WHERE equipment_id = %s", (equipment_id,))
        net_info_cnt = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM tb_network_link WHERE source_equipment_id = %s OR target_equipment_id = %s",
            (equipment_id, equipment_id),
        )
        net_link_cnt = cur.fetchone()[0]

        cascade = {"network_info": net_info_cnt, "network_link": net_link_cnt}

        if dry_run:
            cur.close()
            return {"status": "OK", "dry_run": True, "cascade": cascade}

        cur.execute("DELETE FROM tb_equipment_info WHERE equipment_id = %s", (equipment_id,))
        conn.commit()
        deleted = cur.rowcount
        cur.close()
        return {"status": "OK", "deleted": deleted, "cascade": cascade}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"설비 삭제 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


# =============================================================================
# 배수지 관리 API
# =============================================================================

def _serialize_reservoir_info(r: tuple) -> dict:
    """tb_service_reservoir_info row → dict (general_overview flat 변환).
    SELECT 순서: sitename, general_overview, install_year, service_area,
                 zone_count, zone_1_area..zone_5_height (10 cols)
    """
    go = r[1] if isinstance(r[1], dict) else (json.loads(r[1]) if r[1] else {})
    spec = go.get("reservoir_spec", {}) if isinstance(go.get("reservoir_spec"), dict) else {}
    return {
        "sitename": r[0],
        "install_year": r[2],
        "service_area": r[3],
        "zone_count": r[4],
        "zone_1_area": float(r[5]) if r[5] is not None else None,
        "zone_1_height": float(r[6]) if r[6] is not None else None,
        "zone_2_area": float(r[7]) if r[7] is not None else None,
        "zone_2_height": float(r[8]) if r[8] is not None else None,
        "zone_3_area": float(r[9]) if r[9] is not None else None,
        "zone_3_height": float(r[10]) if r[10] is not None else None,
        "zone_4_area": float(r[11]) if r[11] is not None else None,
        "zone_4_height": float(r[12]) if r[12] is not None else None,
        "zone_5_area": float(r[13]) if r[13] is not None else None,
        "zone_5_height": float(r[14]) if r[14] is not None else None,
        # general_overview flat
        "install_location": go.get("install_location"),
        "operating_status": go.get("operating_status"),
        "supply_population": go.get("supply_population"),
        "facility_capacity_m3": go.get("facility_capacity_m3"),
        "reservoir_count": spec.get("count"),
        "hwl": spec.get("H.W.L"),
        "lwl": spec.get("L.W.L"),
        "emergency_water_plan": go.get("emergency_water_plan"),
        "water_truck_accessible": go.get("water_truck_accessible"),
        "water_truck_turning_possible": go.get("water_truck_turning_possible"),
        "pump_required": go.get("pump_required"),
        "supply_position": go.get("supply_position"),
        "supply_time_hours": go.get("supply_time_hours"),
    }


def _serialize_reservoir_status(r: tuple) -> dict:
    """tb_service_reservoir_status row → dict.
    SELECT 순서: sitename, total_supply_time, supply_time_status,
                 supply_time_reason, meta
    """
    meta_raw = r[4]
    if isinstance(meta_raw, str):
        meta_raw = json.loads(meta_raw)
    meta = meta_raw if isinstance(meta_raw, list) else []
    return {
        "sitename": r[0],
        "total_supply_time": float(r[1]) if r[1] is not None else None,
        "supply_time_status": r[2],
        "supply_time_reason": r[3],
        "equipment_meta": meta,
    }


_RESERVOIR_GO_KEYS = (
    "install_location", "operating_status", "supply_population",
    "facility_capacity_m3", "pump_required", "supply_position",
    "supply_time_hours",
)


def _build_reservoir_general_overview(body: dict) -> dict:
    """프론트엔드 flat 필드 → general_overview JSONB 조립."""
    go: dict = {}
    for key in _RESERVOIR_GO_KEYS:
        val = body.get(key)
        if val is not None:
            go[key] = val
    spec: dict = {}
    if body.get("reservoir_count") is not None:
        spec["count"] = body["reservoir_count"]
    if body.get("hwl") is not None:
        spec["H.W.L"] = body["hwl"]
    if body.get("lwl") is not None:
        spec["L.W.L"] = body["lwl"]
    if spec:
        go["reservoir_spec"] = spec
    if body.get("emergency_water_plan") is not None:
        go["emergency_water_plan"] = body["emergency_water_plan"]
    if body.get("water_truck_accessible") is not None:
        go["water_truck_accessible"] = body["water_truck_accessible"]
    if body.get("water_truck_turning_possible") is not None:
        go["water_truck_turning_possible"] = body["water_truck_turning_possible"]
    return go


@app.get("/reservoirs")
async def get_reservoirs(
    keyword: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
):
    """배수지 목록 조회 (페이징+키워드 검색)."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        wheres, params = [], []
        if keyword:
            wheres.append("(i.sitename ILIKE %s OR i.service_area ILIKE %s)")
            kw = f"%{keyword}%"
            params.extend([kw, kw])

        where_sql = " AND ".join(wheres) if wheres else "TRUE"

        cur.execute(f"SELECT COUNT(*) FROM tb_service_reservoir_info i WHERE {where_sql}", params)
        total = cur.fetchone()[0]

        offset = (page - 1) * page_size
        cur.execute(f"""
            SELECT i.sitename, i.general_overview, i.install_year, i.service_area,
                   i.zone_count, i.zone_1_area, i.zone_1_height,
                   i.zone_2_area, i.zone_2_height, i.zone_3_area, i.zone_3_height,
                   i.zone_4_area, i.zone_4_height, i.zone_5_area, i.zone_5_height
            FROM tb_service_reservoir_info i
            WHERE {where_sql}
            ORDER BY i.sitename
            LIMIT %s OFFSET %s
        """, params + [page_size, offset])
        rows = cur.fetchall()
        cur.close()

        data = [_serialize_reservoir_info(r) for r in rows]
        return {"status": "OK", "data": data, "total": total, "page": page, "page_size": page_size}
    except Exception as e:
        logger.error(f"배수지 목록 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@app.get("/reservoirs/{sitename}")
async def get_reservoir_detail(sitename: str):
    """배수지 상세 조회 (info + status)."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT sitename, general_overview, install_year, service_area,
                   zone_count, zone_1_area, zone_1_height,
                   zone_2_area, zone_2_height, zone_3_area, zone_3_height,
                   zone_4_area, zone_4_height, zone_5_area, zone_5_height
            FROM tb_service_reservoir_info
            WHERE sitename = %s
        """, (sitename,))
        info_row = cur.fetchone()
        if not info_row:
            cur.close()
            return {"status": "ERROR", "message": f"'{sitename}' 배수지를 찾을 수 없습니다."}

        cur.execute("""
            SELECT sitename, total_supply_time, supply_time_status,
                   supply_time_reason, meta
            FROM tb_service_reservoir_status
            WHERE sitename = %s
        """, (sitename,))
        status_row = cur.fetchone()
        cur.close()

        info = _serialize_reservoir_info(info_row)
        status = _serialize_reservoir_status(status_row) if status_row else None
        return {"status": "OK", "info": info, "reservoir_status": status}
    except Exception as e:
        logger.error(f"배수지 상세 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@app.post("/reservoirs")
async def create_reservoir(request: Request):
    """배수지 추가 (info + status 양쪽 INSERT)."""
    conn = None
    try:
        body = await request.json()
        sitename = body.get("sitename", "").strip()
        if not sitename:
            return {"status": "ERROR", "message": "현장명은 필수입니다."}

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("SELECT 1 FROM tb_service_reservoir_info WHERE sitename = %s", (sitename,))
        if cur.fetchone():
            cur.close()
            return {"status": "ERROR", "message": f"'{sitename}' 배수지가 이미 존재합니다."}

        go = _build_reservoir_general_overview(body)

        cur.execute("""
            INSERT INTO tb_service_reservoir_info
                (sitename, general_overview, install_year, service_area, zone_count,
                 zone_1_area, zone_1_height, zone_2_area, zone_2_height,
                 zone_3_area, zone_3_height, zone_4_area, zone_4_height,
                 zone_5_area, zone_5_height,
                 water_level_unit, reservoir_area_unit)
            VALUES (%s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    'm', '㎥')
        """, (
            sitename, json.dumps(go, ensure_ascii=False),
            body.get("install_year"), body.get("service_area"), body.get("zone_count"),
            body.get("zone_1_area"), body.get("zone_1_height"),
            body.get("zone_2_area"), body.get("zone_2_height"),
            body.get("zone_3_area"), body.get("zone_3_height"),
            body.get("zone_4_area"), body.get("zone_4_height"),
            body.get("zone_5_area"), body.get("zone_5_height"),
        ))

        # status INSERT (equipment_meta 배열)
        eq_meta = body.get("equipment_meta", [])
        cur.execute("""
            INSERT INTO tb_service_reservoir_status
                (sitename, total_supply_time, water_level_unit, meta)
            VALUES (%s, %s, 'm', %s)
        """, (
            sitename,
            body.get("total_supply_time"),
            json.dumps(eq_meta, ensure_ascii=False),
        ))

        conn.commit()
        cur.close()
        return {"status": "OK", "sitename": sitename}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"배수지 추가 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@app.put("/reservoirs/{sitename}")
async def update_reservoir(sitename: str, request: Request):
    """배수지 수정 (info UPDATE + status UPSERT)."""
    conn = None
    try:
        body = await request.json()
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("SELECT 1 FROM tb_service_reservoir_info WHERE sitename = %s", (sitename,))
        if not cur.fetchone():
            cur.close()
            return {"status": "ERROR", "message": f"'{sitename}' 배수지를 찾을 수 없습니다."}

        go = _build_reservoir_general_overview(body)

        cur.execute("""
            UPDATE tb_service_reservoir_info SET
                general_overview = %s, install_year = %s, service_area = %s,
                zone_count = %s,
                zone_1_area = %s, zone_1_height = %s,
                zone_2_area = %s, zone_2_height = %s,
                zone_3_area = %s, zone_3_height = %s,
                zone_4_area = %s, zone_4_height = %s,
                zone_5_area = %s, zone_5_height = %s
            WHERE sitename = %s
        """, (
            json.dumps(go, ensure_ascii=False),
            body.get("install_year"), body.get("service_area"), body.get("zone_count"),
            body.get("zone_1_area"), body.get("zone_1_height"),
            body.get("zone_2_area"), body.get("zone_2_height"),
            body.get("zone_3_area"), body.get("zone_3_height"),
            body.get("zone_4_area"), body.get("zone_4_height"),
            body.get("zone_5_area"), body.get("zone_5_height"),
            sitename,
        ))

        # status UPSERT (equipment_meta 배열 + total_supply_time)
        eq_meta = body.get("equipment_meta", [])
        cur.execute("""
            INSERT INTO tb_service_reservoir_status
                (sitename, total_supply_time, water_level_unit, meta)
            VALUES (%s, %s, 'm', %s)
            ON CONFLICT (sitename) DO UPDATE SET
                total_supply_time = EXCLUDED.total_supply_time,
                meta = EXCLUDED.meta
        """, (
            sitename,
            body.get("total_supply_time"),
            json.dumps(eq_meta, ensure_ascii=False),
        ))

        conn.commit()
        cur.close()
        return {"status": "OK", "updated": 1}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"배수지 수정 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@app.delete("/reservoirs/{sitename}")
async def delete_reservoir(sitename: str, dry_run: bool = Query(False)):
    """배수지 삭제 (dry_run=true → 영향만 확인)."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM tb_service_reservoir_status WHERE sitename = %s", (sitename,))
        status_cnt = cur.fetchone()[0]

        if dry_run:
            cur.close()
            return {"status": "OK", "dry_run": True, "related": {"status_rows": status_cnt}}

        cur.execute("DELETE FROM tb_service_reservoir_status WHERE sitename = %s", (sitename,))
        cur.execute("DELETE FROM tb_service_reservoir_info WHERE sitename = %s", (sitename,))
        conn.commit()
        deleted = cur.rowcount
        cur.close()
        return {"status": "OK", "deleted": deleted}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"배수지 삭제 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


# =============================================================================
# 메인 실행
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
