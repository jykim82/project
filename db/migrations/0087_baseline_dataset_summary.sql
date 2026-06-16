-- Migration 0087: 트렌드 GBT baseline 회차별 데이터셋 요약 컬럼 (박제)
-- 사양: docs/trend-baseline-gbt-spec.md §2 / §6.2
--
-- 재훈련 회차마다 "어떤 데이터로 학습됐나" 를 동결 기록한다. 기간·총 시간행수·
-- 종류별 태그수·skip 사유별·14일 미달 태그수 등을 jsonb 로 박제 → /admin/baseline-eval
-- 화면에서 데이터셋 현황과 회차별 추세를 확인한다.
--
-- 기존 회차 행은 NULL — 화면은 NULL 안전 처리.
--
-- 롤백:
--   ALTER TABLE tb_baseline_model_run DROP COLUMN IF EXISTS dataset_summary;

BEGIN;

ALTER TABLE tb_baseline_model_run
    ADD COLUMN IF NOT EXISTS dataset_summary jsonb;

COMMENT ON COLUMN tb_baseline_model_run.dataset_summary
    IS '회차별 학습 데이터셋 요약(기간·행수·종류별 태그·skip) — 박제 (§2)';

COMMIT;
