-- Migration 0072: EPANET 실측 유량 차이 분석 (B-2) 메뉴 등록
-- 사양: docs/epanet-flow-deviation-spec.md §6
--
-- M008-4 (분석 그룹 4번째). 데이터 품질 게이트 = HAS_PIPE_NETWORK + HAS_LIVE_FLOW
-- 마스터 토글 등록 = tb_epanet_menu_setting.menu_key='flow-deviation'
--
-- 롤백:
--   DELETE FROM tb_menu WHERE menu_idn='M008-4';
--   DELETE FROM tb_epanet_menu_setting WHERE menu_key='flow-deviation';

BEGIN;

INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path,
                     menu_type, menu_idx, use_yn)
SELECT region, 'M008-4', '실측 유량 차이', 'M008',
       '/monitoring/flow-deviation', 'menu', 4, 'Y'
  FROM (SELECT DISTINCT region FROM tb_menu) r
ON CONFLICT (region, menu_idn) DO NOTHING;

INSERT INTO tb_epanet_menu_setting (region, menu_key, label, enabled, updated_by)
SELECT DISTINCT region, 'flow-deviation', '실측 유량 차이', 'Y', 'system'
  FROM tb_epanet_menu_setting
ON CONFLICT (region, menu_key) DO NOTHING;

COMMIT;
