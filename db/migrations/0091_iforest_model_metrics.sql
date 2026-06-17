-- Migration 0091: IForest 이상탐지 모델 성능 기록 테이블
-- 사양: docs/iforest-eval-spec.md §4
--
-- IForest 재훈련 회차별 지표(tb_iforest_model_run)와 모델별(시설/태그) 지표
-- (tb_iforest_model_metric)를 적재한다. /admin/iforest-eval 화면이 이 두 테이블을
-- 조회해 커버리지·캘리브레이션·Tier 분포·최악 캘리브레이션 모델을 보여준다.
--
-- 비지도 모델이라 정답 레이블이 없어 "정확도%"가 아니라 안정성(관측 이상률이
-- 목표 contamination 에서 벗어난 정도 = calibration_err)·커버리지를 기록한다.
--
-- 기록은 부가 기능 — 테이블이 없거나 비어 있어도 학습·예측은 정상 동작.
-- region 기반 멀티테넌시 유지.
--
-- 롤백 (데이터 손실 = 과거 회차 지표만 사라짐, 모델 동작 무관):
--   DROP TABLE IF EXISTS tb_iforest_model_metric;
--   DROP TABLE IF EXISTS tb_iforest_model_run;

BEGIN;

-- 회차별 (재훈련 1회 = 1행)
CREATE TABLE IF NOT EXISTS tb_iforest_model_run (
    id                 bigserial PRIMARY KEY,
    region             character varying NOT NULL DEFAULT 'R01',
    model_version      character varying NOT NULL,
    trained_at         timestamptz NOT NULL DEFAULT now(),
    train_window_days  integer,
    tier1_count        integer,
    tier2_count        integer,
    total_models       integer,
    n_eligible         integer,
    n_skipped          integer,
    coverage_pct       double precision,
    mean_anomaly_rate  double precision,
    mean_contamination double precision,
    calibration_err    double precision,
    mean_score         double precision,
    feature_set        character varying,
    status             character varying NOT NULL DEFAULT 'ok',
    CONSTRAINT tb_iforest_model_run_uq UNIQUE (region, model_version)
);

CREATE INDEX IF NOT EXISTS ix_iforest_model_run_region_time
    ON tb_iforest_model_run (region, trained_at DESC);

-- 모델별 (회차 × 모델: tier-1 시설 또는 tier-2 태그)
CREATE TABLE IF NOT EXISTS tb_iforest_model_metric (
    id              bigserial PRIMARY KEY,
    region          character varying NOT NULL DEFAULT 'R01',
    model_version   character varying NOT NULL,
    tier            integer NOT NULL,
    entity_key      character varying NOT NULL,
    sitename        character varying,
    facilitytype    character varying,
    n_samples       integer,
    n_features      integer,
    contamination   double precision,
    anomaly_rate    double precision,
    mean_score      double precision,
    score_std       double precision,
    calibration_err double precision,
    CONSTRAINT tb_iforest_model_metric_uq UNIQUE (region, model_version, tier, entity_key)
);

CREATE INDEX IF NOT EXISTS ix_iforest_model_metric_run
    ON tb_iforest_model_metric (region, model_version);
CREATE INDEX IF NOT EXISTS ix_iforest_model_metric_worst
    ON tb_iforest_model_metric (region, model_version, calibration_err DESC);

COMMENT ON TABLE tb_iforest_model_run    IS 'IForest 재훈련 회차별 지표 (iforest-eval-spec §4)';
COMMENT ON TABLE tb_iforest_model_metric IS 'IForest 모델별(시설/태그) 지표 (iforest-eval-spec §4)';

COMMIT;
