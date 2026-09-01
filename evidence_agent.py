"""
제한적 에이전트 루프 — 진단 근거 수집기 (docs/agent-loop-spec.md, 로드맵 A P1)

룰 기반 라우터가 화이트리스트 조회 도구를 골라 병렬 실행해 근거 팩을 만든다.
- 도구 선택은 룰(applicable) — LLM 이 고르지 않는다 (부록 B.1)
- 조회 전용 · 도구 최대 4개 · 도구당 3초 · 전체 8초
- 실패·타임아웃도 팩에 남긴다 (침묵 탈락 금지 — 감사 추적)
- 종합 소견은 결정적 조합 — 도구가 반환한 사실만 문장으로 잇는다 (P1 은
  생성 모델 없음. LLM 문체 다듬기는 P2 — shared.llm_narrative 검증 전제)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

logger = logging.getLogger("slm")

_TOOL_TIMEOUT_S = 3.0
_TOTAL_TIMEOUT_S = 8.0
_MAX_TOOLS = 4
_MAX_TAGS = 3          # same_hour_history 대상 이상 태그 상한
_MAX_ITEMS = 5         # 도구당 반환 항목 상한


# ---------------------------------------------------------------------------
# 도구 구현 — 전부 조회 전용. conn 은 도구별 독립 (병렬 실행)
# ---------------------------------------------------------------------------

def _tool_same_hour_history(conn, ctx: dict) -> dict:
    """이상 태그의 14일 동일 시간대 분포 vs 현재값 — 평소와 다른가."""
    items = []
    cur = conn.cursor()
    try:
        for tag in ctx["anomaly_tags"][:_MAX_TAGS]:
            # E-056: logtime 하한 필수
            cur.execute(
                """
                SELECT round(percentile_cont(0.5) WITHIN GROUP (ORDER BY val)::numeric, 2),
                       round(percentile_cont(0.1) WITHIN GROUP (ORDER BY val)::numeric, 2),
                       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY val)::numeric, 2)
                FROM tb_tag_raw_data
                WHERE tagsn = %s AND logtime > now() - interval '14 days'
                  AND extract(hour from logtime) = extract(hour from now())
                  AND val IS NOT NULL
                """,
                (tag["tagsn"],),
            )
            r = cur.fetchone()
            if not r or r[0] is None:
                continue
            med, p10, p90 = float(r[0]), float(r[1]), float(r[2])
            cur_val = tag.get("current_val")
            out_of_band = (cur_val is not None
                           and not (p10 <= float(cur_val) <= p90))
            items.append({
                "datainfo": tag.get("datainfo") or tag["tagsn"],
                "current": cur_val,
                "usual_median": med,
                "usual_p10": p10,
                "usual_p90": p90,
                "out_of_band": out_of_band,
            })
    finally:
        cur.close()
    n_out = sum(1 for i in items if i["out_of_band"])
    summary = (
        f"이상 태그 {len(items)}건 중 {n_out}건이 평소 이 시간대 범위(p10~p90)를 벗어남"
        if items else "동일 시간대 이력 없음"
    )
    return {"summary": summary, "items": items}


def _tool_upstream_status(conn, ctx: dict) -> dict:
    """상류 시설 진행중 알람 — 원인이 상류에 있는가."""
    ups = ctx.get("upstreams") or []
    if not ups:
        return {"summary": "상류 시설 없음", "items": []}
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT sitename, facilitytype, alarm_msg,
                   TO_CHAR(alarm_start_time, 'MM-DD HH24:MI')
            FROM tb_equipment_alarm_report
            WHERE sitename = ANY(%s) AND alarm_status = '진행중'
            ORDER BY alarm_start_time DESC LIMIT %s
            """,
            (ups, _MAX_ITEMS),
        )
        items = [{"sitename": r[0], "facilitytype": r[1] or "",
                  "alarm_msg": r[2] or "", "since": r[3]} for r in cur.fetchall()]
    finally:
        cur.close()
    summary = (
        f"상류 {len(ups)}곳 중 진행중 알람 {len(items)}건"
        if items else f"상류 {len(ups)}곳 진행중 알람 없음"
    )
    return {"summary": summary, "items": items}


def _tool_knowledge_cards(conn, ctx: dict) -> dict:
    """현장 지식 카드 매칭 (site-knowledge-spec 재사용)."""
    from endpoints.site_knowledge import find_matching_cards

    cards = find_matching_cards(
        conn, ctx["sitename"], ctx.get("facilitytype") or "",
    )[:_MAX_ITEMS]
    items = [{"k_type": c["k_type"], "title": c["title"],
              "description": c["description"]} for c in cards]
    summary = (f"관련 현장 지식 {len(items)}건" if items
               else "등록된 현장 지식 없음")
    return {"summary": summary, "items": items}


def _tool_recent_actions(conn, ctx: dict) -> dict:
    """최근 30일 작업·조치 이력 — 이미 알려진 문제인가."""
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT task_category, COALESCE(fault_category, ''),
                   left(COALESCE(task_content, ''), 60),
                   CASE WHEN task_end_time IS NULL THEN '진행중' ELSE '완료' END,
                   TO_CHAR(created_at, 'MM-DD')
            FROM tb_task_master
            WHERE sitename = %s AND created_at > now() - interval '30 days'
            ORDER BY created_at DESC LIMIT %s
            """,
            (ctx["sitename"], _MAX_ITEMS),
        )
        items = [{"category": r[0] or "", "fault_category": r[1],
                  "title": r[2], "status": r[3], "date": r[4]}
                 for r in cur.fetchall()]
    finally:
        cur.close()
    summary = (f"최근 30일 작업 {len(items)}건" if items
               else "최근 30일 작업 이력 없음")
    return {"summary": summary, "items": items}


# (도구, 라벨, applicable) — 라우터가 문맥으로 선별한다
_TOOLS = [
    ("same_hour_history", "평소 동일 시간대 대비", _tool_same_hour_history,
     lambda ctx: bool(ctx.get("anomaly_tags"))),
    ("upstream_status", "상류 시설 알람", _tool_upstream_status,
     lambda ctx: bool(ctx.get("sitename"))),
    ("knowledge_cards", "현장 지식", _tool_knowledge_cards,
     lambda ctx: bool(ctx.get("sitename"))),
    ("recent_actions", "최근 조치 이력", _tool_recent_actions,
     lambda ctx: bool(ctx.get("sitename"))),
]


async def _run_tool(conn_factory, name, label, fn, ctx: dict) -> dict:
    t0 = time.monotonic()

    def _call():
        conn = conn_factory()
        try:
            return fn(conn, ctx)
        finally:
            conn.close()

    try:
        res = await asyncio.wait_for(asyncio.to_thread(_call), _TOOL_TIMEOUT_S)
        status = "ok" if res.get("items") else "empty"
        return {"tool": name, "label": label, "status": status,
                "summary": res["summary"], "items": res.get("items") or [],
                "elapsed_ms": int((time.monotonic() - t0) * 1000)}
    except asyncio.TimeoutError:
        logger.warning(f"[evidence] {name} 타임아웃 ({_TOOL_TIMEOUT_S}s)")
        return {"tool": name, "label": label, "status": "timeout",
                "summary": "확인 실패 (시간 초과)", "items": [],
                "elapsed_ms": int(_TOOL_TIMEOUT_S * 1000)}
    except Exception as e:
        logger.warning(f"[evidence] {name} 실패: {e}")
        return {"tool": name, "label": label, "status": "error",
                "summary": "확인 실패", "items": [],
                "elapsed_ms": int((time.monotonic() - t0) * 1000)}


def _compose_summary(sitename: str, results: list[dict]) -> str:
    """결정적 종합 소견 — 도구 요약 1줄씩 잇는다. 생성 없음."""
    parts = [r["summary"] for r in results if r["status"] in ("ok", "empty")]
    return f"{sitename} 진단 근거 — " + " · ".join(parts) if parts else ""


# ── LLM 문체 서술 (P2) ──────────────────────────────────────────
# 결정적 summary 는 정본으로 유지하고, LLM 서술은 별도 narrative 필드 —
# 프런트가 "[AI 참고 의견]" violet 톤으로 구분 표시 (Zero-Hallucination:
# AI 의견은 DB 사실과 시각·구조적으로 분리).
# gemma4 26B 가 2~3문장 생성에 5~10초 — 진단 응답 자체가 ~20초라 수용 범위
_NARRATE_TIMEOUT_S = 12.0

_NARRATE_PROMPT = """당신은 상수도 관제 시스템의 진단 보조자다. 아래 '수집된 사실'만으로
현장 운영자가 읽을 2~3문장 한국어 소견을 써라.
규칙: 사실에 없는 수치·원인·추측을 만들지 말 것. 제공된 수치는 그대로 인용.
조치 지시는 하지 말 것 (판단은 운영자 몫). 문장만 출력.

[수집된 사실]
{facts}"""


def _fact_lines(sitename: str, results: list[dict]) -> str:
    lines = [f"- 대상 시설: {sitename}"]
    for r in results:
        if r["status"] in ("ok", "empty"):
            lines.append(f"- {r['label']}: {r['summary']}")
            for item in (r.get("items") or [])[:3]:
                # DB Decimal 등 비직렬 타입은 문자열로 (default=str)
                lines.append(f"  · {json.dumps(item, ensure_ascii=False, default=str)}")
    return "\n".join(lines)


async def _narrate(ollama, sitename: str, results: list[dict]) -> str | None:
    """LLM 서술 — 수치 검증 실패·타임아웃이면 None (결정적 summary 만 표시)."""
    if ollama is None:
        return None
    try:
        from shared.llm_narrative import (
            extract_numbers, strip_identifier_strings, validate_numbers_in_text,
        )

        facts = _fact_lines(sitename, results)
        t0 = time.monotonic()
        text = await asyncio.wait_for(
            asyncio.to_thread(
                ollama.generate, _NARRATE_PROMPT.format(facts=facts),
                num_predict=180, timeout=_NARRATE_TIMEOUT_S,
            ),
            _NARRATE_TIMEOUT_S + 1,
        )
        text = (text or "").strip()
        if not text:
            logger.info("[evidence] LLM 서술 빈 응답 — 결정적 소견만 표시")
            return None
        # 허용 수치 = 사실 텍스트의 수치 전부. 시설명 등 식별 문자열은 제거
        allowed = extract_numbers(strip_identifier_strings(facts, [sitename]))
        ok, violations = validate_numbers_in_text(text, allowed, [sitename])
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        try:
            from llm_narrative_log import log_narrative
            log_narrative(
                "evidence_narrative", params={"sitename": sitename},
                llm_generate_ms=elapsed_ms, llm_rejected=not ok,
                allowed_count=len(allowed), violations=violations or None,
            )
        except Exception:
            pass
        if not ok:
            logger.info(f"[evidence] LLM 서술 수치 위반 기각: {violations}")
            return None
        return text
    except asyncio.TimeoutError:
        logger.info("[evidence] LLM 서술 타임아웃 — 결정적 소견만 표시")
        return None
    except Exception as e:
        logger.info(f"[evidence] LLM 서술 실패 (결정적 소견만 표시): {e}")
        return None


async def collect_evidence(conn_factory, ctx: dict, ollama=None) -> dict | None:
    """근거 팩 수집. ctx = {sitename, facilitytype, anomaly_tags, upstreams}.

    실패해도 예외를 밖으로 던지지 않는다 — 근거는 진단 응답의 부가물이다.
    ollama 주입 시 LLM 서술(narrative) 시도 — 검증 실패·타임아웃이면 생략.
    """
    try:
        plan = [(n, l, f) for n, l, f, ok in _TOOLS if ok(ctx)][:_MAX_TOOLS]
        if not plan:
            return None
        t0 = time.monotonic()
        results = await asyncio.wait_for(
            asyncio.gather(*[
                _run_tool(conn_factory, n, l, f, ctx) for n, l, f in plan
            ]),
            _TOTAL_TIMEOUT_S,
        )
        sitename = ctx.get("sitename") or ""
        pack = {
            "items": results,
            "summary": _compose_summary(sitename, results),
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
        }
        narrative = await _narrate(ollama, sitename, results)
        if narrative:
            pack["narrative"] = narrative
        logger.info(
            f"[evidence] {ctx.get('sitename')} 도구 {len(results)}개 "
            f"{pack['elapsed_ms']}ms — " +
            ", ".join(f"{r['tool']}:{r['status']}" for r in results)
        )
        return pack
    except Exception as e:
        logger.warning(f"[evidence] 수집 실패 (진단 응답은 유지): {e}")
        return None
