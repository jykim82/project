# HTTPS 자기서명 인증서 (개발용)

Next.js dev server (`--experimental-https`) 가 사용하는 cert 파일.
**`*.pem`은 git 관리 외** (호스트마다 SAN 다르고 private key 노출 위험).

## 파일

| 파일 | 용도 |
|------|------|
| `localhost.pem` | 서버 인증서 |
| `localhost-key.pem` | 서버 비밀키 (절대 노출 금지) |

## 신규 머신 셋업

### 1. mkcert 설치 + CA 신뢰
```bash
brew install mkcert       # macOS
mkcert -install           # 시스템 CA 저장소에 root CA 등록 (Chrome/Edge/Safari 자동 신뢰)
```

### 2. cert 발급 — **호스트의 모든 LAN IP 를 SAN 에 포함**
```bash
cd /Users/jykim/web/certs

# 호스트 LAN IP 확인
ifconfig | grep 'inet ' | grep -v 127.0 | awk '{print $2}'

# SAN 에 localhost + 127.0.0.1 + ::1 + LAN IP 들 명시
mkcert -key-file localhost-key.pem -cert-file localhost.pem \
  localhost 127.0.0.1 ::1 \
  192.168.x.x 10.x.x.x   # 호스트 IP 로 교체
```

### 3. frontend 컨테이너 재시작
```bash
docker restart slm-frontend
```

## 트러블슈팅 — [E-030] LAN IP 접속 시 화면 자동 reload

### 증상
사용자가 LAN IP (예: `https://192.168.219.105:3000`) 로 접속 시 약 70~90초
주기로 페이지 전체 reload + 스크롤 위치 초기화.

### 원인 (2 중 복합)
1. **cert SAN 에 LAN IP 누락** — `localhost` 만 포함된 cert 로 LAN IP 접속 시
   브라우저는 cert mismatch 경고 후 진행 가능하지만 **WebSocket (wss://) 은
   매 reconnect 마다 검증 실패 → HMR ws 끊김 → Turbopack stale chunk
   감지 시 페이지 전체 reload**.
2. **`next.config.ts` `allowedDevOrigins` 에 LAN IP 누락** — Next.js 16 의
   strict origin 정책. `NEXTAUTH_URL` host 와 다른 origin 에서 오는 `/_next/*`
   리소스 요청 (HMR ws 포함) 차단 → reconnect 반복.

### 해결
- 본 폴더 §"신규 머신 셋업" 으로 cert 재발급 (LAN IP 포함).
- `slm-dashboard/slm-dashboard/next.config.ts` `allowedDevOrigins` 에 사설
  IP 대역 와일드카드 등록 (이미 적용됨, commit 037700f):
  ```ts
  allowedDevOrigins: [
    "*.trycloudflare.com",
    "127.0.0.1",
    "192.168.*.*", "10.*.*.*",
    "172.16.*.*" /* ... 172.31.*.*까지 */,
  ],
  ```
- NextAuth 멀티 호스트 — `src/app/api/auth/[...nextauth]/route.ts` 가 request
  host 헤더로 NEXTAUTH_URL 런타임 오버라이드 (이미 적용됨, commit 705fe23).

### 참조
- `docs/error-management.md` E-030
- `docs/slm-dev-environment-guide.md` §HTTPS / 멀티 호스트
