-- 0134_retire_shift_handover.sql
-- 교대 인수인계 메뉴 폐기 (2026-07-28 사용자 결정 — "메뉴 자체 폐기")
--
-- 0129 의 롤백 성격: 메뉴 M005-5 + SHIFT_BOUNDARIES 설정 제거.
-- 화면(/reports/shift-handover)·백엔드(/shift/handover)·API 클라이언트도
-- 코드에서 함께 제거됐다.
--
-- 점검 도래(tb_inspection_cycle, 0130)는 별개 기능이라 유지한다 — 표시처
-- (인수인계 화면 섹션)만 사라졌고, API(/inspection/due)는 남는다.
-- 표시처 재결정은 inspection-cycle-spec 에 기록.
--
-- 롤백(재도입 시): 0129 를 다시 적용

BEGIN;

DELETE FROM tb_auth_menu WHERE menu_idn = 'M005-5';
DELETE FROM tb_menu      WHERE menu_idn = 'M005-5';
DELETE FROM tb_comm_code
 WHERE grp_cd = 'SITE_SETTING' AND comm_cd = 'SHIFT_BOUNDARIES';

COMMIT;
