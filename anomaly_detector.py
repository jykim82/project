"""
anomaly_detector.py
Z-Score + 방향전환횟수 기반 이상 감지 모듈 + CUSUM 누수추정

설계 원칙:
- Z-Score 숫자를 사용자에게 노출하지 않는다
- "평소 대비 ±N%" 형식으로만 표현한다
- 시맨틱 마커(<<ok/warn/error:text>>)로 상태를 색상 표현한다
"""

import logging
from collections import defaultdict
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# ── Z-Score 판정 임계값 (기본 = B그룹) ──────────────────────
Z_THRESHOLD_ERROR = 3.0   # |Z| >= 3.0 → 이상
Z_THRESHOLD_WARN = 2.0    # |Z| >= 2.0 → 주의

# ── 그룹별 Z-Score 임계값 ──────────────────────────────────
GROUP_THRESHOLDS = {
    "A": {"warn": 3.0, "error": 4.0},  # 고부하-불안정: 완화
    "B": {"warn": 2.0, "error": 3.0},  # 고부하-안정: 기본
    "C": {"warn": 1.5, "error": 2.0},  # 저부하-불안정: 강화
    "D": {"warn": 2.0, "error": 3.0},  # 저부하-안정: 기본
}

# ── 방향전환 판정 임계값 (5분 윈도우, ~16 data points) ──────
DIR_THRESHOLD_ERROR = 4   # >= 4회 → 이상
DIR_THRESHOLD_WARN = 2    # >= 2회 → 주의

# ── 복합 판정 매트릭스 ───────────────────────────────────────
_COMBINED_MATRIX = {
    ("정상", "정상"): "정상",
    ("주의", "정상"): "값주의",
    ("이상", "정상"): "값이상",
    ("정상", "주의"): "패턴주의",
    ("정상", "이상"): "패턴이상",
    ("주의", "주의"): "복합주의",
    ("주의", "이상"): "복합이상",
    ("이상", "주의"): "복합이상",
    ("이상", "이상"): "심각",
}


# ── 판정 함수 ────────────────────────────────────────────────

def classify_z_level(z_score: float) -> str:
    """Z-Score로 이상 수준을 판정한다."""
    abs_z = abs(z_score)
    if abs_z >= Z_THRESHOLD_ERROR:
        return "이상"
    if abs_z >= Z_THRESHOLD_WARN:
        return "주의"
    return "정상"


def classify_direction_level(change_count: int) -> str:
    """방향전환 횟수로 패턴 이상 수준을 판정한다."""
    if change_count >= DIR_THRESHOLD_ERROR:
        return "이상"
    if change_count >= DIR_THRESHOLD_WARN:
        return "주의"
    return "정상"


def combined_judgment(z_level: str, dir_level: str) -> str:
    """Z-Score 판정과 방향전환 판정을 복합 판정한다."""
    return _COMBINED_MATRIX.get((z_level, dir_level), "정상")


def classify_z_level_by_group(z_score: float, group: str = "B") -> str:
    """그룹별 임계값을 적용하여 Z-Score 이상 수준을 판정한다."""
    thresholds = GROUP_THRESHOLDS.get(group, GROUP_THRESHOLDS["B"])
    abs_z = abs(z_score)
    if abs_z >= thresholds["error"]:
        return "이상"
    if abs_z >= thresholds["warn"]:
        return "주의"
    return "정상"


# ── 경보 등급 분류 ─────────────────────────────────────────

def classify_alert_grade(
    site_group: str,
    z_level: str,
    dir_level: str = "정상",
    pattern_result: Optional[dict] = None,
    info_count_7d: int = 0,
) -> Optional[str]:
    """
    3단계 경보 등급을 결정한다.

    Returns:
        'critical' | 'warning' | 'info' | None
    """
    # Critical: C그룹 + 위험 패턴 (밸브 미작동/고착/공급중단)
    if site_group == "C" and pattern_result:
        if pattern_result.get("hh_no_reversal"):
            return "critical"
        if pattern_result.get("ll_no_recovery"):
            return "critical"
        if pattern_result.get("descending_hh_retouch"):
            return "critical"

    # Warning: 그룹 임계값 초과 ("이상"/"심각"/"복합이상" 등)
    if z_level in ("이상", "심각", "값이상", "패턴이상", "복합이상"):
        return "warning"

    # Warning: Info 누적 격상 (7일 내 3건 이상 + 주의)
    if info_count_7d >= 3 and z_level in ("주의", "값주의", "패턴주의", "복합주의"):
        return "warning"

    # Info: 주의 계열
    if z_level in ("주의", "값주의", "패턴주의", "복합주의"):
        return "info"

    return None


# ── 교차검증 종합 판정 ─────────────────────────────────────

_CROSS_SEVERITY = {"교차이상": 2, "교차주의": 1}

# (z_status, cross_status) → verdict
_VERDICT_MATRIX = {
    ("이상", "교차이상"): "복합이상",
    ("이상", "교차주의"): "이상",
    ("이상", None): "이상",
    ("주의", "교차이상"): "복합이상",
    ("주의", "교차주의"): "주의",
    ("주의", None): "주의",
    ("정상", "교차이상"): "교차이상",
    ("정상", "교차주의"): "교차주의",
    ("정상", None): "정상",
}


def map_cross_mismatches_to_facilities(
    mismatches: list[dict],
) -> dict[tuple[str, str], str]:
    """엣지 레벨 교차검증 불일치 → per-(sitename, facilitytype) cross_status.

    하류 facility: 교차이상 (유량 수신 실패)
    상류 facility: 교차주의 (자체는 정상이나 하류 문제)
    동일 시설이 여러 엣지에 관련 시 worst severity 적용.
    """
    facility_map: dict[tuple[str, str], str] = {}

    for m in mismatches:
        us_key = (m["upstream_sitename"], m["upstream_facilitytype"])
        ds_key = (m["downstream_sitename"], m["downstream_facilitytype"])

        # 하류: 교차이상, 상류: 교차주의
        ds_status = "교차이상"
        us_status = "교차주의"

        for key, status in [(us_key, us_status), (ds_key, ds_status)]:
            existing = facility_map.get(key)
            if not existing or _CROSS_SEVERITY.get(status, 0) > _CROSS_SEVERITY.get(existing, 0):
                facility_map[key] = status

    return facility_map


def compute_verdict(z_status: str, cross_status: Optional[str]) -> str:
    """Z-Score 상태 + cross_status → 종합 판정."""
    return _VERDICT_MATRIX.get((z_status, cross_status), z_status)


def enrich_rows_with_cross_verdict(
    rows: list,
    columns: list,
    cross_mismatches: Optional[list[dict]],
    site_profiles: Optional[dict] = None,
) -> None:
    """per-row cross_status + verdict 컬럼 추가 (in-place).

    site_group/alert_grade 컬럼이 이미 추가된 이후에 호출해야 한다.
    """
    if "cross_status" in columns:
        return  # 이미 enriched

    sn_idx = columns.index("sitename") if "sitename" in columns else None
    ft_idx = columns.index("facilitytype") if "facilitytype" in columns else None
    z_idx = columns.index("z_score") if "z_score" in columns else None
    grp_idx = columns.index("site_group") if "site_group" in columns else None

    if sn_idx is None or ft_idx is None or z_idx is None:
        return

    # 엣지 → 시설 레벨 매핑
    facility_cross = map_cross_mismatches_to_facilities(cross_mismatches) if cross_mismatches else {}

    columns.extend(["cross_status", "verdict"])
    enriched = []
    for row in rows:
        sn = row[sn_idx] or ""
        ft = row[ft_idx] or ""
        try:
            z = float(row[z_idx] or 0)
        except (ValueError, TypeError):
            z = 0.0

        grp = row[grp_idx] if grp_idx is not None else "B"
        z_status = classify_z_level_by_group(z, grp or "B")

        cross_st = facility_cross.get((sn, ft))
        verdict = compute_verdict(z_status, cross_st)

        enriched.append(tuple(list(row) + [cross_st, verdict]))

    rows[:] = enriched


# ── C그룹 수위 패턴 분석 ───────────────────────────────────

def analyze_level_pattern(
    series: list[tuple[str, float]],
    hh_value: Optional[float],
    ll_value: Optional[float],
    window_minutes: int = 30,
) -> dict:
    """
    수위 시계열에서 위험 패턴을 분석한다 (C그룹 전용).

    Args:
        series: [(timestamp_str, value), ...] 시간순 정렬
        hh_value: HH 임계값 (없으면 패턴 분석 제한)
        ll_value: LL 임계값
        window_minutes: HH 터치 후 반전 대기 시간(분)

    Returns:
        {
            'trend_direction': 'rising'|'falling'|'stable',
            'reversal_count': int,
            'hh_no_reversal': bool,
            'll_no_recovery': bool,
            'descending_hh_retouch': bool,
            'has_critical_pattern': bool,
        }
    """
    result = {
        "trend_direction": "stable",
        "reversal_count": 0,
        "hh_no_reversal": False,
        "ll_no_recovery": False,
        "descending_hh_retouch": False,
        "has_critical_pattern": False,
    }

    if len(series) < 6:
        return result

    values = [v for _, v in series]

    # 이동평균 기울기 (10포인트 = 약 50분 윈도우)
    ma_window = min(10, len(values) // 2)
    if ma_window < 2:
        return result

    ma = []
    for i in range(len(values) - ma_window + 1):
        ma.append(sum(values[i:i + ma_window]) / ma_window)

    # 기울기 부호 시퀀스 → 반전 횟수
    slopes = [ma[i + 1] - ma[i] for i in range(len(ma) - 1)]
    signs = [1 if s > 0 else (-1 if s < 0 else 0) for s in slopes]
    non_zero = [s for s in signs if s != 0]

    reversal_count = 0
    for i in range(1, len(non_zero)):
        if non_zero[i] != non_zero[i - 1]:
            reversal_count += 1
    result["reversal_count"] = reversal_count

    # 최근 추세 방향
    recent_slopes = slopes[-5:] if len(slopes) >= 5 else slopes
    avg_slope = sum(recent_slopes) / len(recent_slopes) if recent_slopes else 0
    if avg_slope > 0.01:
        result["trend_direction"] = "rising"
    elif avg_slope < -0.01:
        result["trend_direction"] = "falling"

    # 5분 간격 가정 → window_minutes / 5 = 포인트 수
    window_points = max(window_minutes // 5, 3)

    # Pattern 6: HH 터치 후 반전 없음 (오버플로우 위험)
    if hh_value is not None:
        for i, (_, val) in enumerate(series):
            if val >= hh_value:
                # HH 터치 후 window_points 내에 하강(반전) 있는지
                after = values[i + 1: i + 1 + window_points]
                if after and all(v >= hh_value * 0.95 for v in after):
                    result["hh_no_reversal"] = True
                    break

    # Pattern 7: 하강 중 HH 재터치 (밸브 고착)
    if hh_value is not None and len(values) >= 10:
        was_descending = False
        for i in range(1, len(values)):
            if values[i] < values[i - 1]:
                was_descending = True
            elif was_descending and values[i] >= hh_value:
                result["descending_hh_retouch"] = True
                break
            if values[i] > values[i - 1]:
                was_descending = False

    # Pattern 8: LL 도달 후 60분 내 회복 없음 (공급 중단/누수)
    ll_window = max(60 // 5, 6)
    if ll_value is not None:
        for i, (_, val) in enumerate(series):
            if val <= ll_value:
                after = values[i + 1: i + 1 + ll_window]
                if after and all(v <= ll_value * 1.1 for v in after):
                    result["ll_no_recovery"] = True
                    break

    result["has_critical_pattern"] = (
        result["hh_no_reversal"]
        or result["ll_no_recovery"]
        or result["descending_hh_retouch"]
    )

    return result


def get_hh_ll_for_site(
    conn, sitename: str, facilitytype: str, tagsn: str,
    site_profiles: dict,
) -> tuple[Optional[float], Optional[float]]:
    """
    태그의 HH/LL 값을 해석한다.

    우선순위:
    1. monitoring catalog의 alarm_limits (상수 hh/ll)
    2. site_profiles의 P95/P05 (동적 임계값)
    """
    # 1. monitoring catalog에서 alarm_limits 조회
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT meta FROM tb_trend_catalog
            WHERE sitename = %s AND facilitytype = %s
              AND COALESCE((meta->>'monitoring')::boolean, false) = true
        """, (sitename, facilitytype))
        for (meta_row,) in cur.fetchall():
            meta = meta_row if isinstance(meta_row, dict) else {}
            for item in meta.get("items", []):
                if item.get("tagsn") == tagsn:
                    al = item.get("alarm_limits") or {}
                    hh = al.get("hh")
                    ll = al.get("ll")
                    if hh is not None or ll is not None:
                        cur.close()
                        return (
                            float(hh) if hh is not None else None,
                            float(ll) if ll is not None else None,
                        )
        cur.close()
    except Exception:
        logger.debug("alarm_limits 조회 실패 (%s/%s)", sitename, tagsn)

    # 2. P95/P05 폴백
    profile = site_profiles.get((sitename, facilitytype))
    if profile:
        return profile.get("p95_level"), profile.get("p05_level")

    return None, None


# ── 포맷팅 함수 ──────────────────────────────────────────────

def _wrap_marker(text: str, level: str) -> str:
    """레벨에 따라 시맨틱 마커를 감싼다."""
    # 영문 직접 매칭
    if level in ("error", "critical"):
        return f"<<error:{text}>>"
    if level in ("warn", "warning"):
        return f"<<warn:{text}>>"
    if level == "ok":
        return f"<<ok:{text}>>"
    # 복합 상태 키워드에서 마커 결정 (심각/이상→error, 주의→warn, 정상→ok)
    for keyword, marker in [("심각", "error"), ("이상", "error"),
                            ("주의", "warn"), ("정상", "ok")]:
        if keyword in level:
            return f"<<{marker}:{text}>>"
    return f"<<ok:{text}>>"


def format_deviation_text(deviation_pct: float, z_score: float,
                          active_pct: float = 100) -> str:
    """Z-Score를 '평소 대비 ±N%' 형식으로 변환한다."""
    direction = "+" if z_score > 0 else "-"
    text = f"평소 대비 {direction}{abs(deviation_pct):.1f}%"
    if active_pct < 80:
        text += f", 가동률 {active_pct:.0f}%"
    return text


# ── 집계 함수 ────────────────────────────────────────────────

def count_anomaly_levels(rows: list, columns: list,
                         z_col: str = "z_score") -> dict:
    """SQL 결과에서 이상 수준별 건수를 집계한다."""
    col_map = {c: i for i, c in enumerate(columns)}
    z_idx = col_map.get(z_col)
    counts = {"이상": 0, "주의": 0, "정상": 0}

    if z_idx is None:
        counts["정상"] = len(rows)
        return counts

    for row in rows:
        z = float(row[z_idx]) if row[z_idx] is not None else 0.0
        level = classify_z_level(z)
        counts[level] += 1

    return counts


# ── Detail Block 빌더 ────────────────────────────────────────

def count_comm_error_sites(rows: list, columns: list) -> int:
    """통신장애 상태인 고유 사이트 수를 집계한다."""
    col_map = {c: i for i, c in enumerate(columns)}
    if "comm_status" not in col_map:
        return 0
    cs_idx = col_map["comm_status"]
    sn_idx = col_map.get("sitename")
    error_sites = set()
    for row in rows:
        if (row[cs_idx] or "") == "통신장애" and sn_idx is not None:
            error_sites.add(row[sn_idx])
    return len(error_sites)


def build_anomaly_scan_detail_block(
    rows: list, columns: list,
    site_profiles: Optional[dict] = None,
) -> list:
    """
    ANOMALY_SCAN_ALL: SQL 결과를 이상 스캔 결과 리스트로 조립한다.
    이상/주의 항목만 표시 (정상은 생략). 통신장애 사이트는 별도 표시.
    site_profiles가 제공되면 그룹별 임계값을 적용하고 경보 등급을 표시한다.
    """
    col_map = {c: i for i, c in enumerate(columns)}
    items = []

    has_active = "active_pct" in col_map
    has_comm = "comm_status" in col_map
    use_profiles = site_profiles is not None and len(site_profiles) > 0

    # 경보 등급 시맨틱 마커
    _grade_marker = {
        "critical": "<<error:[긴급]>>",
        "warning": "<<warn:[주의]>>",
        "info": "<<ok:[참고]>>",
    }

    for row in rows:
        z_score = float(row[col_map["z_score"]] or 0)
        sitename = row[col_map["sitename"]] or ""
        facilitytype = row[col_map["facilitytype"]] or ""

        # 그룹별 임계값 적용
        if use_profiles:
            profile = site_profiles.get((sitename, facilitytype))
            group = profile.get("site_group", "B") if profile else "B"
            level = classify_z_level_by_group(z_score, group)
        else:
            group = "B"
            level = classify_z_level(z_score)

        if level == "정상":
            continue

        datainfo = row[col_map["datainfo"]] or ""
        deviation_pct = float(row[col_map["deviation_pct"]] or 0)
        active_pct = float(row[col_map["active_pct"]] or 100) if has_active else 100
        comm_status = (row[col_map["comm_status"]] or "정상") if has_comm else "정상"

        dev_text = format_deviation_text(deviation_pct, z_score, active_pct)
        marked_level = _wrap_marker(level, level)
        comm_tag = " <<error:[통신장애]>>" if comm_status == "통신장애" else ""

        # 경보 등급 결정
        grade_tag = ""
        if use_profiles:
            info_count = (profile or {}).get("info_count_7d", 0)
            grade = classify_alert_grade(group, level, "정상", None, info_count)
            if grade:
                grade_tag = f" {_grade_marker[grade]}"

        text = f"{sitename} {facilitytype} {datainfo}: {marked_level} ({dev_text}){comm_tag}{grade_tag}"
        items.append({"prefix": "-", "text": text, "alertGrade": grade if use_profiles else None})

    if not items:
        items.append({"prefix": "✓", "text": "<<ok:모든 센서가 정상 범위 내에 있습니다.>>"})

    return items


def build_anomaly_facility_detail_block(
    rows: list, columns: list,
    site_profiles: Optional[dict] = None,
    sitename: str = "",
    facilitytype: str = "",
    pattern_result: Optional[dict] = None,
    causal_result: Optional[dict] = None,
    cross_facility_result: Optional[dict] = None,
    propagation_trace: Optional[dict] = None,
    intra_facility_result: Optional[list] = None,
    cross_intra_result: Optional[list] = None,
) -> list:
    """
    ANOMALY_FACILITY_DETAIL: SQL 결과를 복합 진단 결과 리스트로 조립한다.
    Z-Score + Direction Change 복합 판정. 전 항목 표시.
    site_profiles가 제공되면 그룹별 임계값을 적용하고 경보 등급을 표시한다.
    """
    col_map = {c: i for i, c in enumerate(columns)}
    has_dir = "change_count" in col_map
    has_active = "active_pct" in col_map
    use_profiles = site_profiles is not None and len(site_profiles) > 0
    items = []

    # 그룹 결정
    group = "B"
    info_count = 0
    if use_profiles:
        profile = site_profiles.get((sitename, facilitytype))
        if profile:
            group = profile.get("site_group", "B")
            info_count = profile.get("info_count_7d", 0)

    _grade_marker = {
        "critical": "<<error:[긴급]>>",
        "warning": "<<warn:[주의]>>",
        "info": "<<ok:[참고]>>",
    }

    for row in rows:
        z_score = float(row[col_map["z_score"]] or 0)

        # 그룹별 Z-Score 판정
        if use_profiles:
            z_level = classify_z_level_by_group(z_score, group)
        else:
            z_level = classify_z_level(z_score)

        dir_level = "정상"
        change_count = 0
        if has_dir:
            change_count = int(row[col_map["change_count"]] or 0)
            dir_level = classify_direction_level(change_count)

        final = combined_judgment(z_level, dir_level) if has_dir else z_level

        datainfo = row[col_map["datainfo"]] or ""
        deviation_pct = float(row[col_map["deviation_pct"]] or 0)
        active_pct = float(row[col_map["active_pct"]] or 100) if has_active else 100

        dev_text = format_deviation_text(deviation_pct, z_score, active_pct)
        marked_level = _wrap_marker(final, final)

        # 경보 등급
        grade_tag = ""
        grade = None
        if use_profiles:
            grade = classify_alert_grade(group, final, dir_level, pattern_result, info_count)
            if grade:
                grade_tag = f" {_grade_marker[grade]}"

        if has_dir and dir_level != "정상":
            text = f"{datainfo}: {marked_level} ({dev_text}, 진동 {change_count}회){grade_tag}"
        else:
            text = f"{datainfo}: {marked_level} ({dev_text}){grade_tag}"

        items.append({"prefix": "-", "text": text, "alertGrade": grade})

    # 인과관계 검증 결과 추가
    if causal_result and causal_result.get("detail"):
        items.append({"prefix": "", "text": "─── 인과관계 분석 ───"})
        for line in causal_result["detail"].split("\n"):
            if line.strip():
                items.append({"prefix": "▸", "text": line.strip()})
        if causal_result.get("possible_causes"):
            causes_text = ", ".join(causal_result["possible_causes"])
            items.append({"prefix": "▸", "text": f"점검 필요: {causes_text}"})
        # SLM 자연어 해석 (Phase 2)
        if causal_result.get("_slm_explanation"):
            items.append({"prefix": "", "text": "─── AI 종합 해석 ───"})
            items.append({"prefix": "💡", "text": causal_result["_slm_explanation"]})

    # 시설간 교차 검증 결과
    cross_items = build_cross_facility_detail_block(cross_facility_result)
    if cross_items:
        items.extend(cross_items)

    # 다중 홉 전파 추적 결과
    if propagation_trace:
        prop_items = build_propagation_trace_block(
            propagation_trace.get("forward"),
            propagation_trace.get("backward"),
        )
        if prop_items:
            items.extend(prop_items)

    # 시설 내부 인과 검증 결과
    if intra_facility_result:
        intra_items = build_intra_facility_block(
            intra_facility_result, sitename, facilitytype,
        )
        if intra_items:
            items.extend(intra_items)

    # 시설간 교차 인과 규칙 검증 결과
    if cross_intra_result:
        mismatches = [r for r in cross_intra_result if r["is_mismatch"]]
        normals = [r for r in cross_intra_result if not r["is_mismatch"]]
        if mismatches or normals:
            items.append({"prefix": "", "text": "─── 시설간 교차 인과 검증 ───"})
        for r in mismatches:
            items.append({"prefix": "error", "text": r["detail"]})
        if normals:
            normal_labels = [r["rule_label"] for r in normals]
            items.append({"prefix": "ok", "text": f"시설간 연동 정상: {', '.join(normal_labels)}"})

    if not items:
        items.append({"prefix": "✓", "text": "<<ok:분석 대상 센서가 없습니다.>>"})

    return items


# ── ANOMALY_HISTORY ────────────────────────────────────────

def count_alarm_severity(rows: list, columns: list) -> dict:
    """alarm_severity별 건수를 집계한다."""
    col_map = {c: i for i, c in enumerate(columns)}
    sev_idx = col_map.get("alarm_severity")
    counts = {"경고": 0, "주의": 0, "정상": 0, "미분류": 0}

    if sev_idx is None:
        counts["미분류"] = len(rows)
        return counts

    for row in rows:
        sev = (row[sev_idx] or "").strip()
        if sev in counts:
            counts[sev] += 1
        else:
            counts["미분류"] += 1

    return counts


def build_anomaly_history_detail_block(rows: list, columns: list) -> list:
    """ANOMALY_HISTORY: 알람 이력을 시맨틱 마커로 포맷한다."""
    col_map = {c: i for i, c in enumerate(columns)}
    items = []

    # severity 기반 시맨틱 마커 매핑
    _sev_marker = {"경고": "error", "주의": "warn", "정상": "ok"}

    for row in rows[:20]:  # 최대 20건만 detail에 표시
        alarm_time = row[col_map.get("alarm_time", 0)] or ""
        sitename = row[col_map.get("sitename", 1)] or ""
        facilitytype = row[col_map.get("facilitytype", 2)] or ""
        severity = (row[col_map.get("alarm_severity", 4)] or "").strip()
        status = (row[col_map.get("alarm_status", 5)] or "").strip()
        alarm_msg = row[col_map.get("alarm_msg", 6)] or ""

        marker = _sev_marker.get(severity, "warn")
        status_text = f" [<<error:진행중>>]" if status == "진행중" else ""

        text = (
            f"<<{marker}:{severity or '미분류'}>> "
            f"{alarm_time} {sitename} {facilitytype} — {alarm_msg}{status_text}"
        )
        items.append({"prefix": "-", "text": text})

    if not items:
        items.append({"prefix": "✓", "text": "<<ok:최근 7일 알람 이력이 없습니다.>>"})

    return items


# ── ANOMALY_PREDICT ────────────────────────────────────────

def build_anomaly_predict_detail_block(rows: list, columns: list) -> list:
    """ANOMALY_PREDICT: 선형 회귀 예측 결과를 시맨틱 마커로 포맷한다."""
    col_map = {c: i for i, c in enumerate(columns)}
    items = []

    for row in rows:
        sitename = row[col_map["sitename"]] or ""
        datainfo = row[col_map["datainfo"]] or ""
        predicted_1h = float(row[col_map["predicted_1h"]] or 0)
        slope_per_hour = float(row[col_map["slope_per_hour"]] or 0)
        predicted_z = float(row[col_map["predicted_z"]] or 0)
        confidence = float(row[col_map["confidence"]] or 0)

        # Z-Score → 시맨틱 마커
        level = classify_z_level(predicted_z)
        marked_level = _wrap_marker(f"이상 예측" if level == "이상" else "주의 예측", level)

        # 방향
        direction = "↑상승" if slope_per_hour > 0 else "↓하강"

        # 신뢰도 텍스트
        if confidence >= 0.7:
            conf_text = "높음"
        elif confidence >= 0.5:
            conf_text = "보통"
        else:
            conf_text = "낮음"

        text = (
            f"{sitename} {datainfo}: {marked_level} "
            f"(1시간 후 {predicted_1h}, {direction}, 신뢰도 {conf_text})"
        )
        items.append({"prefix": "-", "text": text})

    if not items:
        items.append({"prefix": "✓", "text": "<<ok:현재 이상 예측 대상이 없습니다.>>"})

    return items


# ── ANOMALY_COMPARE ────────────────────────────────────────

def build_anomaly_compare_detail_block(rows: list, columns: list) -> list:
    """ANOMALY_COMPARE: 시설간 Z-Score 비교를 시맨틱 마커로 포맷한다."""
    col_map = {c: i for i, c in enumerate(columns)}
    items = []

    for row in rows:
        sitename = row[col_map["sitename"]] or ""
        total = int(row[col_map["total_sensors"]] or 0)
        errors = int(row[col_map["error_count"]] or 0)
        warns = int(row[col_map["warn_count"]] or 0)
        avg_z = float(row[col_map["avg_z_score"]] or 0)

        # 건강도 판정
        if errors > 0:
            health = _wrap_marker("주의 필요", "이상")
        elif warns > 0:
            health = _wrap_marker("관심", "주의")
        else:
            health = _wrap_marker("양호", "정상")

        text = (
            f"{sitename}: {health} "
            f"(이상 {errors}건, 주의 {warns}건 / 전체 {total}개, 평균Z {avg_z})"
        )
        items.append({"prefix": "-", "text": text})

    if not items:
        items.append({"prefix": "✓", "text": "<<ok:비교 대상 시설이 없습니다.>>"})

    return items


# ── ANOMALY_PATTERN ────────────────────────────────────────

def build_anomaly_pattern_detail_block(rows: list, columns: list) -> list:
    """ANOMALY_PATTERN: 시간대 이상 패턴을 시맨틱 마커로 포맷한다."""
    col_map = {c: i for i, c in enumerate(columns)}
    items = []

    for row in rows:
        sitename = row[col_map["sitename"]] or ""
        datainfo = row[col_map["datainfo"]] or ""
        hour_z = float(row[col_map["hour_z_score"]] or 0)
        deviation = float(row[col_map["hour_deviation_pct"]] or 0)
        current_hour = int(row[col_map["current_hour"]] or 0)

        level = classify_z_level(hour_z)
        marked_level = _wrap_marker(f"시간대 {level}", level)

        direction = "+" if hour_z > 0 else "-"
        text = (
            f"{sitename} {datainfo}: {marked_level} "
            f"({current_hour}시 평소 대비 {direction}{deviation:.1f}%)"
        )
        items.append({"prefix": "-", "text": text})

    if not items:
        items.append({"prefix": "✓", "text": "<<ok:현재 시간대에 이상 패턴이 없습니다.>>"})

    return items


# ── CUSUM + MNF 누수추정 ──────────────────────────────────────

# CUSUM 파라미터 (보수적 설정 — 실제 누수만 포착)
CUSUM_K_FACTOR = 1.5     # 허용치 k = 1.5σ (평균+1.5σ 이상만 누적에 기여)
CUSUM_H_ERROR = 5.0      # 누수 의심 기본 임계값 (기간 보정 적용)
CUSUM_H_WARN = 4.0       # 주의 기본 임계값 (기간 보정 적용)
CUSUM_MIN_DAYS = 14      # 최소 데이터 일수
CUSUM_REF_DAYS = 20      # 기간 보정 기준일수 (20일 기준, 장기 분석 보정 강화)


def _is_weekday(log_time) -> bool:
    """log_time(str 또는 datetime)에서 평일 여부를 판별한다."""
    if isinstance(log_time, str):
        dt = datetime.fromisoformat(log_time.replace("Z", "+00:00"))
    else:
        dt = log_time
    return dt.weekday() < 5  # Mon=0 ~ Fri=4


def compute_cusum_for_tags(
    rows: list, columns: list
) -> dict:
    """
    fn_night_min_flow_summary 결과를 태그별 CUSUM + MNF 분석한다.

    Returns:
        {tagsn: {
            "label", "sitename", "facilitytype", "unit",
            "baseline_mean", "baseline_stddev",
            "cusum_series": [(log_time, val, cusum_upper)],
            "cusum_max", "cusum_current", "threshold_h",
            "leak_status": "누수의심"/"주의"/"정상",
            "trend_slope": float,  # 일평균 변화량
            "recent_mean": float,  # 최근 7일 평균
            "day_count": int,
        }}
    """
    col_map = {c: i for i, c in enumerate(columns)}
    val_idx = col_map.get("val")
    time_idx = col_map.get("log_time")
    tagsn_idx = col_map.get("tagsn")
    label_idx = col_map.get("label")
    site_idx = col_map.get("sitename")
    ftype_idx = col_map.get("facilitytype")
    unit_idx = col_map.get("unit")

    if val_idx is None or tagsn_idx is None:
        return {}

    # 태그별 시계열 데이터 수집
    tag_series = defaultdict(list)
    tag_meta = {}
    for row in rows:
        tagsn = row[tagsn_idx] or ""
        val = row[val_idx]
        if val is None:
            continue
        val = float(val)
        log_time = row[time_idx] or ""
        tag_series[tagsn].append((log_time, val))
        if tagsn not in tag_meta:
            tag_meta[tagsn] = {
                "label": row[label_idx] if label_idx is not None else "",
                "sitename": row[site_idx] if site_idx is not None else "",
                "facilitytype": row[ftype_idx] if ftype_idx is not None else "",
                "unit": (row[unit_idx] or "") if unit_idx is not None else "",
            }

    results = {}
    for tagsn, series in tag_series.items():
        if len(series) < CUSUM_MIN_DAYS:
            continue

        # 시간순 정렬
        series.sort(key=lambda x: x[0])
        values = [v for _, v in series]

        # MNF 전체 통계 (stddev는 전체 기준 — 변동성은 공통)
        n = len(values)
        mean_val = sum(values) / n
        variance = sum((v - mean_val) ** 2 for v in values) / n
        stddev = variance ** 0.5

        if stddev < 0.001:
            # 전부 0 또는 변동 없음 → "비활성" 상태로 등록
            results[tagsn] = {
                **tag_meta.get(tagsn, {}),
                "baseline_mean": round(mean_val, 2),
                "baseline_stddev": 0.0,
                "baseline_wd_mean": round(mean_val, 2),
                "baseline_we_mean": round(mean_val, 2),
                "recent_mean": round(mean_val, 2),
                "cusum_series": [],
                "cusum_max": 0.0,
                "cusum_current": 0.0,
                "threshold_h": 0.0,
                "leak_status": "비활성",
                "trend_slope": 0.0,
                "day_count": n,
            }
            continue

        # 평일/주말 분리 기준선 (최소 7건 미달 시 전체 폴백)
        wd_vals = [v for t, v in series if _is_weekday(t)]
        we_vals = [v for t, v in series if not _is_weekday(t)]
        wd_mean = sum(wd_vals) / len(wd_vals) if len(wd_vals) >= 7 else mean_val
        we_mean = sum(we_vals) / len(we_vals) if len(we_vals) >= 7 else mean_val

        # 최근 7일 평균 (MNF 현재 수준)
        recent_vals = values[-7:] if len(values) >= 7 else values
        recent_mean = sum(recent_vals) / len(recent_vals)

        # 추세 기울기 (단순 선형 회귀)
        x_mean = (n - 1) / 2.0
        slope_num = sum((i - x_mean) * (v - mean_val)
                        for i, v in enumerate(values))
        slope_den = sum((i - x_mean) ** 2 for i in range(n))
        trend_slope = slope_num / slope_den if slope_den > 0 else 0.0

        # CUSUM 상한 계산 (요일별 기준선 적용)
        k = CUSUM_K_FACTOR * stddev
        cusum_upper = 0.0
        cusum_max = 0.0
        cusum_series = []

        for log_time, val in series:
            baseline = wd_mean if _is_weekday(log_time) else we_mean
            cusum_upper = max(0, cusum_upper + (val - baseline - k))
            cusum_max = max(cusum_max, cusum_upper)
            cusum_series.append((log_time, val, round(cusum_upper, 2)))

        # 기간 보정: 분석 일수가 길수록 임계값 상향 (sqrt 비례)
        period_factor = (n / CUSUM_REF_DAYS) ** 0.5
        threshold_h = CUSUM_H_ERROR * stddev * period_factor
        threshold_w = CUSUM_H_WARN * stddev * period_factor

        # 누수 판정
        if cusum_max >= threshold_h:
            leak_status = "누수의심"
        elif cusum_max >= threshold_w:
            leak_status = "주의"
        else:
            leak_status = "정상"

        results[tagsn] = {
            **tag_meta.get(tagsn, {}),
            "baseline_mean": round(mean_val, 2),
            "baseline_stddev": round(stddev, 2),
            "baseline_wd_mean": round(wd_mean, 2),
            "baseline_we_mean": round(we_mean, 2),
            "recent_mean": round(recent_mean, 2),
            "cusum_series": cusum_series,
            "cusum_max": round(cusum_max, 2),
            "cusum_current": round(cusum_series[-1][2], 2) if cusum_series else 0,
            "threshold_h": round(threshold_h, 2),
            "leak_status": leak_status,
            "trend_slope": round(trend_slope, 4),
            "day_count": n,
        }

    return results


def build_cusum_summary_table(cusum_results: dict) -> tuple:
    """
    CUSUM 결과를 테이블 데이터(rows, columns)로 변환한다.
    """
    columns = [
        "sitename", "label", "baseline_mean", "current_trend",
        "cusum_value", "cusum_max", "threshold", "leak_status", "trend_slope"
    ]
    table_rows = []
    for tagsn, r in cusum_results.items():
        unit = r.get("unit") or ""
        if r["leak_status"] == "비활성":
            table_rows.append([
                r["sitename"], r["label"],
                "-", "비활성", "-", "-", "-", "비활성", "-",
            ])
            continue
        direction = "↑" if r["trend_slope"] > 0 else "↓" if r["trend_slope"] < 0 else "→"
        current_trend = f"{r['recent_mean']}{unit} ({direction})"
        table_rows.append([
            r["sitename"],
            r["label"],
            f"{r['baseline_mean']}{unit}",
            current_trend,
            str(r["cusum_current"]),
            str(r["cusum_max"]),
            str(r["threshold_h"]),
            r["leak_status"],
            f"{r['trend_slope']:+.4f}",
        ])
    # 누수의심 → 주의 → 정상 → 비활성 순서 정렬
    _status_order = {"누수의심": 0, "주의": 1, "정상": 2, "비활성": 3}
    table_rows.sort(key=lambda r: _status_order.get(r[7], 3))
    return table_rows, columns


def build_leak_cusum_detail_block(cusum_results: dict) -> list:
    """LEAK_CUSUM_ANALYSIS: CUSUM + MNF 결과를 시맨틱 마커로 포맷한다."""
    items = []
    _status_marker = {"누수의심": "error", "주의": "warn", "정상": "ok", "비활성": "warn"}
    _status_order = {"누수의심": 0, "주의": 1, "정상": 2, "비활성": 3}

    sorted_tags = sorted(
        cusum_results.values(),
        key=lambda r: _status_order.get(r["leak_status"], 3)
    )

    for r in sorted_tags:
        status = r["leak_status"]
        marker = _status_marker.get(status, "ok")
        label = r.get("label", "")
        unit = r.get("unit", "")

        if status == "비활성":
            text = (
                f"{label}: <<warn:비활성>> "
                f"(야간유량 전부 0 — 유량계 미작동 또는 무급수 구간, "
                f"분석기간 {r['day_count']}일)"
            )
            items.append({"prefix": "-", "text": text})
            continue

        direction = "↑상승" if r["trend_slope"] > 0 else "↓하강" if r["trend_slope"] < 0 else "→안정"
        deviation_pct = 0
        if r["baseline_mean"] > 0.001:
            deviation_pct = ((r["recent_mean"] - r["baseline_mean"])
                             / r["baseline_mean"] * 100)

        text = (
            f"{label}: <<{marker}:{status}>> "
            f"(기준 {r['baseline_mean']}{unit}, "
            f"최근7일 {r['recent_mean']}{unit}, "
            f"변동 {deviation_pct:+.1f}%, {direction}, "
            f"CUSUM최대 {r['cusum_max']}/{r['threshold_h']})"
        )
        items.append({"prefix": "-", "text": text})

    if not items:
        items.append({"prefix": "✓",
                       "text": "<<ok:분석 기간 내 누수 의심 태그가 없습니다.>>"})

    return items


def count_cusum_status(cusum_results: dict) -> dict:
    """CUSUM 판정 결과별 건수를 집계한다."""
    counts = {"누수의심": 0, "주의": 0, "정상": 0, "비활성": 0}
    for r in cusum_results.values():
        status = r.get("leak_status", "정상")
        counts[status] = counts.get(status, 0) + 1
    return counts


# ── 인과관계 검증 엔진 ──────────────────────────────────────────
# 물리법칙 기반 Rule 판단 — SLM 미사용, 투명하고 즉시 실행
# ────────────────────────────────────────────────────────────────

# 판정 패턴 유형 → 가능 원인 매핑
_CAUSAL_PATTERNS = {
    "PUMP_ON_NO_PRESSURE": {
        "label": "펌프가동+압력무반응",
        "possible_causes": ["펌프 고장", "관로 파손", "체크밸브 고착"],
    },
    "PUMP_ON_NO_FLOW": {
        "label": "펌프가동+유량무반응",
        "possible_causes": ["펌프 고장", "밸브 폐쇄 상태", "유량센서 이상"],
    },
    "VALVE_OPEN_NO_FLOW": {
        "label": "밸브개방+유량무반응",
        "possible_causes": ["밸브 고착", "관로 막힘", "유량센서 이상"],
    },
    "UPSTREAM_FLOW_NO_DOWNSTREAM": {
        "label": "상류유출+하류유입무반응",
        "possible_causes": ["관로 누수", "분기 밸브 이상", "센서 이상"],
    },
    "INLET_FLOW_NO_LEVEL_RISE": {
        "label": "유입증가+수위무반응",
        "possible_causes": ["유출 과다", "배수지 오버플로", "수위센서 이상"],
    },
    "PRESSURE_SUDDEN_DROP": {
        "label": "압력급강하",
        "possible_causes": ["관로 파손 의심", "대량 수요 발생", "밸브 급개방"],
    },
    "CAUSAL_CHAIN_NORMAL": {
        "label": "정상인과",
        "possible_causes": [],
    },
    "UPSTREAM_PROPAGATION": {
        "label": "상류→하류 이상 전파",
        "possible_causes": ["상류 시설 이상 전파", "관로 연쇄 영향", "유량 변동 전파"],
    },
    # 시설 내부 인과 패턴 (intra-facility)
    "LEVEL_DROP_NO_OUTFLOW": {
        "label": "수위하강+유출유량없음(누출의심)",
        "possible_causes": ["배수지 누출", "수위센서 이상", "부지 내 미계량 유출"],
    },
    "INLET_PRESSURE_NO_OUTLET": {
        "label": "유입압력활성+유출압력없음",
        "possible_causes": ["감압밸브 고착", "관로 막힘", "압력센서 이상"],
    },
    "PREREQUISITE_FAILED": {
        "label": "선행조건 미충족",
        "possible_causes": ["밸브 미개방", "전원 이상", "설비 정지 상태에서 동작 시도"],
    },
    "SAFETY_INTERLOCK_VIOLATED": {
        "label": "안전연동 위반",
        "possible_causes": ["HH/LL 초과 시 설비 미정지", "연동 제어 실패", "센서 이상"],
    },
    "AND_CONDITION_VIOLATED": {
        "label": "복합조건 위반",
        "possible_causes": ["복수 조건 충족인데 결과 무반응", "설비 고장", "센서 이상"],
    },
}

# 방향 판정 임계값 (%)
_DIRECTION_RISE_PCT = 5.0    # 5% 이상 상승 → RISE
_DIRECTION_FALL_PCT = -5.0   # 5% 이상 하강 → FALL
_DIRECTION_STABLE_PCT = 10.0  # ±10% 이내 → STABLE
_SUDDEN_DROP_PCT = -30.0     # 30% 이상 급감 → SUDDEN_DROP


def _check_direction(values: list[float], expected: str) -> tuple[bool, str]:
    """시간 윈도우 내 값들의 방향을 판정한다.

    Args:
        values: 시간순 정렬된 값 리스트
        expected: "RISE" | "FALL" | "STABLE" | "CORRELATE"

    Returns:
        (일치 여부, 실제 방향 "RISE"/"FALL"/"STABLE"/"INSUFFICIENT")
    """
    if len(values) < 3:
        return (True, "INSUFFICIENT")  # 데이터 부족 시 통과 처리

    mid = len(values) // 2
    first_half = sum(values[:mid]) / mid
    second_half = sum(values[mid:]) / (len(values) - mid)

    denom = max(abs(first_half), 0.001)
    diff_pct = (second_half - first_half) / denom * 100

    if diff_pct > _DIRECTION_RISE_PCT:
        actual = "RISE"
    elif diff_pct < _DIRECTION_FALL_PCT:
        actual = "FALL"
    else:
        actual = "STABLE"

    if expected == "CORRELATE":
        return (True, actual)  # 단순 감시: 항상 통과
    return (actual == expected, actual)


def _check_prerequisite(value: float, condition: str) -> bool:
    """선행조건 충족 여부를 판정한다.

    Args:
        value: 최신 태그값
        condition: "ON" | "OFF" | "OPEN" | "CLOSED" | "NORMAL"

    Returns:
        True if prerequisite is met
    """
    cond_upper = condition.upper()
    if cond_upper in ("ON", "OPEN"):
        return value > 0
    if cond_upper in ("OFF", "CLOSED"):
        return value == 0
    if cond_upper == "NORMAL":
        return value == 0  # POWER_FAULT/COMM_ERROR: 0 = 정상
    return True  # 알 수 없는 조건은 통과


def _detect_sudden_drop(values: list[float]) -> bool:
    """5분 이내 30% 이상 급감 여부를 판정한다."""
    if len(values) < 3:
        return False
    peak = max(values[:len(values) // 2]) if values[:len(values) // 2] else 0
    last = values[-1]
    if peak <= 0:
        return False
    drop_pct = (last - peak) / peak * 100
    return drop_pct < _SUDDEN_DROP_PCT


# =============================================================================
# SLM 자연어 해석 (Phase 2)
# =============================================================================

_CAUSAL_EXPLAIN_PROMPT = """당신은 수도시설 이상감지 시스템의 분석 전문가입니다.
아래 인과관계 분석 결과를 현장 운영자가 이해할 수 있는 한국어로 설명하세요.

시설: {sitename} {facilitytype}
감지 패턴: {pattern_label}
인과 체인 정상 여부: {chain_status}
추정 원인: {causes}
하류 영향: {downstream}

요구사항:
1. 어떤 현상이 감지되었는지 한 문장
2. 가능한 원인을 우선순위로 한 문장
3. 즉시 확인해야 할 사항 한 문장

3문장 이내로 간결하게 답변하세요. 마크다운 없이 평문으로."""


def generate_causal_explanation(
    ollama_generate_func,
    causal_result: dict,
    sitename: str,
    facilitytype: str,
) -> str | None:
    """인과 판정 결과를 SLM으로 자연어 해석한다.

    Args:
        ollama_generate_func: (prompt: str) → str 텍스트 생성 콜백
        causal_result: verify_causal_context() 반환값
        sitename: 현장명
        facilitytype: 시설유형

    Returns:
        자연어 해석 문자열, 실패/불필요 시 None
    """
    if not causal_result:
        return None
    pattern = causal_result.get("pattern", "")
    if pattern in ("CAUSAL_CHAIN_NORMAL", "NO_INDEX", ""):
        return None

    pat_info = _CAUSAL_PATTERNS.get(pattern, {})
    prompt = _CAUSAL_EXPLAIN_PROMPT.format(
        sitename=sitename,
        facilitytype=facilitytype,
        pattern_label=pat_info.get("label", pattern),
        chain_status="정상" if causal_result.get("chain_matched") else "불일치",
        causes=", ".join(causal_result.get("possible_causes", [])) or "미확인",
        downstream=", ".join(str(d) for d in causal_result.get("downstream_impact", [])) or "없음",
    )

    try:
        explanation = ollama_generate_func(prompt)
        return explanation.strip() if explanation else None
    except Exception as e:
        logger.debug("인과 해석 SLM 호출 실패 (무시): %s", e)
        return None


def verify_causal_context(
    query_func,
    sitename: str,
    facilitytype: str,
    anomaly_group_code: str,
    anomaly_direction: str,
    causal_index: dict,
    lookback_minutes: int = 30,
    zone: str | None = None,
) -> dict:
    """알람 발생 태그에 대해 인과 체인을 검증한다.

    Args:
        query_func: (tagsn_list, minutes) → {tagsn: [float, ...]} 시계열 조회 콜백
        sitename: 현장명
        facilitytype: 시설유형
        anomaly_group_code: 이상 감지된 태그의 group_code
        anomaly_direction: 이상 방향 ("RISE" | "FALL")
        causal_index: _CAUSAL_INDEX 전체 딕셔너리
        lookback_minutes: 시계열 조회 범위 (분)
        zone: 구역 ("1지","2지" 등, 배수지 구역 분리용)

    Returns:
        dict with keys: chain_matched, pattern, cause_found, downstream_impact,
                        confidence, detail, possible_causes
    """
    # zone 지정 시 3-tuple 키 우선, 없으면 2-tuple 폴백
    info = None
    if zone:
        info = causal_index.get((sitename, facilitytype, zone))
    if not info:
        info = causal_index.get((sitename, facilitytype))
    if not info:
        return _no_match_result("인과 인덱스에 시설 없음")

    template = info["template"]
    tag_map = info["tag_map"]
    chain = template["chain"]

    # 이상 태그의 step 찾기
    anomaly_step = None
    for step_info in chain:
        if step_info["group_code"] == anomaly_group_code:
            anomaly_step = step_info
            break
    # group_code가 상위(PRESSURE→PRESSURE_INLET 등)일 수 있으므로 하위도 체크
    if not anomaly_step:
        for step_info in chain:
            gc = step_info["group_code"]
            if anomaly_group_code.startswith(gc) or gc.startswith(anomaly_group_code):
                anomaly_step = step_info
                break
    # 같은 부모 그룹 형제 매칭 (FLOW_INSTANT↔FLOW_INLET 등)
    if not anomaly_step:
        _SIBLING_MAP = {
            "FLOW": ["FLOW_INSTANT", "FLOW_CUMULATIVE", "FLOW_INLET", "FLOW_OUTLET"],
            "PRESSURE": ["PRESSURE_INLET", "PRESSURE_OUTLET", "PRESSURE_DISCHARGE"],
        }
        anomaly_parent = anomaly_group_code.rsplit("_", 1)[0] if "_" in anomaly_group_code else anomaly_group_code
        siblings = _SIBLING_MAP.get(anomaly_parent, [])
        if anomaly_group_code in siblings:
            for step_info in chain:
                if step_info["group_code"] in siblings:
                    anomaly_step = step_info
                    break
    if not anomaly_step:
        return _no_match_result("템플릿에 해당 group_code 없음")

    current_step_num = anomaly_step["step"]
    results = []

    # ── 역방향 추적: 이전 step들의 태그 상태 확인 ──
    for step_info in chain:
        if step_info["step"] >= current_step_num:
            continue
        gc = step_info["group_code"]
        tagsns = tag_map.get(gc, [])
        if not tagsns:
            continue

        try:
            ts_data = query_func(tagsns, lookback_minutes)
        except Exception as e:
            logger.debug("인과 검증 시계열 조회 실패 (%s): %s", gc, e)
            continue

        for tsn, values in ts_data.items():
            if not values:
                continue
            # 디지털 태그 (펌프/밸브): 최신값으로 상태 판정
            if step_info["role"] == "cause":
                last_val = values[-1] if values else 0
                is_active = last_val > 0  # 1=ON/OPEN, 0=OFF/CLOSED
                results.append({
                    "step": step_info["step"],
                    "group_code": gc,
                    "role": step_info["role"],
                    "signal": step_info.get("signal"),
                    "is_active": is_active,
                    "tagsn": tsn,
                })
            elif step_info.get("expected"):
                matched, actual = _check_direction(values, step_info["expected"])
                results.append({
                    "step": step_info["step"],
                    "group_code": gc,
                    "role": step_info["role"],
                    "expected": step_info["expected"],
                    "actual": actual,
                    "matched": matched,
                    "tagsn": tsn,
                })

        # ── 선행조건(requires) 검증 ──
        requires = step_info.get("requires", [])
        for req in requires:
            req_gc = req["group_code"]
            req_tagsns = tag_map.get(req_gc, [])
            if not req_tagsns:
                results.append({
                    "step": step_info["step"],
                    "group_code": req_gc,
                    "role": "prerequisite",
                    "condition": req["condition"],
                    "met": False,
                    "detail": f"{req_gc} 태그 없음",
                })
                continue
            try:
                req_data = query_func(req_tagsns, lookback_minutes)
            except Exception:
                continue
            for req_tsn, req_vals in req_data.items():
                if not req_vals:
                    continue
                req_met = _check_prerequisite(req_vals[-1], req["condition"])
                results.append({
                    "step": step_info["step"],
                    "group_code": req_gc,
                    "role": "prerequisite",
                    "condition": req["condition"],
                    "met": req_met,
                    "tagsn": req_tsn,
                    "actual_value": req_vals[-1],
                })

    # ── 순방향 추적: 이후 step들 + 하류 시설 ──
    downstream_impacts = []
    for step_info in chain:
        if step_info["step"] <= current_step_num:
            continue
        gc = step_info["group_code"]
        tagsns = tag_map.get(gc, [])
        if not tagsns or not step_info.get("expected"):
            continue
        try:
            ts_data = query_func(tagsns, lookback_minutes)
        except Exception:
            continue
        for tsn, values in ts_data.items():
            if values:
                matched, actual = _check_direction(values, step_info["expected"])
                results.append({
                    "step": step_info["step"],
                    "group_code": gc,
                    "role": "downstream_effect",
                    "expected": step_info["expected"],
                    "actual": actual,
                    "matched": matched,
                    "tagsn": tsn,
                })

    # 하류 시설 영향 체크
    cross = template.get("cross_facility")
    if cross:
        for ds_key in info.get("downstream", []):
            ds_info = causal_index.get(ds_key)
            if not ds_info:
                continue
            ds_gc = cross["downstream_group"]
            ds_tagsns = ds_info["tag_map"].get(ds_gc, [])
            if not ds_tagsns:
                continue
            try:
                ts_data = query_func(ds_tagsns, cross.get("lag_max", 30))
            except Exception:
                continue
            for tsn, values in ts_data.items():
                if values:
                    matched, actual = _check_direction(values, cross["expected"])
                    if not matched:
                        downstream_impacts.append(
                            f"{ds_key[0]} {ds_key[1]} ({ds_gc}: {actual})"
                        )

    # ── 패턴 매칭: 인과 불일치 판정 ──
    pattern = _determine_pattern(anomaly_group_code, anomaly_direction, results, facilitytype)
    pat_info = _CAUSAL_PATTERNS.get(pattern, _CAUSAL_PATTERNS["CAUSAL_CHAIN_NORMAL"])

    chain_matched = pattern == "CAUSAL_CHAIN_NORMAL"
    confidence = "high" if len(results) >= 2 else ("medium" if results else "low")
    detail = _build_causal_detail(pat_info, results, sitename, facilitytype, downstream_impacts)

    return {
        "chain_matched": chain_matched,
        "pattern": pattern,
        "cause_found": pat_info["label"] if not chain_matched else None,
        "downstream_impact": downstream_impacts,
        "confidence": confidence,
        "detail": detail,
        "possible_causes": pat_info["possible_causes"],
    }


def _no_match_result(reason: str) -> dict:
    """인과 인덱스에 매핑 없을 때의 기본 반환값."""
    return {
        "chain_matched": True,  # 검증 불가 → 기존 판정 유지
        "pattern": "NO_INDEX",
        "cause_found": None,
        "downstream_impact": [],
        "confidence": "low",
        "detail": reason,
        "possible_causes": [],
    }


def _determine_pattern(
    anomaly_gc: str,
    anomaly_dir: str,
    step_results: list[dict],
    facilitytype: str,
) -> str:
    """step_results를 분석하여 인과 패턴을 판정한다."""
    # 선행조건 실패 체크 (최우선)
    prereq_failures = [r for r in step_results if r.get("role") == "prerequisite" and r.get("met") is False]
    if prereq_failures:
        return "PREREQUISITE_FAILED"

    # cause 역할 태그의 상태 수집
    cause_active = None
    for r in step_results:
        if r.get("role") == "cause" and r.get("is_active") is not None:
            cause_active = r["is_active"]
            break

    # effect 역할 태그의 방향 일치 여부 수집
    effects_matched = [r["matched"] for r in step_results if r.get("role") in ("effect", "downstream_effect") and "matched" in r]

    # 가압장: 펌프ON인데 압력/유량 무반응
    if facilitytype == "가압장" and cause_active is True:
        if anomaly_gc in ("PRESSURE_DISCHARGE", "PRESSURE") and anomaly_dir == "FALL":
            return "PUMP_ON_NO_PRESSURE"
        if anomaly_gc in ("FLOW_OUTLET", "FLOW") and anomaly_dir == "FALL":
            return "PUMP_ON_NO_FLOW"

    # 배수지: 밸브 OPEN인데 유량 무반응
    if facilitytype == "배수지" and cause_active is True:
        if anomaly_gc in ("FLOW_OUTLET", "FLOW") and anomaly_dir != "RISE":
            return "VALVE_OPEN_NO_FLOW"

    # 유입 증가인데 수위 무반응
    if anomaly_gc == "WATER_LEVEL" and anomaly_dir == "FALL":
        for r in step_results:
            if r.get("group_code") in ("FLOW_INLET",) and r.get("actual") == "RISE":
                return "INLET_FLOW_NO_LEVEL_RISE"

    # 하류 영향 불일치
    unmatched_effects = [r for r in step_results if r.get("role") == "downstream_effect" and r.get("matched") is False]
    if unmatched_effects:
        return "UPSTREAM_FLOW_NO_DOWNSTREAM"

    # 모든 체크 통과 → 정상 인과
    return "CAUSAL_CHAIN_NORMAL"


def _build_causal_detail(
    pat_info: dict,
    step_results: list[dict],
    sitename: str,
    facilitytype: str,
    downstream_impacts: list[str],
) -> str:
    """인과 판정 결과를 시맨틱 마커 포함 텍스트로 생성한다."""
    parts = []
    label = pat_info["label"]
    causes = pat_info["possible_causes"]

    if causes:
        # 인과 불일치
        parts.append(f"<<error:인과불일치>> {sitename} {facilitytype}: {label}")
        if causes:
            parts.append(f"추정 원인: {', '.join(causes)}")
    else:
        # 정상 인과
        parts.append(f"<<ok:인과확인>> {sitename} {facilitytype}: 정상 인과 체인")

    # 하류 영향
    if downstream_impacts:
        impacts_str = ", ".join(downstream_impacts[:3])
        parts.append(f"<<warn:하류영향>> {impacts_str}")

    return "\n".join(parts)


# =============================================================================
# 시설 내부 인과 검증 (Intra-Facility Validation)
# =============================================================================
# 시설 내부 물리법칙: 펌프 ON→토출압력, 밸브 OPEN→유량, 유입→수위 등
# verify_causal_context 와 독립적으로 실행 — 태그 보유 시 자동 적용
# =============================================================================

# 시설 내부 규칙 정의
_INTRA_RULES: list[dict] = [
    # === 가압장 내부 ===
    {
        "facilitytype": "가압장",
        "condition_gc": "PUMP_STATUS",
        "condition_check": "active",
        "effect_gc": "PRESSURE_DISCHARGE",
        "effect_gc_fallback": ["PRESSURE_OUTLET", "PRESSURE"],
        "effect_check": "nonzero",
        "mismatch_pattern": "PUMP_ON_NO_PRESSURE",
        "label": "펌프가동+토출압력없음",
    },
    {
        "facilitytype": "가압장",
        "condition_gc": "PUMP_STATUS",
        "condition_check": "active",
        "effect_gc": "FLOW_OUTLET",
        "effect_check": "nonzero",
        "effect_gc_fallback": ["FLOW_INSTANT", "FLOW_CUMULATIVE"],
        "mismatch_pattern": "PUMP_ON_NO_FLOW",
        "label": "펌프가동+유출유량없음",
    },
    # === 배수지 내부 ===
    {
        "facilitytype": "배수지",
        "condition_gc": "VALVE_STATUS",
        "condition_check": "active",
        "effect_gc": "FLOW_OUTLET",
        "effect_check": "nonzero",
        "mismatch_pattern": "VALVE_OPEN_NO_FLOW",
        "label": "유출밸브개방+유량없음",
    },
    {
        "facilitytype": "배수지",
        "condition_gc": "FLOW_INLET",
        "condition_check": "nonzero",
        "effect_gc": "WATER_LEVEL",
        "effect_check": "not_falling",
        "mismatch_pattern": "INLET_FLOW_NO_LEVEL_RISE",
        "label": "유입유량활성+수위하강",
    },
    {
        "facilitytype": "배수지",
        "condition_gc": "WATER_LEVEL",
        "condition_check": "falling",
        "effect_gc": "FLOW_OUTLET",
        "effect_check": "nonzero",
        "inverted": True,
        "mismatch_pattern": "LEVEL_DROP_NO_OUTFLOW",
        "label": "수위하강+유출유량없음(누출의심)",
    },
    # === 감압시설 내부 ===
    {
        "facilitytype": "감압시설",
        "condition_gc": "PRESSURE_INLET",
        "condition_check": "nonzero",
        "effect_gc": "PRESSURE_OUTLET",
        "effect_check": "nonzero",
        "mismatch_pattern": "INLET_PRESSURE_NO_OUTLET",
        "label": "유입압력활성+유출압력없음",
    },
    # === 소블록 내부 ===
    {
        "facilitytype": "소블록",
        "condition_gc": "FLOW_INLET",
        "condition_check": "nonzero",
        "effect_gc": "FLOW_OUTLET",
        "effect_check": "nonzero",
        "effect_gc_fallback": ["FLOW_INSTANT"],
        "mismatch_pattern": "INLET_FLOW_NO_OUTLET",
        "label": "유입유량활성+유출유량없음",
    },
    # === 소소블록 내부 ===
    {
        "facilitytype": "소소블록",
        "condition_gc": "FLOW_INLET",
        "condition_check": "nonzero",
        "effect_gc": "FLOW_OUTLET",
        "effect_check": "nonzero",
        "effect_gc_fallback": ["FLOW_INSTANT"],
        "mismatch_pattern": "INLET_FLOW_NO_OUTLET",
        "label": "유입유량활성+유출유량없음",
    },
]


# =============================================================================
# 시설간 교차 인과 규칙 (Cross-Facility Intra Rules)
# =============================================================================
# 상류 시설의 출력 → 하류 시설의 입력이 물리적으로 연동되어야 하는 규칙
# causal_index의 upstream/downstream 링크를 활용
# =============================================================================

_CROSS_RULES: list[dict] = [
    # 가압장(상류) 유출 → 소블록(하류) 유입
    {
        "upstream_facilitytype": "가압장",
        "downstream_facilitytype": "소블록",
        "upstream_output_gc": "FLOW_OUTLET",
        "upstream_output_gc_fallback": ["FLOW_INSTANT"],
        "downstream_input_gc": "FLOW_INLET",
        "downstream_input_gc_fallback": ["FLOW_INSTANT"],
        "mismatch_pattern": "UPSTREAM_FLOW_NO_DOWNSTREAM_INLET",
        "label": "가압장 유출↑+소블록 유입없음",
    },
    # 배수지(상류) 유출 → 가압장(하류) 유입
    {
        "upstream_facilitytype": "배수지",
        "downstream_facilitytype": "가압장",
        "upstream_output_gc": "FLOW_OUTLET",
        "upstream_output_gc_fallback": [],
        "downstream_input_gc": "FLOW_INLET",
        "downstream_input_gc_fallback": [],
        "mismatch_pattern": "UPSTREAM_FLOW_NO_DOWNSTREAM_INLET",
        "label": "배수지 유출↑+가압장 유입없음",
    },
    # 배수지(상류) 유출 → 소블록(하류) 유입
    {
        "upstream_facilitytype": "배수지",
        "downstream_facilitytype": "소블록",
        "upstream_output_gc": "FLOW_OUTLET",
        "upstream_output_gc_fallback": [],
        "downstream_input_gc": "FLOW_INLET",
        "downstream_input_gc_fallback": ["FLOW_INSTANT"],
        "mismatch_pattern": "UPSTREAM_FLOW_NO_DOWNSTREAM_INLET",
        "label": "배수지 유출↑+소블록 유입없음",
    },
    # 감압시설(상류) 유출압력 → 소블록(하류) 유입
    {
        "upstream_facilitytype": "감압시설",
        "downstream_facilitytype": "소블록",
        "upstream_output_gc": "PRESSURE_OUTLET",
        "upstream_output_gc_fallback": [],
        "downstream_input_gc": "FLOW_INLET",
        "downstream_input_gc_fallback": ["FLOW_INSTANT"],
        "mismatch_pattern": "UPSTREAM_PRESSURE_NO_DOWNSTREAM_INLET",
        "label": "감압시설 유출압력↑+소블록 유입없음",
    },
    # 소블록(상류) 유출 → 소소블록(하류) 유입
    {
        "upstream_facilitytype": "소블록",
        "downstream_facilitytype": "소소블록",
        "upstream_output_gc": "FLOW_OUTLET",
        "upstream_output_gc_fallback": ["FLOW_INSTANT"],
        "downstream_input_gc": "FLOW_INLET",
        "downstream_input_gc_fallback": ["FLOW_INSTANT"],
        "mismatch_pattern": "UPSTREAM_FLOW_NO_DOWNSTREAM_INLET",
        "label": "소블록 유출↑+소소블록 유입없음",
    },
]


def _check_intra_condition(values: list[float], check_type: str) -> bool:
    if not values:
        return False
    if check_type == "active":
        return values[-1] > 0
    if check_type == "nonzero":
        active_rate = sum(1 for v in values if abs(v) > 0.01) / len(values)
        return active_rate >= 0.5
    if check_type == "falling":
        if len(values) < 4:
            return False
        mid = len(values) // 2
        first = sum(values[:mid]) / mid
        second = sum(values[mid:]) / (len(values) - mid)
        if abs(first) < 0.001:
            return False
        return (second - first) / abs(first) * 100 < -5.0
    return False


def _check_intra_effect(values: list[float], check_type: str) -> bool:
    if not values:
        return False
    if check_type == "nonzero":
        active_rate = sum(1 for v in values if abs(v) > 0.01) / len(values)
        return active_rate >= 0.3
    if check_type == "not_falling":
        if len(values) < 4:
            return True
        mid = len(values) // 2
        first = sum(values[:mid]) / mid
        second = sum(values[mid:]) / (len(values) - mid)
        if abs(first) < 0.001:
            return True
        return (second - first) / abs(first) * 100 >= -15.0
    return True


def verify_safety_interlocks(
    query_func,
    sitename: str,
    facilitytype: str,
    causal_index: dict,
    lookback_minutes: int = 30,
    zone: str | None = None,
) -> list[dict]:
    """안전 연동 규칙 검증. trigger 조건 충족 시 action 기대 상태 확인."""
    info = causal_index.get((sitename, facilitytype, zone)) if zone else None
    if not info:
        info = causal_index.get((sitename, facilitytype))
    if not info:
        return []

    template = info["template"]
    tag_map = info["tag_map"]
    interlocks = template.get("safety_interlocks", [])
    results = []

    for si in interlocks:
        trigger_tagsns = tag_map.get(si["trigger_gc"], [])
        if not trigger_tagsns:
            continue
        try:
            trigger_data = query_func(trigger_tagsns, lookback_minutes)
        except Exception:
            continue

        trigger_val = None
        for vals in trigger_data.values():
            if vals:
                trigger_val = vals[-1]
                break
        if trigger_val is None:
            continue

        # action 태그 조회
        direction = si.get("direction", "self")
        if direction == "self":
            action_tagsns = tag_map.get(si["action_gc"], [])
        else:
            action_tagsns = _get_neighbor_tags(
                causal_index, info, direction, si["action_gc"], si.get("target_facilitytype"))

        if not action_tagsns:
            continue
        try:
            action_data = query_func(action_tagsns, si.get("lag_max", 5))
        except Exception:
            continue

        for tsn, vals in action_data.items():
            if not vals:
                continue
            action_met = _check_prerequisite(vals[-1], si["action_expected"])
            results.append({
                "id": si["id"], "label": si["label"],
                "trigger_value": trigger_val, "action_met": action_met,
                "action_value": vals[-1], "tagsn": tsn,
                "violated": not action_met,
            })
    return results


def verify_and_conditions(
    query_func,
    sitename: str,
    facilitytype: str,
    causal_index: dict,
    lookback_minutes: int = 30,
    zone: str | None = None,
) -> list[dict]:
    """AND 조건 검증. 모든 conditions 충족 시 effect 기대."""
    info = causal_index.get((sitename, facilitytype, zone)) if zone else None
    if not info:
        info = causal_index.get((sitename, facilitytype))
    if not info:
        return []

    template = info["template"]
    tag_map = info["tag_map"]
    results = []

    for ac in template.get("and_conditions", []):
        all_met = True
        cond_details = []
        for cond in ac["conditions"]:
            tagsns = tag_map.get(cond["group_code"], [])
            met = False
            if tagsns:
                try:
                    data = query_func(tagsns, lookback_minutes)
                    for vals in data.values():
                        if vals and _check_prerequisite(vals[-1], cond["condition"]):
                            met = True
                            break
                except Exception:
                    pass
            cond_details.append({"group_code": cond["group_code"], "condition": cond["condition"], "met": met})
            if not met:
                all_met = False

        if not all_met:
            continue

        # effect 확인
        effect_tagsns = tag_map.get(ac["effect_gc"], [])
        if not effect_tagsns:
            continue
        try:
            effect_data = query_func(effect_tagsns, ac.get("lag_max", 5))
        except Exception:
            continue

        for tsn, vals in effect_data.items():
            if not vals:
                continue
            effect_ok = vals[-1] > 0 if ac["effect_expected"] == "NONZERO" else (vals[-1] == 0 if ac["effect_expected"] == "ZERO" else True)
            results.append({
                "id": ac["id"], "label": ac["label"],
                "conditions_met": True, "effect_ok": effect_ok,
                "effect_value": vals[-1], "tagsn": tsn,
                "violated": not effect_ok,
                "condition_details": cond_details,
            })
    return results


def _get_neighbor_tags(
    causal_index: dict, info: dict, direction: str,
    group_code: str, target_facilitytype: str | None = None,
) -> list[str]:
    """상류/하류 시설의 특정 group_code 태그를 조회."""
    neighbors = info.get("upstream" if direction == "upstream" else "downstream", [])
    tagsns = []
    for key in neighbors:
        if target_facilitytype and len(key) >= 2 and key[1] != target_facilitytype:
            continue
        nb_info = causal_index.get(key)
        if nb_info:
            tagsns.extend(nb_info["tag_map"].get(group_code, []))
    return tagsns


def verify_intra_facility(
    query_func,
    sitename: str,
    facilitytype: str,
    causal_index: dict,
    lookback_minutes: int = 30,
    zone: str | None = None,
) -> list[dict]:
    """시설 내부 물리법칙 기반 인과 검증."""
    info = None
    if zone:
        info = causal_index.get((sitename, facilitytype, zone))
    if not info:
        info = causal_index.get((sitename, facilitytype))
    if not info:
        return []

    tag_map = info["tag_map"]
    results: list[dict] = []

    for rule in _INTRA_RULES:
        if rule["facilitytype"] != facilitytype:
            continue

        cond_tagsns = tag_map.get(rule["condition_gc"], [])
        if not cond_tagsns:
            continue

        eff_gc = rule["effect_gc"]
        eff_tagsns = tag_map.get(eff_gc, [])
        if not eff_tagsns:
            for fb in rule.get("effect_gc_fallback", []):
                eff_tagsns = tag_map.get(fb, [])
                if eff_tagsns:
                    eff_gc = fb
                    break
        if not eff_tagsns:
            continue

        try:
            cond_data = query_func(cond_tagsns, lookback_minutes)
            eff_data = query_func(eff_tagsns, lookback_minutes)
        except Exception:
            continue

        cond_values = [v for vals in cond_data.values() for v in vals]
        eff_values = [v for vals in eff_data.values() for v in vals]
        if not cond_values:
            continue

        cond_met = _check_intra_condition(cond_values, rule["condition_check"])
        if not cond_met:
            continue

        eff_met = _check_intra_effect(eff_values, rule["effect_check"])
        inverted = rule.get("inverted", False)
        is_mismatch = not eff_met if not inverted else not eff_met

        results.append({
            "rule_label": rule["label"],
            "pattern": rule["mismatch_pattern"] if is_mismatch else "INTRA_NORMAL",
            "condition_gc": rule["condition_gc"],
            "effect_gc": eff_gc,
            "condition_met": True,
            "effect_met": eff_met,
            "is_mismatch": is_mismatch,
            "detail": _build_intra_detail(rule, sitename, facilitytype, cond_values, eff_values, is_mismatch),
        })

    return results


def _build_intra_detail(
    rule: dict, sitename: str, facilitytype: str,
    cond_values: list[float], eff_values: list[float], is_mismatch: bool,
) -> str:
    cond_mean = sum(cond_values) / len(cond_values) if cond_values else 0
    eff_mean = sum(eff_values) / len(eff_values) if eff_values else 0
    eff_active = sum(1 for v in eff_values if abs(v) > 0.01) / max(len(eff_values), 1) * 100

    if is_mismatch:
        pat = _CAUSAL_PATTERNS.get(rule["mismatch_pattern"], {})
        causes = pat.get("possible_causes", [])
        parts = [f"<<error:설비점검필요>> {sitename} {facilitytype}: {rule['label']}"]
        parts.append(f"  조건: {rule['condition_gc']} 평균={cond_mean:.1f}, 결과: {rule['effect_gc']} 평균={eff_mean:.1f} (활성 {eff_active:.0f}%)")
        if causes:
            parts.append(f"  추정 원인: {', '.join(causes)}")
        return chr(10).join(parts)
    return f"<<ok:설비정상>> {sitename} {facilitytype}: {rule['condition_gc']}→{rule['effect_gc']} 정상 연동"


def build_intra_facility_block(
    intra_results: list[dict], sitename: str, facilitytype: str,
) -> list[dict]:
    """verify_intra_facility 결과를 시맨틱 마커 블록으로 변환."""
    items: list[dict] = []
    mismatches = [r for r in intra_results if r["is_mismatch"]]
    normals = [r for r in intra_results if not r["is_mismatch"]]
    if not intra_results:
        return items
    for r in mismatches:
        items.append({"prefix": "error", "text": r["detail"]})
    if normals:
        normal_labels = [r["rule_label"] for r in normals]
        items.append({"prefix": "ok", "text": f"설비 내부 정상: {', '.join(normal_labels)}"})
    return items


def verify_cross_facility_intra_rules(
    query_func,
    sitename: str,
    facilitytype: str,
    causal_index: dict,
    lookback_minutes: int = 30,
) -> list[dict]:
    """시설간 교차 인과 규칙 검증.

    조회 시설(sitename, facilitytype)을 기준으로 causal_index의
    upstream/downstream 링크를 통해 _CROSS_RULES를 적용한다.

    - 조회 시설이 하류(downstream_facilitytype)인 경우: upstream 링크로 상류 확인
    - 조회 시설이 상류(upstream_facilitytype)인 경우: downstream 링크로 하류 확인
    """
    info = causal_index.get((sitename, facilitytype))
    if not info:
        return []

    results: list[dict] = []

    for rule in _CROSS_RULES:
        up_ft = rule["upstream_facilitytype"]
        dn_ft = rule["downstream_facilitytype"]

        # Case 1: 현재 시설이 하류 → 상류 시설 태그 조회
        if facilitytype == dn_ft:
            upstream_pairs = [
                nb for nb in info.get("upstream", [])
                if len(nb) >= 2 and nb[1] == up_ft
            ]
            for up_key in upstream_pairs:
                up_info = causal_index.get(up_key)
                if not up_info:
                    continue
                _run_cross_rule(
                    query_func, rule, up_info, info,
                    up_key[0], facilitytype, sitename,
                    lookback_minutes, results,
                )

        # Case 2: 현재 시설이 상류 → 하류 시설 태그 조회
        elif facilitytype == up_ft:
            downstream_pairs = [
                nb for nb in info.get("downstream", [])
                if len(nb) >= 2 and nb[1] == dn_ft
            ]
            for dn_key in downstream_pairs:
                dn_info = causal_index.get(dn_key)
                if not dn_info:
                    continue
                _run_cross_rule(
                    query_func, rule, info, dn_info,
                    sitename, dn_key[0], facilitytype,
                    lookback_minutes, results,
                )

    return results


def _resolve_gc_tags(tag_map: dict, primary: str, fallbacks: list[str]) -> list[str]:
    """group_code 우선순위로 tagsn 목록 반환 (fallback 포함)."""
    tags = tag_map.get(primary, [])
    if tags:
        return tags
    for fb in fallbacks:
        tags = tag_map.get(fb, [])
        if tags:
            return tags
    return []


def _run_cross_rule(
    query_func,
    rule: dict,
    up_info: dict,
    dn_info: dict,
    up_sitename: str,
    dn_sitename: str,
    focal_facilitytype: str,
    lookback_minutes: int,
    results: list[dict],
) -> None:
    """단일 cross-rule 검증 후 results에 추가."""
    up_tagsns = _resolve_gc_tags(
        up_info["tag_map"], rule["upstream_output_gc"],
        rule.get("upstream_output_gc_fallback", []),
    )
    dn_tagsns = _resolve_gc_tags(
        dn_info["tag_map"], rule["downstream_input_gc"],
        rule.get("downstream_input_gc_fallback", []),
    )
    if not up_tagsns or not dn_tagsns:
        return

    try:
        up_data = query_func(up_tagsns, lookback_minutes)
        dn_data = query_func(dn_tagsns, lookback_minutes)
    except Exception:
        return

    up_values = [v for vals in up_data.values() for v in vals]
    dn_values = [v for vals in dn_data.values() for v in vals]
    if not up_values or not dn_values:
        return

    up_active = _calc_active_rate(up_values)
    dn_active = _calc_active_rate(dn_values)

    # 상류 85%+ 활성인데 하류 15% 미만 → 단절 이상
    is_mismatch = up_active >= _CROSS_ACTIVE_HIGH and dn_active < _CROSS_ACTIVE_LOW

    up_mean = sum(up_values) / len(up_values) if up_values else 0
    dn_mean = sum(dn_values) / len(dn_values) if dn_values else 0

    if is_mismatch:
        detail = (
            f"<<error:시설간단절>> {up_sitename}({rule['upstream_facilitytype']}) "
            f"→ {dn_sitename}({rule['downstream_facilitytype']}): {rule['label']}\n"
            f"  상류 {rule['upstream_output_gc']} 평균={up_mean:.2f} (활성 {up_active*100:.0f}%), "
            f"하류 {rule['downstream_input_gc']} 평균={dn_mean:.2f} (활성 {dn_active*100:.0f}%)"
        )
    else:
        detail = (
            f"<<ok:시설간연동정상>> {up_sitename}({rule['upstream_facilitytype']}) "
            f"→ {dn_sitename}({rule['downstream_facilitytype']}): {rule['label']} 정상"
        )

    results.append({
        "rule_label": rule["label"],
        "pattern": rule["mismatch_pattern"] if is_mismatch else "CROSS_NORMAL",
        "upstream_sitename": up_sitename,
        "upstream_facilitytype": rule["upstream_facilitytype"],
        "downstream_sitename": dn_sitename,
        "downstream_facilitytype": rule["downstream_facilitytype"],
        "upstream_active": up_active,
        "downstream_active": dn_active,
        "is_mismatch": is_mismatch,
        "detail": detail,
    })




# =============================================================================
# 시설간 교차 검증 (Cross-Facility Validation)
# =============================================================================
# 상류→하류 물리적 흐름 일관성 검사
# tb_facility_flow_map 기반, group_code 매칭
# =============================================================================

_FACILITY_OUTPUT_GROUPS: dict[str, list[str]] = {
    "배수지": ["FLOW_OUTLET"],
    "가압장": ["FLOW_OUTLET", "PRESSURE_DISCHARGE"],
    "감압시설": ["PRESSURE_OUTLET"],
    "소블록": ["FLOW_OUTLET", "FLOW_INSTANT"],
    "소소블록": ["FLOW_OUTLET", "FLOW_INSTANT"],
}

_FACILITY_INPUT_GROUPS: dict[str, list[str]] = {
    "배수지": ["FLOW_INLET"],
    "가압장": ["FLOW_INLET"],
    "감압시설": ["PRESSURE_INLET"],
    "소블록": ["FLOW_INLET", "FLOW_INSTANT"],
    "소소블록": ["FLOW_INLET", "FLOW_INSTANT"],
}

# active_ratio 불일치 판정 임계값 (보수적: 전파 지연 오탐 방지)
_CROSS_ACTIVE_HIGH = 0.85  # 상류 85%+ 가동 중
_CROSS_ACTIVE_LOW = 0.15   # 하류 15% 미만 → 확실한 단절만


def _calc_active_rate(values: list[float], threshold: float = 0.01) -> float:
    """비제로(활성) 데이터 비율 계산."""
    if not values:
        return 0.0
    active = sum(1 for v in values if abs(v) > threshold)
    return active / len(values)


def _get_latest_value(tag_data: dict[str, list[float]]) -> Optional[float]:
    """태그 데이터에서 가장 마지막(최신) 값을 반환. 시계열 끝 = 최신."""
    latest: Optional[float] = None
    for values in tag_data.values():
        if values:
            latest = values[-1]  # _query_recent_values는 시간순 정렬
    return latest


def _calc_mean_direction(values: list[float]) -> str:
    """시계열 전반부 vs 후반부 평균 비교 → RISE/FALL/STABLE."""
    if len(values) < 4:
        return "UNKNOWN"
    mid = len(values) // 2
    first_half = sum(values[:mid]) / mid
    second_half = sum(values[mid:]) / (len(values) - mid)
    if abs(first_half) < 0.001:
        return "UNKNOWN"
    change_pct = (second_half - first_half) / abs(first_half) * 100
    if change_pct > 5:
        return "RISE"
    elif change_pct < -5:
        return "FALL"
    return "STABLE"


def _collect_tags_for_groups(
    tag_map: dict[str, list[str]], groups: list[str],
) -> list[str]:
    """group_code 리스트에 해당하는 tagsn 수집."""
    result = []
    for g in groups:
        result.extend(tag_map.get(g, []))
    return result


def _check_edge(
    query_func,
    us_tagsns: list[str],
    ds_tagsns: list[str],
    lookback_minutes: int,
) -> tuple[float, float, str, str, list[dict]]:
    """상류/하류 태그 쌍의 가동률 + 방향 비교.

    Returns: (us_active, ds_active, us_dir, ds_dir, checks)
    """
    try:
        us_data = query_func(us_tagsns, lookback_minutes)
        ds_data = query_func(ds_tagsns, lookback_minutes)
    except Exception:
        return 0.0, 0.0, "UNKNOWN", "UNKNOWN", []

    us_values = [v for vals in us_data.values() for v in vals]
    ds_values = [v for vals in ds_data.values() for v in vals]

    if not us_values or not ds_values:
        return 0.0, 0.0, "UNKNOWN", "UNKNOWN", []

    us_active = _calc_active_rate(us_values)
    ds_active = _calc_active_rate(ds_values)
    us_dir = _calc_mean_direction(us_values)
    ds_dir = _calc_mean_direction(ds_values)

    checks: list[dict] = []

    # 가동률 불일치: 상류 고가동 + 하류 저가동
    if us_active >= _CROSS_ACTIVE_HIGH and ds_active < _CROSS_ACTIVE_LOW:
        checks.append({
            "type": "active_ratio",
            "upstream_active": round(us_active * 100, 1),
            "downstream_active": round(ds_active * 100, 1),
        })

    # 방향 역전: 제거 — 유량 전파 지연으로 일시적 역전이 정상 패턴
    # (상류 펌프 가동 → 하류 도달까지 수십 분 소요 시 RISE vs STABLE/FALL)

    # 하류 비활성: 상류 유량 > 0인데 하류 평균값 ≈ 0 (공급 단절 의심)
    if us_active >= _CROSS_ACTIVE_HIGH:
        # 하류 활성값만 필터링 (0이 아닌 값)
        ds_nonzero = [v for v in ds_values if abs(v) > 0.01]
        if not ds_nonzero:
            # 하류 데이터 전부 0 또는 근사 0
            us_mean = sum(us_values) / len(us_values) if us_values else 0
            checks.append({
                "type": "downstream_zero",
                "upstream_mean": round(us_mean, 2),
                "downstream_nonzero_count": 0,
                "downstream_total_count": len(ds_values),
            })

    # 인과 기대 위반 (급락): 상류 활성 + 하류 전반부 활성 → 후반부 정지
    # 남산11 패턴: [872, 877, 439, 0, 0, 0, 0, 0, 0] — 상류가 보내는데 하류가 갑자기 멈춤
    if us_active >= _CROSS_ACTIVE_HIGH and len(ds_values) >= 4:
        mid = len(ds_values) // 2
        first_half = ds_values[:mid]
        second_half = ds_values[mid:]
        first_active = _calc_active_rate(first_half)
        second_active = _calc_active_rate(second_half)
        # 전반부 35%+ 활성 → 후반부 10%- 정지: 인과적으로 비정상
        if first_active >= 0.35 and second_active < 0.1:
            first_mean = sum(abs(v) for v in first_half) / len(first_half)
            checks.append({
                "type": "sudden_drop",
                "first_half_active_pct": round(first_active * 100, 1),
                "second_half_active_pct": round(second_active * 100, 1),
                "first_half_mean": round(first_mean, 2),
                "upstream_active_pct": round(us_active * 100, 1),
            })

    # 최근 비활성: 상류 활성인데 하류 최근 60분이 전부 0
    # 전체 윈도우에서 일부 활성이 있어도 "지금" 죽어있으면 이상
    _RECENT_MINUTES = 60
    _recent_count = min(_RECENT_MINUTES, len(ds_values))
    if (us_active >= _CROSS_ACTIVE_HIGH and _recent_count >= 10
            and not any(c["type"] == "downstream_zero" for c in checks)):
        recent_tail = ds_values[-_recent_count:]
        recent_active = _calc_active_rate(recent_tail)
        if recent_active < 0.05:  # 최근 60분 5% 미만 활성 → 사실상 정지
            us_mean = sum(us_values) / len(us_values) if us_values else 0
            checks.append({
                "type": "recent_inactive",
                "recent_minutes": _RECENT_MINUTES,
                "recent_active_pct": round(recent_active * 100, 1),
                "overall_active_pct": round(ds_active * 100, 1),
                "upstream_active_pct": round(us_active * 100, 1),
                "upstream_mean": round(us_mean, 2),
            })

    return us_active, ds_active, us_dir, ds_dir, checks


def cross_facility_check_single(
    query_func,
    target_sitename: str,
    target_facilitytype: str,
    causal_index: dict,
    lookback_minutes: int = 180,
) -> Optional[dict]:
    """단일 시설의 상류/하류 교차 검증.

    ANOMALY_FACILITY_DETAIL에서 자동 호출.
    """
    key = (target_sitename, target_facilitytype)
    info = causal_index.get(key)
    if not info:
        return None

    upstream_list = info.get("upstream", [])
    downstream_list = info.get("downstream", [])
    if not upstream_list and not downstream_list:
        return None

    mismatches: list[dict] = []
    input_groups = _FACILITY_INPUT_GROUPS.get(target_facilitytype, [])
    output_groups = _FACILITY_OUTPUT_GROUPS.get(target_facilitytype, [])

    # 상류 검증: upstream output → 자신 input
    for us_key in upstream_list:
        us_info = causal_index.get(us_key)
        if not us_info or not isinstance(us_key, tuple) or len(us_key) != 2:
            continue
        us_out_groups = _FACILITY_OUTPUT_GROUPS.get(us_key[1], [])
        us_tagsns = _collect_tags_for_groups(us_info["tag_map"], us_out_groups)
        my_tagsns = _collect_tags_for_groups(info["tag_map"], input_groups)
        if not us_tagsns or not my_tagsns:
            continue

        us_active, my_active, us_dir, my_dir, checks = _check_edge(
            query_func, us_tagsns, my_tagsns, lookback_minutes,
        )
        if checks:
            mismatches.append({
                "relation": "upstream",
                "peer_sitename": us_key[0],
                "peer_facilitytype": us_key[1],
                "peer_active_pct": round(us_active * 100, 1),
                "self_active_pct": round(my_active * 100, 1),
                "checks": checks,
            })

    # 하류 검증: 자신 output → downstream input
    for ds_key in downstream_list:
        ds_info = causal_index.get(ds_key)
        if not ds_info or not isinstance(ds_key, tuple) or len(ds_key) != 2:
            continue
        ds_in_groups = _FACILITY_INPUT_GROUPS.get(ds_key[1], [])
        my_out_tagsns = _collect_tags_for_groups(info["tag_map"], output_groups)
        ds_tagsns = _collect_tags_for_groups(ds_info["tag_map"], ds_in_groups)
        if not my_out_tagsns or not ds_tagsns:
            continue

        my_active, ds_active, my_dir, ds_dir, checks = _check_edge(
            query_func, my_out_tagsns, ds_tagsns, lookback_minutes,
        )
        if checks:
            mismatches.append({
                "relation": "downstream",
                "peer_sitename": ds_key[0],
                "peer_facilitytype": ds_key[1],
                "self_active_pct": round(my_active * 100, 1),
                "peer_active_pct": round(ds_active * 100, 1),
                "checks": checks,
            })

    if not mismatches:
        return {"has_mismatch": False, "mismatches": [], "summary": ""}

    return {
        "has_mismatch": True,
        "mismatches": mismatches,
        "summary": f"교차 검증 불일치 {len(mismatches)}건",
    }


def cross_facility_check_all(
    query_func,
    causal_index: dict,
    lookback_minutes: int = 180,
) -> list[dict]:
    """전체 시설 교차 검증 스캔 (ANOMALY_CROSS_FACILITY 인텐트).

    모든 flow_map 엣지를 순회하며 불일치를 찾는다.
    """
    checked_edges: set[tuple] = set()
    all_mismatches: list[dict] = []

    for key, info in causal_index.items():
        if not isinstance(key, tuple) or len(key) != 2:
            continue
        sn, ft = key
        out_groups = _FACILITY_OUTPUT_GROUPS.get(ft, [])
        if not out_groups:
            continue

        us_tagsns = _collect_tags_for_groups(info["tag_map"], out_groups)
        if not us_tagsns:
            continue

        for ds_key in info.get("downstream", []):
            edge = (key, ds_key)
            if edge in checked_edges:
                continue
            checked_edges.add(edge)

            ds_info = causal_index.get(ds_key)
            if not ds_info:
                continue

            ds_in_groups = _FACILITY_INPUT_GROUPS.get(ds_key[1], [])
            ds_tagsns = _collect_tags_for_groups(ds_info["tag_map"], ds_in_groups)
            if not ds_tagsns:
                continue

            us_active, ds_active, us_dir, ds_dir, checks = _check_edge(
                query_func, us_tagsns, ds_tagsns, lookback_minutes,
            )
            if checks:
                all_mismatches.append({
                    "upstream_sitename": sn,
                    "upstream_facilitytype": ft,
                    "downstream_sitename": ds_key[0],
                    "downstream_facilitytype": ds_key[1],
                    "upstream_active_pct": round(us_active * 100, 1),
                    "downstream_active_pct": round(ds_active * 100, 1),
                    "upstream_direction": us_dir,
                    "downstream_direction": ds_dir,
                    "checks": checks,
                })

    return all_mismatches


def build_cross_facility_detail_block(result: Optional[dict]) -> list:
    """단일 시설 교차 검증 결과 → detail block 포맷 (ANOMALY_FACILITY_DETAIL용)."""
    if not result or not result.get("has_mismatch"):
        return []

    items: list[dict] = []
    items.append({"prefix": "", "text": "─── 시설간 교차 검증 ───"})

    for m in result["mismatches"]:
        rel = "▲ 상류" if m["relation"] == "upstream" else "▼ 하류"
        peer = f"{m['peer_sitename']} {m['peer_facilitytype']}"

        for c in m["checks"]:
            if c["type"] == "active_ratio":
                items.append({
                    "prefix": "⚠",
                    "text": _wrap_marker(
                        f"{rel} {peer}: 가동률 {c['upstream_active']}% → {c['downstream_active']}% (불일치)",
                        "error",
                    ),
                })
            elif c["type"] == "direction":
                items.append({
                    "prefix": "⚠",
                    "text": _wrap_marker(
                        f"{rel} {peer}: 상류 {c['upstream_direction']} / 하류 {c['downstream_direction']} (역방향)",
                        "error",
                    ),
                })
            elif c["type"] == "downstream_zero":
                items.append({
                    "prefix": "⚠",
                    "text": _wrap_marker(
                        f"{rel} {peer}: 상류 평균 {c['upstream_mean']} → 하류 전부 0 (공급 단절 의심)",
                        "error",
                    ),
                })
            elif c["type"] == "snapshot_zero":
                items.append({
                    "prefix": "⚠",
                    "text": _wrap_marker(
                        f"{rel} {peer}: 상류 현재 {c['upstream_latest']} → 하류 현재 0 "
                        f"(하류 가동률 {c['downstream_active_pct']}%)",
                        "warn",
                    ),
                })
            elif c["type"] == "sudden_drop":
                items.append({
                    "prefix": "⚠",
                    "text": _wrap_marker(
                        f"{rel} {peer}: 하류 유량 급락 "
                        f"(전반 {c['first_half_active_pct']}% → 후반 {c['second_half_active_pct']}%)",
                        "error",
                    ),
                })
            elif c["type"] == "recent_inactive":
                items.append({
                    "prefix": "⚠",
                    "text": _wrap_marker(
                        f"{rel} {peer}: 최근 {c['recent_minutes']}분 비활성 "
                        f"(전체 {c['overall_active_pct']}% / 최근 {c['recent_active_pct']}%)",
                        "error",
                    ),
                })

    return items


import re as _re_for_pills  # 마커→pill 후처리용
_MARKER_RE_CF = _re_for_pills.compile(r"<<(ok|warn|error|critical|info):([^>]+)>>")
_TONE_NORM_CF = {"error": "critical", "critical": "critical", "warn": "warn", "ok": "ok", "info": "info"}
_PILL_LABEL_CF = {"critical": "이상", "warn": "주의", "ok": "정상", "info": "정보"}


def _lift_markers_cf(items: list) -> list:
    """분석형 detail items 의 <<tone:label>> 마커를 pill 로 승격."""
    for it in items:
        if not isinstance(it, dict):
            continue
        text = it.get("text") or ""
        m = _MARKER_RE_CF.search(text)
        if not m:
            continue
        tone = _TONE_NORM_CF.get(m.group(1), "info")
        it["text"] = _MARKER_RE_CF.sub(lambda mo: mo.group(2), text, count=1)
        label = _PILL_LABEL_CF.get(tone, "")
        if label and "pill" not in it:
            it["pill"] = {"text": label, "tone": tone}
    return items


def build_cross_facility_scan_block(mismatches: list[dict]) -> tuple[list, dict]:
    """전체 스캔 결과 → (detail_block, data) (ANOMALY_CROSS_FACILITY용)."""
    data = {
        "mismatch_count": len(mismatches),
    }

    items: list[dict] = []
    if not mismatches:
        items.append({
            "prefix": "✓",
            "text": _wrap_marker("전체 시설간 교차 검증 정상 — 유량 흐름 불일치 없음", "ok"),
        })
        return items, data

    items.append({
        "prefix": "",
        "text": _wrap_marker(f"교차 검증 불일치 {len(mismatches)}건 감지", "error"),
    })

    for i, m in enumerate(mismatches, 1):
        us = f"{m['upstream_sitename']} {m['upstream_facilitytype']}"
        ds = f"{m['downstream_sitename']} {m['downstream_facilitytype']}"
        items.append({"prefix": f"[{i}]", "text": f"{us} → {ds}"})

        for c in m["checks"]:
            if c["type"] == "active_ratio":
                items.append({
                    "prefix": "",
                    "text": _wrap_marker(
                        f"  가동률: 상류 {c['upstream_active']}% / 하류 {c['downstream_active']}%",
                        "error",
                    ),
                })
            elif c["type"] == "direction":
                items.append({
                    "prefix": "",
                    "text": _wrap_marker(
                        f"  방향: 상류 {c['upstream_direction']} / 하류 {c['downstream_direction']} (역전)",
                        "error",
                    ),
                })
            elif c["type"] == "downstream_zero":
                items.append({
                    "prefix": "",
                    "text": _wrap_marker(
                        f"  상류 평균 {c['upstream_mean']} → 하류 전부 0 (공급 단절 의심)",
                        "error",
                    ),
                })
            elif c["type"] == "snapshot_zero":
                items.append({
                    "prefix": "",
                    "text": _wrap_marker(
                        f"  현재 시점: 상류 {c['upstream_latest']} / 하류 0 "
                        f"(하류 가동률 {c['downstream_active_pct']}%)",
                        "warn",
                    ),
                })
            elif c["type"] == "sudden_drop":
                items.append({
                    "prefix": "",
                    "text": _wrap_marker(
                        f"  유량 급락: 전반 {c['first_half_active_pct']}% → 후반 {c['second_half_active_pct']}%",
                        "error",
                    ),
                })
            elif c["type"] == "recent_inactive":
                items.append({
                    "prefix": "",
                    "text": _wrap_marker(
                        f"  최근 {c['recent_minutes']}분 비활성: 전체 {c['overall_active_pct']}% / 최근 {c['recent_active_pct']}%",
                        "error",
                    ),
                })

        items.append({"prefix": "", "text": ""})  # 빈 줄 구분

    return _lift_markers_cf(items), data


# ─── 시설간 다중 홉 전파 추적 ───


def trace_propagation_forward(
    query_func,
    start_key: tuple[str, str],
    causal_index: dict,
    max_depth: int = 3,
    lookback_minutes: int = 180,
) -> dict:
    """start_key에서 하류 방향으로 BFS 전파 추적 (max_depth 홉).

    각 홉마다 _check_edge()로 출력↔입력 비교.
    Returns: {hops: [{hop, from, to, status, checks}], propagation_stopped_at}
    """
    visited: set[tuple[str, str]] = {start_key}
    queue: list[tuple[tuple[str, str], int]] = [(start_key, 0)]
    hops: list[dict] = []
    stopped_at: str | None = None

    while queue:
        current, depth = queue.pop(0)
        if depth >= max_depth:
            break

        info = causal_index.get(current)
        if not info:
            continue

        out_groups = _FACILITY_OUTPUT_GROUPS.get(current[1], [])
        us_tagsns = _collect_tags_for_groups(info["tag_map"], out_groups)
        if not us_tagsns:
            continue

        for ds_key in info.get("downstream", []):
            if ds_key in visited:
                continue
            visited.add(ds_key)

            ds_info = causal_index.get(ds_key)
            if not ds_info:
                continue

            ds_in_groups = _FACILITY_INPUT_GROUPS.get(ds_key[1], [])
            ds_tagsns = _collect_tags_for_groups(ds_info["tag_map"], ds_in_groups)
            if not ds_tagsns:
                continue

            us_active, ds_active, us_dir, ds_dir, checks = _check_edge(
                query_func, us_tagsns, ds_tagsns, lookback_minutes,
            )

            status = "mismatch" if checks else "normal"
            hops.append({
                "hop": depth + 1,
                "from_sitename": current[0],
                "from_facilitytype": current[1],
                "to_sitename": ds_key[0],
                "to_facilitytype": ds_key[1],
                "status": status,
                "checks": checks,
                "upstream_active_pct": round(us_active * 100, 1),
                "downstream_active_pct": round(ds_active * 100, 1),
            })

            if checks:
                # 불일치 발견 → 전파 중단 지점 기록
                stopped_at = f"{ds_key[0]} {ds_key[1]}"
            else:
                # 정상 → 하류로 계속 탐색
                queue.append((ds_key, depth + 1))

    return {"hops": hops, "propagation_stopped_at": stopped_at}


def trace_upstream_root_cause(
    query_func,
    target_key: tuple[str, str],
    causal_index: dict,
    max_depth: int = 3,
    lookback_minutes: int = 180,
) -> dict:
    """target_key에서 상류 방향으로 BFS 역추적하여 근원지를 찾는다.

    가장 먼 상류에서 이상 신호(checks)가 있는 시설 = root_cause candidate.
    Returns: {root_cause, path, hops, confidence}
    """
    visited: set[tuple[str, str]] = {target_key}
    queue: list[tuple[tuple[str, str], int, list[tuple[str, str]]]] = [
        (target_key, 0, [target_key]),
    ]
    hops: list[dict] = []
    root_cause: dict | None = None

    while queue:
        current, depth, path = queue.pop(0)
        if depth >= max_depth:
            break

        info = causal_index.get(current)
        if not info:
            continue

        in_groups = _FACILITY_INPUT_GROUPS.get(current[1], [])
        my_tagsns = _collect_tags_for_groups(info["tag_map"], in_groups)

        for us_key in info.get("upstream", []):
            if us_key in visited:
                continue
            visited.add(us_key)

            us_info = causal_index.get(us_key)
            if not us_info:
                continue

            us_out_groups = _FACILITY_OUTPUT_GROUPS.get(us_key[1], [])
            us_tagsns = _collect_tags_for_groups(us_info["tag_map"], us_out_groups)
            if not us_tagsns or not my_tagsns:
                continue

            us_active, my_active, us_dir, my_dir, checks = _check_edge(
                query_func, us_tagsns, my_tagsns, lookback_minutes,
            )

            new_path = [us_key] + path
            hop_info = {
                "hop": depth + 1,
                "from_sitename": us_key[0],
                "from_facilitytype": us_key[1],
                "to_sitename": current[0],
                "to_facilitytype": current[1],
                "status": "mismatch" if checks else "normal",
                "checks": checks,
                "upstream_active_pct": round(us_active * 100, 1),
                "downstream_active_pct": round(my_active * 100, 1),
            }
            hops.append(hop_info)

            if checks:
                # 불일치 발견 → 이 상류가 근원지 후보 (더 먼 상류 탐색 계속)
                root_cause = {
                    "sitename": us_key[0],
                    "facilitytype": us_key[1],
                    "depth": depth + 1,
                    "path": [f"{k[0]} {k[1]}" for k in new_path],
                    "checks": checks,
                }

            # 상류로 계속 탐색
            queue.append((us_key, depth + 1, new_path))

    confidence = "high" if root_cause and root_cause["depth"] >= 2 else "medium" if root_cause else "low"

    return {
        "root_cause": root_cause,
        "hops": hops,
        "confidence": confidence,
    }


def build_propagation_trace_block(
    forward: dict | None,
    backward: dict | None,
) -> list:
    """forward/backward trace 결과 → 시맨틱 마커 detail block 포맷."""
    items: list[dict] = []

    has_forward = forward and forward.get("hops")
    has_backward = backward and (backward.get("root_cause") or backward.get("hops"))

    if not has_forward and not has_backward:
        return items

    items.append({"prefix": "", "text": "─── 시설간 전파 추적 ───"})

    # 상류 역추적 (근원지)
    if has_backward:
        rc = backward.get("root_cause")
        if rc:
            rc_name = f"{rc['sitename']} {rc['facilitytype']}"
            items.append({
                "prefix": "🔍",
                "text": _wrap_marker(f"근원지 추정: {rc_name} (신뢰도: {backward['confidence']})", "warn"),
            })
            if rc.get("path"):
                items.append({
                    "prefix": "",
                    "text": f"  경로: {' → '.join(rc['path'])}",
                })
        # 각 홉 상세
        for h in backward.get("hops", []):
            src = f"{h['from_sitename']} {h['from_facilitytype']}"
            dst = f"{h['to_sitename']} {h['to_facilitytype']}"
            if h["status"] == "mismatch":
                check_types = [c["type"] for c in h.get("checks", [])]
                reason = "/".join(check_types)
                items.append({
                    "prefix": "▲",
                    "text": _wrap_marker(f"{src} → {dst}: 불일치 ({reason})", "error"),
                })
            else:
                items.append({
                    "prefix": "▲",
                    "text": _wrap_marker(f"{src} → {dst}: 정상", "ok"),
                })

    # 하류 전파 추적
    if has_forward:
        stopped = forward.get("propagation_stopped_at")
        if stopped:
            items.append({
                "prefix": "📍",
                "text": _wrap_marker(f"전파 중단 지점: {stopped}", "error"),
            })

        for h in forward.get("hops", []):
            src = f"{h['from_sitename']} {h['from_facilitytype']}"
            dst = f"{h['to_sitename']} {h['to_facilitytype']}"
            if h["status"] == "mismatch":
                check_types = [c["type"] for c in h.get("checks", [])]
                reason = "/".join(check_types)
                items.append({
                    "prefix": "▼",
                    "text": _wrap_marker(f"{src} → {dst}: 불일치 ({reason})", "error"),
                })
            else:
                items.append({
                    "prefix": "▼",
                    "text": _wrap_marker(f"{src} → {dst}: 정상 전파", "ok"),
                })

    return items
