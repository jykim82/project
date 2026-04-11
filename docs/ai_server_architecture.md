# AI Server Architecture

> 최종 업데이트: 2026-04-11 — Phase 1+2+3 + 추가 분할 완료 (13,256줄 → 5,662줄, 22개 모듈)

## 1. 전체 구조

```
slm/
├── ai_server.py              (5,662줄) ← 코어: app, lifespan, DB pool, AI 채팅(/ask), 인텐트 매칭
├── response_builder.py       (2,170줄) ← 응답 조립: JSONB 파서, 템플릿 렌더러, 응답 빌더, process_sql_result
├── sql_executor.py           (1,138줄) ← SQL 쿼리 실행기: 시계열/야간유량/헌팅체크 등 DB 조회
├── block_builder.py            (707줄) ← UI 블록 빌더: build_*_block, wrap_status_marker 등
├── anomaly_scan.py             (646줄) ← 이상감지 전체 스캔 + 데이터 품질/설비 장애 감지
├── shared/
│   ├── __init__.py
│   └── timeseries.py          (103줄)  ← TimescaleDB 청크 쿼리 공용 헬퍼
├── endpoints/                           ← FastAPI APIRouter 모듈 (16개)
│   ├── admin.py               (645줄)  ← /health, /models/*, /admin/*, /anomaly/profiles
│   ├── alarm_contacts.py      (182줄)  ← /crisis/alarm-contacts CRUD
│   ├── alarm_crisis.py        (860줄)  ← /alarm/*, /crisis/*, /monitoring/alarm-notifications
│   ├── auth_crud.py           (844줄)  ← /auth/* 인증/권한
│   ├── canvas_crud.py         (403줄)  ← /canvas/* 레이아웃
│   ├── causal.py              (462줄)  ← /causal/* 인과관계 체인
│   ├── csv_import.py          (636줄)  ← /import/* CSV 일괄 가져오기
│   ├── dashboard.py           (353줄)  ← /dashboard/overview, /monitoring/dashboard
│   ├── facility_crud.py       (329줄)  ← 설비(Equipment) CRUD
│   ├── facility_types_crud.py(1,252줄) ← 배수지/가압장/감압/블록 시설유형 CRUD
│   ├── flow_map_crud.py       (319줄)  ← /flow-map CRUD
│   ├── flow_realtime.py       (855줄)  ← /flow-map/realtime, /gis/*, /equipments/auto-map
│   ├── monitoring_catalogs.py (312줄)  ← /monitoring/catalogs/*
│   ├── network_crud.py        (882줄)  ← /network/* 통신장비
│   ├── tags.py                (194줄)  ← /tags, /tags/filters, /tags/groups
│   └── trend.py               (313줄)  ← /trend/data, /trend/explain, /trend/facility-sparkline
├── anomaly_detector.py                  ← 이상감지 룰엔진
├── anomaly_iforest.py                   ← Isolation Forest v2 다변량 이상감지
├── causal_estimator.py                  ← 교차상관 기반 시간 지연 추정
├── db_sync.py                           ← 원격→로컬 DB 동기화 워커
├── flow_balance.py                      ← 물수지 균형 계산
├── intent_classifier.py                 ← AI 인텐트 분류 (keyword/vector/SLM)
├── intent_embeddings.py                 ← 임베딩 벡터 인덱스
├── intent_index.py                      ← 인텐트 정의 인덱스
├── korean_fuzzy.py                      ← 한국어 퍼지 매칭
├── ollama_client.py                     ← Ollama LLM 클라이언트
├── param_extractor.py                   ← 질문에서 파라미터 추출
├── query_validator.py                   ← SQL 쿼리 검증/보정
├── session_manager.py                   ← 채팅 세션 관리
├── site_profiler.py                     ← 현장 프로파일링 (A/B/C/D 등급)
├── slm_config.py                        ← 서버 설정 (모델, URL, 타임아웃)
├── snmp_poller.py                       ← SNMP 네트워크 장비 모니터링
└── example3.json                        ← 인텐트 정책 정의 (73개 INTENT, 723개 질문)
```

## 2. ai_server.py 코어 구성 (5,650줄)

| 영역 | 줄 범위 | 설명 |
|------|--------|------|
| 설정/임포트/상수 | 1~350 | 인과 체인 템플릿, 설비 규칙, 글로벌 캐시 |
| 태그분류/인과인덱스/설비매핑 | 350~870 | 백그라운드 인덱스 구축 |
| CSV/백그라운드 루프 | 870~1175 | CSV 내보내기, 프로파일링, SNMP, IForest, 캐시 루프 |
| lifespan/데모/미들웨어 | 1175~1665 | 서버 시작/종료, 데모 모드, CORS |
| DB풀/인텐트로딩/매칭 | 1665~2460 | 커넥션 풀, execute_sql, 인텐트 매칭 |
| 라우터등록 | 2460~2560 | 모듈 init + include_router |
| AI 채팅 (/ask, /ask/stream) | 2560~5535 | 질의응답 핵심 로직 (SSE 스트리밍) |
| CSV다운로드/자동완성 | 5535~5650 | 유틸 엔드포인트 |

## 3. 모듈별 의존성 매트릭스

| 모듈 | DB | Ollama | 캐시 | 인과인덱스 | 기타 |
|------|:--:|:------:|:----:|:---------:|------|
| response_builder.py | O | - | O | O | iforest_manager, anomaly_detector |
| anomaly_scan.py | O | - | - | O | execute_sql, process_sql_result |
| endpoints/admin.py | O | O | - | - | session_manager, site_profiler, _ai_settings |
| endpoints/trend.py | O | O | - | - | psycopg2 직접 연결 |
| endpoints/causal.py | O | - | - | O | causal_estimator |
| endpoints/alarm_crisis.py | O | - | - | - | 독립 |
| endpoints/tags.py | O | - | - | - | 독립 |
| endpoints/dashboard.py | O | - | O | - | _ANOMALY_SCAN_CACHE, _FLOW_BALANCE_CACHE |
| endpoints/flow_realtime.py | O | - | O | - | 3개 캐시 + shared.timeseries |
| endpoints/facility_crud.py | O | - | - | - | 독립 |

## 4. 공유 모듈

### shared/timeseries.py
| 함수 | 용도 | 사용처 |
|------|------|--------|
| `get_chunks_for_range()` | 시간 범위의 청크 목록 | trend, causal, flow_realtime, response_builder, ai_server |
| `query_chunks_agg()` | time_bucket 집계 | trend, response_builder |
| `reaggregate()` | 크로스-청크 재집계 | trend, response_builder |
| `query_chunks_raw()` | 원시 행 조회 | causal, flow_realtime, response_builder |

### response_builder.py
| 카테고리 | 주요 함수 |
|----------|----------|
| JSONB 파서 | parse_general_overview, parse_reservoir_*, parse_booster_* |
| 템플릿 렌더러 | render_answer_template, apply_corrections_to_answer |
| 응답 빌더 | build_success_response, build_error_response, build_no_data_response |
| 블록 빌더 | build_hunting_result_block, build_level_detail_block 등 20+ |
| SQL 실행기 | _execute_night_min_flow_query, _execute_timeseries_query 등 10+ |
| 핵심 프로세서 | process_sql_result (SQL 결과 → 응답 변환) |

## 5. 백그라운드 캐시

| 캐시 | TTL | 용도 | 참조 모듈 |
|------|-----|------|----------|
| `_ANOMALY_SCAN_CACHE` | 5분 | 이상감지 종합 결과 | dashboard, flow_realtime, response_builder |
| `_FLOW_BALANCE_CACHE` | 30분 | 물수지 균형 | dashboard, flow_realtime |
| `_FLOW_BASELINE_CACHE` | 10분 | 7일 평균 기준선 | flow_realtime |
| `_CAUSAL_INDEX` | 서버 시작 | 인과관계 인덱스 | causal, response_builder, anomaly_scan |

## 6. API 라우트 총정리

### ai_server.py (4개 — 코어)
- `POST /ask` — AI 질의응답
- `POST /ask/stream` — AI 질의응답 SSE 스트리밍
- `GET /csv/{filename}` — CSV 다운로드
- `GET /autocomplete/candidates` — 자동완성 후보

### endpoints/ 모듈 (60+개)
- **admin.py** (8): /health, /anomaly/profiles, /models, /models/select, /admin/facility-files/*, /admin/site-settings
- **trend.py** (3): /trend/data, /trend/explain, /trend/facility-sparkline
- **causal.py** (6): /causal/rules, /causal/verify, /causal/chain/*, /causal/estimate-lag
- **alarm_crisis.py** (12): /monitoring/alarm-notifications, /alarm/*, /crisis/*
- **tags.py** (3): /tags, /tags/filters, /tags/groups
- **dashboard.py** (2): /dashboard/overview, /monitoring/dashboard
- **flow_realtime.py** (4): /flow-map/realtime, /gis/facility-info, /flow-map/node-alarms, /equipments/auto-map
- **기존 모듈** (20+): facility_crud, auth_crud, alarm_contacts, monitoring_catalogs, flow_map_crud, csv_import, canvas_crud, network_crud

## 7. 리팩토링 이력

| 날짜 | 작업 | 줄 수 |
|------|------|:-----:|
| 이전 | 원본 | 15,084 |
| 이전 | 1차 분리 (8개 모듈) | 13,256 |
| 2026-04-10 | Phase 1: trend, causal, alarm_crisis, tags | 11,562 |
| 2026-04-10 | Phase 2: dashboard, flow_realtime | 10,555 |
| 2026-04-10 | Phase 3: response_builder, anomaly_scan, admin | 5,650 |
| 2026-04-11 | 추가 분할: response_builder→3개, facility_crud→2개 | **5,662** |

**총 감소: 15,084 → 5,662 (62.5% 감소), 22개 모듈**

## 8. 개발 가이드

### 새 모듈 추가 절차
1. `endpoints/새모듈.py` 생성 (router + init 패턴)
2. `ai_server.py`에 import + init + include_router 추가
3. 원본 엔드포인트 코드 삭제
4. 구문 검증 + 서버 기동 테스트

### 의존성 주입 패턴
```python
# 모듈
router = APIRouter()
_get_db_connection = None
def init(get_db_connection_fn, ...):
    global _get_db_connection
    _get_db_connection = get_db_connection_fn

# ai_server.py
from endpoints.xxx import router, init
init(get_db_connection, ...)
app.include_router(router)
```

### 파일 크기 기준
| 기준 | 줄 수 |
|------|-------|
| 이상적 | 300~500줄 |
| 허용 가능 | 500~800줄 |
| 리팩토링 권장 | 800~1,500줄 |
| 반드시 분리 | 1,500줄 초과 |
