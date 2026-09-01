-- 0137_reservoir_pressure_catalog.sql
-- 배수지 압력 트렌드 카탈로그 등록 [E-060 후속]
--
-- "한달간 배수지 압력을 표로 보여줘"(FACILITY_CATALOG_TREND_TABLE)가 0건 —
-- 실측 압력 태그(PRI, Analog Input)는 배수지 4곳 10개가 있는데 트렌드
-- 카탈로그에 배수지 '압력' 트렌드가 미등록이라 카탈로그 기반 조회가 빈다.
--
-- 태그 원장에서 파생(INSERT...SELECT) — 특정 현장 하드코딩 없이 배수지
-- PRI 태그가 있는 현장마다 카탈로그 1행 생성. 다른 고객 DB 에서도 동일
-- 마이그레이션이 그 현장 데이터 기준으로 동작한다.
--
-- 멱등: 이미 같은 (sitename, facilitytype, trend_name) 이 있으면 건너뜀.
-- 롤백: 파일 하단 ROLLBACK 블록

BEGIN;

INSERT INTO tb_trend_catalog (sitename, facilitytype, trend_name, meta, description)
SELECT
    t.sitename,
    '배수지',
    '압력',
    jsonb_build_object('items', jsonb_agg(
        jsonb_build_object(
            'tagsn', t.tagsn,
            'label', t.datadesc,
            'unit', COALESCE(NULLIF(t.unit, ''), 'kgf/cm²'),
            'data_category', '압력'
        ) ORDER BY t.tagsn
    )),
    '배수지 압력 (0137 — 태그 원장 파생)'
FROM tb_tag_info t
WHERE t.facilitytype = '배수지'
  AND t.tagtype = 'Analog Input'
  AND (t.datainfo ~ '압력' OR t.datadesc ~ '압력')
GROUP BY t.sitename
ON CONFLICT DO NOTHING;

-- 유니크 제약이 없을 수 있어 중복 방지는 사전 존재 검사로 한 번 더
DELETE FROM tb_trend_catalog a
USING tb_trend_catalog b
WHERE a.trend_id > b.trend_id
  AND a.sitename = b.sitename
  AND a.facilitytype = b.facilitytype
  AND a.trend_name = b.trend_name
  AND a.facilitytype = '배수지' AND a.trend_name = '압력';

COMMIT;

-- =============================================================================
-- ROLLBACK
-- =============================================================================
-- DELETE FROM tb_trend_catalog
-- WHERE facilitytype='배수지' AND trend_name='압력'
--   AND description LIKE '%0137%';
