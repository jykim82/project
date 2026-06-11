-- Migration 0083: 태그 모니터링 메뉴 등록
-- 사양: docs/tag-monitoring-spec.md §6.4
--
-- M003-11 (모니터링 그룹 11번째). 조회 전용 — 구축 태그 마스터(adminOnly)와 달리
-- 일반 운영 권한 접근. 현재값 + (Phase 2) 이상 카테고리 필터 + 트랜드 보기.
--
-- 권한: 조회 전용이므로 MASTER/ADMIN/USER 모두 접근 (수운영자·SCADA·관망 운영자).
--
-- 롤백:
--   DELETE FROM tb_auth_menu WHERE menu_idn='M003-11';
--   DELETE FROM tb_menu WHERE menu_idn='M003-11';

BEGIN;

INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path,
                     menu_type, menu_idx, use_yn)
SELECT region, 'M003-11', '태그 모니터링', 'M003',
       '/monitoring/tags', 'menu', 11, 'Y'
  FROM (SELECT DISTINCT region FROM tb_menu) r
ON CONFLICT (region, menu_idn) DO NOTHING;

INSERT INTO tb_auth_menu (region, auth_idn, menu_idn, use_yn, menu_order)
SELECT region, auth_idn, 'M003-11', 'Y', 11
  FROM (SELECT DISTINCT region, auth_idn FROM tb_auth_menu
         WHERE auth_idn IN ('MASTER', 'ADMIN', 'USER')) a
ON CONFLICT (region, auth_idn, menu_idn) DO NOTHING;

COMMIT;
