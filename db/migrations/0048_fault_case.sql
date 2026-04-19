-- ============================================================================
-- migration 0048: tb_fault_case (P3 — 고장 진단 케이스 DB)
-- ============================================================================
-- 목적: 장비별 고장 진단 케이스(증상·원인·조치)를 DB에 구조화하여 채팅 진단
--        RAG 시 매뉴얼과 별도로 우선 검색. 관리자 UI 로 CRUD + 엑셀 일괄 IMPORT/EXPORT.
--
-- 사양: docs/chat-photo-upload-scenario-spec.md §P3
-- 근거: 매뉴얼은 통째 PDF 임베딩이라 "고장 섹션"만 집중 검색이 어려움 → 운영
--        노하우를 구조화된 테이블에 축적해 RAG 품질 향상.
-- ============================================================================

CREATE TABLE IF NOT EXISTS tb_fault_case (
  case_id         BIGSERIAL PRIMARY KEY,
  equipment_type  VARCHAR(20)  NOT NULL,   -- PLC/유량계/모뎀/RTU/인버터/펌프/밸브/수위계/압력계/UPS/기타
  brand           VARCHAR(50),             -- 제조사 (NULL = 범용)
  model           VARCHAR(100),            -- 모델명 (NULL = 범용)
  symptom         TEXT         NOT NULL,   -- 증상 (검색 대상 주필드)
  cause           TEXT,                    -- 원인
  action          TEXT,                    -- 조치 방법
  severity        VARCHAR(10),             -- 경고/주의/정보 (NULL 허용)
  reference_url   TEXT,                    -- 매뉴얼/외부 문서 링크(옵션)
  notes           TEXT,                    -- 자유 메모
  is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
  created_by      VARCHAR(45),
  created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

  -- 임베딩 메타 — 실제 벡터는 NPZ 파일로 저장 (vision_agent RAG 와 동일)
  embedding_key   VARCHAR(100),            -- NPZ 파일명 (fault_case_<id>.npz)
  embedding_updated_at TIMESTAMPTZ,

  CONSTRAINT chk_fault_case_equipment_type CHECK (
    equipment_type IN ('PLC','유량계','모뎀','RTU','인버터','펌프','밸브',
                       '수위계','압력계','UPS','기타')
  ),
  CONSTRAINT chk_fault_case_severity CHECK (
    severity IS NULL OR severity IN ('경고','주의','정보')
  )
);

-- 중복 방지: 같은 (equipment_type, brand, model) 안에서 symptom 은 유일
-- COALESCE 로 NULL 처리 (PostgreSQL UNIQUE 는 NULL 을 다르게 취급하므로)
CREATE UNIQUE INDEX IF NOT EXISTS uq_fault_case_symptom
  ON tb_fault_case (
    equipment_type,
    COALESCE(brand, ''),
    COALESCE(model, ''),
    symptom
  )
  WHERE is_active = TRUE;

-- 일반 조회용 — 장비 타입 + 브랜드 필터
CREATE INDEX IF NOT EXISTS idx_fault_case_equipment
  ON tb_fault_case (equipment_type, brand, model)
  WHERE is_active = TRUE;

-- updated_at 자동 갱신 트리거
CREATE OR REPLACE FUNCTION tg_fault_case_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at := NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tr_fault_case_updated_at ON tb_fault_case;
CREATE TRIGGER tr_fault_case_updated_at
  BEFORE UPDATE ON tb_fault_case
  FOR EACH ROW EXECUTE FUNCTION tg_fault_case_updated_at();

-- 주석
COMMENT ON TABLE  tb_fault_case IS '[P3] 장비별 고장 진단 케이스 (증상·원인·조치) — 채팅 RAG 우선 검색 대상';
COMMENT ON COLUMN tb_fault_case.equipment_type  IS '장비 종류 화이트리스트';
COMMENT ON COLUMN tb_fault_case.symptom         IS '증상 텍스트 (임베딩 주요 대상)';
COMMENT ON COLUMN tb_fault_case.embedding_key   IS 'data/fault_case_embeddings/<key>.npz 파일명';
