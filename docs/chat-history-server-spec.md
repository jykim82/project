# 채팅 대화 목록 서버 계정 전환 사양 v1

> 작성 2026-07-25 · Migration 0119 · Backend `endpoints/chat_history.py` ·
> Frontend `chat-store.ts` 서버-우선 전환
> **v1 + P2(§8) 전체 구현 완료 2026-07-25**

## 1. 배경·목표

AI 채팅의 **대화 목록(그룹)과 메시지 히스토리가 브라우저 localStorage** 에만
저장되어 있어:

- 기기를 바꾸면(사무실 PC ↔ 태블릿 ↔ 현장 폰) 이력이 따라오지 않음
- 브라우저 캐시 삭제 시 전체 대화 유실
- localStorage 5MB 쿼터 — visual_data(jsonb) 포함 메시지가 커서 곧 한계
- B2B 제품 관점에서 "계정 데이터가 단말에 종속" 은 인수 기준 미달

**목표**: 대화 목록·메시지를 **서버(DB) 계정 기반**으로 전환. 어느 기기에서
로그인해도 동일한 대화 이력. 기존 localStorage 데이터는 **일회성 자동 이관**.

### 비목표 (변경하지 않는 것)

- ai_server 의 **메모리 세션**(`session_manager.py` — 인텐트 문맥·턴 누적)은
  그대로 메모리 유지. 대화 문맥은 휘발성이 설계 의도(TTL)이며 본 사양의
  "히스토리 영속화" 와 별개 축.
- 메시지 렌더링 경로 불변 — `ChatMessage.bot.visual` → VisualRenderer 재렌더.
- `/ask/stream` SSE 계약 불변 (인텐트·스모크 테스트 영향 없음).

## 2. 현재 구조 (전환 전)

| 항목 | 위치 | 방식 |
|------|------|------|
| 그룹 목록 | localStorage `slm-chat-groups` | ChatGroup[] (최대 50) |
| 메시지 | localStorage `slm-chat-messages-{groupId}` | ChatMessage[] (user/bot 페이로드 전체) |
| ai 세션 매핑 | 메모리 `sessionMap` | group_id → ai session_id (리로드 시 소멸 — 유지) |
| store | `src/stores/chat-store.ts` | zustand, 액션 내부에서 localStorage 직접 IO |

메시지 변이 지점은 3곳뿐 — `addMessage`(스트림 완료 후 1회),
`markFeedbackSubmitted`(피드백 플래그), `setMessages`(그룹 전환 초기화).
→ **액션 시그니처를 유지한 채 내부 IO 만 서버로 교체 가능.**

## 3. DB 설계 (Migration 0119)

메신저(`tb_chat_group` — 운영자 간 채팅, realtime-comm-spec)와 **테이블 분리**.
AI 채팅은 `tb_ai_chat_*` 접두 통일 (기존 `tb_ai_chat_log`/`tb_ai_chat_feedback`
과 동일 계열).

```sql
CREATE TABLE tb_ai_chat_group (
    region      varchar(20)  NOT NULL,
    group_id    varchar(40)  NOT NULL,   -- 프런트 생성 'g_{ts}_{rand}' 그대로 수용 (이관 호환)
    user_id     varchar(50)  NOT NULL,
    group_title varchar(200) NOT NULL DEFAULT '새 대화',
    sort_order  integer      NOT NULL DEFAULT 0,  -- 드래그 정렬 (작을수록 위)
    last_at     timestamptz  NOT NULL DEFAULT now(),
    del_yn      char(1)      NOT NULL DEFAULT 'N',
    created_at  timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (region, group_id)
);
CREATE INDEX idx_ai_chat_group_user ON tb_ai_chat_group (region, user_id, del_yn, sort_order, last_at DESC);

CREATE TABLE tb_ai_chat_message (
    region       varchar(20) NOT NULL,
    group_id     varchar(40) NOT NULL,
    ask_seq      bigint      NOT NULL,   -- 프런트 생성 (Date.now() ms — 그룹 내 유일)
    user_payload jsonb       NOT NULL,   -- ChatMessage.user { message, images, ask_at }
    bot_payload  jsonb       NOT NULL,   -- ChatMessage.bot { answer, visual, fault_draft, ... }
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (region, group_id, ask_seq)
);
```

**jsonb 통짜 저장 이유**: bot 페이로드는 카드 유형(fault_draft, vision_advice,
report_draft, photo_clarify …)이 계속 늘어나는 개방 구조. 컬럼 정규화 시
카드 추가마다 마이그레이션이 필요해 진화를 막는다. 조회는 항상 그룹 단위
전체 로드(채팅 UI 특성)라 jsonb 인덱스도 불요. 렌더링 경로가 "localStorage
JSON → 컴포넌트" 에서 "DB jsonb → 컴포넌트" 로 바뀔 뿐 구조 동일 — 기존
visual 재렌더링 원칙(tb_ai_chat_bot.visual_data 계보) 계승.

**FK 미설정**: 그룹 소프트 삭제(del_yn) 후에도 메시지 보존(감사·복구 여지).
물리 삭제는 운영 정책으로 후결(§8).

**롤백**: `DROP TABLE tb_ai_chat_message; DROP TABLE tb_ai_chat_group;` —
프런트는 서버 실패 시 localStorage 폴백이라 롤백해도 동작 유지(단 이관 후
쌓인 서버 전용 이력은 유실되므로 필요시 사전 덤프).

## 4. API 계약 (`endpoints/chat_history.py`, prefix `/chat/history`)

인증: Next.js 프록시가 JWT 필수 경로로 강제(기존과 동일). region/user_id 는
쿼리·바디로 명시 전달 — 기존 endpoints 패턴(user_chat.py 등)과 통일.

| 메서드·경로 | 요청 | 응답 | 비고 |
|---|---|---|---|
| GET `/chat/history/groups?region&user_id` | — | `{groups: ChatGroup[]}` | del_yn='N', sort_order ASC → last_at DESC |
| POST `/chat/history/groups` | `{region, user_id, group_id?, group_title?}` | `{group_id}` | 프런트 생성 id 수용, 없으면 서버 생성. upsert(중복 안전) |
| PATCH `/chat/history/groups/{group_id}` | `{region, group_title}` | `{status}` | 제목 변경 + last_at 갱신 |
| DELETE `/chat/history/groups/{group_id}?region` | — | `{status}` | 소프트 삭제(del_yn='Y') |
| PUT `/chat/history/groups/reorder` | `{region, user_id, ordered_ids[]}` | `{status}` | sort_order 재부여 |
| GET `/chat/history/groups/{group_id}/messages?region` | — | `{messages: ChatMessage[]}` | ask_seq ASC |
| PUT `/chat/history/groups/{group_id}/messages/{ask_seq}` | `{region, user, bot}` | `{status}` | upsert — 신규 저장·피드백 플래그 갱신 공용 |
| POST `/chat/history/import` | `{region, user_id, groups:[{...group, messages:[...]}]}` | `{imported_groups, imported_messages}` | 일회성 이관. 기존 (group_id) 존재 시 스킵 — 멱등 |

## 5. 프런트 전환

### 5.1 신설 `src/lib/api/chat-history-api.ts`
위 8개 엔드포인트 래퍼. `apiClient` 경유(`/api/proxy/chat/history/*`).
기존 `chat-api.ts` 의 미사용 그룹 함수(fetchChatGroups 등 — 백엔드 부재로
사장된 초기 설계 잔재)는 제거하고 본 모듈로 일원화.

### 5.2 `chat-store.ts` — 서버-우선 + 로컬 폴백

| 액션 | 전환 후 동작 |
|---|---|
| `refreshGroups` | 서버 GET → 성공 시 상태 반영 + 로컬 미러 저장. **실패 시 localStorage 폴백** + 1회 경고 toast. 서버 성공 후 이관 훅(§5.3) 실행 |
| `loadMessages` | 서버 GET → 실패 시 로컬 폴백 |
| `addMessage` | 상태 즉시 반영(낙관) + 로컬 미러 + 서버 PUT upsert (fire-and-forget, 실패 warn) + 그룹 last_at 갱신 |
| `markFeedbackSubmitted` | 상태 반영 + 로컬 미러 + 해당 메시지 서버 PUT |
| `createGroup` | 로컬 id 생성(`g_…` — 오프라인에서도 동작) → 상태 반영 → 서버 POST |
| `deleteGroup` | 상태·로컬 제거 → 서버 DELETE |
| `updateGroupTitle` | 상태·로컬 반영 → 서버 PATCH |
| `reorderGroups` | 상태·로컬 반영 → 서버 PUT reorder |

원칙: **UI 는 서버 응답을 기다리지 않는다**(낙관 갱신 — 채팅 UX 지연 금지).
서버 쓰기 실패는 콘솔 warn — 다음 refreshGroups 때 서버 상태로 수렴.
로컬 미러는 폐쇄망 내 백엔드 재기동 순간 등의 읽기 폴백용으로 유지한다
(쿼터 초과 시 기존 trim 로직 그대로 — 미러는 캐시일 뿐 정본은 서버).

### 5.3 localStorage 일회성 이관

`src/lib/chat-local-migration.ts`:

1. 플래그 `slm-chat-migrated` 존재 → 스킵
2. `slm-chat-groups` 파싱 → 각 그룹의 `slm-chat-messages-{id}` 수집
3. `POST /chat/history/import` (서버측 멱등 — 재실행 안전)
4. 성공 시 플래그 기록. **로컬 원본은 삭제하지 않음** (안전 — 미러 겸용)
5. 실패 시 플래그 미기록 → 다음 진입 때 재시도

호출 시점: `refreshGroups` 서버 GET **성공 직후** (서버 살아있음이 확인된
시점). 이관이 실제 수행됐으면 그룹 재조회로 목록 갱신.

## 6. 멀티테넌시·보안

- 모든 쿼리 region 스코프 (PK 선두 컬럼) — 기존 원칙 유지
- 그룹 목록·이관은 user_id 스코프 — 타 계정 대화 열람 불가
- 메시지 조회는 (region, group_id) — group_id 가 랜덤 40자로 추측 곤란하나,
  P2 에서 그룹 소유자 검증(user_id JOIN) 추가 여지(§8)

## 7. 검증

- `npx tsc --noEmit` 0건
- `test_chat_smoke.py` 16/16 (ai_server 라우터 등록만 — /ask 경로 불변 확인)
- Playwright: ① 새 대화 생성·질의 → 새로고침 → 목록·메시지 서버 로드 확인
  ② localStorage 기존 데이터 시나리오 — 이관 후 서버 조회로 동일 목록
  ③ 시크릿 창(다른 기기 시뮬레이션) 동일 계정 → 동일 목록 확인
- curl: import 멱등(2회 호출 → 두 번째 0건)

## 8. P2 — 검색·purge·소유자 검증 (구현 완료 2026-07-25)

### 8.1 대화 검색 (제목 + 본문)
- GET `/groups?q=` — 제목 ILIKE **OR** 메시지 EXISTS
  (`user_payload->>'message'`, `bot_payload->'answer'->>'summary'`)
- 프런트 검색창: 로컬 제목 필터 즉시 + **서버 본문 검색 300ms 디바운스**
  (2자 이상). 제목 매칭 우선, 본문 전용 매칭은 뒤에 병합. 서버 불가 시
  로컬 필터만 동작. placeholder "대화 검색 (제목·내용)..."

### 8.2 소프트 삭제 보존 기간 purge
- `CHAT_HISTORY_PURGE_DAYS` (기본 30) — del_yn='Y' + last_at 초과 그룹을
  **목록 조회 시 지연 purge** 로 물리 삭제(메시지 포함). 별도 cron 없음 —
  폐쇄망 구성요소 최소화. 삭제 후 30일 내에는 DB 복구 여지 유지

### 8.3 소유자 검증
- 메시지 GET/PUT·그룹 PATCH/DELETE 에 user_id 필수 — 그룹 소유자 불일치 시
  404 (타 계정 group_id 추측 접근 차단)
- 메시지 PUT 은 그룹 부재 시 **작성자 소유로 자동 생성** — 프런트의 그룹
  POST 와 첫 메시지 PUT 병렬 발사 경합 흡수. 타인 소유 그룹이면 ON CONFLICT
  로 생성이 무시돼 재검증에서 404

### 잔여 보류
- 서버 페이지네이션 (그룹당 전체 로드 — 채팅 UI 특성상 당분간 불요)
