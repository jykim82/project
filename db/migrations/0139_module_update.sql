-- 0139_module_update.sql
-- 업데이트 번들 반입·적용 이력 (docs/module-version-spec.md P2/P3)
--
-- 번들 파일 자체는 files/updates/ 에, 이 테이블은 상태·감사 이력.
-- 상태 흐름: uploaded → verified|failed → approved → applying →
--            applied | apply_failed(자동 복원됨) → rollback_requested → rolled_back
--
-- 롤백: 파일 하단 ROLLBACK 블록

BEGIN;

CREATE TABLE IF NOT EXISTS tb_module_update (
    region       varchar(20)  NOT NULL DEFAULT 'R01',
    bundle_id    varchar(40)  NOT NULL,
    filename     varchar(200) NOT NULL,
    manifest     jsonb        NOT NULL DEFAULT '{}'::jsonb,
    status       varchar(24)  NOT NULL DEFAULT 'uploaded',
    -- 검증·적용 로그 (사유·실패 상세 — 감사 추적)
    detail       text         NOT NULL DEFAULT '',
    uploaded_by  varchar(50)  NOT NULL DEFAULT 'unknown',
    uploaded_at  timestamptz  NOT NULL DEFAULT now(),
    approved_by  varchar(50),
    approved_at  timestamptz,
    applied_at   timestamptz,
    updated_at   timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (region, bundle_id)
);

CREATE INDEX IF NOT EXISTS idx_module_update_list
    ON tb_module_update (region, uploaded_at DESC);

COMMENT ON TABLE tb_module_update IS
  '업데이트 번들 반입·적용 이력 (module-version-spec P2/P3 — 적용은 호스트 에이전트)';

COMMIT;

-- =============================================================================
-- ROLLBACK
-- =============================================================================
-- DROP TABLE IF EXISTS tb_module_update;
