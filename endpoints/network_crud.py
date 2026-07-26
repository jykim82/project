"""
네트워크 CRUD 엔드포인트 모듈

네트워크 장비(tb_network_info), 연결(tb_network_link), SNMP 진단,
통신 상태 조회 등 네트워크 관련 API를 제공한다.

ai_server.py에서 분리된 모듈 — init()으로 DB 커넥션 함수와 SNMP 폴러를 주입받아 사용.
"""

import asyncio
import io
import csv as csv_mod
import json
import logging

import psycopg2
from fastapi import APIRouter, Body, Query, UploadFile

logger = logging.getLogger("slm")

router = APIRouter()

# ai_server.py에서 주입
_get_db_connection = None
_snmp_poller = None


def init(get_db_connection_fn, snmp_poller=None):
    """ai_server.py에서 DB 커넥션 팩토리 함수와 SNMP 폴러를 주입받는다."""
    global _get_db_connection, _snmp_poller
    _get_db_connection = get_db_connection_fn
    _snmp_poller = snmp_poller


# =============================================================================
# 네트워크 모니터링 API
# =============================================================================


@router.get("/network/devices")
async def get_network_devices():
    """네트워크 장비 목록 + 최신 통신 상태 조회 (IP 보유 장비만)"""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        # 최신 check_time을 먼저 구한 뒤 해당 시간 데이터만 조인 (PK 인덱스 활용)
        cur.execute("""
            WITH latest_time AS (
                SELECT MAX(check_time) AS ct FROM tb_network_status
            )
            SELECT
                e.equipment_id,
                e.sitename,
                e.facilitytype,
                e.equipmenttype,
                n.ip_address,
                ns.is_alive,
                ns.status_code,
                ns.rtt_ms,
                ns.check_time,
                ns.error_message
            FROM tb_equipment_info e
            JOIN tb_network_info n ON e.equipment_id = n.equipment_id
            LEFT JOIN tb_network_status ns
                ON ns.equipment_id = e.equipment_id
                AND ns.check_time = (SELECT ct FROM latest_time)
            ORDER BY e.sitename, e.equipmenttype
        """)
        cols = [
            "equipment_id", "sitename", "facilitytype", "equipmenttype",
            "ip_address", "is_alive", "status_code", "rtt_ms",
            "check_time", "error_message",
        ]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        for row in rows:
            if row["check_time"]:
                row["check_time"] = row["check_time"].isoformat()
            if row["rtt_ms"] is not None:
                row["rtt_ms"] = float(row["rtt_ms"])
        cur.close()
        return {"status": "OK", "data": rows}
    except psycopg2.Error as e:
        logger.error(f"네트워크 장비 조회 실패: {e}")
        return {"status": "ERROR", "message": "조회에 실패했습니다.", "data": []}
    finally:
        if conn:
            conn.close()


@router.get("/network/devices/{equipment_id}/tags")
async def get_device_tags(equipment_id: str):
    """장비(L1)에 매핑된 측정 태그(L3) + 현재값 조회.

    네트워크 진단(ping)과 측정 데이터는 별도 계층이므로, ping 장애 장비 뒤에
    어떤 측정 태그가 매달려 있는지를 네트워크 화면에서 역추적해 보여준다.
    (태그 모니터링은 측정 데이터 전용 — 사양 tag-monitoring-spec §3.0.1.)
    현재값은 태그별 LATERAL(idx_tag_raw_tagsn_time seek + LIMIT 1)로 조회.
    """
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT t.tagsn, t.tagtype, t.datainfo, t.datadesc, t.unit,
                   t.alarm_tag_yn, l.val AS current_value, l.logtime
              FROM tb_equipment_tag_map m
              JOIN tb_tag_info t ON t.tagsn = m.tagsn
              LEFT JOIN LATERAL (
                  SELECT val, logtime
                    FROM tb_tag_raw_data r
                   WHERE r.tagsn = t.tagsn
                     AND r.logtime >= now() - interval '7 days'
                   ORDER BY r.logtime DESC
                   LIMIT 1
              ) l ON true
             WHERE m.equipment_id = %s
             ORDER BY t.tagtype, t.datainfo
        """, (equipment_id,))
        cols = [
            "tagsn", "tagtype", "datainfo", "datadesc", "unit",
            "alarm_tag_yn", "current_value", "logtime",
        ]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        for row in rows:
            if row["alarm_tag_yn"] is not None:
                row["alarm_tag_yn"] = int(row["alarm_tag_yn"])
            if row["current_value"] is not None:
                row["current_value"] = float(row["current_value"])
            if row["logtime"]:
                row["logtime"] = row["logtime"].isoformat()
        cur.close()
        return {"status": "OK", "data": rows}
    except psycopg2.Error as e:
        logger.error(f"장비 매핑 태그 조회 실패 ({equipment_id}): {e}")
        return {"status": "ERROR", "message": "조회에 실패했습니다.", "data": []}
    finally:
        if conn:
            conn.close()


@router.get("/network/topology")
async def get_network_topology():
    """토폴로지 그래프 데이터 (nodes + edges) 조회"""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()

        # 노드: 전체 장비 (IP 보유 → tb_network_status, IP 없는 시리얼 → DI 통신이상 태그)
        cur.execute("""
            WITH latest_network AS (
                SELECT MAX(check_time) AS ct FROM tb_network_status
            ),
            -- 시리얼 장비: 통신이상 DI 태그 최신값으로 sitename+facilitytype별 상태 결정
            serial_status AS (
                SELECT
                    t.sitename,
                    t.facilitytype,
                    CASE WHEN MAX(CASE WHEN r.val = 1 THEN 1 ELSE 0 END) = 1
                         THEN '이상' ELSE '정상' END AS status_code
                FROM tb_tag_info t
                JOIN LATERAL (
                    SELECT val FROM tb_tag_raw_data
                    WHERE tagsn = t.tagsn ORDER BY logtime DESC LIMIT 1
                ) r ON true
                WHERE t.tagtype = 'Digital Input'
                  AND t.datadesc ILIKE '%통신이상%'
                GROUP BY t.sitename, t.facilitytype
            )
            SELECT
                e.equipment_id,
                COALESCE(e.equipmenttype, '') || ' (' || e.sitename || ')' AS name,
                e.facilitytype AS category,
                e.equipmenttype,
                e.sitename,
                n.ip_address,
                COALESCE(ns.status_code, ss.status_code, '정상') AS status_code,
                (n.ip_address IS NOT NULL) AS has_ip,
                ns.rtt_ms,
                TO_CHAR(ns.check_time, 'HH24:MI:SS') AS check_time,
                n.meta->'canvas_pos' AS canvas_pos
            FROM tb_equipment_info e
            LEFT JOIN tb_network_info n ON e.equipment_id = n.equipment_id
            LEFT JOIN tb_network_status ns
                ON ns.equipment_id = e.equipment_id
                AND ns.check_time = (SELECT ct FROM latest_network)
            LEFT JOIN serial_status ss
                ON ss.sitename = e.sitename
                AND ss.facilitytype = e.facilitytype
                AND n.ip_address IS NULL   -- 시리얼 장비에만 DI 상태 적용
            ORDER BY e.sitename, e.equipmenttype
        """)
        node_cols = [
            "id", "name", "category", "equipmenttype",
            "sitename", "ip_address", "status", "has_ip",
            "rtt_ms", "check_time", "canvas_pos",
        ]
        nodes = [dict(zip(node_cols, row)) for row in cur.fetchall()]
        for node in nodes:
            if node.get("rtt_ms") is not None:
                node["rtt_ms"] = float(node["rtt_ms"])

        # 엣지: 연결 관계
        cur.execute("""
            SELECT
                l.source_equipment_id AS source,
                l.target_equipment_id AS target,
                l.link_protocol,
                l.link_device_interface
            FROM tb_network_link l
            ORDER BY l.source_equipment_id
        """)
        edge_cols = ["source", "target", "link_protocol", "link_device_interface"]
        edges = [dict(zip(edge_cols, row)) for row in cur.fetchall()]

        cur.close()
        return {
            "status": "OK",
            "data": {"nodes": nodes, "edges": edges},
        }
    except psycopg2.Error as e:
        logger.error(f"네트워크 토폴로지 조회 실패: {e}")
        return {"status": "ERROR", "message": "조회에 실패했습니다.", "data": {"nodes": [], "edges": []}}
    finally:
        if conn:
            conn.close()


@router.get("/network/status/summary")
async def get_network_status_summary():
    """네트워크 통신 상태 요약 통계"""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        # MAX(check_time) 기반 최적화 — PK 인덱스 활용
        cur.execute("""
            WITH latest_time AS (
                SELECT MAX(check_time) AS ct FROM tb_network_status
            )
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE status_code = '정상') AS normal,
                COUNT(*) FILTER (WHERE status_code = '이상') AS error,
                COUNT(*) FILTER (WHERE status_code NOT IN ('정상', '이상') OR status_code IS NULL) AS warning,
                (SELECT ct FROM latest_time) AS last_check_time
            FROM tb_network_status
            WHERE check_time = (SELECT ct FROM latest_time)
        """)
        row = cur.fetchone()
        cur.close()
        result = {
            "total": row[0] or 0,
            "normal": row[1] or 0,
            "error": row[2] or 0,
            "warning": row[3] or 0,
            "lastCheckTime": row[4].isoformat() if row[4] else None,
        }
        return {"status": "OK", "data": result}
    except psycopg2.Error as e:
        logger.error(f"네트워크 상태 요약 조회 실패: {e}")
        return {"status": "ERROR", "message": "조회에 실패했습니다.", "data": {}}
    finally:
        if conn:
            conn.close()


@router.get("/network/comm-alarms")
async def get_comm_alarms():
    """통신이상 알람 조회 — 시리얼(DI 태그) + 이더넷(tb_network_status) 통합

    - 시리얼: IP 미보유 장비의 통신이상 DI 태그 최신값 val=1 항목
    - 이더넷: tb_network_status 최신 스냅샷에서 status_code='이상' 장비
    """
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            WITH
            -- 1) 시리얼 통신이상: DI 태그 직접 조회
            serial_alarms AS (
                SELECT
                    t.sitename,
                    t.facilitytype,
                    t.datadesc                                          AS alarm_msg,
                    TO_CHAR(r.logtime, 'YYYY-MM-DD HH24:MI:SS')        AS alarm_start_time,
                    '시리얼'                                             AS comm_type
                FROM tb_tag_info t
                JOIN LATERAL (
                    SELECT val, logtime
                    FROM tb_tag_raw_data
                    WHERE tagsn = t.tagsn
                    ORDER BY logtime DESC
                    LIMIT 1
                ) r ON true
                WHERE t.tagtype = 'Digital Input'
                  AND t.datadesc ILIKE '%통신이상%'
                  AND r.val = 1
            ),
            -- 2) 이더넷 통신이상: 최신 네트워크 상태 스냅샷
            latest_network AS (
                SELECT MAX(check_time) AS ct FROM tb_network_status
            ),
            ethernet_alarms AS (
                SELECT
                    e.sitename,
                    e.facilitytype,
                    e.equipmenttype || ' 네트워크 통신이상'              AS alarm_msg,
                    TO_CHAR(ns.check_time, 'YYYY-MM-DD HH24:MI:SS')    AS alarm_start_time,
                    '이더넷'                                             AS comm_type
                FROM tb_network_status ns
                JOIN tb_equipment_info e ON e.equipment_id = ns.equipment_id
                WHERE ns.check_time = (SELECT ct FROM latest_network)
                  AND ns.status_code = '이상'
            )
            SELECT sitename, facilitytype, alarm_msg, alarm_start_time, comm_type
            FROM serial_alarms
            UNION ALL
            SELECT sitename, facilitytype, alarm_msg, alarm_start_time, comm_type
            FROM ethernet_alarms
            ORDER BY comm_type DESC, sitename, facilitytype
        """)
        rows = cur.fetchall()
        cur.close()
        result = [
            {
                "sitename": r[0] or "",
                "facilitytype": r[1] or "",
                "alarm_msg": r[2] or "",
                "alarm_severity": None,
                "alarm_start_time": r[3] or "",
                "alarm_status": "진행중",
                "comm_type": r[4] or "",
            }
            for r in rows
        ]
        return {"status": "OK", "data": result}
    except psycopg2.Error as e:
        logger.error(f"통신이상 알람 조회 실패: {e}")
        return {"status": "ERROR", "message": "조회에 실패했습니다.", "data": []}
    finally:
        if conn:
            conn.close()


# =============================================================================
# SNMP 스위치 포트 진단 API
# =============================================================================


@router.get("/network/snmp/{equipment_id}/ports")
async def get_snmp_ports(equipment_id: str):
    """스위치 포트 상태 조회"""
    if not _snmp_poller:
        return {"status": "ERROR", "message": "SNMP 폴러 미초기화", "data": []}
    try:
        ports = await asyncio.to_thread(_snmp_poller.get_ports, equipment_id)
        return {"status": "OK", "data": ports}
    except Exception as e:
        logger.error(f"SNMP 포트 조회 실패 ({equipment_id}): {e}")
        return {"status": "ERROR", "message": str(e), "data": []}


@router.get("/network/snmp/{equipment_id}/system")
async def get_snmp_system(equipment_id: str):
    """스위치 시스템 정보 (장비정보 + 포트 요약)"""
    if not _snmp_poller:
        return {"status": "ERROR", "message": "SNMP 폴러 미초기화", "data": None}
    try:
        info = await asyncio.to_thread(_snmp_poller.get_system_info, equipment_id)
        return {"status": "OK", "data": info}
    except Exception as e:
        logger.error(f"SNMP 시스템 정보 조회 실패 ({equipment_id}): {e}")
        return {"status": "ERROR", "message": str(e), "data": None}


@router.get("/network/snmp/summary")
async def get_snmp_summary():
    """전체 스위치 포트 상태 요약"""
    if not _snmp_poller:
        return {"status": "ERROR", "message": "SNMP 폴러 미초기화", "data": []}
    try:
        summary = await asyncio.to_thread(_snmp_poller.get_summary)
        return {"status": "OK", "data": summary}
    except Exception as e:
        logger.error(f"SNMP 요약 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e), "data": []}


# =============================================================================
# 네트워크 관리 CRUD API (구축 > 네트워크 관리)
# =============================================================================

@router.get("/network/infos")
async def get_network_infos(
    page: int = 1, page_size: int = 50,
    sitename: str = "", facilitytype: str = "",
    equipmenttype: str = "", keyword: str = "",
):
    """네트워크 장비 목록 (tb_network_info + tb_equipment_info JOIN)"""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        where, params = ["1=1"], []
        if sitename:
            where.append("e.sitename = %s"); params.append(sitename)
        if facilitytype:
            where.append("e.facilitytype = %s"); params.append(facilitytype)
        if equipmenttype:
            where.append("e.equipmenttype = %s"); params.append(equipmenttype)
        if keyword:
            where.append("(n.equipment_id ILIKE %s OR n.ip_address ILIKE %s OR e.sitename ILIKE %s)")
            kw = f"%{keyword}%"; params.extend([kw, kw, kw])
        w = " AND ".join(where)
        cur.execute(f"SELECT COUNT(*) FROM tb_network_info n JOIN tb_equipment_info e ON n.equipment_id = e.equipment_id WHERE {w}", params)
        total = cur.fetchone()[0]
        offset = (page - 1) * page_size
        cur.execute(f"""
            SELECT n.equipment_id, n.ip_address, n.description, n.meta,
                   n.created_at, n.updated_at,
                   e.sitename, e.facilitytype, e.equipmenttype
            FROM tb_network_info n
            JOIN tb_equipment_info e ON n.equipment_id = e.equipment_id
            WHERE {w}
            ORDER BY e.sitename, e.facilitytype, n.equipment_id
            LIMIT %s OFFSET %s
        """, params + [page_size, offset])
        rows = cur.fetchall()
        cur.close()
        data = []
        for r in rows:
            data.append({
                "equipment_id": r[0], "ip_address": r[1], "description": r[2],
                "meta": r[3] or {},
                "created_at": r[4].isoformat() if r[4] else None,
                "updated_at": r[5].isoformat() if r[5] else None,
                "sitename": r[6], "facilitytype": r[7], "equipmenttype": r[8],
            })
        return {"status": "OK", "data": data, "total": total, "page": page, "page_size": page_size}
    except Exception as e:
        logger.error(f"네트워크 장비 목록 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn: conn.close()


@router.get("/network/infos/filters")
async def get_network_info_filters():
    """네트워크 장비 필터 옵션"""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT e.sitename FROM tb_network_info n
            JOIN tb_equipment_info e ON n.equipment_id = e.equipment_id ORDER BY 1
        """)
        sitenames = [r[0] for r in cur.fetchall()]
        cur.execute("""
            SELECT DISTINCT e.facilitytype FROM tb_network_info n
            JOIN tb_equipment_info e ON n.equipment_id = e.equipment_id ORDER BY 1
        """)
        facilitytypes = [r[0] for r in cur.fetchall()]
        cur.execute("""
            SELECT DISTINCT e.equipmenttype FROM tb_network_info n
            JOIN tb_equipment_info e ON n.equipment_id = e.equipment_id ORDER BY 1
        """)
        equipmenttypes = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT DISTINCT COALESCE(meta->>'network_role', '미지정') FROM tb_network_info ORDER BY 1")
        roles = [r[0] for r in cur.fetchall()]
        cur.close()
        return {"status": "OK", "data": {
            "sitenames": sitenames, "facilitytypes": facilitytypes,
            "equipmenttypes": equipmenttypes, "roles": roles,
        }}
    except Exception as e:
        logger.error(f"네트워크 필터 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn: conn.close()


@router.post("/network/infos")
async def create_network_info(req: dict = Body(...)):
    """네트워크 장비 추가 (equipment_id 필수, tb_equipment_info에 존재해야 함)"""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO tb_network_info (equipment_id, ip_address, description, meta)
            VALUES (%s, %s, %s, %s::jsonb)
        """, [req["equipment_id"], req.get("ip_address"), req.get("description"),
              json.dumps(req.get("meta", {}))])
        conn.commit()
        cur.close()
        return {"status": "OK", "equipment_id": req["equipment_id"]}
    except Exception as e:
        if conn: conn.rollback()
        logger.error(f"네트워크 장비 추가 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn: conn.close()


@router.put("/network/canvas-positions")
async def save_canvas_positions(req: dict = Body(...)):
    """링크 에디터 노드 배치 일괄 저장 (network-link-editor-spec §2.2).

    meta 병합 — gateway 등 기존 키 보존 (PUT /network/infos 는 meta 전체
    덮어쓰기라 좌표 저장에 부적합).
    """
    positions = req.get("positions", [])
    if not isinstance(positions, list):
        return {"status": "ERROR", "message": "positions 배열 필수"}
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        saved = 0
        for p in positions[:2000]:
            eid = p.get("equipment_id")
            if not eid:
                continue
            # 시리얼 장비 등 tb_network_info 행이 없어도 배치 저장 가능하도록
            # UPSERT (ip_address NULL 행 — has_ip 판정에 무해)
            cur.execute("""
                INSERT INTO tb_network_info (equipment_id, meta)
                VALUES (%s, jsonb_build_object('canvas_pos',
                        jsonb_build_object('x', %s::float, 'y', %s::float)))
                ON CONFLICT (equipment_id) DO UPDATE
                SET meta = COALESCE(tb_network_info.meta, '{}'::jsonb)
                           || jsonb_build_object('canvas_pos',
                               jsonb_build_object(
                                   'x', (EXCLUDED.meta->'canvas_pos'->>'x')::float,
                                   'y', (EXCLUDED.meta->'canvas_pos'->>'y')::float)),
                    updated_at = now()
            """, [eid, p.get("x", 0), p.get("y", 0)])
            saved += cur.rowcount
        conn.commit()
        cur.close()
        return {"status": "OK", "saved": saved}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"캔버스 배치 저장 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.put("/network/infos/{equipment_id}")
async def update_network_info(equipment_id: str, req: dict = Body(...)):
    """네트워크 장비 수정"""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE tb_network_info SET ip_address=%s, description=%s, meta=%s::jsonb
            WHERE equipment_id=%s
        """, [req.get("ip_address"), req.get("description"),
              json.dumps(req.get("meta", {})), equipment_id])
        conn.commit()
        affected = cur.rowcount
        cur.close()
        if affected == 0:
            return {"status": "ERROR", "message": "해당 장비를 찾을 수 없습니다."}
        return {"status": "OK"}
    except Exception as e:
        if conn: conn.rollback()
        logger.error(f"네트워크 장비 수정 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn: conn.close()


@router.delete("/network/infos/{equipment_id}")
async def delete_network_info(equipment_id: str):
    """네트워크 장비 삭제 (tb_network_link CASCADE 삭제)"""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM tb_network_info WHERE equipment_id=%s", [equipment_id])
        conn.commit()
        affected = cur.rowcount
        cur.close()
        if affected == 0:
            return {"status": "ERROR", "message": "해당 장비를 찾을 수 없습니다."}
        return {"status": "OK"}
    except Exception as e:
        if conn: conn.rollback()
        logger.error(f"네트워크 장비 삭제 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn: conn.close()


@router.get("/network/links")
async def get_network_links(
    page: int = 1, page_size: int = 50,
    protocol: str = "", keyword: str = "",
):
    """네트워크 연결 목록 (tb_network_link + 양쪽 장비 JOIN)"""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        where, params = ["1=1"], []
        if protocol:
            where.append("l.link_protocol = %s"); params.append(protocol)
        if keyword:
            where.append("""(l.source_equipment_id ILIKE %s OR l.target_equipment_id ILIKE %s
                OR l.link_role ILIKE %s
                OR COALESCE(se.sitename,'') ILIKE %s OR COALESCE(te.sitename,'') ILIKE %s)""")
            kw = f"%{keyword}%"; params.extend([kw, kw, kw, kw, kw])
        w = " AND ".join(where)
        cur.execute(f"""SELECT COUNT(*) FROM tb_network_link l
            LEFT JOIN tb_equipment_info se ON l.source_equipment_id = se.equipment_id
            LEFT JOIN tb_equipment_info te ON l.target_equipment_id = te.equipment_id
            WHERE {w}""", params)
        total = cur.fetchone()[0]
        offset = (page - 1) * page_size
        cur.execute(f"""
            SELECT l.source_equipment_id, l.target_equipment_id,
                   l.link_protocol, l.link_port, l.link_device_interface,
                   l.link_role, l.description, l.meta,
                   COALESCE(se.sitename,'') || ' ' || COALESCE(se.equipmenttype,'') AS source_name,
                   COALESCE(te.sitename,'') || ' ' || COALESCE(te.equipmenttype,'') AS target_name
            FROM tb_network_link l
            LEFT JOIN tb_equipment_info se ON l.source_equipment_id = se.equipment_id
            LEFT JOIN tb_equipment_info te ON l.target_equipment_id = te.equipment_id
            WHERE {w}
            ORDER BY l.source_equipment_id, l.target_equipment_id
            LIMIT %s OFFSET %s
        """, params + [page_size, offset])
        rows = cur.fetchall()
        cur.close()
        data = []
        for r in rows:
            data.append({
                "source_equipment_id": r[0], "target_equipment_id": r[1],
                "link_protocol": r[2], "link_port": r[3],
                "link_device_interface": r[4], "link_role": r[5],
                "description": r[6], "meta": r[7] or {},
                "source_name": r[8], "target_name": r[9],
            })
        return {"status": "OK", "data": data, "total": total, "page": page, "page_size": page_size}
    except Exception as e:
        logger.error(f"네트워크 연결 목록 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn: conn.close()


@router.get("/network/links/protocols")
async def get_link_protocols():
    """프로토콜 마스터 목록 (tb_protocol_lookup)"""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT protocol_code, display_name, protocol_type, description FROM tb_protocol_lookup ORDER BY display_name")
        rows = cur.fetchall()
        cur.close()
        return {"status": "OK", "data": [
            {"protocol_code": r[0], "display_name": r[1], "protocol_type": r[2], "description": r[3]}
            for r in rows
        ]}
    except Exception as e:
        logger.error(f"프로토콜 목록 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn: conn.close()


@router.post("/network/links")
async def create_network_link(req: dict = Body(...)):
    """네트워크 연결 추가"""
    conn = None
    try:
        src = req.get("source_equipment_id", "").strip()
        tgt = req.get("target_equipment_id", "").strip()
        proto = req.get("link_protocol", "").strip()
        iface = req.get("link_device_interface", "").strip()
        if not src or not tgt or not proto or not iface:
            return {"status": "ERROR", "message": "출발장비, 도착장비, 프로토콜, 인터페이스는 필수입니다."}
        if src == tgt:
            return {"status": "ERROR", "message": "출발장비와 도착장비가 동일합니다."}
        conn = _get_db_connection()
        cur = conn.cursor()
        # FK 검증
        cur.execute("SELECT equipment_id FROM tb_equipment_info WHERE equipment_id IN (%s, %s)", [src, tgt])
        found = {r[0] for r in cur.fetchall()}
        if src not in found:
            return {"status": "ERROR", "message": f"출발장비 '{src}'가 설비 마스터에 없습니다."}
        if tgt not in found:
            return {"status": "ERROR", "message": f"도착장비 '{tgt}'가 설비 마스터에 없습니다."}
        port = req.get("link_port")
        role = req.get("link_role", "").strip() or None
        desc = req.get("description", "").strip() or None
        meta = req.get("meta") or {}
        cur.execute("""
            INSERT INTO tb_network_link
                (source_equipment_id, target_equipment_id, link_protocol, link_device_interface,
                 link_port, link_role, description, meta)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """, [src, tgt, proto, iface, port, role, desc, json.dumps(meta)])
        conn.commit()
        cur.close()
        return {"status": "OK"}
    except Exception as e:
        if conn: conn.rollback()
        msg = str(e)
        if "duplicate key" in msg or "already exists" in msg:
            return {"status": "ERROR", "message": "이미 동일한 연결이 존재합니다."}
        logger.error(f"네트워크 연결 추가 실패: {e}")
        return {"status": "ERROR", "message": msg}
    finally:
        if conn: conn.close()


@router.put("/network/links/{source}/{target}")
async def update_network_link(source: str, target: str, req: dict = Body(...)):
    """네트워크 연결 수정 (PK 변경 불가)"""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        sets, vals = [], []
        for col in ["link_protocol", "link_device_interface", "link_port", "link_role", "description"]:
            if col in req:
                sets.append(f"{col} = %s")
                v = req[col]
                vals.append(v.strip() if isinstance(v, str) else v)
        if "meta" in req:
            sets.append("meta = %s::jsonb")
            vals.append(json.dumps(req["meta"] or {}))
        if not sets:
            return {"status": "ERROR", "message": "수정할 항목이 없습니다."}
        sets.append("updated_at = NOW()")
        vals.extend([source, target])
        cur.execute(f"""
            UPDATE tb_network_link SET {', '.join(sets)}
            WHERE source_equipment_id = %s AND target_equipment_id = %s
        """, vals)
        if cur.rowcount == 0:
            return {"status": "ERROR", "message": "해당 연결을 찾을 수 없습니다."}
        conn.commit()
        cur.close()
        return {"status": "OK"}
    except Exception as e:
        if conn: conn.rollback()
        logger.error(f"네트워크 연결 수정 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn: conn.close()


@router.delete("/network/links/{source}/{target}")
async def delete_network_link(source: str, target: str):
    """네트워크 연결 삭제"""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            DELETE FROM tb_network_link
            WHERE source_equipment_id = %s AND target_equipment_id = %s
        """, [source, target])
        if cur.rowcount == 0:
            return {"status": "ERROR", "message": "해당 연결을 찾을 수 없습니다."}
        conn.commit()
        cur.close()
        return {"status": "OK"}
    except Exception as e:
        if conn: conn.rollback()
        logger.error(f"네트워크 연결 삭제 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn: conn.close()


@router.get("/network/links/equipment-search")
async def search_link_equipment(q: str = ""):
    """연결 폼 장비 검색용 자동완성 (tb_equipment_info)"""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        if q.strip():
            kw = f"%{q.strip()}%"
            cur.execute("""
                SELECT equipment_id, sitename, facilitytype, equipmenttype
                FROM tb_equipment_info
                WHERE equipment_id ILIKE %s OR sitename ILIKE %s OR equipmenttype ILIKE %s
                ORDER BY sitename, equipmenttype, equipment_id
                LIMIT 30
            """, [kw, kw, kw])
        else:
            cur.execute("""
                SELECT equipment_id, sitename, facilitytype, equipmenttype
                FROM tb_equipment_info
                ORDER BY sitename, equipmenttype, equipment_id
                LIMIT 30
            """)
        rows = cur.fetchall()
        cur.close()
        return {"status": "OK", "data": [
            {"equipment_id": r[0], "sitename": r[1], "facilitytype": r[2], "equipmenttype": r[3]}
            for r in rows
        ]}
    except Exception as e:
        logger.error(f"장비 검색 실패: {e}")
        return {"status": "ERROR", "data": []}
    finally:
        if conn: conn.close()


# =============================================================================
# 네트워크 CSV 가져오기
# =============================================================================


@router.post("/network/devices/import/csv")
async def import_network_devices_csv(file: UploadFile):
    """네트워크 장비 CSV 업로드 (tb_equipment_info + tb_network_info 일괄 입력)."""
    conn = None
    try:
        content = await file.read()
        text = content.decode("utf-8-sig")
        reader = csv_mod.reader(io.StringIO(text))
        header = next(reader, None)
        if not header or len(header) < 4:
            return {"status": "ERROR", "message": "CSV 헤더 부족 (최소 4컬럼)"}

        conn = _get_db_connection()
        cur = conn.cursor()
        created = 0
        skipped = 0

        for row in reader:
            if len(row) < 4:
                skipped += 1
                continue
            equipment_id = row[0].strip()
            sitename = row[1].strip()
            facilitytype = row[2].strip()
            equipmenttype = row[3].strip()
            if not equipment_id or not sitename or not facilitytype or not equipmenttype:
                skipped += 1
                continue

            ip_address = row[4].strip() if len(row) > 4 and row[4].strip() else None
            description = row[10].strip() if len(row) > 10 and row[10].strip() else None

            # tb_equipment_info UPSERT
            cur.execute("""
                INSERT INTO tb_equipment_info
                    (equipment_id, sitename, facilitytype, equipmenttype, description)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (equipment_id) DO UPDATE SET
                    sitename = EXCLUDED.sitename,
                    facilitytype = EXCLUDED.facilitytype,
                    equipmenttype = EXCLUDED.equipmenttype,
                    description = EXCLUDED.description,
                    updated_at = NOW()
            """, (equipment_id, sitename, facilitytype, equipmenttype, description))

            # tb_network_info meta JSONB
            net_meta_raw = {
                "gateway": row[5].strip() if len(row) > 5 else "",
                "subnet_mask": row[6].strip() if len(row) > 6 else "",
                "mac_address": row[7].strip() if len(row) > 7 else "",
                "network_role": row[8].strip() if len(row) > 8 else "",
                "ip_assign_method": row[9].strip() if len(row) > 9 else "",
            }
            net_meta = {k: v for k, v in net_meta_raw.items() if v}

            # tb_network_info UPSERT
            cur.execute("""
                INSERT INTO tb_network_info
                    (equipment_id, ip_address, description, meta)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (equipment_id) DO UPDATE SET
                    ip_address = EXCLUDED.ip_address,
                    description = EXCLUDED.description,
                    meta = EXCLUDED.meta,
                    updated_at = NOW()
            """, (equipment_id, ip_address, description, json.dumps(net_meta)))
            created += 1

        conn.commit()
        cur.close()
        return {"status": "OK", "created": created, "skipped": skipped}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"네트워크 장비 CSV 가져오기 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.post("/network/links/import/csv")
async def import_network_links_csv(file: UploadFile):
    """네트워크 연결 CSV 업로드 (일괄 입력)."""
    conn = None
    try:
        content = await file.read()
        text = content.decode("utf-8-sig")
        reader = csv_mod.reader(io.StringIO(text))
        header = next(reader, None)
        if not header or len(header) < 3:
            return {"status": "ERROR", "message": "CSV 헤더 부족 (최소 3컬럼)"}

        conn = _get_db_connection()
        cur = conn.cursor()
        created = 0
        skipped = 0

        for row in reader:
            if len(row) < 3:
                skipped += 1
                continue
            source_equipment_id = row[0].strip()
            # row[1] = 출발현장 (ignored)
            target_equipment_id = row[2].strip()
            # row[3] = 도착현장 (ignored)
            link_protocol = row[4].strip() if len(row) > 4 and row[4].strip() else None
            if not source_equipment_id or not target_equipment_id:
                skipped += 1
                continue

            link_port_str = row[5].strip() if len(row) > 5 else ""
            link_port = int(link_port_str) if link_port_str.isdigit() else None
            link_device_interface = row[6].strip() if len(row) > 6 and row[6].strip() else None
            link_role = row[7].strip() if len(row) > 7 and row[7].strip() else None
            direction = row[8].strip() if len(row) > 8 and row[8].strip() else ""
            description = row[9].strip() if len(row) > 9 and row[9].strip() else None

            meta_raw = {"direction": direction} if direction else {}
            meta = {k: v for k, v in meta_raw.items() if v}

            cur.execute("""
                INSERT INTO tb_network_link
                    (source_equipment_id, target_equipment_id,
                     link_protocol, link_port, link_device_interface,
                     link_role, description, meta)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (source_equipment_id, target_equipment_id) DO UPDATE SET
                    link_protocol = EXCLUDED.link_protocol,
                    link_port = EXCLUDED.link_port,
                    link_device_interface = EXCLUDED.link_device_interface,
                    link_role = EXCLUDED.link_role,
                    description = EXCLUDED.description,
                    meta = EXCLUDED.meta,
                    updated_at = NOW()
            """, (source_equipment_id, target_equipment_id,
                  link_protocol, link_port, link_device_interface,
                  link_role, description,
                  json.dumps(meta) if meta else None))
            created += 1

        conn.commit()
        cur.close()
        return {"status": "OK", "created": created, "skipped": skipped}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"네트워크 연결 CSV 가져오기 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()
