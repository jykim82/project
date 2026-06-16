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
  sigma, coverage_pct, n_samples, method, **trend_kind(Migration 0088)**,
  **lag_avail_pct(Migration 0089)**.

#### 6.2.2 lag 확보율 — 오차의 데이터부족 기여 검증 (Migration 0089)
- 태그별 `lag_avail_pct` = holdout 행 중 `lag_24h` & `lag_168h` 둘 다 확보한 비율(%).
  낮으면 모델이 자기 이력 없이 시설유형 prior 로 예측 → 큰 MAE 가 "데이터 부족"
  기여임을 **검증 가능**. 회차별 `dataset_summary.lag24_avail_pct`/`lag168_avail_pct`
  (학습 전체 행 기준)도 박제.
- **검증 절차:** 같은 태그를 회차 간 (lag 확보율↑ vs MAE↓) 로 대조. dev(6.9일)는
  lag168 0% → 단기 한계 확인용. 운영 60일이면 lag168 ~100% → MAE 변화로 가설 검정.

#### 6.2.1 데이터셋 요약 박제 (`dataset_summary` jsonb, Migration 0087)
- 회차마다 "이번 학습이 **어떤 데이터로** 됐나"를 동결 기록 → 회차별 추세 확인.
- `trend_baseline._dataset_summary(conn, df_full)` 가 min-row 필터 **전** 프레임
  으로 계산: `data_start`/`data_end`/`span_days`, `n_hourly_rows`,
  `n_tags_frame`/`n_tags_trained`/`n_tags_below_min`(`min_tag_rows` 기준),
  `by_trend_kind`, `by_facilitytype`(top-8), `skip{digital,accum,unit}`.
- 기존 회차 행은 NULL — 화면 NULL 안전 처리.

### 6.3 확인 화면 `/admin/baseline-eval` (경량 지표 뷰)
- 상단 KPI: **전체 정확도%**(MAE·RMSE 부기) · 커버리지(95% 신호등) ·
  **GBT vs hourly_mean 개선율** · 폴백 비율.
  - 정확도% = 100 × (1 − MAE / y_scale), [0,100] 클램프. y_scale = holdout 실제값
    평균 절대크기(Migration 0090). 단위(유량 LPS·수위 m·압력 kgf)가 달라도 같은
    척도로 비교 가능. 전체 정확도 = 최신 회차 전 태그 평균. 응답 `overall_accuracy_pct`.
- **데이터셋 현황 섹션**(최신 회차 박제): 기간·시간집계 행수·학습/전체 태그수·
  최소 행수 미달 태그수 + 종류별/시설유형별/skip 사유별 분포.
- 회차 히스토리(버전·시각·지표 추이).
- 최악 태그 테이블(절대 MAE 내림차순, method 배지 + **종류 칩 필터** + **정확도%**
  + lag확보% 컬럼).
  - 절대 MAE 는 단위 큰 종류(유량 LPS·CMH)가 상위 독점 → `kind` 파라미터로
    종류별(유량/수위/압력/수질/기타) 좁혀보기. `tb_baseline_tag_metric.trend_kind`
    + `GET /admin/baseline-eval?kind=flow`, 응답 `kinds`(드롭다운용 종류·개수).
- **그룹별 정확도 테이블**(정확도 낮은 순): 종류(trend_kind) / 시설 **토글**.
  그룹별 태그수·**정확도%(색상 막대)**·평균 MAE·최대 MAE·평균 커버리지·평균
  lag확보율. 정확도% 를 헤드라인으로 두어 단위 무관 비교 + 정확도 낮은 그룹부터
  노출. 단일 태그가 아닌 그룹 단위로 모델이 잘/못 맞추는 영역 + 데이터부족
  기여(lag확보) 를 한눈에 진단. 응답 `groups.by_kind` / `groups.by_site`(각 항목에
  `accuracy_avg`).
  - by_kind 는 `tb_baseline_tag_metric` 집계, by_site 는 `tb_tag_info` 조인 후
    개별 시설(`sitename + ' ' + facilitytype`, 예: "신평 가압장", "남산11 소블록")
    단위 집계. 정확도% = AVG(100×(1−MAE/y_scale)). 평균 MAE 는 단위 스케일에
    비례하므로 참고용.
  - **그룹 행 드릴다운**: 그룹 행 클릭 시 그 그룹이 어떤 태그로 학습됐는지
    펼쳐 봄. `GET /admin/baseline-eval/group-tags?axis=kind|site&group=`
    (최신 회차, MAE 내림차순) → 태그(datadesc+tagsn)·종류·정확도%·MAE·커버리지·
    lag확보·표본·방식. 예: "죽동2 가압장"(4 태그) → 순시유량(통신/실선)·압력계1/2.
- **정성 등급 라벨**: 수치 옆에 운영자가 정상/주의/이상을 즉시 인식하도록 작은
  등급어를 부기. 정확도%(우수≥90/양호≥75/주의) · 커버리지%(정상≥92/주의≥85/미달)
  · lag확보%(충분≥80/부분≥30/부족). 평균/최대 MAE 는 단위 의존이라 등급 미부여
  (정확도% 가 그 대체 척도). 그룹·최악·드릴다운 테이블 공통 적용.
- **저신호(low-signal) 가드**: y_scale(holdout 실측 평균 절대값)이 종류별 바닥값
  미만이면 센서가 사실상 정지/대기 상태라 MAE/y_scale 비율이 폭발해 정확도%가 0%로
  왜곡된다("못 맞춤"이 아니라 "맞출 신호가 없음"). 이를 `low_signal=TRUE` 로 판정해
  (a) 정확도% 대신 **'저신호' 배지** 표시, (b) 그룹·전체 정확도 평균에서 **제외**
  (`AVG(...) FILTER (WHERE NOT low_signal)`), (c) 그룹 행에 `(N 저신호 제외)` 부기.
  - 종류별 바닥값(`LOW_SIGNAL_FLOOR`, endpoints/baseline_eval.py): flow 2.0 /
    pressure 0.5 / level 0.2 / quality 0.05 / other 0.5, 미정의 종류 default 1.0.
    단위 의존적이라
    종류별 분리 — MAE≥y_scale 규칙은 큰 신호 태그의 진짜 부정확(예: 유량 0% MAE
    232.71, y_scale~212)을 가려 채택하지 않음.
  - 응답 필드: worst·group-tags 각 태그 `low_signal`(bool), `groups.*` 각 그룹
    `n_low_signal`, 루트 `overall_low_signal`.
- **시설 진단·권장 조치**: 그룹 행 드릴다운 상단에 `DiagnosisBox` — 그 시설의 태그
  구성을 규칙으로 분석해 무엇을 할지 제시. 저신호(센서 전원·통신·가동 점검) /
  데이터 부족(lag·표본 부족 → 누적 후 재평가) / 변동 큰 태그(lag 충분한데 정확도
  낮음 → 밴드±2σ·임계 조정) / 폴백(데이터 점검) / 정상(조치 불필요) 을 구분.
  종합 판정(데이터 누적 대기 / 조치 권장 / 판단 보류 / 정상) + 조치 불릿. 프런트
  전용(`diagnoseFacility`), API 무관 — group-tags 응답만으로 산출.
- **용어 해설 토글**: 헤더 "용어 해설" 버튼 → 컬럼·지표 정의 카드(정확도%/MAE/
  RMSE/σ/커버리지%/lag확보%/개선%/표본/방식/저신호/진단·권장 조치/그룹 펼치기).
  운영자가 지표 의미를 화면 안에서 확인 — 별도 문서·교육 없이 자립 해석 가능.
  프런트 전용(API 무관).
- **종합 신뢰도 판정 배지**(최상단): 전체 정확도 + 밴드 커버리지 + 점검 필요
  시설 수를 종합해 "운영자가 5초 안에 모델 신뢰 여부 판단"하도록 한 줄 판정 —
  우수(정확도≥85·커버리지 90~98) / 양호(≥75·≥85) / 주의(≥60) / 미흡. 점검 필요
  시설(정확도<75)은 같은 배너에 칩으로 노출. 프런트 전용(`computeVerdict`).
  정확도 목표선 `ACCURACY_TARGET=85`, 커버리지 `COVERAGE_TARGET=95` 상수.
- **성능 추이 라인차트**(`PerfTrendCard`): 회차별 정확도%·커버리지%를 시계열
  라인 + 목표선 markLine 으로 — 표만으로는 안 보이는 개선/악화·커버리지 변동을
  가시화. 회차 2개 이상일 때만 표시. 회차별 정확도는 백엔드가 model_version 별
  태그 평균(저신호 제외)으로 산출해 `runs[].accuracy_pct` 로 제공(EChartWrapper, svg).
- **운영자 친화 라벨**: KPI "hourly_mean 대비 개선" → "구버전(평균) 대비"(내부
  구현어 강등), 회차 히스토리 model_version 해시 → "최신/N회 전" 라벨(해시는
  title 툴팁 유지) + 정확도% 컬럼 추가. 부제에서 hourly_mean 표기 제거.
- 백엔드 `GET /admin/baseline-eval`.
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
- 2026-06-16 최악 태그 종류 필터 — Migration 0088(`tb_baseline_tag_metric.trend_kind`)
  + 학습 시 종류 적재 → `GET /admin/baseline-eval?kind=` 필터 + 응답 `kinds` →
  화면 종류 칩(전체/유량/수위/압력/수질/기타). 절대 MAE 가 유량(스케일 큼) 상위
  독점 문제 완화. dev 검증: 압력 필터 시 top MAE 7.09(전체 410.5 대비) 확인.
- 2026-06-16 lag 확보율(데이터부족 검증) — Migration 0089(`tb_baseline_tag_metric.
  lag_avail_pct`) + 회차 메타 `dataset_summary.lag24/168_avail_pct`. 태그별 holdout
  에서 lag_24h & lag_168h 둘 다 확보한 행 비율(%) 적재 → 큰 MAE 가 "자기 이력 부족"
  기여인지 회차 간 (lag 확보율↑ vs MAE↓) 로 검증. §6.2.2 절차. dev 검증: 학습창
  6.9일이라 lag24 85.6%·lag168 0.0%(7일 미만) → 전 태그 lag_avail_pct=0. 운영
  60일 재학습 시 lag168~100% 로 MAE 감소 여부 직접 대조 가능.
- 2026-06-16 그룹별 성능 테이블 — `GET /admin/baseline-eval` 응답 `groups`
  (by_kind = `tb_baseline_tag_metric` 집계, by_site = `tb_tag_info` 조인 후 개별
  시설 `sitename + facilitytype` 집계). 그룹별 태그수·평균/최대 MAE·평균 커버리지·
  평균 lag확보. 화면 종류/시설 토글 테이블(평균 MAE 내림차순). 신규 마이그레이션
  없음(집계만). dev 검증: 종류 5그룹(기타 32.1→수위 0.16)·시설 ~90개(남산11 소블록
  79.99→가곡 배수지 0.03) 렌더 확인. (시설축은 처음 시설유형으로 구현했다가 사용자
  의도가 "남산1 소블록"·"신평 가압장" 같은 개별 시설임을 확인해 사이트 단위로 변경.)
- 2026-06-16 단위 무관 정확도% — Migration 0090(`tb_baseline_tag_metric.y_scale`,
  holdout 실제값 평균 절대크기). 정확도% = 100×(1−MAE/y_scale) [0,100]. KPI "전체
  정확도"·그룹 테이블 헤드라인(색상 막대, 정확도 낮은 순)·최악 태그 컬럼·응답
  `overall_accuracy_pct`/`accuracy_avg`/`accuracy_pct`. 절대 MAE 가 단위 스케일에
  좌우돼 "유량 79.99 vs 수위 7" 비교 불가하던 문제 해소. dev 검증: 전체 86.6%,
  종류 수질 51.9%→수위 92.9%, 유량 69.1%(MAE 29.7) vs 수위 92.9%(MAE 0.15) 대조 확인.
- 2026-06-16 그룹 드릴다운 + 정성 등급 라벨 — `GET /admin/baseline-eval/group-tags`
  (axis=kind|site, group) 신규: 그룹 행 클릭 시 해당 그룹의 학습 태그 리스트
  (datadesc 포함) 펼침. 수치 옆 등급어(정확도 우수/양호/주의, 커버리지 정상/주의/
  미달, lag 충분/부분/부족) 부기 — MAE 는 단위 의존이라 등급 제외. dev 검증:
  "죽동2 가압장"(4 태그) 펼침 → 순시유량(통신) 0%·압력계1 87% 등 + 등급 라벨 렌더.
- 2026-06-16 용어 해설 토글 — 헤더 "용어 해설" 버튼 → 지표 정의 카드(GlossaryCard,
  프런트 전용). 10개 용어(정확도%/MAE/RMSE/σ/커버리지%/lag확보%/개선%/표본/방식/
  그룹 펼치기) 정의. 운영자 자립 해석용. dev 검증: 토글 시 카드 렌더 확인.
- 2026-06-16 저신호 가드 + 시설 진단 — y_scale 이 종류별 바닥값(flow 2.0/pressure
  0.5/level 0.2/quality 0.05/default 1.0) 미만이면 `low_signal=TRUE`: 정확도% 대신
  '저신호' 배지, 그룹·전체 평균에서 FILTER 제외, 그룹 행 `(N 저신호 제외)` 부기.
  응답에 `low_signal`/`n_low_signal`/`overall_low_signal` 추가(마이그레이션 없음,
  SQL 판정만). 드릴다운 상단 `DiagnosisBox`(프런트 `diagnoseFacility`) — 저신호/
  데이터 부족/변동 큰 태그/폴백/정상 구분 + 권장 조치. 용어 해설에 저신호·진단 2종
  추가(12종). MAE≥y_scale 규칙은 큰 신호 태그의 진짜 부정확(유량 0% MAE 232.71)을
  가려 미채택, 종류별 절대 바닥값 채택. dev 검증: 전체 정확도 86.6%→86.9%(122 저신호
  제외), 유량 그룹 60.4%(48 제외), "죽동2 가압장" 펼침 → 압력계1 87% 유지·순시유량
  3종 저신호 배지 + 진단 "데이터 누적 대기" 렌더 확인.
- 2026-06-16 평가 화면 직관성 강화(운영자용) — "AI 성능 평가로서 직관적인가"
  재평가 후 4종 반영: (1) 종합 신뢰도 판정 배지 + 점검 필요 시설 칩(`computeVerdict`/
  `ModelVerdictBanner`, 정확도≥85·커버리지 90~98 기준), (2) 회차별 정확도·커버리지
  추이 라인차트(`PerfTrendCard`, EChartWrapper svg, 목표선 markLine), (3) KPI 용어
  강등(hourly_mean→"구버전(평균) 대비")·해시→"최신/N회 전" 라벨·부제 hourly_mean
  제거, (4) 회차 히스토리 정확도% 컬럼. 백엔드 `runs[].accuracy_pct`(model_version
  별 태그 평균, 저신호 제외) 추가 — 마이그레이션 없음. dev 검증: 배지 "양호 — 운영
  적용 가능"(86.9%, 점검 9곳), 추이 차트 커버리지 변동(41→89%) 가시화, 목표선 라벨
  insideStartTop 으로 우측 클리핑 해소. tsc 신규 에러 0.
