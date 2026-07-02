"""
endpoints/audit.py — 감사 로그(audit) 공용 헬퍼

시설·설비 CRUD 변경 이력을 tb_audit_log 에 기록한다 (Migration 0094).
사양: docs/gis-facility-menu-spec.md §5.3.

설계 원칙:
- actor 추출은 **비파괴**: 토큰이 없거나 무효여도 절대 401 하지 않고 'unknown' 반환
  (기존 무인증 CRUD 호출 동작을 깨지 않기 위함).
- write_audit 는 예외를 삼킨다: 감사 기록 실패가 본 작업(CRUD)을 실패시키지 않는다.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from endpoints.auth_crud import _decode_token  # 동일 JWT 시크릿/알고리즘 재사용

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)


def get_actor(credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)) -> dict:
    """감사용 actor 추출. 토큰 없거나 무효면 unknown (비파괴 — 401 없음)."""
    if credentials is None:
        return {"user_id": "unknown", "region": None, "auth_idn": None}
    try:
        p = _decode_token(credentials.credentials)
        return {
            "user_id": p.get("sub", "unknown"),
            "region": p.get("region"),
            "auth_idn": p.get("auth_idn"),
        }
    except Exception:
        return {"user_id": "unknown", "region": None, "auth_idn": None}


def write_audit(
    conn,
    *,
    actor: dict,
    action: str,          # create / update / delete
    target_type: str,     # 'equipment' / 'reservoir' ...
    target_key: str,      # equipment_id / sitename
    summary: Optional[str] = None,
    detail: Optional[dict] = None,
    request: Optional[Request] = None,
) -> None:
    """tb_audit_log 에 1행 기록. 본 작업 커밋 이후 별도 트랜잭션으로 실행.

    실패해도 예외를 삼켜 CRUD 결과에 영향을 주지 않는다.
    """
    try:
        client_ip = None
        if request is not None:
            fwd = request.headers.get("x-forwarded-for")
            client_ip = fwd.split(",")[0].strip() if fwd else (
                request.client.host if request.client else None
            )
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tb_audit_log
                    (region, actor, action, target_type, target_key, summary, detail, client_ip)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    actor.get("region"),
                    actor.get("user_id", "unknown"),
                    action,
                    target_type,
                    str(target_key),
                    summary,
                    json.dumps(detail, ensure_ascii=False) if detail is not None else None,
                    client_ip,
                ),
            )
        conn.commit()
    except Exception as e:
        logger.warning(f"감사 로그 기록 실패(무시): {e}")
        try:
            conn.rollback()
        except Exception:
            pass
