-- 0107: 메신저 P2 — 사진·영상·음성메시지 첨부 (docs/realtime-comm-spec.md §5.2)
-- 롤백:
--   ALTER TABLE tb_user_chat_message
--     DROP COLUMN IF EXISTS attach_url,
--     DROP COLUMN IF EXISTS attach_type,
--     DROP COLUMN IF EXISTS attach_name;

ALTER TABLE tb_user_chat_message
  ADD COLUMN IF NOT EXISTS attach_url  varchar(500),
  ADD COLUMN IF NOT EXISTS attach_type varchar(10),   -- image | video | audio
  ADD COLUMN IF NOT EXISTS attach_name varchar(200);
