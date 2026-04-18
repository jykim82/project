# AI 채팅 피드백/로그 텔레메트리

**작성:** 2026-04-18
**상태:** A(👍/👎 양방향) + B(전체 로그) 구현 완료.

## 1. 전제

- **폐쇄망 배포** — 외부 노출 없음, 개인정보 제약이 기본 블로커 아님
- **오답 피드백 루프는 핵심 설계** — 변경 금지. "원하는 답이 아닌가요?" 버튼 → `tb_ai_chat_feedback` → `/admin/chat-feedback` 수동 검토 → intent/템플릿 개선 반영
- 이 문서의 A/B는 위 루프를 **확장**하는 것이지 교체·변경이 아님

## 2. A) 👍/👎 양방향 피드백

### 2.1 변경 요약
- `tb_ai_chat_feedback.feedback_type` 허용값에 `"positive"` 추가 (DDL 변경 없음)
- BotMessage 타임스탬프 행에 `👍 도움됐어요` 버튼 추가. 기존 `👎 원하는 답이 아닌가요?` 병존
- `feedback_kind: "positive" | "negative"`를 ChatMessage에 저장하여 제출 후 UI 분기
- `/admin/chat-feedback`: 상단 요약 카드 4칸 (총 / 긍정 / 오답 / 성공률) + 각 항목에 positive/negative 뱃지

### 2.2 성공률 계산
```
success_rate = positive_count / (positive_count + wrong_answer_count)
```

positive 피드백은 인텐트별 오답 요약(상위 8개 뱃지)에서는 제외 (오답 개선 목적).

### 2.3 관련 파일
- `slm/endpoints/chat_feedback.py` — `_ALLOWED_TYPES`에 `"positive"` 추가
- `slm-dashboard/src/lib/types/chat.ts` — `FeedbackType` / `feedback_kind`
- `slm-dashboard/src/components/chat/BotMessage.tsx` — ThumbsUp 버튼, 분기 렌더
- `slm-dashboard/src/components/chat/ChatMessageArea.tsx` — prop drill
- `slm-dashboard/src/app/(dashboard)/chat/page.tsx` — `handleSubmitPositive`
- `slm-dashboard/src/app/(dashboard)/admin/chat-feedback/page.tsx` — 양방향 KPI
- `slm-dashboard/src/stores/chat-store.ts` — `markFeedbackSubmitted(askSeq, kind)`

## 3. B) 전체 질의 로그 `tb_ai_chat_log`

### 3.1 스키마 (migration 0046)

| 컬럼 | 타입 | 비고 |
|---|---|---|
| `log_id` | BIGSERIAL PK | |
| `region` | VARCHAR(10) NOT NULL | |
| `user_id` | VARCHAR(45) | |
| `user_question` | TEXT NOT NULL | 최대 4,000자 |
| `intent_name` | VARCHAR(80) | |
| `intent_confidence` | NUMERIC(4,3) | 0~1 (meta.confidence ÷ 100) |
| `graph_type` | VARCHAR(20) | none/table/plot/... |
| `response_time_ms` | INTEGER | 측정: outer ask() perf_counter |
| `bot_summary` | TEXT | preview 2,000자 |
| `total_rows` | INTEGER | 결과 행수 (있으면) |
| `has_visual` | BOOLEAN | |
| `is_multimodal` | BOOLEAN | 이미지 질의 여부 |
| `error` | TEXT | 오류 메시지 |
| `asked_at` | TIMESTAMPTZ NOT NULL | |

인덱스: `asked_at DESC`, `intent_name + asked_at`, `region + user_id + asked_at`.

### 3.2 로깅 지점

`ai_server.py ask()` 래퍼의 `finally` 블록에서 `_log_chat_interaction(request, response_obj, elapsed_ms, error)` 호출. 실패 시 응답 블로킹 없음 (예외 삼킴).

### 3.3 API

- `GET /chat/log?days=7&intent=X&user_id=Y&limit=200` — 최신순 목록
- `GET /chat/log/stats?days=7`:
  ```json
  {
    "overall": { "total": N, "avg_response_ms": ms, "error_count": E, "error_rate": pct, "multimodal_count": M },
    "by_intent": [
      { "intent_name": "...", "count": N, "avg_response_ms": ms,
        "wrong_count": W, "positive_count": P, "wrong_rate": pct }
    ],
    "days": 7
  }
  ```

`by_intent`는 `tb_ai_chat_log` LEFT JOIN `tb_ai_chat_feedback`로 오답률/긍정수 동시 계산.

### 3.4 관리자 페이지 `/admin/chat-log`

- 기간 탭: 1일 / 7일 / 30일
- KPI 4칸: 총 질의 / 평균 응답(s) / 오류율 / 멀티모달 건수 (tone 분기: error_rate ≥ 5 rose, 0~5 amber, 0 emerald)
- 인텐트별 집계: 막대 + count + avg_ms + 오답수(오답률%) + 👍 수. 행 클릭 → 해당 인텐트만 필터
- 로그 목록: #id / 타임스탬프 / user / 인텐트 / MM / graph_type / 오류 뱃지 + 질의 + bot_summary preview + 응답시간

### 3.5 관련 파일
- `db/migrations/0046_chat_log.sql` — DDL
- `slm/endpoints/chat_log.py` — API 라우터
- `slm/ai_server.py` — `_log_chat_interaction()` + finally 훅
- `slm-dashboard/src/lib/api/chat-api.ts` — `ChatLogRow` / `ChatLogStats` / fetcher
- `slm-dashboard/src/app/(dashboard)/admin/chat-log/page.tsx` — 관리자 UI
- `slm-dashboard/src/lib/config/sidebar-menus.ts` — "채팅 로그" 메뉴
- `slm-dashboard/src/components/layout/DynamicBreadcrumb.tsx` — 라벨

## 4. 한계·후속

- `intent_confidence`는 현재 template의 `meta.confidence`만 반영 (정적). 분류기 점수 직접 기록하려면 classifier에서 값 주입 필요
- `user_id`는 AskRequest에 없음 — 현재 모두 null. 인증 미들웨어에서 주입하거나 AskRequest에 선택 필드로 추가 검토
- 스트리밍 엔드포인트 `/ask/stream`은 별도 훅 필요 (현 구현은 `/ask`만)

## 5. 커밋

- `slm@3cfe66e` — feedback positive 허용
- `slm-dashboard@f7ea152` — 양방향 피드백 UI + 관리자 KPI
- `slm@e42d800` — tb_ai_chat_log 스키마/로깅/API
- `slm-dashboard@4fa6039` — /admin/chat-log 페이지
