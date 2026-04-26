-- Migration 0059: 보고서 incident 양식 흡수 (장애조치결과보고서)
-- 사양: docs/report-spec.md §3.1 항목 신규 필드 + 결재란
-- 참고 양식: docs/incident_report.html
--
-- 추가 항목:
--   tb_report.approval_chain JSONB
--     {담당:{name, signed_at}, 검토:{...}, 승인:{...}}
--   tb_report_item:
--     symptom            TEXT  — 장애 현상 (occurred_text 와 분리 가능, 점진적 채움)
--     cause              TEXT  — 장애 원인 (신규)
--     key_issues         TEXT  — 주요 사항 (신규)
--     system_categories  JSONB — 장애 시스템 체크박스 (배열) ["현장제어반","네트워크" ...]
--     equipment_categories JSONB — 장애 장비 체크박스 (배열) ["유량계","PLC/RTU"...]
--
-- 롤백:
--   ALTER TABLE tb_report      DROP COLUMN IF EXISTS approval_chain;
--   ALTER TABLE tb_report_item DROP COLUMN IF EXISTS symptom,
--     DROP COLUMN IF EXISTS cause, DROP COLUMN IF EXISTS key_issues,
--     DROP COLUMN IF EXISTS system_categories,
--     DROP COLUMN IF EXISTS equipment_categories;

BEGIN;

ALTER TABLE tb_report
  ADD COLUMN IF NOT EXISTS approval_chain JSONB;

COMMENT ON COLUMN tb_report.approval_chain IS
  '결재란 정보. {담당:{name, signed_at}, 검토:{...}, 승인:{...}} 구조';

ALTER TABLE tb_report_item
  ADD COLUMN IF NOT EXISTS symptom              TEXT,
  ADD COLUMN IF NOT EXISTS cause                TEXT,
  ADD COLUMN IF NOT EXISTS key_issues           TEXT,
  ADD COLUMN IF NOT EXISTS system_categories    JSONB,
  ADD COLUMN IF NOT EXISTS equipment_categories JSONB;

COMMENT ON COLUMN tb_report_item.symptom              IS '장애 현상 (incident_report 양식)';
COMMENT ON COLUMN tb_report_item.cause                IS '장애 원인 (incident_report 양식)';
COMMENT ON COLUMN tb_report_item.key_issues           IS '주요 사항 (incident_report 양식 마지막 멀티라인)';
COMMENT ON COLUMN tb_report_item.system_categories    IS
  '장애 시스템 체크박스 배열. 값 예: 현장제어반, 네트워크, SCADA/HMI, 서버/DB, 전원/UPS, 계측/센서, 응용 SW, 기타';
COMMENT ON COLUMN tb_report_item.equipment_categories IS
  '장애 장비 체크박스 배열. 값 예: 유량계, 수위계, 압력계, 수질계측기, 펌프/밸브, PLC/RTU, DSU/모뎀, Serial Converter, 스위치/라우터, 서버/PC, 기타';

COMMIT;
