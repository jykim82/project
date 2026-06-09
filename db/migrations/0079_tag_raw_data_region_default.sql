-- Migration 0079: tb_tag_raw_data.region default 'KW'→'R01' 통일
-- 사양: docs/dev-tag-ingest-spec.md (region 통일 정책)
--
-- 배경: 다른 모든 테이블 region='R01' 인데 tb_tag_raw_data 만 'KW' (수집 데몬
--       env LOCAL_REGION default 'KW' 영향). 백엔드 쿼리는 region 필터 미사용
--       (tagsn 기반) 이라 동작 영향 없음. 일관성을 위해 정책 통일.
--
-- 변경:
--   1. column default 'KW' → 'R01' (신규 INSERT 영향)
--   2. 수집 데몬: docker-compose.dev.yml LOCAL_REGION default 'R01' (별도 적용)
--   3. 기존 3.5억 행 'KW' 데이터: **이 마이그레이션에서는 UPDATE 안 함**
--      → 점진 cleanup 별도 운영 (chunk 단위, off-peak 시간) — 비고 참조
--
-- 비고 (기존 데이터 점진 UPDATE):
--   SELECT chunk_schema, chunk_name FROM timescaledb_information.chunks
--    WHERE hypertable_name='tb_tag_raw_data' ORDER BY range_start;
--   -- chunk 별로:
--   UPDATE <chunk_name> SET region='R01' WHERE region='KW';
--   -- 19개 chunk, 각 chunk 한 번에 UPDATE 가능. 1 chunk ≈ 수억 행 → 시간·디스크
--   -- 부담. 운영 영향 없으므로 미실행 권장. 통일 필요 시 별도 스크립트.
--
-- 롤백:
--   ALTER TABLE tb_tag_raw_data ALTER COLUMN region SET DEFAULT 'KW';

BEGIN;

ALTER TABLE tb_tag_raw_data ALTER COLUMN region SET DEFAULT 'R01';

COMMIT;
