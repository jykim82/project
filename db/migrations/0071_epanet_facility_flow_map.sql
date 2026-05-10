-- Migration 0071: EPANET 시설 ↔ 실측 유량 태그 매핑 (B-1)
-- 사양: docs/epanet-flow-injection-spec.md §2.1
--
-- 시설별 outflow / inflow 측정 태그를 EPANET demand 입력으로 자동 주입하기
-- 위한 매핑. INP 변환 시 inject_live_demand=True 면 매핑된 시설별 실측 평균을
-- LPS 로 환산해 demand_points 에 합쳐 IDW 보간.
--
-- 단위: cmh / lps / m3h / lpm / m3s 지원. 내부 표준은 LPS.
-- 좌표: EPSG:5186 (Korea 2000 / Central Belt 2010) — 기존 SHP 좌표계와 일치.
--
-- 본 테이블은 기존 `tb_facility_flow_map` (시설 간 상하류 관계) 와 별개.
--
-- 롤백: DROP TABLE IF EXISTS tb_epanet_facility_flow_map;

BEGIN;

CREATE TABLE IF NOT EXISTS tb_epanet_facility_flow_map (
    map_id        BIGSERIAL PRIMARY KEY,
    region        VARCHAR(10)      NOT NULL,
    sitename      VARCHAR(50)      NOT NULL,
    facilitytype  VARCHAR(30)      NOT NULL,
    role          VARCHAR(20)      NOT NULL,
    tagsn         VARCHAR(100)     NOT NULL,
    unit          VARCHAR(20)      NOT NULL,
    scale         DOUBLE PRECISION NOT NULL DEFAULT 1,
    x             DOUBLE PRECISION NOT NULL,
    y             DOUBLE PRECISION NOT NULL,
    enabled       CHAR(1)          NOT NULL DEFAULT 'Y',
    notes         TEXT,
    created_at    TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    created_by    VARCHAR(50)      NOT NULL,
    CONSTRAINT chk_epanet_facility_flow_role CHECK (role IN ('outflow','inflow')),
    CONSTRAINT chk_epanet_facility_flow_unit CHECK (unit IN ('cmh','lps','m3h','lpm','m3s')),
    CONSTRAINT chk_epanet_facility_flow_enabled CHECK (enabled IN ('Y','N')),
    CONSTRAINT uq_epanet_facility_flow_region_site_role
        UNIQUE (region, sitename, facilitytype, role)
);

CREATE INDEX IF NOT EXISTS idx_epanet_facility_flow_region
    ON tb_epanet_facility_flow_map(region, enabled);
CREATE INDEX IF NOT EXISTS idx_epanet_facility_flow_xy
    ON tb_epanet_facility_flow_map(region, x, y);
CREATE INDEX IF NOT EXISTS idx_epanet_facility_flow_tagsn
    ON tb_epanet_facility_flow_map(tagsn);

COMMENT ON TABLE  tb_epanet_facility_flow_map IS
    '시설(배수지·가압장·블록) ↔ 실측 유량 태그 매핑 (B-1: live demand injection)';
COMMENT ON COLUMN tb_epanet_facility_flow_map.role IS
    'outflow=시설 출수량, inflow=시설 유입량 (배수지만 사용)';
COMMENT ON COLUMN tb_epanet_facility_flow_map.unit IS
    '입력 단위. 내부는 LPS 표준. cmh/m3h ÷ 3.6, lpm ÷ 60, m3s × 1000';
COMMENT ON COLUMN tb_epanet_facility_flow_map.scale IS
    '센서 raw → 표준 unit 환산 곱 (캘리브레이션, 기본 1)';

COMMIT;
