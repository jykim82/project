"""
응답 빌더 모듈 — ai_server.py에서 분리

JSONB 파서, 템플릿 렌더러, 응답 빌더, 블록 빌더, SQL 실행기, process_sql_result.
/ask 핸들러에서 사용하는 모든 응답 조립 함수를 포함한다.

init()으로 DB 커넥션 + 글로벌 상태 참조를 주입받아 사용.
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from typing import Any, Optional

import psycopg2

from shared.timeseries import (
    get_chunks_for_range,
    query_chunks_agg,
    reaggregate,
    query_chunks_raw,
)

from sql_executor import (
    _query_recent_values, _query_flow_timeseries, _get_tag_datainfo_cache,
    _execute_night_min_flow_query, _execute_night_min_flow_stddev_query,
    _execute_tag_daily_summary_query, _execute_timeseries_query,
    _execute_hunting_check, _execute_catalog_trend_query,
    _execute_reservoir_supply_query, _execute_reservoir_supply_query_with_conn,
    _get_catalog_trend_filter, _extract_alarm_filter, _extract_alarm_level,
)
from block_builder import (
    build_hunting_result_block, build_level_detail_block,
    build_today_flow_detail_block, build_outflow_detail_block,
    build_alarm_list_block, build_latest_value_list_block,
    build_alarm_rank_block, build_alarm_cause_rank_block,
    build_pressure_detail_block, build_pressure_reference_block,
    build_abnormal_summary_detail_block, build_network_hop_detail_block,
    build_network_status_block, build_avg_usage_detail_block,
    build_upstream_fault_block, build_equipment_table,
    wrap_status_marker, _alarm_category_marker, _alarm_msg_marker,
    _format_latest_value, _ALARM_CATEGORY_SEVERITY,
    build_level_cause_block,
)
from sql_executor import _execute_level_cause_analysis

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

logger = logging.getLogger("slm")

# =============================================================================
# ai_server.py에서 주입되는 의존성
# =============================================================================
_get_db_connection = None
_causal_index = None              # dict ref → _CAUSAL_INDEX
_sitename_facility_map = None     # dict ref → SITENAME_FACILITY_MAP
_get_anomaly_scan_cache = None    # callable → (cache, time)
_iforest_manager = None           # IForestManager ref
_site_profiler = None             # SiteProfiler ref
_causal_template_map = None       # dict ref → _CAUSAL_TEMPLATE_MAP
_group_children = None            # dict ref → _GROUP_CHILDREN
_group_code_to_id = None          # dict ref → _GROUP_CODE_TO_ID
_zone_pattern = None              # re.Pattern → _ZONE_PATTERN
_fallback_gc_keywords = None      # list ref → _FALLBACK_GC_KEYWORDS
_resolve_group_codes_fn = None    # callable → _resolve_group_codes
_resolve_group_code_for_tagsn_fn = None  # callable → _resolve_group_code_for_tagsn
_diagnose_equipment_for_tags_fn = None   # callable → _diagnose_equipment_for_tags


def init(get_db_connection_fn, causal_index, sitename_facility_map,
         get_anomaly_scan_cache_fn, iforest_manager_ref,
         site_profiler_ref=None, causal_template_map=None,
         group_children=None, group_code_to_id=None,
         zone_pattern=None, fallback_gc_keywords=None,
         resolve_group_codes_fn=None, resolve_group_code_for_tagsn_fn=None,
         diagnose_equipment_for_tags_fn=None):
    """ai_server.py에서 의존성을 주입받는다."""
    global _get_db_connection, _causal_index, _sitename_facility_map
    global _get_anomaly_scan_cache, _iforest_manager
    global _site_profiler, _causal_template_map
    global _group_children, _group_code_to_id
    global _zone_pattern, _fallback_gc_keywords
    global _resolve_group_codes_fn, _resolve_group_code_for_tagsn_fn
    global _diagnose_equipment_for_tags_fn
    _get_db_connection = get_db_connection_fn
    _causal_index = causal_index
    _sitename_facility_map = sitename_facility_map
    _get_anomaly_scan_cache = get_anomaly_scan_cache_fn
    _iforest_manager = iforest_manager_ref
    _site_profiler = site_profiler_ref
    _causal_template_map = causal_template_map
    _group_children = group_children
    _group_code_to_id = group_code_to_id
    _zone_pattern = zone_pattern
    _fallback_gc_keywords = fallback_gc_keywords
    _resolve_group_codes_fn = resolve_group_codes_fn
    _resolve_group_code_for_tagsn_fn = resolve_group_code_for_tagsn_fn
    _diagnose_equipment_for_tags_fn = diagnose_equipment_for_tags_fn
    # sql_executor도 동일한 DB 커넥션 + 인과 인덱스 전달
    import sql_executor as _sqe
    _sqe.init(get_db_connection_fn, causal_index)


# --- 내부 래퍼: 주입된 callable 또는 dict를 통해 ai_server.py 글로벌 접근 ---

def _resolve_group_codes(group_code: str) -> list[str]:
    """group_code가 상위 그룹이면 하위 전체를 반환, 아니면 [자신]."""
    if _resolve_group_codes_fn:
        return _resolve_group_codes_fn(group_code)
    # 폴백: _group_children dict 직접 참조
    if _group_children:
        children = _group_children.get(group_code)
        if children:
            return children
    return [group_code]


# =============================================================================
# 예외 클래스
# =============================================================================
class JsonbSchemaViolation(Exception):
    """JSONB 스키마 불일치 예외"""
    def __init__(self, message: str, path: str = ""):
        self.message = message
        self.path = path
        super().__init__(self.message)


# =============================================================================
# 상수
# =============================================================================
MAX_TABLE_ROWS = 1000

# DB 환경변수 (psycopg2 직접 연결 시 사용 — _execute_reservoir_supply_query_with_conn)
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5433")
DB_NAME = os.environ.get("DB_NAME", "slm")
DB_USER = os.environ.get("DB_USER", "slm_dev")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")


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

    # detail 렌더링 (AFTER: icon/pill 확장 필드 preservation)
    if "detail" in template:
        detail_lines = []
        for line in template["detail"]:
            if isinstance(line, dict) and ("icon" in line or "pill" in line):
                # AFTER 확장 필드 있는 dict: text 렌더 후 icon/pill도 같이 보존
                rendered_text = _render_text(line.get("text", ""), data)
                if rendered_text is None:
                    continue
                item = {"prefix": line.get("prefix", ""), "text": rendered_text}
                if "icon" in line:
                    item["icon"] = line["icon"]
                if "pill" in line and isinstance(line["pill"], dict):
                    pill_text = _render_text(line["pill"].get("text", ""), data)
                    pill_tone = _render_text(line["pill"].get("tone", "neutral"), data) or "neutral"
                    if pill_text is not None:
                        item["pill"] = {"text": str(pill_text), "tone": str(pill_tone)}
                detail_lines.append(item)
            else:
                rendered = render_template_line(line, data)
                if rendered:
                    detail_lines.append(rendered)
        if detail_lines:
            result["detail"] = detail_lines

    # kpis 렌더링 (AFTER 컨셉 상단 KPI 스트립)
    # template["kpis"] = [{"label": "공급가능", "value": "{total_supply_time}", "unit": "h", "tone": "warn"}, ...]
    if "kpis" in template:
        rendered_kpis = []
        for kpi in template["kpis"]:
            if not isinstance(kpi, dict):
                continue
            value_raw = kpi.get("value", "")
            value_rendered = _render_text(value_raw, data) if isinstance(value_raw, str) else value_raw
            # value 렌더 실패(placeholder 누락)면 해당 KPI는 건너뜀
            if value_rendered is None:
                continue
            rendered_kpis.append({
                "label": kpi.get("label", ""),
                "value": str(value_rendered),
                "unit": kpi.get("unit", "") or "",
                "tone": kpi.get("tone", "neutral"),
                "pill": bool(kpi.get("pill", False)),
            })
        if rendered_kpis:
            result["kpis"] = rendered_kpis

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

    # meta 렌더링 (AFTER: 푸터 신뢰도 · 응답시간)
    # 정적 값 (confidence: 92) 또는 placeholder 모두 허용.
    if "meta" in template and isinstance(template["meta"], dict):
        meta_raw = template["meta"]
        meta_out: dict = {}
        if "confidence" in meta_raw:
            cf = meta_raw["confidence"]
            if isinstance(cf, (int, float)):
                meta_out["confidence"] = float(cf)
            elif isinstance(cf, str):
                cf_rendered = _render_text(cf, data)
                try:
                    if cf_rendered is not None:
                        meta_out["confidence"] = float(cf_rendered)
                except (TypeError, ValueError):
                    pass
        if "response_time_ms" in meta_raw:
            rt = meta_raw["response_time_ms"]
            if isinstance(rt, (int, float)):
                meta_out["response_time_ms"] = float(rt)
        if meta_out:
            result["meta"] = meta_out

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
        for key, tag_map in _causal_index.items():
            if len(key) == 2:
                tm = tag_map.get("tag_map", {})
                for code in resolved:
                    gc_tagsns.update(tm.get(code, []))
        # _causal_index에 없는 태그를 위해 group_map 직접 조회
        if not gc_tagsns:
            try:
                conn = _get_db_connection()
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
    if kwargs.get("comparison"):
        response["comparison"] = kwargs["comparison"]
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
    if kwargs.get("equipment_diagnosis"):
        response["equipment_diagnosis"] = kwargs["equipment_diagnosis"]
    # IForest ML 필드 (ANOMALY_SCAN_ALL, ANOMALY_FACILITY_DETAIL)
    for _ml_key in ("ml_model_count", "ml_anomaly_count", "ml_agree_count",
                    "ml_tier1_count", "ml_tier2_count", "ml_tier", "ml_anomaly_score",
                    "ml_features_used"):
        if kwargs.get(_ml_key) is not None:
            response[_ml_key] = kwargs[_ml_key]
    return response


def _extract_stddev_stats(data_row: dict) -> Optional[dict]:
    """stats_report JSONB에서 표준편차 분석 통계를 구조화된 dict로 추출한다."""
    stats = data_row.get("stats_report")
    if not isinstance(stats, list) or len(stats) < 4:
        return None

    result = {
        "unit": data_row.get("unit") or "",
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
        conn = _get_db_connection()
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
    # 상위 장비 통신이상 원인 분석
    # -------------------------------------------------
    if intent == "NETWORK_UPSTREAM_FAULT_ANALYSIS":
        data["upstream_fault_block"] = _EXPAND_MARKER
        data["_detail_blocks"]["upstream_fault_block"] = build_upstream_fault_block(rows, columns)

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
    # 배수지 수위 변동 원인 분석
    # -------------------------------------------------
    if intent == "RESERVOIR_LEVEL_CAUSE_ANALYSIS":
        data["cause_detail_block"] = _EXPAND_MARKER
        data["_detail_blocks"]["cause_detail_block"] = build_level_cause_block(rows, columns)
        if rows:
            rd = dict(zip(columns, rows[0]))
            data["direction_label"] = rd.get("direction_label", "변동")
            sev = rd.get("severity", "info")
            data["severity_marker"] = {
                "critical": "<<error:긴급>>",
                "warning": "<<warn:주의>>",
                "info": "<<ok:정상>>",
            }.get(sev, "")

    # -------------------------------------------------
    # 이상 스캔: Z-Score + Isolation Forest 기반 전체 스캔
    # -------------------------------------------------
    if intent == "ANOMALY_SCAN_ALL":
        _profiles = _site_profiler.profiles if _site_profiler and _site_profiler.profiles else None
        counts = count_anomaly_levels(rows, columns)
        data["total_tag_count"] = len(rows)
        data["error_count"] = counts["이상"]
        data["warn_count"] = counts["주의"]
        data["ok_count"] = counts["정상"]
        data["comm_error_sites"] = count_comm_error_sites(rows, columns)

        # Isolation Forest ML 보강 (Tier-1 시설 다변량 + Tier-2 태그 단변량)
        data["ml_model_count"] = 0
        data["ml_anomaly_count"] = 0
        data["ml_agree_count"] = 0
        data["ml_tier1_count"] = 0
        data["ml_tier2_count"] = 0
        try:
            if_result = _iforest_manager.predict_for_rows(rows, columns)
            if if_result:
                data["ml_model_count"]  = _iforest_manager.model_count
                data["ml_anomaly_count"] = if_result.get("if_anomaly_count", 0)
                data["ml_agree_count"]   = if_result.get("z_and_if_agree", 0)
                data["ml_tier1_count"]   = if_result.get("tier1_count", 0)
                data["ml_tier2_count"]   = if_result.get("tier2_count", 0)
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
        if _causal_index:
            data["causal_index_count"] = len(_causal_index)
            data["causal_template_types"] = list(_causal_template_map.keys()) if _causal_template_map else []

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
        _profiles = _site_profiler.profiles if _site_profiler and _site_profiler.profiles else None
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

        # Isolation Forest ML 보강 (Tier-1 시설 다변량 우선)
        data["ml_anomaly_count"] = 0
        data["ml_tier"] = 0
        try:
            # Tier-1: 시설 단위 다변량 예측 시도
            col_map_tmp = {c: i for i, c in enumerate(columns)}
            di_idx_tmp  = col_map_tmp.get("datainfo")
            if _site and _ft and di_idx_tmp is not None:
                sensor_vals: dict[str, float] = {}
                from anomaly_iforest import _datainfo_to_group
                for row in rows:
                    di  = row[di_idx_tmp] or ""
                    val = float(row[col_map_tmp["current_val"]] or 0)
                    gc  = _datainfo_to_group(di)
                    if gc:
                        sensor_vals[gc] = max(sensor_vals.get(gc, 0.0), val)
                fm_result = _iforest_manager.predict_facility(_site, _ft, sensor_vals)
                if fm_result is not None:
                    data["ml_anomaly_count"] = 1 if fm_result["is_anomaly"] else 0
                    data["ml_tier"] = 1
                    data["ml_anomaly_score"] = round(fm_result["anomaly_score"], 4)
                    data["ml_features_used"] = fm_result.get("features_used", [])

            # Tier-1 모델 없으면 Tier-2 fallback
            if data["ml_tier"] == 0:
                if_result = _iforest_manager.predict_for_rows(rows, columns)
                if if_result:
                    data["ml_anomaly_count"] = if_result.get("if_anomaly_count", 0)
                    data["ml_tier"] = 2
        except Exception as e:
            logger.warning(f"IForest enrichment 실패: {e}")

        # C그룹 패턴 분석 (수위 태그 대상)
        pattern_result = None
        if _group == "C" and _profiles:
            try:
                col_map = {c: i for i, c in enumerate(columns)}
                tagsn_idx = col_map.get("tagsn")
                if tagsn_idx is not None:
                    conn = _get_db_connection()
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

        # ── 인과 분석 병렬 실행 (ThreadPoolExecutor) ──────────────────────────
        # 순서: [Phase1 병렬] causal + cross + intra + cross_intra
        #       → [Phase2 병렬] propagation_forward + propagation_backward
        # Ollama SLM 해석은 속도 영향이 크므로 제거 (결과는 causal_result에 충분)
        _a_gc = None
        _a_dir = None
        _a_tagsn = ""
        _a_datainfo = ""
        _c_tagsn_idx = None
        _c_z_idx = None
        causal_result = None
        cross_facility_result = None
        intra_facility_result = None
        cross_intra_result: list[dict] = []
        propagation_trace = None

        # tagsn/z_score 컬럼 인덱스는 인과 인덱스와 독립적으로 설정
        # (설비 건강 진단 등 후속 로직이 causal_index 유무와 무관하게 사용)
        if rows:
            _col_map_global = {c: i for i, c in enumerate(columns)}
            _c_tagsn_idx = _col_map_global.get("tagsn")
            _c_z_idx = _col_map_global.get("z_score")

        # 인과관계 그룹코드 탐색 (선행 — DB 불필요, 빠름)
        if _causal_index and rows:
            col_map_c = {c: i for i, c in enumerate(columns)}
            _c_datainfo_idx = col_map_c.get("datainfo")
            if _c_tagsn_idx is not None and _c_z_idx is not None:
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
                for _zv, _anomaly_row in _anomaly_rows:
                    _a_tagsn = str(_anomaly_row[_c_tagsn_idx] or "")
                    _a_datainfo = str(_anomaly_row[_c_datainfo_idx] or "")
                    _a_dir = "RISE" if _zv > 0 else "FALL"
                    _a_gc = _resolve_group_code_for_tagsn_fn(_site, _ft, _a_tagsn, _a_datainfo) if _resolve_group_code_for_tagsn_fn else None
                    if _a_gc:
                        break

        # Phase 1: 독립 분석 병렬 실행
        import concurrent.futures as _cf
        _t_phase1 = time.time()
        _a_zone = None
        if _a_datainfo:
            _zm = _zone_pattern.search(_a_datainfo) if _zone_pattern else None
            if _zm:
                _a_zone = f"{_zm.group(1)}지"
        _a_zone_intra = _a_zone

        def _run_causal():
            if not (_causal_index and _a_gc):
                return None
            try:
                from anomaly_detector import verify_causal_context
                return verify_causal_context(
                    _query_recent_values, _site, _ft,
                    _a_gc, _a_dir, _causal_index, zone=_a_zone,
                )
            except Exception as _e:
                logger.warning("인과관계 검증 실패 (무시): %s", _e)
                return None

        def _run_cross():
            if not _causal_index:
                return None
            try:
                from anomaly_detector import cross_facility_check_single
                return cross_facility_check_single(
                    _query_recent_values, _site, _ft, _causal_index,
                )
            except Exception as _e:
                logger.warning("교차 검증 실패 (무시): %s", _e)
                return None

        def _run_intra():
            if not _causal_index:
                return None
            try:
                return verify_intra_facility(
                    _query_recent_values, _site, _ft, _causal_index, zone=_a_zone_intra,
                )
            except Exception as _e:
                logger.warning("시설 내부 인과 검증 실패 (무시): %s", _e)
                return None

        def _run_cross_intra():
            try:
                return verify_cross_facility_intra_rules(
                    _query_recent_values, _site, _ft, _causal_index,
                )
            except Exception as _e:
                logger.warning("시설간 교차 인과 검증 실패 (무시): %s", _e)
                return []

        with _cf.ThreadPoolExecutor(max_workers=4) as _pool:
            _f_causal = _pool.submit(_run_causal)
            _f_cross = _pool.submit(_run_cross)
            _f_intra = _pool.submit(_run_intra)
            _f_cross_intra = _pool.submit(_run_cross_intra)
            causal_result = _f_causal.result()
            cross_facility_result = _f_cross.result()
            intra_facility_result = _f_intra.result()
            cross_intra_result = _f_cross_intra.result() or []

        logger.info("⏱ Phase1 병렬 분석 %dms", int((time.time() - _t_phase1) * 1000))

        if causal_result:
            data["causal_context"] = causal_result
        if cross_facility_result and cross_facility_result.get("has_mismatch"):
            data["cross_facility"] = cross_facility_result
        if intra_facility_result:
            data["intra_facility"] = intra_facility_result
        if cross_intra_result:
            data["cross_intra_facility"] = cross_intra_result

        # Phase 2: 전파 추적 병렬 실행 (causal/cross 결과 의존)
        if _causal_index and (causal_result or cross_facility_result):
            try:
                from anomaly_detector import (
                    trace_propagation_forward,
                    trace_upstream_root_cause,
                )
                _target_key = (_site, _ft)
                _t_phase2 = time.time()
                with _cf.ThreadPoolExecutor(max_workers=2) as _pool2:
                    _f_fwd = _pool2.submit(trace_propagation_forward, _query_recent_values, _target_key, _causal_index)
                    _f_bwd = _pool2.submit(trace_upstream_root_cause, _query_recent_values, _target_key, _causal_index)
                    _fwd = _f_fwd.result()
                    _bwd = _f_bwd.result()
                logger.info("⏱ Phase2 전파추적 %dms", int((time.time() - _t_phase2) * 1000))
                if _fwd.get("hops") or _bwd.get("root_cause") or _bwd.get("hops"):
                    propagation_trace = {"forward": _fwd, "backward": _bwd}
                    data["propagation_trace"] = propagation_trace
            except Exception as e:
                logger.warning("전파 추적 실패 (무시): %s", e)

        # 설비 건강 진단 (Phase 3: 이상 태그 → 연결 설비 역추적)
        equip_diagnosis = None
        try:
            _anomaly_tagsns = []
            if _c_tagsn_idx is not None and _c_z_idx is not None:
                _w_th = GROUP_THRESHOLDS.get(_group, GROUP_THRESHOLDS["B"])["warn"]
                for _r in rows:
                    try:
                        _zv2 = float(_r[_c_z_idx]) if _r[_c_z_idx] else 0
                    except (ValueError, TypeError):
                        _zv2 = 0
                    if abs(_zv2) >= _w_th:
                        _anomaly_tagsns.append(str(_r[_c_tagsn_idx] or ""))
            if _anomaly_tagsns:
                equip_diagnosis = _diagnose_equipment_for_tags_fn(
                    _anomaly_tagsns, _site, _ft,
                ) if _diagnose_equipment_for_tags_fn else None
                if equip_diagnosis:
                    data["equipment_diagnosis"] = equip_diagnosis
                    logger.info(f"설비 건강 진단 완료: {len(equip_diagnosis)}개 설비 ({_site} {_ft})")
        except Exception as e:
            logger.warning(f"설비 건강 진단 실패 (무시): {e}")

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
                cross_intra_result=cross_intra_result,
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
                    data["nmf_status"] = ""  # 레거시 호환 (빈 문자열)
                    data["nmf_pill_text"] = f"월평균 대비 {dev_pct:+.0f}%"
                    data["nmf_pill_tone"] = "critical"
                elif abs(dev_pct) >= 20:
                    data["nmf_status"] = ""
                    data["nmf_pill_text"] = f"월평균 대비 {dev_pct:+.0f}%"
                    data["nmf_pill_tone"] = "warn"
                else:
                    data["nmf_status"] = ""
                    data["nmf_pill_text"] = "정상 범위"
                    data["nmf_pill_tone"] = "ok"
            else:
                data["nmf_status"] = ""
                data["nmf_pill_text"] = ""
                data["nmf_pill_tone"] = "neutral"
        except (ValueError, TypeError):
            data["nmf_status"] = ""
            data["nmf_pill_text"] = ""
            data["nmf_pill_tone"] = "neutral"

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

