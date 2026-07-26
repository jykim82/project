-- 0122: 트렌드 카탈로그 일원화 (docs/trend-catalog-unification-spec.md)
-- 정본 = tb_trend_catalog 하나. tb_monitoring_catalog(모니터링 화면 카드)는
-- meta.show_monitoring=true 로 이관 후 _legacy rename.
--
-- 롤백:
--   ALTER TABLE tb_monitoring_catalog_legacy RENAME TO tb_monitoring_catalog;
--   DELETE FROM tb_trend_catalog WHERE meta ? 'show_monitoring';
--   (display_order 컬럼·시퀀스는 무해 — 유지)

-- 1) trend_id 시퀀스 (기존 정적 채번 → nextval 기본값)
CREATE SEQUENCE IF NOT EXISTS tb_trend_catalog_trend_id_seq
    OWNED BY tb_trend_catalog.trend_id;
SELECT setval('tb_trend_catalog_trend_id_seq',
              (SELECT COALESCE(max(trend_id), 0) + 1 FROM tb_trend_catalog),
              false);
ALTER TABLE tb_trend_catalog
    ALTER COLUMN trend_id SET DEFAULT nextval('tb_trend_catalog_trend_id_seq');

-- 2) 표시 순서
ALTER TABLE tb_trend_catalog
    ADD COLUMN IF NOT EXISTS display_order integer NOT NULL DEFAULT 0;

-- 3) 모니터링 카탈로그 이관 (멱등)
-- 3a) 신규 명칭 행 INSERT
INSERT INTO tb_trend_catalog
    (sitename, facilitytype, trend_name, meta, description, display_order)
SELECT m.sitename, m.facilitytype, COALESCE(m.catalog_name, '모니터링'),
       jsonb_build_object('items', COALESCE(m.items, '[]'::jsonb),
                          'show_monitoring', true),
       m.description, COALESCE(m.display_order, 0)
FROM tb_monitoring_catalog m
WHERE NOT EXISTS (
    SELECT 1 FROM tb_trend_catalog t
    WHERE t.sitename = m.sitename AND t.facilitytype = m.facilitytype
      AND t.trend_name = COALESCE(m.catalog_name, '모니터링')
);
-- 3b) 시드와 동명인 행 — 사용자 구성 items 를 시드 행에 병합 + 표시 승격
--     (스킵하면 기존 모니터링 카드가 화면에서 사라지는 회귀)
UPDATE tb_trend_catalog t
SET meta = COALESCE(t.meta, '{}'::jsonb)
           || jsonb_build_object('items', COALESCE(m.items, '[]'::jsonb),
                                 'show_monitoring', true),
    display_order = COALESCE(m.display_order, 0),
    updated_at = now()
FROM tb_monitoring_catalog m
WHERE t.sitename = m.sitename AND t.facilitytype = m.facilitytype
  AND t.trend_name = COALESCE(m.catalog_name, '모니터링')
  AND NOT (t.meta ? 'show_monitoring');

-- 4) 구 테이블 백업 rename (관찰 후 DROP)
ALTER TABLE IF EXISTS tb_monitoring_catalog
    RENAME TO tb_monitoring_catalog_legacy;
