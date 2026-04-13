-- ============================================================
-- 03: Chat Tables - AI 채팅 피드백 (수동 검토 게이트)
--
-- ⚠ 정리 이력 (2026-04-13):
-- 다음 6개 테이블은 미사용으로 확인되어 [E-020]에서 DROP 됨:
--   tb_ai_chat_ask_group, tb_ai_chat_ask, tb_ai_chat_bot,
--   tb_ai_chat_ask_image, tb_ai_chat_bot_image, tb_ai_chat_faq
-- 채팅 히스토리는 클라이언트 localStorage(slm-dashboard chat-store.ts)에만
-- 저장되며, FAQ는 endpoints/chat_faq_examples.py에서 동적 생성됨.
-- 백업: db/backups/unused_tables_backup_2026-04-13.sql
-- ============================================================

SET client_encoding = 'UTF8';

-- ------------------------------------------------------------
-- 오분류 피드백 (수동 검토 게이트) — 유일하게 활성
-- ------------------------------------------------------------
-- 현재 채팅 히스토리는 클라이언트 localStorage에만 저장되므로
-- self-contained 구조 (질문/답변/인텐트 복사본 포함). 추후 채팅
-- 영속화 구현 시 ask_seq 컬럼을 추가해 FK로 확장 가능.
CREATE TABLE IF NOT EXISTS tb_ai_chat_feedback (
    feedback_id    bigserial PRIMARY KEY,
    region         character varying(10) NOT NULL,
    user_id        character varying(45) NOT NULL,
    user_question  text NOT NULL,
    bot_answer     text,
    intent_name    character varying(80),
    feedback_type  character varying(20) NOT NULL DEFAULT 'wrong_answer',
    comment        text,
    reviewed       boolean NOT NULL DEFAULT false,
    reviewed_at    timestamp with time zone,
    reviewed_by    character varying(45),
    created_at     timestamp with time zone NOT NULL DEFAULT now()
);
COMMENT ON TABLE tb_ai_chat_feedback IS
    '채팅 봇 오분류 피드백. 담당자 수동 검토 후 example3.json 반영';
COMMENT ON COLUMN tb_ai_chat_feedback.feedback_type IS
    'wrong_answer(기본) | misclassified | incomplete | other';
COMMENT ON COLUMN tb_ai_chat_feedback.reviewed IS
    'true면 example3.json 또는 인텐트 정의에 반영 완료 (수동 게이트)';

CREATE INDEX IF NOT EXISTS idx_chat_feedback_pending
    ON tb_ai_chat_feedback(region, created_at DESC)
    WHERE reviewed = false;

ALTER TABLE tb_ai_chat_feedback
    ADD CONSTRAINT fk_chat_feedback_user
    FOREIGN KEY (region, user_id)
    REFERENCES tb_user(region, user_id);
