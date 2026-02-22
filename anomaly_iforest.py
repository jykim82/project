"""
anomaly_iforest.py
Isolation Forest 기반 이상 감지 모듈

- 태그별 4-feature 벡터로 IsolationForest 학습
- 30일 cagg 데이터 기반 학습, 24시간마다 재학습
- ANOMALY_SCAN_ALL / ANOMALY_FACILITY_DETAIL 결과에 ML 판정 보강

특징값 (4 features):
  1. approx_avg — 5분 평균값
  2. hour_of_day — 시간대 (0~23, 주야간 패턴)
  3. day_of_week — 요일 (0~6, 주말 패턴)
  4. rate_of_change — 직전 5분 대비 변화량
"""

import logging
import threading
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
from sklearn.ensemble import IsolationForest

logger = logging.getLogger(__name__)

# ── 학습 설정 ────────────────────────────────────────────
RETRAIN_INTERVAL_HOURS = 24
MIN_SAMPLES_PER_TAG = 100    # 학습에 필요한 최소 샘플 수
CONTAMINATION = 0.05          # 이상치 비율 가정 (5%, 기본값)
N_ESTIMATORS = 100
RANDOM_STATE = 42

# 그룹별 contamination (고부하-불안정은 완화, 저부하-불안정은 강화)
GROUP_CONTAMINATION = {"A": 0.03, "B": 0.05, "C": 0.08, "D": 0.05}

# 학습용 SQL: 30일 cagg + Analog Input (적산 제외) + 사이트 매핑
_TRAIN_SQL = """
SELECT c.tagsn, c.bucket,
    (c.min_val + c.max_val) / 2.0 AS approx_avg,
    ti.sitename, ti.facilitytype
FROM cagg_5min_raw_stats_ai c
JOIN tb_tag_info ti ON c.tagsn = ti.tagsn
WHERE c.bucket >= now() - interval '30 days'
    AND ti.tagtype = 'Analog Input'
    AND ti.datainfo NOT LIKE '%적산%'
ORDER BY c.tagsn, c.bucket
"""


class IForestManager:
    """태그별 Isolation Forest 모델을 관리한다."""

    def __init__(self):
        self.models: dict = {}           # tagsn → fitted IsolationForest
        self.last_trained: Optional[datetime] = None
        self._lock = threading.Lock()
        self._is_training = False

    # ── 공개 메서드 ──────────────────────────────────────

    @property
    def model_count(self) -> int:
        return len(self.models)

    @property
    def is_trained(self) -> bool:
        return self.last_trained is not None and len(self.models) > 0

    def ensure_trained(self, get_connection_fn,
                        site_profiles: Optional[dict] = None) -> None:
        """모델이 없거나 재학습 주기가 지나면 학습을 실행한다."""
        if not self._needs_retrain():
            return
        if self._is_training:
            return
        self.train_all(get_connection_fn, site_profiles=site_profiles)

    def train_all(self, get_connection_fn,
                  site_profiles: Optional[dict] = None) -> None:
        """30일 cagg 데이터로 전 Analog 태그 모델을 학습한다.
        site_profiles가 제공되면 태그 소속 그룹의 contamination을 적용한다.
        """
        with self._lock:
            if self._is_training:
                return
            self._is_training = True

        try:
            logger.info("IForest 학습 시작...")
            conn = get_connection_fn()
            cursor = conn.cursor()
            cursor.execute(_TRAIN_SQL)
            rows = cursor.fetchall()
            cursor.close()
            conn.close()
            logger.info(f"IForest 학습 데이터: {len(rows)}행 로드")

            # 태그별로 시계열 그룹핑 + 사이트 매핑
            tag_data: dict[str, list] = {}
            tag_site: dict[str, tuple] = {}  # tagsn → (sitename, facilitytype)
            for tagsn, bucket, avg_val, sitename, facilitytype in rows:
                if tagsn not in tag_data:
                    tag_data[tagsn] = []
                    tag_site[tagsn] = (sitename or "", facilitytype or "")
                tag_data[tagsn].append((bucket, float(avg_val or 0)))

            # 태그별 모델 학습 (그룹별 contamination 적용)
            new_models = {}
            skipped = 0
            for tagsn, data_points in tag_data.items():
                features = self._build_features(data_points)
                if features is None or len(features) < MIN_SAMPLES_PER_TAG:
                    skipped += 1
                    continue

                # 그룹별 contamination 결정
                contam = CONTAMINATION
                if site_profiles:
                    site_key = tag_site.get(tagsn)
                    if site_key:
                        profile = site_profiles.get(site_key)
                        if profile:
                            group = profile.get("site_group", "B")
                            contam = GROUP_CONTAMINATION.get(group, CONTAMINATION)

                model = IsolationForest(
                    contamination=contam,
                    n_estimators=N_ESTIMATORS,
                    random_state=RANDOM_STATE,
                    n_jobs=1,
                )
                model.fit(features)
                new_models[tagsn] = model

            with self._lock:
                self.models = new_models
                self.last_trained = datetime.now()

            logger.info(
                f"IForest 학습 완료: {len(new_models)}개 모델 "
                f"(건너뜀 {skipped}개, 총 {len(tag_data)}개 태그)"
            )

        except Exception as e:
            logger.error(f"IForest 학습 실패: {e}")
        finally:
            with self._lock:
                self._is_training = False

    def predict_single(self, tagsn: str, current_val: float,
                       hour: int, dow: int, roc: float = 0.0
                       ) -> Optional[dict]:
        """단일 태그에 대해 IF 이상 판정을 수행한다."""
        model = self.models.get(tagsn)
        if model is None:
            return None

        features = np.array([[current_val, hour, dow, roc]])
        prediction = model.predict(features)[0]  # -1=anomaly, 1=normal
        score = model.score_samples(features)[0]  # lower = more anomalous

        return {
            "is_anomaly": prediction == -1,
            "anomaly_score": float(score),
        }

    def predict_for_rows(self, rows: list, columns: list) -> dict:
        """
        SQL 결과 rows에 대해 일괄 IF 판정을 수행한다.
        rows에 tagsn과 current_val 컬럼이 필요하다.

        Returns:
            {
                "tag_results": {tagsn: {"is_anomaly": bool, "anomaly_score": float}},
                "total": int,
                "if_anomaly_count": int,
                "z_and_if_agree": int,
            }
        """
        if not self.is_trained:
            return {}

        col_map = {c: i for i, c in enumerate(columns)}
        tagsn_idx = col_map.get("tagsn")
        val_idx = col_map.get("current_val")

        if tagsn_idx is None or val_idx is None:
            return {}

        now = datetime.now()
        hour, dow = now.hour, now.weekday()

        z_idx = col_map.get("z_score")
        tag_results = {}
        if_anomaly_count = 0
        z_and_if_agree = 0

        for row in rows:
            tagsn = row[tagsn_idx]
            current_val = float(row[val_idx] or 0)

            result = self.predict_single(tagsn, current_val, hour, dow)
            if result is None:
                continue

            tag_results[tagsn] = result

            if result["is_anomaly"]:
                if_anomaly_count += 1

                # Z-Score도 이상이면 일치 카운트
                if z_idx is not None:
                    z = float(row[z_idx] or 0)
                    if abs(z) >= 2.0:
                        z_and_if_agree += 1

        return {
            "tag_results": tag_results,
            "total": len(tag_results),
            "if_anomaly_count": if_anomaly_count,
            "z_and_if_agree": z_and_if_agree,
        }

    # ── 내부 메서드 ──────────────────────────────────────

    def _needs_retrain(self) -> bool:
        """재학습이 필요한지 판정한다."""
        if not self.last_trained:
            return True
        elapsed = datetime.now() - self.last_trained
        return elapsed > timedelta(hours=RETRAIN_INTERVAL_HOURS)

    def _build_features(self, data_points: list) -> Optional[np.ndarray]:
        """시계열 데이터포인트 → 4-feature 배열로 변환한다."""
        if len(data_points) < 2:
            return None

        features = []
        prev_val = data_points[0][1]

        for i, (bucket, val) in enumerate(data_points):
            if i == 0:
                prev_val = val
                continue

            # 비가동(near-zero) 데이터 제외
            if abs(val) < 0.001:
                prev_val = val
                continue

            hour = bucket.hour
            dow = bucket.weekday()
            roc = val - prev_val

            features.append([val, hour, dow, roc])
            prev_val = val

        return np.array(features) if features else None
