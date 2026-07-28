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
from datetime import datetime, timedelta

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

# 태그 최신값 조회 창. 창이 없으면 최신 1건을 얻으려고 하이퍼테이블 전 청크를
# 훑는다(경보 이력 화면을 열 때마다 수억 행 스캔). 창을 주면 청크 제외로
# 최근 1개 청크만 본다. 설정값(AMC/LEC)·측정값(LEI) 모두 SCADA 가 상시
# 기록하므로(실측 392개 중 387개가 1일 내 갱신, 나머지 5개는 이력 자체 없음)
# 1일 창으로 잃는 값은 없다.
_LATEST_VAL_LOOKBACK = "1 day"


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

        # quality_suspect: 태그가 품질 계층(tb_tag_quality) 이상 상태 — 값 신뢰
        # 불가라 프런트가 경고 모달 대신 완화 표시 (P3. 알람 자체는 보존 —
        # feedback_no_auto_alarm_link 원칙, 노출 단계 분리만)
        cur.execute("""
            SELECT ar.tagsn,
                   ar.alarm_start_time,
                   COALESCE(ti.sitename, '알 수 없음') AS sitename,
                   COALESCE(ti.facilitytype, '') AS facilitytype,
                   COALESCE(ar.alarm_severity, '정상') AS severity,
                   COALESCE(ar.alarm_msg, ar.alarm_category || ' 알람') AS message,
                   (q.tagsn IS NOT NULL) AS quality_suspect,
                   q.reason AS quality_reason
            FROM tb_equipment_alarm_report ar
            LEFT JOIN tb_tag_info ti ON ar.tagsn = ti.tagsn
            LEFT JOIN tb_tag_quality q ON q.tagsn = ar.tagsn AND q.region = 'R01'
            WHERE ar.alarm_status = '진행중'
            ORDER BY ar.alarm_start_time DESC
            LIMIT 5
        """)
        cols = ["tagsn", "alarm_start_time", "sitename", "facilitytype", "severity",
                "message", "quality_suspect", "quality_reason"]
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
            SELECT alarm_start_time, alarm_end_time, tagsn, sitename, facilitytype,
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
            cur.execute(f"""
                SELECT DISTINCT ON (tagsn) tagsn, val
                FROM tb_tag_raw_data
                WHERE tagsn = ANY(%s)
                  AND logtime > now() - interval '{_LATEST_VAL_LOOKBACK}'
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
            cur.execute(f"""
                SELECT DISTINCT ON (tagsn) tagsn, val
                FROM tb_tag_raw_data
                WHERE tagsn = ANY(%s)
                  AND logtime > now() - interval '{_LATEST_VAL_LOOKBACK}'
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
                # 타임라인 t-시점 진행중 판정용 (GIS 스크러버 Phase 1.5) — 미해제면 null
                "endedAt": row["alarm_end_time"].strftime("%Y-%m-%d %H:%M:%S") if row.get("alarm_end_time") else None,
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
                stat,
                confirmed_by,
                TO_CHAR(confirmed_at, 'YYYY-MM-DD HH24:MI:SS') AS confirmed_at,
                anomaly_label,
                labeled_by,
                TO_CHAR(labeled_at, 'YYYY-MM-DD HH24:MI:SS') AS labeled_at,
                -- 조회 구간 안에서 같은 (현장, 경보메시지) 가 몇 번 울렸는지.
                -- 목록에서 반복 경보를 한 줄로 접고 "반복 N회" 로 보여주기 위한 값
                -- (docs/alarm-chattering-spec.md). 윈도 함수는 LIMIT 이전에
                -- 계산되므로 잘린 500행이 아니라 필터 전체 기준이다.
                COUNT(*) OVER (PARTITION BY sitename, alarm_msg) AS repeat_count
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
async def get_alarm_analysis(days: int = 90):
    """경보분석용 알람 목록 (diagnosed_msg 포함).

    days: 조회 기간(일). 기본 90일.
      - 30일 → 90일로 확장한 이유: diagnosed_msg에 검출 로직 다이어그램 HTML이
        포함된 옛 경보(2026-02 시점)가 30일 컷오프에 잘려 위기대응 화면에서
        다이어그램이 한 건도 보이지 않던 회귀를 해소.
      - days는 7~365 범위로 클램프.
    """
    conn = None
    try:
        days = max(7, min(int(days or 90), 365))
        conn = _get_db_connection()
        cur = conn.cursor()

        cur.execute(f"""
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
            WHERE alarm_start_time >= NOW() - (%s || ' days')::interval
              AND alarm_severity IS DISTINCT FROM %s
            ORDER BY alarm_start_time DESC
            LIMIT 500
        """, (days, '정상',))
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

_RANGE_BUCKETS = 8  # 스파크라인 bar 개수


def _resolve_range_window(range_key: str) -> tuple[datetime, datetime, datetime, datetime]:
    """range 키 → (period_start, period_end, yesterday_start, yesterday_end).

    - 1h/6h: 지금으로부터 trailing 윈도우
    - today: 오늘 00:00 ~ 지금 (어제는 어제 00:00 ~ 어제 같은 시각)
    - week: 최근 7일 trailing (어제는 그 이전 7일)
    DB가 `timestamp without time zone`이므로 TZ-naive 로컬 시각 사용.
    """
    now = datetime.now()
    if range_key == "1h":
        period_start = now - timedelta(hours=1)
    elif range_key == "6h":
        period_start = now - timedelta(hours=6)
    elif range_key == "week":
        period_start = now - timedelta(days=7)
    else:  # "today" 기본
        period_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    period_end = now
    # 어제 동일 윈도우 (단순히 1일 전 shift)
    yday_shift = timedelta(days=7) if range_key == "week" else timedelta(days=1)
    yday_start = period_start - yday_shift
    yday_end = period_end - yday_shift
    return period_start, period_end, yday_start, yday_end


def _bucket_category_counts(
    cur, period_start: datetime, period_end: datetime
) -> dict[str, list[int]]:
    """카테고리별 시간 버킷 count (길이 _RANGE_BUCKETS). 비어있는 버킷은 0."""
    total_seconds = (period_end - period_start).total_seconds()
    if total_seconds <= 0:
        return {}
    bucket_seconds = total_seconds / _RANGE_BUCKETS

    cur.execute(
        """
        SELECT
            COALESCE(alarm_category, '기타') AS category,
            LEAST(
                GREATEST(
                    FLOOR(EXTRACT(EPOCH FROM (alarm_start_time - %s)) / %s)::int,
                    0
                ),
                %s
            ) AS bucket_idx,
            COUNT(*) AS cnt
        FROM tb_equipment_alarm_report
        WHERE alarm_start_time >= %s AND alarm_start_time < %s
        GROUP BY category, bucket_idx
        """,
        (period_start, bucket_seconds, _RANGE_BUCKETS - 1, period_start, period_end),
    )
    hourly: dict[str, list[int]] = {}
    for category, bucket_idx, cnt in cur.fetchall():
        arr = hourly.setdefault(category, [0] * _RANGE_BUCKETS)
        arr[bucket_idx] = cnt
    return hourly


@router.get("/crisis/alarm-dashboard")
async def get_alarm_dashboard_summary(
    range: str = Query("today", description="1h|6h|today|week — 분류별/스파크라인/KPI 시간 범위"),
):
    """경보관리현황 대시보드 요약.

    기존 totalOngoing/criticalCount/warningCount/cautionCount/categorySummary/
    facilitySummary는 '진행중 전체' 기준(하위호환). 새 range 관련 필드:
      - range: 선택한 키
      - rangeCategorySummary: range 내 발생 카테고리별 count
      - rangeStats: range 내 {total, critical, unconfirmed, resolved}
      - hourlyByCategory: range를 _RANGE_BUCKETS로 나눈 카테고리별 count 배열
      - yesterdayDelta: 카테고리별 (오늘 range total - 어제 동일 range total)
    """
    if range not in {"1h", "6h", "today", "week"}:
        range = "today"
    period_start, period_end, yday_start, yday_end = _resolve_range_window(range)

    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()

        # ── 진행중 전체(기존 하위호환) ──
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

        # ── range-scoped 카테고리별 count ──
        cur.execute(
            """
            SELECT COALESCE(alarm_category, '기타') AS category, COUNT(*) AS cnt
            FROM tb_equipment_alarm_report
            WHERE alarm_start_time >= %s AND alarm_start_time < %s
            GROUP BY category
            ORDER BY cnt DESC
            """,
            (period_start, period_end),
        )
        range_category_summary = [
            {"category": row[0], "count": row[1]}
            for row in cur.fetchall()
        ]

        # ── range-scoped KPI (total / critical / unconfirmed / resolved) ──
        cur.execute(
            """
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE alarm_severity = '경고') AS critical,
                COUNT(*) FILTER (WHERE COALESCE(alarm_confirm_yn, '') <> 'Y') AS unconfirmed,
                COUNT(*) FILTER (WHERE alarm_status = '알람해제' OR alarm_end_time IS NOT NULL) AS resolved
            FROM tb_equipment_alarm_report
            WHERE alarm_start_time >= %s AND alarm_start_time < %s
            """,
            (period_start, period_end),
        )
        rs = cur.fetchone()
        range_stats = {
            "total": rs[0] or 0,
            "critical": rs[1] or 0,
            "unconfirmed": rs[2] or 0,
            "resolved": rs[3] or 0,
        }

        # ── 스파크라인 버킷 (현재 range) ──
        hourly_by_category = _bucket_category_counts(cur, period_start, period_end)

        # ── 어제 대비 델타 (카테고리별) ──
        cur.execute(
            """
            SELECT COALESCE(alarm_category, '기타') AS category, COUNT(*) AS cnt
            FROM tb_equipment_alarm_report
            WHERE alarm_start_time >= %s AND alarm_start_time < %s
            GROUP BY category
            """,
            (yday_start, yday_end),
        )
        yday_counts = {row[0]: row[1] for row in cur.fetchall()}
        today_counts = {row["category"]: row["count"] for row in range_category_summary}
        all_categories = set(today_counts) | set(yday_counts)
        yesterday_delta = {
            cat: (today_counts.get(cat, 0) - yday_counts.get(cat, 0))
            for cat in all_categories
        }

        cur.close()

        return {
            # 기존 (하위호환, '진행중 전체')
            "totalOngoing": r[0] or 0,
            "criticalCount": r[1] or 0,
            "warningCount": r[2] or 0,
            "cautionCount": r[3] or 0,
            "categorySummary": category_summary,
            "facilitySummary": facility_summary,
            # 신규 (range-scoped)
            "range": range,
            "rangeStart": period_start.isoformat(),
            "rangeEnd": period_end.isoformat(),
            "rangeCategorySummary": range_category_summary,
            "rangeStats": range_stats,
            "hourlyByCategory": hourly_by_category,
            "yesterdayDelta": yesterday_delta,
        }

    except psycopg2.Error as e:
        logger.error(f"경보관리현황 요약 조회 실패: {e}")
        return {
            "totalOngoing": 0, "criticalCount": 0, "warningCount": 0, "cautionCount": 0,
            "categorySummary": [], "facilitySummary": [],
            "range": range,
            "rangeCategorySummary": [],
            "rangeStats": {"total": 0, "critical": 0, "unconfirmed": 0, "resolved": 0},
            "hourlyByCategory": {},
            "yesterdayDelta": {},
        }
    finally:
        if conn:
            conn.close()


# =============================================================================
# PUT /crisis/alarm-reports/confirm — 경보 확인 처리
# =============================================================================

@router.put("/crisis/alarm-reports/confirm")
async def confirm_alarm_report_api(request: Request):
    """경보 확인 처리 (alarm_confirm_yn = 'Y').

    확인자·시각을 함께 기록한다 (Migration 0131) — "누가 인지했는가"의
    감사 답변이자, 교대 간 확인 책임의 증발 방지.
    이미 확인된 건은 confirmed_by/at 을 덮어쓰지 않는다 — 최초 확인자가
    책임 기록이다 (재확인 클릭이 기록을 바꾸면 감사가 무의미).
    """
    conn = None
    try:
        body = await request.json()
        tagsn = body.get("tagsn", "")
        alarm_start_time = body.get("alarm_start_time", "")
        user_id = (body.get("user_id") or "").strip()
        if not tagsn or not alarm_start_time:
            return {"status": "error", "message": "tagsn, alarm_start_time 필수"}

        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE tb_equipment_alarm_report
            SET alarm_confirm_yn = 'Y',
                confirmed_by = CASE WHEN confirmed_by IS NULL AND %s <> ''
                                    THEN %s ELSE confirmed_by END,
                confirmed_at = COALESCE(confirmed_at, NOW()),
                info_updated = TO_CHAR(NOW(), 'YYYY-MM-DD HH24:MI:SS')
            WHERE tagsn = %s AND alarm_start_time = %s::timestamp
        """, [user_id, user_id, tagsn, alarm_start_time])
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


# [E-025 P9c] 비전 점검에서 알람 해제
@router.post("/crisis/alarm-reports/resolve")
async def resolve_alarm_reports_api(request: Request):
    """복수 알람을 일괄로 해제 (alarm_end_time = NOW()).

    Body:
        {
          "alarms": [{"alarm_start_time": "...", "tagsn": "..."}],
          "resolution_note": "[비전 점검 해제] ..."
        }
    Response:
        {"status": "OK", "resolved": N}
    """
    conn = None
    try:
        body = await request.json()
        alarms: list[dict] = body.get("alarms") or []
        note: str = (body.get("resolution_note") or "").strip()
        user_id: str = (body.get("user_id") or "").strip()

        if not alarms:
            return {"status": "error", "message": "alarms 필수"}

        conn = _get_db_connection()
        cur = conn.cursor()
        resolved = 0
        for a in alarms:
            alarm_start_time = a.get("alarm_start_time")
            tagsn = a.get("tagsn")
            if not alarm_start_time or not tagsn:
                continue
            # 해제도 확인의 한 형태 — 확인자 기록 규칙은 confirm 과 동일
            # (최초 기록 보존, Migration 0131)
            cur.execute(
                """
                UPDATE tb_equipment_alarm_report
                SET alarm_end_time = NOW(),
                    alarm_confirm_yn = 'Y',
                    confirmed_by = CASE WHEN confirmed_by IS NULL AND %s <> ''
                                        THEN %s ELSE confirmed_by END,
                    confirmed_at = COALESCE(confirmed_at, NOW()),
                    user_cause_description = COALESCE(user_cause_description || ' | ', '') || %s,
                    info_updated = TO_CHAR(NOW(), 'YYYY-MM-DD HH24:MI:SS')
                WHERE tagsn = %s
                  AND alarm_start_time = %s::timestamp
                  AND alarm_end_time IS NULL
                """,
                [user_id, user_id, note or "[비전 점검 해제]", tagsn, alarm_start_time],
            )
            resolved += cur.rowcount
        conn.commit()
        cur.close()
        return {"status": "OK", "resolved": resolved}
    except psycopg2.Error as e:
        logger.error(f"알람 해제 실패: {e}")
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
                   TO_CHAR(updated_at, 'YYYY-MM-DD HH24:MI:SS') AS updated_at,
                   equipment_id, equipmenttype, fault_category, severity,
                   recorded_by, resolved_by,
                   TO_CHAR(resolved_at, 'YYYY-MM-DD HH24:MI:SS') AS resolved_at,
                   resolution_note, status, photo_urls
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
    """작업 등록. [E-025] vision_session_id 파라미터 옵셔널 지원."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        vision_session_id = body.get("vision_session_id")
        # [Migration 0045] 고장보고 카테고리면 장애 필드도 저장
        cur.execute("""
            INSERT INTO tb_task_master
                (sitename, facilitytype, task_category, task_start_time, task_end_time,
                 suspend_alarm_types, task_content, alarm_report_id, vision_session_id,
                 equipment_id, equipmenttype, fault_category, severity,
                 recorded_by, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s)
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
            vision_session_id,
            body.get("equipment_id") or None,
            body.get("equipmenttype") or None,
            body.get("fault_category") or None,
            body.get("severity") or None,
            body.get("recorded_by") or None,
            body.get("status") or "진행중",
        ))
        task_id = cur.fetchone()[0]
        # [E-025] vision_session에 linked_task_id 역방향 업데이트
        if vision_session_id:
            try:
                cur.execute(
                    "UPDATE tb_vision_session SET linked_task_id = %s WHERE vision_session_id = %s",
                    (task_id, vision_session_id),
                )
            except Exception as e:
                logger.warning(f"tb_vision_session.linked_task_id 업데이트 실패: {e}")
        conn.commit()
        cur.close()
        return {"status": "OK", "task_id": task_id, "vision_session_id": vision_session_id}
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
                equipment_id = %s, equipmenttype = %s, fault_category = %s, severity = %s,
                status = %s, resolved_by = %s, resolved_at = %s, resolution_note = %s,
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
            body.get("equipment_id") or None,
            body.get("equipmenttype") or None,
            body.get("fault_category") or None,
            body.get("severity") or None,
            body.get("status") or "진행중",
            body.get("resolved_by") or None,
            body.get("resolved_at") or None,
            body.get("resolution_note") or None,
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
