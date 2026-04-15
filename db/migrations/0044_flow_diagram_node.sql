-- [용수흐름도 다이어그램 모드] tb_flow_diagram_node
-- 기존 tb_facility_flow_map(상하류 엣지)는 그대로 두고, 노드의 시각적 배치·계층·
-- 줌 레벨별 노출 조건만 별도 관리. MapLibreGL 렌더러가 이 테이블을 GeoJSON으로
-- 받아 논리 좌표 위에 스키마틱 박스를 그린다.
--
-- 설계 메모:
-- - (sitename, facilitytype) = tb_facility_flow_map과 동일한 합성 키
-- - parent_node_id: 상위 그룹(선부1 → 연성선부01~08 등) 계층 집계용
-- - group_level: 0=source(정수장/댐) / 1=top 배수지 / 2=중간(가압장/감압) / 3=leaf(소블록)
-- - diagram_x / diagram_y: 논리 좌표 (MapLibreGL fake lng/lat, base map 없이 렌더)
-- - display_from_z / display_to_z: 줌 범위별 가시성 (MapLibreGL layer filter로 변환)

BEGIN;

CREATE TABLE IF NOT EXISTS tb_flow_diagram_node (
    node_id         bigserial PRIMARY KEY,
    sitename        text NOT NULL,
    facilitytype    text NOT NULL,
    parent_node_id  bigint REFERENCES tb_flow_diagram_node(node_id) ON DELETE SET NULL,
    group_level     smallint NOT NULL DEFAULT 2,    -- 0~3
    diagram_x       double precision NOT NULL,
    diagram_y       double precision NOT NULL,
    box_width       double precision NOT NULL DEFAULT 120,
    box_height      double precision NOT NULL DEFAULT 40,
    label_text      text,
    display_from_z  real NOT NULL DEFAULT 10,
    display_to_z    real NOT NULL DEFAULT 22,
    meta            jsonb,
    created_at      timestamp with time zone DEFAULT now(),
    updated_at      timestamp with time zone DEFAULT now(),
    CONSTRAINT uq_flow_diagram_node_site UNIQUE (sitename, facilitytype),
    CONSTRAINT chk_group_level CHECK (group_level BETWEEN 0 AND 3)
);

CREATE INDEX IF NOT EXISTS idx_flow_diagram_node_level
    ON tb_flow_diagram_node (group_level);
CREATE INDEX IF NOT EXISTS idx_flow_diagram_node_parent
    ON tb_flow_diagram_node (parent_node_id);

COMMENT ON TABLE tb_flow_diagram_node IS
    '[E-xxx 다이어그램 모드] 용수흐름도 스키마틱 노드 레이아웃';
COMMENT ON COLUMN tb_flow_diagram_node.group_level IS
    '0=source(정수장/댐), 1=top 배수지, 2=가압장·감압설비, 3=leaf(소블록/소소블록)';
COMMENT ON COLUMN tb_flow_diagram_node.diagram_x IS
    '논리 X좌표 — MapLibreGL fake lng (base map 없이 사용)';
COMMENT ON COLUMN tb_flow_diagram_node.display_from_z IS
    '이 줌 이상에서만 렌더 (LOD 폭발 제어)';

COMMIT;
