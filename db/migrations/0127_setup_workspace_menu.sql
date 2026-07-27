-- 0127_setup_workspace_menu.sql
-- 구축 워크스페이스 메뉴 등록 (setup-workspace-spec P2-b)
--
-- 시설→설비→태그를 한 화면에서 관통하는 통합 진입점.
-- 기존 개별 화면(시설정보 구축·네트워크 설정·계통도 설정)은 그대로 둔다 —
-- 워크스페이스는 "한 시설을 끝까지", 표 화면은 "여러 건을 한 번에" 담당.
--
-- PK 가 (region, menu_idn) 이므로 기존 구축 메뉴(M200-17)가 있는 region 마다
-- 복제한다 — 멀티테넌시에서 특정 region 만 누락되지 않도록.
--
-- 롤백: 파일 하단

BEGIN;

INSERT INTO tb_menu (region, menu_idn, pmenu_idn, menu_nm, app_path, menu_type, menu_idx, use_yn)
SELECT region, 'M200-22', 'M200', '구축 워크스페이스', '/setup/workspace', 'menu', 0, 'Y'
  FROM tb_menu
 WHERE menu_idn = 'M200-17'
ON CONFLICT (region, menu_idn) DO UPDATE
   SET menu_nm   = EXCLUDED.menu_nm,
       pmenu_idn = EXCLUDED.pmenu_idn,
       app_path  = EXCLUDED.app_path,
       use_yn    = 'Y',
       updated_at = now();

-- 구축 메뉴를 볼 수 있는 권한에 동일하게 부여 (M200-17 기준 복제)
INSERT INTO tb_auth_menu (region, auth_idn, menu_idn, use_yn, menu_order)
SELECT region, auth_idn, 'M200-22', use_yn, menu_order
  FROM tb_auth_menu
 WHERE menu_idn = 'M200-17'
ON CONFLICT (region, auth_idn, menu_idn) DO NOTHING;

COMMIT;

-- 검증
--   SELECT region, menu_idn, menu_nm, app_path FROM tb_menu WHERE menu_idn='M200-22';
--   SELECT count(*) FROM tb_auth_menu WHERE menu_idn='M200-22';  -- M200-17 과 동수

-- ---------------------------------------------------------------------------
-- ROLLBACK
-- ---------------------------------------------------------------------------
-- DELETE FROM tb_auth_menu WHERE menu_idn = 'M200-22';
-- DELETE FROM tb_menu WHERE menu_idn = 'M200-22';
