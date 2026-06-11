"""
태그 마스터 API 엔드포인트 모듈

- GET /tags         — 태그 마스터 목록 (페이징+필터)
- GET /tags/filters — 태그 필터 옵션
- GET /tags/groups  — 태그 데이터 그룹 현황

ai_server.py에서 분리된 모듈 — init()으로 DB 커넥션 함수를 주입받아 사용.
"""

import logging
import os
from typing import Optional

import psycopg2
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

logger = logging.getLogger("slm")

router = APIRouter()

# ai_server.py에서 주입
_get_db_connection = None
_get_scan_cache = None      # () -> (scan_cache_dict|None, cache_time)
_get_balance_cache = None   # () -> list[edge]|None

# DB 직접 연결용 (커넥션 풀 바이패스)
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5433")
DB_NAME = os.environ.get("DB_NAME", "slm")
DB_USER = os.environ.get("DB_USER", "slm_dev")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")


def init(get_db_connection_fn, get_scan_cache_fn=None, get_balance_cache_fn=None):
    """ai_server.py에서 DB 커넥션 팩토리 + 이상감지 캐시 getter 를 주입받는다."""
    global _get_db_connection, _get_scan_cache, _get_balance_cache
    _get_db_connection = get_db_connection_fn
    _get_scan_cache = get_scan_cache_fn
    _get_balance_cache = get_balance_cache_fn


# =============================================================================
# GET /tags — 태그 마스터 목록
# =============================================================================

@router.get("/tags")
async def get_tags(
    sitename: str = Query("", description="현장명 필터"),
    facilitytype: str = Query("", description="시설유형 필터"),
    tagtype: str = Query("", description="태그유형 필터"),
    keyword: str = Query("", description="태그SN/설명 검색"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """tb_tag_info 태그 마스터 목록 조회 (페이징+필터)"""
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
        cur = conn.cursor()

        where_clauses = []
        params: list = []

        if sitename:
            where_clauses.append("sitename = %s")
            params.append(sitename)
        if facilitytype:
            where_clauses.append("facilitytype = %s")
            params.append(facilitytype)
        if tagtype:
            where_clauses.append("tagtype = %s")
            params.append(tagtype)
        if keyword:
            where_clauses.append("(tagsn ILIKE %s OR datadesc ILIKE %s OR datainfo ILIKE %s)")
            kw = f"%{keyword}%"
            params.extend([kw, kw, kw])

        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        cur.execute(f"SELECT count(*) FROM tb_tag_info{where_sql}", params)
        total = cur.fetchone()[0]

        offset = (page - 1) * page_size
        cur.execute(
            f"""SELECT tagsn, tagtype, sitename, facilitytype, equipmenttype,
                       datainfo, datadesc, unit, alarm_tag_yn
                  FROM tb_tag_info{where_sql}
                 ORDER BY sitename, facilitytype, tagsn
                 LIMIT %s OFFSET %s""",
            params + [page_size, offset],
        )
        cols = ["tagsn", "tagtype", "sitename", "facilitytype", "equipmenttype",
                "datainfo", "datadesc", "unit", "alarm_tag_yn"]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]

        for row in rows:
            if row["alarm_tag_yn"] is not None:
                row["alarm_tag_yn"] = int(row["alarm_tag_yn"])

        cur.close()
        return {
            "status": "OK",
            "data": rows,
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    except psycopg2.Error as e:
        logger.error(f"태그 목록 조회 실패: {e}")
        return {"status": "ERROR", "message": "조회에 실패했습니다.", "data": [], "total": 0}
    finally:
        if conn:
            conn.close()


# =============================================================================
# GET /tags/filters — 태그 필터 옵션
# =============================================================================

@router.get("/tags/filters")
async def get_tag_filters():
    """태그 마스터 필터 옵션 (현장명/시설유형/태그유형/장비유형 목록)"""
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
        cur = conn.cursor()
        result = {}
        for col_name in ["sitename", "facilitytype", "tagtype", "equipmenttype"]:
            cur.execute(
                f"SELECT DISTINCT {col_name} FROM tb_tag_info "
                f"WHERE {col_name} IS NOT NULL ORDER BY {col_name}"
            )
            result[col_name] = [r[0] for r in cur.fetchall()]
        cur.close()
        return {"status": "OK", "data": result}
    except psycopg2.Error as e:
        logger.error(f"태그 필터 조회 실패: {e}")
        return {"status": "ERROR", "message": "조회에 실패했습니다.", "data": {}}
    finally:
        if conn:
            conn.close()


# =============================================================================
# GET /tags/monitoring — 태그 모니터링 (현재값 + 정렬, Phase 1)
# =============================================================================

# 정렬 허용 컬럼 화이트리스트 (SQL 인젝션 방지)
_MONITORING_SORT_COLS = {
    "tagsn": "f.tagsn",
    "tagtype": "f.tagtype",
    "sitename": "f.sitename",
    "facilitytype": "f.facilitytype",
    "equipmenttype": "f.equipmenttype",
    "datainfo": "f.datainfo",
    "datadesc": "f.datadesc",
    "current_value": "l.val",
    "logtime": "l.logtime",
}

# 이상 카테고리 9종 — 코드→라벨 + 적용 단위(scope)
# DI 발(發) 설비고장/전원이상/통신이상은 제외 — 시설 전체 임의 5건에 전파돼
# 아날로그/디지털이 섞이고 기기 단위가 깨짐. 신호는 트리거 DI 태그 자체 행
# (val=1 + 알람 컬럼)에서 직접 보이므로 전파는 노이즈.
_ANOMALY_LABELS = {
    "sensor_dead": "센서 무응답",
    "data_holding": "데이터홀딩",
    "data_missing": "데이터없음",
    "cross_invalid": "교차검증 이상",
    "network_down": "네트워크단절",
    "flow_imbalance": "물수지 불균형",
}
_ANOMALY_CODES = set(_ANOMALY_LABELS)
# data_quality issue_type(한글) → 카테고리 코드
_DQ_ISSUE_TO_CODE = {
    "센서무응답": "sensor_dead",
    "데이터홀딩": "data_holding",
    "데이터없음": "data_missing",
}

# 현재값 비교 연산자 (이상/이하/초과/미만/같음/다름). 현재값 NULL 은 어떤 연산도 불일치.
_VALUE_OPS = {
    "gte": lambda v, t: v >= t,
    "lte": lambda v, t: v <= t,
    "gt": lambda v, t: v > t,
    "lt": lambda v, t: v < t,
    "eq": lambda v, t: v == t,
    "ne": lambda v, t: v != t,
}


def _build_anomaly_maps():
    """이상감지 캐시(5분 anomaly_scan + 30분 flow_balance)에서
    태그/시설 단위 카테고리 맵을 구성한다.

    Returns (tag_cats, facility_cats, ready):
      tag_cats: {tagsn: {code: detail|None}}                태그 직접 이상
      facility_cats: {(sitename, facilitytype): {code: None}} 시설 전파 이상
      ready: 캐시 준비 여부 (부팅 직후 False)
    """
    tag_cats: dict[str, dict] = {}
    facility_cats: dict[tuple, dict] = {}

    scan = _get_scan_cache() if _get_scan_cache else None
    scan_cache = scan[0] if scan else None
    if not scan_cache:
        return tag_cats, facility_cats, False

    pd = scan_cache.get("processed_data", {}) or {}

    # 1~3. 데이터 품질 (태그 단위)
    for issue in pd.get("data_quality_issues", []):
        code = _DQ_ISSUE_TO_CODE.get(issue.get("issue_type"))
        if code:
            tag_cats.setdefault(issue["tagsn"], {})[code] = issue.get("detail")

    # 5~8. 설비 장애 (태그 단위, DI 영향 태그)
    for imp in pd.get("equipment_failure_impacts", []):
        code = imp.get("failure_type")
        if code not in _ANOMALY_CODES:
            continue
        for t in imp.get("affected_tags", []):
            tag_cats.setdefault(t["tagsn"], {})[code] = imp.get("failure_detail")

    # 4. 교차검증 이상 (시설 단위 → 상·하류 시설 전파)
    for m in pd.get("cross_facility_mismatches", []):
        for sn_key, ft_key in (("upstream_sitename", "upstream_facilitytype"),
                               ("downstream_sitename", "downstream_facilitytype")):
            sn, ft = m.get(sn_key), m.get(ft_key)
            if sn and ft:
                facility_cats.setdefault((sn, ft), {})["cross_invalid"] = None

    # 9. 물수지 불균형 (시설 단위 → 상·하류 시설 전파)
    balance = _get_balance_cache() if _get_balance_cache else None
    for edge in balance or []:
        if edge.get("status") != "ok" or edge.get("grade") == "정상":
            continue
        up = (edge.get("upstream_sitename"), edge.get("upstream_facilitytype"))
        if up[0] and up[1]:
            facility_cats.setdefault(up, {})["flow_imbalance"] = None
        for d in edge.get("downstream_facilities", []):
            dk = (d.get("sitename"), d.get("facilitytype"))
            if dk[0] and dk[1]:
                facility_cats.setdefault(dk, {})["flow_imbalance"] = None

    return tag_cats, facility_cats, True


def _categories_for(tagsn, sitename, facilitytype, tag_cats, facility_cats):
    """태그 1건의 이상 카테고리 리스트(태그 직접 + 시설 전파)를 만든다."""
    out = []
    for code, detail in tag_cats.get(tagsn, {}).items():
        c = {"code": code, "label": _ANOMALY_LABELS[code], "scope": "tag"}
        if detail:
            c["detail"] = detail
        out.append(c)
    for code in facility_cats.get((sitename, facilitytype), {}):
        out.append({"code": code, "label": _ANOMALY_LABELS[code], "scope": "facility"})
    return out


@router.get("/tags/monitoring")
async def get_tags_monitoring(
    sitename: str = Query("", description="현장명 필터"),
    facilitytype: str = Query("", description="시설유형 필터"),
    tagtype: str = Query("", description="태그유형 필터"),
    keyword: str = Query("", description="태그SN/설명 검색"),
    anomaly: str = Query("", description="이상 카테고리 CSV (OR 필터)"),
    only_anomaly: bool = Query(False, description="이상 있는 태그만"),
    value_op: str = Query("", description="현재값 비교 연산자 (gte|lte|gt|lt|eq|ne)"),
    value: float | None = Query(None, description="현재값 비교 기준값"),
    sort_by: str = Query("", description="정렬 컬럼 (화이트리스트)"),
    sort_order: str = Query("asc", description="asc|desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """태그 마스터 + 현재값(tb_tag_raw_data 최신) + 이상 카테고리 조인 조회.

    이상 필터/정렬이 없으면 SQL 페이지네이션(빠른 경로), 있으면 필터 결과
    전체를 받아 이상 카테고리 부착 후 메모리에서 필터·정렬·페이지네이션한다.
    """
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
        cur = conn.cursor()

        where_clauses = []
        params: list = []
        if sitename:
            where_clauses.append("sitename = %s")
            params.append(sitename)
        if facilitytype:
            where_clauses.append("facilitytype = %s")
            params.append(facilitytype)
        if tagtype:
            where_clauses.append("tagtype = %s")
            params.append(tagtype)
        if keyword:
            where_clauses.append("(tagsn ILIKE %s OR datadesc ILIKE %s OR datainfo ILIKE %s)")
            kw = f"%{keyword}%"
            params.extend([kw, kw, kw])
        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        anomaly_codes = {c for c in (a.strip() for a in anomaly.split(",")) if c in _ANOMALY_CODES}
        anomaly_active = bool(anomaly_codes) or only_anomaly or sort_by == "anomaly_count"
        value_fn = _VALUE_OPS.get(value_op) if value is not None else None
        # 현재값 비교는 LATERAL 조인 결과(current_value) 기준 → 전체 조회 경로에서 처리
        full_scan = anomaly_active or value_fn is not None

        tag_cats, facility_cats, anomaly_ready = _build_anomaly_maps()

        # 정렬 절 (화이트리스트 + NULLS LAST). anomaly_count 는 SQL 정렬 불가 → 메모리.
        if sort_by in _MONITORING_SORT_COLS:
            direction = "DESC" if sort_order.lower() == "desc" else "ASC"
            order_sql = f"ORDER BY {_MONITORING_SORT_COLS[sort_by]} {direction} NULLS LAST, f.tagsn ASC"
        else:
            order_sql = "ORDER BY f.sitename, f.facilitytype, f.tagsn"

        cols = ["tagsn", "tagtype", "sitename", "facilitytype", "equipmenttype",
                "datainfo", "datadesc", "unit", "alarm_tag_yn",
                "current_value", "logtime"]

        # 현재값은 태그별 LATERAL(인덱스 idx_tag_raw_tagsn_time seek 1회 + LIMIT 1).
        # DISTINCT ON 은 7일 윈도우 전체(~1500만 행)를 스캔해 8s+ → LATERAL 25ms.
        select_sql = f"""
            WITH filtered AS (
                SELECT tagsn, tagtype, sitename, facilitytype, equipmenttype,
                       datainfo, datadesc, unit, alarm_tag_yn
                  FROM tb_tag_info{where_sql}
            )
            SELECT f.tagsn, f.tagtype, f.sitename, f.facilitytype, f.equipmenttype,
                   f.datainfo, f.datadesc, f.unit, f.alarm_tag_yn,
                   l.val AS current_value, l.logtime
              FROM filtered f
              LEFT JOIN LATERAL (
                  SELECT val, logtime
                    FROM tb_tag_raw_data r
                   WHERE r.tagsn = f.tagsn
                     AND r.logtime >= now() - interval '7 days'
                   ORDER BY r.logtime DESC
                   LIMIT 1
              ) l ON true
              {order_sql}
        """

        def _hydrate(row):
            r = dict(zip(cols, row))
            if r["alarm_tag_yn"] is not None:
                r["alarm_tag_yn"] = int(r["alarm_tag_yn"])
            if r["current_value"] is not None:
                r["current_value"] = float(r["current_value"])
            if r["logtime"] is not None:
                r["logtime"] = r["logtime"].isoformat()
            r["anomaly_categories"] = _categories_for(
                r["tagsn"], r["sitename"], r["facilitytype"], tag_cats, facility_cats)
            return r

        offset = (page - 1) * page_size

        if not full_scan:
            # 빠른 경로 — SQL 페이지네이션, 페이지 행에만 카테고리 부착
            cur.execute(f"SELECT count(*) FROM tb_tag_info{where_sql}", params)
            total = cur.fetchone()[0]
            cur.execute(select_sql + " LIMIT %s OFFSET %s", params + [page_size, offset])
            rows = [_hydrate(row) for row in cur.fetchall()]
        else:
            # 전체 조회 경로 — 필터 결과 전체 후 메모리 필터·정렬·페이지네이션
            cur.execute(select_sql, params)
            all_rows = [_hydrate(row) for row in cur.fetchall()]
            if only_anomaly:
                all_rows = [r for r in all_rows if r["anomaly_categories"]]
            if anomaly_codes:
                all_rows = [
                    r for r in all_rows
                    if any(c["code"] in anomaly_codes for c in r["anomaly_categories"])
                ]
            if value_fn is not None:
                all_rows = [
                    r for r in all_rows
                    if r["current_value"] is not None and value_fn(r["current_value"], value)
                ]
            if sort_by == "anomaly_count":
                rev = sort_order.lower() == "desc"
                all_rows.sort(key=lambda r: len(r["anomaly_categories"]), reverse=rev)
            total = len(all_rows)
            rows = all_rows[offset:offset + page_size]

        cur.close()
        return {
            "status": "OK",
            "data": rows,
            "total": total,
            "page": page,
            "page_size": page_size,
            "anomaly_ready": anomaly_ready,
        }
    except psycopg2.Error as e:
        logger.error(f"태그 모니터링 조회 실패: {e}")
        return {"status": "ERROR", "message": "조회에 실패했습니다.", "data": [], "total": 0}
    finally:
        if conn:
            conn.close()


# =============================================================================
# GET /tags/groups — 태그 데이터 그룹 현황
# =============================================================================

@router.get("/tags/groups")
async def get_tag_groups():
    """태그 데이터 그룹 현황 (디버그용) — 그룹별 태그 수 + 전체/분류/미분류 통계"""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT g.group_code, g.group_name, g.parent_code, g.tagtype,
                   COUNT(m.tagsn) AS tag_count
            FROM tb_tag_data_group g
            LEFT JOIN tb_tag_group_map m ON g.group_id = m.group_id
            GROUP BY g.group_id, g.group_code, g.group_name, g.parent_code, g.tagtype
            ORDER BY g.display_order
        """)
        groups = []
        for code, name, parent, tagtype, cnt in cur.fetchall():
            groups.append({
                "group_code": code,
                "group_name": name,
                "parent_code": parent,
                "tagtype": tagtype,
                "tag_count": cnt,
            })

        cur.execute("SELECT COUNT(*) FROM tb_tag_info")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM tb_tag_group_map")
        classified = cur.fetchone()[0]

        cur.close()
        return {
            "status": "OK",
            "total_tags": total,
            "classified": classified,
            "unclassified": total - classified,
            "groups": groups,
        }
    except Exception as e:
        logger.error(f"태그 그룹 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


# =============================================================================
# POST /tags — 태그 마스터 생성
# =============================================================================

class TagCreate(BaseModel):
    tagsn: str = Field(..., min_length=1, max_length=100)
    tagtype: Optional[str] = Field(None, max_length=50)
    sitename: Optional[str] = Field(None, max_length=100)
    facilitytype: Optional[str] = Field(None, max_length=50)
    equipmenttype: Optional[str] = Field(None, max_length=50)
    datainfo: Optional[str] = Field(None, max_length=200)
    datadesc: Optional[str] = Field(None, max_length=500)
    unit: Optional[str] = Field(None, max_length=30)
    alarm_tag_yn: Optional[int] = Field(0, ge=0, le=1)


@router.post("/tags", status_code=status.HTTP_201_CREATED)
async def create_tag(body: TagCreate):
    """태그 마스터에 신규 태그 등록 (tagsn UNIQUE 충돌 시 409)"""
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM tb_tag_info WHERE tagsn = %s", (body.tagsn,))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="이미 등록된 태그SN입니다")

            cur.execute(
                "INSERT INTO tb_tag_info "
                "(tagsn, tagtype, sitename, facilitytype, equipmenttype, "
                " datainfo, datadesc, unit, alarm_tag_yn) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "RETURNING tagsn, tagtype, sitename, facilitytype, equipmenttype, "
                "          datainfo, datadesc, unit, alarm_tag_yn",
                (
                    body.tagsn, body.tagtype, body.sitename, body.facilitytype,
                    body.equipmenttype, body.datainfo, body.datadesc, body.unit,
                    body.alarm_tag_yn,
                ),
            )
            row = cur.fetchone()
        conn.commit()
        return {
            "status": "OK",
            "data": {
                "tagsn": row[0],
                "tagtype": row[1],
                "sitename": row[2],
                "facilitytype": row[3],
                "equipmenttype": row[4],
                "datainfo": row[5],
                "datadesc": row[6],
                "unit": row[7],
                "alarm_tag_yn": row[8],
            },
        }
    except HTTPException:
        raise
    except psycopg2.Error as e:
        if conn:
            conn.rollback()
        logger.error(f"태그 생성 실패: {e}")
        raise HTTPException(status_code=500, detail="태그 생성에 실패했습니다")
    finally:
        if conn:
            conn.close()
