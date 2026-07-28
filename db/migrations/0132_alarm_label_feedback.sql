-- 0132_alarm_label_feedback.sql
-- 경보 판정 라벨 3분법 (docs/alarm-label-feedback-spec.md — 로드맵 C P1)
--
-- 기존 is_false_alarm 은 2분법('Y'/'N')인데 'N' 이 기본값이라
-- "실제 이상으로 확인됨"과 "아직 판정 안 함"을 구분하지 못한다.
-- 오탐률(= 오탐 / 판정된 것)을 계산하려면 분모가 서야 하므로
-- 명시적 3분법 라벨을 신설한다: real(실제) / false(오탐) / check(점검필요).
--
-- is_false_alarm 은 유지한다 — Node-RED 필터·경보분석 화면이 사용 중이고,
-- 라벨 'false' 기록 시 백엔드가 is_false_alarm='Y' 도 함께 갱신해
-- 두 체계가 어긋나지 않게 한다 (정본은 anomaly_label).
--
-- 백필: is_false_alarm='Y' 1,457건 → anomaly_label='false'.
--   라벨 값 자체는 기존에 기록된 사실이므로 옮긴다. 단 labeled_by/at 은
--   NULL — 판정자 소급 추정 금지 (0131 confirmed_by 와 같은 원칙).
--   'N' 은 백필하지 않는다 — 기본값이지 판정이 아니다.
--
-- 롤백: 파일 하단 ROLLBACK 블록

BEGIN;

ALTER TABLE tb_equipment_alarm_report
    ADD COLUMN IF NOT EXISTS anomaly_label varchar(10)
        CHECK (anomaly_label IN ('real', 'false', 'check')),
    ADD COLUMN IF NOT EXISTS labeled_by varchar(50),
    ADD COLUMN IF NOT EXISTS labeled_at timestamptz;

COMMENT ON COLUMN tb_equipment_alarm_report.anomaly_label IS
  '운영자 판정: real(실제 이상)/false(오탐)/check(점검필요). NULL=미판정. 정본 — is_false_alarm 은 파생';
COMMENT ON COLUMN tb_equipment_alarm_report.labeled_by IS
  '판정자 user_id. NULL=백필(판정자 소급 추정 금지)';
COMMENT ON COLUMN tb_equipment_alarm_report.labeled_at IS '판정 시각';

-- 기존 오탐 판정 백필 (판정자·시각은 NULL)
UPDATE tb_equipment_alarm_report
SET anomaly_label = 'false'
WHERE is_false_alarm = 'Y' AND anomaly_label IS NULL;

-- 오탐률 추이 집계용 부분 인덱스 — 라벨된 행만 (전체의 ~7%)
CREATE INDEX IF NOT EXISTS idx_alarm_report_labeled
    ON tb_equipment_alarm_report (alarm_start_time DESC)
    WHERE anomaly_label IS NOT NULL;

COMMIT;

-- 확인
-- SELECT anomaly_label, count(*) FROM tb_equipment_alarm_report GROUP BY 1;

-- =============================================================================
-- ROLLBACK
-- =============================================================================
-- DROP INDEX IF EXISTS idx_alarm_report_labeled;
-- ALTER TABLE tb_equipment_alarm_report
--     DROP COLUMN IF EXISTS anomaly_label,
--     DROP COLUMN IF EXISTS labeled_by,
--     DROP COLUMN IF EXISTS labeled_at;
