-- 0055: tb_comm_code 에 comm_val 컬럼 추가 + AI 런타임 파라미터 영속 시드
--
-- 배경: site-settings 의 AI 파라미터(num_ctx/temperature/timeout) 가
--       백엔드 메모리(_AiRuntimeSettings) 에만 보관되어 서버 재시작 시
--       기본값(4096 / 0.0 / 30) 으로 초기화되는 버그.
-- 조치: tb_comm_code 에 comm_val (수치·문자열 값) 컬럼 추가하고
--       SITE_SETTING/AI_* 3 행을 기본값으로 시드.
--       admin.py PUT/GET 핸들러 + _AiRuntimeSettings.load_from_db() 연동.
--
-- 롤백: ALTER TABLE tb_comm_code DROP COLUMN comm_val;
--       DELETE FROM tb_comm_code WHERE comm_cd LIKE 'AI_%';

ALTER TABLE tb_comm_code
  ADD COLUMN IF NOT EXISTS comm_val VARCHAR(200);

COMMENT ON COLUMN tb_comm_code.comm_val IS
  '공통 코드 값 (수치·문자열 설정용 — Y/N 외 확장)';

-- SITE_SETTING 그룹 보장 (FK 충족)
INSERT INTO tb_grp_code (region, grp_cd, grp_nm, use_yn)
VALUES ('R01', 'SITE_SETTING', '사이트 설정', 'Y')
ON CONFLICT (region, grp_cd) DO NOTHING;

-- AI 런타임 파라미터 기본값 시드
INSERT INTO tb_comm_code (region, grp_cd, comm_cd, comm_nm, comm_val, use_yn)
VALUES
  ('R01', 'SITE_SETTING', 'AI_NUM_CTX',     'AI 컨텍스트 크기',  '4096', 'Y'),
  ('R01', 'SITE_SETTING', 'AI_TEMPERATURE', 'AI Temperature',    '0.0',  'Y'),
  ('R01', 'SITE_SETTING', 'AI_TIMEOUT',     'AI 타임아웃(초)',   '30',   'Y')
ON CONFLICT (region, grp_cd, comm_cd) DO NOTHING;
