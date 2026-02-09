"""
ollama_client.py
Ollama HTTP 클라이언트

- POST /api/generate 래핑
- GET /api/tags (헬스체크 + 모델 목록)
- 타임아웃, 연결 실패 시 OllamaConnectionError 발생
- httpx 사용 (sync)
"""

import logging
from typing import Optional

import httpx

from slm_config import (
    OLLAMA_BASE_URL,
    OLLAMA_TIMEOUT,
    CLASSIFIER_TEMPERATURE,
    get_model,
)

logger = logging.getLogger(__name__)


class OllamaConnectionError(Exception):
    """Ollama 서버 연결 실패"""
    pass


class OllamaClient:
    def __init__(self):
        self._base_url = OLLAMA_BASE_URL.rstrip("/")
        self._timeout = OLLAMA_TIMEOUT

    def health_check(self) -> bool:
        """
        Ollama 서버 상태를 확인한다.
        GET /api/tags 호출하여 정상 응답 여부 반환.
        """
        try:
            resp = httpx.get(
                f"{self._base_url}/api/tags",
                timeout=5.0,
            )
            return resp.status_code == 200
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

    def generate(self, prompt: str, model: Optional[str] = None) -> str:
        """
        Ollama /api/generate 호출하여 텍스트 응답을 반환한다.

        - model 미지정 시 slm_config.get_model() 사용
        - stream=False로 단일 응답 수신
        - 연결 실패 시 OllamaConnectionError 발생
        """
        use_model = model or get_model()

        payload = {
            "model": use_model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": CLASSIFIER_TEMPERATURE,
            },
        }

        try:
            resp = httpx.post(
                f"{self._base_url}/api/generate",
                json=payload,
                timeout=self._timeout,
            )
        except httpx.ConnectError as e:
            raise OllamaConnectionError(f"Ollama 서버 연결 실패: {e}")
        except httpx.TimeoutException as e:
            raise OllamaConnectionError(f"Ollama 응답 타임아웃: {e}")
        except httpx.HTTPError as e:
            raise OllamaConnectionError(f"Ollama HTTP 오류: {e}")

        if resp.status_code != 200:
            raise OllamaConnectionError(
                f"Ollama 응답 오류: status={resp.status_code}, body={resp.text[:200]}"
            )

        data = resp.json()
        response_text = data.get("response", "").strip()

        if not response_text:
            logger.warning(f"Ollama 빈 응답: model={use_model}")

        return response_text
