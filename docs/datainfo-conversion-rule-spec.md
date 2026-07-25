# DATAINFO 변환룰 사양 v1 — 구축 고도화 ①

> **상태:** v1 — **P1 구현 완료** + 태그 단위 정책(0118). **49룰 재현율 87.6%** (2026-07-25)
> **목적:** SCADA 원본 태그 설명(`datadesc`)을 SLM 조회 표준(`datainfo`)으로
> **룰 기반 자동 변환**한다. 신규 구축 시 수천 태그의 datainfo 수동 입력
> (오타 = 조회 실패)을 "자동 변환 + 예외만 검토" 워크플로로 대체.
> **관련:** `docs/slm-setup-phase-spec.md`(구축 단계),
> `docs/alarm-threshold-coverage.md`(datainfo 키워드 의존 실례)

---

## 1. 배경 — datainfo 는 조회 계약이다

- 채팅 인텐트 SQL LIKE·trend_kind 감지·품질 계층·임계 조사 등 시스템 전반이
  `datainfo` 키워드('유량·적산·수위·알람·SET' 등)에 의존.
- 실측 (2026-07-24, 2,700태그): datadesc↔datainfo **완전 동일 26%** —
  74%는 변환돼 있고 패턴이 규칙적. **기본 룰 12개만으로 59% 재현**,
  사전·문맥 룰 확장 시 90%+ 전망 (P1 시드 29룰 실측 74.1% — 잔여는
  원본 축약 등 exclude/override 대상).
- 대표 변환 유형: `#N`·`N호`→`N` / 영문 약어 표준화(FLT→FAULT, RUN→동작,
  AT→자동, REMOTE→원격) / `경보_`→`알람 ` / **조회 키워드 보강**
  ("순시유량(실선)"→"유량순시유량 실선" — '유량' 삽입) / 설비명 표준화
  ("펌프2호"→"가압펌프2", 시설유형 문맥 필요)

## 2. 룰 모델 — 변환 4계층 + 태그 정책 (우선순위 순차 적용)

| # | rule_type | 동작 | 예 |
|---|---|---|---|
| 1 | `regex` | 정규식 치환 | `#(\d)` → `\1` |
| 2 | `dict` | 단어 사전 치환 (word boundary) | `FLT` → `FAULT` |
| 3 | `context` | 조건부 치환 — 시설유형/tagtype 컨텍스트 매칭 시만 | 가압장+`펌프` → `가압펌프` |
| 4 | `override` | 태그 단위 최종 결과 고정 (tagsn 키) | 룰로 못 잡는 예외 |
| 5 | `exclude` | **태그 단위 변환 제외** — 현행 datainfo 유지, 일괄 적용에서도 스킵 (tagsn 키, Migration 0118) | 원본 축약("RE" 등)·룰이 오히려 훼손하는 태그 |

- 적용 순서: priority ASC → 같은 priority 는 id ASC. 태그 정책(override/exclude)은 변환 룰보다 항상 우선.
- region 별 룰셋 분리 (멀티테넌시) — **고객사 SCADA 명명 관례별 재사용 자산**.

## 3. 저장 — `tb_datainfo_rule` (Migration 0117)

```
rule_id serial PK, region, rule_type(regex|dict|context|override|exclude — 0118),
pattern, replacement, context_facilitytype, context_tagtype,
target_tagsn(override/exclude 용), priority int, enabled bool,
notes, updated_at, updated_by
```

## 4. API (`endpoints/datainfo_rules.py`)

| 메서드 | 경로 | 기능 |
|---|---|---|
| GET | `/setup/datainfo-rules` | 룰 목록 |
| POST/PUT/DELETE | `/setup/datainfo-rules` | 룰 CRUD |
| POST | `/setup/datainfo-rules/preview` | 전 태그(또는 필터) desc→룰 적용 결과 — 분류: `unchanged`(desc=info 동일) / `match`(변환=현 info — 룰 검증) / `diff`(≠현 info — 룰 후보) / `hard`(**룰 불가 의심 자동 판별** — 동일 desc 다중 매핑 or desc 에 없는 정보 추가. 제외/확정 지정 대상) / `excluded`(태그 정책 제외 — 현행 유지) / `manual`(override 확정) |
| POST | `/setup/datainfo-rules/apply` | 선택 tagsn 의 datainfo UPDATE (이력 로그 → 롤백 가능). **exclude 태그는 전 경로 스킵** |
| GET | `/setup/datainfo-rules/vocab` | **용어집** — 핵심 표준 단어 23종(조회 계약 근거 포함) + desc 영문 토큰 중 룰 미등록 약어 후보(빈도순, 클릭→룰 초안) |
| GET | `/setup/datainfo-rules/score` | **재현율 채점** — 기존 쌍 대상 일치율% (정책 태그는 분모 분리 — excluded/manual 별도 카운트) |

## 5. 구축 UI (`/setup/datainfo-rules`)

- 좌: 룰 테이블 (유형·패턴·치환·우선순위·on/off) + 추가/수정
- 상단: **재현율 스코어 카드** (룰 변경 즉시 재채점 — 룰셋 품질이 수치로 보임)
- 우/하: 미리보기 테이블 — before/after diff 하이라이트, 분류 필터
  (match=자동 적용 후보 / diff=검토 필요 / 변환 제외 / 수동 확정), 행별
  **[제외]/[확정] 버튼**으로 태그 정책 지정, 체크 선택 → 일괄 적용
- 신규 페이지 등록: `sidebar-menus.ts` + `tb_menu` INSERT (규칙 준수)

## 6. 안전 장치

- **기존 운영 태그는 diff 검토 후 선별 적용** — datainfo 변경은 조회 계약
  변경이라 전 시스템 파급. 자동 일괄은 `match`(현 info 재현) 확인용,
  `diff` 는 명시 선택만 적용.
- 적용 전 스냅샷: apply 시 (tagsn, old, new) 를 이력 테이블 기록 → 롤백 가능.
- 미커버 잔여는 수동 입력 유지 — 100% 자동화가 목표가 아니라 "예외만 검토".

## 7. Phase

- **P1 (본 구현):** Migration + 엔진 + CRUD/미리보기/적용/채점 API + 시드
  룰셋(실측 검증 유형) + setup UI + 재현율 실측 보고
- P2 (추후): CSV 신규 태그 온보딩 플로 통합 (desc 만 있는 태그 일괄 변환),
  lint (유량 태그 '유량' 키워드 부재 경고 등 조회 계약 검증)

## 변경 이력
- 2026-07-24 v1 — 실측 기반 설계 (12룰 59% 재현 확인)
- 2026-07-24 **태그 단위 정책 (exclude/확정) — Migration 0118** — 사용자
  지적("치환이 맞을 때와 적용 안 될 때의 구분·필터 필요") 반영. 미리보기
  분류에 `excluded`(변환 제외 — 현행 유지)·`manual`(override 확정) 추가,
  행별 [제외]/[확정] 버튼으로 지정. 재현율 분모에서 정책 태그 분리 집계.
  apply 는 exclude 태그를 어떤 경로로도 스킵. E2E: 지정→분류 전환→일괄
  적용 스킵(applied 0/skipped 1)→score excluded 1 검증.
- 2026-07-25 **룰 확장 3라운드 — 74.1%→87.6% (49룰)** — 잔여 diff 시그니처
  빈도 마이닝으로 룰 가능 유형 흡수: 밧데리/HIGH·LOW/LO·LOLO·HIHI 사전,
  언더스코어 해체(영문 포함 — F_OPEN 사전보다 늦은 priority 35 필수, 24로
  넣었다가 84.8→79.4 하락 후 교정), 유량 어순 접합, HH/LL DI 접미 보강,
  _FLT/_RUN 결합 약어(word boundary 미매치 보완). **잔여 336건은 룰 불가
  유형이 지배**: RE→자동(72)/RE→원격(10) 동일 축약의 이중 의미(override
  필수), 매방리 REMOTE 설비명 문맥(12), 행정 신설 수위→N지 정보 추가(26).
  스모크 16/16.
- 2026-07-25 **룰 불가 의심(hard) 자동 판별** — 사용자 질문("필터링 안되는
  건 별도 표시?")에서 diff 가 룰 후보와 불가 유형 혼재임을 확인. 판별 신호
  2종(동일 desc 다중 매핑 · info 전용 실질 토큰) 으로 hard 분리 —
  실측 diff 24 / hard 312 (합 336 정합). rose 칩 + 정책 버튼 대상.
- 2026-07-25 **용어집·후보 발굴 (구축 보조)** — 사용자 요청("규칙·핵심
  단어를 별도로 알 수 있게"). `/vocab`: ① 핵심 표준 단어 23종 — 각 단어가
  어떤 기능(인텐트 LIKE·품질 계층·임계 조사 등)에 의존하는지 근거 툴팁
  ② 미등록 약어 자동 발굴(사전·표준에 없는 desc 영문 토큰 빈도순 — 수동
  시그니처 마이닝의 제품화, 검증 9종: FAIL·KEPCO·FLOAT 등). 화면 접이식
  카드 + 약어 클릭 시 사전 룰 초안 프리필.
