"""
이상감지 스캔 모듈 — ai_server.py에서 분리

_compute_anomaly_scan_all 전체 파이프라인 + 관련 헬퍼:
- _detect_data_quality_issues: 데이터 품질 이상 감지
- _detect_equipment_failures: 설비 장애 감지
- _apply_worst_failure: 장애 우선순위 적용
- _diagnose_equipment_for_tags: 태그별 설비 장애 진단
- _compute_anomaly_scan_all: 이상감지 전체 스캔

init()으로 DB 커넥션 + 글로벌 상태 참조를 주입받아 사용.
"""

import json
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger("slm")


# =============================================================================
# 공통 유틸: stale 데이터 대응 시간창 조정
# =============================================================================

def adjust_sql_time_window_to_max_bucket(
    sql: str,
    max_bucket: Any,
    label: str = "",
    threshold_sec: int = 3600,
) -> str:
    """SQL 내 `bucket >= now() - interval '3 hours'` / `'1 hour'` 패턴을
    max_bucket 기준 명시 범위로 치환한다 (stale 데이터 대응).

    DB 데이터가 `threshold_sec`초 이상 오래된 경우에만 치환.
    365일 등 다른 window (baseline)는 건드리지 않음.
    테이블 별칭(`c.bucket`)도 `(\\w+\\.)?bucket` 패턴으로 지원.

    Args:
        sql         : 원본 SQL 템플릿
        max_bucket  : cagg_5min_raw_stats_ai.max(bucket) 값 (datetime)
        label       : 로그 태그 (예: "SCAN_ALL", "FACILITY_DETAIL")
        threshold_sec: 이 값보다 오래되면 치환 (기본 3600s = 1시간)

    Returns:
        치환된 SQL (변경 없으면 원본 반환)
    """
    if max_bucket is None:
        return sql
    mb_naive = (
        max_bucket.replace(tzinfo=None)
        if getattr(max_bucket, "tzinfo", None) else max_bucket
    )
    try:
        age_sec = (datetime.now() - mb_naive).total_seconds()
    except Exception:
        return sql
    if age_sec <= threshold_sec:
        return sql

    rt = (mb_naive + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    rf_3h = (mb_naive - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
    rf_1h = (mb_naive - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

    patched = re.sub(
        r"((?:\w+\.)?bucket)\s*>=\s*now\(\)\s*-\s*interval\s*'3\s*hours?'",
        lambda m: f"{m.group(1)} >= '{rf_3h}'::timestamp AND {m.group(1)} <= '{rt}'::timestamp",
        sql,
    )
    patched = re.sub(
        r"((?:\w+\.)?bucket)\s*>=\s*now\(\)\s*-\s*interval\s*'1\s*hours?'",
        lambda m: f"{m.group(1)} >= '{rf_1h}'::timestamp AND {m.group(1)} <= '{rt}'::timestamp",
        patched,
    )

    if patched != sql:
        prefix = f"{label}: " if label else ""
        logger.info(
            f"{prefix}데이터 {age_sec/3600:.1f}h 오래됨 → "
            f"max_bucket({mb_naive}) 기준 시간창 조정"
        )
    return patched


# ai_server.py에서 주입
_get_db_connection = None
_execute_sql = None           # execute_sql 함수 참조
_process_sql_result = None    # process_sql_result 함수 참조
_intent_definitions = None    # INTENT_DEFINITIONS 리스트 참조
_causal_index = None          # _CAUSAL_INDEX 참조
_query_recent_values = None   # 교차검증용 최근값 조회 함수
_site_profiler = None         # SiteProfiler 인스턴스
_get_flow_balance_cache = None  # callable → _FLOW_BALANCE_CACHE


def init(get_db_connection_fn, execute_sql_fn, process_sql_result_fn,
         intent_definitions, causal_index,
         query_recent_values_fn=None, site_profiler_ref=None,
         get_flow_balance_cache_fn=None):
    """ai_server.py에서 의존성을 주입받는다."""
    global _get_db_connection, _execute_sql, _process_sql_result
    global _intent_definitions, _causal_index
    global _query_recent_values, _site_profiler, _get_flow_balance_cache
    _get_db_connection = get_db_connection_fn
    _execute_sql = execute_sql_fn
    _process_sql_result = process_sql_result_fn
    _intent_definitions = intent_definitions
    _causal_index = causal_index
    _query_recent_values = query_recent_values_fn
    _site_profiler = site_profiler_ref
    _get_flow_balance_cache = get_flow_balance_cache_fn


def _detect_data_quality_issues(rows: list, columns: list) -> list[dict]:
    """ANOMALY_SCAN_ALL 결과에서 빠진 태그를 감지하여 데이터 품질 이상 목록 반환.

    빠진 원인 분류:
    - 센서무응답: 7일간 전체 val≈0 (DEAD sensor)
    - 데이터홀딩: 7일간 데이터 존재하나 active_cnt < 50 + 높은 flat 비율
    - 데이터없음: 7일간 데이터 자체가 없음
    """
    tagsn_idx = columns.index("tagsn") if "tagsn" in columns else None
    if tagsn_idx is None:
        return []

    result_tagsns = {r[tagsn_idx] for r in rows}

    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()

        # 1) 전체 Analog Input (NOT 적산, NOT 설정값) 태그
        cur.execute("""
            SELECT tagsn, sitename, facilitytype, datainfo
            FROM tb_tag_info
            WHERE tagtype = 'Analog Input'
              AND datainfo NOT LIKE '%%적산%%'
              AND datainfo NOT LIKE '%%설정%%'
        """)
        all_tags = {r[0]: {"tagsn": r[0], "sitename": r[1], "facilitytype": r[2], "datainfo": r[3]}
                    for r in cur.fetchall()}

        # 2) 차집합 = 결과에서 빠진 태그
        missing_tagsns = [t for t in all_tags if t not in result_tagsns]
        if not missing_tagsns:
            cur.close()
            return []

        # 3) 빠진 태그의 최근 7일 데이터 상태 (DEAD/데이터없음 판별)
        cur.execute("""
            SELECT tagsn,
                COUNT(*) AS total_5m,
                COUNT(*) FILTER (WHERE (min_val + max_val) / 2.0 > 0.001) AS nonzero_cnt,
                SUM(CASE WHEN min_val = max_val THEN 1 ELSE 0 END) AS flat_cnt,
                MAX(bucket) AS last_bucket
            FROM cagg_5min_raw_stats_ai
            WHERE tagsn = ANY(%s)
                AND bucket >= now() - interval '7 days'
            GROUP BY tagsn
        """, (missing_tagsns,))
        stats_7d = {r[0]: r for r in cur.fetchall()}

        # 4) 최근 24시간 데이터 상태 (홀딩 조기 감지)
        cur.execute("""
            SELECT tagsn,
                COUNT(*) AS total_24h,
                COUNT(*) FILTER (WHERE (min_val + max_val) / 2.0 > 0.001) AS nonzero_24h,
                SUM(CASE WHEN min_val = max_val THEN 1 ELSE 0 END) AS flat_24h
            FROM cagg_5min_raw_stats_ai
            WHERE tagsn = ANY(%s)
                AND bucket >= now() - interval '24 hours'
            GROUP BY tagsn
        """, (missing_tagsns,))
        stats_24h = {r[0]: r for r in cur.fetchall()}
        cur.close()

        issues = []
        for tagsn in missing_tagsns:
            info = all_tags[tagsn]
            s7 = stats_7d.get(tagsn)
            s24 = stats_24h.get(tagsn)

            if not s7:
                issue_type = "데이터없음"
                detail = "최근 7일간 데이터 없음"
            elif int(s7[2]) == 0:  # nonzero_cnt == 0 (7일간 전부 val≈0)
                issue_type = "센서무응답"
                detail = f"7일간 {s7[1]}건 전부 val~0"
            else:
                # 24시간 윈도우 우선 (홀딩 조기 감지)
                # s24 = (tagsn, total_24h, nonzero_24h, flat_24h) → 인덱스 주의
                flat_24h_pct = round(int(s24[3]) / int(s24[1]) * 100, 1) if s24 and int(s24[1]) > 0 else 0
                flat_7d_pct = round(int(s7[3]) / int(s7[1]) * 100, 1) if int(s7[1]) > 0 else 0
                if flat_24h_pct > 90:
                    issue_type = "데이터홀딩"
                    detail = f"24h flat {flat_24h_pct}% (7d flat {flat_7d_pct}%)"
                elif flat_7d_pct > 80:
                    issue_type = "데이터홀딩"
                    detail = f"7d flat {flat_7d_pct}%"
                else:
                    issue_type = "데이터부족"
                    detail = f"활성 {s7[2]}건 (50건 미달)"

            issues.append({
                "tagsn": tagsn,
                "sitename": info["sitename"],
                "facilitytype": info["facilitytype"],
                "datainfo": info["datainfo"],
                "issue_type": issue_type,
                "detail": detail,
            })

        return issues

    except Exception as e:
        logger.warning(f"데이터 품질 감지 실패: {e}")
        return []
    finally:
        if conn:
            try:
                conn.close()
            except Exception as e:
                logger.warning(f"데이터 품질 conn.close 실패: {e}")


def _detect_sensor_saturation(rows: list, columns: list) -> list[dict]:
    """스캔 결과에 '포함된' 태그의 센서 포화·신호 고착 감지 (데이터 품질군).

    _detect_data_quality_issues 는 결과에서 '빠진' 태그(DEAD/홀딩)만 다룬다.
    성상1 유출압력처럼 값이 계측 상한(풀스케일)에 붙어 있으면 통계는 멀쩡해
    보여 그대로 통과됨 (2026-07-23 이틀 포화 미검지 — docs/review-items.md).
    - 센서포화의심: 최근 6h 버킷 80%+ 가 min·max 모두 90일 관측 상한(±0.1%)
    - 신호고착의심: 최근 6h 90%+ flat(min=max)인데 직전 7일엔 변동 있던 신호
    """
    tagsn_idx = columns.index("tagsn") if "tagsn" in columns else None
    if tagsn_idx is None:
        return []
    scan_tagsns = list({r[tagsn_idx] for r in rows})
    if not scan_tagsns:
        return []
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            WITH ceil AS (
                SELECT tagsn, MAX(max_val) AS ceiling
                FROM cagg_5min_raw_stats_ai
                WHERE tagsn = ANY(%s) AND bucket >= now() - interval '90 days'
                GROUP BY tagsn
            ),
            recent AS (
                SELECT s.tagsn,
                       COUNT(*) AS r_cnt,
                       COUNT(*) FILTER (
                         WHERE s.min_val >= c.ceiling * 0.999
                           AND s.max_val >= c.ceiling * 0.999) AS pinned_cnt,
                       COUNT(*) FILTER (WHERE s.min_val = s.max_val) AS flat_cnt
                FROM cagg_5min_raw_stats_ai s
                JOIN ceil c ON c.tagsn = s.tagsn
                WHERE s.bucket >= now() - interval '6 hours'
                GROUP BY s.tagsn
            ),
            week AS (
                SELECT tagsn,
                       COUNT(*) FILTER (WHERE min_val <> max_val) AS moving_cnt
                FROM cagg_5min_raw_stats_ai
                WHERE tagsn = ANY(%s)
                  AND bucket BETWEEN now() - interval '7 days'
                                 AND now() - interval '6 hours'
                GROUP BY tagsn
            )
            SELECT r.tagsn, c.ceiling, r.r_cnt, r.pinned_cnt, r.flat_cnt,
                   COALESCE(w.moving_cnt, 0)
            FROM recent r
            JOIN ceil c ON c.tagsn = r.tagsn
            LEFT JOIN week w ON w.tagsn = r.tagsn
            WHERE c.ceiling > 0.001
        """, (scan_tagsns, scan_tagsns))
        stats = cur.fetchall()

        # 태그 메타 — 설정값 태그는 원래 상수라 제외
        cur.execute("""
            SELECT tagsn, sitename, facilitytype, datainfo
            FROM tb_tag_info
            WHERE tagsn = ANY(%s)
              AND datainfo NOT LIKE '%%설정%%'
              AND datainfo NOT LIKE '%%SET%%'
        """, (scan_tagsns,))
        meta = {r[0]: {"sitename": r[1], "facilitytype": r[2], "datainfo": r[3]}
                for r in cur.fetchall()}
        cur.close()

        issues: list[dict] = []
        for tagsn, ceiling, r_cnt, pinned, flat, moving in stats:
            info = meta.get(tagsn)
            if not info or not r_cnt:
                continue
            pinned_pct = pinned / r_cnt * 100
            flat_pct = flat / r_cnt * 100
            if pinned_pct >= 80:
                issues.append({
                    "tagsn": tagsn, **info,
                    "issue_type": "센서포화의심",
                    "detail": (
                        f"최근 6시간의 {pinned_pct:.0f}% 가 90일 관측 상한 "
                        f"{float(ceiling):g} 에 고정 — 계측기 풀스케일 고착 의심"
                    ),
                })
            elif flat_pct >= 90 and moving > 12:
                issues.append({
                    "tagsn": tagsn, **info,
                    "issue_type": "신호고착의심",
                    "detail": "최근 6시간 값 변화 없음 (직전 7일은 정상 변동) — 계측·전송 정체 의심",
                })
        return issues
    except Exception as e:
        logger.warning(f"센서 포화/고착 감지 실패: {e}")
        return []
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 설비 장애 역추적 (Equipment Failure Traceback)
# ---------------------------------------------------------------------------
_FAILURE_SEVERITY: dict[str, int] = {
    "equip_fault": 4,
    "power_fault": 3,
    "network_down": 2,
    "comm_error": 1,
}


def _detect_equipment_failures(
    rows: list, columns: list,
) -> tuple[list[dict], dict[str, str]]:
    """설비 장애 감지 + 영향 태그 역추적.

    3가지 신호 소스를 조합:
    A) tb_network_status: is_alive=false 설비
    B) DI 태그(COMM_ERROR/EQUIP_FAULT/POWER_FAULT): val=1, 최근 10분
    C) tb_equipment_tag_map: 역방향 매핑 (equipment → tags)

    Returns:
        (impacts_list, tag_to_failure_map, stuck_di_issues)
        - impacts_list: 설비별 장애 요약 [{equipment_id, failure_type, affected_tag_count, ...}]
        - tag_to_failure_map: {tagsn: failure_type} per-row 뱃지용
        - stuck_di_issues: 상시 ON 게이트로 제외된 DI (데이터 품질군 강등)
    """
    tagsn_idx = columns.index("tagsn") if "tagsn" in columns else None
    sn_idx = columns.index("sitename") if "sitename" in columns else None
    ft_idx = columns.index("facilitytype") if "facilitytype" in columns else None
    di_idx = columns.index("datainfo") if "datainfo" in columns else None
    if tagsn_idx is None:
        return [], {}

    result_tagsns = {r[tagsn_idx] for r in rows}
    # tagsn → datainfo 조회용
    tag_datainfo: dict[str, str] = {}
    if di_idx is not None:
        tag_datainfo = {r[tagsn_idx]: str(r[di_idx]) for r in rows}

    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()

        # --- A. 네트워크 장애 설비 ---
        # 전체 장비가 is_alive=false이면 망 분리 환경(로컬 개발) → SNMP 폴링 결과 신뢰 불가, 스킵
        failed_equips: dict[str, dict] = {}
        try:
            cur.execute("""
                WITH lt AS (SELECT MAX(check_time) AS ct FROM tb_network_status)
                SELECT ns.is_alive, ns.equipment_id, e.equipmenttype, e.sitename, e.facilitytype,
                       ns.error_message
                FROM tb_network_status ns
                JOIN lt ON ns.check_time = lt.ct
                JOIN tb_equipment_info e ON ns.equipment_id = e.equipment_id
            """)
            ns_rows = cur.fetchall()
            alive_count = sum(1 for r in ns_rows if r[0] is True)
            if alive_count == 0 and ns_rows:
                # 전체 Timeout → 로컬 서버가 현장 망에 접근 불가 상태 → network_down 판단 생략
                logger.info(f"tb_network_status 전체 is_alive=false ({len(ns_rows)}개) → 망 분리 환경으로 판단, network_down 스킵")
            else:
                for is_alive, eid, etype, sn, ft, emsg in ns_rows:
                    if not is_alive:
                        failed_equips[eid] = {
                            "equipmenttype": etype or "",
                            "sitename": sn or "",
                            "facilitytype": ft or "",
                            "failure_type": "network_down",
                            "failure_detail": emsg or "네트워크 응답 없음",
                        }
        except Exception as e:
            logger.debug(f"네트워크 장애 조회 실패(무시): {e}")

        # --- B. DI 장애 태그 (COMM_ERROR / EQUIP_FAULT / POWER_FAULT) ---
        site_faults: dict[tuple[str, str], str] = {}  # (sitename, facilitytype) → worst failure_type
        stuck_di_issues: list[dict] = []  # 상시 ON 게이트 제외분 → 데이터 품질군
        try:
            # B-1: 대상 DI 태그 조회
            cur.execute("""
                SELECT gm.tagsn, dg.group_code, ti.sitename, ti.facilitytype, ti.datainfo
                FROM tb_tag_group_map gm
                JOIN tb_tag_data_group dg ON gm.group_id = dg.group_id
                JOIN tb_tag_info ti ON gm.tagsn = ti.tagsn
                WHERE dg.group_code IN ('COMM_ERROR', 'EQUIP_FAULT', 'POWER_FAULT')
            """)
            di_tags = cur.fetchall()

            if di_tags:
                di_tagsn_list = [r[0] for r in di_tags]
                di_meta = {r[0]: (r[1], r[2], r[3], r[4]) for r in di_tags}  # tagsn → (group_code, sitename, facilitytype, datainfo)

                # B-2: 최근 10분 val=1 (장애 활성)
                cur.execute("""
                    SELECT DISTINCT tagsn
                    FROM tb_tag_raw_data
                    WHERE tagsn = ANY(%s)
                      AND logtime >= now() - interval '10 minutes'
                      AND val = 1
                """, (di_tagsn_list,))
                active_faults = {r[0] for r in cur.fetchall()}

                # B-2.5: 상시 ON 게이트 — 최근 7일 on 비율 95%+ DI 는 접점 반전/
                # 고착 의심이라 "확정 사고" 판정에서 제외하고 데이터 품질로 강등.
                # (죽동 '정전 발생' DI 30일 상시 1 → power_fault 상시 오경보,
                #  동일 패턴 10개+ 사이트 — 2026-07-23, docs/review-items.md)
                if active_faults:
                    cur.execute("""
                        SELECT tagsn,
                               AVG(CASE WHEN val = 1 THEN 1.0 ELSE 0.0 END) AS on_ratio
                        FROM tb_tag_raw_data
                        WHERE tagsn = ANY(%s)
                          AND logtime >= now() - interval '7 days'
                        GROUP BY tagsn
                    """, (list(active_faults),))
                    for tsn, on_ratio in cur.fetchall():
                        if on_ratio is not None and float(on_ratio) >= 0.95:
                            active_faults.discard(tsn)
                            gc, sn, ft, di = di_meta.get(tsn, ("", "", "", ""))
                            stuck_di_issues.append({
                                "tagsn": tsn,
                                "sitename": sn, "facilitytype": ft, "datainfo": di,
                                "issue_type": "DI상시ON의심",
                                "detail": (
                                    f"최근 7일 on 비율 {float(on_ratio)*100:.0f}% — "
                                    f"접점 반전/고착 의심으로 설비 장애 판정 제외 ({gc})"
                                ),
                            })

                _gc_to_ft = {
                    "COMM_ERROR": "comm_error",
                    "EQUIP_FAULT": "equip_fault",
                    "POWER_FAULT": "power_fault",
                }
                for tsn in active_faults:
                    gc, sn, ft, _di = di_meta[tsn]
                    ftype = _gc_to_ft.get(gc, "comm_error")
                    key = (sn, ft)
                    existing = site_faults.get(key)
                    if not existing or _FAILURE_SEVERITY.get(ftype, 0) > _FAILURE_SEVERITY.get(existing, 0):
                        site_faults[key] = ftype
        except Exception as e:
            logger.debug(f"DI 장애 태그 조회 실패(무시): {e}")

        # --- C. 설비↔태그 역방향 맵 ---
        equip_to_tags: dict[str, set[str]] = {}
        equip_info_map: dict[str, dict] = {}
        try:
            cur.execute("""
                SELECT etm.equipment_id, etm.tagsn, e.equipmenttype, e.sitename, e.facilitytype
                FROM tb_equipment_tag_map etm
                JOIN tb_equipment_info e ON etm.equipment_id = e.equipment_id
            """)
            for eid, tsn, etype, sn, ft in cur.fetchall():
                equip_to_tags.setdefault(eid, set()).add(tsn)
                if eid not in equip_info_map:
                    equip_info_map[eid] = {
                        "equipmenttype": etype or "",
                        "sitename": sn or "",
                        "facilitytype": ft or "",
                    }
        except Exception as e:
            logger.debug(f"설비 태그 맵 조회 실패(무시): {e}")

        cur.close()

        # --- 조인: 장애 설비 → 영향 태그 → 스캔 결과 교차 ---
        impacts: list[dict] = []
        tag_failure_map: dict[str, str] = {}

        # A 소스: 네트워크 장애 설비
        for eid, info in failed_equips.items():
            affected = equip_to_tags.get(eid, set())
            in_scan = affected & result_tagsns
            impacts.append({
                "equipment_id": eid,
                "equipmenttype": info["equipmenttype"],
                "sitename": info["sitename"],
                "facilitytype": info["facilitytype"],
                "failure_type": info["failure_type"],
                "failure_detail": info["failure_detail"],
                "affected_tag_count": len(affected),
                "anomalous_tag_count": len(in_scan),
                "affected_tags": [
                    {"tagsn": t, "datainfo": tag_datainfo.get(t, "")}
                    for t in sorted(in_scan)[:5]
                ],
            })
            for t in affected:
                _apply_worst_failure(tag_failure_map, t, info["failure_type"])

        # B 소스: DI 장애 → 사이트+시설 레벨 전체 태그
        for (sn, ft), ftype in site_faults.items():
            # 해당 사이트+시설의 모든 태그 찾기
            site_tags: set[str] = set()
            if sn_idx is not None and ft_idx is not None:
                for r in rows:
                    if r[sn_idx] == sn and r[ft_idx] == ft:
                        site_tags.add(r[tagsn_idx])
            if not site_tags:
                continue

            impacts.append({
                "equipment_id": f"{sn}_{ft}",
                "equipmenttype": "DI장애",
                "sitename": sn,
                "facilitytype": ft,
                "failure_type": ftype,
                "failure_detail": {
                    "comm_error": "통신이상 DI 활성",
                    "equip_fault": "설비고장 DI 활성",
                    "power_fault": "전원이상 DI 활성",
                }.get(ftype, "DI 활성"),
                "affected_tag_count": len(site_tags),
                "anomalous_tag_count": len(site_tags),
                "affected_tags": [
                    {"tagsn": t, "datainfo": tag_datainfo.get(t, "")}
                    for t in sorted(site_tags)[:5]
                ],
            })
            for t in site_tags:
                _apply_worst_failure(tag_failure_map, t, ftype)

        # 영향 태그 수 내림차순 정렬
        impacts.sort(key=lambda x: x["affected_tag_count"], reverse=True)

        return impacts, tag_failure_map, stuck_di_issues

    except Exception as e:
        logger.warning(f"설비 장애 감지 실패: {e}")
        return [], {}, []
    finally:
        if conn:
            try:
                conn.close()
            except Exception as e:
                logger.warning(f"설비 장애 역추적 conn.close 실패: {e}")


def _apply_worst_failure(
    tag_map: dict[str, str], tagsn: str, failure_type: str,
) -> None:
    """tag_failure_map에 worst severity 기준으로 장애 유형 적용."""
    existing = tag_map.get(tagsn)
    if not existing or _FAILURE_SEVERITY.get(failure_type, 0) > _FAILURE_SEVERITY.get(existing, 0):
        tag_map[tagsn] = failure_type


_FAILURE_LABEL = {
    "network_down": "네트워크 단절",
    "comm_error": "통신이상",
    "equip_fault": "설비고장",
    "power_fault": "전원이상",
}


def _diagnose_equipment_for_tags(
    anomaly_tagsns: list[str], sitename: str, facilitytype: str,
) -> list[dict] | None:
    """이상 태그 → tb_equipment_tag_map 역추적 → 연결 설비 건강 진단.

    Phase 3: 이상감지 결과의 태그에서 연결된 설비를 역추적하고,
    각 설비의 건강 상태(네트워크/DI/장애 신호)를 종합 진단합니다.

    Returns:
        설비별 진단 결과 리스트, 없으면 None
    """
    if not anomaly_tagsns:
        return None
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()

        # 1) 이상 태그 → 연결 설비 조회
        cur.execute("""
            SELECT DISTINCT m.equipment_id, m.tagsn,
                   e.equipmenttype, e.sitename, e.facilitytype
            FROM tb_equipment_tag_map m
            JOIN tb_equipment_info e ON e.equipment_id = m.equipment_id
            WHERE m.tagsn = ANY(%s)
        """, (anomaly_tagsns,))
        rows = cur.fetchall()
        if not rows:
            return None

        # 설비별 그룹핑
        equip_map: dict[str, dict] = {}
        for eid, tagsn, etype, sn, ft in rows:
            if eid not in equip_map:
                equip_map[eid] = {
                    "equipment_id": eid,
                    "equipmenttype": etype,
                    "sitename": sn,
                    "facilitytype": ft,
                    "linked_anomaly_tags": [],
                    "total_tag_count": 0,
                    "failures": [],
                    "health_score": 100,
                }
            equip_map[eid]["linked_anomaly_tags"].append(tagsn)

        # 2) 각 설비의 전체 태그 수 조회
        eids = list(equip_map.keys())
        cur.execute("""
            SELECT equipment_id, COUNT(*) FROM tb_equipment_tag_map
            WHERE equipment_id = ANY(%s) GROUP BY equipment_id
        """, (eids,))
        for eid, cnt in cur.fetchall():
            if eid in equip_map:
                equip_map[eid]["total_tag_count"] = cnt

        # 3) 네트워크 상태 확인 (tb_network_status에 있는 장비만)
        cur.execute("""
            SELECT DISTINCT ON (equipment_id) equipment_id, is_alive, check_time
            FROM tb_network_status
            WHERE equipment_id = ANY(%s)
            ORDER BY equipment_id, check_time DESC
        """, (eids,))
        for eid, alive, check_time in cur.fetchall():
            if eid in equip_map and not alive:
                equip_map[eid]["failures"].append("network_down")
                equip_map[eid]["health_score"] -= 30

        # 4) DI 장애 태그 확인
        cur.execute("""
            SELECT DISTINCT e.equipment_id,
                   CASE WHEN ti.datadesc ILIKE '%%통신이상%%' THEN 'comm_error'
                        WHEN ti.datadesc ILIKE '%%설비고장%%' OR ti.datadesc ILIKE '%%펌프고장%%' THEN 'equip_fault'
                        WHEN ti.datadesc ILIKE '%%전원%%' OR ti.datadesc ILIKE '%%UPS%%' OR ti.datadesc ILIKE '%%정전%%' THEN 'power_fault'
                   END AS ftype
            FROM tb_equipment_tag_map m
            JOIN tb_tag_info ti ON ti.tagsn = m.tagsn AND ti.tagtype = 'Digital Input'
            JOIN tb_equipment_info e ON e.equipment_id = m.equipment_id
            WHERE m.equipment_id = ANY(%s)
              AND (ti.datadesc ILIKE '%%통신이상%%' OR ti.datadesc ILIKE '%%설비고장%%'
                   OR ti.datadesc ILIKE '%%펌프고장%%' OR ti.datadesc ILIKE '%%전원%%'
                   OR ti.datadesc ILIKE '%%UPS%%' OR ti.datadesc ILIKE '%%정전%%')
        """, (eids,))

        # DI 태그별 최신값 확인 (val=1이면 장애 활성)
        di_tags: dict[str, list[tuple[str, str]]] = {}  # eid → [(tagsn, ftype)]
        for eid, ftype in cur.fetchall():
            if ftype:
                di_tags.setdefault(eid, []).append((eid, ftype))

        for eid, ftypes in di_tags.items():
            if eid in equip_map:
                for _, ftype in ftypes:
                    if ftype not in equip_map[eid]["failures"]:
                        equip_map[eid]["failures"].append(ftype)
                        penalty = {
                            "equip_fault": 40, "power_fault": 30,
                            "network_down": 25, "comm_error": 15,
                        }.get(ftype, 10)
                        equip_map[eid]["health_score"] -= penalty

        # 5) 건강 점수 정리 (0~100 클램프)
        results = []
        for eq in equip_map.values():
            eq["health_score"] = max(0, min(100, eq["health_score"]))
            eq["anomaly_tag_count"] = len(eq["linked_anomaly_tags"])
            eq["failure_labels"] = [_FAILURE_LABEL.get(f, f) for f in eq["failures"]]
            # 건강 등급
            s = eq["health_score"]
            eq["health_grade"] = "정상" if s >= 80 else "주의" if s >= 50 else "위험"
            results.append(eq)

        # severity 높은 순 정렬
        results.sort(key=lambda x: x["health_score"])
        return results if results else None

    except Exception as e:
        logger.warning(f"설비 건강 진단 실패: {e}")
        return None
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def _compute_anomaly_scan_all() -> Optional[dict]:
    """ANOMALY_SCAN_ALL 전체 파이프라인을 동기 실행하여 캐시용 결과 반환.

    SQL 실행 → _process_sql_result(IForest+grade/group 포함) → 교차 검증 → 최종 결과.
    캐시된 결과는 핸들러에서 answer_template 렌더링에 직접 사용된다.
    """
    from anomaly_detector import cross_facility_check_all, enrich_rows_with_cross_verdict

    # intent_def 찾기
    intent_def = None
    for idef in _intent_definitions:
        if idef.get("intent") == "ANOMALY_SCAN_ALL":
            intent_def = idef
            break
    if not intent_def:
        return None

    sql_raw = intent_def.get("sql", "")
    if not sql_raw:
        return None
    sql_combined = "\n".join(sql_raw) if isinstance(sql_raw, list) else sql_raw

    # 1단계: SQL 실행 (365일 stats + 3h latest + comm_error)
    # 플레이스홀더 치환 (캐시는 전체 조회이므로 필터 없음)
    cache_params = {"anomaly_facility_filter": "", "anomaly_scope": "", "alarm_filter_clause": ""}

    # 데이터 신선도 확인: SCAN_ALL·FACILITY_DETAIL 공통 헬퍼 사용
    try:
        _max_rows, _ = _execute_sql("SELECT max(bucket) FROM cagg_5min_raw_stats_ai", {})
        if _max_rows and _max_rows[0][0]:
            sql_combined = adjust_sql_time_window_to_max_bucket(
                sql_combined, _max_rows[0][0], label="SCAN_ALL",
            )
    except Exception as e:
        logger.warning(f"SCAN_ALL: max(bucket) 확인 실패: {e}")

    try:
        rows, columns = _execute_sql(sql_combined, cache_params)
    except Exception as e:
        logger.error(f"SCAN_ALL 캐시 SQL 실패: {e}")
        return None

    if not rows:
        return None

    rows = [list(r) for r in rows]

    # 2단계: process_sql_result (IForest + per-row grade/group 포함)
    try:
        processed_data = _process_sql_result(rows, columns, intent_def, {})
    except Exception as e:
        logger.error(f"SCAN_ALL 캐시 후처리 실패: {e}")
        return None

    # 3단계: 교차 검증
    if _causal_index:
        try:
            cross_mismatches = cross_facility_check_all(
                _query_recent_values, _causal_index, lookback_minutes=180,
            )
            if cross_mismatches:
                processed_data["cross_facility_mismatches"] = cross_mismatches
                processed_data["cross_facility_mismatch_count"] = len(cross_mismatches)
            logger.info(f"SCAN_ALL 캐시 교차검증: {len(cross_mismatches)}건")
        except Exception as e:
            logger.warning(f"SCAN_ALL 캐시 교차검증 실패: {e}")

    # 4단계: 교차검증 결과를 per-row cross_status/verdict로 병합
    _profiles = _site_profiler.profiles if _site_profiler and _site_profiler.profiles else None
    _cross_list = processed_data.get("cross_facility_mismatches")
    enrich_rows_with_cross_verdict(rows, columns, _cross_list, site_profiles=_profiles)

    # verdict 기반 교차이상 카운트
    _vd_idx = columns.index("verdict") if "verdict" in columns else None
    if _vd_idx is not None:
        processed_data["cross_anomaly_count"] = sum(
            1 for r in rows if r[_vd_idx] in ("교차이상", "교차주의", "복합이상")
        )

    # 5단계: 데이터 품질 이상 감지
    #   ① 결과에서 빠진 태그 (DEAD/홀딩) ② 결과에 포함됐지만 포화/고착 의심
    dq_issues = _detect_data_quality_issues(rows, columns)
    dq_issues = (dq_issues or []) + _detect_sensor_saturation(rows, columns)
    if dq_issues:
        processed_data["data_quality_issues"] = dq_issues
        logger.info(f"SCAN_ALL 데이터 품질 이상: {len(dq_issues)}건")

    # 6단계: 설비 장애 역추적 (network_down + DI fault → 영향 태그)
    try:
        equip_impacts, tag_failure_map, stuck_di = _detect_equipment_failures(rows, columns)
        if stuck_di:
            # 상시 ON 게이트 제외분은 데이터 품질군으로 노출 (오경보 → 점검 유도)
            processed_data["data_quality_issues"] = (
                processed_data.get("data_quality_issues") or []
            ) + stuck_di
            logger.info(f"SCAN_ALL DI 상시 ON 게이트: {len(stuck_di)}건 제외")
        if equip_impacts:
            processed_data["equipment_failure_impacts"] = equip_impacts
            processed_data["equipment_failure_count"] = len(equip_impacts)
            logger.info(f"SCAN_ALL 설비 장애: {len(equip_impacts)}건")

        # per-row equip_failure 컬럼 추가 (rows가 tuple일 수 있으므로 재구성)
        columns.append("equip_failure")
        tsn_idx = columns.index("tagsn")
        rows[:] = [
            tuple(list(r) + [tag_failure_map.get(r[tsn_idx], "")])
            for r in rows
        ]
    except Exception as e:
        logger.warning(f"SCAN_ALL 설비 장애 감지 실패: {e}")

    # 7단계: 물 수지 요약 (캐시 참조)
    try:
        _fb_cache = _get_flow_balance_cache() if _get_flow_balance_cache else None
        if _fb_cache:
            imbalance_edges = [e for e in _fb_cache if e["grade"] != "정상" and e["status"] == "ok"]
            processed_data["flow_balance_summary"] = {
                "total_edges": len(_fb_cache),
                "imbalance_count": len(imbalance_edges),
                "worst_edges": sorted(imbalance_edges, key=lambda e: -abs(e["imbalance_pct"]))[:5],
            }
            if imbalance_edges:
                logger.info(f"SCAN_ALL 물 수지: {len(imbalance_edges)}/{len(_fb_cache)} 불균형")
    except Exception as e:
        logger.warning(f"SCAN_ALL 물 수지 요약 실패: {e}")

    return {
        "rows": rows,
        "columns": list(columns),
        "processed_data": processed_data,
        "answer_template": intent_def.get("answer_template", {}),
    }

