-- Migration 0064: EPANET 수리 시뮬레이션 Phase 1
-- 사양: docs/gis_plan.md §모듈화 설계 방침 (2026-04-02 결정)
--
-- 본 migration 이 추가하는 것:
--   1) SITE_SETTING/EPANET_ENABLED — 모듈 활성화 토글 (default 'N' — opt-in)
--   2) tb_epanet_artifact — SHP→.inp 변환 결과 메타 테이블 (region 멀티테넌시)
--   3) tb_menu M100-12 — 관리 그룹에 "EPANET 시뮬레이션" 메뉴 추가
--   4) tb_auth_menu — MASTER/ADMIN 접근 권한
--
-- 비활성(default) 상태에선:
--   - 백엔드 endpoints/epanet.py 라우터는 등록되지만 모든 엔드포인트가 503 반환
--   - 프런트 /admin/epanet 페이지는 안내 카드만 표시 (토글 ON 안내)
--   - GIS 페이지에는 영향 없음 (오버레이 컴포넌트는 Phase 2 에서 추가)
--
-- 롤백:
--   DELETE FROM tb_auth_menu WHERE menu_idn = 'M100-12';
--   DELETE FROM tb_menu      WHERE menu_idn = 'M100-12';
--   DELETE FROM tb_comm_code WHERE grp_cd = 'SITE_SETTING' AND comm_cd = 'EPANET_ENABLED';
--   DROP TABLE IF EXISTS tb_epanet_artifact;

BEGIN;

-- ============================================================================
-- 1) SITE_SETTING/EPANET_ENABLED — 활성화 토글
-- ============================================================================
INSERT INTO tb_grp_code (region, grp_cd, grp_nm, use_yn)
SELECT DISTINCT region, 'SITE_SETTING', '사이트 설정', 'Y'
FROM tb_menu
ON CONFLICT (region, grp_cd) DO NOTHING;

INSERT INTO tb_comm_code (region, grp_cd, comm_cd, comm_nm, use_yn)
SELECT DISTINCT region, 'SITE_SETTING', 'EPANET_ENABLED',
       'EPANET 수리 시뮬레이션 활성화', 'N'
FROM tb_menu
ON CONFLICT (region, grp_cd, comm_cd) DO NOTHING;

-- ============================================================================
-- 2) tb_epanet_artifact — SHP→.inp 변환 결과 메타
-- ============================================================================
CREATE TABLE IF NOT EXISTS tb_epanet_artifact (
    artifact_id    BIGSERIAL PRIMARY KEY,
    region         VARCHAR(10)  NOT NULL,
    file_path      TEXT         NOT NULL,           -- 컨테이너 내 절대 경로 (/data/files/epanet/...)
    file_name      VARCHAR(200) NOT NULL,           -- 사용자 다운로드 시 노출되는 이름
    source_shp     VARCHAR(500),                    -- 변환에 사용된 SHP 파일 목록 (콤마 구분)
    node_count     INTEGER      NOT NULL DEFAULT 0,
    link_count     INTEGER      NOT NULL DEFAULT 0,
    status         VARCHAR(20)  NOT NULL DEFAULT 'pending',  -- pending/success/failed
    file_size_bytes BIGINT,
    error_message  TEXT,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_by     VARCHAR(50)  NOT NULL,
    CONSTRAINT chk_epanet_artifact_status
        CHECK (status IN ('pending', 'success', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_epanet_artifact_region_created
    ON tb_epanet_artifact(region, created_at DESC);

COMMENT ON TABLE  tb_epanet_artifact IS 'EPANET .inp 변환 산출물 메타 (Phase 1)';
COMMENT ON COLUMN tb_epanet_artifact.file_path  IS '컨테이너 내 절대 경로';
COMMENT ON COLUMN tb_epanet_artifact.source_shp IS '변환 입력 SHP 파일명 (콤마 구분)';
COMMENT ON COLUMN tb_epanet_artifact.status     IS 'pending/success/failed';

-- ============================================================================
-- 3) tb_menu M100-12 — 관리 그룹에 "EPANET 시뮬레이션" 추가
-- ============================================================================
INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path, menu_type, menu_idx, use_yn)
SELECT region, 'M100-12', 'EPANET 시뮬레이션', 'M100', '/admin/epanet',
       'menu', 12, 'Y'
FROM tb_menu
WHERE menu_idn = 'M100'
ON CONFLICT (region, menu_idn) DO NOTHING;

-- ============================================================================
-- 4) tb_auth_menu — MASTER/ADMIN 접근 권한
-- ============================================================================
INSERT INTO tb_auth_menu (region, auth_idn, menu_idn, use_yn, menu_order)
SELECT m.region, a.auth_idn, 'M100-12', 'Y', 12
FROM tb_menu m
CROSS JOIN (VALUES ('MASTER'), ('ADMIN')) AS a(auth_idn)
WHERE m.menu_idn = 'M100-12'
ON CONFLICT (region, auth_idn, menu_idn) DO NOTHING;

COMMIT;
