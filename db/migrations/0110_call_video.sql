-- 0110: 메신저 P4 — 1:1 영상 통화 (docs/realtime-comm-spec.md §5.6)
--   통화 유형 컬럼 추가. 기존 행은 음성(audio).
-- 롤백:
--   ALTER TABLE tb_call_session DROP COLUMN IF EXISTS call_type;

ALTER TABLE tb_call_session
  ADD COLUMN IF NOT EXISTS call_type varchar(10) NOT NULL DEFAULT 'audio';
