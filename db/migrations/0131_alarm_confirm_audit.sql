-- 0131_alarm_confirm_audit.sql
-- 경보 확인 책임 추적 — 확인자·확인 시각 (docs/alarm-confirm-audit-spec.md)
--
-- 배경: alarm_confirm_yn='Y' 만 있고 "누가 언제" 확인했는지가 없다.
--   공공 납품의 감사 추적 요건("이 경보를 누가 인지했는가")에 답할 수 없고,
--   교대 환경에서 확인 책임이 근무자 간에 증발한다.
--   확인율 0.3%(90일 12,053건 중 36건) 개선의 전제이기도 하다 — 책임이
--   기록되지 않는 행동은 하지 않게 된다.
--
-- 기존 행: confirmed_by/at 은 NULL 로 남긴다. 과거 확인 건의 확인자를
--   소급 추정하지 않는다 — 감사 컬럼에 추정값을 넣는 것이 더 해롭다.
--
-- 롤백: 파일 하단 ROLLBACK 블록

BEGIN;

ALTER TABLE tb_equipment_alarm_report
    ADD COLUMN IF NOT EXISTS confirmed_by varchar(50),
    ADD COLUMN IF NOT EXISTS confirmed_at timestamptz;

COMMENT ON COLUMN tb_equipment_alarm_report.confirmed_by IS
  '경보 확인자 user_id (alarm_confirm_yn=Y 처리 주체). NULL=기록 도입 전 확인 건';
COMMENT ON COLUMN tb_equipment_alarm_report.confirmed_at IS
  '경보 확인 시각. info_updated 와 달리 확인 행위만 기록';

COMMIT;

-- =============================================================================
-- ROLLBACK
-- =============================================================================
-- ALTER TABLE tb_equipment_alarm_report
--     DROP COLUMN IF EXISTS confirmed_by,
--     DROP COLUMN IF EXISTS confirmed_at;
