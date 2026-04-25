---
name: 보고서 사양 (장애 조치·일 점검)
status: P1 사양 확정 / 구현 대기
created: 2026-04-25
---

# 보고서 사양 — 장애 조치 보고서 + 일 점검 보고서

## 1. 목적

현장 작업자·운영자가 채팅·UI 로 등록해 둔 **장애 조치 이력**과 **점검 이력**을
취합하여 일/주/월 단위 보고서로 발행한다. SLM 이 자연어로 요약하고 사용자가
인라인 수정·확정한 뒤 A4 인쇄·PDF 로 배포한다.

- **재사용 원칙**: 이력 데이터는 `tb_task_master` 단일 소스를 그대로 참조.
  보고서는 *발췌·요약·편집·잠금·인쇄* 만 담당한다.
- **확장 원칙**: 동일 구조로 향후 주간·월간·교체·알람 요약 보고서 추가 가능
  (`report_type` 필터 분기).

## 2. 범위 (P1 / P2)

### P1 (본 사양)
- 메뉴 신설: `보고서` (상위) → `장애 조치 보고서`, `일 점검 보고서`
- 보고서 CRUD (목록·상세·작성·수정·삭제)
- 이력 다중 선택 + AI 자동 요약 (Ollama 로컬, 항목 추가 즉시)
- 항목별 인라인 편집, 사진 추가·삭제·제외 토글
- 확정 워크플로 (`draft` → `finalized` 잠금)
- 인쇄용 CSS (브라우저 PDF 출력) + A4 부록 사진 2-up
- 채팅 점검 인텐트 분기 (`일상점검`/`정기점검` 키워드 → `inspection_type`)

### P2 (별도 사양 예정)
- 서버 측 PDF 생성 (puppeteer) + 다운로드 첨부
- 채팅에서 "이번 주 장애 보고서 만들어줘" → 보고서 자동 초안 생성
- 검토·승인 워크플로 (`reviewer_id`, `reviewed_at`)
- 주간·월간 자동 보고서 (스케줄러)
- 알람 요약 보고서 / 설비 교체 보고서

## 3. 데이터 모델

### 3.1 신규 테이블 — Migration 0058

#### `tb_report`
| 컬럼 | 타입 | 설명 |
|---|---|---|
| report_id | BIGSERIAL PK | |
| region | VARCHAR(20) NOT NULL | 멀티테넌시 |
| report_type | VARCHAR(30) NOT NULL | `fault_action` / `daily_inspection` (P2 확장) |
| report_date | DATE NOT NULL | 보고일자 (사용자 선택) |
| author_id | VARCHAR(50) NOT NULL | 작성자 (current user) |
| title | VARCHAR(200) | 자동 생성 (`{보고일자} 장애 조치 보고서`) — 수정 가능 |
| status | VARCHAR(20) NOT NULL DEFAULT `'draft'` | `draft` / `finalized` |
| finalized_at | TIMESTAMPTZ | 확정 시각 (NULL = draft) |
| finalized_by | VARCHAR(50) | 확정자 |
| photo_layout | VARCHAR(10) DEFAULT `'2up'` | `'1up'` / `'2up'` (사진 부록 페이지당 장수) |
| created_at | TIMESTAMPTZ DEFAULT now() | |
| updated_at | TIMESTAMPTZ DEFAULT now() | |

인덱스: `(region, report_type, report_date DESC)`, `(author_id, created_at DESC)`

#### `tb_report_item`
| 컬럼 | 타입 | 설명 |
|---|---|---|
| item_id | BIGSERIAL PK | |
| report_id | BIGINT NOT NULL FK → tb_report ON DELETE CASCADE | |
| seq | INT NOT NULL | 본문 표시 순서 |
| task_id | BIGINT REFERENCES tb_task_master | 원본 이력 (NULL 가능 — 사용자가 수동 입력 시) |
| occurred_at | TIMESTAMPTZ | 발생일자 (원본 task_start_time 복사, 편집 가능) |
| occurred_text | TEXT | 발생내용 (AI 요약 → 사용자 편집) |
| resolved_at | TIMESTAMPTZ | 조치일자 |
| resolved_text | TEXT | 조치내용 (AI 요약 → 사용자 편집) |
| original_text | TEXT | 원문 보존 (`task_content` + `resolution_note` 결합본) — UI "원문 보기" 토글용 |
| photo_urls | JSONB | 본문/부록에 포함될 사진 URL 배열 |
| exclude_photo | BOOLEAN DEFAULT false | 사용자가 사진을 본 항목에서 제외 |
| ai_summary_at | TIMESTAMPTZ | 마지막 요약 생성 시각 (재요약 추적) |
| ai_model | VARCHAR(50) | 사용 모델 (예: `gemma4:26b-a4b-it-q4_K_M`) |
| created_at | TIMESTAMPTZ DEFAULT now() | |
| updated_at | TIMESTAMPTZ DEFAULT now() | |

인덱스: `(report_id, seq)`, `(task_id) WHERE task_id IS NOT NULL`

### 3.2 기존 테이블 확장

#### `tb_task_master`
- `inspection_type VARCHAR(20)` 신규 컬럼 — `'일상' / '정기' / '특별' / NULL`
- `task_category='점검'` 일 때만 의미 가짐 (CHECK 제약은 두지 않음 — 점진적 채움)

### 3.3 분류 정책 (기존 유지)
`docs/fault-category-policy.md` 그대로 적용 — 본 보고서는 분류를 *재해석하지 않으며*
`tb_task_master`의 값을 그대로 표시한다. 알람 자동 연계는 여전히 금지
(`memory/feedback_no_auto_alarm_link.md`).

## 4. 메뉴 / 라우트

| 메뉴 | 라우트 | tb_menu |
|---|---|---|
| 보고서 (상위) | `/reports` (목록 리다이렉트) | `report` 부모 노드 |
| 장애 조치 보고서 | `/reports/fault-action` | 자식 |
| 일 점검 보고서 | `/reports/daily-inspection` | 자식 |
| 보고서 상세 | `/reports/{type}/{report_id}` | (메뉴 제외, 동적 라우트) |

`sidebar-menus.ts` 정적 fallback + `tb_menu` INSERT 둘 다 필요
(CLAUDE.md "메뉴 구조" 항).

## 5. UX 플로우

### 5.1 목록 페이지 (`/reports/{type}`)
- 상단 액션: `[추가]`, 보고일자 범위 필터, 작성자 필터, 상태 필터 (draft/finalized)
- 테이블: 보고일자 / 제목 / 작성자 / 항목 수 / 상태 / 마지막 수정 / 작업
- 행 클릭 → 상세

### 5.2 추가 다이얼로그
1. **보고일자 선택** (날짜 피커, 기본 = 오늘)
2. **이력 검색·선택**
   - `장애 조치 보고서` → `tb_task_master WHERE task_category='고장보고' OR fault_category IN ('고장','이상','교체')`
   - `일 점검 보고서` → `tb_task_master WHERE task_category='점검' OR fault_category='점검'` + `inspection_type` 필터
   - 기간 필터 기본값: 최근 7일
   - 다중 체크 + "선택 추가"
3. "생성" 클릭 → POST `/reports` { report_type, report_date, task_ids[] }
   - 백엔드: `tb_report` INSERT → 각 task 별 LLM 요약 → `tb_report_item` 일괄 INSERT
   - 응답: `report_id` → 상세 페이지로 이동
   - 로딩 스피너: "SLM 이 항목을 요약하는 중…"

### 5.3 상세 페이지 (`/reports/{type}/{report_id}`)
- 헤더 메타: 보고일자 / 작성자 / 상태 / `[수정 모드 진입/종료]` / `[확정]` / `[인쇄]` / `[삭제]`
- 본문: 항목 목록 (드래그 정렬 가능)
  - 각 항목 카드:
    - 시설 / 설비 / 분류 (read-only — 원본 task 표시)
    - 발생일·발생내용 (인라인 편집)
    - 조치일·조치내용 (인라인 편집)
    - 사진 썸네일 (추가/삭제/제외 토글)
    - `[원문 보기]` 토글 → `original_text`
    - `[재요약]` → 동일 task 로 LLM 재호출
- 인쇄 미리보기: `@media print` CSS 적용된 별도 뷰

### 5.4 확정 / 잠금
- `draft` 상태에서만 항목 편집 가능
- `[확정]` 클릭 → 확인 다이얼로그 → `status='finalized'`, `finalized_at/by` 기록
- `finalized` 상태:
  - 모든 입력 disabled
  - `[수정 모드 진입]` 버튼 노출 (재진입 시 `status` 다시 `draft` + finalized 메타 NULL)
  - 권한: 작성자 본인만 (P2 에서 RBAC 확장)

### 5.5 인쇄용 출력 (A4)
- `@page { size: A4; margin: 18mm 14mm; }`
- 페이지 1: 표지 헤더 (제목·보고일자·작성자) + 본문 항목 표
- 부록 페이지: 사진 (`photo_layout` 따라 1-up / 2-up)
  - 캡션: `[항목 N] {시설} · {설비} · {발생일자}`
  - 사진 없는 항목 / `exclude_photo=true` 항목은 부록에 미포함
- `print-color-adjust: exact` — 다크모드 색 밀림 방지

## 6. AI 요약 (Ollama)

### 6.1 요약 호출
- 모델: `tb_ai_model_setting` 기본 모델 사용 (Migration 0056)
- 시스템 프롬프트 (요지):
  - 출력 = 발생내용 + 조치내용 두 단락
  - 한국어 1차, 사실만 추출, 수치는 원문 유지
  - 50~200자 분량
  - 추측·일반론 금지 (Zero-Hallucination 원칙)
- 입력:
  - 시설/설비/분류
  - `task_content` (원문)
  - `resolution_note` (조치 원문)
  - `task_start_time`, `resolved_at`
- 출력 파싱:
  ```json
  { "occurred_text": "...", "resolved_text": "..." }
  ```

### 6.2 원문 보존
- `original_text = "[발생]\n{task_content}\n\n[조치]\n{resolution_note}"`
- UI "원문 보기" 토글 — `feedback_preserve_answer_content` 원칙
- 사용자가 수정해도 `original_text` 는 변하지 않음

### 6.3 재요약
- 단일 항목 `[재요약]` 버튼 → POST `/reports/items/{item_id}/resummarize`
- 사용자 편집 내용은 덮어씀 (확인 다이얼로그)
- `ai_summary_at` 갱신

## 7. 채팅 인텐트 분기 (점검 등록)

### 7.1 키워드 라우팅 (`isFaultRecordIntent` 확장)
기존: `(기록)` + `(고장|이상|교체|점검|오류)` + `(시설키워드)`

추가 규칙 (`parse_fault_text`):
- `(일상점검|일점검|일상 점검)` → `task_category='점검'`, `fault_category='점검'`, `inspection_type='일상'`
- `(정기점검|정기 점검|월간점검|연간점검|분기점검)` → 동일 + `inspection_type='정기'`
- `(특별점검|긴급점검)` → `inspection_type='특별'`
- 위 중 하나가 매칭되면 카드 헤더 = "점검 기록 확인" (`FaultRecordConfirmCard` 동일 컴포넌트, `mode='inspection'` prop)

### 7.2 카드 변경 사항
- 헤더 텍스트 / 아이콘 (🛠️ → 🔍)
- "분류" 행 = `점검 / {inspection_type}`
- 나머지 동일

## 8. API 설계

| 메소드 | 경로 | 설명 |
|---|---|---|
| GET | `/reports` | 목록 (filters: type, region, date_from, date_to, status, author) |
| GET | `/reports/{report_id}` | 상세 (items 포함) |
| POST | `/reports` | 신규 + 항목 일괄 요약 |
| PATCH | `/reports/{report_id}` | 메타 수정 (title, photo_layout) |
| POST | `/reports/{report_id}/finalize` | 확정 |
| POST | `/reports/{report_id}/reopen` | 수정 모드 재진입 |
| DELETE | `/reports/{report_id}` | 삭제 (draft 만 허용) |
| POST | `/reports/{report_id}/items` | 항목 추가 (task_ids[]) |
| PATCH | `/reports/items/{item_id}` | 항목 편집 (필드별) |
| POST | `/reports/items/{item_id}/resummarize` | 재요약 |
| DELETE | `/reports/items/{item_id}` | 항목 삭제 |
| POST | `/reports/items/{item_id}/photos` | 사진 추가 (multipart) |
| DELETE | `/reports/items/{item_id}/photos` | 사진 삭제 (URL 지정) |

모든 변경 API 는 `status='finalized'` 인 보고서에 대해 405 반환. 단 `reopen` 은 예외.

## 9. 권한

| 행위 | 권한 |
|---|---|
| 목록·상세 조회 | 같은 region 의 로그인 사용자 모두 |
| 추가 | 로그인 사용자 모두 |
| 수정·삭제 (draft) | 작성자 본인 |
| 확정·재오픈 | 작성자 본인 (P2 에서 관리자 그룹 확장) |
| 인쇄 | 조회 권한 동일 |

## 10. 테스트 시나리오 (P1 검수)

1. 장애 조치 이력 3건(사진 1건/2건/0건) 선택 → 보고서 생성 → 항목 3개, 사진 부록 페이지 = 3장 / 2-up = 2 페이지
2. 항목 1건 인라인 편집 후 저장 → 새로고침 후 반영 확인
3. 사진 없는 항목에 사진 추가 → 부록 자동 포함
4. `[확정]` → 모든 인라인 편집 disabled
5. `[수정 모드 진입]` → 다시 편집 가능
6. 채팅 "신평 배수지 일상점검 했어 기록해줘" → `inspection_type='일상'`, `task_category='점검'` 으로 저장
7. 일 점검 보고서 추가 시 위 6 의 점검 이력이 후보 목록에 노출
8. 인쇄 미리보기 → A4 페이지 분할·캡션 정상

## 11. 향후 확장 (P2 ~)

- 채팅에서 보고서 직접 생성 (`보고서 만들어줘` 인텐트)
- 서버 PDF 생성 + 첨부 다운로드
- 주간·월간 스케줄러 자동 발행
- 다중 검토·승인 단계
- 보고서 템플릿 커스터마이징 (구축 업체별 머리글·로고)

## 12. 변경 이력

- 2026-04-25 — 초안 작성 (D1~D9 추천안 모두 채택)
