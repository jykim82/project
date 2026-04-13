#!/bin/sh
# ============================================================
# Dev 전용 JIT 컴파일 + 백엔드 캐시 워밍 스크립트
#
# 목적: Turbopack dev 모드에서 첫 접속 시 발생하는 18초 JIT 컴파일 지연과
#       slm-backend 부팅 직후의 백그라운드 작업 경합(IForest + SCAN_ALL + SNMP)이
#       실사용자 첫 페이지 요청과 겹치지 않도록, 서버 기동 직후 한 번 선제 호출.
#
# 실행 흐름:
#   1) backend /health 응답 대기 (최대 120초)
#   2) frontend /login 응답 대기 (최대 120초)
#   3) 자주 쓰는 페이지 + 프록시 라우트 + 백엔드 직접 호출을 순차 curl
#   4) 한 번 돌고 종료 (docker-compose restart: no)
#
# ⚠ 납품 시 제거: 운영에서는 `next build && next start`를 쓰므로 JIT 컴파일 없음
# ============================================================
set -eu

FRONTEND_URL="${FRONTEND_URL:-https://frontend:3000}"
BACKEND_URL="${BACKEND_URL:-http://backend:8000}"
MAX_WAIT_S="${MAX_WAIT_S:-120}"

log() { echo "[prewarm] $*"; }

wait_ready() {
    url=$1
    name=$2
    i=0
    while [ $i -lt "$MAX_WAIT_S" ]; do
        code=$(curl -k -s -o /dev/null -w "%{http_code}" --max-time 5 "$url" 2>/dev/null || echo 000)
        # 200/302(redirect)/401(인증 필요) 모두 "살아있음"으로 간주
        case "$code" in
            200|302|401|307) log "$name ready (${i}s, http=$code)"; return 0 ;;
        esac
        i=$((i + 2))
        sleep 2
    done
    log "$name 대기 타임아웃 (${MAX_WAIT_S}s) — 계속 진행"
    return 1
}

curl_warm() {
    url=$1
    label=$2
    t0=$(date +%s)
    code=$(curl -k -s -o /dev/null -w "%{http_code}" --max-time 60 "$url" 2>/dev/null || echo ERR)
    t1=$(date +%s)
    log "  $label → http=$code (${t0}→${t1}s)"
}

log "=== JIT 워밍 시작 ==="
wait_ready "$BACKEND_URL/health"       "backend"
wait_ready "$FRONTEND_URL/login"       "frontend"

log "1) 백엔드 핵심 엔드포인트 직접 호출 (백그라운드 경합 우회)"
curl_warm "$BACKEND_URL/flow-map"           "backend /flow-map"
curl_warm "$BACKEND_URL/flow-map/roots"     "backend /flow-map/roots"
curl_warm "$BACKEND_URL/flow-map/realtime"  "backend /flow-map/realtime"
curl_warm "$BACKEND_URL/dashboard/summary"  "backend /dashboard/summary"

log "2) 프런트엔드 페이지 컴파일 트리거 (login 리다이렉트라도 page.tsx 컴파일됨)"
curl_warm "$FRONTEND_URL/dashboard"          "page /dashboard"
curl_warm "$FRONTEND_URL/monitoring/flow"    "page /monitoring/flow"
curl_warm "$FRONTEND_URL/monitoring/reservoir" "page /monitoring/reservoir"
curl_warm "$FRONTEND_URL/monitoring/booster"  "page /monitoring/booster"
curl_warm "$FRONTEND_URL/monitoring/block"    "page /monitoring/block"
curl_warm "$FRONTEND_URL/monitoring/gis"      "page /monitoring/gis"
curl_warm "$FRONTEND_URL/trend"               "page /trend"
curl_warm "$FRONTEND_URL/chat"                "page /chat"
curl_warm "$FRONTEND_URL/network"             "page /network"
curl_warm "$FRONTEND_URL/crisis/alarm-dashboard" "page /crisis/alarm-dashboard"

log "3) 프록시 라우트 핸들러 컴파일 트리거 (/api/proxy/*)"
# 401 리턴이 예상되지만 route.ts 컴파일은 401 이전에 완료되므로 목적 달성
curl_warm "$FRONTEND_URL/api/proxy/flow-map"           "proxy flow-map"
curl_warm "$FRONTEND_URL/api/proxy/flow-map/realtime"  "proxy flow-map/realtime"
curl_warm "$FRONTEND_URL/api/proxy/dashboard/summary"  "proxy dashboard/summary"

log "=== JIT 워밍 완료 ==="
