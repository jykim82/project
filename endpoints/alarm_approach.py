"""
임계 도달 예측 API — docs/alarm-approach-spec.md

- GET /alarm/approach — 수위가 현재 추세로 HH/LL 임계에 도달할 시점 예측

현재 경보는 임계를 넘은 뒤에 울린다. 수위는 물리적으로 느리게 움직이므로
최근 추세의 선형 외삽만으로 "이 속도면 LL 도달까지 N분"을 앞당겨 보여줄
수 있다. Chronos 같은 모델을 쓰지 않는 것은 의도다 — 이 용도(단조 추세의
단기 외삽)엔 선형이 맞고, 저사양 서버 전제에도 맞다.

실제 경보를 만들지 않는다 — tb_equipment_alarm_report 에 행을 쓰면 예측이
감사·통계를 오염시킨다. 조회 시점 계산 + 화면 표출까지만 한다 (실측 89ms).

ai_server.py에서 분리된 모듈 — init()으로 DB 커넥션 함수를 주입받아 사용.
"""

import logging

from fastapi import APIRouter, Query

logger = logging.getLogger("slm")

router = APIRouter(tags=["alarm-approach"])

# ai_server.py에서 주입
_get_db_connection = None


def init(get_db_connection_fn):
    """ai_server.py에서 DB 커넥션 팩토리 함수를 주입받는다."""
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


# ── 판정 파라미터 ──────────────────────────────────────────────
# 추세 창. 수위 원시는 1~3분 간격이라 60분이면 20~60점 — 회귀에 충분하고,
# 더 길게 잡으면 일주기 곡선이 섞여 기울기가 무뎌진다.
_TREND_WINDOW_MIN = 60
# 회귀 최소 점수·설명력. R² 게이트가 없으면 펌프 기동 등으로 출렁이는
# 태그가 우연한 기울기로 오탐을 만든다.
_MIN_POINTS = 10
_MIN_R2 = 0.5
# 유의미 기울기 하한 (m/분). 0.001 = 시간당 6cm — 이보다 느리면 사실상 정체.
_MIN_SLOPE = 0.001
# 도달 예측 상한 (분). 수위는 일주기(야간 충수·주간 방류)라 이보다 먼
# 외삽은 물리적으로 무의미하다 — 추세가 그 전에 바뀐다.
_DEFAULT_HORIZON_MIN = 240


@router.get("/alarm/approach")
def get_alarm_approach(
    horizon_min: int = Query(_DEFAULT_HORIZON_MIN, ge=10, le=1440),
):
    """임계 도달 예측 목록.

    동기 def — 블로킹 psycopg2 (memory/feedback_fastapi_blocking_endpoint).
    """
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()

        # 페어링은 tagsn 명명 규칙: 같은 설비코드(두 번째 세그먼트)의
        # LEI(수위 측정) ↔ LEC(임계 설정, HH/LL 은 datainfo). 실측 55개
        # 측정 태그 중 41개 페어 성립.
        #
        # 같은 설비에 임계가 복수면(지별 설정 등) **먼저 도달하는 임계**를
        # 쓴다 — HH 는 현재값보다 큰 것 중 최소, LL 은 작은 것 중 최대.
        # 사전 경보는 보수적(이른) 쪽이 옳다.
        cur.execute(
            f"""
            WITH lei AS (
                SELECT tagsn, sitename, facilitytype, datainfo,
                       split_part(tagsn, '_', 2) AS dev
                FROM tb_tag_info
                WHERE tagsn LIKE %s AND datainfo ~ '수위'
            ),
            lec AS (
                SELECT tagsn, split_part(tagsn, '_', 2) AS dev,
                       CASE WHEN datainfo ~ 'HH' THEN 'HH'
                            WHEN datainfo ~ 'LL' THEN 'LL' END AS lvl
                FROM tb_tag_info
                WHERE tagsn LIKE %s AND datainfo ~ 'HH|LL'
            ),
            trend AS (
                SELECT tagsn,
                       regr_slope(val, EXTRACT(epoch FROM logtime) / 60.0)
                           AS slope_per_min,
                       regr_r2(val, EXTRACT(epoch FROM logtime) / 60.0) AS r2,
                       count(*) AS pts,
                       max(val) FILTER (
                           WHERE logtime > now() - interval '6 minutes'
                       ) AS cur_val
                FROM tb_tag_raw_data
                WHERE logtime > now() - interval '{_TREND_WINDOW_MIN} minutes'
                  AND tagsn IN (SELECT tagsn FROM lei)
                  AND val IS NOT NULL
                GROUP BY tagsn
            ),
            sp AS (
                SELECT DISTINCT ON (tagsn) tagsn, val
                FROM tb_tag_raw_data
                WHERE logtime > now() - interval '1 day'
                  AND tagsn IN (SELECT tagsn FROM lec)
                ORDER BY tagsn, logtime DESC
            ),
            -- 측정 태그 × 레벨(HH/LL)당 "먼저 도달하는" 임계 하나로 압축
            paired AS (
                SELECT lei.tagsn, lei.sitename, lei.facilitytype, lei.datainfo,
                       t.cur_val, t.slope_per_min, t.r2, l.lvl,
                       CASE WHEN l.lvl = 'HH'
                            THEN min(sp.val) FILTER (WHERE sp.val > t.cur_val)
                            ELSE max(sp.val) FILTER (WHERE sp.val < t.cur_val)
                       END AS threshold
                FROM lei
                JOIN trend t ON t.tagsn = lei.tagsn
                    AND t.pts >= {_MIN_POINTS} AND t.cur_val IS NOT NULL
                JOIN lec l ON l.dev = lei.dev
                JOIN sp ON sp.tagsn = l.tagsn
                WHERE t.r2 > {_MIN_R2}
                GROUP BY lei.tagsn, lei.sitename, lei.facilitytype,
                         lei.datainfo, t.cur_val, t.slope_per_min, t.r2, l.lvl
            )
            SELECT tagsn, sitename, facilitytype, datainfo,
                   round(cur_val::numeric, 2),
                   round(slope_per_min::numeric, 4),
                   round(r2::numeric, 2),
                   lvl, round(threshold::numeric, 2),
                   round(((threshold - cur_val) / slope_per_min)::numeric, 0)
                       AS eta_min
            FROM paired
            WHERE threshold IS NOT NULL
              AND ((lvl = 'HH' AND slope_per_min > {_MIN_SLOPE})
                   OR (lvl = 'LL' AND slope_per_min < -{_MIN_SLOPE}))
              AND (threshold - cur_val) / slope_per_min
                  BETWEEN 0 AND {int(horizon_min)}
            ORDER BY eta_min
            """,
            ("%\\_LEI\\_%", "%\\_LEC\\_%"),
        )
        rows = cur.fetchall()

        # 감시 커버리지 — "0건"이 침묵이 아니라 "41페어 감시 중, 접근 없음"
        # 으로 읽히게 한다
        cur.execute(
            """
            SELECT count(DISTINCT lei.tagsn)
            FROM (SELECT tagsn, split_part(tagsn,'_',2) dev FROM tb_tag_info
                  WHERE tagsn LIKE %s AND datainfo ~ '수위') lei
            JOIN (SELECT split_part(tagsn,'_',2) dev FROM tb_tag_info
                  WHERE tagsn LIKE %s AND datainfo ~ 'HH|LL') lec
              ON lec.dev = lei.dev
            """,
            ("%\\_LEI\\_%", "%\\_LEC\\_%"),
        )
        monitored = int(cur.fetchone()[0] or 0)
        cur.close()

        items = [
            {
                "tagsn": r[0], "sitename": r[1], "facilitytype": r[2],
                "datainfo": r[3],
                "current": float(r[4]), "slope_per_min": float(r[5]),
                "r2": float(r[6]), "level": r[7], "threshold": float(r[8]),
                "eta_min": int(r[9]),
            }
            for r in rows
        ]

        return {
            "status": "OK",
            "horizon_min": horizon_min,
            "monitored_tags": monitored,
            "params": {
                "trend_window_min": _TREND_WINDOW_MIN,
                "min_r2": _MIN_R2,
                "min_slope_per_min": _MIN_SLOPE,
            },
            "items": items,
        }

    except Exception as e:
        logger.error(f"임계 도달 예측 실패: {e}")
        return {"status": "ERROR", "message": str(e), "items": []}
    finally:
        if conn:
            conn.close()
