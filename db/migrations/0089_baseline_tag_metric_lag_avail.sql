-- Migration 0089: 트렌드 GBT baseline 태그별 지표에 lag 확보율 컬럼 추가
-- 사양: docs/trend-baseline-gbt-spec.md §6.3
--
-- 자기 과거값(lag_24h & lag_168h) 피처를 둘 다 확보한 holdout 행 비율(%).
-- 낮으면 모델이 자기 이력 없이 시설유형 prior 로 예측 → 큰 MAE 가 "데이터 부족"
-- 기여인지 검증 가능. 학습창이 짧으면(dev) lag_168h 결측 → 0% 근처, 운영 60일
-- 이면 ~100%. 같은 태그를 회차 간 (lag 확보율↑ vs MAE↓) 로 대조해 가설 검증.
-- 기존 회차 행은 NULL. 학습 시 자동 적재.
--
-- 롤백:
--   ALTER TABLE tb_baseline_tag_metric DROP COLUMN IF EXISTS lag_avail_pct;

BEGIN;

ALTER TABLE tb_baseline_tag_metric
    ADD COLUMN IF NOT EXISTS lag_avail_pct double precision;

COMMENT ON COLUMN tb_baseline_tag_metric.lag_avail_pct
    IS 'lag_24h & lag_168h 둘 다 확보한 holdout 행 비율(%) — 데이터부족 기여 검증용 (§6.3)';

COMMIT;
