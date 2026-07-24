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
_rebuild_causal_entry = None  # (sitename, facilitytype) → 인과 인덱스 부분 재구축


def init(get_db_connection_fn, rebuild_causal_entry_fn=None):
    global _get_db_connection, _rebuild_causal_entry
    _get_db_connection = get_db_connection_fn
    _rebuild_causal_entry = rebuild_causal_entry_fn


def _refresh_causal(*facilities: tuple[str, str]):
    """관계 변경 즉시 인과 인덱스 반영 — 재기동 의존 제거 (구축 고도화 ②).

    물수지·교차검증·상류추적이 쓰는 _CAUSAL_INDEX 의 upstream/downstream
    참조를 변경 시설 양쪽만 부분 재구축한다. best-effort (실패해도 CRUD 성공).
    """
    if _rebuild_causal_entry is None:
        return
    for sn, ft in facilities:
        try:
            _rebuild_causal_entry(sn, ft)
        except Exception as e:
            logger.info(f"causal 부분 재구축 건너뜀 ({sn} {ft}): {e}")


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
        _refresh_causal(
            (req["upstream_sitename"], req["upstream_facilitytype"]),
            (req["downstream_sitename"], req["downstream_facilitytype"]),
        )
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
        _refresh_causal(
            (upstream_sitename, upstream_facilitytype),
            (downstream_sitename, downstream_facilitytype),
        )
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


# =============================================================================
# [다이어그램 모드] tb_flow_diagram_node — GeoJSON 서빙 + 드래그 저장
# =============================================================================

@router.get("/flow-diagram/nodes")
async def get_flow_diagram_nodes():
    """tb_flow_diagram_node → GeoJSON FeatureCollection.

    MapLibreGL이 addSource(type: 'geojson')로 그대로 로드 가능한 포맷.
    각 feature의 properties에 group_level/box_width/display_from_z 등을
    포함해 layer filter/expression에서 활용.
    """
    conn = None
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT node_id, sitename, facilitytype, parent_node_id, group_level,
                   diagram_x, diagram_y, box_width, box_height, label_text,
                   display_from_z, display_to_z, meta
            FROM tb_flow_diagram_node
            ORDER BY group_level, sitename
            """
        )
        rows = cur.fetchall()
        cur.close()

        features = []
        for r in rows:
            (node_id, sitename, facilitytype, parent_id, level,
             x, y, w, h, label, from_z, to_z, meta) = r
            features.append({
                "type": "Feature",
                "id": int(node_id),
                "geometry": {"type": "Point", "coordinates": [float(x), float(y)]},
                "properties": {
                    "node_id": int(node_id),
                    "sitename": sitename,
                    "facilitytype": facilitytype,
                    "parent_node_id": int(parent_id) if parent_id else None,
                    "group_level": int(level),
                    "box_width": float(w),
                    "box_height": float(h),
                    "label_text": label,
                    "display_from_z": float(from_z),
                    "display_to_z": float(to_z),
                    "meta": meta or {},
                },
            })
        return {"type": "FeatureCollection", "features": features}
    except Exception as e:
        logger.error(f"flow-diagram nodes GeoJSON 실패: {e}")
        return {"type": "FeatureCollection", "features": [], "error": str(e)}
    finally:
        if conn:
            conn.close()


@router.get("/flow-diagram/edges")
async def get_flow_diagram_edges():
    """tb_facility_flow_map + tb_flow_diagram_node 조인 → LineString 배열.

    각 엣지를 (upstream 좌표) → 중간점(직각 엘보우) → (downstream 좌표)로
    구성. MapLibreGL이 addSource로 로드.
    """
    conn = None
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                fm.upstream_sitename, fm.upstream_facilitytype,
                fm.downstream_sitename, fm.downstream_facilitytype,
                fm.relation_type,
                u.diagram_x AS up_x, u.diagram_y AS up_y,
                u.group_level AS up_level,
                d.diagram_x AS dn_x, d.diagram_y AS dn_y,
                d.group_level AS dn_level
            FROM tb_facility_flow_map fm
            JOIN tb_flow_diagram_node u
                ON u.sitename = fm.upstream_sitename
               AND u.facilitytype = fm.upstream_facilitytype
            JOIN tb_flow_diagram_node d
                ON d.sitename = fm.downstream_sitename
               AND d.facilitytype = fm.downstream_facilitytype
            """
        )
        rows = cur.fetchall()
        cur.close()

        # ── bracket 패턴: 부모별로 묶어서 생성 ──
        # 1. 부모→bus_x 수평 트렁크 (1개)
        # 2. bus_x 수직 trunk (min_Y ~ max_Y, 1개)
        # 3. bus_x→자식 수평 드롭 (N개)
        from collections import defaultdict
        by_parent: dict[str, list] = defaultdict(list)
        for r in rows:
            up_key = f"{r[0]}__{r[1]}"
            by_parent[up_key].append(r)

        features = []
        for up_key, group in by_parent.items():
            first = group[0]
            ux_f, uy_f = float(first[5]), float(first[6])

            # 자식들의 좌표
            children = [(float(r[8]), float(r[9])) for r in group]
            child_ys = [cy for _, cy in children]
            min_child_x = min(cx for cx, _ in children)

            # bus_x = 부모와 가장 가까운 자식 사이 30%
            bus_x = ux_f + (min_child_x - ux_f) * 0.3

            # 1) 부모→bus_x 수평 트렁크
            features.append({
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [
                    [ux_f, uy_f], [bus_x, uy_f],
                ]},
                "properties": {
                    "upstream_sitename": first[0], "upstream_facilitytype": first[1],
                    "downstream_sitename": "_trunk_", "downstream_facilitytype": "",
                    "relation_type": first[4] or "수계",
                    "upstream_level": int(first[7]), "downstream_level": int(first[7]),
                    "min_display_z": 8.0, "edge_type": "trunk",
                },
            })

            # 2) bus_x 수직 trunk (부모 Y 포함 범위)
            all_ys = [uy_f] + child_ys
            features.append({
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [
                    [bus_x, max(all_ys)], [bus_x, min(all_ys)],
                ]},
                "properties": {
                    "upstream_sitename": first[0], "upstream_facilitytype": first[1],
                    "downstream_sitename": "_vertical_", "downstream_facilitytype": "",
                    "relation_type": first[4] or "수계",
                    "upstream_level": int(first[7]), "downstream_level": int(first[7]),
                    "min_display_z": 8.0, "edge_type": "vertical",
                },
            })

            # 3) 개별 자식 수평 드롭
            for r in group:
                dx_f, dy_f = float(r[8]), float(r[9])
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": [
                        [bus_x, dy_f], [dx_f, dy_f],
                    ]},
                    "properties": {
                        "upstream_sitename": r[0],
                        "upstream_facilitytype": r[1],
                        "downstream_sitename": r[2],
                        "downstream_facilitytype": r[3],
                        "relation_type": r[4] or "수계",
                        "upstream_level": int(r[7]),
                        "downstream_level": int(r[10]),
                        "min_display_z": 8.0,
                        "edge_type": "drop",
                    },
                })
        return {"type": "FeatureCollection", "features": features}
    except Exception as e:
        logger.error(f"flow-diagram edges GeoJSON 실패: {e}")
        return {"type": "FeatureCollection", "features": [], "error": str(e)}
    finally:
        if conn:
            conn.close()


@router.put("/flow-diagram/nodes/{node_id}")
async def update_flow_diagram_node(node_id: int, req: dict):
    """드래그 이동 후 diagram_x/y 저장 + 기타 필드 업데이트.

    Body 지원 필드: diagram_x, diagram_y, box_width, box_height,
    label_text, group_level, display_from_z, display_to_z, parent_node_id.
    """
    allowed = {
        "diagram_x", "diagram_y", "box_width", "box_height",
        "label_text", "group_level", "display_from_z", "display_to_z",
        "parent_node_id",
    }
    updates = {k: v for k, v in (req or {}).items() if k in allowed}
    if not updates:
        return {"status": "ERROR", "message": "업데이트 가능 필드 없음"}

    set_sql = ", ".join(f"{k} = %s" for k in updates.keys())
    params = list(updates.values()) + [node_id]

    conn = None
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(
            f"UPDATE tb_flow_diagram_node SET {set_sql}, updated_at = now() "
            f"WHERE node_id = %s RETURNING node_id",
            params,
        )
        row = cur.fetchone()
        if not row:
            cur.close()
            return {"status": "ERROR", "message": "node not found"}
        conn.commit()
        cur.close()
        return {"status": "OK", "node_id": int(row[0])}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"flow-diagram node PUT 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()
