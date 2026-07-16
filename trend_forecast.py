"""향후 전망 forecast 엔진 — Chronos-Bolt (시계열 파운데이션 모델).

PoC 백테스트 (2026-07-16, 8태그×10컷, 운영 클램프 동일 적용):
  평균 MAE 선형회귀 1.2047 → Chronos 0.6391 (+46.9%), 추론 ~24ms/태그 (CPU).
  패턴 신호(주기 유량/압력/제어 수위)에서 +38~81%, 평탄 신호는 동급.
사양: docs/trend-comparison-spec.md §5.4

- 모델: amazon/chronos-bolt-base (205M, Apache-2.0, 미국산) —
  폐쇄망 원칙에 따라 로컬 웨이트(data/models/chronos-bolt-base)만 사용.
- lazy 싱글턴 로드 (첫 호출 ~1s, 이후 ~24ms). 미설치/로드 실패 시 None 반환
  → 호출부(trend_comparison)가 기존 선형회귀로 폴백.
- 반환은 median + 10/90% quantile — 불확실성 밴드 데이터 제공.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional

logger = logging.getLogger(__name__)

MODEL_PATH = os.environ.get(
    "CHRONOS_MODEL_DIR", "data/models/chronos-bolt-base"
)
# 컨텍스트 상한 — chronos-bolt 학습 컨텍스트(2048) 내, 최근 패턴 위주
MAX_CONTEXT = 512

_pipeline = None
_load_failed = False
_lock = threading.Lock()


def _get_pipeline():
    """Chronos 파이프라인 lazy 싱글턴. 실패 시 None (재시도 없음 — 폴백 고정)."""
    global _pipeline, _load_failed
    if _pipeline is not None or _load_failed:
        return _pipeline
    with _lock:
        if _pipeline is not None or _load_failed:
            return _pipeline
        try:
            from chronos import BaseChronosPipeline

            path = MODEL_PATH if os.path.isdir(MODEL_PATH) else None
            if path is None:
                raise FileNotFoundError(f"모델 디렉토리 없음: {MODEL_PATH}")
            _pipeline = BaseChronosPipeline.from_pretrained(path, device_map="cpu")
            logger.info("Chronos-Bolt forecast 엔진 로드: %s", path)
        except Exception as e:
            _load_failed = True
            logger.warning("Chronos 로드 실패 → 선형회귀 폴백 고정: %s", e)
    return _pipeline


def chronos_forecast(
    vals: list[Optional[float]], horizon_steps: int,
) -> Optional[tuple[list[float], list[float], list[float]]]:
    """결측 제거한 시계열로 horizon_steps 예측.

    Returns (median, q10, q90) — 실패/미가용 시 None (호출부 선형 폴백).
    주의: 시간 간격 불균일은 근사 허용 (버킷 시계열 전제 — 백테스트 동일 조건).
    """
    pipe = _get_pipeline()
    if pipe is None:
        return None
    context = [float(v) for v in vals if v is not None]
    if len(context) < 24:  # 최소 하루치 유효 샘플
        return None
    context = context[-MAX_CONTEXT:]
    try:
        import torch

        q, _ = pipe.predict_quantiles(
            torch.tensor(context, dtype=torch.float32).unsqueeze(0),
            prediction_length=horizon_steps,
            quantile_levels=[0.1, 0.5, 0.9],
        )
        arr = q[0].numpy()  # (steps, 3)
        median = [round(float(x), 3) for x in arr[:, 1]]
        q10 = [round(float(x), 3) for x in arr[:, 0]]
        q90 = [round(float(x), 3) for x in arr[:, 2]]
        return median, q10, q90
    except Exception as e:
        logger.warning("Chronos 추론 실패 → 이번 요청 선형 폴백: %s", e)
        return None
