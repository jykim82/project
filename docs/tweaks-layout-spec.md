# Tweaks 패널 + 레이아웃 분기 사양

**작성:** 2026-04-18
**상태:** P1 완료 / 2026-07 DB site-level 이관

## 1. 목적

**테마·브랜드 컬러·레이아웃**을 선택하는 공용 설정 패널.

**저장 방식 (2026-07 변경):** 조직 공통 브랜딩을 위해 **DB(`tb_comm_code`
SITE_SETTING/TWEAKS_BRAND_COLOR·TWEAKS_LAYOUT_MODE·TWEAKS_DEFAULT_THEME, comm_val)
가 단일 소스**. `/admin/site-settings` GET/PUT 로 조회·저장(화이트리스트 검증).
`useTweaksApplier`(대시보드 헤더)가 localStorage 즉시 적용(플래시 방지) 후 DB 값으로
덮어써 적용 → **브라우저·기기·서버 재시작 무관**하게 조직 기본 브랜딩 적용.
localStorage 는 per-browser 캐시로 강등. (이전: localStorage 단독, 기기별 독립.)

## 2. Tweaks 패널

### 2.1 위치
- 우측 상단 헤더 — 기존 "테마 토글" 버튼 옆
- `SlidersHorizontal` 아이콘 클릭 → `Sheet` 우측 슬라이드 패널 오픈

### 2.2 설정 항목

| 구분 | 선택지 |
|---|---|
| **테마** | Light / Dark / System (next-themes) |
| **브랜드 컬러** | 오렌지(기본) / 앰버 / 블루 / 시안 / 틸 / 에메랄드 / 인디고 / 바이올렛 / 핑크 / 로즈 / 슬레이트 — 10종 |
| **레이아웃** | Sidebar / Topbar |

### 2.3 브랜드 컬러 구현
- `BRAND_COLORS` 배열에 각 색상의 `light`/`dark` CSS 변수 매핑 정의
- 적용 대상 변수: `--primary`, `--ring`, `--sidebar-primary`, `--sidebar-ring`, `--accent-foreground`, `--sidebar-accent-foreground`
- `buildColor(lightOK, darkOK)` 헬퍼로 반복 제거
- **런타임 덮어쓰기**: `document.documentElement.style.setProperty()` — 새로고침 불필요

### 2.4 localStorage
- 키: `slm-tweaks`
- 값: `{ brandColorId: string, layoutMode: "sidebar" | "topbar" }`
- MutationObserver로 `data-layout` 변경 실시간 감지 → DashboardShell 리렌더

## 3. 레이아웃 분기

### 3.1 DashboardShell
- `(dashboard)/layout.tsx`를 server component로 유지 + `DashboardShell` 클라이언트 래퍼
- `localStorage.slm-tweaks.layoutMode` 읽어 sidebar / topbar 분기
- `data-layout` 속성 변경 시 MutationObserver로 즉시 반영

### 3.2 AppSidebar (기본)
- 기존 레이아웃 유지
- hover: `bg-primary/5` + `text-primary`
- active (단일/서브): `bg-primary/10` + `text-primary` + `font-medium`
- active (상위 드롭다운): `bg-primary/[0.08]` + `text-primary` + `font-medium`
- 이전 진한 강조(`bg-primary 100%`)에서 subtle 톤으로 완화 (Claude 디자인 패턴)

### 3.3 AppTopbar (신규)
- 로고 (SLM 물관리) + 가로 메뉴 + 우측 유틸
- 하위 메뉴는 `DropdownMenu`로 토글 (클릭 펼침)
- 동일 `useSidebarMenus` 훅 공유 → 권한/숨김 처리 일관
- 활성 상태 스타일: 사이드바와 동일 (`bg-primary/10` + `text-primary`)

## 4. 관련 파일

| 파일 | 역할 |
|---|---|
| `components/tweaks/TweaksPanel.tsx` | 설정 Sheet + CSS 변수 적용 + localStorage persist |
| `components/layout/DashboardShell.tsx` | sidebar/topbar 분기 렌더 클라이언트 wrapper |
| `components/layout/AppSidebar.tsx` | 기존 사이드바 (hover/active 스타일 개선) |
| `components/layout/AppTopbar.tsx` | 신규 상단 가로 메뉴 |
| `components/layout/AppHeader.tsx` | 사이드바 모드 상단 헤더 (TweaksPanel 포함) |
| `app/(dashboard)/layout.tsx` | Server component wrapper |

## 5. 커밋 이력

- `slm-dashboard@bca37cc` — Tweaks 패널 초안 (6색)
- `slm-dashboard@4d3c949` — 사이드바 hover/active 스타일 개선
- `slm-dashboard@2060559` — 선택 배경 투명도 완화 (subtle 톤)
- `slm-dashboard@7a486ca` — 탑바 레이아웃 + 브랜드 컬러 11종
- `slm-dashboard@2ca47f6` — 오렌지 중복 제거 (최종 10종)

## 6. 후속 수정 (2026-04-18)

- **네트워크 페이지 상단 KPI 패널 폭 정합**: `/network/page.tsx` 요약 카드 grid에서 `2xl:grid-cols-5` 제거 → 4개 카드가 항상 하단 토폴로지 카드와 동일 폭을 채우도록 수정. 2xl 해상도에서 4/5 너비로 줄어 불일치하던 이슈 해소.
- **브레드크럼 라벨 누락 보완**: `components/layout/DynamicBreadcrumb.tsx` LABELS에 `gis: "GIS 관망도"`, `equipment-health: "설비 장애 통계"` 추가. 세그먼트 그대로 노출되어 소문자 `gis`로 보이던 이슈 해결.
- **DashboardShell hydration 일치화**: `mounted=false` 스켈레톤과 `layout==="sidebar"` 분기를 통합해 SSR/client 첫 렌더가 완전히 동일한 트리를 만들도록 조정. 이전엔 `<main>` 내부 `<div>` 래퍼 유무가 달라 `Hydration failed ... <main> vs <Suspense>` 경고 발생. topbar는 mounted=true 분기로 분리, localStorage 선호는 hydration 이후 useEffect가 반영.

## 7. 향후 개선 (검토)

- 탑바에서 현재 페이지 breadcrumb 가시성
- 레이아웃 전환 시 부드러운 트랜지션 (opacity fade)
- 브랜드 컬러 커스텀 (색 선택기 추가)
