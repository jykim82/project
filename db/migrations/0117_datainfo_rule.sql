-- 0117: DATAINFO 변환룰 (docs/datainfo-conversion-rule-spec.md)
-- datadesc(SCADA 원본) → datainfo(SLM 조회 표준) 룰 기반 변환.
-- 롤백: DROP TABLE IF EXISTS tb_datainfo_apply_log, tb_datainfo_rule;

CREATE TABLE IF NOT EXISTS tb_datainfo_rule (
  rule_id      serial PRIMARY KEY,
  region       varchar(10) NOT NULL DEFAULT 'R01',
  rule_type    varchar(10) NOT NULL CHECK (rule_type IN ('regex','dict','context','override')),
  pattern      text NOT NULL,          -- regex 패턴 / dict 원어 / override 는 미사용('')
  replacement  text NOT NULL,          -- 치환 결과 (override 는 최종 datainfo)
  context_facilitytype varchar(50),    -- context 룰: 시설유형 일치 시만 적용
  context_tagtype      varchar(50),    -- context 룰: tagtype 일치 시만 적용
  target_tagsn varchar(50),            -- override 룰: 대상 태그
  priority     int NOT NULL DEFAULT 100,
  enabled      boolean NOT NULL DEFAULT true,
  notes        text,
  updated_at   timestamptz NOT NULL DEFAULT now(),
  updated_by   varchar(50)
);

COMMENT ON TABLE tb_datainfo_rule IS
  'datadesc→datainfo 변환룰 — 구축 고도화 ① (spec: docs/datainfo-conversion-rule-spec.md)';

-- 적용 이력 (롤백 가능 스냅샷)
CREATE TABLE IF NOT EXISTS tb_datainfo_apply_log (
  log_id      bigserial PRIMARY KEY,
  region      varchar(10) NOT NULL DEFAULT 'R01',
  tagsn       varchar(50) NOT NULL,
  old_datainfo text,
  new_datainfo text,
  applied_at  timestamptz NOT NULL DEFAULT now(),
  applied_by  varchar(50)
);
