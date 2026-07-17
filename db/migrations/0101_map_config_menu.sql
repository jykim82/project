-- 0101: 구축 > 지도 설정 메뉴 (docs/operations/offline-map-bundle.md §UI)
-- 관할(고객사) 지도 지정·교체 — 중심/줌 + 베이스맵 pmtiles + 레이어 zip 업로드
-- 롤백: DELETE FROM tb_auth_menu WHERE menu_idn='M200-16';
--       DELETE FROM tb_menu WHERE menu_idn='M200-16';

INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path, menu_type, menu_idx, use_yn)
VALUES ('R01', 'M200-16', '지도 설정', 'M200', '/setup/map-config', 'menu', 16, 'Y')
ON CONFLICT (region, menu_idn) DO UPDATE
  SET menu_nm = EXCLUDED.menu_nm, app_path = EXCLUDED.app_path, use_yn = 'Y';

-- 권한: 구축 GIS 관리(M200-13)와 동일 (관리자)
INSERT INTO tb_auth_menu (region, auth_idn, menu_idn)
SELECT region, auth_idn, 'M200-16' FROM tb_auth_menu WHERE menu_idn = 'M200-13'
ON CONFLICT DO NOTHING;
