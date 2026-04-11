"""
SQL 쿼리 실행기 모듈 — response_builder.py에서 분리

TimescaleDB 시계열 쿼리, 야간최소유량, 태그 요약, 헌팅체크 등
DB 접근이 필요한 데이터 조회 함수를 포함한다.

init()으로 DB 커넥션을 주입받아 사용.
"""

import logging
import os
import re
import time
from datetime import datetime, timedelta
from typing import Any, Optional

import psycopg2

from shared.timeseries import get_chunks_for_range as _get_chunks_for_range, query_chunks_raw as _query_chunks_raw

logger = logging.getLogger("slm")

# 의존성 주입
_get_db_connection = None
_causal_index = None

# DB 직접 연결용
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5433")
DB_NAME = os.environ.get("DB_NAME", "slm")
DB_USER = os.environ.get("DB_USER", "slm_dev")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")

# 최근값 캐시
_recent_values_cache: dict = {}
_recent_values_cache_ts: float = 0.0
_RECENT_VALUES_CACHE_TTL_SEC = 60


def init(get_db_connection_fn, causal_index=None):
    """DB 커넥션 + 인과 인덱스를 주입받는다."""
    global _get_db_connection, _causal_index
    _get_db_connection = get_db_connection_fn
    _causal_index = causal_index


def _query_recent_values(tagsn_list: list[str], minutes: int = 180) -> dict[str, list[float]]:
    """교차 검증용 최근 raw 값 조회 — 공용 헬퍼.

    cross_facility_check_all/single에서 query_func으로 사용.
    Returns: {tagsn: [val1, val2, ...]}

    TTL 캐시 적용: 60초 내 동일 (tagsn_set, minutes) 재호출 시 DB 건너뜀.
    ANOMALY_FACILITY_DETAIL 핸들러에서 5~6회 반복 호출 비용을 제거한다.
    """
    global _recent_values_cache, _recent_values_cache_ts
    import time as _time

    now_ts = _time.time()
    # 캐시 TTL 만료 시 전체 초기화
    if now_ts - _recent_values_cache_ts >= _RECENT_VALUES_CACHE_TTL_SEC:
        _recent_values_cache = {}
        _recent_values_cache_ts = now_ts

    cache_key = (frozenset(tagsn_list), minutes)
    if cache_key in _recent_values_cache:
        return _recent_values_cache[cache_key]

    conn = _get_db_connection()
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
        _recent_values_cache[cache_key] = result
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
    conn = _get_db_connection()
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
    conn = _get_db_connection()
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

    conn = _get_db_connection()
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

    conn = _get_db_connection()
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
    conn = _get_db_connection()
    try:
        cur = conn.cursor()

        tag_meta: dict[str, tuple] = {}
        tagsn_list: list[str] = []

        # Step 1-A: group_code가 있으면 tb_tag_group_map JOIN으로 정확한 태그 목록 획득
        if group_code:
            resolved_codes = _resolve_group_codes(group_code)
            resolved_ids = [_group_code_to_id[c] for c in resolved_codes if c in _group_code_to_id]

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

    conn = _get_db_connection()
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
    conn = _get_db_connection()
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


# =============================================================================
# 배수지 수위 변동 원인 분석 (Node-RED 수위 조건 로직 기반)
# =============================================================================

_LEVEL_CAUSE_COLUMNS = [
    "sitename", "direction", "direction_label", "severity",
    "current_level", "hh_set", "ll_set",
    "upstream_sitename", "upstream_facilitytype",
    "pump_status", "pump_detail",
    "supply_time_hours",
    "outflow_exceeds_inflow", "inflow_avg", "outflow_avg",
    "inlet_valve_closed", "outlet_valve_closed",
    "all_pumps_running", "upstream_level_set",
    "cause_summary",
]


def _execute_level_cause_analysis(sitename: str) -> tuple[list, list]:
    """배수지 수위 변동 원인 분석 — Node-RED 수위 조건 로직 참고.

    최근 120분 수위 트렌드로 방향(LL/HH) 판정 후,
    방향별 조건(펌프, 밸브, 유량, 공급시간)을 체크하여 원인을 분석한다.
    """
    conn = _get_db_connection()
    cur = conn.cursor()
    try:
        return _level_cause_inner(cur, sitename)
    except Exception as e:
        logger.error(f"수위 원인 분석 실패 ({sitename}): {e}")
        return [], _LEVEL_CAUSE_COLUMNS
    finally:
        cur.close()
        conn.close()


def _level_cause_inner(cur, sitename: str) -> tuple[list, list]:
    """수위 원인 분석 내부 로직."""
    from datetime import datetime, timedelta

    # ── 1. 수위 태그 + 최근 120분 데이터 → 방향 판정 ──
    cur.execute("""
        SELECT ti.tagsn, ti.datainfo
        FROM tb_tag_info ti
        WHERE ti.sitename = %s AND ti.facilitytype = '배수지'
          AND ti.tagtype = 'Analog Input'
          AND ti.datainfo ~* '수위'
          AND ti.datainfo !~* 'HH|LL|H설정|L설정|SET|알람|설정|염소'
        ORDER BY ti.datainfo
        LIMIT 1
    """, (sitename,))
    level_tag_row = cur.fetchone()
    if not level_tag_row:
        return [], _LEVEL_CAUSE_COLUMNS

    level_tagsn = level_tag_row[0]

    # DB 최신 데이터 시간 기준 윈도우 (데이터 지연 대응)
    cur.execute("SELECT max(logtime) FROM tb_tag_raw_data WHERE tagsn = %s", (level_tagsn,))
    _max_row = cur.fetchone()
    _ref_time = _max_row[0].replace(tzinfo=None) if _max_row and _max_row[0] and getattr(_max_row[0], 'tzinfo', None) else datetime.now()
    if not _max_row or not _max_row[0]:
        _ref_time = datetime.now()
    _to = (_ref_time + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    _from = (_ref_time - timedelta(minutes=120)).strftime("%Y-%m-%d %H:%M:%S")
    chunks = _get_chunks_for_range(cur, _from, _to)
    vals = []
    if chunks:
        raw = _query_chunks_raw(cur, chunks, [level_tagsn], _from, _to)
        for _, logtime, val in raw:
            if val is not None:
                ts = logtime.timestamp() if hasattr(logtime, 'timestamp') else 0
                vals.append((ts, float(val)))

    current_level = vals[-1][1] if vals else 0
    direction = _detect_level_direction(vals)
    direction_label = {"LL": "하락", "HH": "상승", "STABLE": "안정"}[direction]

    # ── 2. HH/LL 설정값 + 공급시간 ──
    hh_set, ll_set = _get_level_thresholds(cur, sitename)
    supply_time_hours = _get_supply_time(cur, sitename)

    # ── 3. 상류 가압장 ──
    upstream_sitename, upstream_facilitytype = _get_upstream_booster(cur, sitename)

    # ── 4. 펌프 상태 ──
    pump_status, pump_detail, all_pumps_running = _check_pump_status(cur, upstream_sitename, _ref_time)

    # ── 5. 유입/유출유량 비교 ──
    inflow_avg, outflow_avg, outflow_exceeds = _check_flow_balance(cur, sitename, _ref_time)

    # ── 6. 밸브 상태 ──
    inlet_closed = _check_valve_closed(cur, sitename, "유입", _ref_time)
    outlet_closed = _check_valve_closed(cur, sitename, "유출", _ref_time)

    # ── 7. 상류 설정수위 ──
    upstream_level_set = 0.0
    if upstream_sitename and direction == "HH":
        upstream_level_set = _get_upstream_hh_set(cur, upstream_sitename)

    # ── 8. 심각도 + 원인 요약 ──
    severity, cause_summary = _determine_severity(
        direction, pump_status, inlet_closed, outlet_closed,
        outflow_exceeds, inflow_avg, outflow_avg,
        supply_time_hours, all_pumps_running,
        upstream_level_set, hh_set,
    )

    row = [
        sitename, direction, direction_label, severity,
        round(current_level, 3), hh_set, ll_set,
        upstream_sitename, upstream_facilitytype,
        pump_status, pump_detail, supply_time_hours,
        outflow_exceeds, inflow_avg, outflow_avg,
        inlet_closed, outlet_closed,
        all_pumps_running, upstream_level_set,
        cause_summary,
    ]
    return [tuple(row)], _LEVEL_CAUSE_COLUMNS


def _detect_level_direction(vals: list[tuple]) -> str:
    """수위 데이터 기울기로 방향 판정 (LL/HH/STABLE)."""
    if len(vals) < 10:
        return "STABLE"
    n = len(vals)
    x = [v[0] for v in vals]
    y = [v[1] for v in vals]
    x_mean = sum(x) / n
    y_mean = sum(y) / n
    num = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, y))
    den = sum((xi - x_mean) ** 2 for xi in x)
    slope = num / den if den else 0
    slope_per_hour = slope * 3600
    if slope_per_hour < -0.03:
        return "LL"
    elif slope_per_hour > 0.03:
        return "HH"
    return "STABLE"


def _get_level_thresholds(cur, sitename: str) -> tuple[float, float]:
    """HH/LL 설정값 조회."""
    hh_set, ll_set = 0.0, 0.0
    cur.execute("""
        SELECT ti.datainfo, r.val
        FROM tb_tag_info ti
        JOIN LATERAL (
            SELECT val FROM tb_tag_raw_data r
            WHERE r.tagsn = ti.tagsn ORDER BY logtime DESC LIMIT 1
        ) r ON true
        WHERE ti.sitename = %s AND ti.facilitytype = '배수지'
          AND ti.tagtype = 'Analog Output'
          AND ti.datainfo ~* '수위.*SET'
    """, (sitename,))
    for datainfo, val in cur.fetchall():
        if val is None:
            continue
        v = float(val)
        if 'HH' in datainfo and v > hh_set:
            hh_set = v
        elif 'LL' in datainfo and (ll_set == 0 or v < ll_set):
            ll_set = v
    return hh_set, ll_set


def _get_supply_time(cur, sitename: str) -> float | None:
    """용수공급가능시간 조회."""
    cur.execute(
        "SELECT total_supply_time FROM tb_service_reservoir_status WHERE sitename = %s",
        (sitename,),
    )
    row = cur.fetchone()
    return float(row[0]) if row and row[0] is not None else None


def _get_upstream_booster(cur, sitename: str) -> tuple[str, str]:
    """상류 가압장 조회."""
    cur.execute("""
        SELECT upstream_sitename, upstream_facilitytype
        FROM tb_facility_flow_map
        WHERE downstream_sitename = %s AND upstream_facilitytype = '가압장'
        LIMIT 1
    """, (sitename,))
    row = cur.fetchone()
    return (row[0], row[1]) if row else ("", "")


def _check_pump_status(cur, upstream_sitename: str, ref_time=None) -> tuple[str, str, bool]:
    """상류 가압장 펌프 가동 상태 확인 (120분간)."""
    from datetime import datetime, timedelta
    if not upstream_sitename:
        return "-", "상류 시설 정보 없음", False

    cur.execute("""
        SELECT ti.tagsn FROM tb_tag_info ti
        WHERE ti.sitename = %s AND ti.equipmenttype = '가압펌프'
          AND ti.tagtype = 'Digital Input'
          AND (ti.datainfo ~* '운전|동작|RUN')
          AND ti.datainfo !~* 'FAULT|FLT|STOP|정지'
    """, (upstream_sitename,))
    pump_tags = [r[0] for r in cur.fetchall()]
    if not pump_tags:
        return "-", "가압펌프 태그 없음", False

    _rt = ref_time or datetime.now()
    _pump_from = (_rt - timedelta(minutes=120)).strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("""
        SELECT tagsn,
               COUNT(*) FILTER (WHERE val = 1) AS on_cnt,
               COUNT(*) AS total_cnt
        FROM tb_tag_raw_data
        WHERE tagsn = ANY(%s) AND logtime >= %s::timestamp
        GROUP BY tagsn
    """, (pump_tags, _pump_from))
    total_pumps = len(pump_tags)
    running = sum(1 for _, on, tot in cur.fetchall() if tot > 0 and on / tot > 0.5)

    if running == 0:
        return "정지", f"가압펌프 {total_pumps}대 전부 정지", False
    if running == total_pumps:
        return "전수운전", f"가압펌프 {total_pumps}대 전부 운전 중", True
    return "운전", f"가압펌프 {running}/{total_pumps}대 운전 중", False


def _check_flow_balance(cur, sitename: str, ref_time=None) -> tuple[float, float, bool]:
    """유입/유출 유량 비교 (최근 30분 평균)."""
    from datetime import datetime, timedelta
    _rt = ref_time or datetime.now()
    _flow_from = (_rt - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    avgs = {}
    for key, pattern in [("inflow", "%유입%순시%"), ("outflow", "%유출%순시%")]:
        cur.execute("""
            SELECT AVG(r.val)
            FROM tb_tag_raw_data r
            JOIN tb_tag_info ti ON ti.tagsn = r.tagsn
            WHERE ti.sitename = %s AND ti.facilitytype = '배수지'
              AND ti.datainfo ILIKE %s AND ti.tagtype = 'Analog Input'
              AND r.logtime >= %s::timestamp
        """, (sitename, pattern, _flow_from))
        row = cur.fetchone()
        avgs[key] = round(float(row[0]), 2) if row and row[0] else 0.0
    exceeds = avgs["inflow"] > 0 and avgs["outflow"] > avgs["inflow"] * 1.1
    return avgs["inflow"], avgs["outflow"], exceeds


def _check_valve_closed(cur, sitename: str, direction: str, ref_time=None) -> bool:
    """밸브 차단 여부 확인 (FULL OPEN 태그 val=0이면 차단)."""
    from datetime import datetime, timedelta
    _rt = ref_time or datetime.now()
    _valve_from = (_rt - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("""
        SELECT ti.tagsn FROM tb_tag_info ti
        WHERE ti.sitename = %s AND ti.facilitytype = '배수지'
          AND ti.tagtype = 'Digital Input'
          AND ti.datainfo ILIKE %s
        LIMIT 3
    """, (sitename, f"%{direction}밸브%OPEN%"))
    valve_tags = [r[0] for r in cur.fetchall()]
    if not valve_tags:
        return False
    cur.execute("""
        SELECT DISTINCT ON (tagsn) tagsn, val
        FROM tb_tag_raw_data
        WHERE tagsn = ANY(%s) AND logtime >= %s::timestamp
        ORDER BY tagsn, logtime DESC
    """, (valve_tags, _valve_from))
    results = cur.fetchall()
    return bool(results) and all(r[1] == 0 for r in results)


def _get_upstream_hh_set(cur, upstream_sitename: str) -> float:
    """상류 가압장의 수위 HH SET 값 조회."""
    cur.execute("""
        SELECT r.val
        FROM tb_tag_info ti
        JOIN LATERAL (
            SELECT val FROM tb_tag_raw_data r
            WHERE r.tagsn = ti.tagsn ORDER BY logtime DESC LIMIT 1
        ) r ON true
        WHERE ti.sitename = %s AND ti.tagtype = 'Analog Output'
          AND ti.datainfo ~* '수위.*HH.*SET'
        LIMIT 1
    """, (upstream_sitename,))
    row = cur.fetchone()
    return float(row[0]) if row and row[0] else 0.0


def _determine_severity(
    direction, pump_status, inlet_closed, outlet_closed,
    outflow_exceeds, inflow_avg, outflow_avg,
    supply_time, all_pumps_running, upstream_set, hh_set,
) -> tuple[str, str]:
    """심각도 판정 + 원인 요약 생성."""
    severity = "info"
    causes = []

    if direction == "LL":
        if pump_status == "정지":
            severity = "critical"
            causes.append("상류 가압장 펌프 전수 정지")
        if inlet_closed:
            severity = "critical"
            causes.append("유입밸브 차단 상태")
        if outflow_exceeds:
            if severity != "critical":
                severity = "warning"
            pct = round((outflow_avg - inflow_avg) / inflow_avg * 100, 1) if inflow_avg > 0 else 0
            causes.append(f"유출량이 유입량 대비 {pct}% 초과")
        if supply_time is not None and supply_time < 2:
            if severity != "critical":
                severity = "warning"
            causes.append(f"용수공급가능시간 {supply_time:.1f}시간 (2시간 미만)")
        if not causes:
            causes.append("자연 수위 변동 (특이 원인 미감지)")

    elif direction == "HH":
        if outlet_closed:
            severity = "critical"
            causes.append("유출밸브 차단 상태")
        if all_pumps_running:
            if severity != "critical":
                severity = "warning"
            causes.append("상류 가압펌프 전수 연속 운전")
        if upstream_set > 0 and hh_set > 0 and upstream_set > hh_set:
            causes.append(f"상류 설정수위({upstream_set}m) > 배수지 HH({hh_set}m)")
        if not causes:
            causes.append("자연 수위 변동 (특이 원인 미감지)")
    else:
        causes.append("수위 안정 상태 (뚜렷한 방향성 없음)")

    return severity, " / ".join(causes)


