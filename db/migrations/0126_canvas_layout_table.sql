-- 0126_canvas_layout_table.sql
-- 캔버스 배치 좌표를 자산 대장에서 분리 (setup-workspace-spec §5, [E-054] 근본 제거)
--
-- 배경:
--   링크 에디터의 노드 배치가 tb_network_info.meta.canvas_pos 에 저장돼 왔다.
--   tb_network_info 는 통신 장비 "대장"(equipment_id·IP·설명)이라, 화면 배치를
--   저장하려고 UPSERT 하는 순간 대장에 빈 행이 생긴다 — 자동 정렬 1회로
--   대장이 180 → 295 로 늘어난 것이 E-054.
--
--   반면 계통도(용수 계통) 좌표는 tb_flow_diagram_node.diagram_x/y 에 있는데,
--   그 테이블은 애초에 "다이어그램 테이블"(box_width/height·label_text·표시 줌
--   범위)이라 좌표가 거기 있는 것이 정상이다 — 대장 오염이 아니다.
--
--   따라서 통일 원칙은 "좌표를 하나의 테이블로 몰기"가 아니라
--   **"좌표를 자산 대장에 저장하지 않는다"** 이다. 통신망 좌표만 분리한다.
--
-- 범위: layer='network' (node_key = equipment_id).
--   계통도는 tb_flow_diagram_node 유지 (런타임 계통도 렌더가 직접 읽으며,
--   옮길 실익이 없다). 향후 다른 레이어가 필요하면 layer 값만 추가한다.
--
-- region: 부모 테이블(tb_network_info·tb_equipment_info)이 아직 region 컬럼이
--   없으므로 동일하게 두지 않는다. 부모에 region 이 도입되면 같은
--   마이그레이션에서 여기에도 추가할 것.
--
-- 롤백: 파일 하단 ROLLBACK 블록

BEGIN;

CREATE TABLE IF NOT EXISTS tb_canvas_layout (
    layer      varchar(20)      NOT NULL,   -- 'network' (향후 확장 가능)
    node_key   varchar(64)      NOT NULL,   -- network: equipment_id
    x          double precision NOT NULL,
    y          double precision NOT NULL,
    updated_at timestamptz      NOT NULL DEFAULT now(),
    PRIMARY KEY (layer, node_key)
);

COMMENT ON TABLE tb_canvas_layout IS
  '캔버스 노드 배치 좌표 — 표시 전용. 자산 대장(tb_network_info)에 좌표를 넣지 않기 위한 분리 테이블';

-- 기존 좌표 이관
INSERT INTO tb_canvas_layout (layer, node_key, x, y)
SELECT 'network',
       equipment_id,
       (meta->'canvas_pos'->>'x')::double precision,
       (meta->'canvas_pos'->>'y')::double precision
  FROM tb_network_info
 WHERE meta ? 'canvas_pos'
   AND (meta->'canvas_pos'->>'x') IS NOT NULL
   AND (meta->'canvas_pos'->>'y') IS NOT NULL
ON CONFLICT (layer, node_key) DO NOTHING;

-- 대장에서 좌표 키 제거 (gateway·subnet_mask 등 다른 meta 키는 보존)
UPDATE tb_network_info
   SET meta = meta - 'canvas_pos',
       updated_at = now()
 WHERE meta ? 'canvas_pos';

COMMIT;

-- 검증
--   SELECT count(*) FROM tb_canvas_layout WHERE layer='network';
--   SELECT count(*) FROM tb_network_info WHERE meta ? 'canvas_pos';  -- 0 이어야 함

-- ---------------------------------------------------------------------------
-- 선택 정리 (수동 판단 — 자동 실행하지 않음)
-- ---------------------------------------------------------------------------
-- 과거 배치 저장으로 생긴 "정보가 전혀 없는" 대장 행이 남아 있을 수 있다.
-- 운영자가 나중에 채우려고 만든 행일 수도 있으므로 확인 후 수동 실행할 것.
--   SELECT equipment_id FROM tb_network_info
--    WHERE ip_address IS NULL AND description IS NULL
--      AND COALESCE(meta, '{}'::jsonb) = '{}'::jsonb;
--   DELETE FROM tb_network_info
--    WHERE ip_address IS NULL AND description IS NULL
--      AND COALESCE(meta, '{}'::jsonb) = '{}'::jsonb;

-- ---------------------------------------------------------------------------
-- ROLLBACK (필요 시 수동 실행)
-- ---------------------------------------------------------------------------
-- BEGIN;
-- UPDATE tb_network_info n
--    SET meta = COALESCE(n.meta, '{}'::jsonb)
--             || jsonb_build_object('canvas_pos',
--                    jsonb_build_object('x', l.x, 'y', l.y))
--   FROM tb_canvas_layout l
--  WHERE l.layer = 'network' AND l.node_key = n.equipment_id;
-- DROP TABLE tb_canvas_layout;
-- COMMIT;
