-- 0121: 캔버스 에디터 독립 메뉴 제거 (canvas-editor-unification-spec P2)
-- /setup/canvas 는 /setup/diagram?tab=canvas 로 redirect — 진입점은
-- "계통도 설정" 하나로 통일 (M200-18).
--
-- 롤백:
--   INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path, ...)
--   VALUES ('R01', 'M200-11', '캔버스 에디터', <구축 메뉴 idn>, '/setup/canvas', ...);
--   (+ tb_auth_menu 재부여. 실제 롤백 시 0121 적용 전 스냅샷 참조)

DELETE FROM tb_auth_menu WHERE menu_idn = 'M200-11';
DELETE FROM tb_menu WHERE menu_idn = 'M200-11';
