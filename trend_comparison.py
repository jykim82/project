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
               -- 품질 불량 진행 구간 제외 — 포화/고착 값의 기준선 오염 차단 (P2)
               AND logtime < COALESCE(
                   (SELECT since FROM tb_tag_quality
                     WHERE region = 'R01' AND tagsn = %s), 'infinity'::timestamptz)
             GROUP BY h
            """,
            (tagsn, str(learning_days), tagsn),
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


def _gbt_baseline_or_none(conn, tagsn: str, target_times: list[datetime]):
    """GBT 정상 기대값 baseline. 모델/데이터 없거나 오류 시 None → 호출부 폴백.

    Returns (series, band_upper, band_lower, mean_sigma, model_version) 또는 None.
    """
    try:
        import trend_baseline
        return trend_baseline.gbt_baseline(conn, tagsn, target_times)
    except Exception as e:  # 모델 미설치/로드 실패 등 — 폴백 안전
        logger.debug(f"trend_comparison: GBT baseline 폴백 ({e})")
        return None


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
    # 추세 적합 창을 '마지막 N개 샘플'이 아니라 '마지막 12시간'으로 고정한다.
    # 버킷이 작으면(예: 1분) 마지막 24샘플이 수십분~수시간짜리 짧은 구간이 되고,
    # 그게 오실레이션의 한 위상(단조 하강/상승)이면 R²가 높게 나와 게이트를 통과해
    # 급락/급등 직선이 나온다. 12h 창은 진동 전체를 포함해 R²를 정상적으로 낮춘다.
    if pairs:
        _cut = pairs[-1][0] - timedelta(hours=12)
        _win = [p for p in pairs if p[0] >= _cut]
        if len(_win) >= 6:
            pairs = _win
        else:
            pairs = pairs[-24:]      # 12h 내 표본 부족 시 폴백
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
    # 적합도(R²) — 진동/노이즈 신호(수위 등 평균회귀)는 R²가 낮다.
    # 순수 선형 외삽은 '지금 오실레이션의 어느 위상인가'를 그대로 연장하므로,
    # 진동 신호에서 순간 하강/상승 구간을 잡으면 밴드를 벗어나는 급락/급등 직선이 나온다.
    # → R² 로 [추세 외삽] vs [최근 평균 회귀] 를 블렌딩한다:
    #   forecast = last_y + (slope·R²)·dh            # 신뢰 높을수록 추세 반영
    #            + (1-R²)·(mean-last_y)·(1-φ^i)       # 신뢰 낮을수록 최근 평균으로 수렴
    # 진동(R²↓): 최근 평균으로 평탄 수렴 / 뚜렷한 추세(R²↑): 방향 그대로 외삽(클램프 별도).
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    r2 = (1.0 - ss_res / ss_tot) if ss_tot > 1e-9 else 0.0
    r2 = max(0.0, min(1.0, r2))
    # 추세 가중치(trend_w) — R² 가 충분히 뚜렷할 때만 추세를 투영한다.
    #   R² ≤ 0.4 : 추세 성분 0 → 최근 평균으로 수렴(수평선). 진동 신호가 순간 기울기
    #              방향으로 "직선으로 계속 떨어지는" 것을 방지(사용자 지적).
    #   R² ≥ 0.8 : 추세 그대로 투영(뚜렷한 상승/하강 추세). 그 사이는 선형 램프.
    trend_w = min(1.0, max(0.0, (r2 - 0.4) / 0.4))
    last_t, last_y = pairs[-1]
    phi = 0.90  # 평균회귀 감쇠(약한 추세가 최근 평균으로 수렴하는 속도)
    out_times: list[str] = []
    out_vals: list[float] = []
    steps = int(forecast_hours * 60 / step_minutes)
    for i in range(1, steps + 1):
        t = last_t + timedelta(minutes=step_minutes * i)
        dh = (t - last_t).total_seconds() / 3600.0
        # 추세 성분(신뢰 높을수록) + 최근 평균 회귀 성분(신뢰 낮을수록·수평)
        val = (last_y + (slope * trend_w) * dh
               + (mean_y - last_y) * (1.0 - phi ** i) * (1.0 - trend_w))
        # 축 라벨 포맷 통일 — 데이터 times 와 동일한 "YYYY-MM-DD HH:MM:SS" (KST 통일 2026-07-20)
        out_times.append(t.strftime("%Y-%m-%d %H:%M:%S"))
        out_vals.append(round(val, 3))
    return out_times, out_vals, round(slope * trend_w, 4)


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

        # chat_intent 신뢰도 보장 (2026-06-02):
        # FACILITY_ALARM_CAUSE_DIAGNOSIS_RANK 인텐트는 SQL 윈도우가 24시간이므로,
        # 6시간 윈도우인 본 hint 에서 alarm 건수가 충분히 많을 때만 제공.
        # - 알람 ≥10건 인 source 가 있으면 신뢰 (인텐트도 결과 응답 가능)
        # - flow_change 만 있는 경우 → chat_intent omit (전용 인텐트 미존재)
        import re as _re
        has_diagnosable_alarm = False
        for s in sources:
            if s.get("kind") != "alarm":
                continue
            m = _re.search(r"(\d+)건", s.get("detail", ""))
            if m and int(m.group(1)) >= 10:
                has_diagnosable_alarm = True
                break

        result: dict = {"summary": summary, "sources": sources}
        if has_diagnosable_alarm:
            result["chat_intent"] = "FACILITY_ALARM_CAUSE_DIAGNOSIS_RANK"
        return result
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

    # ── 계측 품질 게이트 (P2) ────────────────────────────
    # 대상 태그가 품질 이상(포화·고착·무응답 등)이면 판정 신뢰 불가 플래그를
    # 내려보낸다 — 프런트는 평소 대비/전망 배지 대신 품질 배지를 표시.
    # baseline/forecast 자체는 계산 유지 (차트 오버레이 참고용).
    try:
        import tag_quality
        _qcur = conn.cursor()
        _qi = tag_quality.fetch_tag_issue(_qcur, tagsn)
        _qcur.close()
        if _qi:
            out["quality_issue"] = _qi
    except Exception as e:
        logger.debug(f"trend_comparison: 품질 조회 건너뜀 ({e})")

    # ── baseline ─────────────────────────────────────────
    # GBT(정상 기대값) 우선, 실패/데이터부족 시 hourly_mean 폴백 (사양 §4).
    method = "hourly_mean"
    model_version: Optional[str] = None
    bres = _gbt_baseline_or_none(conn, tagsn, times)
    if bres:
        b_series, b_upper, b_lower, mean_sigma, model_version = bres
        method = "gbt"
    else:
        hm = _hourly_pattern_baseline(conn, tagsn, times, learning_days)
        if hm:
            b_series, b_upper, b_lower, mean_sigma = hm
        else:
            b_series = None  # type: ignore[assignment]
    if b_series is not None:
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
                "method": method,
                "model_version": model_version,
                "learning_window_days": learning_days,
                "status": status,
                "status_label": label,
                "deviation_pct": round(deviation_pct, 1),
            }

    # ── forecast ─────────────────────────────────────────
    threshold, threshold_label = _lookup_threshold(conn, trend_kind, sitename or "", facilitytype or "")
    f_times, f_vals, slope = _linear_forecast(times, vals, forecast_hours)
    fc_method = "linear"
    # [2026-07-16] Chronos-Bolt 우선 (백테스트 +46.9% — 사양 §5.4).
    # 실패/미가용 시 위 선형회귀 결과 그대로(폴백). 클램프·임계 스캔 등
    # 후처리는 엔진과 무관하게 아래에서 동일 적용된다.
    f_band_upper = None
    f_band_lower = None
    # 전망 신뢰 낮음 — 변동이 큰 신호인데 지배 주기가 안 잡힌 경우 (배지 "참고" 톤)
    fc_low_confidence = False
    if f_times:
        # Chronos 는 예측 스텝을 "입력 시계열 간격" 단위로 해석한다 (E-049).
        # 전망 그리드(30분)와 입력 버킷(예: 5~6분)이 다르면 시간축이 스케일돼
        # 주기가 왜곡된다. 세분 예측 후 추출 방식은 Bolt 원생 한도(64스텝)를
        # 넘겨 자기회귀 이어붙기로 주기가 다시 뭉개짐 → **컨텍스트를 30분
        # 버킷으로 다운샘플**해 48스텝(≤64) 원샷 예측. 부수 효과로 컨텍스트
        # 512스텝이 10일치가 되어 일주기를 여러 번 학습한다.
        _bm = None
        if len(times) >= 3:
            _dels = sorted(
                (times[i + 1] - times[i]).total_seconds() / 60.0
                for i in range(max(0, len(times) - 21), len(times) - 1)
                if (times[i + 1] - times[i]).total_seconds() > 0
            )
            if _dels:
                _bm = _dels[len(_dels) // 2]  # 중앙값 간격(분)
        _ctx_vals = vals
        if _bm and 0 < _bm < 30:
            # 균일 30분 "시간 격자"에 배치 — 위치 기반 압축은 결측 구간에서
            # 시간축이 줄어들어 주기 검출(ACF)·계절 패턴이 전부 어긋난다 (E-049)
            try:
                _t0 = times[0]
                _slots = int((times[-1] - _t0).total_seconds() // 1800) + 1
                if 0 < _slots <= 4096:
                    _acc: list = [[] for _ in range(_slots)]
                    for _t, _v in zip(times, vals):
                        if _v is None:
                            continue
                        _acc[int((_t - _t0).total_seconds() // 1800)].append(_v)
                    _ctx_vals = [
                        (sum(c) / len(c)) if c else None for c in _acc
                    ]
            except Exception:
                _agg = max(1, int(round(30.0 / _bm)))
                if _agg > 1:
                    _ctx_vals = [
                        (lambda ch: sum(ch) / len(ch) if ch else None)(
                            [v for v in vals[_i:_i + _agg] if v is not None])
                        for _i in range(0, len(vals), _agg)
                    ]
        # ── 주기 신호는 계절 나이브(지배 주기 전 동일 위상) 우선 (E-049) ──
        # 고정 24h 가 아니라 자기상관 스캔(8~36h)으로 지배 주기를 찾는다
        # (난지마을 실측: 14h 주기 r=0.81 — 24h 고정 검사는 역위상 -0.74).
        # r>=0.6 이면 "직전 주기 패턴 반복 + 연속성 앵커"가 실측과 같은
        # 파형·진폭을 보장. Chronos 중앙값은 위상 불확실성에서 진폭이 눌림.
        _seasonal_done = False
        if _bm and len(_ctx_vals) >= 120:  # 30분 그리드 2.5일 이상
            def _r_at(_lag: int):
                _prs = [(_ctx_vals[_i], _ctx_vals[_i + _lag])
                        for _i in range(len(_ctx_vals) - _lag)
                        if _ctx_vals[_i] is not None
                        and _ctx_vals[_i + _lag] is not None]
                if len(_prs) < 40:
                    return None
                _mx = sum(x for x, _ in _prs) / len(_prs)
                _my = sum(y for _, y in _prs) / len(_prs)
                _num = sum((x - _mx) * (y - _my) for x, y in _prs)
                _den = (sum((x - _mx) ** 2 for x, _ in _prs)
                        * sum((y - _my) ** 2 for _, y in _prs)) ** 0.5
                return (_num / _den) if _den > 0 else 0.0

            _best_r, _best_lag = 0.0, None
            for _lag in range(16, min(73, len(_ctx_vals) // 2), 2):
                _rv = _r_at(_lag)
                if _rv is not None and _rv > _best_r:
                    _best_r, _best_lag = _rv, _lag
            if not (_best_lag and _best_r >= 0.6):
                # 주기 불안정 + 변동 유의(CV>15%) → "이 시설은 원래 예측이
                # 어려움" — 확정 톤 대신 참고 톤으로 안내 (난지마을 유형)
                _nn = [v for v in _ctx_vals if v is not None]
                if _nn:
                    _m = sum(_nn) / len(_nn)
                    _sd = (sum((v - _m) ** 2 for v in _nn) / len(_nn)) ** 0.5
                    if abs(_m) > 1e-9 and _sd / abs(_m) > 0.15:
                        fc_low_confidence = True
            if _best_lag and _best_r >= 0.6:
                # 직전 주기 패턴 (결측은 인접값 보간)
                _pat = list(_ctx_vals[-_best_lag:])
                _fill = None
                for _i in range(len(_pat)):
                    if _pat[_i] is None:
                        _pat[_i] = _fill
                    else:
                        _fill = _pat[_i]
                _fill = None
                for _i in range(len(_pat) - 1, -1, -1):
                    if _pat[_i] is None:
                        _pat[_i] = _fill
                    else:
                        _fill = _pat[_i]
                if all(v is not None for v in _pat):
                    _sv = [float(_pat[_i % _best_lag])
                           for _i in range(len(f_times))]
                    # 연속성 앵커 — 시작점을 마지막 실측에 맞추고 선형 점감
                    _now_v = next((v for v in reversed(vals)
                                   if v is not None), None)
                    if _now_v is not None:
                        _off = _now_v - _sv[0]
                        # 오프셋은 초반 램프(~2h)에서만 소멸 — 전 구간 점감은
                        # 패턴 고점을 관측 최대 위로 들어 올림 (사용자 지적:
                        # 실측이 3을 넘은 적 없는데 예측 3.3)
                        _ramp = max(1, min(4, len(_sv) - 1))
                        _sv = [round(v + (_off * (1 - _i / _ramp)
                                          if _i < _ramp else 0.0), 3)
                               for _i, v in enumerate(_sv)]
                    f_vals = _sv
                    fc_method = f"seasonal_{_best_lag * 30 // 60}h"
                    if len(f_vals) > 1 and forecast_hours:
                        slope = round((f_vals[-1] - f_vals[0]) / forecast_hours, 4)
                    _seasonal_done = True
        _ch = None
        if not _seasonal_done:
            try:
                from trend_forecast import chronos_forecast
                _ch = chronos_forecast(_ctx_vals, len(f_times))
            except Exception:
                _ch = None
        if _ch:
            _med, _q10, _q90 = _ch
            if len(_med) >= len(f_times):
                f_vals = _med[:len(f_times)]
                f_band_lower = _q10[:len(f_times)]
                f_band_upper = _q90[:len(f_times)]
                fc_method = "chronos_bolt"
                # 연속성 앵커 (E-049) — 값 + **기울기** 연속.
                # 값만 이으면 배수(톱니) 신호에서 전망이 곧바로 평균 쪽으로
                # 꺾여 "당장 추세와 반대"로 보임 (사용자 검증: 합덕일반 수위2).
                # 초반 4h 는 최근 6h 기울기 연장을 블렌드하고 점차 모델로 수렴.
                _last_obs = next((v for v in reversed(vals) if v is not None), None)
                if _last_obs is not None and f_vals:
                    # 최근 6h 단순 기울기 (30분 격자 기준)
                    _slope30 = 0.0
                    if _ctx_vals and len(_ctx_vals) > 12:
                        _recent = [v for v in _ctx_vals[-13:] if v is not None]
                        if len(_recent) >= 4:
                            _slope30 = (_recent[-1] - _recent[0]) / max(1, len(_recent) - 1)
                    _off = _last_obs - f_vals[0]
                    _ramp = max(1, min(8, len(f_vals) - 1))  # ~4h
                    _out = []
                    for i, v in enumerate(f_vals):
                        _w = max(0.0, 1 - i / _ramp)  # 1→0 (초반 램프)
                        _ext = _last_obs + _slope30 * (i + 1)  # 기울기 연장
                        _out.append(round(_w * _ext + (1 - _w) * v
                                          + _off * _w * 0.0, 3))
                    # 시작점 정확 일치 보정 (블렌드 후 미세 격차 제거)
                    _gap = _last_obs - _out[0] if _out else 0.0
                    _out = [round(v + _gap * max(0.0, 1 - i / _ramp), 3)
                            for i, v in enumerate(_out)]
                    f_vals = _out
                    if f_band_lower:
                        f_band_lower = [round(v + _off * max(0.0, 1 - i / _ramp), 3)
                                        for i, v in enumerate(f_band_lower)]
                    if f_band_upper:
                        f_band_upper = [round(v + _off * max(0.0, 1 - i / _ramp), 3)
                                        for i, v in enumerate(f_band_upper)]
                # slope 는 방향 지표 — 예측 곡선 전체 기울기로 재산출
                if len(f_vals) > 1 and forecast_hours:
                    slope = round((f_vals[-1] - f_vals[0]) / forecast_hours, 4)
    if f_vals:
        # 향후 전망을 '같은 기간 실측이 실제로 간 범위(관측 밴드)' 근처로 하드 클램프.
        # 사용자 검증 원칙: 전망이 표출 기간 실측(min~max)을 크게 벗어나면 비현실적.
        # 선형 외삽이 오실레이션의 한 위상을 잡아 급락/급등해도 밴드 밖으로 못 나간다.
        _obs = [v for v in vals if v is not None]
        _obs_max = max(_obs) if _obs else None
        _obs_min = min(_obs) if _obs else None
        _span = (_obs_max - _obs_min) if (_obs_max is not None and _obs_min is not None) else 0.0
        # 여유폭: 관측 범위의 15% (최소 절대여유). 밴드를 살짝만 넘게 허용.
        _margin = max(_span * 0.15,
                      (abs(_obs_max) * 0.05 if _obs_max is not None else 0.0), 0.05)
        if _obs_max is not None:
            _upper = _obs_max + _margin
            # 수위는 만수위(탱크 상단)를 넘지 않게 추가 상한 — 단, threshold 가
            # 관측 최대보다 위일 때만 (저수위/운영 하한 임계에 적용하면 전망
            # 상단이 잘려 주기가 납작해짐 — E-049 난지마을 사례)
            if (trend_kind == "level" and threshold is not None
                    and threshold > _obs_max):
                _upper = min(_upper, round(threshold / 0.9, 3))
        else:
            _upper = None
        if _obs_min is not None:
            _lower = _obs_min - _margin
            if trend_kind in ("level", "flow", "quality", "pressure"):
                _lower = max(_lower, 0.0)          # 음수 불가
        else:
            _lower = None

        def _clamp(v: float) -> float:
            if _lower is not None:
                v = max(v, _lower)
            if _upper is not None:
                v = min(v, _upper)
            return round(v, 3)

        if _upper is not None or _lower is not None:
            f_vals = [_clamp(v) for v in f_vals]
            if f_band_upper is not None:
                f_band_upper = [_clamp(v) for v in f_band_upper]
            if f_band_lower is not None:
                f_band_lower = [_clamp(v) for v in f_band_lower]

        # 임계 도달 시점 — 블렌딩 forecast 시리즈에서 첫 교차를 직접 스캔(곡선과 일관).
        # 선형 slope 로 계산하면 평균회귀로 평탄해진 곡선과 어긋나므로 시리즈 기준.
        last_val = next((v for v in reversed(vals) if v is not None), None)
        hours_to = None
        if last_val is not None and threshold is not None and f_vals and f_times:
            rising = threshold > last_val
            _last_t = next((t for t, v in zip(reversed(times), reversed(vals)) if v is not None), None)
            for i, fv in enumerate(f_vals):
                if (fv >= threshold) if rising else (fv <= threshold):
                    try:
                        _ct = datetime.fromisoformat(f_times[i])
                        if _last_t is not None:
                            hours_to = round((_ct - _last_t).total_seconds() / 3600.0, 1)
                    except (ValueError, TypeError):
                        hours_to = round((i + 1) * 30 / 60.0, 1)
                    break
        f_status, f_label = _forecast_status(hours_to)
        out["forecast"] = {
            "series": f_vals,
            "forecast_times": f_times,
            "method": fc_method,
            "forecast_hours": forecast_hours,
            "threshold_value": threshold,
            "threshold_label": threshold_label,
            "hours_to_threshold": hours_to,
            "status": f_status,
            "status_label": f_label,
            # 주기 불안정·고변동 시설 — 프런트 배지 "참고" 톤 (확정 아님 안내)
            "low_confidence": fc_low_confidence,
            # chronos 사용 시 10/90% 불확실성 밴드 (선형 폴백이면 None)
            "band_upper": f_band_upper,
            "band_lower": f_band_lower,
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
