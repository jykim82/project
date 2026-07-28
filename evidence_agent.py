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


async def collect_evidence(conn_factory, ctx: dict) -> dict | None:
    """근거 팩 수집. ctx = {sitename, facilitytype, anomaly_tags, upstreams}.

    실패해도 예외를 밖으로 던지지 않는다 — 근거는 진단 응답의 부가물이다.
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
        pack = {
            "items": results,
            "summary": _compose_summary(ctx.get("sitename") or "", results),
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
        }
        logger.info(
            f"[evidence] {ctx.get('sitename')} 도구 {len(results)}개 "
            f"{pack['elapsed_ms']}ms — " +
            ", ".join(f"{r['tool']}:{r['status']}" for r in results)
        )
        return pack
    except Exception as e:
        logger.warning(f"[evidence] 수집 실패 (진단 응답은 유지): {e}")
        return None
