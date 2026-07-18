# 채팅 스모크 테스트 가이드

대표 인텐트 16개를 실제 프런트 경로(`/ask/stream` SSE)로 자동 검증하는 회귀
안전망. **ai_server 리팩토링(Phase 5 등)의 선행 조건**이자 폐쇄망 납품 시
**설치 검수 도구** (전체 ~1분).

## 위치 (Backend 레포 `../slm/`)

| 파일 | 역할 |
|------|------|
| `test_chat_smoke.py` | 러너 — SSE 실행·판정·exit code |
| `chat_smoke_cases.json` | 케이스 정의 (질의 → 기대 intent/graph_type/구조) |

기존 `test_all_intents.py` 와 목적이 다름:
- `test_all_intents.py` — 전수(68개) **데이터 존재 진단** (`/ask`, assert 없음, 느림)
- `test_chat_smoke.py` — 대표(16개) **구조 assert** (`/ask/stream`, exit 0/1, ~1분)

## 실행

```bash
cd ~/slm
python test_chat_smoke.py                  # 전체 (exit 0=통과, 1=실패)
python test_chat_smoke.py --case 스캔       # 질의 부분일치 필터
python test_chat_smoke.py --api http://192.168.x.x:8000 --region R01   # 원격/납품 검수
```

## 판정 원칙 (라이브 DB 대응)

살아있는 DB 라 **값은 변동** → 값 검증은 flaky. 판정은 구조만:

| 항목 | 엄격도 | 이유 |
|------|--------|------|
| `status == OK` | 필수 | |
| `intent` 일치 | 필수 | 오라우팅·분류 회귀의 핵심 신호 |
| `graph_type` 일치 | 케이스별 | 렌더 경로 결정자 |
| `min_rows` | '비면 확실히 고장'인 케이스만 | 알람 이력 등은 정상적으로 빌 수 있음 |
| `require_fields` | 설계상 항상 생성되는 분석 구조만 | `cross_facility_mismatches` 같이 정상적으로 빌 수 있는 리스트는 제외 |

**캐시 웜업 재시도**: 재기동 직후 이상탐지 스캔 캐시 공백으로 ERROR 가 날 수
있어 러너가 30s 후 1회 재시도 (리팩토링 회귀와 구분. 2026-07-14 Phase 4 검증에서 실제 겪은 함정).

## 케이스 추가·갱신 규칙

1. **회귀를 고치면 그 질의를 케이스로 추가** — `regression` 필드에 날짜·증상 기록
   (현재: 07-12 오라우팅, 07-11 분류 지연·신설 인텐트 누락·blocking 이력 포함)
2. **사양 변경으로 인텐트/구조가 바뀌면 같은 커밋에서 JSON 도 갱신** —
   테스트가 코드와 함께 버전 관리되는 것이 이 체계를 UI 기반이 아닌 코드
   레벨로 만든 이유
3. 기대값 초안은 검증된 현재 서버에 질의를 실행해 캡처 (값이 아닌 구조만 옮김)

## 실행 시점

- ai_server / intent_classifier / response_builder / example3.json 변경 후 (필수)
- 리팩토링(모듈 분리·최적화) 후 (필수 — Phase 5 게이트)
- 납품 설치 후 검수 (`--api` 로 대상 지정)
- (선택) 주간 cron — `docs/operations/` cron 가이드 패턴 참조

## Tier 2 — Playwright 프런트 스모크 (완료 2026-07-15)

백엔드 스모크가 못 잡는 층을 커버: **프런트 사전 분류(use-chat-submit.ts)
오라우팅 + 카드 렌더·평문 억제(card_type 경로)**.

| 파일 (Frontend 레포) | 역할 |
|---|---|
| `playwright.config.ts` | baseURL https://localhost:3000, 순차 실행, setup 의존 |
| `e2e/auth.setup.ts` | jykim 로그인 → storageState 재사용 |
| `e2e/chat-smoke.spec.ts` | 4케이스 (아래) |

```bash
cd slm-dashboard/slm-dashboard
npm run test:e2e        # prod 스택(:3000) + 백엔드(:8000) 기동 전제, ~10s
```

케이스 (회귀 이력 기반):
1. "센서이상 스캔" → 이상 카드 렌더 + **"장애 기록 확인" 부재** (07-12 오라우팅 회귀)
2. "판넬 전원이상 발생했어" → 장애 기록 확인 카드 (사전 분류 정상 경로. 취소로 정리 — DB 부작용 없음)
3. "경보 발생 이력" → 알람 카드 + 평문 억제
4. "최근 이상 이력" → AnomalyHistoryView 카드 + 평문 억제

주의: 백엔드 재기동 직후엔 스캔 캐시 웜업(~2분) 후 실행. 실행 시점: 채팅
UI·use-chat-submit·카드 컴포넌트·mapper 변경 후 (필수), 백엔드 스모크와 세트로.

**hydration 레이스 (2026-07-18)**: prod 빌드에서 goto 직후 즉시 fill 하면
React hydration 이 컨트롤드 입력을 초기화해 전송이 유실됨 (번들이 커지며
상시 실패로 표면화). `askChat` 헬퍼가 **전송 버튼 활성화**(=React state 반영
증거)까지 fill 을 재시도(`expect().toPass`)하므로 새 케이스도 반드시 이
헬퍼를 경유할 것 — 직접 fill+Enter 금지.

## Tier 3 — 채팅 카드 갤러리 (완료 2026-07-15)

`/admin/chat-gallery` (관리 > 채팅 카드 갤러리, ADMIN/MASTER) — 대표 질의
12종을 실서버에 실행해 **실제 BotMessage 컴포넌트**로 렌더. 디자인·카드 정책
변경 시 전 카드 한 화면 육안 검수 (자동 판정 아님 — 스모크가 담당).
항목별 card_type·응답시간 라벨. 메뉴: migration 0097.

## 커버리지 계획 (후속)

- 장기: 오답 피드백 루프에서 회귀 케이스로 승격하는 훅
