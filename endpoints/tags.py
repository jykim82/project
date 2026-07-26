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

# 단일 region dev 기본값 (admin SITE_SETTING 과 동일). 멀티테넌시는 향후 JWT region.
DEFAULT_REGION = "R01"

# DI 라벨 기본 규칙 — (label_0, label_1). 오버라이드(tb_tag_di_label) 없을 때 적용.
_DI_DEFAULT_ALARM_LABELS = ("정상", "이상")    # alarm_tag_yn=1 (active-high)
_DI_DEFAULT_STATUS_LABELS = ("OFF", "ON")       # alarm_tag_yn=0 (중립 상태)


def _resolve_di_label(tagtype, current_value, alarm_tag_yn,
                      ovr_label_0, ovr_label_1, ovr_abnormal):
    """DI 태그 현재값 → (표시 라벨, 이상 여부). 아날로그/값없음은 (None, None).

    오버라이드 행이 있으면 그 라벨/극성을 쓰고, 없으면 alarm_tag_yn 기반 기본 규칙.
    abnormal 은 색상·강조용으로 None 이면 중립(상태 표시).
    """
    if tagtype != "Digital Input" or current_value is None:
        return None, None
    bit = 1 if int(round(current_value)) != 0 else 0
    if ovr_label_0 is not None and ovr_label_1 is not None:
        label = ovr_label_1 if bit == 1 else ovr_label_0
        abnormal = (ovr_abnormal == bit) if ovr_abnormal is not None else None
        return label, abnormal
    if alarm_tag_yn == 1:
        return _DI_DEFAULT_ALARM_LABELS[bit], (bit == 1)
    return _DI_DEFAULT_STATUS_LABELS[bit], None


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

# 이상 카테고리 5종 — 코드→라벨 + 적용 단위(scope). 측정 데이터(L3) 계층 전용.
# 제외 1: DI 발(發) 설비고장/전원이상/통신이상(L2) — 시설 전체 임의 5건에 전파돼
#   아날로그/디지털이 섞이고 기기 단위가 깨짐. 신호는 트리거 DI 태그 자체 행
#   (val=1 + 알람 컬럼)에서 직접 보이므로 전파는 노이즈.
# 제외 2: network_down(L1 네트워크 진단, tb_network_status ping) — ping 경로와
#   데이터 경로(TM master/LTE)는 물리적으로 달라 ping 실패해도 데이터는 정상
#   수신될 수 있음. ping 진단을 측정 태그에 전가하면 신선 데이터 태그가
#   '네트워크단절'로 오표시됨. L1 은 네트워크 건강 화면 소관 (사양 §3.0.1).
#   (equipment_failure_impacts 영향분석 로직 자체는 백엔드 보존 — 재사용 여지.)
_ANOMALY_LABELS = {
    "sensor_dead": "센서 무응답",
    "data_holding": "데이터홀딩",
    "data_missing": "데이터없음",
    "cross_invalid": "교차검증 이상",
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

    # 설비 장애 영향 태그 — 현재 L1(network_down)·L2(DI 발) 모두 _ANOMALY_CODES
    # 미포함이라 부착 대상 없음(자동 스킵). 기기 단위 카테고리 재도입 시 진입.
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
                "current_value", "logtime",
                "di_label_0", "di_label_1", "di_abnormal_value"]

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
                   l.val AS current_value, l.logtime,
                   d.label_0, d.label_1, d.abnormal_value
              FROM filtered f
              LEFT JOIN LATERAL (
                  SELECT val, logtime
                    FROM tb_tag_raw_data r
                   WHERE r.tagsn = f.tagsn
                     AND r.logtime >= now() - interval '7 days'
                   ORDER BY r.logtime DESC
                   LIMIT 1
              ) l ON true
              LEFT JOIN tb_tag_di_label d
                     ON d.tagsn = f.tagsn AND d.region = '{DEFAULT_REGION}'
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
            # DI 현재값 라벨 해석 (오버라이드 또는 기본 규칙)
            ovr_abnormal = r.pop("di_abnormal_value")
            label, abnormal = _resolve_di_label(
                r["tagtype"], r["current_value"], r["alarm_tag_yn"],
                r.pop("di_label_0"), r.pop("di_label_1"),
                int(ovr_abnormal) if ovr_abnormal is not None else None)
            r["current_value_label"] = label
            r["current_value_abnormal"] = abnormal
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
# DI 라벨 오버라이드 — GET/PUT /tags/{tagsn}/di-label
# =============================================================================

class DiLabelUpdate(BaseModel):
    label_0: str = Field(..., min_length=1, max_length=50)
    label_1: str = Field(..., min_length=1, max_length=50)
    abnormal_value: Optional[int] = Field(None, ge=0, le=1)


@router.get("/tags/{tagsn}/di-label")
async def get_di_label(tagsn: str):
    """DI 태그의 현재 라벨 설정 조회 — 오버라이드 있으면 그 값, 없으면 기본 규칙.

    다이얼로그 프리필용. is_override 로 사용자 지정 여부를 구분한다.
    """
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tagtype, datainfo, alarm_tag_yn FROM tb_tag_info WHERE tagsn = %s",
                (tagsn,))
            tag = cur.fetchone()
            if not tag:
                raise HTTPException(status_code=404, detail="태그를 찾을 수 없습니다")
            tagtype, datainfo, alarm_tag_yn = tag
            if tagtype != "Digital Input":
                raise HTTPException(status_code=400, detail="DI 태그만 라벨 설정이 가능합니다")
            alarm_tag_yn = int(alarm_tag_yn) if alarm_tag_yn is not None else 0

            cur.execute(
                "SELECT label_0, label_1, abnormal_value FROM tb_tag_di_label "
                "WHERE region = %s AND tagsn = %s",
                (DEFAULT_REGION, tagsn))
            ovr = cur.fetchone()

        if ovr:
            label_0, label_1, abnormal_value = ovr[0], ovr[1], ovr[2]
            is_override = True
        else:
            if alarm_tag_yn == 1:
                label_0, label_1 = _DI_DEFAULT_ALARM_LABELS
                abnormal_value = 1
            else:
                label_0, label_1 = _DI_DEFAULT_STATUS_LABELS
                abnormal_value = None
            is_override = False

        return {
            "status": "OK",
            "data": {
                "tagsn": tagsn,
                "datainfo": datainfo,
                "alarm_tag_yn": alarm_tag_yn,
                "label_0": label_0,
                "label_1": label_1,
                "abnormal_value": int(abnormal_value) if abnormal_value is not None else None,
                "is_override": is_override,
            },
        }
    except HTTPException:
        raise
    except psycopg2.Error as e:
        logger.error(f"DI 라벨 조회 실패 ({tagsn}): {e}")
        return {"status": "ERROR", "message": "조회에 실패했습니다."}
    finally:
        if conn:
            conn.close()


@router.put("/tags/{tagsn}/di-label")
async def put_di_label(tagsn: str, body: DiLabelUpdate):
    """DI 태그 라벨 오버라이드 upsert (예외 지정). DI 가 아니면 400."""
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tagtype FROM tb_tag_info WHERE tagsn = %s", (tagsn,))
            tag = cur.fetchone()
            if not tag:
                raise HTTPException(status_code=404, detail="태그를 찾을 수 없습니다")
            if tag[0] != "Digital Input":
                raise HTTPException(status_code=400, detail="DI 태그만 라벨 설정이 가능합니다")

            cur.execute(
                """
                INSERT INTO tb_tag_di_label
                    (region, tagsn, label_0, label_1, abnormal_value, updated_at)
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (region, tagsn) DO UPDATE SET
                    label_0 = EXCLUDED.label_0,
                    label_1 = EXCLUDED.label_1,
                    abnormal_value = EXCLUDED.abnormal_value,
                    updated_at = now()
                """,
                (DEFAULT_REGION, tagsn, body.label_0, body.label_1, body.abnormal_value))
        conn.commit()
        return {"status": "OK", "data": {"tagsn": tagsn}}
    except HTTPException:
        raise
    except psycopg2.Error as e:
        logger.error(f"DI 라벨 저장 실패 ({tagsn}): {e}")
        return {"status": "ERROR", "message": "저장에 실패했습니다."}
    finally:
        if conn:
            conn.close()


@router.delete("/tags/{tagsn}/di-label")
async def delete_di_label(tagsn: str):
    """DI 라벨 오버라이드 제거 → 기본 규칙으로 복귀."""
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM tb_tag_di_label WHERE region = %s AND tagsn = %s",
                (DEFAULT_REGION, tagsn))
        conn.commit()
        return {"status": "OK", "data": {"tagsn": tagsn}}
    except psycopg2.Error as e:
        logger.error(f"DI 라벨 삭제 실패 ({tagsn}): {e}")
        return {"status": "ERROR", "message": "삭제에 실패했습니다."}
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
# DELETE /tags/{tagsn} — 태그 마스터 삭제 (2026-07-26 UI 검증 — 삭제 경로 부재)
# =============================================================================


@router.delete("/tags/{tagsn}")
async def delete_tag(tagsn: str):
    """태그 마스터 삭제 — 오등록 정리용.

    계측 원본(tb_tag_raw_data)은 하이퍼테이블 이력이므로 삭제하지 않는다
    (재등록 시 이력 복원). 응답에 raw_rows 를 담아 프런트 confirm 이 데이터
    존재를 고지. DI 라벨 오버라이드는 함께 정리.
    """
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT EXISTS(SELECT 1 FROM tb_tag_raw_data WHERE tagsn = %s LIMIT 1)",
                (tagsn,),
            )
            has_raw = cur.fetchone()[0]
            cur.execute("DELETE FROM tb_tag_di_label WHERE tagsn = %s", (tagsn,))
            cur.execute("DELETE FROM tb_tag_info WHERE tagsn = %s", (tagsn,))
            deleted = cur.rowcount
        conn.commit()
        if deleted == 0:
            raise HTTPException(status_code=404, detail="태그를 찾을 수 없습니다")
        logger.info(f"[tags] 태그 삭제: {tagsn} (raw 데이터 존재: {has_raw})")
        return {"status": "OK", "deleted": tagsn, "had_raw_data": bool(has_raw)}
    except HTTPException:
        raise
    except psycopg2.Error as e:
        if conn:
            conn.rollback()
        logger.error(f"태그 삭제 실패: {e}")
        raise HTTPException(status_code=500, detail="태그 삭제에 실패했습니다")
    finally:
        if conn:
            conn.close()


# =============================================================================
# POST /tags — 태그 마스터 생성
# =============================================================================

class TagCreate(BaseModel):
    tagsn: str = Field(..., min_length=1, max_length=100)
    tagtype: Optional[str] = Field(None, max_length=50)
    # 필수 승격 (2026-07-26 UI 검증 발견) — 현장·시설·데이터항목 없는 태그는
    # 조회·검수·품질 어느 경로에도 못 걸리는 유령 행이 됨
    sitename: str = Field(..., min_length=1, max_length=100)
    facilitytype: str = Field(..., min_length=1, max_length=50)
    equipmenttype: Optional[str] = Field(None, max_length=50)
    datainfo: str = Field(..., min_length=1, max_length=200)
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
