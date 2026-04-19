#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""tb_facility_file 테이블 생성 + 뷰 업데이트 마이그레이션"""

import os
os.environ["PGCLIENTENCODING"] = "UTF8"

import psycopg2

DSN = "host=localhost port=5432 dbname=slm user=slm_dev password=slm_dev_1234 client_encoding=UTF8"

def run():
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS tb_facility_file (
        facility_file_id  bigserial PRIMARY KEY,
        region            character varying(10) NOT NULL,
        sitename          character varying(50) NOT NULL,
        file_type         character varying(30) NOT NULL,
        file_id           bigint NOT NULL,
        created_at        timestamp with time zone DEFAULT now(),
        updated_at        timestamp with time zone DEFAULT now(),
        CONSTRAINT fk_facility_file_storage
            FOREIGN KEY (file_id) REFERENCES tb_file_storage(file_id) ON DELETE CASCADE,
        CONSTRAINT uq_facility_file
            UNIQUE (region, sitename, file_type)
    )
    """)
    print("tb_facility_file created")

    cur.execute("CREATE INDEX IF NOT EXISTS idx_facility_file_lookup ON tb_facility_file(region, sitename)")
    print("index created")

    cur.execute("""
    CREATE OR REPLACE VIEW v_reservoir_info_status AS
    SELECT
        ri.region, ri.sitename, ri.facilitytype, ri.general_overview, ri.service_area,
        rs.alarm_high_water_level, rs.alarm_low_water_level, rs.target_level, rs.daily_avg_supply,
        ri.use_yn,
        fs_photo.file_url   AS site_photo_url,
        fs_diagram.file_url AS system_diagram_url,
        fs_manual.file_url  AS manual_url
    FROM tb_service_reservoir_info ri
    LEFT JOIN tb_service_reservoir_status rs ON ri.region = rs.region AND ri.sitename = rs.sitename
    LEFT JOIN tb_facility_file ff_photo   ON ri.region = ff_photo.region   AND ri.sitename = ff_photo.sitename   AND ff_photo.file_type = 'site_photo'
    LEFT JOIN tb_file_storage  fs_photo   ON ff_photo.file_id = fs_photo.file_id
    LEFT JOIN tb_facility_file ff_diagram ON ri.region = ff_diagram.region AND ri.sitename = ff_diagram.sitename AND ff_diagram.file_type = 'system_diagram'
    LEFT JOIN tb_file_storage  fs_diagram ON ff_diagram.file_id = fs_diagram.file_id
    LEFT JOIN tb_facility_file ff_manual  ON ri.region = ff_manual.region  AND ri.sitename = ff_manual.sitename  AND ff_manual.file_type = 'manual'
    LEFT JOIN tb_file_storage  fs_manual  ON ff_manual.file_id = fs_manual.file_id
    WHERE ri.use_yn = 'Y'
    """)
    print("v_reservoir_info_status updated")

    cur.execute("""
    CREATE OR REPLACE VIEW v_booster_station_info_status AS
    SELECT
        bi.region, bi.sitename, bi.facilitytype, bi.general_overview,
        bs.target_pressure, bs.alarm_high_pressure, bs.alarm_low_pressure,
        bi.use_yn,
        fs_photo.file_url   AS site_photo_url,
        fs_diagram.file_url AS system_diagram_url,
        fs_manual.file_url  AS manual_url
    FROM tb_service_booster_station_info bi
    LEFT JOIN tb_service_booster_station_status bs ON bi.region = bs.region AND bi.sitename = bs.sitename
    LEFT JOIN tb_facility_file ff_photo   ON bi.region = ff_photo.region   AND bi.sitename = ff_photo.sitename   AND ff_photo.file_type = 'site_photo'
    LEFT JOIN tb_file_storage  fs_photo   ON ff_photo.file_id = fs_photo.file_id
    LEFT JOIN tb_facility_file ff_diagram ON bi.region = ff_diagram.region AND bi.sitename = ff_diagram.sitename AND ff_diagram.file_type = 'system_diagram'
    LEFT JOIN tb_file_storage  fs_diagram ON ff_diagram.file_id = fs_diagram.file_id
    LEFT JOIN tb_facility_file ff_manual  ON bi.region = ff_manual.region  AND bi.sitename = ff_manual.sitename  AND ff_manual.file_type = 'manual'
    LEFT JOIN tb_file_storage  fs_manual  ON ff_manual.file_id = fs_manual.file_id
    WHERE bi.use_yn = 'Y'
    """)
    print("v_booster_station_info_status updated")

    cur.execute("""
    CREATE OR REPLACE VIEW v_pressure_reducing_facility_info_status AS
    SELECT
        pi.region, pi.sitename, pi.facilitytype, pi.general_overview,
        ps.target_inlet_pressure, ps.target_outlet_pressure,
        pi.use_yn,
        fs_photo.file_url   AS site_photo_url,
        fs_diagram.file_url AS system_diagram_url,
        fs_manual.file_url  AS manual_url
    FROM tb_pressure_reducing_facility_info pi
    LEFT JOIN tb_pressure_reducing_facility_status ps ON pi.region = ps.region AND pi.sitename = ps.sitename
    LEFT JOIN tb_facility_file ff_photo   ON pi.region = ff_photo.region   AND pi.sitename = ff_photo.sitename   AND ff_photo.file_type = 'site_photo'
    LEFT JOIN tb_file_storage  fs_photo   ON ff_photo.file_id = fs_photo.file_id
    LEFT JOIN tb_facility_file ff_diagram ON pi.region = ff_diagram.region AND pi.sitename = ff_diagram.sitename AND ff_diagram.file_type = 'system_diagram'
    LEFT JOIN tb_file_storage  fs_diagram ON ff_diagram.file_id = fs_diagram.file_id
    LEFT JOIN tb_facility_file ff_manual  ON pi.region = ff_manual.region  AND pi.sitename = ff_manual.sitename  AND ff_manual.file_type = 'manual'
    LEFT JOIN tb_file_storage  fs_manual  ON ff_manual.file_id = fs_manual.file_id
    WHERE pi.use_yn = 'Y'
    """)
    print("v_pressure_reducing_facility_info_status updated")

    cur.execute("""
    CREATE OR REPLACE VIEW v_block_info_status AS
    SELECT
        bi.region, bi.sitename, bi.block_level, bi.general_overview,
        bs.alarm_threshold, bs.target_flow,
        bi.use_yn,
        fs_photo.file_url   AS site_photo_url,
        fs_diagram.file_url AS system_diagram_url,
        fs_manual.file_url  AS manual_url
    FROM tb_block_info bi
    LEFT JOIN tb_block_status bs ON bi.region = bs.region AND bi.sitename = bs.sitename
    LEFT JOIN tb_facility_file ff_photo   ON bi.region = ff_photo.region   AND bi.sitename = ff_photo.sitename   AND ff_photo.file_type = 'site_photo'
    LEFT JOIN tb_file_storage  fs_photo   ON ff_photo.file_id = fs_photo.file_id
    LEFT JOIN tb_facility_file ff_diagram ON bi.region = ff_diagram.region AND bi.sitename = ff_diagram.sitename AND ff_diagram.file_type = 'system_diagram'
    LEFT JOIN tb_file_storage  fs_diagram ON ff_diagram.file_id = fs_diagram.file_id
    LEFT JOIN tb_facility_file ff_manual  ON bi.region = ff_manual.region  AND bi.sitename = ff_manual.sitename  AND ff_manual.file_type = 'manual'
    LEFT JOIN tb_file_storage  fs_manual  ON ff_manual.file_id = fs_manual.file_id
    WHERE bi.use_yn = 'Y'
    """)
    print("v_block_info_status updated")

    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='v_reservoir_info_status' AND column_name LIKE '%url'")
    url_cols = [r[0] for r in cur.fetchall()]
    print(f"v_reservoir_info_status URL columns: {url_cols}")

    cur.close()
    conn.close()
    print("ALL DONE")

if __name__ == "__main__":
    run()
