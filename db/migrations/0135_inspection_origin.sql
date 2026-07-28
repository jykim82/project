-- 0135_inspection_origin.sql
-- 점검 기원 구분 + 유발 고장 링크 (docs/inspection-origin-spec.md)
--
-- 점검에는 기원이 두 가지다 (2026-07-28 사용자 정의):
--   일상 — 정기 순회 점검. 그 과정에서 고장을 발견·조치하기도 한다
--   요청 — 고장 이벤트가 먼저 있고, 그 요청으로 수행하는 점검
--
-- inspection_type 컬럼은 이미 있으나 값 체계가 미정('일상' 1건뿐)이었다.
-- '일상'/'요청' 으로 확정하고, 요청 점검이 어느 고장 건에서 비롯됐는지를
-- linked_task_id 로 남긴다 (지금은 알람 링크만 있고 고장 건 링크가 없음).
--
-- 고장 카운트는 변함없이 기록(tb_task_master) 기반 — 어느 보고서에 실리든
-- 집계는 1건이다. 이 마이그레이션은 문서 편입·추적을 위한 것.
--
-- 롤백: 파일 하단 ROLLBACK 블록

BEGIN;

ALTER TABLE tb_task_master
    ADD COLUMN IF NOT EXISTS linked_task_id bigint;

COMMENT ON COLUMN tb_task_master.inspection_type IS
  '점검 기원: 일상(정기 순회) / 요청(고장 이벤트로 수행). NULL=체계 도입 전 기록';
COMMENT ON COLUMN tb_task_master.linked_task_id IS
  '요청 점검을 유발한 고장 건 task_id (inspection-origin-spec). 소급 추정 금지 — NULL 유지';

-- 요청 점검 → 유발 고장 역추적용
CREATE INDEX IF NOT EXISTS idx_task_master_linked
    ON tb_task_master (linked_task_id) WHERE linked_task_id IS NOT NULL;

COMMIT;

-- =============================================================================
-- ROLLBACK
-- =============================================================================
-- DROP INDEX IF EXISTS idx_task_master_linked;
-- ALTER TABLE tb_task_master DROP COLUMN IF EXISTS linked_task_id;
