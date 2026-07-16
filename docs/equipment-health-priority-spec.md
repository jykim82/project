# 설비 교체 우선순위 Top 5 사양 v1

설비 건강성(/monitoring/equipment-health) 개요 탭 최상단 카드 —
**"어떤 장소의 어떤 설비를 교체해야 하는가"** 를 한눈에 (2026-07-16,
사용자 피드백: "항목이 많지만 교체 대상이 직관적으로 보이지 않아").

## 문제
교체 판단 신호가 3개 탭에 분산 — 내용연수(내용연수 교체 권고 탭),
MTBF(개요 하단 표), 재발 지속(교체 후보 분석 탭). 운영자가 종합 판단하려면
탭 3개를 오가며 머릿속에서 병합해야 했음.

## 해법 — 3신호 융합 점수
| 신호 | 출처 (기존 로직 재사용) | 가중치 |
|---|---|---|
| 내용연수 초과 (overdue) | tb_equipment_lifespan + category_map | +3.0 |
| 내용연수 임박 (approaching, 1년 이내) | 〃 | +1.5 |
| MTBF < 30일 (fault_cnt≥2) | v_equipment_mtbf | +3.0 |
| MTBF < 90일 | 〃 | +1.5 |
| 재발 지속 (replacement_candidate) | alarm_fault_correlation.equipment_status **직접 호출** | +3.0 |

- 레벨: score ≥5 `매우 높음` / ≥3 `높음` / 그 외 `보통`
- **판정 로직 이원화 방지**: 재발 신호는 P5-rev `equipment_status()` 함수를
  그대로 호출 (HH/LL 필터·임계 동일). 재발 신호 실패 시 나머지 2신호로 동작.
- 재발 신호는 그룹(sitename+facilitytype+equipmenttype) 단위 — 같은 그룹의
  설비 행 전부에 가산, 설비 행이 없으면 equipment_id=null 그룹 행 생성.

## API
`GET /monitoring/equipment-health/replacement-priority?limit=5&days=90`
→ `{status, period_days, total_candidates, rows:[{sitename, facilitytype,
equipmenttype, equipment_id|null, score, level, reasons:[{type,label}]}]}`
- `type` ∈ lifespan_overdue / lifespan_approaching / mtbf / recurrence
- 구현: `slm/endpoints/replacement_priority.py` (신규 모듈, SRP)

## UI
- `src/components/monitoring/ReplacementPriorityCard.tsx` — 개요 탭 최상단
- 행: 순위 · **시설(장소) 볼드** · 설비유형(ID) · 레벨 배지 · 사유 배지들
  (아이콘+색: 초과 rose / 임박 amber / MTBF orange / 재발 violet, hover 설명)
- 행 클릭 → 주 사유 탭 이동 (lifespan 계열→"내용연수 교체 권고",
  그 외→"교체 후보 분석"). page.tsx Tabs 를 controlled 로 전환.
- 신호 0건이면 "교체 신호가 감지된 설비가 없습니다" (카드 유지 — 정상 상태도 정보)

## 데이터 전제
- 내용연수 신호는 `tb_equipment_info.commissioned_at` 입력에 의존 — 현재
  dev 데이터는 no_data 293건으로 신호 0 (입력 시 자동 반영, 코드 변경 불필요).
  GIS 시설 메뉴 인스펙터 CRUD(설치일자)로 보강 가능.

## 채팅 인텐트 (2026-07-16 완료)
`REPLACEMENT_PRIORITY_QUERY` — "교체해야 할 설비 알려줘" 등 10문형 →
graph_type=table Top 5 표. `intent_handlers/replacement.py` 가
`replacement_priority()` 를 직접 호출해 rows 조달 (개요 카드와 동일 결과 —
판정 이원화 방지). stage1 키워드 9종("교체 우선순위"/"교체해야" 등) 선언으로
SLM 폴백 없이 0.2~0.5s 분류. 스모크 16/16 회귀 통과.

## 향후 후보 (미착수)
- 대시보드 개요(startup)에도 요약 1줄 노출
