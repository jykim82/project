"""
[용수흐름도 다이어그램 모드] 트리 기반 레이아웃 (그리드 배치)

레퍼런스(docs/스크린샷/계통.001.jpeg): 부모 아래에 자식을 가로 그리드로
compact 배치. 3~4개씩 한 줄, 여러 행으로 나눠 공간 효율 극대화.

좌→우 흐름 + 자식 그리드:
  정수장(좌) → 배수지 → 가압장 → 소블록(우)
  같은 부모의 자식이 많으면 세로 N개씩 묶어 X sub-column으로 분산

실행: docker exec slm-backend python3 /app/tools/seed_flow_diagram.py
"""

import os
import sys
from collections import defaultdict, deque
from typing import Optional

import psycopg2

DB_HOST = os.environ.get("DB_HOST", "timescaledb")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "slm")
DB_USER = os.environ.get("DB_USER", "slm_dev")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "slm_dev_1234")

# ── 레이아웃 상수 ──
X_STEP      = 0.045    # depth 간 X 간격
X_SUB_STEP  = 0.020    # 그리드 sub-column X 간격
Y_GAP       = 0.0040   # 같은 컬럼 내 노드 간 Y 간격
GRID_MAX_COL = 4       # 한 sub-column의 최대 노드 수 (초과 시 새 sub-column)
ORIGIN_X    = 126.48
ORIGIN_Y    = 36.92

FACILITY_LEVEL = {
    "정수장": 0, "댐": 0, "취수장": 0,
    "배수지": 1,
    "가압장": 2, "감압설비": 2, "감압시설": 2,
    "소블록": 3, "소소블록": 3, "블록": 3,
}
BOX_SIZES = {0: (180, 60), 1: (160, 52), 2: (140, 44), 3: (120, 36)}
DISPLAY_FROM_Z = {0: 8.0, 1: 9.5, 2: 9.5, 3: 10.0}


def _db():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD,
    )


def _build_tree(cur):
    cur.execute(
        "SELECT upstream_sitename, upstream_facilitytype, "
        "downstream_sitename, downstream_facilitytype "
        "FROM tb_facility_flow_map"
    )
    edges = cur.fetchall()
    all_nodes: set[tuple[str, str]] = set()
    children: dict[str, list[str]] = defaultdict(list)
    has_parent: set[str] = set()
    for us, uf, ds, df in edges:
        up_key, dn_key = f"{us}__{uf}", f"{ds}__{df}"
        all_nodes.add((us, uf))
        all_nodes.add((ds, df))
        children[up_key].append(dn_key)
        has_parent.add(dn_key)
    roots = sorted(f"{s}__{f}" for (s, f) in all_nodes if f"{s}__{f}" not in has_parent)
    # BFS depth
    depth: dict[str, int] = {}
    queue = deque()
    for r in roots:
        depth[r] = 0
        queue.append(r)
    while queue:
        node = queue.popleft()
        for child in children.get(node, []):
            if child not in depth:
                depth[child] = depth[node] + 1
                queue.append(child)
    for (s, f) in all_nodes:
        key = f"{s}__{f}"
        if key not in depth:
            depth[key] = FACILITY_LEVEL.get(f, 2)
    return all_nodes, children, roots, depth


def _subtree_leaf_count(node: str, children_map: dict[str, list[str]], cache: dict[str, int]) -> int:
    """서브트리 리프 수 (그리드 높이 계산용)."""
    if node in cache:
        return cache[node]
    kids = children_map.get(node, [])
    if not kids:
        cache[node] = 1
        return 1
    total = sum(_subtree_leaf_count(k, children_map, cache) for k in kids)
    cache[node] = total
    return total


def _layout_subtree(
    node: str,
    children_map: dict[str, list[str]],
    depth_map: dict[str, int],
    y_cursor: list[float],
    leaf_cache: dict[str, int],
) -> dict[str, tuple[float, float]]:
    """재귀 레이아웃 — 자식 그리드 배치.

    리프: y_cursor 현재 위치에 배치
    내부 노드: 자식을 GRID_MAX_COL개씩 그룹으로 나눠 sub-column 배치
    """
    positions: dict[str, tuple[float, float]] = {}
    kids = sorted(children_map.get(node, []))

    if not kids:
        d = depth_map.get(node, 0)
        x = ORIGIN_X + d * X_STEP
        y = y_cursor[0]
        positions[node] = (x, y)
        y_cursor[0] -= Y_GAP
        return positions

    # ── 자식 그리드 배치 ──
    # 리프 자식 vs 서브트리 자식 분리
    leaf_kids = [k for k in kids if not children_map.get(k)]
    branch_kids = [k for k in kids if children_map.get(k)]

    all_child_ys: list[float] = []

    # 1) 서브트리 자식은 각각 재귀 배치 (기존 방식)
    for child in branch_kids:
        sub = _layout_subtree(child, children_map, depth_map, y_cursor, leaf_cache)
        positions.update(sub)
        if child in positions:
            all_child_ys.append(positions[child][1])

    # 2) 리프 자식은 그리드로 compact 배치
    if leaf_kids:
        base_depth = depth_map.get(leaf_kids[0], 1)
        base_x = ORIGIN_X + base_depth * X_STEP

        # GRID_MAX_COL개씩 sub-column으로 분할
        for col_idx in range(0, len(leaf_kids), GRID_MAX_COL):
            group = leaf_kids[col_idx:col_idx + GRID_MAX_COL]
            sub_x = base_x + (col_idx // GRID_MAX_COL) * X_SUB_STEP
            for child in group:
                y = y_cursor[0]
                positions[child] = (sub_x, y)
                all_child_ys.append(y)
                y_cursor[0] -= Y_GAP

    # 부모는 전체 자식의 Y 중앙
    if all_child_ys:
        y_center = (max(all_child_ys) + min(all_child_ys)) / 2.0
    else:
        y_center = y_cursor[0]
        y_cursor[0] -= Y_GAP

    d = depth_map.get(node, 0)
    positions[node] = (ORIGIN_X + d * X_STEP, y_center)
    return positions


def _compute_layout(all_nodes, children, roots, depth):
    leaf_cache: dict[str, int] = {}
    positions: dict[str, tuple[float, float]] = {}
    y_cursor = [ORIGIN_Y]

    for root in roots:
        sub = _layout_subtree(root, children, depth, y_cursor, leaf_cache)
        positions.update(sub)
        y_cursor[0] -= Y_GAP * 3  # 루트 간 넓은 여유

    for (s, f) in sorted(all_nodes):
        key = f"{s}__{f}"
        if key not in positions:
            d = depth.get(key, 2)
            positions[key] = (ORIGIN_X + d * X_STEP, y_cursor[0])
            y_cursor[0] -= Y_GAP

    rows = []
    for (s, f) in all_nodes:
        key = f"{s}__{f}"
        x, y = positions.get(key, (ORIGIN_X, ORIGIN_Y))
        level = FACILITY_LEVEL.get(f, 2)
        w, h = BOX_SIZES.get(level, (120, 36))
        rows.append({
            "sitename": s, "facilitytype": f,
            "group_level": level,
            "diagram_x": round(x, 6), "diagram_y": round(y, 6),
            "box_width": w, "box_height": h,
            "label_text": f"{s} ({f})",
            "display_from_z": DISPLAY_FROM_Z.get(level, 10.0),
            "display_to_z": 22.0,
        })
    return rows


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
        if cur.fetchone()[0]:
            ins += 1
        else:
            upd += 1
    return ins, upd


def main():
    conn = _db()
    try:
        cur = conn.cursor()
        all_nodes, children, roots, depth = _build_tree(cur)
        print(f"[seed] nodes={len(all_nodes)} edges={sum(len(v) for v in children.values())} roots={len(roots)}")
        rows = _compute_layout(all_nodes, children, roots, depth)
        print(f"[seed] layout: {len(rows)} nodes")
        for lv in [0, 1, 2, 3]:
            print(f"  L{lv}: {sum(1 for r in rows if r['group_level']==lv)}")
        ins, upd = _upsert(cur, rows)
        conn.commit()
        print(f"[seed] upsert: ins={ins} upd={upd}")
        cur.close()
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
