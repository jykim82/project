"""
LLM 자연어 서술 호출 로그 (JSONL, 일 단위 rotation)

경로: /app/logs/llm_narrative/YYYY-MM-DD.jsonl (Docker 볼륨 밖에 남으면 컨테이너
재기동 시 사라지므로 /app 하위에 저장 → /Users/jykim/slm 마운트로 영속화)

각 레코드는 LLM 서술 엔드포인트 1회 호출을 나타내며, 품질·성능 분석에 사용.

사용:
    from llm_narrative_log import log_narrative
    log_narrative(
        endpoint="trend/explain",
        params={"tagsn": "..."},
        source="llm",
        context_mode="on",
        context_used=["baseline_30d"],
        context_fetch_ms=42,
        llm_generate_ms=22000,
        llm_rejected=False,
        allowed_count=9,
    )
"""

import json
import logging
import os
import threading
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger("slm")

_LOG_DIR = os.environ.get("LLM_NARRATIVE_LOG_DIR", "/app/logs/llm_narrative")
_LOCK = threading.Lock()


def _ensure_dir() -> None:
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
    except Exception as e:
        logger.warning(f"llm_narrative_log 디렉토리 생성 실패: {e}")


def _current_path() -> str:
    date = datetime.now().strftime("%Y-%m-%d")
    return os.path.join(_LOG_DIR, f"{date}.jsonl")


def log_narrative(
    endpoint: str,
    params: Optional[dict[str, Any]] = None,
    source: str = "llm",
    context_mode: str = "on",
    context_used: Optional[list[str]] = None,
    context_fetch_ms: int = 0,
    llm_generate_ms: int = 0,
    llm_rejected: bool = False,
    allowed_count: int = 0,
    violations: Optional[list[float]] = None,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    """
    LLM 자연어 서술 호출 1건을 JSONL 파일에 기록한다.
    실패해도 호출자에게 예외를 전파하지 않는다 (관찰성 로깅은 실패 내성).
    """
    try:
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "endpoint": endpoint,
            "params": params or {},
            "source": source,
            "context_mode": context_mode,
            "context_used": context_used or [],
            "context_fetch_ms": context_fetch_ms,
            "llm_generate_ms": llm_generate_ms,
            "llm_rejected": llm_rejected,
            "allowed_count": allowed_count,
        }
        if violations:
            record["violations"] = violations
        if extra:
            record.update(extra)

        _ensure_dir()
        path = _current_path()
        with _LOCK:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        # 로깅 실패는 호출자에 영향 주지 않음
        logger.debug(f"llm_narrative_log 기록 실패: {e}")


def aggregate_last_n_days(days: int = 7) -> dict:
    """
    최근 N일 JSONL 파일을 읽어 간단 통계 집계.

    반환:
      {
        "total": int,
        "by_source": {"llm": int, "fallback": int},
        "llm_pass_rate": float,   # llm / total
        "rejected_rate": float,   # llm_rejected=True / total
        "by_endpoint": {
            "trend/explain": {"total": 10, "llm": 9, "fallback": 1, "avg_llm_ms": 22000, ...},
        },
        "context_mode": {"on": int, "off": int},
      }
    """
    from datetime import timedelta
    stats: dict = {
        "total": 0,
        "by_source": {},
        "by_endpoint": {},
        "context_mode": {},
        "rejected_count": 0,
    }
    today = datetime.now().date()
    for offset in range(days):
        date_str = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
        path = os.path.join(_LOG_DIR, f"{date_str}.jsonl")
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    stats["total"] += 1
                    src = rec.get("source", "unknown")
                    stats["by_source"][src] = stats["by_source"].get(src, 0) + 1
                    mode = rec.get("context_mode", "unknown")
                    stats["context_mode"][mode] = stats["context_mode"].get(mode, 0) + 1
                    if rec.get("llm_rejected"):
                        stats["rejected_count"] += 1
                    ep = rec.get("endpoint", "?")
                    if ep not in stats["by_endpoint"]:
                        stats["by_endpoint"][ep] = {
                            "total": 0, "llm": 0, "fallback": 0,
                            "sum_llm_ms": 0, "sum_ctx_ms": 0,
                        }
                    bucket = stats["by_endpoint"][ep]
                    bucket["total"] += 1
                    if src == "llm":
                        bucket["llm"] += 1
                    elif src == "fallback":
                        bucket["fallback"] += 1
                    bucket["sum_llm_ms"] += int(rec.get("llm_generate_ms") or 0)
                    bucket["sum_ctx_ms"] += int(rec.get("context_fetch_ms") or 0)
        except Exception as e:
            logger.debug(f"aggregate {path} 실패: {e}")

    total = stats["total"]
    stats["llm_pass_rate"] = round(
        stats["by_source"].get("llm", 0) / total, 3
    ) if total else 0.0
    stats["rejected_rate"] = round(
        stats["rejected_count"] / total, 3
    ) if total else 0.0

    for ep, bucket in stats["by_endpoint"].items():
        bt = bucket["total"]
        bucket["llm_pass_rate"] = round(bucket["llm"] / bt, 3) if bt else 0.0
        bucket["avg_llm_ms"] = int(bucket["sum_llm_ms"] / bt) if bt else 0
        bucket["avg_ctx_ms"] = int(bucket["sum_ctx_ms"] / bt) if bt else 0
        # 요약 누적은 제거
        del bucket["sum_llm_ms"]
        del bucket["sum_ctx_ms"]

    return stats
