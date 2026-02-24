"""
ai_server.py 전체 INTENT 통합 테스트 스크립트
example3.json의 모든 INTENT에 대해 /ask API를 호출하여 결과를 수집한다.
"""
import json
import time
import httpx

BASE_URL = "http://localhost:8000"

# ============================================================
# 테스트 케이스 정의
# 각 항목: (질의 문장, 기대 INTENT, 카테고리 설명)
# ============================================================
TEST_CASES = [
    # ---- 배수지 관련 ----
    ("신평 배수지 일반현황은?", "RESERVOIR_OVERVIEW", "배수지 일반현황"),
    ("신평 배수지 설비현황은?", "RESERVOIR_EQUIPMENT_STATUS", "배수지 설비현황"),
    ("신평 배수지 초동대응 매뉴얼은?", "RESERVOIR_INITIAL_RESPONSE_MANUAL", "배수지 초동대응"),
    ("신평 배수지 운영현황은?", "RESERVOIR_OPERATION_STATUS", "배수지 운영현황"),
    ("신평 배수지 시설용량은?", "RESERVOIR_FACILITY_CAPACITY", "배수지 시설용량"),
    ("신평 배수지 수위 현황은?", "RESERVOIR_LEVEL_STATUS", "배수지 수위현황"),
    ("행정 배수지 평균사용량은?", "TODAY_RESERVOIR_AVG_USAGE", "배수지 평균사용량"),
    ("전체 배수지 평균사용량은?", "TODAY_RESERVOIR_AVG_USAGE_ALL", "전체 배수지 평균사용량"),
    ("금일 신평 배수지 적산은?", "TODAY_FLOW_ACCUMULATION", "금일 적산"),
    ("금일 전체 배수지 유출 현황은?", "TODAY_OUTFLOW_ALL_STATUS", "전체 배수지 유출"),
    ("신평 배수지 위치도는?", "RESERVOIR_LOCATION", "배수지 위치도"),
    ("신평 배수지 계통도는?", "RESERVOIR_NETWORK_DIAGRAM", "배수지 계통도"),
    ("신평 배수지 용수공급가능 시간은?", "RESERVOIR_SUPPLY_AVAILABLE_HOURS", "용수공급가능시간"),
    ("신평 배수지 수위계 헌팅여부를 알려줘", "RESERVOIR_LEVEL_HUNTING_CHECK", "수위계 헌팅"),

    # ---- 가압장 관련 ----
    ("행정 가압장 일반현황은?", "BOOSTER_STATION_OVERVIEW", "가압장 일반현황"),
    ("행정 가압장 운영현황은?", "BOOSTER_STATION_OPERATION_STATUS", "가압장 운영현황"),
    ("행정 가압장 설비현황은?", "BOOSTER_STATION_EQUIPMENT_STATUS", "가압장 설비현황"),
    ("행정 가압장 초동대응 매뉴얼은?", "BOOSTER_STATION_INITIAL_RESPONSE_MANUAL", "가압장 초동대응"),
    ("고대리 가압장 위치도는?", "BOOSTER_STATION_LOCATION", "가압장 위치도"),
    ("우강 가압장 계통도는?", "BOOSTER_STATION_NETWORK_DIAGRAM", "가압장 계통도"),

    # ---- 감압시설 관련 ----
    ("순성 감압시설 일반현황은?", "PRESSURE_REDUCING_FACILITY_OVERVIEW", "감압시설 일반현황"),
    ("순성 감압시설 운영현황은?", "PRESSURE_REDUCING_FACILITY_OPERATION_STATUS", "감압시설 운영현황"),
    ("순성 감압시설 설비현황은?", "PRESSURE_REDUCING_FACILITY_EQUIPMENT_STATUS", "감압시설 설비현황"),
    ("순성 감압시설 초동대응 매뉴얼은?", "PRESSURE_REDUCING_FACILITY_INITIAL_RESPONSE_MANUAL", "감압시설 초동대응"),
    ("순성 감압시설 감압기준은?", "PRESSURE_REDUCING_FACILITY_CRITERIA", "감압시설 감압기준"),
    ("순성 감압시설 위치도는?", "PRESSURE_REDUCING_FACILITY_LOCATION", "감압시설 위치도"),
    ("순성 감압설비 계통도는?", "PRESSURE_REDUCING_FACILITY_NETWORK_DIAGRAM", "감압설비 계통도"),

    # ---- 블록 관련 ----
    ("합덕3 소블록 일반현황은?", "BLOCK_OVERVIEW", "블록 일반현황"),
    ("합덕3 소블록 위치도는?", "BLOCK_LOCATION", "블록 위치도"),
    ("합덕3 소블록 계통도는?", "BLOCK_NETWORK_DIAGRAM", "블록 계통도"),
    ("행정1-1 소블록 운영현황은?", "BLOCK_OPERATION_STATUS", "블록 운영현황"),
    # ---- 공통 시설 ----
    ("신평 가압장 압력 현황은?", "FACILITY_PRESSURE_STATUS", "압력 현황"),
    ("신평 배수지 야간최소유량 현황은?", "NIGHT_MIN_FLOW_STATUS", "야간최소유량"),
    ("신평 배수지 통신 구성을 알려줘", "FACILITY_COMMUNICATION_TOPOLOGY", "통신 구성"),
    ("신평 배수지 통신 상태를 알려줘", "FACILITY_COMMUNICATION_STATUS", "통신 상태"),
    ("신평 배수지 주소를 알려줘", "FACILITY_ADDRESS_INFO_RESERVOIR", "주소(배수지)"),
    ("행정 가압장 주소를 알려줘", "FACILITY_ADDRESS_INFO_BOOSTER", "주소(가압장)"),
    ("행정1-1 소블록 주소를 알려줘", "FACILITY_ADDRESS_INFO_BLOCK", "주소(블록)"),
    ("순성 감압시설 주소를 알려줘", "FACILITY_ADDRESS_INFO_PRESSURE", "주소(감압시설)"),
    ("삼봉 가압장의 최근 펌프 알람은 뭐야?", "FACILITY_RECENT_ALARM", "최근 알람"),
    ("경보 발생 누적건수가 높은 순서대로 보여줘", "FACILITY_ALARM_TOP_COUNT", "알람 누적 TOP"),
    ("행정1-1 소블록 통신 경보 발생원인 순위를 알려줘", "FACILITY_ALARM_CAUSE_DIAGNOSIS_RANK", "알람 원인 진단"),
    ("신평 배수지 현재 수위를 알려줘", "FACILITY_TAG_LATEST_VALUE", "태그 최신값"),

    # ---- 테이블 타입 ----
    ("현재 진행중인 알람은?", "ONGOING_ALARM_STATUS", "진행중 알람"),
    ("현재 이상 설비 현황을 알려줘", "FACILITY_ABNORMAL_STATUS_SUMMARY", "이상 설비 현황"),
    ("남산1 소블록 야간최소유량 표준편차분석 결과를 알려줘", "FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS", "야간최소유량 표준편차"),

    # ---- 트렌드 ----
    ("신평 배수지 유량 트렌드 보여줘", "FACILITY_TREND", "트렌드(유량)"),
    ("신평 배수지 수위 트렌드 보여줘", "FACILITY_TREND", "트렌드(수위)"),
    ("행정 가압장 압력 트렌드 보여줘", "FACILITY_TREND", "트렌드(압력)"),
    ("행정1-1 소블록 야간최소유량 트렌드를 보여줘", "FACILITY_TREND", "트렌드(야간최소유량)"),
    ("신평 배수지 유입밸브 상태와 유입유량을 함께 트렌드로 보여줘", "FACILITY_MIXED_TREND", "혼합 트렌드"),

    # ---- 표 형태 조회 ----
    ("현재 남산1 소블록 유량을 표로 보여줘", "FACILITY_FLOW_CURRENT_TABLE", "유량 현재 표"),
    ("현재 우강 가압장 밸브 상태를 표로 보여줘", "FACILITY_VALVE_STATUS_CURRENT_TABLE", "밸브 상태 표"),
    ("7일간 남산1 소블록 유량을 표로 보여줘", "FACILITY_ANALOG_TIMESERIES_TABLE", "아날로그 시계열 표"),
    ("한달간 남산1 소블록 유량 순시를 보여줘", "FACILITY_FLOW_INSTANT_TIMESERIES_TABLE", "유량 순시 표"),
    ("7일간 남산1 소블록 유량 적산을 보여줘", "FACILITY_FLOW_ACCUMULATED_TIMESERIES_TABLE", "유량 적산 표"),
    ("행정 배수지 유입밸브 상태 데이터를 표로 보여줘", "FACILITY_DIGITAL_STATUS_TIMESERIES_TABLE", "디지털 상태 표"),
    ("합덕3 소블록 야간최소유량을 표로 보여줘", "NIGHT_MIN_FLOW_SUMMARY_TABLE", "야간최소유량 표"),
    ("신평 배수지 데이터 결측분석결과는?", "TAG_DAILY_MISSING_SUMMARY", "결측 분석"),

    # ============================================================
    # Edge Case 테스트 (키워드 변형, 경계 케이스, 다른 시설명)
    # ============================================================

    # ---- 키워드 동의어 / 변형 ----
    ("진행 중인 경보 알려줘", "ONGOING_ALARM_STATUS", "EC: 진행 중(띄어쓰기)+경보"),
    ("최근 배수지 경보 알려줘", "FACILITY_RECENT_ALARM", "EC: 경보→알람 동의어"),
    ("최근 가압장 알림 내역", "FACILITY_RECENT_ALARM", "EC: 알림→알람 동의어"),
    ("감압설비 유량 트렌드", "FACILITY_TREND", "EC: 감압설비→감압시설 alias"),
    ("감압밸브 주소 알려줘", "FACILITY_ADDRESS_INFO_PRESSURE", "EC: 감압밸브→감압 alias"),
    ("이상 발생 설비 알려줘", "FACILITY_ABNORMAL_STATUS_SUMMARY", "EC: 이상+설비 변형"),

    # ---- 다른 시설명으로 같은 INTENT ----
    ("순성 감압시설 유량 트렌드 보여줘", "FACILITY_TREND", "EC: 감압시설 트렌드"),
    ("합덕3 소블록 수위 트렌드 보여줘", "FACILITY_TREND", "EC: 블록 트렌드"),
    ("우강 가압장 주소를 알려줘", "FACILITY_ADDRESS_INFO_BOOSTER", "EC: 다른 가압장 주소"),
    ("남산1 소블록 주소 알려줘", "FACILITY_ADDRESS_INFO_BLOCK", "EC: 다른 블록 주소"),
    ("고대리 가압장 현재 압력 알려줘", "FACILITY_TAG_LATEST_VALUE", "EC: 다른 가압장 현재값"),
    ("행정 배수지 현재 유량 알려줘", "FACILITY_TAG_LATEST_VALUE", "EC: 다른 배수지 현재값"),

    # ---- 유사 INTENT 경계 테스트 ----
    ("신평 배수지 현재 수위", "FACILITY_TAG_LATEST_VALUE", "EC: 현재+수위(현황 없음)→TAG"),
    ("배수지 야간최소유량 트렌드 보여줘", "FACILITY_TREND", "EC: 트렌드 우선(야간최소유량보다)"),
    ("오늘 신평 배수지 적산 유량", "TODAY_FLOW_ACCUMULATION", "EC: 적산(표/보여 없음)→적산"),
    ("신평 배수지 적산 유량을 표로 보여줘", "FACILITY_FLOW_ACCUMULATED_TIMESERIES_TABLE", "EC: 적산+표→적산표"),
    ("혼합 트렌드 보여줘", "FACILITY_MIXED_TREND", "EC: 혼합 키워드→MIXED"),
    ("시설 이상 현황 보여줘", "FACILITY_ABNORMAL_STATUS_SUMMARY", "EC: 이상+현황"),

    # ---- 어순 변형 / 다른 표현 ----
    ("현재 행정 배수지 유량을 알려줘", "FACILITY_TAG_LATEST_VALUE", "EC: 현재 앞배치"),
    ("전체 배수지 평균사용량 조회", "TODAY_RESERVOIR_AVG_USAGE_ALL", "EC: 전체+평균사용량 변형"),
    ("배수지 데이터 결측 현황", "TAG_DAILY_MISSING_SUMMARY", "EC: 결측 다른 표현"),
    ("가압장 데이터 결측분석", "TAG_DAILY_MISSING_SUMMARY", "EC: 결측 다른 시설"),
    ("야간최소유량 표준편차 분석 결과", "FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS", "EC: 표준편차 다른 표현"),
    ("적산 유량을 보여줘", "FACILITY_FLOW_ACCUMULATED_TIMESERIES_TABLE", "EC: 적산+보여→적산표"),

    # ---- 축약/간결 질의 ----
    ("배수지 주소", "FACILITY_ADDRESS_INFO_RESERVOIR", "EC: 축약 주소 질의"),
    ("가압장 적산 현황", "TODAY_FLOW_ACCUMULATION", "EC: 적산(표/보여 없음)"),
    ("30일간 행정 배수지 유량 순시", "FACILITY_FLOW_INSTANT_TIMESERIES_TABLE", "EC: 순시+유량 변형"),
    ("블록 주소 알려줘", "FACILITY_ADDRESS_INFO_BLOCK", "EC: 블록 축약 주소"),
    ("감압시설 표준편차 분석", "FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS", "EC: 감압시설 표준편차"),
    ("신평배수지 적산유량은?", "TODAY_FLOW_ACCUMULATION", "EC: 띄어쓰기 없는 질의"),
]


def run_tests():
    results = []
    client = httpx.Client(timeout=30.0)

    for i, (query, expected_intent, desc) in enumerate(TEST_CASES, 1):
        print(f"[{i:02d}/{len(TEST_CASES)}] {desc}: {query}")
        start = time.time()
        try:
            resp = client.post(
                f"{BASE_URL}/ask",
                json={"user_question": query},
            )
            elapsed = time.time() - start
            data = resp.json()

            status = data.get("status", "UNKNOWN")
            matched_intent = data.get("intent", "N/A")
            message = data.get("message", "")
            answer = data.get("answer", {})
            summary = ""
            if isinstance(answer, dict):
                s = answer.get("summary", "")
                if isinstance(s, str):
                    summary = s
                elif isinstance(s, dict):
                    summary = s.get("text", str(s))

            detail_count = 0
            if isinstance(answer, dict) and "detail" in answer:
                detail_count = len(answer["detail"])

            has_data = data.get("data") is not None
            graph_type = data.get("graph_type", "")
            session_id = data.get("session_id", "")

            # 판정
            intent_match = (matched_intent == expected_intent)
            is_ok = status in ("OK", "NEED_CORRECTION")

            result = {
                "no": i,
                "query": query,
                "desc": desc,
                "expected_intent": expected_intent,
                "matched_intent": matched_intent,
                "intent_match": intent_match,
                "status": status,
                "is_ok": is_ok,
                "summary": summary[:100],
                "detail_count": detail_count,
                "has_data": has_data,
                "graph_type": graph_type,
                "message": message[:100] if message else "",
                "elapsed": round(elapsed, 2),
                "session_id": session_id,
                "raw_answer": answer,
            }
            results.append(result)

            mark = "OK" if (intent_match and is_ok) else "FAIL"
            print(f"  → [{mark}] status={status}, intent={matched_intent}, {elapsed:.2f}s")

        except Exception as e:
            elapsed = time.time() - start
            results.append({
                "no": i,
                "query": query,
                "desc": desc,
                "expected_intent": expected_intent,
                "matched_intent": "ERROR",
                "intent_match": False,
                "status": "ERROR",
                "is_ok": False,
                "summary": "",
                "detail_count": 0,
                "has_data": False,
                "graph_type": "",
                "message": str(e)[:200],
                "elapsed": round(elapsed, 2),
                "session_id": "",
                "raw_answer": {},
            })
            print(f"  → [ERROR] {e}")

    client.close()
    return results


def generate_report(results):
    """전체 테스트 리포트 생성"""
    total = len(results)
    passed = sum(1 for r in results if r["intent_match"] and r["is_ok"])
    failed = total - passed

    lines = []
    lines.append("# ai_server.py 전체 INTENT 통합 테스트 결과")
    lines.append("")
    lines.append(f"- 테스트 일시: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 총 테스트: {total}건")
    lines.append(f"- 성공: {passed}건")
    lines.append(f"- 실패: {failed}건")
    lines.append(f"- 성공률: {passed/total*100:.1f}%")
    lines.append("")

    # 요약 테이블
    lines.append("## 요약 테이블")
    lines.append("")
    lines.append("| No | 카테고리 | 질의 | 기대 INTENT | 매칭 INTENT | 상태 | 결과 | 소요시간 |")
    lines.append("|---|---|---|---|---|---|---|---|")

    for r in results:
        mark = "PASS" if (r["intent_match"] and r["is_ok"]) else "**FAIL**"
        q = r["query"][:30] + ("..." if len(r["query"]) > 30 else "")
        lines.append(
            f"| {r['no']} | {r['desc']} | {q} | {r['expected_intent']} | {r['matched_intent']} | {r['status']} | {mark} | {r['elapsed']}s |"
        )

    lines.append("")

    # 상세 결과
    lines.append("## 상세 결과")
    lines.append("")

    for r in results:
        mark = "PASS" if (r["intent_match"] and r["is_ok"]) else "FAIL"
        lines.append(f"### [{r['no']:02d}] {r['desc']} - {mark}")
        lines.append("")
        lines.append(f"- **질의**: {r['query']}")
        lines.append(f"- **기대 INTENT**: {r['expected_intent']}")
        lines.append(f"- **매칭 INTENT**: {r['matched_intent']}")
        lines.append(f"- **상태**: {r['status']}")
        lines.append(f"- **응답 summary**: {r['summary']}")
        lines.append(f"- **detail 항목 수**: {r['detail_count']}")
        lines.append(f"- **graph_type**: {r['graph_type']}")
        lines.append(f"- **data 포함 여부**: {r['has_data']}")
        if r["message"]:
            lines.append(f"- **메시지**: {r['message']}")
        lines.append(f"- **소요시간**: {r['elapsed']}s")
        lines.append("")

    return "\n".join(lines)


def generate_failure_report(results):
    """실패 항목만 정리한 리포트 생성"""
    failures = [r for r in results if not (r["intent_match"] and r["is_ok"])]

    lines = []
    lines.append("# ai_server.py 테스트 실패 항목 리포트")
    lines.append("")
    lines.append(f"- 테스트 일시: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 총 테스트: {len(results)}건")
    lines.append(f"- 실패: {len(failures)}건")
    lines.append("")

    if not failures:
        lines.append("**모든 테스트가 통과되었습니다.**")
        return "\n".join(lines)

    # 실패 유형 분류
    intent_mismatch = [r for r in failures if not r["intent_match"]]
    status_error = [r for r in failures if r["intent_match"] and not r["is_ok"]]

    if intent_mismatch:
        lines.append("## 1. INTENT 매칭 실패")
        lines.append("")
        lines.append("| No | 질의 | 기대 | 실제 | 상태 | 분석 |")
        lines.append("|---|---|---|---|---|---|")
        for r in intent_mismatch:
            analysis = ""
            if r["matched_intent"] == "N/A" and r["status"] == "NEED_CORRECTION":
                analysis = "파라미터 누락 → 정정 요청"
            elif r["matched_intent"] == "N/A" and r["status"] == "ERROR":
                analysis = "INTENT 매칭 자체 실패"
            elif r["matched_intent"] != r["expected_intent"]:
                analysis = f"다른 INTENT로 매칭됨"
            lines.append(
                f"| {r['no']} | {r['query'][:40]} | {r['expected_intent']} | {r['matched_intent']} | {r['status']} | {analysis} |"
            )
        lines.append("")

    if status_error:
        lines.append("## 2. INTENT 매칭 성공이나 처리 실패")
        lines.append("")
        lines.append("| No | 질의 | INTENT | 상태 | 메시지 |")
        lines.append("|---|---|---|---|---|")
        for r in status_error:
            lines.append(
                f"| {r['no']} | {r['query'][:40]} | {r['matched_intent']} | {r['status']} | {r['message'][:60]} |"
            )
        lines.append("")

    # 상세 분석
    lines.append("## 상세 분석")
    lines.append("")

    for r in failures:
        lines.append(f"### [{r['no']:02d}] {r['desc']}")
        lines.append("")
        lines.append(f"- **질의**: {r['query']}")
        lines.append(f"- **기대 INTENT**: {r['expected_intent']}")
        lines.append(f"- **매칭 INTENT**: {r['matched_intent']}")
        lines.append(f"- **상태**: {r['status']}")
        lines.append(f"- **응답 summary**: {r['summary']}")
        if r["message"]:
            lines.append(f"- **에러 메시지**: {r['message']}")

        # 원인 분석
        lines.append(f"- **원인 분석**: ", )
        if not r["intent_match"] and r["status"] == "NEED_CORRECTION":
            lines.append("  - INTENT는 매칭되었으나 필수 파라미터가 누락되어 정정 요청이 발생함")
            lines.append(f"  - 정정 메시지: {r['message']}")
        elif not r["intent_match"] and r["matched_intent"] == "N/A":
            lines.append("  - INTENT 매칭 자체가 실패하여 ERROR 응답 반환")
        elif not r["intent_match"]:
            lines.append(f"  - 기대 INTENT({r['expected_intent']})와 다른 INTENT({r['matched_intent']})로 매칭됨")
            lines.append("  - 키워드 매칭 점수에서 다른 INTENT가 더 높은 점수를 받았을 수 있음")
        elif r["status"] == "ERROR":
            lines.append("  - SQL 실행 오류 또는 DB 연결 실패")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    print("=" * 60)
    print("ai_server.py 전체 INTENT 통합 테스트 시작")
    print("=" * 60)
    print()

    results = run_tests()

    print()
    print("=" * 60)
    print("테스트 완료. 리포트 생성 중...")
    print("=" * 60)

    # 전체 리포트 저장
    report = generate_report(results)
    with open("test_report.md", "w", encoding="utf-8") as f:
        f.write(report)
    print("전체 리포트: test_report.md")

    # 실패 리포트 저장
    failure_report = generate_failure_report(results)
    with open("test_failures.md", "w", encoding="utf-8") as f:
        f.write(failure_report)
    print("실패 리포트: test_failures.md")

    # JSON raw 데이터도 저장
    with open("test_results.json", "w", encoding="utf-8") as f:
        # raw_answer 제거 (너무 큼)
        clean = []
        for r in results:
            c = dict(r)
            del c["raw_answer"]
            clean.append(c)
        json.dump(clean, f, ensure_ascii=False, indent=2)
    print("JSON 결과: test_results.json")

    # 요약 출력
    total = len(results)
    passed = sum(1 for r in results if r["intent_match"] and r["is_ok"])
    print(f"\n총 {total}건 중 {passed}건 성공, {total-passed}건 실패 ({passed/total*100:.1f}%)")
