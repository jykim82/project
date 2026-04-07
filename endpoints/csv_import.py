"""
CSV 일괄 가져오기 (Import) 엔드포인트
태그/설비/배수지/가압장/감압시설/블록 마스터 데이터 업로드
"""

import csv as csv_mod
import io
import json
import logging

from fastapi import APIRouter, UploadFile

logger = logging.getLogger(__name__)

router = APIRouter(tags=["csv-import"])

_get_db_connection = None


def init(get_db_connection_fn):
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


def _get_conn():
    if _get_db_connection is None:
        raise RuntimeError("csv_import not initialized")
    return _get_db_connection()


# ── CSV 헬퍼 함수 ─────────────────────────────────────────────────────────────

def _csv_cell(row: list, idx: int) -> str:
    """CSV 행에서 안전하게 셀 값을 추출 (strip 포함)."""
    return row[idx].strip() if len(row) > idx else ""


def _csv_float(row: list, idx: int):
    """CSV 셀을 float로 변환. 빈 문자열/비숫자는 None 반환."""
    v = _csv_cell(row, idx)
    if not v:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _csv_int(row: list, idx: int):
    """CSV 셀을 int로 변환. 빈 문자열/비숫자는 None 반환."""
    v = _csv_cell(row, idx)
    if not v:
        return None
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None


def _csv_bool(row: list, idx: int):
    """CSV 셀을 bool로 변환. '유'→True, '무'→False, 빈값→None."""
    v = _csv_cell(row, idx)
    if not v:
        return None
    return v.strip() in ("유", "Y", "y", "true", "True", "1", "예")


def _csv_json_array(row: list, idx: int):
    """CSV 셀을 세미콜론 구분 JSON 배열로 변환. 빈값→None."""
    v = _csv_cell(row, idx)
    if not v:
        return None
    parts = [p.strip() for p in v.split(";") if p.strip()]
    return parts if parts else None


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@router.post("/tags/import/csv")
async def import_tags_csv(file: UploadFile):
    """태그 마스터 CSV 업로드 (일괄 입력)."""
    conn = None
    try:
        content = await file.read()
        text = content.decode("utf-8-sig")
        reader = csv_mod.reader(io.StringIO(text))
        header = next(reader, None)
        if not header or len(header) < 6:
            return {"status": "ERROR", "message": "CSV 헤더 부족 (최소 6컬럼)"}

        conn = _get_conn()
        cur = conn.cursor()
        created = 0
        skipped = 0

        for row in reader:
            if len(row) < 6:
                skipped += 1
                continue
            tagsn = row[0].strip()
            if not tagsn:
                skipped += 1
                continue

            tagtype = row[1].strip() or None
            sitename = row[2].strip() or None
            facilitytype = row[3].strip() or None
            equipmenttype = row[4].strip() or None
            datainfo = row[5].strip() or None
            datadesc = row[6].strip() if len(row) > 6 and row[6].strip() else None
            unit = row[7].strip() if len(row) > 7 and row[7].strip() else None
            alarm_tag_yn = int(row[8].strip()) if len(row) > 8 and row[8].strip().isdigit() else 0

            cur.execute("""
                INSERT INTO tb_tag_info
                    (tagsn, tagtype, sitename, facilitytype, equipmenttype,
                     datainfo, datadesc, unit, alarm_tag_yn)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tagsn) DO UPDATE SET
                    tagtype = EXCLUDED.tagtype,
                    sitename = EXCLUDED.sitename,
                    facilitytype = EXCLUDED.facilitytype,
                    equipmenttype = EXCLUDED.equipmenttype,
                    datainfo = EXCLUDED.datainfo,
                    datadesc = EXCLUDED.datadesc,
                    unit = EXCLUDED.unit,
                    alarm_tag_yn = EXCLUDED.alarm_tag_yn
            """, (tagsn, tagtype, sitename, facilitytype, equipmenttype,
                  datainfo, datadesc, unit, alarm_tag_yn))
            created += 1

        conn.commit()
        cur.close()
        return {"status": "OK", "created": created, "skipped": skipped}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"태그 마스터 CSV 가져오기 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.post("/equipments/import/csv")
async def import_equipments_csv(file: UploadFile):
    """설비 정보 CSV 업로드 (일괄 입력)."""
    STATUS_MAP = {"운영중": "operational", "점검중": "maintenance", "폐기": "decommissioned"}

    conn = None
    try:
        content = await file.read()
        text = content.decode("utf-8-sig")
        reader = csv_mod.reader(io.StringIO(text))
        header = next(reader, None)
        if not header or len(header) < 4:
            return {"status": "ERROR", "message": "CSV 헤더 부족 (최소 4컬럼)"}

        conn = _get_conn()
        cur = conn.cursor()
        created = 0
        skipped = 0

        for row in reader:
            if len(row) < 4:
                skipped += 1
                continue
            equipment_id = row[0].strip()
            sitename = row[1].strip()
            facilitytype = row[2].strip()
            equipmenttype = row[3].strip()
            if not equipment_id or not sitename or not facilitytype or not equipmenttype:
                skipped += 1
                continue

            status_raw = row[4].strip() if len(row) > 4 and row[4].strip() else ""
            status = STATUS_MAP.get(status_raw, "operational")

            meta_raw = {
                "model": row[5].strip() if len(row) > 5 else "",
                "manufacturer": row[6].strip() if len(row) > 6 else "",
                "role": row[10].strip() if len(row) > 10 else "",
                "note": row[11].strip() if len(row) > 11 else "",
            }
            meta = {k: v for k, v in meta_raw.items() if v}

            commissioned_at = row[7].strip() if len(row) > 7 and row[7].strip() else None
            decommissioned_at = row[8].strip() if len(row) > 8 and row[8].strip() else None
            description = row[9].strip() if len(row) > 9 and row[9].strip() else None

            cur.execute("""
                INSERT INTO tb_equipment_info
                    (equipment_id, sitename, facilitytype, equipmenttype,
                     status, commissioned_at, decommissioned_at, description, meta)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (equipment_id) DO UPDATE SET
                    sitename = EXCLUDED.sitename,
                    facilitytype = EXCLUDED.facilitytype,
                    equipmenttype = EXCLUDED.equipmenttype,
                    status = EXCLUDED.status,
                    commissioned_at = EXCLUDED.commissioned_at,
                    decommissioned_at = EXCLUDED.decommissioned_at,
                    description = EXCLUDED.description,
                    meta = EXCLUDED.meta,
                    updated_at = NOW()
            """, (equipment_id, sitename, facilitytype, equipmenttype,
                  status, commissioned_at, decommissioned_at, description,
                  json.dumps(meta)))
            created += 1

        conn.commit()
        cur.close()
        return {"status": "OK", "created": created, "skipped": skipped}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"설비 정보 CSV 가져오기 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.post("/reservoirs/import/csv")
async def import_reservoirs_csv(file: UploadFile):
    """배수지 정보 CSV 업로드 (일괄 입력).
    CSV 컬럼 순서 (27열):
      0:현장명, 1:설치위치, 2:운영현황, 3:시설용량(㎥), 4:급수인구(명), 5:설치연도,
      6:급수지역, 7:배수지수, 8:고수위HWL(m), 9:저수위LWL(m), 10:급수위치,
      11:공급시간(시간), 12:급수차접근, 13:급수차회전, 14:펌프필요, 15:비상급수계획,
      16:구역수, 17:1구역면적(㎡), 18:1구역높이(m), 19:2구역면적(㎡), 20:2구역높이(m),
      21:3구역면적(㎡), 22:3구역높이(m), 23:4구역면적(㎡), 24:4구역높이(m),
      25:5구역면적(㎡), 26:5구역높이(m)
    """
    conn = None
    try:
        content = await file.read()
        text = content.decode("utf-8-sig")
        reader = csv_mod.reader(io.StringIO(text))
        header = next(reader, None)
        if not header or len(header) < 1:
            return {"status": "ERROR", "message": "CSV 헤더 부족 (최소 1컬럼)"}

        conn = _get_conn()
        cur = conn.cursor()
        created = 0
        skipped = 0

        for row in reader:
            if len(row) < 1:
                skipped += 1
                continue
            sitename = _csv_cell(row, 0)
            if not sitename:
                skipped += 1
                continue

            # general_overview JSONB 필드 구성
            overview = {}
            v = _csv_cell(row, 1)
            if v:
                overview["install_location"] = v
            v = _csv_cell(row, 2)
            if v:
                overview["operating_status"] = v
            v = _csv_cell(row, 3)
            if v:
                overview["facility_capacity_m3"] = v
            v = _csv_cell(row, 4)
            if v:
                overview["supply_population"] = v
            v = _csv_cell(row, 5)
            if v:
                overview["install_year"] = v
            v = _csv_cell(row, 6)
            if v:
                overview["service_area"] = v
            v = _csv_cell(row, 7)
            if v:
                overview["reservoir_count"] = v
            fv = _csv_float(row, 8)
            if fv is not None:
                overview["hwl"] = fv
            fv = _csv_float(row, 9)
            if fv is not None:
                overview["lwl"] = fv
            v = _csv_cell(row, 10)
            if v:
                overview["supply_position"] = v
            v = _csv_cell(row, 11)
            if v:
                overview["supply_time_hours"] = v
            bv = _csv_bool(row, 12)
            if bv is not None:
                overview["water_truck_accessible"] = bv
            bv = _csv_bool(row, 13)
            if bv is not None:
                overview["water_truck_turning_possible"] = bv
            bv = _csv_bool(row, 14)
            if bv is not None:
                overview["pump_required"] = bv
            av = _csv_json_array(row, 15)
            if av is not None:
                overview["emergency_water_plan"] = av

            # 직접 컬럼 (zone_count + zone_*_area/height)
            zone_count = _csv_int(row, 16)
            zone_1_area = _csv_float(row, 17)
            zone_1_height = _csv_float(row, 18)
            zone_2_area = _csv_float(row, 19)
            zone_2_height = _csv_float(row, 20)
            zone_3_area = _csv_float(row, 21)
            zone_3_height = _csv_float(row, 22)
            zone_4_area = _csv_float(row, 23)
            zone_4_height = _csv_float(row, 24)
            zone_5_area = _csv_float(row, 25)
            zone_5_height = _csv_float(row, 26)

            cur.execute("""
                INSERT INTO tb_service_reservoir_info (
                    sitename, general_overview,
                    zone_count, zone_1_area, zone_1_height, zone_2_area, zone_2_height,
                    zone_3_area, zone_3_height, zone_4_area, zone_4_height,
                    zone_5_area, zone_5_height
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (sitename) DO UPDATE SET
                    general_overview = EXCLUDED.general_overview,
                    zone_count = EXCLUDED.zone_count,
                    zone_1_area = EXCLUDED.zone_1_area,
                    zone_1_height = EXCLUDED.zone_1_height,
                    zone_2_area = EXCLUDED.zone_2_area,
                    zone_2_height = EXCLUDED.zone_2_height,
                    zone_3_area = EXCLUDED.zone_3_area,
                    zone_3_height = EXCLUDED.zone_3_height,
                    zone_4_area = EXCLUDED.zone_4_area,
                    zone_4_height = EXCLUDED.zone_4_height,
                    zone_5_area = EXCLUDED.zone_5_area,
                    zone_5_height = EXCLUDED.zone_5_height
            """, (
                sitename, json.dumps(overview),
                zone_count, zone_1_area, zone_1_height, zone_2_area, zone_2_height,
                zone_3_area, zone_3_height, zone_4_area, zone_4_height,
                zone_5_area, zone_5_height,
            ))
            created += 1

        conn.commit()
        cur.close()
        return {"status": "OK", "created": created, "skipped": skipped}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"배수지 정보 CSV 가져오기 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.post("/boosters/import/csv")
async def import_boosters_csv(file: UploadFile):
    """가압장 정보 CSV 업로드 (일괄 입력).
    CSV 컬럼 순서 (18열):
      0:현장명, 1:설치위치, 2:운영현황, 3:시설용량(㎥), 4:가압장유형, 5:설치연도,
      6:펌프대수, 7:펌프양정(m), 8:펌프시공사, 9:펌프제조사, 10:펌프유형,
      11:정상운전펌프수, 12:집수정수, 13:연계배수지,
      14:1구역면적(㎡), 15:1구역높이(m), 16:2구역면적(㎡), 17:2구역높이(m)
    """
    conn = None
    try:
        content = await file.read()
        text = content.decode("utf-8-sig")
        reader = csv_mod.reader(io.StringIO(text))
        header = next(reader, None)
        if not header or len(header) < 1:
            return {"status": "ERROR", "message": "CSV 헤더 부족 (최소 1컬럼)"}

        conn = _get_conn()
        cur = conn.cursor()
        created = 0
        skipped = 0

        for row in reader:
            if len(row) < 1:
                skipped += 1
                continue
            sitename = _csv_cell(row, 0)
            if not sitename:
                skipped += 1
                continue

            # general_overview JSONB 필드 구성
            overview = {}
            v = _csv_cell(row, 1)
            if v:
                overview["install_location"] = v
            v = _csv_cell(row, 2)
            if v:
                overview["operating_status"] = v
            v = _csv_cell(row, 3)
            if v:
                overview["facility_capacity_m3"] = v
            v = _csv_cell(row, 4)
            if v:
                overview["booster_type"] = v
            v = _csv_cell(row, 5)
            if v:
                overview["install_year"] = v
            v = _csv_cell(row, 6)
            if v:
                overview["pump_count"] = v
            v = _csv_cell(row, 7)
            if v:
                overview["pump_head_m"] = v
            v = _csv_cell(row, 8)
            if v:
                overview["pump_contractor"] = v
            v = _csv_cell(row, 9)
            if v:
                overview["pump_manufacturer"] = v
            v = _csv_cell(row, 10)
            if v:
                overview["pump_type"] = v
            v = _csv_cell(row, 11)
            if v:
                overview["normal_operation_pump_count"] = v
            v = _csv_cell(row, 12)
            if v:
                overview["infiltration_well_count"] = v
            v = _csv_cell(row, 13)
            if v:
                overview["reservoir_linked"] = v

            # 직접 컬럼 (zone_*_area/height)
            zone_1_area = _csv_float(row, 14)
            zone_1_height = _csv_float(row, 15)
            zone_2_area = _csv_float(row, 16)
            zone_2_height = _csv_float(row, 17)

            cur.execute("""
                INSERT INTO tb_service_booster_info (
                    sitename, general_overview,
                    zone_1_area, zone_1_height, zone_2_area, zone_2_height
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (sitename) DO UPDATE SET
                    general_overview = EXCLUDED.general_overview,
                    zone_1_area = EXCLUDED.zone_1_area,
                    zone_1_height = EXCLUDED.zone_1_height,
                    zone_2_area = EXCLUDED.zone_2_area,
                    zone_2_height = EXCLUDED.zone_2_height
            """, (
                sitename, json.dumps(overview),
                zone_1_area, zone_1_height, zone_2_area, zone_2_height,
            ))
            created += 1

        conn.commit()
        cur.close()
        return {"status": "OK", "created": created, "skipped": skipped}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"가압장 정보 CSV 가져오기 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.post("/pressure-reducing/import/csv")
async def import_pressure_reducing_csv(file: UploadFile):
    """감압시설 정보 CSV 업로드 (일괄 입력).
    CSV 컬럼 순서 (9열):
      0:현장명, 1:설치위치, 2:운영현황, 3:PRV제조사, 4:PRV관경,
      5:PRV제어방식, 6:압력단위, 7:감압패턴, 8:감압기준
    """
    conn = None
    try:
        content = await file.read()
        text = content.decode("utf-8-sig")
        reader = csv_mod.reader(io.StringIO(text))
        header = next(reader, None)
        if not header or len(header) < 1:
            return {"status": "ERROR", "message": "CSV 헤더 부족 (최소 1컬럼)"}

        conn = _get_conn()
        cur = conn.cursor()
        created = 0
        skipped = 0

        for row in reader:
            if len(row) < 1:
                skipped += 1
                continue
            sitename = _csv_cell(row, 0)
            if not sitename:
                skipped += 1
                continue

            # general_overview JSONB — 모든 필드가 JSONB 내부
            overview = {}
            v = _csv_cell(row, 1)
            if v:
                overview["install_location"] = v
            v = _csv_cell(row, 2)
            if v:
                overview["operating_status"] = v
            v = _csv_cell(row, 3)
            if v:
                overview["prv_manufacturer"] = v
            v = _csv_cell(row, 4)
            if v:
                overview["prv_pipe_diameter"] = v
            v = _csv_cell(row, 5)
            if v:
                overview["prv_control_method"] = v
            v = _csv_cell(row, 6)
            if v:
                overview["pressure_unit"] = v
            v = _csv_cell(row, 7)
            if v:
                overview["pressure_reduction_pattern"] = v
            v = _csv_cell(row, 8)
            if v:
                overview["pressure_reduction_criteria"] = v

            cur.execute("""
                INSERT INTO tb_service_pressure_info (sitename, general_overview)
                VALUES (%s, %s)
                ON CONFLICT (sitename) DO UPDATE SET
                    general_overview = EXCLUDED.general_overview
            """, (sitename, json.dumps(overview)))
            created += 1

        conn.commit()
        cur.close()
        return {"status": "OK", "created": created, "skipped": skipped}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"감압시설 정보 CSV 가져오기 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.post("/blocks/import/csv")
async def import_blocks_csv(file: UploadFile):
    """블록 정보 CSV 업로드 (일괄 입력).
    CSV 컬럼 순서 (11열):
      0:현장명, 1:블록레벨, 2:설치위치, 3:수용가수, 4:유수율(%),
      5:관로연장(km), 6:노후관로(km), 7:대량수용가수, 8:대량수용가기준월사용량,
      9:임계압력, 10:압력단위
    """
    conn = None
    try:
        content = await file.read()
        text = content.decode("utf-8-sig")
        reader = csv_mod.reader(io.StringIO(text))
        header = next(reader, None)
        if not header or len(header) < 2:
            return {"status": "ERROR", "message": "CSV 헤더 부족 (최소 2컬럼)"}

        conn = _get_conn()
        cur = conn.cursor()
        created = 0
        skipped = 0

        for row in reader:
            if len(row) < 2:
                skipped += 1
                continue
            sitename = _csv_cell(row, 0)
            block_level = _csv_cell(row, 1)
            if not sitename or not block_level:
                skipped += 1
                continue

            # general_overview JSONB 필드 구성
            overview = {}
            v = _csv_cell(row, 2)
            if v:
                overview["install_location"] = v
            v = _csv_cell(row, 3)
            if v:
                overview["customer_count"] = v
            v = _csv_cell(row, 4)
            if v:
                overview["non_revenue_water_rate"] = v
            v = _csv_cell(row, 5)
            if v:
                overview["pipeline_total"] = v
            v = _csv_cell(row, 6)
            if v:
                overview["pipeline_old"] = v
            v = _csv_cell(row, 7)
            if v:
                overview["large_customer_count"] = v
            v = _csv_cell(row, 8)
            if v:
                overview["large_customer_base_month_usage"] = v

            # 직접 컬럼 (critical_pressure, pressure_unit)
            critical_pressure = _csv_float(row, 9)
            pressure_unit = _csv_cell(row, 10)

            cur.execute("""
                INSERT INTO tb_service_block_info (
                    sitename, block_level, general_overview,
                    critical_pressure, pressure_unit
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (sitename, block_level) DO UPDATE SET
                    general_overview = EXCLUDED.general_overview,
                    critical_pressure = EXCLUDED.critical_pressure,
                    pressure_unit = EXCLUDED.pressure_unit
            """, (
                sitename, block_level, json.dumps(overview),
                critical_pressure, pressure_unit or None,
            ))
            created += 1

        conn.commit()
        cur.close()
        return {"status": "OK", "created": created, "skipped": skipped}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"블록 정보 CSV 가져오기 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()
