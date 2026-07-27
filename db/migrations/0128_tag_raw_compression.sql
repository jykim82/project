-- 0128_tag_raw_compression.sql
-- tb_tag_raw_data 컬럼스토어 압축 + 자동 압축 정책
--
-- 배경:
--   2026-07-27 조사 시점 tb_tag_raw_data 는 6개월치(2/2~7/27) 447,356,770 행에
--   120 GB — 데이터 33 GB + **인덱스 88 GB**. 압축·보존정책 모두 없었다.
--   이 속도면 1년차에 240 GB 로, 저사양 온프레미스 납품 서버에서 감당이 안 된다.
--
--   더 큰 문제는 디스크가 아니라 캐시다. shared_buffers 가 4 GB 인데 작업셋이
--   120 GB 라 장기 조회는 전부 디스크 I/O 가 된다 — 1년 트렌드 조회 실측 25.2초.
--
-- 왜 이 설정인가:
--   segmentby=tagsn  — 앱의 조회는 예외 없이 `tagsn = ANY(...)` 로 시작한다
--     (shared/timeseries.py query_chunks_agg, endpoints/trend.py). tagsn 으로
--     세그먼트하면 해당 태그의 압축 배치만 읽고 나머지는 건드리지 않는다.
--   orderby=logtime DESC — 같은 태그 안에서 시간순 델타 인코딩이 걸리고,
--     "최신값 1건" 조회가 배치 선두에서 끝난다.
--
-- 실측 (2026-07-27, dev):
--   압축비   _hyper_1_26_chunk 3,534 MB → 26 MB (135.7x)
--            초기 5개 청크 누적 29 GB → 99 MB (302.7x)
--   조회     7일 청크 1개 집계 쿼리 1,084 ms(cold) → 10.9 ms
--   무결성   2,700 태그 전부 count·min·max 일치
--            (압축 전 원본에서 만들어진 cagg_5min_raw_stats_ai 와 대조.
--             비교 시 청크 경계는 UTC 이므로 KST 날짜로 자르면 9시간 어긋난다)
--
-- 운영 주의 — compress_chunk 는 AccessExclusiveLock 을 잡는다:
--   1) 락을 "기다리는" 동안 뒤따르는 모든 읽기/쓰기가 그 뒤에 줄 서서
--      head-of-line blocking 이 된다. 실제로 검증 중 Node-RED 알람 INSERT 가
--      9분간 막혔다. 그래서 정책 job 은 한산한 시간대로 고정한다.
--   2) 락을 "잡은 뒤"에도 청크당 20~40초 보유한다 (해당 청크 읽기만 대기).
--      compress_after 를 충분히 크게 둬 활성 조회 구간을 건드리지 않게 한다.
--
-- compress_after 14일 근거:
--   청크 간격이 7일이라 14일이면 최근 2개 청크(24시간·7일 조회 구간)는 항상
--   비압축으로 남는다. 적재는 전진 방향만이라(dev_tools/tag_ingest.py 는
--   watermark = max(logtime) - 5분) 과거 청크로 backfill 이 없다.
--
-- 보존정책(drop_chunks)은 이 마이그레이션에 넣지 않는다 — 원시 데이터 삭제는
--   되돌릴 수 없고, 압축만으로 용량 문제가 해소되므로 별도 결정 사항이다.
--
-- 롤백: 파일 하단 ROLLBACK 블록

BEGIN;

-- 1) 컬럼스토어 설정
ALTER TABLE tb_tag_raw_data SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'tagsn',
    timescaledb.compress_orderby   = 'logtime DESC'
);

COMMIT;

-- 2) 자동 압축 정책 (트랜잭션 밖 — job 등록은 자체 트랜잭션을 쓴다)
--    initial_start: 새벽 3시 KST 고정. 정책 job 은 이 시각을 기준으로
--    schedule_interval(1일) 마다 반복하므로, 낮 시간대 락 경합을 피한다.
SELECT add_compression_policy(
    'tb_tag_raw_data',
    compress_after   => INTERVAL '14 days',
    schedule_interval=> INTERVAL '1 day',
    initial_start    => (date_trunc('day', now() AT TIME ZONE 'Asia/Seoul')
                         + INTERVAL '1 day 3 hours') AT TIME ZONE 'Asia/Seoul'
);

-- 3) 기존 14일 경과 청크 일괄 압축은 이 파일에서 하지 않는다.
--    청크당 20~40초 × N 개라 마이그레이션 실행 시간이 예측 불가하고,
--    운영 중 실행하면 위 락 문제가 그대로 재현된다.
--    최초 1회는 한산한 시간대에 아래를 수동 실행할 것:
--
--      SET lock_timeout = '10s';   -- 대기 큐 선점 방지. 실패하면 재시도
--      SELECT compress_chunk(c.chunk_schema || '.' || c.chunk_name)
--      FROM timescaledb_information.chunks c
--      WHERE c.hypertable_name = 'tb_tag_raw_data'
--        AND c.range_end <= now() - INTERVAL '14 days'
--        AND NOT c.is_compressed
--      ORDER BY c.range_start;
--
--    (한 문장으로 돌리면 첫 실패에 전체가 롤백되므로 청크별로 끊어 실행)

-- =============================================================================
-- ROLLBACK
-- =============================================================================
-- SELECT remove_compression_policy('tb_tag_raw_data');
--
-- -- 압축 해제도 AccessExclusiveLock 을 잡는다. 반드시 청크별로, 한산할 때.
-- SET lock_timeout = '10s';
-- SELECT decompress_chunk(c.chunk_schema || '.' || c.chunk_name)
-- FROM timescaledb_information.chunks c
-- WHERE c.hypertable_name = 'tb_tag_raw_data' AND c.is_compressed
-- ORDER BY c.range_start;
--
-- ALTER TABLE tb_tag_raw_data SET (timescaledb.compress = false);
--
-- 주의: 전량 해제하면 120 GB 로 되돌아간다. 디스크 여유를 먼저 확인할 것.
