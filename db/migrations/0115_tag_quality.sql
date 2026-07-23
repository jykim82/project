-- 0115: 태그 품질 계층 P1 — tb_tag_quality (docs/tag-quality-layer-spec.md §4)
-- 검사 5종(포화·고착·무신호·DI 반전·값 이탈)의 현재 상태를 태그별 1행으로
-- 유지 (비정상만 저장, 정상 복귀 시 삭제). 갱신: backend 내장 1시간 루프.
-- 롤백: DROP TABLE IF EXISTS tb_tag_quality;

CREATE TABLE IF NOT EXISTS tb_tag_quality (
  region       varchar(10)  NOT NULL DEFAULT 'R01',
  tagsn        varchar(50)  NOT NULL,
  status       varchar(10)  NOT NULL,          -- suspect | bad
  reason       varchar(30)  NOT NULL,          -- 센서포화의심/신호고착의심/센서무응답/데이터홀딩/데이터없음/DI상시ON의심/값이탈저신호
  detail       text,
  since        timestamptz  NOT NULL DEFAULT now(),  -- 같은 reason 지속 시작
  checked_at   timestamptz  NOT NULL DEFAULT now(),
  window_stats jsonb,                          -- 판정 근거 박제
  PRIMARY KEY (region, tagsn)
);

COMMENT ON TABLE tb_tag_quality IS
  '태그 품질 계층 P1 — 계측 품질 이상 현재 상태 (spec: docs/tag-quality-layer-spec.md)';
