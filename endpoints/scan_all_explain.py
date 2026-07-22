"""
전체 이상 스캔 요약 AI 서술 (P2.6 / [E-022] / [E-023])

- POST /anomaly/scan-all/explain
  요청: {top_n?, sitename?, facilitytype?}
  → "Hybrid" 응답: LLM이 가장 위급한 1건만 1문장으로 서술하고,
     카테고리별 카운트·정의·점검 순서는 Python 정적 조립.

설계 의도 ([E-023]):
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  운영자가 "오늘 가장 큰 문제가 뭐야?"라고 물을 때, 통계 수치 나열보다
  "이 카테고리는 무슨 의미고, 어느 순서로 봐야 하는지" 가이드가 더 가치
  있음. 그러나 LLM에 카테고리 정의·점검 순서까지 맡기면 응답이 길어지고
  (45~60s) 할루시네이션 위험이 증가. 그래서:

  1. **LLM 책임을 1문장(가장 위급한 항목 서술)으로 축소** — 출력 ~50토큰,
     생성 시간 ~5~8초로 단축
  2. **카테고리 정의는 정적 사전(CATEGORY_MEANINGS) 주입** — 할루시네이션
     없음, 운영팀이 용어 수정할 때 코드 1곳만 고치면 됨
  3. **카운트는 Python 집계** — 정확함
  4. **점검 순서는 고정** (설비장애→교차검증→데이터품질→값이탈) — 위급도
     가장 높은 순. 확정 사고(설비) > 물리 피해 의심(교차) > 모니터링 무력화
     (품질) > 통계 경계(값이탈)

응답 형태:
  [중요 알람] {LLM 1문장 — 시설·태그·수치·카테고리 라벨}

  [유형별 현황] 설비장애 N건 · 교차검증 M건 · 데이터품질 K건 · 값이탈 L건
                (총 T건 중)

  [{가장 위급한 카테고리}] {정적 정의 문구}

  [점검 순서] ① 설비 장애 → ② 교차 검증 → ③ 데이터 품질 → ④ 값 이탈

할루시네이션 방어: LLM이 생성하는 1문장에만 적용 — top_row의 수치+라벨만
  허용 수치 화이트리스트에 포함, strip 후 재검증.
"""

import asyncio
import logging
import time
from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from llm_narrative_log import log_narrative
from endpoints.trend import _is_context_enabled, _validate_summary_numbers

logger = logging.getLogger("slm")

router = APIRouter()

_get_scan_cache: Optional[Callable[[], tuple]] = None
_ollama_client = None


def init(get_scan_cache_fn, ollama_client=None):
    global _get_scan_cache, _ollama_client
    _get_scan_cache = get_scan_cache_fn
    _ollama_client = ollama_client


# ─────────────────────────────────────────────────────────────────────
# [E-023] 카테고리 분류 / 정의 / 점검 순서
# ─────────────────────────────────────────────────────────────────────

# 점검 순서 — 위급도 높은 순. 운영팀 도메인 지식 기반:
#   ① 설비 장애   = DI 신호로 확정된 사고. 즉시 출동
#   ② 교차 검증   = 상·하류 수지 불일치. 누수·월류 가능성 → 물리 피해
#   ③ 데이터 품질 = 결측·정체. 모니터링 무력화 → 2차 위험
#   ④ 값 이탈     = Z-Score 통계 경계. 정상 운영 변동 가능성 (오탐 최고)
CATEGORY_PRIORITY = ["equip_fault", "cross_check", "data_quality", "value_deviation"]

CATEGORY_LABELS = {
    "equip_fault":     "설비 장애",
    "cross_check":     "교차 검증",
    "data_quality":    "데이터 품질",
    "value_deviation": "값 이탈",
}

# 정적 정의 — LLM이 생성하지 않음. 운영팀 용어 수정 시 이 사전만 고치면 됨.
CATEGORY_MEANINGS = {
    "equip_fault":     "통신이상·UPS·펌프 등 설비 DI 직접 감지 (확정 사고)",
    "cross_check":     "상류 유입과 하류 유출의 수지 불일치 (누수·월류·계측 오류 의심)",
    "data_quality":    "결측·정체·역전 데이터 (센서·통신 점검 필요)",
    "value_deviation": "요일·시간대 기준 Z-Score 이탈 (통계적 경계, 오탐 가능)",
}

# verdict 심각도 가중치 — 같은 카테고리 내에서 최악을 뽑을 때 사용
_VERDICT_WEIGHT = {
    "복합이상": 10, "교차이상": 9, "이상": 8,
    "교차주의": 7, "주의": 6, "정상": 0,
}


def _build_scope_label(sitename: Optional[str], facilitytype: Optional[str]) -> str:
    parts = []
    if sitename:
        parts.append(sitename)
    if facilitytype:
        parts.append(facilitytype)
    return " ".join(parts) if parts else "전체"


def _classify_row(row: dict) -> set:
    """한 row가 속하는 카테고리 집합. 한 row가 여러 카테고리에 걸칠 수 있음
    (예: 설비 장애 + 교차 검증). 단 '값 이탈'은 다른 카테고리가 없을 때만."""
    cats = set()
    if row.get("equip_failure") or row.get("comm_status") == "통신장애":
        cats.add("equip_fault")
    verdict = row.get("verdict") or ""
    if verdict in ("교차이상", "교차주의", "복합이상"):
        cats.add("cross_check")
    if row.get("recent_holding") == "Y":
        cats.add("data_quality")
    if not cats and verdict in ("이상", "주의"):
        cats.add("value_deviation")
    return cats


def _count_by_category(rows: list[dict]) -> dict:
    """카테고리별 row 수 (중복 카운트 — 한 row가 두 카테고리에 걸치면 양쪽 +1)."""
    counts = {k: 0 for k in CATEGORY_PRIORITY}
    for r in rows:
        for c in _classify_row(r):
            counts[c] += 1
    return counts


def _select_most_urgent(rows: list[dict]) -> Optional[tuple]:
    """우선순위 카테고리 순서로 가장 심각한 1건을 선택.
    반환: (category_key, row_dict) 또는 None.

    같은 카테고리 내 정렬: verdict 가중치 내림차순 → z_score 절댓값 내림차순.
    """
    for cat in CATEGORY_PRIORITY:
        candidates = [r for r in rows if cat in _classify_row(r)]
        if not candidates:
            continue
        candidates.sort(key=lambda r: (
            -_VERDICT_WEIGHT.get(r.get("verdict", ""), 0),
            -abs(float(r.get("z_score") or 0)),
        ))
        return (cat, candidates[0])
    return None



# 감지 신호 원시 라벨 → 운영자 표기 (쉬운 말 서술용)
_SIGNAL_LABELS = {
    "comm_error": "통신이상",
    "ups_fault": "UPS 이상",
    "pump_fault": "펌프 이상",
}


def _signal_label(row: dict) -> str:
    raw = row.get("equip_failure") or (
        "통신장애" if row.get("comm_status") == "통신장애" else "설비 장애 신호"
    )
    return _SIGNAL_LABELS.get(str(raw), str(raw))

def _template_urgent_sentence(row: dict, cat_key: str) -> str:
    """LLM 실패 시 사용할 결정적 1문장 템플릿 — 카테고리별 근거로 서술.

    (사용자 피드백 2026-07-22: 통신장애·교차검증 건에 현재값/평균 비교를
    쓰면 "평균과 동일한데 이상"처럼 모순으로 읽힘 — 값 비교는 값 이탈에만)
    """
    site = row.get("sitename", "?")
    ft = row.get("facilitytype", "?")
    di = row.get("datainfo", "?")
    label = CATEGORY_LABELS.get(cat_key, cat_key)
    head = f"{site} {ft} {di}이(가)"

    if cat_key == "equip_fault":
        return f"{head} {_signal_label(row)} 신호로 감지되었습니다. ({label})"
    if cat_key == "cross_check":
        return (
            f"{head} 상류 유입과 하류 유출의 수지가 맞지 않아 "
            f"{row.get('verdict') or '교차이상'} 단계입니다. ({label})"
        )
    if cat_key == "data_quality":
        return f"{head} 값이 갱신되지 않고 정체(홀딩)되어 있습니다. ({label})"

    # value_deviation — 여기서만 평소(평균) 대비 값 비교 서술
    parts = [head]
    curr = row.get("current_val")
    mean = row.get("mean_30d")
    dev = row.get("deviation_pct")
    if curr is not None:
        parts.append(f"현재 {curr}")
    if mean is not None:
        parts.append(f"30일 평균 {mean} 대비")
    if dev is not None:
        parts.append(f"편차 {dev}%로")
    parts.append(f"{row.get('verdict') or '?'} 단계입니다. ({label})")
    return " ".join(parts)


def _assemble_summary(
    urgent_sentence: str,
    counts: dict,
    total_rows: int,
    urgent_cat: Optional[str],
    scope_label: str,
) -> str:
    """LLM 1문장 + Python 정적 블록 4개를 markdown-lite 형식으로 조립."""
    scope_prefix = f"({scope_label}) " if scope_label != "전체" else ""

    # 카테고리 카운트 라인 — 순서는 우선순위 그대로
    counts_parts = [
        f"{CATEGORY_LABELS[k]} {counts.get(k, 0)}건"
        for k in CATEGORY_PRIORITY
    ]
    counts_line = " · ".join(counts_parts) + f" (총 {total_rows}건 중)"

    # 가장 위급한 카테고리의 정의 1줄
    if urgent_cat:
        meaning_line = (
            f"[{CATEGORY_LABELS[urgent_cat]}] {CATEGORY_MEANINGS[urgent_cat]}"
        )
    else:
        meaning_line = ""

    # 점검 순서 (고정)
    order_line = (
        "[점검 순서] ① 설비 장애 → ② 교차 검증 → ③ 데이터 품질 → ④ 값 이탈"
    )

    sections = [
        f"[중요 알람] {scope_prefix}{urgent_sentence}",
        f"[유형별 현황] {counts_line}",
    ]
    if meaning_line:
        sections.append(meaning_line)
    sections.append(order_line)

    return "\n\n".join(sections)


# ─────────────────────────────────────────────────────────────────────
# 요청 모델
# ─────────────────────────────────────────────────────────────────────

class ScanAllExplainRequest(BaseModel):
    # top_n은 하위호환을 위해 유지하지만 [E-023] 이후 응답은 단일 위급 1건 + 카테고리 집계
    top_n: int = Field(3, ge=1, le=10, description="(레거시) 응답에서 사용되지 않음")
    # 시설 필터 ([E-022])
    sitename: Optional[str] = Field(None, description="필터: 현장명 (정확 매칭)")
    facilitytype: Optional[str] = Field(None, description="필터: 시설유형 (정확 매칭)")


# ─────────────────────────────────────────────────────────────────────
# 메인 엔드포인트
# ─────────────────────────────────────────────────────────────────────

@router.post("/anomaly/scan-all/explain")
async def explain_scan_all(req: ScanAllExplainRequest = ScanAllExplainRequest()):
    """[E-023] Hybrid 응답: LLM 1문장 + Python 정적 조립."""
    _context_mode = "on" if _is_context_enabled("SITE_SETTING", "TREND_EXPLAIN_CONTEXT") else "off"
    _ctx_t0 = time.perf_counter()

    if _get_scan_cache is None:
        raise HTTPException(500, "scan_all_explain not initialized")
    cache, cache_ts = _get_scan_cache()
    if not cache:
        try:
            from anomaly_scan import _compute_anomaly_scan_all
            logger.info("scan_all_explain: 캐시 미스 → 동기 계산 시작")
            cache = await asyncio.to_thread(_compute_anomaly_scan_all)
        except Exception as e:
            logger.warning(f"scan_all_explain: 동기 계산 실패: {e}")
            cache = None
    if not cache:
        return {
            "summary": "이상감지 데이터가 아직 준비되지 않았습니다.",
            "source": "fallback",
            "context_used": [],
            "context_fetch_ms": 0,
            "context_mode": _context_mode,
            "llm_generate_ms": 0,
            "allowed_numbers_count": 0,
        }

    raw_rows = cache.get("rows") or []
    columns = cache.get("columns") or []
    if not raw_rows or not columns:
        return {
            "summary": "이상감지 데이터가 비어 있습니다.",
            "source": "fallback",
            "context_used": [],
            "context_fetch_ms": 0,
            "context_mode": _context_mode,
            "llm_generate_ms": 0,
            "allowed_numbers_count": 0,
        }

    def _as_dict(r) -> dict:
        if isinstance(r, dict):
            return r
        return {c: r[i] if i < len(r) else None for i, c in enumerate(columns)}

    rows: list[dict] = [_as_dict(r) for r in raw_rows]

    # ── 시설 필터 ([E-022]) ──
    scope_label = _build_scope_label(req.sitename, req.facilitytype)
    if req.sitename or req.facilitytype:
        before = len(rows)
        if req.sitename:
            rows = [r for r in rows if r.get("sitename") == req.sitename]
        if req.facilitytype:
            rows = [r for r in rows if r.get("facilitytype") == req.facilitytype]
        logger.info(
            f"scan_all_explain scope 필터: {scope_label!r} — {before} → {len(rows)} rows"
        )

    total_rows = len(rows)
    _context_fetch_ms = int((time.perf_counter() - _ctx_t0) * 1000)
    _context_used = ["scan_cache"]

    # ── 0건 처리 — scope/전역 양쪽 ──
    if total_rows == 0:
        msg = (
            f"{scope_label}에 현재 이상 탐지된 태그가 없습니다."
            if scope_label != "전체"
            else "현재 이상 탐지된 태그가 없습니다. 전체 시스템이 정상 범위에서 동작 중입니다."
        )
        return {
            "summary": msg,
            "source": "template",
            "context_used": _context_used + (["scope_filter"] if scope_label != "전체" else []),
            "context_fetch_ms": _context_fetch_ms,
            "context_mode": _context_mode,
            "llm_generate_ms": 0,
            "allowed_numbers_count": 0,
            "scope": scope_label,
            "category_counts": {k: 0 for k in CATEGORY_PRIORITY},
            "total_rows": 0,
        }

    # ── [E-023] Hybrid 핵심: 가장 위급한 1건 선택 + 카테고리 집계 ──
    urgent = _select_most_urgent(rows)
    counts = _count_by_category(rows)

    # 위급한 1건을 못 뽑으면 (모든 row가 정상) 정상 응답
    if urgent is None:
        return {
            "summary": _assemble_summary(
                urgent_sentence="이상 단계 태그가 없으며 전 시설 정상 범위에서 동작 중입니다.",
                counts=counts,
                total_rows=total_rows,
                urgent_cat=None,
                scope_label=scope_label,
            ),
            "source": "template",
            "context_used": _context_used,
            "context_fetch_ms": _context_fetch_ms,
            "context_mode": _context_mode,
            "llm_generate_ms": 0,
            "allowed_numbers_count": 0,
            "scope": scope_label,
            "category_counts": counts,
            "total_rows": total_rows,
        }

    urgent_cat, urgent_row = urgent
    urgent_label = CATEGORY_LABELS[urgent_cat]

    # 허용 수치 — 가장 위급한 1건의 수치 + 프롬프트 상수
    # ([E-023] 디버그: 30("30일 평균"), 1("1문장")이 LLM 응답에 종종 등장 → 화이트리스트 포함)
    allowed_numbers: list[float] = [0.0, 1.0, 30.0]
    for key in ("z_score", "deviation_pct", "current_val", "mean_30d"):
        v = urgent_row.get(key)
        if v is not None:
            try:
                allowed_numbers.append(float(v))
            except (TypeError, ValueError):
                pass

    # ── LLM 1문장 프롬프트 — 카테고리별 근거만 제공 ──
    # (사용자 피드백 2026-07-22: 통신장애·교차검증 건에 현재값/30일 평균을
    #  제공하면 "평균과 동일한데 이상 판정"처럼 모순으로 읽히는 문장이 생성됨.
    #  판정 근거가 되는 정보만 카테고리별로 주입한다)
    base_lines = (
        f"- 시설: {urgent_row.get('sitename','?')} {urgent_row.get('facilitytype','?')}\n"
        f"- 태그: {urgent_row.get('datainfo','?')}\n"
    )
    if urgent_cat == "equip_fault":
        evidence = f"- 감지 신호: {_signal_label(urgent_row)} (설비 DI 직접 감지)\n"
        reason_rule = (
            "5. 판정 근거는 위 '감지 신호'다 — 현재값·평균 비교로 서술하지 마라\n"
        )
    elif urgent_cat == "cross_check":
        evidence = (
            f"- 판정: {urgent_row.get('verdict','?')}\n"
            "- 근거: 상류 유입과 하류 유출의 수지 불일치 (교차 검증)\n"
        )
        reason_rule = (
            "5. 판정 근거는 상류·하류 수지 불일치다 — 태그 자체의 현재값·평균 "
            "비교로 서술하지 마라\n"
        )
    elif urgent_cat == "data_quality":
        evidence = "- 감지: 값이 갱신되지 않고 정체(홀딩)\n"
        reason_rule = "5. 판정 근거는 값 정체다 — 현재값·평균 비교로 서술하지 마라\n"
    else:  # value_deviation — 여기서만 값 비교가 판정 근거
        evidence = (
            f"- 현재값: {urgent_row.get('current_val')}\n"
            f"- 30일 평균: {urgent_row.get('mean_30d')}\n"
            f"- 편차: {urgent_row.get('deviation_pct')}%\n"
            f"- 판정: {urgent_row.get('verdict','?')}\n"
        )
        reason_rule = (
            "5. 'Z-Score' 같은 통계 용어 대신 '평소보다 크게 높습니다/낮습니다'"
            "처럼 쉬운 표현으로 서술하고 수치는 괄호로 병기하라\n"
        )
    prompt = (
        "다음은 이상 스캔에서 가장 위급한 1건이다. **단 1문장**으로 자연어 서술하라.\n\n"
        "## 대상 정보\n"
        f"{base_lines}{evidence}"
        f"- 유형: {urgent_label}\n\n"
        "## 절대 규칙\n"
        "1. 위 수치·명칭만 사용. 외부 지식 추가 금지\n"
        "2. **1문장만**. 존댓말 '~습니다' 종결\n"
        f"3. 문장 끝에 유형을 괄호로 명시: ({urgent_label})\n"
        "4. 권고·조치·원인추측 금지 (현황 서술만)\n"
        f"{reason_rule}\n"
        "출력 (1문장):"
    )

    # ── LLM 호출 (Ollama 미가용 시 즉시 템플릿 폴백) ──
    if not _ollama_client:
        return {
            "summary": _assemble_summary(
                urgent_sentence=_template_urgent_sentence(urgent_row, urgent_cat),
                counts=counts,
                total_rows=total_rows,
                urgent_cat=urgent_cat,
                scope_label=scope_label,
            ),
            "source": "template",
            "context_used": _context_used,
            "context_fetch_ms": _context_fetch_ms,
            "context_mode": _context_mode,
            "llm_generate_ms": 0,
            "allowed_numbers_count": len(allowed_numbers),
            "scope": scope_label,
            "category_counts": counts,
            "total_rows": total_rows,
            "urgent_category": urgent_cat,
        }

    _t0 = time.perf_counter()
    try:
        # num_predict는 None으로 (모델 기본값) — gemma4가 chat 템플릿 토큰을
        # 먼저 생성하므로 num_predict<300이면 visible response가 빈 문자열로
        # 잘리는 동작 관찰됨 ([E-023] 디버그)
        text = await asyncio.to_thread(
            _ollama_client.generate,
            prompt,
            None, None, None,
            120.0, 3,
        )
    except Exception as e:
        logger.warning(f"scan_all_explain LLM 실패: {e}")
        return {
            "summary": _assemble_summary(
                urgent_sentence=_template_urgent_sentence(urgent_row, urgent_cat),
                counts=counts,
                total_rows=total_rows,
                urgent_cat=urgent_cat,
                scope_label=scope_label,
            ),
            "source": "template",
            "error": str(e),
            "context_used": _context_used,
            "context_fetch_ms": _context_fetch_ms,
            "context_mode": _context_mode,
            "llm_generate_ms": int((time.perf_counter() - _t0) * 1000),
            "allowed_numbers_count": len(allowed_numbers),
            "scope": scope_label,
            "category_counts": counts,
            "total_rows": total_rows,
            "urgent_category": urgent_cat,
        }

    _llm_ms = int((time.perf_counter() - _t0) * 1000)
    text = (text or "").strip()

    # ── 할루시네이션 검증 ──
    strip_strings = []
    for key in ("sitename", "facilitytype", "datainfo", "tagsn"):
        v = urgent_row.get(key)
        if v:
            strip_strings.append(str(v))

    ok, violations = _validate_summary_numbers(text, allowed_numbers)
    if not ok or not text:
        cleaned = text
        for s in sorted(set(strip_strings), key=len, reverse=True):
            cleaned = cleaned.replace(s, " ")
        ok, violations = _validate_summary_numbers(cleaned, allowed_numbers)

    if not ok or not text:
        logger.warning(f"scan_all_explain 할루시네이션 → 템플릿 폴백: 위반={violations}")
        log_narrative(
            endpoint="anomaly/scan-all/explain",
            params={"top_n": req.top_n, "scope": scope_label},
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
            "summary": _assemble_summary(
                urgent_sentence=_template_urgent_sentence(urgent_row, urgent_cat),
                counts=counts,
                total_rows=total_rows,
                urgent_cat=urgent_cat,
                scope_label=scope_label,
            ),
            "source": "fallback",
            "llm_rejected": True,
            "violations": violations,
            "context_used": _context_used,
            "context_fetch_ms": _context_fetch_ms,
            "context_mode": _context_mode,
            "llm_generate_ms": _llm_ms,
            "allowed_numbers_count": len(allowed_numbers),
            "scope": scope_label,
            "category_counts": counts,
            "total_rows": total_rows,
            "urgent_category": urgent_cat,
        }

    # ── 성공 — LLM 문장 + 정적 블록 조립 ──
    final_summary = _assemble_summary(
        urgent_sentence=text,
        counts=counts,
        total_rows=total_rows,
        urgent_cat=urgent_cat,
        scope_label=scope_label,
    )

    logger.info(
        f"scan_all_explain[hybrid]: scope={scope_label!r} urgent={urgent_cat} "
        f"counts={counts} ⏱ ctx={_context_fetch_ms}ms, llm={_llm_ms}ms"
    )
    log_narrative(
        endpoint="anomaly/scan-all/explain",
        params={
            "top_n": req.top_n,
            "scope": scope_label,
            "urgent_category": urgent_cat,
            "category_counts": counts,
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
        "summary": final_summary,
        "source": "llm",
        "context_used": _context_used,
        "context_fetch_ms": _context_fetch_ms,
        "context_mode": _context_mode,
        "llm_generate_ms": _llm_ms,
        "allowed_numbers_count": len(allowed_numbers),
        "scope": scope_label,
        "category_counts": counts,
        "total_rows": total_rows,
        "urgent_category": urgent_cat,
    }
