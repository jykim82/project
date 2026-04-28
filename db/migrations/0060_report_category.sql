-- Migration 0060: 보고서 카테고리(장애 시스템 / 장애 장비) 동적 관리
-- 사양: docs/report-spec.md §3.5 (incident 양식 옵션을 DB 에서 CRUD)
--
-- 기존: types/report.ts 의 SYSTEM_CATEGORY_OPTIONS (8종) + EQUIPMENT_CATEGORY_OPTIONS (11종)
--       하드코딩 상수
-- 변경: tb_report_category 신규 — 관리 메뉴에서 추가·수정·삭제 가능
--
-- 롤백:
--   DROP TABLE tb_report_category;

BEGIN;

CREATE TABLE IF NOT EXISTS tb_report_category (
  category_id    BIGSERIAL PRIMARY KEY,
  category_type  VARCHAR(20) NOT NULL,        -- 'system' (장애 시스템) / 'equipment' (장애 장비)
  code           VARCHAR(50) NOT NULL,        -- DB 상의 식별자 (라벨과 동일하게 시작, 다국어 시 분리 가능)
  label          VARCHAR(100) NOT NULL,       -- 화면 표시 라벨
  sort_order     INT NOT NULL DEFAULT 100,
  use_yn         CHAR(1) NOT NULL DEFAULT 'Y',
  created_at     TIMESTAMPTZ DEFAULT now(),
  updated_at     TIMESTAMPTZ DEFAULT now(),
  CONSTRAINT chk_report_category_type CHECK (category_type IN ('system','equipment')),
  CONSTRAINT chk_report_category_use_yn CHECK (use_yn IN ('Y','N')),
  CONSTRAINT uq_report_category UNIQUE (category_type, code)
);

CREATE INDEX IF NOT EXISTS idx_report_category_type_order
  ON tb_report_category(category_type, sort_order, category_id)
  WHERE use_yn = 'Y';

COMMENT ON TABLE  tb_report_category IS
  '보고서 incident 양식의 장애 시스템/장비 체크박스 옵션. 관리 메뉴에서 CRUD';
COMMENT ON COLUMN tb_report_category.category_type IS 'system = 장애 시스템 / equipment = 장애 장비';
COMMENT ON COLUMN tb_report_category.code  IS '식별자 — tb_report_item.system_categories/equipment_categories JSONB 배열에 저장되는 값';
COMMENT ON COLUMN tb_report_category.label IS '화면 표시 라벨 (현재는 code 와 동일 운용)';

-- 기본값 seed — 기존 하드코딩 상수와 동일
-- 장애 시스템 (8종)
INSERT INTO tb_report_category (category_type, code, label, sort_order, use_yn) VALUES
  ('system', '현장제어반',    '현장제어반',    10, 'Y'),
  ('system', '네트워크',      '네트워크',      20, 'Y'),
  ('system', 'SCADA/HMI',    'SCADA/HMI',    30, 'Y'),
  ('system', '서버/DB',       '서버/DB',       40, 'Y'),
  ('system', '전원/UPS',      '전원/UPS',      50, 'Y'),
  ('system', '계측/센서',     '계측/센서',     60, 'Y'),
  ('system', '응용 SW',       '응용 SW',       70, 'Y'),
  ('system', '기타',          '기타',          99, 'Y')
ON CONFLICT (category_type, code) DO NOTHING;

-- 장애 장비 (11종)
INSERT INTO tb_report_category (category_type, code, label, sort_order, use_yn) VALUES
  ('equipment', '유량계',           '유량계',           10, 'Y'),
  ('equipment', '수위계',           '수위계',           20, 'Y'),
  ('equipment', '압력계',           '압력계',           30, 'Y'),
  ('equipment', '수질계측기',       '수질계측기',       40, 'Y'),
  ('equipment', '펌프/밸브',        '펌프/밸브',        50, 'Y'),
  ('equipment', 'PLC/RTU',         'PLC/RTU',         60, 'Y'),
  ('equipment', 'DSU/모뎀',         'DSU/모뎀',         70, 'Y'),
  ('equipment', 'Serial Converter', 'Serial Converter', 80, 'Y'),
  ('equipment', '스위치/라우터',     '스위치/라우터',     90, 'Y'),
  ('equipment', '서버/PC',           '서버/PC',          95, 'Y'),
  ('equipment', '기타',              '기타',             99, 'Y')
ON CONFLICT (category_type, code) DO NOTHING;

COMMIT;
