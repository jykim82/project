"""EPANET API — 시뮬레이션 실행/이력/cron/정리/최신·단건 조회 + simulation DB 헬퍼."""

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
        "bbox": list(result.bbox) if result.bbox else None,
        "bbox_lnglat": list(result.bbox_lnglat) if result.bbox_lnglat else None,
        "junctions": result.junctions,
        "pipes": result.pipes,
        "reservoirs": result.reservoirs,
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
# 6-CRON) POST /admin/epanet/sim/cron — 자동 시뮬 (시계열 누적용)
# ===========================================================================

class SimCronRequest(BaseModel):
    region: str = "R01"
    use_synthetic_elevation: bool = True
    use_synthetic_demand: bool = True
    skip_if_recent_minutes: int = 30  # N분 이내 시뮬 있으면 스킵 (중복 방지)


@router.post("/sim/cron")
def run_sim_cron(req: SimCronRequest, request: Request) -> dict:
    """region 의 가장 최근 success artifact 에 대해 자동 시뮬.

    cron / launchd 에서 주기적으로 호출 — 시계열 누적용.
    이미 N분 이내 시뮬이 있으면 스킵 (skip_if_recent_minutes).
    """
    _ensure_enabled(req.region)
    if not is_wntr_available():
        raise HTTPException(501, detail="wntr 미설치")

    conn = get_db()
    try:
        cur = conn.cursor()
        # 최근 시뮬 체크
        if req.skip_if_recent_minutes > 0:
            cur.execute(
                """
                SELECT sim_id FROM tb_epanet_simulation_result
                 WHERE region = %s AND status = 'success'
                   AND created_at > NOW() - (%s || ' minutes')::interval
                 ORDER BY created_at DESC LIMIT 1
                """,
                (req.region, str(req.skip_if_recent_minutes)),
            )
            recent = cur.fetchone()
            if recent:
                cur.close()
                return {
                    "skipped": True,
                    "reason": f"최근 {req.skip_if_recent_minutes}분 이내 시뮬 #{recent[0]} 존재",
                    "sim_id": recent[0],
                }

        # 가장 최근 success artifact
        cur.execute(
            """
            SELECT artifact_id, file_path FROM tb_epanet_artifact
             WHERE region = %s AND status = 'success'
             ORDER BY created_at DESC LIMIT 1
            """,
            (req.region,),
        )
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    if not row:
        raise HTTPException(400, detail="성공 INP 산출물 없음 — INP 생성 후 cron 등록")
    artifact_id, file_path = row[0], row[1]
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(410, detail="INP 파일이 디스크에 없음")

    user_id = _get_user_id(request)
    sim_id = _insert_simulation_pending(
        artifact_id=artifact_id, region=req.region,
        sim_type="steady", created_by=f"cron:{user_id}",
    )
    result = run_steady_state(file_path)
    if not result.success:
        _update_simulation_failed(
            sim_id=sim_id, duration_ms=result.duration_ms,
            error_message=result.error or "unknown",
        )
        raise HTTPException(500, detail=f"cron 시뮬 실패: {result.error}")
    _update_simulation_success(sim_id=sim_id, result=result)
    return {
        "skipped": False,
        "sim_id": sim_id,
        "artifact_id": artifact_id,
        "node_count": result.node_count,
        "link_count": result.link_count,
        "duration_ms": result.duration_ms,
    }


@router.post("/sim/cleanup")
def cleanup_old_sims(region: str = "R01", days: int = 90,
                    keep_min: int = 30) -> dict:
    """region 별 days 일 이전 시뮬 삭제. 단 최소 keep_min 개는 유지."""
    _ensure_enabled(region)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM tb_epanet_simulation_result WHERE region = %s",
            (region,),
        )
        total = cur.fetchone()[0] or 0
        if total <= keep_min:
            cur.close()
            return {"deleted": 0, "total_before": total,
                    "reason": f"전체 {total} ≤ keep_min({keep_min}) — 삭제 안 함"}

        # 최소 keep_min 보존 — 가장 최근 keep_min 개 ID 추출, 나머지 중 days 이전
        cur.execute(
            """
            DELETE FROM tb_epanet_simulation_result
             WHERE region = %s
               AND created_at < NOW() - (%s || ' days')::interval
               AND sim_id NOT IN (
                 SELECT sim_id FROM tb_epanet_simulation_result
                  WHERE region = %s
                  ORDER BY created_at DESC LIMIT %s
               )
            """,
            (region, str(days), region, keep_min),
        )
        deleted = cur.rowcount
        conn.commit()
        cur.close()
        return {"deleted": deleted, "total_before": total, "keep_min": keep_min, "days": days}
    finally:
        conn.close()


# ===========================================================================
# 6-D) GET /admin/epanet/sim/latest — region 별 가장 최근 success 시뮬
# ===========================================================================

@router.get("/sim/latest")
def get_latest_simulation(region: str = "R01") -> dict:
    """region 의 가장 최근 success 시뮬 상세 (artifact 무관). 없으면 404."""
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
             WHERE region = %s AND status = 'success'
             ORDER BY created_at DESC
             LIMIT 1
            """,
            (region,),
        )
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, detail="성공한 시뮬레이션이 없습니다.")
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
