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

### 2.2 배치 좌표
- 저장: `tb_network_info.meta.canvas_pos = {x, y}` — **스키마 변경 없음**
- 신규 API `PUT /network/canvas-positions` `{positions:[{equipment_id,x,y}]}`
  — `meta = COALESCE(meta,'{}') || {"canvas_pos": …}` 병합 (gateway 등
  기존 meta 키 보존. 기존 PUT /network/infos 는 meta 전체 덮어쓰기라 부적합)
- 로드: `/network/topology` nodes 에 `canvas_pos` 필드 추가 (meta 유도 —
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
