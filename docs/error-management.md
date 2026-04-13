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

## 관련 파일

- 시작 스크립트: `D:\web\start-services.bat`
- 시작 사양: `D:\web\docs\startup-spec.md`
- Next.js 설정: `D:\web\slm-dashboard\slm-dashboard\next.config.ts`
- HTTPS 인증서: `D:\web\certs\localhost.pem`, `D:\web\certs\localhost-key.pem`
