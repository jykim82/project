"""EPANET API — 상태/SHP 스캔/INP 생성·목록·다운로드·삭제 + artifact DB 헬퍼."""

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



from .common import INP_OUTPUT_DIR, SHP_BASE_DIR, _classify_shp, _ensure_enabled, _get_user_id, _list_shp, router
from .flow_map import _compute_live_demands

logger = logging.getLogger(__name__)

# ===========================================================================
# 1) GET /admin/epanet/status
# ===========================================================================

@router.get("/status")
def get_status(region: str = "R01") -> dict:
    """모듈 활성화·환경 상태 — 토글 OFF 여도 항상 200 응답."""
    enabled = is_enabled(region)
    wntr_ok = is_wntr_available()
    shp_files = _list_shp(SHP_BASE_DIR)
    classified = _classify_shp(shp_files)
    return {
        "enabled": enabled,
        "wntr_available": wntr_ok,
        "shp_base_dir": SHP_BASE_DIR,
        "inp_output_dir": INP_OUTPUT_DIR,
        "shp_total_count": len(shp_files),
        "shp_pipes": [p.name for p in classified["pipes"]],
        "shp_reservoirs": [p.name for p in classified["reservoirs"]],
        "shp_others_count": len(classified["others"]),
    }


# ===========================================================================
# 2) POST /admin/epanet/scan — 변환 전 검증
# ===========================================================================

class ScanRequest(BaseModel):
    region: str = "R01"
    only_pipes: bool = True


@router.post("/scan")
def scan_shp_files(req: ScanRequest) -> dict:
    _ensure_enabled(req.region)
    shp_files = _list_shp(SHP_BASE_DIR)
    if not shp_files:
        return {"results": [], "warning": f"SHP 파일이 없습니다: {SHP_BASE_DIR}"}

    classified = _classify_shp(shp_files)
    targets = classified["pipes"] + classified["reservoirs"]
    if not req.only_pipes:
        targets += classified["others"]

    results = []
    for path in targets:
        r = scan_shp(path)
        results.append({
            "file_name": r.file_name,
            "record_count": r.record_count,
            "geometry_type": r.geometry_type,
            "field_names": r.field_names,
            "encoding": r.encoding,
            "bbox": list(r.bbox) if r.bbox else None,
            "sample": r.sample,
            "error": r.error,
        })
    return {"results": results}


# ===========================================================================
# 3) POST /admin/epanet/inp/generate — 실제 변환
# ===========================================================================

class GenerateRequest(BaseModel):
    region: str = "R01"
    title: Optional[str] = None
    pipe_files: Optional[list[str]] = None        # 파일명만 (SHP_BASE_DIR 기준), None 이면 자동 분류
    reservoir_file: Optional[str] = None
    default_diameter_mm: float = 100.0
    default_roughness_c: float = 120.0
    default_demand_lps: float = 0.1               # 0 이면 flow 가 모두 0 → 화살표 방향 무의미
    use_elevation_points: bool = True             # tb_epanet_elevation_point 의 입력 표고를 IDW 보간
    use_synthetic_elevation: bool = False         # 시연용 합성 표고 (운영자 입력 전 미리보기)
    use_demand_points: bool = True                # tb_epanet_demand_point 의 입력 수요를 IDW 보간
    use_synthetic_demand: bool = False            # 시연용 합성 demand (도심 고/외곽 저)
    inject_live_demand: bool = False              # B-1: 실측 유량 매핑을 demand 로 자동 주입
    inject_live_hours: int = 1                    # 실측 평균 윈도우 (1~24)


@router.post("/inp/generate")
def generate_inp(req: GenerateRequest, request: Request) -> dict:
    _ensure_enabled(req.region)

    shp_files = _list_shp(SHP_BASE_DIR)
    if not shp_files:
        raise HTTPException(
            status_code=400,
            detail=f"SHP 파일이 없습니다: {SHP_BASE_DIR}",
        )

    if req.pipe_files:
        pipe_paths = [Path(SHP_BASE_DIR) / n for n in req.pipe_files]
        missing = [p.name for p in pipe_paths if not p.exists()]
        if missing:
            raise HTTPException(400, detail=f"SHP 파일 없음: {missing}")
    else:
        pipe_paths = _classify_shp(shp_files)["pipes"]
        if not pipe_paths:
            raise HTTPException(400, detail="송수관/배수관 SHP 가 없습니다.")

    if req.reservoir_file:
        reservoir_path: Optional[Path] = Path(SHP_BASE_DIR) / req.reservoir_file
        if not reservoir_path.exists():
            raise HTTPException(400, detail=f"배수지 SHP 없음: {req.reservoir_file}")
    else:
        reservoirs = _classify_shp(shp_files)["reservoirs"]
        reservoir_path = reservoirs[0] if reservoirs else None

    user_id = _get_user_id(request)
    title = req.title or f"SLM EPANET {req.region}"

    os.makedirs(INP_OUTPUT_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_name = f"{req.region}_{ts}.inp"
    out_path = os.path.join(INP_OUTPUT_DIR, out_name)

    # 산출물 행 — pending 으로 먼저 INSERT
    artifact_id = _insert_artifact(
        region=req.region,
        file_path=out_path,
        file_name=out_name,
        source_shp=", ".join([p.name for p in pipe_paths]
                             + ([reservoir_path.name] if reservoir_path else [])),
        created_by=user_id,
    )

    try:
        # 표고·수요 입력 조회 (운영자 입력 점들 → IDW 보간 입력)
        elevation_points: list = []
        demand_points: list = []
        if req.use_elevation_points or req.use_demand_points:
            conn_e = get_db()
            try:
                cur = conn_e.cursor()
                if req.use_elevation_points:
                    cur.execute(
                        "SELECT x, y, elevation_m FROM tb_epanet_elevation_point WHERE region = %s",
                        (req.region,),
                    )
                    elevation_points = [(float(r[0]), float(r[1]), float(r[2])) for r in cur.fetchall()]
                if req.use_demand_points:
                    cur.execute(
                        "SELECT x, y, demand_lps FROM tb_epanet_demand_point WHERE region = %s",
                        (req.region,),
                    )
                    demand_points = [(float(r[0]), float(r[1]), float(r[2])) for r in cur.fetchall()]
                cur.close()
            finally:
                conn_e.close()

        # B-1: 실측 유량 주입 — manual 점 ±10m 범위는 기존값 우선
        live_injected_count = 0
        if req.inject_live_demand:
            live_pts = _compute_live_demands(req.region, hours=req.inject_live_hours)
            existing_xy = [(p[0], p[1]) for p in demand_points]
            for lx, ly, lps, _label in live_pts:
                near = any((lx - mx) ** 2 + (ly - my) ** 2 < 100  # 10m^2 = 100
                           for mx, my in existing_xy)
                if near:
                    continue
                demand_points.append((lx, ly, lps))
                live_injected_count += 1

        result = convert_pipes_to_inp(
            pipe_shp_paths=pipe_paths,
            reservoir_shp_path=reservoir_path,
            network_title=title,
            default_diameter_mm=req.default_diameter_mm,
            default_roughness_c=req.default_roughness_c,
            default_demand_lps=req.default_demand_lps,
            elevation_points=elevation_points if elevation_points else None,
            use_synthetic_elevation=req.use_synthetic_elevation,
            demand_points=demand_points if demand_points else None,
            use_synthetic_demand=req.use_synthetic_demand,
        )
        Path(out_path).write_text(result.inp_text, encoding="utf-8")
        size = os.path.getsize(out_path)

        validation = (
            validate_with_wntr(out_path) if is_wntr_available() else
            {"valid": None, "error": "wntr 미설치 — 검증 스킵"}
        )

        _update_artifact_success(
            artifact_id=artifact_id,
            node_count=result.node_count,
            link_count=result.link_count,
            file_size_bytes=size,
        )
        return {
            "artifact_id": artifact_id,
            "file_name": out_name,
            "file_path": out_path,
            "file_size_bytes": size,
            "node_count": result.node_count,
            "link_count": result.link_count,
            "reservoir_count": result.reservoir_count,
            "skipped_records": result.skipped_records,
            "warnings": result.warnings,
            "wntr_validation": validation,
            "live_injected_count": live_injected_count,
        }
    except Exception as e:
        logger.exception("EPANET .inp 변환 실패")
        _update_artifact_failed(artifact_id=artifact_id, error_message=str(e))
        raise HTTPException(500, detail=f"변환 실패: {e}")


# ===========================================================================
# 4) GET /admin/epanet/inp/list
# ===========================================================================

@router.get("/inp/list")
def list_artifacts(region: str = "R01", limit: int = 50) -> dict:
    _ensure_enabled(region)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT artifact_id, region, file_name, source_shp,
                   node_count, link_count, status, file_size_bytes,
                   error_message, created_at, created_by
              FROM tb_epanet_artifact
             WHERE region = %s
             ORDER BY created_at DESC
             LIMIT %s
            """,
            (region, limit),
        )
        rows = cur.fetchall()
        cur.close()
        items = [{
            "artifact_id": r[0], "region": r[1], "file_name": r[2],
            "source_shp": r[3], "node_count": r[4], "link_count": r[5],
            "status": r[6], "file_size_bytes": r[7], "error_message": r[8],
            "created_at": r[9].isoformat() if r[9] else None,
            "created_by": r[10],
        } for r in rows]
        return {"items": items, "total": len(items)}
    finally:
        conn.close()


# ===========================================================================
# 5) GET /admin/epanet/inp/{id}/download
# ===========================================================================

@router.get("/inp/{artifact_id}/download")
def download_inp(artifact_id: int, region: str = "R01"):
    _ensure_enabled(region)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT file_path, file_name, status FROM tb_epanet_artifact "
            "WHERE artifact_id = %s AND region = %s",
            (artifact_id, region),
        )
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, detail="산출물 없음")
    file_path, file_name, status = row
    if status != "success":
        raise HTTPException(409, detail=f"상태가 success 가 아닙니다: {status}")
    if not os.path.exists(file_path):
        raise HTTPException(410, detail="파일이 디스크에 없습니다.")
    return FileResponse(
        file_path,
        filename=file_name,
        media_type="application/octet-stream",
    )



# ===========================================================================
# 6) DELETE /admin/epanet/inp/{id}
# ===========================================================================

@router.delete("/inp/{artifact_id}")
def delete_artifact(artifact_id: int, region: str = "R01") -> dict:
    _ensure_enabled(region)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT file_path FROM tb_epanet_artifact "
            "WHERE artifact_id = %s AND region = %s",
            (artifact_id, region),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, detail="산출물 없음")
        file_path = row[0]
        cur.execute(
            "DELETE FROM tb_epanet_artifact WHERE artifact_id = %s AND region = %s",
            (artifact_id, region),
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()
    try:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
    except Exception as e:
        logger.warning(f"산출물 파일 삭제 실패 (DB 행은 삭제됨): {e}")
    return {"deleted": artifact_id}



def _insert_artifact(*, region: str, file_path: str, file_name: str,
                     source_shp: str, created_by: str) -> int:
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO tb_epanet_artifact
                (region, file_path, file_name, source_shp, status, created_by)
            VALUES (%s, %s, %s, %s, 'pending', %s)
            RETURNING artifact_id
            """,
            (region, file_path, file_name, source_shp, created_by),
        )
        artifact_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        return artifact_id
    finally:
        conn.close()


def _update_artifact_success(*, artifact_id: int, node_count: int,
                             link_count: int, file_size_bytes: int) -> None:
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE tb_epanet_artifact
               SET status = 'success', node_count = %s, link_count = %s,
                   file_size_bytes = %s, error_message = NULL
             WHERE artifact_id = %s
            """,
            (node_count, link_count, file_size_bytes, artifact_id),
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()


def _update_artifact_failed(*, artifact_id: int, error_message: str) -> None:
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE tb_epanet_artifact SET status = 'failed', error_message = %s "
            "WHERE artifact_id = %s",
            (error_message[:1000], artifact_id),
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()


