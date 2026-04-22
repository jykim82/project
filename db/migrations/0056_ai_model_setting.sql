-- 0056: AI 모델명 영속화
--
-- 배경: slm_config._current_model 이 메모리 전용이라 서버 재시작 시
--       환경변수 OLLAMA_MODEL (기본 gemma4:26b-a4b-it-q4_K_M) 으로 복원.
-- 조치: tb_comm_code 에 AI_MODEL 행 추가 → _AiRuntimeSettings.load_from_db()
--       가 기동 시 set_model() 호출.
--
-- 롤백: DELETE FROM tb_comm_code WHERE comm_cd='AI_MODEL';

INSERT INTO tb_comm_code (region, grp_cd, comm_cd, comm_nm, comm_val, use_yn)
VALUES ('R01', 'SITE_SETTING', 'AI_MODEL', 'AI 모델명',
        'gemma4:26b-a4b-it-q4_K_M', 'Y')
ON CONFLICT (region, grp_cd, comm_cd) DO NOTHING;
