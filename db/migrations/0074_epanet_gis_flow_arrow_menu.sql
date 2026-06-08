-- Migration 0074: GIS 물흐름 표시 토글 menu_key 등록
-- 사양: docs/epanet-menu-spec.md (2026-06-08 추가)
--
-- 배경: GIS 페이지의 "물흐름 표시" 토글 버튼은 2026-05-10 사양상 마스터 토글과
--       분리되어 항상 노출되었으나, 운영자가 사이트 정책에 따라 노출 자체를
--       숨기길 원하는 요구가 발생.
--
-- 해결: EPANET 메뉴 토글 인프라 재사용 — menu_key='gis-flow-arrow' 추가.
--       _MENU_REQUIREMENTS 의 required/recommended 는 비어 있어 항상 ready,
--       enabled='N' 일 때만 토글이 사라짐 (마스터 토글과의 독립성 유지).
--
-- 롤백:
--   DELETE FROM tb_epanet_menu_setting WHERE menu_key='gis-flow-arrow';

BEGIN;

INSERT INTO tb_epanet_menu_setting (region, menu_key, label, enabled, updated_by)
SELECT DISTINCT region, 'gis-flow-arrow', '물흐름 표시 (GIS)', 'Y', 'system'
  FROM tb_epanet_menu_setting
ON CONFLICT (region, menu_key) DO NOTHING;

COMMIT;
