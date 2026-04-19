-- ============================================================================
-- migration 0049: tb_menu 에 "설비 건강성" (M003-8) 등록
-- ============================================================================
-- 목적: /monitoring/equipment-health 페이지가 이미 구현돼 있으나 tb_menu 미등록
--       으로 사이드바 동적 메뉴(/auth/me)에 노출되지 않음. 이 마이그레이션으로
--       모든 region 에 M003-8 + MASTER/ADMIN 권한 매핑 추가.
-- ============================================================================

-- 1. tb_menu 에 M003-8 (설비 건강성) 추가 — 모든 region 적용
INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path, menu_type, menu_idx, use_yn)
SELECT region, 'M003-8', '설비 건강성', 'M003', '/monitoring/equipment-health',
       'menu', 8, 'Y'
FROM tb_menu
WHERE menu_idn = 'M003'
ON CONFLICT (region, menu_idn) DO NOTHING;

-- 2. MASTER/ADMIN 권한에 M003-8 접근 부여 (USER 는 수동 부여)
INSERT INTO tb_auth_menu (region, auth_idn, menu_idn, use_yn, menu_order)
SELECT m.region, a.auth_idn, 'M003-8', 'Y', 8
FROM tb_menu m
CROSS JOIN (VALUES ('MASTER'), ('ADMIN')) AS a(auth_idn)
WHERE m.menu_idn = 'M003-8'
ON CONFLICT (region, auth_idn, menu_idn) DO NOTHING;
