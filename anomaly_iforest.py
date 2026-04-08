"""
anomaly_iforest.py  v2
Isolation Forest 기반 이상 감지 모듈

변경 (v2):
  - Tier-1: 시설 단위 다변량 모델 — (sitename, facilitytype) 상관 센서 묶어 학습
  - Tier-2: 태그 단변량 모델    — Tier-1 미포함 태그 fallback (기존 방식)

물리적 상관 관계 기반 feature vector:
  가압장:   [토출압력, 유출유량,            hour, dow]
  배수지:   [수위, 유출유량, (유입유량),    hour, dow]
  감압시설: [유입압력, 유출압력,            hour, dow]
  소블록:   [유량순시, 압력,                hour, dow]
  소소블록: [유량순시, 압력,                hour, dow]
  저수지:   [수위, 유출유량, (유입유량),    hour, dow]

Tier-2 feature vector (기존):
  [approx_avg, hour_of_day, day_of_week, rate_of_change]
"""

import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
from sklearn.ensemble import IsolationForest

logger = logging.getLogger(__name__)

# ── 학습 설정 ────────────────────────────────────────────
RETRAIN_INTERVAL_HOURS = 24
MIN_SAMPLES_PER_TAG = 100       # Tier-2 최소 샘플
MIN_FACILITY_TIMESTAMPS = 80    # Tier-1 최소 공동 관측 타임스탬프
CONTAMINATION = 0.05
N_ESTIMATORS = 100
RANDOM_STATE = 42

GROUP_CONTAMINATION = {"A": 0.03, "B": 0.05, "C": 0.08, "D": 0.05}

# ── datainfo 키워드 → group_code ──────────────────────────
# 긴 키워드가 앞에 와야 한다 (유출유량 > 유량, 토출압력 > 압력)
DATAINFO_TO_GROUP: list[tuple[str, str]] = [
    ("유출유량",   "FLOW_OUTLET"),
    ("유입유량",   "FLOW_INLET"),
    ("유량순시",   "FLOW_INSTANT"),
    ("토출압력",   "PRESSURE_DISCHARGE"),
    ("유출압력",   "PRESSURE_OUTLET"),
    ("유입압력",   "PRESSURE_INLET"),
    ("수위",       "WATER_LEVEL"),
    ("압력",       "PRESSURE"),
    ("유량",       "FLOW"),
]

# ── 시설 유형별 센서 스키마 ───────────────────────────────
# required: 전부 있어야 Tier-1 모델 생성 및 예측 가능
# optional: 있으면 feature에 추가 (없어도 모델 생성)
FACILITY_SENSOR_SCHEMA: dict[str, dict] = {
    "가압장":   {"required": ["PRESSURE_DISCHARGE", "FLOW_OUTLET"],   "optional": ["PRESSURE_INLET"]},
    "배수지":   {"required": ["WATER_LEVEL",         "FLOW_OUTLET"],   "optional": ["FLOW_INLET"]},
    "감압시설": {"required": ["PRESSURE_INLET",      "PRESSURE_OUTLET"], "optional": []},
    "소블록":   {"required": ["FLOW_INSTANT",         "PRESSURE"],      "optional": []},
    "소소블록": {"required": ["FLOW_INSTANT",         "PRESSURE"],      "optional": []},
    "저수지":   {"required": ["WATER_LEVEL",          "FLOW_OUTLET"],   "optional": ["FLOW_INLET"]},
}

# ── SQL ───────────────────────────────────────────────────

# Tier-1: facilitytype 대상, 30일 cagg 전체 반환 → Python에서 pivot
_TRAIN_SQL_FACILITY = """
SELECT
    ti.sitename,
    ti.facilitytype,
    time_bucket('5 minutes', c.bucket) AS ts,
    ti.datainfo,
    (c.min_val + c.max_val) / 2.0     AS approx_avg
FROM cagg_5min_raw_stats_ai c
JOIN tb_tag_info ti ON c.tagsn = ti.tagsn
WHERE c.bucket >= now() - interval '30 days'
  AND ti.tagtype = 'Analog Input'
  AND ti.datainfo NOT LIKE '%%적산%%'
  AND ti.facilitytype IN (
      '가압장', '배수지', '감압시설', '소블록', '소소블록', '저수지'
  )
ORDER BY ti.sitename, ti.facilitytype, ts
"""

# Tier-2: 태그별 단변량 (기존)
_TRAIN_SQL_TAG = """
SELECT c.tagsn, c.bucket,
    (c.min_val + c.max_val) / 2.0 AS approx_avg,
    ti.sitename, ti.facilitytype
FROM cagg_5min_raw_stats_ai c
JOIN tb_tag_info ti ON c.tagsn = ti.tagsn
WHERE c.bucket >= now() - interval '30 days'
    AND ti.tagtype = 'Analog Input'
    AND ti.datainfo NOT LIKE '%%적산%%'
ORDER BY c.tagsn, c.bucket
"""


# ── 데이터 클래스 ─────────────────────────────────────────

@dataclass
class FacilityModel:
    """Tier-1 — 시설 단위 다변량 IF 모델."""
    sitename: str
    facilitytype: str
    feature_cols: list[str]      # 학습 시 확정된 group_code 순서
    model: IsolationForest
    trained_at: datetime = field(default_factory=datetime.now)

    def predict(
        self,
        sensor_vals: dict[str, float],
        hour: int,
        dow: int,
    ) -> Optional[dict]:
        """
        Args:
            sensor_vals: {group_code: 현재값}
        Returns:
            {is_anomaly, anomaly_score, features_used, tier} or None
        """
        schema = FACILITY_SENSOR_SCHEMA.get(self.facilitytype, {})
        required = schema.get("required", [])

        # 필수 센서 누락 또는 비가동이면 예측 불가
        for gc in required:
            if abs(sensor_vals.get(gc, 0.0)) < 0.001:
                return None

        vec = [sensor_vals.get(gc, 0.0) for gc in self.feature_cols]
        vec.extend([hour, dow])

        arr = np.array([vec])
        prediction = self.model.predict(arr)[0]   # -1=이상, 1=정상
        score = float(self.model.score_samples(arr)[0])

        return {
            "is_anomaly": prediction == -1,
            "anomaly_score": score,
            "features_used": self.feature_cols,
            "tier": 1,
        }


# ── 유틸 ──────────────────────────────────────────────────

def _datainfo_to_group(datainfo: str) -> Optional[str]:
    """datainfo 문자열에서 group_code를 추출한다 (첫 매칭)."""
    for keyword, gc in DATAINFO_TO_GROUP:
        if keyword in (datainfo or ""):
            return gc
    return None


def _build_facility_matrix(
    ts_data: dict[str, dict[str, float]],
    feature_cols: list[str],
    required: set[str],
) -> Optional[np.ndarray]:
    """
    ts_data: {ts_str → {group_code → value}}
    Returns: (N, len(feature_cols)+2) ndarray or None if 샘플 부족
    """
    rows = []
    for ts_str, gc_vals in ts_data.items():
        # 필수 센서 전부 있고 비가동 아닌지 확인
        if not required.issubset(gc_vals.keys()):
            continue
        if any(abs(gc_vals.get(gc, 0.0)) < 0.001 for gc in required):
            continue
        try:
            ts = datetime.fromisoformat(str(ts_str))
        except Exception:
            continue
        row = [gc_vals.get(gc, 0.0) for gc in feature_cols]
        row.extend([ts.hour, ts.weekday()])
        rows.append(row)

    if len(rows) < MIN_FACILITY_TIMESTAMPS:
        return None
    return np.array(rows, dtype=float)


# ── 메인 매니저 ───────────────────────────────────────────

class IForestManager:
    """Tier-1(시설 다변량) + Tier-2(태그 단변량) IF 통합 관리."""

    def __init__(self):
        # Tier-1: (sitename, facilitytype) → FacilityModel
        self.facility_models: dict[tuple[str, str], FacilityModel] = {}
        # Tier-2: tagsn → IsolationForest
        self.tag_models: dict[str, IsolationForest] = {}
        # Tier-1이 커버하는 (sitename, facilitytype) 집합
        self._tier1_covered: set[tuple[str, str]] = set()

        self.last_trained: Optional[datetime] = None
        self._lock = threading.Lock()
        self._is_training = False

    # ── 공개 속성 ────────────────────────────────────────

    @property
    def model_count(self) -> int:
        return len(self.facility_models) + len(self.tag_models)

    @property
    def is_trained(self) -> bool:
        return self.last_trained is not None and self.model_count > 0

    # ── 학습 진입점 ──────────────────────────────────────

    def ensure_trained(self, get_connection_fn,
                       site_profiles: Optional[dict] = None) -> None:
        if not self._needs_retrain() or self._is_training:
            return
        self.train_all(get_connection_fn, site_profiles=site_profiles)

    def train_all(self, get_connection_fn,
                  site_profiles: Optional[dict] = None) -> None:
        with self._lock:
            if self._is_training:
                return
            self._is_training = True

        try:
            logger.info("IForest v2 학습 시작 (Tier-1 + Tier-2)...")
            conn = get_connection_fn()

            tier1 = self._train_facility_models(conn, site_profiles)
            tier2 = self._train_tag_models(conn, site_profiles, covered=set(tier1.keys()))

            conn.close()

            with self._lock:
                self.facility_models = tier1
                self.tag_models = tier2
                self._tier1_covered = set(tier1.keys())
                self.last_trained = datetime.now()

            logger.info(
                "IForest v2 학습 완료: Tier-1 %d개 시설, Tier-2 %d개 태그",
                len(tier1), len(tier2),
            )

        except Exception as e:
            logger.error("IForest 학습 실패: %s", e)
        finally:
            with self._lock:
                self._is_training = False

    # ── Tier-1 학습 ──────────────────────────────────────

    def _train_facility_models(
        self, conn, site_profiles: Optional[dict]
    ) -> dict[tuple[str, str], FacilityModel]:
        cursor = conn.cursor()
        cursor.execute(_TRAIN_SQL_FACILITY)
        rows = cursor.fetchall()
        cursor.close()
        logger.info("Tier-1 원시 데이터: %d행", len(rows))

        # (sitename, facilitytype, ts) → {group_code → value} pivot
        # 동일 gc가 여러 태그면 최댓값 사용 (펌프 병렬 운전 등)
        pivot: dict[tuple, dict] = defaultdict(dict)
        for sitename, facilitytype, ts, datainfo, approx_avg in rows:
            gc = _datainfo_to_group(datainfo or "")
            if gc is None:
                continue
            key = (sitename, facilitytype, ts)
            val = float(approx_avg or 0)
            pivot[key][gc] = max(pivot[key].get(gc, 0.0), val)

        # (sitename, facilitytype) 단위로 재그룹
        facility_pivot: dict[tuple, dict] = defaultdict(dict)
        for (sitename, facilitytype, ts), gc_vals in pivot.items():
            facility_pivot[(sitename, facilitytype)][str(ts)] = gc_vals

        models: dict[tuple, FacilityModel] = {}

        for (sitename, facilitytype), ts_data in facility_pivot.items():
            schema = FACILITY_SENSOR_SCHEMA.get(facilitytype)
            if schema is None:
                continue

            required_list = schema["required"]
            optional_list = schema["optional"]

            # 실제 데이터에 등장한 optional만 포함
            all_gc = set(gc for gv in ts_data.values() for gc in gv)
            active_optional = [gc for gc in optional_list if gc in all_gc]
            feature_cols = required_list + active_optional
            required_set = set(required_list)

            X = _build_facility_matrix(ts_data, feature_cols, required_set)
            if X is None:
                logger.debug(
                    "Tier-1 스킵 (%s/%s): 유효 샘플 부족 (필요 %d)",
                    sitename, facilitytype, MIN_FACILITY_TIMESTAMPS,
                )
                continue

            contam = _get_contamination(
                sitename, facilitytype, site_profiles
            )
            model = IsolationForest(
                contamination=contam,
                n_estimators=N_ESTIMATORS,
                random_state=RANDOM_STATE,
                n_jobs=1,
            )
            model.fit(X)
            models[(sitename, facilitytype)] = FacilityModel(
                sitename=sitename,
                facilitytype=facilitytype,
                feature_cols=feature_cols,
                model=model,
            )
            logger.debug(
                "Tier-1 학습 완료: %s/%s  features=%s  N=%d  contam=%.2f",
                sitename, facilitytype, feature_cols, len(X), contam,
            )

        return models

    # ── Tier-2 학습 ──────────────────────────────────────

    def _train_tag_models(
        self, conn, site_profiles: Optional[dict],
        covered: set[tuple[str, str]],
    ) -> dict[str, IsolationForest]:
        cursor = conn.cursor()
        cursor.execute(_TRAIN_SQL_TAG)
        rows = cursor.fetchall()
        cursor.close()
        logger.info("Tier-2 원시 데이터: %d행", len(rows))

        tag_data: dict[str, list] = defaultdict(list)
        tag_site: dict[str, tuple] = {}
        for tagsn, bucket, avg_val, sitename, facilitytype in rows:
            tag_data[tagsn].append((bucket, float(avg_val or 0)))
            tag_site.setdefault(tagsn, (sitename or "", facilitytype or ""))

        models: dict[str, IsolationForest] = {}
        skipped = 0

        for tagsn, data_points in tag_data.items():
            site_key = tag_site.get(tagsn, ("", ""))

            # Tier-1이 커버하는 시설은 Tier-2 학습 생략
            if site_key in covered:
                continue

            X = self._build_tag_features(data_points)
            if X is None or len(X) < MIN_SAMPLES_PER_TAG:
                skipped += 1
                continue

            contam = _get_contamination(
                site_key[0], site_key[1], site_profiles
            )
            model = IsolationForest(
                contamination=contam,
                n_estimators=N_ESTIMATORS,
                random_state=RANDOM_STATE,
                n_jobs=1,
            )
            model.fit(X)
            models[tagsn] = model

        logger.info("Tier-2 학습 완료: %d개 모델, %d개 스킵", len(models), skipped)
        return models

    # ── 예측 ─────────────────────────────────────────────

    def predict_for_rows(self, rows: list, columns: list) -> dict:
        """
        SQL 결과 rows에 대해 Tier-1 우선, 미포함 태그는 Tier-2 판정.

        Returns:
            {
                tag_results:      {tagsn: {is_anomaly, anomaly_score, tier}},
                facility_results: {"sitename/facilitytype": {is_anomaly, ...}},
                total, if_anomaly_count, z_and_if_agree,
                tier1_count, tier2_count,
            }
        """
        if not self.is_trained:
            return {}

        col = {c: i for i, c in enumerate(columns)}
        if "tagsn" not in col or "current_val" not in col:
            return {}

        now = datetime.now()
        hour, dow = now.hour, now.weekday()

        tagsn_idx = col["tagsn"]
        val_idx   = col["current_val"]
        sn_idx    = col.get("sitename")
        ft_idx    = col.get("facilitytype")
        di_idx    = col.get("datainfo")
        z_idx     = col.get("z_score")

        # ── Tier-1: 시설별 현재 센서값 수집 ─────────────
        facility_sensors: dict[tuple, dict[str, float]] = defaultdict(dict)
        if sn_idx is not None and ft_idx is not None and di_idx is not None:
            for row in rows:
                sn = row[sn_idx] or ""
                ft = row[ft_idx] or ""
                di = row[di_idx] or ""
                gc = _datainfo_to_group(di)
                if gc and sn and ft:
                    val = float(row[val_idx] or 0)
                    existing = facility_sensors[(sn, ft)].get(gc, 0.0)
                    facility_sensors[(sn, ft)][gc] = max(val, existing)

        # ── Tier-1 예측 ───────────────────────────────────
        facility_results: dict[str, dict] = {}
        tier1_covered_fkeys: set[tuple] = set()

        for fkey, sensor_vals in facility_sensors.items():
            fm = self.facility_models.get(fkey)
            if fm is None:
                continue
            result = fm.predict(sensor_vals, hour, dow)
            if result is not None:
                label = f"{fkey[0]}/{fkey[1]}"
                facility_results[label] = result
                tier1_covered_fkeys.add(fkey)

        # ── Tier-2 예측 (Tier-1 미커버 태그만) ───────────
        tag_results: dict[str, dict] = {}
        for row in rows:
            tagsn = row[tagsn_idx]
            sn = row[sn_idx] or "" if sn_idx is not None else ""
            ft = row[ft_idx] or "" if ft_idx is not None else ""
            if (sn, ft) in tier1_covered_fkeys:
                continue

            model = self.tag_models.get(tagsn)
            if model is None:
                continue

            val = float(row[val_idx] or 0)
            result = self._predict_tag(model, val, hour, dow)
            tag_results[tagsn] = result

        # ── 이상 집계 ─────────────────────────────────────
        if_anomaly_count = 0
        z_and_if_agree = 0

        anomalous_fkeys = {
            (label.split("/")[0], label.split("/")[1])
            for label, r in facility_results.items()
            if r["is_anomaly"]
        }
        for row in rows:
            sn = row[sn_idx] or "" if sn_idx is not None else ""
            ft = row[ft_idx] or "" if ft_idx is not None else ""
            tagsn = row[tagsn_idx]
            is_anomaly = False

            if (sn, ft) in anomalous_fkeys:
                is_anomaly = True
            elif tagsn in tag_results and tag_results[tagsn]["is_anomaly"]:
                is_anomaly = True

            if is_anomaly:
                if_anomaly_count += 1
                if z_idx is not None and abs(float(row[z_idx] or 0)) >= 2.0:
                    z_and_if_agree += 1

        return {
            "tag_results":      tag_results,
            "facility_results": facility_results,
            "total":            len(facility_results) + len(tag_results),
            "if_anomaly_count": if_anomaly_count,
            "z_and_if_agree":   z_and_if_agree,
            "tier1_count":      len(facility_results),
            "tier2_count":      len(tag_results),
        }

    def predict_facility(
        self,
        sitename: str,
        facilitytype: str,
        sensor_vals: dict[str, float],
        hour: Optional[int] = None,
        dow: Optional[int] = None,
    ) -> Optional[dict]:
        """ANOMALY_FACILITY_DETAIL에서 단일 시설 직접 예측."""
        fm = self.facility_models.get((sitename, facilitytype))
        if fm is None:
            return None
        now = datetime.now()
        return fm.predict(
            sensor_vals,
            now.hour if hour is None else hour,
            now.weekday() if dow is None else dow,
        )

    def predict_single(
        self, tagsn: str, current_val: float,
        hour: int, dow: int, roc: float = 0.0,
    ) -> Optional[dict]:
        """Tier-2 단일 태그 예측 (하위 호환)."""
        model = self.tag_models.get(tagsn)
        if model is None:
            return None
        return self._predict_tag(model, current_val, hour, dow, roc)

    def get_status(self) -> dict:
        return {
            "last_trained":    self.last_trained.isoformat() if self.last_trained else None,
            "tier1_facilities": [
                {"sitename": k[0], "facilitytype": k[1], "features": v.feature_cols}
                for k, v in self.facility_models.items()
            ],
            "tier2_tag_count": len(self.tag_models),
            "total":           self.model_count,
        }

    # ── 내부 헬퍼 ────────────────────────────────────────

    def _needs_retrain(self) -> bool:
        if not self.last_trained:
            return True
        return datetime.now() - self.last_trained > timedelta(hours=RETRAIN_INTERVAL_HOURS)

    def _predict_tag(
        self, model: IsolationForest,
        val: float, hour: int, dow: int, roc: float = 0.0,
    ) -> dict:
        arr = np.array([[val, hour, dow, roc]])
        return {
            "is_anomaly":    model.predict(arr)[0] == -1,
            "anomaly_score": float(model.score_samples(arr)[0]),
            "tier":          2,
        }

    def _build_tag_features(self, data_points: list) -> Optional[np.ndarray]:
        """Tier-2: 시계열 → 4-feature 배열."""
        if len(data_points) < 2:
            return None
        features = []
        prev_val = data_points[0][1]
        for i, (bucket, val) in enumerate(data_points):
            if i == 0:
                prev_val = val
                continue
            if abs(val) < 0.001:
                prev_val = val
                continue
            features.append([val, bucket.hour, bucket.weekday(), val - prev_val])
            prev_val = val
        return np.array(features) if features else None


# ── 모듈 수준 헬퍼 ────────────────────────────────────────

def _get_contamination(
    sitename: str, facilitytype: str,
    site_profiles: Optional[dict],
) -> float:
    if not site_profiles:
        return CONTAMINATION
    profile = site_profiles.get((sitename, facilitytype))
    if not profile:
        return CONTAMINATION
    grp = profile.get("site_group", "B")
    return GROUP_CONTAMINATION.get(grp, CONTAMINATION)
