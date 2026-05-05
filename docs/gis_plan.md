# EPANET + GIS 관망 고도화 계획

## 현재 보유한 기반

| 영역 | 현황 | 비고 |
|------|------|------|
| **GIS** | MapLibre + PMTiles 완성 | 15종 SHP 레이어, 시설 마커, 파이프 네트워크 |
| **용수 흐름** | 토폴로지 기반 실시간 모니터링 | 95개 엣지, 타임라인 재생, 교차검증 |
| **관망 데이터** | PMTiles (송수관/배수관/급수관/공업용수관/소방관) | SHP 원본도 보유 |
| **EPANET** | **미구현** — 현재 센서 실측값 기반만 운영 | |

## EPANET 연동 시 얻는 것

1. **수리 시뮬레이션** — 관망 내 유량/수압/유속을 물리 모델로 계산 (센서 없는 지점도 추정)
2. **시나리오 분석** — 밸브 개폐, 펌프 가동 변경, 관로 파손 시 영향 범위 예측
3. **수질 모델링** — 잔류염소 확산, 체류시간 분석
4. **GIS 오버레이** — 시뮬레이션 결과를 지도 위에 히트맵/색상으로 표출

## 기술 구현 방안

```
[SHP/GeoJSON 관망] → [EPANET .inp 변환] → [wntr (Python)] → [시뮬레이션 결과]
                                                                      ↓
[GIS MapLibre] ← [Next.js API] ← [FastAPI 엔드포인트] ← [결과 JSON]
```

- **Python**: `wntr` (Water Network Tool for Resilience) — EPANET 엔진을 Python에서 직접 호출
- **입력**: 기존 SHP 관망 데이터 → `.inp` 파일 변환 (노드/파이프/펌프/밸브/배수지)
- **출력**: 각 노드의 수압/유량/수질 → GIS 레이어로 시각화
- **기존 시스템 연계**: 실측값(센서) vs 시뮬레이션값 비교 → 이상감지 고도화

## 구현 단계

| Phase | 내용 | 난이도 |
|-------|------|--------|
| **1. INP 변환** | SHP 관망 → EPANET .inp 파일 생성 (wntr) | 중 |
| **2. 시뮬레이션 API** | FastAPI 엔드포인트, 정상상태/시나리오 분석 | 중 |
| **3. GIS 오버레이** | 수압/유량 히트맵, 파이프 색상 표출 | 중 |
| **4. 시나리오 UI** | 밸브 개폐/펌프 변경 시뮬레이션 인터페이스 | 상 |
| **5. 실측-모델 비교** | 센서 실측 vs EPANET 예측 편차 분석 | 상 |

## 주요 고려사항

- **SHP 데이터 품질** — 관경, 관종, 연장 등 속성이 충분해야 정확한 모델링 가능
- **경계 조건** — 배수지 수위, 펌프 특성곡선, 수요 패턴 데이터 필요
- **wntr 설치** — `pip install wntr` (EPANET 2.2 엔진 포함, Windows 지원)

## 모듈화 설계 방침 (2026-04-02 결정)

**상태: Phase 1 구현 완료 (2026-05-03) — 별도 모듈로 On/Off 토글 동작**

### 설계 원칙
- EPANET 기능은 **독립 모듈**로 분리 (기존 GIS/모니터링에 의존성 없음)
- 사이트 설정(관리 > 사이트 설정)에서 **EPANET 모듈 활성화** 토글
- 비활성 시: 모든 `/admin/epanet/*` 엔드포인트가 503 반환, 관리 페이지는 안내 카드만 노출
- wntr 라이브러리는 임포트 가능 시에만 검증 단계 활성, 미설치 시 변환은 정상 동작 + 검증만 스킵

### 모듈 구조 (실제 구현)
```
slm/epanet/
  __init__.py          # 모듈 진입점 — is_enabled() / is_wntr_available() / get_db() / init()
  shp_reader.py        # pyshp 기반 SHP 스캐너 (geopandas 미사용, 경량)
  inp_converter.py     # SHP → EPANET .inp 텍스트 변환 (wntr 의존 X — 직접 텍스트 생성)

slm/endpoints/epanet.py
  GET    /admin/epanet/status                 — 활성·환경 진단 (토글 OFF 시도 200 응답)
  POST   /admin/epanet/scan                   — SHP 메타·필드명·인코딩 (변환 전 검증)
  POST   /admin/epanet/inp/generate           — SHP→.inp 변환 + tb_epanet_artifact 저장
  GET    /admin/epanet/inp/list               — 산출물 목록
  GET    /admin/epanet/inp/{id}/download      — .inp 다운로드
  DELETE /admin/epanet/inp/{id}               — 산출물 삭제

slm-dashboard/src/app/(dashboard)/admin/
  epanet/page.tsx       # EPANET 관리 페이지 (상태/스캔/생성/다운로드/삭제)
  site-settings/page.tsx (EPANET 토글 카드 추가)
```

### 활성화 흐름 (Phase 1 실측)
1. 관리 > 사이트 설정 > "EPANET 모듈 활성화" 토글 ON
2. tb_comm_code (R01, SITE_SETTING, EPANET_ENABLED).use_yn = 'Y' 즉시 반영 (서버 재시작 불필요)
3. /admin/epanet 페이지에서 SHP 스캔·INP 생성·다운로드 가능
4. GIS 시각화 오버레이는 Phase 2 (시뮬레이션 결과 히트맵) 에서 추가

### Phase 1 변환 결과 (2026-05-03 검증)
- 입력: SAA003(송수관) 132건 + SA114(배수지) 15건
- 출력: 노드 131 / 링크 132 / 배수지 15 / 26KB .inp
- skipped 0, warnings 없음, EPANET 2.2 표준 텍스트 포맷
- wntr 검증은 미설치 (Docker 빌드 시 build-essential 추가 필요 — 다음 이미지 빌드 시 자동 설치)

### Phase 1 한계 (Phase 2 후속)
- 직선 근사: PolyLine 의 첫 점 ↔ 마지막 점만 한 파이프로 단순화 (다중 vertex 미반영)
- 표고/수요 기본값 0 (EPANET 시뮬레이션 결과 정확도 제약)
- 펌프·밸브 SHP 미반영 (Phase 2 에서 SA100 제수밸브 등 추가)

### Phase 2 (2026-05-04 — wntr 시뮬레이션 동작)
- ✅ wntr 설치 — Dockerfile 에 build-essential 추가 + backend 이미지 재빌드 (wntr 1.x + pyshp 3.x)
- ✅ Migration 0065 — `tb_epanet_simulation_result` (sim_id/artifact_id/sim_type/status + 수치 요약 6 + result_data JSONB)
- ✅ 신규 모듈 `slm/epanet/simulator.py` — `run_steady_state(inp_path)` 정상상태 시뮬레이션
- ✅ 엔드포인트 추가:
  · POST   /admin/epanet/inp/{id}/simulate       — 정상상태 시뮬레이션 실행
  · GET    /admin/epanet/inp/{id}/simulations    — artifact 별 시뮬레이션 이력
  · GET    /admin/epanet/sim/{sim_id}            — 시뮬 상세 (result_data 포함)
- ✅ 프런트 — 산출물 표 행에 [시뮬] 버튼 추가 + 결과 미리보기 카드 (압력 범위·유량 범위·실행 시간)
- 노드 ID 좌표 정밀도 4자리 → 0자리 (1m 단위 병합) — connected components 17→15
- ARM64 환경 폴백: EpanetSimulator 미가용 시 `WNTRSimulator` 사용. PDD 모드 + 가장 큰 connected component 자동 추출
- 검증 (artifact #3, 송수관 132건 + 1m 병합):
  · 시뮬 #3 — 노드 128 / 링크 131 / 압력 50.0~50.0m / 유량 ±0.0205 LPS / 108ms
  · 표고 0 + 배수지 head 50m + 수요 0 LPS 환경에서의 정적 분포 (Phase 3 에서 표고·수요 입력 후 의미 있는 분포)

### Phase 2.6 (2026-05-05 ~ 06 — GIS 페이지 시뮬 오버레이 + 토글 + 흐름 화살표)
- ✅ pyproj 추가 — `EPANET_SHP_CRS` 환경변수(default `EPSG:5186` Korea 2000 / Central Belt 2010 — 당진시 SHP 검증)
- ✅ 시뮬 응답에 lng/lat 포함 — `junction.lng/lat`, `pipe.vertices_lnglat`, `reservoir.lng/lat`, `bbox_lnglat`
- ✅ 백엔드 `GET /admin/epanet/sim/latest?region=R01` — region 단위 가장 최근 success 시뮬 단일 쿼리 (artifact 무관)
- ✅ 신규 컴포넌트 `GisEpanetSimLayer.tsx` — `/sim/latest` 단일 호출 + MapLibre source/layer 추가
  · 노드: `circle` paint — 압력 색상 (HSL 240 파랑 → 0 빨강, 4구간 보간; 분포 없으면 단색 hsl(160) 폴백)
  · 파이프: `line` paint — 정류 청록 / 역류 주황 / |flow|<2% max 회색, 굵기 0.6~4 LPS 절댓값 비례 (분포 없으면 회색 1.5px)
  · 흐름 방향 화살표: SDF 화살표 SVG → addImage → `symbol` layer (`symbol-placement: line`)
    · symbol-spacing 200~600px 줌별 보간 (널찍한 간격)
    · 정류 0° / 역류 180° (`icon-rotate` case 표현식)
    · 색상 paint = line-color 와 동일 분기
  · 배수지: `circle` 청록 + 외곽선
- ✅ GIS 페이지 상단 툴바에 [EPANET 시뮬] 토글 버튼 (시설물 목록 옆)
  · 비활성 시 source/layer 자체 미추가 (성능 보호)
  · 활성 시 가장 최근 success 시뮬 1회 fetch + 캐시
  · 시뮬 없으면 화면 상단에 안내 메시지

### Phase 2.5 (2026-05-04 — 다중 vertex + 시뮬 좌표 응답 + SVG 시각화)
- ✅ INP `[VERTICES]` 섹션 — PolyLine 첫·끝점 외 중간 점 모두 보존 (굴곡 표현)
- ✅ 시뮬 응답 좌표 포함 — junction.x/y, pipe.vertices, reservoir.x/y, bbox
- ✅ tb_epanet_simulation_result.result_data 에 좌표 + 결과 저장
- ✅ `EpanetSimulationCanvas` SVG 컴포넌트 신규 (`components/epanet/`)
  · UTM-K 좌표 자체 SVG viewBox (좌표 변환 라이브러리 의존 X — proj4js 등 불필요)
  · 노드: 압력 색상 히트맵 (HSL 240→0)
  · 파이프: polyline + 유량 색상(정류 청록 / 역류 주황) + 굵기 (절댓값 비례)
  · 배수지: 청록 사각형
  · 호버 툴팁 (id / 압력 / 유량 / 유속 / 시작·끝 노드)
  · 압력·유량 범례
- 검증: artifact #4 송수관 132건 → 시뮬 #4 — bbox UTM-K 미터 좌표 [156280, 465458, 194003, 494896], 노드/파이프 좌표 응답 정상
- ⚠ MapLibre 통합 (GIS 페이지 오버레이) 은 별도 Phase 3 — 현재는 `/admin/epanet` 페이지의 자체 SVG 캔버스로 결과 확인

### Phase 3 (계획)
- 표고 데이터 매핑 (DEM/배수지 EL.) — EPANET junction.elevation 자동 입력
- 시간대별 수요 패턴 (계량기 데이터 + DAY/NIGHT pattern) — EPS(Extended Period Simulation) 활성
- 시나리오 분석 — 밸브 개폐·관로 파손·펌프 가동 변경 시뮬레이션 비교
- 실측-모델 비교 — 센서 실측값 vs EPANET 예측 편차
- GIS 페이지 통합 오버레이 — UTM-K → WGS84 (proj4js EPSG:5179→4326) 변환 후 MapLibre 압력/유량 레이어 표출
- 펌프·밸브 SHP 반영 (`SA100` 제수밸브)

### Phase 3 (계획)
- 표고 데이터 매핑 (DEM/배수지 EL.) — EPANET junction.elevation 자동 입력
- 시간대별 수요 패턴 (계량기 데이터 + DAY/NIGHT pattern) — EPS(Extended Period Simulation) 활성
- 시나리오 분석 — 밸브 개폐·관로 파손·펌프 가동 변경 시뮬레이션 비교
- 실측-모델 비교 — 센서 실측값 vs EPANET 예측 편차

### SHP 파일 위치
- 호스트: `/Users/jykim/web/files/gis/shp/` (`docs/SHP추출/` 의 SAA003/SAA004/SA114 만 복사)
- 컨테이너: `/data/files/gis/shp/` (`./files:/data/files` 바인드 마운트)
- 환경변수 `EPANET_SHP_BASE_DIR` 로 변경 가능
