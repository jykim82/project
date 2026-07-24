"""계통도 자동 레이아웃 + 정합 lint — 구축 고도화 ②.

배경: 노드 배치가 tools/seed_flow_diagram.py 수동 실행에 갇혀 있어
관계(tb_facility_flow_map) 변경 → 다이어그램 반영 고리가 끊겨 있었다.
레이아웃 알고리즘(B안 가로배치+bracket)을 API 로 이관:
- POST /flow-diagram/relayout  mode=new_only(기존 수동 조정 보존) | full
- GET  /flow-diagram/lint      관계↔노드↔EPANET 정합 검사 (구축 검수)
"""

import logging
from collections import defaultdict, deque
from typing import Callable, Optional

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

router = APIRouter(tags=["flow-diagram-layout"])

_get_db_connection: Optional[Callable] = None
_rebuild_causal_entry: Optional[Callable] = None


def init(get_db_connection_fn, rebuild_causal_entry_fn=None):
    global _get_db_connection, _rebuild_causal_entry
    _get_db_connection = get_db_connection_fn
    _rebuild_causal_entry = rebuild_causal_entry_fn


def _conn():
    if _get_db_connection is None:
        raise RuntimeError("flow_diagram_layout not initialized")
    return _get_db_connection()


# ── 레이아웃 (tools/seed_flow_diagram.py 이관 — B안 상수 동일) ──

PARENT_X_GAP = 0.035
CHILD_X_GAP = 0.025
ROW_Y_GAP = 0.006
TREE_Y_GAP = 0.015
ORIGIN_X = 126.48
ORIGIN_Y = 36.92

FACILITY_LEVEL = {
    "정수장": 0, "댐": 0, "취수장": 0,
    "배수지": 1,
    "가압장": 2, "감압설비": 2, "감압시설": 2,
    "소블록": 3, "소소블록": 3, "블록": 3,
}
BOX_SIZES = {0: (180, 60), 1: (160, 52), 2: (140, 44), 3: (120, 36)}
DISPLAY_FROM_Z = {0: 8.0, 1: 9.5, 2: 9.5, 3: 10.0}


def _build_tree(cur):
    cur.execute(
        "SELECT upstream_sitename, upstream_facilitytype, "
        "downstream_sitename, downstream_facilitytype FROM tb_facility_flow_map")
    edges = cur.fetchall()
    all_nodes: set[tuple[str, str]] = set()
    children: dict[str, list[str]] = defaultdict(list)
    has_parent: set[str] = set()
    parent_of: dict[str, str] = {}
    for us, uf, ds, df in edges:
        uk, dk = f"{us}__{uf}", f"{ds}__{df}"
        all_nodes.add((us, uf))
        all_nodes.add((ds, df))
        children[uk].append(dk)
        has_parent.add(dk)
        parent_of.setdefault(dk, uk)
    roots = sorted(f"{s}__{f}" for (s, f) in all_nodes if f"{s}__{f}" not in has_parent)
    depth: dict[str, int] = {}
    q = deque()
    for r in roots:
        depth[r] = 0
        q.append(r)
    while q:
        n = q.popleft()
        for c in children.get(n, []):
            if c not in depth:
                depth[c] = depth[n] + 1
                q.append(c)
    for (s, f) in all_nodes:
        k = f"{s}__{f}"
        depth.setdefault(k, FACILITY_LEVEL.get(f, 2))
    return all_nodes, children, roots, depth, parent_of, edges


def _subtree_height(node, children_map, cache):
    if node in cache:
        return cache[node]
    kids = children_map.get(node, [])
    h = sum(_subtree_height(k, children_map, cache) for k in kids) if kids else ROW_Y_GAP
    cache[node] = max(h, ROW_Y_GAP)
    return cache[node]


def _place_subtree(node, x, y, children_map, positions, hcache):
    positions[node] = (x, y)
    cursor_y = y
    for kid in sorted(children_map.get(node, [])):
        _place_subtree(kid, x + PARENT_X_GAP, cursor_y, children_map, positions, hcache)
        cursor_y -= _subtree_height(kid, children_map, hcache)


def _full_layout(all_nodes, children_map, roots):
    positions: dict[str, tuple[float, float]] = {}
    hcache: dict[str, float] = {}
    y_cursor = ORIGIN_Y
    for root in roots:
        root_h = _subtree_height(root, children_map, hcache)
        _place_subtree(root, ORIGIN_X, y_cursor, children_map, positions, hcache)
        y_cursor -= root_h + TREE_Y_GAP
    return positions, y_cursor


def _node_row(sitename, facilitytype, x, y):
    lv = FACILITY_LEVEL.get(facilitytype, 2)
    w, h = BOX_SIZES.get(lv, (120, 36))
    return dict(
        sitename=sitename, facilitytype=facilitytype, group_level=lv,
        diagram_x=round(x, 6), diagram_y=round(y, 6),
        box_width=w, box_height=h,
        label_text=f"{sitename} ({facilitytype})",
        display_from_z=DISPLAY_FROM_Z.get(lv, 10.0), display_to_z=22.0,
    )


def _upsert_nodes(cur, rows) -> tuple[int, int]:
    ins = upd = 0
    for r in rows:
        cur.execute(
            """INSERT INTO tb_flow_diagram_node
                (sitename,facilitytype,group_level,diagram_x,diagram_y,
                 box_width,box_height,label_text,display_from_z,display_to_z)
            VALUES (%(sitename)s,%(facilitytype)s,%(group_level)s,
                    %(diagram_x)s,%(diagram_y)s,%(box_width)s,%(box_height)s,
                    %(label_text)s,%(display_from_z)s,%(display_to_z)s)
            ON CONFLICT (sitename,facilitytype) DO UPDATE SET
                group_level=EXCLUDED.group_level, diagram_x=EXCLUDED.diagram_x,
                diagram_y=EXCLUDED.diagram_y, box_width=EXCLUDED.box_width,
                box_height=EXCLUDED.box_height, label_text=EXCLUDED.label_text,
                display_from_z=EXCLUDED.display_from_z, display_to_z=EXCLUDED.display_to_z,
                updated_at=now()
            RETURNING (xmax=0)""", r)
        if cur.fetchone()[0]:
            ins += 1
        else:
            upd += 1
    return ins, upd


@router.post("/flow-diagram/relayout")
async def relayout(mode: str = Query("new_only", pattern="^(new_only|full)$")):
    """관계 정본 기준 노드 자동 배치.

    - new_only: 다이어그램에 없는 시설만 배치 — **기존 수동 미세조정 보존**.
      신규 노드는 상류 노드 옆(X+간격), 상류의 기존 자식들 아래에 삽입.
      상류도 미배치(신규 루트)면 전체 최저 Y 아래 새 행.
    - full: 전체 재배치 (수동 조정 소실 — 확인 후 사용)
    """
    conn = None
    try:
        conn = _conn()
        cur = conn.cursor()
        all_nodes, children, roots, depth, parent_of, _ = _build_tree(cur)

        cur.execute("SELECT sitename, facilitytype, diagram_x, diagram_y FROM tb_flow_diagram_node")
        existing = {f"{s}__{f}": (float(x), float(y)) for s, f, x, y in cur.fetchall()}

        if mode == "full":
            positions, _ = _full_layout(all_nodes, children, roots)
            rows = [_node_row(s, f, *positions[f"{s}__{f}"]) for (s, f) in all_nodes]
        else:
            missing = [(s, f) for (s, f) in all_nodes if f"{s}__{f}" not in existing]
            if not missing:
                cur.close()
                return {"status": "OK", "mode": mode, "inserted": 0, "updated": 0,
                        "message": "신규 배치 대상 없음 — 관계의 모든 시설이 이미 배치됨"}
            floor_y = min((y for _, y in existing.values()), default=ORIGIN_Y)
            rows = []
            # 깊이 순(상류 먼저) 배치 — 부모가 같은 배치 회차의 신규여도 좌표 참조 가능
            placed = dict(existing)
            for (s, f) in sorted(missing, key=lambda n: depth.get(f"{n[0]}__{n[1]}", 9)):
                k = f"{s}__{f}"
                pk = parent_of.get(k)
                if pk and pk in placed:
                    px, py = placed[pk]
                    # 부모의 기존 자식들 최저 Y 아래 (겹침 회피)
                    sib_ys = [placed[c][1] for c in children.get(pk, []) if c in placed]
                    y = (min(sib_ys) - ROW_Y_GAP) if sib_ys else py
                    x = px + PARENT_X_GAP
                else:
                    floor_y -= TREE_Y_GAP
                    x, y = ORIGIN_X, floor_y
                placed[k] = (x, y)
                rows.append(_node_row(s, f, x, y))

        ins, upd = _upsert_nodes(cur, rows)
        conn.commit()
        cur.close()
        logger.info(f"flow-diagram relayout({mode}): ins={ins} upd={upd}")
        return {"status": "OK", "mode": mode, "inserted": ins, "updated": upd}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"relayout 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.get("/flow-diagram/lint")
async def lint():
    """관계↔다이어그램↔EPANET 정합 검사 — 구축 검수용.

    - missing_nodes: 관계에 있는데 다이어그램 노드 없음 (엣지 미표시 원인)
    - orphan_nodes: 다이어그램에 있는데 관계에 없음 (고아 — 삭제 후보)
    - cycles: 순환 참조 (상류→하류가 루프 — 물수지·상류추적 오동작 원인)
    - epanet_unmapped: 관계 시설 중 EPANET 유량 매핑 없음 (참고 — B-1 주입 제외)
    """
    conn = None
    try:
        conn = _conn()
        cur = conn.cursor()
        all_nodes, children, roots, depth, _parent, edges = _build_tree(cur)
        node_keys = {f"{s}__{f}" for (s, f) in all_nodes}

        cur.execute("SELECT sitename, facilitytype FROM tb_flow_diagram_node")
        diagram = {f"{s}__{f}" for s, f in cur.fetchall()}

        missing = sorted(node_keys - diagram)
        orphan = sorted(diagram - node_keys)

        # 순환 탐지 (DFS 3색)
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {k: WHITE for k in node_keys}
        cycles: list[str] = []

        def dfs(n, path):
            color[n] = GRAY
            for c in children.get(n, []):
                if color.get(c) == GRAY:
                    cycles.append(" → ".join(path + [n, c]))
                elif color.get(c) == WHITE:
                    dfs(c, path + [n])
            color[n] = BLACK

        for k in node_keys:
            if color[k] == WHITE:
                dfs(k, [])

        cur.execute("SELECT DISTINCT sitename, facilitytype FROM tb_epanet_facility_flow_map WHERE enabled = 'Y'")
        epanet = {f"{s}__{f}" for s, f in cur.fetchall()}
        epanet_unmapped = sorted(node_keys - epanet)

        cur.close()
        ok = not missing and not orphan and not cycles
        return {
            "status": "OK", "ok": ok,
            "summary": {"nodes": len(node_keys), "diagram_nodes": len(diagram),
                        "edges": len(edges), "roots": len(roots)},
            "missing_nodes": [k.replace("__", " ") for k in missing],
            "orphan_nodes": [k.replace("__", " ") for k in orphan],
            "cycles": cycles[:10],
            "epanet_unmapped": [k.replace("__", " ") for k in epanet_unmapped],
        }
    except Exception as e:
        logger.error(f"flow-diagram lint 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()
