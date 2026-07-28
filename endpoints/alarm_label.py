"""
경보 판정 라벨 API — docs/alarm-label-feedback-spec.md (로드맵 C P1)

- PUT /crisis/alarm-reports/label — 1클릭 판정 (real/false/check, 재클릭 해제)
- GET /crisis/alarm-label-stats  — 오탐률 추이 (주별 + 분류별)

목적은 두 가지다.
1) 판정 수집 — 자동 이상감지의 오탐/실제를 운영자가 기록한다.
2) 오탐률 추이 — "도입 후 오탐률 42% → 11%" 형태의 정량 실적. 라벨이
   분모다: 오탐률 = false / (real + false). check(점검필요)는 판정 보류라
   분모에서 뺀다 — 섞으면 비율이 판정이 아니라 보류량에 흔들린다.

가중치 자동 조정은 P2 다 — 분류별 라벨 30건 미만에서 조정하면 소표본
과적합이 된다 (roadmap 부록 B.4). P1 은 수집·표시까지만.

채팅 오답 피드백(chat-feedback-telemetry)과 다른 체계다 — 그쪽은 인텐트
분류 품질, 이쪽은 이상감지 판정 품질. 합치지 않는다 (roadmap 부록 A.4).

ai_server.py에서 분리된 모듈 — init()으로 DB 커넥션 함수를 주입받아 사용.
"""

import logging

from fastapi import APIRouter, Query, Request

logger = logging.getLogger("slm")

router = APIRouter(tags=["alarm-label"])

# ai_server.py에서 주입
_get_db_connection = None


def init(get_db_connection_fn):
    """ai_server.py에서 DB 커넥션 팩토리 함수를 주입받는다."""
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


_VALID_LABELS = {"real", "false", "check"}


@router.put("/crisis/alarm-reports/label")
async def set_alarm_label(request: Request):
    """경보 판정 라벨 기록.

    Body: { tagsn, alarm_start_time, label: real|false|check|null, user_id }
    label null = 판정 해제 (실수 정정 경로 — 확인(0131)과 달리 라벨은
    판단이라 정정을 허용한다. 대신 항상 마지막 판정자·시각을 남긴다).
    """
    conn = None
    try:
        body = await request.json()
        tagsn = body.get("tagsn", "")
        alarm_start_time = body.get("alarm_start_time", "")
        label = body.get("label")  # None 허용
        user_id = (body.get("user_id") or "").strip()

        if not tagsn or not alarm_start_time:
            return {"status": "error", "message": "tagsn, alarm_start_time 필수"}
        if label is not None and label not in _VALID_LABELS:
            return {"status": "error", "message": f"label 은 {_VALID_LABELS} 또는 null"}

        conn = _get_db_connection()
        cur = conn.cursor()
        # is_false_alarm 은 파생으로 동기화 — Node-RED 필터·경보분석 화면이
        # 아직 그 컬럼을 본다. 정본은 anomaly_label (Migration 0132).
        cur.execute(
            """
            UPDATE tb_equipment_alarm_report
            SET anomaly_label = %s,
                labeled_by = CASE WHEN %s <> '' THEN %s ELSE labeled_by END,
                labeled_at = NOW(),
                -- 파생 동기화: false=Y, 그 외(real/check/해제)=N.
                -- 해제도 N 이다 — 판정 무효인데 파생 Y 가 남으면 라벨 없는
                -- 행이 오탐으로 계속 필터링된다
                is_false_alarm = CASE WHEN %s = 'false' THEN 'Y' ELSE 'N' END,
                info_updated = TO_CHAR(NOW(), 'YYYY-MM-DD HH24:MI:SS')
            WHERE tagsn = %s AND alarm_start_time = %s::timestamp
            """,
            [label, user_id, user_id, label, tagsn, alarm_start_time],
        )
        updated = cur.rowcount
        conn.commit()
        cur.close()
        if updated == 0:
            return {"status": "error", "message": "대상 경보 없음"}
        return {"status": "OK", "label": label}
    except Exception as e:
        logger.error(f"경보 라벨 기록 실패: {e}")
        if conn:
            conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.get("/crisis/alarm-label-stats")
def get_alarm_label_stats(weeks: int = Query(12, ge=2, le=52)):
    """오탐률 추이 — 주별 + 분류별.

    동기 def — 블로킹 psycopg2 (memory/feedback_fastapi_blocking_endpoint).
    """
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()

        # 주별 추이. 주 기준은 판정 시각이 아니라 **경보 발생 시각**이다 —
        # "그 주에 발생한 경보가 얼마나 오탐이었나"가 실적 지표지,
        # "그 주에 몇 건 판정했나"가 아니다. 백필분(labeled_at NULL)도
        # 발생 주 기준으로 자연 편입된다.
        cur.execute(
            f"""
            SELECT TO_CHAR(date_trunc('week', alarm_start_time), 'YYYY-MM-DD') AS wk,
                   count(*) FILTER (WHERE anomaly_label = 'real')  AS real_cnt,
                   count(*) FILTER (WHERE anomaly_label = 'false') AS false_cnt,
                   count(*) FILTER (WHERE anomaly_label = 'check') AS check_cnt,
                   count(*) AS total_alarms
            FROM tb_equipment_alarm_report
            WHERE alarm_start_time > now() - interval '{int(weeks)} weeks'
            GROUP BY 1 ORDER BY 1
            """
        )
        weekly = []
        for wk, real_c, false_c, check_c, total in cur.fetchall():
            judged = int(real_c) + int(false_c)
            weekly.append({
                "week": wk,
                "real": int(real_c), "false": int(false_c),
                "check": int(check_c), "total_alarms": int(total),
                "labeled": judged + int(check_c),
                # 판정된 것(real+false) 대비 오탐 비율. 판정 0건이면 null —
                # 0% 로 보이면 "오탐 없음"으로 오독된다
                "false_rate": round(false_c / judged * 100, 1) if judged else None,
            })

        # 분류별 누적 (같은 기간) — 가중치 조정 P2 의 준비 지표.
        # 30건 미만 분류는 조정 유보 대상임을 프런트가 표시한다 (부록 B.4)
        cur.execute(
            f"""
            SELECT coalesce(alarm_category, '미분류'),
                   count(*) FILTER (WHERE anomaly_label = 'real'),
                   count(*) FILTER (WHERE anomaly_label = 'false'),
                   count(*) FILTER (WHERE anomaly_label = 'check'),
                   count(*)
            FROM tb_equipment_alarm_report
            WHERE alarm_start_time > now() - interval '{int(weeks)} weeks'
            GROUP BY 1 ORDER BY 5 DESC
            """
        )
        by_category = []
        for cat, real_c, false_c, check_c, total in cur.fetchall():
            judged = int(real_c) + int(false_c)
            by_category.append({
                "category": cat,
                "real": int(real_c), "false": int(false_c),
                "check": int(check_c), "total_alarms": int(total),
                "labeled": judged + int(check_c),
                "false_rate": round(false_c / judged * 100, 1) if judged else None,
            })
        cur.close()

        totals = {
            "real": sum(w["real"] for w in weekly),
            "false": sum(w["false"] for w in weekly),
            "check": sum(w["check"] for w in weekly),
            "total_alarms": sum(w["total_alarms"] for w in weekly),
        }
        judged = totals["real"] + totals["false"]
        totals["labeled"] = judged + totals["check"]
        totals["false_rate"] = (
            round(totals["false"] / judged * 100, 1) if judged else None
        )

        return {
            "status": "OK", "weeks": weeks,
            "totals": totals, "weekly": weekly, "by_category": by_category,
            # 가중치 조정 유보 하한 (roadmap 부록 B.4)
            "min_labels_for_tuning": 30,
        }
    except Exception as e:
        logger.error(f"경보 라벨 통계 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


@router.put("/crisis/alarm-reports/label-group")
async def set_alarm_label_group(request: Request):
    """(현장, 경보메시지) 그룹 일괄 판정 — 반복 경보 순위표에서 호출.

    죽동 탁도계처럼 수천 건 반복되는 경보를 행별로 판정하는 것은 비현실이라
    그룹 단위로 채운다 (spec §6). 규칙:

    - **미판정 건만 채운다** — 일괄이 기존 개별 판정을 덮으면 정성 들인
      판정이 사라진다. 정정은 행별로.
    - 해제(null)는 그룹 단위로 지원하지 않는다 — 일괄 삭제는 위험하다.

    Body: { sitename, alarm_msg, label: real|false|check, days, user_id }
    """
    conn = None
    try:
        body = await request.json()
        sitename = (body.get("sitename") or "").strip()
        alarm_msg = body.get("alarm_msg") or ""
        label = body.get("label")
        days = int(body.get("days") or 30)
        user_id = (body.get("user_id") or "").strip()

        if not sitename or not alarm_msg:
            return {"status": "error", "message": "sitename, alarm_msg 필수"}
        if label not in _VALID_LABELS:
            return {"status": "error", "message": f"label 은 {_VALID_LABELS} (그룹 해제 미지원)"}
        if not 1 <= days <= 365:
            return {"status": "error", "message": "days 는 1~365"}

        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute(
            f"""
            UPDATE tb_equipment_alarm_report
            SET anomaly_label = %s,
                labeled_by = CASE WHEN %s <> '' THEN %s ELSE labeled_by END,
                labeled_at = NOW(),
                is_false_alarm = CASE WHEN %s = 'false' THEN 'Y' ELSE 'N' END,
                info_updated = TO_CHAR(NOW(), 'YYYY-MM-DD HH24:MI:SS')
            WHERE sitename = %s AND alarm_msg = %s
              AND alarm_start_time > now() - interval '{days} days'
              AND anomaly_label IS NULL
            """,
            [label, user_id, user_id, label, sitename, alarm_msg],
        )
        updated = cur.rowcount
        conn.commit()
        cur.close()
        return {"status": "OK", "labeled": updated}
    except Exception as e:
        logger.error(f"경보 그룹 판정 실패: {e}")
        if conn:
            conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        if conn:
            conn.close()
