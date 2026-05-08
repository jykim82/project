-- Migration 0069: 센서 ↔ EPANET 노드 매핑 (Phase 3.3a)
-- 사양: docs/epanet-menu-spec.md §2.2 (HAS_METER_MAPPING) — leak-suspicious /
-- network-aging 메뉴 활성을 위한 인프라.
--
-- 매핑 방식: 좌표 (x, y) 기반 — 시뮬마다 INP 의 노드 ID 가 좌표 해시이므로 안정적.
-- 시뮬 결과 비교 시 매핑 좌표에서 가장 가까운 EPANET junction 을 KNN 으로 매칭.
--
-- tag_sn:  tb_tag_master.tag_sn (압력 태그) — FK 강제 안 함 (외부 시스템 호환성)
-- calibration_offset_m: 센서 측정값에 더할 보정값 (m). 실측 = raw + offset.
--
-- 롤백: DROP TABLE IF EXISTS tb_epanet_meter_map;

BEGIN;

CREATE TABLE IF NOT EXISTS tb_epanet_meter_map (
    map_id                BIGSERIAL PRIMARY KEY,
    region                VARCHAR(10)      NOT NULL,
    tag_sn                VARCHAR(100)     NOT NULL,
    x                     DOUBLE PRECISION NOT NULL,
    y                     DOUBLE PRECISION NOT NULL,
    calibration_offset_m  DOUBLE PRECISION NOT NULL DEFAULT 0,
    label                 VARCHAR(100),
    notes                 TEXT,
    created_at            TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    created_by            VARCHAR(50)      NOT NULL,
    CONSTRAINT uq_epanet_meter_map_region_tag UNIQUE (region, tag_sn)
);

CREATE INDEX IF NOT EXISTS idx_epanet_meter_region
    ON tb_epanet_meter_map(region, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_epanet_meter_xy
    ON tb_epanet_meter_map(region, x, y);

COMMENT ON TABLE  tb_epanet_meter_map IS '센서(압력 태그) ↔ EPANET 노드 좌표 매핑';
COMMENT ON COLUMN tb_epanet_meter_map.tag_sn IS 'tb_tag_master.tag_sn — 압력 태그 SN';
COMMENT ON COLUMN tb_epanet_meter_map.calibration_offset_m IS '실측 = raw + offset (m)';

COMMIT;
