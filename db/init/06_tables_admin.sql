-- ============================================================
-- 06: Admin Tables - 프롬프트, 파일, 필드 잠금
-- ============================================================

SET client_encoding = 'UTF8';

-- ⚠ tb_prompt_template / tb_prompt_column 제거됨 [E-020] (2026-04-13)
-- 프롬프트는 코드(슬롯-필링 + example3.json)에서 직접 관리되며 DB 테이블 미사용.
-- /admin/prompts 메뉴(M100-3)도 숨김 처리 상태(sidebar-menus.ts).
-- 백업: db/backups/unused_tables_backup_2026-04-13.sql

-- ------------------------------------------------------------
-- 파일 스토리지
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tb_file_storage (
    file_id        bigserial PRIMARY KEY,
    region         character varying(10) NOT NULL,
    file_category  character varying(30) NOT NULL,    -- 'chat_ask_image', 'chat_bot_image', 'upload' 등
    original_name  character varying(200),
    stored_name    character varying(200) NOT NULL,    -- UUID 기반 저장 파일명
    file_path      character varying(500) NOT NULL,    -- 상대 경로
    file_url       character varying(500),
    mime_type      character varying(100),
    file_size      bigint,
    uploaded_by    character varying(45),
    created_at     timestamp with time zone DEFAULT now()
);
COMMENT ON TABLE tb_file_storage IS '파일 스토리지 관리. 이미지/문서 파일의 메타데이터';

CREATE INDEX IF NOT EXISTS idx_file_storage_category
    ON tb_file_storage(file_category, region);

-- ⚠ tb_ai_chat_ask_image / tb_ai_chat_bot_image FK 제거됨 [E-020] (2026-04-13)
--    채팅 이미지 테이블 자체가 미사용으로 드롭되어 FK도 함께 제거됨.

-- ⚠ tb_file_history 제거됨 [E-020] (2026-04-13)
--    파일 이력 추적은 미구현 상태였으므로 제거. 필요 시 tb_file_storage에
--    audit 컬럼(version, replaced_by 등) 추가로 대체 가능.
--    백업: db/backups/unused_tables_backup_2026-04-13.sql

-- ------------------------------------------------------------
-- 컬럼 잠금 관리 (tb_field_lock)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tb_field_lock (
    lock_id       bigserial PRIMARY KEY,
    region        character varying(10) NOT NULL,
    target_table  character varying(100) NOT NULL,
    target_key    jsonb NOT NULL,                     -- 대상 행 식별 (복합키 JSON)
    field_name    character varying(100) NOT NULL,
    is_locked     boolean NOT NULL DEFAULT false,
    lock_reason   character varying(200),
    locked_by     character varying(45),
    locked_at     timestamp with time zone,
    created_at    timestamp with time zone DEFAULT now(),
    updated_at    timestamp with time zone DEFAULT now(),
    CONSTRAINT uq_field_lock UNIQUE (region, target_table, target_key, field_name)
);
COMMENT ON TABLE tb_field_lock IS '시설별 컬럼 잠금 관리. 현장 로컬 제어 항목은 잠금 처리하여 웹에서 수정 불가';
COMMENT ON COLUMN tb_field_lock.target_key IS '대상 행 식별 (복합키 JSON). 예: {"sitename":"신평"}';
COMMENT ON COLUMN tb_field_lock.is_locked IS 'true=현장 로컬 제어(웹 수정 불가), false=시스템 제어(웹 수정 가능)';

CREATE INDEX IF NOT EXISTS idx_field_lock_target
    ON tb_field_lock(region, target_table);
