"""
야간최소유량 CUSUM 기반 누수 의심 알림

- POST  /leak-cusum/scan           — 수동 스캔 트리거 (디버그·강제 실행)
- GET   /leak-cusum/alerts         — 알림 이력 조회 (acknowledged 필터)
- PATCH /leak-cusum/alerts/{id}/ack — 확인 처리

백그라운드 태스크는 ai_server.py lifespan에서 `_run_leak_cusum_scan_loop()`로
주기 실행 (기본 6시간 간격).
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger("slm")

router = APIRouter()

_get_db_connection = None


def init(get_db_connection_fn):
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


# =============================================================================
# 스캔 로직 — 백그라운드 태스크에서도 호출
# =============================================================================

def run_leak_cusum_scan(
    execute_night_min_flow_query_fn,
    region: str = "R01",
    sitename_like: str = "",
    facilitytype: str = "소블록",
    days: int = 90,
    dedupe_hours: int = 24,
) -> dict:
    """
    야간최소유량 데이터를 조회해 CUSUM 분석 → "누수의심" 태그를 알림 테이블에 기록.

    중복 방지: 최근 `dedupe_hours` 이내에 동일 tagsn의 알림이 있으면 건너뜀.

    반환 통계:
      {
        "scanned_tags": int,
        "detected": int,        # 이번 스캔에서 누수의심으로 판정된 태그 수
        "inserted": int,        # 실제로 테이블에 새로 저장된 건수 (dedupe 이후)
        "skipped_dup": int,     # 중복으로 skip된 건수
      }
    """
    if _get_db_connection is None:
        raise RuntimeError("leak_cusum_alert not initialized")

    try:
        # 1. 야간최소유량 시계열 조회
        rows, columns = execute_night_min_flow_query_fn(
            sitename=sitename_like or "%%",
            facilitytype=facilitytype,
            days=days,
        )
    except Exception as e:
        logger.warning(f"CUSUM scan: night_min_flow 쿼리 실패: {e}")
        return {"scanned_tags": 0, "detected": 0, "inserted": 0, "skipped_dup": 0}

    if not rows:
        return {"scanned_tags": 0, "detected": 0, "inserted": 0, "skipped_dup": 0}

    # 2. CUSUM 분석 (기존 엔진 재사용)
    from anomaly_detector import compute_cusum_for_tags
    cusum_results = compute_cusum_for_tags(rows, columns)
    scanned = len(cusum_results)

    # 3. "누수의심"만 필터
    suspects = [
        (tagsn, info)
        for tagsn, info in cusum_results.items()
        if info.get("leak_status") == "누수의심"
    ]
    detected = len(suspects)

    if not suspects:
        return {
            "scanned_tags": scanned, "detected": 0,
            "inserted": 0, "skipped_dup": 0,
        }

    # 4. 저장 (dedupe_hours 이내 중복 제외)
    inserted = 0
    skipped = 0
    conn = None
    try:
        conn = _get_db_connection()
        with conn.cursor() as cur:
            for tagsn, info in suspects:
                cur.execute(
                    """
                    SELECT 1 FROM tb_leak_cusum_alert
                    WHERE region = %s AND tagsn = %s
                      AND detected_at >= NOW() - (%s || ' hours')::interval
                    LIMIT 1
                    """,
                    (region, tagsn, dedupe_hours),
                )
                if cur.fetchone():
                    skipped += 1
                    continue
                cur.execute(
                    """
                    INSERT INTO tb_leak_cusum_alert
                        (region, sitename, facilitytype, tagsn, label,
                         leak_status, cusum_value, threshold_h, baseline_mean)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        region,
                        info.get("sitename", ""),
                        info.get("facilitytype", "소블록"),
                        tagsn,
                        info.get("label", ""),
                        info.get("leak_status", "누수의심"),
                        float(info.get("cusum_current") or 0),
                        float(info.get("threshold_h") or 0),
                        float(info.get("baseline_mean") or 0),
                    ),
                )
                inserted += 1
        conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"CUSUM scan 저장 실패: {e}")
    finally:
        if conn:
            conn.close()

    logger.info(
        f"누수 CUSUM 스캔 완료: 태그 {scanned}개 분석, "
        f"누수의심 {detected}건, 신규 저장 {inserted}건, 중복 skip {skipped}건"
    )
    return {
        "scanned_tags": scanned,
        "detected": detected,
        "inserted": inserted,
        "skipped_dup": skipped,
    }


# =============================================================================
# 엔드포인트
# =============================================================================

class AckPayload(BaseModel):
    ack_by: str = Field(..., max_length=45)
    note: Optional[str] = Field(None, max_length=500)


_SELECT_COLS = (
    "alert_id, region, sitename, facilitytype, tagsn, label, "
    "leak_status, cusum_value, threshold_h, baseline_mean, "
    "detected_at, acknowledged, ack_at, ack_by, note"
)


def _row_to_dict(r) -> dict:
    return {
        "alert_id": r[0],
        "region": r[1],
        "sitename": r[2],
        "facilitytype": r[3],
        "tagsn": r[4],
        "label": r[5],
        "leak_status": r[6],
        "cusum_value": float(r[7]) if r[7] is not None else None,
        "threshold_h": float(r[8]) if r[8] is not None else None,
        "baseline_mean": float(r[9]) if r[9] is not None else None,
        "detected_at": r[10].isoformat() if r[10] else None,
        "acknowledged": bool(r[11]),
        "ack_at": r[12].isoformat() if r[12] else None,
        "ack_by": r[13],
        "note": r[14],
    }


@router.get("/leak-cusum/alerts")
def list_alerts(
    region: str = Query("R01"),
    acknowledged: Optional[bool] = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    if _get_db_connection is None:
        raise HTTPException(500, "leak_cusum_alert not initialized")
    conn = None
    try:
        conn = _get_db_connection()
        with conn.cursor() as cur:
            if acknowledged is None:
                cur.execute(
                    f"SELECT {_SELECT_COLS} FROM tb_leak_cusum_alert "
                    "WHERE region = %s ORDER BY detected_at DESC LIMIT %s",
                    (region, limit),
                )
            else:
                cur.execute(
                    f"SELECT {_SELECT_COLS} FROM tb_leak_cusum_alert "
                    "WHERE region = %s AND acknowledged = %s "
                    "ORDER BY detected_at DESC LIMIT %s",
                    (region, acknowledged, limit),
                )
            rows = [_row_to_dict(r) for r in cur.fetchall()]
        return {"status": "OK", "total": len(rows), "data": rows}
    except Exception as e:
        logger.error(f"list_leak_alerts 실패: {e}")
        raise HTTPException(500, str(e))
    finally:
        if conn:
            conn.close()


@router.patch("/leak-cusum/alerts/{alert_id}/ack")
def ack_alert(alert_id: int, body: AckPayload):
    if _get_db_connection is None:
        raise HTTPException(500, "leak_cusum_alert not initialized")
    conn = None
    try:
        conn = _get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tb_leak_cusum_alert "
                "SET acknowledged = true, ack_at = now(), "
                "    ack_by = %s, note = COALESCE(%s, note) "
                "WHERE alert_id = %s "
                f"RETURNING {_SELECT_COLS}",
                (body.ack_by, body.note, alert_id),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "alert not found")
        conn.commit()
        return _row_to_dict(row)
    except HTTPException:
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"ack_leak_alert 실패: {e}")
        raise HTTPException(500, str(e))
    finally:
        if conn:
            conn.close()


# 수동 스캔 트리거 (디버그·운영자용)
@router.post("/leak-cusum/scan")
def manual_scan(
    region: str = Query("R01"),
    facilitytype: str = Query("소블록"),
    days: int = Query(90, ge=7, le=365),
):
    """수동 CUSUM 스캔 실행. 백그라운드 태스크와 동일 로직."""
    from sql_executor import _execute_night_min_flow_query as _night_fn

    def _exec(sitename: str, facilitytype: str, days: int):
        from datetime import datetime, timedelta
        to_ts = datetime.now().strftime("%Y-%m-%d")
        from_ts = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        return _night_fn(sitename, facilitytype, from_ts, to_ts)

    stats = run_leak_cusum_scan(
        execute_night_min_flow_query_fn=_exec,
        region=region,
        facilitytype=facilitytype,
        days=days,
    )
    return {"status": "OK", **stats}
