"""
slm_config.py
SLM(Ollama) 연동 설정 상수

- 환경변수로 오버라이드 가능
- 기본값은 로컬 Ollama + qwen2.5:3b 기준
- 런타임 모델 변경: set_model() / get_model()
"""

import os
import threading

# Ollama 서버 설정
OLLAMA_BASE_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:26b-a4b-it-q4_K_M")
OLLAMA_TIMEOUT = 30  # seconds
# 모델을 VRAM에 상주시켜 cold-start 페널티 회피 ("24h" / "-1" / "0" 등)
# Ollama /api/generate의 keep_alive 필드로 전달됨. 기본값 5분 → idle 후 언로드되어 첫 요청이 느려짐.
OLLAMA_KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "24h")

# 세션 설정
SESSION_TTL = 600  # 10분 (초)
SESSION_MAX_TURNS = 10

# 분류기 설정
CLASSIFIER_TEMPERATURE = 0.0  # 결정론적 출력

# 폴백 설정
ENABLE_KEYWORD_FALLBACK = True  # Ollama 장애 시 기존 키워드 매칭 폴백

# 런타임 모델 선택 (스레드 안전)
_model_lock = threading.Lock()
_current_model: str = OLLAMA_MODEL


def get_model() -> str:
    """현재 선택된 Ollama 모델명을 반환한다."""
    with _model_lock:
        return _current_model


def set_model(model_name: str) -> None:
    """Ollama 모델을 런타임에 변경한다."""
    global _current_model
    with _model_lock:
        _current_model = model_name
