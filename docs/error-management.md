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
netstat -ano | findstr ":8000.*LISTENING"
netstat -ano | findstr ":3000.*LISTENING"
```

---

## 관련 파일

- 시작 스크립트: `D:\web\start-services.bat`
- 시작 사양: `D:\web\docs\startup-spec.md`
- Next.js 설정: `D:\web\slm-dashboard\slm-dashboard\next.config.ts`
- HTTPS 인증서: `D:\web\certs\localhost.pem`, `D:\web\certs\localhost-key.pem`
