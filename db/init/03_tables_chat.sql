-- ============================================================
-- 03: Chat Tables - AI 채팅 세션, 메시지, 이미지, FAQ
-- ============================================================

SET client_encoding = 'UTF8';

-- ------------------------------------------------------------
-- 채팅 세션 그룹
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tb_ai_chat_ask_group (
    group_id    character varying(100) NOT NULL,
    region      character varying(10) NOT NULL,
    user_id     character varying(45) NOT NULL,
    group_title character varying(200),
    last_dt     character varying(8),               -- 레거시: YYYYMMDD
    last_at     timestamp with time zone,           -- 개선: timestamptz
    del_yn      character varying(1) DEFAULT 'N',
    created_at  timestamp with time zone DEFAULT now(),
    PRIMARY KEY (group_id, region, user_id)
);
COMMENT ON TABLE tb_ai_chat_ask_group IS 'AI 채팅 세션 그룹. del_yn=Y로 소프트 삭제';

CREATE INDEX IF NOT EXISTS idx_chat_ask_group_list
    ON tb_ai_chat_ask_group(region, user_id, del_yn);

-- ------------------------------------------------------------
-- 사용자 질문
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tb_ai_chat_ask (
    ask_seq   bigserial,
    region    character varying(10) NOT NULL,
    user_id   character varying(45) NOT NULL,
    group_id  character varying(100),
    ask_msg   text,
    ask_dt    character varying(8),                 -- 레거시: YYYYMMDD
    ask_tm    character varying(6),                 -- 레거시: HHMMSS
    ask_at    timestamp with time zone,             -- 개선: timestamptz
    PRIMARY KEY (ask_seq, region, user_id)
);
COMMENT ON TABLE tb_ai_chat_ask IS 'AI 채팅 사용자 질문';

ALTER TABLE tb_ai_chat_ask
    ADD CONSTRAINT fk_chat_ask_user
    FOREIGN KEY (region, user_id)
    REFERENCES tb_user(region, user_id);

ALTER TABLE tb_ai_chat_ask
    ADD CONSTRAINT fk_chat_ask_group
    FOREIGN KEY (group_id, region, user_id)
    REFERENCES tb_ai_chat_ask_group(group_id, region, user_id);

CREATE INDEX IF NOT EXISTS idx_chat_ask_group_user
    ON tb_ai_chat_ask(region, user_id, group_id);

-- ------------------------------------------------------------
-- 봇 응답
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tb_ai_chat_bot (
    ask_seq          bigint NOT NULL,
    region           character varying(10) NOT NULL,
    user_id          character varying(45) NOT NULL,
    bot_msg          text,
    references_data  jsonb,                         -- 참조 데이터
    recquestions     jsonb,                          -- 추천 질문
    visual_data      jsonb,                          -- 시각화 데이터 (table/plot/diagram/document)
    bot_dt           character varying(8),           -- 레거시
    bot_tm           character varying(6),           -- 레거시
    bot_at           timestamp with time zone,       -- 개선: timestamptz
    PRIMARY KEY (ask_seq, region, user_id)
);
COMMENT ON TABLE tb_ai_chat_bot IS 'AI 채팅 봇 응답. visual_data로 차트 재렌더링';
COMMENT ON COLUMN tb_ai_chat_bot.visual_data IS
    'graph_type별 시각화 데이터 (table/plot/diagram/document). 히스토리에서 차트 재렌더링용';

ALTER TABLE tb_ai_chat_bot
    ADD CONSTRAINT fk_chat_bot_ask
    FOREIGN KEY (ask_seq, region, user_id)
    REFERENCES tb_ai_chat_ask(ask_seq, region, user_id);

CREATE INDEX IF NOT EXISTS idx_chat_bot_visual_type
    ON tb_ai_chat_bot ((visual_data ->> 'type'))
    WHERE visual_data IS NOT NULL;

-- ------------------------------------------------------------
-- 질문 첨부 이미지
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tb_ai_chat_ask_image (
    image_seq  bigserial PRIMARY KEY,
    ask_seq    bigint NOT NULL,
    region     character varying(10) NOT NULL,
    user_id    character varying(45) NOT NULL,
    image_data bytea,                               -- 레거시 (향후 제거)
    file_id    bigint,                              -- 개선: tb_file_storage 참조
    created_at timestamp with time zone DEFAULT now()
);

ALTER TABLE tb_ai_chat_ask_image
    ADD CONSTRAINT fk_ask_image_ask
    FOREIGN KEY (ask_seq, region, user_id)
    REFERENCES tb_ai_chat_ask(ask_seq, region, user_id);

-- ------------------------------------------------------------
-- 봇 응답 이미지
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tb_ai_chat_bot_image (
    image_seq  bigserial PRIMARY KEY,
    ask_seq    bigint NOT NULL,
    region     character varying(10) NOT NULL,
    user_id    character varying(45) NOT NULL,
    image_data bytea,                               -- 레거시 (향후 제거)
    file_id    bigint,                              -- 개선: tb_file_storage 참조
    created_at timestamp with time zone DEFAULT now()
);

ALTER TABLE tb_ai_chat_bot_image
    ADD CONSTRAINT fk_bot_image_bot
    FOREIGN KEY (ask_seq, region, user_id)
    REFERENCES tb_ai_chat_bot(ask_seq, region, user_id);

-- ------------------------------------------------------------
-- 오분류 피드백 (수동 검토 게이트)
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

-- ------------------------------------------------------------
-- 채팅 FAQ
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tb_ai_chat_faq (
    region       character varying(10) NOT NULL,
    faq_idn      character varying(20) NOT NULL,
    faq_contents text,
    sort_num     integer DEFAULT 0,
    open_date    character varying(8),              -- 레거시
    close_date   character varying(8),              -- 레거시
    open_at      timestamp with time zone,          -- 개선
    close_at     timestamp with time zone,          -- 개선
    use_yn       character varying(1) DEFAULT 'Y',
    created_at   timestamp with time zone DEFAULT now(),
    PRIMARY KEY (region, faq_idn)
);
COMMENT ON TABLE tb_ai_chat_faq IS 'AI 채팅 FAQ. 채팅 시작 시 추천 질문으로 표시';
