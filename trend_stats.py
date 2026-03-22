"""
트렌드 통계 요약 모듈

시계열 데이터에서 통계(평균/최대/최소/변화율/급변감지)를 추출하여
사용자에게 의미 있는 텍스트 요약을 생성한다.

LLM 없이 프로그램 Rule로 생성 가능한 수준의 통계 설명.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta

logger = logging.getLogger("slm")


def compute_trend_summary(
    rows: list[tuple],
    columns: list[str],
) -> list[dict[str, str]]:
    """트렌드 데이터에서 태그별 통계 요약을 생성한다.

    Args:
        rows: 시계열 쿼리 결과 (sitename, facilitytype, tagsn, val, datainfo, logtime, datadesc)
        columns: 컬럼명 리스트

    Returns:
        [{"prefix": "•", "text": "..."}, ...] 형태의 detail 블록 리스트
    """
    if not rows or not columns:
        return []

    col_idx = {c: i for i, c in enumerate(columns)}
    val_i = col_idx.get("val")
    di_i = col_idx.get("datainfo")
    lt_i = col_idx.get("logtime")

    if val_i is None or lt_i is None:
        return []

    # 태그(datainfo)별 시계열 분리
    series_map: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for row in rows:
        val = row[val_i]
        logtime = row[lt_i]
        datainfo = row[di_i] if di_i is not None else "값"

        if val is None:
            continue
        try:
            series_map[datainfo].append((str(logtime), float(val)))
        except (ValueError, TypeError):
            continue

    if not series_map:
        return []

    items: list[dict[str, str]] = []
    items.append({"prefix": "📊", "text": "**트렌드 통계 요약**"})

    for datainfo, series in series_map.items():
        if len(series) < 2:
            continue

        stats = _calc_series_stats(series)
        if not stats:
            continue

        items.extend(_format_stats(datainfo, stats))

    return items if len(items) > 1 else []


def _calc_series_stats(series: list[tuple[str, float]]) -> dict | None:
    """시계열 데이터의 통계를 계산한다."""
    series.sort(key=lambda x: x[0])

    vals = [v for _, v in series]
    times = [t for t, _ in series]

    n = len(vals)
    if n < 2:
        return None

    avg_val = sum(vals) / n
    max_val = max(vals)
    min_val = min(vals)
    first_val = vals[0]
    last_val = vals[-1]

    # 변화량/변화율
    change = last_val - first_val
    change_pct = (change / abs(first_val) * 100) if abs(first_val) > 0.001 else 0

    # 표준편차
    variance = sum((v - avg_val) ** 2 for v in vals) / n
    stddev = variance ** 0.5

    # 급변 감지: 연속 구간에서 평균의 30% 이상 급변
    sudden_changes = _detect_sudden_changes(vals, times, avg_val)

    return {
        "avg": round(avg_val, 2),
        "max": round(max_val, 2),
        "min": round(min_val, 2),
        "first": round(first_val, 2),
        "last": round(last_val, 2),
        "change": round(change, 2),
        "change_pct": round(change_pct, 1),
        "stddev": round(stddev, 2),
        "count": n,
        "first_time": times[0],
        "last_time": times[-1],
        "sudden_changes": sudden_changes,
    }


def _detect_sudden_changes(
    vals: list[float],
    times: list[str],
    avg_val: float,
    threshold_pct: float = 30.0,
) -> list[dict]:
    """급변 구간을 감지한다.

    연속 10포인트 윈도우에서 최대-최소 차이가 평균의 threshold_pct% 이상이면 급변.
    """
    if len(vals) < 10 or abs(avg_val) < 0.001:
        return []

    window = min(10, len(vals) // 5)
    threshold = abs(avg_val) * threshold_pct / 100
    changes: list[dict] = []

    for i in range(window, len(vals)):
        window_vals = vals[i - window:i + 1]
        diff = max(window_vals) - min(window_vals)
        if diff >= threshold:
            # 이전 급변과 가까우면 스킵 (중복 방지)
            if changes and i - changes[-1].get("_idx", 0) < window * 2:
                continue
            direction = "↑" if vals[i] > vals[i - window] else "↓"
            changes.append({
                "time": times[i],
                "direction": direction,
                "magnitude": round(diff, 2),
                "_idx": i,
            })

    # 상위 3건만
    changes.sort(key=lambda x: x["magnitude"], reverse=True)
    return changes[:3]


def _format_stats(datainfo: str, stats: dict) -> list[dict[str, str]]:
    """통계를 사용자 친화적 텍스트로 포맷한다."""
    items: list[dict[str, str]] = []

    # 기본 통계
    change_arrow = "↑" if stats["change"] > 0 else "↓" if stats["change"] < 0 else "→"
    change_desc = f"{change_arrow}{abs(stats['change_pct'])}%"

    items.append({
        "prefix": "•",
        "text": (
            f"{datainfo}: 평균 {stats['avg']}, "
            f"최대 {stats['max']}, 최소 {stats['min']}, "
            f"변화 {stats['first']}→{stats['last']} ({change_desc})"
        ),
    })

    # 변동성 판단
    cv = (stats["stddev"] / abs(stats["avg"]) * 100) if abs(stats["avg"]) > 0.001 else 0
    if cv > 30:
        items.append({"prefix": " ", "text": f"  <<warn:변동 큼 (CV={cv:.0f}%)>>"})
    elif cv > 15:
        items.append({"prefix": " ", "text": f"  변동 보통 (CV={cv:.0f}%)"})

    # 급변 감지
    for sc in stats.get("sudden_changes", []):
        time_str = sc["time"]
        if len(time_str) > 16:
            time_str = time_str[:16]
        items.append({
            "prefix": " ",
            "text": f"  <<error:급변 감지>>: {time_str} {sc['direction']} (변동폭 {sc['magnitude']})",
        })

    return items
