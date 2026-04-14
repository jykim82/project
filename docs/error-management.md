# SLM Dashboard — 에러 관리 파일

> 발생한 에러, 원인, 해결책을 기록. 동일 에러 재발 방지용.

---

## 에러 목록

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
  [가장 위급] {LLM 1문장 — 시설·태그·수치·카테고리 라벨}

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
     [가장 위급] 남산10 소블록의 남산10(블) 압력이 30일 평균 4.77 대비 0.4%의 편차와 -0.11의 Z-Score를 보이는 4.75로 교차이상 판정되었습니다 (설비 장애).

     [유형별 현황] 설비 장애 112건 · 교차 검증 9건 · 데이터 품질 14건 · 값 이탈 31건 (총 298건 중)

     [설비 장애] 통신이상·UPS·펌프 등 설비 DI 직접 감지 (확정 사고)

     [점검 순서] ① 설비 장애 → ② 교차 검증 → ③ 데이터 품질 → ④ 값 이탈
     ```

  2. **scope: 행정1수청 소소블록 (1 row, all 정상)** — `source: template` (LLM 호출 없음, **15ms**):
     ```
     [가장 위급] (행정1수청 소소블록) 이상 단계 태그가 없으며 전 시설 정상 범위에서 동작 중입니다.

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

---

## 관련 파일

- 시작 스크립트: `D:\web\start-services.bat`
- 시작 사양: `D:\web\docs\startup-spec.md`
- Next.js 설정: `D:\web\slm-dashboard\slm-dashboard\next.config.ts`
- HTTPS 인증서: `D:\web\certs\localhost.pem`, `D:\web\certs\localhost-key.pem`
