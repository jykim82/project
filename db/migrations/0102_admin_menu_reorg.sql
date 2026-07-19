-- 0102: 관리 메뉴 정비 — 명칭 정규화 + 소속 그룹 이동 (2026-07-19 사용자 요청)
--   1) 사용자 → 사용자 관리 / 메뉴 → 메뉴 관리 / FAQ → FAQ 관리
--   2) 시설 약칭(M100-8): 관리(M100) → 구축(M200, 지도 설정 다음)
--   3) 설비 신뢰성(M100-9): 관리(M100) → 모니터링(M003, 설비 건강성 다음)
-- 롤백:
--   UPDATE tb_menu SET menu_nm='사용자' WHERE menu_idn='M100-1';
--   UPDATE tb_menu SET menu_nm='메뉴' WHERE menu_idn='M100-2';
--   UPDATE tb_menu SET menu_nm='FAQ' WHERE menu_idn='M100-4';
--   UPDATE tb_menu SET pmenu_idn='M100', menu_idx=8 WHERE menu_idn='M100-8';
--   UPDATE tb_menu SET pmenu_idn='M100', menu_idx=9 WHERE menu_idn='M100-9';
--   UPDATE tb_menu SET menu_idx=menu_idx-1 WHERE pmenu_idn='M003' AND menu_idx>=10;

-- 1) 명칭 정규화
UPDATE tb_menu SET menu_nm = '사용자 관리' WHERE menu_idn = 'M100-1';
UPDATE tb_menu SET menu_nm = '메뉴 관리'   WHERE menu_idn = 'M100-2';
UPDATE tb_menu SET menu_nm = 'FAQ 관리'    WHERE menu_idn = 'M100-4';

-- 2) 시설 약칭 → 구축 (지도 설정 M200-16 다음)
UPDATE tb_menu SET pmenu_idn = 'M200', menu_idx = 17 WHERE menu_idn = 'M100-8';

-- 3) 설비 신뢰성 → 모니터링 (설비 건강성 M003-8 다음)
UPDATE tb_menu SET menu_idx = menu_idx + 1 WHERE pmenu_idn = 'M003' AND menu_idx >= 9;
UPDATE tb_menu SET pmenu_idn = 'M003', menu_idx = 9 WHERE menu_idn = 'M100-9';
