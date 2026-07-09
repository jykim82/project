"""
endpoints/user_prefs.py — 사용자별 선호도 (테마·브랜드·레이아웃 등)

per-user 저장(tb_user.preferences jsonb). 개인 설정이 없으면 사이트 기본값
(tb_comm_code SITE_SETTING/TWEAKS_*) 으로 폴백한다.
사양: docs/tweaks-layout-spec.md (2026-07 개인별 저장), Migration 0096.

actor 는 JWT(get_actor). GET 은 미인증이면 사이트 기본값만, PUT 은 인증 필수.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Request

from endpoints.audit import get_actor

logger = logging.getLogger(__name__)

router = APIRouter()
_get_db_connection = None


def init(get_db_connection_fn):
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


_ALLOWED_BRANDS = {
    "claude-orange", "amber", "blue", "cyan", "teal",
    "emerald", "indigo", "violet", "pink", "rose",
}


def _site_default_tweaks(cur) -> dict:
    """사이트 기본 tweaks (tb_comm_code SITE_SETTING/TWEAKS_*)."""
    cur.execute(
        "SELECT comm_cd, comm_val FROM tb_comm_code "
        "WHERE region = 'R01' AND grp_cd = 'SITE_SETTING' "
        "AND comm_cd IN ('TWEAKS_BRAND_COLOR','TWEAKS_LAYOUT_MODE','TWEAKS_DEFAULT_THEME')"
    )
    d = {c: v for c, v in cur.fetchall()}
    return {
        "brand_color_id": d.get("TWEAKS_BRAND_COLOR") or "claude-orange",
        "layout_mode": d.get("TWEAKS_LAYOUT_MODE") or "sidebar",
        "default_theme": d.get("TWEAKS_DEFAULT_THEME") or "dark",
    }


def _validate_tweaks(tw: dict) -> dict:
    """허용값만 통과."""
    out = {}
    if tw.get("brand_color_id") in _ALLOWED_BRANDS:
        out["brand_color_id"] = tw["brand_color_id"]
    if tw.get("layout_mode") in {"sidebar", "topbar"}:
        out["layout_mode"] = tw["layout_mode"]
    if tw.get("default_theme") in {"light", "dark", "system"}:
        out["default_theme"] = tw["default_theme"]
    return out


@router.get("/me/tweaks")
def get_my_tweaks(actor: dict = Depends(get_actor)):
    """현재 사용자의 tweaks (개인 우선, 사이트 기본값 폴백)."""
    if _get_db_connection is None:
        return {"tweaks": {}, "source": "none"}
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        merged = _site_default_tweaks(cur)
        user_tw = {}
        uid = actor.get("user_id")
        region = actor.get("region") or "R01"
        if uid and uid != "unknown":
            cur.execute(
                "SELECT preferences->'tweaks' FROM tb_user WHERE user_id = %s AND region = %s",
                (uid, region),
            )
            row = cur.fetchone()
            if row and row[0]:
                user_tw = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        cur.close()
        # 개인값이 있으면 사이트 기본값 위에 덮어씀
        for k, v in (user_tw or {}).items():
            if v:
                merged[k] = v
        return {"tweaks": merged, "source": "user" if user_tw else "site"}
    except Exception as e:
        logger.error(f"사용자 tweaks 조회 실패: {e}")
        return {"tweaks": _fallback_tweaks(), "source": "error"}
    finally:
        if conn:
            conn.close()


@router.put("/me/tweaks")
async def put_my_tweaks(request: Request, actor: dict = Depends(get_actor)):
    """현재 사용자의 tweaks 저장 (인증 필수)."""
    uid = actor.get("user_id")
    region = actor.get("region") or "R01"
    if not uid or uid == "unknown":
        return {"status": "ERROR", "message": "인증이 필요합니다."}
    if _get_db_connection is None:
        return {"status": "ERROR", "message": "DB 미초기화"}
    conn = None
    try:
        body = await request.json()
        tw = body.get("tweaks", body)  # {tweaks:{...}} 또는 {...} 모두 허용
        valid = _validate_tweaks(tw if isinstance(tw, dict) else {})
        if not valid:
            return {"status": "OK", "updated": 0, "message": "유효한 항목 없음"}
        conn = _get_db_connection()
        cur = conn.cursor()
        # preferences->'tweaks' 에 부분 병합 (기존 값 보존)
        cur.execute(
            """
            UPDATE tb_user
            SET preferences = jsonb_set(
                    COALESCE(preferences, '{}'::jsonb),
                    '{tweaks}',
                    COALESCE(preferences->'tweaks', '{}'::jsonb) || %s::jsonb
                ),
                updated_at = now()
            WHERE user_id = %s AND region = %s
            """,
            (json.dumps(valid, ensure_ascii=False), uid, region),
        )
        conn.commit()
        updated = cur.rowcount
        cur.close()
        return {"status": "OK", "updated": updated, "tweaks": valid}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"사용자 tweaks 저장 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()


def _fallback_tweaks() -> dict:
    return {"brand_color_id": "claude-orange", "layout_mode": "sidebar", "default_theme": "dark"}
