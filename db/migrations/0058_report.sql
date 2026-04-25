-- Migration 0058: 보고서 (장애 조치 / 일 점검) + 점검 sub-type + 메뉴 등록
-- 사양: docs/report-spec.md (v2 정합성 보강)
-- 설계 근거:
--   1) tb_task_master 단일 소스 재사용 (Migration 0045 확장)
--   2) tb_report / tb_report_item 신규 — 발췌·요약·편집·잠금만 담당
--   3) tb_task_master.inspection_type — 일상/정기/특별 구분
--   4) tb_report_item — 시점 메타(시설/설비/분류) 캐시 + 사진 JSONB 객체 배열(전/후/추가 출처 보존)
--   5) tb_menu / tb_auth_menu — 보고서 메뉴 등록 (Migration 0049 패턴)
-- 롤백 절차:
--   DELETE FROM tb_auth_menu WHERE menu_idn IN ('M005','M005-1','M005-2');
--   DELETE FROM tb_menu      WHERE menu_idn IN ('M005-1','M005-2');
--   DELETE FROM tb_menu      WHERE menu_idn = 'M005';
--   DROP TABLE tb_report_item CASCADE;
--   DROP TABLE tb_report CASCADE;
--   ALTER TABLE tb_task_master DROP COLUMN IF EXISTS inspection_type;

BEGIN;

-- ========================================================================
-- 1. tb_task_master 점검 sub-type 컬럼 확장
-- ========================================================================
ALTER TABLE tb_task_master
  ADD COLUMN IF NOT EXISTS inspection_type VARCHAR(20);

COMMENT ON COLUMN tb_task_master.inspection_type IS
  '점검 sub-type (일상/정기/특별). task_category=''점검'' 일 때만 의미. NULL 허용';

CREATE INDEX IF NOT EXISTS idx_task_inspection_type
  ON tb_task_master(inspection_type)
  WHERE inspection_type IS NOT NULL;


-- ========================================================================
-- 2. 보고서 본체
-- ========================================================================
CREATE TABLE IF NOT EXISTS tb_report (
  report_id     BIGSERIAL PRIMARY KEY,
  region        VARCHAR(20) NOT NULL,
  report_type   VARCHAR(30) NOT NULL,                  -- fault_action / daily_inspection
  report_date   DATE NOT NULL,                         -- 사용자 선택 보고일자
  author_id     VARCHAR(50) NOT NULL,                  -- 작성자 user_id (recorded_by 와 동일 패턴)
  title         VARCHAR(200),                          -- 제목 (자동 생성, 수정 가능)
  status        VARCHAR(20) NOT NULL DEFAULT 'draft',  -- draft / finalized
  finalized_at  TIMESTAMPTZ,                           -- 확정 시각 (NULL = draft)
  finalized_by  VARCHAR(50),                           -- 확정자 user_id
  photo_layout  VARCHAR(10) DEFAULT '2up',             -- 1up / 2up
  created_at    TIMESTAMPTZ DEFAULT now(),
  updated_at    TIMESTAMPTZ DEFAULT now(),
  CONSTRAINT chk_report_type   CHECK (report_type IN ('fault_action','daily_inspection')),
  CONSTRAINT chk_report_status CHECK (status      IN ('draft','finalized')),
  CONSTRAINT chk_photo_layout  CHECK (photo_layout IN ('1up','2up'))
);

CREATE INDEX IF NOT EXISTS idx_report_region_type_date
  ON tb_report(region, report_type, report_date DESC);
CREATE INDEX IF NOT EXISTS idx_report_author_created
  ON tb_report(author_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_report_status_draft
  ON tb_report(status) WHERE status = 'draft';

COMMENT ON TABLE  tb_report IS
  '보고서 본체 (장애 조치 / 일 점검 등). 항목은 tb_report_item 참조';
COMMENT ON COLUMN tb_report.region       IS '작성자 JWT 의 region. 멀티테넌시 격리';
COMMENT ON COLUMN tb_report.report_type  IS '보고서 유형 (fault_action / daily_inspection / 향후 weekly_summary 등)';
COMMENT ON COLUMN tb_report.author_id    IS '작성자 user_id (tb_task_master.recorded_by 와 동일 컬럼 형식)';
COMMENT ON COLUMN tb_report.status       IS 'draft = 편집 가능, finalized = 잠금 (재오픈 가능)';
COMMENT ON COLUMN tb_report.photo_layout IS '인쇄 시 사진 부록 페이지당 장수 (1up=풀페이지, 2up=반페이지)';


-- ========================================================================
-- 3. 보고서 항목 (시점 메타 캐시 + 사진 JSONB 객체 배열)
-- ========================================================================
CREATE TABLE IF NOT EXISTS tb_report_item (
  item_id          BIGSERIAL PRIMARY KEY,
  report_id        BIGINT NOT NULL REFERENCES tb_report(report_id) ON DELETE CASCADE,
  seq              INT NOT NULL,                       -- 본문 표시 순서 (1..N)
  task_id          BIGINT REFERENCES tb_task_master(task_id),  -- 원본 이력 (NULL = 수동 입력, P2)

  -- 시점 메타 캐시 (보고서 생성 시 스냅샷, 원본 변경에 영향받지 않음)
  site_name        VARCHAR(100),                       -- 시설명
  facility_type    VARCHAR(50),                        -- 시설 유형 (배수지/가압장/블록…)
  equipment_name   VARCHAR(100),                       -- 설비명
  fault_category   VARCHAR(30),                        -- 분류 (고장/이상/교체/점검)
  inspection_type  VARCHAR(20),                        -- 점검 sub-type (일상/정기/특별)

  -- 본문 내용
  occurred_at      TIMESTAMPTZ,                        -- 발생일자 (편집 가능)
  occurred_text    TEXT,                               -- 발생내용 (AI 요약 → 편집)
  resolved_at      TIMESTAMPTZ,                        -- 조치일자
  resolved_text    TEXT,                               -- 조치내용 (AI 요약 → 편집)
  original_text    TEXT,                               -- 원문 보존 ([발생]+[조치] 결합)

  -- 사진 JSONB 객체 배열 — 출처 보존 (사양 §3.4)
  --   [{ "url":"/api/files/chat_attachments/...", "source":"fault|action|user",
  --      "caption":"...", "taken_at":"..." }, ...]
  photo_urls       JSONB,
  exclude_photo    BOOLEAN NOT NULL DEFAULT false,     -- 사진 부록 전체 제외 토글

  -- AI 요약 메타
  ai_summary_at    TIMESTAMPTZ,
  ai_model         VARCHAR(50),

  created_at       TIMESTAMPTZ DEFAULT now(),
  updated_at       TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_report_item_report_seq
  ON tb_report_item(report_id, seq);
CREATE INDEX IF NOT EXISTS idx_report_item_task
  ON tb_report_item(task_id) WHERE task_id IS NOT NULL;

-- 같은 task_id 가 한 보고서에 두 번 들어가는 것을 방지 (수동 입력 NULL 은 예외)
CREATE UNIQUE INDEX IF NOT EXISTS uq_report_item_task
  ON tb_report_item(report_id, task_id)
  WHERE task_id IS NOT NULL;

COMMENT ON TABLE  tb_report_item IS
  '보고서 본문 항목 1건 = 장애/점검 이력 1건 (또는 수동 입력)';
COMMENT ON COLUMN tb_report_item.site_name     IS '시설명 — 보고 시점 캐시 (원본 변경 영향 X)';
COMMENT ON COLUMN tb_report_item.facility_type IS '시설 유형 — 보고 시점 캐시';
COMMENT ON COLUMN tb_report_item.equipment_name IS '설비명 — 보고 시점 캐시';
COMMENT ON COLUMN tb_report_item.fault_category IS '분류 — 보고 시점 값 (고장/이상/교체/점검)';
COMMENT ON COLUMN tb_report_item.inspection_type IS '점검 sub-type — 보고 시점 값';
COMMENT ON COLUMN tb_report_item.original_text IS
  '원문 보존본 — UI "원문 보기" 토글용. 사용자 편집과 무관';
COMMENT ON COLUMN tb_report_item.photo_urls    IS
  '사진 객체 배열. {url, source(fault|action|user), caption, taken_at}';
COMMENT ON COLUMN tb_report_item.exclude_photo IS
  '사진 부록 전체 제외 토글';


-- ========================================================================
-- 4. tb_menu / tb_auth_menu 등록 (Migration 0049 패턴)
-- ========================================================================

-- 4.1 보고서 그룹 노드 (M005)
INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path, menu_type, menu_idx, use_yn)
SELECT DISTINCT region, 'M005', '보고서', NULL, NULL, 'menu', 90, 'Y'
FROM tb_menu
ON CONFLICT (region, menu_idn) DO NOTHING;

-- 4.2 장애 조치 보고서 (M005-1)
INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path, menu_type, menu_idx, use_yn)
SELECT region, 'M005-1', '장애 조치 보고서', 'M005', '/reports/fault-action',
       'menu', 1, 'Y'
FROM tb_menu
WHERE menu_idn = 'M005'
ON CONFLICT (region, menu_idn) DO NOTHING;

-- 4.3 일 점검 보고서 (M005-2)
INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path, menu_type, menu_idx, use_yn)
SELECT region, 'M005-2', '일 점검 보고서', 'M005', '/reports/daily-inspection',
       'menu', 2, 'Y'
FROM tb_menu
WHERE menu_idn = 'M005'
ON CONFLICT (region, menu_idn) DO NOTHING;

-- 4.4 MASTER/ADMIN 권한 부여
INSERT INTO tb_auth_menu (region, auth_idn, menu_idn, use_yn, menu_order)
SELECT m.region, a.auth_idn, m.menu_idn, 'Y',
       CASE m.menu_idn WHEN 'M005' THEN 90
                       WHEN 'M005-1' THEN 1
                       WHEN 'M005-2' THEN 2 END
FROM tb_menu m
CROSS JOIN (VALUES ('MASTER'), ('ADMIN')) AS a(auth_idn)
WHERE m.menu_idn IN ('M005','M005-1','M005-2')
ON CONFLICT (region, auth_idn, menu_idn) DO NOTHING;


COMMIT;
