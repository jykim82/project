# SLM Dashboard 기능 사양서

> 완성된 기능 + 구현 예정 사양을 통합 관리하는 문서입니다.
> 최종 갱신: 2026-04-08

---

## 1. 종합 현황판 (대시보드)

### 1.1 KPI 카드 (6종)
| # | 카드 | 아이콘 | 조건 | 클릭 질의 |
|---|------|--------|------|-----------|
| 1 | 이상 감지 | AlertTriangle (빨강) | 복합이상 + 이상 합산 | "전체 센서 이상 스캔해줘" |
| 2 | 교차검증 이상 | Shield (주황) | edge mismatch 수 | "교차 검증 결과 보여줘" |
| 3 | 설비 장애 | Wrench (보라) | 장애 설비 수 | "전체 센서 이상 스캔해줘" |
| 4 | 유량 불균형 | Droplet (시안) | 불균형 edge 수 | "물 수지 검증해줘" |
| 5 | 진행중 경보 | Bell (파랑) | 경고 + 주의 합산 | "경보 발생 이력 보여줘" |
| 6 | 정상 시설 | Activity (초록) | 정상 비율 % | (클릭 없음) |

- count > 0 일 때 펄스 애니메이션
- 클릭 시 QuickAnalysisDialog 호출 (AI SSE 스트리밍)

### 1.2 이상 시설 TOP 테이블
- 컬럼: 시설 / 유형 / Z-Score / 판정 / 그룹
- Z-Score >= 2 빨강, >= 1.5 주황
- 홀딩/설비장애 뱃지
- 행 클릭 → AI 진단 질의 (QuickAnalysisDialog)

### 1.3 유량 불균형 TOP
- 수평 막대 차트 (상류→하류 경로 + 불균형 %)
- 등급별 색상: 경고(빨강) / 주의(주황) / 기본(노랑)
- 클릭 → 물 수지 질의

### 1.4 시설유형별 이상 분포
- 배수지(에메랄드) / 가압장(주황) / 소블록(보라) / 소소블록(하늘) / 감압(핑크)
- 유형별 이상 건수 표시

### 1.5 하단 3열
| 열 | 내용 | 최대 |
|----|------|------|
| 설비 장애 현황 | 장애유형 뱃지 + 시설명 + 설비유형 | 8건 + "+N" |
| 데이터 품질 이상 | 센서무응답/데이터홀딩/데이터없음/데이터부족 카드 | 4종 |
| 최근 경보 | 시간 + 심각도 + 시설명 + 메시지 40자 | 10건 |

- 최근 경보 행 클릭 → 경보 분석 상세 팝업 (AlarmAnalysisDetail)

### 1.6 데이터 갱신
- 캐시 준비 완료: 5분(300초) 간격 자동 갱신
- 캐시 구축 중 (anomaly=null): 10초 간격 재시도
- API: `GET /dashboard/overview`

---

## 2. AI 채팅 (채팅)

### 2.1 SSE 스트리밍
- API: `POST /api/proxy/ask/stream`
- 4단계 진행 표시: 분류 → 추출 → 조회 → 렌더링
- 각 단계 뱃지 (파랑=진행, 초록=완료)

### 2.2 시각화 렌더링 (8종)
| graph_type | 컴포넌트 | 설명 |
|-----------|----------|------|
| table | VisualRenderer | 30행 제한 + CSV 다운로드 |
| plot | PlotChart | 라인/멀티축 + 이상구간 하이라이트 |
| diagram | VisualRenderer | 시설 계통도/위치도 |
| document | VisualRenderer | 매뉴얼/참조문서 |
| stddev | StddevAnalysisView | 표준편차 분석 |
| stddev_multi | StddevMultiAnalysisView | 다중시설 표준편차 비교 |
| anomaly | AnomalyVisualization | CUSUM/교차검증/설비장애/물수지 |
| pie | AlarmCauseRankChart | 경보 원인 순위 파이차트 |

### 2.3 세션 관리
- LocalStorage 기반 영속화
- 날짜별 그룹: 오늘/어제/이번주/이번달/이전
- 세션 검색, 생성, 삭제
- URL 파라미터 자동 질의: `?q=query`

### 2.4 추천 질의 (FAQ)
- 6 카테고리: 현황조회(파랑) / 트렌드(초록) / 이상감지(앰버) / 분석(보라) / 시설정보(시안) / 경보(빨강)
- `{sitename}` 플레이스홀더 → 실제 시설명 자동 치환
- 시설유형별 매칭 (배수지/가압장/감압)

### 2.5 자동완성
- 5 카테고리: 질의 템플릿 / 현장명 / 시설유형 / 데이터정보 / 블록레벨
- 한국어 초성 매칭 (예: "ㅂㅅ" → "배수지")
- 키보드 내비게이션 (↑↓ + Tab/Enter)
- API: `GET /api/proxy/autocomplete`

### 2.6 인텐트 보정
- `intentCandidates`: 벡터 유사도 기반 대안 인텐트 목록
- "원하는 답이 아닌가요?" → 클릭 시 `force_intent` 파라미터로 재질의

### 2.7 봇 응답 구조
- 답변 요약 (시맨틱 컬러 마커)
- 상세 항목 (계층 접두사: • = 그룹, - = 하위)
- 시각화 데이터
- 참조 섹션
- 추천 후속 질의
- 인텐트 보정 버튼

---

## 3. 트렌드 분석 (모니터링 > 트렌드)

### 3.1 기간 선택
- 사전 정의: 1일 / 7일 / 30일 / 사용자 지정
- DateRangePicker (커스텀 날짜 범위)

### 3.2 태그 선택
- 다중 태그 선택 (비교 차트)
- TagBrowserPanel: 태그 목록 탐색/검색
- 태그별 뱃지 (색상 구분) + 제거 버튼

### 3.3 차트
- TrendChart 컴포넌트 (ECharts)
- 듀얼 패널: 아날로그 상단 + 디지털 하단
- 태그별 고유 색상, 범례 토글
- 알람 한계선 (HH/LL) 표시
- **곡선 보간**: 아날로그 시리즈 `smooth: 0.3` + `smoothMonotone: 'x'` (monotone 보간, 오버슈팅 없음)
- **디지털 시리즈**: `step: 'end'` (계단형, smooth 미적용)

### 3.4 통계 카드
- 태그별: 최소 / 최대 / 평균 / 최근값
- ChartStatsBar 컴포넌트

---

## 4. 모니터링 > 배수지 / 가압장 / 블록

### 4.1 공통 구조 (MonitoringFacilityPage)
- 시설 선택: FacilityCombobox (권역 그룹 계층 드롭다운)
- 기간 선택: 24h / 7d / 30d / 사용자 지정
- 자동 갱신: 30초 간격
- rootFilter 쿼리 파라미터 지원 (수계별 필터)

### 4.2 카탈로그 기반 표시
- tb_monitoring_catalog 테이블 기준
- 카탈로그별 MonitoringTrendBlock:
  - 통계 카드: Min / Max / Avg / Last (태그별)
  - 라인/영역 차트 + 알람 한계선 (HH/LL markLine)
  - **곡선 보간**: plot-chart.ts `buildAnalogSeries()` — `smooth: 0.3` + `smoothMonotone: 'x'`
- 데이터 부족 경고 배너

### 4.3 시설유형별 설정
| 페이지 | facilityTypes | 비고 |
|--------|--------------|------|
| 배수지 | ["배수지"] | rootFilter 수계별 필터 지원 |
| 가압장 | ["가압장"] | |
| 블록 | ["소블록", "소소블록"] | |

---

## 5. 모니터링 > 감압시설

### 5.1 시설 현황 카드
- 그리드 레이아웃 (md: 2열)
- 카드당: 시설명 + 상태 뱃지
- 1차측(유입) 압력 = 빨강, 2차측(유출) 압력 = 파랑
- 감압비율 화살표 표시

### 5.2 압력 트렌드 차트
- EChartWrapper (buildPressureChartOption)
- 시리즈: 1차측(빨강 실선) + 2차측(파랑 실선 + 그라데이션 영역)
- Y축: 0~5 kgf/cm²
- 24시간 기본, 시설별 탭 전환
- **곡선 보간**: `smooth: true` + `smoothMonotone: 'x'` (양쪽 시리즈 공통)

### 5.3 데이터 갱신
- 30초 간격 자동 갱신

---

## 6. 모니터링 > 용수 흐름

### 6.1 실시간 네트워크 그래프
- FlowMonitoringGraph: 계통도 시각화
- 엣지 폭: 유량 비례 (2~14px)
- 엣지 색상: 회색(0) → 시안(저) → 파랑(고) 그라데이션
- 노드 상태: ok / warn / err / off
- 뷰 모드: 상세(네트워크) / 그룹(계통 트리)
- 미니맵 + 줌/팬

### 6.2 KPI 카드 (7종)
| KPI | 아이콘 | 조건 |
|-----|--------|------|
| 유량 활성 | Droplets (하늘) | flow.value > 0.01 |
| 유량 없음 | Activity (슬레이트) | flow.value <= 0.01 |
| 알람 진행 | AlertTriangle (빨강) | alarm_severity 존재 |
| 교차검증 이상 | AlertTriangle (빨강) | cross_mismatches |
| 유량 불균형 | AlertTriangle (앰버) | imbalance_grade != "정상" |
| 설비 장애 | Wrench (보라) | equip_failures 존재 |
| 통신 이상 | WifiOff (주황) | comm_error 플래그 |

- KPI 클릭 → 노드 필터링 + 하이라이트

### 6.3 배수지 패널 (좌측 사이드바)
- 공급가능시간 색상: >=24h(하늘) / 12~24h(초록) / 6~12h(앰버) / <6h(빨강)
- 평균 유입/유출, 시간당 사용량

### 6.4 타임라인 재생
- 이력 데이터 재생 (play/pause)
- 스크러버 (5눈금: 0/25/50/75/100%)
- 실시간 ↔ 타임라인 모드 전환

### 6.5 트렌드 패널 (하단 슬라이드업)
- 노드 클릭 → 실시간 메트릭 + 알람/장애 상세
- 데스크톱 200px / 모바일 60vh
- 액션 버튼 4종: 트렌드 / 시설 / 알람 / 진단 (패널 최상단 배치)
- 상호 배타적 팝업 (한 번에 하나만 열림)

### 6.6 GIS 팝업 연동 (노드 클릭 → 상세 팝업)
| 버튼 | 팝업 컴포넌트 | 동작 |
|------|-------------|------|
| 트렌드 | GisTrendPopup | 90vw x 90vh 모달, TrendChart 재사용, 기간 선택 |
| 시설 | GisFacilityInfoPopup | 일반현황 + 설비현황 테이블 |
| 알람 | GisAlarmPopup | 경보 리스트 → 경보 분석 2단 모달 |
| 진단 | QuickAnalysisDialog | AI SSE 스트리밍 이상 스캔 |

### 6.7 데이터 갱신
- 데이터: 60초 / UI 카운트다운: 120초
- 계통 선택 드롭다운 (전체 / 수계별)
- API: `GET /flow-map/realtime`, `GET /flow-maps`, `GET /flow-map/roots`

### 6.8 전체화면 확장 (레이아웃 내) [완료 2026-04-04]
- 계통도 카드 헤더의 "전체화면" 버튼 클릭 → 사이드바 우측 전체 영역으로 확장
- **확장 방식**: `position:fixed` + `left: var(--sidebar-width, 256px)` → 사이드바를 제외한 전체 뷰포트 사용 (브라우저 Fullscreen API 미사용)
- **애니메이션**: `opacity(0→1) + scale(0.97→1)`, 300ms ease-out (GIS 전체화면 동일 효과)
- **닫기**: "축소" 버튼 또는 ESC 키 → 역방향 애니메이션 후 패널 제거
- 확장 시 `fitViewTrigger` 카운터 증가 → 전체 화면 크기에 맞춰 카메라 자동 재조정
- 모바일: left:0 (사이드바 없음)

### 6.9 초기화면 버튼 (카메라 리셋) [완료 2026-04-04]
- 계통도 카드 헤더 상단에 "초기화면" 버튼 배치
- 클릭 시 계통도 카메라를 초기 OVERVIEW 스케일/위치로 즉시 리셋
- **애니메이션**: easeInOutCubic 380ms, `requestAnimationFrame` 루프로 부드럽게 보간 (GIS flyTo와 동일 방식)
- **자동 리셋 제거**: 데이터 갱신(60/120초)마다 zoom 초기화되던 동작 삭제 (`hasInitFitRef` 플래그)
  - 초기 마운트 1회, viewMode 전환 시에만 fitToView 실행
  - 데이터 리프레시로 `layout` 재계산되어도 카메라 위치 유지

---

## 7. GIS 관망도 (모니터링 > GIS 관망도)

### 7.1 LOD (Level of Detail) 자동 확장
| 줌 레벨 | 표시 내용 |
|---------|----------|
| ~11 | 마커(원형 점)만 |
| 12~14 | 축약 카드 (시설명 + Q/H/P 대표값) |
| 15+ | 확장 카드 (메트릭 그리드 + 트렌드/시설/알람/진단 버튼) |

- 클릭 없이 줌 레벨에 따라 자동 전환
- 아이보리 테마 (`rgba(255,252,245,0.96)`) 다크/라이트 동일

### 7.2 상태 판정 기준
- **tb_equipment_alarm_report** 기준만 사용
- 경고 (빨강): `alarm_severity = "경고"`
- 주의 (주황): `alarm_severity = "주의"`
- 정상 (초록): 위 해당 없음

### 7.3 마커 색상
| 시설유형 | 색상 | 코드 |
|---------|------|------|
| 배수지 | 딥블루 | #1D4ED8 |
| 가압장 | 앰버 | #F59E0B |
| 감압시설 | 바이올렛 | #a78bfa |
| 소블록 | 에메랄드 | #10B981 |
| 소소블록 | 바이올렛 | #8B5CF6 |

### 7.4 기능 버튼 (확장 카드 하단)
| 버튼 | 동작 | 팝업 컴포넌트 |
|------|------|-------------|
| 트렌드 | 별도 모달 (90vw x 90vh) | GisTrendPopup |
| 시설 | 별도 모달 | GisFacilityInfoPopup |
| 알람 | 별도 모달 -> 경보분석 2단 | GisAlarmPopup |
| 진단 | QuickAnalysisDialog | AI SSE 스트리밍 |

### 7.5 트렌드 팝업
- **TrendChart 컴포넌트 재사용** (모니터링 트렌드와 동일)
- 기간 선택: 1h / 6h / 12h / 24h / 7d / 1M / 6M / 1Y
- 태그: Analog 최대 8개 + Digital 최대 4개 자동 조회
- API: `/tags` (태그 목록) + `/trend/data` (시계열 500포인트)
- 듀얼 패널: 아날로그 상단 + 디지털 하단
- **곡선 보간**: TrendChart 재사용이므로 3.3 동일 (`smoothMonotone: 'x'`) 자동 적용

### 7.6 시설정보 팝업
- **시설 사진**: site_photo_url 표시 + 업로드 버튼 (JPG/PNG/WebP, 10MB 제한)
- **일반현황** (general_overview): 설치위치, 설치연도, 운영상태, 시설용량, 급수인구 등
  - 가압장: 펌프 제조사/대수/양정 (pump 객체 flat 변환)
- **배수지 규격** (reservoir_spec): 지수, HWL, LWL
- **설비 현황** (meta 배열): 설비유무="유" 항목만 테이블 표시 (구분/설비유무/감시제어)
- API: `GET /gis/facility-info?sitename=X&facilitytype=Y` (site_photo_url 포함)
- 사진 업로드: `POST /admin/facility-files/upload` (기존 API 재사용)

### 7.7 알람 팝업
- **경보 리스트** (1단 모달, z-index 9998)
  - 진행중 알람: 기간 무제한
  - 알람해제: 최근 30일
  - 진행중 우선 정렬
- **경보 분석** (2단 모달, z-index 9999)
  - 알람 클릭 시 별도 모달
  - 발생원인 / 대응방안 표시 (meta JSONB)

### 7.8 이상 진단 팝업
- **QuickAnalysisDialog 재사용** (대시보드와 동일)
- 질의: `"{현장명} {시설유형} 이상 스캔해줘"`
- sitename 필터링: 데이터품질/설비장애/물수지 모두 해당 시설만

### 7.9 레이어 목록
- **전체 레이어** 체크박스 (모니터링+SHP 전체 ON/OFF)
- **그룹별** 체크박스 (모니터링시설/관로/밸브시설/블록경계)
- **개별** 체크박스
- indeterminate 상태 (일부 선택 시 `-` 표시)

### 7.10 전체보기 버튼
- 유리효과 (backdrop-blur, 반투명 다크)
- 아이콘: 4개 네모 SVG
- 클릭 시: flyTo(당진 중심, 줌 11) + 선택 해제

### 7.11 포커싱 효과 (확정 사양)
- 시설 클릭(목록/마커/카드) -> **flyTo(줌 16, 2초, essential:true)** + 스포트라이트
- **flyTo**: MapLibre `essential: true` -- 브라우저 reduced-motion 설정 무시, 반드시 애니메이션
- **스포트라이트**: flyTo 완료 2초 후 발동
  - 위치: 지도 영역(.maplibregl-map) 중앙 계산 (getBoundingClientRect)
  - 색상: **시설유형별** (배수지=#1D4ED8, 가압장=#F59E0B, 소블록=#10B981, 소소블록=#8B5CF6)
  - hex->rgba 변환하여 radial-gradient 적용
  - 애니메이션: scale(0.15->1.3->1.8), opacity(0->1->0), 총 3.5초
  - 구현: `document.body.appendChild` 순수 DOM (React 트리 외부, transform 부모 회피)
  - z-index: 2147483647 (최대값)

### 7.12 블록경계
- fill-opacity: 0.18 (투명도 강화)
- 블록별 파스텔 20색 순환

---

## 8. 위기대응 > 경보현황 (경보 대시보드)

### 8.1 탭 구조
- **현황 탭**: 실시간 경보 대시보드
- **이력 탭**: 경보 기록 검색/조회

### 8.2 현황 탭
- 요약 카드 4종: 진행중 전체 / 경고 / 주의 / 정상
- 카테고리 도넛 차트 + 범례
- 시설별 경보 집계 테이블 (8건/페이지)
- 진행중 알람 목록 → 행 클릭 시 상세 모달

### 8.3 이력 탭
- 요약 카드 4종: 전체 / 경고 / 주의 / 복귀
- 필터: 기간(7일 기본) / 시설명 / 상태(진행중/알람해제) / 심각도(경고/주의/정상) / 카테고리(수위/압력/유량/밸브/펌프/네트워크/UPS)
- 경보 이력 테이블 (30건/페이지)
- 확인(컨펌) 버튼 + 행 클릭 → 상세 분석 모달

### 8.4 데이터 갱신
- 60초 자동 갱신
- API: `GET /crisis/alarm-dashboard`, `GET /crisis/alarm-reports`

---

## 9. 위기대응 > 경보분석

### 9.1 2패널 레이아웃
- **좌측**: 날짜별 그룹 알람 목록 + 상태 필터
- **우측**: 선택된 알람의 상세 분석 결과

### 9.2 상태 필터
- 진행중 / 필터링(오탐) / 알람해제
- 토글 뱃지 + 건수 표시

### 9.3 분석 결과 표시
- stat='Y' (분석 완료) 알람만 표시
- 구조화 표출: 진단원인/대응방안/필터결과/운영현황
- HTML 리포트: iframe + 다크모드 지원

### 9.4 데이터
- 60초 자동 갱신
- API: `GET /crisis/alarm-analysis`

---

## 10. 경보분석 구조화 표출

### 10.1 데이터 구조
- tb_equipment_alarm_report.meta JSONB에 저장
- Node-RED HTML에서 자동 파싱 -> 구조화 백필

### 10.2 카테고리별 표출
| 카테고리 | 표출 항목 |
|---------|----------|
| 수위/압력 | 경보등급분석 + 발생원인 + 대응방안 + 운영현황 (5섹션) |
| 네트워크/UPS/밸브 | 경보등급 + 발생원인 + 대응방안 + 참고사항 + 비상연락처 |
| 펌프 | 경보등급 + 발생원인 + 대응방안 |

### 10.3 meta JSONB 필드
- 공통: title, category, alarm_time, alarm_status, cause, action
- 간단형: grade, note, contacts [{name, phone}]
- 상세형: filter_result, expected_damage, supply_area, operations

---

## 11. 위기대응 > 작업관리

### 11.1 작업 등록 (TaskFormDialog)
- **시설유형** 드롭다운 선택 → **현장명** 해당 유형만 필터링 (DB facility_map, 83개 시설)
- **작업 종류**: 점검 / 정비 / 교체 / 청소 / 기타
- **시작/종료 시간**: datetime-local 입력
- **억제할 알람 유형**: 전체 버튼 + 개별 9종 (수위/압력/유량/펌프/밸브/통신/네트워크/UPS/수질)
- **개별 태그 추가**: 시설 선택 후 /tags API로 태그 검색 → 클릭 추가 (하늘색 뱃지, X 제거)
- **작업 내용**: 텍스트 입력

### 11.2 조회 필터 (TaskFilters)
| 필터 | 타입 | 설명 |
|------|------|------|
| 작업일자 (시작/종료) | date picker | 기간 내 작업 필터 |
| 시설유형 | 드롭다운 | 배수지/가압장/감압시설/소블록/소소블록 |
| 현장명 | 드롭다운 | 시설유형에 따라 필터링된 현장 목록 |
| 작업종류 | 드롭다운 | 점검/정비/교체/청소/기타 |
| 내용 | 텍스트 | task_content 키워드 매칭 |
| 진행중만 | 토글 | 종료시간 미래/NULL만 |

### 11.3 작업 테이블 (TaskTable)
- 컬럼: ID / 시간 / 현장명 / 시설유형 / 작업종류 / 중지알람 / 상태 / 종료 / 관리
- **정렬**: 시간/현장명/시설유형/작업종류/중지알람/상태 — 컬럼 헤더 클릭 asc/desc 토글
- 상태: 진행중(초록) / 완료
- 관리: 수정(연필) / 삭제(휴지통)

### 11.4 알람 연동
- GisAlarmPopup: 진행중 알람에 "작업등록" 버튼 → TaskFormDialog 자동 채움 (시설명/유형/카테고리/내용)
- 용수흐름 FlowNodeTrendPanel: 알람 버튼 → GisAlarmPopup → 작업등록

### 11.5 DB / API
- DB: `tb_task_master` (task_id, sitename, facilitytype, task_category, start/end_time, suspend_alarm_types JSONB, task_content, alarm_report_id)
- API: `GET/POST/PUT/DELETE /crisis/tasks`
- GET 필터: sitename, facilitytype, task_category, active_only, date_from, date_to, keyword

---

## 12. 네트워크 토폴로지 (네트워크)

### 12.1 듀얼 뷰
- **Force-directed**: 힘 기반 그래프
- **Hierarchical**: 계층 트리 레이아웃
- 토글 버튼으로 전환

### 12.2 요약 카드
- 전체 장비 / 정상 / 에러 / 경고

### 12.3 장비 테이블
- 장비 목록 + 상태 정보
- 행 클릭 → 노드 상세 사이드패널

### 12.4 기능
- 에러 장비 하이라이트 버튼
- 30초 자동 갱신
- 최종 점검시간 / 갱신시간 표시

### 12.4-b 초기화면 / 전체화면 (2026-04-07 추가)
- **초기화면 버튼**: 확대/이동 후 클릭 시 전체 뷰로 부드럽게 복귀 (시간 자동 초기화 아님)
  - Force 모드: ECharts `setOption(freshOption, true)` (Force 레이아웃 재시작)
  - 계층형 모드: easeInOutCubic 380ms rAF 카메라 애니메이션 (GIS flyTo 동일 방식)
  - `fitViewTrigger` prop 값 변경 → 컴포넌트 내부 fitView 호출
- **전체화면 버튼**: 사이드바 제외 화면 전체로 확장
  - `position: fixed` 오버레이 (`left: var(--sidebar-width)`)
  - opacity + scale 300ms 트랜지션 (확장/축소 양방향)
  - ESC 키로 전체화면 해제
  - 전체화면 내부에도 초기화면 + 축소 버튼 제공
- Force / 계층형 양쪽 모드에서 동일 동작
- 용수 흐름 계통도의 초기화면/전체화면과 동일 UX 패턴

### 12.5 통신이상 알람 카드 (2026-04-04 추가)
- **대상**: 이더넷(IP 기반) + 시리얼(DI 태그 기반) 통합 표시
- **이더넷**: `tb_network_status` MAX(check_time) 기준 status_code='이상' 장비
- **시리얼**: `tb_tag_info` (tagtype='Digital Input', datadesc ILIKE '%통신이상%') LATERAL JOIN 최신값=1 인 태그
- **API**: `GET /network/comm-alarms` → UNION ALL (serial_alarms + ethernet_alarms)
- **필터**: 전체 / 이더넷 / 시리얼 토글 버튼 (동일 버튼 재클릭 시 전체로 복원)
- **뱃지**: 이더넷=파랑, 시리얼=앰버

### 12.6 시리얼 장비 상태 (2026-04-04 추가)
- **토폴로지 노드**: `has_ip=false` 장비도 DI 태그 기반 status(정상/이상) 표시
- **serial_status CTE**: 시설명+시설유형 기준 DI 통신이상 태그 MAX 집계 → COALESCE(네트워크상태, 시리얼상태, '정상')
- **계층형 토폴로지**: 시리얼 장비도 status 보유 시 색상/에러 표시 동일 적용
- **장비 상세 패널**: 시리얼 장비 상태 → `시리얼(정상)` / `시리얼(이상)` 뱃지 (초록/빨강)

---

## 13. 관리 > 사용자 관리

### 13.1 사용자 테이블
- 검색: user_id / user_nm
- 컬럼: ID / 이름 / 상태 / 액션

### 13.2 기능
- 사용자 추가 (UserFormDialog, 중복 ID 검증)
- 활성/비활성 토글
- 계정 잠금 해제
- 세션 종료

---

## 14. 관리 > 메뉴 관리

### 14.1 메뉴 트리
- 트리 구조 (tb_menu.pmenu_idn 자기참조)
- 메뉴명 / app_path 편집 (MenuEditDialog)

---

## 15. 관리 > 프롬프트 관리

### 15.1 템플릿 카드
- 그리드 레이아웃 (2~3열)
- 카드별: 이름 / 설명 / 포맷
- 클릭 → 편집 다이얼로그 (PromptEditDialog)

---

## 16. 관리 > FAQ 관리

### 16.1 카테고리별 관리
- 카테고리 요약 카드 (건수 표시)
- 카테고리 필터
- 확장/축소 FAQ 목록

### 16.2 CRUD
- 추가 (FaqFormDialog) / 편집 / 삭제 (FaqDeleteDialog 확인)

---

## 17. 관리 > 시설 파일 관리

### 17.1 파일 유형
- 위치도 / 계통도 / 비상 매뉴얼

### 17.2 기능
- 시설별 파일 업로드/삭제
- 시설명/유형 필터
- FacilityFileUploadDialog

---

## 18. 관리 > 사이트 설정

### 18.1 랜딩 페이지
- 활성/비활성 토글 스위치
- tb_comm_code UPSERT

### 18.2 데이터베이스 접속정보
- 읽기 전용 표시 (host/port/db/user)
- 비밀번호 마스킹
- 연결 상태 뱃지 (connected/disconnected)

### 18.3 AI 모델 파라미터
- num_ctx: 슬라이더 (1024~32768, 기본 4096)
- temperature: 슬라이더 (0.0~1.0, 기본 0.0)
- timeout: 슬라이더 (10~120초, 기본 30)
- Ollama 상태 뱃지
- 서버 재시작 없이 즉시 반영 (_AiRuntimeSettings)

### 18.4 EPANET 수리 시뮬레이션 (Migration 0064 — Phase 1)
- 활성/비활성 토글 (`tb_comm_code SITE_SETTING.EPANET_ENABLED`, default 'N')
- 활성화 시: 관리 그룹에 "EPANET 시뮬레이션" 메뉴(M100-12) 노출, `/admin/epanet/*` API 200 응답
- 비활성 시: 모든 `/admin/epanet/*` 엔드포인트 503, 관리 페이지는 안내 카드만 노출
- 활성화 후 [관리 페이지로 이동] 버튼 제공 → `/admin/epanet`
- 상세 사양: `docs/gis_plan.md`

---

## 18-A. 관리 > EPANET 시뮬레이션 (M100-12, Phase 1)

### 18-A.1 페이지 구성
- 환경 진단 카드 — 모듈 활성/wntr 가용성/SHP 디렉토리/INP 출력 디렉토리/SHP 파일 분류 카운트
- 변환 작업 카드 — [SHP 스캔] / [INP 생성] 두 버튼
- 스캔 결과 표 — 파일/레코드/지오메트리/인코딩/필드 수 (스캔 실행 후 노출)
- 산출물 표 — 파일/노드/링크/상태/생성 시각/작성자 + [다운로드][삭제]

### 18-A.2 API (`/admin/epanet/*`)
| 엔드포인트 | 동작 |
|-----------|------|
| GET    /admin/epanet/status                     | 활성화·환경 상태 (토글 OFF 도 200) |
| POST   /admin/epanet/scan                       | SHP 메타·필드명·인코딩·BBOX (변환 전 검증) |
| POST   /admin/epanet/inp/generate               | SHP→.inp 변환 + tb_epanet_artifact 저장 |
| GET    /admin/epanet/inp/list                   | 산출물 목록 (region 필터, 최근순) |
| GET    /admin/epanet/inp/{id}/download          | .inp 파일 다운로드 |
| DELETE /admin/epanet/inp/{id}                   | 산출물 삭제 (DB+파일) |
| POST   /admin/epanet/inp/{id}/simulate          | (Phase 2) 정상상태 시뮬레이션 실행 |
| GET    /admin/epanet/inp/{id}/simulations       | (Phase 2) artifact 별 시뮬 이력 |
| GET    /admin/epanet/sim/{sim_id}               | (Phase 2) 시뮬 상세 (result_data 포함) |

### 18-A.3 SHP 입력 (Phase 1 자동 분류)
- 송수관/배수관: `SAA003*`, `SAA004*` (`PIPE_SHP_HINTS`)
- 배수지: `SA114*` (`RESERVOIR_SHP_HINT`)
- 위치: 컨테이너 `/data/files/gis/shp/` (호스트 `web/files/gis/shp/`), 환경변수 `EPANET_SHP_BASE_DIR` 변경 가능
- 인코딩: .cpg 자동 감지 + EUC-KR/CP949 폴백 (한글 필드명 정상 처리)

### 18-A.4 INP 출력
- 위치: 컨테이너 `/data/files/epanet/{region}_{YYYYMMDD_HHMMSS}.inp`
- 포맷: EPANET 2.2 텍스트 — `[TITLE]/[JUNCTIONS]/[RESERVOIRS]/[PIPES]/[OPTIONS]/[TIMES]/[REPORT]/[COORDINATES]` 등
- 노드 ID: 좌표 4자리 반올림 → MD5 해시 8자리 prefix (`N{hex8}`) — 동일 좌표 자동 병합
- 기본값: 관경 100mm / 조도 C=120 / 표고 0m / 수요 0 LPS

### 18-A.5 Phase 2 (2026-05-04 — wntr 시뮬레이션 동작)
- Migration 0065 — `tb_epanet_simulation_result` (sim_id/artifact_id/result_data JSONB)
- 산출물 표 행에 [시뮬] 버튼 추가 → 정상상태 시뮬 → 결과 미리보기 카드 (압력 범위·유량 범위·실행 시간)
- ARM64 환경: EPANET 네이티브 라이브러리 미포함 → `WNTRSimulator` 폴백 + PDD 모드 + 가장 큰 connected component 자동 추출
- 노드 좌표 1m 단위 병합 — SHP line 끝점 미접합으로 인한 disconnected component 감소
- 검증: 송수관 132건 → 시뮬 #3 — 노드 128 / 링크 131 / 압력 50.0m / 108ms

### 18-A.6 Phase 2.5 (2026-05-04 — 다중 vertex + SVG 시각화)
- INP `[VERTICES]` 섹션 — PolyLine 굴곡 점 보존
- 시뮬 응답 좌표 포함 — junction.x/y, pipe.vertices, reservoir.x/y, bbox
- `EpanetSimulationCanvas` SVG 컴포넌트 — 압력 히트맵·유량 색상·정/역류 구분·호버 툴팁·범례
- UTM-K 좌표 자체 SVG viewBox (변환 라이브러리 의존 없음)

### 18-A.6.5 Phase 2.6 후속 (2026-05-06 — 흐름 방향 정합성)
- pipe.start = 배수지 가까운 끝점 (SHP line direction 의 임의성 제거)
- reservoir snap — 200m 이내 가장 가까운 송수관 끝점으로 흡수 (connected component 자동 포함)
- default_demand_lps 0 → 0.1 (균등 demand 부여로 의미 있는 flow 발생)
- 검증: flow ±3.4 LPS, 정류 82 / 역류 42 (정류 우세 = 수원→소비처 정합)
- GenerateRequest 에 default_demand_lps 파라미터 추가 (운영자 조정 가능)

### 18-A.7 Phase 2.6 (2026-05-05 — GIS 페이지 시뮬 오버레이 + 토글)
- pyproj 의존성 추가 (`EPANET_SHP_CRS=EPSG:5186` default — 당진시 검증)
- 시뮬 응답에 lng/lat (WGS84) 포함 — junction.lng/lat, pipe.vertices_lnglat, reservoir.lng/lat, bbox_lnglat
- 신규 `components/gis/GisEpanetSimLayer.tsx` — 가장 최근 success 시뮬 자동 fetch + MapLibre paint expression
  · 노드 압력 → `circle-color` interpolate (HSL 240 → 0)
  · 파이프 유량 → `line-color` (정/역/거의 0 분기) + `line-width` interpolate
  · 배수지 → 청록 큰 원
- `monitoring/gis/page.tsx` 상단 툴바 [EPANET 시뮬] 토글 버튼 (cyan, 시설물 목록 우측)
- 토글 OFF 시 source/layer 미추가 (성능 보호)

### 18-A.8 Phase 3 (계획)
- 표고 데이터 매핑 (DEM/배수지 EL.)
- 시간대별 수요 패턴 + EPS 시계열
- 시나리오 분석·실측-모델 비교
- 펌프·밸브 SHP 반영 (SA100 제수밸브)
- 시뮬 선택 UI (GIS 오버레이에 사용할 sim_id 드롭다운 — 현재는 가장 최근 자동)

---

## 19. 구축 > 태그 마스터

### 19.1 태그 테이블
- 50건/페이지
- 컬럼: 태그SN / 태그유형 / 시설명 / 시설유형 / 설비유형 / 데이터항목 / 설명 / 단위 / 알람태그

### 19.2 필터
- 시설명 / 시설유형 / 태그유형 (Analog Input/Digital Input/Analog Output)
- 키워드 검색 (태그SN, 설명)

### 19.3 기능
- CSV 다운로드/업로드 (CsvUploadDialog)
- 태그 추가 (TagAddFormFields)
- DEMO_MODE 시 태그SN 숨김

---

## 20. 구축 > 설비 관리

### 20.1 설비 테이블
- 50건/페이지
- 필터: 시설명 / 시설유형 / 설비유형 + 키워드 검색

### 20.2 CRUD
- 추가/편집 (EquipmentFormDialog)
- 삭제 (EquipmentDeleteDialog 확인)
- CSV 다운로드/업로드

### 20.3 CSV 컬럼
설비ID, 시설명, 시설유형, 설비유형, 상태, 모델, 제조사, 설치일, 폐기일, 설명, 역할, 비고

---

## 21. 구축 > 배수지 관리

### 21.1 배수지 테이블
- 50건/페이지 + 키워드 검색

### 21.2 CRUD + CSV
- 추가/편집 (ReservoirFormDialog) / 삭제 확인
- CSV 컬럼: 시설명, 설치위치, 운영상태, 시설용량, 급수인구, 준공연도, 급수구역, 배수지수, 수위(HWL/LWL), 지수정보(5개존)

---

## 22. 구축 > 가압장 관리

### 22.1 가압장 테이블
- 50건/페이지 + 키워드 검색

### 22.2 CRUD + CSV
- CSV 컬럼: 시설명, 위치, 운영상태, 용량, 유형, 준공연도, 펌프대수, 양정, 시공사, 제조사, 정상가동펌프, 관정수, 연계배수지, 지구정보

---

## 23. 구축 > 감압시설 관리

### 23.1 감압시설 테이블
- 50건/페이지 + 키워드 검색

### 23.2 CRUD + CSV
- CSV 컬럼: 시설명, 위치, 운영상태, PRV제조사, 관경, 제어방식, 압력단위, 패턴, 기준

---

## 24. 구축 > 블록 관리

### 24.1 블록 테이블
- 50건/페이지
- 필터: 키워드 + 블록레벨 (소블록/소소블록)

### 24.2 CRUD + CSV
- CSV 컬럼: 시설명, 블록레벨, 위치, 수용가수, 유수율, 관로(전체/노후), 대구경, 압력, 지구정보

---

## 25. 구축 > 용수 흐름 관리

### 25.1 흐름 다이어그램
- FlowDiagramGraph: 상류→하류 관계 시각화
- 계통 선택 드롭다운

### 25.2 통계
- 노드 수 / 엣지 수 / 시설유형별 분포 뱃지

### 25.3 연결 관리
- 연결 테이블 (상류/하류/관계유형)
- 연결 추가/삭제
- CSV 다운로드/업로드
- API: `GET/POST/DELETE /flow-maps`

---

## 26. 구축 > GIS 설정

### 26.1 인터랙티브 지도
- GisMap + GisDragMarker (드래그로 좌표 이동)
- 다중 배경지도 (거리/위성)

### 26.2 좌표 관리
- GisEditPanel: GPS 좌표 편집
- 확인(confirmed) 추적
- 미저장 변경 추적 (pending changes)

### 26.3 시설 관리
- GisFacilityList: 좌측 시설 목록 + 클릭 줌
- 시설 추가 (GisAddFacilityDialog) / 삭제
- 시설유형별 레이어 토글

### 26.4 SHP 레이어
- 행정경계, 관로, 밸브 등 SHP 표시/숨김
- GisLayerPanel

---

## 27. 구축 > 인과 규칙

### 27.1 요약 카드
- 전체 시설 / 적용 시설 / 완전 매핑 / 오버라이드

### 27.2 템플릿 패널 (TemplatePanel)
- 인과 체인 정의 (step 순서, lag 시간)
- 4개 확장 섹션:
  - 안전연동 (safety_interlocks)
  - AND조건 (and_conditions)
  - 역방향 진단 (reverse_diagnostics)
  - 전파설정 (propagation)
- 선행조건 뱃지

### 27.3 시설 테이블 (FacilityTable)
- 시설유형별 탭: 가압장 / 배수지 / 감압시설 / 소블록
- 시설명 검색 + 매핑 상태 필터 (완전/부분/없음)
- 확장 행: step별 태그 매핑 현황
- 진행률 바 (초록=완전, 주황=부분, 빨강=없음)
- API: `GET /causal-rules`

---

## 28. 구축 > 모니터링 설정

### 28.1 기능
- MonitoringSetupPage 래핑
- 시설별 모니터링 카탈로그 구성
- 태그 할당/해제

---

## 29. 구축 > 캔버스 에디터

### 29.1 기능
- CanvasEditor (dynamic import, SSR 없음)
- 시각 다이어그램 편집
- 태그 링크 (pending 패턴)
- 2줄 태그 표시 (tagsn/datainfo)

---

## 30. 구축 > 네트워크 설정

### 30.1 탭 구조
- **네트워크 장비**: NetworkInfoTab (장비 구성)
- **네트워크 연결**: NetworkLinkTab (연결 구성)

---

## 31. 구축 > 컬럼 잠금

### 31.1 잠금 테이블
- 필터: 시설유형 / 잠금상태
- 컬럼: 테이블 / 필드 / 잠금상태(현장제어/시스템제어) / 사유

### 31.2 기능
- 잠금 토글 (FieldLockToggleDialog 확인 + 사유 입력)
- 요약 카드: 전체 / 잠금 / 해제

---

## 32. 인과 규칙 엔진 고도화

### 32.1 템플릿 구조 확장
| 섹션 | 설명 |
|------|------|
| chain | 기존 순방향 선형 체인 (유지) |
| cross_facility | 하류 시설 전파 (유지) |
| safety_interlocks | 안전 연동 (HH/LL -> 설비 정지) |
| and_conditions | AND 조건 (복합 조건 -> 결과 기대) |
| reverse_diagnostics | 역방향 진단 (결과 -> 원인 추적) |
| propagation | 전파 설정 (max_hops, 방향) |

### 32.2 선행조건 (requires)
- step에 `requires` 필드 추가
- 예: 펌프 ON -> 밸브 OPEN + 전원 NORMAL 필요
- 미충족 시 PREREQUISITE_FAILED 패턴

### 32.3 규칙 현황
| 시설유형 | 선행조건 | 안전연동 | AND조건 | 역방향 | 전파홉 |
|---------|---------|---------|--------|-------|-------|
| 가압장 | 1 | 2 | 1 | 1 | 3 |
| 배수지 | 0 | 2 | 1 | 1 | 3 |
| 감압시설 | 0 | 1 | 0 | 0 | 2 |
| 소블록 | 0 | 0 | 0 | 0 | 1 |

---

## 33. ANOMALY_SCAN_ALL sitename 필터링

### 33.1 필터 대상
| 데이터 | 필터 함수 |
|--------|----------|
| Z-Score rows | _filter_anomaly_cache_rows (기존) |
| 교차검증 불일치 | 기존 sitename 필터 |
| 데이터 품질 | _filter_by_sitename (신규) |
| 설비 장애 | _filter_by_sitename (신규) |
| 물 수지 불균형 | _filter_flow_balance (신규) |

### 33.2 동작
- sitename 지정 시: 해당 시설만 필터링
- sitename 미지정/"전체": 원본 반환 (기존 동작 유지)
- AI 채팅 + GIS 진단 동일 적용

---

## 34. 경보 이력 (단독)

### 34.1 요약 카드
- 전체 / HH(상한위험) / H(상한주의) / 복귀

### 34.2 필터
- 기간 (7일 기본) / 레벨 (HH/H/L/LL) / 시설유형 (배수지/가압장/감압)

### 34.3 경보 테이블
- 컬럼: 발생시각 / 시설명 / 경보레벨 / 태그명 / 값 / 임계값 / 상태(발생중/복귀)

---

## [완료] 35. 통신이상 알람 실시간 조회 (2026-04-04, commit edac6b2/da4e63f)

> Node-RED 배치 방식 대신 실시간 DI 태그 직접 조회 방식으로 구현

### 35.1 구현 방식 (변경)
- **당초 계획**: Node-RED 60초 배치 → tb_equipment_alarm_report INSERT
- **실제 구현**: API에서 tb_tag_raw_data LATERAL JOIN 실시간 조회 (Node-RED 불필요)

### 35.2 감지 방법 (구현)
| 감지 유형 | 데이터 소스 | 판정 조건 |
|----------|-----------|----------|
| 시리얼 통신이상 | tb_tag_raw_data LATERAL (최신값) | tagtype='Digital Input', datadesc ILIKE '%통신이상%', val=1 |
| 이더넷 통신이상 | tb_network_status MAX(check_time) | status_code='이상' |

### 35.3 백엔드 구현
- **파일**: `D:\slm\endpoints\network_crud.py`
- **신규 엔드포인트**: `GET /network/comm-alarms`
- **UNION ALL**: serial_alarms + ethernet_alarms → comm_type 구분자
- **SQL 2**: 네트워크 장비 alive 상태 집계
- **Function**: 감지 결과 → tb_equipment_alarm_report INSERT/UPDATE
  - alarm_severity: 통신이상 수에 따라 "경고"(3개+) / "주의"(1~2개)
  - category: "통신"
  - meta JSONB: { cause, action, affected_tags: [...] }

### 35.4 DB 업데이트 대상
| 테이블 | 컬럼 | 동작 |
|--------|------|------|
| tb_equipment_alarm_report | 신규 INSERT | 통신이상 알람 기록 (stat=진행중) |
| tb_equipment_alarm_report | UPDATE stat | 통신 복구 시 알람해제 |

### 35.5 프론트엔드 연동 (기존 기능 활용)
- **대시보드**: 진행중 경보 KPI 카드에 자동 반영
- **GIS**: alarm_severity 기반 마커 상태색 (주의=주황/경고=빨강)
- **계통도**: comm_error 플래그 → WifiOff 아이콘 + KPI "통신 이상"
- **경보현황**: 경보 리스트에 자동 표시

### 35.6 구현 파일
- `flows_deploy.json` — 신규 통신이상감지 탭 추가
- 프론트엔드 변경 없음 (기존 alarm_severity/comm_error 인프라 재사용)

---

## [예정] 36. 알람 → 작업관리 등록 연동

### 36.1 목적
- 경보 발생 시 예방정비 작업을 등록하여 중복 알람 억제
- 작업 진행 중에는 해당 시설의 알람 자동 필터링

### 36.2 동작 흐름
1. 경보현황/GIS/대시보드에서 알람 확인
2. "작업 등록" 버튼 클릭 → 작업관리 폼 자동 채움 (시설명/유형/카테고리)
3. 작업 저장 → tb_task INSERT (start_time, end_time, suspended_alarm_types)
4. 작업 진행 중: 해당 시설+알람유형 경보 억제 (필터링)
5. 작업 종료/만료 → 알람 억제 해제

### 36.3 연동 지점
| 페이지 | 버튼 위치 | 자동 채움 필드 |
|--------|----------|--------------|
| 경보현황 > 현황탭 | 알람 행 우측 | sitename, facilitytype, category |
| GIS > 알람 팝업 | 알람 행 우측 | sitename, facilitytype, category |
| 대시보드 > 최근 경보 | 알람 행 우측 | sitename, facilitytype, category |

### 36.4 작업관리 API 확장
- `POST /crisis/tasks` — 기존 API 활용
- 요청 본문에 alarm_report_id 추가 (연결 추적)

### 36.5 알람 억제 로직
- Node-RED/AI Server에서 알람 발생 시 `tb_task` 조회
- 해당 시설 + 알람유형 + 현재시간이 작업 기간 내 → 알람 미발생 또는 "필터링" 처리
- is_false_alarm = 'Y' 대신 task_id 참조 (원인 추적 가능)

### 36.6 구현 파일
- `GisAlarmPopup.tsx` / `AlarmReportTable.tsx` / `RecentAlarmList.tsx` — "작업 등록" 버튼 추가
- `TaskFormDialog.tsx` — 신규 작업 등록 다이얼로그 (자동 채움)
- `ai_server.py` — 알람 발생 시 task 확인 로직
- `flows_deploy.json` — 알람등급 플로우에 task 확인 노드 추가

---

## [예정] 37. 계정 권한 관리 구조화 + API 구축

### 37.1 현재 상태
- 인증: dev 폴백 (admin/1234, kwater/1234, jykim/midi1212) — AI Server 로그인 API 미구현
- 권한: MASTER / ADMIN / USER 3단계 (tb_auth)
- 메뉴 필터: 프론트엔드에서 adminOnly 정적 필터링
- 사용자: tb_user 테이블 (user_pw_hash 컬럼 있으나 bcrypt 미적용)

### 37.2 구현 목표

#### Phase 1: 인증 API 구축
- `POST /api/auth/login` — region + user_id + password → JWT 발급 (access_token + refresh_token)
- `POST /api/auth/refresh` — refresh_token → 새 access_token
- `POST /api/auth/logout` — 세션 무효화 (tb_user_session)
- `GET /api/auth/me` — 현재 사용자 정보 + 권한별 메뉴 트리 반환
- 비밀번호: bcrypt 검증 + pw_migrated 플래그 (기존 AES→bcrypt 점진 전환)

#### Phase 2: 권한 기반 메뉴 제어
- tb_auth_menu: 권한(auth_idn) × 메뉴(menu_idn) 접근 제어 테이블
- `/api/auth/me` 응답에 접근 가능 메뉴 트리 포함
- 프론트엔드: 정적 sidebarMenus → API 기반 동적 메뉴로 전환
- MASTER: 모든 메뉴 접근 + 메뉴 숨김/표시 토글 UI

#### Phase 3: 사용자 관리 UI 고도화
- 사용자 CRUD: 생성/수정/삭제 + 권한 변경
- 비밀번호 초기화 (관리자/마스터만)
- 계정 잠금/해제 (lock_cnt 기반)
- 세션 강제 종료
- 로그인 이력 조회

#### Phase 4: MASTER 전용 기능
- 메뉴 숨김/표시 토글 (tb_menu.visible_yn)
- 시스템 설정 접근 (DB 접속정보, AI 모델 파라미터)
- 전체 사용자 권한 변경
- 감사 로그 조회

---

## 38. ECharts 차트 공통 사양

### 38.1 라인 시리즈 곡선 보간 표준

모든 아날로그 라인 시리즈에 **monotone 보간**을 적용한다. (commit `3217876`, 2026-04-08)

| 속성 | 값 | 설명 |
|------|-----|------|
| `smooth` | `true` 또는 `0.3` | 부드러운 곡선 활성화 |
| `smoothMonotone` | `'x'` | 단조(monotone) 보간 — 오버슈팅 방지 |

**오버슈팅(overshooting)이란?**  
단순 cubic spline은 데이터가 급격히 변하는 구간에서 실제 값 범위를 초과하는 곡선이 그려진다.  
`smoothMonotone: 'x'`는 인접 포인트 사이에서 단조 보간을 보장해 수위·압력·유량 등 물리적 경계가 있는 데이터에서 과대/과소 표현을 막는다.  
Recharts `type="monotone"`, Nivo `curve="monotoneX"` 와 동일한 알고리즘.

### 38.2 적용 범위

| 파일 | 함수/컴포넌트 | 대상 시리즈 | smooth 값 |
|------|-------------|------------|-----------|
| `plot-chart.ts` | `buildAnalogSeries()` | 트렌드/모니터링 아날로그 | `0.3` |
| `plot-chart.ts` | dual panel analog | 혼합 패널 아날로그 | `0.3` |
| `reservoir-chart.ts` | `buildReservoirChartOption()` | 배수지 수위 | `true` |
| `pressure-chart.ts` | `buildPressureChartOption()` | 감압 1차측·2차측 압력 | `true` |
| `booster-chart.ts` | `buildBoosterChartOption()` | 가압장 토출압력·유량 | `true` |
| `StddevAnalysisView.tsx` | 인라인 series | 표준편차 분석 라인 | `true` |
| `StddevMultiAnalysisView.tsx` | 인라인 series | 복수 표준편차 라인 | `true` |
| `LeakCusumView.tsx` | 인라인 series | 야간최소유량·CUSUM | `true` |

### 38.3 제외 대상 (smooth 미적용)

| 조건 | 이유 |
|------|------|
| `step: 'end'` 디지털 시리즈 | 계단형(ON/OFF)이므로 보간 불필요/불가 |
| `markLine` 기준선·임계값 | 수평 직선이므로 보간 불필요 |
| bar / pie 차트 | 라인 아님 |

### 38.4 검증 결과 (Playwright, 2026-04-08)

5개 화면에서 Recharts `type="monotone"` 대비 동등 수준 확인:

| # | 화면 | 컴포넌트 | 결과 |
|---|------|---------|------|
| 1 | 트렌드 분석 | TrendChart (아날로그 2태그) | ✅ smooth 곡선, 디지털 계단형 구분 정상 |
| 2 | 배수지 모니터링 (24h) | MonitoringTrendBlock | ✅ 수위 smooth, HH/LL 직선 유지 |
| 3 | 감압시설 | buildPressureChartOption | ✅ 1·2차측 모두 smooth, 오버슈팅 없음 |
| 4 | 배수지 모니터링 (7일) | MonitoringTrendBlock (장기 범위) | ✅ 큰 진폭에서도 monotone 보간 정상 |
| 5 | GIS 관망도 트렌드 팝업 | GisTrendPopup (TrendChart 재사용) | ✅ 다중 시리즈 smooth, 아날로그/디지털 패널 정상 |

---

## [완료] 39. Isolation Forest v2 — 시설 단위 다변량 이상 감지 (2026-04-08)

### 39.1 배경 및 문제점

기존(v1) IF는 **태그별 독립 단변량 모델**로, 자기 자신의 값이 시간대별 분포에서 벗어났는지만 판단했다.  
수처리 시스템은 센서 간 물리적 인과 관계가 강하기 때문에, 단변량 모델로는 아래와 같은 복합 이상을 탐지할 수 없었다.

| 시나리오 | 이상 패턴 | v1 탐지 |
|---------|----------|---------|
| 누수 의심 | 수위 하락 + 유출유량 0 + 유입유량 정상 | ❌ |
| 펌프 공회전 | 주파수 높음 + 전류 낮음 + 토출압력 미달 | ❌ |
| 하류 차단 | 유량 정상 + 압력 급등 | ❌ |
| 밸브 이상 | 수위 정상 + 유출유량 급감 | ❌ |
| 감압 실패 | 유입압력 정상 + 유출압력 이상 | ❌ |

### 39.2 v2 아키텍처 — Tier 구조

```
Tier-1 (시설 다변량)  ← 우선 적용
  (sitename, facilitytype) 단위 1개 모델
  상관 센서를 하나의 feature vector로 학습

Tier-2 (태그 단변량)  ← Tier-1 미포함 태그 fallback
  기존 per-tag 방식 유지
```

### 39.3 Tier-1 feature vector 정의

| 시설 유형 | 필수 feature | 선택 feature | 시간 feature |
|----------|------------|-------------|-------------|
| 가압장   | 토출압력(`PRESSURE_DISCHARGE`), 유출유량(`FLOW_OUTLET`) | 유입압력(`PRESSURE_INLET`) | hour, dow |
| 배수지   | 수위(`WATER_LEVEL`), 유출유량(`FLOW_OUTLET`) | 유입유량(`FLOW_INLET`) | hour, dow |
| 감압시설 | 유입압력(`PRESSURE_INLET`), 유출압력(`PRESSURE_OUTLET`) | — | hour, dow |
| 소블록/소소블록 | 유량순시(`FLOW_INSTANT`), 압력(`PRESSURE`) | — | hour, dow |
| 저수지   | 수위(`WATER_LEVEL`), 유출유량(`FLOW_OUTLET`) | 유입유량(`FLOW_INLET`) | hour, dow |

- **필수 feature** 중 하나라도 누락(≤0.001) → 예측 불가 (None 반환)
- **선택 feature** 는 30일 학습 데이터에 실제 존재할 때만 포함 (시설마다 다를 수 있음)
- **동일 group_code 태그 복수** (펌프 병렬 운전 등) → 최댓값 사용

### 39.4 datainfo → group_code 매핑 (`DATAINFO_TO_GROUP`)

긴 키워드 우선 매칭 순서:

| datainfo 키워드 | group_code |
|---------------|-----------|
| 유출유량 | FLOW_OUTLET |
| 유입유량 | FLOW_INLET |
| 유량순시 | FLOW_INSTANT |
| 토출압력 | PRESSURE_DISCHARGE |
| 유출압력 | PRESSURE_OUTLET |
| 유입압력 | PRESSURE_INLET |
| 수위 | WATER_LEVEL |
| 압력 | PRESSURE |
| 유량 | FLOW |

### 39.5 학습 설정

| 파라미터 | 값 | 설명 |
|---------|-----|------|
| 학습 기간 | 30일 cagg | `cagg_5min_raw_stats_ai` |
| 최소 타임스탬프 (Tier-1) | 80개 | 공동 관측 기준 |
| 최소 샘플 (Tier-2) | 100개 | 기존과 동일 |
| contamination 기본 | 5% | 그룹별 A:3% / B:5% / C:8% / D:5% |
| 재학습 주기 | 24시간 | 서버 시작 10초 후 첫 실행 |

비가동 데이터 제외: `abs(val) < 0.001` (펌프 정지 구간)

### 39.6 예측 인터페이스

| 메서드 | 용도 |
|--------|------|
| `predict_for_rows(rows, columns)` | ANOMALY_SCAN_ALL — 전체 태그 일괄 판정 |
| `predict_facility(sitename, ft, sensor_vals)` | ANOMALY_FACILITY_DETAIL — 단일 시설 직접 예측 |
| `predict_single(tagsn, val, hour, dow)` | Tier-2 하위 호환 |

`predict_for_rows` 반환값 추가 필드:

| 필드 | 설명 |
|------|------|
| `facility_results` | `{sitename/facilitytype: {is_anomaly, anomaly_score, features_used, tier}}` |
| `tier1_count` | Tier-1 판정 시설 수 |
| `tier2_count` | Tier-2 판정 태그 수 |

`ANOMALY_FACILITY_DETAIL` 응답 추가 필드:

| 필드 | 설명 |
|------|------|
| `ml_tier` | 1 = 다변량, 2 = 단변량 fallback |
| `ml_anomaly_score` | IF anomaly score (낮을수록 이상) |
| `ml_features_used` | 예측에 사용된 group_code 목록 |

### 39.7 구현 파일

| 파일 | 변경 내용 |
|------|---------|
| `D:\slm\anomaly_iforest.py` | v2 전면 재설계 (FacilityModel 클래스, Tier-1/2 분리) |
| `D:\slm\ai_server.py` | ANOMALY_SCAN_ALL / ANOMALY_FACILITY_DETAIL 연동 업데이트 |

