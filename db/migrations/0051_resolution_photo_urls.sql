-- ============================================================================
-- migration 0051: tb_task_master.resolution_photo_urls JSONB (P8)
-- ============================================================================
-- 목적: 조치 완료 기록 시 현장 조치 사진을 첨부. 고장 등록 사진(photo_urls)
--       과 별도로 "조치 후 상태" 사진을 보관.
--       예: 교체 완료 후 설치 장면, 정상 LED 상태, 복구된 계기판 등
--
-- 저장 구조: photo_urls 와 동일한 URL 배열 (/api/files/chat_attachments/...)
-- ============================================================================

ALTER TABLE tb_task_master
  ADD COLUMN IF NOT EXISTS resolution_photo_urls JSONB;

COMMENT ON COLUMN tb_task_master.resolution_photo_urls IS
  '[P8] 조치 완료 사진 URL 배열. photo_urls(고장 보고 사진) 과 별도 저장.';
