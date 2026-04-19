# 채팅 사진 업로드 시나리오 통합 사양 (E-025 확장)

## 목적
사진 + 텍스트 조합을 **의도 기반**으로 라우팅하여, 사용자가 한 턴에 종합적으로 말하든 나눠서 말하든 동일한 결과(장애 등록 / 고장 진단)에 도달.

---

## 4가지 시나리오

### 1-a. 사진만 → 재질의 → 고장 등록
```
사용자: [사진 첨부]
AI   : "사진은 어떤 용도로 업로드 하신 건가요?
        ① 이 설비 고장으로 등록  ② 이 설비 상태 진단  ③ 그냥 참고"
사용자: "신평 배수지 PLC 고장이야 등록해줘"
AI   : [FaultRecordConfirmCard 렌더 — 사진 썸네일 포함]
       → 예/수정/취소
```

### 1-b. 사진만 → 재질의 → 진단 요청
```
사용자: [사진 첨부]
AI   : "사진은 어떤 용도로 업로드 하신 건가요?" (위와 동일)
사용자: "이 장비가 현재 정상이야?"
AI   : [VisionAdviceCard — 매뉴얼 RAG(고장 섹션 우선)]
```

### 2. 사진 + 고장 등록 텍스트 동시
```
사용자: [사진 첨부] + "신평 배수지 PLC 고장 등록해줘"
AI   : [FaultRecordConfirmCard — 사진 썸네일 포함]
       (VISION_DIAGNOSE는 백그라운드로 equipment_guess만 뽑아 draft 필드 보조)
```

### 3. 장애 등록만 (사진 없이)
```
사용자: "신평 배수지 PLC 현재 고장이야 등록해줘"
AI   : [FaultRecordConfirmCard — "사진 첨부" 버튼 노출]
       사용자가 버튼 누르면 사진 업로드 후 동일 draft에 URL 추가
```

### 4. 복합/멀티턴
- 2/3 두 플로우를 병행 지원
- pending_action이 **partial draft** 허용 → 후속 메시지로 누락 필드 보강

---

## 현재 시스템과의 갭

| 항목 | 현재 | 필요 | 비고 |
|------|------|------|------|
| 사진+텍스트 의도 분류 | 사진=무조건 VISION_DIAGNOSE 고정 | FAULT_KEYWORDS 감지 시 FAULT_RECORD_DRAFT 분기 | `vision_proxy.py` 수정 |
| 사진만 → 재질의 | 없음 (바로 진단) | 의도 분류 `pending_intent` + 재질의 카드 | 신규 UI `IntentClarifyCard` |
| pending_action 사진 URL | 컬럼 없음 | `draft.photo_urls[]` | migration |
| FaultRecordConfirmCard 사진 | 미지원 | 썸네일 슬롯 + "사진 추가" 버튼 | 기존 카드 확장 |
| partial draft 병합 | 신규 draft로 덮어씀 | 기존 draft + 새 필드 merge | `_upsert_pending_action` 로직 |
| 매뉴얼 RAG 고장 섹션 | `manual_type` = user_manual/catalog 2분류 | **결정 필요** (아래) | `tb_equipment_manual` 확장 |

---

## 결정 필요 사항

### D1. 진단(1-b) 시 "고장 부분" RAG 전략
아래 3개 중 선택:

- **A안 — 매뉴얼 재청킹**: 기존 매뉴얼 PDF를 "고장/트러블슈팅" 섹션만 별도 임베딩.
  - 장점: 기존 RAG와 자동 통합
  - 단점: 섹션 식별을 장비별로 반수작업 필요 (LS XGB는 "Chapter X 고장 진단" 패턴 탐색 가능)

- **B안 — UI 기반 케이스 등록**: 관리자 UI에서 `tb_fault_case(equipment_type, symptom, cause, action)` 테이블 직접 입력 → 임베딩.
  - 장점: 현장 노하우 즉시 반영, 수치/절차 포함 가능
  - 단점: 초기 데이터 구축 비용

- **C안 — 사용자가 케이스 템플릿 제공**: 사용자(담당자)가 엑셀/Markdown 케이스 정의 → 일괄 import.
  - 장점: A + B의 절충
  - 단점: 포맷·정합성 검증 로직 필요

**추천: A + B 병행**. A는 즉시 기존 매뉴얼 활용도 올리고, B는 장기 축적. C는 B의 입력 보조 수단.

### D2. 장비 커버리지
시나리오 검토에 언급된 6종 중:
- ✅ **매뉴얼 인덱스 有**: PLC(LS XGB/XGT), 인버터(LS G100/iS7), RTU(AC&T), 모뎀(AC&T)
- ❓ **매뉴얼 인덱스 無**: 수질계, 압력계
- **결정**: 수질계/압력계 매뉴얼 PDF 확보 경로는? (제조사별 Top 1~2만 등록하면 충분할지)

### D3. 1-a/1-b 재질의 UX
- "사진 용도?" 재질의 시:
  - **버튼 3개**(고장등록/진단/참고) — 명확하지만 자유 텍스트 제약
  - **자유 텍스트만** — 자연스럽지만 재분류 필요
  - **버튼 + 자유 텍스트 병행** ← 추천

### D4. 의도 분류 위치
- **A: vision_proxy 내부** — 사진과 함께 텍스트 오면 FAULT_KEYWORDS 매칭 후 fault_draft 호출
- **B: 별도 preprocessor intent 분류 레이어** — 재사용성 높지만 오버엔지니어링
- **추천 A** (E-025 범위 내에서 간단히)

### D5. pending_action partial 지원 시 세션 키
- 현재: `(session_id, user_id)` 로 1건 — 초안 덮어쓰기
- 제안: 동일 키에 `draft` JSONB merge (사진 URL 추가, 설비명 추가 등) → 확정 전까지 누적

---

## 구현 우선순위 (제안)

**P1** — 시나리오 2/3 (가장 흔함, 기존 플로우 최소 변경)
  - vision_proxy 에 FAULT_KEYWORDS 감지 → fault_draft 호출 분기
  - FaultRecordConfirmCard 에 사진 썸네일 슬롯 + "사진 추가" 버튼
  - pending_action `draft.photo_urls[]` 추가 (migration)

**P2** — 시나리오 1-a (멀티턴)
  - IntentClarifyCard (버튼 3개 + 텍스트)
  - pending_intent = "PHOTO_CLARIFY" state

**P3** — 시나리오 1-b + RAG 확장 (D1 결정 후)
  - A안: 매뉴얼 재청킹 스크립트
  - B안: `tb_fault_case` 테이블 + 관리자 UI
  - 진단 시 fault_case 우선 검색 → 부족하면 user_manual fallback

**P4** — 수질계/압력계 매뉴얼 추가 (D2 결정 후)

---

## 구현 진행 상황

- ✅ **P1** (시나리오 2·3) 완료 — `slm@e344be0` + `slm-dashboard@5517ca9` + `web@a2cc44c`
- ✅ **P2** (시나리오 1-a 용도 재질의) 완료 — `slm@753d76c` + `slm-dashboard@db447ff`
  - 사진만 업로드 → PHOTO_CLARIFY 즉시 응답 (0ms, VLM 스킵)
  - IntentClarifyCard: 썸네일 + 고장등록/진단/참고 버튼 + 자유 텍스트
  - 버튼 선택 시 `/chat/photo-action` → 결과를 카드 내부에서 후속 카드로 치환
- ✅ **P3** (시나리오 1-b RAG — 고장 케이스 DB + 엑셀 IMPORT/EXPORT + 관리자 UI + vision_agent 통합) 완료
  — `slm@8b8cf8e` + `slm-dashboard@f29520e` + migration `0048_fault_case.sql`
  - `tb_fault_case` 테이블 (증상/원인/조치/severity/참고URL/notes)
  - CRUD + snowflake-arctic-embed2 임베딩 (`data/fault_case_embeddings/*.npz`)
  - 엑셀 IMPORT/EXPORT + 템플릿 (`docs/examples/fault_case_template.xlsx`, 5건 샘플)
  - 관리자 UI `/setup/fault-cases` (테이블 + 필터 + 등록/수정/삭제 + 엑셀 버튼)
  - vision_agent `_FaultCaseIndex` + `/vision/fault-cases/reload` + DiagnoseResponse.fault_cases
  - E2E: "PLC ERR LED 빨강 점등" 쿼리 → 3 cases 매칭 (#1 PLC 0.571 최우선)
  - A안(매뉴얼 섹션 재청킹)은 별도 작업으로 분리 (운영 시 필요성 평가 후)
- ⏳ **P4** (수질계/압력계 매뉴얼 추가) — D2 결정 후

## 기존 사양 위배 여부

- ✅ **E-025 Zero-Hallucination 격리**: vision_advice 필드로만 참고 의견 전달 — 유지
- ✅ **FaultRecordConfirmCard 멀티턴 사양**: pending_action TTL 5분 — 유지
- ⚠️ **vision_proxy 의도 고정**: 변경됨 (사진 + 텍스트 FAULT_KEYWORDS 시 분기). E-025 P9에 "점검→장애→알람 단방향 플로우" 명시됐으나 본 변경은 그 플로우 **시작점을 더 자연스럽게** 만들 뿐 역행 아님.

변경 이력 저장 예정: `docs/error-management.md` E-030 (구현 후)
