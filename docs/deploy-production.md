# 프로덕션(납품) 배포 가이드

> 개발 서버(`next dev`)가 아닌 **프로덕션 빌드(`next build` + `next start`)** 로
> 구동한다. 이것이 화면 자동 리로드(E-034)를 **IP 변경과 무관하게 근본 제거**하는
> 방법이다. 개발 서버의 HMR(Fast Refresh) 웹소켓이 없어 인증서 SAN·접속 IP
> 불일치로 인한 리로드가 발생할 수 없다.

## 왜 프로덕션 빌드인가 (근본 원인)

`next dev --turbopack --experimental-https` 는 코드 변경을 실시간 반영하기 위해
HMR 웹소켓(wss)을 유지한다. 접속 IP 가 자기서명 인증서 SAN 에 없으면 이 wss
검증이 반복 실패 → Turbopack 이 stale chunk 로 판단 → **페이지 전체 리로드**
(약 70~90초 주기, docs/error-management.md **E-034**).

- 대증요법: cert SAN 에 접속 IP 추가 → IP 바뀔 때마다 재발급 필요 (납품 부적합).
- **근본 해결: 프로덕션 빌드.** `next start` 에는 HMR 웹소켓이 없다 → 리로드
  트리거 자체가 존재하지 않는다. 인증서/IP 와 무관하게 영구 해결.

## 구성 요소

| 파일 | 역할 |
|------|------|
| `slm-dashboard/slm-dashboard/Dockerfile.prod` | 프런트 멀티스테이지 빌드(`next build`) → `next start` |
| `docker-compose.prod.yml` | 프로덕션 스택 (db/backend/node-red/frontend/caddy) |
| `certs/Caddyfile` | Caddy 리버스 프록시 — HTTPS:3000 종단 → frontend:3000 |

프런트는 HTTP:3000 내부 서비스만 하고, 외부 노출은 **Caddy(TLS)** 만 담당한다.
`next start` 는 자체 HTTPS 를 하지 않으므로 TLS 는 리버스 프록시가 종단한다.
NextAuth 는 요청 `host` 헤더 + `x-forwarded-proto`(Caddy 자동 주입)로 origin 을
런타임 결정하므로(E-034 fix#3) 어떤 IP/도메인으로 들어와도 세션이 동작한다.

## 배포 절차

### 1. 필수 환경변수 (`.env`)

```bash
DB_PASSWORD=<강한 랜덤값>
NEXTAUTH_SECRET=<강한 랜덤값 — openssl rand -base64 32>
# (선택) 기존 데이터/파일 경로를 재사용하려면:
# TIMESCALE_DATA=/절대/경로/timescaledb
# FILES_DATA=./files
```

`DB_PASSWORD`, `NEXTAUTH_SECRET` 는 미지정 시 compose 가 에러로 중단한다(의도).

### 2. 인증서 (TLS)

폐쇄망 기준 택1:
- **고객 도메인 + 사설 CA**: 고객 CA 로 발급한 `localhost.pem`/`localhost-key.pem`
  을 `certs/` 에 배치. 클라이언트에 사설 CA 설치 시 경고 없음.
- **mkcert(사설 CA)**: `certs/README.md` 참고. 접속에 쓸 호스트명/IP 를 SAN 에 포함.
- 자기서명 그대로: 브라우저 1회 경고 클릭 후 사용(리로드는 발생 안 함 —
  프로덕션엔 HMR 이 없으므로 인증서 경고와 리로드는 무관).

> 핵심: 프로덕션에서는 인증서 SAN 이 접속 IP 와 달라도 **리로드가 발생하지 않는다.**
> 인증서는 오직 브라우저 신뢰(경고 유무)에만 영향.

### 3. 빌드 & 기동

```bash
# 개발 스택이 떠 있으면 먼저 내린다 (컨테이너명·포트 충돌 방지)
docker compose -f docker-compose.dev.yml down

docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d
```

접속: `https://<호스트-IP 또는 도메인>:3000`

### 4. 검증

- `docker compose -f docker-compose.prod.yml logs -f frontend` → `✓ Ready` (Turbopack/HMR 로그 없음)
- 페이지에서 확대/스크롤 후 수 분 대기 → **리로드 없음** 확인.

## 무중단 프런트만 프로덕션 교체 (개발 스택 위에서 — 현재 적용 방식)

리로드(E-034)는 **프런트 전용** 이슈다. 개발 스택(db/backend/node-red)과 그
캐시(이상감지 스캔 ~200초 등)를 유지한 채 **프런트 레이어만** 프로덕션으로
바꾸면 backend 재기동 없이 리로드를 제거할 수 있다.

```bash
scripts/switch-frontend-prod.sh   # dev 프런트 중지 → prod 프런트+Caddy (재빌드 포함)
scripts/switch-frontend-dev.sh    # 개발(HMR) 복귀
```

동작:
1. `next build` 로 `slm-frontend-prod:latest` 이미지 빌드.
2. dev 프런트(`slm-frontend`) 중지 → `:3000` 과 `frontend` 네트워크 alias 해제.
3. 프로덕션 프런트를 `web_default` 네트워크에 `--network-alias frontend` 로 기동
   (내부 전용). 기존 `backend:8000` 을 그대로 프록시.
4. `slm-caddy`(caddy:2)가 `:3000` HTTPS 종단 → `frontend:3000`.

- **코드 변경 반영(재빌드)**: `scripts/switch-frontend-prod.sh` 재실행.
- **자동 재빌드 루틴**: `scripts/watch-frontend-prod.sh` 를 백그라운드로 띄우면
  `src/` 변경을 감지(mtime 폴링 + 20s 디바운스)해 자동으로 재빌드·재배포한다.
  `nohup scripts/watch-frontend-prod.sh >/tmp/slm-fe-watch.log 2>&1 &`
  (중지: `pkill -f watch-frontend-prod.sh`). fswatch 불필요.
- backend/db 는 건드리지 않으므로 이상감지 캐시 등 유지.
- DDNS(`dnhigh98.duckdns.org` — 2026-07-23 LG U+ 라우터 전환으로 asuscomm 폐기,
  Mac launchd `local.slm.duckdns` 5분 갱신)·LAN IP·새 IP 어디로 접속해도 HMR 이
  없어 리로드 없음.
- **LG U+ 공유기는 NAT 루프백 미지원** — LAN 안에서는 공개 도메인 접속이
  타임아웃된다(정상). 내부는 `https://<LAN IP>:3000`, 외부는 도메인(443).
  내부에서 도메인을 쓰려면 해당 PC hosts 에 `<LAN IP> dnhigh98.duckdns.org` 추가.
- 외부망 통화(TURN)는 `.env` `TURN_HOST`(도메인)+`TURN_EXTERNAL_IP`(공인 IP,
  **정적 — IP 변경 시 수동 갱신+coturn 재기동**) 소비. 검증:
  `GET /call/turn-credentials`.
- **주의**: 접속 도메인이 cert SAN 에 없으면 브라우저 1회 인증서 경고(리로드와
  무관, 클릭 통과). 경고 제거는 §인증서 참조.

전체 스택을 프로덕션으로 올리려면(납품 표준) 아래 `docker-compose.prod.yml` 사용.

## 개발 ↔ 프로덕션 전환

| | 개발 (`docker-compose.dev.yml`) | 프로덕션 (`docker-compose.prod.yml`) |
|---|---|---|
| 프런트 | `next dev` (HMR, 소스 바인드) | `next build`+`next start` (이미지 baked) |
| 코드 변경 | 실시간 반영 | 재빌드 필요 |
| 자동 리로드 | IP/cert 불일치 시 발생 (E-034) | **발생 안 함** |
| TLS | next `--experimental-https` | Caddy 리버스 프록시 |
| 첫 로딩 | JIT 컴파일 지연(프리워밍 사이드카) | 사전 빌드 → 즉시 |

개발 중에는 dev, 납품/운영은 prod 를 사용한다.

## 주의

- 코드 수정 후 프로덕션에 반영하려면 `build` 재실행 필요(HMR 없음 — 의도된 동작).
- `docker-compose.prod.yml` 은 dev-tag-ingest(원격 복제 데몬)와 프리워밍
  사이드카를 포함하지 않는다(개발 전용).
- backend 는 현재 dev 와 동일 이미지를 사용한다(리로드 이슈는 프런트 전용).
  완전 baked backend 이미지가 필요하면 별도 하드닝(추후).
