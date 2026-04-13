-- ============================================================
-- 05: Tag & TimeSeries Tables - 센서 태그 + 시계열 데이터
-- TimescaleDB 하이퍼테이블 설정 포함
-- ============================================================

SET client_encoding = 'UTF8';

-- ------------------------------------------------------------
-- 태그 마스터 (센서 정의)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tb_tag_info (
    region        character varying(10) NOT NULL,
    tagsn         character varying(50) NOT NULL,
    sitename      character varying(50),
    facilitytype  character varying(30),
    tagtype       character varying(30),            -- 'Analog Input', 'Digital Input' 등
    datadesc      character varying(200),
    datainfo      character varying(50),            -- '수위', '유량', '압력', '밸브', '펌프' 등
    unit          character varying(20),
    use_yn        character varying(1) DEFAULT 'Y',
    created_at    timestamp with time zone DEFAULT now(),
    PRIMARY KEY (region, tagsn)
);
COMMENT ON TABLE tb_tag_info IS '센서/태그 마스터. SCADA 태그와 1:1 매핑';

CREATE INDEX IF NOT EXISTS idx_tag_info_site
    ON tb_tag_info(region, sitename, facilitytype);

-- ------------------------------------------------------------
-- 태그 원시 데이터 (TimescaleDB 하이퍼테이블)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tb_tag_raw_data (
    region   character varying(10) NOT NULL,
    tagsn    character varying(50) NOT NULL,
    logtime  timestamp with time zone NOT NULL,
    val      double precision
);
COMMENT ON TABLE tb_tag_raw_data IS 'SCADA 센서 시계열 데이터. TimescaleDB 하이퍼테이블';

-- TimescaleDB 하이퍼테이블로 변환 (logtime 기준 자동 파티션)
SELECT create_hypertable(
    'tb_tag_raw_data',
    by_range('logtime'),
    if_not_exists => TRUE
);

-- 태그별 + 시간별 조회 인덱스
CREATE INDEX IF NOT EXISTS idx_tag_raw_tagsn_time
    ON tb_tag_raw_data(tagsn, logtime DESC);

CREATE INDEX IF NOT EXISTS idx_tag_raw_region_time
    ON tb_tag_raw_data(region, logtime DESC);

-- ------------------------------------------------------------
-- 트렌드 카탈로그 (사전 정의 트렌드 차트)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tb_trend_catalog (
    region      character varying(10) NOT NULL,
    trend_id    character varying(50) NOT NULL,
    trend_name  character varying(200),
    meta        jsonb DEFAULT '{}'::jsonb,          -- 차트 설정, 태그 목록 등
    use_yn      character varying(1) DEFAULT 'Y',
    created_at  timestamp with time zone DEFAULT now(),
    PRIMARY KEY (region, trend_id)
);

-- ⚠ tb_alarm_log 제거됨 [E-020] (2026-04-13)
-- 알람 이력은 tb_equipment_alarm_report로 통합되었음 (richer schema:
-- alarm_severity / diagnosed_cause / countermeasure / meta jsonb / diagnosed_msg HTML).
-- 백업: db/backups/unused_tables_backup_2026-04-13.sql

-- ------------------------------------------------------------
-- 누수 CUSUM 알림 이력 (야간최소유량 기반)
-- ------------------------------------------------------------
-- 백그라운드 태스크가 LEAK_CUSUM_ANALYSIS를 주기 실행 → leak_status="누수의심"
-- 감지 시 이 테이블에 저장. 사용자가 UI에서 확인·ack 처리.
CREATE TABLE IF NOT EXISTS tb_leak_cusum_alert (
    alert_id      bigserial PRIMARY KEY,
    region        character varying(10) NOT NULL,
    sitename      character varying(100) NOT NULL,
    facilitytype  character varying(50) NOT NULL,
    tagsn         character varying(100) NOT NULL,
    label         text,
    leak_status   character varying(20) NOT NULL,
    cusum_value   numeric(12,4),
    threshold_h   numeric(12,4),
    baseline_mean numeric(12,4),
    detected_at   timestamp with time zone NOT NULL DEFAULT now(),
    acknowledged  boolean NOT NULL DEFAULT false,
    ack_at        timestamp with time zone,
    ack_by        character varying(45),
    note          text
);
COMMENT ON TABLE tb_leak_cusum_alert IS
    '야간최소유량 CUSUM 기반 누수 의심 알림 이력 (수동 acknowledged)';

CREATE INDEX IF NOT EXISTS idx_leak_cusum_pending
    ON tb_leak_cusum_alert(region, detected_at DESC)
    WHERE acknowledged = false;

CREATE INDEX IF NOT EXISTS idx_leak_cusum_site_tagsn
    ON tb_leak_cusum_alert(region, sitename, facilitytype, tagsn, detected_at DESC);
