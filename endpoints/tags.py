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

# DB 직접 연결용 (커넥션 풀 바이패스)
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5433")
DB_NAME = os.environ.get("DB_NAME", "slm")
DB_USER = os.environ.get("DB_USER", "slm_dev")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")


def init(get_db_connection_fn):
    """ai_server.py에서 DB 커넥션 팩토리 함수를 주입받는다."""
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


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


@router.get("/tags/monitoring")
async def get_tags_monitoring(
    sitename: str = Query("", description="현장명 필터"),
    facilitytype: str = Query("", description="시설유형 필터"),
    tagtype: str = Query("", description="태그유형 필터"),
    keyword: str = Query("", description="태그SN/설명 검색"),
    sort_by: str = Query("", description="정렬 컬럼 (화이트리스트)"),
    sort_order: str = Query("asc", description="asc|desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """태그 마스터 + 현재값(tb_tag_raw_data 최신) 조인 조회.

    Phase 1: 현재값/갱신시각 컬럼 + 서버 사이드 정렬/페이지네이션.
    이상 카테고리(anomaly_categories)는 Phase 2 — 현재 빈 배열 반환.
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

        cur.execute(f"SELECT count(*) FROM tb_tag_info{where_sql}", params)
        total = cur.fetchone()[0]

        # 정렬 절 (화이트리스트 + NULLS LAST)
        if sort_by in _MONITORING_SORT_COLS:
            direction = "DESC" if sort_order.lower() == "desc" else "ASC"
            order_sql = f"ORDER BY {_MONITORING_SORT_COLS[sort_by]} {direction} NULLS LAST, f.tagsn ASC"
        else:
            order_sql = "ORDER BY f.sitename, f.facilitytype, f.tagsn"

        offset = (page - 1) * page_size
        # filtered → 현재값 LEFT JOIN (DISTINCT ON 최신, 7일 윈도우 — 데이터없음 경계 일치)
        cur.execute(
            f"""
            WITH filtered AS (
                SELECT tagsn, tagtype, sitename, facilitytype, equipmenttype,
                       datainfo, datadesc, unit, alarm_tag_yn
                  FROM tb_tag_info{where_sql}
            ),
            latest AS (
                SELECT DISTINCT ON (tagsn) tagsn, val, logtime
                  FROM tb_tag_raw_data
                 WHERE tagsn IN (SELECT tagsn FROM filtered)
                   AND logtime >= now() - interval '7 days'
                 ORDER BY tagsn, logtime DESC
            )
            SELECT f.tagsn, f.tagtype, f.sitename, f.facilitytype, f.equipmenttype,
                   f.datainfo, f.datadesc, f.unit, f.alarm_tag_yn,
                   l.val AS current_value, l.logtime
              FROM filtered f
              LEFT JOIN latest l ON f.tagsn = l.tagsn
              {order_sql}
             LIMIT %s OFFSET %s
            """,
            params + [page_size, offset],
        )
        cols = ["tagsn", "tagtype", "sitename", "facilitytype", "equipmenttype",
                "datainfo", "datadesc", "unit", "alarm_tag_yn",
                "current_value", "logtime"]
        rows = []
        for row in cur.fetchall():
            r = dict(zip(cols, row))
            if r["alarm_tag_yn"] is not None:
                r["alarm_tag_yn"] = int(r["alarm_tag_yn"])
            if r["current_value"] is not None:
                r["current_value"] = float(r["current_value"])
            if r["logtime"] is not None:
                r["logtime"] = r["logtime"].isoformat()
            r["anomaly_categories"] = []  # Phase 2
            rows.append(r)

        cur.close()
        return {
            "status": "OK",
            "data": rows,
            "total": total,
            "page": page,
            "page_size": page_size,
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
