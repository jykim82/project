-- 0057: 배수지 수위 trend_catalog 백필
--
-- 배경: 6 배수지(죽동/송산2산단공업·생활/합덕/합덕인더스/합덕일반) 에
--       수위 태그(tb_tag_info, Analog Input) 가 존재하지만
--       tb_trend_catalog 에 trend_name='수위' 행이 없어
--       "죽동 배수지 수위 트렌드 보여줘" 질의가 "카탈로그가 등록되지
--       않았습니다" 로 실패.
--
-- 조치: 해당 시설들의 Analog Input 수위 태그를 자동 items 배열 생성
--       (label = "{sitename} 배수지 수위N", unit='m', data_category='수위')
--
-- 롤백:
--   DELETE FROM tb_trend_catalog
--   WHERE facilitytype='배수지' AND trend_name='수위'
--     AND sitename IN ('죽동','송산2산단공업','송산2산단생활',
--                      '합덕','합덕인더스','합덕일반');

WITH missing AS (
  SELECT
    t.sitename,
    jsonb_build_object(
      'items',
      jsonb_agg(
        jsonb_build_object(
          'unit',          COALESCE(NULLIF(t.unit, ''), 'm'),
          'label',          t.sitename || ' 배수지 ' || REGEXP_REPLACE(t.datainfo, '^.*?\s*', ''),
          'tagsn',          t.tagsn,
          'data_category', '수위'
        )
        ORDER BY t.tagsn
      )
    ) AS meta
  FROM tb_tag_info t
  LEFT JOIN tb_trend_catalog tc
    ON tc.sitename     = t.sitename
   AND tc.facilitytype = '배수지'
   AND tc.trend_name   = '수위'
  WHERE t.facilitytype = '배수지'
    AND t.tagtype      = 'Analog Input'
    AND t.datainfo ILIKE '%수위%'
    AND t.datainfo NOT LIKE '%SET%'
    AND t.datainfo NOT LIKE '%알람%'
    AND t.sitename <> '스모크테스트'
    AND tc.sitename IS NULL
  GROUP BY t.sitename
), next_id AS (
  SELECT COALESCE(MAX(trend_id), 0) AS mx FROM tb_trend_catalog
)
INSERT INTO tb_trend_catalog (trend_id, sitename, facilitytype, trend_name, meta)
SELECT (SELECT mx FROM next_id) + ROW_NUMBER() OVER (ORDER BY m.sitename),
       m.sitename, '배수지', '수위', m.meta
FROM missing m;
