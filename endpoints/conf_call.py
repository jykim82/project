"""
3~4인 회의 통화 (풀메시) — tb_conf_call / tb_conf_member / tb_conf_signal
(docs/realtime-comm-spec.md §7)

REST 폴링 시그널링. 프로토콜:
  호스트: create(초대 목록) → 즉시 joined
  피초대: incoming 폴링 → 벨 → join (응답: 기존 joined 목록 → 각자에게 offer)
  쌍별 SDP: signal 사서함 (신규 합류자가 offer, 기존 참가자가 answer)
  생존: state 폴링 = 하트비트. 30s 끊김 → left. joined ≤ 1 → 회의 종료.
"""

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger("slm")

router = APIRouter(prefix="/conf", tags=["conf-call"])

_get_db_connection = None

INVITE_TIMEOUT_SEC = 20     # 벨 무응답 → missed
MEMBER_STALE_SEC = 30       # joined 하트비트 끊김 → left
MAX_MEMBERS = 4             # 풀메시 상한 (호스트 포함)


def init(get_db_connection_fn):
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


def _get_conn():
    if _get_db_connection is None:
        raise RuntimeError("conf_call not initialized")
    return _get_db_connection()


class CreateBody(BaseModel):
    host_id: str = Field(..., max_length=50)
    member_ids: list[str] = Field(..., min_length=1, max_length=MAX_MEMBERS - 1)


class ConfActionBody(BaseModel):
    conf_id: int
    user_id: str = Field(..., max_length=50)


class SignalBody(BaseModel):
    conf_id: int
    from_user: str = Field(..., max_length=50)
    to_user: str = Field(..., max_length=50)
    kind: str = Field(..., pattern="^(offer|answer)$")
    sdp: str = Field(..., max_length=100_000)


def _expire_stale(cur, region: str):
    """유령 정리 — 벨 무응답 missed / 하트비트 끊긴 joined → left /
    joined 1명 이하 active 회의 → ended."""
    cur.execute(
        "UPDATE tb_conf_member SET status='missed' "
        "WHERE region=%s AND status='invited' "
        "  AND invited_at < now() - make_interval(secs => %s)",
        (region, INVITE_TIMEOUT_SEC),
    )
    cur.execute(
        "UPDATE tb_conf_member SET status='left', left_at=now() "
        "WHERE region=%s AND status='joined' "
        "  AND last_poll_at < now() - make_interval(secs => %s)",
        (region, MEMBER_STALE_SEC),
    )
    cur.execute(
        """
        UPDATE tb_conf_call c SET status='ended', ended_at=now()
        WHERE c.region=%s AND c.status='active'
          AND c.created_at < now() - interval '25 seconds'
          AND (SELECT count(*) FROM tb_conf_member m
               WHERE m.region=c.region AND m.conf_id=c.conf_id
                 AND m.status='joined') <= 1
          AND NOT EXISTS (SELECT 1 FROM tb_conf_member m2
               WHERE m2.region=c.region AND m2.conf_id=c.conf_id
                 AND m2.status='invited')
        """,
        (region,),
    )


def _check_member(cur, region: str, conf_id: int, user_id: str) -> str:
    cur.execute(
        "SELECT status FROM tb_conf_member "
        "WHERE region=%s AND conf_id=%s AND user_id=%s",
        (region, conf_id, user_id),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="회의 참가자가 아닙니다")
    return row[0]


@router.post("/create")
def create_conf(body: CreateBody, region: str = "R01"):
    """회의 생성 — 호스트 즉시 합류, 나머지 초대(벨)."""
    members = [m for m in dict.fromkeys(body.member_ids) if m != body.host_id]
    if not members:
        raise HTTPException(status_code=400, detail="초대할 상대를 선택하세요")
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            _expire_stale(cur, region)
            # 이미 회의 중인 호스트 차단
            cur.execute(
                "SELECT m.conf_id FROM tb_conf_member m "
                "JOIN tb_conf_call c ON c.region=m.region AND c.conf_id=m.conf_id "
                "WHERE m.region=%s AND m.user_id=%s AND m.status='joined' AND c.status='active'",
                (region, body.host_id),
            )
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="이미 진행 중인 회의가 있습니다")
            cur.execute(
                "INSERT INTO tb_conf_call (region, host_id) VALUES (%s, %s) RETURNING conf_id",
                (region, body.host_id),
            )
            conf_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO tb_conf_member (region, conf_id, user_id, status, joined_at) "
                "VALUES (%s, %s, %s, 'joined', now())",
                (region, conf_id, body.host_id),
            )
            for m in members:
                cur.execute(
                    "INSERT INTO tb_conf_member (region, conf_id, user_id) VALUES (%s, %s, %s)",
                    (region, conf_id, m),
                )
        conn.commit()
        logger.info("conf create: host=%s members=%s conf=%s", body.host_id, members, conf_id)
        return {"status": "OK", "conf_id": conf_id}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.error("conf create error: %s", e)
        raise HTTPException(status_code=500, detail="회의 생성 실패")
    finally:
        conn.close()


@router.get("/incoming")
def incoming(user_id: str, region: str = "R01"):
    """초대 벨 폴링 — 호스트명·참가 인원 포함."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            _expire_stale(cur, region)
            cur.execute(
                """
                SELECT c.conf_id, c.host_id, COALESCE(u.user_nm, c.host_id),
                       (SELECT count(*) FROM tb_conf_member m2
                        WHERE m2.region=c.region AND m2.conf_id=c.conf_id) AS total
                FROM tb_conf_member m
                JOIN tb_conf_call c ON c.region=m.region AND c.conf_id=m.conf_id
                LEFT JOIN tb_user u ON u.region=c.region AND u.user_id=c.host_id
                WHERE m.region=%s AND m.user_id=%s AND m.status='invited'
                  AND c.status='active'
                ORDER BY c.created_at DESC LIMIT 1
                """,
                (region, user_id),
            )
            r = cur.fetchone()
        conn.commit()
        if not r:
            return {"status": "OK", "conf": None}
        return {
            "status": "OK",
            "conf": {"conf_id": r[0], "host_id": r[1], "host_nm": r[2], "total": r[3]},
        }
    except Exception as e:
        conn.rollback()
        logger.error("conf incoming error: %s", e)
        raise HTTPException(status_code=500, detail="회의 수신 조회 실패")
    finally:
        conn.close()


@router.post("/join")
def join(body: ConfActionBody, region: str = "R01"):
    """합류 — 응답으로 기존 joined 목록 반환 (신규 합류자가 각자에게 offer)."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            st = _check_member(cur, region, body.conf_id, body.user_id)
            if st not in ("invited", "missed"):
                raise HTTPException(status_code=409, detail="합류할 수 없는 상태입니다")
            cur.execute(
                "UPDATE tb_conf_member SET status='joined', joined_at=now(), last_poll_at=now() "
                "WHERE region=%s AND conf_id=%s AND user_id=%s",
                (region, body.conf_id, body.user_id),
            )
            cur.execute(
                "SELECT m.user_id, COALESCE(u.user_nm, m.user_id) FROM tb_conf_member m "
                "LEFT JOIN tb_user u ON u.region=m.region AND u.user_id=m.user_id "
                "WHERE m.region=%s AND m.conf_id=%s AND m.status='joined' AND m.user_id<>%s",
                (region, body.conf_id, body.user_id),
            )
            peers = [{"user_id": r[0], "user_nm": r[1]} for r in cur.fetchall()]
        conn.commit()
        return {"status": "OK", "peers": peers}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.error("conf join error: %s", e)
        raise HTTPException(status_code=500, detail="회의 합류 실패")
    finally:
        conn.close()


@router.post("/reject")
def reject(body: ConfActionBody, region: str = "R01"):
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            _check_member(cur, region, body.conf_id, body.user_id)
            cur.execute(
                "UPDATE tb_conf_member SET status='rejected' "
                "WHERE region=%s AND conf_id=%s AND user_id=%s AND status='invited'",
                (region, body.conf_id, body.user_id),
            )
        conn.commit()
        return {"status": "OK"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.error("conf reject error: %s", e)
        raise HTTPException(status_code=500, detail="거절 실패")
    finally:
        conn.close()


@router.post("/leave")
def leave(body: ConfActionBody, region: str = "R01"):
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            _check_member(cur, region, body.conf_id, body.user_id)
            cur.execute(
                "UPDATE tb_conf_member SET status='left', left_at=now() "
                "WHERE region=%s AND conf_id=%s AND user_id=%s",
                (region, body.conf_id, body.user_id),
            )
            _expire_stale(cur, region)
        conn.commit()
        return {"status": "OK"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.error("conf leave error: %s", e)
        raise HTTPException(status_code=500, detail="나가기 실패")
    finally:
        conn.close()


@router.get("/state")
def state(conf_id: int, user_id: str, region: str = "R01"):
    """회의 상태 폴링 (=하트비트) — 참가자 목록·회의 상태."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            _check_member(cur, region, conf_id, user_id)
            cur.execute(
                "UPDATE tb_conf_member SET last_poll_at=now() "
                "WHERE region=%s AND conf_id=%s AND user_id=%s AND status='joined'",
                (region, conf_id, user_id),
            )
            _expire_stale(cur, region)
            cur.execute(
                "SELECT status FROM tb_conf_call WHERE region=%s AND conf_id=%s",
                (region, conf_id),
            )
            conf_status = (cur.fetchone() or ["ended"])[0]
            cur.execute(
                "SELECT m.user_id, COALESCE(u.user_nm, m.user_id), m.status "
                "FROM tb_conf_member m "
                "LEFT JOIN tb_user u ON u.region=m.region AND u.user_id=m.user_id "
                "WHERE m.region=%s AND m.conf_id=%s ORDER BY m.invited_at",
                (region, conf_id),
            )
            members = [
                {"user_id": r[0], "user_nm": r[1], "status": r[2]} for r in cur.fetchall()
            ]
        conn.commit()
        return {"status": "OK", "conf_status": conf_status, "members": members}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.error("conf state error: %s", e)
        raise HTTPException(status_code=500, detail="회의 상태 조회 실패")
    finally:
        conn.close()


@router.post("/signal")
def post_signal(body: SignalBody, region: str = "R01"):
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            _check_member(cur, region, body.conf_id, body.from_user)
            cur.execute(
                "INSERT INTO tb_conf_signal (region, conf_id, from_user, to_user, kind, sdp) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (region, body.conf_id, body.from_user, body.to_user, body.kind, body.sdp),
            )
        conn.commit()
        return {"status": "OK"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.error("conf signal error: %s", e)
        raise HTTPException(status_code=500, detail="시그널 전송 실패")
    finally:
        conn.close()


@router.get("/signal")
def get_signals(conf_id: int, user_id: str, region: str = "R01"):
    """내 앞으로 온 미소비 시그널 수신 (1회성 — 소비 처리)."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            _check_member(cur, region, conf_id, user_id)
            cur.execute(
                "UPDATE tb_conf_signal SET consumed_at=now() "
                "WHERE region=%s AND conf_id=%s AND to_user=%s AND consumed_at IS NULL "
                "RETURNING from_user, kind, sdp",
                (region, conf_id, user_id),
            )
            rows = cur.fetchall()
        conn.commit()
        return {
            "status": "OK",
            "signals": [{"from_user": r[0], "kind": r[1], "sdp": r[2]} for r in rows],
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.error("conf get signals error: %s", e)
        raise HTTPException(status_code=500, detail="시그널 수신 실패")
    finally:
        conn.close()
