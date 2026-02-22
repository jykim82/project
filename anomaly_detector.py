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
        direction = "↑" if r["trend_slope"] > 0 else "↓" if r["trend_slope"] < 0 else "→"
        unit = r.get("unit") or ""
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
    # 누수의심 → 주의 → 정상 순서 정렬
    _status_order = {"누수의심": 0, "주의": 1, "정상": 2}
    table_rows.sort(key=lambda r: _status_order.get(r[7], 3))
    return table_rows, columns


def build_leak_cusum_detail_block(cusum_results: dict) -> list:
    """LEAK_CUSUM_ANALYSIS: CUSUM + MNF 결과를 시맨틱 마커로 포맷한다."""
    items = []
    _status_marker = {"누수의심": "error", "주의": "warn", "정상": "ok"}
    _status_order = {"누수의심": 0, "주의": 1, "정상": 2}

    sorted_tags = sorted(
        cusum_results.values(),
        key=lambda r: _status_order.get(r["leak_status"], 3)
    )

    for r in sorted_tags:
        status = r["leak_status"]
        marker = _status_marker.get(status, "ok")
        label = r.get("label", "")
        unit = r.get("unit", "")

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
    counts = {"누수의심": 0, "주의": 0, "정상": 0}
    for r in cusum_results.values():
        status = r.get("leak_status", "정상")
        counts[status] = counts.get(status, 0) + 1
    return counts
