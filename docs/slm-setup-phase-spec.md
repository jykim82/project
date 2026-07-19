# 기초정보 구축 & 컬럼 잠금 기능 명세

> **상태 (2026-07-19): 컬럼 잠금은 미구현 상태로 UI·메뉴 제거됨** — 프런트가
> mock 데이터·로컬 state 뿐이었고 백엔드 참조 0건, `tb_field_lock` 0행이라
> 구축 메뉴 개편(Migration 0103)에서 잠금 관리 메뉴(M200-10)와 관련 컴포넌트를
> 삭제. 테이블·본 사양은 유지 — 실수요 발생 시 본 명세 기준으로 재구현.

---

## 1. 신규 테이블: 컬럼 잠금 관리

```sql
-- 시설별/컬럼별 잠금 상태 관리
CREATE TABLE tb_field_lock (
    lock_id         bigserial PRIMARY KEY,
    region          character varying(10) NOT NULL,
    target_table    character varying(100) NOT NULL,   -- 'tb_service_reservoir_status'
    target_key      jsonb NOT NULL,                    -- {"sitename": "신평", "facilitytype": "배수지"}
    field_name      character varying(100) NOT NULL,   -- 'alarm_high_water_level'
    is_locked       boolean NOT NULL DEFAULT false,    -- true: 현장 로컬 제어 (웹 수정 불가)
    lock_reason     character varying(200),            -- '현장 PLC 직접 제어'
    locked_by       character varying(45),             -- 잠근 사용자
    locked_at       timestamp with time zone,
    created_at      timestamp with time zone DEFAULT now(),
    updated_at      timestamp with time zone DEFAULT now(),

    CONSTRAINT uq_field_lock UNIQUE (region, target_table, target_key, field_name)
);

CREATE INDEX idx_field_lock_target ON tb_field_lock(region, target_table);

COMMENT ON TABLE tb_field_lock IS '시설별 컬럼 잠금 관리. 현장 로컬 제어 항목은 잠금 처리하여 웹에서 수정 불가';
COMMENT ON COLUMN tb_field_lock.target_key IS '대상 행 식별 (복합키 JSON). 예: {"sitename":"신평"}';
COMMENT ON COLUMN tb_field_lock.is_locked IS 'true=현장 로컬 제어(웹 수정 불가), false=시스템 제어(웹 수정 가능)';
```

### 사용 예시

```sql
-- 신평 배수지: 수위 알람 상/하한 → 현장 로컬 제어 (잠금)
INSERT INTO tb_field_lock (region, target_table, target_key, field_name, is_locked, lock_reason)
VALUES
  ('R01', 'tb_service_reservoir_status', '{"sitename":"신평"}', 'alarm_high_water_level', true, '현장 PLC 직접 제어'),
  ('R01', 'tb_service_reservoir_status', '{"sitename":"신평"}', 'alarm_low_water_level',  true, '현장 PLC 직접 제어');

-- 행정 배수지: 시스템 제어 (잠금 해제)
INSERT INTO tb_field_lock (region, target_table, target_key, field_name, is_locked)
VALUES
  ('R01', 'tb_service_reservoir_status', '{"sitename":"행정"}', 'alarm_high_water_level', false),
  ('R01', 'tb_service_reservoir_status', '{"sitename":"행정"}', 'alarm_low_water_level',  false);
```

---

## 2. 구축 대상 테이블 총괄

### 2-1. 시설 기본정보 (info 테이블)

| # | 테이블 | 설명 | 주요 입력 항목 |
|---|--------|------|---------------|
| 1 | `tb_service_reservoir_info` | 배수지 기본정보 | sitename, general_overview(JSONB), service_area, meta(JSONB) |
| 2 | `tb_service_booster_station_info` | 가압장 기본정보 | sitename, general_overview(JSONB) |
| 3 | `tb_pressure_reducing_facility_info` | 감압시설 기본정보 | sitename, general_overview(JSONB) |
| 4 | `tb_block_info` | 블록 기본정보 | sitename, block_level, general_overview(JSONB) |
| 5 | `tb_equipment_info` | 설비 정보 | sitename, facilitytype, equipment_name, specs |
| 6 | `tb_tag_info` | 태그 마스터 | tagsn, sitename, facilitytype, datadesc, datainfo, unit |
| 7 | `tb_trend_catalog` | 트렌드 차트 설정 | trend_name, meta(JSONB), tag 매핑 |
| 8 | `tb_network_info` | 네트워크 장비 | 장비 정보 |
| 9 | `tb_network_link` | 네트워크 연결 | 장비 간 연결 |
| 10 | `tb_facility_flow_map` | 용수 흐름 | 시설 간 계통 관계 |

### 2-2. 시설 상태정보 (status 테이블) — 🔒 컬럼 잠금 대상

| # | 테이블 | 설명 | 잠금 가능 컬럼 (예시) |
|---|--------|------|---------------------|
| 1 | `tb_service_reservoir_status` | 배수지 상태 | alarm_high_water_level, alarm_low_water_level, target_level |
| 2 | `tb_service_booster_station_status` | 가압장 상태 | target_pressure, alarm_high_pressure, alarm_low_pressure |
| 3 | `tb_pressure_reducing_facility_status` | 감압시설 상태 | target_inlet_pressure, target_outlet_pressure |
| 4 | `tb_block_status` | 블록 상태 | alarm_threshold, target_flow |
| 5 | `tb_equipment_status` | 설비 상태 | alarm_setting, operation_mode |

---

## 3. 컬럼 잠금 동작 방식

### 3-1. 흐름

```
관리자가 구축 페이지 진입
  │
  ├── 시설 선택 (예: 신평 배수지)
  │
  ├── status 테이블 데이터 표시
  │     각 컬럼 옆에 🔒/🔓 아이콘
  │
  │     alarm_high_water_level: [4.5] 🔒  ← 회색 배경, 편집 불가
  │     alarm_low_water_level:  [1.0] 🔒  ← 회색 배경, 편집 불가
  │     target_level:           [3.2] 🔓  ← 편집 가능
  │
  ├── 🔒 클릭 → 잠금 해제 확인 다이얼로그
  │     "현장 로컬 제어에서 시스템 제어로 변경하시겠습니까?"
  │     사유 입력 (선택)
  │     → tb_field_lock.is_locked = false
  │
  ├── 🔓 클릭 → 잠금 설정 다이얼로그
  │     "이 항목을 현장 로컬 제어로 잠그시겠습니까?"
  │     사유 입력
  │     → tb_field_lock.is_locked = true
  │
  └── 값 수정
        잠금 해제된 필드만 수정 가능
        저장 시 잠금 상태 검증 (서버 측에서도 체크)
```

### 3-2. UI 컴포넌트

```tsx
// src/components/setup/LockableField.tsx

interface LockableFieldProps {
  tableName: string;
  targetKey: Record<string, string>;  // {sitename: "신평"}
  fieldName: string;
  label: string;
  value: any;
  type: "number" | "text" | "select";
  unit?: string;
  isLocked: boolean;
  lockReason?: string;
  onValueChange: (value: any) => void;
  onLockToggle: (locked: boolean, reason?: string) => void;
}

function LockableField({
  label, value, type, unit, isLocked, lockReason,
  onValueChange, onLockToggle
}: LockableFieldProps) {
  return (
    <div className="flex items-center gap-2">
      <Label className="w-48">{label}</Label>

      <Input
        type={type}
        value={value}
        onChange={(e) => onValueChange(e.target.value)}
        disabled={isLocked}
        className={isLocked ? "bg-muted cursor-not-allowed" : ""}
      />

      {unit && <span className="text-sm text-muted-foreground">{unit}</span>}

      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => {
                if (isLocked) {
                  // 잠금 해제 확인
                  openUnlockDialog();
                } else {
                  // 잠금 설정
                  openLockDialog();
                }
              }}
            >
              {isLocked ? <Lock className="text-red-500" /> : <Unlock className="text-green-500" />}
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            {isLocked
              ? `🔒 현장 로컬 제어 (수정 불가)\n사유: ${lockReason || '-'}`
              : '🔓 시스템 제어 (수정 가능)'}
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    </div>
  );
}
```

### 3-3. 일괄 잠금 관리 (시설 단위)

```tsx
// 시설 단위로 모든 status 필드 잠금/해제
function FacilityLockManager({ sitename, tableName }: Props) {
  return (
    <div className="border rounded p-4">
      <div className="flex justify-between items-center mb-4">
        <h3>{sitename} 제어 모드</h3>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => lockAll()}>
            <Lock className="mr-1" /> 전체 잠금 (현장 로컬 제어)
          </Button>
          <Button variant="outline" onClick={() => unlockAll()}>
            <Unlock className="mr-1" /> 전체 해제 (시스템 제어)
          </Button>
        </div>
      </div>

      {/* 개별 필드 목록 */}
      {fields.map(field => (
        <LockableField key={field.name} {...field} />
      ))}
    </div>
  );
}
```

---

## 4. Python API 엔드포인트

### 4-1. 컬럼 잠금 관리

| Method | Path | 설명 |
|--------|------|------|
| GET | `/api/field-locks?table={table}&key={json}` | 특정 시설의 잠금 상태 조회 |
| PUT | `/api/field-locks` | 잠금/해제 토글 |
| PUT | `/api/field-locks/batch` | 시설 단위 일괄 잠금/해제 |
| GET | `/api/field-locks/summary` | 전체 잠금 현황 요약 |

**잠금 조회 응답:**
```typescript
interface FieldLockResponse {
  locks: {
    field_name: string;
    is_locked: boolean;
    lock_reason?: string;
    locked_by?: string;
    locked_at?: string;
  }[];
}
```

**잠금 토글 요청:**
```typescript
interface FieldLockToggleRequest {
  region: string;
  target_table: string;
  target_key: Record<string, string>;
  field_name: string;
  is_locked: boolean;
  lock_reason?: string;
}
```

### 4-2. Status 테이블 수정 (잠금 검증 포함)

```python
# Python API: 상태값 수정 시 잠금 체크

async def update_facility_status(request: StatusUpdateRequest):
    # 1. 수정 대상 필드의 잠금 상태 확인
    locked_fields = await db.fetch_all("""
        SELECT field_name FROM tb_field_lock
        WHERE region = $1
          AND target_table = $2
          AND target_key @> $3::jsonb
          AND is_locked = true
    """, request.region, request.table_name, json.dumps(request.target_key))

    locked_names = {r['field_name'] for r in locked_fields}

    # 2. 잠긴 필드 수정 시도 → 거부
    for field, value in request.updates.items():
        if field in locked_names:
            raise HTTPException(
                status_code=403,
                detail=f"'{field}' 필드는 현장 로컬 제어로 잠겨 있어 수정할 수 없습니다."
            )

    # 3. 잠금 해제된 필드만 업데이트
    await update_fields(request)
```

### 4-3. 기초정보 CRUD

| Method | Path | 설명 |
|--------|------|------|
| **배수지** | | |
| GET | `/api/setup/reservoirs` | 배수지 목록 |
| GET | `/api/setup/reservoirs/{sitename}` | 배수지 상세 (info + status + locks) |
| POST | `/api/setup/reservoirs` | 배수지 등록 |
| PUT | `/api/setup/reservoirs/{sitename}` | 배수지 수정 (잠금 체크) |
| DELETE | `/api/setup/reservoirs/{sitename}` | 배수지 삭제 |
| **가압장** | | |
| CRUD | `/api/setup/boosters` | 동일 패턴 |
| **감압시설** | | |
| CRUD | `/api/setup/pressure` | 동일 패턴 |
| **블록** | | |
| CRUD | `/api/setup/blocks` | 동일 패턴 |
| **태그 마스터** | | |
| CRUD | `/api/setup/tags` | 개별 CRUD |
| POST | `/api/setup/tags/bulk` | 엑셀 일괄 업로드 |
| **설비** | | |
| CRUD | `/api/setup/equipments` | 동일 패턴 |
| **트렌드** | | |
| CRUD | `/api/setup/trends` | 동일 패턴 |
| **네트워크** | | |
| CRUD | `/api/setup/networks` | 장비 + 링크 |
| **용수 흐름** | | |
| CRUD | `/api/setup/flow-map` | 계통 관계 |

---

## 5. 구축 페이지 구조

### 메뉴 추가

```sql
-- tb_menu에 구축 메뉴 추가
INSERT INTO tb_menu (region, menu_idn, menu_nm, pmenu_idn, app_path, menu_type, menu_idx, use_yn) VALUES
  ('R01', 'M200',   '구축',         NULL,    NULL,                      'group', 90, 'Y'),
  ('R01', 'M200-1', '배수지',      'M200',  '/setup/reservoir',         'menu',  1,  'Y'),
  ('R01', 'M200-2', '가압장',      'M200',  '/setup/booster',           'menu',  2,  'Y'),
  ('R01', 'M200-3', '감압시설',    'M200',  '/setup/pressure',          'menu',  3,  'Y'),
  ('R01', 'M200-4', '블록',        'M200',  '/setup/block',             'menu',  4,  'Y'),
  ('R01', 'M200-5', '태그 마스터', 'M200',  '/setup/tags',              'menu',  5,  'Y'),
  ('R01', 'M200-6', '설비',        'M200',  '/setup/equipments',        'menu',  6,  'Y'),
  ('R01', 'M200-7', '트렌드 설정', 'M200',  '/setup/trends',            'menu',  7,  'Y'),
  ('R01', 'M200-8', '네트워크',    'M200',  '/setup/networks',          'menu',  8,  'Y'),
  ('R01', 'M200-9', '용수 흐름',   'M200',  '/setup/flow-map',          'menu',  9,  'Y'),
  ('R01', 'M200-10','잠금 관리',   'M200',  '/setup/field-locks',       'menu',  10, 'Y');

-- 관리자만 접근
INSERT INTO tb_auth_menu (region, auth_idn, menu_idn)
SELECT 'R01', 'ADMIN', menu_idn FROM tb_menu WHERE region = 'R01' AND menu_idn LIKE 'M200%';
```

### Next.js 라우트

```
src\app\(dashboard)\setup\
├── reservoir\
│   ├── page.tsx              # 배수지 목록
│   └── [sitename]\page.tsx   # 배수지 상세 (info + status + 🔒잠금)
├── booster\
│   ├── page.tsx
│   └── [sitename]\page.tsx
├── pressure\
│   ├── page.tsx
│   └── [sitename]\page.tsx
├── block\
│   ├── page.tsx
│   └── [sitename]\page.tsx
├── tags\
│   └── page.tsx              # 태그 목록 + 엑셀 업로드
├── equipments\
│   └── page.tsx
├── trends\
│   └── page.tsx
├── networks\
│   └── page.tsx
├── flow-map\
│   └── page.tsx
└── field-locks\
    └── page.tsx              # 전체 잠금 현황 대시보드
```

### 시설 상세 페이지 레이아웃 (배수지 예시)

```
┌─────────────────────────────────────────────────────┐
│  ◀ 배수지 목록    신평 배수지                          │
├─────────────────────────────────────────────────────┤
│                                                      │
│  [기본정보]  [상태설정]  [설비]  [잠금이력]              │
│  ─────────────────────────────────────────────       │
│                                                      │
│  ┌─ 기본정보 탭 ──────────────────────────────┐      │
│  │  현장명:        [신평        ]               │      │
│  │  설치위치:      [당진시 신평면 ]               │      │
│  │  운영상태:      [정상 ▾      ]               │      │
│  │  급수인구:      [12500       ] 명            │      │
│  │  시설용량:      [3000        ] ㎥            │      │
│  │  급수지역:      [신평면 일원  ]               │      │
│  └──────────────────────────────────────────┘      │
│                                                      │
│  ┌─ 상태설정 탭 ──────────────────────────────┐      │
│  │                                              │      │
│  │  제어 모드: [전체 잠금] [전체 해제]            │      │
│  │  ───────────────────────────────────         │      │
│  │  수위 상한 경보:  [4.5 ] m  🔒 현장 로컬제어  │      │
│  │  수위 하한 경보:  [1.0 ] m  🔒 현장 로컬제어  │      │
│  │  목표 수위:       [3.2 ] m  🔓 수정 가능      │      │
│  │  일평균 공급량:   [150 ] ㎥  🔓 수정 가능      │      │
│  │                                              │      │
│  └──────────────────────────────────────────┘      │
│                                                      │
│                              [취소]  [저장]          │
└─────────────────────────────────────────────────────┘
```

### 잠금 관리 대시보드 (`/setup/field-locks`)

```
┌────────────────────────────────────────────────────┐
│  잠금 관리 현황                                      │
├────────────────────────────────────────────────────┤
│                                                     │
│  필터: [시설유형 ▾] [현장 ▾] [잠금상태 ▾] [검색]     │
│                                                     │
│  ┌──────────┬──────────┬───────────┬─────┬──────┐  │
│  │ 현장      │ 시설유형  │ 항목       │상태  │ 사유  │  │
│  ├──────────┼──────────┼───────────┼─────┼──────┤  │
│  │ 신평      │ 배수지   │ 수위상한경보│ 🔒  │PLC제어│  │
│  │ 신평      │ 배수지   │ 수위하한경보│ 🔒  │PLC제어│  │
│  │ 신평      │ 배수지   │ 목표수위   │ 🔓  │      │  │
│  │ 행정      │ 배수지   │ 수위상한경보│ 🔓  │      │  │
│  │ 행정      │ 배수지   │ 수위하한경보│ 🔓  │      │  │
│  │ 기지시    │ 가압장   │ 목표압력   │ 🔒  │현장고정│  │
│  └──────────┴──────────┴───────────┴─────┴──────┘  │
│                                                     │
│  요약: 전체 42개 항목 / 🔒 잠금 15개 / 🔓 해제 27개  │
└────────────────────────────────────────────────────┘
```

---

## 6. 개발 순서 (업데이트)

```
Step 0: 참고 사이트 분석
  ↓
Step 1: 프로젝트 초기화
  ↓
Step 2: DB 개선 (FK + 날짜 + bcrypt + file_storage + visual_data + tb_field_lock)
  ↓
Step 3: 인증 시스템
  ↓
Step 4: 레이아웃 & 동적 메뉴
  ↓
Step 5: 기초정보 구축 페이지 + 컬럼 잠금  ← 신규
  ↓
Step 6: 채팅 ─┐
Step 6: 차트   ├── 병렬 가능
Step 6: 관리자 ┘
  ↓
Step 7: 고도화 & 테스트
```

구축 페이지가 모니터링/채팅보다 앞에 와야 하는 이유:
- 시설/태그 마스터 데이터가 없으면 차트가 비어있음
- 알람 설정값이 없으면 알람이 동작 안 함
- 계통도 데이터가 없으면 네트워크 페이지가 빈 화면

---

## 7. Claude Code 실행 명령

```
/sc:spawn "기초정보 구축 페이지 전체 구현 - 
info 테이블 10개 CRUD + status 테이블 5개 수정 + 
컬럼 잠금(tb_field_lock) 관리 + 잠금 현황 대시보드.
각 시설 상세 페이지에서 기본정보/상태설정/설비/잠금이력 탭 구성.
status 필드는 LockableField 컴포넌트로 잠금/해제 토글.
태그 마스터는 엑셀 일괄 업로드 지원."
```
