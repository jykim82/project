"""물 수지 (Mass Balance) 검증 모듈.

상류 유출유량 vs 하류 유입유량 합계를 비교하여
불명수량(NRW) 및 누수 의심 구간을 감지한다.

설계 원칙:
- 적산 태그(CUMULATIVE) delta 우선, 순시 태그(INSTANT) 적분 폴백
- 24시간 롤링 윈도우
- 70% 이상 데이터 커버리지 필요 (그 미만은 "데이터부족" 플래그)
"""
import logging
import re
from datetime import datetime, timedelta
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# <<tone:label>> 마커를 pill 로 승격 (분석형 응답 디자인 일관화)
_MARKER_RE = re.compile(r"<<(ok|warn|error|critical|info):([^>]+)>>")
_TONE_NORMALIZE = {"error": "critical", "critical": "critical", "warn": "warn", "ok": "ok", "info": "info"}
_TONE_PILL_LABEL = {"critical": "이상", "warn": "주의", "ok": "정상", "info": "정보"}


def _lift_markers_to_pills(items: list) -> list:
    """각 item text 내의 첫 <<tone:label>> 마커를 벗겨 pill 필드로 이동."""
    for it in items:
        if not isinstance(it, dict):
            continue
        text = it.get("text") or ""
        m = _MARKER_RE.search(text)
        if not m:
            continue
        tone = _TONE_NORMALIZE.get(m.group(1), "info")
        # 마커 벗기기 (첫 번째만, 라벨 문자열만 남김)
        it["text"] = _MARKER_RE.sub(lambda mo: mo.group(2), text, count=1)
        pill_label = _TONE_PILL_LABEL.get(tone, "")
        if pill_label and "pill" not in it:
            it["pill"] = {"text": pill_label, "tone": tone}
    return items

# ── 임계값 ──
_GRADE_THRESHOLDS = [
    (5.0, "정상"),
    (15.0, "관심"),
    (25.0, "주의"),
]
_DEFAULT_GRADE = "경고"

# ── 데이터 커버리지 최소 기준 (24h = 288 buckets @ 5min) ──
_MIN_COVERAGE_RATIO = 0.70
_EXPECTED_POINTS_24H = 288

# ── 시설유형별 유출/유입 group_code (우선순위순, 첫 매칭 사용) ──
_OUTPUT_GROUPS: dict[str, list[str]] = {
    "배수지": ["FLOW_OUTLET", "FLOW_INSTANT", "FLOW_CUMULATIVE"],
    "가압장": ["FLOW_OUTLET", "FLOW_INSTANT", "FLOW_CUMULATIVE"],
    "감압시설": ["FLOW_OUTLET", "FLOW_INSTANT", "FLOW_CUMULATIVE"],
    "소블록": ["FLOW_OUTLET", "FLOW_INSTANT", "FLOW_CUMULATIVE"],
    "소소블록": ["FLOW_OUTLET", "FLOW_INSTANT", "FLOW_CUMULATIVE"],
}
_INPUT_GROUPS: dict[str, list[str]] = {
    # 유입 태그 없으면 유출/순시로 폴백 (통과 유량 ≈ 유입)
    "배수지": ["FLOW_INLET", "FLOW_INSTANT", "FLOW_OUTLET", "FLOW_CUMULATIVE"],
    "가압장": ["FLOW_INLET", "FLOW_INSTANT", "FLOW_OUTLET", "FLOW_CUMULATIVE"],
    "감압시설": ["FLOW_INLET", "FLOW_INSTANT", "FLOW_OUTLET", "FLOW_CUMULATIVE"],
    "소블록": ["FLOW_INLET", "FLOW_INSTANT", "FLOW_OUTLET", "FLOW_CUMULATIVE"],
    "소소블록": ["FLOW_INLET", "FLOW_INSTANT", "FLOW_OUTLET", "FLOW_CUMULATIVE"],
}

# ── 유량 관련 group_code (압력 제외) ──
_FLOW_GROUPS = {
    "FLOW_OUTLET", "FLOW_INLET", "FLOW_INSTANT",
    "FLOW_CUMULATIVE",
}


def classify_balance_grade(imbalance_pct: float) -> str:
    """불균형률(%) → 등급 분류."""
    abs_pct = abs(imbalance_pct)
    for threshold, grade in _GRADE_THRESHOLDS:
        if abs_pct < threshold:
            return grade
    return _DEFAULT_GRADE


def _is_cumulative_tag(tagsn: str, tag_datainfo: dict[str, str]) -> bool:
    """적산 태그 여부 판별 (datainfo에 "적산" 포함)."""
    info = tag_datainfo.get(tagsn, "")
    return "적산" in info


def _integrate_instantaneous(
    values: list[tuple[datetime, float]],
    bucket_min: int = 5,
) -> tuple[float, int]:
    """순시유량(m³/h) 시계열 → 총 유량(m³) 사다리꼴 적분.

    Returns: (volume_m3, point_count)
    """
    if len(values) < 2:
        return 0.0, len(values)

    total_m3 = 0.0
    for i in range(1, len(values)):
        dt_hours = (values[i][0] - values[i - 1][0]).total_seconds() / 3600.0
        if dt_hours <= 0 or dt_hours > 1.0:
            # 1시간 이상 갭이면 스킵 (데이터 결측)
            continue
        avg_flow = (values[i - 1][1] + values[i][1]) / 2.0
        total_m3 += avg_flow * dt_hours

    return total_m3, len(values)


def _cumulative_delta(
    values: list[tuple[datetime, float]],
) -> Optional[tuple[float, int]]:
    """적산유량 시계열 → 총 유량(m³) = last - first.

    롤오버(reset) 감지 시 None 반환.
    Returns: (volume_m3, point_count) or None
    """
    if len(values) < 2:
        return None

    first_val = values[0][1]
    last_val = values[-1][1]
    delta = last_val - first_val

    # 롤오버 감지: 적산값이 크게 감소하면 유효하지 않음
    if delta < -1000:
        return None

    # 음수이지만 작은 경우: 계측 노이즈 → 0 처리
    if delta < 0:
        delta = 0.0

    return delta, len(values)


def _compute_facility_volume(
    raw_data: dict[str, list[tuple[datetime, float]]],
    tagsns: list[str],
    tag_datainfo: dict[str, str],
) -> dict:
    """시설 하나의 유량 체적(m³) 계산.

    Returns: {volume_m3, method, tag_count, point_count, coverage_pct, status}
    """
    if not tagsns:
        return {
            "volume_m3": 0.0, "method": "none",
            "tag_count": 0, "point_count": 0,
            "coverage_pct": 0.0, "status": "no_tags",
        }

    # 적산 태그와 순시 태그 분리
    cum_tags = [t for t in tagsns if _is_cumulative_tag(t, tag_datainfo)]
    inst_tags = [t for t in tagsns if not _is_cumulative_tag(t, tag_datainfo)]

    # 1순위: 적산 태그 delta
    if cum_tags:
        total_vol = 0.0
        total_points = 0
        valid_count = 0
        for tsn in cum_tags:
            vals = raw_data.get(tsn, [])
            if not vals:
                continue
            result = _cumulative_delta(vals)
            if result is not None:
                vol, pts = result
                total_vol += vol
                total_points += pts
                valid_count += 1

        if valid_count > 0:
            coverage = total_points / max(valid_count * _EXPECTED_POINTS_24H, 1)
            return {
                "volume_m3": total_vol,
                "method": "cumulative",
                "tag_count": valid_count,
                "point_count": total_points,
                "coverage_pct": min(coverage, 1.0),
                "status": "ok" if coverage >= _MIN_COVERAGE_RATIO else "low_coverage",
            }

    # 2순위: 순시 태그 적분
    if inst_tags:
        total_vol = 0.0
        total_points = 0
        valid_count = 0
        for tsn in inst_tags:
            vals = raw_data.get(tsn, [])
            if len(vals) < 2:
                continue
            vol, pts = _integrate_instantaneous(vals)
            total_vol += vol
            total_points += pts
            valid_count += 1

        if valid_count > 0:
            coverage = total_points / max(valid_count * _EXPECTED_POINTS_24H, 1)
            return {
                "volume_m3": total_vol,
                "method": "integration",
                "tag_count": valid_count,
                "point_count": total_points,
                "coverage_pct": min(coverage, 1.0),
                "status": "ok" if coverage >= _MIN_COVERAGE_RATIO else "low_coverage",
            }

    return {
        "volume_m3": 0.0, "method": "none",
        "tag_count": 0, "point_count": 0,
        "coverage_pct": 0.0, "status": "no_data",
    }


def _collect_tags(tag_map: dict, groups: list[str]) -> list[str]:
    """group_code 우선순위 리스트 → 첫 매칭 그룹의 tagsn 반환.

    중복 계산 방지: FLOW_OUTLET이 있으면 FLOW_INSTANT은 사용하지 않음.
    """
    for g in groups:
        if g in _FLOW_GROUPS:
            tags = tag_map.get(g, [])
            if tags:
                return tags
    return []


def compute_flow_balance_all(
    query_ts_func: Callable,
    causal_index: dict,
    tag_datainfo: dict[str, str],
    lookback_hours: int = 24,
) -> list[dict]:
    """전체 네트워크 물 수지 검증.

    Args:
        query_ts_func: (tagsn_list, from_ts, to_ts) → {tagsn: [(logtime, val)]}
        causal_index: _CAUSAL_INDEX
        tag_datainfo: {tagsn: datainfo}
        lookback_hours: 분석 윈도우 (기본 24h)

    Returns: FlowBalanceEdge 리스트
    """
    now = datetime.now()
    from_ts = (now - timedelta(hours=lookback_hours)).strftime("%Y-%m-%d %H:%M:%S")
    to_ts = now.strftime("%Y-%m-%d %H:%M:%S")

    edges: list[dict] = []
    checked: set[tuple] = set()

    for key, info in causal_index.items():
        if not isinstance(key, tuple) or len(key) != 2:
            continue

        sn, ft = key
        out_groups = _OUTPUT_GROUPS.get(ft, [])
        if not out_groups:
            continue

        downstream_list = info.get("downstream", [])
        if not downstream_list:
            continue

        # 이미 처리한 엣지 그룹 스킵
        edge_key = (sn, ft)
        if edge_key in checked:
            continue
        checked.add(edge_key)

        # 상류 유출 태그 수집
        us_tagsns = _collect_tags(info.get("tag_map", {}), out_groups)
        if not us_tagsns:
            continue

        # 하류 시설별 유입 태그 수집
        ds_facilities = []
        all_ds_tagsns = []
        for ds_key in downstream_list:
            if not isinstance(ds_key, tuple) or len(ds_key) != 2:
                continue
            ds_info = causal_index.get(ds_key)
            if not ds_info:
                continue
            ds_in_groups = _INPUT_GROUPS.get(ds_key[1], [])
            ds_tagsns = _collect_tags(ds_info.get("tag_map", {}), ds_in_groups)
            ds_facilities.append({
                "sitename": ds_key[0],
                "facilitytype": ds_key[1],
                "tagsns": ds_tagsns,
            })
            all_ds_tagsns.extend(ds_tagsns)

        if not ds_facilities:
            continue

        # 배치 쿼리: 상류 + 하류 태그 전체를 한 번에
        all_tagsns = list(set(us_tagsns + all_ds_tagsns))
        try:
            raw_data = query_ts_func(all_tagsns, from_ts, to_ts)
        except Exception as e:
            logger.warning(f"물 수지 쿼리 실패 {sn} {ft}: {e}")
            continue

        # 상류 유출량 계산
        us_result = _compute_facility_volume(raw_data, us_tagsns, tag_datainfo)

        # 하류 시설별 유입량 계산
        ds_results = []
        ds_total_m3 = 0.0
        for dsf in ds_facilities:
            ds_result = _compute_facility_volume(
                raw_data, dsf["tagsns"], tag_datainfo,
            )
            ds_total_m3 += ds_result["volume_m3"]
            ds_results.append({
                "sitename": dsf["sitename"],
                "facilitytype": dsf["facilitytype"],
                "volume_m3": round(ds_result["volume_m3"], 2),
                "method": ds_result["method"],
                "tag_count": ds_result["tag_count"],
                "coverage_pct": round(ds_result["coverage_pct"] * 100, 1),
            })

        # 불균형 계산
        us_vol = us_result["volume_m3"]
        # 하류 전체가 데이터 없음/부족 → 불균형 산출 불가
        # (태그 없음 OR 태그 있지만 유효 데이터 없음)
        ds_all_no_data = all(
            d["method"] == "none" or (d["volume_m3"] < 0.01 and d["coverage_pct"] < 50)
            for d in ds_results
        )
        if us_vol < 0.01:
            status = "upstream_zero"
            imbalance_m3 = 0.0
            imbalance_pct = 0.0
            grade = "정상"
        elif us_result["status"] == "no_data" or us_result["status"] == "no_tags":
            status = "insufficient_data"
            imbalance_m3 = 0.0
            imbalance_pct = 0.0
            grade = "정상"
        elif ds_all_no_data:
            status = "downstream_no_data"
            imbalance_m3 = 0.0
            imbalance_pct = 0.0
            grade = "정상"
        else:
            status = "ok"
            imbalance_m3 = us_vol - ds_total_m3
            imbalance_pct = (imbalance_m3 / us_vol) * 100.0
            grade = classify_balance_grade(imbalance_pct)

        edges.append({
            "upstream_sitename": sn,
            "upstream_facilitytype": ft,
            "upstream_volume_m3": round(us_vol, 2),
            "upstream_method": us_result["method"],
            "upstream_tag_count": us_result["tag_count"],
            "upstream_coverage_pct": round(us_result["coverage_pct"] * 100, 1),
            "downstream_facilities": ds_results,
            "downstream_total_m3": round(ds_total_m3, 2),
            "imbalance_m3": round(imbalance_m3, 2),
            "imbalance_pct": round(imbalance_pct, 1),
            "grade": grade,
            "period_hours": lookback_hours,
            "status": status,
        })

    # 불균형 심한 순 정렬
    edges.sort(key=lambda e: -abs(e["imbalance_pct"]))
    return edges


def build_flow_balance_scan_block(
    edges: list[dict],
) -> tuple[list[dict], dict]:
    """물 수지 결과 → 시맨틱 마커 포맷.

    Returns: (detail_block_items, data_dict)
    """
    ok_edges = [e for e in edges if e["grade"] == "정상" and e["status"] == "ok"]
    warn_edges = [e for e in edges if e["grade"] not in ("정상",) and e["status"] == "ok"]
    skip_edges = [e for e in edges if e["status"] != "ok"]

    data = {
        "total_edges": len(edges),
        "ok_count": len(ok_edges),
        "imbalance_count": len(warn_edges),
        "skip_count": len(skip_edges),
    }

    items: list[dict] = []

    if not edges:
        items.append({
            "prefix": "✓",
            "text": "<<ok:물 수지 검증 대상 경로가 없습니다>>",
        })
        return items, data

    # 요약
    if warn_edges:
        items.append({
            "prefix": "",
            "text": f"<<error:물 수지 불균형 {len(warn_edges)}건 감지>> "
                    f"(정상 {len(ok_edges)}건, 데이터부족 {len(skip_edges)}건)",
        })
    else:
        items.append({
            "prefix": "✓",
            "text": f"<<ok:물 수지 검증 정상>> — 전체 {len(ok_edges)}개 경로 균형 (±5% 이내)",
        })

    # 불균형 엣지 상세 (최대 10건)
    for i, e in enumerate(warn_edges[:10], 1):
        us = f"{e['upstream_sitename']} {e['upstream_facilitytype']}"
        ds_names = ", ".join(
            f"{d['sitename']} {d['facilitytype']}" for d in e["downstream_facilities"]
        )
        grade = e["grade"]
        marker = "error" if grade == "경고" else "warn"

        items.append({"prefix": f"[{i}]", "text": f"{us} → {ds_names}"})
        items.append({
            "prefix": "",
            "text": f"  상류 유출: {e['upstream_volume_m3']:,.1f}m³ "
                    f"/ 하류 합계: {e['downstream_total_m3']:,.1f}m³",
        })
        items.append({
            "prefix": "",
            "text": f"  <<{marker}:불균형 {e['imbalance_pct']:+.1f}% "
                    f"({e['imbalance_m3']:,.1f}m³) — {grade}>>",
        })

        # 하류 시설별 상세 — 들여쓰기는 prefix="-" (isChild) 로 처리,
        # text 는 간결한 "시설: 값 (메타)" 형태 (leader 트리거 회피)
        for d in e["downstream_facilities"]:
            vol_str = f"{d['volume_m3']:,.1f}m³" if d["volume_m3"] > 0 else "0m³"
            items.append({
                "prefix": "-",
                "text": f"{d['sitename']} {d['facilitytype']}: "
                        f"{vol_str} ({d['method']}, 커버리지 {d['coverage_pct']:.0f}%)",
            })

    # 정상 요약
    if ok_edges and warn_edges:
        items.append({
            "prefix": "",
            "text": f"<<ok:정상 경로 {len(ok_edges)}건 (±5% 이내)>>",
        })

    # 데이터 부족 요약
    if skip_edges:
        skip_names = ", ".join(
            f"{e['upstream_sitename']}" for e in skip_edges[:5]
        )
        suffix = f" 외 {len(skip_edges) - 5}건" if len(skip_edges) > 5 else ""
        items.append({
            "prefix": "",
            "text": f"<<warn:데이터 부족 {len(skip_edges)}건: {skip_names}{suffix}>>",
        })

    return _lift_markers_to_pills(items), data
