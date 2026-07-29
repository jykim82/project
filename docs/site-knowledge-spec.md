# 현장 지식 카드 사양 v1 — 로드맵 B P1 (Migration 0136)

운영자의 암묵지("이 지점은 매주 화요일 역세척으로 유량이 튄다")를
**코드 수정 없이 웹 UI 로 등록**하고, 경보를 보는 자리에서 바로 보여준다.
`/crisis/site-knowledge` (M006-8).

**관련**: `slm-feature-roadmap-draft.md` §3.B·부록 A.5·B.2·B.3,
`docs/alarm-label-feedback-spec.md`(§3.C 연동 — P2 선제 제안),
`docs/emergency-contact-spec.md`(연락 체계는 그쪽이 정본 — 카드 유형에서 제외)

---

## 1. 원칙 (부록 권고안 채택)

- **안전 신호 불가침 (B.2)** — SCADA 임계(HH/LL)·태그 품질 게이트는 카드로
  끌 수 없다. 카드가 미래에 할 수 있는 최대치는 통계 감시(z-score·IForest)
  신뢰도 하향과 억제 **마킹**까지다.
- **P1 은 억제하지 않는다** — 조회·기록 계층만. 카드는 사람에게 보이는
  맥락이고, 알람 생성·해제 어디에도 개입하지 않는다. 억제 반영(P2)은
  기존 두 억제 경로(작업관리 `suspend_alarm_types`·태그 품질 계층)와
  **통합 억제 로그** 설계가 선행돼야 한다 (부록 A.5).
- **유형 4종 (B.3)** — 주기 이벤트/설비 특성/예외 상황/조치 절차.
  '연락 체계'는 비상연락처가 정본이라 제외 (정본 이원화 금지).
- **감사 추적** — 모든 변경은 `tb_site_knowledge_revision` 에 스냅샷 보존.
  공공 납품에서 "이 지식을 누가 언제 왜 등록·수정했나"에 답한다.
- 조건(`conditions`)은 LLM 프롬프트 전용이 아닌 **구조화 입력** — 미리보기
  매칭·향후 룰 엔진 참조가 같은 조건을 읽는다 (§3.B 핵심 요구).

## 2. 데이터 모델 — Migration 0136

`tb_site_knowledge` (region 멀티테넌시, PK region+knowledge_id):

| 필드 | 내용 |
|---|---|
| `k_type` | `periodic` 주기 이벤트 / `trait` 설비 특성 / `exception` 예외 상황 / `procedure` 조치 절차 |
| `title` | 카드 한 줄 제목 |
| `target_refs` jsonb | `{sitenames[], facilitytypes[], tagsns[], alarm_categories[]}` — 지정 필드 간 AND, 배열 내 OR. **최소 1개 필드 필수** (전역 카드 금지 — 무관 지점 혼입 방지) |
| `conditions` jsonb | `{weekdays[1~7 ISO], time_from "HH:MM", time_to, months[1~12]}` — 전부 선택. 비우면 상시 |
| `description` | 자유 서술 (향후 임베딩 대상) |
| `valid_from/valid_until` | 기간 한정 (공사 등). NULL=상시 |
| `status` | `draft` 초안 / `active` 적용 / `retired` 종료 |
| `created_by/updated_by` | 작성·최종 수정자 |

`tb_site_knowledge_revision` — 변경 전 스냅샷(jsonb) + 변경자·시각.
수정·삭제 전 상태를 통째로 남긴다 (삭제도 revision 을 남기고 soft-delete
아님 — 카드 자체는 삭제 가능하되 이력은 남는다).

## 3. API — `slm/endpoints/site_knowledge.py`

- `GET /site-knowledge` — 목록 (status·k_type·검색 필터)
- `POST /site-knowledge` / `PUT /{id}` / `DELETE /{id}` — CRUD.
  수정·삭제는 revision 선기록
- `GET /site-knowledge/match?sitename=&facilitytype=&category=&at=` —
  그 경보에 지금 보여줄 카드. active + 유효기간 내 + target AND 매칭 +
  (at 시각이 conditions 요일·시간대에 들면) 반환
- `POST /site-knowledge/preview` — **영향 미리보기**: 카드 조건으로 지난
  30일 `tb_equipment_alarm_report` 를 매칭해 "전체 N건 중 M건 해당" +
  대상 목록 표본. 등록 전 잘못된 조건을 눈으로 확인 (§3.B 안전장치 ①).
  P1 은 억제가 없으므로 "억제 예정"이 아니라 **"이 카드가 표시될 경보"**다

## 4. UI

- `/crisis/site-knowledge` (M006-8 "현장 지식") — 카드 목록(유형·상태
  필터) + 등록/수정 폼 + 폼 안 영향 미리보기 버튼
- 승인 워크플로는 강제하지 않는다 — draft/active 는 작성자가 선택
  (§3.B "2단계 승인 on/off" 중 off 기본. 지자체 1~2인 담당 현실)
- **소비처 1 (P1)**: 경보 분석 상세(`AlarmAnalysisDetail`) — 그 경보의
  현장·분류·발생 시각으로 match 조회 → "현장 지식" 카드 표시.
  운영자가 경보를 판단하는 자리에 암묵지가 함께 뜬다

## 5. 이 사양이 하지 않는 것 (P1)

- **알람 억제·신뢰도 하향 없음** — 통합 억제 로그 설계 선행 (부록 A.5).
  `feedback_no_auto_alarm_link` 원칙 준수: 자동 제안이 자동 적용으로
  넘어가지 않는다
- pgvector 임베딩·채팅 컨텍스트 주입 없음 — 채팅 연동은 P2
- 오탐 라벨 → 카드 등록 선제 제안 없음 (P2 — §3.C 연동)

## 6. 후속 (P2+)

- 통계 감시 신뢰도 하향·억제 마킹 + 통합 억제 로그 (안전 임계 불가침 유지)
- ~~채팅 응답에 관련 카드 주입~~ → **구현 완료 (2026-07-29)**:
  sitename 문맥이 있는 채팅 성공 응답에 매칭 active 카드 최대 3장 첨부
  (`_attach_site_knowledge` — ai_server 결과 조립 2개 지점). BotMessage
  amber "현장 지식" 블록 — 경보 상세와 동일 체계. 임베딩 검색(서술 유사도)
  은 여전히 P2 — 현재는 대상(현장·유형)·조건 매칭만
- 반복 오탐 패턴 → 카드 등록 선제 제안 (제안까지만 — 적용은 사람)
- 2단계 승인 워크플로 SITE_SETTING opt-in
