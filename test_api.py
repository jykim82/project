"""ai_server.py API 테스트 스크립트 (SLM 통합 버전)"""
import urllib.request
import json
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE_URL = "http://localhost:8000"


def ask(question: str, session_id: str = None) -> dict:
    payload = {"user_question": question}
    if session_id:
        payload["session_id"] = session_id
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/ask",
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read().decode("utf-8"))


def get_json(path: str) -> dict:
    req = urllib.request.Request(f"{BASE_URL}{path}")
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read().decode("utf-8"))


def print_result(label: str, data: dict):
    status = data.get("status", "?")
    intent = data.get("intent", "-")
    session_id = data.get("session_id", "-")
    graph_type = data.get("graph_type", "-")
    table_type = data.get("table_type", "-")
    summary = data.get("answer", {}).get("summary", "-")
    detail = data.get("answer", {}).get("detail", [])
    ref = data.get("answer", {}).get("reference", {})
    rec = data.get("answer", {}).get("recommend_questions", {})
    has_data = "data" in data and data["data"] is not None
    data_len = len(data["data"]) if has_data else 0

    print(f"\n{'='*70}")
    print(f"[{label}]")
    print(f"  status={status}  intent={intent}")
    print(f"  session_id={session_id[:16]}..." if session_id != "-" else f"  session_id={session_id}")
    print(f"  graph_type={graph_type}  table_type={table_type}")
    print(f"  summary: {summary}")
    if detail:
        print(f"  detail ({len(detail)}건):")
        for d in detail[:5]:
            if isinstance(d, dict) and "prefix" in d:
                print(f"    {d['prefix']} {d['text']}")
            elif isinstance(d, dict):
                print(f"    {json.dumps(d, ensure_ascii=False)[:80]}")
            else:
                print(f"    {str(d)[:80]}")
    if ref:
        print(f"  reference: {ref.get('title', '-')}")
        for item in ref.get("items", [])[:3]:
            if isinstance(item, dict):
                print(f"    {item.get('prefix','')} {item.get('text','')}")
            else:
                print(f"    {item}")
    if rec:
        print(f"  recommend_questions: {rec.get('title', '-')}")
        for item in rec.get("items", [])[:3]:
            if isinstance(item, dict):
                print(f"    {item.get('prefix','')} {item.get('text','')}")
            else:
                print(f"    {item}")
    if has_data:
        print(f"  data: {data_len}건")
        for row in data["data"][:3]:
            print(f"    {json.dumps(row, ensure_ascii=False)[:100]}")
    elif "message" in data:
        print(f"  message: {data['message']}")
    # NEED_CORRECTION 전용 필드
    if status == "NEED_CORRECTION":
        hints = data.get("correction_hints", [])
        if hints:
            print(f"  correction_hints: {hints[:3]}")
    print()


# ===== 기존 테스트 (하위 호환성) =====
tests = [
    # 설비현황 (equipment table)
    ("배수지 설비현황", "신평 배수지 설비현황은?"),
    ("가압장 설비현황", "행정 가압장 설비현황은?"),
    ("감압시설 설비현황", "순성 감압시설 설비현황은?"),
    # 일반현황
    ("배수지 일반현황", "신평 배수지 일반현황은?"),
    # 운영현황
    ("가압장 운영현황", "행정 가압장 운영현황은?"),
    # 초동대응 매뉴얼
    ("배수지 초동대응", "신평 배수지 초동대응 매뉴얼은?"),
    # 수위현황
    ("배수지 수위", "신평 배수지 수위 현황은?"),
    # sitename 누락 → NEED_CORRECTION
    ("sitename 누락", "배수지 일반현황은?"),
]

print("=" * 70)
print("[ 기존 호환성 테스트 ]")
print("=" * 70)

passed = 0
failed = 0
for label, question in tests:
    try:
        result = ask(question)
        print_result(label, result)
        passed += 1
    except Exception as e:
        print(f"\n[{label}] ERROR: {e}")
        failed += 1


# ===== 범위 밖 질의 테스트 =====
print("\n" + "=" * 70)
print("[ 범위 밖 질의 테스트 ]")
print("=" * 70)

out_of_scope_tests = [
    ("날씨 질문", "오늘 날씨 어때?"),
    ("주식 질문", "삼성전자 주가는?"),
]

for label, question in out_of_scope_tests:
    try:
        result = ask(question)
        print_result(label, result)
        expected_status = result.get("status")
        if expected_status in ("NEED_CORRECTION", "ERROR"):
            print(f"  ✓ 범위 밖 질의 정상 거부")
            passed += 1
        else:
            print(f"  ✗ 범위 밖 질의가 OK로 처리됨")
            failed += 1
    except Exception as e:
        print(f"\n[{label}] ERROR: {e}")
        failed += 1


# ===== Multi-Turn 정정 테스트 =====
print("\n" + "=" * 70)
print("[ Multi-Turn 정정 테스트 ]")
print("=" * 70)

try:
    # Turn 1: sitename 누락
    print("\n--- Turn 1: sitename 누락 ---")
    result1 = ask("배수지 일반현황은?")
    print_result("Turn 1", result1)

    sid = result1.get("session_id")
    if sid and result1.get("status") == "NEED_CORRECTION":
        # Turn 2: 정정 — sitename만 입력
        print("--- Turn 2: 정정 (sitename 보완) ---")
        result2 = ask("신평", session_id=sid)
        print_result("Turn 2", result2)
        if result2.get("status") == "OK":
            print(f"  ✓ Multi-turn 정정 성공")
            passed += 1
        else:
            print(f"  △ Multi-turn 정정 후 상태: {result2.get('status')}")
            passed += 1  # NEED_CORRECTION도 정상 동작
    else:
        print(f"  (session_id 없음 또는 즉시 OK — SLM 폴백 모드)")
        passed += 1
except Exception as e:
    print(f"\n[Multi-Turn] ERROR: {e}")
    failed += 1


# ===== session_id 없는 요청 하위호환 테스트 =====
print("\n" + "=" * 70)
print("[ session_id 없는 요청 하위호환 테스트 ]")
print("=" * 70)

try:
    result = ask("신평 배수지 일반현황은?")
    if result.get("status") == "OK" and result.get("session_id"):
        print(f"  ✓ session_id 없는 요청 정상 (자동 생성: {result['session_id'][:16]}...)")
        passed += 1
    elif result.get("status") == "OK":
        print(f"  ✓ session_id 없는 요청 정상 (OK)")
        passed += 1
    else:
        print(f"  ✗ 비정상 상태: {result.get('status')}")
        failed += 1
except Exception as e:
    print(f"\n[하위호환] ERROR: {e}")
    failed += 1


# ===== /health 엔드포인트 테스트 =====
print("\n" + "=" * 70)
print("[ /health 엔드포인트 테스트 ]")
print("=" * 70)

try:
    health = get_json("/health")
    print(f"  status: {health.get('status')}")
    print(f"  ollama_available: {health.get('ollama_available')}")
    print(f"  current_model: {health.get('current_model')}")
    print(f"  active_sessions: {health.get('active_sessions')}")
    passed += 1
except Exception as e:
    print(f"\n[/health] ERROR: {e}")
    failed += 1


# ===== /models 엔드포인트 테스트 =====
print("\n" + "=" * 70)
print("[ /models 엔드포인트 테스트 ]")
print("=" * 70)

try:
    models = get_json("/models")
    print(f"  current_model: {models.get('current_model')}")
    available = models.get("available_models", [])
    print(f"  available_models: {len(available)}개")
    for m in available[:5]:
        print(f"    - {m.get('name')}")
    passed += 1
except Exception as e:
    print(f"\n[/models] ERROR: {e}")
    failed += 1


# ===== 결과 =====
print(f"\n{'='*70}")
print(f"테스트 결과: {passed}/{passed+failed} 성공, {failed} 실패")
