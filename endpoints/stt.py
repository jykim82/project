"""음성 입력 STT — 로컬 Whisper (faster-whisper, CPU int8).

현장 작업자가 말로 장애·조치를 기록하는 UX (E-025 플로우의 입력 확장).
- 모델: large-v3-turbo CT2 (OpenAI 계열, MIT) — 폐쇄망 원칙에 따라 로컬
  웨이트(data/models/faster-whisper-large-v3-turbo)만 사용, lazy 싱글턴.
- 도메인 initial_prompt 로 상수도 용어(탁도계 등) 오인식 방지
  (검증: 프롬프트 없이 '탁도계→학도 개' 오인식 → 프롬프트로 3/3 정확).
- 실측: 발화당 ~3.4s (CPU, beam_size=5), 모델 로드 ~1.4s (1회).
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/stt", tags=["stt"])

MODEL_DIR = os.environ.get(
    "WHISPER_MODEL_DIR", "data/models/faster-whisper-large-v3-turbo"
)
MAX_AUDIO_BYTES = 15 * 1024 * 1024  # 15MB (~2분 webm)

# 도메인 용어 바이어스 — 현장 발화에서 상수도 용어 인식률 확보.
# Whisper initial_prompt 는 224 토큰 초과 시 '앞'이 잘리므로, 검증된 핵심
# 용어(탁도계 등 — 없으면 '학도 개' 오인식)를 문장 '뒤'에 배치한다.
_BASE_DOMAIN_PROMPT = (
    "상수도 시설 현장 보고. 용어: 배수지, 가압장, 감압시설, 소블록, "
    "탁도계, 수위계, 유량계, 압력계, 잔류염소, 판넬, 인버터, PLC, RTU, "
    "UPS, 펌프, 밸브, 센서, 수리 완료, 조치 완료, 이상 발생, 고장"
)
_SITENAME_MAX_CHARS = 250  # 시설명 블록 상한 (224 토큰 예산 내 유지)
_PROMPT_TTL_S = 3600       # 시설명 캐시 주기

_model = None
_load_failed = False
_lock = threading.Lock()

_get_db_connection = None
_prompt_cache: str = _BASE_DOMAIN_PROMPT
_prompt_cached_at: float = 0.0
_prompt_lock = threading.Lock()


def init(get_db_connection_fn) -> None:
    """DB 커넥션 주입 — 시설명 동적 프롬프트 활성화 (미주입 시 base 만 사용)."""
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


def _load_sitenames() -> list[str]:
    """운영 시설명 + 별칭 — 발화에서 가장 오인식되기 쉬운 고유명사."""
    conn = _get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT sitename FROM tb_equipment_info
            WHERE sitename IS NOT NULL AND sitename <> ''
            UNION
            SELECT DISTINCT alias FROM tb_facility_alias
            WHERE use_yn = 'Y' AND alias IS NOT NULL AND alias <> ''
            ORDER BY 1
        """)
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def _domain_prompt() -> str:
    """시설명(DB, 1h 캐시) + 핵심 용어 프롬프트. DB 실패 시 base 폴백.

    핵심 용어를 마지막에 두어 토큰 초과 절단 시에도 살아남게 한다.
    """
    global _prompt_cache, _prompt_cached_at
    if _get_db_connection is None:
        return _BASE_DOMAIN_PROMPT
    now = time.monotonic()
    if now - _prompt_cached_at < _PROMPT_TTL_S and _prompt_cached_at > 0:
        return _prompt_cache
    with _prompt_lock:
        if now - _prompt_cached_at < _PROMPT_TTL_S and _prompt_cached_at > 0:
            return _prompt_cache
        try:
            names, used = [], 0
            for n in _load_sitenames():
                used += len(n) + 2
                if used > _SITENAME_MAX_CHARS:
                    break
                names.append(n)
            site_block = f"시설명: {', '.join(names)}. " if names else ""
            _prompt_cache = site_block + _BASE_DOMAIN_PROMPT
            logger.info("STT 도메인 프롬프트 갱신: 시설명 %d개, %d자",
                        len(names), len(_prompt_cache))
        except Exception as e:
            logger.warning("STT 시설명 로드 실패 — base 프롬프트 사용: %s", e)
            _prompt_cache = _BASE_DOMAIN_PROMPT
        _prompt_cached_at = now
    return _prompt_cache


def _get_model():
    """Whisper 모델 lazy 싱글턴. 실패 시 None (503 응답)."""
    global _model, _load_failed
    if _model is not None or _load_failed:
        return _model
    with _lock:
        if _model is not None or _load_failed:
            return _model
        try:
            from faster_whisper import WhisperModel

            if not os.path.isdir(MODEL_DIR):
                raise FileNotFoundError(f"모델 디렉토리 없음: {MODEL_DIR}")
            t0 = time.perf_counter()
            _model = WhisperModel(MODEL_DIR, device="cpu", compute_type="int8")
            logger.info("Whisper STT 모델 로드 (%.1fs): %s",
                        time.perf_counter() - t0, MODEL_DIR)
        except Exception as e:
            _load_failed = True
            logger.warning("Whisper 로드 실패 — STT 비활성: %s", e)
    return _model


@router.post("/transcribe")
def transcribe(audio: UploadFile = File(...), language: Optional[str] = None) -> dict:
    """오디오(webm/opus·wav·m4a 등) → 한국어 텍스트.

    동기 def — FastAPI threadpool 실행 (event loop 비블로킹, §blocking 원칙).
    """
    model = _get_model()
    if model is None:
        raise HTTPException(503, detail="STT 모델이 준비되지 않았습니다.")

    data = audio.file.read()
    if not data:
        raise HTTPException(400, detail="오디오가 비어 있습니다.")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413, detail="오디오가 너무 깁니다 (최대 2분).")

    suffix = os.path.splitext(audio.filename or "")[1] or ".webm"
    t0 = time.perf_counter()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
        tmp.write(data)
        tmp.flush()
        try:
            segments, info = model.transcribe(
                tmp.name,
                language=language or "ko",
                beam_size=5,
                vad_filter=True,
                initial_prompt=_domain_prompt(),
            )
            text = "".join(s.text for s in segments).strip()
        except Exception as e:
            logger.error("STT 전사 실패: %s", e)
            raise HTTPException(500, detail="음성 인식에 실패했습니다.") from e

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    logger.info("STT 전사: %.1fs 오디오 → %dms, %d자",
                getattr(info, "duration", 0.0), elapsed_ms, len(text))
    return {
        "text": text,
        "language": getattr(info, "language", language or "ko"),
        "audio_duration_s": round(float(getattr(info, "duration", 0.0)), 1),
        "elapsed_ms": elapsed_ms,
    }
