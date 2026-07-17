---
name: 보고서 사양 (장애 조치·일 점검)
status: P1 사양 v4 — PDF·인쇄 양식 100% 일치 + UX 보강 / 구현 완료
created: 2026-04-25
updated: 2026-04-28
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
  - **프런트 사전 분류 게이트** (2026-07-17 수정): `isFaultRecordIntent` 에
    점검 패턴(`점검 && (기록|등록|저장|남겨) && 시설유형`) 추가 — 기존엔
    고장/이상 키워드가 없어 "점검 기록해줘" 문구가 draft 경로로 못 갔음.
    현장 모드 일상 점검 액션(field-mode-spec v3)이 이 경로 사용

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
| approval_chain | JSONB | 결재 도장 이름 `{manager, reviewer, approver}` (Migration 0059) |
| responsible_name | VARCHAR(100) | 인쇄 본문 담당자 셀 (운영자 직접 입력, NULL=공란, Migration 0073) |
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

### 3.4.1 보고서 유형별 항목 라벨 (v4)

같은 컬럼 (`tb_report_item`) 이지만 보고서 유형에 따라 표시 라벨이 다름:

| 컬럼 | 장애 조치 보고서 (`fault_action`) | 일 점검 보고서 (`daily_inspection`) |
|---|---|---|
| `occurred_at` | 발생 일시 | 점검 일시 |
| `occurred_text` | 발생 내용 | 점검 내용 |
| `resolved_at` | 조치 일시 | 조치 일시 |
| `resolved_text` | 조치 내용 | 조치 사항 |
| `symptom` | 장애 현상 | 점검 결과 |
| `cause` | 장애 원인 | *(미사용 — 카드/양식에서 숨김)* |
| `key_issues` | 주요 사항 | 특이 사항 |
| `system_categories` | 장애 시스템 (`system`) | 점검 시스템 (`inspection_system`) |
| `equipment_categories` | 장애 장비 (`equipment`) | 점검 장비 (`inspection_equipment`) |
| 인쇄 양식 제목 | 장애조치결과보고서 | 일상점검결과보고서 |

프런트 분기: ItemCard 가 `getItemLabels(reportType)` 으로 라벨·카테고리 type
선택. 인쇄 양식 (`incident-html.ts`) 은 `getSheetLabels(reportType)` 사용.

### 3.4.2 근본원인 LLM 사후 분류 (Migration 0063)

사용자 입력 부담 없이 통계용 라벨을 누적하기 위해, **자유 텍스트는 그대로
유지** + **LLM 이 사후에 분류** 하는 방식 채택. 운영자는 평소처럼 자유롭게
보고서를 작성하고, 백엔드가 야간 배치 또는 [재분류] 트리거로 코드를 부여.

**분류 체계 (`tb_root_cause_taxonomy`)** — 18 코드 / 7 그룹:

| 그룹 | 코드 | 라벨 |
|---|---|---|
| COMM | COMM_MODEM/COMM_CABLE/COMM_SIGNAL/COMM_NETWORK | 모뎀 고장 / 케이블 빠짐 / LTE 신호 / 네트워크 일반 |
| POWER | POWER_UPS/POWER_TRIP/POWER_NOISE | UPS 노후 / 차단기 트립 / 전원 노이즈 |
| PLC | PLC_CARD/PLC_FIRMWARE | PLC 카드 고장 / 펌웨어·메모리 |
| SENSOR | SENSOR_STUCK/SENSOR_NOISE/SENSOR_DRIFT/SENSOR_FAULT | 계측값 고정 / 노이즈 / 드리프트 / 센서 고장 |
| MECHANICAL | MECH_PUMP/MECH_VALVE | 펌프 / 밸브 |
| HUMAN | HUMAN_INSTALL/HUMAN_OPERATE | 시공 결함 / 조작 실수 |
| UNKNOWN | UNKNOWN | 원인 미상 |

`weight` 컬럼으로 노후도 가중치 보유 (P1 단순 합산, P2 가중치·시간 감쇠).

**`tb_report_item` 추가:**
- `root_causes JSONB` — 코드 배열 (LLM 분류 결과)
- `root_cause_classified_at TIMESTAMPTZ`
- `root_cause_model VARCHAR(50)`

**백엔드 모듈 / 엔드포인트:**
- `slm/root_cause_classifier.py` `classify_item()` — 자유 텍스트 → 코드 0~3개
  · 시스템 프롬프트: 명백히 부합하는 코드만, 새로운 사실 생성 금지
  · LLM 빈 응답·실패 시 빈 codes + fallback=True (잘못된 코드 절대 부여 X)
- `GET  /reports/taxonomy/root-causes` — 마스터 조회
- `POST /reports/items/{id}/classify-causes` — 단일 즉시 분류
- `POST /reports/items/classify-causes-batch` — 일괄 (기본 미분류만, limit)
- `GET  /reports/stats/root-causes?region=` — 빈도/설비별/교체 후보 순위

**노후도 점수 (구현 완료):**

`weighted_score = SUM( taxonomy.weight × time_decay )` — 통계 SQL CTE 안에서 산출

| 시점 | 시간 감쇠 (`time_decay`) |
|---|---|
| 최근 1년 | 1.0 |
| 1~2년 | 0.5 |
| 그 이상 | 0.2 |

UI 노출 (교체 후보 순위 표):
- 가중치 점수 (≥5 default badge / 2~5 secondary / <2 outline)
- 최근 1년 발생 건수 (primary 컬러 강조)
- 코드 매칭 건수 / 총 보고 건수

**야간 cron 트리거:**
- `POST /reports/items/classify-causes-cron` — 모든 region 미분류 항목 일괄 분류
- 외부 cron 또는 OS 스케줄러에서 호출 (인증 헤더 없이 동작 — 내부망 한정)
- 응답: `{ regions:[{region, processed, hits}], total_processed }`
- 운영 권장: `crontab -e` 으로 매일 03:00 호출

**P3 추가 확장 가능 (현재 보류):**
- `tb_equipment_lifespan` 연식 보정 (설비별 도입 연도 기반)

**미분류 항목 진단 (운영자 보강):**
- `GET /reports/stats/unclassified-items?region=` — 분류 시도 후 실패 항목 + 추정 사유
- 사유 휴리스틱: `too_short` (30자 미만) / `placeholder` (더미·테스트 패턴) /
  `unknown_only` (UNKNOWN 만 매칭) / `no_match` (코드 매칭 0)
- 통계 페이지 "미분류 항목" 섹션 — 각 행에 사유 Badge + 본문 미리보기 + tooltip 가이드
- 운영자 액션: hint 보강 (관리 메뉴) → [전체 재분류] 재실행

**대시보드 KPI — 교체 권고:**
- `/dashboard` 메인 KPI 카드 7번째: 교체 권고 (가중치 점수 ≥ 5.0 인 설비 수)
- 1순위 설비 ID·점수 sub-text 노출
- 클릭 시 `/monitoring/equipment-health` 근본원인 통계 탭으로 이동

**LLM 빈 응답 회피 — Ollama `format=json` 옵션:**
`gemma4` 같은 멀티모달 모델은 자유 단락엔 강하나 정형 JSON 출력엔
빈 응답을 자주 반환. `ollama_client.generate(format="json")` 으로
JSON-only 모드 강제. **모델 교체 불필요 — 1줄 수정만**.

**통계 대시보드 (`/monitoring/equipment-health` 5번째 탭):**
- `RootCauseStatsSection` 컴포넌트
- 코드별 빈도 (그룹 색상 배지 + 분포 막대)
- 설비 교체 후보 순위 (cause_count, total_count)
- 설비별 코드 분포
- [전체 재분류] 버튼 — `classify-causes-batch` 호출

**점검 보고서 항목 제외:** `report_type='daily_inspection'` 인 항목은 근본원인
분류 대상이 아니므로 batch / stats 모두 자동 제외.

### 3.5 incident_report 양식 흡수 (Migration 0059)

`docs/incident_report.html` (장애조치결과보고서 표준 양식) 의 항목을 흡수.

**`tb_report` 추가:**
- `approval_chain JSONB` — 결재란
  ```json
  {
    "manager":  { "name": "홍길동", "signed_at": null },
    "reviewer": { "name": "김검토", "signed_at": null },
    "approver": { "name": "이승인", "signed_at": null }
  }
  ```
  값이 `null`/`""` 이면 빈 칸으로 인쇄.

**검토·승인 정책 (확정):**
- **전자서명 미사용**. 결재란은 **인쇄 후 수기 사인** 전용.
- 시스템에는 *담당자 이름* 만 기록 (참고용). `signed_at` 필드는 스키마에만 존재
  하며 P1 에선 **항상 `null`** — 향후 전자서명 도입 시 활용 가능.
- 결재 워크플로 (다단계 승인·반려·이의제기) 시스템화 **미진행**. 이름이 인쇄
  양식에 노출되는 것만으로 충분하다는 운영자 결정.
- UI 의 ApprovalEditor 는 "이름만 입력" 가이드를 노출.

**`tb_report_item` 추가:**
| 컬럼 | 타입 | 설명 |
|---|---|---|
| symptom | TEXT | 장애 현상 (양식 §장애현상) |
| cause | TEXT | 장애 원인 (양식 §장애원인) |
| key_issues | TEXT | 주요 사항 (양식 마지막 멀티라인) |
| system_categories | JSONB | 장애 시스템 체크박스 배열 |
| equipment_categories | JSONB | 장애 장비 체크박스 배열 |

**카테고리 type 4종 (Migration 0060/0062):**
- `system` (장애 시스템) / `equipment` (장애 장비) — 장애 조치 보고서
- `inspection_system` (점검 시스템) / `inspection_equipment` (점검 장비) — 일 점검 보고서

**system_categories / equipment_categories 옵션 — 관리 메뉴에서 동적 CRUD (Migration 0060):**

- 테이블: `tb_report_category(category_id, category_type, code, label, sort_order, use_yn)`
- 관리 메뉴: **`관리 > 보고서 카테고리`** (`/admin/report-categories`, `M100-11`)
- 백엔드 API: `slm/endpoints/report_categories.py`
  - `GET /report-categories?category_type=system|equipment`
  - `POST /report-categories` `{ category_type, code, label?, sort_order? }`
  - `PATCH /report-categories/{id}` `{ label?, sort_order?, use_yn? }`
  - `DELETE /report-categories/{id}`
- 프런트 훅: `useReportCategories('system' | 'equipment')`
- 보고서 항목 카드 + 인쇄 양식이 본 훅을 통해 옵션 동적 로딩
- 초기 seed 19종 (장애 시스템 8 + 장애 장비 11) — 기존 하드코딩 상수와 동일

**기본 seed 값:**
- 장애 시스템 `system` (8): 현장제어반·네트워크·SCADA/HMI·서버/DB·전원/UPS·계측/센서·응용 SW·기타
- 장애 장비 `equipment` (11): 유량계·수위계·압력계·수질계측기·펌프/밸브·PLC/RTU·DSU/모뎀·Serial Converter·스위치/라우터·서버/PC·기타
- 점검 시스템 `inspection_system` (8) — Migration 0062: 시설 외관·계측/센서·전원/UPS·통신/네트워크·제어반·서버/DB·응용 SW·기타
- 점검 장비 `inspection_equipment` (10) — Migration 0062: 펌프/밸브·유량계·수위계·압력계·수질계측기·PLC/RTU·모뎀·스위치·서버/PC·기타

추가/수정/사용 토글/삭제 모두 관리 페이지에서 가능. `tb_report_item.system_categories`/`equipment_categories` JSONB 배열에는 `code` 값이 저장되며, 화면 표시는 `label` 사용.

**기존 occurred_text/resolved_text 매핑:**
- `occurred_text` ⇄ 양식 "장애현상" (symptom 비어있을 때 fallback)
- `resolved_text` ⇄ 양식 "조치사항"
- 두 새 필드 (cause, key_issues) 는 사용자가 추가로 입력 (LLM 요약은 occurred/resolved
  단락만 다룸)

### 3.6 분류 정책 (기존 유지)

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

### 5.5 인쇄·PDF 출력 (A4) — 새 창 방식

**v4 변경:** in-page `@media print` 분기는 dashboard layout 폰트·색이 인쇄에
새는 문제로 폐기. **새 창에 incident_report.html 양식을 그대로 재현하는 방식**
으로 통일.

- 모듈: `slm-dashboard/src/lib/reports/incident-html.ts`
- 함수: `openIncidentPrintWindow(report)`
  1. `generateIncidentHtml(report)` 가 docs/incident_report.html 의 CSS
     (Nanum Myeongjo 30px·.document max-width 820px·결재란 width/height·표
     padding) 그대로 포함한 self-contained HTML 문자열 생성
  2. `window.open('_blank')` → `document.write(html)` → 폰트 로딩 후
     `window.print()` 자동 호출
  3. 사용자가 OS 인쇄 다이얼로그에서 **PDF로 저장** 또는 실제 프린터 선택
- 본문: 항목별 1페이지 (`page-break-after: always`) + 사진 부록 페이지
- 사진 부록 정렬: `fault` → `action` → `user`
- 캡션: `[항목 N] {site_name} · {equipment_name} · {source 한글화}` — `user`
  사진엔 사용자 캡션 추가
- `exclude_photo=true` 인 항목 또는 `photo_urls=[]` 인 항목은 부록 미포함
- 새 창 상단 `.pdf-hint` 노출: "PDF로 저장하려면 인쇄 다이얼로그에서 대상을
  'PDF로 저장' 으로 선택하세요" (인쇄 시 hidden)

**상세 페이지 헤더 버튼:**
- `[인쇄]` (Printer 아이콘) — `openIncidentPrintWindow` 호출
- `[PDF]` (Download 아이콘) — `openIncidentPrintWindow` 호출 (동일 함수, 사용자가
  다이얼로그에서 선택)

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

### 6.4 정제 (재요약) — v4: 사용자 입력 보존 + 미리보기 후 적용

**v4 변경 ①** 의미가 *"원본에서 다시 시작"* → *"현재 본문 정제 (사용자 편집 보존)"*
로 변경됨.

- 모듈: `slm/report_summarizer.py` — `refine_item_summary()` 신규
- 입력: 현재 `tb_report_item.occurred_text` + `resolved_text` + 시점 메타 +
  `original_text` (참고용 컨텍스트로만 — 절대 새 정보로 추가 X)
- 시스템 프롬프트 (요지):
  - "사용자가 추가한 단편을 반드시 포함, 임의 생성 금지"
  - "입력의 시간·수치·고유명사·인물명 절대 누락하지 않음"
  - "원문은 참고만 하고 새 사실로 추가하지 않음"
- 호출 실패·응답 누락 시 **사용자 입력 그대로 보존** (절대 클리어 X)
- LLM 응답이 빈 칸이면 사용자 입력으로 보강

**v4 변경 ②** UI 흐름 — *미리보기 후 적용*:

1. 사용자가 항목 카드의 [정제] (Sparkles 아이콘) 클릭
2. 확인 다이얼로그: "현재 본문을 SLM 이 정제할까요?"
3. [정제 실행] 클릭 → `POST /reports/items/{id}/resummarize` `{ user_id, dry_run: true }`
   - 백엔드: `refine_item_summary` 결과만 반환, **DB 미반영**
4. 미리보기 다이얼로그: 현재 본문 vs 정제 결과를 두 컬럼으로 비교
   - "발생 단락": 현재 / 정제 결과
   - "조치 단락": 현재 / 정제 결과
5. [적용] 클릭 → `PATCH /reports/items/{id}` 로 occurred_text/resolved_text 업데이트
   → 화면 새로고침
6. [취소] 클릭 → 다이얼로그 닫고 현재 본문 유지

**편집 모드에서는 [정제] 비활성** (저장되지 않은 입력이 LLM 호출 후 사라지지 않도록).

### 6.5 약식 정제 규칙 (rule-based light format) — v4

LLM 응답 / 사용자 입력 / fallback 모두에 동일하게 후처리하여 **보고서 본문이
일관된 양식**으로 출력되도록 한다.

함수: `slm/report_summarizer.py` `light_format_report_text(text)`

**적용 단계:**

1. **단어 치환** (`apply_phrase_normalization`):
   - 명령형 어미 제거 — `기록해줘`, `등록해줘`, 끝의 `기록`
   - 종결어미 보고체 변환 — `했어`/`했네`/`했지` → `함.`, `났어`/`났네` →
     `발생함.`, `이야`/`야`/`이지`/`라네` → `임.`
   - 구어체 → 보고서체 — `안 돌아감` → `동작 불가.`, `안 돼서` → `동작하지 않아`,
     `안 돼.` → `동작 불가.`, `먹통` → `동작 불가`
   - 다중 공백·빈 줄 정리

2. **문장 분리** (`_split_sentences`): 마침표/물음표/느낌표 + 명시적 줄바꿈
   기준으로 문장 단위 분할

3. **기호 부여** (`apply_bullet_format`): 각 문장 앞에 **`• `** 추가.
   이미 `•·○▶※-*1)2.` 등으로 시작하면 그대로 유지

**예시:**

| 입력 | 출력 |
|---|---|
| `신평 배수지 PLC 고장 기록해줘` | `• 신평 배수지 PLC 고장` |
| `난지마을 배수지 전원이상 발생. 13:20부터 PLC 전원 LED 적색 점등. 추가 — LTE모뎀도 통신이상이 발생하여 제조사 의뢰중.` | `• 난지마을 배수지 전원이상 발생.`<br>`• 13:20부터 PLC 전원 LED 적색 점등.`<br>`• 추가 — LTE모뎀도 통신이상이 발생하여 제조사 의뢰중.` |
| `UPS 배터리 안 돌아감. 교체 필요했네` | `• UPS 배터리 동작 불가.`<br>`• 교체 필요함.` |

**적용 시점:**
- `summarize_task` (보고서 생성 시 항목 첫 요약) — return 전에 적용
- `refine_item_summary` (정제 / 미리보기) — return 전에 적용
- LLM 정상 응답 / fallback 모두에 동일 적용 (양식 일관성)

**대상 필드 (서술이 포함된 5개 항목 모두 정제):**
| 필드 | 소스 | LLM 호출 | light_format |
|---|---|---|---|
| `occurred_text` (발생 단락) | LLM 프롬프트 입력 | ✓ | ✓ |
| `resolved_text` (조치 단락) | LLM 프롬프트 입력 | ✓ | ✓ |
| `symptom` (장애 현상) | incident 양식 사용자 입력 | — | ✓ |
| `cause` (장애 원인) | incident 양식 사용자 입력 | — | ✓ |
| `key_issues` (주요 사항) | incident 양식 사용자 입력 | — | ✓ |

incident 양식 3필드는 분량·중요도가 LLM 정제 대상이 되기엔 작아 `light_format`
만 적용 (사실 보존 + 양식 통일). LLM 호출은 발생/조치 두 단락에 집중.

**미리보기 다이얼로그**: 5개 필드를 row 단위로 좌(현재) / 우(정제) 비교.
비어있는 필드는 row 자체가 자동 생략. 사용자가 [적용 (5개 필드 대체)] 누르면
`PATCH /reports/items/{id}` 한 번에 5개 필드 모두 갱신.

### 6.7 사진 영역 — 조치 전 / 조치 후 분리

상세 페이지 항목 카드의 사진 영역은 **조치 전(fault) / 조치 후(action)** 두
그룹으로 분리 노출 + 각각 별도 추가 입력:

- **조치 전 사진 그룹**: `source IN ('fault','user')` 사진 표시 + URL 입력
  → [사진 추가] 시 `source='fault'` + `caption='조치 전'` 으로 저장
- **조치 후 사진 그룹**: `source = 'action'` 사진 표시 + URL 입력 → [사진 추가]
  시 `source='action'` + `caption='조치 후'` 으로 저장

**컴포넌트:** `PhotoGroup` (props: label, source, photos, editable, inputUrl,
onInputChange, onAdd, adding, onDelete) — 두 인스턴스로 사용.

**인쇄 양식 영향 없음:** `lib/reports/incident-html.ts` 의 사진 부록은 이미
`fault → action → user` 순서로 정렬됨 — UI 분리와 동일한 출력 순.

### 6.6 항목 헤더 5필드 인라인 편집 (v4)

편집 모드에서 헤더에 5개 입력 필드 노출 (기존 read-only 에서 변경):
- `site_name` (시설명) / `facility_type` (시설 유형) / `equipment_name` (설비명)
- `fault_category` (분류 — 고장/이상/교체/점검)
- `inspection_type` (점검 sub-type — 일상/정기/특별)

저장 후 다시 편집 진입 시 `useEffect` 로 draft 상태를 최신값으로 동기화.

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

- ✅ 채팅에서 보고서 직접 생성 (2026-04-30, B 작업 완료)
  · 인텐트 정규식: `(보고서.*(만들|작성|생성|뽑아|편집)|(만들|작성|생성).*보고서)`
  · 기간: 오늘/어제/이번 주/지난 주/이번 달/지난 달 (기본 최근 7일)
  · `slm/endpoints/chat_report_create.py` (draft + confirm)
  · `ReportCreateConfirmCard` (후보 미리보기 + 예/취소 + [상세 보기])
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
- 2026-04-26 — v3 incident_report.html 양식 흡수 (Migration 0059):
  approval_chain (결재란), symptom/cause/key_issues 본문 필드,
  system_categories/equipment_categories 체크박스 배열,
  인쇄 모드에 `IncidentReportPrintLayout` 컴포넌트 (Nanum Myeongjo, A4 표 양식)
- 2026-04-27 — v4 PDF·인쇄 양식 100% 일치 + UX 보강:
  · `lib/reports/incident-html.ts` `generateIncidentHtml` 새 창 방식으로 전환
    (in-page `@media print` 폐기) — incident_report.html CSS 그대로 재현
  · 상세 페이지 헤더 [PDF] 버튼 추가
  · `refine_item_summary()` — 재요약을 "현재 본문 정제 (사용자 편집 보존)"
    의미로 변경. LLM 실패·응답 누락 시 사용자 입력 보존
  · 항목 헤더 5필드 인라인 편집 (site_name/facility_type/equipment_name/
    fault_category/inspection_type)
  · 편집 모드에서 [정제] 버튼 비활성화 + 경고 다이얼로그 강화
- 2026-04-28 — v4 정제 미리보기 흐름:
  · `POST /reports/items/{id}/resummarize` 가 `dry_run` 파라미터 지원
  · 결과를 미리보기 다이얼로그에서 현재 vs 정제 결과 두 컬럼 비교
  · [적용] 클릭 시에만 PATCH 로 본문 대체 (사용자 의도 명시 후 반영)
- 2026-04-28 — v4 약식 정제 규칙 (§6.5):
  · `light_format_report_text` — 단어 치환 + 문장 분리 + `• ` 기호 부여
  · `summarize_task` / `refine_item_summary` 모두에 적용 (LLM 응답·fallback 공통)
  · 보고서 본문이 일관된 양식으로 출력 (LLM 부재 환경에서도 보고서체 보장)
- 2026-04-28 — v4 근본원인 LLM 사후 분류 (Migration 0063, §3.4.2):
  · `tb_root_cause_taxonomy` 18 코드 / 7 그룹 (COMM/POWER/PLC/SENSOR/MECH/HUMAN/UNKNOWN)
  · `tb_report_item.root_causes JSONB` + classified_at + model
  · `slm/root_cause_classifier.py` — 자유 텍스트 → 코드 0~3개
  · API 4종 (taxonomy 조회, 단일 분류, 배치, 통계)
  · 통계 API: 코드별 빈도 / 설비별 분포 / 교체 후보 순위
  · 사용자 입력 부담 0 — 야간 배치 또는 [재분류] 트리거
- 2026-04-28 — v4 일 점검 보고서 항목 정리 (Migration 0062):
  · 카테고리 type 2종 추가: inspection_system (8) / inspection_equipment (10)
  · 보고서 유형별 라벨·옵션 분기 (§3.4.1 표)
  · 인쇄 양식 제목 분기 (장애조치결과보고서 / 일상점검결과보고서)
  · `getItemLabels(reportType)` (프런트) / `getSheetLabels(reportType)` (인쇄)
  · 일 점검 보고서엔 `cause` (장애 원인) 행이 카드/양식에서 숨김
  · 관리 페이지 4 탭 (시스템/장비 × 장애/점검)
- 2026-04-30 — 검토·승인 정책 확정 (§3.5):
  · 전자서명 미사용 — 결재란은 인쇄 후 수기 사인 전용
  · `signed_at` 필드는 P1 항상 NULL (스키마만 보존, 향후 활용 가능)
  · 결재 워크플로 시스템화 미진행 — 이름만 인쇄 양식에 노출
  · ApprovalEditor 안내 문구 추가
- 2026-04-30 — 운영 가이드 신설 (`docs/operations/`):
  · `report-quickstart.md` — 운영자용 1장 매뉴얼 (작성·정제·결재·인쇄 흐름)
  · `cron-setup.md` — 근본원인 자동 분류 cron 등록 가이드
    (Linux crontab / macOS launchd / Docker compose 사이드카)
  · launchd `local.slm.classify-cron` 등록 검증 (매일 03:00 호출)
- 2026-04-28 — v4 사진 영역 조치 전/후 분리 (§6.7):
  · 항목 카드의 사진 영역을 두 그룹으로 분리 (조치 전 / 조치 후)
  · 각 그룹에 별도 URL 입력 + [사진 추가] 버튼
  · 추가 시 자동으로 source('fault' 또는 'action') + caption 셋업
  · 인쇄 양식 부록은 영향 없음 (이미 source 순 정렬)
  · `PhotoGroup` 컴포넌트 도입
- 2026-04-28 — v4 카테고리 동적 관리 (Migration 0060):
  · `tb_report_category` 신규 + seed 19종 (장애 시스템 8 + 장애 장비 11)
  · 관리 메뉴 `/admin/report-categories` (M100-11) — 추가/수정/사용 토글/삭제
  · 백엔드 `endpoints/report_categories.py` (GET/POST/PATCH/DELETE)
  · 프런트 `useReportCategories` 훅 — 항목 카드·인쇄 양식 모두 DB 옵션 사용
  · `SYSTEM_CATEGORY_OPTIONS` / `EQUIPMENT_CATEGORY_OPTIONS` 상수는 @deprecated
- 2026-04-28 — v4 5필드 정제 확장:
  · `refine_item_summary` 가 occurred/resolved 외에 symptom/cause/key_issues
    도 입력·출력 (LLM 호출은 발생/조치만, 나머지 3필드는 light_format)
  · dry_run 응답 5필드 current/refined
  · 미리보기 다이얼로그 — 5개 row 비교, 비어있는 필드 자동 생략
  · [적용 (5개 필드 대체)] → PATCH 한 번에 5필드 갱신


## 목록 상단 통계 (2026-07-16)

장애 조치 보고서 목록(/reports/fault-action) 상단에 통계 배치 (사용자 요청).

- **KPI 4카드**: 보고서(초안·확정) / 장애 항목(최다 분류) / 조치 완료율
  (80% 미만 amber) / 평균 조치 시간(발생→조치)
- **분류 칩**: fault-category-policy 4분류 색 (고장 rose·이상 amber·교체 sky·점검 emerald)
- **기간 필터**: 30일(기본)/90일/전체 — report_date 기준
- API: `GET /reports/stats?region&report_type&days` (endpoints/reports.py)
- 컴포넌트: `ReportStatsHeader.tsx` — 목록 갱신(추가/삭제) 시 refreshKey 로 동기 재조회
- **일 점검 보고서에도 공통 적용** (2026-07-16 확장): 동일 지표 체계, 항목
  라벨만 "점검 항목" 으로 분기 (`isInspection`). 점검 중 발견 항목의
  조치율·조치 시간도 동일 정의로 유의미

### 조치 시각 역전파 (2026-07-16)

task 조치 완료(채팅 confirm / direct resolve) 시, 그 task 를 참조하는
**초안(draft) 보고서 항목**의 `resolved_at` 을 동기화한다
(`chat_fault_record._sync_draft_report_items`). 확정 보고서는 불변.
배경: 조치 내용을 보고서에 먼저 쓰고 task 를 나중에 완료하면 시각이 영구
누락돼 목록 통계(조치 완료율)가 왜곡됨. 기존 14건은 해당 task 완료 시 자동
회복되는 구조 (임의 백필 안 함 — 시각을 지어내지 않음).
