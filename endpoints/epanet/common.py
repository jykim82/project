"""EPANET API 공통 — router·상수·활성화 게이트·공용 헬퍼."""

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



logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/epanet", tags=["epanet"])


SHP_BASE_DIR = os.environ.get("EPANET_SHP_BASE_DIR", "/data/files/gis/shp")
INP_OUTPUT_DIR = os.environ.get(
    "EPANET_INP_OUTPUT_DIR", "/data/files/epanet"
)

PIPE_SHP_HINTS = (
    "SAA003",  # 송수관
    "SAA004",  # 배수관
)
RESERVOIR_SHP_HINT = "SA114"  # 배수지


def _ensure_enabled(region: str) -> None:
    if not is_enabled(region):
        raise HTTPException(
            status_code=503,
            detail="EPANET 모듈이 비활성 상태입니다. 사이트 설정에서 활성화하세요.",
        )


def _list_shp(base_dir: str) -> list[Path]:
    if not os.path.isdir(base_dir):
        return []
    return sorted(Path(base_dir).glob("*.shp"))


def _classify_shp(paths: list[Path]) -> dict:
    """파일명으로 송수관/배수관/배수지/기타 분류."""
    pipes: list[Path] = []
    reservoirs: list[Path] = []
    others: list[Path] = []
    for p in paths:
        name = p.name
        if any(hint in name for hint in PIPE_SHP_HINTS):
            pipes.append(p)
        elif RESERVOIR_SHP_HINT in name:
            reservoirs.append(p)
        else:
            others.append(p)
    return {"pipes": pipes, "reservoirs": reservoirs, "others": others}



# 단위 → LPS 환산 계수
_UNIT_TO_LPS = {
    "lps": 1.0,
    "lpm": 1.0 / 60.0,
    "cmh": 1000.0 / 3600.0,
    "m3h": 1000.0 / 3600.0,
    "m3s": 1000.0,
}


def _to_lps(value: float, unit: str, scale: float = 1.0) -> float:
    """단위 환산. unit 미지원 시 lps 로 가정."""
    factor = _UNIT_TO_LPS.get(unit, 1.0)
    return value * scale * factor


# ===========================================================================
# E_HELPERS) 분석 공통 헬퍼
# ===========================================================================

def _latest_artifact_inp(region: str) -> Optional[str]:
    """region 의 가장 최근 success artifact 의 INP 파일 경로."""
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT file_path FROM tb_epanet_artifact "
            "WHERE region = %s AND status = 'success' "
            "ORDER BY created_at DESC LIMIT 1",
            (region,),
        )
        r = cur.fetchone()
        cur.close()
        return r[0] if r and r[0] and os.path.exists(r[0]) else None
    finally:
        conn.close()


def _latest_sim_data(region: str) -> Optional[dict]:
    """region 의 가장 최근 success 시뮬 result_data."""
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT sim_id, result_data FROM tb_epanet_simulation_result "
            "WHERE region = %s AND status = 'success' "
            "ORDER BY created_at DESC LIMIT 1",
            (region,),
        )
        r = cur.fetchone()
        cur.close()
        if not r or not r[1]:
            return None
        return {"sim_id": r[0], "result_data": r[1]}
    finally:
        conn.close()


def _baseline_pressure_map(region: str) -> dict:
    """기본 시뮬의 노드별 압력 (변경 시뮬과 비교 기준)."""
    sim = _latest_sim_data(region)
    if not sim:
        return {}
    return {j["id"]: j.get("pressure_m") for j in (sim["result_data"].get("junctions") or [])}



# ===========================================================================
# 내부 헬퍼
# ===========================================================================

def _get_user_id(request: Request) -> str:
    """JWT 미들웨어가 request.state.user 에 채운 user_id 사용 (없으면 'system')."""
    user = getattr(request.state, "user", None)
    if isinstance(user, dict):
        return user.get("user_id") or user.get("sub") or "system"
    return "system"


