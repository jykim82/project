#!/usr/bin/env bash
# ============================================================
# 프런트만 프로덕션(next build+start)으로 교체 — DB/backend 무중단
# ============================================================
# 개발 스택(db/backend/node-red)은 그대로 두고 프런트 레이어만 프로덕션으로
# 바꾼다. `next start` 에는 HMR 웹소켓이 없어, DDNS/LAN IP/새 IP 로 접속해도
# 인증서·origin 불일치로 인한 ~70~90초 주기 자동 리로드(E-034)가 원천적으로
# 발생하지 않는다. backend/db 는 재기동하지 않으므로 이상감지 등 캐시가 유지된다.
#
# 사용:   scripts/switch-frontend-prod.sh      # 최초 전환 & 코드 반영 재빌드
# 되돌리기: scripts/switch-frontend-dev.sh
set -euo pipefail
cd "$(dirname "$0")/.."

NET="${DOCKER_NET:-web_default}"
# NEXTAUTH_SECRET 은 기존 dev 프런트 env 에서 승계(세션 유지). 없으면 랜덤 생성.
SECRET="$(docker inspect slm-frontend --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null | sed -n 's/^NEXTAUTH_SECRET=//p' || true)"
SECRET="${SECRET:-$(openssl rand -base64 32)}"

echo "▶ 프로덕션 프런트 이미지 빌드 (next build)..."
docker build -f slm-dashboard/slm-dashboard/Dockerfile.prod \
  -t slm-frontend-prod:latest slm-dashboard/slm-dashboard

echo "▶ 개발 프런트 중지 (:3000 · frontend alias 해제)..."
docker compose -f docker-compose.dev.yml stop frontend frontend-prewarm >/dev/null 2>&1 || true

echo "▶ 프로덕션 프런트 기동 (내부 전용, alias=frontend)..."
docker rm -f slm-frontend-prod >/dev/null 2>&1 || true
docker run -d --name slm-frontend-prod \
  --network "$NET" --network-alias frontend \
  -e TZ=Asia/Seoul -e NODE_ENV=production \
  -e INTERNAL_API_URL=http://backend:8000 \
  -e NEXT_PUBLIC_API_URL=http://localhost:8000 \
  -e NEXT_PUBLIC_DEMO_MODE=false \
  -e NEXTAUTH_URL=https://localhost:3000 \
  -e NEXTAUTH_SECRET="$SECRET" \
  -e DATABASE_URL='postgresql://slm_dev:slm_dev_1234@timescaledb:5432/slm' \
  -e FILE_STORAGE_PATH=/data/files \
  -v "$PWD/files:/data/files" \
  --restart unless-stopped \
  slm-frontend-prod:latest >/dev/null

echo "▶ Caddy TLS 리버스 프록시 기동 (HTTPS:3000 → frontend:3000)..."
docker rm -f slm-caddy >/dev/null 2>&1 || true

# 공개 도메인(Let's Encrypt) 옵션 — .env 의 PUBLIC_DOMAIN 설정 시 443/80 도 종단.
# 공유기 TCP 80/443 포워딩 필요. 미설정이면 기존 :3000(mkcert)만.
PUBLIC_DOMAIN="$(sed -n 's/^PUBLIC_DOMAIN=//p' .env 2>/dev/null | tail -1)"
ACME_EMAIL="$(sed -n 's/^ACME_EMAIL=//p' .env 2>/dev/null | tail -1)"
CADDY_PORTS=(-p 3000:3000)
CADDYFILE_SRC="$PWD/certs/Caddyfile"
if [ -n "$PUBLIC_DOMAIN" ]; then
  CADDY_PORTS+=(-p 80:80 -p 443:443)
  CADDYFILE_SRC="$PWD/certs/.Caddyfile.generated"
  { cat "$PWD/certs/Caddyfile";
    sed -e "s/__PUBLIC_DOMAIN__/$PUBLIC_DOMAIN/" \
        -e "s/__ACME_EMAIL__/${ACME_EMAIL:-internal}/" \
        "$PWD/certs/Caddyfile.public"; } > "$CADDYFILE_SRC"
  echo "   공개 도메인 활성: https://$PUBLIC_DOMAIN (LE 자동 발급 — 80/443 포워딩 필요)"
fi

docker run -d --name slm-caddy --network "$NET" "${CADDY_PORTS[@]}" \
  -v "$CADDYFILE_SRC:/etc/caddy/Caddyfile:ro" \
  -v "$PWD/certs:/certs:ro" \
  -v slm-caddy-data:/data \
  --restart unless-stopped caddy:2 >/dev/null

echo "✅ 프로덕션 프런트 가동. https://<host 또는 DDNS>:3000 — HMR 없음(리로드 없음)."
echo "   코드 변경 반영/재빌드: 이 스크립트를 다시 실행."
echo "   개발(HMR) 복귀: scripts/switch-frontend-dev.sh"
