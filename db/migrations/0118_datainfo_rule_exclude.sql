-- 0118: DATAINFO 변환룰 — 태그 단위 '제외(exclude)' 정책 추가
-- (docs/datainfo-conversion-rule-spec.md §2 확장)
-- 배경: 룰 변환이 맞는 태그와 원문(현행 datainfo) 유지가 맞는 태그가 섞여
-- 있음 — 태그 단위 구분 필요 (사용자 요청 2026-07-24).
--  - override: 지정 값으로 최종 고정 (기존)
--  - exclude : 룰 변환 대상에서 제외 — 현행 datainfo 그대로 유지 (신규)
-- 롤백: 아래 CHECK 를 원래 3종으로 재생성 + exclude 행 삭제

ALTER TABLE tb_datainfo_rule DROP CONSTRAINT IF EXISTS tb_datainfo_rule_rule_type_check;
ALTER TABLE tb_datainfo_rule ADD CONSTRAINT tb_datainfo_rule_rule_type_check
  CHECK (rule_type IN ('regex','dict','context','override','exclude'));
