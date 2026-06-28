---
name: 알람 진단 — 펌프 제어모드 게이트 사양
status: v1 — Phase A 사양 완료 / Phase B 1차(S2 게이트) 배포 완료 (2026-06-29)
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
- **두 진단 경로(배선 추적 결과)**:
  - (가) "펌프 조건검토 의사결정 트리(19단계)" 노드 — C1/C2→S1→S2→P1… cause_code 산출.
  - (나) **"가동조건 설정 오류로 인한 펌프 미가동"의 실제 생산 경로**: 홀딩/정상 →
    펌프모드 쿼리 → 수동/자동 switch → 가동조건 쿼리 → **미충족/충족 switch →
    S2 writer(`d5f8e543ad132d96`)**. ⚠️ 19단계 트리와 **별개 경로**이며, 보고된
    죽동 오분류는 (나) 경로에서 발생. 트리(가)는 S2 writer 로 연결되지 않음.
- 최종 `diagnosed_cause` 텍스트는 트리가 아니라 **writer 노드**가 하드코딩해 UPDATE.

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

### 7.1 1차 배포 완료 (2026-06-29) — S2 writer 게이트
보고된 죽동 오분류의 생산 경로(§2 (나))인 **S2 writer(`d5f8e543ad132d96`)의
UPDATE WHERE 에 fail-safe 게이트 SQL**을 추가. 트리/CTE/params 배선 변경 없이
writer 한 노드만 수정(자기검증형):

```sql
AND NOT EXISTS (
    SELECT 1 FROM tb_facility_flow_map f
    JOIN tb_service_booster_station_status b ON b.sitename = f.upstream_sitename
    WHERE f.downstream_sitename = '${sitename}'
      AND f.downstream_facilitytype = '${facilitytype}'
      AND f.upstream_facilitytype = '가압장'
      AND (b.pump_control_mode LIKE '%압력%' OR COALESCE(b.running_pump_count,0) > 0)
)
```
- **효과**: 전단 가압장이 압력제어이거나 펌프 실가동 중이면 "가동조건 설정
  오류로 인한 펌프 미가동"을 **쓰지 않음**(diagnosed_cause 는 빈 채로 두어 수지
  기반 원인이 채우도록).
- **fail-safe**: 전단 가압장 토폴로지 매핑(`tb_facility_flow_map`)이 없으면
  NOT EXISTS=true → **기존 동작 그대로**(과잉 억제 없음).
- **백업/롤백**: `dev-data/noderered-backups/flows.live.20260629.json`(원본),
  `flows.patched.20260629.json`(배포본). 롤백 = 원본을 `/data/flows.json`로 복사
  후 `docker restart slm-node-red`.
- **검증**: 게이트 SQL DB 검증(죽동 차단 / 행정·신평 유지), 최종 UPDATE 파싱
  (BEGIN/ROLLBACK, 0행), 배포 후 node-red healthy + "Started flows" + 노드 1040개
  보존 + 신규 에러 0. **런타임 억제는 신규 죽동 HH 알람 발생 시 확인 필요(모니터링).**

### 7.2 잔여 (후속 Phase)
1. 같은 게이트를 S1·P1/P2/P4·V1/V2·원인10/11(supply-time 미가동) writer 로 확대.
2. §5.1a 수위제어 가압장의 임계(pump_start/stop_threshold) 기반 기대상태 계산.
3. §5.3 경합 제거(허용 원인 화이트리스트/우선순위).
4. §6 데이터 보강(linked_reservoir_name·토폴로지 매핑 2→23 → 게이트 커버리지 확대).

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
- 2026-06-29 Phase B 1차 배포 — 배선 추적으로 S2 생산 경로가 19단계 트리가 아닌
  펌프모드→가동조건 chain 임을 확인(§2 정정). S2 writer 에 fail-safe 게이트 SQL
  배포(§7.1). 죽동 차단·행정/신평 유지 DB 검증, node-red 무중단 재기동 확인.
