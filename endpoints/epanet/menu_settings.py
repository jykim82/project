"""EPANET API — EPANET 활용 메뉴 on/off 설정 (menu-settings)."""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from epanet import is_enabled, is_wntr_available, get_db
from epanet.shp_reader import scan_shp
from epanet.inp_converter import convert_pipes_to_inp, validate_with_wntr
from epanet.simulator import run_steady_state, run_what_if



from .common import _ensure_enabled, _get_user_id, router

logger = logging.getLogger(__name__)

# ===========================================================================
# E_MENU) 메뉴 활성/비활성 토글 (Phase 3.3 후속)
# ===========================================================================

class MenuSettingIn(BaseModel):
    region: str = "R01"
    menu_key: str
    enabled: bool


@router.get("/menu-settings")
def list_menu_settings(region: str = "R01") -> dict:
    """region 의 EPANET 표현 메뉴별 활성/비활성 상태."""
    _ensure_enabled(region)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT menu_key, label, enabled, updated_at, updated_by
              FROM tb_epanet_menu_setting
             WHERE region = %s
             ORDER BY menu_key
            """,
            (region,),
        )
        rows = cur.fetchall()
        cur.close()
        items = [{
            "menu_key": r[0],
            "label": r[1],
            "enabled": (r[2] == "Y"),
            "updated_at": r[3].isoformat() if r[3] else None,
            "updated_by": r[4],
        } for r in rows]
        return {"items": items}
    finally:
        conn.close()


class MenuBulkIn(BaseModel):
    region: str = "R01"
    enabled: bool


@router.put("/menu-settings/bulk")
def update_menu_settings_bulk(req: MenuBulkIn, request: Request) -> dict:
    """region 의 모든 EPANET 메뉴를 일괄 ON/OFF (마스터 스위치)."""
    _ensure_enabled(req.region)
    user_id = _get_user_id(request)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE tb_epanet_menu_setting
               SET enabled = %s, updated_at = NOW(), updated_by = %s
             WHERE region = %s
            """,
            ("Y" if req.enabled else "N", user_id, req.region),
        )
        rc = cur.rowcount
        conn.commit()
        cur.close()
        return {"status": "OK", "updated_count": rc, "enabled": req.enabled}
    finally:
        conn.close()


@router.put("/menu-settings")
def update_menu_setting(req: MenuSettingIn, request: Request) -> dict:
    """단건 토글 변경. enabled 'Y'/'N' 으로 UPSERT."""
    _ensure_enabled(req.region)
    user_id = _get_user_id(request)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE tb_epanet_menu_setting
               SET enabled = %s, updated_at = NOW(), updated_by = %s
             WHERE region = %s AND menu_key = %s
            """,
            ("Y" if req.enabled else "N", user_id, req.region, req.menu_key),
        )
        rc = cur.rowcount
        conn.commit()
        cur.close()
        if rc == 0:
            raise HTTPException(404, detail=f"menu_key 없음: {req.menu_key}")
        return {"status": "OK", "menu_key": req.menu_key, "enabled": req.enabled}
    finally:
        conn.close()


def _menus_disabled(region: str) -> set:
    """enabled='N' 인 메뉴 키 집합."""
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT menu_key FROM tb_epanet_menu_setting "
            "WHERE region = %s AND enabled = 'N'",
            (region,),
        )
        rows = cur.fetchall()
        cur.close()
        return {r[0] for r in rows}
    except Exception:
        return set()
    finally:
        conn.close()


