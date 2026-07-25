# 계통도 엔지니어링화 사양 v1 — 구축 고도화 ②

> **상태:** v1 구현 완료 (2026-07-24)
> **목적:** 설비 선후관계 다이어그램(실시간 계통도)을 "그리는 작업"에서
> **"관계 데이터의 파생 뷰"** 로 전환 — 구축자가 관계만 등록하면 배치·검증·
> 실시간 반영은 시스템이 처리한다.
> **관련:** `docs/flow-diagram-mode-spec.md`(계통도 뷰),
> `docs/datainfo-conversion-rule-spec.md`(구축 고도화 ①)

---

## 1. 기존 구조 (검토 결과 — 유지)

관계는 이미 단일 정본 + 다중 파생:
- **정본** `tb_facility_flow_map` (상류→하류 시설 쌍) — CRUD API + CSV
  import/export + `setup/flow-map` 화면 기존재
- 파생 4곳: 계통도 엣지(정본 JOIN 노드) / `_CAUSAL_INDEX`(교차 검증·상류
  장애·물수지) / EPANET 매핑 / 상류 원인 설명
- `tb_flow_diagram_node` 는 **배치만** 저장 (좌표·박스·줌레벨)

## 2. 공백과 해소 (v1 구현)

| 공백 | 해소 |
|---|---|
| 배치가 `tools/seed_flow_diagram.py` 수동 실행에 갇힘 | **`POST /flow-diagram/relayout`** — 레이아웃 알고리즘(B안 가로배치+bracket) API 이관. `mode=new_only`(신규 시설만 배치 — 기존 수동 조정 보존, 상류 노드 옆 삽입) / `mode=full`(전체 재배치, confirm 후) |
| 배치 편집 UI 부재 (`setup/diagram` stub) | **"노드 배치" 탭** (`DiagramLayoutEditor`) — SVG 렌더 + 노드 드래그 → PUT 즉시 저장(엣지 실시간 추종) + relayout 버튼 2종 + lint 배지 |
| 자동 배치 노드 겹침 (2026-07-25 수정) | 원인 2중: ① 에디터가 전체 Y 범위를 900px 고정 캔버스에 정규화 → 형제 간격 0.006°가 ~11px 로 압축돼 22~30px 박스 겹침. **`computeCanvasH()`** — 최소 인접 Y 간격이 36px 로 렌더되도록 캔버스 높이 동적 산정(900~6000px, 세로 스크롤. 0.002° 미만 간격은 드래그 잔재로 간주 제외). ② `new_only` 삽입이 형제만 회피하고 타 서브트리와 충돌 — **`_avoid_overlap()`** 같은 X 열(±PARENT_X_GAP/2) 기존 노드와 ROW_Y_GAP 미만이면 아래로 밀기. 99노드 전수 사각형 충돌 검사 0건 확인 |
| 정합 검증 부재 | **`GET /flow-diagram/lint`** — 배치 누락(엣지 미표시 원인)·고아 노드·순환 참조(DFS)·EPANET 미매핑(참고). 구축 검수 자산 (`delivery-checklist` 연계 후보) |
| 관계 변경 후 재기동 의존 | flow-map POST/DELETE → `_rebuild_causal_index_entry` 부분 리로드 연결 (best-effort). **부수 수정: 리로드 함수의 early-return 이중 close 버그** (풀 이중 반환 → "unkeyed connection") |

## 3. 구축 워크플로 (완성형)

1. 관계 등록 — `setup/diagram > 용수 흐름` 탭 (개별/CSV 일괄)
2. **신규 시설 자동 배치** 버튼 — 계통도에 즉시 반영
3. 드래그 미세조정 — 저장 자동
4. **정합 배지** 확인 — 누락·고아·순환 0 이면 검수 통과
5. 물수지·교차 검증·상류 추적은 재기동 없이 즉시 새 관계 반영

## 4. 검증 (2026-07-24)

- E2E: 신규 관계 추가 → lint `missing: [검증테스트 소블록]` 감지 →
  relayout(new_only) → 상류(신평 배수지) 옆 자동 배치 → lint ok. 정리 완료
- new_only 가 기존 99노드 좌표 무변경(보존) 확인. 현 데이터 정합:
  99노드 전부 배치·순환 0·EPANET 미매핑 43(참고)
- seed 스크립트는 유지 (초기 대량 시드용) — 이후 운영 변경은 API 경로

## 변경 이력
- 2026-07-24 v1 — 검토(5개 저장소 전수) 후 4공백 구현
