# 트렌드 정상 기대값 — 그래디언트 부스팅 baseline 사양 (v1)

> **상태:** v1 구현 완료 (2026-06-16) — P1+P2 backend·frontend 연동·검증 완료
> **목적:** 트렌드 "평소 대비" 판정의 **정상 기대값**을 시간대 평균(`hourly_mean`)
> 에서 **그래디언트 부스팅 트리(GBT) 예측값**으로 고도화해 오탐·미탐을 줄인다.
> **관련 사양:** `docs/trend-comparison-spec.md`(§5 baseline), `docs/iforest.md`
> **비변경:** z-score 판정·정상/주의/이상 분류·알람 연동·향후 전망(forecast 선형
> 외삽)·UI 배지 구조는 **그대로 유지**. 바뀌는 것은 "정상 기대값을 무엇으로 보느냐"
> 딱 하나.

---

## 1. 배경 · 결정

- 현재 baseline = 같은 `(weekday, hour)` 의 학습창 평균 (`_hourly_pattern_baseline`,
  `method="hourly_mean"`). 시간대 패턴만 반영 → 비선형·다요인 패턴을 못 잡음.
- forecast(향후 전망, "이대로 가면 한계 넘나")는 **추세 외삽**이 필요 → 트리 모델은
  학습 범위 밖을 외삽 못 하므로 **선형 회귀 유지**. GBT 는 **baseline 전용**.
- 모델 선택: `xgboost`/`LightGBM` 미설치(폐쇄망 신규 의존성 부담) vs 이미 설치된
  `scikit-learn>=1.5` 의 **`HistGradientBoostingRegressor`**(히스토그램 기반,
  LightGBM 계열, 결측 native). 데이터는 **시간 집계 후 수백만 행**이라 세 모델 성능
  동급 → **신규 의존성 0 인 HistGradientBoosting 채택**. 분 단위 원본(3.6억 행)
  직접 학습으로 확장 시 LightGBM 으로 교체(전략 함수 1개 추가 수준).
- `method` 필드가 이미 전략 셀렉터(`hourly_mean`|`gbt`) → 계약 불변, 폴백 안전.

## 2. 데이터셋 사양

### 2.1 학습 1행 = (태그, 시각) 단위
- **대상 y**: 해당 태그의 시간 평균값 `AVG(val) GROUP BY date_trunc('hour', logtime)`.
- **학습창**: 최근 **60일** 롤링.
- **전역 단일 모델**: 2,700 태그를 ③ 식별 피처로 한 모델이 커버(태그별 모델 X).
- region 격리 — region 별 1 아티팩트.

### 2.2 피처 (v1 = ①②③)

| 그룹 | 피처 | 출처 | 비고 |
|---|---|---|---|
| ① 캘린더(주기성) | hour, dow, month, is_weekend, is_holiday, sin/cos(hour), sin/cos(dow) | 시각 자체 | is_holiday = KR 정적 공휴일 리스트(폐쇄망, config) |
| ② 계절 lag(자기) | lag_24h, lag_168h(어제·지난주 같은 시각), roll_mean_24h, roll_std_24h | 대상 태그 과거 | **동시점 아님** → anomaly masking 회피 |
| ③ 태그 식별(전역) | tagsn, trend_kind, facilitytype, equipmenttype, site_group(A/B/C/D) | `tb_tag_info`, `tb_site_anomaly_profile` | categorical → native 처리 |

> **⚠️ anomaly masking 회피 원칙:** baseline 은 "이 시각·조건이면 정상값이 얼마인가"
> 만 학습한다. **동시점(t) 상관 측정값**(유입↔유출↔수위)을 피처로 넣으면 입력이
> 비정상일 때 비정상 출력을 "정상"으로 예측해 이상을 가린다. 물리 질량보존은
> `flow_imbalance`·EPANET 소관. 따라서 v1 피처는 **캘린더 + 시차 자기lag + 식별**만.

### 2.3 V2 (추후 — 화면·API 불변)
- ④ 운전 컨텍스트: 상류 시설(`tb_facility_flow_map`)의 펌프 가동율·수위를 **시차(+1h)**
  로 추가(제어 신호 위주, 동시점 공측정 제외). 모델 내부 피처 추가일 뿐 평가 화면·
  추론 인터페이스 동일. **평가 수치에서 상류 의존형 태그가 부실하면 도입 판단.**

### 2.4 skip 대상 (학습·추론 제외)
- 적산/누적/총량(단조증가) · 디지털(DI) · 데이터 < 14일 태그 → 학습 제외 +
  추론 시 **`hourly_mean` 폴백** (기존 `_is_skip_target` 재사용).

## 3. 학습 (offline)

- 엔트리: `python -m trend_baseline train [--region R01] [--window-days 60]`
  (수동 트리거 = 동일 명령). cron 은 이 명령을 호출.
- 절차: 피처셋 빌드(SQL) → 시간순 **train/holdout 분할(최근 7일 holdout)** →
  `HistGradientBoostingRegressor` 학습 → holdout 예측 → **태그별 잔차 σ**·MAE·RMSE·
  커버리지 산출 → 아티팩트 + 메타 저장 → (P2)지표 테이블 적재.
- 비교군: 동일 holdout 에서 `hourly_mean` MAE 도 계산 → **개선율** 기록.

## 4. 추론 (요청 시)

- `compute_comparison` 의 baseline 단계에서 `_gbt_baseline(conn, tagsn, target_times)`
  호출:
  1. 아티팩트 로드(프로세스 캐시, mtime 변경 시 리로드).
  2. 모델 없음/로드 실패/skip/데이터부족 → **`_hourly_pattern_baseline` 폴백**.
  3. target_times 각 시점의 피처 벡터(②lag 는 **실시간 최근 데이터**로 계산) → 예측.
  4. `baseline_series` = 예측값, `band = 예측 ± 2×태그σ`, `mean_stddev = 태그σ`.
- 반환 형식은 `_hourly_pattern_baseline` 과 동일 → 이후 z-score·판정 로직 무변경.
- `out["baseline"]["method"] = "gbt"`(폴백 시 `"hourly_mean"`),
  `out["baseline"]["model_version"]` 추가.

## 5. 갱신주기 · 관리

| 구분 | 주기 | 방식 |
|---|---|---|
| 재훈련 | **주 1회**(야간 cron) + 운영 큰 변화 시 수동 트리거 | 60일 롤링 학습창 |
| 추론 | 요청 시 실시간 | 아티팩트 로드 + 실시간 lag 피처 |
| σ(잔차) | 학습 시 산출 | 태그별 σ → z-score 입력 |

> 주 1회로 충분한 이유: 모델은 "조건→정상값" **안정 매핑**을 학습하고, 추론 입력
> (lag)은 항상 최신. 매핑은 주·계절 단위로만 천천히 변함. 주기는 cron 설정값이라
> 야간 1회로 무비용 변경 가능.

### 5.1 아티팩트 관리
- 경로: `data/models/baseline_gbt_{region}.pkl` + `baseline_gbt_{region}_meta.json`
  (피처 목록·태그별 σ·trained_at·window·overall MAE·폴백 태그·feature_set).
- 버전: `data/models/archive/baseline_gbt_{region}_{ts}.pkl` 보관(최근 N개) +
  현재 포인터. **신모델 이상 시 포인터 되돌리기 = 무중단 롤백.**
- 폐쇄망: 학습·추론 전부 온프레미스. 외부 의존 없음(numpy/sklearn 기설치).

## 6. 성능 평가 (P2)

### 6.1 지표
| 지표 | 의미 | 기준 |
|---|---|---|
| MAE/RMSE/MAPE | 예측 오차 | 낮을수록 |
| 밴드 커버리지 | 실제값 ±2σ 포함률 | ≈95% (σ 보정 적정) |
| GBT vs hourly_mean | 동일 holdout MAE 비교 | 개선율 % (업그레이드 정당성) |
| 폴백 태그 수/비율 | 데이터 부족 등 | 커버리지 추적 |

### 6.2 기록 테이블 (Migration)
- `tb_baseline_model_run` — 회차별: region, model_version, trained_at,
  train_window_days, n_tags_trained, n_tags_fallback, overall_mae, overall_rmse,
  coverage_pct, mae_hourly_mean, improvement_pct, feature_set, status,
  **dataset_summary(jsonb)**.
- `tb_baseline_tag_metric` — 태그별: region, model_version, tagsn, mae, rmse,
  sigma, coverage_pct, n_samples, method.

#### 6.2.1 데이터셋 요약 박제 (`dataset_summary` jsonb, Migration 0087)
- 회차마다 "이번 학습이 **어떤 데이터로** 됐나"를 동결 기록 → 회차별 추세 확인.
- `trend_baseline._dataset_summary(conn, df_full)` 가 min-row 필터 **전** 프레임
  으로 계산: `data_start`/`data_end`/`span_days`, `n_hourly_rows`,
  `n_tags_frame`/`n_tags_trained`/`n_tags_below_min`(`min_tag_rows` 기준),
  `by_trend_kind`, `by_facilitytype`(top-8), `skip{digital,accum,unit}`.
- 기존 회차 행은 NULL — 화면 NULL 안전 처리.

### 6.3 확인 화면 `/admin/baseline-eval` (경량 지표 뷰)
- 상단 KPI: 전체 MAE · 커버리지(95% 신호등) · **GBT vs hourly_mean 개선율** · 폴백 비율.
- **데이터셋 현황 섹션**(최신 회차 박제): 기간·시간집계 행수·학습/전체 태그수·
  최소 행수 미달 태그수 + 종류별/시설유형별/skip 사유별 분포.
- 회차 히스토리(버전·시각·지표 추이).
- 최악 태그 테이블(MAE/커버리지 정렬, method 배지, 검색·필터).
- 드릴다운 차트는 생략(필요 시 추가). 백엔드 `GET /admin/baseline-eval`.
- 메뉴: `sidebar-menus.ts` + `tb_menu` 등록.

## 7. xAI 산출 설명 갱신

- `endpoints/trend.py`·`ai_server.py` comparison 블록: baseline method 설명을
  "시간대 평균" → "그래디언트 부스팅 정상 기대값(캘린더·계절 반영)" + 모델 버전/폴백.
- 프론트 `ComparisonOverlayUI` `METHOD_LABEL_BASELINE`: `gbt` 라벨 추가.

## 8. 구축 계획 (Phase)

- **P1** — trend_baseline 모듈(피처/학습/추론) + 학습 CLI·주1회 cron + hourly_mean
  폴백 통합 + xAI 설명 갱신.
- **P2** — 성능 기록 2 테이블 + `/admin/baseline-eval` 경량 지표 뷰.
- **V2(추후)** — ④ 운전 컨텍스트 피처(평가 수치 보고 판단).

## 9. 변경 이력
- 2026-06-16 v1 — 초안. GBT baseline(정상 기대값) 사양 + 데이터셋 V1 + 평가 P2.
- 2026-06-16 v1 구현 — P1+P2 완료.
  - 백엔드: `slm/trend_baseline.py`(피처/학습/추론), `compute_comparison` 폴백
    통합(GBT 우선 → hourly_mean), `out["baseline"]["method"]`/`model_version`,
    학습 CLI `python -m trend_baseline train`, 주1회 cron 가이드
    (`docs/operations/baseline-train-cron.md`).
  - xAI: `endpoints/trend.py` comparison 블록 기대값 산출방식 표기 추가,
    프런트 `ComparisonOverlayUI`/`comparison-overlay` `gbt` 라벨 + 모델버전 툴팁.
  - P2: Migration 0085(`tb_baseline_model_run`/`tb_baseline_tag_metric`),
    `GET /admin/baseline-eval`, `/admin/baseline-eval` 경량 지표 뷰,
    Migration 0086 메뉴 등록(M100-13) + `sidebar-menus.ts`.
  - 검증(dev, 데이터 ~6.6일 → env 하향 학습창): 831 태그 학습, GBT MAE 4.86 vs
    hourly_mean 32.17 → **개선 +84.9%**, 커버리지 86.6%. 화면 KPI·히스토리·최악
    태그 렌더 확인. (운영 60일 학습창에서 커버리지 ≈95% 수렴 기대.)
  - dev 검증용 env 스위치: `BASELINE_WINDOW_DAYS`/`BASELINE_HOLDOUT_DAYS`/
    `BASELINE_MIN_TAG_ROWS`(운영 기본 60/7/336 고정, 데이터 짧은 dev 만 하향).
- 2026-06-16 데이터셋 현황 박제 — Migration 0087(`dataset_summary` jsonb 컬럼) +
  `_dataset_summary()`(기간·행수·종류/시설/skip 분포, min-row 필터 전 프레임 계산)
  → `_persist_metrics`·메타 적재 → `GET /admin/baseline-eval` latest 반환 →
  화면 "학습 데이터셋 현황" 섹션. dev 검증: 6.6일/132,129행/831 태그 렌더 확인.
