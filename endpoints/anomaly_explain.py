"""
이상감지 원인 LLM 서술 API 엔드포인트

- POST /anomaly/explain — 설비 진단(equipment_diagnosis) → 자연어 원인 서술

할루시네이션 방어 (endpoints/trend.py 패턴 재사용):
1. 엄격 프롬프트 (제공 수치·장애 라벨 외 추가 금지)
2. 출력 검증 (_validate_numbers_in_text — 허용 수치와 대조)
3. 결정적 폴백 (_fallback_narrative — 템플릿 기반 자연어 요약)
"""

import asyncio
import logging
import re
import time
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("slm")

router = APIRouter()

_ollama_client = None


def init(ollama_client=None):
    """ai_server.py에서 Ollama 클라이언트를 주입받는다."""
    global _ollama_client
    _ollama_client = ollama_client


# =============================================================================
# 요청/응답 모델
# =============================================================================

class EquipmentDiagnosisInput(BaseModel):
    equipment_id: str = Field(..., max_length=100)
    equipmenttype: str = Field(..., max_length=50)
    sitename: str = Field(..., max_length=100)
    facilitytype: str = Field(..., max_length=50)
    health_score: int = Field(..., ge=0, le=100)
    health_grade: str = Field(..., max_length=20)
    failure_labels: list[str] = Field(default_factory=list)
    anomaly_tag_count: int = Field(0, ge=0)
    total_tag_count: int = Field(0, ge=0)
    linked_anomaly_tags: list[str] = Field(default_factory=list)
    user_question: Optional[str] = Field(None, max_length=500)


# =============================================================================
# 할루시네이션 방어 유틸
# =============================================================================

_NUM_RE = re.compile(r"(?<![0-9A-Za-z])(-?\d+(?:,\d{3})*(?:\.\d+)?)")

# 문장 형식상 쓰이는 정수는 무시 (2문장, 1회 등)
_NUM_IGNORE = {"1", "2"}


def _strip_identifier_strings(text: str, strings: list[str]) -> str:
    """식별자 문자열(설비ID, 태그명 등)을 공백으로 치환해
    내부 숫자·하이픈이 검증에 오탐되지 않게 한다.
    """
    result = text
    # 긴 문자열부터 치환 (부분 겹침 방지)
    for s in sorted(set(strings), key=len, reverse=True):
        if s and s in result:
            result = result.replace(s, " ")
    return result


def _extract_numbers(text: str) -> list[float]:
    out = []
    for m in _NUM_RE.finditer(text):
        tok = m.group(1).replace(",", "")
        if tok in _NUM_IGNORE:
            continue
        try:
            out.append(float(tok))
        except ValueError:
            continue
    return out


def _validate_numbers_in_text(
    text: str,
    allowed: list[float],
    strip_strings: Optional[list[str]] = None,
    tolerance: float = 0.02,
) -> tuple[bool, list[float]]:
    """텍스트 내 모든 숫자가 허용 수치와 tolerance 내 일치하는지 검증.

    strip_strings: 검증 전 제거할 식별자 문자열 (설비ID·태그명·시설명 등).
    내부 숫자/하이픈이 섞여 있어도 false positive를 피한다.
    """
    cleaned = _strip_identifier_strings(text, strip_strings or [])
    nums = _extract_numbers(cleaned)
    violations: list[float] = []
    for n in nums:
        ok = any(abs(n - a) <= max(abs(a) * tolerance, 0.01) for a in allowed)
        if not ok:
            violations.append(n)
    return (len(violations) == 0, violations)


def _fallback_narrative(diag: EquipmentDiagnosisInput) -> str:
    """LLM 검증 실패/불가 시 결정적 템플릿 요약 (할루시네이션 0)."""
    first = (
        f"{diag.sitename} {diag.facilitytype}의 {diag.equipmenttype}({diag.equipment_id})은(는) "
        f"건강 점수 {diag.health_score}점({diag.health_grade}) 상태입니다."
    )

    if diag.failure_labels:
        failure_str = ", ".join(diag.failure_labels)
        second = (
            f"주요 이상 원인은 {failure_str}이며, "
            f"연결 태그 {diag.total_tag_count}개 중 {diag.anomaly_tag_count}개에서 "
            f"이상 신호가 감지되었습니다."
        )
    elif diag.anomaly_tag_count > 0:
        second = (
            f"명시적 장애는 없으나 연결 태그 {diag.total_tag_count}개 중 "
            f"{diag.anomaly_tag_count}개에서 이상 신호가 감지되었습니다."
        )
    else:
        second = f"연결된 {diag.total_tag_count}개 태그 모두 정상 범위 내에서 동작 중입니다."

    return f"{first} {second}"


# =============================================================================
# POST /anomaly/explain
# =============================================================================

@router.post("/anomaly/explain")
async def explain_anomaly(diag: EquipmentDiagnosisInput):
    """
    설비 진단 결과를 자연어 원인 서술로 변환.

    응답:
      summary         : 원인 서술 텍스트 (2~3문장)
      source          : "llm" | "fallback"
      llm_rejected    : (선택) LLM 응답이 검증 실패한 경우 true
      violations      : (선택) 검증 실패 시 허용되지 않은 숫자 목록
    """
    try:
        # 허용 수치 — LLM이 사용 가능한 모든 DB 값
        allowed_numbers = [
            float(diag.health_score),
            float(diag.anomaly_tag_count),
            float(diag.total_tag_count),
            0.0,  # "0건" 같은 부정 표현 허용
        ]

        failure_block = (
            "없음 (정상)"
            if not diag.failure_labels
            else ", ".join(diag.failure_labels)
        )

        linked_preview = ""
        if diag.linked_anomaly_tags:
            preview = diag.linked_anomaly_tags[:3]
            more = (
                f" 외 {len(diag.linked_anomaly_tags) - 3}개"
                if len(diag.linked_anomaly_tags) > 3 else ""
            )
            linked_preview = f"\n- 이상 태그 예시: {', '.join(preview)}{more}"

        # 엄격 프롬프트 — 제공된 정보만 사용 강제
        prompt = (
            "다음 설비 진단 결과를 읽고 이상 원인을 2~3문장으로 서술하라.\n\n"
            "## 절대 규칙\n"
            "1. 아래 '진단 데이터' 섹션에 제공된 값(건강 점수·태그 수·장애 라벨)만 사용하라.\n"
            "2. 제공되지 않은 숫자(백분율·시간·임계값 등)는 절대 생성하지 마라.\n"
            "3. 장애 라벨 목록에 없는 새로운 원인을 추측하거나 추가하지 마라.\n"
            "4. 조치 지시·권고·복구 방법은 포함하지 마라 (상태 서술에 집중).\n"
            "5. 외부 지식·일반적인 센서 기준·유지보수 주기는 언급하지 마라.\n\n"
            "## 진단 데이터 (이 값들만 사용)\n"
            f"- 시설: {diag.sitename} {diag.facilitytype}\n"
            f"- 설비 종류: {diag.equipmenttype}\n"
            f"- 설비 ID: {diag.equipment_id}\n"
            f"- 건강 점수: {diag.health_score}점\n"
            f"- 건강 등급: {diag.health_grade}\n"
            f"- 감지된 장애: {failure_block}\n"
            f"- 연결 태그: 총 {diag.total_tag_count}개 중 {diag.anomaly_tag_count}개 이상"
            f"{linked_preview}\n\n"
            "원인 서술 (2~3문장, 위 정보만 사용, 존댓말 '~습니다' 종결):"
        )

        if not _ollama_client:
            logger.info("Ollama 클라이언트 없음 — 템플릿 폴백 반환")
            return {"summary": _fallback_narrative(diag), "source": "fallback"}

        _t0 = time.perf_counter()
        text = await asyncio.to_thread(
            _ollama_client.generate,
            prompt,
            None,   # model
            None,   # num_ctx
            None,   # num_predict
            90.0,   # timeout — Gemma4:26b tail latency 커버 (측정: p95 약 30s, p99 약 50s)
            3,      # backoff_seconds — 사용자 클릭 UX에 맞게 짧게 (cascading 회피)
        )
        _elapsed = time.perf_counter() - _t0
        text = (text or "").strip()

        # 할루시네이션 검증 — 식별자(설비ID·태그명·시설명) 내부 숫자 오탐 방지
        strip_strings = [
            diag.equipment_id,
            diag.sitename,
            diag.facilitytype,
            diag.equipmenttype,
            *diag.linked_anomaly_tags,
        ]
        ok, violations = _validate_numbers_in_text(
            text, allowed_numbers, strip_strings=strip_strings,
        )
        if not ok or not text:
            logger.warning(
                f"이상감지 LLM 서술 할루시네이션 감지 → 폴백: "
                f"위반={violations}, 허용={allowed_numbers}"
            )
            return {
                "summary": _fallback_narrative(diag),
                "source": "fallback",
                "llm_rejected": True,
                "violations": violations,
            }

        logger.info(
            f"이상감지 원인 서술 완료: {diag.equipment_id} "
            f"({diag.health_grade}) ⏱ {_elapsed*1000:.0f}ms"
        )
        return {"summary": text, "source": "llm"}

    except Exception as e:
        logger.error(f"이상감지 원인 서술 실패: {e}")
        # 예외 상황에서도 폴백 반환 (사용자 경험 보호)
        return {
            "summary": _fallback_narrative(diag),
            "source": "fallback",
            "error": str(e),
        }
