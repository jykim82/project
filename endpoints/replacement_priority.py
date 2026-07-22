"""교체 우선순위 Top N — 설비 건강성 개요 최상단 카드용 3신호 융합.

"어떤 장소의 어떤 설비를 교체해야 하는가"를 한눈에 — 흩어져 있던 교체
신호 3종을 설비 단위 점수로 융합해 우선순위 랭킹으로 제공:

  ① 내용연수 (tb_equipment_lifespan): overdue +3.0 / approaching +1.5
  ② MTBF (v_equipment_mtbf, fault_cnt>=2): <30일 +3.0 / <90일 +1.5
  ③ 재발 지속 (alarm_fault_correlation.replacement_candidate): +3.0

레벨: score>=5 '매우 높음' / >=3 '높음' / 그 외 '보통'.
원칙: 리포트 전용 (자동 조치·상태 변경 없음). 신호별 임계는 기존
각 엔드포인트의 기본값을 그대로 사용 — 판정 로직 이원화 방지.

GET /monitoring/equipment-health/replacement-priority?limit=5&days=90
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/monitoring/equipment-health", tags=["equipment-health"])

_get_db_connection = None

# 신호 가중치 — 사양 docs/equipment-health-priority-spec.md
_SCORE_LIFESPAN_OVERDUE = 3.0
_SCORE_LIFESPAN_APPROACHING = 1.5
_SCORE_MTBF_CRITICAL = 3.0   # < 30일
_SCORE_MTBF_WARNING = 1.5    # < 90일
_SCORE_RECURRENCE = 3.0
# 실알람 추세 신호 (2026-07-22 v2 — 사용자 검토: 수기 고장보고 카운트만으로는
# 실제 조치 방향과 어긋남. 예: 탁도계 통신이상 1.5만건이 미반영)
_SCORE_ALARM_SURGE = 3.0   # 기간 내 >=1000건 + 최근 7일에도 발생 (폭주 지속)
_SCORE_ALARM_HIGH = 1.5    # 기간 내 >=100건
_ALARM_HIGH_MIN = 100
_ALARM_SURGE_MIN = 1000

_LEVEL_VERY_HIGH = 5.0
_LEVEL_HIGH = 3.0


def init(get_db_connection_fn):
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


def _level(score: float) -> str:
    if score >= _LEVEL_VERY_HIGH:
        return "매우 높음"
    if score >= _LEVEL_HIGH:
        return "높음"
    return "보통"


def _lifespan_signals(cur) -> list[dict]:
    """내용연수 초과/임박 설비 (equipment_id 단위)."""
    cur.execute("""
        SELECT
          e.equipment_id, e.sitename, e.facilitytype, e.equipmenttype,
          EXTRACT(YEAR FROM age(now()::date, e.commissioned_at))::int AS years_used,
          l.years_recommended,
          CASE
            WHEN EXTRACT(YEAR FROM age(now()::date, e.commissioned_at))
                 >= l.years_recommended     THEN 'overdue'
            ELSE 'approaching'
          END AS status
        FROM tb_equipment_info e
        JOIN tb_equipment_category_map m ON m.equipmenttype = e.equipmenttype
        JOIN tb_equipment_lifespan    l ON l.category      = m.category
        WHERE e.status IN ('운영중', 'operational')
          AND e.commissioned_at IS NOT NULL
          AND EXTRACT(YEAR FROM age(now()::date, e.commissioned_at))
              >= l.years_recommended - 1
    """)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _mtbf_signals(cur) -> list[dict]:
    """MTBF 90일 미만 설비 (fault_cnt>=2, equipment_id 단위).

    v2 정제 (2026-07-22): 분류 정책상 현장 확인된 fault_category='고장' 만
    카운트하고, 같은 설비의 같은 날 중복 기록(테스트·재입력)은 1회로 접는다
    — 수기 기록 몇 건이 순위를 좌우하는 왜곡 축소. (v_equipment_mtbf 뷰는
    이력 탭 용도로 유지 — 여기서만 인라인 정제 쿼리 사용)
    """
    cur.execute("""
        WITH dedup AS (
            SELECT DISTINCT equipment_id, sitename, facilitytype, equipmenttype,
                   date_trunc('day', task_start_time) AS fault_day
            FROM tb_task_master
            WHERE task_category = '고장보고'
              AND fault_category = '고장'
              AND equipment_id IS NOT NULL
        ), gaps AS (
            SELECT equipment_id, sitename, facilitytype, equipmenttype, fault_day,
                   EXTRACT(epoch FROM (fault_day - lag(fault_day) OVER (
                       PARTITION BY equipment_id ORDER BY fault_day))) / 86400.0 AS gap_days
            FROM dedup
        )
        SELECT equipment_id, sitename, facilitytype, equipmenttype,
               COUNT(*) AS fault_cnt, ROUND(AVG(gap_days)::numeric, 2) AS mtbf_days
        FROM gaps
        GROUP BY 1,2,3,4
        HAVING COUNT(*) >= 2 AND AVG(gap_days) IS NOT NULL AND AVG(gap_days) < 90
    """)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _alarm_trend_signals(cur, days: int) -> list[dict]:
    """실알람 발생 추세 — (sitename, facilitytype, equipmenttype) 그룹 단위.

    기간 내 발생 건수 + 최근 7일 지속 여부. 알람 리포트에 설비 매핑이
    있는 행만 집계 (equipmenttype NOT NULL).
    """
    cur.execute("""
        SELECT sitename, facilitytype, equipmenttype,
               COUNT(*) AS alarm_cnt,
               COUNT(*) FILTER (
                   WHERE alarm_start_time >= now() - interval '7 days'
               ) AS recent7_cnt
        FROM tb_equipment_alarm_report
        WHERE alarm_start_time >= now() - make_interval(days => %s)
          AND equipmenttype IS NOT NULL AND sitename IS NOT NULL
        GROUP BY 1,2,3
        HAVING COUNT(*) >= %s
    """, (days, _ALARM_HIGH_MIN))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _recurrence_signals(days: int) -> list[dict]:
    """조치 후 재발 지속 그룹 (sitename+facilitytype+equipmenttype 단위).

    기존 P5-rev 분류 로직을 그대로 호출 — 임계·HH/LL 필터 이원화 방지.
    """
    from endpoints.alarm_fault_correlation import equipment_status

    resp = equipment_status(
        days=days, min_alarm=10, recurrence_cnt=3, recurrence_rate=0.2,
    )
    return [
        {
            "sitename": r.sitename,
            "facilitytype": r.facilitytype,
            "equipmenttype": r.equipmenttype,
            "alarm_after_resolved": r.alarm_after_resolved,
        }
        for r in resp.rows
        if r.status == "replacement_candidate"
    ]


@router.get("/replacement-priority")
def replacement_priority(
    limit: int = Query(5, ge=1, le=20),
    days: int = Query(90, ge=1, le=365, description="재발 신호 집계 기간"),
) -> dict:
    """4신호 융합 교체 우선순위 Top N (v2 — 실알람 추세 포함).

    응답 rows: [{sitename, facilitytype, equipmenttype, equipment_id,
                 score, level, reasons: [{type, label}]}]
      type ∈ {lifespan_overdue, lifespan_approaching, mtbf, recurrence, alarm_trend}
      equipment_id 는 그룹 신호(재발)만 있는 행에서 null.
    """
    if _get_db_connection is None:
        raise HTTPException(500, "DB 미초기화")

    conn = _get_db_connection()
    try:
        cur = conn.cursor()
        lifespan = _lifespan_signals(cur)
        mtbf = _mtbf_signals(cur)
        try:
            alarm_trend = _alarm_trend_signals(cur, days)
        except Exception as e:
            logger.warning("실알람 추세 신호 집계 실패 — 나머지 신호로 동작: %s", e)
            alarm_trend = []
        cur.close()
    finally:
        conn.close()

    try:
        recurrence = _recurrence_signals(days)
    except Exception as e:  # 재발 신호 실패해도 나머지 2신호로 동작
        logger.warning("재발 신호 집계 실패 — lifespan/MTBF 만 사용: %s", e)
        recurrence = []

    # 설비 단위 병합 — key: (sitename, facilitytype, equipmenttype, equipment_id)
    entries: dict[tuple, dict] = {}

    def _entry(sitename, facilitytype, equipmenttype, equipment_id: Optional[str]):
        key = (sitename, facilitytype, equipmenttype, equipment_id)
        if key not in entries:
            entries[key] = {
                "sitename": sitename,
                "facilitytype": facilitytype,
                "equipmenttype": equipmenttype,
                "equipment_id": equipment_id,
                "score": 0.0,
                "reasons": [],
            }
        return entries[key]

    for r in lifespan:
        e = _entry(r["sitename"], r["facilitytype"], r["equipmenttype"], r["equipment_id"])
        if r["status"] == "overdue":
            e["score"] += _SCORE_LIFESPAN_OVERDUE
            e["reasons"].append({
                "type": "lifespan_overdue",
                "label": f"내용연수 초과 ({r['years_used']}/{r['years_recommended']}년)",
            })
        else:
            e["score"] += _SCORE_LIFESPAN_APPROACHING
            e["reasons"].append({
                "type": "lifespan_approaching",
                "label": f"내용연수 임박 ({r['years_used']}/{r['years_recommended']}년)",
            })

    for r in mtbf:
        e = _entry(r["sitename"], r["facilitytype"], r["equipmenttype"], r["equipment_id"])
        critical = float(r["mtbf_days"]) < 30
        e["score"] += _SCORE_MTBF_CRITICAL if critical else _SCORE_MTBF_WARNING
        e["reasons"].append({
            "type": "mtbf",
            "label": f"MTBF {round(float(r['mtbf_days']))}일 (고장 {r['fault_cnt']}회)",
        })

    # 재발 신호는 그룹 단위 — 같은 그룹의 설비 행 전부에 부여, 없으면 그룹 행 생성
    for r in recurrence:
        group = (r["sitename"], r["facilitytype"], r["equipmenttype"])
        matched = [e for k, e in entries.items() if k[:3] == group and k[3] is not None]
        targets = matched or [_entry(*group, None)]
        for e in targets:
            e["score"] += _SCORE_RECURRENCE
            e["reasons"].append({
                "type": "recurrence",
                "label": f"재발 지속 (조치 후 알람 {r['alarm_after_resolved']}건)",
            })

    # 실알람 추세 — 그룹 단위 (재발 신호와 동일 부여 방식)
    for r in alarm_trend:
        group = (r["sitename"], r["facilitytype"], r["equipmenttype"])
        cnt = int(r["alarm_cnt"])
        surge = cnt >= _ALARM_SURGE_MIN and int(r["recent7_cnt"]) > 0
        matched = [e for k, e in entries.items() if k[:3] == group and k[3] is not None]
        targets = matched or [_entry(*group, None)]
        for e in targets:
            e["score"] += _SCORE_ALARM_SURGE if surge else _SCORE_ALARM_HIGH
            e["reasons"].append({
                "type": "alarm_trend",
                "label": f"최근 {days}일 알람 {cnt:,}건"
                         + (" · 지속 중" if int(r["recent7_cnt"]) > 0 else ""),
            })

    rows = sorted(entries.values(), key=lambda e: (-e["score"], -len(e["reasons"])))
    for e in rows:
        e["score"] = round(e["score"], 1)
        e["level"] = _level(e["score"])

    return {
        "status": "OK",
        "period_days": days,
        "total_candidates": len(rows),
        "rows": rows[:limit],
    }
