# 업무 메모 + 일정 알림 사양 v1

> 2026-07-19 사용자 요청. DB 영속(시스템 리셋 후에도 유지) 전제.
> Migration 0105 / 메뉴 M005-3(업무 메모)·M005-4(일정 알림) — 보고서 그룹.

## 1. 목적·범위

- **업무 메모**: 운영자가 제목+내용으로 기록을 남기고, 날짜·제목·내용·작성자로
  검색. 조직 내 공유(전체 열람). 수정은 작성자 본인만, **삭제는 본인 + 마스터
  권한자**(2026-07-19 추가 — 서버가 tb_user.user_auth='MASTER' 직접 확인,
  클라이언트 플래그 불신뢰. 마스터도 타인 메모 수정은 불가 — 내용 위변조 방지).
- **일정 알림**: 달력에서 할 일(제목)+내용+날짜·시각을 등록하면 해당 시각에
  **팝업**으로 알림. 확인 전까지 재표시(놓침 방지).

### 용어 구분 (기존 사양 충돌 검토)
- SCADA **알람**(tb_equipment_alarm_report, alarm-popup-spec)과 완전 별개.
  본 기능은 **일정 알림**(개인 스케줄 리마인더)으로 명명·표기 통일.
- `docs/alarm-popup-spec.md` 의 위기대응 모달·SITE_SETTING.ALARM_POPUP_ENABLED
  마스터 토글과 무관 — 일정 알림 팝업은 독립 컴포넌트·독립 폴링.
- 모니터링 > 알람 캘린더(/monitoring/alarm-calendar)는 SCADA 알람 히트맵 —
  본 일정 달력(/reports/schedule)과 별개 화면.

## 2. DB (Migration 0105)

```sql
CREATE TABLE tb_memo (
  region     varchar(20) NOT NULL,
  memo_idn   bigserial,
  title      varchar(200) NOT NULL,
  content    text NOT NULL DEFAULT '',
  created_by varchar(50) NOT NULL,       -- tb_user.user_id
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  use_yn     char(1) NOT NULL DEFAULT 'Y',
  PRIMARY KEY (region, memo_idn)
);
CREATE INDEX idx_memo_search ON tb_memo (region, created_at DESC);

CREATE TABLE tb_user_schedule (
  region       varchar(20) NOT NULL,
  schedule_idn bigserial,
  title        varchar(200) NOT NULL,    -- 할 일
  content      text NOT NULL DEFAULT '',
  alarm_at     timestamptz NOT NULL,     -- 알림 시각
  created_by   varchar(50) NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  notified_at  timestamptz,              -- 최초 팝업 표시 시각 (관측용)
  acked_at     timestamptz,              -- 사용자가 팝업에서 확인한 시각
  use_yn       char(1) NOT NULL DEFAULT 'Y',
  PRIMARY KEY (region, schedule_idn)
);
CREATE INDEX idx_schedule_due ON tb_user_schedule (region, created_by, alarm_at)
  WHERE acked_at IS NULL AND use_yn = 'Y';
```

- region 멀티테넌시 유지 (PK 포함).
- 일정은 **개인 소유** — 등록자 본인에게만 팝업 (created_by 기준 due 조회).
- 삭제는 소프트(use_yn='N') — 감사 추적.

## 3. API (Python FastAPI)

### 메모 `endpoints/memo.py` — prefix `/memo`
| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/memo/list` | 검색: `date_from`·`date_to`(작성일), `title`, `content`, `created_by` (ILIKE 부분일치), 페이징 `page`/`page_size` |
| POST | `/memo` | 생성 {title, content, created_by} |
| PUT | `/memo/{memo_idn}` | 수정 (작성자 본인만 — user_id 대조) |
| DELETE | `/memo/{memo_idn}` | 소프트 삭제 (작성자 본인 또는 마스터) |

- 목록 응답에 작성자명(tb_user.user_nm) join 포함.

### 일정 `endpoints/user_schedule.py` — prefix `/schedule`
| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/schedule/list` | `month=YYYY-MM` 달력 월 단위 조회 (본인 것) |
| GET | `/schedule/due` | `user_id` 기준 due 목록: `alarm_at <= now AND acked_at IS NULL AND use_yn='Y'`. 조회 시 notified_at 최초 기록 |
| POST | `/schedule` | 등록 {title, content, alarm_at, created_by} |
| PUT | `/schedule/{idn}` | 수정 (본인만) |
| POST | `/schedule/{idn}/ack` | 팝업 확인 → acked_at=now |
| DELETE | `/schedule/{idn}` | 소프트 삭제 (본인만) |

## 4. UI

### 업무 메모 `/reports/memo` (M005-3)
- 상단 검색바: 기간(from/to) + 검색 대상 선택(제목/내용/작성자) + 키워드
- 목록 테이블: 작성일 | 제목 | 작성자 | (행 클릭 → 상세/수정 다이얼로그)
- "메모 작성" 버튼 → 제목+내용 다이얼로그. 본인 글만 수정/삭제 노출.
- **달력 보기 (2026-07-20 추가)**: 목록 ↔ 달력 토글 — 작성일 기준 월 달력에
  메모 제목 뱃지(최대 3+more), 날짜 클릭 → 우측 그날 메모 목록 → 클릭 상세.
  월 100건 초과 시 최근 100건 표시 안내. 일정 알림 달력과 동일 look.

### 일정 알림 `/reports/schedule` (M005-4)
- 월 달력 그리드 (일~토). 날짜 셀에 해당일 일정 제목 뱃지(최대 3 + more).
- 날짜 클릭 → 해당일 일정 목록 + "일정 추가" (제목/내용/시각 입력).
- 지난 일정은 회색, 확인(acked) 표시.

### 알림 팝업 `ScheduleAlarmPopup` (전역)
- `(dashboard)` 레이아웃에 마운트. **30초 폴링** `/schedule/due`.
- due 존재 시 모달: 제목·내용·예정 시각 목록 표시, 건별 [확인] → ack.
- 확인 전까지 다음 폴링에도 재표시 (모달 떠 있는 동안 중복 오픈 방지).
- 폐쇄망 원칙 — 브라우저 푸시(FCM 등) 사용 안 함. 폴링 방식.
- 로그인한 본인 일정만 (session.user.user_id 기준).

## 5. 비범위 (v2 후보)

- 반복 일정(매일/매주), 다른 사용자 지정 알림(위임), 메모 첨부파일,
  메모↔시설 연계 태깅, 일정 채팅 등록 인텐트("내일 9시 점검 알림 등록해줘").
- ~~메모→일정 연계~~ → **v2 구현 (2026-07-21)**: 메모 상세의 "일정으로 등록"
  버튼 — 제목·내용 프리필된 일정 등록 다이얼로그.

## 6. 검증 체크리스트

- [ ] 메모 생성→검색(제목/내용/작성자/기간 각각)→수정→삭제
- [ ] 일정 등록(1~2분 뒤 시각)→해당 시각 경과 후 30s 내 팝업→확인→재표시 없음
- [ ] backend 재시작 후 데이터 유지 (DB 영속)
- [ ] tsc 0건, 메뉴 접근 권한(전 권한 부여)


## 달력 직접 편집 (2026-07-23)

일정·메모 달력 보기의 **셀 내 항목을 직접 클릭**하면 보기/수정 다이얼로그가
열린다 (구글 캘린더식 — 기존 날짜 선택 → 우측 목록 2단계 유지 + 단축 경로
추가). hover 링 표시, 날짜 선택도 함께 갱신.

### 달력 더블클릭 등록 (2026-07-23)

일정 달력의 **날짜 셀 더블클릭 = 해당 날짜로 신규 등록** 다이얼로그 (날짜
프리필). 항목 위 더블클릭은 제외 — 항목 단일 클릭이 이미 수정 창을 연다.
