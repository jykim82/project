-- 0097: 채팅 카드 갤러리 메뉴 등록 (Tier 3 — docs/chat-smoke-test-guide.md)
-- 대표 질의 카드 육안 검수 페이지 /admin/chat-gallery
-- 롤백: DELETE FROM tb_auth_menu WHERE menu_idn='M100-16';
--       DELETE FROM tb_menu WHERE menu_idn='M100-16';

INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path, menu_type, menu_idx, use_yn)
VALUES ('R01', 'M100-16', '채팅 카드 갤러리', 'M100', '/admin/chat-gallery', 'menu', 16, 'Y')
ON CONFLICT (region, menu_idn) DO UPDATE
  SET menu_nm = EXCLUDED.menu_nm, app_path = EXCLUDED.app_path, use_yn = 'Y';

-- 권한: AI 모델 평가(M100-13)와 동일 (ADMIN/MASTER)
INSERT INTO tb_auth_menu (region, auth_idn, menu_idn)
SELECT region, auth_idn, 'M100-16' FROM tb_auth_menu WHERE menu_idn = 'M100-13'
ON CONFLICT DO NOTHING;
