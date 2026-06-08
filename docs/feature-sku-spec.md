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

#### admin 페이지 (1개) — **유지** (복구 경로)
| menu_idn | label | path | 비고 |
|----------|-------|------|------|
| M100-12 | EPANET 시뮬레이션 | /admin/epanet | OFF 상태에서도 사이드바 노출. 마스터 토글 ON 진입점. |

#### GIS 페이지 내부 토글 (6개) — `data-quality menus_disabled` 자동 포함
EPANET 시뮬 / 누수 의심 / 헤드손실 이상 / 밸브 영향 / 관로 파손 / 교체 후보 / 실측 유량 차이.
`isEpanetEnabled(menu_key)` 가 false 반환 → 토글 hidden.

#### EPANET 무관 (유지)
- M003-5 GIS 관망도 (시설 마커·SHP 관망 표시 — EPANET 시뮬 무관)
- M003-5 안의 [물흐름 표시] 토글 (`gis-flow-arrow`) — EPANET 시뮬과 독립

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
1. `/admin/menus` 또는 `/admin/site-settings` URL 직접 진입 (사이드바에 항상 노출 — adminOnly 보호)
2. 토글 ON

### 3.5 백엔드 변경

#### `endpoints/epanet.py`
`_check_data_quality(region)` — 응답 `menus_disabled` 생성 로직 변경:
```python
disabled = _menus_disabled(region)  # 기존: tb_epanet_menu_setting enabled='N'
if not is_enabled(region):           # 신규: 마스터 OFF 시 11 menu_key 모두 추가
    for key in _MENU_REQUIREMENTS.keys():
        if key == "gis-flow-arrow":  # 마스터 독립 — 제외
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
- **B4 트렌드 비교**: 데이터 누적 14일 후 자동 활성 옵션 (시간 게이트)
- **B5 보고서**: 자체 양식 사용 고객 위해 분리

---

## 5. 변경 이력

- 2026-06-08 v1 — 초안. EPANET (B1) Phase 1 구현. 후속 모듈 B2~B5 예고.
