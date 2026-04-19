# SLM Dashboard - Next.js Frontend

## 참고 사이트 (레퍼런스)
- URL: http://112.166.183.65:15000/
- 로그인: kwater / 1234
- 이 사이트의 UI/UX를 참고하여 Next.js로 재구현

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

## 사양 (`docs/*-spec.md` 및 관련)
- `docs/slm-api-contract-final.md` — Python ↔ Next.js API 규약 (56개 인텐트)
- `docs/slm-setup-phase-spec.md` — 기초정보 구축 + 컬럼 잠금
- `docs/alarm-category-summary-spec.md` — 분류별 경보 현황
- `docs/chat-after-concept-spec.md` — AFTER 채팅 컨셉
- `docs/chat-feedback-telemetry-spec.md` — 오답 피드백 루프 텔레메트리
- `docs/chat-photo-upload-scenario-spec.md` — 사진 업로드 4 시나리오 (P1~P3)
- `docs/dev-tag-ingest-spec.md` — 원격→로컬 tb_tag_raw_data 복제 (납품 시 제거)
- `docs/equipment-fault-tracking-spec.md` — 설비 장애 이력 추적 (migration 0045)
- `docs/feature-spec.md` — 기능 개괄
- `docs/flow-diagram-mode-spec.md` + `docs/flow-diagram-mode-enhancement.md` — 실시간 계통도 모드 + 개선
- `docs/inspector-pattern-spec.md` — 우측 인스펙터 패턴
- `docs/metric-trend-panel-spec.md` — Q/H/P 트렌드 공용 패널
- `docs/startup-spec.md` — 시작 페이지
- `docs/tweaks-layout-spec.md` — 테마/브랜드/레이아웃 Tweaks 패널

## 가이드
- `docs/slm-dev-environment-guide.md` — 로컬 TimescaleDB 환경 구성
- `docs/claude-code-setup-guide.md` — 스킬/에이전트/셋업
- `docs/migration-to-mac.md` — Windows → Mac 이전 기록
- `docs/deploy-secrets.md` — 시크릿 관리
- `docs/START.md` — 빠른 시작

## 에러관리
- `docs/error-management.md` — E-NNN 번호 체계 (현재 E-029)
  - 1차 시도 실패 시: 전체 스캔 → 유사 패턴 확인 → 해결책 참고
  - 새 버그 해결 후: E-NNN 번호 부여 기록 (날짜/증상/원인/해결/재발방지)
  - 기록 대상: 포트 충돌, DB 연결 오류, SQL 오류, 서버 기동 실패, 환경변수 문제 등 **재발 가능성 있는 모든 에러**

## 작업 이력 / 로드맵
- `docs/work-history.md` — 완료/진행중/남은 항목 + 태그 분류 체계
- `docs/slm-project-roadmap-v2.md` — DB 개선 + 개발 순서
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
- `docs/facility-selector.md`, `docs/gis_plan.md`, `docs/iforest.md`
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
