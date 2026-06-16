-- Migration 0086: 트렌드 모델 평가 메뉴 등록
-- 사양: docs/trend-baseline-gbt-spec.md §6.3
--
-- M100-13 (관리 그룹 13번째). GBT baseline 성능 지표 조회 뷰 (/admin/baseline-eval).
-- 관리 그룹은 adminOnly → MASTER/ADMIN 만 접근.
--
-- 롤백:
--   DELETE FROM tb_auth_menu WHERE menu_idn='M100-13';
--   DELETE FROM tb_menu WHERE menu_idn='M100-13';

BEGIN;

INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path,
                     menu_type, menu_idx, use_yn)
SELECT region, 'M100-13', '트렌드 모델 평가', 'M100',
       '/admin/baseline-eval', 'menu', 13, 'Y'
  FROM (SELECT DISTINCT region FROM tb_menu) r
ON CONFLICT (region, menu_idn) DO NOTHING;

INSERT INTO tb_auth_menu (region, auth_idn, menu_idn, use_yn, menu_order)
SELECT region, auth_idn, 'M100-13', 'Y', 13
  FROM (SELECT DISTINCT region, auth_idn FROM tb_auth_menu
         WHERE auth_idn IN ('MASTER', 'ADMIN')) a
ON CONFLICT (region, auth_idn, menu_idn) DO NOTHING;

COMMIT;
