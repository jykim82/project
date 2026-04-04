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

# .env 파일 로드 (python-dotenv 설치 시)
try:
    from dotenv import load_dotenv
    load_dotenv()
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
    GROUP_THRESHOLDS,
    classify_alert_grade,
    verify_intra_facility,
    build_intra_facility_block,
)
from anomaly_iforest import IForestManager
from site_profiler import SiteProfiler
from db_sync import DbSyncWorker  # [임시] 개발 완료 후 제거 예정
from snmp_poller import SnmpPoller
from endpoints.facility_crud import router as facility_crud_router, init as init_facility_crud
from endpoints.network_crud import router as network_crud_router, init as init_network_crud
from endpoints.canvas_crud import router as canvas_crud_router, init as init_canvas_crud
from endpoints.auth_crud import router as auth_crud_router, init as init_auth_crud

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
    """백그라운드: 서버 시작 10초 후 첫 실행, 이후 24시간마다 IForest 학습."""
    from anomaly_iforest import RETRAIN_INTERVAL_HOURS
    await asyncio.sleep(10)
    while True:
        try:
            _profiles = site_profiler.profiles if site_profiler and site_profiler.profiles else None
            logger.info("IForest 백그라운드 학습 시작...")
            await asyncio.to_thread(iforest_manager.train_all, get_db_connection, site_profiles=_profiles)
            logger.info(f"IForest 백그라운드 학습 완료: {iforest_manager.model_count}개 모델")
        except Exception as e:
            logger.error(f"IForest 백그라운드 학습 실패: {e}")
        await asyncio.sleep(RETRAIN_INTERVAL_HOURS * 3600)


async def _anomaly_scan_cache_loop():
    """백그라운드: 프로파일링 완료 대기 후 첫 실행, 이후 5분마다 ANOMALY_SCAN_ALL 전체 캐시 갱신.

    전체 파이프라인(SQL + IForest + 교차검증)을 미리 실행하여 캐시에 저장.
    사용자 요청 시 캐시 반환 (<1s).
    """
    global _ANOMALY_SCAN_CACHE, _ANOMALY_SCAN_CACHE_TIME
    # 프로파일링 완료 대기 (최대 120초, 5초 간격 체크)
    for _ in range(24):
        if site_profiler and site_profiler.profiles:
            break
        await asyncio.sleep(5)
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

        chunks = _get_chunks_for_range(cur, _from, _to)
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


def _detect_data_quality_issues(rows: list, columns: list) -> list[dict]:
    """ANOMALY_SCAN_ALL 결과에서 빠진 태그를 감지하여 데이터 품질 이상 목록 반환.

    빠진 원인 분류:
    - 센서무응답: 7일간 전체 val≈0 (DEAD sensor)
    - 데이터홀딩: 7일간 데이터 존재하나 active_cnt < 50 + 높은 flat 비율
    - 데이터없음: 7일간 데이터 자체가 없음
    """
    tagsn_idx = columns.index("tagsn") if "tagsn" in columns else None
    if tagsn_idx is None:
        return []

    result_tagsns = {r[tagsn_idx] for r in rows}

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # 1) 전체 Analog Input (NOT 적산, NOT 설정값) 태그
        cur.execute("""
            SELECT tagsn, sitename, facilitytype, datainfo
            FROM tb_tag_info
            WHERE tagtype = 'Analog Input'
              AND datainfo NOT LIKE '%%적산%%'
              AND datainfo NOT LIKE '%%설정%%'
        """)
        all_tags = {r[0]: {"tagsn": r[0], "sitename": r[1], "facilitytype": r[2], "datainfo": r[3]}
                    for r in cur.fetchall()}

        # 2) 차집합 = 결과에서 빠진 태그
        missing_tagsns = [t for t in all_tags if t not in result_tagsns]
        if not missing_tagsns:
            cur.close()
            return []

        # 3) 빠진 태그의 최근 7일 데이터 상태 (DEAD/데이터없음 판별)
        cur.execute("""
            SELECT tagsn,
                COUNT(*) AS total_5m,
                COUNT(*) FILTER (WHERE (min_val + max_val) / 2.0 > 0.001) AS nonzero_cnt,
                SUM(CASE WHEN min_val = max_val THEN 1 ELSE 0 END) AS flat_cnt,
                MAX(bucket) AS last_bucket
            FROM cagg_5min_raw_stats_ai
            WHERE tagsn = ANY(%s)
                AND bucket >= now() - interval '7 days'
            GROUP BY tagsn
        """, (missing_tagsns,))
        stats_7d = {r[0]: r for r in cur.fetchall()}

        # 4) 최근 24시간 데이터 상태 (홀딩 조기 감지)
        cur.execute("""
            SELECT tagsn,
                COUNT(*) AS total_24h,
                COUNT(*) FILTER (WHERE (min_val + max_val) / 2.0 > 0.001) AS nonzero_24h,
                SUM(CASE WHEN min_val = max_val THEN 1 ELSE 0 END) AS flat_24h
            FROM cagg_5min_raw_stats_ai
            WHERE tagsn = ANY(%s)
                AND bucket >= now() - interval '24 hours'
            GROUP BY tagsn
        """, (missing_tagsns,))
        stats_24h = {r[0]: r for r in cur.fetchall()}
        cur.close()

        issues = []
        for tagsn in missing_tagsns:
            info = all_tags[tagsn]
            s7 = stats_7d.get(tagsn)
            s24 = stats_24h.get(tagsn)

            if not s7:
                issue_type = "데이터없음"
                detail = "최근 7일간 데이터 없음"
            elif int(s7[2]) == 0:  # nonzero_cnt == 0 (7일간 전부 val≈0)
                issue_type = "센서무응답"
                detail = f"7일간 {s7[1]}건 전부 val~0"
            else:
                # 24시간 윈도우 우선 (홀딩 조기 감지)
                # s24 = (tagsn, total_24h, nonzero_24h, flat_24h) → 인덱스 주의
                flat_24h_pct = round(int(s24[3]) / int(s24[1]) * 100, 1) if s24 and int(s24[1]) > 0 else 0
                flat_7d_pct = round(int(s7[3]) / int(s7[1]) * 100, 1) if int(s7[1]) > 0 else 0
                if flat_24h_pct > 90:
                    issue_type = "데이터홀딩"
                    detail = f"24h flat {flat_24h_pct}% (7d flat {flat_7d_pct}%)"
                elif flat_7d_pct > 80:
                    issue_type = "데이터홀딩"
                    detail = f"7d flat {flat_7d_pct}%"
                else:
                    issue_type = "데이터부족"
                    detail = f"활성 {s7[2]}건 (50건 미달)"

            issues.append({
                "tagsn": tagsn,
                "sitename": info["sitename"],
                "facilitytype": info["facilitytype"],
                "datainfo": info["datainfo"],
                "issue_type": issue_type,
                "detail": detail,
            })

        return issues

    except Exception as e:
        logger.warning(f"데이터 품질 감지 실패: {e}")
        return []
    finally:
        if conn:
            try:
                conn.close()
            except Exception as e:
                logger.warning(f"데이터 품질 conn.close 실패: {e}")


# ---------------------------------------------------------------------------
# 설비 장애 역추적 (Equipment Failure Traceback)
# ---------------------------------------------------------------------------
_FAILURE_SEVERITY: dict[str, int] = {
    "equip_fault": 4,
    "power_fault": 3,
    "network_down": 2,
    "comm_error": 1,
}


def _detect_equipment_failures(
    rows: list, columns: list,
) -> tuple[list[dict], dict[str, str]]:
    """설비 장애 감지 + 영향 태그 역추적.

    3가지 신호 소스를 조합:
    A) tb_network_status: is_alive=false 설비
    B) DI 태그(COMM_ERROR/EQUIP_FAULT/POWER_FAULT): val=1, 최근 10분
    C) tb_equipment_tag_map: 역방향 매핑 (equipment → tags)

    Returns:
        (impacts_list, tag_to_failure_map)
        - impacts_list: 설비별 장애 요약 [{equipment_id, failure_type, affected_tag_count, ...}]
        - tag_to_failure_map: {tagsn: failure_type} per-row 뱃지용
    """
    tagsn_idx = columns.index("tagsn") if "tagsn" in columns else None
    sn_idx = columns.index("sitename") if "sitename" in columns else None
    ft_idx = columns.index("facilitytype") if "facilitytype" in columns else None
    di_idx = columns.index("datainfo") if "datainfo" in columns else None
    if tagsn_idx is None:
        return [], {}

    result_tagsns = {r[tagsn_idx] for r in rows}
    # tagsn → datainfo 조회용
    tag_datainfo: dict[str, str] = {}
    if di_idx is not None:
        tag_datainfo = {r[tagsn_idx]: str(r[di_idx]) for r in rows}

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # --- A. 네트워크 장애 설비 ---
        failed_equips: dict[str, dict] = {}
        try:
            cur.execute("""
                WITH lt AS (SELECT MAX(check_time) AS ct FROM tb_network_status)
                SELECT ns.equipment_id, e.equipmenttype, e.sitename, e.facilitytype,
                       ns.error_message
                FROM tb_network_status ns
                JOIN lt ON ns.check_time = lt.ct
                JOIN tb_equipment_info e ON ns.equipment_id = e.equipment_id
                WHERE ns.is_alive = false
            """)
            for eid, etype, sn, ft, emsg in cur.fetchall():
                failed_equips[eid] = {
                    "equipmenttype": etype or "",
                    "sitename": sn or "",
                    "facilitytype": ft or "",
                    "failure_type": "network_down",
                    "failure_detail": emsg or "네트워크 응답 없음",
                }
        except Exception as e:
            logger.debug(f"네트워크 장애 조회 실패(무시): {e}")

        # --- B. DI 장애 태그 (COMM_ERROR / EQUIP_FAULT / POWER_FAULT) ---
        site_faults: dict[tuple[str, str], str] = {}  # (sitename, facilitytype) → worst failure_type
        try:
            # B-1: 대상 DI 태그 조회
            cur.execute("""
                SELECT gm.tagsn, dg.group_code, ti.sitename, ti.facilitytype
                FROM tb_tag_group_map gm
                JOIN tb_tag_data_group dg ON gm.group_id = dg.group_id
                JOIN tb_tag_info ti ON gm.tagsn = ti.tagsn
                WHERE dg.group_code IN ('COMM_ERROR', 'EQUIP_FAULT', 'POWER_FAULT')
            """)
            di_tags = cur.fetchall()

            if di_tags:
                di_tagsn_list = [r[0] for r in di_tags]
                di_meta = {r[0]: (r[1], r[2], r[3]) for r in di_tags}  # tagsn → (group_code, sitename, facilitytype)

                # B-2: 최근 10분 val=1 (장애 활성)
                cur.execute("""
                    SELECT DISTINCT tagsn
                    FROM tb_tag_raw_data
                    WHERE tagsn = ANY(%s)
                      AND logtime >= now() - interval '10 minutes'
                      AND val = 1
                """, (di_tagsn_list,))
                active_faults = {r[0] for r in cur.fetchall()}

                _gc_to_ft = {
                    "COMM_ERROR": "comm_error",
                    "EQUIP_FAULT": "equip_fault",
                    "POWER_FAULT": "power_fault",
                }
                for tsn in active_faults:
                    gc, sn, ft = di_meta[tsn]
                    ftype = _gc_to_ft.get(gc, "comm_error")
                    key = (sn, ft)
                    existing = site_faults.get(key)
                    if not existing or _FAILURE_SEVERITY.get(ftype, 0) > _FAILURE_SEVERITY.get(existing, 0):
                        site_faults[key] = ftype
        except Exception as e:
            logger.debug(f"DI 장애 태그 조회 실패(무시): {e}")

        # --- C. 설비↔태그 역방향 맵 ---
        equip_to_tags: dict[str, set[str]] = {}
        equip_info_map: dict[str, dict] = {}
        try:
            cur.execute("""
                SELECT etm.equipment_id, etm.tagsn, e.equipmenttype, e.sitename, e.facilitytype
                FROM tb_equipment_tag_map etm
                JOIN tb_equipment_info e ON etm.equipment_id = e.equipment_id
            """)
            for eid, tsn, etype, sn, ft in cur.fetchall():
                equip_to_tags.setdefault(eid, set()).add(tsn)
                if eid not in equip_info_map:
                    equip_info_map[eid] = {
                        "equipmenttype": etype or "",
                        "sitename": sn or "",
                        "facilitytype": ft or "",
                    }
        except Exception as e:
            logger.debug(f"설비 태그 맵 조회 실패(무시): {e}")

        cur.close()

        # --- 조인: 장애 설비 → 영향 태그 → 스캔 결과 교차 ---
        impacts: list[dict] = []
        tag_failure_map: dict[str, str] = {}

        # A 소스: 네트워크 장애 설비
        for eid, info in failed_equips.items():
            affected = equip_to_tags.get(eid, set())
            in_scan = affected & result_tagsns
            impacts.append({
                "equipment_id": eid,
                "equipmenttype": info["equipmenttype"],
                "sitename": info["sitename"],
                "facilitytype": info["facilitytype"],
                "failure_type": info["failure_type"],
                "failure_detail": info["failure_detail"],
                "affected_tag_count": len(affected),
                "anomalous_tag_count": len(in_scan),
                "affected_tags": [
                    {"tagsn": t, "datainfo": tag_datainfo.get(t, "")}
                    for t in sorted(in_scan)[:5]
                ],
            })
            for t in affected:
                _apply_worst_failure(tag_failure_map, t, info["failure_type"])

        # B 소스: DI 장애 → 사이트+시설 레벨 전체 태그
        for (sn, ft), ftype in site_faults.items():
            # 해당 사이트+시설의 모든 태그 찾기
            site_tags: set[str] = set()
            if sn_idx is not None and ft_idx is not None:
                for r in rows:
                    if r[sn_idx] == sn and r[ft_idx] == ft:
                        site_tags.add(r[tagsn_idx])
            if not site_tags:
                continue

            impacts.append({
                "equipment_id": f"{sn}_{ft}",
                "equipmenttype": "DI장애",
                "sitename": sn,
                "facilitytype": ft,
                "failure_type": ftype,
                "failure_detail": {
                    "comm_error": "통신이상 DI 활성",
                    "equip_fault": "설비고장 DI 활성",
                    "power_fault": "전원이상 DI 활성",
                }.get(ftype, "DI 활성"),
                "affected_tag_count": len(site_tags),
                "anomalous_tag_count": len(site_tags),
                "affected_tags": [
                    {"tagsn": t, "datainfo": tag_datainfo.get(t, "")}
                    for t in sorted(site_tags)[:5]
                ],
            })
            for t in site_tags:
                _apply_worst_failure(tag_failure_map, t, ftype)

        # 영향 태그 수 내림차순 정렬
        impacts.sort(key=lambda x: x["affected_tag_count"], reverse=True)

        return impacts, tag_failure_map

    except Exception as e:
        logger.warning(f"설비 장애 감지 실패: {e}")
        return [], {}
    finally:
        if conn:
            try:
                conn.close()
            except Exception as e:
                logger.warning(f"설비 장애 역추적 conn.close 실패: {e}")


def _apply_worst_failure(
    tag_map: dict[str, str], tagsn: str, failure_type: str,
) -> None:
    """tag_failure_map에 worst severity 기준으로 장애 유형 적용."""
    existing = tag_map.get(tagsn)
    if not existing or _FAILURE_SEVERITY.get(failure_type, 0) > _FAILURE_SEVERITY.get(existing, 0):
        tag_map[tagsn] = failure_type


def _compute_anomaly_scan_all() -> Optional[dict]:
    """ANOMALY_SCAN_ALL 전체 파이프라인을 동기 실행하여 캐시용 결과 반환.

    SQL 실행 → process_sql_result(IForest+grade/group 포함) → 교차 검증 → 최종 결과.
    캐시된 결과는 핸들러에서 answer_template 렌더링에 직접 사용된다.
    """
    from anomaly_detector import cross_facility_check_all, enrich_rows_with_cross_verdict

    # intent_def 찾기
    intent_def = None
    for idef in INTENT_DEFINITIONS:
        if idef.get("intent") == "ANOMALY_SCAN_ALL":
            intent_def = idef
            break
    if not intent_def:
        return None

    sql_raw = intent_def.get("sql", "")
    if not sql_raw:
        return None
    sql_combined = "\n".join(sql_raw) if isinstance(sql_raw, list) else sql_raw

    # 1단계: SQL 실행 (365일 stats + 3h latest + comm_error)
    # 플레이스홀더 치환 (캐시는 전체 조회이므로 필터 없음)
    cache_params = {"anomaly_facility_filter": "", "anomaly_scope": "", "alarm_filter_clause": ""}
    try:
        rows, columns = execute_sql(sql_combined, cache_params)
    except Exception as e:
        logger.error(f"SCAN_ALL 캐시 SQL 실패: {e}")
        return None

    if not rows:
        return None

    rows = [list(r) for r in rows]

    # 2단계: process_sql_result (IForest + per-row grade/group 포함)
    try:
        processed_data = process_sql_result(rows, columns, intent_def, {})
    except Exception as e:
        logger.error(f"SCAN_ALL 캐시 후처리 실패: {e}")
        return None

    # 3단계: 교차 검증
    if _CAUSAL_INDEX:
        try:
            cross_mismatches = cross_facility_check_all(
                _query_recent_values, _CAUSAL_INDEX, lookback_minutes=180,
            )
            if cross_mismatches:
                processed_data["cross_facility_mismatches"] = cross_mismatches
                processed_data["cross_facility_mismatch_count"] = len(cross_mismatches)
            logger.info(f"SCAN_ALL 캐시 교차검증: {len(cross_mismatches)}건")
        except Exception as e:
            logger.warning(f"SCAN_ALL 캐시 교차검증 실패: {e}")

    # 4단계: 교차검증 결과를 per-row cross_status/verdict로 병합
    _profiles = site_profiler.profiles if site_profiler and site_profiler.profiles else None
    _cross_list = processed_data.get("cross_facility_mismatches")
    enrich_rows_with_cross_verdict(rows, columns, _cross_list, site_profiles=_profiles)

    # verdict 기반 교차이상 카운트
    _vd_idx = columns.index("verdict") if "verdict" in columns else None
    if _vd_idx is not None:
        processed_data["cross_anomaly_count"] = sum(
            1 for r in rows if r[_vd_idx] in ("교차이상", "교차주의", "복합이상")
        )

    # 5단계: 데이터 품질 이상 감지 (DEAD/홀딩 — 결과에서 빠진 태그)
    dq_issues = _detect_data_quality_issues(rows, columns)
    if dq_issues:
        processed_data["data_quality_issues"] = dq_issues
        logger.info(f"SCAN_ALL 데이터 품질 이상: {len(dq_issues)}건")

    # 6단계: 설비 장애 역추적 (network_down + DI fault → 영향 태그)
    try:
        equip_impacts, tag_failure_map = _detect_equipment_failures(rows, columns)
        if equip_impacts:
            processed_data["equipment_failure_impacts"] = equip_impacts
            processed_data["equipment_failure_count"] = len(equip_impacts)
            logger.info(f"SCAN_ALL 설비 장애: {len(equip_impacts)}건")

        # per-row equip_failure 컬럼 추가 (rows가 tuple일 수 있으므로 재구성)
        columns.append("equip_failure")
        tsn_idx = columns.index("tagsn")
        rows[:] = [
            tuple(list(r) + [tag_failure_map.get(r[tsn_idx], "")])
            for r in rows
        ]
    except Exception as e:
        logger.warning(f"SCAN_ALL 설비 장애 감지 실패: {e}")

    # 7단계: 물 수지 요약 (캐시 참조)
    try:
        if _FLOW_BALANCE_CACHE:
            imbalance_edges = [e for e in _FLOW_BALANCE_CACHE if e["grade"] != "정상" and e["status"] == "ok"]
            processed_data["flow_balance_summary"] = {
                "total_edges": len(_FLOW_BALANCE_CACHE),
                "imbalance_count": len(imbalance_edges),
                "worst_edges": sorted(imbalance_edges, key=lambda e: -abs(e["imbalance_pct"]))[:5],
            }
            if imbalance_edges:
                logger.info(f"SCAN_ALL 물 수지: {len(imbalance_edges)}/{len(_FLOW_BALANCE_CACHE)} 불균형")
    except Exception as e:
        logger.warning(f"SCAN_ALL 물 수지 요약 실패: {e}")

    return {
        "rows": rows,
        "columns": list(columns),
        "processed_data": processed_data,
        "answer_template": intent_def.get("answer_template", {}),
    }


async def _session_cleanup_loop():
    """백그라운드: 60초마다 만료 세션 정리 + CSV 파일 정리"""
    while True:
        await asyncio.sleep(60)
        session_manager.cleanup_expired()
        cleanup_old_csv_files()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작/종료 시 실행되는 lifespan 이벤트"""
    global intent_classifier, param_extractor_instance, query_validator, _cleanup_task, _profiling_task, _sync_task, site_profiler, _sync_worker

    # DB 커넥션 풀 초기화 (get_db_connection보다 먼저)
    _init_db_pool()

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

    # Ollama 연결 상태 로그 + embed_query 백오프 초기화
    if ollama_client.health_check():
        logger.info(f"Ollama 연결 성공: {get_model()}")
        if embedding_index.ready:
            logger.info(f"벡터 검색 활성화: {embedding_index.size}벡터")
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
    for task in (_cleanup_task, _profiling_task, _snmp_polling_task, _iforest_task, _anomaly_scan_task, _flow_baseline_task, _sync_task):
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
    """커넥션 풀 초기화 — get_db_connection() 첫 호출 전에 실행되어야 한다."""
    global _db_pool
    _db_pool = psycopg2.pool.ThreadedConnectionPool(
        minconn=DB_POOL_MIN,
        maxconn=DB_POOL_MAX,
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )
    logger.info(f"DB 커넥션 풀 초기화 완료 (min={DB_POOL_MIN}, max={DB_POOL_MAX})")


# =============================================================================
# AI 런타임 설정 (UI에서 변경 가능)
# =============================================================================
class _AiRuntimeSettings:
    """서버 재시작 없이 변경 가능한 AI 파라미터"""
    def __init__(self):
        self.num_ctx: int = 4096
        self.temperature: float = 0.0
        self.timeout: int = 30

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

# 네트워크 CRUD 엔드포인트 모듈 초기화
init_network_crud(get_db_connection, snmp_poller=snmp_poller_instance)
app.include_router(network_crud_router)

# 캔버스 레이아웃 엔드포인트 모듈 초기화
init_canvas_crud(get_db_connection)
app.include_router(canvas_crud_router)

# 인증 엔드포인트 모듈 초기화
init_auth_crud(get_db_connection)
app.include_router(auth_crud_router)


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


def _sql_escape_literal(value: str) -> str:
    """SQL 문자열 리터럴 이스케이프 — 싱글쿼트 이중화 + 백슬래시 제거."""
    return value.replace("\\", "").replace("'", "''")


def build_anomaly_facility_filter(intent_name: str, params: dict) -> str:
    """ANOMALY 인텐트에 대해 선택적 sitename/facilitytype/group_code WHERE 절 생성."""
    alias = _ANOMALY_FILTER_INTENTS.get(intent_name)
    if not alias:
        return ""
    parts = []
    site = params.get("sitename", "")
    ftype = params.get("facilitytype", "")
    if site and site not in ("전체", "%%", ""):
        site_esc = _sql_escape_literal(site)
        parts.append(f"AND {alias}.sitename = '{site_esc}'")
    if ftype and ftype not in ("전체", "%%", ""):
        ftype_esc = _sql_escape_literal(ftype)
        parts.append(f"AND {alias}.facilitytype = '{ftype_esc}'")
    # group_code 필터 (센서 유형별 점검: 유량/압력/수질 등)
    gc = params.get("group_code", "")
    if gc and alias == "ti":
        resolved = _resolve_group_codes(gc)
        gc_sql = ", ".join(f"'{_sql_escape_literal(c)}'" for c in resolved)
        parts.append(
            f"AND {alias}.tagsn IN ("
            f"SELECT gm.tagsn FROM tb_tag_group_map gm "
            f"JOIN tb_tag_data_group dg ON gm.group_id = dg.group_id "
            f"WHERE dg.group_code IN ({gc_sql}))"
        )
    return "\n    ".join(parts)


def _filter_by_sitename(items: list | None, sitename: str | None) -> list | None:
    """리스트 내 dict의 sitename 필드로 필터링. sitename 미지정/전체이면 원본 반환.
    정확 매칭 우선, 없으면 빈 리스트 반환."""
    if not items or not sitename or sitename in ("전체", "%%", ""):
        return items
    return [
        item for item in items
        if isinstance(item, dict) and item.get("sitename", "") == sitename
    ] or None


def _filter_flow_balance(summary: dict | None, sitename: str | None) -> dict | None:
    """물 수지 요약에서 sitename이 정확히 일치하는 엣지만 필터링."""
    if not summary or not sitename or sitename in ("전체", "%%", ""):
        return summary
    worst = summary.get("worst_edges", [])
    filtered = [
        e for e in worst
        if e.get("upstream_sitename", "") == sitename
        or e.get("downstream_sitename", "") == sitename
    ]
    if not filtered and not worst:
        return None
    total = summary.get("total_edges", 0)
    return {
        "total_edges": total,
        "imbalance_count": len(filtered),
        "worst_edges": filtered,
    }


def _filter_cross_mismatches(mismatches: list | None, sitename: str | None) -> list | None:
    """교차검증 불일치 목록에서 sitename이 정확히 일치하는 항목만 필터링."""
    if not mismatches or not sitename or sitename in ("전체", "%%", ""):
        return mismatches
    return [
        m for m in mismatches
        if isinstance(m, dict) and (
            m.get("sitename", "") == sitename
            or m.get("upstream_sitename", "") == sitename
            or m.get("downstream_sitename", "") == sitename
        )
    ] or None


def _filter_anomaly_cache_rows(
    rows: list, columns: list, params: dict,
) -> list:
    """캐시된 ANOMALY_SCAN_ALL 결과에서 facilitytype/group_code 필터를 적용."""
    ftype = params.get("facilitytype", "")
    site = params.get("sitename", "")
    gc = params.get("group_code", "")

    # 필터가 없으면 전체 반환
    if not ftype and (not site or site in ("전체", "%%", "")) and not gc:
        return rows

    ft_idx = columns.index("facilitytype") if "facilitytype" in columns else -1
    sn_idx = columns.index("sitename") if "sitename" in columns else -1
    di_idx = columns.index("datainfo") if "datainfo" in columns else -1
    tsn_idx = columns.index("tagsn") if "tagsn" in columns else -1

    # group_code 필터를 위한 tagsn set 미리 계산
    gc_tagsns: set | None = None
    if gc:
        resolved = _resolve_group_codes(gc)
        gc_tagsns = set()
        for key, tag_map in _CAUSAL_INDEX.items():
            if len(key) == 2:
                tm = tag_map.get("tag_map", {})
                for code in resolved:
                    gc_tagsns.update(tm.get(code, []))
        # _CAUSAL_INDEX에 없는 태그를 위해 group_map 직접 조회
        if not gc_tagsns:
            try:
                conn = get_db_connection()
                cur = conn.cursor()
                gc_sql = ", ".join(f"'{_sql_escape_literal(c)}'" for c in resolved)
                cur.execute(f"""
                    SELECT gm.tagsn FROM tb_tag_group_map gm
                    JOIN tb_tag_data_group dg ON gm.group_id = dg.group_id
                    WHERE dg.group_code IN ({gc_sql})
                """)
                gc_tagsns = {r[0] for r in cur.fetchall()}
                cur.close()
                conn.close()
            except Exception as e:
                logger.warning(f"group_code 필터 DB 조회 실패, 필터 생략: {e}")
                gc_tagsns = None  # 실패 시 필터 없이

    filtered = []
    for row in rows:
        if ftype and ftype not in ("전체", "%%", "") and ft_idx >= 0:
            if row[ft_idx] != ftype:
                continue
        if site and site not in ("전체", "%%", "") and sn_idx >= 0:
            if row[sn_idx] != site:
                continue
        if gc_tagsns is not None and tsn_idx >= 0:
            if row[tsn_idx] not in gc_tagsns:
                continue
        filtered.append(row)
    return filtered


# group_code → 한글 레이블 매핑
_GROUP_CODE_LABELS = {
    "FLOW": "유량", "FLOW_INSTANT": "유량순시", "FLOW_CUMULATIVE": "유량적산",
    "FLOW_INLET": "유입유량", "FLOW_OUTLET": "유출유량",
    "PRESSURE": "압력", "PRESSURE_INLET": "유입압력",
    "PRESSURE_OUTLET": "유출압력", "PRESSURE_DISCHARGE": "토출압력",
    "WATER_LEVEL": "수위", "WATER_QUALITY": "수질",
    "WATER_QUALITY_PH": "pH", "WATER_QUALITY_TURB": "탁도",
    "WATER_QUALITY_CL": "잔류염소",
}


def build_anomaly_scope_label(params: dict) -> str:
    """ANOMALY 인텐트 답변 summary에 쓸 범위 표시 문자열 생성."""
    site = params.get("sitename", "")
    ftype = params.get("facilitytype", "")
    gc = params.get("group_code", "")
    parts = []
    if site and site not in ("전체", "%%", ""):
        parts.append(site)
    if ftype:
        parts.append(ftype)
    if gc:
        parts.append(_GROUP_CODE_LABELS.get(gc, gc))
    if parts:
        return " ".join(parts)
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
            except Exception as e:
                logger.debug(f"이상구간 시간 파싱 실패, 비연속 처리: {e}")
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
    if kwargs.get("stddev_stats_list"):
        response["stddev_stats_list"] = kwargs["stddev_stats_list"]
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
    if kwargs.get("cross_facility_mismatches"):
        response["cross_facility_mismatches"] = kwargs["cross_facility_mismatches"]
    if kwargs.get("cross_facility_mismatch_count"):
        response["cross_facility_mismatch_count"] = kwargs["cross_facility_mismatch_count"]
    if kwargs.get("cross_anomaly_count") is not None:
        response["cross_anomaly_count"] = kwargs["cross_anomaly_count"]
    if kwargs.get("data_quality_issues"):
        response["data_quality_issues"] = kwargs["data_quality_issues"]
    if kwargs.get("equipment_failure_impacts"):
        response["equipment_failure_impacts"] = kwargs["equipment_failure_impacts"]
    if kwargs.get("equipment_failure_count") is not None:
        response["equipment_failure_count"] = kwargs["equipment_failure_count"]
    if kwargs.get("flow_balance_summary"):
        response["flow_balance_summary"] = kwargs["flow_balance_summary"]
    if kwargs.get("intra_facility"):
        response["intra_facility"] = kwargs["intra_facility"]
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


def _diagnose_no_data(intent: str, params: dict) -> str | None:
    """데이터 없음의 원인을 동적으로 진단하여 구체적 메시지 반환."""
    sn = params.get("sitename", "")
    ft = params.get("facilitytype", "")
    di = params.get("datainfo", "")

    if not sn or sn in ("%%", "전체"):
        return None

    # 트렌드/시계열 인텐트만 진단
    _TREND_INTENTS = {
        "FACILITY_TREND", "FACILITY_MIXED_TREND",
        "FACILITY_ANALOG_TIMESERIES_TABLE", "FACILITY_TAG_DATA_TABLE",
        "FACILITY_FLOW_INSTANT_TIMESERIES_TABLE", "FACILITY_FLOW_ACCUMULATED_TIMESERIES_TABLE",
        "NIGHT_MIN_FLOW_STATUS", "NIGHT_MIN_FLOW_SUMMARY_TABLE",
    }
    if intent not in _TREND_INTENTS:
        return None

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            # 1) 시설 존재 여부
            cur.execute(
                "SELECT COUNT(*) FROM tb_tag_info WHERE sitename = %s AND facilitytype = %s",
                (sn, ft),
            )
            tag_count = cur.fetchone()[0]
            if tag_count == 0:
                # 시설 자체가 없는지 확인
                cur.execute("SELECT COUNT(*) FROM tb_tag_info WHERE sitename = %s", (sn,))
                site_exists = cur.fetchone()[0] > 0
                if not site_exists:
                    return f"'{sn}' 현장이 등록되어 있지 않습니다. 현장명을 확인해 주세요."
                return f"{sn} {ft}에 등록된 태그가 없습니다."

            # 2) 해당 데이터항목(datainfo) 태그 존재 여부
            if di and di not in ("%%", ".*"):
                cur.execute(
                    "SELECT COUNT(*) FROM tb_tag_info WHERE sitename = %s AND facilitytype = %s"
                    " AND datainfo ~ %s AND tagtype = 'Analog Input'",
                    (sn, ft, di),
                )
                di_count = cur.fetchone()[0]
                if di_count == 0:
                    # 어떤 데이터항목이 있는지 안내
                    cur.execute(
                        "SELECT DISTINCT datainfo FROM tb_tag_info"
                        " WHERE sitename = %s AND facilitytype = %s"
                        " AND tagtype = 'Analog Input' AND datainfo NOT LIKE '%%알람%%'"
                        " AND datainfo NOT LIKE '%%SET%%' AND datainfo NOT LIKE '%%상태%%'"
                        " ORDER BY datainfo LIMIT 5",
                        (sn, ft),
                    )
                    available = [r[0] for r in cur.fetchall()]
                    avail_str = ", ".join(available) if available else "없음"
                    return f"{sn} {ft}에는 '{di}' 관련 아날로그 태그가 없습니다. 조회 가능 항목: {avail_str}"

            # 3) 조회 기간 내 데이터 존재 여부
            from_ts = params.get("from_ts", "")
            to_ts = params.get("to_ts", "")
            if from_ts and to_ts:
                cur.execute(
                    "SELECT EXISTS(SELECT 1 FROM tb_tag_raw_data r"
                    " JOIN tb_tag_info t ON r.tagsn = t.tagsn"
                    " WHERE t.sitename = %s AND t.facilitytype = %s"
                    " AND r.logtime >= %s::timestamptz AND r.logtime < %s::timestamptz"
                    " LIMIT 1)",
                    (sn, ft, from_ts, to_ts),
                )
                has_data = cur.fetchone()[0]
                if not has_data:
                    return f"{sn} {ft}의 조회 기간({from_ts} ~ {to_ts}) 내 데이터가 없습니다."

            # 4) 데이터는 있지만 트렌드 함수가 결과를 못 찾은 경우
            #    (카탈로그 미등록, 태그 매핑 누락 등)
            cur.execute(
                "SELECT DISTINCT datainfo FROM tb_tag_info"
                " WHERE sitename = %s AND facilitytype = %s"
                " AND tagtype = 'Analog Input' AND datainfo NOT LIKE '%%알람%%'"
                " AND datainfo NOT LIKE '%%SET%%' AND datainfo NOT LIKE '%%상태%%'"
                " ORDER BY datainfo LIMIT 8",
                (sn, ft),
            )
            available = [r[0] for r in cur.fetchall()]
            if available:
                avail_str = ", ".join(available)
                return f"{sn} {ft}에 '{di}' 트렌드 카탈로그가 등록되지 않았습니다. 조회 가능 항목: {avail_str}"

            cur.close()
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"no-data 진단 실패 (무시): {e}")

    return None


def build_no_data_response(
    intent: str,
    answer_template: dict,
    params: dict = None,
    session_id: Optional[str] = None,
) -> dict:
    """
    조회 결과 없음 응답을 생성한다.
    1) 동적 진단 → 2) 정적 힌트 → 3) 기본 메시지
    """
    _params = params or {}
    ft = _params.get("facilitytype", "")

    # 동적 진단 시도
    hint = _diagnose_no_data(intent, _params)
    # 정적 힌트 폴백
    if not hint:
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
    헌팅 점검 결과를 시맨틱 마커 리스트로 조립한다.
    듀얼 알고리즘: A(3h 방향전환) + B(5m 분산 뷰) 비교.
    """
    items = []
    for row in rows:
        rd = dict(zip(columns, row))
        site = rd.get("sitename", "")
        reversals = rd.get("reversal_count", 0)
        level_range = rd.get("level_range_3h", 0)
        status = rd.get("hunting_status", "STABLE")

        # 알고리즘 A 마커
        if status == "CONFIRMED_HUNTING":
            marker_a = "<<error:헌팅 확인>>"
        elif status == "SUSPECTED":
            marker_a = "<<warn:헌팅 의심>>"
        else:
            marker_a = "<<ok:정상>>"

        # 알고리즘 B (5분 분산)
        diff_pct = rd.get("diff_percent_5m", 0)
        var_status = rd.get("variance_status_5m", "-")
        max_5m = rd.get("max_val_5m", 0)
        min_5m = rd.get("min_val_5m", 0)

        if var_status == "이상":
            marker_b = f"<<warn:이상>> (변동률 {diff_pct}%)"
        elif var_status == "정상":
            marker_b = f"<<ok:정상>> (변동률 {diff_pct}%)"
        else:
            marker_b = "데이터 없음"

        items.append({
            "prefix": "###",
            "text": f"{site}",
        })
        items.append({
            "prefix": "-",
            "text": f"**[A] 방향전환 분석 (3시간)**: {marker_a} — 반전 {reversals}회, 진동폭 {level_range}m",
        })
        items.append({
            "prefix": "-",
            "text": f"**[B] 5분 분산 분석**: {marker_b} — 최대 {max_5m}m, 최소 {min_5m}m",
        })
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


# ── 청크 직접 쿼리 공용 유틸 ──────────────────────────────────
# TimescaleDB ChunkAppend가 (tagsn, logtime) 인덱스를 사용하지 못하는 문제를
# 우회하기 위해 각 청크를 직접 쿼리하여 Bitmap Index Scan을 강제한다.


def _get_chunks_for_range(cur, from_ts: str, to_ts: str) -> list[str]:
    """시간 범위에 해당하는 tb_tag_raw_data 청크 목록 반환 (schema.table)."""
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
    return [r[0] for r in cur.fetchall()]


def _query_chunks_agg(
    cur,
    chunks: list[str],
    tagsn_list: list[str],
    from_ts: str,
    to_ts: str,
    bucket_interval: str = "1 day",
) -> dict[tuple[str, object], list[tuple[float, int, float, float]]]:
    """청크별 time_bucket 집계 → cross-chunk 재집계용 dict 반환.

    key: (tagsn, bucket_timestamp)
    value: list of (sum_val, count, max_val, min_val) per chunk
    """
    from collections import defaultdict
    agg: dict[tuple[str, object], list] = defaultdict(list)
    for chunk_name in chunks:
        cur.execute(f"""
            SELECT tagsn,
                time_bucket('{bucket_interval}', logtime) AS bucket,
                SUM(val) AS sum_val,
                COUNT(*) AS cnt,
                MAX(val) AS max_val,
                MIN(val) AS min_val
            FROM {chunk_name}
            WHERE tagsn = ANY(%s)
              AND logtime >= %s::timestamptz AND logtime < %s::timestamptz
            GROUP BY tagsn, time_bucket('{bucket_interval}', logtime)
        """, (tagsn_list, from_ts, to_ts))
        for tagsn, bucket, sum_val, cnt, max_val, min_val in cur.fetchall():
            agg[(tagsn, bucket)].append(
                (float(sum_val), int(cnt), float(max_val), float(min_val)))
    return agg


def _reaggregate(
    agg: dict[tuple[str, object], list[tuple[float, int, float, float]]],
) -> dict[tuple[str, object], tuple[float, float, float, int]]:
    """cross-chunk 재집계 → (avg, max, min, total_count) dict."""
    result = {}
    for key, vals in agg.items():
        total_sum = sum(v[0] for v in vals)
        total_cnt = sum(v[1] for v in vals)
        avg_val = round(total_sum / total_cnt, 2) if total_cnt > 0 else 0
        max_val = round(max(v[2] for v in vals), 2)
        min_val = round(min(v[3] for v in vals), 2)
        result[key] = (avg_val, max_val, min_val, total_cnt)
    return result


def _query_chunks_raw(
    cur,
    chunks: list[str],
    tagsn_list: list[str],
    from_ts: str,
    to_ts: str,
    hour_filter: tuple[int, int] | None = None,
) -> list[tuple]:
    """청크별 raw 행(tagsn, logtime, val) 반환.

    hour_filter: (start_hour, end_hour) — 시간대 필터 (야간 등)
    """
    all_rows: list[tuple] = []
    hour_clause = ""
    if hour_filter:
        hour_clause = (
            f" AND EXTRACT(HOUR FROM logtime) >= {hour_filter[0]}"
            f" AND EXTRACT(HOUR FROM logtime) < {hour_filter[1]}"
        )
    for chunk_name in chunks:
        cur.execute(f"""
            SELECT tagsn, logtime, val
            FROM {chunk_name}
            WHERE tagsn = ANY(%s)
              AND logtime >= %s::timestamptz AND logtime < %s::timestamptz
              {hour_clause}
            ORDER BY tagsn, logtime
        """, (tagsn_list, from_ts, to_ts))
        all_rows.extend(cur.fetchall())
    return all_rows


def _query_recent_values(tagsn_list: list[str], minutes: int = 180) -> dict[str, list[float]]:
    """교차 검증용 최근 raw 값 조회 — 공용 헬퍼.

    cross_facility_check_all/single에서 query_func으로 사용.
    Returns: {tagsn: [val1, val2, ...]}
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        _to = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _from = (datetime.now() - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
        chunks = _get_chunks_for_range(cur, _from, _to)
        result: dict[str, list[float]] = {}
        if chunks:
            raw = _query_chunks_raw(cur, chunks, tagsn_list, _from, _to)
            for tsn, _, val in raw:
                result.setdefault(tsn, []).append(float(val) if val else 0.0)
        return result
    finally:
        cur.close()
        conn.close()


def _query_flow_timeseries(
    tagsn_list: list[str], from_ts: str, to_ts: str,
) -> dict[str, list[tuple]]:
    """물 수지용 시계열 조회 — (tagsn, logtime, val) raw 데이터.

    Returns: {tagsn: [(logtime, val), ...]} sorted by logtime
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        chunks = _get_chunks_for_range(cur, from_ts, to_ts)
        result: dict[str, list[tuple]] = {}
        if chunks:
            raw = _query_chunks_raw(cur, chunks, tagsn_list, from_ts, to_ts)
            for tsn, logtime, val in raw:
                result.setdefault(tsn, []).append(
                    (logtime, float(val) if val else 0.0))
        # 시간순 정렬
        for tsn in result:
            result[tsn].sort(key=lambda x: x[0])
        return result
    finally:
        cur.close()
        conn.close()


def _get_tag_datainfo_cache() -> dict[str, str]:
    """모든 태그의 tagsn → datainfo 매핑 캐시."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT tagsn, datainfo FROM tb_tag_info WHERE datainfo IS NOT NULL")
        return {r[0]: r[1] for r in cur.fetchall()}
    finally:
        cur.close()
        conn.close()


# ── 야간최소유량 청크 직접 쿼리 ──────────────────────────────
# fn_night_min_flow_summary PostgreSQL 함수의 60분 이동평균 윈도우가
# 43만행 대상 22초 소요 → Python 측 청크 직접 쿼리 + numpy 이동평균으로 대체.

def _execute_night_min_flow_query(
    sitename: str, facilitytype: str,
    from_ts: str, to_ts: str,
) -> tuple[list, list]:
    """tb_night_min_flow_daily 테이블에서 사전 집계된 야간최소유량 조회.

    DB 스케줄(매일 07시)로 사전 계산된 데이터를 단순 SELECT.
    기존: 청크 직접 쿼리 + Python 60분 이동평균 → 27초
    개선: 인덱스 스캔 → <0.5초
    """
    columns = ["log_time", "sitename", "facilitytype", "label",
               "tagsn", "datainfo", "datadesc", "unit", "val"]

    conn = get_db_connection()
    try:
        cur = conn.cursor()

        conditions = ["log_date >= %s::date", "log_date <= %s::date"]
        params_q: list = [from_ts, to_ts]

        if sitename and sitename not in ("전체", "%%", ""):
            conditions.append("sitename = %s")
            params_q.append(sitename)
        if facilitytype and facilitytype not in ("전체", "%%", ""):
            conditions.append("facilitytype = %s")
            params_q.append(facilitytype)

        where_sql = " AND ".join(conditions)
        cur.execute(f"""
            SELECT log_date::text, sitename, facilitytype, label,
                   tagsn, datainfo, datadesc, unit, min_val
            FROM tb_night_min_flow_daily
            WHERE {where_sql}
            ORDER BY log_date, label
        """, params_q)
        result_rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()

    return result_rows, columns


# ── 야간최소유량 표준편차분석 청크 직접 쿼리 ──────────────────
# 원본: fn_night_min_flow_stats(내부 3회) + fn_night_min_flow_summary(2회)
# = 총 5회 함수 호출 → 53초.  여기서 1회 청크 쿼리 + Python 통계로 대체.

def _execute_night_min_flow_stddev_query(
    sitename: str, facilitytype: str,
) -> tuple[list, list, list]:
    """fn_night_min_flow_stats + fn_night_min_flow_summary 대체.
    Returns (rows, columns, stddev_stats_list).

    400일 범위 1회 조회 → Python 통계 계산.
    """
    import numpy as np
    import calendar
    from collections import defaultdict
    from datetime import date

    out_cols = ["sitename", "facilitytype", "stats_report",
                "avg_month", "avg_year", "unit"]
    today = date.today()

    # 400일 범위로 1회 조회 (365일 + 작년 동월 여유분)
    from_date = (today - timedelta(days=400)).strftime("%Y-%m-%d")
    to_date = today.strftime("%Y-%m-%d")

    rows, _ = _execute_night_min_flow_query(sitename, facilitytype, from_date, to_date)
    if not rows:
        return [], out_cols

    # rows: (log_time, sitename, facilitytype, label, tagsn, ..., unit, val)
    # "전체" 조회 시 소블록별 분리, 개별 조회 시 단일 통계
    is_all = sitename in ("전체", "%%", "")

    # 소블록별로 일별 평균 그룹화
    from itertools import groupby as _gb
    site_daily: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    site_meta: dict[str, tuple[str, str]] = {}  # sn → (ft, unit)
    for r in rows:
        val = r[8]
        sn_key = r[1]
        if val is not None:
            site_daily[sn_key][r[0]].append(float(val))
        if sn_key not in site_meta:
            site_meta[sn_key] = (r[2], r[7] or "㎥/hr")

    if not is_all:
        # 개별 조회: 모든 태그를 하나로 합산
        site_daily = {"_all": {}}
        for r in rows:
            val = r[8]
            if val is not None:
                site_daily["_all"].setdefault(r[0], []).append(float(val))
        site_meta["_all"] = (rows[0][2], rows[0][7] or "㎥/hr")

    # 기간 키 (daily_avg 키는 UTC 날짜)
    d365_start = (today - timedelta(days=365)).strftime("%Y-%m-%d")
    year_start = today.replace(month=1, day=1).strftime("%Y-%m-%d")
    month_start = today.replace(day=1).strftime("%Y-%m-%d")

    ly = today.replace(year=today.year - 1, day=1)
    ly_end_day = calendar.monthrange(ly.year, ly.month)[1]
    ly_start_str = ly.strftime("%Y-%m-%d")
    ly_end_str = ly.replace(day=ly_end_day).strftime("%Y-%m-%d")

    def _safe_round(x, n=2):
        return round(float(x), n) if x is not None else None

    def _compute_stats(daily_avg: dict[str, float]) -> dict:
        v365 = [v for d, v in daily_avg.items() if d >= d365_start]
        v_ly = [v for d, v in daily_avg.items() if ly_start_str <= d <= ly_end_str]
        v_month = [v for d, v in daily_avg.items() if d >= month_start]
        v_year = [v for d, v in daily_avg.items() if d >= year_start]
        sorted_days = sorted(daily_avg.keys())
        yesterday_val = daily_avg[sorted_days[-1]] if sorted_days else None

        avg_365 = _safe_round(np.mean(v365)) if v365 else None
        std_365 = _safe_round(np.std(v365, ddof=0)) if v365 else None
        avg_30 = _safe_round(np.mean(v_ly)) if v_ly else None
        std_30 = _safe_round(np.std(v_ly, ddof=0)) if v_ly else None
        avg_month_val = _safe_round(np.mean(v_month)) if v_month else None
        avg_year_val = _safe_round(np.mean(v_year)) if v_year else None
        yesterday_min = _safe_round(yesterday_val) if yesterday_val is not None else None

        ci_min_365 = _safe_round(avg_365 - std_365) if avg_365 is not None and std_365 is not None else None
        ci_max_365 = _safe_round(avg_365 + std_365) if avg_365 is not None and std_365 is not None else None
        ci_min_30 = _safe_round(avg_30 - std_30) if avg_30 is not None and std_30 is not None else None
        ci_max_30 = _safe_round(avg_30 + std_30) if avg_30 is not None and std_30 is not None else None

        exceed_365 = _safe_round(yesterday_min - ci_max_365) if yesterday_min is not None and ci_max_365 is not None else None
        exceed_30 = _safe_round(yesterday_min - ci_max_30) if yesterday_min is not None and ci_max_30 is not None else None

        stats_report = [
            {"구분": "평균", "비고": "누수증감량 반영",
             "365일기준": avg_365, "1년전 30일기준": avg_30},
            {"구분": "표준편차", "비고": "",
             "365일기준": std_365, "1년전 30일기준": std_30},
            {"구분": "신뢰구간", "비고": "(평균±표준편차)구간",
             "365일기준": f"{ci_min_365} ~ {ci_max_365}" if ci_min_365 is not None else None,
             "1년전 30일기준": f"{ci_min_30} ~ {ci_max_30}" if ci_min_30 is not None else None},
            {"구분": "신뢰구간 초과량", "비고": "금일 - 신뢰구간 최대값",
             "365일기준": exceed_365, "1년전 30일기준": exceed_30},
        ]
        return {
            "stats_report": stats_report,
            "avg_month": avg_month_val, "avg_year": avg_year_val,
            "yesterday_min": yesterday_min,
            "avg_365": avg_365, "std_365": std_365,
            "ci_min_365": ci_min_365, "ci_max_365": ci_max_365,
            "exceed_365": exceed_365,
        }

    result_rows = []
    stddev_stats_list: list[dict] = []

    for sn_key in sorted(site_daily.keys()):
        dvals = site_daily[sn_key]
        daily_avg = {d: float(np.mean(vs)) for d, vs in dvals.items()}
        if not daily_avg:
            continue
        stats = _compute_stats(daily_avg)
        ft_val, unit_val = site_meta.get(sn_key, (facilitytype, "㎥/hr"))

        if is_all:
            # 전체: flat 요약 행 + stddev_stats 구조
            exceed = stats["exceed_365"]
            verdict = "이상" if exceed is not None and exceed > 0 else "정상"
            sr = stats["stats_report"]
            ci_range = sr[2]["365일기준"] if len(sr) > 2 else None
            result_rows.append([
                sn_key, ft_val,
                stats["avg_365"], stats["std_365"],
                ci_range, exceed,
                stats["yesterday_min"], stats["avg_month"], stats["avg_year"],
                verdict, unit_val,
            ])
            stddev_stats_list.append({
                "sitename": sn_key,
                "facilitytype": ft_val,
                "mean": stats["avg_365"],
                "stddev": stats["std_365"],
                "ci_lower": stats["ci_min_365"],
                "ci_upper": stats["ci_max_365"],
                "excess": exceed,
                "today_value": stats["yesterday_min"],
                "unit": unit_val,
                "avg_month": stats["avg_month"],
                "avg_year": stats["avg_year"],
            })
        else:
            # 개별: 기존 stats_report 구조
            out_sn = rows[0][1]
            result_rows.append([out_sn, ft_val, stats["stats_report"],
                                stats["avg_month"], stats["avg_year"], unit_val])

    if is_all:
        out_cols = ["sitename", "facilitytype",
                    "avg_365", "std_365", "ci_range_365", "exceed_365",
                    "yesterday_min", "avg_month", "avg_year", "판정", "unit"]

    if not result_rows:
        return [], out_cols, []

    return result_rows, out_cols, stddev_stats_list


# ── 결측분석 청크 직접 쿼리 ──────────────────────────────────
# fn_tag_daily_summary PostgreSQL 함수의 대량 JOIN + 홀딩 계산이
# 14초 소요 → Python 측 청크 직접 쿼리 + 분단위 집계로 대체.

def _execute_tag_daily_summary_query(
    start_date: str, end_date: str,
    sitename: str | None = None, facilitytype: str | None = None,
    datainfo_filter: str | None = None,
) -> tuple[list, list]:
    """fn_tag_daily_summary 대체: 청크별 분단위 집계 쿼리 + Python 홀딩 계산.

    466만 raw 행을 Python으로 가져오는 대신, SQL에서 분단위 min/max 집계 후
    약 4만 행만 가져와서 홀딩/통신 결측을 계산한다.
    """
    from collections import defaultdict

    columns = ["result_log_date", "result_sitename", "result_facilitytype",
               "total_good_cnt", "total_expect_cnt", "total_missing_cnt",
               "good_rate_pct", "missing_rate_pct"]

    conn = get_db_connection()
    try:
        cur = conn.cursor()

        # Step 1: 태그 필터링
        conditions = []
        qp: list = []
        if sitename and sitename not in ("", "전체", "%%"):
            conditions.append("sitename = %s")
            qp.append(sitename)
        if facilitytype and facilitytype not in ("", "전체", "%%"):
            conditions.append("facilitytype = %s")
            qp.append(facilitytype)
        if datainfo_filter and datainfo_filter.strip():
            conditions.append("datainfo ~* %s")
            qp.append(datainfo_filter)

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        cur.execute(f"SELECT tagsn, datainfo FROM tb_tag_info{where}", qp)
        tag_datainfo: dict[str, str] = {}
        tagsn_list: list[str] = []
        for tsn, di in cur.fetchall():
            tag_datainfo[tsn] = di or ""
            tagsn_list.append(tsn)

        if not tagsn_list:
            cur.close()
            return [], columns

        # 통신 태그 존재 여부
        has_comm = any("통신" in di for di in tag_datainfo.values())
        comm_tagsns = [tsn for tsn, di in tag_datainfo.items() if "통신" in di]
        non_comm_tagsns = [tsn for tsn, di in tag_datainfo.items() if "통신" not in di]

        from_ts = start_date
        to_ts = (datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        chunks = _get_chunks_for_range(cur, from_ts, to_ts)
        if not chunks:
            cur.close()
            return [], columns

        _sn_display = sitename if sitename and sitename not in ("", "전체", "%%") else "전체"
        _ft_display = facilitytype if facilitytype and facilitytype not in ("", "전체", "%%") else "전체"

        daily_missing: dict[str, int] = defaultdict(int)

        if has_comm and comm_tagsns:
            # 통신 누락: SQL에서 직접 val=1인 분 카운트
            for chunk_name in chunks:
                cur.execute(f"""
                    SELECT logtime::date AS log_dt, COUNT(*) AS cnt
                    FROM {chunk_name}
                    WHERE tagsn = ANY(%s)
                      AND logtime >= %s::timestamptz AND logtime < %s::timestamptz
                      AND val = 1
                    GROUP BY logtime::date
                """, (comm_tagsns, from_ts, to_ts))
                for dt, cnt in cur.fetchall():
                    daily_missing[dt.strftime("%Y-%m-%d")] += cnt
        elif non_comm_tagsns:
            # 홀딩: SQL에서 분단위 min/max 집계 → Python에서 연속 30분 체크
            minute_flags: dict[str, dict[str, bool]] = defaultdict(dict)  # day -> {minute: is_hold}
            for chunk_name in chunks:
                cur.execute(f"""
                    SELECT logtime::date AS log_dt,
                           date_trunc('minute', logtime) AS log_min,
                           CASE WHEN MIN(val) = MAX(val) THEN 1 ELSE 0 END AS hold_flag
                    FROM {chunk_name}
                    WHERE tagsn = ANY(%s)
                      AND logtime >= %s::timestamptz AND logtime < %s::timestamptz
                    GROUP BY logtime::date, date_trunc('minute', logtime)
                """, (non_comm_tagsns, from_ts, to_ts))
                for dt, log_min, hold in cur.fetchall():
                    day_str = dt.strftime("%Y-%m-%d")
                    min_str = log_min.strftime("%Y-%m-%d %H:%M")
                    # 여러 청크에서 같은 분이 올 수 있으므로 hold=0이면 우선
                    if min_str in minute_flags[day_str]:
                        if hold == 0:
                            minute_flags[day_str][min_str] = False
                    else:
                        minute_flags[day_str][min_str] = (hold == 1)

            for day, minutes in minute_flags.items():
                sorted_mins = sorted(minutes.keys())
                streak = 0
                for m_key in sorted_mins:
                    if minutes[m_key]:
                        streak += 1
                    else:
                        if streak >= 30:
                            daily_missing[day] += streak
                        streak = 0
                if streak >= 30:
                    daily_missing[day] += streak

        cur.close()
    finally:
        conn.close()

    # 일별 결과 생성
    result_rows: list[tuple] = []
    d = datetime.strptime(start_date, "%Y-%m-%d")
    end_d = datetime.strptime(end_date, "%Y-%m-%d")
    while d <= end_d:
        day_str = d.strftime("%Y-%m-%d")
        missing = daily_missing.get(day_str, 0)
        good = 1440 - missing
        good_pct = round(good / 1440.0 * 100, 2)
        missing_pct = round(missing / 1440.0 * 100, 2)
        result_rows.append((
            day_str, _sn_display, _ft_display,
            good, 1440, missing, good_pct, missing_pct,
        ))
        d += timedelta(days=1)

    return result_rows, columns


# ── TIMESERIES 인텐트 청크 직접 쿼리 ──────────────────────────
# tb_tag_raw_data JOIN tb_tag_info 패턴의 Hash Join → 116M행 스캔 문제를
# 2단계(tb_tag_info 조회 → 청크별 raw 쿼리)로 분리하여 우회한다.

_TIMESERIES_CHUNK_INTENTS = frozenset({
    "FACILITY_ANALOG_TIMESERIES_TABLE",
    "FACILITY_TAG_DATA_TABLE",
    "FACILITY_FLOW_INSTANT_TIMESERIES_TABLE",
    "FACILITY_FLOW_ACCUMULATED_TIMESERIES_TABLE",
    "FACILITY_DIGITAL_STATUS_TIMESERIES_TABLE",
})

_TIMESERIES_COLUMNS = [
    "sitename", "facilitytype", "tagsn", "val",
    "datainfo", "logtime", "datadesc",
]


def _execute_timeseries_query(
    sitename: str, facilitytype: str, tagtype: str,
    datainfo_pattern: str, from_ts: str, to_ts: str,
    group_code: str | None = None,
) -> tuple[list, list]:
    """tb_tag_info → 청크 직접 쿼리로 시계열 raw 데이터 반환.

    group_code 우선 → datainfo regex 폴백.
    JOIN을 Python 측에서 수행하여 ChunkAppend 플래너의
    Hash Join + 전체 스캔 문제를 우회한다.
    """
    conn = get_db_connection()
    try:
        cur = conn.cursor()

        tag_meta: dict[str, tuple] = {}
        tagsn_list: list[str] = []

        # Step 1-A: group_code가 있으면 tb_tag_group_map JOIN으로 정확한 태그 목록 획득
        if group_code:
            resolved_codes = _resolve_group_codes(group_code)
            resolved_ids = [_GROUP_CODE_TO_ID[c] for c in resolved_codes if c in _GROUP_CODE_TO_ID]

            if resolved_ids:
                conditions_g = ["gm.group_id = ANY(%s)", "ti.tagtype = %s"]
                qp_g: list = [resolved_ids, tagtype]
                if sitename and sitename != "%%":
                    conditions_g.append("ti.sitename = %s")
                    qp_g.append(sitename)
                if facilitytype and facilitytype != "%%":
                    conditions_g.append("ti.facilitytype = %s")
                    qp_g.append(facilitytype)

                cur.execute(
                    "SELECT ti.tagsn, ti.sitename, ti.facilitytype, ti.datainfo,"
                    " COALESCE(ti.datadesc, '') FROM tb_tag_info ti"
                    " JOIN tb_tag_group_map gm ON ti.tagsn = gm.tagsn"
                    f" WHERE {' AND '.join(conditions_g)}",
                    qp_g,
                )
                for tagsn, sn, ft, di, dd in cur.fetchall():
                    tag_meta[tagsn] = (sn, ft, di, dd)
                    tagsn_list.append(tagsn)

                if tagsn_list:
                    logger.info(f"그룹 매칭: group_code={group_code} → {len(tagsn_list)}태그")

        # Step 1-B: 그룹 매칭 결과 없으면 datainfo regex 폴백
        if not tagsn_list:
            conditions = ["tagtype = %s"]
            qp: list = [tagtype]
            if sitename and sitename != "%%":
                conditions.append("sitename = %s")
                qp.append(sitename)
            if facilitytype and facilitytype != "%%":
                conditions.append("facilitytype = %s")
                qp.append(facilitytype)
            if datainfo_pattern:
                conditions.append("datainfo ~ %s")
                qp.append(datainfo_pattern)

            cur.execute(
                "SELECT tagsn, sitename, facilitytype, datainfo,"
                " COALESCE(datadesc, '') FROM tb_tag_info"
                f" WHERE {' AND '.join(conditions)}",
                qp,
            )
            for tagsn, sn, ft, di, dd in cur.fetchall():
                tag_meta[tagsn] = (sn, ft, di, dd)
                tagsn_list.append(tagsn)
            if tagsn_list and group_code:
                logger.info(f"그룹 폴백→datainfo: group_code={group_code}, pattern={datainfo_pattern} → {len(tagsn_list)}태그")

        if not tagsn_list:
            cur.close()
            return [], _TIMESERIES_COLUMNS

        # Step 2: 청크 직접 쿼리
        chunks = _get_chunks_for_range(cur, from_ts, to_ts)
        if not chunks:
            cur.close()
            return [], _TIMESERIES_COLUMNS

        raw_rows = _query_chunks_raw(cur, chunks, tagsn_list, from_ts, to_ts)

        # Step 3: Python JOIN + 정렬 (원본 SQL과 동일: tagsn, logtime ASC)
        result: list[tuple] = []
        for tagsn, logtime, val in raw_rows:
            meta = tag_meta.get(tagsn)
            if meta:
                sn, ft, di, dd = meta
                lt = (logtime.strftime("%Y-%m-%d %H:%M:%S")
                      if hasattr(logtime, "strftime") else str(logtime))
                result.append((sn, ft, tagsn, val, di, lt, dd))
        result.sort(key=lambda r: (r[2], r[5]))

        cur.close()
        return result, _TIMESERIES_COLUMNS
    finally:
        conn.close()


# ── RESERVOIR_LEVEL_HUNTING_CHECK 청크 직접 쿼리 ───────────────


def _execute_hunting_check(sitename: str) -> tuple[list, list]:
    """
    배수지 수위 헌팅 점검 — 듀얼 알고리즘 비교.

    알고리즘 A: 3시간 윈도우, 1분 버킷, 방향 전환 분석 (커스텀)
    알고리즘 B: 5분 분산 뷰 (v_reservoir_status_variance_5min)
    """
    from collections import defaultdict
    from datetime import timedelta

    COLUMNS = [
        "sitename",
        # 알고리즘 A: 방향전환
        "reversal_count", "level_range_3h", "hunting_status",
        # 알고리즘 B: 5분 분산
        "max_val_5m", "min_val_5m", "diff_percent_5m", "variance_status_5m",
    ]

    conn = get_db_connection()
    try:
        cur = conn.cursor()

        # 1. 배수지 수위 태그 조회
        site_clause = ""
        site_params: list = []
        if sitename and sitename != "%%":
            site_clause = "AND ti.sitename = %s"
            site_params = [sitename]

        cur.execute(f"""
            SELECT ti.tagsn, ti.sitename
            FROM tb_tag_info ti
            WHERE ti.facilitytype = '배수지'
              AND ti.datainfo LIKE '%%수위%%'
              AND ti.tagtype = 'Analog Input'
              {site_clause}
        """, site_params)
        tag_rows = cur.fetchall()
        if not tag_rows:
            cur.close()
            return [], COLUMNS

        tagsn_list = [r[0] for r in tag_rows]
        tagsn_to_site: dict[str, str] = {r[0]: r[1] for r in tag_rows}
        target_sites = set(tagsn_to_site.values())

        # ---------- 알고리즘 B: 5분 분산 뷰 쿼리 ----------
        variance_map: dict[str, dict] = {}
        try:
            v_clause = ""
            v_params: list = []
            if sitename and sitename != "%%":
                v_clause = "WHERE v.sitename = %s"
                v_params = [sitename]
            cur.execute(f"""
                SELECT v.sitename,
                       COALESCE(v.max_val, 0),
                       COALESCE(v.min_val, 0),
                       COALESCE(v.diff_percent, 0),
                       COALESCE(v.variance_status, '정상')
                FROM v_reservoir_status_variance_5min v
                {v_clause}
            """, v_params)
            for r in cur.fetchall():
                sn = r[0]
                # 동일 사이트 복수 태그 → diff_percent 최대값 우선
                existing = variance_map.get(sn)
                if existing is None or float(r[3]) > float(existing["diff_percent"]):
                    variance_map[sn] = {
                        "max_val": float(r[1]),
                        "min_val": float(r[2]),
                        "diff_percent": float(r[3]),
                        "variance_status": r[4],
                    }
        except Exception as e:
            logger.warning(f"5분 분산 뷰 조회 실패 (무시): {e}")
            conn.rollback()

        # ---------- 알고리즘 A: 3시간 방향전환 분석 ----------
        now = datetime.now()
        from_ts = (now - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
        to_ts = now.strftime("%Y-%m-%d %H:%M:%S")

        chunks = _get_chunks_for_range(cur, from_ts, to_ts)
        if not chunks:
            cur.close()
            # 청크 없으면 B 결과만 반환
            results = []
            for sn in sorted(target_sites):
                vd = variance_map.get(sn, {})
                results.append((
                    sn, 0, 0.0, "STABLE",
                    vd.get("max_val", 0), vd.get("min_val", 0),
                    vd.get("diff_percent", 0), vd.get("variance_status", "-"),
                ))
            return results, COLUMNS

        raw_rows = _query_chunks_raw(cur, chunks, tagsn_list, from_ts, to_ts)
        cur.close()

        if not raw_rows:
            results = []
            for sn in sorted(target_sites):
                vd = variance_map.get(sn, {})
                results.append((
                    sn, 0, 0.0, "STABLE",
                    vd.get("max_val", 0), vd.get("min_val", 0),
                    vd.get("diff_percent", 0), vd.get("variance_status", "-"),
                ))
            return results, COLUMNS

        # 3. 사이트별 1분 버킷 평균
        site_buckets: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
        for tagsn, logtime, val in raw_rows:
            sn = tagsn_to_site.get(tagsn)
            if not sn or val is None:
                continue
            ts = logtime if isinstance(logtime, datetime) else datetime.fromisoformat(str(logtime))
            bucket_key = int(ts.timestamp()) // 60
            site_buckets[sn][bucket_key].append(float(val))

        # 4. 사이트별 분석 + B 결과 병합
        results = []
        all_sites = target_sites | set(site_buckets.keys())
        for sn in all_sites:
            buckets = site_buckets.get(sn, {})
            vd = variance_map.get(sn, {})

            if len(buckets) < 3:
                results.append((
                    sn, 0, 0.0, "STABLE",
                    vd.get("max_val", 0), vd.get("min_val", 0),
                    vd.get("diff_percent", 0), vd.get("variance_status", "-"),
                ))
                continue

            sorted_keys = sorted(buckets.keys())
            avg_levels = [(k, sum(buckets[k]) / len(buckets[k])) for k in sorted_keys]

            # 진동폭
            all_vals = [lv for _, lv in avg_levels]
            level_range = round(max(all_vals) - min(all_vals), 3)

            # 방향 감지
            directions: list[tuple[int, int]] = []
            for i in range(1, len(avg_levels)):
                delta = avg_levels[i][1] - avg_levels[i - 1][1]
                if delta > 0.05:
                    directions.append((avg_levels[i][0], 1))
                elif delta < -0.05:
                    directions.append((avg_levels[i][0], -1))

            if not directions:
                results.append((
                    sn, 0, level_range, "STABLE",
                    vd.get("max_val", 0), vd.get("min_val", 0),
                    vd.get("diff_percent", 0), vd.get("variance_status", "-"),
                ))
                continue

            # 유효 방향 구간 (동일 방향 ≥3분 지속)
            valid_cycles = []
            cur_dir = directions[0][1]
            cur_count = 1

            for j in range(1, len(directions)):
                if directions[j][1] == cur_dir:
                    cur_count += 1
                else:
                    if cur_count >= 3:
                        valid_cycles.append(cur_dir)
                    cur_dir = directions[j][1]
                    cur_count = 1
            if cur_count >= 3:
                valid_cycles.append(cur_dir)

            reversal_count = max(len(valid_cycles) - 1, 0) if valid_cycles else 0

            if reversal_count >= 4 and level_range >= 0.3:
                status = "CONFIRMED_HUNTING"
            elif reversal_count >= 2 and level_range >= 0.2:
                status = "SUSPECTED"
            else:
                status = "STABLE"

            results.append((
                sn, reversal_count, level_range, status,
                vd.get("max_val", 0), vd.get("min_val", 0),
                vd.get("diff_percent", 0), vd.get("variance_status", "-"),
            ))

        results.sort(key=lambda r: (-r[1], r[0]))
        return results, COLUMNS

    finally:
        conn.close()


# ── FACILITY_CATALOG_TREND_TABLE 청크 직접 쿼리 ───────────────


def _execute_catalog_trend_query(
    facilitytype: str,
    sitename: str,
    trend_name_filter: str,
    label_pattern: str,
    from_ts: str,
    to_ts: str,
) -> tuple[list, list]:
    """tb_trend_catalog + tb_tag_raw_data 청크 직접 쿼리."""
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

    # Step2+3: 공용 유틸로 청크 직접 쿼리
    chunks = _get_chunks_for_range(cur, from_ts, to_ts)
    if not chunks:
        return [], []
    logger.info(f"대상 청크: {len(chunks)}개")

    agg = _query_chunks_agg(cur, chunks, tagsn_list, from_ts, to_ts, "1 day")
    if not agg:
        return [], []

    # Step4: 재집계 + 행 변환
    reagg = _reaggregate(agg)
    columns = ["현장명", "항목", "날짜", "평균", "최대", "최소", "단위"]
    rows = []
    for (tagsn, bucket) in sorted(
        reagg.keys(),
        key=lambda x: (tag_meta.get(x[0], ("",))[0], tag_meta.get(x[0], ("",))[1], x[1]),
    ):
        meta = tag_meta.get(tagsn)
        if not meta:
            continue
        avg_val, max_val, min_val, _ = reagg[(tagsn, bucket)]
        date_str = bucket.strftime("%Y-%m-%d") if hasattr(bucket, "strftime") else str(bucket)[:10]
        rows.append((meta[0], meta[1], date_str, avg_val, max_val, min_val, meta[2]))

    return rows, columns


# ── RESERVOIR SUPPLY QUERY ────────────────────────────────────────────


_SUPPLY_INTENTS = frozenset({
    "RESERVOIR_DAILY_SUPPLY_TABLE",
    "RESERVOIR_MONTHLY_SUPPLY_TABLE",
    "RESERVOIR_DAILY_SUPPLY_CHART",
    "RESERVOIR_MONTHLY_SUPPLY_CHART",
})


def _execute_reservoir_supply_query(
    conn,
    mode: str,  # "daily" or "monthly"
    from_date,  # datetime.date
    to_date,    # datetime.date
) -> tuple[list, list]:
    """
    유량적산(유출) 기반 배수지 일별/월별 공급량 계산.

    경계값 = 해당 기간 시작점의 첫 기록 (00:00:00 우선).
    공급량 = 다음 경계값 - 현재 경계값 (음수 → 0, 통신홀딩 기간은 0).
    적산유량 센서 없는 현장은 "적산유량이 없는 현장" 표기.
    """
    from datetime import timedelta as _td

    cur = conn.cursor()

    # 1. 적산유량(유출) 태그 목록
    cur.execute("""
        SELECT t.tagsn, t.sitename, t.datainfo, COALESCE(t.unit, '㎥')
        FROM tb_tag_info t
        WHERE t.facilitytype = '배수지'
          AND t.tagtype = 'Analog Input'
          AND (
            t.datainfo ILIKE '%유출유량적산%'
            OR t.datainfo ILIKE '%유출적산유량%'
            OR (t.datainfo ILIKE '%유량적산%' AND t.datainfo NOT ILIKE '%유입%')
          )
        ORDER BY t.sitename, t.datainfo
    """)
    tag_rows = cur.fetchall()

    # 2. 전체 배수지 현장 목록
    cur.execute("""
        SELECT DISTINCT sitename
        FROM tb_service_reservoir_info
        ORDER BY sitename
    """)
    all_sites = [r[0] for r in cur.fetchall()]

    # sitename → [(tagsn, datainfo, unit)]
    site_tags: dict = {}
    tagsn_to_meta: dict = {}
    for tagsn, sn, di, unit in tag_rows:
        site_tags.setdefault(sn, []).append((tagsn, di, unit))
        tagsn_to_meta[tagsn] = (sn, di, unit)

    # 3. 경계 날짜 목록 생성 (periods: [(start_str, end_str, label)])
    if mode == "daily":
        periods = []
        d = from_date
        while d <= to_date:
            d_prev = d - _td(days=1)
            periods.append((d_prev.strftime("%Y-%m-%d"), d.strftime("%Y-%m-%d"), d.strftime("%Y-%m-%d")))
            d += _td(days=1)
        # LATERAL 쿼리용 경계 날짜 범위 (from_date-1 ~ to_date)
        bdate_start = from_date - _td(days=1)
        bdate_end = to_date
        interval_expr = "INTERVAL '1 day'"
    else:  # monthly
        periods = []
        m = from_date.replace(day=1)
        end_m = to_date.replace(day=1)
        while m <= end_m:
            m_next = (m + _td(days=32)).replace(day=1)
            label = m.strftime("%Y-%m")
            periods.append((m.strftime("%Y-%m-%d"), m_next.strftime("%Y-%m-%d"), label))
            m = m_next
        # LATERAL 쿼리용 경계 날짜 범위 (from_month 1일 ~ to_month 다음달 1일)
        bdate_start = from_date.replace(day=1)
        bdate_end = (to_date.replace(day=1) + _td(days=32)).replace(day=1)
        interval_expr = "INTERVAL '1 month'"

    # 4. LATERAL 인덱스 스캔으로 경계값 조회
    # idx_tag_raw_tagsn_time(tagsn, logtime DESC) 활용 → 경계 날짜별 첫 기록 1건만 조회
    # 기존 full-scan(~97만 행) 대비 경계점만 조회(~700행)로 10배 이상 속도 향상
    # generate_series를 서브쿼리로 date 캐스팅 → bdate::text = 'YYYY-MM-DD' 포맷 보장
    all_tagsn = list(tagsn_to_meta.keys())
    boundary_vals: dict = {}  # (tagsn, date_str) → (val, tag_stat)
    if all_tagsn and periods:
        cur.execute(f"""
            SELECT t.tagsn, d.bdate::text, r.val, r.tag_stat
            FROM unnest(%s::text[]) AS t(tagsn)
            CROSS JOIN (
                SELECT gs::date AS bdate
                FROM generate_series(%s::date, %s::date, {interval_expr}) AS gs
            ) AS d
            LEFT JOIN LATERAL (
                SELECT val, tag_stat
                FROM tb_tag_raw_data
                WHERE tagsn = t.tagsn
                  AND logtime >= d.bdate::timestamp
                  AND logtime < (d.bdate + {interval_expr})::timestamp
                  AND val IS NOT NULL
                ORDER BY logtime ASC
                LIMIT 1
            ) AS r ON true
        """, (all_tagsn, bdate_start, bdate_end))
        for tagsn, bdate, val, tag_stat in cur.fetchall():
            if val is not None:
                boundary_vals[(tagsn, bdate)] = (float(val), tag_stat)

    # 5. 공급량 계산
    col_label = "date" if mode == "daily" else "month"
    columns = ["sitename", col_label, "supply_m3", "unit", "note"]
    rows = []

    for sn in sorted(set(all_sites)):
        tags_for_site = site_tags.get(sn, [])
        if not tags_for_site:
            # 적산유량 센서 없는 현장 → 첫 기간 행에만 표시
            first_label = periods[0][2] if periods else "-"
            rows.append((sn, first_label, None, "-", "<<warn:적산유량이 없는 현장>>"))
            continue

        for tagsn, di, unit in tags_for_site:
            for start_str, end_str, label in periods:
                bv_s = boundary_vals.get((tagsn, start_str))
                bv_e = boundary_vals.get((tagsn, end_str))
                v_s = bv_s[0] if bv_s else None
                t_s = bv_s[1] if bv_s else None
                v_e = bv_e[0] if bv_e else None
                t_e = bv_e[1] if bv_e else None

                if v_s is None or v_e is None:
                    rows.append((sn, label, None, unit, "<<warn:데이터 없음>>"))
                    continue

                delta = v_e - v_s
                is_holding = (
                    (t_s and t_s != "GOOD!!") or (t_e and t_e != "GOOD!!")
                )
                if delta < 0:
                    supply, note = 0.0, "<<warn:리셋>>"
                elif delta == 0 and is_holding:
                    supply, note = 0.0, "<<warn:통신홀딩>>"
                else:
                    supply, note = round(delta, 2), ""

                rows.append((sn, label, supply, unit, note))

    return rows, columns


def _execute_reservoir_supply_query_with_conn(
    mode: str, from_date, to_date
) -> tuple[list, list]:
    """DB 연결 포함 공급량 쿼리 wrapper (asyncio.to_thread 호환).
    병렬 쿼리 비활성화 옵션을 연결 레벨에서 적용한다.
    """
    # options로 세션 GUC 설정 (병렬 worker 비활성화 → shared memory 부족 방지)
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, database=DB_NAME,
        user=DB_USER, password=DB_PASSWORD,
        options="-c max_parallel_workers_per_gather=0 -c enable_parallel_hash=off",
    )
    conn.autocommit = True
    try:
        return _execute_reservoir_supply_query(conn, mode, from_date, to_date)
    finally:
        conn.close()


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
        # 한달/일년 평균 수위 + 단위 추출 (첫 행 기준 or 전체 평균)
        _level_vals = []
        _month_vals = []
        _year_vals = []
        _level_unit = ""
        for row in rows:
            rd = dict(zip(columns, row))
            lv = rd.get("latest_val")
            am = rd.get("avg_month")
            ay = rd.get("avg_year")
            if lv is not None:
                try:
                    _level_vals.append(float(lv))
                except (ValueError, TypeError):
                    pass
            if am is not None:
                try:
                    _month_vals.append(float(am))
                except (ValueError, TypeError):
                    pass
            if ay is not None:
                try:
                    _year_vals.append(float(ay))
                except (ValueError, TypeError):
                    pass
            if not _level_unit:
                _level_unit = rd.get("unit", "m")
        if _level_vals:
            data["avg_latest"] = round(sum(_level_vals) / len(_level_vals), 2)
        if _month_vals:
            data["avg_month"] = round(sum(_month_vals) / len(_month_vals), 2)
        if _year_vals:
            data["avg_year"] = round(sum(_year_vals) / len(_year_vals), 2)
        data["unit"] = _level_unit or "m"

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
        _profiles = site_profiler.profiles if site_profiler and site_profiler.profiles else None
        counts = count_anomaly_levels(rows, columns)
        data["total_tag_count"] = len(rows)
        data["error_count"] = counts["이상"]
        data["warn_count"] = counts["주의"]
        data["ok_count"] = counts["정상"]
        data["comm_error_sites"] = count_comm_error_sites(rows, columns)

        # Isolation Forest ML 보강 (백그라운드 학습 — ensure_trained 불필요)
        data["ml_model_count"] = 0
        data["ml_anomaly_count"] = 0
        data["ml_agree_count"] = 0
        try:
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

        # 인과 인덱스 통계 (전체 스캔 시에는 요약만)
        if _CAUSAL_INDEX:
            data["causal_index_count"] = len(_CAUSAL_INDEX)
            data["causal_template_types"] = list(_CAUSAL_TEMPLATE_MAP.keys())

        # per-row grade/group 보강 — 프론트엔드에서 그룹별 필터/정렬 지원
        _sn_idx = columns.index("sitename") if "sitename" in columns else None
        _ft_idx = columns.index("facilitytype") if "facilitytype" in columns else None
        _z_idx = columns.index("z_score") if "z_score" in columns else None
        if _sn_idx is not None and _ft_idx is not None and _z_idx is not None:
            columns.extend(["site_group", "alert_grade"])
            enriched_rows = []
            for row in rows:
                sn = row[_sn_idx] or ""
                ft = row[_ft_idx] or ""
                try:
                    z = float(row[_z_idx] or 0)
                except (ValueError, TypeError):
                    z = 0.0
                grp = "B"
                grade = None
                if _profiles:
                    _prof = _profiles.get((sn, ft))
                    grp = _prof.get("site_group", "B") if _prof else "B"
                    level = classify_z_level_by_group(z, grp)
                    if level != "정상":
                        info_cnt = (_prof or {}).get("info_count_7d", 0)
                        grade = classify_alert_grade(grp, level, "정상", None, info_cnt)
                enriched_rows.append(tuple(list(row) + [grp, grade]))
            rows[:] = enriched_rows

        data["anomaly_scan_detail_block"] = _EXPAND_MARKER
        data["_detail_blocks"]["anomaly_scan_detail_block"] = \
            build_anomaly_scan_detail_block(rows, columns, site_profiles=_profiles)

    # -------------------------------------------------
    # 시설 정밀 진단: Z-Score + 방향전환 + IF 복합 판정
    # -------------------------------------------------
    if intent == "ANOMALY_FACILITY_DETAIL":
        _profiles = site_profiler.profiles if site_profiler and site_profiler.profiles else None
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
        data["ml_anomaly_count"] = 0
        try:
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

        # 인과관계 검증 (group_code 매칭 가능한 이상 태그 탐색)
        causal_result = None
        if _CAUSAL_INDEX and rows:
            try:
                col_map_c = {c: i for i, c in enumerate(columns)}
                _c_tagsn_idx = col_map_c.get("tagsn")
                _c_z_idx = col_map_c.get("z_score")
                _c_datainfo_idx = col_map_c.get("datainfo")
                if _c_tagsn_idx is not None and _c_z_idx is not None:
                    # |z_score| 높은 순으로 정렬 → group_code 매칭 가능한 태그 찾기
                    _warn_th = GROUP_THRESHOLDS.get(_group, GROUP_THRESHOLDS["B"])["warn"]
                    _anomaly_rows = []
                    for _r in rows:
                        try:
                            _zv = float(_r[_c_z_idx]) if _r[_c_z_idx] else 0
                        except (ValueError, TypeError):
                            _zv = 0
                        if abs(_zv) >= _warn_th:
                            _anomaly_rows.append((_zv, _r))
                    _anomaly_rows.sort(key=lambda x: abs(x[0]), reverse=True)

                    _a_gc = None
                    _a_dir = None
                    _a_tagsn = ""
                    _a_datainfo = ""
                    from anomaly_detector import verify_causal_context
                    for _zv, _anomaly_row in _anomaly_rows:
                        _a_tagsn = str(_anomaly_row[_c_tagsn_idx] or "")
                        _a_datainfo = str(_anomaly_row[_c_datainfo_idx] or "")
                        _a_dir = "RISE" if _zv > 0 else "FALL"
                        # _CAUSAL_INDEX tag_map 우선 → _FALLBACK_GC_KEYWORDS 폴백
                        _a_gc = _resolve_group_code_for_tagsn(
                            _site, _ft, _a_tagsn, _a_datainfo,
                        )
                        if _a_gc:
                            break
                    if _a_gc:
                            # 구역 감지 (배수지: datainfo에서 "1지","2지" 패턴)
                            _a_zone = None
                            _zm = _ZONE_PATTERN.search(_a_datainfo)
                            if _zm:
                                _a_zone = f"{_zm.group(1)}지"

                            causal_result = verify_causal_context(
                                _query_recent_values, _site, _ft,
                                _a_gc, _a_dir, _CAUSAL_INDEX,
                                zone=_a_zone,
                            )
                            if causal_result:
                                # SLM 자연어 해석 (비정상 패턴일 때만)
                                if not causal_result.get("chain_matched"):
                                    try:
                                        from anomaly_detector import generate_causal_explanation
                                        _slm_text = generate_causal_explanation(
                                            lambda p: ollama_client.generate(p),
                                            causal_result, _site, _ft,
                                        )
                                        if _slm_text:
                                            causal_result["_slm_explanation"] = _slm_text
                                    except Exception as _slm_err:
                                        logger.debug("인과 SLM 해석 실패 (무시): %s", _slm_err)
                                data["causal_context"] = causal_result
            except Exception as e:
                logger.warning(f"인과관계 검증 실패 (무시): {e}")

        # 시설간 교차 검증 (상류/하류 가동률·방향 비교)
        cross_facility_result = None
        if _CAUSAL_INDEX:
            try:
                from anomaly_detector import cross_facility_check_single

                cross_facility_result = cross_facility_check_single(
                    _query_recent_values, _site, _ft, _CAUSAL_INDEX,
                )
                if cross_facility_result and cross_facility_result.get("has_mismatch"):
                    data["cross_facility"] = cross_facility_result
            except Exception as e:
                logger.warning(f"교차 검증 실패 (무시): {e}")

        # 다중 홉 전파 추적 (인과 or 교차 검증 결과가 있을 때만)
        propagation_trace = None
        if _CAUSAL_INDEX and (causal_result or cross_facility_result):
            try:
                from anomaly_detector import (
                    trace_propagation_forward,
                    trace_upstream_root_cause,
                )
                _target_key = (_site, _ft)
                _fwd = trace_propagation_forward(
                    _query_recent_values, _target_key, _CAUSAL_INDEX,
                )
                _bwd = trace_upstream_root_cause(
                    _query_recent_values, _target_key, _CAUSAL_INDEX,
                )
                if _fwd.get("hops") or _bwd.get("root_cause") or _bwd.get("hops"):
                    propagation_trace = {"forward": _fwd, "backward": _bwd}
                    data["propagation_trace"] = propagation_trace
            except Exception as e:
                logger.warning(f"전파 추적 실패 (무시): {e}")

        # 시설 내부 인과 검증 (펌프/밸브/수위 물리법칙)
        intra_facility_result = None
        if _CAUSAL_INDEX:
            try:
                _a_zone_intra = None
                if _a_datainfo:
                    _zm2 = _ZONE_PATTERN.search(_a_datainfo)
                    if _zm2:
                        _a_zone_intra = f"{_zm2.group(1)}지"
                intra_facility_result = verify_intra_facility(
                    _query_recent_values, _site, _ft, _CAUSAL_INDEX,
                    zone=_a_zone_intra,
                )
                if intra_facility_result:
                    data["intra_facility"] = intra_facility_result
            except Exception as e:
                logger.warning("시설 내부 인과 검증 실패 (무시): %s", e)

        data["anomaly_facility_detail_block"] = _EXPAND_MARKER
        data["_detail_blocks"]["anomaly_facility_detail_block"] =             build_anomaly_facility_detail_block(
                rows, columns,
                site_profiles=_profiles,
                sitename=_site,
                facilitytype=_ft,
                pattern_result=pattern_result,
                causal_result=causal_result,
                cross_facility_result=cross_facility_result,
                propagation_trace=propagation_trace,
                intra_facility_result=intra_facility_result,
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
        data["cusum_inactive_count"] = cusum_counts.get("비활성", 0)

        # 테이블 데이터를 CUSUM 결과 테이블로 교체
        cusum_table_rows, cusum_table_cols = build_cusum_summary_table(cusum_results)
        data["_cusum_results"] = cusum_results
        data["_cusum_table_rows"] = cusum_table_rows
        data["_cusum_table_columns"] = cusum_table_cols

        data["leak_cusum_detail_block"] = _EXPAND_MARKER
        data["_detail_blocks"]["leak_cusum_detail_block"] = \
            build_leak_cusum_detail_block(cusum_results)

    # -------------------------------------------------
    # 시설간 교차 검증 (ANOMALY_CROSS_FACILITY)
    # -------------------------------------------------
    if intent == "ANOMALY_CROSS_FACILITY":
        mismatches = params.get("_cross_facility_mismatches", [])
        from anomaly_detector import build_cross_facility_scan_block
        scan_items, scan_data = build_cross_facility_scan_block(mismatches)
        data.update(scan_data)
        data["cross_facility_scan_block"] = _EXPAND_MARKER
        data["_detail_blocks"]["cross_facility_scan_block"] = scan_items

    # -------------------------------------------------
    # 물 수지 검증: 상류 유출 vs 하류 합계 비교
    # -------------------------------------------------
    if intent == "ANOMALY_FLOW_BALANCE":
        edges = params.get("_flow_balance_edges", [])
        from flow_balance import build_flow_balance_scan_block
        scan_items, scan_data = build_flow_balance_scan_block(edges)
        data.update(scan_data)
        data["flow_balance_scan_block"] = _EXPAND_MARKER
        data["_detail_blocks"]["flow_balance_scan_block"] = scan_items
        # flow_balance_summary for frontend UI
        imbalance_edges = [e for e in edges if e["grade"] != "정상" and e["status"] == "ok"]
        data["flow_balance_summary"] = {
            "total_edges": len(edges),
            "imbalance_count": len(imbalance_edges),
            "worst_edges": sorted(
                [e for e in edges if e["status"] == "ok"],
                key=lambda e: abs(e["imbalance_pct"]),
                reverse=True,
            )[:10],
        }

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

    # 빈 SQL 체크 (동적 SQL 생성 인텐트는 커스텀 핸들러에서 sql_combined 설정)
    _DYNAMIC_SQL_INTENTS = {"ALARM_ABNORMAL_LOCATIONS", "FACILITY_CATALOG_TREND_TABLE", "RESERVOIR_LEVEL_HUNTING_CHECK", "ANOMALY_CROSS_FACILITY", "ANOMALY_FLOW_BALANCE", "NIGHT_MIN_FLOW_SUMMARY_TABLE", "NIGHT_MIN_FLOW_STATUS", "TAG_DAILY_MISSING_SUMMARY", "FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS"} | _SUPPLY_INTENTS
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
            rows, columns = await asyncio.to_thread(
                _execute_night_min_flow_query, _nmf_sn, _nmf_ft, _nmf_from, _nmf_to
            )
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

    # ── ANOMALY_SCAN_ALL 캐시 히트: 전체 파이프라인 건너뜀 ──
    if intent == "ANOMALY_SCAN_ALL" and _ANOMALY_SCAN_CACHE and _ANOMALY_SCAN_CACHE_TIME:
        cache_age = (datetime.now() - _ANOMALY_SCAN_CACHE_TIME).total_seconds()
        if cache_age < _ANOMALY_SCAN_CACHE_TTL:
            logger.info(f"ANOMALY_SCAN_ALL 캐시 히트 ({cache_age:.0f}초 전) sitename={params.get('sitename')!r} facilitytype={params.get('facilitytype')!r}")
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

    # NIGHT_MIN_FLOW_STATUS: 커스텀 핸들러 (tb_tag_group_map 폴백 포함)
    if intent == "NIGHT_MIN_FLOW_STATUS":
        _nfs_sn = params.get("sitename", "")
        _nfs_ft = params.get("facilitytype", "소블록")
        try:
            _now = datetime.now()
            # current: 최근 1일
            _cur_rows, _cur_cols = await asyncio.to_thread(
                _execute_night_min_flow_query,
                _nfs_sn, _nfs_ft,
                (_now - timedelta(days=1)).strftime("%Y-%m-%d"),
                _now.strftime("%Y-%m-%d"),
            )
            if not _cur_rows:
                logger.info(f"NIGHT_MIN_FLOW_STATUS 데이터 없음: {_nfs_sn} {_nfs_ft}")
            else:
                # avg month: 최근 1달
                _mon_rows, _ = await asyncio.to_thread(
                    _execute_night_min_flow_query,
                    _nfs_sn, _nfs_ft,
                    (_now - timedelta(days=30)).strftime("%Y-%m-%d"),
                    _now.strftime("%Y-%m-%d"),
                )
                # avg year: 최근 1년
                _yr_rows, _ = await asyncio.to_thread(
                    _execute_night_min_flow_query,
                    _nfs_sn, _nfs_ft,
                    (_now - timedelta(days=365)).strftime("%Y-%m-%d"),
                    _now.strftime("%Y-%m-%d"),
                )
                # 결과 집계: current_val = 최신 row, avg_month/avg_year = 평균
                _last = dict(zip(_cur_cols, _cur_rows[-1]))
                _result_cols = ["sitename", "facilitytype", "label", "datadesc",
                                "current_val", "unit", "log_time", "avg_month", "avg_year"]
                # 태그별 그룹핑
                from collections import defaultdict
                _tag_vals_m: dict[str, list] = defaultdict(list)
                _tag_vals_y: dict[str, list] = defaultdict(list)
                for r in _mon_rows:
                    rd = dict(zip(_cur_cols, r))
                    _tag_vals_m[rd.get("tagsn", "")].append(float(rd.get("val") or 0))
                for r in _yr_rows:
                    rd = dict(zip(_cur_cols, r))
                    _tag_vals_y[rd.get("tagsn", "")].append(float(rd.get("val") or 0))
                # 태그별 최신 row → 결과 행 생성
                _seen: set = set()
                _result_rows = []
                for r in reversed(_cur_rows):
                    rd = dict(zip(_cur_cols, r))
                    _tsn = rd.get("tagsn", "")
                    if _tsn in _seen:
                        continue
                    _seen.add(_tsn)
                    _cv = float(rd.get("val") or 0)
                    _mv = _tag_vals_m.get(_tsn, [])
                    _yv = _tag_vals_y.get(_tsn, [])
                    _am = round(sum(_mv) / len(_mv), 2) if _mv else None
                    _ay = round(sum(_yv) / len(_yv), 2) if _yv else None
                    _result_rows.append((
                        rd.get("sitename", ""), rd.get("facilitytype", ""),
                        rd.get("label", rd.get("datainfo", "")), rd.get("datadesc", ""),
                        round(_cv, 2), rd.get("unit", ""), rd.get("log_time", ""),
                        _am, _ay,
                    ))
                if _result_rows:
                    sql_combined = "-- custom handler"
                    rows = _result_rows
                    columns = _result_cols
                    logger.info(f"NIGHT_MIN_FLOW_STATUS 커스텀: {len(_result_rows)}행")
        except Exception as e:
            logger.warning(f"NIGHT_MIN_FLOW_STATUS 커스텀 실패, 원본 SQL 폴백: {e}")

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
                _tdm_rendered = answer_template if isinstance(answer_template, dict) else {}
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
        cross_facility_mismatches=_filter_cross_mismatches(processed_data.get("cross_facility_mismatches"), params.get("sitename")),
        cross_facility_mismatch_count=len(_filter_cross_mismatches(processed_data.get("cross_facility_mismatches"), params.get("sitename")) or []),
        cross_anomaly_count=processed_data.get("cross_anomaly_count"),
        data_quality_issues=_filter_by_sitename(processed_data.get("data_quality_issues"), params.get("sitename")),
        equipment_failure_impacts=_filter_by_sitename(processed_data.get("equipment_failure_impacts"), params.get("sitename")),
        equipment_failure_count=len(_filter_by_sitename(processed_data.get("equipment_failure_impacts"), params.get("sitename")) or []),
        flow_balance_summary=_filter_flow_balance(processed_data.get("flow_balance_summary"), params.get("sitename")),
        intra_facility=processed_data.get("intra_facility"),
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

        # 빈 SQL 체크 (동적 SQL 생성 인텐트는 커스텀 핸들러에서 sql_combined 설정)
        _DYNAMIC_SQL_INTENTS_STREAM = {"ALARM_ABNORMAL_LOCATIONS", "FACILITY_CATALOG_TREND_TABLE", "RESERVOIR_LEVEL_HUNTING_CHECK", "ANOMALY_CROSS_FACILITY", "ANOMALY_FLOW_BALANCE", "NIGHT_MIN_FLOW_SUMMARY_TABLE", "NIGHT_MIN_FLOW_STATUS", "TAG_DAILY_MISSING_SUMMARY", "FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS"} | _SUPPLY_INTENTS
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

        # ── ANOMALY_SCAN_ALL 캐시 히트: 전체 파이프라인 건너뜀 ──
        if intent == "ANOMALY_SCAN_ALL" and _ANOMALY_SCAN_CACHE and _ANOMALY_SCAN_CACHE_TIME:
            cache_age = (datetime.now() - _ANOMALY_SCAN_CACHE_TIME).total_seconds()
            if cache_age < _ANOMALY_SCAN_CACHE_TTL:
                logger.info(f"[SSE] ANOMALY_SCAN_ALL 캐시 히트 ({cache_age:.0f}초 전)")
                yield _sse_event("progress", {"step": "cache_hit", "message": "캐시된 이상감지 결과를 반환합니다..."})
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

        # NIGHT_MIN_FLOW_STATUS: 커스텀 핸들러 (tb_tag_group_map 폴백 포함)
        if intent == "NIGHT_MIN_FLOW_STATUS":
            _nfs_sn = params.get("sitename", "")
            _nfs_ft = params.get("facilitytype", "소블록")
            try:
                _now = datetime.now()
                _cur_rows, _cur_cols = await asyncio.to_thread(
                    _execute_night_min_flow_query,
                    _nfs_sn, _nfs_ft,
                    (_now - timedelta(days=1)).strftime("%Y-%m-%d"),
                    _now.strftime("%Y-%m-%d"),
                )
                if _cur_rows:
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
                    from collections import defaultdict
                    _tag_vals_m: dict[str, list] = defaultdict(list)
                    _tag_vals_y: dict[str, list] = defaultdict(list)
                    for r in _mon_rows:
                        rd = dict(zip(_cur_cols, r))
                        _tag_vals_m[rd.get("tagsn", "")].append(float(rd.get("val") or 0))
                    for r in _yr_rows:
                        rd = dict(zip(_cur_cols, r))
                        _tag_vals_y[rd.get("tagsn", "")].append(float(rd.get("val") or 0))
                    _result_cols = ["sitename", "facilitytype", "label", "datadesc",
                                    "current_val", "unit", "log_time", "avg_month", "avg_year"]
                    _seen: set = set()
                    _result_rows = []
                    for r in reversed(_cur_rows):
                        rd = dict(zip(_cur_cols, r))
                        _tsn = rd.get("tagsn", "")
                        if _tsn in _seen:
                            continue
                        _seen.add(_tsn)
                        _cv = float(rd.get("val") or 0)
                        _mv = _tag_vals_m.get(_tsn, [])
                        _yv = _tag_vals_y.get(_tsn, [])
                        _am = round(sum(_mv) / len(_mv), 2) if _mv else None
                        _ay = round(sum(_yv) / len(_yv), 2) if _yv else None
                        _result_rows.append((
                            rd.get("sitename", ""), rd.get("facilitytype", ""),
                            rd.get("label", rd.get("datainfo", "")), rd.get("datadesc", ""),
                            round(_cv, 2), rd.get("unit", ""), rd.get("log_time", ""),
                            _am, _ay,
                        ))
                    if _result_rows:
                        sql_combined = "-- custom handler"
                        rows = _result_rows
                        columns = _result_cols
                        logger.info(f"SSE NIGHT_MIN_FLOW_STATUS 커스텀: {len(_result_rows)}행")
            except Exception as e:
                logger.warning(f"SSE NIGHT_MIN_FLOW_STATUS 커스텀 실패, 원본 SQL 폴백: {e}")

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
                    _tdm_rendered = answer_template if isinstance(answer_template, dict) else {}
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
            cross_anomaly_count=processed_data.get("cross_anomaly_count"),
            cross_facility_mismatches=_filter_cross_mismatches(processed_data.get("cross_facility_mismatches"), params.get("sitename")),
            cross_facility_mismatch_count=len(_filter_cross_mismatches(processed_data.get("cross_facility_mismatches"), params.get("sitename")) or []),
            data_quality_issues=_filter_by_sitename(processed_data.get("data_quality_issues"), params.get("sitename")),
            equipment_failure_impacts=_filter_by_sitename(processed_data.get("equipment_failure_impacts"), params.get("sitename")),
            equipment_failure_count=len(_filter_by_sitename(processed_data.get("equipment_failure_impacts"), params.get("sitename")) or []),
            flow_balance_summary=_filter_flow_balance(processed_data.get("flow_balance_summary"), params.get("sitename")),
            intra_facility=processed_data.get("intra_facility"),
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
# 종합 현황판 API
# =============================================================================

@app.get("/dashboard/overview")
async def dashboard_overview():
    """종합 현황판 — 캐시 데이터 집계 (이상감지+교차+물수지+설비장애+데이터품질+경보)."""
    result: dict = {}

    # 1) 이상감지 캐시에서 KPI + TOP 시설 추출
    if _ANOMALY_SCAN_CACHE:
        c = _ANOMALY_SCAN_CACHE
        rows = c.get("rows", [])
        columns = c.get("columns", [])
        pd = c.get("processed_data", {})

        col_idx = {col: i for i, col in enumerate(columns)}
        vd_i = col_idx.get("verdict")
        sn_i = col_idx.get("sitename")
        ft_i = col_idx.get("facilitytype")
        di_i = col_idx.get("datainfo")
        zs_i = col_idx.get("z_score")
        sg_i = col_idx.get("site_group")
        ag_i = col_idx.get("alert_grade")
        ef_i = col_idx.get("equip_failure")
        rh_i = col_idx.get("recent_holding")

        # KPI 카운트
        verdicts = {"복합이상": 0, "이상": 0, "교차이상": 0, "주의": 0, "정상": 0}
        for r in rows:
            v = r[vd_i] if vd_i is not None else "정상"
            if v in verdicts:
                verdicts[v] += 1

        # 시설유형별 분포
        ft_dist: dict[str, int] = {}
        for r in rows:
            ft = r[ft_i] if ft_i is not None else ""
            ft_dist[ft] = ft_dist.get(ft, 0) + 1

        # TOP 이상 시설 (verdict 우선순위 정렬)
        _vd_order = {"복합이상": 0, "이상": 1, "교차이상": 2, "주의": 3, "정상": 4}
        anomaly_rows = []
        for r in rows:
            vd = r[vd_i] if vd_i is not None else "정상"
            if vd == "정상":
                continue
            anomaly_rows.append({
                "sitename": r[sn_i] if sn_i is not None else "",
                "facilitytype": r[ft_i] if ft_i is not None else "",
                "datainfo": r[di_i] if di_i is not None else "",
                "z_score": round(float(r[zs_i]), 2) if zs_i is not None and r[zs_i] is not None else 0,
                "verdict": vd,
                "site_group": r[sg_i] if sg_i is not None else "",
                "alert_grade": r[ag_i] if ag_i is not None else "",
                "equip_failure": r[ef_i] if ef_i is not None else "",
                "recent_holding": r[rh_i] if rh_i is not None else "",
            })
        anomaly_rows.sort(key=lambda x: (_vd_order.get(x["verdict"], 9), -abs(x["z_score"])))

        result["anomaly"] = {
            "total": len(rows),
            "verdicts": verdicts,
            "ft_distribution": ft_dist,
            "top_facilities": anomaly_rows[:15],
            "cross_anomaly_count": pd.get("cross_anomaly_count", 0),
        }

        # 데이터 품질
        result["data_quality"] = pd.get("data_quality_issues", [])

        # 설비 장애
        result["equipment_failures"] = pd.get("equipment_failure_impacts", [])
        result["equipment_failure_count"] = pd.get("equipment_failure_count", 0)

        # 물수지 요약
        result["flow_balance"] = pd.get("flow_balance_summary")

        # 캐시 시간
        if _ANOMALY_SCAN_CACHE_TIME:
            result["cache_time"] = _ANOMALY_SCAN_CACHE_TIME.isoformat()
    else:
        result["anomaly"] = None

    # 2) 최근 경보 (진행중 + 최근 해제 12건) — asyncio.to_thread로 블로킹 방지
    def _fetch_recent_alarms() -> dict:
        alarms_result: dict = {}
        try:
            with db_conn() as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT alarm_start_time, tagsn, alarm_status, alarm_severity,
                           alarm_category, alarm_msg, sitename, facilitytype
                    FROM tb_equipment_alarm_report
                    WHERE alarm_start_time > NOW() - INTERVAL '24 hours'
                    ORDER BY alarm_start_time DESC
                    LIMIT 20
                """)
                alarm_cols = [d[0] for d in cur.description]
                alarm_rows = cur.fetchall()
                alarms_result["recent_alarms"] = [dict(zip(alarm_cols, r)) for r in alarm_rows]
                ongoing = [r for r in alarms_result["recent_alarms"] if r.get("alarm_status") == "진행중"]
                alarms_result["ongoing_alarm_count"] = len(ongoing)
                alarms_result["ongoing_alarm_severity"] = {
                    "경고": sum(1 for a in ongoing if a.get("alarm_severity") == "경고"),
                    "주의": sum(1 for a in ongoing if a.get("alarm_severity") == "주의"),
                }
                cur.close()
        except Exception as e:
            logger.warning(f"dashboard/overview 경보 조회 실패: {e}")
            alarms_result["recent_alarms"] = []
            alarms_result["ongoing_alarm_count"] = 0
        return alarms_result

    alarm_data = await asyncio.to_thread(_fetch_recent_alarms)
    result.update(alarm_data)

    return result


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
    profiles = site_profiler.profiles if site_profiler and site_profiler.profiles else {}
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

    # DEMO_MODE: form-data의 sitename 역변환 (코드→원본)
    if DEMO_MODE and _DEMO_REVERSE_MAP:
        sitename = _demo_restore_text(sitename)
        region = _demo_restore_text(region)

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
            except Exception as e:
                logger.warning(f"이전 시설 파일 DB 삭제 실패 (file_id={old_file_id}): {e}")
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
    """사이트 설정 조회 (랜딩/DB접속/AI파라미터)"""
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

        if "landing_enabled" not in settings:
            settings["landing_enabled"] = True

        # DB 접속정보 (읽기 전용, 비밀번호 마스킹)
        settings["db"] = {
            "host": DB_HOST,
            "port": int(DB_PORT),
            "name": DB_NAME,
            "user": DB_USER,
            "password_masked": "****" if DB_PASSWORD else "(미설정)",
            "status": "connected",
        }
        try:
            test_conn = get_db_connection()
            test_conn.close()
        except Exception:
            settings["db"]["status"] = "disconnected"

        # AI 모델 파라미터
        from slm_config import (
            OLLAMA_BASE_URL, OLLAMA_TIMEOUT,
            CLASSIFIER_TEMPERATURE, get_model,
        )
        settings["ai"] = {
            "base_url": OLLAMA_BASE_URL,
            "model": get_model(),
            "num_ctx": getattr(_ai_settings, "num_ctx", 4096),
            "temperature": getattr(_ai_settings, "temperature", CLASSIFIER_TEMPERATURE),
            "timeout": getattr(_ai_settings, "timeout", OLLAMA_TIMEOUT),
            "ollama_available": ollama_client.health_check() if ollama_client else False,
        }

        return settings

    except Exception as e:
        logger.error(f"사이트 설정 조회 실패: {e}")
        return {"landing_enabled": True}
    finally:
        if conn:
            conn.close()


@app.put("/admin/site-settings")
async def update_site_settings(request: Request):
    """사이트 설정 업데이트 (랜딩/AI파라미터)"""
    conn = None
    try:
        body = await request.json()
        conn = get_db_connection()
        cur = conn.cursor()

        # 랜딩 페이지 설정
        if "landing_enabled" in body:
            use_yn = "Y" if body["landing_enabled"] else "N"
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

        # AI 파라미터 변경 (서버 재시작 없이 즉시 반영)
        ai = body.get("ai")
        if ai:
            if "num_ctx" in ai:
                val = max(1024, min(32768, int(ai["num_ctx"])))
                _ai_settings.num_ctx = val
                logger.info(f"AI num_ctx 변경: {val}")
            if "temperature" in ai:
                val = max(0.0, min(1.0, float(ai["temperature"])))
                _ai_settings.temperature = val
                logger.info(f"AI temperature 변경: {val}")
            if "timeout" in ai:
                val = max(10, min(120, int(ai["timeout"])))
                _ai_settings.timeout = val
                logger.info(f"AI timeout 변경: {val}")
            if "model" in ai:
                from slm_config import set_model
                set_model(ai["model"])
                logger.info(f"AI model 변경: {ai['model']}")

        cur.close()
        return {"status": "OK"}

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
# 경보 관리 API (프론트엔드 /alarm 페이지용)
# =============================================================================

# 장비유형별 기본 unit 매핑
_EQUIP_DEFAULT_UNIT = {"수위계": "m", "압력계": "kgf/㎠", "유량계": "㎥/h"}


def _extract_alarm_level(alarm_msg: str) -> str:
    """alarm_msg에서 HH/H/L/LL 레벨 추출"""
    if not alarm_msg:
        return "H"
    if "HH" in alarm_msg:
        return "HH"
    if "LL" in alarm_msg:
        return "LL"
    # H/L 단독: ' H ', 'H 상태', '#n H' 등
    import re
    if re.search(r"(?<![HL])\bH\b|H\s*상태", alarm_msg):
        return "H"
    if re.search(r"(?<![HL])\bL\b|L\s*상태", alarm_msg):
        return "L"
    return "H"


def _derive_related_tags(tagsn: str):
    """AMA/LEA 경보태그 → (설정값 태그, 측정값 태그) 도출"""
    setpoint_tag, measure_tag = None, None
    if "_AMA_" in tagsn:
        setpoint_tag = tagsn.replace("_AMA_", "_AMC_")
        parts = tagsn.split("_AMA_N0")
        if len(parts) == 2 and len(parts[1]) >= 1:
            pool = int(parts[1][0]) + 1
            measure_tag = f"{parts[0]}_LEI_N00{pool}"
    elif "_LEA_" in tagsn:
        setpoint_tag = tagsn.replace("_LEA_", "_LEC_")
        parts = tagsn.split("_LEA_N0")
        if len(parts) == 2 and len(parts[1]) >= 1:
            pool = int(parts[1][0]) + 1
            measure_tag = f"{parts[0]}_LEI_N00{pool}"
    return setpoint_tag, measure_tag


def _clean_tag_name(alarm_msg: str) -> str:
    """alarm_msg → 표시용 태그명 (접두사/레벨 제거)"""
    name = (alarm_msg or "").replace("경보_", "")
    for sfx in (" HH 상태", " LL 상태", " H 상태", " L 상태",
                 " HH", " LL"):
        name = name.replace(sfx, "")
    return name.strip()


@app.get("/alarm/list")
async def get_alarm_list(
    date_from: str = "",
    date_to: str = "",
    level: str = "",
    facility: str = "",
):
    """경보 이력 조회 — 실제 측정값/설정값 포함 AlarmRecord 형식"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # ── 1. 알람 레코드 조회 ──
        conditions: list[str] = []
        params: list = []
        if date_from:
            conditions.append("alarm_start_time >= %s::timestamp")
            params.append(f"{date_from} 00:00:00")
        if date_to:
            conditions.append("alarm_start_time <= %s::timestamp")
            params.append(f"{date_to} 23:59:59")
        if facility:
            conditions.append("facilitytype = %s")
            params.append(facility)
        where_sql = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        cur.execute(f"""
            SELECT alarm_start_time, tagsn, sitename, facilitytype,
                   equipmenttype, alarm_msg, alarm_status, alarm_value
            FROM tb_equipment_alarm_report
            {where_sql}
            ORDER BY alarm_start_time DESC
            LIMIT 500
        """, params)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

        # ── 2. 관련 태그 매핑 수집 ──
        sp_map: dict[str, str] = {}   # alarm_tagsn → setpoint_tagsn
        ms_map: dict[str, str] = {}   # alarm_tagsn → measure_tagsn
        for row in rows:
            sp, ms = _derive_related_tags(row["tagsn"])
            if sp:
                sp_map[row["tagsn"]] = sp
            if ms:
                ms_map[row["tagsn"]] = ms

        # ── 3. 설정값(AMC/LEC) 배치 조회 ──
        sp_vals: dict[str, float] = {}
        uniq_sp = list(set(sp_map.values()))
        if uniq_sp:
            cur.execute("""
                SELECT DISTINCT ON (tagsn) tagsn, val
                FROM tb_tag_raw_data
                WHERE tagsn = ANY(%s)
                ORDER BY tagsn, logtime DESC
            """, (uniq_sp,))
            for tagsn, val in cur.fetchall():
                if val is not None:
                    sp_vals[tagsn] = float(val)

        # ── 4. 측정값(LEI) + unit 배치 조회 ──
        ms_vals: dict[str, float] = {}
        ms_units: dict[str, str] = {}
        uniq_ms = list(set(ms_map.values()))
        if uniq_ms:
            cur.execute("""
                SELECT DISTINCT ON (tagsn) tagsn, val
                FROM tb_tag_raw_data
                WHERE tagsn = ANY(%s)
                ORDER BY tagsn, logtime DESC
            """, (uniq_ms,))
            for tagsn, val in cur.fetchall():
                if val is not None:
                    ms_vals[tagsn] = float(val)
            cur.execute("""
                SELECT tagsn, COALESCE(unit, '') FROM tb_tag_info
                WHERE tagsn = ANY(%s)
            """, (uniq_ms,))
            for tagsn, unit in cur.fetchall():
                ms_units[tagsn] = unit or ""

        cur.close()

        # ── 5. AlarmRecord 조립 ──
        results = []
        for idx, row in enumerate(rows):
            alarm_level = _extract_alarm_level(row["alarm_msg"])
            if level and alarm_level != level:
                continue

            tagsn = row["tagsn"]
            sp_tag = sp_map.get(tagsn)
            ms_tag = ms_map.get(tagsn)

            threshold = sp_vals.get(sp_tag) if sp_tag else None
            value = ms_vals.get(ms_tag) if ms_tag else None
            unit = ms_units.get(ms_tag, "") if ms_tag else ""

            # unit 없으면 장비유형 기본값
            if not unit:
                unit = _EQUIP_DEFAULT_UNIT.get(row["equipmenttype"] or "", "")

            # value 없으면 alarm_value fallback
            if value is None:
                av = row["alarm_value"]
                value = float(av) if av is not None else 0

            status = "발생중" if row["alarm_status"] == "진행중" else "복귀"

            results.append({
                "id": idx + 1,
                "occurredAt": row["alarm_start_time"].strftime("%Y-%m-%d %H:%M:%S") if row["alarm_start_time"] else "",
                "siteName": row["sitename"] or "",
                "facilityType": row["facilitytype"] or "",
                "alarmLevel": alarm_level,
                "tagName": _clean_tag_name(row["alarm_msg"]),
                "value": round(value, 2),
                "threshold": round(threshold, 2) if threshold else 0,
                "unit": unit,
                "status": status,
            })

        return results

    except psycopg2.Error as e:
        logger.error(f"알람 목록 조회 실패: {e}")
        return []
    finally:
        if conn:
            conn.close()


@app.get("/alarm/summary")
async def get_alarm_summary():
    """경보 요약 통계 (최근 30일)"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE alarm_msg ~* 'HH') AS hh_count,
                COUNT(*) FILTER (WHERE alarm_msg ~* 'LL') AS ll_count,
                COUNT(*) FILTER (WHERE alarm_status = '알람해제') AS recovered
            FROM tb_equipment_alarm_report
            WHERE alarm_start_time >= NOW() - INTERVAL '30 days'
        """)
        total, hh, ll, recovered = cur.fetchone()
        cur.close()
        # hCount = total - hh - ll (HH/LL 아닌 나머지 = H/L/기타)
        return {
            "totalCount": total or 0,
            "hhCount": (hh or 0) + (ll or 0),
            "hCount": (total or 0) - (hh or 0) - (ll or 0),
            "recoveredCount": recovered or 0,
        }
    except psycopg2.Error as e:
        logger.error(f"알람 요약 조회 실패: {e}")
        return {"totalCount": 0, "hhCount": 0, "hCount": 0, "recoveredCount": 0}
    finally:
        if conn:
            conn.close()


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
                COALESCE(meta->>'cause', diagnosed_cause) AS diagnosed_cause,
                action_plan,
                user_cause_description,
                meta,
                COALESCE(alarm_confirm_yn, 'N') AS alarm_confirm_yn,
                COALESCE(meta->>'action', countermeasure) AS countermeasure,
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


@app.get("/crisis/alarm-analysis/detail")
async def get_alarm_analysis_detail(tagsn: str, alarm_start_time: str):
    """단건 경보분석 상세 조회 (tagsn + alarm_start_time PK)"""
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
                alarm_category, alarm_msg, alarm_value,
                alarm_status, alarm_severity,
                diagnosed_cause, action_plan, user_cause_description,
                meta,
                COALESCE(alarm_confirm_yn, 'N') AS alarm_confirm_yn,
                countermeasure,
                COALESCE(off_alarm_confirm_yn, 'N') AS off_alarm_confirm_yn,
                is_false_alarm, false_alarm_notes, info_updated,
                COALESCE(tagtype, '') AS tagtype,
                stat, diagnosed_msg
            FROM tb_equipment_alarm_report
            WHERE tagsn = %s AND alarm_start_time = %s::timestamptz
        """, (tagsn, alarm_start_time))
        columns = [desc[0] for desc in cur.description]
        row = cur.fetchone()
        cur.close()
        if not row:
            return {"status": "NOT_FOUND"}
        rec = {}
        for i, col in enumerate(columns):
            val = row[i]
            if col == "meta" and val is not None:
                rec[col] = val if isinstance(val, dict) else {}
            else:
                rec[col] = val
        return rec
    except psycopg2.Error as e:
        logger.error(f"경보분석 단건 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
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
# 작업관리 CRUD API
# =============================================================================

@app.get("/crisis/tasks")
async def get_tasks(
    sitename: str = Query("", description="현장명 필터"),
    facilitytype: str = Query("", description="시설유형 필터"),
    task_category: str = Query("", description="카테고리 필터"),
    active_only: bool = Query(False, description="진행중만"),
    date_from: str = Query("", description="작업일자 시작"),
    date_to: str = Query("", description="작업일자 종료"),
    keyword: str = Query("", description="내용 키워드"),
):
    """작업 목록 조회."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        conditions = ["1=1"]
        params: list = []
        if sitename:
            conditions.append("sitename ILIKE %s")
            params.append(f"%{sitename}%")
        if facilitytype and facilitytype != "all":
            conditions.append("facilitytype = %s")
            params.append(facilitytype)
        if task_category and task_category != "all":
            conditions.append("task_category = %s")
            params.append(task_category)
        if active_only:
            conditions.append("(task_end_time IS NULL OR task_end_time > NOW())")
        if date_from:
            conditions.append("task_start_time >= %s::timestamptz")
            params.append(date_from)
        if date_to:
            conditions.append("(task_end_time IS NULL OR task_end_time <= %s::timestamptz + INTERVAL '1 day')")
            params.append(date_to)
        if keyword:
            conditions.append("task_content ILIKE %s")
            params.append(f"%{keyword}%")
        where = " AND ".join(conditions)
        cur.execute(f"""
            SELECT task_id, sitename, facilitytype, task_category,
                   TO_CHAR(task_start_time, 'YYYY-MM-DD HH24:MI:SS') AS task_start_time,
                   TO_CHAR(task_end_time, 'YYYY-MM-DD HH24:MI:SS') AS task_end_time,
                   suspend_alarm_types, task_content, alarm_report_id,
                   TO_CHAR(created_at, 'YYYY-MM-DD HH24:MI:SS') AS created_at,
                   TO_CHAR(updated_at, 'YYYY-MM-DD HH24:MI:SS') AS updated_at
            FROM tb_task_master
            WHERE {where}
            ORDER BY task_start_time DESC
            LIMIT 200
        """, params)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close()
        return {"status": "OK", "data": rows}
    except Exception as e:
        logger.error(f"작업 목록 조회 실패: {e}")
        return {"status": "error", "message": str(e), "data": []}
    finally:
        if conn:
            conn.close()


@app.post("/crisis/tasks")
async def create_task(body: dict = Body(...)):
    """작업 등록."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO tb_task_master
                (sitename, facilitytype, task_category, task_start_time, task_end_time,
                 suspend_alarm_types, task_content, alarm_report_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING task_id
        """, (
            body.get("sitename", ""),
            body.get("facilitytype", ""),
            body.get("task_category", "점검"),
            body.get("task_start_time"),
            body.get("task_end_time") or None,
            json.dumps(body.get("suspend_alarm_types", [])),
            body.get("task_content", ""),
            body.get("alarm_report_id") or None,
        ))
        task_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        return {"status": "OK", "task_id": task_id}
    except Exception as e:
        logger.error(f"작업 등록 실패: {e}")
        if conn:
            conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        if conn:
            conn.close()


@app.put("/crisis/tasks/{task_id}")
async def update_task(task_id: int, body: dict = Body(...)):
    """작업 수정."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE tb_task_master SET
                sitename = %s, facilitytype = %s, task_category = %s,
                task_start_time = %s, task_end_time = %s,
                suspend_alarm_types = %s, task_content = %s,
                updated_at = NOW()
            WHERE task_id = %s
        """, (
            body.get("sitename", ""),
            body.get("facilitytype", ""),
            body.get("task_category", "점검"),
            body.get("task_start_time"),
            body.get("task_end_time") or None,
            json.dumps(body.get("suspend_alarm_types", [])),
            body.get("task_content", ""),
            task_id,
        ))
        conn.commit()
        cur.close()
        return {"status": "OK"}
    except Exception as e:
        logger.error(f"작업 수정 실패: {e}")
        if conn:
            conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        if conn:
            conn.close()


@app.delete("/crisis/tasks/{task_id}")
async def delete_task(task_id: int):
    """작업 삭제."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM tb_task_master WHERE task_id = %s", (task_id,))
        conn.commit()
        cur.close()
        return {"status": "OK"}
    except Exception as e:
        logger.error(f"작업 삭제 실패: {e}")
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


@app.get("/tags/groups")
async def get_tag_groups():
    """태그 데이터 그룹 현황 (디버그용) — 그룹별 태그 수 + 전체/분류/미분류 통계"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # 그룹별 태그 수
        cur.execute("""
            SELECT g.group_code, g.group_name, g.parent_code, g.tagtype,
                   COUNT(m.tagsn) AS tag_count
            FROM tb_tag_data_group g
            LEFT JOIN tb_tag_group_map m ON g.group_id = m.group_id
            GROUP BY g.group_id, g.group_code, g.group_name, g.parent_code, g.tagtype
            ORDER BY g.display_order
        """)
        groups = []
        for code, name, parent, tagtype, cnt in cur.fetchall():
            groups.append({
                "group_code": code,
                "group_name": name,
                "parent_code": parent,
                "tagtype": tagtype,
                "tag_count": cnt,
            })

        # 전체 통계
        cur.execute("SELECT COUNT(*) FROM tb_tag_info")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM tb_tag_group_map")
        classified = cur.fetchone()[0]

        cur.close()
        return {
            "status": "OK",
            "total_tags": total,
            "classified": classified,
            "unclassified": total - classified,
            "groups": groups,
        }
    except Exception as e:
        logger.error(f"태그 그룹 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


# =============================================================================
# 인과관계 디버그 API
# =============================================================================

@app.get("/causal/rules")
async def get_causal_rules():
    """인과 체인 템플릿 + 시설별 매핑 현황 + 커버리지 통계."""
    # 1) 시설유형별 템플릿
    templates: dict[str, dict] = {}
    for t in CAUSAL_CHAIN_TEMPLATES:
        templates[t["facilitytype"]] = {
            "chain": t["chain"],
            "cross_facility": t.get("cross_facility"),
            "safety_interlocks": t.get("safety_interlocks", []),
            "and_conditions": t.get("and_conditions", []),
            "reverse_diagnostics": t.get("reverse_diagnostics", []),
            "propagation": t.get("propagation"),
        }

    # 2) 오버라이드 목록 (sitename, facilitytype, zone)
    override_set: set[tuple[str, str]] = set()
    for key in _CAUSAL_INDEX:
        if len(key) == 3:  # 3-tuple = zone override
            override_set.add((key[0], key[1]))
    # 2-tuple override 감지: 체인이 템플릿과 다르면 오버라이드
    for key, info in _CAUSAL_INDEX.items():
        if len(key) != 2:
            continue
        sn, ft = key
        orig_tmpl = _CAUSAL_TEMPLATE_MAP.get(ft)
        if orig_tmpl and info["template"].get("chain") != orig_tmpl.get("chain"):
            override_set.add((sn, ft))

    # 3) 시설별 상세
    facilities = []
    for key, info in _CAUSAL_INDEX.items():
        if len(key) != 2:
            continue  # zone entries 제외 (2-tuple만)
        sn, ft = key
        tmpl = info["template"]
        chain_steps = tmpl.get("chain", [])
        tag_map = info.get("tag_map", {})
        # step별 태그 커버리지
        tag_coverage: dict[str, int] = {}
        for step in chain_steps:
            gc = step["group_code"]
            tag_coverage[gc] = len(tag_map.get(gc, []))
        total_steps = len(chain_steps)
        mapped_steps = sum(1 for v in tag_coverage.values() if v > 0)
        # 구역 정보 (3-tuple 키에서 추출)
        zones = sorted(
            k[2] for k in _CAUSAL_INDEX if len(k) == 3 and k[0] == sn and k[1] == ft
        )
        # 상류/하류 시설명
        upstream = [f"{u[0]} {u[1]}" for u in info.get("upstream", [])]
        downstream = [f"{d[0]} {d[1]}" for d in info.get("downstream", [])]
        facilities.append({
            "sitename": sn,
            "facilitytype": ft,
            "zones": zones,
            "has_override": (sn, ft) in override_set,
            "tag_coverage": tag_coverage,
            "total_steps": total_steps,
            "mapped_steps": mapped_steps,
            "upstream": upstream,
            "downstream": downstream,
        })

    # 4) 요약 통계
    # 전체 시설 수 (인과 템플릿이 있는 시설유형만)
    covered_types = set(templates.keys())
    total_facilities = 0
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(DISTINCT (sitename, facilitytype))
            FROM tb_tag_info
            WHERE facilitytype IN %s AND sitename IS NOT NULL
        """, (tuple(covered_types),))
        total_facilities = cur.fetchone()[0]
        cur.close()
        conn.close()
    except Exception as e:
        logger.warning(f"인과 규칙 전체 시설 수 조회 실패, 폴백 사용: {e}")
        total_facilities = len(facilities)

    summary = {
        "total_facilities": total_facilities,
        "covered_facilities": len(facilities),
        "override_count": len(override_set),
        "full_coverage_count": sum(
            1 for f in facilities if f["mapped_steps"] == f["total_steps"] and f["total_steps"] > 0
        ),
    }

    return {
        "status": "OK",
        "templates": templates,
        "facilities": facilities,
        "summary": summary,
    }


@app.get("/causal/verify")
async def verify_causal(sitename: str, facilitytype: str):
    """특정 시설의 인과 체인 현재 상태 검증 (디버그용)."""
    key = (sitename, facilitytype)
    info = _CAUSAL_INDEX.get(key)
    if not info:
        return {"status": "ERROR", "message": f"인과 인덱스에 없음: {sitename} {facilitytype}"}

    template = info["template"]
    tag_map = info["tag_map"]

    # 각 step의 태그 존재 여부 + 최신값 조회
    steps = []
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        for step_info in template["chain"]:
            gc = step_info["group_code"]
            tagsns = tag_map.get(gc, [])
            latest_values = {}
            if tagsns:
                cur.execute("""
                    SELECT tagsn, val
                    FROM tb_tag_raw_data
                    WHERE tagsn = ANY(%s)
                    ORDER BY logtime DESC
                    LIMIT %s
                """, (tagsns, len(tagsns)))
                for tsn, val in cur.fetchall():
                    if tsn not in latest_values:
                        latest_values[tsn] = float(val) if val else None
            steps.append({
                "step": step_info["step"],
                "group_code": gc,
                "role": step_info["role"],
                "expected": step_info.get("expected"),
                "tag_count": len(tagsns),
                "latest_values": latest_values,
            })
        cur.close()
    except Exception as e:
        logger.warning(f"인과 검증 API 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()

    return {
        "status": "OK",
        "sitename": sitename,
        "facilitytype": facilitytype,
        "upstream": info.get("upstream", []),
        "downstream": info.get("downstream", []),
        "chain_steps": steps,
        "cross_facility": template.get("cross_facility"),
        "safety_interlocks": template.get("safety_interlocks", []),
        "and_conditions": template.get("and_conditions", []),
        "reverse_diagnostics": template.get("reverse_diagnostics", []),
        "propagation": template.get("propagation"),
    }


# =============================================================================
# 인과 체인 CRUD API (Phase 2)
# =============================================================================

@app.get("/causal/chain/{sitename}/{facilitytype}")
async def get_causal_chain(sitename: str, facilitytype: str):
    """시설별 인과 체인 상세 (템플릿 + 오버라이드 + 태그매핑 + 구역정보)."""
    template = _CAUSAL_TEMPLATE_MAP.get(facilitytype)
    if not template:
        return {"status": "ERROR", "message": f"인과 템플릿 없음: {facilitytype}"}

    info = _CAUSAL_INDEX.get((sitename, facilitytype))
    tag_map = info["tag_map"] if info else {}

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # 오버라이드 조회
        cur.execute("""
            SELECT zone, chain_json, cross_facility_json, source, updated_at
            FROM tb_causal_chain_override
            WHERE sitename = %s AND facilitytype = %s
            ORDER BY zone NULLS FIRST
        """, (sitename, facilitytype))
        overrides = []
        for zone, cj, cfj, src, upd in cur.fetchall():
            overrides.append({
                "zone": zone,
                "chain": cj,
                "cross_facility": cfj,
                "source": src,
                "updated_at": upd.isoformat() if upd else None,
            })

        # 구역 감지
        zones = _detect_zones(conn, sitename, facilitytype)

        cur.close()
    except Exception as e:
        logger.warning("인과 체인 조회 실패: %s", e)
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()

    # effective chain: 기본 오버라이드 → 템플릿 폴백
    effective_chain = template["chain"]
    effective_cross = template.get("cross_facility")
    default_override = next((o for o in overrides if o["zone"] is None), None)
    if default_override:
        effective_chain = default_override["chain"]
        if default_override.get("cross_facility") is not None:
            effective_cross = default_override["cross_facility"]

    return {
        "status": "OK",
        "template": {"chain": template["chain"], "cross_facility": template.get("cross_facility")},
        "effective": {"chain": effective_chain, "cross_facility": effective_cross},
        "overrides": overrides,
        "tag_map": tag_map,
        "zones": zones,
        "upstream": [list(u) for u in (info.get("upstream", []) if info else [])],
        "downstream": [list(d) for d in (info.get("downstream", []) if info else [])],
    }


class CausalChainSaveRequest(BaseModel):
    zone: Optional[str] = None
    chain: list
    cross_facility: Optional[dict] = None
    source: str = "manual"


@app.put("/causal/chain/{sitename}/{facilitytype}")
async def save_causal_chain(sitename: str, facilitytype: str, body: CausalChainSaveRequest):
    """인과 체인 오버라이드 저장 (UPSERT)."""
    zone = body.zone
    chain = body.chain
    cross = body.cross_facility
    source = body.source

    if not chain:
        return {"status": "ERROR", "message": "chain 필수"}

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO tb_causal_chain_override
                (sitename, facilitytype, zone, chain_json, cross_facility_json, source, updated_at)
            VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, now())
            ON CONFLICT (sitename, facilitytype, zone) DO UPDATE SET
                chain_json = EXCLUDED.chain_json,
                cross_facility_json = EXCLUDED.cross_facility_json,
                source = EXCLUDED.source,
                updated_at = now()
        """, (sitename, facilitytype, zone,
              json.dumps(chain), json.dumps(cross) if cross else None, source))
        conn.commit()
        cur.close()
    except Exception as e:
        logger.error("인과 체인 저장 실패: %s", e)
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()

    _rebuild_causal_index_entry(sitename, facilitytype)
    return {"status": "OK"}


@app.delete("/causal/chain/{sitename}/{facilitytype}")
async def delete_causal_chain(sitename: str, facilitytype: str, zone: Optional[str] = None):
    """인과 체인 오버라이드 삭제 (템플릿으로 복귀)."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        if zone:
            cur.execute(
                "DELETE FROM tb_causal_chain_override WHERE sitename=%s AND facilitytype=%s AND zone=%s",
                (sitename, facilitytype, zone))
        else:
            cur.execute(
                "DELETE FROM tb_causal_chain_override WHERE sitename=%s AND facilitytype=%s AND zone IS NULL",
                (sitename, facilitytype))
        conn.commit()
        cur.close()
    except Exception as e:
        logger.error("인과 체인 삭제 실패: %s", e)
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()

    _rebuild_causal_index_entry(sitename, facilitytype)
    return {"status": "OK"}


class CausalLagRequest(BaseModel):
    sitename: str
    facilitytype: str
    zone: Optional[str] = None
    days: int = 14


@app.post("/causal/estimate-lag")
async def estimate_causal_lag_api(body: CausalLagRequest):
    """교차상관 기반 인과 시간 지연 자동 추정."""
    sitename = body.sitename
    facilitytype = body.facilitytype
    zone = body.zone
    days = body.days

    info = _get_causal_info(sitename, facilitytype, zone)
    if not info:
        return {"status": "ERROR", "message": f"인과 인덱스에 없음: {sitename} {facilitytype}"}

    template = info["template"]
    tag_map = info["tag_map"]
    chain = template["chain"]

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        from_ts = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        to_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        chunks = _get_chunks_for_range(cur, from_ts, to_ts)

        from causal_estimator import estimate_lag

        estimates = []
        for i in range(len(chain) - 1):
            cause_step = chain[i]
            effect_step = chain[i + 1]
            if not effect_step.get("expected"):
                continue

            cause_tags = tag_map.get(cause_step["group_code"], [])
            effect_tags = tag_map.get(effect_step["group_code"], [])
            if not cause_tags or not effect_tags:
                estimates.append({
                    "cause_step": cause_step["step"],
                    "effect_step": effect_step["step"],
                    "cause_group": cause_step["group_code"],
                    "effect_group": effect_step["group_code"],
                    "peak_lag_min": 0, "peak_corr": 0.0,
                    "lag_min": 0, "lag_max": 0, "confidence": "low",
                })
                continue

            # 대표 태그 1개씩 사용
            all_tags = [cause_tags[0], effect_tags[0]]
            cause_vals = []
            effect_vals = []

            if chunks:
                raw_rows = _query_chunks_raw(cur, chunks, all_tags, from_ts, to_ts)
                for tsn, _, val in raw_rows:
                    v = float(val) if val is not None else 0.0
                    if tsn == cause_tags[0]:
                        cause_vals.append(v)
                    elif tsn == effect_tags[0]:
                        effect_vals.append(v)

            result = estimate_lag(cause_vals, effect_vals)
            estimates.append({
                "cause_step": cause_step["step"],
                "effect_step": effect_step["step"],
                "cause_group": cause_step["group_code"],
                "effect_group": effect_step["group_code"],
                **result,
            })

        cur.close()
        return {"status": "OK", "estimates": estimates}
    except Exception as e:
        logger.error("인과 lag 추정 실패: %s", e)
        return {"status": "ERROR", "message": str(e)}
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

        # 청크 직접 쿼리로 time_bucket 집계 (ChunkAppend 플래너 우회)
        bucket_interval = f"{bucket_mins} minutes"
        chunks = _get_chunks_for_range(cur, from_ts, to_ts)

        if chunks:
            agg = _query_chunks_agg(
                cur, chunks, req.tag_ids, from_ts, to_ts, bucket_interval)
            reagg = _reaggregate(agg)
        else:
            reagg = {}
        cur.close()

        # 후처리: 공통 times + tagsn별 values 배열
        from collections import OrderedDict
        time_set = OrderedDict()
        tag_data: dict[str, dict] = {}
        for (tagsn, bucket), (avg_val, _, _, _) in sorted(
            reagg.items(), key=lambda x: (x[0][1], x[0][0]),
        ):
            ts = bucket.strftime("%Y-%m-%d %H:%M") if hasattr(bucket, "strftime") else str(bucket)[:16]
            time_set[ts] = True
            if tagsn not in tag_data:
                tag_data[tagsn] = {}
            # 디지털 태그 → 0/1 반올림
            if avg_val is not None:
                v = round(avg_val) if tagsn in digital_tags else round(avg_val, 4)
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
# GIS 스파크라인 트렌드
# =============================================================================

@app.get("/trend/facility-sparkline")
async def get_facility_sparkline(sitename: str, facilitytype: str, hours: int = 24):
    """시설별 주요 태그 24h 스파크라인 데이터 (GIS 상세 패널용)."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # 주요 아날로그 태그 최대 6개 (수위/유량순시/적산/압력 우선)
        cur.execute("""
            SELECT tagsn, datainfo FROM tb_tag_info
            WHERE sitename = %s AND facilitytype = %s
              AND tagtype = 'Analog Input'
              AND datainfo !~* 'HH|LL|알람|SET|상태'
            ORDER BY
                CASE WHEN datainfo ~* '수위' THEN 1
                     WHEN datainfo ~* '유량' AND datainfo ~* '순시' THEN 2
                     WHEN datainfo ~* '적산' THEN 3
                     WHEN datainfo ~* '유량' THEN 4
                     WHEN datainfo ~* '압력' THEN 5
                     ELSE 6 END,
                tagsn
            LIMIT 6
        """, (sitename, facilitytype))
        tags = cur.fetchall()
        if not tags:
            cur.close()
            return []

        tagsn_list = [t[0] for t in tags]
        tag_info = {t[0]: t[1] for t in tags}

        # 시계열 조회 (5분 간격 다운샘플링)
        cur.execute("""
            SELECT tagsn,
                   time_bucket('5 minutes', logtime) AS bucket,
                   AVG(val) AS val
            FROM tb_tag_raw_data
            WHERE tagsn = ANY(%s)
              AND logtime >= NOW() - INTERVAL '%s hours'
            GROUP BY tagsn, bucket
            ORDER BY tagsn, bucket
        """, (tagsn_list, hours))
        rows = cur.fetchall()
        cur.close()

        # 태그별 그룹핑
        result = []
        tag_data: dict[str, list] = {tsn: [] for tsn in tagsn_list}
        for tsn, bucket, val in rows:
            if tsn in tag_data and val is not None:
                tag_data[tsn].append({"logtime": str(bucket), "val": round(float(val), 2)})

        for tsn in tagsn_list:
            if tag_data[tsn]:
                result.append({
                    "tagsn": tsn,
                    "datainfo": tag_info.get(tsn, tsn),
                    "data": tag_data[tsn],
                })

        return result

    except Exception as e:
        logger.error(f"스파크라인 조회 실패: {e}")
        return []
    finally:
        if conn:
            conn.close()


@app.get("/gis/facility-info")
async def get_gis_facility_info(sitename: str, facilitytype: str):
    """GIS 시설 상세 정보 — 배수지/가압장 뷰 데이터 반환."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        result: dict = {"sitename": sitename, "facilitytype": facilitytype}

        if facilitytype == "배수지":
            cur.execute("""
                SELECT v.general_overview, v.meta, i.site_photo_url
                FROM v_reservoir_info_status v
                LEFT JOIN tb_service_reservoir_info i ON i.sitename = v.sitename
                WHERE v.sitename = %s
            """, (sitename,))
            row = cur.fetchone()
            if row:
                result["general_overview"] = row[0] if row[0] else None
                result["equipment_meta"] = row[1] if row[1] else None
                result["site_photo_url"] = row[2] if row[2] else None

        elif facilitytype == "가압장":
            cur.execute("""
                SELECT v.general_overview, v.meta, i.site_photo_url
                FROM v_booster_station_info_status v
                LEFT JOIN tb_service_booster_station_info i ON i.sitename = v.sitename
                WHERE v.sitename = %s
            """, (sitename,))
            row = cur.fetchone()
            if row:
                result["general_overview"] = row[0] if row[0] else None
                result["equipment_meta"] = row[1] if row[1] else None
                result["site_photo_url"] = row[2] if row[2] else None

        # 알람: 진행중은 기간 무제한 + 해제는 최근 30일
        cur.execute("""
            SELECT TO_CHAR(alarm_start_time, 'YYYY-MM-DD HH24:MI') AS start_time,
                   tagsn,
                   alarm_severity, alarm_msg, alarm_status, alarm_category,
                   COALESCE(meta->>'cause', diagnosed_cause) AS cause,
                   COALESCE(meta->>'action', countermeasure) AS action,
                   meta->>'title' AS diag_title
            FROM tb_equipment_alarm_report
            WHERE sitename = %s
              AND (alarm_status = '진행중' OR alarm_start_time >= NOW() - INTERVAL '30 days')
            ORDER BY
              CASE WHEN alarm_status = '진행중' THEN 0 ELSE 1 END,
              alarm_start_time DESC
            LIMIT 30
        """, (sitename,))
        alarm_cols = [d[0] for d in cur.description]
        result["alarms"] = [dict(zip(alarm_cols, r)) for r in cur.fetchall()]

        cur.close()
        return result

    except Exception as e:
        logger.error(f"GIS 시설정보 조회 실패: {e}")
        return {"sitename": sitename, "facilitytype": facilitytype, "info": {}, "alarms": []}
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
            f"SELECT DISTINCT sitename FROM tb_tag_info WHERE facilitytype IN ({placeholders}) AND sitename IS NOT NULL ORDER BY sitename",
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


@app.get("/monitoring/catalogs/site-groups")
async def get_monitoring_site_groups(facilitytype: str = ""):
    """블록/소블록 등 하위 시설을 상류 시설 기준으로 그룹핑하여 반환"""
    if not facilitytype:
        return {"status": "ERROR", "message": "facilitytype 필수"}
    ftypes = [f.strip() for f in facilitytype.split(",") if f.strip()]
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        ph = ",".join(["%s"] * len(ftypes))
        # 상류 시설 기준 그룹핑 (재귀 BFS — 최상위 정수장/배수지까지 추적)
        cur.execute(f"""
            WITH RECURSIVE all_sites AS (
                SELECT DISTINCT sitename FROM tb_tag_info
                WHERE facilitytype IN ({ph}) AND sitename IS NOT NULL
            ),
            root_trace AS (
                -- 시드: 대상 시설의 직접 상류
                SELECT f.downstream_sitename AS sitename,
                       f.upstream_sitename AS ancestor,
                       f.upstream_facilitytype AS ancestor_ft,
                       1 AS depth
                FROM tb_facility_flow_map f
                WHERE f.downstream_facilitytype IN ({ph})
                UNION ALL
                -- 재귀: 상류의 상류를 추적 (최대 6단계)
                SELECT rt.sitename,
                       f.upstream_sitename,
                       f.upstream_facilitytype,
                       rt.depth + 1
                FROM root_trace rt
                JOIN tb_facility_flow_map f
                    ON f.downstream_sitename = rt.ancestor
                   AND f.downstream_facilitytype = rt.ancestor_ft
                WHERE rt.depth < 6
            ),
            best_root AS (
                -- 각 시설별 최상위 정수장 우선, 없으면 배수지, 없으면 가압장
                SELECT DISTINCT ON (sitename)
                    sitename,
                    ancestor || ' ' || ancestor_ft AS group_label
                FROM root_trace
                WHERE ancestor_ft IN ('정수장','배수지','가압장','감압시설')
                ORDER BY sitename,
                    CASE ancestor_ft
                        WHEN '정수장' THEN 1
                        WHEN '배수지' THEN 2
                        WHEN '가압장' THEN 3
                        ELSE 4
                    END,
                    depth DESC
            )
            SELECT a.sitename,
                   COALESCE(br.group_label, '미분류') AS group_label
            FROM all_sites a
            LEFT JOIN best_root br ON a.sitename = br.sitename
            ORDER BY group_label, a.sitename
        """, ftypes + ftypes)
        rows = cur.fetchall()
        # 그룹별 정리
        groups: dict[str, list[str]] = {}
        for sitename, group_label in rows:
            groups.setdefault(group_label, []).append(sitename)
        return {"status": "OK", "groups": groups}
    except Exception as e:
        logger.error(f"사이트 그룹핑 실패: {e}")
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


_LEVEL_ALARM_SETTING_RE = re.compile(r'(HH|LL|H설정|L설정|_H_|_L_|설정값)', re.IGNORECASE)


@app.get("/flow-map/realtime")
async def get_flow_map_realtime():
    """용수 흐름 실시간 모니터링 — 시설별 최신 유량/수위/압력 + 교차검증 상태."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # 1) 토폴로지
        cur.execute("""
            SELECT upstream_sitename, upstream_facilitytype,
                   downstream_sitename, downstream_facilitytype,
                   relation_type
            FROM tb_facility_flow_map
            ORDER BY upstream_facilitytype, upstream_sitename
        """)
        edges = [
            {
                "upstream_sitename": r[0], "upstream_facilitytype": r[1],
                "downstream_sitename": r[2], "downstream_facilitytype": r[3],
                "relation_type": r[4],
            }
            for r in cur.fetchall()
        ]

        # 2) 고유 시설 목록
        node_set: set[tuple[str, str]] = set()
        for e in edges:
            node_set.add((e["upstream_sitename"], e["upstream_facilitytype"]))
            node_set.add((e["downstream_sitename"], e["downstream_facilitytype"]))

        # 3) 시설별 대표 태그 최신값 조회 (FLOW_OUTLET/INSTANT, WATER_LEVEL, PRESSURE_OUTLET/DISCHARGE)
        _TARGET_GROUPS = ["FLOW_OUTLET", "FLOW_INSTANT", "FLOW_INLET", "WATER_LEVEL",
                          "PRESSURE_OUTLET", "PRESSURE_DISCHARGE", "PRESSURE_INLET"]
        cur.execute("""
            SELECT t.sitename, t.facilitytype, dg.group_code, t.tagsn, t.datainfo
            FROM tb_tag_info t
            JOIN tb_tag_group_map gm ON gm.tagsn = t.tagsn
            JOIN tb_tag_data_group dg ON dg.group_id = gm.group_id
            WHERE dg.group_code = ANY(%s)
              AND COALESCE(t.datainfo, '') NOT LIKE '%%적산%%'
              AND t.tagtype = 'Analog Input'
            ORDER BY t.sitename, dg.group_code
        """, (_TARGET_GROUPS,))
        tag_rows = cur.fetchall()

        # 시설별 그룹별 후보 태그 수집 (여러 개 유지)
        facility_tag_candidates: dict[tuple[str, str], dict[str, list[dict]]] = {}
        for sitename, ft, gc, tagsn, datainfo in tag_rows:
            key = (sitename, ft)
            if key not in facility_tag_candidates:
                facility_tag_candidates[key] = {}
            if gc not in facility_tag_candidates[key]:
                facility_tag_candidates[key][gc] = []
            facility_tag_candidates[key][gc].append(
                {"tagsn": tagsn, "tagname": datainfo or tagsn, "datainfo": datainfo}
            )

        # 4) 모든 후보 tagsn의 최신값 일괄 조회
        all_tagsns: list[str] = []
        tagsn_to_facility: dict[str, tuple[str, str, str]] = {}
        for (sn, ft), groups in facility_tag_candidates.items():
            for gc, candidates in groups.items():
                for cand in candidates:
                    tsn = cand["tagsn"]
                    all_tagsns.append(tsn)
                    tagsn_to_facility[tsn] = (sn, ft, gc)

        latest_values: dict[str, float] = {}
        if all_tagsns:
            _to = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _from = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
            chunks = _get_chunks_for_range(cur, _from, _to)
            if chunks:
                raw = _query_chunks_raw(cur, chunks, all_tagsns, _from, _to)
                for tsn, _, val in raw:
                    latest_values[tsn] = val

        # 4-a) 후보 전체 유지 (노드 구성 시 활성 태그 합산)

        # 4-b) 배수지 용수공급가능시간 + 일평균 유입/유출/사용량 조회
        reservoir_supply: dict[str, dict] = {}  # sitename -> {hours, status, reason, avg_inflow, avg_outflow, avg_usage}
        reservoir_sites = [sn for sn, ft in node_set if ft == "배수지"]
        if reservoir_sites:
            try:
                cur.execute("""
                    SELECT s.sitename, s.total_supply_time, s.supply_time_status, s.supply_time_reason,
                           v.avg_inflow, v.avg_outflow, v.avg_usage,
                           nmf.night_min_flow
                    FROM tb_service_reservoir_status s
                    LEFT JOIN v_reservoir_info_status v ON s.sitename = v.sitename
                    LEFT JOIN LATERAL (
                        SELECT round(MIN(r.val)::numeric, 2) AS night_min_flow
                        FROM tb_tag_raw_data r
                        JOIN tb_tag_info ti ON r.tagsn = ti.tagsn
                        WHERE ti.sitename = s.sitename
                          AND ti.facilitytype = '배수지'
                          AND ti.datainfo ILIKE '%%유출%%유량%%순시%%'
                          AND EXTRACT(HOUR FROM r.logtime) BETWEEN 2 AND 4
                          AND r.logtime >= now() - interval '7 days'
                    ) nmf ON true
                    WHERE s.sitename = ANY(%s)
                """, (reservoir_sites,))
                for row in cur.fetchall():
                    sn = row[0]
                    reservoir_supply[sn] = {
                        "hours": float(row[1]) if row[1] is not None else None,
                        "status": row[2] or "",
                        "reason": row[3] or "",
                        "avg_inflow": float(row[4]) if row[4] is not None else None,
                        "avg_outflow": float(row[5]) if row[5] is not None else None,
                        "avg_usage": float(row[6]) if row[6] is not None else None,
                        "night_min_flow": float(row[7]) if row[7] is not None else None,
                    }
            except Exception as e:
                logger.warning(f"배수지 공급가능시간 조회 실패: {e}")
                conn.rollback()  # 컬럼 미존재 시 롤백 후 계속

        # 4-b2) 배수지 적산 유량 (현재적산 + 금일적산)
        if reservoir_sites:
            try:
                cur.execute("""
                    WITH accum_tags AS (
                        SELECT ti.sitename, ti.tagsn, ti.datainfo,
                            CASE WHEN ti.datainfo ILIKE '%%유입%%' THEN 'inflow'
                                 WHEN ti.datainfo ILIKE '%%유출%%' THEN 'outflow'
                            END AS flow_dir
                        FROM tb_tag_info ti
                        WHERE ti.sitename = ANY(%s)
                          AND ti.facilitytype = '배수지'
                          AND ti.datainfo ILIKE '%%적산%%'
                          AND (ti.datainfo ILIKE '%%유입%%' OR ti.datainfo ILIKE '%%유출%%')
                          AND ti.tagtype = 'Analog Input'
                    ),
                    latest AS (
                        SELECT a.sitename, a.flow_dir, a.tagsn,
                            (SELECT r.val FROM tb_tag_raw_data r
                             WHERE r.tagsn = a.tagsn AND r.logtime >= now() - interval '10 minutes'
                             ORDER BY r.logtime DESC LIMIT 1) AS current_val
                        FROM accum_tags a
                    ),
                    midnight AS (
                        SELECT a.sitename, a.flow_dir, a.tagsn,
                            (SELECT r.val FROM tb_tag_raw_data r
                             WHERE r.tagsn = a.tagsn
                               AND r.logtime >= date_trunc('day', now())
                               AND r.logtime < date_trunc('day', now()) + interval '10 minutes'
                             ORDER BY r.logtime ASC LIMIT 1) AS midnight_val
                        FROM accum_tags a
                    )
                    SELECT l.sitename, l.flow_dir,
                           l.current_val,
                           CASE WHEN l.current_val IS NOT NULL AND m.midnight_val IS NOT NULL
                                THEN l.current_val - m.midnight_val ELSE NULL END AS today_accum
                    FROM latest l
                    LEFT JOIN midnight m ON l.sitename = m.sitename AND l.flow_dir = m.flow_dir AND l.tagsn = m.tagsn
                    WHERE l.current_val IS NOT NULL
                """, (reservoir_sites,))
                for row in cur.fetchall():
                    sn, flow_dir, current_val, today_accum = row
                    if sn in reservoir_supply:
                        key_curr = f"{flow_dir}_accum_current"
                        key_today = f"{flow_dir}_accum_today"
                        reservoir_supply[sn][key_curr] = float(current_val) if current_val is not None else None
                        reservoir_supply[sn][key_today] = float(today_accum) if today_accum is not None else None
            except Exception as e:
                logger.warning(f"배수지 적산유량 조회 실패: {e}")
                conn.rollback()

        # 4-c) 가압장 펌프 가동 상태 조회
        booster_pump_status: dict[str, dict] = {}  # sitename -> {running, total}
        booster_sites = [sn for sn, ft in node_set if ft == "가압장"]
        if booster_sites:
            try:
                cur.execute("""
                    SELECT ti.sitename, COUNT(*) AS total_pumps,
                           SUM(CASE WHEN r.val = 1 THEN 1 ELSE 0 END) AS running_pumps
                    FROM tb_tag_info ti
                    JOIN LATERAL (
                        SELECT val FROM tb_tag_raw_data r
                        WHERE r.tagsn = ti.tagsn
                          AND r.logtime >= now() - interval '10 minutes'
                        ORDER BY logtime DESC LIMIT 1
                    ) r ON true
                    WHERE ti.sitename = ANY(%s)
                      AND ti.facilitytype = '가압장'
                      AND ti.equipmenttype ~ '가압펌프'
                      AND (ti.datainfo ~* '운전|동작' OR ti.datadesc ~* 'RUN|동작')
                      AND NOT ti.datainfo ~* 'FAULT|FLT|STOP|정지'
                      AND ti.tagtype = 'Digital Input'
                    GROUP BY ti.sitename
                """, (booster_sites,))
                for row in cur.fetchall():
                    booster_pump_status[row[0]] = {
                        "total": int(row[1]),
                        "running": int(row[2]),
                    }
            except Exception as e:
                logger.warning(f"가압장 펌프 가동 상태 조회 실패: {e}")
                conn.rollback()

        # 5) 노드 데이터 구성 — 같은 그룹의 활성 태그 합산
        node_data: dict[str, dict] = {}
        for sn, ft in node_set:
            nid = f"{sn}__{ft}"
            cand_groups = facility_tag_candidates.get((sn, ft), {})
            metrics: dict[str, dict] = {}
            for gc, candidates in cand_groups.items():
                category = "flow" if "FLOW" in gc else ("level" if "LEVEL" in gc else "pressure")
                is_summable = category == "flow"  # 유량만 합산, 수위/압력은 대표값

                active_vals = []
                active_names = []
                for cand in candidates:
                    v = latest_values.get(cand["tagsn"])
                    if v is not None and v > 0:
                        active_vals.append(v)
                        active_names.append(cand["tagname"])
                if not active_vals:
                    first = candidates[0]
                    v0 = latest_values.get(first["tagsn"])
                    if v0 is not None:
                        active_vals = [v0]
                        active_names = [first["tagname"]]
                if not active_vals:
                    continue

                if is_summable:
                    total_val = sum(active_vals)
                    tag_label = active_names[0] if len(active_names) == 1 else f"{active_names[0]} 외 {len(active_names)-1}개 합산"
                else:
                    # 수위/압력: 첫 번째 활성 태그 대표값
                    total_val = active_vals[0]
                    tag_label = active_names[0]
                if category not in metrics or _group_priority(gc) > _group_priority(metrics[category].get("group_code", "")):
                    # 기준선(7일 동일 요일·시간대 평균) 참조
                    primary_tsn = candidates[0]["tagsn"]
                    baseline_val = _FLOW_BASELINE_CACHE.get(primary_tsn)
                    metric_entry: dict = {
                        "value": round(total_val, 2),
                        "group_code": gc,
                        "tagname": tag_label,
                        "tagsn": primary_tsn,
                        "tag_count": len(active_vals) if is_summable else 1,
                    }
                    if baseline_val and baseline_val > 0.01:
                        metric_entry["baseline_avg"] = baseline_val
                    metrics[category] = metric_entry
            # level_zones: 배수지 구역별 수위 태그 전체 (2개 이상일 때만)
            # HH/LL/H/L 설정값 태그는 제외 (실제 수위 측정값만)
            level_zones: list[dict] = []
            if ft == "배수지":
                for gc, candidates in cand_groups.items():
                    if "LEVEL" not in gc:
                        continue
                    for cand in candidates:
                        di = cand.get("datainfo", "") or ""
                        # 알람 설정값 태그 제외
                        if _LEVEL_ALARM_SETTING_RE.search(di):
                            continue
                        v = latest_values.get(cand["tagsn"])
                        if v is None:
                            continue
                        level_zones.append({
                            "value": round(v, 3),
                            "group_code": gc,
                            "tagname": cand["tagname"],
                            "tagsn": cand["tagsn"],
                            "datainfo": di,
                        })

            node_entry: dict = {
                "sitename": sn,
                "facilitytype": ft,
                "metrics": metrics,
            }
            if len(level_zones) > 1:
                # 자연 정렬: datainfo 내 숫자 기준 (수위1, 수위2, 1지, 2지 ...)
                def _zone_sort_key(z: dict) -> tuple:
                    di = z.get("datainfo", "")
                    nums = re.findall(r'\d+', di)
                    return (int(nums[0]) if nums else 999, di)
                level_zones.sort(key=_zone_sort_key)
                node_entry["level_zones"] = level_zones
            # 배수지: 용수공급가능시간 추가
            if ft == "배수지" and sn in reservoir_supply:
                node_entry["supply_time"] = reservoir_supply[sn]
            # 가압장: 펌프 가동 수 (DI 운전/동작/RUN 태그 중 val=1인 수)
            if ft == "가압장":
                pump_info = booster_pump_status.get(sn)
                if pump_info:
                    node_entry["pump_status"] = pump_info
            node_data[nid] = node_entry

        # 5-b) 진행 중인 알람 조회 — 시설별 가장 심각한 알람 등급 판정
        alarm_severity_map: dict[str, str] = {}  # nid -> "경고"|"주의"
        try:
            cur_alarm = conn.cursor()
            cur_alarm.execute("""
                SELECT t.sitename, t.facilitytype, a.alarm_severity
                FROM tb_equipment_alarm_report a
                JOIN tb_tag_info t ON t.tagsn = a.tagsn
                WHERE a.alarm_status = '진행중'
                  AND a.alarm_severity IN ('경고', '주의')
                ORDER BY t.sitename
            """)
            for r_sn, r_ft, r_sev in cur_alarm.fetchall():
                nid = f"{r_sn}__{r_ft}"
                existing = alarm_severity_map.get(nid)
                if existing != "경고":  # 경고가 최우선
                    alarm_severity_map[nid] = r_sev
            cur_alarm.close()
        except Exception as e:
            logger.debug("알람 조회 실패: %s", e)

        for nid, sev in alarm_severity_map.items():
            if nid in node_data:
                node_data[nid]["alarm_severity"] = sev

        # 5-c) 통신이상 태그 조회 — Digital Input, datainfo LIKE '%통신이상%', val=1이면 알람
        comm_error_nodes: set[str] = set()
        try:
            cur2 = conn.cursor()
            cur2.execute("""
                SELECT DISTINCT t.sitename, t.facilitytype
                FROM tb_tag_info t
                WHERE t.tagtype = 'Digital Input'
                  AND t.datainfo LIKE '%%통신이상%%'
                  AND EXISTS (
                      SELECT 1 FROM tb_tag_raw_data r
                      WHERE r.tagsn = t.tagsn
                        AND r.logtime >= NOW() - INTERVAL '30 minutes'
                        AND r.val = 1
                  )
            """)
            for row in cur2.fetchall():
                comm_error_nodes.add(f"{row[0]}__{row[1]}")
            cur2.close()
        except Exception as e:
            logger.debug("통신이상 태그 조회 실패: %s", e)

        for nid in comm_error_nodes:
            if nid in node_data:
                node_data[nid]["comm_error"] = True

        # 5-d) 설비 장애 감지 — 네트워크 장애 + DI 장애(COMM_ERROR/EQUIP_FAULT/POWER_FAULT)
        equip_failures_map: dict[str, list[dict]] = {}  # nid -> [{type, label, count}, ...]
        try:
            # A) 네트워크 장애 설비 → 시설별 집계
            cur_ef = conn.cursor()
            cur_ef.execute("""
                WITH lt AS (SELECT MAX(check_time) AS ct FROM tb_network_status)
                SELECT e.sitename, e.facilitytype, COUNT(*) AS cnt
                FROM tb_network_status ns
                JOIN lt ON ns.check_time = lt.ct
                JOIN tb_equipment_info e ON ns.equipment_id = e.equipment_id
                WHERE ns.is_alive = false
                GROUP BY e.sitename, e.facilitytype
            """)
            for sn, ft, cnt in cur_ef.fetchall():
                nid = f"{sn}__{ft}"
                equip_failures_map.setdefault(nid, []).append({
                    "type": "network_down",
                    "label": "네트워크 단절",
                    "count": cnt,
                })

            # B) DI 장애 태그 (COMM_ERROR/EQUIP_FAULT/POWER_FAULT) val=1 최근 10분
            cur_ef.execute("""
                SELECT ti.sitename, ti.facilitytype, dg.group_code, COUNT(DISTINCT gm.tagsn) AS cnt
                FROM tb_tag_group_map gm
                JOIN tb_tag_data_group dg ON gm.group_id = dg.group_id
                JOIN tb_tag_info ti ON gm.tagsn = ti.tagsn
                WHERE dg.group_code IN ('COMM_ERROR', 'EQUIP_FAULT', 'POWER_FAULT')
                  AND EXISTS (
                      SELECT 1 FROM tb_tag_raw_data r
                      WHERE r.tagsn = gm.tagsn
                        AND r.logtime >= now() - interval '10 minutes'
                        AND r.val = 1
                  )
                GROUP BY ti.sitename, ti.facilitytype, dg.group_code
            """)
            _DI_LABEL = {
                "COMM_ERROR": "통신이상",
                "EQUIP_FAULT": "설비고장",
                "POWER_FAULT": "전원이상",
            }
            _DI_TYPE = {
                "COMM_ERROR": "comm_error",
                "EQUIP_FAULT": "equip_fault",
                "POWER_FAULT": "power_fault",
            }
            for sn, ft, gc, cnt in cur_ef.fetchall():
                nid = f"{sn}__{ft}"
                equip_failures_map.setdefault(nid, []).append({
                    "type": _DI_TYPE.get(gc, "comm_error"),
                    "label": _DI_LABEL.get(gc, gc),
                    "count": cnt,
                })
            cur_ef.close()
        except Exception as e:
            logger.debug("설비 장애 조회 실패(무시): %s", e)

        for nid, failures in equip_failures_map.items():
            if nid in node_data:
                node_data[nid]["equip_failures"] = failures

        # 6) 교차검증 불일치 (캐시 참조)
        cross_mismatches_map: dict[str, list[str]] = {}  # node_id -> [check_type, ...]
        if _ANOMALY_SCAN_CACHE:
            cm_list = _ANOMALY_SCAN_CACHE.get("processed_data", {}).get("cross_facility_mismatches", [])
            for cm in (cm_list or []):
                ds_sn = cm.get("downstream_sitename", "")
                ds_ft = cm.get("downstream_facilitytype", "")
                checks = cm.get("checks", [])
                # checks는 리스트: [{"type": ..., "level": ...}, ...]
                error_checks = [c["type"] for c in checks if c.get("level") in ("error", "warn")]
                if error_checks:
                    nid = f"{ds_sn}__{ds_ft}"
                    cross_mismatches_map[nid] = error_checks

        # 6-b) 상류-하류 유량 불일치 인라인 감지
        #   (1) zero_flow: 상류 유량 > 1 m³/h + 하류 유량 ≈ 0
        #   (2) flow_disparity: 상류 대비 하류 총합 비율 < 20% (대량 유실 의심)
        _UPSTREAM_FLOW_THRESHOLD = 1.0  # m³/h
        # 상류별 하류 유량 합산용
        _us_ds_map: dict[str, list[tuple[str, float]]] = {}  # us_nid -> [(ds_nid, ds_flow), ...]
        _us_flow_map: dict[str, float] = {}  # us_nid -> us_flow
        for e in edges:
            us_nid = f"{e['upstream_sitename']}__{e['upstream_facilitytype']}"
            ds_nid = f"{e['downstream_sitename']}__{e['downstream_facilitytype']}"
            us_node = node_data.get(us_nid)
            ds_node = node_data.get(ds_nid)
            if not us_node or not ds_node:
                continue
            us_flow_val = (us_node.get("metrics", {}).get("flow") or {}).get("value")
            ds_flow_val = (ds_node.get("metrics", {}).get("flow") or {}).get("value")
            # (1) zero_flow: 하류 유량 ≈ 0
            if us_flow_val is not None and us_flow_val > _UPSTREAM_FLOW_THRESHOLD:
                if ds_flow_val is not None and ds_flow_val <= 0.01:
                    cross_mismatches_map.setdefault(ds_nid, [])
                    if "zero_flow" not in cross_mismatches_map[ds_nid]:
                        cross_mismatches_map[ds_nid].append("zero_flow")
            # 상류별 하류 합산 기록
            if us_flow_val is not None and us_flow_val > _UPSTREAM_FLOW_THRESHOLD:
                _us_flow_map[us_nid] = us_flow_val
                _us_ds_map.setdefault(us_nid, [])
                if ds_flow_val is not None:
                    _us_ds_map[us_nid].append((ds_nid, ds_flow_val))
        # (2) flow_disparity: 상류 대비 하류 총합 비율 체크
        _DISPARITY_RATIO = 0.20  # 하류 총합 < 상류 20% → 유실 의심
        for us_nid, ds_list in _us_ds_map.items():
            us_flow = _us_flow_map.get(us_nid, 0)
            if us_flow < 10:  # 상류 유량 10 미만이면 소규모 시설로 스킵
                continue
            ds_total = sum(v for _, v in ds_list)
            if ds_total < us_flow * _DISPARITY_RATIO:
                # 전체 하류 유량 합이 상류의 20% 미만 → 모든 하류에 flow_disparity 마킹
                for ds_nid, _ in ds_list:
                    cross_mismatches_map.setdefault(ds_nid, [])
                    if "flow_disparity" not in cross_mismatches_map[ds_nid]:
                        cross_mismatches_map[ds_nid].append("flow_disparity")

        # (3) 상류 활성인데 하류 유량의 최근 1시간 가동률 < 30% (순간 스파이크 감지)
        #   상류 시설유형에 따라 판정 분리:
        #   - 배수지→하류: 중력식이므로 배수지 유량>0이면 하류 반드시 유량>0 → gravity_no_flow (확정 이상)
        #   - 가압장→하류: 펌프 가동(압력>0) 확인 후 판단 → pump_no_flow (펌프 가동 중 하류 유량 이상)
        _LOW_ACTIVE_RATIO = 0.30  # 가압장/기타 하류 기준
        _GRAVITY_ACTIVE_RATIO = 0.80  # 배수지→하류: 중력식은 거의 100% 활성이어야 정상
        # 상류 시설유형 추적: ds_nid → upstream facilitytype
        _ds_us_ft: dict[str, str] = {}  # ds_nid -> upstream facilitytype
        _ds_us_pressure: dict[str, float] = {}  # ds_nid -> upstream pressure value
        for e in edges:
            us_nid = f"{e['upstream_sitename']}__{e['upstream_facilitytype']}"
            ds_nid = f"{e['downstream_sitename']}__{e['downstream_facilitytype']}"
            us_node = node_data.get(us_nid)
            if us_nid in _us_flow_map and us_node:  # 상류 유량 활성인 경우만
                _ds_us_ft[ds_nid] = e["upstream_facilitytype"]
                us_pressure = (us_node.get("metrics", {}).get("pressure") or {}).get("value")
                if us_pressure is not None:
                    _ds_us_pressure[ds_nid] = us_pressure
        # 하류 노드의 flow tagsn 수집
        _ds_flow_tags: dict[str, str] = {}  # tagsn -> ds_nid
        for us_nid, ds_list in _us_ds_map.items():
            for ds_nid, ds_flow_val in ds_list:
                ds_node = node_data.get(ds_nid)
                if not ds_node:
                    continue
                ds_flow_tag = (ds_node.get("metrics", {}).get("flow") or {}).get("tagsn")
                if ds_flow_tag:
                    _ds_flow_tags[ds_flow_tag] = ds_nid
        if _ds_flow_tags:
            try:
                cur_active = conn.cursor()
                tag_list = list(_ds_flow_tags.keys())
                cur_active.execute("""
                    SELECT tagsn,
                           COUNT(*) FILTER (WHERE val > 0.5) AS active_cnt,
                           COUNT(*) AS total_cnt
                    FROM tb_tag_raw_data
                    WHERE tagsn = ANY(%s)
                      AND logtime >= now() - interval '1 hour'
                    GROUP BY tagsn
                """, (tag_list,))
                for tsn, active_cnt, total_cnt in cur_active.fetchall():
                    if total_cnt < 3:  # 데이터 부족 스킵
                        continue
                    active_ratio = active_cnt / total_cnt
                    ds_nid = _ds_flow_tags[tsn]
                    us_ft = _ds_us_ft.get(ds_nid, "")
                    cross_mismatches_map.setdefault(ds_nid, [])
                    if us_ft == "배수지":
                        # 중력식: 배수지→하류는 거의 100% 활성이어야 정상
                        if active_ratio < _GRAVITY_ACTIVE_RATIO:
                            check_type = "gravity_no_flow"
                        else:
                            continue
                    elif us_ft == "가압장":
                        # 펌프식: 가압장 압력>0 (펌프 가동) 확인
                        if active_ratio >= _LOW_ACTIVE_RATIO:
                            continue  # 30% 이상이면 펌프 가동 패턴으로 정상
                        us_prs = _ds_us_pressure.get(ds_nid, 0)
                        if us_prs > 0.5:
                            # 펌프 가동 중(토출압력 활성)인데 하류 유량 불안정 → 이상
                            check_type = "pump_no_flow"
                        else:
                            # 펌프 미가동(압력 0) → 간헐적 동작 가능, 스킵
                            continue
                    else:
                        if active_ratio >= _LOW_ACTIVE_RATIO:
                            continue
                        check_type = "low_active"
                    if check_type not in cross_mismatches_map[ds_nid]:
                        cross_mismatches_map[ds_nid].append(check_type)
                cur_active.close()
            except Exception as e:
                logger.debug("가동률 조회 실패(무시): %s", e)

        # 7) 물 수지 불균형 (엣지별)
        edge_imbalance: dict[str, dict] = {}  # "us_nid|ds_nid" -> {pct, grade}
        if _FLOW_BALANCE_CACHE:
            for fb in _FLOW_BALANCE_CACHE:
                if fb.get("status") != "ok":
                    continue
                us_sn = fb.get("upstream_sitename", "")
                us_ft = fb.get("upstream_facilitytype", "")
                for ds in fb.get("downstream_facilities", []):
                    ds_sn = ds.get("sitename", "")
                    ds_ft = ds.get("facilitytype", "")
                    ek = f"{us_sn}__{us_ft}|{ds_sn}__{ds_ft}"
                    edge_imbalance[ek] = {
                        "imbalance_pct": round(fb.get("imbalance_pct", 0), 1),
                        "grade": fb.get("grade", "정상"),
                    }

        cur.close()
        return {
            "status": "OK",
            "edges": edges,
            "nodes": node_data,
            "cross_mismatches": {k: v for k, v in cross_mismatches_map.items() if v},
            "edge_imbalance": edge_imbalance,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"용수 흐름 실시간 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


def _group_priority(gc: str) -> int:
    """유량 그룹 우선순위 — OUTLET > INSTANT > INLET > 기타."""
    _P = {"FLOW_OUTLET": 4, "PRESSURE_DISCHARGE": 4, "FLOW_INSTANT": 3,
           "PRESSURE_OUTLET": 3, "FLOW_INLET": 2, "PRESSURE_INLET": 2,
           "WATER_LEVEL": 3}
    return _P.get(gc, 1)


@app.get("/flow-map/node-alarms")
async def get_flow_map_node_alarms(sitename: str, facilitytype: str):
    """시설별 최근 알람 목록 (진행중 + 최근 24시간 해제)"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT a.alarm_start_time, a.alarm_end_time,
                   a.alarm_severity, a.alarm_status, a.alarm_category,
                   a.alarm_msg, a.tagsn, t.datainfo
            FROM tb_equipment_alarm_report a
            JOIN tb_tag_info t ON t.tagsn = a.tagsn
            WHERE t.sitename = %s AND t.facilitytype = %s
              AND (a.alarm_status = '진행중'
                   OR a.alarm_start_time >= now() - interval '24 hours')
            ORDER BY
              CASE WHEN a.alarm_status = '진행중' THEN 0 ELSE 1 END,
              a.alarm_start_time DESC
            LIMIT 20
        """, (sitename, facilitytype))
        rows = cur.fetchall()
        cur.close()
        alarms = []
        for r in rows:
            alarms.append({
                "start_time": r[0].isoformat() if r[0] else None,
                "end_time": r[1].isoformat() if r[1] else None,
                "severity": r[2],
                "status": r[3],
                "category": r[4],
                "message": r[5],
                "tagsn": r[6],
                "datainfo": r[7],
            })
        return {"status": "OK", "alarms": alarms}
    except Exception as e:
        logger.error(f"시설 알람 조회 실패: {e}")
        return {"status": "ERROR", "alarms": []}
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
# CSV 일괄 가져오기 (Import) 엔드포인트
# =============================================================================


@app.post("/tags/import/csv")
async def import_tags_csv(file: UploadFile):
    """태그 마스터 CSV 업로드 (일괄 입력)."""
    import io
    import csv as csv_mod

    conn = None
    try:
        content = await file.read()
        text = content.decode("utf-8-sig")
        reader = csv_mod.reader(io.StringIO(text))
        header = next(reader, None)
        if not header or len(header) < 6:
            return {"status": "ERROR", "message": "CSV 헤더 부족 (최소 6컬럼)"}

        conn = get_db_connection()
        cur = conn.cursor()
        created = 0
        skipped = 0

        for row in reader:
            if len(row) < 6:
                skipped += 1
                continue
            tagsn = row[0].strip()
            if not tagsn:
                skipped += 1
                continue

            tagtype = row[1].strip() or None
            sitename = row[2].strip() or None
            facilitytype = row[3].strip() or None
            equipmenttype = row[4].strip() or None
            datainfo = row[5].strip() or None
            datadesc = row[6].strip() if len(row) > 6 and row[6].strip() else None
            unit = row[7].strip() if len(row) > 7 and row[7].strip() else None
            alarm_tag_yn = int(row[8].strip()) if len(row) > 8 and row[8].strip().isdigit() else 0

            cur.execute("""
                INSERT INTO tb_tag_info
                    (tagsn, tagtype, sitename, facilitytype, equipmenttype,
                     datainfo, datadesc, unit, alarm_tag_yn)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tagsn) DO UPDATE SET
                    tagtype = EXCLUDED.tagtype,
                    sitename = EXCLUDED.sitename,
                    facilitytype = EXCLUDED.facilitytype,
                    equipmenttype = EXCLUDED.equipmenttype,
                    datainfo = EXCLUDED.datainfo,
                    datadesc = EXCLUDED.datadesc,
                    unit = EXCLUDED.unit,
                    alarm_tag_yn = EXCLUDED.alarm_tag_yn
            """, (tagsn, tagtype, sitename, facilitytype, equipmenttype,
                  datainfo, datadesc, unit, alarm_tag_yn))
            created += 1

        conn.commit()
        cur.close()
        return {"status": "OK", "created": created, "skipped": skipped}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"태그 마스터 CSV 가져오기 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@app.post("/equipments/import/csv")
async def import_equipments_csv(file: UploadFile):
    """설비 정보 CSV 업로드 (일괄 입력)."""
    import io
    import csv as csv_mod

    STATUS_MAP = {"운영중": "operational", "점검중": "maintenance", "폐기": "decommissioned"}

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
            equipment_id = row[0].strip()
            sitename = row[1].strip()
            facilitytype = row[2].strip()
            equipmenttype = row[3].strip()
            if not equipment_id or not sitename or not facilitytype or not equipmenttype:
                skipped += 1
                continue

            status_raw = row[4].strip() if len(row) > 4 and row[4].strip() else ""
            status = STATUS_MAP.get(status_raw, "operational")

            meta_raw = {
                "model": row[5].strip() if len(row) > 5 else "",
                "manufacturer": row[6].strip() if len(row) > 6 else "",
                "role": row[10].strip() if len(row) > 10 else "",
                "note": row[11].strip() if len(row) > 11 else "",
            }
            meta = {k: v for k, v in meta_raw.items() if v}

            commissioned_at = row[7].strip() if len(row) > 7 and row[7].strip() else None
            decommissioned_at = row[8].strip() if len(row) > 8 and row[8].strip() else None
            description = row[9].strip() if len(row) > 9 and row[9].strip() else None

            cur.execute("""
                INSERT INTO tb_equipment_info
                    (equipment_id, sitename, facilitytype, equipmenttype,
                     status, commissioned_at, decommissioned_at, description, meta)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (equipment_id) DO UPDATE SET
                    sitename = EXCLUDED.sitename,
                    facilitytype = EXCLUDED.facilitytype,
                    equipmenttype = EXCLUDED.equipmenttype,
                    status = EXCLUDED.status,
                    commissioned_at = EXCLUDED.commissioned_at,
                    decommissioned_at = EXCLUDED.decommissioned_at,
                    description = EXCLUDED.description,
                    meta = EXCLUDED.meta,
                    updated_at = NOW()
            """, (equipment_id, sitename, facilitytype, equipmenttype,
                  status, commissioned_at, decommissioned_at, description,
                  json.dumps(meta)))
            created += 1

        conn.commit()
        cur.close()
        return {"status": "OK", "created": created, "skipped": skipped}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"설비 정보 CSV 가져오기 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


def _csv_cell(row: list, idx: int) -> str:
    """CSV 행에서 안전하게 셀 값을 추출 (strip 포함)."""
    return row[idx].strip() if len(row) > idx else ""


def _csv_float(row: list, idx: int):
    """CSV 셀을 float로 변환. 빈 문자열/비숫자는 None 반환."""
    v = _csv_cell(row, idx)
    if not v:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _csv_int(row: list, idx: int):
    """CSV 셀을 int로 변환. 빈 문자열/비숫자는 None 반환."""
    v = _csv_cell(row, idx)
    if not v:
        return None
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None


def _csv_bool(row: list, idx: int):
    """CSV 셀을 bool로 변환. '유'→True, '무'→False, 빈값→None."""
    v = _csv_cell(row, idx)
    if not v:
        return None
    return v.strip() in ("유", "Y", "y", "true", "True", "1", "예")


def _csv_json_array(row: list, idx: int):
    """CSV 셀을 세미콜론 구분 JSON 배열로 변환. 빈값→None."""
    v = _csv_cell(row, idx)
    if not v:
        return None
    parts = [p.strip() for p in v.split(";") if p.strip()]
    return parts if parts else None


@app.post("/reservoirs/import/csv")
async def import_reservoirs_csv(file: UploadFile):
    """배수지 정보 CSV 업로드 (일괄 입력).
    CSV 컬럼 순서 (27열):
      0:현장명, 1:설치위치, 2:운영현황, 3:시설용량(㎥), 4:급수인구(명), 5:설치연도,
      6:급수지역, 7:배수지수, 8:고수위HWL(m), 9:저수위LWL(m), 10:급수위치,
      11:공급시간(시간), 12:급수차접근, 13:급수차회전, 14:펌프필요, 15:비상급수계획,
      16:구역수, 17:1구역면적(㎡), 18:1구역높이(m), 19:2구역면적(㎡), 20:2구역높이(m),
      21:3구역면적(㎡), 22:3구역높이(m), 23:4구역면적(㎡), 24:4구역높이(m),
      25:5구역면적(㎡), 26:5구역높이(m)
    """
    import io
    import csv as csv_mod

    conn = None
    try:
        content = await file.read()
        text = content.decode("utf-8-sig")
        reader = csv_mod.reader(io.StringIO(text))
        header = next(reader, None)
        if not header or len(header) < 1:
            return {"status": "ERROR", "message": "CSV 헤더 부족 (최소 1컬럼)"}

        conn = get_db_connection()
        cur = conn.cursor()
        created = 0
        skipped = 0

        for row in reader:
            if len(row) < 1:
                skipped += 1
                continue
            sitename = _csv_cell(row, 0)
            if not sitename:
                skipped += 1
                continue

            # general_overview JSONB 필드 구성
            overview = {}
            v = _csv_cell(row, 1)
            if v:
                overview["install_location"] = v
            v = _csv_cell(row, 2)
            if v:
                overview["operating_status"] = v
            v = _csv_cell(row, 3)
            if v:
                overview["facility_capacity_m3"] = v
            v = _csv_cell(row, 4)
            if v:
                overview["supply_population"] = v
            v = _csv_cell(row, 5)
            if v:
                overview["install_year"] = v
            v = _csv_cell(row, 6)
            if v:
                overview["service_area"] = v
            v = _csv_cell(row, 7)
            if v:
                overview["reservoir_count"] = v
            fv = _csv_float(row, 8)
            if fv is not None:
                overview["hwl"] = fv
            fv = _csv_float(row, 9)
            if fv is not None:
                overview["lwl"] = fv
            v = _csv_cell(row, 10)
            if v:
                overview["supply_position"] = v
            v = _csv_cell(row, 11)
            if v:
                overview["supply_time_hours"] = v
            bv = _csv_bool(row, 12)
            if bv is not None:
                overview["water_truck_accessible"] = bv
            bv = _csv_bool(row, 13)
            if bv is not None:
                overview["water_truck_turning_possible"] = bv
            bv = _csv_bool(row, 14)
            if bv is not None:
                overview["pump_required"] = bv
            av = _csv_json_array(row, 15)
            if av is not None:
                overview["emergency_water_plan"] = av

            # 직접 컬럼 (zone_count + zone_*_area/height)
            zone_count = _csv_int(row, 16)
            zone_1_area = _csv_float(row, 17)
            zone_1_height = _csv_float(row, 18)
            zone_2_area = _csv_float(row, 19)
            zone_2_height = _csv_float(row, 20)
            zone_3_area = _csv_float(row, 21)
            zone_3_height = _csv_float(row, 22)
            zone_4_area = _csv_float(row, 23)
            zone_4_height = _csv_float(row, 24)
            zone_5_area = _csv_float(row, 25)
            zone_5_height = _csv_float(row, 26)

            cur.execute("""
                INSERT INTO tb_service_reservoir_info (
                    sitename, general_overview,
                    zone_count, zone_1_area, zone_1_height, zone_2_area, zone_2_height,
                    zone_3_area, zone_3_height, zone_4_area, zone_4_height,
                    zone_5_area, zone_5_height
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (sitename) DO UPDATE SET
                    general_overview = EXCLUDED.general_overview,
                    zone_count = EXCLUDED.zone_count,
                    zone_1_area = EXCLUDED.zone_1_area,
                    zone_1_height = EXCLUDED.zone_1_height,
                    zone_2_area = EXCLUDED.zone_2_area,
                    zone_2_height = EXCLUDED.zone_2_height,
                    zone_3_area = EXCLUDED.zone_3_area,
                    zone_3_height = EXCLUDED.zone_3_height,
                    zone_4_area = EXCLUDED.zone_4_area,
                    zone_4_height = EXCLUDED.zone_4_height,
                    zone_5_area = EXCLUDED.zone_5_area,
                    zone_5_height = EXCLUDED.zone_5_height
            """, (
                sitename, json.dumps(overview),
                zone_count, zone_1_area, zone_1_height, zone_2_area, zone_2_height,
                zone_3_area, zone_3_height, zone_4_area, zone_4_height,
                zone_5_area, zone_5_height,
            ))
            created += 1

        conn.commit()
        cur.close()
        return {"status": "OK", "created": created, "skipped": skipped}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"배수지 정보 CSV 가져오기 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@app.post("/boosters/import/csv")
async def import_boosters_csv(file: UploadFile):
    """가압장 정보 CSV 업로드 (일괄 입력).
    CSV 컬럼 순서 (18열):
      0:현장명, 1:설치위치, 2:운영현황, 3:시설용량(㎥), 4:가압장유형, 5:설치연도,
      6:펌프대수, 7:펌프양정(m), 8:펌프시공사, 9:펌프제조사, 10:펌프유형,
      11:정상운전펌프수, 12:집수정수, 13:연계배수지,
      14:1구역면적(㎡), 15:1구역높이(m), 16:2구역면적(㎡), 17:2구역높이(m)
    """
    import io
    import csv as csv_mod

    conn = None
    try:
        content = await file.read()
        text = content.decode("utf-8-sig")
        reader = csv_mod.reader(io.StringIO(text))
        header = next(reader, None)
        if not header or len(header) < 1:
            return {"status": "ERROR", "message": "CSV 헤더 부족 (최소 1컬럼)"}

        conn = get_db_connection()
        cur = conn.cursor()
        created = 0
        skipped = 0

        for row in reader:
            if len(row) < 1:
                skipped += 1
                continue
            sitename = _csv_cell(row, 0)
            if not sitename:
                skipped += 1
                continue

            # general_overview JSONB 필드 구성
            overview = {}
            v = _csv_cell(row, 1)
            if v:
                overview["install_location"] = v
            v = _csv_cell(row, 2)
            if v:
                overview["operating_status"] = v
            v = _csv_cell(row, 3)
            if v:
                overview["facility_capacity_m3"] = v
            v = _csv_cell(row, 4)
            if v:
                overview["booster_type"] = v
            v = _csv_cell(row, 5)
            if v:
                overview["install_year"] = v
            v = _csv_cell(row, 6)
            if v:
                overview["pump_count"] = v
            v = _csv_cell(row, 7)
            if v:
                overview["pump_head_m"] = v
            v = _csv_cell(row, 8)
            if v:
                overview["pump_contractor"] = v
            v = _csv_cell(row, 9)
            if v:
                overview["pump_manufacturer"] = v
            v = _csv_cell(row, 10)
            if v:
                overview["pump_type"] = v
            v = _csv_cell(row, 11)
            if v:
                overview["normal_operation_pump_count"] = v
            v = _csv_cell(row, 12)
            if v:
                overview["infiltration_well_count"] = v
            v = _csv_cell(row, 13)
            if v:
                overview["reservoir_linked"] = v

            # 직접 컬럼 (zone_*_area/height)
            zone_1_area = _csv_float(row, 14)
            zone_1_height = _csv_float(row, 15)
            zone_2_area = _csv_float(row, 16)
            zone_2_height = _csv_float(row, 17)

            cur.execute("""
                INSERT INTO tb_service_booster_info (
                    sitename, general_overview,
                    zone_1_area, zone_1_height, zone_2_area, zone_2_height
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (sitename) DO UPDATE SET
                    general_overview = EXCLUDED.general_overview,
                    zone_1_area = EXCLUDED.zone_1_area,
                    zone_1_height = EXCLUDED.zone_1_height,
                    zone_2_area = EXCLUDED.zone_2_area,
                    zone_2_height = EXCLUDED.zone_2_height
            """, (
                sitename, json.dumps(overview),
                zone_1_area, zone_1_height, zone_2_area, zone_2_height,
            ))
            created += 1

        conn.commit()
        cur.close()
        return {"status": "OK", "created": created, "skipped": skipped}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"가압장 정보 CSV 가져오기 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@app.post("/pressure-reducing/import/csv")
async def import_pressure_reducing_csv(file: UploadFile):
    """감압시설 정보 CSV 업로드 (일괄 입력).
    CSV 컬럼 순서 (9열):
      0:현장명, 1:설치위치, 2:운영현황, 3:PRV제조사, 4:PRV관경,
      5:PRV제어방식, 6:압력단위, 7:감압패턴, 8:감압기준
    """
    import io
    import csv as csv_mod

    conn = None
    try:
        content = await file.read()
        text = content.decode("utf-8-sig")
        reader = csv_mod.reader(io.StringIO(text))
        header = next(reader, None)
        if not header or len(header) < 1:
            return {"status": "ERROR", "message": "CSV 헤더 부족 (최소 1컬럼)"}

        conn = get_db_connection()
        cur = conn.cursor()
        created = 0
        skipped = 0

        for row in reader:
            if len(row) < 1:
                skipped += 1
                continue
            sitename = _csv_cell(row, 0)
            if not sitename:
                skipped += 1
                continue

            # general_overview JSONB — 모든 필드가 JSONB 내부
            overview = {}
            v = _csv_cell(row, 1)
            if v:
                overview["install_location"] = v
            v = _csv_cell(row, 2)
            if v:
                overview["operating_status"] = v
            v = _csv_cell(row, 3)
            if v:
                overview["prv_manufacturer"] = v
            v = _csv_cell(row, 4)
            if v:
                overview["prv_pipe_diameter"] = v
            v = _csv_cell(row, 5)
            if v:
                overview["prv_control_method"] = v
            v = _csv_cell(row, 6)
            if v:
                overview["pressure_unit"] = v
            v = _csv_cell(row, 7)
            if v:
                overview["pressure_reduction_pattern"] = v
            v = _csv_cell(row, 8)
            if v:
                overview["pressure_reduction_criteria"] = v

            cur.execute("""
                INSERT INTO tb_service_pressure_info (sitename, general_overview)
                VALUES (%s, %s)
                ON CONFLICT (sitename) DO UPDATE SET
                    general_overview = EXCLUDED.general_overview
            """, (sitename, json.dumps(overview)))
            created += 1

        conn.commit()
        cur.close()
        return {"status": "OK", "created": created, "skipped": skipped}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"감압시설 정보 CSV 가져오기 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@app.post("/blocks/import/csv")
async def import_blocks_csv(file: UploadFile):
    """블록 정보 CSV 업로드 (일괄 입력).
    CSV 컬럼 순서 (11열):
      0:현장명, 1:블록레벨, 2:설치위치, 3:수용가수, 4:유수율(%),
      5:관로연장(km), 6:노후관로(km), 7:대량수용가수, 8:대량수용가기준월사용량,
      9:임계압력, 10:압력단위
    """
    import io
    import csv as csv_mod

    conn = None
    try:
        content = await file.read()
        text = content.decode("utf-8-sig")
        reader = csv_mod.reader(io.StringIO(text))
        header = next(reader, None)
        if not header or len(header) < 2:
            return {"status": "ERROR", "message": "CSV 헤더 부족 (최소 2컬럼)"}

        conn = get_db_connection()
        cur = conn.cursor()
        created = 0
        skipped = 0

        for row in reader:
            if len(row) < 2:
                skipped += 1
                continue
            sitename = _csv_cell(row, 0)
            block_level = _csv_cell(row, 1)
            if not sitename or not block_level:
                skipped += 1
                continue

            # general_overview JSONB 필드 구성
            overview = {}
            v = _csv_cell(row, 2)
            if v:
                overview["install_location"] = v
            v = _csv_cell(row, 3)
            if v:
                overview["customer_count"] = v
            v = _csv_cell(row, 4)
            if v:
                overview["non_revenue_water_rate"] = v
            v = _csv_cell(row, 5)
            if v:
                overview["pipeline_total"] = v
            v = _csv_cell(row, 6)
            if v:
                overview["pipeline_old"] = v
            v = _csv_cell(row, 7)
            if v:
                overview["large_customer_count"] = v
            v = _csv_cell(row, 8)
            if v:
                overview["large_customer_base_month_usage"] = v

            # 직접 컬럼 (critical_pressure, pressure_unit)
            critical_pressure = _csv_float(row, 9)
            pressure_unit = _csv_cell(row, 10)

            cur.execute("""
                INSERT INTO tb_service_block_info (
                    sitename, block_level, general_overview,
                    critical_pressure, pressure_unit
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (sitename, block_level) DO UPDATE SET
                    general_overview = EXCLUDED.general_overview,
                    critical_pressure = EXCLUDED.critical_pressure,
                    pressure_unit = EXCLUDED.pressure_unit
            """, (
                sitename, block_level, json.dumps(overview),
                critical_pressure, pressure_unit or None,
            ))
            created += 1

        conn.commit()
        cur.close()
        return {"status": "OK", "created": created, "skipped": skipped}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"블록 정보 CSV 가져오기 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


# =============================================================================
# 설비↔태그 자동 매핑 API
# =============================================================================

@app.get("/equipments/auto-map")
async def auto_map_equipment_tags(dry_run: bool = True):
    """설비↔태그 자동 매핑 실행/미리보기.

    dry_run=true(기본): 매핑 예상 결과만 반환 (DB 변경 없음)
    dry_run=false: 실제 INSERT 수행 (ON CONFLICT DO NOTHING)
    """
    conn = None
    try:
        conn = get_db_connection()
        result = _auto_map_equipment_tags(conn, dry_run=dry_run)
        return {"status": "OK", **result}
    except Exception as e:
        logger.error("설비 태그 자동 매핑 실패: %s", e)
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


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

    uvicorn.run(app, host="0.0.0.0", port=8000, **_ssl_kwargs)
