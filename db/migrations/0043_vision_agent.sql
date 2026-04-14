-- ============================================================
-- Migration 0043: Vision Agent (멀티모달 현장 진단 · 설비 등록)
-- ============================================================
-- 관련 문서: docs/ultraplan_*.html 섹션 2 (DB 스키마 변경)
-- 에러 이력: [E-025] 멀티모달 현장 진단 Phase 1 DB 기반
--
-- 신규 테이블 3개:
--   1. tb_equipment_image   — 명판/외관/위치/고장 사진 + OCR 원본 + VLM 메타
--   2. tb_equipment_manual  — 제조사 매뉴얼 PDF + RAG 임베딩 키
--   3. tb_vision_session    — 비전 호출 감사 로그 + 채팅→작업→등록 추적
--
-- 기존 테이블 확장:
--   - tb_task_master.vision_session_id    (작업→비전 세션 링크)
--   - tb_equipment_info.equipment_photo_url (대표 사진)
--   - tb_equipment_info.nameplate_photo_url (명판 사진)
--
-- ⚠ plan에서는 tb_equipment로 명시됐으나 실제 테이블명은 tb_equipment_info.
-- 멀티테넌시 PK는 (region, equipment_id)가 아니고 (equipment_id) 단일이다.
-- ============================================================

SET client_encoding = 'UTF8';

BEGIN;

-- ─────────────────────────────────────────────────────────
-- 1. tb_equipment_image — 설비 사진 (명판/외관/위치/고장)
-- ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tb_equipment_image (
    image_id        bigserial PRIMARY KEY,
    -- 등록 대상 설비 (미등록 상태에서도 사진 등록 가능 → NULL 허용)
    equipment_id    character varying(64),
    sitename        character varying(100),
    facilitytype    text,
    -- 파일 저장 경로 (/admin/facility-files 업로드 후 반환된 URL)
    image_url       text NOT NULL,
    image_kind      text NOT NULL
        CHECK (image_kind IN ('nameplate', 'exterior', 'location', 'failure')),
    -- OCR 원본 텍스트 (감사용, 재처리 시 재참조 가능)
    ocr_text        text,
    -- VLM 분석 메타 (equipment_guess, brand, model, confidence, observed_state)
    vlm_meta        jsonb,
    uploaded_by     character varying(45),
    uploaded_at     timestamp with time zone DEFAULT now()
);

COMMENT ON TABLE tb_equipment_image IS
    '설비 사진 (명판/외관/위치/고장). equipment_id NULL은 미등록 장비용.';
COMMENT ON COLUMN tb_equipment_image.image_kind IS
    'nameplate(명판) | exterior(외관) | location(위치) | failure(고장 장면)';
COMMENT ON COLUMN tb_equipment_image.vlm_meta IS
    'VLM 응답 JSON: {equipment_guess, brand, model, confidence, observed_state[]}';

CREATE INDEX IF NOT EXISTS ix_equipment_image_eq
    ON tb_equipment_image(equipment_id);
CREATE INDEX IF NOT EXISTS ix_equipment_image_site
    ON tb_equipment_image(sitename, facilitytype);

-- equipment_id NOT NULL 시점에 tb_equipment_info FK
ALTER TABLE tb_equipment_image
    DROP CONSTRAINT IF EXISTS fk_equipment_image_equipment;
ALTER TABLE tb_equipment_image
    ADD CONSTRAINT fk_equipment_image_equipment
    FOREIGN KEY (equipment_id)
    REFERENCES tb_equipment_info(equipment_id)
    ON DELETE CASCADE;

-- ─────────────────────────────────────────────────────────
-- 2. tb_equipment_manual — 장비 매뉴얼 메타 (PDF + RAG 인덱스)
-- ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tb_equipment_manual (
    manual_id       bigserial PRIMARY KEY,
    -- 매뉴얼이 다루는 장비 종류 (PLC/유량계/모뎀/RTU/펌프/밸브/수위계/압력계/UPS)
    equipment_type  text NOT NULL,
    brand           text,           -- 제조사 (LS, SIEMENS, 삼성, ABB 등)
    model           text,           -- 모델명 (XGB-XBCH, S7-1200 등)
    title           text NOT NULL,
    -- PDF 파일 경로 (/admin/facility-files 저장)
    file_url        text NOT NULL,
    page_count      integer,
    -- RAG 임베딩 캐시 키 (data/manual_embeddings/<key>.npy)
    embedding_key   text,
    uploaded_by     character varying(45),
    uploaded_at     timestamp with time zone DEFAULT now()
);

COMMENT ON TABLE tb_equipment_manual IS
    '장비 매뉴얼 PDF + RAG 임베딩 인덱스 매핑. vision_agent 매뉴얼 검색 소스.';
COMMENT ON COLUMN tb_equipment_manual.embedding_key IS
    'RAG 임베딩 캐시 키. data/manual_embeddings/<key>.npy (snowflake-arctic-embed2)';

CREATE INDEX IF NOT EXISTS ix_equipment_manual_type_brand_model
    ON tb_equipment_manual(equipment_type, brand, model);

-- ─────────────────────────────────────────────────────────
-- 3. tb_vision_session — 비전 세션 (감사 로그 + 추적)
-- ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tb_vision_session (
    vision_session_id       bigserial PRIMARY KEY,
    user_id                 character varying(45),
    region                  character varying(10),
    -- 업로드된 이미지 URL (/admin/facility-files)
    image_url               text NOT NULL,
    image_kind              text,
    -- vision_agent 원본 응답 전체 (재사용/디버깅)
    agent_response          jsonb NOT NULL,
    -- 컨텍스트 힌트 (GIS 페이지에서 온 경우 등)
    sitename                character varying(100),
    facilitytype            text,
    -- 후속 액션 연결 (이 세션이 작업/설비 등록으로 이어졌는지)
    linked_task_id          integer,
    linked_equipment_id     character varying(64),
    created_at              timestamp with time zone DEFAULT now()
);

COMMENT ON TABLE tb_vision_session IS
    '비전 호출 감사 로그. 채팅→작업→설비 등록으로 이어지는 추적 ID.';
COMMENT ON COLUMN tb_vision_session.agent_response IS
    'vision_agent 응답 JSON 전체 (equipment_guess, manual_excerpts, advice_text 등)';

CREATE INDEX IF NOT EXISTS ix_vision_session_user
    ON tb_vision_session(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_vision_session_task
    ON tb_vision_session(linked_task_id)
    WHERE linked_task_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_vision_session_equipment
    ON tb_vision_session(linked_equipment_id)
    WHERE linked_equipment_id IS NOT NULL;

-- linked_task_id FK (tb_task_master.task_id는 integer)
ALTER TABLE tb_vision_session
    DROP CONSTRAINT IF EXISTS fk_vision_session_task;
ALTER TABLE tb_vision_session
    ADD CONSTRAINT fk_vision_session_task
    FOREIGN KEY (linked_task_id)
    REFERENCES tb_task_master(task_id)
    ON DELETE SET NULL;

-- linked_equipment_id FK
ALTER TABLE tb_vision_session
    DROP CONSTRAINT IF EXISTS fk_vision_session_equipment;
ALTER TABLE tb_vision_session
    ADD CONSTRAINT fk_vision_session_equipment
    FOREIGN KEY (linked_equipment_id)
    REFERENCES tb_equipment_info(equipment_id)
    ON DELETE SET NULL;

-- ─────────────────────────────────────────────────────────
-- 4. 기존 테이블 확장
-- ─────────────────────────────────────────────────────────

-- tb_task_master에 비전 세션 링크 (alarm_report_id 옆에)
ALTER TABLE tb_task_master
    ADD COLUMN IF NOT EXISTS vision_session_id bigint;
ALTER TABLE tb_task_master
    DROP CONSTRAINT IF EXISTS fk_task_vision_session;
ALTER TABLE tb_task_master
    ADD CONSTRAINT fk_task_vision_session
    FOREIGN KEY (vision_session_id)
    REFERENCES tb_vision_session(vision_session_id)
    ON DELETE SET NULL;
COMMENT ON COLUMN tb_task_master.vision_session_id IS
    '이 작업이 비전 진단에서 등록된 경우 vision_session 링크 [E-025]';

-- tb_equipment_info에 대표/명판 사진 URL
ALTER TABLE tb_equipment_info
    ADD COLUMN IF NOT EXISTS equipment_photo_url text,
    ADD COLUMN IF NOT EXISTS nameplate_photo_url text;
COMMENT ON COLUMN tb_equipment_info.equipment_photo_url IS
    '설비 대표 사진 URL (/admin/facility-files) [E-025]';
COMMENT ON COLUMN tb_equipment_info.nameplate_photo_url IS
    '설비 명판 사진 URL — OCR 원본 보존 용도 [E-025]';

COMMIT;
