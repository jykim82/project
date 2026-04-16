"""
[B안] 가로 배치 + bracket 레이아웃 (계통.001 레퍼런스)

핵심: 같은 부모의 자식을 **가로 한 줄**로 배치 (같은 Y, X 분산).
초과 시 다음 Y 행으로 wrap. bracket 연결(수평 버스바 + 수직 드롭).
"""

import os, sys
from collections import defaultdict, deque
import psycopg2

DB_HOST = os.environ.get("DB_HOST", "timescaledb")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "slm")
DB_USER = os.environ.get("DB_USER", "slm_dev")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "slm_dev_1234")

# ── 레이아웃 상수 (넓은 간격 — 박스/라인 겹침 방지) ──
PARENT_X_GAP  = 0.055   # 부모→자식 X 간격 (넓힘)
CHILD_X_GAP   = 0.038   # 자식 간 가로 간격 (넓힘)
ROW_Y_GAP     = 0.010   # 행 wrap 시 Y 간격 (넓힘)
TREE_Y_GAP    = 0.025   # 서로 다른 계통(루트별) Y 간격 (넓힘)
MAX_PER_ROW   = 5       # 한 행 최대 자식 수 (줄임)
ORIGIN_X      = 126.48
ORIGIN_Y      = 36.92

FACILITY_LEVEL = {
    "정수장": 0, "댐": 0, "취수장": 0,
    "배수지": 1,
    "가압장": 2, "감압설비": 2, "감압시설": 2,
    "소블록": 3, "소소블록": 3, "블록": 3,
}
BOX_SIZES = {0: (180, 60), 1: (160, 52), 2: (140, 44), 3: (120, 36)}
DISPLAY_FROM_Z = {0: 8.0, 1: 9.5, 2: 9.5, 3: 10.0}


def _db():
    return psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)


def _build_tree(cur):
    cur.execute(
        "SELECT upstream_sitename, upstream_facilitytype, "
        "downstream_sitename, downstream_facilitytype FROM tb_facility_flow_map"
    )
    edges = cur.fetchall()
    all_nodes: set[tuple[str, str]] = set()
    children: dict[str, list[str]] = defaultdict(list)
    has_parent: set[str] = set()
    for us, uf, ds, df in edges:
        uk, dk = f"{us}__{uf}", f"{ds}__{df}"
        all_nodes.add((us, uf)); all_nodes.add((ds, df))
        children[uk].append(dk); has_parent.add(dk)
    roots = sorted(f"{s}__{f}" for (s, f) in all_nodes if f"{s}__{f}" not in has_parent)
    depth: dict[str, int] = {}
    q = deque()
    for r in roots:
        depth[r] = 0; q.append(r)
    while q:
        n = q.popleft()
        for c in children.get(n, []):
            if c not in depth:
                depth[c] = depth[n] + 1; q.append(c)
    for (s, f) in all_nodes:
        k = f"{s}__{f}"
        if k not in depth:
            depth[k] = FACILITY_LEVEL.get(f, 2)
    return all_nodes, children, roots, depth


def _subtree_height(node: str, children_map: dict, cache: dict) -> float:
    """서브트리가 차지하는 Y 높이 (자식들의 높이 합산).
    형제는 세로로 나열되므로 높이 = sum(자식 높이)."""
    if node in cache:
        return cache[node]
    kids = children_map.get(node, [])
    if not kids:
        cache[node] = ROW_Y_GAP
        return ROW_Y_GAP
    h = sum(_subtree_height(k, children_map, cache) for k in kids)
    cache[node] = max(h, ROW_Y_GAP)
    return cache[node]


def _layout(all_nodes, children_map, roots, depth_map):
    positions: dict[str, tuple[float, float]] = {}
    height_cache: dict[str, float] = {}
    y_cursor = ORIGIN_Y

    for root in roots:
        root_h = _subtree_height(root, children_map, height_cache)
        _place_subtree(root, ORIGIN_X, y_cursor,
                       children_map, positions, height_cache)
        y_cursor -= root_h + TREE_Y_GAP

    for (s, f) in sorted(all_nodes):
        k = f"{s}__{f}"
        if k not in positions:
            positions[k] = (ORIGIN_X + depth_map.get(k, 2) * PARENT_X_GAP, y_cursor)
            y_cursor -= ROW_Y_GAP

    rows = []
    for (s, f) in all_nodes:
        k = f"{s}__{f}"
        x, y = positions.get(k, (ORIGIN_X, ORIGIN_Y))
        lv = FACILITY_LEVEL.get(f, 2)
        w, h = BOX_SIZES.get(lv, (120, 36))
        rows.append(dict(
            sitename=s, facilitytype=f, group_level=lv,
            diagram_x=round(x, 6), diagram_y=round(y, 6),
            box_width=w, box_height=h,
            label_text=f"{s} ({f})",
            display_from_z=DISPLAY_FROM_Z.get(lv, 10.0), display_to_z=22.0,
        ))
    return rows


def _place_subtree(node, x, y, children_map, positions, hcache):
    """형제를 세로로 나열 (같은 X, 다른 Y → 병렬 관계).
    부모는 첫째 자식과 같은 Y (top-aligned)."""
    positions[node] = (x, y)
    kids = sorted(children_map.get(node, []))
    if not kids:
        return

    child_x = x + PARENT_X_GAP
    cursor_y = y
    for kid in kids:
        _place_subtree(kid, child_x, cursor_y,
                       children_map, positions, hcache)
        cursor_y -= _subtree_height(kid, children_map, hcache)


def _upsert(cur, rows):
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
        if cur.fetchone()[0]: ins += 1
        else: upd += 1
    return ins, upd


def main():
    conn = _db()
    try:
        cur = conn.cursor()
        all_nodes, children, roots, depth = _build_tree(cur)
        print(f"[seed] nodes={len(all_nodes)} edges={sum(len(v) for v in children.values())} roots={len(roots)}")
        rows = _layout(all_nodes, children, roots, depth)
        print(f"[seed] layout: {len(rows)} nodes")
        for lv in [0,1,2,3]:
            print(f"  L{lv}: {sum(1 for r in rows if r['group_level']==lv)}")
        # X/Y 범위
        xs = [r['diagram_x'] for r in rows]
        ys = [r['diagram_y'] for r in rows]
        print(f"  X: {min(xs):.3f} ~ {max(xs):.3f} (span {max(xs)-min(xs):.3f})")
        print(f"  Y: {min(ys):.3f} ~ {max(ys):.3f} (span {max(ys)-min(ys):.3f})")
        ins, upd = _upsert(cur, rows)
        conn.commit()
        print(f"[seed] upsert: ins={ins} upd={upd}")
        cur.close()
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
