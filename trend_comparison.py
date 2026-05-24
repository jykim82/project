"""trend_comparison.py — 트렌드 비교 지표 (평소 대비 / 향후 전망) 헬퍼.

사양: docs/trend-comparison-spec.md (v1, 2026-05-21)

핵심 설계:
- 사용자 의도 2개 — "평소 대비 이상한가?" / "이대로 가면 문제 생기나?"
- 트렌드 종류 (유량/수위/압력/수질) 와 무관한 동일 응답 구조 (ComparisonData)
- 상태 판정은 대시보드 z-score 알람 체계와 통일 (anomaly_detector 재사용)
- z 숫자 노출 X, "평소 대비 ±N%" 만 표시
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Optional

from anomaly_detector import (
    classify_z_level_by_group,
    format_deviation_text,
)

logger = logging.getLogger(__name__)

# 트렌드 종류 매핑 — intent → trend_kind
INTENT_TREND_KIND: dict[str, str] = {
    # 유량
    "BLOCK_FLOW_TREND":                        "flow",
    "FACILITY_FLOW_TREND":                     "flow",
    "FACILITY_NIGHT_MIN_FLOW_TREND":           "flow",
    "FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS": "flow",
    # 수위
    "RESERVOIR_LEVEL_TREND":                   "level",
    "FACILITY_LEVEL_TREND":                    "level",
    # 압력
    "FACILITY_PRESSURE_TREND":                 "pressure",
    "BLOCK_PRESSURE_TREND":                    "pressure",
    # 수질
    "WATER_QUALITY_TREND":                     "quality",
    "FACILITY_QUALITY_TREND":                  "quality",
}


def detect_trend_kind(
    intent: str,
    columns: list[str] | None = None,
    rows: list | None = None,
) -> Optional[str]:
    """intent → trend_kind. FACILITY_TREND 같은 범용 인텐트는 label/datainfo 로 추정."""
    kind = INTENT_TREND_KIND.get(intent)
    if kind:
        return kind
    # FACILITY_TREND / FACILITY_MIXED_TREND 는 컬럼명 + 첫 행의 label/datainfo 검사
    haystack_parts: list[str] = []
    if columns:
        haystack_parts.extend(c.lower() for c in columns)
        if rows:
            label_idx = next((i for i, c in enumerate(columns) if c.lower() in ("label", "datainfo")), None)
            if label_idx is not None:
                for r in rows[:50]:    # 첫 50행만
                    v = r[label_idx] if isinstance(r, (list, tuple)) else r.get(columns[label_idx])
                    if v:
                        haystack_parts.append(str(v).lower())
    joined = " ".join(haystack_parts)
    if any(k in joined for k in ("유량", "flow", "lps", "cmh", "lpm")):
        return "flow"
    if any(k in joined for k in ("수위", "level", "height")):
        return "level"
    if any(k in joined for k in ("압력", "pressure", "kgf", "bar")):
        return "pressure"
    if any(k in joined for k in ("탁도", "잔류염소", "ph", "turbidity", "chlorine")):
        return "quality"
    return None


def _parse_ts(s) -> Optional[datetime]:
    if s is None:
        return None
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _hourly_pattern_baseline(
    conn, tagsn: str, target_times: list[datetime], learning_days: int,
) -> Optional[tuple[list[Optional[float]], list[Optional[float]], list[Optional[float]], float]]:
    """경량 baseline — 같은 hour×weekday 의 학습 윈도우 평균·표준편차.

    Returns: (baseline_series, band_upper, band_lower, mean_stddev)
    또는 None (데이터 부족).
    """
    if not target_times:
        return None
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT date_trunc('hour', logtime) AS h, AVG(val) AS avg_v, STDDEV(val) AS sd_v
              FROM tb_tag_raw_data
             WHERE tagsn = %s
               AND logtime > NOW() - (%s || ' days')::interval
               AND val IS NOT NULL
             GROUP BY h
            """,
            (tagsn, str(learning_days)),
        )
        hourly_rows = cur.fetchall()
        if len(hourly_rows) < 24:        # 1일 미만 → 데이터 부족
            return None

        # (weekday, hour) → 평균·σ
        bucket: dict[tuple[int, int], list[float]] = {}
        for h, avg_v, _sd in hourly_rows:
            if avg_v is None or h is None:
                continue
            key = (h.weekday(), h.hour)
            bucket.setdefault(key, []).append(float(avg_v))

        baseline_pattern: dict[tuple[int, int], tuple[float, float]] = {}
        for key, vals in bucket.items():
            n = len(vals)
            m = sum(vals) / n
            v = sum((x - m) ** 2 for x in vals) / max(1, n - 1) if n > 1 else 0.0
            baseline_pattern[key] = (m, math.sqrt(v))

        stddevs: list[float] = []
        baseline_series: list[Optional[float]] = []
        band_upper: list[Optional[float]] = []
        band_lower: list[Optional[float]] = []
        for t in target_times:
            key = (t.weekday(), t.hour)
            if key in baseline_pattern:
                m, s = baseline_pattern[key]
                baseline_series.append(round(m, 3))
                band_upper.append(round(m + 2 * s, 3))
                band_lower.append(round(m - 2 * s, 3))
                stddevs.append(s)
            else:
                baseline_series.append(None)
                band_upper.append(None)
                band_lower.append(None)

        mean_stddev = (sum(stddevs) / len(stddevs)) if stddevs else 0.0
        return baseline_series, band_upper, band_lower, mean_stddev
    finally:
        cur.close()


def _lookup_site_group(conn, sitename: str, facilitytype: str) -> str:
    """tb_site_anomaly_profile 의 site_group (A/B/C/D). 없으면 'B'."""
    if not sitename:
        return "B"
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT site_group FROM tb_site_anomaly_profile "
            "WHERE sitename = %s AND (facilitytype = %s OR %s IS NULL) "
            "LIMIT 1",
            (sitename, facilitytype, facilitytype),
        )
        r = cur.fetchone()
        return (r[0] if r and r[0] in ("A", "B", "C", "D") else "B")
    except Exception:
        return "B"
    finally:
        cur.close()


def _lookup_threshold(conn, trend_kind: str, sitename: str, facilitytype: str) -> tuple[Optional[float], Optional[str]]:
    """trend_kind 별 위험 임계값 + 라벨. 임계 미설정 시 (None, None)."""
    if not sitename:
        return None, None
    cur = conn.cursor()
    try:
        if trend_kind == "level":
            cur.execute(
                "SELECT zone_1_height FROM tb_service_reservoir_info "
                "WHERE sitename = %s LIMIT 1",
                (sitename,),
            )
            r = cur.fetchone()
            if r and r[0]:
                # HH 근사 = 만수위의 90%
                return round(float(r[0]) * 0.9, 2), "운영 한계 (HH)"
        elif trend_kind == "pressure":
            cur.execute(
                "SELECT critical_pressure FROM tb_block_info "
                "WHERE sitename = %s LIMIT 1",
                (sitename,),
            )
            r = cur.fetchone()
            if r and r[0]:
                return round(float(r[0]), 2), "최소 운영 압력"
        elif trend_kind == "quality":
            # 잔류염소 0.1 mg/L (수도법 최저)
            return 0.1, "수질 기준 (잔류염소)"
        elif trend_kind == "flow":
            # NMF baseline + 3σ 는 baseline 계산 후 외부에서 보정
            return None, "누수 의심 한계 (NMF)"
        return None, None
    except Exception:
        return None, None
    finally:
        cur.close()


def _linear_forecast(
    times: list[datetime], vals: list[Optional[float]],
    forecast_hours: int = 24, step_minutes: int = 30,
) -> tuple[list[str], list[float], Optional[float]]:
    """단순 선형회귀 외삽. 최근 ~24 샘플로 slope+intercept 계산.

    Returns: (forecast_iso_times, forecast_values, slope_per_hour)
    """
    pairs = [(t, v) for t, v in zip(times, vals) if v is not None]
    pairs = pairs[-24:]
    if len(pairs) < 6:
        return [], [], None
    t0 = pairs[0][0]
    xs = [(t - t0).total_seconds() / 3600.0 for t, _ in pairs]
    ys = [v for _, v in pairs]
    n = len(xs)
    mean_x = sum(xs) / n; mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    if den == 0:
        return [], [], 0.0
    slope = num / den
    intercept = mean_y - slope * mean_x
    last_t = pairs[-1][0]
    out_times: list[str] = []
    out_vals: list[float] = []
    steps = int(forecast_hours * 60 / step_minutes)
    for i in range(1, steps + 1):
        t = last_t + timedelta(minutes=step_minutes * i)
        x = (t - t0).total_seconds() / 3600.0
        out_times.append(t.isoformat())
        out_vals.append(round(intercept + slope * x, 3))
    return out_times, out_vals, round(slope, 4)


def _hours_to_threshold(
    last_val: float, slope_per_hour: float, threshold: float, forecast_hours: int,
) -> Optional[float]:
    """현재값에서 임계까지 도달 예상 시간 (시). 추세가 임계 반대 방향이면 None."""
    if slope_per_hour == 0:
        return None
    if threshold > last_val:    # 상승 위험
        if slope_per_hour <= 0:
            return None
        h = (threshold - last_val) / slope_per_hour
    else:                       # 하락 위험
        if slope_per_hour >= 0:
            return None
        h = (threshold - last_val) / slope_per_hour
    if h < 0:
        return None
    if h > forecast_hours * 10:
        return None             # 너무 멀면 무의미
    return round(h, 1)


def _baseline_status(
    deviation_pct: float, z_score: float, group: str,
) -> tuple[str, str]:
    """z-score 기반 상태 + 라벨. 대시보드 alarms 와 통일."""
    z_level = classify_z_level_by_group(z_score, group)
    status = {"정상": "normal", "주의": "warning", "이상": "alert"}.get(z_level, "normal")
    if z_level == "정상":
        label = "정상"
    else:
        direction = "↑" if z_score > 0 else "↓"
        label = f"{z_level} · 평소보다 {abs(deviation_pct):.1f}% {direction}"
    return status, label


def compute_causal_hint(
    conn, sitename: Optional[str], facilitytype: Optional[str],
    region: str = "R01", hours: int = 6,
) -> Optional[dict]:
    """이상 신호 발생 시 인과 후보 (B안 — `feedback_remember_completion` 패턴).

    상류 시설 (`tb_facility_flow_map`) 의 최근 N시간 알람·운영 변화를 요약.
    트렌드 비교에서 baseline.status 가 warning/alert 일 때만 호출 권장.

    Returns: {
      summary: "전단 신평(가) 알람 3건 / 상류 송악(배) 출수량 -28%",
      sources: [{sitename, facilitytype, kind: 'alarm'|'flow_change', detail: str}],
      chat_intent: "FACILITY_ALARM_CAUSE_DIAGNOSIS_RANK",   # 후속 진단 인텐트
    } 또는 None (인과 데이터 없음).
    """
    if not sitename:
        return None
    cur = conn.cursor()
    try:
        # 1) 상류 시설 조회 (사용자 시설이 downstream 인 경우)
        cur.execute(
            """
            SELECT DISTINCT upstream_sitename, upstream_facilitytype
              FROM tb_facility_flow_map
             WHERE downstream_sitename = %s
               AND (downstream_facilitytype = %s OR %s IS NULL)
            """,
            (sitename, facilitytype, facilitytype),
        )
        upstreams = [(r[0], r[1]) for r in cur.fetchall()]
        if not upstreams:
            cur.close()
            return None

        sources: list = []
        # 2) 상류 시설별 최근 알람 count (tb_tag_raw_data 에서 알람 태그 변화)
        try:
            up_sites = [u[0] for u in upstreams]
            cur.execute(
                """
                SELECT t.sitename, COUNT(*) AS alarm_count
                  FROM tb_tag_raw_data r
                  JOIN tb_tag_info t ON t.tagsn = r.tagsn
                 WHERE t.sitename = ANY(%s)
                   AND COALESCE(t.alarm_tag_yn, 0) = 1
                   AND r.logtime > NOW() - (%s || ' hours')::interval
                   AND r.val::numeric > 0
                 GROUP BY t.sitename
                """,
                (up_sites, str(max(1, min(24, hours)))),
            )
            for sn, cnt in cur.fetchall():
                if cnt and cnt > 0:
                    ft = next((u[1] for u in upstreams if u[0] == sn), "")
                    sources.append({
                        "sitename": sn, "facilitytype": ft,
                        "kind": "alarm",
                        "detail": f"최근 {hours}시간 알람 {cnt}건",
                    })
        except Exception:
            pass

        # 3) 상류 outflow 운영 변화 (tb_epanet_facility_flow_map 매핑된 시설 한정)
        try:
            cur.execute(
                """
                SELECT m.sitename, m.facilitytype, m.tagsn, m.unit, m.scale,
                       AVG(r.val) FILTER (WHERE r.logtime > NOW() - (%s || ' hours')::interval) AS recent_v,
                       AVG(r.val) FILTER (WHERE r.logtime < NOW() - (%s || ' hours')::interval
                                          AND r.logtime > NOW() - '7 days'::interval) AS base_v
                  FROM tb_epanet_facility_flow_map m
                  JOIN tb_tag_raw_data r ON r.tagsn = m.tagsn
                 WHERE m.region = %s AND m.enabled = 'Y'
                   AND (m.sitename, m.facilitytype) = ANY(%s::record[])
                   AND m.role = 'outflow'
                 GROUP BY m.sitename, m.facilitytype, m.tagsn, m.unit, m.scale
                """,
                (str(hours), str(hours), region,
                 [(u[0], u[1]) for u in upstreams]),
            )
            for sn, ft, _ts, _u, _s, recent_v, base_v in cur.fetchall():
                if recent_v is None or base_v is None or float(base_v) == 0:
                    continue
                pct = (float(recent_v) - float(base_v)) / float(base_v) * 100
                if abs(pct) >= 15:    # ±15% 이상 변화만
                    direction = "↑" if pct > 0 else "↓"
                    sources.append({
                        "sitename": sn, "facilitytype": ft,
                        "kind": "flow_change",
                        "detail": f"출수량 {abs(pct):.0f}% {direction} (7일 평균 대비)",
                    })
        except Exception:
            pass

        cur.close()
        if not sources:
            return None
        # 요약 문자열
        parts: list[str] = []
        for s in sources[:3]:
            parts.append(f"{s['sitename']}({s.get('facilitytype','')}) {s['detail']}")
        summary = " / ".join(parts)
        return {
            "summary": summary,
            "sources": sources,
            "chat_intent": "FACILITY_ALARM_CAUSE_DIAGNOSIS_RANK",
        }
    except Exception as e:
        logger.debug(f"causal_hint 실패: {e}")
        return None
    finally:
        try: cur.close()
        except Exception: pass


def _forecast_status(hours_to_threshold: Optional[float]) -> tuple[str, str]:
    if hours_to_threshold is None:
        return "normal", "안전 (24시간+)"
    if hours_to_threshold > 24:
        return "normal", "안전 (24시간+)"
    if hours_to_threshold > 6:
        return "warning", f"{hours_to_threshold:.0f}시간 후 한계 접근"
    return "alert", f"{hours_to_threshold:.1f}시간 후 한계 도달"


def _is_skip_target(conn, tagsn: str) -> Optional[str]:
    """비교 의미 없는 대상 (적산/디지털/누적 단위) 인지 판별.

    사양: docs/trend-comparison-spec.md §4.5
    Returns: skip 사유 문자열 또는 None.
    """
    if not tagsn:
        return None
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT tagtype, datainfo, unit FROM tb_tag_info WHERE tagsn = %s",
            (tagsn,),
        )
        r = cur.fetchone()
        cur.close()
        if not r:
            return None
        tagtype, datainfo, unit = (r[0] or ""), (r[1] or ""), (r[2] or "")
        # 디지털
        if "Digital" in tagtype:
            return "digital_input"
        # 적산 — datainfo 패턴
        if "적산" in datainfo or "누적" in datainfo or "총량" in datainfo:
            return "accumulated_flow"
        # 누적 단위
        u = unit.strip().lower()
        if u in ("m³", "m3", "kwh", "kg") or u.endswith("·일"):
            return "cumulative_unit"
        return None
    except Exception:
        return None


def compute_comparison(
    rows: list, columns: list[str],
    intent: str,
    sitename: Optional[str], facilitytype: Optional[str],
    tagsn: Optional[str],
    conn,
    learning_days: int = 14,
    forecast_hours: int = 24,
) -> Optional[dict]:
    """트렌드 비교 응답 생성 (ComparisonData dict).

    데이터 부족 시 None. baseline 계산 가능하면 baseline 만이라도 반환.
    적산/디지털/누적 단위 등 비교 의미 없는 대상은 자동 skip (§4.5).
    """
    trend_kind = detect_trend_kind(intent, columns, rows)
    if not trend_kind:
        logger.debug(f"trend_comparison: trend_kind 감지 실패 intent={intent}")
        return None
    if not rows or not columns or not tagsn:
        return None
    # §4.5 skip 가드 — 적산/디지털/누적 단위
    skip_reason = _is_skip_target(conn, tagsn)
    if skip_reason:
        logger.debug(f"trend_comparison: skip {tagsn} ({skip_reason})")
        return None
    # log_time / val 컬럼 추출 — dict row / tuple row 모두 지원
    cols_lower = [c.lower() for c in columns]
    t_keys = ("log_time", "logtime", "ts", "time", "datetime", "t")
    v_keys = ("val", "value", "v", "reading", "measurement")
    t_idx = next((i for i, c in enumerate(cols_lower) if c in t_keys), None)
    v_idx = next((i for i, c in enumerate(cols_lower) if c in v_keys), None)
    if t_idx is None or v_idx is None:
        logger.debug(f"trend_comparison: 필수 컬럼 없음 (columns={columns})")
        return None

    times: list[datetime] = []
    vals: list[Optional[float]] = []
    for r in rows:
        t = _parse_ts(r[t_idx])
        v = r[v_idx]
        if t is None:
            continue
        times.append(t)
        vals.append(float(v) if v is not None else None)
    if not times:
        return None

    out: dict = {"trend_kind": trend_kind,
                 "computed_at": datetime.now(timezone.utc).isoformat()}

    # ── baseline ─────────────────────────────────────────
    bres = _hourly_pattern_baseline(conn, tagsn, times, learning_days)
    if bres:
        b_series, b_upper, b_lower, mean_sigma = bres
        # 최근 6 샘플 평균
        recent_pairs = [(b, v) for b, v in zip(b_series[-6:], vals[-6:])
                        if b is not None and v is not None]
        if recent_pairs and mean_sigma > 1e-6:
            recent_b = sum(p[0] for p in recent_pairs) / len(recent_pairs)
            recent_v = sum(p[1] for p in recent_pairs) / len(recent_pairs)
            z = (recent_v - recent_b) / mean_sigma
            deviation_pct = ((recent_v - recent_b) / recent_b * 100) if recent_b != 0 else 0.0
            group = _lookup_site_group(conn, sitename or "", facilitytype or "")
            status, label = _baseline_status(deviation_pct, z, group)
            out["baseline"] = {
                "series": b_series,
                "band_upper": b_upper,
                "band_lower": b_lower,
                "method": "hourly_mean",
                "learning_window_days": learning_days,
                "status": status,
                "status_label": label,
                "deviation_pct": round(deviation_pct, 1),
            }

    # ── forecast ─────────────────────────────────────────
    threshold, threshold_label = _lookup_threshold(conn, trend_kind, sitename or "", facilitytype or "")
    f_times, f_vals, slope = _linear_forecast(times, vals, forecast_hours)
    if f_vals:
        last_val = next((v for v in reversed(vals) if v is not None), None)
        hours_to = None
        if last_val is not None and threshold is not None and slope is not None:
            hours_to = _hours_to_threshold(last_val, slope, threshold, forecast_hours)
        f_status, f_label = _forecast_status(hours_to)
        out["forecast"] = {
            "series": f_vals,
            "forecast_times": f_times,
            "method": "linear",
            "forecast_hours": forecast_hours,
            "threshold_value": threshold,
            "threshold_label": threshold_label,
            "hours_to_threshold": hours_to,
            "status": f_status,
            "status_label": f_label,
        }

    # baseline 도 forecast 도 없으면 None
    if "baseline" not in out and "forecast" not in out:
        return None

    # ── causal_hint (B안) — baseline/forecast 가 정상 아닌 경우만 ─
    b_status = out.get("baseline", {}).get("status")
    f_status = out.get("forecast", {}).get("status")
    if b_status in ("warning", "alert") or f_status in ("warning", "alert"):
        hint = compute_causal_hint(conn, sitename, facilitytype, region="R01")
        if hint:
            out["causal_hint"] = hint

    return out
