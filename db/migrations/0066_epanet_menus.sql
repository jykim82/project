-- Migration 0066: EPANET 활용 메뉴 9개 + 분석 그룹 신규
-- 사양: docs/epanet-menu-spec.md (Phase 2.7 — 메뉴 뼈대)
--
-- 메뉴 매핑 (실제 ID — 사양 문서의 M030-/M040-/M050- 는 임시 코드, 실제는
-- 기존 그룹 M003 모니터링 / M006 위기대응 + 신규 M008 분석):
--
--   모니터링 (M003) 하위:
--     M003-9   누수 의심 구간       /monitoring/leak-suspicious      Phase 3
--     M003-10  헤드손실 이상 구간   /monitoring/headloss-anomaly    Phase 3
--   위기대응 (M006) 하위:
--     M006-4   차단밸브 영향범위    /crisis/valve-impact            Phase 4
--     M006-5   관로 파손 시뮬       /crisis/pipe-break              Phase 4
--     M006-6   펌프 가동 변경       /crisis/pump-control            Phase 4
--     M006-7   시나리오 비교        /crisis/scenario-diff           Phase 4
--   분석 (M008 신규 그룹):
--     M008     분석 그룹            (group, no path)
--     M008-1   블록 교체 후보       /analysis/replacement-candidates Phase 5
--     M008-2   관망 노후도 평가     /analysis/network-aging         Phase 5
--     M008-3   수질·체류시간        /analysis/water-quality         Phase 6
--
-- 모든 메뉴는 등록되지만 페이지는 placeholder (DataQualityCard + Phase 안내).
--
-- 롤백:
--   DELETE FROM tb_auth_menu WHERE menu_idn IN
--     ('M003-9','M003-10','M006-4','M006-5','M006-6','M006-7',
--      'M008','M008-1','M008-2','M008-3');
--   DELETE FROM tb_menu WHERE menu_idn IN
--     ('M003-9','M003-10','M006-4','M006-5','M006-6','M006-7',
--      'M008-1','M008-2','M008-3','M008');

BEGIN;

-- ============================================================================
-- 1) 모니터링 하위 (M003-9, M003-10)
-- ============================================================================
INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path, menu_type, menu_idx, use_yn)
SELECT region, 'M003-9', '누수 의심 구간', 'M003', '/monitoring/leak-suspicious',
       'menu', 9, 'Y'
FROM tb_menu WHERE menu_idn = 'M003'
ON CONFLICT (region, menu_idn) DO NOTHING;

INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path, menu_type, menu_idx, use_yn)
SELECT region, 'M003-10', '헤드손실 이상 구간', 'M003', '/monitoring/headloss-anomaly',
       'menu', 10, 'Y'
FROM tb_menu WHERE menu_idn = 'M003'
ON CONFLICT (region, menu_idn) DO NOTHING;

-- ============================================================================
-- 2) 위기대응 하위 (M006-4 ~ M006-7)
-- ============================================================================
INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path, menu_type, menu_idx, use_yn)
SELECT region, 'M006-4', '차단밸브 영향범위', 'M006', '/crisis/valve-impact',
       'menu', 4, 'Y'
FROM tb_menu WHERE menu_idn = 'M006'
ON CONFLICT (region, menu_idn) DO NOTHING;

INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path, menu_type, menu_idx, use_yn)
SELECT region, 'M006-5', '관로 파손 시뮬', 'M006', '/crisis/pipe-break',
       'menu', 5, 'Y'
FROM tb_menu WHERE menu_idn = 'M006'
ON CONFLICT (region, menu_idn) DO NOTHING;

INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path, menu_type, menu_idx, use_yn)
SELECT region, 'M006-6', '펌프 가동 변경', 'M006', '/crisis/pump-control',
       'menu', 6, 'Y'
FROM tb_menu WHERE menu_idn = 'M006'
ON CONFLICT (region, menu_idn) DO NOTHING;

INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path, menu_type, menu_idx, use_yn)
SELECT region, 'M006-7', '시나리오 비교', 'M006', '/crisis/scenario-diff',
       'menu', 7, 'Y'
FROM tb_menu WHERE menu_idn = 'M006'
ON CONFLICT (region, menu_idn) DO NOTHING;

-- ============================================================================
-- 3) 분석 그룹 신규 (M008)
-- ============================================================================
INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path, menu_type, menu_idx, use_yn)
SELECT DISTINCT region, 'M008', '분석', NULL, NULL, 'group', 7, 'Y'
FROM tb_menu
ON CONFLICT (region, menu_idn) DO NOTHING;

INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path, menu_type, menu_idx, use_yn)
SELECT region, 'M008-1', '블록 교체 후보', 'M008', '/analysis/replacement-candidates',
       'menu', 1, 'Y'
FROM tb_menu WHERE menu_idn = 'M008'
ON CONFLICT (region, menu_idn) DO NOTHING;

INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path, menu_type, menu_idx, use_yn)
SELECT region, 'M008-2', '관망 노후도 평가', 'M008', '/analysis/network-aging',
       'menu', 2, 'Y'
FROM tb_menu WHERE menu_idn = 'M008'
ON CONFLICT (region, menu_idn) DO NOTHING;

INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path, menu_type, menu_idx, use_yn)
SELECT region, 'M008-3', '수질·체류시간', 'M008', '/analysis/water-quality',
       'menu', 3, 'Y'
FROM tb_menu WHERE menu_idn = 'M008'
ON CONFLICT (region, menu_idn) DO NOTHING;

-- ============================================================================
-- 4) tb_auth_menu — MASTER/ADMIN 권한
-- ============================================================================
INSERT INTO tb_auth_menu (region, auth_idn, menu_idn, use_yn, menu_order)
SELECT m.region, a.auth_idn, m.menu_idn, 'Y',
       CASE m.menu_idn
         WHEN 'M003-9'  THEN 9
         WHEN 'M003-10' THEN 10
         WHEN 'M006-4'  THEN 4
         WHEN 'M006-5'  THEN 5
         WHEN 'M006-6'  THEN 6
         WHEN 'M006-7'  THEN 7
         WHEN 'M008'    THEN 7
         WHEN 'M008-1'  THEN 1
         WHEN 'M008-2'  THEN 2
         WHEN 'M008-3'  THEN 3
         ELSE 99
       END
FROM tb_menu m
CROSS JOIN (VALUES ('MASTER'), ('ADMIN')) AS a(auth_idn)
WHERE m.menu_idn IN
  ('M003-9','M003-10','M006-4','M006-5','M006-6','M006-7',
   'M008','M008-1','M008-2','M008-3')
ON CONFLICT (region, auth_idn, menu_idn) DO NOTHING;

COMMIT;
