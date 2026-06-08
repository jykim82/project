-- Migration 0076: EPANET 관리 페이지 (M100-12) menu_key 추가
-- 사양: docs/feature-sku-spec.md §3.2 (사용자 요구 2026-06-08)
--
-- 배경: 마스터 OFF 시 사이드바의 /admin/epanet (M100-12) 도 함께 hide.
--       복구 경로는 /admin/site-settings 의 관망수리분석 토글 (항상 노출).
--
-- 동작: menu_key='epanet-admin' 추가 → _check_data_quality 의 마스터 OFF
--       분기에서 menus_disabled 에 자동 포함 (gis-flow-arrow 제외 정책 유지).
--       프론트 MENU_DATA_QUALITY_KEY['M100-12']='epanet-admin' 매핑 →
--       사이드바 statusOf 자동 hide.
--
-- 롤백: DELETE FROM tb_epanet_menu_setting WHERE menu_key='epanet-admin';

BEGIN;

INSERT INTO tb_epanet_menu_setting (region, menu_key, label, enabled, updated_by)
SELECT DISTINCT region, 'epanet-admin', 'EPANET 시뮬레이션 (관리)', 'Y', 'system'
  FROM tb_epanet_menu_setting
ON CONFLICT (region, menu_key) DO NOTHING;

COMMIT;
