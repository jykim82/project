---
name: 알람 진단 — 펌프 제어모드 게이트 사양
status: v1 사양 (구현 대기 — Node-RED 진단 파이프라인)
created: 2026-06-29
updated: 2026-06-29
---

# 알람 진단 펌프 제어모드 게이트 사양

## 1. 목적

배수지 수위 알람(HH/LL)의 근본원인(`tb_equipment_alarm_report.diagnosed_cause`)
진단이 **가압장 제어 방식(`pump_control_mode`)과 실제 펌프 상태를 무시**하고
모든 가압장을 "배수지 수위 연동"으로 가정해 오분류하는 문제를 바로잡는다.

대표 증상(2026-06): **죽동(배) 수위#1 HH 알람**에 "가동조건 설정 오류로 인한
펌프 미가동"이 96건 부여. 그러나 죽동 가압장은 **압력 제어 방식**이고 펌프는
**실제 가동 중**(`running_pump_count=1`)이라, 이 진단은 사실·제어모드 양쪽으로
틀렸다.

## 2. 현행 구조

- **생성 주체**: `slm-node-red` 컨테이너의 룰 기반 진단 파이프라인이
  `diagnosed_cause`/`countermeasure`를 생성해 `tb_equipment_alarm_report`에
  UPDATE/INSERT. 제품 백엔드(FastAPI)는 **조회·집계·표시만** 한다.
- **핵심 노드**: "펌프 조건검토 의사결정 트리(19단계)" — 펌프 미가동 전제로
  C1/C2(정전·통신)→S1(설정오류)→**S2(가동조건 설정 오류)**→P1(FAULT)→… 분기.
- **파라미터 수집**: "전체 파라미터 수집 쿼리(v2)" CTE 가 other_pump / comm_status
  / pump_mode(auto·manual) / run_condition(SET 태그) 만 수집.

## 3. 결함 (3종)

### ① 제어모드 무시 — 핵심
19단계 트리는 "펌프가 돌아야 하는데 안 돈다"(= **LL/유입 펌프 ON 기대**)
시나리오 전용인데, **HH(상한)** 에도 그대로 적용해 "펌프 OFF = 설정 오류"로
단정. 인터록 사양(`ai_server.py` SI_RESERVOIR_01: "수위 HH → 상류 가압장 펌프
정지")상 **HH에서 펌프 정지는 정상**이다.
- 코드 증거: "전단시설 판정(HH)"·"펌프작동조건(H)" 노드의
  `_pump_condition='DEFAULT'`, `_pump_condition_match=true`, 그리고 주석
  `// TODO: tb_service_booster_station_status 연동 수위 기준(HH/LL) 연동`.

### ② 실제 펌프 상태 미검증
`running_pump_count`/운전 DI 태그를 확인하지 않고 "미가동"을 결론. 죽동은
펌프 가동 중인데 "미가동"이 96건 부여됨.

### ③ 진단 노드 경합 (first-writer-wins)
모든 진단 UPDATE 가 `WHERE diagnosed_cause IS NULL OR ''`(또는
`countermeasure='펌프검증중'`)라 **먼저 쓴 노드가 이김**. 발생 시점마다 다른
원인이 박혀 한 알람에 모순 원인("펌프 미가동"↔"펌프 미정지")이 혼재.

## 4. 데이터 기반 (이미 존재 — 미사용)

| 컬럼 | 테이블 | 채움(2026-06) | 용도 |
|---|---|---|---|
| `pump_control_mode` | tb_service_booster_station_status | **23/23** | 연동 여부: "배수지 수위 제어 방식"(2) / "가압장 압력 제어 방식"(21) |
| `pump_start_threshold` | 〃 | 0/23 | 펌프 가동 수위 (미입력) |
| `pump_stop_threshold` | 〃 | 0/23 | 펌프 정지 수위 (미입력) |
| `linked_reservoir_name` | 〃 | 2/23 | 가압장↔배수지 매핑 |
| `current_water_level` | tb_service_reservoir_status | populated | 배수지 현재 수위 |
| `alarm_high_water_level` / `alarm_low_water_level` | 〃 | populated | 배수지 HH/LL 임계 |

**임계 대체 규칙**: `pump_start/stop_threshold`가 비면 배수지
`alarm_low_water_level`(가동 기준)·`alarm_high_water_level`(정지 기준)을
연동 기준으로 대체 채택한다.

## 5. 진단 규칙 (신규)

### 5.1 제어모드 게이트 (트리 진입 전)
대상 알람의 가압장 `pump_control_mode`로 분기한다.

**(a) "배수지 수위 제어 방식"** — 펌프가 배수지 수위에 연동:
- 기대 펌프 상태 = f(배수지 수위, 임계)
  - 수위 ≥ `pump_stop_threshold`(없으면 HH 임계) → **기대 OFF**
    → 펌프 OFF 면 **정상 인터록**(진단 없음 또는 "정상 범위 복귀 예정")
    → 펌프 ON 이면 **"펌프 미정지"**
  - 수위 ≤ `pump_start_threshold`(없으면 LL 임계) → **기대 ON**
    → 펌프 OFF 면 기존 19단계 미가동 트리(S1/S2/P1…) 적용 (진짜 고장 후보)

**(b) "가압장 압력 제어 방식"** — 펌프가 압력 제어, 배수지 수위 비연동:
- 배수지 수위 알람에 **"펌프 미가동/가동조건 설정 오류" 진단 금지**.
- 펌프 실가동 + 배수지 HH → **"펌프 미정지"** 또는 수지 기반("유입량>유출량").
- 원인은 유입/유출 수지(flow-balance) 중심으로 산출.

### 5.2 실제 펌프 상태 검증 (선행 가드)
"미가동" 계열 결론 전, `running_pump_count > 0` 또는 운전 DI 태그=1 이면
**"미가동" 진단을 차단**한다 (사실 모순 방지).

### 5.3 경합/우선순위
한 알람당 **결정적 1개 진단**만 남긴다.
- 방향(HH/LL)·제어모드별 **허용 원인 화이트리스트**로 모순 원인 기록 차단, 또는
- 우선순위(정전 > FAULT > 설정오류 > 수지) 정렬 후 최상위만 기록.

## 6. 데이터 보강 (제품 품질)

1. `linked_reservoir_name` 2→23 채움 (가압장↔배수지 매핑 확정).
2. 수위 제어 가압장의 `pump_start/stop_threshold` 입력, 또는 §4 임계 대체 규칙
   공식 채택.
3. **Admin UI/시드 migration** 으로 관리 (하드코딩 금지, region 격리 유지).
4. 롤백: 신규 시드는 별도 migration, 기존 행 미파괴(UPSERT) 가정.

## 7. 구현 (Node-RED) — Phase B

1. "전체 파라미터 수집 쿼리(v2)" CTE 에
   `pump_control_mode` / `linked_reservoir_name` / 배수지
   `current_water_level`·`alarm_high/low_water_level` 수집 추가.
2. 트리 진입부에 **제어모드 게이트 스위치 노드** 삽입 (§5.1).
3. "미가동" 노드군 앞에 **실가동 가드** (§5.2).
4. **백업 절차**: 변경 전 Node-RED `flows` export(JSON) → `dev-data/` 백업.
   거대 단일 JSON 수기 편집 금지, 에디터 import/배포로 안전 적용.

## 8. 검증 기준

- 죽동(배) 수위#1 HH → "가동조건 설정 오류로 인한 펌프 미가동" **소멸**,
  "펌프 미정지" 또는 "유입량>유출량(수지)"으로 대체.
- 행정(배수지 수위 제어) HH 시 펌프 OFF → 고장 진단 없음(정상 인터록).
- 임의 알람에 모순 원인(미가동↔미정지) **동시 기록 0건**.

## 9. 폐쇄망/멀티테넌시 제약

외부 의존 없음(전 로직 로컬). `pump_control_mode`/임계는 납품처별 상이 →
config·시드 분리. region 기반 격리 유지.

## 10. 이력

- 2026-06-29 v1 작성 — 죽동 HH 오분류 분석에서 도출. `pump_control_mode`(23/23)
  미사용 결함 + 실가동 미검증 + 경합 3종. Phase A(사양)/B(구현) 분리.
