"""
intent_embeddings.py
인메모리 벡터 유사도 기반 인텐트 분류

- Ollama nomic-embed-text 모델로 example3.json 질문 임베딩
- numpy 인메모리 캐시: embeddings_cache.npy + embeddings_meta.json
- MD5 해시 기반 캐시 유효성 검사 (example3.json 변경 시 재계산)
- cosine similarity 검색 (~1ms for 500 vectors)
"""

import hashlib
import json
import logging
import os
import time
from typing import Optional

import httpx
import numpy as np

from slm_config import OLLAMA_BASE_URL

logger = logging.getLogger(__name__)

# 캐시 파일 경로
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(_SCRIPT_DIR, "data")
CACHE_NPY = os.path.join(CACHE_DIR, "embeddings_cache.npy")
CACHE_META = os.path.join(CACHE_DIR, "embeddings_meta.json")

# 임베딩 모델 설정
EMBED_MODEL = "snowflake-arctic-embed2"
EMBED_DIM = 1024
EMBED_TIMEOUT = 120  # 배치 임베딩 타임아웃 (초)

# 검색 설정
VECTOR_THRESHOLD = 0.75  # cosine 유사도 확정 임계값
VECTOR_CANDIDATE_THRESHOLD = 0.70  # LLM 후보 제공 임계값


class IntentEmbeddingIndex:
    """인메모리 벡터 인텐트 인덱스"""

    def __init__(self):
        self._matrix: Optional[np.ndarray] = None  # (N, 768) L2-normalized
        self._labels: list[tuple[str, str]] = []    # [(intent_name, question), ...]
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def size(self) -> int:
        return len(self._labels) if self._labels else 0

    def load_or_build(self, example3_path: str) -> None:
        """
        캐시 유효 시 로드, 아니면 재계산.
        서버 startup에서 동기적으로 호출.
        """
        current_hash = self._file_hash(example3_path)

        # 캐시 유효성 확인
        if os.path.exists(CACHE_NPY) and os.path.exists(CACHE_META):
            try:
                meta = json.load(open(CACHE_META, "r", encoding="utf-8"))
                if meta.get("hash") == current_hash:
                    matrix = np.load(CACHE_NPY)
                    labels = [tuple(x) for x in meta["labels"]]
                    self._matrix = matrix
                    self._labels = labels
                    self._ready = True
                    logger.info(
                        f"임베딩 캐시 로드 완료: {len(labels)}벡터, "
                        f"{matrix.shape}, hash={current_hash[:8]}"
                    )
                    return
            except Exception as e:
                logger.warning(f"캐시 로드 실패, 재계산: {e}")

        # 재계산
        self._build_and_save(example3_path, current_hash)

    def search(self, query_embedding: np.ndarray, top_k: int = 5) -> list[dict]:
        """
        cosine 유사도 검색.
        반환: [{"intent_name": str, "question": str, "score": float}, ...]
        """
        if not self._ready or self._matrix is None:
            return []

        # L2 정규화
        norm = np.linalg.norm(query_embedding)
        if norm < 1e-10:
            return []
        query_norm = query_embedding / norm

        # cosine similarity (행렬 곱)
        similarities = self._matrix @ query_norm
        top_indices = np.argsort(similarities)[::-1][:top_k]

        results = []
        for idx in top_indices:
            intent_name, question = self._labels[idx]
            results.append({
                "intent_name": intent_name,
                "question": question,
                "score": float(similarities[idx]),
            })
        return results

    def _build_and_save(self, example3_path: str, file_hash: str) -> None:
        """example3.json 전체 질문을 임베딩하고 캐시 저장."""
        logger.info("임베딩 캐시 빌드 시작...")
        start = time.time()

        with open(example3_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 모든 질문 수집
        texts: list[str] = []
        labels: list[tuple[str, str]] = []
        for intent_def in data:
            intent_name = intent_def.get("intent", "")
            questions = intent_def.get("questions", [])
            for q in questions:
                texts.append(q)
                labels.append((intent_name, q))

        if not texts:
            logger.error("example3.json에 질문이 없습니다")
            return

        # 배치 임베딩 (Ollama /api/embed)
        embeddings = self._batch_embed(texts)
        if embeddings is None or len(embeddings) != len(texts):
            logger.error(f"임베딩 실패: expected={len(texts)}, got={len(embeddings) if embeddings else 0}")
            return

        # numpy 행렬 생성 + L2 정규화
        matrix = np.array(embeddings, dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms = np.maximum(norms, 1e-10)  # 0벡터 방지
        matrix = matrix / norms

        # 캐시 저장
        os.makedirs(CACHE_DIR, exist_ok=True)
        np.save(CACHE_NPY, matrix)
        meta = {
            "hash": file_hash,
            "labels": labels,
            "model": EMBED_MODEL,
            "dim": EMBED_DIM,
            "count": len(labels),
            "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(CACHE_META, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        self._matrix = matrix
        self._labels = labels
        self._ready = True

        elapsed = time.time() - start
        logger.info(
            f"임베딩 캐시 빌드 완료: {len(labels)}벡터, "
            f"{matrix.shape}, {elapsed:.1f}초"
        )

    def _batch_embed(self, texts: list[str]) -> Optional[list[list[float]]]:
        """
        Ollama /api/embed 배치 임베딩.
        대량 텍스트를 50개씩 배치 처리.
        """
        base_url = OLLAMA_BASE_URL.rstrip("/")
        all_embeddings: list[list[float]] = []
        batch_size = 50

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            try:
                resp = httpx.post(
                    f"{base_url}/api/embed",
                    json={"model": EMBED_MODEL, "input": batch},
                    timeout=EMBED_TIMEOUT,
                )
                if resp.status_code != 200:
                    logger.error(f"Ollama embed 오류: status={resp.status_code}")
                    return None
                data = resp.json()
                embeddings = data.get("embeddings", [])
                if len(embeddings) != len(batch):
                    logger.error(
                        f"임베딩 수 불일치: batch={len(batch)}, got={len(embeddings)}"
                    )
                    return None
                all_embeddings.extend(embeddings)
                logger.info(f"배치 임베딩 {i + len(batch)}/{len(texts)} 완료")
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                logger.error(f"Ollama embed 연결 실패: {e}")
                return None

        return all_embeddings

    @staticmethod
    def _file_hash(path: str) -> str:
        """파일 MD5 해시."""
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()


def embed_query(text: str) -> Optional[np.ndarray]:
    """
    단일 질문 임베딩 (검색 시 사용).
    반환: (768,) float32 배열 또는 None.
    """
    base_url = OLLAMA_BASE_URL.rstrip("/")
    try:
        resp = httpx.post(
            f"{base_url}/api/embed",
            json={"model": EMBED_MODEL, "input": [text]},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning(f"embed_query 오류: status={resp.status_code}")
            return None
        data = resp.json()
        embeddings = data.get("embeddings", [])
        if not embeddings:
            return None
        return np.array(embeddings[0], dtype=np.float32)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        logger.warning(f"embed_query 연결 실패: {e}")
        return None
