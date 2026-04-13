-- ============================================================
-- Seed 03: 메뉴 트리 + 권한 매핑
--
-- 단일 출처: slm-dashboard/src/lib/config/sidebar-menus.ts
-- 변경 시 양쪽을 동시에 동기화할 것.
--
-- ON CONFLICT DO UPDATE — 재시드 시 라벨/경로/순서 변경이 반영되도록 함
-- (단, FK가 걸린 menu_idn은 PK라 보존됨)
-- ============================================================

SET client_encoding = 'UTF8';

-- ── 메뉴 트리 ────────────────────────────────────────────────
INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path, menu_type, menu_idx, icon, use_yn) VALUES
    -- 루트
    ('R01', 'M001',    '대시보드',       NULL,    '/dashboard',                 'menu',   1,  'LayoutDashboard', 'Y'),
    ('R01', 'M006',    '위기대응',       NULL,    NULL,                          'group',  2,  'ShieldAlert',     'Y'),
    ('R01', 'M002',    'AI 채팅',        NULL,    '/chat',                       'menu',   3,  'MessageSquare',   'Y'),
    ('R01', 'M003',    '모니터링',       NULL,    NULL,                          'group',  4,  'Activity',        'Y'),
    ('R01', 'M004',    '트렌드',         NULL,    '/trend',                      'menu',   5,  'TrendingUp',      'Y'),
    ('R01', 'M007',    '네트워크',       NULL,    '/network',                    'menu',   6,  'Network',         'Y'),
    ('R01', 'M100',    '관리',           NULL,    NULL,                          'group', 100, 'Settings',        'Y'),
    ('R01', 'M200',    '구축',           NULL,    NULL,                          'group', 200, 'Database',        'Y'),

    -- M006 위기대응
    ('R01', 'M006-1',  '경보관리',       'M006',  '/crisis/alarm-dashboard',     'menu',   1,  'Bell',            'Y'),
    ('R01', 'M006-2',  '경보분석',       'M006',  '/crisis/alarm-analysis',      'menu',   2,  'BarChart',        'Y'),
    ('R01', 'M006-3',  '작업관리 설정',  'M006',  '/crisis/task-management',     'menu',   3,  'ClipboardList',   'Y'),

    -- M003 모니터링
    ('R01', 'M003-1',  '배수지',         'M003',  '/monitoring/reservoir',       'menu',   1,  'Droplets',        'Y'),
    ('R01', 'M003-2',  '가압장',         'M003',  '/monitoring/booster',         'menu',   2,  'Gauge',           'Y'),
    ('R01', 'M003-3',  '블록',           'M003',  '/monitoring/block',           'menu',   3,  'Boxes',           'Y'),
    ('R01', 'M003-4',  '용수 흐름',      'M003',  '/monitoring/flow',            'menu',   4,  'Waves',           'Y'),
    ('R01', 'M003-5',  'GIS 관망도',     'M003',  '/monitoring/gis',             'menu',   5,  'Map',             'Y'),
    ('R01', 'M003-6',  '알람 캘린더',    'M003',  '/monitoring/alarm-calendar',  'menu',   6,  'Calendar',        'Y'),
    ('R01', 'M003-7',  '누수 의심 알림', 'M003',  '/monitoring/leak-alerts',     'menu',   7,  'Droplet',         'Y'),

    -- M100 관리 (adminOnly)
    ('R01', 'M100-1',  '사용자',         'M100',  '/admin/users',                'menu',   1,  'Users',           'Y'),
    ('R01', 'M100-2',  '메뉴',           'M100',  '/admin/menus',                'menu',   2,  'Menu',            'Y'),
    ('R01', 'M100-4',  'FAQ',            'M100',  '/admin/faq',                  'menu',   4,  'HelpCircle',      'Y'),
    ('R01', 'M100-5',  '시설 파일',      'M100',  '/admin/facility-files',       'menu',   5,  'FileText',        'Y'),
    ('R01', 'M100-6',  '사이트 설정',    'M100',  '/admin/site-settings',        'menu',   6,  'Settings2',       'Y'),
    ('R01', 'M100-7',  '채팅 피드백',    'M100',  '/admin/chat-feedback',        'menu',   7,  'MessageCircle',   'Y'),
    ('R01', 'M100-8',  '시설 약칭',      'M100',  '/admin/facility-alias',       'menu',   8,  'Tag',             'Y'),
    ('R01', 'M100-9',  '설비 신뢰성',    'M100',  '/admin/equipment-mtbf',       'menu',   9,  'Wrench',          'Y'),
    ('R01', 'M100-10', 'LLM 서술 관찰',  'M100',  '/admin/llm-narrative',        'menu',  10,  'Sparkles',        'Y'),

    -- M200 구축 (adminOnly)
    ('R01', 'M200-1',  '배수지',         'M200',  '/setup/reservoir',            'menu',   1,  'Droplets',        'Y'),
    ('R01', 'M200-2',  '가압장',         'M200',  '/setup/booster',              'menu',   2,  'Gauge',           'Y'),
    ('R01', 'M200-3',  '감압시설',       'M200',  '/setup/pressure',             'menu',   3,  'ArrowDownCircle', 'Y'),
    ('R01', 'M200-4',  '블록',           'M200',  '/setup/block',                'menu',   4,  'Boxes',           'Y'),
    ('R01', 'M200-5',  '태그 마스터',    'M200',  '/setup/tags',                 'menu',   5,  'Hash',            'Y'),
    ('R01', 'M200-6',  '설비',           'M200',  '/setup/equipments',           'menu',   6,  'Cpu',             'Y'),
    ('R01', 'M200-7',  '모니터링 설정',  'M200',  '/setup/trends',               'menu',   7,  'TrendingUp',      'Y'),
    ('R01', 'M200-8',  '네트워크',       'M200',  '/setup/networks',             'menu',   8,  'Network',         'Y'),
    ('R01', 'M200-9',  '용수 흐름',      'M200',  '/setup/flow-map',             'menu',   9,  'Waves',           'Y'),
    ('R01', 'M200-10', '잠금 관리',      'M200',  '/setup/field-locks',          'menu',  10,  'Lock',            'Y'),
    ('R01', 'M200-11', '캔버스 에디터',  'M200',  '/setup/canvas',               'menu',  11,  'Layout',          'Y'),
    ('R01', 'M200-12', '인과 규칙',      'M200',  '/setup/causal-rules',         'menu',  12,  'GitBranch',       'Y'),
    ('R01', 'M200-13', 'GIS 관리',       'M200',  '/setup/gis',                  'menu',  13,  'Map',             'Y'),
    ('R01', 'M200-14', '비상연락처',     'M200',  '/setup/alarm-contacts',       'menu',  14,  'PhoneCall',       'Y')
ON CONFLICT (region, menu_idn) DO UPDATE SET
    pmenu_idn = EXCLUDED.pmenu_idn,
    menu_nm   = EXCLUDED.menu_nm,
    app_path  = EXCLUDED.app_path,
    menu_type = EXCLUDED.menu_type,
    menu_idx  = EXCLUDED.menu_idx,
    icon      = EXCLUDED.icon,
    use_yn    = EXCLUDED.use_yn,
    updated_at = now();

-- ── 권한별 메뉴 접근 ─────────────────────────────────────────
-- ADMIN/MASTER: 전체
INSERT INTO tb_auth_menu (region, auth_idn, menu_idn)
SELECT 'R01', 'ADMIN', menu_idn FROM tb_menu WHERE region = 'R01'
ON CONFLICT DO NOTHING;

INSERT INTO tb_auth_menu (region, auth_idn, menu_idn)
SELECT 'R01', 'MASTER', menu_idn FROM tb_menu WHERE region = 'R01'
ON CONFLICT DO NOTHING;

-- USER: 관리(M100) + 구축(M200) 제외
INSERT INTO tb_auth_menu (region, auth_idn, menu_idn)
SELECT 'R01', 'USER', menu_idn
FROM tb_menu
WHERE region = 'R01'
  AND menu_idn NOT LIKE 'M100%'
  AND menu_idn NOT LIKE 'M200%'
ON CONFLICT DO NOTHING;

-- VIEWER: 조회 메뉴만 (채팅/관리/구축 제외) — VIEWER 권한이 정의된 경우만
INSERT INTO tb_auth_menu (region, auth_idn, menu_idn)
SELECT 'R01', 'VIEWER', menu_idn
FROM tb_menu
WHERE region = 'R01'
  AND menu_idn NOT LIKE 'M100%'
  AND menu_idn NOT LIKE 'M200%'
  AND menu_idn NOT IN ('M002')
  AND EXISTS (SELECT 1 FROM tb_auth WHERE region = 'R01' AND auth_idn = 'VIEWER')
ON CONFLICT DO NOTHING;
