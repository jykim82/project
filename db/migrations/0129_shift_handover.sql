-- 0129_shift_handover.sql
-- 교대 인수인계 브리핑 (docs/shift-handover-spec.md)
--
-- 1) SITE_SETTING.SHIFT_BOUNDARIES — 교대 경계 시각
-- 2) 메뉴 M005-5 — 보고서 그룹에 "교대 인수인계"
--
-- 신규 테이블 없음. 인계에 필요한 데이터(경보·작업·메모·일정)는 모두 이미
-- 쌓이고 있고, 모으는 화면만 없었다.
--
-- 롤백: 파일 하단 ROLLBACK 블록

BEGIN;

-- ─────────────────────────────────────────────────────────────
-- 1) 교대 경계 시각
-- ─────────────────────────────────────────────────────────────
-- 교대 시각은 고객마다 다르다. 하드코딩하지 않고 설정으로 뺀다.
-- 값 = "하루를 자르는 경계 시각" 목록. N 개면 N 교대.
-- 빈 값·파싱 실패 시 백엔드가 24시간 단일 근무로 처리하므로,
-- 교대 개념이 없는 현장에서도 화면은 동작한다.
INSERT INTO tb_comm_code (region, grp_cd, comm_cd, comm_nm, sort_num, use_yn, comm_val)
SELECT r.region, 'SITE_SETTING', 'SHIFT_BOUNDARIES',
       '교대 경계 시각 (쉼표 구분, 비우면 24시간 단일 근무)', 90, 'Y', '08:00,16:00,00:00'
FROM (SELECT DISTINCT region FROM tb_comm_code WHERE grp_cd = 'SITE_SETTING') r
ON CONFLICT (region, grp_cd, comm_cd) DO NOTHING;

-- ─────────────────────────────────────────────────────────────
-- 2) 메뉴 — 보고서 그룹 5번째
-- ─────────────────────────────────────────────────────────────
-- 인수인계는 "읽고 넘기는 산출물"이라 보고서 계열이 맞다.
-- 경보관리에 두면 경보 화면이 되어 장애·메모·일정이 곁다리로 보인다.
INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path, menu_type, menu_idx, use_yn)
SELECT m.region, 'M005-5', '교대 인수인계', 'M005', '/reports/shift-handover', 'menu', 5, 'Y'
FROM tb_menu m WHERE m.menu_idn = 'M005'
ON CONFLICT (region, menu_idn) DO NOTHING;

-- 권한 복제 — 같은 그룹의 기존 메뉴(M005-1) 권한을 그대로 따른다.
-- 신규 메뉴가 아무에게도 안 보이는 사고를 막는다.
INSERT INTO tb_auth_menu (region, auth_idn, menu_idn)
SELECT DISTINCT a.region, a.auth_idn, 'M005-5'
FROM tb_auth_menu a
WHERE a.menu_idn = 'M005-1'
ON CONFLICT DO NOTHING;

COMMIT;

-- 확인
-- SELECT region, menu_idn, menu_nm, app_path, menu_idx
--   FROM tb_menu WHERE pmenu_idn = 'M005' ORDER BY region, menu_idx;
-- SELECT region, comm_cd, comm_val
--   FROM tb_comm_code WHERE comm_cd = 'SHIFT_BOUNDARIES';

-- =============================================================================
-- ROLLBACK
-- =============================================================================
-- BEGIN;
-- DELETE FROM tb_auth_menu WHERE menu_idn = 'M005-5';
-- DELETE FROM tb_menu      WHERE menu_idn = 'M005-5';
-- DELETE FROM tb_comm_code WHERE grp_cd = 'SITE_SETTING' AND comm_cd = 'SHIFT_BOUNDARIES';
-- COMMIT;
