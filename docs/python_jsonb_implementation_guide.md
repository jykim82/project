# python_jsonb_implementation_guide.md
DB JSONB 기반 Python 구현 규약서

---

## 1. 문서 목적

본 문서는 PostgreSQL DB에 JSONB 타입으로 저장된 컬럼을  
Python 코드에서 **일관되고 안전하게 처리하기 위한 구현 규약**을 정의한다.

이 문서는 다음을 목적으로 한다.

- DB JSONB 구조와 Python 구현 간의 계약 명시
- Claude Code가 생성하는 Python 코드의 일관성 보장
- JSONB 구조 변경 시 영향 범위 명확화
- 조용히 실패하는 코드 패턴 방지

---

## 2. 문서 간 역할 분리

본 문서는 다음 문서들과 함께 사용된다.

| 문서 | 역할 |
|---|---|
| db_jsonb_schema.md | DB JSONB 컬럼의 구조 정의 |
| example3_policy.md | 질의(INTENT) 정책 정의 |
| python_jsonb_implementation_guide.md | Python 구현 규약 |

본 문서는 **JSONB가 무엇을 담고 있는지 정의하지 않는다.**  
JSONB 구조 정의는 반드시 `db_jsonb_schema.md`를 따른다.

---

## 3. 공통 구현 원칙

### 3.1 책임 분리 원칙

- SQL은 JSONB 컬럼을 **그대로 조회**한다.
- JSONB 구조 해석 책임은 Python에 있다.
- Python 코드는 본 문서를 계약으로 신뢰한다.

---

### 3.2 접근 방식 원칙

- JSONB 컬럼은 Python에서 dict로 처리한다.
- SQL에서 `->`, `->>` 접근을 남용하지 않는다.
- Python에서 구조적 접근을 수행한다.

---

### 3.3 방어 코드 사용 원칙

다음 패턴은 **금지**한다.

- `.get().get()` 체인
- KeyError를 무시하는 try/except
- 기본값으로 구조 오류를 숨기는 코드

```python
# 금지 예시
value = overview.get("reservoir_spec", {}).get("count", 0)

### 3.4 표준 파서 함수 패턴 (참고)

본 예시는 JSONB 컬럼 접근에 대한 **형태 예시**이며,  
구현 상세를 강제하지 않는다.

```python
def parse_xxx(jsonb: dict) -> dict:
    """
    JSONB schema reference: docs/db_jsonb_schema.md
    """
    return {
        "some_value": jsonb["some_key"]["nested_key"]
    }


## answer_template null 처리 규칙

answer_template 기반 설명 문장 생성 시,
치환 값이 null, None, 빈 문자열인 경우
해당 문장 라인은 **출력에서 제외한다.**

이 규칙은 모든 INTENT에 공통 적용된다.

---

### 처리 원칙

- 값이 null인 경우
  - "null", "없음", "미정" 등의 문구를 생성하지 않는다.
  - 해당 문장 전체를 결과에서 제거한다.

- 여러 placeholder가 포함된 문장일 경우
  - 하나라도 null이면 해당 문장은 제거한다.

---

### 예시

answer_template:
- "{sitename} 배수지는 총 {supply_count}지로 용수 공급 가능합니다."
- "{sitename} 배수지의 총 용수 공급 가능량은 {supply_capacity}입니다."

데이터:
- supply_count = 2
- supply_capacity = null

출력 결과:
- "○○ 배수지는 총 2지로 용수 공급 가능합니다."

두 번째 문장은 출력하지 않는다.
