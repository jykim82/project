# SLM Dashboard — Windows → Mac Mini 이관 가이드

> ⚠️ **폴더 구조 정본은 `CLAUDE.md` 의 "📂 디렉토리 구조" 섹션**.
> 본 문서의 구조 다이어그램은 **2026-03 이관 시점 스냅샷**이므로 신규 폴더
> (`screenshots/`, `dev-logs/`, `dev-data/` 등)는 반영돼 있지 않다. 최신 구조는 CLAUDE.md 를 우선 참조.

## 대상 환경
- **이관 전**: Windows 11, D:\web (Next.js) + D:\slm (Python AI Server)
- **이관 후 (현재)**: Mac Mini M4 Pro 36GB + 외장 Thunderbolt SSD 1TB

## Mac 디렉토리 구조 (2026-03 이관 시점)

```
~/web/                              ← 내장 SSD (소스코드)
  slm-dashboard/slm-dashboard/      ← Next.js 프론트엔드
  certs/                             ← mkcert HTTPS 인증서
  docs/                              ← 프로젝트 문서
  db/                                ← DB init/seed SQL
  CLAUDE.md

~/slm/                              ← 내장 SSD (AI 서버)
  ai_server.py
  venv/
  *.py

/Volumes/ExtSSD/                    ← 외장 SSD (데이터)
  docker/
    timescaledb/                    ← PostgreSQL 데이터
    node-red/                       ← Node-RED 플로우
  web/
    files/                          ← 업로드 파일/이미지
  backup/                           ← DB 덤프 백업
```

> 이후 확장된 구조 (`screenshots/`, `dev-logs/`, `dev-data/`, `prototype/`,
> `scripts/`, `agents/`, `skills/` 등 포함) 는 CLAUDE.md 참조.

## 사전 준비 (사람이 직접)

1. Mac Mini 전원 ON → 초기 설정 완료
2. 시스템 설정 → 일반 → 공유 → **원격 로그인 ON**
3. 외장 SSD 연결 → 디스크 유틸리티에서 APFS 포맷 (이름: `ExtSSD`)
4. Docker Desktop for Mac 설치 + 실행 (https://docker.com)
5. Claude Code 설치:
   ```bash
   npm install -g @anthropic-ai/claude-code
   ```
6. Mac IP 확인: `ifconfig | grep "inet "`

## 자동 이관 절차 (Claude Code에 요청)

Mac에서 Claude Code 실행 후 아래 요청:

> "Windows D:\web 프로젝트를 이 Mac으로 이관해줘. docs/migration-to-mac.md 참고해."

### Phase 1: 기본 도구 설치

```bash
# Homebrew
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Node.js 22 + Python 3.12
brew install node@22 python@3.12 git mkcert

# Ollama (Apple Silicon Metal 가속)
brew install ollama
```

### Phase 2: 외장 SSD 디렉토리 생성

```bash
mkdir -p /Volumes/ExtSSD/docker/timescaledb
mkdir -p /Volumes/ExtSSD/docker/node-red
mkdir -p /Volumes/ExtSSD/web/files
mkdir -p /Volumes/ExtSSD/backup
```

### Phase 3: 소스코드 가져오기

**방법 A — Git (추천)**
```bash
cd ~
git clone <repo-url> web
git clone <repo-url-slm> slm
```

**방법 B — Windows에서 직접 전송**
```bash
# Windows 터미널에서 실행
tar -czf /tmp/slm-project.tar.gz \
  -C /d/web . \
  --exclude=node_modules --exclude=.next --exclude=__pycache__

tar -czf /tmp/slm-ai.tar.gz \
  -C /d/slm . \
  --exclude=venv --exclude=__pycache__ --exclude=*.npy

scp /tmp/slm-project.tar.gz user@mac-ip:~/
scp /tmp/slm-ai.tar.gz user@mac-ip:~/

# Mac에서 압축 해제
mkdir -p ~/web ~/slm
tar -xzf ~/slm-project.tar.gz -C ~/web
tar -xzf ~/slm-ai.tar.gz -C ~/slm
```

### Phase 4: Docker Compose 설정

`~/web/docker-compose.dev.yml` 볼륨 경로 수정:

```yaml
services:
  slm-timescaledb:
    image: timescale/timescaledb:latest-pg16
    ports:
      - "5433:5432"
    environment:
      POSTGRES_DB: slm
      POSTGRES_USER: slm_dev
      POSTGRES_PASSWORD: slm_dev_1234
    volumes:
      - /Volumes/ExtSSD/docker/timescaledb:/var/lib/postgresql/data
      - ./db/init:/docker-entrypoint-initdb.d

  node-red:
    image: nodered/node-red
    ports:
      - "1880:1880"
    volumes:
      - /Volumes/ExtSSD/docker/node-red:/data
```

```bash
cd ~/web
docker compose -f docker-compose.dev.yml up -d
```

### Phase 5: DB 복원

**Windows에서 덤프 생성:**
```bash
docker exec slm-timescaledb pg_dump -U slm_dev slm > /tmp/slm_dump.sql
scp /tmp/slm_dump.sql user@mac-ip:~/
```

**Mac에서 복원:**
```bash
# 컨테이너 기동 대기
sleep 10

# 복원
docker exec -i slm-timescaledb psql -U slm_dev slm < ~/slm_dump.sql
```

### Phase 6: 프론트엔드 설치

```bash
cd ~/web/slm-dashboard/slm-dashboard
npm install

# HTTPS 인증서
mkcert -install
mkcert -key-file ~/web/certs/localhost-key.pem \
       -cert-file ~/web/certs/localhost.pem \
       localhost 127.0.0.1 ::1
```

### Phase 7: Python 환경

```bash
cd ~/slm
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Ollama 모델
ollama pull phi4-mini
ollama pull snowflake-arctic-embed2
```

### Phase 8: 경로 수정

| 파일 | 변경 내용 |
|------|----------|
| `CLAUDE.md` | `D:\web` → `~/web`, `D:\slm` → `~/slm` |
| `ai_server.py` | `FILE_STORAGE_PATH` → `/Volumes/ExtSSD/web/files` |
| `ai_server.py` | DB 포트 5433 유지 확인 |
| `.env.local` | `HTTPS_ENABLED=true` 유지 |
| `package.json` | `dev:https` 스크립트 인증서 경로 수정 |
| `CLAUDE.md` | OS: macOS, 터미널: zsh |
| `memory/MEMORY.md` | 경로 참조 전체 수정 |

### Phase 9: 서비스 시작 + 검증

```bash
# 1. Docker 확인
docker ps  # timescaledb + node-red 실행 중

# 2. AI Server
cd ~/slm && source venv/bin/activate
python ai_server.py &

# 3. Next.js
cd ~/web/slm-dashboard/slm-dashboard
npm run dev:https &

# 4. 검증
curl -sk https://localhost:3000/ -o /dev/null -w "%{http_code}"   # 200
curl -s http://localhost:8000/health                                # OK
curl -s http://localhost:8000/ask -X POST \
  -H "Content-Type: application/json" \
  -d '{"user_question":"남산 배수지 수위 현황","region":"R01"}'     # 응답 확인
```

### Phase 10: 외장 SSD 자동 마운트 + 잠자기 방지

```bash
# 외장 SSD 자동 마운트 (fstab)
sudo vifs
# UUID=<disk-uuid> /Volumes/ExtSSD apfs rw 0 0

# 잠자기 방지 (서버 용도)
sudo pmset -a disksleep 0
sudo pmset -a sleep 0
```

## 이관 후 체크리스트

- [ ] Docker TimescaleDB 기동 + DB 데이터 확인
- [ ] Node-RED 플로우 정상 (http://localhost:1880)
- [ ] AI Server 시작 + 캐시 빌드 완료 (로그 확인)
- [ ] Next.js HTTPS 접속 (https://localhost:3000)
- [ ] 로그인 → 대시보드 → 채팅 → 모니터링 순회 테스트
- [ ] GIS 관망도 레이어 표시 확인
- [ ] 용수 흐름 실시간 모니터링 확인
- [ ] Ollama 추론 속도 확인 (Metal 가속)
- [ ] 외장 SSD 파일 업로드/다운로드 테스트

## Windows 경로 → Mac 경로 매핑

| Windows | Mac |
|---------|-----|
| `D:\web` | `~/web` |
| `D:\slm` | `~/slm` |
| `D:\web\files\` | `/Volumes/ExtSSD/web/files/` |
| `D:\web\certs\` | `~/web/certs/` |
| `localhost:5433` | `localhost:5433` (동일) |
| `localhost:8000` | `localhost:8000` (동일) |
| `localhost:3000` | `localhost:3000` (동일) |
| `pathlib.Path` 사용 | 그대로 호환 (OS 무관) |

## 주의사항

- **TimescaleDB ARM 이미지**: `timescale/timescaledb:latest-pg16` (ARM 네이티브)
- **node_modules 복사 금지**: Windows 빌드 바이너리 호환 안 됨 → `npm install` 필수
- **venv 복사 금지**: 동일 이유 → `pip install -r requirements.txt` 필수
- **Docker Desktop 리소스**: 설정 → Resources → Memory 8GB+ 할당 권장
- **Spotlight 인덱싱 제외**: 외장 SSD를 Spotlight 제외 목록에 추가 (I/O 부하 감소)
