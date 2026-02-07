#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""테스트 스크립트"""
import json
import requests

TEST_CASES = [
    ("신평 배수지 초동대응 매뉴얼은?", "RESERVOIR_INITIAL_RESPONSE_MANUAL"),
    ("금일 신평 배수지 적산은?", "TODAY_FLOW_ACCUMULATION"),
    ("행정 가압장 주소는?", "FACILITY_ADDRESS_INFO_BOOSTER"),
    ("신평 배수지 주소는?", "FACILITY_ADDRESS_INFO_RESERVOIR"),
    ("합덕3 소블록 일반현황은?", "BLOCK_OVERVIEW"),
    ("신평 배수지 일반현황은?", "RESERVOIR_OVERVIEW"),
    ("행정 가압장 운영현황은?", "BOOSTER_STATION_OPERATION_STATUS"),
    ("신평 배수지 현재 수위는?", "RESERVOIR_LEVEL_STATUS"),
    ("신평 가압장 압력 현황은?", "FACILITY_PRESSURE_STATUS"),
    ("금일 신평 배수지 유출 현황은?", "TODAY_OUTFLOW_ALL_STATUS"),
]

url = "http://localhost:8000/ask"
passed = 0
failed = 0

for question, expected_intent in TEST_CASES:
    try:
        response = requests.post(url, json={"user_question": question})
        data = response.json()
        actual_intent = data.get("intent", "NONE")
        status = data.get("status", "ERROR")

        if actual_intent == expected_intent:
            print(f"[PASS] '{question}' -> {actual_intent}")
            passed += 1
        else:
            print(f"[FAIL] '{question}'")
            print(f"  Expected: {expected_intent}, Got: {actual_intent}")
            print(f"  Status: {status}, Message: {data.get('message', '')}")
            failed += 1
    except Exception as e:
        print(f"[ERROR] '{question}' - {e}")
        failed += 1

print(f"\nResult: {passed}/{passed+failed} passed ({100*passed/(passed+failed):.1f}%)")
