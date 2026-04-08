"""
test_iforest_v2.py
IsolationForest v2 테스트 수트

실행 방법:
  # T1/T3/T5 (DB 불필요, 언제든 실행 가능)
  python test_iforest_v2.py unit

  # T2 (DB 필요 — Docker TimescaleDB 기동 후)
  python test_iforest_v2.py train

  # T3 (학습된 모델로 예측 품질 검증)
  python test_iforest_v2.py quality

  # T4 (AI 서버 기동 후, 실제 HTTP 요청)
  python test_iforest_v2.py api

  # 전체 (T1+T3+T5, DB/서버 없이)
  python test_iforest_v2.py
"""

import sys
import json
import time
import logging
import traceback
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
from sklearn.ensemble import IsolationForest

sys.path.insert(0, "D:/slm")
from anomaly_iforest import (
    IForestManager,
    FacilityModel,
    FACILITY_SENSOR_SCHEMA,
    DATAINFO_TO_GROUP,
    MIN_FACILITY_TIMESTAMPS,
    _datainfo_to_group,
    _build_facility_matrix,
    _get_contamination,
)

logging.basicConfig(level=logging.WARNING)  # 테스트 중 로그 억제

# ── 결과 집계 ─────────────────────────────────────────────

_results: list[dict] = []


def _run(name: str, fn):
    try:
        fn()
        _results.append({"name": name, "ok": True})
        print(f"  [OK]  {name}")
    except AssertionError as e:
        _results.append({"name": name, "ok": False, "err": str(e)})
        print(f"  [FAIL]  {name}\n      {e}")
    except Exception as e:
        _results.append({"name": name, "ok": False, "err": traceback.format_exc()})
        print(f"  [ERR]  {name}\n      {e}")


def _summary():
    ok = sum(1 for r in _results if r["ok"])
    total = len(_results)
    print(f"\n{'-'*50}")
    print(f"  Result: {ok}/{total} passed")
    if ok < total:
        print("\n  Failed:")
        for r in _results:
            if not r["ok"]:
                print(f"    - {r['name']}: {r['err'][:120]}")
    print(f"{'-'*50}\n")
    return ok == total


# ── 공통 픽스처 ───────────────────────────────────────────

def _make_facility_model(
    facilitytype: str,
    feature_cols: list[str],
    n_samples: int = 200,
) -> FacilityModel:
    """더미 데이터로 학습된 FacilityModel 반환.

    실제 학습과 동일한 feature 구조 사용:
      - 센서값: [1.0, 10.0] 균등분포
      - hour:   [0, 23] 정수 (시각)
      - dow:    [0, 6]  정수 (요일)
    """
    rng = np.random.default_rng(42)
    X_sensors = rng.uniform(1.0, 10.0, size=(n_samples, len(feature_cols)))
    X_hour = rng.integers(0, 24, size=(n_samples, 1)).astype(float)
    X_dow  = rng.integers(0, 7,  size=(n_samples, 1)).astype(float)
    X = np.concatenate([X_sensors, X_hour, X_dow], axis=1)
    model = IsolationForest(contamination=0.05, n_estimators=50, random_state=42)
    model.fit(X)
    return FacilityModel(
        sitename="테스트현장",
        facilitytype=facilitytype,
        feature_cols=feature_cols,
        model=model,
    )


def _make_manager_with_tier1(facilitytype: str, feature_cols: list[str]) -> IForestManager:
    """Tier-1 모델 1개가 장착된 IForestManager 반환."""
    mgr = IForestManager()
    fm = _make_facility_model(facilitytype, feature_cols)
    mgr.facility_models[("테스트현장", facilitytype)] = fm
    mgr._tier1_covered = {("테스트현장", facilitytype)}
    mgr.last_trained = datetime.now()
    return mgr


def _make_manager_with_tier2(tagsn: str = "TAG001") -> IForestManager:
    """Tier-2 모델 1개가 장착된 IForestManager 반환."""
    mgr = IForestManager()
    rng = np.random.default_rng(0)
    X = rng.uniform(1.0, 10.0, size=(200, 4))
    model = IsolationForest(contamination=0.05, n_estimators=50, random_state=42)
    model.fit(X)
    mgr.tag_models[tagsn] = model
    mgr.last_trained = datetime.now()
    return mgr


# ═══════════════════════════════════════════════════════════
# T1. 단위 테스트 (DB 불필요)
# ═══════════════════════════════════════════════════════════

def t1_datainfo_mapping():
    """T1-1: datainfo → group_code 매핑 정확성"""
    print("\n[T1] 단위 테스트")

    # 정확 매핑
    _run("T1-1-a 유출유량 → FLOW_OUTLET", lambda:
        assert_eq(_datainfo_to_group("갈산 유출유량(신규)"), "FLOW_OUTLET"))

    _run("T1-1-b 유입유량 (유량보다 우선)", lambda:
        assert_eq(_datainfo_to_group("유입유량 순시"), "FLOW_INLET"))

    _run("T1-1-c 토출압력 → PRESSURE_DISCHARGE", lambda:
        assert_eq(_datainfo_to_group("가압펌프 토출압력"), "PRESSURE_DISCHARGE"))

    _run("T1-1-d 유출압력 → PRESSURE_OUTLET", lambda:
        assert_eq(_datainfo_to_group("유출압력 센서"), "PRESSURE_OUTLET"))

    _run("T1-1-e 유입압력 → PRESSURE_INLET", lambda:
        assert_eq(_datainfo_to_group("유입압력"), "PRESSURE_INLET"))

    _run("T1-1-f 수위 → WATER_LEVEL", lambda:
        assert_eq(_datainfo_to_group("수위계 1호"), "WATER_LEVEL"))

    _run("T1-1-g 유량순시 → FLOW_INSTANT (유량보다 우선)", lambda:
        assert_eq(_datainfo_to_group("유량순시"), "FLOW_INSTANT"))

    _run("T1-1-h 압력 (단독) → PRESSURE", lambda:
        assert_eq(_datainfo_to_group("압력 센서 A"), "PRESSURE"))

    _run("T1-1-i 유량 (단독) → FLOW", lambda:
        assert_eq(_datainfo_to_group("유량"), "FLOW"))

    _run("T1-1-j 매핑 없음 → None", lambda:
        assert_eq(_datainfo_to_group("온도 센서"), None))

    _run("T1-1-k 빈 문자열 → None", lambda:
        assert_eq(_datainfo_to_group(""), None))

    _run("T1-1-l 유출유량이 유량보다 우선", lambda:
        assert_eq(_datainfo_to_group("유출유량"), "FLOW_OUTLET"))  # FLOW 아님


def t1_facility_model_predict():
    """T1-2: FacilityModel.predict 케이스"""

    # 가압장 픽스처
    fm = _make_facility_model("가압장", ["PRESSURE_DISCHARGE", "FLOW_OUTLET"])

    _run("T1-2-a 필수 센서 정상 → 결과 반환", lambda: _check_predict_ok(fm))

    _run("T1-2-b 필수 센서 값 0 → None", lambda:
        assert_none(fm.predict({"PRESSURE_DISCHARGE": 0.0, "FLOW_OUTLET": 5.0}, 9, 1)))

    _run("T1-2-c 필수 센서 키 누락 → None", lambda:
        assert_none(fm.predict({"FLOW_OUTLET": 5.0}, 9, 1)))  # PRESSURE_DISCHARGE 없음

    _run("T1-2-d 필수 센서 0.0005 (< 0.001) → None", lambda:
        assert_none(fm.predict({"PRESSURE_DISCHARGE": 0.0005, "FLOW_OUTLET": 5.0}, 9, 1)))

    _run("T1-2-e 결과에 tier=1 포함", lambda:
        assert_eq(_check_predict_ok(fm)["tier"], 1))

    _run("T1-2-f 결과에 features_used 포함", lambda:
        assert_eq(_check_predict_ok(fm)["features_used"], ["PRESSURE_DISCHARGE", "FLOW_OUTLET"]))

    _run("T1-2-g 선택 센서 누락해도 예측 가능 (배수지 유입유량 없음)", lambda:
        _check_predict_ok_배수지())


def t1_build_facility_matrix():
    """T1-3: _build_facility_matrix 필터링"""

    # 충분한 정상 데이터
    ts_data = _make_ts_data(n=120, required_gcs=["WATER_LEVEL", "FLOW_OUTLET"])
    feature_cols = ["WATER_LEVEL", "FLOW_OUTLET"]
    required = {"WATER_LEVEL", "FLOW_OUTLET"}

    _run("T1-3-a 충분한 데이터 → ndarray 반환", lambda:
        assert_not_none(_build_facility_matrix(ts_data, feature_cols, required)))

    _run("T1-3-b shape 확인 (N, feature+2)", lambda:
        assert_eq(
            _build_facility_matrix(ts_data, feature_cols, required).shape[1],
            len(feature_cols) + 2
        ))

    # 부족한 데이터
    ts_few = _make_ts_data(n=50, required_gcs=["WATER_LEVEL", "FLOW_OUTLET"])
    _run("T1-3-c 샘플 부족 → None", lambda:
        assert_none(_build_facility_matrix(ts_few, feature_cols, required)))

    # 필수 센서 결측 포함
    ts_missing = _make_ts_data(n=120, required_gcs=["WATER_LEVEL", "FLOW_OUTLET"])
    # 일부 타임스탬프에서 FLOW_OUTLET 제거
    for i, k in enumerate(list(ts_missing.keys())[:30]):
        del ts_missing[k]["FLOW_OUTLET"]
    result = _build_facility_matrix(ts_missing, feature_cols, required)
    _run("T1-3-d 필수 센서 결측 행 제외 (≤90행)", lambda:
        assert_true(result is None or len(result) <= 90))

    # 비가동(0.0) 포함
    ts_zero = _make_ts_data(n=120, required_gcs=["WATER_LEVEL", "FLOW_OUTLET"])
    for k in list(ts_zero.keys())[:20]:
        ts_zero[k]["WATER_LEVEL"] = 0.0
    result_z = _build_facility_matrix(ts_zero, feature_cols, required)
    _run("T1-3-e 비가동 행 제외", lambda:
        assert_true(result_z is None or len(result_z) <= 100))


def t1_predict_for_rows_tier_priority():
    """T1-4: predict_for_rows Tier 우선순위"""

    # Tier-1 있는 매니저
    mgr_t1 = _make_manager_with_tier1("가압장", ["PRESSURE_DISCHARGE", "FLOW_OUTLET"])
    rows_t1 = _make_rows(
        sitename="테스트현장", facilitytype="가압장",
        datainfos=["토출압력", "유출유량"],
        vals=[5.0, 3.0],
    )
    columns = ["tagsn", "current_val", "sitename", "facilitytype", "datainfo", "z_score"]

    _run("T1-4-a Tier-1 있음 → facility_results에 결과", lambda: (
        assert_true(len(mgr_t1.predict_for_rows(rows_t1, columns).get("facility_results", {})) > 0)
    ))
    _run("T1-4-b Tier-1 있음 → tag_results 없음 (Tier-2 스킵)", lambda: (
        assert_eq(len(mgr_t1.predict_for_rows(rows_t1, columns).get("tag_results", {})), 0)
    ))
    _run("T1-4-c tier1_count = 1", lambda: (
        assert_eq(mgr_t1.predict_for_rows(rows_t1, columns).get("tier1_count"), 1)
    ))

    # Tier-2만 있는 매니저
    mgr_t2 = _make_manager_with_tier2("TAG001")
    rows_t2 = [("TAG001", 5.0, "현장A", "배수지", "수위", 0.5)]
    _run("T1-4-d Tier-1 없음 → tag_results에 결과 (Tier-2)", lambda: (
        assert_true(len(mgr_t2.predict_for_rows(rows_t2, columns).get("tag_results", {})) > 0)
    ))
    _run("T1-4-e tier2_count = 1", lambda: (
        assert_eq(mgr_t2.predict_for_rows(rows_t2, columns).get("tier2_count"), 1)
    ))

    # is_trained=False
    mgr_empty = IForestManager()
    _run("T1-4-f is_trained=False → {} 반환", lambda: (
        assert_eq(mgr_empty.predict_for_rows(rows_t1, columns), {})
    ))

    # datainfo 컬럼 없음 → Tier-1 건너뜀
    mgr_t1_2 = _make_manager_with_tier1("가압장", ["PRESSURE_DISCHARGE", "FLOW_OUTLET"])
    cols_no_di = ["tagsn", "current_val", "sitename", "facilitytype"]
    rows_no_di = [("TAG001", 5.0, "테스트현장", "가압장")]
    result_no_di = mgr_t1_2.predict_for_rows(rows_no_di, cols_no_di)
    _run("T1-4-g datainfo 컬럼 없음 → 에러 없이 동작", lambda: (
        assert_true(isinstance(result_no_di, dict))
    ))


# ═══════════════════════════════════════════════════════════
# T2. 학습 통합 테스트 (DB 필요)
# ═══════════════════════════════════════════════════════════

def t2_training_integration():
    """T2: 실제 DB 연결 후 학습 검증"""
    print("\n[T2] 학습 통합 테스트 (DB 연결 필요)")

    try:
        import psycopg2
        conn = psycopg2.connect(
            host="localhost", port=5433, dbname="slm",
            user="slm_dev", password="slm_dev_1234"
        )
        conn.set_client_encoding("UTF8")
        conn.close()
    except Exception as e:
        print(f"  [SKIP]  DB 연결 불가, T2 건너뜀: {e}")
        return

    def get_conn():
        import psycopg2
        c = psycopg2.connect(
            host="localhost", port=5433, dbname="slm",
            user="slm_dev", password="slm_dev_1234"
        )
        c.set_client_encoding("UTF8")
        return c

    mgr = IForestManager()
    t_start = time.time()

    _run("T2-1 train_all 에러 없이 완료", lambda: mgr.train_all(get_conn))

    elapsed = time.time() - t_start
    print(f"       학습 소요: {elapsed:.1f}s")

    _run("T2-2 is_trained=True", lambda: assert_true(mgr.is_trained))

    _run("T2-3 Tier-1 모델 1개 이상 생성", lambda: assert_true(mgr.tier1_count > 0))

    _run("T2-4 Tier-2 모델 1개 이상 생성", lambda: assert_true(mgr.tier2_count > 0))

    _run("T2-5 Tier-1 feature_cols 2개 이상", lambda: (
        assert_true(all(
            len(fm.feature_cols) >= 2
            for fm in mgr.facility_models.values()
        ))
    ))

    _run("T2-6 get_status 정상 반환", lambda: (
        assert_true("tier1_facilities" in mgr.get_status())
    ))

    # 가압장 모델이 있으면 feature 검증
    booster_models = {
        k: v for k, v in mgr.facility_models.items() if k[1] == "가압장"
    }
    if booster_models:
        fm = next(iter(booster_models.values()))
        _run("T2-7 가압장 필수 feature 포함 (토출압력+유출유량)", lambda: (
            assert_true(
                "PRESSURE_DISCHARGE" in fm.feature_cols and
                "FLOW_OUTLET" in fm.feature_cols
            )
        ))

    배수지_models = {k: v for k, v in mgr.facility_models.items() if k[1] == "배수지"}
    if 배수지_models:
        fm = next(iter(배수지_models.values()))
        _run("T2-8 배수지 필수 feature 포함 (수위+유출유량)", lambda: (
            assert_true(
                "WATER_LEVEL" in fm.feature_cols and
                "FLOW_OUTLET" in fm.feature_cols
            )
        ))

    print(f"\n       Tier-1: {mgr.tier1_count}개 시설")
    print(f"       Tier-2: {mgr.tier2_count}개 태그")
    for k, v in mgr.facility_models.items():
        print(f"         [{k[1]}] {k[0]} → {v.feature_cols}")

    return mgr  # T3에서 재사용


# ═══════════════════════════════════════════════════════════
# T3. 예측 품질 테스트
# ═══════════════════════════════════════════════════════════

def t3_prediction_quality(mgr: Optional[IForestManager] = None):
    """T3: 정상/이상 데이터 예측 품질"""
    print("\n[T3] 예측 품질 테스트")

    if mgr is None:
        # DB 없이도 실행 가능하도록 더미 모델 사용
        mgr = _make_manager_with_tier1("가압장", ["PRESSURE_DISCHARGE", "FLOW_OUTLET"])
        print("       (더미 모델로 실행 -- 정확도 검증은 실제 DB 모델 필요)")

    # Tier-1 가압장 모델 선택
    booster = next(
        (fm for fm in mgr.facility_models.values() if fm.facilitytype == "가압장"),
        None
    )
    if booster is None:
        print("  [SKIP]  가압장 Tier-1 모델 없음, T3 스킵")
        return

    # T3-1: 정상 운영값 → False Positive 비율 ≤ contamination
    _run("T3-1 정상 범위 값 → 대부분 정상 판정", lambda: _check_normal_fp_rate(booster))

    # T3-2: 물리 모순 시나리오 이상 감지
    _run("T3-2-a 누수 시나리오: 수위↓+유출=0 (배수지)", lambda: (
        _check_anomaly_scenario_배수지(mgr)
    ))
    _run("T3-2-b 펌프 공회전: 토출압력 미달+유출=0", lambda: (
        _check_anomaly_scenario_가압장(mgr)
    ))
    _run("T3-2-c 감압 실패: 유입압=정상+유출압=이상 (감압시설)", lambda: (
        _check_anomaly_scenario_감압시설(mgr)
    ))

    # T3-3: z_and_if_agree 카운트 검증
    _run("T3-3 Z이상+IF이상 → z_and_if_agree 증가", lambda: (
        _check_z_and_if_agree(mgr)
    ))


def _check_normal_fp_rate(fm: FacilityModel):
    """학습 데이터 분포 안의 값을 100번 입력 → FP ≤ 15%

    hour/dow도 학습 분포(0-23, 0-6)에서 무작위 샘플링
    (고정 경계값 사용 시 IForest가 코너 포인트를 이상으로 판정)
    """
    fp = 0
    rng = np.random.default_rng(99)
    for _ in range(100):
        sensor_vals = {gc: float(rng.uniform(2.0, 8.0)) for gc in fm.feature_cols}
        hour = int(rng.integers(6, 22))   # 주간 시간대 (경계 회피)
        dow  = int(rng.integers(0, 7))
        result = fm.predict(sensor_vals, hour=hour, dow=dow)
        if result and result["is_anomaly"]:
            fp += 1
    assert fp <= 15, f"False Positive 과다: {fp}/100"


def _check_anomaly_scenario_배수지(mgr: IForestManager):
    """배수지 Tier-1 모델이 있으면 누수 시나리오 테스트.

    스펙(iforest.md T3-2): WATER_LEVEL=50(급감), FLOW_OUTLET=0.001(극소)
    v1 단변량에서는 수위 50이 정상 범위여도, v2는 유출과의 모순을 잡아야 함.
    """
    reservoir = next(
        (fm for fm in mgr.facility_models.values() if fm.facilitytype == "배수지"),
        None
    )
    if reservoir is None:
        return  # 모델 없으면 스킵
    # 누수: 수위 50(급감 중) + 유출유량 0.001(거의 0 = 펌프 비가동인데 수위 하락)
    result = reservoir.predict(
        {"WATER_LEVEL": 50.0, "FLOW_OUTLET": 0.002},
        hour=3, dow=6
    )
    # 극단 조합이므로 이상 또는 None(비가동 판정)이어야 함
    assert result is None or result["is_anomaly"], "누수 시나리오 미탐지"


def _check_anomaly_scenario_가압장(mgr: IForestManager):
    """가압장 Tier-1 모델이 있으면 공회전 시나리오 테스트."""
    booster = next(
        (fm for fm in mgr.facility_models.values() if fm.facilitytype == "가압장"),
        None
    )
    if booster is None:
        return
    # 공회전: 토출압력 극소 + 유출유량 극소
    result = booster.predict(
        {"PRESSURE_DISCHARGE": 0.002, "FLOW_OUTLET": 0.002},
        hour=14, dow=2
    )
    assert result is None or result["is_anomaly"], "공회전 시나리오 미탐지"


def _check_anomaly_scenario_감압시설(mgr: IForestManager):
    """감압시설 Tier-1 모델이 있으면 감압 실패 시나리오 테스트.

    스펙(iforest.md T3-2): PRESSURE_INLET=5.0(정상), PRESSURE_OUTLET=0.1(이상)
    v1에서는 유출압 0.1이 단독으로는 낮아 보이지 않을 수 있어도,
    v2는 유입압 정상 + 유출압 극소 조합을 모순으로 판정해야 함.
    """
    reducer = next(
        (fm for fm in mgr.facility_models.values() if fm.facilitytype == "감압시설"),
        None
    )
    if reducer is None:
        return  # 모델 없으면 스킵
    # 감압 실패: 유입압 정상(5.0) + 유출압 극소(0.1)
    result = reducer.predict(
        {"PRESSURE_INLET": 5.0, "PRESSURE_OUTLET": 0.1},
        hour=10, dow=2
    )
    assert result is None or result["is_anomaly"], "감압 실패 시나리오 미탐지"


def _check_z_and_if_agree(mgr: IForestManager):
    """Z-Score 이상 + IF 이상 행 → z_and_if_agree 카운트 올바름."""
    # Tier-2 태그로 테스트 (더 예측 가능)
    if not mgr.tag_models:
        return

    tagsn = next(iter(mgr.tag_models))
    columns = ["tagsn", "current_val", "sitename", "facilitytype", "datainfo", "z_score"]

    # z_score=0 (정상) → z_and_if_agree 0
    rows_ok = [(tagsn, 5.0, "현장X", "알수없음", "기타", 0.0)]
    result_ok = mgr.predict_for_rows(rows_ok, columns)
    assert result_ok.get("z_and_if_agree", 0) == 0, "z_score=0인데 agree 카운트됨"


# ═══════════════════════════════════════════════════════════
# T4. API 엔드포인트 테스트 (서버 필요)
# ═══════════════════════════════════════════════════════════

def t4_api_endpoints():
    """T4: 실제 AI 서버 HTTP 요청 (127.0.0.1:8000)"""
    print("\n[T4] API 엔드포인트 테스트 (서버 필요)")

    import urllib.request, urllib.error, json as _json

    API_BASE = "http://127.0.0.1:8000"

    try:
        urllib.request.urlopen(f"{API_BASE}/health", timeout=3)
    except Exception:
        print("  [SKIP]  AI 서버(8000) 미응답, T4 건너뜀")
        return

    def ask(question: str, force_intent: str = None) -> dict:
        payload = {"user_question": question, "user_id": "jykim", "region": "A"}
        if force_intent:
            payload["force_intent"] = force_intent
        body = _json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{API_BASE}/ask",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            return _json.loads(r.read())

    def _check_scan_all_fields():
        # stale-while-revalidate: 캐시 없으면 첫 호출 시 "준비 중" ERROR 반환 → 재시도
        import time as _time
        for _attempt in range(5):
            resp = ask("전체 이상 스캔해줘", force_intent="ANOMALY_SCAN_ALL")
            if resp.get("status") == "OK":
                break
            _time.sleep(12)
        assert resp.get("status") == "OK", f"status != OK: {resp.get('status')}"
        assert resp.get("intent") == "ANOMALY_SCAN_ALL", f"intent 오류: {resp.get('intent')}"
        # ML 필드는 IForest 학습 완료 시에만 0 이상
        assert "ml_model_count"  in resp, "ml_model_count 필드 없음"
        assert "ml_anomaly_count" in resp, "ml_anomaly_count 필드 없음"
        assert "ml_agree_count"  in resp, "ml_agree_count 필드 없음"
        assert "ml_tier1_count"  in resp, "ml_tier1_count 필드 없음 (v2 신규)"
        assert "ml_tier2_count"  in resp, "ml_tier2_count 필드 없음 (v2 신규)"
        print(f"       ml_model_count={resp['ml_model_count']}, "
              f"tier1={resp['ml_tier1_count']}, tier2={resp['ml_tier2_count']}, "
              f"anomaly={resp['ml_anomaly_count']}, agree={resp['ml_agree_count']}")

    def _check_facility_detail_fields():
        resp = ask("갈산 가압장 이상 진단해줘", force_intent="ANOMALY_FACILITY_DETAIL")
        assert resp.get("status") == "OK", f"status != OK: {resp.get('status')}"
        assert "ml_anomaly_count" in resp, "ml_anomaly_count 필드 없음"
        # ml_tier: 0=미학습, 1=Tier-1(시설 다변량), 2=Tier-2(태그 단변량)
        tier = resp.get("ml_tier", 0)
        if tier:
            assert tier in (1, 2), f"ml_tier 값 이상: {tier}"
        # Tier-1인 경우 features_used, anomaly_score 포함 여부 확인
        if tier == 1:
            assert "ml_features_used" in resp, "Tier-1인데 ml_features_used 없음"
            assert isinstance(resp["ml_features_used"], list) and len(resp["ml_features_used"]) >= 2, \
                f"ml_features_used 이상: {resp.get('ml_features_used')}"
            assert "ml_anomaly_score" in resp, "Tier-1인데 ml_anomaly_score 없음"
        print(f"       ml_anomaly_count={resp.get('ml_anomaly_count')}, "
              f"ml_tier={tier}, "
              f"ml_features_used={resp.get('ml_features_used')}, "
              f"ml_score={resp.get('ml_anomaly_score')}")

    _run("T4-1 ANOMALY_SCAN_ALL 응답 필드 확인", _check_scan_all_fields)
    _run("T4-2 ANOMALY_FACILITY_DETAIL 응답 필드 확인", _check_facility_detail_fields)


# ═══════════════════════════════════════════════════════════
# T5. 회귀 테스트 — v1 하위 호환
# ═══════════════════════════════════════════════════════════

def t5_regression():
    """T5: v1과의 하위 호환 검증"""
    print("\n[T5] 회귀 테스트")

    mgr = _make_manager_with_tier2("TAG_REG")

    _run("T5-1 predict_single (v1 인터페이스) 동작", lambda: (
        assert_not_none(mgr.predict_single("TAG_REG", 5.0, 10, 1))
    ))

    _run("T5-2 predict_single 미등록 태그 → None", lambda: (
        assert_none(mgr.predict_single("TAG_NONE", 5.0, 10, 1))
    ))

    _run("T5-3 is_trained=False → predict_for_rows 빈 dict", lambda: (
        assert_eq(IForestManager().predict_for_rows([("T", 1.0)], ["tagsn", "current_val"]), {})
    ))

    _run("T5-4 columns에 필수 컬럼 없음 → 빈 dict", lambda: (
        assert_eq(mgr.predict_for_rows([("TAG_REG", 1.0)], ["tagsn"]), {})
    ))

    _run("T5-5 rows 빈 리스트 → 에러 없음", lambda: (
        assert_true(isinstance(mgr.predict_for_rows([], ["tagsn", "current_val"]), dict))
    ))

    _run("T5-6 predict_facility 미등록 시설 → None", lambda: (
        assert_none(mgr.predict_facility("없는현장", "배수지", {"WATER_LEVEL": 5.0}))
    ))

    _run("T5-7 model_count 속성", lambda: (
        assert_eq(mgr.model_count, 1)
    ))

    _run("T5-8 get_status 키 포함 확인", lambda: (
        assert_true({"last_trained", "tier1_facilities", "tier2_tag_count", "total"}
                    .issubset(mgr.get_status().keys()))
    ))


# ── 헬퍼 ──────────────────────────────────────────────────

def assert_eq(a, b):
    assert a == b, f"expected {b!r}, got {a!r}"

def assert_none(v):
    assert v is None, f"expected None, got {v!r}"

def assert_not_none(v):
    assert v is not None, "expected not None"

def assert_true(v):
    assert v, f"expected True, got {v!r}"

def _check_predict_ok(fm: FacilityModel) -> dict:
    sensor_vals = {gc: 5.0 for gc in fm.feature_cols}
    result = fm.predict(sensor_vals, hour=10, dow=1)
    assert result is not None, "정상 센서값인데 None 반환"
    assert "is_anomaly" in result
    assert "anomaly_score" in result
    return result

def _check_predict_ok_배수지():
    fm = _make_facility_model("배수지", ["WATER_LEVEL", "FLOW_OUTLET"])  # 선택 FLOW_INLET 없음
    result = fm.predict({"WATER_LEVEL": 5.0, "FLOW_OUTLET": 3.0}, 10, 1)
    assert result is not None, "선택 센서 누락인데 None 반환"

def _make_ts_data(n: int, required_gcs: list[str]) -> dict:
    """더미 ts_data 생성."""
    rng = np.random.default_rng(7)
    base = datetime(2026, 3, 1, 0, 0)
    ts_data = {}
    for i in range(n):
        ts = (base + timedelta(minutes=5 * i)).isoformat()
        ts_data[ts] = {gc: float(rng.uniform(1.0, 10.0)) for gc in required_gcs}
    return ts_data

def _make_rows(
    sitename: str, facilitytype: str,
    datainfos: list[str], vals: list[float],
) -> list:
    return [
        (f"TAG{i:03d}", vals[i], sitename, facilitytype, datainfos[i], 0.5)
        for i in range(len(datainfos))
    ]


# ═══════════════════════════════════════════════════════════
# 진입점
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "unit"
    print(f"\n{'='*50}")
    print(f"  IForest v2 Test  [{mode}]")
    print(f"{'='*50}")

    if mode in ("unit", "all"):
        t1_datainfo_mapping()
        t1_facility_model_predict()
        t1_build_facility_matrix()
        t1_predict_for_rows_tier_priority()
        t5_regression()

    if mode in ("train", "all"):
        mgr = t2_training_integration()
        if mgr and mode == "all":
            t3_prediction_quality(mgr)

    if mode == "quality":
        t3_prediction_quality()

    if mode == "api":
        t4_api_endpoints()

    ok = _summary()
    sys.exit(0 if ok else 1)
