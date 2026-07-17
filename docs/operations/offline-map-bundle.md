# GIS 오프라인 지도 번들 (납품 필수)

폐쇄망에서 GIS 관망도 베이스맵을 자급하는 절차. cartocdn 외부 의존
(E-038 조사에서 발견된 납품 차단 요소)을 제거한 구성 (2026-07-17).

## 구성
| 요소 | 경로 (프런트 submodule) | 크기 | 출처 |
|---|---|---|---|
| 관할 벡터 타일 | `public/map/dangjin.pmtiles` | ~19MB | protomaps 빌드에서 bbox 추출 |
| 글리프(폰트) | `public/map/assets/fonts/` | ~14MB | protomaps/basemaps-assets |
| 스프라이트 | `public/map/assets/sprites/` | ~260KB | 〃 |

- **git 제외** (`public/map/.gitignore`) — 용량. 개발 장비 준비 후 Docker
  빌드 컨텍스트에 포함되어 prod 이미지에 자동 반영 (`COPY . .`)
- 스타일: `src/lib/config/gis-offline-style.ts` — protomaps-themes-base
  레이어(언어 ko) + pmtiles 프로토콜 (GisMap 등록)
- 기본 **오프라인**. 개발 편의로 CDN 지도가 필요하면 `NEXT_PUBLIC_MAP_CDN=1`

## 개발 장비 — 에셋 준비 (인터넷 필요, 1회)
```bash
brew install pmtiles
# 관할 bbox 추출 (당진 + 여유)
pmtiles extract https://build.protomaps.com/$(date -v-2d +%Y%m%d).pmtiles \
  slm-dashboard/slm-dashboard/public/map/dangjin.pmtiles \
  --bbox=126.35,36.65,127.05,37.15
# 글리프/스프라이트
git clone --depth 1 https://github.com/protomaps/basemaps-assets.git /tmp/bma
cp -R /tmp/bma/fonts /tmp/bma/sprites slm-dashboard/slm-dashboard/public/map/assets/
```
다른 고객사(관할) 납품 시 bbox 만 변경해 재추출.

## 납품 장비 — 검증
1. 인터넷 차단 상태에서 `/monitoring/gis` 접속 → 지도 타일·한글 지명 표시
2. 브라우저 Network 에서 `cartocdn.com` 요청 0건 확인
3. 지도 안 뜨면: `public/map/dangjin.pmtiles` 존재 + 서버가 Range 요청
   (206) 지원하는지 확인

## 주의
- pmtiles 는 HTTP **Range 요청** 필수 — Next 정적 서빙·Caddy 모두 지원.
  다른 정적 서버로 바꿀 땐 Range 지원 확인
- 라이선스: 타일 데이터 © OpenStreetMap (ODbL), 스타일/에셋 protomaps (BSD/OFL)
- 모델 웨이트 번들과 함께 납품 체크리스트 항목 (delivery-checklist 참조)
