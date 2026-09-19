-- 0142_kiosk_p2_settings.sql
-- 상황실 키오스크 모드 P2 사이트 설정 (docs/kiosk-mode-spec.md §5)
--  KIOSK_VIEWS      : 순환 화면 목록·순서 (comm_val = 콤마 구분 키, use_yn 항상 Y)
--  KIOSK_NIGHT_DIM  : 야간 밝기 스케줄 (comm_val = "시작시,종료시,밝기%", use_yn = 사용)
--  KIOSK_KPI_STRIP  : KPI 요약 스트립 (use_yn = 표시)
-- 롤백: 파일 하단 ROLLBACK 블록

BEGIN;

INSERT INTO tb_grp_code (region, grp_cd, grp_nm, use_yn)
VALUES ('R01', 'SITE_SETTING', '사이트 설정', 'Y')
ON CONFLICT (region, grp_cd) DO NOTHING;

INSERT INTO tb_comm_code (region, grp_cd, comm_cd, comm_nm, comm_val, use_yn)
VALUES
  ('R01', 'SITE_SETTING', 'KIOSK_VIEWS', '키오스크 순환 화면', 'gis,flow,alarm', 'Y'),
  ('R01', 'SITE_SETTING', 'KIOSK_NIGHT_DIM', '키오스크 야간 밝기', '22,6,40', 'N'),
  ('R01', 'SITE_SETTING', 'KIOSK_KPI_STRIP', '키오스크 KPI 스트립', NULL, 'Y')
ON CONFLICT (region, grp_cd, comm_cd) DO NOTHING;

COMMIT;

-- =============================================================================
-- ROLLBACK
-- =============================================================================
-- DELETE FROM tb_comm_code
--  WHERE region = 'R01' AND grp_cd = 'SITE_SETTING'
--    AND comm_cd IN ('KIOSK_VIEWS', 'KIOSK_NIGHT_DIM', 'KIOSK_KPI_STRIP');
