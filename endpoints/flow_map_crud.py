"""
용수 흐름 관리 CRUD API
tb_facility_flow_map 테이블 (정적 토폴로지)
실시간/노드알람 엔드포인트는 ai_server.py에 유지
"""

import csv as csv_mod
import io
import logging

from fastapi import APIRouter, UploadFile
from starlette.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["flow-map"])

_get_db_connection = None


def init(get_db_connection_fn):
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


def _get_conn():
    if _get_db_connection is None:
        raise RuntimeError("flow_map_crud not initialized")
    return _get_db_connection()


@router.get("/flow-map")
async def get_flow_maps():
    """용수 흐름 전체 조회."""
    conn = None
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT upstream_sitename, upstream_facilitytype,
                   downstream_sitename, downstream_facilitytype,
                   relation_type, description
            FROM tb_facility_flow_map
            ORDER BY upstream_facilitytype, upstream_sitename,
                     downstream_facilitytype, downstream_sitename
        """)
        rows = cur.fetchall()
        cur.close()
        data = [
            {
                "upstream_sitename": r[0],
                "upstream_facilitytype": r[1],
                "downstream_sitename": r[2],
                "downstream_facilitytype": r[3],
                "relation_type": r[4],
                "description": r[5],
            }
            for r in rows
        ]
        return {"status": "OK", "data": data, "total": len(data)}
    except Exception as e:
        logger.error(f"용수 흐름 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.get("/flow-map/roots")
async def get_flow_map_roots():
    """최상위 노드 목록 (상류에만 존재하고 하류에는 없는 노드)."""
    conn = None
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT upstream_sitename, upstream_facilitytype
            FROM tb_facility_flow_map
            WHERE (upstream_sitename, upstream_facilitytype) NOT IN (
                SELECT downstream_sitename, downstream_facilitytype
                FROM tb_facility_flow_map
            )
            ORDER BY upstream_facilitytype, upstream_sitename
        """)
        rows = cur.fetchall()
        cur.close()
        data = [
            {"sitename": r[0], "facilitytype": r[1]}
            for r in rows
        ]
        return {"status": "OK", "data": data}
    except Exception as e:
        logger.error(f"용수 흐름 루트 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.get("/flow-map/downstream")
async def get_flow_map_downstream(sitename: str, facilitytype: str):
    """특정 노드의 하류 전체 (재귀 CTE)."""
    conn = None
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("""
            WITH RECURSIVE downstream AS (
                SELECT upstream_sitename, upstream_facilitytype,
                       downstream_sitename, downstream_facilitytype,
                       relation_type, description
                FROM tb_facility_flow_map
                WHERE upstream_sitename = %s AND upstream_facilitytype = %s
                UNION
                SELECT f.upstream_sitename, f.upstream_facilitytype,
                       f.downstream_sitename, f.downstream_facilitytype,
                       f.relation_type, f.description
                FROM tb_facility_flow_map f
                JOIN downstream d
                  ON f.upstream_sitename = d.downstream_sitename
                 AND f.upstream_facilitytype = d.downstream_facilitytype
            )
            SELECT * FROM downstream
            ORDER BY upstream_facilitytype, upstream_sitename
        """, (sitename, facilitytype))
        rows = cur.fetchall()
        cur.close()
        data = [
            {
                "upstream_sitename": r[0],
                "upstream_facilitytype": r[1],
                "downstream_sitename": r[2],
                "downstream_facilitytype": r[3],
                "relation_type": r[4],
                "description": r[5],
            }
            for r in rows
        ]
        return {"status": "OK", "data": data}
    except Exception as e:
        logger.error(f"용수 흐름 하류 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.post("/flow-map")
async def create_flow_map(req: dict):
    """용수 흐름 연결 추가."""
    conn = None
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO tb_facility_flow_map
                (upstream_sitename, upstream_facilitytype,
                 downstream_sitename, downstream_facilitytype,
                 relation_type, description)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (upstream_sitename, upstream_facilitytype,
                         downstream_sitename, downstream_facilitytype)
            DO UPDATE SET
                relation_type = EXCLUDED.relation_type,
                description = EXCLUDED.description
        """, (
            req["upstream_sitename"], req["upstream_facilitytype"],
            req["downstream_sitename"], req["downstream_facilitytype"],
            req.get("relation_type", "수계"),
            req.get("description"),
        ))
        conn.commit()
        cur.close()
        return {"status": "OK"}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"용수 흐름 추가 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.delete("/flow-map")
async def delete_flow_map(
    upstream_sitename: str,
    upstream_facilitytype: str,
    downstream_sitename: str,
    downstream_facilitytype: str,
):
    """용수 흐름 연결 삭제."""
    conn = None
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("""
            DELETE FROM tb_facility_flow_map
            WHERE upstream_sitename = %s AND upstream_facilitytype = %s
              AND downstream_sitename = %s AND downstream_facilitytype = %s
        """, (
            upstream_sitename, upstream_facilitytype,
            downstream_sitename, downstream_facilitytype,
        ))
        deleted = cur.rowcount
        conn.commit()
        cur.close()
        return {"status": "OK", "deleted": deleted}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"용수 흐름 삭제 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.get("/flow-map/export/csv")
async def export_flow_map_csv():
    """용수 흐름 CSV 다운로드."""
    conn = None
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT upstream_sitename, upstream_facilitytype,
                   downstream_sitename, downstream_facilitytype,
                   relation_type, COALESCE(description, '')
            FROM tb_facility_flow_map
            ORDER BY upstream_facilitytype, upstream_sitename
        """)
        rows = cur.fetchall()
        cur.close()

        buf = io.StringIO()
        writer = csv_mod.writer(buf)
        writer.writerow([
            "상류현장명", "상류시설유형",
            "하류현장명", "하류시설유형",
            "관계유형", "설명",
        ])
        for r in rows:
            writer.writerow(r)
        buf.seek(0)

        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv; charset=utf-8-sig",
            headers={
                "Content-Disposition":
                    "attachment; filename=flow_map.csv"
            },
        )
    except Exception as e:
        logger.error(f"용수 흐름 CSV 내보내기 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.post("/flow-map/import/csv")
async def import_flow_map_csv(file: UploadFile):
    """용수 흐름 CSV 업로드 (일괄 입력)."""
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
            up_sn = row[0].strip()
            up_ft = row[1].strip()
            dn_sn = row[2].strip()
            dn_ft = row[3].strip()
            rel = row[4].strip() if len(row) > 4 and row[4].strip() else "수계"
            desc = row[5].strip() if len(row) > 5 else None

            if not up_sn or not up_ft or not dn_sn or not dn_ft:
                skipped += 1
                continue

            cur.execute("""
                INSERT INTO tb_facility_flow_map
                    (upstream_sitename, upstream_facilitytype,
                     downstream_sitename, downstream_facilitytype,
                     relation_type, description)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (upstream_sitename, upstream_facilitytype,
                             downstream_sitename, downstream_facilitytype)
                DO UPDATE SET
                    relation_type = EXCLUDED.relation_type,
                    description = EXCLUDED.description
            """, (up_sn, up_ft, dn_sn, dn_ft, rel, desc))
            created += 1

        conn.commit()
        cur.close()
        return {"status": "OK", "created": created, "skipped": skipped}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"용수 흐름 CSV 가져오기 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()
