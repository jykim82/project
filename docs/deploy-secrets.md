# 프로덕션 시크릿 관리 가이드

SLM 대시보드 프로덕션 배포 시 민감 정보(DB 비밀번호, NextAuth 시크릿 등)를 안전하게 주입하는 방법.

## 기본 원칙

1. **하드코딩 금지** — 모든 시크릿은 환경 변수 또는 시크릿 매니저로 주입
2. **기본값은 dev 편의용** — `docker-compose.dev.yml`의 `${VAR:-default}` 패턴에서 `default`는 프로덕션에서 덮어써야 함
3. **`.env`는 커밋 금지** — `.gitignore`에 `.env*` 포함 확인
4. **시크릿 교체 주기** — `NEXTAUTH_SECRET`은 분기별 1회, DB 비밀번호는 연 1회 이상

## 시크릿 생성

```bash
# NextAuth 시크릿 (32바이트 base64)
openssl rand -base64 32

# DB 비밀번호 (24바이트 URL-safe)
openssl rand -base64 24 | tr -d '/+' | head -c 24
```

## 배포 방식별 주입 방법

### 1. Docker Compose + `.env` 파일 (소규모 온프레미스)

```bash
# 프로덕션 서버에서:
cp .env.example .env
vi .env   # 실제 값으로 교체
docker compose -f docker-compose.dev.yml up -d
```

`docker compose`는 기본으로 `.env`를 읽어 `${VAR}` 치환에 사용한다.
`.env` 파일 권한은 `chmod 600`로 제한.

### 2. Docker Secrets (Swarm 모드)

```yaml
services:
  frontend:
    secrets:
      - nextauth_secret
    environment:
      NEXTAUTH_SECRET_FILE: /run/secrets/nextauth_secret

secrets:
  nextauth_secret:
    external: true   # docker secret create nextauth_secret <file>
```

Next.js 코드에서 `NEXTAUTH_SECRET_FILE`을 읽어 파일 내용을 `NEXTAUTH_SECRET`으로 설정하는 래퍼 필요.

### 3. Kubernetes Secret

```bash
kubectl create secret generic slm-secrets \
  --from-literal=NEXTAUTH_SECRET="$(openssl rand -base64 32)" \
  --from-literal=DB_PASSWORD="$(openssl rand -base64 24)"
```

Pod spec에서 `envFrom: [secretRef: {name: slm-secrets}]`로 주입.

### 4. 외부 시크릿 매니저 (AWS Secrets Manager / Vault)

컨테이너 시작 시 init container가 시크릿을 fetch해 `/run/secrets/`에 떨어뜨리고, 메인 컨테이너가 `_FILE` 패턴으로 읽는다.

## 프로덕션 배포 체크리스트

- [ ] `openssl rand -base64 32`로 `NEXTAUTH_SECRET` 생성 후 주입
- [ ] DB 비밀번호를 dev 기본값(`slm_dev_1234`)에서 강한 값으로 교체
- [ ] `NEXTAUTH_URL`을 실제 도메인으로 교체 (HTTPS 필수)
- [ ] `NEXT_PUBLIC_API_URL`과 `INTERNAL_API_URL`을 실제 endpoint로 교체
- [ ] `.env` 파일 권한 `chmod 600` 또는 시크릿 매니저 사용
- [ ] 백엔드 DB 유저 `slm_dev` → 프로덕션 전용 유저로 교체 (최소 권한 원칙)
- [ ] `docker-compose.dev.yml` 대신 `docker-compose.prod.yml` 분리 (선택)
- [ ] TimescaleDB 데이터 볼륨 백업 정책 확인

## 시크릿 노출 사고 대응

시크릿이 git 이력에 실수로 커밋된 경우:

1. 즉시 해당 시크릿 재발급/회전 (이력 삭제해도 이미 노출된 것으로 간주)
2. `git filter-repo` 또는 BFG로 이력 정리
3. `git push --force` (협업 브랜치는 사전 공지)
4. `docs/error-management.md`에 사고 기록

## 관련 파일

- `docker-compose.dev.yml` — `${VAR:-default}` 보간 패턴 사용
- `.env.example` (루트) — compose 변수 템플릿
- `slm-dashboard/slm-dashboard/.env.example` — Next.js 전용
- `../slm/.env.example` — Python 서버 전용
