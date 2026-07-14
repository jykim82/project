# -*- coding: utf-8 -*-
"""채팅 스모크 테스트 — 대표 인텐트 자동 E2E (/ask/stream, 실제 프런트 경로).

목적:
- 회귀 안전망: 분류·응답 구조가 기대와 다르면 즉시 fail (exit 1)
- ai_server 리팩토링(Phase 5 등) 선행 조건
- 폐쇄망 납품 시 설치 검수 도구 (대표 질의 전체 정상 확인 ~2분)

기존 test_all_intents.py 와의 차이:
- 그쪽: 전수(68개) 데이터 존재 '진단' (/ask, assert 없음)
- 이쪽: 대표(16개) 구조 'assert' (/ask/stream, exit code)

사용:
  python test_chat_smoke.py                 # 전체 실행
  python test_chat_smoke.py --case 스캔      # 질의 부분일치 필터
  python test_chat_smoke.py --api http://host:8000 --region R01

판정 (chat_smoke_cases.json):
- status == OK
- intent 일치
- graph_type 일치 (케이스에 있으면)
- min_rows 이상 (케이스에 있으면 — '비면 확실히 고장'인 것만 지정)
- require_fields 존재·비어있지 않음 (설계상 항상 생성되는 분석 구조만)

주의: 재기동 직후 이상탐지 스캔 캐시 공백으로 ERROR 가 날 수 있어
status=ERROR 는 RETRY_WAIT 초 후 1회 재시도한다 (분리 회귀와 구분).
"""
import argparse
import json
import os
import sys
import time
import urllib.request


def parse_args():
    p = argparse.ArgumentParser(description="채팅 스모크 테스트")
    p.add_argument("--api", default=os.environ.get("SMOKE_API", "http://localhost:8000"))
    p.add_argument("--region", default=os.environ.get("SMOKE_REGION", "R01"))
    p.add_argument("--user", default=os.environ.get("SMOKE_USER", "jykim"))
    p.add_argument("--case", default=None, help="질의 부분일치 필터")
    p.add_argument("--retry-wait", type=int, default=30,
                   help="ERROR 시 재시도 대기(초) — 캐시 웜업 대응")
    p.add_argument("--timeout", type=int, default=180)
    return p.parse_args()


def ask_stream(api: str, question: str, region: str, user: str, timeout: int):
    """SSE result 이벤트의 payload(dict)와 소요시간(초)을 반환."""
    body = json.dumps(
        {"user_question": question, "region": region, "user_id": user}
    ).encode()
    req = urllib.request.Request(
        f"{api}/ask/stream", data=body,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
    )
    t0 = time.perf_counter()
    ev, result = None, None
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line.startswith("event: "):
                ev = line[7:].strip()
            elif line.startswith("data: ") and ev == "result":
                try:
                    result = json.loads(line[6:])
                except json.JSONDecodeError:
                    pass
                ev = None
    return result, time.perf_counter() - t0


def judge(case: dict, res: dict) -> list[str]:
    """케이스 기대값 대비 실패 사유 목록 (비면 통과)."""
    fails = []
    if res.get("status") != "OK":
        fails.append(f"status={res.get('status')}")
    if res.get("intent") != case["intent"]:
        fails.append(f"intent={res.get('intent')} (기대 {case['intent']})")
    if "graph_type" in case and res.get("graph_type") != case["graph_type"]:
        fails.append(f"graph_type={res.get('graph_type')} (기대 {case['graph_type']})")
    if "min_rows" in case:
        rows = len(res.get("data") or [])
        if rows < case["min_rows"]:
            fails.append(f"rows={rows} < {case['min_rows']}")
    for f in case.get("require_fields", []):
        if not res.get(f):
            fails.append(f"필드 없음/비어있음: {f}")
    return fails


def run_case(case: dict, args) -> dict:
    q = case["question"]
    try:
        res, elapsed = ask_stream(args.api, q, args.region, args.user, args.timeout)
    except Exception as e:
        return {"case": case, "ok": False, "fails": [f"요청 실패: {e}"], "elapsed": 0.0}
    if res is None:
        return {"case": case, "ok": False, "fails": ["result 이벤트 없음"], "elapsed": elapsed}

    # 캐시 웜업 대응: ERROR 는 1회 재시도 (재기동 직후 스캔 캐시 공백)
    if res.get("status") == "ERROR":
        print(f"  … ERROR (캐시 웜업 가능성) — {args.retry_wait}s 후 재시도: {q}")
        time.sleep(args.retry_wait)
        try:
            res, elapsed = ask_stream(args.api, q, args.region, args.user, args.timeout)
        except Exception as e:
            return {"case": case, "ok": False, "fails": [f"재시도 실패: {e}"], "elapsed": 0.0}
        if res is None:
            return {"case": case, "ok": False, "fails": ["재시도: result 없음"], "elapsed": elapsed}

    fails = judge(case, res)
    return {"case": case, "ok": not fails, "fails": fails, "elapsed": elapsed}


def main():
    args = parse_args()
    cases_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_smoke_cases.json")
    with open(cases_path, encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    if args.case:
        cases = [c for c in cases if args.case in c["question"]]
        if not cases:
            print(f"필터 '{args.case}' 일치 케이스 없음")
            return 1

    print(f"채팅 스모크: {len(cases)}케이스 → {args.api} (region={args.region})")
    print("=" * 78)
    results = []
    t0 = time.perf_counter()
    for i, case in enumerate(cases, 1):
        r = run_case(case, args)
        results.append(r)
        mark = "✅" if r["ok"] else "❌"
        print(f"{mark} [{i:2d}/{len(cases)}] {r['elapsed']:5.1f}s  {case['question']}")
        for fail in r["fails"]:
            print(f"      ↳ {fail}")
    total = time.perf_counter() - t0

    n_fail = sum(1 for r in results if not r["ok"])
    print("=" * 78)
    print(f"결과: {len(results) - n_fail}/{len(results)} 통과 · 총 {total:.0f}s")
    if n_fail:
        print("\n실패 케이스:")
        for r in results:
            if not r["ok"]:
                reg = r["case"].get("regression")
                print(f"  - {r['case']['question']}: {'; '.join(r['fails'])}")
                if reg:
                    print(f"    (회귀 이력: {reg})")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
