-- 0047_chat_feedback_correct.sql
-- 채팅 피드백에 "정답 인텐트 지정" + "학습용 유사 질문" 컬럼 추가
-- C1 인간 개입 루프 + C2 재학습 주입용.

BEGIN;

ALTER TABLE tb_ai_chat_feedback
    ADD COLUMN IF NOT EXISTS correct_intent      VARCHAR(80),
    ADD COLUMN IF NOT EXISTS suggested_question  TEXT,
    ADD COLUMN IF NOT EXISTS applied_to_index    BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS applied_at          TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_chat_feedback_reindex
    ON tb_ai_chat_feedback (applied_to_index, reviewed)
    WHERE correct_intent IS NOT NULL;

COMMENT ON COLUMN tb_ai_chat_feedback.correct_intent     IS '운영자가 지정한 정답 intent (classifier 재학습 샘플)';
COMMENT ON COLUMN tb_ai_chat_feedback.suggested_question IS '재학습용 유사 질문 (비어 있으면 user_question 사용)';
COMMENT ON COLUMN tb_ai_chat_feedback.applied_to_index   IS 'intent_index에 반영되었는지 (C2 재학습 플래그)';

COMMIT;
