---
name: GIS 관망도 시설 메뉴 고도화 사양
status: v1 계획 (사양 작성 — 구현 1순위 대기)
created: 2026-06-29
updated: 2026-06-29
priority: P1 (1순위)
---

# GIS 관망도 시설 메뉴 고도화 사양

## 1. 목적 / 배경

GIS 관망도에서 **시설(배수지·가압장 등)과 그 안의 설비(PLC·유량계·수위계·펌프·
통신장비)를 직접 추가·수정·삭제**하고, 설치일자·제조사·제원 등 자산 정보를
관리하는 메뉴로 고도화한다. 운영자가 관망도 위에서 시설을 클릭해 일반현황과
설비 목록을 보고, 자산 이력을 유지보수할 수 있게 한다.

**우선순위: 본 사양 작업을 1순위로 진행** (구현은 사양 확정 후).

## 2. 범위 (1차 / 2차)

### 1차 고도화 (본 사양 핵심)
- 시설 **일반정보 CRUD** (배수지·가압장·감압시설): 일반현황·제원·사진·매뉴얼.
- 시설 내 **설비 CRUD** (펌프·밸브·수위계·압력계·유량계·수질계·UPS·전원·
  침수센서·PLC·통신장비 등): 설치일자·제조사·제원(모델)·상태·설명·명판/외관 사진.
- GIS 관망도 시설 마커 → **인스펙터**에서 일반현황 + 설비 목록 + CRUD 진입.

### 2차 고도화 (1차 이후)
- 설비별 **고장이력 조회** (알람·장애 이력, MTBF·월별 통계).
- **교체 주기/수명** 관리 (권장 내용연수·세무 내용연수·EOL·교체 후보).

## 3. 데이터 모델 — **기존 스키마 재사용** (신규 테이블 최소화)

> 원칙: 아래 테이블이 이미 존재하므로 **신규 테이블 생성이 아니라 메뉴/CRUD/검증
> 계층을 얹는다**. 부족한 항목만 컬럼·메타 보강.

### 3.1 시설(facility) 계층
> 실제 `tb_tag_info.facilitytype` 분포: 배수지(1504) · 가압장(719) · 소블록(470)
> · 소소블록(7). `tb_equipment_info` 에는 제어망(통신장비 소속)도 존재.

| 시설 유형 | 일반현황 테이블 | 상태/현재값 |
|---|---|---|
| 배수지 | `tb_service_reservoir_info` (general_overview jsonb, install_year, zone_N_area/height, *_url 사진·매뉴얼·계통도) | `tb_service_reservoir_status` |
| 가압장 | `tb_service_booster_station_info` | `tb_service_booster_station_status` (pump_control_mode, pump_start/stop_threshold, linked_reservoir_name …) |
| 감압시설 | `tb_pressure_reducing_facility_info` | `tb_pressure_reducing_facility_status` |
| **블록(소블록·소소블록)** | **전용 _info 테이블 없음** — 토폴로지·태그 기반 네트워크 단위. 1차에선 식별·설비 귀속만, 일반현황 필요 시 `meta` 확장 | `tb_facility_flow_map` |
| 제어망 | (tb_equipment_info 그룹) — 통신장비 소속 시설 | |
| 별칭/매핑 | `tb_facility_alias`, `tb_facility_flow_map` (토폴로지) | |
| 파일 | `tb_facility_file`, `tb_file_storage` (위치도·계통도·매뉴얼 PDF) | |

### 3.2 설비(equipment) — `tb_equipment_info` (마스터, 이미 존재)
| 요청 항목 | 컬럼 | 비고 |
|---|---|---|
| 설비 ID | `equipment_id` (PK) | |
| 소속 시설 | `sitename` + `facilitytype` | 시설↔설비 1:N |
| 설비 종류 | `equipmenttype` | PLC/유량계/수위계/펌프/UTM/SSLVPN/LTE/DSU … |
| **설치일자** | `commissioned_at` (date) | 폐기일=`decommissioned_at` |
| **제조사** | `meta->>'manufacturer'` | 예: SECUI |
| **제원/모델** | `meta->>'model'`, `meta` 자유 키 | 예: "BLUEMAX NGF 500" |
| 상태 | `status` ('운영중'/operational …) | |
| 설명/역할 | `description`, `meta->>'role'`, `meta->>'note'` | |
| 사진 | `equipment_photo_url`, `nameplate_photo_url` | 외관·명판 |
| 태그 연결 | `tb_equipment_tag_map` | 설비↔태그(유량계→유량태그 등) |
| 이미지/매뉴얼 | `tb_equipment_image`, `tb_equipment_manual` | |
| 카테고리 | `tb_equipment_category_map` | equipmenttype→대분류 |

> **제조사/제원은 신규 컬럼 없이 `meta` jsonb 로 관리**(이미 manufacturer/model
> 사용 중). 단, 표준 입력 폼을 위해 **권장 메타 키 스키마**(manufacturer, model,
> serial_no, spec, capacity 등)를 §5.2 로 고정한다. 하드코딩 금지·config 분리.

### 3.3 2차용 (이미 존재)
- 고장이력: `tb_equipment_alarm_report`, `v_equipment_fault_stats`,
  `v_equipment_fault_monthly`, `v_equipment_mtbf` (설비 장애 이력 사양 Migration 0045).
- 교체주기: `tb_equipment_lifespan` (category, years_recommended, years_tax, eol_note).

### 3.4 컬럼 잠금
기초정보 구축 사양(`slm-setup-phase-spec.md`)의 `tb_field_lock` 을 그대로 적용 —
확정된 시설/설비 필드는 잠금 후 보호.

## 4. 계층 구조 (요청 2번 반영)

```
시설 (배수지 / 가압장 / 블록(소블록·소소블록) / 감압시설 / 제어망)
├─ 일반현황  (tb_service_*_info: 제원·존별 면적/높이·사진·매뉴얼·계통도)
├─ 상태/현재값 (tb_service_*_status)
└─ 설비들 (tb_equipment_info, 시설 1 : N 설비)   ※ equipmenttype = DB 실제 값
   ├─ 펌프        (가압펌프)
   ├─ 밸브        (유입밸브 / 유출밸브 / 토출밸브 / 송수밸브 / 흡수정밸브)
   ├─ 수위계 / 압력계 / 유량계 / 수질계
   ├─ 수질        (수질계)
   ├─ UPS / 전원
   ├─ 침수센서
   ├─ PLC
   └─ 통신장비    (네트워크 / LTE / 유선 / DSU / UTM / SSLVPN)
   (각 설비: commissioned_at=설치일자, meta.manufacturer/model=제조사/제원,
    사진, tb_equipment_tag_map 태그매핑)
```

## 5. 1차 고도화 상세

### 5.1 기능
- 시설: 조회·등록·수정·삭제(soft, decommission). 일반현황 폼 + 사진/매뉴얼 업로드.
- 설비: 시설 컨텍스트 내 목록 + 등록·수정·삭제. 종류 선택 → 종류별 폼.
- GIS 관망도 연동: 시설 마커 클릭 → **인스펙터**(`inspector-pattern-spec.md` 재사용)
  → 탭(일반현황 / 설비목록) → 설비 행 클릭 시 상세·편집.
- 검증: 필수값(시설/종류/설치일자), 날짜 형식(YYYY-MM-DD), region 일치, 중복 ID 방지.
- 삭제는 **decommissioned_at 기록(논리 삭제)** 기본, 물리삭제는 관리자 한정.

### 5.2 설비 종류별 입력 스키마 (config 분리)
종류별 권장 메타 키를 `config`(예: `equipment-field-schema`)로 정의 — 하드코딩 금지.
- 공통: manufacturer, model, serial_no, commissioned_at, status, photo, nameplate.
- 펌프(가압펌프): capacity(용량), head(양정), power_kw, control_mode.
- 밸브(유입/유출/토출/송수/흡수정): valve_type, size_mm, actuator(전동/수동), open_close_time.
- 계측기(수위계/압력계/유량계/수질계): range, unit, output_type, accuracy.
- UPS/전원: capacity_va, battery_type, backup_minutes.
- 침수센서: detect_type, mount_height.
- PLC: maker_series, firmware, io_points.
- 통신장비(네트워크/LTE/유선/DSU/UTM/SSLVPN): comm_type, carrier, ip_address, modem_model.

### 5.3 권한·감사
- `tb_auth_menu` 권한 기반 접근. 등록/수정/삭제는 감사 로그(작성자·시각).
- 멀티테넌시: `region` 격리. 전역 상태 금지.

## 6. 2차 고도화 상세 (1차 이후)

**구현 완료 (2026-06-29) — GIS 인스펙터 통합** (신규 `GisEquipmentRow` 컴포넌트):
- **고장이력**: 설비 행 확장 시 해당 설비유형의 알람+고장+조치 타임라인 표시
  (`/monitoring/alarm-fault-correlation/equipment-timeline`, 최근 1년 알람/고장 건수).
- **교체주기**: 설비 행에 잔존수명 배지(교체권장/임박/정상 + 사용/권장년)
  (`/monitoring/equipment-health/lifespan`, `tb_equipment_lifespan` 카테고리 기준).
- 기존 엔드포인트 재사용 — 백엔드 변경 없음.
- **데이터 보강 의존**: 잔존수명 배지는 `commissioned_at`(설치일자) + equipmenttype→
  `tb_equipment_lifespan` 카테고리 매핑이 있어야 표시(현재 대부분 미입력 → no_data).
  1차 설비 CRUD 설치일자 입력 + 카테고리 시드로 활성화. (검증: 임시 설치일 주입 시
  "교체권장 16/12년" 정상 표시 확인.)

**후속(미구현):** 시설 단위 MTBF·월별 추세 탭, 대시보드 "교체 권고" KPI 딥링크.

## 7. UI/UX
- 인스펙터 패턴(`inspector-pattern-spec.md`), 메트릭 패널(`metric-trend-panel-spec.md`)
  재사용. 다크모드 기본/라이트 토글. 한국어 UI(YYYY-MM-DD).
- 신규 페이지 추가 시 `sidebar-menus.ts` + `tb_menu` INSERT 둘 다 (CLAUDE.md 규칙).

## 8. 폐쇄망 / 멀티테넌시 / 호환
- 외부 API·CDN 의존 없음(사진/매뉴얼은 `files/` 로컬 저장).
- migration 추가 시 롤백 절차 명시, 데이터 손실 없는 업그레이드.
- 구축 업체별 설비 종류·메타 스키마 차이는 config 로 분리.

## 9. 관련 사양 (정합 확인)
- `docs/slm-setup-phase-spec.md` — 기초정보 구축 + 컬럼 잠금(tb_field_lock) 재사용.
- `docs/equipment-fault-tracking-spec.md` — 설비 단위 장애 이력(Migration 0045) → 2차 고장이력.
- `docs/gis_plan.md`, `docs/epanet-menu-spec.md` — GIS 관망도/메뉴 컨텍스트.
- `docs/inspector-pattern-spec.md`, `docs/metric-trend-panel-spec.md` — UI 패턴.
- `docs/report-spec.md` 교체 권고 KPI — 2차 교체주기 연계.

## 10. 단계 / 우선순위 + 구현 현황

> **조사 결과: Phase 1의 백엔드·기본 UI 대부분이 이미 존재.** 신규 구축이 아니라
> 흩어진 기능을 GIS 관망도 컨텍스트로 통합하는 것이 실제 갭이었음.

**이미 존재 (재사용):**
- 백엔드 CRUD 라이브: `/equipments`(GET/POST/PUT/DELETE)·`/reservoirs`·`/boosters`·
  `/pressure-reducing`·`/canvas/equipment-tag-link`·CSV import·매뉴얼 업로드
  (`endpoints/facility_crud.py`, `facility_types_crud.py`, `canvas_crud.py`, `admin.py`).
- 프런트: `/setup/equipments`(설비 CRUD 테이블+EquipmentFormDialog), `/setup/reservoir`·
  `/booster`(시설 CRUD), `/monitoring/gis`(관망도+GisDetailPanel 인스펙터, 읽기전용),
  `/admin/facility-files`(파일).

**1차 구현 완료 (2026-06-29) — GIS 인스펙터 설비 CRUD 통합:**
- `GisDetailPanel` 설비 정보 섹션에 추가 버튼 + 행별 수정/삭제 → 기존
  `EquipmentFormDialog`/`EquipmentDeleteDialog` 재사용(시설 컨텍스트 prefill+잠금).
- 버그 수정: 설비 목록을 `res.data?.items`(오경로)로 읽어 항상 비어 보이던 것 →
  `fetchEquipments(res.data)` 로 교체.

**1차 추가 완료 (2026-06-29):**
- 시설 클릭 → 인스펙터 오픈 배선 수정(`setDetailFacility` 누락 → 마커·목록 클릭 연결).
- 설비 종류별 **제원 필드 스키마(§5.2)** 구현 — `config/equipment-field-schema.ts`
  (펌프·밸브·계측기·UPS·침수센서·PLC·통신장비). 장비유형 선택 시 폼 동적 노출 →
  `meta` 저장. /setup·GIS 인스펙터 양쪽 폼 공통 반영. Playwright 검증 완료.

**1차 잔여:** 시설 일반현황 편집을 인스펙터에서 직접(현재는 /setup 페이지),
권한/감사 강화.

**2차 구현 완료 (2026-06-29):** GIS 인스펙터에 설비 고장이력 타임라인 + 잔존수명
교체 배지 통합(`GisEquipmentRow`). 기존 엔드포인트 재사용. 잔존수명은 설치일자·
카테고리 데이터 보강 시 활성(코드 검증 완료).

## 11. 이력
- 2026-06-29 v1 작성 — GIS 관망도 시설 메뉴 고도화 계획. 기존 스키마
  (tb_equipment_info.meta·commissioned_at, tb_equipment_lifespan, tb_service_*_info)
  재사용 전제. 1차(CRUD)/2차(고장이력·교체주기) 분리. 사양 작업 1순위.
- 2026-06-29 v1.1 — 계층 구조를 DB 실제 값으로 확정. 시설=배수지/가압장/블록
  (소블록·소소블록)/감압시설/제어망, 설비=펌프·밸브(유입/유출/토출/송수/흡수정)·
  수위계·압력계·유량계·수질계·UPS·전원·침수센서·PLC·통신장비. §5.2 설비 종류별
  필드 스키마에 밸브·계측기·UPS·침수센서 추가. (블록은 전용 _info 테이블 없음.)
- 2026-06-29 1차 구현 — 탐색 결과 백엔드 CRUD·setup UI 가 이미 존재함을 확인(§10).
  실제 갭인 GIS 인스펙터 설비 CRUD 통합을 구현: GisDetailPanel 추가/수정/삭제 +
  EquipmentFormDialog prefill·잠금 옵션 + 설비목록 빈-표시 버그 수정. (submodule)
