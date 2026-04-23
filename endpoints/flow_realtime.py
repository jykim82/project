"""
용수 흐름 실시간 모니터링 API

엔드포인트:
  GET /gis/facility-info      — GIS 시설 상세 정보 (배수지/가압장)
  GET /flow-map/realtime       — 시설별 최신 유량/수위/압력 + 교차검증 상태
  GET /flow-map/node-alarms    — 시설별 최근 알람 목록
  GET /equipments/auto-map     — 설비↔태그 자동 매핑 실행/미리보기

의존성은 init()을 통해 주입받는다.
"""

import logging
import re
from datetime import datetime, timedelta

from fastapi import APIRouter

from shared.timeseries import get_chunks_for_range, query_chunks_raw

logger = logging.getLogger("slm")

router = APIRouter(tags=["flow-realtime"])

# ---------------------------------------------------------------------------
# 모듈-로컬 상수
# ---------------------------------------------------------------------------
_LEVEL_ALARM_SETTING_RE = re.compile(
    r'(HH|LL|H설정|L설정|_H_|_L_|설정값)', re.IGNORECASE
)


def _group_priority(gc: str) -> int:
    """유량 그룹 우선순위 — OUTLET > INSTANT > INLET > 기타."""
    _P = {
        "FLOW_OUTLET": 4, "PRESSURE_DISCHARGE": 4, "FLOW_INSTANT": 3,
        "PRESSURE_OUTLET": 3, "FLOW_INLET": 2, "PRESSURE_INLET": 2,
        "WATER_LEVEL": 3,
    }
    return _P.get(gc, 1)


# ---------------------------------------------------------------------------
# 주입 의존성 (init 에서 설정)
# ---------------------------------------------------------------------------
_get_db_connection = None
_get_anomaly_scan_cache = None
_get_flow_balance_cache = None
_get_flow_baseline_cache = None
_auto_map_equipment_tags_fn = None


def init(get_db_connection_fn, get_anomaly_scan_cache_fn,
         get_flow_balance_cache_fn, get_flow_baseline_cache_fn,
         auto_map_equipment_tags_fn):
    """모듈 초기화 — 외부 의존성 주입."""
    global _get_db_connection, _get_anomaly_scan_cache
    global _get_flow_balance_cache, _get_flow_baseline_cache
    global _auto_map_equipment_tags_fn

    _get_db_connection = get_db_connection_fn
    _get_anomaly_scan_cache = get_anomaly_scan_cache_fn
    _get_flow_balance_cache = get_flow_balance_cache_fn
    _get_flow_baseline_cache = get_flow_baseline_cache_fn
    _auto_map_equipment_tags_fn = auto_map_equipment_tags_fn


def _get_conn():
    if _get_db_connection is None:
        raise RuntimeError("flow_realtime not initialized")
    return _get_db_connection()


# =========================================================================
# GET /gis/facility-info
# =========================================================================

@router.get("/gis/facility-info")
async def get_gis_facility_info(sitename: str, facilitytype: str):
    """GIS 시설 상세 정보 — 배수지/가압장 뷰 데이터 반환."""
    conn = None
    try:
        conn = _get_conn()
        cur = conn.cursor()

        result: dict = {"sitename": sitename, "facilitytype": facilitytype}

        if facilitytype == "배수지":
            cur.execute("""
                SELECT v.general_overview, v.meta, i.site_photo_url
                FROM v_reservoir_info_status v
                LEFT JOIN tb_service_reservoir_info i ON i.sitename = v.sitename
                WHERE v.sitename = %s
            """, (sitename,))
            row = cur.fetchone()
            if row:
                result["general_overview"] = row[0] if row[0] else None
                result["equipment_meta"] = row[1] if row[1] else None
                result["site_photo_url"] = row[2] if row[2] else None

        elif facilitytype == "가압장":
            cur.execute("""
                SELECT v.general_overview, v.meta, i.site_photo_url
                FROM v_booster_station_info_status v
                LEFT JOIN tb_service_booster_station_info i ON i.sitename = v.sitename
                WHERE v.sitename = %s
            """, (sitename,))
            row = cur.fetchone()
            if row:
                result["general_overview"] = row[0] if row[0] else None
                result["equipment_meta"] = row[1] if row[1] else None
                result["site_photo_url"] = row[2] if row[2] else None

        # 알람: 진행중은 기간 무제한 + 해제는 최근 30일
        cur.execute("""
            SELECT TO_CHAR(alarm_start_time, 'YYYY-MM-DD HH24:MI') AS start_time,
                   tagsn,
                   alarm_severity, alarm_msg, alarm_status, alarm_category,
                   COALESCE(meta->>'cause', diagnosed_cause) AS cause,
                   COALESCE(meta->>'action', countermeasure) AS action,
                   meta->>'title' AS diag_title
            FROM tb_equipment_alarm_report
            WHERE sitename = %s
              AND (alarm_status = '진행중' OR alarm_start_time >= NOW() - INTERVAL '30 days')
            ORDER BY
              CASE WHEN alarm_status = '진행중' THEN 0 ELSE 1 END,
              alarm_start_time DESC
            LIMIT 30
        """, (sitename,))
        alarm_cols = [d[0] for d in cur.description]
        result["alarms"] = [dict(zip(alarm_cols, r)) for r in cur.fetchall()]

        cur.close()
        return result

    except Exception as e:
        logger.error(f"GIS 시설정보 조회 실패: {e}")
        return {"sitename": sitename, "facilitytype": facilitytype, "info": {}, "alarms": []}
    finally:
        if conn:
            conn.close()


# =========================================================================
# GET /flow-map/realtime
# =========================================================================

@router.get("/flow-map/realtime")
async def get_flow_map_realtime():
    """용수 흐름 실시간 모니터링 — 시설별 최신 유량/수위/압력 + 교차검증 상태."""
    conn = None
    try:
        conn = _get_conn()
        cur = conn.cursor()

        # 1) 토폴로지
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

        # 2) 고유 시설 목록
        node_set: set[tuple[str, str]] = set()
        for e in edges:
            node_set.add((e["upstream_sitename"], e["upstream_facilitytype"]))
            node_set.add((e["downstream_sitename"], e["downstream_facilitytype"]))

        # 3) 시설별 대표 태그 최신값 조회
        _TARGET_GROUPS = [
            "FLOW_OUTLET", "FLOW_INSTANT", "FLOW_INLET", "FLOW_CUMULATIVE",
            "WATER_LEVEL",
            "PRESSURE_OUTLET", "PRESSURE_DISCHARGE", "PRESSURE_INLET", "PRESSURE",
        ]
        cur.execute("""
            SELECT t.sitename, t.facilitytype, dg.group_code, t.tagsn, t.datainfo
            FROM tb_tag_info t
            JOIN tb_tag_group_map gm ON gm.tagsn = t.tagsn
            JOIN tb_tag_data_group dg ON dg.group_id = gm.group_id
            WHERE dg.group_code = ANY(%s)
              AND (dg.group_code = 'FLOW_CUMULATIVE' OR COALESCE(t.datainfo, '') NOT LIKE '%%적산%%')
              AND t.tagtype = 'Analog Input'
            ORDER BY t.sitename, dg.group_code
        """, (_TARGET_GROUPS,))
        tag_rows = cur.fetchall()

        # 시설별 그룹별 후보 태그 수집 (여러 개 유지)
        facility_tag_candidates: dict[tuple[str, str], dict[str, list[dict]]] = {}
        for sitename, ft, gc, tagsn, datainfo in tag_rows:
            key = (sitename, ft)
            if key not in facility_tag_candidates:
                facility_tag_candidates[key] = {}
            if gc not in facility_tag_candidates[key]:
                facility_tag_candidates[key][gc] = []
            facility_tag_candidates[key][gc].append(
                {"tagsn": tagsn, "tagname": datainfo or tagsn, "datainfo": datainfo}
            )

        # 4) 모든 후보 tagsn의 최신값 일괄 조회
        all_tagsns: list[str] = []
        tagsn_to_facility: dict[str, tuple[str, str, str]] = {}
        for (sn, ft), groups in facility_tag_candidates.items():
            for gc, candidates in groups.items():
                for cand in candidates:
                    tsn = cand["tagsn"]
                    all_tagsns.append(tsn)
                    tagsn_to_facility[tsn] = (sn, ft, gc)

        latest_values: dict[str, float] = {}
        if all_tagsns:
            # DB에 최근 1시간 데이터가 없으면 최신 데이터 기준으로 쿼리 창 이동
            cur.execute("SELECT max(logtime) FROM tb_tag_raw_data")
            _max_row = cur.fetchone()
            _max_logtime = _max_row[0] if _max_row and _max_row[0] else datetime.now()
            # 최신 데이터 시점이 1시간 이내면 now() 사용, 그보다 오래됐으면 최신 시점 + 10분 버퍼
            if (datetime.now() - _max_logtime.replace(tzinfo=None)).total_seconds() > 3600:
                _to = (_max_logtime.replace(tzinfo=None) + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
                _from = (_max_logtime.replace(tzinfo=None) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
            else:
                _to = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                _from = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
            chunks = get_chunks_for_range(cur, _from, _to)
            if chunks:
                raw = query_chunks_raw(cur, chunks, all_tagsns, _from, _to)
                for tsn, _, val in raw:
                    latest_values[tsn] = val

        # 4-a) 후보 전체 유지 (노드 구성 시 활성 태그 합산)

        # 4-b) 배수지 용수공급가능시간 + 일평균 유입/유출/사용량 조회
        reservoir_supply: dict[str, dict] = {}
        reservoir_sites = [sn for sn, ft in node_set if ft == "배수지"]
        if reservoir_sites:
            try:
                cur.execute("""
                    SELECT s.sitename, s.total_supply_time, s.supply_time_status, s.supply_time_reason,
                           v.avg_inflow, v.avg_outflow, v.avg_usage,
                           nmf.night_min_flow
                    FROM tb_service_reservoir_status s
                    LEFT JOIN v_reservoir_info_status v ON s.sitename = v.sitename
                    LEFT JOIN LATERAL (
                        SELECT round(MIN(r.val)::numeric, 2) AS night_min_flow
                        FROM tb_tag_raw_data r
                        JOIN tb_tag_info ti ON r.tagsn = ti.tagsn
                        WHERE ti.sitename = s.sitename
                          AND ti.facilitytype = '배수지'
                          AND ti.datainfo ILIKE '%%유출%%유량%%순시%%'
                          AND EXTRACT(HOUR FROM r.logtime) BETWEEN 2 AND 4
                          AND r.logtime >= now() - interval '7 days'
                    ) nmf ON true
                    WHERE s.sitename = ANY(%s)
                """, (reservoir_sites,))
                for row in cur.fetchall():
                    sn = row[0]
                    reservoir_supply[sn] = {
                        "hours": float(row[1]) if row[1] is not None else None,
                        "status": row[2] or "",
                        "reason": row[3] or "",
                        "avg_inflow": float(row[4]) if row[4] is not None else None,
                        "avg_outflow": float(row[5]) if row[5] is not None else None,
                        "avg_usage": float(row[6]) if row[6] is not None else None,
                        "night_min_flow": float(row[7]) if row[7] is not None else None,
                    }
            except Exception as e:
                logger.warning(f"배수지 공급가능시간 조회 실패: {e}")
                conn.rollback()  # 컬럼 미존재 시 롤백 후 계속

        # 4-b2) 배수지 적산 유량 (현재적산 + 금일적산)
        if reservoir_sites:
            try:
                cur.execute("""
                    WITH accum_tags AS (
                        SELECT ti.sitename, ti.tagsn, ti.datainfo,
                            CASE WHEN ti.datainfo ILIKE '%%유입%%' THEN 'inflow'
                                 WHEN ti.datainfo ILIKE '%%유출%%' THEN 'outflow'
                            END AS flow_dir
                        FROM tb_tag_info ti
                        WHERE ti.sitename = ANY(%s)
                          AND ti.facilitytype = '배수지'
                          AND ti.datainfo ILIKE '%%적산%%'
                          AND (ti.datainfo ILIKE '%%유입%%' OR ti.datainfo ILIKE '%%유출%%')
                          AND ti.tagtype = 'Analog Input'
                    ),
                    latest AS (
                        SELECT a.sitename, a.flow_dir, a.tagsn,
                            (SELECT r.val FROM tb_tag_raw_data r
                             WHERE r.tagsn = a.tagsn AND r.logtime >= now() - interval '10 minutes'
                             ORDER BY r.logtime DESC LIMIT 1) AS current_val
                        FROM accum_tags a
                    ),
                    midnight AS (
                        SELECT a.sitename, a.flow_dir, a.tagsn,
                            (SELECT r.val FROM tb_tag_raw_data r
                             WHERE r.tagsn = a.tagsn
                               AND r.logtime >= date_trunc('day', now())
                               AND r.logtime < date_trunc('day', now()) + interval '10 minutes'
                             ORDER BY r.logtime ASC LIMIT 1) AS midnight_val
                        FROM accum_tags a
                    )
                    SELECT l.sitename, l.flow_dir,
                           l.current_val,
                           CASE WHEN l.current_val IS NOT NULL AND m.midnight_val IS NOT NULL
                                THEN l.current_val - m.midnight_val ELSE NULL END AS today_accum
                    FROM latest l
                    LEFT JOIN midnight m ON l.sitename = m.sitename AND l.flow_dir = m.flow_dir AND l.tagsn = m.tagsn
                    WHERE l.current_val IS NOT NULL
                """, (reservoir_sites,))
                for row in cur.fetchall():
                    sn, flow_dir, current_val, today_accum = row
                    if sn in reservoir_supply:
                        key_curr = f"{flow_dir}_accum_current"
                        key_today = f"{flow_dir}_accum_today"
                        reservoir_supply[sn][key_curr] = float(current_val) if current_val is not None else None
                        reservoir_supply[sn][key_today] = float(today_accum) if today_accum is not None else None
            except Exception as e:
                logger.warning(f"배수지 적산유량 조회 실패: {e}")
                conn.rollback()

        # 4-c) 가압장 펌프 가동 상태 조회
        #   - 동일 펌프가 인버터/직기동 2개 운전 태그를 갖는 경우(예: 삼봉 펌프1 →
        #     "인버터1 운전" + "직기동1 운전") 중복 카운트 방지 위해 펌프 번호
        #     기준 DISTINCT 집계. val=1 태그 하나라도 있으면 해당 펌프 가동으로 간주.
        booster_pump_status: dict[str, dict] = {}
        booster_sites = [sn for sn, ft in node_set if ft == "가압장"]
        if booster_sites:
            try:
                cur.execute("""
                    WITH pump_tags AS (
                        SELECT ti.sitename,
                               -- datainfo 에서 펌프 번호 추출 (가압펌프N/인버터N/직기동N/펌프N)
                               COALESCE(
                                   substring(ti.datainfo FROM '가압펌프\\s*(\\d+)'),
                                   substring(ti.datainfo FROM '인버터\\s*(\\d+)'),
                                   substring(ti.datainfo FROM '직기동\\s*(\\d+)'),
                                   substring(ti.datainfo FROM '펌프\\s*(\\d+)')
                               ) AS pump_num,
                               r.val
                        FROM tb_tag_info ti
                        JOIN LATERAL (
                            SELECT val FROM tb_tag_raw_data r
                            WHERE r.tagsn = ti.tagsn
                              AND r.logtime >= now() - interval '10 minutes'
                            ORDER BY logtime DESC LIMIT 1
                        ) r ON true
                        WHERE ti.sitename = ANY(%s)
                          AND ti.facilitytype = '가압장'
                          AND ti.equipmenttype ~ '가압펌프'
                          AND (ti.datainfo ~* '운전|동작' OR ti.datadesc ~* 'RUN|동작')
                          AND NOT ti.datainfo ~* 'FAULT|FLT|STOP|정지'
                          AND ti.tagtype = 'Digital Input'
                    )
                    SELECT sitename,
                           COUNT(DISTINCT pump_num) FILTER (WHERE pump_num IS NOT NULL) AS total_pumps,
                           COUNT(DISTINCT pump_num) FILTER (WHERE pump_num IS NOT NULL AND val = 1) AS running_pumps
                    FROM pump_tags
                    GROUP BY sitename
                    HAVING COUNT(DISTINCT pump_num) FILTER (WHERE pump_num IS NOT NULL) > 0
                """, (booster_sites,))
                for row in cur.fetchall():
                    booster_pump_status[row[0]] = {
                        "total": int(row[1]),
                        "running": int(row[2]),
                    }
            except Exception as e:
                logger.warning(f"가압장 펌프 가동 상태 조회 실패: {e}")
                conn.rollback()

        # 4-d) 배수지 수질 (탁도/잔류염소/PH/전기전도/온도)
        reservoir_water_quality: dict[str, dict] = {}
        reservoir_sites = [sn for sn, ft in node_set if ft == "배수지"]
        if reservoir_sites:
            try:
                cur.execute("""
                    SELECT ti.sitename,
                      MAX(CASE WHEN ti.datainfo ILIKE '%%탁도%%'        THEN r.val END) AS turbidity,
                      MAX(CASE WHEN ti.datainfo ILIKE '%%잔류염소%%'    THEN r.val END) AS chlorine,
                      MAX(CASE WHEN ti.datainfo ILIKE '%%PH%%'         THEN r.val END) AS ph,
                      MAX(CASE WHEN ti.datainfo ILIKE '%%전기전도%%'    THEN r.val END) AS conductivity,
                      MAX(CASE WHEN ti.datainfo ILIKE '%%온도%%'
                               AND ti.datainfo NOT ILIKE '%%조도%%'      THEN r.val END) AS temperature
                    FROM tb_tag_info ti
                    JOIN LATERAL (
                        SELECT val FROM tb_tag_raw_data r
                        WHERE r.tagsn = ti.tagsn
                          AND r.logtime >= now() - interval '30 minutes'
                        ORDER BY logtime DESC LIMIT 1
                    ) r ON true
                    WHERE ti.sitename = ANY(%s)
                      AND ti.facilitytype = '배수지'
                      AND ti.tagtype = 'Analog Input'
                      AND (ti.datainfo ILIKE '%%탁도%%' OR ti.datainfo ILIKE '%%잔류염소%%'
                           OR ti.datainfo ILIKE '%%PH%%' OR ti.datainfo ILIKE '%%전기전도%%'
                           OR (ti.datainfo ILIKE '%%온도%%' AND ti.datainfo NOT ILIKE '%%조도%%'))
                      AND ti.datainfo NOT LIKE '%%SET%%'
                      AND ti.datainfo NOT LIKE '%%알람%%'
                    GROUP BY ti.sitename
                """, (reservoir_sites,))
                for row in cur.fetchall():
                    sn, turb, chl, ph, cond, temp = row
                    quality: dict = {}
                    if turb is not None: quality["turbidity"] = float(turb)
                    if chl  is not None: quality["chlorine"]  = float(chl)
                    if ph   is not None: quality["ph"]        = float(ph)
                    if cond is not None: quality["conductivity"] = float(cond)
                    if temp is not None: quality["temperature"]  = float(temp)
                    if quality:
                        reservoir_water_quality[sn] = quality
            except Exception as e:
                logger.warning(f"배수지 수질 조회 실패: {e}")
                conn.rollback()

        # 5) 노드 데이터 구성 — 같은 그룹의 활성 태그 합산
        node_data = _build_node_data(
            node_set, facility_tag_candidates, latest_values,
            reservoir_supply, booster_pump_status,
            reservoir_water_quality=reservoir_water_quality,
        )

        # 5-b) 진행 중인 알람 조회 — 시설별 가장 심각한 알람 등급 판정
        _attach_alarm_severity(conn, node_data)

        # 5-c) 통신이상 태그 조회
        _attach_comm_errors(conn, node_data)

        # 5-d) 설비 장애 감지
        _attach_equip_failures(conn, node_data)

        # 6) 교차검증 불일치 (캐시 참조)
        cross_mismatches_map = _build_cross_mismatches(edges, node_data)

        # 6-b ~ 6-c) 상류-하류 유량 불일치 인라인 감지 + 가동률
        _detect_flow_anomalies(conn, edges, node_data, cross_mismatches_map)

        # 7) 물 수지 불균형 (엣지별)
        edge_imbalance = _build_edge_imbalance()

        cur.close()
        return {
            "status": "OK",
            "edges": edges,
            "nodes": node_data,
            "cross_mismatches": {k: v for k, v in cross_mismatches_map.items() if v},
            "edge_imbalance": edge_imbalance,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"용수 흐름 실시간 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# realtime 내부 헬퍼 함수들
# ---------------------------------------------------------------------------

def _build_node_data(node_set, facility_tag_candidates, latest_values,
                     reservoir_supply, booster_pump_status,
                     reservoir_water_quality=None):
    """시설별 노드 데이터 구성 — 같은 그룹의 활성 태그 합산."""
    reservoir_water_quality = reservoir_water_quality or {}
    baseline_cache = _get_flow_baseline_cache() if _get_flow_baseline_cache else {}
    node_data: dict[str, dict] = {}

    for sn, ft in node_set:
        nid = f"{sn}__{ft}"
        cand_groups = facility_tag_candidates.get((sn, ft), {})
        metrics: dict[str, dict] = {}
        for gc, candidates in cand_groups.items():
            if gc == "FLOW_CUMULATIVE":
                category = "flow_accum"
            elif "FLOW" in gc:
                category = "flow"
            elif "LEVEL" in gc:
                category = "level"
            else:
                category = "pressure"
            is_summable = category == "flow"

            active_vals = []
            active_names = []
            for cand in candidates:
                v = latest_values.get(cand["tagsn"])
                if v is not None and v > 0:
                    active_vals.append(v)
                    active_names.append(cand["tagname"])
            if not active_vals:
                first = candidates[0]
                v0 = latest_values.get(first["tagsn"])
                if v0 is not None:
                    active_vals = [v0]
                    active_names = [first["tagname"]]
            if not active_vals:
                continue

            if is_summable:
                total_val = sum(active_vals)
                tag_label = (
                    active_names[0] if len(active_names) == 1
                    else f"{active_names[0]} 외 {len(active_names)-1}개 합산"
                )
            else:
                # 수위/압력: 첫 번째 활성 태그 대표값
                total_val = active_vals[0]
                tag_label = active_names[0]
            if category not in metrics or _group_priority(gc) > _group_priority(metrics[category].get("group_code", "")):
                # 기준선(7일 동일 요일·시간대 평균) 참조
                primary_tsn = candidates[0]["tagsn"]
                baseline_val = baseline_cache.get(primary_tsn)
                metric_entry: dict = {
                    "value": round(total_val, 2),
                    "group_code": gc,
                    "tagname": tag_label,
                    "tagsn": primary_tsn,
                    "tag_count": len(active_vals) if is_summable else 1,
                }
                if baseline_val and baseline_val > 0.01:
                    metric_entry["baseline_avg"] = baseline_val
                metrics[category] = metric_entry

        # level_zones: 배수지 구역별 수위 태그 전체 (2개 이상일 때만)
        # HH/LL/H/L 설정값 태그는 제외 (실제 수위 측정값만)
        level_zones: list[dict] = []
        if ft == "배수지":
            for gc, candidates in cand_groups.items():
                if "LEVEL" not in gc:
                    continue
                for cand in candidates:
                    di = cand.get("datainfo", "") or ""
                    # 알람 설정값 태그 제외
                    if _LEVEL_ALARM_SETTING_RE.search(di):
                        continue
                    v = latest_values.get(cand["tagsn"])
                    if v is None:
                        continue
                    level_zones.append({
                        "value": round(v, 3),
                        "group_code": gc,
                        "tagname": cand["tagname"],
                        "tagsn": cand["tagsn"],
                        "datainfo": di,
                    })

        node_entry: dict = {
            "sitename": sn,
            "facilitytype": ft,
            "metrics": metrics,
        }
        if len(level_zones) > 1:
            # 자연 정렬: datainfo 내 숫자 기준 (수위1, 수위2, 1지, 2지 ...)
            def _zone_sort_key(z: dict) -> tuple:
                di = z.get("datainfo", "")
                nums = re.findall(r'\d+', di)
                return (int(nums[0]) if nums else 999, di)
            level_zones.sort(key=_zone_sort_key)
            node_entry["level_zones"] = level_zones
        # 배수지: 용수공급가능시간 추가
        if ft == "배수지" and sn in reservoir_supply:
            node_entry["supply_time"] = reservoir_supply[sn]
        # 가압장: 펌프 가동 수
        if ft == "가압장":
            pump_info = booster_pump_status.get(sn)
            if pump_info:
                node_entry["pump_status"] = pump_info
        elif ft == "배수지":
            wq = reservoir_water_quality.get(sn)
            if wq:
                node_entry["water_quality"] = wq
        node_data[nid] = node_entry

    return node_data


def _attach_alarm_severity(conn, node_data):
    """진행 중인 알람 조회 — 시설별 가장 심각한 알람 등급 판정."""
    alarm_severity_map: dict[str, str] = {}
    try:
        cur_alarm = conn.cursor()
        cur_alarm.execute("""
            SELECT t.sitename, t.facilitytype, a.alarm_severity
            FROM tb_equipment_alarm_report a
            JOIN tb_tag_info t ON t.tagsn = a.tagsn
            WHERE a.alarm_status = '진행중'
              AND a.alarm_severity IN ('경고', '주의')
            ORDER BY t.sitename
        """)
        for r_sn, r_ft, r_sev in cur_alarm.fetchall():
            nid = f"{r_sn}__{r_ft}"
            existing = alarm_severity_map.get(nid)
            if existing != "경고":  # 경고가 최우선
                alarm_severity_map[nid] = r_sev
        cur_alarm.close()
    except Exception as e:
        logger.debug("알람 조회 실패: %s", e)

    for nid, sev in alarm_severity_map.items():
        if nid in node_data:
            node_data[nid]["alarm_severity"] = sev


def _attach_comm_errors(conn, node_data):
    """통신이상 태그 조회 — Digital Input, datainfo LIKE '%통신이상%', val=1이면 알람."""
    comm_error_nodes: set[str] = set()
    try:
        cur2 = conn.cursor()
        cur2.execute("""
            SELECT DISTINCT t.sitename, t.facilitytype
            FROM tb_tag_info t
            WHERE t.tagtype = 'Digital Input'
              AND t.datainfo LIKE '%%통신이상%%'
              AND EXISTS (
                  SELECT 1 FROM tb_tag_raw_data r
                  WHERE r.tagsn = t.tagsn
                    AND r.logtime >= NOW() - INTERVAL '30 minutes'
                    AND r.val = 1
              )
        """)
        for row in cur2.fetchall():
            comm_error_nodes.add(f"{row[0]}__{row[1]}")
        cur2.close()
    except Exception as e:
        logger.debug("통신이상 태그 조회 실패: %s", e)

    for nid in comm_error_nodes:
        if nid in node_data:
            node_data[nid]["comm_error"] = True


def _attach_equip_failures(conn, node_data):
    """설비 장애 감지 — 네트워크 장애 + DI 장애(COMM_ERROR/EQUIP_FAULT/POWER_FAULT)."""
    equip_failures_map: dict[str, list[dict]] = {}
    try:
        # A) 네트워크 장애 설비 -> 시설별 집계
        cur_ef = conn.cursor()
        cur_ef.execute("""
            WITH lt AS (SELECT MAX(check_time) AS ct FROM tb_network_status)
            SELECT e.sitename, e.facilitytype, COUNT(*) AS cnt
            FROM tb_network_status ns
            JOIN lt ON ns.check_time = lt.ct
            JOIN tb_equipment_info e ON ns.equipment_id = e.equipment_id
            WHERE ns.is_alive = false
            GROUP BY e.sitename, e.facilitytype
        """)
        for sn, ft, cnt in cur_ef.fetchall():
            nid = f"{sn}__{ft}"
            equip_failures_map.setdefault(nid, []).append({
                "type": "network_down",
                "label": "네트워크 단절",
                "count": cnt,
            })

        # B) DI 장애 태그 (COMM_ERROR/EQUIP_FAULT/POWER_FAULT) val=1 최근 10분
        cur_ef.execute("""
            SELECT ti.sitename, ti.facilitytype, dg.group_code, COUNT(DISTINCT gm.tagsn) AS cnt
            FROM tb_tag_group_map gm
            JOIN tb_tag_data_group dg ON gm.group_id = dg.group_id
            JOIN tb_tag_info ti ON gm.tagsn = ti.tagsn
            WHERE dg.group_code IN ('COMM_ERROR', 'EQUIP_FAULT', 'POWER_FAULT')
              AND EXISTS (
                  SELECT 1 FROM tb_tag_raw_data r
                  WHERE r.tagsn = gm.tagsn
                    AND r.logtime >= now() - interval '10 minutes'
                    AND r.val = 1
              )
            GROUP BY ti.sitename, ti.facilitytype, dg.group_code
        """)
        _DI_LABEL = {
            "COMM_ERROR": "통신이상",
            "EQUIP_FAULT": "설비고장",
            "POWER_FAULT": "전원이상",
        }
        _DI_TYPE = {
            "COMM_ERROR": "comm_error",
            "EQUIP_FAULT": "equip_fault",
            "POWER_FAULT": "power_fault",
        }
        for sn, ft, gc, cnt in cur_ef.fetchall():
            nid = f"{sn}__{ft}"
            equip_failures_map.setdefault(nid, []).append({
                "type": _DI_TYPE.get(gc, "comm_error"),
                "label": _DI_LABEL.get(gc, gc),
                "count": cnt,
            })
        cur_ef.close()
    except Exception as e:
        logger.debug("설비 장애 조회 실패(무시): %s", e)

    for nid, failures in equip_failures_map.items():
        if nid in node_data:
            node_data[nid]["equip_failures"] = failures


def _build_cross_mismatches(edges, node_data):
    """교차검증 불일치 (캐시 참조) — ANOMALY_SCAN_CACHE 기반."""
    cross_mismatches_map: dict[str, list[str]] = {}
    anomaly_cache, _ = _get_anomaly_scan_cache() if _get_anomaly_scan_cache else ({}, None)
    if anomaly_cache:
        cm_list = anomaly_cache.get("processed_data", {}).get("cross_facility_mismatches", [])
        for cm in (cm_list or []):
            ds_sn = cm.get("downstream_sitename", "")
            ds_ft = cm.get("downstream_facilitytype", "")
            checks = cm.get("checks", [])
            # checks는 리스트: [{"type": ..., "level": ...}, ...]
            error_checks = [c["type"] for c in checks if c.get("level") in ("error", "warn")]
            if error_checks:
                nid = f"{ds_sn}__{ds_ft}"
                cross_mismatches_map[nid] = error_checks
    return cross_mismatches_map


def _detect_flow_anomalies(conn, edges, node_data, cross_mismatches_map):
    """상류-하류 유량 불일치 인라인 감지 (zero_flow, flow_disparity, 가동률)."""
    _UPSTREAM_FLOW_THRESHOLD = 1.0  # m3/h

    # 상류별 하류 유량 합산용
    _us_ds_map: dict[str, list[tuple[str, float]]] = {}
    _us_flow_map: dict[str, float] = {}

    for e in edges:
        us_nid = f"{e['upstream_sitename']}__{e['upstream_facilitytype']}"
        ds_nid = f"{e['downstream_sitename']}__{e['downstream_facilitytype']}"
        us_node = node_data.get(us_nid)
        ds_node = node_data.get(ds_nid)
        if not us_node or not ds_node:
            continue
        us_flow_val = (us_node.get("metrics", {}).get("flow") or {}).get("value")
        ds_flow_val = (ds_node.get("metrics", {}).get("flow") or {}).get("value")
        # (1) zero_flow: 하류 유량 ~ 0
        if us_flow_val is not None and us_flow_val > _UPSTREAM_FLOW_THRESHOLD:
            if ds_flow_val is not None and ds_flow_val <= 0.01:
                cross_mismatches_map.setdefault(ds_nid, [])
                if "zero_flow" not in cross_mismatches_map[ds_nid]:
                    cross_mismatches_map[ds_nid].append("zero_flow")
        # 상류별 하류 합산 기록
        if us_flow_val is not None and us_flow_val > _UPSTREAM_FLOW_THRESHOLD:
            _us_flow_map[us_nid] = us_flow_val
            _us_ds_map.setdefault(us_nid, [])
            if ds_flow_val is not None:
                _us_ds_map[us_nid].append((ds_nid, ds_flow_val))

    # (2) flow_disparity: 상류 대비 하류 총합 비율 체크
    _DISPARITY_RATIO = 0.20
    for us_nid, ds_list in _us_ds_map.items():
        us_flow = _us_flow_map.get(us_nid, 0)
        if us_flow < 10:  # 상류 유량 10 미만이면 소규모 시설로 스킵
            continue
        ds_total = sum(v for _, v in ds_list)
        if ds_total < us_flow * _DISPARITY_RATIO:
            for ds_nid, _ in ds_list:
                cross_mismatches_map.setdefault(ds_nid, [])
                if "flow_disparity" not in cross_mismatches_map[ds_nid]:
                    cross_mismatches_map[ds_nid].append("flow_disparity")

    # (3) 상류 활성인데 하류 유량의 최근 1시간 가동률 체크
    _LOW_ACTIVE_RATIO = 0.30
    _GRAVITY_ACTIVE_RATIO = 0.80

    # 상류 시설유형 추적
    _ds_us_ft: dict[str, str] = {}
    _ds_us_pressure: dict[str, float] = {}
    for e in edges:
        us_nid = f"{e['upstream_sitename']}__{e['upstream_facilitytype']}"
        ds_nid = f"{e['downstream_sitename']}__{e['downstream_facilitytype']}"
        us_node = node_data.get(us_nid)
        if us_nid in _us_flow_map and us_node:
            _ds_us_ft[ds_nid] = e["upstream_facilitytype"]
            us_pressure = (us_node.get("metrics", {}).get("pressure") or {}).get("value")
            if us_pressure is not None:
                _ds_us_pressure[ds_nid] = us_pressure

    # 하류 노드의 flow tagsn 수집
    _ds_flow_tags: dict[str, str] = {}
    for us_nid, ds_list in _us_ds_map.items():
        for ds_nid, ds_flow_val in ds_list:
            ds_node = node_data.get(ds_nid)
            if not ds_node:
                continue
            ds_flow_tag = (ds_node.get("metrics", {}).get("flow") or {}).get("tagsn")
            if ds_flow_tag:
                _ds_flow_tags[ds_flow_tag] = ds_nid

    if _ds_flow_tags:
        try:
            cur_active = conn.cursor()
            tag_list = list(_ds_flow_tags.keys())
            cur_active.execute("""
                SELECT tagsn,
                       COUNT(*) FILTER (WHERE val > 0.5) AS active_cnt,
                       COUNT(*) AS total_cnt
                FROM tb_tag_raw_data
                WHERE tagsn = ANY(%s)
                  AND logtime >= now() - interval '1 hour'
                GROUP BY tagsn
            """, (tag_list,))
            for tsn, active_cnt, total_cnt in cur_active.fetchall():
                if total_cnt < 3:  # 데이터 부족 스킵
                    continue
                active_ratio = active_cnt / total_cnt
                ds_nid = _ds_flow_tags[tsn]
                us_ft = _ds_us_ft.get(ds_nid, "")
                cross_mismatches_map.setdefault(ds_nid, [])
                if us_ft == "배수지":
                    # 중력식: 배수지->하류는 거의 100% 활성이어야 정상
                    if active_ratio < _GRAVITY_ACTIVE_RATIO:
                        check_type = "gravity_no_flow"
                    else:
                        continue
                elif us_ft == "가압장":
                    # 펌프식: 가압장 압력>0 (펌프 가동) 확인
                    if active_ratio >= _LOW_ACTIVE_RATIO:
                        continue  # 30% 이상이면 펌프 가동 패턴으로 정상
                    us_prs = _ds_us_pressure.get(ds_nid, 0)
                    if us_prs > 0.5:
                        # 펌프 가동 중(토출압력 활성)인데 하류 유량 불안정
                        check_type = "pump_no_flow"
                    else:
                        # 펌프 미가동(압력 0) -> 간헐적 동작 가능, 스킵
                        continue
                else:
                    if active_ratio >= _LOW_ACTIVE_RATIO:
                        continue
                    check_type = "low_active"
                if check_type not in cross_mismatches_map[ds_nid]:
                    cross_mismatches_map[ds_nid].append(check_type)
            cur_active.close()
        except Exception as e:
            logger.debug("가동률 조회 실패(무시): %s", e)


def _build_edge_imbalance():
    """물 수지 불균형 (엣지별) — FLOW_BALANCE_CACHE 기반."""
    edge_imbalance: dict[str, dict] = {}
    flow_balance = _get_flow_balance_cache() if _get_flow_balance_cache else []
    if flow_balance:
        for fb in flow_balance:
            if fb.get("status") != "ok":
                continue
            us_sn = fb.get("upstream_sitename", "")
            us_ft = fb.get("upstream_facilitytype", "")
            for ds in fb.get("downstream_facilities", []):
                ds_sn = ds.get("sitename", "")
                ds_ft = ds.get("facilitytype", "")
                ek = f"{us_sn}__{us_ft}|{ds_sn}__{ds_ft}"
                edge_imbalance[ek] = {
                    "imbalance_pct": round(fb.get("imbalance_pct", 0), 1),
                    "grade": fb.get("grade", "정상"),
                }
    return edge_imbalance


# =========================================================================
# GET /flow-map/node-alarms
# =========================================================================

@router.get("/flow-map/node-alarms")
async def get_flow_map_node_alarms(sitename: str, facilitytype: str):
    """시설별 최근 알람 목록 (진행중 + 최근 24시간 해제)"""
    conn = None
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT a.alarm_start_time, a.alarm_end_time,
                   a.alarm_severity, a.alarm_status, a.alarm_category,
                   a.alarm_msg, a.tagsn, t.datainfo
            FROM tb_equipment_alarm_report a
            JOIN tb_tag_info t ON t.tagsn = a.tagsn
            WHERE t.sitename = %s AND t.facilitytype = %s
              AND (a.alarm_status = '진행중'
                   OR a.alarm_start_time >= now() - interval '24 hours')
            ORDER BY
              CASE WHEN a.alarm_status = '진행중' THEN 0 ELSE 1 END,
              a.alarm_start_time DESC
            LIMIT 20
        """, (sitename, facilitytype))
        rows = cur.fetchall()
        cur.close()
        alarms = []
        for r in rows:
            alarms.append({
                "start_time": r[0].isoformat() if r[0] else None,
                "end_time": r[1].isoformat() if r[1] else None,
                "severity": r[2],
                "status": r[3],
                "category": r[4],
                "message": r[5],
                "tagsn": r[6],
                "datainfo": r[7],
            })
        return {"status": "OK", "alarms": alarms}
    except Exception as e:
        logger.error(f"시설 알람 조회 실패: {e}")
        return {"status": "ERROR", "alarms": []}
    finally:
        if conn:
            conn.close()


# =========================================================================
# GET /equipments/auto-map
# =========================================================================

@router.get("/equipments/auto-map")
async def auto_map_equipment_tags(dry_run: bool = True):
    """설비<->태그 자동 매핑 실행/미리보기.

    dry_run=true(기본): 매핑 예상 결과만 반환 (DB 변경 없음)
    dry_run=false: 실제 INSERT 수행 (ON CONFLICT DO NOTHING)
    """
    conn = None
    try:
        conn = _get_conn()
        result = _auto_map_equipment_tags_fn(conn, dry_run=dry_run)
        return {"status": "OK", **result}
    except Exception as e:
        logger.error("설비 태그 자동 매핑 실패: %s", e)
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()
