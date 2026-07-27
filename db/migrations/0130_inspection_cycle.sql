-- 0130_inspection_cycle.sql
-- 설비 점검 주기 마스터 (docs/inspection-cycle-spec.md)
--
-- 점검 주기 마스터가 지금까지 없었다 — tb_equipment_lifespan 은 교체
-- 주기(년)지 점검 주기가 아니다. 유형 단위로 신설한다 (현장 규정이 유형
-- 단위로 정해지므로 설비 개별 주기는 과설계).
--
-- 시드는 실제 등록 설비 유형 기준 보수적 기본값 — 출발점이지 규정이 아니다.
-- 현장 규정 확정 시 UPDATE 로 대체한다.
--
-- 롤백: 파일 하단 ROLLBACK 블록

BEGIN;

CREATE TABLE IF NOT EXISTS tb_inspection_cycle (
    region        varchar(10) NOT NULL,
    equipmenttype varchar(50) NOT NULL,  -- tb_equipment_info.equipmenttype 매칭
    cycle_days    integer     NOT NULL CHECK (cycle_days > 0),
    note          text,
    created_at    timestamptz DEFAULT now(),
    updated_at    timestamptz DEFAULT now(),
    PRIMARY KEY (region, equipmenttype)
);

COMMENT ON TABLE tb_inspection_cycle IS
  '설비유형별 점검 주기 (일). 도래 계산은 조회 시점 — cron 없음 (inspection-cycle-spec §3)';

-- 시드 — 실제 등록 유형만 (관념적 유형은 매칭 0 이라 시드 무의미).
-- 서버·PC 류는 IT 자산 점검 체계가 달라 제외 — 필요 시 현장이 추가.
INSERT INTO tb_inspection_cycle (region, equipmenttype, cycle_days, note) VALUES
  ('R01', '가압펌프',   30,  '회전기기 월 점검 관행 — 현장 규정으로 대체할 것'),
  ('R01', 'PLC',        90,  '분기'),
  ('R01', '유량계',     180, '반기 (계측 교정 주기와 별개)'),
  ('R01', 'LTE 모뎀',   180, '반기'),
  ('R01', 'L2 스위치',  180, '반기'),
  ('R01', 'L3 스위치',  180, '반기'),
  ('R01', 'UTM',        180, '반기')
ON CONFLICT (region, equipmenttype) DO NOTHING;

COMMIT;

-- 확인
-- SELECT * FROM tb_inspection_cycle ORDER BY equipmenttype;

-- =============================================================================
-- ROLLBACK
-- =============================================================================
-- DROP TABLE IF EXISTS tb_inspection_cycle;
