# 캔버스 에디터 일원화 사양 v1

> 작성 2026-07-26 · Migration 0120 · Backend `endpoints/canvas_crud.py` 재작성 ·
> Frontend `canvas-editor/` 영속화 전환 + `setup/diagram` 탭 정리

## 1. 배경 — 좌표 정본 이원화

시설·네트워크 구축 편집기가 두 벌 존재하며 **좌표 저장소가 갈라져 있다**:

| | 캔버스 에디터 (2026-02-27) | 노드 배치 탭 (2026-07-24) |
|---|---|---|
| UI | React Flow — 팔레트 드래그·미니맵·Undo/PNG | 경량 SVG 드래그 |
| 좌표 저장 | `tb_canvas_node_position` (px, 104행) | `tb_flow_diagram_node` (경위도, 99행) |
| 관계 편집 | PUT full-diff → `tb_facility_flow_map` | 용수 흐름 탭 CRUD (causal 리로드 연동) |
| 계통도 반영 | **안 됨** (계통도는 flow_diagram_node 사용) | 즉시 반영 |

→ 캔버스에서 배치를 다듬어도 실시간 계통도에 반영되지 않고, 구축 업체
입장에서 "어디서 고쳐야 하나"가 모호. 제품 원칙(단일 정본) 위배.

## 2. 확정 원칙

- **관계 정본** = `tb_facility_flow_map` (불변 — 기존 확정)
- **배치 정본** = `tb_flow_diagram_node` (경위도 + box/zoom 메타)
- **캔버스 에디터 = 유일한 시각 편집기**로 승격. 노드 배치 탭은 기능 흡수
  후 제거 (P2). 용수 흐름 탭은 표·CSV 일괄 등록 용도로 유지
- `tb_canvas_node_position` 폐기 — 2월 수동 배치는 현행 배치(2026-07-25
  겹침 수정 완료)보다 낡아 이관하지 않음. 안전을 위해 rename 백업(§6)
- 레이아웃 알고리즘 정본 = 서버 relayout(B안 가로배치). 캔버스의
  클라이언트 자동 정렬(`use-canvas-auto-layout`)은 제거 — 알고리즘 두 벌 금지

### 기존 사양과의 관계 (충돌 검토)

- `flow-diagram-engineering-spec.md` — 노드 배치 탭·relayout·lint 가 그
  산출물. **본 사양이 UI 를 캔버스로 승계** (relayout/lint API 는 그대로
  재사용). 해당 사양에 승계 각주 추가
- `flow-diagram-mode-spec.md` (실시간 계통도) — 영향 없음 (같은 테이블 읽음)
- gis-facility-menu-spec 인스펙터 CRUD — 영향 없음 (설비·태그는 캔버스
  속성 패널과 같은 테이블 공유, 편집 UI 병존 허용)

## 3. 좌표 변환 (px ↔ 경위도)

React Flow 는 px, 정본은 경위도. **백엔드 변환 계층**에서 선형 사상:

```
CANVAS_DEG_PER_PX_X = PARENT_X_GAP / 240   # 레벨 간 0.035° = 240px
CANVAS_DEG_PER_PX_Y = ROW_Y_GAP / 56       # 형제 간 0.006° = 56px

pos_x = (lon - ORIGIN_X) / CANVAS_DEG_PER_PX_X
pos_y = (ORIGIN_Y - lat) / CANVAS_DEG_PER_PX_Y   # y 축 반전
```

- 선형 사상이므로 순서·상대 구조 보존 — 캔버스·계통도·(제거 전) 노드 배치
  탭이 항상 같은 그림
- 상수 선택 근거: 캔버스 노드 폭 ~180px/높이 ~40px 기준, 자동 배치 결과가
  캔버스에서 레벨 간 240px·형제 간 56px 로 겹침 없이 보이도록
- 변환은 백엔드 단독 소유 (`canvas_crud.py` 상수) — 프런트는 px 만 다룸

## 4. API 재설계 (P1)

### GET `/canvas/layout` (재작성)
- 노드: `tb_flow_diagram_node` 전체 → px 변환해 반환 (기존 응답 필드 유지
  — equipment_count·tag_group_count·monitoring 조인 동일)
- 엣지: `tb_facility_flow_map` (기존 동일)
- 관계에 있으나 배치 없는 시설은 노드로 내려주지 않음 — lint 의
  `missing_nodes` 로 노출 (자동 배치 버튼 안내)

### PUT `/canvas/layout` (전면 재설계 — full-diff 제거)

기존 위험 2건 제거: ① body 에 없는 노드 위치 전체 삭제 ② 엣지 full-diff
(스테일 클라이언트가 `edges=[]` 전송 시 관계 정본 전멸).

```
PUT /canvas/layout
{
  nodes:        [{sitename, facilitytype, pos_x, pos_y}],   // 위치 upsert 만
  added_edges:  [{up..., down..., relation_type}],          // 명시 diff
  removed_edges:[{up..., down...}],
  deleted_nodes:[{sitename, facilitytype}]                  // 명시 삭제
}
```

- 노드 upsert: 기존 행은 **좌표만** 갱신 (box/label/zoom 메타 보존). 신규
  행은 `_node_row()` 메타로 생성 (flow_diagram_layout 재사용)
- 엣지 add/remove 후 `_rebuild_causal_index_entry` 호출 — 용수 흐름 탭과
  동일하게 재기동 없이 물수지·상류추적 반영
- 프런트는 로드 시점 스냅샷 대비 diff 를 계산해 전송
  (`use-canvas-persistence` 개편)

### 이식 (기존 API 재사용, UI 만 추가)
- CanvasToolbar: **신규 시설 자동 배치**(relayout new_only) · 전체
  재배치(confirm) · **정합 lint 배지** (`/flow-diagram/lint`)
- relayout 후 loadFromDB 재실행으로 캔버스 갱신

## 5. 프런트 변경 (P1)

- `use-canvas-persistence`: 로드 스냅샷 보관 → 저장 시 added/removed/
  deleted diff 계산. 응답 후 스냅샷 갱신
- `use-canvas-auto-layout` 제거 → 툴바 버튼을 서버 relayout 으로 교체
- `canvas-store` 노드 삭제 시 deleted 추적 (Undo 와 정합 — 스냅샷 diff
  방식이므로 Undo 로 복원되면 diff 에서 자연 소거)
- PropertyPanel·설비/태그 링크·PNG 내보내기 등 나머지 불변

## 6. Migration 0120

```sql
ALTER TABLE tb_canvas_node_position RENAME TO tb_canvas_node_position_legacy;
```
- 관찰 기간(다음 납품 점검) 후 DROP. 롤백 = rename 원복 + canvas_crud 구버전
- `ai_server.py` 의 `CREATE TABLE IF NOT EXISTS tb_canvas_node_position`
  lifespan 구문 제거

## 7. P2 — 탭·메뉴 정리 + 시설 생성 연동

1. `setup/diagram` 탭: "용수 흐름 | 캔버스 에디터" 2탭 (노드 배치 탭 제거).
   노드 배치 탭의 잔여 가치(캔버스 높이 동적 산정 등)는 캔버스가 흡수
2. `/setup/canvas` 독립 페이지 → `setup/diagram?tab=canvas` redirect
   (tb_menu 항목 정리 — sidebar-menus 동기화)
3. 팔레트 드롭 신규 시설: sitename 입력 → `tb_flow_diagram_node` 생성까지는
   P1 경로로 동작. **기초정보(배수지/가압장 등 CRUD) 연동 확인** — 미등록
   시설은 setup 검수(base_info)가 잡아주므로 P2 에서는 드롭 시 기초정보
   등록 페이지 딥링크 안내 배너까지만 (자동 생성은 하지 않음 — 기초정보는
   유형별 필수 속성이 달라 캔버스에서 대충 만들면 검수 warn 만 늘어남)

## 8. 검증

- P1: 캔버스 로드(99노드 px 변환 정합) → 노드 드래그·저장 → **계통도
  지도·노드 배치 탭에서 동일 위치 확인** (정본 통일 증명) → 엣지 추가/삭제
  → 용수 흐름 탭·물수지 반영 → relayout·lint 툴바 동작. 스테일 가드:
  removed_edges 만으로 정본이 비는 시나리오 불가(명시 diff) 확인
- P2: redirect·메뉴·탭 제거 후 tsc 0 + 전체 스모크 16/16 + 검수 diagram
  항목 pass 유지
- 산출물 스크린샷은 `tmp/` (feedback_test_artifacts_tmp)

## 9. 위험·롤백

- 캔버스 드래그 → 정본 즉시 반영이므로 실수 이동이 계통도에 바로 보임 —
  Undo(Ctrl+Z) + 30s 자동저장 전 수동 저장 안내 유지. 대량 오배치는
  relayout full 로 복구 가능
- 롤백: Migration 0120 rename 원복 + slm/frontend 커밋 revert (배치 정본은
  flow_diagram_node 로 남아 데이터 손실 없음)
