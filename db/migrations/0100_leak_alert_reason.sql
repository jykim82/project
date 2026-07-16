-- 0100: 누수 의심 알림 선정 사유 서술 컬럼 (사용자 요청 2026-07-16)
-- CUSUM 스캔 시점의 판정 근거(최근 평균 vs 기준·초과 시점·추세)를 자연어로 저장.
-- 기존 행은 NULL — 프런트가 저장 수치로 요약 폴백 표기.
-- 롤백: ALTER TABLE tb_leak_cusum_alert DROP COLUMN reason;

ALTER TABLE tb_leak_cusum_alert ADD COLUMN IF NOT EXISTS reason text;
