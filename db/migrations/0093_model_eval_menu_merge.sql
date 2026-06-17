-- Migration 0093: 트렌드/IForest 모델 평가 메뉴를 "AI 모델 평가" 로 통합
-- 사양: docs/trend-baseline-gbt-spec.md §6.3, docs/iforest-eval-spec.md §7
--
-- M100-13(트렌드 모델 평가, /admin/baseline-eval)을 "AI 모델 평가"
-- (/admin/model-eval)로 재지정하고, M100-14(IForest 모델 평가)를 제거한다.
-- 통합 페이지에서 모델을 선택해 전환한다. 구 라우트는 프런트에서 redirect.
--
-- 롤백 (0086/0092 상태 복원):
--   BEGIN;
--   UPDATE tb_menu SET menu_nm='트렌드 모델 평가', app_path='/admin/baseline-eval'
--    WHERE menu_idn='M100-13';
--   INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path,
--                        menu_type, menu_idx, use_yn)
--   SELECT region, 'M100-14', 'IForest 모델 평가', 'M100',
--          '/admin/iforest-eval', 'menu', 14, 'Y'
--     FROM (SELECT DISTINCT region FROM tb_menu) r
--   ON CONFLICT (region, menu_idn) DO NOTHING;
--   INSERT INTO tb_auth_menu (region, auth_idn, menu_idn, use_yn, menu_order)
--   SELECT region, auth_idn, 'M100-14', 'Y', 14
--     FROM (SELECT DISTINCT region, auth_idn FROM tb_auth_menu
--            WHERE auth_idn IN ('MASTER','ADMIN')) a
--   ON CONFLICT (region, auth_idn, menu_idn) DO NOTHING;
--   COMMIT;

BEGIN;

UPDATE tb_menu
   SET menu_nm = 'AI 모델 평가',
       app_path = '/admin/model-eval'
 WHERE menu_idn = 'M100-13';

DELETE FROM tb_auth_menu WHERE menu_idn = 'M100-14';
DELETE FROM tb_menu WHERE menu_idn = 'M100-14';

COMMIT;
