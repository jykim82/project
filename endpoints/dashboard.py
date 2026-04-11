"""
대시보드 API 엔드포인트 모듈

- GET /dashboard/overview   — 종합 현황판 (캐시 데이터 집계)
- GET /monitoring/dashboard — 대시보드 요약 (시설현황, 알람, 태그, 배수지 수위, 알람추세)

ai_server.py에서 분리된 모듈 — init()으로 의존성을 주입받아 사용.
"""

import asyncio
import logging
from contextlib import contextmanager

import psycopg2
from fastapi import APIRouter

logger = logging.getLogger("slm")

router = APIRouter()

# ai_server.py에서 주입
_get_db_connection = None
_get_anomaly_scan_cache = None      # callable → (cache_dict, cache_time)
_get_flow_balance_cache = None      # callable → cache_list


def init(get_db_connection_fn, get_anomaly_scan_cache_fn, get_flow_balance_cache_fn):
    """ai_server.py에서 의존성을 주입받는다."""
    global _get_db_connection, _get_anomaly_scan_cache, _get_flow_balance_cache
    _get_db_connection = get_db_connection_fn
    _get_anomaly_scan_cache = get_anomaly_scan_cache_fn
    _get_flow_balance_cache = get_flow_balance_cache_fn


@contextmanager
def _db_conn():
    """DB 커넥션 컨텍스트 매니저."""
    conn = _get_db_connection()
    try:
        yield conn
    except Exception as e:
        logger.error(f"db_conn 컨텍스트 내 에러, rollback 수행: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


# =============================================================================
# GET /dashboard/overview — 종합 현황판
# =============================================================================

@router.get("/dashboard/overview")
async def dashboard_overview():
    """종합 현황판 — 캐시 데이터 집계 (이상감지+교차+물수지+설비장애+데이터품질+경보)."""
    result: dict = {}

    scan_cache, scan_cache_time = _get_anomaly_scan_cache()
    flow_balance_cache = _get_flow_balance_cache()

    # 1) 이상감지 캐시에서 KPI + TOP 시설 추출
    if scan_cache:
        c = scan_cache
        rows = c.get("rows", [])
        columns = c.get("columns", [])
        pd = c.get("processed_data", {})

        col_idx = {col: i for i, col in enumerate(columns)}
        vd_i = col_idx.get("verdict")
        sn_i = col_idx.get("sitename")
        ft_i = col_idx.get("facilitytype")
        di_i = col_idx.get("datainfo")
        zs_i = col_idx.get("z_score")
        sg_i = col_idx.get("site_group")
        ag_i = col_idx.get("alert_grade")
        ef_i = col_idx.get("equip_failure")
        rh_i = col_idx.get("recent_holding")

        # KPI 카운트
        verdicts = {"복합이상": 0, "이상": 0, "교차이상": 0, "주의": 0, "정상": 0}
        for r in rows:
            v = r[vd_i] if vd_i is not None else "정상"
            if v in verdicts:
                verdicts[v] += 1

        # 시설유형별 분포
        ft_dist: dict[str, int] = {}
        for r in rows:
            ft = r[ft_i] if ft_i is not None else ""
            ft_dist[ft] = ft_dist.get(ft, 0) + 1

        # TOP 이상 시설 (verdict 우선순위 정렬)
        _vd_order = {"복합이상": 0, "이상": 1, "교차이상": 2, "주의": 3, "정상": 4}
        anomaly_rows = []
        for r in rows:
            vd = r[vd_i] if vd_i is not None else "정상"
            if vd == "정상":
                continue
            anomaly_rows.append({
                "sitename": r[sn_i] if sn_i is not None else "",
                "facilitytype": r[ft_i] if ft_i is not None else "",
                "datainfo": r[di_i] if di_i is not None else "",
                "z_score": round(float(r[zs_i]), 2) if zs_i is not None and r[zs_i] is not None else 0,
                "verdict": vd,
                "site_group": r[sg_i] if sg_i is not None else "",
                "alert_grade": r[ag_i] if ag_i is not None else "",
                "equip_failure": r[ef_i] if ef_i is not None else "",
                "recent_holding": r[rh_i] if rh_i is not None else "",
            })
        anomaly_rows.sort(key=lambda x: (_vd_order.get(x["verdict"], 9), -abs(x["z_score"])))

        result["anomaly"] = {
            "total": len(rows),
            "verdicts": verdicts,
            "ft_distribution": ft_dist,
            "top_facilities": anomaly_rows[:15],
            "cross_anomaly_count": pd.get("cross_anomaly_count", 0),
        }

        # 데이터 품질
        result["data_quality"] = pd.get("data_quality_issues", [])

        # 설비 장애
        result["equipment_failures"] = pd.get("equipment_failure_impacts", [])
        result["equipment_failure_count"] = pd.get("equipment_failure_count", 0)

        # 물수지 요약
        if flow_balance_cache:
            _imbalance_edges = [
                e for e in flow_balance_cache
                if e.get("grade") != "정상" and e.get("status") == "ok"
            ]
            result["flow_balance"] = {
                "total_edges": len(flow_balance_cache),
                "imbalance_count": len(_imbalance_edges),
                "worst_edges": sorted(_imbalance_edges, key=lambda e: -abs(e.get("imbalance_pct", 0)))[:5],
            }
        else:
            result["flow_balance"] = pd.get("flow_balance_summary")

        # 캐시 시간
        if scan_cache_time:
            result["cache_time"] = scan_cache_time.isoformat()
    else:
        result["anomaly"] = None

    # 2) 최근 경보
    def _fetch_recent_alarms() -> dict:
        alarms_result: dict = {}
        try:
            with _db_conn() as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT alarm_start_time, tagsn, alarm_status, alarm_severity,
                           alarm_category, alarm_msg, sitename, facilitytype
                    FROM tb_equipment_alarm_report
                    WHERE alarm_start_time > NOW() - INTERVAL '24 hours'
                    ORDER BY alarm_start_time DESC
                    LIMIT 20
                """)
                alarm_cols = [d[0] for d in cur.description]
                alarm_rows = cur.fetchall()
                alarms_result["recent_alarms"] = [dict(zip(alarm_cols, r)) for r in alarm_rows]
                ongoing = [r for r in alarms_result["recent_alarms"] if r.get("alarm_status") == "진행중"]
                alarms_result["ongoing_alarm_count"] = len(ongoing)
                alarms_result["ongoing_alarm_severity"] = {
                    "경고": sum(1 for a in ongoing if a.get("alarm_severity") == "경고"),
                    "주의": sum(1 for a in ongoing if a.get("alarm_severity") == "주의"),
                }
                cur.close()
        except Exception as e:
            logger.warning(f"dashboard/overview 경보 조회 실패: {e}")
            alarms_result["recent_alarms"] = []
            alarms_result["ongoing_alarm_count"] = 0
        return alarms_result

    alarm_data = await asyncio.to_thread(_fetch_recent_alarms)
    result.update(alarm_data)

    return result


# =============================================================================
# GET /monitoring/dashboard — 대시보드 요약
# =============================================================================

@router.get("/monitoring/dashboard")
async def get_dashboard_summary():
    """대시보드 요약 정보 (시설현황, 알람, 태그, 배수지 수위, 알람추세)"""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()

        # 1) 시설유형별 현장 수
        cur.execute("""
            SELECT facilitytype, COUNT(DISTINCT sitename) as cnt
            FROM tb_trend_catalog
            GROUP BY facilitytype
        """)
        facility_counts = {}
        total_sites = 0
        for row in cur.fetchall():
            ft, cnt = row[0], row[1]
            facility_counts[ft] = cnt
            total_sites += cnt
        reservoir_cnt = facility_counts.get("배수지", 0)
        booster_cnt = facility_counts.get("가압장", 0)
        block_cnt = facility_counts.get("소블록", 0) + facility_counts.get("소소블록", 0)

        # 2) 진행중 알람
        cur.execute("""
            SELECT alarm_severity, COUNT(*) as cnt
            FROM tb_equipment_alarm_report
            WHERE alarm_status = '진행중'
            GROUP BY alarm_severity
        """)
        alarm_counts = {}
        total_alarms = 0
        for row in cur.fetchall():
            sev = row[0] or "기타"
            cnt = row[1]
            alarm_counts[sev] = cnt
            total_alarms += cnt
        alarm_desc_parts = [f"{k}: {v}" for k, v in sorted(alarm_counts.items(), key=lambda x: -x[1])]

        # 3) 센서 태그 수
        cur.execute("SELECT COUNT(*) FROM tb_tag_info")
        tag_total = cur.fetchone()[0]

        # 4) 관리 장비 수
        cur.execute("SELECT COUNT(DISTINCT equipment_id) FROM tb_network_status")
        equip_cnt = cur.fetchone()[0]

        # 요약 카드 구성
        summary_cards = [
            {
                "title": "관리 현장",
                "value": str(total_sites),
                "description": f"배수지 {reservoir_cnt} / 가압장 {booster_cnt} / 블록 {block_cnt}",
            },
            {
                "title": "진행중 알람",
                "value": str(total_alarms),
                "description": " / ".join(alarm_desc_parts) if alarm_desc_parts else "없음",
            },
            {
                "title": "센서 태그",
                "value": f"{tag_total:,}",
                "description": f"관리 장비 {equip_cnt}대",
            },
            {
                "title": "시스템 상태",
                "value": "정상",
                "description": "AI 서버 + DB 연결 정상",
            },
        ]

        # 5) 배수지 수위 현황
        cur.execute("""
            WITH level_tags AS (
                SELECT tagsn, sitename, datadesc
                FROM tb_tag_info
                WHERE facilitytype = '배수지'
                  AND tagtype = 'Analog Input'
                  AND datainfo LIKE '%%수위%%'
                  AND datadesc NOT LIKE '%%설정%%'
                  AND datadesc NOT LIKE '%%HH%%'
                  AND datadesc NOT LIKE '%%LL%%'
                  AND datadesc NOT LIKE '%%H설정%%'
                  AND datadesc NOT LIKE '%%염소%%'
            ),
            latest AS (
                SELECT DISTINCT ON (tagsn) tagsn, val, logtime
                FROM tb_tag_raw_data
                WHERE tagsn IN (SELECT tagsn FROM level_tags)
                  AND logtime >= NOW() - INTERVAL '1 day'
                ORDER BY tagsn, logtime DESC
            ),
            site_avg AS (
                SELECT lt.sitename,
                       ROUND(AVG(l.val)::numeric, 2) as avg_level,
                       COUNT(*) as tag_cnt
                FROM level_tags lt
                JOIN latest l ON lt.tagsn = l.tagsn
                WHERE l.val IS NOT NULL AND l.val > 0
                GROUP BY lt.sitename
            )
            SELECT sitename, avg_level, tag_cnt FROM site_avg ORDER BY sitename
        """)
        reservoir_summaries = []
        for row in cur.fetchall():
            reservoir_summaries.append({
                "name": row[0],
                "currentLevel": float(row[1]),
                "maxCapacity": 5.0,
            })

        # 6) 7일 알람 추세
        cur.execute("""
            SELECT alarm_start_time::date as d, COALESCE(alarm_severity, '기타') as sev, COUNT(*) as cnt
            FROM tb_equipment_alarm_report
            WHERE alarm_start_time >= NOW() - INTERVAL '7 days'
            GROUP BY d, sev
            ORDER BY d
        """)
        alarm_trend = []
        for row in cur.fetchall():
            alarm_trend.append({
                "date": row[0].strftime("%m-%d"),
                "severity": row[1],
                "count": row[2],
            })

        # 7) 최근 알람 (24시간, 최대 20건)
        cur.execute("""
            SELECT r.alarm_start_time, r.tagsn, r.alarm_severity, r.alarm_status,
                   t.sitename, t.datadesc
            FROM tb_equipment_alarm_report r
            LEFT JOIN tb_tag_info t ON r.tagsn = t.tagsn
            WHERE r.alarm_start_time >= NOW() - INTERVAL '24 hours'
            ORDER BY r.alarm_start_time DESC
            LIMIT 20
        """)
        recent_alarms = []
        for i, row in enumerate(cur.fetchall()):
            alarm_time = row[0]
            tagsn = row[1]
            severity = row[2] or "기타"
            status = row[3]
            sitename = row[4] or ""
            datadesc = row[5] or tagsn
            recent_alarms.append({
                "id": i + 1,
                "time": alarm_time.strftime("%Y-%m-%d %H:%M"),
                "facility": sitename,
                "level": severity,
                "message": f"{datadesc} ({status})",
            })

        return {
            "summaryCards": summary_cards,
            "reservoirSummaries": reservoir_summaries,
            "recentAlarms": recent_alarms,
            "alarmTrend": alarm_trend,
        }

    except Exception as e:
        logger.error(f"대시보드 요약 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()
