-- Migration 0062: 일 점검 보고서용 카테고리 type 추가
-- 사양: docs/report-spec.md §3.5 (보고서 유형별 카테고리 분기)
--
-- 기존 (Migration 0060): system / equipment — 장애 조치 보고서 전용
-- 추가: inspection_system / inspection_equipment — 일 점검 보고서 전용
--
-- 롤백:
--   DELETE FROM tb_report_category WHERE category_type IN ('inspection_system','inspection_equipment');
--   ALTER TABLE tb_report_category DROP CONSTRAINT chk_report_category_type;
--   ALTER TABLE tb_report_category ADD CONSTRAINT chk_report_category_type
--     CHECK (category_type IN ('system','equipment'));

BEGIN;

-- 1) CHECK 제약 갱신 — 4개 type 허용
ALTER TABLE tb_report_category DROP CONSTRAINT IF EXISTS chk_report_category_type;
ALTER TABLE tb_report_category
  ADD CONSTRAINT chk_report_category_type
  CHECK (category_type IN ('system','equipment','inspection_system','inspection_equipment'));

-- 2) 점검 시스템 (8) seed
INSERT INTO tb_report_category (category_type, code, label, sort_order, use_yn) VALUES
  ('inspection_system', '시설 외관',     '시설 외관',     10, 'Y'),
  ('inspection_system', '계측/센서',     '계측/센서',     20, 'Y'),
  ('inspection_system', '전원/UPS',      '전원/UPS',      30, 'Y'),
  ('inspection_system', '통신/네트워크', '통신/네트워크', 40, 'Y'),
  ('inspection_system', '제어반',        '제어반',        50, 'Y'),
  ('inspection_system', '서버/DB',       '서버/DB',       60, 'Y'),
  ('inspection_system', '응용 SW',       '응용 SW',       70, 'Y'),
  ('inspection_system', '기타',          '기타',          99, 'Y')
ON CONFLICT (category_type, code) DO NOTHING;

-- 3) 점검 장비 (10) seed
INSERT INTO tb_report_category (category_type, code, label, sort_order, use_yn) VALUES
  ('inspection_equipment', '펌프/밸브',    '펌프/밸브',    10, 'Y'),
  ('inspection_equipment', '유량계',       '유량계',       20, 'Y'),
  ('inspection_equipment', '수위계',       '수위계',       30, 'Y'),
  ('inspection_equipment', '압력계',       '압력계',       40, 'Y'),
  ('inspection_equipment', '수질계측기',   '수질계측기',   50, 'Y'),
  ('inspection_equipment', 'PLC/RTU',     'PLC/RTU',     60, 'Y'),
  ('inspection_equipment', '모뎀',         '모뎀',         70, 'Y'),
  ('inspection_equipment', '스위치',       '스위치',       80, 'Y'),
  ('inspection_equipment', '서버/PC',      '서버/PC',      90, 'Y'),
  ('inspection_equipment', '기타',         '기타',         99, 'Y')
ON CONFLICT (category_type, code) DO NOTHING;

COMMIT;
