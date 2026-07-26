-- 0125_setup_menu_cleanup.sql
-- 구축 메뉴 중복 정리 (setup-workspace-spec P1)
--
-- 배경: 2026-07-19 개편 이후 통합 페이지(탭)가 개별 페이지를 그대로 품고 있는데
--       tb_menu 에는 개별 메뉴 행이 남아 있어, DB 기반 메뉴(권한·검수·감사)와
--       화면(정적 sidebar-menus.ts, 이미 11개)이 불일치했다.
--
--       M200-1 배수지        → M200-17 시설정보 구축 탭
--       M200-2 가압장        → M200-17 탭
--       M200-3 감압시설      → M200-17 탭
--       M200-4 블록          → M200-17 탭
--       M200-9 용수 흐름     → M200-18 계통도 설정 탭
--       M200-12 인과규칙 확인 → M200-18 계통도 설정 탭 (본 마이그레이션에서 신규 흡수)
--       M200-13 GIS 관리     → M200-19 GIS 설정 탭
--       M200-16 지도 설정    → M200-19 GIS 설정 탭
--
--       추가로 M200-10 '잠금 관리'(/setup/field-locks)는 라우트·백엔드가 모두
--       존재하지 않는 죽은 메뉴 — 클릭 시 404. 컬럼 잠금은 폼 내부 기능으로
--       구현돼 있어 별도 화면이 없다. 함께 제거.
--
-- 원 라우트(/setup/reservoir 등)는 살려 둔다 — 통합 페이지가 그 컴포넌트를
-- import 하고 있고, 북마크·딥링크 호환도 유지된다. 삭제 대상은 "메뉴 행"뿐.
--
-- 롤백: 아래 ROLLBACK 블록 참조 (menu 행 재삽입 + 권한 재부여)

BEGIN;

-- 권한 매핑 먼저 (FK 순서)
DELETE FROM tb_auth_menu
 WHERE menu_idn IN ('M200-1','M200-2','M200-3','M200-4','M200-9','M200-12','M200-13','M200-16','M200-10');

DELETE FROM tb_menu
 WHERE menu_idn IN ('M200-1','M200-2','M200-3','M200-4','M200-9','M200-12','M200-13','M200-16','M200-10');

COMMIT;

-- 검증
--   SELECT menu_idn, menu_nm, app_path FROM tb_menu WHERE pmenu_idn='M200' ORDER BY menu_idn;
--   → 10행 + M100-8(시설 약칭) (M200-5,6,7,8,14,15,17,18,19,21)

-- ---------------------------------------------------------------------------
-- ROLLBACK (필요 시 수동 실행)
-- ---------------------------------------------------------------------------
-- INSERT INTO tb_menu (menu_idn, pmenu_idn, menu_nm, app_path) VALUES
--   ('M200-1','M200','배수지','/setup/reservoir'),
--   ('M200-2','M200','가압장','/setup/booster'),
--   ('M200-3','M200','감압시설','/setup/pressure'),
--   ('M200-4','M200','블록','/setup/block'),
--   ('M200-9','M200','용수 흐름','/setup/flow-map'),
--   ('M200-12','M200','인과규칙 확인','/setup/causal-rules'),
--   ('M200-13','M200','GIS 관리','/setup/gis'),
--   ('M200-16','M200','지도 설정','/setup/map-config'),
--   ('M200-10','M200','잠금 관리','/setup/field-locks');
-- INSERT INTO tb_auth_menu (auth_idn, menu_idn)
--   SELECT a.auth_idn, m.menu_idn FROM tb_auth_info a
--   CROSS JOIN (VALUES ('M200-1'),('M200-2'),('M200-3'),('M200-4'),('M200-9'),
--                      ('M200-12'),('M200-13'),('M200-16'),('M200-10')) AS m(menu_idn)
--   WHERE a.auth_idn = 'A001';
