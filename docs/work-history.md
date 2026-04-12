## 현재 작업 상태
- 작업 진행할 때마다 CLAUDE.md의 "현재 작업 상태" 섹션을 업데이트해.
- 완료된 항목, 진행 중인 항목, 남은 항목을 정리해둬.

### 완료 (2026-04-12 — NEXTAUTH_URL 서버 IP 자동 감지 + 개발 환경 개선)

#### NEXTAUTH_URL IP 자동 감지 (다른 PC 접속 로그인 오류 해결)
- **증상**: 같은 망의 다른 PC에서 `https://192.168.219.105:3000` 접속 시 `/api/auth/error`로 포워딩, jykim 계정 로그인 실패
- **원인**: `.env.local`의 `NEXTAUTH_URL=https://localhost:3000` 하드코딩 → NextAuth가 콜백을 localhost로 리다이렉트하여 세션 검증 실패
- **해결**: `package.json`에 `_ip` 헬퍼 스크립트 추가 — `os.networkInterfaces()`로 외부 IPv4 주소 자동 감지
  - `dev`, `dev:fast`, `dev:http`, `dev:http:fast`, `start` 모든 스크립트에 `NEXTAUTH_URL=https://$(npm run -s _ip):3000` 주입
  - `.env.local`에서 `NEXTAUTH_URL` 라인 제거 (스크립트 주입값 우선)
- **효과**: 어떤 서버(Mac/Linux/Windows)에 배포해도 **현재 서버의 IP가 자동으로 NEXTAUTH_URL에 적용**됨 — 하드코딩 불필요
- **파일**: `slm-dashboard/package.json`, `slm-dashboard/.env.local`

#### 개발 환경 개선
- **Ollama 모델 교체**: `gemma4:latest` → `gemma4:26b` (A4B IT, MoE 4B 활성, 17GB)
  - 한국어 품질 향상, MoE 구조로 속도는 4B급 유지
  - Mac mini M4 Pro 48GB 환경에서 검증 완료
- **Node-RED**: Docker `slm-node-red` 컨테이너 확인 (포트 1880), 글로벌 npm 버전 제거
- **Claude Code Discord 채널 연동**: 작업 완료 알림 + 핸드폰에서 지침 전송 병렬 제어
  - `claude-plugins-official` 마켓플레이스 등록, `discord@claude-plugins-official` 플러그인 설치
  - Bun 런타임 설치 (`brew install oven-sh/bun/bun`)
- **Claude Code 권한 설정**: `bypassPermissions` 모드 + allow 목록(Bash, Grep, Glob, Read, WebFetch, WebSearch, Agent)

---

### 완료 (2026-04-11 — Docker 환경 이전 + 수위 원인 분석 + 알람 자동 해제 + 트렌드 수정)

#### Docker 개발 환경 이전
- **docker-compose.dev.yml**: TimescaleDB + Node-RED + Backend(FastAPI) + Frontend(Next.js) 통합
- **Backend Dockerfile**: Python 3.12-slim, 볼륨 마운트(hot-reload), uvicorn --reload
- **Frontend Dockerfile**: Node 22, HTTPS (mkcert 인증서), Turbopack
- **INTERNAL_API_URL**: 서버사이드 프록시 502 수정 (Docker 내부: `backend:8000`)
- **OLLAMA_MODEL**: `gemma4` → `gemma4:26b` (실제 모델명)
- `docker compose -f docker-compose.dev.yml up -d` 로 전체 스택 실행

#### RESERVOIR_LEVEL_CAUSE_ANALYSIS 인텐트 추가
- "배수지 수위 하락/상승 이유" 질문 → Node-RED 수위 조건 로직 기반 원인 분석
- 조건 체크: 상류 펌프 상태, 밸브 상태, 유입/유출 균형, 공급가능시간
- sql_executor.py: `_execute_level_cause_analysis` (10개 헬퍼 함수)
- block_builder.py: `build_level_cause_block` (시맨틱 마커 포맷)
- intent_classifier.py: "수위" + "이유/원인/왜" 우선 키워드 매칭

#### 오래된 알람 자동 해제
- **문제**: Node-RED가 과거 '진행중' 알람을 소급 해제하지 않음 (죽동 배수지 132건 미해제)
- **해결**: `_alarm_release_loop` 백그라운드 (2분 주기) — DI 태그 최신값=0이면 자동 해제
- **결과**: 전체 194건 → 52건 (142건 해제), 죽동 132건 → 4건 (LL 실제 활성만 유지)

#### 트렌드 AI 요약 빈 응답 수정
- **원인**: gemma4:26b 모델이 `options.num_predict` 지정 시 빈 응답 반환
- **해결**: `num_predict=150` → `None` (모델 기본값 사용)

#### response_builder 추가 분할 + facility_crud 분할
- response_builder.py (3,908줄) → response_builder(2,170) + sql_executor(1,138) + block_builder(707)
- facility_crud.py (1,551줄) → facility_crud(329) + facility_types_crud(1,252)

---

### 완료 (2026-04-11 — ai_server.py 모듈 분리 리팩토링 Phase 1+2+3)

#### 개요
- **목표**: ai_server.py 단일 파일(13,256줄)을 기능별 모듈로 분리하여 유지보수 가능한 구조로 전환
- **결과**: 13,256줄 → **5,650줄** (57.4% 감소), 신규 모듈 9개 생성
- **검증**: 구문 검증 11/11 PASS, API 테스트 10/10 PASS (DB 연결 포함)

#### Phase 1 — 독립 CRUD 모듈 분리 (13,256 → 11,562줄)
- `endpoints/trend.py` (313줄) — `/trend/data`, `/trend/explain`, `/trend/facility-sparkline`
- `endpoints/causal.py` (462줄) — `/causal/rules`, `/causal/verify`, `/causal/chain/*`, `/causal/estimate-lag`
- `endpoints/alarm_crisis.py` (860줄) — `/monitoring/alarm-notifications`, `/alarm/*`, `/crisis/*` (12개 엔드포인트)
- `endpoints/tags.py` (194줄) — `/tags`, `/tags/filters`, `/tags/groups`
- `shared/timeseries.py` (103줄) — TimescaleDB 청크 쿼리 공용 헬퍼 (trend, causal, flow_realtime 공유)

#### Phase 2 — 대시보드 + 실시간 분리 (11,562 → 10,555줄)
- `endpoints/dashboard.py` (353줄) — `/dashboard/overview`, `/monitoring/dashboard`
- `endpoints/flow_realtime.py` (855줄) — `/flow-map/realtime`, `/gis/facility-info`, `/flow-map/node-alarms`, `/equipments/auto-map`

#### Phase 3 — 응답 빌더 + 이상감지 + 관리자 분리 (10,555 → 5,650줄)
- `response_builder.py` (3,908줄) — JSONB 파서, 템플릿 렌더러, 응답 빌더, 블록 빌더, SQL 실행기, process_sql_result
- `anomaly_scan.py` (646줄) — `_compute_anomaly_scan_all` + 데이터 품질/설비 장애 감지 헬퍼
- `endpoints/admin.py` (645줄) — `/health`, `/models/*`, `/admin/facility-files/*`, `/admin/site-settings`

#### 아키텍처 문서
- `slm/docs/ai_server_architecture.md` — 전체 폴더 구조, 모듈 의존관계, 엔드포인트 매핑, 개발 가이드

#### ai_server.py 코어 (5,650줄)에 남은 기능
- AI 채팅 `/ask`, `/ask/stream` 핸들러 (인텐트 매칭 + SSE 스트리밍)
- 인과 체인 템플릿/인덱스 (글로벌 상수)
- 백그라운드 캐시 루프 (anomaly_scan, flow_balance, iforest)
- lifespan/미들웨어/데모 모드
- DB 커넥션 풀 + execute_sql
- `/csv/{filename}`, `/autocomplete/candidates`

---

### 확인 완료 (2026-04-10 — 미들웨어 인증 정리 점검)

#### 점검 결과
- **미들웨어**: 기본 전체 인증 적용, 읽기전용 GIS(tiles/layer/pipelines)만 예외 — 정상
- **API 프록시**: PUBLIC_PATHS 화이트리스트(login/refresh/health/models)만 우회 — 정상
- **GIS coordinates 쓰기**: PUT/POST/DELETE에 `requireSession()` 적용 완료 (4/9 커밋)
- **테스트 우회/bypass 패턴**: 발견 안 됨

#### 프로덕션 배포 전 남은 항목
- → 로드맵 단기 D항목으로 통합

---

### 로드맵 요약 (2026-04-12 재검증 기준)

> 2026-04-12 실제 코드베이스 대조 검증 완료. 완료 항목은 `[x]` + 근거 파일/라인 추가.
> 검증 범위: `/Users/jykim/web` (Next.js) + `/Users/jykim/slm` (Python AI 서버)

#### 단기 (Phase 0 — Gemma4:26b, 즉시 적용)

**A. AI 인텐트 품질 개선**
- [x] 오타/구어체 처리 — example3.json 501→744 질문 확장 (74 인텐트)
  - 근거: `/Users/jykim/slm/example3.json` 744 questions / 74 intents (목표 600+ 초과)
  - 약칭 정규화: `korean_fuzzy.py`(한글 자모분해 퍼지) + `param_extractor.py` 3단계 fuzzy fallback (sitename L331-346, facilitytype L529-545, datainfo L579-596)
- [x] 오분류 피드백 수집 — "원하는 답이 아닌가요?" 클릭 → DB 저장 (수동 검토 게이트)
  - 근거: `db/init/03_tables_chat.sql` `tb_ai_chat_feedback` 테이블 (self-contained: 질문/답변/인텐트 복사본)
  - Python: `endpoints/chat_feedback.py` (`/chat/feedback` POST/GET/PATCH) + `ai_server.py:106,2524-2525` 라우터 등록
  - Next.js: `src/lib/api/chat-api.ts` `submitChatFeedback()` + `src/components/chat/BotMessage.tsx` 피드백 버튼 UI + `chat/page.tsx:handleSubmitFeedback`
  - Store: `chat-store.ts:markFeedbackSubmitted` 플래그 localStorage 저장 (중복 제출 방지)
  - 검토 UI: `src/app/(dashboard)/admin/chat-feedback/page.tsx` — 인텐트별 집계 + 미검토/검토완료/전체 탭 필터 + 행 펼침(질문/응답/의견) + 검토 완료 버튼
  - 메뉴: `sidebar-menus.ts` M100-7 "채팅 피드백" 추가
- [x] 날짜 표현 파싱 확장 — 상대 시점 프로그래밍 추출 (SLM fallback 회피)
  - 근거: `param_extractor.py:_extract_date_programmatic` 확장 — 어제/그저께/엊그제, 지난주/저번주, 이번달/이번 달 초/이번 달 말, 지난달/저번달/전달, 작년/지난해, 올해/금년 (17개 패턴)
  - `_DATE_KW` 키워드 리스트에 그저께/엊그제 추가 (L197)
  - `slm_date` 로그 플래그 정확화: `_slm_date_called` 실제 호출 여부 추적 (L204, L308)
  - 검증: 프로그래밍 추출 성공 시 Phase2 < 5ms (SLM 호출 없음), 17개 패턴 모두 정상 변환
- [x] 시설명 약칭 매핑 테이블 — DB 기반 약칭→sitename 사전
  - DB: `tb_facility_alias` (alias, sitename, priority, note, use_yn) + unique index on (region, alias)
  - Python: `ai_server.py:load_facility_aliases_from_db()` + `_reload_facility_aliases()` (CRUD 후 런타임 즉시 반영)
  - `ParamExtractor.__init__`에 `facility_alias_map` 추가, `_extract_sitename`에서 fuzzy 단계 전 exact lookup (긴 alias 우선)
  - CRUD API: `endpoints/facility_alias.py` (`/admin/facility-alias` GET/POST/PATCH/DELETE)
  - Next.js: `src/app/(dashboard)/admin/facility-alias/page.tsx` — 검색/추가/수정/삭제 UI, `facility-alias-api.ts` 클라이언트, M100-8 메뉴 추가
  - 검증: "합일" → "합덕일반" alias 등록 → `/ask`에서 "합일 수위" 질의 시 자동 치환 + `corrections` 표출 확인
- ⏸ 프롬프트 구조 최적화 — Gemma4:26b few-shot 설계 **장기 보류** (→ 아래 "장기(Phase 2)" 참조)

**B. 이상감지 Phase 3 (설비 역추적)**
- [x] 이상 태그 → 연결 설비 → 종합 진단 연동
  - 근거: `EquipmentDiagnosis` 타입(`src/lib/types/chat.ts`) + `chat-response-mapper.ts` equipment_diagnosis 필드 매핑 + `src/components/chat/anomaly/AnomalyDetailView.tsx:247-255` 설비 건강 진단 렌더링
  - Python 측: `build_success_response()` equipment_diagnosis kwarg, /ask /ask/stream 핸들러 전달 (commit c7e69a3, 2026-04-09)
- [x] ai_server.py 이상감지 pass 스텁 → tb_tag_group_map 실구현
  - 근거: `anomaly_scan.py:243-250` DI 장애 태그 조회 (COMM_ERROR/EQUIP_FAULT/POWER_FAULT) `tb_tag_group_map` JOIN
  - `sql_executor.py:517-544` group_code 기반 tb_tag_group_map JOIN 분기
  - `ai_server.py` 11건의 tb_tag_group_map 참조 (L402-1375)

**C. 할루시네이션 방어 레이어**
- [x] Entity 검증 레이어 — LLM 추출값 → DB 퍼지매칭 → 실제 ID 치환
  - 근거: `query_validator.py` `unknown_sitename` / `missing_*` 검증 (L24-49, CORRECTION_TEMPLATES) + `param_extractor.py` fuzzy fallback 3종 (sitename/facilitytype/datainfo) + `korean_fuzzy.find_best_match`
- [x] 값 주입 프롬프트 — DB 수치만 사용하도록 생성 전 제약 + 출력 검증
  - 응답 템플릿: `{placeholder}` 치환으로 DB 값 직접 주입 (`ai_server.py:3019-3129`)
  - AI 요약 LLM 경로 (`endpoints/trend.py:/trend/explain`) 강화:
    - 엄격 프롬프트 (6개 절대 규칙: 제공 수치 외 숫자 금지, 외부 지식 금지, 권고 금지, baseline 비교 유도 등)
    - `_extract_numbers` + `_validate_summary_numbers` — 출력에서 숫자 추출 후 허용값과 대조 (2% tolerance)
    - `_fallback_summary` — 검증 실패 또는 LLM 불가 시 결정적 템플릿 요약 (할루시네이션 0)
    - 응답에 `source: "llm" | "fallback"` + 실패 시 `llm_rejected`/`violations` 포함
  - **C안 컨텍스트 확장** (`_fetch_trend_context`) — 버튼 클릭 시 tagsn 기반 30일 baseline 조회
    - `cagg_5min_raw_stats_ai` 연속 집계 활용 (32ms 수준 쿼리)
    - 반환: `baseline_min_30d`, `baseline_avg_30d` (가중 평균), `baseline_max_30d`
    - 프롬프트 "30일 Baseline" 섹션 + "구간 평균이 30일 평균 대비 어떤 수준인지 비교 서술" 규칙
    - `allowed_numbers`에 baseline 3개 값 + 상수 `30`(일) 자동 허용
    - 프런트: `PlotChart.tsx`가 `plot.tag_ids[pickedIdx]`로 tagsn 추출 후 payload에 포함
    - tagsn 누락 시 backward compatible (컨텍스트 없이 기본 요약)
  - 검증: 4종 E2E
    1. 1.02 vs baseline 0.77 → "높은 편입니다"
    2. 0.30 vs baseline 0.77 → "낮은 편입니다"
    3. 0.78 vs baseline 0.77 → "높은 편입니다" (미세 차이)
    4. tagsn 누락 → 기본 요약 (backward compat)
- [x] SQL 생성 완전 차단 — SQL_TEMPLATES dict 고정
  - 근거: 모든 SQL은 `intent_def.get("sql", "")`에서만 로드 (`ai_server.py` L2912, L4434, L2337), LLM SQL 생성 경로 없음. `execute_sql(sql_template, params)` 만 사용 (L2549)
- [x] 시맨틱 마커 일관 적용 — `<<ok>>` `<<warn>>` `<<error>>`
  - 근거: `block_builder.py:15-20 wrap_status_marker` + L36-57 `_alarm_category_marker`/`_alarm_msg_marker` + `response_builder.py:1282 _STATUS_MARKER_MAP` (`ai_server.py` 11건 사용)

**D. 프로덕션 인증 정리**
- [x] NEXTAUTH_SECRET → 강한 무작위 값 교체 (dev 포함)
  - `.env.local`, root `.env` (gitignore) 에 `openssl rand -base64 32` 생성 값 주입
  - `docker-compose.dev.yml` 기본값 `dev-secret-change-in-prod` 유지(폴백용) + `${NEXTAUTH_SECRET:-...}` 보간
  - Frontend 컨테이너 재기동으로 실제 활성화 확인 (`printenv NEXTAUTH_SECRET` = 강한 값)
- [x] DB 크레덴셜 → 환경별 분리 구조
  - `docker-compose.dev.yml` 전체를 `${VAR:-default}` 패턴으로 변경 (timescaledb/backend/frontend 3곳)
  - `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DATABASE_URL`, `NEXTAUTH_SECRET` 등 주요 변수 오버라이드 가능
  - dev 기본값 유지 → 기존 개발 흐름 무변화, prod는 `.env` 또는 시크릿 매니저로 주입
  - `.env.example` 템플릿 3종 (루트 / slm-dashboard / slm) + `docs/deploy-secrets.md` 배포 가이드 (Docker Secrets / k8s / 외부 매니저 패턴 설명)
  - 루트 `.gitignore` 신규: `.env*` 차단 + `.env.example` 허용
- [x] setup/tags TODO 스텁 2개
  - [x] 벌크 업로드 — `src/lib/api/tag-api.ts:80 uploadTagsCsv` + `src/app/(dashboard)/setup/tags/page.tsx:335 onUpload` 연결
  - [x] 태그 생성 API — `POST /tags` (`endpoints/tags.py:200-262`) + `tag-api.ts:createTag` + `setup/tags/page.tsx:handleAdd` 실제 호출
    - 스모크 테스트: 정상 201 / 중복 409 / 삭제 정리 확인

#### 중기 (Phase 1 — 납품 서버, A30 24GB + Gemma4 12B)
- [x] 이상감지 원인 LLM 서술 생성 (Phase 3 설비 진단 기반)
  - Python: `endpoints/anomaly_explain.py` — `POST /anomaly/explain`
    - 엄격 프롬프트 (6개 절대 규칙: 제공 수치·라벨 외 생성 금지, 권고 금지, 비교 서술 유도 등)
    - `_validate_numbers_in_text` — 허용 수치 + `strip_strings`로 식별자 내 숫자 오탐 방지
    - `_fallback_narrative` — 검증 실패/Ollama 불가/예외 시 결정적 템플릿 요약 (할루시네이션 0)
    - 응답: `{summary, source:"llm"|"fallback", llm_rejected?, violations?}`
    - `ai_server.py` 라우터 등록
  - **C안 컨텍스트 확장** — 버튼 클릭 시점에만 DB 컨텍스트 조회 → LLM 해석 가치 확보
    - `_fetch_anomaly_context()`: 3개 쿼리로 비교용 수치 조회
      1. 연결 이상 태그의 지난 7일 알람 건수 (`tb_equipment_alarm_report` WHERE tagsn IN linked_anomaly_tags)
      2. 같은 시설 전체의 지난 7일 알람 건수 (sitename + facilitytype)
      3. 같은 시설의 총 태그 수 (`tb_tag_info`)
    - 프롬프트 "비교 컨텍스트" 섹션 + "이 설비가 시설 평균 대비 어떤 수준인지 비교 서술" 규칙 추가
    - `allowed_numbers` 확장 (컨텍스트 값 + 프롬프트 상수 `7` 자동 허용)
    - 결과 예: "이 설비의 지난 7일 알람 0건으로, 같은 시설 전체 30건(일평균 4.3건)보다 낮은 편입니다"
  - Ollama 튜닝: timeout 45s → 90s, backoff 10s → 3s (`trend.py` 동시 적용)
  - Next.js: `src/lib/api/anomaly-api.ts` `explainAnomalyCause()` + `AnomalyDetailView.tsx`
    - 설비별 "AI 원인 분석" 버튼 (정상 등급 + 이상 태그 0건인 설비는 비활성)
    - per-equipment 상태 관리 (idle/loading/done/error)
    - 결과 카드: LLM 경로는 보라색, 폴백 경로는 노란색 (시각 구분)
  - 검증: 10회 재테스트 10/10 LLM 경로 통과 + C안 컨텍스트 적용 후 3종 서로 다른 비교 서술 확인 (0건/동일/낮은편)

#### 장기 (Phase 2 — Mac Mini Pro 또는 L40S + Gemma4 27B)
- [ ] 멀티모달 현장 사진 분석 ("참고 의견" 전용)
- ⏸ **EPANET 수리 시뮬레이션 모듈** — **장기 보류** (네트워크 모델링 설계 선행 필요, 별도 요청 시 재개)
- ⏸ **인텐트 68개 → 200개 확장** (Slot-Filling 구조 유지, 2단계 분류) — **장기 보류** (현재 74개, 사용자 지시로 보류. 별도 요청 시 재개)
- ⏸ **Gemma4:26b few-shot 프롬프트 최적화** — **장기 보류** (A-1 피드백 데이터 축적 후 혼동 쌍 기반으로 설계 예정)
- ⏸ **보고서 초안 자동 생성 → Word/PDF 다운로드** — **장기 보류** (설계·템플릿·주기 논의 선행 필요, 별도 요청 시 재개)

---

### 완료 (2026-04-10 — ANOMALY_SCAN_ALL 캐시 0행 수정 + DB IPv6 연결 수정)

#### 구현 내역
- **ANOMALY_SCAN_ALL latest CTE 시간창 동적 조정** (`D:\slm\ai_server.py`, `_compute_anomaly_scan_all()`)
  - 문제: SQL의 `latest` CTE가 `now() - 3h` 고정 → DB 데이터가 3시간 이상 오래되면 0행 반환 → 종합 현황판 데이터 없음
  - 해결: SQL 실행 전 `max(bucket)` 확인, 1시간 이상 오래됐으면 `latest`/`recent_holding` CTE를 max_bucket 기준으로 패치
  - 결과: 7.8h 오래된 데이터에서 293행 정상 캐시 생성 (캐시 완료 98.9점)
- **DB 연결 IPv6 문제 수정** (`D:\slm\.env`)
  - 문제: `DB_HOST=localhost` → C:\Python313 psycopg2가 `::1`(IPv6)로 해석 → Docker DB(IPv4) 연결 실패 + `fe_sendauth: no password supplied`
  - 해결: `DB_HOST=127.0.0.1` 명시 (IPv4 강제)
  - `start-services.bat`은 WSL_IP로 오버라이드하므로 영향 없음
- **에러 기록**: `error-management.md` E-011, E-012 추가

---

### 완료 (2026-04-09 — Phase3 설비 역추적 버그 수정 + 미들웨어 인증 정리, commit c7e69a3/9465bc3/7112262)

#### 구현 내역
- **미들웨어 인증 범위 개선** (`middleware.ts`)
  - `api/gis` 전체 우회 → `tiles/layer/pipelines`(읽기전용)만 우회
  - `/api/gis/coordinates` 쓰기 작업 인증 대상 포함
- **GIS coordinates 인증 추가** (`gis/coordinates/route.ts`)
  - PUT/POST/DELETE에 `requireSession()` 추가 → 미인증 401 반환
- **Phase 3 equipment_diagnosis 응답 누락 수정** (`ai_server.py`)
  - `build_success_response()`: `equipment_diagnosis` kwarg 처리 추가
  - `/ask` 핸들러: `equipment_diagnosis=processed_data.get(...)` 추가
  - `/ask/stream` SSE 핸들러: 동일하게 추가
  - `AiServerResponse` 타입: `equipment_diagnosis?: EquipmentDiagnosis[]` 추가
- **테스트 3회** — 죽동 배수지 ✅ (PLC 55점·통신이상·주의), 합덕/갈산 정상 동작 확인

---

### 완료 (2026-04-09 — GIS 기본값 최소화 + ANOMALY_SCAN_ALL IForest ML 배지, commit 27f881c)

#### 구현 내역
- **GIS 관망도 기본값 최소화** (`gis/page.tsx`)
  - 시설목록 패널: `showFacilityList true → false` (기본 접힘)
  - 유량흐름 패널: `showFlowPanel true → false` (기본 접힘)
  - Phase1(유량두께/글로우/불균형): `showBase/showGlow/showImbalance true → false`
  - Phase2(셔머 애니메이션): `showShimmer true → false`
  - **이유**: GIS 초기 진입 화면 과부하 → 최소 표시, 사용자가 필요 시 활성화
- **ANOMALY_SCAN_ALL IForest ML 배지** (`AnomalyScanView.tsx`)
  - `AnomalyData` 타입에 `mlModelCount/mlAnomalyCount/mlAgreeCount/mlTier1Count/mlTier2Count` 추가
  - `AiServerResponse` 타입에 `ml_*` 서버 필드 추가
  - `chat-response-mapper.ts`: ML 필드 매핑 추가 (서버 응답 → 프론트엔드 타입)
  - `AnomalyScanView.tsx`: KPI 카드 위에 IForest ML 탐지 배지 섹션 추가
    - 모델 수 / Tier-1(시설 다변량) / Tier-2(태그 단변량) / ML이상 수 / Z+ML 동시이상 수

---

### 완료 (2026-04-08 — IForest v2 테스트 스위트 구축 + T4 API 필드 수정)

#### 구현 내역
- **test_iforest_v2.py** 전체 작성 (T1~T5, 총 46개 케이스)
  - T1 단위: 31/31 ✅ (_datainfo_to_group, FacilityModel.predict, _build_facility_matrix, predict_for_rows)
  - T3 예측 품질: 5/5 ✅ (정상 FP율, 누수/펌프공회전/감압실패 물리 모순 감지)
  - T5 회귀: 8/8 ✅ (v1 하위 호환, predict_single, is_trained=False 방어)
  - T4 API: 2/2 ✅ (ANOMALY_SCAN_ALL ml 필드, ANOMALY_FACILITY_DETAIL Tier 정보)
  - T2 학습: SKIP — Windows에서 WSL localhost:5433 직접 접속 불가 (서버 로그로 Tier-1 41개/Tier-2 183개 확인)
- **ai_server.py 수정** — ANOMALY_SCAN_ALL stale-while-revalidate 캐시 경로(line ~7763)에 ML 필드 누락
  - `ml_model_count`, `ml_anomaly_count`, `ml_agree_count`, `ml_tier1_count`, `ml_tier2_count` 추가
- **버그 수정** — FP율 100% (hour/dow 고정값으로 학습 → 경계값에서 이상 판정)
  - 학습 시 hour∈[0,24), dow∈[0,7) 균일 분포 사용으로 수정
- **결과**: T1(31)+T3(5)+T4(2)+T5(8) = 46/46 통과

---

### 완료 (2026-04-08 — ANOMALY_FACILITY_DETAIL 500 오류 수정 + IForest SQL 수정)

#### 구현 내역
- **증상**: 10개 시설 중 5개(합덕 배수지, 갈산 가압장, 부곡/석문 배수지, 합덕 정수장) HTTP 500 반환
- **근본 원인 1**: `propagation_trace` 변수 미초기화 → Phase 2 결과를 `build_anomaly_facility_detail_block()` 인자로 전달 시 NameError
  - 수정: `propagation_trace = None` 초기화 블록에 추가 (`ai_server.py` line 6510)
- **근본 원인 2**: `_diagnose_equipment_for_tags()` SQL에 `e.ip_address, e.has_ip` 컬럼 참조 — `tb_equipment_info`에 존재하지 않는 컬럼
  - 수정: SELECT에서 해당 컬럼 제거, 네트워크 상태는 `tb_network_status` JOIN으로 대체
- **근본 원인 3**: `verify_causal_context` import가 외부 if 블록 안에서만 실행 → `_run_causal()` 클로저 내에서 미접근
  - 수정: import를 클로저 내부로 이동
- **숨겨진 원인**: Windows `localhost` → `::1` (IPv6)로 해석 → 구버전 서버(PID 15184)에 라우팅
  - 수정: 구버전 서버 강제 종료, `.env.local` `NEXT_PUBLIC_API_URL=http://127.0.0.1:8000` 변경
  - `start-services.bat` 포트 정리 후 2초 대기 추가
- **IForest SQL**: `anomaly_iforest.py` `date_trunc('5 minutes', ...)` → `time_bucket('5 minutes', ...)` (E-010)
- **결과**: 10/10 OK, avg 3.0s, max 3.7s (수정 전: 5/10 OK + 5/10 HTTP 500)

### 완료 (2026-04-08 — ANOMALY_SCAN_ALL 성능 개선 + 임베딩 keep-warm, commit 2ab1c90)

#### 구현 내역
- **증상**: "전체 센서 점검해줘" (ANOMALY_SCAN_ALL) → 캐시 미스 시 102초 대기
- **근본 원인**: cagg_5min_raw_stats_ai 34M행 통계 집계 (stats_global 단독 4.4s), TTL 만료 시 유저가 SQL 직접 실행
- **수정 1 — stale-while-revalidate**: 캐시 있으면 TTL 무관 즉시 반환, 캐시 없으면 "준비 중" 안내
- **수정 2 — 초기 딜레이 단축**: 프로파일링 대기 최대 120s → 30s
- **수정 3 — 임베딩 keep-warm**: snowflake-arctic-embed2 /api/embed 더미 요청 4분 주기 추가 (첫 임베딩 3s 지연 방지)
- **결과**: 102초 → 0.4초 (캐시 히트 기준)

### 완료 (2026-04-08 — AI 채팅 트렌드 PlotChart AI 요약, commit a644cf9)

#### 구현 내역
- **목표**: 트렌드 페이지 BrushToolbar AI 요약 기능을 AI 채팅 트렌드 차트에도 적용
- **수정 파일**: `src/components/chat/PlotChart.tsx`
  - 마운트 시 `trend_explain_enabled` 시스템설정 확인
  - 첫 번째 아날로그 시리즈 min/max/avg 계산 → `/api/proxy/trend/explain` 자동 호출
  - 차트 하단 파란 박스로 요약 표시 (Loader2 → Sparkles 아이콘)
  - `plot.period.from/to` 변경 시 재요약 (네비게이션 이동 제외)

### 완료 (2026-04-08 — NETWORK_UPSTREAM_FAULT_ANALYSIS 구현, commit 0cc6bd4+8f39555)

#### 구현 내역
- **목표**: "현장 LTE모뎀이 다 접속 안 되면 상위 UTM/SSLVPN 문제" 도메인 지식 기반 인텐트
- **인텐트 분류** (`intent_classifier.py`): "SSLVPN/UTM/상위 장비/다 통신이상/LTE 전부" → keyword 즉시 확정
- **SQL** (`ai_server.py`): MAX(check_time) CTE + equipmenttype 필터 + bool_or(is_alive)
  - sslvpn_summary CTE: LTE 모뎀 → SSLVPN 연결 집계, down_sites array_agg
  - utm_info CTE: UTM 전체 상태 집계
  - 결과: UTM/SSLVPN 정상 여부 + 이상 현장명 목록 출력
- **임베딩**: example3.json 15개 한국어 질문 정상화, 723벡터로 재구축
- **버그 수정**: MAX(boolean) → bool_or() (commit 8f39555)

### 완료 (2026-04-08 — AI 요약 응답속도 개선, commit 3fa7860)

#### 구현 내역
- **증상**: 트렌드 AI 요약 ~19초, 실패 잦음
- **근본 원인 1 (가장 큰)**: WSL에서 `localhost` 연결 시 IPv6(`::1`) 먼저 시도 → 2초 타임아웃 → IPv4 폴백
  - 모든 FastAPI 호출에 2.2초 오버헤드 존재 (AI 채팅 포함)
  - 수정: `uvicorn.run(host="::")` 듀얼스택 바인딩
  - 효과: localhost 응답 2.2s → 0.28s
- **근본 원인 2**: Ollama 5분 비활성 후 모델 언로드 → 재로드 9.5초
  - 수정: `_ollama_keepwarm_loop()` — 4분 주기 1-token 더미 요청으로 VRAM 유지
- **근본 원인 3**: `ollama_client.generate()` 동기 호출 → 이벤트 루프 블로킹
  - 수정: `asyncio.to_thread()` 래핑
- **추가**: `ollama_client.generate()` num_ctx/num_predict/timeout/backoff_seconds override 지원
  - explain: `num_predict=150`, `timeout=30s`, `backoff_seconds=10` (분류기 60s 독립)
- **결과**: 19s → 6s (3배 개선)

### 완료 (2026-04-08 — 인텐트 분류 버그 수정: "트렌드" 쿼리 FACILITY_TREND 강제 매핑)

#### 구현 내역
- **증상**: "한달간 신평배수지 수위 트렌드 보여줘" → FACILITY_TAG_DATA_TABLE(표) 반환
- **근본 원인 1**: `intent_classifier.py` `_classify_intent()` 391-404라인
  - "N일" 기간 패턴 + "수위/압력/유량" → 카테고리 무관하게 `FACILITY_TAG_DATA_TABLE` 반환
  - `normalize_question()`이 "한달간" → "30일간" 변환 후 `\d+\s*일` 패턴 매칭
  - 수정: `_TAG_DATA_EXCLUDE`에 `"트렌드", "트랜드", "추이", "그래프"` 추가
- **근본 원인 2**: `ai_server.py` `match_intent()` (최종 폴백 함수)
  - example3.json 질문을 `normalize_for_matching()`만 적용하고 `normalize_question()` 미적용
  - "30일간"(normalize_question 결과) vs "한달간"(원본 예시) 불일치 → 점수 오매칭
  - 수정: example 질문에도 `normalize_question()` 적용 후 비교
  - 추가: "트렌드" 포함 시 `FACILITY_TREND` 즉시 반환하는 우선 규칙
- **적용 파일**: `D:\slm\intent_classifier.py`, `D:\slm\ai_server.py`
- **테스트**: "한달간/30일간/7일간 ... 트렌드" → FACILITY_TREND, graph_type=plot ✓

### 완료 (2026-04-08 — ECharts smoothMonotone 적용)

#### 구현 내역
- **목표**: ECharts 트렌드 라인을 Recharts처럼 부드러운 곡선으로 표시
- **방법**: `smooth: true/0.3` 기존 설정 유지 + `smoothMonotone: 'x'` 추가
  - `smoothMonotone: 'x'` = monotone 보간 (Recharts `type="monotone"` 동일 알고리즘)
  - 오버슈팅(실제 데이터 범위 초과) 방지 — 수위/압력/유량 물리량에 필수
- **적용 파일** (commit `3217876`):
  - `plot-chart.ts`: `buildAnalogSeries()` + dual panel analog 시리즈 (디지털 step 시리즈는 제외)
  - `reservoir-chart.ts`: 배수지 수위 시리즈
  - `pressure-chart.ts`: 감압시설 1차측/2차측 압력 시리즈
  - `booster-chart.ts`: 가압장 토출압력/유량 시리즈
  - `StddevAnalysisView.tsx`, `StddevMultiAnalysisView.tsx`: 표준편차 분석 라인
  - `LeakCusumView.tsx`: 야간최소유량 + CUSUM 라인
- **제외**: 디지털 시리즈(`step: 'end'`), 기준선/임계값(수평 직선), bar/pie 차트

### 완료 (2026-04-08 — 트렌드 AI 요약 설명)

#### 구현 내역
- **`POST /trend/explain`** (ai_server.py) 신규 엔드포인트
  - gemma4:latest로 선택 구간 수치·패턴 2문장 요약 (권고 없음)
  - 요청: tag_name, unit, from_ts, to_ts, min/max/avg/count, anomaly_count
  - TREND_EXPLAIN_ENABLED 설정 DB 조회 → 비활성 시 거부
- **`GET/PUT /admin/site-settings`** — `trend_explain_enabled` 필드 추가
  - `tb_grp_code` SITE_SETTING 그룹 UPSERT 보장 (FK 충족)
  - `tb_comm_code` TREND_EXPLAIN_ENABLED UPSERT (create_dt/update_dt 제거)
- **`site-settings/page.tsx`** — AI 카드에 "트렌드 AI 요약" 토글 추가
  - Ollama 미연결 시 토글 비활성화
- **`BrushToolbar.tsx`** — "AI 요약" 버튼 + 로딩 스피너 + 결과 텍스트 표시
  - `ExplainState` 타입: idle / loading / done / error
  - 카드 너비 w-64 → w-72 확장 (요약 텍스트 공간 확보)
- **`TrendChart.tsx`** — explain 로직 통합
  - 마운트 시 `/api/proxy/admin/site-settings` 1회 조회 → `trendExplainEnabled` 상태
  - 브러시 새 구간 선택 시 이전 요약 초기화
  - `handleExplain()`: `/api/proxy/trend/explain` POST → `explainState/Summary` 관리
  - BrushToolbar에 새 props 전달
- **Playwright 테스트**: 영역 선택 → AI 요약 버튼 → "AI 분석 중..." → 2문장 요약 표시 확인

### 완료 (2026-04-07 — 기타 소규모 수정 3종)

#### 구현 내역
- **`zero_flow` 한글 레이블 적용** (commit `562da48`)
  - `GisFacilityCard.tsx` `MISMATCH_KO` 맵에 `zero_flow: "유량값 0"` 추가
  - `FlowMonitoringGraph.tsx` 툴팁 레이블 `"상류 유량 활성 + 하류 유량 0"` → `"유량값 0"` 통일
- **GisTrendPopup 두번 갱신 버그 수정** (commit `a6f3054`)
  - **원인**: 2단계 cascading useEffect — `useEffect1`이 `setTags()` 호출 → `useEffect2`([tags, activeRange])가 트렌드 조회 → 두 번의 render cycle이 사용자에게 "두번 갱신"으로 인식
  - **수정**: 두 useEffect를 단일 async IIFE로 통합 — tag 조회 후 연속으로 trend 조회, `setTags + setTagDataMap + setLoading(false)` 동일 함수 내 호출 → React 18 automatic batching으로 1회 커밋
  - 의존 배열: `[sitename, facilitytype]` + `[tags, activeRange]` → `[sitename, facilitytype, activeRange]` 단일화
  - 동일 패턴이 적용되는 용수 흐름 팝업도 동일 효과 (데이터 연속 패치)
- **package.json HTTPS 스크립트 정리 + GIS 좌표 업데이트** (commit `7e5902d`)
  - `dev`/`dev:fast` → HTTPS 기본, HTTP는 `dev:http`/`dev:http:fast`로 명칭 통일
  - `gis-facility-coords.json`: _comment 업데이트, PDF 지도 기반 배수지·가압장 좌표 재확정

### 완료 (2026-04-07 — ai_server.py 모듈 분리: 3개 endpoint 모듈 추출)

#### 구현 내역
- **`endpoints/monitoring_catalogs.py`** (312줄): `/monitoring/catalogs/*` 7개 엔드포인트
  - GET sites, site-groups, reference, catalogs (목록), POST/PUT/DELETE catalogs
- **`endpoints/flow_map_crud.py`** (319줄): `/flow-map` CRUD 7개 엔드포인트
  - GET (전체), roots, downstream, POST, DELETE, export/csv, import/csv
- **`endpoints/csv_import.py`** (636줄): CSV 일괄 가져오기 6개 엔드포인트 + 헬퍼 함수
  - tags, equipments, reservoirs, boosters, pressure-reducing, blocks
  - `_csv_cell/float/int/bool/json_array` 헬퍼 함수 이동
- **`ai_server.py`**: 13,921줄 → 12,724줄 (-1,197줄), 기존 패턴 동일하게 초기화
  - `from endpoints.X import router as X_router, init as init_X`
  - `init_X(get_db_connection); app.include_router(X_router)`
- 실시간/AI 로직 의존 엔드포인트(`/flow-map/realtime`, `/equipments/auto-map` 등)는 ai_server.py 유지

### 완료 (2026-04-07 — GIS 유량 흐름 통합 고도화: 노드 물수지 히트맵 + 카드 팝업 연동)

#### 구현 내역
- **`GisFlowOverlayLayer.tsx`** Phase 4 추가 — 노드 물수지 히트맵
  - `buildNodeImbalanceGeoJson()`: 불균형 엣지 연결 노드 worst grade 계산 (upstream+downstream 모두)
  - `gis-node-imbalance-src` point 소스 + `gis-node-imbalance` circle 레이어
  - 경고=빨강/주의=오렌지/관심=노랑 컬러 링 (circle-stroke), 투명 채움 → 기존 마커 보존
  - `showImbalance` 토글과 연동 (불균형 dashed 라인과 동시 on/off)
- **`GisFacilityCard.tsx`** — 확장 카드 유량 불균형/교차검증 배지 추가
  - `edgeImbalance` prop: 이 시설과 연결된 불균형 엣지 계산 (upstream/downstream)
  - `imbalanceEdges`: grade/imbalance_pct/other(시설명) 목록 → "불균형 +N%" 배지
  - `cross_mismatch: true` → "교차이상 + mismatch 유형 한글" 배지 (MISMATCH_KO 맵)
- **`use-gis-facilities.ts`** — `cross_mismatches` 캡처 → 노드 `cross_mismatch`/`cross_mismatch_types` 채움
- **`gis.ts`** 타입 — `GisFacilityNode.cross_mismatch_types?: string[]` 추가
- **`gis/page.tsx`** — `GisFacilityCards`에 `edgeImbalance` prop 전달

### 완료 (2026-04-07 — 용수 흐름 ↔ 대시보드 유량 불균형 수치 통일 + KPI 레이블 명확화)

#### 원인 분석
- **유량 불균형 불일치**: `dashboard_overview()`가 `_ANOMALY_SCAN_CACHE` 내 스냅샷을 사용 (ANOMALY_SCAN 5분 + FLOW_BALANCE 30분 복합 지연 가능) → 용수 흐름 페이지의 `_FLOW_BALANCE_CACHE` 직접 참조와 타이밍 불일치
- **교차검증 이상 불일치**: 알고리즘 차이 (대시보드=AI 스캔 verdict "교차이상" 시설 수, 용수 흐름=실시간 유량 비교 노드 수) → 의도적 차이, 레이블로 명확화

#### 수정 내역
- **`ai_server.py` `dashboard_overview()`**: `flow_balance`를 `_ANOMALY_SCAN_CACHE` 스냅샷 대신 `_FLOW_BALANCE_CACHE` 직접 계산 → 용수 흐름 페이지와 동일 소스 사용
- **`dashboard/page.tsx`**: `교차검증 이상` sub "엣지 불일치 N건" → "5분 스캔 · 불일치 N건" (스캔 기반임 명시)
- **`monitoring/flow/page.tsx`**: `교차검증 이상` sub "실시간", `유량 불균형` sub "30분 갱신" 추가

### 완료 (2026-04-06 — GIS 관망도 유량 흐름 오버레이 구현)

#### 구현 내역
- **`GisFlowOverlayLayer.tsx`** (신규): MapLibre GL 직접 레이어 컴포넌트
  - Phase 1: 유량 비례 굵기(2~14px) + 색상(회색→하늘→파랑) 라인
  - Phase 2: rAF 30fps shimmer 애니메이션 (line-gradient, ANIM_SPEED=0.004)
  - Phase 3: 불균형 강조 dashed 오렌지/빨강 오버레이 (imbalance_grade 기반)
  - 레이어 ID: `gis-flow-glow`, `gis-flow-base`, `gis-flow-anim`, `gis-flow-imbalance`
  - 시설 마커 레이어 아래 삽입 (facility-clusters 이전) → 기존 아이콘/클러스터 보존
  - visible prop으로 on/off 토글 (map.setLayoutProperty visibility)
  - pendingDataRef 패턴: map load 지연 시 데이터 누락 방지
- **`use-gis-facilities.ts`** 확장: edges, flowNodes, edgeImbalance 추가 반환
  - `GisFlowEdge` 인터페이스 export
- **`monitoring/gis/page.tsx`** 헤더 토글 버튼 추가: "유량 흐름" (기존 "전체보기" 옆)
  - showFlowOverlay state (기본 true), 버튼 색상 active/inactive 구분

#### 핵심 MapLibre 제약 (재확인)
- `lineMetrics: true` source 필수 → `line-gradient` 사용 조건
- `line-gradient` 레이어는 `line-width` constant만 허용 (data-driven 불가)
  → `gis-flow-base`(data-driven width+color) + `gis-flow-anim`(constant width, gradient) 분리
- `line-cap`/`line-join` → layout 속성 (paint에 넣으면 MapLibre 4.x 검증 오류)
- shimmer p0~p2 범위가 겹치면 "ascending order" 에러 → clamp(0.0001, 0.9999) 필수

#### 필수 조건 (유지됨)
- 기존 GIS 레이어(SHP, 시설 아이콘, 클러스터, 팝업) 변경 없음
- additive only: 새 레이어 추가만, 기존 레이어 수정 없음

### 완료 (2026-04-06 — AI Server 시작 hang 수정 + start-services.bat 개선)

#### AI Server hang 원인 및 수정

**원인**: AI Server 강제 종료 시 PostgreSQL에 좀비 연결 잔류 → 테이블 락 점유
- 이전 프로세스 백그라운드 태스크(`_anomaly_scan_cache_loop`)가 `cagg_5min_raw_stats_ai` 365일 조회 쿼리 실행 중 강제 종료
- 연결이 `idle in transaction` 또는 `active` 상태로 PostgreSQL에 남아 테이블 락 점유
- 새 서버 시작 시 `_auto_classify_tags()` → `DELETE FROM tb_tag_group_map` 에서 락 대기 → 무한 hang

**수정 2종**:
1. `ai_server.py` `_auto_classify_tags()`: `SET lock_timeout = '10000'` 추가 → 10초 초과 시 예외 발생 → lifespan catch로 처리 (무한 hang 방지)
2. `start-services.bat` 2단계 추가: AI Server 시작 전 `pg_terminate_backend`로 `slm` DB의 좀비 연결 자동 정리

#### start-services.bat 개선 이력
- Docker Desktop 로직 제거 (WSL Docker로 전환)
- BOM 제거 + CP949 인코딩 저장 (한글 깨짐 수정)
- `chcp 949` 강제 설정
- `netstat | findstr` → PowerShell TcpClient 체크 → 포트 체크 제거 (WSL 관리로 불필요)
- AI Server 실행: `python` → `venv\Scripts\python.exe` (가상환경 명시)
- PostgreSQL 좀비 연결 정리 단계 추가 (2단계)
- AI Server 대기: 10초 → 15초

#### load_dotenv 수정
- `ai_server.py` `load_dotenv()` → `load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))`
- 이유: 실행 디렉토리가 다를 경우 `.env` 파일을 못 찾아 DB_PASSWORD 미설정 → DB 연결 실패

### 완료 (2026-04-05 — 이상 시설 TOP network_down 혼입 수정)

#### 원인 분석
- `tb_network_status`는 로컬 AI 서버의 `snmp_poller`가 직접 작성 (db_sync 동기화 대상 아님)
- 로컬 개발 서버는 현장 PLC/LTE 장비와 다른 망 → SNMP 폴링 전부 Timeout
- **178개 장비 전체 `is_alive=false`** (실제 장애가 아닌 망 분리 때문)
- `_detect_equipment_failures()`가 이것을 진짜 `network_down`으로 판단 → 합덕 배수지 수위 태그 전체에 마킹
- 결과: 이상 시설 TOP에 수위알람 + network_down 배지가 함께 표시 (오탐)

#### 수정 (`ai_server.py` — `_detect_equipment_failures`)
- 최신 체크 시점의 `is_alive=true` 장비가 **0개**이면 → 망 분리 환경으로 판단, A소스(network_down) 전체 스킵
- 운영 환경(현장 망과 동일)에서는 `is_alive=true` 장비가 있으므로 기존 로직 정상 작동
- 로그: `tb_network_status 전체 is_alive=false (N개) → 망 분리 환경으로 판단, network_down 스킵`

#### 사양 확정
- **기준**: 알람 기준(tb_equipment_alarm_report) + DI fault 태그(B소스) — 운영 환경에서만 네트워크 상태(A소스) 추가
- **개발 환경**: A소스 자동 비활성화 (전체 Timeout 감지)
- **운영 환경 배포 시**: 동일 망에서 SNMP 폴링되므로 A소스 정상 동작

### 완료 (2026-04-06 — GIS 관망도 유량 흐름 시각화 설계 + 프로토타입)

- **디자인 방안 수립**: 용수 흐름도의 유량 표현(두께/색상/애니메이션)을 GIS 관망도에 적용하는 4가지 기술 방안 비교 (line-dasharray / line-gradient / Custom WebGL / data-driven)
- **Phase 1**: `line-width` + `line-color` data-driven — `flowToWidth()`, `flowToColor()` 로직 MapLibre expression으로 이식
- **Phase 2**: `line-gradient` + rAF 30fps — `line-progress` [0→1]으로 흐름 방향 셔머 애니메이션
- **프로토타입 HTML**: `docs/gis-flow-animation-prototype.html` — 샘플 7개 시설 네트워크, 불균형 경보 점선, 토글 컨트롤, 다크/라이트 모드
- **핵심 발견**: `line-gradient` 레이어는 `lineMetrics: true` 필수, `line-width` data-driven 불가 (상수만 허용), `line-cap`/`line-join`은 `layout`이 아닌 MapLibre 4.x에서 별도 처리

### 완료 (2026-04-07 — Node-RED 네트워크 동기화 + 버그 수정 2건)

#### 구현 내역
- **`expected_impact_assessment` "정보없음개" 수정** (commit `3b95730`)
  - Node-RED 함수 노드 `d11b61e81d4e4df7` (수위 LL 예상피해평가 UPDATE)
  - `supply_population` 비숫자("정보없음", NULL) → `isNaN()` 가드 + 0 대체
  - 기존 50건 오염 데이터 DB 직접 UPDATE ("정보없음개 수용가..." → "수용가 용수공급차질")
- **`build_level_detail_block` "2.73None" 수정** — 이미 `or ""` 패턴 적용 확인, 추가 수정 불필요
- **네트워크 상태 동기화 Node-RED 신규 탭** (`flows_deploy.json`)
  - "네트워크 체크" 탭 비활성화 (`disabled: true`) — 개발환경 현장 IP 접근 불가
  - 신규 "네트워크 상태 동기화" 탭: cronplus(5분) → 원격 SELECT(최근 10분) → UPSERT 생성(500건 배치) → 로컬 INSERT ON CONFLICT
  - 원격 DB config 신규: `remote_pg_config_001` (112.166.183.65:25479/postgres)
  - 로컬 DB: 기존 `slm-timescaledb` (172.17.0.1:5433/slm)
  - 수동 실행 inject 노드 + catch/에러 처리 + 상태 표시 포함
  - **주의**: postgresql 노드 `query` 필드에 `{{{msg.payload}}}` 필수 (빈 문자열이면 쿼리 무시)
  - 원격 DB user: `postgres` (dj_post 아님, db_sync.py 참조)
  - 테스트: 수동 트리거 → 1,780건 UPSERT 성공 (178장비, 정상138/이상40)

### 완료 (2026-04-07 — 이상감지→설비 역추적 Phase 3: 연결 설비 건강 진단)

#### 구현 내역
- **`_diagnose_equipment_for_tags()`** (`ai_server.py`): 이상 태그 → tb_equipment_tag_map 역추적 → 연결 설비 건강 진단
  - 5단계 진단: 태그-설비 매핑 → 전체 태그수 → 네트워크 상태 → DI 장애 → 건강점수
  - 건강 점수 0~100 (장애 유형별 감점: equip_fault -40, power_fault -30, network_down -25, comm_error -15)
  - 건강 등급: 정상(≥80) / 주의(≥50) / 위험(<50)
- **ANOMALY_FACILITY_DETAIL 핸들러**: 이상 태그 수집 → `_diagnose_equipment_for_tags` 호출 → `data["equipment_diagnosis"]`
- **프론트엔드**: `EquipmentDiagnosis` 타입 + `AnomalyDetailView` 보라 테두리 카드
  - 설비 ID + 유형 + 건강점수 + 이상태그/전체태그 비율 + 장애 라벨
  - chat-response-mapper `equipment_diagnosis` 패스스루

### 완료 (2026-04-07 — 계정 권한 Phase 3~4: 메뉴 접근 권한 매트릭스)

#### 구현 내역
- **DB 시드**: `tb_menu` 35건 + `tb_auth_menu` 84건 (MASTER 35 + ADMIN 35 + USER 14)
  - 정적 사이드바 메뉴 구조를 DB로 완전 이관
  - `/api/auth/me`가 `tb_auth_menu` 기반 권한별 메뉴 필터링 동작 확인
- **백엔드 API 3종** (`auth_crud.py`)
  - `GET /api/auth/menu-permissions`: 전체 메뉴 + 권한별(MASTER/ADMIN/USER) 허용 매트릭스
  - `PUT /api/auth/menu-permissions`: 권한별 메뉴 허용/차단 토글 (use_yn Y/N)
  - `PUT /api/auth/menu-visibility`: 메뉴 전체 표시/숨김 (tb_menu.use_yn)
- **프론트엔드**: `MenuPermissionMatrix.tsx` 신규
  - 메뉴 관리 페이지에 "메뉴 트리" / "접근 권한" 탭 추가 (MASTER 전용)
  - 체크박스 매트릭스: 부모/자식 메뉴 × 마스터/관리자/일반 3열
  - Eye 아이콘: 메뉴 전체 표시/숨김 토글 (DB 영속화)
  - MASTER는 항상 전체 접근 (체크 비활성)
- **`tb_auth_menu`**: PK `(region, auth_idn, menu_idn)` + `use_yn`, `menu_order` 컬럼 추가

### 완료 (2026-04-07 — 네트워크 토폴로지 초기화면 + 전체화면 버튼)

#### 구현 내역
- **네트워크 페이지 초기화면/전체화면 버튼** — 용수흐름과 동일한 UX 패턴 적용
  - `network/page.tsx`: 카드 헤더에 "초기화면" + "전체화면/축소" 버튼 추가
  - 전체화면: `position:fixed` 오버레이 (사이드바 제외), opacity+scale 300ms 트랜지션
  - ESC 키로 전체화면 해제
  - Force/계층형 양쪽 모드에서 동일 동작
- **`HierarchicalTopologyGraph.tsx`**: `fitViewTrigger` prop 추가
  - easeInOutCubic 380ms rAF 루프 카메라 애니메이션 (GIS flyTo 동일 방식)
  - 트리거 시 선택 노드 해제 + 전체 뷰로 복귀
- **`TopologyGraph.tsx`**: `fitViewTrigger` prop 추가
  - `handleResetView()` 호출 (ECharts Force 레이아웃 재시작)
- 전체화면 오버레이 CardHeader `relative z-10` 추가 (ECharts canvas 가림 방지)

### 이전 할 일 (2026-04-08 계획 → 전부 완료)

- ~~알람 테이블 meta 컬럼 NULL 원인 파악~~ → 완료 (04-05 비상연락처 구축 시 처리)
- ~~용수 흐름 교차검증이상 vs 대시보드 교차검증 불일치~~ → 완료 (04-07)
- ~~이상 시설 TOP에서 network down이 합덕배수지 수위알람에 포함되는 이유~~ → 완료 (04-05)
- ~~GIS 용수 흐름 기능 통합~~ → 완료 (04-07 Phase 4 물수지 히트맵 + 팝업 배지)
- ~~ai_server.py 분할 최적화~~ → 완료 (04-07 3개 모듈 추출, 13,921줄 → 12,724줄)
- ~~`expected_impact_assessment` "정보없음개" 수정~~ → 완료 (04-07 Node-RED isNaN 가드 + DB 정리)
- ~~네트워크 상태 동기화 Node-RED~~ → 완료 (04-07 원격→로컬 5분 증분 동기화)
- ~~"2.73None" 표기 버그~~ → 완료 (04-07 이미 수정 확인)

---

### 완료 (2026-04-05 — fn_reservoir_level_summary DB 함수 최적화)

#### RESERVOIR_LEVEL_STATUS ("남산 배수지 수위 현황") SQL 13~26s → 11ms

- **원인**: `latest` CTE가 `tb_tag_info JOIN tb_tag_raw_data` 시간 범위 없이 전체 스캔 → `read=168K blocks`
- **수정** (`fix_reservoir_level.py` → PostgreSQL 함수 재작성):
  1. `v_tagsns` 배열을 `tb_tag_info` 단독 스캔으로 사전 추출
  2. `latest` CTE: `r.tagsn = ANY(v_tagsns) AND r.logtime >= NOW()-7일` → `idx_tag_raw_tagsn_time` 인덱스 스캔
  3. `tag_info` CTE 별도 분리 (RETURNS TABLE `unit` 변수 충돌 → `i.unit AS tag_unit`으로 해결)
- **결과**: 13~26s → **11ms** (~100배 개선), 전체 응답 ~250ms

### 완료 (2026-04-05 — AI 파이프라인 3종 성능 최적화)

#### 병목 분석 및 개선 결과

| 병목 | 원인 | 개선 전 | 개선 후 |
|------|------|---------|---------|
| 임베딩 중복 계산 | `classify()`에서 `embed_query()` 2회 호출 | +1~4s 낭비 | 1회로 통합 |
| SLM 날짜 추출 오작동 | `_DATE_KW`에 `"분"/"간"/"주"` 단독 포함 → "분析/야간/주소" 오매칭 | +11~17s | 0~1ms (스킵) |
| FACILITY_TREND DB | `fn_trend_period_summary` 2회 풀스캔(287K blocks) | 2,852ms | 78~131ms (30×) |

#### 1. `intent_classifier.py` — 임베딩 1회 계산 공유
- `classify()` 내 `embed_query()` 2회 호출 → 1회로 통합 (query_vec 전달)
- `_get_vector_candidates()`, `_classify_by_vector()` 모두 `query_vec=None` 파라미터 추가
- 타이밍 로그 추가: `⏱ 임베딩 Xms`, `⏱ 분류(keyword/vector/slm) kw=Xms embed=Xms total=Xms`

#### 2. `param_extractor.py` — SLM 날짜 추출 조건부 스킵
- `_DATE_KW` 단독 한글자(`"분"/"간"/"주"`) 제거 → 복합 표현만 유지
  - `"분"` → `"분간"/"분 전"` (기존 "분析" 오매칭 방지)
  - `"간"` → 완전 제거 (기존 "야간/기간/구간" 오매칭 방지)
  - `"주"` → `"주간"/"주일"` (기존 "주소/주요" 오매칭 방지)
- `_has_date_hint = False`이면 Ollama 호출 없이 기본값 사용
- 타이밍 로그 추가: `⏱ 추출 Phase1=Xms Phase2(날짜)=Xms 합계=Xms slm_date=Y/N`

#### 3. `ai_server.py` — 전구간 타이밍 계측 + DB 함수 최적화
- `/ask`, `/ask/stream` 양쪽에 단계별 `⏱` 로그 추가
  - `분류=Xms 추출=Xms SQL=Xms 합계=Xms rows=N`
- `execute_sql()` 내 SQL 실행 시간 로그: `⏱ SQL Xms → N행`
- `fn_trend_period_summary` PostgreSQL 함수 재작성
  - 기존: `tag_counts` CTE COUNT(*) + `ranked` CTE 풀스캔 — 동적 JOIN으로 인덱스 미사용
  - 개선: `ARRAY(SELECT tagsn...)` 사전 추출 → `WHERE r.tagsn = ANY(v_tagsns)` 인덱스 스캔
  - 결과: 2,852ms → **78~131ms** (약 30배 개선)

#### 최종 응답 성능 (server9 기준, 웜업 후)
- `FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS` ("신평 배수지 야간최소유량 표준편차 분析"):
  - 임베딩=400ms, 분류=0ms, 추출(Phase2)=**0ms** (SLM 스킵), DB=~3ms
  - 전체 ≈ **2초** 이내 (이전: 분析/야간 오매칭으로 SLM 11~17s 낭비)

### 완료 (2026-04-05 — 비상연락처 관리 기능 + Node-RED 인코딩 수정)

#### 1. 비상연락처 관리 시스템 구축 (`tb_alarm_contact` → API → Web UI → Node-RED)
- **DB**: `tb_alarm_contact` 테이블 신규 생성, 시드 10건 (UPS 3, 정전 4, 통신이상 2, 밸브 1)
- **Python API** (`D:/slm/endpoints/alarm_contacts.py`):
  - `GET /crisis/alarm-contacts?category=` → 목록 조회
  - `GET /crisis/alarm-contacts/categories` → 카테고리 목록
  - `POST/PUT/DELETE /crisis/alarm-contacts[/{id}]` → CRUD
- **Web UI**: `/setup/alarm-contacts` 신규 페이지 (카테고리별 그룹 테이블, CRUD 다이얼로그)
  - 기존 카테고리 선택 + 신규 카테고리 직접 입력 지원
  - 사이드바 메뉴 '비상연락처' 추가 (M200-14, Phone 아이콘)
- **Node-RED**: `HTML 위기대응 표시` 3개 노드 UPDATE에 meta 서브쿼리 추가
  - `diagnosed_msg` 업데이트 시 `tb_alarm_contact` 서브쿼리로 `meta.contacts` 동시 저장
  - 대상: a1ff5115e65d1474, 820cf7cd8e67c2f9, a655fae0839ec028

#### 2. flows_deploy.json 한글 인코딩 깨짐 전면 수정
- **원인**: Node-RED 편집기가 일부 한글 문자를 U+FFFD(대체 문자)로 저장
- **범위**: 39개 노드, 122개 U+FFFD → 모두 올바른 한글로 복원
- **대표 예시**: `윤활 [?]태` → `윤활 상태`, `가[?]중` → `가동중`, `[?]력계` → `압력계` 등

#### 3. "통신이상감지" Node-RED 탭 비활성화 (`flows_deploy.json`)
- **원인**: 사양에 없는 탭이 60초 주기로 "통신이상 N개 태그 감지" 알람 자동 생성
- **수정**: 탭 ID `a1b2c3d4e5f60001` 및 하위 7개 노드 `d: true`로 비활성화

#### 4. meta NULL 원인 분석
- **원인**: Node-RED INSERT/UPDATE 쿼리에 meta 컬럼 포함 안 됨 (미구현)
- **해결**: 연락처 관리 구축 후 `HTML 위기대응 표시` UPDATE에 meta 서브쿼리 추가
- **반영 범위**: alarm_category에 대응하는 연락처가 있을 때 meta.contacts 자동 채움

---

### 완료 (2026-04-05 — psycopg2 한글 인코딩 수정 + 경보분석 필터 정상화)

#### 1. psycopg2 `client_encoding` 누락 수정 (`D:/slm/ai_server.py`)
- **증상**: 경보분석 페이지(`/crisis/alarm-analysis`)에 `alarm_severity = '정상'`인 성북2 알람이 계속 출현
- **원인**: `_init_db_pool()` 에 `client_encoding` 미지정 → psycopg2가 Windows cp949 인코딩으로 연결
  - SQL 인라인 한글 리터럴 `IS DISTINCT FROM '정상'`의 인코딩 불일치 → 필터 조건 비교 실패
- **수정**:
  1. `ThreadedConnectionPool` 에 `options="-c client_encoding=utf8"` 추가
  2. `/crisis/alarm-analysis` 쿼리 인라인 한글 → 파라미터화: `IS DISTINCT FROM %s` + `('정상',)` 튜플
- **결과**: API 응답에서 성북2 알람 0건, 필터 정상 동작 확인

#### 2. Node-RED 헌팅(Hunting) 필터링 로직 분석 (`flows_deploy.json`)
- **경보_송산2산단(배) 생활 2지 수위 LL [alarm_value=2.09]** 가 "필터링" 표시되는 원인 확인
- **Node-RED 3단계 흐름**:
  1. **CASE 2 노드** (SQL): 최근 5분간 아날로그 수위 데이터 `(max-min)/min × 100 ≥ 10%` → `hunting_detected='Y'`
  2. **LL/HH 판단 노드** (JS): `hunting === 'Y'` 이면 "헌팅 Y (필터링)" 경로로 라우팅
  3. **헌팅 필터링 UPDATE 노드** (SQL): `is_false_alarm='Y'`, `false_alarm_notes='이상데이터(헌팅) 감지에 의한 필터링'` 업데이트
- **결론**: DB의 `is_false_alarm='Y'` 값이 정상적으로 설정된 것임, 수위 헌팅으로 인한 올바른 필터링

#### 3. 사양 확정 — 경보분석 "필터링" 표시 기준
- `getDisplayStatus` (`AlarmAnalysisPanel.tsx`): `is_false_alarm === 'Y'` OR `task_suppressed === true` 중 하나면 "필터링"
- `alarm_severity = '정상'` 단독은 "필터링" 조건 아님 (진행중/알람해제로 분류)
- `/crisis/alarm-analysis` API: `alarm_severity IS DISTINCT FROM '정상'` 로 정상 등급 알람 자체 제외

---

### 사양 확정 (2026-04-05 — 경보분석 "필터링" 표시 기준)

#### 필터링 컬럼 표시 대상 (`getDisplayStatus` in `AlarmAnalysisPanel.tsx`)
- **조건 1**: Node-RED 필터링기능 탭 로직 처리 → `is_false_alarm = 'Y'`
- **조건 2**: Web 작업관리설정 억제 알람 유형/태그 → `task_suppressed = true`
- 위 두 조건 중 하나라도 해당하면 "필터링" 뱃지 + 필터링 탭으로 분류
- `alarm_severity = '정상'` 단독으로는 "필터링" 조건 아님 (진행중/알람해제로 분류됨)

---

### 완료 (2026-04-05 — 경보분석 위기대응 카드뷰 개선 v2)

#### 1. `AlarmAnalysisDetail` — React Hooks 위반 수정 (`src/components/crisis/AlarmAnalysisDetail.tsx`)
- **버그**: `useMemo`를 early return(`if (!report)`) 이후에 호출 → Rules of Hooks 위반
  - null → non-null 전환 시 hook 수 불일치로 렌더링 실패 (경보 선택 시 섹션 미표출)
- **수정**: `useMemo`를 early return 전으로 이동, `report?.diagnosed_msg` 옵셔널 체이닝 사용

#### 2. 탁도계 통신이상 `alarm_severity` 정상화 (DB 직접 실행)
- `alarm_msg LIKE '%탁도계 통신이상%'` 레코드 1790건 `alarm_severity = '정상'` 업데이트
- 헌팅성 통신이상으로 필터링 처리 필요한 알람

#### 3. 심각도 키워드 색상 최종 확정
- `정상` → `text-green-500`, `주의` → `text-amber-500`, `이상`/`경고` → `text-red-500`
- `경보` 키워드 색상 제외 — "경보" = 일반 단어(경보등급, 경보 필터링 결과), 경고와 혼동하지 않도록
- `isNumericContext` 적용: "3.5m 이상" 등 수치 비교 표현의 "이상"은 색상 제외

---

### 완료 (2026-04-05 — 경보분석 위기대응 카드뷰 개선)

#### 1. `AlarmAnalysisDetail` — diagnosed_msg 파싱 카드 렌더링 (`src/components/crisis/AlarmAnalysisDetail.tsx`)
- **변경 전**: `diagnosed_msg` 있으면 iframe으로 표출 (다크모드 CSS 주입 방식), `diagnosed_cause`/`countermeasure` 있으면 구조화 카드
- **변경 후**: `diagnosed_msg` → DOMParser로 섹션 파싱 → 카드 뷰 렌더링 (iframe 완전 제거)
- **파싱 로직** (`parseDiagnosedMsg`):
  - `section.section` 요소 순회, `1. 경보 경과` 섹션은 헤더에 이미 표시되므로 스킵
  - `.label` 클래스(red/green/blue) → `bgColor` Tailwind 매핑
  - 자식 DOM 순서 보존: `<p>` → `TextBlock`, `<ul>/<ol>` → `ListBlock`, `.info/.info-box` → `InfoBlock`
  - `InfoBlock` 파싱: `<p><strong>(Key)</strong> value</p>` 패턴 정규식 `/^\(([^)]+)\)\s*(.*)/`
- **우선순위**: `diagnosed_msg` 파싱 성공 → 파싱 카드 / 실패 시 기존 `diagnosed_cause`/`countermeasure`/`meta.operations` fallback
- **제거**: `DiagnosedMsgIframe`, `DARK_MODE_CSS`, `FLOWCHART_LAYOUT_CSS`, `injectDarkMode`, `injectFlowchartLayout` 전부 삭제

#### 2. 심각도 키워드 색상 하이라이팅 (`HighlightedText` 컴포넌트)
- 텍스트 내 `정상` → `text-green-500`, `주의` → `text-amber-500`, `이상`/`경고` → `text-red-500`
- 적용 범위: TextBlock(단락), ListBlock(목록 항목), InfoBlock(key-value 값) 전체

#### 3. 로컬 DB ↔ 원격 DB 동기화 확인
- `44270_24700_AMA_N001` (경보_송악2(배) 1지 수위 HH 상태, 2026-04-05 13:16:00)
- `diagnosed_msg` 3922자, `diagnosed_cause`, `countermeasure`, `alarm_severity` 모두 identical 확인 → 동기화 불필요

---

### 완료 (2026-04-05 — Node-RED 위기대응 HTML 개선 + DB 알람 적재 수정)

#### 1. 위기대응 메시지 경보등급 동적 표시 (`flows_deploy.json`)
- **노드**: `HTML 위기대응 표시` (a1ff5115e65d1474)
- 기존: `(경보등급) 주의등급.` 하드코딩
- 변경: `const alarm_severity = msg.payload.alarm_severity || "주의"` 변수 추출 → `${alarm_severity}등급.` 동적 표시
- 효과: 경고/주의/정상 등 DB 실제 값 반영

#### 2. `build_sql` JSONB 경로 버그 수정 + 작업억제 로직 강화 (`flows_deploy.json`)
- **노드**: `SQL 생성(발생)` (build_sql)
- **JSONB 경로 오류**: `tm.suspend_alarm_types->'suspend_alarm_types'` → `tm.suspend_alarm_types` (직접 배열 접근)
  - `suspend_alarm_types` 컬럼은 `["수위", "압력"]` 형태 직접 JSONB 배열로 저장됨
  - 기존 코드는 항상 `'[]'::jsonb`로 fallback → 작업 억제 기능 전혀 동작 안 함
- **카테고리명 정렬**: `'통신상태'` → `'네트워크'/'통신'`, `'펌프상태'` → `'펌프'`, `'밸브'` 추가
- **`'전체'` 처리 추가**: `sat.type = '전체'` 조건으로 사이트 전체 알람 억제
- **개별 태그명 매칭 추가**: `sat.type = ti.datadesc OR sat.type = ti.datainfo`

#### 3. 중복 open 알람 정리 (DB 직접 실행)
- **원인**: `NOT EXISTS(alarm_end_time IS NULL)` 중복 방지 체크가 없던 시기에 10초마다 INSERT → tagsn당 최대 82건 중복
  - `44270_24110_SYA_N001` (죽동(배) 탁도계 통신이상): 82건 중복
  - `44270_24110_LEA_N001` (죽동(배) 수위#1 HH 알람): 4건 등 총 88건
- **수정**: tagsn별 최신 1건만 open 유지, 나머지 `alarm_end_time = alarm_start_time`, `alarm_status = '알람해제'` 처리
- **SQL**:
  ```sql
  UPDATE tb_equipment_alarm_report
  SET alarm_end_time = alarm_start_time, alarm_status = '알람해제'
  WHERE alarm_end_time IS NULL
    AND (tagsn, alarm_start_time) NOT IN (
        SELECT tagsn, MAX(alarm_start_time)
        FROM tb_equipment_alarm_report
        WHERE alarm_end_time IS NULL GROUP BY tagsn
    );
  ```

#### 4. Node-RED PostgreSQL 연결 수정 (`flows_deploy.json`)
- **원인**: Node-RED(bridge 네트워크, 172.17.0.2)와 TimescaleDB(web_default 네트워크, 172.20.0.2)가 서로 다른 Docker 네트워크
- **postgresSQLConfig** (71827310c941a9d1): `host: localhost` → `host: 172.17.0.1` (bridge 게이트웨이)
- 효과: `0.0.0.0:5433` 포스트 포워딩으로 호스트 경유 접속

#### 5. `cagg_daily_pressure` 컬럼 참조 오류 수정 (`flows_deploy.json`)
- **노드**: `펌프 정보` (38b543d1e395154e)
- **원인**: `cagg_daily_pressure` 연속집계 뷰는 `bucket`, `tagsn`, `avg_val`, `min_val`, `max_val`만 보유 — `sitename`, `facilitytype` 없음
- `c.sitename` → `t.sitename`, `c.facilitytype` → `t.facilitytype` (JOIN된 `tb_tag_info t` 컬럼 사용)
- 서브쿼리 `SELECT max(bucket) FROM cagg_daily_pressure WHERE facilitytype = '가압장'` → JOIN 경유로 수정

### 완료 (2026-04-05 — 작업관리 태그 기능 보완)
- **TaskFormDialog 개별 태그 추가 항상 표시** (`src/components/crisis/TaskFormDialog.tsx`)
  - 기존: `sitename && facilitytype` 조건부 → 시설 선택 전 섹션 숨겨짐
  - 변경: 항상 표시, 미선택 시 안내문 "시설유형과 현장을 먼저 선택하면 개별 태그를 추가할 수 있습니다."
  - 태그 드롭다운 onBlur 150ms 딜레이 추가 (클릭 전 닫힘 방지)
- **수정 다이얼로그 시작/종료 시간 보존** (`task-management/page.tsx`)
  - 기존: 수정 시 defaults에 task_start_time/task_end_time 누락 → 현재 시각으로 초기화됨
  - 변경: editingTask.task_start_time/task_end_time을 defaults에 포함
  - TaskFormDialog 초기 상태도 defaults 있으면 해당 시간 사용 (DB 포맷 "YYYY-MM-DD HH:MM:SS" → "YYYY-MM-DDTHH:MM" 변환)
- **개별 태그명 알람 억제 로직** (`ai_server.py`)
  - 기존: `alarm_category`만 suspend_alarm_types와 비교 → 개별 태그명 억제 불가
  - 변경: `_is_alarm_suppressed(sitename, alarm_category, alarm_msg, active_tasks)` 시그니처 확장
    - 표준 카테고리 외 태그명은 `alarm_msg`에 포함 여부로 판정
    - `_ALARM_CATEGORY_NAMES` 상수로 표준/개별 구분
  - 실사용 예: "갈산" 가압장 작업 등록 시 suspend_alarm_types=["갈산(가) 유출압력(신규)", "갈산(가) 통신이상"] → 해당 alarm_msg 포함 알람 자동 억제

### 완료 (2026-04-05 — 계정 권한 Phase2 + 알람→작업관리 억제 로직, commit a640e9a)
- **계정 권한 Phase 2: 동적 메뉴 API 연동** (`src/hooks/use-sidebar-menus.ts`)
  - `/api/auth/me` 호출 → 백엔드 메뉴 우선 사용 (실패 시 정적 폴백)
  - 미인증/API 빈 응답 → 기존 정적 메뉴 + adminOnly 필터 폴백
- **알람→작업관리 억제 로직 (#36)**
  - `ai_server.py` — `GET /crisis/alarm-reports`에 `task_suppressed` 플래그 추가
    - `_get_active_task_suppressions()`: 현재 진행중인 작업(시간 범위 내) 조회
    - `_is_alarm_suppressed()`: '전체' 또는 alarm_category 매칭 시 억제
  - `crisis.ts` — `AlarmReportRecord.task_suppressed?: boolean` 필드 추가
  - `AlarmReportTable` — 행 우측 `ClipboardList` 작업 등록 버튼, 억제중 배지 + opacity-50
  - `dashboard/page.tsx` — `RecentAlarmList` 각 행에 작업 등록 버튼 추가

### 완료 (2026-04-04 — 대시보드 전체 폭 제약 해소)
- **layout.tsx 최대폭 제거** (`src/app/(dashboard)/layout.tsx`)
  - `2xl:max-w-screen-2xl 2xl:mx-auto` 클래스 제거
  - 원인: 디자인 개선 Step 8(와이드 레이아웃)에서 추가한 1536px 최대폭 제한이 모든 화면 해상도에서 콘텐츠 폭을 제약
  - 효과: 대시보드 종합현황판 캔버스가 사이드바 옆 가용 폭 전체를 채움

### 완료 (2026-04-04 — AI 질의 정확도 개선 3종, commit 48f0a25)
- **normalize_question 강화** (ai_server.py)
  - 숫자+달: "3달동안" → "90일동안", "6달간" → "180일간" (N×30일)
  - 숫자+개년: "3개년간" → "1095일간" (N×365일)
  - 오타: "작산" → "적산"
- **example3.json 질문 확장** (+45문항, 72→72인텐트, 708벡터)
  - NIGHT_MIN_FLOW_STATUS: +10 (트렌드/그래프/소소블록/중블록 패턴)
  - FACILITY_MIXED_TREND: +10 (복수시설/다중데이터 패턴)
  - FACILITY_TAG_LATEST_VALUE: +8 (최근 N분간 패턴)
  - RESERVOIR/BOOSTER EQUIPMENT_STATUS: +12
  - NIGHT_MIN_FLOW_SUMMARY_TABLE: +5
- **복수 시설 NMF 처리** (ai_server.py — NIGHT_MIN_FLOW_STATUS 커스텀 핸들러)
  - "행정1-1과 행정2-1 야간최소유량" → 두 시설 결과 병합
  - /ask + /ask/stream 양쪽 적용
  - FACILITY_TREND _is_night_min_flow 경로도 다중 시설 NMF 루프 추가
- **벡터 임계값**: VECTOR_THRESHOLD 0.75→0.68 (이전 세션)
- **임베딩 캐시 재구축**: 708벡터 (data/embeddings_cache.npy)

### 완료 (2026-04-04 — 네트워크 통신이상 알람 통합, commit da4e63f / edac6b2)
- **통신이상 알람 카드 (CommAlarmCard)**
  - `GET /network/comm-alarms`: 시리얼(DI 태그 val=1) + 이더넷(tb_network_status 이상) UNION
  - comm_type 필드 ('시리얼'/'이더넷') 추가, 유형별 필터 토글 버튼
  - 카드 제목 "시리얼 통신이상 알람" → "통신이상 알람", 이더넷 N · 시리얼 N 통계 표시
- **계층형 토폴로지 시리얼 장비 상태 표시**
  - `GET /network/topology`: serial_status CTE — DI 통신이상 태그 sitename+facilitytype 집계 → IP없는 장비 status 결정
  - hierarchical-topology.ts: has_ip 없어도 status 있으면 정상(초록)/이상(빨강) 도트 표시
  - isSerial 조건 수정: `!has_ip && !status` — DI 상태 있는 시리얼은 IP 장비처럼 선명하게
  - 이상 사이트 정렬, 링크 색상에 시리얼 이상 포함
- **NodeDetailPanel 시리얼 상태 뱃지**
  - "시리얼 (미모니터링)" → "시리얼 (정상)" 초록 / "시리얼 (이상)" 빨간 뱃지

### 완료 (2026-04-04 — Task 9+15+Node-RED #35)
- **Task 9: 사용자 관리 API 실DB 연동** (commit 981c593)
  - admin-user-api.ts: `/admin/users` → `/api/auth/users` 경로 정렬
  - `UserAuthType`: MASTER 추가, VIEWER 제거, LOCK_THRESHOLD 3→5
  - auth_crud.py: `create_user` 응답 user 객체 반환, `PUT /active`, `PUT /unlock`, `POST /session-end` 엔드포인트 추가
  - `last_login` varchar `.isoformat()` 버그 수정 (TypeError 해결)
  - `tb_access_log` user_agent 컬럼 없음 버그 수정
- **Node-RED #35: 통신이상감지 탭 추가** (flows_deploy.json)
  - 60초 주기 inject → DI 통신이상 태그(val=1) 시설 단위 집계
  - severity: 3개+ = '경고', 1~2개 = '주의', alarm_category='통신'
  - 해제: 모든 통신이상 태그 val=0 복귀 시 alarm_status='알람해제'
  - Node-RED REST API로 즉시 배포 완료

### 완료 (2026-04-04 — 팝업 최종, 용수공급 알람 팝업완료, commit dfef1ab)
- **Task 14-c: 용수 흐름 알람 클릭 → 경보분석 상세 팝업**
  - FlowNodeTrendPanel AlarmRow에 `onAlarmClick` prop + `role="button"` 클릭 핸들러 추가
  - flow/page.tsx에 `openAlarmDetail` 콜백 + AlarmAnalysisDetail Dialog 추가
  - `fetchAlarmAnalysisDetail(tagsn, startTime)` → AlarmReportRecord → Dialog 렌더링
- **Task 15: 사용자 수정 다이얼로그 (UserEditDialog)**
  - `UserEditDialog.tsx` 신규 — 이름·권한 변경 + 비밀번호 선택 초기화
  - `UserUpdateFormData` 타입 추가 (admin.ts), admin-user-api/store 업데이트
  - admin/users/page.tsx: `handleEdit` → `editTarget` 상태로 다이얼로그 오픈
- **대시보드 모바일 내부 스크롤바 제거**
  - 이상시설 TOP / 설비 장애 / 최근 경보 목록: `max-h / overflow-auto` → `md:` prefix로 데스크톱 전용화
  - 모바일에서 카드 내부 스크롤바 없이 전체 펼침, 페이지 스크롤로 통일
- **DB 커넥션 풀 누수 버그 수정 (ai_server.py)**
  - `_compute_flow_baselines()` finally 블록에서 `conn.close()` 누락 → 10분마다 커넥션 누수 → 풀 고갈
  - `finally: cur.close(); conn.close()` 로 수정
  - 증상: `/flow-map/realtime`, `/dashboard/overview` 등 모든 DB 엔드포인트 `"connection pool exhausted"` 반환

### 완료 (2026-02-21)
- 스윔레인 디지털 트렌드 (commit 9066332)
- 차트 재생 기능 TrendChart + PlotChart (commit 2c0eec6)
- 경보분석 로직점검 플로우차트 — CSS-only iframe 오버라이드 (commit 68ddf5b)
- 시맨틱 마커 전체 인텐트 적용
- 자동 알람 백그라운드 스캔
- 채팅 예시 질문 개선 (실제 현장명 포함)
- 문서 렌더링 개선 (매뉴얼 인텐트, detail 이미지 블록)
- 아날로그 듀얼 Y축 자동 분리
- 추천질의 현장명 자동 보강
- 채팅 사이드바 드래그 스크롤
- 구축 Setup 테이블 duplicate key 수정 (reservoir/booster general_overview)
- 태그 마스터 실데이터 연동 — tb_tag_info 2,698건, 서버사이드 페이징+필터, 태그추가/엑셀업로드/CSV다운로드
- 트렌드 태그 조합 비교 — mock 제거, tb_tag_info+tb_tag_raw_data 실데이터 연동, 5종필터+페이징 태그브라우저, POST /trend/data (TimescaleDB time_bucket), 최대15태그 자유조합

### 완료 (2026-02-22)
- 모니터링 메뉴 고도화 프론트엔드 — mock→실데이터 전환, 9개 파일 신규/수정
  - monitoring-config.ts 타입 + monitoring-config-api.ts API + monitoring-view-store.ts 스토어 + monitoring-utils.ts 유틸
  - TrendChart.tsx alarmMarkLines/title prop 추가 (HH/LL 가이드선)
  - MonitoringTrendBlock + MonitoringFacilityPage 공용 컴포넌트
  - reservoir/booster page.tsx 래퍼 교체 + block page.tsx 신규
  - MonitoringSetupPage + MonitoringTrendDialog + ExistingCatalogList 설정 페이지
  - sidebar-menus.ts: 감압시설→블록, 트렌드설정→모니터링설정
  - 빌드 검증 통과

### 완료 (2026-02-22 — 인텐트 분류 정확도 개선)
- 인메모리 벡터 유사도 검색 — snowflake-arctic-embed2 (1024dim), numpy cosine, NPY+JSON 캐시
  - intent_embeddings.py: IntentEmbeddingIndex 클래스 (load_or_build, search, embed_query)
  - intent_classifier.py: 3단계 분류 (keyword → vector ≥0.75 → SLM Phi-4-mini 폴백)
  - ai_server.py: startup 임베딩 캐시 빌드, force_intent 파라미터 지원
  - example3.json: 501 질문 (64 인텐트, 동의어 포함)
- 오분류 사후 보정 UI — 답변 하단 "원하는 답이 아닌가요?" + 대체 인텐트 후보 3개 표시
  - chat.ts: IntentCandidate 타입, AiServerResponse.intent_candidates
  - chat-response-mapper.ts: intent_candidates 매핑
  - BotMessage.tsx: IntentCandidatesSection (amber 테마 카드)
  - use-chat-submit.ts: submitWithForceIntent() — force_intent 파라미터 전달
  - ChatInput.tsx: submitCorrection DOM ref 노출
  - chat/page.tsx: handleCorrectIntent 콜백 연결
- 분류 정확도 테스트 및 개선 — 64% → 89% (25%p 향상)
  - example3.json: +63 동의어 질문, -2 혼동 질문 (440→501)
  - intent_classifier.py: 키워드 규칙 추가 (추이/같이→트렌드, 다발/빈번→경보다발)
  - ai_server.py: build_correction_response에 intent_candidates 반환 버그 수정
  - NEED_CORRECTION 후보 반환율: 0% → 67% (0/9→6/9)

### 완료 (2026-02-22 — 모니터링 고도화 추가, commit b932bf2)
- HH/LL 알람 설정 듀얼모드 — Analog Input만 표시, 상수/태그참조 선택
  - AlarmLimits 타입: hh_tagsn/ll_tagsn 태그참조 + hh/ll 상수
  - AlarmLimitCell 컴포넌트: 3모드 토글(없음/상수/태그) + Analog Output 드롭다운
  - monitoring-utils.ts: 태그참조 우선 해석, getAlarmRefTagSns()
  - monitoring-view-store.ts: 알람 참조 태그 시계열 일괄 조회 + 최신값 추출
- 모니터링 사이트 필터링 — 설정된(monitoring=true) 사이트만 모니터링 표시
  - ai_server.py: /monitoring/catalogs/sites에 monitoring_only 파라미터 추가
  - COALESCE 안전 캐스트 (meta.monitoring NULL 처리)
  - 설정 페이지는 전체 사이트 유지
- 트렌드 편집 기능 수정 — 다이얼로그 열릴 때 useEffect로 상태 초기화
- 인텐트 표시명 한글화 — 누락 7개 추가 (intent_index.py INTENT_DESCRIPTIONS)

### 완료 (2026-02-22 — 이상감지 고도화)
- 현장 프로파일링 + 그룹별 임계값 + 3단계 경보
  - site_profiler.py: SiteProfiler 클래스 (일 1회 유출유량/알람빈도/P95/P05 프로파일링)
  - tb_site_anomaly_profile 테이블 (원격 DB), A/B/C/D 그룹 분류
  - anomaly_detector.py: GROUP_THRESHOLDS, classify_z_level_by_group, classify_alert_grade(critical/warning/info)
  - anomaly_detector.py: analyze_level_pattern (C그룹 HH/LL 패턴 분석 3가지)
  - anomaly_detector.py: get_hh_ll_for_site (alarm_limits 우선, P95/P05 폴백)
  - anomaly_detector.py: build_*_detail_block에 site_profiles + alertGrade 시맨틱 마커
  - anomaly_iforest.py: GROUP_CONTAMINATION (A:0.03, B:0.05, C:0.08, D:0.05)
  - ai_server.py: _site_profiling_loop (60초 지연 후 24h 주기), lifespan에 등록
  - ai_server.py: GET /anomaly/profiles 디버깅 엔드포인트
  - ai_server.py: ANOMALY_SCAN_ALL에 site_group_distribution + 그룹별 Z-Score
  - ai_server.py: ANOMALY_FACILITY_DETAIL에 C그룹 패턴 분석 + site_group 표시
  - 프론트: anomaly-utils.ts에 ALERT_GRADE_*/SITE_GROUP_* 상수
  - 프론트: AnomalyScanView.tsx 그룹 분포 요약 카드
  - 프론트: AnomalyDetailView.tsx 사이트 그룹 뱃지 + 패턴 분석 경고
  - 프론트: chat.ts AnomalyData 타입 확장, chat-response-mapper.ts 매핑
  - proxy: PUBLIC_PATHS에 "anomaly" 추가
  - 빌드 검증 통과

### 완료 (2026-02-22 — 시간대별 기준선 보정)
- Z-Score 적응형 시간 기준선 — 평일/주말 × 피크/오프피크 4구간 분리
  - example3.json: ANOMALY_SCAN_ALL SQL CTE 교체 (raw_adaptive + time_ctx + stats_segment + stats_global + COALESCE 폴백)
  - example3.json: ANOMALY_FACILITY_DETAIL SQL CTE 동일 패턴 적용
  - 적응형 윈도우: interval '365 days' (가용 데이터 자동 사용, 30일→365일 자동 확장)
  - 구간별 최소 30건 미달 시 전체 기준선 폴백
  - answer_template 참고 문구 "동일 요일·시간대 기준" 반영
- CUSUM MNF 평일/주말 분리
  - anomaly_detector.py: _is_weekday() 헬퍼, compute_cusum_for_tags()에 wd_mean/we_mean 분리 기준선
  - CUSUM 계산 시 각 날짜의 요일에 맞는 기준선 적용
  - baseline_wd_mean, baseline_we_mean 필드 추가
- 프론트엔드 변경 없음 (컬럼명 하위 호환)

### 완료 (2026-02-22 — 용수 흐름 메뉴 구축)
- 용수 흐름 실데이터 연동 — tb_facility_flow_map 76건, ECharts 계통도, CRUD, CSV 입출력
  - setup.ts: FacilityFlowMap/FacilityFlowMapPK/FlowMapRoot 타입 (운영DB 스키마 기준 교체)
  - ai_server.py: 7개 API 엔드포인트 (GET/POST/DELETE /flow-map, roots, downstream, CSV export/import)
  - flow-map-api.ts: 프론트엔드 API 클라이언트 7개 함수
  - flow-diagram-chart.ts: ECharts graph 옵션 빌더 (시설유형별 색상/크기, BFS 하류 하이라이트)
  - FlowDiagramGraph.tsx: EChartWrapper 기반 계통도 (줌/패닝/리셋, 노드 클릭 하이라이트)
  - page.tsx: 실데이터+계통도+CRUD+CSV 전체 리팩토링
  - proxy: PUBLIC_PATHS에 "flow-map" 추가
  - FlowDiagram.tsx: CSS 기반 구버전 미사용 (ECharts로 대체)
  - 빌드 검증 통과

### 완료 (2026-02-23 — 설비 관리 CRUD, commit 9db1999)
- 설비 관리(tb_equipment_info) 실데이터 CRUD — 290건 운영DB 기반
  - setup.ts: EquipmentInfo/EquipmentMeta 타입 교체 (운영DB 스키마 기준)
  - ai_server.py: 6개 API (GET /equipments, /equipments/filters, /equipments/next-id, POST /equipments, PUT /equipments/{id}, DELETE /equipments/{id})
  - equipment-api.ts: 프론트엔드 API 클라이언트 7개 함수
  - EquipmentFormDialog.tsx: 12필드 추가/수정 폼, 접두사→equipment_id 자동생성 미리보기
  - EquipmentDeleteDialog.tsx: dry_run CASCADE 영향 확인 + 경고 표시
  - page.tsx: 서버사이드 페이징 50건 + 3종필터(현장명/시설유형/장비유형) + 키워드 + CSV
  - proxy: PUBLIC_PATHS에 "equipments" 추가
- 용수 흐름 계통도 SVG 전환 — ECharts→순수 SVG/DOM, BFS 레이어 배치, 줌/패닝/하이라이트

### 완료 (2026-02-24 — 네트워크 관리 고도화)
- 네트워크 관리 메뉴 CRUD + 시각 미리보기 — 4가지 요구사항 전체 구현
  - DeviceTable.tsx: 장비유형 필터 드롭다운 + equipment_id 컬럼 + 키워드 검색 확장
  - ai_server.py: POST/PUT/DELETE /network/links + GET /network/links/equipment-search (4 API)
  - ai_server.py: GET /network/links keyword 검색에 sitename ILIKE 추가, COUNT 쿼리 JOIN 수정
  - setup/networks/page.tsx: 437줄 → 25줄 thin shell (Tabs 셸)
  - NetworkInfoTab.tsx: 장비 탭 추출 (변경없음)
  - NetworkLinkTab.tsx: 연결 탭 + CRUD 버튼 + 현장명 2줄 표시 + CSV 현장명 포함
  - NetworkLinkFormDialog.tsx: 장비 자동완성 피커(EquipmentPicker) + 추가/수정 폼
  - LinkPreviewDialog.tsx: Force ↔ 계층형 토폴로지 미리보기 (90vw×78vh)
  - network-manage-api.ts: createNetworkLink/updateNetworkLink/deleteNetworkLink/searchLinkEquipment
  - setup.ts: NetworkLinkPayload, EquipmentSearchResult 타입 추가
  - 빌드 검증 통과
- 네트워크 연결 행 클릭 → 미니 플로우 프리뷰
  - link-flow-utils.ts: BFS 서브그래프 추출 (역방향 루트탐색 + 정방향 트리, max depth 4)
  - LinkFlowPanel.tsx: SVG 미니 트리 (depth별 열 배치, FACILITY_COLOR, 상태 도트, 하이라이트)
  - NetworkLinkTab.tsx: 행 클릭 선택/해제 + 테이블 하단 인라인 플로우 패널
  - topology 데이터 lazy fetch + useRef 캐시 (백엔드 추가 없음)
  - 빌드 검증 통과
- 네트워크 토폴로지 듀얼 뷰 — 이전 세션에서 이미 완료 확인
  - network/page.tsx: viewMode "force"|"hierarchical" 토글
  - TopologyGraph (ECharts Force) ↔ HierarchicalTopologyGraph (SVG/DOM 3컬럼 적응형)
  - 공유 selectedNodeId + NodeDetailPanel

### 완료 (2026-02-25 — tb_monitoring_catalog 분리 + 인텐트/추천질의 버그픽스)
- tb_monitoring_catalog 테이블 분리 (tb_trend_catalog → 모니터링 전용)
  - 원격 DB: CREATE TABLE tb_monitoring_catalog + 6행 마이그레이션
  - ai_server.py: 5개 모니터링 API SQL 교체 + GET /monitoring/catalogs/reference 신규
  - monitoring-config.ts: MonitoringCatalog flat 구조 (catalog_id, catalog_name, display_order, items)
  - monitoring-config-api.ts: monitoringOnly 파라미터 제거, fetchTrendCatalogReference 추가
  - monitoring-view-store.ts / monitoring-utils.ts: meta.* → 직접 필드 접근
  - MonitoringTrendDialog / MonitoringSetupPage / ExistingCatalogList: 필드명 교체
  - MonitoringTrendBlock / MonitoringFacilityPage: catalog_id, catalog.items 직접 접근
- 인텐트 분류 버그픽스
  - intent_classifier.py: 야간최소유량 "표준편차"/"분석" 체크 → "표" 체크 앞으로 이동
  - intent_classifier.py: "네트워크" 키워드 → FACILITY_COMMUNICATION_STATUS 직접 분류
  - intent_classifier.py: common_keywords에 "네트워크" 추가, _TAG_LATEST_EXCLUDE에 추가
- 추천질의 및 no-data 응답 개선
  - example3.json: "신평 배수지 압력 현황은?" → "신평 가압장 압력 현황은?" (배수지 압력 태그 없음)
  - example3.json: FACILITY_PRESSURE_STATUS 추천질의 → {sitename} {facilitytype} 포함 3건 교체
  - example3.json: "한달간 송악1 배수지 압력 트렌드" → "한달간 기지시 가압장 압력 트렌드"
  - ai_server.py: _NO_DATA_HINTS 딕셔너리 → 배수지 압력 쿼리 시 맞춤 안내 메시지
  - param_extractor.py: _SITENAME_FUZZY_STOPWORDS에 "전단","후단","가동" 등 8종 추가
  - use-chat-faq.ts: 야간최소유량 FAQ 질문 교체
  - 임베딩 캐시 재빌드

### 완료 (2026-02-26 — 네트워크 플로우 팝업 + 추천 질의 전수 테스트)
- 네트워크 연결 플로우 팝업 — 행 더블클릭 → Dialog (인라인 패널 → 팝업 전환)
  - NetworkLinkTab.tsx: selectedLink→flowLink, onClick→onDoubleClick, Dialog 래핑
  - LinkFlowPanel.tsx: Card/onClose 제거, NODE_W 140→170, NODE_H 44→48, 반응형 SVG
- 추천 질의 전수 테스트 — 92건 고유 추천 질의 테스트, 20건 수정
  - example3.json: 12개 인텐트의 recommend_questions에 {sitename} {facilitytype} 플레이스홀더 추가
  - RESERVOIR_LEVEL_HUNTING_CHECK: 3건 수정 (수위 트렌드, 계통도, 헌팅확인)
  - FACILITY_TAG_LATEST_VALUE: 3건 수정 (트렌드, 결측분석, 수위현황)
  - NIGHT_MIN_FLOW_STATUS: 3건 수정 (야간최소유량 트렌드/표준편차/결측)
  - FACILITY_COMMUNICATION_STATUS: 2건 수정 (네트워크상태, 최근알람)
  - FACILITY_COMMUNICATION_TOPOLOGY: 1건 수정 (통신상태)
  - FACILITY_MIXED_TREND: 2건 수정 ("1월 1일" → "최근 1주일간")
  - RESERVOIR_LEVEL_STATUS: 1건 수정 (가압장 가동현황)
  - RESERVOIR_SUPPLY_AVAILABLE_HOURS: 1건 수정 (비상급수→초동대응)
  - FACILITY_ADDRESS_INFO_RESERVOIR/BOOSTER/BLOCK: 3건 수정 (다른설비/블록→구체적)

### 완료 (2026-02-26 — 트렌드 시간 네비게이션)
- 트렌드 차트 좌/우 시간 네비게이션 — 모니터링 + AI 채팅 양쪽 적용
  - use-time-navigation.ts (신규): 핵심 네비게이션 훅 (prependedData, viewOffset, goLeft/goRight)
  - use-chart-playback.ts: onStop 콜백 추가 (확장 모드에서 정지 시 네비게이션 위치 복귀)
  - TrendChart.tsx: 네비게이션 통합 + 이전/다음 버튼 UI + 확장 데이터 재생 연동
  - PlotChart.tsx: 네비게이션 통합 + 이전/다음 버튼 UI (tag_ids 존재 시에만)
  - chat.ts: PlotData.tag_ids 필드 추가
  - chat-response-mapper.ts: buildPlotData/buildPivotedPlotData에 tag_ids 매핑
  - trend-to-plot.ts: tag_ids 전달
  - 버그 수정: 시간 포맷 로컬 타임스탬프 변환 (toISOString → formatLocalTimestamp)
  - 버그 수정: tagIds 배열 참조 안정화 (stableTagIds useMemo, 무한 루프 방지)
  - 빌드 검증 통과

### 완료 (2026-02-26 — ChartStatsBar 통합)
- DualPanelStats → ChartStatsBar 리네이밍 — 단일 트렌드에서도 ↑max ≈avg ↓min 통계 표시
  - ChartStatsBar.tsx 신규: 빈 섹션 자동 숨김 (아날로그만/디지털만/혼합 모두 대응)
  - TrendChart.tsx + PlotChart.tsx: isDualPanel 가드 제거, 항상 통계 바 렌더링
  - DualPanelStats.tsx 삭제
  - 빌드 검증 통과

### 완료 (2026-02-26 — ALARM_ABNORMAL_LOCATIONS 인텐트)
- 경보 이상 발생 지점 조회 — 65번째 인텐트 (통신/수위/압력/펌프/밸브/유량/전원 × 시설유형 × HH/LL/FAULT)
  - example3.json: ALARM_ABNORMAL_LOCATIONS (18개 질문, 빈 SQL, table, summary)
  - intent_classifier.py: "지점" 키워드 규칙 (ALARM_ABNORMAL_LOCATIONS)
  - intent_index.py: FACILITY_CATEGORIES + INTENT_DESCRIPTIONS 등록
  - ai_server.py: _extract_alarm_level (HH/LL/FAULT), 커스텀 핸들러(sync+SSE), 7일 폴백, 후처리
  - ai_server.py: _DYNAMIC_SQL_INTENTS (빈 SQL 조기 반환 우회), _TABLE_INTENTS_ALLOW_ALL 추가
  - query_validator.py: _SKIP_REQUIRED_CHECK 추가
  - chat-response-mapper.ts: alarm_start_time 컬럼 매핑 추가
  - chat-stream.ts: try/catch 분리 (JSON 파싱 에러만 catch, 콜백 에러 전파)
  - 프론트엔드 검증 통과 (Playwright 테스트)

### 완료 (2026-02-26 — 배수지 정보 구축 고도화)
- 배수지 관리(tb_service_reservoir_info + status) 실데이터 CRUD — 운영 DB 연동
  - ai_server.py: 5개 API (GET /reservoirs, GET /reservoirs/{sitename}, POST, PUT, DELETE)
  - ai_server.py: _serialize_reservoir_info/_status, _build_reservoir_general_overview 헬퍼
  - setup.ts: ReservoirInfo/ReservoirStatus/ReservoirEquipmentMeta 타입 교체 (운영DB 스키마 기준)
  - reservoir-api.ts: 5개 API 클라이언트 함수 (fetchReservoirs/fetchReservoir/create/update/delete)
  - ReservoirFormDialog.tsx: 3탭 (기본정보/구역설정/운영상태) + 설비메타 16항목 테이블
  - page.tsx: 서버사이드 페이징 50건 + 키워드 검색 + CSV 다운로드
  - general_overview JSONB → flat 변환 (install_location, facility_capacity_m3 등 + pump_required, supply_position, supply_time_hours)
  - status.meta: 16항목 배열 [{구분, 설비유무, 원격감시제어구분}]
  - proxy: PUBLIC_PATHS에 "reservoirs" 추가
  - ReservoirInfoForm.tsx, [sitename]/page.tsx 삭제, mock 데이터 제거
  - 빌드 검증 통과

### 완료 (2026-02-27 — FACILITY_CATALOG_TREND_TABLE 인텐트)
- 카탈로그 트렌드 표 — 66번째 인텐트 (배수지/가압장/소블록/소소블록/감압시설 × 수위/유출유량/유입유량/압력/유량/밸브/펌프)
  - example3.json: FACILITY_CATALOG_TREND_TABLE (10개 질문, 빈 SQL, table, table_type: summary)
  - intent_classifier.py: "표" + 데이터키워드 → FACILITY_CATALOG_TREND_TABLE 규칙
  - intent_index.py: INTENT_DESCRIPTIONS 등록
  - param_extractor.py: DATE_REQUIRED_INTENTS 추가
  - query_validator.py: _SKIP_REQUIRED_CHECK 추가
  - ai_server.py: _get_catalog_trend_filter() 헬퍼, _execute_catalog_trend_query() 2단계 청크 직접 쿼리
  - ai_server.py: _DYNAMIC_SQL_INTENTS + _TABLE_INTENTS_ALLOW_ALL + 커스텀 핸들러(sync+SSE)
  - TimescaleDB 성능 최적화: 49초→8초 (청크 직접 쿼리로 ChunkAppend 플래너 우회)

### 완료 (2026-02-27 — 청크 직접 쿼리 최적화 확대)
- TimescaleDB 청크 직접 쿼리 공용화 + TIMESERIES/POST /trend/data 최적화 (JOIN 39s→0.14s, 285배)
  - ai_server.py: 공용 유틸 4개 (_get_chunks_for_range, _query_chunks_agg, _reaggregate, _query_chunks_raw)
  - ai_server.py: _execute_timeseries_query — tb_tag_info → 청크별 raw 쿼리 → Python JOIN
  - ai_server.py: _TIMESERIES_CHUNK_INTENTS 5개 인텐트 커스텀 핸들러 (sync+SSE)
  - ai_server.py: POST /trend/data 청크별 집계로 교체
  - ai_server.py: SSE from_ts/to_ts 보정 누락 수정 (FLOW_ACCUMULATED/INSTANT 추가)
  - 실패 시 원본 execute_sql 자동 폴백

### 완료 (2026-02-27 — 구축 통합 캔버스 에디터)
- React Flow(@xyflow/react v12) 기반 통합 캔버스 에디터 — 78노드+76엣지 실데이터 연동, 4-Phase 전체 구현
  - canvas-config.ts: PALETTE_FACILITY_TYPES 6종, CANVAS_NODE_W/H, DND_FACILITY_TYPE, getFacilityStyle 재사용
  - canvas-types.ts: CanvasNode/CanvasEdge/CanvasLayoutResponse/CanvasNodeDetailResponse 타입, canvasEdgeId()
  - FacilityNode.tsx: 커스텀 노드 (시설유형 색상 테두리, 핸들, 설비/태그 배지, 선택 글로우)
  - FlowEdge.tsx: 커스텀 엣지 (Bezier, 호버 삭제 버튼, 20px 히트영역)
  - FacilityPalette.tsx: 6종 시설유형 드래그 소스 (HTML5 DnD)
  - canvas-store.ts: Zustand (nodes/edges/selectedNodeId/isDirty, applyNodeChanges/applyEdgeChanges/addEdge)
  - CanvasEditor.tsx: 3패널 레이아웃 (팔레트|ReactFlow+MiniMap+Controls|속성패널), DnD 드롭, Ctrl+S
  - CanvasToolbar.tsx: 자동배치/뷰맞춤/삭제/저장 버튼 + 미저장 표시
  - CanvasStatusBar.tsx: 노드/엣지 카운트, 선택 노드 ID
  - PropertyPanel.tsx: 3탭 속성 패널 (정보/설비/태그), useNodeDetail 훅으로 lazy load
  - SiteInfoTab.tsx: 현장명 편집, 시설유형, 설비/태그 카운트, 모니터링 상태
  - EquipmentTab.tsx: 설비 목록 + EquipmentFormDialog/DeleteDialog 연동 CRUD
  - TagMappingTab.tsx: 카탈로그 목록 + MonitoringTrendDialog 연동 CRUD
  - use-node-detail.ts: 선택 노드 상세 데이터 fetch 훅 (설비+카탈로그 lazy load, refresh)
  - canvas-types.ts: CanvasEquipmentItem/CanvasCatalogItem/CanvasNodeDetailResponse 구체 타입
  - use-canvas-persistence.ts: DB 로드/저장, 30초 자동저장, dirty 추적, beforeunload 경고
  - use-canvas-auto-layout.ts: BFS depth + Sugiyama median 교차 최소화 (8라운드), COL_GAP=260 ROW_GAP=80
  - canvas-layout-api.ts: fetchCanvasLayout/saveCanvasLayout/fetchNodeDetail API 클라이언트
  - ai_server.py: GET/PUT /canvas/layout + GET /canvas/node-detail/{sitename}/{facilitytype} (3 API)
  - ai_server.py: tb_canvas_node_position 자동 생성 (lifespan), 엣지 diff (추가/삭제), 고아 위치 정리
  - setup/canvas/page.tsx: dynamic import (SSR off)
  - sidebar-menus.ts: 구축 메뉴에 "캔버스 에디터" 추가
  - proxy: PUBLIC_PATHS에 "canvas" 추가
  - 빌드 검증 통과

### 완료 (2026-02-28 — 가압장/감압시설/블록 정보 구축 고도화)
- 가압장/감압시설/블록 CRUD — 배수지와 동일 패턴 (운영DB 실데이터 연동)
  - ai_server.py: 15 API endpoints + 9 helpers (booster 5 + pressure 5 + block 5)
  - setup.ts: 6 인터페이스 flat 교체 + EquipmentMetaItem 공용 타입
  - booster-api.ts, pressure-api.ts, block-api.ts: API 클라이언트 3파일
  - form-fields.tsx: Field/SelectField 공용 컴포넌트 추출
  - BoosterFormDialog.tsx: 2탭 (기본정보/운영상태) + 설비메타 26항목
  - PressureFormDialog.tsx: 2탭 (기본정보/운영상태) + 설비메타 5항목
  - BlockFormDialog.tsx: 2탭 (기본정보/운영상태) + 설비메타 5항목 + block_level 필터
  - booster/pressure/block page.tsx: 서버사이드 페이징 + 키워드 검색 + CSV 다운로드
  - [sitename] detail 페이지 3개 삭제, mock 데이터 제거
  - proxy: PUBLIC_PATHS에 "boosters", "pressure-reducing", "blocks" 추가
  - 빌드 검증 통과

### 완료 (2026-02-28 — 경보통합 + 헌팅 듀얼 알고리즘 + 캔버스 고도화)
- 경보관리 2탭 통합 — `/crisis/alarm-dashboard` 현황/이력 2탭, alarm-history redirect
  - alarm-dashboard/page.tsx: 2탭 구조 재작성 (현황 도넛+카테고리+테이블 / 이력 필터+확인)
  - alarm-history/page.tsx: redirect → alarm-dashboard?tab=history
  - sidebar-menus.ts: M006-1→"경보관리", M006-4 제거
- 헌팅 듀얼 알고리즘 비교 — [A] 3h 방향전환 + [B] 5m 분산뷰 동시 실행
  - ai_server.py: _execute_hunting_check() 듀얼 분석, build_hunting_result_block() 비교 표시
  - example3.json: answer_template 듀얼 형식 갱신
- 캔버스 에디터 Undo/Redo — Ctrl+Z/Y 키보드 + 툴바 버튼, 스냅샷 히스토리 MAX=50
  - canvas-store.ts: history/future 배열, pushHistory/undo/redo
  - CanvasEditor.tsx: 키보드 핸들러, CanvasToolbar.tsx: Undo2/Redo2 버튼
- 캔버스 PNG/SVG 내보내기 — html-to-image 기반
  - use-canvas-export.ts 신규: toPng/toSvg + getNodesBounds, CanvasToolbar.tsx: 내보내기 드롭다운
- 캔버스 설비↔태그 링크 — tb_equipment_tag_map CRUD
  - ai_server.py: DDL + 3 API (GET/POST/DELETE equipment-tag-link)
  - canvas-layout-api.ts: 3 API 함수, use-node-detail.ts: equipmentTags fetch
  - EquipmentTab.tsx: 접을 수 있는 설비별 태그 목록 + 연결/해제
  - EquipmentTagLinker.tsx 신규: 태그 검색 피커
- 프록시 PUBLIC_PATHS 정리 — admin/alarm/chat 통합 추가, 메뉴 401 에러 해소
- 빌드 검증 통과

### 완료 (2026-02-28 — 구축 CSV 다운로드/업로드 통합)
- CSV 다운로드 빈 템플릿 지원 + CSV 업로드 일괄 구현 (8개 구축 메뉴)
  - csv-utils.ts 신규: downloadCsv(빈 템플릿 지원), toCsvRow, escapeCsvField, uploadCsv, fetchAllPages 공용 유틸
  - CsvUploadDialog.tsx 신규: 범용 CSV 업로드 다이얼로그 (title, columns, onUpload, onComplete)
  - 8개 페이지 CSV 다운로드: 빈 데이터 가드 제거 → 헤더만 포함된 빈 CSV 템플릿 다운로드
  - ai_server.py: 8개 CSV import 엔드포인트 + 5개 헬퍼(_csv_cell/_csv_float/_csv_int/_csv_bool/_csv_json_array)
  - 7개 API 클라이언트: upload 래퍼 함수 추가 (tag/equipment/reservoir/booster/pressure/block/network-manage)
  - 8개 페이지에 업로드 버튼 + CsvUploadDialog 추가
  - TagUploadDialog.tsx stub → CsvUploadDialog로 교체
  - CSV 컬럼 전체 확장: 배수지 27컬럼, 가압장 18컬럼, 감압시설 9컬럼, 블록 11컬럼 (FormDialog 전필드 포함)
  - fetchAllPages: page_size=200 분할 페이징 (백엔드 le=500 검증 대응), arrow wrapper 패턴
  - 버튼 명칭 통일: "CSV 업로드" / "CSV 다운로드"
  - 백엔드 CSV import 컬럼 확장: 배수지 6→27, 가압장 7→18, 감압시설 6→9, 블록 7→11
  - 빌드 검증 + API 검증 통과

### 완료 (2026-03-02 — 태그 데이터 그룹 분류 시스템)
- TIMESERIES 인텐트 datainfo regex 오매칭 해결 — 계층형 그룹 기반 정확 매칭
  - ai_server.py: TAG_DATA_GROUPS 상수 21개 (유량/압력/수위/수질 + 하위 세분류)
  - ai_server.py: tb_tag_data_group DDL + tb_tag_group_map DDL (lifespan 자동 생성)
  - ai_server.py: _auto_classify_tags() — longest-keyword-first 전략, 2508/2698건 93% 분류
  - ai_server.py: _execute_timeseries_query — group_code 우선 JOIN → datainfo regex 폴백
  - ai_server.py: _resolve_group_codes() — 상위 그룹(FLOW) → 하위 전부(INSTANT+CUMULATIVE+INLET+OUTLET)
  - ai_server.py: sync + SSE 핸들러 — params.get("group_code") + intent-specific override
  - ai_server.py: GET /tags/groups — 그룹별 태그수 + 전체/분류/미분류 통계
  - param_extractor.py: _KEYWORD_TO_GROUP_CODE 매핑 17쌍 (compound→simple 순서)
  - param_extractor.py: _extract_group_code() + extract_all() group_code 필드 추가
  - 검증: "유입압력" → PRESSURE_INLET → 822행 (유입압력만), "압력" → PRESSURE → 1644행 (전체)
  - 프론트엔드 변경 없음

### 완료 (2026-03-02 — 인과관계 Rule 엔진 Phase 1)
- 인과관계 Rule 엔진 — 시설유형별 물리법칙 기반 인과 체인 템플릿 + 검증
  - ai_server.py: CAUSAL_CHAIN_TEMPLATES 5개 시설유형 (가압장/배수지/감압시설/소블록/소소블록)
  - ai_server.py: _build_causal_index() — 서버 시작 시 95개 시설 인과 인덱스 자동 구축
  - ai_server.py: ANOMALY_FACILITY_DETAIL 핸들러에 인과 검증 통합 (이상 태그 역추적)
  - anomaly_detector.py: verify_causal_context() — 5가지 인과 불일치 패턴 판정
  - anomaly_detector.py: _check_direction() — 시간 윈도우 방향 비교 (RISE/FALL/STABLE)
  - anomaly_detector.py: build_anomaly_facility_detail_block에 causal_result 시맨틱 마커 추가
  - ai_server.py: GET /causal/rules, /causal/verify 디버그 API
  - proxy: PUBLIC_PATHS에 "causal" 추가
  - 설계 원칙: 판단=Rule (물리법칙, 투명, 즉시), 설명=SLM (사후, 자연어)

### 완료 (2026-03-02 — SNMP 스위치 포트 진단 + 시각화)
- SNMP 스위치 포트 진단 — Mock 기반 13대 스위치 24포트 폴링 + NodeDetailPanel 포트 탭
  - snmp_poller.py (신규): SnmpPoller 클래스 (mock/real 듀얼모드, 조건부 pysnmp import)
  - snmp_poller.py: load_switches(), poll_all(), _poll_switch_mock(), _poll_switch_real()
  - snmp_poller.py: _save_port_status() UPSERT, get_ports(), get_system_info(), get_summary()
  - ai_server.py: tb_snmp_port_status DDL + _snmp_polling_loop (30초 후 첫 실행, 3분 주기)
  - ai_server.py: GET /network/snmp/{id}/ports, /network/snmp/{id}/system, /network/snmp/summary
  - src/lib/types/snmp.ts: SnmpPortStatus/SystemInfo/Summary + formatTraffic/formatSpeed/isSwitch
  - src/lib/api/snmp-api.ts: fetchSnmpPorts, fetchSnmpSystem, fetchSnmpSummary
  - src/hooks/use-snmp-ports.ts: lazy-load 훅 (탭 전환 시 활성화)
  - SwitchPortDiagram.tsx: SVG 포트 정면 다이어그램 (홀수 상단/짝수 하단, Up=emerald/Down=gray)
  - PortStatusTab.tsx: 요약카드 + SVG 다이어그램 + 필터 + 포트 테이블
  - NodeDetailPanel.tsx: 스위치 장비만 Tabs(정보/포트상태), 비-스위치는 기존 유지
  - 환경변수: SNMP_ENABLED(default:false), SNMP_COMMUNITY, SNMP_POLL_INTERVAL
  - 빌드 검증 통과

### 완료 (2026-03-03 — 인과관계 엔진 Phase 2)
- 캔버스 PropertyPanel 4탭 확장 — "인과 체인" 탭 추가 (정보/설비/태그/인과)
  - CausalChainTab.tsx (신규): 인과 체인 시각화 + 편집 + 구역 선택 + 자동 추정
  - PropertyPanel.tsx: grid-cols-3 → grid-cols-4, 4번째 탭 "인과" (causalChain 없으면 disabled)
  - canvas-types.ts: CausalChainStep/CausalCrossFacility/CausalZoneInfo/CausalChainResponse/CausalLagEstimate 타입
  - canvas-layout-api.ts: fetchCausalChain/saveCausalChain/deleteCausalChainOverride/estimateCausalLag 4 함수
  - use-node-detail.ts: causalChain 필드 추가, CAUSAL_FACILITY_TYPES 5종 조건부 fetch
- tb_causal_chain_override 오버라이드 테이블 + CRUD API
  - ai_server.py: DDL (SERIAL PK, UNIQUE(sitename, facilitytype, zone), JSONB chain/cross_facility)
  - ai_server.py: GET/PUT/DELETE /causal/chain/{sitename}/{facilitytype} (Pydantic BaseModel 사용)
  - ai_server.py: _build_causal_index 오버라이드 로딩, _rebuild_causal_index_entry 단건 갱신
  - ai_server.py: _get_causal_info(sn, ft, zone) 래퍼 (3-tuple 우선 → 2-tuple 폴백)
- 구역(1지/2지) 분리 — 배수지 태그 datainfo regex 구역 감지
  - ai_server.py: _detect_zones() — r'(\d)[지구역]' 패턴, zone별 tag_count/group_codes
  - ai_server.py: _CAUSAL_INDEX 3-tuple 키 확장 (sitename, facilitytype, zone)
  - anomaly_detector.py: verify_causal_context zone 파라미터 추가
  - ANOMALY_FACILITY_DETAIL: 이상 태그 datainfo에서 구역 자동 감지 → zone 전달
- 교차상관 시간 지연 자동 추정
  - causal_estimator.py (신규): numpy 교차상관 (Z-score 정규화, positive lag, scipy 미사용)
  - ai_server.py: POST /causal/estimate-lag — 14일 raw 데이터 → 연속 step 쌍 lag 추정
  - CausalChainTab: "자동 추정" 버튼 → amber 텍스트 표시 → "적용" 클릭 시 lag 교체
- SLM 자연어 해석 — Phi-4-mini 인과 판정 결과 자연어 변환
  - anomaly_detector.py: generate_causal_explanation() — _CAUSAL_EXPLAIN_PROMPT 3문장 해석
  - anomaly_detector.py: build_anomaly_facility_detail_block에 "AI 종합 해석" 섹션
  - ai_server.py: ANOMALY_FACILITY_DETAIL에서 chain_matched=false일 때 SLM 호출
- 빌드 검증 + API 전수 테스트 통과

### 완료 (2026-03-03 — 시설간 교차 검증)
- 시설간 교차 검증 시스템 — 상류→하류 유량/압력 흐름 일관성 자동 검증
  - anomaly_detector.py: cross_facility_check_single/all(), _check_edge(), _calc_active_rate/mean_direction()
  - anomaly_detector.py: build_cross_facility_detail_block/scan_block() 시맨틱 마커 포맷
  - anomaly_detector.py: _FACILITY_OUTPUT_GROUPS/INPUT_GROUPS 시설유형별 입출력 group_code
  - anomaly_detector.py: _wrap_marker 영문 level 직접 매칭 추가 (error/warn/ok)
  - anomaly_detector.py: build_anomaly_facility_detail_block에 cross_facility_result 파라미터 추가
  - ai_server.py: ANOMALY_FACILITY_DETAIL에 cross_facility_check_single 자동 통합
  - ai_server.py: ANOMALY_CROSS_FACILITY 커스텀 핸들러 (sync+SSE), _DYNAMIC_SQL_INTENTS 등록
  - ai_server.py: process_sql_result에 build_cross_facility_scan_block 연동
  - example3.json: ANOMALY_CROSS_FACILITY 67번째 인텐트 (12개 질문, graph_type:none)
  - intent_classifier.py: "교차 검증", "시설간 불일치", "상류 하류 비교" 키워드 규칙
  - intent_index.py: INTENT_DESCRIPTIONS 등록
  - query_validator.py: _SKIP_REQUIRED_CHECK 추가
  - 검증 유형: active_ratio(가동률 70%+ → 40%- 불일치), direction(상류 RISE + 하류 FALL 역전)
  - 프론트엔드 변경 없음 (graph_type:none + 시맨틱 마커)
  - 빌드 검증 통과

### 완료 (2026-03-03 — 태그 분류 오매칭 + 인과 검증 버그픽스)
- 태그 자동분류 복합 키워드 추가 — FLOW_INLET/OUTLET "유출유량순시" 등 6글자 복합어 우선 매칭
  - TAG_DATA_GROUPS: FLOW_INLET 키워드 +4 (유입유량순시/적산/순시유량/적산유량), FLOW_OUTLET 동일 +4
  - 분류 결과 변화: FLOW_INSTANT 108→60, FLOW_CUMULATIVE 101→55, FLOW_INLET 3→47, FLOW_OUTLET 4→54
  - 근본 원인: longest-keyword-first 전략에서 동일 길이(4글자) "유량순시"와 "유출유량" 중 리스트 순서 우선 매칭
- GROUP_THRESHOLDS import 누락 수정 — ANOMALY_FACILITY_DETAIL 핸들러 NameError 수정
  - ai_server.py: `from anomaly_detector import (..., GROUP_THRESHOLDS)` 추가
  - 이 에러로 인과 체인 검증 + 교차 시설 체크가 전체 try/except에 잡혀 무시되고 있었음
- 이상 태그 반복 탐색 — 첫 태그(잔류염소 등) group_code 미매칭 시 전체 스킵 → |z_score| 내림차순 순회
  - ai_server.py: _anomaly_rows 정렬 → group_code 매칭될 때까지 순회
- 형제 그룹(sibling) 매칭 — 소블록 FLOW_INSTANT↔FLOW_INLET 등 같은 부모 하위 그룹 호환
  - ai_server.py: _build_causal_index에서 resolved_tag_map 형제 그룹 폴백
  - anomaly_detector.py: verify_causal_context에 _SIBLING_MAP (FLOW 5형제, PRESSURE 3형제)
- 검증 결과: 남산 배수지 12항목(인과확인+하류영향 포함), 남산2 소블록 6항목(형제매칭), 갈산 가압장 9항목(z<임계→정상)
- 빌드 검증 통과

### 완료 (2026-03-04 — ANOMALY_SCAN_ALL 고도화)
- per-row grade/group 추가 — 각 데이터 행에 site_group/alert_grade 컬럼 보강
  - ai_server.py process_sql_result: site_profiles → classify_z_level_by_group + classify_alert_grade
  - `columns.extend(["site_group", "alert_grade"])` + `rows[:] = enriched_rows`
  - 결과: D=62, A=9, B=231, C=19 (per-row), warning=1, info=10, none=310 (grade)
- 교차 검증 SCAN_ALL 통합 — cross_facility_check_all을 ANOMALY_SCAN_ALL 핸들러에 추가
  - process_sql_result 외부에서 asyncio.to_thread로 비동기 실행 (이벤트 루프 블로킹 방지)
  - sync + SSE 양쪽 핸들러에 동일 적용
  - 결과: cross_facility_mismatches/count 필드 응답에 포함
- 하류 비활성 감지 — anomaly_detector.py _check_edge에 downstream_zero 체크 타입 추가
- 공용 헬퍼 _query_recent_values — 3곳(CROSS_FACILITY sync/SSE, FACILITY_DETAIL) 인라인 쿼리 통합
- AnomalyScanView.tsx 프론트엔드 — 서버 제공 grade/group 우선 사용 (classifyZLevel 폴백)
  - 테이블 뷰: "그룹" 컬럼 + site_group 뱃지 + alert_grade 뱃지
- 빌드 검증 + API 전수 테스트 통과 (321행, 13컬럼)
- **성능**: ~~응답 74.8초~~ → 캐시 히트 2.1초 (40배 개선, 아래 최적화 참조)

### 완료 (2026-03-04 — ANOMALY_SCAN_ALL 응답 최적화)
- 백그라운드 캐시 패턴 — 84초 전체 파이프라인을 5분 주기로 사전 계산, 캐시 히트 2.1초
  - ai_server.py: `_anomaly_scan_cache_loop()` (150s 초기 지연 → 5분 주기)
  - ai_server.py: `_compute_anomaly_scan_all()` (SQL+process_sql_result+cross_facility 전체 파이프라인)
  - ai_server.py: `_ANOMALY_SCAN_CACHE` / `_ANOMALY_SCAN_CACHE_TIME` / `_ANOMALY_SCAN_CACHE_TTL=300`
  - sync + SSE 핸들러: 캐시 freshness 체크 → 히트 시 즉시 반환 (early return)
- IForest 백그라운드 학습 — 요청 시 35초 블로킹 제거
  - ai_server.py: `_iforest_training_loop()` (90s 초기 지연 → 24h 주기)
  - process_sql_result: `ensure_trained()` 호출 제거, `predict_for_rows()`만 호출
  - 322개 모델 학습 (37초, 백그라운드에서 비동기 실행)
- Zero-flow z_score 정책 확정 — val≈0 → z=0 유지 (롤백)
  - 가압장 펌프 정지 등 val=0은 정상 운영 패턴 → z-score에서 이상 판정하지 않음
  - "뜻밖의 0" 판단은 인과 프로세스(verify_causal_context) + 교차검증(cross_facility_check)이 담당
  - deviation_pct: `ABS(mean_30d) < 0.001 THEN 0` 가드 유지 (division by zero 방지)
  - 설계 원칙: z-score=통계적 편차, 인과검증=물리적 의미 판단 (역할 분리)
- lifespan 등록: `_iforest_task` + `_anomaly_scan_task` (shutdown 시 cancel)

### 완료 (2026-03-04 — 인과검증 엔진 고도화)
- group_code 결정 개선 — 하드코딩 _GC_KEYWORDS → _resolve_group_code_for_tagsn() 교체
  - ai_server.py: 인라인 _GC_KEYWORDS 삭제, _CAUSAL_INDEX tag_map 우선 → _FALLBACK_GC_KEYWORDS 폴백
  - ai_server.py: 인라인 _causal_query_func 클로저 삭제 → 공용 _query_recent_values 사용
- 시설간 다중 홉 전파 추적 — BFS 기반 하류 전파 + 상류 근원지 역추적 (max_depth=3)
  - anomaly_detector.py: trace_propagation_forward() — 하류 BFS, 불일치 시 전파 중단 지점 기록
  - anomaly_detector.py: trace_upstream_root_cause() — 상류 역추적, 근원지 후보 + confidence(high/medium/low)
  - anomaly_detector.py: build_propagation_trace_block() — 시맨틱 마커 포맷 (근원지/전파중단/정상전파)
  - anomaly_detector.py: build_anomaly_facility_detail_block에 propagation_trace 파라미터 추가
  - anomaly_detector.py: _CAUSAL_PATTERNS에 UPSTREAM_PROPAGATION 패턴 추가
  - ai_server.py: process_sql_result ANOMALY_FACILITY_DETAIL에 전파 추적 통합 (인과/교차 결과 있을 때만)
  - 프론트엔드 변경 없음 (시맨틱 마커 {prefix, text} 형식 그대로)
  - 빌드 검증 + 단위 테스트 통과 (정상/불일치/리프노드 3 시나리오)

### 완료 (2026-03-04 — 설비↔태그 자동 매핑 Phase 1)
- 설비↔태그 자동 매핑 — _EQUIPMENT_GROUP_RULES 기반 그룹 레벨 매핑, 3,375건 자동 생성
  - ai_server.py: _EQUIPMENT_GROUP_RULES 상수 (가압펌프/유량계/PLC/LTE 모뎀 4종)
  - ai_server.py: _PUMP_NUM_RE — datainfo "가압펌프N" 패턴 1:1 매칭
  - ai_server.py: _auto_map_equipment_tags() — 설비별 규칙 적용 + ON CONFLICT DO NOTHING
  - ai_server.py: _resolve_group_list(), _map_pumps() 헬퍼
  - ai_server.py: GET /equipments/auto-map (dry_run 파라미터) API
  - ai_server.py: lifespan에서 _build_causal_index 직후 자동 실행
  - 매핑 결과: PLC 2,185건 + 가압펌프 519건 + LTE 모뎀 603건 + 유량계 68건
  - 가압펌프 1:1: "가압펌프N" → N번째 equipment, 번호 없는 태그 → 전체 펌프 공유
  - 네트워크 장비(L2/L3/UTM 등): 매핑 제외
  - 프론트엔드 변경 없음 (기존 EquipmentTab/EquipmentTagLinker 그대로)

### 완료 (2026-03-04 — 센서 점검 인텐트 통합 + 시설/센서유형 필터링)
- "전체 센서 점검해줘" = "전체 센서 이상 스캔해줘" → ANOMALY_SCAN_ALL 동일 처리
- 시설유형별 필터: "소블록/배수지/가압장/소소블록 센서 점검해줘" → facilitytype WHERE 절
- 센서유형별 필터: "유량/압력/수질 센서 점검해줘" → group_code JOIN 필터
  - intent_classifier.py: common_keywords에 "센서 점검/설비 점검/센서 스캔" 추가, _TAG_LATEST_EXCLUDE에 "점검/스캔/센서" 추가
  - param_extractor.py: _SITENAME_FUZZY_STOPWORDS에 "센서/유량계/수질계/압력계/장비/계측기" 추가
  - param_extractor.py: _FT_FUZZY_SKIP — "유량계"→"유량계실" fuzzy 오매칭 방지
  - ai_server.py: build_anomaly_facility_filter에 group_code SQL 필터 추가
  - ai_server.py: _filter_anomaly_cache_rows — 캐시 히트 시 facilitytype/group_code 필터
  - ai_server.py: _GROUP_CODE_LABELS — group_code→한글 범위 표시
  - ai_server.py: sync+SSE 캐시 히트 양쪽에 필터+카운트 재계산 적용
  - 결과: 전체 321건, 소블록 67건, 배수지 140건, 가압장 113건, 유량 82건, 압력 54건, 수질 43건

### 완료 (2026-03-04 — 소블록 인과 템플릿 수정 + snapshot_zero 교차 검증)
- 소블록/소소블록 CAUSAL_CHAIN_TEMPLATES 수정 — FLOW_INLET+WATER_LEVEL → FLOW_INSTANT+PRESSURE
  - ai_server.py: 소블록/소소블록 인과 체인 템플릿 group_code 교체
  - 근본 원인: 대부분 소블록에 FLOW_INLET(유입유량) 없고 FLOW_INSTANT(유량순시)만 존재 → 31/39 소블록 인과 인덱스 누락
  - 수정 후: 39/39 소블록 전체 인과 인덱스 등록
- snapshot_zero 교차 검증 체크 타입 추가 — 상류 최신값 활성 + 하류 최신값 ≈ 0 감지
  - anomaly_detector.py: _get_latest_value() 헬퍼 (시계열 끝 = 최신값)
  - anomaly_detector.py: _check_edge에 snapshot_zero 체크 (us_latest > 1.0 + ds_latest < 0.01)
  - anomaly_detector.py: build_cross_facility_detail_block + scan_block에 downstream_zero/snapshot_zero 표시
  - 결과: 남산11 소블록 → active_ratio(100%→24.4% error) + direction(RISE→FALL error) + snapshot_zero(warn)
- z-score zero-flow 정책 유지 — val≈0 → z=0 (펌프 정지 등 정상 운영 패턴, 인과/교차 검증이 물리적 판단 담당)

### 완료 (2026-03-04 — ANOMALY_SCAN_ALL 종합 판정 통합)
- 교차검증 결과를 per-row로 매핑 + 종합 판정(verdict) 컬럼 추가
  - anomaly_detector.py: map_cross_mismatches_to_facilities, compute_verdict, enrich_rows_with_cross_verdict
  - ai_server.py: _compute_anomaly_scan_all + sync/SSE 3곳 enrichment, 캐시 히트 cross_anomaly_count 재계산
  - ai_server.py: build_success_response에 cross_anomaly_count 전달
- 종합 판정 5단계: 복합이상(z이상+cross) > 이상(z이상) > 교차이상(z정상+cross) > 주의(z주의) > 정상
  - 남산11 케이스: z_score=0 + 상류 유량 활성 → verdict="교차이상" (이전: "정상")
- 프론트엔드 통합
  - chat.ts: CrossFacilityMismatch/CrossFacilityCheck 타입, AnomalyData/AiServerResponse 확장
  - chat-response-mapper.ts: crossFacilityMismatches/crossAnomalyCount 패스스루
  - anomaly-utils.ts: VERDICT_COLOR, CROSS_CHECK_LABEL 상수
  - AnomalyScanView.tsx: verdict 기반 필터/정렬/카운트, "교차"+"판정" 컬럼, KPI 교차이상 카드, CrossFacilitySummary 접기 요약
- 빌드 검증 + 단위 테스트 통과

### 완료 (2026-03-04 — 교차검증 임계값 강화 + 남산11 감지)
- 교차검증 오탐 축소 — 유량 전파 지연에 의한 false positive 제거
  - _CROSS_ACTIVE_HIGH/LOW: 70/40 → 85/15 (확실한 단절만 감지)
  - direction 체크 제거 (전파 지연으로 일시적 역전이 정상 패턴)
  - snapshot_zero 체크 제거 (간헐 운영 노이즈)
  - sudden_drop 임계값 완화: first_active 0.5→0.35 (남산11 45% 활성 패턴 포착)
- recent_inactive 체크 신규 — 상류 85%+ 활성인데 하류 최근 60분 전부 0
  - 전체 윈도우 22% 활성이라도 "지금" 죽어있으면 이상
  - 남산11: 180분 중 42분 활성(22%) → active_ratio/downstream_zero 미감지 → recent_inactive로 감지
- 교차검증 UI 상단 배치 — 고장에 가장 근접한 정보이므로 눈에 잘 띄는 상단 고정
  - AnomalyScanView.tsx: CrossFacilitySummary를 KPI 위로 이동 + 항시 표시(접기 제거) + 펄스 인디케이터
  - 결과: 2 edge (남산→남산10: active_ratio+downstream_zero, 남산→남산11: sudden_drop+recent_inactive)
  - verdict 분포: 이상 8, 주의 10, 교차이상 9, 복합이상 2, 정상 294

### 완료 (2026-03-05 — 기준선 오염 방지 + 데이터 품질 감지)
- hourly_holding CTE — 5분 버킷 flat 감지 + 시간 단위 집계 → 기준선에서 홀딩 시간 제외
  - example3.json: ANOMALY_SCAN_ALL + ANOMALY_FACILITY_DETAIL 양쪽 SQL CTE 삽입
  - raw_adaptive CTE: is_flat (min_val=max_val), bucket_hr (date_trunc hour) 추가
  - hourly_holding CTE: 1시간 내 전 버킷 flat + 1개 고유값 + 6건 이상 → 비영 홀딩 판정
  - stats_segment + stats_global: LEFT JOIN hourly_holding + WHERE IS NULL (홀딩 시간 제외)
  - 검증: 행정2-2 유량순시 deviation_pct 174.8% → 36.5% (오판 해소), 전체 278건 리그레션 없음
- 데이터 품질 이상 감지 (Layer 2) — ANOMALY_SCAN_ALL 결과에서 빠진 DEAD/홀딩 센서 별도 표시
  - ai_server.py: _detect_data_quality_issues() — Analog Input(NOT 적산/설정) 차집합 + 7일 상태 분류
  - 분류 4종: 센서무응답(val≈0), 데이터홀딩(flat>80%), 데이터없음(7일 무데이터), 데이터부족(active<50)
  - _compute_anomaly_scan_all 5단계에 통합 + build_success_response 패스스루
  - chat.ts: DataQualityIssue 인터페이스, AnomalyData.dataQualityIssues 필드
  - chat-response-mapper.ts: dataQualityIssues 패스스루
  - anomaly-utils.ts: DQ_ISSUE_COLOR, DQ_ISSUE_ICON 상수
  - AnomalyScanView.tsx: DataQualitySection 컴포넌트 (빨간 테두리+펄스, 유형별 카운트 뱃지, 접기/펼치기)
  - 결과: 76건 (센서무응답 62, 데이터홀딩 12, 데이터없음 2)
  - 남산10 유량순시유량, 남산11 유량계실 유속/압력, 행정 유입유량순시 모두 감지 확인
- 단기 홀딩 감지 (recent_holding) — 결과에 포함된 태그의 최근 1시간 홀딩 의심 인라인 표시
  - example3.json: recent_holding CTE (1h 내 전 버킷 flat + 1개 고유값 + val > 0)
  - AnomalyScanView.tsx: SensorItem.recentHolding 파싱 + 센서명 옆 주황 "홀딩?" 뱃지
  - KPI 카드: "홀딩의심" 주황 카드 (조건부 표시)
  - 결과: 278행 중 23건 recent_holding='Y' (매방리 유량순시, 가곡 수위 등)
- 설비 장애 역추적 Phase 2 — 설비 통신 끊김/장애 → 연결 태그 자동 점검 + UI
  - ai_server.py: _detect_equipment_failures() — 3가지 신호 (network_down/DI fault) + tb_equipment_tag_map 역추적
  - ai_server.py: _compute_anomaly_scan_all 6단계 통합 + per-row equip_failure 컬럼
  - ai_server.py: build_success_response + 캐시 히트 패스스루 (sync/SSE)
  - chat.ts: EquipmentFailureImpact 타입, AnomalyData/AiServerResponse 확장
  - chat-response-mapper.ts: equipmentFailureImpacts/Count 패스스루
  - anomaly-utils.ts: EQUIP_FAILURE_COLOR/LABEL/ICON 상수 (4종: network_down/comm_error/equip_fault/power_fault)
  - AnomalyScanView.tsx: EquipmentFailureSection (보라 테두리+펄스), per-row 인라인 뱃지 (NET/COM/FLT/PWR), "설비장애" KPI 카드
  - 결과: 59건 장애 설비, 98/278 per-row 매핑, 4종 장애 유형 (설비고장3, 전원이상12, 네트워크단절36, 통신이상8)
- 데이터 품질 감지 버그수정 — _detect_data_quality_issues s24[0] tagsn→s24[1] total_24h 인덱스 수정 (76건 복원)
- enrich_rows_with_cross_verdict 후 tuple 호환 — rows[:] = [tuple(...)] 패턴으로 per-row 컬럼 추가
- 인과 규칙 구축 메뉴 UI — 구축 사이드바에 "인과 규칙" 전용 페이지 추가
  - ai_server.py: GET /causal/rules 확장 — 시설 커버리지 + 오버라이드 현황 + tag_coverage per-step
  - setup.ts: CausalChainStepDef/CausalRuleTemplate/CausalFacilityStatus/CausalRulesResponse 타입
  - causal-rules-api.ts: fetchCausalRules API 클라이언트
  - setup/causal-rules/page.tsx: 요약 카드 4개 + 시설유형 5탭 + 템플릿 시각화 + 시설 테이블(프로그레스 바, 행 확장 상세)
  - sidebar-menus.ts: M200-12 "인과 규칙" 메뉴 추가
  - 결과: 96 시설 중 95 인과 적용, 58 완전 매핑 (가압장29, 배수지25, 감압시설0, 소블록39, 소소블록2)

### 완료 (2026-03-05 — 물 수지 검증 Mass Balance)
- 물 수지 검증 시스템 — 상류 유출유량 vs 하류 유입유량 합계 비교, 누수 의심 구간 감지
  - flow_balance.py (신규, ~280줄): 핵심 모듈
    - classify_balance_grade: <5% 정상, 5-15% 관심, 15-25% 주의, >25% 경고
    - _integrate_instantaneous: 순시유량 사다리꼴 적분 (m³/h → m³)
    - _cumulative_delta: 적산 태그 last-first delta
    - _compute_facility_volume: 시설별 유량 계산 (적산 우선, 순시 폴백)
    - compute_flow_balance_all: 전체 네트워크 수지 검증 (24h 롤링, 70% 커버리지)
    - build_flow_balance_scan_block: 시맨틱 마커 포맷
  - example3.json: ANOMALY_FLOW_BALANCE 68번째 인텐트 (10개 질문)
  - intent_classifier.py: "물 수지/물수지/유량 균형/불명수량/누수 구간" 키워드 규칙
  - intent_index.py + query_validator.py: 등록
  - param_extractor.py: "수지" fuzzy 오매칭 방지 (_FT_FUZZY_SKIP + _SITENAME_FUZZY_STOPWORDS)
  - ai_server.py: 8개소 수정
    - _FLOW_BALANCE_CACHE + _flow_balance_cache_loop (200초 지연, 30분 주기)
    - _query_flow_timeseries + _get_tag_datainfo_cache 헬퍼
    - ANOMALY_FLOW_BALANCE sync+SSE 커스텀 핸들러
    - process_sql_result: build_flow_balance_scan_block 호출 + flow_balance_summary 생성
    - build_success_response: flow_balance_summary 패스스루 (sync+SSE 양쪽)
    - _compute_anomaly_scan_all 7단계: 캐시 참조 flow_balance_summary
  - chat.ts: FlowBalanceDownstream/Edge/Summary 타입, AnomalyData 확장
  - chat-response-mapper.ts: flowBalanceSummary 패스스루
  - anomaly-utils.ts: FLOW_BALANCE_GRADE_COLOR 상수
  - AnomalyScanView.tsx: FlowBalanceSummarySection (청록 테두리, worst 5 엣지, 유량 비교 바) + "유량불균형" KPI 카드
  - 로컬 DB tb_facility_flow_map 스키마 교체 (76건 remote→local 복사)
  - 검증: 원격 DB 테스트 21 edges (경고 13, 관심 6, 정상 2), 빌드 통과

### 완료 (2026-03-06 — 시설 내부 인과 검증 Intra-Facility)
- 시설 내부 물리법칙 기반 인과 검증 — 펌프/밸브/수위/압력 6개 규칙
  - anomaly_detector.py: _INTRA_RULES 6개 (가압장3 + 배수지3 + 감압시설1)
    - 가압장: 펌프ON→토출압력, 펌프ON→유출유량, (PRESSURE_DISCHARGE fallback: PRESSURE_OUTLET→PRESSURE)
    - 배수지: 밸브OPEN→유출유량, 유입유량→수위 not_falling, 수위하강→유출유량(누출의심)
    - 감압시설: 유입압력→유출압력
  - anomaly_detector.py: verify_intra_facility(), _check_intra_condition/effect(), build_intra_facility_block()
  - anomaly_detector.py: _CAUSAL_PATTERNS 2개 추가 (LEVEL_DROP_NO_OUTFLOW, INLET_PRESSURE_NO_OUTLET)
  - anomaly_detector.py: _FACILITY_OUTPUT_GROUPS 가압장에 PRESSURE_DISCHARGE 추가
  - ai_server.py: ANOMALY_FACILITY_DETAIL 핸들러에 intra-facility 검증 통합
  - ai_server.py: build_success_response에 intra_facility 패스스루 (sync+SSE 양쪽)
  - chat.ts: IntraFacilityResult 타입, AnomalyData/AiServerResponse 확장
  - chat-response-mapper.ts: intraFacility 패스스루
  - 검증: 복운/고대리 가압장 (펌프ON → 정상판정), 남산 배수지 (유입유량→수위 정상판정)
  - 빌드 검증 통과

### 완료 (2026-03-06 — 용수 흐름 실시간 모니터링)
- 용수 흐름 실시간 모니터링 페이지 — 모니터링 메뉴 하위, 유량 비례 엣지 + 수치 오버레이
  - ai_server.py: GET /flow-map/realtime (토폴로지 + 시설별 최신 유량/수위/압력 + 교차검증 + 물수지)
  - ai_server.py: _group_priority() 헬퍼 (OUTLET > INSTANT > INLET 우선순위)
  - ai_server.py: tb_tag_group_map JOIN tb_tag_data_group (group_code 정확 매칭)
  - flow-monitoring-api.ts: fetchFlowMapRealtime + FlowRealtimeNode/FlowEdgeImbalance 타입
  - FlowMonitoringGraph.tsx: Sankey SVG 기반 실시간 계통도
    - 엣지: 상류 유량 비례 두께(2~14px) + 색상(회색→하늘→파랑), 유량 0=얇은 회색
    - 물수지 불균형 엣지: 경고=빨강, 주의=주황, 관심=노랑 + Bezier 중간점 %뱃지
    - 교차검증 이상 노드: 빨간 펄스 링 + 교차검증 상세 툴팁
    - 노드 수치 오버레이: Q(유량)/H(수위)/P(압력) 소형 텍스트
    - 하이라이트: 클릭 시 상류+하류 경로만 강조, 나머지 dim
  - monitoring/flow/page.tsx: KPI 4종(유량활성/없음/교차이상/불균형) + 계통 필터 + 60초 자동 갱신
  - sidebar-menus.ts: M003-4 "용수 흐름" 모니터링 하위 메뉴 추가
  - 검증: 79엣지 80노드, 유량 41/80 활성, 빌드 통과

### 완료 (2026-03-07 — HTTPS 전환 + Ollama 백오프 최적화 + 용수 흐름 계통도 교정)
- HTTPS 전환 — 브라우저→Next.js HTTPS, Next.js→API HTTP (내부 프록시)
  - mkcert 인증서: d:\web\certs\localhost.pem + localhost-key.pem
  - .env.local: HTTPS_ENABLED=true, NEXT_PUBLIC_API_URL=http://localhost:8000 (프록시는 HTTP 유지)
  - package.json: dev:https / dev:https:fast 스크립트 추가
- Ollama 백오프 최적화 — Ollama 비가용 시 요청 응답 38초→2.6초 (15배 개선)
  - 원인: embed_query(10초 타임아웃) + generate(30초 타임아웃) 매 요청 대기
  - intent_embeddings.py: 연결 실패 후 60초간 embed_query 즉시 None 반환
  - ollama_client.py: 연결 실패 후 60초간 generate() 즉시 에러, health_check 성공 시 리셋
  - ai_server.py: 서버 시작 시 health_check 실패하면 양쪽 백오프 즉시 설정
- 용수 흐름 계통도 교정 — tb_facility_flow_map 79→95엣지
  - facilitytype 수정 4건, 비존재 시설 삭제 5건, 누락 시설 추가 19건
  - install_location 기반 상류 추정 (석우/복운/율사/매방리/용연2통/죽동2)

### 완료 (2026-03-07 — 용수 흐름 배수지 공급가능시간 표시)
- 용수 흐름 실시간 모니터링에 배수지 용수공급가능시간(T) 오버레이 추가
  - ai_server.py: /flow-map/realtime에 tb_service_reservoir_status 조회 (total_supply_time, supply_time_status, supply_time_reason)
  - ai_server.py: 컬럼 미존재 시 안전 스킵 (try/except + conn.rollback)
  - flow-monitoring-api.ts: SupplyTimeInfo 타입 + FlowRealtimeNode.supply_time 필드
  - FlowMonitoringGraph.tsx: MetricOverlay에 T(공급시간) 표시 + 상태별 색상 (24h+하늘/12h+녹/6h+노/빨)
  - FlowMonitoringGraph.tsx: 툴팁에 공급시간 + 상태사유 표시
  - monitoring/flow/page.tsx: API 에러 시 nodes undefined 방어 (realtime.status === "OK" 체크 + ?? {} 폴백)
  - 로컬 DB: tb_service_reservoir_status에 total_supply_time/supply_time_status/supply_time_reason 컬럼 추가 + 원격 데이터 동기화

### 완료 (2026-03-07 — 쿼리 최적화 + 누락 함수 생성 + 기간 추출 수정)
- 야간최소유량 청크 최적화 — fn_night_min_flow_summary 대체 (31.9초→7.5초, 4.3배)
  - ai_server.py: _execute_night_min_flow_query() — 청크 직접 쿼리 + numpy 60분 이동평균
  - ai_server.py: sync+SSE 커스텀 핸들러 (NIGHT_MIN_FLOW_SUMMARY_TABLE)
  - UTC 타임존 보정 (time_bucket 호환), 원본 대비 100건 전수 일치 검증
- 결측분석 청크 최적화 — fn_tag_daily_summary 대체 (15.5초→2.8초, 5.5배)
  - ai_server.py: _execute_tag_daily_summary_query() — 청크별 분단위 SQL 집계 + Python 홀딩 계산
  - 466만 raw 행 Python 전송 → SQL 분단위 집계로 데이터량 100배 축소
  - ai_server.py: sync+SSE 커스텀 핸들러 (TAG_DAILY_MISSING_SUMMARY)
  - 원본 대비 31행 전수 일치 검증
- 누락 PostgreSQL 함수 13개 생성 — 원격 DB에서 추출 → 로컬 DB 적용
  - db/create_missing_functions.sql: fn_night_min_flow_stats, fn_trend_period_summary 등 13개
  - FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS: ERROR → OK 수정
- "N개월" 기간 추출 수정 — "최근 3개월간" 미파싱 → 기본 7일 폴백 문제
  - param_extractor.py: r"(\d+)\s*개월" 패턴 추가 (1개월=30일, 3개월=90일 등)
  - 기간 미지정 시 기본 7일 유지 (기존 동작 호환)
- 표준편차분석 청크 최적화 — fn_night_min_flow_stats 5회 호출 대체 (53초→3.6초, 15배)
  - ai_server.py: _execute_night_min_flow_stddev_query() — 400일 1회 조회 + Python 통계 계산
  - avg/stddev/신뢰구간/초과량 + 금월/금년 평균 전부 Python 산출
  - sync+SSE 커스텀 핸들러 (FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS)
- SSE 핸들러 sse_event → _sse_event 오타 수정 (3곳)
- 전수 테스트 스크립트
  - test_all_intents.py: 68개 인텐트 첫 번째 질문 자동 테스트
  - test_perf.py: 68개 인텐트 응답 시간 측정 + TOP 15 느린 인텐트 표시

### 완료 (2026-03-07 — 다중 시설 표준편차분석 뷰)
- 다중 시설 표준편차분석 뷰 — "전체 소블록 야간최소유량 표준편차분석" 분석 뷰 구현
  - ai_server.py: build_success_response에 stddev_stats_list 패스스루 추가
  - ai_server.py: sync+SSE 핸들러 — "%%" → "전체" 치환, 커스텀 답변 생성 (정상N/초과N)
  - chat.ts: StddevSiteStats/StddevMultiData 타입, stddev_multi VisualData union 추가
  - chat-response-mapper.ts: stddev_stats_list → stddev_multi 매핑 (stddev_stats 앞에 우선 배치)
  - StddevMultiAnalysisView.tsx (신규): KPI 4종 + 비교 바 차트 + 미니 정규분포 카드 그리드
  - VisualRenderer.tsx: stddev_multi 렌더링 분기 추가
  - 빌드 검증 통과

### 완료 (2026-03-08 — 용수 흐름 모니터링 UI 고도화)
- 통신이상 노드 점멸 표시 — Digital Input `datainfo LIKE '%통신이상%'` 기반 CSS step-end 애니메이션
- KPI 카드 클릭 필터 — 유량없음/교차검증이상/유량불균형/통신이상 카드 클릭 시 해당 노드 글로우 강조
  - page.tsx: activeFilter 상태 + filterNodeIds useMemo + toggleFilter 콜백
  - FlowMonitoringGraph.tsx: filterNodeIds prop, soft-dim 엣지(opacity .25), fm-filtered 노드(brightness+drop-shadow)
  - KpiCard: active ring + onClick 토글
- 선 교차/글자 겹침 Sugiyama 미세조정 시도 → 롤백 (오히려 악화)
  - 블록별 그룹핑 레이아웃으로 재설계 예정

### 완료 (2026-03-08 — 이동평균 확장 + 계통도 그룹핑 레이아웃)
- 모니터링 이동평균 확장 — 6개월/1년 조회 시 1개월/1년 이동평균 활성화
  - trend.ts: 6M interval 720→360min, 1Y interval 1440→720min (데이터 포인트 증가)
  - moving-average.ts: 가용성 판정 80% → 100% 완화 (expanding window + 버퍼 프라이밍으로 유효)
  - 6M: 720포인트 → ma30d 윈도우 120 ✓, 1Y: 730포인트 → ma1y 윈도우 730 ✓
- 계통도 블록별 그룹핑 레이아웃 — 선 교차/글자 겹침 근본 해결
  - flow-diagram-chart.ts: Sugiyama 이후 그룹 재배치 (primaryParent 기반)
  - 같은 상류 부모의 자식 노드를 수직 인접 배치
  - 그룹 간 GROUP_GAP=14px 여백으로 시각적 구분
  - 그룹 정렬: 부모의 레이어 내 인덱스 순
  - 결과: 신평공업/신평생활→우강→합덕 계통 인접 배치, 행정→행정1-2 교차 해소
  - 빌드 검증 통과
- 용수 흐름 프로토타입 HTML v2 — san.MD + SAN개선.MD 개선사항 반영
  - flow-monitoring-v2.html: 미니맵, 계층형 그룹화, LOD, 검색 하이라이트
  - 배수지 공급시간(T) 노드 오버레이 + 하단 공급가능시간 카드 바

### 완료 (2026-03-08 — v2 프로토타입 프로덕션 적용)
- 용수 흐름 모니터링 v2 기능 6종 프로덕션 적용
  - FlowMonitoringGraph.tsx: 미니맵 + 검색 + LOD + 계통 그룹 뷰 + 물흐름 파티클 통합
  - 미니맵: 좌하단 200×130px 축소 계통도, 뷰포트 사각형 표시, 클릭→해당 위치 이동
  - 검색: 좌상단 검색 아이콘, 시설명/시설유형 필터, Enter→첫 결과 포커스, 클릭→노드 줌+포커스 애니메이션
  - LOD: 줌 55% 이하에서 수치 오버레이(Q/H/P/T) 자동 숨김 (CSS .lod-hidden .fm-metric-overlay)
  - 계통 그룹 뷰: FlowViewMode "detail"|"grouped", BFS 루트별 그룹 카드, 상세/계통 토글 버튼
  - 물흐름 파티클: SVG animateMotion 3입자/엣지, dur=3.5s, 유량>0 엣지만, 불균형=빨강/정상=에메랄드
  - FlowNodeTrendPanel.tsx (신규): 노드 클릭 시 24h 스파크라인 차트 (Canvas), 유량/수위/압력 3종
  - page.tsx: 상세/계통 토글 + FlowNodeTrendPanel 통합 + 기존 MetricDisplay 제거
  - 빌드 검증 통과

### 완료 (2026-03-09 — 엣지 교차 수정 + 상태 기반 노드 색상)
- 엣지 슬롯 정렬 교차 수정 — 가나다순 → 타겟 노드 y좌표순 (합덕2/합덕3 교차 해소)
  - flow-diagram-chart.ts: links.sort를 source y → target y 기준으로 변경
- 상태 기반 노드 색상 — 진행 중 알람(경고/주의) 시 노드 빨강/주황 + 깜빡임
  - ai_server.py: tb_equipment_alarm_report 진행중 알람 조회, alarm_severity 필드 추가
  - flow-monitoring-api.ts: FlowRealtimeNode.alarm_severity 타입 추가
  - FlowMonitoringGraph.tsx: deriveNodeStatus에 알람 등급 반영, rect fill 상태 기반 색상, 알람 경고 링, 툴팁 알람 표시
  - page.tsx: "알람 진행" KPI 카드 + 알람 필터 추가 (6컬럼)
  - 빌드 검증 통과

### 완료 (2026-03-09 — 캔버스 태그 링크 저장/취소 + 2줄 표시)
- 캔버스 에디터 태그 링크 저장/취소 패턴 — 즉시 API → pending 로컬 관리
  - EquipmentTab.tsx: pendingAdds/pendingDeletes 상태 + effectiveTags useMemo
  - 저장 버튼: 일괄 API 호출 (삭제 먼저, 추가 후), 취소 버튼: pending 초기화
  - amber 저장/취소 바, 설비별 변경됨 표시, 추가(녹색)/삭제(빨강) 시각 표시
  - EquipmentTagLinker.tsx: 즉시 API → 부모 onLink 콜백 패턴 전환
- 태그 목록 2줄 레이아웃 — tagsn 위, datainfo(unit) 아래 서브텍스트
  - EquipmentTab.tsx + EquipmentTagLinker.tsx: 연결/삭제/검색 3곳 일관 적용

### 완료 (2026-03-09 — 용수 계통도 설비 장애 표출)
- 설비 장애 4종 감지 + 계통도 노드 뱃지 표시 — 네트워크 단절/통신이상/설비고장/전원이상
  - ai_server.py: /flow-map/realtime에 설비 장애 감지 추가 (네트워크 is_alive=false + DI 장애 3종)
  - flow-monitoring-api.ts: EquipFailure 타입 + FlowRealtimeNode.equip_failures 필드
  - FlowMonitoringGraph.tsx: 노드 하단 뱃지(NET/COM/FLT/PWR) + 툴팁 상세 + deriveNodeStatus 확장
  - page.tsx: "설비 장애" KPI 카드(보라색 Wrench) + 클릭 필터 (7종 KPI)
  - 결과: 24시설 감지 (네트워크7, 통신10, 설비고장3, 전원12)
  - 빌드 검증 + Playwright 검증 통과

### 완료 (2026-03-10 — 상류 유형별 교차검증 + fixed 트렌드 패널)
- 상류 시설유형별 교차검증 분리 — 배수지(중력식 80%) vs 가압장(펌프 30%+압력) vs 기타(30%)
  - ai_server.py: _GRAVITY_ACTIVE_RATIO=0.80, _LOW_ACTIVE_RATIO=0.30
  - ai_server.py: gravity_no_flow/pump_no_flow 체크 타입 추가
  - ai_server.py: _ds_us_ft/_ds_us_pressure 상류 시설유형+압력 추적
  - ai_server.py: cross_mismatches 빈 엔트리 필터링
  - FlowMonitoringGraph.tsx: gravity_no_flow/pump_no_flow 툴팁 라벨
- 갈산→성북 불균형 필터링 — 하류 데이터 없는 구간 제외
  - flow_balance.py: downstream_no_data 상태 추가 (태그 있지만 volume≈0 + coverage<50%)
- viewMode 전환 시 이벤트 리스너 재등록 — 계통↔상세 전환 버그 수정
  - FlowMonitoringGraph.tsx: 3개 useEffect 의존성에 viewMode 추가
- 노드 클릭 시 화면 하단 fixed 트렌드 패널 — 슬라이드업/다운 애니메이션
  - page.tsx: fixed bottom 패널, left=--sidebar-width, z-index:50
  - page.tsx: trendVisible 상태 + requestAnimationFrame 슬라이드 애니메이션
  - FlowNodeTrendPanel.tsx: 24h 스파크라인(Canvas) + 알람 목록 오버레이
  - flow-monitoring-api.ts: EquipFailure/SupplyTimeInfo 타입 추가
  - 계통도 높이 550px 고정 (레이아웃 리플로우 없음)
- 빌드 검증 + Playwright 검증 통과

### 완료 (2026-03-11 — 데모 완성본: 이미지 업로드 + 위치도 표시 수정)
- multipart 업로드 truncation 수정 — ReadableStream 소진 문제 해결
  - route.ts: request.body → await request.arrayBuffer() (73바이트→412KB 정상 전송)
  - route.ts: duplex: "half" 제거 (arrayBuffer는 duplex 불필요)
- DEMO_MODE multipart 역변환 — POST form-data sitename/region 코드→원본 변환
  - ai_server.py: upload_facility_file에 _demo_restore_text() 추가
- 위치도 중복 표시 제거 — 이미지 + "시설 위치" 카드 → 이미지만 표시
  - VisualRenderer.tsx: DiagramView에서 diagram_type="location" 시 null 반환
  - example3.json: 4개 위치 인텐트 answer_template에서 install_location 텍스트 + reference 섹션 제거
    - BLOCK_LOCATION, RESERVOIR_LOCATION, BOOSTER_STATION_LOCATION, PRESSURE_REDUCING_FACILITY_LOCATION
- 용수 흐름 계통 필터 수정 — tb_facility_flow_map DEMO코드('GSU') → 한글('정수장') 교체
- 임베딩 캐시 재빌드 (example3.json 변경 반영)
- 빌드 검증 + Playwright E2E 검증 통과

### 완료 (2026-03-12 — 트렌드 데이터 부족 안내 + HH/LL 재생 유지)
- 트렌드/모니터링 6개월+ 조회 시 데이터 부족 안내 — 로컬 DB 38일분만 보유 시 amber 배너 표시
  - trend-store.ts: dataGapInfo 상태 (요청 기간 vs 실제 데이터 비교, 80% 미만 시 안내)
  - monitoring-view-store.ts: 동일 dataGapInfo 패턴 적용
  - trend/page.tsx + MonitoringFacilityPage.tsx: amber 배너 UI
- 모니터링 HH/LL 가이드선 재생 시 사라지는 버그 수정
  - 원인: 재생 시 dataZoom 10% 윈도우 → Y축 자동 축소 → HH/LL 값이 축 범위 밖으로 밀림
  - TrendChart.tsx: alarmMarkLines 존재 시 Y축 min/max를 함수로 설정하여 HH/LL 항상 포함
  - 빌드 + Playwright 검증 통과

### 완료 (2026-03-13 — 용수 흐름 계통도 고도화: 타임라인+구역선택+기준선비율+엣지유량)
- 타임라인 재생 기능 — 과거 24h 데이터 5분 간격 슬라이더 재생
  - use-flow-timeline.ts (신규): 타임라인 훅 (태그 수집, fetchTrendData, 프레임별 노드 오버라이드)
  - page.tsx: TimelineBar 컴포넌트 (재생/정지/슬라이더/시간표시), 타임라인↔실시간 전환
  - page.tsx: 타임라인 모드에서 자동 갱신 일시정지 + 교차검증/불균형 숨김
- 배수지 구역 선택 탭 — 다중 수위 태그(1지/2지, 공업/생활) 전환
  - ai_server.py: /flow-map/realtime에 level_zones 배열 추가 (HH/LL/설정값 필터 + 자연정렬)
  - flow-monitoring-api.ts: FlowNodeMetric.datainfo + FlowRealtimeNode.level_zones 타입
  - FlowNodeTrendPanel.tsx: extractZoneLabel (공업용수→공업, 생활용수→생활 축약) + 구역 탭 UI
  - use-flow-timeline.ts: level_zones tagsn 수집 + 타임라인 오버라이드
- 평소 대비 비율 표시 — 수치 옆 baseline_avg 대비 % (색상: <50% 빨강, 50-80% 주황, >150% 파랑)
  - ai_server.py: /flow-map/realtime에 baseline_avg 추가 (7일 동일 요일·시간대 평균)
  - FlowMonitoringGraph.tsx: MetricOverlay에 ratioText/ratioColor + baselineRatioColor()
- 엣지 유량 수치 라벨 — Bezier t=0.3 지점에 유량 수치 표시 + LOD 연동
  - FlowMonitoringGraph.tsx: EdgeFlowLabel 컴포넌트 + fm-edge-flow-label CSS (줌 55% 이하 숨김)
- RootSummaryBar 삭제 — 미사용 ~90줄 제거
- 빌드 검증 + Playwright 검증 통과

### 완료 (2026-03-13 — 종합 현황판 Dashboard Overview)
- 종합 현황판 — 기존 캐시 데이터 집계 + 클릭→AI 채팅 자동 질문 연결
  - ai_server.py: GET /dashboard/overview 엔드포인트 (캐시 집계 + 24h 경보 조회)
    - _ANOMALY_SCAN_CACHE: KPI 카운트, verdict 분포, TOP 15 시설, 시설유형 분포
    - _FLOW_BALANCE_CACHE: 유량 불균형 요약
    - processed_data: data_quality_issues, equipment_failure_impacts
    - tb_equipment_alarm_report: 24시간 내 최근 경보 20건
  - dashboard-api.ts: 7개 인터페이스 + fetchDashboardOverview API 클라이언트
  - dashboard/page.tsx: KPI 6종 카드 + 이상시설 TOP + 유량불균형 + 시설유형분포 + 설비장애 + 데이터품질 + 최근경보
    - 모든 항목 클릭 → router.push(/chat?q=...) → AI 채팅 자동 질문 제출
    - 5분 자동 갱신 + 캐시 시간 표시
  - chat/page.tsx: useSearchParams → ?q= 파라미터 자동 질문 제출 + URL 정리
  - proxy route.ts: PUBLIC_PATHS에 "dashboard" 추가
  - 빌드 검증 통과

### 완료 (2026-03-14 — 물 수지 인포그래픽 + 시설별 필터링 + 대시보드 버그수정)
- 물 수지 인포그래픽 — ANOMALY_FLOW_BALANCE 전용 시각화 컴포넌트
  - FlowBalanceInfographic.tsx (신규): SVG 게이지(불균형률) + 등급별 분포 카드 + 수지 분포 바 + 파이프 카드(상류/하류 비교 바)
  - chat.ts: flow_balance VisualData union 타입 추가
- 물 수지 시설별 필터링 — "송산2산단생활 물 수지 검증해줘" → 해당 계통만 표시
  - ai_server.py: _filter_flow_balance_edges() 헬퍼 (upstream/downstream sitename 매칭)
  - sync + SSE 핸들러 캐시 히트/미스 양쪽에 필터 적용
  - chat-response-mapper.ts: ANOMALY_FLOW_BALANCE 인텐트 → flow_balance 타입 라우팅
  - VisualRenderer.tsx: flow_balance 렌더링 분기
- 대시보드 최근 경보 미표시 버그수정
  - ai_server.py: _get_conn() (미정의) → get_db_connection() 교체 (2곳)
  - 원인: NameError 예외 → except 블록에서 빈 배열 반환
- 대시보드 설비 장애 현황 "더보기" 개선
  - 8건 표시 + max-height 스크롤 + "더보기 →" 클릭 버튼 (AI 채팅 이동)
- 대시보드→채팅 자동 질문 미실행 버그수정
  - dashboard/page.tsx: router.push → window.location.href (full navigation으로 ChatPage fresh mount 보장)
  - chat/page.tsx: useSearchParams 제거, window.location.search 직접 폴링
  - 대시보드 9개 질의 전수 검증 완료 (2건 수정)
- 빌드 + Playwright 검증 통과

### 완료 (2026-03-14 — 대시보드 팝업 전환 + 경보분석 상세)
- 대시보드 클릭 질의 팝업 전환 — 채팅 이동 대신 QuickAnalysisDialog 팝업
  - dashboard/page.tsx: goChat()를 window.location.href → setQaOpen(true) 팝업 전환
  - KPI 카드, 이상시설, 유량불균형, 설비장애, 데이터품질 — 모두 팝업 적용
  - 데이터 품질 항목: onGoChat prop 추가 + "전체 센서 점검해줘" 통일 질의
- 최근 경보 클릭 → 경보분석 상세 팝업
  - ai_server.py: GET /crisis/alarm-analysis/detail?tagsn=...&alarm_start_time=... 단건 조회 API
  - crisis-api.ts: fetchAlarmAnalysisDetail() 함수 추가
  - dashboard/page.tsx: AlarmAnalysisDetail 컴포넌트를 Dialog 팝업으로 렌더링 (요약 카드 + diagnosed_msg iframe)
- 빌드 검증 통과

### 완료 (2026-03-14 — 모니터링 설정/블록 그룹핑 + 경보관리 고도화)
- 모니터링 설정 현장명 전체 표시 — tb_monitoring_catalog → tb_tag_info 소스 변경
- 감압시설 탭 추가 — MONITORING_CONFIGS + FACILITY_KEYS 확장
- 블록 모니터링 상류 시설 기준 그룹핑 — 계통 드롭다운 (남산 배수지(11), 합덕 배수지(2) 등)
  - GET /monitoring/catalogs/site-groups API (tb_facility_flow_map BFS 2단계 그룹핑)
  - fetchMonitoringSiteGroups API 클라이언트
  - MonitoringFacilityPage: 블록일 때 계통 Select + 하위 사이트 탭 필터링
- 모니터링 사이트 탭 가로 스크롤 — flex-wrap → overflow-x-auto (42개 블록에서 차트 가림 해소)
- 경보관리 행 클릭 → 경보분석 상세 팝업 — AlarmReportTable onRowClick + Dialog
- 경보관리 30건 페이징 — ChevronLeft/Right, 페이지 표시
- tb_monitoring_catalog PK 시퀀스 리셋 (last_value 4 → MAX 6)
- tb_task_master 테이블 생성 (Node-RED 작업관리 알람 억제용)
- Node-RED DB 접속 수정 (localhost → slm-timescaledb) + web_default 네트워크 연결
- Node-RED 펌프 정보 SQL 수정 (cagg_daily_pressure c.sitename → t.sitename)
- Node-RED JOIN LATERAL 12곳 시간 제한 추가 (now() - interval '10 minutes')
- 빌드 + Playwright 검증 통과

### 완료 (2026-03-15 — 용수 흐름 계통도 레이아웃 근본 개선)
- 서브계통 기반 수직 분리 — depth-1 분기를 서브계통 ID로 사용, BFS 전파
  - flow-diagram-chart.ts: nodeTopRoot(정수장별) + nodeRoot(서브계통별) 2단계 그룹핑
  - 서브계통 정렬: 하류 그룹키(같은 하류 공유 시 인접) → leafCount → 이름순
  - 루트 간 ROOT_GAP*2(56px), 서브계통 간 ROOT_GAP(28px), 부모 그룹 간 GROUP_GAP(14px)
- 보령댐/아산 정수장 계통 완전 분리 — 석문이 아산 계통에 섞이는 문제 해소
- 신평공업+신평생활→우강 인접 배치 — 같은 하류 공유 서브계통 연속 정렬
- 엣지 오프셋 클램핑 — inOffset/outOffset이 노드 높이 초과 시 노드 범위 내로 제한
  - 신평생활→우강 엣지 도착점 1060→999.7 (우강 범위 958~1033 내)
  - 다수 엣지가 동일 노드에 연결될 때 마지막 엣지 빗나감 방지
- 커밋: b59588d → 09082bd → 9430570 → fe9c387
- 모니터링 전체 시설유형 계통 그룹핑 — 배수지/가압장/감압시설/블록 모두 적용 (129dc5f)
- 배수지 일평균 유입/유출/사용량 — mv_reservoir_daily_flow 적산 max-min 방식 482건
  - Node-RED: 1시간 주기 갱신 플로우 추가 (운영현황 갱신 탭)
  - 적산유량 이상값 필터 (delta > 100,000 m³ 제외, 카운터 리셋 노이즈)
- 용수 흐름 배수지 패널 확장 — ▼ 버튼 클릭 시 유입/유출/사용량 표시 (4928711)
  - SupplyTimeInfo에 avg_inflow/avg_outflow/avg_usage 추가
  - /flow-map/realtime API에 v_reservoir_info_status JOIN

### 완료 (2026-03-15 — 모니터링 UI + 캐시 + 계통도 고도화)
- 모니터링 설정 현장명 전체 표시 + 감압시설 탭 추가
- 블록 모니터링 상류 시설 기준 그룹핑 → 계통 드롭다운 (이후 비활성화)
- 모니터링 사이트 탭 → FacilityCombobox 검색 드롭다운 교체
  - 권역 그룹핑 + 검색 필터 + 현장 카운트
  - rootFilter prop으로 정수장 계통별 필터링 가능
- site-groups API 재귀 BFS 6단계 확장 (정수장까지 추적)
  - 보령댐 5→7개, 아산 6→14개 배수지 올바른 그룹핑
- 경보관리 행 클릭 → 경보분석 상세 팝업 + 30건 페이징
- 대시보드 클릭 질의 팝업 전환 (채팅 이동 → QuickAnalysisDialog)
- cagg_5min_raw_stats_ai 자동 리프레시 정책 추가 (5분 주기)
- 캐시 빌드 초기 지연 단축 (IForest 10s, SCAN_ALL 20s, FlowBalance 30s)
- _compute_anomaly_scan_all 플레이스홀더 치환 수정 (빈 params 전달)
- 계통도 교차 최소화 — 리프 먼저 + 하류 노드 이름순 정렬
- 엣지 최소 두께 3px (EDGE_ZERO_W 1.5→3)
- flow_map: 송악1-1→고대리 연결 추가, 미분류 17건 동기화
- Node-RED JOIN LATERAL 12곳 시간 제한 추가 (10분)
- Node-RED 펌프 의사결정 트리 UPDATE null 가드 추가
- 대시보드 팝업 전환 (채팅 이동 → QuickAnalysisDialog) + 경보분석 상세 팝업
- 경보관리 행 클릭 → 경보분석 팝업 + 30건 페이징
- cagg_5min_raw_stats_ai 자동 리프레시 정책 추가 (5분 주기)
- 캐시 빌드 초기 지연 단축 (IForest 10s, SCAN_ALL 20s, FlowBalance 30s)
- _compute_anomaly_scan_all 플레이스홀더 치환 수정 ({anomaly_facility_filter} 빈 params)
- tb_site_anomaly_profile DDL 자동 생성 확인
- site-groups API 재귀 BFS 6단계 확장 (정수장까지 추적)
- 파티클 별도 SVG 레이어 분리 (엣지 겹침 → 파티클 안 보임 해소)
- 엣지 ID 한글 보존 (`\W` → `[^a-zA-Z0-9가-힣_]`, 모든 엣지 ID 충돌 근본 수정)
- __EXPAND__ 마커 프론트 필터링 (BotMessage + QuickAnalysisDialog)
- KPI 카드 레이아웃 flex-wrap 전환 (글자 잘림/넘침 해소) — 검증 완료
- 작업관리 mock 현장명 한글화 (B/F/G/D시설 → 남산/복운/매방리/행정)
- 채팅 SSE 진행 표시 라벨+도트 스타일 (분류→추출→조회→렌더링)

### 즉시 진행 가능
1. **인과관계 내재화 확장** — 가압장→소블록 cross-facility 신규 규칙 추가 (기존 6개 intra-rule 완료)
2. **인과 규칙 엔진 고도화** — 선형 체인 → 조건부 규칙 그래프 (선행조건/안전연동/역방향/AND/다중홉)

### 보류/후순위
3. **배수지 이상 스캔 컴팩트 레이아웃** — 보류 (유저 요청으로 리버트, 재논의 필요)
4. **엑셀 템플릿 보고서** — 프롬프트 기반 SLM 확장 (후순위)
5. **EPANET 수리 시뮬레이션** — 장기 과제 (SHP→inp변환 + wntr시뮬 + GIS히트맵, On/Off 토글 방식)
6. **UTM/SSLVPN 계층적 통신이상 감지** (사양 확정 후 구현)
   - 인텐트명(안): `NETWORK_UPSTREAM_FAULT_ANALYSIS`
   - 트리거 키워드: "상위 장비", "왜 다 통신이상", "UTM 이상", "SSLVPN 문제", "통신이상 원인"
   - 로직: `tb_network_link` 재귀 CTE로 UTM→SSLVPN→LTE 계층 트리 + `tb_network_status` 최신값 조인
   - 사양 확정 필요: 임계값(80% vs 전체), 신규 인텐트 vs 기존 `NETWORK_COMM_STATUS` 확장

### 완료 처리 이력 (이전 남은/향후 항목 중 완료된 것)
- ~~TIMESERIES 태그 조회 카탈로그 우선 전환~~ — 완료 (tb_tag_data_group 그룹 기반 전환)
- ~~인과관계 엔진 Phase 2~~ — 완료 (캔버스 인과 탭 + 구역 분리 + 교차상관 + SLM 해석)
- ~~시설간 교차 검증~~ — 완료 (ANOMALY_FACILITY_DETAIL 자동 + ANOMALY_CROSS_FACILITY 인텐트)
- ~~태그 분류 오매칭 + 인과 검증 버그픽스~~ — 완료 (복합 키워드, import 누락, 형제 그룹 매칭)
- ~~ANOMALY_SCAN_ALL 고도화~~ — 완료 (per-row grade/group, 교차검증 통합, 하류 비활성)
- ~~ANOMALY_SCAN_ALL 응답 최적화~~ — 완료 (84초 → 2.1초, 백그라운드 캐시 + IForest)
- ~~인과검증 고도화~~ — 완료 (group_code 정확매칭 + 다중 홉 전파 추적)
- ~~설비↔태그 자동 매핑 Phase 1~~ — 완료 (3,375건, 4종 장비유형)
- ~~용수 흐름 계통도 레이아웃 개선~~ — 완료 (서브계통 분리 + 엣지 클램핑)
- ~~배수지 일평균 유입/유출량~~ — 완료 (mv_reservoir_daily_flow + Node-RED)
- ~~모니터링 그룹핑 미분류 정리~~ — 완료 (원격 DB 17건 동기화)
- ~~Node-RED 펌프 의사결정 트리 누락 경로~~ — 완료 (fallback 확인, UPDATE null 가드)
- ~~용수 흐름 계통도 블록별 그룹핑 레이아웃~~ — 완료 (primaryParent + GROUP_GAP)
- ~~v2 프로토타입 프로덕션 적용~~ — 완료 (미니맵+검색+LOD+계통그룹+파티클+트렌드패널)
- ~~교차 검증 고도화~~ — 완료 (물 수지 검증 ANOMALY_FLOW_BALANCE)
- ~~디자인 개선~~ — 완료 (2026-03-22, 8단계)
- ~~코드 품질 종합 개선~~ — 완료 (모듈 분리 15,084→12,450줄)
- ~~설비 장애 역추적 Phase 2~~ — 완료 (59건 장애 설비, 4종 장애 유형)
- ~~용수 계통도 설비 상태 표출~~ — 완료 (4종 장애 뱃지, 24시설, KPI 7종)
- ~~용수 흐름 실시간 모니터링~~ — 완료 (유량 비례 엣지 + 교차검증/물수지)
- ~~성능 최적화~~ — 완료 (2026-03-22)
- ~~UX 개선~~ — 완료 (2026-03-22)
- ~~GIS 클러스터 범위 확대~~ — 완료 (2026-04-04)
- ~~용수 흐름 알람 클릭 → 경보분석 팝업~~ — 완료
- ~~인과관계 내재화 6종~~ — 완료 (PUMP_ON_NO_PRESSURE/FLOW, VALVE_OPEN_NO_FLOW, INLET_FLOW_NO_LEVEL_RISE, LEVEL_DROP_NO_OUTFLOW, INLET_PRESSURE_NO_OUTLET)
- ~~알람→작업관리 억제 로직 #36~~ — 완료 (04-05, alarm-reports task_suppressed)
- ~~계정 권한 Phase 1(인증API) + Phase 2(동적메뉴)~~ — 완료 (04-03 auth_crud.py + 04-05 use-sidebar-menus.ts)
- ~~계정 권한 Phase 3(메뉴 접근 제어) + Phase 4(MASTER 메뉴 관리)~~ — 완료 (04-07 권한 매트릭스 UI + DB 시드)
- ~~GIS 관망도 유량 흐름 오버레이~~ — 완료 (04-06~07, Phase 1~4)
- ~~ai_server.py 모듈 분리 추가~~ — 완료 (04-07, 3개 모듈 추출, 12,724줄)

### 완료 (2026-04-04 — 팝업 크기 통일 + GIS 레이어 z-order 수정)
- **팝업 크기 `max-w-2xl max-h-[75vh]` 통일** — 대시보드 내 모든 팝업창 동일 크기
  - `dashboard/page.tsx`: 경보 분석 상세 Dialog (max-w-4xl → max-w-2xl)
  - `alarm-dashboard/page.tsx`: 위기대응 경보분석 Dialog (max-w-4xl → max-w-2xl)
  - `QuickAnalysisDialog.tsx`: 이상시설/유량불균형 AI분석 (w-1600px/98vw → max-w-2xl)
  - `GisAlarmPopup.tsx`: GIS 경보 분석 팝업 width 600→520, maxHeight 80vh→60vh (원복)
- **GIS SHP 레이어 z-order 수정** — 도형 레이어(SHP)가 클러스터 마커 위로 올라오는 버그 수정
  - `GisFacilityMarkers.tsx`: `map.on("idle", bringMarkersToTop)` — idle 시 마커 레이어 최상단 이동
  - `GisShpLayer.tsx`: `beforeId="facility-circles"` 제거 (race condition E-006 해소)
  - 레이어 z-order: SHP 폴리곤 → 알람링 → 시설심볼 → 라벨 → 클러스터원/숫자
- **alarm-pulse-ring interpolate 수정** — CLUSTER_MAX_ZOOM=14 변경 후 줌 스텝 중복 (14,14,16 → 14,16,18)
- **시작/에러 사양서 신규** — `docs/error-management.md` + `docs/startup-spec.md`
  - E-001~E-006 에러 원인·해결·재발방지 기록

### 완료 (2026-04-05 — Task 15 사용자 수정 + 14-c)

- **15. 사용자 수정 다이얼로그** — `UserEditDialog.tsx` 신규 + `admin/users/page.tsx` handleEdit 연결
  - `UserUpdateFormData` 타입 추가 (이름·권한·선택적 비밀번호 변경)
  - `UserEditDialog`: 현재 사용자 정보 자동 로드, 이름·권한 수정 + 비밀번호 선택 변경
  - `admin-user-api.ts` updateUser: `Partial<AdminUser>` → `UserUpdateFormData` 타입 정확화
  - `handleEdit` stub → `setEditTarget` → 다이얼로그 오픈으로 교체
  - Playwright 3회 테스트 통과 (admin, operator1 수정 다이얼로그 열기·저장)

### 완료 (2026-04-05 — 3개 UI 개선)
- **14. GIS 클러스터 범위 확대** — `CLUSTER_MAX_ZOOM` 12 → 14 (`GisFacilityMarkers.tsx`)
  - 전체보기(zoom~11)에서 시설 이미지 대신 숫자 동그라미로 표시, zoom 15+ 부터 개별 SVG 전환
- **14-b. 배수지 이상 스캔 컴팩트 레이아웃** — `AnomalyScanView.tsx` early return 추가
  - `rawData.length === 0` 시 KPI카드/차트 생략 → 1줄 컴팩트 상태 카드로 표시
  - 교차검증/데이터품질/설비장애/유량불균형 이슈 있으면 목록 함께 표시
- **14-c. 용수 흐름 알람 클릭 → 경보분석 팝업** — `FlowNodeTrendPanel.tsx` + `flow/page.tsx`
  - `FlowNodeTrendPanel`: `AlarmRow`에 `role="button"` + hover 스타일 + `onAlarmClick` prop 추가
  - `flow/page.tsx`: `alarmDetailOpen/alarmDetailRecord/alarmDetailLoading` 상태 추가
  - `openAlarmDetail(tagsn, startTime)` → `fetchAlarmAnalysisDetail` 호출 → `AlarmAnalysisDetail` Dialog 표시
  - 팝업 규격: `max-w-2xl max-h-[75vh]` (대시보드 팝업 표준과 동일)

### 완료 (2026-04-04 — GIS 클러스터 동그라미 개선)
- **클러스터 클릭 애니메이션** — `flyTo/fitBounds` duration 2000ms + `essential: true` 적용
  - 전체보기 버튼(`GisMapRef.flyTo`)과 동일한 애니메이션 동작으로 통일
  - `essential: true`: 사용자 `prefers-reduced-motion` 설정과 무관하게 애니메이션 항상 실행
- **클러스터 레이어 최상단 보장** — GisFacilityMarkers.tsx 레이어 렌더 순서 변경
  - 변경 전: clusterCircle → clusterCount → symbolLayer → labelLayer → alarm-pulse-ring
  - 변경 후: alarm-pulse-ring → symbolLayer → labelLayer → clusterCircle → clusterCount
  - 클러스터 숫자 동그라미가 SHP 레이어, 알람링 등 모든 레이어 위에 항상 표시

### 완료 (2026-04-03 — 배수지 공급량 인텐트 4종 + 쿼리 최적화)
- **배수지 공급량 인텐트 4종 신규** — RESERVOIR_DAILY/MONTHLY_SUPPLY_TABLE/CHART
  - ai_server.py: `_execute_reservoir_supply_query` 신규 (LATERAL + generate_series 인덱스 스캔)
  - chat-response-mapper.ts: `supply_chart` 타입 매핑, TABLE_COLUMN_MAP에 `unit` 컬럼 추가
  - ReservoirSupplyChart.tsx: ECharts 막대그래프 (daily MM-DD / monthly YYYY-MM X축)
  - types.ts: `SupplyChartData` 타입 추가
  - VisualRenderer.tsx: `supply_chart` 분기 추가
- **공급량 쿼리 최적화 (300× 속도향상)** — 974,100행 풀스캔/24.5s → 736행/0.08s
  - LATERAL + generate_series 구조로 경계일 인덱스 직접 탐색
  - `idx_tag_raw_tagsn_time ON (tagsn, logtime DESC)` 역방향 스캔 활용
  - `generate_series` → `gs::date` 서브쿼리 캐스트 (timestamptz→date 타입 불일치 수정)
  - psycopg2 GUC: `max_parallel_workers_per_gather=0` (Docker WSL shared memory 오류 방지)
  - API 응답 단대단: 4.2s
- **경보 상위 건수 오류 수정** — alarm top count 인텐트 정상화
- **인텐트 분류 수정** — "그래프" + "공급량" 조합이 FACILITY_TREND로 오분류되던 버그 수정
  - intent_classifier.py: `_is_supply` 가드 추가 (공급량 인텐트 트렌드보다 우선)
- **Playwright 5종 테스트 통과** — 일별/월별 테이블+차트 4종 + 알람 상위건수 1종

### 완료 (2026-04-03)
- **인증 API 구현** — Python `/api/auth/login|refresh|logout|me` + 사용자 관리 + 접속 이력
  - endpoints/auth_crud.py: 신규 (bcrypt 직접 사용, python-jose JWT, 평문→bcrypt 자동 마이그레이션)
  - tb_user 실제 로컬 컬럼 기반 (user_pw_hash, pw_migrated, lock_cnt int, last_login varchar)
  - 로그인 실패 5회 → 계정 잠금 (lock_cnt >= MAX_LOGIN_ATTEMPTS), ADMIN 이상 해제
  - GET /api/auth/users, POST /api/auth/users, PUT /api/auth/users/{id}, POST /users/{id}/unlock
  - GET /api/auth/access-logs (ADMIN 이상, tb_access_log 조회)
  - requirements.txt: python-jose[cryptography], bcrypt 추가
- **proxy PUBLIC_PATHS 보안 강화** — 전체 경로 공개→login/refresh/health/models 4개만 공개
- **auth.ts dev 폴백 제거** — admin/1234, kwater/1234, dev-token 하드코딩 계정 전면 삭제
- **로그인 페이지 dev 힌트 제거** — "개발 모드: admin/1234 또는 kwater/1234" 텍스트 삭제
- **GIS clusterMinPoints=1** — 단독 시설도 원 "1" 표기
- **GIS 클러스터 클릭→fitBounds** — getClusterLeaves → 단일:flyTo zoom16, 복수:fitBounds maxZoom15
- **GIS SVG 지도 마커** — data URL 캔버스 방식 (map.loadImage Chrome SVG 미지원 우회)

### 완료 (2026-04-02)
- **GIS 심볼 KS 표준화** — 12종 SVG 아이콘을 KS B 0052 P&ID 표준 심볼로 교체 (투명 배경 벡터)
  - 밸브 6종: 보타이 형태 통일 + 색상/마크 구분 (G/B/S/D/A/P)
  - 알람 링 minzoom=12 (클러스터 줌 유령 링 제거), icon-ignore-placement 통일
- **GIS 고도화 Phase 1~2** — 마커 클러스터링 + SHP캐시 + 좌표검증 + 트렌드아이콘 + 팝업포맷 + 투명도슬라이더
- **GIS SVG 아이콘 24종 도입** — C-Water NAVI 참고사이트에서 밸브10종+시설8종+기타6종 추출
- **지도 마커 circle→symbol 변환** — SVG→ImageData→map.addImage() + styleimagemissing 대응
- **SVG 배경 투명화 + 크기 축소** — fill:#fff→fill:none, 24px, icon-size 0.35~0.7
- **ANOMALY_SCAN_ALL sitename 정확매칭** — 부분매칭→정확매칭, SSE 3곳 필터 적용
- **작업관리 전면 구현** — CRUD + 시설드롭다운 + 알람억제 + 개별태그 + 필터/정렬
- **jykim MASTER 계정** — 3단계 권한(MASTER/ADMIN/USER) + 메뉴 숨김/표시 관리
- **Node-RED 알람 아날로그값** — DB 트리거 fn_fill_alarm_analog_value + 126건 백필
- **미들웨어 인증 복원** + 캔버스 중복 수정 + 네트워크 장비 설비검색 + 프롬프트 숨김
- **EPANET 계획 기록** — 향후 별도 모듈, On/Off 토글 방식

### 완료 (2026-03-29)
- **기능 사양서 전면 작성** — docs/feature-spec.md 34개 섹션 (전 메뉴 커버)
- **용수흐름도→GIS 팝업 연동** — FlowNodeTrendPanel에 트렌드/시설/알람/진단 4버튼 추가, GIS 팝업 컴포넌트 재사용
- **Node-RED 통신이상 + 알람→작업관리** 사양서 작성 (feature-spec.md #35, #36)
- **미들웨어 인증 복원** — matcher에서 대시보드 라우트 제외 해제, 모든 페이지 인증 필수
- **GIS 시설정보 CRUD** — 사진 업로드/표시 + 제조사/설치연도 표시 (pump 객체 flat 변환)
- **AI Server** — `/gis/facility-info` 응답에 site_photo_url 추가
- **작업관리 전면 구현** — tb_task_master DB + CRUD API + TaskFormDialog
  - 작업등록: 시설유형→현장명 연동 드롭다운 (83개, DB facility_map)
  - 작업종류: 점검/정비/교체/청소/기타 선택
  - 억제 알람유형: 전체 버튼 + 개별 유형 9종 + 개별 태그 검색/추가
  - 개별 태그: 시설 선택 후 /tags API 태그 검색 → 하늘색 뱃지 추가/제거
  - 조회 필터: 작업일자(시작/종료) + 시설유형 + 현장명(드롭다운) + 작업종류 + 내용(키워드) + 진행중만
  - 테이블 정렬: 시간/현장명/시설유형/작업종류/중지알람/상태 (컬럼 클릭 asc/desc)
  - GisAlarmPopup 진행중 알람 → "작업등록" 버튼 연동
- **프롬프트 메뉴 숨김** — 현재 미사용

---

## 향후 계획 — SLM 고도화 (Zero-Hallucination 아키텍처)

> **배경**: Gemma4:26b (현재, Mac 17GB) → A30 24GB + Gemma4 12B (납품 표준) → L40S + Gemma4 27B (고품질)
> **핵심 원칙**: LLM은 라우터·분류기만 담당, 사실(수치/이름/ID)은 100% DB에서만 생성

### Phase 0 — 현재 (Gemma4:26b, 지금 당장 적용 가능)

**목표**: 기존 68 인텐트 구조 유지, 분류 정확도 + 답변 품질 + 할루시네이션 방어 기반 구축

#### A. 인텐트 분류 정확도 개선

- [ ] **오타/구어체 처리** — example3.json 동의어 질문 추가 (501개 → 600개+ 목표)
  - 오타 패턴: "수압" → "수알", "가압장" → "가압쟝" 등 현장 구어체 수집
  - 약칭 전처리: "1호가압" → "1호 가압장" 정규화를 intent_classifier.py 진입 전에 처리

- [ ] **오분류 패턴 수집** — 운전원 "원하는 답이 아닌가요?" 클릭 이력 → DB 저장
  - 수집만 자동화, example3.json 반영은 담당자 검토 후 수동 적용
  - ※ 검토 없는 자동 반영은 품질 저하 위험 → 수동 게이트 필수

- [ ] **벡터 임계값 최적화** — ※ 오분류 데이터 300건+ 축적 후 진행
  - 지금 튜닝하면 데이터 부족으로 과최적화 위험
  - 오분류 수집 후 혼동 인텐트 쌍 분석 → 인텐트별 차등 임계값 적용

#### B. 답변 품질 고도화

- [ ] **날짜 표현 파싱** — "그저께", "이번 달 초", "지난주 화요일" → 절대 날짜 변환
  - Python dateparser 또는 커스텀 규칙으로 LLM 개입 없이 전처리 단계에서 처리

- [ ] **시설명 약칭 매핑 테이블** — tb_facility 기반 약칭 → facility_id 사전 구축
  - "1호가압" / "1가압" / "1호 가압장" → 동일 facility_id 매핑
  - DB 테이블로 관리 (코드 하드코딩 금지)

- [ ] **시맨틱 마커 일관 적용** — `<<ok>>` `<<warn>>` `<<error>>` 전 인텐트 표준화
  - 현재 일부만 적용 → 모든 핸들러 응답에 상태 마커 포함
  - 임계값은 tb_tag_info 기반 (HH/HL/LL 설정값 활용)
  - 프론트엔드 BotMessage.tsx 마커 렌더링 고도화

- [ ] **프롬프트 구조 최적화** — Gemma4:26b 컨텍스트 128K 활용 few-shot 설계
  - 인텐트별 3~5개 few-shot 예시 (26b 컨텍스트 여유 충분 → 풍부한 예시로 분류 안정화)
  - JSON Schema 출력 지정으로 슬롯필링 구조화 응답 안정화

#### C. 할루시네이션 방어 레이어

- [ ] **Entity 검증 레이어** — LLM 추출값을 DB 조회로 실제 ID 치환
  - `facility_name ILIKE %...%` 퍼지 매칭 → 실제 facility_id 교체
  - 미발견 → "시설명을 확인해주세요" 반환 (추측 금지)
  - 복수 매칭 → 채팅 UI에 후보 버튼 표시 후 사용자 선택 (프론트엔드 신규 컴포넌트)

- [ ] **값 주입 프롬프트** — Post-hoc 검증 대신 Pre-hoc 제약으로 방식 변경
  - ~~regex 숫자 검증~~ → 오탐 과다로 폐기
  - 대신: LLM 프롬프트에 "사용 가능한 수치: {db_values}" 명시적 주입
  - LLM이 다른 수치를 생성할 원천 차단 (생성 후 검사 → 생성 전 제약)
  ```python
  prompt = f"""
  [사용 가능한 수치만 사용하세요]
  {json.dumps(db_result_values)}
  이 값들로만 설명하고, 목록에 없는 수치는 절대 사용하지 마세요.
  """
  ```

- [ ] **SQL 생성 완전 차단** — 모든 SQL을 SQL_TEMPLATES dict로 고정
  - LLM이 SQL 문자열을 반환하는 경로 제거
  - 파라미터는 psycopg2 바인딩만 허용 (f-string 금지)

---

### Phase 1 — 납품 서버 (A30 24GB + Gemma4 12B)

**목표**: 인텐트 68개 → 200개 확장, 서술 품질 향상, 보고서 초안 생성

- [ ] **인텐트 200개 확장 (Slot-Filling 유지)**
  - LLM 역할: 분류(N-class) + 파라미터 추출(JSON)만, SQL 미개입
  - SQL 템플릿 설계 시 복수 시설 파라미터 처음부터 반영
    ```sql
    WHERE facility_id = ANY(%(facility_ids)s)  -- 단수/복수 통일 패턴
    ```
  - 인텐트 목록을 프롬프트에 전부 나열하지 않음 → 카테고리 기반 2단계 분류
    (1단계: 대분류 10개 → 2단계: 소분류 20개씩)

- [ ] **보고서 초안 자동 생성** — DB 집계 결과 → LLM 서술화
  - tb_equipment_alarm_report 기반 일일/주간 통계 → 자연어 초안 생성
  - **사람 검토 단계 필수**: "초안 다운로드 → 담당자 확인 → 최종 저장" 워크플로우
  - 납품처별 양식 차이 대응: 섹션 템플릿 분리 (헤더/본문/서명란 독립 관리)
  - Word/PDF 다운로드 연동

- [ ] **이상감지 원인 설명 생성** — ※ 4계층 탐지 완성 후 진행
  - 선행 조건: ai_server.py 이상감지 pass 스텁 → tb_tag_group_map 기반 실구현 완료
  - 탐지 결과(수치) → "값 주입 프롬프트" 방식으로 LLM 서술화
  - 운전원 권고 문구는 고정 템플릿 (LLM 생성 금지)

---

### Phase 2 — 고품질 서버 (Mac Mini Pro 또는 L40S + Gemma4 27B)

**목표**: 응답 품질 최상위, 멀티모달 참고 기능, 장기 컨텍스트 대화

- [ ] **멀티모달 현장 사진 분석** — Zero-Hallucination과 분리된 별도 경로
  - 결과는 "AI 참고 의견"으로만 제공, DB 수치 기반 판단과 명확히 구분 표시
  - 분석 결과를 운영 데이터로 사용 금지 (면책 문구 포함)
  - 폐쇄망 사진 업로드 경로 별도 설계 필요 (모바일 → 내부망 서버)

- [ ] **EPANET 결과 해석** — ※ EPANET 모듈 구현 완료 후 진행
  - 선행 조건: EPANET 수리 시뮬레이션 모듈 구현 (docs/gis_plan.md)
  - 시뮬레이션 JSON → LLM 해석은 "설명 전용", 운영 지시 금지
  - 수리학 판단은 LLM이 내리지 않음 → EPANET 결과 수치를 그대로 표시

---

### 아키텍처 원칙 (불변)

```
[질문]
  → ① Intent 분류   : LLM (N-class 택1, 자유 생성 없음)
  → ② Entity 추출   : LLM 후보 → DB 검증 → 실제 ID 치환
  → ③ SQL 실행      : 고정 템플릿 + 검증된 파라미터 (LLM 개입 없음)
  → ④ 답변 생성     : DB 결과값 주입 프롬프트 → LLM 서술 (값 외 생성 차단)

사실 출처: 100% DB
LLM 역할: 분류 + 라우팅 + 포맷팅 전용
멀티모달: 별도 경로, "참고 의견"으로만 제공
```

---

### 태그 분류 체계 현황 (문서화 참고)
- **tb_tag_info.datainfo**: 태그의 실제 의미 (유출유량, 유입압력, 수위 등) — 유일한 의미 정보원
- **tb_tag_data_group** (21그룹): FLOW_INLET/OUTLET/INSTANT/CUMULATIVE, PRESSURE_INLET/OUTLET/DISCHARGE 등 계층 구조
- **tb_tag_group_map**: datainfo 키워드 기반 자동 분류 (2,508/2,698건 = 93%)
- **인과 체인 tag_map**: group_code → [tagsn 리스트] — 시설별 인과 step에 태그 배정
- **이상감지 group_code 결정**: 현재 ai_server.py에서 datainfo 키워드 하드코딩 (pass 스텁 → tb_tag_group_map 미사용) — 향후 교체 필요
- **설비↔태그(tb_equipment_tag_map)**: 3,375건 자동 매핑 완료 (PLC:2185 + 가압펌프:519 + LTE:603 + 유량계:68), 설비 장애 역추적 Phase 2 대기
