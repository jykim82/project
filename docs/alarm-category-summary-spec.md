# 분류별 경보 현황 — 수평 막대 + 스파크라인 + KPI 레이아웃

**작성:** 2026-04-18
**상태:** UI 개편 완료, 데이터 확장 대기

## 1. 목적

경보관리 > 현황 탭 상단 `AlarmCategorySummary`의 "분류별 경보 현황" 카드를 도넛 중심에서 **정보 밀도 높은 수평 막대 + 스파크라인 + 하단 KPI** 구조로 개편.

참조: Claude 디자인 "SLM Alert Analytics". 기존 도넛은 총수/상위 카테고리는 알려주지만 각 카테고리의 추세와 상대 비중 파악에 약했음.

## 2. 레이아웃

```
┌─ 분류별 경보 현황 [추천]       [1h][6h][오늘][주] ─┐
│                                                    │
│ ● 수위      ▅▅▅▅▅▅▅▅▅▅▅▅▅▅         17   33%   ▫▅▆▇▇▅  ▲ 2 어제
│ ● 네트워크  ▅▅▅▅▅▅▅▅▅▅▅            15   29%   ▅▆▅▅▅▆  ▼ 1 어제
│ ● UPS       ▅▅▅▅▅▅▅▅               12   23%   ▫▫▅▆▅▆  ▲ 3 어제
│ ● 압력      ▅▅                       4    8%   ▫▫▅▫▫▫  – 0 어제
│ ...                                                │
│ ──────────────────────────────────────────────    │
│  총 경보    긴급     미확인    해결                │
│   52건     8건·15%  23건·44%  29건·56%            │
└────────────────────────────────────────────────────┘
```

## 3. 구성 요소

### 3.1 헤더
- 타이틀 "분류별 경보 현황" + `[추천]` 뱃지 (secondary variant, 10px)
- 우측 시간 범위 탭: `1h / 6h / 오늘 / 주` — 현재 선택만 `bg-background shadow-sm`

### 3.2 카테고리 행 — `CategoryRow`
5-컬럼 grid (`grid-cols-[90px_1fr_36px_40px_72px]`):
1. **이름:** 색 도트(`size-2 rounded-full`) + 카테고리명
2. **막대:** `h-6 bg-muted/30` 바탕에 `width: {pct}%` 컬러 바 (transition 500ms)
3. **건수:** 우측 정렬 bold `text-base tabular-nums`
4. **퍼센트:** muted, `Math.round(pct)%`
5. **스파크라인:** SVG 8-bucket 미니 바차트 + 어제 대비 델타 뱃지

정렬: count 내림차순.

### 3.3 카테고리 색상 매핑
```ts
const CATEGORY_COLORS = {
  수위: "#60a5fa",       // blue-400
  네트워크: "#f59e0b",   // amber-500
  UPS: "#34d399",        // emerald-400
  압력: "#a78bfa",       // violet-400
  정보: "#f472b6",       // pink-400
  유량: "#22d3ee",       // cyan-400
  교차검증: "#fb7185",   // rose-400
  밸브: "#facc15",       // yellow-400
  전원: "#fb923c",       // orange-400
};
// fallback: slate/zinc/neutral 순환
```

### 3.4 스파크라인
SVG rects. `v === 0`이면 `opacity 0.15`, 그 외 `0.9`. 최대값 정규화. width = 8 × (6+3)px.

### 3.5 어제 대비 델타
- `delta > 0`: `▲ {n}` rose-400
- `delta < 0`: `▼ {|n|}` emerald-400
- `delta === 0`: `– 0`
- `delta === undefined`: `–` (데이터 미제공)

### 3.6 하단 KPI 스트립
4개 셀 (`grid-cols-4`), 상단 `border-t pt-3`:
- **총 경보** — total, neutral tone, sub = 선택 시간 범위
- **긴급** — `summary.criticalCount`, critical(rose-400), sub = 총 대비 %
- **미확인** — `reports.filter(r => r.alarm_confirm_yn !== 'Y').length`, warn(amber-400), sub = 총 대비 %
- **해결** — `reports.filter(r => r.alarm_status === '알람해제').length`, ok(emerald-400), sub = (resolved / (ongoing+resolved)) %

## 4. Props

```ts
interface AlarmCategorySummaryProps {
  summary: AlarmDashboardSummary;
  reports?: AlarmReportRecord[];           // 미확인/해결 계산용
  hourlyByCategory?: Record<string, number[]>;  // 스파크라인 데이터
  yesterdayDelta?: Record<string, number>;      // 어제 대비 델타
  onRangeChange?: (range: TimeRange) => void;   // 시간 범위 변경 훅
}
```

**호환성:** 기존 `summary`만 전달해도 동작 (스파크라인/델타는 placeholder, 미확인/해결은 0).

## 5. 시설별 경보 현황 (우측 카드)

기존 테이블 + 페이징(`PAGE_SIZE=8`) 유지. 이번 개편 대상 아님.

## 6. 백엔드 연결 (2026-04-18 완료)

- `GET /crisis/alarm-dashboard?range={1h|6h|today|week}` — range 쿼리 파라미터 수용
- 응답 확장: `range` / `rangeStart` / `rangeEnd` / `rangeCategorySummary` / `rangeStats` / `hourlyByCategory` / `yesterdayDelta`
- 기존 `totalOngoing` 등 '진행중 전체' 필드는 하위호환 유지 (top `AlarmDashboardCards`에서 사용)
- `_resolve_range_window(range)`: 1h/6h는 trailing, today는 00:00~지금, week는 7일 trailing. 어제 대비는 동일 폭을 1일(주: 7일) shift
- `_bucket_category_counts`: `FLOOR(EXTRACT(EPOCH FROM (alarm_start_time - start)) / bucket_secs)` + `LEAST/GREATEST`로 [0, 7] clip하여 8개 버킷으로 강제 정규화
- 프런트: `refreshDashboard(nextRange?)`에 range 인자 추가, AlarmCategorySummary는 controlled prop + summary에서 rangeCategorySummary/rangeStats/hourlyByCategory/yesterdayDelta 직접 참조
- 검증: 1h/6h/today/week 모두 count + hourly 버킷 8개 + delta 정상 반환 확인

## 7. 후속 작업 (TODO)

- **카테고리 행 클릭:** 해당 카테고리로 이력 탭 필터링 딥링크
- **연관 시설 하이라이트:** 행 hover 시 시설별 경보 현황 테이블에 해당 카테고리 관련 현장 강조

## 7. 관련 파일

- `src/components/crisis/AlarmCategorySummary.tsx` — 개편 대상 컴포넌트
- `src/app/(dashboard)/crisis/alarm-dashboard/page.tsx` — `reports={ongoingReports}` prop 추가
- `src/lib/types/crisis.ts` — `AlarmDashboardSummary` 타입 (향후 확장 지점)

## 8. 커밋

- `slm-dashboard@e804be7` — 수평 막대 + 스파크라인 + KPI 개편 (UI)
- `slm-dashboard@db3e11d` — Turbopack 파싱 캐시 회피 (멀티라인 template literal 제거)
- `slm@437d10b` — 백엔드 range/hourly/delta 구현
- `slm-dashboard@d81c82c` — 프런트 range 상태·api·store·page·컴포넌트 연결
