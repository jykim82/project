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
