# 트렌드 비교 지표 사양 v1

> **상태:** 초안 v1 (2026-05-21)
> **목적:** 채팅 트렌드 응답·트렌드 페이지 차트에 "평소 대비" / "향후 전망"
> 비교 지표를 추가해 운영자가 한눈에 이상·위험을 인지하도록 한다.
> **관련 사양:** `docs/slm-api-contract-final.md`, `docs/metric-trend-panel-spec.md`,
> `docs/iforest.md`

---

## 1. 의도

운영자가 트렌드를 볼 때 묻는 2가지 질문에 응답한다.

| # | 질문 | UI 표현 |
|---|---|---|
| 1 | **"지금 평소보다 이상한가?"** | "평소 대비" 토글·배지 |
| 2 | **"이대로 가면 문제 생기나?"** | "향후 전망" 토글·배지 |

→ 트렌드 종류 (유량/수위/압력/수질) 와 무관하게 **동일 UI 구조**.
응답 데이터의 baseline·임계값만 종류별로 다르게 채움.

---

## 2. UI 설계

### 2.1 차트 우상단 KPI 배지 (상시 노출 — 신호등 즉답)

```
[ 정상 ]              [ 6시간 후 한계 ]
 ───────              ────────────────
 평소 대비             향후 전망
```

| 상태 | 평소 대비 라벨 | 향후 전망 라벨 | 색상 |
|------|-------------|--------------|------|
| **정상** | "정상" | "안전 (24시간+)" | emerald-500 |
| **주의** | "주의 · 평소보다 N% ↑/↓" | "N시간 후 한계 접근" | amber-500 |
| **이상** | "이상 · 평소보다 N% ↑/↓" | "N시간 후 한계 도달" | rose-500 |

라벨 "정상/주의/이상" 은 대시보드 z-score 알람과 동일 (`anomaly_detector.py`
`classify_z_level_by_group` 결과). 그룹별 임계 (A=3/4, B=2/3, C=1.5/2, D=2/3)
적용. 사용자는 z 숫자 대신 "평소보다 ±N%" 만 본다.

- 배지 **클릭 → 해당 토글 자동 ON** (overlay 표시)
- 데이터 부족 등으로 계산 불가 시 배지 hidden (회색 placeholder 표시 안 함)

### 2.2 차트 토글 2개

```
[ 📊 평소 대비 ]  [ ⏱ 향후 전망 ]
```

| 토글 | OFF | ON 시 overlay |
|------|-----|---------------|
| 평소 대비 | 원본 line | baseline dashed + 정상 범위 음영 (±2σ) + 이상 구간 highlight + (유량) CUSUM mini-bar |
| 향후 전망 | 원본 line | 외삽 line + 위험 임계 가로선 + annotation |

두 토글 모두 ON 가능. 사용자 마지막 선택은 localStorage 저장 (`trend-overlay-prefs`).

### 2.3 친화적 라벨 규칙

- 수치는 반올림 1자리 (예: "12.3%")
- 시간은 한국식 (예: "6시간 후", "1.5일 후")
- 영문 통계 용어 (σ, z-score) 노출 금지. 툴팁에만 부가 정보.

---

## 3. 데이터 모델 — `comparison` 필드

PlotData (`slm-api-contract-final.md` §시각화 응답) 에 신규 필드 추가.

```typescript
interface ComparisonData {
  // 평소 대비
  baseline?: {
    series: (number | null)[];      // x_axis 와 동일 길이 — 평소 패턴 line
    band_upper: (number | null)[];  // +2σ 또는 90 percentile
    band_lower: (number | null)[];  // -2σ 또는 10 percentile
    method: "regression" | "hourly_mean" | "nmf_30d";
    learning_window_days: number;   // 학습 데이터 기간 (예: 30)
    status: "normal" | "warning" | "alert";
    status_label: string;           // "정상" / "평소보다 12% ↑" / "이상 신호"
    deviation_pct: number | null;   // 최근값의 평소 대비 % (음수=하회)
    // 유량 인텐트 한정 — CUSUM 통합
    cusum?: {
      current: number;              // 누적 CUSUM 값
      threshold_h: number;          // 누수 의심 임계
      baseline_mean: number;
      baseline_stddev: number;
      leak_status: "normal" | "watch" | "leak_suspected";
    };
  };

  // 향후 전망
  forecast?: {
    series: (number | null)[];      // x_axis 끝점부터 forecast_hours 만큼
    forecast_times: string[];       // ISO timestamp 외삽 시점들
    method: "linear" | "moving_avg_slope";
    forecast_hours: number;         // 외삽 시간 윈도우 (기본 24h)
    threshold_value: number | null; // 위험 임계 (HH/LL/min_pressure 등)
    threshold_label: string;        // "운영 한계 (HH)" 등
    hours_to_threshold: number | null;  // 임계 도달 예상 시간 (NULL=안전)
    status: "normal" | "warning" | "alert";
    status_label: string;           // "안전 (24시간+)" / "6시간 후 한계" 등
  };

  // 메타
  trend_kind: "flow" | "level" | "pressure" | "quality";
  computed_at: string;              // ISO timestamp
}
```

---

## 4. 트렌드 종류별 baseline·임계값 매핑

### 4.1 유량 (Flow)

| 항목 | 값 |
|------|---|
| trend_kind | `"flow"` |
| baseline.method | `"nmf_30d"` (NMF 인텐트) 또는 `"regression"` |
| baseline 학습 | 최근 30일, hour×weekday feature |
| baseline.cusum | `tb_leak_cusum_alert` 또는 실시간 계산 |
| forecast.threshold_value | NMF 누계 한계 (관리 임계 또는 baseline + 3σ) |
| forecast.threshold_label | "누수 의심 한계 (NMF)" |
| forecast.method | `"linear"` (NMF 추세선) |

### 4.2 수위 (Level)

| 항목 | 값 |
|------|---|
| trend_kind | `"level"` |
| baseline.method | `"regression"` (시간×요일) |
| baseline 학습 | 최근 14일 |
| forecast.threshold_value | `tb_service_reservoir_info` 의 HH/LL 또는 `tb_block_info.critical_pressure` 적용 시설별 |
| forecast.threshold_label | "운영 한계 (HH)" 또는 "운영 한계 (LL)" |
| forecast.method | `"linear"` (현재 변화율 외삽) |

### 4.3 압력 (Pressure)

| 항목 | 값 |
|------|---|
| trend_kind | `"pressure"` |
| baseline.method | `"regression"` |
| baseline 학습 | 최근 14일 |
| forecast.threshold_value | `tb_block_info.critical_pressure` (시설별 최소 운영 압력) |
| forecast.threshold_label | "최소 운영 압력" |
| forecast.method | `"moving_avg_slope"` |

### 4.4 수질 (Chlorine / Turbidity)

| 항목 | 값 |
|------|---|
| trend_kind | `"quality"` |
| baseline.method | `"regression"` |
| baseline 학습 | 최근 14일 |
| forecast.threshold_value | 잔류염소 0.1 mg/L (수도법 최저) / 탁도 1.0 NTU 등 |
| forecast.threshold_label | "수질 기준" |
| forecast.method | `"linear"` |

---

## 5. 알고리즘

### 5.1 회귀 잔차 (baseline.method = "regression")

**입력**: 같은 tagsn 의 학습 윈도우 (14~30일) raw_data
**Feature**: `hour (0-23)` × `weekday (0-6)` one-hot (또는 sin/cos cyclical)
**모델**: `sklearn.HuberRegressor` (outlier robust) 또는 단순 hour×weekday 평균
**산출**:
- `baseline.series` = 차트 x_axis 각 시점에 대해 모델 예측값
- `baseline.band_upper/lower` = 예측값 ± 2 × 학습 잔차 σ

**경량 대안 (학습 모델 없음)**: 같은 hour×weekday 의 14일 평균 + 표준편차 — 외부
라이브러리 무필요, 정확도 충분.

→ **P1 구현은 경량 대안 (hour×weekday 평균) 먼저**, 운영 데이터 검증 후 회귀
필요 시 P2.

### 5.2 상태 판정 (baseline.status)

**대시보드 z-score 알람 체계와 통일** (`slm/anomaly_detector.py` 재사용):

```python
from anomaly_detector import classify_z_level_by_group, format_deviation_text

recent_n = 6  # 최근 6 샘플 (5분 간격 → 30분)
recent_vals = filter_non_null(series[-recent_n:])
recent_mean = mean(recent_vals)
baseline_mean = mean(baseline.series[-recent_n:])
baseline_stddev = std(baseline.series[-recent_n:])    # 학습 잔차 σ

z_score = (recent_mean - baseline_mean) / baseline_stddev
deviation_pct = (recent_mean - baseline_mean) / baseline_mean * 100

# 시설 그룹 (A/B/C/D) 조회 (tb_site_anomaly_profile 또는 fallback="B")
group = lookup_site_group(sitename, facilitytype) or "B"

# 대시보드와 동일 임계 적용
z_level = classify_z_level_by_group(z_score, group)  # "정상" / "주의" / "이상"

# status enum 매핑 (UI 일관성)
status = {"정상": "normal", "주의": "warning", "이상": "alert"}[z_level]

# 라벨도 대시보드 표현 패턴 재사용
status_label = format_deviation_text(deviation_pct, z_score, active_pct=100)
# 예: "정상" / "평소보다 +12% ↑ (주의)" / "평소보다 -38% ↓ (이상)"
```

**그룹별 임계 (slm/anomaly_detector.py GROUP_THRESHOLDS)**:
- A: warn 3.0 / error 4.0 (완화)
- B: warn 2.0 / error 3.0 (기본)
- C: warn 1.5 / error 2.0 (강화)
- D: warn 2.0 / error 3.0

NMF 인텐트는 위 z-score 판정 + CUSUM `leak_status` 둘 다 평가 후 더 보수적인
쪽 (worse) 채택:
- z="정상" + CUSUM="watch" → warning
- z="주의" + CUSUM="leak_suspected" → alert

**z-score 숫자는 사용자에게 노출하지 않음** (anomaly_detector.py 원칙).
status_label 에는 "평소 대비 ±N%" 만, z 는 툴팁에도 노출 X.

### 5.3 외삽 (forecast.method = "linear")

**입력**: 최근 N 샘플 (기본 24개 — 2시간 분량 5분 데이터)
**모델**: 단순 선형회귀 (slope + intercept) 또는 최근 N 의 평균 변화율
**산출**:
- `forecast.series` = `forecast_hours` 만큼 외삽
- `hours_to_threshold` = 외삽 line 이 threshold_value 와 교차하는 시간 (없으면 NULL)

### 5.4 상태 판정 (forecast.status)

```python
if hours_to_threshold is None or hours_to_threshold > 24:
    status = "normal"; label = "안전 (24시간+)"
elif hours_to_threshold > 6:
    status = "warning"; label = f"{hours_to_threshold:.0f}시간 후 한계 접근"
else:
    status = "alert"; label = f"{hours_to_threshold:.1f}시간 후 한계 도달"
```

### 5.5 데이터 부족 처리

- 학습 윈도우 데이터 < 24 (1일 미만) → `comparison` 필드 자체 omit
- forecast 입력 < 6 샘플 (30분 미만) → `forecast` 만 omit (`baseline` 은 유지)
- 임계값 (`threshold_value`) 없음 → `forecast.hours_to_threshold = null`,
  status_label = `"임계 미설정"`, status = `"normal"`

---

## 6. 백엔드 — `slm/trend_comparison.py` 신규 모듈

```python
def compute_comparison(
    rows: list,                # tb_tag_raw_data 결과 [(log_time, val), ...]
    columns: list[str],
    tagsn: str,
    trend_kind: str,           # 'flow' | 'level' | 'pressure' | 'quality'
    conn,
    learning_days: int = 14,
    forecast_hours: int = 24,
) -> dict | None:
    """
    Returns ComparisonData dict 또는 None (데이터 부족).
    """
```

### 6.1 호출 위치

`ai_server.py` 의 `build_success_response` 직전, anomaly_zones 계산과 같은 위치:

```python
_comparison = None
if graph_type == "plot" and rows and columns and intent in TREND_COMPARISON_INTENTS:
    _comparison = compute_comparison(rows, columns, tagsn, _trend_kind, conn)
```

`TREND_COMPARISON_INTENTS` 화이트리스트 — 트렌드 종류 결정:
- `FACILITY_TREND`, `FACILITY_MIXED_TREND` → kind 는 첫 tag 의 datainfo 패턴 매칭
- `FACILITY_NIGHT_MIN_FLOW_*`, `BLOCK_FLOW_*` → `"flow"`
- `FACILITY_LEVEL_*`, `RESERVOIR_LEVEL_*` → `"level"`
- `FACILITY_PRESSURE_*` → `"pressure"`
- `WATER_QUALITY_*` → `"quality"`

### 6.2 임계값 조회

trend_kind 별 SQL 조회:
- `flow`: `tb_night_min_flow_daily` 또는 baseline + 3σ 계산
- `level`: `tb_service_reservoir_info.zone_1_height * 0.9` (HH 근사)
- `pressure`: `tb_block_info.critical_pressure`
- `quality`: 상수 (0.1 mg/L, 1.0 NTU)

임계값 조회 실패 시 NULL 반환 (forecast 는 series 만 채움).

---

## 7. 프런트 — PlotChart 강화

### 7.1 PlotData 타입 확장

`src/lib/types/plot.ts`:

```typescript
export interface PlotData {
  // 기존 필드 ...
  comparison?: ComparisonData;
}
```

### 7.2 차트 우상단 KPI 배지

`src/components/chat/PlotChart.tsx` 헤더 영역:

```tsx
{plot.comparison?.baseline && (
  <ComparisonBadge
    status={plot.comparison.baseline.status}
    label={plot.comparison.baseline.status_label}
    kind="baseline"
    onClick={() => setShowBaseline(true)}
  />
)}
{plot.comparison?.forecast && (
  <ComparisonBadge
    status={plot.comparison.forecast.status}
    label={plot.comparison.forecast.status_label}
    kind="forecast"
    onClick={() => setShowForecast(true)}
  />
)}
```

### 7.3 토글 버튼

기존 기간 필터 옆에 추가:

```tsx
<ToggleButton on={showBaseline} onChange={setShowBaseline}>📊 평소 대비</ToggleButton>
<ToggleButton on={showForecast} onChange={setShowForecast}>⏱ 향후 전망</ToggleButton>
```

### 7.4 ECharts overlay

`buildPlotChartOption` 에 옵션 인자 `showBaseline` / `showForecast` 추가:

- `showBaseline=true` →
  - `series` 에 baseline dashed line 추가
  - `markArea` 로 band 음영 (band_lower~band_upper)
- `showForecast=true` →
  - `series` 에 forecast dashed line 추가 (다른 색)
  - `markLine` 으로 threshold_value 가로선
  - `markPoint` 로 hours_to_threshold 위치 annotation
- 유량 + showBaseline + CUSUM 데이터 있음 → 차트 하단에 mini CUSUM bar (별도 sub-chart)

---

## 8. API 응답 변경 예시

기존:
```json
{
  "intent": "FACILITY_TREND",
  "graph_type": "plot",
  "data": [...],
  "anomaly_zones": [...]
}
```

추가 후:
```json
{
  ...기존 필드 동일...,
  "comparison": {
    "trend_kind": "level",
    "baseline": {
      "series": [12.3, 12.5, ...],
      "band_upper": [12.8, 13.0, ...],
      "band_lower": [11.8, 12.0, ...],
      "method": "hourly_mean",
      "learning_window_days": 14,
      "status": "warning",
      "status_label": "평소보다 18% ↑",
      "deviation_pct": 18.4
    },
    "forecast": {
      "series": [13.5, 13.7, ...],
      "forecast_times": ["2026-05-21T23:00", ...],
      "method": "linear",
      "forecast_hours": 24,
      "threshold_value": 14.5,
      "threshold_label": "운영 한계 (HH)",
      "hours_to_threshold": 5.2,
      "status": "alert",
      "status_label": "5시간 후 한계 도달"
    },
    "computed_at": "2026-05-21T17:00:00+09:00"
  }
}
```

---

## 9. 단계 / 범위

### P1 (본 사양 — 1차 출시)
- 백엔드 `slm/trend_comparison.py` 모듈 — 경량 (hour×weekday 평균) baseline
  + linear 외삽 + 상태 판정
- 트렌드 인텐트 화이트리스트 적용 (4가지 trend_kind)
- 임계값 조회 (시설 마스터 + NMF + 상수)
- 프런트 PlotChart 에 토글 2 + 배지 2 + ECharts overlay
- localStorage 사용자 선호 저장

### P2 (후속)
- baseline `regression` 모델 (HuberRegressor) — 정확도 향상
- 유량 인텐트 CUSUM 통합 mini-bar
- 외삽 `moving_avg_slope` (linear 부족 시)
- `docs/iforest.md` IF 도입 (이상 검출 보강)

### 범위 밖
- 일간 NMF 시계열 차트 전체 재설계 (`FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS`
  현재 stddev_stats KPI 카드는 그대로 유지)
- 다중 시설 동조성 비교 (P2.3 peer 확장 별도)

---

## 10. 검증

### 10.1 단위 테스트
- `compute_comparison` — 정상/주의/위험 상태 분기
- 외삽 hours_to_threshold — 교차 없음 (안전) / 6시간 / 1.5시간 케이스
- 데이터 부족 → None 반환

### 10.2 통합 테스트 (Playwright)
- 신평 배수지 수위 트렌드 질문 → comparison 필드 응답
- 토글 ON → baseline dashed line + band 음영 렌더
- 토글 ON → forecast dashed line + threshold 가로선 렌더
- 배지 클릭 → 토글 자동 ON

### 10.3 회귀 방지
- comparison 필드 없음 → 기존 차트 그대로 (regression)
- anomaly_zones 표시 영향 없음

---

## 11. 변경 이력

- 2026-05-21 — v1 초안 작성 (사용자 의도 2개 압축 + 토글 2개 통합 설계 +
  NMF/CUSUM 통합 방향)
