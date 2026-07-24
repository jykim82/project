"""구축 완결성 검수 리포트 — 구축 고도화 ③.

사양: docs/setup-audit-spec.md
흩어져 있던 검수 자산(기초정보 대조·알람 임계 커버리지·계통도 lint·
datainfo 변환 재현율·품질 계층)을 GET /setup/audit 한 번으로 집계 —
신규 고객 구축·납품 검수(delivery-checklist)의 단일 진입점.

각 항목: {key, title, status(pass|warn|info), count, items(top), guide}
- pass: 문제 0 / warn: 조치 필요 / info: 참고 (구축 판단 사항)
"""

import logging
from typing import Callable, Optional

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["setup-audit"])

_get_db_connection: Optional[Callable] = None


def init(get_db_connection_fn):
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


def _item(key, title, status, count, items, guide):
    return {"key": key, "title": title, "status": status,
            "count": count, "items": items[:15], "guide": guide}


def _check_base_info(cur) -> dict:
    """태그에 있는데 시설 기초정보 테이블에 없는 사이트 (수청2지구2 사례)."""
    cur.execute("""
        WITH tag_sites AS (
            SELECT DISTINCT sitename, facilitytype FROM tb_tag_info
            WHERE sitename IS NOT NULL AND facilitytype IS NOT NULL
        )
        SELECT t.sitename, t.facilitytype FROM tag_sites t
        WHERE (t.facilitytype = '배수지' AND NOT EXISTS
                (SELECT 1 FROM tb_service_reservoir_info r WHERE r.sitename = t.sitename))
           OR (t.facilitytype = '가압장' AND NOT EXISTS
                (SELECT 1 FROM tb_service_booster_station_info b WHERE b.sitename = t.sitename))
           OR (t.facilitytype IN ('소블록','소소블록','블록') AND NOT EXISTS
                (SELECT 1 FROM tb_block_info bl WHERE bl.sitename = t.sitename))
        ORDER BY t.facilitytype, t.sitename
    """)
    rows = [f"{s} {f}" for s, f in cur.fetchall()]
    return _item(
        "base_info", "시설 기초정보 등록", "pass" if not rows else "warn",
        len(rows), rows,
        "태그 마스터에는 있는데 시설 기초정보(배수지/가압장/블록)에 미등록 — "
        "자동완성·시설 조회에서 누락됩니다. 시설정보 구축 화면에서 등록하세요.")


def _check_threshold(cur) -> dict:
    """압력·수위 계측 보유 시설의 SCADA 알람 임계 커버리지 (성상1 사례)."""
    cur.execute("""
        WITH meas AS (
            SELECT CASE WHEN datainfo LIKE '%수위%' THEN '수위' ELSE '압력' END AS kind,
                   sitename
            FROM tb_tag_info
            WHERE tagtype = 'Analog Input'
              AND (datainfo LIKE '%수위%' OR datainfo LIKE '%압력%')
              AND datainfo NOT LIKE '%설정%' AND datainfo NOT LIKE '%SET%'
              AND datainfo NOT LIKE '%적산%'
            GROUP BY 1, 2
        ),
        lim AS (
            SELECT CASE WHEN datainfo LIKE '%수위%' THEN '수위' ELSE '압력' END AS kind,
                   sitename
            FROM tb_tag_info
            WHERE (datainfo LIKE '%수위%' OR datainfo LIKE '%압력%')
              AND datainfo ~ '(HH?|LL?) ?(SET|알람|상태|발생|설정값)'
            GROUP BY 1, 2
        )
        SELECT m.kind, m.sitename FROM meas m
        LEFT JOIN lim l ON l.kind = m.kind AND l.sitename = m.sitename
        WHERE l.sitename IS NULL
        ORDER BY m.kind, m.sitename
    """)
    rows = [f"{k} — {s}" for k, s in cur.fetchall()]
    return _item(
        "threshold", "SCADA 알람 임계 보유", "pass" if not rows else "info",
        len(rows), rows,
        "계측은 있는데 H/HH·L/LL 임계 태그가 없는 시설 — SCADA 측 설정 협의 "
        "대상입니다 (docs/alarm-threshold-coverage.md). 시스템 통계 감시는 "
        "임계 없이도 동작하므로 '참고' 등급입니다.")


def _check_datainfo(cur) -> dict:
    """datainfo 변환 상태 — 룰 미커버(diff) 태그 수 (변환룰 화면 연계)."""
    try:
        from endpoints.datainfo_rules import apply_rules, _load_rules, _norm
        rules = _load_rules(cur, "R01")
        cur.execute("""
            SELECT tagsn, facilitytype, tagtype, COALESCE(datadesc,''), COALESCE(datainfo,'')
            FROM tb_tag_info WHERE datadesc IS NOT NULL AND datadesc <> ''
        """)
        diff = total = 0
        samples: list[str] = []
        for tagsn, ft, tt, desc, info in cur.fetchall():
            total += 1
            if _norm(desc) == _norm(info):
                continue
            if apply_rules(desc, rules, ft or "", tt or "", tagsn) != _norm(info):
                diff += 1
                if len(samples) < 15:
                    samples.append(f"{desc} → {info}")
        pct = round((total - diff) / total * 100, 1) if total else 0
        return _item(
            "datainfo", "DATAINFO 변환 정합", "pass" if pct >= 95 else "info",
            diff, samples,
            f"룰 재현율 {pct}% — 미커버 {diff}건은 원본 축약 등으로 수동/override "
            "확인 대상입니다 (DATAINFO 변환룰 화면).")
    except Exception as e:
        logger.warning(f"datainfo 검사 건너뜀: {e}")
        return _item("datainfo", "DATAINFO 변환 정합", "info", 0, [], f"검사 불가: {e}")


def _check_diagram(cur) -> dict:
    """계통도 정합 — flow-diagram lint 축약판 (누락·고아·순환)."""
    from endpoints.flow_diagram_layout import _build_tree
    all_nodes, children, _roots, _depth, _parent, _edges = _build_tree(cur)
    node_keys = {f"{s}__{f}" for (s, f) in all_nodes}
    cur.execute("SELECT sitename, facilitytype FROM tb_flow_diagram_node")
    diagram = {f"{s}__{f}" for s, f in cur.fetchall()}
    problems = [f"배치 누락: {k.replace('__', ' ')}" for k in sorted(node_keys - diagram)]
    problems += [f"고아 노드: {k.replace('__', ' ')}" for k in sorted(diagram - node_keys)]
    # 순환 (DFS 3색)
    color: dict[str, int] = {k: 0 for k in node_keys}

    def dfs(n, path):
        color[n] = 1
        for c in children.get(n, []):
            if color.get(c) == 1:
                problems.append("순환: " + " → ".join(path + [n, c]))
            elif color.get(c) == 0:
                dfs(c, path + [n])
        color[n] = 2

    for k in node_keys:
        if color[k] == 0:
            dfs(k, [])
    return _item(
        "diagram", "계통도 관계·배치 정합", "pass" if not problems else "warn",
        len(problems), problems,
        "관계↔노드 배치↔순환 검사 — 계통도 설정 > 노드 배치 탭에서 자동 배치로 "
        "해소할 수 있습니다.")


def _check_quality(cur) -> dict:
    """계측 품질 현황 — 구축 직후 무응답·두절은 결선/통신 미완 신호."""
    cur.execute("""
        SELECT q.reason, COUNT(*) FROM tb_tag_quality q GROUP BY q.reason ORDER BY 2 DESC
    """)
    rows = cur.fetchall()
    total = sum(c for _, c in rows)
    return _item(
        "quality", "계측 품질 (포화·고착·무응답·두절)",
        "pass" if total == 0 else "warn", total,
        [f"{r}: {c}건" for r, c in rows],
        "품질 이상 태그 — 구축 검수 시 0 이 목표입니다 (무응답=결선 미완, "
        "통신두절=망 미개통 가능). 이상 진단 화면·AI 채팅에서 태그별 상세 확인.")


def _check_epanet(cur) -> dict:
    """EPANET 유량 매핑 커버리지 (참고 — B 기능군 사용 시)."""
    cur.execute("""
        WITH rel AS (
            SELECT DISTINCT sitename, facilitytype FROM (
                SELECT upstream_sitename AS sitename, upstream_facilitytype AS facilitytype
                FROM tb_facility_flow_map
                UNION
                SELECT downstream_sitename, downstream_facilitytype FROM tb_facility_flow_map
            ) u
        )
        SELECT r.sitename, r.facilitytype FROM rel r
        WHERE NOT EXISTS (
            SELECT 1 FROM tb_epanet_facility_flow_map e
            WHERE e.sitename = r.sitename AND e.facilitytype = r.facilitytype AND e.enabled = 'Y')
        ORDER BY r.facilitytype, r.sitename
    """)
    rows = [f"{s} {f}" for s, f in cur.fetchall()]
    return _item(
        "epanet", "EPANET 유량 매핑", "pass" if not rows else "info",
        len(rows), rows,
        "관계에 있는 시설 중 EPANET 실측 주입 매핑이 없는 곳 — EPANET 기능군"
        "(B) 사용 시에만 해당하는 참고 항목입니다.")


@router.get("/setup/audit")
def setup_audit():
    """구축 완결성 검수 — 검사 6종 집계 (납품 검수 단일 진입점)."""
    if _get_db_connection is None:
        return {"status": "ERROR", "message": "미초기화"}
    conn = _get_db_connection()
    try:
        cur = conn.cursor()
        checks = []
        for fn in (_check_base_info, _check_threshold, _check_datainfo,
                   _check_diagram, _check_quality, _check_epanet):
            try:
                checks.append(fn(cur))
            except Exception as e:
                logger.error(f"setup audit {fn.__name__} 실패: {e}")
                checks.append(_item(fn.__name__, fn.__name__, "info", 0, [], f"검사 오류: {e}"))
        cur.close()
        summary = {
            "pass": sum(1 for c in checks if c["status"] == "pass"),
            "warn": sum(1 for c in checks if c["status"] == "warn"),
            "info": sum(1 for c in checks if c["status"] == "info"),
            "total": len(checks),
        }
        return {"status": "OK", "summary": summary, "checks": checks}
    finally:
        conn.close()
