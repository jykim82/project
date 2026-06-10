# 알람 폴링·인덱스 최적화 (2026-06-10)

알람 조회 / features fetch / 번들 사이즈 3축 최적화.

**관련 사양**: `docs/alarm-popup-spec.md` (알람 팝업)

---

## 1. 폴링 현황 (적용 전)

| 컴포넌트 | 간격 | 페이지 |
|---|---|---|
| `AlarmNotificationBell` | 30초 | 모든 페이지 |
| `AlarmCrisisModal` features fetch | **60초** ← 개선 | 모든 페이지 |
| `dashboard` summary | 5분 / 10초 재시도 | dashboard 만 |
| `GIS facilities` | 60초 | GIS 페이지만 |
| `alarm-dashboard` | 60초 | alarm-dashboard 만 |

---

## 2. 적용한 최적화 3건

### 2.1 features fetch 60초 → 5분 + 이벤트 invalidate
- **변경**: `AlarmCrisisModal.tsx` `FEATURES_REFETCH_MS = 5 * 60_000`
- **이유**: features 는 운영자가 자주 안 바꾸는 설정 (alarm_popup / epanet / manual_rag).
- **즉시성 유지**: 사이트 설정 토글 직후 `window.dispatchEvent(new Event('slm:features-invalidate'))` →
  AlarmCrisisModal 이 이벤트 수신 → 즉시 fetch. 5분 기다리지 않음.
- **효과**: 폴링 횟수 5배 감소 (분당 1회 → 12분당 1회). 6개 페이지 × 24시간 누적 효과 큼.

### 2.2 AlarmAnalysisDetail dynamic import
- **변경**: `AlarmCrisisModal.tsx` 에서 `AlarmAnalysisDetail` 을 `next/dynamic` 으로 lazy load.
- **이유**: AlarmAnalysisDetail 은 크기 있는 컴포넌트 (chart / table / detail card 다수).
  평소엔 안 쓰는데 `DashboardShell → AlarmCrisisModal` 경유로 모든 페이지 초기 bundle 에 포함됨.
- **효과**: 초기 페이지 로드 시 큰 모듈 skip. 사용자가 "알람 분석" 클릭 시점에 로드.
  모바일·저속망 사용자 체감 가능.

### 2.3 Migration 0081 — partial index
- **변경**:
  ```sql
  CREATE INDEX idx_alarm_report_ongoing
    ON tb_equipment_alarm_report (alarm_start_time DESC)
    WHERE alarm_status = '진행중';
  ```
- **EXPLAIN 비교**:
  - Before: `Execution Time: 0.311 ms` (Memoize cache)
  - After: **`Execution Time: 0.082 ms`** (Index Scan)
  - **3.8배 빠름**
- **future-proof**: 현재 13,621 행 / 진행중 49 행. 진행중 알람이 누적되어도 `LIMIT 5` 가 즉시 도달.

---

## 3. 보류 (효과 작음 / 사양 변경)

### 3.1 응답 ETag 캐싱 (보류)
30초마다 호출되는 endpoint 에 ETag → 효과 작음. 구현 복잡도 대비 이득 적음.

### 3.2 SSE/WebSocket 실시간 push (Phase 2)
- 30초 폴링 → 즉시 알림
- alarm-popup-spec.md §8 에 이미 예고
- 사양 변경 (큰 작업)

### 3.3 여러 폴링 통합 hook (Phase 2)
- `useGlobalRealtimeData()` 하나로 묶어서 polling endpoint 줄임
- 별도 사양

---

## 4. 검증

### 4.1 features 정합 (6회 ON/OFF)
6/6 PASS — DB use_yn ↔ /auth/me features.alarm_popup 양방향 정합 + invalidate event 6회 모두 캐치.

### 4.2 인덱스 사용 (EXPLAIN)
`Index Scan using idx_alarm_report_ongoing on tb_equipment_alarm_report` — Seq Scan 폐기.

---

## 5. 변경 이력

- 2026-06-10 — 초안. 폴링 5배 감소 + dynamic import + 부분 인덱스.
