# 비상연락처 사양 (v1)

알람·고장 발생 시 카테고리별 출동/AS 업체 연락처 조회 인프라.
관리 UI (`/setup/alarm-contacts`) + AI 채팅 인텐트 (`EMERGENCY_CONTACT_QUERY`)
2 경로로 활용.

**관련 사양**: `docs/alarm-category-summary-spec.md` (알람 카테고리), `docs/slm-api-contract-final.md`

---

## 1. 데이터 모델

### `tb_alarm_contact`
| 컬럼 | 타입 | 설명 |
|------|------|------|
| contact_id | SERIAL PK | 식별자 |
| region | VARCHAR(10) | 멀티테넌시 (기본 'R01', 모든 다른 테이블과 정합 — 2026-06-08 'water'→'R01' 통일) |
| category | VARCHAR(50) | UPS / 정전 / 네트워크 / 밸브 / 펌프 / 압력 / 수질 등 (자유 입력) |
| company | VARCHAR(100) | 업체명 |
| phone | VARCHAR(50) | 전화번호 (휴대폰·일반·콜센터) |
| description | TEXT | 점검 범위·기타 비고 |
| sort_order | INT | 카테고리 내 우선순위 (1=주력) |
| created_at, updated_at | TIMESTAMPTZ | 감사 |

### 초기 시드 (2026-04-05) — 10건
- UPS 3건 (동양산전 / 제우씨엔아이 / 오라테크)
- 정전 3건 (UPS 와 동일 업체)
- 네트워크 2건 (JS정보통신 / KT콜센터)
- 밸브 1건 (아세아상사)
- (펌프 / 압력 / 수질 등 미시드 — 사이트 별 추가)

---

## 2. 관리 UI — `/setup/alarm-contacts`

### 위치
- 사이드바: 구축 > 비상연락처 (M200-14)
- adminOnly (MASTER/ADMIN 만)

### CRUD
| 동작 | 메서드 | path |
|------|--------|------|
| 조회 | GET | `/crisis/alarm-contacts?region=R01` |
| 카테고리 목록 | GET | `/crisis/alarm-contacts/categories?region=R01` |
| 추가 | POST | `/crisis/alarm-contacts?region=R01` |
| 수정 | PUT | `/crisis/alarm-contacts/{contact_id}` |
| 삭제 | DELETE | `/crisis/alarm-contacts/{contact_id}` |

### 프론트 호출 — `apiClient` 통한 `/api/proxy/*` 경유
직접 `localhost:8000` 호출 금지 — JWT 자동 첨부 + LAN IP 호환.

---

## 3. AI 채팅 인텐트 — `EMERGENCY_CONTACT_QUERY`

### 등록
- `example3.json` (75개째)
- `intent_classifier.py` Stage 2 키워드 단축 — 카테고리 무관 **최우선** 분기
  (다른 알람 인텐트보다 먼저 잡혀야 함)

### 트리거 키워드
```python
_contact_trigger_kws = (
    "비상연락", "비상 연락", "긴급 연락", "긴급연락", "연락처", "연락망",
    "어디로 전화", "어디 연락", "누구한테 전화", "누구한테 연락",
    "업체 전화", "업체 연락", "출동 업체",
)
```

### 예시 질의
- "비상연락처 알려줘"
- "UPS 고장났는데 어디에 연락해?"
- "정전 비상연락망 알려줘"
- "네트워크 장애 어디로 연락해?"
- "펌프 이상 업체 연락처"

### SQL
```sql
SELECT category, company, phone,
       COALESCE(description, '') AS description, sort_order
FROM tb_alarm_contact
ORDER BY category, sort_order
```
- 현재 단일 region 환경 가정 (`{region}` placeholder 인프라 부재). 멀티 region
  지원 시 sql_executor 에 region 자동 주입 로직 추가 필요.

### 응답
- `graph_type: "table"` — 카테고리/업체/전화/설명 4열
- summary: "등록된 비상연락처 {total_count}건입니다. 알람·고장 발생 시 해당
  카테고리 업체에 직접 연락하세요."
- reference: "등록·수정·삭제: /setup/alarm-contacts (구축 > 비상연락처)"

---

## 4. 향후 확장 (v2)

### 카테고리 필터링
사용자가 "UPS 고장났는데 어디 연락?" → 카테고리 추출 (UPS) → WHERE category='UPS' 자동 적용.

### 알람 ↔ 연락처 자동 연결
알람 카테고리 (`tb_alarm_history.category`) 가 `tb_alarm_contact.category` 와
매칭 시 알람 상세 카드에 "관련 비상연락처" 섹션 자동 노출.
- 매핑 정책: 알람 category 와 contact category 동일 문자열 매칭
- 부재 시 카테고리 매핑 테이블 (`tb_alarm_contact_mapping`) 또는 LLM 분류

### 알람 분석 / 작업 등록 흐름 통합
- `/crisis/alarm-analysis` 카드에 "현장 출동" 버튼 → 연관 contact dropdown
- 작업 등록 시 contact_id 외래키로 기록 → 작업 이력에 "JS정보통신 출동" 자동 표기

---

## 5. 변경 이력

- 2026-06-08 v1 — 초안. region 'water'→'R01' 통일 (Migration 0077),
  프론트 apiClient 패턴 전환, AI 채팅 인텐트 EMERGENCY_CONTACT_QUERY 신규.
