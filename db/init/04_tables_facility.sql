-- ============================================================
-- 04: Facility Tables - 시설 정보 + 상태 + 장비
-- ============================================================

SET client_encoding = 'UTF8';

-- ============================================================
-- 배수지
-- ============================================================
CREATE TABLE IF NOT EXISTS tb_service_reservoir_info (
    region            character varying(10) NOT NULL,
    sitename          character varying(50) NOT NULL,
    facilitytype      character varying(30) DEFAULT '배수지',
    general_overview  jsonb DEFAULT '{}'::jsonb,
    service_area      character varying(200),
    meta              jsonb DEFAULT '{}'::jsonb,
    use_yn            character varying(1) DEFAULT 'Y',
    created_at        timestamp with time zone DEFAULT now(),
    updated_at        timestamp with time zone DEFAULT now(),
    PRIMARY KEY (region, sitename)
);
COMMENT ON TABLE tb_service_reservoir_info IS '배수지 기본정보. general_overview에 설치위치/용량/규격 등 JSON';

CREATE TABLE IF NOT EXISTS tb_service_reservoir_status (
    region                  character varying(10) NOT NULL,
    sitename                character varying(50) NOT NULL,
    facilitytype            character varying(30) DEFAULT '배수지',
    alarm_high_water_level  numeric(10,2),
    alarm_low_water_level   numeric(10,2),
    target_level            numeric(10,2),
    daily_avg_supply        numeric(12,2),
    updated_at              timestamp with time zone DEFAULT now(),
    PRIMARY KEY (region, sitename)
);

-- ============================================================
-- 가압장
-- ============================================================
CREATE TABLE IF NOT EXISTS tb_service_booster_station_info (
    region            character varying(10) NOT NULL,
    sitename          character varying(50) NOT NULL,
    facilitytype      character varying(30) DEFAULT '가압장',
    general_overview  jsonb DEFAULT '{}'::jsonb,
    use_yn            character varying(1) DEFAULT 'Y',
    created_at        timestamp with time zone DEFAULT now(),
    updated_at        timestamp with time zone DEFAULT now(),
    PRIMARY KEY (region, sitename)
);
COMMENT ON TABLE tb_service_booster_station_info IS '가압장 기본정보';

CREATE TABLE IF NOT EXISTS tb_service_booster_station_status (
    region              character varying(10) NOT NULL,
    sitename            character varying(50) NOT NULL,
    facilitytype        character varying(30) DEFAULT '가압장',
    target_pressure     numeric(10,2),
    alarm_high_pressure numeric(10,2),
    alarm_low_pressure  numeric(10,2),
    updated_at          timestamp with time zone DEFAULT now(),
    PRIMARY KEY (region, sitename)
);

-- ============================================================
-- 감압시설
-- ============================================================
CREATE TABLE IF NOT EXISTS tb_pressure_reducing_facility_info (
    region            character varying(10) NOT NULL,
    sitename          character varying(50) NOT NULL,
    facilitytype      character varying(30) DEFAULT '감압시설',
    general_overview  jsonb DEFAULT '{}'::jsonb,
    use_yn            character varying(1) DEFAULT 'Y',
    created_at        timestamp with time zone DEFAULT now(),
    updated_at        timestamp with time zone DEFAULT now(),
    PRIMARY KEY (region, sitename)
);

CREATE TABLE IF NOT EXISTS tb_pressure_reducing_facility_status (
    region                  character varying(10) NOT NULL,
    sitename                character varying(50) NOT NULL,
    facilitytype            character varying(30) DEFAULT '감압시설',
    target_inlet_pressure   numeric(10,2),
    target_outlet_pressure  numeric(10,2),
    updated_at              timestamp with time zone DEFAULT now(),
    PRIMARY KEY (region, sitename)
);

-- ============================================================
-- 블록
-- ============================================================
CREATE TABLE IF NOT EXISTS tb_block_info (
    region            character varying(10) NOT NULL,
    sitename          character varying(50) NOT NULL,
    block_level       character varying(20),
    general_overview  jsonb DEFAULT '{}'::jsonb,
    use_yn            character varying(1) DEFAULT 'Y',
    created_at        timestamp with time zone DEFAULT now(),
    updated_at        timestamp with time zone DEFAULT now(),
    PRIMARY KEY (region, sitename)
);

CREATE TABLE IF NOT EXISTS tb_block_status (
    region           character varying(10) NOT NULL,
    sitename         character varying(50) NOT NULL,
    alarm_threshold  numeric(10,2),
    target_flow      numeric(12,2),
    updated_at       timestamp with time zone DEFAULT now(),
    PRIMARY KEY (region, sitename)
);

-- ============================================================
-- 장비 (Equipment)
-- ============================================================
CREATE TABLE IF NOT EXISTS tb_equipment_info (
    equipment_id    character varying(100) NOT NULL,
    region          character varying(10),
    sitename        character varying(50),
    facilitytype    character varying(30),
    equipmenttype   character varying(50),
    equipment_name  character varying(100),
    specs           jsonb DEFAULT '{}'::jsonb,
    ip_address      character varying(50),
    use_yn          character varying(1) DEFAULT 'Y',
    created_at      timestamp with time zone DEFAULT now(),
    updated_at      timestamp with time zone DEFAULT now(),
    PRIMARY KEY (equipment_id)
);
COMMENT ON TABLE tb_equipment_info IS '장비 마스터. 네트워크 토폴로지 및 상태 추적용';

CREATE TABLE IF NOT EXISTS tb_equipment_status (
    equipment_id    character varying(100) NOT NULL,
    region          character varying(10),
    sitename        character varying(50),
    facilitytype    character varying(30),
    equipment_name  character varying(100),
    alarm_setting   numeric(10,2),
    operation_mode  character varying(20) DEFAULT 'AUTO',
    is_alive        boolean DEFAULT true,
    rtt_ms          numeric(10,2),
    status_code     character varying(20),
    protocol_status character varying(20),
    error_message   text,
    check_time      timestamp with time zone DEFAULT now(),
    updated_at      timestamp with time zone DEFAULT now(),
    PRIMARY KEY (equipment_id)
);

-- ============================================================
-- 장비 알람 리포트
-- ============================================================
CREATE TABLE IF NOT EXISTS tb_equipment_alarm_report (
    report_id     bigserial PRIMARY KEY,
    region        character varying(10),
    equipment_id  character varying(100),
    sitename      character varying(50),
    facilitytype  character varying(30),
    alarm_level   character varying(10),
    alarm_message text,
    alarm_time    timestamp with time zone DEFAULT now(),
    resolved_time timestamp with time zone,
    resolved_yn   character varying(1) DEFAULT 'N'
);

-- ============================================================
-- 네트워크 장비 연결
-- ============================================================
CREATE TABLE IF NOT EXISTS tb_network_link (
    link_id        character varying(100) NOT NULL,
    region         character varying(10),
    source_id      character varying(100),
    target_id      character varying(100),
    link_type      character varying(30),
    link_protocol  character varying(30),
    link_port      integer,
    link_device_interface character varying(50),
    bandwidth      character varying(30),
    status         character varying(20) DEFAULT '정상',
    created_at     timestamp with time zone DEFAULT now(),
    PRIMARY KEY (link_id)
);

-- ============================================================
-- 시설명 약칭 매핑 (사용자 구어체 → 정식 sitename)
-- ============================================================
-- 목적: 사용자가 "합일", "죽배", "구 남산" 같은 약칭/구어체로 질문할 때
-- 정식 sitename으로 변환해 ParamExtractor._extract_sitename의 fuzzy 매칭을
-- 건너뛰고 즉시 매핑한다. 관리자(또는 A-1 피드백 검토 결과)가 채운다.
CREATE TABLE IF NOT EXISTS tb_facility_alias (
    alias_id    bigserial PRIMARY KEY,
    region      character varying(10) NOT NULL,
    alias       character varying(100) NOT NULL,
    sitename    character varying(50) NOT NULL,
    priority    integer NOT NULL DEFAULT 0,
    note        text,
    use_yn      character varying(1) NOT NULL DEFAULT 'Y',
    created_at  timestamp with time zone NOT NULL DEFAULT now(),
    updated_at  timestamp with time zone NOT NULL DEFAULT now()
);
COMMENT ON TABLE tb_facility_alias IS
    '시설명 약칭 → 정식 sitename 매핑. 런타임 load 후 ParamExtractor가 exact lookup';
COMMENT ON COLUMN tb_facility_alias.priority IS
    '중복 alias 충돌 시 더 높은 값이 우선. 기본 0';

CREATE UNIQUE INDEX IF NOT EXISTS uq_facility_alias_region_alias
    ON tb_facility_alias(region, alias) WHERE use_yn = 'Y';

CREATE INDEX IF NOT EXISTS idx_facility_alias_sitename
    ON tb_facility_alias(region, sitename);

-- ============================================================
-- 시설 흐름 매핑
-- ============================================================
CREATE TABLE IF NOT EXISTS tb_facility_flow_map (
    flow_id          character varying(100) NOT NULL,
    region           character varying(10) NOT NULL,
    source_facility  character varying(50),
    source_type      character varying(30),
    target_facility  character varying(50),
    target_type      character varying(30),
    flow_order       integer DEFAULT 0,
    use_yn           character varying(1) DEFAULT 'Y',
    created_at       timestamp with time zone DEFAULT now(),
    updated_at       timestamp with time zone DEFAULT now(),
    PRIMARY KEY (flow_id, region)
);
