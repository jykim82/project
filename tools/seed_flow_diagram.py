"""
[용수흐름도 다이어그램 모드 Phase 1b] 자동 레이아웃 시드

tb_facility_flow_map(upstream→downstream 엣지)에서 노드 집합을 추출해
Sugiyama 스타일 층별 좌표(group_level + depth 기반 column)로 배치한 뒤
tb_flow_diagram_node에 UPSERT한다.

레이아웃 규칙 (좌→우 수계 흐름):
  1. facilitytype 기반 group_level 고정
     - 0: 정수장, 댐, 취수장 (source) — 가장 왼쪽
     - 1: 배수지 (top-level)
     - 2: 가압장, 감압설비 (intermediate)
     - 3: 소블록, 소소블록, 블록 (leaf) — 가장 오른쪽
  2. diagram_x = group_level * COLUMN_X_STEP  (수직 컬럼, 수계 방향 좌→우)
  3. diagram_y = 같은 level 내 알파벳 정렬 index * ROW_Y_STEP  (세로 배치)
  4. box_width/height: level별 차등 (source 크게, leaf 작게)
  5. display_from_z: level이 높을수록 늦게(= 더 줌 인해야) 노출
     - level 0: 8~22 (항상 보임)
     - level 1: 10~22
     - level 2: 11~22
     - level 3: 12~22

실행:
    docker exec slm-backend python3 /app/tools/seed_flow_diagram.py
    # 기존 row 모두 재계산 (idempotent)
"""

import os
import sys

import psycopg2

DB_HOST = os.environ.get("DB_HOST", "timescaledb")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "slm")
DB_USER = os.environ.get("DB_USER", "slm_dev")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "slm_dev_1234")

# ───────────────────────────────────────────────────────────
# 레이아웃 상수
# ───────────────────────────────────────────────────────────

# facilitytype → group_level
FACILITY_LEVEL = {
    "정수장": 0,
    "댐": 0,
    "취수장": 0,
    "배수지": 1,
    "가압장": 2,
    "감압설비": 2,
    "감압시설": 2,
    "소블록": 3,
    "소소블록": 3,
    "블록": 3,
}

# level별 box 크기
BOX_SIZES = {
    0: (180, 60),
    1: (160, 52),
    2: (140, 44),
    3: (120, 36),
}

# level별 display_from_z (작을수록 빨리 노출)
DISPLAY_FROM_Z = {
    0: 8.0,
    1: 10.0,
    2: 11.0,
    3: 12.0,
}

# 레이아웃 canvas 상수 (fake lng/lat, 좌→우 수계 흐름)
COLUMN_X_STEP = 0.040    # 레벨 간 가로 간격 (lng) — 수계 방향 컬럼
ROW_Y_STEP    = 0.004    # 같은 레벨 내 세로 간격 (lat)
ORIGIN_X = 126.5         # 레벨 0 시작 lng (맨 왼쪽)
ORIGIN_Y = 36.8          # 중심 lat (각 레벨 세로 중앙값)


def _db():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD,
    )


def _collect_nodes(cur) -> list[tuple[str, str]]:
    """tb_facility_flow_map에서 고유 노드(상류+하류 union) 추출."""
    cur.execute(
        """
        SELECT DISTINCT sitename, facilitytype FROM (
            SELECT upstream_sitename AS sitename, upstream_facilitytype AS facilitytype
            FROM tb_facility_flow_map
            UNION
            SELECT downstream_sitename, downstream_facilitytype
            FROM tb_facility_flow_map
        ) t
        ORDER BY facilitytype, sitename
        """
    )
    return cur.fetchall()


def _resolve_level(facilitytype: str) -> int:
    return FACILITY_LEVEL.get(facilitytype, 2)  # 미지정은 중간 레벨


def _layout(nodes: list[tuple[str, str]]) -> list[dict]:
    """레벨별로 그룹핑 후 canvas 좌표 계산."""
    by_level: dict[int, list[tuple[str, str]]] = {0: [], 1: [], 2: [], 3: []}
    for sitename, facilitytype in nodes:
        level = _resolve_level(facilitytype)
        by_level[level].append((sitename, facilitytype))

    rows: list[dict] = []
    for level in sorted(by_level.keys()):
        items = sorted(by_level[level], key=lambda t: (t[1], t[0]))
        if not items:
            continue
        # 좌→우 흐름: 같은 레벨의 노드들을 같은 X 컬럼에 세로로 중앙 정렬 배치
        n = len(items)
        half = (n - 1) / 2.0
        x_col = ORIGIN_X + level * COLUMN_X_STEP  # 레벨별 X 컬럼 (정수장 leftmost)
        for idx, (sitename, facilitytype) in enumerate(items):
            offset = idx - half
            # Y는 중앙 정렬 + 소량 랜덤 오프셋 방지 위해 단순 offset*step
            y = ORIGIN_Y - offset * ROW_Y_STEP
            w, h = BOX_SIZES[level]
            rows.append({
                "sitename": sitename,
                "facilitytype": facilitytype,
                "group_level": level,
                "diagram_x": round(x_col, 6),
                "diagram_y": round(y, 6),
                "box_width": w,
                "box_height": h,
                "label_text": f"{sitename} ({facilitytype})",
                "display_from_z": DISPLAY_FROM_Z[level],
                "display_to_z": 22.0,
            })
    return rows


def _upsert(cur, rows: list[dict]) -> tuple[int, int]:
    """idempotent: 같은 (sitename, facilitytype)면 좌표 재계산 업데이트."""
    inserted = 0
    updated = 0
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
            RETURNING (xmax = 0)  -- true이면 신규 INSERT
            """,
            r,
        )
        is_insert = cur.fetchone()[0]
        if is_insert:
            inserted += 1
        else:
            updated += 1
    return inserted, updated


def main() -> int:
    conn = _db()
    try:
        cur = conn.cursor()
        nodes = _collect_nodes(cur)
        print(f"[seed] collected {len(nodes)} unique nodes from tb_facility_flow_map")

        rows = _layout(nodes)
        print(f"[seed] layout computed for {len(rows)} nodes")
        # Level breakdown
        for level in [0, 1, 2, 3]:
            count = sum(1 for r in rows if r["group_level"] == level)
            print(f"  level {level}: {count} nodes")

        inserted, updated = _upsert(cur, rows)
        conn.commit()
        print(f"[seed] upsert done — inserted={inserted}, updated={updated}")
        cur.close()
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
