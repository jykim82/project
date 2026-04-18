-- 0046_chat_log.sql
-- 채팅 전체 질의 로그 — intent 정확도·응답시간 분석용 (폐쇄망 운영)
-- 사용자 피드백(tb_ai_chat_feedback)과 JOIN하여 인텐트별 오답률 계산.

BEGIN;

CREATE TABLE IF NOT EXISTS tb_ai_chat_log (
    log_id              BIGSERIAL PRIMARY KEY,
    region              VARCHAR(10)  NOT NULL,
    user_id             VARCHAR(45),
    user_question       TEXT         NOT NULL,
    intent_name         VARCHAR(80),
    intent_confidence   NUMERIC(4,3),            -- 0.000~1.000 (있으면)
    graph_type          VARCHAR(20),             -- none/table/plot/diagram/...
    response_time_ms    INTEGER,                 -- 요청 수신~응답 반환 ms
    bot_summary         TEXT,                    -- 답변 summary (preview; 전체 X)
    total_rows          INTEGER,                 -- 결과 행 수 (있으면)
    has_visual          BOOLEAN NOT NULL DEFAULT FALSE,
    is_multimodal       BOOLEAN NOT NULL DEFAULT FALSE,  -- 이미지 포함 질의 여부
    error               TEXT,                    -- 오류 메시지 (있으면)
    asked_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_log_asked_at
    ON tb_ai_chat_log (asked_at DESC);

CREATE INDEX IF NOT EXISTS idx_chat_log_intent
    ON tb_ai_chat_log (intent_name, asked_at DESC);

CREATE INDEX IF NOT EXISTS idx_chat_log_region_user
    ON tb_ai_chat_log (region, user_id, asked_at DESC);

COMMENT ON TABLE tb_ai_chat_log IS '채팅 전체 질의/응답 로그 (폐쇄망 운영, 인텐트 정확도·응답시간 분석용)';
COMMENT ON COLUMN tb_ai_chat_log.intent_confidence IS '분류기 score 0~1 (있으면). LLM 경로는 null 가능';
COMMENT ON COLUMN tb_ai_chat_log.response_time_ms IS '요청 수신 ~ 응답 반환까지의 ms. 스트리밍 종료 시 총합';

COMMIT;
