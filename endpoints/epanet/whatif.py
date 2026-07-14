"""EPANET API — What-if 시나리오: 밸브 차단/관 파단/펌프 제어/시나리오 비교."""

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



from .common import _baseline_pressure_map, _ensure_enabled, _latest_artifact_inp, _latest_sim_data, router

logger = logging.getLogger(__name__)

# ===========================================================================
# E6) 차단밸브 영향범위 / 관로 파손 + 우회 (Phase 4-1, 4-2)
# 합성 자동 fallback — 송수관 임의 K개 link 가상 밸브로 사용
# ===========================================================================

def _synthetic_valve_pipes(sim_data: dict, n: int = 5) -> list:
    """대표 가상 밸브 K개 — 첫 N개 큰 |flow| pipe."""
    pipes = sim_data.get("result_data", {}).get("pipes") or []
    sorted_p = sorted(pipes, key=lambda p: -abs(p.get("flow_lps") or 0))
    return [p["id"] for p in sorted_p[:n]]


@router.get("/synthetic-valves")
def get_synthetic_valves(region: str = "R01", n: int = 5) -> dict:
    """합성 가상 밸브 목록 (분석용)."""
    _ensure_enabled(region)
    sim = _latest_sim_data(region)
    if not sim:
        return {"items": [], "warning": "성공한 시뮬이 없습니다."}
    pipes = sim["result_data"].get("pipes") or []
    sorted_p = sorted(pipes, key=lambda p: -abs(p.get("flow_lps") or 0))
    items = [{
        "valve_id": f"V{i+1:02d}",
        "pipe_id": p["id"],
        "flow_lps": p.get("flow_lps"),
        "label": f"가상밸브 {i+1} (pipe {p['id']})",
    } for i, p in enumerate(sorted_p[:n])]
    return {"items": items, "synthetic": True}


@router.get("/valve-impact")
def get_valve_impact(
    region: str = "R01",
    pipe_id: Optional[str] = None,
    pressure_drop_m: float = 5.0,
) -> dict:
    """선택한 가상 밸브(=pipe id) 차단 시 단수 영향 범위.

    - 미지정 시 합성 가상 밸브 첫 번째 사용
    - 영향 받음 = baseline pressure - what-if pressure > pressure_drop_m
    """
    _ensure_enabled(region)
    sim = _latest_sim_data(region)
    if not sim:
        return {"warning": "성공한 시뮬이 없습니다.", "impacted": [], "valve_pipe_id": None}
    inp = _latest_artifact_inp(region)
    if not inp:
        return {"warning": "INP 파일이 없습니다.", "impacted": [], "valve_pipe_id": None}

    if not pipe_id:
        valves = _synthetic_valve_pipes(sim, n=1)
        if not valves:
            return {"warning": "분석할 pipe 가 없습니다.", "impacted": [], "valve_pipe_id": None}
        pipe_id = valves[0]

    baseline = _baseline_pressure_map(region)
    res = run_what_if(inp, remove_links=[pipe_id])
    if not res.success:
        return {"error": res.error, "impacted": [], "valve_pipe_id": pipe_id}

    impacted: list = []
    for j in res.junctions:
        nid = j["id"]
        b = baseline.get(nid)
        a = j.get("pressure_m")
        if b is None or a is None:
            continue
        drop = float(b) - float(a)
        if drop > pressure_drop_m:
            impacted.append({
                "id": nid,
                "lng": j.get("lng"), "lat": j.get("lat"),
                "baseline_m": round(float(b), 2),
                "after_m": round(float(a), 2),
                "drop_m": round(drop, 2),
            })
    impacted.sort(key=lambda i: -i["drop_m"])
    return {
        "valve_pipe_id": pipe_id,
        "synthetic": True,
        "pressure_drop_m": pressure_drop_m,
        "impacted_count": len(impacted),
        "total_nodes": len(res.junctions),
        "impacted": impacted[:50],
        "duration_ms": res.duration_ms,
    }


@router.get("/pipe-break")
def get_pipe_break(
    region: str = "R01",
    pipe_id: Optional[str] = None,
    pressure_drop_m: float = 5.0,
    flow_change_lps: float = 1.0,
) -> dict:
    """관로 파손 시뮬 + 우회 경로 분석.

    - pipe_id 없으면 가장 큰 |flow| pipe 자동 선택
    - 영향: baseline - after > pressure_drop_m
    - 우회: |flow_after - flow_before| > flow_change_lps
    """
    _ensure_enabled(region)
    sim = _latest_sim_data(region)
    if not sim:
        return {"warning": "성공한 시뮬이 없습니다.", "impacted": [], "rerouted": []}
    inp = _latest_artifact_inp(region)
    if not inp:
        return {"warning": "INP 파일이 없습니다.", "impacted": [], "rerouted": []}

    if not pipe_id:
        pipes_b = sim["result_data"].get("pipes") or []
        if not pipes_b:
            return {"warning": "분석할 pipe 가 없습니다.", "impacted": [], "rerouted": []}
        pipes_b_sorted = sorted(pipes_b, key=lambda p: -abs(p.get("flow_lps") or 0))
        pipe_id = pipes_b_sorted[0]["id"]

    baseline_p = _baseline_pressure_map(region)
    baseline_f = {p["id"]: p.get("flow_lps") or 0
                  for p in (sim["result_data"].get("pipes") or [])}
    res = run_what_if(inp, remove_links=[pipe_id])
    if not res.success:
        return {"error": res.error, "impacted": [], "rerouted": []}

    impacted: list = []
    for j in res.junctions:
        b = baseline_p.get(j["id"])
        a = j.get("pressure_m")
        if b is None or a is None:
            continue
        drop = float(b) - float(a)
        if drop > pressure_drop_m:
            impacted.append({
                "id": j["id"], "lng": j.get("lng"), "lat": j.get("lat"),
                "baseline_m": round(float(b), 2),
                "after_m": round(float(a), 2),
                "drop_m": round(drop, 2),
            })
    impacted.sort(key=lambda i: -i["drop_m"])

    rerouted: list = []
    for p in res.pipes:
        if p["id"] == pipe_id:
            continue
        bf = baseline_f.get(p["id"])
        af = p.get("flow_lps")
        if bf is None or af is None:
            continue
        delta = abs(float(af) - float(bf))
        if delta > flow_change_lps:
            rerouted.append({
                "id": p["id"],
                "before_lps": round(float(bf), 3),
                "after_lps": round(float(af), 3),
                "delta_lps": round(delta, 3),
            })
    rerouted.sort(key=lambda p: -p["delta_lps"])
    return {
        "broken_pipe_id": pipe_id,
        "pressure_drop_m": pressure_drop_m,
        "flow_change_lps": flow_change_lps,
        "impacted_count": len(impacted),
        "rerouted_count": len(rerouted),
        "total_nodes": len(res.junctions),
        "impacted": impacted[:50],
        "rerouted": rerouted[:50],
        "duration_ms": res.duration_ms,
    }


# ===========================================================================
# E7) 펌프 가동 변경 (Phase 4-3) — 합성 펌프 = reservoir head boost
# ===========================================================================

@router.get("/pump-control")
def get_pump_control(
    region: str = "R01",
    head_boost_m: float = 10.0,
) -> dict:
    """가상 펌프 가동 — 모든 reservoir head + boost 후 압력 변화.

    합성 펌프이므로 ON/OFF 가 아니라 head_boost_m 슬라이더로 강도 조절.
    """
    _ensure_enabled(region)
    inp = _latest_artifact_inp(region)
    if not inp:
        return {"warning": "INP 파일이 없습니다.", "items": []}

    baseline = _baseline_pressure_map(region)
    res = run_what_if(inp, add_pump_boost=head_boost_m)
    if not res.success:
        return {"error": res.error, "items": []}

    diffs: list = []
    for j in res.junctions:
        b = baseline.get(j["id"])
        a = j.get("pressure_m")
        if b is None or a is None:
            continue
        delta = float(a) - float(b)
        diffs.append({
            "id": j["id"], "lng": j.get("lng"), "lat": j.get("lat"),
            "baseline_m": round(float(b), 2),
            "after_m": round(float(a), 2),
            "delta_m": round(delta, 2),
        })
    diffs.sort(key=lambda d: -abs(d["delta_m"]))
    return {
        "synthetic": True,
        "head_boost_m": head_boost_m,
        "total_nodes": len(diffs),
        "items": diffs[:50],
        "min_delta_m": min((d["delta_m"] for d in diffs), default=0),
        "max_delta_m": max((d["delta_m"] for d in diffs), default=0),
        "avg_delta_m": (sum(d["delta_m"] for d in diffs) / len(diffs)) if diffs else 0,
        "duration_ms": res.duration_ms,
    }


# ===========================================================================
# E8) 시나리오 비교 (Phase 4-4) — 두 sim_id diff
# ===========================================================================

@router.get("/scenario-diff")
def get_scenario_diff(
    region: str = "R01",
    sim_a: Optional[int] = None,
    sim_b: Optional[int] = None,
) -> dict:
    """두 sim_id 의 노드 압력 / 파이프 유량 차이 (B - A)."""
    _ensure_enabled(region)
    conn = get_db()
    try:
        cur = conn.cursor()
        if sim_a is None or sim_b is None:
            cur.execute(
                "SELECT sim_id, result_data FROM tb_epanet_simulation_result "
                "WHERE region = %s AND status = 'success' "
                "ORDER BY created_at DESC LIMIT 2",
                (region,),
            )
            rows = cur.fetchall()
            if len(rows) < 2:
                cur.close()
                return {"warning": "비교할 시뮬이 2개 이상 필요합니다."}
            sim_b_id, b_data = rows[0]
            sim_a_id, a_data = rows[1]
        else:
            cur.execute(
                "SELECT sim_id, result_data FROM tb_epanet_simulation_result "
                "WHERE sim_id = ANY(%s) AND region = %s",
                ([sim_a, sim_b], region),
            )
            rows = cur.fetchall()
            if len(rows) != 2:
                cur.close()
                return {"warning": "지정한 sim_id 를 찾을 수 없습니다."}
            d = {r[0]: r[1] for r in rows}
            sim_a_id, sim_b_id = sim_a, sim_b
            a_data = d[sim_a]; b_data = d[sim_b]
        cur.close()
    finally:
        conn.close()

    a_p = {j["id"]: j.get("pressure_m") for j in (a_data.get("junctions") or [])}
    b_p = {j["id"]: j.get("pressure_m") for j in (b_data.get("junctions") or [])}
    nodes: list = []
    for nid, av in a_p.items():
        bv = b_p.get(nid)
        if av is None or bv is None:
            continue
        nodes.append({"id": nid, "a_m": round(float(av), 2),
                      "b_m": round(float(bv), 2),
                      "delta_m": round(float(bv) - float(av), 2)})
    nodes.sort(key=lambda n: -abs(n["delta_m"]))
    a_f = {p["id"]: p.get("flow_lps") for p in (a_data.get("pipes") or [])}
    b_f = {p["id"]: p.get("flow_lps") for p in (b_data.get("pipes") or [])}
    pipes: list = []
    for pid, av in a_f.items():
        bv = b_f.get(pid)
        if av is None or bv is None:
            continue
        pipes.append({"id": pid, "a_lps": round(float(av), 3),
                      "b_lps": round(float(bv), 3),
                      "delta_lps": round(float(bv) - float(av), 3)})
    pipes.sort(key=lambda p: -abs(p["delta_lps"]))
    return {
        "sim_a": sim_a_id, "sim_b": sim_b_id,
        "node_count": len(nodes), "pipe_count": len(pipes),
        "top_nodes": nodes[:20],
        "top_pipes": pipes[:20],
    }


