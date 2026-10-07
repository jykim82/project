-- 0143_remove_incident_field_siteknowledge.sql
-- 기능 제거 3종 (2026-10-07 사용자 결정) — 교대 인수인계 폐기(0134) 전례 준용
--   ① 상황보고 1·2보 (M005-6, /reports/incident — incident-report-spec)
--   ② 현장 모드     (M009,   /field          — field-mode-spec)
--   ③ 현장 지식     (M006-8, /crisis/site-knowledge — site-knowledge-spec)
-- 데이터 테이블(tb_incident_report·tb_site_knowledge 계열)은 보존 — 이력
-- 데이터이며 복귀 시 재사용. 메뉴·권한만 제거한다.
-- 롤백: 파일 하단 ROLLBACK 블록

BEGIN;

DELETE FROM tb_auth_menu WHERE menu_idn IN ('M005-6', 'M009', 'M006-8');
DELETE FROM tb_menu      WHERE menu_idn IN ('M005-6', 'M009', 'M006-8');

COMMIT;

-- =============================================================================
-- ROLLBACK (삭제 시점 실제 행 — 코드 복원은 git revert 병행 필요)
-- =============================================================================
-- INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path, menu_type, menu_idx, use_yn) VALUES
--   ('R01', 'M005-6', '상황보고 (1·2보)', 'M005', '/reports/incident',      'menu', 6, 'Y'),
--   ('R01', 'M009',   '현장 모드',        NULL,   '/field',                 'menu', 8, 'Y'),
--   ('R01', 'M006-8', '현장 지식',        'M006', '/crisis/site-knowledge', 'menu', 8, 'Y');
-- INSERT INTO tb_auth_menu (region, auth_idn, menu_idn) VALUES
--   ('R01','ADMIN','M005-6'), ('R01','MASTER','M005-6'),
--   ('R01','ADMIN','M006-8'), ('R01','MASTER','M006-8'), ('R01','USER','M006-8'),
--   ('R01','ADMIN','M009'),   ('R01','MASTER','M009'),   ('R01','USER','M009');
