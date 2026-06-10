-- Migration 0081: tb_equipment_alarm_report 진행중 알람 부분 인덱스
-- 사양: docs/performance/alarm-notifications-index.md (성능 검토 2026-06-10)
--
-- 배경: /monitoring/alarm-notifications 가 30초마다 모든 페이지에서 호출
--       (AlarmNotificationBell + AlarmCrisisModal). 쿼리:
--         SELECT ... FROM tb_equipment_alarm_report
--          WHERE alarm_status='진행중'
--          ORDER BY alarm_start_time DESC LIMIT 5
--       현재 13,621 행 / 진행중 49 행 → EXPLAIN 0.311ms (rapid). 데이터
--       증가 대비 부분 인덱스로 future-proof.
--
-- 인덱스:
--   부분 인덱스 (WHERE alarm_status='진행중') — 진행중 행만 색인 →
--   인덱스 크기 작음, 쿼리 시 즉시 LIMIT 5 도달.
--
-- 효과 예측: 진행중 알람이 1만 건 이상 누적 시 EXPLAIN 시간 큰 폭 감소
-- (Index Scan with LIMIT 가 Seq Scan 보다 빠름).
--
-- 롤백:
--   DROP INDEX IF EXISTS idx_alarm_report_ongoing;

BEGIN;

CREATE INDEX IF NOT EXISTS idx_alarm_report_ongoing
  ON tb_equipment_alarm_report (alarm_start_time DESC)
 WHERE alarm_status = '진행중';

COMMIT;
