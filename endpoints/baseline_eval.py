"""트렌드 GBT baseline 성능 평가 조회 API (P2, 읽기 전용).

사양: docs/trend-baseline-gbt-spec.md §6.3
tb_baseline_model_run / tb_baseline_tag_metric 를 조회해 KPI·회차 히스토리·
최악 태그를 반환한다. 학습 회차가 없으면 빈 결과(ready=false).
"""

import logging
from typing import Callable, Optional

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/baseline-eval", tags=["baseline-eval"])

_get_db_connection: Optional[Callable] = None


def init(get_db_connection_fn):
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


def _get_conn():
    if _get_db_connection is None:
        raise RuntimeError("baseline_eval not initialized")
    return _get_db_connection()


@router.get("")
def get_baseline_eval(
    region: str = Query("R01"),
    worst_limit: int = Query(20, ge=1, le=200),
):
    """최신 회차 KPI + 회차 히스토리 + 최악 태그(MAE 내림차순)."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        # 회차 히스토리 (최근 20)
        cur.execute(
            """
            SELECT model_version, trained_at, train_window_days,
                   n_tags_trained, n_tags_fallback, overall_mae, overall_rmse,
                   coverage_pct, mae_hourly_mean, improvement_pct, feature_set, status,
                   dataset_summary
              FROM tb_baseline_model_run
             WHERE region = %s
             ORDER BY trained_at DESC
             LIMIT 20
            """,
            (region,),
        )
        runs = [
            {
                "model_version": r[0],
                "trained_at": r[1].isoformat() if r[1] else None,
                "train_window_days": r[2],
                "n_tags_trained": r[3],
                "n_tags_fallback": r[4],
                "overall_mae": r[5],
                "overall_rmse": r[6],
                "coverage_pct": r[7],
                "mae_hourly_mean": r[8],
                "improvement_pct": r[9],
                "feature_set": r[10],
                "status": r[11],
                "dataset_summary": r[12],
            }
            for r in cur.fetchall()
        ]

        if not runs:
            cur.close()
            return {"ready": False, "region": region, "latest": None,
                    "runs": [], "worst_tags": []}

        latest = runs[0]
        latest_version = latest["model_version"]

        # 최악 태그 (최신 회차, MAE 내림차순)
        cur.execute(
            """
            SELECT tagsn, mae, rmse, sigma, coverage_pct, n_samples, method
              FROM tb_baseline_tag_metric
             WHERE region = %s AND model_version = %s
             ORDER BY mae DESC NULLS LAST
             LIMIT %s
            """,
            (region, latest_version, worst_limit),
        )
        worst = [
            {
                "tagsn": r[0], "mae": r[1], "rmse": r[2], "sigma": r[3],
                "coverage_pct": r[4], "n_samples": r[5], "method": r[6],
            }
            for r in cur.fetchall()
        ]
        cur.close()
        return {
            "ready": True,
            "region": region,
            "latest": latest,
            "runs": runs,
            "worst_tags": worst,
        }
    finally:
        conn.close()
