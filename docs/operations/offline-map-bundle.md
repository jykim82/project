# GIS 오프라인 지도 번들 (납품 필수)

폐쇄망에서 GIS 관망도 베이스맵을 자급하는 절차. cartocdn 외부 의존
(E-038 조사에서 발견된 납품 차단 요소)을 제거한 구성 (2026-07-17).

## 구성
| 요소 | 경로 (프런트 submodule) | 크기 | 출처 |
|---|---|---|---|
| 관할 벡터 타일 | `public/map/region.pmtiles` | ~19MB | protomaps 빌드에서 bbox 추출 |
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
# 관할 타일 추출 — bbox 파라미터 스크립트 (파일명 region.pmtiles 고정)
scripts/extract-map-region.sh "126.35,36.65,127.05,37.15"   # 당진
# 글리프/스프라이트 (관할 무관 — 1회만)
git clone --depth 1 https://github.com/protomaps/basemaps-assets.git /tmp/bma
cp -R /tmp/bma/fonts /tmp/bma/sprites slm-dashboard/slm-dashboard/public/map/assets/
```

## 고객사(관할) 변경 절차 — UI (구축 > 지도 설정, 2026-07-17 제품화)

**`/setup/map-config`** (M200-16, Migration 0101) 에서 재빌드·코드 수정 없이 처리:
| 항목 | UI 동작 |
|---|---|
| 지도 중심/줌 | 폼 입력 → site-settings DB (`GIS_MAP_CENTER`/`GIS_MAP_ZOOM`) — GIS 진입 시 적용 |
| 베이스맵 타일 | `region.pmtiles` 업로드 → `files/map/` 저장, 즉시 반영 |
| 관망 레이어 (관로·밸브·경계) | geojson/pmtiles zip 업로드 → `files/gis/` 저장, 즉시 반영 |
| 내장본 복원 | 각 섹션 "내장본 복원" 버튼 (업로드본 삭제) |

**서빙 아키텍처**: 업로드본(files/) 우선 → 빌드 내장본(public/) 폴백.
- `/api/gis/basemap` (Range 지원, region.pmtiles) / `/api/gis/layer/[id]` /
  `/api/gis/tiles/*` — 모두 `resolveGisAsset()` 경유
- 백엔드: `slm/endpoints/map_assets.py` (업로드·상태·삭제, PMTiles 매직 검증)

**산출물 생성은 개발/구축 장비 스크립트** (인터넷 필요):
| 산출물 | 스크립트 |
|---|---|
| region.pmtiles | `scripts/extract-map-region.sh "<bbox>"` — 예) 광주 `"126.60,35.02,127.05,35.30"` |
| 레이어 zip | `scripts/import-shp-layers.py <SHP디렉토리>` (§SHP 임포트) → public/gis 산출물을 zip |

env `NEXT_PUBLIC_GIS_CENTER`/`GIS_ZOOM` 은 DB 설정 없을 때 폴백으로 유지.
시설 좌표(`gis-facility-coords.json`)는 구축 단계 데이터 (setup 플로우).

## 납품 장비 — 검증
1. 인터넷 차단 상태에서 `/monitoring/gis` 접속 → 지도 타일·한글 지명 표시
2. 브라우저 Network 에서 `cartocdn.com` 요청 0건 확인
3. 지도 안 뜨면: `public/map/region.pmtiles` 존재 + 서버가 Range 요청
   (206) 지원하는지 확인

## 주의
- pmtiles 는 HTTP **Range 요청** 필수 — Next 정적 서빙·Caddy 모두 지원.
  다른 정적 서버로 바꿀 땐 Range 지원 확인
- 라이선스: 타일 데이터 © OpenStreetMap (ODbL), 스타일/에셋 protomaps (BSD/OFL)
- 모델 웨이트 번들과 함께 납품 체크리스트 항목 (delivery-checklist 참조)

## SHP 임포트 — 관로·밸브·경계 레이어 (2026-07-17 스크립트화)

관할의 상수도 GIS SHP 세트(표준 코드 SA1xx/SA2xx/SAA0xx + 블록경계)를
받아 프런트 레이어 데이터를 재생성:

```bash
/Users/jykim/slm/venv/bin/python scripts/import-shp-layers.py "docs/SHP추출"
# → public/gis/{layer}.geojson 17종 + pipes/facilities.pmtiles 재생성
```

- 입력 규격: EPSG:5186, DBF 인코딩 CP949 (EUC-KR/UTF-8 자동 폴백)
- 코드 매핑: 스크립트 `CODE_TO_LAYER` (SAA004→배수관 distribution 등).
  같은 코드 복수 파일(블록구축신설 등) 자동 병합, **"폐쇄" 파일 제외**
- 의존: pyshp·pyproj (slm venv) + tippecanoe (brew) — 개발/구축 장비 전용
- 재현 검증 (당진 원본 22종): 15개 레이어 feature 수 기존 산출과 정확 일치,
  관로 2종은 기존에 누락됐던 신설분 포함으로 +137/+2
- 좌표계가 다른 관할(예: EPSG:5185/5187 서부/동부원점)은 스크립트 상단
  Transformer 의 소스 CRS 만 변경
