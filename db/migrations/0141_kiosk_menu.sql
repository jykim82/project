-- 0141_kiosk_menu.sql
-- 상황실 키오스크 모드 메뉴 (docs/kiosk-mode-spec.md)
-- 롤백: 파일 하단 ROLLBACK 블록

BEGIN;

INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path, menu_type, menu_idx, use_yn)
SELECT m.region, 'M003-12', '상황실 모드', 'M003', '/kiosk', 'menu', 12, 'Y'
FROM tb_menu m WHERE m.menu_idn = 'M003'
ON CONFLICT (region, menu_idn) DO NOTHING;

INSERT INTO tb_auth_menu (region, auth_idn, menu_idn)
SELECT DISTINCT a.region, a.auth_idn, 'M003-12'
FROM tb_auth_menu a WHERE a.menu_idn = 'M003-5'
ON CONFLICT DO NOTHING;

COMMIT;

-- =============================================================================
-- ROLLBACK
-- =============================================================================
-- DELETE FROM tb_auth_menu WHERE menu_idn = 'M003-12';
-- DELETE FROM tb_menu WHERE menu_idn = 'M003-12';
