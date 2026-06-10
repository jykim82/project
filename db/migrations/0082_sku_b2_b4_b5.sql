-- Migration 0082: SKU 모듈 B2 / B4 / B5 마스터 토글
-- 사양: docs/feature-sku-spec.md §6
--
-- B2 Vision Agent (멀티모달 사진 진단) — GPU 필수, 모델 다운로드 19GB
-- B4 트렌드 비교 분석 — 14일+ 히스토리 필요
-- B5 보고서 자동 생성 — 자체 양식 사용 고객 위해 분리
--
-- 기본값:
-- - B2: 'N' — GPU 의존, 데이터 미구축 사이트 비활성
-- - B4: 'Y' — 데이터 누적 자동 활성 (기존 코드 영향 적음)
-- - B5: 'Y' — 기본 운영 모듈 (작업·점검 기록 누적이 전제)
--
-- 롤백:
--   DELETE FROM tb_comm_code WHERE grp_cd='SITE_SETTING'
--    AND comm_cd IN ('VISION_AGENT_ENABLED','TREND_COMPARISON_ENABLED','REPORT_AUTOMATION_ENABLED');

BEGIN;

INSERT INTO tb_comm_code (region, grp_cd, comm_cd, comm_nm, sort_num, use_yn)
SELECT DISTINCT region, 'SITE_SETTING', 'VISION_AGENT_ENABLED',
       'AI 멀티모달 진단 (B2)', 50, 'N'
  FROM tb_comm_code WHERE grp_cd='SITE_SETTING'
ON CONFLICT (region, grp_cd, comm_cd) DO NOTHING;

INSERT INTO tb_comm_code (region, grp_cd, comm_cd, comm_nm, sort_num, use_yn)
SELECT DISTINCT region, 'SITE_SETTING', 'TREND_COMPARISON_ENABLED',
       '트렌드 비교 분석 (B4)', 60, 'Y'
  FROM tb_comm_code WHERE grp_cd='SITE_SETTING'
ON CONFLICT (region, grp_cd, comm_cd) DO NOTHING;

INSERT INTO tb_comm_code (region, grp_cd, comm_cd, comm_nm, sort_num, use_yn)
SELECT DISTINCT region, 'SITE_SETTING', 'REPORT_AUTOMATION_ENABLED',
       '보고서 자동 생성 (B5)', 70, 'Y'
  FROM tb_comm_code WHERE grp_cd='SITE_SETTING'
ON CONFLICT (region, grp_cd, comm_cd) DO NOTHING;

COMMIT;
