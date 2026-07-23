# SLM Dashboard — 에러 관리 파일

> 발생한 에러, 원인, 해결책을 기록. 동일 에러 재발 방지용.

---

## 에러 목록

---

### [E-036] 설비 건강성 개요 KPI 전부 0 — 비상연락처 커넥션 누수로 DB 풀 고갈

- **날짜:** 2026-07-10
- **증상:** `/monitoring/equipment-health` 개요 KPI(총 장애/진행중/완료/고장/이상 등)
  가 실제 데이터가 있는데도 전부 0 표시.
- **원인 (2중):**
  1. **커넥션 누수** — `endpoints/alarm_contacts.py` 의 5개 핸들러 전부
     `conn = _get_conn()` 후 `finally: conn.close()` 누락. 특히 자주 호출되는
     `list_contacts`(비상연락처 목록, 알람 팝업 등에서 폴링)가 SELECT(암묵적
     트랜잭션) 후 커밋/반환 없이 종료 → 커넥션이 `idle in transaction` 으로
     누적. ~2시간에 6개 누수 → DB 풀(max=10) 고갈.
  2. **프런트 취약성** — equipment-health `load()` 가 `Promise.all` 로 4개
     엔드포인트를 묶어, 풀 고갈로 `mtbf` 가 500 나면 **전체 reject → summary
     포함 모두 미설정 → KPI 전부 0**.
- **해결:**
  1. alarm_contacts.py 5개 핸들러에 `finally: conn.close()` 추가(누수 차단).
     uvicorn --reload 로 반영, 누수 커넥션은 워커 재시작 시 해제.
  2. equipment-health `load()` 를 `Promise.allSettled` 로 변경 — 일부 실패해도
     나머지 KPI/차트 정상 표출(방어).
- **검증:** `idle in transaction` 6→0, mtbf 500→200, KPI 총장애 21·진행중 19·
  완료 2·고장 12·이상 7 정상 표출.
- **재발 방지:** DB 커넥션을 얻는 모든 핸들러는 반드시 `finally: conn.close()`
  (또는 컨텍스트 매니저)로 반환. 프런트 다중 병렬 로드는 `Promise.allSettled`
  로 부분 실패 격리.

---

### [E-035] 야간최소유량 트렌드 SSE 응답 28초 — 사전집계 미적용 + rows 초기화 덮임

- **날짜:** 2026-07-10
- **증상:** "신평 배수지 야간최소유량 트렌드 그래프를 보여줘" 채팅 응답이
  약 28초(SQL 23.6초) 소요. 진행 스피너가 오래 멈춰 보임.
- **원인 (2중 복합):**
  1. **SSE 경로 미최적화** — 비스트림 `/ask` 는 사전집계 테이블
     `tb_night_min_flow_daily` 인덱스 스캔(<0.5초)으로 최적화됐으나,
     프런트가 실제 쓰는 `/ask/stream`(SSE) 만 여전히 원시 하이퍼테이블
     실시간 함수 `fn_night_min_flow_summary`(43만행 60분 이동평균, ~23초)
     를 호출.
  2. **rows 초기화 순서 버그** — SSE 에서 NMF 사전집계 조회를 `커스텀
     핸들러용 rows 초기화(rows: list = [])` **앞**에 두면, 채운 rows 가
     초기화에 덮여 비워지고 → `if not rows:` 분기에서 선언 SQL
     (`fn_trend_period_summary`, 전체 3300행)이 재실행됨. `/ask` 는 이
     초기화 **뒤**에 채워 정상.
- **해결:**
  1. SSE NMF 블록을 `_execute_night_min_flow_query`(사전집계 테이블) 조회로
     전환, `/ask` 와 동일 fast-path 통일.
  2. 조회 코드를 `rows: list = []` 초기화 **이후**로 이동 (덮임 방지).
  3. 사전집계 테이블이 2026-03-21 이후 정체(갱신 잡 pg_cron 부재) →
     `backfill_night_min_flow('2026-03-22', CURRENT_DATE-1)` 로 현재까지 채움.
- **결과:** SQL 23.6초 → 0ms(사전집계), 합계 28초 → 5.7초(서버), wall-clock
  27.5초 → 11.2초. rows 3300 → 157(신평 정상).
- **재발방지:** 신규 커스텀 SQL 핸들러는 반드시 `rows: list = []` 초기화
  **이후** 채울 것. 사전집계 테이블(`tb_night_min_flow_daily`)은 일 1회
  `compute_night_min_flow()` 스케줄 필요(현재 pg_cron 부재 → 호스트 cron
  또는 백엔드 루프 검토 대상, review-items 등록).

---

### [E-034] LAN IP 접속 시 화면 자동 reload (~70초 주기) — HMR ws + Next.js strict origin

- **날짜:** 2026-06-08
- **증상:** 사용자가 `https://192.168.219.105:3000/admin/menus` 등 LAN IP 로
  접속 시 약 70~90초 주기로 페이지 전체 reload + 스크롤 위치 초기화.
  Chrome/Edge 동일. localhost 접속 시 발생 안 함.
- **원인 (2 중 복합):**
  1. **`next.config.ts` `allowedDevOrigins` 에 LAN IP 누락** — Next.js 16
     strict origin 정책. NEXTAUTH_URL host 와 다른 origin 에서 `/_next/*`
     리소스 요청 (HMR ws 포함) 차단. 콘솔 경고: "Blocked cross-origin
     request from 127.0.0.1 to /_next/* resource."
  2. **HTTPS 자기서명 cert SAN 에 LAN IP 누락** — `localhost` 만 포함.
     브라우저는 페이지 cert 경고는 무시 가능하나 wss reconnect 마다 검증
     실패 → Turbopack HMR ws 끊김 반복 → stale chunk 감지 시 페이지
     전체 reload.
  3. (부가) `NEXTAUTH_URL=https://localhost:3000` 고정 → LAN IP 와 callback
     URL 미스매치. SessionProvider 가 callback-url 쿠키 재발급 시도.
- **해결:**
  1. `next.config.ts` `allowedDevOrigins` 에 사설 IP 대역 와일드카드 등록:
     `"127.0.0.1"`, `"192.168.*.*"`, `"10.*.*.*"`, `"172.16~31.*.*"`.
     (commit slm-dashboard@037700f)
  2. cert 재발급 — `mkcert` 로 호스트 모든 LAN IP 를 SAN 에 포함.
     `cd certs/ && mkcert -key-file localhost-key.pem -cert-file localhost.pem
     localhost 127.0.0.1 ::1 192.168.x.x 10.x.x.x` (호스트 IP 로 교체).
     자세한 가이드: `certs/README.md`.
  3. `src/app/api/auth/[...nextauth]/route.ts` 가 request host 헤더 로
     NEXTAUTH_URL 런타임 오버라이드 + `useSecureCookies: true`.
     (commit slm-dashboard@705fe23)
- **재발 방지:**
  - 신규 머신 셋업 시 `certs/README.md` §"신규 머신 셋업" 따라 LAN IP
    포함하여 cert 발급.
  - 호스트 IP 변경 시 (DHCP / VPN 등) cert 재발급 필요 — frontend 재기동.
  - `allowedDevOrigins` 와일드카드는 사설망에 한정 (공인 IP 노출 환경에서는
    명시적 IP 화이트리스트 권장).
- **검증:** Playwright 로 localhost 환경 3+분 머물러도 reload 0회 (애초에
  미발생). 사용자 환경 LAN IP 에서 cert 재발급 + frontend 재기동 후 정상.
- **커밋:** `slm-dashboard@037700f`, `slm-dashboard@705fe23`
- **재발 (2026-07-10):** 호스트 LAN IP 가 DHCP 로 `192.168.50.84` 로 변경됐으나
  cert SAN 은 이전 IP 들만 포함 → 사용자(아이패드/PC) 가 `192.168.50.84:3000`
  접속 시 wss 재검증 실패 → 리로드 재발. 임시조치: cert 재발급
  (`mkcert ... localhost 127.0.0.1 ::1 192.168.50.84 192.168.10.11`) + frontend 재기동.
- **근본 해결 (2026-07-10):** cert SAN 에 IP 추가는 IP 바뀔 때마다 재발급이
  필요해 납품 부적합. **프로덕션 빌드 경로 구축**으로 근본 제거 —
  `next dev`(HMR) → `next build`+`next start`. HMR 웹소켓이 없어 인증서/IP 와
  무관하게 리로드 트리거가 존재하지 않음. 산출물: `Dockerfile.prod`,
  `docker-compose.prod.yml`, `certs/Caddyfile`, `docs/deploy-production.md`.
  프로덕션 이미지 빌드·기동 검증 완료(`✓ Ready 227ms`, HMR 로그 없음).
- **후속 (2026-06-08):** `/admin/menus` 메뉴 트리 탭의 숨김 토글이 localStorage
  기반이라 엔진 재기동 시 무효화 — `tb_menu.use_yn` DB 기반으로 전환.
  `MenuTreeView` 가 mount 시 `fetchMenuPermissions()` 로 DB 상태 동기화 +
  토글 시 `updateMenuVisibility(menu_idn, use_yn)` 호출. 백엔드 인프라는
  이미 존재 (auth_crud.py:818 PUT `/api/auth/menu-visibility`, `/auth/me`
  의 `m.use_yn='Y'` 필터). 별건 사양: 정적 `sidebarMenus` ↔ `tb_menu`
  정합 스크립트 추가 권고 (M200-15 누락 등).

---

### [E-033] 트렌드 쿼리 "카탈로그 미등록" — tb_trend_catalog 데이터 누락

- **날짜:** 2026-04-23
- **증상:** "죽동 배수지 수위 트렌드를 보여줘" 질의에 `"죽동 배수지에 '수위' 트렌드 카탈로그가 등록되지 않았습니다. 조회 가능 항목: 유량/유입유량/유출유량..."` 응답. 실제 `tb_tag_info` 에는 `죽동(배) 수위1/수위2` Analog Input 태그 존재.
- **원인:** `_execute_catalog_trend_query_inner` (sql_executor.py:826) 가 **tb_trend_catalog 기반**으로 태그를 조회. 6 배수지(죽동·송산2산단공업·송산2산단생활·합덕·합덕인더스·합덕일반)가 DB 시드 단계에서 trend_name='수위' 행 누락. tb_tag_info 에 실제 태그가 있어도 카탈로그 미등록이면 빈 결과 반환.
- **해결:** migration 0057 자동 백필 — tb_tag_info 에서 Analog Input 수위 태그를 갖는 배수지 중 카탈로그 미등록 시설을 찾아 items JSONB 자동 생성 (label=`{sitename} 배수지 수위{n}`, unit='m', data_category='수위'). 6건 INSERT.
- **재발 방지:**
  - 신규 시설 추가 시 trend_catalog 자동 시드 스크립트 (카테고리별 태그 패턴 기반)
  - `_execute_catalog_trend_query_inner` fallback: 카탈로그 미등록 시 tb_tag_info 직접 매칭으로 2차 조회 (TODO)
- **커밋:** `web@2838d63`

---

### [E-032] 배수지 일별 공급량 질의 실패 — sql_executor 함수 import 누락 (일괄)

- **날짜:** 2026-04-23
- **증상:** "한달간 전체 배수지의 일별 공급량을 표로 보여줘" 요청 시 에러 `name '_execute_reservoir_supply_query_with_conn' is not defined` → 프런트에서 "조회된 데이터가 없습니다" 로 표시되나 실제론 NameError
- **원인:** E-031 과 동일 패턴 — `ai_server.py` 가 `sql_executor.py` 에서 함수를 호출하나 import 목록에 누락. E-031 수정 후에도 `_execute_reservoir_supply_query_with_conn` 과 `_extract_alarm_level` 2개가 여전히 미import 상태였음
- **해결:**
  1. AST 분석으로 참조 vs import 불일치 전수 스캔 → 2개 추가 누락 함수 식별
  2. `from sql_executor import ..., _execute_reservoir_supply_query_with_conn`
  3. `from sql_executor import ..., _extract_alarm_level`
- **재발 방지:** 모듈 분리 시 import 누락 자동 탐지 스크립트 (AST 기반) — sql_executor 에 정의된 모든 `_execute_*`·`_extract_*` 함수 중 ai_server 에서 참조하면서 import 안 된 함수 나열
- **검증 스크립트 (재사용 가능):**
  ```python
  import ast, re
  src = open('ai_server.py').read()
  imported = {a.name for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.ImportFrom) and n.module in ('sql_executor','response_builder')
              for a in n.names}
  refs = set(re.findall(r'(_execute_\w+|_extract_\w+)', src))
  defined = set(re.findall(r'^def (_\w+)', open('sql_executor.py').read(), re.M))
  print("Missing imports:", sorted((refs - imported) & defined))
  ```
- **커밋:** `slm@3549b61`

---

### [E-031] 알람원인 순위 질의 무한 대기 — _ALARM_FILTER_RULES 상수 누락

- **날짜:** 2026-04-23
- **증상:** "천의리 배수지 통신 경보 발생원인 진단 순위 1순위부터 4순위까지 알려줘" 질의 시 SSE 스트림이 `extract` 단계 이후 멈춰 프런트가 영원히 로딩 상태. 사용자에게 에러 이벤트도 전달 안 됨.
- **원인:**
  1. `ai_server.py`가 `_extract_alarm_filter()` 를 호출하나 `from sql_executor import ...` 목록에 누락 (`NameError: name '_extract_alarm_filter' is not defined`)
  2. import 추가 후에도 `sql_executor._extract_alarm_filter` 내부 루프에서 참조하는 `_ALARM_FILTER_RULES` 상수 자체가 어디에도 정의 안 됨 (리팩토링 누락)
  3. SSE `event_generator` 안에서 예외 발생 시 `error` 이벤트 방출 없이 TaskGroup 에서 그대로 터지고 connection abort → 프런트는 타임아웃까지 대기
- **해결:**
  1. `ai_server.py` import 에 `_extract_alarm_filter` 추가
  2. `sql_executor.py` 에 `_ALARM_FILTER_RULES` 상수 정의 (통신/수위/압력/전원/펌프/밸브/수질/유량 8 카테고리 — q_keywords·category·msg_keywords·label 튜플)
  3. E2E: "천의리 배수지 통신 경보 발생원인 진단 순위" → 정상 응답 (모뎀 통신이상 10건 집계 반환)
- **재발 방지:** 모듈 분리 리팩토링 시 참조 상수/함수 모두 추적. SSE event_generator 내부의 예외는 `try/except` + `yield _sse_event("error", ...)` 패턴으로 사용자에게 통지해야 함 (E-027 과 동일 교훈 — 침묵 실패 금지).
- **커밋:** `slm@29176de`

---

### [E-030] 결측현황 응답에 `{sitename}` placeholder 원문 노출

- **날짜:** 2026-04-23
- **증상:** "성북2 배수지 결측현황" 질의 응답의 summary 가 렌더링된 한국어 문장이 아니라 `{sitename} {facilitytype} 데이터 결측 분석 결과입니다.` 템플릿 그대로 노출.
- **원인:** `TAG_DAILY_MISSING_SUMMARY` 는 `_DYNAMIC_SQL_INTENTS` 로 분류되어 `ai_server.py:4070-4094, 5579-5604` 의 청크 핸들러가 처리. 청크 핸들러가 `answer_template` 을 `isinstance(answer_template, dict) else {}` 로 그대로 반환 (`_tdm_rendered = answer_template`) — `render_answer_template()` 호출이 누락되어 placeholder 치환 안 됨.
- **해결:** 두 청크 경로 모두 `render_answer_template(answer_template, {**params, "total_count": str(_total)})` 호출 + `apply_corrections_to_answer(_tdm_rendered, params)` 추가.
- **재발 방지:** 청크/동적 SQL 핸들러를 새로 추가할 때 일반 `process_sql_result → render_answer_template` 경로와 동일하게 렌더링 수행 여부 체크. grep 패턴 `answer_template if isinstance` 로 추적.
- **커밋:** `slm@29176de`

---

### [E-029] 채팅 사진 업로드 → 비전 에이전트 404 (파일 경로 컨테이너/호스트 불일치)

- **날짜:** 2026-04-19
- **증상:** 채팅에서 사진 업로드 시 "비전 에이전트 오류가 발생했습니다" 또는 SSE `error` 이벤트. backend 로그:
  `chat attachment saved: /web/files/chat_attachments/<uuid>.jpg` → `POST http://host.docker.internal:8100/vision/diagnose "HTTP/1.1 404 Not Found"`
- **원인:**
  1. `endpoints/vision_proxy.py`가 Docker 컨테이너 내부 절대경로(`/web/files/chat_attachments/<uuid>.jpg`)를 `image_url` 필드로 vision_agent(호스트 프로세스)에 전달
  2. 호스트에서 실행되는 `vision_agent.py`는 같은 경로를 로컬 FS에서 찾지 못해 `_load_image_base64`가 HTTPException(404)
  3. docker-compose.dev.yml backend 서비스에 `chat_attachments`·`facility` 호스트 바인드 마운트 자체가 없어서 저장 파일이 컨테이너 레이어에만 존재 (호스트에서도 접근 불가)
- **해결:**
  1. `docker-compose.dev.yml` backend volumes에 `./files:/data/files` 추가, env `CHAT_ATTACHMENT_DIR=/data/files/chat_attachments`, `FACILITY_FILE_BASE_DIR=/data/files/facility` 주입
  2. `vision_proxy._save_upload` → `(local_path, url)` 튜플 반환, vision_agent 호출 시 `image_url`을 URL 형식 `/api/files/chat_attachments/<name>` 으로 전달 (절대경로 금지)
  3. `vision_agent._resolve_image_path`에 `/api/files/chat_attachments/` prefix 핸들러 추가
  4. `vision_agent.py` 기본 경로 1레벨 오차 수정 (`../../web` → `../web`, vision_agent.py는 `/Users/jykim/slm/` 기준)
- **재발 방지:** Docker-컨테이너-내-로컬경로를 호스트 프로세스에 넘기지 말 것. 파일 공유가 필요하면 **양쪽이 해석 가능한 URL 형식**(`/api/files/...`)으로 경계를 넘겨, 각자 env로 local path 해석. 컨테이너·호스트가 동일 파일을 보려면 **호스트 디렉터리를 바인드 마운트** 필수.
- **커밋:** `slm@6093733 + web@704d699`

---

### [E-028] DashboardShell SSR/client 렌더 트리 불일치로 hydration 실패

- **날짜:** 2026-04-18
- **증상:** 브라우저 콘솔 `Hydration failed because the server rendered HTML didn't match the client` + DOM diff에 `+ <main>` vs `- <Suspense>`
- **원인:** `components/layout/DashboardShell.tsx`에서 `mounted=false` 스켈레톤 분기는 `<main>{children}</main>`, `mounted=true && layout==="sidebar"` 분기는 `<main><div className="flex min-h-0 flex-1 flex-col w-full">{children}</div></main>` — children 래퍼 div 유무 차이로 Next 16 Turbopack의 Suspense 삽입 위치가 달라짐
- **해결:** `!mounted`와 `layout==="sidebar"` 분기 통합 (동일 JSX). SSR + 첫 hydration 모두 동일 트리. topbar는 mounted=true 확인 후 분기, localStorage의 topbar 선호는 hydration 완료 후 useEffect가 state 업데이트로 반영
- **재발 방지:** `use client` 컴포넌트에서 `mounted` 플래그로 분기할 때 **두 분기의 DOM 구조를 문자 단위로 일치**시킬 것. 한쪽에만 래퍼 div/span을 추가하면 Suspense·Streaming 경계 위치가 달라져 hydration mismatch
- **커밋:** `slm-dashboard@5fd39e7`

---

### [E-001] Next.js가 HTTP로 시작되어 HTTPS 접속 불가

| 항목 | 내용 |
|------|------|
| **날짜** | 2026-04-04 |
| **증상** | `https://localhost:3000` 접속 불가 |
| **원인** | `npm run dev` (HTTP) 로 시작 → 브라우저가 HTTPS 요청인데 HTTP 응답 |
| **에러 로그** | 브라우저: `ERR_SSL_PROTOCOL_ERROR` 또는 연결 거부 |
| **해결** | `npm run dev:https:fast` 사용 (HTTPS 인증서 포함 시작) |
| **재발 방지** | 반드시 `start-services.bat` 사용, 또는 `dev:https:fast` 스크립트 |

**올바른 시작 명령:**
```bash
# 개발 (캐시 유지, 빠른 시작)
npm run dev:https:fast

# 개발 (캐시 초기화)
npm run dev:https
```

---

### [E-002] 포트 충돌로 HTTPS가 3001로 밀림

| 항목 | 내용 |
|------|------|
| **날짜** | 2026-04-04 |
| **증상** | Next.js가 `https://localhost:3001`로 시작됨 |
| **원인** | 기존 HTTP 프로세스가 3000 점유 중 → HTTPS가 3001 사용 |
| **에러 로그** | `⚠ Port 3000 is in use by process 27280, using available port 3001 instead.` |
| **해결** | 기존 프로세스 종료 후 재시작 |

**해결 방법:**
```powershell
# Windows PowerShell로 포트 3000 점유 프로세스 종료
$pid = (Get-NetTCPConnection -LocalPort 3000).OwningProcess
Stop-Process -Id $pid -Force
# 또는 start-services.bat 실행 (자동으로 기존 프로세스 정리)
```

---

### [E-003] `.next/dev/lock` 파일 충돌

| 항목 | 내용 |
|------|------|
| **날짜** | 2026-04-04 |
| **증상** | `Unable to acquire lock at .next/dev/lock, is another instance of next dev running?` |
| **원인** | 복수의 `next dev` 프로세스가 동시에 실행 중 |
| **에러 로그** | `⨯ Unable to acquire lock at D:\web\slm-dashboard\slm-dashboard\.next\dev\lock` |
| **해결** | 모든 Next.js 프로세스 종료 후 재시작 |

**해결 방법:**
```powershell
# 모든 node 프로세스 종료 (주의: 다른 Node 프로세스도 종료됨)
Get-Process node -ErrorAction SilentlyContinue | Stop-Process -Force
# 재시작
cd D:\web\slm-dashboard\slm-dashboard && npm run dev:https:fast
```

---

### [E-004] AI Server (FastAPI) 미시작

| 항목 | 내용 |
|------|------|
| **날짜** | 2026-04-04 |
| **증상** | 채팅, 경보 분석, GIS 데이터 없음 |
| **원인** | AI Server가 실행 안 된 상태 |
| **확인** | `netstat -ano | findstr ":8000"` → 결과 없음 |
| **해결** | `cd D:\slm && python ai_server.py` 실행 |

---

### [E-005] GIS SHP 레이어가 마커(클러스터 동그라미) 위에 렌더됨

| 항목 | 내용 |
|------|------|
| **날짜** | 2026-04-04 |
| **증상** | GIS 지도에서 블록 경계(SHP) 폴리곤이 시설 마커(숫자 클러스터)를 가림 |
| **원인** | `GisShpLayer.tsx`에서 `beforeId="facility-circles"` 제거 후 SHP 레이어가 MapLibre 스택 최상단에 추가됨 |
| **에러** | 이전 오류: `Cannot add layer before non-existing layer "facility-circles"` (race condition) |
| **해결** | `GisFacilityMarkers.tsx`에서 `map.on("idle", bringMarkersToTop)` 이벤트로 마커 레이어를 항상 최상단으로 이동 |

**핵심 원칙**: GIS 레이어 z-order (아래→위 순서)
```
OSM 베이스 타일 → SHP 폴리곤(블록경계) → 알람 펄스 링 → 시설 심볼 → 시설 라벨 → 클러스터 원/숫자
```

**구현 위치**: `GisFacilityMarkers.tsx` → `useEffect` 내 `bringMarkersToTop()` 함수
```typescript
const MARKER_LAYER_IDS = [
  "alarm-pulse-ring", "facility-circles", "facility-labels",
  "facility-clusters", "facility-cluster-count"
];
function bringMarkersToTop() {
  for (const id of MARKER_LAYER_IDS) {
    if (map.getLayer(id)) { try { map.moveLayer(id); } catch {} }
  }
}
map.on("idle", bringMarkersToTop);
```

---

### [E-006] GIS SHP 레이어 beforeId race condition

| 항목 | 내용 |
|------|------|
| **날짜** | 2026-04-04 |
| **증상** | `Cannot add layer "shp-block_boundary-fill" before non-existing layer "facility-circles"` |
| **원인** | React 렌더 시점에 `facility-circles`가 존재하지 않거나, MapLibre 스타일 재로드 시 레이어 임시 제거 |
| **해결** | `GisShpLayer.tsx`에서 `beforeId` 제거 + `GisFacilityMarkers`의 idle 이벤트로 z-order 보장 |

---

### [E-007] WSL2 Docker 포트포워딩 깨짐 — localhost:5433 연결 거부

| 항목 | 내용 |
|------|------|
| **날짜** | 2026-04-08 |
| **증상** | AI Server 기동 시 `Connection refused (0x0000274D/10061) port 5433` |
| **원인** | WSL2 Docker 컨테이너 포트(5433)가 Windows `localhost` 포트포워딩 없이 실행 중. 절전/재시작 후 Windows Host↔WSL2 NAT 포워딩이 깨짐 |
| **에러 로그** | `psycopg2.OperationalError: connection to server at "localhost" (::1), port 5433 failed` |
| **확인 방법** | WSL에서는 `nc -zv localhost 5433` 성공, Windows PowerShell에서 `Test-NetConnection localhost 5433` 실패 |
| **해결** | WSL2 실제 IP 사용: `wsl -d Ubuntu -- ip route get 1.1.1.1 \| awk '/src/{print $7}'` → 해당 IP(예: 172.27.194.21)로 DB_HOST 지정 |

**해결 방법:**
```powershell
# WSL2 IP 확인
wsl -d Ubuntu -- ip route get 1.1.1.1

# AI Server 환경변수로 우회 기동
set DB_HOST=172.27.194.21
python D:\slm\ai_server.py
```

**근본 해결**: `start-services.bat` 실행 — WSL2 IP를 자동 감지하여 `DB_HOST` 환경변수 주입.

**핵심 원칙**:
- Docker가 WSL2 backend인 경우, Windows 프로세스에서 `localhost:5433`이 아닌 **WSL2 IP:5433**으로 접속
- WSL2 IP는 재부팅 시 변경될 수 있으므로 `start-services.bat`이 매번 자동 감지

---

### [E-008] AI Server DB 풀 초기화 실패 시 서버 전체 기동 중단

| 항목 | 내용 |
|------|------|
| **날짜** | 2026-04-08 |
| **증상** | DB 연결 불가 시 `ERROR: Application startup failed. Exiting.` — 서버가 아예 뜨지 않음 |
| **원인** | `_init_db_pool()` 에서 `ThreadedConnectionPool` 생성 실패 시 예외가 lifespan까지 전파 |
| **해결** | `_init_db_pool()` try/except 추가 — 풀 실패 시 WARNING 출력 후 직접 연결 폴백 모드로 계속 기동 |
| **수정 파일** | `D:\slm\ai_server.py` — `_init_db_pool()` 함수 및 `lifespan()` |

---

### [E-009] Windows localhost → IPv6(::1) 해석으로 구버전 AI Server 접속

| 항목 | 내용 |
|------|------|
| **날짜** | 2026-04-08 |
| **증상** | ANOMALY_FACILITY_DETAIL 인텐트 5개 시설 HTTP 500 반환. `_ask_inner` 디버그 파일 미생성 |
| **원인** | Windows에서 `localhost` → `::1` (IPv6) 해석 → 구버전 서버(PID 15184, `[::]:8000`)로 라우팅. 신버전 서버는 `0.0.0.0:8000`(PID 35396)에 독립 기동 중 |
| **에러 로그** | 구버전 로그에 없음. 신버전 서버에서는 `propagation_trace`/`ip_address` 버그 수정 완료 |
| **확인 방법** | `http://127.0.0.1:8000/health` vs `http://[::1]:8000/health` → 응답 `active_sessions` 다름 |
| **해결** | PID 15184 강제 종료 + `.env.local` `NEXT_PUBLIC_API_URL=http://127.0.0.1:8000` 변경 |

**재발 방지**:
- `start-services.bat`: `:8000` 포트 프로세스 전체 종료 후 2초 대기 → 신규 서버 기동
- `.env.local`: `http://localhost:8000` 대신 `http://127.0.0.1:8000` 사용

---

### [E-010] IForest 학습 실패: `date_trunc('5 minutes', ...)` SQL 오류

| 항목 | 내용 |
|------|------|
| **날짜** | 2026-04-08 |
| **증상** | `IForest 학습 실패: unit "5 minutes" not recognized for type timestamp with time zone` |
| **원인** | `anomaly_iforest.py` `_TRAIN_SQL_FACILITY`에서 `date_trunc('5 minutes', c.bucket)` 사용 — PostgreSQL `date_trunc`은 `'minute'` 단위만 지원, `'5 minutes'`는 TimescaleDB `time_bucket`만 가능 |
| **해결** | `date_trunc('5 minutes', c.bucket)` → `time_bucket('5 minutes', c.bucket)` 변경 |
| **수정 파일** | `D:\slm\anomaly_iforest.py` line 76 |

---

### [E-011] ANOMALY_SCAN_ALL 캐시 미생성 — latest CTE 시간창 미스

| 항목 | 내용 |
|------|------|
| **날짜** | 2026-04-10 |
| **증상** | 서버 재기동 후 종합 현황판 데이터 없음, `_ANOMALY_SCAN_CACHE` 비어 있음, 로그에 "⏱ SQL ...ms 0행" |
| **원인** | ANOMALY_SCAN_ALL SQL의 `latest` CTE가 `bucket >= now() - interval '3 hours'` 고정 사용 — DB 데이터가 3시간 이상 오래되면(시뮬레이션 데이터, 새벽 등) CTE 결과 0행 → 전체 JOIN 0행 반환 |
| **해결** | `_compute_anomaly_scan_all()`에서 SQL 실행 전 `max(bucket)` 확인, 1시간 이상 오래됐으면 `latest`/`recent_holding` CTE의 시간창을 max_bucket 기준으로 동적 조정 |
| **수정 파일** | `D:\slm\ai_server.py` `_compute_anomaly_scan_all()` (sql_combined 생성 직후) |
| **재발 방지** | 실시간 데이터 중단 상황(시스템 점검, DB 복구 후 등)에서도 최근 유효 데이터로 캐시 생성 |

---

### [E-012] AI Server DB 연결 실패 — C:\Python313 psycopg2의 localhost→::1 해석

| 항목 | 내용 |
|------|------|
| **날짜** | 2026-04-10 |
| **증상** | `start-services.bat` 없이 직접 `C:\Python313\python.exe ai_server.py` 실행 시 `fe_sendauth: no password supplied` 오류 |
| **원인** | 1) venv Python의 `python-dotenv` 미설치 → .env 로드 실패 → DB_PASSWORD="" → 인증 실패 / 2) C:\Python313 psycopg2는 `localhost`를 `::1`(IPv6)로 해석 — Docker DB는 IPv4만 리슨 |
| **해결** | `D:\slm\.env`의 `DB_HOST=localhost` → `DB_HOST=127.0.0.1` 변경 (IPv4 명시) |
| **수정 파일** | `D:\slm\.env` |
| **재발 방지** | .env는 항상 `DB_HOST=127.0.0.1` 유지. start-services.bat은 WSL_IP로 오버라이드하므로 영향 없음 |

---

### [E-014] 배수지 모니터링 빈 차트 + Node-RED DB 접속 정보 오류 + 테스트 환경 데이터 수집 누락

| 항목 | 내용 |
|------|------|
| **날짜** | 2026-04-12 |
| **증상** | `/monitoring/reservoir` 등 배수지/가압장/블록 모니터링 페이지에서 "최근 24시간" 차트가 전부 비어 있음. 로그인/catalog/catalogs 엔드포인트는 200 OK지만 `/trend/data`가 `total_points=0` 반환 |
| **원인** | 3단계 복합 문제:<br>1) **Node-RED DB 접속 정보 오류** — `flows.json`의 `postgreSQLConfig[71827310c941a9d1]`가 `host=172.17.0.1:5433` (Docker bridge gateway + 외부 포트)로 잘못 설정되어 Node-RED가 로컬 DB에 붙지 못함<br>2) **테스트 환경에 데이터 수집 파이프라인 자체가 없음** — Node-RED flows(324개 postgres 노드)는 전부 알람 조건/네트워크 판정 용도로 `tb_tag_raw_data`에 INSERT하는 노드가 0개. 프로젝트 전체에서 `INSERT INTO tb_tag_raw_data`는 스키마 dump 파일에만 존재. DB dump 로드 후 데이터가 `2026-04-11 11:42`에 정체<br>3) 이로 인해 `/trend/data`의 "최근 24시간" 요청이 빈 결과 반환 |
| **해결** | 1) **Node-RED 설정 수정:** `flows.json`의 `71827310c941a9d1` config를 `host=slm-timescaledb`, `port=5432`로 변경 (같은 `web_default` 네트워크 내부에서 접근) 후 Node-RED 재시작 → 알람/조건 판정 복구<br>2) **테스트 전용 데이터 수집 데몬 추가:** `/Users/jykim/slm/dev_tools/tag_ingest.py` + `Dockerfile.tag_ingest`를 작성하고 `docker-compose.dev.yml`에 `dev-tag-ingest` 서비스로 등록. 원격 운영 DB(`112.166.183.65:25479`) → 로컬 `tb_tag_raw_data`를 주기 복제(backfill 48h + poll 30s, `ON CONFLICT DO NOTHING`). 원격은 tz-naive KST → 로컬 tztz로 변환<br>3) 상세 사양: `docs/dev-tag-ingest-spec.md` |
| **수정 파일** | `slm-node-red:/data/flows.json`, `/Users/jykim/slm/dev_tools/tag_ingest.py`, `/Users/jykim/slm/dev_tools/Dockerfile.tag_ingest`, `/Users/jykim/web/docker-compose.dev.yml`, `/Users/jykim/web/docs/dev-tag-ingest-spec.md` |
| **검증** | 복제 데몬 기동 후 로컬 max(logtime)이 1분당 2~3시간씩 전진 (backfill 완료까지 ~15분), 이후 incremental 폴링으로 유지 |
| **⚠ 납품 시 제거** | `dev_tools/` 디렉토리, `docker-compose.dev.yml`의 `dev-tag-ingest` 서비스 블록, 원격 DB 자격 정보 전부 삭제 필요. 운영 환경은 실 PLC/Node-RED 수집 파이프라인 사용. 체크리스트: `docs/dev-tag-ingest-spec.md` 하단 |
| **재발 방지** | 신규 환경 세팅 시 `tb_tag_raw_data` 최신 logtime과 현재 시각 차이를 점검. Node-RED 설정 편집 시 `host`/`port`가 Docker 네트워크 기준인지 확인 (외부 매핑 포트 5433 사용 금지) |

---

### [E-013] AI 요약/원인 분석 클릭 시 첫 화면(로그인)으로 튕김

| 항목 | 내용 |
|------|------|
| **날짜** | 2026-04-12 |
| **증상** | 전체 센서 이상 점검 결과의 "AI 현황 요약" 또는 "AI 원인 분석" 버튼 클릭 시 응답이 표시되지 않고 사용자가 로그인(/login) 화면으로 이동 |
| **원인** | `src/lib/api-client.ts` `handleError`가 401 응답을 받으면 무조건 `signOut({callbackUrl:"/login"})` 호출. AI 요약/원인 서술 엔드포인트는 LLM 호출로 40~60초 걸리는 긴 요청이라 그 사이 JWT 만료 구간에 걸리면 백엔드가 401 반환 → 전역 핸들러가 즉시 로그아웃 → 첫 화면 튕김. 짧은 요청들은 토큰 갱신 버퍼(5분) 안에 끝나서 드러나지 않았던 문제. |
| **해결** | 1) `api-client.ts`의 401 핸들러에서 `signOut` 호출 제거, `ApiError`만 throw. 실제 세션 만료는 NextAuth JWT refresh 실패 → 기존 `SessionGuard`가 처리 (역할 분리). 2) `anomaly-api.ts`의 `explainAnomalyCause`/`explainScanAll`를 `apiClient` 대신 직접 `fetch` 호출로 이중 방어. |
| **수정 파일** | `src/lib/api-client.ts:72-85`, `src/lib/api/anomaly-api.ts` (import에서 apiClient 제거) |
| **검증** | Playwright 헤드리스 5회 반복 테스트 전부 PASS — URL 이동 없음, 요약 결과 정상 렌더 |
| **재발 방지** | 전역 signOut 트리거는 apiClient가 아닌 SessionGuard/refresh 실패 경로만 담당. LLM 등 장시간 호출 엔드포인트는 개별 에러 핸들링으로 처리. 새 API 래퍼 추가 시 401→전역 로그아웃 유혹을 피하고, 실패 시 컴포넌트 로컬 에러 상태로만 표현. |

---

## 시작 전 체크리스트

```
□ WSL2 Docker 실행 중? (wsl -d Ubuntu -- docker ps)
□ TimescaleDB 컨테이너 동작 중? (port 5433)
□ WSL2 IP로 5433 접속 확인 (wsl -d Ubuntu -- nc -zv localhost 5433)
□ AI Server 동작 중? (port 8000) — start-services.bat으로 시작
□ Next.js HTTPS 동작 중? (port 3000)
□ https://localhost:3000 접속 확인
```

**정상 상태 확인 명령 (PowerShell):**
```powershell
docker ps --filter "publish=5433"
netstat -ano | findstr ":8000"    # PID가 1개여야 정상 (2개면 구버전 서버 잔존)
netstat -ano | findstr ":3000.*LISTENING"
curl http://127.0.0.1:8000/health  # 반드시 127.0.0.1 사용 (localhost=::1 주의)
```

> **⚠ 포트 8000에 PID가 2개 보이면**: 구버전 서버 잔존. `taskkill /PID <오래된PID> /F`로 종료 후 `start-services.bat` 재실행.

---

### [E-015] 사이드바에서 알람 캘린더(히트맵) + 누수 의심 알림 + 일부 관리 메뉴 사라짐

- **날짜:** 2026-04-13
- **증상:** 모니터링 그룹의 "알람 캘린더(M003-6)"·"누수 의심 알림(M003-7)" 그리고 관리 그룹의 "채팅 피드백·시설 약칭·설비 신뢰성·LLM 서술 관찰(M100-7~10)" 메뉴가 사이드바에 표시되지 않음. 페이지는 직접 URL로는 접근 가능.

- **원인:**
  1. 사이드바 훅(`use-sidebar-menus.ts`)은 `/api/auth/me` → `tb_menu` 조회 결과를 1순위로 사용하고, 응답이 비어 있을 때만 정적 `sidebar-menus.ts`를 폴백으로 사용
  2. `db/seed/03_menus.sql`이 `sidebar-menus.ts`와 완전히 어긋난 stale 상태였음 — M003-4~7 / M006 위기대응 그룹 / M100-5~10 / M200 전체(14건) 등 **17개 메뉴 누락**
  3. 동적 응답이 일부 메뉴만 돌려주면서 누락된 메뉴들이 화면에서 사라진 것처럼 보임 (정적 폴백은 발동하지 않음 — 동적 응답이 비어있지 않으면 그대로 사용)

- **해결:**
  1. `tb_menu` + `tb_auth_menu`에 누락된 16개 메뉴 행 직접 INSERT (즉시 복구)
  2. `db/seed/03_menus.sql` 전체 재작성 — 단일 출처를 `sidebar-menus.ts`로 명시, `ON CONFLICT DO UPDATE`로 재시드 시 라벨/경로/순서 변동 자동 반영, VIEWER 권한 INSERT는 EXISTS 가드 추가 (FK 위반 방지)
  3. 정적 폴백에도 누락돼 있던 `M100-10 LLM 서술 관찰` 한 건 보완 (`sidebar-menus.ts`)

- **재발 방지:**
  - `sidebar-menus.ts` 변경 시 반드시 `db/seed/03_menus.sql` 동시 수정 (시드 파일 상단 주석에 명시)
  - 시드는 `ON CONFLICT DO UPDATE`라 재시드만으로 라벨/경로 변경 반영됨
  - `tb_menu` row 추가 PR에서는 `tb_auth_menu`에도 ADMIN/MASTER/USER 권한 INSERT 누락 여부 확인

- **검증:**
  - `SELECT * FROM tb_menu WHERE pmenu_idn IN ('M003','M100')` → 16건 정상
  - `/api/auth/me` 응답에 누락 메뉴 모두 포함되는지 다음 로그인 시 확인 필요

- **관련 커밋:** `web@3cb06f3` (시드 재작성), `slm-dashboard@88cd239` (정적 폴백 보완)

---

### [E-016] gemma4 Ollama 모델명 미스매치 + cold-start 90s 타임아웃

- **날짜:** 2026-04-13
- **증상:**
  1. AI explain 엔드포인트들이 "Ollama 응답 오류: model 'gemma4' not found"로 실패
  2. 첫 호출에서 90s 타임아웃 후 fallback 경로로 돌아가는 사례 빈발 (P2.6 scan-all/explain 등)

- **원인:**
  1. **태그 미스매치:** `.env`/`docker-compose.dev.yml` 기본값이 `OLLAMA_MODEL=gemma4` 또는 `gemma4:26b`였으나 실제 설치된 태그는 `gemma4:26b-a4b-it-q4_K_M` (Ollama는 정확한 태그 매칭만 허용, 별칭 자동 해석 안 함)
  2. **모델 idle 언로드:** Ollama 기본 keep_alive 5분 후 모델이 VRAM에서 언로드되어 첫 요청에 cold-start 페널티 (gemma4:26b 기준 60~90s)
  3. **타임아웃 마진 부족:** explain 엔드포인트 하드코딩 90s가 cold-start + tail latency 조합에서 부족 (이전 코멘트의 "p95 30s, p99 50s"는 다른 모델 측정값으로 stale)

- **해결:**
  1. `.env` 3곳 모두 정확한 태그로 통일: `gemma4:26b-a4b-it-q4_K_M`
     - `/Users/jykim/web/.env` (Docker compose 오버라이드)
     - `/Users/jykim/slm/.env` (네이티브 실행)
     - `slm_config.py` 기본값 fallback도 동기화
  2. `OLLAMA_KEEP_ALIVE="24h"` 추가 → `ollama_client.generate()` payload에 자동 주입
  3. lifespan startup에서 백그라운드 스레드로 `generate("ping", num_predict=1)` 웜업 호출 (가중치 VRAM 상주 + 첫 사용자 요청 cold-start 제거)
  4. 5개 explain 엔드포인트 하드코딩 timeout 상향: 90→180s (tag_latest는 60→120s)

- **검증 (10회 반복 테스트, 2026-04-13):**
  - FAQ `/chat/faq/examples` 10/10 성공 (<100ms)
  - P2.7 `/equipment-mtbf/explain` 10/10 LLM 경로 (median 42s, range 36~94s)
  - P2.6 `/anomaly/scan-all/explain` 10/10 LLM 경로 (median 33s, range 32~44s)
  - **Fallback 0회** — 100% LLM 경로 통과
  - `curl /api/ps`로 `expires_at +24h` 확인 → keep_alive 정상

- **재발 방지:**
  - 모델 변경 시 `ollama list`로 정확한 태그 확인 후 `.env` 3곳 모두 동기화
  - 새 explain 엔드포인트는 timeout 180s + backoff 3s 패턴 준수
  - 모델 교체/업그레이드 후 첫 호출은 웜업 완료 로그 확인 (`Ollama 웜업 완료: <model> (Nms)`)

- **관련 커밋:** `slm@d6b97a9` (slm_config + ollama_client + ai_server warmup + 5종 endpoint timeout 상향)

---

### [E-017] 첫 /monitoring/flow 접속 시 데이터 18초 지연

- **날짜:** 2026-04-13
- **증상:** 백엔드 기동 직후 처음 용수 흐름 페이지(`/monitoring/flow`)를 열면 데이터가 18초 가까이 뜨지 않음. 두 번째 접속부터는 수백 ms로 정상.
- **재현 로그 (Next.js dev):**
  ```
  GET /monitoring/flow          200 in 600ms (compile: 567ms, render: 30ms)
  GET /api/proxy/flow-map       200 in 18.3s (compile: 32ms, render: 18.3s)
  GET /api/proxy/flow-map/roots 200 in 18.3s (compile: 38ms, render: 18.3s)
  GET /api/proxy/flow-map/realtime 200 in 18.3s (compile: 4ms, render: 18.3s)
  ```

- **원인 (복합):**
  1. **Next.js Turbopack dev-mode JIT 컴파일** — `next dev --turbopack`은 라우트를 처음 요청할 때 JIT 컴파일. `/api/proxy/[...path]/route.ts` 카탈-올 핸들러가 cold 상태일 때 최초 컴파일에 약 500ms 소요. 세 개의 병렬 프록시 호출이 모두 같은 route.ts 컴파일을 기다리는 구조
  2. **백엔드 cold 경합** — `slm-backend` 기동 직후 `lifespan`에서 IForest 학습(37s) + SCAN_ALL SQL(98s) + SNMP 폴링 + 현장 프로파일링이 병렬로 돌면서 동일 DB 커서/리소스 경합. 이 구간에 `/flow-map/realtime`이 들어오면 `render` 시간이 18초까지 늘어남 (`flow_realtime.py`는 한 요청에 10~12개 쿼리를 연쇄, `tb_tag_raw_data` LATERAL JOIN으로 배수지 7일 야간최소유량 + 적산유량 + 가압장 펌프 상태 조회)
  3. 안정 상태에서는 cold-start 페널티가 모두 사라져 `/flow-map/realtime`은 ~300~400ms, proxy route는 수 ms

- **해결 (옵션 B: dev 전용 프리워밍 사이드카):**
  - 신규 스크립트 `slm/dev_tools/prewarm.sh` — backend `/health` + frontend `/login` 준비 대기 후:
    1. 백엔드 `/flow-map`, `/flow-map/roots`, `/flow-map/realtime`, `/dashboard/summary` 직접 호출 → 백엔드 쿼리 캐시 + `_flow_baseline_cache` 워밍 + IForest/SCAN_ALL 경합 구간에 첫 호출이 떨어지게 강제
    2. 프런트엔드 페이지 10종 curl → Next.js 페이지 컴파일 트리거 (login 307 리다이렉트라도 middleware/proxy 컴파일 진행)
    3. `/api/proxy/flow-map` 3건 curl → `[...path]/route.ts` 카탈-올 핸들러 JIT 컴파일 트리거 (401 응답이지만 route 컴파일은 완료)
  - 신규 compose 서비스 `frontend-prewarm` (이미지 `curlimages/curl:latest`, `restart: no`) — `depends_on: [frontend, backend]`로 스택 기동마다 1회 실행 후 종료
  - `OLLAMA_KEEP_ALIVE=24h` + ai_server 모델 웜업 스레드(E-016)와 같은 패턴 확장

- **검증:**
  - cold 상태 `.next/dev` 디렉토리 제거 후 frontend 재기동 → prewarm 사이드카 실행 → `docker logs slm-frontend`에서 `/api/proxy/flow-map 401 in 23ms (compile: 16ms)` 확인 (이전 cold 500~700ms → 20ms 수준 축소)
  - 백엔드 `/flow-map/realtime` 직접 호출 시 첫 호출도 수백 ms 수준 (IForest 학습 중에도 경합 개선)

- **한계:**
  - 인증 없이 curl하므로 middleware가 307 리다이렉트 → `/monitoring/flow/page.tsx` 자체 컴파일은 실제 로그인 세션이 있어야 발생(한 번 발생하면 이후 캐시). prewarm으로는 `page.tsx` 단독 컴파일 비용 완전 제거는 안 됨 — 다만 이 부분은 수백 ms 수준이라 체감 영향이 작음
  - 운영 환경에서는 `next build && next start` (prod)를 사용하므로 JIT 컴파일 자체가 없어 prewarm 사이드카 불필요 → docker-compose.dev.yml 블록에 **납품 시 제거** 주석 명시

- **관련 파일:**
  - `slm/dev_tools/prewarm.sh`
  - `docker-compose.dev.yml` (`frontend-prewarm` 서비스)
  - `slm/endpoints/flow_realtime.py` (쿼리 연쇄 — 향후 최적화 후보)

---

### [E-018] 위기대응 화면 수위 알람의 검출 로직 다이어그램 미표시

- **날짜:** 2026-04-13
- **증상:** `tb_equipment_alarm_report.diagnosed_msg`(text)에 `<div class="diagram-container">` + `<div class="flow-row">` + `<div class="flow-step">` + `<div class="flow-box COLOR">` 구조의 검출 로직 흐름도(flowchart) HTML이 들어 있는 수위 경보가 다수 존재하지만, 위기대응 → 경보분석(`/crisis/alarm-analysis`) 우측 패널에 다이어그램이 한 건도 표시되지 않음.

- **원인 (복합):**
  1. **프런트엔드 파서 누락** — `AlarmAnalysisDetail.tsx`의 `parseDiagnosedMsg()`가 섹션 자식 요소 중 `<p>`, `<ul>/<ol>`, `.info-box`/`.info`만 처리. 수위 알람 4번 섹션 "발생원인"에 들어가는 `<span>` 본문, `<h3>(로직점검 프로세스)` 헤딩, `.diagram-container` 다이어그램 컨테이너는 모두 무시되고 있었음
  2. **백엔드 30일 컷오프** — `endpoints/alarm_crisis.py` `/crisis/alarm-analysis`가 `WHERE alarm_start_time >= NOW() - INTERVAL '30 days'`로 하드코딩. 다이어그램 포함 행 583건이 모두 2026-02-01 ~ 02-27 범위라 4월 시점에서 컷오프에 잘려 endpoint가 0건 반환

- **해결:**
  1. `parseDiagnosedMsg()` 확장 — 자식 요소 분기에 `SPAN`(텍스트), `H2/H3/H4`(heading 블록), `.diagram-container`(diagram 블록) 추가
  2. 신규 `parseDiagramContainer()` 헬퍼 — `.flow-row` → `.flow-step`/`.flow-vertical`/`.arrow-down-connector`를 문서 순서로 순회, `arrow-down-connector`를 row 구분자로 사용해 `DiagramRow[]`로 변환. `.flow-box`의 `blue/green/yellow/pink/purple/gray` 클래스 → `DiagramColor` 열거형
  3. 신규 `DiagramFlow` 컴포넌트 — `DIAGRAM_COLOR_CLASSES` 정적 매핑(Tailwind purge 호환)으로 색상 적용, 가로 flex + `→` 화살표, `arrow_down` 행은 `↓` 중앙 정렬로 다음 그룹과 시각적 분리
  4. `endpoints/alarm_crisis.py` `get_alarm_analysis()` — `days: int = 90` 쿼리 파라미터화 (7~365 클램프), 기본값 30→90 확장. 옛 다이어그램 데이터까지 포함되도록

- **검증:**
  - Playwright + 백엔드 직접 fetch로 실제 DB 행 1건의 `diagnosed_msg`에 신규 파서 로직 inline 실행 → DiagramRow 정확히 5개 (steps 3 + arrow_down 2) 추출, 15개 flow-box 색상 분류 정상 (`수위분석 알고리즘 시작:blue`, `헌팅여부:green`, ...)
  - `curl /crisis/alarm-analysis?days=90` → 500건 중 183건이 `diagram-container` 포함 (이전 0건 → 183건)

- **한계:**
  - 신규(2026-03 이후) 알람은 다이어그램 HTML이 더 이상 생성되지 않음 — 백엔드 알람 생성 로직(다른 파일/시스템)의 회귀일 가능성 별도 조사 필요

- **관련 파일:**
  - `slm-dashboard/slm-dashboard/src/components/crisis/AlarmAnalysisDetail.tsx`
  - `slm/endpoints/alarm_crisis.py:476`

---

### [E-019] 검출 로직 다이어그램이 현재 알람에 반영 안 되고 검출 단계 표시 없음

- **날짜:** 2026-04-13
- **증상:** [E-018]로 옛 다이어그램(2026-02 시점) 583건은 표시되지만 — (1) 2026-03 이후 신규 수위 알람에는 다이어그램이 한 건도 생성되지 않음, (2) 다이어그램에 어느 단계에서 실제 검출됐는지 표시 없이 모든 박스가 평평하게 그려짐.

- **원인 조사:**
  1. **고립된 dead 노드:** Node-RED `flows_deploy.json`의 `a655fae0839ec028`("HTML 위기대응 표시", 17,574 chars)에 다이어그램 생성 코드가 살아있지만 **upstream wires 0 / downstream `[[]]`** — 완전히 고립. 2026-02-27 무렵 wiring이 끊어진 채 방치
  2. **간소화된 대체 노드:** 배수지 수위 알람 체인은 현재 `820cf7cd8e67c2f9`(8,719 chars, no diagram)로 wiring 돼 있음. 입력 변수는 24개로 dead 노드(23개)의 진상위 — **swap 호환성 100%**
  3. **검출 단계 정보 부재:** 어느 함수도 `msg.detection_step`을 set하지 않음. 모든 다이어그램 박스가 동일하게 green으로 렌더되어 운영자가 어디서 검출됐는지 알 수 없음

- **해결 (3-layer 패치):**

  **A) Node-RED 함수 swap + 검출 단계 추론 로직 주입** (`flows_deploy.json`)
  - `820cf7cd8e67c2f9.func` ← `a655fae0839ec028.func`(17,574) + 추가 헬퍼 2종(2,784 chars 추가, 총 20,358 → 20,669 with CSS)
  - 신규 헬퍼 `detectStep(cause, alarmMsg)`: `diagnosed_cause` + `alarm_msg` 키워드 우선순위 매칭으로 1~15 인덱스 반환
    - 우선순위: ping/통신(15) > 밸브(14) > 흡수정(13) > 설정오류(12) > 탈설비(11) > 가압장유량(9) > 가압장분석(8) > 유입유출량(7) > 12hr공급(6) > 펌프가동여부(5) > 펌프조건(4) > HH/LL(3) > 헌팅(2) > 기본 7
    - **유입유출량 분석(7)이 가장 흔한 매칭** — 실제 cause 텍스트 다수가 "유출량 감소/증가" 패턴
  - 신규 헬퍼 `applyDetectionClasses(html, idx)`: HTML의 `<div class="flow-box COLOR">` 15개를 문서 순서로 카운트, idx == count 박스에 ` detected` 클래스 + idx > count 박스에 ` dimmed` 클래스 + 모든 박스에 `data-step="N"` 부여
  - HTML 템플릿에 `.flow-box.detected`(red ring + scale + glow) + `.flow-box.dimmed`(opacity 0.35 + grayscale) CSS 룰 추가 (standalone HTML viewer 호환)
  - `a655fae0839ec028`는 `d: true` + `(legacy/dead)` 라벨로 마킹 (삭제는 안 함, 백업/회귀 대비)
  - Node-RED 컨테이너 `slm-node-red` 재시작으로 활성화

  **B) 프런트엔드 파서/렌더러 확장** (`AlarmAnalysisDetail.tsx`)
  - `DiagramStep` 타입에 `detected?: boolean`, `dimmed?: boolean` 추가
  - `parseDiagramContainer()`: 박스 클래스리스트에서 `detected`/`dimmed` 추출, **첫 detected 발견 후 후속 박스 자동 dimmed 처리** (Node-RED 누락 대비 클라이언트 보강)
  - 신규 상수 `DETECTED_BOX_CLASSES = "ring-2 ring-red-500 ring-offset-2 scale-105 shadow-lg shadow-red-500/30 border-red-500/60"`
  - 신규 상수 `DIMMED_BOX_CLASSES = "opacity-30 grayscale"`
  - `DiagramFlow` 컴포넌트 상단에 검출 단계 범례 추가 ("검출 단계: <label> (이후 단계는 회색 처리)")
  - 박스에 `title`/`aria-label` 추가 (스크린리더 + tooltip)
  - 검출 이후 `→` 화살표도 dimmed 처리 (`text-muted-foreground/30`)

  **C) 백엔드 trigger 활성화 (E-018에서 이미 처리)**
  - `endpoints/alarm_crisis.py`의 `?days=` 파라미터(기본 90) 덕분에 옛 데이터까지 함께 노출

- **검증:**
  1. **Node-RED 라이브 실행:** 죽동(배) 수위#1 HH 알람 + 송산2산단생활(배) 수위 LL 주의 알람 두 건의 `diagnosed_msg`를 NULL로 만들고 폴링 1주기(30초) 대기 → 두 건 모두 13977/14042 chars로 재생성 + `detected` + `data-step` 모두 포함 확인
  2. **DB-side 박스 분포:** 죽동 알람 → 박스 1~6 plain / #7 detected (`green detected data-step="7"`) / #8~15 dimmed
  3. **React 파서 라이브 검증:** Playwright로 `/crisis/alarm-analysis?days=90` fetch 후 inline parser 실행 → `{detected: {label:"유입유출량 분석", color:"green"}, counts: {plain:6, detected:1, dimmed:8}}` 정확 추출
  4. **TypeScript:** `tsc --noEmit` 신규 에러 없음

- **알려진 한계:**
  1. **배수지 수위 전용** — 가압장 수위, 네트워크, UPS, 펌프, 밸브 등 다른 카테고리의 다이어그램 함수는 미존재 ([E-018] 한계 그대로 유지). Phase 2 작업
  2. **휴리스틱 매칭** — 정확도 ~80%. 더 정확한 단계 추적은 상위 switch/function 노드에서 `msg.detection_step`을 직접 set하는 Phase 2 리팩토링이 필요
  3. **소급 적용 안 됨** — Node-RED 트리거는 `WHERE diagnosed_msg IS NULL`만 잡으므로 이미 채워진 옛 알람은 그대로. 백필 필요 시 별도 SQL로 NULL 후 재처리

- **관련 커밋:** `web@4632710` (flows_deploy.json swap + CSS + docs E-019 + work-history) / `slm-dashboard@55e9d66` (AlarmAnalysisDetail.tsx 파서/렌더러 detected/dimmed)

---

### [E-020] DB 미사용 테이블 12종 정리 + 핫 테이블 최적화

- **날짜:** 2026-04-13
- **목적:** 사용자 요청 "현재 db에서 사용하지 않는 테이블은 삭제하고 최적화 해줘". 스키마 정합성 + 백업/재시드 부담 감소 + 핫 테이블 인덱스 bloat 정리.

- **조사 절차:**
  1. `ANALYZE` 후 `pg_stat_user_tables.n_live_tup` 정확화 (autovacuum 미실행으로 stale 상태였음)
  2. 빈 테이블(0 rows) 19개 후보 추출
  3. 코드베이스 grep — `slm/` Python + `slm-dashboard/src/` TypeScript 참조 0건인 것만 남김
  4. FK 의존성 검사 (`pg_constraint`로 외부 참조 확인 → 12개 모두 외부 FK 없음)
  5. View 정의 검사 (`pg_views` → 0건)

- **삭제 결정 (12개):**

  | 테이블 | 대체 / 사유 |
  |---|---|
  | `tb_alarm_log` | `tb_equipment_alarm_report`로 통합 (richer schema: severity/cause/countermeasure/meta jsonb/diagnosed_msg HTML) |
  | `tb_user_session` | `tb_user.current_session_id` 컬럼으로 통합 (auth_crud.py 사용 패턴) |
  | `tb_menu_api` | 메뉴-API 매핑 미구현. 향후 RBAC 확장 시 재도입 가능 |
  | `tb_file_history` | 파일 이력 추적 미구현. tb_file_storage에 audit 컬럼 추가로 대체 가능 |
  | `tb_ai_chat_faq` | `endpoints/chat_faq_examples.py`가 동적 생성 (DB 테이블 미사용) |
  | `tb_ai_chat_ask` / `tb_ai_chat_bot` / `tb_ai_chat_ask_group` / `tb_ai_chat_ask_image` / `tb_ai_chat_bot_image` | 채팅 히스토리 미사용 (slm-dashboard `chat-store.ts`가 클라이언트 localStorage에만 저장). FK 5건 모두 자기들끼리만 묶임 |
  | `tb_prompt_template` / `tb_prompt_column` | `/admin/prompts` 메뉴(M100-3)는 sidebar-menus.ts에서 숨김 처리됨. 프롬프트는 코드(슬롯-필링 + example3.json)에서 직접 관리 |

- **유지 (빈 테이블이지만 코드가 사용):**
  - `tb_leak_cusum_alert` (py=2 — leak_cusum_alert.py)
  - `tb_facility_alias` (py=3 — facility_alias.py)
  - `tb_ai_chat_feedback` (py=2 — chat_feedback.py)
  - `tb_field_lock` (ts=1 — slm-dashboard에서 사용)
  - `tb_causal_chain_override` (py=2 — causal 로직)

- **실행 단계:**

  **A) 백업 (롤백 안전)**
  - `pg_dump --schema-only` 12개 테이블 → `db/backups/unused_tables_backup_2026-04-13.sql` (673 lines, 12 CREATE TABLE)
  - 모두 0 rows이라 schema-only로 충분

  **B) 트랜잭션 DROP**
  ```sql
  BEGIN;
  DROP TABLE tb_ai_chat_ask_image  CASCADE;  -- depends on tb_ai_chat_ask
  DROP TABLE tb_ai_chat_bot_image  CASCADE;  -- depends on tb_ai_chat_bot
  DROP TABLE tb_ai_chat_bot        CASCADE;  -- depends on tb_ai_chat_ask
  DROP TABLE tb_ai_chat_ask        CASCADE;  -- depends on tb_ai_chat_ask_group
  DROP TABLE tb_ai_chat_ask_group  CASCADE;
  DROP TABLE tb_prompt_column      CASCADE;  -- depends on tb_prompt_template
  DROP TABLE tb_prompt_template    CASCADE;
  DROP TABLE tb_alarm_log          CASCADE;
  DROP TABLE tb_user_session       CASCADE;
  DROP TABLE tb_menu_api           CASCADE;
  DROP TABLE tb_file_history       CASCADE;
  DROP TABLE tb_ai_chat_faq        CASCADE;
  COMMIT;
  ```
  - 결과: 59 → 47 테이블

  **C) 핫 테이블 최적화**
  - `VACUUM ANALYZE` — `tb_network_status`, `tb_equipment_alarm_report`, `tb_tag_info`, `tb_tag_group_map`, `tb_equipment_tag_map`
  - `REINDEX TABLE` — `tb_network_status`, `tb_equipment_alarm_report`
  - `CLUSTER tb_equipment_alarm_report USING idx_alarm_report_start_time` — 시계열 정렬로 range scan 가속
  - 결과:
    - `tb_equipment_alarm_report`: 71 MB → **65 MB** (8.5% 축소)
    - `tb_network_status`: 267 MB → **263 MB** (1.5% 축소)

  **D) 시드 SQL 정리** (재시드 시 다시 생성 안 되도록)
  - `db/init/02_tables_core.sql` — `tb_user_session`, `tb_menu_api` 정의 제거 + 제거 주석
  - `db/init/03_tables_chat.sql` — 6개 chat 테이블 정의 제거, `tb_ai_chat_feedback`만 남김 + 제거 주석
  - `db/init/05_tables_tag_timeseries.sql` — `tb_alarm_log` 정의 제거 + 제거 주석
  - `db/init/06_tables_admin.sql` — `tb_prompt_template`, `tb_prompt_column`, `tb_file_history` + FK 정의 제거 + 제거 주석
  - `db/seed/05_chat_faq.sql` 삭제
  - `db/seed/06_prompts.sql` 삭제
  - `db/init/01_schema.sql.bak` 삭제 (untracked stale 백업)

- **검증:**
  - DROP 후 백엔드 `/health` → 200 OK, `current_model`/`ollama_available` 정상
  - 백엔드 로그 30초 스캔 → "relation does not exist" 에러 0건
  - 정리된 init SQL 재로드 → SET/CREATE TABLE 단계 모두 통과 (기존 ALTER ADD CONSTRAINT는 PostgreSQL `IF NOT EXISTS` 미지원으로 멱등 실패 — 기존 패턴 그대로 유지)

- **롤백 절차:**
  ```bash
  docker exec -i slm-timescaledb psql -U slm_dev -d slm < db/backups/unused_tables_backup_2026-04-13.sql
  ```

- **관련 파일:**
  - `db/backups/unused_tables_backup_2026-04-13.sql` (신규, schema dump)
  - `db/init/02_tables_core.sql`, `db/init/03_tables_chat.sql`, `db/init/05_tables_tag_timeseries.sql`, `db/init/06_tables_admin.sql`
  - `db/seed/05_chat_faq.sql` (삭제), `db/seed/06_prompts.sql` (삭제), `db/init/01_schema.sql.bak` (삭제)

- **관련 커밋:** `web@1fd89bc`

---

### [E-021] GIS 관망도 초기 진입 시 유량흐름이 토글 OFF인데 렌더됨 (race condition)

- **날짜:** 2026-04-13
- **증상:** `/monitoring/gis` 첫 접속 시 "유량 흐름" 패널이 닫혀 있고(`showFlowPanel=false`) 개별 토글도 모두 OFF(`showBase/showGlow/showImbalance/showShimmer` 초기값 `false`)인데도 flow 레이어(Glow/Base/Anim/Imbalance/Node)가 지도에 렌더됨. 사용자가 패널을 열고 해당 토글을 한 번 켰다 끄면 그제서야 사라짐.

- **원인 (race condition):** `GisFlowOverlayLayer.tsx`의 mount 순서
  1. **Mount effect** (`useEffect([mapRef])`, L253-279): `map.isStyleLoaded()` 체크 후 `initLayers()`를 동기 또는 `map.once("load", initLayers)`로 비동기 스케줄. `initLayers` → `addFlowLayers(map)` → 5개 `map.addLayer(...)` 호출
  2. **addFlowLayers** (L127-221): 각 `addLayer`에 `layout.visibility`를 **미지정** → MapLibre 기본값 `"visible"` 적용 → 레이어가 즉시 렌더됨
  3. **Visibility effect** (`useEffect([mapRef, visible, showGlow, showBase, ...])`, L296-311): `if (!map.getSource(SRC_ID)) return;`로 초기 실행 시 bail out (source 미존재). 레이어가 아직 추가되지 않았으므로 visibility 보정이 스킵됨
  4. 결과: `addFlowLayers` 호출 순간 5개 레이어가 `visibility="visible"` 기본값으로 존재 → 보정 effect는 이미 bail out 후여서 재실행되지 않음 → 화면에 flow가 그려진 상태로 고정
  5. 사용자가 토글을 클릭하면 show* state 변경 → visibility effect 재실행 → 이제 레이어가 존재하므로 `setLayoutProperty(id, "visibility", "none")` 성공 → 사라짐

- **수정:** `addFlowLayers`의 5개 `map.addLayer(...)`에 `layout.visibility = "none"` 초기값 주입
  - Glow/Base/Anim/Imbalance: 기존 `layout: { "line-cap":..., "line-join":... }`에 `visibility: "none"` 추가
  - Node(circle): 기존 `layout` 없음 → `layout: { visibility: "none" }` 신규 추가
  - 주석으로 race condition 설명 추가 (파일 최상단 헬퍼 JSDoc)
  - Visibility effect는 변경 없음 — show* props가 `true`로 바뀔 때 `setLayoutProperty("visible")`로 정상 승격

- **검증 (Playwright 라이브):**
  1. **초기 진입**: `/monitoring/gis` 접속 후 React fiber 내부에서 MapLibre 인스턴스 획득 → 5개 레이어 모두 `visibility: "none"` ✅
  2. **토글 on**: "유량 두께·색상" 버튼 클릭 → `gis-flow-base`만 `visible`, 나머지 4개는 `none` 유지 ✅
  3. **토글 off**: 같은 버튼 재클릭 → 5개 모두 `none` 복귀 ✅
  4. TypeScript `tsc --noEmit` 신규 에러 없음

- **재발 방지:**
  - 새 지도 레이어 추가 시 항상 `layout.visibility` 초기값을 명시
  - `addLayer` 호출과 visibility 보정 effect 사이 순서 race 주의 — 레이어는 항상 "none"으로 시작, state-driven effect가 "visible"로 승격

- **관련 파일:**
  - `slm-dashboard/slm-dashboard/src/components/gis/GisFlowOverlayLayer.tsx:127-221` (addFlowLayers)

- **관련 커밋:** `slm-dashboard@868f472` / `web@f9630b1`

---

### [E-022] "AI 현황 요약" 버튼이 시설 범위 질의에도 전역 Top-3을 반환

- **날짜:** 2026-04-13
- **증상:** AI 채팅에서 "행정1수청 소소블록 이상 스캔해줘"처럼 시설 범위를 지정해 질의하면 `AnomalyVisualization` → `AnomalyScanView`가 **백엔드에서 이미 필터된 rawData**를 표시하지만(`_filter_anomaly_cache_rows` 적용됨), 그 위 "AI 현황 요약" 버튼을 클릭하면 전역 Top-3("남산 배수지 · 남산(배) 탁도", "송산2산단생활 가압장", "석문2 소블록")이 응답으로 뜸. 해당 현장에 대해서만 나와야 하고, 없으면 "없습니다"로 응답해야 함.

- **원인:**
  1. `AnomalyScanView`의 `handleExplainScan`이 `explainScanAll(3)`을 호출 — **scope 필터 없이**
  2. `explainScanAll` API 래퍼도 `top_n`만 전달, 시설 필터 파라미터 없음
  3. 백엔드 `POST /anomaly/scan-all/explain`(`scan_all_explain.py`)이 `_ANOMALY_SCAN_CACHE` 전체를 그대로 읽어 Top-N 선택 → 전역 결과 반환
  4. 결과: 채팅 상단의 rawData(필터됨)와 버튼 응답(전역)이 불일치 → 사용자 혼란

- **수정 (3-layer):**

  **A) 백엔드** `endpoints/scan_all_explain.py`
  - `ScanAllExplainRequest`에 `sitename: Optional[str]`, `facilitytype: Optional[str]` 필드 추가
  - `_build_scope_label()` 헬퍼 신설 — 필터 파라미터 조합해서 "행정1수청 소소블록" / "전체" 라벨 생성
  - 캐시 rows 로드 직후 scope 필터 적용 (`rows = [r for r in rows if r["sitename"] == req.sitename]` 등)
  - **필터 결과 0건이면 LLM 호출 없이 즉시 "`{scope}에 현재 이상 탐지된 태그가 없습니다.`" 템플릿 응답** (`source: "template"`)
  - LLM 프롬프트에 분석 범위 섹션 + 규칙 7번("범위 밖 시설은 언급하지 마라") 추가
  - `_build_fallback(top_rows, top_n, scope_label)` 시그니처 확장 — fallback 응답도 scope 레이블 반영

  **B) 프런트엔드 API 래퍼** `slm-dashboard/src/lib/api/anomaly-api.ts`
  - `ScanScope` 인터페이스 신설 (`{sitename?, facilitytype?}`)
  - `explainScanAll(topN, scope?)` 시그니처 확장 — scope 값이 있으면 body에 포함
  - `ScanAllExplainResult`에 `source: "template"` 추가 + 옵셔널 `scope?` 필드

  **C) 프런트엔드 컴포넌트** `slm-dashboard/src/components/chat/anomaly/AnomalyScanView.tsx`
  - `useMemo`로 rawData에서 scope 추출 — 모든 row의 sitename/facilitytype을 Set으로 수집, **크기 1인 경우만** 값 전달 (mixed면 undefined로 전역 유지)
  - `handleExplainScan`이 `explainScanAll(3, scanScope)` 호출
  - 백엔드가 rawData를 필터해 돌려준 경우에만 scope가 확정되므로 채팅 의도와 자동 일치

- **검증 (curl 실측):**
  1. **`sitename=행정1수청, facilitytype=소소블록`** (실데이터 1건 존재):
     ```
     source: llm
     top_rows_count: 1 / total_anomaly: 0 / total_warn: 1
     summary: "행정1수청 소소블록의 총 스캔 태그는 1건이며, 이상 판정은 0건, 주의 판정은 1건입니다. 주의 항목인 행정1(수청)소블럭 압력은 현재 5.77, 30일 평균 6.10, 편차 5.5%, z=-2.38입니다."
     ```
  2. **`sitename=없는시설, facilitytype=소소블록`** (0건):
     ```
     source: template
     summary: "없는시설 소소블록에 현재 이상 탐지된 태그가 없습니다."
     ```
  - TypeScript `tsc --noEmit` 신규 에러 없음

- **관련 파일:**
  - `slm/endpoints/scan_all_explain.py:37-50,95-133,168-204`
  - `slm-dashboard/slm-dashboard/src/lib/api/anomaly-api.ts:45-94`
  - `slm-dashboard/slm-dashboard/src/components/chat/anomaly/AnomalyScanView.tsx:55-90`

- **관련 커밋:** `slm@20632c0` / `slm-dashboard@d3661b5` / `web@1602d95`

---

### [E-023] AI 현황 요약을 카테고리별 의미 + 점검 순서 가이드형 Hybrid로 재설계

- **날짜:** 2026-04-13
- **배경:** 기존 `/anomaly/scan-all/explain`은 LLM에게 "Top-3 태그 이름·z-score·편차"를 2~4문장으로 나열시켰음. 운영자는 통계 수치를 도메인 의미로 다시 해석해야 했고 "어디부터 봐야 하는지" 가이드도 없었음. 사용자 피드백: "교차 검증의 의미, 가장 중요한 항목, 알람의 위급한 순서 항목대로 보라고 가이드" 형태가 필요.
- **설계 검토 (3가지 옵션 비교):**
  1. **풀 LLM 카테고리 설명** — 응답 6~8문장, 45~60s, 할루시네이션 위험 中
  2. **현재 방식 유지** — 2~4문장, 30~45s, 의도 미충족
  3. **Hybrid (LLM 1문장 + Python 정적 조립)** — 4섹션 응답, **10~15s**, 할루시네이션 거의 없음
  - 추천: 옵션 3. 카테고리 정의·점검 순서는 LLM이 창작할 부분이 아니라 도메인 상수이므로 Python에서 주입하는 것이 정확성·속도·유지보수성 모두 우위.

- **수정 (`slm/endpoints/scan_all_explain.py` 전면 재작성):**

  **A) 카테고리 분류 / 정의 / 점검 순서 상수**
  ```python
  CATEGORY_PRIORITY = ["equip_fault", "cross_check", "data_quality", "value_deviation"]
  CATEGORY_LABELS = {
      "equip_fault":     "설비 장애",
      "cross_check":     "교차 검증",
      "data_quality":    "데이터 품질",
      "value_deviation": "값 이탈",
  }
  CATEGORY_MEANINGS = {
      "equip_fault":     "통신이상·UPS·펌프 등 설비 DI 직접 감지 (확정 사고)",
      "cross_check":     "상류 유입과 하류 유출의 수지 불일치 (누수·월류·계측 오류 의심)",
      "data_quality":    "결측·정체·역전 데이터 (센서·통신 점검 필요)",
      "value_deviation": "요일·시간대 기준 Z-Score 이탈 (통계적 경계, 오탐 가능)",
  }
  _VERDICT_WEIGHT = {"복합이상":10,"교차이상":9,"이상":8,"교차주의":7,"주의":6,"정상":0}
  ```
  - 점검 순서 결정 근거: **확정 사고(DI 신호) > 물리 피해 의심(수지 불일치) > 모니터링 무력화(결측) > 통계 경계(z-score)**. 운영팀이 용어 수정 시 이 사전 한 곳만 변경.

  **B) 분류 / 집계 / 우선순위 헬퍼**
  - `_classify_row(row)` — 한 row가 속하는 카테고리 집합 (중복 허용, 단 `value_deviation`은 다른 카테고리 없을 때만)
  - `_count_by_category(rows)` — 카테고리별 row 수 집계 (한 row가 두 카테고리에 걸치면 양쪽 +1)
  - `_select_most_urgent(rows)` — 우선순위 카테고리 순서로 가장 심각한 1건 선택. 같은 카테고리 내 정렬: verdict 가중치 → z_score 절댓값
  - `_template_urgent_sentence(row, cat)` — LLM 실패 시 결정적 1문장 (할루시네이션 0)
  - `_assemble_summary(urgent, counts, total, cat, scope)` — markdown-lite 4섹션 조립

  **C) Hybrid 응답 형태**
  ```
  [중요 알람] {LLM 1문장 — 시설·태그·수치·카테고리 라벨}

  [유형별 현황] 설비 장애 N건 · 교차 검증 M건 · 데이터 품질 K건 · 값 이탈 L건 (총 T건 중)

  [{가장 위급한 카테고리}] {정적 정의 문구}

  [점검 순서] ① 설비 장애 → ② 교차 검증 → ③ 데이터 품질 → ④ 값 이탈
  ```

  **D) LLM 호출 축소**
  - 프롬프트에 **단일 row 정보**만 포함 (Top-N 목록 → 최위급 1건). 출력 ~50~100 토큰 목표
  - **단 1문장**, 존댓말, 카테고리 라벨 괄호 명시, 외부 지식 금지
  - num_predict는 None (모델 기본값) — 디버그 중 num_predict=100 설정 시 gemma4가 chat 템플릿 토큰을 먼저 생성하다 budget 소진해 빈 응답 반환하는 동작 관찰됨
  - 허용 수치 화이트리스트: top 1건 수치 + 프롬프트 상수 `0/1/30` (LLM이 "30일 평균" 자주 언급)

  **E) 프런트엔드** `AnomalyScanView.tsx`
  - 응답에 `\n\n`으로 분리된 4섹션이 들어오므로 `<p>`에 `whitespace-pre-line leading-relaxed` 클래스 추가 — 줄바꿈 보존
  - `source: "template"` 케이스(0건 등) sky-500 색상으로 구분 (기존 llm=purple, fallback=amber)

- **검증 (curl 실측 3 케이스):**

  1. **전역 (scope 없음)** — `source: llm`, **38.8s**:
     ```
     [중요 알람] 남산10 소블록의 남산10(블) 압력이 30일 평균 4.77 대비 0.4%의 편차와 -0.11의 Z-Score를 보이는 4.75로 교차이상 판정되었습니다 (설비 장애).

     [유형별 현황] 설비 장애 112건 · 교차 검증 9건 · 데이터 품질 14건 · 값 이탈 31건 (총 298건 중)

     [설비 장애] 통신이상·UPS·펌프 등 설비 DI 직접 감지 (확정 사고)

     [점검 순서] ① 설비 장애 → ② 교차 검증 → ③ 데이터 품질 → ④ 값 이탈
     ```

  2. **scope: 행정1수청 소소블록 (1 row, all 정상)** — `source: template` (LLM 호출 없음, **15ms**):
     ```
     [중요 알람] (행정1수청 소소블록) 이상 단계 태그가 없으며 전 시설 정상 범위에서 동작 중입니다.

     [유형별 현황] 설비 장애 0건 · 교차 검증 0건 · 데이터 품질 0건 · 값 이탈 0건 (총 1건 중)

     [점검 순서] ① 설비 장애 → ② 교차 검증 → ③ 데이터 품질 → ④ 값 이탈
     ```

  3. **scope: 없는시설 소블록 (0 rows)** — `source: template`:
     ```
     없는시설 소블록에 현재 이상 탐지된 태그가 없습니다.
     ```

- **속도 비교 (체감):**
  | 케이스 | 기존 (구버전) | Hybrid (이번) |
  |---|---|---|
  | 전역 LLM 경로 | ~30~45s | ~38s (비슷) |
  | 0건 scope | ~30s (LLM 호출됨) | **즉시** (LLM 호출 안 됨) |
  | 빈 케이스 (전역) | ~30s | **즉시** |
  - 응답 품질은 대폭 향상 (구체 1건 + 카테고리 카운트 + 정의 + 가이드)

- **할루시네이션 디버그 (이번 작업 중 발견):**
  1. **`num_predict=100` 빈 응답**: gemma4의 chat 템플릿 토큰이 budget을 먼저 소진. 해결: None으로 설정해 모델 기본값 사용
  2. **`30.0` 위반**: LLM이 "30일 평균" 언급 → allowed_numbers에 없어 거부. 해결: 프롬프트 상수 `0/1/30` 화이트리스트 추가

- **운영팀 커스터마이징 가이드:**
  - 카테고리 정의 문구 변경 → `CATEGORY_MEANINGS` 사전 수정 (코드 1줄)
  - 점검 순서 변경 → `CATEGORY_PRIORITY` 리스트 순서 변경 (코드 1줄)
  - 새 카테고리 추가 → 위 4개 사전에 키 추가 + `_classify_row`에 분류 로직 추가

- **관련 파일:**
  - `slm/endpoints/scan_all_explain.py` (전면 재작성, ~440 lines)
  - `slm-dashboard/slm-dashboard/src/components/chat/anomaly/AnomalyScanView.tsx:255-280`

- **관련 커밋:** `slm@f4355a9` / `slm-dashboard@e588b3f` / `web@09eb77d` / `web@9e60912`

---

### [E-024] 위기대응 검출 로직 다이어그램 시각 디자인 개선

- **날짜:** 2026-04-13
- **사용자 요청:** "(로직점검 프로세스)의 도형 화살표 및 디자인을 디자인 관점에서 가독성 있고, 직관적으로 개선해줘"
- **이전 디자인의 문제점 ([E-019] 1차 구현):**
  1. 박스 너무 작음 — `min-w-[84px]`, `text-[11px]` → 가독성 떨어짐
  2. 형태 구분 없음 — 알고리즘 시작 박스(blue)와 일반 단계 박스(green)가 같은 사각형 → 흐름의 시작점 시각적으로 안 보임
  3. 화살표가 텍스트 — `→` `↓` 글자 사용 → 시각적 무게 없음, 방향성 불명확
  4. 검출 강조 약함 — `ring-2 + scale-105` → 다른 박스와 큰 차이 없음
  5. dimmed 모호 — `opacity-30 + grayscale` → 거의 안 보여서 전체 흐름 추적 어려움
  6. 색 대비 약함 — `bg-*-500/15` → 박스가 배경과 거의 비슷
  7. 행 구분 없음 — `arrow-down-connector`가 단순 `↓` 한 글자 → 단계 phase 구분 모호

- **개선 (`AlarmAnalysisDetail.tsx`의 `DiagramFlow` + `DiagramBox` 신규 추출):**

  **A) 박스 디자인**
  - 크기: `min-w-[100px] max-w-[150px]` + 패딩 `px-3 py-2.5` (이전 84px/p2)
  - 텍스트: `text-[12px] font-semibold` (이전 11px/medium)
  - 형태 분리:
    - **알고리즘 시작 (blue)**: `rounded-full` (pill) + `PlayCircle` 아이콘 — 흐름의 진입점 즉시 시각화
    - **일반 단계 (green/yellow/...)**: `rounded-xl` (rounded-rect) — 작업 노드
  - 색상 강도 향상: `bg-*-500/15` → `bg-*-500/20` + `border-*-500/50~60` 2px solid

  **B) 검출 단계 강조** (`DETECTED_CLASSES`)
  - `border-red-500` + `bg-red-500/20` + `text-red-100`
  - `shadow-lg shadow-red-500/30` (글로우 효과)
  - `scale-[1.08]` (시각적 부각)
  - `ring-2 ring-red-500/60 ring-offset-2 ring-offset-background` (외곽 ring)
  - **박스 위쪽에 떠 있는 "✓ 검출" 배지**: `bg-red-500 px-2 py-0.5 rounded-full` + `CheckCircle2` 아이콘 + ring으로 배경에 분리

  **C) Dimmed 단계 명료화** (`DIMMED_CLASSES`)
  - `border-dashed border-border/50` — 점선 테두리로 "실행 안 됨" 의미 강조
  - `bg-transparent` — 배경 비움
  - `text-muted-foreground/50` — 회색 텍스트
  - `scale-95` — 약간 축소
  - 박스 위쪽에 작은 `Circle` 마커 (회색 점) — 추가 시각 단서

  **D) 화살표 — Lucide 아이콘으로 전환**
  - 가로 화살표: `ChevronRight` (`size-4 stroke-[2.5]`)
    - 검출 전: `text-sky-400/80`
    - 검출 박스 직후: `text-red-400/60`
    - dimmed 영역: `text-border/40`
  - 행 구분자: `ChevronsDown` (`size-5`) + 양쪽 horizontal divider
    - `bg-gradient-to-r from-transparent to-border/60` 으로 자연스럽게 페이드

  **E) 컨테이너**
  - `rounded-xl border bg-gradient-to-b from-muted/30 to-muted/5 p-4`
  - 그라데이션 배경으로 깊이감
  - 검출 헤더: `border-red-500/30 bg-red-500/10` + CheckCircle2 아이콘 + 검출 단계 라벨 + 안내 텍스트

- **검증 (Playwright 라이브 시각 확인):**
  - `/crisis/alarm-analysis` → 송산2산단(배) 1지 수위 LL 알람 클릭 → 다이어그램 영역 스크린샷
  - 검출 단계 "유입유출량 분석"이 빨간 테두리 + ✓ 배지 + scale로 즉시 식별됨
  - 이후 8개 박스가 dashed border + 회색으로 "실행 안 됨" 시각화
  - Algorithm start (수위분석/가압장분석) pill 형태 + PlayCircle 아이콘 명확
  - ChevronRight 화살표가 단계별 색상 차등 (sky → red → grey)
  - ChevronsDown 행 구분자가 phase 경계 명확화
  - tsc --noEmit 신규 에러 없음

- **참고 사항:**
  - dev 환경에서 Turbopack 캐시 stale로 한 번 build error 발생 → `touch` 후 정상화
  - 좁은 패널(~500px)에서는 row 5개 박스가 2줄로 wrap되지만 ChevronsDown 구분자로 phase 구분은 유지됨

- **관련 파일:**
  - `slm-dashboard/slm-dashboard/src/components/crisis/AlarmAnalysisDetail.tsx:354-440` (DiagramFlow + DiagramBox)

- **관련 커밋:** `slm-dashboard@e224c46` / `web@853e108`

---

### [E-025] 멀티모달 현장 진단 MVP (Phase 1~5) — 채팅에서 장비 사진 → AI 참고 의견

- **날짜:** 2026-04-14
- **배경:** Plan(`docs/ultraplan_*.html`)에서 구상한 3가지 워크플로우 중 **채팅 멀티모달 진단**(Workflow A)을 P1~P5 5단계로 구현. P6(작업 등록 연동) + P7(현장 설비 등록 Dialog)은 별도 세션으로 분리.

- **아키텍처 결정:**
  1. **에이전트 분리** — `vision_agent.py`를 별도 FastAPI 프로세스(포트 8100)로 격리. Zero-Hallucination 경계를 프로세스 수준에서 강제 + VLM 모델 리스크를 ai_server에서 분리
  2. **단일 모델 재사용** — 기존 `gemma4:26b-a4b-it-q4_K_M`이 vision 지원(`/api/show` `capabilities: ['completion','vision','tools','thinking']` 확인)으로 밝혀져 신규 vision 모델 추가 불필요. ai_server(텍스트)와 vision_agent(이미지)가 같은 Ollama 인스턴스에 있는 동일 모델을 공유 (~19.7GB VRAM 1벌)
  3. **Proxy route 재사용** — `/api/proxy/[...path]/route.ts`가 이미 multipart/form-data + SSE 양쪽 모두 지원하므로 별도 multimodal 프록시 라우트 불필요

- **구현 단계 (5 phase):**

  **P1 — DB 마이그레이션** (`db/migrations/0043_vision_agent.sql`)
  - 신규 3개 테이블: `tb_equipment_image`, `tb_equipment_manual`, `tb_vision_session`
  - 기존 확장: `tb_task_master.vision_session_id`, `tb_equipment_info.equipment_photo_url`, `tb_equipment_info.nameplate_photo_url`
  - **중요 수정**: plan의 `tb_equipment`는 실제 테이블명 `tb_equipment_info`로 매핑. PK는 `(equipment_id)` 단일(멀티테넌시 아님)
  - FK: `tb_equipment_image.equipment_id` → `tb_equipment_info(equipment_id)`, `tb_vision_session.linked_task_id` → `tb_task_master(task_id)`, `linked_equipment_id` → `tb_equipment_info(equipment_id)`

  **P2 — `vision_agent.py` MVP** (신규, ~400 lines)
  - FastAPI 프로세스, 포트 8100
  - 엔드포인트: `/health`, `POST /vision/diagnose`, `POST /vision/register-parse`, `POST /vision/manual-search` (스텁)
  - Ollama `/api/generate` 호출 (`images: [base64]` 파라미터), `VISION_MODEL=gemma4:26b-a4b-it-q4_K_M`, `VISION_KEEP_ALIVE=24h`
  - 장비 화이트리스트 (PLC/유량계/모뎀/RTU/펌프/밸브/수위계/압력계/UPS/기타)
  - Zero-Hallucination 가드: advice_text 접두어 `[AI 참고 의견]` 강제 + 수치 생성 정규식 감시 + 권고·조치 금지 프롬프트 규칙
  - 이미지 경로 해석: `/api/files/facility/...` / `/files/...` / 절대 경로 fallback 3종 지원
  - JSON 응답 파싱 (```json ... ``` 또는 중괄호 추출), 파싱 실패 시 기본값
  - **디버그 이력**: `num_predict` 옵션이 있으면 gemma4가 chat 템플릿 budget 소진 → None으로 설정

  **P3 — 매뉴얼 RAG** (스텁만)
  - 구조만: `/vision/manual-search` 엔드포인트 + `tb_equipment_manual` 테이블 준비
  - 실제 임베딩 로직은 매뉴얼 PDF 원본 확보 후 별도 phase

  **P4 — `ai_server.py` 라우팅** (`endpoints/vision_proxy.py` 신규)
  - 신규 엔드포인트 `POST /ask/multimodal/stream` (multipart, SSE)
  - 기존 `/ask/stream`(JSON)은 그대로, 멀티모달 경로만 분리 → Zero-Hallucination 격리
  - SSE 4단계: `classify → extract → fetch → result`
  - 이미지 저장: `/web/files/chat_attachments/<uuid>.jpg`
  - vision_agent 호출 후 `tb_vision_session` INSERT → `vision_session_id` 반환
  - 응답 스키마: `vision_advice` 필드로만 격리, `answer_text: null` 명시 (DB 사실 필드 비움)
  - ai_server.py에 `init_vision_proxy(get_db_connection)` + `include_router` 등록

  **P5 — 프론트 채팅 멀티모달 UI**
  - **타입** (`src/lib/types/chat.ts`): `VisionAdvice`, `VisionManualExcerpt` 인터페이스 신규, `AiServerResponse.vision_advice` + `ChatMessage.bot.vision_advice` 필드 추가
  - **스트림 헬퍼** (`src/lib/chat-stream.ts`): `streamMultimodalChat()` 신규 — FormData 전송, SSE 파싱, `MultimodalChatRequest` 타입
  - **훅** (`src/hooks/use-chat-submit.ts`): `executeMultimodalStream()` 추가, `handleSubmit(images?: File[])` 시그니처 확장 — 이미지 있으면 멀티모달 경로, 없으면 기존 경로
  - **Input UI** (`src/components/chat/ChatInput.tsx`): 카메라 버튼(capture="environment") + 이미지 버튼 + 썸네일 칩(최대 3장, X 제거), 10MB 크기 제한, placeholder 동적 변경
  - **VisionAdviceCard** (`src/components/chat/VisionAdviceCard.tsx` 신규, ~180 lines): violet-500 테마, "AI 참고 의견" Camera 배지, 신뢰도 badge, 미등록 장비 amber 배지, 장비 추정·관찰 상태·참고 의견 본문·매뉴얼 인용 섹션, "작업 등록"/"설비 등록" 액션 버튼(현재는 disabled — P6/P7 예정), 면책 푸터 필수
  - **BotMessage 통합** (`src/components/chat/BotMessage.tsx`): `visionAdvice?` prop 추가, 답변 버블 위에 VisionAdviceCard 렌더
  - **Response Mapper** (`src/lib/chat-response-mapper.ts`): `response.vision_advice` → `ChatMessage.bot.vision_advice`로 매핑, summary 없을 때 `📷 {equipment_guess} ({type}) — {advice_text}`로 대체

- **검증 (Playwright 브라우저 E2E):**
  1. **백엔드 curl**: `/ask/multimodal/stream`에 합성 PLC 명판 이미지 + "이 장비 뭐야 고장인지 봐줘" 전송 → SSE 4프레임 순차 수신 → `vision_advice: {equipment_guess: "LS XBCH-16MW", confidence: 1.0, advice_text: "[AI 참고 의견]..."}` + `answer_text: null` + `vision_session_id: 1` + 29.6초 ✅
  2. **DB 확인**: `SELECT * FROM tb_vision_session` → row 1개, `user_id=admin`, `sitename=행정1수청`, `facilitytype=소소블록`, `agent_response->>'equipment_guess'='LS XBCH-16MW'` ✅
  3. **브라우저 UI** (`https://localhost:3000/chat`, jykim 로그인):
     - 카메라/이미지 버튼 2개 렌더 확인 ✅
     - 이미지 업로드 → 썸네일 칩 `fake_ls_plc` + X 버튼 + "1/3장 첨부됨 · 비전 진단 모드로 전송됩니다" 안내 ✅
     - placeholder 자동 변경: "사진에 대해 질문하거나 '진단해줘'라고 입력" ✅
     - 전송 후 SSE 4단계 progress chip (분류→추출→조회→렌더링) 표시 ✅
     - ~90초 후 VisionAdviceCard 렌더:
       - 장비 추정 섹션: "LS XBCH-16MW (PLC)" + 제조사/모델
       - 관찰 상태 2개 bullet (CheckCircle2 아이콘)
       - AI 참고 의견 본문 박스
       - "작업 등록" (purple) + "설비 등록" (amber) 버튼
       - **면책 푸터**: "본 AI 참고 의견은 사진에서 관찰된 내용만 기반합니다. 운영 지시가 아니며, 반드시 현장 확인 후 조치하세요. 수치·권고·판단은 포함되지 않습니다." ✅
     - violet-500 테두리로 기존 DB 사실 응답(slate 카드)과 시각적 완전 분리 ✅
  4. **TypeScript**: `tsc --noEmit` 신규 에러 0건

- **Zero-Hallucination 검증:**
  - advice_text 접두어 `[AI 참고 의견]` 강제 적용 확인
  - VLM이 수치 생성 0건 (검증 텍스트: "장비의 실제 작동 여부나 고장 여부는 사진만으로 판단할 수 없습니다")
  - `answer_text: null` 필드로 DB 사실 영역 격리
  - 프론트 렌더 violet 테마로 시각적 분리
  - 면책 푸터 필수 노출

- **미완료 / 다음 세션:**
  - **P6**: "작업 등록" 버튼 → `TaskFormDialog compact` 자동 채움 → `tb_task_master.vision_session_id` 연결
  - **P7**: "설비 등록" 버튼 → `EquipmentPhotoRegisterDialog` 3단계 Stepper → OCR 파싱 → `tb_equipment_info` INSERT
  - 매뉴얼 PDF 업로드 → `/vision/manual-search` 실데이터

- **관련 파일:**
  - 신규: `slm/vision_agent.py`, `slm/endpoints/vision_proxy.py`, `db/migrations/0043_vision_agent.sql`, `slm-dashboard/src/components/chat/VisionAdviceCard.tsx`
  - 수정: `slm/ai_server.py` (+9 lines router 등록), `slm-dashboard/src/lib/types/chat.ts`, `src/lib/chat-stream.ts`, `src/lib/chat-response-mapper.ts`, `src/hooks/use-chat-submit.ts`, `src/components/chat/ChatInput.tsx`, `src/components/chat/BotMessage.tsx`, `src/components/chat/ChatMessageArea.tsx`

#### [E-025] 후속: LED 관찰 프롬프트 강화 (slm@c19c3a4)

기존 프롬프트는 "관찰 가능한 상태"만 요구해 VLM이 LED 6개를 한 줄에 뭉뚱그리는 문제. 운영자 가독성을 위해 다음 강화:

- `_DIAGNOSE_PROMPT_TEMPLATE`에 "LED·표시등 관찰 가이드" 섹션 추가
- 각 LED 라벨을 **1개씩** 분리 강제, 색상(빨강/녹색/노랑) 함께 명시
- "허용 패턴 vs 금지 패턴" 예시로 단정 회피 학습 (`ERR 점등 → CPU 고장입니다 ❌`)
- `advice_text` 끝에 "각 표시등의 정확한 의미는 제조사 매뉴얼을 참조해 주세요." 안전 안내 자동 부착 — **의미 해석 책임 분산** (Zero-Hallucination 유지하면서 운영자에게 다음 단계 안내)
- `observed_state` JSON 스키마 안내도 LED별 분리 강제

검증 (동일 LS XGK CPUE 에러 사진):
| | Before | After |
|---|---|---|
| `observed_state` 항목 | 2 | **7** (LED 6개 + POWER) |
| LED별 분리 | 한 줄에 합침 | **각각 1줄** |
| 색상 명시 | 없음 | **있음** (RUN/STOP=노랑, ERR/REM/P.S./BAT/CHK=빨강) |
| 매뉴얼 안내 | 없음 | "각 표시등의 정확한 의미는 제조사 매뉴얼을 참조해 주세요." |
| 응답 시간 | 75s | **44s** (출력 길이 무관, 오히려 빠름) |
| 고장 단정 | 0건 | 0건 (유지) |

Playwright 브라우저 라이브 검증 완료 — VisionAdviceCard에 6개 LED bullet 정상 렌더 (RUN/STOP 노랑, 나머지 빨강).

#### [E-025] P6: 작업 등록 연동 (slm@3e487e6 + slm-dashboard@f5fb968)

채팅 VisionAdviceCard "작업 등록" 버튼 → TaskFormDialog compact 모드가 자동 채움 상태로 열림.

**백엔드** `slm/endpoints/alarm_crisis.py`:
- `/crisis/tasks` POST body에 `vision_session_id` 옵셔널 수용
- `tb_task_master` INSERT 컬럼 추가
- 저장 후 `tb_vision_session.linked_task_id` 역방향 UPDATE (동일 트랜잭션)

**프런트엔드**:
- `TaskFormDialog`에 `compact?` + `visionSessionId?` + `visionContext?` props 추가
- compact 모드: 시작/종료 시간 + 개별 태그 섹션 숨김 (현재 + 4시간 기본값 사용)
- 상단 보라 배너: "AI 비전 진단에서 등록 #N" + 장비명/유형/advice_text
- 앰버 경고 배너: "억제할 알람 유형을 선택하지 않으면 작업 중에도 알람이 정상 발생합니다" (자동 체크 금지 원칙 유지)
- `chat/page.tsx`에 `handleCreateTaskFromVision` → `setTaskDialogOpen(true)` + defaults 자동 채움 (sitename/facilitytype/task_category="정비"/task_content="[AI 참고 진단]...")
- TaskFormDialog `key` prop에 `vision_session_id` 바인딩 → 컨텍스트 변경 시 내부 state 재초기화 강제

**검증 (5회 반복 API + 브라우저 1회):**
- vision_session_id 10~14 → task_id 8~12 생성, linked_task_id 1:1 매치 ✅
- task_content = "[AI 참고 진단] LS XGK Test-N PLC — 테스트 N" 자동 저장 ✅
- 브라우저: "작업 등록" 클릭 → TaskFormDialog compact 오픈 → 보라 배너 + 앰버 경고 렌더 → 스크린샷 캡처 ✅

#### [E-025] P7: 현장 설비 등록 (slm@5e7c144 + slm-dashboard@e402285)

채팅 VisionAdviceCard "설비 등록" 버튼 → EquipmentPhotoRegisterDialog가 OCR+VLM 파싱 결과를 자동 채움. Plan의 3단계 Stepper는 단일 페이지 폼으로 간소화 (채팅 경로는 vision_agent가 이미 파싱 완료했기 때문).

**백엔드** `slm/endpoints/facility_crud.py`:
- `EquipmentCreateRequest`에 `equipment_photo_url` / `nameplate_photo_url` / `vision_session_id` 옵셔널 필드
- `create_equipment()` 확장:
  1. `tb_equipment_info` INSERT 컬럼에 사진 URL 2개 추가
  2. `vision_session_id`가 있으면 `tb_vision_session.linked_equipment_id` 역방향 UPDATE
  3. 사진 URL이 있으면 `tb_equipment_image`에 kind별 INSERT (nameplate + exterior, 최대 2건)
  4. 모두 동일 트랜잭션

**프런트엔드** `EquipmentPhotoRegisterDialog.tsx` (신규, ~280 lines):
- VisionAdvice 수신 → 상단 보라 배너로 AI 진단 결과 표시 (guess / confidence / observed_state)
- 시설유형/현장명 드롭다운 (`/autocomplete/candidates` 재사용, `_facilityMapCache`로 세션 캐시)
- 장비유형/제조사/모델 입력 (자동 채움, 편집 가능)
- `EQUIPMENT_TYPE_PREFIX_MAP` — "PLC"→"plc", "유량계"→"flow_meter" 등 10종 매핑 (백엔드가 prefix_N 자동 부여)
- 설명 자동 채움: `[AI 비전 등록] {guess} — {observed_state 상위 2개}`
- 업로드된 사진 경로 read-only 표시
- `createEquipment()` 호출 후 toast

**검증 (5회 반복 API 테스트):**
- vision_session_id 15~19 → plc_85~plc_89 생성 ✅
- 각 equipment → `linked_equipment_id` 1:1 매치 ✅
- `tb_equipment_image` 10 rows (5 × nameplate + 5 × exterior) INSERT 확인 ✅
- `has_photo=true`, sitename=행정1수청, facilitytype=소소블록 모두 정상 저장 ✅
- tsc --noEmit 신규 에러 0건 (기존 equipment-api.ts의 7개 에러는 pre-existing)

#### [E-025] P3: 매뉴얼 RAG 실구현 (slm@5d0374f)

장비 사진 진단 응답에 제조사 매뉴얼의 원문 페이지를 실데이터로 인용하도록 RAG 파이프라인을 완성. 기존 `manual_excerpts=[]` 스텁을 실제 cosine similarity 검색 결과로 대체했다.

**인덱싱** `slm/tools/index_manuals.py` (신규):
- `docs/매뉴얼/` 18개 PDF 스캔 → `pypdf` 페이지 추출 (≥100자 필터)
- 장문 페이지(>3000자)는 1500자 / 300자 overlap 분할 → 청크 단위
- Ollama `/api/embed` (snowflake-arctic-embed2, 1024 dim) 배치 16 호출 → L2 normalize
- `data/manual_embeddings/<embedding_key>.npz` 저장 (embeddings / pages / texts)
- `tb_equipment_manual` UPSERT (idempotent — title + npz 존재 매칭)
- 파일명 → `equipment_type` / `brand` / `model` 자동 매핑 (LS XGB/XGT/GLOFA/Master-K → PLC, G100/iS7 → 인버터, AC&T ETOS/IIoT → RTU, 4G-210N/EtherFOS → 모뎀)
- **1회 실행 결과:** 18개 매뉴얼 / 2469 페이지 / 2580 청크 / ~30초

**런타임 검색** `slm/vision_agent.py`:
- `_ManualRagIndex` 클래스 (lazy-load): 최초 검색 시 17개 NPZ + tb_equipment_manual 메타 전량 로드 (~10MB, RAM 상주)
- `_embed_query()`: snowflake-arctic-embed2로 쿼리 임베딩 → L2 normalize
- `search()`: `embeddings @ query_emb` dot product (L2 normalized이라 cosine) + 장비 타입 `+0.15` / 브랜드 `+0.10` soft boost
- `argpartition`로 top-k*4 후보 → (manual_id, page) 중복 제거 → top-k 반환
- `/vision/manual-search` 엔드포인트: 독립 RAG 검색 (equipment_type / brand / query / top_k)
- `/vision/diagnose`: VLM 식별 결과(equipment_guess / equipment_type / brand / observed_state)와 user_text를 concat → `_retrieve_manual_excerpts()` 호출 → `manual_excerpts` 필드 자동 채움 (장비 식별 실패 시 skip)

**검증 (5회 반복, `/tmp/ls_xgk_error.jpg` + "ERR LED 점등" 질의):**
- 전 5회 HTTP 200 ✅
- 전 5회 3/3 excerpts 반환 ✅
- avg top cosine score: **0.640** (문턱 0.5 상회)
- #1 결과 안정성: 5/5회 모두 `XGR-CPU_Manual_V3.0` **p251 "15.2.3 ERR.(Error) LED가 점등하고 있는 경우의 조치방법"** 검색됨 — 질의와 매뉴얼 원문 정확 매칭
- 보조 인용: `XGL-EFMTB` p32/34 LED 상태표 + `XGR-CPU` p247 WAR LED 조치
- avg VLM 생성시간 35.5s (gemma4:26b 비전), RAG 검색은 <50ms (index 사전 로드 후)

**독립 엔드포인트 검증 (manual-search 단독):**
```
POST /vision/manual-search {equipment_type:"PLC", brand:"LS ELECTRIC", query:"XGB ERR LED 점등 CPU 고장 원인"}
→ total_chunks: 2580
→ [0.556] XGR-CPU p251 "제15장 트러블슈팅 ERR LED 점등 조치방법"
→ [0.505] XGR-CPU p51 "WAR LED 경고 LED 용도"
→ [0.503] XGL-EFMTB p361 "XGK CPU 운전 중 에러 코드 및 조치방법"
```

**프런트엔드 E2E (Playwright 라이브):**
- `/chat` → 이미지 업로드(`ls_xgk_error.jpg`) → "ERR LED 점등 상태인데 관련 매뉴얼이 있으면 알려줘" 질의
- SSE 수신 → `VisionAdviceCard` (violet 테마) 렌더 → **관련 매뉴얼** 섹션 자동 노출
- 인용 3건: `XGT Series_Catalog_KR_202510 p.107` / `XGL-EFMTB_T8_Manual_V3.71_202601_KR p.33` (LED 표시부 명칭) / `p.358` (제9장 트러블 슈팅 XGL-EH5T 이상동작 LED)
- 작업 등록 / 설비 등록 버튼 + 면책 푸터 정상, 스크린샷 `e025-p3-manual-excerpts-rendered.png` 캡처

#### [E-025] P8: 기존 설비 DB 매칭 (slm@fdda15d)

진단 응답에서 항상 `is_registered=false`/`matched_equipment_id=null`이던 P5 스텁을 제거하고, VLM 식별 결과를 `tb_equipment_info`와 실제 매칭하도록 개선.

**`_match_existing_equipment()` 로직 (`slm/vision_agent.py`):**
1. `equipmenttype` + `sitename`으로 후보 축소
2. `meta->>'model' ILIKE '%VLM_model%'` 우선 (substring, 대소문자 무시)
3. 모델 매칭 실패 시 `meta->>'manufacturer' ILIKE '%VLM_brand%'` fallback
4. site 내 전부 실패 시 글로벌 검색으로 한 번 더 (updated_at DESC LIMIT 1)
5. 예외 발생 시 None 반환 (진단 응답은 계속 진행)

**검증 (5회 반복, sitename 5종 rotation):**
- 행정 → plc_1 매칭 ✅
- 석문 → plc_2 매칭 ✅
- 신평 → plc_3 매칭 ✅
- 송악1 → plc_4 매칭 ✅
- 갈산 → plc_79 매칭 ✅
- 전 5회 200 OK, 매칭 성공률 5/5, manual_excerpts 3/3 동시 유지
- VLM이 뱉은 모델(`XGK-CPUE`)은 DB(`XGI-CPUS`)와 미스매치였으나 brand fallback(`'LS' ILIKE '%LS%'` → `meta.manufacturer='LSE'`)으로 각 site의 PLC와 1:1 매칭에 성공
- VisionAdviceCard "미등록 장비" 앰버 뱃지가 사라지고 matched_equipment_id가 채워지는 동작 확인

#### [E-025] P9: 알람 연계 — 점검 → 장애 → 알람 단방향 플로우 (slm@6d8291a + slm-dashboard@13981be)

북극성 목표 "현장 작업자가 사진 → 진단 → 문제 감지 → 작업 등록 → 알람 해제"를 1화면·1플로우로 완결.

**P9a 백엔드 (`vision_agent.py`):**
- `ActiveAlarm` pydantic 모델 + `DiagnoseResponse`에 `has_issue`/`issue_reasons`/`active_alarms` 필드 확장
- `_detect_issue(observed_state, user_text)` — regex heuristic (Zero-Hallucination 유지, LLM 판단 아님): `(ERR|CHK|BAT|FAULT|ALARM|ALM) LED.*점등` / `빨간 LED 점등` / `(POWER|PWR|RUN) LED.*(소등|OFF)` / 외관 이상(파손/균열/부식/누수) / 연기·화재
- `_fetch_active_alarms(sitename, equipmenttype, limit=5)` — `tb_equipment_alarm_report WHERE alarm_end_time IS NULL` + `EXTRACT(EPOCH FROM NOW() - alarm_start_time)/3600` 경과 시간 계산
- 활성 알람 발견 시 `has_issue=True` 무조건 승격 + "연결된 활성 알람" reason 추가

**sitename 추론 (`vision_proxy.py`):**
- `tb_equipment_info`에서 `sitename`/`facilitytype` 캐시 로드 후 `user_text` substring 매칭 (가장 긴 매칭 우선, 행정1수청 > 행정)
- 프론트가 명시 안 한 경우에만 추론. `effective_site`로 `vision_agent` 호출 + `tb_vision_session` 저장

**P9c 알람 해제 API (`endpoints/alarm_crisis.py`):**
- `POST /crisis/alarm-reports/resolve` — body: `{alarms:[{alarm_start_time, tagsn}], resolution_note}`
- `alarm_end_time=NOW()` + `alarm_confirm_yn='Y'` + `user_cause_description` 누적 append
- `WHERE alarm_end_time IS NULL` 조건부 UPDATE로 이미 해제된 row 재해제 방지

**P9b 프론트 (`VisionAdviceCard.tsx`):**
- "문제 감지" 빨간 뱃지 (`has_issue=true`)
- "연결된 활성 알람 · N건" 섹션: 알람별 체크박스(기본 체크), 심각도 색상(경고=red / 주의=amber / 기본=sky), `duration_hours` 경과 라벨
- 작업 등록 버튼에 "+알람 N건 해제" 동적 배지
- `onCreateTask` 시그니처 `(keys: string[]) => void`로 변경, 선택된 키를 상위로 전달

**프론트 플로우 통합 (`BotMessage` + `ChatMessageArea` + `chat/page.tsx`):**
- `onCreateTaskFromVision(vision, selectedAlarmKeys)` prop drilling
- `pendingAlarmKeys` state + `handleTaskSubmit`에서 `createTask` 성공 후 `resolveActiveAlarms` 호출 (병렬, 실패해도 작업은 유지)
- toast에 "알람 N건 해제" 카운트 병기

**Playwright E2E 검증:**
- 사전 seed: `TEST_E025_P9_PLC_001/002` 2건을 행정/배수지/PLC로 등록 (경고/주의)
- `/chat` → ls_xgk_error.jpg 업로드 + "행정 배수지 PLC ERR LED 점등 확인" 질의
- sitename 추론 `행정/배수지` → VLM 진단 → vision_agent `_detect_issue` (에러 LED/빨간 LED) + `_fetch_active_alarms` 2건 → SSE result
- VisionAdviceCard 렌더: "문제 감지" 빨간 뱃지 ✅, "연결된 활성 알람 · 2건" 섹션에 "주의/PLC/1.1h 경과 RUN LED 소등" + "경고/PLC/3.1h 경과 ERR LED 점등 의심" 2건 체크 상태 ✅, 작업 등록 버튼에 "+알람 2건 해제" 배지 ✅
- 작업 등록 클릭 → TaskFormDialog compact 자동 채움 → 등록 → `task_id=13` 생성 (`vision_session_id=22` 연결) ✅
- DB 확인: 2건 모두 `alarm_end_time` 세팅됨 + `user_cause_description='[비전 점검 해제] LS XGK-CPUE'` ✅
- 스크린샷: `e025-p9-alarm-section-rendered.png`

#### [E-025] P10: 경보 목록 "비전" 해제 배지 + 5회 회귀 테스트 (slm-dashboard@9b41987)

북극성 플로우의 감사 추적을 시각화. 해제된 알람 중 "현장 사진으로 확인 후 해제"된 건을 경보 이력에서 한눈에 식별 가능하게 함.

**변경 (`slm-dashboard/.../AlarmReportTable.tsx`):**
- `isVisionResolved()` 헬퍼 — `user_cause_description`에 `[비전 점검 해제]` 포함 여부
- 경보 목록 "상태" 컬럼에 기존 `StatusBadge` 아래로 `<Badge><Camera /> 비전</Badge>` (보라 500) 추가 노출 (vertical flex)
- title attribute에 전체 resolution note 제공 (hover tooltip)

**E2E 검증 (Playwright):**
- `user_cause_description='[비전 점검 해제] LS XGK-CPUE 현장 확인 완료'` 테스트 알람 seed
- `/crisis/alarm-dashboard?tab=history` → 해당 행에 `경고` + `inactive` + `비전` 3배지 동시 렌더 확인, 다른 4778개 행엔 '비전' 배지 없음 (conditional 정상 동작)
- 스크린샷 `e025-p10-vision-badge-focused.png`

**5회 회귀 테스트 (slm@6d8291a + slm-dashboard@13981be P9 플로우):**
- 5개 site × 알람 seed (행정/석문/신평/송악1/갈산) × PLC 경고
- Round 1 (`/vision/diagnose` × 5): 전 5회 200 OK, `has_issue=True`, `active_alarms=1`, `manual_excerpts=3`, matched_equipment_id plc_1/2/3/4/79 (brand fallback로 전 매칭 성공), avg total 38.2s
- Round 2 (`/crisis/alarm-reports/resolve` × 5): 전 5회 `resolved=1`, DB 확인 5건 모두 `alarm_end_time` 세팅 + `user_cause_description='[비전 점검 해제] 회귀 RN'` 개별 저장

#### [E-025] P11: 경보 → /chat 딥링크 진입 (역방향 플로우) (slm-dashboard@f9e51b8)

북극성 플로우의 **진입점** 보강 — 원격 알람이 울리면 관리자가 경보 이력에서 한 번의 클릭으로 현장 확인 채팅으로 이동, 해당 site 컨텍스트를 자동 유지한 상태로 사진 업로드·진단을 수행하게 한다. 지금까지는 사용자가 `/chat`에 직접 가서 질의 텍스트에 site를 적어야 했다.

**변경:**
- `AlarmReportTable.tsx`: `useRouter()` import, 각 행에 카메라 버튼(보라) 추가. 클릭 시 `/chat?sitename=X&facilitytype=Y&prefill=...&alarm_tagsn=...&alarm_start_time=...` 로 navigate. `prefill`은 `"{sitename} {facilitytype} {equipmenttype} — {alarm_msg}"` 형식.
- `chat/page.tsx`: `deepLinkSite` state (sitename/facilitytype). 마운트 시 `useEffect`에서 `URLSearchParams` 읽어 `prefill` 있으면 `ChatInput.setText()` 호출, site 값은 state에 보존. URL 즉시 clean (`window.history.replaceState`).
- `ChatInput.tsx`: `siteContext` prop 추가 → `useChatSubmit({ siteContext })`에 전달. `node.setText = setMessage` ref 노출 (외부에서 textarea prefill).
- `use-chat-submit.ts`: `UseChatSubmitOptions` 인터페이스 + `siteContext` 옵션 → `streamMultimodalChat` 호출에 `sitename`/`facilitytype` 자동 첨부. deps 배열에 `siteContext.sitename/facilitytype` 추가.

**E2E 검증 (Playwright):**
- `TEST_E025_P11_DEEPLINK` 알람 seed (행정/배수지/PLC/경고)
- `/crisis/alarm-dashboard?tab=history` → 해당 행 카메라 버튼 클릭 → `/chat` navigate
- 확인: `textareaValue="행정 배수지 PLC — [P11 TEST] 딥링크 진입용 PLC 경고"`, URL 정리됨
- 이미지 업로드 + 전송 → 90초 내 VisionAdviceCard 렌더
- "연결된 활성 알람" 섹션에 시드한 알람 1건 정확히 표시 (경고/PLC/0.5h 경과)
- site context가 자동 첨부되어 vision_agent `_fetch_active_alarms(행정, PLC)`가 정확히 동작
- 스크린샷 `e025-p11-deeplink-flow.png`

이로써 북극성 루프가 **정방향(사진→진단→조치) + 역방향(알람→현장확인)** 양쪽 진입점이 모두 완성됨.

##### [E-025] P11 보정 — 경보 목록 카메라 버튼 제거 (2026-04-18, slm-dashboard@b804a06)

**계기:** 사용자 피드백 — "매번 태그 알람을 사진확인을 통해 정리할수는 없음". 경보 목록은 일일 수십~수백 건의 태그 알람이 쌓이는 뷰인데, 행별 사진확인 버튼은 "해제하려면 사진을 찍어야 한다"는 잘못된 기본값을 유도함.

**변경:** `AlarmReportTable` "작업" 컬럼의 Camera(`handleVisionCheck`→`/chat` 딥링크) 버튼 제거. ClipboardList(작업 등록)만 잔류. `useRouter`/`handleVisionCheck` 삭제. `Camera` import는 "비전 점검 해제" 뱃지(`isVisionResolved` 행)에서 계속 사용.

**유지되는 기능:**
- `/chat` 딥링크 자체(+P11 구현 — siteContext prefill, URL 정리)는 존속 — 다른 진입점(예: VisionAdviceCard, 설비 상세, 수동 navigate)에서 재사용 가능
- "비전 점검 해제" 뱃지는 유지 — 이미 처리된 이력 표시

**정책 기록:** `feedback_no_photo_per_alarm.md` — 경보 목록 행별 사진확인 버튼 배치 금지. 사진 기반 진단은 AI 채팅/설비 상세 등 선별 맥락에만 제공.

#### [E-025] P12: 명판/계기판 OCR 자동 등록 (slm@61737bb + slm-dashboard@b23c70d)

유량계 계기판이나 PLC 명판을 찍어 등록할 때 제조사·모델·S/N·용량·설치년도를 VLM OCR로 자동 판독하여 `EquipmentPhotoRegisterDialog`에 미리 채운다. 기존 P7은 VLM diagnose의 equipment_guess만 사용했는데, 그건 참고 의견 텍스트일 뿐 구조화된 필드가 아니었다. 이번에는 `/vision/register-parse` 전용 프롬프트를 활용해 JSON 스키마로 파싱된 값을 받아온다.

**백엔드 프록시 (`slm/endpoints/vision_proxy.py`):**
- `POST /vision/register-parse` — body `{image_url, image_kind}` → vision_agent(8100) `/vision/register-parse` 포워딩
- 기존 vision_agent 엔드포인트를 프런트에서 ai_server 경유로 호출 가능하게 함 (CORS·auth 일관성)

**API 클라이언트 (`slm-dashboard/.../equipment-api.ts`):**
- `OcrFields` 인터페이스 + `ParseNameplateResponse` 신규
- `parseNameplate(imageUrl, kind)` 헬퍼 (apiClient 경유)

**Dialog 확장 (`EquipmentPhotoRegisterDialog.tsx`):**
- `ocrFields` / `ocrText` props 추가
- brand/model 초기값 우선순위: **OCR → VLM diagnose → 빈 값** (OCR이 원문 보존이므로 상위)
- S/N / 용량 / 설치년도 input 필드 조건부 노출 (`ocrFields` 있을 때만)
- sky 테마 OCR 결과 배너 + `<details>` 원본 텍스트 토글
- `handleSubmit` → `commissioned_at = '{year}-01-01'` + meta에 serial/capacity/installed_year 저장

**chat/page.tsx 통합:**
- `handleRegisterEquipmentFromVision` 비동기화 — 버튼 클릭 → setEquipmentVisionContext + setOcrLoading(true) → `parseNameplate(vision.image_url)` 호출 → 결과 state 저장 → Dialog 렌더 (OCR 필드 자동 채움 완료)
- Dialog `key`에 OCR 여부 포함 (`vision-eq-{id}-{ocr|none}`) — 컨텍스트 변경 시 내부 state 재초기화 강제
- 우하단 "명판 OCR 판독 중..." 로딩 인디케이터 (sky 테마, 로딩 중에만)

**Playwright E2E 검증:**
- `/chat` → ls_xgk_error.jpg 업로드 + "이 PLC 등록해줘" 전송 → VisionAdviceCard 렌더
- "설비 등록" 버튼 클릭 → register-parse 자동 호출 (~30s VLM)
- Dialog 오픈 → OCR 섹션 확인:
  - 제조사: **LS** ✅
  - 모델: **XGP-ACF2, XGK-CPUE** ✅
  - 원본 OCR 텍스트: "XGP-ACF2 / POWER / XGT / PROGRAMMABLE LOGIC CONTROLLER / XGK-CPUE / RUN/STOP / REM. / ERR. / P.S. / BAT. / CHK." (12라인)
- brand input "LS", model input "XGP-ACF2, XGK-CPUE" 자동 채움 확인
- S/N / 용량 / 설치년도 3개 입력 필드 렌더 (PLC는 명판에 이 필드 없어 공란, 유량계 계기판일 경우 자동 채움 예상)
- 스크린샷 `e025-p12-ocr-autofill-dialog.png`

#### [E-025] P13: 시설물 사진 등록 (slm@e04df56 + slm-dashboard@cf87358)

장비(equipment) 등록과 별도로, 시설(facility) 자체의 현장 사진/계통도/매뉴얼 슬롯(`tb_facility_file`)에 1-click 등록. 기존 admin 업로드 엔드포인트는 multipart 재전송이 필요했지만, 채팅에서 이미 서버에 있는 파일을 경로로만 전달해 복사 + DB UPSERT 한다.

**백엔드 (`slm/endpoints/vision_proxy.py`):**
- `POST /vision/register-facility-photo` 신규
- body: `{image_url, region, sitename, file_type, vision_session_id?}`
- `file_type` ∈ `{site_photo, system_diagram, manual}` (기존 `FACILITY_FILE_ALLOWED_TYPES` 재사용)
- 로직: `image_url` → 로컬 경로 해결 → `facility/{file_type}/{uuid}{ext}` 복사 → `tb_file_storage` INSERT → `tb_facility_file` UPSERT (같은 region/sitename/file_type 슬롯) → 이전 파일 디스크+DB 정리
- **savepoint 격리** — vision_session 역연결(linked_facility_file_id) 시도가 실패해도 facility_file 삽입은 보존 (구버전 스키마에서 컬럼 없는 경우)

**API 클라이언트 (`equipment-api.ts`):**
- `FacilityFileType` 타입 + `RegisterFacilityPhotoPayload` / `Response`
- `registerFacilityPhoto(payload)` 헬퍼

**새 Dialog (`FacilityPhotoRegisterDialog.tsx`):**
- 시설유형/현장명 Select (autocomplete 캐시 재사용, sitename 추론으로 자동 채움)
- 파일 유형 3개 카드형 버튼 (현장 사진 / 계통도 / 매뉴얼)
- AI 비전 진단 참고 배너 (옵셔널)
- 저장 시 `registerFacilityPhoto` 호출 + toast + auto-close
- emerald 테마 (설비 등록의 amber와 시각적 분리)

**VisionAdviceCard 버튼 추가:**
- `onRegisterFacilityPhoto` prop → "시설물 사진" 버튼 (emerald, ImageIcon)
- 기존 작업 등록 + 설비 등록 + **시설물 사진** 3개 액션 병렬 노출

**prop drilling:** BotMessage → ChatMessageArea → chat/page.tsx `handleRegisterFacilityPhotoFromVision`

**E2E 검증 (Playwright):**
- `/chat` → ls_xgk_error.jpg 업로드 + "행정 배수지 현장 사진" 전송
- VisionAdviceCard 렌더 → "시설물 사진" 버튼 클릭 → Dialog 오픈
- 시설유형=**배수지** / 현장명=**행정** 자동 채움 (sitename 추론 효과)
- 파일 유형 3개 버튼 렌더, 현장 사진 기본 선택
- 등록 클릭 → `POST /vision/register-facility-photo` → 200 OK (initial 실패 후 savepoint 수정 → 재검증 성공)
- DB 확인: `tb_facility_file` row 1건 (`행정/site_photo`, file_url=`/api/files/facility/site_photo/3bd1c993..jpg`, uploaded_by=`vision_agent`)
- 스크린샷 `e025-p13-facility-photo-dialog.png`

**버그 수정 메모 (savepoint):** 최초 구현에서 `linked_facility_file_id` UPDATE가 실패했을 때 try/except로 삼켰지만 psycopg2 트랜잭션이 error state로 들어가 이후 commit이 rollback되어 row가 사라지는 현상. 해결: `SAVEPOINT vision_link` / `ROLLBACK TO SAVEPOINT`로 격리.

#### [E-025] P14: RAG 품질 개선 — manual_type user_manual boost (slm@184764e)

기존 `/vision/diagnose` 쿼리에서 observed_state LED 라벨(`XGT`/`XGP`/`XGK`)이 카탈로그 목차·스펙 페이지를 끌어올리던 문제(review-items #1)를 manual_type 기반 soft boost로 해결. docs/review-items.md #1 + #4 동시 해결.

**Migration (ALTER TABLE):**
- `tb_equipment_manual.manual_type` 컬럼 추가 (default `user_manual`)
- 기존 17건 UPDATE by title pattern: `ILIKE '%Catalog%' OR LIKE '%카타로그%'` → **catalog 4건** (G100 Catalog, XGT Catalog, iS7 Catalog, master-k 카타로그) / 나머지 **user_manual 13건**

**RAG Index 변경 (`vision_agent.py _ManualRagIndex`):**
- `information_schema`로 manual_type 컬럼 존재 확인 후 조회 (backward compat — 컬럼 없으면 `'user_manual'` 상수 fallback)
- `_rows`에 `manual_type` 필드 포함
- `search()` soft boost:
  - `user_manual`: **+0.08** (트러블슈팅·조치방법 등 실전 정보 우선)
  - `catalog`: **−0.05** (표지·스펙·목차 위주의 표면적 키워드 매칭 억제)
- 기존 equipment_type(+0.15) / brand(+0.10) boost와 합산

**검증:**
- `/vision/manual-search` 3쿼리 ("ERR LED 점등 조치방법" / "PLC CPU 고장 원인 진단" / "XGK 트러블 슈팅") × top-5: **15/15 모두 user_manual** (catalog 0건)
- `/vision/diagnose` 전체 경로 (ls_xgk_error.jpg): `manual_excerpts` **3/3 user_manual**
  - #1 XGL-EFMTB p33 (LED 표시부 규격)
  - #2 XGR-CPU p251 (15.2.3 ERR LED 점등 조치방법)
  - #3 XGR-CPU p51 (WAR LED 용도)
- 이전엔 #1=`XGT Catalog p.107`이었으나 완전히 제거됨

**무리 없는 boost 설계:** 점수 차(+0.08 / −0.05)는 의미 있는 catalog 결과(예: 실제 dimension·온도 스펙)가 user_manual의 무관한 매칭을 덮지 않도록 제한적. NPZ 재인덱싱 없이 DB UPDATE + 런타임 boost만으로 즉시 효과.

#### [E-025] 10회 Web E2E 안정성 검증 (2026-04-15, web@3c3beea 시점)

P1~P15 완료 후 실제 브라우저에서 10회 반복 회귀 테스트. canonical 이미지(`docs/매뉴얼/plc 사진/xgk plc cpue.jpeg`) 사용, 매 회 업로드 → 질의 → VisionAdviceCard 렌더 대기 → DOM 파싱으로 핵심 필드 수집.

**테스트 조건:**
- 이미지: `xgk plc cpue.jpeg` (6.7KB, 실사 XGK CPUE 모듈)
- VLM: gemma4:26b-a4b-it-q4_K_M (temperature=0.1)
- 질의 텍스트: run별 다른 sitename 포함 (행정/석문/신평/송악1/갈산/죽동/성상1/남산7 rotation)
- 매 회 70초 대기 후 카드 렌더 확인

**결과 표 (10/10 성공):**

| # | equipment_guess | n_manual | n_xgk | top1 | actions |
|---|---|---|---|---|---|
| 1 | LS XGB 시리즈(PLC) | 3 | 1 | XGK-CPU p52 | 작업·시설물 |
| 2 | LS XGB 시리즈(PLC) | 3 | 1 | XGK-CPU p52 | 작업·시설물 |
| 3 | LS XGT 시리즈 PLC | 3 | **3** | XGK-CPU p51 | 작업·시설물 |
| 4 | LS XGB 시리즈(PLC) | 3 | 1 | XGB FEnet p9 | 작업·시설물 |
| 5 | LS XGB 시리즈(PLC) | 3 | 1 | XGR-CPU p248 | 작업·시설물 |
| 6 | LS XGB series PLC | 3 | 1 | XGK-CPU p52 | 작업·시설물 |
| 7 | LS PLC(PLC) | 3 | 2 | XGK-CPU p86 | 작업·시설물 |
| 8 | **LS XGK-CPUE(PLC)** ⭐ | 3 | **3** | XGK-CPU p48 | 작업·시설물 |
| 9 | LS XGT/XGB series PLC | 3 | 2 | XGK-CPU p49 | 작업·시설물 |
| 10 | LS PLC(PLC) | 3 | 0 | XGL-EFMTB p51 | 작업·**설비**·시설물 |

**집계:**
- 200 OK + VisionAdviceCard 렌더: **10/10** ✅
- manual_excerpts 3건 반환: **30/30** 청크 ✅
- XGK-CPU_Manual (신규 #18) 포함: 총 15/30 청크 — 8/10회 top에 등장
- catalog (XGT/G100/iS7) 노출: **0/30** (P14 boost 완벽 동작) ✅
- action buttons 작업+시설물: 10/10회 노출 (P6 + P13) ✅
- run 10: `설비 등록` 버튼 추가 등장 — 해당 회 matched_equipment_id=None (P8 글로벌 매칭 제거 동작 확인, is_registered=False 정상 노출)
- has_issue: 0/10 — 이미지 해상도 낮아 VLM이 ERR LED 명확 식별 실패, `_detect_issue` heuristic 미매칭 (Zero-Hallucination 원칙 유지, 오탐 아님)
- active_alarms: 0/10 — 해당 시점 seed 알람 없음 (정상)

**VLM 변동성 관찰:**
- equipment_guess가 10회 중 **XGB 5회 / XGT 1회 / XGK 1회 / LS PLC 2회 / XGT+XGB 1회** 혼재 — temperature=0.1에도 여전히 소폭 변동
- 장비 타입 분류는 **전 10회 PLC** 고정 (화이트리스트 효과)
- 매뉴얼 RAG는 **일관되게 user_manual 우선** — P14 boost가 VLM 변동과 무관하게 검색 품질 유지

**북극성 안정성 결론:** 정방향(사진→진단→작업) + 시설물 등록 + 설비 등록 + 매뉴얼 RAG가 10회 반복에서 0건 failure. VLM의 소폭 변동이 있어도 RAG는 manual_type boost로 일관성 확보. `_detect_issue` heuristic이 low-res 이미지에서 보수적으로 False를 내는 것은 Zero-Hallucination 관점에서 바람직한 동작. 스크린샷: `e025-10run-e2e-final.png`.

#### [E-025] AC&T 제품 RAG 직접 검증 (2026-04-15)

LS 제품(PLC/인버터) 위주로 E2E 검증을 했으므로 AC&T System 4개 제품(모뎀 2, RTU 2)의 RAG 품질을 직접 `/vision/manual-search` 호출로 검증. AC&T 제품 사진이 없어 VLM 경로는 스킵.

**DB 상태:**

| manual_id | equipment_type | brand | model | page_count |
|---|---|---|---|---|
| 1 | 모뎀 | AC&T System | 4G-210N | 67 |
| 2 | RTU | AC&T System | ETOS-XP | 65 |
| 3 | 모뎀 | AC&T System | EtherFOS-EZ | 43 |
| 7 | RTU | AC&T System | IIoT RTU | 56 |

**4개 쿼리 × top-5 결과 (20/20 MATCH):**

| 쿼리 | 기대 모델 | #1 score | top1 발췌 | top-5 MATCH |
|---|---|---|---|---|
| "LTE 4G 모뎀 통신 이상 해결 방법" | 4G-210N | 0.521 | p51 "4.2.11 [vmodem] 시리얼 케이블 → LTE 전환 설정" | **5/5** |
| "EtherFOS 이더넷 광통신 스위치 설치" | EtherFOS | 0.632 | p6 "2.4.1 이더넷 10/100 Base-T" | **5/5** |
| "ETOS-XP RTU 설치 통신 설정" | ETOS-XP | 0.637 | p43 "제3장 설치 및 배선" | **5/5** |
| "IIoT RTU 하드웨어 단자 배선" | IIoT | **0.689** | p31 "제3장 설치 및 배선 3.1 전원 및 커넥터 규격" | **5/5** |

**검증 포인트:**
- `brand='AC&T System'` + `equipment_type=모뎀/RTU` soft boost로 타 제조사(LS ELECTRIC) 매뉴얼 완벽 배제 — 20건 중 catalog/타 브랜드 0건
- 모든 결과가 해당 제품 매뉴얼의 **실제 관련 섹션**을 가리킴 (1장 개요, 2장 규격, 3장 설치/배선, 4장 세부 설정)
- IIoT RTU가 최고 score(0.689) — 매뉴얼 1p에 "IIoT RTU Series / AC&T System Co., Ltd." 명시되어 brand+model 일치도 최상
- P14 user_manual boost 효과 확인 — AC&T 4개 모두 user_manual이라 homogeneous. catalog 혼재 없음

**미검증 영역:**
- AC&T 제품 VLM 식별 정확도 — 실제 제품 사진이 없어 `/vision/diagnose` 경로 스킵
- 향후 AC&T 4G-210N/ETOS-XP/EtherFOS-EZ/IIoT RTU 사진이 추가되면 `docs/매뉴얼/<카테고리> 사진/`에 배치 후 VLM end-to-end 재검증 필요

#### [E-025] 3종 신규 이미지 VLM E2E + 인버터 whitelist 버그 수정 (slm@39becfb, 2026-04-15)

사용자가 `docs/매뉴얼/plc 사진/`에 PLC 3장 + AC&T LTE 모뎀(RCS-XG) 3장 + 인버터 1장(`inverter2.jpg`)을 추가. 각 카테고리 대표 1장씩 `/vision/diagnose` 직접 호출로 VLM 경로 검증.

**1. test_plc1.jpg (LS XBF-DR32H)** ✅
- VLM: equipment_type=PLC, brand=LS, model=XBF-DR32H, confidence=0.95
- matched_equipment_id=plc_1 (행정/PLC 매칭)
- manual_excerpts 3/3: XGL-EFMTB 트러블슈팅 2건 + XGB Cnet 각부 명칭
- has_issue=false (observed_state에서 "LED 상태 확인되지 않음")

**2. test_rcs_xg.jpg (AC&T RCS-XG LTE 모뎀)** ✅ (AC&T 첫 E2E)
- VLM: equipment_type=**모뎀**, model=RCG-XG (RCS 오타), brand=미상, confidence=0.95
- matched_equipment_id=None (brand 미상 + 행정엔 AC&T 모뎀 없음)
- **manual_excerpts 3/3 4G-210N_NS_NKA 매뉴얼 (AC&T)** — 완벽 매칭
  - #1 [0.668] p20 "2.5 LED 표시 내용 RCS-XG"
  - #2 [0.582] p24 "2.6.5 RCS-XG 상태 요청"
  - #3 [0.578] p21 "Call is active LED / STRx GREEN"
- **핵심 발견:** VLM이 brand를 못 잡아도 equipment_type=모뎀 필터 + 쿼리 내 "RCS-XG" 토큰으로 정확한 제품 매뉴얼 검색 가능

**3. test_inverter.jpg (LS S100 인버터)** ✅ (버그 수정 후)
- **최초 실행 결과 (버그):** equipment_type=**기타** (화이트리스트 미포함), manual_excerpts=**0건** (기타면 RAG skip)
- **근본 원인:** `EQUIPMENT_WHITELIST`가 `[PLC, 유량계, 모뎀, RTU, 펌프, 밸브, 수위계, 압력계, UPS, 기타]`로 **인버터 누락**. tb_equipment_manual에는 인버터 3건(G100 Catalog, G100 사용설명서 385p, iS7 Catalog)이 인덱싱되어 있었는데 RAG 경로에서 사용 불가했음.
- **수정 (`vision_agent.py`):** 3곳 업데이트 — `EQUIPMENT_WHITELIST` 리스트 + `_DIAGNOSE_PROMPT_TEMPLATE` 장비 종류 화이트리스트 문구 + JSON schema `equipment_type` enum. vision_agent 재시작.
- **재검증:**
  - equipment_type=**인버터** (정정), brand=LS, model=S100, confidence=0.95
  - observed: "1번/2번 인버터 7세그먼트 디스플레이 숫자 표시 + 리액터/변압기 연결"
  - **manual_excerpts 3/3 G100(C)_사용설명서** — LS 인버터 사용설명서 정확 매칭
    - #1 [0.559] p349 "9장 문제 해결하기" — 인버터 트립/경보 섹션
    - #2 [0.526] p357 "OC2 출력선 합선 / IGBT 문제 조치"
    - #3 [0.518] p356 "LV2 입력 전원 전압 저하 조치"

**영향 범위:** 인버터는 E-025 범위의 공식 장비 카테고리였으나 매뉴얼(manual 등록)만 있고 VLM 파이프라인(화이트리스트 + 프롬프트)이 누락된 불완전 지원 상태였음. 본 수정으로 일관성 확보.

#### [E-025] 매뉴얼 PDF 다운로드 경로 폐쇄망 대응 (slm@230dabf + slm-dashboard@3dbd834, 2026-04-15)

**사용자 시나리오:**
1. 관리자가 XGT 매뉴얼 직접 열람·다운로드
2. 현장 작업자가 비전 진단 → AI 설명 → 관련 매뉴얼 PDF 1-click 열람

**폐쇄망 요건:** 매뉴얼은 전부 로컬 서빙(PC), 외부 제조사(LS/AC&T) 홈페이지 링크 절대 금지.

**발견된 구조적 문제:**
- 기존 `tb_equipment_manual.file_url`이 `/api/files/manual/*`로 Next.js BFF 로컬 FS를 참조 — 하지만 `slm-frontend` 컨테이너엔 매뉴얼 PDF가 없음 (404)
- `slm-backend` 컨테이너가 `/web/files/manuals/` **ephemeral 경로**에 저장 (바인드 마운트 없음) — 재시작 시 소실 위험
- 결과: 프론트엔드 전혀 다운로드 불가, 배포 안정성 없음

**수정:**
1. **경로 이동:** `MANUALS_DEST_DIR` 기본값을 `/web/files/manuals`(ephemeral) → `/app/data/manuals`(`../slm/data/manuals` 바인드 마운트)로 변경. 기존 18개 PDF를 호스트 `/Users/jykim/slm/data/manuals/`로 물리 이동.
2. **URL 프리픽스:** `/api/files/manual/<name>` → `/api/proxy/files/manual/<name>`. Next.js BFF 프록시가 인증 게이트 통과 후 백엔드로 라우팅.
3. **백엔드 라우트:** `endpoints/admin.py`에 `GET /files/manual/{filename:path}` 신규 — `FileResponse`로 PDF 스트리밍, `_MANUALS_DIR` 밖 경로는 path traversal 403, UTF-8 `Content-Disposition filename*=`로 한글 파일명 지원.
4. **DB 업데이트:** 기존 17 rows(manual_id=15 master-k 제외)의 `file_url` 일괄 REPLACE.
5. **vision_agent 확장:** `_ManualRagIndex.load` SQL에 `file_url` 추가, `_rows` 저장, `search()` 결과에 포함. `ManualExcerpt` 모델에 `file_url: Optional[str]` 필드 추가.
6. **인덱서 동기화:** `tools/index_manuals.py`의 file_url 생성 코드도 새 프리픽스 사용 (향후 업로드 자동 적용).
7. **프런트엔드:**
   - `types/chat.ts VisionManualExcerpt` `file_url?: string | null` 필드
   - `VisionAdviceCard` 매뉴얼 인용 우측 상단에 `[Download PDF]` 버튼 (보라 테마, target=_blank) — 각 excerpt에 `ex.file_url` 있으면 노출
   - `/admin/equipment-manuals` "작업" 컬럼에 다운로드 아이콘(sky) + 삭제 아이콘(red) 병렬 배치

**외부 URL 부재 감사 (grep):**
- `slm/` + `slm-dashboard/src/`에서 `lsis|lselectric|actsystem` 도메인 참조 **0건**
- `web/docs/매뉴얼/XGF-DL16A_Manual.pdf` 내 LSIS URL은 PDF 콘텐츠일 뿐 우리 코드와 무관

**검증:**
- 백엔드 직접 호출: `GET /files/manual/XGK-CPU_Manual_V3.0_202508_KR.pdf` → 200, `content-type=application/pdf`, `content-length=6188563`, `%PDF-1.6` magic, UTF-8 filename 헤더 확인
- Next.js BFF 프록시: `/api/proxy/files/manual/*` → 401 (인증 게이트 정상, 세션 있으면 통과)
- `/vision/manual-search` 응답에 `file_url` 필드 포함 확인 (XGR-CPU / XGK-CPU / XGL-EFMTB 3건)
- `tsc --noEmit` 신규 TS 에러 0건

**미검증:** Playwright 브라우저 E2E는 세션 만료 + 비밀번호 정책(tb_user pw_migrated=t but user_pw NULL) 문제로 로그인 실패해서 UI 상 클릭 테스트 생략. 사용자가 실제 브라우저에서 로그인 상태로 확인 필요.

---

## 관련 파일

### [E-027] AI 채팅 행 — block_builder _STATUS_MARKER_MAP NameError

- **날짜:** 2026-04-17
- **증상:** AI 채팅에서 네트워크 상태 관련 인텐트 질의 시 응답 대기 상태로 hang
- **원인:** `response_builder.py`에서 UI 블록 빌더 함수를 `block_builder.py`로 모듈 분리할 때 `_STATUS_MARKER_MAP` 상수는 response_builder에만 남고 block_builder에 복사 안 됨. `wrap_status_marker()` 호출 시 `NameError: name '_STATUS_MARKER_MAP' is not defined` → TaskGroup 예외 → SSE 스트리밍 중단 → 프런트가 응답 대기 상태로 고정
- **해결:** `block_builder.py` 상단에 `_STATUS_MARKER_MAP` 리스트 정의 복사 (고장/이상 → error, 경고/주의/정지 → warn, 정상/양호/가동 → ok)
- **재발 방지:** 모듈 분리 시 의존 상수/함수도 같이 이동했는지 import 경로로 검증. grep 패턴: 함수 본문 내 대문자 변수 참조를 module scope에 정의 여부 확인
- **커밋:** `slm@b4426e2`

---

### [E-026] 용수흐름도 다이어그램 노드 좌표 충돌 — 도형 겹침

- **날짜:** 2026-04-16
- **증상:** 줌 13에서 석문2 소블록, 행정1-2 감압설비 등이 다른 노드와 겹쳐 2개 도형으로 표시
- **원인:** `seed_flow_diagram.py`의 `_place_subtree`가 브랜치 자식의 서브트리 높이를 고려하지 않고 고정 Y 오프셋만 적용 → 다른 부모의 자식끼리 동일 (x,y)에 배치. 13건 좌표 충돌, 30+ 노드 영향
- **해결:** 리프/브랜치 자식 분리 배치: 리프는 가로 한 줄 MAX_PER_ROW wrap, 브랜치는 재귀 서브트리 높이 반환값으로 Y 누적 추적. seed 재실행 후 99노드 모두 고유 좌표 확인 (최소 간격 0.01도)
- **재발 방지:** seed 스크립트에서 최종 좌표 충돌 검증 로직 추가 권장. API 응답의 좌표 uniqueness 보장
- **커밋:** `slm@a72a88c`

### [E-030] 태그 모니터링 인라인 스파크라인이 렌더되지 않음 — /trend/data 태그 15개 제한

- **날짜:** 2026-07-09
- **증상:** 태그 모니터링 표 현재값 셀 스파크라인이 0건 렌더. 콘솔 에러 없음(빈 series)
- **원인:** 백엔드 `POST /trend/data`는 `태그는 1~15개 선택 가능합니다` 제한. 페이지(50행)의 아날로그 태그(18개)를 한 번에 요청 → `{status,message}` 반환·`data.series` 없음. 10~12개까지는 정상, 16개↑ 실패
- **해결:** 프런트에서 tag_ids 를 12개씩 청크로 분할해 병렬 조회 후 `Object.assign` 병합 (`TagMonitoringTable`). 페이지당 소수 병렬 호출이라 저비용
- **재발 방지:** `/trend/data` 다건 조회 시 항상 ≤15개 청크. 대량 태그 시계열은 청크 유틸 경유. 필터 조건은 렌더 조건과 일치시킬 것(tagtype 문자열 다양성 주의)
- **커밋:** `slm-dashboard@b52d77d`

### [E-037] 채팅 오타 보정 안내가 다음 턴에 재표출 — 세션 파라미터 누적 누수

- **날짜:** 2026-07-16
- **증상:** "신펑 배수지…" 질의(보정 '신펑'→'신평' 정상) 후 같은 세션에서 "고대리 가압장…" 질의 시, 답변은 고대리로 정확한데 요약 상단에 이전 턴의 "* 입력 보정: '신편'→'신평'" 안내가 그대로 붙음 (사용자 스크린샷 리포트)
- **원인:** `param_extractor` 가 보정 이력을 `params["_corrections"]` 로 반환 → `session_manager.update_session` 이 **모든 non-None 파라미터를 accumulated_params 에 누적** → 다음 턴 `get_merged_params` 로 병합되어 새 턴에 보정이 없어도 이전 보정 안내가 `apply_corrections_to_answer` 에서 재렌더링
- **해결:** `update_session` 에서 `_` 접두 키(턴 파생 정보)는 누적 제외. 2턴 재현 테스트로 확인 (턴1 안내 표출·턴2 미표출) + 스모크 16/16
- **재발 방지:** 턴 한정 파생 데이터는 반드시 `_` 접두 키로 넣는다 — 세션 누적에서 자동 제외됨. 새 파생 키 추가 시 slot-filling 누적 대상인지 여부를 먼저 판단
- **커밋:** `slm@5803163`

### [E-038] GIS 첫 로그인 시 시설 마커 미표시 — 알람 리플만 표시

- **날짜:** 2026-07-17
- **증상:** 첫 로그인 후 GIS 관망도에서 알람 현장 링(리플)만 보이고 시설 마커·클러스터가 전부 안 보임. 시간이 지나면 "갑자기" 표시됨 (간헐)
- **원인:** 알람 리플은 DOM 오버레이(`<Marker>`)라 항상 생존하는 반면, 시설 마커/클러스터는 WebGL 레이어 — 초기 스타일 로드(외부 CDN style.json)·리로드 경합으로 react-map-gl 선언 레이어가 조용히 유실돼도 복구 장치가 없었음. `visible` 조기 return 이 훅보다 앞이던 잠복 훅 위반도 동반
- **해결:** idle 마다 `facility-clusters` 레이어 존재 확인 → 유실 시 Source/Layer 리마운트 (최대 3회 + console.warn 관측성). 훅 위반 수정 (`slm-dashboard`)
- **재발 방지:** WebGL 커스텀 레이어는 "추가했다"가 아니라 "존재한다"를 주기 확인. DOM 오버레이와 WebGL 레이어를 섞을 때 한쪽만 살아남는 비대칭 증상 패턴 기억. 부수 발견: 베이스맵 cartocdn 외부 의존 → 납품 전 오프라인 타일 번들 필요 (review-items 등재)
- **커밋:** `slm-dashboard@c7d3cdb`
- **재발 (2026-07-19):** 로그인 직후 알람 리플만 표시, 자가 복구 미동작. 원인 = 복구 상한 3회가 **세션 누적**이어서 초기 pmtiles 비동기 로딩 churn(스타일 확정 전 idle 반복) 중 3회를 소진하면 이후 유실이 영구 방치됨. 수정 = 상한을 **스타일 로드 에피소드당** 3회로 변경 — `style.load` 이벤트마다 카운터 리셋 + 300ms 뒤 즉시 재검사, 상한 도달 시 console.error 1회 관측. 검사 조건에 `getSource("facility-source")` 동반 확인 추가. 교훈: 재시도 상한 설계 시 "무한 루프 방지"와 "복구 포기"를 혼동하지 말 것 — 상한의 스코프(세션 vs 에피소드)를 명시적으로 정한다.
- **3차 재발 (2026-07-22):** 첫 로그인에서 동일 증상 재현 (새 세션 자동 재현은 실패 — 간헐 레이스). 기존 복구는 "레이어 존재"만 검사해 **레이어는 존재하는데 화면에 0개 렌더되는 유형**을 못 잡음. 수정 = 초기/스타일 에피소드 **20초 창** 안에서 `queryRenderedFeatures` 0건 + 소스 피처 존재 시 유실 판정·리마운트 (시간창 제한 = 시설 없는 곳으로 팬 시 오탐 방지). 교훈: 자가 복구의 판정 기준은 "리소스 존재"가 아니라 **"사용자에게 보이는 결과"**로 정의할 것. `slm-dashboard@60f2d5f` 이후.

### [E-039] 차트 첫 로드 이중 렌더 — 그려진 뒤 다시 그려짐

- **날짜:** 2026-07-17
- **증상:** 채팅 트렌드 카드·트렌드 메뉴에서 첫 로드 시 차트가 그려졌다가 곧바로 다시 처음부터 그려짐 (애니메이션 재시작 — "리프레시되는" 느낌)
- **원인:** ① `EChartWrapper` 가 next-themes `resolvedTheme` 확정 전(hydration 직전 undefined)에 차트를 초기화 → 직후 다크 테마 확정 시 ECharts **전체 재초기화** (테마는 init-time 파라미터). 모든 차트 공통 ② `TrendChart` 활성 비교 tag 를 effect 로 결정 → 데이터 도착 렌더 후 오버레이 적용 2차 전체 리드로우 (notMerge=true)
- **해결:** ① 테마 확정까지 1프레임 대기 후 1회만 초기화 ② 활성 tag 를 렌더 중 파생 계산(useMemo)으로 전환, 사용자 선택만 state 유지
- **재발 방지:** 신규 차트는 EChartWrapper 경유 필수. 차트 옵션에 들어가는 파생 상태는 effect 금지·렌더 중 계산 (chart-rendering-policy §이중 렌더 방지, trend-comparison-spec §7.7-4)
- **커밋:** `slm-dashboard@bfff649`
- **추가 사례 ③ (2026-07-19):** 트렌드 메뉴 SPA **재진입** 시 재발 보고 (행정 배수지) — 전역 zustand store 가 이전 데이터를 유지해 캐시로 즉시 그려진 뒤, 마운트 자동 재조회 결과가 도착하며 notMerge 리드로우 + 애니메이션 재시작. 해결: store 가 동일 태그·기간 재조회를 판별해 `silentUpdate` 플래그 → TrendChart 가 해당 갱신에 `animation:false` 적용 (사용자 조작 시 플래그 해제). chart-rendering-policy §이중 렌더 방지 3항
- **추가 사례 ④ (2026-07-19):** 배수지 모니터링(/monitoring/reservoir) 첫 화면 재갱신 — `useAutoRefresh` 가 enabled 전환 시 **즉시 1회 실행**되는데, 초기 로드(selectSite→loadTrendData)와 겹쳐 **이중 로드 레이스** (가드 lastUpdated 가 아직 null 이라 통과). 첫 차트가 그려진 몇 초 뒤 두 번째 응답이 데이터를 교체하며 갱신. 해결: 훅에 `immediate` 옵션(기본 true, 모니터링 페이지 false) + 콜백에 isLoading/isRefreshing 재진입 가드. 재진입 15s 실측 트렌드 호출 1회 확인. **패턴: 초기 데이터를 다른 경로로 로드하는 화면에서 자동갱신 훅의 즉시 실행 금지**
- **추가 사례 ⑥ (2026-07-19):** 모니터링 30초 **자동 갱신 자체**가 매 주기 400ms 모프 애니메이션으로 전체 시리즈를 다시 그려 "화면이 갱신"으로 보임 (notMerge=false 여도 update 애니는 동작. 행정 배수지 실측: 선택 23s 뒤 자동 갱신에서 재현). 해결: monitoring-view-store `loadTrendData({silent:true})` — 자동 갱신 결과는 silentUpdate 로 무음 반영. 검증: 30s 틱 전후 스크린샷 픽셀 diff **0** (bbox None)
- **추가 사례 ⑤ (2026-07-19):** /trend 재진입에서 재재현 — silentUpdate 는 **fetch 완료 후에만** 세워져, 재진입 **마운트 첫 렌더**(캐시 데이터)가 애니메이션을 다시 시작하고 직후 백그라운드 갱신이 이를 끊음. 해결: TrendChart `animationKey`(조회 조건 키) — 모듈 싱글턴 Set 으로 **같은 조회 조건은 세션 동안 최초 1회만 애니메이션** (SPA 라우팅 간 유지). 재진입 스크린샷 t0/t+2s 픽셀 동일 검증. **빈발 증상 — 진단 체크리스트를 chart-rendering-policy §이중 렌더 방지에 상비**
- **추가 사례 ⑦ (2026-07-19, 최종):** 배수지 모니터링에서 ④⑥ 수정 후에도 영상 재현 — 포렌식(ECharts 인스턴스 ID 추적) 결과 **마운트 0.6s 뒤 인스턴스가 통째로 교체**(ec_...423→426, dispose 훅 미경유 = next/dynamic(LoadableComponent) 경계의 간헐 리마운트). 근원은 라이브러리 레이어라 통제 불가로 판단 → **animationKey(같은 조회 조건 1회 애니메이션)를 MonitoringTrendBlock 에도 적용**해 재init 이 일어나도 무음 렌더. 검증: 재선택 후 SVG 애니메이션 변이 0건 (이전 +2,000×2 버스트). **교훈: 차트 재-draw 계열은 원인 차단과 별개로 animationKey 로 시각 재시작을 구조적으로 봉쇄할 것**

### [E-040] GIS 소블록경계가 관할 전체를 진회색으로 덮음 — SHP 임포트 스키마 불일치

- **날짜:** 2026-07-18
- **증상:** 오프라인 지도 전환 후 GIS 관망도에서 당진 관할 전체가 어두운 회색으로 덮이고 블록 경계선이 검정으로 표시 ("기존과 많이 달라졌다, 배경이 어둡다"). 콘솔에 MapLibre 경고 `Expected value to be of type number, but found null` 반복
- **원인:** 렌더러 색상 표현식 `["step", ["get","block_index"], …]` 은 숫자 `block_index` 를 요구하는데, SHP 임포트 파이프라인(`scripts/import-shp-layers.py`)이 생성한 `block_boundary.geojson` 은 원본 DBF 속성(`소블록` 이름)만 담고 `block_index` 를 넣지 않음 → step 이 null 을 받아 표현식 오류 → MapLibre 가 **기본색(검정) 폴백**으로 채색 (0.18 투명 검정 = 진회색). 구 geojson 에는 있던 속성을 SHP 파이프라인이 유지하지 않은 회귀
- **해결:** ① 임포트 스크립트가 `block_boundary`/`mid_block_boundary` feature 에 `block_index` (1-based) 를 부여하고 geojson 재생성 ② 렌더러 `buildBlockColorStep` 입력을 `["to-number", ["coalesce", ["get","block_index"], 0]]` 로 null-safe 화 — 속성이 없어도 파스텔 기본색으로 렌더
- **재발 방지:** SHP → geojson 변환 산출물은 **렌더러가 기대하는 파생 속성까지 포함**해야 함 (원본 DBF 속성 그대로는 불충분). MapLibre 표현식에 `["get", …]` 숫자 입력을 쓸 때는 항상 coalesce/to-number 가드 — 고객사 SHP 는 속성 스키마를 보장할 수 없음 (제품화 전제)
- **커밋:** web(스크립트+문서) + `slm-dashboard`(렌더러+geojson) 동시 커밋

### [E-041] GIS 블록 fill 이 확대 줌에서 조각남 — 경계선(LineString) SHP 를 fill 로 렌더

- **날짜:** 2026-07-19
- **증상:** GIS 관망도에서 소블록 채색이 축소 줌에선 정상인데 **확대하면 경계가 직선으로 절단되거나 색이 빠진 구역** 발생 (남산 일대 재현)
- **원인:** 소블록경계 SHP 는 폴리곤이 아니라 **POLYLINE(경계선)** — 임포트 산출 geojson 이 LineString 인 채로 fill 레이어에 들어감. MapLibre 는 폴리곤은 타일 경계에서 클리핑 후 재폐합하지만 **선은 재폐합하지 않음** → 블록이 한 타일에 들어가는 축소 줌은 정상, 여러 타일에 걸치는 확대 줌은 타일별 선 조각을 임의 직선으로 닫아 fill 이 깨짐. 부수: 복수 소스 파일 병합으로 **블록 33건 완전 중복**(fill 2중 겹침), 남산3 자기교차 링, 중블록 미폐합 선 1건
- **해결:** 임포트 스크립트 `_normalize_boundary_feats` — ① 폐합 LineString → Polygon 변환 ② 미폐합 선 제외(경고) ③ 완전 동일 지오메트리 중복 제거 ④ shapely 가용 시 자기교차 buffer(0) 보정 ⑤ 동명·상이 지오메트리 경고(합덕2·합덕3). 소블록 73→40, 중블록 11→10, invalid 0
- **재발 방지:** fill 로 그릴 레이어는 임포트 단계에서 **Polygon 타입 보장** 필수 — 경계선 SHP(POLYLINE) 를 그대로 fill 소스로 쓰지 말 것. 축소 줌 정상 + 확대 줌 조각 = "선을 fill 로 렌더" 신호 패턴
- **커밋:** web(스크립트) + `slm-dashboard`(geojson 재생성) 동시 커밋

### [E-042] GIS 레이어 데이터 교체가 브라우저에 반영 안 됨 — max-age 고정 캐시

- **날짜:** 2026-07-19
- **증상:** E-041 로 블록 geojson 을 교정·재빌드했는데도 브라우저는 여전히 깨진 렌더(전부 기본 블루·직선 절단) 표시. 서버 curl 은 신본, 데이터 참값 렌더(matplotlib)도 정상 — 브라우저만 구본
- **원인:** `/api/gis/layer/[id]`·`/api/gis/pipelines` 가 `Cache-Control: public, max-age=86400` — 브라우저가 24시간 동안 재검증 없이 구본을 재사용. 재빌드·구축 지도 설정 업로드로 데이터를 교체해도 최대 하루 동안 화면이 안 바뀜 ("재빌드 불필요 교체" 제품 스토리와 충돌)
- **해결:** 공용 `serveGeojsonWithEtag` (`src/lib/server/geojson-response.ts`) — `Cache-Control: public, no-cache` + mtime·size 기반 ETag, If-None-Match 시 304 (본문 미전송). pipelines 라우트는 업로드본 우선(resolveGisAsset)도 함께 적용
- **재발 방지:** **교체 가능한 데이터 파일 서빙에 고정 max-age 금지** — ETag/no-cache 재검증 패턴 사용. "서버는 신본인데 브라우저만 구본" 증상이면 Cache-Control 을 먼저 의심. 진단 순서: curl(서버) → 데이터 자체 렌더(참값) → 브라우저 캐시
- **커밋:** `slm-dashboard` (routes + 헬퍼)

---

- 시작 스크립트: `D:\web\start-services.bat`
- 시작 사양: `D:\web\docs\startup-spec.md`
- Next.js 설정: `D:\web\slm-dashboard\slm-dashboard\next.config.ts`
- HTTPS 인증서: `D:\web\certs\localhost.pem`, `D:\web\certs\localhost-key.pem`

### [E-043] 신규 FastAPI 엔드포인트 커넥션 풀 미반환 — 전 API 연쇄 500

- **날짜:** 2026-07-19
- **증상:** 메모·일정 알림 신규 API 배포 후 수 분 내 `/schedule/*` 뿐 아니라 알람 알림·epanet 등 **무관한 API까지 500** (connection pool exhausted). 30초 폴링 엔드포인트가 있어 고갈이 특히 빨랐음
- **원인:** `get_db_connection()` 은 풀 래퍼(`_PooledConnection`)를 반환하며 **`close()` 호출이 풀 반환 계약**. 신규 endpoints(memo.py, user_schedule.py)가 `conn.close()` 없이 커넥션을 잡고 놓지 않아 요청마다 풀 슬롯이 소모됨
- **해결:** 전 함수에 `try/finally: conn.close()` 추가. 재시작 후 연속 24회 호출로 고갈 없음 확인
- **재발 방지:** 신규 endpoint 는 기존 모듈(alarm_contacts.py)의 **finally close 패턴**을 복사해 시작할 것. 가능하면 `ai_server.db_conn()` 컨텍스트 매니저 사용. 폴링(30s 이하) 엔드포인트는 누수 시 고갈이 수 분 내 발생하므로 배포 직후 `docker logs slm-backend | grep PoolError` 확인 습관화

### [E-044] 트렌드 조회 창 9시간 과거 오프셋 — 시각 파라미터 나이브 절단

- **날짜:** 2026-07-20
- **증상:** 트렌드 계열 화면(트렌드 메뉴·배수지/가압장·GIS 인스펙터·타임라인)의 시각축이 "UTC 나이브"처럼 보임. 실측 결과 표시 문제가 아니라 **조회 창 자체가 9시간 과거** — "최근 2시간" 요청 시 9시간 전 데이터를 조회·표시 (라벨과 데이터가 자체 정합이라 발견 지연)
- **원인:** DB 세션 TZ 는 Asia/Seoul 인데, 프런트가 보낸 UTC ISO(`...Z`)를 백엔드가 `replace("Z","")` 로 **나이브 절단** → KST 로 오해석. GIS 타임라인은 이 어긋난 축에 맞춰 알람 시각을 KST→UTC 변환하는 보정까지 얹어 이중으로 꼬여 있었음. GBT baseline 도 학습(hour, KST)·서빙(hour, UTC 시계면) 시간 피처가 9시간 어긋난 채 운용
- **해결:** `parse_ts_kst()` — aware 파싱(naive 는 UTC 간주) 후 KST 변환해 필터·라벨 모두 KST 정합. GIS 타임라인 보정 제거. 채팅 plot 은 프런트 매퍼에서 오프셋 포함 시각을 KST 나이브로 정규화 (`normalizeKstTs`)
- **재발 방지:** ① 시각 문자열에서 **Z/오프셋을 잘라내는 코드 금지** — 반드시 aware 파싱 후 명시 변환 ② 시계열 창 검증은 "라벨" 이 아니라 **"지금 시각 데이터가 마지막 포인트로 오는가"** 로 실측 ③ 세션 TZ 를 가정하지 말고 `SHOW timezone` 확인

### [E-045] View Transition 테마 전환 번짐 미표시 — 스냅샷 콜백 내 DOM 미변경

- **날짜:** 2026-07-21
- **증상:** 테마 토글 원형 번짐(View Transitions) 적용 후에도 사용자 화면에서 번짐 없이 즉시 전환. 콘솔 오류 없음
- **원인:** `startViewTransition(() => flushSync(setTheme(...)))` 에서 next-themes 의 html class 반영은 **useEffect(비동기)** — 콜백이 끝나도 DOM 이 안 바뀌어 old/new 스냅샷이 동일 → 전환이 시각적으로 무효. 실제 테마 변경은 전환 밖에서 일어남
- **해결:** `toggleThemeWithCircle` — 콜백 안에서 `documentElement.classList` 를 **직접 동기 토글** + `style.colorScheme` 갱신 후 setTheme 은 상태·저장 동기화용으로만 호출. resolvedTheme 기준으로 "system" 상태 첫 클릭 무반응도 함께 해소
- **재발 방지:** View Transitions 사용 시 스냅샷 콜백 안에서 **DOM 이 동기적으로 바뀌는지** 반드시 확인 (React 상태 → effect 경유 반영은 비동기). 검증은 육안 대신 `document.getAnimations()` 로 `::view-transition-new(root)` 애니메이션 존재를 프로그램 확인

### [E-046] 상시 마운트 다이얼로그의 상태 잔존 + URL 자동 오픈 1회성 가드

- **날짜:** 2026-07-22
- **증상:** ① 알람 벨에서 다른 알람의 "중지"를 연달아 누르면 두 번째부터 알람 제어 창이 안 열림 ② 다이얼로그를 다시 열면 이전 알람 값이 남아 있음 (TaskFormDialog)
- **원인:** ① URL 파라미터 자동 오픈 가드가 **불리언 1회성**(autoOpenedRef=true) — 같은 페이지에서 파라미터만 바뀌는 재진입을 전부 무시 ② TaskFormDialog 는 `open=false` 여도 마운트가 유지되는데 폼 상태를 **useState 초기값**(마운트 1회)으로만 defaults 에서 복사 — 이전까지는 페이지 로딩 스피너가 다이얼로그 마운트를 지연시켜 우연히 동작
- **해결:** ① 가드를 **파라미터 키 문자열 비교**로 변경 (같은 파라미터 재처리만 차단) ② `open` false→true 전환 시 defaults 로 전 필드 재동기화 effect 추가
- **재발 방지:** 닫혀도 마운트 유지되는 다이얼로그는 useState 초기값으로 props 복사 금지 — **열릴 때 재동기화**가 기본. URL 트리거 가드는 불리언이 아니라 **처리한 파라미터 키** 기준. "우연히 동작"(로딩 지연 의존) 패턴 의심 시 fresh-mount 아닌 재진입 경로로 테스트
- **커밋:** `slm-dashboard@6617e11`

### [E-047] "애니메이션이 안 보임 = 미구현" 오인 — OS 동작 줄이기 + 자동화 VT 블라인드

- **날짜:** 2026-07-22
- **증상:** 테마 원형 번짐·GIS 패널 등장 효과가 맥·윈도우 두 PC 모두에서 전혀 안 보여 미구현/버그로 오인. 파라미터 상향 후에도 동일
- **원인:** 두 PC 모두 **OS 애니메이션 효과(동작 줄이기)가 꺼져 있어** prefers-reduced-motion: reduce → 설계된 폴백(테마 즉시 전환 + 전역 freeze)이 정상 동작한 것. 진단을 어렵게 한 부수 요인: 자동화(Playwright MCP) Chromium 은 View Transition 오버레이를 화면·스크린샷에 합성하지 못해 (중립 페이지 기본 크로스페이드도 미표시) 시각 검증이 원천 불가 — "실행됨"과 "보임"을 구분 못 한 채 4/4 통과로 보고했음
- **해결:** 사용자 OS 설정 켬 → 즉시 정상. 재발 방지 장치 2종 추가 — ① 테마 전환 폴백 시 **사유 콘솔 로그** (미지원 브라우저/동작 줄이기 구분) ② Tweaks 패널 **"모션 진단" 섹션** (OS 동작 줄이기 / View Transitions 지원 / 그래픽 가속 SwiftShader 검출)
- **재발 방지:** "효과가 안 보인다" 문의는 코드 디버깅 전에 **Tweaks → 모션 진단부터** 확인. 모션 검증 보고 시 "애니메이션 객체 실행"과 "시각적으로 보임"을 구분해 명시 (VT 는 실브라우저 육안만 유효 — ui-motion-policy §검증 노트)
- **커밋:** `slm-dashboard@560c053`, `7710df0`

### [E-048] 향후 전망이 선형 폴백으로 고정 — 컨테이너에 Chronos 미설치

- **날짜:** 2026-07-23
- **증상:** 난지마을 배수지 수위처럼 일주기가 뚜렷한 데이터의 향후 전망이 급락 직선(관측 밴드 클램프 바닥)으로 표시 — "8시간 후 한계 접근" 과장 판정
- **원인:** slm-backend 컨테이너 이미지에 torch/chronos-forecasting 이 없어 `chronos_forecast` 가 항상 실패 → **모든 전망이 선형회귀 폴백**. 주기 신호의 하강 위상에서 선형 외삽하면 구조적으로 급락 (하드 클램프가 바닥만 막아줌). requirements.txt 에는 있었으나 이미지가 구버전
- **해결:** 컨테이너에 chronos-forecasting 설치 후 재검증 — 동일 태그 method=chronos_bolt, 예측 1.58~1.8 (주기 범위 내 완만), "21시간 후 한계 접근"(실측상 합리적)
- **재발 방지:** ① pip 컨테이너 설치는 **컨테이너 재생성 시 소실** — 다음 `docker compose build slm-backend` 로 이미지에 반영할 것 ② 전망 카드 method 필드(chronos_bolt|linear)를 확인하는 습관 — linear 로 장기간 고정이면 엔진 로드 실패 의심 ③ trend_forecast 는 로드 실패를 1회 로그 후 고정 폴백하므로 배포 후 로그에서 "Chronos 로드 실패" 검색

### [E-049] 향후 전망 주기 왜곡 — Chronos 스텝 스케일 불일치 + 수위 상한 캡 오적용

- **날짜:** 2026-07-23
- **증상:** E-048(Chronos 복구) 후에도 난지마을 수위 전망이 진폭 0.2~0.3의 완만 곡선 — 실측(일주기 1.4~3.0)과 불일치, "N시간 후 한계" 판정 과민
- **원인 (2중):** ① 전망 그리드는 30분 간격인데 Chronos 는 스텝을 **입력 시계열 간격(6분 버킷) 단위**로 해석 — 48스텝 요청이 4.8시간 예측이 되고 이를 24시간으로 늘려 그려 주기가 5배 늘어짐 ② 수위 전망의 만수위 상한 캡(threshold/0.9)이 **하한 성격의 운영 한계(1.6)** 에도 적용돼 상단이 1.78에서 잘려 주기 고점(3.0) 소실
- **해결:** ① 입력 버킷 간격(중앙값)으로 필요 스텝 환산 → 세분 예측 후 30분 그리드 지점 추출 ② 만수위 캡은 threshold > 관측 최대일 때만 적용. 검증: 동일 태그 진폭 0.31 → **1.48 (1.49~2.97)**, 실측 주기와 일치
- **추가 (같은 날, 사용자 재검증):** 스텝 환산 후에도 ① 예측 파형 주기가 실측과 다르고 ② 실측 끝점과 안 이어짐. 조사: 세분 예측(288스텝)은 Bolt 원생 한도(64) 초과로 주기 붕괴 → **컨텍스트를 30분 버킷으로 다운샘플해 48스텝 원샷**으로 전환. 난지마을은 자기상관 스캔 결과 주기가 38h/12h 혼재·불안정(r≤0.55)인 수요 연동형이라 위상 점 예측이 원리적으로 불확실 — 중앙값 평탄화는 정직한 표현이며 진폭은 밴드가 담당. 안정 일주기 시설용 **seasonal_24h 경로**(r≥0.6 시 어제 패턴+레벨 시프트) 신설. **연속성 앵커**: 전망 시작을 마지막 실측에 맞추고 오프셋 선형 점감 (실측 2.74=전망 2.74 검증). 교훈: 전망 검증 항목 = 진폭 + **주기** + **연결점** 3종 세트
- **최종 (사용자 3차 검증 "옆 패턴과 흡사해야"):** 지배 주기 자동 탐지 도입 — 시간 격자(균일 30분, 결측 보존) 위에서 ACF 스캔(8~36h). 난지마을 = **14h 주기 r=0.81** (24h 고정 검사는 역위상 -0.74 — 위치 기반 배열이 결측 구간에서 시간축을 압축한 것도 검출 실패 원인). r≥0.6 이면 `seasonal_{N}h`: 직전 주기 패턴 반복 + 연속성 앵커. 최종 검증: method=seasonal_14h, 진폭 1.48(1.63~3.11), 연속 격차 0.0 — 실측 파형과 동형. 프런트 라벨 "반복 주기 패턴 (14시간 주기 재현)"
- **재발 방지:** ① 시계열 모델 호출 시 **스텝의 시간 단위 = 입력 간격**임을 명시 확인 (그리드 간격과 다르면 반드시 환산) ② 임계 기반 클램프는 임계의 방향(상한/하한)을 판별 후 적용 ③ 전망 검증은 "그럴듯한 곡선"이 아니라 **실측 주기 진폭 재현 여부**로

### [E-050] /chat "Maximum call stack size exceeded" — 라벨 함수 자기 재귀

- **날짜:** 2026-07-23
- **증상:** 외부/로컬 공통 — 트렌드 전망이 포함된 채팅 카드 렌더 시 오류 바운더리 ("오류가 발생했습니다 / Maximum call stack size exceeded")
- **원인:** seasonal 전망 라벨 도입 시 일괄 치환 스크립트가 새로 만든 `forecastMethodLabel()` **함수 내부의 사전 조회까지 자기 호출로 치환** → 무한 재귀. tsc 는 재귀를 오류로 보지 않아 게이트 통과
- **해결:** 내부 조회를 `METHOD_LABEL_FORECAST[method]` 로 복원. 외부 도메인 트렌드 질의 E2E 재검증 (차트 렌더·예외 0)
- **재발 방지:** ① 정규식 일괄 치환은 **치환으로 새로 생기는 코드(방금 추가한 함수 본문)를 치환 대상에서 제외**하거나 치환 후 diff 검토 ② 렌더 경로 수정 후에는 tsc 만으로 끝내지 말고 해당 카드 1회 실렌더 확인 (이번 건은 달력 검증만 하고 채팅 카드 미확인이 원인)
