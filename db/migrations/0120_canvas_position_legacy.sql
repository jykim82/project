-- 0120: 캔버스 좌표 정본 일원화 (docs/canvas-editor-unification-spec.md §6)
-- 캔버스 에디터가 tb_flow_diagram_node(경위도 정본)를 직접 읽고 쓰도록 전환.
-- tb_canvas_node_position 은 rename 백업 — 관찰 기간(다음 납품 점검) 후 DROP.
--
-- 롤백:
--   ALTER TABLE tb_canvas_node_position_legacy RENAME TO tb_canvas_node_position;
--   (+ slm/frontend 해당 커밋 revert. 배치 정본은 tb_flow_diagram_node 에
--    남아 있어 데이터 손실 없음)

ALTER TABLE IF EXISTS tb_canvas_node_position
    RENAME TO tb_canvas_node_position_legacy;
