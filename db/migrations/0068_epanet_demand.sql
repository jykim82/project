-- Migration 0068: EPANET 수요 입력 (Phase 3.2)
-- 사양: docs/epanet-menu-spec.md §2.2 (HAS_DEMAND_PROFILE 충족)
--
-- Phase 3.1 표고 입력과 동일 패턴 — 운영자가 알려진 지점의 수요를 입력하면
-- INP 변환 시 IDW(역거리가중) 보간으로 모든 junction 에 노드별 수요 부여.
--
-- source 필드:
--   manual    — 운영자 단건 입력
--   csv       — CSV 일괄 업로드
--   facility  — 시설 마스터(가압장·블록) 출수량 import (Phase 3.2b)
--
-- 롤백: DROP TABLE IF EXISTS tb_epanet_demand_point;

BEGIN;

CREATE TABLE IF NOT EXISTS tb_epanet_demand_point (
    point_id      BIGSERIAL PRIMARY KEY,
    region        VARCHAR(10)      NOT NULL,
    x             DOUBLE PRECISION NOT NULL,
    y             DOUBLE PRECISION NOT NULL,
    demand_lps    DOUBLE PRECISION NOT NULL,
    source        VARCHAR(20)      NOT NULL DEFAULT 'manual',
    label         VARCHAR(100),
    notes         TEXT,
    created_at    TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    created_by    VARCHAR(50)      NOT NULL,
    CONSTRAINT chk_epanet_demand_source CHECK (source IN ('manual','csv','facility'))
);

CREATE INDEX IF NOT EXISTS idx_epanet_demand_region
    ON tb_epanet_demand_point(region, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_epanet_demand_xy
    ON tb_epanet_demand_point(region, x, y);

COMMENT ON TABLE  tb_epanet_demand_point IS '운영자 입력 수요 지점 (INP 변환 시 IDW 보간)';
COMMENT ON COLUMN tb_epanet_demand_point.demand_lps IS '초당 리터 (Liters Per Second)';

COMMIT;
