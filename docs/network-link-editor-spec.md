# 네트워크 링크 에디터 사양 v1

> 작성 2026-07-26 · Backend `network_crud.py` 소폭 확장 · Frontend
> `setup/networks` 3탭 — 목적: **구축 편의성·직관성** (표에서 장비 ID 를
> 검색해 짝짓던 링크 등록을 화면 드래그로)
> **v1 구현 완료 2026-07-26** — 295장비·280링크 렌더, 드래그 연결→프리필
> 폼, 배치 저장(meta 병합·gateway 보존 확인), lint 첫 실행에서 **미연결
> 14대·IP 중복 12건** 검출. 부수 발견: `tb_protocol_lookup` 부재로 링크
> 폼 프로토콜이 항상 비어 UI 등록이 불가능했던 기존 버그 → Migration
> 0124 로 마스터 생성·시드 [E-052]

## 1. 배경

- 네트워크 정본: `tb_network_info`(장비 속성) + `tb_network_link`(장비 간
  통신 링크). 계통도 관계(`tb_facility_flow_map` — 시설 단위)와 **별개 정본**
- 현재 편집은 표+폼 (`/setup/networks` 2탭). 시각화는 `/network` 토폴로지
  — **읽기 전용**. "그림은 있는데 그림 위에서 편집 불가"
- 계통도 캔버스와 같은 유형의 불편 — 단, **노드 단위(장비)·엣지 의미
  (통신)가 달라 캔버스 에디터 재사용이 아닌 별도 에디터** (혼동 방지,
  색·라벨 구분)

## 2. 설계

### 2.1 UI — `setup/networks` 3탭
**링크 에디터(기본)** | 네트워크 장비(표) | 네트워크 연결(표 — 확인·CSV)

`src/components/setup/network-editor/NetworkLinkEditor.tsx` (React Flow):
- 노드 = `/network/topology` nodes (장비) — 장비유형·sitename·IP·상태 배지.
  상태색은 토폴로지 화면과 동일 계열
- 엣지 = topology edges (`tb_network_link`) — 프로토콜 라벨
- **드래그 연결** → 기존 `NetworkLinkFormDialog` 재사용 (source/target
  프리필 — `prefill` prop 추가) → 저장 시 기존 `POST /network/links`
  (FK 검증·프로토콜 마스터 그대로)
- **엣지 선택 삭제** → confirm → 기존 `DELETE /network/links/{s}/{t}`
- 링크 CRUD 는 **기존 API 그대로** — 캔버스처럼 배치 스냅샷 diff 가 아닌
  즉시 반영 (연결마다 폼 확정이 개입하므로 직접 CRUD 가 자연스러움)

### 2.2 배치 좌표 (v1.2 — Migration 0126 으로 저장 위치 변경)
- 저장: **`tb_canvas_layout(layer='network', node_key=equipment_id, x, y)`**
  — 표시 전용 테이블
- ~~v1: `tb_network_info.meta.canvas_pos`~~ → **폐기.** 그 테이블은 통신 장비
  *대장*이라, 배치 저장이 UPSERT 로 대장 행을 만들어 자동 정렬 1회에 대장이
  180 → 295 로 늘었다 [E-054]. 좌표는 자산 대장에 저장하지 않는다
- 참고: 계통도(용수 계통) 좌표는 `tb_flow_diagram_node.diagram_x/y` 유지 —
  그 테이블은 애초에 다이어그램 테이블(box·label·표시 줌)이라 대장 오염이 아님
- API `PUT /network/canvas-positions` `{positions:[{equipment_id,x,y}]}`
  — `tb_canvas_layout` UPSERT (경로만 바뀌고 계약은 동일)
- 로드: `/network/topology` nodes 에 `canvas_pos` 필드 (LEFT JOIN 유도 —
  기존 소비처엔 무해한 추가 필드)
- 초기 배치(canvas_pos 없는 장비): sitename 그룹 열 배치 (그룹당 세로
  나열) — 클라이언트 계산. 네트워크 배치는 지리 정본이 없는 순수 표시
  값이므로 클라 레이아웃 허용 (계통도의 서버 정본 원칙과 구별)
- 노드 드래그 후 debounce 일괄 저장

### 2.3 구축 lint (클라이언트 계산 배지)
- **미연결 장비** n건 (링크가 하나도 없는 장비 — 배선 누락 후보)
- **IP 중복** n건
- 후속: `/setup/audit` 네트워크 검사 항목 승격 (§5)

## 3. 변경 파일

| 계층 | 파일 | 내용 |
|---|---|---|
| BE | `endpoints/network_crud.py` | topology nodes 에 canvas_pos + PUT /network/canvas-positions |
| FE | `setup/network-editor/NetworkLinkEditor.tsx` (신규) | React Flow 에디터 |
| FE | `NetworkLinkFormDialog.tsx` | `prefill` prop (create 모드 프리필) |
| FE | `setup/networks/page.tsx` | 3탭 (에디터 기본) |
| FE | `network-api.ts` / `network-manage-api.ts` | canvas_pos 타입·저장 래퍼 |

## 4. 검증

- 에디터 로드 (전 장비·링크 렌더) → 드래그 연결 → 폼 저장 → 표 탭·
  `/network` 토폴로지에 반영 → 엣지 삭제 → 반영. 배치 드래그 → 저장 →
  새로고침 유지. 기존 meta 키(gateway 등) 보존 확인. tsc 0

## 5. 후속 보류

- lint 의 `/setup/audit` 검사 항목 승격 (미연결 장비·IP 중복)
- 링크 방향·이중화(role) 시각 표현 고도화
- 장비 노드 더블클릭 → 장비 상세(핑 이력) 연결

## 8. 조작 모델 (v1.1 — 2026-07-26)

**원칙: 클릭은 선택, 변경은 명시적 어포던스.** 구축 화면은 조회·점검 목적
클릭이 훨씬 잦으므로, 클릭 즉시 파괴적 동작이 일어나면 오조작 위험이 크다
(초기 v1 의 "엣지 클릭 → 삭제 확인창"은 철회).

| 조작 | 동작 |
|------|------|
| 노드/엣지 클릭 | **선택**. 툴바에 `선택: …` 표시, 수정·삭제 활성화 |
| 엣지 선택 시 | 선 중앙에 `삭제` 버튼 노출 → 확인창 후 삭제 |
| 노드/엣지 더블클릭 | 해당 대상 **수정** 다이얼로그 (표 화면과 동일 폼) |
| 장비 사이 드래그 | 링크 등록 폼 프리필 (기존 v1 유지) |
| Delete 키 | `onBeforeDelete` 사전 확인 후 삭제 |
| 노드 드래그 | 배치 저장 (debounce 800ms) |

### 8.1 표준 CRUD 툴바
`[장비 추가] [링크 추가] | [수정] [삭제] | [자동 정렬] | lint | 선택 표시 | 새로고침`

- 추가/수정은 표 탭과 **같은 다이얼로그·같은 API** 재사용 (정본 이원화 없음)
- 수정·삭제는 선택 대상 종류(장비/링크)에 따라 분기, 미선택 시 비활성
- 장비 삭제는 연결된 링크 수를 확인창에 명시 (tb_network_link CASCADE)

### 8.2 자동 정렬 · 겹침
- `자동 정렬` = 현장(sitename)별 격자 재배치. 열 220px·행 64px (노드 168×46)
- 저장 좌표가 없는 장비의 초기 배치도 **이미 확정된 좌표와 충돌하면 아래 행으로
  밀어냄** (기존엔 저장 좌표를 무시하고 0,0 부터 깔아 겹침 발생)
- lint 에 `겹침 N` 추가 — 자동 정렬로 0 이 되는지로 검증

### 8.3 배치 저장은 장비 대장을 건드리지 않는다 [E-054 마감]
배치 좌표는 표시 전용 테이블 `tb_canvas_layout` 에 저장된다(Migration 0126).
따라서 자동 정렬로 295건을 저장해도 장비 대장(`tb_network_info`)은 그대로다.

> 중간 단계였던 "신규 등록 수 `created` 고지"(사전 확인창·토스트)는 원인 자체가
> 사라져 제거했다. 부수효과를 알리는 것보다 부수효과를 없애는 것이 근본책.


## 계층 통일 (2026-08-03)

구축 링크 에디터와 모니터링 계층형 토폴로지가 **같은 시설유형 계층**을
쓴다 — "구축에서 그린 것 = 모니터링에서 보이는 것".

- 단일 소스: `src/lib/config/network-layers.ts` — 계층 순서·라벨·색 선언.
  **동적 원칙**: 데이터에 존재하는 유형만 밴드 생성(정수장·감압설비는
  선언돼 있고 통신 장비 등록 시 자동 등장), 미선언 유형도 맨 뒤 자체
  밴드로 표시(침묵 누락 방지)
- 에디터 "자동 정렬" → **"계층 정렬"**: 현장별 격자 대신 계층(밴드) 배치.
  좌표 없는 초기 상태의 기본 배치도 계층. 검증: 295대 재정렬 —
  CORE(0~448)→가압장→배수지→소블록·소소블록→수압계실·유량계실(3296)
- 모니터링 계층형은 동일 config 를 소비 (미선언 유형 드롭되던 결함도 해소)
- 전환 시점의 기존 수동 배치는 `tmp_canvas_layout_backup` 에 보존
