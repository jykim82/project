-- 0138_module_version.sql
-- 모듈 버전·라이선스 관리 P1 (docs/module-version-spec.md)
--
-- 버전은 시드+수동 갱신(P1) — 배포 스크립트 자동 스탬핑은 P2.
-- 라이선스 활성 여부는 저장하지 않는다: SKU 상태는 tb_comm_code
-- (SITE_SETTING) 가 정본이라 조회 시 실시간 조인 (정본 이원화 금지).
--
-- 롤백: 파일 하단 ROLLBACK 블록

BEGIN;

CREATE TABLE IF NOT EXISTS tb_module_version (
    region       varchar(20)  NOT NULL DEFAULT 'R01',
    module_key   varchar(40)  NOT NULL,
    name         varchar(100) NOT NULL,
    -- container(이미지)/bundle(파일 번들)/feature(SKU 기능)/data(구축 데이터)
    kind         varchar(20)  NOT NULL,
    version      varchar(100) NOT NULL,
    installed_at timestamptz  NOT NULL DEFAULT now(),
    installed_by varchar(50)  NOT NULL DEFAULT 'seed',
    notes        varchar(300),
    updated_at   timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (region, module_key)
);

CREATE TABLE IF NOT EXISTS tb_module_license (
    region       varchar(20) NOT NULL DEFAULT 'R01',
    module_key   varchar(40) NOT NULL,
    -- NULL = 기본 포함(core). 값이 있으면 SITE_SETTING 의 해당 코드가 정본
    sku_code     varchar(40),
    -- ["OpenStreetMap (ODbL)", ...] — 납품 고지 의무 목록
    oss_notices  jsonb       NOT NULL DEFAULT '[]'::jsonb,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (region, module_key)
);

COMMENT ON TABLE tb_module_version IS
  '모듈 버전 레지스트리 (module-version-spec P1 — 시드+수동, 자동 스탬핑 P2)';
COMMENT ON TABLE tb_module_license IS
  '모듈 라이선스 — sku_code 활성 여부는 tb_comm_code(SITE_SETTING) 정본';

-- 시드: 현 설치 상태 (2026-09-01 기준)
INSERT INTO tb_module_version (module_key, name, kind, version, notes) VALUES
  ('backend',      '백엔드 (FastAPI)',        'container', '2026.09.01',  'ai_server + endpoints'),
  ('frontend',     '프런트엔드 (Next.js)',    'container', '2026.09.01',  'slm-dashboard 프로덕션 빌드'),
  ('db',           'DB 마이그레이션',          'data',      '0138',        'TimescaleDB — 순차 SQL'),
  ('node-red',     'Node-RED (수집·알람)',    'container', 'flows 2026-07', ''),
  ('ai-weights',   'AI 모델 웨이트',          'bundle',    'chronos-bolt-base + whisper-large-v3', 'model_weights_bundle.sh 관리'),
  ('map-bundle',   'GIS 지도 번들',           'bundle',    'dangjin 2026-07', 'region.pmtiles (OSM/Protomaps)'),
  ('vision-agent', '비전 에이전트 (gemma)',   'container', '2026.07',     '호스트 네이티브 :8100'),
  ('epanet',       'EPANET 관망 해석 (B1)',   'feature',   'Phase 1',     'feature-sku-spec B1')
ON CONFLICT (region, module_key) DO NOTHING;

INSERT INTO tb_module_license (module_key, sku_code, oss_notices) VALUES
  ('backend',      NULL, '["FastAPI (MIT)", "PostgreSQL (PostgreSQL License)", "TimescaleDB (Apache-2.0/TSL)"]'),
  ('frontend',     NULL, '["Next.js (MIT)", "Apache ECharts (Apache-2.0)", "MapLibre GL JS (BSD-3-Clause)", "shadcn/ui (MIT)"]'),
  ('db',           NULL, '[]'),
  ('node-red',     NULL, '["Node-RED (Apache-2.0)"]'),
  ('ai-weights',   NULL, '["Chronos-Bolt (Apache-2.0)", "OpenAI Whisper (MIT)", "snowflake-arctic-embed2 (Apache-2.0)"]'),
  ('map-bundle',   NULL, '["© OpenStreetMap contributors (ODbL)", "Protomaps (ODbL)"]'),
  ('vision-agent', 'VISION_AGENT_ENABLED', '["gemma (Google Terms of Use)", "Ollama (MIT)"]'),
  ('epanet',       'EPANET_ENABLED', '["EPANET 2.2 (Public Domain / MIT wrapper)"]')
ON CONFLICT (region, module_key) DO NOTHING;

-- 메뉴 M100-17 — 관리 그룹
INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path, menu_type, menu_idx, use_yn)
SELECT m.region, 'M100-17', '시스템 버전', 'M100', '/admin/system-modules', 'menu', 17, 'Y'
FROM tb_menu m WHERE m.menu_idn = 'M100'
ON CONFLICT (region, menu_idn) DO NOTHING;

INSERT INTO tb_auth_menu (region, auth_idn, menu_idn)
SELECT DISTINCT a.region, a.auth_idn, 'M100-17'
FROM tb_auth_menu a WHERE a.menu_idn = 'M100-1'
ON CONFLICT DO NOTHING;

COMMIT;

-- =============================================================================
-- ROLLBACK
-- =============================================================================
-- BEGIN;
-- DELETE FROM tb_auth_menu WHERE menu_idn = 'M100-17';
-- DELETE FROM tb_menu      WHERE menu_idn = 'M100-17';
-- DROP TABLE IF EXISTS tb_module_license;
-- DROP TABLE IF EXISTS tb_module_version;
-- COMMIT;
