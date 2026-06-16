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


def _ACC_SQL(prefix: str = "") -> str:
    """단위 무관 정확도% = 100 × (1 − MAE/y_scale), [0,100] 클램프. y_scale 결측 시 NULL."""
    return (
        f"GREATEST(0, LEAST(100, 100 * (1 - {prefix}mae / "
        f"NULLIF({prefix}y_scale, 0))))"
    )

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
    kind: Optional[str] = Query(None, description="최악 태그 종류 필터 (flow/level/pressure/quality/other)"),
):
    """최신 회차 KPI + 회차 히스토리 + 최악 태그(MAE 내림차순). kind 로 종류 필터."""
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

        # 최신 회차에 존재하는 종류 목록 (필터 드롭다운용)
        cur.execute(
            """
            SELECT trend_kind, COUNT(*)
              FROM tb_baseline_tag_metric
             WHERE region = %s AND model_version = %s AND trend_kind IS NOT NULL
             GROUP BY trend_kind
             ORDER BY COUNT(*) DESC
            """,
            (region, latest_version),
        )
        kinds = [{"kind": r[0], "n": r[1]} for r in cur.fetchall()]

        # 최악 태그 (최신 회차, MAE 내림차순) — kind 필터 선택 시 좁힘
        params = [region, latest_version]
        kind_clause = ""
        if kind:
            kind_clause = " AND trend_kind = %s"
            params.append(kind)
        params.append(worst_limit)
        cur.execute(
            f"""
            SELECT tagsn, mae, rmse, sigma, coverage_pct, n_samples, method,
                   trend_kind, lag_avail_pct, y_scale, {_ACC_SQL("")} AS accuracy
              FROM tb_baseline_tag_metric
             WHERE region = %s AND model_version = %s{kind_clause}
             ORDER BY mae DESC NULLS LAST
             LIMIT %s
            """,
            tuple(params),
        )
        worst = [
            {
                "tagsn": r[0], "mae": r[1], "rmse": r[2], "sigma": r[3],
                "coverage_pct": r[4], "n_samples": r[5], "method": r[6],
                "trend_kind": r[7], "lag_avail_pct": r[8], "y_scale": r[9],
                "accuracy_pct": round(r[10], 1) if r[10] is not None else None,
            }
            for r in cur.fetchall()
        ]

        # 그룹별 성능 집계 (최신 회차) — 종류(trend_kind) / 개별 시설(sitename)
        # 절대 MAE 는 단위 스케일에 비례하므로 단위 무관 정확도%(=100×(1−MAE/y_scale))
        # 를 함께 산출해 유량·수위·압력 시설을 같은 척도로 비교 가능하게 한다.
        def _round_group(rows):
            return [
                {
                    "group": g[0],
                    "n": g[1],
                    "accuracy_avg": round(g[2], 1) if g[2] is not None else None,
                    "mae_avg": round(g[3], 4) if g[3] is not None else None,
                    "mae_max": round(g[4], 4) if g[4] is not None else None,
                    "coverage_avg": round(g[5], 1) if g[5] is not None else None,
                    "lag_avail_avg": round(g[6], 1) if g[6] is not None else None,
                }
                for g in rows
            ]

        cur.execute(
            f"""
            SELECT trend_kind, COUNT(*), AVG({_ACC_SQL("")}), AVG(mae), MAX(mae),
                   AVG(coverage_pct), AVG(lag_avail_pct)
              FROM tb_baseline_tag_metric
             WHERE region = %s AND model_version = %s AND trend_kind IS NOT NULL
             GROUP BY trend_kind
             ORDER BY AVG({_ACC_SQL("")}) ASC NULLS LAST
            """,
            (region, latest_version),
        )
        by_kind = _round_group(cur.fetchall())

        # 개별 시설(사이트명 + 시설유형, 예: "신평 가압장")
        cur.execute(
            f"""
            SELECT TRIM(COALESCE(t.sitename, '') || ' ' || COALESCE(t.facilitytype, '')),
                   COUNT(*), AVG({_ACC_SQL("m.")}), AVG(m.mae), MAX(m.mae),
                   AVG(m.coverage_pct), AVG(m.lag_avail_pct)
              FROM tb_baseline_tag_metric m
              JOIN tb_tag_info t ON t.tagsn = m.tagsn
             WHERE m.region = %s AND m.model_version = %s
             GROUP BY TRIM(COALESCE(t.sitename, '') || ' ' || COALESCE(t.facilitytype, ''))
             ORDER BY AVG({_ACC_SQL("m.")}) ASC NULLS LAST
            """,
            (region, latest_version),
        )
        by_site = _round_group(cur.fetchall())

        # 전체 정확도(단위 무관) — 최신 회차 전 태그 평균
        cur.execute(
            f"""
            SELECT AVG({_ACC_SQL("")})
              FROM tb_baseline_tag_metric
             WHERE region = %s AND model_version = %s
            """,
            (region, latest_version),
        )
        acc_row = cur.fetchone()
        overall_accuracy = (
            round(acc_row[0], 1) if acc_row and acc_row[0] is not None else None
        )

        cur.close()
        return {
            "ready": True,
            "region": region,
            "latest": latest,
            "runs": runs,
            "worst_tags": worst,
            "kinds": kinds,
            "groups": {"by_kind": by_kind, "by_site": by_site},
            "overall_accuracy_pct": overall_accuracy,
        }
    finally:
        conn.close()


@router.get("/group-tags")
def get_group_tags(
    region: str = Query("R01"),
    axis: str = Query("site", description="그룹 축: kind(종류) | site(시설)"),
    group: str = Query(..., description="그룹 키 (종류 코드 또는 '사이트명 시설유형')"),
):
    """그룹(종류/시설)에 속한 학습 태그 상세 리스트 (최신 회차, MAE 내림차순).

    그룹별 정확도 행을 펼쳐 '어떤 태그로 학습됐나'를 확인한다.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT model_version FROM tb_baseline_model_run "
            "WHERE region = %s ORDER BY trained_at DESC LIMIT 1",
            (region,),
        )
        row = cur.fetchone()
        if not row:
            cur.close()
            return {"region": region, "axis": axis, "group": group, "tags": []}
        version = row[0]

        if axis == "kind":
            where = "m.trend_kind = %s"
        else:
            where = (
                "TRIM(COALESCE(t.sitename, '') || ' ' || "
                "COALESCE(t.facilitytype, '')) = %s"
            )
        cur.execute(
            f"""
            SELECT m.tagsn, t.datadesc, m.trend_kind, m.mae, m.rmse, m.sigma,
                   m.coverage_pct, m.lag_avail_pct, m.n_samples, m.method,
                   m.y_scale, {_ACC_SQL("m.")} AS accuracy
              FROM tb_baseline_tag_metric m
              LEFT JOIN tb_tag_info t ON t.tagsn = m.tagsn
             WHERE m.region = %s AND m.model_version = %s AND {where}
             ORDER BY m.mae DESC NULLS LAST
            """,
            (region, version, group),
        )
        tags = [
            {
                "tagsn": r[0], "datadesc": r[1], "trend_kind": r[2],
                "mae": r[3], "rmse": r[4], "sigma": r[5],
                "coverage_pct": r[6], "lag_avail_pct": r[7],
                "n_samples": r[8], "method": r[9], "y_scale": r[10],
                "accuracy_pct": round(r[11], 1) if r[11] is not None else None,
            }
            for r in cur.fetchall()
        ]
        cur.close()
        return {
            "region": region, "axis": axis, "group": group,
            "model_version": version, "tags": tags,
        }
    finally:
        conn.close()
