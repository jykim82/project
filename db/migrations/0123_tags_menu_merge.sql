-- 0123: 태그 메뉴 통합 — 태그 마스터 + DATAINFO 변환룰을 "태그 설정" 탭
-- 페이지(/setup/tags)로 정리. /setup/datainfo-rules 는 redirect.
-- (구축 메뉴 직관성 — 태그 등록과 변환룰은 한 몸인데 메뉴가 갈라져 있던 것)
--
-- 롤백:
--   UPDATE tb_menu SET menu_nm='태그 마스터' WHERE menu_idn='M200-5';
--   INSERT INTO tb_menu ... M200-20 '/setup/datainfo-rules' 재등록 + tb_auth_menu 재부여
--   (0117 마이그레이션의 M200-20 등록 구문 참조)

UPDATE tb_menu SET menu_nm = '태그 설정' WHERE menu_idn = 'M200-5';
DELETE FROM tb_auth_menu WHERE menu_idn = 'M200-20';
DELETE FROM tb_menu WHERE menu_idn = 'M200-20';
