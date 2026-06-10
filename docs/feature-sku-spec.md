# 기능 SKU·feature flag 사양 (v1)

판매 단위 (SKU) 별 모듈 포함 여부 + 운영 중 미세 조정의 두 층위 분리.

**관련 사양**: `docs/epanet-menu-spec.md` (EPANET 메뉴 트리),
`docs/error-management.md` E-034 (메뉴 hide 정책)

---

## 1. 두 레벨 정책

| 레벨 | 결정자 | 시점 | 도구 | 끌 수 있음 / 켤 수 있음 |
|------|--------|------|------|------------------------|
| #1 **제품 SKU** (모듈 포함 여부) | 영업·설치자 | 판매·설치 시 | `tb_comm_code(SITE_SETTING.*_ENABLED)` 기본값 + 설치 seed | master 가 끄기/켜기 가능 (SKU 한도 내) |
| #2 **운영 메뉴 hide** (미세 조정) | master | 운영 중 | `tb_menu.use_yn` ( `/admin/menus` ) | master 만 |

**관계**: #1 (모듈 OFF) → 사이드바·페이지·하위 토글 모두 자동 hide.
#2 는 #1 의 한도 내 추가 hide (예: EPANET 활성화 상태에서 일부 분석 메뉴만 hide).

---

## 2. SLM 모듈 지도

### 2.1 기본 운영 (모든 SKU 공통, 분리 불가)

| 영역 | 의존 |
|------|------|
| 실시간 감시 (대시보드·트렌드·계통도) | `tb_tag_raw_data` |
| 알람 (분류별 경보·이력) | `tb_alarm_history` |
| 작업 관리 (장애·조치) | `tb_fault_action` |
| 사용자·권한·메뉴 관리 | `tb_user`, `tb_menu`, `tb_auth_menu` |
| GIS 관망도 (시설 마커) | `tb_facility` 좌표 (EPANET 토글 제외) |

### 2.2 SKU 분리 가능 모듈 (B군)

| # | 모듈 | flag 키 | 데이터 의존 | HW 의존 | 영향 메뉴 수 |
|---|------|---------|-------------|---------|--------------|
| B1 | **관망수리분석** (EPANET) | `EPANET_ENABLED` | 관로 SHP, 표고, 수요, 펌프/밸브, 미터 매핑 (구축 비용 高) | 메모리 +2.3GB/시뮬 | 11 (사이드바) + 6 (GIS 토글) |
| B2 | AI 멀티모달 진단 (Vision) | `VISION_AGENT_ENABLED` | 사진만 | **GPU 필수** (Metal/CUDA) + Ollama gemma4:26b (~19GB VRAM) | 채팅 사진 시나리오 + 설비 명판 파싱 |
| B3 | 매뉴얼·고장 케이스 RAG | `MANUAL_RAG_ENABLED` | 매뉴얼 PDF · 고장 케이스 DB seed | embeddings 모델 (CPU 가능) | 채팅 매뉴얼 인용 · 유사 사례 |
| B4 | 트렌드 비교 분석 (평소 대비 / 향후 전망) | `TREND_COMPARISON_ENABLED` | 14일+ 히스토리 (자동 누적) | 무관 | `/trend` 페이지 + 트렌드 인텐트 |
| B5 | 보고서 자동 생성 (장애 조치 / 일 점검) | `REPORT_AUTOMATION_ENABLED` | 작업·점검 기록 (기본 운영서 누적) | 무관 | M005 그룹 (2 메뉴) |

**Phase 1 (이 사양 v1) — B1 (EPANET) 만 구현**. B2~B5 는 동일 패턴으로 후속.

### 2.3 SKU 등급 (예시)

| 등급 | 포함 (B군) | 대상 |
|------|-----------|------|
| Basic | (없음) | 데이터·인프라 미구축 신규 사이트 |
| Plus | B1, B4 | 관망 데이터 구축 완료, 분석 욕구 ↑ |
| Enterprise | B1, B2, B3, B4, B5 | GPU 보유, 차별화 가치 활용 |

또는 모듈별 add-on 모델 (각 모듈 독립 구매) 도 가능 — 영업 정책에 따라.

---

## 3. EPANET 마스터 토글 (B1) — Phase 1 구현

### 3.1 데이터 모델

```
tb_comm_code:
  region: VARCHAR(10)
  grp_cd: 'SITE_SETTING'
  comm_cd: 'EPANET_ENABLED'
  use_yn:  CHAR(1)  -- 'N' = 기본 (비활성), 'Y' = master 가 활성화
```

**기본값**: `use_yn='N'` — 신규 region 추가 시 자동 비활성.

### 3.2 영향 메뉴 (12개)

#### 사이드바 자동 hide 대상 (11개) — `feature: "epanet"` 부여
| menu_idn | label | path | dataQualityKey |
|----------|-------|------|----------------|
| M003-9 | 누수 의심 구간 | /monitoring/leak-suspicious | leak-suspicious |
| M003-10 | 헤드손실 이상 구간 | /monitoring/headloss-anomaly | headloss-anomaly |
| M006-4 | 차단밸브 영향범위 | /crisis/valve-impact | valve-impact |
| M006-5 | 관로 파손 시뮬 | /crisis/pipe-break | pipe-break |
| M006-6 | 펌프 가동 변경 | /crisis/pump-control | pump-control |
| M006-7 | 시나리오 비교 | /crisis/scenario-diff | scenario-diff |
| M008-1 | 블록 교체 후보 | /analysis/replacement-candidates | replacement-candidates |
| M008-2 | 관망 노후도 평가 | /analysis/network-aging | network-aging |
| M008-3 | 수질·체류시간 | /analysis/water-quality | water-quality |
| M008-4 | 실측 유량 차이 | /monitoring/flow-deviation | flow-deviation |
| (분석 그룹 M008) | 분석 | (자식 모두 hidden 시 그룹 자체 hide — 기존 정책) | — |

#### admin 페이지 (1개) — **hide 적용** (사용자 결정 2026-06-08)
| menu_idn | label | path | menu_key | 비고 |
|----------|-------|------|----------|------|
| M100-12 | EPANET 시뮬레이션 | /admin/epanet | `epanet-admin` | 마스터 OFF 시 사이드바 hide. 복구 경로: `/admin/site-settings` (항상 노출). |

#### GIS 페이지 내부 토글 (6개) — `data-quality menus_disabled` 자동 포함
EPANET 시뮬 / 누수 의심 / 헤드손실 이상 / 밸브 영향 / 관로 파손 / 교체 후보 / 실측 유량 차이.
`isEpanetEnabled(menu_key)` 가 false 반환 → 토글 hidden.

#### EPANET 무관 (유지)
- M003-5 GIS 관망도 (시설 마커·SHP 관망 표시 — EPANET 시뮬 무관)
- M003-5 안의 [물흐름 표시] 토글 (`gis-flow-arrow`) — **2026-06-09 정책 변경**:
  데이터 source 가 EPANET 시뮬 결과 의존이므로 마스터 OFF 시 함께 hide
  (옵션 A). epanet-menu-spec.md 2026-06-09 참조.

### 3.3 동작 흐름

#### 신규 설치
1. DB seed: `tb_comm_code(SITE_SETTING.EPANET_ENABLED) use_yn='N'`
2. 첫 로그인 시 사이드바: 11 메뉴 + GIS EPANET 토글 모두 hidden
3. M100-12 (admin EPANET) 만 노출 → master 가 접근

#### master 활성화
1. `/admin/site-settings` 진입 → "관망수리분석" 토글 ON
2. `PUT /admin/site-settings { epanet_enabled: true }` → tb_comm_code use_yn='Y'
3. **무효화 + 재fetch**: `/auth/me` + `/admin/epanet/data-quality` 다시 호출
4. 사이드바: 11 메뉴 노출 (단, 데이터 품질 게이트로 ready/warning/blocked 분기)
5. GIS 페이지: EPANET 토글 노출

#### master 비활성화
1. `/admin/site-settings` 토글 OFF → DB use_yn='N'
2. 사이드바: 11 메뉴 + GIS 토글 모두 hide
3. URL 직접 진입 시: 페이지가 비활성 안내 카드 (EpanetMenuPlaceholder 유사)
4. M100-12 만 사이드바에 남아 재활성화 가능

### 3.4 복구 경로

master 가 실수로 끄거나, 신규 사이트에서 활성화 필요 시:
1. `/admin/site-settings` 진입 (사이드바 항상 노출 — adminOnly 보호)
2. **관망수리분석** 토글 ON
3. 즉시 사이드바·GIS·`/admin/epanet` 메뉴 복원

### 3.5 백엔드 변경

#### `endpoints/epanet.py`
`_check_data_quality(region)` — 응답 `menus_disabled` 생성 로직 변경:
```python
disabled = _menus_disabled(region)  # 기존: tb_epanet_menu_setting enabled='N'
if not is_enabled(region):           # 신규: 마스터 OFF 시 11 menu_key 모두 추가
    for key in _MENU_REQUIREMENTS.keys():
        # 2026-06-09: gis-flow-arrow 도 EPANET 시뮬 의존이라 포함 (예외 제거)
        if False:  # was: key == "gis-flow-arrow"
            continue
        disabled.add(key)
```

#### `endpoints/auth_crud.py` `/auth/me`
응답에 `features` 객체 추가:
```python
"features": {
    "epanet": is_enabled(region),
    # 후속 모듈도 동일 패턴
}
```

### 3.6 프론트 변경

#### `src/lib/config/sidebar-menus.ts`
```ts
export interface SidebarMenuChildDef {
  id: string;
  label: string;
  path: string;
  dataQualityKey?: string;
  feature?: string;   // 신규 — feature flag 키
}
// 11개 메뉴에 feature: "epanet" 추가
```

#### `src/hooks/use-sidebar-menus.ts`
`/auth/me.features.epanet === false` 일 때 `feature === "epanet"` 메뉴 모두 hide.

#### `src/hooks/use-epanet-data-quality.ts`
`isEpanetEnabled` 는 기존 그대로 — 백엔드 `menus_disabled` 응답이 마스터 OFF 반영됨.

#### `/admin/site-settings`
`epanet_enabled` 토글 라벨/설명을 **"관망수리분석"** + "ON 시 EPANET 시뮬 기반 11개 메뉴 + GIS 오버레이 노출. 기본값 OFF."

#### `/admin/epanet` 페이지
마스터 OFF 상태일 때 상단 큰 안내 카드 + "활성화" 버튼 (PUT site-settings 호출).

---

## 4. 후속 모듈 (B2~B5) — Phase 2 예고

### 공통 패턴 (B1 와 동일)
1. DB: `tb_comm_code(SITE_SETTING.XXX_ENABLED)` 시드 default 'N'
2. 백엔드: `is_xxx_enabled(region)` 헬퍼 + `/auth/me.features.xxx`
3. 프론트: `sidebar-menus.feature: "xxx"` + useSidebarMenus 필터
4. `/admin/site-settings` 에 토글 추가

### 모듈별 특이 사항
- **B2 Vision**: GPU 자동 감지 + 모델 다운로드 상태 표시 권장 (활성 전 사전 체크)
- **B3 RAG**: 매뉴얼 업로드 UI + embeddings 빌드 진행률 (admin/equipment-manuals 활용)
  → **Phase 1 구현 완료 (2026-06-08)** — `MANUAL_RAG_ENABLED` 마스터 토글,
    Vision Agent 가드, /admin/site-settings 카드. Migration 0078.
- **B4 트렌드 비교**: 데이터 누적 14일 후 자동 활성 옵션 (시간 게이트)
- **B5 보고서**: 자체 양식 사용 고객 위해 분리

---

## 5. B3 매뉴얼·고장 케이스 RAG 마스터 토글 — Phase 1 구현 (2026-06-08)

### 5.1 데이터 모델
- `tb_comm_code(region, 'SITE_SETTING', 'MANUAL_RAG_ENABLED').use_yn`
- 기본 'N' (Migration 0078)

### 5.2 동작
| 상태 | Vision Agent 동작 |
|------|-------------------|
| OFF (기본) | `_retrieve_manual_excerpts` + `_retrieve_fault_cases` skip → 응답 manuals_retrieved=[], fault_cases=[] |
| ON | 정상 RAG 검색 → 매뉴얼 인용 + 유사 고장 케이스 N건 응답 |

### 5.3 vision_agent.py 가드
```python
_rag_on = is_manual_rag_enabled()  # 60초 캐시
if _rag_on and equipment_guess and ...:
    manual_excerpts_raw = _retrieve_manual_excerpts(...)
if _rag_on and (equipment_type != "기타" or observed_state):
    fault_cases_raw = _retrieve_fault_cases(...)
```

### 5.4 백엔드 `/auth/me` features
```json
{ "features": { "epanet": false, "manual_rag": false } }
```
→ 프론트가 활용 (예: 채팅 응답 카드 표시 분기)

### 5.5 운영자 UX — `/admin/site-settings`
"매뉴얼·고장 케이스 RAG" 카드 추가 (purple Waves 아이콘) — ON 시 "고장 케이스
관리 페이지로 이동" 빠른 링크 노출.

### 5.6 검증 (Playwright 6회 토글)
6/6 PASS — DB use_yn ↔ /auth/me features.manual_rag 양방향 정합. 마지막 OFF
복원으로 기본 정책 유지.

### 5.7 후속 권고
- 채팅 응답 카드에 features.manual_rag=false 안내 ("RAG OFF — 매뉴얼·고장 케이스 미인용")
- 매뉴얼 PDF 업로드 + embeddings 빌드 UI (`/admin/equipment-manuals` 보강)
- B2 (Vision Agent 자체) 마스터 토글도 동일 패턴 추가 가능

---

## 6. B2 / B4 / B5 마스터 토글 — Phase 1 구현 (2026-06-10)

### 6.1 데이터 모델 (Migration 0082)

| flag 키 | use_yn 기본값 | 이유 |
|---------|---------------|------|
| `VISION_AGENT_ENABLED` (B2) | **'N'** | GPU 필수 (Metal/CUDA) + Ollama gemma4:26b ~19GB. 데이터 미구축 사이트 비활성. |
| `TREND_COMPARISON_ENABLED` (B4) | **'Y'** | 데이터 누적 자동 활성. 기존 `/trend` 페이지에 영향 적음. |
| `REPORT_AUTOMATION_ENABLED` (B5) | **'Y'** | 작업·점검 기록 누적이 전제. 기본 운영 모듈. 자체 양식 사용 고객만 OFF. |

Migration 0082 — `INSERT … SELECT DISTINCT region` 로 모든 region 동시 시드.
롤백 시 `DELETE FROM tb_comm_code WHERE grp_cd='SITE_SETTING' AND comm_cd IN
('VISION_AGENT_ENABLED','TREND_COMPARISON_ENABLED','REPORT_AUTOMATION_ENABLED')`.

### 6.2 백엔드 변경

#### `endpoints/admin.py`
`GET /admin/site-settings` 응답에 3 flag 추가 (`vision_agent_enabled`,
`trend_comparison_enabled`, `report_automation_enabled`). `PUT` 도 동일 키 수용.

`_sku_map` 으로 한국어 라벨 통일 (`AI 멀티모달 진단 (B2)` 등 — `comm_nm` 채움).

#### `endpoints/auth_crud.py` `/auth/me`
`features` 객체에 6개 flag 노출:
```json
{
  "features": {
    "epanet": false, "manual_rag": false, "alarm_popup": true,
    "vision_agent": false, "trend_comparison": true, "report_automation": true
  }
}
```
`SITE_SETTING` 쿼리는 `IN (…)` 으로 단일 호출로 묶음 (N+1 회피).

### 6.3 프론트 변경 (`/admin/site-settings`)

3 신규 카드 추가 (pink/blue/emerald 아이콘):
- **AI 멀티모달 진단 (B2)** — 사진 기반 설비 진단. GPU 필수.
- **트렌드 비교 분석 (B4)** — 평소 대비 / 향후 전망. 14일+ 히스토리 필요.
- **보고서 자동 생성 (B5)** — 장애 조치 / 일 점검 자동 양식.

`handleSkuToggle(key, current)` — generic 핸들러. 토글 후
`window.dispatchEvent('slm:features-invalidate')` 로 AlarmCrisisModal 캐시 즉시 무효화
(20절 invalidate event 재사용).

### 6.4 검증 (Playwright 6회 토글)

```
iter 1: vision_agent ON  → actual: true,  pass: true
iter 2: vision_agent OFF → actual: false, pass: true
iter 3: trend_comparison OFF → actual: false, pass: true
iter 4: trend_comparison ON  → actual: true,  pass: true
iter 5: report_automation OFF → actual: false, pass: true
iter 6: report_automation ON  → actual: true,  pass: true
```
6/6 PASS — DB use_yn ↔ /auth/me features 양방향 정합. 마지막 상태로 기본값 정책 유지.

### 6.5 후속 권고
- B2 활성 전 GPU 자동 감지 + 모델 다운로드 진행률 카드 (현재는 단순 토글)
- B4 데이터 누적 14일 충족 자동 활성 시간 게이트 (현재 default 'Y' 로 즉시 활성)
- B5 자체 양식 사용 고객용 양식 import API (PDF 템플릿 업로드)

---

## 7. 변경 이력

- 2026-06-10 v1.2 — B2 / B4 / B5 마스터 토글 Phase 1 구현. Migration 0082 + admin.py + auth_crud.py + /admin/site-settings 3 카드. 6/6 PASS.
- 2026-06-08 v1.1 — B3 매뉴얼·고장 케이스 RAG 마스터 토글 Phase 1 구현. Migration 0078.
- 2026-06-08 v1 — 초안. EPANET (B1) Phase 1 구현. 후속 모듈 B2~B5 예고.
