-- Migration 0061: 관리 그룹에 "보고서 카테고리" 메뉴 등록
-- 사양: docs/report-spec.md §3.5 (관리 페이지에서 카테고리 CRUD)
--
-- 0060 에서 tb_report_category + 페이지(/admin/report-categories) 만 추가하고
-- tb_menu 등록을 빠뜨려 관리 그룹에 노출되지 않는 문제 보완.
-- (정적 sidebar-menus.ts fallback 만으론 인증 후 동적 메뉴에 안 나옴)
--
-- 롤백:
--   DELETE FROM tb_auth_menu WHERE menu_idn = 'M100-11';
--   DELETE FROM tb_menu      WHERE menu_idn = 'M100-11';

BEGIN;

-- 1) 메뉴 등록 — 모든 region 적용
INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path, menu_type, menu_idx, use_yn)
SELECT region, 'M100-11', '보고서 카테고리', 'M100', '/admin/report-categories',
       'menu', 11, 'Y'
FROM tb_menu
WHERE menu_idn = 'M100'
ON CONFLICT (region, menu_idn) DO NOTHING;

-- 2) MASTER/ADMIN 권한에 접근 부여
INSERT INTO tb_auth_menu (region, auth_idn, menu_idn, use_yn, menu_order)
SELECT m.region, a.auth_idn, 'M100-11', 'Y', 11
FROM tb_menu m
CROSS JOIN (VALUES ('MASTER'), ('ADMIN')) AS a(auth_idn)
WHERE m.menu_idn = 'M100-11'
ON CONFLICT (region, auth_idn, menu_idn) DO NOTHING;

COMMIT;
