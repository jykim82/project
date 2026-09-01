# 제한적 에이전트 루프 사양 v1 — 로드맵 A P1

시설 정밀 진단(`ANOMALY_FACILITY_DETAIL` — "신평 배수지 이상 진단해줘")에서
**룰 기반 라우터가 화이트리스트 조회 도구를 골라 실행**해 근거 팩을 만들고,
그 근거만으로 종합 소견을 조립해 카드에 표시한다.

**관련**: `slm-feature-roadmap-draft.md` §3.A·부록 B.1,
`docs/site-knowledge-spec.md`(도구 ③), `docs/intent-architecture-spec.md`

---

## 1. 원칙 (부록 B.1 채택)

- **도구 선택은 룰 기반 라우터** — LLM 이 도구를 고르지 않는다. 로컬 LLM 의
  도구 선택은 지연·비결정성을 더하고 §5.1(LLM as announcer)을 흔든다.
  각 도구는 `applicable(문맥)` 규칙으로 스스로 판단.
- **조회 계열만** (P1) — 제어·설정 변경 도구 없음. 향후 추가 시에도 제어
  계열은 운영자 승인 게이트 필수 (§3.A 원안 유지).
- **루프 제한 명시**: 도구 최대 4개 · 도구당 타임아웃 3초 · 전체 8초.
  초과 도구는 `timeout` 상태로 팩에 남긴다 (침묵 탈락 금지 — 감사 추적).
- **Zero-Hallucination**: P1 종합 소견은 **결정적 템플릿 조합** — 도구가
  반환한 수치·문장만 조립하고 생성 모델을 쓰지 않는다. LLM 문체 다듬기는
  P2 (기존 `shared.llm_narrative` 검증 인프라 재사용 전제).
- 근거 팩은 **어떤 도구가 무엇을 찾았는지** 그대로 노출 — "이 소견이 어디서
  왔는가"에 항상 답한다 (공공 납품 감사 추적).

## 2. 도구 화이트리스트 (P1 4종)

| 도구 | 내용 | applicable |
|---|---|---|
| `same_hour_history` | 이상 태그의 14일 동일 시간대 분포(p10~p90·중앙값) vs 현재값 — "지금이 평소 이 시간과 다른가" | 스캔 결과에 이상/주의 태그 존재 (최대 3태그) |
| `upstream_status` | 인접(상류) 시설 진행중 알람 — 원인이 상류에 있는가 | causal index 에 상류 존재 |
| `knowledge_cards` | 현장 지식 카드 매칭 (`site-knowledge-spec` 재사용) | sitename 존재 |
| `recent_actions` | 최근 30일 작업·조치 이력 (tb_task_master) | sitename 존재 |

## 3. 구조 — `slm/evidence_agent.py`

```
collect_evidence(conn_factory, ctx) -> {items: [...], summary, elapsed_ms}
  ctx = {sitename, facilitytype, anomaly_tags[(tagsn, datainfo, level)]}
  라우터: 도구별 applicable → 해당 도구만 병렬 실행 (to_thread + wait_for)
  팩 항목: {tool, label, status: ok|empty|timeout|error, summary(1줄), items[]}
```

- 채팅 통합: `AnomalyFacilityDetailHandler.post_process` 에서 수집,
  `response_extras` 로 `evidence_pack` 응답 병합. **실패·타임아웃이
  진단 응답 자체를 막지 않는다** (근거는 부가물).
- 종합 소견(`summary`): 결정적 조합 — 예) "죽동(배) 탁도 현재 1.2는 평소
  이 시간대(0.3~0.6)를 벗어남 · 상류 알람 없음 · 관련 현장 지식 1건 ·
  최근 30일 조치 2건".

## 4. UI — 채팅 시설 진단 카드 (`AnomalyDetailView`)

- 진단 테이블 아래 **"진단 근거" 섹션** — 도구별 행 (아이콘 + 라벨 +
  1줄 요약, 펼치면 상세 항목). timeout/error 도 "확인 실패"로 표시.
- 종합 소견은 카드 상단 요약 문장. `[AI 참고 의견]` violet 카드는 쓰지
  않는다 — P1 소견은 생성이 아니라 사실 조합이므로 DB 사실 톤 유지.
- visual_data 에 포함 → 히스토리 재렌더링 동일.

## 5. 이 사양이 하지 않는 것 (P1)

- LLM 도구 선택 없음 · 제어 도구 없음 · 다회 왕복(도구 결과를 보고 추가
  도구 결정) 없음 — 라우터는 1회 계획·병렬 실행. "제한적" 루프의 P1 해석

## 5.1 P2 확대 — 원인 분석 인텐트 (2026-07-29 완료)

`EvidencePackMixin` 으로 수집 로직 공용화 —
`RESERVOIR_LEVEL_CAUSE_ANALYSIS`(수위 하락 원인)에도 적용. z_score 컬럼이
없는 인텐트는 동일 시간대 도구만 자동 제외되고 나머지(상류 알람·지식
카드·조치 이력)가 동작한다. anomaly 카드 외 인텐트는 BotMessage 레벨에서
`EvidencePackSection` 렌더 (anomaly 카드는 기존 위치 — 이중 렌더 방지).

## 6. 후속 (P2+)

- ~~LLM 문체 서술~~ → **구현 완료 (2026-09-01)**: 결정적 summary 는 정본
  유지, LLM 서술은 별도 `narrative` 필드 — 수치 검증(shared.llm_narrative,
  사실 텍스트의 수치만 허용) 통과분만, 실패·타임아웃(12s)·빈 응답이면
  생략(결정적 소견만). 프런트는 "[AI 참고 의견]" violet 톤으로 구분
  (Zero-Hallucination). llm_narrative_log 관찰 연동. 구현 중 [E-063]
  (Ollama thinking 기본화로 전 생성 빈 응답) 발견·수정
- 결과 조건부 2차 도구 (예: 상류 알람 발견 시 상류의 상류 추가 조회)
- 원인 분석 계열 인텐트로 확대, 도구 실행 로그 텔레메트리
