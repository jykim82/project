"""SHP 관망 → EPANET .inp 파일 변환 (Phase 1).

입력:
  - SAA003 (송수관) / SAA004 (배수관) — PolyLine SHP
  - SA114 (배수지) — Point SHP

출력:
  - EPANET .inp 텍스트 (junctions + pipes + reservoirs + 기본 옵션)

Phase 1 정책:
  - wntr 가용 시 wntr.network.WaterNetworkModel 로 안전하게 빌드
  - 미가용 시 ImportError 발생 (호출 측에서 503 반환)
  - 노드 ID = 좌표 해시 (소수 4자리 반올림 → 동일 좌표 노드 자동 병합)
  - 누락 속성(관경/조도) 은 기본값 적용 (관경 100mm, Hazen-Williams C=120)
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .shp_reader import iter_records


logger = logging.getLogger(__name__)


@dataclass
class ConvertResult:
    inp_text: str
    node_count: int
    link_count: int
    reservoir_count: int = 0
    skipped_records: int = 0
    vertex_count: int = 0
    warnings: list = field(default_factory=list)


def _node_id(x: float, y: float, precision: int = 0) -> str:
    """좌표 → 짧은 노드 ID. precision=0 → 1m 단위 병합 (UTM-K 미터 좌표).

    SHP 라인의 끝점이 인근 다른 라인의 끝점과 정확히 일치하지 않을 때
    같은 노드로 묶어 connected component 수를 줄임 (시뮬레이션 정합성).
    """
    rx, ry = round(x, precision), round(y, precision)
    h = hashlib.md5(f"{rx},{ry}".encode()).hexdigest()
    return f"N{h[:8]}"


def _resolve_attr(attrs: dict, candidates: tuple, default=None):
    """SHP 속성 이름이 한글/영문/대소문자 혼재 — 후보 키 중 첫 매칭."""
    lower_map = {k.lower(): k for k in attrs.keys()}
    for cand in candidates:
        c = cand.lower()
        if c in lower_map:
            v = attrs[lower_map[c]]
            if v not in (None, "", " "):
                return v
    return default


def convert_pipes_to_inp(
    pipe_shp_paths: list[str | Path],
    reservoir_shp_path: Optional[str | Path] = None,
    *,
    network_title: str = "SLM EPANET Phase 1",
    default_diameter_mm: float = 100.0,
    default_roughness_c: float = 120.0,
    default_demand_lps: float = 0.0,
    default_elevation_m: float = 0.0,
) -> ConvertResult:
    """관망 SHP → EPANET .inp 텍스트.

    Returns ConvertResult — inp_text 는 그대로 파일에 기록 가능.
    """
    junctions: dict[str, dict] = {}      # node_id → {coord, elevation, demand}
    pipes: list[dict] = []               # [{id, n1, n2, length, diameter, roughness}]
    reservoirs: dict[str, dict] = {}     # node_id → {coord, head, name}
    warnings: list[str] = []
    skipped = 0

    # ---- 1) 파이프 SHP 처리 ----
    for shp in pipe_shp_paths:
        path = Path(shp)
        if not path.exists():
            warnings.append(f"파이프 SHP 없음: {path.name}")
            continue
        for rec in iter_records(path):
            if not rec.points or len(rec.points) < 2:
                skipped += 1
                continue
            # 첫 점 ↔ 마지막 점만 한 파이프로 단순화 (Phase 1 직선 근사)
            p1, p2 = rec.points[0], rec.points[-1]
            n1, n2 = _node_id(*p1), _node_id(*p2)
            if n1 == n2:
                skipped += 1
                continue

            length_m = _resolve_attr(rec.attrs, ("연장", "LEN", "LENGTH", "길이"))
            diameter_mm = _resolve_attr(rec.attrs,
                                        ("구경", "DIA", "DIAMETER", "관경"),
                                        default=default_diameter_mm)
            try:
                length_m = float(length_m) if length_m else _euclidean_length(rec.points)
            except (ValueError, TypeError):
                length_m = _euclidean_length(rec.points)
            try:
                diameter_mm = float(diameter_mm) if diameter_mm else default_diameter_mm
            except (ValueError, TypeError):
                diameter_mm = default_diameter_mm

            pipe_id = f"P{len(pipes) + 1:06d}"
            # 중간 vertex (첫·끝점 제외) — INP [VERTICES] 섹션에 보존하여 굴곡 표현
            mid_vertices = [(float(x), float(y)) for x, y in rec.points[1:-1]]
            pipes.append({
                "id": pipe_id,
                "n1": n1, "n2": n2,
                "length": max(0.1, length_m),
                "diameter": max(1.0, diameter_mm),
                "roughness": default_roughness_c,
                "vertices": mid_vertices,
            })
            for nid, pt in ((n1, p1), (n2, p2)):
                if nid not in junctions:
                    junctions[nid] = {
                        "coord": pt,
                        "elevation": default_elevation_m,
                        "demand": default_demand_lps,
                    }

    # ---- 2) 배수지 SHP 처리 ----
    if reservoir_shp_path:
        rpath = Path(reservoir_shp_path)
        if not rpath.exists():
            warnings.append(f"배수지 SHP 없음: {rpath.name}")
        else:
            for idx, rec in enumerate(iter_records(rpath), start=1):
                if not rec.points:
                    continue
                pt = rec.points[0]
                rid = _node_id(*pt)
                head_m = _resolve_attr(rec.attrs,
                                       ("표고", "EL", "ELEVATION", "수위"),
                                       default=50.0)
                try:
                    head_m = float(head_m) if head_m else 50.0
                except (ValueError, TypeError):
                    head_m = 50.0
                name = _resolve_attr(rec.attrs,
                                     ("시설명", "NAME", "관리번호"),
                                     default=f"RES{idx:03d}")
                reservoirs[rid] = {"coord": pt, "head": head_m, "name": str(name)}
                # 배수지 노드는 junction 에서 제외 (reservoir 로 분류)
                junctions.pop(rid, None)

    # ---- 3) .inp 텍스트 구성 ----
    inp = _build_inp_text(
        title=network_title,
        junctions=junctions,
        pipes=pipes,
        reservoirs=reservoirs,
    )

    vertex_count = sum(len(p.get("vertices") or []) for p in pipes)
    return ConvertResult(
        inp_text=inp,
        node_count=len(junctions),
        link_count=len(pipes),
        reservoir_count=len(reservoirs),
        skipped_records=skipped,
        vertex_count=vertex_count,
        warnings=warnings,
    )


def _euclidean_length(points: list) -> float:
    """좌표 거리 합 (좌표계가 미터 기준이라고 가정 — UTM-K 등)."""
    total = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        dx, dy = x2 - x1, y2 - y1
        total += (dx * dx + dy * dy) ** 0.5
    return max(0.1, total)


def _build_inp_text(*, title: str, junctions: dict, pipes: list,
                    reservoirs: dict) -> str:
    """EPANET .inp 텍스트 직접 구성 (wntr 의존 없음).

    EPANET 2.2 .inp 포맷 — 섹션별로 [JUNCTIONS] [RESERVOIRS] [PIPES] [COORDINATES] [OPTIONS] 등.
    """
    lines: list[str] = []
    lines.append("[TITLE]")
    lines.append(title)
    lines.append("")

    # JUNCTIONS — ID  Elev  Demand  Pattern
    lines.append("[JUNCTIONS]")
    lines.append(";ID              Elev        Demand      Pattern")
    for nid, j in junctions.items():
        lines.append(f" {nid:<15} {j['elevation']:<11.2f} {j['demand']:<11.2f}")
    lines.append("")

    # RESERVOIRS — ID  Head  Pattern
    lines.append("[RESERVOIRS]")
    lines.append(";ID              Head        Pattern")
    for rid, r in reservoirs.items():
        lines.append(f" {rid:<15} {r['head']:<11.2f}")
    lines.append("")

    lines.append("[TANKS]")
    lines.append(";ID  Elev  InitLvl  MinLvl  MaxLvl  Diam  MinVol  VolCurve")
    lines.append("")

    # PIPES — ID  Node1  Node2  Length  Diam  Roughness  MinorLoss  Status
    lines.append("[PIPES]")
    lines.append(";ID              Node1           Node2           Length      Diameter    Roughness   MinorLoss   Status")
    for p in pipes:
        lines.append(
            f" {p['id']:<15} {p['n1']:<15} {p['n2']:<15} "
            f"{p['length']:<11.2f} {p['diameter']:<11.2f} {p['roughness']:<11.2f} "
            f"0           Open"
        )
    lines.append("")

    for section in ("PUMPS", "VALVES", "DEMANDS", "EMITTERS", "CURVES",
                    "PATTERNS", "ENERGY", "STATUS", "CONTROLS", "RULES",
                    "QUALITY", "SOURCES", "REACTIONS", "MIXING"):
        lines.append(f"[{section}]")
        lines.append("")

    # OPTIONS
    lines.append("[OPTIONS]")
    lines.append(" Units               LPS")
    lines.append(" Headloss            H-W")
    lines.append(" Specific Gravity    1.0")
    lines.append(" Viscosity           1.0")
    lines.append(" Trials              40")
    lines.append(" Accuracy            0.001")
    lines.append(" Unbalanced          Continue 10")
    lines.append(" Demand Multiplier   1.0")
    lines.append(" Emitter Exponent    0.5")
    lines.append(" Quality             None")
    lines.append(" Tolerance           0.01")
    lines.append("")

    lines.append("[TIMES]")
    lines.append(" Duration            0:00")
    lines.append(" Hydraulic Timestep  1:00")
    lines.append(" Quality Timestep    0:05")
    lines.append(" Pattern Timestep    1:00")
    lines.append(" Pattern Start       0:00")
    lines.append(" Report Timestep     1:00")
    lines.append(" Report Start        0:00")
    lines.append(" Start ClockTime     12 am")
    lines.append(" Statistic           None")
    lines.append("")

    lines.append("[REPORT]")
    lines.append(" Status              No")
    lines.append(" Summary             No")
    lines.append("")

    # COORDINATES — 지도 표출용
    lines.append("[COORDINATES]")
    lines.append(";Node            X-Coord     Y-Coord")
    for nid, j in junctions.items():
        x, y = j["coord"]
        lines.append(f" {nid:<15} {x:<11.4f} {y:<11.4f}")
    for rid, r in reservoirs.items():
        x, y = r["coord"]
        lines.append(f" {rid:<15} {x:<11.4f} {y:<11.4f}")
    lines.append("")

    # [VERTICES] — 파이프 중간 vertex (지도 표출 굴곡 보존)
    lines.append("[VERTICES]")
    lines.append(";Link            X-Coord     Y-Coord")
    for p in pipes:
        for vx, vy in (p.get("vertices") or []):
            lines.append(f" {p['id']:<15} {vx:<11.4f} {vy:<11.4f}")
    lines.append("")

    lines.append("[BACKDROP]")
    lines.append("")

    lines.append("[TAGS]")
    lines.append("")

    lines.append("[END]")
    return "\n".join(lines) + "\n"


def validate_with_wntr(inp_path: str | Path) -> dict:
    """wntr 로 .inp 파일을 다시 읽어 검증 (옵션, wntr 가용 시).

    Returns: {valid: bool, node_count, link_count, error?}
    """
    try:
        import wntr
    except ImportError as e:
        return {"valid": False, "error": f"wntr 미설치: {e}"}
    try:
        wn = wntr.network.WaterNetworkModel(str(inp_path))
        return {
            "valid": True,
            "node_count": len(wn.node_name_list),
            "link_count": len(wn.link_name_list),
        }
    except Exception as e:
        return {"valid": False, "error": str(e)}
