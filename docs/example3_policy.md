# example3_policy.md
example3.json 작성 및 운영 규약

---

## 1. 문서 목적

본 문서는 `example3.json`의 작성 규칙과 설계 철학을 정의한다.  
`example3.json`은 예시 파일이 아니라 **운영 정책 선언 파일**이다.

이 문서는 다음을 보장하기 위해 존재한다.

- INTENT 정의의 일관성
- SQL 실행의 안정성
- SLM 개입 범위의 제한
- 운영 중 구조 붕괴 방지

---

## 2. example3.json의 정체성

- LLM 프롬프트 파일이 아니다
- 자연어 해석 로직이 아니다
- SQL 자동 생성기가 아니다

`example3.json`은 다음만을 담당한다.

- 질의 의도(INTENT) 정의
- 허용되는 질문 표현 목록
- 실행 가능한 SQL 템플릿
- 출력 구조의 선언

---

## 3. 기본 구조

```json
{
  "intent": "INTENT_NAME",
  "questions": [],
  "sql": "",
  "meta": {},
  "answer_template": {},
  "graph_type": ""
}
