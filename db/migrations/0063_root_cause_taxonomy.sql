-- Migration 0063: 근본원인 분류 체계 + 보고서 항목 root_causes 자동 라벨
-- 사양: docs/report-spec.md §3.4.2
--
-- 설계: 사용자는 자유 텍스트로 보고서 작성 (cause, occurred_text, resolved_text...).
--       LLM 이 사후에 텍스트를 분류해 root_causes JSONB 배열에 코드 저장.
--       사용자 입력 부담 없이 통계용 라벨 누적.
--
-- 롤백:
--   ALTER TABLE tb_report_item DROP COLUMN IF EXISTS root_causes,
--     DROP COLUMN IF EXISTS root_cause_classified_at,
--     DROP COLUMN IF EXISTS root_cause_model;
--   DROP TABLE tb_root_cause_taxonomy;

BEGIN;

-- 1) 분류 체계 마스터
CREATE TABLE IF NOT EXISTS tb_root_cause_taxonomy (
  code          VARCHAR(40) PRIMARY KEY,
  group_code    VARCHAR(20) NOT NULL,        -- COMM/POWER/PLC/SENSOR/MECHANICAL/HUMAN/UNKNOWN
  label         VARCHAR(100) NOT NULL,
  hint          TEXT,                         -- LLM 분류기에 줄 키워드 힌트
  weight        NUMERIC(4,2) DEFAULT 1.00,   -- 노후도 가중치 (P3 활용)
  sort_order    INT DEFAULT 100,
  use_yn        CHAR(1) NOT NULL DEFAULT 'Y',
  created_at    TIMESTAMPTZ DEFAULT now(),
  CONSTRAINT chk_root_cause_use_yn CHECK (use_yn IN ('Y','N'))
);

CREATE INDEX IF NOT EXISTS idx_root_cause_group_order
  ON tb_root_cause_taxonomy(group_code, sort_order)
  WHERE use_yn = 'Y';

COMMENT ON TABLE tb_root_cause_taxonomy IS
  '보고서 항목 자유 텍스트로부터 LLM 이 사후 분류할 근본원인 코드 체계';
COMMENT ON COLUMN tb_root_cause_taxonomy.weight IS
  '노후도 점수 가중치 (1.0 기준). 카드 고장처럼 치명적이면 ↑';

-- 2) 시드 — 17 코드 (사용자 예시 + 일반 운영 케이스)
INSERT INTO tb_root_cause_taxonomy (code, group_code, label, hint, weight, sort_order) VALUES
  -- 통신 COMM
  ('COMM_MODEM',    'COMM',  '모뎀 고장',          '모뎀, LTE 모뎀, DSU, 통신 모듈 고장·먹통',                   2.0, 11),
  ('COMM_CABLE',    'COMM',  '케이블 빠짐/단선',   '케이블 빠짐, 단선, 접촉 불량, 커넥터, RJ45',                  1.0, 12),
  ('COMM_SIGNAL',   'COMM',  'LTE 신호 약함',      'LTE 신호 약함, 안테나, RSRP, RSSI 낮음',                      1.0, 13),
  ('COMM_NETWORK',  'COMM',  '네트워크 일반',      '통신 두절, 핑 안 됨, 스위치, 라우터, 네트워크 단절',          1.5, 14),
  -- 전원 POWER
  ('POWER_UPS',     'POWER', 'UPS 배터리 노후',    'UPS 배터리, 방전, 배터리 교체, 백업 시간 부족',               2.0, 21),
  ('POWER_TRIP',    'POWER', '차단기 트립',        '차단기 트립, 누전, 과부하, 분전반 OFF',                       1.5, 22),
  ('POWER_NOISE',   'POWER', '전원 노이즈/순간 정전','전원 튐, 순간 정전, 노이즈, 서지, 전압 변동',                1.5, 23),
  -- 제어반 PLC
  ('PLC_CARD',      'PLC',   'PLC 카드 고장',      'PLC 카드 고장, IO 모듈, AI 카드, AO 카드, 디지털 입출력',     3.0, 31),
  ('PLC_FIRMWARE',  'PLC',   'PLC 펌웨어/메모리',  '펌웨어 오류, 메모리 손상, PLC 리부팅, 프로그램 다운',         2.5, 32),
  -- 계측 SENSOR
  ('SENSOR_STUCK',  'SENSOR','계측값 고정',        '계측값 고정, 스턱, 같은 값 유지, 응답 없음, 변하지 않음',     2.5, 41),
  ('SENSOR_NOISE',  'SENSOR','계측 노이즈',        '노이즈, 튀는 값, 스파이크, 흔들림',                           1.0, 42),
  ('SENSOR_DRIFT',  'SENSOR','오프셋·드리프트',    '오프셋, 드리프트, 캘리브레이션 어긋남, 영점 이동',            1.5, 43),
  ('SENSOR_FAULT',  'SENSOR','센서 고장',          '센서 고장, 부식, 막힘, 측정 불가',                            2.0, 44),
  -- 기계 MECHANICAL
  ('MECH_PUMP',     'MECHANICAL', '펌프 기계적 고장', '펌프 모터, 임펠러, 베어링, 진동, 누수',                      2.0, 51),
  ('MECH_VALVE',    'MECHANICAL', '밸브 동작 불량', '밸브 막힘, 개폐 불량, 액추에이터, 솔레노이드',                1.5, 52),
  -- 인적/환경 HUMAN
  ('HUMAN_INSTALL', 'HUMAN', '시공 결함',          '시공 결함, 외주 작업 실수, 잘못된 결선, 접지 불량',           1.0, 61),
  ('HUMAN_OPERATE', 'HUMAN', '조작 실수',          '사용자 조작 실수, 설정 변경, 수동 조작, 버튼 눌림',           1.0, 62),
  -- 미상
  ('UNKNOWN',       'UNKNOWN','원인 미상',         '원인 불명, 미상, 재현 안 됨, 일시적, 자연 복구',              0.5, 91)
ON CONFLICT (code) DO NOTHING;


-- 3) tb_report_item 컬럼 추가
ALTER TABLE tb_report_item
  ADD COLUMN IF NOT EXISTS root_causes              JSONB,
  ADD COLUMN IF NOT EXISTS root_cause_classified_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS root_cause_model         VARCHAR(50);

COMMENT ON COLUMN tb_report_item.root_causes IS
  '[Migration 0063] LLM 사후 분류 결과 — taxonomy.code 배열. 사용자 입력 X';
COMMENT ON COLUMN tb_report_item.root_cause_classified_at IS
  '마지막 자동 분류 시각. NULL = 미분류';

CREATE INDEX IF NOT EXISTS idx_report_item_root_causes
  ON tb_report_item USING GIN (root_causes)
  WHERE root_causes IS NOT NULL;

COMMIT;
