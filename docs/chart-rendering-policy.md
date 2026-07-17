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
