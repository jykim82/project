"""IForest 이상탐지 모델 성능 평가 조회 API (읽기 전용).

사양: docs/iforest-eval-spec.md §6
tb_iforest_model_run / tb_iforest_model_metric 를 조회해 KPI·회차 히스토리·
그룹별 집계·최악 캘리브레이션 모델을 반환한다. 학습 회차가 없으면 ready=false.

비지도 모델이라 정답 레이블이 없어 "정확도%"가 아니라 안정성(calibration_err =
관측 이상률이 목표 contamination 에서 벗어난 정도)·커버리지를 평가한다.
"""

import logging
from typing import Callable, Optional

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/iforest-eval", tags=["iforest-eval"])

_get_db_connection: Optional[Callable] = None


def init(get_db_connection_fn):
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


def _get_conn():
    if _get_db_connection is None:
        raise RuntimeError("iforest_eval not initialized")
    return _get_db_connection()


def _run_dict(r) -> dict:
    return {
        "model_version": r[0],
        "trained_at": r[1].isoformat() if r[1] else None,
        "train_window_days": r[2],
        "tier1_count": r[3],
        "tier2_count": r[4],
        "total_models": r[5],
        "n_eligible": r[6],
        "n_skipped": r[7],
        "coverage_pct": r[8],
        "mean_anomaly_rate": r[9],
        "mean_contamination": r[10],
        "calibration_err": r[11],
        "mean_score": r[12],
        "feature_set": r[13],
        "status": r[14],
    }


_RUN_COLS = """model_version, trained_at, train_window_days,
               tier1_count, tier2_count, total_models, n_eligible, n_skipped,
               coverage_pct, mean_anomaly_rate, mean_contamination,
               calibration_err, mean_score, feature_set, status"""


@router.get("")
def get_iforest_eval(
    region: str = Query("R01"),
    worst_limit: int = Query(20, ge=1, le=200),
    tier: Optional[int] = Query(None, description="최악 모델 Tier 필터 (1|2)"),
):
    """최신 회차 KPI + 회차 히스토리 + 그룹별 집계 + 최악 캘리브레이션 모델."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT {_RUN_COLS}
              FROM tb_iforest_model_run
             WHERE region = %s
             ORDER BY trained_at DESC
             LIMIT 20
            """,
            (region,),
        )
        runs = [_run_dict(r) for r in cur.fetchall()]
        if not runs:
            cur.close()
            return {"ready": False, "region": region, "latest": None,
                    "runs": [], "groups": None, "worst_models": [],
                    "overall": None}

        latest = runs[0]
        version = latest["model_version"]

        # 그룹 집계 헬퍼 — 캘리브레이션·이상률·모델수
        def _group(rows):
            return [
                {
                    "group": g[0],
                    "n": g[1],
                    "calibration_avg": round(g[2], 2) if g[2] is not None else None,
                    "calibration_max": round(g[3], 2) if g[3] is not None else None,
                    "anomaly_rate_avg": round(g[4], 2) if g[4] is not None else None,
                    "contamination_avg": round(g[5] * 100, 2) if g[5] is not None else None,
                    "samples_avg": round(g[6], 0) if g[6] is not None else None,
                }
                for g in rows
            ]

        # Tier 별
        cur.execute(
            """
            SELECT 'Tier-' || tier, COUNT(*),
                   AVG(calibration_err), MAX(calibration_err),
                   AVG(anomaly_rate), AVG(contamination), AVG(n_samples)
              FROM tb_iforest_model_metric
             WHERE region = %s AND model_version = %s
             GROUP BY tier
             ORDER BY tier
            """,
            (region, version),
        )
        by_tier = _group(cur.fetchall())

        # 시설유형 별
        cur.execute(
            """
            SELECT COALESCE(NULLIF(facilitytype, ''), '(미상)'), COUNT(*),
                   AVG(calibration_err), MAX(calibration_err),
                   AVG(anomaly_rate), AVG(contamination), AVG(n_samples)
              FROM tb_iforest_model_metric
             WHERE region = %s AND model_version = %s
             GROUP BY COALESCE(NULLIF(facilitytype, ''), '(미상)')
             ORDER BY AVG(calibration_err) DESC NULLS LAST
            """,
            (region, version),
        )
        by_facilitytype = _group(cur.fetchall())

        # 최악 캘리브레이션 모델 (퇴화 의심)
        params = [region, version]
        tier_clause = ""
        if tier in (1, 2):
            tier_clause = " AND tier = %s"
            params.append(tier)
        params.append(worst_limit)
        cur.execute(
            f"""
            SELECT tier, entity_key, sitename, facilitytype, n_samples,
                   n_features, contamination, anomaly_rate, mean_score,
                   score_std, calibration_err
              FROM tb_iforest_model_metric
             WHERE region = %s AND model_version = %s{tier_clause}
             ORDER BY calibration_err DESC NULLS LAST
             LIMIT %s
            """,
            tuple(params),
        )
        worst = [
            {
                "tier": r[0], "entity_key": r[1], "sitename": r[2],
                "facilitytype": r[3], "n_samples": r[4], "n_features": r[5],
                "contamination": r[6], "anomaly_rate": r[7], "mean_score": r[8],
                "score_std": r[9], "calibration_err": r[10],
            }
            for r in cur.fetchall()
        ]

        cur.close()
        return {
            "ready": True,
            "region": region,
            "latest": latest,
            "runs": runs,
            "groups": {"by_tier": by_tier, "by_facilitytype": by_facilitytype},
            "worst_models": worst,
            "overall": {
                "coverage_pct": latest["coverage_pct"],
                "calibration_err": latest["calibration_err"],
                "mean_anomaly_rate": latest["mean_anomaly_rate"],
                "tier1_count": latest["tier1_count"],
                "tier2_count": latest["tier2_count"],
            },
        }
    finally:
        conn.close()


@router.get("/group-models")
def get_group_models(
    region: str = Query("R01"),
    axis: str = Query("facilitytype", description="그룹 축: tier | facilitytype"),
    group: str = Query(..., description="그룹 키 ('Tier-1'/'Tier-2' 또는 시설유형)"),
):
    """그룹에 속한 모델 상세 리스트 (최신 회차, 캘리브레이션 내림차순)."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT model_version FROM tb_iforest_model_run "
            "WHERE region = %s ORDER BY trained_at DESC LIMIT 1",
            (region,),
        )
        row = cur.fetchone()
        if not row:
            cur.close()
            return {"region": region, "axis": axis, "group": group,
                    "model_version": None, "models": []}
        version = row[0]

        if axis == "tier":
            where = "tier = %s"
            key = group.replace("Tier-", "").strip()
        else:
            where = "COALESCE(NULLIF(facilitytype, ''), '(미상)') = %s"
            key = group
        cur.execute(
            f"""
            SELECT tier, entity_key, sitename, facilitytype, n_samples,
                   n_features, contamination, anomaly_rate, mean_score,
                   score_std, calibration_err
              FROM tb_iforest_model_metric
             WHERE region = %s AND model_version = %s AND {where}
             ORDER BY calibration_err DESC NULLS LAST
            """,
            (region, version, key),
        )
        models = [
            {
                "tier": r[0], "entity_key": r[1], "sitename": r[2],
                "facilitytype": r[3], "n_samples": r[4], "n_features": r[5],
                "contamination": r[6], "anomaly_rate": r[7], "mean_score": r[8],
                "score_std": r[9], "calibration_err": r[10],
            }
            for r in cur.fetchall()
        ]
        cur.close()
        return {
            "region": region, "axis": axis, "group": group,
            "model_version": version, "models": models,
        }
    finally:
        conn.close()
