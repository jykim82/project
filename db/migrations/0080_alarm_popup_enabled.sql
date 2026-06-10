-- Migration 0080: 알람 팝업 마스터 토글 (위기대응 모달)
-- 사양: docs/alarm-popup-spec.md §2.1
--
-- 기본값 'Y' — 운영자에게 유용한 기본 기능 (B1 EPANET / B3 RAG 와 달리
-- 데이터 의존 없음). 운영자가 명시적으로 끌 수 있음.
--
-- 롤백:
--   DELETE FROM tb_comm_code WHERE grp_cd='SITE_SETTING' AND comm_cd='ALARM_POPUP_ENABLED';

BEGIN;

INSERT INTO tb_comm_code (region, grp_cd, comm_cd, comm_nm, sort_num, use_yn)
SELECT DISTINCT region, 'SITE_SETTING', 'ALARM_POPUP_ENABLED',
       '알람 팝업 (위기대응 모달)', 40, 'Y'
  FROM tb_comm_code
 WHERE grp_cd = 'SITE_SETTING'
ON CONFLICT (region, grp_cd, comm_cd) DO NOTHING;

COMMIT;
