너는 이 프로젝트의 Python 구현자다.
설계자나 정책 결정자가 아니다.

다음 문서들은 계약(contract)이며,
반드시 그대로 준수하여 코드를 작성해야 한다.

[필수 참조 문서]
- docs/ai_server_plan.md
- docs/example3_policy.md
- docs/db_jsonb_schema.md
- docs/python_jsonb_implementation_guide.md
- example3.json

---

[작업 목표]

- FastAPI 기반 단일 파일 ai_server.py를 생성(또는 전면 리팩토링)하라.
- example3.json을 로딩하여 질문을 INTENT에 매칭하고 SQL을 실행한 뒤,
  결과를 answer_template 기반으로 응답 JSON으로 반환하라.
- DB JSONB 컬럼(general_overview 등)은 SQL에서 가공하지 말고
  Python에서 전용 파서 함수로만 접근하라.

---

[엔드포인트]

- POST /ask
- 요청: { "user_question": "..." }
- 응답: example3.json 및 ai_server_plan.md에서 정의된 구조를 따른다.

---

[질문 처리 정책]

- 질문 정규화 후 example3.json.questions와 부분 포함 매칭으로 INTENT 결정
- sitename 누락 시 SQL 실행 금지
  → ERROR 응답 + 질의 보완 요청 메시지 반환
- facilitytype은 추론하지 않는다
  - block_level이 있으면 facilitytype = block_level
  - 없으면 배수지 / 가압장 / 감압시설 키워드만 허용
- datainfo는 압력 / 유량 / 수위 키워드 기반으로만 추출

---

[SQL 실행 규칙]

- SQL 템플릿이 빈 문자열이면 실행하지 않는다.
- psycopg2 사용
- 템플릿 변수 치환은 정책에 정의된 항목만 허용
- SQL 실행 오류, DB 접속 오류는 공통 오류 처리 규칙을 따른다.

---

[JSONB 처리 규칙 — 중요]

- JSONB 컬럼은 SQL에서 ->, ->> 접근을 사용하지 않는다.
- JSONB 구조 해석은 Python 전용 파서 함수에서만 수행한다.
- dict.get().get() 체인 사용 금지
- KeyError를 try/except로 무시하는 코드 금지
- 기본값으로 구조 오류를 숨기는 코드 금지
- db_jsonb_schema.md에 정의된 구조와 다를 경우
  → 즉시 ERROR 처리 (JSONB 스키마 위반)

---

[answer_template 처리 규칙 — 매우 중요]

- answer_template은 문장 “틀”이며, 로직을 포함하지 않는다.
- placeholder 치환은 Python에서 수행한다.

### null 처리 규칙

- placeholder 치환 값이
  null / None / 빈 문자열일 경우,
  해당 문장 라인은 **출력에서 완전히 제외한다**.

- "null입니다", "없음", "미정" 등의 문구를 생성하지 않는다.

- 하나의 문장에 여러 placeholder가 포함된 경우,
  그 중 하나라도 null이면 해당 문장은 제외한다.

### 예시

answer_template:
- "{sitename} 배수지는 총 {supply_count}지로 용수 공급 가능합니다."
- "{sitename} 배수지의 총 용수 공급 가능량은 {supply_capacity}입니다."

데이터:
- supply_count = 2
- supply_capacity = null

출력:
- 첫 번째 문장만 출력
- 두 번째 문장은 제외

---

[공통 오류 처리 규칙]

- 모든 오류는 서버 로그로 기록하고,
  동시에 API 응답으로 반환한다.
- 내부 오류 상세(스택 트레이스, SQL 원문 등)는
  API 응답에 포함하지 않는다.

오류 유형별 처리는 docs/ai_server_plan.md를 따른다.

---

[코드 품질 요구사항]

- 파일 상단에 서버의 설계 철학(판단 시스템 아님)을 주석으로 명시
- 함수 책임을 명확히 분리
  (정규화 / 매칭 / SQL 실행 / JSONB 파싱 / template 조립)
- 각 JSONB 파서 함수에는
  schema reference 주석 포함
- 접속 정보는 환경변수 기반으로 처리

---

[산출물]

- ai_server.py 전체 파일을 완성된 상태로 출력하라.
- 설계 제안만 하고 코드를 출력하지 않는 행위는 금지한다.
- 추가 파일을 생성하지 않는다.

---

[금지]

- 문서에 없는 규칙을 추론하여 추가하는 행위
- 질문 문장 단위 분기
- UI 편의를 이유로 응답 구조 변경
