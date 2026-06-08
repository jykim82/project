-- Migration 0075: EPANET (관망수리분석) 기본 비활성화 정책 적용
-- 사양: docs/feature-sku-spec.md §3 (Phase 1)
--
-- 배경: 관망수리분석은 관로 SHP/표고/수요 등 사이트별 데이터 구축이 전제.
--       데이터 미구축 사이트는 메뉴가 보여도 의미 없음. 따라서 기본 OFF.
--       master 가 명시적으로 활성화 한 경우에만 노출.
--
-- 영향: tb_comm_code(SITE_SETTING.EPANET_ENABLED) 의 모든 region row 를
--       use_yn='N' 으로 reset. 이전에 'Y' 였던 region (예: R01 개발/테스트)
--       도 함께 reset. 활성화 필요 시 /admin/site-settings 에서 master 가
--       명시적으로 ON.
--
-- 신규 region: tb_comm_code row 없으면 epanet.is_enabled() 가 False 반환
--             (안전). 별도 시드 INSERT 불필요.
--
-- 롤백:
--   UPDATE tb_comm_code SET use_yn='Y'
--    WHERE grp_cd='SITE_SETTING' AND comm_cd='EPANET_ENABLED';

BEGIN;

UPDATE tb_comm_code
   SET use_yn = 'N'
 WHERE grp_cd = 'SITE_SETTING'
   AND comm_cd = 'EPANET_ENABLED';

COMMIT;
