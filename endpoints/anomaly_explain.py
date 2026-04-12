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

from llm_narrative_log import log_narrative
from shared.llm_narrative import (
    extract_numbers as _shared_extract_numbers,
    strip_identifier_strings as _shared_strip_identifier_strings,
    validate_numbers_in_text as _shared_validate_numbers_in_text,
    is_context_enabled as _shared_is_context_enabled,
)

logger = logging.getLogger("slm")

router = APIRouter()

_ollama_client = None
_get_db_connection = None


def init(ollama_client=None, get_db_connection_fn=None):
    """ai_server.py에서 Ollama 클라이언트 + DB 커넥션 팩토리를 주입받는다."""
    global _ollama_client, _get_db_connection
    _ollama_client = ollama_client
    _get_db_connection = get_db_connection_fn


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

# shared.llm_narrative로 이관 — 하위 호환 래퍼
def _strip_identifier_strings(text: str, strings: list[str]) -> str:
    return _shared_strip_identifier_strings(text, strings)


def _extract_numbers(text: str) -> list[float]:
    return _shared_extract_numbers(text)


def _validate_numbers_in_text(
    text: str,
    allowed: list[float],
    strip_strings: Optional[list[str]] = None,
    tolerance: float = 0.02,
) -> tuple[bool, list[float]]:
    return _shared_validate_numbers_in_text(text, allowed, strip_strings, tolerance)


def _is_context_enabled(grp_cd: str, comm_cd: str) -> bool:
    return _shared_is_context_enabled(_get_db_connection, grp_cd, comm_cd)


def _fetch_anomaly_context(diag: EquipmentDiagnosisInput) -> dict:
    """
    DB에서 비교 컨텍스트를 조회해 LLM이 해석할 수 있는 수치를 반환한다.

    반환 키 (실패한 경우 누락될 수 있음):
      linked_alarms_7d   : 연결 이상 태그의 지난 7일 알람 발생 건수 (이 설비)
      site_alarms_7d     : 같은 시설 전체의 지난 7일 알람 건수
      site_tag_count     : 같은 시설의 총 태그 수
      linked_per_day     : 일 평균 이 설비 알람 건수 (소수 첫째 자리)
      site_per_day       : 일 평균 같은 시설 알람 건수 (소수 첫째 자리)

    모든 값은 정수/소수 — LLM 검증용 allowed_numbers에 함께 전달된다.
    """
    if _get_db_connection is None:
        return {}

    ctx: dict = {}
    conn = None
    try:
        conn = _get_db_connection()
        with conn.cursor() as cur:
            # 1. 같은 시설(sitename + facilitytype)의 지난 7일 총 알람 건수
            cur.execute(
                "SELECT COUNT(*) FROM tb_equipment_alarm_report "
                "WHERE sitename = %s AND facilitytype = %s "
                "  AND alarm_start_time >= NOW() - INTERVAL '7 days'",
                (diag.sitename, diag.facilitytype),
            )
            row = cur.fetchone()
            ctx["site_alarms_7d"] = int(row[0]) if row else 0

            # 2. 연결 이상 태그의 지난 7일 알람 건수 (이 설비 전용)
            if diag.linked_anomaly_tags:
                cur.execute(
                    "SELECT COUNT(*) FROM tb_equipment_alarm_report "
                    "WHERE tagsn = ANY(%s) "
                    "  AND alarm_start_time >= NOW() - INTERVAL '7 days'",
                    (diag.linked_anomaly_tags,),
                )
                row = cur.fetchone()
                ctx["linked_alarms_7d"] = int(row[0]) if row else 0
            else:
                ctx["linked_alarms_7d"] = 0

            # 3. 같은 시설의 총 태그 수 (참고)
            cur.execute(
                "SELECT COUNT(DISTINCT tagsn) FROM tb_tag_info "
                "WHERE sitename = %s AND facilitytype = %s",
                (diag.sitename, diag.facilitytype),
            )
            row = cur.fetchone()
            ctx["site_tag_count"] = int(row[0]) if row else 0

        # 일평균 (한 자리 소수) — LLM이 직관적으로 쓸 수 있도록
        ctx["linked_per_day"] = round(ctx.get("linked_alarms_7d", 0) / 7, 1)
        ctx["site_per_day"] = round(ctx.get("site_alarms_7d", 0) / 7, 1)

    except Exception as e:
        logger.warning(f"anomaly 컨텍스트 조회 실패: {e}")
    finally:
        if conn:
            conn.close()

    return ctx


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
        # C안: DB 컨텍스트 조회 — 버튼 클릭 시점에만 실행
        _context_mode = "on" if _is_context_enabled("SITE_SETTING", "ANOMALY_EXPLAIN_CONTEXT") else "off"
        _ctx_t0 = time.perf_counter()
        context = _fetch_anomaly_context(diag) if _context_mode == "on" else {}
        _context_fetch_ms = int((time.perf_counter() - _ctx_t0) * 1000)
        _context_used: list[str] = []
        if "linked_alarms_7d" in context:
            _context_used.append("linked_alarms_7d")
        if "site_alarms_7d" in context:
            _context_used.append("site_alarms_7d")
        if "site_tag_count" in context:
            _context_used.append("site_tag_count")

        # 허용 수치 — LLM이 사용 가능한 모든 DB 값 + 컨텍스트 값
        allowed_numbers = [
            float(diag.health_score),
            float(diag.anomaly_tag_count),
            float(diag.total_tag_count),
            0.0,  # "0건" 같은 부정 표현 허용
            7.0,  # 프롬프트의 "지난 7일" 상수 (LLM이 자주 인용)
        ]
        for key in (
            "linked_alarms_7d", "site_alarms_7d", "site_tag_count",
            "linked_per_day", "site_per_day",
        ):
            if key in context:
                allowed_numbers.append(float(context[key]))

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

        # C안: 비교 컨텍스트 섹션 — 컨텍스트가 있을 때만 추가
        context_block = ""
        if context:
            lines = []
            if "linked_alarms_7d" in context:
                lines.append(
                    f"- 이 설비 연결 태그의 지난 7일 알람 발생: "
                    f"{context['linked_alarms_7d']}건 (일평균 {context['linked_per_day']}건)"
                )
            if "site_alarms_7d" in context:
                lines.append(
                    f"- 같은 시설 전체의 지난 7일 알람 발생: "
                    f"{context['site_alarms_7d']}건 (일평균 {context['site_per_day']}건)"
                )
            if "site_tag_count" in context:
                lines.append(
                    f"- 같은 시설의 총 태그 수: {context['site_tag_count']}개"
                )
            if lines:
                context_block = (
                    "\n\n## 비교 컨텍스트 (지난 7일, 이 값들도 서술에 사용 가능)\n"
                    + "\n".join(lines)
                )

        # 엄격 프롬프트 — 제공된 정보만 사용 강제
        prompt = (
            "다음 설비 진단 결과와 비교 컨텍스트를 읽고 이상 원인을 "
            "2~3문장으로 분석·서술하라.\n\n"
            "## 절대 규칙\n"
            "1. 아래 섹션들에 제공된 값(건강 점수·태그 수·장애 라벨·컨텍스트 수치)만 사용하라.\n"
            "2. 제공되지 않은 숫자(백분율·시간·임계값 등)는 절대 생성하지 마라.\n"
            "3. 장애 라벨 목록에 없는 새로운 원인을 추측하거나 추가하지 마라.\n"
            "4. 조치 지시·권고·복구 방법은 포함하지 마라 (상태·비교 서술에 집중).\n"
            "5. 외부 지식·일반적인 센서 기준·유지보수 주기는 언급하지 마라.\n"
            "6. 비교 컨텍스트가 있으면 이 설비가 같은 시설 평균 대비 어떤 수준인지 "
            "간단히 비교 서술하라 (예: '시설 평균보다 높습니다', '낮은 편입니다').\n\n"
            "## 진단 데이터 (이 값들만 사용)\n"
            f"- 시설: {diag.sitename} {diag.facilitytype}\n"
            f"- 설비 종류: {diag.equipmenttype}\n"
            f"- 설비 ID: {diag.equipment_id}\n"
            f"- 건강 점수: {diag.health_score}점\n"
            f"- 건강 등급: {diag.health_grade}\n"
            f"- 감지된 장애: {failure_block}\n"
            f"- 연결 태그: 총 {diag.total_tag_count}개 중 {diag.anomaly_tag_count}개 이상"
            f"{linked_preview}"
            f"{context_block}\n\n"
            "원인 분석·서술 (2~3문장, 위 정보만 사용, 존댓말 '~습니다' 종결):"
        )

        if not _ollama_client:
            logger.info("Ollama 클라이언트 없음 — 템플릿 폴백 반환")
            return {
                "summary": _fallback_narrative(diag),
                "source": "fallback",
                "context_used": _context_used,
                "context_fetch_ms": _context_fetch_ms,
                "context_mode": _context_mode,
                "llm_generate_ms": 0,
                "allowed_numbers_count": len(allowed_numbers),
            }

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
        _llm_ms = int(_elapsed * 1000)
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
            log_narrative(
                endpoint="anomaly/explain",
                params={
                    "equipment_id": diag.equipment_id,
                    "sitename": diag.sitename,
                    "health_grade": diag.health_grade,
                },
                source="fallback",
                context_mode=_context_mode,
                context_used=_context_used,
                context_fetch_ms=_context_fetch_ms,
                llm_generate_ms=_llm_ms,
                llm_rejected=True,
                violations=violations,
                allowed_count=len(allowed_numbers),
            )
            return {
                "summary": _fallback_narrative(diag),
                "source": "fallback",
                "llm_rejected": True,
                "violations": violations,
                "context_used": _context_used,
                "context_fetch_ms": _context_fetch_ms,
                "context_mode": _context_mode,
                "llm_generate_ms": _llm_ms,
                "allowed_numbers_count": len(allowed_numbers),
            }

        logger.info(
            f"이상감지 원인 서술 완료: {diag.equipment_id} "
            f"({diag.health_grade}) "
            f"⏱ context={_context_fetch_ms}ms, llm={_llm_ms}ms, "
            f"ctx={_context_used}"
        )
        log_narrative(
            endpoint="anomaly/explain",
            params={
                "equipment_id": diag.equipment_id,
                "sitename": diag.sitename,
                "health_grade": diag.health_grade,
            },
            source="llm",
            context_mode=_context_mode,
            context_used=_context_used,
            context_fetch_ms=_context_fetch_ms,
            llm_generate_ms=_llm_ms,
            llm_rejected=False,
            allowed_count=len(allowed_numbers),
        )
        return {
            "summary": text,
            "source": "llm",
            "context_used": _context_used,
            "context_fetch_ms": _context_fetch_ms,
            "context_mode": _context_mode,
            "llm_generate_ms": _llm_ms,
            "allowed_numbers_count": len(allowed_numbers),
        }

    except Exception as e:
        logger.error(f"이상감지 원인 서술 실패: {e}")
        # 예외 상황에서도 폴백 반환 (사용자 경험 보호)
        return {
            "summary": _fallback_narrative(diag),
            "source": "fallback",
            "error": str(e),
        }
