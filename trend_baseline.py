"""trend_baseline.py — 트렌드 정상 기대값(baseline) GBT 모델.

사양: docs/trend-baseline-gbt-spec.md (v1, 2026-06-16)

"평소 대비" 판정의 정상 기대값을 시간대 평균(hourly_mean)에서 그래디언트 부스팅
트리(sklearn HistGradientBoostingRegressor) 예측값으로 고도화한다.
- 전역 단일 모델(region 1개) — 캘린더 + 계절 lag + 시설 식별 피처.
- 고카디널리티 tagsn 은 피처에서 제외, 태그 개별성은 lag_24h/168h 로 반영.
- z-score 판정·forecast·UI 는 불변. 본 모듈은 기대값(+밴드)만 산출.
- 모델 없음/데이터 부족/skip 대상 → 호출부가 hourly_mean 으로 폴백.

CLI:
    python -m trend_baseline train [--region R01] [--window-days 60]
"""

from __future__ import annotations

import json
import logging
import os
import pickle
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

logger = logging.getLogger("slm.trend_baseline")

DEFAULT_REGION = "R01"
MODEL_DIR = os.environ.get("BASELINE_MODEL_DIR", "data/models")
# 운영 기본값. dev(데이터 짧음) 검증 시 env 로 하향 가능 — 사양 §2.1/§2.4 불변.
WINDOW_DAYS = int(os.environ.get("BASELINE_WINDOW_DAYS", "60"))      # 학습창
HOLDOUT_DAYS = int(os.environ.get("BASELINE_HOLDOUT_DAYS", "7"))     # 시간순 홀드아웃
MIN_TAG_ROWS = int(os.environ.get("BASELINE_MIN_TAG_ROWS", str(24 * 14)))  # 태그당 최소(시간행)
FEATURE_SET = "v1"

# 저카디널리티 categorical (native) — tagsn(2700)은 제외
CAT_COLS = ["trend_kind", "facilitytype", "equipmenttype", "site_group"]
NUM_COLS = [
    "hour", "dow", "month", "is_weekend", "is_holiday",
    "hour_sin", "hour_cos", "dow_sin", "dow_cos",
    "lag_24h", "lag_168h", "roll_mean_24h", "roll_std_24h",
]

# KR 공휴일(폐쇄망 — 정적 리스트). 운영 시 config 로 분리 가능. 2026 기준.
KR_HOLIDAYS = {
    "2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18", "2026-03-01",
    "2026-03-02", "2026-05-05", "2026-05-24", "2026-05-25", "2026-06-06",
    "2026-08-15", "2026-08-17", "2026-09-24", "2026-09-25", "2026-09-26",
    "2026-10-03", "2026-10-05", "2026-10-09", "2026-12-25",
}


def _connect():
    import psycopg2
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=os.environ.get("DB_PORT", "5433"),
        dbname=os.environ.get("DB_NAME", "slm"),
        user=os.environ.get("DB_USER", "slm_dev"),
        password=os.environ.get("DB_PASSWORD", ""),
    )


def _kind_from_text(datainfo: str, unit: str) -> Optional[str]:
    """datainfo/unit 키워드 → trend_kind (trend_comparison.detect_trend_kind 와 동일 규칙)."""
    s = f"{datainfo} {unit}".lower()
    if any(k in s for k in ("유량", "flow", "lps", "cmh", "lpm")):
        return "flow"
    if any(k in s for k in ("수위", "level", "height")):
        return "level"
    if any(k in s for k in ("압력", "pressure", "kgf", "bar")):
        return "pressure"
    if any(k in s for k in ("탁도", "잔류염소", "ph", "turbidity", "chlorine")):
        return "quality"
    return None


# ── 피처 빌드 ────────────────────────────────────────────

def _load_hourly_frame(conn, region: str, window_days: int):
    """analog non-skip 태그의 시간 집계 + 메타 → pandas DataFrame.

    columns: tagsn, ts, y, trend_kind, facilitytype, equipmenttype, site_group
    """
    import pandas as pd

    cur = conn.cursor()
    cur.execute(
        """
        SELECT r.tagsn, date_trunc('hour', r.logtime) AS ts, AVG(r.val) AS y
          FROM tb_tag_raw_data r
          JOIN tb_tag_info t ON t.tagsn = r.tagsn
         WHERE r.region = %s
           AND r.logtime > NOW() - (%s || ' days')::interval
           AND r.val IS NOT NULL
           AND t.tagtype NOT LIKE %s
           AND COALESCE(t.datainfo,'') NOT LIKE %s
           AND COALESCE(t.datainfo,'') NOT LIKE %s
           AND COALESCE(t.datainfo,'') NOT LIKE %s
           AND lower(COALESCE(t.unit,'')) NOT IN ('m³','m3','kwh','kg')
         GROUP BY r.tagsn, ts
        """,
        (region, str(window_days), "%Digital%", "%적산%", "%누적%", "%총량%"),
    )
    rows = cur.fetchall()
    cur.close()
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["tagsn", "ts", "y"])
    df["y"] = df["y"].astype(float)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)

    # 태그 메타 + site_group
    cur = conn.cursor()
    cur.execute(
        """
        SELECT t.tagsn, COALESCE(t.datainfo,''), COALESCE(t.unit,''),
               COALESCE(t.facilitytype,'-'), COALESCE(t.equipmenttype,'-'),
               COALESCE(p.site_group,'B')
          FROM tb_tag_info t
          LEFT JOIN tb_site_anomaly_profile p
                 ON p.sitename = t.sitename AND p.facilitytype = t.facilitytype
        """
    )
    meta = {}
    for tagsn, datainfo, unit, ft, et, sg in cur.fetchall():
        meta[tagsn] = {
            "trend_kind": _kind_from_text(datainfo, unit) or "other",
            "facilitytype": ft, "equipmenttype": et,
            "site_group": sg if sg in ("A", "B", "C", "D") else "B",
        }
    cur.close()

    df["trend_kind"] = df["tagsn"].map(lambda x: meta.get(x, {}).get("trend_kind", "other"))
    df["facilitytype"] = df["tagsn"].map(lambda x: meta.get(x, {}).get("facilitytype", "-"))
    df["equipmenttype"] = df["tagsn"].map(lambda x: meta.get(x, {}).get("equipmenttype", "-"))
    df["site_group"] = df["tagsn"].map(lambda x: meta.get(x, {}).get("site_group", "B"))
    return df


def _add_features(df):
    """캘린더 + 계절 lag 피처 추가 (그룹: tagsn, 정렬: ts)."""
    import pandas as pd

    df = df.sort_values(["tagsn", "ts"]).reset_index(drop=True)
    t = df["ts"].dt.tz_convert("Asia/Seoul")
    df["hour"] = t.dt.hour
    df["dow"] = t.dt.dayofweek
    df["month"] = t.dt.month
    df["is_weekend"] = (df["dow"] >= 5).astype(int)
    df["is_holiday"] = t.dt.strftime("%Y-%m-%d").isin(KR_HOLIDAYS).astype(int)
    df["hour_sin"] = np.sin(2 * np.pi * df["hour"] / 24)
    df["hour_cos"] = np.cos(2 * np.pi * df["hour"] / 24)
    df["dow_sin"] = np.sin(2 * np.pi * df["dow"] / 7)
    df["dow_cos"] = np.cos(2 * np.pi * df["dow"] / 7)

    # 시간 인덱스 기반 lag — 결측 시간 허용 위해 tagsn 별 reindex 없이 shift 근사
    # (집계 누락 시간이 적다는 가정; 정밀 lag 는 추론 단계에서 ts 매핑으로 처리)
    g = df.groupby("tagsn", sort=False)["y"]
    df["lag_24h"] = g.shift(24)
    df["lag_168h"] = g.shift(168)
    df["roll_mean_24h"] = g.shift(1).rolling(24, min_periods=6).mean().reset_index(level=0, drop=True)
    df["roll_std_24h"] = g.shift(1).rolling(24, min_periods=6).std().reset_index(level=0, drop=True)
    return df


def _encode_cats(encoder, df):
    """categorical → float code (unknown=nan → HGBR missing 처리)."""
    codes = encoder.transform(df[CAT_COLS].astype(str).values).astype(float)
    codes[codes < 0] = np.nan
    return codes


def _build_matrix(df, encoder):
    cat = _encode_cats(encoder, df)
    num = df[NUM_COLS].astype(float).values
    X = np.hstack([cat, num])
    return X


# ── 학습 ────────────────────────────────────────────────

def train(region: str = DEFAULT_REGION, window_days: int = WINDOW_DAYS) -> dict:
    """GBT baseline 학습 + 홀드아웃 평가 + 아티팩트 저장. 평가 지표 dict 반환."""
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.preprocessing import OrdinalEncoder

    conn = _connect()
    try:
        logger.info(f"[baseline] 학습 시작 region={region} window={window_days}d")
        df = _load_hourly_frame(conn, region, window_days)
        if df is None or df.empty:
            raise RuntimeError("학습 데이터 없음")

        df_full = df  # min-row 필터 전 — 데이터셋 요약(박제)용

        # 태그별 최소 행수 필터
        counts = df.groupby("tagsn")["y"].transform("count")
        df = df[counts >= MIN_TAG_ROWS].copy()
        if df.empty:
            raise RuntimeError("학습 가능 태그 없음 (데이터 부족)")

        df = _add_features(df)
        df = df.dropna(subset=["y"])

        # 시간순 홀드아웃
        cutoff = df["ts"].max() - timedelta(days=HOLDOUT_DAYS)
        train_df = df[df["ts"] <= cutoff]
        hold_df = df[df["ts"] > cutoff]
        if train_df.empty or hold_df.empty:
            raise RuntimeError("train/holdout 분할 실패")

        encoder = OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1)
        encoder.fit(train_df[CAT_COLS].astype(str).values)

        Xtr = _build_matrix(train_df, encoder)
        ytr = train_df["y"].values
        cat_mask = [True] * len(CAT_COLS) + [False] * len(NUM_COLS)

        model = HistGradientBoostingRegressor(
            loss="squared_error", max_iter=300, learning_rate=0.08,
            max_depth=8, l2_regularization=1.0,
            categorical_features=cat_mask, random_state=42,
        )
        model.fit(Xtr, ytr)

        # ── 홀드아웃 평가 (GBT) ──
        Xh = _build_matrix(hold_df, encoder)
        yh = hold_df["y"].values
        pred = model.predict(Xh)
        resid = yh - pred
        overall_mae = float(np.mean(np.abs(resid)))
        overall_rmse = float(np.sqrt(np.mean(resid ** 2)))

        # 태그별 σ·MAE·coverage
        tag_sigma: dict[str, float] = {}
        tag_metrics: list[dict] = []
        hold_df = hold_df.assign(pred=pred, resid=resid)
        for tagsn, grp in hold_df.groupby("tagsn"):
            r = grp["resid"].values
            sigma = float(np.std(r)) if len(r) > 1 else float(np.abs(r).mean() or 0.0)
            sigma = sigma if sigma > 1e-9 else max(1e-6, float(np.abs(r).mean()))
            mae = float(np.mean(np.abs(r)))
            rmse = float(np.sqrt(np.mean(r ** 2)))
            cover = float(np.mean(np.abs(r) <= 2 * sigma)) * 100 if sigma > 0 else 0.0
            # 자기 과거(lag) 피처 확보율 — 둘 다 있는 행 비율. 낮으면 모델이 자기
            # 이력 없이 prior 로 예측 → 오차의 데이터부족 기여를 검증 가능하게 박제.
            lag_ok = grp["lag_24h"].notna() & grp["lag_168h"].notna()
            lag_avail = float(lag_ok.mean()) * 100
            # 실제값 크기(scale) — 단위 무관 정확도 산출용. MAE 를 신호 크기로 나눠
            # 유량(LPS 수백)·수위(m 한자리)를 같은 정확도% 척도로 비교 가능하게 박제.
            y_scale = float(np.abs(grp["y"].values).mean())
            tag_sigma[tagsn] = sigma
            tag_metrics.append({
                "tagsn": tagsn, "mae": round(mae, 4), "rmse": round(rmse, 4),
                "sigma": round(sigma, 4), "coverage_pct": round(cover, 1),
                "n_samples": int(len(r)), "method": "gbt",
                "trend_kind": str(grp["trend_kind"].iloc[0]),
                "lag_avail_pct": round(lag_avail, 1),
                "y_scale": round(y_scale, 4),
            })
        global_sigma = float(np.median(list(tag_sigma.values()))) if tag_sigma else overall_rmse
        coverage_pct = float(np.mean([m["coverage_pct"] for m in tag_metrics])) if tag_metrics else 0.0

        # ── 비교군: hourly_mean (train 의 weekday×hour 평균) ──
        hm = (train_df.groupby(["tagsn", "dow", "hour"])["y"].mean()
              .rename("hm").reset_index())
        merged = hold_df.merge(hm, on=["tagsn", "dow", "hour"], how="left")
        merged["hm"] = merged["hm"].fillna(merged["y"].mean())
        mae_hm = float(np.mean(np.abs(merged["y"].values - merged["hm"].values)))
        improvement_pct = ((mae_hm - overall_mae) / mae_hm * 100) if mae_hm > 1e-9 else 0.0

        n_tags = int(hold_df["tagsn"].nunique())
        version = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        metrics = {
            "region": region, "model_version": version,
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "train_window_days": window_days,
            "n_tags_trained": n_tags, "n_tags_fallback": 0,
            "overall_mae": round(overall_mae, 4), "overall_rmse": round(overall_rmse, 4),
            "coverage_pct": round(coverage_pct, 1),
            "mae_hourly_mean": round(mae_hm, 4),
            "improvement_pct": round(improvement_pct, 1),
            "feature_set": FEATURE_SET, "status": "ok",
            "dataset_summary": {
                **_dataset_summary(conn, df_full),
                "lag24_avail_pct": round(float(df["lag_24h"].notna().mean()) * 100, 1),
                "lag168_avail_pct": round(float(df["lag_168h"].notna().mean()) * 100, 1),
            },
        }

        _save_artifact(region, {
            "model": model, "encoder": encoder,
            "cat_cols": CAT_COLS, "num_cols": NUM_COLS, "cat_mask": cat_mask,
            "feature_set": FEATURE_SET, "region": region,
            "model_version": version,
            "trained_at": metrics["trained_at"], "window_days": window_days,
            "tag_sigma": tag_sigma, "global_sigma": global_sigma,
        }, metrics, tag_metrics)

        # P2 지표 적재 (테이블 없거나 실패해도 학습은 성공으로 간주)
        try:
            _persist_metrics(conn, metrics, tag_metrics)
        except Exception as e:
            logger.warning(f"[baseline] 지표 적재 건너뜀: {e}")

        logger.info(
            f"[baseline] 완료 v={version} tags={n_tags} MAE={overall_mae:.3f} "
            f"(hm={mae_hm:.3f}, 개선 {improvement_pct:+.1f}%) cover={coverage_pct:.1f}%"
        )
        return {"metrics": metrics, "tag_metrics": tag_metrics}
    finally:
        conn.close()


def _dataset_summary(conn, df_full) -> dict:
    """학습 회차의 데이터셋 요약 — 회차별 박제용 (§2 데이터셋 현황).

    df_full = min-row 필터 전 전체 시간집계 프레임. 회차마다 "어떤 데이터로
    학습됐나" 를 동결 기록 → 평가 화면에서 추세 확인.
    """
    per_tag = df_full.groupby("tagsn")["y"].count()
    tag_meta = df_full.drop_duplicates("tagsn")
    by_trend_kind = {k: int(v) for k, v in tag_meta["trend_kind"].value_counts().items()}
    by_facilitytype = {k: int(v) for k, v in tag_meta["facilitytype"].value_counts().head(8).items()}

    ts_min, ts_max = df_full["ts"].min(), df_full["ts"].max()
    summary = {
        "data_start": ts_min.isoformat(),
        "data_end": ts_max.isoformat(),
        "span_days": round((ts_max - ts_min).total_seconds() / 86400, 1),
        "n_hourly_rows": int(len(df_full)),
        "n_tags_frame": int(per_tag.size),
        "n_tags_trained": int((per_tag >= MIN_TAG_ROWS).sum()),
        "n_tags_below_min": int((per_tag < MIN_TAG_ROWS).sum()),
        "min_tag_rows": MIN_TAG_ROWS,
        "by_trend_kind": by_trend_kind,
        "by_facilitytype": by_facilitytype,
    }

    # skip 사유별 태그 수 (학습 제외 — _load_hourly_frame 의 WHERE 와 동일 규칙)
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT
              COUNT(*) FILTER (WHERE t.tagtype LIKE %s) AS digital,
              COUNT(*) FILTER (WHERE t.tagtype NOT LIKE %s AND (
                    COALESCE(t.datainfo,'') LIKE %s OR COALESCE(t.datainfo,'') LIKE %s
                    OR COALESCE(t.datainfo,'') LIKE %s)) AS accum,
              COUNT(*) FILTER (WHERE t.tagtype NOT LIKE %s
                    AND lower(COALESCE(t.unit,'')) IN ('m³','m3','kwh','kg')) AS unit
              FROM tb_tag_info t
            """,
            ("%Digital%", "%Digital%", "%적산%", "%누적%", "%총량%", "%Digital%"),
        )
        d, a, u = cur.fetchone()
        summary["skip"] = {"digital": int(d or 0), "accum": int(a or 0), "unit": int(u or 0)}
    except Exception as e:
        logger.debug(f"[baseline] skip 집계 건너뜀: {e}")
        summary["skip"] = {}
    finally:
        cur.close()
    return summary


def _persist_metrics(conn, metrics: dict, tag_metrics: list[dict]):
    """회차/태그 지표를 tb_baseline_model_run / tb_baseline_tag_metric 에 적재 (§6.2)."""
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO tb_baseline_model_run
              (region, model_version, trained_at, train_window_days,
               n_tags_trained, n_tags_fallback, overall_mae, overall_rmse,
               coverage_pct, mae_hourly_mean, improvement_pct, feature_set, status,
               dataset_summary)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
            ON CONFLICT (region, model_version) DO UPDATE SET
               trained_at=EXCLUDED.trained_at, overall_mae=EXCLUDED.overall_mae,
               overall_rmse=EXCLUDED.overall_rmse, coverage_pct=EXCLUDED.coverage_pct,
               mae_hourly_mean=EXCLUDED.mae_hourly_mean,
               improvement_pct=EXCLUDED.improvement_pct, status=EXCLUDED.status,
               dataset_summary=EXCLUDED.dataset_summary
            """,
            (metrics["region"], metrics["model_version"], metrics["trained_at"],
             metrics["train_window_days"], metrics["n_tags_trained"],
             metrics["n_tags_fallback"], metrics["overall_mae"], metrics["overall_rmse"],
             metrics["coverage_pct"], metrics["mae_hourly_mean"],
             metrics["improvement_pct"], metrics["feature_set"], metrics["status"],
             json.dumps(metrics.get("dataset_summary") or {}, ensure_ascii=False)),
        )
        region, version = metrics["region"], metrics["model_version"]
        rows = [
            (region, version, m["tagsn"], m["mae"], m["rmse"], m["sigma"],
             m["coverage_pct"], m["n_samples"], m["method"], m.get("trend_kind"),
             m.get("lag_avail_pct"), m.get("y_scale"))
            for m in tag_metrics
        ]
        if rows:
            cur.executemany(
                """
                INSERT INTO tb_baseline_tag_metric
                  (region, model_version, tagsn, mae, rmse, sigma,
                   coverage_pct, n_samples, method, trend_kind, lag_avail_pct,
                   y_scale)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (region, model_version, tagsn) DO NOTHING
                """,
                rows,
            )
        conn.commit()
    finally:
        cur.close()


def _save_artifact(region: str, payload: dict, metrics: dict, tag_metrics: list[dict]):
    os.makedirs(MODEL_DIR, exist_ok=True)
    os.makedirs(os.path.join(MODEL_DIR, "archive"), exist_ok=True)
    pkl = os.path.join(MODEL_DIR, f"baseline_gbt_{region}.pkl")
    meta = os.path.join(MODEL_DIR, f"baseline_gbt_{region}_meta.json")
    arch = os.path.join(MODEL_DIR, "archive", f"baseline_gbt_{region}_{payload['model_version']}.pkl")
    with open(pkl, "wb") as f:
        pickle.dump(payload, f)
    with open(arch, "wb") as f:
        pickle.dump(payload, f)
    meta_out = {**metrics, "n_tag_metrics": len(tag_metrics)}
    with open(meta, "w", encoding="utf-8") as f:
        json.dump(meta_out, f, ensure_ascii=False, indent=2)
    _ARTIFACT_CACHE.pop(region, None)


# ── 추론 ────────────────────────────────────────────────

_ARTIFACT_CACHE: dict[str, tuple[float, dict]] = {}


def _load_artifact(region: str) -> Optional[dict]:
    pkl = os.path.join(MODEL_DIR, f"baseline_gbt_{region}.pkl")
    if not os.path.exists(pkl):
        return None
    mtime = os.path.getmtime(pkl)
    cached = _ARTIFACT_CACHE.get(region)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with open(pkl, "rb") as f:
            payload = pickle.load(f)
        _ARTIFACT_CACHE[region] = (mtime, payload)
        return payload
    except Exception as e:
        logger.warning(f"[baseline] 아티팩트 로드 실패: {e}")
        return None


def gbt_baseline(conn, tagsn: str, target_times: list, region: str = DEFAULT_REGION):
    """GBT 정상 기대값 + ±2σ 밴드. 반환 형식은 _hourly_pattern_baseline 과 동일.

    Returns (baseline_series, band_upper, band_lower, mean_sigma, model_version)
    또는 None (모델 없음/데이터 부족 → 호출부가 hourly_mean 폴백).
    """
    import pandas as pd

    if not target_times:
        return None
    art = _load_artifact(region)
    if art is None:
        return None

    # 태그 메타
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COALESCE(t.datainfo,''), COALESCE(t.unit,''),
               COALESCE(t.facilitytype,'-'), COALESCE(t.equipmenttype,'-'),
               COALESCE(p.site_group,'B')
          FROM tb_tag_info t
          LEFT JOIN tb_site_anomaly_profile p
                 ON p.sitename = t.sitename AND p.facilitytype = t.facilitytype
         WHERE t.tagsn = %s LIMIT 1
        """,
        (tagsn,),
    )
    m = cur.fetchone()
    cur.close()
    if not m:
        return None
    trend_kind = _kind_from_text(m[0], m[1]) or "other"
    facilitytype, equipmenttype = m[2], m[3]
    site_group = m[4] if m[4] in ("A", "B", "C", "D") else "B"

    # lag 계산용 시간 집계 (target 범위 - 168h ~ max)
    tt = [t if isinstance(t, datetime) else pd.to_datetime(t).to_pydatetime() for t in target_times]
    tt = [t if t.tzinfo else t.replace(tzinfo=timezone.utc) for t in tt]
    tmin, tmax = min(tt), max(tt)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT date_trunc('hour', logtime) AS h, AVG(val) AS y
          FROM tb_tag_raw_data
         WHERE tagsn = %s AND region = %s
           AND logtime BETWEEN %s AND %s AND val IS NOT NULL
         GROUP BY h
        """,
        (tagsn, region, tmin - timedelta(hours=170), tmax),
    )
    hourly = {pd.Timestamp(h).tz_convert("UTC") if pd.Timestamp(h).tzinfo
              else pd.Timestamp(h, tz="UTC"): float(y) for h, y in cur.fetchall() if y is not None}
    cur.close()
    if len(hourly) < 24:
        return None

    def _at(ts):
        return hourly.get(pd.Timestamp(ts).floor("h").tz_convert("UTC")
                          if pd.Timestamp(ts).tzinfo else pd.Timestamp(ts, tz="UTC").floor("h"))

    def _roll(ts, fn):
        end = pd.Timestamp(ts).floor("h")
        end = end.tz_convert("UTC") if end.tzinfo else end.tz_localize("UTC")
        vals = [v for k, v in hourly.items() if end - pd.Timedelta(hours=24) <= k < end]
        return fn(vals) if len(vals) >= 6 else np.nan

    rows = []
    for t in tt:
        tl = pd.Timestamp(t).tz_convert("Asia/Seoul")
        rows.append({
            "trend_kind": trend_kind, "facilitytype": facilitytype,
            "equipmenttype": equipmenttype, "site_group": site_group,
            "hour": tl.hour, "dow": tl.dayofweek, "month": tl.month,
            "is_weekend": int(tl.dayofweek >= 5),
            "is_holiday": int(tl.strftime("%Y-%m-%d") in KR_HOLIDAYS),
            "hour_sin": np.sin(2 * np.pi * tl.hour / 24),
            "hour_cos": np.cos(2 * np.pi * tl.hour / 24),
            "dow_sin": np.sin(2 * np.pi * tl.dayofweek / 7),
            "dow_cos": np.cos(2 * np.pi * tl.dayofweek / 7),
            "lag_24h": _at(t - timedelta(hours=24)),
            "lag_168h": _at(t - timedelta(hours=168)),
            "roll_mean_24h": _roll(t, lambda v: float(np.mean(v))),
            "roll_std_24h": _roll(t, lambda v: float(np.std(v))),
        })
    fdf = pd.DataFrame(rows)
    X = _build_matrix(fdf, art["encoder"])
    pred = art["model"].predict(X)

    sigma = art["tag_sigma"].get(tagsn, art["global_sigma"])
    series = [round(float(p), 3) for p in pred]
    upper = [round(float(p + 2 * sigma), 3) for p in pred]
    lower = [round(float(p - 2 * sigma), 3) for p in pred]
    return series, upper, lower, float(sigma), art.get("model_version")


# ── CLI ─────────────────────────────────────────────────

def _main():
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="트렌드 GBT baseline 학습")
    ap.add_argument("cmd", choices=["train"], help="명령")
    ap.add_argument("--region", default=DEFAULT_REGION)
    ap.add_argument("--window-days", type=int, default=WINDOW_DAYS)
    args = ap.parse_args()
    if args.cmd == "train":
        res = train(args.region, args.window_days)
        print(json.dumps(res["metrics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
