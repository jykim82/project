"""
모니터링 카탈로그 CRUD API
tb_monitoring_catalog + tb_trend_catalog 참조
"""

import json
import logging

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter(tags=["monitoring-catalogs"])

_get_db_connection = None


def init(get_db_connection_fn):
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


def _get_conn():
    if _get_db_connection is None:
        raise RuntimeError("monitoring_catalogs not initialized")
    return _get_db_connection()


@router.get("/monitoring/catalogs/sites")
async def get_monitoring_catalog_sites(facilitytype: str = ""):
    """시설유형별 DISTINCT 사이트 목록 반환 (tb_monitoring_catalog)"""
    if not facilitytype:
        return {"status": "ERROR", "message": "facilitytype 필수"}
    ftypes = [f.strip() for f in facilitytype.split(",") if f.strip()]
    conn = None
    try:
        conn = _get_conn()
        cur = conn.cursor()
        placeholders = ",".join(["%s"] * len(ftypes))
        cur.execute(
            f"SELECT DISTINCT sitename FROM tb_tag_info WHERE facilitytype IN ({placeholders}) AND sitename IS NOT NULL ORDER BY sitename",
            ftypes,
        )
        sites = [r[0] for r in cur.fetchall()]
        return {"status": "OK", "sites": sites}
    except Exception as e:
        logger.error(f"모니터링 사이트 목록 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.get("/monitoring/catalogs/site-groups")
async def get_monitoring_site_groups(facilitytype: str = ""):
    """블록/소블록 등 하위 시설을 상류 시설 기준으로 그룹핑하여 반환"""
    if not facilitytype:
        return {"status": "ERROR", "message": "facilitytype 필수"}
    ftypes = [f.strip() for f in facilitytype.split(",") if f.strip()]
    conn = None
    try:
        conn = _get_conn()
        cur = conn.cursor()
        ph = ",".join(["%s"] * len(ftypes))
        # 상류 시설 기준 그룹핑 (재귀 BFS — 최상위 정수장/배수지까지 추적)
        cur.execute(f"""
            WITH RECURSIVE all_sites AS (
                SELECT DISTINCT sitename FROM tb_tag_info
                WHERE facilitytype IN ({ph}) AND sitename IS NOT NULL
            ),
            root_trace AS (
                -- 시드: 대상 시설의 직접 상류
                SELECT f.downstream_sitename AS sitename,
                       f.upstream_sitename AS ancestor,
                       f.upstream_facilitytype AS ancestor_ft,
                       1 AS depth
                FROM tb_facility_flow_map f
                WHERE f.downstream_facilitytype IN ({ph})
                UNION ALL
                -- 재귀: 상류의 상류를 추적 (최대 6단계)
                SELECT rt.sitename,
                       f.upstream_sitename,
                       f.upstream_facilitytype,
                       rt.depth + 1
                FROM root_trace rt
                JOIN tb_facility_flow_map f
                    ON f.downstream_sitename = rt.ancestor
                   AND f.downstream_facilitytype = rt.ancestor_ft
                WHERE rt.depth < 6
            ),
            best_root AS (
                -- 각 시설별 최상위 정수장 우선, 없으면 배수지, 없으면 가압장
                SELECT DISTINCT ON (sitename)
                    sitename,
                    ancestor || ' ' || ancestor_ft AS group_label
                FROM root_trace
                WHERE ancestor_ft IN ('정수장','배수지','가압장','감압시설')
                ORDER BY sitename,
                    CASE ancestor_ft
                        WHEN '정수장' THEN 1
                        WHEN '배수지' THEN 2
                        WHEN '가압장' THEN 3
                        ELSE 4
                    END,
                    depth DESC
            )
            SELECT a.sitename,
                   COALESCE(br.group_label, '미분류') AS group_label
            FROM all_sites a
            LEFT JOIN best_root br ON a.sitename = br.sitename
            ORDER BY group_label, a.sitename
        """, ftypes + ftypes)
        rows = cur.fetchall()
        # 그룹별 정리
        groups: dict[str, list[str]] = {}
        for sitename, group_label in rows:
            groups.setdefault(group_label, []).append(sitename)
        return {"status": "OK", "groups": groups}
    except Exception as e:
        logger.error(f"사이트 그룹핑 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.get("/monitoring/catalogs/reference")
async def get_monitoring_catalog_reference(
    facilitytype: str = "",
    sitename: str = "",
):
    """기존 트렌드 카탈로그(tb_trend_catalog) 참조 조회 — 모니터링 설정에서 태그 가져오기용"""
    if not facilitytype:
        return {"status": "ERROR", "message": "facilitytype 필수"}
    ftypes = [f.strip() for f in facilitytype.split(",") if f.strip()]
    conn = None
    try:
        conn = _get_conn()
        cur = conn.cursor()
        conditions = [f"facilitytype IN ({','.join(['%s'] * len(ftypes))})"]
        params = list(ftypes)
        if sitename:
            conditions.append("sitename = %s")
            params.append(sitename)
        where = " AND ".join(conditions)
        cols = ["trend_id", "sitename", "facilitytype", "trend_name", "meta", "description"]
        cur.execute(
            f"SELECT {', '.join(cols)} FROM tb_trend_catalog WHERE {where} ORDER BY sitename, trend_name",
            params,
        )
        rows = cur.fetchall()
        data = [dict(zip(cols, r)) for r in rows]
        return {"status": "OK", "data": data}
    except Exception as e:
        logger.error(f"카탈로그 참조 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.get("/monitoring/catalogs")
async def get_monitoring_catalogs(
    facilitytype: str = "",
    sitename: str = "",
):
    """모니터링 카탈로그 목록 조회 (tb_monitoring_catalog)"""
    if not facilitytype:
        return {"status": "ERROR", "message": "facilitytype 필수"}
    ftypes = [f.strip() for f in facilitytype.split(",") if f.strip()]
    conn = None
    try:
        conn = _get_conn()
        cur = conn.cursor()
        conditions = [f"facilitytype IN ({','.join(['%s'] * len(ftypes))})"]
        params = list(ftypes)
        if sitename:
            conditions.append("sitename = %s")
            params.append(sitename)
        where = " AND ".join(conditions)
        cols = ["catalog_id", "sitename", "facilitytype", "catalog_name", "display_order", "items", "description", "created_at", "updated_at"]
        cur.execute(
            f"SELECT {', '.join(cols)} FROM tb_monitoring_catalog WHERE {where} "
            f"ORDER BY sitename, display_order, catalog_name",
            params,
        )
        rows = cur.fetchall()
        data = []
        for r in rows:
            item = dict(zip(cols, r))
            if item.get("created_at"):
                item["created_at"] = str(item["created_at"])
            if item.get("updated_at"):
                item["updated_at"] = str(item["updated_at"])
            data.append(item)
        sites = sorted(set(item["sitename"] for item in data))
        return {"status": "OK", "data": data, "sites": sites}
    except Exception as e:
        logger.error(f"모니터링 카탈로그 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.post("/monitoring/catalogs")
async def create_monitoring_catalog(request: Request):
    """모니터링 카탈로그 생성 (tb_monitoring_catalog) — 이름 충돌 시 자동 접미사"""
    body = await request.json()
    sitename = body.get("sitename", "")
    facilitytype = body.get("facilitytype", "")
    catalog_name = body.get("catalog_name", "")
    display_order = body.get("display_order", 999)
    items = body.get("items", [])
    description = body.get("description", "")
    if not sitename or not facilitytype or not catalog_name:
        return {"status": "ERROR", "message": "sitename, facilitytype, catalog_name 필수"}
    conn = None
    try:
        conn = _get_conn()
        cur = conn.cursor()
        # 이름 충돌 확인 → 자동 접미사 부여
        final_name = catalog_name
        cur.execute(
            "SELECT catalog_name FROM tb_monitoring_catalog "
            "WHERE sitename = %s AND facilitytype = %s AND catalog_name LIKE %s",
            (sitename, facilitytype, catalog_name + "%"),
        )
        existing_names = {r[0] for r in cur.fetchall()}
        if final_name in existing_names:
            for i in range(2, 100):
                candidate = f"{catalog_name}({i})"
                if candidate not in existing_names:
                    final_name = candidate
                    break
        items_json = json.dumps(items, ensure_ascii=False)
        cur.execute(
            "INSERT INTO tb_monitoring_catalog (sitename, facilitytype, catalog_name, display_order, items, description) "
            "VALUES (%s, %s, %s, %s, %s::jsonb, %s) RETURNING catalog_id",
            (sitename, facilitytype, final_name, display_order, items_json, description),
        )
        catalog_id = cur.fetchone()[0]
        conn.commit()
        return {"status": "OK", "catalog_id": catalog_id, "catalog_name": final_name}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"모니터링 카탈로그 생성 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.put("/monitoring/catalogs/{catalog_id}")
async def update_monitoring_catalog(catalog_id: int, request: Request):
    """모니터링 카탈로그 수정 (tb_monitoring_catalog)"""
    body = await request.json()
    fields, params = [], []
    if "catalog_name" in body:
        fields.append("catalog_name = %s")
        params.append(body["catalog_name"])
    if "display_order" in body:
        fields.append("display_order = %s")
        params.append(body["display_order"])
    if "items" in body:
        fields.append("items = %s::jsonb")
        params.append(json.dumps(body["items"], ensure_ascii=False))
    if "description" in body:
        fields.append("description = %s")
        params.append(body["description"])
    if not fields:
        return {"status": "ERROR", "message": "수정할 필드 없음"}
    params.append(catalog_id)
    conn = None
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(
            f"UPDATE tb_monitoring_catalog SET {', '.join(fields)} WHERE catalog_id = %s",
            params,
        )
        conn.commit()
        return {"status": "OK"}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"모니터링 카탈로그 수정 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.delete("/monitoring/catalogs/{catalog_id}")
async def delete_monitoring_catalog(catalog_id: int):
    """모니터링 카탈로그 삭제 (tb_monitoring_catalog)"""
    conn = None
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM tb_monitoring_catalog WHERE catalog_id = %s", (catalog_id,))
        conn.commit()
        return {"status": "OK"}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"모니터링 카탈로그 삭제 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()
