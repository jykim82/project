-- 0104: 시설 약칭 → 시설 약칭 설정 (구축 메뉴 개편 후속, 2026-07-19)
-- 롤백: UPDATE tb_menu SET menu_nm='시설 약칭' WHERE menu_idn='M100-8';

UPDATE tb_menu SET menu_nm = '시설 약칭 설정' WHERE menu_idn = 'M100-8';
