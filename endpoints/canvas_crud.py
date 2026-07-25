"""
캔버스 레이아웃 엔드포인트 모듈

캔버스 노드 위치, 엣지 관리, 설비-태그 링크 등
캔버스(위치도) 관련 API를 제공한다.

ai_server.py에서 분리된 모듈 — init()으로 DB 커넥션 함수를 주입받아 사용.

[일원화 v1 — docs/canvas-editor-unification-spec.md]
좌표 정본을 tb_flow_diagram_node(경위도) 로 통일. 캔버스(px)와는 본 모듈의
선형 변환 계층으로 사상 — 캔버스 편집이 실시간 계통도에 즉시 반영된다.
tb_canvas_node_position 은 폐기 (Migration 0120 rename).
"""

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from endpoints.flow_diagram_layout import (
    ORIGIN_X, ORIGIN_Y, PARENT_X_GAP, ROW_Y_GAP, _node_row,
)

logger = logging.getLogger("slm")

router = APIRouter()

# ai_server.py에서 주입
_get_db_connection = None
_rebuild_causal_entry = None

# ── px ↔ 경위도 선형 사상 (사양 §3 — 백엔드 단독 소유) ──
# 자동 배치 결과가 캔버스에서 레벨 간 240px·형제 간 56px 로 보이도록 선택
CANVAS_DEG_PER_PX_X = PARENT_X_GAP / 240
CANVAS_DEG_PER_PX_Y = ROW_Y_GAP / 56


def _deg_to_px(lon: float, lat: float) -> tuple[float, float]:
    return ((lon - ORIGIN_X) / CANVAS_DEG_PER_PX_X,
            (ORIGIN_Y - lat) / CANVAS_DEG_PER_PX_Y)


def _px_to_deg(pos_x: float, pos_y: float) -> tuple[float, float]:
    return (ORIGIN_X + pos_x * CANVAS_DEG_PER_PX_X,
            ORIGIN_Y - pos_y * CANVAS_DEG_PER_PX_Y)


def init(get_db_connection_fn, rebuild_causal_entry_fn=None):
    """ai_server.py에서 DB 커넥션 팩토리·인과 인덱스 부분 재구축 함수를 주입받는다."""
    global _get_db_connection, _rebuild_causal_entry
    _get_db_connection = get_db_connection_fn
    _rebuild_causal_entry = rebuild_causal_entry_fn


def _refresh_causal(*facilities: tuple[str, str]):
    """관계 변경 즉시 인과 인덱스 부분 재구축 — best-effort (flow_map_crud 와 동일)."""
    if _rebuild_causal_entry is None:
        return
    for sn, ft in set(facilities):
        try:
            _rebuild_causal_entry(sn, ft)
        except Exception as e:
            logger.info(f"causal 부분 재구축 건너뜀 ({sn} {ft}): {e}")


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

        # 2) 배치 정본 tb_flow_diagram_node → px 변환 (일원화 v1)
        #    관계에 있으나 배치 없는 시설은 내려주지 않음 — lint missing_nodes 로
        #    노출되고 "신규 시설 자동 배치" 버튼이 해소 경로
        cur.execute("SELECT sitename, facilitytype, diagram_x, diagram_y FROM tb_flow_diagram_node")
        pos_map = {
            (r[0], r[1]): _deg_to_px(float(r[2]), float(r[3]))
            for r in cur.fetchall()
        }
        node_set: set[tuple[str, str]] = set(pos_map)

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


class CanvasNodeKey(BaseModel):
    sitename: str
    facilitytype: str


class CanvasLayoutPayload(BaseModel):
    """명시 diff — full-diff 는 스테일 클라이언트가 관계 정본을 전멸시킬 수
    있어 폐지 (사양 §4). 프런트가 로드 스냅샷 대비 diff 를 계산해 보낸다."""
    nodes: list[CanvasNodePos] = Field(default_factory=list)
    added_edges: list[CanvasEdgePayload] = Field(default_factory=list)
    removed_edges: list[CanvasEdgePayload] = Field(default_factory=list)
    deleted_nodes: list[CanvasNodeKey] = Field(default_factory=list)


@router.put("/canvas/layout")
async def save_canvas_layout(body: CanvasLayoutPayload):
    """캔버스 저장 — 배치 정본(tb_flow_diagram_node) upsert + 관계 명시 diff.

    - 기존 노드는 좌표만 갱신 (box/label/zoom 메타 보존)
    - 신규 노드는 _node_row() 메타로 생성 (계통도 LOD 즉시 유효)
    - 관계 add/remove 후 인과 인덱스 부분 재구축 (재기동 불요)
    """
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()

        # 1) 노드 위치 upsert — px → 경위도
        for n in body.nodes:
            lon, lat = _px_to_deg(n.pos_x, n.pos_y)
            cur.execute(
                """UPDATE tb_flow_diagram_node
                   SET diagram_x=%s, diagram_y=%s, updated_at=now()
                   WHERE sitename=%s AND facilitytype=%s""",
                (round(lon, 6), round(lat, 6), n.sitename, n.facilitytype),
            )
            if cur.rowcount == 0:
                row = _node_row(n.sitename, n.facilitytype, lon, lat)
                cur.execute(
                    """INSERT INTO tb_flow_diagram_node
                        (sitename,facilitytype,group_level,diagram_x,diagram_y,
                         box_width,box_height,label_text,display_from_z,display_to_z)
                    VALUES (%(sitename)s,%(facilitytype)s,%(group_level)s,
                            %(diagram_x)s,%(diagram_y)s,%(box_width)s,%(box_height)s,
                            %(label_text)s,%(display_from_z)s,%(display_to_z)s)
                    ON CONFLICT (sitename,facilitytype) DO NOTHING""", row)

        # 2) 노드 명시 삭제
        for k in body.deleted_nodes:
            cur.execute(
                "DELETE FROM tb_flow_diagram_node WHERE sitename=%s AND facilitytype=%s",
                (k.sitename, k.facilitytype),
            )

        # 3) 관계 명시 diff
        added = removed = 0
        touched: list[tuple[str, str]] = []
        for e in body.added_edges:
            cur.execute(
                """INSERT INTO tb_facility_flow_map
                   (upstream_sitename, upstream_facilitytype,
                    downstream_sitename, downstream_facilitytype, relation_type)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT DO NOTHING""",
                (e.upstream_sitename, e.upstream_facilitytype,
                 e.downstream_sitename, e.downstream_facilitytype, e.relation_type),
            )
            added += cur.rowcount
            touched += [(e.upstream_sitename, e.upstream_facilitytype),
                        (e.downstream_sitename, e.downstream_facilitytype)]
        for e in body.removed_edges:
            cur.execute(
                """DELETE FROM tb_facility_flow_map
                   WHERE upstream_sitename=%s AND upstream_facilitytype=%s
                   AND downstream_sitename=%s AND downstream_facilitytype=%s""",
                (e.upstream_sitename, e.upstream_facilitytype,
                 e.downstream_sitename, e.downstream_facilitytype),
            )
            removed += cur.rowcount
            touched += [(e.upstream_sitename, e.upstream_facilitytype),
                        (e.downstream_sitename, e.downstream_facilitytype)]

        conn.commit()
        cur.close()
        _refresh_causal(*touched)
        return {
            "status": "OK",
            "nodes_saved": len(body.nodes),
            "nodes_deleted": len(body.deleted_nodes),
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
