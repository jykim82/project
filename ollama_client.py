"""
ollama_client.py
Ollama HTTP 클라이언트

- POST /api/generate 래핑
- GET /api/tags (헬스체크 + 모델 목록)
- 타임아웃, 연결 실패 시 OllamaConnectionError 발생
- httpx 사용 (sync)
"""

import logging
import time
from typing import Optional

import httpx

from slm_config import (
    OLLAMA_BASE_URL,
    OLLAMA_TIMEOUT,
    OLLAMA_KEEP_ALIVE,
    CLASSIFIER_TEMPERATURE,
    get_model,
)

logger = logging.getLogger(__name__)

_BACKOFF_SECONDS = 60


class OllamaConnectionError(Exception):
    """Ollama 서버 연결 실패"""
    pass


class OllamaClient:
    def __init__(self):
        self._base_url = OLLAMA_BASE_URL.rstrip("/")
        self._timeout = OLLAMA_TIMEOUT
        self._unavailable_until: float = 0.0

    def health_check(self) -> bool:
        """
        Ollama 서버 상태를 확인한다.
        GET /api/tags 호출하여 정상 응답 여부 반환.
        성공 시 백오프를 리셋하여 generate() 재활성화.
        """
        try:
            resp = httpx.get(
                f"{self._base_url}/api/tags",
                timeout=5.0,
            )
            if resp.status_code == 200:
                self._unavailable_until = 0.0  # 백오프 리셋
                return True
            return False
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError) as e:
            logger.warning(f"Ollama health_check 실패: {e}")
            return False

    def list_models(self) -> list:
        """
        Ollama에 설치된 모델 목록을 반환한다.
        GET /api/tags → models[].name
        반환: [{"name": "gemma3:12b", "size": 12345678, ...}, ...]
        연결 실패 시 빈 리스트 반환.
        """
        try:
            resp = httpx.get(
                f"{self._base_url}/api/tags",
                timeout=5.0,
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            models = data.get("models", [])
            result = []
            for m in models:
                result.append({
                    "name": m.get("name", ""),
                    "size": m.get("size", 0),
                    "modified_at": m.get("modified_at", ""),
                })
            return result
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError) as e:
            logger.warning(f"Ollama list_models 실패: {e}")
            return []

    def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        num_ctx: Optional[int] = None,
        num_predict: Optional[int] = None,
        timeout: Optional[float] = None,
        backoff_seconds: float = _BACKOFF_SECONDS,
        keep_alive: Optional[str] = None,
        format: Optional[str] = None,
        think: bool = False,
    ) -> str:
        """
        Ollama /api/generate 호출하여 텍스트 응답을 반환한다.

        - model 미지정 시 slm_config.get_model() 사용
        - stream=False로 단일 응답 수신
        - 연결 실패 시 OllamaConnectionError 발생
        - 이전 연결 실패 후 backoff_seconds간 재시도 억제 (기본 60초)
        - num_ctx/num_predict: 호출별 override (None이면 ai_settings 사용)
        - timeout: 호출별 타임아웃 override (None이면 self._timeout 사용)
        """
        # 백오프: 이전 실패 후 지정 시간 동안 즉시 에러
        if time.time() < self._unavailable_until:
            raise OllamaConnectionError("Ollama 백오프 중 (이전 연결 실패)")

        use_model = model or get_model()
        use_timeout = timeout if timeout is not None else self._timeout

        # AI 런타임 설정에서 파라미터 로드 (UI에서 변경 가능)
        try:
            from ai_server import _ai_settings
            default_num_ctx = _ai_settings.num_ctx
            temperature = _ai_settings.temperature
        except (ImportError, AttributeError):
            default_num_ctx = 4096
            temperature = CLASSIFIER_TEMPERATURE

        options: dict = {
            "temperature": temperature,
            "num_ctx": num_ctx if num_ctx is not None else default_num_ctx,
        }
        if num_predict is not None:
            options["num_predict"] = num_predict

        payload = {
            "model": use_model,
            "prompt": prompt,
            "stream": False,
            "options": options,
            # 모델을 VRAM에 상주시켜 idle 후 cold-start 회피
            "keep_alive": keep_alive if keep_alive is not None else OLLAMA_KEEP_ALIVE,
        }
        # format='json' 시 Ollama 가 JSON-only 출력을 강제 (분류·구조화 응답에 유용)
        if format:
            payload["format"] = format
        # [E-063] Ollama 0.20.x + gemma4: think 미지정 시 thinking 모드로
        # 동작해 전 토큰이 thinking 필드로 새고 response 가 빈다 — 산발적
        # "Ollama 빈 응답" 경고의 근본 원인. 기본 명시 차단.
        payload["think"] = think

        try:
            resp = httpx.post(
                f"{self._base_url}/api/generate",
                json=payload,
                timeout=use_timeout,
            )
        except httpx.ConnectError as e:
            self._unavailable_until = time.time() + backoff_seconds
            raise OllamaConnectionError(f"Ollama 서버 연결 실패: {e}")
        except httpx.TimeoutException as e:
            self._unavailable_until = time.time() + backoff_seconds
            raise OllamaConnectionError(f"Ollama 응답 타임아웃: {e}")
        except httpx.HTTPError as e:
            self._unavailable_until = time.time() + backoff_seconds
            raise OllamaConnectionError(f"Ollama HTTP 오류: {e}")

        if resp.status_code != 200:
            raise OllamaConnectionError(
                f"Ollama 응답 오류: status={resp.status_code}, body={resp.text[:200]}"
            )

        data = resp.json()
        response_text = data.get("response", "").strip()

        if not response_text:
            # keep-warm 1토큰 핑은 빈 응답이 정상 — 소음 방지 위해 debug 로
            if num_predict == 1:
                logger.debug(f"Ollama keep-warm 핑 (빈 응답 정상): model={use_model}")
            else:
                logger.warning(f"Ollama 빈 응답: model={use_model}")

        return response_text
