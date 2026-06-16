-- Migration 0084: DI 현재값 라벨 오버라이드
-- 사양: docs/tag-monitoring-spec.md §11
--
-- Digital Input 태그의 0/1 raw 값을 의미 라벨로 표시하기 위한 "예외 전용" 테이블.
-- 행이 없는 태그는 백엔드 기본 규칙으로 라벨링한다:
--   alarm_tag_yn=1 → {0:정상, 1:이상}, 이상값=1 (기존 active-high 규칙과 일치)
--   alarm_tag_yn=0 → {0:OFF, 1:ON},  이상값=NULL (중립 상태표시)
-- 극성이 반대(active-low)거나 라벨을 운전/정지 등으로 바꾸려는 태그만 여기에 기록.
--
-- abnormal_value: 어느 raw 값(0|1)이 "이상" 상태인지. NULL 이면 색상/이상 표시 없음.
--
-- region 기반 멀티테넌시 유지. (region, tagsn) 복합 PK.
--
-- 롤백 (데이터 손실 없이 기본 규칙으로 복귀):
--   DROP TABLE IF EXISTS tb_tag_di_label;

BEGIN;

CREATE TABLE IF NOT EXISTS tb_tag_di_label (
    region         character varying NOT NULL DEFAULT 'R01',
    tagsn          character varying NOT NULL,
    label_0        character varying NOT NULL,
    label_1        character varying NOT NULL,
    abnormal_value smallint,
    updated_at     timestamptz NOT NULL DEFAULT now(),
    updated_by     character varying,
    PRIMARY KEY (region, tagsn),
    CONSTRAINT tb_tag_di_label_abnormal_chk
        CHECK (abnormal_value IS NULL OR abnormal_value IN (0, 1))
);

COMMENT ON TABLE  tb_tag_di_label IS 'DI 태그 현재값 라벨 오버라이드 (예외 전용, 없으면 기본 규칙)';
COMMENT ON COLUMN tb_tag_di_label.abnormal_value IS '이상 상태로 간주할 raw 값 (0|1), NULL=중립';

COMMIT;
