# 차트 렌더링 정책 (ECharts)

**상태:** v1 적용 완료 (2026-04-28)
**관련 코드:** `slm-dashboard/src/components/charts/EChartWrapper.tsx`, `globals.css`

---

## 1. 목적

전 시스템 차트의 **시각 일관성**, **부드러움(애니메이션)**, **DOM 직접 제어**
(CSS transition·접근성·인쇄·고DPI) 를 통일된 정책으로 보장한다.

`EChartWrapper` 가 모든 차트의 공용 진입점이며, 본 정책은 wrapper 의 default 동작
+ 페이지별 override 규칙을 정의한다.

---

## 2. 렌더러 정책

### 2.1 기본값: **SVG**

`EChartWrapper.renderer` prop default = **`"svg"`**.

이유:
- DOM 직접 접근 가능 → CSS transition·hover 효과·테마 색 변수 연동 자유
- 인쇄·PDF 시 깨끗한 벡터 출력
- 고DPI(레티나) 환경에서 흐림 없음
- 접근성(스크린리더) 우수

### 2.2 명시적 `renderer="canvas"` 지정 대상

데이터 포인트가 1만+ 또는 노드/엣지가 많아 SVG DOM 노드 폭증이 우려되는 차트:

| 컴포넌트 | 위치 | 사유 |
|---|---|---|
| `TopologyGraph` | `components/charts/TopologyGraph.tsx` | 네트워크 그래프 노드·엣지 多 |

신규로 데이터량이 큰 차트를 추가할 때:
- 일/시간 단위 다운샘플(LTTB) 적용해 1만 미만 유지 → SVG 유지
- 다운샘플 어려우면 `renderer="canvas"` 명시 + 본 표에 등록

### 2.3 페이지별 검증 결과 (2026-04-28)

| 페이지 | 결과 |
|---|---|
| `/monitoring/alarm-calendar` | SVG 2 (히트맵 + 라인) |
| `/monitoring/reservoir` | SVG 1 (24h 수위) |
| `/monitoring/pressure` | SVG 1 (24h 1차/2차 압력) |
| `/monitoring/flow` | Canvas (TopologyGraph) |
| `/monitoring/equipment-health` | EChartWrapper 미사용 (KPI·lucide) |
| `/monitoring/leak-alerts`/`booster`/`block` | 데이터·트렌드 미설정 시 페이지 정상 |
| 채팅 시각화 (`PlotChart`/`AnomalyScanView` 등) | SVG (default 따라감) |

---

## 3. 부드러운 애니메이션 디폴트

`EChartWrapper` 가 모든 옵션에 자동 병합 (사용자 옵션이 우선):

```ts
const SMOOTH_ANIMATION_DEFAULTS = {
  animation: true,
  animationDuration: 600,
  animationEasing: "cubicOut",
  animationDurationUpdate: 400,
  animationEasingUpdate: "cubicInOut",
};
```

근거: ECharts 기본은 `expoOut`/1000ms 라 다소 튀는 느낌 → `cubicOut`/600ms 가
대시보드 데이터 갱신 빈도(분 단위)에 더 자연스러움.

페이지별로 더 빠른 응답이 필요하면 `option.animationDuration` 으로 override.

---

## 4. SVG 모드 전용 CSS

`.echart-svg-mode` 래퍼 클래스가 wrapper 에 자동 부여 (`renderer="svg"` 시).

`globals.css` 의 selector 한정 규칙:

```css
.echart-svg-mode svg path[stroke],
.echart-svg-mode svg circle,
.echart-svg-mode svg rect[fill] {
  transition:
    stroke-width 200ms cubic-bezier(0.4, 0, 0.2, 1),
    opacity 200ms cubic-bezier(0.4, 0, 0.2, 1),
    fill 200ms cubic-bezier(0.4, 0, 0.2, 1),
    stroke 200ms cubic-bezier(0.4, 0, 0.2, 1);
}
.echart-svg-mode svg path:hover {
  filter: brightness(1.15) drop-shadow(0 1px 2px rgba(0,0,0,0.18));
}
.echart-svg-mode svg rect[fill]:hover {
  opacity: 0.92;
  filter: brightness(1.12);
}
```

원칙:
- **selector 는 항상 `.echart-svg-mode svg ...` 한정**.  Canvas 차트·전역 SVG
  (lucide 아이콘) 에 영향이 가지 않도록.
- 전역 트랜지션·필터는 추가하지 않음.

---

## 5. EChartWrapper 사용 가이드

### 5.1 기본 사용

```tsx
<EChartWrapper option={chartOption} height={320} />
// → renderer="svg" (default), 부드러운 애니메이션 디폴트 자동 적용
```

### 5.2 명시적 canvas

```tsx
<EChartWrapper option={chartOption} height={400} renderer="canvas" />
```

### 5.3 ref 핸들 (ECharts 인스턴스 접근)

```tsx
const chartRef = useRef<EChartHandle>(null);
// chartRef.current?.getInstance() → ECharts native API
```

---

## 6. 빌드·호환성 주의

- 본 wrapper 의 `SMOOTH_ANIMATION_DEFAULTS` 는 **단순 객체 + `as const`** 로 정의.
  과거 `Partial<EChartsOption>` generic 으로 정의 시 swc(.tsx) 파서가
  `Expected '</', got '<eof>'` 에러 발생 — 회피.
- ECharts SVG renderer 는 `echarts/renderers` 의 `SVGRenderer` 가 자동 번들됨.
  추가 import 불필요 (`opts={{ renderer: "svg" }}` 만으로 동작).

---

## 7. 향후 확장

- `<EChartWrapper variant="mini">` 등 사이즈/여백 프리셋 (P2)
- 색 팔레트 자동 주입 (Tailwind theme color → ECharts color)
- 트렌드 데이터 LTTB 다운샘플 헬퍼 (P2)

---

## 8. 변경 이력

- 2026-04-28 — v1 초안. default svg + smooth animation + alarm-calendar/
  reservoir/pressure 검증. TopologyGraph 만 canvas 명시 유지.

## 이중 렌더(재초기화) 방지 (2026-07-17)

첫 로드 시 "그려졌다가 다시 그려지는" 증상의 두 가지 원인과 규칙:

1. **테마 확정 전 초기화 금지** — `EChartWrapper` 는 next-themes
   `resolvedTheme` 이 undefined(hydration 직전)인 동안 차트를 만들지 않고
   1프레임 대기한다. 테마는 ECharts **init-time 파라미터**라 나중에 바뀌면
   전체 재초기화가 일어남. 새 차트 컴포넌트는 반드시 EChartWrapper 경유.
2. **옵션에 들어가는 파생 상태는 effect 가 아니라 렌더 중 계산** —
   effect 로 결정하면 "데이터 렌더 → effect → 옵션 변경 → notMerge 전체
   리드로우" 2차 패스가 생긴다 (TrendChart 활성 태그 사례). `useMemo` 파생.
3. **같은 조건 백그라운드 재조회는 무음 갱신** (2026-07-19 추가) —
   전역 store(zustand)가 데이터를 유지하는 화면은 SPA 재진입 시 캐시로
   즉시 그려진 뒤 마운트 자동 재조회가 도착하며 notMerge 리드로우 +
   애니메이션 재시작이 보인다 (/trend 사례). store 가 "직전과 동일한
   태그·기간의 재조회"를 판별해 `silentUpdate` 를 세우고, 차트는 해당
   갱신에 `animation:false` 를 적용한다 (trend-store → TrendChart
   `silentUpdate` prop). 사용자 조작(태그·기간 변경)은 플래그를 해제해
   정상 애니메이션 유지.
4. **자동 갱신 훅의 즉시 실행 금지 (초기 로드가 별도 경로일 때)** —
   `useAutoRefresh` 류가 enabled 전환 시 즉시 1회 실행하면 초기 로드와
   **이중 로드 레이스** (가드 기준시각이 아직 null). 첫 차트 직후 두 번째
   응답이 데이터를 교체하며 갱신처럼 보임. `immediate:false` +
   isLoading/isRefreshing 재진입 가드 (E-039④, 배수지 모니터링).
5. **같은 조회 조건은 최초 1회만 애니메이션** — 재진입 마운트가 캐시
   데이터로 애니메이션을 다시 시작하고, 직후 백그라운드 갱신이 그것을
   끊는 잔여 경로 (silentUpdate 는 fetch 후에만 세워져 마운트 렌더엔
   못 미침). TrendChart `animationKey`(조회 조건 키) — 모듈 싱글턴
   Set 으로 키당 1회만 애니메이션, SPA 라우팅 간 유지 (E-039⑤).

### 진단 체크리스트 — "차트가 그려졌다 다시 그려짐" 신고 시

빈발 증상이므로 순서대로 배제할 것:
1. 테마 확정 전 초기화? (EChartWrapper 미경유 차트)
2. 옵션 파생 상태를 effect 로 계산? (렌더 중 useMemo 로)
3. 전역 store 캐시 + 마운트 자동 재조회? (silentUpdate / animationKey)
4. 자동 갱신 훅 즉시 실행이 초기 로드와 중복? (immediate:false + 가드)
5. fetch 로그로 이중 호출 실측 (window.fetch 패치로 /trend/data 타임라인)
   — DOM 쪽은 MutationObserver 타임라인 (E-039⑧ 실측: 등장 1초 뒤
   202건 버스트 = setOption 리드로우 서명. 수정 후 8건)
6. **같은 패턴을 쓰는 형제 컴포넌트까지 시그니처 검색으로 일괄 수정**
   — ② 를 TrendChart 만 고치고 PlotChart(채팅 카드)를 놓쳐 1년 뒤
   같은 신고가 재발했다 [E-039⑧]. `selectWorstTag`·`setActiveTagId` 처럼
   결함 패턴의 시그니처로 전 코드 grep 후 함께 고칠 것.
— 신규 차트 화면은 위 1~4 를 처음부터 적용해 재발 자체를 차단.
— 적용 완료 컴포넌트: TrendChart(②③⑤) · MonitoringTrendBlock(⑦) ·
  **PlotChart(⑧ 2026-07-28)** — 셋 다 "기본값은 렌더 중 파생, 사용자
  선택만 state" 규칙을 따른다.

## category 축 시간 비례 규칙 [E-059] (2026-07-29)

ECharts category 축은 **포인트당 등폭**이라, 시간 간격이 다른 시리즈를
이어붙이면 시간 축이 왜곡된다 (매방리 압력 실사: 5분 버킷 관측 2,016점 뒤에
30분 간격 전망 48점 직결 → 24h 전망이 정상 폭 12.5% 대신 2.3%로 압축,
파형이 "빠른 지글거림"으로 보임. 수치는 전부 정상이라 데이터 검사로는 안
잡히는 유형).

- **규칙**: category 축에 간격이 다른 시리즈를 연결할 때는 **기존 그리드
  간격으로 선형 재보간 후 연결** — `comparison-overlay.ts`
  `resampleForecastToBucket` 가 기준 구현 (전망 + 불확실성 밴드 동시 재보간)
- 장기 창(버킷 > 시리즈 간격)에서는 반대로 과신장 왜곡 — 같은 재보간이
  양방향을 해소한다
- **검증법**: 스크린샷 육안 대신 **SVG path bbox 폭 / 전체 폭** 실측이
  정량적 (수정 전 2.3% → 후 9.2% ≈ 플롯의 1/8 이론값)
