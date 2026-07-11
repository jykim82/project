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
import time

# .env 파일 로드 (python-dotenv 설치 시) — 스크립트 위치 기준 절대경로
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass  # python-dotenv 미설치 시 환경변수만 사용
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Optional

import psycopg2
import psycopg2.pool
from fastapi import FastAPI, Request, UploadFile, File, Form, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from slm_config import ENABLE_KEYWORD_FALLBACK, get_model, set_model, OLLAMA_BASE_URL
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
    GROUP_THRESHOLDS,
    classify_alert_grade,
    verify_intra_facility,
    build_intra_facility_block,
    verify_cross_facility_intra_rules,
)
from anomaly_iforest import IForestManager
from site_profiler import SiteProfiler
from db_sync import DbSyncWorker  # [임시] 개발 완료 후 제거 예정
from snmp_poller import SnmpPoller
from endpoints.facility_crud import router as facility_crud_router, init as init_facility_crud
from endpoints.facility_types_crud import router as facility_types_crud_router, init as init_facility_types_crud
from endpoints.network_crud import router as network_crud_router, init as init_network_crud
from endpoints.canvas_crud import router as canvas_crud_router, init as init_canvas_crud
from endpoints.auth_crud import router as auth_crud_router, init as init_auth_crud
from endpoints.alarm_contacts import router as alarm_contacts_router, init as init_alarm_contacts
from endpoints.monitoring_catalogs import router as monitoring_catalogs_router, init as init_monitoring_catalogs
from endpoints.flow_map_crud import router as flow_map_crud_router, init as init_flow_map_crud
from endpoints.csv_import import router as csv_import_router, init as init_csv_import
from endpoints.trend import router as trend_router, init as init_trend
from endpoints.causal import router as causal_router, init as init_causal
from endpoints.alarm_crisis import router as alarm_crisis_router, init as init_alarm_crisis
from endpoints.tags import router as tags_router, init as init_tags
from endpoints.dashboard import router as dashboard_router, init as init_dashboard
from endpoints.flow_realtime import router as flow_realtime_router, init as init_flow_realtime
from endpoints.admin import router as admin_router, init as init_admin
from endpoints.chat_feedback import router as chat_feedback_router, init as init_chat_feedback, init_intent_index as init_feedback_intent_index
from endpoints.chat_log import router as chat_log_router, init as init_chat_log
from endpoints.chat_fault_record import router as chat_fault_record_router, init as init_chat_fault_record
from endpoints.fault_case import router as fault_case_router, init as init_fault_case
from endpoints.alarm_fault_correlation import router as afc_router, init as init_afc
from endpoints.equipment_health import router as equipment_health_router, init as init_equipment_health
from endpoints.facility_alias import router as facility_alias_router, init as init_facility_alias
from endpoints.baseline_eval import router as baseline_eval_router, init as init_baseline_eval
from endpoints.iforest_eval import router as iforest_eval_router, init as init_iforest_eval
from endpoints.anomaly_explain import router as anomaly_explain_router, init as init_anomaly_explain
from endpoints.equipment_mtbf import router as equipment_mtbf_router, init as init_equipment_mtbf
from endpoints.alarm_calendar import router as alarm_calendar_router, init as init_alarm_calendar
from endpoints.leak_cusum_alert import (
    router as leak_cusum_alert_router,
    init as init_leak_cusum_alert,
    run_leak_cusum_scan,
)
from endpoints.llm_narrative_stats import router as llm_narrative_stats_router
from endpoints.tag_latest_explain import router as tag_latest_explain_router, init as init_tag_latest_explain
from endpoints.scan_all_explain import router as scan_all_explain_router, init as init_scan_all_explain
from endpoints.equipment_mtbf_explain import (
    router as equipment_mtbf_explain_router,
    init as init_equipment_mtbf_explain,
)
from endpoints.network_upstream_explain import (
    router as network_upstream_explain_router,
    init as init_network_upstream_explain,
)
from endpoints.chat_faq_examples import (
    router as chat_faq_examples_router,
    init as init_chat_faq_examples,
)
from shared.timeseries import get_chunks_for_range, query_chunks_agg, reaggregate, query_chunks_raw
import response_builder
import anomaly_scan
from anomaly_scan import (
    _compute_anomaly_scan_all,
    _diagnose_equipment_for_tags,
    adjust_sql_time_window_to_max_bucket,
)
from response_builder import (
    JsonbSchemaViolation, process_sql_result,
    render_answer_template, apply_corrections_to_answer,
    build_success_response, build_error_response, build_no_data_response,
    build_correction_response, compute_anomaly_zones,
    build_anomaly_facility_filter, build_anomaly_scope_label,
    _filter_anomaly_cache_rows, _filter_by_sitename, _filter_flow_balance,
    _filter_cross_mismatches, _diagnose_no_data, classify_chart_data_type,
    _query_recent_values,
    _execute_night_min_flow_query, _execute_night_min_flow_stddev_query,
    _extract_stddev_stats,
    _execute_tag_daily_summary_query, _execute_timeseries_query,
    _execute_hunting_check, _execute_catalog_trend_query,
    _execute_reservoir_supply_query, _execute_reservoir_supply_query_with_conn, wrap_status_marker,
    _sql_escape_literal, _get_tag_datainfo_cache, _query_flow_timeseries,
)
from sql_executor import _SUPPLY_INTENTS, _TIMESERIES_CHUNK_INTENTS, _execute_level_cause_analysis, _extract_alarm_filter, _extract_alarm_level
from response_builder import _ANOMALY_FILTER_INTENTS

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
_snmp_polling_task: Optional[asyncio.Task] = None
_iforest_task: Optional[asyncio.Task] = None
_anomaly_scan_task: Optional[asyncio.Task] = None
_ollama_keepwarm_task: Optional[asyncio.Task] = None
snmp_poller_instance: Optional[SnmpPoller] = None

# ── ANOMALY_SCAN_ALL 백그라운드 캐시 ──────────────────────────
_ANOMALY_SCAN_CACHE: Optional[dict] = None
_ANOMALY_SCAN_CACHE_TIME: Optional[datetime] = None
_ANOMALY_SCAN_CACHE_TTL = 300  # 5분

# ── 물 수지 백그라운드 캐시 ──────────────────────────────────
_FLOW_BALANCE_CACHE: Optional[list] = None
_FLOW_BALANCE_CACHE_TIME: Optional[datetime] = None
_FLOW_BALANCE_CACHE_TTL = 1800  # 30분

# ── 용수 흐름 기준선(7일 평균) 캐시 ─────────────────────────
_FLOW_BASELINE_CACHE: dict[str, float] = {}   # tagsn → 7d avg
_FLOW_BASELINE_CACHE_TIME: Optional[datetime] = None
_FLOW_BASELINE_CACHE_TTL = 600  # 10분

# ── 배수지 야간 최소 유량(NMF, 7일 02~04시 최소) 캐시 ──────────
# 7일 창의 느린 지표라 실시간 폴링마다 재계산하지 않고 30분 주기로 갱신한다.
_NIGHT_MIN_FLOW_CACHE: dict[str, float] = {}   # sitename → 야간 최소 유량
_NIGHT_MIN_FLOW_CACHE_TIME: Optional[datetime] = None
_NIGHT_MIN_FLOW_CACHE_TTL = 1800  # 30분

# ── 야간최소유량 일별 사전집계 테이블(tb_night_min_flow_daily) 갱신 주기 ──
# 야간최소유량 트렌드/표준편차 fast-path 의 원천 테이블. pg_cron 부재 환경에서
# 정체(2026-03 사례, E-035)를 막기 위해 백엔드 백그라운드 루프로 일 1회 갱신.
_NIGHT_MIN_FLOW_AGG_TTL = 86400  # 24시간

# =============================================================================
# CSV 내보내기 설정
# =============================================================================
MAX_TABLE_ROWS = 1000  # JSON 응답 최대 행 수 (테이블)
MAX_GRAPH_ROWS = 5000  # JSON 응답 최대 행 수 (그래프/차트)
CSV_EXPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "csv_exports")
CSV_MAX_AGE_SECONDS = 3600  # CSV 파일 보존 기간 (1시간)

# =============================================================================
# 태그 데이터 그룹 (tb_tag_data_group 시드 데이터)
# 2레벨 계층: parent_code=None → 상위 그룹, parent_code!=None → 하위 그룹
# keywords=[] → 상위 전용 (자동분류 대상 아님, 질의 시 children 확장용)
# =============================================================================
TAG_DATA_GROUPS: list[tuple[str, str, str | None, str, list[str]]] = [
    # (group_code, group_name, parent_code, tagtype, keywords)
    # --- Analog Input: 트렌드 대상 ---
    # 유량 계열
    ("FLOW",               "유량",       None,        "Analog Input",  []),
    ("FLOW_INSTANT",       "유량순시",   "FLOW",      "Analog Input",  ["유량순시", "순시유량"]),
    ("FLOW_CUMULATIVE",    "유량적산",   "FLOW",      "Analog Input",  ["유량적산", "적산유량"]),
    ("FLOW_INLET",         "유입유량",   "FLOW",      "Analog Input",  ["유입유량순시", "유입유량적산", "유입순시유량", "유입적산유량", "유입 유량", "유입유량"]),
    ("FLOW_OUTLET",        "유출유량",   "FLOW",      "Analog Input",  ["유출유량순시", "유출유량적산", "유출순시유량", "유출적산유량", "유출 유량", "유출유량"]),
    # 압력 계열
    ("PRESSURE",           "압력",       None,        "Analog Input",  ["압력"]),
    ("PRESSURE_INLET",     "유입압력",   "PRESSURE",  "Analog Input",  ["유입압력"]),
    ("PRESSURE_OUTLET",    "유출압력",   "PRESSURE",  "Analog Input",  ["유출압력"]),
    ("PRESSURE_DISCHARGE", "토출압력",   "PRESSURE",  "Analog Input",  ["토출압력"]),
    # 수위 (리프 — 상위=하위)
    ("WATER_LEVEL",        "수위",       None,        "Analog Input",  ["수위"]),
    # 수질 계열
    ("WATER_QUALITY",      "수질",       None,        "Analog Input",  []),
    ("WATER_QUALITY_PH",   "pH",         "WATER_QUALITY", "Analog Input", ["PH", "ph"]),
    ("WATER_QUALITY_TURB", "탁도",       "WATER_QUALITY", "Analog Input", ["탁도"]),
    ("WATER_QUALITY_CL",   "잔류염소",   "WATER_QUALITY", "Analog Input", ["잔류염소", "염소"]),
    # --- Analog Output: 설정값 ---
    ("ALARM_SETPOINT",     "알람설정값", None,        "Analog Output", ["SET", "설정"]),
    # --- Digital Input: 상태/알람 ---
    ("COMM_ERROR",         "통신이상",   None,        "Digital Input", ["통신이상"]),
    ("POWER_FAULT",        "전원이상",   None,        "Digital Input", ["UPS", "정전", "배터리"]),
    ("EQUIP_FAULT",        "설비고장",   None,        "Digital Input", ["FAULT", "고장"]),
    ("OPERATIONAL",        "운영상태",   None,        "Digital Input", ["동작", "자동", "원격"]),
    ("VALVE_STATUS",       "밸브상태",   None,        "Digital Input", ["밸브"]),
    ("PUMP_STATUS",        "펌프상태",   None,        "Digital Input", ["펌프"]),
]

# =============================================================================
# 인과관계 체인 템플릿 (시설유형별 장비 인과 순서)
# 판단은 Rule-based, SLM은 해석/설명에만 사용
# =============================================================================
CAUSAL_CHAIN_TEMPLATES: list[dict] = [
    # === 가압장: 펌프 → 토출압력 → 유출유량 → [하류 수위] ===
    {
        "facilitytype": "가압장",
        "chain": [
            {"step": 1, "group_code": "PUMP_STATUS",        "role": "cause",  "signal": "ON",      "expected": None,
             "requires": [{"group_code": "VALVE_STATUS", "condition": "OPEN"}, {"group_code": "POWER_FAULT", "condition": "NORMAL"}]},
            {"step": 2, "group_code": "PRESSURE_DISCHARGE",  "role": "effect", "signal": None,      "expected": "RISE", "lag_min": 1, "lag_max": 5},
            {"step": 3, "group_code": "FLOW_OUTLET",         "role": "effect", "signal": None,      "expected": "RISE", "lag_min": 1, "lag_max": 5},
        ],
        "cross_facility": {
            "downstream_group": "WATER_LEVEL",
            "expected": "RISE",
            "lag_min": 5,
            "lag_max": 30,
        },
        "safety_interlocks": [
            {"id": "SI_BOOSTER_01", "label": "토출압력 HH → 펌프 정지",
             "trigger_gc": "PRESSURE_DISCHARGE", "trigger_condition": ">=HH",
             "action_gc": "PUMP_STATUS", "action_expected": "OFF",
             "direction": "self", "lag_min": 0, "lag_max": 1},
            {"id": "SI_BOOSTER_02", "label": "하류 배수지 수위 HH → 펌프 정지",
             "trigger_gc": "WATER_LEVEL", "trigger_condition": ">=HH",
             "action_gc": "PUMP_STATUS", "action_expected": "OFF",
             "direction": "downstream", "target_facilitytype": "배수지", "lag_min": 0, "lag_max": 3},
        ],
        "and_conditions": [
            {"id": "AND_BOOSTER_01", "label": "펌프ON + 밸브OPEN + 전원정상 → 유출유량 > 0",
             "conditions": [
                 {"group_code": "PUMP_STATUS", "condition": "ON"},
                 {"group_code": "VALVE_STATUS", "condition": "OPEN"},
                 {"group_code": "POWER_FAULT", "condition": "NORMAL"},
             ],
             "effect_gc": "FLOW_OUTLET", "effect_expected": "NONZERO", "lag_min": 1, "lag_max": 5},
        ],
        "reverse_diagnostics": [
            {"id": "RD_BOOSTER_01", "label": "유출유량 0 → 밸브 폐쇄 또는 펌프 정지 확인",
             "observation_gc": "FLOW_OUTLET", "observation_condition": "ZERO",
             "diagnose_gc": "PUMP_STATUS", "diagnose_expected": "OFF", "direction": "self"},
        ],
        "propagation": {"max_hops": 3, "forward_enabled": True, "backward_enabled": True},
    },
    # === 배수지: 수위 감시 → 밸브 개방 → 유출유량 → [하류 유입유량] ===
    {
        "facilitytype": "배수지",
        "chain": [
            {"step": 1, "group_code": "WATER_LEVEL",  "role": "trigger", "signal": "MONITOR", "expected": None},
            {"step": 2, "group_code": "VALVE_STATUS",  "role": "cause",   "signal": "OPEN",    "expected": None},
            {"step": 3, "group_code": "FLOW_OUTLET",   "role": "effect",  "signal": None,      "expected": "RISE", "lag_min": 0, "lag_max": 3},
        ],
        "cross_facility": {
            "downstream_group": "FLOW_INLET",
            "expected": "RISE",
            "lag_min": 5,
            "lag_max": 20,
        },
        "safety_interlocks": [
            {"id": "SI_RESERVOIR_01", "label": "수위 HH → 상류 가압장 펌프 정지 요청",
             "trigger_gc": "WATER_LEVEL", "trigger_condition": ">=HH",
             "action_gc": "PUMP_STATUS", "action_expected": "OFF",
             "direction": "upstream", "target_facilitytype": "가압장", "lag_min": 0, "lag_max": 3},
            {"id": "SI_RESERVOIR_02", "label": "수위 LL → 밸브 폐쇄 확인",
             "trigger_gc": "WATER_LEVEL", "trigger_condition": "<=LL",
             "action_gc": "VALVE_STATUS", "action_expected": "CLOSED",
             "direction": "self", "lag_min": 0, "lag_max": 2},
        ],
        "and_conditions": [
            {"id": "AND_RESERVOIR_01", "label": "밸브OPEN → 유출유량 > 0",
             "conditions": [{"group_code": "VALVE_STATUS", "condition": "OPEN"}],
             "effect_gc": "FLOW_OUTLET", "effect_expected": "NONZERO", "lag_min": 0, "lag_max": 3},
        ],
        "reverse_diagnostics": [
            {"id": "RD_RESERVOIR_01", "label": "수위 하강 + 유출유량 0 → 누수 의심",
             "observation_gc": "WATER_LEVEL", "observation_condition": "FALLING",
             "diagnose_gc": "FLOW_OUTLET", "diagnose_expected": "ZERO", "direction": "self"},
        ],
        "propagation": {"max_hops": 3, "forward_enabled": True, "backward_enabled": True},
    },
    # === 감압시설: 유입압력 감시 → 밸브 조절 → 유출압력 안정 → [하류 유입압력] ===
    {
        "facilitytype": "감압시설",
        "chain": [
            {"step": 1, "group_code": "PRESSURE_INLET",  "role": "trigger", "signal": "MONITOR",  "expected": None},
            {"step": 2, "group_code": "VALVE_STATUS",     "role": "cause",   "signal": "REGULATE", "expected": None},
            {"step": 3, "group_code": "PRESSURE_OUTLET",  "role": "effect",  "signal": None,       "expected": "STABLE", "lag_min": 0, "lag_max": 3},
        ],
        "cross_facility": {
            "downstream_group": "PRESSURE_INLET",
            "expected": "STABLE",
            "lag_min": 1,
            "lag_max": 10,
        },
        "safety_interlocks": [
            {"id": "SI_PRV_01", "label": "유출압력 HH → 밸브 추가 개방",
             "trigger_gc": "PRESSURE_OUTLET", "trigger_condition": ">=HH",
             "action_gc": "VALVE_STATUS", "action_expected": "REGULATE",
             "direction": "self", "lag_min": 0, "lag_max": 2},
        ],
        "and_conditions": [],
        "reverse_diagnostics": [],
        "propagation": {"max_hops": 2, "forward_enabled": True, "backward_enabled": True},
    },
    # === 소블록: 유량순시 감시 → 압력 상관 ===
    {
        "facilitytype": "소블록",
        "chain": [
            {"step": 1, "group_code": "FLOW_INSTANT",  "role": "trigger", "signal": "MONITOR",   "expected": None},
            {"step": 2, "group_code": "PRESSURE",       "role": "effect",  "signal": None,        "expected": "CORRELATE", "lag_min": 0, "lag_max": 10},
        ],
        "cross_facility": None,
        "safety_interlocks": [],
        "and_conditions": [],
        "reverse_diagnostics": [],
        "propagation": {"max_hops": 1, "forward_enabled": False, "backward_enabled": True},
    },
    # === 소소블록: 소블록과 동일 ===
    {
        "facilitytype": "소소블록",
        "chain": [
            {"step": 1, "group_code": "FLOW_INSTANT",  "role": "trigger", "signal": "MONITOR",   "expected": None},
            {"step": 2, "group_code": "PRESSURE",       "role": "effect",  "signal": None,        "expected": "CORRELATE", "lag_min": 0, "lag_max": 10},
        ],
        "cross_facility": None,
        "safety_interlocks": [],
        "and_conditions": [],
        "reverse_diagnostics": [],
        "propagation": {"max_hops": 1, "forward_enabled": False, "backward_enabled": True},
    },
]

# facilitytype → template 빠른 조회
_CAUSAL_TEMPLATE_MAP: dict[str, dict] = {t["facilitytype"]: t for t in CAUSAL_CHAIN_TEMPLATES}

# =============================================================================
# 설비↔태그 자동 매핑 규칙
# None = 시설 내 전체 태그, list = 해당 group_code만, 키 없음 = 매핑 안 함
# =============================================================================
_EQUIPMENT_GROUP_RULES: dict[str, list[str] | None] = {
    "가압펌프": ["PUMP_STATUS", "PRESSURE_DISCHARGE", "PRESSURE_INLET",
                "PRESSURE_OUTLET", "FLOW_OUTLET", "FLOW_INSTANT",
                "EQUIP_FAULT", "OPERATIONAL", "COMM_ERROR"],
    "유량계": ["FLOW_INSTANT", "FLOW_CUMULATIVE", "FLOW_INLET", "FLOW_OUTLET"],
    "PLC": None,       # 시설 내 전체 태그
    "LTE 모뎀": None,  # 시설 내 전체 태그
}
# 가압펌프 1:1 매칭용 regex — datainfo에서 "가압펌프N" 번호 추출
_PUMP_NUM_RE = re.compile(r"가압펌프(\d+)")

# =============================================================================
# 인과 인덱스 (런타임 캐시, _build_causal_index 후 채워짐)
# key: (sitename, facilitytype)
# value: {"template", "tag_map", "upstream", "downstream"}
# =============================================================================
_CAUSAL_INDEX: dict[tuple[str, str], dict] = {}

# group_code → children group_codes 매핑 (런타임 캐시)
_GROUP_CHILDREN: dict[str, list[str]] = {}
# group_code → group_id 매핑 (런타임 캐시, _auto_classify_tags 후 채워짐)
_GROUP_CODE_TO_ID: dict[str, int] = {}


def _build_group_children_cache():
    """TAG_DATA_GROUPS에서 parent→children 매핑을 빌드한다."""
    _GROUP_CHILDREN.clear()
    for code, _, parent, _, _ in TAG_DATA_GROUPS:
        if parent:
            _GROUP_CHILDREN.setdefault(parent, []).append(code)


def _resolve_group_codes(group_code: str) -> list[str]:
    """group_code가 상위 그룹이면 하위 전체를 반환, 아니면 [자신]."""
    children = _GROUP_CHILDREN.get(group_code)
    if children:
        return children
    return [group_code]


def _auto_classify_tags(conn) -> int:
    """태그 자동분류: TAG_DATA_GROUPS → tb_tag_data_group UPSERT → tb_tag_group_map 갱신.

    longest-keyword-first 전략으로 datainfo를 매칭한다.
    반환: 분류된 태그 수
    """
    cur = conn.cursor()
    cur.execute("SET lock_timeout = '10000'")  # 10초 초과 시 LockNotAvailable 예외 → lifespan catch로 처리

    # 1. tb_tag_data_group UPSERT
    for idx, (code, name, parent, tagtype, keywords) in enumerate(TAG_DATA_GROUPS):
        cur.execute("""
            INSERT INTO tb_tag_data_group (group_code, group_name, parent_code, tagtype, keywords, display_order)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s)
            ON CONFLICT (group_code) DO UPDATE SET
                group_name = EXCLUDED.group_name,
                parent_code = EXCLUDED.parent_code,
                tagtype = EXCLUDED.tagtype,
                keywords = EXCLUDED.keywords,
                display_order = EXCLUDED.display_order
        """, (code, name, parent, tagtype, json.dumps(keywords, ensure_ascii=False), idx))
    conn.commit()

    # group_code → group_id 캐시 로드
    cur.execute("SELECT group_code, group_id FROM tb_tag_data_group")
    _GROUP_CODE_TO_ID.clear()
    for gc, gid in cur.fetchall():
        _GROUP_CODE_TO_ID[gc] = gid

    # 2. tb_tag_info 전체 로드
    cur.execute("SELECT tagsn, datainfo FROM tb_tag_info")
    all_tags = cur.fetchall()

    # 3. 키워드 정렬: longest-first (긴 키워드가 먼저 매칭되어야 정확)
    keyword_pairs: list[tuple[str, str]] = []  # (keyword, group_code)
    for code, _, _, _, keywords in TAG_DATA_GROUPS:
        for kw in keywords:
            keyword_pairs.append((kw, code))
    keyword_pairs.sort(key=lambda x: len(x[0]), reverse=True)

    # 4. 태그별 그룹 매칭
    mappings: list[tuple[str, int]] = []  # (tagsn, group_id)
    for tagsn, datainfo in all_tags:
        if not datainfo:
            continue
        matched_code = None
        for kw, code in keyword_pairs:
            if kw.upper() in datainfo.upper():
                matched_code = code
                break
        if matched_code and matched_code in _GROUP_CODE_TO_ID:
            mappings.append((tagsn, _GROUP_CODE_TO_ID[matched_code]))

    # 5. tb_tag_group_map 갱신 (DELETE + bulk INSERT)
    cur.execute("DELETE FROM tb_tag_group_map")
    if mappings:
        from psycopg2.extras import execute_values
        execute_values(
            cur,
            "INSERT INTO tb_tag_group_map (tagsn, group_id) VALUES %s",
            mappings,
            page_size=500,
        )
    conn.commit()
    cur.close()

    # children 캐시 빌드
    _build_group_children_cache()

    return len(mappings)


def _build_causal_index(conn) -> int:
    """인과 인덱스 구축: 시설별 템플릿 + 태그 매핑 + 상류/하류 관계.

    _auto_classify_tags() 이후 호출해야 한다 (_GROUP_CODE_TO_ID 필요).
    반환: 인덱싱된 시설 수
    """
    _CAUSAL_INDEX.clear()
    cur = conn.cursor()

    # 1) 시설별 group_code → tagsn[] 매핑
    #    tb_tag_info JOIN tb_tag_group_map JOIN tb_tag_data_group
    cur.execute("""
        SELECT t.sitename, t.facilitytype, g.group_code, t.tagsn
        FROM tb_tag_info t
        JOIN tb_tag_group_map gm ON t.tagsn = gm.tagsn
        JOIN tb_tag_data_group g ON gm.group_id = g.group_id
        WHERE t.sitename IS NOT NULL AND t.facilitytype IS NOT NULL
        ORDER BY t.sitename, t.facilitytype, g.group_code
    """)
    # {(sitename, facilitytype): {group_code: [tagsn, ...]}}
    site_tag_map: dict[tuple[str, str], dict[str, list[str]]] = {}
    for sn, ft, gc, tsn in cur.fetchall():
        key = (sn, ft)
        site_tag_map.setdefault(key, {}).setdefault(gc, []).append(tsn)

    # 2) flow_map 로드 → upstream/downstream 관계
    cur.execute("""
        SELECT upstream_sitename, upstream_facilitytype,
               downstream_sitename, downstream_facilitytype
        FROM tb_facility_flow_map
    """)
    # {(sn, ft): [downstream (sn, ft), ...]}
    downstream_map: dict[tuple[str, str], list[tuple[str, str]]] = {}
    upstream_map: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for usn, uft, dsn, dft in cur.fetchall():
        ukey, dkey = (usn, uft), (dsn, dft)
        downstream_map.setdefault(ukey, []).append(dkey)
        upstream_map.setdefault(dkey, []).append(ukey)

    cur.close()

    # 3) 인덱스 구축 — tag_map에 체인 step group_code 폴백 매핑 포함
    count = 0
    for (sn, ft), tag_map in site_tag_map.items():
        template = _CAUSAL_TEMPLATE_MAP.get(ft)
        if not template:
            continue
        # 체인 step의 group_code가 tag_map에 없으면 같은 부모의 형제 그룹에서 폴백
        resolved_tag_map = dict(tag_map)
        for step in template.get("chain", []):
            gc = step["group_code"]
            if gc in resolved_tag_map:
                continue
            # 부모 그룹 찾기 → 형제 그룹의 태그를 폴백
            parent = None
            for code, _, par, _, _ in TAG_DATA_GROUPS:
                if code == gc:
                    parent = par
                    break
            if parent:
                siblings = _GROUP_CHILDREN.get(parent, [])
                fallback_tags = []
                for sib in siblings:
                    if sib != gc and sib in tag_map:
                        fallback_tags.extend(tag_map[sib])
                if fallback_tags:
                    resolved_tag_map[gc] = fallback_tags

        _CAUSAL_INDEX[(sn, ft)] = {
            "template": template,
            "tag_map": resolved_tag_map,
            "upstream": upstream_map.get((sn, ft), []),
            "downstream": downstream_map.get((sn, ft), []),
        }
        count += 1

    # 4) 오버라이드 로딩 — tb_causal_chain_override가 있으면 체인 교체
    try:
        cur2 = conn.cursor()
        cur2.execute("""
            SELECT sitename, facilitytype, zone, chain_json, cross_facility_json
            FROM tb_causal_chain_override
            ORDER BY sitename, facilitytype, zone NULLS FIRST
        """)
        override_count = 0
        for o_sn, o_ft, o_zone, o_chain, o_cross in cur2.fetchall():
            if o_zone:
                # 구역별 오버라이드: 3-tuple 키
                base = _CAUSAL_INDEX.get((o_sn, o_ft))
                if base:
                    zone_entry = {
                        "template": {**base["template"], "chain": o_chain},
                        "tag_map": base["tag_map"],  # 구역 필터링은 _detect_zones에서
                        "upstream": base["upstream"],
                        "downstream": base["downstream"],
                    }
                    if o_cross:
                        zone_entry["template"] = {**zone_entry["template"], "cross_facility": o_cross}
                    _CAUSAL_INDEX[(o_sn, o_ft, o_zone)] = zone_entry
                    override_count += 1
            else:
                # 전체 오버라이드: 기존 2-tuple 키의 template 교체
                key2 = (o_sn, o_ft)
                if key2 in _CAUSAL_INDEX:
                    orig = _CAUSAL_INDEX[key2]
                    merged_template = {**orig["template"], "chain": o_chain}
                    if o_cross:
                        merged_template["cross_facility"] = o_cross
                    _CAUSAL_INDEX[key2] = {**orig, "template": merged_template}
                    override_count += 1
        cur2.close()
        if override_count:
            logger.info("인과 오버라이드 적용: %d건", override_count)
    except Exception as e:
        logger.debug("인과 오버라이드 로딩 스킵: %s", e)

    logger.info("인과 인덱스 구축 완료: %d개 시설 매핑", count)
    return count


import re
_ZONE_PATTERN = re.compile(r'(\d)[지구역]')


# =============================================================================
# 설비↔태그 자동 매핑
# =============================================================================

def _auto_map_equipment_tags(conn, *, dry_run: bool = False) -> dict:
    """설비↔태그 자동 매핑: _EQUIPMENT_GROUP_RULES 기반 그룹 레벨 매핑.

    가압펌프는 datainfo에서 "가압펌프N" 패턴으로 1:1 매칭,
    번호 없는 태그는 시설 내 모든 가압펌프에 공유 매핑.
    dry_run=True이면 INSERT 하지 않고 결과만 반환.
    """
    cur = conn.cursor()

    # 1) 설비 로드 — 매핑 대상만 필터
    cur.execute("""
        SELECT equipment_id, sitename, facilitytype, equipmenttype
        FROM tb_equipment_info
        WHERE equipmenttype IN %s
        ORDER BY sitename, facilitytype, equipmenttype, equipment_id
    """, (tuple(_EQUIPMENT_GROUP_RULES.keys()),))
    equip_rows = cur.fetchall()
    if not equip_rows:
        cur.close()
        return {"total_links": 0, "by_type": {}, "dry_run": dry_run}

    # {(sitename, ft): {equipmenttype: [equipment_id, ...]}}
    site_equip: dict[tuple[str, str], dict[str, list[str]]] = {}
    for eid, sn, ft, et in equip_rows:
        site_equip.setdefault((sn, ft), {}).setdefault(et, []).append(eid)

    # 2) 태그 + group_code 로드
    cur.execute("""
        SELECT t.tagsn, t.sitename, t.facilitytype, t.datainfo,
               COALESCE(g.group_code, '') as group_code
        FROM tb_tag_info t
        LEFT JOIN tb_tag_group_map gm ON t.tagsn = gm.tagsn
        LEFT JOIN tb_tag_data_group g ON gm.group_id = g.group_id
        WHERE t.sitename IS NOT NULL AND t.facilitytype IS NOT NULL
        ORDER BY t.sitename, t.facilitytype
    """)
    # {(sitename, ft): [(tagsn, datainfo, group_code), ...]}
    site_tags: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    for tsn, sn, ft, di, gc in cur.fetchall():
        site_tags.setdefault((sn, ft), []).append((tsn, di or "", gc))

    # 3) 매핑 생성
    links: list[tuple[str, str]] = []  # (equipment_id, tagsn)
    by_type: dict[str, int] = {}

    for (sn, ft), type_equips in site_equip.items():
        tags = site_tags.get((sn, ft), [])
        if not tags:
            continue

        for et, eids in type_equips.items():
            rules = _EQUIPMENT_GROUP_RULES.get(et)
            if rules is None:
                # None → 시설 내 전체 태그
                for eid in eids:
                    for tsn, _, _ in tags:
                        links.append((eid, tsn))
                by_type[et] = by_type.get(et, 0) + len(eids) * len(tags)
            elif et == "가압펌프":
                _map_pumps(eids, tags, rules, links, by_type)
            else:
                # list → group_code 매칭만, 모든 장비에 동일 매핑
                resolved = _resolve_group_list(rules)
                matched = [(tsn, di, gc) for tsn, di, gc in tags if gc in resolved]
                for eid in eids:
                    for tsn, _, _ in matched:
                        links.append((eid, tsn))
                by_type[et] = by_type.get(et, 0) + len(eids) * len(matched)

    # 4) INSERT (dry_run이 아닌 경우)
    if not dry_run and links:
        from psycopg2.extras import execute_values
        execute_values(
            cur,
            "INSERT INTO tb_equipment_tag_map (equipment_id, tagsn) VALUES %s ON CONFLICT DO NOTHING",
            links,
            page_size=1000,
        )
        conn.commit()

    cur.close()
    return {"total_links": len(links), "by_type": by_type, "dry_run": dry_run}


def _resolve_group_list(rules: list[str]) -> set[str]:
    """group_code 리스트에서 상위 그룹은 하위 전체를 포함하여 반환."""
    resolved: set[str] = set()
    for gc in rules:
        resolved.update(_resolve_group_codes(gc))
    return resolved


def _map_pumps(
    eids: list[str],
    tags: list[tuple[str, str, str]],
    rules: list[str],
    links: list[tuple[str, str]],
    by_type: dict[str, int],
) -> None:
    """가압펌프 특수 매핑: 번호 있는 태그는 1:1, 없는 태그는 모든 펌프에 공유."""
    resolved = _resolve_group_list(rules)

    # 펌프번호 → equipment_id (정렬 순서 = 1번, 2번, ...)
    pump_by_num: dict[int, str] = {}
    for idx, eid in enumerate(eids, start=1):
        pump_by_num[idx] = eid

    count = 0
    for tsn, di, gc in tags:
        if gc not in resolved:
            continue
        m = _PUMP_NUM_RE.search(di)
        if m:
            # 1:1 매칭: "가압펌프N" → N번째 equipment
            num = int(m.group(1))
            eid = pump_by_num.get(num)
            if eid:
                links.append((eid, tsn))
                count += 1
        else:
            # 공유 태그: "가압펌프" 포함하지만 번호 없음 → 모든 펌프에
            if "가압펌프" in di:
                for eid in eids:
                    links.append((eid, tsn))
                    count += 1
            else:
                # 펌프 관련 아닌 일반 group_code 매칭 태그 → 모든 펌프에 공유
                for eid in eids:
                    links.append((eid, tsn))
                    count += 1

    by_type["가압펌프"] = by_type.get("가압펌프", 0) + count


def _detect_zones(conn, sitename: str, facilitytype: str) -> list[dict]:
    """배수지 태그에서 구역(1지, 2지...) 패턴을 감지한다."""
    if facilitytype != "배수지":
        return []
    cur = conn.cursor()
    cur.execute("""
        SELECT t.tagsn, t.datainfo, g.group_code
        FROM tb_tag_info t
        JOIN tb_tag_group_map gm ON t.tagsn = gm.tagsn
        JOIN tb_tag_data_group g ON gm.group_id = g.group_id
        WHERE t.sitename = %s AND t.facilitytype = %s
    """, (sitename, facilitytype))
    zone_tags: dict[str, dict[str, list[str]]] = {}
    for tagsn, datainfo, gc in cur.fetchall():
        m = _ZONE_PATTERN.search(datainfo or "")
        if m:
            zone = f"{m.group(1)}지"
            zone_tags.setdefault(zone, {}).setdefault(gc, []).append(tagsn)
    cur.close()
    result = []
    for zone in sorted(zone_tags.keys()):
        groups = zone_tags[zone]
        result.append({
            "zone": zone,
            "tag_count": sum(len(v) for v in groups.values()),
            "group_codes": list(groups.keys()),
        })
    return result


def _get_causal_info(sitename: str, facilitytype: str, zone: str | None = None) -> dict | None:
    """인과 인덱스에서 시설+구역 조회. zone 지정 시 3-tuple 우선, 없으면 2-tuple 폴백."""
    if zone:
        key3 = (sitename, facilitytype, zone)
        if key3 in _CAUSAL_INDEX:
            return _CAUSAL_INDEX[key3]
    return _CAUSAL_INDEX.get((sitename, facilitytype))


# _GC_KEYWORDS 폴백용 (인덱스에 없는 시설)
_FALLBACK_GC_KEYWORDS = [
    ("유입압력", "PRESSURE_INLET"), ("유출압력", "PRESSURE_OUTLET"),
    ("토출압력", "PRESSURE_DISCHARGE"), ("유입유량", "FLOW_INLET"),
    ("유출유량", "FLOW_OUTLET"), ("유량순시", "FLOW_INSTANT"),
    ("유량적산", "FLOW_CUMULATIVE"),
    ("수위", "WATER_LEVEL"), ("압력", "PRESSURE"), ("유량", "FLOW"),
    ("밸브", "VALVE_STATUS"), ("펌프", "PUMP_STATUS"),
]


def _resolve_group_code_for_tagsn(
    sitename: str, facilitytype: str, tagsn: str, datainfo: str,
) -> str | None:
    """tagsn의 group_code를 _CAUSAL_INDEX tag_map에서 역조회한다.
    인덱스에 없으면 datainfo 키워드 폴백.
    """
    info = _CAUSAL_INDEX.get((sitename, facilitytype))
    if info:
        tag_map = info.get("tag_map", {})
        for gc, tagsn_list in tag_map.items():
            if tagsn in tagsn_list:
                return gc
    # 폴백: datainfo 키워드 매칭
    for kw, code in _FALLBACK_GC_KEYWORDS:
        if kw in datainfo:
            return code
    return None


def _rebuild_causal_index_entry(sitename: str, facilitytype: str):
    """단일 시설의 인과 인덱스 항목을 재구축한다 (PUT/DELETE 후 호출)."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # 태그 맵 재조회
        cur.execute("""
            SELECT g.group_code, t.tagsn
            FROM tb_tag_info t
            JOIN tb_tag_group_map gm ON t.tagsn = gm.tagsn
            JOIN tb_tag_data_group g ON gm.group_id = g.group_id
            WHERE t.sitename = %s AND t.facilitytype = %s
        """, (sitename, facilitytype))
        tag_map: dict[str, list[str]] = {}
        for gc, tsn in cur.fetchall():
            tag_map.setdefault(gc, []).append(tsn)

        template = _CAUSAL_TEMPLATE_MAP.get(facilitytype)
        if not template or not tag_map:
            cur.close()
            conn.close()
            return

        # upstream/downstream 재조회
        cur.execute("""
            SELECT upstream_sitename, upstream_facilitytype,
                   downstream_sitename, downstream_facilitytype
            FROM tb_facility_flow_map
            WHERE (upstream_sitename = %s AND upstream_facilitytype = %s)
               OR (downstream_sitename = %s AND downstream_facilitytype = %s)
        """, (sitename, facilitytype, sitename, facilitytype))
        upstream = []
        downstream = []
        for usn, uft, dsn, dft in cur.fetchall():
            if usn == sitename and uft == facilitytype:
                downstream.append((dsn, dft))
            else:
                upstream.append((usn, uft))

        # 기본 항목 갱신
        _CAUSAL_INDEX[(sitename, facilitytype)] = {
            "template": template,
            "tag_map": tag_map,
            "upstream": upstream,
            "downstream": downstream,
        }

        # 오버라이드 적용
        cur.execute("""
            SELECT zone, chain_json, cross_facility_json
            FROM tb_causal_chain_override
            WHERE sitename = %s AND facilitytype = %s
            ORDER BY zone NULLS FIRST
        """, (sitename, facilitytype))
        base = _CAUSAL_INDEX[(sitename, facilitytype)]
        for o_zone, o_chain, o_cross in cur.fetchall():
            if o_zone:
                zone_entry = {
                    "template": {**base["template"], "chain": o_chain},
                    "tag_map": tag_map,
                    "upstream": upstream,
                    "downstream": downstream,
                }
                if o_cross:
                    zone_entry["template"] = {**zone_entry["template"], "cross_facility": o_cross}
                _CAUSAL_INDEX[(sitename, facilitytype, o_zone)] = zone_entry
            else:
                merged = {**base["template"], "chain": o_chain}
                if o_cross:
                    merged["cross_facility"] = o_cross
                _CAUSAL_INDEX[(sitename, facilitytype)] = {**base, "template": merged}

        cur.close()
    except Exception as e:
        logger.warning("인과 인덱스 항목 재구축 실패: %s", e)
    finally:
        if conn:
            conn.close()


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
    """백그라운드: 서버 시작 5초 후 첫 실행, 이후 24시간마다 현장 프로파일링"""
    await asyncio.sleep(5)
    while True:
        try:
            logger.info("현장 프로파일링 시작...")
            await asyncio.to_thread(site_profiler.run_daily_profiling)
            profile_count = len(site_profiler.profiles) if site_profiler and site_profiler.profiles else 0
            logger.info(f"현장 프로파일링 완료: {profile_count}개 현장 분류")
        except Exception as e:
            logger.error(f"현장 프로파일링 실패: {e}")
        await asyncio.sleep(86400)


async def _snmp_polling_loop():
    """백그라운드: 서버 시작 30초 후 첫 실행, 이후 3분마다 SNMP 폴링"""
    from snmp_poller import POLL_INTERVAL
    await asyncio.sleep(30)
    while True:
        try:
            count = await asyncio.to_thread(snmp_poller_instance.poll_all)
            logger.info(f"SNMP 폴링 완료: {count}개 스위치")
        except Exception as e:
            logger.error(f"SNMP 폴링 실패: {e}")
        await asyncio.sleep(POLL_INTERVAL)


async def _iforest_training_loop():
    """백그라운드: 서버 시작 10초 후 첫 실행, 이후 24시간마다 IForest 학습.

    기동 직후 디스크 pkl 에서 직전 모델을 로드해 무탐지 사각을 제거한다.
    (사양: docs/iforest-eval-spec.md §3.1)
    """
    from anomaly_iforest import RETRAIN_INTERVAL_HOURS
    try:
        await asyncio.to_thread(iforest_manager.load_from_disk)
    except Exception as e:
        logger.warning(f"IForest 디스크 로드 건너뜀: {e}")
    await asyncio.sleep(10)
    while True:
        try:
            _profiles = site_profiler.profiles if site_profiler and site_profiler.profiles else None
            logger.info("IForest v2 백그라운드 학습 시작...")
            await asyncio.to_thread(iforest_manager.train_all, get_db_connection, site_profiles=_profiles)
            status = iforest_manager.get_status()
            logger.info(
                "IForest v2 학습 완료: Tier-1 %d개 시설, Tier-2 %d개 태그",
                len(status["tier1_facilities"]), status["tier2_tag_count"],
            )
        except Exception as e:
            logger.error(f"IForest 백그라운드 학습 실패: {e}")
        await asyncio.sleep(RETRAIN_INTERVAL_HOURS * 3600)


async def _anomaly_scan_cache_loop():
    """백그라운드: 프로파일링 완료 대기 후 첫 실행, 이후 5분마다 ANOMALY_SCAN_ALL 전체 캐시 갱신.

    전체 파이프라인(SQL + IForest + 교차검증)을 미리 실행하여 캐시에 저장.
    사용자 요청 시 캐시 반환 (<1s).
    """
    global _ANOMALY_SCAN_CACHE, _ANOMALY_SCAN_CACHE_TIME
    # 프로파일링 완료 대기 (최대 30초, 3초 간격 체크) — 초기 캐시 빌드 빠르게 시작
    for _ in range(10):
        if site_profiler and site_profiler.profiles:
            break
        await asyncio.sleep(3)
    if not (site_profiler and site_profiler.profiles):
        logger.warning("ANOMALY_SCAN_ALL: 프로파일 미완성 상태로 캐시 빌드 시작")
    while True:
        try:
            t0 = datetime.now()
            result = await asyncio.to_thread(_compute_anomaly_scan_all)
            elapsed = (datetime.now() - t0).total_seconds()
            if result:
                _ANOMALY_SCAN_CACHE = result
                _ANOMALY_SCAN_CACHE_TIME = datetime.now()
                row_count = len(result.get("rows", []))
                logger.info(f"ANOMALY_SCAN_ALL 캐시 갱신: {row_count}행, {elapsed:.1f}초")
                await asyncio.sleep(_ANOMALY_SCAN_CACHE_TTL)
            else:
                logger.warning("ANOMALY_SCAN_ALL 캐시 갱신 실패: 빈 결과 — 30초 후 재시도")
                await asyncio.sleep(30)
        except Exception as e:
            logger.error(f"ANOMALY_SCAN_ALL 캐시 갱신 실패: {e}")
            await asyncio.sleep(30)


def _filter_flow_balance_edges(edges: list, sitename: str | None) -> list:
    """sitename이 주어지면 해당 시설이 upstream 또는 downstream에 포함된 엣지만 반환."""
    if not sitename or sitename in ("%%", "%"):
        return edges
    filtered = []
    for e in edges:
        if e.get("upstream_sitename") == sitename:
            filtered.append(e)
            continue
        ds_names = [d.get("sitename", "") for d in e.get("downstream_facilities", [])]
        if sitename in ds_names:
            filtered.append(e)
    return filtered  # 매칭 없으면 빈 리스트


async def _leak_cusum_scan_loop():
    """백그라운드: 서버 시작 10분 후 첫 실행, 이후 6시간마다 CUSUM 누수 스캔.

    야간최소유량 데이터를 사용해 소블록 단위로 CUSUM 분석 후
    leak_status="누수의심" 태그를 `tb_leak_cusum_alert`에 저장.
    중복 방지: 24시간 이내 동일 tagsn 알림은 skip.
    """
    # 초기 지연: 다른 캐시·임베딩 빌드 이후 실행
    await asyncio.sleep(600)

    def _night_query(sitename: str, facilitytype: str, days: int):
        from sql_executor import _execute_night_min_flow_query
        to_ts = datetime.now().strftime("%Y-%m-%d")
        from_ts = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        return _execute_night_min_flow_query(sitename, facilitytype, from_ts, to_ts)

    while True:
        try:
            stats = await asyncio.to_thread(
                run_leak_cusum_scan,
                _night_query,
                "R01",   # region
                "",      # sitename_like (전체)
                "소블록",  # facilitytype
                90,      # days
                24,      # dedupe_hours
            )
            if stats.get("inserted", 0) > 0:
                logger.info(
                    f"[누수CUSUM] 스캔 완료: 분석 {stats['scanned_tags']}개, "
                    f"탐지 {stats['detected']}건, 신규 저장 {stats['inserted']}건"
                )
        except Exception as e:
            logger.error(f"[누수CUSUM] 스캔 루프 오류: {e}")
        # 6시간 주기
        await asyncio.sleep(6 * 3600)


async def _flow_balance_cache_loop():
    """백그라운드: 서버 시작 30초 후 첫 실행, 이후 30분마다 물 수지 캐시 갱신."""
    global _FLOW_BALANCE_CACHE, _FLOW_BALANCE_CACHE_TIME
    await asyncio.sleep(30)
    while True:
        try:
            from flow_balance import compute_flow_balance_all
            t0 = datetime.now()
            tag_info = await asyncio.to_thread(_get_tag_datainfo_cache)
            edges = await asyncio.to_thread(
                compute_flow_balance_all,
                _query_flow_timeseries, _CAUSAL_INDEX, tag_info,
            )
            elapsed = (datetime.now() - t0).total_seconds()
            _FLOW_BALANCE_CACHE = edges
            _FLOW_BALANCE_CACHE_TIME = datetime.now()
            imbalance_count = sum(1 for e in edges if e["grade"] != "정상")
            logger.info(f"물 수지 캐시 갱신: {len(edges)}엣지, 불균형 {imbalance_count}건, {elapsed:.1f}초")
        except Exception as e:
            logger.error(f"물 수지 캐시 갱신 실패: {e}")
        await asyncio.sleep(_FLOW_BALANCE_CACHE_TTL)


async def _flow_baseline_cache_loop():
    """백그라운드: 서버 시작 120초 후 첫 실행, 이후 10분마다 7일 평균 기준선 갱신."""
    global _FLOW_BASELINE_CACHE, _FLOW_BASELINE_CACHE_TIME
    await asyncio.sleep(120)
    while True:
        try:
            t0 = datetime.now()
            result = await asyncio.to_thread(_compute_flow_baselines)
            if result:
                _FLOW_BASELINE_CACHE = result
                _FLOW_BASELINE_CACHE_TIME = datetime.now()
                logger.info(f"용수 흐름 기준선 캐시 갱신: {len(result)}태그, "
                            f"{(datetime.now() - t0).total_seconds():.1f}초")
        except Exception as e:
            logger.error(f"용수 흐름 기준선 캐시 갱신 실패: {e}")
        await asyncio.sleep(_FLOW_BASELINE_CACHE_TTL)


def _compute_flow_baselines() -> dict[str, float]:
    """7일간 동일 요일·시간대(±1h) 평균값을 태그별로 계산."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        now = datetime.now()
        _from = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        _to = now.strftime("%Y-%m-%d %H:%M:%S")

        # 모니터링 대상 Analog Input 태그 전체
        cur.execute("""
            SELECT m.tagsn FROM tb_tag_group_map m
            JOIN tb_tag_data_group g ON m.group_id = g.group_id
            WHERE g.group_code IN (
                'FLOW_OUTLET','FLOW_INSTANT','FLOW_INLET','FLOW_CUMULATIVE',
                'WATER_LEVEL','PRESSURE_OUTLET','PRESSURE_INLET',
                'PRESSURE_DISCHARGE','PRESSURE'
            )
        """)
        tagsns = [r[0] for r in cur.fetchall()]
        if not tagsns:
            return {}

        chunks = get_chunks_for_range(cur, _from, _to)
        if not chunks:
            return {}

        # 동일 요일·시간대(±1h) 필터링을 위한 현재 요일/시간
        cur_dow = now.weekday()   # 0=Mon
        cur_hour = now.hour

        # 청크별 집계: 동일 요일, 시간대(±1h) 필터
        baselines: dict[str, list[float]] = {}
        for chunk in chunks:
            try:
                cur.execute(f"""
                    SELECT tagsn, AVG(val)
                    FROM {chunk}
                    WHERE tagsn = ANY(%s)
                      AND logtime >= %s AND logtime < %s
                      AND val IS NOT NULL
                      AND EXTRACT(DOW FROM logtime) = %s
                      AND EXTRACT(HOUR FROM logtime) BETWEEN %s AND %s
                    GROUP BY tagsn
                """, (tagsns, _from, _to,
                      cur_dow, max(0, cur_hour - 1), min(23, cur_hour + 1)))
                for tsn, avg_val in cur.fetchall():
                    if avg_val is not None and avg_val > 0.01:
                        baselines.setdefault(tsn, []).append(float(avg_val))
            except Exception as e:
                logger.error(f"기준선 청크 조회 실패: {e}")
                conn.rollback()
                continue

        # 청크별 평균의 전체 평균
        result: dict[str, float] = {}
        for tsn, vals in baselines.items():
            result[tsn] = round(sum(vals) / len(vals), 2)
        return result
    except Exception as e:
        logger.error(f"기준선 계산 실패: {e}")
        conn.rollback()
        return {}
    finally:
        cur.close()
        conn.close()


def _compute_night_min_flows() -> dict[str, float]:
    """배수지별 야간(02~04시) 최소 유출유량을 최근 7일 기준으로 계산.

    실시간 계통도에서 폴링마다 LATERAL 서브쿼리로 7일 하이퍼테이블을 스캔하던 것을
    분리 — 느리게 변하는 지표이므로 백그라운드에서 1회 집계해 캐시한다.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT ti.sitename, round(MIN(r.val)::numeric, 2) AS night_min_flow
            FROM tb_tag_raw_data r
            JOIN tb_tag_info ti ON r.tagsn = ti.tagsn
            WHERE ti.facilitytype = '배수지'
              AND ti.datainfo ILIKE '%%유출%%유량%%순시%%'
              AND EXTRACT(HOUR FROM r.logtime) BETWEEN 2 AND 4
              AND r.logtime >= now() - interval '7 days'
            GROUP BY ti.sitename
        """)
        return {sn: float(v) for sn, v in cur.fetchall() if v is not None}
    except Exception as e:
        logger.error(f"야간 최소유량 계산 실패: {e}")
        conn.rollback()
        return {}
    finally:
        cur.close()
        conn.close()


async def _night_min_flow_cache_loop():
    """백그라운드: 서버 시작 150초 후 첫 실행, 이후 30분마다 배수지 NMF 갱신."""
    global _NIGHT_MIN_FLOW_CACHE, _NIGHT_MIN_FLOW_CACHE_TIME
    await asyncio.sleep(150)
    while True:
        try:
            t0 = datetime.now()
            result = await asyncio.to_thread(_compute_night_min_flows)
            if result:
                _NIGHT_MIN_FLOW_CACHE = result
                _NIGHT_MIN_FLOW_CACHE_TIME = datetime.now()
                logger.info(f"배수지 야간 최소유량 캐시 갱신: {len(result)}개 배수지, "
                            f"{(datetime.now() - t0).total_seconds():.1f}초")
        except Exception as e:
            logger.error(f"배수지 야간 최소유량 캐시 갱신 실패: {e}")
        await asyncio.sleep(_NIGHT_MIN_FLOW_CACHE_TTL)


def _refresh_night_min_flow_daily() -> int:
    """tb_night_min_flow_daily 사전집계 테이블을 어제까지 최신화한다.

    max(log_date) 이후 ~ 어제(CURRENT_DATE-1) 구간의 공백만 backfill 하여
    자기치유(백엔드가 며칠 다운돼 있었어도 gap 을 메움). pg_cron 부재 환경
    대체 스케줄. DB 함수 backfill_night_min_flow(start, end) 는 upsert 로
    설계돼 재실행 안전. 반환: 새로 채운 일수(0 = 이미 최신).
    """
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT max(log_date) FROM tb_night_min_flow_daily")
        _max = cur.fetchone()[0]
        cur.execute("SELECT (CURRENT_DATE - 1)")
        _yesterday = cur.fetchone()[0]
        # 빈 테이블이면 최근 365일부터, 아니면 max+1 부터
        _start = (_max + timedelta(days=1)) if _max else (_yesterday - timedelta(days=365))
        if _start > _yesterday:
            return 0  # 이미 최신
        cur.execute("SELECT backfill_night_min_flow(%s::date, %s::date)", (_start, _yesterday))
        conn.commit()
        return (_yesterday - _start).days + 1
    finally:
        cur.close()
        conn.close()


async def _night_min_flow_agg_loop():
    """백그라운드: 서버 시작 200초 후 첫 실행, 이후 24시간마다 사전집계 갱신.

    야간최소유량 트렌드/표준편차 fast-path 원천인 tb_night_min_flow_daily 를
    최신 상태로 유지한다 (E-035 재발 방지). 원시 데이터 동기화(_sync_worker,
    30일 초기 적재)와 프로파일링 이후 실행되도록 200초 지연.
    """
    await asyncio.sleep(200)
    while True:
        try:
            t0 = datetime.now()
            filled = await asyncio.to_thread(_refresh_night_min_flow_daily)
            elapsed = (datetime.now() - t0).total_seconds()
            if filled:
                logger.info(f"야간최소유량 사전집계 테이블 갱신: {filled}일 채움, {elapsed:.1f}초")
            else:
                logger.info("야간최소유량 사전집계 테이블 이미 최신 (갱신 불필요)")
        except Exception as e:
            logger.error(f"야간최소유량 사전집계 테이블 갱신 실패: {e}")
        await asyncio.sleep(_NIGHT_MIN_FLOW_AGG_TTL)



# 이상감지 스캔 → anomaly_scan.py로 분리됨


async def _ollama_keepwarm_loop():
    """백그라운드: 4분 주기로 Ollama generate + embed 더미 요청으로 모델을 VRAM에 유지.

    Ollama 기본 만료 시간은 5분이므로 4분마다 ping하면 리로드 오버헤드(~9.5s)를 방지한다.
    임베딩 모델도 같이 keep-warm하여 첫 임베딩 3초 지연을 방지한다.
    """
    await asyncio.sleep(30)  # 서버 완전 시작 대기
    while True:
        try:
            if ollama_client and ollama_client.health_check():
                # generate 모델 keep-warm (1-token)
                await asyncio.to_thread(
                    ollama_client.generate,
                    ".",            # 최소 프롬프트
                    None,           # model (default)
                    None,           # num_ctx (ai_settings 따름 → 리로드 없음)
                    1,              # num_predict: 1토큰만 생성
                    5.0,            # timeout: 5초
                    0,              # backoff_seconds: 실패해도 백오프 없음
                )
                # 임베딩 모델 keep-warm (첫 임베딩 3초 지연 방지)
                try:
                    import httpx as _httpx
                    from intent_embeddings import EMBED_MODEL as _EMBED_MODEL
                    await asyncio.to_thread(
                        lambda: _httpx.post(
                            f"{OLLAMA_BASE_URL.rstrip('/')}/api/embed",
                            json={"model": _EMBED_MODEL, "input": "."},
                            timeout=5.0,
                        )
                    )
                except Exception:
                    pass  # 임베딩 keep-warm 실패는 무시
                logger.debug("Ollama keep-warm 완료 (generate + embed)")
        except Exception:
            pass  # keep-warm 실패는 무시
        await asyncio.sleep(240)  # 4분 대기


async def _session_cleanup_loop():
    """백그라운드: 60초마다 만료 세션 정리 + CSV 파일 정리"""
    while True:
        await asyncio.sleep(60)
        session_manager.cleanup_expired()
        cleanup_old_csv_files()


async def _alarm_release_loop():
    """백그라운드: 2분마다 진행중 알람의 태그 최신값 확인 → val=0이면 자동 해제.

    Node-RED는 새 알람 발생/해제만 처리하므로, 과거 진행중 알람이
    태그값 복구 후에도 해제되지 않는 문제를 보완한다.
    """
    await asyncio.sleep(30)  # 서버 시작 후 30초 대기
    while True:
        try:
            released = await asyncio.to_thread(_release_stale_alarms)
            if released > 0:
                logger.info(f"오래된 알람 자동 해제: {released}건")
        except Exception as e:
            logger.warning(f"알람 자동 해제 실패: {e}")
        await asyncio.sleep(120)  # 2분 주기


def _release_stale_alarms() -> int:
    """진행중 알람 중 태그값이 0(정상)으로 돌아온 건을 해제한다."""
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        # DI 태그 최신값이 0인 진행중 알람을 '알람해제'로 업데이트
        cur.execute("""
            WITH stale AS (
                SELECT a.tagsn, a.alarm_start_time
                FROM tb_equipment_alarm_report a
                JOIN tb_tag_info ti ON a.tagsn = ti.tagsn
                WHERE a.alarm_status = '진행중'
                  AND ti.tagtype = 'Digital Input'
                  AND EXISTS (
                      SELECT 1 FROM (
                          SELECT DISTINCT ON (tagsn) tagsn, val
                          FROM tb_tag_raw_data
                          WHERE tagsn = a.tagsn
                          ORDER BY tagsn, logtime DESC
                      ) latest WHERE latest.val = 0
                  )
            )
            UPDATE tb_equipment_alarm_report a
            SET alarm_status = '알람해제',
                alarm_end_time = now(),
                info_updated = TO_CHAR(now(), 'YYYY-MM-DD HH24:MI:SS')
            FROM stale s
            WHERE a.tagsn = s.tagsn
              AND a.alarm_start_time = s.alarm_start_time
              AND a.alarm_status = '진행중'
        """)
        released = cur.rowcount
        conn.commit()
        cur.close()
        return released
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작/종료 시 실행되는 lifespan 이벤트"""
    global intent_classifier, param_extractor_instance, query_validator, _cleanup_task, _profiling_task, _sync_task, site_profiler, _sync_worker, _ollama_keepwarm_task

    # DB 커넥션 풀 초기화 (실패해도 직접 연결 폴백으로 계속 기동)
    try:
        _init_db_pool()
    except Exception as _pool_err:
        logger.warning(f"DB 풀 초기화 실패 (무시, 직접 연결 폴백): {_pool_err}")

    # site_profiler 초기화 (get_db_connection 정의 후)
    site_profiler = SiteProfiler(get_db_connection)

    # AI 런타임 설정 DB 로드 (풀 초기화 후)
    _ai_settings.load_from_db()

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
        facility_alias_map=FACILITY_ALIAS_MAP,
    )
    query_validator = QueryValidator(intent_index, KNOWN_SITENAMES, SITENAME_FACILITY_MAP)

    # Ollama 연결 상태 로그 + embed_query 백오프 초기화
    if ollama_client.health_check():
        logger.info(f"Ollama 연결 성공: {get_model()}")
        if embedding_index.ready:
            logger.info(f"벡터 검색 활성화: {embedding_index.size}벡터")
        # 모델 웜업 — 첫 사용자 요청이 cold-start 페널티(gemma4:26b 기준 60~90s)를 맞지 않도록
        # 백그라운드 스레드에서 짧은 generate 호출해 가중치를 VRAM에 올려둠.
        # keep_alive는 OLLAMA_KEEP_ALIVE(기본 24h) 설정에 따라 상주 유지.
        def _warmup_model():
            import time as _t
            _t0 = _t.time()
            try:
                ollama_client.generate(
                    "ping", None, None, 1, 120.0, 3,
                )
                logger.info(f"Ollama 웜업 완료: {get_model()} ({int((_t.time()-_t0)*1000)}ms)")
            except Exception as _e:
                logger.warning(f"Ollama 웜업 실패 (무시): {_e}")
        import threading as _th
        _th.Thread(target=_warmup_model, daemon=True, name="ollama-warmup").start()
    else:
        logger.warning("Ollama 연결 실패 — 키워드 매칭 폴백 모드로 동작")
        # Ollama 비가용: embed_query + generate 백오프 즉시 설정 (첫 요청 타임아웃 방지)
        import time as _time_mod
        import intent_embeddings
        intent_embeddings._ollama_unavailable_until = _time_mod.time() + 60
        ollama_client._unavailable_until = _time_mod.time() + 60

    # DDL 자동 생성 (캔버스 + 태그 그룹)
    try:
        _conn = get_db_connection()
        _cur = _conn.cursor()
        _cur.execute("""
            CREATE TABLE IF NOT EXISTS tb_canvas_node_position (
                sitename     VARCHAR(100)       NOT NULL,
                facilitytype VARCHAR(50)        NOT NULL,
                pos_x        DOUBLE PRECISION   NOT NULL DEFAULT 0,
                pos_y        DOUBLE PRECISION   NOT NULL DEFAULT 0,
                updated_at   TIMESTAMPTZ        DEFAULT now(),
                PRIMARY KEY (sitename, facilitytype)
            )
        """)
        _cur.execute("""
            CREATE TABLE IF NOT EXISTS tb_equipment_tag_map (
                equipment_id VARCHAR(64)  NOT NULL,
                tagsn        VARCHAR(64)  NOT NULL,
                PRIMARY KEY (equipment_id, tagsn)
            )
        """)
        _cur.execute("""
            CREATE TABLE IF NOT EXISTS tb_tag_data_group (
                group_id      SERIAL PRIMARY KEY,
                group_code    VARCHAR(50)  NOT NULL UNIQUE,
                group_name    VARCHAR(100) NOT NULL,
                parent_code   VARCHAR(50),
                tagtype       VARCHAR(50)  NOT NULL,
                keywords      JSONB        NOT NULL DEFAULT '[]'::jsonb,
                display_order INT          DEFAULT 0,
                created_at    TIMESTAMPTZ  DEFAULT now()
            )
        """)
        _cur.execute("""
            CREATE TABLE IF NOT EXISTS tb_tag_group_map (
                tagsn    VARCHAR(64) PRIMARY KEY,
                group_id INT NOT NULL REFERENCES tb_tag_data_group(group_id)
            )
        """)
        _cur.execute("""
            CREATE TABLE IF NOT EXISTS tb_snmp_port_status (
                equipment_id          VARCHAR(64)   NOT NULL,
                port_index            INT           NOT NULL,
                port_name             VARCHAR(100),
                oper_status           VARCHAR(20)   NOT NULL DEFAULT 'unknown',
                admin_status          VARCHAR(20)   NOT NULL DEFAULT 'up',
                speed_mbps            INT           DEFAULT 0,
                connected_mac         VARCHAR(20),
                connected_ip          VARCHAR(50),
                connected_device_name VARCHAR(100),
                in_octets             BIGINT        DEFAULT 0,
                out_octets            BIGINT        DEFAULT 0,
                in_errors             INT           DEFAULT 0,
                out_errors            INT           DEFAULT 0,
                polled_at             TIMESTAMPTZ   NOT NULL DEFAULT now(),
                PRIMARY KEY (equipment_id, port_index)
            )
        """)
        _cur.execute("""
            CREATE TABLE IF NOT EXISTS tb_causal_chain_override (
                override_id       SERIAL PRIMARY KEY,
                sitename          VARCHAR(100) NOT NULL,
                facilitytype      VARCHAR(50) NOT NULL,
                zone              VARCHAR(10) DEFAULT NULL,
                chain_json        JSONB NOT NULL,
                cross_facility_json JSONB,
                source            VARCHAR(20) DEFAULT 'manual',
                updated_at        TIMESTAMPTZ DEFAULT now(),
                UNIQUE(sitename, facilitytype, zone)
            )
        """)
        _conn.commit()
        _cur.close()
        _conn.close()
        logger.info("DDL 확인/생성 완료: canvas_node_position, equipment_tag_map, tag_data_group, tag_group_map, snmp_port_status, causal_chain_override")
    except Exception as e:
        logger.warning(f"DDL 생성 실패 (무시): {e}")

    # 태그 자동분류 + 인과 인덱스 구축
    try:
        _conn2 = get_db_connection()
        classified = _auto_classify_tags(_conn2)
        logger.info(f"태그 그룹 자동분류 완료: {classified}건 분류")
        causal_count = _build_causal_index(_conn2)
        logger.info(f"인과 인덱스 구축 완료: {causal_count}개 시설")
        # 설비↔태그 자동 매핑 (ON CONFLICT DO NOTHING → 기존 수동 매핑 보존)
        map_result = _auto_map_equipment_tags(_conn2, dry_run=False)
        logger.info("설비↔태그 자동 매핑 완료: %d건 (by_type: %s)",
                     map_result["total_links"], map_result["by_type"])
        _conn2.close()
    except Exception as e:
        logger.warning(f"태그 자동분류/인과 인덱스 실패 (무시): {e}")
        _build_group_children_cache()  # 분류 실패해도 children 캐시는 빌드

    # 세션 정리 백그라운드 태스크
    _cleanup_task = asyncio.create_task(_session_cleanup_loop())

    # 알람 자동 해제 백그라운드 태스크
    _alarm_release_task = asyncio.create_task(_alarm_release_loop())

    # 현장 프로파일링 백그라운드 태스크 (기존 DB 프로파일 로드 후 시작)
    try:
        site_profiler.load_from_db()
        if site_profiler and site_profiler.profiles:
            logger.info(f"기존 현장 프로파일 로드: {len(site_profiler.profiles)}개")
    except Exception as e:
        logger.warning(f"기존 프로파일 로드 실패 (서버 시작 후 재생성): {e}")
    _profiling_task = asyncio.create_task(_site_profiling_loop())

    # SNMP 스위치 포트 폴링 백그라운드 태스크
    global snmp_poller_instance, _snmp_polling_task
    snmp_poller_instance = SnmpPoller(get_db_connection)
    try:
        sw_count = snmp_poller_instance.load_switches()
        logger.info(f"SNMP 스위치 로드: {sw_count}대")
    except Exception as e:
        logger.warning(f"SNMP 스위치 로드 실패 (무시): {e}")
    # 네트워크 모듈에 SNMP 폴러 재주입 (lifespan에서 초기화 이후)
    init_network_crud(get_db_connection, snmp_poller=snmp_poller_instance)
    _snmp_polling_task = asyncio.create_task(_snmp_polling_loop())

    # IForest 백그라운드 학습 (90초 후 첫 실행, 이후 24시간 주기)
    global _iforest_task
    _iforest_task = asyncio.create_task(_iforest_training_loop())

    # ANOMALY_SCAN_ALL 백그라운드 캐시 (150초 후 첫 실행, 이후 5분 주기)
    global _anomaly_scan_task
    _anomaly_scan_task = asyncio.create_task(_anomaly_scan_cache_loop())
    _flow_balance_task = asyncio.create_task(_flow_balance_cache_loop())
    _flow_baseline_task = asyncio.create_task(_flow_baseline_cache_loop())
    _night_min_flow_task = asyncio.create_task(_night_min_flow_cache_loop())

    # 야간최소유량 사전집계 테이블 일 1회 갱신 (200초 후 첫 실행, pg_cron 대체)
    _night_min_flow_agg_task = asyncio.create_task(_night_min_flow_agg_loop())

    # 누수 CUSUM 알림 스캔 (서버 시작 10분 후 첫 실행, 이후 6시간 주기)
    _leak_cusum_task = asyncio.create_task(_leak_cusum_scan_loop())

    # Ollama 모델 Keep-Warm (4분 주기 더미 요청으로 VRAM 유지)
    # Ollama 기본 만료=5분, 4분마다 ping하여 리로드 오버헤드(~9.5s) 방지
    _ollama_keepwarm_task = asyncio.create_task(_ollama_keepwarm_loop())

    # [임시] 로컬 DB 사용 시 원격→로컬 실시간 동기화 (개발 완료 후 제거 예정)
    _sync_task = None
    _sync_worker = None
    if DB_HOST in ("localhost", "127.0.0.1"):
        _sync_worker = DbSyncWorker(
            local_db={"host": DB_HOST, "port": int(DB_PORT), "dbname": DB_NAME,
                       "user": DB_USER, "password": DB_PASSWORD},
            interval=60,
            batch_size=50000,
            initial_days=30,
        )
        _sync_task = asyncio.create_task(_sync_worker.run_loop())
        logger.info("[임시] 원격→로컬 DB 동기화 태스크 등록 (30초 후 시작)")

    yield

    # shutdown
    if _db_pool:
        _db_pool.closeall()
        logger.info("DB 커넥션 풀 정리 완료")
    if _sync_worker:
        _sync_worker.stop()
    for task in (_cleanup_task, _profiling_task, _snmp_polling_task, _iforest_task, _anomaly_scan_task, _flow_baseline_task, _night_min_flow_task, _sync_task):
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

_allowed_origins = os.environ.get(
    "CORS_ORIGINS", "https://localhost:3000,http://localhost:3000"
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["content-type", "authorization"],
    allow_credentials=True,
)

# =============================================================================
# 데모 모드 — sitename 익명화 미들웨어
# DEMO_MODE=true 환경변수로 활성화, site_mapping.csv 기반 치환
# =============================================================================
DEMO_MODE = os.environ.get("DEMO_MODE", "false").lower() == "true"
_DEMO_SITE_MAP: dict[str, str] = {}
# 지역명 익명화 매핑 (현장명 이외 응답에 포함되는 행정구역명)
_DEMO_REGION_MAP: dict[str, str] = {
    "충청남도": "X도",
    "충남": "X도",
    "당진시": "Y시",
    "당진": "Y시",
}

def _load_demo_site_map():
    """site_mapping.csv 로드 → {원본: 코드} 딕셔너리 (긴 이름 우선 정렬)"""
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web", "files", "site_mapping.csv")
    csv_path = os.path.normpath(csv_path)
    if not os.path.exists(csv_path):
        logger.warning(f"[DEMO] site_mapping.csv 없음: {csv_path}")
        return
    import csv as csv_mod
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv_mod.DictReader(f)
        for row in reader:
            orig = row.get("original", "").strip()
            code = row.get("code", "").strip()
            if orig and code:
                _DEMO_SITE_MAP[orig] = code
    # 긴 이름 우선 (남산11 → 남산1 → 남산 순서로 치환하기 위해)
    logger.info(f"[DEMO] site_mapping 로드: {len(_DEMO_SITE_MAP)}건")

if DEMO_MODE:
    _load_demo_site_map()
    _DEMO_SITE_MAP.update(_DEMO_REGION_MAP)
    logger.info(f"[DEMO] 데모 모드 활성화 — {len(_DEMO_SITE_MAP)}개 현장명+지역명 익명화")

# 역변환 맵: {코드: 원본} — 사용자 입력(코드)을 DB 원본명으로 복원
# 지역명 역변환 제외 (X도→충청남도/충남 중복, 사용자가 "X도" 입력할 일 없음)
_DEMO_REGION_CODES = set(_DEMO_REGION_MAP.values())
_DEMO_REVERSE_MAP: dict[str, str] = {
    v: k for k, v in _DEMO_SITE_MAP.items() if v not in _DEMO_REGION_CODES
} if _DEMO_SITE_MAP else {}

def _demo_replace_text(text: str) -> str:
    """문자열 내 모든 현장명을 코드로 치환 (긴 이름 우선)"""
    if not text or not _DEMO_SITE_MAP:
        return text
    for orig in sorted(_DEMO_SITE_MAP.keys(), key=len, reverse=True):
        if orig in text:
            text = text.replace(orig, _DEMO_SITE_MAP[orig])
    return text

def _demo_restore_text(text: str) -> str:
    """문자열 내 익명 코드를 원본 현장명으로 복원 (단어 경계 기반, 긴 코드 우선)

    단일 문자 코드(A, B, H 등)가 JSON 키/값의 일부와 충돌하지 않도록
    공백/시작/끝 경계에서만 치환한다.
    """
    if not text or not _DEMO_REVERSE_MAP:
        return text
    import re
    for code in sorted(_DEMO_REVERSE_MAP.keys(), key=len, reverse=True):
        if code in text:
            # 한글이 포함된 코드는 그대로 치환 (충돌 없음)
            if re.search(r'[가-힣]', code):
                text = text.replace(code, _DEMO_REVERSE_MAP[code])
            else:
                # 영문 코드: 단어 경계에서만 치환 (A가 "application"의 A를 치환하는 것 방지)
                pattern = r'(?<![A-Za-z0-9_])' + re.escape(code) + r'(?![A-Za-z0-9_])'
                text = re.sub(pattern, _DEMO_REVERSE_MAP[code], text)
    return text

def _demo_restore_json_fields(body: bytes, fields: tuple[str, ...] = ("user_question", "query", "sitename", "keyword")) -> bytes:
    """JSON body에서 특정 필드만 역변환 (안전한 방식)"""
    import json as _json
    try:
        obj = _json.loads(body.decode("utf-8"))
    except Exception as e:
        logger.debug(f"DEMO JSON 역변환 파싱 실패 (비JSON body): {e}")
        return body

    changed = False
    for field in fields:
        if field in obj and isinstance(obj[field], str):
            restored = _demo_restore_text(obj[field])
            if restored != obj[field]:
                obj[field] = restored
                changed = True

    if changed:
        return _json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return body

def _demo_anonymize(obj):
    """재귀적으로 dict/list/str 내 현장명 치환"""
    if isinstance(obj, str):
        return _demo_replace_text(obj)
    if isinstance(obj, dict):
        return {k: _demo_anonymize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_demo_anonymize(item) for item in obj]
    return obj

@app.middleware("http")
async def demo_anonymize_middleware(request: Request, call_next):
    """DEMO_MODE 시 요청 역변환(코드→원본) + 응답 익명화(원본→코드)"""
    if not DEMO_MODE:
        return await call_next(request)

    # ── GET 쿼리 파라미터 역변환 (sitename=BH → sitename=신평 등) ──
    if request.method == "GET" and _DEMO_REVERSE_MAP:
        from urllib.parse import parse_qs, urlencode
        query_string = request.scope.get("query_string", b"").decode("utf-8")
        if query_string:
            parsed = parse_qs(query_string, keep_blank_values=True)
            changed = False
            for key in list(parsed.keys()):
                new_vals = []
                for v in parsed[key]:
                    restored = _demo_restore_text(v)
                    if restored != v:
                        changed = True
                    new_vals.append(restored)
                parsed[key] = new_vals
            if changed:
                # urlencode는 한글을 %XX 형태로 URL-인코딩함
                restored_qs = urlencode(parsed, doseq=True)
                logger.debug("[DEMO] GET 쿼리 파라미터 역변환 적용")
                request.scope["query_string"] = restored_qs.encode("ascii")

    response = await call_next(request)
    content_type = response.headers.get("content-type", "")

    # SSE 스트리밍 응답 처리
    if "text/event-stream" in content_type:
        original_body = response.body_iterator

        async def anonymize_stream():
            async for chunk in original_body:
                if isinstance(chunk, bytes):
                    text = chunk.decode("utf-8", errors="replace")
                else:
                    text = chunk
                yield _demo_replace_text(text).encode("utf-8") if isinstance(chunk, bytes) else _demo_replace_text(text)

        return StreamingResponse(
            anonymize_stream(),
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type="text/event-stream",
        )

    # JSON 응답 처리
    if "application/json" in content_type:
        body_bytes = b""
        async for chunk in response.body_iterator:
            if isinstance(chunk, bytes):
                body_bytes += chunk
            else:
                body_bytes += chunk.encode("utf-8")

        try:
            body_text = body_bytes.decode("utf-8")
            anonymized = _demo_replace_text(body_text)
            new_body = anonymized.encode("utf-8")
        except Exception as e:
            logger.debug(f"DEMO 응답 익명화 실패 (바이너리 body): {e}")
            new_body = body_bytes

        from starlette.responses import Response as StarletteResponse
        headers = dict(response.headers)
        headers["content-length"] = str(len(new_body))
        return StarletteResponse(
            content=new_body,
            status_code=response.status_code,
            headers=headers,
            media_type="application/json",
        )

    return response


# =============================================================================
# 환경변수 기반 DB 접속 정보
# docs/ai_server_task.md 참조: 접속 정보는 환경변수 기반으로 처리
# =============================================================================
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5433")
DB_NAME = os.environ.get("DB_NAME", "slm")
DB_USER = os.environ.get("DB_USER", "slm_dev")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_POOL_MIN = int(os.environ.get("DB_POOL_MIN", "2"))
DB_POOL_MAX = int(os.environ.get("DB_POOL_MAX", "10"))
if not DB_PASSWORD:
    logger.warning("DB_PASSWORD 환경변수가 설정되지 않았습니다. .env 파일을 확인하세요.")

_db_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None


def _init_db_pool() -> None:
    """커넥션 풀 초기화 — 실패해도 서버는 계속 기동 (직접 연결 폴백)."""
    global _db_pool
    try:
        _db_pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=DB_POOL_MIN,
            maxconn=DB_POOL_MAX,
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            options="-c client_encoding=utf8",
        )
        logger.info(f"DB 커넥션 풀 초기화 완료 (min={DB_POOL_MIN}, max={DB_POOL_MAX})")
    except Exception as e:
        logger.warning(f"DB 풀 초기화 실패 — 직접 연결 폴백 모드로 기동: {e}")


# =============================================================================
# AI 런타임 설정 (UI에서 변경 가능)
# =============================================================================
class _AiRuntimeSettings:
    """서버 재시작 없이 변경 가능한 AI 파라미터.

    DB 영속: tb_comm_code (SITE_SETTING/AI_NUM_CTX/AI_TEMPERATURE/AI_TIMEOUT).
    load_from_db() 는 DB 풀 초기화 이후 기동 시 1회 호출.
    """
    def __init__(self):
        self.num_ctx: int = 4096
        self.temperature: float = 0.0
        self.timeout: int = 30

    def load_from_db(self) -> None:
        """DB 에서 AI 파라미터·모델명을 읽어 메모리 값을 덮어쓴다.
        실패 시 기본값·환경변수 모델 유지."""
        model_loaded: Optional[str] = None
        try:
            with db_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT comm_cd, comm_val FROM tb_comm_code "
                    "WHERE region='R01' AND grp_cd='SITE_SETTING' "
                    "AND comm_cd IN ('AI_NUM_CTX','AI_TEMPERATURE',"
                    "'AI_TIMEOUT','AI_MODEL')"
                )
                for cd, val in cur.fetchall():
                    if val is None:
                        continue
                    try:
                        if cd == "AI_NUM_CTX":
                            self.num_ctx = int(val)
                        elif cd == "AI_TEMPERATURE":
                            self.temperature = float(val)
                        elif cd == "AI_TIMEOUT":
                            self.timeout = int(val)
                        elif cd == "AI_MODEL":
                            model_loaded = str(val)
                    except (TypeError, ValueError):
                        logger.warning(f"AI 파라미터 파싱 실패 {cd}={val}")
                cur.close()
            if model_loaded:
                from slm_config import set_model
                set_model(model_loaded)
            logger.info(
                f"AI 파라미터 DB 로드: num_ctx={self.num_ctx} "
                f"temperature={self.temperature} timeout={self.timeout} "
                f"model={model_loaded or '(env 유지)'}"
            )
        except Exception as e:
            logger.warning(f"AI 파라미터 DB 로드 실패 (기본값 유지): {e}")

_ai_settings = _AiRuntimeSettings()


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
FACILITY_ALIAS_MAP = load_facility_aliases_from_db()


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


# =============================================================================
# DB 연결 및 SQL 실행 함수
# docs/ai_server_task.md 참조:
# - psycopg2 사용
# - SQL 템플릿이 빈 문자열이면 실행하지 않는다
# =============================================================================
class _PooledConnection:
    """
    psycopg2 연결 래퍼 — close() 호출 시 실제 연결을 닫지 않고 풀에 반환한다.
    기존 CRUD 모듈의 finally: conn.close() 패턴과 완전 호환된다.
    psycopg2 연결 객체는 C 확장 타입이라 속성 패치가 불가하므로 래퍼로 처리.
    """

    __slots__ = ("_pool", "_conn")

    def __init__(self, pool: psycopg2.pool.ThreadedConnectionPool, conn) -> None:
        self._pool = pool
        self._conn = conn

    def close(self) -> None:
        """풀에 연결을 반환한다."""
        self._pool.putconn(self._conn)

    def __getattr__(self, name: str):
        return getattr(self._conn, name)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def get_db_connection():
    """
    커넥션 풀에서 연결을 가져온다.
    반환된 연결의 close()를 호출하면 풀로 반환된다.
    """
    if _db_pool is None:
        # 풀 미초기화 시 직접 연결 (안전 fallback)
        logger.warning("DB 풀이 초기화되지 않았습니다. 직접 연결로 fallback합니다.")
        return psycopg2.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
    return _PooledConnection(_db_pool, _db_pool.getconn())


from contextlib import contextmanager

@contextmanager
def db_conn():
    """DB 커넥션 컨텍스트 매니저 — 자동 close 보장, 에러 시 rollback."""
    conn = get_db_connection()
    try:
        yield conn
    except Exception as e:
        logger.error(f"db_conn 컨텍스트 내 에러, rollback 수행: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


# 설비 CRUD 엔드포인트 모듈 초기화 (get_db_connection 정의 이후)
init_facility_crud(get_db_connection)
app.include_router(facility_crud_router)

# 시설유형별 CRUD (배수지/가압장/감압/블록)
init_facility_types_crud(get_db_connection)
app.include_router(facility_types_crud_router)

# 감사 로그 조회 (관리) — Migration 0094
from endpoints.audit import router as audit_router, init as init_audit
init_audit(get_db_connection)
app.include_router(audit_router)

# 사용자별 선호도 (테마·브랜드·레이아웃) — Migration 0096
from endpoints.user_prefs import router as user_prefs_router, init as init_user_prefs
init_user_prefs(get_db_connection)
app.include_router(user_prefs_router)

# 네트워크 CRUD 엔드포인트 모듈 초기화
init_network_crud(get_db_connection, snmp_poller=snmp_poller_instance)
app.include_router(network_crud_router)

# 캔버스 레이아웃 엔드포인트 모듈 초기화
init_canvas_crud(get_db_connection)
app.include_router(canvas_crud_router)

# 인증 엔드포인트 모듈 초기화
init_auth_crud(get_db_connection)
app.include_router(auth_crud_router)

# 비상연락처 엔드포인트 모듈 초기화
init_alarm_contacts(get_db_connection)
app.include_router(alarm_contacts_router)

# 모니터링 카탈로그 엔드포인트 모듈 초기화
init_monitoring_catalogs(get_db_connection)
app.include_router(monitoring_catalogs_router)

# 용수 흐름 CRUD 엔드포인트 모듈 초기화
init_flow_map_crud(get_db_connection)
app.include_router(flow_map_crud_router)

# CSV 일괄 가져오기 엔드포인트 모듈 초기화
init_csv_import(get_db_connection)
app.include_router(csv_import_router)

# 트렌드 GBT baseline 성능 평가 조회 엔드포인트 모듈 초기화
init_baseline_eval(get_db_connection)
app.include_router(baseline_eval_router)

# IForest 이상탐지 모델 성능 평가 조회 엔드포인트 모듈 초기화
init_iforest_eval(get_db_connection)
app.include_router(iforest_eval_router)

# 트렌드 엔드포인트 모듈 초기화
init_trend(get_db_connection, ollama_client)
app.include_router(trend_router)

# 인과관계 엔드포인트 모듈 초기화
init_causal(
    get_db_connection,
    _CAUSAL_INDEX,
    _CAUSAL_TEMPLATE_MAP,
    CAUSAL_CHAIN_TEMPLATES,
    _detect_zones,
    _get_causal_info,
    _rebuild_causal_index_entry,
)
app.include_router(causal_router)

# 경보/위기관리 엔드포인트 모듈 초기화
init_alarm_crisis(get_db_connection)
app.include_router(alarm_crisis_router)

# 대시보드 엔드포인트 모듈 초기화
def _get_scan_cache():
    return (_ANOMALY_SCAN_CACHE, _ANOMALY_SCAN_CACHE_TIME)

def _get_balance_cache():
    return _FLOW_BALANCE_CACHE

# 태그 엔드포인트 모듈 초기화 (모니터링 이상 카테고리용 캐시 getter 주입)
init_tags(get_db_connection, _get_scan_cache, _get_balance_cache)
app.include_router(tags_router)

init_dashboard(get_db_connection, _get_scan_cache, _get_balance_cache)
app.include_router(dashboard_router)

# 응답 빌더 모듈 초기화
response_builder.init(
    get_db_connection, _CAUSAL_INDEX, SITENAME_FACILITY_MAP,
    _get_scan_cache, iforest_manager,
    site_profiler_ref=site_profiler,
    causal_template_map=_CAUSAL_TEMPLATE_MAP,
    group_children=_GROUP_CHILDREN,
    group_code_to_id=_GROUP_CODE_TO_ID,
    resolve_group_codes_fn=_resolve_group_codes,
    diagnose_equipment_for_tags_fn=_diagnose_equipment_for_tags,
)

# 이상감지 스캔 모듈 초기화 — execute_sql 정의 후 호출 (아래 참조)

# 용수 흐름 실시간 + GIS + 설비 자동매핑 엔드포인트 모듈 초기화
def _get_baseline_cache():
    return _FLOW_BASELINE_CACHE

def _get_night_min_flow_cache():
    return _NIGHT_MIN_FLOW_CACHE

init_flow_realtime(
    get_db_connection, _get_scan_cache, _get_balance_cache,
    _get_baseline_cache, _auto_map_equipment_tags,
    get_night_min_flow_cache_fn=_get_night_min_flow_cache,
)
app.include_router(flow_realtime_router)

# 관리자 엔드포인트 모듈 초기화
init_admin(
    get_db_connection, ollama_client, get_model, set_model,
    session_manager, site_profiler, _ai_settings,
    DEMO_MODE, _demo_restore_text,
)
app.include_router(admin_router)

# 채팅 봇 오분류 피드백 엔드포인트 모듈 초기화
init_chat_feedback(get_db_connection)
init_feedback_intent_index(intent_index, embedding_index)
app.include_router(chat_feedback_router)
init_chat_log(get_db_connection)
app.include_router(chat_log_router)

# 채팅 기반 설비 장애 기록 엔드포인트 (migration 0045)
init_chat_fault_record(get_db_connection)
app.include_router(chat_fault_record_router)

# [P3] 고장 진단 케이스 DB (migration 0048) — CRUD + 엑셀 IMPORT/EXPORT
init_fault_case(get_db_connection)
app.include_router(fault_case_router)

# [P5] 알람 ↔ 장애 매칭 분석
init_afc(get_db_connection)
app.include_router(afc_router)

# 설비 건강성 통계 엔드포인트 (migration 0045 views)
init_equipment_health(get_db_connection)
app.include_router(equipment_health_router)


def _reload_facility_aliases():
    """facility_alias CRUD 후 런타임 param_extractor에 즉시 반영"""
    global FACILITY_ALIAS_MAP
    FACILITY_ALIAS_MAP = load_facility_aliases_from_db()
    if param_extractor_instance is not None:
        param_extractor_instance._alias_map = FACILITY_ALIAS_MAP
        param_extractor_instance._aliases_sorted = sorted(
            FACILITY_ALIAS_MAP.keys(), key=len, reverse=True,
        )
        logger.info(f"ParamExtractor alias 리로드: {len(FACILITY_ALIAS_MAP)}건")


# 시설명 약칭 매핑 CRUD 엔드포인트 모듈 초기화
init_facility_alias(get_db_connection, _reload_facility_aliases)
app.include_router(facility_alias_router)

# 이상감지 원인 LLM 서술 엔드포인트 모듈 초기화
init_anomaly_explain(ollama_client, get_db_connection)
app.include_router(anomaly_explain_router)

# 설비 신뢰성 리포트 (MTBF/MTTR/Availability) 엔드포인트
init_equipment_mtbf(get_db_connection)
app.include_router(equipment_mtbf_router)

# 알람 캘린더 히트맵 엔드포인트
init_alarm_calendar(get_db_connection)
app.include_router(alarm_calendar_router)

# 누수 CUSUM 알림 엔드포인트 (백그라운드 태스크는 lifespan에서 등록)
init_leak_cusum_alert(get_db_connection)
app.include_router(leak_cusum_alert_router)

# LLM 자연어 서술 호출 통계 (운영자 관찰용)
app.include_router(llm_narrative_stats_router)

# 단일 태그 최신값 AI 해석 엔드포인트 (P2.4)
init_tag_latest_explain(get_db_connection, ollama_client)
app.include_router(tag_latest_explain_router)

# 전체 이상 스캔 요약 AI 서술 엔드포인트 (P2.6)
init_scan_all_explain(_get_scan_cache, ollama_client)
app.include_router(scan_all_explain_router)

# 설비 MTBF AI 해석 엔드포인트 (P2.7)
init_equipment_mtbf_explain(get_db_connection, ollama_client)
app.include_router(equipment_mtbf_explain_router)

# NETWORK_UPSTREAM_FAULT_ANALYSIS AI 원인 추정 (P2.8)
init_network_upstream_explain(get_db_connection, ollama_client)
app.include_router(network_upstream_explain_router)

# 채팅 FAQ 예시 구문 동적 생성 (실제 답변 있는 지점으로 치환)
init_chat_faq_examples(get_db_connection, _get_scan_cache)
app.include_router(chat_faq_examples_router)

# 멀티모달 현장 진단 프록시 [E-025]
# /ask/multimodal/stream 엔드포인트 — vision_agent(8100)로 multipart 포워딩 + SSE
from endpoints.vision_proxy import (
    router as vision_proxy_router,
    init as init_vision_proxy,
)
init_vision_proxy(get_db_connection)
app.include_router(vision_proxy_router)

# 보고서 (장애 조치 / 일 점검) — Migration 0058
from endpoints.reports import router as reports_router, init as init_reports
init_reports(get_db_connection)
app.include_router(reports_router)

# 보고서 카테고리 (장애 시스템 / 장애 장비) — Migration 0060
from endpoints.report_categories import router as report_categories_router, init as init_report_categories
init_report_categories(get_db_connection)
app.include_router(report_categories_router)

# 채팅에서 보고서 직접 생성 — "이번 주 장애 보고서 만들어줘"
from endpoints.chat_report_create import router as chat_report_router, init as init_chat_report
init_chat_report(get_db_connection)
app.include_router(chat_report_router)

# EPANET 수리 시뮬레이션 (Migration 0064 Phase 1) — 활성화 토글 OFF default
import epanet as epanet_module
from endpoints.epanet import router as epanet_router
epanet_module.init(get_db_connection)
app.include_router(epanet_router)


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
                escaped_value = _sql_escape_literal(str(value))
                if key in _QUOTE_PARAMS:
                    escaped_value = f"'{escaped_value}'"
                sql = sql.replace(placeholder, escaped_value)

    # 세미콜론으로 구분된 다중 SQL인 경우 개별 실행 후 결과 병합
    # psycopg2는 nextset()을 지원하지 않으므로 수동 분리 필요
    # 문자열 리터럴('...')안의 세미콜론은 무시한다
    statements = _split_sql_statements(sql)

    conn = None
    cur = None
    _sql_t0 = time.perf_counter()
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        all_rows = []
        all_columns = []

        for stmt in statements:
            logger.debug(f"execute_sql statement ({len(stmt)} chars): {stmt[:500]}")
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

        _sql_elapsed = (time.perf_counter() - _sql_t0) * 1000
        logger.info(f"⏱ SQL {_sql_elapsed:.0f}ms → {len(all_rows)}행")
        return all_rows, all_columns
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


# =============================================================================
# 응답 빌더 / SQL 실행기 / process_sql_result → response_builder.py로 분리됨
# =============================================================================

# 이상감지 스캔 모듈 초기화 (execute_sql 정의 후)
anomaly_scan.init(
    get_db_connection, execute_sql, process_sql_result,
    INTENT_DEFINITIONS, _CAUSAL_INDEX,
    query_recent_values_fn=_query_recent_values,
    site_profiler_ref=site_profiler,
    get_flow_balance_cache_fn=_get_balance_cache,
)


def _log_chat_interaction(
    request: "AskRequest",
    response: Any,
    elapsed_ms: int,
    error: Optional[str] = None,
) -> None:
    """채팅 질의/응답을 tb_ai_chat_log에 기록 (폐쇄망 통계용).

    실패해도 응답 블로킹하지 않음. 모든 예외를 삼킨다.
    """
    try:
        if get_db_connection is None:
            return
        region = getattr(request, "region", None) or "R01"
        user_id = getattr(request, "user_id", None)
        question = getattr(request, "user_question", "") or ""
        images = getattr(request, "images", None) or []
        is_multi = bool(images)

        resp_dict = response if isinstance(response, dict) else {}
        intent = resp_dict.get("intent")
        answer = resp_dict.get("answer") or {}
        summary = answer.get("summary") if isinstance(answer, dict) else None
        visual = resp_dict.get("visual") or {}
        graph_type = visual.get("type") if isinstance(visual, dict) else None
        has_visual = bool(graph_type and graph_type != "none")

        # intent_confidence: 있으면 응답 meta에서, 없으면 None
        intent_confidence = None
        meta = answer.get("meta") if isinstance(answer, dict) else None
        if isinstance(meta, dict):
            conf = meta.get("confidence")
            if isinstance(conf, (int, float)) and conf > 1:
                intent_confidence = conf / 100.0  # 92 → 0.92
            elif isinstance(conf, (int, float)):
                intent_confidence = conf

        # total_rows 힌트
        total_rows = resp_dict.get("total_rows")

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO tb_ai_chat_log
                      (region, user_id, user_question, intent_name, intent_confidence,
                       graph_type, response_time_ms, bot_summary, total_rows,
                       has_visual, is_multimodal, error)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        region,
                        user_id,
                        question[:4000],
                        intent,
                        intent_confidence,
                        graph_type,
                        elapsed_ms,
                        (summary or "")[:2000] if summary else None,
                        total_rows,
                        has_visual,
                        is_multi,
                        (error or "")[:2000] if error else None,
                    ),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as _log_e:
        logger.warning("chat_log insert 실패 (무시): %s", _log_e)


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
    8. tb_ai_chat_log 기록 (실패 무시)
    """
    _t_outer = time.perf_counter()
    response_obj = None
    error_msg = None
    try:
        response_obj = await _ask_inner(request)
        return response_obj
    except Exception as _top_e:
        import traceback as _tb
        error_msg = repr(_top_e)
        logger.error("ask() 최상위 예외:\n%s", _tb.format_exc())
        raise
    finally:
        elapsed_ms = int((time.perf_counter() - _t_outer) * 1000)
        _log_chat_interaction(request, response_obj, elapsed_ms, error=error_msg)


async def _ask_inner(request: AskRequest):
    _t_start = time.perf_counter()
    # DEBUG: write to dedicated file to diagnose 500 errors
    try:
        with open(r"D:\web\ask_debug.txt", "a", encoding="utf-8") as _dbg:
            _dbg.write(f"[{time.strftime('%H:%M:%S')}] _ask_inner called: {request.user_question[:50]}\n")
    except Exception:
        pass
    logger.info(f"[DEBUG] _ask_inner 진입: q={request.user_question[:30]!r}")
    raw_question = request.user_question
    # DEMO_MODE: 사용자 입력의 익명 코드를 원본 현장명으로 복원
    if DEMO_MODE and _DEMO_REVERSE_MAP:
        raw_question = _demo_restore_text(raw_question)
    user_question = normalize_question(raw_question)
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

    # 2. 정정 턴 단축 체크 + [multiturn-a] 성공 턴 직후 짧은 follow-up 감지
    is_correction = session_manager.is_correction_turn(session, user_question)
    is_followup = (not is_correction) and session_manager.is_short_followup(session, user_question)

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
    elif is_followup and session.last_intent:
        # [multiturn-a] 성공 턴 직후 짧은 follow-up → 직전 인텐트 상속
        intent_name = session.last_intent
        intent_def = intent_index.get_definition(intent_name)
        category = intent_classifier._get_category_for_intent(intent_name) or "기타"
        classify_method = "followup_inherit"
        logger.info(f"짧은 follow-up 상속: intent={intent_name} q={user_question!r}")
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

    _t_classified = time.perf_counter()

    # 4. 하이브리드 파라미터 추출
    new_params = await asyncio.to_thread(
        param_extractor_instance.extract_all, user_question, intent_name
    )

    _t_extracted = time.perf_counter()

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
        "배수지": "RESERVOIR_NETWORK_DIAGRAM",
        "가압장": "BOOSTER_STATION_NETWORK_DIAGRAM",
        "소블록": "BLOCK_NETWORK_DIAGRAM", "중블록": "BLOCK_NETWORK_DIAGRAM", "대블록": "BLOCK_NETWORK_DIAGRAM",
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

    # 4.10. RESERVOIR SUPPLY 인텐트: 기본 날짜 설정
    if intent_name in _SUPPLY_INTENTS:
        _is_monthly = "MONTHLY" in intent_name
        if not new_params.get("from_ts"):
            _days_back = 365 if _is_monthly else 30
            new_params["from_ts"] = (datetime.now() - timedelta(days=_days_back)).strftime("%Y-%m-%d")
        if not new_params.get("to_ts"):
            new_params["to_ts"] = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        logger.info(f"SUPPLY defaults: intent={intent_name}, from={new_params['from_ts']}, to={new_params['to_ts']}")

    # 4.11. ANOMALY 인텐트: 선택적 시설 필터 + 범위 라벨 설정
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
    # 2026-05-10 fix: 사용자 메시지에 시설 명시 안 했으면 세션 컨텍스트 무시 (전체 조회 의도)
    _ALARM_PIE_INTENTS = {"FACILITY_ALARM_CAUSE_DIAGNOSIS_RANK", "FACILITY_ALARM_TOP_COUNT"}
    if intent in _ALARM_PIE_INTENTS:
        _u_sitename = extract_sitename(request.user_question or "")
        _u_facilitytype = extract_facility_type_from_question(request.user_question or "")
        if not _u_sitename or not params.get("sitename") or params.get("sitename") == "%%":
            params["sitename"] = ""
        if not _u_facilitytype or not params.get("facilitytype") or params.get("facilitytype") == "%%":
            params["facilitytype"] = ""
        # 경보 카테고리 필터 (수위/압력/통신/전원/펌프/밸브/수질/유량)
        clause, label = _extract_alarm_filter(request.user_question or "")
        params["alarm_filter_clause"] = clause
        params["alarm_label"] = label
        # {limit} 기본값: 질문에서 추출, 없으면 10
        if not params.get("limit"):
            params["limit"] = str(extract_limit(request.user_question or ""))
        # {from_ts}/{to_ts} 기본값: 최근 30일
        if not params.get("from_ts"):
            params["from_ts"] = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        if not params.get("to_ts"):
            params["to_ts"] = datetime.now().strftime("%Y-%m-%d")

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

    # ANOMALY_FACILITY_DETAIL: stale 데이터 대응 — 공통 헬퍼 사용
    if intent == "ANOMALY_FACILITY_DETAIL" and sql_combined:
        try:
            _mb_rows, _ = execute_sql("SELECT max(bucket) FROM cagg_5min_raw_stats_ai", {})
            if _mb_rows and _mb_rows[0][0]:
                sql_combined = adjust_sql_time_window_to_max_bucket(
                    sql_combined, _mb_rows[0][0], label="FACILITY_DETAIL",
                )
        except Exception as _e:
            logger.warning(f"FACILITY_DETAIL: max(bucket) 확인 실패: {_e}")

    # 빈 SQL 체크 (동적 SQL 생성 인텐트는 커스텀 핸들러에서 sql_combined 설정)
    _DYNAMIC_SQL_INTENTS = {"ALARM_ABNORMAL_LOCATIONS", "FACILITY_CATALOG_TREND_TABLE", "RESERVOIR_LEVEL_HUNTING_CHECK", "RESERVOIR_LEVEL_CAUSE_ANALYSIS", "ANOMALY_CROSS_FACILITY", "ANOMALY_FLOW_BALANCE", "EQUIPMENT_FAULT_STATUS", "NIGHT_MIN_FLOW_SUMMARY_TABLE", "NIGHT_MIN_FLOW_STATUS", "TAG_DAILY_MISSING_SUMMARY", "FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS", "NETWORK_UPSTREAM_FAULT_ANALYSIS"} | _SUPPLY_INTENTS
    if intent not in _DYNAMIC_SQL_INTENTS and (not sql_combined or not sql_combined.strip()):
        rendered_answer = render_answer_template(answer_template, params)
        rendered_answer = apply_corrections_to_answer(rendered_answer, params)
        return build_success_response(
            intent=intent,
            answer=rendered_answer,
            graph_type=graph_type,
            session_id=sid,
        )

    # FACILITY_TREND + 야간최소유량: tb_night_min_flow_daily 테이블 조회
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
        if params.get("sitename") == "%%":
            params["sitename"] = "전체"
        # tb_night_min_flow_daily 직접 조회 (사전 집계 테이블)
        _nmf_sn = params.get("sitename", "전체")
        _nmf_ft = params.get("facilitytype", "소블록")
        _nmf_from = params.get("from_ts", "")
        _nmf_to = params.get("to_ts", "")
        try:
            # 다중 시설 지원: extra_sitenames도 NMF 조회
            _nmf_site_list = [_nmf_sn]
            if extra_sitenames:
                _nmf_site_list.extend(extra_sitenames)
                extra_sitenames = None  # 이중 처리 방지
            _all_nmf_rows: list = []
            _nmf_cols_ref = None
            for _sn_i in _nmf_site_list:
                _r_i, _c_i = await asyncio.to_thread(
                    _execute_night_min_flow_query, _sn_i, _nmf_ft, _nmf_from, _nmf_to
                )
                if _r_i:
                    _all_nmf_rows.extend(_r_i)
                    if _nmf_cols_ref is None:
                        _nmf_cols_ref = _c_i
            rows = _all_nmf_rows
            columns = _nmf_cols_ref or []
            if len(_nmf_site_list) > 1:
                params["sitename"] = ", ".join(_nmf_site_list)
        except Exception as e:
            logger.warning(f"야간최소유량 테이블 조회 실패: {e}")
            rows, columns = [], []
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
            _site_esc = _sql_escape_literal(_site)
            where_parts.append(f"sitename = '{_site_esc}'")
        if _category:
            _cat_esc = _sql_escape_literal(_category)
            where_parts.append(f"alarm_category = '{_cat_esc}'")
        where_clause = " AND ".join(where_parts)
        sql_combined = (
            f"SELECT sitename, facilitytype, alarm_msg, alarm_category,"
            f" TO_CHAR(alarm_start_time, 'YYYY-MM-DD HH24:MI:SS') AS alarm_start_time"
            f" FROM tb_equipment_alarm_report"
            f" WHERE {where_clause}"
            f" ORDER BY alarm_start_time DESC;"
        )

    # 커스텀 핸들러용 rows/columns 초기화
    rows: list = []
    columns: list = []

    # ALARM_ABNORMAL_LOCATIONS: 경보 이상 발생 지점 (동적 필터)
    if intent == "ALARM_ABNORMAL_LOCATIONS":
        alarm_filter_clause, alarm_label = _extract_alarm_filter(user_question)
        alarm_level_clause, alarm_level_label = _extract_alarm_level(user_question)
        _ftype = params.get("facilitytype", "")

        where_parts = ["alarm_status = '진행중'"]
        if _ftype:
            _ftype_esc = _sql_escape_literal(_ftype)
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

    # NETWORK_UPSTREAM_FAULT_ANALYSIS: SSLVPN/UTM 계층 통신이상 원인 분석
    if intent == "NETWORK_UPSTREAM_FAULT_ANALYSIS":
        sql_combined = """
WITH latest AS (
    SELECT equipment_id, MAX(check_time) AS mt
    FROM tb_network_status
    GROUP BY equipment_id
),
current_status AS (
    SELECT ns.equipment_id, ns.is_alive
    FROM tb_network_status ns
    JOIN latest ON latest.equipment_id = ns.equipment_id AND latest.mt = ns.check_time
),
sslvpn_summary AS (
    SELECT
        ei_t.sitename || ' ' || ei_t.equipmenttype AS sslvpn_id,
        COUNT(*)                                                            AS total_lte,
        COUNT(*) FILTER (WHERE NOT COALESCE(cs_s.is_alive, false))         AS down_lte,
        bool_or(cs_t.is_alive)                                             AS sslvpn_alive,
        array_agg(ei_s.sitename ORDER BY ei_s.sitename)
            FILTER (WHERE NOT COALESCE(cs_s.is_alive, false))              AS down_sites
    FROM tb_network_link nl
    JOIN tb_equipment_info ei_s ON ei_s.equipment_id = nl.source_equipment_id
    JOIN tb_equipment_info ei_t ON ei_t.equipment_id = nl.target_equipment_id
    LEFT JOIN current_status cs_s ON cs_s.equipment_id = nl.source_equipment_id
    LEFT JOIN current_status cs_t ON cs_t.equipment_id = nl.target_equipment_id
    WHERE ei_s.equipmenttype = 'LTE 모뎀'
      AND ei_t.equipmenttype = 'SSLVPN'
    GROUP BY ei_t.sitename, ei_t.equipmenttype
),
utm_info AS (
    SELECT
        COUNT(*)                                                            AS total_utm,
        COUNT(*) FILTER (WHERE NOT COALESCE(cs.is_alive, true))            AS down_utm
    FROM tb_equipment_info ei
    LEFT JOIN current_status cs ON cs.equipment_id = ei.equipment_id
    WHERE ei.equipmenttype IN ('UTM', 'FA망 현대화사업소 UTM')
),
total_lte AS (
    SELECT
        COUNT(*)                                                            AS global_lte_total,
        COUNT(*) FILTER (WHERE NOT COALESCE(cs.is_alive, false))           AS global_lte_down
    FROM tb_equipment_info ei
    LEFT JOIN current_status cs ON cs.equipment_id = ei.equipment_id
    WHERE ei.equipmenttype = 'LTE 모뎀'
)
SELECT
    ss.sslvpn_id,
    ss.total_lte::int,
    ss.down_lte::int,
    ss.sslvpn_alive,
    ss.down_sites,
    ui.total_utm::int,
    ui.down_utm::int,
    tl.global_lte_total::int,
    tl.global_lte_down::int
FROM sslvpn_summary ss
CROSS JOIN utm_info ui
CROSS JOIN total_lte tl
ORDER BY ss.down_lte DESC, ss.sslvpn_id
"""

    # RESERVOIR_LEVEL_HUNTING_CHECK: 3시간 방향전환 분석 (커스텀 핸들러)
    if intent == "RESERVOIR_LEVEL_HUNTING_CHECK":
        _sn = params.get("sitename", "")
        try:
            _hunt_rows, _hunt_cols = await asyncio.to_thread(
                _execute_hunting_check, _sn,
            )
            if _hunt_rows:
                rows = _hunt_rows
                columns = _hunt_cols
        except Exception as e:
            logger.error(f"HUNTING_CHECK 쿼리 실패: {e}")

    # RESERVOIR_LEVEL_CAUSE_ANALYSIS: 수위 변동 원인 분석 (Node-RED 로직 기반)
    if intent == "RESERVOIR_LEVEL_CAUSE_ANALYSIS":
        _sn = params.get("sitename", "")
        try:
            _cause_rows, _cause_cols = await asyncio.to_thread(
                _execute_level_cause_analysis, _sn,
            )
            if _cause_rows:
                rows = _cause_rows
                columns = _cause_cols
        except Exception as e:
            logger.error(f"LEVEL_CAUSE_ANALYSIS 쿼리 실패: {e}")

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

    # RESERVOIR SUPPLY: 유량적산 기반 일별/월별 공급량
    if intent in _SUPPLY_INTENTS:
        _mode = "monthly" if "MONTHLY" in intent else "daily"
        _from_str = params.get("from_ts", "")
        _to_str = params.get("to_ts", "")
        try:
            _from_d = datetime.strptime(_from_str[:10], "%Y-%m-%d").date()
            _to_d = datetime.strptime(_to_str[:10], "%Y-%m-%d").date()
            _sup_rows, _sup_cols = await asyncio.to_thread(
                _execute_reservoir_supply_query_with_conn, _mode, _from_d, _to_d
            )
            if _sup_rows:
                rows = _sup_rows
                columns = _sup_cols
            params["total_count"] = str(len(_sup_rows))
        except Exception as e:
            logger.error(f"RESERVOIR SUPPLY 쿼리 실패 ({intent}): {e}")

    # ANOMALY_CROSS_FACILITY: 시설간 교차 검증 (SQL 미사용, 인과 인덱스 직접 조회)
    if intent == "ANOMALY_CROSS_FACILITY":
        try:
            from anomaly_detector import cross_facility_check_all

            mismatches = await asyncio.to_thread(
                cross_facility_check_all, _query_recent_values, _CAUSAL_INDEX,
            )
            params["_cross_facility_mismatches"] = mismatches
            rows = [["cross_facility_done"]]
            columns = ["status"]
            logger.info(f"ANOMALY_CROSS_FACILITY: {len(mismatches)}건 불일치")
        except Exception as e:
            logger.error(f"ANOMALY_CROSS_FACILITY 실패: {e}")
            params["_cross_facility_mismatches"] = []
            rows = [["cross_facility_error"]]
            columns = ["status"]

    # EQUIPMENT_FAULT_STATUS: 설비 장애 전용 (ANOMALY_SCAN_ALL 캐시의 DI 고장 재사용)
    if intent == "EQUIPMENT_FAULT_STATUS":
        cache = (_ANOMALY_SCAN_CACHE or {}).get("processed_data", {})
        impacts = cache.get("equipment_failure_impacts") or []
        params["_equipment_failure_impacts"] = impacts
        rows = [["equipment_fault_done"]]
        columns = ["status"]
        logger.info(f"EQUIPMENT_FAULT_STATUS: 설비 장애 {len(impacts)}건 (스캔 캐시)")

    # ANOMALY_FLOW_BALANCE: 물 수지 검증 (SQL 미사용, 인과 인덱스 + 시계열 직접 조회)
    if intent == "ANOMALY_FLOW_BALANCE":
        _fb_sitename = params.get("sitename")
        try:
            if _FLOW_BALANCE_CACHE and _FLOW_BALANCE_CACHE_TIME:
                cache_age = (datetime.now() - _FLOW_BALANCE_CACHE_TIME).total_seconds()
                if cache_age < _FLOW_BALANCE_CACHE_TTL:
                    params["_flow_balance_edges"] = _filter_flow_balance_edges(_FLOW_BALANCE_CACHE, _fb_sitename)
                    rows = [["flow_balance_cached"]]
                    columns = ["status"]
                    logger.info(f"ANOMALY_FLOW_BALANCE 캐시 히트 ({cache_age:.0f}초 전), sitename={_fb_sitename}")
                else:
                    raise ValueError("cache expired")
            else:
                raise ValueError("no cache")
        except (ValueError, Exception):
            try:
                from flow_balance import compute_flow_balance_all
                tag_info = await asyncio.to_thread(_get_tag_datainfo_cache)
                edges = await asyncio.to_thread(
                    compute_flow_balance_all,
                    _query_flow_timeseries, _CAUSAL_INDEX, tag_info,
                )
                params["_flow_balance_edges"] = _filter_flow_balance_edges(edges, _fb_sitename)
                rows = [["flow_balance_done"]]
                columns = ["status"]
                logger.info(f"ANOMALY_FLOW_BALANCE: {len(edges)}엣지, sitename={_fb_sitename}")
            except Exception as e2:
                logger.error(f"ANOMALY_FLOW_BALANCE 실패: {e2}")
                params["_flow_balance_edges"] = []
                rows = [["flow_balance_error"]]
                columns = ["status"]

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

    # ── ANOMALY_SCAN_ALL: stale-while-revalidate 캐시 반환 ──
    if intent == "ANOMALY_SCAN_ALL":
        # 캐시 없음(서버 최초 시작 후 약 2분 이내) → 즉시 안내 메시지 반환
        if not _ANOMALY_SCAN_CACHE:
            return build_error_response(
                "전체 센서 점검 데이터를 준비 중입니다 (서버 시작 후 약 1~2분 소요). 잠시 후 다시 질문해 주세요.", sid
            )
        # 캐시 있으면 신선/만료 무관 즉시 반환 (백그라운드 루프가 5분마다 자동 갱신)
        cache_age = (datetime.now() - _ANOMALY_SCAN_CACHE_TIME).total_seconds() if _ANOMALY_SCAN_CACHE_TIME else 0
        logger.info(f"ANOMALY_SCAN_ALL 캐시 반환 ({cache_age:.0f}초 전) sitename={params.get('sitename')!r} facilitytype={params.get('facilitytype')!r}")
        _c = _ANOMALY_SCAN_CACHE
        _c_rows = _c["rows"]
        _c_cols = _c["columns"]
        _c_data = dict(_c["processed_data"])  # 복사 (필터 시 수정)
        _c_tmpl = _c["answer_template"]

        # facilitytype / group_code 필터 적용
        _c_rows = _filter_anomaly_cache_rows(_c_rows, _c_cols, params)
        _scope = build_anomaly_scope_label(params)
        if _scope != "전체":
            _c_data["total_tag_count"] = len(_c_rows)
            # 필터된 결과로 카운트 재계산
            from anomaly_detector import count_anomaly_levels
            _fc = count_anomaly_levels(_c_rows, _c_cols)
            _c_data["error_count"] = _fc["이상"]
            _c_data["warn_count"] = _fc["주의"]
            _c_data["ok_count"] = _fc["정상"]
            # verdict 기반 교차이상 카운트 재계산
            _vd_idx = _c_cols.index("verdict") if "verdict" in _c_cols else None
            if _vd_idx is not None:
                _c_data["cross_anomaly_count"] = sum(
                    1 for r in _c_rows if r[_vd_idx] in ("교차이상", "교차주의", "복합이상")
                )

        params["total_count"] = str(len(_c_rows))
        rendered = render_answer_template(_c_tmpl, _c_data)
        rendered = apply_corrections_to_answer(rendered, params)

        # 테이블 데이터 구성 (summary 타입)
        csv_fn = save_csv(_c_rows, _c_cols, intent, sid)
        _total = len(_c_rows)
        if _total > MAX_TABLE_ROWS:
            _sampled = stratified_sample(_c_rows, _c_cols, MAX_TABLE_ROWS)
            _resp_data = [dict(zip(_c_cols, r)) for r in _sampled]
            _trunc = True
        else:
            _resp_data = [dict(zip(_c_cols, r)) for r in _c_rows]
            _trunc = False

        return build_success_response(
            intent=intent,
            answer=rendered,
            graph_type=graph_type,
            data=_resp_data,
            table_columns=table_columns,
            table_type=table_type,
            session_id=sid,
            csv_url=f"/csv/{csv_fn}",
            total_rows=_total,
            data_truncated=_trunc,
            intent_candidates=intent_candidates,
            site_group_distribution=_c_data.get("site_group_distribution"),
            cross_anomaly_count=_c_data.get("cross_anomaly_count"),
            cross_facility_mismatches=_filter_cross_mismatches(_c_data.get("cross_facility_mismatches"), params.get("sitename")),
            cross_facility_mismatch_count=len(_filter_cross_mismatches(_c_data.get("cross_facility_mismatches"), params.get("sitename")) or []),
            data_quality_issues=_filter_by_sitename(_c_data.get("data_quality_issues"), params.get("sitename")),
            equipment_failure_impacts=_filter_by_sitename(_c_data.get("equipment_failure_impacts"), params.get("sitename")),
            equipment_failure_count=len(_filter_by_sitename(_c_data.get("equipment_failure_impacts"), params.get("sitename")) or []),
            flow_balance_summary=_filter_flow_balance(_c_data.get("flow_balance_summary"), params.get("sitename")),
            ml_model_count=_c_data.get("ml_model_count"),
            ml_anomaly_count=_c_data.get("ml_anomaly_count"),
            ml_agree_count=_c_data.get("ml_agree_count"),
            ml_tier1_count=_c_data.get("ml_tier1_count"),
            ml_tier2_count=_c_data.get("ml_tier2_count"),
        )

    # FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS: 청크 직접 쿼리 (fn_night_min_flow_stats 대체, 53s→~10s)
    if intent == "FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS":
        _sd_sn = params.get("sitename", "")
        _sd_ft = params.get("facilitytype", "소블록")
        try:
            _sd_rows, _sd_cols, _sd_stats_list = await asyncio.to_thread(
                _execute_night_min_flow_stddev_query, _sd_sn, _sd_ft,
            )
            if _sd_rows:
                _sd_data = [dict(zip(_sd_cols, r)) for r in _sd_rows]
                _sd_kwargs: dict = {}
                if len(_sd_rows) == 1:
                    _sd_kwargs["stddev_stats"] = _extract_stddev_stats(_sd_data[0])
                    _sd_rendered = render_answer_template(answer_template, params) if isinstance(answer_template, dict) else {}
                elif _sd_stats_list:
                    _sd_kwargs["stddev_stats_list"] = _sd_stats_list
                    # 전체 조회: 요약 텍스트 직접 생성
                    _sd_ft_label = params.get("facilitytype", "소블록")
                    _raw_sn = params.get("sitename", "")
                    _sd_sn_label = "전체" if (not _raw_sn or _raw_sn == "%%") else _raw_sn
                    _n_normal = sum(1 for s in _sd_stats_list if (s.get("excess") or 0) <= 0)
                    _n_exceed = len(_sd_stats_list) - _n_normal
                    _sd_summary = f"{_sd_sn_label} {_sd_ft_label} 야간최소유량 표준편차 분석 결과입니다."
                    _sd_rendered = {
                        "summary": _sd_summary,
                        "detail": [
                            {"prefix": "ㆍ", "text": f"총 {len(_sd_stats_list)}개 시설 분석 — 정상 {_n_normal}개, 신뢰구간 초과 {_n_exceed}개"},
                        ],
                    }
                else:
                    _sd_rendered = render_answer_template(answer_template, params) if isinstance(answer_template, dict) else {}
                return build_success_response(
                    intent=intent, answer=_sd_rendered, graph_type=graph_type,
                    data=_sd_data, columns=_sd_cols,
                    session_id=sid, intent_candidates=intent_candidates,
                    total_rows=len(_sd_rows),
                    **_sd_kwargs,
                )
        except Exception as e:
            logger.warning(f"표준편차분석 청크 쿼리 실패, 원본 함수 폴백: {e}")

    # LEAK_CUSUM_ANALYSIS: 청크 직접 쿼리 (fn_night_min_flow_summary 대체, rows만 채움 → process_sql_result에서 CUSUM)
    if intent == "LEAK_CUSUM_ANALYSIS":
        _lc_sn = params.get("sitename", "전체")
        _lc_ft = params.get("facilitytype", "소블록")
        _lc_from = params.get("from_ts", "")
        _lc_to = params.get("to_ts", "")
        try:
            _lc_rows, _lc_cols = await asyncio.to_thread(
                _execute_night_min_flow_query,
                _lc_sn, _lc_ft, _lc_from, _lc_to,
            )
            if _lc_rows:
                rows = _lc_rows
                columns = _lc_cols
                logger.info(f"LEAK_CUSUM 청크 쿼리: {len(_lc_rows)}행")
        except Exception as e:
            logger.warning(f"LEAK_CUSUM 청크 쿼리 실패, 원본 함수 폴백: {e}")

    # NIGHT_MIN_FLOW_STATUS: 커스텀 핸들러 (다중 시설 지원)
    if intent == "NIGHT_MIN_FLOW_STATUS":
        _nfs_ft = params.get("facilitytype", "소블록")
        # 다중 시설 처리: 메인 sitename + extra_sitenames
        _nfs_site_list = [params.get("sitename", "")]
        if extra_sitenames:
            _nfs_site_list.extend(extra_sitenames)
            extra_sitenames = None  # 이중 처리 방지
        _nfs_result_cols = ["sitename", "facilitytype", "label", "datadesc",
                            "current_val", "unit", "log_time", "avg_month", "avg_year"]
        _all_nfs_rows: list = []
        from collections import defaultdict
        for _nfs_sn in _nfs_site_list:
            if not _nfs_sn:
                continue
            try:
                _now = datetime.now()
                _cur_rows, _cur_cols = await asyncio.to_thread(
                    _execute_night_min_flow_query,
                    _nfs_sn, _nfs_ft,
                    (_now - timedelta(days=1)).strftime("%Y-%m-%d"),
                    _now.strftime("%Y-%m-%d"),
                )
                if not _cur_rows:
                    logger.info(f"NIGHT_MIN_FLOW_STATUS 데이터 없음: {_nfs_sn} {_nfs_ft}")
                    continue
                _mon_rows, _ = await asyncio.to_thread(
                    _execute_night_min_flow_query,
                    _nfs_sn, _nfs_ft,
                    (_now - timedelta(days=30)).strftime("%Y-%m-%d"),
                    _now.strftime("%Y-%m-%d"),
                )
                _yr_rows, _ = await asyncio.to_thread(
                    _execute_night_min_flow_query,
                    _nfs_sn, _nfs_ft,
                    (_now - timedelta(days=365)).strftime("%Y-%m-%d"),
                    _now.strftime("%Y-%m-%d"),
                )
                _tag_vals_m: dict[str, list] = defaultdict(list)
                _tag_vals_y: dict[str, list] = defaultdict(list)
                for r in _mon_rows:
                    rd = dict(zip(_cur_cols, r))
                    _tag_vals_m[rd.get("tagsn", "")].append(float(rd.get("val") or 0))
                for r in _yr_rows:
                    rd = dict(zip(_cur_cols, r))
                    _tag_vals_y[rd.get("tagsn", "")].append(float(rd.get("val") or 0))
                _seen: set = set()
                for r in reversed(_cur_rows):
                    rd = dict(zip(_cur_cols, r))
                    _tsn = rd.get("tagsn", "")
                    if _tsn in _seen:
                        continue
                    _seen.add(_tsn)
                    _cv = float(rd.get("val") or 0)
                    _mv = _tag_vals_m.get(_tsn, [])
                    _yv = _tag_vals_y.get(_tsn, [])
                    _all_nfs_rows.append((
                        rd.get("sitename", ""), rd.get("facilitytype", ""),
                        rd.get("label", rd.get("datainfo", "")), rd.get("datadesc", ""),
                        round(_cv, 2), rd.get("unit") or "", rd.get("log_time", ""),
                        round(sum(_mv) / len(_mv), 2) if _mv else None,
                        round(sum(_yv) / len(_yv), 2) if _yv else None,
                    ))
                logger.info(f"NIGHT_MIN_FLOW_STATUS '{_nfs_sn}': {len(_all_nfs_rows)}행 누적")
            except Exception as e:
                logger.warning(f"NIGHT_MIN_FLOW_STATUS '{_nfs_sn}' 커스텀 실패, 폴백: {e}")
        if _all_nfs_rows:
            sql_combined = "-- custom handler"
            rows = _all_nfs_rows
            columns = _nfs_result_cols
            if len([s for s in _nfs_site_list if s]) > 1:
                params["sitename"] = ", ".join(s for s in _nfs_site_list if s)
            logger.info(f"NIGHT_MIN_FLOW_STATUS 커스텀 완료: {len(_all_nfs_rows)}행")

    # NIGHT_MIN_FLOW_SUMMARY_TABLE: 청크 직접 쿼리 (fn_night_min_flow_summary 대체)
    if intent == "NIGHT_MIN_FLOW_SUMMARY_TABLE":
        _nmf_sn = params.get("sitename", "전체")
        _nmf_ft = params.get("facilitytype", "소블록")
        _nmf_from = params.get("from_ts", "")
        _nmf_to = params.get("to_ts", "")
        try:
            _nmf_rows, _nmf_cols = await asyncio.to_thread(
                _execute_night_min_flow_query,
                _nmf_sn, _nmf_ft, _nmf_from, _nmf_to,
            )
            if _nmf_rows:
                csv_fn = save_csv(_nmf_rows, _nmf_cols, intent, sid)
                _total = len(_nmf_rows)
                if _total > MAX_TABLE_ROWS:
                    _sampled = stratified_sample(_nmf_rows, _nmf_cols, MAX_TABLE_ROWS)
                    _resp_data = [dict(zip(_nmf_cols, r)) for r in _sampled]
                    _trunc = True
                else:
                    _resp_data = [dict(zip(_nmf_cols, r)) for r in _nmf_rows]
                    _trunc = False
                _nmf_rendered = answer_template if isinstance(answer_template, dict) else {}
                return build_success_response(
                    intent=intent, answer=_nmf_rendered, graph_type=graph_type,
                    data=_resp_data, columns=_nmf_cols, csv_file=csv_fn,
                    session_id=sid, intent_candidates=intent_candidates,
                    total_rows=_total, data_truncated=_trunc,
                )
        except Exception as e:
            logger.warning(f"야간최소유량 청크 쿼리 실패, 원본 함수 폴백: {e}")

    # TAG_DAILY_MISSING_SUMMARY: 청크 직접 쿼리 (fn_tag_daily_summary 대체)
    if intent == "TAG_DAILY_MISSING_SUMMARY":
        _tdm_sn = params.get("sitename", "")
        _tdm_ft = params.get("facilitytype", "")
        _tdm_from = params.get("from_ts", "")
        _tdm_to = params.get("to_ts", "")
        _tdm_di = params.get("datainfo", "")
        logger.info(f"결측분석 청크 핸들러 진입: from={_tdm_from} to={_tdm_to} sn={_tdm_sn} ft={_tdm_ft}")
        try:
            _tdm_rows, _tdm_cols = await asyncio.to_thread(
                _execute_tag_daily_summary_query,
                _tdm_from, _tdm_to, _tdm_sn, _tdm_ft, _tdm_di,
            )
            logger.info(f"결측분석 청크 결과: {len(_tdm_rows)}행")
            if _tdm_rows:
                csv_fn = save_csv(_tdm_rows, _tdm_cols, intent, sid)
                _total = len(_tdm_rows)
                _resp_data = [dict(zip(_tdm_cols, r)) for r in _tdm_rows]
                # placeholder 치환 — params 기반 ({sitename}, {facilitytype} 등)
                _tdm_rendered = (
                    render_answer_template(answer_template, {**params, "total_count": str(_total)})
                    if isinstance(answer_template, dict) else {}
                )
                _tdm_rendered = apply_corrections_to_answer(_tdm_rendered, params)
                return build_success_response(
                    intent=intent, answer=_tdm_rendered, graph_type=graph_type,
                    data=_resp_data, columns=_tdm_cols, csv_file=csv_fn,
                    session_id=sid, intent_candidates=intent_candidates,
                    total_rows=_total,
                )
        except Exception as e:
            logger.warning(f"결측분석 청크 쿼리 실패, 원본 함수 폴백: {e}")

    # TIMESERIES 인텐트: 그룹 기반 + 청크 직접 쿼리 (JOIN 플래너 우회)
    if intent in _TIMESERIES_CHUNK_INTENTS:
        _sn = params.get("sitename", "%%")
        _ft_ts = params.get("facilitytype", "%%")
        _from = params.get("from_ts", "")
        _to = params.get("to_ts", "")

        # tagtype 결정 (tagtype 주입 블록과 동일 로직)
        if intent == "FACILITY_DIGITAL_STATUS_TIMESERIES_TABLE":
            _tagtype = "Digital Input"
        elif intent == "FACILITY_TAG_DATA_TABLE":
            _dk = params.get("datakey") or params.get("datainfo") or ""
            _tagtype = ("Digital Input" if "밸브" in _dk
                        else "Analog Output" if "설정" in _dk
                        else "Analog Input")
        else:
            _tagtype = "Analog Input"

        # datainfo 패턴 결정
        if intent == "FACILITY_FLOW_INSTANT_TIMESERIES_TABLE":
            _di_pat = "(유량.*순시|순시.*유량)"
        elif intent == "FACILITY_FLOW_ACCUMULATED_TIMESERIES_TABLE":
            _di_pat = "유량.*적산"
        else:
            _di_pat = params.get("datainfo", ".*")

        # group_code 결정: param_extractor에서 추출 → intent-specific 오버라이드
        _group = params.get("group_code")
        if intent == "FACILITY_FLOW_INSTANT_TIMESERIES_TABLE":
            _group = _group or "FLOW_INSTANT"
        elif intent == "FACILITY_FLOW_ACCUMULATED_TIMESERIES_TABLE":
            _group = _group or "FLOW_CUMULATIVE"

        try:
            _ts_rows, _ts_cols = await asyncio.to_thread(
                _execute_timeseries_query,
                _sn, _ft_ts, _tagtype, _di_pat, _from, _to,
                _group,
            )
            if _ts_rows:
                rows = _ts_rows
                columns = _ts_cols
                logger.info(f"TIMESERIES 청크 쿼리: {len(_ts_rows)}행 ({intent}, group={_group})")
        except Exception as e:
            logger.error(f"TIMESERIES 청크 쿼리 실패 ({intent}): {e}")

    # 커스텀 핸들러에서 rows/columns가 이미 채워진 경우 SQL 실행 건너뜀
    _t_sql_start = time.perf_counter()
    if not rows:
        try:
            rows, columns = await asyncio.to_thread(execute_sql, sql_combined, params)
        except psycopg2.OperationalError as e:
            logger.error(f"DB 접속 오류: {e}")
            logger.error(f"  sql_combined[:500]: {sql_combined[:500]}")
            return build_error_response(
                message="데이터베이스 연결 오류가 발생했습니다.",
                session_id=sid,
            )
        except psycopg2.Error as e:
            err_msg = str(e)
            logger.error(f"SQL 실행 오류: {err_msg}")
            logger.error(f"  sql_combined[:500]: {sql_combined[:500]}")
            logger.error(f"  params keys: {list(params.keys())}")
            # 사용자에게 친절한 에러 메시지
            if "Invalid period" in err_msg:
                user_msg = "조회 기간이 올바르지 않습니다. 시작일이 종료일보다 앞서야 합니다."
            else:
                user_msg = "데이터 조회 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
            return build_error_response(
                message=user_msg,
                session_id=sid,
            )
    _t_sql_end = time.perf_counter()

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
    logger.info(f"[DEBUG] process_sql_result 시작: intent={intent} rows={len(rows)}")
    try:
        processed_data = process_sql_result(rows, columns, intent_def, params)
        logger.info(f"[DEBUG] process_sql_result 완료: keys={list(processed_data.keys())[:5]}")
    except JsonbSchemaViolation as e:
        logger.error(f"JSONB 스키마 위반: {e.message}, path: {e.path}")
        return build_error_response(
            message="데이터 구조 오류가 발생했습니다.",
            session_id=sid,
        )

    # ANOMALY_SCAN_ALL: 교차 검증 + per-row 종합 판정
    if intent == "ANOMALY_SCAN_ALL" and _CAUSAL_INDEX:
        try:
            from anomaly_detector import cross_facility_check_all, enrich_rows_with_cross_verdict
            cross_mismatches = await asyncio.to_thread(
                cross_facility_check_all, _query_recent_values, _CAUSAL_INDEX,
                lookback_minutes=180,
            )
            if cross_mismatches:
                processed_data["cross_facility_mismatches"] = cross_mismatches
                processed_data["cross_facility_mismatch_count"] = len(cross_mismatches)
            logger.info(f"SCAN_ALL 교차 검증: {len(cross_mismatches)}건 불일치")
            # per-row cross_status/verdict 병합
            _profiles = site_profiler.profiles if site_profiler and site_profiler.profiles else None
            enrich_rows_with_cross_verdict(rows, columns, cross_mismatches, site_profiles=_profiles)
            _vd_idx = columns.index("verdict") if "verdict" in columns else None
            if _vd_idx is not None:
                processed_data["cross_anomaly_count"] = sum(
                    1 for r in rows if r[_vd_idx] in ("교차이상", "교차주의", "복합이상")
                )
        except Exception as e:
            logger.warning(f"SCAN_ALL 교차 검증 실패: {e}")

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

    # STDDEV 분석: stddev_stats 추출 (개별 조회 시만)
    _stddev_stats = None
    if intent == "FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS" and response_data and len(response_data) == 1:
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
            _region = params.get("region", "R01")
            _az_conn = get_db_connection()
            try:
                _anomaly_zones = compute_anomaly_zones(rows, columns, _region, _az_conn)
            finally:
                _az_conn.close()
        except Exception as e:
            logger.warning(f"Anomaly zone computation failed: {e}")

    # 트렌드 비교 (평소 대비 / 향후 전망) — docs/trend-comparison-spec.md
    _comparison = None
    if graph_type == "plot" and rows and columns:
        try:
            from trend_comparison import compute_comparison
            _tagsn = (params.get("tagsn") or
                      (intent_def.get("tagsn") if intent_def else None) or
                      (rows[0][columns.index("tagsn")] if "tagsn" in columns else None))
            if _tagsn:
                _tc_conn = get_db_connection()
                try:
                    _comparison = compute_comparison(
                        rows=rows, columns=columns, intent=intent,
                        sitename=params.get("sitename"),
                        facilitytype=params.get("facilitytype"),
                        tagsn=_tagsn,
                        conn=_tc_conn,
                    )
                finally:
                    _tc_conn.close()
        except Exception as e:
            logger.warning(f"Trend comparison computation failed: {e}")

    _t_end = time.perf_counter()
    logger.info(
        f"⏱ /ask [{intent}|{classify_method}] "
        f"분류={(_t_classified - _t_start)*1000:.0f}ms "
        f"추출={(_t_extracted - _t_classified)*1000:.0f}ms "
        f"SQL={(_t_sql_end - _t_sql_start)*1000:.0f}ms "
        f"합계={(_t_end - _t_start)*1000:.0f}ms "
        f"rows={len(rows)}"
    )

    logger.info(f"[DEBUG] build_success_response 시작: intent={intent}")
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
        comparison=_comparison,
        intent_candidates=intent_candidates,
        site_group_distribution=processed_data.get("site_group_distribution"),
        site_group=processed_data.get("site_group"),
        pattern_analysis=processed_data.get("pattern_analysis"),
        cross_facility_mismatches=_filter_cross_mismatches(processed_data.get("cross_facility_mismatches"), params.get("sitename")),
        cross_facility_mismatch_count=len(_filter_cross_mismatches(processed_data.get("cross_facility_mismatches"), params.get("sitename")) or []),
        cross_anomaly_count=processed_data.get("cross_anomaly_count"),
        data_quality_issues=_filter_by_sitename(processed_data.get("data_quality_issues"), params.get("sitename")),
        equipment_failure_impacts=_filter_by_sitename(processed_data.get("equipment_failure_impacts"), params.get("sitename")),
        equipment_failure_count=len(_filter_by_sitename(processed_data.get("equipment_failure_impacts"), params.get("sitename")) or []),
        flow_balance_summary=_filter_flow_balance(processed_data.get("flow_balance_summary"), params.get("sitename")),
        intra_facility=processed_data.get("intra_facility"),
        equipment_diagnosis=processed_data.get("equipment_diagnosis"),
        ml_model_count=processed_data.get("ml_model_count"),
        ml_anomaly_count=processed_data.get("ml_anomaly_count"),
        ml_agree_count=processed_data.get("ml_agree_count"),
        ml_tier1_count=processed_data.get("ml_tier1_count"),
        ml_tier2_count=processed_data.get("ml_tier2_count"),
        ml_tier=processed_data.get("ml_tier"),
        ml_anomaly_score=processed_data.get("ml_anomaly_score"),
        ml_features_used=processed_data.get("ml_features_used"),
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
        _t_start = time.perf_counter()
        raw_question = request.user_question
        # DEMO_MODE: 사용자 입력의 익명 코드를 원본 현장명으로 복원
        if DEMO_MODE and _DEMO_REVERSE_MAP:
            raw_question = _demo_restore_text(raw_question)
        user_question = normalize_question(raw_question)
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

        # 2. 정정 턴 단축 체크 + [multiturn-a] 성공 턴 직후 짧은 follow-up
        is_correction = session_manager.is_correction_turn(session, user_question)
        is_followup = (not is_correction) and session_manager.is_short_followup(session, user_question)

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
        elif is_followup and session.last_intent:
            # [multiturn-a] 성공 턴 직후 짧은 follow-up → 직전 인텐트 상속
            intent_name = session.last_intent
            intent_def = intent_index.get_definition(intent_name)
            category = intent_classifier._get_category_for_intent(intent_name) or "기타"
            classify_method = "followup_inherit"
            logger.info(f"[SSE] 짧은 follow-up 상속: intent={intent_name} q={user_question!r}")
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

        _t_classified = time.perf_counter()

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

        _t_extracted = time.perf_counter()

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
            "배수지": "RESERVOIR_NETWORK_DIAGRAM",
            "가압장": "BOOSTER_STATION_NETWORK_DIAGRAM",
            "소블록": "BLOCK_NETWORK_DIAGRAM", "중블록": "BLOCK_NETWORK_DIAGRAM", "대블록": "BLOCK_NETWORK_DIAGRAM",
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

        # 4.10. RESERVOIR SUPPLY 인텐트: 기본 날짜 설정
        if intent_name in _SUPPLY_INTENTS:
            _is_monthly_sse = "MONTHLY" in intent_name
            if not new_params.get("from_ts"):
                _days_back_sse = 365 if _is_monthly_sse else 30
                new_params["from_ts"] = (datetime.now() - timedelta(days=_days_back_sse)).strftime("%Y-%m-%d")
            if not new_params.get("to_ts"):
                new_params["to_ts"] = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            logger.info(f"[SSE] SUPPLY defaults: intent={intent_name}, from={new_params['from_ts']}, to={new_params['to_ts']}")

        # 4.11. ANOMALY 인텐트: 선택적 시설 필터 + 범위 라벨 설정
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
        # 2026-05-10 fix: 사용자 메시지에 시설 명시 안 했으면 세션 컨텍스트 무시
        _ALARM_PIE_INTENTS_SSE = {"FACILITY_ALARM_CAUSE_DIAGNOSIS_RANK", "FACILITY_ALARM_TOP_COUNT"}
        if intent in _ALARM_PIE_INTENTS_SSE:
            _u_sitename_s = extract_sitename(request.user_question or "")
            _u_facilitytype_s = extract_facility_type_from_question(request.user_question or "")
            if not _u_sitename_s or not params.get("sitename") or params.get("sitename") == "%%":
                params["sitename"] = ""
            if not _u_facilitytype_s or not params.get("facilitytype") or params.get("facilitytype") == "%%":
                params["facilitytype"] = ""
            # 경보 카테고리 필터 (수위/압력/통신/전원/펌프/밸브/수질/유량)
            clause, label = _extract_alarm_filter(request.user_question or "")
            params["alarm_filter_clause"] = clause
            params["alarm_label"] = label
            # {limit} 기본값: 질문에서 추출, 없으면 10
            if not params.get("limit"):
                params["limit"] = str(extract_limit(request.user_question or ""))
            # {from_ts}/{to_ts} 기본값: 최근 30일
            if not params.get("from_ts"):
                params["from_ts"] = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
            if not params.get("to_ts"):
                params["to_ts"] = datetime.now().strftime("%Y-%m-%d")

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

        # ANOMALY_FACILITY_DETAIL: stale 데이터 대응 — 공통 헬퍼 사용
        if intent == "ANOMALY_FACILITY_DETAIL" and sql_combined:
            try:
                _mb_rows, _ = execute_sql("SELECT max(bucket) FROM cagg_5min_raw_stats_ai", {})
                if _mb_rows and _mb_rows[0][0]:
                    sql_combined = adjust_sql_time_window_to_max_bucket(
                        sql_combined, _mb_rows[0][0], label="[SSE] FACILITY_DETAIL",
                    )
            except Exception as _e:
                logger.warning(f"[SSE] FACILITY_DETAIL: max(bucket) 확인 실패: {_e}")

        # 빈 SQL 체크 (동적 SQL 생성 인텐트는 커스텀 핸들러에서 sql_combined 설정)
        _DYNAMIC_SQL_INTENTS_STREAM = {"ALARM_ABNORMAL_LOCATIONS", "FACILITY_CATALOG_TREND_TABLE", "RESERVOIR_LEVEL_HUNTING_CHECK", "RESERVOIR_LEVEL_CAUSE_ANALYSIS", "ANOMALY_CROSS_FACILITY", "ANOMALY_FLOW_BALANCE", "EQUIPMENT_FAULT_STATUS", "NIGHT_MIN_FLOW_SUMMARY_TABLE", "NIGHT_MIN_FLOW_STATUS", "TAG_DAILY_MISSING_SUMMARY", "FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS", "NETWORK_UPSTREAM_FAULT_ANALYSIS"} | _SUPPLY_INTENTS
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
            # '%%' 와일드카드 대신 '전체' 사용
            if params.get("sitename") == "%%":
                params["sitename"] = "전체"
            # NOTE: 실제 사전집계 테이블 조회(rows 채우기)는 아래 "커스텀 핸들러용
            # rows/columns 초기화"(rows: list = []) 이후에 수행한다. 여기서 채우면
            # 그 초기화에 덮여 execute_sql(느린 선언 SQL)이 재실행된다. [E-030 후속]
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
                _site_esc = _sql_escape_literal(_site)
                where_parts.append(f"sitename = '{_site_esc}'")
            if _category:
                _cat_esc = _sql_escape_literal(_category)
                where_parts.append(f"alarm_category = '{_cat_esc}'")
            where_clause = " AND ".join(where_parts)
            sql_combined = (
                f"SELECT sitename, facilitytype, alarm_msg, alarm_category,"
                f" TO_CHAR(alarm_start_time, 'YYYY-MM-DD HH24:MI:SS') AS alarm_start_time"
                f" FROM tb_equipment_alarm_report"
                f" WHERE {where_clause}"
                f" ORDER BY alarm_start_time DESC;"
            )

        # 커스텀 핸들러용 rows/columns 초기화
        rows: list = []
        columns: list = []

        # FACILITY_TREND + 야간최소유량: 사전집계 테이블 tb_night_min_flow_daily
        # 직접 조회 fast-path. 기존 SSE 는 fn_night_min_flow_summary(원시
        # 하이퍼테이블 실시간 60분 이동평균)를 호출 → 43만행 대상 약 23초 소요.
        # 비스트림 /ask 경로(3458-3487)는 이미 인덱스 스캔(<0.5초)로 최적화돼
        # 있었으나 SSE 만 누락 → 두 경로를 동일 fast-path 로 통일. 반드시 위
        # rows 초기화 이후에 채워야 execute_sql(선언 SQL) 재실행을 방지한다.
        if _is_night_min_flow:
            _nmf_sn = params.get("sitename", "전체")
            _nmf_ft = params.get("facilitytype", "소블록")
            _nmf_from = params.get("from_ts", "")
            _nmf_to = params.get("to_ts", "")
            try:
                _nmf_site_list = [_nmf_sn]
                if extra_sitenames:
                    _nmf_site_list.extend(extra_sitenames)
                    extra_sitenames = None  # 이중 처리 방지
                _all_nmf_rows: list = []
                _nmf_cols_ref = None
                for _sn_i in _nmf_site_list:
                    _r_i, _c_i = await asyncio.to_thread(
                        _execute_night_min_flow_query, _sn_i, _nmf_ft, _nmf_from, _nmf_to
                    )
                    if _r_i:
                        _all_nmf_rows.extend(_r_i)
                        if _nmf_cols_ref is None:
                            _nmf_cols_ref = _c_i
                rows = _all_nmf_rows
                columns = _nmf_cols_ref or []
                if len(_nmf_site_list) > 1:
                    params["sitename"] = ", ".join(_nmf_site_list)
                logger.info(f"[SSE] 야간최소유량 사전집계 조회 완료: {len(rows)}행")
            except Exception as e:
                logger.warning(f"[SSE] 야간최소유량 테이블 조회 실패: {e}")
                rows, columns = [], []

        # ALARM_ABNORMAL_LOCATIONS: 경보 이상 발생 지점 (동적 필터)
        if intent == "ALARM_ABNORMAL_LOCATIONS":
            alarm_filter_clause, alarm_label = _extract_alarm_filter(user_question)
            alarm_level_clause, alarm_level_label = _extract_alarm_level(user_question)
            _ftype = params.get("facilitytype", "")

            where_parts = ["alarm_status = '진행중'"]
            if _ftype:
                _ftype_esc = _sql_escape_literal(_ftype)
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

        # NETWORK_UPSTREAM_FAULT_ANALYSIS: SSLVPN/UTM 계층 통신이상 원인 분석 (SSE)
        if intent == "NETWORK_UPSTREAM_FAULT_ANALYSIS":
            sql_combined = """
WITH latest AS (
    SELECT equipment_id, MAX(check_time) AS mt
    FROM tb_network_status
    GROUP BY equipment_id
),
current_status AS (
    SELECT ns.equipment_id, ns.is_alive
    FROM tb_network_status ns
    JOIN latest ON latest.equipment_id = ns.equipment_id AND latest.mt = ns.check_time
),
sslvpn_summary AS (
    SELECT
        ei_t.sitename || ' ' || ei_t.equipmenttype AS sslvpn_id,
        COUNT(*)                                                            AS total_lte,
        COUNT(*) FILTER (WHERE NOT COALESCE(cs_s.is_alive, false))         AS down_lte,
        bool_or(cs_t.is_alive)                                             AS sslvpn_alive,
        array_agg(ei_s.sitename ORDER BY ei_s.sitename)
            FILTER (WHERE NOT COALESCE(cs_s.is_alive, false))              AS down_sites
    FROM tb_network_link nl
    JOIN tb_equipment_info ei_s ON ei_s.equipment_id = nl.source_equipment_id
    JOIN tb_equipment_info ei_t ON ei_t.equipment_id = nl.target_equipment_id
    LEFT JOIN current_status cs_s ON cs_s.equipment_id = nl.source_equipment_id
    LEFT JOIN current_status cs_t ON cs_t.equipment_id = nl.target_equipment_id
    WHERE ei_s.equipmenttype = 'LTE 모뎀'
      AND ei_t.equipmenttype = 'SSLVPN'
    GROUP BY ei_t.sitename, ei_t.equipmenttype
),
utm_info AS (
    SELECT
        COUNT(*)                                                            AS total_utm,
        COUNT(*) FILTER (WHERE NOT COALESCE(cs.is_alive, true))            AS down_utm
    FROM tb_equipment_info ei
    LEFT JOIN current_status cs ON cs.equipment_id = ei.equipment_id
    WHERE ei.equipmenttype IN ('UTM', 'FA망 현대화사업소 UTM')
),
total_lte AS (
    SELECT
        COUNT(*)                                                            AS global_lte_total,
        COUNT(*) FILTER (WHERE NOT COALESCE(cs.is_alive, false))           AS global_lte_down
    FROM tb_equipment_info ei
    LEFT JOIN current_status cs ON cs.equipment_id = ei.equipment_id
    WHERE ei.equipmenttype = 'LTE 모뎀'
)
SELECT
    ss.sslvpn_id,
    ss.total_lte::int,
    ss.down_lte::int,
    ss.sslvpn_alive,
    ss.down_sites,
    ui.total_utm::int,
    ui.down_utm::int,
    tl.global_lte_total::int,
    tl.global_lte_down::int
FROM sslvpn_summary ss
CROSS JOIN utm_info ui
CROSS JOIN total_lte tl
ORDER BY ss.down_lte DESC, ss.sslvpn_id
"""

        # RESERVOIR_LEVEL_HUNTING_CHECK: 3시간 방향전환 분석 (커스텀 핸들러)
        if intent == "RESERVOIR_LEVEL_HUNTING_CHECK":
            _sn = params.get("sitename", "")
            try:
                _hunt_rows, _hunt_cols = await asyncio.to_thread(
                    _execute_hunting_check, _sn,
                )
                if _hunt_rows:
                    rows = _hunt_rows
                    columns = _hunt_cols
            except Exception as e:
                logger.error(f"[SSE] HUNTING_CHECK 쿼리 실패: {e}")

        # RESERVOIR_LEVEL_CAUSE_ANALYSIS: 수위 변동 원인 분석
        if intent == "RESERVOIR_LEVEL_CAUSE_ANALYSIS":
            _sn = params.get("sitename", "")
            try:
                _cause_rows, _cause_cols = await asyncio.to_thread(
                    _execute_level_cause_analysis, _sn,
                )
                if _cause_rows:
                    rows = _cause_rows
                    columns = _cause_cols
            except Exception as e:
                logger.error(f"[SSE] LEVEL_CAUSE_ANALYSIS 쿼리 실패: {e}")

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
                logger.error(f"[SSE] FACILITY_CATALOG_TREND_TABLE 쿼리 실패: {e}")

        # RESERVOIR SUPPLY: 유량적산 기반 일별/월별 공급량
        if intent in _SUPPLY_INTENTS:
            _mode = "monthly" if "MONTHLY" in intent else "daily"
            _from_str = params.get("from_ts", "")
            _to_str = params.get("to_ts", "")
            try:
                _from_d = datetime.strptime(_from_str[:10], "%Y-%m-%d").date()
                _to_d = datetime.strptime(_to_str[:10], "%Y-%m-%d").date()
                _sup_rows, _sup_cols = await asyncio.to_thread(
                    _execute_reservoir_supply_query_with_conn, _mode, _from_d, _to_d
                )
                if _sup_rows:
                    rows = _sup_rows
                    columns = _sup_cols
                params["total_count"] = str(len(_sup_rows))
            except Exception as e:
                logger.error(f"[SSE] RESERVOIR SUPPLY 쿼리 실패 ({intent}): {e}")

        # ANOMALY_CROSS_FACILITY: 시설간 교차 검증 (SQL 미사용)
        if intent == "ANOMALY_CROSS_FACILITY":
            try:
                from anomaly_detector import cross_facility_check_all

                mismatches = await asyncio.to_thread(
                    cross_facility_check_all, _query_recent_values, _CAUSAL_INDEX,
                )
                params["_cross_facility_mismatches"] = mismatches
                rows = [["cross_facility_done"]]
                columns = ["status"]
                logger.info(f"[SSE] ANOMALY_CROSS_FACILITY: {len(mismatches)}건 불일치")
            except Exception as e:
                logger.error(f"[SSE] ANOMALY_CROSS_FACILITY 실패: {e}")
                params["_cross_facility_mismatches"] = []
                rows = [["cross_facility_error"]]
                columns = ["status"]

        # EQUIPMENT_FAULT_STATUS: 설비 장애 전용 (ANOMALY_SCAN_ALL 캐시의 DI 고장 재사용)
        if intent == "EQUIPMENT_FAULT_STATUS":
            cache = (_ANOMALY_SCAN_CACHE or {}).get("processed_data", {})
            impacts = cache.get("equipment_failure_impacts") or []
            params["_equipment_failure_impacts"] = impacts
            rows = [["equipment_fault_done"]]
            columns = ["status"]
            logger.info(f"[SSE] EQUIPMENT_FAULT_STATUS: 설비 장애 {len(impacts)}건 (스캔 캐시)")

        # ANOMALY_FLOW_BALANCE: 물 수지 검증 (SQL 미사용)
        if intent == "ANOMALY_FLOW_BALANCE":
            _fb_sitename = params.get("sitename")
            try:
                if _FLOW_BALANCE_CACHE and _FLOW_BALANCE_CACHE_TIME:
                    cache_age = (datetime.now() - _FLOW_BALANCE_CACHE_TIME).total_seconds()
                    if cache_age < _FLOW_BALANCE_CACHE_TTL:
                        params["_flow_balance_edges"] = _filter_flow_balance_edges(_FLOW_BALANCE_CACHE, _fb_sitename)
                        rows = [["flow_balance_cached"]]
                        columns = ["status"]
                        logger.info(f"[SSE] ANOMALY_FLOW_BALANCE 캐시 히트 ({cache_age:.0f}초 전), sitename={_fb_sitename}")
                    else:
                        raise ValueError("cache expired")
                else:
                    raise ValueError("no cache")
            except (ValueError, Exception):
                try:
                    from flow_balance import compute_flow_balance_all
                    tag_info = await asyncio.to_thread(_get_tag_datainfo_cache)
                    edges = await asyncio.to_thread(
                        compute_flow_balance_all,
                        _query_flow_timeseries, _CAUSAL_INDEX, tag_info,
                    )
                    params["_flow_balance_edges"] = _filter_flow_balance_edges(edges, _fb_sitename)
                    rows = [["flow_balance_done"]]
                    columns = ["status"]
                    logger.info(f"[SSE] ANOMALY_FLOW_BALANCE: {len(edges)}엣지, sitename={_fb_sitename}")
                except Exception as e2:
                    logger.error(f"[SSE] ANOMALY_FLOW_BALANCE 실패: {e2}")
                    params["_flow_balance_edges"] = []
                    rows = [["flow_balance_error"]]
                    columns = ["status"]

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
                       "FACILITY_DIGITAL_STATUS_TIMESERIES_TABLE",
                       "FACILITY_FLOW_CURRENT_TABLE",
                       "FACILITY_FLOW_ACCUMULATED_TIMESERIES_TABLE",
                       "FACILITY_FLOW_INSTANT_TIMESERIES_TABLE",
                       "FACILITY_VALVE_STATUS_CURRENT_TABLE"):
            _ft = params.get("from_ts")
            _tt = params.get("to_ts")
            if _ft and _tt and len(_tt) == 10 and _ft == _tt:
                try:
                    to_date = datetime.strptime(_tt, "%Y-%m-%d")
                    params["to_ts"] = (to_date + timedelta(days=1)).strftime("%Y-%m-%d")
                except ValueError:
                    pass

        # ── ANOMALY_SCAN_ALL: stale-while-revalidate 캐시 반환 ──
        if intent == "ANOMALY_SCAN_ALL":
            if not _ANOMALY_SCAN_CACHE:
                yield _sse_event("result", build_error_response(
                    "전체 센서 점검 데이터를 준비 중입니다 (서버 시작 후 약 1~2분 소요). 잠시 후 다시 질문해 주세요.", sid
                ))
                return
            # 캐시 있으면 항상 반환 (신선/만료 무관)
            cache_age = (datetime.now() - _ANOMALY_SCAN_CACHE_TIME).total_seconds() if _ANOMALY_SCAN_CACHE_TIME else 0
            logger.info(f"[SSE] ANOMALY_SCAN_ALL 캐시 반환 ({cache_age:.0f}초 전)")
            yield _sse_event("progress", {"step": "cache_hit", "message": "이상감지 결과를 반환합니다..."})
            _c = _ANOMALY_SCAN_CACHE
            _c_rows = _c["rows"]
            _c_cols = _c["columns"]
            _c_data = dict(_c["processed_data"])
            _c_tmpl = _c["answer_template"]

            # facilitytype / group_code 필터 적용
            _c_rows = _filter_anomaly_cache_rows(_c_rows, _c_cols, params)
            _scope = build_anomaly_scope_label(params)
            if _scope != "전체":
                _c_data["total_tag_count"] = len(_c_rows)
                from anomaly_detector import count_anomaly_levels
                _fc = count_anomaly_levels(_c_rows, _c_cols)
                _c_data["error_count"] = _fc["이상"]
                _c_data["warn_count"] = _fc["주의"]
                _c_data["ok_count"] = _fc["정상"]
                # verdict 기반 교차이상 카운트 재계산
                _vd_idx = _c_cols.index("verdict") if "verdict" in _c_cols else None
                if _vd_idx is not None:
                    _c_data["cross_anomaly_count"] = sum(
                        1 for r in _c_rows if r[_vd_idx] in ("교차이상", "교차주의", "복합이상")
                    )

            params["total_count"] = str(len(_c_rows))
            rendered = render_answer_template(_c_tmpl, _c_data)
            rendered = apply_corrections_to_answer(rendered, params)

            csv_fn = save_csv(_c_rows, _c_cols, intent, sid)
            _total = len(_c_rows)
            if _total > MAX_TABLE_ROWS:
                _sampled = stratified_sample(_c_rows, _c_cols, MAX_TABLE_ROWS)
                _resp_data = [dict(zip(_c_cols, r)) for r in _sampled]
                _trunc = True
            else:
                _resp_data = [dict(zip(_c_cols, r)) for r in _c_rows]
                _trunc = False

            yield _sse_event("result", build_success_response(
                intent=intent,
                answer=rendered,
                graph_type=graph_type,
                data=_resp_data,
                table_columns=table_columns,
                table_type=table_type,
                session_id=sid,
                csv_url=f"/csv/{csv_fn}",
                total_rows=_total,
                data_truncated=_trunc,
                intent_candidates=intent_candidates,
                site_group_distribution=_c_data.get("site_group_distribution"),
                cross_anomaly_count=_c_data.get("cross_anomaly_count"),
                cross_facility_mismatches=_filter_cross_mismatches(_c_data.get("cross_facility_mismatches"), params.get("sitename")),
                cross_facility_mismatch_count=len(_filter_cross_mismatches(_c_data.get("cross_facility_mismatches"), params.get("sitename")) or []),
                data_quality_issues=_filter_by_sitename(_c_data.get("data_quality_issues"), params.get("sitename")),
                equipment_failure_impacts=_filter_by_sitename(_c_data.get("equipment_failure_impacts"), params.get("sitename")),
                equipment_failure_count=len(_filter_by_sitename(_c_data.get("equipment_failure_impacts"), params.get("sitename")) or []),
                flow_balance_summary=_filter_flow_balance(_c_data.get("flow_balance_summary"), params.get("sitename")),
            ))
            return

        # FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS: 청크 직접 쿼리 (53s→~10s)
        if intent == "FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS":
            _sd_sn = params.get("sitename", "")
            _sd_ft = params.get("facilitytype", "소블록")
            try:
                _sd_rows, _sd_cols, _sd_stats_list = await asyncio.to_thread(
                    _execute_night_min_flow_stddev_query, _sd_sn, _sd_ft,
                )
                if _sd_rows:
                    _sd_data = [dict(zip(_sd_cols, r)) for r in _sd_rows]
                    _sd_kwargs_s: dict = {}
                    if len(_sd_rows) == 1:
                        _sd_kwargs_s["stddev_stats"] = _extract_stddev_stats(_sd_data[0])
                        _sd_rendered_s = render_answer_template(answer_template, params) if isinstance(answer_template, dict) else {}
                    elif _sd_stats_list:
                        _sd_kwargs_s["stddev_stats_list"] = _sd_stats_list
                        _sd_ft_label = params.get("facilitytype", "소블록")
                        _raw_sn = params.get("sitename", "")
                        _sd_sn_label = "전체" if (not _raw_sn or _raw_sn == "%%") else _raw_sn
                        _n_normal = sum(1 for s in _sd_stats_list if (s.get("excess") or 0) <= 0)
                        _n_exceed = len(_sd_stats_list) - _n_normal
                        _sd_summary = f"{_sd_sn_label} {_sd_ft_label} 야간최소유량 표준편차 분석 결과입니다."
                        _sd_rendered_s = {
                            "summary": _sd_summary,
                            "detail": [
                                {"prefix": "ㆍ", "text": f"총 {len(_sd_stats_list)}개 시설 분석 — 정상 {_n_normal}개, 신뢰구간 초과 {_n_exceed}개"},
                            ],
                        }
                    else:
                        _sd_rendered_s = render_answer_template(answer_template, params) if isinstance(answer_template, dict) else {}
                    yield _sse_event("result", build_success_response(
                        intent=intent, answer=_sd_rendered_s, graph_type=graph_type,
                        data=_sd_data, columns=_sd_cols,
                        session_id=sid, intent_candidates=intent_candidates,
                        total_rows=len(_sd_rows),
                        **_sd_kwargs_s,
                    ))
                    return
            except Exception as e:
                logger.warning(f"SSE 표준편차분석 청크 쿼리 실패, 원본 함수 폴백: {e}")

        # LEAK_CUSUM_ANALYSIS: 청크 직접 쿼리 (rows만 채움 → process_sql_result에서 CUSUM)
        if intent == "LEAK_CUSUM_ANALYSIS":
            _lc_sn = params.get("sitename", "전체")
            _lc_ft = params.get("facilitytype", "소블록")
            _lc_from = params.get("from_ts", "")
            _lc_to = params.get("to_ts", "")
            try:
                _lc_rows, _lc_cols = await asyncio.to_thread(
                    _execute_night_min_flow_query,
                    _lc_sn, _lc_ft, _lc_from, _lc_to,
                )
                if _lc_rows:
                    rows = _lc_rows
                    columns = _lc_cols
                    logger.info(f"SSE LEAK_CUSUM 청크 쿼리: {len(_lc_rows)}행")
            except Exception as e:
                logger.warning(f"SSE LEAK_CUSUM 청크 쿼리 실패, 원본 함수 폴백: {e}")

        # NIGHT_MIN_FLOW_STATUS: 커스텀 핸들러 (다중 시설 지원)
        if intent == "NIGHT_MIN_FLOW_STATUS":
            _nfs_ft = params.get("facilitytype", "소블록")
            _nfs_site_list = [params.get("sitename", "")]
            if extra_sitenames:
                _nfs_site_list.extend(extra_sitenames)
                extra_sitenames = None
            _nfs_result_cols = ["sitename", "facilitytype", "label", "datadesc",
                                "current_val", "unit", "log_time", "avg_month", "avg_year"]
            _all_nfs_rows: list = []
            from collections import defaultdict
            for _nfs_sn in _nfs_site_list:
                if not _nfs_sn:
                    continue
                try:
                    _now = datetime.now()
                    _cur_rows, _cur_cols = await asyncio.to_thread(
                        _execute_night_min_flow_query,
                        _nfs_sn, _nfs_ft,
                        (_now - timedelta(days=1)).strftime("%Y-%m-%d"),
                        _now.strftime("%Y-%m-%d"),
                    )
                    if not _cur_rows:
                        continue
                    _mon_rows, _ = await asyncio.to_thread(
                        _execute_night_min_flow_query,
                        _nfs_sn, _nfs_ft,
                        (_now - timedelta(days=30)).strftime("%Y-%m-%d"),
                        _now.strftime("%Y-%m-%d"),
                    )
                    _yr_rows, _ = await asyncio.to_thread(
                        _execute_night_min_flow_query,
                        _nfs_sn, _nfs_ft,
                        (_now - timedelta(days=365)).strftime("%Y-%m-%d"),
                        _now.strftime("%Y-%m-%d"),
                    )
                    _tag_vals_m: dict[str, list] = defaultdict(list)
                    _tag_vals_y: dict[str, list] = defaultdict(list)
                    for r in _mon_rows:
                        rd = dict(zip(_cur_cols, r))
                        _tag_vals_m[rd.get("tagsn", "")].append(float(rd.get("val") or 0))
                    for r in _yr_rows:
                        rd = dict(zip(_cur_cols, r))
                        _tag_vals_y[rd.get("tagsn", "")].append(float(rd.get("val") or 0))
                    _seen: set = set()
                    for r in reversed(_cur_rows):
                        rd = dict(zip(_cur_cols, r))
                        _tsn = rd.get("tagsn", "")
                        if _tsn in _seen:
                            continue
                        _seen.add(_tsn)
                        _cv = float(rd.get("val") or 0)
                        _mv = _tag_vals_m.get(_tsn, [])
                        _yv = _tag_vals_y.get(_tsn, [])
                        _all_nfs_rows.append((
                            rd.get("sitename", ""), rd.get("facilitytype", ""),
                            rd.get("label", rd.get("datainfo", "")), rd.get("datadesc", ""),
                            round(_cv, 2), rd.get("unit") or "", rd.get("log_time", ""),
                            round(sum(_mv) / len(_mv), 2) if _mv else None,
                            round(sum(_yv) / len(_yv), 2) if _yv else None,
                        ))
                    logger.info(f"SSE NIGHT_MIN_FLOW_STATUS '{_nfs_sn}': {len(_all_nfs_rows)}행 누적")
                except Exception as e:
                    logger.warning(f"SSE NIGHT_MIN_FLOW_STATUS '{_nfs_sn}' 커스텀 실패, 폴백: {e}")
            if _all_nfs_rows:
                sql_combined = "-- custom handler"
                rows = _all_nfs_rows
                columns = _nfs_result_cols
                if len([s for s in _nfs_site_list if s]) > 1:
                    params["sitename"] = ", ".join(s for s in _nfs_site_list if s)
                logger.info(f"SSE NIGHT_MIN_FLOW_STATUS 커스텀 완료: {len(_all_nfs_rows)}행")

        # NIGHT_MIN_FLOW_SUMMARY_TABLE: 청크 직접 쿼리 (fn_night_min_flow_summary 대체)
        if intent == "NIGHT_MIN_FLOW_SUMMARY_TABLE":
            _nmf_sn = params.get("sitename", "전체")
            _nmf_ft = params.get("facilitytype", "소블록")
            _nmf_from = params.get("from_ts", "")
            _nmf_to = params.get("to_ts", "")
            try:
                _nmf_rows, _nmf_cols = await asyncio.to_thread(
                    _execute_night_min_flow_query,
                    _nmf_sn, _nmf_ft, _nmf_from, _nmf_to,
                )
                if _nmf_rows:
                    csv_fn = save_csv(_nmf_rows, _nmf_cols, intent, sid)
                    _total = len(_nmf_rows)
                    if _total > MAX_TABLE_ROWS:
                        _sampled = stratified_sample(_nmf_rows, _nmf_cols, MAX_TABLE_ROWS)
                        _resp_data = [dict(zip(_nmf_cols, r)) for r in _sampled]
                        _trunc = True
                    else:
                        _resp_data = [dict(zip(_nmf_cols, r)) for r in _nmf_rows]
                        _trunc = False
                    _nmf_rendered = answer_template if isinstance(answer_template, dict) else {}
                    yield _sse_event("result", build_success_response(
                        intent=intent, answer=_nmf_rendered, graph_type=graph_type,
                        data=_resp_data, columns=_nmf_cols, csv_file=csv_fn,
                        session_id=sid, intent_candidates=intent_candidates,
                        total_rows=_total, data_truncated=_trunc,
                    ))
                    return
            except Exception as e:
                logger.warning(f"SSE 야간최소유량 청크 쿼리 실패, 원본 함수 폴백: {e}")

        # TAG_DAILY_MISSING_SUMMARY: 청크 직접 쿼리 (fn_tag_daily_summary 대체)
        if intent == "TAG_DAILY_MISSING_SUMMARY":
            _tdm_sn = params.get("sitename", "")
            _tdm_ft = params.get("facilitytype", "")
            _tdm_from = params.get("from_ts", "")
            _tdm_to = params.get("to_ts", "")
            _tdm_di = params.get("datainfo", "")
            try:
                _tdm_rows, _tdm_cols = await asyncio.to_thread(
                    _execute_tag_daily_summary_query,
                    _tdm_from, _tdm_to, _tdm_sn, _tdm_ft, _tdm_di,
                )
                if _tdm_rows:
                    csv_fn = save_csv(_tdm_rows, _tdm_cols, intent, sid)
                    _total = len(_tdm_rows)
                    _resp_data = [dict(zip(_tdm_cols, r)) for r in _tdm_rows]
                    # placeholder 치환 — params 기반 ({sitename}, {facilitytype} 등)
                    _tdm_rendered = (
                        render_answer_template(answer_template, {**params, "total_count": str(_total)})
                        if isinstance(answer_template, dict) else {}
                    )
                    _tdm_rendered = apply_corrections_to_answer(_tdm_rendered, params)
                    yield _sse_event("result", build_success_response(
                        intent=intent, answer=_tdm_rendered, graph_type=graph_type,
                        data=_resp_data, columns=_tdm_cols, csv_file=csv_fn,
                        session_id=sid, intent_candidates=intent_candidates,
                        total_rows=_total,
                    ))
                    return
            except Exception as e:
                logger.warning(f"SSE 결측분석 청크 쿼리 실패, 원본 함수 폴백: {e}")

        # TIMESERIES 인텐트: 그룹 기반 + 청크 직접 쿼리 (JOIN 플래너 우회)
        if intent in _TIMESERIES_CHUNK_INTENTS:
            _sn = params.get("sitename", "%%")
            _ft_ts = params.get("facilitytype", "%%")
            _from = params.get("from_ts", "")
            _to = params.get("to_ts", "")

            # tagtype 결정
            if intent == "FACILITY_DIGITAL_STATUS_TIMESERIES_TABLE":
                _tagtype = "Digital Input"
            elif intent == "FACILITY_TAG_DATA_TABLE":
                _dk = params.get("datakey") or params.get("datainfo") or ""
                _tagtype = ("Digital Input" if "밸브" in _dk
                            else "Analog Output" if "설정" in _dk
                            else "Analog Input")
            else:
                _tagtype = "Analog Input"

            # datainfo 패턴 결정
            if intent == "FACILITY_FLOW_INSTANT_TIMESERIES_TABLE":
                _di_pat = "(유량.*순시|순시.*유량)"
            elif intent == "FACILITY_FLOW_ACCUMULATED_TIMESERIES_TABLE":
                _di_pat = "유량.*적산"
            else:
                _di_pat = params.get("datainfo", ".*")

            # group_code 결정: param_extractor에서 추출 → intent-specific 오버라이드
            _group = params.get("group_code")
            if intent == "FACILITY_FLOW_INSTANT_TIMESERIES_TABLE":
                _group = _group or "FLOW_INSTANT"
            elif intent == "FACILITY_FLOW_ACCUMULATED_TIMESERIES_TABLE":
                _group = _group or "FLOW_CUMULATIVE"

            logger.info(f"[SSE] TIMESERIES 쿼리 시작: intent={intent}, site={_sn}, ft={_ft_ts}, tagtype={_tagtype}, di_pat={_di_pat}, group={_group}, from={_from}, to={_to}")
            try:
                _ts_rows, _ts_cols = await asyncio.to_thread(
                    _execute_timeseries_query,
                    _sn, _ft_ts, _tagtype, _di_pat, _from, _to,
                    _group,
                )
                logger.info(f"[SSE] TIMESERIES 쿼리 완료: {len(_ts_rows)}행")
                if _ts_rows:
                    rows = _ts_rows
                    columns = _ts_cols
            except Exception as e:
                logger.error(f"[SSE] TIMESERIES 청크 쿼리 실패 ({intent}): {e}")

        # --- 진행 3: 데이터 조회 ---
        yield _sse_event("progress", {
            "step": "query",
            "message": "데이터를 조회 중입니다...",
        })
        await asyncio.sleep(0)

        # 커스텀 핸들러에서 rows/columns가 이미 채워진 경우 SQL 실행 건너뜀
        _t_sql_start = time.perf_counter()
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
                err_msg = str(e)
                logger.error(f"[SSE] SQL 실행 오류: {err_msg}")
                user_msg = ("조회 기간이 올바르지 않습니다. 시작일이 종료일보다 앞서야 합니다."
                            if "Invalid period" in err_msg
                            else "데이터 조회 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")
                yield _sse_event("error", {
                    "status": "ERROR",
                    "message": user_msg,
                    "session_id": sid,
                })
                return
        _t_sql_end = time.perf_counter()

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

        # ANOMALY_SCAN_ALL: 교차 검증 + per-row 종합 판정
        if intent == "ANOMALY_SCAN_ALL" and _CAUSAL_INDEX:
            try:
                from anomaly_detector import cross_facility_check_all, enrich_rows_with_cross_verdict
                cross_mismatches = await asyncio.to_thread(
                    cross_facility_check_all, _query_recent_values, _CAUSAL_INDEX,
                    lookback_minutes=180,
                )
                if cross_mismatches:
                    processed_data["cross_facility_mismatches"] = cross_mismatches
                    processed_data["cross_facility_mismatch_count"] = len(cross_mismatches)
                logger.info(f"[SSE] SCAN_ALL 교차 검증: {len(cross_mismatches)}건 불일치")
                # per-row cross_status/verdict 병합
                _profiles = site_profiler.profiles if site_profiler and site_profiler.profiles else None
                enrich_rows_with_cross_verdict(rows, columns, cross_mismatches, site_profiles=_profiles)
                _vd_idx = columns.index("verdict") if "verdict" in columns else None
                if _vd_idx is not None:
                    processed_data["cross_anomaly_count"] = sum(
                        1 for r in rows if r[_vd_idx] in ("교차이상", "교차주의", "복합이상")
                    )
            except Exception as e:
                logger.warning(f"[SSE] SCAN_ALL 교차 검증 실패: {e}")

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

        # STDDEV 분석: stddev_stats 추출 (개별 조회 시만)
        _stddev_stats = None
        if intent == "FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS" and response_data and len(response_data) == 1:
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
                _region = params.get("region", "R01")
                _az_conn = get_db_connection()
                try:
                    _anomaly_zones = compute_anomaly_zones(rows, columns, _region, _az_conn)
                finally:
                    _az_conn.close()
            except Exception as e:
                logger.warning(f"Anomaly zone computation failed (SSE): {e}")

        # 트렌드 비교 (평소 대비 / 향후 전망) — docs/trend-comparison-spec.md
        _comparison = None
        if graph_type == "plot" and rows and columns:
            try:
                from trend_comparison import compute_comparison
                _tagsn = (params.get("tagsn") or
                          (intent_def.get("tagsn") if intent_def else None) or
                          (rows[0][columns.index("tagsn")] if "tagsn" in columns else None))
                if _tagsn:
                    _tc_conn = get_db_connection()
                    try:
                        _comparison = compute_comparison(
                            rows=rows, columns=columns, intent=intent,
                            sitename=params.get("sitename"),
                            facilitytype=params.get("facilitytype"),
                            tagsn=_tagsn,
                            conn=_tc_conn,
                        )
                    finally:
                        _tc_conn.close()
            except Exception as e:
                logger.warning(f"Trend comparison computation failed (SSE): {e}")

        _t_end = time.perf_counter()
        logger.info(
            f"⏱ /ask/stream [{intent}|{classify_method}] "
            f"분류={(_t_classified - _t_start)*1000:.0f}ms "
            f"추출={(_t_extracted - _t_classified)*1000:.0f}ms "
            f"SQL={(_t_sql_end - _t_sql_start)*1000:.0f}ms "
            f"합계={(_t_end - _t_start)*1000:.0f}ms "
            f"rows={len(rows)}"
        )

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
        comparison=_comparison,
            intent_candidates=intent_candidates,
            site_group_distribution=processed_data.get("site_group_distribution"),
            site_group=processed_data.get("site_group"),
            pattern_analysis=processed_data.get("pattern_analysis"),
            cross_anomaly_count=processed_data.get("cross_anomaly_count"),
            cross_facility_mismatches=_filter_cross_mismatches(processed_data.get("cross_facility_mismatches"), params.get("sitename")),
            cross_facility_mismatch_count=len(_filter_cross_mismatches(processed_data.get("cross_facility_mismatches"), params.get("sitename")) or []),
            data_quality_issues=_filter_by_sitename(processed_data.get("data_quality_issues"), params.get("sitename")),
            equipment_failure_impacts=_filter_by_sitename(processed_data.get("equipment_failure_impacts"), params.get("sitename")),
            equipment_failure_count=len(_filter_by_sitename(processed_data.get("equipment_failure_impacts"), params.get("sitename")) or []),
            flow_balance_summary=_filter_flow_balance(processed_data.get("flow_balance_summary"), params.get("sitename")),
            intra_facility=processed_data.get("intra_facility"),
            equipment_diagnosis=processed_data.get("equipment_diagnosis"),
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


# 종합 현황판 API → endpoints/dashboard.py로 분리됨
# 종합 현황판 API → endpoints/dashboard.py로 분리됨


# 관리자 API (health, models, admin/*) → endpoints/admin.py로 분리됨

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


# 경보/위기관리/태그 API → endpoints/alarm_crisis.py, endpoints/tags.py로 분리됨

# 인과관계 API → endpoints/causal.py로 분리됨

# 트렌드 시계열 데이터 조회 → endpoints/trend.py로 분리됨



# 대시보드 요약 API → endpoints/dashboard.py로 분리됨

# GIS 스파크라인 트렌드 → endpoints/trend.py로 분리됨



# GIS/용수흐름/설비자동매핑 API → endpoints/flow_realtime.py로 분리됨

# =============================================================================
# 메인 실행
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    import os as _os

    _ssl_enabled = _os.getenv("HTTPS_ENABLED", "false").lower() == "true"
    _ssl_kwargs = {}
    if _ssl_enabled:
        _cert_dir = _os.path.normpath(
            _os.path.join(_os.path.dirname(__file__), "..", "web", "certs")
        )
        _ssl_kwargs = {
            "ssl_keyfile": _os.path.join(_cert_dir, "localhost-key.pem"),
            "ssl_certfile": _os.path.join(_cert_dir, "localhost.pem"),
        }
        print(f"[HTTPS] SSL enabled (certs: {_cert_dir})")

    # Windows: "::" 이중 스택이 IPv4 포함 안 됨 → 0.0.0.0으로 IPv4 바인딩
    # Linux:   0.0.0.0은 IPv4만 수신 (IPv6 필요 시 "::" 사용)
    import platform
    _host = "::" if platform.system() == "Linux" else "0.0.0.0"
    uvicorn.run(app, host=_host, port=8000, **_ssl_kwargs)
