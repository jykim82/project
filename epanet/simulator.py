"""EPANET 시뮬레이션 실행 (Phase 2 — wntr 기반).

정상상태(Steady-state) 시뮬레이션만 우선 지원. 시계열(EPS) 은 Phase 2.5.

wntr 미설치 시 ImportError 발생 — 호출 측에서 503 으로 응답.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# SHP 좌표계 (당진시 SHP 검증 결과 EPSG:5186 = Korea 2000 / Central Belt 2010)
# 다른 사이트는 환경변수로 변경 가능
SHP_CRS = os.environ.get("EPANET_SHP_CRS", "EPSG:5186")
WGS84 = "EPSG:4326"


def _get_transformer():
    """UTM/TM-K → WGS84 변환기 (한 번만 생성, lazy)."""
    global _transformer
    try:
        return _transformer
    except NameError:
        pass
    try:
        from pyproj import Transformer
        _transformer = Transformer.from_crs(SHP_CRS, WGS84, always_xy=True)
        return _transformer
    except Exception as e:
        logger.warning(f"pyproj Transformer 생성 실패 ({e}) — lng/lat 변환 비활성")
        _transformer = None
        return None


def _to_lnglat(x: float, y: float) -> Optional[tuple]:
    tr = _get_transformer()
    if tr is None:
        return None
    try:
        lng, lat = tr.transform(x, y)
        return (round(float(lng), 7), round(float(lat), 7))
    except Exception:
        return None


logger = logging.getLogger(__name__)


@dataclass
class SimulationResult:
    success: bool
    junctions: list = field(default_factory=list)
    pipes: list = field(default_factory=list)
    reservoirs: list = field(default_factory=list)
    bbox: Optional[tuple] = None         # (xmin, ymin, xmax, ymax) UTM
    bbox_lnglat: Optional[tuple] = None  # (lng_min, lat_min, lng_max, lat_max)
    node_count: int = 0
    link_count: int = 0
    min_pressure_m: Optional[float] = None
    max_pressure_m: Optional[float] = None
    avg_pressure_m: Optional[float] = None
    min_flow_lps: Optional[float] = None
    max_flow_lps: Optional[float] = None
    duration_ms: int = 0
    error: Optional[str] = None


def _isolate_largest_component(wn) -> None:
    """가장 큰 connected component 외의 노드·링크 제거 (in-place).

    WNTRSimulator 는 disconnected fragment 가 있으면 인덱스 에러 발생.
    SHP 변환 INP 는 끝점 미접합으로 fragment 가 다수 생기므로 정상 부분만 시뮬.
    """
    try:
        import networkx as nx
    except ImportError:
        return
    try:
        G = wn.to_graph().to_undirected()
        comps = sorted(nx.connected_components(G), key=len, reverse=True)
        if len(comps) <= 1:
            return
        keep = comps[0]
        drop_nodes = [n for n in wn.node_name_list if n not in keep]
        # 링크 먼저 제거 (노드 의존)
        drop_links = [
            lname for lname in wn.link_name_list
            if (lambda link: link.start_node_name in drop_nodes
                              or link.end_node_name in drop_nodes)(wn.get_link(lname))
        ]
        for lname in drop_links:
            try:
                wn.remove_link(lname)
            except Exception:
                pass
        for nname in drop_nodes:
            try:
                wn.remove_node(nname)
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"connected component 정리 실패 (무시): {e}")


def run_steady_state(inp_path: str | Path) -> SimulationResult:
    """wntr.sim.EpanetSimulator 로 정상상태 시뮬레이션 실행.

    Returns SimulationResult — junctions[{id, pressure_m, head_m, demand_lps}],
    pipes[{id, flow_lps, velocity_mps, headloss_m}].
    """
    start = time.time()
    try:
        import wntr
    except ImportError as e:
        return SimulationResult(success=False, error=f"wntr 미설치: {e}")

    inp_path = str(inp_path)
    if not Path(inp_path).exists():
        return SimulationResult(success=False, error=f"INP 파일 없음: {inp_path}")

    try:
        wn = wntr.network.WaterNetworkModel(inp_path)
        # 단일 시점 시뮬레이션 (PDD = Pressure Driven Demand)
        # SHP 변환 INP 는 disconnected components 가 있어 DD 모드에선 실패함.
        # PDD 모드에선 도달 불가 노드는 압력 0 으로 처리되어 시뮬레이션 진행 가능.
        wn.options.time.duration = 0
        try:
            wn.options.hydraulic.demand_model = "PDD"
            wn.options.hydraulic.minimum_pressure = 0.0
            wn.options.hydraulic.required_pressure = 20.0
        except Exception:
            pass

        # ARM64 환경에선 wntr 패키지에 EPANET .so 가 없음 → WNTRSimulator 폴백.
        # WNTRSimulator 는 순수 Python 으로 PDD/DD 시뮬레이션 제공.
        try:
            sim = wntr.sim.EpanetSimulator(wn)
            results = sim.run_sim()
            engine = "epanet"
        except (FileNotFoundError, OSError) as native_err:
            logger.warning(
                f"EpanetSimulator 미가용 ({native_err}) — WNTRSimulator 폴백"
            )
            # WNTRSimulator 는 disconnected node 처리를 위해 가장 큰 connected
            # component 만 유지 (작은 fragment 는 시뮬레이션 대상에서 제외).
            _isolate_largest_component(wn)
            sim = wntr.sim.WNTRSimulator(wn)
            results = sim.run_sim()
            engine = "wntr"

        # 노드 결과 (압력·헤드·수요) + 좌표 포함
        pressure = results.node["pressure"]
        head = results.node.get("head") if hasattr(results.node, "get") else results.node["head"]
        demand = results.node.get("demand") if hasattr(results.node, "get") else results.node["demand"]
        junctions: list = []
        pressures_m: list = []
        all_xs: list = []
        all_ys: list = []
        for nid in wn.junction_name_list:
            try:
                node = wn.get_node(nid)
                coord = getattr(node, "coordinates", None)
                cx = float(coord[0]) if coord else None
                cy = float(coord[1]) if coord else None
                lnglat = _to_lnglat(cx, cy) if cx is not None else None
                p = float(pressure[nid].iloc[0])
                h = float(head[nid].iloc[0]) if head is not None else None
                d = float(demand[nid].iloc[0]) if demand is not None else None
                junctions.append({
                    "id": nid,
                    "x": cx, "y": cy,
                    "lng": lnglat[0] if lnglat else None,
                    "lat": lnglat[1] if lnglat else None,
                    "pressure_m": round(p, 3),
                    "head_m": round(h, 3) if h is not None else None,
                    "demand_lps": round(d * 1000.0, 4) if d is not None else None,
                })
                pressures_m.append(p)
                if cx is not None:
                    all_xs.append(cx)
                    all_ys.append(cy)
            except Exception as e:
                logger.warning(f"junction {nid} 결과 추출 실패: {e}")

        # 파이프 결과 (유량·유속·손실) + vertex 좌표
        flow = results.link["flowrate"]
        velocity = results.link.get("velocity") if hasattr(results.link, "get") else None
        headloss = results.link.get("headloss") if hasattr(results.link, "get") else None
        pipes: list = []
        flows_lps: list = []
        for lid in wn.pipe_name_list:
            try:
                pipe = wn.get_link(lid)
                start_id = pipe.start_node_name
                end_id = pipe.end_node_name
                vertices = list(getattr(pipe, "vertices", None) or [])
                vertices_xy = [[float(vx), float(vy)] for vx, vy in vertices]
                vertices_lnglat = [
                    list(_to_lnglat(vx, vy) or [None, None])
                    for vx, vy in vertices_xy
                ]
                f_cms = float(flow[lid].iloc[0])
                v = float(velocity[lid].iloc[0]) if velocity is not None else None
                hl = float(headloss[lid].iloc[0]) if headloss is not None else None
                pipes.append({
                    "id": lid,
                    "start": start_id, "end": end_id,
                    "vertices": vertices_xy,
                    "vertices_lnglat": vertices_lnglat,
                    "flow_lps": round(f_cms * 1000.0, 4),
                    "velocity_mps": round(v, 3) if v is not None else None,
                    "headloss_m": round(hl, 3) if hl is not None else None,
                })
                flows_lps.append(f_cms * 1000.0)
            except Exception as e:
                logger.warning(f"pipe {lid} 결과 추출 실패: {e}")

        # 배수지 좌표 (시각화용 — 시뮬 결과에는 포함 안 됨)
        reservoirs: list = []
        for rid in wn.reservoir_name_list:
            try:
                node = wn.get_node(rid)
                coord = getattr(node, "coordinates", None)
                cx = float(coord[0]) if coord else None
                cy = float(coord[1]) if coord else None
                lnglat = _to_lnglat(cx, cy) if cx is not None else None
                head_m = float(getattr(node, "head_timeseries", lambda: None)() or 0)
                reservoirs.append({
                    "id": rid, "x": cx, "y": cy,
                    "lng": lnglat[0] if lnglat else None,
                    "lat": lnglat[1] if lnglat else None,
                    "head_m": round(head_m, 2),
                })
                if cx is not None:
                    all_xs.append(cx)
                    all_ys.append(cy)
            except Exception:
                pass

        duration_ms = int((time.time() - start) * 1000)
        bbox = (
            (round(min(all_xs), 4), round(min(all_ys), 4),
             round(max(all_xs), 4), round(max(all_ys), 4))
            if all_xs else None
        )
        bbox_lnglat = None
        if bbox:
            sw = _to_lnglat(bbox[0], bbox[1])
            ne = _to_lnglat(bbox[2], bbox[3])
            if sw and ne:
                bbox_lnglat = (sw[0], sw[1], ne[0], ne[1])
        return SimulationResult(
            success=True,
            junctions=junctions,
            pipes=pipes,
            reservoirs=reservoirs,
            bbox=bbox,
            bbox_lnglat=bbox_lnglat,
            node_count=len(junctions),
            link_count=len(pipes),
            min_pressure_m=round(min(pressures_m), 3) if pressures_m else None,
            max_pressure_m=round(max(pressures_m), 3) if pressures_m else None,
            avg_pressure_m=round(sum(pressures_m) / len(pressures_m), 3) if pressures_m else None,
            min_flow_lps=round(min(flows_lps), 4) if flows_lps else None,
            max_flow_lps=round(max(flows_lps), 4) if flows_lps else None,
            duration_ms=duration_ms,
        )

    except Exception as e:
        logger.exception("EPANET 시뮬레이션 실패")
        duration_ms = int((time.time() - start) * 1000)
        return SimulationResult(
            success=False,
            error=str(e),
            duration_ms=duration_ms,
        )
