#!/usr/bin/env bash
# ============================================================
# 개발 프런트(next dev, HMR)로 복귀 — switch-frontend-prod.sh 되돌리기
# ============================================================
# 프로덕션 프런트(slm-frontend-prod) + Caddy 를 내리고 개발 프런트를 다시 띄운다.
# 코드 실시간 반영(HMR)이 필요할 때 사용. (개발 서버는 IP/인증서 불일치 시
# 리로드 발생 가능 — docs/error-management.md E-034 / deploy-production.md)
set -euo pipefail
cd "$(dirname "$0")/.."

echo "▶ 프로덕션 프런트 + Caddy 중지..."
docker rm -f slm-caddy slm-frontend-prod >/dev/null 2>&1 || true

echo "▶ 개발 프런트(next dev) 기동..."
docker compose -f docker-compose.dev.yml up -d frontend

echo "✅ 개발 프런트(HMR) 복귀. https://localhost:3000 (또는 cert SAN 에 포함된 IP)."
