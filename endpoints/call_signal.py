"""
운영자 1:1 음성 통화 시그널링 P3 — tb_call_session
(docs/realtime-comm-spec.md §5.4)

REST 폴링 시그널링 (wss 불요 — 기존 HTTPS/프록시 인프라 그대로):
  caller: invite(offer SDP) → status 폴링 → answer SDP 수신 → P2P 연결
  callee: incoming 폴링 → answer(응답 SDP) / reject
미디어는 WebRTC LAN P2P 직결 (DTLS-SRTP 내장 암호화). ICE 는 non-trickle.
"""

import base64
import hashlib
import hmac
import logging
import os
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("slm")

router = APIRouter(prefix="/call", tags=["call"])

_get_db_connection = None

# ringing 상태가 이 시간(초)을 넘으면 부재중(missed) 처리 — 조회 시점 정리
RING_TIMEOUT_SEC = 60

# 외부망 통화 TURN 옵션 (§5.5) — 납품 기본 off. coturn use-auth-secret 방식과
# 동일 시크릿으로 시간제한 자격증명(HMAC-SHA1)을 발급한다.
TURN_ENABLED = os.environ.get("TURN_ENABLED", "0") == "1"
TURN_HOST = os.environ.get("TURN_HOST", "")
TURN_SECRET = os.environ.get("TURN_SECRET", "")
TURN_CRED_TTL_SEC = 3600


def init(get_db_connection_fn):
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


def _get_conn():
    if _get_db_connection is None:
        raise RuntimeError("call_signal not initialized")
    return _get_db_connection()


class InviteBody(BaseModel):
    caller_id: str = Field(..., max_length=50)
    callee_id: str = Field(..., max_length=50)
    sdp: str = Field(..., max_length=100_000, description="offer SDP (gathering 완료본)")


class AnswerBody(BaseModel):
    call_id: int
    user_id: str = Field(..., max_length=50)
    sdp: str = Field(..., max_length=100_000, description="answer SDP")


class CallActionBody(BaseModel):
    call_id: int
    user_id: str = Field(..., max_length=50)


def _expire_stale(cur, region: str):
    """오래된 ringing 을 missed 로 정리 — 폴링 조회 시마다 수행."""
    cur.execute(
        "UPDATE tb_call_session SET status='missed', ended_at=now() "
        "WHERE region=%s AND status='ringing' "
        "  AND created_at < now() - make_interval(secs => %s)",
        (region, RING_TIMEOUT_SEC),
    )


def _row(cur):
    r = cur.fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="통화 세션이 없습니다")
    return r


@router.post("/invite")
def invite(body: InviteBody, region: str = "R01"):
    """발신 — 상대가 이미 내게 걸어온 ringing 이 있으면 중복 발신 차단."""
    if body.caller_id == body.callee_id:
        raise HTTPException(status_code=400, detail="본인에게는 전화할 수 없습니다")
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            _expire_stale(cur, region)
            cur.execute(
                "SELECT call_id FROM tb_call_session "
                "WHERE region=%s AND status IN ('ringing','accepted') "
                "  AND ((caller_id=%s AND callee_id=%s) OR (caller_id=%s AND callee_id=%s))",
                (region, body.caller_id, body.callee_id, body.callee_id, body.caller_id),
            )
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="이미 진행 중인 통화가 있습니다")
            cur.execute(
                "INSERT INTO tb_call_session (region, caller_id, callee_id, offer_sdp) "
                "VALUES (%s, %s, %s, %s) RETURNING call_id",
                (region, body.caller_id, body.callee_id, body.sdp),
            )
            call_id = cur.fetchone()[0]
        conn.commit()
        logger.info("call invite: %s -> %s (call_id=%s)", body.caller_id, body.callee_id, call_id)
        return {"status": "OK", "call_id": call_id}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.error("call invite error: %s", e)
        raise HTTPException(status_code=500, detail="발신 실패")
    finally:
        conn.close()


@router.get("/incoming")
def incoming(user_id: str, region: str = "R01"):
    """수신 폴링 — 내게 걸려온 ringing (발신자명 포함)."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            _expire_stale(cur, region)
            cur.execute(
                "SELECT c.call_id, c.caller_id, COALESCE(u.user_nm, c.caller_id), "
                "       c.offer_sdp, c.created_at "
                "FROM tb_call_session c "
                "LEFT JOIN tb_user u ON u.region=c.region AND u.user_id=c.caller_id "
                "WHERE c.region=%s AND c.callee_id=%s AND c.status='ringing' "
                "ORDER BY c.created_at DESC LIMIT 1",
                (region, user_id),
            )
            r = cur.fetchone()
        conn.commit()  # _expire_stale 반영
        if not r:
            return {"status": "OK", "call": None}
        return {
            "status": "OK",
            "call": {
                "call_id": r[0],
                "caller_id": r[1],
                "caller_nm": r[2],
                "offer_sdp": r[3],
                "created_at": r[4].isoformat(),
            },
        }
    except Exception as e:
        conn.rollback()
        logger.error("call incoming error: %s", e)
        raise HTTPException(status_code=500, detail="수신 조회 실패")
    finally:
        conn.close()


@router.post("/answer")
def answer(body: AnswerBody, region: str = "R01"):
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tb_call_session SET status='accepted', answer_sdp=%s, answered_at=now() "
                "WHERE region=%s AND call_id=%s AND callee_id=%s AND status='ringing' "
                "RETURNING call_id",
                (body.sdp, region, body.call_id, body.user_id),
            )
            _row(cur)
        conn.commit()
        return {"status": "OK"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.error("call answer error: %s", e)
        raise HTTPException(status_code=500, detail="응답 실패")
    finally:
        conn.close()


@router.post("/reject")
def reject(body: CallActionBody, region: str = "R01"):
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tb_call_session SET status='rejected', ended_at=now(), end_by=%s "
                "WHERE region=%s AND call_id=%s AND callee_id=%s AND status='ringing' "
                "RETURNING call_id",
                (body.user_id, region, body.call_id, body.user_id),
            )
            _row(cur)
        conn.commit()
        return {"status": "OK"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.error("call reject error: %s", e)
        raise HTTPException(status_code=500, detail="거절 실패")
    finally:
        conn.close()


@router.post("/cancel")
def cancel(body: CallActionBody, region: str = "R01"):
    """발신 취소 — 응답 전 caller 가 끊음."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tb_call_session SET status='canceled', ended_at=now(), end_by=%s "
                "WHERE region=%s AND call_id=%s AND caller_id=%s AND status='ringing' "
                "RETURNING call_id",
                (body.user_id, region, body.call_id, body.user_id),
            )
            _row(cur)
        conn.commit()
        return {"status": "OK"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.error("call cancel error: %s", e)
        raise HTTPException(status_code=500, detail="취소 실패")
    finally:
        conn.close()


@router.post("/end")
def end_call(body: CallActionBody, region: str = "R01"):
    """통화 종료 — 양쪽 누구든 가능."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tb_call_session SET status='ended', ended_at=now(), end_by=%s "
                "WHERE region=%s AND call_id=%s AND status='accepted' "
                "  AND (caller_id=%s OR callee_id=%s) RETURNING call_id",
                (body.user_id, region, body.call_id, body.user_id, body.user_id),
            )
            _row(cur)
        conn.commit()
        return {"status": "OK"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.error("call end error: %s", e)
        raise HTTPException(status_code=500, detail="종료 실패")
    finally:
        conn.close()


@router.get("/status")
def call_status(call_id: int, user_id: str, region: str = "R01"):
    """상태 폴링 — caller 는 answer_sdp 수신, 양쪽은 종료 감지."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            _expire_stale(cur, region)
            cur.execute(
                "SELECT status, answer_sdp, caller_id, callee_id "
                "FROM tb_call_session WHERE region=%s AND call_id=%s",
                (region, call_id),
            )
            r = _row(cur)
        conn.commit()
        if user_id not in (r[2], r[3]):
            raise HTTPException(status_code=403, detail="통화 당사자가 아닙니다")
        return {
            "status": "OK",
            "call_status": r[0],
            # answer_sdp 는 caller 에게만 의미 — callee 응답 후 1회성 소비
            "answer_sdp": r[1] if user_id == r[2] else None,
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.error("call status error: %s", e)
        raise HTTPException(status_code=500, detail="상태 조회 실패")
    finally:
        conn.close()


@router.get("/turn-credentials")
def turn_credentials(user_id: str):
    """외부망 통화용 시간제한 TURN 자격증명 (coturn use-auth-secret 규격).

    비활성(기본) 시 enabled=false — 프런트는 LAN P2P(host only)로 동작.
    username = 만료 epoch + user, credential = base64(HMAC-SHA1(secret, username)).
    """
    if not TURN_ENABLED or not TURN_HOST or not TURN_SECRET:
        return {"enabled": False}
    # 사이트 설정 토글 (관리 > 사이트 설정) — 인프라(env)와 AND 조건
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT use_yn FROM tb_comm_code "
                "WHERE region='R01' AND grp_cd='SITE_SETTING' AND comm_cd='CALL_TURN_ENABLED'",
            )
            row = cur.fetchone()
            if row and row[0] != "Y":
                return {"enabled": False}
    except Exception as e:
        logger.error("turn setting check error: %s", e)
    finally:
        conn.close()
    username = f"{int(time.time()) + TURN_CRED_TTL_SEC}:{user_id}"
    digest = hmac.new(TURN_SECRET.encode(), username.encode(), hashlib.sha1).digest()
    return {
        "enabled": True,
        "urls": [f"turn:{TURN_HOST}:3478?transport=udp", f"turn:{TURN_HOST}:3478?transport=tcp"],
        "username": username,
        "credential": base64.b64encode(digest).decode(),
        "ttl": TURN_CRED_TTL_SEC,
    }
