-- ============================================================================
-- migration 0050: tb_task_master.replacement_info JSONB (P7)
-- ============================================================================
-- 목적: fault_category='교체' 기록 시 제조사/모델/시리얼/교체일자 등 메타데이터
--       를 구조화하여 저장. 스키마 변경 영향 최소화 위해 JSONB 단일 컬럼 사용.
--
-- 권장 키셋:
--   {
--     "manufacturer": "LS",           -- 신규 설치 제조사
--     "model":        "XGB-XBCH",     -- 신규 모델
--     "serial":       "12345",        -- 신규 시리얼 (옵션)
--     "old_manufacturer": "LS",       -- 기존 설비 제조사 (옵션)
--     "old_model":        "XGB-XBCL", -- 기존 모델 (옵션)
--     "old_serial":       "00999",    -- 기존 시리얼 (옵션)
--     "replaced_at":  "2026-04-19T14:30"  -- 실제 교체 완료 시각 (ISO)
--   }
-- ============================================================================

ALTER TABLE tb_task_master
  ADD COLUMN IF NOT EXISTS replacement_info JSONB;

COMMENT ON COLUMN tb_task_master.replacement_info IS
  '[P7] 교체 메타데이터 — 제조사/모델/시리얼/교체일자 등. fault_category=''교체'' 에서만 사용 권장';

-- 교체 이력 조회용 인덱스 (관리자 UI 리포트에서 최신순 조회)
CREATE INDEX IF NOT EXISTS idx_task_master_replacement
  ON tb_task_master ((replacement_info->>'manufacturer'), (replacement_info->>'model'))
  WHERE fault_category = '교체' AND replacement_info IS NOT NULL;
