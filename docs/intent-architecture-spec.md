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

## 2단계 — 핸들러 레지스트리 (1차 진행 2026-07-15)

SSE `event_generator` 의 인텐트별 인라인 분기(42개)를 훅 클래스로 점진 이관.

### 프레임 (`slm/intent_handlers/`, 완료)

```python
@intent_handler
class EquipmentFaultHandler(IntentHandler):
    intents = ("EQUIPMENT_FAULT_STATUS",)
    async def pre_sql(self, ctx): ...   # ctx.sql 변형 / ctx.rows 채우면 SQL 대체
    def response_extras(self, ctx, processed_data): ...  # 응답 추가 필드 (2차부터)
```

- `IntentContext`: intent/question/params/sql/rows/columns/extras
- **서비스 getter 주입** (`init_services`): ai_server 전역(_CAUSAL_INDEX,
  _ANOMALY_SCAN_CACHE, _FLOW_BALANCE_CACHE)은 재할당되는 변수라 값이 아닌
  lambda getter 로 주입 — 호출 시점에 현재 값
- 파이프라인 훅 포인트: SQL 실행 직전 1곳 (`pre_sql` → sql 반영, rows 설정
  시 기존 'if not rows' 게이트로 SQL 스킵)

### 이관 현황 (5/42 분기)

| 배치 | 인텐트 | 패턴 | 상태 |
|---|---|---|---|
| 1차 | ANOMALY_CROSS_FACILITY | rows 대체 (causal 교차검증) | ✅ `2b9adb7` |
| 1차 | EQUIPMENT_FAULT_STATUS | rows 대체 (스캔 캐시 재사용) | ✅ |
| 1차 | ANOMALY_FLOW_BALANCE | rows 대체 (30분 캐시+계산) | ✅ |
| 1차 | FACILITY_TAG_LATEST_VALUE / TAG_DATA_TABLE | SQL 변형 (tagtype 주입) | ✅ |
| 후속 | 나머지 37개 분기 (트렌드 기간·stddev·CUSUM·SCAN_ALL early-return·후처리) | — | 계획 |

- 매 배치 게이트: 스모크 16 (+대규모 배치는 /ask 68 비교)
- 새 인텐트 = JSON 선언 + (필요시) 핸들러 파일 1개

## 3단계 — 카드 타입 선언화 (계획)

백엔드 응답에 `card_type` 필드 선언 → 프런트는 인텐트 목록
(chat-response-mapper ANOMALY_INTENTS, BotMessage 억제 목록) 대신 card_type
레지스트리로 렌더. Tier 3 카드 갤러리와 함께 진행.

## 인텐트 추가 절차 (1단계 기준)

1. `example3.json` 에 정의 추가 (+ 필요시 `dynamic_sql`, `classify_keywords.stage1`)
2. 커스텀 처리 필요 시: event_generator 분기 (2단계 후에는 핸들러 파일)
3. 채팅 스모크 케이스 추가 (`chat_smoke_cases.json`) → `python test_chat_smoke.py`
4. 복잡한 Stage2 규칙(다중 조건)이 필요하면 아직 `intent_classifier._classify_intent`
   코드 — 2단계에서 데이터화 검토
