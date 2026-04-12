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
