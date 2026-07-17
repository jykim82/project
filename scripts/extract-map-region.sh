#!/usr/bin/env bash
# ============================================================
# GIS 오프라인 타일 관할 추출 — 고객사(관할) 변경 시 실행
# ============================================================
# 사용:  scripts/extract-map-region.sh <bbox> [빌드날짜 YYYYMMDD]
#   bbox: "minLon,minLat,maxLon,maxLat" (WGS84, 여유 포함 권장)
#
# 예)  당진: scripts/extract-map-region.sh "126.35,36.65,127.05,37.15"
#      광주: scripts/extract-map-region.sh "126.60,35.02,127.05,35.30"
#
# 결과: slm-dashboard/slm-dashboard/public/map/region.pmtiles 교체
#       (파일명 고정 — 프런트 코드 변경 불필요)
# 함께 바꿀 것: NEXT_PUBLIC_GIS_CENTER / NEXT_PUBLIC_GIS_ZOOM (gis-config 참조)
#              + 시설 좌표 gis-facility-coords.json (구축 단계 데이터)
# 문서: docs/operations/offline-map-bundle.md
set -euo pipefail
cd "$(dirname "$0")/.."

BBOX="${1:?사용법: extract-map-region.sh <minLon,minLat,maxLon,maxLat> [YYYYMMDD]}"
BUILD_DATE="${2:-$(date -v-2d +%Y%m%d 2>/dev/null || date -d '2 days ago' +%Y%m%d)}"
OUT="slm-dashboard/slm-dashboard/public/map/region.pmtiles"

command -v pmtiles >/dev/null || { echo "❌ pmtiles CLI 필요: brew install pmtiles"; exit 1; }

echo "▶ 관할 추출: bbox=$BBOX (protomaps build $BUILD_DATE)"
pmtiles extract "https://build.protomaps.com/${BUILD_DATE}.pmtiles" "$OUT" --bbox="$BBOX"
ls -lh "$OUT"
echo "✅ 완료 — 프런트 재빌드 후 /monitoring/gis 에서 확인"
echo "   중심좌표/줌은 NEXT_PUBLIC_GIS_CENTER / NEXT_PUBLIC_GIS_ZOOM 으로 설정"
