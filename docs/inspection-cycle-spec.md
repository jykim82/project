# 설비 점검 주기 도래 사양 v1

설비유형별 점검 주기를 마스터로 두고, **마지막 점검 기록에서 다음 도래를
계산**해 인수인계 화면에 보여준다. 지금은 점검 시기를 사람이 기억한다.

**관련**: `docs/shift-handover-spec.md`(표시 위치),
`docs/report-spec.md`(일 점검 보고서 — 점검 기록의 원천),
`docs/equipment-fault-tracking-spec.md`(tb_task_master)

---

## 1. 데이터 현실 (2026-07-27 조사)

| 항목 | 상태 |
|---|---|
| 점검 주기 마스터 | **없음** — `tb_equipment_lifespan` 은 교체 주기(년)지 점검 주기가 아니다 |
| 설비 설치일자 | 0/295 — 내용연수 기반 계산은 아직 불가 |
| 점검 기록 | `tb_task_master` 점검 5·정비 7·청소 1건 (희소하지만 존재) |

따라서 v1 은 **주기 마스터 신설 + 기록 기반 도래 계산**이다. 기록이 없는
설비는 "점검 기록 없음"으로 보여준다 — 이것도 중요한 신호다(숨기지 않는다).

## 2. 데이터 모델 — Migration 0130

```sql
tb_inspection_cycle (
  region        varchar(10)  NOT NULL,
  equipmenttype varchar(50)  NOT NULL,   -- tb_equipment_info.equipmenttype 과 매칭
  cycle_days    integer      NOT NULL,   -- 점검 주기 (일)
  note          text,
  PRIMARY KEY (region, equipmenttype)
)
```

- **equipmenttype 단위**다. 설비 개별 주기는 과설계 — 현장 규정은 유형
  단위(펌프 월 1회 등)로 정해진다. 개별 예외가 필요해지면 그때 컬럼 추가.
- 시드는 보수적 기본값(§부록). 값 자체는 현장 규정 확정 필요 — **시드는
  출발점이지 규정이 아니다.** 편집 UI 는 P2 (마스터 테이블이므로 SQL 로
  조정 가능, 하드코딩 아님).

## 3. 도래 계산 — `GET /inspection/due`

```
마지막 점검일 = max(tb_task_master.task_start_time)
               WHERE task_category IN ('점검','정비','청소')
                 AND (equipment_id 매칭 또는 sitename+equipmenttype 매칭)
다음 예정일   = 마지막 점검일 + cycle_days
상태          = overdue (예정일 경과) | due_soon (7일 내) | ok | never (기록 없음)
```

- **조회 시점 계산이다. cron·배치 없음** — 설비 295 × 기록 수백 건 규모라
  집계 SQL 한 번이면 된다 (저사양 전제).
- `never` 는 주기 마스터에 유형이 있는 설비만 대상 — 주기가 정의되지 않은
  유형(예: 임의 등록 설비)까지 "기록 없음"으로 도배하지 않는다.
- 정렬: overdue(경과일 큰 순) → due_soon → never. `ok` 는 기본 미포함
  (`include_ok=1` 로 요청 시만).

## 4. 표시 — 인수인계 화면 섹션 (신규 메뉴 없음)

`/reports/shift-handover` 의 "다음 근무 예정 일정" 아래 **"점검 도래 설비"**
섹션. 교대 시 "이번 근무에 할 점검"으로 넘어가는 것이 자연스럽고, 점검
도래는 근무 구간과 무관하므로 구간 토글의 영향을 받지 않는다(명시).

각 행: 설비(현장·유형) · 마지막 점검일 · 예정일 · 상태 배지 +
**"일정으로 등록" 버튼** — 기존 `createSchedule`(tb_user_schedule) 재사용.

- **자동 일정 생성은 하지 않는다.** 등록은 운영자의 명시 행동이다
  (자동 생성은 중복·소유자 문제를 만들고, 자동 조치 금지 원칙과 같은 계열).
- 등록된 일정은 기존 30초 폴링 팝업·인수인계 upcoming 에 자연히 합류한다.

## 5. 하지 않는 것

- 내용연수(교체) 계산과 섞지 않는다 — 교체 후보는
  `/monitoring/equipment-health` 소관 (`equipment-health-priority-spec`).
- 점검 완료 처리 없음 — 완료는 기존 경로(채팅 점검 기록·일 점검 보고서)로
  기록하면 다음 조회에서 도래가 갱신된다. 별도 상태 관리를 만들지 않는다.

## 6. 후속 (P2)

- 주기 마스터 편집 UI (구축 > 장비 설정 탭)
- 설치일자 데이터가 채워지면: 기록 없는 설비의 기준일을 설치일로 폴백
- 채팅 인텐트 "점검 도래 설비 알려줘"

---

## 부록 — 시드 기본값 (현장 규정으로 대체 전제)

시드는 **실제 등록 설비 유형**(`tb_equipment_info.equipmenttype` DISTINCT)
기준으로 넣는다 — 관념적 유형(인버터·수위계 등)은 현 DB 에 없어 시드해도
매칭 0 이다.

| equipmenttype | 대수 | cycle_days | 근거 |
|---|---:|---|---|
| 가압펌프 | 72 | 30 | 회전기기 월 점검 관행 |
| PLC | 89 | 90 | 분기 |
| 유량계 | 40 | 180 | 반기 (계측 교정 주기와 별개) |
| LTE 모뎀 | 49 | 180 | 반기 |
| L2 스위치 · L3 스위치 | 9 | 180 | 반기 |
| UTM | 3 | 180 | 반기 |

서버·PC 류(관망관리 서버 등 소수 대수)는 시드하지 않는다 — IT 자산 점검은
현장 설비 점검과 규정 체계가 달라, 필요 시 현장이 행을 추가한다.
