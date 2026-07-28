"""
반복 경보(채터링) 분석 API — docs/alarm-chattering-spec.md

- GET /crisis/alarm-chattering — 반복 경보 순위표

왜 필요한가 (2026-07-27 실측):
    최근 30일 경보 3,561건 중 **죽동(배) 탁도계 통신이상 한 건이 2,866건(81%)**.
    같은 기간 확인율은 90일 12,053건 중 36건 = 0.3%.
    한 태그가 목록을 잠식해 운영자가 경보 자체를 안 보게 된 상태다.
    경보 기능을 더 얹기 전에 이걸 먼저 걷어내야 한다.

설계 원칙:
    원본 행을 지우거나 합치지 않는다. 경보 이력은 감사 자료다.
    여기서는 **집계해서 보여줄 뿐**이고, 목록 화면의 접기도 표시 계층에서만 한다.

    기존 "억제"(작업관리 task_suppressed, is_false_alarm)와 혼동하면 안 된다.
    억제는 "이 경보를 무시한다"는 정책 결정이고, 반복 집계는 "같은 경보가
    N번 울렸다"는 사실 보고다. 억제 여부와 무관하게 집계한다.

ai_server.py에서 분리된 모듈 — init()으로 DB 커넥션 함수를 주입받아 사용.
"""

import logging

from fastapi import APIRouter, Query

logger = logging.getLogger("slm")

router = APIRouter(tags=["alarm-chattering"])

# ai_server.py에서 주입
_get_db_connection = None


def init(get_db_connection_fn):
    """ai_server.py에서 DB 커넥션 팩토리 함수를 주입받는다."""
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


# ---------------------------------------------------------------------------
# 반복 유형 판정 기준
# ---------------------------------------------------------------------------
# 갈라야 하는 이유는 **조치 주체가 다르기** 때문이다.
#   짧게 붙었다 떨어지는 반복 → 계측·신호 품질 문제.
#       조치는 SCADA 데드밴드·on-delay 설정 또는 현장 점검.
#   오래 지속되는 상태가 반복    → 실제 운영 문제.
#       조치는 공급·설비 검토. 경보 설정을 건드리면 오히려 위험을 가린다.
#
# 판정은 **평균 지속시간 하나**로 한다. 처음엔 "시간당 발생 횟수"를 같이
# 걸었으나 실측에서 틀렸다 (최근 30일):
#   성하리(배) 오뚜기 HH    78회 · 평균 12.8분 · 재발간격 중앙값 1.0분
#       → 간격 1분이면 명백한 채터링인데 관측구간이 길어 0.32회/시간,
#         시간당 기준에 걸려 '만성'으로 오분류됐다.
#   천의리(가) 가압장침수  135회 · 평균 1.5분  · 0.19회/시간
#       → 1.5분짜리 펄스가 135번이면 실제 침수가 아니라 순간 펄스인데
#         역시 '만성'으로 빠졌다.
# 시간당 횟수는 관측 구간 전체의 평균이라 "몰려서 터지는" 성질을 못 잡는다.
# 반면 지속시간은 "상태가 실제로 유지됐는가"를 직접 말해준다.
# 시간당 횟수·재발 간격은 판정에서 빼고 강도 지표로만 돌려준다.
_CHATTERING_MAX_DURATION_MIN = 15.0   # 이보다 짧게 끝나면 "붙었다 떨어진 것"

# 순위표 진입 하한. 이보다 적으면 반복이라 부를 근거가 약하다.
_DEFAULT_MIN_COUNT = 5


@router.get("/crisis/alarm-chattering")
async def get_alarm_chattering(
    days: int = Query(30, ge=1, le=365),
    min_count: int = Query(_DEFAULT_MIN_COUNT, ge=2, le=1000),
):
    """반복 경보 순위표 — (현장, 경보메시지) 단위 집계.

    tagsn 이 아니라 (sitename, alarm_msg) 로 묶는 이유: 같은 현상이 태그
    교체·재등록으로 tagsn 이 바뀌어도 운영자에겐 같은 경보다. tagsn 은
    대표값으로 함께 돌려준다(SCADA 에서 찾아갈 때 필요).
    """
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()

        # 지속시간: 진행중(alarm_end_time IS NULL)은 now() 까지로 본다.
        # 재발간격: 직전 경보가 끝난 시점 ~ 다음 경보 시작 시점의 중앙값.
        #   평균이 아니라 중앙값을 쓰는 이유 — 야간 정지 등으로 한 번씩 크게
        #   벌어지는 구간이 평균을 끌어올려 채터링을 가려버린다.
        cur.execute(
            """
            WITH base AS (
                SELECT sitename, facilitytype, equipmenttype, tagsn,
                       alarm_msg, alarm_category, alarm_severity,
                       alarm_start_time, alarm_end_time, alarm_confirm_yn,
                       anomaly_label,
                       LEAD(alarm_start_time) OVER (
                           PARTITION BY sitename, alarm_msg
                           ORDER BY alarm_start_time
                       ) AS next_start
                FROM tb_equipment_alarm_report
                WHERE alarm_start_time > now() - (%s || ' days')::interval
            ),
            agg AS (
                SELECT
                    sitename,
                    MIN(facilitytype)  AS facilitytype,
                    MIN(equipmenttype) AS equipmenttype,
                    MIN(tagsn)         AS tagsn,
                    alarm_msg,
                    MIN(alarm_category) AS alarm_category,
                    MIN(alarm_severity) AS alarm_severity,
                    COUNT(*)           AS cnt,
                    COUNT(*) FILTER (WHERE alarm_confirm_yn = 'Y') AS confirmed,
                    COUNT(*) FILTER (WHERE anomaly_label IS NOT NULL) AS labeled,
                    MIN(alarm_start_time) AS first_seen,
                    MAX(alarm_start_time) AS last_seen,
                    AVG(EXTRACT(epoch FROM (
                        COALESCE(alarm_end_time, now()) - alarm_start_time
                    )) / 60.0) AS avg_dur_min,
                    -- GREATEST(...,0): 한 메시지가 여러 태그에서 나오면 구간이
                    -- 겹쳐 "직전 종료 → 다음 시작"이 음수가 된다(실측 -496분).
                    -- 음수 간격은 뜻이 없고, 겹침은 "해제 전에 또 울렸다"이므로
                    -- 간격 0 으로 본다. 프런트는 0 을 '겹침'으로 표시한다.
                    percentile_cont(0.5) WITHIN GROUP (
                        ORDER BY GREATEST(EXTRACT(epoch FROM (
                            next_start - COALESCE(alarm_end_time, alarm_start_time)
                        )) / 60.0, 0)
                    ) AS median_gap_min
                FROM base
                GROUP BY sitename, alarm_msg
                HAVING COUNT(*) >= %s
            )
            SELECT
                sitename, facilitytype, equipmenttype, tagsn,
                alarm_msg, alarm_category, alarm_severity,
                cnt, confirmed, labeled,
                TO_CHAR(first_seen, 'YYYY-MM-DD HH24:MI:SS'),
                TO_CHAR(last_seen,  'YYYY-MM-DD HH24:MI:SS'),
                ROUND(avg_dur_min::numeric, 1),
                ROUND(median_gap_min::numeric, 1),
                -- 시간당 발생 횟수: 관측 구간(첫~마지막) 기준.
                -- 하루 몰아친 뒤 멎은 경보를 30일로 나눠 희석하지 않기 위함.
                ROUND((cnt::numeric / GREATEST(
                    EXTRACT(epoch FROM (last_seen - first_seen)) / 3600.0, 1.0
                ))::numeric, 2)
            FROM agg
            ORDER BY cnt DESC
            """,
            (days, min_count),
        )
        rows = cur.fetchall()

        cur.execute(
            "SELECT COUNT(*) FROM tb_equipment_alarm_report "
            "WHERE alarm_start_time > now() - (%s || ' days')::interval",
            (days,),
        )
        total = cur.fetchone()[0] or 0
        cur.close()

        items = []
        for r in rows:
            cnt = int(r[7])
            avg_dur = float(r[12]) if r[12] is not None else 0.0
            per_hour = float(r[14]) if r[14] is not None else 0.0
            items.append({
                "sitename": r[0] or "",
                "facilitytype": r[1] or "",
                "equipmenttype": r[2] or "",
                "tagsn": r[3] or "",
                "alarm_msg": r[4] or "",
                "alarm_category": r[5] or "",
                "alarm_severity": r[6] or "",
                "count": cnt,
                "confirmed": int(r[8]),
                # 판정 진행도 — 그룹 일괄 판정 후 남은 미판정을 보여준다
                "labeled": int(r[9]),
                "confirm_rate": round(int(r[8]) / cnt * 100, 1) if cnt else 0.0,
                "share_pct": round(cnt / total * 100, 1) if total else 0.0,
                "first_seen": r[10],
                "last_seen": r[11],
                "avg_duration_min": avg_dur,
                "median_gap_min": float(r[13]) if r[13] is not None else None,
                "per_hour": per_hour,
                "kind": (
                    "chattering"
                    if avg_dur <= _CHATTERING_MAX_DURATION_MIN
                    else "chronic"
                ),
            })

        return {
            "status": "OK",
            "days": days,
            "min_count": min_count,
            "total_alarms": total,
            # 반복 경보가 전체에서 차지하는 비중 — "목록이 잠식됐다"의 크기
            "repeat_alarms": sum(i["count"] for i in items),
            "thresholds": {
                "chattering_max_duration_min": _CHATTERING_MAX_DURATION_MIN,
            },
            "items": items,
        }

    except Exception as e:
        logger.error(f"반복 경보 조회 실패: {e}")
        return {"status": "ERROR", "message": str(e), "items": []}
    finally:
        if conn:
            conn.close()
