-- 0109: 외부망 통화(TURN) 사이트 설정 토글 (docs/realtime-comm-spec.md §5.5)
--   관리 > 사이트 설정 UI 에서 켜기/끄기. 실동작 = env TURN_ENABLED AND 본 토글.
-- 롤백:
--   DELETE FROM tb_comm_code WHERE grp_cd='SITE_SETTING' AND comm_cd='CALL_TURN_ENABLED';

INSERT INTO tb_comm_code (region, grp_cd, comm_cd, comm_nm, use_yn)
VALUES ('R01', 'SITE_SETTING', 'CALL_TURN_ENABLED', '외부망 통화 (TURN 릴레이)', 'Y')
ON CONFLICT (region, grp_cd, comm_cd) DO NOTHING;
