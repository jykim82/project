-- Migration 0073: tb_report 에 담당자 이름 필드 추가
--
-- 인쇄 양식 본문의 "담당자" 셀이 작성자 ID (author_id) 로 자동 채워지던 것을
-- 운영자가 빈 상태로 두고 직접 입력하도록 분리.
--
-- - NULL 허용 (기본: 빈 상태)
-- - 100자 제한 (이름 + 직책 표기 여유)
-- - 운영자가 결재란과 함께 직접 편집
--
-- 롤백: ALTER TABLE tb_report DROP COLUMN responsible_name;

BEGIN;

ALTER TABLE tb_report
  ADD COLUMN IF NOT EXISTS responsible_name VARCHAR(100);

COMMENT ON COLUMN tb_report.responsible_name IS
  '인쇄 양식 본문의 담당자 이름. NULL 이면 공란 출력. 운영자 직접 입력.';

COMMIT;
