# 누수 의심 알림 사양 (야간최소유량 CUSUM)

`/monitoring/leak-alerts` — 야간최소유량(MNF) CUSUM 분석으로 누수 의심
현장을 자동 감지·수동 확인하는 알림 체계.

## 파이프라인
1. 백그라운드 스캔 (ai_server lifespan, 6시간 주기) + 수동 `POST /leak-cusum/scan`
2. `anomaly_detector.compute_cusum_for_tags` — 평일/주말 분리 baseline,
   k=1.5σ 허용치, 임계 H=5.0σ×기간보정(√(n/20)). **판정 = cusum_max ≥ H**
3. 누수의심만 `tb_leak_cusum_alert` 저장 (24h dedupe) → 목록·확인(ack) UI

## 선정 사유 서술 (2026-07-16 — 사용자 요청)
알림마다 **왜 선정됐는지** 자연어 서술을 스캔 시점에 생성·저장
(`_build_reason`, Migration 0100 `reason` 컬럼):
- 최근 7일 평균 vs 기준 (±값·%)
- CUSUM 최대가 임계를 넘은 값 + **최초 초과 날짜** (cusum_series 역산)
- 현재 누적이 임계 미만이면 "완화된 상태" 명시
- 상승 추세(일평균) + 분석 일수 + 누수 해석 안내 1문장
- 수치는 전부 CUSUM 엔진 산출값 — 생성 수치 없음 (Zero-Hallucination)
- 구 데이터(reason null)는 프런트가 저장 수치로 요약 폴백

### 함께 교정된 데이터 버그
기존엔 `cusum_value` 에 **cusum_current**(리셋 후 0 가능)를 저장 —
"CUSUM 0.00 인데 누수의심" 혼란의 근원. 판정 근거값 **cusum_max** 로 교정
(구 행 6건은 0 그대로 — 사유 폴백 문구로 흡수).

## UI
`leak-alerts/page.tsx` 행 확장 시: **선정 사유** 문단 → CUSUM 값(=max)·
임계 H·Baseline 3지표 → 확인 이력. 필터 미확인/확인 완료/전체.

## 상단 통계 (2026-07-19 — "그래프로 통계나 직관적 표현")

`LeakAlertStats` — 목록 위 3분할 요약. **탭 필터와 무관하게 전체 알림 기준**
(페이지가 전체 목록을 별도 fetch):
- **KPI**: 미확인(red)·확인 완료(emerald)·최근 7일 신규 — CountUp 숫자
- **최근 14일 발생 추이**: 일자별 스택 바 (미확인 red / 확인 emerald)
- **CUSUM 임계 초과율 Top 5**: 블록별 `cusum/threshold` 배율 가로 바 —
  같은 블록 복수 알림은 **최대 배율 1행으로 집계** (label 이 전체 명칭,
  미확인 포함 시 red). 배율↑ = 누수 가능성↑ 를 한눈에
- 기존 "시설별 알림" 뱃지 카드는 본 통계로 대체
