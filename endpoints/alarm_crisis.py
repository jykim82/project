"""
경보/위기관리 API 엔드포인트 모듈

- GET  /monitoring/alarm-notifications — 헤더 알람 벨
- GET  /alarm/list                     — 경보 이력
- GET  /alarm/summary                  — 경보 요약 통계
- GET  /crisis/alarm-reports           — 경보발생이력
- GET  /crisis/alarm-analysis          — 경보분석 목록
- GET  /crisis/alarm-analysis/detail   — 경보분석 단건 상세
- GET  /crisis/alarm-dashboard         — 경보관리현황 대시보드
- PUT  /crisis/alarm-reports/confirm   — 경보 확인 처리
- GET  /crisis/tasks                   — 작업 목록
- POST /crisis/tasks                   — 작업 등록
- PUT  /crisis/tasks/{task_id}         — 작업 수정
- DELETE /crisis/tasks/{task_id}       — 작업 삭제

ai_server.py에서 분리된 모듈 — init()으로 DB 커넥션 함수를 주입받아 사용.
"""

import json
import logging
import re

import psycopg2
from fastapi import APIRouter, Query, Request, Body

logger = logging.getLogger("slm")

router = APIRouter()

# ai_server.py에서 주입
_get_db_connection = None


def init(get_db_connection_fn):
    """ai_server.py에서 DB 커넥션 팩토리 함수를 주입받는다."""
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


# =============================================================================
# 헬퍼 함수
# =============================================================================

# 장비유형별 기본 unit 매핑
_EQUIP_DEFAULT_UNIT = {"수위계": "m", "압력계": "kgf/㎠", "유량계": "㎥/h"}

# 표준 알람 분류 카테고리 (개별 태그명과 구분하기 위한 기준)
_ALARM_CATEGORY_NAMES = frozenset(
    ["전체", "수위", "압력", "유량", "펌프", "밸브", "통신", "네트워크", "UPS", "수질"]
)


def _extract_alarm_level(alarm_msg: str) -> str:
    """alarm_msg에서 HH/H/L/LL 레벨 추출"""
    if not alarm_msg:
        return "H"
    if "HH" in alarm_msg:
        return "HH"
    if "LL" in alarm_msg:
        return "LL"
    if re.search(r"(?<![HL])\bH\b|H\s*상태", alarm_msg):
        return "H"
    if re.search(r"(?<![HL])\bL\b|L\s*상태", alarm_msg):
        return "L"
    return "H"


def _derive_related_tags(tagsn: str):
    """AMA/LEA 경보태그 → (설정값 태그, 측정값 태그) 도출"""
    setpoint_tag, measure_tag = None, None
    if "_AMA_" in tagsn:
        setpoint_tag = tagsn.replace("_AMA_", "_AMC_")
        parts = tagsn.split("_AMA_N0")
        if len(parts) == 2 and len(parts[1]) >= 1:
            pool = int(parts[1][0]) + 1
            measure_tag = f"{parts[0]}_LEI_N00{pool}"
    elif "_LEA_" in tagsn:
        setpoint_tag = tagsn.replace("_LEA_", "_LEC_")
        parts = tagsn.split("_LEA_N0")
        if len(parts) == 2 and len(parts[1]) >= 1:
            pool = int(parts[1][0]) + 1
            measure_tag = f"{parts[0]}_LEI_N00{pool}"
    return setpoint_tag, measure_tag


def _clean_tag_name(alarm_msg: str) -> str:
    """alarm_msg → 표시용 태그명 (접두사/레벨 제거)"""
    name = (alarm_msg or "").replace("경보_", "")
    for sfx in (" HH 상태", " LL 상태", " H 상태", " L 상태",
                 " HH", " LL"):
        name = name.replace(sfx, "")
    return name.strip()


def _get_active_task_suppressions(conn) -> list[dict]:
    """현재 진행중인 작업의 (sitename, suspend_alarm_types) 목록 반환."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT sitename, suspend_alarm_types
                FROM tb_task_master
                WHERE task_start_time <= NOW()
                  AND (task_end_time IS NULL OR task_end_time >= NOW())
                """,
            )
            rows = cur.fetchall()
        result = []
        for site, types_raw in rows:
            if isinstance(types_raw, list):
                types = types_raw
            elif isinstance(types_raw, str):
                try:
                    types = json.loads(types_raw)
                except Exception:
                    types = []
            else:
                types = []
            result.append({"sitename": site, "types": types})
        return result
    except Exception as e:
        logger.warning(f"[alarm-suppress] active task 조회 실패 (무시): {e}")
        return []


def _is_alarm_suppressed(
    sitename: str,
    alarm_category: str,
    alarm_msg: str,
    active_tasks: list[dict],
) -> bool:
    """해당 경보가 진행중인 작업에 의해 억제되는지 판정."""
    for task in active_tasks:
        if task["sitename"] != sitename:
            continue
        types: list = task.get("types", [])
        for t in types:
            if t == "전체" or t == alarm_category:
                return True
            if t not in _ALARM_CATEGORY_NAMES and alarm_msg and t in alarm_msg:
                return True
    return False


# =============================================================================
# GET /monitoring/alarm-notifications
# =============================================================================

@router.get("/monitoring/alarm-notifications")
async def get_alarm_notifications():
    """헤더 알람 벨용: 진행중 알람 건수 + 최근 5건"""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT COUNT(*) FROM tb_equipment_alarm_report
            WHERE alarm_status = '진행중'
        """)
        ongoing_count = cur.fetchone()[0] or 0

        cur.execute("""
            SELECT ar.tagsn,
                   ar.alarm_start_time,
                   COALESCE(ti.sitename, '알 수 없음') AS sitename,
                   COALESCE(ti.facilitytype, '') AS facilitytype,
                   COALESCE(ar.alarm_severity, '정상') AS severity,
                   COALESCE(ar.alarm_msg, ar.alarm_category || ' 알람') AS message
            FROM tb_equipment_alarm_report ar
            LEFT JOIN tb_tag_info ti ON ar.tagsn = ti.tagsn
            WHERE ar.alarm_status = '진행중'
            ORDER BY ar.alarm_start_time DESC
            LIMIT 5
        """)
        cols = ["tagsn", "alarm_start_time", "sitename", "facilitytype", "severity", "message"]
        items = [dict(zip(cols, row)) for row in cur.fetchall()]
        for item in items:
            if item["alarm_start_time"]:
                item["alarm_start_time"] = item["alarm_start_time"].isoformat()

        cur.close()
        return {"status": "OK", "data": {"ongoingCount": ongoing_count, "items": items}}
    except psycopg2.Error as e:
        logger.error(f"알람 알림 조회 실패: {e}")
        return {"status": "OK", "data": {"ongoingCount": 0, "items": []}}
    finally:
        if conn:
            conn.close()


# =============================================================================
# GET /alarm/list — 경보 이력
# =============================================================================

@router.get("/alarm/list")
async def get_alarm_list(
    date_from: str = "",
    date_to: str = "",
    level: str = "",
    facility: str = "",
):
    """경보 이력 조회 — 실제 측정값/설정값 포함 AlarmRecord 형식"""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()

        conditions: list[str] = []
        params: list = []
        if date_from:
            conditions.append("alarm_start_time >= %s::timestamp")
            params.append(f"{date_from} 00:00:00")
        if date_to:
            conditions.append("alarm_start_time <= %s::timestamp")
            params.append(f"{date_to} 23:59:59")
        if facility:
            conditions.append("facilitytype = %s")
            params.append(facility)
        where_sql = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        cur.execute(f"""
            SELECT alarm_start_time, tagsn, sitename, facilitytype,
                   equipmenttype, alarm_msg, alarm_status, alarm_value
            FROM tb_equipment_alarm_report
            {where_sql}
            ORDER BY alarm_start_time DESC
            LIMIT 500
        """, params)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

        # 관련 태그 매핑 수집
        sp_map: dict[str, str] = {}
        ms_map: dict[str, str] = {}
        for row in rows:
            sp, ms = _derive_related_tags(row["tagsn"])
            if sp:
                sp_map[row["tagsn"]] = sp
            if ms:
                ms_map[row["tagsn"]] = ms

        # 설정값(AMC/LEC) 배치 조회
        sp_vals: dict[str, float] = {}
        uniq_sp = list(set(sp_map.values()))
        if uniq_sp:
            cur.execute("""
                SELECT DISTINCT ON (tagsn) tagsn, val
                FROM tb_tag_raw_data
                WHERE tagsn = ANY(%s)
                ORDER BY tagsn, logtime DESC
            """, (uniq_sp,))
            for tagsn, val in cur.fetchall():
                if val is not None:
                    sp_vals[tagsn] = float(val)

        # 측정값(LEI) + unit 배치 조회
        ms_vals: dict[str, float] = {}
        ms_units: dict[str, str] = {}
        uniq_ms = list(set(ms_map.values()))
        if uniq_ms:
            cur.execute("""
                SELECT DISTINCT ON (tagsn) tagsn, val
                FROM tb_tag_raw_data
                WHERE tagsn = ANY(%s)
                ORDER BY tagsn, logtime DESC
            """, (uniq_ms,))
            for tagsn, val in cur.fetchall():
                if val is not None:
                    ms_vals[tagsn] = float(val)
            cur.execute("""
                SELECT tagsn, COALESCE(unit, '') FROM tb_tag_info
                WHERE tagsn = ANY(%s)
            """, (uniq_ms,))
            for tagsn, unit in cur.fetchall():
                ms_units[tagsn] = unit or ""

        cur.close()

        # AlarmRecord 조립
        results = []
        for idx, row in enumerate(rows):
            alarm_level = _extract_alarm_level(row["alarm_msg"])
            if level and alarm_level != level:
                continue

            tagsn = row["tagsn"]
            sp_tag = sp_map.get(tagsn)
            ms_tag = ms_map.get(tagsn)

            threshold = sp_vals.get(sp_tag) if sp_tag else None
            value = ms_vals.get(ms_tag) if ms_tag else None
            unit = ms_units.get(ms_tag, "") if ms_tag else ""

            if not unit:
                unit = _EQUIP_DEFAULT_UNIT.get(row["equipmenttype"] or "", "")

            if value is None:
                av = row["alarm_value"]
                value = float(av) if av is not None else 0

            status = "발생중" if row["alarm_status"] == "진행중" else "복귀"

            results.append({
                "id": idx + 1,
                "occurredAt": row["alarm_start_time"].strftime("%Y-%m-%d %H:%M:%S") if row["alarm_start_time"] else "",
                "siteName": row["sitename"] or "",
                "facilityType": row["facilitytype"] or "",
                "alarmLevel": alarm_level,
                "tagName": _clean_tag_name(row["alarm_msg"]),
                "value": round(value, 2),
                "threshold": round(threshold, 2) if threshold else 0,
                "unit": unit,
                "status": status,
            })

        return results

    except psycopg2.Error as e:
        logger.error(f"알람 목록 조회 실패: {e}")
        return []
    finally:
        if conn:
            conn.close()


# =============================================================================
# GET /alarm/summary — 경보 요약 통계
# =============================================================================

@router.get("/alarm/summary")
async def get_alarm_summary():
    """경보 요약 통계 (최근 30일)"""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE alarm_msg ~* 'HH') AS hh_count,
                COUNT(*) FILTER (WHERE alarm_msg ~* 'LL') AS ll_count,
                COUNT(*) FILTER (WHERE alarm_status = '알람해제') AS recovered
            FROM tb_equipment_alarm_report
            WHERE alarm_start_time >= NOW() - INTERVAL '30 days'
        """)
        total, hh, ll, recovered = cur.fetchone()
        cur.close()
        return {
            "totalCount": total or 0,
            "hhCount": (hh or 0) + (ll or 0),
            "hCount": (total or 0) - (hh or 0) - (ll or 0),
            "recoveredCount": recovered or 0,
        }
    except psycopg2.Error as e:
        logger.error(f"알람 요약 조회 실패: {e}")
        return {"totalCount": 0, "hhCount": 0, "hCount": 0, "recoveredCount": 0}
    finally:
        if conn:
            conn.close()


# =============================================================================
# GET /crisis/alarm-reports — 경보발생이력
# =============================================================================

@router.get("/crisis/alarm-reports")
async def get_alarm_reports(
    date_from: str = "",
    date_to: str = "",
    sitename: str = "",
    alarm_status: str = "",
    alarm_severity: str = "",
    alarm_category: str = "",
):
    """경보발생이력 목록 조회 (task 억제 플래그 포함)."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()

        conditions = []
        params_list: list = []

        if date_from:
            conditions.append("alarm_start_time >= %s::timestamp")
            params_list.append(f"{date_from} 00:00:00")
        if date_to:
            conditions.append("alarm_start_time <= %s::timestamp")
            params_list.append(f"{date_to} 23:59:59")
        if sitename:
            conditions.append("sitename LIKE %s")
            params_list.append(f"%{sitename}%")
        if alarm_status:
            conditions.append("alarm_status = %s")
            params_list.append(alarm_status)
        if alarm_severity:
            conditions.append("alarm_severity = %s")
            params_list.append(alarm_severity)
        if alarm_category:
            conditions.append("alarm_category = %s")
            params_list.append(alarm_category)

        where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        sql = f"""
            SELECT
                TO_CHAR(alarm_start_time, 'YYYY-MM-DD HH24:MI:SS') AS alarm_start_time,
                TO_CHAR(alarm_end_time, 'YYYY-MM-DD HH24:MI:SS') AS alarm_end_time,
                tagsn,
                COALESCE(sitename, '') AS sitename,
                COALESCE(facilitytype, '') AS facilitytype,
                COALESCE(equipmenttype, '') AS equipmenttype,
                COALESCE(equipment_id, '') AS equipment_id,
                alarm_category,
                alarm_msg,
                alarm_value,
                alarm_status,
                alarm_severity,
                diagnosed_cause,
                action_plan,
                user_cause_description,
                meta,
                COALESCE(alarm_confirm_yn, 'N') AS alarm_confirm_yn,
                countermeasure,
                COALESCE(off_alarm_confirm_yn, 'N') AS off_alarm_confirm_yn,
                is_false_alarm,
                false_alarm_notes,
                info_updated,
                COALESCE(tagtype, '') AS tagtype,
                stat
            FROM tb_equipment_alarm_report
            {where_clause}
            ORDER BY alarm_start_time DESC
            LIMIT 500
        """
        cur.execute(sql, params_list)
        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        cur.close()

        active_tasks = _get_active_task_suppressions(conn)

        results = []
        for row in rows:
            rec = {}
            for i, col in enumerate(columns):
                val = row[i]
                if col == "meta" and val is not None:
                    rec[col] = val if isinstance(val, dict) else {}
                else:
                    rec[col] = val
            rec["task_suppressed"] = _is_alarm_suppressed(
                rec.get("sitename", ""),
                rec.get("alarm_category", "") or "",
                rec.get("alarm_msg", "") or "",
                active_tasks,
            )
            results.append(rec)

        return results

    except psycopg2.Error as e:
        logger.error(f"경보발생이력 조회 실패: {e}")
        return []
    finally:
        if conn:
            conn.close()


# =============================================================================
# GET /crisis/alarm-analysis — 경보분석 목록
# =============================================================================

@router.get("/crisis/alarm-analysis")
async def get_alarm_analysis():
    """경보분석용 알람 목록 (diagnosed_msg 포함, 최근 30일)"""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT
                TO_CHAR(alarm_start_time, 'YYYY-MM-DD HH24:MI:SS') AS alarm_start_time,
                TO_CHAR(alarm_end_time, 'YYYY-MM-DD HH24:MI:SS') AS alarm_end_time,
                tagsn,
                COALESCE(sitename, '') AS sitename,
                COALESCE(facilitytype, '') AS facilitytype,
                COALESCE(equipmenttype, '') AS equipmenttype,
                COALESCE(equipment_id, '') AS equipment_id,
                alarm_category,
                alarm_msg,
                alarm_value,
                alarm_status,
                alarm_severity,
                COALESCE(meta->>'cause', diagnosed_cause) AS diagnosed_cause,
                action_plan,
                user_cause_description,
                meta,
                COALESCE(alarm_confirm_yn, 'N') AS alarm_confirm_yn,
                COALESCE(meta->>'action', countermeasure) AS countermeasure,
                COALESCE(off_alarm_confirm_yn, 'N') AS off_alarm_confirm_yn,
                is_false_alarm,
                false_alarm_notes,
                info_updated,
                COALESCE(tagtype, '') AS tagtype,
                stat,
                diagnosed_msg
            FROM tb_equipment_alarm_report
            WHERE alarm_start_time >= NOW() - INTERVAL '30 days'
              AND alarm_severity IS DISTINCT FROM %s
            ORDER BY alarm_start_time DESC
            LIMIT 500
        """, ('정상',))
        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        cur.close()

        results = []
        for row in rows:
            rec = {}
            for i, col in enumerate(columns):
                val = row[i]
                if col == "meta" and val is not None:
                    rec[col] = val if isinstance(val, dict) else {}
                else:
                    rec[col] = val
            results.append(rec)

        return results

    except psycopg2.Error as e:
        logger.error(f"경보분석 조회 실패: {e}")
        return []
    finally:
        if conn:
            conn.close()


# =============================================================================
# GET /crisis/alarm-analysis/detail — 경보분석 단건 상세
# =============================================================================

@router.get("/crisis/alarm-analysis/detail")
async def get_alarm_analysis_detail(tagsn: str, alarm_start_time: str):
    """단건 경보분석 상세 조회 (tagsn + alarm_start_time PK)"""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT
                TO_CHAR(alarm_start_time, 'YYYY-MM-DD HH24:MI:SS') AS alarm_start_time,
                TO_CHAR(alarm_end_time, 'YYYY-MM-DD HH24:MI:SS') AS alarm_end_time,
                tagsn,
                COALESCE(sitename, '') AS sitename,
                COALESCE(facilitytype, '') AS facilitytype,
                COALESCE(equipmenttype, '') AS equipmenttype,
                COALESCE(equipment_id, '') AS equipment_id,
                alarm_category, alarm_msg, alarm_value,
                alarm_status, alarm_severity,
                diagnosed_cause, action_plan, user_cause_description,
                meta,
                COALESCE(alarm_confirm_yn, 'N') AS alarm_confirm_yn,
                countermeasure,
                COALESCE(off_alarm_confirm_yn, 'N') AS off_alarm_confirm_yn,
                is_false_alarm, false_alarm_notes, info_updated,
                COALESCE(tagtype, '') AS tagtype,
                stat, diagnosed_msg
            FROM tb_equipment_alarm_report
            WHERE tagsn = %s AND alarm_start_time = %s::timestamptz
        """, (tagsn, alarm_start_time))
        columns = [desc[0] for desc in cur.description]
        row = cur.fetchone()
        cur.close()
        if not row:
            return {"status": "NOT_FOUND"}
        rec = {}
        for i, col in enumerate(columns):
            val = row[i]
            if col == "meta" and val is not None:
                rec[col] = val if isinstance(val, dict) else {}
            else:
                rec[col] = val
        return rec
    except psycopg2.Error as e:
        logger.error(f"경보분석 단건 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


# =============================================================================
# GET /crisis/alarm-dashboard — 경보관리현황 대시보드
# =============================================================================

@router.get("/crisis/alarm-dashboard")
async def get_alarm_dashboard_summary():
    """경보관리현황 대시보드 요약 (진행중 알람 집계)"""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT
                COUNT(*) AS total_ongoing,
                COUNT(*) FILTER (WHERE alarm_severity = '경고') AS critical_cnt,
                COUNT(*) FILTER (WHERE alarm_severity = '주의') AS warning_cnt,
                COUNT(*) FILTER (WHERE alarm_severity = '정상' OR alarm_severity IS NULL) AS caution_cnt
            FROM tb_equipment_alarm_report
            WHERE alarm_status = '진행중'
        """)
        r = cur.fetchone()

        cur.execute("""
            SELECT COALESCE(alarm_category, '기타') AS category, COUNT(*) AS cnt
            FROM tb_equipment_alarm_report
            WHERE alarm_status = '진행중'
            GROUP BY alarm_category
            ORDER BY cnt DESC
        """)
        category_summary = [
            {"category": row[0], "count": row[1]}
            for row in cur.fetchall()
        ]

        cur.execute("""
            SELECT COALESCE(sitename, '알 수 없음') AS sitename,
                   COALESCE(facilitytype, '') AS facilitytype,
                   COUNT(*) AS cnt
            FROM tb_equipment_alarm_report
            WHERE alarm_status = '진행중'
            GROUP BY sitename, facilitytype
            ORDER BY cnt DESC
        """)
        facility_summary = [
            {"sitename": row[0], "facilitytype": row[1], "count": row[2]}
            for row in cur.fetchall()
        ]

        cur.close()

        return {
            "totalOngoing": r[0] or 0,
            "criticalCount": r[1] or 0,
            "warningCount": r[2] or 0,
            "cautionCount": r[3] or 0,
            "categorySummary": category_summary,
            "facilitySummary": facility_summary,
        }

    except psycopg2.Error as e:
        logger.error(f"경보관리현황 요약 조회 실패: {e}")
        return {
            "totalOngoing": 0, "criticalCount": 0, "warningCount": 0, "cautionCount": 0,
            "categorySummary": [], "facilitySummary": [],
        }
    finally:
        if conn:
            conn.close()


# =============================================================================
# PUT /crisis/alarm-reports/confirm — 경보 확인 처리
# =============================================================================

@router.put("/crisis/alarm-reports/confirm")
async def confirm_alarm_report_api(request: Request):
    """경보 확인 처리 (alarm_confirm_yn = 'Y')"""
    conn = None
    try:
        body = await request.json()
        tagsn = body.get("tagsn", "")
        alarm_start_time = body.get("alarm_start_time", "")
        if not tagsn or not alarm_start_time:
            return {"status": "error", "message": "tagsn, alarm_start_time 필수"}

        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE tb_equipment_alarm_report
            SET alarm_confirm_yn = 'Y', info_updated = TO_CHAR(NOW(), 'YYYY-MM-DD HH24:MI:SS')
            WHERE tagsn = %s AND alarm_start_time = %s::timestamp
        """, [tagsn, alarm_start_time])
        conn.commit()
        cur.close()
        return {"status": "OK"}
    except psycopg2.Error as e:
        logger.error(f"경보 확인 처리 실패: {e}")
        if conn:
            conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        if conn:
            conn.close()


# =============================================================================
# 작업관리 CRUD API
# =============================================================================

@router.get("/crisis/tasks")
async def get_tasks(
    sitename: str = Query("", description="현장명 필터"),
    facilitytype: str = Query("", description="시설유형 필터"),
    task_category: str = Query("", description="카테고리 필터"),
    active_only: bool = Query(False, description="진행중만"),
    date_from: str = Query("", description="작업일자 시작"),
    date_to: str = Query("", description="작업일자 종료"),
    keyword: str = Query("", description="내용 키워드"),
):
    """작업 목록 조회."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        conditions = ["1=1"]
        params: list = []
        if sitename:
            conditions.append("sitename ILIKE %s")
            params.append(f"%{sitename}%")
        if facilitytype and facilitytype != "all":
            conditions.append("facilitytype = %s")
            params.append(facilitytype)
        if task_category and task_category != "all":
            conditions.append("task_category = %s")
            params.append(task_category)
        if active_only:
            conditions.append("(task_end_time IS NULL OR task_end_time > NOW())")
        if date_from:
            conditions.append("task_start_time >= %s::timestamptz")
            params.append(date_from)
        if date_to:
            conditions.append("(task_end_time IS NULL OR task_end_time <= %s::timestamptz + INTERVAL '1 day')")
            params.append(date_to)
        if keyword:
            conditions.append("task_content ILIKE %s")
            params.append(f"%{keyword}%")
        where = " AND ".join(conditions)
        cur.execute(f"""
            SELECT task_id, sitename, facilitytype, task_category,
                   TO_CHAR(task_start_time, 'YYYY-MM-DD HH24:MI:SS') AS task_start_time,
                   TO_CHAR(task_end_time, 'YYYY-MM-DD HH24:MI:SS') AS task_end_time,
                   suspend_alarm_types, task_content, alarm_report_id,
                   TO_CHAR(created_at, 'YYYY-MM-DD HH24:MI:SS') AS created_at,
                   TO_CHAR(updated_at, 'YYYY-MM-DD HH24:MI:SS') AS updated_at
            FROM tb_task_master
            WHERE {where}
            ORDER BY task_start_time DESC
            LIMIT 200
        """, params)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close()
        return {"status": "OK", "data": rows}
    except Exception as e:
        logger.error(f"작업 목록 조회 실패: {e}")
        return {"status": "error", "message": str(e), "data": []}
    finally:
        if conn:
            conn.close()


@router.post("/crisis/tasks")
async def create_task(body: dict = Body(...)):
    """작업 등록."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO tb_task_master
                (sitename, facilitytype, task_category, task_start_time, task_end_time,
                 suspend_alarm_types, task_content, alarm_report_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING task_id
        """, (
            body.get("sitename", ""),
            body.get("facilitytype", ""),
            body.get("task_category", "점검"),
            body.get("task_start_time"),
            body.get("task_end_time") or None,
            json.dumps(body.get("suspend_alarm_types", [])),
            body.get("task_content", ""),
            body.get("alarm_report_id") or None,
        ))
        task_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        return {"status": "OK", "task_id": task_id}
    except Exception as e:
        logger.error(f"작업 등록 실패: {e}")
        if conn:
            conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.put("/crisis/tasks/{task_id}")
async def update_task(task_id: int, body: dict = Body(...)):
    """작업 수정."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE tb_task_master SET
                sitename = %s, facilitytype = %s, task_category = %s,
                task_start_time = %s, task_end_time = %s,
                suspend_alarm_types = %s, task_content = %s,
                updated_at = NOW()
            WHERE task_id = %s
        """, (
            body.get("sitename", ""),
            body.get("facilitytype", ""),
            body.get("task_category", "점검"),
            body.get("task_start_time"),
            body.get("task_end_time") or None,
            json.dumps(body.get("suspend_alarm_types", [])),
            body.get("task_content", ""),
            task_id,
        ))
        conn.commit()
        cur.close()
        return {"status": "OK"}
    except Exception as e:
        logger.error(f"작업 수정 실패: {e}")
        if conn:
            conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.delete("/crisis/tasks/{task_id}")
async def delete_task(task_id: int):
    """작업 삭제."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM tb_task_master WHERE task_id = %s", (task_id,))
        conn.commit()
        cur.close()
        return {"status": "OK"}
    except Exception as e:
        logger.error(f"작업 삭제 실패: {e}")
        if conn:
            conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        if conn:
            conn.close()
