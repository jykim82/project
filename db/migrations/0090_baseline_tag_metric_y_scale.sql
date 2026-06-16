-- Migration 0090: 트렌드 GBT baseline 태그별 지표에 실제값 크기(y_scale) 컬럼 추가
-- 사양: docs/trend-baseline-gbt-spec.md §6.3
--
-- holdout 실제값의 평균 절대크기(scale). MAE 를 이 값으로 나눠 단위 무관 정확도%
-- (= 100 × (1 − MAE/y_scale)) 를 산출한다. 유량(LPS 수백)·수위(m 한자리)처럼
-- 단위·스케일이 다른 태그/시설을 같은 정확도 척도로 비교 가능하게 한다.
-- 기존 회차 행은 NULL(정확도 미표시). 학습 시 자동 적재.
--
-- 롤백:
--   ALTER TABLE tb_baseline_tag_metric DROP COLUMN IF EXISTS y_scale;

BEGIN;

ALTER TABLE tb_baseline_tag_metric
    ADD COLUMN IF NOT EXISTS y_scale double precision;

COMMENT ON COLUMN tb_baseline_tag_metric.y_scale
    IS 'holdout 실제값 평균 절대크기 — 단위 무관 정확도%(100×(1−MAE/y_scale)) 산출용 (§6.3)';

COMMIT;
