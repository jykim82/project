#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Debug matching algorithm"""
import json
import os

# Load from ai_server
from ai_server import (
    INTENT_DEFINITIONS,
    KNOWN_SITENAMES,
    normalize_for_matching,
    remove_sitename_for_matching,
    extract_facility_type_from_question,
    is_intent_for_facility,
)

user_question = "행정 가압장 주소는?"

print(f"User question: {user_question}")
print(f"Normalized user: {normalize_for_matching(user_question)}")
print(f"User without sitename: {remove_sitename_for_matching(user_question)}")
print(f"Normalized without site: {normalize_for_matching(remove_sitename_for_matching(user_question))}")
print(f"Facility type: {extract_facility_type_from_question(user_question)}")
print()

# Check facility type filtering
print(f"is_intent_for_facility('FACILITY_ADDRESS_INFO_BOOSTER', '가압장'): {is_intent_for_facility('FACILITY_ADDRESS_INFO_BOOSTER', '가압장')}")
print()

# Find FACILITY_ADDRESS_INFO_BOOSTER
for intent_def in INTENT_DEFINITIONS:
    if intent_def.get("intent") == "FACILITY_ADDRESS_INFO_BOOSTER":
        print(f"Found intent: FACILITY_ADDRESS_INFO_BOOSTER")
        print(f"Questions: {intent_def.get('questions')}")
        print()

        for q in intent_def.get("questions", []):
            print(f"  Pattern: {q}")
            print(f"  Normalized pattern: {normalize_for_matching(q)}")
            print(f"  Pattern without site: {remove_sitename_for_matching(q)}")
            print(f"  Normalized pattern without site: {normalize_for_matching(remove_sitename_for_matching(q))}")

            user_norm = normalize_for_matching(user_question)
            pattern_norm = normalize_for_matching(q)
            user_without_site = normalize_for_matching(remove_sitename_for_matching(user_question))
            pattern_without_site = normalize_for_matching(remove_sitename_for_matching(q))

            check1 = pattern_norm in user_norm
            check2 = pattern_norm in user_without_site
            check3 = pattern_without_site in user_without_site or user_without_site in pattern_without_site

            print(f"  Check1 (pattern in user): {check1}")
            print(f"  Check2 (pattern in user_without_site): {check2}")
            print(f"  Check3 (both without site): {check3}")
            print()
        break
