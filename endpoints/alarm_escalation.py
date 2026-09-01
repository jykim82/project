"""
미확인 경보 메신저 상신 — docs/alarm-confirm-audit-spec.md P2 (Migration 0140)

경고(심각) 경보가 기준 분(SITE_SETTING.ALARM_ESCALATION_MIN, 0=끔)째
미확인이면 메신저 **전체 채널(all)** 에 1회 상신한다.

- 1회 멱등: escalated_at 기록 — 재상신 없음 (채터링 사태를 메신저로
  옮기지 않는다. 반복 경보라도 건당 1회)
- 발신자 'system' — 운영자 계정을 사칭하지 않는다
- 루프는 ai_server 가 60초 주기로 run_once() 호출.
  POST /alarm/escalation/run 은 수동 트리거(운영 점검·테스트용)

ai_server.py에서 분리된 모듈 — init()으로 DB 커넥션 함수를 주입받아 사용.
"""

import logging

from fastapi import APIRouter

logger = logging.getLogger("slm")

router = APIRouter(tags=["alarm-escalation"])

_get_db_connection = None


def init(get_db_connection_fn):
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


def run_once(region: str = "R01") -> dict:
    """상신 1회 패스 — {escalated: n, threshold_min} (0=끔이면 skipped)."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT COALESCE(comm_val, '10') FROM tb_comm_code "
            "WHERE region = %s AND grp_cd = 'SITE_SETTING' "
            "AND comm_cd = 'ALARM_ESCALATION_MIN' AND use_yn = 'Y'",
            (region,),
        )
        row = cur.fetchone()
        try:
            threshold_min = int(row[0]) if row else 10
        except (TypeError, ValueError):
            threshold_min = 10
        if threshold_min <= 0:
            return {"status": "OK", "escalated": 0, "threshold_min": 0,
                    "detail": "비활성 (0=끔)"}

        # 대상: 경고·진행중·미확인·미상신·기준 경과 (부분 인덱스 0140)
        cur.execute(
            """
            SELECT tagsn, alarm_start_time, sitename,
                   COALESCE(facilitytype, ''), COALESCE(alarm_msg, ''),
                   floor(EXTRACT(epoch FROM (now() - alarm_start_time)) / 60)
            FROM tb_equipment_alarm_report
            WHERE alarm_status = '진행중' AND alarm_confirm_yn = 'N'
              AND escalated_at IS NULL AND alarm_severity = '경고'
              AND alarm_start_time < now() - (%s || ' minutes')::interval
            ORDER BY alarm_start_time
            LIMIT 20
            """,
            (threshold_min,),
        )
        targets = cur.fetchall()

        count = 0
        for tagsn, start_t, sitename, ftype, msg, mins in targets:
            content = (f"⚠️ [경보 상신] {sitename} {ftype} — {msg}\n"
                       f"{int(mins)}분째 미확인 (발생 {start_t:%m-%d %H:%M}). "
                       f"경보관리에서 확인 처리해 주세요.")
            cur.execute(
                "INSERT INTO tb_user_chat_message "
                "(region, room_id, sender_id, content) VALUES (%s, 'all', 'system', %s)",
                (region, content),
            )
            cur.execute(
                "UPDATE tb_equipment_alarm_report SET escalated_at = now() "
                "WHERE tagsn = %s AND alarm_start_time = %s",
                (tagsn, start_t),
            )
            count += 1
        conn.commit()
        cur.close()
        if count:
            logger.info(f"[escalation] 미확인 경보 {count}건 메신저 상신 "
                        f"(기준 {threshold_min}분)")
        return {"status": "OK", "escalated": count,
                "threshold_min": threshold_min}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"경보 상신 실패: {e}")
        return {"status": "ERROR", "message": str(e), "escalated": 0}
    finally:
        if conn:
            conn.close()


@router.post("/alarm/escalation/run")
async def trigger_escalation(region: str = "R01"):
    """수동 1회 실행 — 운영 점검·테스트용 (루프와 동일 로직·멱등)."""
    import asyncio
    return await asyncio.to_thread(run_once, region)
