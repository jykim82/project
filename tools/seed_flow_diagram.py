"""
[용수흐름도 다이어그램 모드] 트리 기반 자동 레이아웃 시드

레퍼런스(docs/스크린샷): 버스바(수평 트렁크) + 수직 드롭으로 부모-자식을
직교 연결. 같은 레벨 노드가 한 컬럼에 몰리지 않고 **부모 근처에 자식 배치**.

레이아웃 알고리즘:
  1. tb_facility_flow_map에서 DAG 구성
  2. 루트(upstream에만 등장, downstream에 없는 노드) 탐색
  3. BFS로 depth 할당 + 부모-자식 트리 구성
  4. 각 서브트리의 Y 범위를 재귀적으로 분배 (자식이 부모 바로 옆)
  5. X = depth * X_STEP (좌→우 흐름)
  6. Y = 서브트리 내 재귀 균등 분배

실행:
  docker exec slm-backend python3 /app/tools/seed_flow_diagram.py
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
X_STEP = 0.045         # 깊이 간 X 간격 (lng) — 컬럼 간격 넓힘
Y_GAP  = 0.0045        # 리프 간 최소 Y 간격 (lat) — 밀집 해소 (A)
ORIGIN_X = 126.48
ORIGIN_Y = 36.90       # 최상단 시작 lat (아래로 내려감, 여유 확보)

FACILITY_LEVEL = {
    "정수장": 0, "댐": 0, "취수장": 0,
    "배수지": 1,
    "가압장": 2, "감압설비": 2, "감압시설": 2,
    "소블록": 3, "소소블록": 3, "블록": 3,
}

BOX_SIZES = {0: (180, 60), 1: (160, 52), 2: (140, 44), 3: (120, 36)}
DISPLAY_FROM_Z = {0: 8.0, 1: 10.0, 2: 11.0, 3: 12.0}

SKIP_FILENAME_PATTERNS = ["master-k"]

EMBEDDINGS_DIR = os.environ.get(
    "EMBEDDINGS_DIR",
    os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "manual_embeddings")),
)
MANUALS_DEST_DIR = os.environ.get(
    "MANUALS_DEST_DIR",
    os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "manuals")),
)
MIN_PAGE_CHARS = 100


def _db():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD,
    )


def _build_tree(cur):
    """DAG → 트리 구성. 부모-자식 관계 + 루트 식별."""
    cur.execute(
        """
        SELECT upstream_sitename, upstream_facilitytype,
               downstream_sitename, downstream_facilitytype
        FROM tb_facility_flow_map
        """
    )
    edges = cur.fetchall()

    # 모든 노드 수집
    all_nodes: set[tuple[str, str]] = set()
    children: dict[str, list[str]] = defaultdict(list)  # parent_key → [child_key]
    has_parent: set[str] = set()

    for us, uf, ds, df in edges:
        up_key = f"{us}__{uf}"
        dn_key = f"{ds}__{df}"
        all_nodes.add((us, uf))
        all_nodes.add((ds, df))
        children[up_key].append(dn_key)
        has_parent.add(dn_key)

    # 루트 = 부모가 없는 노드
    roots = []
    for (s, f) in all_nodes:
        key = f"{s}__{f}"
        if key not in has_parent:
            roots.append(key)

    # BFS로 depth 할당
    depth: dict[str, int] = {}
    queue = deque()
    for r in sorted(roots):
        depth[r] = 0
        queue.append(r)

    while queue:
        node = queue.popleft()
        for child in children.get(node, []):
            if child not in depth:
                depth[child] = depth[node] + 1
                queue.append(child)

    # depth가 없는 고립 노드 처리
    for (s, f) in all_nodes:
        key = f"{s}__{f}"
        if key not in depth:
            level = FACILITY_LEVEL.get(f, 2)
            depth[key] = level

    return all_nodes, children, roots, depth


def _layout_subtree(
    node: str,
    children_map: dict[str, list[str]],
    depth_map: dict[str, int],
    y_cursor: list[float],  # mutable ref [current_y]
) -> dict[str, tuple[float, float]]:
    """재귀적으로 서브트리의 (x, y) 좌표 배정.

    리프 노드: y_cursor 현재 위치에 배치, y_cursor를 Y_GAP만큼 감소
    내부 노드: 모든 자식 배치 후, 자식의 y 범위 중앙에 배치
    """
    positions: dict[str, tuple[float, float]] = {}

    kids = sorted(children_map.get(node, []))

    if not kids:
        # 리프
        d = depth_map.get(node, 0)
        x = ORIGIN_X + d * X_STEP
        y = y_cursor[0]
        positions[node] = (x, y)
        y_cursor[0] -= Y_GAP  # 다음 리프는 더 아래
        return positions

    # 자식 먼저 배치 (재귀)
    child_ys = []
    for child in kids:
        if child not in positions:  # DAG에서 중복 방문 방지
            sub = _layout_subtree(child, children_map, depth_map, y_cursor)
            positions.update(sub)
        if child in positions:
            child_ys.append(positions[child][1])

    # 부모는 자식 Y 범위 중앙에
    if child_ys:
        y_center = (max(child_ys) + min(child_ys)) / 2.0
    else:
        y_center = y_cursor[0]
        y_cursor[0] -= Y_GAP

    d = depth_map.get(node, 0)
    x = ORIGIN_X + d * X_STEP
    positions[node] = (x, y_center)

    return positions


def _compute_layout(
    all_nodes: set[tuple[str, str]],
    children_map: dict[str, list[str]],
    roots: list[str],
    depth_map: dict[str, int],
) -> list[dict]:
    """전체 노드 좌표 계산."""
    positions: dict[str, tuple[float, float]] = {}
    y_cursor = [ORIGIN_Y]  # 위에서 아래로 내려감

    for root in sorted(roots):
        sub = _layout_subtree(root, children_map, depth_map, y_cursor)
        positions.update(sub)
        y_cursor[0] -= Y_GAP * 2  # 루트 간 여유 간격

    # 고립 노드 (루트에서 도달 못 한 것) 별도 배치
    for (s, f) in sorted(all_nodes):
        key = f"{s}__{f}"
        if key not in positions:
            d = depth_map.get(key, 2)
            x = ORIGIN_X + d * X_STEP
            y = y_cursor[0]
            positions[key] = (x, y)
            y_cursor[0] -= Y_GAP

    # dict → list[dict]
    rows = []
    for (s, f) in all_nodes:
        key = f"{s}__{f}"
        x, y = positions.get(key, (ORIGIN_X, ORIGIN_Y))
        level = FACILITY_LEVEL.get(f, 2)
        w, h = BOX_SIZES.get(level, (120, 36))
        rows.append({
            "sitename": s,
            "facilitytype": f,
            "group_level": level,
            "diagram_x": round(x, 6),
            "diagram_y": round(y, 6),
            "box_width": w,
            "box_height": h,
            "label_text": f"{s} ({f})",
            "display_from_z": DISPLAY_FROM_Z.get(level, 10.0),
            "display_to_z": 22.0,
        })
    return rows


def _upsert(cur, rows):
    inserted = updated = 0
    for r in rows:
        cur.execute(
            """
            INSERT INTO tb_flow_diagram_node
                (sitename, facilitytype, group_level, diagram_x, diagram_y,
                 box_width, box_height, label_text, display_from_z, display_to_z)
            VALUES (%(sitename)s, %(facilitytype)s, %(group_level)s,
                    %(diagram_x)s, %(diagram_y)s, %(box_width)s, %(box_height)s,
                    %(label_text)s, %(display_from_z)s, %(display_to_z)s)
            ON CONFLICT (sitename, facilitytype) DO UPDATE SET
                group_level = EXCLUDED.group_level,
                diagram_x = EXCLUDED.diagram_x,
                diagram_y = EXCLUDED.diagram_y,
                box_width = EXCLUDED.box_width,
                box_height = EXCLUDED.box_height,
                label_text = EXCLUDED.label_text,
                display_from_z = EXCLUDED.display_from_z,
                display_to_z = EXCLUDED.display_to_z,
                updated_at = now()
            RETURNING (xmax = 0)
            """,
            r,
        )
        is_insert = cur.fetchone()[0]
        if is_insert:
            inserted += 1
        else:
            updated += 1
    return inserted, updated


def main():
    conn = _db()
    try:
        cur = conn.cursor()
        all_nodes, children, roots, depth = _build_tree(cur)
        print(f"[seed] nodes={len(all_nodes)} edges={sum(len(v) for v in children.values())} roots={len(roots)}")
        for r in sorted(roots):
            print(f"  root: {r}")

        rows = _compute_layout(all_nodes, children, roots, depth)
        print(f"[seed] layout computed: {len(rows)} nodes")
        for level in [0, 1, 2, 3]:
            count = sum(1 for r in rows if r["group_level"] == level)
            print(f"  L{level}: {count}")

        inserted, updated = _upsert(cur, rows)
        conn.commit()
        print(f"[seed] upsert: inserted={inserted}, updated={updated}")
        cur.close()
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
