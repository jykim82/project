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

**향후 전망 물리 한계 클램프 (2026-07-11):** 외삽은 단순 선형회귀라 유계 신호
(수위·유량 등)를 상승/하락 기울기로 6~24h 연장하면 물리적으로 불가능한 값으로
폭주한다(예: 행정 배수지 수위가 만수위 4.2m 를 크게 초과). `compute_comparison`
에서 `trend_kind` 별 상·하한으로 forecast series 를 클램프한다:
- **level(수위)**: 상한 = 만수위(`threshold/0.9`), 하한 = 0. 한계에서 평탄화.
- **flow/pressure/quality**: 하한 = 0 (음수 불가), 상한 = 관측최대 + 여유(범위 또는 20%).
- 임계 미설정: 관측 범위 ± 여유.

클램프 후에도 `hours_to_threshold`("N시간 후 한계 접근")는 원 기울기로 계산되어
의미를 유지한다.

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

  // B안 — 인과 후보 hint (baseline/forecast 가 warning/alert 일 때만)
  causal_hint?: {
    summary: string;                // "죽동(가압장) 최근 6시간 알람 1857건 / ..."
    sources: {
      sitename: string;
      facilitytype: string;
      kind: "alarm" | "flow_change";
      detail: string;               // "최근 6시간 알람 1857건" / "출수량 28% ↓ (7일 평균 대비)"
    }[];
    chat_intent?: string;           // 후속 진단 인텐트 (예: FACILITY_ALARM_CAUSE_DIAGNOSIS_RANK)
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

### 4.5 비교 의미 없음 — 자동 skip 대상 (2026-05-24 추가)

다음 트렌드 종류는 baseline·forecast 모두 의미가 없으므로 `compute_comparison`
이 **None 반환** (응답의 `comparison` 에서 omit). 프런트는 자동으로 표시 제외.

| 대상 | 판정 기준 | 이유 |
|------|----------|------|
| **적산 유량** | `datainfo` 에 `"적산"` 포함 | 단조증가 — baseline 평균/σ 무의미, forecast 도 항상 상승 (한계 임계 도달 의미 없음) |
| **디지털 태그** | `tagtype = "Digital Input"` | 0/1 또는 상태값 — 평균·표준편차 무의미 |
| **누적 단위** | `unit` 이 `"m³"` / `"kWh"` / `"kg"` 등 누적 단위 | 누적 측정값은 baseline 비교 의미 없음 |
| **샘플 < 24** | 학습 데이터 < 1일 | 패턴 학습 불가능 (기존 규칙) |
| **상수값** | 학습 윈도우 내 σ ≈ 0 | 변동 없음 → 이격 측정 불가 |

**유량 케이스 예시**:
- `죽동(배) 유출유량순시` (datainfo) → ✓ 비교 적용
- `죽동(배) 유출유량적산` (datainfo) → ✗ skip
- `죽동(배) 유량적산` → ✗ skip

→ 한 차트에 순시+적산이 같이 있어도 순시만 비교 표시.

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

### 6.3 `compute_causal_hint` — B안 인과 후보

baseline/forecast 가 warning/alert 일 때만 호출. 상류 시설 (`tb_facility_flow_map`)
의 운영 변화를 hint 로 산출.

```python
def compute_causal_hint(
    conn, sitename, facilitytype, region="R01", hours=6,
) -> dict | None:
    """
    Returns: { summary, sources, chat_intent } 또는 None.
    """
```

SQL 흐름:
1. `tb_facility_flow_map` 에서 사용자 시설이 downstream 인 상류 시설들 조회
2. 상류별 최근 6시간 알람 count (`tb_tag_raw_data` × `tb_tag_info.alarm_tag_yn=1`)
3. 상류 outflow 매핑 (`tb_epanet_facility_flow_map.role='outflow'`) 의 최근 6h
   평균 vs 7일 baseline 평균 — `|pct| >= 15` 만 source 로 추가
4. summary 문자열 = source 상위 3개 join
5. **chat_intent 신뢰도 보장 (2026-06-02)**:
   - alarm source 중 detail 의 N건 추출 후 **≥10건** 인 source 가 있을 때만
     `chat_intent = "FACILITY_ALARM_CAUSE_DIAGNOSIS_RANK"` 부여
   - flow_change 만 있는 경우 (전용 인텐트 미존재) → chat_intent omit
   - 이유: 후속 진단 인텐트의 SQL 윈도우 (24h) 와 본 hint 윈도우 (6h) 불일치
     로 인해 인텐트 응답이 "조회된 데이터 없음" 되는 케이스 방지
   - 프런트 `CausalHintCard` 는 `chat_intent` 없으면 "→ 상세 원인 진단 →"
     버튼을 hide (사용자 혼선 방지)

source 없으면 None 반환 → 응답에서 omit.

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

`PlotChart.tsx` 의 `option` useMemo 내부에서 `showBaseline` / `showForecast`
에 따라 series 동적 추가:

- `showBaseline=true` →
  - `series` 에 baseline dashed line + `band_lower~band_upper` 음영
  - 음영은 ECharts stack 방식: `band_lower` (line opacity 0, areaStyle 없음) +
    `(band_upper - band_lower)` (line opacity 0, areaStyle 색) 두 series 를
    같은 stack 으로 묶어 결과적으로 lower~upper 영역만 채움
- `showForecast=true` →
  - 외삽 dashed line — x_axis 끝에 `forecast_times` 이어붙이고 기존 series
    는 `padForecast` (null 배열) 로 길이 보정
  - `markLine` 으로 `threshold_value` 가로선 (label `threshold_label`)
- 유량 + showBaseline + CUSUM 데이터 있음 → 차트 하단에 mini CUSUM bar
  (별도 sub-chart, P2)

### 7.5 Overlay 시각 스타일 가이드 (2026-05-24 강화)

**원칙**: 다크모드 차트 배경에서 baseline·forecast 가 메인 line 과 명확히
구분되고 정상 범위 음영이 한눈에 보여야 한다.

| 요소 | 스타일 |
|------|--------|
| 정상 범위 음영 (areaStyle) | `rgba(16, 185, 129, 0.32)` (이전 0.18 → 0.32) |
| baseline line | dashed / `width 2.5` (이전 1.5) / `color #34d399` (이전 `#10b981`) |
| baseline shadow | `shadowBlur 4, shadowColor rgba(52, 211, 153, 0.5)` |
| baseline z-order | `z: 5` (메인 line 위에 떠서 비교 직관 — 이전 z:2) |
| baseline smooth | `true` (부드러운 dashed) |
| forecast line | dashed / `width 2.5` / `color #fb923c` |
| forecast shadow | `shadowBlur 4, shadowColor rgba(251, 146, 60, 0.5)` |
| forecast z-order | `z: 5` |
| threshold markLine | dashed `width 1.5` / `color #ef4444` / label `fontWeight bold` |

**stack 음영 구현 (중요 — 변수명 혼동 주의)**:
```typescript
// (1) base: 하한 line (invisible)
series.push({ data: b.band_lower, lineStyle: { opacity: 0 }, stack: "baseline-band", silent: true });
// (2) stack on top: (upper - lower) 차이 + areaStyle
series.push({
  data: b.band_upper.map((upper, i) => {
    const lower = b.band_lower[i];
    return upper != null && lower != null ? upper - lower : null;
  }),
  lineStyle: { opacity: 0 },
  stack: "baseline-band",
  areaStyle: { color: "rgba(16, 185, 129, 0.32)" },
  silent: true,
});
```

→ 결과: `lower` 위에 `(upper - lower)` 가 쌓여 최종적으로 `lower~upper`
영역만 음영. 두 번째 series 의 data 로 `(upper - lower)` 차이 사용 (절대값
upper 가 아님) 이 핵심.

### 7.5b 계산 기법 툴팁 (2026-05-24 추가)

사용자가 보조 지표가 어떻게 계산됐는지 인지할 수 있도록 모든 진입점에서
hover 툴팁으로 method/학습 윈도우/임계 등을 표시한다.

**ComparisonBadge / 토글 버튼** (`title` 속성, 멀티라인):
- baseline:
  ```
  [기법] 같은 시간×요일 평균 (학습 14일)
  [정상 범위] 평균 ±2σ (음영)
  [현재 이격] +18.4%
  [판정] 주의 · 평소보다 +18.4% ↑ (anomaly_detector z-score 그룹 임계 적용)
  ```
- forecast:
  ```
  [기법] 최근 24샘플 선형회귀 외삽
  [외삽 윈도우] 24시간
  [위험 임계] 14.5 (운영 한계 (HH))
  [판정] 5.2시간 후 한계 도달
  [도달 예상] 5.2 시간 후
  ```

**CausalHintCard** (`title`):
- ```
  [기법] tb_facility_flow_map 상류 시설 join
  [조회 윈도우] 최근 6시간
  [알람] tb_tag_raw_data 의 alarm_tag_yn=1 태그 변화 count
  [유량 변화] tb_epanet_facility_flow_map outflow 매핑 시설의
              (최근 6시간 평균 vs 7일 baseline 평균) ±15% 이상만
  [발동 조건] baseline.status 또는 forecast.status 가 warning/alert
  ```

**ECharts dashed line** (legend / hover tooltip):
- `series.name = "평소 패턴 [같은 시간×요일 평균 (학습 14일)]"` 형식
- `series.name = "향후 전망 [최근 24샘플 선형회귀 외삽, 24h]"` 형식
- ECharts 의 기본 legend·tooltip 이 method 가 부착된 series 명을 표시
  → 운영자가 어떤 line 이 무슨 기법인지 한눈에 인지

**메소드 → 한국어 라벨 매핑** (`comparison-overlay.ts` 와 `ComparisonOverlayUI.tsx` 양쪽에 동일 정의):
- baseline.method:
  - `hourly_mean` → "같은 시간×요일 평균"
  - `regression` → "회귀 (HuberRegressor)" (P2)
  - `nmf_30d` → "야간최소유량 30일 baseline" (P2)
- forecast.method:
  - `linear` → "최근 24샘플 선형회귀 외삽"
  - `moving_avg_slope` → "이동평균 기울기 외삽" (P2)

### 7.7 다중 tag·이종 trend 처리 — 비교 대상 셀렉터 (2026-05-24 추가)

한 차트에 여러 시리즈가 있을 때 (수위+유량, 다중 시설 동종, 순시+적산 등)
보조 지표는 **활성 1개 tag** 만 표시. 사용자가 dropdown 으로 전환.

#### 백엔드
- `/trend/data` 응답의 `comparison: Record<tag_id, ComparisonData>` 이미 다중
  tag 지원 (단계 변경 없음)
- §4.5 skip 규칙 적용 결과로 적산·디지털 등은 응답에서 자체 omit

#### 프런트 — 활성 tag 선택 로직
1. **자동 기본값** (worst-status 우선):
   - 응답의 `comparison` 객체 중 `baseline.status` 또는 `forecast.status` 가
     가장 심각한 tag 선택 (alert > warning > normal)
   - 동률 시 차트 series 순서 (`selectedTags[0]` 우선)
2. **localStorage 영속**: 키 `trend-overlay-active-tag-{chartKey}` —
   - `chartKey`: PlotChart = `plot.tag_ids?.[0]` 기반 hash / TrendChart = 첫
     tag id
   - 사용자가 직접 선택한 tag 가 다음 진입에서도 유지
3. **자동 무효화**: 활성 tag 가 새 응답의 `comparison` 에 없으면 (skip 됐거나
   사라짐) → 워스트 자동 재선택

#### UI — ComparisonHeader 의 셀렉터

```
[비교 대상: 죽동 수위#1 ▾]  📊 평소 대비 (주의 · 평소보다 4.1% ↓)  ⏱ 향후 전망 (안전 24h+)
                ├ 죽동 수위#1 [주의]
                ├ 죽동 수위#2 [정상]
                ├ 죽동 출수유량순시 [정상]
                ╰ (죽동 적산유량 — 비교 대상 아님)
```

- comparison 응답에 있는 tag 만 dropdown 표시 (없는 건 회색 / "비교 대상 아님" 안내)
- 옵션마다 status 미니 배지 (정상/주의/이상)
- 단일 tag 면 dropdown 숨김 (현재와 동일)

#### 시각화
- 활성 tag 의 baseline + forecast overlay 만 표시 (다중 overlay 혼잡 방지)
- 다른 시리즈는 메인 line 그대로
- 활성 tag 의 메인 line 색상과 overlay 색상 매칭 (emerald=baseline, orange=forecast)

#### 대안 — "전체 보기" 모드 (P2 검토)
헤더에 모든 비교 가능 tag 의 status 배지 나열. overlay 는 active 1개만. 운영자가
한 화면에서 다중 시설 상태 인지하고 싶을 때.

### 7.6 통일 적용 대상 (2026-05-24)

본 사양의 보조 기능 (평소 대비 / 향후 전망 / causal_hint) 은 **모든 트렌드
차트에 동일하게 적용**한다. 사용자가 어느 진입점으로 트렌드를 보든 동일한
배지·토글·overlay·인과 hint 가 표시되어야 한다.

| # | 진입점 | 컴포넌트 | 통합 방식 |
|---|--------|--------|-----------|
| 1 | **AI 채팅** 트렌드 응답 | `components/chat/PlotChart.tsx` | `chat-response-mapper` 가 SSE/일반 응답의 `comparison` 필드 PlotData 로 매핑 |
| 2 | **트렌드 메뉴** (`/trend`) 사용자 트렌드 | `components/trend/TrendChart.tsx` (via `trend-store`) | `fetchTrendData` 응답의 `comparison` 을 `comparisonMap` 으로 store 보관 → page 가 prop 전달 |
| 3 | **트렌드 메뉴** > 모니터링 (`/monitoring/reservoir`, `/booster`, `/block`) | `MonitoringTrendBlock → TrendChart` | `monitoring-view-store.CatalogTrendData.comparison` 추가 → block 에서 prop 전달 |
| 4 | **GIS 관망도** 시설 클릭 트렌드 팝업 | `components/gis/GisTrendPopup.tsx → TrendChart` | `fetchTrendData` 응답의 `comparison` 을 로컬 state 로 저장 후 prop 전달 |

**공통 자산** (PlotChart ↔ TrendChart 공유):
- `lib/chart-options/comparison-overlay.ts` (`applyComparisonOverlay`)
- `components/chat/ComparisonOverlayUI.tsx` —
  - `ComparisonHeader` (KPI 배지 + 토글)
  - `CausalHintCard` (purple chip + sources + chat_intent 트리거)
  - `loadComparisonPrefs` / `saveComparisonPrefs` (localStorage)

**일관성 원칙**:
- 동일 라벨 ("정상" / "주의 · 평소보다 N% ↑↓" / "이상 · 평소보다 N% ↑↓" / "안전 (24시간+)" / "N시간 후 한계 접근" / "N시간 후 한계 도달")
- 동일 색상 (emerald/amber/rose), 동일 z-order, 동일 음영 opacity 0.32
- 동일 localStorage 키 `trend-overlay-prefs` 영속 — 한 곳에서 켜두면 다른 차트에서도 기본 ON
- 동일 백엔드 응답 — `/ask` (PlotChart), `/trend/data` (TrendChart/GisTrendPopup) 양쪽 응답 모두 `compute_comparison` 동일 함수 호출
- causal_hint 도 동일 표시 — 어디서 트렌드를 보든 인과 후보가 같이 노출

**신규 트렌드 차트 추가 시 의무**: 위 4개 외 신규 진입점이 추가되면 동일
공통 헬퍼·UI 컴포넌트를 사용해야 한다. 별도 차트 컴포넌트 신설 금지 (분기
필요 시 TrendChart 의 prop 으로 확장).

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

### P1 (본 사양 — ✅ 1차 출시 완료 2026-05-21~24)
- ✅ 백엔드 `slm/trend_comparison.py` — hour×weekday baseline + linear 외삽 +
  상태 판정 (`anomaly_detector.classify_z_level_by_group` 재사용)
- ✅ 트렌드 인텐트 화이트리스트 + `FACILITY_TREND` label 자동 분류
- ✅ 임계값 조회 (수위 zone_1_height·압력 critical_pressure·수질 상수)
- ✅ B안 `compute_causal_hint` — 상류 알람·유량 변화 hint
- ✅ `/ask` (PlotChart) + `/trend/data` (TrendChart) 양쪽 응답 통합
- ✅ 공통 추출 `comparison-overlay.ts` + `ComparisonOverlayUI.tsx`
- ✅ 4개 진입점 (AI 채팅 / 트렌드 메뉴 / 모니터링 / GIS 팝업) 동일 적용
- ✅ 가시성 강화 (음영 0.32, dashed 2.5px + shadow, z:5)
- ✅ 계산 기법 hover 툴팁 (배지·토글·causal_hint 카드·ECharts series.name)
- ✅ localStorage `trend-overlay-prefs` 영속

### P2 (후속)
- baseline `regression` 모델 (HuberRegressor) — 정확도 향상
- 유량 인텐트 CUSUM 통합 mini-bar
- 외삽 `moving_avg_slope` (linear 부족 시)
- `docs/iforest.md` IF 도입 (이상 검출 보강)
- AI 요약 (`/trend/explain`) 의 LLM 프롬프트에 comparison 컨텍스트 주입
  → 근거 기반 자연어 설명 (xAI)

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

- 2026-07-09 — 역동적 UX 적용 (dynamic-ux 시안 16·24)
  · **시안 16 CompareSlider** (`components/trend/CompareSlider.tsx` 신규): 평소 대비
    활성 시 차트 아래 before/after wipe 슬라이더. 손잡이로 좌=평소 기대값(baseline
    점선 + 정상범위 밴드) / 우=현재 실측을 가름. `ComparisonBaseline.series/band_upper
    /band_lower/deviation_pct` 실데이터만 사용. EPANET 유량수지는 제외(사용자 지시).
    TrendChart 에서 `showBaseline && comparison.baseline.series` 조건 렌더.
  · **시안 24 브러시-줌**: 트렌드 단일 패널 옵션(`plot-chart.ts`)에 toolbox.feature.
    dataZoom(x축 러버밴드 확대) + restore 추가. 드래그로 구간 박스 확대.
  · 검증(Playwright): 가곡 수위 평소대비 ON → CompareSlider wipe(clip 300→150)·
    평소/실측 라인 · toolbox "구간 확대/전체 보기". 커밋 `slm-dashboard@dc964c6`(16)·
    `de149cd`(24).
- 2026-05-24 (심야 4) — 다중 tag·이종 trend 처리 (§4.5 + §7.7 신설)
  · 의도: "유량순시+적산", "수위+유량", "동종 다중" 같이 한 차트에 여러 시리즈가
    있을 때 보조 지표가 어떻게 적용되는가 명세
  · §4.5 — 비교 의미 없는 대상 자동 skip (적산/디지털/누적 단위/상수값)
  · §7.7 — 활성 tag 선택 로직 (worst-status 자동 + localStorage 영속 +
    dropdown 셀렉터) + 단일 overlay 정책 (혼잡 방지)
- 2026-05-24 (심야 3) — xAI: `/trend/explain` LLM 컨텍스트에 comparison 주입
  · 백엔드 `endpoints/trend.py` — `comparison` body 필드 파싱 + `comparison_block`
    프롬프트 섹션 + `comparison_rule` (a/b/c 자연어 설명 규칙) + allowed_numbers
    확장 (deviation_pct / learning_window_days / threshold_value /
    hours_to_threshold / forecast_hours / causal_hint source 수치)
  · 응답 메타 `context_used` 에 `comparison.baseline/forecast/causal_hint` 표시
  · 프런트 `PlotChart.handleExplain` + `TrendChart.handleExplain` — body 에
    `comparison: plot.comparison` / `comparisonMap[firstAnalog.id]` 추가
  · 효과: AI 요약이 단순 추세 묘사가 아니라 "기법 + 학습 기간 + 평소 대비 +
    향후 전망 + 원인 후보" 를 자연어로 풀어 설명 (xAI 자연어 explanation)
- 2026-05-24 (심야 2) — 계산 기법 hover 툴팁 (§7.5b 신설)
  · ComparisonBadge / 토글 / CausalHintCard 모두 `title` 멀티라인 (기법/학습/임계/판정)
  · ECharts dashed line 의 `series.name` 에 method 라벨 부착 → legend·tooltip 자동 노출
  · `comparison-overlay.ts` + `ComparisonOverlayUI.tsx` 양쪽에 동일 method→라벨 매핑
  · 사용자 요청: "보조 지표에 마우스 커서를 오버하면 툴팁으로 어떤 기법으로 계산되었는지 표현"
- 2026-05-24 (심야) — GIS 관망도 GisTrendPopup 통합 + §7.6 통일 적용 대상 명시
  · GisTrendPopup 의 `fetchTrendData` 응답에서 `comparison` 추출 → `TrendChart`
    에 `comparisonMap` prop 전달 — 코드 4줄 추가 (이미 TrendChart 사용 중이라
    공통 헬퍼 자동 적용)
  · §7.6 신설 — AI 채팅 / 트렌드 메뉴 / 모니터링 / GIS 팝업 4개 진입점 모두
    동일 적용 명시 + 신규 차트 추가 시 의무 규정
- 2026-05-24 (저녁) — B안 적용 + 트렌드 메뉴·모니터링 페이지 통합
  · 백엔드 `slm/trend_comparison.py:compute_causal_hint` 신규 — 상류
    `tb_facility_flow_map` join 으로 알람·outflow 변화 hint 산출 (baseline/forecast
    가 warning/alert 일 때만)
  · `/trend/data` 응답에 `comparison: Record<tag_id, ComparisonData>` 추가
  · 공통 추출: `lib/chart-options/comparison-overlay.ts` (applyComparisonOverlay) +
    `components/chat/ComparisonOverlayUI.tsx` (ComparisonHeader/CausalHintCard)
  · PlotChart (채팅) / TrendChart (트렌드 메뉴 · 모니터링 배수지/가압장/블록)
    동일 공통 헬퍼 사용
  · monitoring-view-store 의 `CatalogTrendData` 에 comparison 필드 추가 →
    MonitoringTrendBlock 이 TrendChart 에 전달
  · 검증: 죽동 배수지 수위 24h → baseline "주의 · 평소보다 4.1% ↓" +
    causal_hint "죽동(가압장) 최근 6시간 알람 1857건"
- 2026-05-24 — 평소 대비 overlay 가시성 강화 (§7.5 신설)
  · stack 음영 로직 버그 fix (변수명 혼동으로 잘못된 영역 계산 → `band_lower` base + `(upper - lower)` stack 으로 교정)
  · 음영 opacity 0.18 → 0.32, baseline line 1.5px → 2.5px + shadow + `#34d399` (밝은 emerald)
  · z-order 2 → 5 (메인 line 위에 떠서 비교 직관)
  · forecast 동일 강화 (2.5px + shadow + bold threshold 라벨)
  · 사용자 피드백 "평소 대비 트랜드가 눈에 잘 안띄네" 대응
  · 커밋: `slm-dashboard@6ffa335`
- 2026-05-22 — 트렌드 비교 4회 운영 검증 (메모리 영속)
  · 4 시설 (신평·죽동·합덕·남산 배수지 수위) 채팅 UI 테스트
  · 합덕 forecast warning "22시간 후 한계 접근" 발견 — 외삽 알고리즘 실 데이터 작동 검증
  · `memory/project_trend_comparison_v1.md` 신규 (의도·설계·산출물·검증·P2)
- 2026-05-21 — v1 초안 작성 (사용자 의도 2개 압축 + 토글 2개 통합 설계 +
  NMF/CUSUM 통합 방향) + P1 구현 완료 + 4회 백엔드 검증
  · `slm/trend_comparison.py` 신규 (hourly_mean baseline + linear 외삽)
  · `anomaly_detector.classify_z_level_by_group` 재사용 — 대시보드 z-score 알람과 통일
  · PlotChart ComparisonBadge + 토글 + ECharts overlay + localStorage 영속
  · 커밋: `slm@fc0faba` / `slm-dashboard@82ddab4` / `web@3195456`
