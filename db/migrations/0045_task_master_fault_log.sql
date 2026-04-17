-- Migration 0045: tb_task_master 확장 — 설비 장애 이력 관리 통합
-- 목적:
--   1) 현장 작업자가 채팅으로 "신평 배수지 PLC 고장 기록해줘" 자연어 기록
--   2) tb_task_master에 task_category='고장보고' 추가 + 설비 연결 + 사진 첨부
--   3) 년간 장애 통계 + MTBF 분석 가능하도록 뷰 제공
-- 설계 근거: docs/flow-diagram-mode-spec.md (별도 섹션 예정), 채팅 멀티턴 세션
-- 신규 테이블/뷰 없이 기존 tb_task_master 확장 (단일 소스 오브 트루스)

BEGIN;

-- ========================================================================
-- 1. tb_task_master 컬럼 확장
-- ========================================================================
ALTER TABLE tb_task_master
  ADD COLUMN IF NOT EXISTS equipment_id     VARCHAR(64) REFERENCES tb_equipment_info(equipment_id),
  ADD COLUMN IF NOT EXISTS equipmenttype    VARCHAR(50),      -- PLC/가압펌프/유량계/밸브/모뎀 등
  ADD COLUMN IF NOT EXISTS fault_category   VARCHAR(30),      -- 고장/이상/교체/점검 (task_category 보조)
  ADD COLUMN IF NOT EXISTS severity         VARCHAR(20),      -- 경고/주의/정보
  ADD COLUMN IF NOT EXISTS linked_alarm_start TIMESTAMP,      -- 알람 연계 (PK alarm_start_time + tagsn 중 start)
  ADD COLUMN IF NOT EXISTS linked_alarm_tagsn VARCHAR(64),    -- 알람 연계 tagsn
  ADD COLUMN IF NOT EXISTS photo_urls       JSONB,            -- 현장 사진 URL 배열
  ADD COLUMN IF NOT EXISTS recorded_by      VARCHAR(50),      -- 기록자 user_id
  ADD COLUMN IF NOT EXISTS resolved_by      VARCHAR(50),      -- 조치자 user_id
  ADD COLUMN IF NOT EXISTS resolved_at      TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS resolution_note  TEXT,
  ADD COLUMN IF NOT EXISTS status           VARCHAR(20) DEFAULT '진행중';  -- 진행중/완료/보류

-- 인덱스 추가 (조회 성능)
CREATE INDEX IF NOT EXISTS idx_task_equipment_id  ON tb_task_master(equipment_id) WHERE equipment_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_task_category      ON tb_task_master(task_category, task_start_time DESC);
CREATE INDEX IF NOT EXISTS idx_task_fault_cat     ON tb_task_master(fault_category) WHERE fault_category IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_task_linked_alarm  ON tb_task_master(linked_alarm_start, linked_alarm_tagsn) WHERE linked_alarm_start IS NOT NULL;

COMMENT ON COLUMN tb_task_master.equipment_id     IS '설비 직접 연결 (tb_equipment_info FK)';
COMMENT ON COLUMN tb_task_master.equipmenttype    IS '설비 유형 (PLC/가압펌프/유량계/밸브/모뎀)';
COMMENT ON COLUMN tb_task_master.fault_category   IS '장애 분류 (고장/이상/교체/점검)';
COMMENT ON COLUMN tb_task_master.severity         IS '심각도 (경고/주의/정보)';
COMMENT ON COLUMN tb_task_master.linked_alarm_start IS '연결 알람 alarm_start_time (tb_equipment_alarm_report PK 일부)';
COMMENT ON COLUMN tb_task_master.linked_alarm_tagsn IS '연결 알람 tagsn (tb_equipment_alarm_report PK 일부)';
COMMENT ON COLUMN tb_task_master.photo_urls       IS '현장 사진 URL 배열 JSON';
COMMENT ON COLUMN tb_task_master.status           IS '진행상태 (진행중/완료/보류)';


-- ========================================================================
-- 2. 채팅 멀티턴 세션 임시 저장 테이블
-- ========================================================================
CREATE TABLE IF NOT EXISTS tb_chat_pending_action (
  session_id   VARCHAR(64) PRIMARY KEY,
  user_id      VARCHAR(50) NOT NULL,
  intent       VARCHAR(50) NOT NULL,        -- FAULT_RECORD_DRAFT 등
  draft        JSONB NOT NULL,              -- 파싱된 필드 (sitename/equipmenttype/fault_category/...)
  created_at   TIMESTAMPTZ DEFAULT now(),
  expires_at   TIMESTAMPTZ DEFAULT now() + interval '5 minutes'
);
CREATE INDEX IF NOT EXISTS idx_pending_user_intent ON tb_chat_pending_action(user_id, intent);
CREATE INDEX IF NOT EXISTS idx_pending_expires     ON tb_chat_pending_action(expires_at);

COMMENT ON TABLE tb_chat_pending_action IS
  '채팅 멀티턴 대화의 초안 상태 (예: FAULT_RECORD_DRAFT → CONFIRM 대기). TTL 5분 자동 만료';


-- ========================================================================
-- 3. 장애 이력 통계 뷰
-- ========================================================================

-- 3-a. 시설·설비·분류별 장애 집계
CREATE OR REPLACE VIEW v_equipment_fault_stats AS
SELECT
  sitename,
  facilitytype,
  equipmenttype,
  fault_category,
  COUNT(*) AS total_cnt,
  COUNT(*) FILTER (WHERE status = '진행중') AS ongoing_cnt,
  COUNT(*) FILTER (WHERE status = '완료')   AS resolved_cnt,
  ROUND(
    (AVG(EXTRACT(EPOCH FROM (resolved_at - task_start_time)) / 3600)
      FILTER (WHERE resolved_at IS NOT NULL))::numeric, 2) AS avg_resolve_hours,
  MIN(task_start_time) AS first_occurred,
  MAX(task_start_time) AS last_occurred
FROM tb_task_master
WHERE task_category = '고장보고'
GROUP BY sitename, facilitytype, equipmenttype, fault_category;

COMMENT ON VIEW v_equipment_fault_stats IS '시설·설비유형·장애분류별 고장 통계 (년간·월간 추이)';


-- 3-b. 월별 장애 추이
CREATE OR REPLACE VIEW v_equipment_fault_monthly AS
SELECT
  date_trunc('month', task_start_time)::date AS month,
  fault_category,
  equipmenttype,
  COUNT(*) AS cnt
FROM tb_task_master
WHERE task_category = '고장보고'
GROUP BY 1, 2, 3
ORDER BY 1 DESC;

COMMENT ON VIEW v_equipment_fault_monthly IS '월별 장애 건수 (분류·설비유형별 추이 차트용)';


-- 3-c. 설비별 MTBF (Mean Time Between Failures, 단위: 일)
CREATE OR REPLACE VIEW v_equipment_mtbf AS
WITH gaps AS (
  SELECT
    equipment_id,
    sitename,
    facilitytype,
    equipmenttype,
    task_start_time,
    EXTRACT(EPOCH FROM (task_start_time - LAG(task_start_time)
      OVER (PARTITION BY equipment_id ORDER BY task_start_time))) / 86400 AS gap_days
  FROM tb_task_master
  WHERE task_category = '고장보고' AND equipment_id IS NOT NULL
)
SELECT
  equipment_id,
  sitename,
  facilitytype,
  equipmenttype,
  COUNT(*) AS fault_cnt,
  ROUND(AVG(gap_days)::numeric, 2) AS mtbf_days,
  ROUND(MIN(gap_days)::numeric, 2) AS min_gap_days,
  MAX(task_start_time) AS last_failed_at
FROM gaps
GROUP BY equipment_id, sitename, facilitytype, equipmenttype;

COMMENT ON VIEW v_equipment_mtbf IS '설비별 MTBF (고장 간격 평균 일수) — 노후 설비 집중관리 대상 식별용';


-- 3-d. 시설별 장애 Top N용 집계
CREATE OR REPLACE VIEW v_site_fault_ranking AS
SELECT
  sitename,
  facilitytype,
  COUNT(*) AS total_faults,
  COUNT(*) FILTER (WHERE task_start_time >= now() - interval '30 days')  AS last_30d_cnt,
  COUNT(*) FILTER (WHERE task_start_time >= now() - interval '365 days') AS last_1y_cnt,
  COUNT(DISTINCT equipment_id) AS affected_equipments
FROM tb_task_master
WHERE task_category = '고장보고'
GROUP BY sitename, facilitytype;

COMMENT ON VIEW v_site_fault_ranking IS '시설별 장애 Top N + 최근 30일/1년 건수';


COMMIT;

-- 롤백 예시 (필요 시):
-- BEGIN;
-- ALTER TABLE tb_task_master DROP COLUMN IF EXISTS status, DROP COLUMN IF EXISTS resolution_note,
--   DROP COLUMN IF EXISTS resolved_at, DROP COLUMN IF EXISTS resolved_by, DROP COLUMN IF EXISTS recorded_by,
--   DROP COLUMN IF EXISTS photo_urls, DROP COLUMN IF EXISTS linked_alarm_id, DROP COLUMN IF EXISTS severity,
--   DROP COLUMN IF EXISTS fault_category, DROP COLUMN IF EXISTS equipmenttype, DROP COLUMN IF EXISTS equipment_id;
-- DROP VIEW IF EXISTS v_equipment_fault_stats, v_equipment_fault_monthly, v_equipment_mtbf, v_site_fault_ranking;
-- DROP TABLE IF EXISTS tb_chat_pending_action;
-- COMMIT;
