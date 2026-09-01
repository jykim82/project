-- 0140_alarm_escalation.sql
-- 미확인 경보 메신저 상신 (docs/alarm-confirm-audit-spec.md P2)
--
-- 경고(심각) 경보가 N분째 미확인이면 메신저 전체 채널로 1회 상신한다.
-- escalated_at: 상신 사실 기록 — 알람 판정 데이터가 아니라 발송 감사
-- (재상신 방지 멱등 기준). 예측·파생 신호를 알람 테이블에 "쓰지 않는다"
-- 원칙과 구분: 이것은 이 경보 자신의 알림 이력이다.
--
-- 롤백: 파일 하단 ROLLBACK 블록

BEGIN;

ALTER TABLE tb_equipment_alarm_report
    ADD COLUMN IF NOT EXISTS escalated_at timestamptz;

COMMENT ON COLUMN tb_equipment_alarm_report.escalated_at IS
  '미확인 상신(메신저) 발송 시각 — 1회 발송 멱등 기준 (0140)';

-- 상신 대상 스캔용 부분 인덱스 (진행중·미확인·미상신·경고)
CREATE INDEX IF NOT EXISTS idx_alarm_report_escalation
    ON tb_equipment_alarm_report (alarm_start_time)
    WHERE alarm_status = '진행중' AND alarm_confirm_yn = 'N'
      AND escalated_at IS NULL AND alarm_severity = '경고';

-- 상신 기준(분) 설정 — 0 이면 비활성. 사이트 설정에서 조정
INSERT INTO tb_comm_code (region, grp_cd, comm_cd, comm_nm, comm_val, use_yn)
VALUES ('R01', 'SITE_SETTING', 'ALARM_ESCALATION_MIN',
        '미확인 경보 메신저 상신 기준(분, 0=끔)', '10', 'Y')
ON CONFLICT (region, grp_cd, comm_cd) DO NOTHING;

COMMIT;

-- =============================================================================
-- ROLLBACK
-- =============================================================================
-- BEGIN;
-- DELETE FROM tb_comm_code WHERE grp_cd='SITE_SETTING' AND comm_cd='ALARM_ESCALATION_MIN';
-- DROP INDEX IF EXISTS idx_alarm_report_escalation;
-- ALTER TABLE tb_equipment_alarm_report DROP COLUMN IF EXISTS escalated_at;
-- COMMIT;
