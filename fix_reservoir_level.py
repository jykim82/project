"""
fn_reservoir_level_summary 최적화
기존: tb_tag_raw_data 전체 JOIN → 168K block reads, 13~26s
개선: tagsn 배열 사전 추출 + ANY(array) + 7일 시간 범위 → idx_tag_raw_tagsn_time 인덱스 활용
"""
import sys, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import psycopg2

conn = psycopg2.connect(host='localhost', port=5433, dbname='slm', user='slm_dev', password='slm_dev_1234')
conn.set_client_encoding('UTF8')
cur = conn.cursor()

NEW_FUNC = """
CREATE OR REPLACE FUNCTION fn_reservoir_level_summary(
    p_sitename     text DEFAULT NULL,
    p_facilitytype text DEFAULT NULL,
    p_datainfo     text DEFAULT NULL
)
RETURNS TABLE(
    log_time         text,
    out_sitename     text,
    out_facilitytype text,
    out_datainfo     text,
    avg_latest       numeric,
    latest_val       numeric,
    avg_month        numeric,
    avg_year         numeric,
    unit             text
)
LANGUAGE plpgsql AS $$
DECLARE
    v_tagsns text[];
BEGIN
    -- Step 1: 대상 tagsn 배열 추출 (tb_tag_info 단독 스캔, 빠름)
    SELECT ARRAY(
        SELECT tagsn
        FROM tb_tag_info
        WHERE tagtype = 'Analog Input'
          AND (p_sitename     IS NULL OR sitename     = p_sitename)
          AND (p_facilitytype IS NULL OR facilitytype = p_facilitytype)
          AND (p_datainfo     IS NULL OR datainfo ILIKE '%' || p_datainfo || '%')
    ) INTO v_tagsns;

    IF array_length(v_tagsns, 1) IS NULL THEN
        RETURN;
    END IF;

    RETURN QUERY
    WITH
    -- Step 2: 최신값 — tagsn = ANY(array) + 7일 범위 → idx_tag_raw_tagsn_time 인덱스 스캔
    latest AS (
        SELECT DISTINCT ON (r.tagsn)
            r.tagsn,
            r.val::numeric  AS latest_val,
            r.logtime
        FROM tb_tag_raw_data r
        WHERE r.tagsn = ANY(v_tagsns)
          AND r.logtime >= NOW() - INTERVAL '7 days'
        ORDER BY r.tagsn, r.logtime DESC
    ),
    -- Step 3: 태그 메타 (별도 조인 — latest CTE와 분리)
    tag_info AS (
        SELECT i.tagsn,
               i.sitename::text     AS sitename,
               i.facilitytype::text AS facilitytype,
               i.datainfo::text     AS datainfo,
               i.unit::text         AS tag_unit
        FROM tb_tag_info i
        WHERE i.tagsn = ANY(v_tagsns)
    ),
    -- Step 4: 월/연 평균 (mv_reservoir_level_stats_all 사전 집계 뷰, 빠름)
    level_stats AS (
        SELECT
            sitename::text     AS sitename,
            datainfo::text     AS datainfo,
            ROUND(AVG(CASE WHEN bucket_day >= now() - interval '1 month' THEN avg_daily_level END), 2) AS avg_month,
            ROUND(AVG(CASE WHEN bucket_day >= now() - interval '1 year'  THEN avg_daily_level END), 2) AS avg_year
        FROM mv_reservoir_level_stats_all
        WHERE (p_sitename IS NULL OR sitename = p_sitename)
          AND (p_datainfo IS NULL OR datainfo ILIKE '%' || p_datainfo || '%')
        GROUP BY sitename, datainfo
    )
    SELECT
        to_char(l.logtime, 'YYYY-MM-DD HH24:MI:SS')    AS log_time,
        ti.sitename,
        ti.facilitytype,
        ti.datainfo,
        ROUND(AVG(l.latest_val) OVER (), 2)             AS avg_latest,
        ROUND(l.latest_val, 2)                          AS latest_val,
        ls.avg_month,
        ls.avg_year,
        ti.tag_unit
    FROM latest l
    JOIN  tag_info   ti ON ti.tagsn    = l.tagsn
    LEFT JOIN level_stats ls ON ls.sitename = ti.sitename
                             AND ls.datainfo = ti.datainfo
    ORDER BY ti.datainfo;
END;
$$;
"""

print("함수 재작성 중...")
cur.execute(NEW_FUNC)
conn.commit()
print("완료. 성능 테스트...")

# 테스트 1: 남산 배수지 수위 (기존 느린 케이스)
for label, sn, ft, di in [
    ("남산/배수지/수위", "남산", "배수지", "수위"),
    ("전체/배수지/수위", None, "배수지", "수위"),
    ("합덕일반/배수지/수위", "합덕일반", "배수지", "수위"),
]:
    t0 = time.perf_counter()
    cur.execute("SELECT * FROM fn_reservoir_level_summary(%s, %s, %s)", (sn, ft, di))
    rows = cur.fetchall()
    elapsed = (time.perf_counter() - t0) * 1000
    print(f"  {label}: {len(rows)}행 {elapsed:.0f}ms")

cur.close()
conn.close()
print("Done.")
