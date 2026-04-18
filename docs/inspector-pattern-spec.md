# SLM Inspector Pattern — 오브젝트 상세 우측 고정

**작성:** 2026-04-18
**상태:** P1 완료

## 1. 목적

대시보드 내 "오브젝트 클릭 → 상세 정보 표출" 화면을 **우측 사이드 인스펙터** 방식으로 통일.
기존 하단(슬라이드업/테이블) 스타일이 혼재하던 페이지들을 `/network` 페이지의 `NodeDetailPanel` 패턴과 일치시킨다.

## 2. 패턴 정의

### 2.1 공통 구조

```
<Card>
  <CardContent>
    <div className="flex gap-2">
      <div className={selected ? "flex-1 min-w-0" : "w-full"}>
        { /* 주 시각화 영역 (그래프/지도/다이어그램) */ }
      </div>
      {selected && (
        <div className="hidden sm:block w-[320px] lg:w-[360px] flex-shrink-0
                        overflow-y-auto border-l border-border pl-3"
             style={{ height: 550 }}>
          { /* 인스펙터 컴포넌트 */ }
        </div>
      )}
    </div>
  </CardContent>
</Card>
```

### 2.2 치수 가이드

| 항목 | 값 |
|---|---|
| 기본 너비 | `w-[320px]` (sm~), `lg:w-[360px]` (lg+) |
| GIS 등 맵 컨텍스트 | `w-[360px]`, `lg:w-[400px]` |
| 높이 | 시각화 영역과 동일 (예: 550px) 또는 `h-full` |
| 구분선 | `border-l border-border pl-3` (좌측 경계선 + 패딩) |
| 모바일 | `hidden sm:block` → 모바일은 기존 bottom 슬라이드업 유지 |

### 2.3 적용 기준

- 데스크톱에서 `flex` 레이아웃으로 좌/우 분할 (오버레이 X)
- 선택 해제 시 주 시각화 영역이 자동으로 `w-full`로 복귀
- 인스펙터 내부 스크롤(overflow-y-auto) — 주 영역은 스크롤 안 함

## 3. 적용 페이지 (2026-04-18 완료)

| 페이지 | 이전 | 이후 |
|---|---|---|
| `/network` | 우측 `NodeDetailPanel` (기준 패턴) | 유지 |
| `/monitoring/flow` | `fixed bottom-0 right-0 translate-y` 슬라이드업 | 카드 내부 우측 인스펙터 (데스크톱), 모바일만 기존 슬라이드업 |
| `/monitoring/gis` | `GisDetailPanel` = `fixed bottom-0 …` + 박스셀렉트 `border-t` 테이블 | 지도 옆 `<aside w-[360px]>`에 시설 상세 or 박스셀렉트 결과 |

## 4. 컴포넌트별 변경 사항

### 4.1 FlowNodeTrendPanel
- 호출 시 `isMobile` prop을 강제로 `true` 설정 → 스파크라인 단일 컬럼 세로 스택 (좁은 폭 대응)
- 기존 컴포넌트 구조/로직 무변경

### 4.2 GisDetailPanel
- 외부 래퍼: `fixed bottom-0 left-[var(--sidebar-width)]` → `flex h-full w-full flex-col bg-card/95 backdrop-blur-sm`
- 내부 섹션 그리드: `grid-cols-4` → `grid-cols-1` (세로 스택)

### 4.3 박스셀렉트 결과 테이블 (/monitoring/gis)
- 컬럼 수: 8개 표시 → 4개 (좁아진 우측 패널 폭 대응, 가로 스크롤 회피)

## 5. 커밋 이력

- `slm-dashboard@73b439a` — 브레드크럼 gis 라벨 + 네트워크 KPI 폭 정합
- `slm-dashboard@<새 커밋>` — Inspector Pattern 적용 (flow/gis 상세 우측 이동)

## 6. 향후 개선 (검토)

- 모바일 우측 인스펙터 전환 (현재 bottom 슬라이드업 유지) — 화면 폭 임계점 재검토
- Inspector 컴포넌트 공통 래퍼 추출 (`<InspectorPanel onClose={}>`)
- 인스펙터 상태 URL 쿼리 연동 (?selected=...)
