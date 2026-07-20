-- 0111: 통화 세션 하트비트 — 유령 accepted 세션 자동 정리
--   (docs/realtime-comm-spec.md §5.4. 브라우저 강제 종료 등으로 end 미호출 시
--    "이미 진행 중인 통화" 영구 차단되던 문제)
-- 롤백:
--   ALTER TABLE tb_call_session DROP COLUMN IF EXISTS last_poll_at;

ALTER TABLE tb_call_session
  ADD COLUMN IF NOT EXISTS last_poll_at timestamptz NOT NULL DEFAULT now();
