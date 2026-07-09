-- Migration 0096: 사용자별 선호도(preferences) — 테마·브랜드·레이아웃 개인 설정
-- 사양: docs/tweaks-layout-spec.md (2026-07 개인별 저장)
--
-- tb_user 에 jsonb preferences 컬럼 추가. 현재는 preferences->'tweaks'
-- (brand_color_id/layout_mode/default_theme) 저장. 개인 설정이 없으면
-- 사이트 기본값(tb_comm_code SITE_SETTING/TWEAKS_*)으로 폴백.
--
-- 롤백:
--   ALTER TABLE tb_user DROP COLUMN IF EXISTS preferences;

BEGIN;

ALTER TABLE tb_user ADD COLUMN IF NOT EXISTS preferences jsonb;

COMMIT;
