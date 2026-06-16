-- Migration 0085: 트렌드 GBT baseline 성능 기록 테이블 (P2)
-- 사양: docs/trend-baseline-gbt-spec.md §6.2
--
-- 트렌드 "평소 대비" 정상 기대값 GBT 모델의 재훈련 회차별 지표(tb_baseline_model_run)
-- 와 태그별 지표(tb_baseline_tag_metric)를 적재한다. /admin/baseline-eval 화면이
-- 이 두 테이블을 조회해 GBT vs hourly_mean 개선율·커버리지·최악 태그를 보여준다.
--
-- 기록은 부가 기능 — 테이블이 없거나 비어 있어도 학습·추론은 정상 동작.
-- region 기반 멀티테넌시 유지.
--
-- 롤백 (데이터 손실 = 과거 회차 지표만 사라짐, 모델 동작 무관):
--   DROP TABLE IF EXISTS tb_baseline_tag_metric;
--   DROP TABLE IF EXISTS tb_baseline_model_run;

BEGIN;

-- 회차별 (재훈련 1회 = 1행)
CREATE TABLE IF NOT EXISTS tb_baseline_model_run (
    id                bigserial PRIMARY KEY,
    region            character varying NOT NULL DEFAULT 'R01',
    model_version     character varying NOT NULL,
    trained_at        timestamptz NOT NULL DEFAULT now(),
    train_window_days integer,
    n_tags_trained    integer,
    n_tags_fallback   integer,
    overall_mae       double precision,
    overall_rmse      double precision,
    coverage_pct      double precision,
    mae_hourly_mean   double precision,
    improvement_pct   double precision,
    feature_set       character varying,
    status            character varying NOT NULL DEFAULT 'ok',
    CONSTRAINT tb_baseline_model_run_uq UNIQUE (region, model_version)
);

CREATE INDEX IF NOT EXISTS ix_baseline_model_run_region_time
    ON tb_baseline_model_run (region, trained_at DESC);

-- 태그별 (회차 × 태그)
CREATE TABLE IF NOT EXISTS tb_baseline_tag_metric (
    id            bigserial PRIMARY KEY,
    region        character varying NOT NULL DEFAULT 'R01',
    model_version character varying NOT NULL,
    tagsn         character varying NOT NULL,
    mae           double precision,
    rmse          double precision,
    sigma         double precision,
    coverage_pct  double precision,
    n_samples     integer,
    method        character varying NOT NULL DEFAULT 'gbt',
    CONSTRAINT tb_baseline_tag_metric_uq UNIQUE (region, model_version, tagsn)
);

CREATE INDEX IF NOT EXISTS ix_baseline_tag_metric_run
    ON tb_baseline_tag_metric (region, model_version);
CREATE INDEX IF NOT EXISTS ix_baseline_tag_metric_worst
    ON tb_baseline_tag_metric (region, model_version, mae DESC);

COMMENT ON TABLE tb_baseline_model_run  IS '트렌드 GBT baseline 재훈련 회차별 지표 (§6.2)';
COMMENT ON TABLE tb_baseline_tag_metric IS '트렌드 GBT baseline 태그별 지표 (§6.2)';

COMMIT;
