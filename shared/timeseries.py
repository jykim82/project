"""
TimescaleDB 청크 기반 시계열 쿼리 공용 헬퍼.

ai_server.py에서 분리 — trend, causal 등 여러 모듈이 공유.
"""

from collections import defaultdict


def get_chunks_for_range(cur, from_ts: str, to_ts: str) -> list[str]:
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


def query_chunks_agg(
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


def reaggregate(
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


def query_chunks_raw(
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
