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
- **알갱이 애니메이션:** 2겹 대시 레이어 (fd-particle-a/b) + 교대 opacity 펄스. 라인 굵기 2배 상향
- **GIS 데이터 동기:** lg 노드에 배수지 supply_time (공급가능시간/일평균유입/유출/야간최소유량) 추가. GIS 관망도 팝업과 동일 데이터 항목
- **커밋:** `slm-dashboard@ac8b19e`

## 8. 결정 필요 사항

사용자 확인 후 Phase 1 시작:

1. **Option A/B/C 중 B(MapLibreGL) 확정?** — 권장
2. **데이터 소스:** 기존 `tb_facility_flow_map`(상하류 관계)으로 충분, OR 신규 `tb_flow_diagram_node` 테이블 추가?
3. **초기 좌표:** `computeSankeyLayout()` 자동 배치 + 관리자 수동 조정?
4. **탭 vs 별도 페이지:** `/setup/flow-map` 내 탭 전환 vs `/setup/flow-diagram` 신규 라우트?
5. **Base map:** 완전히 빈 캔버스 vs 간단한 격자(grid) 배경?
6. **3방향 연계:** 노드 클릭 시 `/chat` 딥링크 + `/setup/gis` 딥링크 둘 다?
