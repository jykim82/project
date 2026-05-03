"""endpoints/epanet.py — EPANET 수리 시뮬레이션 API (Phase 1).

활성화: tb_comm_code (region, 'SITE_SETTING', 'EPANET_ENABLED')='Y'
SHP 위치: 환경변수 EPANET_SHP_BASE_DIR (기본 /data/files/gis/shp)
산출물: /data/files/epanet/{region}_{ts}.inp + tb_epanet_artifact 행

엔드포인트:
- GET    /admin/epanet/status                — 활성화·환경 상태
- POST   /admin/epanet/scan                  — SHP 메타 스캔 (변환 전 검증)
- POST   /admin/epanet/inp/generate          — SHP→.inp 변환 실행
- GET    /admin/epanet/inp/list              — 산출물 목록
- GET    /admin/epanet/inp/{artifact_id}/download — .inp 파일 다운로드
- DELETE /admin/epanet/inp/{artifact_id}     — 산출물 삭제

비활성(default) 시: status 는 응답, 나머지는 503.
"""

from __future__ import annotations

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
from epanet.simulator import run_steady_state


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
        result = convert_pipes_to_inp(
            pipe_shp_paths=pipe_paths,
            reservoir_shp_path=reservoir_path,
            network_title=title,
            default_diameter_mm=req.default_diameter_mm,
            default_roughness_c=req.default_roughness_c,
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
# 6-A) POST /admin/epanet/inp/{id}/simulate — 정상상태 시뮬레이션 실행
# ===========================================================================

@router.post("/inp/{artifact_id}/simulate")
def simulate_inp(artifact_id: int, request: Request, region: str = "R01") -> dict:
    _ensure_enabled(region)
    if not is_wntr_available():
        raise HTTPException(
            status_code=501,
            detail="wntr 라이브러리가 설치되지 않았습니다. Docker 이미지를 재빌드하세요.",
        )

    # artifact 조회
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT file_path, status FROM tb_epanet_artifact "
            "WHERE artifact_id = %s AND region = %s",
            (artifact_id, region),
        )
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, detail="산출물 없음")
    file_path, art_status = row
    if art_status != "success":
        raise HTTPException(409, detail=f"INP 산출물 상태가 success 가 아닙니다: {art_status}")
    if not os.path.exists(file_path):
        raise HTTPException(410, detail="INP 파일이 디스크에 없습니다.")

    user_id = _get_user_id(request)
    sim_id = _insert_simulation_pending(
        artifact_id=artifact_id, region=region, sim_type="steady",
        created_by=user_id,
    )
    result = run_steady_state(file_path)

    if not result.success:
        _update_simulation_failed(
            sim_id=sim_id,
            duration_ms=result.duration_ms,
            error_message=result.error or "unknown",
        )
        raise HTTPException(500, detail=f"시뮬레이션 실패: {result.error}")

    _update_simulation_success(sim_id=sim_id, result=result)
    return {
        "sim_id": sim_id,
        "artifact_id": artifact_id,
        "status": "success",
        "node_count": result.node_count,
        "link_count": result.link_count,
        "min_pressure_m": result.min_pressure_m,
        "max_pressure_m": result.max_pressure_m,
        "avg_pressure_m": result.avg_pressure_m,
        "min_flow_lps": result.min_flow_lps,
        "max_flow_lps": result.max_flow_lps,
        "duration_ms": result.duration_ms,
    }


# ===========================================================================
# 6-B) GET /admin/epanet/inp/{id}/simulations — 산출물별 시뮬레이션 이력
# ===========================================================================

@router.get("/inp/{artifact_id}/simulations")
def list_simulations(artifact_id: int, region: str = "R01", limit: int = 20) -> dict:
    _ensure_enabled(region)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT sim_id, sim_type, status,
                   node_count, link_count,
                   min_pressure_m, max_pressure_m, avg_pressure_m,
                   min_flow_lps, max_flow_lps,
                   duration_ms, error_message, created_at, created_by
              FROM tb_epanet_simulation_result
             WHERE artifact_id = %s AND region = %s
             ORDER BY created_at DESC
             LIMIT %s
            """,
            (artifact_id, region, limit),
        )
        rows = cur.fetchall()
        cur.close()
        items = [{
            "sim_id": r[0], "sim_type": r[1], "status": r[2],
            "node_count": r[3], "link_count": r[4],
            "min_pressure_m": r[5], "max_pressure_m": r[6], "avg_pressure_m": r[7],
            "min_flow_lps": r[8], "max_flow_lps": r[9],
            "duration_ms": r[10], "error_message": r[11],
            "created_at": r[12].isoformat() if r[12] else None,
            "created_by": r[13],
        } for r in rows]
        return {"items": items, "total": len(items)}
    finally:
        conn.close()


# ===========================================================================
# 6-C) GET /admin/epanet/sim/{sim_id} — 시뮬레이션 상세 (result_data 포함)
# ===========================================================================

@router.get("/sim/{sim_id}")
def get_simulation(sim_id: int, region: str = "R01") -> dict:
    _ensure_enabled(region)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT sim_id, artifact_id, sim_type, status,
                   node_count, link_count,
                   min_pressure_m, max_pressure_m, avg_pressure_m,
                   min_flow_lps, max_flow_lps,
                   result_data, duration_ms, error_message,
                   created_at, created_by
              FROM tb_epanet_simulation_result
             WHERE sim_id = %s AND region = %s
            """,
            (sim_id, region),
        )
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, detail="시뮬레이션 결과 없음")
    return {
        "sim_id": row[0], "artifact_id": row[1], "sim_type": row[2], "status": row[3],
        "node_count": row[4], "link_count": row[5],
        "min_pressure_m": row[6], "max_pressure_m": row[7], "avg_pressure_m": row[8],
        "min_flow_lps": row[9], "max_flow_lps": row[10],
        "result_data": row[11], "duration_ms": row[12], "error_message": row[13],
        "created_at": row[14].isoformat() if row[14] else None,
        "created_by": row[15],
    }


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


# ===========================================================================
# 내부 헬퍼
# ===========================================================================

def _get_user_id(request: Request) -> str:
    """JWT 미들웨어가 request.state.user 에 채운 user_id 사용 (없으면 'system')."""
    user = getattr(request.state, "user", None)
    if isinstance(user, dict):
        return user.get("user_id") or user.get("sub") or "system"
    return "system"


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


def _insert_simulation_pending(*, artifact_id: int, region: str, sim_type: str,
                               created_by: str) -> int:
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO tb_epanet_simulation_result
                (artifact_id, region, sim_type, status, created_by)
            VALUES (%s, %s, %s, 'pending', %s)
            RETURNING sim_id
            """,
            (artifact_id, region, sim_type, created_by),
        )
        sim_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        return sim_id
    finally:
        conn.close()


def _update_simulation_success(*, sim_id: int, result) -> None:
    """SimulationResult 객체로 결과 행을 갱신."""
    import json
    payload = {"junctions": result.junctions, "pipes": result.pipes}
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE tb_epanet_simulation_result
               SET status = 'success',
                   node_count = %s, link_count = %s,
                   min_pressure_m = %s, max_pressure_m = %s, avg_pressure_m = %s,
                   min_flow_lps = %s, max_flow_lps = %s,
                   result_data = %s::jsonb,
                   duration_ms = %s, error_message = NULL
             WHERE sim_id = %s
            """,
            (result.node_count, result.link_count,
             result.min_pressure_m, result.max_pressure_m, result.avg_pressure_m,
             result.min_flow_lps, result.max_flow_lps,
             json.dumps(payload, ensure_ascii=False),
             result.duration_ms, sim_id),
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()


def _update_simulation_failed(*, sim_id: int, duration_ms: int,
                              error_message: str) -> None:
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE tb_epanet_simulation_result
               SET status = 'failed', duration_ms = %s, error_message = %s
             WHERE sim_id = %s
            """,
            (duration_ms, error_message[:1000], sim_id),
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()
