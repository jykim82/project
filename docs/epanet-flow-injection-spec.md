# EPANET 실측 유량 주입 사양 (B-1)

> **상태:** 초안 v1 (2026-05-10)
> **목적:** EPANET 시뮬레이션의 demand 입력을 운영 SCADA 의 실측 유량으로 자동
> 갱신해 시뮬 정확도를 베이스라인 운영 데이터에 맞춘다.
> **관련 사양:** `docs/epanet-menu-spec.md`, `docs/gis_plan.md`,
> `docs/epanet-flow-deviation-spec.md` (B-2 — 본 사양과 짝)

---

## 1. 배경 / 문제

### 1.1 현재 (Phase 3.2 까지)

INP 변환 시 demand 입력 경로는 두 가지:

1. `tb_epanet_demand_point` — 운영자가 GIS·CSV 로 입력한 **고정값** (source:
   `manual` / `csv` / `facility`)
2. `default_demand_lps` — 입력이 없는 노드의 기본값 (현재 0.1 LPS)

→ 시뮬 결과는 입력 시점의 정적 데이터로 굳어진다. 실제 운영 (배수지 출수량,
가압장 토출량) 이 시간에 따라 변해도 시뮬은 따라가지 않음.

### 1.2 문제

- 누수 의심 / 헤드손실 이상 / 밸브 영향 분석은 모두 시뮬 결과 위에서 동작.
  시뮬이 현실과 다르면 분석 결과의 신뢰도도 떨어진다.
- 운영자가 demand 를 매일 수동으로 갱신하는 것은 비현실적 (배수지 30개,
  블록 100+).

### 1.3 목표

- **자동화**: 시설별 실측 유량 (cmh / lps) → 가장 가까운 EPANET junction 의
  demand 로 일괄 주입.
- **주기성**: 시뮬 cron (이미 운영 중인 `/sim/cron`) 직전에 demand 자동 갱신.
- **수동 fallback**: 자동 매핑이 안 된 시설은 운영자가 GIS 에서 추가 가능.
- **출처 보존**: source=`live` 로 표기 (manual/csv/facility 와 구분).

---

## 2. 데이터 모델

### 2.1 신규 테이블 — `tb_epanet_facility_flow_map`

배수지·가압장·블록의 **출수량 측정 태그** 와 **시설 좌표** 매핑.

```sql
CREATE TABLE tb_epanet_facility_flow_map (
  map_id        BIGSERIAL PRIMARY KEY,
  region        VARCHAR(10)      NOT NULL,
  sitename      VARCHAR(50)      NOT NULL,           -- tb_tag_info.sitename
  facilitytype  VARCHAR(30)      NOT NULL,           -- 배수지 / 가압장 / 소블록 / 소소블록
  role          VARCHAR(20)      NOT NULL,           -- 'outflow' / 'inflow'
  tagsn         VARCHAR(100)     NOT NULL,           -- tb_tag_info.tagsn (FK 강제 X — 외부 시스템)
  unit          VARCHAR(20)      NOT NULL,           -- 'cmh' / 'lps' / 'm3h' / 'lpm'
  scale         DOUBLE PRECISION NOT NULL DEFAULT 1, -- raw → 표준 단위 곱
  x             DOUBLE PRECISION NOT NULL,           -- 시설 좌표 (EPSG:5186)
  y             DOUBLE PRECISION NOT NULL,
  enabled       CHAR(1)          NOT NULL DEFAULT 'Y',
  notes         TEXT,
  created_at    TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
  created_by    VARCHAR(50)      NOT NULL,
  CONSTRAINT chk_epanet_facility_flow_role CHECK (role IN ('outflow','inflow')),
  CONSTRAINT chk_epanet_facility_flow_unit CHECK (unit IN ('cmh','lps','m3h','lpm','m3s')),
  CONSTRAINT uq_epanet_facility_flow_region_site_role UNIQUE (region, sitename, facilitytype, role)
);

CREATE INDEX idx_epanet_facility_flow_region ON tb_epanet_facility_flow_map(region, enabled);
CREATE INDEX idx_epanet_facility_flow_xy     ON tb_epanet_facility_flow_map(region, x, y);
CREATE INDEX idx_epanet_facility_flow_tagsn  ON tb_epanet_facility_flow_map(tagsn);
```

**중복 방지**: `(region, sitename, facilitytype, role)` 유일. 한 시설의 같은
역할 (출수/유입) 은 하나의 태그만.

**기존 `tb_facility_flow_map` 와 별개**: 그 테이블은 시설 간 상하류 관계
(upstream/downstream sitename) 이며 본 사양과 무관.

### 2.2 단위 정규화

내부 표준은 **LPS** (Liters Per Second). `unit` × `scale` → LPS:

| 입력 unit | 표준화 식 |
|-----------|-----------|
| `lps`     | val × scale |
| `lpm`     | (val × scale) / 60 |
| `cmh` / `m3h` | (val × scale) × 1000 / 3600 ≈ × 0.2778 |
| `m3s`     | (val × scale) × 1000 |

운영자 입력 시 단위는 dropdown, scale 은 보통 1 (다른 값은 센서 캘리브레이션).

### 2.3 Migration

`db/migrations/0071_epanet_facility_flow_map.sql` — 위 DDL + 코멘트 +
롤백 주석.

---

## 3. 백엔드 API

### 3.1 매핑 CRUD — `/admin/epanet/facility-flow-map`

기존 `/admin/epanet/demand` (수요 점) / `/admin/epanet/meter-map` (압력 매핑)
패턴 그대로.

| Method | Path | 동작 |
|--------|------|------|
| GET    | `/admin/epanet/facility-flow-map?region=R01` | 매핑 목록 |
| POST   | `/admin/epanet/facility-flow-map` | 단건 추가 (사용자 인증 필수) |
| POST   | `/admin/epanet/facility-flow-map/csv` | CSV 일괄 업로드 |
| POST   | `/admin/epanet/facility-flow-map/auto-suggest` | 자동 제안 (아래 §3.2) |
| DELETE | `/admin/epanet/facility-flow-map/{map_id}` | 단건 삭제 |
| DELETE | `/admin/epanet/facility-flow-map/all?region=R01` | 전체 삭제 |

요청 모델:

```python
class FacilityFlowMapItem(BaseModel):
    region: str = "R01"
    sitename: str          # "신평(배)", "송악1-2(블)" 등
    facilitytype: str      # 배수지 / 가압장 / 소블록 / 소소블록
    role: str              # "outflow" | "inflow"
    tagsn: str
    unit: str              # cmh | lps | m3h | lpm | m3s
    scale: float = 1.0
    x: float; y: float     # EPSG:5186
    notes: Optional[str] = None
```

### 3.2 자동 제안 — `/admin/epanet/facility-flow-map/auto-suggest`

`tb_tag_info` 의 `datainfo` 패턴 매칭으로 시설별 outflow 태그 제안:

- **배수지 outflow**: `datainfo` 에 `유출유량` (적산이 아닌 `순시` 우선) +
  facilitytype = `배수지`
- **가압장 outflow**: `유출유량순시` + `토출` 또는 `송수`. facilitytype =
  `가압장`
- **블록 outflow**: `유량순시유량` + facilitytype IN (`소블록`,`소소블록`)
- **inflow**: `유입유량순시` 패턴 (배수지만 사용)

**좌표 추정**: 같은 sitename 의 **압력 매핑** (`tb_epanet_meter_map`) 좌표,
또는 가장 최근 INP 의 reservoir 좌표를 기본값. 운영자가 GIS 에서 보정.

응답: `{ items: [...candidates], unmapped_facilities: [...] }`. 운영자가
체크박스로 일괄 등록.

### 3.3 실측 demand 계산 — `_compute_live_demands(region, hours)`

내부 헬퍼. 매핑된 시설별로 최근 `hours` (기본 1) 의 평균 유량을 LPS 로 환산.

```python
def _compute_live_demands(region: str, hours: int = 1) -> list[tuple[float, float, float, str]]:
    """
    Returns list of (x, y, demand_lps, source_label).
    source_label: "live:신평(배):outflow"
    """
    # 1) tb_epanet_facility_flow_map 조회 (enabled='Y')
    # 2) tb_tag_raw_data 에서 tagsn 별 평균 (logtime > NOW()-hours)
    # 3) unit/scale 환산 → LPS
    # 4) inflow 는 +, outflow 는 + (junction demand 는 sink 양수)
    #    실제로 EPANET demand 양수는 nodal 출수 — 배수지 자체는 reservoir 라
    #    구분. 본 사양에선 facilitytype != '배수지' 시설을 junction demand 로 주입.
    #    배수지는 reservoir base 로 별도 사용 (Phase 4 — 본 사양 범위 밖).
    return [(x, y, lps, label), ...]
```

**중요 제약**: EPANET junction 의 demand 는 그 노드에서 빠져나가는 양 (sink).
- **블록 outflow** = 블록을 빠져나가는 출수량 = junction demand (✓)
- **배수지 outflow** = reservoir 에서 나가는 양 ≠ junction demand. 배수지는
  EPANET 모델에서 reservoir 또는 source — 본 사양 P1 에선 **블록·가압장만
  주입**. 배수지 outflow 는 검증·B-2 비교용으로만 사용.

### 3.4 INP 변환 — `inject_live` 옵션

`/admin/epanet/inp/generate` 의 GenerateRequest 에 옵션 추가:

```python
class GenerateRequest(BaseModel):
    ...
    inject_live_demand: bool = False    # True 면 실측 유량을 demand_points 에 합쳐 IDW 보간
    inject_live_hours: int = 1          # 평균 시간 (1~24)
```

처리 순서:

1. 기존 `tb_epanet_demand_point` (manual/csv/facility) 조회
2. `inject_live_demand=True` 면 `_compute_live_demands(region, hours)` 호출 →
   결과 (x, y, lps) 를 demand_points 에 **append**
3. IDW 보간으로 모든 junction 에 demand 분배 (기존 로직 그대로)
4. 결과 metadata 에 `live_injected_count` 추가

**수동 입력 우선순위**: 같은 좌표 ±10m 범위에 manual 점이 있으면 manual 우선
(live 무시). 운영자 의도 보호.

### 3.5 시뮬 cron 통합 — `/sim/cron`

기존 `/sim/cron` 에 옵션 추가 (기본 OFF — 안정화 후 ON):

```python
class SimCronRequest(BaseModel):
    region: str = "R01"
    auto_inject_live: bool = True   # True 면 시뮬 직전에 INP 재생성 (live demand 적용)
    inject_hours: int = 1
```

처리 순서 (auto_inject_live=True):

1. INP 재생성 (`generate_inp` 내부 호출, `inject_live_demand=True`)
2. wntr 시뮬 실행
3. 결과 저장 (`tb_epanet_simulation_result`)

**보수적 동작**: 매핑이 0건이면 INP 재생성 스킵하고 마지막 artifact 사용
(현재 동작 유지). 사용자 입력 손상 방지.

### 3.6 데이터 품질 게이트 — `HAS_LIVE_FLOW`

`docs/epanet-menu-spec.md` §2.2 에 신규 게이트:

| 키 | 충족 조건 |
|---|---|
| `HAS_LIVE_FLOW` | `tb_epanet_facility_flow_map.enabled='Y'` 행 ≥ 5 (region별) |

본 게이트는 B-2 (flow-deviation) 메뉴 활성화에 필요. B-1 자체는 게이트
**아님** (옵션 기능). 단, 매핑 0 → cron 자동 주입 무동작.

---

## 4. 프런트엔드

### 4.1 관리 메뉴 추가 — `/admin/epanet`

기존 `EpanetElevationInput`, `EpanetDemandInput`, `EpanetMeterMapping` 옆에
**신규 탭** `EpanetFacilityFlowMap`:

- 매핑 목록 테이블 (sitename / facilitytype / role / tagsn / unit / x / y / 사용여부)
- [자동 제안] 버튼 → `/auto-suggest` → 결과 다이얼로그 → 체크 후 일괄 등록
- [CSV 업로드] / [전체 삭제] / [추가] 버튼 (다른 탭과 동일 패턴)
- 상단 안내: "매핑이 5건 이상이면 [실측 유량 차이] 분석 메뉴가 활성화됩니다"
- 실시간 검증: tagsn 입력 시 `tb_tag_info` 조회로 datainfo·unit 미리보기

### 4.2 INP 생성 다이얼로그

`/admin/epanet` 의 [INP 생성] 버튼 → 다이얼로그에 체크박스 추가:
- ☐ 실측 유량 자동 주입 (지난 [드롭다운: 1/3/6/24] 시간 평균)
- 매핑 0건이면 disabled + "매핑 추가 후 사용 가능" 툴팁

### 4.3 시뮬 cron 설정 (관리)

`/admin/epanet` 페이지에 cron 설정 카드:
- 토글 [실측 유량 자동 주입 + 시뮬]
- 주기 (현재 cron 외부 설정 — 본 페이지에선 표시만)

상태는 `tb_comm_code(grp_cd='EPANET_CRON', comm_cd='AUTO_INJECT_LIVE')` 에
저장 (기존 EPANET 설정 패턴 follow).

---

## 5. 운영 절차 (사용자 시나리오)

1. **초기 셋업** (1회):
   - `/admin/epanet` → [실측 유량 매핑] 탭 → [자동 제안] →
     배수지 30개 / 블록 100개 중 명백한 매핑 일괄 등록
   - 좌표는 GIS 에서 시설 마커 클릭 → "좌표 복사" → 페이스트 (또는 자동 제안의
     기본값 그대로)
   - 단위 검증 (대부분 cmh — `m³/h`). 운영 데이터 표본으로 1건 확인

2. **일상 운영** (자동):
   - 시뮬 cron 시 → demand 갱신 → 시뮬 → 결과 저장
   - 운영자는 분석 메뉴에서 결과만 확인

3. **이상 발생 시**:
   - B-2 (flow-deviation) 패널에서 시설별 시뮬 vs 실측 차이 확인
   - 차이 큰 시설 → 매핑 검증 (단위·tagsn 오류) 또는 실제 누수·이상 의심

---

## 6. 구현 단계 (P1 / P2)

### P1 (본 사양 — 1차 출시)
- Migration 0071
- CRUD API + 자동 제안 + INP `inject_live_demand` 옵션
- 관리 페이지 매핑 탭
- INP 생성 다이얼로그 체크박스

### P2 (후속)
- `/sim/cron` 자동 주입 통합 (cron 외부 설정 + 토글)
- 배수지 outflow 매핑은 검증·B-2 비교용 (junction 주입은 안 함)
- CSV 템플릿 다운로드 + import 마법사

### 범위 밖
- 시계열 demand pattern (시간별 변동 패턴) — Phase 4 별도
- EPANET reservoir 의 head 동적 갱신 — Phase 4 별도

---

## 7. 검증

### 7.1 단위 테스트 (백엔드)
- `_compute_live_demands` — 단위 환산 (cmh / lpm / m3s → lps)
- 매핑 0건 fallback (기존 demand_points 만 사용)
- manual 점 ±10m 우선순위
- inflow / outflow 부호 처리 (현재 P1 은 블록·가압장 outflow 만)

### 7.2 통합 테스트
- 매핑 5건 등록 → INP 생성 (`inject_live_demand=True`) → metadata
  `live_injected_count = 5` 확인
- 시뮬 실행 → junction demand 가 실측 평균에 근사한지 (±10%)
- 매핑 enabled='N' → 주입에서 제외

### 7.3 회귀
- 기존 `inject_live_demand=False` (기본) 시 동작 변화 0
- `tb_epanet_demand_point` 의 manual 점 영향 없음
- 누수의심 / 헤드손실 / 밸브 영향 분석 결과 형식 변화 없음 (값은 변경 가능)

---

## 8. 위험 / 트레이드오프

| 위험 | 완화 |
|------|------|
| 실측 태그 단위 오류 → demand 1000배 이상 왜곡 | 자동 제안 시 unit 미리보기 + 등록 후 검증 패널 (B-2) 가 즉시 노출 |
| 매핑 좌표 오차 → IDW 가 잘못된 junction 에 주입 | KNN dist 표시 + 50m 초과 경고 |
| cron 실패 시 demand 가 stale 한 채 시뮬 | 시뮬 결과 metadata 에 `live_injected_at` 기록, 패널에 표시 |
| 통신이상 태그 → 평균 0 → 시뮬 왜곡 | `tb_tag_info` 의 통신이상 태그는 자동 제안에서 제외 + COUNT(*)<3 시 그 태그 스킵 |

---

## 9. 변경 이력

- 2026-05-10 — v1 초안 작성 (B-1 사양 수립)
