# 인텐트 아키텍처 사양 — 레지스트리 + 훅 전환

인텐트(JSON 질의응답)가 늘수록 등록 지점이 흩어져 누락 회귀가 구조적으로
발생하던 문제("늘어나면 점점 늘어나는 구조")의 해소 로드맵. 3단계 점진 전환.

## 배경 — 분산 등록의 실사고 이력

| 등록 지점 | 사고 |
|---|---|
| `_DYNAMIC_SQL_INTENTS` 2곳 | 1곳 누락 → 빈 응답 (2026-07-11, EQUIPMENT_FAULT_STATUS) |
| classifier Stage1 키워드 | 누락 → SLM 폴백 분류 ~10초 지연 (2026-07-11) |
| 프런트 BotMessage 억제 목록 | 누락 → 카드 위 평문 중복 (2026-07-12, ANOMALY_HISTORY) |
| SSE 응답 필드 | 동기에만 있고 SSE 누락 → ml_* 배지 미표시 (2026-07-14 발견) |

전제 조건 (완료): Phase 5 로 인텐트 파이프라인이 SSE 한 곳으로 수렴,
채팅 스모크 16케이스 게이트 확보.

## 1단계 — 인텐트 메타 단일화 (완료 2026-07-15)

**원칙: 인텐트 추가 시 `example3.json` 한 곳에만 선언한다.**

### example3.json 확장 필드 (선택)

```jsonc
{
  "intent": "EQUIPMENT_FAULT_STATUS",
  "questions": [...], "sql": "...", "answer_template": {...}, "graph_type": "none",
  // SQL 템플릿 없이(또는 무시하고) 커스텀 핸들러가 rows 를 채우는 인텐트
  "dynamic_sql": true,
  // Stage1 분류 단축 키워드 — 질의에 포함되면 '공통' 카테고리 확정 (SLM 폴백 회피)
  "classify_keywords": { "stage1": ["설비 장애", "설비장애", "설비 고장", ...] }
}
```

### 파생 지점 (`intent_matching.py`)

- `dynamic_sql_intents()` → ai_server `_DYNAMIC_SQL_INTENTS_STREAM` (모듈 로드 1회)
- `stage1_keywords_from_definitions()` → classifier `_classify_category` 의
  공통 키워드에 합산 (lazy import + 캐시)

하드코딩에서 JSON 으로 이관된 것: dynamic 16개 인텐트 마킹 + Stage1 키워드
6그룹 27개 (교차검증/설비장애/센서스캔/물수지/누수/표준편차). classifier 의
하드코딩 리스트에는 **인텐트 무관 공통 신호만** 남음 (알람/트렌드/주소 등).

### 검증 (완료)
- 파생 dynamic 세트 == 기존 하드코딩+_SUPPLY_INTENTS (16개 완전 일치)
- 파생 stage1 ⊇ 이관 키워드 27개 (누락 0)
- 스모크 16/16 + 파생 키워드 실매칭 로그 확인 ('교차 검증'/'물 수지'/'설비 장애')

## 2단계 — 핸들러 레지스트리 (완료 2026-07-15)

SSE `event_generator` 의 인텐트별 인라인 분기 **42개 전부** 훅 클래스로 이관 —
event_generator 는 인텐트 분기 0개, 651줄 순수 파이프라인만 남음.

### 파이프라인 훅 (5개)

```
분류 → 추출 → [prepare] → 세션병합·검증 → SQL 준비 → [pre_sql]
  → (final_response 면 즉시 result·종료) → SQL 실행 → [post_sql]
  → no-data 체크 → process_sql_result → [post_process]
  → 렌더링 → [response_extras] → build_success_response
```

| 훅 | 시점 | 용도 |
|---|---|---|
| `prepare(ctx)` | 세션 병합 전 (new_params) | 기간·기본값·필터 보정 |
| `pre_sql(ctx)` | SQL 실행 전 | SQL 변형·rows 조달·템플릿 오버라이드·early-return(`ctx.final_response`) |
| `post_sql(ctx)` | 실행 직후 (no-data 전) | 폴백 조회 (예: 알람 7일) |
| `post_process(ctx, pd)` | process_sql_result 후 | processed_data/rows 보강 (예: SCAN_ALL 교차검증) |
| `response_extras(ctx, pd)` | 응답 조립 직전 | 추가 kwargs 반환 + response_data 교체 (예: CUSUM 요약) |

### 프레임 (`slm/intent_handlers/`)

- `base.py` — IntentContext(파이프라인 상태 왕복) + IntentHandler + `@intent_handler`
  레지스트리 + `init_services` (**getter 주입**: ai_server 전역 _CAUSAL_INDEX·캐시류는
  재할당되는 변수라 lambda 로 호출 시점 조회. save_csv/execute_sql 등 유틸도 주입)
- 도메인 모듈: `anomaly` `alarm` `trend` `night_min_flow` `reservoir` `tag_query`

### 이관 완료 42/42 (커밋 체인)

| 배치 | 범위 | 커밋 |
|---|---|---|
| 1차 | 교차검증/설비장애/물수지/tagtype (5) | `2b9adb7` |
| 2차 | prepare 계열+템플릿+SQL 생성 (17) | `82d5f36` |
| 3차 | 조달·early-return 계열 (13) | `99a9b6f` |
| 4차 | 후처리·extras 계열 (7) | `3a35c41` |

- 매 배치 게이트: 스모크 16/16 + /ask 68케이스 구조 비교 (최종 불일치 0건) + 실 UI 확인
- 주의 사례: from_ts==to_ts 보정은 TIMESERIES 조달보다 먼저여야 해서 훅 앞으로
  이동 (실행 순서 보존). 같은 인텐트의 복수 시점 분기는 한 핸들러의 여러 훅으로.

### 파라미터 규약 — `_` 접두 파생 키 [E-037]

`params` 에 넣는 **턴 한정 파생 정보**(오타 보정 이력 `_corrections` 등)는
반드시 `_` 접두 키를 사용한다. `session_manager.update_session` 이 `_` 접두
키를 세션 누적(slot-filling)에서 제외하기 때문 — 미준수 시 이전 턴의 파생
정보가 다음 턴 응답에 재표출된다 (E-037: '신편'→'신평' 보정 안내가 무관한
다음 질문에 붙던 버그). 반대로 **턴 간 유지해야 하는 슬롯**(sitename 등)은
`_` 없이.

### 후속 추가 인텐트 (아키텍처 검증 사례)
- `REPLACEMENT_PRIORITY_QUERY` (2026-07-16) — 선언 + 핸들러 파일 1개
  (`intent_handlers/replacement.py`)로 추가 완료. 3단계 아키텍처 의도대로
  ai_server 본체 무변경 (docs/equipment-health-priority-spec.md §채팅 인텐트)

**새 인텐트 추가 = example3.json 선언 + (커스텀 필요 시) 핸들러 클래스 1개.**

## 3단계 — 카드 타입 선언화 (완료 2026-07-15)

**백엔드가 카드를 선언하고 프런트는 따른다** — 카드 렌더·평문 억제의 단일 소스.

### 백엔드 (`slm@9156d70`)
- example3.json 인텐트 정의에 `card_type` 11종 선언 (anomaly_scan/detail/
  predict/pattern/history/compare, leak_cusum, alarm_history, cross_facility,
  flow_balance, equipment_fault)
- `build_success_response` 가 인텐트→card_type 맵(1회 캐시)으로 **자동 주입** —
  파이프라인·핸들러 수정 없이 모든 빌드 지점 적용

### 프런트 (`slm-dashboard@3cc666c`)
- mapper: ANOMALY 카드 게이트 `response.card_type` 우선
- BotMessage: `isCard(cardType, legacyIntent)` — 데이터 카드 3종 판정 +
  평문 억제 3종(alarm_history/leak_cusum/anomaly_history)
- VisualRenderer: ONGOING_ALARM 특례 → `alarm_history` 우선

### 하위호환 (필수 불변식)
**히스토리(tb_ai_chat_bot 저장 응답)에는 card_type 이 없다** → 모든 소비처는
인텐트 목록 폴백을 유지한다. 폴백 제거 금지 (과거 대화 재렌더 깨짐).
검증: 신규 응답 카드+평문 억제 정상 / 과거 대화 재렌더 카드 정상 / 스모크 16/16.

### 새 카드 추가 절차
1. example3.json 인텐트에 `card_type` 선언
2. 프런트 카드 컴포넌트 + mapper/BotMessage 의 card_type 매핑 1곳
3. (기존 인텐트 목록은 건드릴 필요 없음)

### 남은 후속
- Tier 3 `/admin/chat-gallery` 카드 갤러리 (미적 검수) — 별도 작업
- 신규 카드가 쌓이면 프런트 card_type→컴포넌트 매핑을 단일 레지스트리 객체로 승격 검토

## 인텐트 추가 절차 (1단계 기준)

1. `example3.json` 에 정의 추가 (+ 필요시 `dynamic_sql`, `classify_keywords.stage1`)
2. 커스텀 처리 필요 시: event_generator 분기 (2단계 후에는 핸들러 파일)
3. 채팅 스모크 케이스 추가 (`chat_smoke_cases.json`) → `python test_chat_smoke.py`
4. 복잡한 Stage2 규칙(다중 조건)이 필요하면 아직 `intent_classifier._classify_intent`
   코드 — 2단계에서 데이터화 검토
