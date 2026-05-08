-- Migration 0070: EPANET 메뉴 활성/비활성 토글 (Phase 3.3 후속)
-- 사용자 요구: "표현 항목들은 나중에 구현후에 활성 비활성화를 선택할수 있도록"
--
-- 관리 > EPANET 시뮬레이션 페이지에서 10 메뉴별 토글로 ON/OFF.
-- enabled='N' 인 메뉴는 사이드바 hidden + 페이지 직접 진입 시 안내.
--
-- menu_key: docs/epanet-menu-spec.md §1 의 메뉴 키 (data-quality API 의 키와 동일)
--   gis-flow / leak-suspicious / headloss-anomaly /
--   valve-impact / pipe-break / pump-control / scenario-diff /
--   replacement-candidates / network-aging / water-quality
--
-- 초기값: 모든 메뉴 'Y' (default 활성). 운영자가 사이트별로 비활성화.
--
-- 롤백: DROP TABLE IF EXISTS tb_epanet_menu_setting;

BEGIN;

CREATE TABLE IF NOT EXISTS tb_epanet_menu_setting (
    region      VARCHAR(10) NOT NULL,
    menu_key    VARCHAR(50) NOT NULL,
    enabled     CHAR(1)     NOT NULL DEFAULT 'Y',
    label       VARCHAR(100),
    notes       TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by  VARCHAR(50),
    PRIMARY KEY (region, menu_key),
    CONSTRAINT chk_epanet_menu_enabled CHECK (enabled IN ('Y','N'))
);

COMMENT ON TABLE  tb_epanet_menu_setting IS 'EPANET 표현 메뉴별 활성/비활성 토글 (운영자 설정)';
COMMENT ON COLUMN tb_epanet_menu_setting.menu_key IS 'data-quality API 의 메뉴 키와 동일';
COMMENT ON COLUMN tb_epanet_menu_setting.enabled  IS 'Y=활성 (사이드바 노출) / N=비활성 (hidden)';

-- ---- 초기 seed: 10 메뉴 default 'Y' (모든 region) ----
INSERT INTO tb_epanet_menu_setting (region, menu_key, label, enabled)
SELECT DISTINCT r.region, m.menu_key, m.label, 'Y'
FROM (SELECT DISTINCT region FROM tb_menu) r
CROSS JOIN (VALUES
    ('gis-flow',                'GIS 관망 흐름'),
    ('leak-suspicious',         '누수 의심 구간'),
    ('headloss-anomaly',        '헤드손실 이상 구간'),
    ('valve-impact',            '차단밸브 영향범위'),
    ('pipe-break',              '관로 파손 시뮬'),
    ('pump-control',            '펌프 가동 변경'),
    ('scenario-diff',           '시나리오 비교'),
    ('replacement-candidates',  '블록 교체 후보'),
    ('network-aging',           '관망 노후도 평가'),
    ('water-quality',           '수질·체류시간')
) AS m(menu_key, label)
ON CONFLICT (region, menu_key) DO NOTHING;

COMMIT;
