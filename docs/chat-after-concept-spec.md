# AI 채팅 답변 — AFTER 컨셉 (SLM Chat Readability)

**작성:** 2026-04-18
**상태:** Phase 1 (글로벌 cosmetic) + Phase 2(RESERVOIR_OVERVIEW 레퍼런스) 완료. 다른 intent 점진 확산 대기.

## 1. 목적

Claude 디자인 "SLM Chat Readability"의 AFTER 컨셉을 채팅 답변에 적용하여 **정보 위계·스캔성·단위·상태 가독성**을 향상.

## 2. AFTER 컨셉 7요소 매핑

| AFTER 요소 | 구현 위치 | 백엔드 필드 |
|---|---|---|
| ① 상단 KPI 3~4칸 | `KpiStrip` (BotMessage.tsx) | `answer.kpis[]` |
| ② 리드 문장 (숫자 하이라이트) | `parseStatusMarkers` (기존) | `answer.summary` |
| ③ 아이콘 + 점선 leader 리스트 | `DetailLeaderRow` | `answer.detail[].text` 에 " · " 구분자 |
| ④ 번호 인용 카드 | `ReferenceSection` 재디자인 | `answer.reference` (기존) |
| ⑤ 계속 물어보기 카드 | `RecommendSection` 재디자인 | `answer.recommend_questions` (기존) |
| ⑥ 신뢰도 게이지 | Phase 3 (TODO) | `answer.confidence?` |
| ⑦ 응답시간/참조건수 | Phase 3 (TODO) | `answer.render_time_ms?` |

## 3. 프런트 데이터 모델

```ts
// types/chat.ts
interface KpiItem {
  label: string;     // "공급가능"
  value: string;     // "24.0"
  unit?: string;     // "h"
  tone?: "neutral" | "ok" | "warn" | "critical" | "info";
  pill?: boolean;    // true면 알약 뱃지 (예: "정상 운영")
}

interface ChatResponse {
  answer: {
    summary: string;
    detail: DetailItem[];
    reference?: ReferenceBlock;
    recommend_questions?: RecommendBlock;
    kpis?: KpiItem[];   // 신규 (옵션)
  };
  ...
}
```

**하위호환:** `kpis` 없으면 상단 스트립 생략. detail text에 " · " 없으면 기존 스타일(prefix + text)로 렌더.

## 4. 백엔드 템플릿 확장

### 4.1 `render_answer_template` (response_builder.py)

```python
if "kpis" in template:
    rendered_kpis = []
    for kpi in template["kpis"]:
        value_rendered = _render_text(kpi.get("value", ""), data)
        if value_rendered is None: continue
        rendered_kpis.append({
            "label": kpi.get("label", ""),
            "value": str(value_rendered),
            "unit": kpi.get("unit", "") or "",
            "tone": kpi.get("tone", "neutral"),
            "pill": bool(kpi.get("pill", False)),
        })
    if rendered_kpis: result["kpis"] = rendered_kpis
```

value 렌더 실패(placeholder 누락) 시 해당 KPI 자동 스킵.

### 4.2 intent별 template 업데이트 방법

```json
{
  "answer_template": {
    "summary": "{sitename} 배수지는 공급가능시간 {total_supply_time}h로 ...",
    "kpis": [
      { "label": "공급가능", "value": "{total_supply_time}", "unit": "h", "tone": "warn" },
      { "label": "급수 인구", "value": "{supply_population}", "unit": "명", "tone": "warn" },
      { "label": "상태", "value": "{operating_status}", "tone": "ok", "pill": true }
    ],
    "detail": [
      { "prefix": "•", "text": "급수 대상 · {service_area}" },
      { "prefix": "•", "text": "급수 인구 · {supply_population}명" },
      ...
    ],
    "reference": { "title": "근거 자료", "items": [...] },
    "recommend_questions": { "title": "계속 물어보기", "items": [...] }
  }
}
```

## 5. 렌더 컴포넌트 명세

### 5.1 `KpiStrip` (상단 KPI 그리드)

- `grid gridTemplateColumns: repeat(N, minmax(0, 1fr))` — 카드 개수만큼 균등 분할
- 각 카드: `rounded-lg border bg-muted/30 px-3 py-2`
- label: `text-[10px] text-muted-foreground`
- value: `text-lg font-bold tabular-nums` + tone 색상
- unit: `ml-0.5 text-sm opacity-80`
- pill=true면 value가 `inline-flex rounded-md border px-2 py-0.5` 알약 뱃지로

### 5.2 `DetailLeaderRow` (점선 leader)

- " · " 첫 번째를 label/value 분리 경계로 사용
- label: left, muted color, shrink-0
- 가운데: `flex-1 border-b border-dashed border-border/50` (점선)
- value: right, font-medium foreground, parseStatusMarkers 적용

### 5.3 `ReferenceSection` (번호 인용)

- 컨테이너: `border-l-2 border-cyan-500/70 bg-cyan-500/5 rounded-lg border border-cyan-500/20`
- 제목: FileText 아이콘 + title + "N건" 알약
- 항목: 번호 뱃지(cyan 배경) + 텍스트 + → 화살표, hover 시 cyan 배경 강조

### 5.4 `RecommendSection` (팔로업 카드)

- 상단 구분선 + ArrowUpRight 아이콘 + title
- 세로 스택 (`flex-col gap-1.5`)
- 각 항목: full-width `rounded-md border bg-muted/20`, hover 시 `border-primary/40 bg-primary/5`, 오른쪽 → 화살표

## 6. 적용 완료 intent (레퍼런스)

- **RESERVOIR_OVERVIEW** (송산2산단공업 배수지 등) — KPI 3 / leader detail 2 + sentence 3 / 근거 자료 2 / 계속 물어보기 3

## 7. 글로벌 cosmetic 영향

모든 intent 답변이 자동으로 아래 효과를 받음:
- ReferenceSection 재디자인 (번호 뱃지 + 화살표 + cyan 좌측 accent)
- RecommendSection 재디자인 (세로 카드 스택 + 계속 물어보기 타이틀)
- detail.text에 " · " 포함 시 leader 스타일 (template에서 채택하면 즉시 AFTER)
- KPI는 백엔드 template에 `kpis` 필드 추가 시 자동 렌더

## 8. 점진 확산 가이드 (intent 단위 마이그레이션)

1. `example3.json`에서 해당 intent `answer_template` 찾기
2. `summary` → 리드 문장으로 재작성 (핵심 수치 2~3개 포함)
3. `kpis` 배열 추가 (최대 4칸, 핵심 지표)
4. `detail` 중 적합한 항목을 `"라벨 · 값"` 형식으로 변환
5. `reference.title` → "근거 자료", `recommend_questions.title` → "계속 물어보기"로 통일 (권장)
6. 검증: curl `/ask` 호출 후 응답 JSON 확인, 브라우저 채팅에서 실제 렌더 확인

## 9. 후속 Phase 3 (TODO)

- `answer.confidence?: number (0~1)` — 신뢰도 게이지 (푸터 프로그레스 바)
- `answer.render_time_ms?: number` — 응답시간 표시 ("응답 1.4s")
- `answer.reference_count?: number` — 참조 건수 (이미 reference.length로 계산 가능)
- Footer 메타 스트립: 타임스탬프 행에 추가 정보 통합

## 10. 관련 파일

- `slm/response_builder.py` — render_answer_template에 kpis 처리
- `slm/example3.json` — intent별 answer_template (RESERVOIR_OVERVIEW 개편)
- `slm-dashboard/src/lib/types/chat.ts` — KpiItem + answer.kpis
- `slm-dashboard/src/components/chat/BotMessage.tsx` — KpiStrip / DetailLeaderRow / ReferenceSection / RecommendSection 리디자인

## 11. 커밋

- `slm@c2ffdf7` — backend kpis 필드 + RESERVOIR_OVERVIEW 템플릿 개편
- `slm-dashboard@65940f6` — BotMessage 리라이트 (KPI/leader/citations/followup)
