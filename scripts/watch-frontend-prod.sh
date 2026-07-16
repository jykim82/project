#!/usr/bin/env bash
# ============================================================
# 프런트 소스 변경 감지 → 프로덕션 프런트 자동 재빌드·재배포
# ============================================================
# 프로덕션 프런트(next start)는 HMR 이 없어 코드 변경이 자동 반영되지 않는다.
# 본 워처가 src/ 변경을 감지해 디바운스 후 scripts/switch-frontend-prod.sh 를
# 자동 재실행 → prod 프런트를 재빌드·교체한다. (fswatch 불필요 — mtime 폴링)
#
# 사용:   scripts/watch-frontend-prod.sh          # 포그라운드
#         nohup scripts/watch-frontend-prod.sh >/tmp/slm-fe-watch.log 2>&1 &   # 백그라운드
# 중지:   Ctrl-C  또는  pkill -f watch-frontend-prod.sh
#
# 환경변수: POLL_S(기본 5), DEBOUNCE_S(기본 20)
set -uo pipefail
cd "$(dirname "$0")/.."

WATCH_DIRS="slm-dashboard/slm-dashboard/src slm-dashboard/slm-dashboard/public"
WATCH_FILES="slm-dashboard/slm-dashboard/next.config.ts slm-dashboard/slm-dashboard/package.json"
POLL_S="${POLL_S:-5}"
DEBOUNCE_S="${DEBOUNCE_S:-20}"

# 감시 대상 파일들의 최신 수정시각(epoch) — 변경 signature
sig() {
  { find $WATCH_DIRS -type f \
      \( -name '*.ts' -o -name '*.tsx' -o -name '*.css' -o -name '*.js' -o -name '*.json' \) \
      -not -path '*/node_modules/*' -not -path '*/.next/*' -exec stat -f '%m' {} + 2>/dev/null
    stat -f '%m' $WATCH_FILES 2>/dev/null; } | sort -n | tail -1
}

now() { date +%s; }

echo "▶ 프런트 소스 감시 시작 (poll=${POLL_S}s, debounce=${DEBOUNCE_S}s)"
echo "  대상: $WATCH_DIRS $WATCH_FILES"

last_seen="$(sig)"
last_built="$last_seen"      # 시작 시점 코드는 이미 빌드돼 있다고 가정
changed_at=0

while true; do
  cur="$(sig)"
  if [ "$cur" != "$last_seen" ]; then
    last_seen="$cur"
    changed_at="$(now)"
    echo "  … 변경 감지 ($(date '+%H:%M:%S')) — ${DEBOUNCE_S}s 안정 대기"
  fi
  # 변경이 있고(빌드된 것과 다름) 디바운스 경과 시 재빌드
  if [ "$last_seen" != "$last_built" ] && [ "$changed_at" -ne 0 ]; then
    if [ $(( $(now) - changed_at )) -ge "$DEBOUNCE_S" ]; then
      echo "▶ [$(date '+%H:%M:%S')] 변경 확정 → 프로덕션 프런트 재빌드·재배포"
      build_sig="$(sig)"      # 빌드 '시작 시점' signature — 빌드 중 변경은 다음 루프에서 감지
      if scripts/switch-frontend-prod.sh; then
        last_built="$build_sig"
        echo "✅ [$(date '+%H:%M:%S')] 반영 완료 — 브라우저 새로고침 시 최신 반영"
      else
        echo "⚠ 재빌드 실패 — ${POLL_S}s 후 재시도"
      fi
    fi
  fi
  sleep "$POLL_S"
done
