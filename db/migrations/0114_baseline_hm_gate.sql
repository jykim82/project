-- 0114: GBT baseline 태그별 hourly_mean 게이트 (docs/trend-baseline-gbt-spec.md)
-- 배경: 2026-07-23 회차 홀드아웃 개선율 -3.6% — 일부 태그는 GBT 가
-- hourly_mean 보다 부정확. 홀드아웃에서 hm 이 명확히 우세한 태그는 추론 시
-- hourly_mean 으로 폴백(게이트)하고, 회차 지표에 태그별 hm MAE·게이트 여부를
-- 박제해 평가 화면에서 확인 가능하게 한다.
-- 롤백: ALTER TABLE tb_baseline_tag_metric
--         DROP COLUMN IF EXISTS mae_hourly_mean, DROP COLUMN IF EXISTS hm_gated;

ALTER TABLE tb_baseline_tag_metric
  ADD COLUMN IF NOT EXISTS mae_hourly_mean double precision,
  ADD COLUMN IF NOT EXISTS hm_gated boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN tb_baseline_tag_metric.mae_hourly_mean IS
  '같은 홀드아웃 구간에 hourly_mean(요일×시각 평균)을 적용했을 때의 MAE — GBT 대비 비교군';
COMMENT ON COLUMN tb_baseline_tag_metric.hm_gated IS
  'true = 홀드아웃에서 hm 이 GBT 보다 우세해 추론 시 hourly_mean 으로 폴백되는 태그';
