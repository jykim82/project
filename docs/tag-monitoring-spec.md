# 태그 모니터링 사양 (v1)

모니터링 그룹에 **태그 모니터링** 페이지 신규 추가. 구축의 "태그 마스터"
(`/setup/tags`) 와 동일한 태그 목록을 **운영자 관점의 실시간 감시 뷰**로 재구성한다.
태그 마스터가 *구성·편집*용이라면, 본 페이지는 *조회·감시·진단 진입*용이다.

**관련 사양**:
- `docs/slm-setup-phase-spec.md` — 태그 마스터 구축 (원본 기반)
- `docs/metric-trend-panel-spec.md` — 트렌드 패널 (트랜드 보기 연계)
- `docs/fault-category-policy.md` — 장애 분류 정책 (이상 카테고리 정합)
- `docs/trend-comparison-spec.md` — anomaly_detector z-score 알람 체계

---

## 1. 목표 · 비목표

### 목표
- 전체 태그를 한 화면에서 **현재값 + 이상 상태**와 함께 감시
- **6종 이상 카테고리**로 빠르게 문제 태그 필터링
- 컬럼별 정렬로 임의 기준(현장·유형·현재값 등) 우선순위 탐색
- 행 우클릭 → **트랜드 보기**로 즉시 이력 진단 진입 (페이지 이탈 최소화)

### 비목표 (태그 마스터와의 차이 — 제외 기능)
| 태그 마스터 (`/setup/tags`) | 태그 모니터링 (`/monitoring/tags`) |
|------------------------------|-------------------------------------|
| CSV 업로드 (`CsvUploadDialog`) | **제외** |
| CSV/엑셀 다운로드 (`handleExcelDownload`) | **제외** |
| 태그 추가 (`TagAddFormFields` + `createTag`) | **제외** |
| 태그 편집·삭제 | **제외** (조회 전용) |
| (없음) | **현재값 컬럼 추가** |
| (없음) | **이상 카테고리 필터 6종 추가** |
| (없음) | **컬럼별 정렬** |
| (없음) | **우클릭 → 트랜드 보기** |

→ 구축(adminOnly) 권한이 아니어도 **수운영자·SCADA 운영자**가 접근 가능
(메뉴 권한은 §7 참조).

---

## 2. 컬럼 구성

태그 마스터 컬럼 + **현재값 / 갱신시각 / 이상 상태** 추가.

| # | 컬럼 | 키 | 정렬 | 비고 |
|---|------|-----|------|------|
| 1 | 태그 SN | `tagsn` | ✓ | DEMO 모드 시 숨김 (마스터 동일) |
| 2 | 태그유형 | `tagtype` | ✓ | Badge |
| 3 | 현장명 | `sitename` | ✓ | |
| 4 | 시설유형 | `facilitytype` | ✓ | |
| 5 | 장비유형 | `equipmenttype` | ✓ | |
| 6 | 데이터항목 | `datainfo` | ✓ | |
| 7 | 데이터설명 | `datadesc` | ✓ | 좁은 화면 숨김 |
| 8 | 단위 | `unit` | — | |
| 9 | **현재값** | `current_value` | ✓ | 숫자 우정렬, NULL→"–" |
| 10 | **갱신시각** | `logtime` | ✓ | 상대시간(예: "3분 전") + tooltip 절대시각 |
| 11 | **이상 상태** | `anomaly_categories` | ✓ | 카테고리 Badge 0~N개. 정렬은 "이상 개수" 기준 |
| 12 | 알람 | `alarm_tag_yn` | — | Badge (마스터 동일) |

- **현재값** 출처: `tb_tag_raw_data` 최신 행 (`DISTINCT ON (tagsn) ... ORDER BY logtime DESC`).
  Digital Input 은 0/1 → 의미 라벨 매핑 가능(후속). v1 은 raw 값 + 단위 표기.
- **갱신시각**이 임계(예: 15분) 초과 시 회색 처리 + "지연" 힌트 (데이터없음/무응답과 시각 일관).

---

## 3. 이상 카테고리 필터 (6종)

기존 백엔드 감지 로직을 **재사용**한다. 신규 감지 알고리즘은 만들지 않는다.

| # | 필터 라벨 | 내부 코드 | 출처 (기존 로직) | 단위 |
|---|-----------|-----------|------------------|------|
| 1 | 센서 무응답 | `sensor_dead` | `anomaly_scan.py` data_quality `issue_type="센서무응답"` (7일 전부 val≈0) | 태그 |
| 2 | 데이터홀딩 | `data_holding` | data_quality `issue_type="데이터홀딩"` (flat% 임계 초과) | 태그 |
| 3 | 데이터없음 | `data_missing` | data_quality `issue_type="데이터없음"` (7일 무수집) | 태그 |
| 4 | 교차검증 이상 | `cross_invalid` | `anomaly_detector.py` `cross_status` (피어 시설 불일치) | 시설→태그 전파 |
| 5 | 네트워크단절 | `network_down` | `anomaly_scan.py` A소스(`tb_network_status` is_alive=false) → 기기 매핑 태그(`tb_equipment_tag_map`) | 기기→매핑 태그 |
| 6 | 물수지 불균형 | `flow_imbalance` | `flow_balance.py` (시설 유입/유출 불균형) | 시설→태그 전파 |

### 3.0 제외: 설비고장·전원이상·통신이상 (DI 발) — v1.2 제거
- 기존 9종 중 `equip_fault`(설비고장)/`power_fault`(전원이상)/`comm_error`(통신이상)
  3종은 `anomaly_scan.py` **B소스**(DI val=1) 기반으로, 트리거 DI 신호 태그가
  속한 **현장+시설 전체 측정 태그 중 알파벳 상위 5건**에 전파됐다.
- 문제: ① 아날로그·디지털이 섞이고 ② 기기 단위가 깨지며 ③ 임의 5건이라
  같은 시설인데 표시가 들쭉날쭉. → **오해·노이즈**.
- DI 장애 신호는 **트리거 DI 태그 자체 행**(예: `…VVA_N004 밸브 FAULT`,
  현재값=1 + `알람` 컬럼)에서 직접 확인되므로 측정 태그 전파는 정보 추가가 아님.
- 향후 "기기 단위 설비고장"이 필요하면 `tb_equipment_tag_map` 기반으로
  재설계 후 별도 재도입 (현재 미계획).

### 3.1 카테고리 적용 단위 차이 (중요)
- **태그 단위** (1·2·3): 해당 태그에 직접 부여.
- **기기 매핑** (5 네트워크단절): 장애 기기에 매핑된 태그(`tb_equipment_tag_map`)
  중 스캔 결과 교차분에 부여. (폐쇄망/로컬에선 전체 is_alive=false → 비활성.)
- **시설 단위** (4 교차검증 / 6 물수지): 시설(`sitename`+`facilitytype`) 전체에
  부여 → 그 시설에 속한 모든 태그에 전파 표시. Badge 에 "(시설)" 보조 라벨로
  태그 직접 이상과 시각 구분.

### 3.2 필터 UX
- 기존 마스터 드롭다운(현장/시설유형/태그유형/키워드)은 **유지**.
- 이상 카테고리는 **다중 선택 토글 칩**(6개) 추가. 선택 시 OR 조건
  (선택 카테고리 중 하나라도 해당하는 태그). "이상 있는 태그만" 단축 토글 제공.
- 선택 상태 URL 쿼리 반영(공유·새로고침 유지) — `?anomaly=sensor_dead,data_holding`.

### 3.3 현재값 비교 필터
- 마스터 필터 옆에 **현재값 비교** 컨트롤 추가 — 연산자 셀렉트 + 기준값 입력.
- 연산자 6종: 이상(≥, `gte`) / 이하(≤, `lte`) / 초과(>, `gt`) / 미만(<, `lt`)
  / 같음(=, `eq`) / 다름(≠, `ne`). 기본값 "비교 안 함".
- 연산자 미선택 시 입력 비활성. 입력이 숫자가 아니면 필터 미적용.
- **NULL 현재값은 어떤 연산에도 불일치** (값 없는 태그는 비교 결과에서 제외).
- 현재값은 LATERAL 조인 결과라 fast-path SQL 카운트에서 거를 수 없음 → 이상
  필터와 동일하게 **전체 조회 경로(full_scan)** 에서 Python 필터링.
- URL 동기화 비대상(이상 필터와 달리 일시 탐색 용도).

---

## 4. 정렬

- 모든 정렬 가능 컬럼(§2 ✓) 헤더 클릭 → 오름차순 → 내림차순 → 해제 3-state.
- 활성 정렬 컬럼에 ▲/▼ 아이콘. 동시 단일 컬럼 정렬(다중 정렬은 비목표).
- **서버 사이드 정렬**: 페이지네이션과 정합 위해 `sort_by`/`sort_order` 파라미터로
  백엔드 처리. 현재값/갱신시각은 NULL 후순위(NULLS LAST).
- `current_value` 는 숫자 캐스팅 정렬(`val::numeric`), `anomaly_categories` 는
  이상 개수(`COUNT`) 기준.

---

## 5. 트랜드 보기 (우클릭 컨텍스트 메뉴)

- 행 우클릭 → 컨텍스트 메뉴 표시. v1 항목: **"트랜드 보기"** (후속 항목 확장 여지).
- 동작: 선택 태그를 트렌드 스토어에 추가 후 트렌드 뷰로 진입.
  - 1안 (권장, 페이지 이탈 최소): **인라인 모달**에 `TrendChart` 렌더.
    `useTrendStore.addTagFromInfo(tagInfo)` → 모달 open → 24h 기본 기간 fetch.
  - 2안: `/trend` 페이지로 라우팅 (`addTagFromInfo` 후 `router.push("/trend")`).
- 컴포넌트: shadcn **ContextMenu** 신규 도입 필요(`@radix-ui/react-context-menu`
  미설치 — §6.3). 좌클릭 선택 → 우클릭 메뉴 표준 패턴.
- 모바일/터치: 우클릭 불가 → 행 끝 "⋯" 버튼으로 동일 메뉴 노출(폴백).

---

## 6. 구현 설계

### 6.1 백엔드 — 신규 엔드포인트 `GET /tags/monitoring`
태그 마스터(`GET /tags`)를 그대로 쓰지 않고 **현재값 + 이상 상태 조인** 전용
엔드포인트 신설(마스터 엔드포인트 오염 방지, SRP).

**쿼리 파라미터**
```
sitename, facilitytype, tagtype, keyword   # 마스터와 동일
anomaly        # CSV: sensor_dead,data_holding,... (OR 필터)
only_anomaly   # bool — 이상 있는 태그만
value_op       # gte|lte|gt|lt|eq|ne — 현재값 비교 연산자 (§3.3)
value          # float — 비교 기준값 (value_op 와 함께)
sort_by        # tagsn|tagtype|sitename|...|current_value|logtime|anomaly_count
sort_order     # asc|desc
page, page_size
```

**응답 (행)**
```jsonc
{
  "tagsn": "...", "tagtype": "...", "sitename": "...", "facilitytype": "...",
  "equipmenttype": "...", "datainfo": "...", "datadesc": "...", "unit": "...",
  "alarm_tag_yn": "Y",
  "current_value": 12.3,        // nullable
  "logtime": "2026-06-11T...",  // nullable
  "anomaly_categories": [        // 0~N
    { "code": "data_holding", "label": "데이터홀딩", "scope": "tag", "detail": "24h flat 95%" },
    { "code": "flow_imbalance", "label": "물수지 불균형", "scope": "facility" }
  ]
}
```

**구현 방식**
1. `tb_tag_info` 필터 쿼리 (마스터 재사용).
2. 현재값: 결과 tagsn 집합에 대해 `DISTINCT ON (tagsn) val, logtime
   FROM tb_tag_raw_data WHERE tagsn = ANY(%s) AND logtime >= now()-interval '1 day'
   ORDER BY tagsn, logtime DESC` (TimescaleDB 하이퍼테이블, dashboard.py:274 패턴).
3. 이상 상태: **5분 anomaly_scan 캐시 재사용**으로 tag→categories 맵 구성.
   - `data_quality_issues` (per tagsn) → sensor_dead/data_holding/data_missing
   - `equipment_failure_impacts` A소스(기기 매핑) → network_down
     (B소스 DI 발 equip_fault/power_fault/comm_error 는 `_ANOMALY_CODES` 미포함 → 자동 스킵, §3.0)
   - `cross_status` (per 시설) → cross_invalid, 시설 태그 전파
   - flow_balance (per 시설) → flow_imbalance, 시설 태그 전파
4. 필터/정렬/페이지네이션 적용 후 반환.
   - 이상 카테고리 필터·이상개수 정렬은 캐시 맵 의존 → 후보 집합이 작지 않으면
     캐시 조인 후 메모리 정렬. (태그 수 수천 규모 가정 — 허용. 대규모 시 캐시를
     임시 테이블 조인으로 전환하는 후속 최적화 여지.)

성능: 캐시 미준비(부팅 직후) 시 `anomaly_categories=[]` 빈 배열 + 헤더로
"이상 분석 준비중" 안내. 폴링 주기는 마스터 페이지 수준(과도한 재조회 금지).

### 6.2 프론트 — 페이지·컴포넌트
- 페이지: `src/app/(dashboard)/monitoring/tags/page.tsx`
- API: `src/lib/api/tag-api.ts` 에 `fetchTagMonitoring(params)` 추가
  (마스터 `fetchTags` 와 분리). 타입 `src/lib/types/tag.ts` 에
  `TagMonitoringRow`, `AnomalyCategory`, `TagMonitoringQueryParams` 추가.
- 테이블: `src/components/monitoring/TagMonitoringTable.tsx` 신규
  - shadcn `Table` 기반, 정렬 헤더 + 현재값/갱신시각/이상 Badge 렌더
  - 우클릭 `ContextMenu` 래핑
- 필터: 마스터 드롭다운 재사용 + `AnomalyFilterChips` 신규(9 토글 칩)
- 트랜드 모달: `TrendChart`(`src/components/trend/TrendChart.tsx`) 인라인 렌더
  + `useTrendStore.addTagFromInfo`

### 6.3 의존성
- `@radix-ui/react-context-menu` 신규 설치 + shadcn `context-menu.tsx` 추가.
  - **폐쇄망 제약**: 빌드 타임 npm 의존 추가는 허용(런타임 외부 호출 없음).
    설치 후 `package-lock.json` 커밋, 오프라인 빌드 가능 확인.

### 6.4 메뉴 등록
- `sidebar-menus.ts` M003(모니터링) children 에 추가:
  `{ id: "M003-11", label: "태그 모니터링", path: "/monitoring/tags" }`
- `tb_menu` INSERT 마이그레이션 (pmenu_idn = M003, app_path=`/monitoring/tags`).
- `tb_auth_menu` 권한: 수운영자·SCADA·관망 운영자 접근 (구축 adminOnly 아님).

---

## 7. 권한 · 멀티테넌시
- 본 페이지는 **조회 전용** → 일반 운영 권한 노출(태그 마스터의 adminOnly 와 구분).
- 모든 쿼리 `region` 필터 유지(멀티테넌시). 현재값·이상 캐시도 region 격리.

## 8. 다국어 · 표기
- UX 텍스트 한국어 1차, 하드코딩 회피(향후 i18n). 날짜 YYYY-MM-DD,
  상대시간 한국식("3분 전"). 숫자 한국식 천단위 구분.

---

## 9. 구축 계획 (Phase)

### Phase 1 — 기본 조회 + 현재값 + 정렬 (MVP) ✅ 완료 (2026-06-11)
1. 백엔드 `GET /tags/monitoring` — 마스터 필터 + 현재값 조인 + 서버 정렬/페이지네이션
   (이상 카테고리 **제외**, 빈 배열 반환).
2. 프론트 페이지 + `TagMonitoringTable`(현재값/갱신시각 컬럼 + 컬럼 정렬).
3. 메뉴 등록(sidebar + tb_menu 마이그레이션).
4. 검증: 현재값 정확성(대시보드 값과 대조), 정렬 3-state, region 격리.

> **성능**: 현재값 조인은 `DISTINCT ON` CTE(7일 윈도우 전량 스캔, ~8.4s) 대신
> `LEFT JOIN LATERAL`(태그별 `idx_tag_raw_tagsn_time` 인덱스 시크 + `LIMIT 1`)로
> 구현 — 기본 조회 ~12ms, 현재값 정렬 최악 ~31ms. PostgreSQL 은 loose index
> skip-scan 이 없어 LATERAL 이 정석.

### Phase 2 — 이상 카테고리 필터 6종 ✅ 완료 (2026-06-11)
1. 백엔드 anomaly_scan 캐시 → tag→categories 맵 구성 + `anomaly`/`only_anomaly`
   필터 + 이상개수 정렬. (`init_tags` 에 scan/balance 캐시 getter 주입,
   `anomaly_ready` 플래그로 캐시 미준비 안내.)
2. 프론트 `AnomalyFilterChips` + 이상 Badge 컬럼 + URL 쿼리 동기화.
3. 검증: 9 카테고리 각각 알려진 케이스로 매칭 확인(시설 단위 전파 포함).
   Playwright — `센서 무응답` 칩 클릭 시 2,700→62건, URL `?anomaly=sensor_dead`,
   요청 `anomaly=sensor_dead` 반영 확인.

### Phase 3 — 트랜드 보기 컨텍스트 메뉴
1. `@radix-ui/react-context-menu` + shadcn `context-menu.tsx` 추가.
2. 우클릭 메뉴 + 트랜드 인라인 모달(`TrendChart` + `addTagFromInfo`).
3. 모바일 "⋯" 폴백.
4. 검증: 우클릭→모달 렌더, 기간 토글, 다중 행 연속 진입.

### Phase 4 — 다듬기 (선택)
- DI 현재값 의미 라벨(0/1→정상/고장), 갱신 지연 시각 강조, 자동 새로고침 토글,
  이상 카테고리 요약 KPI 바.

**검증 공통**: 각 Phase 완료 후 Playwright 6회 반복 시나리오 + 기존 마스터
페이지 회귀 확인. 산출물은 `tmp/`.

---

## 10. 변경 이력
- 2026-06-11 v1 — 초안. 태그 모니터링 페이지 사양 + 4 Phase 구축 계획.
- 2026-06-11 v1.1 — Phase 1·2 완료. 현재값 조인 LATERAL 성능 개선(8.4s→~12ms),
  이상 카테고리 9종 필터 + Badge 컬럼 + URL 동기화 구현·검증.
- 2026-06-11 v1.2 — 이상 카테고리 9→6종. DI 발 설비고장·전원이상·통신이상
  제거 (시설 전체 임의 5건 전파 → 아날로그/디지털 혼재·기기 단위 깨짐·노이즈.
  신호는 트리거 DI 태그 자체 행에서 확인 가능). §3.0 참조.
- 2026-06-11 v1.3 — 현재값 비교 필터 추가 (§3.3). 연산자 6종(이상/이하/초과/
  미만/같음/다름) + 기준값. NULL 불일치, full_scan 경로 Python 필터링.
