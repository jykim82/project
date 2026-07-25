# 알람 팝업 (위기대응 모달) 사양 v1

알람 발생 시 운영자에게 즉시 위기대응 모달을 노출하여 **알람 → 조치** 흐름을
단축. CLAUDE.md 차별화 #2 "알람 ↔ 현장 조치 통합" 강화.

**관련 사양**: `docs/alarm-category-summary-spec.md` (알람 분류 정책),
`docs/emergency-contact-spec.md` (비상연락처 자동 매칭),
`docs/feature-sku-spec.md` §6 (SKU 토글)

---

## 1. 동작 흐름

```
[기존] AlarmNotificationBell 30초 폴링 → notification-store 업데이트
            ↓
[신규] useNewAlarmTrigger hook:
   1) items 변화 감지 (lastCheckedAt 기반)
   2) ALARM_POPUP_ENABLED=true 면 새 알람 추출
   3) severity 별 분기:
      - 경고 → AlarmCrisisModal (강제, 사용자 행동 필요)
      - 주의 → toast (sonner, 우상단 슬라이드)
      - 정상 → 무시
   4) 동시 다발 (5건 이상) → 종합 카드 ("진행중 알람 N건 보기")
```

---

## 2. SKU 토글

### 2.1 마스터 — `SITE_SETTING.ALARM_POPUP_ENABLED`
- DB: `tb_comm_code(region, 'SITE_SETTING', 'ALARM_POPUP_ENABLED').use_yn`
- **기본값 'Y'** — 기본 운영 모듈이라 default ON (B1/B3 와 달리 데이터 의존 없음)
- 관리 페이지: `/admin/site-settings` → "알람 팝업" 카드
- 백엔드: `/auth/me.features.alarm_popup`
- Migration 0080 시드

### 2.2 사용자 세부 옵션 (Phase 2 — 향후)
| 옵션 키 | 기본값 | 설명 |
|---------|--------|------|
| `severityFilter` | `["경고"]` | 모달 강제 발생 severity (체크 안 한 건 toast) |
| `dismissTimeoutS` | `0` (수동) | 0 = 사용자가 닫아야 함, >0 = 자동 닫힘 |
| `groupThreshold` | `5` | N건 이상 동시 발생 시 종합 카드로 묶음 |

> Phase 1 은 마스터 ON/OFF 만. 위 세부 옵션은 사양 v2 에 정의.
> **사운드 알림은 사양에서 제외** (2026-06-10 사용자 결정 — SCADA 운영자
> 대부분 외부 경광등·사이렌 별도 운영, 브라우저 사운드 중복).

---

## 3. UI 사양

### 3.1 AlarmCrisisModal (경고 알람)

```
┌─────────────────────────────────────────────────────────────┐
│ 🚨 위기대응 — 신평 배수지                       [✕]          │
│ ─────────────────────────────────────────────────────────── │
│ 경고   2026-06-10 14:23:05  (방금)                          │
│                                                              │
│ [신평(배) 1지 수위 HH 상태]                                  │
│ 수위 상한 초과 — 펌프 자동 정지                              │
│                                                              │
│ ─────────────────────────────────────────────────────────── │
│ [🚨 현장 출동]  [📋 작업 등록]  [📊 알람 분석]  [✓ 확인]    │
└─────────────────────────────────────────────────────────────┘
```

- **헤더**: 🚨 + "위기대응 — {sitename}" + 닫기
- **severity 배지**: 경고(red) / 주의(amber)
- **타임스탬프**: 절대 시각 + 상대 시각 ("방금" / "3분 전")
- **알람 메시지**: tagsn 기반 message
- **액션 4종** (모두 비동기 처리, 모달 자동 닫힘):

#### 액션 #1 — 🚨 현장 출동
- 알람 메시지에서 카테고리 자동 추출 (UPS / 정전 / 네트워크 / 펌프 / 밸브 등)
- → `EMERGENCY_CONTACT_{카테고리}` SQL 호출
- → 매칭 업체 1순위 전화번호 dropdown ("동양산전 010-5457-3368")
- 사용자 선택 → `tel:` 링크로 dial 또는 클립보드 복사
- v2: 작업 이력에 "출동 요청 → {업체}" 자동 기록

#### 액션 #2 — 📋 작업 등록
- `/crisis/task-management` 신규 페이지로 navigate
- 알람 메타데이터 사전 채워짐 (시설, 알람 메시지, 발생 시각)
- 운영자가 조치 내용 추가 → 저장

#### 액션 #3 — 📊 알람 분석 (2026-06-10 사양 변경)
- **인라인 모달 전환** — 페이지 이동 없이 같은 위치에서 모달만 전환
- 동작:
  1) 위기대응 모달 닫기 + `markAsChecked()`
  2) `fetchAlarmAnalysisDetail(tagsn, alarm_start_time)` 호출 (loading state)
  3) 새 다이얼로그 "경보 분석 상세" open — `<AlarmAnalysisDetail report={...} />`
- 발생원인 / 대응방안 / 알람값 / 경보등급 등 표시 (alarm-dashboard 의 분석 다이얼로그와 동일)
- 사용자가 현재 페이지 (예: 대시보드/트렌드/GIS) 를 벗어나지 않음
- 보조 경로: 직접 `/crisis/alarm-dashboard?tagsn=…&start=…` URL 진입 시에도 자동 open
  (alarm-dashboard 페이지의 autoOpenedRef 로 1회 보장)

#### 액션 #4 — ✓ 확인
- 단순 dismiss + `markAsChecked()` 호출 → lastCheckedAt 갱신
- 같은 알람 재발생 안 함

### 3.2 Toast (주의 알람)

```
┌────────────────────────────────────────────┐
│ ⚠ 주의 — 죽동 배수지 통신이상              │
│ [분석 상세] [확인]                          │
└────────────────────────────────────────────┘
```
- sonner toast (우상단)
- 자동 닫힘 8초
- **"분석 상세"** → `handleAnalyze(alarm)` 호출 (페이지 이동 없이 인라인 모달
  open — 2026-06-10 사양 변경. 이전: URL navigate)

### 3.3 종합 카드 (5건 이상)

```
┌──────────────────────────────────────────────┐
│ 🚨 진행중 알람 N건 — 일괄 확인               │
│  • 신평(배) 수위 HH (경고, 방금)         >    │
│  • 죽동(배) 탁도계 통신이상 (주의, 3분)  >    │
│  • 천의리(배) 모뎀 통신이상 (주의, 5분)  >    │
│  ... 외 (N-3)건                              │
│  💡 행을 클릭하면 분석 상세 모달이 자동 열림 │
│ [전체 알람 보기] [확인]                       │
└──────────────────────────────────────────────┘
```
- 모달 형태 (경고 1건 이상 포함 시) 또는 toast (주의만)
- **각 행** → 클릭 시 위기대응 모달 닫고 인라인 경보 분석 상세 모달 전환
  (페이지 이동 없음 — 2026-06-10 사양). hover 강조 + chevron 아이콘 (>) 표시
- "전체 알람 보기" → `/crisis/alarm-dashboard` (필터 없이 전체 목록)
- 안내 텍스트 (💡) — 행 클릭 가능 명시

#### 행 클릭 동작 (2026-06-10)
종합 카드 행 = `<button>` 으로 변경 (Link 아님). 클릭 시 단일 모달의
액션 #3 과 동일한 `handleAnalyze(alarm)` 호출 → 페이지 이동 없이 분석 모달 open.

---

## 4. 중복 방지

### 4.1 lastCheckedAt 기반
- notification-store 이미 보유
- **팝업 신선도 컷 24h (2026-07-25)**: 발생 후 24시간 지난 진행중 알람은
  첫 방문·localStorage 초기화 브라우저에서도 모달/toast 트리거 제외
  (묵은 알람이 종합 카드로 쏟아지는 문제). 벨 목록·미확인 카운트는 유지.
- **계측 품질 게이트 (P3, 2026-07-24)**: `/monitoring/alarm-notifications`
  items 에 `quality_suspect`/`quality_reason`(tb_tag_quality 조인) 추가.
  품질 이상 태그의 새 알람은 경고 모달·OS 알림·종합 카드 판정에서 제외하고
  **info toast "계측 품질 의심"** 로만 안내 (값 신뢰 불가 — 센서 점검 유도).
  알람 자체는 벨 목록·이력에 그대로 보존 (자동 해제·억제 없음 —
  feedback_no_auto_alarm_link 원칙, 노출 단계 분리만).
- 모달/toast 표시 후 자동 `markAsChecked()` → 다음 폴링에서 같은 알람 재노출 X

### 4.2 페이지 navigate 시
- 새 페이지 이동 후에도 modal 상태 zustand store 유지
- 같은 알람 ID 는 한 세션 내 한 번만 표시

### 4.3 SSE/실시간 push (Phase 2 예정)
- 현재는 30초 폴링 — 최대 30초 지연
- Phase 2 에서 SSE 구독으로 실시간

---

## 5. 데이터 source

### 5.1 새 알람 감지 로직 (notification-store 확장)
```ts
function getNewItems(): NotificationAlarm[] {
  const { items, lastCheckedAt, lastNotifiedAt } = get();
  const since = lastNotifiedAt ?? lastCheckedAt;
  if (!since) return [];
  return items.filter(i => i.alarm_start_time > since);
}
```

### 5.2 카테고리 자동 추출 (현장 출동 액션용)
알람 message 에서 카테고리 키워드 추출:
- "UPS" → UPS
- "정전" / "단전" / "전원" → 정전
- "통신" / "모뎀" / "LTE" / "네트워크" → 네트워크
- "밸브" → 밸브
- "펌프" → 펌프 (현재 비상연락처 시드 없음 — 사용자 추가 필요)

매칭 없으면 — 전체 비상연락처 표시 (`/setup/alarm-contacts` 링크)

---

## 6. 정책 / 예외

### 6.1 알람 폭주 보호
- 30초 폴링 한 번에 5건 이상 → **모든 알람을 종합 카드 1개로** (개별 모달 X)
- 100건 이상 (DB 이상 등) → 모달/toast 모두 skip + 상단 경고 배너

### 6.2 운영자가 자리 비웠을 때
- Phase 2 — 브라우저 Notification API + 소리 + 자동 SMS/이메일 (선택)

### 6.3 같은 알람 반복 발생
- 동일 tagsn + 같은 시간(±5분) 알람 = 동일 알람으로 인식, 모달 1회만
- 알람 닫혀도 같은 tagsn 신규 알람 발생 시 다시 모달

### 6.4 시뮬레이션 / 테스트 알람
- `tb_equipment_alarm_report.alarm_source = 'test'` 인 알람은 모달 skip (Phase 2 — 필요 시)

---

## 7. 검증 시나리오 (Phase 1)

| # | 시나리오 | 기대 |
|---|---------|------|
| 1 | ALARM_POPUP_ENABLED=Y + 경고 알람 발생 | 모달 노출 |
| 2 | ALARM_POPUP_ENABLED=Y + 주의 알람 발생 | toast 노출 |
| 3 | ALARM_POPUP_ENABLED=N + 알람 발생 | 모달/toast 없음 (벨만 점등) |
| 4 | 모달 "확인" 클릭 | lastCheckedAt 갱신, 다음 폴링 미재발생 |
| 5 | 5건 동시 발생 | 종합 카드 1개 |
| 6 | 토글 ON/OFF 6회 | DB use_yn ↔ features.alarm_popup 정합 |

---

## 8. 향후 (Phase 2 / v2)

- 사용자 세부 옵션 (severityFilter / dismissTimeoutS / groupThreshold) —
  Tweaks 패널 통합 또는 별도 사용자 옵션 페이지
- 브라우저 Notification API (탭 백그라운드 알림)
- SSE 실시간 push (30초 지연 제거)
- 알람 → 작업 등록 자동 매핑 (작업관리 사전 폼)
- 카테고리별 비상연락처 자동 다이얼 (deep link, 모바일 `tel:` link)

> **사운드 알림 제외** (2026-06-10) — SCADA 운영자 환경에서 외부 경광등·
> 사이렌과 중복. 브라우저 사운드 별도 가치 낮음.

---

## 9. 변경 이력

- 2026-06-10 v1 — 초안. Phase 1 (모달 + toast + 종합 카드 + SKU 토글) 구현 사양.
- 2026-06-10 v1.1 — 인라인 모달 전환 (§3 / §3.2 / §3.3 갱신). 사운드 알림
  사양 제외 (§2.2 / §8). Tweaks 패널 (이미 구현 완료) 와 사용자 세부 옵션
  관계 명시.


## §신규 2026-07-20 — 해제 표시 · 알람 제어 연동

1. **해제 상태 표시**: 위기대응 모달이 열려 있는 동안 해당 알람이 해제되면
   (진행중 알람 폴링 목록에서 소멸) 카드가 **초록 테두리/배경 + "해제됨" 뱃지
   + "알람이 해제되었습니다" 안내**로 전환. 종합(그룹) 카드 행도 해제됨 뱃지.
2. **명칭**: 작업관리의 "작업 수정" 다이얼로그 → **"알람 제어"** 로 변경.
3. **이 알람 중지 버튼**: 알람 카드에 추가. 클릭 →
   `/crisis/task-management?mode=suppress&...` 로 이동해 **알람 제어 창 자동
   오픈** — 시설/유형/내용(`[알람 중지] <메시지>`)과 **중지할 알람 유형**
   (메시지 키워드 매칭: 수위/압력/유량/펌프/밸브/통신/네트워크/UPS/수질)을
   자동 채움, 기간 등 나머지는 사용자가 입력 후 등록.
   — 알람 자동 해제 금지 원칙(feedback_no_auto_alarm_link)과 부합: 억제는
   작업관리에 **명시 등록**으로만 이뤄짐.

### §신규-③ 확장 2026-07-21 — 헤더 실시간 알람에도 진입점 추가

"이 알람 중지" 진입점을 위기대응 모달 외 **헤더 실시간 알람 2곳**으로 확장.
프리필 URL 은 `buildAlarmSuppressHref()` (`src/lib/utils/alarm-suppress.ts`)
공용 헬퍼로 일원화 (모달·벨·toast 3곳 동일).

1. **헤더 알림 벨 드롭다운** (`AlarmNotificationBell`): 진행중 알람 행마다
   메시지 우측에 **"중지"** 미니 버튼 (BellOff 아이콘). 클릭 시 행 기본 동작
   (알람 현황 이동)과 분리되어 알람 제어 프리필로 이동 (dropdown 은 controlled
   전환으로 닫힘 처리).
2. **주의 toast**: 기존 "분석 상세" 액션 옆 **"알람 중지"** 보조 버튼 추가
   (sonner cancel 슬롯) — 클릭 시 동일 프리필 이동.

### §신규-③ 확장 2026-07-21(2) — 개별 태그 자동 추가

"그 태그만 중지"할 수 있도록 프리필 URL 에 **tagsn** 을 포함하고, 알람 제어
창에서 시설 태그 목록 로드 후 해당 tagsn 의 **datainfo 라벨을 개별 태그로
자동 추가** (`TaskFormDialog.suppressTagsn`). 사용자는 유형 선택을 해제하고
태그 칩만 남겨 해당 태그만 억제 가능.

구현 노트:
- 억제 매칭(backend `_is_alarm_suppressed`)은 비표준 항목을 **알람 메시지
  부분일치**로 판정 — datainfo 라벨 방식과 정합. 단, 태그 datainfo 와 알람
  메시지가 불일치하는 데이터(예: 합덕일반 수위 — 메시지 "공업 1지" vs
  datainfo "공업 3지")는 칩이 tagsn 기준 정확한 태그로 추가되지만 메시지
  부분일치에 걸리지 않을 수 있음 → tagsn 기반 억제는 P2 검토.
- 같은 페이지에서 다른 알람 "중지" 재진입 허용: 자동 오픈 가드를 1회성 →
  **파라미터 키 기반**으로 변경. TaskFormDialog 는 닫혀도 마운트 유지되므로
  **열릴 때마다 defaults 재동기화** (이전 값 잔존 방지 — 일반 "작업 등록"
  버튼도 매번 빈 폼으로 열림).
