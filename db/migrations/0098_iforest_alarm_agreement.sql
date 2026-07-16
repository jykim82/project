-- 0098: IForest P1.5 — 알람-일치율(weak-label) 평가 지표 컬럼
-- 실제 알람 이벤트(tb_equipment_alarm_report)를 약한 레이블로 탐지 일치율 산출.
-- 사양: docs/iforest-eval-spec.md §P1.5
-- 롤백: ALTER TABLE tb_iforest_model_run
--         DROP COLUMN IF EXISTS alarm_events_total,
--         DROP COLUMN IF EXISTS alarm_events_evaluated,
--         DROP COLUMN IF EXISTS alarm_recall_pct,
--         DROP COLUMN IF EXISTS alarm_window_anomaly_rate,
--         DROP COLUMN IF EXISTS alarm_lift;

ALTER TABLE tb_iforest_model_run
  ADD COLUMN IF NOT EXISTS alarm_events_total        integer,
  ADD COLUMN IF NOT EXISTS alarm_events_evaluated    integer,
  ADD COLUMN IF NOT EXISTS alarm_recall_pct          numeric(6,2),
  ADD COLUMN IF NOT EXISTS alarm_window_anomaly_rate numeric(6,2),
  ADD COLUMN IF NOT EXISTS alarm_lift                numeric(8,2);

COMMENT ON COLUMN tb_iforest_model_run.alarm_events_total IS
  'P1.5: 평가 창 내 아날로그 계열(수위/압력/유량) 알람 이벤트 수';
COMMENT ON COLUMN tb_iforest_model_run.alarm_events_evaluated IS
  'P1.5: Tier-2 태그 모델로 평가 가능했던 이벤트 수 (커버리지)';
COMMENT ON COLUMN tb_iforest_model_run.alarm_recall_pct IS
  'P1.5: 알람 이벤트 ±60분 구간에서 IForest 이상 판정된 비율 (재현율 proxy — 알람 오탐 포함, 정밀도 아님)';
COMMENT ON COLUMN tb_iforest_model_run.alarm_window_anomaly_rate IS
  'P1.5: 알람 구간 샘플 이상률(%) — lift 분자';
COMMENT ON COLUMN tb_iforest_model_run.alarm_lift IS
  'P1.5: 알람 구간 이상률 / 평상시 이상률(mean_anomaly_rate). >1 이면 알람과 정렬';
