-- 0103: 구축 메뉴 개편 (2026-07-19 사용자 요청 11건)
--   통합: 배수지/가압장/감압시설/블록 → 시설정보 구축(M200-17, /setup/facility-info 탭)
--         용수 흐름/캔버스 에디터 → 계통도 설정(M200-18, /setup/diagram 탭)
--         GIS 관리/지도 설정 → GIS 설정(M200-19, /setup/gis-config 탭)
--   명칭: 설비→장비 설정 / 모니터링 설정→트렌드 설정 / 네트워크→네트워크 설정 /
--         인과 규칙→인과규칙 확인 / 비상연락처→비상연락처 설정 /
--         고장 진단 케이스→고장 진단 케이스 설정
--   삭제: 잠금 관리(M200-10) — 미구현 기능(mock UI·백엔드 참조 0·tb_field_lock 0행)
--   원 라우트는 유지되므로 use_yn='N' 처리만 (딥링크·북마크 호환)
-- 롤백:
--   UPDATE tb_menu SET use_yn='Y' WHERE menu_idn IN
--     ('M200-1','M200-2','M200-3','M200-4','M200-9','M200-11','M200-13','M200-16','M200-10');
--   DELETE FROM tb_auth_menu WHERE menu_idn IN ('M200-17','M200-18','M200-19');
--   DELETE FROM tb_menu WHERE menu_idn IN ('M200-17','M200-18','M200-19');
--   UPDATE tb_menu SET menu_nm='설비' WHERE menu_idn='M200-6';
--   UPDATE tb_menu SET menu_nm='모니터링 설정' WHERE menu_idn='M200-7';
--   UPDATE tb_menu SET menu_nm='네트워크' WHERE menu_idn='M200-8';
--   UPDATE tb_menu SET menu_nm='인과 규칙' WHERE menu_idn='M200-12';
--   UPDATE tb_menu SET menu_nm='비상연락처' WHERE menu_idn='M200-14';
--   UPDATE tb_menu SET menu_nm='고장 진단 케이스' WHERE menu_idn='M200-15';

-- 1) 통합 메뉴 신설
INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path, menu_type, menu_idx, use_yn)
VALUES
  ('R01', 'M200-17', '시설정보 구축', 'M200', '/setup/facility-info', 'menu', 1, 'Y'),
  ('R01', 'M200-18', '계통도 설정',   'M200', '/setup/diagram',       'menu', 9, 'Y'),
  ('R01', 'M200-19', 'GIS 설정',      'M200', '/setup/gis-config',    'menu', 13, 'Y')
ON CONFLICT (region, menu_idn) DO UPDATE
  SET menu_nm = EXCLUDED.menu_nm, app_path = EXCLUDED.app_path,
      pmenu_idn = EXCLUDED.pmenu_idn, menu_idx = EXCLUDED.menu_idx, use_yn = 'Y';

-- 권한: 기존 구축 메뉴(GIS 관리 M200-13)와 동일
INSERT INTO tb_auth_menu (region, auth_idn, menu_idn)
SELECT region, auth_idn, m.new_idn
FROM tb_auth_menu, (VALUES ('M200-17'), ('M200-18'), ('M200-19')) AS m(new_idn)
WHERE menu_idn = 'M200-13'
ON CONFLICT DO NOTHING;

-- 2) 통합된 개별 메뉴 + 미구현 잠금 관리 비활성
UPDATE tb_menu SET use_yn = 'N' WHERE menu_idn IN
  ('M200-1','M200-2','M200-3','M200-4',  -- 시설 4종 → 시설정보 구축
   'M200-9','M200-11',                   -- 용수 흐름·캔버스 → 계통도 설정
   'M200-13','M200-16',                  -- GIS 관리·지도 설정 → GIS 설정
   'M200-10');                           -- 잠금 관리 (미구현 삭제)

-- 3) 명칭 변경
UPDATE tb_menu SET menu_nm = '장비 설정'             WHERE menu_idn = 'M200-6';
UPDATE tb_menu SET menu_nm = '트렌드 설정'           WHERE menu_idn = 'M200-7';
UPDATE tb_menu SET menu_nm = '네트워크 설정'         WHERE menu_idn = 'M200-8';
UPDATE tb_menu SET menu_nm = '인과규칙 확인'         WHERE menu_idn = 'M200-12';
UPDATE tb_menu SET menu_nm = '비상연락처 설정'       WHERE menu_idn = 'M200-14';
-- M200-15 는 정적 fallback 에만 있고 tb_menu 에 미등록 상태였음 → 신규 등록
INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path, menu_type, menu_idx, use_yn)
VALUES ('R01', 'M200-15', '고장 진단 케이스 설정', 'M200', '/setup/fault-cases', 'menu', 15, 'Y')
ON CONFLICT (region, menu_idn) DO UPDATE SET menu_nm = EXCLUDED.menu_nm, use_yn = 'Y';
INSERT INTO tb_auth_menu (region, auth_idn, menu_idn)
SELECT region, auth_idn, 'M200-15' FROM tb_auth_menu WHERE menu_idn = 'M200-14'
ON CONFLICT DO NOTHING;
