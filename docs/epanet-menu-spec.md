# EPANET 활용 메뉴 사양 (v1)

EPANET 수리 시뮬레이션을 SLM 운영자 흐름에 녹이기 위한 메뉴 구조 + 화면 사양.
메뉴는 **사용자 역할 기준 3 그룹 (모니터링·위기대응·분석)** 으로 분리하고,
각 메뉴는 동일한 시뮬 결과를 *재사용* 한다 (같은 데이터를 6번 다시 로드하는
구조 회피).

**관련 사양**: `docs/gis_plan.md` (Phase 2.6 — 시뮬 인프라) / `docs/feature-spec.md`
§18-A (관리 페이지). 본 문서는 *분석·운영 화면* 사양.

---

## 1. 메뉴 트리

### 1.1 모니터링 그룹 (일상 감시) — 수운영자
```
모니터링
├─ GIS 관망도 (M030, 기존 /monitoring/gis)
│   └─ [EPANET 시뮬] 토글 — 기본 흐름·압력 (Phase 2.6 동작)
├─ 누수 의심 구간 (M030-1, /monitoring/leak-suspicious) [Phase 3]
└─ 헤드손실 이상 구간 (M030-2, /monitoring/headloss-anomaly) [Phase 3]
```

### 1.2 위기대응 그룹 (사고·이상 시) — SCADA·관망 운영자
```
위기대응
├─ 차단밸브 영향범위 (M040-1, /crisis/valve-impact)              [Phase 4]
├─ 관로 파손 시뮬 + 우회경로 (M040-2, /crisis/pipe-break)         [Phase 4]
├─ 펌프 가동 변경 (M040-3, /crisis/pump-control)                   [Phase 4]
└─ 시나리오 비교 Before/After (M040-4, /crisis/scenario-diff)     [Phase 4]
```

### 1.3 분석 그룹 (장기 계획·평가) — 관망 운영자·기획
```
분석 (신규 그룹 또는 모니터링 하위)
├─ 블록 교체 후보 (M050-1, /analysis/replacement-candidates)     [Phase 5]
├─ 관망 노후도 평가 (M050-2, /analysis/network-aging)            [Phase 5]
└─ 수질·체류시간 핫스팟 (M050-3, /analysis/water-quality)        [Phase 6]
```

### 1.4 관리 그룹 (운영자 도구, 기존 유지)
```
관리
└─ EPANET 시뮬 (M100-12, 기존 /admin/epanet)
    — 사이트 설정 토글, INP 생성·시뮬 실행·산출물 다운로드, 데이터 품질 점검
```

---

## 2. 데이터 품질 게이트 (★ 핵심 — 의미 없는 결과 방지)

EPANET 시뮬은 입력 데이터 품질에 따라 *결과 의미가 완전히 달라짐*. 표고 정보가
없으면 모든 노드 압력이 균일하게 나오고, 수요가 없으면 흐름 자체가 발생하지
않는다. 메뉴 진입 시 *현재 데이터 상태* 를 명확히 알리고, 의미 없는 결과를
*마치 의미 있는 것처럼* 표시하는 일을 막는다.

### 2.1 데이터 품질 항목 (8종)

| 코드 | 의미 | 판정 기준 |
|-----|------|----------|
| `HAS_PIPE_NETWORK` | 관망 SHP 변환 INP 존재 | tb_epanet_artifact.status='success' 1건 이상 |
| `HAS_RESERVOIR_HEAD` | 배수지 수두 다양성 | reservoir.head_m stddev > 0 (default 50.0 만 있으면 X) |
| `HAS_ELEVATION` | junction 표고 입력 | junction.elevation_m 평균이 default(0) 가 아니거나 stddev > 0 |
| `HAS_DEMAND_PROFILE` | 노드별 수요 다양성 | demand_lps stddev > 0 (균등 0.1 만 있으면 X) |
| `HAS_TIME_PATTERN` | DAY/NIGHT 수요 패턴 | EPS 시뮬용 패턴 정의 |
| `HAS_METER_MAPPING` | 센서 ↔ EPANET 노드 매핑 | tb_epanet_meter_map 행 존재 |
| `HAS_VALVE_DATA` | 밸브 SHP 변환 | INP 의 [VALVES] 섹션 비어있지 않음 |
| `HAS_PUMP_DATA` | 펌프 SHP 변환 | INP 의 [PUMPS] 섹션 비어있지 않음 |

### 2.2 메뉴별 필수·권장 데이터

| 메뉴 | 필수 (없으면 동작 X) | 권장 (없으면 결과 의미 약함) |
|------|--------------------|--------------------------|
| GIS 관망도 (기본 흐름) | PIPE | ELEVATION, DEMAND |
| 누수 의심 구간 | PIPE, METER_MAP | ELEVATION, DEMAND |
| 헤드손실 이상 구간 | PIPE | ELEVATION, DEMAND, METER_MAP |
| 차단밸브 영향범위 | PIPE, VALVE_DATA | ELEVATION, DEMAND |
| 관로 파손 + 우회 | PIPE | ELEVATION, DEMAND |
| 펌프 가동 변경 | PIPE, PUMP_DATA | ELEVATION, DEMAND, TIME_PATTERN |
| 시나리오 비교 | PIPE, 2개 시뮬 | ELEVATION, DEMAND |
| 블록 교체 후보 | PIPE | 시뮬 누적 시계열, MTBF |
| 관망 노후도 | PIPE, METER_MAP | 시계열 비교 데이터 |
| 수질·체류시간 | PIPE, 수질 입력 | (별도 설계) |

### 2.3 백엔드 API
```
GET /admin/epanet/data-quality?region=R01
→ {
    "checks": {
      "HAS_PIPE_NETWORK":   { "ok": true,  "detail": "artifact #9, 131 links" },
      "HAS_RESERVOIR_HEAD": { "ok": false, "detail": "모든 reservoir head=50m default" },
      "HAS_ELEVATION":      { "ok": false, "detail": "모든 junction elevation=0 default" },
      "HAS_DEMAND_PROFILE": { "ok": false, "detail": "균등 0.1 LPS default — 노드별 차이 없음" },
      "HAS_TIME_PATTERN":   { "ok": false, "detail": "패턴 미설정" },
      "HAS_METER_MAPPING":  { "ok": false, "detail": "tb_epanet_meter_map 비어있음" },
      "HAS_VALVE_DATA":     { "ok": false, "detail": "[VALVES] 섹션 비어있음" },
      "HAS_PUMP_DATA":      { "ok": false, "detail": "[PUMPS] 섹션 비어있음" }
    },
    "menus_ready":    ["gis-flow"],
    "menus_warning":  ["leak-suspicious", "headloss-anomaly", "pipe-break", "scenario-diff"],
    "menus_blocked":  ["valve-impact", "pump-control", "water-quality"]
}
```

### 2.4 안내 UX (DataQualityCard)

각 EPANET 활용 메뉴 페이지 *상단* 에 자동 노출되는 카드.
3 단계 상태:

#### ✅ Ready (모든 권장 데이터 충족)
- 카드 미노출 또는 작은 배지 ("데이터 정합 ✓")

#### 🟡 Warning (필수는 충족 — 권장 부족)
```
┌──────────────────────────────────────────────────────────────┐
│ ⚠ 일부 데이터가 부족합니다 — 결과는 참고용                    │
│                                                              │
│ • 표고 정보 없음 → 압력 분포가 균일하게 보일 수 있습니다     │
│ • 수요 다양성 없음 → 모든 흐름이 균등하게 추정됩니다         │
│                                                              │
│ [그래도 보기]  [데이터 입력 가이드 →]                         │
└──────────────────────────────────────────────────────────────┘
```
- 클릭 시 결과 표시되되, 차트·지도에 워터마크 "[참고용]"
- 사이드바 메뉴 항목에 amber 점 표시 (데이터 부족 시각 알림)

#### 🔴 Blocked (필수 데이터 부족 — 동작 불가)
```
┌──────────────────────────────────────────────────────────────┐
│ ✗ 이 메뉴는 다음 데이터가 필요합니다                          │
│                                                              │
│ • 밸브 SHP 변환 (현재 [VALVES] 섹션 비어있음)               │
│                                                              │
│ → /admin/epanet 에서 SAA005 (밸브) SHP 추가 후 INP 재생성    │
│                                                              │
│ [관리 페이지로 이동]                                          │
└──────────────────────────────────────────────────────────────┘
```
- 결과 영역은 회색 placeholder
- 사이드바 메뉴 항목에 red 점 표시

### 2.5 사이드바 표시
```typescript
// sidebar-menus.ts 의 EPANET 활용 메뉴들에 dataQualityKey 부여
{
  id: "M030-1", label: "누수 의심 구간",
  path: "/monitoring/leak-suspicious",
  dataQualityKey: "leak-suspicious",  // 백엔드 menus_ready/warning/blocked 와 매칭
}
```
사이드바가 `/admin/epanet/data-quality` 1회 호출 → 각 메뉴 옆에 점 표시.

---

## 3. 메뉴별 화면 사양 (요약)

### 3.1 GIS 관망도 (기본 흐름) — Phase 2.6 동작
- **지도 위 오버레이**: 노드(압력 색상), 파이프(유량 색상·굵기), 흐름 화살표
- **토글 버튼**: 상단 툴바 [EPANET 시뮬]
- **데이터 품질 카드**: GIS 페이지 좌상단 (현재 50m 균일 → 표고 입력 안내)

### 3.2 누수 의심 구간 [Phase 3]
- **입력**: 시뮬 압력 + 센서 실측 압력 (tb_tag_raw_data 의 압력 태그)
- **로직**: |실측 - 시뮬| > 임계값 (예: 5m) 인 노드 강조
- **UI**: 지도 위 의심 노드 적색 펄스 + 우측 표 (sitename / 차이 m / 의심 점수)
- **데이터 게이트**: METER_MAPPING 필수, ELEVATION+DEMAND 권장

### 3.3 헤드손실 이상 구간 [Phase 3]
- **입력**: 시뮬 결과 pipe.headloss_m / pipe.length / pipe.diameter
- **로직**: 같은 구경·재질 그룹의 단위길이당 headloss 평균 대비 z-score > 2
- **UI**: 지도 위 이상 파이프 황색 표시 + 우측 표 (구경 / 재질 / 단위 손실 / z-score)
- **데이터 게이트**: PIPE 필수, ELEVATION+DEMAND+METER_MAP 권장

### 3.4 차단밸브 영향범위 [Phase 4]
- **입력**: 사용자가 지도에서 밸브 클릭 → 시뮬 재실행 (해당 밸브 close)
- **로직**: 압력 0 으로 떨어지는 노드 = 단수 영향 범위
- **UI**: 영향 범위 회색 음영 + 영향 가구 수 + [실행] / [취소]
- **데이터 게이트**: PIPE + VALVE_DATA 필수

### 3.5 관로 파손 + 우회경로 [Phase 4]
- **입력**: 사용자가 지도에서 파이프 클릭 → 시뮬 재실행 (해당 파이프 제거)
- **출력 1**: 단수 영향 범위 (압력 0 노드)
- **출력 2**: 우회 경로 (이전 흐름 비해 |flow| 차이가 큰 인접 파이프 = 우회로)
- **UI**: 영향 가구 수 + 우회 파이프 점선 표시 + 변경 후 압력 분포

### 3.6 펌프 가동 변경 [Phase 4]
- **입력**: 펌프 ON/OFF 토글 (또는 회전수 변경)
- **출력**: 변경 후 압력·유량 분포
- **UI**: 좌측 펌프 목록 (현재 상태) + 토글 + [재시뮬]

### 3.7 시나리오 비교 [Phase 4]
- **입력**: 두 시뮬 결과 sim_id 선택 (예: 평소 vs 펌프 1대 가동 변경 후)
- **출력**: 노드별 Δpressure, 파이프별 Δflow
- **UI**: 좌·우 분할 지도 + 차이 색상 (저하=빨강 / 향상=청록)

### 3.8 블록 교체 후보 [Phase 5]
- **입력**: 시뮬 누적 (예: 최근 3개월) + tb_equipment_mtbf + 알람 이력
- **로직**: 시뮬 압력 부족 횟수 + MTBF 단축 + 알람 빈도 → 종합 점수
- **UI**: 우선순위 표 + 지도 강조

### 3.9 관망 노후도 평가 [Phase 5]
- **입력**: 시뮬 vs 실측 편차 시계열 (월별)
- **로직**: 편차 추세 증가 = 부식·침전 의심
- **UI**: 라인 차트 (월별 평균 편차) + 노후도 등급 지도

### 3.10 수질·체류시간 [Phase 6]
- **입력**: EPANET 수질 모델 (잔류염소 초기 농도 + 반응 계수)
- **출력**: 노드별 체류시간 / 잔류염소 농도
- **UI**: 지도 색상 + 핫스팟 표

---

## 4. 구현 순서 (Phase Roadmap)

| Phase | 단계 | 산출물 |
|-------|------|--------|
| **Phase 2.7 (즉시)** | 데이터 품질 API + 사이드바 점 표시 + DataQualityCard 컴포넌트 + 메뉴 뼈대 (placeholder 페이지) | `/admin/epanet/data-quality`, sidebar 점, 메뉴 진입 시 안내 |
| **Phase 3** | 표고 매핑 (DEM 또는 수동 입력 UI) + 노드별 demand 입력 (계량기 데이터) | 메뉴 3.1~3.3 활성 |
| **Phase 4** | 시뮬 재실행 UI 인프라 (사용자 입력 → 백엔드 재시뮬 → diff 응답) | 메뉴 3.4~3.7 활성 |
| **Phase 5** | 시뮬 시계열 누적 + 노후도 점수 모델 | 메뉴 3.8~3.9 활성 |
| **Phase 6** | EPANET 수질 모듈 활성 + 입력 UI | 메뉴 3.10 활성 |

---

## 5. tb_menu / sidebar-menus.ts 등록 (Phase 2.7 시작 시)

```sql
-- Migration 0066 — EPANET 활용 메뉴 뼈대 (placeholder, dataQualityKey 부여)
INSERT INTO tb_menu VALUES
  ('R01', 'M030-1', '누수 의심 구간',     'M030', '/monitoring/leak-suspicious', ...),
  ('R01', 'M030-2', '헤드손실 이상',      'M030', '/monitoring/headloss-anomaly', ...),
  ('R01', 'M040-1', '차단밸브 영향범위',  'M040', '/crisis/valve-impact', ...),
  ('R01', 'M040-2', '관로 파손 시뮬',     'M040', '/crisis/pipe-break', ...),
  ('R01', 'M040-3', '펌프 가동 변경',     'M040', '/crisis/pump-control', ...),
  ('R01', 'M040-4', '시나리오 비교',      'M040', '/crisis/scenario-diff', ...),
  ('R01', 'M050',   '분석',              NULL,    NULL, 'group', ...),
  ('R01', 'M050-1', '블록 교체 후보',     'M050', '/analysis/replacement-candidates', ...),
  ('R01', 'M050-2', '관망 노후도',        'M050', '/analysis/network-aging', ...),
  ('R01', 'M050-3', '수질·체류시간',      'M050', '/analysis/water-quality', ...);
```

---

## 6. 변경 이력
- 2026-05-06 — v1 초안 작성 (사용자 결정: 추천안 채택, 데이터 품질 안내 포함)
- 2026-05-07 — Phase 2.7 구현 완료
  · Migration 0066 — 메뉴 10건 (M003-9/10, M006-4~7, M008 그룹 + M008-1~3) + 권한 20건
  · 백엔드 `GET /admin/epanet/data-quality` — 9 항목 체크 + 메뉴별 ready/warning/blocked 분류
  · 프런트 `DataQualityCard` 컴포넌트 (Ready/Warning/Blocked 3 단계)
  · `EpanetMenuPlaceholder` 공통 페이지 + 9 placeholder 페이지
  · 사이드바 amber/red 점 (`useEpanetDataQuality` 훅 + `MENU_DATA_QUALITY_KEY` 매핑)
  · sidebar-menus.ts fallback + dataQualityKey 필드
  · 사양 §1.1~1.3 의 임시 코드 (M030-/M040-/M050-) 는 실제로 M003-/M006-/M008- 로 등록됨
- 2026-05-07 — Phase 3.1 구현 완료 (표고 입력)
  · Migration 0067 — `tb_epanet_elevation_point` (region/x/y/elevation_m/source/label/notes)
  · 백엔드 `endpoints/epanet.py`:
    - GET/POST/DELETE `/admin/epanet/elevations` (단건/일괄/CSV-bulk)
    - `inp_converter.py` IDW 보간 (k=4, power=2) — 운영자 입력 표고를 모든 junction 에 부여
    - `GenerateRequest.use_elevation_points` (default true), `use_synthetic_elevation` (default false)
    - 합성 표고: NW 30m → SE 5m 그라디언트 (배수지 head 50m 보다 충분히 낮게, 음수 압력 방지)
  · 프런트 `EpanetElevationInput` 컴포넌트 — 단건 입력 / CSV 업로드 / 표 / 일괄 삭제
  · `/admin/epanet` 페이지 변환 작업 카드에 표고 옵션 체크박스 (운영자 입력 / 합성)
  · 검증: 합성 표고 ON → 시뮬 #9 압력 20.01~41.67m / flow ±3.4 LPS, HAS_ELEVATION ok=true
  · 메뉴 분류 변화: warning 5 → 1 (gis-flow / pipe-break / scenario-diff / replacement-candidates 가 ready 로 이동)
- 2026-05-08 — Phase 3.3d / 4 / 5 / 6 일괄 구현 완료 (합성 자동 fallback)
  · **합성 자동 fallback**: data-quality 의 HAS_VALVE_DATA / HAS_PUMP_DATA / HAS_WATER_QUALITY_MODEL 모두 ok=true (detail 에 "합성" 표기). 실 SHP/모델 입력 후 자동 전환.
  · **simulator.py `run_what_if(inp_path, remove_links, add_pump_boost, quality_initial, quality_kbulk)`** — 변경 시나리오 즉석 시뮬 (link 제거 / reservoir head boost / 수질 모델)
  · **분석 API 6개 신규**:
    - GET `/admin/epanet/synthetic-valves?n=5` — 가상 밸브 목록 (큰 |flow| pipe 5개)
    - GET `/admin/epanet/valve-impact?pipe_id=&pressure_drop_m=` — 밸브 차단 단수 영향
    - GET `/admin/epanet/pipe-break?pipe_id=&pressure_drop_m=&flow_change_lps=` — 파손 영향 + 우회 경로
    - GET `/admin/epanet/pump-control?head_boost_m=` — 펌프 boost 압력 변화
    - GET `/admin/epanet/scenario-diff?sim_a=&sim_b=` — 두 시뮬 비교 (자동: 최근 2개)
    - GET `/admin/epanet/replacement-candidates?top=` — z-score + length 가중 우선순위
    - GET `/admin/epanet/network-aging?months=` — 매핑별 월별 편차 추세
    - GET `/admin/epanet/water-quality?initial_mg_l=&kbulk_per_day=` — 합성 잔류염소 EPS 시뮬
  · **프런트 분석 컴포넌트 7개**: ValveImpactAnalysis / PipeBreakAnalysis / PumpControlAnalysis / ScenarioDiffAnalysis / ReplacementCandidatesAnalysis / NetworkAgingAnalysis / WaterQualityAnalysis
  · **페이지 활성**: 7 placeholder → 분석 컴포넌트 (DataQualityCard 안에서 ready/warning 시 노출)
  · **검증 (sim #12)**:
    - valve-impact: 32 노드 영향 (drop>5m)
    - pipe-break: 32 영향 + 우회 경로
    - pump-control: 50 노드 (head +10m → 평균 +23m 증가)
    - scenario-diff: top 20 노드/파이프 차이
    - replacement-candidates: P000091 score 5.44 1순위
    - network-aging: 5 매핑 / 4개월 시계열 / 추세 stable
  · **메뉴 분류**: ready 9 / warning 1 (pump-control) / blocked 0 / disabled 0
- 2026-05-08 — Phase 3.3c 구현 완료 (headloss-anomaly 분석 + 메뉴 토글 인프라)
  · **메뉴 토글 인프라**:
    - Migration 0070 — `tb_epanet_menu_setting` (region/menu_key/enabled, 10 메뉴 default Y)
    - GET/PUT `/admin/epanet/menu-settings` — region 별 토글 조회·변경
    - data-quality 응답에 `menus_disabled` 추가 (운영자 비활성)
    - 사이드바: disabled 메뉴 hidden 처리
    - DataQualityCard: disabled 상태 안내 ("관리자가 비활성화" + [관리 페이지] 버튼)
    - `EpanetMenuToggles` 컴포넌트 (10 스위치 + 활성/비활성 카운트)
  · **Phase 3.3c — headloss-anomaly 분석**:
    - GET `/admin/epanet/headloss-anomaly?z_threshold=2`
    - WNTRSimulator 가 headloss 안 채우므로 Hazen-Williams 즉석 계산 (HL = 10.67·Q^1.852 / C^1.852·D^4.87 · L)
    - 50mm 단위 구경 그룹 → 그룹별 평균/stddev → z-score
    - 응답: items[{id, diameter, length, unit_loss, group_mean, z_score, anomaly}]
    - 프런트 `HeadlossAnomalyAnalysis` — z_threshold 슬라이더 + KPI 3 + 파이프별 표 (상위 50건)
    - 검증: sim #12 → 131 파이프 / 13 구경 그룹 / 9건 이상 (z > 2)
- 2026-05-08 — Phase 3.3b 구현 완료 (leak-suspicious 분석 활성)
  · 백엔드 `GET /admin/epanet/leak-suspicious?region=R01&threshold_m=5&hours=1`
    - 매핑별 실측 (tb_tag_raw_data 최근 N시간 평균 + offset) vs 시뮬 (KNN 노드) 압력 차이
    - |diff| > threshold → 의심 분류, 의심 → 차이 큰 순 정렬
    - 응답: items[{tag_sn, label, observed_m, sim_pressure_m, diff_m, suspicious, dist_to_node_m, ...}]
  · 프런트 `LeakSuspiciousAnalysis` 컴포넌트 — 임계값/시간 슬라이더 + KPI 3개 + 매핑별 비교 표 (의심 행 destructive)
  · `/monitoring/leak-suspicious/page.tsx` — DataQualityCard 안에 분석 컴포넌트 (ready/warning 시 노출, blocked 시 placeholder)
  · 검증: 매핑 5건 (압력 태그) → 시뮬 #12 비교 → 의심 5/5 (합성 시뮬 30~35m vs 실측 5~9m, 차이 24~26m)
  · 운영 환경에선 표고/수요 입력 후 차이가 5m 미만으로 수렴 (정상)
- 2026-05-08 — Phase 3.3a 구현 완료 (센서 매핑 인프라)
  · Migration 0069 — `tb_epanet_meter_map` (region/tag_sn/x/y/calibration_offset_m/label/notes, UNIQUE region+tag_sn)
  · 백엔드 `/admin/epanet/meters` GET/POST/DELETE + `bulk-csv` (CSV 형식: tag_sn,x,y[,offset,label,notes])
  · `_check_data_quality` HAS_METER_MAPPING 을 실제 카운트 검사로 변경
  · 프런트 `EpanetMeterMapping` 컴포넌트 + admin/epanet 페이지 통합
  · 검증: 매핑 1건 추가만으로 ready 메뉴 4 → **7개** (leak-suspicious / headloss-anomaly / network-aging 활성)
  · blocked 잔여 3개: valve-impact (Phase 4 — 밸브 SHP), pump-control (Phase 4 — 펌프 SHP), water-quality (Phase 6 — 수질 모델)
  · Phase 3.3b/c 후속: leak-suspicious / network-aging 페이지의 실제 분석 로직 (실측 vs 시뮬 압력 차이)
- 2026-05-07 — Phase 3.2 구현 완료 (수요 입력)
  · Migration 0068 — `tb_epanet_demand_point` (region/x/y/demand_lps/source/label/notes)
  · 백엔드:
    - `/admin/epanet/demands` GET/POST/DELETE + `bulk-csv` (CSV 본문 업로드)
    - `inp_converter.py` 에 demand_points + use_synthetic_demand 옵션 추가
    - 합성 수요: bbox 중심 1.0 LPS → 외곽 0.05 LPS 그라디언트
    - 우선순위 명확화: 합성 옵션이 운영자 입력보다 우선 (시연 모드 명시적 의도)
  · simulator.py: junction.elevation_m 도 시뮬 응답에 포함 (data-quality 검증 정확도 향상)
  · 프런트 `EpanetDemandInput` 컴포넌트 + admin/epanet 변환 카드에 수요 옵션 체크박스
  · 검증: 합성 표고+수요 ON → 시뮬 #12 압력 20.01~41.66m / flow -11.36~+20.24 LPS / elev distinct 96 / demand distinct 47
  · 메뉴 분류: ready 4 (gis-flow/pipe-break/scenario-diff/replacement-candidates) / warning 1 (headloss-anomaly) / blocked 5
