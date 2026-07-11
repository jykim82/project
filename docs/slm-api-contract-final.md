# SLM API 규약 정의서 (최종본)

> Python FastAPI ↔ Next.js 프론트엔드 간 전체 인터페이스 규약
> 기준: example3.json (56 인텐트) + DB 스키마 분석

---

## 1. 인텐트 분류 총괄

### 56개 인텐트, 6가지 graph_type

| graph_type | 건수 | 설명 | Next.js 렌더링 |
|-----------|------|------|----------------|
| `none` | 26 | 텍스트 전용 | 마크다운 메시지 |
| `table` | 14 | 데이터 테이블 | `<DataTable />` |
| `diagram` | 8 | 위치도/계통도 | `<DiagramViewer />` |
| `document` | 4 | 매뉴얼/문서 | `<DocumentViewer />` |
| `plot` | 2 | 시계열 차트 | `<TrendChart />` (ECharts) |
| `graph_type` | 2 | ⚠️ 오타 추정 (주소정보) | `none`으로 처리 |

### table 하위 분류

| table_type | 건수 | 용도 |
|-----------|------|------|
| `equipment` | 3 | 설비 현황 (JSONB meta 기반) |
| `summary` | 11 | 조회 결과 요약 테이블 |

### plot 하위 분류

| plot_type | 건수 | 용도 |
|----------|------|------|
| `line` | 1 | 단일 시계열 라인 차트 |
| `multi_axis_line` | 1 | 아날로그+디지털 복합 차트 (이중 Y축) |

### diagram 하위 분류

| 패턴 | 건수 | 용도 |
|------|------|------|
| `*_LOCATION` | 4 | 시설 위치도 (이미지) |
| `*_NETWORK_DIAGRAM` | 4 | 용수 계통도 (system_diagram_url + facility_flow_path) |

### ⚠️ example3.json 구조 비일관성 (Python에서 정규화 필요)

| 필드 | answer_template 안 | answer_template 밖 | 해당 인텐트 |
|------|-------------------|-------------------|-----------|
| `recommend_questions` | 38개 | 11개 | FACILITY_FLOW_CURRENT_TABLE 등 table 계열 |
| `table_columns` | 0개 | 12개 | 전부 밖에 위치 |

→ **Python API에서 정규화하여 통일된 응답 구조로 반환**

---

## 2. DB 스키마 변경 (채팅 관련)

### 신규 컬럼

```sql
-- 시각화 데이터 저장 (히스토리에서 차트 재렌더링용)
ALTER TABLE tb_ai_chat_bot ADD COLUMN visual_data jsonb;

COMMENT ON COLUMN tb_ai_chat_bot.visual_data IS 
  'graph_type별 시각화 데이터 (table/plot/diagram/document). 히스토리 조회 시 차트 재렌더링용';

-- visual_data 검색용 인덱스 (graph_type으로 필터링 시)
CREATE INDEX idx_chat_bot_visual_type 
  ON tb_ai_chat_bot ((visual_data ->> 'type')) 
  WHERE visual_data IS NOT NULL;
```

### 최종 tb_ai_chat_bot 구조

```
tb_ai_chat_bot
├── ask_seq          (PK, 질문 시퀀스)
├── region           (PK)
├── user_id          (PK)
├── bot_msg          (text, 렌더링된 텍스트)
├── bot_at           (timestamptz) ← varchar에서 변환
├── references_data  (text/json, 참고 자료)
├── recquestions     (text/json, 추천 질문)
├── visual_data      (jsonb) ← 신규
├── created_at       (timestamptz)
└── updated_at       (timestamptz)
```

---

## 3. 통합 응답 스키마

### 3-1. 채팅 응답 (Python → Next.js)

```typescript
/** Python API가 반환하는 최종 응답 구조 (SSE 완료 후 DB 저장 단위) */
interface ChatResponse {
  // ── 메타 ──
  intent: string;                    // "RESERVOIR_OVERVIEW" 등
  ask_seq: number;

  // ── 텍스트 응답 (항상 존재) ──
  answer: {
    summary: string;                 // "신평 배수지 일반 현황은 다음과 같습니다."
    detail: DetailItem[];            // 상세 항목
    reference?: ReferenceBlock;      // 참고 자료 (선택)
    recommend_questions?: RecommendBlock;  // 추천 질문 (선택)
  };

  // ── 시각화 데이터 (graph_type !== "none"일 때) ──
  // → tb_ai_chat_bot.visual_data에 그대로 저장
  visual: VisualData;
}
```

### 3-2. VisualData 유니온 타입

```typescript
type VisualData =
  | { type: "none" }
  | { type: "table";    table: TableData }
  | { type: "plot";     plot: PlotData }
  | { type: "diagram";  diagram: DiagramData }
  | { type: "document"; document: DocumentData };
```

### 3-3. 공통 하위 타입

```typescript
interface DetailItem {
  prefix: string;       // "•", "-", "ㆍ", "1.", "" 등
  text: string;         // 템플릿 치환 완료된 텍스트
}

interface ReferenceBlock {
  title: string;        // "참고 자료"
  items: DetailItem[];
}

interface RecommendBlock {
  title: string;        // "추가로 추천 질의 사항입니다."
  items: DetailItem[];
}
```

### 3-4. TableData

```typescript
interface TableData {
  table_type: "equipment" | "summary";
  columns: TableColumn[];
  rows: Record<string, any>[];
}

interface TableColumn {
  key: string;          // DB 컬럼명 ("sitename", "alarm_msg" 등)
  label: string;        // 한글 표시명 ("현장명", "알람 메시지" 등)
  align?: "left" | "center" | "right";
  width?: number;
}
```

**Python 컬럼 매핑 테이블:**

```python
TABLE_COLUMN_MAP = {
    # 공통
    "no":                   {"label": "No.",         "align": "center"},
    "sitename":             {"label": "현장명",       "align": "left"},
    "facilitytype":         {"label": "시설유형",     "align": "center"},
    # 알람
    "alarm_msg":            {"label": "알람 메시지",   "align": "left"},
    "alarm_value":          {"label": "알람 값",      "align": "right"},
    # 시계열/태그
    "logtime":              {"label": "시간",         "align": "center"},
    "log_time":             {"label": "시간",         "align": "center"},
    "tagsn":                {"label": "태그",         "align": "center"},
    "datadesc":             {"label": "데이터 설명",   "align": "left"},
    "val":                  {"label": "값",           "align": "right"},
    # 결측 분석
    "log_date":             {"label": "일자",         "align": "center"},
    "total_good_cnt":       {"label": "정상 건수",    "align": "right"},
    "total_expect_cnt":     {"label": "예상 건수",    "align": "right"},
    "total_missing_cnt":    {"label": "결측 건수",    "align": "right"},
    "good_rate_pct":        {"label": "정상률(%)",    "align": "right"},
    "missing_rate_pct":     {"label": "결측률(%)",    "align": "right"},
    # 이상설비
    "cnt":                  {"label": "총 건수",      "align": "right"},
    "missing_cnt":          {"label": "이상 건수",    "align": "right"},
    "missing_sites":        {"label": "이상 현장",    "align": "left"},
    # 야간최소유량
    "out_sitename":         {"label": "현장명",       "align": "left"},
    "out_facilitytype":     {"label": "시설유형",     "align": "center"},
    "out_label":            {"label": "구분",         "align": "center"},
    "out_val":              {"label": "값",           "align": "right"},
}
```

### 3-5. PlotData

```typescript
interface PlotData {
  plot_type: "line" | "multi_axis_line";
  series: PlotSeries[];
  x_axis: string[];                // ISO 8601 시간 배열
  period: {
    from: string;                  // ISO 8601
    to: string;
    desc: string;                  // "최근 7일간 데이터입니다."
  };
}

interface PlotSeries {
  name: string;                    // "유입유량", "수위", "펌프상태" 등
  type: "line" | "step";          // step: 디지털(on/off) 데이터
  y_axis_index: number;            // 0: 좌측 Y축 (아날로그), 1: 우측 Y축 (디지털)
  unit: string;                    // "㎥/hr", "m", "kgf/㎠" 등
  data: (number | null)[];         // x_axis와 1:1 매핑, null=결측
}
```

### 3-6. DiagramData

```typescript
interface DiagramData {
  diagram_type: "location" | "network";

  // location: 시설 위치도
  image_url?: string;              // 정적 이미지 URL (/api/files/{file_id})
  install_location?: string;       // 설치 위치 텍스트

  // network: 용수 계통도
  system_diagram_url?: string;     // 계통도 이미지 URL
  facility_flow_path?: string;     // "정수장→배수지→가압장→..." 경로 텍스트
}
```

### 3-7. DocumentData

```typescript
interface DocumentData {
  doc_type: "manual" | "criteria";
  content: string;                 // 마크다운/HTML 본문 (manual_block 렌더링 결과)
  url?: string;                    // 원본 문서 URL (manual_url)
}
```

---

## 4. SSE 스트리밍 프로토콜

### 4-1. 이벤트 타입

| 이벤트 | 데이터 | 설명 | 빈도 |
|--------|--------|------|------|
| `token` | `{"text": "..."}` | 텍스트 토큰 | 반복 (답변 길이만큼) |
| `answer_complete` | `{"intent": "...", "ask_seq": N}` | 텍스트 완료 | 1회 |
| `visual` | `VisualData 전체 JSON` | 시각화 데이터 | 0~1회 (none이면 미전송) |
| `recommend` | `RecommendBlock JSON` | 추천 질문 | 0~1회 |
| `done` | `{}` | 스트림 종료 | 1회 |
| `error` | `{"code": "...", "message": "..."}` | 에러 | 0~1회 |

### 4-2. SSE 스트림 예시

**텍스트 전용 (graph_type: none):**
```
event: token
data: {"text": "신평"}

event: token
data: {"text": " 배수지 일반 현황은 다음과 같습니다.\n\n"}

event: token
data: {"text": "• 급수 대상 지역은 신평동입니다.\n"}

event: token
data: {"text": "• 급수 인구는 12,500명입니다.\n"}

event: answer_complete
data: {"intent": "RESERVOIR_OVERVIEW", "ask_seq": 42}

event: recommend
data: {"title": "추가로 추천 질의 사항입니다.", "items": [{"prefix": "1.", "text": "배수지 운영현황은?"}, {"prefix": "2.", "text": "배수지 설비현황은?"}]}

event: done
data: {}
```

**차트 포함 (graph_type: plot):**
```
event: token
data: {"text": "최근 7일간 신평 배수지 유량 트렌드입니다.\n\n"}

event: token
data: {"text": "• 신평 배수지 유량 트렌드 그래프입니다."}

event: answer_complete
data: {"intent": "FACILITY_TREND", "ask_seq": 43}

event: visual
data: {"type": "plot", "plot": {"plot_type": "line", "series": [{"name": "유출유량", "type": "line", "y_axis_index": 0, "unit": "㎥/hr", "data": [12.3, 11.8, ...]}], "x_axis": ["2026-02-03T00:00:00", ...], "period": {"from": "2026-02-03", "to": "2026-02-10", "desc": "최근 7일간 데이터입니다."}}}

event: recommend
data: {"title": "추가로 추천 질의입니다.", "items": [{"prefix": "1.", "text": "신평 배수지 수위 트렌드 그래프를 보여줘"}]}

event: done
data: {}
```

**테이블 포함 (graph_type: table):**
```
event: token
data: {"text": "현재 기준 진행중인 알람은 총 5건입니다.\n\n"}

event: token
data: {"text": "• 배수지 2건, 가압장 2건, 감압시설 1건"}

event: answer_complete
data: {"intent": "ONGOING_ALARM_STATUS", "ask_seq": 44}

event: visual
data: {"type": "table", "table": {"table_type": "summary", "columns": [{"key": "no", "label": "No.", "align": "center"}, {"key": "sitename", "label": "현장명", "align": "left"}, {"key": "facilitytype", "label": "시설유형", "align": "center"}, {"key": "alarm_msg", "label": "알람 메시지", "align": "left"}, {"key": "alarm_value", "label": "알람 값", "align": "right"}], "rows": [{"no": 1, "sitename": "신평", "facilitytype": "배수지", "alarm_msg": "수위 LL", "alarm_value": "1.2m"}]}}

event: recommend
data: {"title": "추가로 추천 질의입니다.", "items": [{"prefix": "1.", "text": "알람 발생 누적건수가 높은 순서대로 보여줘"}]}

event: done
data: {}
```

### 4-3. 전체 흐름

```
Client (Next.js)                    Server (Python FastAPI)
  │                                      │
  │  POST /api/chat/ask                  │
  │  Content-Type: application/json      │
  │  Accept: text/event-stream           │
  │  Authorization: Bearer {jwt}         │
  │  ──────────────────────────────────► │
  │                                      │  1. JWT 검증
  │                                      │  2. tb_ai_chat_ask INSERT
  │                                      │  3. SLM 인텐트 분류
  │                                      │  4. SQL 파라미터 추출 (sitename 등)
  │                                      │  5. SQL 실행 → 데이터 조회
  │                                      │  6. answer_template 치환
  │                                      │
  │  event: token (반복)                 │
  │  ◄────────────────────────────────── │  7. 텍스트 스트리밍
  │                                      │
  │  event: answer_complete              │
  │  ◄────────────────────────────────── │  8. 텍스트 완료
  │                                      │
  │  event: visual                       │
  │  ◄────────────────────────────────── │  9. 시각화 (type !== "none"일 때만)
  │                                      │
  │  event: recommend                    │
  │  ◄────────────────────────────────── │  10. 추천 질문 (있을 때만)
  │                                      │
  │  event: done                         │
  │  ◄────────────────────────────────── │  11. 스트림 종료
  │                                      │
  │                                      │  12. tb_ai_chat_bot INSERT
  │                                      │      (bot_msg + visual_data + references + recquestions)
```

---

## 5. Next.js 클라이언트 구현

### 5-1. SSE 수신 클라이언트

```typescript
// src/lib/chat-stream.ts

interface StreamCallbacks {
  onToken: (text: string) => void;
  onAnswerComplete: (data: { intent: string; ask_seq: number }) => void;
  onVisual: (visual: VisualData) => void;
  onRecommend: (recommend: RecommendBlock) => void;
  onDone: () => void;
  onError: (error: { code: string; message: string }) => void;
}

export async function streamChatResponse(
  request: ChatAskRequest,
  callbacks: StreamCallbacks
) {
  const response = await fetch('/api/proxy/chat/ask', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'text/event-stream',
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    callbacks.onError({
      code: 'HTTP_ERROR',
      message: `${response.status} ${response.statusText}`,
    });
    return;
  }

  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let currentEvent = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    for (const line of lines) {
      if (line.startsWith('event: ')) {
        currentEvent = line.slice(7).trim();
        continue;
      }

      if (line.startsWith('data: ') && currentEvent) {
        try {
          const payload = JSON.parse(line.slice(6));

          switch (currentEvent) {
            case 'token':
              callbacks.onToken(payload.text);
              break;
            case 'answer_complete':
              callbacks.onAnswerComplete(payload);
              break;
            case 'visual':
              callbacks.onVisual(payload);
              break;
            case 'recommend':
              callbacks.onRecommend(payload);
              break;
            case 'done':
              callbacks.onDone();
              break;
            case 'error':
              callbacks.onError(payload);
              break;
          }
        } catch (e) {
          // JSON 파싱 실패 시 무시
        }
        currentEvent = '';
      }
    }
  }
}
```

### 5-2. 봇 메시지 렌더링 컴포넌트

```tsx
// src/components/chat/BotMessage.tsx

interface BotMessageProps {
  answer: ChatResponse['answer'];
  visual?: VisualData;
  isStreaming?: boolean;
  streamingText?: string;
}

export function BotMessage({ answer, visual, isStreaming, streamingText }: BotMessageProps) {
  return (
    <div className="flex gap-3 py-4">
      <BotAvatar />

      <div className="flex-1 space-y-3">
        {/* 스트리밍 중이면 실시간 텍스트, 아니면 answer 렌더링 */}
        {isStreaming ? (
          <StreamingText text={streamingText} />
        ) : (
          <>
            <p className="font-medium">{answer.summary}</p>
            <DetailList items={answer.detail} />
          </>
        )}

        {/* 시각화 영역 */}
        {visual && visual.type !== 'none' && (
          <VisualRenderer visual={visual} />
        )}

        {/* 참고 자료 */}
        {answer.reference && (
          <ReferenceBox {...answer.reference} />
        )}

        {/* 추천 질문 */}
        {answer.recommend_questions && (
          <RecommendChips {...answer.recommend_questions} />
        )}
      </div>
    </div>
  );
}
```

### 5-3. 시각화 라우터 컴포넌트

```tsx
// src/components/chat/VisualRenderer.tsx

export function VisualRenderer({ visual }: { visual: VisualData }) {
  switch (visual.type) {
    case 'table':
      return (
        <ChatDataTable
          columns={visual.table.columns}
          rows={visual.table.rows}
          tableType={visual.table.table_type}
        />
      );

    case 'plot':
      return (
        <TrendChart
          plotType={visual.plot.plot_type}
          series={visual.plot.series}
          xAxis={visual.plot.x_axis}
          period={visual.plot.period}
        />
      );

    case 'diagram':
      return visual.diagram.diagram_type === 'location'
        ? <LocationViewer {...visual.diagram} />
        : <NetworkDiagram {...visual.diagram} />;

    case 'document':
      return (
        <DocumentViewer
          content={visual.document.content}
          url={visual.document.url}
        />
      );

    default:
      return null;
  }
}
```

### 5-4. ECharts 매핑

```typescript
// src/components/charts/TrendChart.tsx

function buildEChartsOption(plot: PlotData): EChartsOption {
  if (plot.plot_type === 'line') {
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
      legend: { data: plot.series.map(s => s.name) },
      xAxis: { type: 'time', data: plot.x_axis },
      yAxis: {
        type: 'value',
        name: plot.series[0]?.unit || '',
        axisLabel: { formatter: '{value}' },
      },
      series: plot.series.map(s => ({
        name: s.name,
        type: 'line',
        data: plot.x_axis.map((t, i) => [t, s.data[i]]),
        smooth: true,
        connectNulls: false,       // null 결측 표시
      })),
      dataZoom: [
        { type: 'slider', start: 0, end: 100 },
        { type: 'inside' },
      ],
    };
  }

  if (plot.plot_type === 'multi_axis_line') {
    const analog = plot.series.filter(s => s.type === 'line');
    const digital = plot.series.filter(s => s.type === 'step');

    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
      legend: { data: plot.series.map(s => s.name) },
      xAxis: { type: 'time', data: plot.x_axis },
      yAxis: [
        {
          type: 'value',
          name: analog[0]?.unit || '',
          position: 'left',
        },
        {
          type: 'value',
          name: '가동상태',
          position: 'right',
          min: 0, max: 1,
          axisLabel: { formatter: (v: number) => v === 1 ? 'ON' : 'OFF' },
        },
      ],
      series: [
        ...analog.map(s => ({
          name: s.name,
          type: 'line' as const,
          yAxisIndex: 0,
          data: plot.x_axis.map((t, i) => [t, s.data[i]]),
          smooth: true,
        })),
        ...digital.map(s => ({
          name: s.name,
          type: 'line' as const,
          step: 'end' as const,
          yAxisIndex: 1,
          data: plot.x_axis.map((t, i) => [t, s.data[i]]),
          areaStyle: { opacity: 0.15 },
          lineStyle: { type: 'dashed' as const },
        })),
      ],
      dataZoom: [
        { type: 'slider', start: 0, end: 100 },
        { type: 'inside' },
      ],
    };
  }

  return {};
}
```

---

## 6. Python API 구현 가이드

### 6-1. 채팅 응답 생성 파이프라인

```python
# app/services/chat_service.py

from fastapi.responses import StreamingResponse
import json, asyncio

async def process_chat(request: ChatAskRequest) -> StreamingResponse:
    return StreamingResponse(
        chat_stream(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def chat_stream(request: ChatAskRequest):
    """채팅 질문 처리 → SSE 스트림 제너레이터"""

    # 1. 질문 저장
    ask_seq = await save_ask(request)

    # 2. SLM 인텐트 분류
    intent_result = await classify_intent(request.message)
    intent = intent_result["intent"]
    params = intent_result["params"]  # sitename, facilitytype, from_ts, to_ts 등

    # 3. 인텐트 설정 로드
    config = INTENT_CONFIGS[intent]

    # 4. SQL 실행
    sql = build_sql(config["sql"], params)
    rows = await db.fetch_all(sql)

    # 5. answer 생성 (템플릿 치환)
    answer = build_answer(config, rows, params)

    # 6. 텍스트 스트리밍
    full_text = render_answer_text(answer)
    for token in tokenize(full_text):
        yield sse_event("token", {"text": token})
        await asyncio.sleep(0.02)

    yield sse_event("answer_complete", {"intent": intent, "ask_seq": ask_seq})

    # 7. 시각화 데이터
    visual = build_visual(config, rows, params)
    if visual["type"] != "none":
        yield sse_event("visual", visual)

    # 8. 추천 질문
    recommend = extract_recommend(config)
    if recommend:
        yield sse_event("recommend", recommend)

    # 9. 완료
    yield sse_event("done", {})

    # 10. 응답 저장 (visual_data 포함)
    await save_bot_response(
        ask_seq=ask_seq,
        region=request.region,
        user_id=request.user_id,
        bot_msg=full_text,
        references_data=json.dumps(answer.get("reference"), ensure_ascii=False),
        recquestions=json.dumps(recommend, ensure_ascii=False),
        visual_data=json.dumps(visual, ensure_ascii=False),
    )


def sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
```

### 6-2. 정규화 함수들

```python
def build_answer(config: dict, rows: list, params: dict) -> dict:
    """answer_template + DB 결과 → 통일된 answer 객체"""
    template = config["answer_template"]
    row = rows[0] if rows else {}
    merged = {**params, **dict(row)}

    return {
        "summary": safe_format(template["summary"], merged),
        "detail": [
            {"prefix": d["prefix"], "text": safe_format(d["text"], merged)}
            for d in template.get("detail", [])
            if safe_format(d["text"], merged).strip()
        ],
        "reference": format_block(template.get("reference"), merged),
    }


def extract_recommend(config: dict) -> dict | None:
    """recommend_questions를 answer_template 안팎 모두에서 추출 (정규화)"""
    # answer_template 안에 있는 경우
    rq = config.get("answer_template", {}).get("recommend_questions")
    # 밖에 있는 경우 (FACILITY_FLOW_CURRENT_TABLE 등)
    if not rq:
        rq = config.get("recommend_questions")
    return rq


def build_visual(config: dict, rows: list, params: dict) -> dict:
    """graph_type에 따라 visual 객체 생성"""
    graph_type = config.get("graph_type", "none")

    # graph_type이 "graph_type"인 오타 → none 처리
    if graph_type == "graph_type":
        graph_type = "none"

    if graph_type == "none":
        return {"type": "none"}

    elif graph_type == "table":
        # table_columns는 항상 config 최상위에 위치
        col_keys = config.get("table_columns", [])
        columns = [
            {"key": k, **TABLE_COLUMN_MAP.get(k, {"label": k, "align": "left"})}
            for k in col_keys
        ]
        return {
            "type": "table",
            "table": {
                "table_type": config.get("table_type", "summary"),
                "columns": columns,
                "rows": [dict(r) for r in rows],
            },
        }

    elif graph_type == "plot":
        return build_plot_visual(config, rows, params)

    elif graph_type == "diagram":
        return build_diagram_visual(config, rows, params)

    elif graph_type == "document":
        return build_document_visual(config, rows, params)

    return {"type": "none"}


def build_plot_visual(config: dict, rows: list, params: dict) -> dict:
    """시계열 차트 visual 생성"""
    from collections import defaultdict

    plot_type = config.get("plot_type", "line")
    x_axis_set = set()
    tag_groups = defaultdict(list)

    for row in rows:
        x_axis_set.add(row["log_time"].isoformat())
        tag_key = (row.get("tagsn"), row.get("datadesc"), row.get("unit", ""))
        tag_groups[tag_key].append(row)

    x_axis = sorted(x_axis_set)
    x_index = {t: i for i, t in enumerate(x_axis)}

    series = []
    for (tagsn, datadesc, unit), group_rows in tag_groups.items():
        data = [None] * len(x_axis)
        for r in group_rows:
            idx = x_index.get(r["log_time"].isoformat())
            if idx is not None:
                data[idx] = r.get("val")

        # 디지털 데이터 감지 (값이 0/1만 있으면 step)
        is_digital = all(v in (0, 1, None) for v in data)

        series.append({
            "name": datadesc or tagsn,
            "type": "step" if is_digital else "line",
            "y_axis_index": 1 if is_digital else 0,
            "unit": unit,
            "data": data,
        })

    from_ts = params.get("from_ts", x_axis[0] if x_axis else "")
    to_ts = params.get("to_ts", x_axis[-1] if x_axis else "")

    return {
        "type": "plot",
        "plot": {
            "plot_type": plot_type,
            "series": series,
            "x_axis": x_axis,
            "period": {
                "from": str(from_ts),
                "to": str(to_ts),
                "desc": config.get("default_period_desc", ""),
            },
        },
    }


def build_diagram_visual(config: dict, rows: list, params: dict) -> dict:
    """위치도/계통도 visual 생성"""
    row = rows[0] if rows else {}
    intent = config["intent"]

    if intent.endswith("_LOCATION"):
        return {
            "type": "diagram",
            "diagram": {
                "diagram_type": "location",
                "image_url": row.get("location_image_url"),
                "install_location": row.get("install_location"),
            },
        }
    else:  # NETWORK_DIAGRAM
        return {
            "type": "diagram",
            "diagram": {
                "diagram_type": "network",
                "system_diagram_url": row.get("system_diagram_url"),
                "facility_flow_path": row.get("facility_flow_path"),
            },
        }


def build_document_visual(config: dict, rows: list, params: dict) -> dict:
    """매뉴얼/문서 visual 생성"""
    row = rows[0] if rows else {}
    intent = config["intent"]

    doc_type = "criteria" if "CRITERIA" in intent else "manual"

    return {
        "type": "document",
        "document": {
            "doc_type": doc_type,
            "content": row.get("manual_block", ""),
            "url": row.get("manual_url"),
        },
    }


def safe_format(template: str, data: dict) -> str:
    """KeyError 없이 안전한 포맷팅"""
    try:
        return template.format(**data)
    except (KeyError, IndexError):
        return template
```

### 6-3. DB 저장

```python
async def save_bot_response(
    ask_seq: int,
    region: str,
    user_id: str,
    bot_msg: str,
    references_data: str | None,
    recquestions: str | None,
    visual_data: str | None,
):
    await db.execute("""
        INSERT INTO tb_ai_chat_bot
            (ask_seq, region, user_id, bot_msg, bot_at,
             references_data, recquestions, visual_data)
        VALUES
            ($1, $2, $3, $4, now(), $5, $6, $7::jsonb)
    """, ask_seq, region, user_id, bot_msg,
        references_data, recquestions, visual_data)
```

---

## 7. 메시지 히스토리 API

### GET `/api/chat/messages/{group_id}`

**Request:**
```
GET /api/chat/messages/{group_id}
Authorization: Bearer {jwt}
```

**Response:**
```typescript
interface ChatHistoryResponse {
  group_id: string;
  title: string;              // tb_ai_chat_ask_group.group_title
  messages: ChatMessage[];
}

interface ChatMessage {
  ask_seq: number;

  user: {
    message: string;
    images: FileInfo[];        // tb_ai_chat_ask_image → tb_file_storage
    ask_at: string;            // ISO 8601
  };

  bot: {
    answer: {
      summary: string;
      detail: DetailItem[];
      reference?: ReferenceBlock;
      recommend_questions?: RecommendBlock;
    };
    visual?: VisualData;       // tb_ai_chat_bot.visual_data (jsonb)
    images: FileInfo[];        // tb_ai_chat_bot_image → tb_file_storage
    bot_at: string;
  };
}

interface FileInfo {
  file_id: number;
  file_url: string;
  original_name: string;
  mime_type: string;
}
```

**Python 쿼리:**
```sql
SELECT
  a.ask_seq,
  a.ask_msg,
  a.ask_at,
  b.bot_msg,
  b.bot_at,
  b.references_data,
  b.recquestions,
  b.visual_data,
  -- 이미지 서브쿼리
  (SELECT json_agg(json_build_object(
      'file_id', f.file_id,
      'file_url', f.file_url,
      'original_name', f.original_name,
      'mime_type', f.mime_type
  ))
  FROM tb_ai_chat_ask_image ai
  JOIN tb_file_storage f ON f.file_id = ai.file_id
  WHERE ai.ask_seq = a.ask_seq
    AND ai.region = a.region
    AND ai.user_id = a.user_id
  ) AS ask_images,
  (SELECT json_agg(json_build_object(
      'file_id', f.file_id,
      'file_url', f.file_url,
      'original_name', f.original_name,
      'mime_type', f.mime_type
  ))
  FROM tb_ai_chat_bot_image bi
  JOIN tb_file_storage f ON f.file_id = bi.file_id
  WHERE bi.ask_seq = a.ask_seq
    AND bi.region = a.region
    AND bi.user_id = a.user_id
  ) AS bot_images
FROM tb_ai_chat_ask a
LEFT JOIN tb_ai_chat_bot b
  ON b.ask_seq = a.ask_seq
  AND b.region = a.region
  AND b.user_id = a.user_id
WHERE a.group_id = $1
  AND a.region = $2
  AND a.user_id = $3
ORDER BY a.ask_seq ASC;
```

---

## 8. 전체 API 엔드포인트

### 인증

| Method | Path | Request | Response | 비고 |
|--------|------|---------|----------|------|
| POST | `/api/auth/login` | `{region, user_id, password}` | `{token, refresh_token, user}` | bcrypt 검증 + 세션 생성 |
| POST | `/api/auth/logout` | `{session_id}` | `{ok}` | tb_user_session 만료 |
| POST | `/api/auth/refresh` | `{refresh_token}` | `{token}` | JWT 갱신 |
| GET | `/api/auth/me` | Bearer token | `{user, permissions, menus[]}` | 메뉴 트리 포함 |

### 채팅

| Method | Path | Request | Response | 비고 |
|--------|------|---------|----------|------|
| GET | `/api/chat/groups` | query: `region, user_id` | `{groups[]}` | del_yn='N' 필터 |
| POST | `/api/chat/groups` | `{region, user_id, title?}` | `{group_id}` | 새 세션 |
| DELETE | `/api/chat/groups/{id}` | - | `{ok}` | del_yn='Y' 소프트삭제 |
| PATCH | `/api/chat/groups/{id}` | `{title}` | `{ok}` | 제목 변경 |
| GET | `/api/chat/messages/{group_id}` | Bearer token | `ChatHistoryResponse` | visual_data 포함 |
| POST | `/api/chat/ask` | `ChatAskRequest` | SSE stream | 핵심 엔드포인트 |
| GET | `/api/chat/faq` | query: `region` | `{faqs[]}` | 활성 FAQ만 |
| POST | `/chat/feedback` | `FeedbackCreate` | `FeedbackRow` | 오분류 피드백 등록 (self-contained) |
| GET | `/chat/feedback` | query: `region, reviewed?, limit?` | `FeedbackRow[]` | 관리자 검토 목록 |
| PATCH | `/chat/feedback/{id}/review` | `{reviewed_by}` | `FeedbackRow` | 검토 완료 마킹 |
| POST | `/trend/explain` | `{tag_name, unit, from_ts, to_ts, min, max, avg, count, anomaly_count}` | `{summary, source:"llm"\|"fallback", llm_rejected?, violations?}` | AI 트렌드 요약 (할루시네이션 검증 + 템플릿 폴백) |
| POST | `/anomaly/explain` | `EquipmentDiagnosis` (+ `user_question?`) | `{summary, source:"llm"\|"fallback", llm_rejected?, violations?}` | 이상감지 설비 진단 → AI 원인 서술 (할루시네이션 검증 + 식별자 strip + 템플릿 폴백) |

### 파일

| Method | Path | Request | Response | 비고 |
|--------|------|---------|----------|------|
| POST | `/api/files/upload` | multipart/form-data | `{file_id, file_url}` | tb_file_storage INSERT |
| GET | `/api/files/{file_id}` | - | binary | 파일 다운로드/서빙 |

### 모니터링 (대시보드/차트 페이지용)

| Method | Path | Response | 데이터 소스 |
|--------|------|----------|-----------|
| GET | `/api/facilities/reservoirs` | 배수지 목록+상태 | v_reservoir_info_status |
| GET | `/api/facilities/boosters` | 가압장 목록+상태 | v_booster_station_info_status |
| GET | `/api/facilities/pressure` | 감압시설 목록+상태 | v_pressure_reducing_facility_info_status |
| GET | `/api/facilities/blocks` | 소블록 목록+상태 | v_block_info_status |
| GET | `/api/trend/{trend_id}` | 트렌드 데이터 | tb_trend_catalog → tb_tag_raw_data |
| GET | `/api/alarms/ongoing` | 진행중 알람 | v_ongoing_alarm |
| GET | `/api/alarms/history` | 알람 이력 | tb_alarm_log |
| GET | `/api/tags/status` | 태그 상태 | mv_tag_daily_status |
| GET | `/api/network/topology` | 네트워크 토폴로지 | v_network_path_trace_stop_local_with_status |

### 관리자

| Method | Path | 설명 | 대상 테이블 |
|--------|------|------|-----------|
| GET/POST/PUT/DELETE | `/api/admin/users` | 사용자 CRUD | tb_user |
| GET/POST/PUT/DELETE | `/api/admin/menus` | 메뉴 CRUD | tb_menu |
| PUT | `/api/admin/menus/order` | 메뉴 순서 변경 | tb_menu.menu_idx |
| GET/POST/PUT/DELETE | `/api/admin/auth` | 권한 그룹 CRUD | tb_auth |
| GET/PUT | `/api/admin/auth-menus/{auth_idn}` | 권한별 메뉴 할당 | tb_auth_menu |
| GET/POST/PUT/DELETE | `/api/admin/prompts` | 프롬프트 CRUD | tb_prompt_template |
| GET/POST/PUT/DELETE | `/api/admin/prompts/{id}/columns` | 프롬프트 컬럼 CRUD | tb_prompt_column |
| GET/POST/PUT/DELETE | `/api/admin/faq` | FAQ CRUD | tb_ai_chat_faq |
| GET/POST/PATCH/DELETE | `/admin/facility-alias` | 시설명 약칭 매핑 CRUD (런타임 리로드) | tb_facility_alias |
| GET | `/admin/equipment-mtbf` | 설비 MTBF 집계 (query: `days, sitename?, facilitytype?, limit`) | tb_equipment_alarm_report + tb_equipment_info |
| GET | `/admin/llm-narrative/stats` | LLM narrative 통과율·거부율·응답시간 통계 (query: `days`) | `logs/llm_narrative/*.jsonl` |
| POST | `/tags` | 태그 마스터 신규 등록 (tagsn UNIQUE) | tb_tag_info |

### AI 서술 (LLM + 할루시네이션 검증)

대부분의 엔드포인트는 다음 공통 응답 스키마를 사용한다:
`{summary: string, source: "llm"|"fallback"|"template", llm_rejected?: boolean, violations?: number[], context_fetch_ms?: number, llm_generate_ms?: number, context_used?: string[], ...}`

"수치 화이트리스트" 정책: 응답 텍스트에 허용되지 않은 숫자가 등장하면 할루시네이션으로 간주 → 템플릿 폴백으로 대체. `context_used`로 실제 반영된 컨텍스트 종류를 확인할 수 있다 (예: `["baseline_30d", "thresholds", "peers"]`).

`source` 값:
- `"llm"` — LLM 생성 + 수치 검증 통과
- `"fallback"` — LLM 호출 실패 또는 수치 검증 실패 → 결정적 템플릿 응답
- `"template"` — 애초에 LLM 호출 안 함 (예: 조회 결과 0건, 모든 row 정상). [E-023] Hybrid 설계 이후 도입

| Method | Path | Request | 비고 |
|--------|------|---------|------|
| POST | `/trend/explain` | `{tag_name, tagsn?, unit, from_ts, to_ts, min, max, avg, count, anomaly_count}` | 트렌드 구간 요약. baseline/임계값/**피어**(P2.3) 컨텍스트 자동 주입 |
| POST | `/anomaly/explain` | `EquipmentDiagnosis (+ user_question?)` | 이상감지 설비 단건 원인 서술 |
| POST | `/anomaly/scan-all/explain` | `{top_n?, sitename?, facilitytype?}` | 스캔 현황 **Hybrid 응답** [E-023]. scope 필터 [E-022] 지원. ↓ 별도 명세 |
| POST | `/equipment-mtbf/explain` | `{days=90, sitename?, facilitytype?, top_n=5}` | MTBF 최악 Top-N 설비 자연어 요약 |
| POST | `/tag/latest/explain` | `{tagsn, current_value, tag_name?, unit?}` | 단일 태그 현재값 → baseline·임계값·**피어**(P2.3) 대비 서술 |
| POST | `/network/upstream-fault/explain` | 본문 없음 (`{}`) | 상위 장비(SSLVPN/UTM) 장애 → 하위 LTE 모뎀 통신이상 연쇄를 실시간 조회 후 자연어로 서술 (P2.8). 응답에 `sslvpn_count`, `global_lte_down_pct` 추가 포함 |

**P2.3 피어 태그 비교 컨텍스트 (trend/explain + tag/latest/explain 공통):**
- 같은 `facilitytype` + 같은 측정 카테고리(datainfo에서 `수위/유량/압력/순시유량/...` 추출)의 다른 현장 태그를 **최대 5개** 선정
- 실측 태그만 대상 (`설정/알람/HH/LL/H/L/상태/고수위/저수위` 계열 datainfo 제외)
- 각 피어의 `cagg_5min_raw_stats_ai` 30일 baseline avg 수집 → LLM 프롬프트에 "피어 비교" 섹션 추가
- 피어 수치·시설명은 모두 허용 수치 화이트리스트에 포함, 식별자는 검증 시 strip 후 재검증 (오탐 방지)
- 응답 `context_used`에 `"peers"` 포함 시 피어 비교가 실제 반영됨을 의미

#### `POST /anomaly/scan-all/explain` 상세 ([E-022] / [E-023])

**Request body** (모두 옵셔널):
```json
{
  "top_n": 3,                  // 레거시 — Hybrid 이후 사용 안 함
  "sitename": "행정1수청",     // [E-022] scope 필터 — 정확 매칭
  "facilitytype": "소소블록"   // [E-022] scope 필터 — 정확 매칭
}
```

**Response (성공 — LLM 경로):**
```json
{
  "summary": "[중요 알람] 남산10 소블록의 ... 교차이상 판정되었습니다 (설비 장애).\n\n[유형별 현황] 설비 장애 112건 · 교차 검증 9건 · 데이터 품질 14건 · 값 이탈 31건 (총 298건 중)\n\n[설비 장애] 통신이상·UPS·펌프 등 설비 DI 직접 감지 (확정 사고)\n\n[점검 순서] ① 설비 장애 → ② 교차 검증 → ③ 데이터 품질 → ④ 값 이탈",
  "source": "llm",
  "category_counts": { "equip_fault": 112, "cross_check": 9, "data_quality": 14, "value_deviation": 31 },
  "total_rows": 298,
  "urgent_category": "equip_fault",
  "scope": "전체",
  "context_used": ["scan_cache"],
  "llm_generate_ms": 38888,
  "allowed_numbers_count": 7
}
```

**Response (template — 0건 또는 모두 정상):**
```json
{
  "summary": "행정1수청 소소블록에 현재 이상 탐지된 태그가 없습니다.",
  "source": "template",
  "scope": "행정1수청 소소블록",
  "total_rows": 0,
  "category_counts": { "equip_fault": 0, "cross_check": 0, "data_quality": 0, "value_deviation": 0 },
  "llm_generate_ms": 0
}
```

**스캔 대상 태그 필터 (ANOMALY_SCAN_ALL SQL `WHERE` — 2026-07-11):**
z-score 이상탐지는 **실측 아날로그 계측 태그만** 대상으로 한다.
- `ti.tagtype = 'Analog Input'` — 아날로그 계측만 (DI 신호는 설비 장애 감지로 별도 처리)
- `ti.datainfo NOT LIKE '%적산%'` — **적산(누적)** 은 단조 증가 누적값이라 z-score 무의미 → 제외
- `ti.datainfo NOT LIKE '%설정%'` — **설정값·설정압력**(알람 임계치 L/H/HH, 설정 상수)은
  실측이 아닌 **설정 상수**라 이상탐지 대상 아님 → 제외 (설정 태그 34개)
- 동일 필터를 z-score 계열 5개 인텐트에 공통 적용: `ANOMALY_SCAN_ALL`,
  `ANOMALY_FACILITY_DETAIL`, `ANOMALY_PREDICT`, `ANOMALY_COMPARE`, `ANOMALY_PATTERN`
- **원칙:** 신규 이상탐지 SQL 추가 시 적산·설정 제외 필터를 함께 넣을 것(모니터링
  실측값만 통계 이상 판정 대상).

> **UI — 시설별 이상 분포(AnomalyScanView):** '영향 센서 N개'(그룹 전체 수)와 목록
> (상위 3개)이 달라 보이지 않도록 3개 초과 시 '외 M개 더' 표기.

**`summary` 필드 포맷 (Hybrid markdown-lite, 4섹션, `\n\n` 구분):**
1. `[중요 알람] {LLM 1문장}` — 시설명·태그·수치·카테고리 라벨 포함 (*구 `[가장 위급]` — 2026-04-18 변경*)
2. `[유형별 현황] 설비 장애 N건 · 교차 검증 M건 · 데이터 품질 K건 · 값 이탈 L건 (총 T건 중)`
3. `[{가장 위급한 카테고리}] {정적 정의 문구}` — `CATEGORY_MEANINGS` 사전에서 주입
4. `[점검 순서] ① 설비 장애 → ② 교차 검증 → ③ 데이터 품질 → ④ 값 이탈` — 고정 순서

**카테고리 분류 규칙** (`scan_all_explain.py:_classify_row`):
- **`equip_fault`** = `equip_failure` 비어있지 않음 OR `comm_status == "통신장애"`
- **`cross_check`** = `verdict in ("교차이상", "교차주의", "복합이상")`
- **`data_quality`** = `recent_holding == "Y"`
- **`value_deviation`** = `verdict in ("이상", "주의")` AND 위 3개에 해당 안 됨

한 row가 여러 카테고리에 걸칠 수 있음 (예: 설비 장애 + 교차 검증). 단 `value_deviation`은 다른 카테고리가 없을 때만 부여.

**우선순위 및 정의 (점검 순서 결정 근거):**

| 순위 | 카테고리 | 정의 (`CATEGORY_MEANINGS`) | 근거 |
|---|---|---|---|
| ① | 설비 장애 | 통신이상·UPS·펌프 등 설비 DI 직접 감지 (확정 사고) | DI 신호로 확정된 사실. 즉시 출동 |
| ② | 교차 검증 | 상류 유입과 하류 유출의 수지 불일치 (누수·월류·계측 오류 의심) | 물리 피해 직결 가능성. 단 원인 판정 1단계 추가 |
| ③ | 데이터 품질 | 결측·정체·역전 데이터 (센서·통신 점검 필요) | 모니터링 무력화. 직접 피해 없으나 2차 위험 |
| ④ | 값 이탈 | 요일·시간대 기준 Z-Score 이탈 (통계적 경계, 오탐 가능) | 통계 경계 초과. 정상 운영 변동 가능성 가장 높음 |

**가장 위급한 1건 선택 알고리즘** (`_select_most_urgent`):
1. `CATEGORY_PRIORITY` 순서로 카테고리 순회
2. 해당 카테고리에 속한 row가 있으면 그 카테고리 내에서 정렬:
   - `verdict` 가중치 (`복합이상=10 > 교차이상=9 > 이상=8 > 교차주의=7 > 주의=6 > 정상=0`)
   - 동률 시 `|z_score|` 내림차순
3. 첫 번째 row 반환

**LLM 프롬프트 정책:**
- 프롬프트에 **단일 row 정보만 포함** (Top-N 목록 X)
- 출력 ~50~100 토큰 목표, "단 1문장" 강제, 끝에 카테고리 라벨 괄호로 명시
- `num_predict`는 `None` (모델 기본값) — gemma4가 chat 템플릿 토큰을 먼저 생성하다 budget 소진해 빈 응답 반환하는 동작 회피
- 허용 수치: top 1건의 `z_score`/`deviation_pct`/`current_val`/`mean_30d` + 프롬프트 상수 `0`/`1`/`30`

**운영팀 커스터마이징 가이드:**
- 카테고리 정의 문구 변경 → `slm/endpoints/scan_all_explain.py`의 `CATEGORY_MEANINGS` 사전 수정 (코드 1줄)
- 점검 순서 변경 → `CATEGORY_PRIORITY` 리스트 순서 변경 (코드 1줄)
- 새 카테고리 추가 → 4개 사전(`PRIORITY/LABELS/MEANINGS`) + `_classify_row` 분류 로직 추가

### 시설간 교차 검증 (ANOMALY_CROSS_FACILITY)

"교차 검증 결과 보여줘" 등 → 인과 인덱스(상하류) 기반으로 상류 가동인데 하류가
정지/급락한 흐름 불일치를 탐지 (SQL 미사용, `cross_facility_check_all`).

**응답 top-level 구조화 필드 (2026-07-11 추가):**
- `cross_facility_mismatches: CrossFacilityMismatch[]` — 불일치 목록
  - `upstream_sitename/facilitytype`, `downstream_sitename/facilitytype`
  - `upstream_active_pct`, `downstream_active_pct` (가동률 %)
  - `checks: { type, ... }[]` — 판정 근거. `type ∈ {active_ratio, downstream_zero,
    sudden_drop, recent_inactive, snapshot_zero, direction}`. 각 type 별 부가 필드
    (예: `downstream_zero → upstream_mean`, `sudden_drop → first/second_half_active_pct`)
- `cross_anomaly_count: number`
- 기존 flat 텍스트 `answer.detail`(cross_facility_scan_block)은 하위호환 유지하되
  프런트가 카드 렌더 시 억제

**프런트 렌더:** `CrossValidationList` — 상류→하류 흐름(✕▶) + 가동률 비교 바 +
진단 배지(공급 단절/인과 급락/방향 역전/가동률) + 핵심 지표 + 판정 근거 칩.
`BotMessage`(intent 게이트), `QuickAnalysisDialog`(대시보드 KPI 팝업),
`AnomalyScanView`(전체 스캔) 3곳 공용. 백엔드는 `process_sql_result` 의 해당
분기에서 `data["cross_facility_mismatches"]` 노출 → `build_success_response` 가
기존 kwarg 경로로 top-level 전달.

> **SSE 진행 표시 — 분류 지연 이슈와 해결 (2026-07-11):**
> - **증상:** "교차 검증 결과" 질의가 분류(classify) 스텝에서 ~10초 멈춘 뒤 나머지
>   스텝(추출→조회→렌더링)은 순식간에 통과. SSE 진행 표시는 정확했음 —
>   실제로 분류가 느렸던 것.
> - **원인:** `_classify_category`(Stage1 카테고리 분류)의 `common_keywords` 목록에
>   "교차 검증" 계열이 빠져 있어 keyword 단축 실패 → **SLM(gemma4:26b) 카테고리
>   분류로 폴백해 ~10초 소요**. (인텐트 단계 `_classify_intent` 에는 교차검증
>   키워드가 있어 조회 자체는 즉시였음 → 카테고리·인텐트 키워드 집합 불일치)
> - **해결:** `intent_classifier._classify_category` 의 `common_keywords` 에
>   `교차 검증/교차검증/시설간 불일치/유량 불일치/흐름 불일치/정합성` 추가 →
>   keyword 로 "공통" 즉시 확정, SLM 폴백 회피. **분류 ~10초 → 0.1~0.4초.**
> - **일반 원칙:** 새 인텐트 keyword 추가 시 **카테고리(Stage1)와 인텐트(Stage2)
>   양쪽 키워드 집합을 함께 갱신**해야 SLM 폴백 지연이 생기지 않는다.
> - 스피너·활성 스텝은 `.slm-live-*` 로 계속 동작해(§flow-diagram-mode-spec 7.16)
>   느린 구간에도 "멈춤" 오인을 방지.

### 설비 장애 현황 (EQUIPMENT_FAULT_STATUS)

"설비 장애 현황 보여줘" 등 → DI 신호로 직접 감지된 설비 고장(통신이상·UPS·펌프·
전원)만 분리 조회. **ANOMALY_SCAN_ALL 백그라운드 캐시(`_ANOMALY_SCAN_CACHE
.processed_data.equipment_failure_impacts`)를 재사용**(재계산 없음). 대시보드 KPI
"설비 장애" 카드가 이 인텐트로 연결(이전에는 "전체 센서 이상 스캔"과 동일 질의
버그 → 2026-07-11 분리).

**응답 top-level 필드:**
- `equipment_failure_impacts: EquipmentFailureImpact[]` — `{equipment_id,
  equipmenttype, sitename, facilitytype, failure_type(network_down|comm_error|
  equip_fault|power_fault), failure_detail, affected_tag_count,
  anomalous_tag_count, affected_tags[]}`
- `equipment_failure_count: number`

**프런트 렌더:** `EquipmentFaultList` — 유형 필터 칩 + 현장/설비 계층 + 유형 배지
(FLT/PWR/NET/COM) + 영향·이상 태그 수 + 더보기. `BotMessage`·`QuickAnalysisDialog`
공용, flat detail 억제.

**등록 체크리스트(신규 SQL-less 인텐트 공통):**
1. `example3.json` 인텐트 정의(questions=벡터 인덱스용, sql="", answer_template,
   graph_type="none")
2. `intent_classifier`: Stage1 `common_keywords` + Stage2 키워드 규칙(둘 다 —
   미동기 시 SLM 폴백 ~10초)
3. `ai_server._DYNAMIC_SQL_INTENTS` **및** `_DYNAMIC_SQL_INTENTS_STREAM` 에 추가
   (누락 시 SQL-less 특수 분기 스킵 → 빈 결과)
4. `ai_server` 두 핸들러(비-SSE·SSE)에 데이터 로드 분기 (`_ANOMALY_SCAN_CACHE`는
   `.get("processed_data")` 로 언랩)
5. `response_builder.process_sql_result` 분기: 구조화 데이터를 `data[...]` 로
   노출(generic build_success_response kwarg 로 top-level 전달) + flat detail 폴백
   (평문 — `_wrap_marker` 는 response_builder 스코프에 없음)

### 경보 캘린더

| Method | Path | Request | Response | 비고 |
|--------|------|---------|----------|------|
| GET | `/alarm/calendar` | query: `days=30, sitename?, facilitytype?, alarm_category?` | `{dates: [{date, total, critical, warning, ...}]}` | 달력 히트맵 UI용 집계 |

### 누수 CUSUM 경보

| Method | Path | Request | Response | 비고 |
|--------|------|---------|----------|------|
| GET | `/leak-cusum/alerts` | query: `region=R01, acknowledged?, limit=100` | `AlertRow[]` | CUSUM 이상 감지 이력 |
| PATCH | `/leak-cusum/alerts/{alert_id}/ack` | `{acknowledged_by, note?}` | `AlertRow` | 담당자 승인 마킹 |
| POST | `/leak-cusum/scan` | query: `region=R01, facilitytype=소블록, days=90` | `{scanned, new_alerts}` | 수동 스캔 트리거 |

### 채팅 FAQ 예시 (동적)

| Method | Path | Request | Response | 비고 |
|--------|------|---------|----------|------|
| GET | `/chat/faq/examples` | query: `region=R01, per_category=2` | `{categories: [{id, questions[]}]}` | 실제 데이터 있는 시설·태그만 샘플링해 반환. 실패 시 클라이언트가 정적 풀로 폴백 |

### 위기대응 (경보분석 / 다이어그램)

| Method | Path | Request | Response | 비고 |
|--------|------|---------|----------|------|
| GET | `/crisis/alarm-reports` | query: `region, facilitytype?, sitename?, status?` | `AlarmReport[]` | 경보발생이력 (상세 필드 일체) |
| GET | `/crisis/alarm-analysis` | query: `days=90 (7~365)` | `AlarmReportRecord[]` | 경보분석 목록 (`diagnosed_msg` 포함, `severity != '정상'`). days는 7~365 클램프, **기본 30→90으로 확장**해 옛 다이어그램 583건 노출 [E-018] |
| GET | `/crisis/alarm-analysis/detail` | query: `tagsn, alarm_start_time` | `AlarmReportRecord` | 단건 상세 (PK 조회) |
| GET | `/crisis/alarm-dashboard` | - | 대시보드 집계 | 위기대응 대시보드 카드용 |
| PUT | `/crisis/alarm-reports/confirm` | `{tagsn, alarm_start_time, alarm_confirm_yn, ...}` | `{ok}` | 경보 확인/해제 처리 |

#### `tb_equipment_alarm_report.diagnosed_msg` HTML 구조 ([E-018], [E-019])

`diagnosed_msg`는 Node-RED `slm-node-red` 컨테이너의 함수 노드(`820cf7cd8e67c2f9`, "HTML 위기대응 표시 (배수지 수위 + 검출단계 시각화)")가 생성하는 **완성된 HTML 문서**. 프런트엔드 `AlarmAnalysisDetail.tsx`의 `parseDiagnosedMsg()` 파서가 섹션·블록 단위로 추출해 React 컴포넌트로 재렌더링.

**최상위 구조:**
```html
<!doctype html><html><body>
  <article class="card">
    <div class="header">...</div>
    <section class="section"><span class="label red">1. 경보 경과</span> <ul>...</ul></section>
    <section class="section"><span class="label red">2. 경보등급분석</span> ...</section>
    <section class="section"><span class="label blue">3. 분석결과</span>
      <h3>(분석내용)</h3><p>...</p>
      <h3>(로직점검 프로세스)</h3>
      <div class="diagram-container">
        <div class="flow-row">
          <div class="flow-step">
            <div class="flow-box blue [detected|dimmed]" data-step="1">수위분석 알고리즘 시작</div>
            <div class="arrow"><svg/></div>
          </div>
          ... (총 15개 flow-step)
          <div class="arrow-down-connector">...</div>  <!-- row 구분자 -->
          ...
        </div>
      </div>
    </section>
    <section class="section"><span class="label blue">4. 발생원인</span> ...</section>
    <section class="section"><span class="label blue">5. 대응방안</span> ...</section>
    <section class="section"><span class="label green">6. 운영현황</span> <div class="info-box">...</div></section>
  </article>
</body></html>
```

**검출 단계 다이어그램 (배수지 수위 알람 전용, 15개 flow-box):**

| # | label | category |
|---|---|---|
| 1 | 수위분석 알고리즘 시작 (blue) | row 1 시작 |
| 2 | 헌팅여부 (green) | 수위 |
| 3 | 수위알림판별 (green) | 수위 |
| 4 | 펌프가동조건 (green) | 펌프 (보조) |
| 5 | 펌프가동여부 (green) | 펌프 (보조) |
| ↓ | arrow-down-connector | row 구분 |
| 6 | 용수공급시간 12hr기준판별 (green) | 운영 |
| 7 | 유입유출량 분석 (green) | 운영 |
| 8 | 가압장분석 알고리즘 시작 (blue) | 가압장 (보조) |
| 9 | 유입유출유량 분석 (green) | 운영 |
| 10 | 용수공급시간 12hr기준판별 (green) | 운영 |
| ↓ | arrow-down-connector | row 구분 |
| 11 | 탈설비 가동여부 (green) | 보조 진단 |
| 12 | 설정오류 분석 (green) | 보조 진단 |
| 13 | 흡수정여부 수위판단 (green) | 보조 진단 |
| 14 | 밸브진단 (유입유출,감압) (green) | 보조 진단 |
| 15 | 통신기 Ping (green) | 보조 진단 |

**검출 단계 동적 클래스 ([E-019]):**

- `class="flow-box COLOR detected"` — Node-RED `detectStep(diagnosed_cause, alarm_msg)` 헬퍼가 키워드 우선순위 매칭으로 결정한 1~15 박스. **단일 박스에만 부여**
- `class="flow-box COLOR dimmed"` — 검출된 박스 이후의 모든 박스 (실행되지 않은 단계). **회색 음영 + 50% 투명도**
- `data-step="N"` — 1~15 정수, 디버깅·테스트 보조

**Node-RED 키워드 우선순위 (높은 → 낮은):**
1. `ping|통신|네트워크 단절` → 15 (통신기 Ping)
2. `밸브` → 14
3. `흡수정` → 13
4. `설정오류|기준오류|임계값` → 12
5. `탈설비|탈수|탈염` → 11
6. `가압장.*펌프.*유량|가압장.*유입|가압장.*유출` → 9
7. `가압장.*분석|상위.*가압장` → 8
8. `유입.*유출|유출량.*많|유입량.*많|유출량.*증가|유출량.*감소` → 7 (가장 흔한 매칭)
9. `12hr|12시간|공급가능시간` → 6
10. `펌프.*가동.*여부|펌프.*동작|펌프.*가동중` → 5
11. `펌프.*가동.*조건|펌프.*조건` → 4
12. `HH|LL|수위.*임계|수위.*알림|상한|하한` → 3
13. `헌팅` → 2
14. (기본) → 7

**프런트 렌더링 (`DiagramFlow` 컴포넌트):**
- 검출 박스: `ring-2 ring-red-500 + scale-105 + shadow + border` (red glow + 5% 확대)
- Dimmed 박스: `opacity-30 grayscale`
- 컴포넌트 상단에 검출 단계 범례: "검출 단계: <label> (이후 단계는 회색 처리)"
- box `title`/`aria-label`로 tooltip + 스크린리더 지원

**알려진 한계:**
- **배수지/수위 카테고리 전용** — 가압장 수위, 네트워크, UPS, 펌프, 밸브 알람은 별도 함수에서 다이어그램 없는 단축 HTML 생성
- 휴리스틱 매칭 정확도 ~80%. 더 정확한 단계 추적은 상위 switch/function 노드에서 `msg.detection_step`을 직접 set하는 Phase 2 리팩토링 필요
- Node-RED 트리거가 `WHERE diagnosed_msg IS NULL`만 처리하므로 옛 알람의 소급 적용은 별도 백필 SQL 필요

---

## 9. 인텐트 전체 목록 (56개)

### none (26개) — 텍스트 전용

| # | Intent | 설명 |
|---|--------|------|
| 1 | RESERVOIR_OVERVIEW | 배수지 일반현황 |
| 2 | RESERVOIR_OPERATION_STATUS | 배수지 운영현황 |
| 3 | RESERVOIR_FACILITY_CAPACITY | 배수지 시설용량 |
| 4 | RESERVOIR_LEVEL_STATUS | 배수지 수위 현황 |
| 5 | RESERVOIR_LEVEL_HUNTING_CHECK | 배수지 수위 헌팅 점검 |
| 6 | RESERVOIR_SUPPLY_AVAILABLE_HOURS | 배수지 용수공급 가능시간 |
| 7 | TODAY_RESERVOIR_AVG_USAGE | 금일 배수지 평균사용량 (개별) |
| 8 | TODAY_RESERVOIR_AVG_USAGE_ALL | 금일 배수지 평균사용량 (전체) |
| 9 | TODAY_FLOW_ACCUMULATION | 금일 적산 현황 |
| 10 | TODAY_OUTFLOW_ALL_STATUS | 금일 전체 유출 현황 |
| 11 | BOOSTER_STATION_OVERVIEW | 가압장 일반현황 |
| 12 | BOOSTER_STATION_OPERATION_STATUS | 가압장 운영현황 |
| 13 | PRESSURE_REDUCING_FACILITY_OVERVIEW | 감압시설 일반현황 |
| 14 | PRESSURE_REDUCING_FACILITY_OPERATION_STATUS | 감압시설 운영현황 |
| 15 | BLOCK_OVERVIEW | 소블록 일반현황 |
| 16 | BLOCK_OPERATION_STATUS | 소블록 운영현황 |
| 17 | FACILITY_PRESSURE_STATUS | 시설 압력 현황 |
| 18 | NIGHT_MIN_FLOW_STATUS | 야간최소유량 현황 |
| 19 | FACILITY_COMMUNICATION_TOPOLOGY | 통신 토폴로지 |
| 20 | FACILITY_COMMUNICATION_STATUS | 통신 상태 |
| 21 | FACILITY_ADDRESS_INFO_BLOCK | 소블록 주소 정보 |
| 22 | FACILITY_ADDRESS_INFO_PRESSURE | 감압시설 주소 정보 |
| 23 | FACILITY_RECENT_ALARM | 최근 알람 |
| 24 | FACILITY_ALARM_TOP_COUNT | 알람 누적 건수 상위 |
| 25 | FACILITY_ALARM_CAUSE_DIAGNOSIS_RANK | 알람 원인 진단 순위 |
| 26 | FACILITY_TAG_LATEST_VALUE | 태그 최신값 조회 |

### table (14개) — 데이터 테이블

| # | Intent | table_type | 주요 컬럼 |
|---|--------|-----------|----------|
| 27 | RESERVOIR_EQUIPMENT_STATUS | equipment | (JSONB meta) |
| 28 | BOOSTER_STATION_EQUIPMENT_STATUS | equipment | (JSONB meta) |
| 29 | PRESSURE_REDUCING_FACILITY_EQUIPMENT_STATUS | equipment | (JSONB meta) |
| 30 | FACILITY_FLOW_CURRENT_TABLE | summary | logtime, tagsn, datadesc, val |
| 31 | FACILITY_VALVE_STATUS_CURRENT_TABLE | summary | logtime, tagsn, datadesc, val |
| 32 | FACILITY_ANALOG_TIMESERIES_TABLE | summary | logtime, tagsn, datadesc, val |
| 33 | FACILITY_FLOW_INSTANT_TIMESERIES_TABLE | summary | logtime, tagsn, datadesc, val |
| 34 | FACILITY_FLOW_ACCUMULATED_TIMESERIES_TABLE | summary | logtime, tagsn, datadesc, val |
| 35 | FACILITY_DIGITAL_STATUS_TIMESERIES_TABLE | summary | logtime, tagsn, datadesc, val |
| 36 | NIGHT_MIN_FLOW_SUMMARY_TABLE | summary | log_time, out_sitename, out_facilitytype, out_label, out_val |
| 37 | TAG_DAILY_MISSING_SUMMARY | summary | log_date, total_good_cnt, missing_rate_pct 등 |
| 38 | ONGOING_ALARM_STATUS | summary | no, sitename, facilitytype, alarm_msg, alarm_value |
| 39 | FACILITY_ABNORMAL_STATUS_SUMMARY | summary | facilitytype, cnt, missing_cnt, missing_sites |
| 40 | FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS | summary | (동적) |

### diagram (8개) — 위치도/계통도

| # | Intent | diagram_type | 데이터 |
|---|--------|-------------|--------|
| 41 | BLOCK_LOCATION | location | 이미지 URL |
| 42 | RESERVOIR_LOCATION | location | 이미지 URL |
| 43 | BOOSTER_STATION_LOCATION | location | 이미지 URL |
| 44 | PRESSURE_REDUCING_FACILITY_LOCATION | location | 이미지 URL |
| 45 | BLOCK_NETWORK_DIAGRAM | network | system_diagram_url + facility_flow_path |
| 46 | RESERVOIR_NETWORK_DIAGRAM | network | system_diagram_url + facility_flow_path |
| 47 | BOOSTER_STATION_NETWORK_DIAGRAM | network | system_diagram_url + facility_flow_path |
| 48 | PRESSURE_REDUCING_FACILITY_NETWORK_DIAGRAM | network | system_diagram_url + facility_flow_path |

### document (4개) — 매뉴얼/문서

| # | Intent | doc_type | 데이터 |
|---|--------|---------|--------|
| 49 | RESERVOIR_INITIAL_RESPONSE_MANUAL | manual | manual_url + manual_block |
| 50 | BOOSTER_STATION_INITIAL_RESPONSE_MANUAL | manual | manual_url + manual_block |
| 51 | PRESSURE_REDUCING_FACILITY_INITIAL_RESPONSE_MANUAL | manual | manual_url + manual_block |
| 52 | PRESSURE_REDUCING_FACILITY_CRITERIA | criteria | 감압 패턴/기준 텍스트 |

### plot (2개) — ECharts 차트

| # | Intent | plot_type | 데이터 |
|---|--------|----------|--------|
| 53 | FACILITY_TREND | line | fn_trend_period_summary |
| 54 | FACILITY_MIXED_TREND | multi_axis_line | 아날로그+디지털 UNION |

### ⚠️ 오타 (2개) — none으로 처리

| # | Intent | graph_type 원본 |
|---|--------|----------------|
| 55 | FACILITY_ADDRESS_INFO_RESERVOIR | "graph_type" (문자열 오타) |
| 56 | FACILITY_ADDRESS_INFO_BOOSTER | "graph_type" (문자열 오타) |

---

## 10. 에러 처리

### SSE 에러 이벤트

```
event: error
data: {"code": "INTENT_NOT_FOUND", "message": "질문을 이해하지 못했습니다. 다시 질문해 주세요."}
```

### 에러 코드

| code | HTTP | 설명 |
|------|------|------|
| `AUTH_EXPIRED` | 401 | JWT 만료 |
| `AUTH_INVALID` | 401 | 유효하지 않은 토큰 |
| `PERMISSION_DENIED` | 403 | 권한 없음 |
| `INTENT_NOT_FOUND` | 200 (SSE) | 인텐트 분류 실패 |
| `SQL_ERROR` | 200 (SSE) | 쿼리 실행 오류 |
| `NO_DATA` | 200 (SSE) | 조회 결과 없음 |
| `SLM_ERROR` | 200 (SSE) | SLM 추론 오류 |
| `FILE_NOT_FOUND` | 404 | 파일 없음 |
| `FILE_TOO_LARGE` | 413 | 파일 크기 초과 |
| `RATE_LIMIT` | 429 | 요청 제한 |
| `INTERNAL_ERROR` | 500 | 서버 내부 오류 |

### Next.js 에러 처리

```tsx
// SSE 에러 수신 시
callbacks.onError = (error) => {
  switch (error.code) {
    case 'AUTH_EXPIRED':
      router.push('/login');
      break;
    case 'INTENT_NOT_FOUND':
      appendBotMessage("죄송합니다. 질문을 이해하지 못했습니다. 다시 질문해 주세요.");
      break;
    case 'NO_DATA':
      appendBotMessage("조회된 데이터가 없습니다.");
      break;
    default:
      appendBotMessage(`오류가 발생했습니다: ${error.message}`);
      toast.error(error.message);
  }
};
```
