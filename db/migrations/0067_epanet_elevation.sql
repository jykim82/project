-- Migration 0067: EPANET 표고 입력 (Phase 3.1)
-- 사양: docs/epanet-menu-spec.md §2.2 (HAS_ELEVATION 충족) / docs/gis_plan.md Phase 3
--
-- SHP 데이터에 Z 값이 0 이고 시설 마스터에 직접 표고 컬럼 없음 → 운영자가 표고를
-- 알려진 지점에 입력 → INP 변환 시 IDW(역거리가중) 보간으로 모든 junction 에 부여.
--
-- 좌표계는 SHP 와 동일 (default EPSG:5186 — Korea 2000 / Central Belt 2010).
-- source 필드:
--   manual    — 운영자가 단건 입력
--   csv       — CSV 일괄 업로드
--   facility  — 시설 마스터에서 자동 import (Phase 3.1b)
--   synthetic — 합성 표고 (시연용, INP 변환 시 즉석 생성, 본 테이블엔 저장 X)
--
-- 롤백: DROP TABLE IF EXISTS tb_epanet_elevation_point;

BEGIN;

CREATE TABLE IF NOT EXISTS tb_epanet_elevation_point (
    point_id      BIGSERIAL PRIMARY KEY,
    region        VARCHAR(10)      NOT NULL,
    x             DOUBLE PRECISION NOT NULL,
    y             DOUBLE PRECISION NOT NULL,
    elevation_m   DOUBLE PRECISION NOT NULL,
    source        VARCHAR(20)      NOT NULL DEFAULT 'manual',
    label         VARCHAR(100),
    notes         TEXT,
    created_at    TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    created_by    VARCHAR(50)      NOT NULL,
    CONSTRAINT chk_epanet_elev_source CHECK (source IN ('manual','csv','facility'))
);

CREATE INDEX IF NOT EXISTS idx_epanet_elev_region
    ON tb_epanet_elevation_point(region, created_at DESC);

-- 좌표 가까운 점 빠른 조회 (IDW 시 KNN 검색)
CREATE INDEX IF NOT EXISTS idx_epanet_elev_xy
    ON tb_epanet_elevation_point(region, x, y);

COMMENT ON TABLE  tb_epanet_elevation_point IS '운영자가 입력한 표고 지점 (INP 변환 시 IDW 보간 입력)';
COMMENT ON COLUMN tb_epanet_elevation_point.x IS 'SHP 좌표계 X (default EPSG:5186)';
COMMENT ON COLUMN tb_epanet_elevation_point.y IS 'SHP 좌표계 Y';
COMMENT ON COLUMN tb_epanet_elevation_point.source IS 'manual / csv / facility';

COMMIT;
