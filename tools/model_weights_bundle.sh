#!/usr/bin/env bash
# ============================================================
# 모델 웨이트 오프라인 번들 — 패키징/설치/검증
# ============================================================
# 폐쇄망 납품 대상 사전학습 웨이트 (git 제외, 재학습 불가):
#   - data/models/chronos-bolt-base              (~783MB, 트렌드 향후 전망)
#   - data/models/faster-whisper-large-v3-turbo  (~1.5GB, 음성 입력 STT)
# 재학습형 모델(iforest_*.pkl, baseline_gbt_*)은 현장 데이터로 cron 재학습
# 되므로 번들 대상이 아님 (docs/operations/*-train-cron.md).
#
# 사용:
#   tools/model_weights_bundle.sh pack [출력디렉토리]        # 개발장비에서
#   tools/model_weights_bundle.sh install <번들.tar.gz> [슬름루트]  # 납품장비에서
#   tools/model_weights_bundle.sh verify [슬름루트]          # 무결성 검증
#
# 문서: web/docs/operations/model-weights-bundle.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SLM_ROOT_DEFAULT="$(dirname "$SCRIPT_DIR")"

MODELS=(
  "data/models/chronos-bolt-base"
  "data/models/faster-whisper-large-v3-turbo"
)
MANIFEST="data/models/WEIGHTS_MANIFEST.sha256"

# macOS(shasum) / Linux(sha256sum) 겸용
sha() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$@"
  else shasum -a 256 "$@"; fi
}

pack() {
  local out_dir="${1:-.}"
  out_dir="$(cd "$out_dir" && pwd)"   # cd 전에 절대경로화
  local root="$SLM_ROOT_DEFAULT"
  cd "$root"

  for m in "${MODELS[@]}"; do
    [ -d "$m" ] || { echo "❌ 웨이트 없음: $root/$m — 개발장비가 맞는지 확인"; exit 1; }
  done

  echo "▶ 매니페스트 생성 (sha256)..."
  : > "$MANIFEST"
  for m in "${MODELS[@]}"; do
    find "$m" -type f ! -name '.*' | sort | while read -r f; do
      sha "$f" >> "$MANIFEST"
    done
  done
  echo "  $(wc -l < "$MANIFEST" | tr -d ' ')개 파일 등록"

  local stamp; stamp="$(date +%Y%m%d)"
  local bundle="$out_dir/slm-model-weights-${stamp}.tar.gz"
  echo "▶ 번들 생성: $bundle (수 분 소요)..."
  tar czf "$bundle" "$MANIFEST" "${MODELS[@]}"
  sha "$bundle" > "${bundle}.sha256"
  echo "✅ 완료:"
  ls -lh "$bundle" "${bundle}.sha256"
  echo "   납품 매체에 두 파일을 함께 복사하세요."
}

install() {
  local bundle="${1:?사용법: install <번들.tar.gz> [슬름루트]}"
  local root="${2:-$SLM_ROOT_DEFAULT}"
  [ -f "$bundle" ] || { echo "❌ 번들 파일 없음: $bundle"; exit 1; }

  if [ -f "${bundle}.sha256" ]; then
    echo "▶ 번들 파일 무결성 확인..."
    # sha256 파일은 생성 시 경로 기준 — 파일명만 비교
    local expect actual
    expect="$(awk '{print $1}' "${bundle}.sha256")"
    actual="$(sha "$bundle" | awk '{print $1}')"
    [ "$expect" = "$actual" ] || { echo "❌ 번들 체크섬 불일치 — 전송 중 손상"; exit 1; }
    echo "  체크섬 일치"
  else
    echo "⚠ ${bundle}.sha256 없음 — 파일 무결성 확인 생략"
  fi

  echo "▶ 압축 해제 → $root ..."
  tar xzf "$bundle" -C "$root"
  verify "$root"
}

verify() {
  local root="${1:-$SLM_ROOT_DEFAULT}"
  cd "$root"
  [ -f "$MANIFEST" ] || { echo "❌ 매니페스트 없음: $root/$MANIFEST — install 미수행?"; exit 1; }

  echo "▶ 웨이트 무결성 검증 ($(wc -l < "$MANIFEST" | tr -d ' ')개 파일)..."
  local fail=0
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -c "$MANIFEST" --quiet || fail=1
  else
    shasum -a 256 -c "$MANIFEST" >/dev/null || fail=1
  fi
  if [ "$fail" -ne 0 ]; then
    echo "❌ 체크섬 불일치 파일 있음 — 번들 재설치 필요"
    exit 1
  fi
  # 핵심 파일 존재 확인 (매니페스트 자체 누락 방어)
  for key in \
    "data/models/chronos-bolt-base/model.safetensors" \
    "data/models/faster-whisper-large-v3-turbo/model.bin"; do
    [ -f "$key" ] || { echo "❌ 핵심 파일 없음: $key"; exit 1; }
  done
  echo "✅ 웨이트 검증 통과"
  echo "   런타임 확인: 백엔드 기동 후"
  echo "   - STT:     curl -F 'audio=@test.webm' http://localhost:8000/stt/transcribe"
  echo "   - Chronos: 트렌드 '향후 전망' 응답의 forecast.method == 'chronos_bolt'"
}

case "${1:-}" in
  pack)    shift; pack "$@" ;;
  install) shift; install "$@" ;;
  verify)  shift; verify "$@" ;;
  *) grep '^#' "$0" | sed -n '2,15p'; exit 1 ;;
esac
