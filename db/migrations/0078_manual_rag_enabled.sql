-- Migration 0078: 매뉴얼·고장 케이스 RAG 모듈 마스터 토글 (B3 Phase 1)
-- 사양: docs/feature-sku-spec.md §4 B3 → v1.1 Phase 1 구현
--
-- 배경: 매뉴얼 PDF 업로드 + embeddings 빌드 + 고장 케이스 시드가 전제.
--       데이터 미구축 사이트는 비전 진단 응답이 빈 RAG 결과로 무의미.
--       기본 OFF — master 가 명시 활성화한 사이트만 RAG 동작.
--
-- 영향: Vision Agent (vision_agent.py) 가 매 진단 호출 시 is_manual_rag_enabled()
--       체크. False 면 _retrieve_manual_excerpts + _retrieve_fault_cases skip
--       (응답에서 manuals_retrieved, fault_cases 빈 배열).
--
-- 롤백:
--   DELETE FROM tb_comm_code WHERE grp_cd='SITE_SETTING' AND comm_cd='MANUAL_RAG_ENABLED';

BEGIN;

INSERT INTO tb_comm_code (region, grp_cd, comm_cd, comm_nm, sort_num, use_yn)
SELECT DISTINCT region, 'SITE_SETTING', 'MANUAL_RAG_ENABLED',
       '매뉴얼·고장 케이스 RAG (B3)', 30, 'N'
  FROM tb_comm_code
 WHERE grp_cd = 'SITE_SETTING'
ON CONFLICT (region, grp_cd, comm_cd) DO NOTHING;

COMMIT;
