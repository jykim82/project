# MetricTrendPanel 사양

## 목적
용수흐름 / GIS 관망도 / 기타 운영 화면에서 오브젝트(시설) 클릭 시 **유량(Q) · 수위(H) · 압력(P)** 3종 실시간 + 24h 트렌드를 **동일한 시각 언어**로 표출하는 공용 컴포넌트. `trend_panels.html` 레퍼런스 기반.

## 위치
- 컴포넌트: `slm-dashboard/src/components/monitoring/MetricTrendPanel.tsx`
- 애니메이션: `slm-dashboard/src/app/globals.css` (keyframes 10종)
- 소비자:
  - `components/monitoring/FlowNodeTrendPanel.tsx` — `/monitoring/flow` 우측 인스펙터
  - `components/gis/GisDetailPanel.tsx` — `/monitoring/gis` 우측 인스펙터
  - (확장 가능) 다른 시설 상세 뷰

## Props
```ts
type MetricVariant = "flow" | "level" | "pressure";

interface Props {
  variant: MetricVariant;
  label: string;              // "유량" / "수위" / "압력" (한글 권장)
  code: "Q" | "H" | "P";
  current: number | null;     // 현재값
  unit: string;               // "m³/h" / "m" / "kgf"
  values: (number | null)[];  // 24h 시계열 (null 자동 필터)
  timeRange?: string;         // 기본 "LAST 24H"
  decimals?: number;          // 기본 flow=1, level=2, pressure=2
}
```

## 디자인 토큰
| variant  | border              | code tag            | delta text       | end color |
|----------|---------------------|---------------------|------------------|-----------|
| flow     | sky-500/18%         | sky-300 bg + border | sky-400          | #38bdf8   |
| level    | emerald-500/18%     | emerald-300         | emerald-400      | #34d399   |
| pressure | amber-500/18%       | amber-300           | amber-400        | #fbbf24   |

배경: `bg-gradient-to-b from-[#0b1420] to-[#0f1823]` (다크 전용 강조. 라이트 모드는 body/parent 컨테이너에서 자연스럽게 뒤덮인다.)

## 레이아웃 (상→하)
1. **상단 글로우 라인** — variant 컬러 수평 그라데이션 (1px)
2. **헤더** (flex items-start justify-between)
   - 좌: 라벨(text-[13px]) + 코드 뱃지(Q/H/P, text-[10px] mono)
   - 우: 값(text-[24px] light tabular-nums) + 단위(text-[11px]) + delta(mono)
3. **중앙 SVG** — 380×95 viewBox, variant별 차트
4. **푸터** — MIN/AVG/MAX (mono, tracking-wider) + `LAST 24H` (mono, right)

## variant별 SVG

### Flow (Q)
- area fill (top-down gradient sky-300 → transparent)
- static underlay line (sky-700 opacity 0.5)
- **dash overlay** (sky-200, strokeDasharray "6,4", `animation: metric-dash-flow 4s linear infinite` → 우→좌 흐름)
- 끝점 crosshair: vertical + horizontal dashed lines
- 끝점 2중 pulse ring (`metric-ring-pulse`, `metric-ring-pulse-2`)
- 끝점 solid dot

### Level (H)
- MAX/MIN 가로 점선 (slate-600)
- area fill (emerald-400 gradient)
- clipPath로 area 내부에 `metric-level-shine` rect (좌→우 반짝임, 4s)
- smooth line (emerald-400)
- 현재값 가로 indicator 점선 (`metric-level-glow` 2s breath)
- 물방울 3개 (`metric-droplet` 2.8s, delay 0/1.1/2s)
- 끝점 solid + halo

### Pressure (P)
- area fill (amber-400 gradient)
- thick line (amber-400 stroke-2)
- highlight line (amber-100 stroke-0.8)
- 끝점 정적 pulse (`metric-end-dot`, r 8↔11)
- 끝점 solid (amber-100)
- **주의: 흐르는 wave 애니메이션 없음** — 2026-04-18 사용자 요청으로 제거 (정적만 남김)

## 애니메이션 (globals.css)

| 클래스                   | duration | 설명                                      |
|--------------------------|----------|-------------------------------------------|
| `metric-dash-flow`       | **4s**   | dashoffset -30 (유량 흐름, 느리게)        |
| `metric-ring-pulse`      | 1.8s     | r 4→14 + opacity 0.8→0 (끝점 링)          |
| `metric-ring-pulse-2`    | 1.8s (+0.9s delay) | 위와 엇갈림                      |
| `metric-level-shine`     | 4s       | translateX -100→420 (수면 반짝)           |
| `metric-level-glow`      | 2s       | opacity 0.4↔0.9 (수위 indicator breath)   |
| `metric-droplet`         | 2.8s     | translateY 0→-18, opacity 0→0.6→0         |
| `metric-end-dot`         | 1.5s     | r 8↔11, opacity 0.35↔0.15 (끝점 정적)     |
| `metric-status-blink`    | 1.6s     | opacity 1↔0.4 (현재 미사용 — LIVE 제거)   |
| `metric-pressure-wave`   | 1.8s     | **현재 미사용** (압력 흐름 제거, keyframes만 남김) |
| `metric-pressure-pipe`   | 2.4s     | stroke-width 9↔10.5 (현재 미사용)         |

## 데이터 요구사항
- 소비 컴포넌트는 `fetchTrendData({ tag_ids, from_ts, to_ts, max_points: 48 })` 로 24h × 48pt 취득 후 `result.series[tagsn]` 을 `values` 로 전달
- `current` 는 `metrics.{flow|level|pressure}.value` 에서 직접 공급 (실시간 최신값, trend 시계열 끝값과 소폭 차이 허용)
- `tagsn` 기준 매칭 — variant 배정은 소비 컴포넌트에서 수행 (`metrics.flow` → Q, `metrics.level` → H, `metrics.pressure` → P)

## 배수지 zone 지원
- FlowNodeTrendPanel은 `node.level_zones[]` 가 있으면 zone 탭 UI 제공 → 선택 시 `label = "수위 · {zone명}"` 으로 전달
- zone이 1개뿐이거나 없으면 `label = "수위"` 고정

## 설계 원칙
- **한글 라벨** — UI 한글 원칙(CLAUDE.md) 준수. variant 코드(Q/H/P) 만 영문 유지 (물리량 기호)
- **LIVE 뱃지 없음** — 사용자 요청으로 제거. 대신 dash/ring/droplet 애니메이션 자체가 "live" 시각 단서 역할
- **variant 애니메이션 차별화** — flow는 흐름 방향, level은 수면, pressure는 정적. 같은 시계열 데이터라도 물리량 특성이 시각적으로 드러나도록
- **SVG 통일** — 기존 Canvas 기반 `SparklineChart` 2곳(Flow/GIS)을 단일 SVG 컴포넌트로 흡수해 중복 제거
- **라이트 모드 대응** — 그라데이션 어두운 카드는 어둡게 유지(emphasis), 테마 토글은 외부 컨테이너 배경에 맡김

## 변경 이력
- 2026-04-18 초안 구현 (variant 3종, SVG, 애니메이션 10종)
- 2026-04-18 LIVE 뱃지 제거, 라벨 한글화
- 2026-04-18 압력 wave 애니메이션 제거
- 2026-04-18 유량 dash 흐름 1.5s → 4s 완화
