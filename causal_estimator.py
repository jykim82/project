"""
causal_estimator.py
교차상관 기반 인과 시간 지연 자동 추정

- cause→effect 태그 쌍의 최적 lag를 교차상관으로 추정
- numpy 전용 (scipy 미사용)
"""
import logging
import numpy as np

logger = logging.getLogger(__name__)


def estimate_lag(
    cause_values: list[float],
    effect_values: list[float],
    sample_interval_min: int = 1,
    max_lag_min: int = 60,
) -> dict:
    """교차상관으로 cause→effect 시간 지연을 추정한다.

    Args:
        cause_values: 원인 태그 시계열 (1분 간격, 시간순)
        effect_values: 결과 태그 시계열 (동일 시간대)
        sample_interval_min: 샘플링 간격 (분)
        max_lag_min: 최대 탐색 지연 (분)

    Returns:
        {"peak_lag_min": int, "peak_corr": float,
         "lag_min": int, "lag_max": int, "confidence": str}
    """
    max_lag_samples = max_lag_min // sample_interval_min
    n = min(len(cause_values), len(effect_values))

    if n < max_lag_samples * 2:
        return _empty_result()

    cause = np.array(cause_values[:n], dtype=float)
    effect = np.array(effect_values[:n], dtype=float)

    # Z-score 정규화
    c_std = cause.std()
    e_std = effect.std()
    if c_std < 1e-10 or e_std < 1e-10:
        return _empty_result()

    cause_norm = (cause - cause.mean()) / c_std
    effect_norm = (effect - effect.mean()) / e_std

    # 교차상관: positive lag만 (effect가 cause 뒤에 옴)
    correlations = np.zeros(max_lag_samples)
    for lag in range(max_lag_samples):
        if lag >= n:
            break
        correlations[lag] = np.mean(cause_norm[:n - lag] * effect_norm[lag:])

    peak_idx = int(np.argmax(correlations))
    peak_corr = float(correlations[peak_idx])
    peak_lag = peak_idx * sample_interval_min

    # 신뢰도 판정
    if peak_corr > 0.5:
        confidence = "high"
    elif peak_corr > 0.3:
        confidence = "medium"
    else:
        confidence = "low"

    # 범위 추정: peak ±30%, 최소 spread 1분
    spread = max(int(peak_lag * 0.3), 1)
    lag_min = max(0, peak_lag - spread)
    lag_max = peak_lag + spread

    return {
        "peak_lag_min": peak_lag,
        "peak_corr": round(peak_corr, 3),
        "lag_min": lag_min,
        "lag_max": lag_max,
        "confidence": confidence,
    }


def _empty_result() -> dict:
    return {
        "peak_lag_min": 0,
        "peak_corr": 0.0,
        "lag_min": 0,
        "lag_max": 0,
        "confidence": "low",
    }
