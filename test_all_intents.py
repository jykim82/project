# -*- coding: utf-8 -*-
"""전체 인텐트 테스트 - 응답 내 데이터 유무 확인"""
import requests, json, sys, time

API = "http://localhost:8000"

# 68개 인텐트 x 대표 질문 (한글 현장명 사용 - DEMO_MODE 역변환 테스트 포함)
TESTS = [
    ("RESERVOIR_OVERVIEW", "남산 배수지 일반현황은?"),
    ("RESERVOIR_EQUIPMENT_STATUS", "남산 배수지 설비현황은?"),
    ("RESERVOIR_INITIAL_RESPONSE_MANUAL", "남산 배수지 초동대응 매뉴얼은?"),
    ("RESERVOIR_OPERATION_STATUS", "남산 배수지 운영현황은?"),
    ("RESERVOIR_FACILITY_CAPACITY", "남산 배수지 시설용량은?"),
    ("RESERVOIR_LEVEL_STATUS", "남산 배수지 수위 현황을 알려줘"),
    ("TODAY_RESERVOIR_AVG_USAGE", "남산 배수지 평균사용량은?"),
    ("TODAY_RESERVOIR_AVG_USAGE_ALL", "전체 배수지 평균사용량은?"),
    ("TODAY_FLOW_ACCUMULATION", "금일 남산 배수지 적산은?"),
    ("TODAY_OUTFLOW_ALL_STATUS", "금일 배수지 유출 현황은?"),
    ("BOOSTER_STATION_OVERVIEW", "갈산 가압장 일반현황은?"),
    ("BOOSTER_STATION_OPERATION_STATUS", "갈산 가압장 운영현황은?"),
    ("BOOSTER_STATION_EQUIPMENT_STATUS", "갈산 가압장 설비현황은?"),
    ("BOOSTER_STATION_INITIAL_RESPONSE_MANUAL", "갈산 가압장 초동대응 매뉴얼은?"),
    ("PRESSURE_REDUCING_FACILITY_OVERVIEW", "면천 감압시설 일반현황은?"),
    ("PRESSURE_REDUCING_FACILITY_OPERATION_STATUS", "면천 감압시설 운영현황은?"),
    ("PRESSURE_REDUCING_FACILITY_EQUIPMENT_STATUS", "면천 감압시설 설비현황은?"),
    ("PRESSURE_REDUCING_FACILITY_INITIAL_RESPONSE_MANUAL", "면천 감압시설 초동대응 매뉴얼은?"),
    ("PRESSURE_REDUCING_FACILITY_CRITERIA", "면천 감압시설 감압기준은?"),
    ("BLOCK_OVERVIEW", "남산1 소블록 일반현황은?"),
    ("BLOCK_LOCATION", "남산1 소블록 위치도는?"),
    ("RESERVOIR_LOCATION", "남산 배수지 위치도는?"),
    ("BOOSTER_STATION_LOCATION", "갈산 가압장 위치도는?"),
    ("PRESSURE_REDUCING_FACILITY_LOCATION", "면천 감압설비 위치도는?"),
    ("BLOCK_NETWORK_DIAGRAM", "남산1 소블록 계통도는?"),
    ("RESERVOIR_NETWORK_DIAGRAM", "남산 배수지 계통도는?"),
    ("BOOSTER_STATION_NETWORK_DIAGRAM", "갈산 가압장 계통도는?"),
    ("PRESSURE_REDUCING_FACILITY_NETWORK_DIAGRAM", "면천 감압설비 계통도는?"),
    ("BLOCK_OPERATION_STATUS", "남산1 소블록 운영현황은?"),
    ("RESERVOIR_LEVEL_HUNTING_CHECK", "남산 배수지 수위계 헌팅여부를 알려줘"),
    ("FACILITY_PRESSURE_STATUS", "갈산 가압장 압력 현황은?"),
    ("NIGHT_MIN_FLOW_STATUS", "남산1 소블록 야간최소유량 현황은?"),
    ("FACILITY_COMMUNICATION_TOPOLOGY", "남산 배수지 통신 구성을 알려줘"),
    ("FACILITY_COMMUNICATION_STATUS", "남산 배수지 통신 상태를 알려줘"),
    ("FACILITY_ADDRESS_INFO_RESERVOIR", "남산 배수지 주소를 알려줘"),
    ("FACILITY_ADDRESS_INFO_BOOSTER", "갈산 가압장 주소를 알려줘"),
    ("FACILITY_ADDRESS_INFO_BLOCK", "남산1 소블록 주소를 알려줘"),
    ("FACILITY_ADDRESS_INFO_PRESSURE", "면천 감압시설 주소를 알려줘"),
    ("FACILITY_RECENT_ALARM", "남산 배수지 최근 알람은 뭐야?"),
    ("FACILITY_ALARM_TOP_COUNT", "경보 발생 누적건수가 높은 순서대로 보여줘"),
    ("FACILITY_ALARM_CAUSE_DIAGNOSIS_RANK", "통신 경보 발생원인 진단 순위를 알려줘"),
    ("FACILITY_TAG_LATEST_VALUE", "남산 배수지 현재 수위를 알려줘"),
    ("RESERVOIR_SUPPLY_AVAILABLE_HOURS", "남산 배수지 용수공급가능 시간은?"),
    ("FACILITY_FLOW_CURRENT_TABLE", "현재 남산1 소블록 유량을 표로 보여줘"),
    ("FACILITY_VALVE_STATUS_CURRENT_TABLE", "현재 우강 가압장 밸브 상태를 표로 보여줘"),
    ("FACILITY_ANALOG_TIMESERIES_TABLE", "7일간 남산1 소블록 유량을 표로 보여줘"),
    ("FACILITY_FLOW_INSTANT_TIMESERIES_TABLE", "한달간 남산2 소블록 유량 순시 데이터를 보여줘"),
    ("FACILITY_FLOW_ACCUMULATED_TIMESERIES_TABLE", "한달간 남산2 소블록 유량 적산 데이터를 보여줘"),
    ("FACILITY_DIGITAL_STATUS_TIMESERIES_TABLE", "행정 배수지 유입밸브 상태 데이터를 표로 보여줘"),
    ("FACILITY_TAG_DATA_TABLE", "최근 10분간 신평 배수지의 수위는?"),
    ("NIGHT_MIN_FLOW_SUMMARY_TABLE", "소블록 야간최소유량 데이터를 표로 보여줘"),
    ("TAG_DAILY_MISSING_SUMMARY", "한달간 송악1 배수지 유량데이터 중 결측구간 분석결과는?"),
    ("ONGOING_ALARM_STATUS", "현재 진행중인 알람은?"),
    ("ALARM_ABNORMAL_LOCATIONS", "통신 이상지점을 알려줘"),
    ("FACILITY_ABNORMAL_STATUS_SUMMARY", "현재 이상 설비 현황을 알려줘"),
    ("FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS", "석문 배수지 야간최소유량 표준편차분석을 통해 이상여부를 확인해줘"),
    ("FACILITY_TREND", "남산 배수지 수위 그래프 보여줘"),
    ("FACILITY_MIXED_TREND", "행정 배수지 유입밸브 상태와 유입유량을 함께 트렌드로 보여줘"),
    ("ANOMALY_SCAN_ALL", "전체 이상 스캔해줘"),
    ("ANOMALY_FACILITY_DETAIL", "신평 배수지 이상 진단해줘"),
    ("ANOMALY_HISTORY", "이상 이력 보여줘"),
    ("ANOMALY_PREDICT", "위험 예측해줘"),
    ("ANOMALY_COMPARE", "배수지 이상 비교해줘"),
    ("ANOMALY_PATTERN", "시간대별 이상 패턴 보여줘"),
    ("ANOMALY_CROSS_FACILITY", "시설간 유량 불일치 확인해줘"),
    ("ANOMALY_FLOW_BALANCE", "물 수지 검증해줘"),
    ("LEAK_CUSUM_ANALYSIS", "남산1 소블록 야간최소유량 누수추정 분석해줘"),
    ("FACILITY_CATALOG_TREND_TABLE", "한달간 전체 배수지 수위를 표로 보여줘"),
]


def check_has_data(resp_json):
    """응답에 실제 데이터가 있는지 판별. (has_data, reason)"""
    status = resp_json.get("status", "")
    if status == "ERROR":
        return False, "status=ERROR"
    if status == "NEED_CORRECTION":
        return False, "NEED_CORRECTION"

    answer = resp_json.get("answer", "")
    answer_text = ""
    if isinstance(answer, str):
        answer_text = answer
    elif isinstance(answer, dict):
        # answer가 dict인 경우 (recommend_questions 등)
        answer_text = json.dumps(answer, ensure_ascii=False)

    # 명시적 no-data 문구
    no_data_phrases = [
        "조회된 데이터가 없습니다",
        "데이터가 없습니다",
        "데이터를 찾을 수 없",
        "등록된 정보가 없",
        "해당 시설이 없",
        "해당하는 시설을 찾을 수 없",
        "조회 결과가 없",
        "결과가 없습니다",
        "태그가 없습니다",
        "데이터가 존재하지 않",
    ]
    for phrase in no_data_phrases:
        if phrase in answer_text:
            return False, f'no-data: "{phrase}"'

    # visual_data 확인
    vd = resp_json.get("visual_data")
    if vd and isinstance(vd, dict):
        rows = vd.get("rows")
        if rows is not None and len(rows) == 0:
            return False, "visual_data.rows empty"
        data_list = vd.get("data")
        if data_list is not None and len(data_list) == 0:
            return False, "visual_data.data empty"

    return True, "OK"


def get_answer_preview(resp_json, max_len=120):
    answer = resp_json.get("answer", "")
    if isinstance(answer, str):
        return answer[:max_len]
    elif isinstance(answer, dict):
        return json.dumps(answer, ensure_ascii=False)[:max_len]
    return str(answer)[:max_len]


def run_test(intent, question, idx, total):
    t0 = time.time()
    try:
        r = requests.post(
            f"{API}/ask",
            json={"user_question": question, "session_id": "test-intents"},
            timeout=120,
        )
        elapsed = time.time() - t0
        if r.status_code != 200:
            return {
                "intent": intent,
                "question": question,
                "has_data": False,
                "reason": f"HTTP {r.status_code}",
                "elapsed": elapsed,
                "classified_as": "",
                "answer_preview": r.text[:120],
            }
        data = r.json()
        has_data, reason = check_has_data(data)
        classified = data.get("intent", "")
        return {
            "intent": intent,
            "question": question,
            "has_data": has_data,
            "reason": reason,
            "elapsed": elapsed,
            "classified_as": classified,
            "answer_preview": get_answer_preview(data),
        }
    except Exception as e:
        return {
            "intent": intent,
            "question": question,
            "has_data": False,
            "reason": str(e)[:100],
            "elapsed": time.time() - t0,
            "classified_as": "",
            "answer_preview": "",
        }


def main():
    total = len(TESTS)
    results = []
    no_data = []
    misclass = []
    ok_count = 0

    print(f"\n{'='*80}")
    print(f"  Intent Test - {total} intents")
    print(f"{'='*80}\n")

    for i, (intent, question) in enumerate(TESTS):
        result = run_test(intent, question, i, total)
        results.append(result)

        status = "OK" if result["has_data"] else "FAIL"
        cls_mark = ""
        if result["classified_as"] and result["classified_as"] != intent:
            cls_mark = f" [MISCLASS -> {result['classified_as']}]"
            misclass.append(result)

        if result["has_data"]:
            ok_count += 1

        print(f"[{i+1:2d}/{total}] {status:4s} {result['elapsed']:5.1f}s  {intent}{cls_mark}")
        if not result["has_data"]:
            print(f"         reason: {result['reason']}")
            if result.get("answer_preview"):
                print(f"         answer: {result['answer_preview'][:80]}")
            no_data.append(result)

    # Summary
    print(f"\n{'='*80}")
    print(f"  SUMMARY")
    print(f"{'='*80}")
    print(f"  Total: {total}  |  OK: {ok_count}  |  FAIL: {len(no_data)}  |  MISCLASS: {len(misclass)}")

    if no_data:
        print(f"\n  --- FAIL LIST ---")
        for r in no_data:
            print(f"  {r['intent']:50s} | {r['reason']}")
            print(f"    Q: {r['question']}")

    if misclass:
        print(f"\n  --- MISCLASS LIST ---")
        for r in misclass:
            print(f"  {r['intent']:50s} -> {r['classified_as']}")
            print(f"    Q: {r['question']}")

    # Save results
    with open("test_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n  Results saved: test_results.json\n")


if __name__ == "__main__":
    main()
