# 용수흐름도 "다이어그램 모드" 추가 구현 사양 (Draft)

**작성:** 2026-04-15 (초안), 2026-04-16 (B안 구현 완료 + 레이아웃 3차 개선)
**상태:** B안 구현 완료 — 세로 병렬 레이아웃
**관련:** `docs/review-items.md`, `docs/work-history.md`

## 1. 요구사항 (사용자 원문)

> 용수흐름도에 네모 다이어그램형태의 모드도 추가. `http://59.14.182.46:8080/realtimeDashBrd` 여기서 admin/admin 로그인 후 용수공급계통도 메뉴 → 줌을 가장 작게 점점 키워 보면 GIS관망과 같이 네모 박스가 레이어별로 확대되면서 정보가 상세해지는걸 확인 가능. 다이어그램 형태의 모드를 구현하되 GIS관망도 메뉴와 외부 웹페이지를 참고해 다이어그램 형태의 모드 추가 구현 사양을 검토해줘.

## 2. 외부 레퍼런스 조사 결과 (C-Water NAVI `/advancedDiagram`)

### 2.1 기술 스택

| 항목 | 사용 기술 |
|---|---|
| Base map | Kakao Maps SDK + OpenLayers ol-layer 1개 |
| Feature 서빙 | GeoServer WFS (`geoserver/cwaternavi/wfs?TYPENAME=DSTD_PT_M`) |
| 좌표계 | **Web Mercator (EPSG:3857)** — 지리 좌표 위에 스키마틱 박스 오버레이 |
| 렌더링 | Canvas (OL이 vector feature를 canvas로 그림) |
| 실시간 값 | `/api/diagram/tag/msrmdatas` 주기 폴링 |

### 2.2 줌 레벨별 LOD (Level-Of-Detail) 관찰

| Zoom Level | 표시되는 feature | 실측 |
|---|---|---|
| **10.x** (최외곽) | 정수장 4개(`반월(정)`, `연성(정)`, `시흥(정)`, `안산(정)`) + 상위 배수지 ~15개 | 19개 labels |
| **11.0** (중간) | 상위 배수지 + 중간 그룹(`선부1`, `선부2`, `목내`, `선부`…) | ~25 labels |
| **12.0** (상세) | 개별 말단 사이트(`연성선부01~08`, `연성목내28`, `반월고잔38`…) | 109+ labels |

**핵심 패턴:** 줌 인할수록 상위 그룹 박스가 "폭발"해서 하위 개별 노드로 분해됨. GIS 관망도와 동일한 LOD 패러다임이지만 **논리적 상하류 관계**를 보존.

### 2.3 좌측 메뉴 UX
- **"표시설정"** 토글 패널으로 범례(정수장/유량계/배수지/물방향/타이틀) 레이어별 on/off
- **"계측시간"** 표시 + 실시간 메트릭 갱신
- **"줌 Lv"** 표시 (현재 배율 숫자 노출)

## 3. 현재 SLM 시스템 상태

### 3.1 용수 흐름도 (`/setup/flow-map`)
- **`FlowDiagramGraph.tsx`** — 커스텀 SVG Sankey (Sugiyama layout)
- 데이터: `tb_facility_flow_map` (upstream→downstream 단방향 엣지)
- API: `/flow-map`, `/flow-map/roots`, `/flow-map/downstream`, `/flow-map/import-export`
- 좌표: **논리 좌표** (`computeSankeyLayout()`이 depth 기반 자동 배치)
- 한 화면 전체 fit-to-view, LOD 없음
- 노드 클릭 → 상/하류 경로 하이라이트
- **파이프 흐름 애니 (역동적 UX, 2026-07-09)**: 각 링크에 `.s-flow` 오버레이
  path(stroke-dashoffset 애니, source→target 방향)로 물 흐름 시각화. motion-safe —
  `useMotionActive()` 게이트로 감소모션/탭숨김 시 미렌더 + `@media
  (prefers-reduced-motion)` CSS 억제 이중 안전. `<style>` 주입(Tailwind v4 커스텀
  클래스 drop 회피). dimmed 링크는 흐름 숨김.

### 3.2 GIS 관망도 (`/setup/gis`)
- **`GisMap.tsx`** + MapLibreGL + PMTiles/GeoJSON
- 좌표: **실지리 좌표** (EPSG:3857 / WGS84)
- LOD 임계값: `LOD_LABEL_ZOOM=12` / `LOD_METRIC_ZOOM=14` / `LOD_SHP_INFO_ZOOM=16`
- 레이어: Pipeline 3종 + Facility 9종 + Boundary 2종

### 3.3 공통 데이터 모델
- `sitename__facilitytype` 합성 키로 양쪽 일관 사용
- 시설유형: 정수장/취수장/가압장/배수지/소블록/소소블록/블록/감압시설
- `FACILITY_STYLES` (flow) + `FACILITY_MARKER_COLORS` (gis) 색상 일치

## 4. 제안 설계

### 4.1 핵심 설계 결정

**Option A: 기존 FlowDiagramGraph 확장 (LOD 추가)**
- 장점: Sankey layout 재사용, 논리 좌표 유지, 학습곡선 낮음
- 단점: 줌 LOD · 계층 집계 · 팬 등을 SVG로 구현 필요
- 적합성: ⭐⭐⭐

**Option B: MapLibreGL 위 스키마틱 오버레이 (레퍼런스 방식)**
- 장점: 기존 GIS 인프라 재사용, 줌/팬/LOD 기본 제공, 지리 좌표와 자연스럽게 공존
- 단점: 노드 좌표를 지리 좌표로 "스키마틱"하게 재배치 필요 (= fake EPSG:3857 좌표)
- 적합성: ⭐⭐⭐⭐⭐

**Option C: React Flow 기반 신규 컴포넌트**
- 장점: 업계 표준, miniMap/zoom/pan 기본, 그룹 노드 지원
- 단점: 새 라이브러리 의존성, 기존 Sankey와 별개로 유지
- 적합성: ⭐⭐⭐⭐

**확정: Option B (MapLibreGL 재사용)** — 2026-04-16 구현 완료.

### 4.1a 구현 결과 (2026-04-16)

**레이아웃:** B안 가로 배치 (계통.001 레퍼런스 정확 재현)
- 자식을 부모와 같은 Y에 가로로 배치 (X 분산)
- MAX_PER_ROW=5 초과 시 다음 Y 행 wrap
- 자동 레이아웃 (seed_flow_diagram.py) — tb_facility_flow_map 데이터 기반, 하드코딩 아님

**엣지:** bracket 패턴 (trunk + vertical + drop 분리)
- 부모→bus_x 수평 trunk (부모당 1개)
- bus_x 수직 trunk (min_Y~max_Y, 부모당 1개)
- bus_x→자식 수평 drop (자식당 1개)
- 97 엣지 → 177 segments (40 trunk + 40 vertical + 97 drops)

**노드:** HTML Marker 3단계 (sm/md/lg)
- sm (줌<11): GIS SVG 아이콘 26px 원형 (reservoir/booster/meter/purifier/valve_pressure)
- md (줌 11~13): 아이콘 + 시설명 + 유형
- lg (줌>13): 전체 (메트릭 + 알람 뱃지)

**기능:**
- 다이어그램 모드 기본 ON
- 전체화면 지원 + 초기화면(fitView) 연동
- 계통별 선택 (selectedRoot → BFS downstream 필터)
- 레이어 토글 (라벨/메트릭/알람/엣지)
- 드래그 편집 모드 (PUT /flow-diagram/nodes/{id})
- 실시간 메트릭 60초 폴링
- 애니메이션: opacity 펄스 (LineAtlas overflow 방지)

**커밋 히스토리:**
- `slm@dcd230d` Phase 1 (데이터모델+시드+API)
- `slm-dashboard@f5c4c1c~9c4f1f6` Phase 2~3 (렌더+메트릭+하이라이트+토글+편집+전체화면+계통필터+B안레이아웃+bracket엣지+초기화면)

### 4.2 데이터 모델 확장

**신규 테이블 `tb_flow_diagram_node`:**
```sql
CREATE TABLE tb_flow_diagram_node (
  node_id         serial PRIMARY KEY,
  sitename        text NOT NULL,
  facilitytype    text NOT NULL,
  parent_node_id  int REFERENCES tb_flow_diagram_node(node_id),  -- 계층 집계
  group_level     int NOT NULL,    -- 0=root(정수장) / 1=top / 2=mid / 3=leaf
  diagram_x       double precision,  -- 스키마틱 X좌표 (EPSG:3857 fake coord)
  diagram_y       double precision,  -- 스키마틱 Y좌표
  box_width       double precision,  -- 줌 레벨별 상대 크기
  box_height      double precision,
  label_text      text,
  display_from_z  real DEFAULT 10,    -- 이 줌 이상에서만 렌더
  display_to_z    real DEFAULT 18,
  UNIQUE (sitename, facilitytype)
);

-- 기존 tb_facility_flow_map(상하류 엣지)은 그대로 재사용
-- diagram_node ↔ facility_flow_map 조인으로 엣지 렌더
```

**Initial seed 전략:**
- `computeSankeyLayout()`으로 자동 배치된 좌표를 DB에 1회 export → `diagram_x/y`로 저장
- 이후 관리자가 드래그해서 세부 조정 (GIS 마커 드래그와 동일 UX)

### 4.3 UI 토글 (`/setup/flow-map` 페이지 내)

```
┌──────────────────────────────────────────────┐
│  [Sankey 모드] [다이어그램 모드] [GIS 모드]  │ ← Tab 3개
└──────────────────────────────────────────────┘
```

- **Sankey 모드** (기본): 기존 `FlowDiagramGraph.tsx` 유지
- **다이어그램 모드** (신규): MapLibreGL + 논리 좌표 + LOD 박스
- **GIS 모드** (신규 링크): `/setup/gis`로 navigate (이미 있는 페이지 재사용)

### 4.4 다이어그램 모드 LOD 설계 (SLM 기준)

| Zoom | 표시 내용 | 그룹핑 전략 |
|---|---|---|
| `< 10` | 계통 개요: 정수장(N) → 가압장·배수지 주요 허브(N) | `group_level=0,1` |
| `10 ~ 12` | 중간 그룹: 소블록/중블록 단위 | `group_level=0,1,2` |
| `12 ~ 14` | 개별 시설 대부분 노출 | `group_level` 전체 |
| `> 14` | 메트릭(유입/유출/수위) + 태그값 실시간 표시 | + `tb_tag_raw_data` 조인 |

**구현:** MapLibreGL `layer.minzoom`/`maxzoom` + `layer.filter`로 `group_level` 기반 필터링. `display_from_z`/`display_to_z` 필드로 노드별 미세 조정 가능.

### 4.5 엣지 렌더

Sankey와 달리 Bezier 곡선 대신 **직각 엘보우(elbow)** 라인 사용 (다이어그램 전통 스타일):
```
정수장 ──┐
         ├── 배수지1 ─── 소블록A
         └── 배수지2 ─── 소블록B
```

- `GeoJSON LineString` 생성 — 상류→직각 점→하류 2~3 포인트
- `upstream_sitename + downstream_sitename` 공통 구간 묶기
- `line-width`는 `relation_type`/flow rate 기반 (P14 manual boost처럼 soft)

### 4.6 실시간 값 오버레이

기존 P9 `/vision/diagnose` 경로와 독립적이지만 **데이터 소스는 공유**:
- `tb_equipment_alarm_report` → 활성 알람 있는 시설 빨간 테두리
- `tb_tag_raw_data` → 실시간 유입·유출·수위 라벨
- 폴링 주기: 10초 (레퍼런스와 동일 수준)

### 4.7 북극성 플로우와의 통합

- 다이어그램 노드 클릭 → **VisionAdviceCard 역방향 딥링크** 재사용 (P11 구조)
  - `/chat?sitename=X&facilitytype=Y&prefill=...`로 이동
- 알람 있는 노드에 "카메라" 아이콘 오버레이 → 클릭 시 `/chat` 딥링크

## 5. 단계별 구현 계획

### Phase 1: 데이터 모델 + 시드 (2~3일)
- [ ] `tb_flow_diagram_node` 마이그레이션
- [ ] 기존 `computeSankeyLayout()` 결과를 DB로 export 스크립트 (`tools/export_flow_layout.py`)
- [ ] 관리자 CRUD API (`/admin/flow-diagram/nodes`)

### Phase 2: 렌더링 (3~4일)
- [ ] `FlowDiagramMap.tsx` 신규 컴포넌트 (MapLibreGL 재사용, base map 없이 빈 캔버스)
- [ ] LOD 레이어 정의 (`flow-diagram-layers.ts`)
- [ ] 노드 박스 + 엣지 엘보우 GeoJSON 생성 로직
- [ ] 줌/팬 제스처 + 범례 + "줌 Lv" 표시

### Phase 3: 상호작용 (2~3일)
- [ ] 노드 클릭 → 상/하류 하이라이트 (기존 `getAllPaths()` 재사용)
- [ ] 노드 클릭 → `/chat` 역방향 딥링크 (P11 재사용)
- [ ] 실시간 값 폴링 + 활성 알람 강조
- [ ] 관리자 드래그 모드 (좌표 미세 조정, GIS 드래그와 동일)

### Phase 4: 탭 전환 + 마무리 (1~2일)
- [ ] `/setup/flow-map` 페이지에 탭 토글 추가 (Sankey / 다이어그램 / GIS 링크)
- [ ] Playwright E2E: 3개 모드 전환 + LOD 동작 + 노드 클릭 + 딥링크
- [ ] 문서 업데이트 (work-history, error-management)

### 총 예상 소요: **8~12일** (1개 스프린트)

## 6. 폐쇄망 납품 고려사항

- ❌ Kakao Maps SDK 사용 **금지** (레퍼런스와 다름) — base map 없이 빈 캔버스
- ❌ GeoServer WFS 사용 **금지** — 우리는 Python 백엔드가 `tb_flow_diagram_node` → GeoJSON 직접 반환
- ✅ MapLibreGL은 이미 납품 번들에 포함 (GIS 관망도용)
- ✅ PMTiles 불필요 — 노드/엣지 수가 적어 GeoJSON 직접 로드

## 7. 해결된 이슈

### 7.1 좌표 충돌 (2026-04-16)
- **증상:** 석문2 소블록, 행정1-2 감압설비 등 30+ 노드가 다른 노드와 동일 좌표에 배치되어 줌 확대 시 도형이 2개로 겹쳐 보임
- **원인:** `seed_flow_diagram.py`의 `_place_subtree`에서 브랜치 자식 서브트리 높이 미반영
- **수정:** `slm@a72a88c`

### 7.2 레이아웃 3차 개선 — 세로 병렬 배치 (2026-04-16)
- **1차 (수평 가로):** 형제를 같은 Y에 가로 배치 → 직렬(종속) 관계로 오인. 병렬 표현 불가
- **2차 (수평 compact):** 서브트리 폭(X) 기반 가로 배치 → 같은 문제
- **3차 (세로 병렬, 최종):** 형제를 **같은 X(깊이), 다른 Y(세로 스택)** → 병렬 관계 명확
  - 부모는 첫째 자식과 같은 Y (top-aligned)
  - 각 자식에 `_subtree_height()` 만큼 Y 할당 → 충돌 0
  - X span: 0.330 (깊이 6단), Y span: 0.730 (형제 세로 나열)
- **검증:** 줌 11(md) + 줌 13(lg) 각 99 markers, 0 duplicates, 0 pixel overlaps, 7개 병렬 그룹 확인
- **커밋:** `slm@d4c5161`

### 7.3 방향 화살표 + 알갱이 애니메이션 + GIS 데이터 동기 (2026-04-16)
- **방향 인식:** canvas 생성 화살표 아이콘을 엣지에 symbol-placement=line으로 배치 (폐쇄망 대응, 외부 glyph 불필요)
- **이동 particle 애니메이션:** 엣지 LineString을 따라 circle 점이 상류→하류로 이동 (세그먼트당 2개, 120ms 주기 GeoJSON 갱신). 방향 인식 명확
- **GIS 데이터 동기:** lg 노드에 배수지 supply_time (공급가능시간/일평균유입/유출/야간최소유량) 추가. GIS 관망도 팝업과 동일 데이터 항목
- **충전 상태 표기 규칙 (2026-07-12):** 백엔드(Node-RED)는 `net = 유출률 − 유입률 ≤ 0`
  (유입 ≥ 유출)이면 `supply_time.status='CHARGING'`, `total_supply_time=24h` **고정값**으로
  저장한다. 24h 는 실제 소진 시간이 아닌 "소진 안 됨" 플레이스홀더라 저수위(≤12%) 게이지
  옆에 "24.0h" 로 뜨면 공급 여유로 오독될 수 있다. → 프런트는 CHARGING 이면 숫자 대신
  **"충전 중"** 배지로 표기(저수위면 "⚠ 수위 N% 저수위 주의" 병기). 적용 4곳: GisDetailPanel /
  GisFacilityCard(마커) / GisFacilityPopup / FlowMonitoringGraph(계통도 툴팁·오버레이).
  저수위 임계는 WaterLevelGauge 와 동일한 ≤12% 재사용(임계 지어내지 않음).
- **커밋:** `slm-dashboard@ac8b19e`

### 7.4 소블록 유량적산/압력 추가 (2026-04-17)
- **백엔드:** `_TARGET_GROUPS`에 FLOW_CUMULATIVE + PRESSURE 추가, 적산 제외 필터는 FLOW_CUMULATIVE에 미적용, flow_accum 별도 카테고리 매핑 (`slm@4b09297`)
- **프런트:** FlowDiagramNode lg + GisFacilityPopup에 유량적산(k m³) 표시 (`slm-dashboard@a147884`)
- **GIS ↔ 다이어그램 동일 데이터:** 소블록은 유량순시 + 유량적산 + 압력 3종

### 7.5 노드 클릭 flyTo 포커싱 (2026-04-17)
- **동작:** 노드 클릭 시 해당 좌표로 `flyTo(zoom 14, 800ms)` 이동, lg 상세 뷰로 전환
- **재클릭 토글:** 같은 노드 재클릭 시 deselect + flyTo 없음
- **커밋:** `slm-dashboard@0f8e4f2`
- **후속(§7.15):** reduced-motion 환경에서 flyTo 애니메이션이 생략되던 문제 해결

### 7.6 노드 간격 40% 축소 (2026-04-17)
- PARENT_X_GAP 0.055→0.035, CHILD_X_GAP 0.038→0.025, ROW_Y_GAP 0.010→0.006, TREE_Y_GAP 0.025→0.015
- X span 0.330→0.210, Y span 0.730→0.438, 충돌 0건 유지
- **커밋:** `slm@116e294`

### 7.7 라이트 모드 디자인 대응 (2026-04-17)
- 맵 배경: dark `#0b0f1a` / light `#f1f5f9` (useTheme 감지, `makeMapStyle`/`makeEdgePaint` 테마별)
- 노드 박스: 파스텔 배경(`bg-*-50/90`) + 진한 보더, 텍스트 slate-800, 아이콘 진한 컬러(*-700)
- 엣지 dim: 라이트에서 slate-400 회색 + opacity 0.2~0.3 (가시성 확보)
- 오버레이/범례/버튼: `dark:bg-black/60 bg-white/80` + 텍스트 slate-700
- 필터 하이라이트: `outline outline-[3px] outline-blue-500` (ring 속성 충돌 회피, alarm ring과 공존)
- **커밋:** `slm-dashboard@6a2614d`, `42eddd8`, `fb8b8d7`, `4cad721`, `b3dc210`

### 7.8 상단 요약 패널 + 필터 동작 (2026-04-17)
- **상단 패널:** 시설유형 범례 + 실시간 통계(유량 불균형/교차검증/알람/설비 장애/통신 이상) 카운트
- **필터 동작:** 각 통계 배지 클릭 시 해당 노드만 하이라이트(파란 outline + scale-110), 나머지는 dim
  - 노드: opacity 10~15%
  - 엣지: opacity 5~8% (dark) / 20~30% + slate-400 색 (light)
  - particle: 대상 엣지에만 흐름
- **재클릭 해제** + 우측 "필터 해제 ✕" 링크 지원
- **커밋:** `slm-dashboard@229d396`, `e9fb342`, `851ea09`, `876571f`

### 7.9 KPI 카드 ↔ 다이어그램 필터 연동 (2026-04-17)
- **문제:** 페이지 최상단 7개 KPI 카드는 Sankey용 `filterNodeIds`에만 연결, 다이어그램 모드에는 미연결 → 사용자가 카드 클릭해도 다이어그램 반응 없음
- **해결:** `FlowDiagramMap`에 `externalFilter` prop 추가. KPI `activeFilter` → 다이어그램 내부 필터 매핑
  - `crossWarn → cross`, `imbalance → imbalance`, `alarm → alarm`, `equipFailure → equip`, `commError → comm`
- 이제 큰 KPI 카드 클릭 = 다이어그램 내부 배지 클릭 = 동일 효과
- **커밋:** `slm-dashboard@84779a5`

### 7.10 버튼 중복 제거 + 디자인 통일 (2026-04-17)
- 맵 내부 "전체 맞춤" 버튼 제거 (카드 헤더 "초기화면"과 기능 중복)
- 초기화면 아이콘 Maximize2 → Scan (전체화면과 시각 차별화)
- 전체화면 활성 시 amber 강조
- 맵 내부 버튼: `border + dark:slate-800/80 + shadow-sm` (카드 헤더 outline Button과 일관성)
- **커밋:** `slm-dashboard@631892b`

### 7.13 계통도 엣지 불균형 % 뱃지 + hover 툴팁 (2026-04-17)
- **뱃지 표시:** 각 불균형 엣지(경고/주의/관심) 중간에 `±N%` 뱃지 Marker 배치
  - 경고 red, 주의 amber, 관심 yellow
  - 1000% 이상은 `1000%↑` 축약 표기
- **위치 계산:** 동일 parent→child 엣지의 여러 세그먼트(trunk/vertical/drop) 중 가장 긴 세그먼트 선택 → 해당 세그먼트의 **상류쪽 35% 지점**에 배치 (노드 박스와 겹침 방지)
- **앵커:** `anchor="bottom"` + `marginBottom: 2px` → 엣지 라인 위쪽에 띄움
- **hover 툴팁:**
  - 등급 (경고/주의/관심) — 색상 매칭
  - 상류 시설명 + 유량 (m³/h)
  - 하류 시설명 + 유량 (m³/h)
  - 불균형률 % (큰 숫자로 강조)
  - 다크/라이트 모드 대응, z-index 100
- **필터 dim 연동:** `filter_dimmed` 엣지에는 뱃지 표시 안 함
- **키 수정:** edgeImbalance separator `->` → `|` (실제 백엔드 응답과 맞춤)
- **커밋:** `slm-dashboard@2bcd5c6`, `6fecad5`, `a20e068`

### 7.12a 계통도/흐름도 범례 통합 (2026-04-17)
- **문제:** 계통도에 "유량 활성/없음"이 없고, 흐름도에도 카운트 없는 색상 범례만 있어 양측 지표 불일치
- **해결:**
  - 계통도 상단 패널: 유량 활성/유량 없음 배지 + 필터 동작 추가. 시설유형 옆 "유량 정상" 엣지 색 범례 표시
  - 흐름도 범례 바: 기존 "유량 정상 / 교차검증 이상" 외 "유량 활성/없음" 카운트 배지 추가
  - KPI 카드 ↔ 다이어그램 매핑에 `activeFlow`/`zeroFlow` 추가 (양쪽 클릭 동일 동작)
- **결과:** 계통도와 흐름도의 지표 세트가 동일하게 통일 (유량 활성 / 유량 없음 / 유량 정상 / 유량 불균형 / 교차검증 이상 / 알람 / 설비 장애 / 통신 이상)
- **커밋:** `slm-dashboard@41973af`

### 7.11a 탭 명칭 변경 + 계통 카드 뷰 선택 제공 (2026-04-17)
- **탭 명칭 (사용자 멘탈 모델 정합):**
  - "상세" → "**흐름도**" (생키 다이어그램 — 유량 기반 연속 흐름)
  - "다이어그램" → "**계통도**" (박스 트리 — 상세 시각화)
  - "계통" 유지 (계통별 그룹 카드)
- **계통 카드 UX 개선:**
  - 이전: 카드 클릭 → 상세(생키) 자동 전환 (선택권 없음)
  - 이후: 카드 내 `[흐름도] [계통도]` 버튼 제공 → 사용자가 원하는 뷰 선택
  - `handleGroupExpand(rootId, target: "flow" | "diagram")`
- 각 탭/버튼에 `title` 속성으로 의미 설명 추가
- **커밋:** `slm-dashboard@bda145e`

### 7.11 계통(Root) 선택 시 자동 fit-view (2026-04-17)
- **문제:** 드롭다운에서 특정 계통 선택 시 필터는 적용되지만 줌/뷰는 그대로 → 해당 계통 위치를 수동으로 찾아가야 했음
- **해결:**
  - `selectedRoot` 변경 감지 → 해당 계통 노드 bounds로 자동 `fitBounds` (800ms 애니메이션)
  - `handleFitView` 로직 강화: `rootFilteredKeys` 있으면 해당 계통만 대상, 없으면 전체
- **UX 효과:**
  - 페이지 상단 드롭다운(전체/특정 계통) 변경 → 다이어그램 자동 줌인/아웃
  - 생키 "계통" 뷰 카드 클릭 → setSelectedRoot → 다이어그램도 동일 동작
  - "초기화면" 버튼도 현재 선택 계통에 맞게 동작
- **커밋:** `slm-dashboard@dcb2680`

### 7.12 Dev 캐시 헤더 (2026-04-17)
- 개발 환경에서 브라우저 HTTP 캐시가 old JS 번들을 사용해 HMR 후에도 반영 안 되는 문제 방지
- `next.config.ts` dev 모드만 `Cache-Control: no-store, must-revalidate` + `Pragma: no-cache`
- **커밋:** `slm-dashboard@2937f13`

### 7.14 MapLibre attribution 컨트롤 숨김 (2026-06-18)
- **증상:** 용수 흐름 계통도 우측 하단에 "MapLibre ⓘ" 배지 표시
- **원인:** `react-map-gl/maplibre` 의 `attributionControl` 기본 활성. GIS 지도
  (`GisMap.tsx`)는 `attributionControl={false}` 적용했으나 `FlowDiagramMap` 만 누락
- **라이선스 판단:** MapLibre GL JS 는 BSD-3 라 로고·attribution 표시 **의무 없음**
  (Mapbox 와 차이). 이 지도는 외부 타일 없는 빈 스타일(`sources:{}`)이라 데이터
  출처 표기 의무도 0 → 숨겨도 무방 (§6 폐쇄망 빈 캔버스 원칙과 일치)
- **수정:** `FlowDiagramMap.tsx` `<MapGL>` 에 `attributionControl={false}` 추가
- **검증:** `/monitoring/flow` 에서 "MapLibre" 텍스트·attribution DOM 제거 확인

### 7.15 노드 클릭 부드러운 확대 복원 — reduced-motion 대응 (2026-07-11)
- **증상:** 용수공급 계통도(FlowDiagramMap)에서 배수지 등 노드 클릭 시 부드러운
  확대(flyTo)가 사라지고 즉시 점프하는 것처럼 보임. 흐름도(FlowMonitoringGraph)는
  정상 — 이쪽은 custom rAF 카메라라 무영향
- **원인:** MapLibre GL 은 OS **동작 줄이기**(`prefers-reduced-motion: reduce`)가
  켜지면 `flyTo` 애니메이션을 건너뛰고 목적지로 즉시 이동함(라이브러리 기본 동작).
  §4.1 의 `prefers-reduced-motion` 전역 억제 정책과 별개로, MapLibre 내부 판정임
- **수정:** `FlowDiagramMap.tsx` handleNodeClick →
  `mapRef.current.getMap().flyTo({ center, zoom: Math.max(getZoom()+1, 15),
  duration: 800, essential: true })`
  - **`essential: true`** — reduced-motion 에서도 애니메이션 유지(로딩 스피너와
    동일하게 "상태 전달용 필수 모션" 예외. §7.16/globals `.slm-live-*` 와 같은 취지)
  - **`getMap().flyTo`** — 엣지 렌더가 쓰는 검증된 저수준 API 로 통일(안정성)
  - **`zoom+1`** — 이미 확대돼 있어도 한 단계 더 들어가 "확대" 체감 보장
- **검증:** 클릭 후 줌 Lv 15 확대 + 노드 포커스(Playwright)
- **커밋:** `slm-dashboard` 계통도 클릭 확대 reduced-motion 대응

### 7.16 상시 로딩 인디케이터 reduced-motion 예외 전역화 (2026-07-11)
- **맥락:** 계통도 KPI(교차검증 이상) → AI 분석 팝업(`QuickAnalysisDialog`)의 로딩
  스피너가 동작 줄이기 환경에서 1회 후 멈춰 "작업이 멈춘 것"처럼 보임
- **원인:** `.slm-live-spin`/`.slm-live-pulse`(전역 freeze 예외 규칙)가 채팅
  `ChatMessageArea` 컴포넌트 스코프 `<style jsx global>` 안에만 정의 → 그 컴포넌트가
  마운트되지 않은 팝업에선 클래스 자체가 없어 예외가 적용되지 않음
- **수정:** 예외 키프레임/규칙을 `globals.css` 전역으로 승격(단일 정본). 팝업 로딩
  스피너·진행 스텝퍼에 `.slm-live-spin`/`.slm-live-pulse` 적용, 로딩 중 진행 스텝퍼
  상시 노출. 병목 조회(교차검증 수초) 중에도 계속 회전 → 멈춘 것으로 오인 방지
- **검증:** 전역 `.slm-live-spin` 규칙이 reduced-motion 에서 `animation-iteration-count:
  infinite` 로 복원됨을 확인(Playwright)

## 8. 결정 필요 사항

사용자 확인 후 Phase 1 시작:

1. **Option A/B/C 중 B(MapLibreGL) 확정?** — 권장
2. **데이터 소스:** 기존 `tb_facility_flow_map`(상하류 관계)으로 충분, OR 신규 `tb_flow_diagram_node` 테이블 추가?
3. **초기 좌표:** `computeSankeyLayout()` 자동 배치 + 관리자 수동 조정?
4. **탭 vs 별도 페이지:** `/setup/flow-map` 내 탭 전환 vs `/setup/flow-diagram` 신규 라우트?
5. **Base map:** 완전히 빈 캔버스 vs 간단한 격자(grid) 배경?
6. **3방향 연계:** 노드 클릭 시 `/chat` 딥링크 + `/setup/gis` 딥링크 둘 다?
