-- Migration 0065: EPANET 수리 시뮬레이션 결과 저장 (Phase 2)
-- 사양: docs/gis_plan.md / docs/feature-spec.md §18-A
--
-- Phase 1 (Migration 0064) 의 tb_epanet_artifact (INP 파일) 에 대해
-- wntr 시뮬레이션 실행 결과를 저장. 정상상태 (Steady-state) — 단일 시점.
-- 시계열 시뮬레이션 (EPS) 은 Phase 2.5 에서 확장.
--
-- 롤백:
--   DROP TABLE IF EXISTS tb_epanet_simulation_result;

BEGIN;

CREATE TABLE IF NOT EXISTS tb_epanet_simulation_result (
    sim_id          BIGSERIAL PRIMARY KEY,
    artifact_id     BIGINT       NOT NULL REFERENCES tb_epanet_artifact(artifact_id) ON DELETE CASCADE,
    region          VARCHAR(10)  NOT NULL,
    sim_type        VARCHAR(20)  NOT NULL DEFAULT 'steady',  -- steady / eps
    status          VARCHAR(20)  NOT NULL DEFAULT 'pending', -- pending / success / failed
    -- 수치 요약 (빠른 조회용 캐시)
    node_count      INTEGER      NOT NULL DEFAULT 0,
    link_count      INTEGER      NOT NULL DEFAULT 0,
    min_pressure_m  DOUBLE PRECISION,
    max_pressure_m  DOUBLE PRECISION,
    avg_pressure_m  DOUBLE PRECISION,
    min_flow_lps    DOUBLE PRECISION,
    max_flow_lps    DOUBLE PRECISION,
    -- 상세 결과 (JSONB) — 노드별·링크별 결과
    --   { junctions: [{id, pressure_m, head_m, demand_lps}],
    --     pipes:     [{id, flow_lps, velocity_mps, headloss_m}] }
    result_data     JSONB,
    duration_ms     INTEGER,                   -- 시뮬레이션 실행 시간 (ms)
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by      VARCHAR(50) NOT NULL,
    CONSTRAINT chk_epanet_sim_status CHECK (status IN ('pending','success','failed')),
    CONSTRAINT chk_epanet_sim_type   CHECK (sim_type IN ('steady','eps'))
);

CREATE INDEX IF NOT EXISTS idx_epanet_sim_artifact_created
    ON tb_epanet_simulation_result(artifact_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_epanet_sim_region_created
    ON tb_epanet_simulation_result(region, created_at DESC);

COMMENT ON TABLE  tb_epanet_simulation_result IS 'EPANET 시뮬레이션 결과 (Phase 2)';
COMMENT ON COLUMN tb_epanet_simulation_result.sim_type    IS 'steady=정상상태 / eps=시계열';
COMMENT ON COLUMN tb_epanet_simulation_result.result_data IS 'JSONB: junctions[]/pipes[] 상세';

COMMIT;
