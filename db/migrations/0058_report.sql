-- Migration 0058: 보고서 (장애 조치 / 일 점검) + 점검 sub-type
-- 사양: docs/report-spec.md
-- 설계 근거:
--   1) tb_task_master 단일 소스 재사용 (Migration 0045 확장)
--   2) tb_report / tb_report_item 신규 — 발췌·요약·편집·잠금만 담당
--   3) tb_task_master.inspection_type — 일상/정기/특별 구분
-- 롤백 절차:
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
  report_type   VARCHAR(30) NOT NULL,                  -- fault_action / daily_inspection (P2 확장 예정)
  report_date   DATE NOT NULL,                         -- 사용자 선택 보고일자
  author_id     VARCHAR(50) NOT NULL,                  -- 작성자 user_id
  title         VARCHAR(200),                          -- 제목 (자동 생성, 수정 가능)
  status        VARCHAR(20) NOT NULL DEFAULT 'draft',  -- draft / finalized
  finalized_at  TIMESTAMPTZ,                           -- 확정 시각 (NULL = draft)
  finalized_by  VARCHAR(50),                           -- 확정자 user_id
  photo_layout  VARCHAR(10) DEFAULT '2up',             -- 1up / 2up (사진 부록 페이지당 장수)
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
CREATE INDEX IF NOT EXISTS idx_report_status
  ON tb_report(status) WHERE status = 'draft';

COMMENT ON TABLE  tb_report IS
  '보고서 본체 (장애 조치 / 일 점검 등). 항목은 tb_report_item 참조';
COMMENT ON COLUMN tb_report.report_type  IS '보고서 유형 (fault_action / daily_inspection / 향후 weekly_summary 등)';
COMMENT ON COLUMN tb_report.status       IS 'draft = 편집 가능, finalized = 잠금';
COMMENT ON COLUMN tb_report.photo_layout IS '인쇄 시 사진 부록 페이지당 장수 (1up=풀페이지, 2up=반페이지)';


-- ========================================================================
-- 3. 보고서 항목
-- ========================================================================
CREATE TABLE IF NOT EXISTS tb_report_item (
  item_id        BIGSERIAL PRIMARY KEY,
  report_id      BIGINT NOT NULL REFERENCES tb_report(report_id) ON DELETE CASCADE,
  seq            INT NOT NULL,                         -- 본문 표시 순서
  task_id        BIGINT REFERENCES tb_task_master(task_id),  -- 원본 이력 (NULL 가능)
  occurred_at    TIMESTAMPTZ,                          -- 발생일자 (편집 가능)
  occurred_text  TEXT,                                 -- 발생내용 (AI 요약 → 편집)
  resolved_at    TIMESTAMPTZ,                          -- 조치일자
  resolved_text  TEXT,                                 -- 조치내용 (AI 요약 → 편집)
  original_text  TEXT,                                 -- 원문 보존 (요약 전 task_content + resolution_note)
  photo_urls     JSONB,                                -- 본문/부록 포함 사진 URL 배열
  exclude_photo  BOOLEAN NOT NULL DEFAULT false,       -- 사용자가 사진 제외 토글
  ai_summary_at  TIMESTAMPTZ,                          -- 마지막 요약 시각
  ai_model       VARCHAR(50),                          -- 요약에 쓰인 모델명
  created_at     TIMESTAMPTZ DEFAULT now(),
  updated_at     TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_report_item_report_seq
  ON tb_report_item(report_id, seq);
CREATE INDEX IF NOT EXISTS idx_report_item_task
  ON tb_report_item(task_id) WHERE task_id IS NOT NULL;

COMMENT ON TABLE  tb_report_item IS
  '보고서 본문 항목 1건 = 장애/점검 이력 1건 (또는 사용자 수동 입력)';
COMMENT ON COLUMN tb_report_item.original_text IS
  '원문 보존본 — UI "원문 보기" 토글용. 사용자 편집과 무관';
COMMENT ON COLUMN tb_report_item.exclude_photo IS
  '사진을 본 항목에서 제외할지 (사진 부록 미포함)';


-- ========================================================================
-- 4. updated_at 자동 갱신 트리거 (기존 패턴 재사용 가정)
-- ========================================================================
-- 프로젝트 공통 set_updated_at() 함수가 없을 경우 별도 마이그레이션에서 보강.
-- 본 마이그레이션은 의존을 두지 않고, 애플리케이션 레벨에서 updated_at 갱신.


COMMIT;
