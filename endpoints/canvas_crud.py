"""
캔버스 레이아웃 엔드포인트 모듈

캔버스 노드 위치, 엣지 관리, 설비-태그 링크 등
캔버스(위치도) 관련 API를 제공한다.

ai_server.py에서 분리된 모듈 — init()으로 DB 커넥션 함수를 주입받아 사용.
"""

import json
import logging

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger("slm")

router = APIRouter()

# ai_server.py에서 주입
_get_db_connection = None


def init(get_db_connection_fn):
    """ai_server.py에서 DB 커넥션 팩토리 함수를 주입받는다."""
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


# =============================================================================
# 캔버스 레이아웃 관리 (Canvas Layout)
# =============================================================================


@router.get("/canvas/layout")
async def get_canvas_layout():
    """캔버스 노드 위치 + 엣지 + 설비/태그 카운트 일괄 조회."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()

        # 1) 엣지 (기존 flow-map)
        cur.execute("""
            SELECT upstream_sitename, upstream_facilitytype,
                   downstream_sitename, downstream_facilitytype,
                   relation_type
            FROM tb_facility_flow_map
            ORDER BY upstream_facilitytype, upstream_sitename
        """)
        edges = [
            {
                "upstream_sitename": r[0], "upstream_facilitytype": r[1],
                "downstream_sitename": r[2], "downstream_facilitytype": r[3],
                "relation_type": r[4],
            }
            for r in cur.fetchall()
        ]

        # 2) 엣지에서 고유 노드 추출
        node_set: set[tuple[str, str]] = set()
        for e in edges:
            node_set.add((e["upstream_sitename"], e["upstream_facilitytype"]))
            node_set.add((e["downstream_sitename"], e["downstream_facilitytype"]))

        # 3) 저장된 위치 조회
        cur.execute("SELECT sitename, facilitytype, pos_x, pos_y FROM tb_canvas_node_position")
        pos_map = {(r[0], r[1]): (r[2], r[3]) for r in cur.fetchall()}
        # 위치 테이블에만 있는 노드도 추가
        for key in pos_map:
            node_set.add(key)

        # 4) 설비 카운트 (테이블 없으면 빈 맵)
        equip_counts: dict[tuple, int] = {}
        try:
            cur.execute("""
                SELECT sitename, facilitytype, COUNT(*) as cnt
                FROM tb_equipment_info
                GROUP BY sitename, facilitytype
            """)
            equip_counts = {(r[0], r[1]): r[2] for r in cur.fetchall()}
        except Exception as e:
            logger.warning(f"캔버스 설비 카운트 조회 실패: {e}")
            conn.rollback()

        # 5) 카탈로그 카운트 (테이블 없으면 빈 맵)
        catalog_counts: dict[tuple, int] = {}
        try:
            cur.execute("""
                SELECT sitename, facilitytype, COUNT(*) as cnt
                FROM tb_monitoring_catalog
                GROUP BY sitename, facilitytype
            """)
            catalog_counts = {(r[0], r[1]): r[2] for r in cur.fetchall()}
        except Exception as e:
            logger.warning(f"캔버스 카탈로그 카운트 조회 실패: {e}")
            conn.rollback()

        # 6) 모니터링 플래그 (테이블 없으면 빈 맵)
        monitoring_map: dict[str, bool] = {}
        try:
            cur.execute("""
                SELECT sitename, COALESCE((meta->>'monitoring')::boolean, false)
                FROM tb_admin_site_settings
            """)
            monitoring_map = {r[0]: r[1] for r in cur.fetchall()}
        except Exception as e:
            logger.warning(f"캔버스 모니터링 플래그 조회 실패: {e}")
            conn.rollback()

        cur.close()

        # 노드 응답 조합
        nodes = []
        for sn, ft in sorted(node_set):
            px, py = pos_map.get((sn, ft), (0.0, 0.0))
            nodes.append({
                "sitename": sn,
                "facilitytype": ft,
                "pos_x": px,
                "pos_y": py,
                "equipment_count": equip_counts.get((sn, ft), 0),
                "tag_group_count": catalog_counts.get((sn, ft), 0),
                "monitoring": monitoring_map.get(sn, False),
            })

        return {"status": "OK", "nodes": nodes, "edges": edges}
    except Exception as e:
        logger.error(f"캔버스 레이아웃 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


class CanvasNodePos(BaseModel):
    sitename: str
    facilitytype: str
    pos_x: float
    pos_y: float


class CanvasEdgePayload(BaseModel):
    upstream_sitename: str
    upstream_facilitytype: str
    downstream_sitename: str
    downstream_facilitytype: str
    relation_type: str = "수계"


class CanvasLayoutPayload(BaseModel):
    nodes: list[CanvasNodePos]
    edges: list[CanvasEdgePayload]


@router.put("/canvas/layout")
async def save_canvas_layout(body: CanvasLayoutPayload):
    """캔버스 노드 위치 UPSERT + 엣지 diff (추가/삭제)."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()

        # 1) 노드 위치 UPSERT
        if body.nodes:
            from psycopg2.extras import execute_values
            execute_values(
                cur,
                """
                INSERT INTO tb_canvas_node_position (sitename, facilitytype, pos_x, pos_y)
                VALUES %s
                ON CONFLICT (sitename, facilitytype) DO UPDATE
                SET pos_x = EXCLUDED.pos_x, pos_y = EXCLUDED.pos_y, updated_at = now()
                """,
                [(n.sitename, n.facilitytype, n.pos_x, n.pos_y) for n in body.nodes],
            )

        # 기존에 있지만 body에 없는 노드 위치 삭제
        new_node_keys = {(n.sitename, n.facilitytype) for n in body.nodes}
        cur.execute("SELECT sitename, facilitytype FROM tb_canvas_node_position")
        db_node_keys = {(r[0], r[1]) for r in cur.fetchall()}
        orphan_keys = db_node_keys - new_node_keys
        for sn, ft in orphan_keys:
            cur.execute(
                "DELETE FROM tb_canvas_node_position WHERE sitename=%s AND facilitytype=%s",
                (sn, ft),
            )

        # 2) 엣지 diff
        cur.execute("""
            SELECT upstream_sitename, upstream_facilitytype,
                   downstream_sitename, downstream_facilitytype
            FROM tb_facility_flow_map
        """)
        db_edges = {(r[0], r[1], r[2], r[3]) for r in cur.fetchall()}
        new_edges = {
            (e.upstream_sitename, e.upstream_facilitytype,
             e.downstream_sitename, e.downstream_facilitytype)
            for e in body.edges
        }

        # 추가할 엣지
        added = 0
        for e_key in new_edges - db_edges:
            edge_data = next(
                e for e in body.edges
                if (e.upstream_sitename, e.upstream_facilitytype,
                    e.downstream_sitename, e.downstream_facilitytype) == e_key
            )
            cur.execute(
                """INSERT INTO tb_facility_flow_map
                   (upstream_sitename, upstream_facilitytype,
                    downstream_sitename, downstream_facilitytype, relation_type)
                   VALUES (%s, %s, %s, %s, %s)""",
                (*e_key, edge_data.relation_type),
            )
            added += 1

        # 삭제할 엣지
        removed = 0
        for e_key in db_edges - new_edges:
            cur.execute(
                """DELETE FROM tb_facility_flow_map
                   WHERE upstream_sitename=%s AND upstream_facilitytype=%s
                   AND downstream_sitename=%s AND downstream_facilitytype=%s""",
                e_key,
            )
            removed += 1

        conn.commit()
        cur.close()
        return {
            "status": "OK",
            "nodes_saved": len(body.nodes),
            "edges_added": added,
            "edges_removed": removed,
        }
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"캔버스 레이아웃 저장 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.get("/canvas/node-detail/{sitename}/{facilitytype}")
async def get_canvas_node_detail(sitename: str, facilitytype: str):
    """선택 노드 상세 (설비 목록, 카탈로그, 사이트 메타)."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()

        # 설비 목록
        cur.execute("""
            SELECT equipment_id, equipmenttype, status,
                   COALESCE(meta->>'note','') as note,
                   COALESCE(meta->>'model','') as model
            FROM tb_equipment_info
            WHERE sitename=%s AND facilitytype=%s
            ORDER BY equipment_id
        """, (sitename, facilitytype))
        equipment = [
            {"equipment_id": r[0], "equipmenttype": r[1], "status": r[2],
             "note": r[3], "model": r[4]}
            for r in cur.fetchall()
        ]

        # 카탈로그 목록
        cur.execute("""
            SELECT catalog_id, catalog_name, display_order, items
            FROM tb_monitoring_catalog
            WHERE sitename=%s AND facilitytype=%s
            ORDER BY display_order
        """, (sitename, facilitytype))
        catalogs = [
            {"catalog_id": r[0], "catalog_name": r[1],
             "display_order": r[2], "items": r[3]}
            for r in cur.fetchall()
        ]

        # 사이트 메타 (테이블 없으면 빈 딕셔너리)
        site_meta: dict = {}
        try:
            cur.execute("""
                SELECT meta FROM tb_admin_site_settings WHERE sitename=%s
            """, (sitename,))
            row = cur.fetchone()
            site_meta = row[0] if row else {}
        except Exception as e:
            logger.warning(f"캔버스 사이트 메타 조회 실패 ({sitename}): {e}")
            conn.rollback()

        cur.close()
        return {
            "status": "OK",
            "equipment": equipment,
            "catalogs": catalogs,
            "site_meta": site_meta,
        }
    except Exception as e:
        logger.error(f"캔버스 노드 상세 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


# =============================================================================
# 설비↔태그 링크 (tb_equipment_tag_map) — 캔버스 UI에서 사용
# =============================================================================


@router.get("/canvas/equipment-tags/{sitename}/{facilitytype}")
async def get_equipment_tags(sitename: str, facilitytype: str):
    """설비별 연결 태그 목록 조회."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT m.equipment_id, m.tagsn,
                   COALESCE(t.datainfo, '') as datainfo,
                   COALESCE(t.unit, '') as unit,
                   COALESCE(t.datadesc, '') as datadesc
            FROM tb_equipment_tag_map m
            JOIN tb_equipment_info e ON e.equipment_id = m.equipment_id
            LEFT JOIN tb_tag_info t ON t.tagsn = m.tagsn
            WHERE e.sitename = %s AND e.facilitytype = %s
            ORDER BY m.equipment_id, m.tagsn
        """, (sitename, facilitytype))
        rows = cur.fetchall()
        cur.close()

        result: dict[str, list] = {}
        for r in rows:
            eid = r[0]
            if eid not in result:
                result[eid] = []
            result[eid].append({
                "tagsn": r[1], "datainfo": r[2],
                "unit": r[3], "datadesc": r[4],
            })

        return {"status": "OK", "equipment_tags": result}
    except Exception as e:
        logger.error(f"설비 태그 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


class EquipmentTagLinkBody(BaseModel):
    equipment_id: str
    tagsn: str


@router.post("/canvas/equipment-tag-link")
async def create_equipment_tag_link(body: EquipmentTagLinkBody):
    """설비↔태그 연결."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO tb_equipment_tag_map (equipment_id, tagsn)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
        """, (body.equipment_id, body.tagsn))
        conn.commit()
        cur.close()
        return {"status": "OK"}
    except Exception as e:
        logger.error(f"설비 태그 연결 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.delete("/canvas/equipment-tag-link")
async def delete_equipment_tag_link(equipment_id: str, tagsn: str):
    """설비↔태그 연결 해제."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            DELETE FROM tb_equipment_tag_map
            WHERE equipment_id = %s AND tagsn = %s
        """, (equipment_id, tagsn))
        conn.commit()
        cur.close()
        return {"status": "OK"}
    except Exception as e:
        logger.error(f"설비 태그 해제 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()
