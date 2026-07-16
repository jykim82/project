-- 0099: 현장 모드 메뉴 등록 (docs/field-mode-spec.md)
-- 모바일 우선 현장 작업자 홈 /field — 사진 진단·음성 기록·진행중 장애 런처
-- 롤백: DELETE FROM tb_auth_menu WHERE menu_idn='M009';
--       DELETE FROM tb_menu WHERE menu_idn='M009';

INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path, menu_type, menu_idx, use_yn)
VALUES ('R01', 'M009', '현장 모드', NULL, '/field', 'menu', 8, 'Y')
ON CONFLICT (region, menu_idn) DO UPDATE
  SET menu_nm = EXCLUDED.menu_nm, app_path = EXCLUDED.app_path, use_yn = 'Y';

-- 권한: AI 채팅(M002)과 동일 — 현장 작업자 포함 전 사용자
INSERT INTO tb_auth_menu (region, auth_idn, menu_idn)
SELECT region, auth_idn, 'M009' FROM tb_auth_menu WHERE menu_idn = 'M002'
ON CONFLICT DO NOTHING;
