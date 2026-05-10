# EPANET 시뮬 vs 실측 유량 차이 분석 사양 (B-2)

> **상태:** 초안 v1 (2026-05-10)
> **목적:** 시설별로 시뮬 유량과 실측 유량을 대조해 모델 정합성·이상 신호를
> 시각화한다. 누수의심 (압력 기반) 의 유량판 자매 분석.
> **관련 사양:** `docs/epanet-flow-injection-spec.md` (B-1 — 인프라),
> `docs/epanet-menu-spec.md` (메뉴), `docs/gis_plan.md`

---

## 1. 배경 / 위치

### 1.1 누수의심 분석 (기존, Phase 3.3b)

매핑된 **압력 센서별** 로 실측 vs 시뮬 압력 차이를 비교 → 의심 노드 노출.

### 1.2 본 사양 (B-2)

매핑된 **시설별 (배수지·가압장·블록)** 로 실측 vs 시뮬 **유량** 차이를 비교
→ 차이 큰 시설 노출. 차이의 의미:

| 패턴 | 해석 |
|------|------|
| 실측 ≫ 시뮬 | 모델이 demand 를 과소 추정 / 미반영 신규 수요 / 분기 누락 |
| 실측 ≪ 시뮬 | 시설 가동률 저하 / 실측 센서 이상 / 누수 (정반대 위치) |
| 실측 ≈ 시뮬 (±5%) | 모델 정합성 양호 — 이 시설 기준으로 nearby 분석 신뢰 가능 |

### 1.3 B-1 과의 관계

- **B-1** (실측 주입) 적용 후 → 시뮬이 실측에 fitting 되므로 차이가 줄어듦
- **남는 차이** = (a) demand 매핑이 안 된 다른 시설 + (b) 모델 토폴로지 결함
  + (c) 실제 운영 이상
- → B-2 는 B-1 의 효과를 검증하면서 동시에 모델 결함을 식별

---

## 2. 데이터 모델

신규 테이블 없음. 다음 기존 자산 사용:

| 자산 | 역할 |
|------|------|
| `tb_epanet_facility_flow_map` (B-1) | 시설 ↔ 실측 태그 매핑 |
| `tb_epanet_simulation_result.result_data` | 시뮬 link 별 flow_lps |
| `tb_tag_raw_data` | 실측 시계열 |

---

## 3. 백엔드 API

### 3.1 GET `/epanet/flow-deviation`

**요청 파라미터:**

| 이름 | 타입 | 기본 | 설명 |
|------|------|------|------|
| `region` | str | "R01" | 멀티테넌시 키 |
| `hours` | int | 1 | 실측 평균 윈도우 (1~24) |
| `threshold_pct` | float | 10 | 의심 임계 (%, 기본 10 = ±10%) |
| `min_flow_lps` | float | 1.0 | 무시 임계 — 실측·시뮬 모두 < min_flow 면 제외 (소블록 휴지) |

**응답:**

```json
{
  "items": [
    {
      "map_id": 12,
      "sitename": "신평(배)",
      "facilitytype": "배수지",
      "role": "outflow",
      "tagsn": "FT-SP-001",
      "x": 968432.1, "y": 1840923.5,
      "lng": 126.752, "lat": 36.832,
      "observed_lps": 124.8,
      "observed_count": 60,
      "sim_link_id": "L_4523",
      "sim_flow_lps": 110.2,
      "dist_to_link_m": 12.4,
      "diff_lps": 14.6,
      "diff_pct": 13.2,
      "suspicious": true,
      "direction": "observed_higher"
    }
  ],
  "total_mapped": 28,
  "suspicious_count": 7,
  "threshold_pct": 10,
  "hours": 1,
  "sim_id": 4823,
  "sim_created_at": "2026-05-10T03:00:12+09:00"
}
```

**핵심 로직:**

1. `tb_epanet_facility_flow_map` 활성 매핑 조회
2. 가장 최근 success 시뮬 (`tb_epanet_simulation_result`) 결과 로드
3. 매핑별로:
   - 실측: `tb_tag_raw_data.val` 평균 × scale → unit 환산 → LPS
   - 시뮬: 매핑 좌표에 가장 가까운 **link** 의 `flow_lps` 절댓값 (link 좌표 =
     중점). 거리 임계 50m 초과 시 `sim_flow_lps=null` + 경고.
   - `diff_lps = |observed - sim|`
   - `diff_pct = diff_lps / max(observed, sim) × 100`
   - `direction`: `observed_higher` / `sim_higher` / `match`
   - `suspicious = diff_pct > threshold_pct` AND `max(observed, sim) ≥ min_flow_lps`
4. 정렬: 의심 → diff_pct 큰 순

**제약 / 처리:**
- 실측 샘플 < 3 → `observed_lps=null`, suspicious=false (데이터 부족)
- 매핑 0건 → `items=[]`, warning 메시지
- 시뮬 결과 없음 → warning + 빈 items
- 배수지 outflow → reservoir 와 인접 송수관 link 의 flow 로 비교 (배수지는
  junction 이 아니므로 link 매칭이 자연)

### 3.2 GET `/epanet/flow-deviation/timeseries?map_id={id}&hours=24`

시설 1개 상세 — 24시간 시계열 (시뮬 [상수] vs 실측 [시간별 평균]).

응답:

```json
{
  "map_id": 12,
  "sitename": "신평(배)",
  "facilitytype": "배수지",
  "role": "outflow",
  "tagsn": "FT-SP-001",
  "sim_flow_lps": 110.2,
  "sim_created_at": "2026-05-10T03:00:12+09:00",
  "observed": [
    {"t": "2026-05-09T04:00:00+09:00", "lps": 122.4},
    {"t": "2026-05-09T05:00:00+09:00", "lps": 118.7},
    ...
  ]
}
```

비교 차트 (line: 실측, dashed line: 시뮬 baseline) 용.

### 3.3 데이터 품질 게이트 — `flow-deviation`

`docs/epanet-menu-spec.md` §3 메뉴 등록부에 추가:

```python
"flow-deviation": {
    "required": ["HAS_PIPE_NETWORK", "HAS_LIVE_FLOW"],
    "menu": "M008-4 실측 유량 차이"  # 신규
}
```

`HAS_LIVE_FLOW` 게이트는 B-1 §3.6 정의 (매핑 ≥ 5).

---

## 4. 프런트엔드

### 4.1 분석 메뉴 추가 — M008-4

`/analytics/flow-deviation` (또는 `/monitoring/flow-deviation` — 기존
누수의심과 동일 카테고리). M008-3 누수의심 다음.

`sidebar-menus.ts`:
```typescript
{
  id: "M008-4",
  label: "실측 유량 차이",
  path: "/monitoring/flow-deviation",
  dataQualityKey: "flow-deviation",
}
```

`tb_menu` INSERT (Migration 0072 — 본 사양 §6).

### 4.2 컴포넌트 — `FlowDeviationAnalysis.tsx`

기존 `LeakSuspiciousAnalysis.tsx` 패턴 그대로:

- 상단 컨트롤: hours / threshold_pct / min_flow 슬라이더 + [새로고침]
- 통계 카드 4개: 총 매핑 / 의심 시설 / 평균 |diff_pct| / 마지막 시뮬 시각
- 시설 목록 테이블 (정렬: 의심 → diff_pct):

| 열 | 형식 |
|----|------|
| 시설 | sitename + facilitytype 뱃지 |
| 실측 (LPS) | 숫자 + 샘플 수 |
| 시뮬 (LPS) | 숫자 + dist_to_link_m |
| 차이 (LPS / %) | ±값 색상 (실측 높음 = 청록, 시뮬 높음 = 주황) |
| 의심 | 뱃지 (red ≥10% / amber 5~10% / gray <5%) |
| 액션 | [GIS 보기] / [상세 차트] |

- [GIS 보기] → 메인 GIS 페이지로 이동 (deeplink ?focus=map_id) → 시설 마커
  하이라이트 + flow-deviation 토글 자동 ON
- [상세 차트] → 다이얼로그 with `/timeseries` 24h 비교 차트 (ECharts)

### 4.3 GIS 오버레이 — `GisFlowDeviationLayer.tsx`

기존 `GisLeakSuspiciousLayer.tsx` 패턴:

- circle layer: 시설 좌표 (lng, lat)
  - color expression: diff_pct
    - 0~5% → 회색
    - 5~10% → amber
    - ≥10% → red (실측 높음) / cyan (시뮬 높음)
  - radius: 6~20px (diff_pct 따라)
- symbol layer: sitename 라벨 (의심만)
- onClick → popup: 시뮬 vs 실측 / 차이 / 24h sparkline (mini)

### 4.4 GIS 토글 추가

`/monitoring/gis` 페이지의 7개 토글에 [실측 유량 차이] 추가. 마스터 비활성
영향 + `HAS_LIVE_FLOW` 게이트 미충족 시 disabled.

색상: 분홍 (`text-pink-500`) — 누수의심 (red) 과 구분.

---

## 5. 채팅 인텐트 통합

### 5.1 신규 인텐트 — `EPANET_FLOW_DEVIATION_TOP`

질문 패턴:
- "시뮬 vs 실측 유량 차이 큰 시설 보여줘"
- "실측 유량 차이 TOP 10"
- "유량 모델 정합성 확인"

응답: 표 (`graph_type=table`, 실측 / 시뮬 / 차이 / 의심), 후속 추천:
- "[GIS 에서 보기]"
- "[B-1 매핑 추가]"

intent 정의는 `slm/intents/epanet_intents.json` 추가 (실제 파일은 구현 시
확인). `example3.json` 에 질문 패턴 5건 + 응답 템플릿 등록.

### 5.2 시설 단건 인텐트 — `EPANET_FLOW_DEVIATION_FACILITY`

질문 패턴:
- "신평 배수지 유량 정합성"
- "송악1-2 블록 시뮬 vs 실측"

slot: sitename / facilitytype. 응답: 단건 시계열 차트 (`graph_type=plot`).

`memory/feedback_no_auto_alarm_link.md` 와 충돌 없음 — 본 인텐트는 알람을
조작하지 않음.

---

## 6. Migration 0072 — 메뉴 등록

```sql
-- Migration 0072: EPANET 실측 유량 차이 분석 메뉴
INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path,
                     menu_type, menu_idx, use_yn)
SELECT region, 'M008-4', '실측 유량 차이', 'M008',
       '/monitoring/flow-deviation', 'menu', 4, 'Y'
  FROM (SELECT DISTINCT region FROM tb_menu) r
ON CONFLICT (region, menu_idn) DO NOTHING;

INSERT INTO tb_epanet_menu_setting (region, menu_key, enabled)
SELECT DISTINCT region, 'flow-deviation', 'Y'
  FROM tb_epanet_menu_setting
ON CONFLICT (region, menu_key) DO NOTHING;
```

---

## 7. 운영 절차

1. **B-1 매핑 5건+ 등록** → `HAS_LIVE_FLOW` 충족 → M008-4 메뉴 활성
2. M008-4 진입 → 시설 목록 확인 → 의심 시설 클릭
3. [GIS 보기] → 위치 확인 → 인접 시설 / 관망 검토
4. 차이 원인 판정:
   - 단위·tagsn 오류 → B-1 매핑 수정
   - 실제 운영 이상 → 알람·작업관리 등록 (수동)
   - 모델 결함 → SHP / 직경 검토 (Phase 2)

---

## 8. 검증

### 8.1 단위 테스트
- 차이 계산 (실측 단위 환산 + 시뮬 link KNN)
- min_flow_lps 필터 (양쪽 < min_flow → 제외)
- 의심 정렬 (suspicious DESC, diff_pct DESC)
- direction 분류 (observed_higher / sim_higher / match)

### 8.2 통합 테스트
- 매핑 0건 → 빈 응답 + warning
- 시뮬 결과 없음 → warning
- B-1 inject 후 → diff_pct 평균이 inject 전 대비 50% 이상 감소 (시뮬이 실측에
  fitting) — fitting 검증

### 8.3 UX 검증 (Playwright)
- M008-4 페이지 — 매핑 0 시 안내 카드, 5+ 시 테이블
- GIS 토글 ON → 마커 표시 → 클릭 → 팝업
- 차이 큰 시설 [상세 차트] → 24h 차트 정상 렌더

---

## 9. 위험 / 한계

| 한계 | 설명 |
|------|------|
| 시뮬 = 정상상태 (steady-state) | 시간별 변동 반영 X. timeseries 차트의 sim 라인은 상수 (가로) |
| 실측은 시간 평균 | 단기 스파이크는 평균에 묻힘. 의심 = 지속적 차이만 |
| KNN link 매칭 부정확 | 시설이 분기점 사이에 있으면 link 선택이 모호 — `dist_to_link_m` 표시로 운영자 인지 |
| reservoir outflow | EPANET reservoir 의 link 로 우회 비교 — 직접 demand 비교 X |

---

## 10. 변경 이력

- 2026-05-10 — v1 초안 작성 (B-2 사양 수립, B-1 과 짝)
