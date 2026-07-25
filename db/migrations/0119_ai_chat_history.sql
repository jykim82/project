-- 0119: AI 채팅 대화 목록 서버 계정 전환 (docs/chat-history-server-spec.md)
-- localStorage 기반 대화 목록·메시지를 계정(DB) 기반으로 영속화.
-- 메신저(tb_chat_group)와 별개 — AI 채팅은 tb_ai_chat_* 계열.
--
-- 롤백:
--   DROP TABLE IF EXISTS tb_ai_chat_message;
--   DROP TABLE IF EXISTS tb_ai_chat_group;
--   (프런트는 서버 실패 시 localStorage 폴백으로 동작 유지.
--    이관 이후 서버에만 쌓인 이력은 유실되므로 필요시 사전 pg_dump)

CREATE TABLE IF NOT EXISTS tb_ai_chat_group (
    region      varchar(20)  NOT NULL,
    -- 프런트 생성 'g_{ts}_{rand}' 형식 그대로 수용 (localStorage 이관 호환)
    group_id    varchar(40)  NOT NULL,
    user_id     varchar(50)  NOT NULL,
    group_title varchar(200) NOT NULL DEFAULT '새 대화',
    sort_order  integer      NOT NULL DEFAULT 0,
    last_at     timestamptz  NOT NULL DEFAULT now(),
    del_yn      char(1)      NOT NULL DEFAULT 'N',
    created_at  timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (region, group_id)
);

CREATE INDEX IF NOT EXISTS idx_ai_chat_group_user
    ON tb_ai_chat_group (region, user_id, del_yn, sort_order, last_at DESC);

-- 메시지 페이로드는 jsonb 통짜 — 카드 유형(fault_draft/vision_advice/...)이
-- 늘어나는 개방 구조라 컬럼 정규화는 진화를 막음. 조회는 그룹 단위 전체 로드.
CREATE TABLE IF NOT EXISTS tb_ai_chat_message (
    region       varchar(20) NOT NULL,
    group_id     varchar(40) NOT NULL,
    ask_seq      bigint      NOT NULL,
    user_payload jsonb       NOT NULL,
    bot_payload  jsonb       NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (region, group_id, ask_seq)
);
