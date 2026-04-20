-- 0054_menu_trend_reorg.sql
-- 메뉴 트리 개편: 모니터링 하위 배수지/가압장/블록 → 트렌드 하위로 이동.
-- 기존 트렌드(/trend) 는 "사용자 트렌드" 로 이름 변경 후 트렌드 그룹의
-- 하위 메뉴로 편입. 트렌드 자체는 그룹 노드(app_path=NULL) 가 된다.
--
-- 이유: 트렌드·모니터링 경계가 모호했던 배수지/가압장/블록 트렌드 관련
-- 페이지를 트렌드 그룹 아래로 모으고, 사용자 정의 트렌드는 별도 하위로
-- 분리하여 탐색성 향상.
--
-- 롤백: 아래 변경은 모두 UPDATE 기반이라 수동 되돌림 가능.
--   UPDATE tb_menu SET pmenu_idn='M003' WHERE menu_idn IN ('M003-1','M003-2','M003-3');
--   UPDATE tb_menu SET app_path='/trend', menu_nm='트렌드' WHERE menu_idn='M004';
--   DELETE FROM tb_menu WHERE menu_idn='M004-USR';

BEGIN;

-- 1) 배수지·가압장·블록을 트렌드 하위로 이동 (menu_idn 은 유지,
--    pmenu_idn 만 변경 — tb_auth_menu 등 참조 보존).
UPDATE tb_menu
SET pmenu_idn = 'M004'
WHERE menu_idn IN ('M003-1', 'M003-2', 'M003-3');

-- 2) 기존 트렌드(M004) 를 그룹 노드로 변환.
--    app_path 제거 + 메뉴 이름 유지. 하위로 새 "사용자 트렌드" 추가.
UPDATE tb_menu
SET app_path = NULL
WHERE menu_idn = 'M004';

-- 3) "사용자 트렌드" 신규 메뉴 — 기존 /trend 경로를 이어받음.
--    region 은 기본 R01 가정. 다른 region 이 있으면 수동 추가 필요.
INSERT INTO tb_menu (region, menu_idn, pmenu_idn, menu_nm, app_path, menu_type, menu_idx, use_yn)
SELECT region, 'M004-USR', 'M004', '사용자 트렌드', '/trend', 'page', 99, 'Y'
FROM (SELECT DISTINCT region FROM tb_menu WHERE menu_idn = 'M004') r
ON CONFLICT (region, menu_idn) DO NOTHING;

-- 3.1) 기존 M004 권한을 가진 (region, auth_idn) 들에 M004-USR 권한 동일 부여.
--      (사용자 트렌드는 기존 트렌드와 동일 권한으로 접근 가능해야 함.)
INSERT INTO tb_auth_menu (region, auth_idn, menu_idn)
SELECT region, auth_idn, 'M004-USR'
FROM tb_auth_menu
WHERE menu_idn = 'M004'
ON CONFLICT DO NOTHING;

-- 4) 새 트렌드 하위 메뉴 순서 조정 — 기존 menu_idx 유지(1,2,3) + 사용자 트렌드 끝.
--    배수지/가압장/블록은 이미 idx 1/2/3 이라 변경 불필요.

-- 5) 모니터링 (M003) 는 남은 하위 (용수 흐름~설비 건강성) 그대로.
--    menu_idx 1~3 비게 되지만 렌더링엔 문제 없음.

COMMIT;
