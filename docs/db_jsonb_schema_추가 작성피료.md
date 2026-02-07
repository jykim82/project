# db_jsonb_schema.md
DB JSONB 컬럼 구조 정의서

---

## 1. 문서 목적

본 문서는 PostgreSQL DB에 JSONB 타입으로 저장된 컬럼의  
구조(schema)를 명시적으로 정의한다.

이 문서는 다음을 보장하기 위해 존재한다.

- Python 코드에서의 안전한 접근
- SQL / JSON 처리의 일관성
- 운영 중 구조 변경에 따른 장애 방지

---

## 2. 기본 원칙

- JSONB 컬럼은 자유 형식이 아니다.
- 구조 변경은 스키마 변경에 준한다.
- Python 코드는 본 문서를 신뢰하고 구현한다.

---

## 3. tb_service_reservoir_info.general_overview

### 3.1 컬럼 개요

- 테이블: tb_service_reservoir_info
- 컬럼: general_overview
- 타입: jsonb
- 설명: 배수지 일반현황 메타데이터

본 JSONB 컬럼은 배수지 시설의 기본 제원 및 운영 정보를 담고 있으며,  
Python 코드에서 구조적으로 해석하여 설명 문장 생성에 사용된다.

---

### 3.2 JSON 구조

```json
{
  "install_location": "string",
  "operating_status": "string",
  "supply_population": "number",
  "facility_capacity_m3": "number",
  "reservoir_spec": {
    "count": "number",
    "H.W.L": "number",
    "L.W.L": "number"
  },
  "emergency_water_plan": ["string"],
  "water_truck_accessible": "boolean",
  "water_truck_turning_possible": "boolean"
}

---
## 4. v_booster_station_info_status.general_overview

### 4.1 컬럼 개요

- 뷰(View): v_booster_station_info_status
- 컬럼: general_overview
- 타입: jsonb
- 설명: 가압장(부스터 스테이션) 일반 현황 메타데이터

본 JSONB 컬럼은 가압장의 펌프 구성, 설치 정보, 운영 상태 등  
기본 제원 정보를 담고 있으며, Python 코드에서 구조적으로 해석되어  
설명 문장 생성에 사용된다.

---

### 4.2 JSON 구조

```json
{
  "pump": {
    "count": "string",
    "head_m": "string",
    "contractor": "string | null",
    "manufacturer": "string | null",
    "reservoir_linked": "string",
    "linked_reservoirs": "string"
  },
  "booster_type": "string",
  "install_year": "string",
  "install_location": "string",
  "operating_status": "string",
  "facility_capacity_m3": "string"
}

---

## 5. v_block_info_status.general_overview

### 5.1 컬럼 개요

- 뷰(View): v_block_info_status
- 컬럼: general_overview
- 타입: jsonb
- 설명: 블록(소/중/대) 일반 현황 메타데이터

본 JSONB 컬럼은 블록 단위의 기본 제원, 고객 수, 관로 정보 및  
대용량 수용가 현황 등을 포함하며,  
Python 코드에서 구조적으로 해석되어 설명 문장 생성에 사용된다.

---

### 5.2 JSON 구조

```json
{
  "customer_count": "string",
  "pipeline_length": {
    "old": "string",
    "total": "string"
  },
  "install_location": "string",
  "large_customer_status": {
    "count": "string",
    "base_month_usage": "string"
  },
  "non_revenue_water_rate": "string"
}


---

## 6. v_pressure_reducing_facility_info_status.general_overview

### 6.1 컬럼 개요

- 뷰(View): v_pressure_reducing_facility_info_status
- 컬럼: general_overview
- 타입: jsonb
- 설명: 감압시설(감압밸브) 일반 현황 메타데이터

본 JSONB 컬럼은 감압시설의 기본 제원 및 운영 정보를 담고 있으며,  
Python 코드에서 구조적으로 해석하여 설명 문장 생성에 사용된다.

---

### 6.2 JSON 구조

```json
{
  "install_location": "string",
  "operating_status": "string",
  "pressure_reducing_valve": {
    "manufacturer": "string",
    "pipe_diameter": "string",
    "control_method": "string"
  }
}
