-- Migration 0095: 감사 로그 메뉴 등록 (M100-15)
-- 사양: docs/gis-facility-menu-spec.md §5.3
--
-- 관리 그룹(M100, adminOnly) 하위. /admin/audit-logs. MASTER/ADMIN 접근.
--
-- 롤백:
--   DELETE FROM tb_auth_menu WHERE menu_idn='M100-15';
--   DELETE FROM tb_menu WHERE menu_idn='M100-15';

BEGIN;

INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path,
                     menu_type, menu_idx, use_yn)
SELECT region, 'M100-15', '감사 로그', 'M100',
       '/admin/audit-logs', 'menu', 15, 'Y'
  FROM (SELECT DISTINCT region FROM tb_menu) r
ON CONFLICT (region, menu_idn) DO NOTHING;

INSERT INTO tb_auth_menu (region, auth_idn, menu_idn, use_yn, menu_order)
SELECT region, auth_idn, 'M100-15', 'Y', 15
  FROM (SELECT DISTINCT region, auth_idn FROM tb_auth_menu
         WHERE auth_idn IN ('MASTER', 'ADMIN')) a
ON CONFLICT (region, auth_idn, menu_idn) DO NOTHING;

COMMIT;
