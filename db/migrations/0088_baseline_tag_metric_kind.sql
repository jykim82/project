-- Migration 0088: 트렌드 GBT baseline 태그별 지표에 종류(trend_kind) 컬럼 추가
-- 사양: docs/trend-baseline-gbt-spec.md §6.3
--
-- /admin/baseline-eval 최악 태그 표의 종류 필터(유량/수위/압력/수질/기타)용.
-- 절대 MAE 정렬은 스케일 큰 유량이 상위를 점유 → 종류별로 좁혀 볼 수 있게 한다.
-- 기존 회차 행은 NULL(필터 '전체'로 노출). 학습 시 자동 적재.
--
-- 롤백:
--   ALTER TABLE tb_baseline_tag_metric DROP COLUMN IF EXISTS trend_kind;

BEGIN;

ALTER TABLE tb_baseline_tag_metric
    ADD COLUMN IF NOT EXISTS trend_kind character varying;

CREATE INDEX IF NOT EXISTS ix_baseline_tag_metric_kind
    ON tb_baseline_tag_metric (region, model_version, trend_kind);

COMMENT ON COLUMN tb_baseline_tag_metric.trend_kind
    IS '태그 종류(flow/level/pressure/quality/other) — 최악 태그 종류 필터용 (§6.3)';

COMMIT;
