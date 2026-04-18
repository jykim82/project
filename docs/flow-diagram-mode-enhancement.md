# 용수흐름도 다이어그램 모드 고도화 사양

**작성:** 2026-04-16
**상태:** 검토 대기 (사용자 승인 전)
**이전 사양:** `docs/flow-diagram-mode-spec.md` (Phase 1~2b 초기안)
**관련 커밋:** `slm@421262e`, `slm-dashboard@6ce37b9`

## 1. 현재 상태 (Phase 2b 기준)

**완료:**
- MapLibreGL 빈 캔버스 위 노드 + 엣지 GeoJSON 렌더
- 99 노드 / 97 엣지 (좌→우 수계 흐름, `group_level` × `ROW_Y_STEP` 자동 배치)
- GIS 관망도 SVG 아이콘 재사용 (reservoir/booster/purifier/valve_pressure/meter)
- 레벨별 배경 circle (cyan/green/amber/purple)
- 줌 레벨 기반 단순 LOD (`display_from_z` filter)
- `/monitoring/flow` 탭에 통합 (상세/계통/다이어그램 3-way)

**미흡:**
- **정적 표현 일색** — 실시간 수치 없음, 알람 반영 없음, 흐름 방향 시각화 없음
- **LOD가 하드코딩** — 노드가 한 번에 모두 나타나거나 사라짐
- **밀집** — L3(소블록) 42개가 세로 한 컬럼, 라벨 겹침
- **레이어 토글 부재** — 한 번에 모든 것 렌더, 사용자가 필요한 정보만 볼 수 없음
- **연관 기능 미연결** — 북극성 `/chat` 딥링크 / 작업 / 알람해제 / GIS 맵 점프 없음

## 2. 레이어별 데이터 표현 설계

**핵심 아이디어:** 6개 논리 레이어를 정의하고 각각 on/off, 줌 임계값 독립 제어.

### L0. **Topology 레이어** (항상 ON, 줌 무관)
- 엣지 엘보우(`fd-edges`) — 수계 라인
- 흐름 방향 화살표(애니메이션 `line-dasharray` + `line-gradient`)
- 레이어 우선순위: 가장 아래

### L1. **Node Base 레이어** (항상 ON, 줌 임계값으로 계층 집계)
- 레벨별 배경 circle (cyan/green/amber/purple)
- `parent_node_id` 기반 **그룹 집계** — 줌 아웃하면 하위 노드가 부모로 접히고 parent box 1개만 노출
- 줌 임계값: L0 ≥ 8, L1 ≥ 10, L2 ≥ 11, L3 ≥ 12

### L2. **Symbol 레이어** (항상 ON)
- GIS SVG 아이콘 (reservoir/booster/purifier/meter 등)
- 노드 배경 위에 겹침

### L3. **Label 레이어** (토글 ON/OFF, 줌 ≥ 11)
- `sitename (facilitytype)` 텍스트
- 우측 배치(`text-anchor: left`, `text-offset: [1.4, 0]`)
- 밀집 시 MapLibreGL `text-allow-overlap: false` 자동 숨김

### L4. **Metric 레이어** (토글 ON/OFF, 줌 ≥ 12)
- 기존 `FlowRealtimeNode.metrics` 재사용 — flow / level / pressure
- 노드 하단에 `"52.3 m³/h"` 형태 텍스트
- **색상 tinting**: 수치 기반으로 노드 배경을 붉은색(고압) / 파란색(저수위) 그라데이션
- **Level zones** (배수지 여러 구역) — 라디얼 pie 미니 차트 또는 stacked bar
- API 폴링: 기존 `fetchFlowMapRealtime()` 60초 주기 재사용

### L5. **Alarm 레이어** (토글 ON/OFF, 항상 줌 ≥ 9)
- `alarm_severity`(경고/주의) → 노드 주변 **glow 링** (빨강/앰버)
- `comm_error: true` → 회색 점선 테두리
- `equip_failures[]` → 작은 빨간 X 뱃지
- P9 `tb_equipment_alarm_report` 활성 알람 `SELECT`으로 교차 조인
- 알람 우선순위: 심각 > 경고 > 주의

### L6. **Highlight 레이어** (상호작용 일시 ON)
- 노드 클릭 시 상류/하류 경로 재귀 계산 (기존 `getAllPaths()` 재사용 가능)
- 해당 경로의 엣지만 두꺼운 흰색 + glow
- 나머지는 opacity 0.15로 fade

### 추가: **Edge Imbalance 레이어** (토글 ON/OFF, 줌 ≥ 11)
- 기존 `edgeImbalance` 재사용 — 물 수지 불균형 grade (정상/관심/주의/경고)
- 엣지를 grade 기반으로 색상·두께 차등 (경고일수록 빨강·굵게)

---

## 3. 디자인 개선

### 3.1 노드 시각 요소 (rectangle 박스로 진화)

**현재:**
```
⬤ (circle + icon 겹침)
   라벨
```

**개선안:**
```
┌─────────────────┐
│ 🏭  배수지명        │ ← L3 아이콘 + 제목
│ ─── 52.3 m³/h ─── │ ← L4 메트릭
│ ● 경고 1          │ ← L5 알람 뱃지 (선택)
└─────────────────┘
```

**구현 옵션:**
- (a) MapLibreGL `fill` + `line` + `symbol` 조합 (rect polygon 생성 필요, box_width/height를 lng/lat 단위로 변환 — 줌 의존적)
- (b) HTML overlay (`react-map-gl` `Marker` 컴포넌트) — React 노드를 맵 좌표에 고정, 복잡한 레이아웃 자유로움, 성능은 노드 수 100 이하면 무난
- (c) Canvas custom layer — 완전 커스텀 drawing (가장 고성능, 구현 복잡)

**권장: (b) HTML Marker** — 99 노드면 React 렌더링 오버헤드 허용 범위, 박스/뱃지/차트 조합 자유로움.

### 3.2 색상 체계 확장

| 용도 | 색상 |
|---|---|
| 정수장 (cyan-500) | `#06b6d4` |
| 배수지 (green-500) | `#22c55e` |
| 가압장·감압 (amber-500) | `#f59e0b` |
| 소블록·소소블록 (purple-500) | `#a855f7` |
| **알람 심각** (red-500) | `#ef4444` |
| **알람 경고** (red-400) | `#f87171` |
| **알람 주의** (amber-400) | `#fbbf24` |
| **통신 단절** (slate-500) | `#64748b` |
| **imbalance 경고** (엣지) | red gradient |
| **정상 흐름** (엣지) | blue-400 `#60a5fa` |

### 3.3 밀도 관리 (L3 소블록 42개 문제)

**현재:** 한 컬럼 세로 배치 → 라벨 겹침 + 스크롤 필요
**개선:**
- (a) **자동 2~3 sub-column**: 같은 레벨 내 n > 20이면 두 줄로 split
- (b) **상위 노드별 클러스터링**: 부모 가압장/배수지 근처로 leaf 이동 (parent_node_id 활용)
- (c) **Force-directed mini-layout**: leaf끼리 collision 방지 Repulsion

**권장: (b) + (a) 조합** — parent_node_id로 부모 근처 그룹핑 + overflow 시 2열 분산.

### 3.4 흐름 방향 애니메이션

`line-dasharray` 시간 기반 offset 애니메이션:
```ts
// 매 프레임마다 offset 증가 → 대시가 흐르는 효과
const step = (performance.now() / 50) % 60;
map.setPaintProperty("fd-edges", "line-dasharray", [0, step / 10]);
```

또는 MapLibreGL `line-gradient` + `line-progress` expression으로 구현.

### 3.5 Minimap / Navigation

- 우상단 미니맵 (전체 다이어그램 100% view + 현재 viewport 박스)
- 줌 Lv 슬라이더 (우하단)
- fit-view 버튼 (전체 맞춤)
- 검색 박스 (좌상단, sitename 필터 → flyTo)

---

## 4. 상호작용 고도화

### 4.1 노드 클릭 액션 (북극성 연계)

팝오버 메뉴 4개 액션:
1. **선택** — 상류/하류 경로 highlight (L6)
2. **현장 사진 확인** — `/chat?sitename=X&facilitytype=Y` 딥링크 (P11 재사용)
3. **작업 등록** — TaskFormDialog compact (P6 재사용)
4. **트렌드 보기** — 기존 `FlowNodeTrendPanel` 재사용 (monitoring flow 페이지 컴포넌트)

### 4.2 키보드 단축키
- `↑↓←→` — 팬
- `+/-` — 줌
- `F` — fit-view
- `L` — 라벨 토글
- `M` — 메트릭 토글
- `A` — 알람 토글
- `Esc` — 선택 해제

### 4.3 드래그 편집 모드 (관리자)

- 우상단 "편집" 토글 버튼 → 노드 드래그 가능
- 드래그 종료 → `PUT /flow-diagram/nodes/{id}` 호출 (이미 구현)
- 편집 모드 중에만 노드 테두리 대시 + 커서 move

### 4.4 타임라인 재생 (과거 재현)

기존 `useFlowTimeline` 훅 재사용. 다이어그램에도 timeline slider 연결:
- 특정 시각의 `flow-map/realtime?at=2026-04-15T10:00:00` 조회
- Metric 레이어를 과거 값으로 스냅샷

---

## 5. 제안 단계별 계획

### Phase 3a: HTML Marker 전환 (핵심) — 2~3일
- MapLibreGL symbol → `react-map-gl` Marker 기반 rectangle box 전환
- 기본 rect + icon + label + metric placeholder
- parent_node_id 기반 그룹 집계 로직

### Phase 3b: 실시간 데이터 결합 — 1~2일
- `fetchFlowMapRealtime()` 통합 → L4 Metric 레이어
- 60초 폴링 + 손실 grade 엣지 색상
- 통신 단절 / 알람 / equip_failures 표시

### Phase 3c: 상호작용 + 딥링크 — 1~2일
- 노드 클릭 팝오버 + 4개 액션 (선택/사진/작업/트렌드)
- `/chat` + TaskFormDialog 재사용
- 키보드 단축키

### Phase 3d: 레이어 토글 패널 — 1일
- 좌측 슬라이딩 패널 (GisLayerPanel 패턴 재사용)
- 6개 레이어 on/off 체크박스 + 줌 임계값 조정

### Phase 3e: 시각 효과 — 1~2일
- 흐름 방향 애니메이션
- 알람 glow 링
- Minimap

### Phase 3f: 드래그 편집 + 타임라인 — 2일
- 편집 모드 드래그 저장
- Timeline slider 통합

**총: 8~11일** (Phase 3 전체)

### 단계별 독립 배포 가능:
- 3a 완료 시 "박스 레이아웃"만 보이지만 바로 delivery 가능
- 3b 추가 시 "실시간 값 보이는 박스"
- 3c 추가 시 "클릭해서 현장 이동 가능"
- 각 단계별 git tag로 rollback 지원

---

## 6. 사용자 결정 필요

### 6.1 박스 렌더 방식
- (a) MapLibreGL 네이티브 (fill + line + symbol 조합) — 성능 최상, 코드 복잡
- (b) **HTML Marker (react-map-gl)** — 권장, 99 노드면 OK
- (c) Canvas custom layer — 과제-난이도 최상

### 6.2 그룹 집계 전략
- `parent_node_id`를 **seed 스크립트에서 자동 계산** 할지, 관리자가 수동 지정할지
- 자동: 같은 sitename prefix 기반 (예: "연성선부01~08" → "연성선부" parent)
- 수동: 편집 UI 제공

### 6.3 실시간 데이터 연동 범위
- L4 Metric 필수? (값 표시) — 권장 필수
- L5 Alarm 필수? (glow 링) — 권장 필수
- Edge Imbalance 옵션? — 기본 OFF로 시작

### 6.4 상호작용 범위
- 노드 클릭 → 팝오버 메뉴 (권장) vs 바로 하이라이트 (현재)
- `/chat` 딥링크 필수? — **권장 필수** (북극성 연계)

### 6.5 Minimap·Timeline
- Phase 3에 포함 vs 후순위?
- Timeline은 `useFlowTimeline` 이미 있어서 재사용 쉬움

### 6.6 관리자 편집 모드
- Phase 3에 포함 vs 후순위?
- 포함 시 좌표 수동 조정 가능, 미포함 시 자동 레이아웃 고정

---

## 7. 권장 우선순위 (사용자 확인 후 실행)

1. **Phase 3a (HTML Marker 박스)** — 가장 큰 시각적 변화, 필수
2. **Phase 3b (실시간 메트릭)** — 운영 가치 가장 큼
3. **Phase 3c (딥링크)** — 북극성 통합, 필수
4. **Phase 3d (레이어 토글)** — UX 필수
5. **Phase 3e (애니메이션/minimap)** — polish
6. **Phase 3f (편집/타임라인)** — 후순위

---

## 8. 후속 수정 이력

### 8.1 불균형 엣지 색상 trunk/vertical 전파 (2026-04-18)

**문제:** 실시간 계통도에서 부모→자식 엣지가 "절반 파랑 / 절반 빨강"으로 분절되어 렌더됨. 예) 석문→석문1·석문2 는 +87% 불균형인데 석문 쪽(trunk·vertical)은 파랑, 자식 쪽(drop)만 빨강.

**원인:**
- bracket 레이아웃용으로 `/flow-diagram/edges` 는 각 연결을 3개 feature 로 분할:
  1. `edge_type="trunk"`: 부모→bus_x (수평)
  2. `edge_type="vertical"`: bus_x 세로 트렁크
  3. `edge_type="drop"`: bus_x→자식 (수평)
- `trunk`·`vertical` feature의 downstream 은 `_trunk_`/`_vertical_` 가상 노드
- 프런트 `enrichedEdges` 의 `edgeImbalance[${upKey}|${dnKey}]` 조회 시 가상 dn은 매칭 실패 → 기본 `"정상"`(파랑) 고정, 실제 `drop` 만 불균형 색

**해결 (`FlowDiagramMap.tsx` `enrichedEdges`):**
- 부모별(`upKey`) 자식 `drop` 들의 최악 grade 를 사전 집계 (`worstByParent: Map<string, string>`)
- `trunk`/`vertical` feature 색상을 `worstByParent.get(upKey)` 로 전파
- GRADE_RANK(정상<관심<주의<경고)로 가장 심각한 자식 등급 선택 → 동일 부모 공유 세그먼트 통일
- 뱃지(`imbalanceBadges`)는 기존 `edgeImbalance[imbKey]` 필터로 trunk/vertical 가상 dn 자동 제외 → 중복 없음

**영향 범위:** FlowDiagramMap(다이어그램 모드)만. FlowMonitoringGraph(Sankey 실시간 그래프)는 링크당 단일 cubic bezier path 라 분절 문제 없음 → 변경 불필요.

3a~3c만 해도 "현장 운영자가 쓰는 실시간 다이어그램"으로 충분히 기능.
