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
from typing import Optional

logger = logging.getLogger(__name__)

# ── Z-Score 판정 임계값 ──────────────────────────────────────
Z_THRESHOLD_ERROR = 3.0   # |Z| >= 3.0 → 이상
Z_THRESHOLD_WARN = 2.0    # |Z| >= 2.0 → 주의

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


def build_anomaly_scan_detail_block(rows: list, columns: list) -> list:
    """
    ANOMALY_SCAN_ALL: SQL 결과를 이상 스캔 결과 리스트로 조립한다.
    이상/주의 항목만 표시 (정상은 생략). 통신장애 사이트는 별도 표시.
    """
    col_map = {c: i for i, c in enumerate(columns)}
    items = []

    has_active = "active_pct" in col_map
    has_comm = "comm_status" in col_map

    for row in rows:
        z_score = float(row[col_map["z_score"]] or 0)
        level = classify_z_level(z_score)
        if level == "정상":
            continue

        sitename = row[col_map["sitename"]] or ""
        facilitytype = row[col_map["facilitytype"]] or ""
        datainfo = row[col_map["datainfo"]] or ""
        deviation_pct = float(row[col_map["deviation_pct"]] or 0)
        active_pct = float(row[col_map["active_pct"]] or 100) if has_active else 100
        comm_status = (row[col_map["comm_status"]] or "정상") if has_comm else "정상"

        dev_text = format_deviation_text(deviation_pct, z_score, active_pct)
        marked_level = _wrap_marker(level, level)
        comm_tag = " <<error:[통신장애]>>" if comm_status == "통신장애" else ""
        text = f"{sitename} {facilitytype} {datainfo}: {marked_level} ({dev_text}){comm_tag}"
        items.append({"prefix": "-", "text": text})

    if not items:
        items.append({"prefix": "✓", "text": "<<ok:모든 센서가 정상 범위 내에 있습니다.>>"})

    return items


def build_anomaly_facility_detail_block(rows: list, columns: list) -> list:
    """
    ANOMALY_FACILITY_DETAIL: SQL 결과를 복합 진단 결과 리스트로 조립한다.
    Z-Score + Direction Change 복합 판정. 전 항목 표시.
    """
    col_map = {c: i for i, c in enumerate(columns)}
    has_dir = "change_count" in col_map
    has_active = "active_pct" in col_map
    items = []

    for row in rows:
        z_score = float(row[col_map["z_score"]] or 0)
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

        if has_dir and dir_level != "정상":
            text = f"{datainfo}: {marked_level} ({dev_text}, 진동 {change_count}회)"
        else:
            text = f"{datainfo}: {marked_level} ({dev_text})"

        items.append({"prefix": "-", "text": text})

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

        # MNF 기본 통계
        n = len(values)
        mean_val = sum(values) / n
        variance = sum((v - mean_val) ** 2 for v in values) / n
        stddev = variance ** 0.5

        if stddev < 0.001:
            # 변동 없는 데이터는 CUSUM 무의미
            continue

        # 최근 7일 평균 (MNF 현재 수준)
        recent_vals = values[-7:] if len(values) >= 7 else values
        recent_mean = sum(recent_vals) / len(recent_vals)

        # 추세 기울기 (단순 선형 회귀)
        x_mean = (n - 1) / 2.0
        slope_num = sum((i - x_mean) * (v - mean_val)
                        for i, v in enumerate(values))
        slope_den = sum((i - x_mean) ** 2 for i in range(n))
        trend_slope = slope_num / slope_den if slope_den > 0 else 0.0

        # CUSUM 상한 계산 (누수 = 야간유량 증가 감지)
        k = CUSUM_K_FACTOR * stddev
        cusum_upper = 0.0
        cusum_max = 0.0
        cusum_series = []

        for log_time, val in series:
            cusum_upper = max(0, cusum_upper + (val - mean_val - k))
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
