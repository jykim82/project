"""EPANET API — 데이터 품질 게이트 (data-quality) — 메뉴별 요구 데이터 검사."""

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



from .common import router
from .menu_settings import _menus_disabled

logger = logging.getLogger(__name__)

# ===========================================================================
# 0) GET /admin/epanet/data-quality — 메뉴별 데이터 품질 게이트
# ===========================================================================

# 메뉴별 필수·권장 데이터 매트릭스 (사양: docs/epanet-menu-spec.md §2.2)
_MENU_REQUIREMENTS = {
    "gis-flow":               {"required": ["HAS_PIPE_NETWORK"],
                               "recommended": ["HAS_ELEVATION", "HAS_DEMAND_PROFILE"]},
    "leak-suspicious":        {"required": ["HAS_PIPE_NETWORK", "HAS_METER_MAPPING"],
                               "recommended": ["HAS_ELEVATION", "HAS_DEMAND_PROFILE"]},
    "headloss-anomaly":       {"required": ["HAS_PIPE_NETWORK"],
                               "recommended": ["HAS_ELEVATION", "HAS_DEMAND_PROFILE",
                                               "HAS_METER_MAPPING"]},
    "valve-impact":           {"required": ["HAS_PIPE_NETWORK", "HAS_VALVE_DATA"],
                               "recommended": ["HAS_ELEVATION", "HAS_DEMAND_PROFILE"]},
    "pipe-break":             {"required": ["HAS_PIPE_NETWORK"],
                               "recommended": ["HAS_ELEVATION", "HAS_DEMAND_PROFILE"]},
    "pump-control":           {"required": ["HAS_PIPE_NETWORK", "HAS_PUMP_DATA"],
                               "recommended": ["HAS_ELEVATION", "HAS_DEMAND_PROFILE",
                                               "HAS_TIME_PATTERN"]},
    "scenario-diff":          {"required": ["HAS_PIPE_NETWORK"],
                               "recommended": ["HAS_ELEVATION", "HAS_DEMAND_PROFILE"]},
    "replacement-candidates": {"required": ["HAS_PIPE_NETWORK"],
                               "recommended": ["HAS_ELEVATION", "HAS_DEMAND_PROFILE"]},
    "network-aging":          {"required": ["HAS_PIPE_NETWORK", "HAS_METER_MAPPING"],
                               "recommended": []},
    "water-quality":          {"required": ["HAS_PIPE_NETWORK", "HAS_WATER_QUALITY_MODEL"],
                               "recommended": []},
    "flow-deviation":         {"required": ["HAS_PIPE_NETWORK", "HAS_LIVE_FLOW"],
                               "recommended": ["HAS_DEMAND_PROFILE"]},
    # 운영자 토글 전용 (데이터 품질 게이트 없음, 단순 시각화 노출 제어)
    "gis-flow-arrow":         {"required": [], "recommended": []},
    # /admin/epanet 관리 페이지 — 마스터 OFF 시 함께 hide (사용자 요구 2026-06-08)
    # 복구 경로: /admin/site-settings 의 관망수리분석 토글 (항상 노출)
    "epanet-admin":           {"required": [], "recommended": []},
}


def _check_data_quality(region: str) -> dict:
    """8 항목 데이터 품질 체크. 각 항목 ok=bool + detail=설명."""
    checks: dict = {}
    conn = get_db()
    try:
        cur = conn.cursor()
        # 1) HAS_PIPE_NETWORK
        cur.execute(
            "SELECT COUNT(*), MAX(link_count) FROM tb_epanet_artifact "
            "WHERE region = %s AND status = 'success'",
            (region,),
        )
        row = cur.fetchone()
        if row and row[0] > 0:
            checks["HAS_PIPE_NETWORK"] = {
                "ok": True,
                "detail": f"성공 산출물 {row[0]}건, 최대 {row[1]} links",
            }
        else:
            checks["HAS_PIPE_NETWORK"] = {
                "ok": False,
                "detail": "/admin/epanet 에서 INP 생성 필요",
            }

        # 가장 최근 시뮬에서 추가 항목 추출
        cur.execute(
            "SELECT result_data FROM tb_epanet_simulation_result "
            "WHERE region = %s AND status = 'success' "
            "ORDER BY created_at DESC LIMIT 1",
            (region,),
        )
        sim_row = cur.fetchone()
        cur.close()
    finally:
        conn.close()

    # 2~5) 시뮬 결과 기반 항목 (RESERVOIR_HEAD / ELEVATION / DEMAND / PATTERN)
    if not sim_row or not sim_row[0]:
        checks["HAS_RESERVOIR_HEAD"] = {"ok": False, "detail": "시뮬 결과 없음"}
        checks["HAS_ELEVATION"]      = {"ok": False, "detail": "시뮬 결과 없음"}
        checks["HAS_DEMAND_PROFILE"] = {"ok": False, "detail": "시뮬 결과 없음"}
    else:
        rd = sim_row[0]
        # head 다양성
        reservoirs = rd.get("reservoirs") or []
        heads = [r.get("head_m") for r in reservoirs if r.get("head_m") is not None]
        if heads and len(set(heads)) > 1:
            checks["HAS_RESERVOIR_HEAD"] = {
                "ok": True,
                "detail": f"{len(heads)} reservoir, head 분포 {min(heads):.1f}~{max(heads):.1f}m",
            }
        else:
            checks["HAS_RESERVOIR_HEAD"] = {
                "ok": False,
                "detail": "모든 reservoir head 동일 (default 가능성)",
            }

        # elevation — 시뮬 결과의 elevation_m 우선, 없으면 head_m - pressure_m 추정
        junctions = rd.get("junctions") or []
        elevs = []
        for j in junctions:
            ev = j.get("elevation_m")
            if ev is not None:
                elevs.append(float(ev))
            else:
                h = j.get("head_m")
                p = j.get("pressure_m")
                if h is not None and p is not None:
                    elevs.append(h - p)
        if elevs:
            stddev = (sum((e - sum(elevs) / len(elevs)) ** 2 for e in elevs) / len(elevs)) ** 0.5
            if stddev > 0.5:
                checks["HAS_ELEVATION"] = {
                    "ok": True,
                    "detail": f"표고 분포 {min(elevs):.1f}~{max(elevs):.1f}m (σ={stddev:.2f})",
                }
            else:
                checks["HAS_ELEVATION"] = {
                    "ok": False,
                    "detail": "모든 junction 표고 동일 (default 0m)",
                }
        else:
            checks["HAS_ELEVATION"] = {"ok": False, "detail": "표고 데이터 없음"}

        # demand
        demands = [j.get("demand_lps") for j in junctions if j.get("demand_lps") is not None]
        if demands and len(set(d for d in demands if d > 0)) > 1:
            checks["HAS_DEMAND_PROFILE"] = {
                "ok": True,
                "detail": f"수요 분포 {min(demands):.2f}~{max(demands):.2f} LPS",
            }
        else:
            checks["HAS_DEMAND_PROFILE"] = {
                "ok": False,
                "detail": "균등 demand (default 0.1 LPS) — 노드별 차이 없음",
            }

    # 6) HAS_TIME_PATTERN — 별도 테이블 미구현 (Phase 3.4 예정)
    checks["HAS_TIME_PATTERN"] = {
        "ok": False,
        "detail": "tb_epanet_time_pattern 미구현 (Phase 3.4)",
    }
    # 7) HAS_METER_MAPPING — Phase 3.3a tb_epanet_meter_map 카운트
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM tb_epanet_meter_map WHERE region = %s",
            (region,),
        )
        meter_count = cur.fetchone()[0] or 0
        cur.close()
        conn.close()
    except Exception:
        meter_count = 0
    if meter_count > 0:
        checks["HAS_METER_MAPPING"] = {
            "ok": True,
            "detail": f"센서 매핑 {meter_count}건",
        }
    else:
        checks["HAS_METER_MAPPING"] = {
            "ok": False,
            "detail": "tb_epanet_meter_map 비어있음 — /admin/epanet 에서 매핑 추가",
        }
    # 7b) HAS_LIVE_FLOW — B-1 시설 유량 매핑 (≥5 충족)
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM tb_epanet_facility_flow_map "
            "WHERE region = %s AND enabled = 'Y'",
            (region,),
        )
        live_count = cur.fetchone()[0] or 0
        cur.close()
        conn.close()
    except Exception:
        live_count = 0
    if live_count >= 5:
        checks["HAS_LIVE_FLOW"] = {
            "ok": True,
            "detail": f"실측 유량 매핑 {live_count}건",
        }
    else:
        checks["HAS_LIVE_FLOW"] = {
            "ok": False,
            "detail": f"실측 유량 매핑 {live_count}건 (5건 이상 필요)",
        }
    # 8) HAS_VALVE_DATA — INP 의 [VALVES] 섹션 검사 (가장 최근 artifact)
    # 9) HAS_PUMP_DATA — INP 의 [PUMPS] 섹션 검사
    valve_ok = False
    pump_ok = False
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT file_path FROM tb_epanet_artifact "
            "WHERE region = %s AND status = 'success' "
            "ORDER BY created_at DESC LIMIT 1",
            (region,),
        )
        r = cur.fetchone()
        cur.close()
        conn.close()
        if r and r[0] and os.path.exists(r[0]):
            text = open(r[0], "r", encoding="utf-8", errors="ignore").read()
            valve_ok = _section_has_data(text, "[VALVES]")
            pump_ok = _section_has_data(text, "[PUMPS]")
    except Exception:
        pass
    # 합성 자동 fallback — 실 SHP/모델 없어도 송수관 위 가상 데이터로 분석 가능
    checks["HAS_VALVE_DATA"] = {
        "ok": True,
        "detail": "INP [VALVES] 섹션에 데이터" if valve_ok else "합성 (송수관 임의 5 link 가상 밸브로 사용)",
    }
    checks["HAS_PUMP_DATA"] = {
        "ok": True,
        "detail": "INP [PUMPS] 섹션에 데이터" if pump_ok else "합성 (배수지↔junction 가상 펌프 1대)",
    }
    checks["HAS_WATER_QUALITY_MODEL"] = {
        "ok": True,
        "detail": "합성 (잔류염소 초기 0.5 mg/L, 1차 반응)",
    }

    # 운영자가 명시적으로 비활성화한 메뉴 (개별 토글)
    disabled = _menus_disabled(region)

    # 마스터 OFF (SITE_SETTING.EPANET_ENABLED='N') 시 모든 EPANET 메뉴를
    # disabled 로 처리 (feature-sku-spec.md §3). 2026-06-09 정책 변경:
    # gis-flow-arrow (물흐름 표시) 도 EPANET 시뮬 결과 의존이라 함께 disable.
    # (epanet-menu-spec.md 2026-05-10 "마스터 독립" 정책 폐지)
    if not is_enabled(region):
        for key in _MENU_REQUIREMENTS.keys():
            disabled.add(key)

    # 메뉴별 ready/warning/blocked/disabled 분류
    menus_ready: list = []
    menus_warning: list = []
    menus_blocked: list = []
    menus_disabled_list: list = []
    for menu_key, req in _MENU_REQUIREMENTS.items():
        if menu_key in disabled:
            menus_disabled_list.append(menu_key)
            continue
        required_ok = all(checks.get(k, {}).get("ok", False) for k in req["required"])
        recommended_ok = all(checks.get(k, {}).get("ok", False) for k in req["recommended"])
        if not required_ok:
            menus_blocked.append(menu_key)
        elif not recommended_ok:
            menus_warning.append(menu_key)
        else:
            menus_ready.append(menu_key)

    return {
        "checks": checks,
        "menus_ready": menus_ready,
        "menus_warning": menus_warning,
        "menus_blocked": menus_blocked,
        "menus_disabled": menus_disabled_list,
    }


def _section_has_data(inp_text: str, section_name: str) -> bool:
    """INP 의 특정 섹션이 ; 외에 실제 데이터 행을 가지는지."""
    try:
        idx = inp_text.index(section_name)
    except ValueError:
        return False
    # 다음 섹션까지 스캔
    rest = inp_text[idx + len(section_name):]
    next_section = rest.find("\n[")
    block = rest[:next_section] if next_section >= 0 else rest
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        return True
    return False


@router.get("/data-quality")
def get_data_quality(region: str = "R01") -> dict:
    """8 항목 데이터 품질 + 메뉴별 ready/warning/blocked.

    토글 OFF 여도 항상 200 응답 (사이드바가 비활성 상태도 표시).
    """
    return _check_data_quality(region)


