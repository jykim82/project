# SLM Dashboard - Next.js Frontend

## 🎯 제품 정체성

**본 소프트웨어는 제품화가 목표다.** 개인 프로젝트·학습용·참고 사이트 복제가
아니라, **상수도 구축 업체(SI·솔루션 벤더)에 판매되는 상용 제품**으로
설계·구현한다.

### 타겟 사용자
- **수운영자** — 배수지·가압장·블록 일상 감시·대응
- **SCADA 운영자** — 알람·설비 통합 감시·통제
- **상수관망 운영자** — 관망 구성·유량·압력 관리

### 핵심 제공 가치 (SLM)
**S**upervisory **L**ifecycle **M**anagement — 상수도 자산의 감시·알람·장비
고장·관망을 **조회·구축이 쉬운 고도화된 시스템**:
1. **감시** — 실시간 계통도/GIS/트렌드로 설비 상태 가시화
2. **알람** — 분류별 경보 + 오탐·미검지 분석 + 현장 조치 통합
3. **장비 고장 관리** — 채팅 AI 진단 + 고장 케이스 DB + 조치 이력·교체 후보
4. **관망 구축·조회** — 시설·설비·태그·매뉴얼·GIS 를 일관된 모델로 구성·관리

### 비즈니스 모델
**B2B 제품 판매** — 구축 업체(SI)가 본 제품을 구매해 최종 고객(지자체 상수도
사업본부·K-water·민간 수도 운영사)에 설치·운영. 제품은 **폐쇄망 온프레미스**
설치를 전제로 하며, 외부 인터넷·클라우드 LLM 의존 없음 (모든 AI 는 로컬
Ollama). 구축 업체별 커스터마이징은 **config·미그레이션 분리 설계**로 대응.

### 차별화 포인트
1. **AI 멀티모달 현장 진단** (E-025) — 채팅 사진 업로드 → 장비 식별 + 매뉴얼
   RAG + 고장 케이스 DB 통합. 폐쇄망에서 동작하는 유일한 접근
2. **알람 ↔ 현장 조치 통합 분석** — 자동 알람 오탐·미검지를 실제 조치 이력과
   대조해 **교체 후보** 식별 (P5-rev, `/monitoring/equipment-health`)
3. **자연어 장애·조치 기록** — 현장 작업자가 대화로 고장 등록·사진 첨부·
   조치 완료 기록. 시설·설비·태그 자동 매칭
4. **Zero-Hallucination 원칙** — AI 참고 의견은 DB 사실과 시각·구조적으로
   분리 (별도 violet 카드, `[AI 참고 의견]` 접두어, 수치 생성 차단)

### 제품화 판단 기준 (에이전트 의사결정 지침)

신규 기능·UX·구조 결정 시 아래를 기준으로 판단한다:

- **"참고 사이트에 없다"** 는 기능 제외 사유가 **아니다**. 타겟 사용자 가치
  기준으로 판단.
- **"일단 돌아간다"** 수준은 미완성으로 간주. **성능·보안·감사·문서·배포**
  까지 포함한 제품 품질 기준 적용.
- 신규 기능은 **전체 고객 배포**를 가정 — 고객별 플래그가 필요하면 처음부터
  config 로 분리. 하드코딩 금지.
- **호환성·업그레이드 경로** — migration 추가 시 롤백 절차 명시, 데이터
  손실 없는 업그레이드 가정.
- **폐쇄망 제약 상시 적용** — 외부 API·CDN·cloud LLM·원격 인증 의존 금지.
- **멀티 고객 격리** — `region` 기반 멀티테넌시 유지. 전역 상태는 피한다.
- **다국어는 한국어 1차**. UX 텍스트 하드코딩 회피로 i18n 분리 경로 확보.
- 쓰레기 코드·미완성 커밋·테스트 없는 PR 은 제품 품질을 해친다.

### 참고 사이트의 역할
`http://112.166.183.65:15000/` (kwater/1234) 는 **요구사항 소스 중 하나**로만
활용한다. UI/UX 참고 대상이며 복제 목표가 **아니다**. 본 제품의 차별화
포인트는 위 4개를 기준으로 독자 유지·확장한다.

---

## 참고 사이트 (레퍼런스)
- URL: http://112.166.183.65:15000/
- 로그인: kwater / 1234
- 상수도 운영 시스템 UI/UX 참고 대상 (복제 아님 — 위 "제품 정체성" 참조)

## 기술 스택
- Frontend: Next.js 16 (App Router) + TypeScript + Tailwind CSS + shadcn/ui
- Charts: ECharts (apache-echarts + echarts-for-react)
- State: Zustand
- Auth: NextAuth.js (CredentialsProvider → Python API JWT)
- Backend: Python FastAPI (별도 레포 `../slm/`, Docker Compose 로 통합 기동)
- DB: PostgreSQL 16 + TimescaleDB
- Vision: Ollama `gemma4:26b-a4b-it-q4_K_M` (호스트), Embed `snowflake-arctic-embed2`

## 개발 환경
- OS: macOS (이전 Windows 10/11 에서 이전됨 — `docs/migration-to-mac.md`)
- 프로젝트 루트: `/Users/jykim/web` (Backend 레포: `/Users/jykim/slm`)
- DB: Docker OrbStack → TimescaleDB `postgresql://slm_dev:slm_dev_1234@localhost:5433/slm`
- 파일 저장: `/Users/jykim/web/files/` (컨테이너에선 `/data/files/` 바인드)
- 개발 계정: `jykim` / `midi1212` (임의 변경 금지 — `memory/feedback_no_password_change.md`)

## API 연동
- Python API Base: http://localhost:8000 (컨테이너 slm-backend)
- Vision Agent: http://localhost:8100 (호스트 네이티브 — Metal GPU 가속)
- Next.js BFF: `/api/proxy/*` → Python API 프록시
- 파일 서빙: `/api/files/*` → 프런트 라우트 (`slm-frontend` 가 `/data/files` 바인드 읽기)
- 인증: JWT (Python API 발급, region + user_id + auth_idn 포함)

## DB 핵심 사항
- region 기반 멀티테넌시 (대부분의 PK에 region 포함)
- tb_tag_raw_data: TimescaleDB 하이퍼테이블
- 비밀번호: bcrypt (pw_migrated 플래그로 AES→bcrypt 점진 마이그레이션)
- 이미지: tb_file_storage + 파일시스템 (`files/facility/`, `files/chat_attachments/`)
- 채팅 날짜: timestamp with time zone (ask_at, bot_at)
- 채팅 시각화: tb_ai_chat_bot.visual_data (jsonb) → 히스토리에서 차트 재렌더링

## 메뉴 구조
- tb_menu.pmenu_idn으로 트리 구조 (자기참조 FK)
- tb_auth_menu로 권한별 접근 제어
- tb_menu.app_path → Next.js 라우트와 1:1 매핑
- **신규 페이지 추가 시**: `sidebar-menus.ts` (정적 fallback) + `tb_menu` INSERT 둘 다 필요

## 채팅 응답 구조 (56개 인텐트)
- graph_type: none(26) / table(14) / diagram(8) / document(4) / plot(2)
- SSE 스트리밍: token → answer_complete → visual → recommend → done
- 상세 스키마: `docs/slm-api-contract-final.md`

## 컨벤션
- **커밋 게이트: `npx tsc --noEmit` 0건** (2026-07-18 제로화 완료 — 신규 오류를 만들지 말 것. "기존 오류"는 더 이상 없음)
- 컴포넌트: PascalCase, `src/components/`
- 페이지: `src/app/(dashboard)/` 내 라우트 그룹
- API 호출: `src/lib/api-client.ts` 중앙 관리
- 타입: `src/lib/types/` DB 스키마 기반 정의
- 상태: Zustand `src/stores/`
- 다크모드 기본, 라이트모드 토글 지원
- 한국어 UI (날짜 YYYY-MM-DD, 숫자 한국식)

## 코드 작성 원칙

실제 프로덕션 환경에서 운영 가능한 수준으로 작성.

### 필수 원칙
1. 단일 책임 원칙(SRP)
2. 200줄 이상이면 파일 분리
3. 하드코딩 금지, 설정은 config 분리
4. 비즈니스 로직과 입출력 로직 분리
5. 예외처리 및 로깅 포함
6. 중복 코드 제거
7. 확장 가능 구조
8. 함수 50줄 이하
9. 주석은 "왜"를 설명
10. 유지보수 가능한 클린 코드

### 품질 요구사항
구조적 분리 / 예외처리 / 로깅 / 테스트 가능성 / 확장성 / 가독성 / 성능

---

# 📂 디렉토리 구조 (/Users/jykim/web)

```
web/
├── CLAUDE.md                      # 본 파일 — 프로젝트 헌장
├── docker-compose.dev.yml         # 전체 dev 스택 (backend/frontend/db/node-red)
├── dump-postgres-*.sql            # DB 스키마 원본
├── .env, .env.example             # 환경변수
├── agents/                        # Claude Code 서브에이전트 정의
├── skills/                        # Claude Code 스킬 정의
├── certs/                         # HTTPS dev 인증서
├── db/
│   └── migrations/                # 0043_*.sql ~ 0050_*.sql 순차 마이그레이션
├── docs/                          # 공개 문서 (§문서 인벤토리 참조)
│   ├── dialog_log/YYYY-MM-DD.md  # 일자별 세션 대화 로그 (3일 롤링)
│   └── examples/                  # 예제 파일 (xlsx 템플릿 등)
├── files/                         # 런타임 파일 저장소 (컨테이너 `/data/files` 바인드)
│   ├── chat_attachments/          # 채팅 사진
│   ├── facility/                  # 시설 사진·매뉴얼 PDF
│   └── chat_ask_image/, chat_bot_image/  # 레거시
├── slm-dashboard/slm-dashboard/   # 프런트 Next.js submodule
├── screenshots/                   # 개발 세션 스크린샷 (dev 참고용, 릴리스엔 불필요)
├── dev-logs/                      # 레거시 서버 로그·디버깅 텍스트
├── dev-data/                      # 런타임 덤프 JSON (payload_*, flow_*, flows_*)
├── prototype/                     # HTML 프로토타입 초안
├── scripts/                       # 개발 편의 Python 스크립트
└── gis-demo/                      # GIS 독립 데모
```

**정리 원칙:** 루트에 *.png / *.txt / *.json 방치 금지 → 목적별 폴더로 이동.

---

# 📚 문서 인벤토리

신규 문서 생성 시 **반드시 이 인벤토리에 한 줄 등록**. 누락된 문서는 에이전트가
찾지 못해 같은 작업을 중복하거나 기존 원칙을 어기게 됨.

## 정책 (`docs/*-policy.md`)
- `docs/fault-category-policy.md` — 장애 분류 4종(고장/이상/교체/점검) 정의 + 알람="이상", 현장 확인 고장만 "고장" 원칙
- `docs/chart-rendering-policy.md` — ECharts 렌더링 정책 (default svg + 부드러운 애니메이션 + .echart-svg-mode CSS, TopologyGraph만 canvas 유지)
- `docs/popup-expand-policy.md` — 팝업 전체화면(크게보기) 기본 제공 정책 (DialogContent expandable 기본 true, 전체화면 오버레이 유지 결정 — 사이드바 제외 확장 안 함)
- `docs/ui-motion-policy.md` — UI 모션 정책 (신규 애니메이션 라이브러리 금지, 채택 3종: ActionStateButton 진행 버튼·테마 View Transition·`.slm-stagger-in` 순차 등장. motion/GSAP 배제 사유 기록)

## 사양 (`docs/*-spec.md` 및 관련)
- `docs/slm-api-contract-final.md` — Python ↔ Next.js API 규약 (56개 인텐트)
- `docs/intent-architecture-spec.md` — 인텐트 레지스트리+훅 아키텍처 (1단계 완료: example3.json 에 dynamic_sql·classify_keywords 선언 → 파생 생성. 인텐트 추가 = JSON 한 곳. 2단계 핸들러 레지스트리·3단계 card_type 계획)
- `docs/slm-setup-phase-spec.md` — 기초정보 구축 + 컬럼 잠금
- `docs/voice-input-spec.md` — 음성 입력 (로컬 Whisper STT, 채팅 마이크 버튼. 도메인 용어 프롬프트 바이어스. 웨이트 1.5GB git 제외 — 납품 번들)
- `docs/tag-monitoring-spec.md` — 태그 모니터링 (모니터링 그룹 — 현재값 컬럼 + 이상 카테고리 9종 필터 + 컬럼 정렬 + 우클릭 트랜드 보기, CSV·태그추가 제외, 4 Phase)
- `docs/alarm-category-summary-spec.md` — 분류별 경보 현황
- `docs/alarm-confirm-audit-spec.md` — 경보 확인 책임 추적 v1 (Migration 0131 confirmed_by/at — 최초 확인만 기록·소급 추정 금지·자동해제는 기록 안 함. confirm/resolve 양쪽 기록, 목록 확인자 표시. P2=미확인 메신저 상신)
- `docs/alarm-chattering-spec.md` — 반복 경보(채터링) 정리 v1 (죽동 탁도계 1건이 30일 경보의 80.7%·확인율 0.3% → 목록 접기 "반복 N회" + 순위표 `/crisis/alarm-dashboard?tab=repeat`. 판정=평균 지속 15분(시간당 횟수 기준은 실측 오분류로 폐기), 채터링=SCADA 데드밴드/만성=설비 검토로 조치 분리. 자동 억제 없음)
- `docs/chat-after-concept-spec.md` — AFTER 채팅 컨셉
- `docs/chat-feedback-telemetry-spec.md` — 오답 피드백 루프 텔레메트리
- `docs/chat-photo-upload-scenario-spec.md` — 사진 업로드 4 시나리오 (P1~P3)
- `docs/dev-tag-ingest-spec.md` — 원격→로컬 tb_tag_raw_data 복제 (납품 시 제거)
- `docs/equipment-fault-tracking-spec.md` — 설비 장애 이력 추적 (migration 0045)
- `docs/report-spec.md` — 보고서 (장애 조치 / 일 점검) 사양 + 채팅 점검 인텐트 (migration 0058)
- `docs/feature-spec.md` — 기능 개괄
- `docs/gis_plan.md` — EPANET + GIS 관망 고도화 (Phase 1 구현 완료 / Migration 0064 — `slm/epanet/` 모듈 + `/admin/epanet` 페이지)
- `docs/gis-timeline-scrubber-spec.md` — GIS 관망도 타임라인 스크러버 (시안 14 — 24h 되감기/재생·알람 도트·useChartPlayback 재사용. Phase 1 구현 완료 — 실시간↔타임라인 토글·재생/배속·알람 도트, 신규 API 없음)
- `docs/gis-facility-menu-spec.md` — GIS 관망도 시설 메뉴 고도화 (P1 1순위 계획. 시설↔설비 CRUD: 설치일자/제조사/제원, 기존 tb_equipment_info.meta·tb_equipment_lifespan·tb_service_*_info 재사용. 1차 CRUD/2차 고장이력·교체주기 분리)
- `docs/epanet-menu-spec.md` — EPANET 활용 메뉴 사양 v1 (3 그룹 10 메뉴 트리 + 데이터 품질 게이트 + DataQualityCard UX + Phase 2.7~6 로드맵)
- `docs/epanet-flow-injection-spec.md` — EPANET 실측 유량 주입 사양 (B-1: 시설별 outflow 매핑 → INP demand IDW 보간, Migration 0071)
- `docs/epanet-flow-deviation-spec.md` — 시뮬 vs 실측 유량 차이 분석 사양 (B-2: 시설 단위 패널 + GIS 오버레이, Migration 0072 메뉴 등록)
- `docs/trend-comparison-spec.md` — 트렌드 비교 지표 v1 (평소 대비 / 향후 전망 두 토글 + KPI 배지, anomaly_detector z-score 알람과 통일, NMF/CUSUM 흡수)
- `docs/trend-baseline-gbt-spec.md` — 트렌드 정상 기대값 GBT baseline v1 (평소 대비의 기대값을 hourly_mean→HistGradientBoosting 으로 고도화, 주1회 학습 cron + hourly_mean 폴백, 성능평가 통합화면 `/admin/model-eval?model=baseline`. forecast/z-score 판정 불변)
- `docs/iforest-eval-spec.md` — IForest 이상탐지 모델 영속화·평가 v1 (인메모리 → 디스크 .pkl + 지표 DB. 비지도라 calibration_err·coverage% 로 평가(P1), 레이블 기반 정밀도 P2 보류. CLI `python -m anomaly_iforest train` + 주1회 cron + 성능평가 통합화면 `/admin/model-eval?model=iforest`)
- `docs/feature-sku-spec.md` — 기능 SKU·feature flag (v1, B1 EPANET Phase 1 구현 + B2~B5 예고)
- `docs/emergency-contact-spec.md` — 비상연락처 (관리 UI + AI 채팅 EMERGENCY_CONTACT_QUERY 인텐트, region 'water'→'R01' 통일)
- `docs/alarm-popup-spec.md` — 알람 발생 시 위기대응 모달 (경고 모달 / 주의 toast / 종합 카드 + SITE_SETTING.ALARM_POPUP_ENABLED 마스터 토글)
- `docs/alarm-diagnosis-control-mode-spec.md` — 알람 진단 펌프 제어모드 게이트 (Node-RED diagnosed_cause 오분류 수정: pump_control_mode 게이트 + 실가동 검증 + 경합 제거. 죽동 HH "펌프 미가동" 오분류 분석에서 도출. Phase A 사양/B 구현)
- `docs/performance/alarm-notifications-index.md` — 알람 폴링·인덱스 최적화 (features fetch 5분 + invalidate event + dynamic import + Migration 0081 부분 인덱스)
- `docs/performance/tag-raw-scan-optimization.md` — 태그 원시데이터 스캔 최적화 (Migration 0128 압축 segmentby=tagsn 135~300× + **전 청크 스캔 3건 제거** — 알람 해제 루프 14분30초→6.9ms·경보 이력 332초→0.16초·max(logtime) 7.6초→3.5ms. `tb_tag_raw_data` 조회는 logtime 하한 필수 [E-056])
- `docs/equipment-health-priority-spec.md` — 설비 교체 우선순위 Top 5 (개요 탭 카드. 3신호 융합: 내용연수·MTBF·재발 지속. `slm/endpoints/replacement_priority.py` + ReplacementPriorityCard)
- `docs/flow-diagram-mode-spec.md` + `docs/flow-diagram-mode-enhancement.md` — 실시간 계통도 모드 + 개선
- `docs/leak-alert-spec.md` — 누수 의심 알림 (야간최소유량 CUSUM 자동 감지·확인 + 선정 사유 서술. 판정=cusum_max, Migration 0100 reason 컬럼)
- `docs/field-mode-spec.md` — 현장 모드 v3 (/field — ①알람 대응(확장 액션: 사진 진단/고장 기록/매뉴얼/조치) ②시설 선택 작업(일상점검·알람 없는 설비) ③진행중 장애 조치 완료 ④자유 입력. 처리는 채팅 재사용, field-handoff 사진 전달, FieldModeBanner, Migration 0099 M009)
- `docs/tag-quality-layer-spec.md` — 태그 품질 계층 v1 (포화·고착·무신호·DI 반전·값 이탈 5종 검사 통합 → tb_tag_quality + 1h 내장 루프. P1 일원화/P2 기준선 보호·판정 게이트/P3 알람 완화·점검 후보 — **전 단계 구현 완료 2026-07-24**)
- `docs/alarm-threshold-coverage.md` — SCADA 알람 임계 보유 현황 조사 (2026-07-24. 압력 33곳 공백 — 가압장 전원. 수위 완비·유량은 통계 감시 소관 판정. 재조사 SQL + SCADA 보강 우선순위 + 셋업 검수 활용)
- `docs/flow-diagram-engineering-spec.md` — 계통도 엔지니어링화 (구축 고도화 ②. 자동 레이아웃 API(relayout new_only/full) + 정합 lint(누락·고아·순환·EPANET) + setup/diagram 노드 배치 탭(드래그 저장) + flow-map CRUD→causal 리로드. v1 구현 완료 2026-07-24)
- `docs/canvas-editor-unification-spec.md` — 캔버스 에디터 일원화 (좌표 정본 tb_flow_diagram_node 통일 — tb_canvas_node_position 폐기(0120 rename)·M200-11 메뉴 제거(0121). px↔경위도 백엔드 변환(레벨240px·형제56px), PUT full-diff→명시 diff+causal 리로드, relayout·lint 툴바 이식, 노드 배치 탭 제거·/setup/canvas redirect. **P1+P2 완료 2026-07-26**)
- `docs/setup-workspace-spec.md` — 구축 워크스페이스 (시설·설비·통신·계통도 통합 진입점. 캔버스 병합이 아니라 트리+인스펙터+등록 흐름 통합 — 레이어 토글(용수 계통/통신망), 시설 등록 마법사 4단계, 좌표 정본 이원화 해소 선결. **P1·P2-a·P2-b 완료** — 구축 메뉴 19→11(0125), tb_canvas_layout 분리(0126), /setup/workspace 트리+레이어 토글(0127), 시설 등록 마법사 4단계, 공용 인스펙터 5탭(통신 포함). **P1~P3 전 단계 완료 2026-07-27**)
- `docs/network-link-editor-spec.md` — 네트워크 링크 에디터 (setup/networks 3탭 — 링크 에디터 기본. React Flow 드래그 연결→기존 링크 폼 프리필 POST, 배치=tb_network_info.meta.canvas_pos 병합(PUT /network/canvas-positions), lint 배지 미연결·IP중복. 계통도 캔버스와 별개 에디터. **v1 완료 2026-07-26**, 0124 프로토콜 마스터 신설 [E-052])
- `docs/trend-catalog-unification-spec.md` — 트렌드 카탈로그 일원화 (정본 tb_trend_catalog 하나 — tb_monitoring_catalog 이관·legacy(0122). 모니터링 화면=meta.show_monitoring 큐레이션, 채팅·이상감지=전체. UI 생성 트렌드 즉시 채팅 조회 가능. 시드 행 UI 편집·삭제 차단. **v1 완료 2026-07-26**, 프런트 무변경)
- `docs/setup-audit-spec.md` — 구축 완결성 검수 (구축 고도화 ③. 검사 6종(기초정보/임계/datainfo/계통도/품질/EPANET) 집계 `/setup/audit` M200-21 — 납품 검수 단일 진입점, warn 0=인수 기준. 첫 실행에서 가압장 기초정보 공백 11곳 검출. v1 완료 2026-07-24)
- `docs/datainfo-conversion-rule-spec.md` — DATAINFO 변환룰 (구축 고도화 ①. datadesc→datainfo 룰 4계층(regex/dict/context/override) + 미리보기·선별 적용·이력 롤백 + 재현율 채점. Migration 0117·0118. 메뉴는 2026-07-26 "태그 설정"(/setup/tags) 탭으로 통합 — /setup/datainfo-rules 는 redirect, M200-20 제거(0123). P1 완료 — 49룰 87.6% + 태그 단위 정책 exclude(변환 제외)/override(확정) 행별 버튼)
- `docs/chat-history-server-spec.md` — 채팅 대화 목록 서버 계정 전환 (localStorage→tb_ai_chat_group/tb_ai_chat_message jsonb, Migration 0119. 서버-우선+로컬 폴백, 일회성 이관 멱등 import. ai 메모리 세션·/ask/stream 불변. **v1+P2 완료 2026-07-25** — 본문 검색 q·지연 purge 30일·소유자 검증 404+경합 흡수)
- `docs/inspection-cycle-spec.md` — 설비 점검 주기 도래 v1 (tb_inspection_cycle 유형 단위 마스터 + 조회 시점 계산(cron 없음) `GET /inspection/due`. 표시=인수인계 화면 섹션, never 261대는 한 줄 요약(목록 잠식 방지), "일정 등록"은 명시 행동 — 자동 생성 없음. Migration 0130. 주기 편집 UI 는 P2)
- `docs/shift-handover-spec.md` — 교대 인수인계 브리핑 v1 (`/reports/shift-handover` M005-5. 신규 테이블 0 — 경보·작업·메모·일정 집계. "넘겨받는 것"(구간 끝 시점 진행중·미확인)을 최상단. 교대 경계는 SITE_SETTING.SHIFT_BOUNDARIES 로 config 분리(빈 값=24h 단일). 경보 목록은 (현장,메시지) 접기 필수 — 안 접으면 채터링이 목록 잠식. Migration 0129)
- `docs/memo-schedule-spec.md` — 업무 메모 + 일정 알림 v1 (Migration 0105. 메모: 검색+달력 보기 토글, 삭제=본인+마스터. 일정: 달력 등록→30s 폴링 전역 팝업, 확인 전 재표시. SCADA 알람과 용어·시스템 분리)
- `docs/realtime-comm-spec.md` — 실시간 통신 확정판 v2 (전 단계 완료: P1 메신저+그룹 채팅(0106·0113) / P2 파일 전송(0107) / P3 음성·P4 영상 통화(0108~0111) / 3~4인 회의 통화 풀메시(0112) / 외부망 TURN 옵션(coturn·이중 토글). REST 폴링+non-trickle ICE 설계. LTE 실통화 검증)
- `docs/inspector-pattern-spec.md` — 우측 인스펙터 패턴
- `docs/metric-trend-panel-spec.md` — Q/H/P 트렌드 공용 패널
- `docs/startup-spec.md` — 시작 페이지
- `docs/tweaks-layout-spec.md` — 테마/브랜드/레이아웃 Tweaks 패널

## 마케팅·영업 자산
- `docs/catalog-draft.md` — 제품 카탈로그 초안 원고 (A4 16p 구성 — 개요·차별화 4·기능군 4축·SKU·기술 사양·구축 프로세스. 캡처 15점 목록 + 확정 필요 항목 부록)
- `docs/brochure-draft.md` — 제품 브로셔 초안 원고 (A4 6p / 3단 접지 — 카탈로그 요약판. 관심 유발용, 캡처 7점)
- `docs/catalog-draft.html` — 카탈로그 HTML 판 (자립형 16p, A4 인쇄 CSS. `[화면]` 자리를 임의 현장명(청수시·한빛배수지 등) HTML/CSS 목업으로 직접 구성 — 실 현장명 비노출)
- `docs/brochure-draft.html` — 브로셔 HTML 판 (자립형 6p, 동일 목업 체계. 브라우저 인쇄 → PDF 제작 가능)

## 가이드
- `docs/slm-dev-environment-guide.md` — 로컬 TimescaleDB 환경 구성
- `docs/chat-smoke-test-guide.md` — 채팅 스모크 테스트 (대표 인텐트 16개 자동 E2E. Backend `test_chat_smoke.py`+`chat_smoke_cases.json`. ai_server/인텐트 변경·리팩토링 후 필수 실행, 납품 검수 겸용)
- `docs/operations/report-quickstart.md` — 보고서 작성 빠른 가이드 (운영자용 1장)
- `docs/operations/cron-setup.md` — 근본원인 자동 분류 cron 등록 가이드 (Linux/macOS/Docker)
- `docs/operations/epanet-sim-cron.md` — EPANET 자동 시뮬 cron (시계열 누적, launchd plist + crontab + 트러블슈팅)
- `docs/operations/baseline-train-cron.md` — 트렌드 GBT baseline 주1회 재훈련 cron (CLI `python -m trend_baseline train` + launchd/crontab + 롤백)
- `docs/operations/iforest-train-cron.md` — IForest 이상탐지 모델 주1회 재학습 cron (CLI `python -m anomaly_iforest train` + launchd/crontab + 지표 모니터링)
- `docs/operations/model-weights-bundle.md` — 모델 웨이트 오프라인 번들 (납품 필수 — Chronos 783MB + Whisper 1.5GB. `slm/tools/model_weights_bundle.sh` pack/install/verify, sha256 매니페스트)
- `docs/operations/offline-map-bundle.md` — GIS 오프라인 지도 번들 (납품 필수 — 당진 pmtiles 19MB + 글리프/스프라이트, cartocdn 의존 제거. 기본 오프라인, NEXT_PUBLIC_MAP_CDN=1 로 CDN opt-in)
- `docs/operations/delivery-checklist.md` — 납품 체크리스트 (오프라인 자산 반입·dev 요소 제거·배포 구성·검수·인수인계의 단일 진입점)
- `docs/claude-code-setup-guide.md` — 스킬/에이전트/셋업
- `docs/migration-to-mac.md` — Windows → Mac 이전 기록 (구조 다이어그램은 이관 시점 스냅샷 — **폴더 구조 정본은 본 CLAUDE.md 의 "📂 디렉토리 구조" 섹션**)
- `docs/deploy-secrets.md` — 시크릿 관리
- `docs/deploy-production.md` — 프로덕션(납품) 배포 (next build/start + Caddy TLS, HMR 리로드 E-034 근본 제거. `Dockerfile.prod`·`docker-compose.prod.yml`·`certs/Caddyfile`)
- `docs/START.md` — 빠른 시작 (GIS Demo)

## 에러관리
- `docs/error-management.md` — E-NNN 번호 체계 (현재 E-056)
  - 1차 시도 실패 시: 전체 스캔 → 유사 패턴 확인 → 해결책 참고
  - 새 버그 해결 후: E-NNN 번호 부여 기록 (날짜/증상/원인/해결/재발방지)
  - 기록 대상: 포트 충돌, DB 연결 오류, SQL 오류, 서버 기동 실패, 환경변수 문제 등 **재발 가능성 있는 모든 에러**

## 작업 이력 / 로드맵
- `docs/work-history.md` — 완료/진행중/남은 항목 + 태그 분류 체계
- `docs/slm-project-roadmap-v2.md` — DB 개선 + 개발 순서
- `docs/slm-feature-roadmap-draft.md` — **기능 로드맵 검토안 (미래 계획, 착수 대상 아님)**. A 제한적 에이전트 루프 / B 현장별 지식 카드 UI / C 오탐 피드백 루프 / D 상황보고서 1·2보 초안 / E 현장 점검 모바일. **방향성 참조용 — 이 문서를 근거로 선제 구현하지 말 것**. 부록 A 에 기존 구현 대조 (What-if 시뮬은 이미 B1 EPANET 로 출시됨 · OCR 파이프라인은 부재로 신규 스택 · 오탐 라벨은 이미 수집 중)
- `docs/review-items.md` — 검토 필요 항목

## 대화 이력 (세션별)
- `docs/dialog_log/YYYY-MM-DD.md` — 일자별 세션 요약 (사용자 요청·변경 요지)
  - **커밋마다 업데이트** (`memory/feedback_commit_dialog_log.md`)
  - **3일 초과 파일은 롤링 삭제** (D-4 이전 파일 제거)

## 예제/자산
- `docs/examples/fault_case_template.xlsx` — 고장 케이스 5건 샘플 (PLC/인버터/RTU/모뎀/UPS)

## DB 스키마 원본
- `dump-postgres-202602101740.sql` — DB 스키마 원본
- `example3.json` (Backend 레포) — 채팅 인텐트 64개 정의 (501 질문, 응답 템플릿)

## 탐색·분석 문서 (히스토리성)
- `docs/design-analysis.md`, `docs/test-image-samples.md`, `docs/query-test-results.md`
- `docs/facility-selector.md`, `docs/iforest.md`
- `docs/reservoir_hunting_detection_logic.md`, `docs/c-plan-expansion.md`

---

# 📜 문서 관리 규칙

## 저장 위치 구분
| 위치 | 용도 | 커밋 |
|------|------|------|
| `docs/` | 사양·정책·가이드·이력 — 공개 문서 | ✅ |
| `memory/` (`.claude/projects/.../memory/*.md`) | 에이전트 세션 규범·개인 선호 | ❌ (로컬) |

## 신규 문서 생성 시
1. **네이밍 규칙**
   - 정책: `docs/*-policy.md`
   - 사양: `docs/*-spec.md`
   - 가이드: 주제 기반 (`*-guide.md` 등)
2. **본 CLAUDE.md 인벤토리에 등록** (위 섹션 해당 카테고리)
3. 관련 세션 규범은 메모리(`feedback_*.md`)에도 저장 — 향후 자동 준수

## 경로 표기
Unix `/` 통일. `docs/foo.md` (과거 `docs\foo.md` 표기 폐지).

## 작업 이력 업데이트
- 주요 작업 완료 시 `docs/work-history.md` 섹션 추가
- 커밋마다 `docs/dialog_log/YYYY-MM-DD.md` 에 사용자 요청·변경 요지 기록
- dialog_log 3일 초과 파일은 커밋 시 함께 삭제 (롤링)

## 커밋·푸시 규칙
(`memory/feedback_commit_push.md`)
- 작업 완료 후 **항상 git commit + push**
- 커밋 메시지: 한국어, 기능/수정 카테고리 접두어 (`feat/fix/refactor/docs/chore`)
- submodule 변경 시 외부 web 레포에서 `git add slm-dashboard` 후 commit
- `git push --force` 금지

---

# 🧠 메모리 활용 원칙

본 프로젝트의 에이전트 규범·사용자 선호는 `memory/` 에 영속 저장되어 새 세션마다
자동 로드됨. 중요한 것들:

- `feedback_spec_conflict_check.md` — 새 사양 요청 시 기존 사양 위배 여부 사전 검토
- `feedback_commit_push.md` — 작업 완료 후 항상 커밋·푸시
- `feedback_commit_dialog_log.md` — 커밋마다 dialog_log 업데이트, 3일 롤링
- `feedback_update_spec.md` — 작업 완료 후 관련 사양 동기화
- `feedback_no_auto_alarm_link.md` — 장애 기록 시 알람 자동 해제 금지
- `feedback_fault_category_policy.md` — 분류 정책 (알람="이상"/현장 확인만 "고장")
- `feedback_no_photo_per_alarm.md` — 경보 목록 행별 사진확인 버튼 금지
- `feedback_wrong_answer_loop.md` — 오답 피드백 루프는 핵심 설계
- `feedback_no_password_change.md` — jykim/midi1212 비밀번호 건드리지 말 것
- `project_closed_network.md` — 폐쇄망 배포 가정
- `project_overview.md`, `roadmap_status.md` — 프로젝트 요약

전체 인덱스는 `.claude/projects/-Users-jykim-web/memory/MEMORY.md` 참조.
