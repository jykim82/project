"""야간최소유량(MNF) CUSUM 분석 AI 요약 (2026-07-22)

- POST /leak/cusum/explain — 채팅 누수 의심 분석 카드의 "AI 요약" 버튼용.

scan_all_explain(E-023) Hybrid 패턴 재사용:
  [판정 요약] LLM 1문장 — 가장 심각한 태그 1건, 쉬운 말 서술
  [현황] 누수의심/주의/정상 건수 (Python 집계)
  [지표 뜻] 야간최소유량·CUSUM 정적 정의 (할루시네이션 없음)
  [확인 순서] 정적 안내

설계 근거: 비전문 운영자는 CUSUM·기울기 수치를 해석하기 어렵다 — 판정의
근거(평소 야간 유량 대비 초과 누적)를 쉬운 말로 번역하는 계층 제공.
판정 자체는 leak-alert-spec(판정=cusum_max) 값을 그대로 서술만 한다.
"""

import asyncio
import logging
import time
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from llm_narrative_log import log_narrative
from shared.llm_narrative import (
    validate_numbers_in_text as _validate_numbers_in_text,
)

logger = logging.getLogger("slm")

router = APIRouter()

_ollama_client = None


def init(ollama_client=None):
    global _ollama_client
    _ollama_client = ollama_client


class MnfTagInput(BaseModel):
    label: str = Field(..., max_length=200)
    sitename: str = Field("", max_length=100)
    # ── CUSUM 분석 (누수 의심) ──
    baseline_mean: Optional[float] = None   # 평시 야간최소유량 기준
    current_trend: Optional[float] = None   # 최근 야간최소유량 추세
    cusum_max: Optional[float] = None       # 판정 지표 (leak-alert-spec)
    threshold: Optional[float] = None       # CUSUM 임계
    leak_status: str = Field("정상", max_length=20)  # 누수의심/주의/정상
    trend_slope: Optional[float] = None
    # ── 표준편차 분석 (신뢰구간) — analysis_type='stddev' ──
    mean: Optional[float] = None
    ci_lower: Optional[float] = None
    ci_upper: Optional[float] = None
    today_value: Optional[float] = None
    excess: Optional[float] = None          # 금일값 - 상한 (음수=이내)
    unit: str = Field("", max_length=20)


class MnfExplainRequest(BaseModel):
    tags: list[MnfTagInput] = Field(..., max_length=50)
    analysis_type: str = Field("cusum", max_length=10)  # cusum | stddev
    user_question: Optional[str] = Field(None, max_length=300)


_STATUS_ORDER = {"누수의심": 0, "주의": 1, "정상": 2}

_METRIC_MEANING = (
    "[지표 뜻] 야간최소유량은 사용량이 거의 없는 새벽 시간대의 최저 유량으로, "
    "평소보다 계속 높게 유지되면 관로 누수 가능성을 의심합니다. "
    "CUSUM은 평소 기준을 넘은 만큼을 누적한 값으로, 임계를 넘으면 누수의심으로 판정합니다."
)

_CHECK_ORDER = (
    "[확인 순서] ① 누수의심 지점 현장 조사(청음·계량) → "
    "② 야간 사용처(공사·청소·살수) 여부 확인 → ③ 주의 지점 추세 관찰"
)


def _stddev_verdict(t: MnfTagInput) -> str:
    """신뢰구간 기준 판정 — 상한 초과/하한 미달/정상."""
    if t.today_value is None or t.ci_lower is None or t.ci_upper is None:
        return "판정 불가"
    if t.today_value > t.ci_upper:
        return "평소보다 높음"
    if t.today_value < t.ci_lower:
        return "평소보다 낮음"
    return "정상"


def _worst_stddev_tag(tags: list[MnfTagInput]) -> MnfTagInput:
    def sev(t: MnfTagInput):
        v = _stddev_verdict(t)
        # 상한 초과(누수 방향) > 하한 미달 > 정상. 같은 급이면 이탈 폭 큰 순
        rank = {"평소보다 높음": 0, "평소보다 낮음": 1, "정상": 2, "판정 불가": 3}[v]
        dev = 0.0
        if t.today_value is not None and t.ci_upper is not None and t.ci_lower is not None:
            if t.today_value > t.ci_upper:
                dev = t.today_value - t.ci_upper
            elif t.today_value < t.ci_lower:
                dev = t.ci_lower - t.today_value
        return (rank, -dev)
    return sorted(tags, key=sev)[0]


def _template_stddev_sentence(t: MnfTagInput) -> str:
    name = t.label or t.sitename or "대상 지점"
    v = _stddev_verdict(t)
    u = t.unit or ""
    if v == "평소보다 높음":
        base = (f"{name}의 금일 야간최소유량({t.today_value}{u})이 평소 범위 상한"
                f"({t.ci_upper}{u})을 넘어 평소보다 높습니다")
    elif v == "평소보다 낮음":
        base = (f"{name}의 금일 야간최소유량({t.today_value}{u})이 평소 범위 하한"
                f"({t.ci_lower}{u})보다 낮아 평소보다 적게 흐르고 있습니다")
    elif v == "정상":
        base = (f"{name}의 금일 야간최소유량({t.today_value}{u})은 평소 범위"
                f"({t.ci_lower}~{t.ci_upper}{u}) 안에 있어 정상입니다")
    else:
        base = f"{name}은 금일 값이 없어 판정할 수 없습니다"
    return base + f". ({v})"


_STDDEV_MEANING = (
    "[지표 뜻] 야간최소유량은 사용량이 거의 없는 새벽 시간대의 최저 유량입니다. "
    "평소 변동 범위(평균±표준편차 신뢰구간)를 벗어나면 평소와 다른 상태로 봅니다 — "
    "계속 높으면 누수 가능성, 계속 낮으면 공급·계측 변화 가능성을 확인합니다."
)


def _assemble_stddev(sentence: str, tags: list[MnfTagInput]) -> str:
    high = sum(1 for t in tags if _stddev_verdict(t) == "평소보다 높음")
    low = sum(1 for t in tags if _stddev_verdict(t) == "평소보다 낮음")
    ok = sum(1 for t in tags if _stddev_verdict(t) == "정상")
    counts = (f"[현황] 평소보다 높음 {high}건 · 평소보다 낮음 {low}건 · "
              f"정상 {ok}건 (총 {len(tags)}건)")
    return "\n\n".join([f"[판정 요약] {sentence}", counts, _STDDEV_MEANING, _CHECK_ORDER])


def _worst_tag(tags: list[MnfTagInput]) -> MnfTagInput:
    return sorted(
        tags,
        key=lambda t: (
            _STATUS_ORDER.get(t.leak_status, 9),
            -(t.cusum_max or 0),
        ),
    )[0]


def _template_sentence(t: MnfTagInput) -> str:
    name = t.label or t.sitename or "대상 지점"
    if t.leak_status == "누수의심":
        base = f"{name}의 야간최소유량이 평소 기준을 넘어선 상태가 누적되어 누수의심 단계입니다"
    elif t.leak_status == "주의":
        base = f"{name}의 야간최소유량이 평소보다 높아지는 추세라 주의 단계입니다"
    else:
        base = f"{name}의 야간최소유량이 평소 범위 안에 있어 정상입니다"
    if t.cusum_max is not None and t.threshold is not None:
        base += f" (누적 초과 {t.cusum_max} / 임계 {t.threshold})"
    return base + "."


def _assemble(sentence: str, tags: list[MnfTagInput]) -> str:
    alarm = sum(1 for t in tags if t.leak_status == "누수의심")
    warn = sum(1 for t in tags if t.leak_status == "주의")
    ok = sum(1 for t in tags if t.leak_status == "정상")
    counts = f"[현황] 누수의심 {alarm}건 · 주의 {warn}건 · 정상 {ok}건 (총 {len(tags)}건)"
    return "\n\n".join([f"[판정 요약] {sentence}", counts, _METRIC_MEANING, _CHECK_ORDER])


@router.post("/leak/cusum/explain")
async def explain_leak_cusum(req: MnfExplainRequest):
    """MNF CUSUM 분석 Hybrid 요약 — LLM 1문장 + 정적 블록."""
    if not req.tags:
        return {"summary": "분석 대상 태그가 없습니다.", "source": "template"}

    is_stddev = req.analysis_type == "stddev"
    worst = _worst_stddev_tag(req.tags) if is_stddev else _worst_tag(req.tags)

    # 허용 수치 화이트리스트 (worst 태그 수치만)
    allowed: list[float] = [0.0, 1.0]
    for v in (worst.baseline_mean, worst.current_trend, worst.cusum_max,
              worst.threshold, worst.trend_slope,
              worst.mean, worst.ci_lower, worst.ci_upper,
              worst.today_value, worst.excess):
        if v is not None:
            allowed.append(float(v))

    if is_stddev:
        verdict = _stddev_verdict(worst)
        prompt = (
            "다음은 야간최소유량 표준편차(평소 범위) 분석에서 가장 두드러진 1건이다. "
            "**단 1문장**으로 자연어 서술하라.\n\n"
            "## 대상 정보\n"
            f"- 지점: {worst.label} ({worst.sitename})\n"
            f"- 평소 평균: {worst.mean}{worst.unit}\n"
            f"- 평소 범위(신뢰구간): {worst.ci_lower} ~ {worst.ci_upper}{worst.unit}\n"
            f"- 금일 값: {worst.today_value}{worst.unit}\n"
            f"- 판정: {verdict}\n\n"
            "## 절대 규칙\n"
            "1. 위 수치·명칭만 사용. 외부 지식 추가 금지\n"
            "2. **1문장만**. 존댓말 '~습니다' 종결\n"
            "3. '표준편차·신뢰구간' 같은 용어 대신 '평소 변동 범위'처럼 쉬운 "
            "표현으로 서술하고 수치는 괄호로 병기하라\n"
            f"4. 문장 끝에 판정을 괄호로 명시: ({verdict})\n"
            "5. 권고·조치·원인추측 금지 (현황 서술만)\n\n"
            "출력 (1문장):"
        )
    else:
        prompt = (
        "다음은 야간최소유량(누수 감시) 분석에서 가장 심각한 1건이다. "
        "**단 1문장**으로 자연어 서술하라.\n\n"
        "## 대상 정보\n"
        f"- 지점: {worst.label} ({worst.sitename})\n"
        f"- 평소 야간최소유량 기준: {worst.baseline_mean}\n"
        f"- 최근 추세값: {worst.current_trend}\n"
        f"- 누적 초과(CUSUM): {worst.cusum_max} / 임계 {worst.threshold}\n"
        f"- 판정: {worst.leak_status}\n\n"
        "## 절대 규칙\n"
        "1. 위 수치·명칭만 사용. 외부 지식 추가 금지\n"
        "2. **1문장만**. 존댓말 '~습니다' 종결\n"
        "3. 'CUSUM' 같은 용어 대신 '평소 야간 유량보다 높은 상태가 누적'처럼 "
        "쉬운 표현으로 서술하고 수치는 괄호로 병기하라\n"
        f"4. 문장 끝에 판정을 괄호로 명시: ({worst.leak_status})\n"
        "5. 권고·조치·원인추측 금지 (현황 서술만)\n\n"
        "출력 (1문장):"
    )

    _tpl = _template_stddev_sentence if is_stddev else _template_sentence
    _asm = _assemble_stddev if is_stddev else _assemble

    if not _ollama_client:
        return {
            "summary": _asm(_tpl(worst), req.tags),
            "source": "template",
        }

    _t0 = time.perf_counter()
    try:
        text = await asyncio.to_thread(
            _ollama_client.generate, prompt, None, None, None, 180.0, 3,
        )
        _elapsed_ms = int((time.perf_counter() - _t0) * 1000)
        sentence = (text or "").strip().replace("\n", " ")
        ok, violations = _validate_numbers_in_text(
            sentence, allowed, strip_strings=[worst.label, worst.sitename],
        ) if sentence else (False, [])
        source = "llm" if ok else "fallback"
        if not ok:
            sentence = _tpl(worst)
        log_narrative(
            endpoint="leak/cusum/explain", source=source,
            llm_generate_ms=_elapsed_ms, llm_rejected=not ok,
            allowed_count=len(allowed), violations=violations,
        )
        return {"summary": _asm(sentence, req.tags), "source": source}
    except Exception as e:
        logger.warning(f"leak cusum explain LLM 실패 — 템플릿 폴백: {e}")
        return {
            "summary": _asm(_tpl(worst), req.tags),
            "source": "fallback",
        }
