# 상황실 키오스크 모드 사양 v1 (P1) — Migration 0141

관제실 벽면·상황실 대형 화면용 **자동 순환 뷰어**. `/kiosk` (M003-12).
프로토타입: `prototype/kiosk-mode-prototype.html` (2026-09-01 승인).

## 1. 원칙 — 새 화면을 만들지 않는다

키오스크는 **기존 화면 3개를 그대로 순환**한다 (GIS 관망도 → 실시간
계통도 → 경보관리). 별도 요약 화면을 새로 만들면 정보가 이원화되고
유지보수가 둘이 된다 — 기존 화면이 곧 콘텐츠.

- 각 화면은 same-origin **iframe 상주 로드** — 순환은 opacity 전환
  (재로딩 없음. display:none 미사용 — 지도·차트 크기 0 초기화 방지,
  워크스페이스 레이어 겹침 수정과 동일 원칙)
- `?kiosk=1` 로 열면 DashboardShell 이 **크롬(사이드바·헤더) 제거** —
  iframe 안 화면이 내용만 차지
- 하단 바: 시계 · 화면 도트(진행 표시) · 최근 경보 티커 · 일시정지 ·
  전체화면 버튼

## 2. 경보 인터럽트

20초 폴링(`/monitoring/alarm-notifications`) — **새 '경고' 경보** 등장 시:
1. 경보관리 화면으로 즉시 전환 + 상단 red flash 배너 (현장·메시지)
2. 60초 유지 후 자동 순환 복귀. 같은 경보로 재인터럽트 없음 (키 기억)
- '주의'는 인터럽트하지 않는다 — 벽면이 주의 알람마다 널뛰면 채터링
  사태의 화면판이 된다. 티커에만 흐름

## 3. 설정

- 순환 간격: 기본 20초, `?interval=초` 오버라이드 (localStorage 유지)
- 화면 목록: P1 고정 3종 → **P2 에서 사이트 설정으로 커스텀** (§5)

## 4. 하지 않는 것 (P1)

- 새 대시보드 위젯 없음 · 자동 로그인 없음(운영 PC 세션 전제) ·
  모바일 대응 없음 (벽면 전용)

## 5. P2 — 구현 완료 (2026-09-19, Migration 0142)

사이트 설정(`/admin/site-settings` "상황실 키오스크 모드" 카드,
`tb_comm_code` SITE_SETTING 그룹) 3종. 키오스크는 로드 시 1회 조회,
실패하면 P1 기본값 폴백 — 벽면 화면은 설정 오류로 멈추지 않는다.

### 5.1 화면 목록·순서 커스텀 — `KIOSK_VIEWS`
- comm_val = 콤마 구분 키, 순서 = 순환 순서. 허용 5종: `gis` 관망 현황 /
  `flow` 실시간 계통도 / `alarm` 경보관리 / `tags` 태그 모니터링 /
  `health` 설비 건강성 (카탈로그는 프런트 `kiosk-views.ts`, 백엔드
  `_KIOSK_ALLOWED_VIEWS` 와 동기 유지 — 새 화면 추가 시 둘 다)
- **경보 인터럽트는 목록과 무관** — 경보관리가 순환에서 빠져도 iframe 은
  항상 상주시키고 경고 발생 시 전환한다 (spec §2 불변)
- 저장 검증: 허용 외 키 제거·중복 제거, 결과 0개면 400

### 5.2 야간 밝기 스케줄 — `KIOSK_NIGHT_DIM`
- comm_val = `"시작시,종료시,밝기%"` (예 `22,6,40`), use_yn = 사용 여부.
  자정 걸침(22→06) 지원, 시작=종료는 비활성 취급
- 적용은 검은 오버레이 opacity (pointer-events 통과) — 조작은 그대로,
  벽면 눈부심만 낮춘다. KPI 스트립에 "야간" 배지 표시

### 5.3 KPI 요약 스트립 — `KIOSK_KPI_STRIP`
- 화면 상단 중앙 오버레이: 진행중 경보(알람 폴링 공유 — 중복 폴링 없음) ·
  30일 발생/해제(`/alarm/summary` 5분 주기). **새 API 없음** — 기존 조합만
- 기본 ON. 새 대시보드 위젯이 아니라 얇은 오버레이 — §1 원칙 유지

### 구현 파일
- 프런트: `src/components/kiosk/` (kiosk-views.ts · use-kiosk-settings.ts ·
  KioskKpiStrip.tsx) + `kiosk/page.tsx` 키 기반 순환 재작성 +
  `admin/KioskSettingsCard.tsx`
- 백엔드: `admin.py` GET/PUT kiosk 블록 (`_parse_kiosk_settings`).
  부수 수정: PUT 의 HTTPException 이 broad except 에 삼켜져 400 이 200 으로
  보이던 문제 재전파로 수정
- 롤백: Migration 0142 하단 ROLLBACK 블록 (설정 3행 삭제 — 키오스크는
  P1 기본값으로 동작)
