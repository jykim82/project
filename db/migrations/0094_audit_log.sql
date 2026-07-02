-- Migration 0094: 감사 로그 (audit) — 시설·설비 CRUD 변경 이력
-- 사양: docs/gis-facility-menu-spec.md §5.3 (권한·감사)
--
-- 시설/설비 자산 변경(추가/수정/삭제)을 누가·언제·무엇을 기준으로 기록한다.
-- 1단계는 설비 CRUD(facility_crud) 에 적용, 이후 시설 CRUD 로 확대.
-- actor 는 JWT sub(user_id). 토큰 없으면 'unknown' (기존 무인증 호출 비파괴).
--
-- 롤백:
--   DROP TABLE IF EXISTS tb_audit_log;

BEGIN;

CREATE TABLE IF NOT EXISTS tb_audit_log (
    audit_id     BIGSERIAL PRIMARY KEY,
    region       VARCHAR(20),
    actor        VARCHAR(64)  NOT NULL DEFAULT 'unknown',  -- JWT sub(user_id)
    action       VARCHAR(20)  NOT NULL,                    -- create / update / delete
    target_type  VARCHAR(40)  NOT NULL,                    -- 'equipment' / 'reservoir' ...
    target_key   VARCHAR(128) NOT NULL,                    -- equipment_id / sitename
    summary      TEXT,                                     -- 사람이 읽는 요약
    detail       JSONB,                                    -- payload / before-after
    client_ip    VARCHAR(64),
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_log_target
    ON tb_audit_log (target_type, target_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_created
    ON tb_audit_log (created_at DESC);

COMMIT;
