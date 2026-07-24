-- 0116: 1시간 사전집계 cagg — ANOMALY_SCAN_ALL 365일 통계 가속
-- (docs/review-items.md 2026-07-23 "SCAN_ALL 1,217초" 선택지 a)
--
-- 원인: 스캔 SQL 의 raw_adaptive CTE 가 365일 5분 버킷(85M 행, ~5GB)을
-- 물질화 후 4회 반복 스캔 → 캐시 빌드 20분. 본 cagg 는 5분 통계를 1시간으로
-- 접어(12×) 장기 통계 구간의 스캔량을 ~7M 행으로 줄인다.
--
-- 설계 (의미 보존):
-- - nz_* = approx(=(min+max)/2) > 0.001 인 5분 샘플만의 합·제곱합·건수
--   → 상위 쿼리가 mean=Σnz_sum/Σnz_cnt, var=Σnz_sumsq/Σnz_cnt−mean² 로
--   **5분 샘플 수준의 분산을 그대로 재구성** (시간평균의 분산 축소 왜곡 없음)
-- - nz_flat_cnt·nz_rmin·nz_rmax → hourly_holding 판정
--   (전부 flat AND 반올림 0.1 자리 distinct≤1 ⟺ rmin=rmax) 원본과 동치
-- - cnt = 전체 샘플 수 (active_pct 분모)
-- 롤백: DROP MATERIALIZED VIEW IF EXISTS cagg_1h_raw_stats_ai CASCADE;

CREATE MATERIALIZED VIEW IF NOT EXISTS cagg_1h_raw_stats_ai
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', bucket) AS bucket_hr,
    tagsn,
    COUNT(*)                                         AS cnt,
    SUM(CASE WHEN (min_val + max_val) / 2.0 > 0.001
             THEN (min_val + max_val) / 2.0 END)     AS nz_sum,
    SUM(CASE WHEN (min_val + max_val) / 2.0 > 0.001
             THEN POWER((min_val + max_val) / 2.0, 2) END) AS nz_sumsq,
    COUNT(CASE WHEN (min_val + max_val) / 2.0 > 0.001 THEN 1 END) AS nz_cnt,
    COUNT(CASE WHEN (min_val + max_val) / 2.0 > 0.001
                AND min_val = max_val THEN 1 END)    AS nz_flat_cnt,
    MIN(CASE WHEN (min_val + max_val) / 2.0 > 0.001
             THEN ROUND(((min_val + max_val) / 2.0)::numeric, 1) END) AS nz_rmin,
    MAX(CASE WHEN (min_val + max_val) / 2.0 > 0.001
             THEN ROUND(((min_val + max_val) / 2.0)::numeric, 1) END) AS nz_rmax
FROM cagg_5min_raw_stats_ai
GROUP BY 1, 2
WITH NO DATA;

-- 1시간 주기 자동 갱신 (최근 2일 창 — 지연 유입 보정)
SELECT add_continuous_aggregate_policy('cagg_1h_raw_stats_ai',
    start_offset => INTERVAL '2 days',
    end_offset   => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => true);

-- 초기 백필은 별도 실행 (수 분 소요):
-- CALL refresh_continuous_aggregate('cagg_1h_raw_stats_ai', NULL, now() - interval '1 hour');
