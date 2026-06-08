-- Migration 0077: tb_alarm_contact.region 'water' → 'R01' 통일
-- 사양: docs/emergency-contact-spec.md
--
-- 배경: 다른 모든 테이블 (tb_user, tb_menu, tb_facility 등) 은 region='R01'
--       사용. tb_alarm_contact 만 레거시로 region='water' 10건 보유. 사이드바
--       /setup/alarm-contacts 페이지에서 R01 region 조회 시 빈 화면.
--
-- 변경: 'water' → 'R01' UPDATE. 같은 (region, category, company) 조합이 R01
--       에 이미 있으면 ON CONFLICT 처리 (현재 R01 데이터 없음 — 안전).
--
-- 롤백:
--   UPDATE tb_alarm_contact SET region='water' WHERE region='R01' AND
--   contact_id IN (1,2,3,4,5,6,7,8,9,10);  -- 또는 이 마이그레이션 적용
--   직전 시점의 contact_id 식별

BEGIN;

UPDATE tb_alarm_contact
   SET region = 'R01', updated_at = NOW()
 WHERE region = 'water';

COMMIT;
