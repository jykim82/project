# 설비 장애 이력 관리 사양 (Migration 0045)

**작성:** 2026-04-17~18
**상태:** P1 완료

## 1. 목적

- 현장 작업자가 알람 발생 인지 → 현장 확인 → 채팅창 자연어로 기록
  - 예: "신평 배수지 PLC 고장 기록해줘" → 봇 확인 → DB 저장
- 각 태그별 수동 관리 불가 → **설비(equipment) 단위**로 관리
- 년간 장애 통계 · 원인 분석 · MTBF 산출 기반 마련

## 2. 설계 결정

**신규 테이블 불필요, `tb_task_master` 확장 채택.**
이유:
- 단일 소스 오브 트루스 (점검/정비/교체/청소/고장보고 통합)
- 인프라 재사용 (TaskFormDialog, /crisis/tasks CRUD, vision_session FK, 알람 억제)
- 이중 관리 방지

## 3. DB 스키마 (Migration 0045)

### 3.1 `tb_task_master` 확장 컬럼

| 컬럼 | 타입 | 설명 |
|---|---|---|
| equipment_id | VARCHAR(64) FK → tb_equipment_info | 설비 연결 |
| equipmenttype | VARCHAR(50) | PLC/가압펌프/유량계/밸브/모뎀/UPS/센서/전원 |
| fault_category | VARCHAR(30) | 고장/이상/교체/점검 |
| severity | VARCHAR(20) | 경고/주의/정보 |
| linked_alarm_start, linked_alarm_tagsn | — | tb_equipment_alarm_report 복합 FK |
| photo_urls | JSONB | 현장 사진 URL 배열 |
| recorded_by | VARCHAR(50) | 기록자 user_id |
| resolved_by, resolved_at, resolution_note | — | 조치 내역 |
| status | VARCHAR(20) | 진행중/완료/보류 (기본 '진행중') |

### 3.2 `task_category` 확장

기존 `점검/정비/교체/청소/기타` + **`고장보고`** 추가.

### 3.3 신규 테이블 `tb_chat_pending_action`

채팅 멀티턴 초안 (TTL 5분):
- session_id (PK, uuid hex)
- user_id, intent, draft JSONB, created_at, expires_at

### 3.4 통계 뷰 (4개)

| 뷰 | 용도 |
|---|---|
| v_equipment_fault_stats | 시설·설비·분류별 총계/진행/완료/평균조치시간 |
| v_equipment_fault_monthly | 월별 추이 |
| v_equipment_mtbf | 설비별 MTBF (고장 간격 평균 일수) |
| v_site_fault_ranking | 시설 Top N + 30일/1년 건수 |

## 4. 채팅 플로우

```
[U1] 신평 배수지 PLC 고장 기록해줘
  ↓ 프런트 useChatSubmit.isFaultRecordIntent() 감지
  ↓ POST /chat/fault/draft { user_id, text }
  ↓ 백엔드 parse_fault_text() 파싱 + equipment_id fuzzy resolve
  ↓ tb_chat_pending_action INSERT (TTL 5분)

[B1] FaultRecordConfirmCard 렌더:
  ┌───────────────────────────────┐
  │ 🔧 장애 기록 확인              │
  │ 시설: 신평(배수지)            │
  │ 설비: PLC (plc_3)             │
  │ 분류: 고장                    │
  │ 발생: 2026-04-17 15:30        │
  │ [예, 기록] [취소]             │
  └───────────────────────────────┘

[U2] "예, 기록" 클릭
  ↓ POST /chat/fault/confirm { session_id, action: "yes" }
  ↓ INSERT tb_task_master (task_category='고장보고', status='진행중')
  ↓ DELETE tb_chat_pending_action

[B2] "장애 기록 완료 (ID: 15)"
```

## 5. 컴포넌트 맵

| 파일 | 역할 |
|---|---|
| `db/migrations/0045_task_master_fault_log.sql` | DB 확장 |
| `slm/endpoints/chat_fault_record.py` | /chat/fault/draft + /confirm |
| `slm/endpoints/equipment_health.py` | /monitoring/equipment-health/* |
| `slm/endpoints/alarm_crisis.py` | /crisis/tasks CRUD (확장 필드 지원) |
| `components/chat/FaultRecordConfirmCard.tsx` | 채팅 확인 카드 |
| `hooks/use-chat-submit.ts` | 키워드 감지 + 분기 |
| `lib/api/fault-record-api.ts` | 채팅 API 클라 |
| `lib/api/equipment-health-api.ts` | 통계 API 클라 |
| `components/crisis/TaskFormDialog.tsx` | 고장보고 필드 (설비/분류/심각도) |
| `components/crisis/TaskTable.tsx` | 고장보고 Badge + 메타 표시 |
| `app/(dashboard)/monitoring/equipment-health/page.tsx` | 통계 대시보드 |

## 6. 키워드 감지 규칙

`isFaultRecordIntent(text)` 조건:
- `기록` 포함
- `(고장|이상|교체|점검|오류)` 포함
- `(배수지|가압장|감압|정수장|취수장|소블록|블록|댐)` 포함

세 조건 모두 충족 → `/chat/fault/draft` 경로.

## 7. 권한

- 기록: 로그인 사용자 누구나
- 삭제/완료 처리: 계정 권한 기반 (RBAC는 기존 시스템 재사용)

## 8. 통계 대시보드 (/monitoring/equipment-health)

### 8.1 KPI
- 총 장애 건수 / 진행중 / 완료 / 평균 조치시간
- 영향 설비 수 + 분류별 (고장/이상/교체+점검)

### 8.2 월별 추이
- 최근 12개월 바 차트 (합산)

### 8.3 시설 Top 20
- 총계 / 30일 / 1년 / 설비수 컬럼

### 8.4 MTBF 집중관리
- 최소 2회 이상 고장 발생 설비
- MTBF < 30일: 빨강 (critical)
- < 90일: 노랑 (warning)
- 이상: 회색 (normal)

## 9. 향후 확장 (P2+)

- 사진 첨부 턴 (챗봇이 선제 질문)
- 알람 자동 감지 → linked_alarm_id 자동 제안
- LLM 후처리로 resolution_note → 표준 원인 태깅
- 월간 보고서 자동 생성

## 10. 커밋 이력

- `web@09b395d` — Migration 0045
- `slm@eb5e185` — /chat/fault/draft + /confirm 엔드포인트
- `slm-dashboard@968b17b`, `5034fee` — FaultRecordConfirmCard + 채팅 통합
- `slm@346c762`, `slm-dashboard@93d8027` — /crisis/tasks 고장보고 지원
- `slm@22a636b`, `slm-dashboard@d199f14` — 설비 건강성 통계 대시보드
