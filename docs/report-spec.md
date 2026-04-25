---
name: 보고서 사양 (장애 조치·일 점검)
status: P1 사양 확정 v2 — 정합성 보강 / 구현 대기
created: 2026-04-25
updated: 2026-04-25
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
- **사실 보존**: 보고서 시점 메타(시설명·설비명·분류)를 항목에 캐시 — 원본
  변경에 영향받지 않음.

## 2. 범위 (P1 / P2)

### P1 (본 사양)
- 메뉴 신설: `보고서` (상위) → `장애 조치 보고서`, `일 점검 보고서`
- 보고서 CRUD (목록·상세·작성·수정·삭제)
- 이력 다중 선택 + AI 자동 요약 (Ollama 로컬, 항목 추가 즉시)
- 항목별 인라인 편집, 사진 추가·삭제·제외 토글, 정렬 변경
- 확정 워크플로 (`draft` → `finalized` 잠금)
- 인쇄용 CSS (브라우저 PDF 출력) + A4 부록 사진 2-up
- 채팅 점검 인텐트 분기 (`일상점검`/`정기점검`/`특별점검` → `inspection_type`)

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
| region | VARCHAR(20) NOT NULL | 멀티테넌시 (작성자 JWT region) |
| report_type | VARCHAR(30) NOT NULL | `fault_action` / `daily_inspection` (CHECK) |
| report_date | DATE NOT NULL | 보고일자 (사용자 선택, 과거·미래 모두 허용) |
| author_id | VARCHAR(50) NOT NULL | 작성자 user_id (recorded_by 와 동일 패턴) |
| title | VARCHAR(200) | 자동 생성 후 수정 가능 |
| status | VARCHAR(20) NOT NULL DEFAULT `'draft'` | `draft` / `finalized` (CHECK) |
| finalized_at | TIMESTAMPTZ | 확정 시각 (NULL = draft) |
| finalized_by | VARCHAR(50) | 확정자 user_id |
| photo_layout | VARCHAR(10) DEFAULT `'2up'` | `'1up'` / `'2up'` (CHECK) |
| created_at | TIMESTAMPTZ DEFAULT now() | |
| updated_at | TIMESTAMPTZ DEFAULT now() | |

인덱스: `(region, report_type, report_date DESC)`, `(author_id, created_at DESC)`,
`(status) WHERE status='draft'`

#### `tb_report_item`
| 컬럼 | 타입 | 설명 |
|---|---|---|
| item_id | BIGSERIAL PK | |
| report_id | BIGINT NOT NULL FK → tb_report ON DELETE CASCADE | |
| seq | INT NOT NULL | 본문 표시 순서 (1..N) |
| task_id | BIGINT REFERENCES tb_task_master(task_id) | 원본 이력 (NULL = 수동 입력, P2) |
| **site_name** | VARCHAR(100) | **시설명 — 보고 시점 캐시** |
| **facility_type** | VARCHAR(50) | **시설 유형 (배수지/가압장/블록…)** |
| **equipment_name** | VARCHAR(100) | **설비명 — 보고 시점 캐시** |
| **fault_category** | VARCHAR(30) | **분류 — 보고 시점 값 캐시 (고장/이상/교체/점검)** |
| **inspection_type** | VARCHAR(20) | **점검 sub-type (일상/정기/특별 / NULL)** |
| occurred_at | TIMESTAMPTZ | 발생일자 (원본 task_start_time 복사, 편집 가능) |
| occurred_text | TEXT | 발생내용 (AI 요약 → 사용자 편집) |
| resolved_at | TIMESTAMPTZ | 조치일자 |
| resolved_text | TEXT | 조치내용 (AI 요약 → 사용자 편집) |
| original_text | TEXT | 원문 보존 (`task_content` + `resolution_note` 결합) |
| photo_urls | JSONB | 사진 객체 배열 (3.4 절 참조) |
| exclude_photo | BOOLEAN DEFAULT false | 사진 부록 전체 제외 토글 |
| ai_summary_at | TIMESTAMPTZ | 마지막 요약 생성 시각 |
| ai_model | VARCHAR(50) | 사용 모델명 |
| created_at | TIMESTAMPTZ DEFAULT now() | |
| updated_at | TIMESTAMPTZ DEFAULT now() | |

인덱스:
- `(report_id, seq)` — 본문 정렬
- `(task_id) WHERE task_id IS NOT NULL` — 원본 추적
- **UNIQUE `(report_id, task_id)` WHERE task_id IS NOT NULL** — 같은 task 중복 추가 방지

### 3.2 region 멀티테넌시 처리

- `tb_task_master` 자체엔 region 컬럼이 없음 → `equipment_id` 통해
  `tb_equipment_info.region` 으로 추론.
- 보고서 생성 시 흐름:
  1. `tb_report.region` = 작성자 JWT region
  2. 항목 추가 시, 각 task_id 의 region 을 `tb_equipment_info` 조인으로 확인
  3. 보고서 region 과 불일치 시 **422 Unprocessable Entity** + 항목 거부
- 같은 region 내 사용자만 보고서 조회·편집 가능 (`tb_report.region = ?`).

### 3.3 기존 테이블 확장

#### `tb_task_master`
- `inspection_type VARCHAR(20)` 신규 컬럼 — `'일상' / '정기' / '특별' / NULL`
- CHECK 제약은 P1 에선 두지 않음 (운영 데이터 누적 후 P2 에서 화이트리스트 검토)

### 3.4 사진 객체 배열 (`photo_urls` JSONB) — 전/후 구분

장애 조치 **전/후 사진 구분이 필요하다는 요구사항**에 따라, 단일 컬럼 유지 +
JSONB 객체 배열로 출처 메타 보존:

```json
[
  {
    "url": "/api/files/chat_attachments/abc.jpg",
    "source": "fault",
    "caption": "발생 시점",
    "taken_at": "2026-04-17T15:30:00Z"
  },
  {
    "url": "/api/files/chat_attachments/def.jpg",
    "source": "action",
    "caption": "조치 후",
    "taken_at": "2026-04-17T18:00:00Z"
  },
  {
    "url": "/api/files/chat_attachments/ghi.jpg",
    "source": "user",
    "caption": "추가 참고"
  }
]
```

- `source`: `fault` (고장보고 사진) / `action` (조치 사진) / `user` (보고서에서
  추가)
- 보고서 항목 생성 시:
  - `tb_task_master.photo_urls` (Migration 0045) → `source='fault'`
  - `tb_task_master.resolution_photo_urls` (Migration 0051) → `source='action'`
  - 두 배열을 합쳐 단일 `tb_report_item.photo_urls` 에 저장
- 사용자가 추가 업로드한 사진은 `source='user'` 로 append
- `taken_at` 은 메타데이터(원본 사진의 timestamp 또는 task 의
  task_start_time/resolved_at)를 best-effort 로 채움. 없으면 NULL
- **인쇄 부록 정렬**: 항목별로 `fault → action → user` 순서로 출력
- **인쇄 캡션**: `[항목 N] {site_name} · {equipment_name} · {source 한글화}`
  (예: "발생 시점", "조치 후", "추가 참고")

### 3.5 분류 정책 (기존 유지)

`docs/fault-category-policy.md` 그대로 적용. 본 보고서는 분류를 *재해석하지
않으며* `tb_task_master` 의 값을 그대로 캐시·표시. 알람 자동 연계 금지
(`memory/feedback_no_auto_alarm_link.md`).

## 4. 메뉴 / 라우트

| 메뉴 | 라우트 | menu_idn | menu_type |
|---|---|---|---|
| 보고서 (상위) | (없음, 그룹 노드) | `M005` | `menu` (app_path=NULL) |
| 장애 조치 보고서 | `/reports/fault-action` | `M005-1` | `menu` |
| 일 점검 보고서 | `/reports/daily-inspection` | `M005-2` | `menu` |
| 보고서 상세 | `/reports/{type}/{report_id}` | (메뉴 등록 X, 동적 라우트) | — |

### 4.1 `tb_menu` INSERT (Migration 0058 포함)

Migration 0049/0054 패턴 따라 모든 region 에 동일 등록 + MASTER/ADMIN 권한 부여
(`tb_auth_menu`).

### 4.2 `sidebar-menus.ts` 정적 fallback

```typescript
{
  id: "M005",
  label: "보고서",
  icon: FileText,  // lucide-react
  children: [
    { id: "M005-1", label: "장애 조치 보고서", path: "/reports/fault-action" },
    { id: "M005-2", label: "일 점검 보고서",   path: "/reports/daily-inspection" },
  ],
}
```

## 5. UX 플로우

### 5.1 목록 페이지 (`/reports/{type}`)
- 상단 액션: `[추가]`, 보고일자 범위 필터, 작성자 필터, 상태 필터 (draft/finalized)
- 테이블: 보고일자 / 제목 / 작성자 / 항목 수 / 상태 / 마지막 수정 / 작업
- 행 클릭 → 상세

### 5.2 추가 다이얼로그

1. **보고일자 선택** (날짜 피커, 기본 = 오늘. 과거·미래 모두 허용)
2. **이력 검색·선택** — 후보 필터:
   - 장애 조치 보고서: `task_category = '고장보고'` 단독
   - 일 점검 보고서: `task_category = '점검'` 단독
   - 기간 필터 기본값: 최근 7일
   - 보조 필터: `inspection_type` (일 점검만), 시설/설비 검색
   - 다중 체크 → "선택 추가"
3. **"생성" 클릭** → POST `/reports`
   ```json
   { "report_type": "fault_action", "report_date": "2026-04-25", "task_ids": [12, 15, 18] }
   ```
   - 백엔드:
     1. `tb_report` INSERT (region = JWT region, author_id = JWT user_id)
     2. 각 task_id 의 region 검증 (불일치 시 422)
     3. 각 task 메타 + 사진 합쳐 LLM 직렬 호출 → `tb_report_item` 일괄 INSERT
     4. 응답: `{ report_id, items: [...] }` → 상세 페이지로 이동
   - 로딩 스피너: "SLM 이 항목을 요약하는 중… ({현재}/{전체})"

### 5.3 상세 페이지 (`/reports/{type}/{report_id}`)

- 헤더 메타: 보고일자 / 작성자 / 상태 / `[수정 모드 진입/종료]` /
  `[확정/재오픈]` / `[인쇄]` / `[삭제]`
- 본문: 항목 목록 (드래그 정렬 → `PATCH /reports/{report_id}/items/reorder`)
  - 각 항목 카드:
    - 시설 / 설비 / 분류 (`site_name`/`equipment_name`/`fault_category` —
      read-only, 캐시 값)
    - 발생일·발생내용 (인라인 편집)
    - 조치일·조치내용 (인라인 편집)
    - 사진 썸네일 (출처별 그룹: 발생/조치/추가)
      - 추가/삭제/캡션 수정/`exclude_photo` 토글
    - `[원문 보기]` 토글 → `original_text`
    - `[재요약]` → 동일 task 로 LLM 재호출 (사용자 편집 덮어쓰기 확인 다이얼로그)
- 인쇄 미리보기: `@media print` CSS 적용된 별도 뷰

### 5.4 확정 / 잠금

- `draft` 상태에서만 항목 편집 가능
- `[확정]` 클릭 → 확인 다이얼로그 → `status='finalized'`, `finalized_at/by` 기록
- `finalized` 상태:
  - 모든 입력 disabled
  - `[수정 모드 진입]` 버튼 노출 → `POST /reports/{id}/reopen` →
    `status='draft'` + `finalized_at/by = NULL`
- 권한:
  - 본인이 아닌 사용자가 편집·확정·재오픈 호출 시 **403 Forbidden**
  - finalized 보고서 삭제도 본인만 (P1 — P2 에서 admin 분리)

### 5.5 인쇄용 출력 (A4)

- `@page { size: A4; margin: 18mm 14mm; }`
- **라이트모드 강제**:
  ```css
  @media print {
    :root { color-scheme: light !important; }
    body { background: white !important; color: black !important; }
    .no-print { display: none !important; }
  }
  ```
- 페이지 1: 표지 헤더 (제목·보고일자·작성자) + 본문 항목 표
- 부록 페이지: 사진 (`photo_layout` 따라 1-up / 2-up)
  - 출처 순서: `fault` → `action` → `user`
  - 캡션: `[항목 N] {site_name} · {equipment_name} · {source 한글화}`
  - `exclude_photo=true` 인 항목 또는 `photo_urls=[]` 인 항목은 부록 미포함
- `print-color-adjust: exact` — 일부 색 보존이 필요한 카드만 적용

## 6. AI 요약 (Ollama)

### 6.1 요약 모듈

- 신규 모듈: `slm/report_summarizer.py`
- 기존 자산 재사용:
  - `slm/ollama_client.py` 의 `generate()` 함수 (HTTP 호출)
  - 모델 설정: `tb_comm_code(grp_cd='SITE_SETTING', comm_cd='AI_MODEL')` (Migration 0056)
- 인터페이스:
  ```python
  def summarize_task(
      task_content: str,
      resolution_note: str | None,
      site_name: str,
      equipment_name: str,
      fault_category: str,
  ) -> dict[str, str]:
      """returns {"occurred_text": "...", "resolved_text": "..."}"""
  ```

### 6.2 시스템 프롬프트 (요지)

- 출력 = 발생내용 + 조치내용 두 단락 (JSON 으로 반환)
- 한국어 1차, 사실만 추출, 수치는 원문 유지
- 50~200자 분량
- 추측·일반론 금지 (Zero-Hallucination 원칙)
- 사용자 입력에 없는 시간·수치·고유명사 절대 생성 금지

### 6.3 원문 보존

- `original_text = "[발생]\n{task_content}\n\n[조치]\n{resolution_note}"`
- UI "원문 보기" 토글 (`feedback_preserve_answer_content` 원칙)
- 사용자 편집 후에도 `original_text` 불변

### 6.4 재요약

- 단일 항목 `[재요약]` → POST `/reports/items/{item_id}/resummarize`
- 사용자 편집 내용은 덮어씀 (확인 다이얼로그)
- `ai_summary_at` 갱신

## 7. 채팅 인텐트 분기 (점검 등록)

### 7.1 키워드 라우팅 (`parse_fault_text` 확장)

기존 조건: `(기록)` + `(고장|이상|교체|점검|오류)` + `(시설키워드)` 유지

추가 분기:
- `(일상점검|일점검|일상 점검)` → `task_category='점검'`,
  `fault_category='점검'`, `inspection_type='일상'`
- `(정기점검|정기 점검|월간점검|연간점검|분기점검)` → `inspection_type='정기'`
- `(특별점검|긴급점검)` → `inspection_type='특별'`

### 7.2 카드 변경 사항

`FaultRecordConfirmCard` 동일 컴포넌트에 `mode='inspection'` prop 추가:
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
| DELETE | `/reports/{report_id}` | 삭제 (작성자 본인) |
| POST | `/reports/{report_id}/items` | 항목 추가 (task_ids[]) |
| PATCH | `/reports/{report_id}/items/reorder` | seq 일괄 갱신 (`{ item_ids: [] }`) |
| PATCH | `/reports/items/{item_id}` | 항목 편집 (필드별) |
| POST | `/reports/items/{item_id}/resummarize` | 재요약 |
| DELETE | `/reports/items/{item_id}` | 항목 삭제 |
| POST | `/reports/items/{item_id}/photos` | 사진 추가 (multipart, source='user') |
| DELETE | `/reports/items/{item_id}/photos` | 사진 삭제 (URL 지정) |
| PATCH | `/reports/items/{item_id}/photos` | 사진 캡션·출처 수정 |

응답 코드:
- 변경 API 가 `status='finalized'` 보고서에 호출되면 **405 Method Not Allowed**
  (단 `POST /reopen` 예외)
- region 불일치 / 작성자 아닌 자의 편집 시도: **403 Forbidden**
- 항목 task region 검증 실패: **422 Unprocessable Entity**

## 9. 권한

| 행위 | 권한 |
|---|---|
| 목록·상세 조회 | 같은 region 의 로그인 사용자 모두 |
| 신규 작성 | 로그인 사용자 모두 |
| 수정·항목 변경 (draft) | 작성자 본인만 (403) |
| 확정 / 재오픈 | 작성자 본인만 (P2 에서 admin 그룹 확장) |
| 삭제 | 작성자 본인만 (draft & finalized 모두) |
| 인쇄 | 조회 권한 동일 |

## 10. 파일 저장 경로

- 보고서 사진은 별도 디렉토리 신설 X — 기존 `files/chat_attachments/` 재사용
- 백엔드 업로드 핸들러: `slm/endpoints/reports.py` 가
  `slm/endpoints/chat_fault_record.py` 의 사진 저장 헬퍼를 import 또는 공유

## 11. 테스트 시나리오 (P1 검수)

1. 장애 조치 이력 3건(사진 1건/2건/0건) 선택 → 보고서 생성 → 항목 3개,
   `photo_urls` JSONB 에 source 메타 정확
2. 항목 1건 인라인 편집 후 저장 → 새로고침 후 반영 확인
3. 사진 없는 항목에 사진 추가 → `source='user'` 로 저장, 부록 자동 포함
4. `[확정]` → 모든 인라인 편집 disabled, `[재오픈]` → 다시 편집 가능
5. 채팅 "신평 배수지 일상점검 했어 기록해줘" → `inspection_type='일상'`,
   `task_category='점검'` 으로 저장
6. 일 점검 보고서 추가 시 위 5 의 점검 이력이 후보 목록에 노출
7. 다른 region 사용자가 같은 보고서 GET 시 403, POST 시 422
8. 인쇄 미리보기 → A4 페이지 분할·캡션·라이트모드 정상
9. 항목 드래그 정렬 → `seq` 갱신 후 새로고침해도 순서 유지
10. 같은 task_id 두 번 추가 시도 → unique 제약으로 거부

## 12. 향후 확장 (P2 ~)

- 채팅에서 보고서 직접 생성 (`보고서 만들어줘` 인텐트)
- 서버 PDF 생성 (puppeteer) + 첨부 다운로드
- 주간·월간 스케줄러 자동 발행
- 다중 검토·승인 단계
- 보고서 템플릿 커스터마이징 (구축 업체별 머리글·로고)
- `inspection_type` CHECK 제약 추가 (운영 데이터 누적 후)

## 13. 변경 이력

- 2026-04-25 — 초안 작성 (D1~D9 추천안 채택)
- 2026-04-25 — v2 정합성 보강 (10항목): region 처리, photo_urls JSONB 객체
  배열, 시점 메타 5컬럼 캐시, 후보 필터 단순화, 메뉴 INSERT 형식, AI 요약 모듈
  명시, unique 제약, reorder API, 인쇄 라이트모드 강제, 권한 응답 코드
