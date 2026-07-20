## 현재 작업 상태
- 작업 진행할 때마다 CLAUDE.md의 "현재 작업 상태" 섹션을 업데이트해.
- 완료된 항목, 진행 중인 항목, 남은 항목을 정리해둬.

### 완료 (2026-07-18~19 — 검증 라운드 + GIS 오프라인 후속 + 메뉴 개편)

**1. 통합 검증·정리 라운드** — 프런트 e2e 5/5(hydration 레이스 헬퍼 수정)
· 백엔드 스모크 16/16 · 주요 화면 6종 콘솔 0 · FlowDiagram.tsx 삭제 · tmp 정리
**2. GIS 오프라인 후속 (E-040~E-042)** — block_index 검정 폴백, 경계선
폴리곤화·중복 제거(SHP 정규화), geojson ETag 캐시(24h 구본 재사용 근절),
Voyager 톤 테마 재현, 블록 채도 전과 동일 보정
**3. 모션 예외 정비** — 알람 리플·숫자 카운트업을 기능성 모션으로 reduce-motion
예외 편입 (탭 숨김 시에만 정지)
**4. 트렌드 재진입 무음 갱신 (E-039③)** — trend-store silentUpdate
**5. GIS 인스펙터 펌프 가동 카드** — 가압장 N대 중 M대 (flow-map/realtime 재사용)
**6. 메뉴 개편 (Migration 0102~0104)** — 관리 명칭 3건+이동 2건, 구축 통합
3종(시설정보 구축/계통도 설정/GIS 설정 — SetupTabbedPage 탭·fill 모드)
+명칭 6건+미구현 잠금 관리 삭제
**7. 누수 의심 알림 상단 통계** — KPI+14일 추이+CUSUM 초과율 Top5

### 완료 (2026-07-17 — 현장 모드 v2/v3 + 누수 사유 + E-037/E-038)

**1. 현장 모드 v2 (알람 중심) → v3 (시설 선택·일상점검)** (`slm-dashboard`)
- v2: 대응 필요 알람 + 알람별 액션 2×2 (사진 진단/사진 고장 기록/초동대응
  매뉴얼/조치 완료), 진행중 장애 행별 📷 조치 완료 (조치 후 사진)
- v3: 시설 검색·선택 → 일상 점검 기록(사진→점검/일상 자동 분기)·알람 없는
  설비 고장 기록. isFaultRecordIntent 점검 패턴 갭 수정
- 노출: FieldModeBanner (모바일 하단 전환 제안) + 대시보드 교체 KPI 를
  3신호 융합으로 통일

**2. 누수 의심 알림 선정 사유 서술** (`slm`, Migration 0100)
- CUSUM 근거를 스캔 시점 자연어 저장 (최근 7일 vs 기준·최초 초과 날짜·완화
  상태·추세). cusum_value 를 cusum_current→cusum_max 로 교정 ("0.00 인데
  누수의심" 혼란 해소). docs/leak-alert-spec.md 신설

**3. [E-037] 오타 보정 안내 세션 누수** (`slm@5803163`)
- '_' 접두 파생 params 세션 누적 제외. 규약 intent-architecture-spec 명문화

**4. [E-038] GIS 첫 로그인 마커 미표시** (`slm-dashboard@c7d3cdb`)
- WebGL 레이어 유실 자가 복구 (idle 존재 확인→리마운트 ≤3회). 부수 발견:
  베이스맵 cartocdn 외부 CDN — 납품 전 오프라인 타일 번들 필요 (review-items)

### 완료 (2026-07-16 후반 — 사양·백로그 배치: 인텐트·통계·E-037·번들·현장 모드)

**1. REPLACEMENT_PRIORITY_QUERY 채팅 인텐트** (`slm@…`)
- "교체해야 할 설비 알려줘" → replacement_priority() 직접 호출 (이원화 방지).
  선언+핸들러 1파일, ai_server 무변경 (3단계 아키텍처 검증 사례). 스모크 16/16

**2. 일 점검 보고서 통계 헤더** (`slm-dashboard@…`)
- ReportStatsHeader 공통 적용, 라벨 "점검 항목" 분기

**3. [E-037] 오타 보정 안내 세션 누수 수정** (`slm@5803163`)
- _corrections 가 세션 누적 → 다음 턴 재표출. '_' 접두 파생 키 누적 제외.
  규약을 intent-architecture-spec 에 명문화

**4. 모델 웨이트 오프라인 번들** (`slm/tools/model_weights_bundle.sh`)
- pack/install/verify (sha256 31파일 전수 + 번들 체크섬). 종단 테스트 +
  손상 감지 확인. docs/operations/model-weights-bundle.md — 납품 리스크 해소

**5. 현장 모드 /field** (`slm-dashboard@…`, Migration 0099)
- 모바일 런처: 사진 진단(핸드오프→채팅 첨부+프리필)·음성 기록(VAD→프리필)·
  진행중 장애 Top5(탭→조치 기록 프리필). docs/field-mode-spec.md

### 완료 (2026-07-16 — 현장 UX 라운드: 음성 VAD·게이지 PoC·교체 우선순위·팝업 전체화면)

**1. 음성 입력 고도화** (`slm-dashboard@677d59d`, `slm@…stt`)
- VAD 무음 감지 자동 종료 (발화 후 2s 무음 → 자동 전사, 무발화 8s 취소) —
  "종료 버튼 재클릭" UX 지적 해소. 수동 정지 폴백 유지
- STT 도메인 프롬프트 동적화: DB 시설명(tb_equipment_info+tb_facility_alias)
  1h 캐시 결합, 224 토큰 절단 대비 핵심 용어 후방 배치. "기지시/소난지도" 2/2

**2. VLM 게이지 판독 PoC → 라이트 통합 종결**
- 합성 게이지 5/5 값 정확 (gemma4 비전). 본격 기능화 보류(사용자 결정 —
  점검 보고는 숫자 기록 아님), 진단 observed_state 관찰 한 줄만 채택

**3. 교체 우선순위 Top 5 카드** (`slm@b9deba5`, `slm-dashboard@…`)
- 설비 건강성 개요 최상단 — 3신호 융합(내용연수/MTBF/재발) 랭킹+사유 배지,
  행 클릭 → 상세 탭. docs/equipment-health-priority-spec.md

**4. 장애조치 보고서 통계 헤더 + resolved_at 역전파** (이전 세션분 포함)
- KPI 4카드+분류 칩+기간 필터. task 완료 시 초안 보고서 항목 resolved_at 자동 회복

**5. 모든 팝업 전체화면 토글** (`slm-dashboard@…dialog`)
- DialogContent expandable 기본 true — 35개 팝업 일괄. docs/popup-expand-policy.md

### 완료 (2026-07-14 — 코드 최적화 라운드: GBT 25× + supply_time 공용화 + epanet·ai_server 분리)

3개월(4월 정리 이후 +39K줄) 드리프트 검토 후 승인받아 순차 수행. 4개 작업 전체 완료:

**1. GBT baseline 시(hour) 버킷 최적화** (`slm@ab2596c`)
- gbt_baseline() 타임스탬프별 예측(10,080행)+_roll 전수 스캔 → 고유 시 버킷(~169행)만
  예측 후 매핑. 완전 동치(고정창 10,080값 일치). 벤치 78.1s→3.06s, 실 UI 636ms.
- 사양: trend-comparison-spec §6.4 ③ 완료 처리

**2. supply_time 표시 규칙 단일 소스화** (`slm-dashboard@80decec`)
- `src/lib/supply-time.ts` — 충전 배지/24h+ 캡/저수위 임계(12%/90%) 중앙화
- 소비처 5곳 교체. **공용화 중 5번째 누락 지점 발견·수정** (monitoring/flow 사이드
  리스트가 충전 상태 24.0h 표기). WaterLevelGauge 임계도 공용 상수로.

**3. endpoints/epanet.py 3,223줄 → 패키지 12모듈** (`slm@8ea5743`)
- common/points_crud/flow_map/deviation/menu_settings/leak_headloss/whatif/
  assessment/data_quality/artifacts/simulations. import 경로 유지(ai_server 무변경).
- 라우트 48개 diff 0, EPANET 임시 활성화 GET 20개 본문 200 스모크 후 원복.

**4. ai_server.py Phase 4 — intent_matching.py 분리** (`slm@f6b6200`)
- 질의 정규화·인텐트 매칭 클러스터 639줄 이관. 6,449→5,876줄.
- FACILITY_ALIAS_MAP 재바인딩 시맨틱 유지(ai_server 잔류). 채팅 E2E 4종 OK.

**5. 채팅 스모크 테스트 체계 (Tier 1)** (`slm@45f6db9`)
- test_chat_smoke.py + chat_smoke_cases.json — 대표 인텐트 16케이스 /ask/stream 구조
  assert, exit 0/1, ~1분. 가이드 docs/chat-smoke-test-guide.md. 납품 검수 겸용.
- 부정 테스트로 fail 경로 검증. 첫 실행에서 40s 아웃라이어 발견.

**6. FACILITY_TAG_LATEST_VALUE 40s→0.1s** (`slm@c9a236c`)
- 최신값 SQL 이 시간 하한 없이 태그당 전 기간 스캔(251,721 buffers) → LATERAL
  top-1 (28 buffers, ~9,000×). 신/구 결과 EXCEPT 0건. 스모크 스위트 51s→11s.

**7. ai_server Phase 5 — /ask 어댑터화** (`slm@e0eaa9d`)
- _ask_inner(1,533줄)가 SSE event_generator 와 인텐트 42분기 1:1 복제 →
  /ask 를 SSE 내부 소비 어댑터(~50줄)로 재구현, 복제 삭제. **5,879→4,374줄**.
- SSE 응답 ml_* 8필드 누락(기존 격차) 동반 해소 — 프런트 ML 배지 복원.
- 검증: /ask 68케이스 전/후 구조 차이 0건 + 스모크 16/16 (게이트 첫 실전).
- **ai_server.py 누적: 15,084 → 4,374줄 (71% 감소)**

### 완료 (2026-07-11 — 대시보드 KPI 팝업 카드 일관화 + 설비장애 인텐트 + 트렌드 다중tag + 이상탐지 필터)

**대시보드 KPI 팝업(QuickAnalysisDialog) 카드 디자인 일관화**
- 교차검증 → `CrossValidationList`(상류→하류 흐름+가동률 바+진단 배지). 백엔드
  ANOMALY_CROSS_FACILITY 구조화 데이터(cross_facility_mismatches) top-level 노출.
- 물수지/경보이력 → 기존 카드(FlowBalanceInfographic/AlarmHistoryList) 위 중복 flat
  텍스트 억제. 팝업 VisualRenderer 에 intent 전달로 AlarmHistoryList 활성화.
- 로딩 스피너 `.slm-live-spin`/`.slm-live-pulse` reduced-motion 예외를 globals.css
  전역 승격(팝업 스피너 멈춤 해결) + 로딩 중 진행 스텝퍼 상시 표시.

**설비 장애 전용 인텐트 EQUIPMENT_FAULT_STATUS**
- 이상감지·설비장애 KPI 가 동일 질의('전체 센서 이상 스캔')였던 버그 → DI 설비 고장
  전용 조회 신설. ANOMALY_SCAN_ALL 캐시(equipment_failure_impacts) 재사용.
- 프런트 `EquipmentFaultList`(유형 칩+현장/설비+영향태그) + 행 펼치기 태그 드릴다운
  (affected_tags). 영향/이상 카운트 범례.
- 이상감지 KPI 는 값이탈 focus(QuickAnalysisContext.focus + AnomalyData.focusMode)로
  완전 분리 — 겹치는 하위 섹션 숨김 + '전체 이상 스캔 보기(종합)' 별도 버튼.

**트렌드 평소대비/향후전망 다중 tag**
- /trend 라벨 버그(첫 tag 표시→활성 tag) + 채팅 PlotChart 셀렉터 도입.
- 백엔드 `_compute_comparison_map`: tag별 자기 행 필터 + 독립 커넥션 → comparison_map.
  첫 tag 편향·다중 tag 데이터 혼입 버그 동시 해결.

**이상탐지 스캔 태그 필터 — 설정값 제외**
- z-score 스캔이 '설정값'(알람 임계치, Analog Input) 을 이상으로 잡던 문제 →
  datainfo NOT LIKE '%설정%' 추가(기존 적산 필터에 이어). 5개 이상탐지 인텐트 공통.
- 시설별 이상 분포 '영향 센서 N개' vs 표시 3개 불일치 → '외 M개 더' 표기.

**계통도(FlowDiagramMap) 클릭 확대 복원** — flyTo essential:true(reduced-motion 대응).

관련 사양: slm-api-contract-final(교차검증·설비장애·스캔 필터), trend-comparison-spec
§7.7, flow-diagram-mode-spec §7.15/7.16. 상세 커밋: `docs/dialog_log/2026-07-11.md`.

### 완료 (2026-07-10 — 프로덕션 배포 경로 + 외부/DDNS 리로드 근본해결 + UX 반응형/팝업)

**리로드 근본해결 (E-034 재발 → 프로덕션 빌드)**
- 증상: DDNS(`dnhigh98.asuscomm.com:3000`, ASUS)/LAN IP/새 IP 접속 시 ~70~90초
  주기 전체 리로드(확대·스크롤 초기화). 원인: `next dev` HMR 웹소켓이 자기서명
  cert SAN·origin 불일치 시 재검증 실패 → stale chunk → 리로드 (E-034).
- 임시: 호스트 현재 IP(192.168.50.84/10.11) 포함 cert 재발급.
- **근본**: 프로덕션 빌드(`next build`+`next start`, HMR 없음) → IP/인증서 무관
  리로드 원천 제거. 산출물: `Dockerfile.prod`, `docker-compose.prod.yml`(전체 스택
  템플릿), `certs/Caddyfile`(HTTPS:3000 종단), `docs/deploy-production.md`.
- **무중단 프런트 교체**(현재 가동): db/backend/캐시 유지, 프런트만 prod 교체 —
  `scripts/switch-frontend-prod.sh`(재빌드=재실행)/`switch-frontend-dev.sh`(복귀).
  prod 프런트 `web_default`+alias=frontend, `slm-caddy`가 TLS 종단.
- **자동 재빌드 워처**: `scripts/watch-frontend-prod.sh`(src mtime 폴링+20s 디바운스
  → 자동 재빌드·재배포, fswatch 불요). 백그라운드 가동.
- 검증: 프로덕션 HTML HMR 클라이언트 부재, `/_next/webpack-hmr`=404, login 200,
  프록시 401(backend 도달). 사용자 DDNS 실측 확인.
- 메모리: `project_prod_frontend_reload_fix.md`, `feedback_frontend_prod_autorebuild.md`.

**채팅 인텐트·성능**
- 짧은 follow-up 지표 상속 오답 수정("배수지 총유량은?"→유출량 답 재사용)
  — is_short_followup 지표 명사 가드 (review-items §멀티턴 a-fix).
- 야간최소유량 트렌드 SSE 28초→5.7초 (사전집계 fast-path + rows 초기화 순서
  버그 + backfill), E-035. 사전집계 테이블 일 1회 자동 갱신 루프 추가.

**UX**
- 탑바 좁은 폭 메뉴 2줄 접힘 → 한 줄+폰트 축소+가로 스크롤, 빠른이동/브레드크럼
  반응형, 아이패드 브레드크럼 flex-nowrap.
- 팝업 "크게보기" 토글(공용 DialogContent expandable, 96vw×94vh 스크롤 제거).
- 로딩 스피너·진행 표지 reduced-motion 예외(동작 줄이기 PC에서도 회전).

---

### 완료 (2026-06-08 — GIS [물흐름 표시] 가시성 토글 + Migration 0074)

**배경**: 2026-05-10 사양으로 [물흐름 표시] 버튼은 마스터 토글과 독립되어 GIS 페이지에
항상 노출됨. 사이트 정책상 운영자가 노출 자체를 숨기길 원함.

**해결**: 기존 EPANET 메뉴 토글 인프라 재사용 — 신규 `menu_key='gis-flow-arrow'`.

**백엔드**:
- `slm/endpoints/epanet.py` `_MENU_REQUIREMENTS` 에 `gis-flow-arrow` 추가
  (required/recommended 빈 배열 → 데이터 품질 게이트 없음, 항상 ready)
- 동작 흐름: `_check_data_quality()` 가 enabled='N' 이면 `menus_disabled` 에 포함 →
  프론트 `isEpanetEnabled("gis-flow-arrow") === false` → 버튼 hidden.

**DB**: `db/migrations/0074_epanet_gis_flow_arrow_menu.sql`
- `tb_epanet_menu_setting` 에 region별 row seed (`label='물흐름 표시 (GIS)'`, `enabled='Y'`)
- 적용 후 R01 확인됨.

**프론트** (submodule):
- `src/app/(dashboard)/monitoring/gis/page.tsx` line 268 — [물흐름 표시] 버튼을
  `{isEpanetEnabled("gis-flow-arrow") && (...)}` 로 감쌈.
- `src/components/epanet/DataQualityCard.tsx` MENU_LABEL 에
  `"gis-flow-arrow": "물흐름 표시 (GIS)"` 추가.

**검증 (Playwright)**:
- `/admin/epanet` → "메뉴 활성화 설정" 카드에 "물흐름 표시 (GIS)" 토글 노출 (12개로 증가)
- 토글 OFF → `/monitoring/gis` 에서 [물흐름 표시] 버튼 사라짐 (button.length === 0)
- 토글 ON 복원 → 버튼 재노출

**비고**: `gis-flow` (EPANET 시뮬), `flow-deviation` (실측 유량 차이) 는 이미 기존
인프라로 동일 제어 가능 — 별도 작업 불필요.

**문서**: `docs/epanet-menu-spec.md` 변경 이력 §2026-06-08 추가.

---

### 완료 (2026-05-21 — 트렌드 비교 지표 P1) [평소 대비 / 향후 전망 통합 + 대시보드 z-score 알람 체계 정합]

**의도 압축** (사용자 정의): ① "지금 평소보다 이상한가?" ② "이대로 가면 문제 생기나?"
→ 트렌드 종류 (유량/수위/압력/수질) 와 무관한 동일 UI · 데이터 모델 (`ComparisonData`).

**백엔드** — `slm/trend_comparison.py` 신규 모듈:
- `detect_trend_kind` — intent + label 패턴 매칭 (FACILITY_TREND 도 자동 분류)
- `_hourly_pattern_baseline` — hour×weekday 평균 + 표준편차 (학습 14일, 외부 ML 없음)
- `_linear_forecast` — 최근 24샘플 slope+intercept → 24시간 외삽
- `_lookup_threshold` — trend_kind 별 임계 (수위 `zone_1_height * 0.9` / 압력 `tb_block_info.critical_pressure` / 수질 0.1 mg/L)
- 상태 판정 — `anomaly_detector.classify_z_level_by_group` 재사용 (대시보드 알람과 100% 통일, A/B/C/D 그룹 임계 적용)
- 라벨: "정상" / "주의 · 평소보다 N% ↑↓" / "이상 · 평소보다 N% ↑↓" (z 숫자 노출 X)
- `/ask` + `/ask/stream` 두 경로 모두 통합, `response_builder.py` 의 build_success_response 에 comparison 전달

**프런트** — `PlotChart.tsx`:
- `ComparisonBadge` — 평소 대비 / 향후 전망 KPI 배지 2개 (status 별 색: emerald/amber/rose)
- 토글 버튼 2개 `📊 평소 대비` / `⏱ 향후 전망` — 좌상단 헤더 영역
- ECharts overlay — baseline dashed line + ±2σ band 음영 / forecast dashed + threshold markLine
- localStorage `trend-overlay-prefs` 영속 (사용자 선호 저장)

**검증 (Playwright)**:
- "죽동 배수지 수위 트렌드" → 응답에 `comparison.trend_kind: level` ✓
- 배지: "평소 대비 / 주의 · 평소보다 19.3% ↑" + "향후 전망 / 안전 (24시간+)" ✓
- 토글 클릭 → ECharts overlay 추가 + localStorage 저장 ✓

**커밋**: `slm@?` (trend_comparison.py + ai_server.py + response_builder.py) +
`slm-dashboard@?` (PlotChart + chat.ts 타입 + chat-response-mapper) +
`web@?` (docs/trend-comparison-spec.md)

---

### 완료 (2026-05-21 — EPANET B-1 운영 검증 + flow-deviation 키 버그 fix) [B-1/B-2 첫 실측 검증]

**검증 플로우** (사용자 요청 — 시스템 진짜 작동 확인):
1. 매핑 70건 등록 (5/11 자동 제안 기반 등록 잔존) — HAS_LIVE_FLOW 충족
2. 마스터 토글 ON (이전 테스트 잔재로 11개 메뉴 OFF 상태였음)
3. INP 생성 `inject_live_demand=true&inject_live_hours=1` → **live_injected_count: 39** (가압장/블록만, 배수지 제외 — 사양 §3.3)
4. 시뮬 sim_id=17 (10.8초, valid)
5. `/flow-deviation` 호출 → **0/70 sim 매칭** (모두 dist=None)

**키 버그 발견**:
- `simulator.py:216` 가 pipe 결과를 `"start"/"end"` 키로 저장
- `endpoints/epanet.py:1038, 1199` 의 flow-deviation 이 `"start_node"/"end_node"` 로 lookup → 매칭 0건
- 좌표 변환 (lnglatToEpsg5186) 은 정상이었음 — JSON join 실패로 거리 측정도 잘못 나옴
- Fix: flow-deviation 의 key 를 `"start"/"end"` 로 통일 (시뮬 결과 호환성 유지)

**Fix 후 결과**:
- 15/70 sim 매칭 (거리 11~43m 정상 범위)
- 7건 의심 분류 (실측↑ 6 / 시뮬↑ 1)
- **남산 배수지 outflow 293 LPS vs 시뮬 0.1 LPS** — 실측 발견: 모델 demand 누락 시설
- 남은 55건 매핑은 50m 임계 초과 — 운영 환경엔 200m+ 임계 권장 (P2)

**커밋**: `slm@?` (flow-deviation 키 fix)

---

### 완료 (2026-05-11 — 보고서 담당자란 분리 + 깨진 사진 정리)

1. **`tb_report.responsible_name` 신규 컬럼** (Migration 0073) — 인쇄 본문 담당자 셀이 `author_id` (작성자 ID) 로 자동 채워지던 것을 별도 NULL 허용 컬럼으로 분리. 빈 칸 시작 + 운영자 직접 입력.
   - 백엔드: PatchReportRequest 에 responsible_name 추가, 빈 문자열 → NULL 정규화
   - 프런트: ApprovalEditor 카드 "결재란" → "담당자 / 결재란" 확장 + 인쇄 양식 화면용/PDF 새 창용 모두 교체
2. **깨진 test-* 사진 정리** — `tb_report_item.photo_urls` JSONB 배열에서 `'%/test-%'` URL 객체만 필터 제거 (5 rows UPDATE, 12 → 5 photo objects, 실제 hash 사진 보존)

**커밋 체인**: `slm@dada94d` + `slm-dashboard@31735c6` + `web@07c26e2` (담당자) / `web@8683ded` (사진 정리)

---

### 완료 (2026-05-10 — EPANET B-1/B-2 사양·구현 + 자동 제안 강화)

1. **사양 작성** — `docs/epanet-flow-injection-spec.md` (B-1) + `docs/epanet-flow-deviation-spec.md` (B-2)
2. **B-1 구현** — Migration 0071 `tb_epanet_facility_flow_map` + CRUD + auto-suggest + INP inject_live_demand 옵션 + HAS_LIVE_FLOW 게이트
3. **B-2 구현** — Migration 0072 (M008-4 메뉴) + `/flow-deviation` API + FlowDeviationAnalysis 페이지 + GisFlowDeviationLayer
4. **자동 제안 강화** — tb_facility_flow_map join 으로 신뢰도 (verified/probable/weak) + `unmapped_facilities` + `gis-facility-coords.json` 좌표 자동 채움 → 당진 R01 74건 100% 좌표 채움

**커밋 체인**: `slm@fed88f1 → 67ac64d` + `slm-dashboard@a0c1a46 → ce2cc57` + `web@d26a2e4 → 8683ded`

---

### 완료 (2026-05-10 — EPANET 메뉴 토글 정합성) [마스터 + 물흐름 분리 + 그룹 hidden]

**관리자 토글 → 사이드바·탑바·GIS 토글 즉시 반영, DB 영구 저장**

1. **마스터 스위치** — `EpanetMenuToggles` 카드 [전체 활성] / [전체 비활성] 버튼 (10 메뉴 일괄)
   - 백엔드 `PUT /admin/epanet/menu-settings/bulk`
   - `tb_epanet_menu_setting` DB 영구 저장 (재시작·재배포 후에도 유지)

2. **물흐름 화살표 별도 토글** — 새 `GisFlowArrowLayer` (옅은 회색 line + 정/역 색상 화살표만)
   - GIS 페이지 [물흐름 표시] 토글 (cyan) — **마스터 비활성에 영향 X**

3. **data-quality 캐시 → Zustand store** — `invalidateEpanetDataQuality(region)` 호출 시 모든 컴포넌트 즉시 갱신

4. **AppTopbar statusOf 필터 추가** — 탑바 모드도 사이드바와 동일하게 disabled 메뉴 hidden

5. **M003-5 GIS 관망도 dataQualityKey 제거** — 기존 메뉴이므로 EPANET 토글 영향 안 받게

6. **자식 모두 disabled → 상위 그룹 hidden** — 분석 그룹 자식 3개 모두 EPANET 이면 그룹 사라짐

**검증** (Playwright tmp/sidebar-after-master-off.png + 다수):
- 마스터 비활성 → 6 그룹 (분석 사라짐), 활성 → 7 그룹
- GIS 페이지: 마스터 OFF 시 [물흐름 표시] 1개만, ON 시 7 토글
- DB 영구성: backend 재시작 후에도 enabled='N' 유지

**커밋 체인**: `slm@dc86922` (bulk API) + `slm-dashboard@9e30317` (마스터 토글·store·필터·hidden) + `web@e4759fc`

**추가 작업 (저녁)**:
- EPANET 성능 분리 구조 검토 — 이미 Lazy import 구조 (wntr/pyproj/pyshp 모두 함수 내부 import)
  · 메모리 측정: 시작 310MB / 시뮬 1회 후 2.72GB (+2.3GB) — Python 모듈 unload 불가
  · 진정한 격리는 사이드카 분리 (단일 사이트엔 현재 구성 충분)
- EPANET 시뮬 GC 추가 (`simulator.py` run_steady_state / run_what_if 종료 시 del+gc.collect)
  · 누적 호출 시 메모리 폭증 방지
- 채팅 슬롯 컨텍스트 누수 fix
  · 사용자 보고: "경보 누적 TOP 10" 답 0건 — 이전 대화 sitename(석문)/facilitytype(배수지) 자동 적용
  · `_ALARM_PIE_INTENTS` (FACILITY_ALARM_TOP_COUNT, FACILITY_ALARM_CAUSE_DIAGNOSIS_RANK) 매칭 시 사용자 메시지에 시설 미언급이면 빈 문자열 (전체 조회)
  · 적용 위치 2곳 (`ai_server.py` line 3293 /ask + 4843 /ask/stream)
  · 검증: DB SQL 직접 → 10건 정상 (`죽동(배) 탁도계 통신이상 4397건` 1위)
- 커밋: `slm@fa4c1e6`

---

### 완료 (2026-05-09 — EPANET 시계열 누적 + GIS 통합 오버레이) [본 사이클 마무리]

**cron 시뮬 + 분석 결과 GIS 통합**

1. **시계열 누적 (cron)**
   - 백엔드: POST `/admin/epanet/sim/cron` (skip_if_recent_minutes 중복 방지) + POST `/admin/epanet/sim/cleanup?days=90&keep_min=30`
   - 가이드 `docs/operations/epanet-sim-cron.md` 신규 (launchd plist + Linux crontab + 모니터링·트러블슈팅)
   - 효과: network-aging 추세 정확도 ↑, scenario-diff 평소 기준점 풍부, replacement-candidates 일시적 vs 지속 z 구분

2. **GIS 통합 오버레이 (분석 결과)**
   - `GisLeakSuspiciousLayer` — 매핑 위치 의심 빨강 + glow / 정상 회색 + 범례
   - `GisHeadlossAnomalyLayer` — 이상 파이프 z-score 그라디언트 (황→주황→빨강) + 범례
   - GIS 페이지 [누수 의심] / [헤드손실 이상] 토글 추가 (기존 [EPANET 시뮬] 과 독립, 3개 동시 활성 가능)

3. **검증** (Playwright `tmp/epanet-gis-integrated.png`):
   - 3 토글 모두 ON → 시뮬 #14 (11882 노드) + 의심 5 + 이상 340 동시 표시
   - 우상단 시뮬 정보 / 좌하단 누수 범례 / 우하단 헤드손실 범례 정상
   - 콘솔 에러 0

**커밋 체인**: `slm@21ac4c4` + `slm-dashboard@e3e5f43` + `web@707c717`

**EPANET 본 사이클 완성** (Phase 1~6, 시계열 누적, GIS 통합 모두). 후속은 운영 데이터 입력으로 정확도 향상 + 추가 GIS 모드 (밸브/파손/교체) 단계.

---

### 완료 (2026-05-08 — EPANET Phase 3.3a) [센서 매핑 인프라 — leak-suspicious/network-aging 활성]

**Migration 0069 + 매핑 CRUD + data-quality 자동 갱신 + 프런트 UI**

1. **Migration 0069** — `tb_epanet_meter_map` (region/tag_sn/x/y/offset_m/label, UNIQUE region+tag_sn)
2. **백엔드** — `/admin/epanet/meters` GET/POST/DELETE + bulk-csv. data-quality HAS_METER_MAPPING 카운트 검사
3. **프런트** — `EpanetMeterMapping` 컴포넌트 + admin/epanet 통합
4. **메뉴 분류 변화**: ready 4 → **7** (leak-suspicious / headloss-anomaly / network-aging 활성). blocked 3 잔여 (valve/pump → Phase 4, water-quality → Phase 6)

**커밋 체인**: `slm@TBD` + `slm-dashboard@TBD` + `web@TBD`

**Phase 3.3b/c (다음)**: 페이지 실 분석 로직 (실측 vs 시뮬 차이 / 편차 시계열)

---

### 완료 (2026-05-08 — EPANET Phase 3.1/3.2) [표고+수요 입력 + IDW 보간]

**Migration 0067/0068 + 표고/수요 CRUD + 합성 그라디언트 + admin UI**

1. **Phase 3.1** (표고): tb_epanet_elevation_point + IDW 보간 + 합성 표고 (NW 30m → SE 5m). 메뉴 ready 0→4
2. **Phase 3.2** (수요): tb_epanet_demand_point + 합성 수요 (도심 1.0 → 외곽 0.05 LPS). flow ±0.02 → ±20 LPS
3. 우선순위 명확화 — 합성 옵션이 운영자 입력보다 우선 (시연 모드)
4. simulator.py: junction.elevation_m 시뮬 응답 포함 (data-quality 정확도 향상)
5. 검증: artifact #14 / 시뮬 #12 — 압력 20.01~41.66m, flow -11.36~+20.24 LPS

---

### 완료 (2026-05-07 — EPANET Phase 2.7) [메뉴 뼈대 + 데이터 품질 게이트]

**10 메뉴 등록 + 데이터 품질 API + DataQualityCard 3 단계 UX + 사이드바 점**

1. **Migration 0066** — tb_menu 10건 (M003-9/10, M006-4~7, M008+M008-1~3) + tb_auth_menu 20건
2. **백엔드** — `GET /admin/epanet/data-quality` 9 항목 체크 + 메뉴별 ready/warning/blocked 분류
3. **프런트**
   - `DataQualityCard` 컴포넌트 (✅ Ready / 🟡 Warning + [참고용] 워터마크 / 🔴 Blocked + [관리 페이지로 이동])
   - `EpanetMenuPlaceholder` + 9 placeholder 페이지 (/monitoring/{leak-suspicious,headloss-anomaly}, /crisis/{valve-impact,pipe-break,pump-control,scenario-diff}, /analysis/{replacement-candidates,network-aging,water-quality})
   - 사이드바 amber/red 점 (`useEpanetDataQuality` 훅 + `MENU_DATA_QUALITY_KEY` 매핑)
   - sidebar-menus.ts SidebarMenuChildDef 분리 + 9 fallback
4. **Playwright 검증** — Blocked/Warning 카드 시각 확인, 콘솔 에러 0
5. **분류 결과 (현재 데이터)**: warning 5 / blocked 5 / ready 0 — 의도대로 모든 신규 메뉴가 데이터 부족 상태로 표시되어 운영자에게 명확히 안내됨

**커밋 체인**: `slm@TBD` + `slm-dashboard@TBD` + `web@TBD`

---

### 완료 (2026-05-06 — EPANET Phase 2.6 후속) [흐름 방향 정합성]

**pipe direction 정렬 + reservoir snap + 균등 demand → 의미 있는 화살표**

1. **pipe start = 배수지 가까운 끝점** (`inp_converter.py`)
   - SHP line direction 이 임의로 그려져 있어 화살표 방향이 무의미했던 문제 해결
   - reservoir 좌표 미리 로드 후 pipe 의 첫·끝점 중 reservoir 최단 거리 더 짧은 쪽을 start, vertices 도 같이 reverse
2. **reservoir snap** — 200m 이내 가장 가까운 송수관 끝점으로 흡수
   - reservoir SHP 가 송수관 끝점과 미세하게 어긋나 별도 component 로 빠지던 문제 해결
   - 10/15 reservoir snap 성공 (나머지 5는 200m 이상 떨어짐)
3. **default_demand_lps 0 → 0.1** — 균등 demand 부여로 정수상태에서도 의미 있는 flow 발생
   - GenerateRequest 에 파라미터 추가 (운영자 조정 가능)
   - Phase 3 에서 계량기 기반 실측 demand 로 대체 예정
4. **검증**: artifact #9 → 시뮬 #7 — flow ±3.39 LPS / pressure 49.959~50.0m / 정류 82 / 역류 42 / 거의 0 7 (정류 우세 = 수원→소비처 방향 정합)

**커밋 체인**: `slm@TBD` + `web@TBD`

---

### 완료 (2026-05-05 — EPANET 수리 시뮬레이션 Phase 2.6) [GIS 페이지 오버레이 + 토글]

**좌표계 EPSG:5186 확정 + pyproj 변환 + GIS 페이지 시뮬 오버레이 + [EPANET 시뮬] 토글 버튼**

1. **좌표계 EPSG:5186 확정** — 당진시 SHP BBOX 검증 (5종 한국 좌표계 후보 비교)
2. **백엔드** (`slm/`)
   - `requirements.txt` — pyproj>=3.6.0
   - `epanet/simulator.py` — `_get_transformer()` lazy + `_to_lnglat()` 헬퍼, `SimulationResult.bbox_lnglat` 추가, junction/reservoir 에 `lng/lat`, pipe 에 `vertices_lnglat` 추가
   - `endpoints/epanet.py` — simulate 응답에 `bbox_lnglat` 포함
3. **프런트** (`slm-dashboard/`)
   - `components/gis/GisEpanetSimLayer.tsx` 신규 — visible=true 시 최신 success 시뮬 자동 fetch + MapLibre 3 layer (노드/파이프/배수지) + 압력/유량 paint expression
   - `monitoring/gis/page.tsx` — `showEpanetSim` state + [EPANET 시뮬] 토글 버튼 (cyan, 상단 툴바) + `<GisEpanetSimLayer />` 통합
4. **검증** — bbox UTM [156280, 465458, 194003, 494896] → WGS84 [126.51, 36.79, 126.93, 37.05] (당진시 영역 정확)
5. **사양**: `docs/gis_plan.md` Phase 2.6 절 + `docs/feature-spec.md` §18-A.7 (Phase 3 §18-A.8 로 이동)

**커밋 체인**: `slm@TBD` + `slm-dashboard@TBD` + `web@TBD`

---

### 완료 (2026-05-04 — EPANET 수리 시뮬레이션 Phase 2.5) [시각화 + GIS 밸브 심볼]

**다중 vertex 보존 + 시뮬 응답 좌표 포함 + SVG 시각화 컴포넌트 + GIS 밸브 SVG 심볼**

1. **INP `[VERTICES]` 섹션** — PolyLine 첫·끝점 외 중간 점 보존 (지도 표출 굴곡 정확도)
2. **시뮬 응답 좌표 포함** — `simulator.py` 가 wntr.WaterNetworkModel 의 노드 좌표·파이프 vertex·배수지 좌표·bbox 를 응답에 포함, `tb_epanet_simulation_result.result_data` 에도 저장
3. **`EpanetSimulationCanvas` SVG 컴포넌트** (`slm-dashboard/src/components/epanet/`)
   - UTM-K 좌표 자체 viewBox (proj4js 등 변환 라이브러리 의존 없음)
   - 노드: 압력 색상 히트맵 (HSL 240→0)
   - 파이프: polyline + 정류(청록)/역류(주황) 색상 + 굵기 비례
   - 배수지: 청록 사각형
   - 호버 툴팁 + 범례
   - `/admin/epanet` 페이지 시뮬 결과 카드에 통합
4. **GIS 밸브 SVG 심볼** — `GisShpLayer` 가 `layerDef.icon` 무시하던 버그 수정. SVG → Canvas → `addImage` → MapLibre symbol layer. 줌별 0.45x ~ 1.4x 스케일. 5종 밸브 (제수/경계/지수/이토/공기) + 유량계/소화전/배수지/가압장 심볼 표출
5. **사양**: `docs/gis_plan.md` Phase 2.5 절 + Phase 3 계획 / `docs/feature-spec.md` §18-A.6/§18-A.7

**커밋 체인**: `slm-dashboard@ce44b1a` (밸브) → `web@cbafb63` / `slm@TBD` + `slm-dashboard@TBD` + `web@TBD` (Phase 2.5)

---

### 완료 (2026-05-04 — EPANET 수리 시뮬레이션 Phase 2) [관망 고도화 — wntr 동작]

**Migration 0065 + wntr 설치 + 정상상태 시뮬레이션 엔드포인트 + 프런트 [시뮬] 버튼**

1. **wntr 라이브러리 설치** — Dockerfile build-essential 추가 + backend 이미지 재빌드 (wntr 1.x + pyshp 3.x)
2. **DB Migration 0065** — `tb_epanet_simulation_result`
   - sim_id/artifact_id (FK CASCADE)/sim_type/status + 수치 요약 6 컬럼 (min/max/avg pressure_m, min/max flow_lps, node/link count) + result_data JSONB + duration_ms
   - region 멀티테넌시 + 인덱스 2종 (artifact + region)
3. **백엔드** (`slm/epanet/`)
   - `simulator.py` 신규 — `run_steady_state(inp_path)` 정상상태 시뮬레이션
   - ARM64 환경 폴백: EpanetSimulator 미가용 시 `WNTRSimulator` 자동 사용
   - PDD(Pressure Driven Demand) 모드 + 가장 큰 connected component 자동 추출 (`_isolate_largest_component`)
   - 노드 좌표 정밀도 4자리 → 0자리 (1m 단위 병합) — disconnected components 17→15
   - `endpoints/epanet.py` — 시뮬 3 엔드포인트 추가
     · POST `/admin/epanet/inp/{id}/simulate`
     · GET  `/admin/epanet/inp/{id}/simulations`
     · GET  `/admin/epanet/sim/{sim_id}`
4. **프런트** — 산출물 표 행에 [시뮬] 버튼 (Activity icon, cyan) + 시뮬 결과 미리보기 카드 (4 KPI: 노드/링크/압력 범위/평균 압력 + 유량 범위/실행 시간)
5. **검증** — artifact #3 (송수관 132건, 1m 병합) → 시뮬 #3 — 노드 128 / 링크 131 / 압력 50.0m / 유량 ±0.0205 LPS / 108ms
6. **사양**: `docs/gis_plan.md` Phase 2 절 추가 + Phase 2.5/Phase 3 계획 / `docs/feature-spec.md` §18-A.5/§18-A.6

**Phase 2.5 후속**: 다중 vertex 보존 / GIS 시각화 오버레이 / 펌프·밸브 SHP

**커밋 체인**: `slm@TBD` + `slm-dashboard@TBD` + `web@TBD`

---

### 완료 (2026-05-04 — EPANET 수리 시뮬레이션 Phase 1) [관망 고도화]

**Migration 0064 + 백엔드 모듈 + 프런트 관리 페이지 — On/Off 토글 동작**

1. **DB Migration 0064** (`db/migrations/0064_epanet_phase1.sql`)
   - `SITE_SETTING.EPANET_ENABLED` (default 'N' — opt-in)
   - `tb_epanet_artifact` (region 멀티테넌시, 변환 산출물 메타)
   - `tb_menu M100-12 EPANET 시뮬레이션` + MASTER/ADMIN 권한

2. **백엔드 신규 모듈** (`slm/epanet/`)
   - `__init__.py` — 활성화·wntr 가용성 체크
   - `shp_reader.py` — pyshp 기반 경량 SHP 스캐너 (geopandas 미사용, .cpg 자동 인코딩 감지 + EUC-KR/CP949 폴백)
   - `inp_converter.py` — SHP → EPANET 2.2 .inp 텍스트 직접 생성 (wntr 의존 X, validate_with_wntr 옵션)
   - `endpoints/epanet.py` — status/scan/inp/generate/list/download/delete (6 엔드포인트, 토글 OFF 시 503)
   - `endpoints/admin.py` — 사이트 설정 GET/PUT 에 `epanet_enabled` 추가
   - `requirements.txt` — wntr+pyshp 추가
   - `Dockerfile` — build-essential 추가 (다음 이미지 빌드 시 wntr 자동 설치)

3. **프런트** (`slm-dashboard/`)
   - `admin/site-settings/page.tsx` — EPANET 토글 카드 (Waves icon, cyan)
   - `admin/epanet/page.tsx` 신규 — 환경 진단·SHP 스캔·INP 생성·산출물 표
   - `sidebar-menus.ts` — M100-12 fallback 등록

4. **검증**
   - 송수관 132건 + 배수지 15건 → 노드 131 / 링크 132 / 배수지 15 / 26KB .inp 생성
   - 토글 OFF 시 모든 `/admin/epanet/*` 엔드포인트 503 (게이팅 정상)
   - 사이트 설정 GET/PUT 에 `epanet_enabled` 정상 반영

5. **사양**: `docs/gis_plan.md` (Phase 1 결과 절 추가) / `docs/feature-spec.md` §18.4 + §18-A

**Phase 2 후속**: wntr 검증 활성화 (Docker 이미지 빌드 후) / 다중 vertex 보존 / 펌프·밸브 SHP 반영 / GIS 시각화 오버레이 / 시뮬레이션 실행

**커밋 체인**: `slm@3b2be9f` + `slm-dashboard@63b4f4b` + `web@b18eda3`

---

### 완료 (2026-04-22 ~ 23 — 관리/UX + 채팅 SSE 안정화 + 카탈로그 보강)

**1. AI 런타임 파라미터 DB 영속화** (migration 0055/0056)
- `tb_comm_code.comm_val` 컬럼 추가 → 수치·문자열 설정 영속 경로 확보
- `AI_NUM_CTX/AI_TEMPERATURE/AI_TIMEOUT/AI_MODEL` 4 행 시드 +
  `_AiRuntimeSettings.load_from_db()` + admin.py PUT UPSERT

**2. 고장 추이 차트 다각화 + 밀도 반응** (#152/155/157/158)
- Backend `/fault-trend-by-category` granularity(year/month) + equipment_count
- Frontend: 연/월 · 건수/고장률% · 범례 on/off · 상단 배치 · 밀도 반응 layout

**3. 채팅 피드백 집계 대시보드** (#159)
- `GET /chat/feedback/stats` — totals 6 + by_intent + weekly
- `/admin/chat-feedback` 상단 KPI 6 + 주간 스택 + 인텐트 오답 순위

**4. 6 페이지 섹션 접기/펴기 통일** (#160/161/162)
- `components/ui/collapsible-section.tsx` — CollapsibleSection + usePersistedCollapse
- alarm / alarm-calendar / equipment-health / network / crisis (현황+이력)
- SectionHeader(제목 + outline `[^ 접기]`) 패턴으로 전 페이지 시각 통일

**5. 경보 확인 기능** (#163/164)
- 현황 탭 onConfirm 미전달 버그 수정 + UI amber outline `[⚠ 확인]` 버튼
- **전체 확인 일괄** `[✓✓ 전체 확인 (N건)]` — Promise.all 병렬

**6. 용수흐름 상단 탭 재배치** (#153)
- 흐름도·계통·계통도 → 계통·흐름도·계통도 + 아이콘(Layers/Waves/Network) 중복 해소

**7. 채팅 SSE 안정화 버그 수정 — 에러 E-030/031/032/033**
- **E-030** `TAG_DAILY_MISSING_SUMMARY` placeholder `{sitename}` 원문 노출 →
  청크 핸들러 render_answer_template 호출 추가
- **E-031** 알람원인 순위 질의 무한 대기 → `_extract_alarm_filter` import +
  `_ALARM_FILTER_RULES` 8 카테고리 정의
- **E-032** 배수지 공급량 NameError → AST 스캔으로 `_execute_reservoir_supply_query_with_conn`
  + `_extract_alarm_level` 추가 import
- **E-033** 죽동 수위 트렌드 "카탈로그 미등록" → migration 0057 자동 백필 6 배수지

**8. 펌프 가동 시각화 + 배수지 수질값 통일** (3 뷰)
- flow_realtime.py 펌프 SQL 인버터+직기동 중복 카운트 수정 (삼봉 10→5, 복운 4→2 등)
- water_quality 신규 집계 (탁도/잔류염소/pH/전기전도/온도)
- 계통도/흐름도/GIS 관망도 **동일한 Fan 회전 아이콘** 으로 통일
  (running=빨간 animate-spin, 정지=회색). 배수지 수질 한 줄 라벨로 표시

**9. FlowNodeTrendPanel 트렌드 접기/펴기**
- 유량/수위/수압 각 트렌드 독립 접기. 카드 외부 헤더 행에 토글 배치
  (큰 숫자 가리지 않게), localStorage 개별 유지

**검토 중 (미적용):**
- 합덕3 소블록 압력 현황 SQL 13초 — `fn_pressure_avg_summary` CTE 최적화 권장
- 석문 가압장 알람 오늘 11초 — 벡터 검색 threshold 조정
- "순위→수위" 입력 보정 오작동 — 문맥 고려 필요
- 트렌드 카탈로그 fallback — 미등록 시 tb_tag_info 직접 매칭
- 펌프 집계 자동화 — tb_equipment_info 기반 매핑 테이블 (1 설비:N 태그) 전환 검토

---

### 완료 (2026-04-20 — 대형 작업 묶음) [UX 일관성 + 데이터 모델]

**1. Rule 9 — 전체 네트워크 다운 시 원인 병기** (`slm@3ea6d02`)
- UTM 전 구간 장애 + LTE 90%+ 다운 스냅샷은 UTM 장애·관제 호스트
  네트워크 문제 둘 다 가능성. `_fallback_summary` + LLM 프롬프트 분기

**2. 채팅 응답 디자인 일관화 (27 인텐트 전부)** — 대형 작업
- 일반현황 벤치마크(아이콘+라벨+값+pill) 로 전 인텐트 통일. 내용
  100% 보존 원칙 (`memory/feedback_preserve_answer_content.md`)
- 일반/운영/압력/수위/알람/주소/네트워크/평균사용량/적산/공급시간/태그
  최신값 + 분석형 5(마커→pill)
- 부가: 숫자+단위 cyan regex 확장 / 분석형 foreground 색 / 라벨 폭
  `w-48 break-keep` / bullet pill 렌더
- E2E 20 테스트 100% compliant (초기 10%)

**3. 네트워크 경로 일방향 경계 모델링** (migration 0052)
- `tb_network_link.bidirectional` 플래그 + view 재귀 CTE 필터
- 잘못된 UTM→업무망 직결 3건 삭제
- "일방향 경계로 역방향 모니터링 불가" 안내 표시

**4. 사진 첨부 기능 정비**
- Scenario 1a 유실 버그 수정 (`slm-dashboard@d7a0673`) —
  `chat-store.pendingPhotoUrls` state
- 등록 사진 UI 추가 (TaskListDialog, EquipmentTimelineDialog)

**5. 설비 건강성 대개편**
- 4탭 구조: 개요 / 장애·조치 이력 / 내용연수 교체 권고 / 교체 후보
- 장애·조치 이력 탭 신규 — 리스트 + 필터 + 사진 lightbox + 분포 차트
- 교체 후보 분석 HH/LL 필터 (`slm@81c8005`) — 수위계 HH 오탐 제거
- 내용연수 기반 교체 권고 섹션 (migration 0053) — 구조 완비,
  설치일 미조사(295 설비 전부 NULL)

**6. 메뉴 트리 개편** (migration 0054)
- 트렌드 상위 그룹 → 배수지/가압장/블록/사용자 트렌드 4 하위
- URL 경로 100% 유지

**후속 작업 (미착수)**:
- 설치일(commissioned_at) 입력 UI — 내용연수 기능 활성화 전제
- 엑셀 일괄 업로드 경로
- 고장률 추이 차트 / LCC 경제성 분석 (장기)

### 완료 (2026-04-19 — 장애 분류 정책 + 교체 메타 P7) [데이터 신뢰도]

**배경:**
- "통신 알람으로 뜨는것으로 내가 볼때는 전부 이상으로 하고, 사용자가 현장에서 고장
   등록을 하면 그것을 고장으로 처리하는게 맞지 않을까? 통신 알람만으로 정확한 고장을
   인지하기는 어려울거같아"
- "교체시에는 교체 일자, 교체 설비, 현장명, 제품명, 제조사 등의 기록이 있어야할거같아"

**정책 명문화 (`docs/fault-category-policy.md` + 메모리)**
- fault_category 4값 명확한 정의 (고장/이상/교체/점검)
- 알람은 기본 "이상", 현장 확인된 경우만 "고장"
- 통신·네트워크 키워드는 자동 "이상" 강제 (regex 휴리스틱)
- 알람→장애 자동 변환 경로 없음 원칙 유지

**교체 메타 (P7, migration 0050):**
- `tb_task_master.replacement_info JSONB` 신규 (manufacturer/model/serial/
  old_*/replaced_at)
- `chat_fault_record._extract_replacement_info` — 자연어에서 "제조사 / 모델
  / S/N" regex 추출
- `build_fault_draft` 에 `replacement_info` 파라미터 + 자동 추출+명시 병합
- `/chat/fault/confirm` INSERT 컬럼 매핑
- equipment_health `/tasks` + afc `/equipment-timeline` 에 replacement_info
  포함

**프런트 (`slm-dashboard@9110dce`):**
- FaultRecordConfirmCard: fault_category='교체' 전용 violet 박스로 제조사/
  모델/시리얼 표시. 자동 추출 실패 시 보완 안내
- TaskListDialog: 교체 행 내용 셀에 메타 요약(violet)
- EquipmentTimelineDialog: 교체 건 메타 표시

**E2E 검증 (`p7-replacement-info.png`):**
- 채팅 "행정 배수지 PLC 교체 제조사 LS 모델 XGB-XBCH S/N 20240419-001" →
  draft.replacement_info 3필드 추출 → DB task#29 저장 → 교체/점검 KPI
  Dialog 에서 "LS · XGB-XBCH · 20240419-001" violet 표시

**커밋:** `slm@40c67af` + `slm-dashboard@9110dce`

### 완료 (2026-04-19 — 설비 타임라인·KPI 드릴다운·채팅 조치완료 P6) [조치 이력 경로]

**배경:** 장애 기록 후 **조치 이력 확인**과 **조치 완료 등록** 경로가 공백.
설비 단위 타임라인 Dialog + KPI 드릴다운 + 채팅 자연어 조치완료 3 경로 도입.

**백엔드 (`slm@bf9884b`)**
- `chat_fault_record.py` — `RESOLVE_KEYWORDS` 상수, `/chat/fault/resolve/draft`
  (자연어→진행중 task 자동 탐색), `/confirm` (UPDATE resolved_at/resolved_by/
  status='완료'/resolution_note), `/direct` (task_id 지정 버튼용)
- `alarm_fault_correlation.py` — `/equipment-timeline?sitename=&...` 알람+
  고장+조치 이벤트 최신순 병합
- `equipment_health.py` — `/tasks?status=&fault_category=` KPI 드릴다운 목록

**프런트 (`slm-dashboard@db0efbb`)**
- `EquipmentTimelineDialog` — 교체 후보 분석 행 클릭 → 알람/보고/조치 시계열
  병합. 진행중 task 인라인 "조치 완료" 입력창+버튼 (`/resolve/direct`)
- `TaskListDialog` — equipment-health 상단 KPI 6개(총 장애/진행중/완료/고장/
  이상/교체) 클릭 드릴다운. 각 행 인라인 완료 처리
- `FaultResolveConfirmCard` + `isResolveIntent` + `use-chat-submit` 분기 —
  "신평 배수지 PLC 조치 완료했어" 같은 자연어 → 최근 진행중 task 자동
  매칭 + 조치 내용 입력 + 확정
- API: `fetchEquipmentTimeline`, `fetchHealthTasks`, `createResolveDraft`,
  `confirmResolveDraft`, `resolveTaskDirect`

**E2E 검증 (3 스크린샷):**
- `p6-task-list-dialog.png` — "진행중" KPI 클릭 → 14건 Dialog
- `p6-equipment-timeline.png` — 죽동 배수지 네트워크 행 → 알람 3,812건 시계열
- `p6-chat-resolve.png` — "신평 배수지 PLC 조치 완료했어" → task #28 확인 카드 →
  DB 반영 확인 (status=완료, resolved_at, resolution_note)

**원칙 유지:** 자동 연계·해제 없음. 조치 완료는 사용자 명시 등록만
(`memory/feedback_no_auto_alarm_link.md`).

### 완료 (2026-04-19 — 설비 교체 후보 분석 P5-rev) [관점 전환]

**배경:** 사용자 피드백 — 네트워크 LTE 모뎀은 한 설비에서 짧은 발생/해제
알람이 수천 번 반복. linked_alarm 1:1 매칭 지표는 의미 없음. 실제 의사결정
지표는 "**설비별 알람 빈도 + 조치 전후 재발률 → 교체 후보**".

**변경:**
- backend `slm@b8d47f2` — 기존 4 엔드포인트 폐기, `/equipment-status` 단일
  엔드포인트로 교체. 설비 그룹(sitename+facilitytype+equipmenttype) 단위
  집계 + 4상태 분류(needs_action/in_progress/replacement_candidate/resolved).
  파라미터: days/min_alarm/recurrence_cnt/recurrence_rate.
- frontend `slm-dashboard@88e4f87` — AlarmFaultCorrelationSection 전면
  재작성. KPI 5개(교체 후보·조치 필요 색상 링 강조) + 설비 상세 테이블
  (상태/시설·설비/알람/지속/보고/최근조치/조치후재발/재발률/최근알람) +
  필터(기간/최소알람/상태/키워드).

**실데이터 (90d, min_alarm=10):**
- 13 설비 모두 `needs_action` (현장이 linked_alarm 미사용 사실이 명확히 드러남)
- 죽동 배수지 네트워크 3,806건 / 134d 지속 최다 — 실제 교체/점검 1순위

**원칙 유지:** 리포트 전용. 자동 해제·상태 변경 없음
(`memory/feedback_no_auto_alarm_link.md`).

### 완료 (2026-04-19 — 알람 ↔ 장애 매칭 분석 P5) [설비 건강성 확장]

**배경:** 시스템 자동 알람(tb_equipment_alarm_report)과 현장 수동 장애 기록
(tb_task_master) 의 상관 분석이 없었음. 알람 정확도·장애 커버리지·미매칭
오탐·미검지 후보를 리포트로 제공. 자동 해제·연계는 이전 결정대로 금지
(`memory/feedback_no_auto_alarm_link.md`).

**백엔드 (`slm@40cd7f9`)**
- `endpoints/alarm_fault_correlation.py` 신규 — 4 엔드포인트
  - `/summary` — 5 KPI (알람/장애/매칭 + 정확도/커버리지 + 미매칭)
  - `/matrix` — 설비유형별 (알람, 장애, 매칭) 교차표
  - `/lag` — 알람→장애 기록 시간차 히스토그램 (5 bin) + p50/p95/평균
  - `/unmatched?kind=alarm|fault` — Top 20 목록 (오탐·미검지 후보)
- 조인 키: `task_master.linked_alarm_start+tagsn ↔ alarm.start_time+tagsn`

**프런트 (`slm-dashboard@fecad28`)**
- `api/alarm-fault-correlation-api.ts` 신규
- `AlarmFaultCorrelationSection` 컴포넌트 — KPI/교차표/지연분포/미매칭탭
  (기간 선택 30/90/180/365일)
- `/monitoring/equipment-health` 하단에 섹션 삽입 (border-t 구분)
- 사이드바 메뉴 등록 — 모니터링 > "설비 건강성" (M003-8)

**실데이터 인사이트 (90일):**
- 알람 5,738 vs 장애 14, 매칭 0 — 현장이 linked_alarm 필드를 미사용
- 네트워크 알람 3,924 vs 장애 0 — 오탐·센서 과민 가능성
- PLC 알람 0 vs 장애 5 — 미검지 영역
- UPS/가압펌프 비율 편차

**디자인 원칙:** 리포트 전용 — 자동 판단/해제 없음. 담당자가 미매칭 Top
검토 후 센서 임계값/알람 로직 개선에 참고.

### 완료 (2026-04-19 — VisionAdviceCard fault_cases 섹션 렌더) [P3 후속]

P3 백엔드가 채운 `DiagnoseResponse.fault_cases` 를 진단 카드 UI 에 표시.

- `types/chat.ts` — `VisionFaultCase` 인터페이스 + `VisionAdvice.fault_cases[]`
- `VisionAdviceCard.tsx` — "관련 고장 케이스 · N건" 섹션 (Wrench 아이콘).
  매뉴얼 인용(BookOpen)보다 앞에 배치. 케이스당 case_id + equipment_type Badge +
  brand/model + severity Badge + score(3자리) + 증상/원인/조치 3단 라벨드 행.
- **E2E** (`p3-vision-card-fault-cases.png`): PLC ERR LED 쿼리에 3건 노출
  (#1 PLC 0.622 / #4 모뎀 0.478 / #3 RTU 0.459)

커밋: `slm-dashboard@dad3e31`

### 완료 (2026-04-19 — 고장 진단 케이스 DB + RAG 통합 P3) [B+C 병행]

**배경:** 사용자 추천 결정 "A+B 병행 + C 엑셀 IMPORT/EXPORT 포함". 진단 품질의
본질은 **데이터** 이므로 구조화된 케이스 DB + 관리자 UI + 엑셀 업로드로 축적
경로 확보. A안(매뉴얼 고장 섹션 재청킹)은 운영 중 필요 판단 후 진행으로 분리.

**구현:**

Migration (`web@ece49d4` / `0048_fault_case.sql`)
- `tb_fault_case` — case_id/equipment_type/brand/model/symptom/cause/action/
  severity/reference_url/notes/is_active/created_by/created_at/updated_at +
  embedding_key. 화이트리스트/심각도 CHECK + UNIQUE(equipment_type, brand, model,
  symptom). updated_at 자동 갱신 트리거.

백엔드 (`slm@8b8cf8e`)
- `endpoints/fault_case.py` 신규 — CRUD + 임베딩 + 엑셀 IMPORT/EXPORT
  - snowflake-arctic-embed2 (Ollama) — symptom+cause+action 결합 텍스트 →
    NPZ 저장 (`data/fault_case_embeddings/fault_case_<id>.npz`)
  - `/fault-cases/template` + `/fault-cases/export` + `/fault-cases/import`
    (openpyxl, 헤더 검증 + 중복 skip/overwrite + 행별 에러 리포트)
- `vision_agent.py` — `_FaultCaseIndex` + `_retrieve_fault_cases()` +
  `POST /vision/fault-cases/reload` (CRUD 후 즉시 반영)
- `DiagnoseResponse.fault_cases[]` — 매뉴얼 RAG 와 별개 top_k=3
- requirements.txt: +openpyxl

프런트 (`slm-dashboard@f29520e`)
- `api/fault-case-api.ts` — CRUD + importFaultCases (multipart) +
  exportFaultCasesUrl + templateFaultCasesUrl
- `/setup/fault-cases/page.tsx` — 테이블 + equipment_type 필터 + 키워드 검색 +
  신규/수정 다이얼로그 + 엑셀 업로드·다운로드·템플릿 버튼 3개
- sidebar-menus.ts: "고장 진단 케이스" 메뉴 추가

예제 (`docs/examples/fault_case_template.xlsx`) — 5건 샘플 (PLC/인버터/RTU/모뎀/UPS)

**검증:**
- IMPORT 5건 (PLC 1건은 수동 create 했으므로 중복 skipped) → 총 5건
- EXPORT OK (xlsx 유효)
- 진단 테스트: "PLC ERR LED 빨강 점등" 쿼리 → 3 cases hit
  (#1 PLC/LS/XGB-XBCH score=0.571 / #3 RTU 0.468 / #4 모뎀 0.445)
- 관리자 UI 렌더 OK (5건 테이블 + 액션 버튼 + equipment_type Badge 통계)

**남은 것:**
- A안 (매뉴얼 고장 섹션 재청킹) — 운영 중 RAG 품질 평가 후 필요 시 진행
- P4 (수질계/압력계 매뉴얼 PDF 추가) — 매뉴얼 확보 경로 결정 필요

### 완료 (2026-04-19 — 사진만 업로드 시 용도 재질의 P2) [시나리오 1-a]

**배경:** 사진만 첨부 (텍스트 없음) 시 기존엔 무조건 VLM(46s) 호출 → 낭비.
용도 재질의 UI 추가로 사용자가 "고장 등록 / 진단 / 참고" 선택 → 해당 플로우만 수행.

**구현:**

백엔드 (`slm@753d76c`)
- `vision_proxy.py` — `is_photo_only` 판정 (user_question 빈 문자열/2자 미만).
  PHOTO_CLARIFY intent 즉시 응답 (VLM 스킵, 0ms). photo_urls 그대로 보존.
- 신규 `POST /chat/photo-action` — IntentClarifyCard 버튼 액션:
  - `fault` → `build_fault_draft` 호출 (photo_urls 포함)
  - `diagnose` → `vision_agent /vision/diagnose` 호출 (photo_urls[0])
  - `reference` → 단순 확인

프런트 (`slm-dashboard@db447ff`)
- `AiServerResponse.photo_clarify` + `PhotoClarify` 타입 + `bot.photo_clarify`
- `chat-response-mapper.ts` — photo_clarify summary 대체 + 전달
- `api/fault-record-api.ts` — `postPhotoAction({action, photo_urls, ...})`
- 신규 `IntentClarifyCard` — 썸네일 + 3버튼 (고장등록/상태진단/참고용) +
  자유 텍스트. 버튼 클릭 후 결과는 카드 **내부에서** `FaultRecordConfirmCard` /
  `VisionAdviceCard` 로 **치환 렌더** (기존 메시지 자리 유지)
- `BotMessage` + `ChatMessageArea` — photoClarify prop 전파

**E2E 검증 (브라우저):**
1. 사진만 업로드 → IntentClarifyCard 표시 (썸네일 + 3버튼, 0ms 응답)
2. 텍스트 "난지마을 배수지 PLC 고장" + "고장 등록" 버튼 →
   동일 위치가 FaultRecordConfirmCard 로 교체 (equipment 자동 매칭, 사진 유지)

**다음:** P3(시나리오 1-b RAG 확장 — D1 결정 필요) → P4(수질계/압력계 매뉴얼).

### 완료 (2026-04-19 — 사진+고장 등록 시나리오 P1) [채팅 통합 플로우]

**배경:** 채팅 사진 업로드 시 4가지 시나리오 정의 (사진만/사진+등록/등록만/멀티턴).
기존엔 사진이 있으면 무조건 `VISION_DIAGNOSE` 로 라우팅돼 "사진+등록" 플로우가 없었음.

**사양 정의:** `docs/chat-photo-upload-scenario-spec.md` 신규. D1~D5 결정 기록.

**P1 범위 (시나리오 2·3):**

백엔드 (`slm@e344be0`)
- `chat_fault_record.py` — `build_fault_draft()` 공용 헬퍼, `photo_urls[]` 필드,
  `/confirm` 의 `tb_task_master.photo_urls` 컬럼 매핑 (migration 0045 기존),
  신규 `POST /chat/fault/attach-photo` (multipart) — 기존 pending 에 사진 추가
- `vision_proxy.py` — `_detect_fault_intent()` + `_fault_branch()` 로
  `/ask/multimodal/stream` 첫단에 FAULT 의도 판정 → VLM 대신 fault_draft emit

프런트 (`slm-dashboard@5517ca9`)
- `AiServerResponse.fault_draft` + `FaultDraft.draft.photo_urls[]`
- `chat-response-mapper` 에 fault_draft 전달
- `attachFaultPhotos()` API + `FaultRecordConfirmCard` 썸네일 그리드 +
  "사진 추가" 버튼 (최대 3장)

인프라 (`web@a2cc44c`)
- `docker-compose.dev.yml` frontend 에 `./files:/data/files` 바인드 +
  `FILE_STORAGE_PATH=/data/files` env (썸네일 서빙)

**검증 (E2E 5회):** PLC 고장 / 판넬 전원이상 / UPS 교체 / 가압펌프 이상 모두
FAULT_RECORD_DRAFT 정상 라우팅. "정상이야?" 진단 회귀 VISION_DIAGNOSE OK.

**다음:** P2(시나리오 1-a 재질의) → P3(1-b RAG 확장, D1 결정 필요) → P4(수질계/압력계 매뉴얼).

### 완료 (2026-04-19 — 비전 에이전트 사진 업로드 404 수정) [E-029]

**증상:** 채팅에서 사진 업로드 시 SSE `error` 이벤트 + "비전 에이전트 호출 실패" (HTTP 404).

**원인:** backend(Docker)가 저장한 파일의 **컨테이너 내부 절대경로**(`/web/files/chat_attachments/<uuid>`)를 그대로 vision_agent(호스트 프로세스)에 전달. 호스트에선 해당 경로가 없어 `_load_image_base64` 404. 추가로 docker-compose.dev.yml에 `chat_attachments`/`facility` 호스트 바인드 마운트 자체가 없었음.

**수정:**
- `docker-compose.dev.yml` backend — volumes `./files:/data/files` 추가, env `CHAT_ATTACHMENT_DIR=/data/files/chat_attachments`, `FACILITY_FILE_BASE_DIR=/data/files/facility`
- `endpoints/vision_proxy.py` — `_save_upload`가 `(path, url)` 반환, vision_agent엔 URL 형식(`/api/files/chat_attachments/<name>`)만 전달
- `vision_agent.py` — `CHAT_ATTACHMENT_DIR` 추가, `_resolve_image_path`에 chat_attachments prefix 핸들러, 기본 경로 1레벨 오차 수정
- 호스트 `/Users/jykim/web/files/chat_attachments` 디렉터리 신규 생성

**검증:** 실제 이미지로 `/ask/multimodal/stream` 재호출 → vision_session_id=36, VLM 46.9s 응답 정상. 호스트·컨테이너 양쪽에서 동일 파일 가시.

사양: `docs/error-management.md [E-029]`

### 완료 (2026-04-18 — Q/H/P 트렌드 패널 공용 컴포넌트 도입)

**목적:** `trend_panels.html` 레퍼런스 기반으로 용수흐름 / GIS 관망도 우측 인스펙터의 **유량(Q)·수위(H)·압력(P)** 표출을 동일한 시각 언어로 통일

**구현 (`slm-dashboard@b3e6e7a`):**
- 신규 공용 컴포넌트 `components/monitoring/MetricTrendPanel.tsx`
- variant(flow/level/pressure)별 컬러 토큰 + 서로 다른 SVG 차트
  - Flow: dash 흐름 + 끝점 펄스 링
  - Level: 수면 반짝임 + 물방울 + indicator breath
  - Pressure: 두꺼운 파이프 + 끝점 정적 펄스
- 헤더: 라벨 + Q/H/P 코드 뱃지 + 값 + 단위 + delta(24h 첫값 대비)
- 푸터: MIN/AVG/MAX + LAST 24H
- `globals.css` — 애니메이션 keyframes 10종
- Canvas 기반 SparklineChart 2곳(FlowNodeTrendPanel, GisDetailPanel) → MetricTrendPanel로 일원화

**후속 보정:**
- `slm-dashboard@92f3d23` — LIVE 뱃지 제거 + 라벨 한글화 (유량/수위/압력)
- `slm-dashboard@44f6f46` — 압력 흐르는 wave 애니메이션 제거
- `slm-dashboard@6f34bfc` — 유량 dash 흐름 속도 1.5s → 4s 완화

사양: `docs/metric-trend-panel-spec.md`

### 완료 (2026-04-18 — 실시간 계통도 불균형 엣지 색상 분절 수정)

**문제:** bracket 레이아웃의 trunk/vertical/drop 3분할 엣지 중 drop 만 불균형 색 반영 → 부모→자식 라인이 "절반 파랑/절반 빨강"으로 렌더

**해결 (`slm-dashboard@7e1e237`):**
- `FlowDiagramMap.tsx` `enrichedEdges` 에서 부모별 자식 drop 들의 최악 grade 사전 집계(`worstByParent`)
- `edge_type==="trunk"` · `"vertical"` feature 색상을 해당 최악 grade 로 전파
- GRADE_RANK: 정상<관심<주의<경고 → 공유 세그먼트 통일
- 뱃지 중복은 `edgeImbalance[imbKey]` 필터로 자동 방지 (trunk/vertical 가상 dn 자연 제외)

**범위:** FlowMonitoringGraph(Sankey)는 링크당 단일 cubic path 라 무관 → 변경 없음

사양: `docs/flow-diagram-mode-enhancement.md` §8.1

### 완료 (2026-04-18 — Tweaks 패널 + 레이아웃 분기 + 사이드바 UX 개선)

**Tweaks 패널** (`slm-dashboard@bca37cc`, `2ca47f6`)
- 우측 슬라이드 Sheet: 테마/브랜드 컬러/레이아웃 3종 설정
- 브랜드 컬러 10종 (오렌지 기본 / 앰버 / 블루 / 시안 / 틸 / 에메랄드 / 인디고 / 바이올렛 / 핑크 / 로즈 / 슬레이트)
- CSS 변수(`--primary`, `--ring` 등) 런타임 덮어쓰기 + localStorage persist

**사이드바 스타일** (`slm-dashboard@4d3c949`, `2060559`)
- hover: `bg-primary/5` + `text-primary`
- active: `bg-primary/10` + `text-primary` + `font-medium` (Claude 디자인 톤)
- 상위 드롭다운 active: `bg-primary/[0.08]` (더 subtle)

**탑바 레이아웃** (`slm-dashboard@7a486ca`)
- AppTopbar: 로고 + 가로 메뉴 + DropdownMenu 하위 + 우측 유틸
- DashboardShell: layoutMode 감지 분기 + MutationObserver 실시간 반영
- 동일 useSidebarMenus 훅 공유

사양: `docs/tweaks-layout-spec.md`

### 완료 (2026-04-18 — 설비 장애 이력 관리 P1 구현)

채팅 자연어로 설비 장애 기록 (E-025 비전 진단 확장, 태그 단위 X → 설비 단위 O)

**DB (migration 0045):**
- `tb_task_master` 확장 11컬럼 (equipment_id/equipmenttype/fault_category/severity/
  linked_alarm_start+tagsn/photo_urls/recorded_by/resolved_by/resolved_at/resolution_note/status)
- task_category에 "고장보고" 추가
- tb_chat_pending_action (채팅 멀티턴 초안, TTL 5분)
- 통계 뷰 4개: v_equipment_fault_stats / v_equipment_fault_monthly /
  v_equipment_mtbf / v_site_fault_ranking (`web@09b395d`)

**백엔드:**
- `/chat/fault/draft` + `/confirm`: 키워드 매칭 파싱 → pending_action → INSERT (`slm@eb5e185`)
- `/crisis/tasks` CRUD 확장: 고장보고 필드 INSERT/UPDATE/SELECT (`slm@346c762`)
- `/monitoring/equipment-health/*`: KPI/monthly/stats/mtbf/ranking (`slm@22a636b`)

**프런트:**
- FaultRecordConfirmCard + 채팅 통합 (키워드 감지 → 카드 → 예/취소) (`slm-dashboard@968b17b`, `5034fee`)
- /crisis/tasks 고장보고 카테고리 지원: TaskFormDialog 조건부 필드, TaskTable Badge (`slm-dashboard@93d8027`)
- /monitoring/equipment-health 설비 건강성 대시보드 (KPI + 월별바 + 시설Top + MTBF) (`slm-dashboard@d199f14`)

**E2E 검증:** "신평 배수지 PLC 고장 기록해줘" → task_id 14, 15 INSERT 성공

사양: `docs/equipment-fault-tracking-spec.md`

### 완료 (2026-04-17 — 다이어그램 상단 패널 + KPI 연동 + 라이트모드 완성)

**상단 요약 패널 + 필터 동작 (생키 UX 동기화):**
- 시설유형 범례 + 불균형/교차검증/알람/장애/통신 카운트 표시 (`slm-dashboard@229d396`)
- 배지 클릭 시 대상 노드 파란 outline 하이라이트, 나머지 dim (`e9fb342`, `851ea09`)
- 엣지/particle도 dim 처리로 생키 동일 UX (`876571f`)
- 라이트모드에서 dim 엣지 slate-400 회색으로 가시성 확보 (`b3dc210`)
- ring→outline 교체로 alarm ring과 충돌 해결 (`4cad721`)

**KPI 카드 ↔ 다이어그램 필터 연동:**
- externalFilter prop으로 KPI 카드 activeFilter와 다이어그램 필터 매핑 (`slm-dashboard@84779a5`)
- 기존 KPI 카드는 Sankey에만 연결되어 다이어그램 반응 없던 문제 해결

**라이트모드 + 기타 개선:**
- 라이트모드: 맵/노드/오버레이/엣지 완전 대응 (`slm-dashboard@6a2614d`, `fb8b8d7`)
- 박스 텍스트 가독성: slate-800 + font-medium, 아이콘 *-700 (`42eddd8`)
- 소블록 유량적산+압력: `slm@4b09297` + `slm-dashboard@a147884`
- 노드 클릭 flyTo zoom 14 포커싱 (`slm-dashboard@0f8e4f2`)
- 노드 간격 40% 축소 (`slm@116e294`)
- dev 캐시 방지 헤더 (`slm-dashboard@2937f13`)

### 완료 (2026-04-16 — 다이어그램 방향 화살표 + 알갱이 애니메이션 + GIS 데이터 동기)

- 엣지에 canvas 생성 화살표 아이콘 (symbol-placement=line, 폐쇄망 대응)
- 이동 particle 점 애니메이션: circle이 상류→하류로 이동, 방향 인식 명확 (`slm-dashboard@23fc578`)
- lg 노드 배수지: supply_time (공급가능시간/유입/유출/야간최소) — GIS 관망도와 동일 데이터
- 커밋: `slm-dashboard@ac8b19e`

### 완료 (2026-04-16 — 용수흐름도 다이어그램 레이아웃 3차 개선 [E-026])

- 좌표 충돌 수정: 13건 collision → 0건 (`slm@a72a88c`)
- 수평 compact 레이아웃: 계단식 제거, Y 75% 절감 (`slm@e686f6a`)
- 세로 병렬 배치 (최종): 형제를 같은 X, 다른 Y로 나열 → 병렬 관계 명확 (`slm@d4c5161`)
  - 이전 가로 배치는 직렬(종속) 관계로 오인되는 문제
  - 검증: 줌 11/13에서 99 markers, 0 duplicates, 0 pixel overlaps

### 완료 (2026-04-14 — 멀티모달 현장 진단 MVP P1~P5 [E-025])

- 목표: Plan(docs/ultraplan_*.html)의 워크플로우 A "채팅 멀티모달 진단" 구현. P6/P7은 별도 세션으로 분리.
- 핵심 결정:
  1. **에이전트 분리** — `vision_agent.py` 별도 FastAPI 프로세스(포트 8100)로 Zero-Hallucination 경계 프로세스 수준 강제
  2. **단일 모델 재사용** — 기존 `gemma4:26b-a4b-it-q4_K_M`이 vision capability 지원(`/api/show` 확인)으로 신규 모델 불필요, 19.7GB VRAM 1벌 공유
  3. **Proxy 재사용** — `/api/proxy/[...path]/route.ts`가 이미 multipart+SSE 지원
- 구현 (5 phase):
  - **P1**: `db/migrations/0043_vision_agent.sql` — `tb_equipment_image`/`tb_equipment_manual`/`tb_vision_session` 신규 + `tb_task_master.vision_session_id` + `tb_equipment_info.equipment_photo_url/nameplate_photo_url` 확장. `tb_equipment` → 실제 `tb_equipment_info` 매핑
  - **P2**: `slm/vision_agent.py` (~400 lines) — FastAPI 8100, `/health`/`/vision/diagnose`/`/vision/register-parse`/`/vision/manual-search`, Ollama `images:[base64]` 호출, 장비 화이트리스트, advice_text 접두어 강제, 수치 생성 감시, `num_predict=None`
  - **P3**: 매뉴얼 RAG 스텁 (PDF 없이 구조만)
  - **P4**: `slm/endpoints/vision_proxy.py` 신규 — `POST /ask/multimodal/stream` multipart + SSE 4단계(classify/extract/fetch/result), `vision_advice` 필드 격리 + `answer_text: null`, `tb_vision_session` INSERT
  - **P5**: 프론트 — `VisionAdvice` 타입 + `streamMultimodalChat` + `executeMultimodalStream` + `ChatInput` 카메라/이미지 버튼 + 썸네일 칩 + `VisionAdviceCard` (violet 테마 + 면책 푸터) + `BotMessage.visionAdvice` prop + response mapper
- 검증 (Playwright 브라우저 E2E):
  - 백엔드 curl: `/ask/multimodal/stream` SSE 4프레임 → `vision_advice.equipment_guess="LS XBCH-16MW", confidence=1.0` + `answer_text=null` + `vision_session_id=1`, 29.6s
  - DB: `tb_vision_session` row 1개 (agent_response JSON 저장 완료)
  - 브라우저 (/chat): 카메라/이미지 버튼 렌더 → 이미지 업로드 → 썸네일 칩 → "비전 진단 모드" 안내 → SSE progress chip (분류/추출/조회/렌더링) → VisionAdviceCard 렌더(장비 추정/관찰 상태/참고 의견/작업 등록/설비 등록 버튼/**면책 푸터**) 스크린샷 캡처
  - tsc --noEmit 신규 에러 0건
- Zero-Hallucination 검증:
  - advice_text `[AI 참고 의견]` 접두어 강제
  - VLM이 수치 생성 0건 ("고장 여부는 사진만으로 판단할 수 없습니다")
  - `answer_text: null` 명시로 DB 사실 영역과 격리
  - violet-500 테두리로 시각적 분리 (기존 slate 카드와 구분)
- 미완료:
  - **P6**: 작업 등록 버튼 → TaskFormDialog compact 자동 채움
  - **P7**: 설비 등록 버튼 → EquipmentPhotoRegisterDialog 3단계
  - 매뉴얼 PDF 업로드 → RAG 실데이터

#### 후속 (LED 관찰 프롬프트 강화)
- `vision_agent.py` `_DIAGNOSE_PROMPT_TEMPLATE`에 LED 관찰 가이드 섹션 추가 — 각 LED 라벨 분리 + 색상 명시 + 매뉴얼 참조 안내 자동 부착. observed_state 2개 → 7개로 가독성 향상. 응답 시간 75s → 44s. Zero-Hallucination 유지 (단정 0건, 운영 지시 0건). 실제 LS XGK 사진으로 라이브 재검증 완료.

#### 커밋 (P1~P5 + 프롬프트 개선)
- `slm@474352f` vision_agent.py + endpoints/vision_proxy.py + ai_server.py router
- `slm-dashboard@8f51b8b` 프론트 8개 파일 (types + stream + hook + ChatInput + VisionAdviceCard + BotMessage + mapper + ChatMessageArea)
- `web@b13fa2e` db/migrations/0043 + docs E-025 + work-history
- `slm@c19c3a4` 후속 — vision_agent 프롬프트 LED 관찰 강화
- `web@71557ae` docs 후속

#### P6/P7 후속 (2026-04-15)
- P6 작업 등록 연동: VisionAdviceCard "작업 등록" → TaskFormDialog compact 자동 채움 + `tb_task_master.vision_session_id` + `tb_vision_session.linked_task_id` 5회 매치 검증 완료
- P7 현장 설비 등록: VisionAdviceCard "설비 등록" → EquipmentPhotoRegisterDialog 단일 페이지 폼 + `tb_equipment_info` + `tb_equipment_image` + `linked_equipment_id` 5회 매치 검증 완료
- `slm@3e487e6` /crisis/tasks POST에 vision_session_id 지원
- `slm-dashboard@f5fb968` VisionAdviceCard "작업 등록" → TaskFormDialog compact 연동
- `slm@5e7c144` /equipments POST에 vision 등록 필드 지원
- `slm-dashboard@e402285` VisionAdviceCard "설비 등록" → EquipmentPhotoRegisterDialog

#### 매뉴얼 PDF 다운로드 경로 폐쇄망 대응 (2026-04-15)
- 배경: 사용자 시나리오 "XGT 매뉴얼 열람" + "비전 진단 후 매뉴얼 다운로드 링크", 폐쇄망 = 외부 벤더 사이트 링크 금지
- 발견 문제: file_url이 /api/files/manual/*로 Next.js BFF 로컬 FS 참조했으나 slm-frontend엔 파일 없음 + slm-backend는 ephemeral /web/files/manuals/에 저장 중
- 수정:
  - 18개 PDF를 ../slm/data/manuals/ (바인드 마운트)로 물리 이동
  - file_url 프리픽스 /api/files/manual/ → /api/proxy/files/manual/
  - 백엔드 GET /files/manual/{filename} 신규 (path traversal 방어 + UTF-8 Content-Disposition)
  - tb_equipment_manual 17건 file_url 일괄 UPDATE, vision_agent _ManualRagIndex + ManualExcerpt 모델 확장
  - VisionAdviceCard 매뉴얼 인용에 [Download PDF] 버튼 + /admin/equipment-manuals 다운로드 아이콘
- 외부 URL grep 결과: slm/slm-dashboard 코드 0건 (PDF 콘텐츠 내부만)
- 검증: 백엔드 직접 200 PDF 응답, 프록시 401 게이트 정상, manual-search 응답 file_url 포함
- `slm@230dabf` + `slm-dashboard@3dbd834`

#### 3종 신규 이미지 E2E + 인버터 whitelist 버그 수정 (2026-04-15)
- 사용자가 `docs/매뉴얼/plc 사진/`에 PLC 3장 + AC&T RCS-XG LTE 모뎀 3장 + 인버터 1장 추가
- **plc1.jpg**: LS XBF-DR32H 정확 식별, matched=plc_1, manual 3건 (XGL-EFMTB 트러블슈팅)
- **rcs_xg_lte.jpg**: 모뎀/RCS-XG 식별, **AC&T 첫 VLM E2E 성공**, manual 3/3 4G-210N 매뉴얼(p20 LED 표시 등 완벽 매칭)
- **inverter2.jpg 버그**: equipment_type=기타로 fallback → manual 0건 → `EQUIPMENT_WHITELIST` + prompt + JSON schema 3곳에 "인버터" 누락 발견
- **수정**: whitelist에 "인버터" 추가 → 재검증 시 LS S100 정확 식별, G100 사용설명서 p349 "9장 문제 해결하기" 정확 매칭
- `slm@39becfb`

#### AC&T 제품 RAG 직접 검증 (2026-04-15)
- 4개 제품(4G-210N / ETOS-XP / EtherFOS-EZ / IIoT RTU) × 각 제품 관련 쿼리 × top-5 = **20/20 정확 매칭**
- brand='AC&T System' + equipment_type 필터로 타 제조사(LS) 매뉴얼 완벽 배제
- IIoT RTU score 0.689 최상, 실제 매뉴얼 설치/배선 섹션 정확 매칭
- VLM 경로는 AC&T 제품 사진이 없어 스킵 — 향후 이미지 추가 시 E2E 재검증 필요

#### 10회 Web E2E 안정성 검증 (2026-04-15)
- P1~P15 완료 후 실제 브라우저 회귀 — `xgk plc cpue.jpeg` 업로드+질의 × 10회
- 10/10 성공: VisionAdviceCard 렌더, manual_excerpts 30/30, 작업+시설물 버튼 10/10회, 설비 등록 1회 (is_registered=False 케이스), catalog 노출 0/30 (P14 boost 검증)
- VLM 변동성: equipment_guess가 XGB/XGT/XGK/LS PLC 혼재, 장비 타입은 전 10회 PLC 고정
- has_issue 0/10 — 저해상도 이미지에서 heuristic 보수적(Zero-Hallucination 유지)
- 스크린샷 `e025-10run-e2e-final.png`

#### P15 리뷰 항목 일괄 해결 — master-k 제외 / 글로벌 매칭 제거 / 매뉴얼 업로드 UI / canonical 샘플 경로 (2026-04-15)
- **#2 master-k 제외**: `SKIP_FILENAME_PATTERNS`에 master-k 추가, DB row+NPZ 삭제, 2833→2830 chunks
- **#6 글로벌 매칭 제거**: `_match_existing_equipment` site-scoped only로 리팩토링, 오탐 방지. 5 site rotation 유지 확인
- **#5 매뉴얼 업로드 UI**: `index_single_pdf` 헬퍼 추출 + `/admin/equipment-manuals` 3개 엔드포인트 (GET/POST upload/DELETE) + `/admin/equipment-manuals/page.tsx` 관리자 페이지 (테이블 + 업로드 Dialog + 삭제)
- **#7 canonical 경로**: `docs/매뉴얼/plc 사진/`를 공식 경로로 지정, `docs/test-image-samples.md` 가이드 신규
- **✅ review-items 7건 전부 해결**
- `slm@decb86c` + `slm-dashboard@a81ae5e`

#### P14 RAG 품질 개선 — user_manual boost (2026-04-15)
- `tb_equipment_manual.manual_type` 컬럼 추가 + 17건 title pattern 분류 (catalog 4 / user_manual 13)
- `_ManualRagIndex` search에 soft boost (user_manual +0.08 / catalog -0.05), backward compat (information_schema 확인)
- 검증: manual-search 3쿼리 15/15 user_manual, diagnose 3/3 user_manual, 기존 XGT Catalog p.107 #1 끌어올림 현상 제거
- review-items #1(쿼리 튜닝) + #4(catalog 분리) 동시 해결
- `slm@184764e`

#### P13 시설물 사진 등록 (2026-04-15)
- `vision_proxy POST /vision/register-facility-photo` 신규 — chat_attachment 이미지 → facility/<type>/<uuid> 복사 + tb_file_storage INSERT + tb_facility_file UPSERT, savepoint로 vision_session 역연결 격리
- `FacilityPhotoRegisterDialog.tsx` 신규 (240줄, emerald 테마) — 시설유형/현장명 Select + 파일 유형 3개 카드 (현장 사진/계통도/매뉴얼) + 저장
- VisionAdviceCard에 "시설물 사진" 버튼 (emerald, ImageIcon) — 작업/설비 등록과 병렬 노출
- E2E: ls_xgk_error.jpg → 행정 배수지 질의 → 버튼 클릭 → Dialog 자동 채움 → 등록 → tb_facility_file row 확인
- 버그 수정: savepoint 격리로 linked_facility_file_id 컬럼 미존재 시 facility_file 삽입 롤백 방지
- `slm@e04df56` + `slm-dashboard@cf87358`

#### P12 명판/계기판 OCR 자동 등록 (2026-04-15)
- 설비 등록 버튼 클릭 시 `/vision/register-parse` 자동 호출로 manufacturer/model/serial/capacity/installed_year 판독
- 백엔드: `endpoints/vision_proxy.py` POST `/vision/register-parse` 프록시 추가 (ai_server 8000 → vision_agent 8100)
- 프런트: `equipment-api.ts parseNameplate`, `EquipmentPhotoRegisterDialog` ocrFields/ocrText props + S/N·용량·설치년도 입력 필드, sky 테마 OCR 배너, `chat/page.tsx handleRegisterEquipmentFromVision` 비동기화
- E2E: ls_xgk_error.jpg → 설비 등록 → OCR 자동 호출 → Dialog 오픈 시 brand=LS, model=XGP-ACF2+XGK-CPUE 자동 채움 + 원본 OCR 12라인 표시
- `slm@61737bb` + `slm-dashboard@b23c70d`

#### P11 경보 → /chat 딥링크 진입 (역방향 플로우) (2026-04-15)
- `AlarmReportTable` 각 행에 카메라 버튼 (`/chat?sitename=X&facilitytype=Y&prefill=...`)
- `chat/page.tsx` `deepLinkSite` state + `useEffect`에서 query params 읽어 prefill + 컨텍스트 저장, URL 즉시 clean
- `ChatInput`/`useChatSubmit` `siteContext` prop drill → `streamMultimodalChat`에 `sitename/facilitytype` 자동 첨부
- E2E: 테스트 알람 seed → 카메라 버튼 클릭 → /chat 진입 → prefill 확인 → 이미지 업로드 → VisionAdviceCard "연결된 활성 알람" 섹션에 시드 알람 정확히 1건 노출
- 정방향(사진→진단→조치) + 역방향(알람→현장확인) 북극성 루프 완성
- `slm-dashboard@f9e51b8`

##### P11 보정 (2026-04-18): 경보 목록 카메라 버튼 제거
- 사용자 피드백 "매번 태그 알람을 사진확인을 통해 정리할수는 없음" → 행별 사진확인 버튼이 "해제=사진 필수" 오해를 유도
- `AlarmReportTable` "작업" 컬럼에서 Camera 버튼 삭제, ClipboardList(작업 등록)만 유지. `useRouter`/`handleVisionCheck` 정리
- `/chat` 딥링크 로직 자체는 존속 — VisionAdviceCard·설비 상세 등 다른 진입점에서 재사용 가능. "비전 점검 해제" 뱃지(P10) 유지
- 정책 memory: `feedback_no_photo_per_alarm.md` (경보 목록 행별 사진확인 버튼 배치 금지)
- `slm-dashboard@b804a06`

#### P10 경보 목록 비전 해제 배지 + 5회 회귀 테스트 (2026-04-15)
- `AlarmReportTable` — `isVisionResolved()` + "상태" 컬럼에 보라 `<Badge>카메라 비전</Badge>` (user_cause_description에 '[비전 점검 해제]' prefix 있으면). title attribute로 hover시 전체 note 노출
- 감사 추적: 해제된 알람 중 현장 비전 점검으로 해제된 건을 즉시 식별
- 5회 회귀 테스트: 5 site rotation × diagnose → resolve, 전 10회 성공 (has_issue 5/5, active_alarms 5/5, manual 5/5, resolve 5/5)
- `slm-dashboard@9b41987`

#### P9 알람 연계 — 점검→장애→알람 단방향 플로우 (2026-04-15)
- **P9a 백엔드**: `vision_agent.ActiveAlarm` + `_detect_issue`(regex heuristic, LED/외관/화재) + `_fetch_active_alarms(sitename, equipmenttype)` → `tb_equipment_alarm_report WHERE alarm_end_time IS NULL`
- **P9a sitename 추론**: `vision_proxy._infer_site_from_text` — `tb_equipment_info` 캐시 substring 매칭 (가장 긴 것 우선), 프론트 미명시 시 자동
- **P9c 알람 해제 API**: `POST /crisis/alarm-reports/resolve` — `alarm_end_time=NOW()` + `alarm_confirm_yn='Y'` + `user_cause_description` append
- **P9b 프런트**: `VisionAdviceCard` "문제 감지" 빨간 뱃지 + "연결된 활성 알람 · N건" 섹션(체크박스 기본 체크) + 작업 등록 버튼에 "+알람 N건 해제" 배지
- **프론트 통합**: `onCreateTaskFromVision(vision, selectedAlarmKeys)` 시그니처 확장, `chat/page.tsx pendingAlarmKeys` state + `handleTaskSubmit`에서 createTask 성공 후 `resolveActiveAlarms` 호출
- **E2E**: 행정 배수지 PLC 2건 알람 seed → 질의 → VisionAdviceCard 2건 알람 체크 상태 렌더 → 작업 등록 → task_id=13 생성(vision_session_id=22) + 2건 알람 alarm_end_time DB 확인
- `slm@6d8291a` vision_agent + vision_proxy + alarm_crisis
- `slm-dashboard@13981be` 6개 파일 (chat.ts + VisionAdviceCard + BotMessage + ChatMessageArea + chat/page + alarm-api)

#### P8 기존 설비 DB 매칭 (2026-04-15)
- `vision_agent._match_existing_equipment()` — sitename + equipmenttype + `meta->>'model'` ILIKE 우선, 실패 시 `meta->>'manufacturer'` ILIKE fallback, site 내 실패 시 글로벌 재시도
- `/vision/diagnose` 응답의 `is_registered`/`matched_equipment_id` 하드코딩 제거
- 검증: sitename 5종(행정/석문/신평/송악1/갈산) → plc_1/plc_2/plc_3/plc_4/plc_79 5/5 매칭 성공. VLM 모델(XGK-CPUE) 미스매치였으나 brand fallback(LS → LSE)으로 site별 PLC 1:1 매칭
- `slm@fdda15d` _match_existing_equipment + diagnose 통합

#### P3 매뉴얼 RAG 실구현 (2026-04-15)
- `docs/매뉴얼/` 18개 PDF(LS PLC/인버터 14 + AC&T RTU/모뎀 4) → snowflake-arctic-embed2 임베딩 → NPZ 캐시 + `tb_equipment_manual` 등록 (2469 페이지 / 2580 청크)
- `slm/tools/index_manuals.py` 신규 — 파일명 → equipment_type/brand/model 자동 매핑, 장문 페이지 overlap 분할, idempotent UPSERT
- `slm/vision_agent.py` `_ManualRagIndex` 클래스 — lazy-load NPZ + cosine search + 장비 타입/브랜드 soft boost. `/vision/manual-search` 실구현, `/vision/diagnose` 응답에 `manual_excerpts` 자동 채움
- 검증: LS XGK ERR LED 이미지 5회 반복 — 전 5회 3/3 excerpts, avg top score 0.640, #1 결과 5/5회 `XGR-CPU p251 "15.2.3 ERR LED 점등 조치방법"` 안정 검색
- `slm@5d0374f` vision_agent RAG + tools/index_manuals.py

---

### 완료 (2026-04-13 — 위기대응 검출 로직 다이어그램 시각 디자인 개선 [E-024])

- 사용자 요청: "(로직점검 프로세스)의 도형 화살표 및 디자인을 디자인 관점에서 가독성 있고, 직관적으로 개선"
- 이전 디자인 문제: 박스 너무 작음(11px), 형태 구분 없음, 화살표가 텍스트, 검출 강조 약함, dimmed 모호, 색 대비 약함, 행 구분 없음
- 개선 (`AlarmAnalysisDetail.tsx`의 `DiagramFlow` + 신규 `DiagramBox` 컴포넌트):
  1. **박스 크기·텍스트** 100px+ / 12px / px-3 py-2.5 + 2px solid border
  2. **형태 분리** — 알고리즘 시작(blue)은 `rounded-full` pill + `PlayCircle` 아이콘, 일반 단계는 `rounded-xl` rounded-rect
  3. **검출 강조** — `border-red-500 + bg-red-500/20 + scale-[1.08] + ring-2 + shadow-lg shadow-red-500/30` + 박스 위쪽 떠 있는 "✓ 검출" 배지(`bg-red-500 rounded-full + CheckCircle2`)
  4. **dimmed 명료화** — `border-dashed + bg-transparent + text-muted-foreground/50 + scale-95` + 위쪽 작은 회색 Circle 마커
  5. **화살표 Lucide 전환** — `ChevronRight` (가로, 검출 전 sky / 검출 후 red / dimmed grey), `ChevronsDown` (행 구분, 양쪽 그라데이션 divider)
  6. **컨테이너** — `rounded-xl + gradient bg + border` + 검출 헤더 카드 (`border-red-500/30 bg-red-500/10 + CheckCircle2`)
- 검증 (Playwright 라이브):
  - 송산2산단(배) 1지 수위 LL 알람의 다이어그램 스크린샷 캡처
  - 검출 박스 "유입유출량 분석" 빨간 테두리 + ✓ 배지로 즉시 식별
  - 이후 8개 박스 dashed + 회색으로 "실행 안 됨" 시각화
  - Algorithm start 박스가 pill + PlayCircle 아이콘으로 일반 단계와 즉시 구분
  - tsc --noEmit 신규 에러 없음

#### 커밋
- `slm-dashboard@(예정)` AlarmAnalysisDetail.tsx DiagramFlow/DiagramBox 디자인 개선
- `web@(예정)` docs E-024

---

### 완료 (2026-04-13 — AI 현황 요약 Hybrid 재설계 [E-023])

- 사용자 의도: "교차 검증의 의미, 가장 중요한 항목, 알람의 위급한 순서대로 보라고 가이드". 통계 수치 나열 → 카테고리 의미 + 점검 순서 가이드 형식
- 설계 검토 결과 **Hybrid 추천** 채택: LLM은 가장 위급한 1건만 1문장 서술, 카테고리 정의·카운트·점검 순서는 Python 정적 조립 (속도+의도+할루시네이션 3축 모두 우위)
- 변경 (`slm/endpoints/scan_all_explain.py` 전면 재작성, 약 440 lines):
  1. 카테고리 상수 4종 (`CATEGORY_PRIORITY`/`LABELS`/`MEANINGS`/`_VERDICT_WEIGHT`)
  2. `_classify_row` / `_count_by_category` / `_select_most_urgent` / `_template_urgent_sentence` / `_assemble_summary` 헬퍼
  3. LLM 프롬프트 축소: 단일 row 정보 + "1문장" 강제. 허용 수치 화이트리스트에 프롬프트 상수 `0/1/30` 추가
  4. `num_predict=None`으로 — gemma4 chat 템플릿 토큰 budget 소진 회피
  5. 0건 / 모두 정상 케이스 LLM 건너뛰고 즉시 템플릿 응답
- 점검 순서 (위급도 순): **① 설비 장애 → ② 교차 검증 → ③ 데이터 품질 → ④ 값 이탈** (확정 사고 → 물리 피해 의심 → 모니터링 무력화 → 통계 경계)
- 응답 형태: `[중요 알람] LLM 1문장` + `[유형별 현황] N·M·K·L건` + `[가장 위급한 카테고리] 정의` + `[점검 순서] ①→②→③→④` (4섹션, `\n\n` 구분) — *라벨명 2026-04-18 `[가장 위급]` → `[중요 알람]` 변경*
- 프런트 (`AnomalyScanView.tsx`): `<p>`에 `whitespace-pre-line leading-relaxed` 추가, `source: "template"` sky 색상 구분
- 검증 (curl):
  1. 전역 → `source: llm`, **38.8s**, 4섹션 정상 (counts: 설비장애 112 / 교차 9 / 품질 14 / 값이탈 31, 총 298)
  2. 행정1수청 소소블록 (1 row 정상) → `source: template`, **15ms**, "전 시설 정상 범위에서 동작 중"
  3. 없는시설 (0 row) → `source: template`, "현재 이상 탐지된 태그가 없습니다"
- 속도: 0건/정상 케이스가 30s → **즉시**로 단축. LLM 경로는 비슷
- 디버그: gemma4의 `num_predict=100` 빈 응답 버그 + `30.0` allowed_numbers 누락 해결

#### 커밋
- `slm@(예정)` scan_all_explain.py 전면 재작성
- `slm-dashboard@(예정)` AnomalyScanView whitespace-pre-line + template source 색상
- `web@(예정)` docs E-023

---

### 완료 (2026-04-13 — 채팅 "AI 현황 요약" 시설 범위 필터 [E-022])

- 사용자 요청: "행정1수청 소소블록 이상 스캔해줘" 질의 후 AI 현황 요약이 전역 Top-3(남산/송산2산단생활/석문2)을 반환. 해당 현장만 나와야 하고 없으면 "없다"고 해야
- 원인: `AnomalyScanView`의 `handleExplainScan`이 `explainScanAll(3)`을 scope 필터 없이 호출 → 백엔드가 전역 `_ANOMALY_SCAN_CACHE`에서 Top-N 반환
- 수정 (3-layer):
  1. **백엔드** `scan_all_explain.py` — `ScanAllExplainRequest`에 `sitename/facilitytype` 필드 추가, 캐시 rows 로드 직후 scope 필터, 0건 시 LLM 건너뛰고 "`{scope}에 현재 이상 탐지된 태그가 없습니다.`" 템플릿 응답, LLM 프롬프트에 분석 범위 섹션 + "범위 밖 시설 언급 금지" 규칙 7 추가, `_build_fallback(top_rows, top_n, scope_label)` 시그니처 확장
  2. **API 래퍼** `anomaly-api.ts` — `ScanScope` 타입 신설, `explainScanAll(topN, scope?)` 시그니처, `source: "template"` 추가
  3. **컴포넌트** `AnomalyScanView.tsx` — rawData에서 scope 추출(sitename/facilitytype Set이 크기 1인 경우만 확정), `explainScanAll(3, scanScope)` 호출
- 검증 (curl 실측):
  - `sitename=행정1수청, facilitytype=소소블록` → `"행정1수청 소소블록의 총 스캔 태그는 1건이며, 이상 판정은 0건, 주의 판정은 1건입니다. 주의 항목인 행정1(수청)소블럭 압력은 현재 5.77, 30일 평균 6.10, 편차 5.5%, z=-2.38입니다."` (`source: llm`, `top_rows_count: 1`)
  - `sitename=없는시설, facilitytype=소소블록` → `"없는시설 소소블록에 현재 이상 탐지된 태그가 없습니다."` (`source: template`)
  - tsc --noEmit 신규 에러 없음

#### 커밋
- `slm@(예정)` endpoints/scan_all_explain.py scope 필터
- `slm-dashboard@(예정)` anomaly-api.ts + AnomalyScanView.tsx scope 전달
- `web@(예정)` docs E-022

---

### 완료 (2026-04-13 — GIS 유량흐름 초기 visibility race 버그 [E-021])

- 증상: `/monitoring/gis` 첫 접속 시 "유량 흐름" 토글이 모두 off인데 flow 레이어(Glow/Base/Anim/Imbalance/Node)가 화면에 렌더됨. 사용자가 토글을 on→off 한 번 거쳐야 사라짐
- 원인: `GisFlowOverlayLayer.tsx`의 `addFlowLayers`가 `map.addLayer(...)`에 `layout.visibility` 미지정 → MapLibre 기본값 `"visible"` 적용. Mount effect의 `initLayers`가 비동기 실행되는 사이 visibility 보정 effect는 source 미존재로 bail out → 레이어 추가 후 보정이 재실행되지 않음 → 초기 상태가 visible로 고정
- 수정: `addFlowLayers`의 5개 `map.addLayer` 모두에 `layout.visibility = "none"` 초기값 주입. Visibility effect는 그대로 — show* props가 true로 바뀔 때 `setLayoutProperty("visible")`로 승격
- 검증 (Playwright 라이브):
  1. 초기 진입 → 5개 레이어 모두 `none` ✅
  2. "유량 두께·색상" 토글 on → `gis-flow-base`만 `visible`, 나머지 `none` ✅
  3. 다시 토글 → 5개 모두 `none` 복귀 ✅

#### 커밋
- `slm-dashboard@(예정)` GisFlowOverlayLayer.tsx addFlowLayers visibility 초기값
- `web@(예정)` docs/error-management.md E-021 + work-history.md

---

### 완료 (2026-04-13 — DB 미사용 테이블 12종 정리 + 핫 테이블 최적화 [E-020])

- 사용자 요청: "현재 db에서 사용하지 않는 테이블은 삭제하고 최적화 해줘"
- 조사: ANALYZE 후 빈 테이블 19개 → 코드 grep + FK + view 검사로 진짜 미사용 12개 확정
- 삭제 12개 (모두 0 rows, 외부 FK 없음, view 참조 없음):
  - `tb_alarm_log` → `tb_equipment_alarm_report`로 통합
  - `tb_user_session` → `tb_user.current_session_id` 컬럼으로 통합
  - `tb_menu_api`, `tb_file_history` → 미구현
  - `tb_ai_chat_faq` → `chat_faq_examples.py`가 동적 생성
  - `tb_ai_chat_ask/bot/ask_group/ask_image/bot_image` (5개) → 채팅 히스토리는 클라이언트 localStorage만 사용. FK 5건 모두 자기들끼리만 묶임
  - `tb_prompt_template/column` → 코드(슬롯필링 + example3.json) 직접 관리, 메뉴 숨김 처리됨
- 유지 (빈 테이블이지만 사용 중): `tb_leak_cusum_alert`, `tb_facility_alias`, `tb_ai_chat_feedback`, `tb_field_lock`, `tb_causal_chain_override`
- 단계:
  1. 백업 — `pg_dump --schema-only` 12개 → `db/backups/unused_tables_backup_2026-04-13.sql` (롤백용)
  2. 트랜잭션 DROP CASCADE — 의존성 순서로 12개 (BEGIN/COMMIT)
  3. VACUUM ANALYZE + REINDEX — `tb_network_status`, `tb_equipment_alarm_report`, `tb_tag_info` 등 핫 테이블
  4. CLUSTER `tb_equipment_alarm_report USING idx_alarm_report_start_time` (시계열 정렬)
  5. 시드 SQL 정리 — `db/init/02/03/05/06_*.sql` 정의 제거 + 주석 마킹, `db/seed/05_chat_faq.sql`/`06_prompts.sql` + `db/init/01_schema.sql.bak` 삭제
- 결과:
  - 테이블 수: **59 → 47** (-12, -20%)
  - `tb_equipment_alarm_report`: 71 MB → **65 MB** (-8.5%)
  - `tb_network_status`: 267 MB → **263 MB** (-1.5%)
- 검증: 백엔드 `/health` 200 OK, 로그에 "relation does not exist" 0건. 정리된 init SQL 재로드 시 SET/CREATE TABLE 모두 통과 (기존 ALTER ADD CONSTRAINT 패턴은 멱등성 없음 — 기존 코드 유지)
- 롤백: `psql -d slm < db/backups/unused_tables_backup_2026-04-13.sql`

#### 커밋
- `web@(예정)` db/backups + db/init/* + db/seed/* + docs/error-management.md E-020 + docs/work-history.md + docs/slm-api-contract-final.md (E-019 추가)

---

### 완료 (2026-04-13 — 검출 로직 다이어그램 현재 알람 반영 + 검출 단계 시각화 [E-019])

- 증상: [E-018]로 옛 다이어그램 583건은 표시되지만 신규 알람에 다이어그램 미생성 + 검출 단계 표시 없음
- 원인: Node-RED `flows_deploy.json`의 다이어그램 생성 함수 `a655fae0839ec028`(17,574 chars)가 upstream/downstream wires 모두 끊긴 dead 노드. 활성 체인은 단축 함수 `820cf7cd8e67c2f9`(8,719 chars)로 wired
- 해결 (3-layer):
  1. **Node-RED 함수 swap + 검출 단계 로직 주입** — `820cf7cd8e67c2f9.func` ← dead a655 코드 + 신규 헬퍼 2종(`detectStep` 키워드 매칭, `applyDetectionClasses` 클래스 주입). HTML 템플릿에 `.detected`/`.dimmed` CSS 추가. a655는 `disabled=true`로 백업 마킹
  2. **프런트엔드 파서/렌더러** — `DiagramStep`에 `detected`/`dimmed` 추가, 첫 detected 발견 후 후속 박스 자동 dimmed (Node-RED 누락 대비 보강), `DETECTED_BOX_CLASSES`(red ring + scale + glow) / `DIMMED_BOX_CLASSES`(opacity 0.3 + grayscale), 검출 단계 범례 + tooltip + aria-label
  3. **백엔드** — E-018에서 이미 `?days=90` 노출 처리됨
- 검증:
  - Node-RED 라이브: 죽동(배) HH 알람 + 송산2산단생활(배) 수위 LL 주의 알람 NULL→재처리 → 13977/14042 chars + detected + data-step 정상
  - 박스 분포: plain 6 + detected 1 (#7 유입유출량 분석) + dimmed 8 = 15
  - React 파서 라이브 (Playwright): `{detected:{label:"유입유출량 분석"}, counts:{plain:6,detected:1,dimmed:8}}` 정확 추출
- 한계:
  1. 배수지 수위 전용 — 가압장/네트워크/UPS/펌프/밸브는 다이어그램 함수 미존재 (Phase 2)
  2. 휴리스틱 매칭(~80% 정확도) — 정확한 단계 추적은 상위 switch에서 `msg.detection_step` 직접 set하는 Phase 2 리팩토링 필요

#### 커밋
- `web@(예정)` flows_deploy.json swap + CSS + docs E-019
- `slm-dashboard@(예정)` AlarmAnalysisDetail.tsx 파서/렌더러 detected/dimmed

---

### 완료 (2026-04-13 — 위기대응 수위 알람 검출 로직 다이어그램 렌더링 [E-018])

- 증상: `tb_equipment_alarm_report.diagnosed_msg`에 들어있는 검출 로직 흐름도(`<div class="diagram-container">` + `.flow-row/.flow-step/.flow-box` + `.arrow-down-connector`)가 위기대응 화면에 한 건도 표시되지 않음
- 원인 (복합):
  1. 프런트엔드 파서 누락 — `parseDiagnosedMsg()`가 `<p>/<ul>/<ol>/.info-box`만 처리, `<span>/<h3>/.diagram-container`는 무시
  2. 백엔드 30일 컷오프 — 다이어그램 행 583건이 모두 2026-02-01~02-27 범위라 30일 필터에 잘림
- 해결:
  1. `AlarmAnalysisDetail.tsx` 파서 확장 — `<span>` (text), `<h3>` (heading), `.diagram-container` (diagram) 블록 추가
  2. 신규 `parseDiagramContainer()` — `.flow-step/.flow-vertical/.arrow-down-connector`를 문서 순서로 순회, `arrow-down-connector`를 row 구분자로 사용해 `DiagramRow[]` 생성
  3. 신규 `DiagramFlow` 컴포넌트 — Tailwind 정적 매핑(`bg-sky-500/15` 등)으로 6색 지원, 가로 flex + `→`/`↓` 화살표
  4. `endpoints/alarm_crisis.py:476` `days` 쿼리 파라미터(7~365 클램프), 기본값 30→90으로 확장
- 검증: Playwright + 실제 DB 행으로 신규 파서 inline 실행 → DiagramRow 5개 (steps 3 + arrow_down 2), 15개 flow-box 색상 분류 정상. `curl /crisis/alarm-analysis?days=90` → 500건 중 183건이 다이어그램 포함 (이전 0건)

#### 커밋
- `slm@(예정)` endpoints/alarm_crisis.py days 파라미터화
- `slm-dashboard@(예정)` AlarmAnalysisDetail.tsx 파서 + DiagramFlow 컴포넌트
- `web@(예정)` docs E-018 + work-history

---

### 완료 (2026-04-13 — /monitoring/flow 첫 접속 18s 지연 제거 — dev JIT 프리워밍 사이드카)

- 증상: 백엔드·프런트엔드 기동 직후 처음 용수 흐름 페이지 접속 시 데이터가 18초 가까이 뜨지 않음 [E-017]
- 원인 (복합):
  1. Turbopack `next dev` JIT 컴파일 — `/api/proxy/[...path]/route.ts` 카탈-올 첫 컴파일 ~500ms, 세 개 병렬 호출이 같은 compile을 대기
  2. 백엔드 cold 경합 — IForest 학습(37s) + SCAN_ALL SQL(98s) + SNMP 폴링이 동일 DB 리소스 경합 구간에 `flow_realtime.py`의 10~12개 쿼리 연쇄가 떨어지면서 render 시간 18s까지 증가
- 해결: dev 전용 프리워밍 사이드카 `frontend-prewarm` 추가
  - `slm/dev_tools/prewarm.sh` — backend /health + frontend /login 준비 대기 후 백엔드 핵심 엔드포인트 4건 + 프런트엔드 페이지 10종 + 프록시 라우트 3건 순차 curl
  - `docker-compose.dev.yml` — `curlimages/curl:latest` 기반 원샷 서비스, `restart: no`, `depends_on: [frontend, backend]`
  - 스택 기동마다 1회 자동 실행 → 카탈-올 프록시 핸들러 컴파일 + 백엔드 캐시 + `_flow_baseline_cache` 워밍
- 검증: `.next/dev` 디렉토리 제거 후 frontend 재기동 → prewarm 사이드카 실행 → `/api/proxy/flow-map 401 in 23ms (compile: 16ms)` 확인 (cold 500~700ms → 20ms)
- 납품 시 제거: `next build && next start` 운영 빌드는 JIT 컴파일이 없어 불필요. docker-compose.dev.yml에 제거 주석 명시

#### 커밋
- `slm@(예정)` dev_tools/prewarm.sh 신규
- `web@(예정)` docker-compose.dev.yml + docs/error-management.md E-017 + work-history.md

---

### 완료 (2026-04-13 — Ollama gemma4:26b-a4b 연결 + 최적화 + 사이드바 메뉴 복원 + 10회 안정성 테스트)

#### 1. Ollama 연결 [E-016]
- Mac 네이티브 Ollama(Metal GPU) 설치 후 AI explain 엔드포인트들이 "model 'gemma4' not found"로 실패하던 문제 해결
- 실제 설치 태그(`gemma4:26b-a4b-it-q4_K_M`)로 `.env` 3곳 + `slm_config.py` 기본값 통일
- Docker→host 네트워크: `OLLAMA_URL=http://host.docker.internal:11434`

#### 2. Ollama 최적화 (cold-start 제거)
- `OLLAMA_KEEP_ALIVE="24h"` 도입 (`slm_config.py` + `ollama_client.generate()` payload 자동 주입)
- ai_server.py lifespan startup에서 백그라운드 스레드로 모델 웜업 (`generate("ping", num_predict=1)`)
- 5개 explain 엔드포인트 timeout 상향 (90→180s, tag_latest 60→120s) — 이전 코멘트의 p95/p99 stale 수치 갱신
- `/api/ps`에서 `expires_at +24h` 확인 → 모델 VRAM 상주 정상

#### 3. 10회 안정성 테스트 (2026-04-13)
| 엔드포인트 | 성공/시도 | LLM 경로 | median | range |
|---|---|---|---|---|
| FAQ `/chat/faq/examples` | 10/10 | n/a | <100ms | <100ms |
| P2.7 `/equipment-mtbf/explain` | 10/10 | 10/10 | 42s | 36~94s |
| P2.6 `/anomaly/scan-all/explain` | 10/10 | 10/10 | 33s | 32~44s |

**Fallback 0회** — 100% LLM 경로 통과. 첫 호출만 outlier(82~94s)이고 이후 안정적(~36s/~33s).

#### 4. 사이드바 메뉴 17건 복원 [E-015]
- 증상: 모니터링 그룹 "알람 캘린더(M003-6, 히트맵)"·"누수 의심 알림(M003-7)" + 관리 그룹 "채팅 피드백·시설 약칭·설비 신뢰성·LLM 서술 관찰(M100-7~10)" 메뉴가 사이드바에서 보이지 않음
- 원인: `use-sidebar-menus.ts`가 `tb_menu` DB를 1순위로 사용하는데 시드(`db/seed/03_menus.sql`)가 `sidebar-menus.ts`와 완전 어긋난 stale 상태였음 (M003-4~7 / M006 위기대응 / M100-5~10 / M200 14건 모두 누락 — 총 17개)
- 해결:
  1. `tb_menu` + `tb_auth_menu` 직접 INSERT로 즉시 복구 (16건)
  2. `db/seed/03_menus.sql` 전체 재작성 — 단일 출처를 `sidebar-menus.ts`로 명시, `ON CONFLICT DO UPDATE`로 재시드 시 변경 자동 반영, VIEWER 권한은 EXISTS 가드로 FK 위반 방지
  3. `sidebar-menus.ts`에도 누락돼 있던 M100-10 정적 폴백 보완

#### 커밋
- `slm@d6b97a9` Ollama 연결 + 최적화 (slm_config/ollama_client/ai_server/5개 endpoint)
- `web@3cb06f3` `db/seed/03_menus.sql` 전체 재작성 (메뉴 17건 동기화)
- `slm-dashboard@88cd239` `sidebar-menus.ts` M100-10 정적 폴백 추가
- `web@c979141` `f1e6679` 이전 P2.4/P2.6/P2.7/P2.8/FAQ 완료 이력 동기화

#### 미커밋 잔여
- `slm` 레포 `endpoints/trend.py`·`endpoints/tag_latest_explain.py`: 본 세션 timeout 1줄 변경이 이전 미커밋과 섞여 있어 별도 정리 필요 (운영 컨테이너에는 이미 적용됨)

---

### 완료 (2026-04-13 — P2.6 종합 이상 요약 + P2.7 MTBF 서술 + FAQ 동적화)

C안 중기 확장 Phase 2의 남은 3종(P2.6/P2.7/FAQ) 백엔드·프런트 연동 상태 재확인 및 문서 반영.
실상은 이미 엔드포인트·프런트 컴포넌트 모두 구현돼 동작 중이었으나 work-history에 독립 항목으로 기록되지 않아 남은 할 일로 오인되던 상태. `py_compile` 정적 검증 통과, UI 버튼·결과 카드 렌더 코드 확인 완료.

#### P2.6 — `/anomaly/scan-all/explain`
- 백엔드 `endpoints/scan_all_explain.py` (282 lines):
  - `_ANOMALY_SCAN_CACHE`에서 verdict(`이상`/`주의`) Top-N 선별 (z_score 절댓값 내림차순), 캐시 미스 시 `_compute_anomaly_scan_all()` 동기 계산 폴백
  - 할루시네이션 가드: `z_score`/`deviation_pct`/`current_val`/`mean_30d` + 총계 + 프롬프트 상수(0, 30)를 `allowed_numbers`에 포함, 시설명·datainfo strip 후 재검증
  - 결정적 폴백 `_build_fallback()` (LLM 실패·거부 시)
  - `ai_server.py:2674-2675` 라우터 등록
- 프런트: `AnomalyScanView.tsx:55-72,209-` — "AI 현황 요약" 버튼 + 상태/결과 카드, `explainScanAll(3)` 호출
- API 래퍼: `src/lib/api/anomaly-api.ts:67` `explainScanAll()` — 직접 fetch (장시간 LLM 호출, [E-013] 패턴)

#### P2.7 — `/equipment-mtbf/explain`
- 백엔드 `endpoints/equipment_mtbf_explain.py` (288 lines):
  - `_fetch_mtbf_top(days, sitename, facilitytype, top_n)` — `tb_equipment_alarm_report` + `tb_equipment_tag_map` + `tb_equipment_info` JOIN, `availability_pct ASC, fault_count DESC`로 최악 설비 Top-N
  - 프롬프트: 절대 규칙 7개(수치 외 숫자 금지/권고 금지/설비 유형 분포 서술 등) + 분포 Counter + Top 목록
  - 할루시네이션 가드: `fault_count/downtime_h/mttr_h/mtbf_h/avail_pct` + `days/top_n/0/100` 상수 + 유형별 카운트 → `allowed_numbers`, 식별자(sitename·facilitytype·equipmenttype·equipment_id) strip 후 검증
  - 폴백 `_fallback_summary()` (가장 심각 설비 + 유형 분포 서술)
  - `ai_server.py:2678-2679` 라우터 등록
- 프런트: `/admin/equipment-mtbf/page.tsx:110-130, 194-236` — 헤더 "AI 해석" 버튼 + 결과 카드(LLM 보라색 / 폴백 앰버)
- API 래퍼: `src/lib/api/equipment-mtbf-api.ts:69` `explainEquipmentMtbf()`

#### FAQ 예시 동적화 — `/chat/faq/examples`
- 백엔드 `endpoints/chat_faq_examples.py` (247 lines):
  - 카테고리 6종(basic/trend/anomaly/analysis/facility/alarm)별 템플릿 20여개
  - `_fetch_active_sitenames(facilitytype, datainfo_filter, days=7)` — `cagg_5min_raw_stats_ai` EXISTS로 최근 7일 실데이터 있는 시설만 선별
  - `_anomaly_sitenames()` — `_ANOMALY_SCAN_CACHE`에서 현재 이상/주의 시설 우선 추출 → 이상감지 카테고리에 우선 치환
  - `ai_server.py:2686-2687` 라우터 등록
- 프런트: `src/hooks/use-chat-faq.ts:156-188` — 1차 서버 `fetchFaqExamples("R01", 2)`, 실패 시 2차 폴백(기존 `FAQ_POOL` + autocomplete 기반 로컬 치환)
- API 래퍼: `src/lib/api/chat-api.ts:183` `fetchFaqExamples()`

#### 공통 패턴 확인
- 3건 모두 P2.3/P2.4 확립 패턴(컨텍스트 조회 → allowed_numbers → 엄격 프롬프트 → 수치 검증 → fallback) 준수
- `log_narrative()`로 LLM 통과/거부 로그 → `/admin/llm-narrative` 관찰 UI에서 집계

---

### 완료 (2026-04-13 — P2.4 태그 현재값 AI 해석 UI + P2.8 NETWORK_UPSTREAM 원인 추정)

#### P2.4 — `/tag/latest/explain` UI 삽입
- `src/components/trend/TagStatsCards.tsx` — Analog 태그 카드 헤더에 Sparkles 버튼 + Popover 추가. 팝오버 열릴 때 `explainTagLatest` 호출, loading/done/error 상태 전환. source 배지(LLM/템플릿) + `context_used` 태그 표시
- `src/lib/api/tag-latest-explain-api.ts` — 신규 API 래퍼. apiClient 대신 직접 fetch로 장시간 호출 시 전역 signOut 회피 [E-013]
- 검증: Playwright로 `/monitoring/reservoir` 접속 → 가곡(배) 수위 카드의 AI 해석 클릭 → Popover에 `context_used = [baseline_30d, peers]` + LLM 뱃지와 함께 2문장 응답 렌더 확인

#### P2.8 — NETWORK_UPSTREAM_FAULT_ANALYSIS LLM 원인 추정
- `endpoints/network_upstream_explain.py` 신규:
  - `_UPSTREAM_SQL`: `ai_server.py`의 NETWORK_UPSTREAM_FAULT_ANALYSIS 쿼리와 동일 (SSLVPN 그룹별 하위 LTE 통계 + UTM 상태 + 전체 LTE 통계)
  - `_fetch_upstream_state()`: 1회 쿼리 → `(sslvpn_rows, global_stats)` 파싱, down_pct 계산
  - `_build_context_block()`: UTM/전체 LTE/SSLVPN 그룹별 다운율 구조화
  - 프롬프트 규칙: UTM 전체 장애 → 전 구간 장애 / SSLVPN 하위 80%↑ → 집단 이상 / 전체 LTE <10% → 개별 현장 이상
  - 할루시네이션 가드: 수치 whitelist + 식별자(sslvpn_id, down_sites) strip 후 재검증. 프롬프트 상수 `10.0/80.0/100.0`도 whitelist에 포함 (규칙 문구 인용 시 오탐 방지)
  - 결정적 템플릿 폴백 `_fallback_summary()` — LLM 실패·거부 시 사용
- `ai_server.py`: import + `init_network_upstream_explain(get_db_connection, ollama_client)` + router 등록
- 프런트 통합:
  - `BotMessage.tsx` — `intent` prop 추가 + `NetworkUpstreamExplainSection` 컴포넌트. intent가 `NETWORK_UPSTREAM_FAULT_ANALYSIS`일 때 visual 아래에 "AI 원인 추정" 버튼 + 결과 박스 렌더
  - `ChatMessageArea.tsx` — `intent={msg.bot.intent}` 전달
  - `src/lib/api/network-upstream-explain-api.ts` — `explainUpstreamFault()` 래퍼 (직접 fetch)
- 검증: `POST /network/upstream-fault/explain` 실호출
  - 응답 예: "전체 LTE 모뎀 다운율이 6.1%로 10% 미만이기에 상위 장비는 정상이고 개별 현장 이상입니다. 당진시청 SSLVPN 하위 LTE 중 남산10, 행정2-2 현장이 다운되었습니다."
  - `context_used=["network_status_latest","network_link"]`, `sslvpn_count=1`, `global_lte_down_pct=6.1`
  - 초기 첫 응답에서 `10.0`이 whitelist에 없어 거부 → 프롬프트 규칙 상수 추가로 해결
- 사양 반영: `docs/slm-api-contract-final.md` AI 서술 섹션에 `/network/upstream-fault/explain` 추가

---

### 완료 (2026-04-13 — P2.3 피어 태그 비교 컨텍스트 확장)

#### 목적
같은 시설 유형의 다른 현장 동종 태그를 "피어"로 비교 서술해, 운영자가
"이 배수지가 다른 배수지 대비 어느 수준인지"를 AI 응답 한 문장으로 파악할 수
있게 한다. 단일 태그·트렌드 구간 모두 확장 대상.

#### 구현
- `endpoints/trend.py`:
  - 카테고리 키워드 추출기 `_extract_peer_category(datainfo)` — 순시유량/토출압력/수위/유량/압력 등 긴 키워드 우선 매칭
  - 실측 필터 `_is_peer_candidate_datainfo(datainfo)` — 설정/알람/HH/LL/H/L/상태/고수위/저수위 계열 datainfo 배제
  - `_fetch_peer_context(tagsn, limit=5)` — 동종 카테고리·타현장 태그 최대 5개 baseline 조회 (DISTINCT ON sitename로 1 현장 1 태그), 30일 avg의 평균 = `peer_avg_of_avgs`
- `/trend/explain` 엔드포인트 (`trend.py`):
  - 컨텍스트 조회에 peer 블록 추가, `context_used += ["peers"]`
  - 허용 수치 화이트리스트에 각 피어의 avg/min/max + `peer_avg_of_avgs` + `peer_count` 포함
  - 프롬프트 확장: 피어 비교 섹션 + 규칙 8 ("피어 비교 시 1문장 추가 서술, 제공된 시설명·수치만 사용")
  - 문장 수 `2~3` → `3~4`로 확대
  - 식별자 strip 목록에 피어 `sitename`/`datainfo` 추가 → 수치 검증 오탐 방지
- `/tag/latest/explain` 엔드포인트 (`tag_latest_explain.py`):
  - 동일 피어 컨텍스트 재사용 (`_fetch_peer_context` import)
  - 문장 수 `1~2` → `2~3`로 확대 (피어 블록 있을 때만)
  - strip 후 재검증 로직 신규 추가 (기존엔 없던 2차 검증)

#### 검증
- 가곡 배수지 수위 태그(`44270_24904_LEI_N001`, 현재 1.04m) 실호출:
  - `/tag/latest/explain` → "현재값은 1.04로 지난 30일 평균인 0.781보다 높습니다. 현재값은 피어 평균인 1.15보다 낮습니다." (3문장, `context_used=["baseline_30d","peers"]`, allowed=21)
  - `/trend/explain` → "값이 0.93~1.05m 범위에서 평균 0.98m로 유지되었습니다. 이는 지난 30일 평균인 0.781m 대비 높은 편입니다. 이번 구간 평균은 피어 평균인 1.15m 대비 낮은 편입니다." (3문장, allowed=24)
- 초기 필터 버그: `"수위1 H"` 변수 태그가 `" H "` 필터를 우회해 피어 평균이 0.025m로 잘못 집계 → exclude 키워드에 `" H"`/`" L"` (leading space만) 및 `H2/L2/H3/L3` 추가해 해결
- 사양 반영: `docs/slm-api-contract-final.md` AI 서술 섹션에 P2.3 블록 추가 (공통 컨텍스트 명세)

---

### 완료 (2026-04-12 — API 사양 동기화 + LLM 서술 관찰 UI)

#### 2단계: API contract 동기화
- `docs/slm-api-contract-final.md` 8절 "전체 API 엔드포인트"에 누락된 7개 신규 엔드포인트 반영:
  - `GET /admin/equipment-mtbf`, `GET /admin/llm-narrative/stats`
  - `GET /alarm/calendar`
  - `GET /leak-cusum/alerts`, `PATCH /leak-cusum/alerts/{id}/ack`, `POST /leak-cusum/scan`
  - `GET /chat/faq/examples`
  - AI 서술 섹션 신설: `/anomaly/scan-all/explain`, `/equipment-mtbf/explain`, `/tag/latest/explain` (공통 응답 스키마 + 수치 화이트리스트 정책 명시)
- 기존 `/chat/feedback`, `/admin/facility-alias`, `/anomaly/explain`, `/trend/explain`, `POST /tags`는 이미 문서화돼 있어 손대지 않음

#### 3단계: LLM 서술 관찰 UI (M100-10)
- 신규 페이지: `src/app/(dashboard)/admin/llm-narrative/page.tsx`
  - 요약 카드 4종: 총 호출 수, **LLM 통과율** (≥95% emerald / ≥80% amber / <80% red), **할루시네이션 거부율** (≤5% emerald / ≤15% amber / >15% red), 컨텍스트 모드
  - 엔드포인트별 상세 테이블: 호출 수 / LLM / 폴백 / 통과율 / 평균 LLM 시간 / 평균 컨텍스트 조회 시간
  - 기간 선택 (1/7/14/30/90일) + 수동 새로고침
- API 래퍼: `src/lib/api/llm-narrative-api.ts` — `fetchLlmNarrativeStats(days)` → `GET /admin/llm-narrative/stats?days=N`
- 메뉴 등록: `sidebar-menus.ts` M100-10 "LLM 서술 관찰" → `/admin/llm-narrative`
- 검증: Playwright 브라우저로 실제 로그인 후 페이지 렌더 확인 — 31건 호출 / 96.8% 통과율 / 3.2% 거부율 / 5개 엔드포인트 표시 정상

---

### 완료 (2026-04-12 — 배수지 모니터링 빈 차트 + 테스트 환경 tag 수집 데몬 추가 [E-014])

#### 증상
- `/monitoring/reservoir` 등 배수지/가압장/블록 모니터링 페이지에서 최근 24시간 차트가 전부 비어 있음
- `/trend/data` 요청은 200 OK지만 `total_points=0`
- 전체 2700개 태그의 최신 logtime이 `2026-04-11 11:42`에 고정 (약 35시간 전)

#### 원인 (3단 복합)
1. **Node-RED DB 접속 정보 오류** — `flows.json`의 로컬 postgres config가 `host=172.17.0.1:5433` (Docker bridge gateway + 외부 포트)로 설정되어 같은 compose 네트워크에서 접근 불가
2. **테스트 환경에 데이터 수집 파이프라인이 없음** — Node-RED flows 324개 postgres 노드 중 `tb_tag_raw_data`에 INSERT하는 노드는 0개. 프로젝트 전체에서 `INSERT INTO tb_tag_raw_data`는 스키마 dump 파일에만 존재
3. DB dump 로드 이후 증분 수집이 없어 데이터가 정체됨

#### 수정
1. **`slm-node-red:/data/flows.json`** — `postgreSQLConfig[71827310c941a9d1]`: `host=slm-timescaledb`, `port=5432` 로 변경 후 Node-RED 재시작 → 알람/조건 판정 로직 복구
2. **테스트 전용 데이터 수집 데몬 추가 (⚠ 납품 시 제거):**
   - `/Users/jykim/slm/dev_tools/tag_ingest.py` — 원격 운영 DB(`112.166.183.65:25479`) → 로컬 `tb_tag_raw_data` 주기 복제 Python 데몬. backfill 48h + poll 30s, `ON CONFLICT DO NOTHING`, 원격 tz-naive KST → 로컬 timestamptz 변환, SIGTERM graceful shutdown
   - `/Users/jykim/slm/dev_tools/Dockerfile.tag_ingest` — python:3.12-slim + psycopg2-binary
   - `docker-compose.dev.yml`에 `dev-tag-ingest` 서비스 블록 추가 (환경변수로 모든 파라미터 오버라이드 가능)
   - 사양: `docs/dev-tag-ingest-spec.md`
   - 에러 이력: `docs/error-management.md` [E-014]

#### 검증
- `docker compose up -d --build dev-tag-ingest` 후 로그에서 "복제 시작 watermark=..." 확인
- 로컬 max(logtime)이 1분당 2~3시간씩 전진 (backfill 완료까지 ~15분)
- 이후 POLL_INTERVAL_S 주기로 원격과 격차 유지

#### ⚠ 납품 시 제거 체크리스트
- `/Users/jykim/slm/dev_tools/` 디렉토리 전체 삭제
- `docker-compose.dev.yml`의 `dev-tag-ingest` 서비스 블록 삭제
- 원격 DB 자격 정보(`112.166.183.65:25479`, `DJpost0827///`) 흔적 grep 후 완전 제거
- `docs/dev-tag-ingest-spec.md` 삭제 또는 archive
- 운영 환경은 실 PLC/Node-RED 수집 파이프라인을 사용하므로 이 데몬 불필요

---

### 완료 (2026-04-12 — AI 요약 클릭 시 로그인 화면 튕김 버그 수정 [E-013])

#### 루트 원인
- `src/lib/api-client.ts` `handleError`가 401 응답을 받으면 무조건 `signOut({callbackUrl:"/login"})` 호출
- AI 현황 요약(`/anomaly/scan-all/explain`)과 AI 원인 분석(`/anomaly/explain`)은 LLM 호출로 40~60초 걸리는 긴 요청
- 이 사이 JWT 만료 구간에 걸리면 백엔드 401 → 전역 핸들러가 즉시 로그아웃 → 사용자가 "버튼 클릭 → 첫 화면 튕김"으로 체감
- 다른 짧은 호출들은 토큰 갱신 버퍼(5분) 안에 끝나서 같은 문제가 드러나지 않았음

#### 수정
- **`src/lib/api-client.ts:72-85`** — 401 응답 시 `signOut` 호출 제거, `ApiError`만 throw. 전역 signOut 책임을 NextAuth JWT refresh 실패 → `SessionGuard`로 단일화 (역할 분리)
- **`src/lib/api/anomaly-api.ts`** — `explainAnomalyCause`/`explainScanAll` 두 함수를 `apiClient` 대신 직접 `fetch` 호출로 전환 (이중 방어). apiClient import 제거
- 검증: Playwright 헤드리스 5회 반복 테스트 전부 PASS — URL `/chat` 유지, AI 요약 결과 정상 렌더
- 상세: `docs/error-management.md` [E-013]

#### 교훈 (재발 방지)
- 새 API 래퍼 추가 시 401→전역 로그아웃 유혹을 피한다. 실패는 컴포넌트 로컬 에러 상태로만 표현
- 장시간 LLM 호출 엔드포인트는 개별 에러 핸들링 경로를 가져야 함
- 전역 세션 관리 책임은 `SessionGuard` 한 곳에만 둔다

---

### 완료 (2026-04-11 — Docker 환경 이전 + 수위 원인 분석 + 알람 자동 해제 + 트렌드 수정)

#### Docker 개발 환경 이전
- **docker-compose.dev.yml**: TimescaleDB + Node-RED + Backend(FastAPI) + Frontend(Next.js) 통합
- **Backend Dockerfile**: Python 3.12-slim, 볼륨 마운트(hot-reload), uvicorn --reload
- **Frontend Dockerfile**: Node 22, HTTPS (mkcert 인증서), Turbopack
- **INTERNAL_API_URL**: 서버사이드 프록시 502 수정 (Docker 내부: `backend:8000`)
- **OLLAMA_MODEL**: `gemma4` → `gemma4:26b` (실제 모델명)
- `docker compose -f docker-compose.dev.yml up -d` 로 전체 스택 실행

#### RESERVOIR_LEVEL_CAUSE_ANALYSIS 인텐트 추가
- "배수지 수위 하락/상승 이유" 질문 → Node-RED 수위 조건 로직 기반 원인 분석
- 조건 체크: 상류 펌프 상태, 밸브 상태, 유입/유출 균형, 공급가능시간
- sql_executor.py: `_execute_level_cause_analysis` (10개 헬퍼 함수)
- block_builder.py: `build_level_cause_block` (시맨틱 마커 포맷)
- intent_classifier.py: "수위" + "이유/원인/왜" 우선 키워드 매칭

#### 오래된 알람 자동 해제
- **문제**: Node-RED가 과거 '진행중' 알람을 소급 해제하지 않음 (죽동 배수지 132건 미해제)
- **해결**: `_alarm_release_loop` 백그라운드 (2분 주기) — DI 태그 최신값=0이면 자동 해제
- **결과**: 전체 194건 → 52건 (142건 해제), 죽동 132건 → 4건 (LL 실제 활성만 유지)

#### 트렌드 AI 요약 빈 응답 수정
- **원인**: gemma4:26b 모델이 `options.num_predict` 지정 시 빈 응답 반환
- **해결**: `num_predict=150` → `None` (모델 기본값 사용)

#### response_builder 추가 분할 + facility_crud 분할
- response_builder.py (3,908줄) → response_builder(2,170) + sql_executor(1,138) + block_builder(707)
- facility_crud.py (1,551줄) → facility_crud(329) + facility_types_crud(1,252)

---

### 완료 (2026-04-11 — ai_server.py 모듈 분리 리팩토링 Phase 1+2+3)

#### 개요
- **목표**: ai_server.py 단일 파일(13,256줄)을 기능별 모듈로 분리하여 유지보수 가능한 구조로 전환
- **결과**: 13,256줄 → **5,650줄** (57.4% 감소), 신규 모듈 9개 생성
- **검증**: 구문 검증 11/11 PASS, API 테스트 10/10 PASS (DB 연결 포함)

#### Phase 1 — 독립 CRUD 모듈 분리 (13,256 → 11,562줄)
- `endpoints/trend.py` (313줄) — `/trend/data`, `/trend/explain`, `/trend/facility-sparkline`
- `endpoints/causal.py` (462줄) — `/causal/rules`, `/causal/verify`, `/causal/chain/*`, `/causal/estimate-lag`
- `endpoints/alarm_crisis.py` (860줄) — `/monitoring/alarm-notifications`, `/alarm/*`, `/crisis/*` (12개 엔드포인트)
- `endpoints/tags.py` (194줄) — `/tags`, `/tags/filters`, `/tags/groups`
- `shared/timeseries.py` (103줄) — TimescaleDB 청크 쿼리 공용 헬퍼 (trend, causal, flow_realtime 공유)

#### Phase 2 — 대시보드 + 실시간 분리 (11,562 → 10,555줄)
- `endpoints/dashboard.py` (353줄) — `/dashboard/overview`, `/monitoring/dashboard`
- `endpoints/flow_realtime.py` (855줄) — `/flow-map/realtime`, `/gis/facility-info`, `/flow-map/node-alarms`, `/equipments/auto-map`

#### Phase 3 — 응답 빌더 + 이상감지 + 관리자 분리 (10,555 → 5,650줄)
- `response_builder.py` (3,908줄) — JSONB 파서, 템플릿 렌더러, 응답 빌더, 블록 빌더, SQL 실행기, process_sql_result
- `anomaly_scan.py` (646줄) — `_compute_anomaly_scan_all` + 데이터 품질/설비 장애 감지 헬퍼
- `endpoints/admin.py` (645줄) — `/health`, `/models/*`, `/admin/facility-files/*`, `/admin/site-settings`

#### 아키텍처 문서
- `slm/docs/ai_server_architecture.md` — 전체 폴더 구조, 모듈 의존관계, 엔드포인트 매핑, 개발 가이드

#### ai_server.py 코어 (5,650줄)에 남은 기능
- AI 채팅 `/ask`, `/ask/stream` 핸들러 (인텐트 매칭 + SSE 스트리밍)
- 인과 체인 템플릿/인덱스 (글로벌 상수)
- 백그라운드 캐시 루프 (anomaly_scan, flow_balance, iforest)
- lifespan/미들웨어/데모 모드
- DB 커넥션 풀 + execute_sql
- `/csv/{filename}`, `/autocomplete/candidates`

---

### 확인 완료 (2026-04-10 — 미들웨어 인증 정리 점검)

#### 점검 결과
- **미들웨어**: 기본 전체 인증 적용, 읽기전용 GIS(tiles/layer/pipelines)만 예외 — 정상
- **API 프록시**: PUBLIC_PATHS 화이트리스트(login/refresh/health/models)만 우회 — 정상
- **GIS coordinates 쓰기**: PUT/POST/DELETE에 `requireSession()` 적용 완료 (4/9 커밋)
- **테스트 우회/bypass 패턴**: 발견 안 됨

#### 프로덕션 배포 전 남은 항목
- → 로드맵 단기 D항목으로 통합

---

### 로드맵 요약 (2026-04-12 재검증 기준)

> 2026-04-12 실제 코드베이스 대조 검증 완료. 완료 항목은 `[x]` + 근거 파일/라인 추가.
> 검증 범위: `/Users/jykim/web` (Next.js) + `/Users/jykim/slm` (Python AI 서버)

#### 단기 (Phase 0 — Gemma4:26b, 즉시 적용)

**A. AI 인텐트 품질 개선**
- [x] 오타/구어체 처리 — example3.json 501→744 질문 확장 (74 인텐트)
  - 근거: `/Users/jykim/slm/example3.json` 744 questions / 74 intents (목표 600+ 초과)
  - 약칭 정규화: `korean_fuzzy.py`(한글 자모분해 퍼지) + `param_extractor.py` 3단계 fuzzy fallback (sitename L331-346, facilitytype L529-545, datainfo L579-596)
- [x] 오분류 피드백 수집 — "원하는 답이 아닌가요?" 클릭 → DB 저장 (수동 검토 게이트)
  - 근거: `db/init/03_tables_chat.sql` `tb_ai_chat_feedback` 테이블 (self-contained: 질문/답변/인텐트 복사본)
  - Python: `endpoints/chat_feedback.py` (`/chat/feedback` POST/GET/PATCH) + `ai_server.py:106,2524-2525` 라우터 등록
  - Next.js: `src/lib/api/chat-api.ts` `submitChatFeedback()` + `src/components/chat/BotMessage.tsx` 피드백 버튼 UI + `chat/page.tsx:handleSubmitFeedback`
  - Store: `chat-store.ts:markFeedbackSubmitted` 플래그 localStorage 저장 (중복 제출 방지)
  - 검토 UI: `src/app/(dashboard)/admin/chat-feedback/page.tsx` — 인텐트별 집계 + 미검토/검토완료/전체 탭 필터 + 행 펼침(질문/응답/의견) + 검토 완료 버튼
  - 메뉴: `sidebar-menus.ts` M100-7 "채팅 피드백" 추가
- [x] 날짜 표현 파싱 확장 — 상대 시점 프로그래밍 추출 (SLM fallback 회피)
  - 근거: `param_extractor.py:_extract_date_programmatic` 확장 — 어제/그저께/엊그제, 지난주/저번주, 이번달/이번 달 초/이번 달 말, 지난달/저번달/전달, 작년/지난해, 올해/금년 (17개 패턴)
  - `_DATE_KW` 키워드 리스트에 그저께/엊그제 추가 (L197)
  - `slm_date` 로그 플래그 정확화: `_slm_date_called` 실제 호출 여부 추적 (L204, L308)
  - 검증: 프로그래밍 추출 성공 시 Phase2 < 5ms (SLM 호출 없음), 17개 패턴 모두 정상 변환
- [x] 시설명 약칭 매핑 테이블 — DB 기반 약칭→sitename 사전
  - DB: `tb_facility_alias` (alias, sitename, priority, note, use_yn) + unique index on (region, alias)
  - Python: `ai_server.py:load_facility_aliases_from_db()` + `_reload_facility_aliases()` (CRUD 후 런타임 즉시 반영)
  - `ParamExtractor.__init__`에 `facility_alias_map` 추가, `_extract_sitename`에서 fuzzy 단계 전 exact lookup (긴 alias 우선)
  - CRUD API: `endpoints/facility_alias.py` (`/admin/facility-alias` GET/POST/PATCH/DELETE)
  - Next.js: `src/app/(dashboard)/admin/facility-alias/page.tsx` — 검색/추가/수정/삭제 UI, `facility-alias-api.ts` 클라이언트, M100-8 메뉴 추가
  - 검증: "합일" → "합덕일반" alias 등록 → `/ask`에서 "합일 수위" 질의 시 자동 치환 + `corrections` 표출 확인
- ⏸ 프롬프트 구조 최적화 — Gemma4:26b few-shot 설계 **장기 보류** (→ 아래 "장기(Phase 2)" 참조)

**B. 이상감지 Phase 3 (설비 역추적)**
- [x] 이상 태그 → 연결 설비 → 종합 진단 연동
  - 근거: `EquipmentDiagnosis` 타입(`src/lib/types/chat.ts`) + `chat-response-mapper.ts` equipment_diagnosis 필드 매핑 + `src/components/chat/anomaly/AnomalyDetailView.tsx:247-255` 설비 건강 진단 렌더링
  - Python 측: `build_success_response()` equipment_diagnosis kwarg, /ask /ask/stream 핸들러 전달 (commit c7e69a3, 2026-04-09)
- [x] ai_server.py 이상감지 pass 스텁 → tb_tag_group_map 실구현
  - 근거: `anomaly_scan.py:243-250` DI 장애 태그 조회 (COMM_ERROR/EQUIP_FAULT/POWER_FAULT) `tb_tag_group_map` JOIN
  - `sql_executor.py:517-544` group_code 기반 tb_tag_group_map JOIN 분기
  - `ai_server.py` 11건의 tb_tag_group_map 참조 (L402-1375)

**C. 할루시네이션 방어 레이어**
- [x] Entity 검증 레이어 — LLM 추출값 → DB 퍼지매칭 → 실제 ID 치환
  - 근거: `query_validator.py` `unknown_sitename` / `missing_*` 검증 (L24-49, CORRECTION_TEMPLATES) + `param_extractor.py` fuzzy fallback 3종 (sitename/facilitytype/datainfo) + `korean_fuzzy.find_best_match`
- [x] 값 주입 프롬프트 — DB 수치만 사용하도록 생성 전 제약 + 출력 검증
  - 응답 템플릿: `{placeholder}` 치환으로 DB 값 직접 주입 (`ai_server.py:3019-3129`)
  - AI 요약 LLM 경로 (`endpoints/trend.py:/trend/explain`) 강화:
    - 엄격 프롬프트 (5개 절대 규칙: 제공 수치 외 숫자 금지, 외부 지식 금지, 권고 금지 등)
    - `_extract_numbers` + `_validate_summary_numbers` — 출력에서 숫자 추출 후 허용값과 대조 (2% tolerance)
    - `_fallback_summary` — 검증 실패 또는 LLM 불가 시 결정적 템플릿 요약 (할루시네이션 0)
    - 응답에 `source: "llm" | "fallback"` + 실패 시 `llm_rejected`/`violations` 포함
  - 검증: 일반/제로/이상구간 3종 E2E, 단위 테스트 5종 (할루시네이션 감지 + tolerance + ignore_words)
- [x] SQL 생성 완전 차단 — SQL_TEMPLATES dict 고정
  - 근거: 모든 SQL은 `intent_def.get("sql", "")`에서만 로드 (`ai_server.py` L2912, L4434, L2337), LLM SQL 생성 경로 없음. `execute_sql(sql_template, params)` 만 사용 (L2549)
- [x] 시맨틱 마커 일관 적용 — `<<ok>>` `<<warn>>` `<<error>>`
  - 근거: `block_builder.py:15-20 wrap_status_marker` + L36-57 `_alarm_category_marker`/`_alarm_msg_marker` + `response_builder.py:1282 _STATUS_MARKER_MAP` (`ai_server.py` 11건 사용)

**D. 프로덕션 인증 정리**
- [x] NEXTAUTH_SECRET → 강한 무작위 값 교체 (dev 포함)
  - `.env.local`, root `.env` (gitignore) 에 `openssl rand -base64 32` 생성 값 주입
  - `docker-compose.dev.yml` 기본값 `dev-secret-change-in-prod` 유지(폴백용) + `${NEXTAUTH_SECRET:-...}` 보간
  - Frontend 컨테이너 재기동으로 실제 활성화 확인 (`printenv NEXTAUTH_SECRET` = 강한 값)
- [x] DB 크레덴셜 → 환경별 분리 구조
  - `docker-compose.dev.yml` 전체를 `${VAR:-default}` 패턴으로 변경 (timescaledb/backend/frontend 3곳)
  - `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DATABASE_URL`, `NEXTAUTH_SECRET` 등 주요 변수 오버라이드 가능
  - dev 기본값 유지 → 기존 개발 흐름 무변화, prod는 `.env` 또는 시크릿 매니저로 주입
  - `.env.example` 템플릿 3종 (루트 / slm-dashboard / slm) + `docs/deploy-secrets.md` 배포 가이드 (Docker Secrets / k8s / 외부 매니저 패턴 설명)
  - 루트 `.gitignore` 신규: `.env*` 차단 + `.env.example` 허용
- [x] setup/tags TODO 스텁 2개
  - [x] 벌크 업로드 — `src/lib/api/tag-api.ts:80 uploadTagsCsv` + `src/app/(dashboard)/setup/tags/page.tsx:335 onUpload` 연결
  - [x] 태그 생성 API — `POST /tags` (`endpoints/tags.py:200-262`) + `tag-api.ts:createTag` + `setup/tags/page.tsx:handleAdd` 실제 호출
    - 스모크 테스트: 정상 201 / 중복 409 / 삭제 정리 확인

#### 중기 (Phase 1 — 납품 서버, A30 24GB + Gemma4 12B)
- [x] 이상감지 원인 LLM 서술 생성 (4계층 탐지 완성 후) — `fbed558` (2026-04-12): `POST /anomaly/explain` + 3단 방어(프롬프트/수치 whitelist/fallback) + UX 설명 완료

#### 장기 (Phase 2 — Mac Mini Pro 또는 L40S + Gemma4 27B)
- [ ] EPANET 수리 시뮬레이션 모듈
- [ ] 멀티모달 현장 사진 분석 ("참고 의견" 전용)
- ⏸ **인텐트 68개 → 200개 확장** (Slot-Filling 구조 유지, 2단계 분류) — **장기 보류** (현재 74개, 사용자 지시로 보류. 별도 요청 시 재개)
- ⏸ **Gemma4:26b few-shot 프롬프트 최적화** — **장기 보류** (A-1 피드백 데이터 축적 후 혼동 쌍 기반으로 설계 예정)
- [x] **보고서 초안 자동 생성** — **완료** (P1 항목 AI 요약 + P2 채팅 자동초안, `docs/report-spec.md`). 잔여: Word(.docx)·서버측 puppeteer PDF·결재 체인·주간/월간 스케줄러는 별도 사양

---

### 완료 (2026-04-10 — ANOMALY_SCAN_ALL 캐시 0행 수정 + DB IPv6 연결 수정)

#### 구현 내역
- **ANOMALY_SCAN_ALL latest CTE 시간창 동적 조정** (`D:\slm\ai_server.py`, `_compute_anomaly_scan_all()`)
  - 문제: SQL의 `latest` CTE가 `now() - 3h` 고정 → DB 데이터가 3시간 이상 오래되면 0행 반환 → 종합 현황판 데이터 없음
  - 해결: SQL 실행 전 `max(bucket)` 확인, 1시간 이상 오래됐으면 `latest`/`recent_holding` CTE를 max_bucket 기준으로 패치
  - 결과: 7.8h 오래된 데이터에서 293행 정상 캐시 생성 (캐시 완료 98.9점)
- **DB 연결 IPv6 문제 수정** (`D:\slm\.env`)
  - 문제: `DB_HOST=localhost` → C:\Python313 psycopg2가 `::1`(IPv6)로 해석 → Docker DB(IPv4) 연결 실패 + `fe_sendauth: no password supplied`
  - 해결: `DB_HOST=127.0.0.1` 명시 (IPv4 강제)
  - `start-services.bat`은 WSL_IP로 오버라이드하므로 영향 없음
- **에러 기록**: `error-management.md` E-011, E-012 추가

---

### 완료 (2026-04-09 — Phase3 설비 역추적 버그 수정 + 미들웨어 인증 정리, commit c7e69a3/9465bc3/7112262)

#### 구현 내역
- **미들웨어 인증 범위 개선** (`middleware.ts`)
  - `api/gis` 전체 우회 → `tiles/layer/pipelines`(읽기전용)만 우회
  - `/api/gis/coordinates` 쓰기 작업 인증 대상 포함
- **GIS coordinates 인증 추가** (`gis/coordinates/route.ts`)
  - PUT/POST/DELETE에 `requireSession()` 추가 → 미인증 401 반환
- **Phase 3 equipment_diagnosis 응답 누락 수정** (`ai_server.py`)
  - `build_success_response()`: `equipment_diagnosis` kwarg 처리 추가
  - `/ask` 핸들러: `equipment_diagnosis=processed_data.get(...)` 추가
  - `/ask/stream` SSE 핸들러: 동일하게 추가
  - `AiServerResponse` 타입: `equipment_diagnosis?: EquipmentDiagnosis[]` 추가
- **테스트 3회** — 죽동 배수지 ✅ (PLC 55점·통신이상·주의), 합덕/갈산 정상 동작 확인

---

### 완료 (2026-04-09 — GIS 기본값 최소화 + ANOMALY_SCAN_ALL IForest ML 배지, commit 27f881c)

#### 구현 내역
- **GIS 관망도 기본값 최소화** (`gis/page.tsx`)
  - 시설목록 패널: `showFacilityList true → false` (기본 접힘)
  - 유량흐름 패널: `showFlowPanel true → false` (기본 접힘)
  - Phase1(유량두께/글로우/불균형): `showBase/showGlow/showImbalance true → false`
  - Phase2(셔머 애니메이션): `showShimmer true → false`
  - **이유**: GIS 초기 진입 화면 과부하 → 최소 표시, 사용자가 필요 시 활성화
- **ANOMALY_SCAN_ALL IForest ML 배지** (`AnomalyScanView.tsx`)
  - `AnomalyData` 타입에 `mlModelCount/mlAnomalyCount/mlAgreeCount/mlTier1Count/mlTier2Count` 추가
  - `AiServerResponse` 타입에 `ml_*` 서버 필드 추가
  - `chat-response-mapper.ts`: ML 필드 매핑 추가 (서버 응답 → 프론트엔드 타입)
  - `AnomalyScanView.tsx`: KPI 카드 위에 IForest ML 탐지 배지 섹션 추가
    - 모델 수 / Tier-1(시설 다변량) / Tier-2(태그 단변량) / ML이상 수 / Z+ML 동시이상 수

---

### 완료 (2026-04-08 — IForest v2 테스트 스위트 구축 + T4 API 필드 수정)

#### 구현 내역
- **test_iforest_v2.py** 전체 작성 (T1~T5, 총 46개 케이스)
  - T1 단위: 31/31 ✅ (_datainfo_to_group, FacilityModel.predict, _build_facility_matrix, predict_for_rows)
  - T3 예측 품질: 5/5 ✅ (정상 FP율, 누수/펌프공회전/감압실패 물리 모순 감지)
  - T5 회귀: 8/8 ✅ (v1 하위 호환, predict_single, is_trained=False 방어)
  - T4 API: 2/2 ✅ (ANOMALY_SCAN_ALL ml 필드, ANOMALY_FACILITY_DETAIL Tier 정보)
  - T2 학습: SKIP — Windows에서 WSL localhost:5433 직접 접속 불가 (서버 로그로 Tier-1 41개/Tier-2 183개 확인)
- **ai_server.py 수정** — ANOMALY_SCAN_ALL stale-while-revalidate 캐시 경로(line ~7763)에 ML 필드 누락
  - `ml_model_count`, `ml_anomaly_count`, `ml_agree_count`, `ml_tier1_count`, `ml_tier2_count` 추가
- **버그 수정** — FP율 100% (hour/dow 고정값으로 학습 → 경계값에서 이상 판정)
  - 학습 시 hour∈[0,24), dow∈[0,7) 균일 분포 사용으로 수정
- **결과**: T1(31)+T3(5)+T4(2)+T5(8) = 46/46 통과

---

### 완료 (2026-04-08 — ANOMALY_FACILITY_DETAIL 500 오류 수정 + IForest SQL 수정)

#### 구현 내역
- **증상**: 10개 시설 중 5개(합덕 배수지, 갈산 가압장, 부곡/석문 배수지, 합덕 정수장) HTTP 500 반환
- **근본 원인 1**: `propagation_trace` 변수 미초기화 → Phase 2 결과를 `build_anomaly_facility_detail_block()` 인자로 전달 시 NameError
  - 수정: `propagation_trace = None` 초기화 블록에 추가 (`ai_server.py` line 6510)
- **근본 원인 2**: `_diagnose_equipment_for_tags()` SQL에 `e.ip_address, e.has_ip` 컬럼 참조 — `tb_equipment_info`에 존재하지 않는 컬럼
  - 수정: SELECT에서 해당 컬럼 제거, 네트워크 상태는 `tb_network_status` JOIN으로 대체
- **근본 원인 3**: `verify_causal_context` import가 외부 if 블록 안에서만 실행 → `_run_causal()` 클로저 내에서 미접근
  - 수정: import를 클로저 내부로 이동
- **숨겨진 원인**: Windows `localhost` → `::1` (IPv6)로 해석 → 구버전 서버(PID 15184)에 라우팅
  - 수정: 구버전 서버 강제 종료, `.env.local` `NEXT_PUBLIC_API_URL=http://127.0.0.1:8000` 변경
  - `start-services.bat` 포트 정리 후 2초 대기 추가
- **IForest SQL**: `anomaly_iforest.py` `date_trunc('5 minutes', ...)` → `time_bucket('5 minutes', ...)` (E-010)
- **결과**: 10/10 OK, avg 3.0s, max 3.7s (수정 전: 5/10 OK + 5/10 HTTP 500)

### 완료 (2026-04-08 — ANOMALY_SCAN_ALL 성능 개선 + 임베딩 keep-warm, commit 2ab1c90)

#### 구현 내역
- **증상**: "전체 센서 점검해줘" (ANOMALY_SCAN_ALL) → 캐시 미스 시 102초 대기
- **근본 원인**: cagg_5min_raw_stats_ai 34M행 통계 집계 (stats_global 단독 4.4s), TTL 만료 시 유저가 SQL 직접 실행
- **수정 1 — stale-while-revalidate**: 캐시 있으면 TTL 무관 즉시 반환, 캐시 없으면 "준비 중" 안내
- **수정 2 — 초기 딜레이 단축**: 프로파일링 대기 최대 120s → 30s
- **수정 3 — 임베딩 keep-warm**: snowflake-arctic-embed2 /api/embed 더미 요청 4분 주기 추가 (첫 임베딩 3s 지연 방지)
- **결과**: 102초 → 0.4초 (캐시 히트 기준)

### 완료 (2026-04-08 — AI 채팅 트렌드 PlotChart AI 요약, commit a644cf9)

#### 구현 내역
- **목표**: 트렌드 페이지 BrushToolbar AI 요약 기능을 AI 채팅 트렌드 차트에도 적용
- **수정 파일**: `src/components/chat/PlotChart.tsx`
  - 마운트 시 `trend_explain_enabled` 시스템설정 확인
  - 첫 번째 아날로그 시리즈 min/max/avg 계산 → `/api/proxy/trend/explain` 자동 호출
  - 차트 하단 파란 박스로 요약 표시 (Loader2 → Sparkles 아이콘)
  - `plot.period.from/to` 변경 시 재요약 (네비게이션 이동 제외)

### 완료 (2026-04-08 — NETWORK_UPSTREAM_FAULT_ANALYSIS 구현, commit 0cc6bd4+8f39555)

#### 구현 내역
- **목표**: "현장 LTE모뎀이 다 접속 안 되면 상위 UTM/SSLVPN 문제" 도메인 지식 기반 인텐트
- **인텐트 분류** (`intent_classifier.py`): "SSLVPN/UTM/상위 장비/다 통신이상/LTE 전부" → keyword 즉시 확정
- **SQL** (`ai_server.py`): MAX(check_time) CTE + equipmenttype 필터 + bool_or(is_alive)
  - sslvpn_summary CTE: LTE 모뎀 → SSLVPN 연결 집계, down_sites array_agg
  - utm_info CTE: UTM 전체 상태 집계
  - 결과: UTM/SSLVPN 정상 여부 + 이상 현장명 목록 출력
- **임베딩**: example3.json 15개 한국어 질문 정상화, 723벡터로 재구축
- **버그 수정**: MAX(boolean) → bool_or() (commit 8f39555)

### 완료 (2026-04-08 — AI 요약 응답속도 개선, commit 3fa7860)

#### 구현 내역
- **증상**: 트렌드 AI 요약 ~19초, 실패 잦음
- **근본 원인 1 (가장 큰)**: WSL에서 `localhost` 연결 시 IPv6(`::1`) 먼저 시도 → 2초 타임아웃 → IPv4 폴백
  - 모든 FastAPI 호출에 2.2초 오버헤드 존재 (AI 채팅 포함)
  - 수정: `uvicorn.run(host="::")` 듀얼스택 바인딩
  - 효과: localhost 응답 2.2s → 0.28s
- **근본 원인 2**: Ollama 5분 비활성 후 모델 언로드 → 재로드 9.5초
  - 수정: `_ollama_keepwarm_loop()` — 4분 주기 1-token 더미 요청으로 VRAM 유지
- **근본 원인 3**: `ollama_client.generate()` 동기 호출 → 이벤트 루프 블로킹
  - 수정: `asyncio.to_thread()` 래핑
- **추가**: `ollama_client.generate()` num_ctx/num_predict/timeout/backoff_seconds override 지원
  - explain: `num_predict=150`, `timeout=30s`, `backoff_seconds=10` (분류기 60s 독립)
- **결과**: 19s → 6s (3배 개선)

### 완료 (2026-04-08 — 인텐트 분류 버그 수정: "트렌드" 쿼리 FACILITY_TREND 강제 매핑)

#### 구현 내역
- **증상**: "한달간 신평배수지 수위 트렌드 보여줘" → FACILITY_TAG_DATA_TABLE(표) 반환
- **근본 원인 1**: `intent_classifier.py` `_classify_intent()` 391-404라인
  - "N일" 기간 패턴 + "수위/압력/유량" → 카테고리 무관하게 `FACILITY_TAG_DATA_TABLE` 반환
  - `normalize_question()`이 "한달간" → "30일간" 변환 후 `\d+\s*일` 패턴 매칭
  - 수정: `_TAG_DATA_EXCLUDE`에 `"트렌드", "트랜드", "추이", "그래프"` 추가
- **근본 원인 2**: `ai_server.py` `match_intent()` (최종 폴백 함수)
  - example3.json 질문을 `normalize_for_matching()`만 적용하고 `normalize_question()` 미적용
  - "30일간"(normalize_question 결과) vs "한달간"(원본 예시) 불일치 → 점수 오매칭
  - 수정: example 질문에도 `normalize_question()` 적용 후 비교
  - 추가: "트렌드" 포함 시 `FACILITY_TREND` 즉시 반환하는 우선 규칙
- **적용 파일**: `D:\slm\intent_classifier.py`, `D:\slm\ai_server.py`
- **테스트**: "한달간/30일간/7일간 ... 트렌드" → FACILITY_TREND, graph_type=plot ✓

### 완료 (2026-04-08 — ECharts smoothMonotone 적용)

#### 구현 내역
- **목표**: ECharts 트렌드 라인을 Recharts처럼 부드러운 곡선으로 표시
- **방법**: `smooth: true/0.3` 기존 설정 유지 + `smoothMonotone: 'x'` 추가
  - `smoothMonotone: 'x'` = monotone 보간 (Recharts `type="monotone"` 동일 알고리즘)
  - 오버슈팅(실제 데이터 범위 초과) 방지 — 수위/압력/유량 물리량에 필수
- **적용 파일** (commit `3217876`):
  - `plot-chart.ts`: `buildAnalogSeries()` + dual panel analog 시리즈 (디지털 step 시리즈는 제외)
  - `reservoir-chart.ts`: 배수지 수위 시리즈
  - `pressure-chart.ts`: 감압시설 1차측/2차측 압력 시리즈
  - `booster-chart.ts`: 가압장 토출압력/유량 시리즈
  - `StddevAnalysisView.tsx`, `StddevMultiAnalysisView.tsx`: 표준편차 분석 라인
  - `LeakCusumView.tsx`: 야간최소유량 + CUSUM 라인
- **제외**: 디지털 시리즈(`step: 'end'`), 기준선/임계값(수평 직선), bar/pie 차트

### 완료 (2026-04-08 — 트렌드 AI 요약 설명)

#### 구현 내역
- **`POST /trend/explain`** (ai_server.py) 신규 엔드포인트
  - gemma4:latest로 선택 구간 수치·패턴 2문장 요약 (권고 없음)
  - 요청: tag_name, unit, from_ts, to_ts, min/max/avg/count, anomaly_count
  - TREND_EXPLAIN_ENABLED 설정 DB 조회 → 비활성 시 거부
- **`GET/PUT /admin/site-settings`** — `trend_explain_enabled` 필드 추가
  - `tb_grp_code` SITE_SETTING 그룹 UPSERT 보장 (FK 충족)
  - `tb_comm_code` TREND_EXPLAIN_ENABLED UPSERT (create_dt/update_dt 제거)
- **`site-settings/page.tsx`** — AI 카드에 "트렌드 AI 요약" 토글 추가
  - Ollama 미연결 시 토글 비활성화
- **`BrushToolbar.tsx`** — "AI 요약" 버튼 + 로딩 스피너 + 결과 텍스트 표시
  - `ExplainState` 타입: idle / loading / done / error
  - 카드 너비 w-64 → w-72 확장 (요약 텍스트 공간 확보)
- **`TrendChart.tsx`** — explain 로직 통합
  - 마운트 시 `/api/proxy/admin/site-settings` 1회 조회 → `trendExplainEnabled` 상태
  - 브러시 새 구간 선택 시 이전 요약 초기화
  - `handleExplain()`: `/api/proxy/trend/explain` POST → `explainState/Summary` 관리
  - BrushToolbar에 새 props 전달
- **Playwright 테스트**: 영역 선택 → AI 요약 버튼 → "AI 분석 중..." → 2문장 요약 표시 확인

### 완료 (2026-04-07 — 기타 소규모 수정 3종)

#### 구현 내역
- **`zero_flow` 한글 레이블 적용** (commit `562da48`)
  - `GisFacilityCard.tsx` `MISMATCH_KO` 맵에 `zero_flow: "유량값 0"` 추가
  - `FlowMonitoringGraph.tsx` 툴팁 레이블 `"상류 유량 활성 + 하류 유량 0"` → `"유량값 0"` 통일
- **GisTrendPopup 두번 갱신 버그 수정** (commit `a6f3054`)
  - **원인**: 2단계 cascading useEffect — `useEffect1`이 `setTags()` 호출 → `useEffect2`([tags, activeRange])가 트렌드 조회 → 두 번의 render cycle이 사용자에게 "두번 갱신"으로 인식
  - **수정**: 두 useEffect를 단일 async IIFE로 통합 — tag 조회 후 연속으로 trend 조회, `setTags + setTagDataMap + setLoading(false)` 동일 함수 내 호출 → React 18 automatic batching으로 1회 커밋
  - 의존 배열: `[sitename, facilitytype]` + `[tags, activeRange]` → `[sitename, facilitytype, activeRange]` 단일화
  - 동일 패턴이 적용되는 용수 흐름 팝업도 동일 효과 (데이터 연속 패치)
- **package.json HTTPS 스크립트 정리 + GIS 좌표 업데이트** (commit `7e5902d`)
  - `dev`/`dev:fast` → HTTPS 기본, HTTP는 `dev:http`/`dev:http:fast`로 명칭 통일
  - `gis-facility-coords.json`: _comment 업데이트, PDF 지도 기반 배수지·가압장 좌표 재확정

### 완료 (2026-04-07 — ai_server.py 모듈 분리: 3개 endpoint 모듈 추출)

#### 구현 내역
- **`endpoints/monitoring_catalogs.py`** (312줄): `/monitoring/catalogs/*` 7개 엔드포인트
  - GET sites, site-groups, reference, catalogs (목록), POST/PUT/DELETE catalogs
- **`endpoints/flow_map_crud.py`** (319줄): `/flow-map` CRUD 7개 엔드포인트
  - GET (전체), roots, downstream, POST, DELETE, export/csv, import/csv
- **`endpoints/csv_import.py`** (636줄): CSV 일괄 가져오기 6개 엔드포인트 + 헬퍼 함수
  - tags, equipments, reservoirs, boosters, pressure-reducing, blocks
  - `_csv_cell/float/int/bool/json_array` 헬퍼 함수 이동
- **`ai_server.py`**: 13,921줄 → 12,724줄 (-1,197줄), 기존 패턴 동일하게 초기화
  - `from endpoints.X import router as X_router, init as init_X`
  - `init_X(get_db_connection); app.include_router(X_router)`
- 실시간/AI 로직 의존 엔드포인트(`/flow-map/realtime`, `/equipments/auto-map` 등)는 ai_server.py 유지

### 완료 (2026-04-07 — GIS 유량 흐름 통합 고도화: 노드 물수지 히트맵 + 카드 팝업 연동)

#### 구현 내역
- **`GisFlowOverlayLayer.tsx`** Phase 4 추가 — 노드 물수지 히트맵
  - `buildNodeImbalanceGeoJson()`: 불균형 엣지 연결 노드 worst grade 계산 (upstream+downstream 모두)
  - `gis-node-imbalance-src` point 소스 + `gis-node-imbalance` circle 레이어
  - 경고=빨강/주의=오렌지/관심=노랑 컬러 링 (circle-stroke), 투명 채움 → 기존 마커 보존
  - `showImbalance` 토글과 연동 (불균형 dashed 라인과 동시 on/off)
- **`GisFacilityCard.tsx`** — 확장 카드 유량 불균형/교차검증 배지 추가
  - `edgeImbalance` prop: 이 시설과 연결된 불균형 엣지 계산 (upstream/downstream)
  - `imbalanceEdges`: grade/imbalance_pct/other(시설명) 목록 → "불균형 +N%" 배지
  - `cross_mismatch: true` → "교차이상 + mismatch 유형 한글" 배지 (MISMATCH_KO 맵)
- **`use-gis-facilities.ts`** — `cross_mismatches` 캡처 → 노드 `cross_mismatch`/`cross_mismatch_types` 채움
- **`gis.ts`** 타입 — `GisFacilityNode.cross_mismatch_types?: string[]` 추가
- **`gis/page.tsx`** — `GisFacilityCards`에 `edgeImbalance` prop 전달

### 완료 (2026-04-07 — 용수 흐름 ↔ 대시보드 유량 불균형 수치 통일 + KPI 레이블 명확화)

#### 원인 분석
- **유량 불균형 불일치**: `dashboard_overview()`가 `_ANOMALY_SCAN_CACHE` 내 스냅샷을 사용 (ANOMALY_SCAN 5분 + FLOW_BALANCE 30분 복합 지연 가능) → 용수 흐름 페이지의 `_FLOW_BALANCE_CACHE` 직접 참조와 타이밍 불일치
- **교차검증 이상 불일치**: 알고리즘 차이 (대시보드=AI 스캔 verdict "교차이상" 시설 수, 용수 흐름=실시간 유량 비교 노드 수) → 의도적 차이, 레이블로 명확화

#### 수정 내역
- **`ai_server.py` `dashboard_overview()`**: `flow_balance`를 `_ANOMALY_SCAN_CACHE` 스냅샷 대신 `_FLOW_BALANCE_CACHE` 직접 계산 → 용수 흐름 페이지와 동일 소스 사용
- **`dashboard/page.tsx`**: `교차검증 이상` sub "엣지 불일치 N건" → "5분 스캔 · 불일치 N건" (스캔 기반임 명시)
- **`monitoring/flow/page.tsx`**: `교차검증 이상` sub "실시간", `유량 불균형` sub "30분 갱신" 추가

### 완료 (2026-04-06 — GIS 관망도 유량 흐름 오버레이 구현)

#### 구현 내역
- **`GisFlowOverlayLayer.tsx`** (신규): MapLibre GL 직접 레이어 컴포넌트
  - Phase 1: 유량 비례 굵기(2~14px) + 색상(회색→하늘→파랑) 라인
  - Phase 2: rAF 30fps shimmer 애니메이션 (line-gradient, ANIM_SPEED=0.004)
  - Phase 3: 불균형 강조 dashed 오렌지/빨강 오버레이 (imbalance_grade 기반)
  - 레이어 ID: `gis-flow-glow`, `gis-flow-base`, `gis-flow-anim`, `gis-flow-imbalance`
  - 시설 마커 레이어 아래 삽입 (facility-clusters 이전) → 기존 아이콘/클러스터 보존
  - visible prop으로 on/off 토글 (map.setLayoutProperty visibility)
  - pendingDataRef 패턴: map load 지연 시 데이터 누락 방지
- **`use-gis-facilities.ts`** 확장: edges, flowNodes, edgeImbalance 추가 반환
  - `GisFlowEdge` 인터페이스 export
- **`monitoring/gis/page.tsx`** 헤더 토글 버튼 추가: "유량 흐름" (기존 "전체보기" 옆)
  - showFlowOverlay state (기본 true), 버튼 색상 active/inactive 구분

#### 핵심 MapLibre 제약 (재확인)
- `lineMetrics: true` source 필수 → `line-gradient` 사용 조건
- `line-gradient` 레이어는 `line-width` constant만 허용 (data-driven 불가)
  → `gis-flow-base`(data-driven width+color) + `gis-flow-anim`(constant width, gradient) 분리
- `line-cap`/`line-join` → layout 속성 (paint에 넣으면 MapLibre 4.x 검증 오류)
- shimmer p0~p2 범위가 겹치면 "ascending order" 에러 → clamp(0.0001, 0.9999) 필수

#### 필수 조건 (유지됨)
- 기존 GIS 레이어(SHP, 시설 아이콘, 클러스터, 팝업) 변경 없음
- additive only: 새 레이어 추가만, 기존 레이어 수정 없음

### 완료 (2026-04-06 — AI Server 시작 hang 수정 + start-services.bat 개선)

#### AI Server hang 원인 및 수정

**원인**: AI Server 강제 종료 시 PostgreSQL에 좀비 연결 잔류 → 테이블 락 점유
- 이전 프로세스 백그라운드 태스크(`_anomaly_scan_cache_loop`)가 `cagg_5min_raw_stats_ai` 365일 조회 쿼리 실행 중 강제 종료
- 연결이 `idle in transaction` 또는 `active` 상태로 PostgreSQL에 남아 테이블 락 점유
- 새 서버 시작 시 `_auto_classify_tags()` → `DELETE FROM tb_tag_group_map` 에서 락 대기 → 무한 hang

**수정 2종**:
1. `ai_server.py` `_auto_classify_tags()`: `SET lock_timeout = '10000'` 추가 → 10초 초과 시 예외 발생 → lifespan catch로 처리 (무한 hang 방지)
2. `start-services.bat` 2단계 추가: AI Server 시작 전 `pg_terminate_backend`로 `slm` DB의 좀비 연결 자동 정리

#### start-services.bat 개선 이력
- Docker Desktop 로직 제거 (WSL Docker로 전환)
- BOM 제거 + CP949 인코딩 저장 (한글 깨짐 수정)
- `chcp 949` 강제 설정
- `netstat | findstr` → PowerShell TcpClient 체크 → 포트 체크 제거 (WSL 관리로 불필요)
- AI Server 실행: `python` → `venv\Scripts\python.exe` (가상환경 명시)
- PostgreSQL 좀비 연결 정리 단계 추가 (2단계)
- AI Server 대기: 10초 → 15초

#### load_dotenv 수정
- `ai_server.py` `load_dotenv()` → `load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))`
- 이유: 실행 디렉토리가 다를 경우 `.env` 파일을 못 찾아 DB_PASSWORD 미설정 → DB 연결 실패

### 완료 (2026-04-05 — 이상 시설 TOP network_down 혼입 수정)

#### 원인 분석
- `tb_network_status`는 로컬 AI 서버의 `snmp_poller`가 직접 작성 (db_sync 동기화 대상 아님)
- 로컬 개발 서버는 현장 PLC/LTE 장비와 다른 망 → SNMP 폴링 전부 Timeout
- **178개 장비 전체 `is_alive=false`** (실제 장애가 아닌 망 분리 때문)
- `_detect_equipment_failures()`가 이것을 진짜 `network_down`으로 판단 → 합덕 배수지 수위 태그 전체에 마킹
- 결과: 이상 시설 TOP에 수위알람 + network_down 배지가 함께 표시 (오탐)

#### 수정 (`ai_server.py` — `_detect_equipment_failures`)
- 최신 체크 시점의 `is_alive=true` 장비가 **0개**이면 → 망 분리 환경으로 판단, A소스(network_down) 전체 스킵
- 운영 환경(현장 망과 동일)에서는 `is_alive=true` 장비가 있으므로 기존 로직 정상 작동
- 로그: `tb_network_status 전체 is_alive=false (N개) → 망 분리 환경으로 판단, network_down 스킵`

#### 사양 확정
- **기준**: 알람 기준(tb_equipment_alarm_report) + DI fault 태그(B소스) — 운영 환경에서만 네트워크 상태(A소스) 추가
- **개발 환경**: A소스 자동 비활성화 (전체 Timeout 감지)
- **운영 환경 배포 시**: 동일 망에서 SNMP 폴링되므로 A소스 정상 동작

### 완료 (2026-04-06 — GIS 관망도 유량 흐름 시각화 설계 + 프로토타입)

- **디자인 방안 수립**: 용수 흐름도의 유량 표현(두께/색상/애니메이션)을 GIS 관망도에 적용하는 4가지 기술 방안 비교 (line-dasharray / line-gradient / Custom WebGL / data-driven)
- **Phase 1**: `line-width` + `line-color` data-driven — `flowToWidth()`, `flowToColor()` 로직 MapLibre expression으로 이식
- **Phase 2**: `line-gradient` + rAF 30fps — `line-progress` [0→1]으로 흐름 방향 셔머 애니메이션
- **프로토타입 HTML**: `docs/gis-flow-animation-prototype.html` — 샘플 7개 시설 네트워크, 불균형 경보 점선, 토글 컨트롤, 다크/라이트 모드
- **핵심 발견**: `line-gradient` 레이어는 `lineMetrics: true` 필수, `line-width` data-driven 불가 (상수만 허용), `line-cap`/`line-join`은 `layout`이 아닌 MapLibre 4.x에서 별도 처리

### 완료 (2026-04-07 — Node-RED 네트워크 동기화 + 버그 수정 2건)

#### 구현 내역
- **`expected_impact_assessment` "정보없음개" 수정** (commit `3b95730`)
  - Node-RED 함수 노드 `d11b61e81d4e4df7` (수위 LL 예상피해평가 UPDATE)
  - `supply_population` 비숫자("정보없음", NULL) → `isNaN()` 가드 + 0 대체
  - 기존 50건 오염 데이터 DB 직접 UPDATE ("정보없음개 수용가..." → "수용가 용수공급차질")
- **`build_level_detail_block` "2.73None" 수정** — 이미 `or ""` 패턴 적용 확인, 추가 수정 불필요
- **네트워크 상태 동기화 Node-RED 신규 탭** (`flows_deploy.json`)
  - "네트워크 체크" 탭 비활성화 (`disabled: true`) — 개발환경 현장 IP 접근 불가
  - 신규 "네트워크 상태 동기화" 탭: cronplus(5분) → 원격 SELECT(최근 10분) → UPSERT 생성(500건 배치) → 로컬 INSERT ON CONFLICT
  - 원격 DB config 신규: `remote_pg_config_001` (112.166.183.65:25479/postgres)
  - 로컬 DB: 기존 `slm-timescaledb` (172.17.0.1:5433/slm)
  - 수동 실행 inject 노드 + catch/에러 처리 + 상태 표시 포함
  - **주의**: postgresql 노드 `query` 필드에 `{{{msg.payload}}}` 필수 (빈 문자열이면 쿼리 무시)
  - 원격 DB user: `postgres` (dj_post 아님, db_sync.py 참조)
  - 테스트: 수동 트리거 → 1,780건 UPSERT 성공 (178장비, 정상138/이상40)

### 완료 (2026-04-07 — 이상감지→설비 역추적 Phase 3: 연결 설비 건강 진단)

#### 구현 내역
- **`_diagnose_equipment_for_tags()`** (`ai_server.py`): 이상 태그 → tb_equipment_tag_map 역추적 → 연결 설비 건강 진단
  - 5단계 진단: 태그-설비 매핑 → 전체 태그수 → 네트워크 상태 → DI 장애 → 건강점수
  - 건강 점수 0~100 (장애 유형별 감점: equip_fault -40, power_fault -30, network_down -25, comm_error -15)
  - 건강 등급: 정상(≥80) / 주의(≥50) / 위험(<50)
- **ANOMALY_FACILITY_DETAIL 핸들러**: 이상 태그 수집 → `_diagnose_equipment_for_tags` 호출 → `data["equipment_diagnosis"]`
- **프론트엔드**: `EquipmentDiagnosis` 타입 + `AnomalyDetailView` 보라 테두리 카드
  - 설비 ID + 유형 + 건강점수 + 이상태그/전체태그 비율 + 장애 라벨
  - chat-response-mapper `equipment_diagnosis` 패스스루

### 완료 (2026-04-07 — 계정 권한 Phase 3~4: 메뉴 접근 권한 매트릭스)

#### 구현 내역
- **DB 시드**: `tb_menu` 35건 + `tb_auth_menu` 84건 (MASTER 35 + ADMIN 35 + USER 14)
  - 정적 사이드바 메뉴 구조를 DB로 완전 이관
  - `/api/auth/me`가 `tb_auth_menu` 기반 권한별 메뉴 필터링 동작 확인
- **백엔드 API 3종** (`auth_crud.py`)
  - `GET /api/auth/menu-permissions`: 전체 메뉴 + 권한별(MASTER/ADMIN/USER) 허용 매트릭스
  - `PUT /api/auth/menu-permissions`: 권한별 메뉴 허용/차단 토글 (use_yn Y/N)
  - `PUT /api/auth/menu-visibility`: 메뉴 전체 표시/숨김 (tb_menu.use_yn)
- **프론트엔드**: `MenuPermissionMatrix.tsx` 신규
  - 메뉴 관리 페이지에 "메뉴 트리" / "접근 권한" 탭 추가 (MASTER 전용)
  - 체크박스 매트릭스: 부모/자식 메뉴 × 마스터/관리자/일반 3열
  - Eye 아이콘: 메뉴 전체 표시/숨김 토글 (DB 영속화)
  - MASTER는 항상 전체 접근 (체크 비활성)
- **`tb_auth_menu`**: PK `(region, auth_idn, menu_idn)` + `use_yn`, `menu_order` 컬럼 추가

### 완료 (2026-04-07 — 네트워크 토폴로지 초기화면 + 전체화면 버튼)

#### 구현 내역
- **네트워크 페이지 초기화면/전체화면 버튼** — 용수흐름과 동일한 UX 패턴 적용
  - `network/page.tsx`: 카드 헤더에 "초기화면" + "전체화면/축소" 버튼 추가
  - 전체화면: `position:fixed` 오버레이 (사이드바 제외), opacity+scale 300ms 트랜지션
  - ESC 키로 전체화면 해제
  - Force/계층형 양쪽 모드에서 동일 동작
- **`HierarchicalTopologyGraph.tsx`**: `fitViewTrigger` prop 추가
  - easeInOutCubic 380ms rAF 루프 카메라 애니메이션 (GIS flyTo 동일 방식)
  - 트리거 시 선택 노드 해제 + 전체 뷰로 복귀
- **`TopologyGraph.tsx`**: `fitViewTrigger` prop 추가
  - `handleResetView()` 호출 (ECharts Force 레이아웃 재시작)
- 전체화면 오버레이 CardHeader `relative z-10` 추가 (ECharts canvas 가림 방지)

### 이전 할 일 (2026-04-08 계획 → 전부 완료)

- ~~알람 테이블 meta 컬럼 NULL 원인 파악~~ → 완료 (04-05 비상연락처 구축 시 처리)
- ~~용수 흐름 교차검증이상 vs 대시보드 교차검증 불일치~~ → 완료 (04-07)
- ~~이상 시설 TOP에서 network down이 합덕배수지 수위알람에 포함되는 이유~~ → 완료 (04-05)
- ~~GIS 용수 흐름 기능 통합~~ → 완료 (04-07 Phase 4 물수지 히트맵 + 팝업 배지)
- ~~ai_server.py 분할 최적화~~ → 완료 (04-07 3개 모듈 추출, 13,921줄 → 12,724줄)
- ~~`expected_impact_assessment` "정보없음개" 수정~~ → 완료 (04-07 Node-RED isNaN 가드 + DB 정리)
- ~~네트워크 상태 동기화 Node-RED~~ → 완료 (04-07 원격→로컬 5분 증분 동기화)
- ~~"2.73None" 표기 버그~~ → 완료 (04-07 이미 수정 확인)

---

### 완료 (2026-04-05 — fn_reservoir_level_summary DB 함수 최적화)

#### RESERVOIR_LEVEL_STATUS ("남산 배수지 수위 현황") SQL 13~26s → 11ms

- **원인**: `latest` CTE가 `tb_tag_info JOIN tb_tag_raw_data` 시간 범위 없이 전체 스캔 → `read=168K blocks`
- **수정** (`fix_reservoir_level.py` → PostgreSQL 함수 재작성):
  1. `v_tagsns` 배열을 `tb_tag_info` 단독 스캔으로 사전 추출
  2. `latest` CTE: `r.tagsn = ANY(v_tagsns) AND r.logtime >= NOW()-7일` → `idx_tag_raw_tagsn_time` 인덱스 스캔
  3. `tag_info` CTE 별도 분리 (RETURNS TABLE `unit` 변수 충돌 → `i.unit AS tag_unit`으로 해결)
- **결과**: 13~26s → **11ms** (~100배 개선), 전체 응답 ~250ms

### 완료 (2026-04-05 — AI 파이프라인 3종 성능 최적화)

#### 병목 분석 및 개선 결과

| 병목 | 원인 | 개선 전 | 개선 후 |
|------|------|---------|---------|
| 임베딩 중복 계산 | `classify()`에서 `embed_query()` 2회 호출 | +1~4s 낭비 | 1회로 통합 |
| SLM 날짜 추출 오작동 | `_DATE_KW`에 `"분"/"간"/"주"` 단독 포함 → "분析/야간/주소" 오매칭 | +11~17s | 0~1ms (스킵) |
| FACILITY_TREND DB | `fn_trend_period_summary` 2회 풀스캔(287K blocks) | 2,852ms | 78~131ms (30×) |

#### 1. `intent_classifier.py` — 임베딩 1회 계산 공유
- `classify()` 내 `embed_query()` 2회 호출 → 1회로 통합 (query_vec 전달)
- `_get_vector_candidates()`, `_classify_by_vector()` 모두 `query_vec=None` 파라미터 추가
- 타이밍 로그 추가: `⏱ 임베딩 Xms`, `⏱ 분류(keyword/vector/slm) kw=Xms embed=Xms total=Xms`

#### 2. `param_extractor.py` — SLM 날짜 추출 조건부 스킵
- `_DATE_KW` 단독 한글자(`"분"/"간"/"주"`) 제거 → 복합 표현만 유지
  - `"분"` → `"분간"/"분 전"` (기존 "분析" 오매칭 방지)
  - `"간"` → 완전 제거 (기존 "야간/기간/구간" 오매칭 방지)
  - `"주"` → `"주간"/"주일"` (기존 "주소/주요" 오매칭 방지)
- `_has_date_hint = False`이면 Ollama 호출 없이 기본값 사용
- 타이밍 로그 추가: `⏱ 추출 Phase1=Xms Phase2(날짜)=Xms 합계=Xms slm_date=Y/N`

#### 3. `ai_server.py` — 전구간 타이밍 계측 + DB 함수 최적화
- `/ask`, `/ask/stream` 양쪽에 단계별 `⏱` 로그 추가
  - `분류=Xms 추출=Xms SQL=Xms 합계=Xms rows=N`
- `execute_sql()` 내 SQL 실행 시간 로그: `⏱ SQL Xms → N행`
- `fn_trend_period_summary` PostgreSQL 함수 재작성
  - 기존: `tag_counts` CTE COUNT(*) + `ranked` CTE 풀스캔 — 동적 JOIN으로 인덱스 미사용
  - 개선: `ARRAY(SELECT tagsn...)` 사전 추출 → `WHERE r.tagsn = ANY(v_tagsns)` 인덱스 스캔
  - 결과: 2,852ms → **78~131ms** (약 30배 개선)

#### 최종 응답 성능 (server9 기준, 웜업 후)
- `FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS` ("신평 배수지 야간최소유량 표준편차 분析"):
  - 임베딩=400ms, 분류=0ms, 추출(Phase2)=**0ms** (SLM 스킵), DB=~3ms
  - 전체 ≈ **2초** 이내 (이전: 분析/야간 오매칭으로 SLM 11~17s 낭비)

### 완료 (2026-04-05 — 비상연락처 관리 기능 + Node-RED 인코딩 수정)

#### 1. 비상연락처 관리 시스템 구축 (`tb_alarm_contact` → API → Web UI → Node-RED)
- **DB**: `tb_alarm_contact` 테이블 신규 생성, 시드 10건 (UPS 3, 정전 4, 통신이상 2, 밸브 1)
- **Python API** (`D:/slm/endpoints/alarm_contacts.py`):
  - `GET /crisis/alarm-contacts?category=` → 목록 조회
  - `GET /crisis/alarm-contacts/categories` → 카테고리 목록
  - `POST/PUT/DELETE /crisis/alarm-contacts[/{id}]` → CRUD
- **Web UI**: `/setup/alarm-contacts` 신규 페이지 (카테고리별 그룹 테이블, CRUD 다이얼로그)
  - 기존 카테고리 선택 + 신규 카테고리 직접 입력 지원
  - 사이드바 메뉴 '비상연락처' 추가 (M200-14, Phone 아이콘)
- **Node-RED**: `HTML 위기대응 표시` 3개 노드 UPDATE에 meta 서브쿼리 추가
  - `diagnosed_msg` 업데이트 시 `tb_alarm_contact` 서브쿼리로 `meta.contacts` 동시 저장
  - 대상: a1ff5115e65d1474, 820cf7cd8e67c2f9, a655fae0839ec028

#### 2. flows_deploy.json 한글 인코딩 깨짐 전면 수정
- **원인**: Node-RED 편집기가 일부 한글 문자를 U+FFFD(대체 문자)로 저장
- **범위**: 39개 노드, 122개 U+FFFD → 모두 올바른 한글로 복원
- **대표 예시**: `윤활 [?]태` → `윤활 상태`, `가[?]중` → `가동중`, `[?]력계` → `압력계` 등

#### 3. "통신이상감지" Node-RED 탭 비활성화 (`flows_deploy.json`)
- **원인**: 사양에 없는 탭이 60초 주기로 "통신이상 N개 태그 감지" 알람 자동 생성
- **수정**: 탭 ID `a1b2c3d4e5f60001` 및 하위 7개 노드 `d: true`로 비활성화

#### 4. meta NULL 원인 분석
- **원인**: Node-RED INSERT/UPDATE 쿼리에 meta 컬럼 포함 안 됨 (미구현)
- **해결**: 연락처 관리 구축 후 `HTML 위기대응 표시` UPDATE에 meta 서브쿼리 추가
- **반영 범위**: alarm_category에 대응하는 연락처가 있을 때 meta.contacts 자동 채움

---

### 완료 (2026-04-05 — psycopg2 한글 인코딩 수정 + 경보분석 필터 정상화)

#### 1. psycopg2 `client_encoding` 누락 수정 (`D:/slm/ai_server.py`)
- **증상**: 경보분석 페이지(`/crisis/alarm-analysis`)에 `alarm_severity = '정상'`인 성북2 알람이 계속 출현
- **원인**: `_init_db_pool()` 에 `client_encoding` 미지정 → psycopg2가 Windows cp949 인코딩으로 연결
  - SQL 인라인 한글 리터럴 `IS DISTINCT FROM '정상'`의 인코딩 불일치 → 필터 조건 비교 실패
- **수정**:
  1. `ThreadedConnectionPool` 에 `options="-c client_encoding=utf8"` 추가
  2. `/crisis/alarm-analysis` 쿼리 인라인 한글 → 파라미터화: `IS DISTINCT FROM %s` + `('정상',)` 튜플
- **결과**: API 응답에서 성북2 알람 0건, 필터 정상 동작 확인

#### 2. Node-RED 헌팅(Hunting) 필터링 로직 분석 (`flows_deploy.json`)
- **경보_송산2산단(배) 생활 2지 수위 LL [alarm_value=2.09]** 가 "필터링" 표시되는 원인 확인
- **Node-RED 3단계 흐름**:
  1. **CASE 2 노드** (SQL): 최근 5분간 아날로그 수위 데이터 `(max-min)/min × 100 ≥ 10%` → `hunting_detected='Y'`
  2. **LL/HH 판단 노드** (JS): `hunting === 'Y'` 이면 "헌팅 Y (필터링)" 경로로 라우팅
  3. **헌팅 필터링 UPDATE 노드** (SQL): `is_false_alarm='Y'`, `false_alarm_notes='이상데이터(헌팅) 감지에 의한 필터링'` 업데이트
- **결론**: DB의 `is_false_alarm='Y'` 값이 정상적으로 설정된 것임, 수위 헌팅으로 인한 올바른 필터링

#### 3. 사양 확정 — 경보분석 "필터링" 표시 기준
- `getDisplayStatus` (`AlarmAnalysisPanel.tsx`): `is_false_alarm === 'Y'` OR `task_suppressed === true` 중 하나면 "필터링"
- `alarm_severity = '정상'` 단독은 "필터링" 조건 아님 (진행중/알람해제로 분류)
- `/crisis/alarm-analysis` API: `alarm_severity IS DISTINCT FROM '정상'` 로 정상 등급 알람 자체 제외

---

### 사양 확정 (2026-04-05 — 경보분석 "필터링" 표시 기준)

#### 필터링 컬럼 표시 대상 (`getDisplayStatus` in `AlarmAnalysisPanel.tsx`)
- **조건 1**: Node-RED 필터링기능 탭 로직 처리 → `is_false_alarm = 'Y'`
- **조건 2**: Web 작업관리설정 억제 알람 유형/태그 → `task_suppressed = true`
- 위 두 조건 중 하나라도 해당하면 "필터링" 뱃지 + 필터링 탭으로 분류
- `alarm_severity = '정상'` 단독으로는 "필터링" 조건 아님 (진행중/알람해제로 분류됨)

---

### 완료 (2026-04-05 — 경보분석 위기대응 카드뷰 개선 v2)

#### 1. `AlarmAnalysisDetail` — React Hooks 위반 수정 (`src/components/crisis/AlarmAnalysisDetail.tsx`)
- **버그**: `useMemo`를 early return(`if (!report)`) 이후에 호출 → Rules of Hooks 위반
  - null → non-null 전환 시 hook 수 불일치로 렌더링 실패 (경보 선택 시 섹션 미표출)
- **수정**: `useMemo`를 early return 전으로 이동, `report?.diagnosed_msg` 옵셔널 체이닝 사용

#### 2. 탁도계 통신이상 `alarm_severity` 정상화 (DB 직접 실행)
- `alarm_msg LIKE '%탁도계 통신이상%'` 레코드 1790건 `alarm_severity = '정상'` 업데이트
- 헌팅성 통신이상으로 필터링 처리 필요한 알람

#### 3. 심각도 키워드 색상 최종 확정
- `정상` → `text-green-500`, `주의` → `text-amber-500`, `이상`/`경고` → `text-red-500`
- `경보` 키워드 색상 제외 — "경보" = 일반 단어(경보등급, 경보 필터링 결과), 경고와 혼동하지 않도록
- `isNumericContext` 적용: "3.5m 이상" 등 수치 비교 표현의 "이상"은 색상 제외

---

### 완료 (2026-04-05 — 경보분석 위기대응 카드뷰 개선)

#### 1. `AlarmAnalysisDetail` — diagnosed_msg 파싱 카드 렌더링 (`src/components/crisis/AlarmAnalysisDetail.tsx`)
- **변경 전**: `diagnosed_msg` 있으면 iframe으로 표출 (다크모드 CSS 주입 방식), `diagnosed_cause`/`countermeasure` 있으면 구조화 카드
- **변경 후**: `diagnosed_msg` → DOMParser로 섹션 파싱 → 카드 뷰 렌더링 (iframe 완전 제거)
- **파싱 로직** (`parseDiagnosedMsg`):
  - `section.section` 요소 순회, `1. 경보 경과` 섹션은 헤더에 이미 표시되므로 스킵
  - `.label` 클래스(red/green/blue) → `bgColor` Tailwind 매핑
  - 자식 DOM 순서 보존: `<p>` → `TextBlock`, `<ul>/<ol>` → `ListBlock`, `.info/.info-box` → `InfoBlock`
  - `InfoBlock` 파싱: `<p><strong>(Key)</strong> value</p>` 패턴 정규식 `/^\(([^)]+)\)\s*(.*)/`
- **우선순위**: `diagnosed_msg` 파싱 성공 → 파싱 카드 / 실패 시 기존 `diagnosed_cause`/`countermeasure`/`meta.operations` fallback
- **제거**: `DiagnosedMsgIframe`, `DARK_MODE_CSS`, `FLOWCHART_LAYOUT_CSS`, `injectDarkMode`, `injectFlowchartLayout` 전부 삭제

#### 2. 심각도 키워드 색상 하이라이팅 (`HighlightedText` 컴포넌트)
- 텍스트 내 `정상` → `text-green-500`, `주의` → `text-amber-500`, `이상`/`경고` → `text-red-500`
- 적용 범위: TextBlock(단락), ListBlock(목록 항목), InfoBlock(key-value 값) 전체

#### 3. 로컬 DB ↔ 원격 DB 동기화 확인
- `44270_24700_AMA_N001` (경보_송악2(배) 1지 수위 HH 상태, 2026-04-05 13:16:00)
- `diagnosed_msg` 3922자, `diagnosed_cause`, `countermeasure`, `alarm_severity` 모두 identical 확인 → 동기화 불필요

---

### 완료 (2026-04-05 — Node-RED 위기대응 HTML 개선 + DB 알람 적재 수정)

#### 1. 위기대응 메시지 경보등급 동적 표시 (`flows_deploy.json`)
- **노드**: `HTML 위기대응 표시` (a1ff5115e65d1474)
- 기존: `(경보등급) 주의등급.` 하드코딩
- 변경: `const alarm_severity = msg.payload.alarm_severity || "주의"` 변수 추출 → `${alarm_severity}등급.` 동적 표시
- 효과: 경고/주의/정상 등 DB 실제 값 반영

#### 2. `build_sql` JSONB 경로 버그 수정 + 작업억제 로직 강화 (`flows_deploy.json`)
- **노드**: `SQL 생성(발생)` (build_sql)
- **JSONB 경로 오류**: `tm.suspend_alarm_types->'suspend_alarm_types'` → `tm.suspend_alarm_types` (직접 배열 접근)
  - `suspend_alarm_types` 컬럼은 `["수위", "압력"]` 형태 직접 JSONB 배열로 저장됨
  - 기존 코드는 항상 `'[]'::jsonb`로 fallback → 작업 억제 기능 전혀 동작 안 함
- **카테고리명 정렬**: `'통신상태'` → `'네트워크'/'통신'`, `'펌프상태'` → `'펌프'`, `'밸브'` 추가
- **`'전체'` 처리 추가**: `sat.type = '전체'` 조건으로 사이트 전체 알람 억제
- **개별 태그명 매칭 추가**: `sat.type = ti.datadesc OR sat.type = ti.datainfo`

#### 3. 중복 open 알람 정리 (DB 직접 실행)
- **원인**: `NOT EXISTS(alarm_end_time IS NULL)` 중복 방지 체크가 없던 시기에 10초마다 INSERT → tagsn당 최대 82건 중복
  - `44270_24110_SYA_N001` (죽동(배) 탁도계 통신이상): 82건 중복
  - `44270_24110_LEA_N001` (죽동(배) 수위#1 HH 알람): 4건 등 총 88건
- **수정**: tagsn별 최신 1건만 open 유지, 나머지 `alarm_end_time = alarm_start_time`, `alarm_status = '알람해제'` 처리
- **SQL**:
  ```sql
  UPDATE tb_equipment_alarm_report
  SET alarm_end_time = alarm_start_time, alarm_status = '알람해제'
  WHERE alarm_end_time IS NULL
    AND (tagsn, alarm_start_time) NOT IN (
        SELECT tagsn, MAX(alarm_start_time)
        FROM tb_equipment_alarm_report
        WHERE alarm_end_time IS NULL GROUP BY tagsn
    );
  ```

#### 4. Node-RED PostgreSQL 연결 수정 (`flows_deploy.json`)
- **원인**: Node-RED(bridge 네트워크, 172.17.0.2)와 TimescaleDB(web_default 네트워크, 172.20.0.2)가 서로 다른 Docker 네트워크
- **postgresSQLConfig** (71827310c941a9d1): `host: localhost` → `host: 172.17.0.1` (bridge 게이트웨이)
- 효과: `0.0.0.0:5433` 포스트 포워딩으로 호스트 경유 접속

#### 5. `cagg_daily_pressure` 컬럼 참조 오류 수정 (`flows_deploy.json`)
- **노드**: `펌프 정보` (38b543d1e395154e)
- **원인**: `cagg_daily_pressure` 연속집계 뷰는 `bucket`, `tagsn`, `avg_val`, `min_val`, `max_val`만 보유 — `sitename`, `facilitytype` 없음
- `c.sitename` → `t.sitename`, `c.facilitytype` → `t.facilitytype` (JOIN된 `tb_tag_info t` 컬럼 사용)
- 서브쿼리 `SELECT max(bucket) FROM cagg_daily_pressure WHERE facilitytype = '가압장'` → JOIN 경유로 수정

### 완료 (2026-04-05 — 작업관리 태그 기능 보완)
- **TaskFormDialog 개별 태그 추가 항상 표시** (`src/components/crisis/TaskFormDialog.tsx`)
  - 기존: `sitename && facilitytype` 조건부 → 시설 선택 전 섹션 숨겨짐
  - 변경: 항상 표시, 미선택 시 안내문 "시설유형과 현장을 먼저 선택하면 개별 태그를 추가할 수 있습니다."
  - 태그 드롭다운 onBlur 150ms 딜레이 추가 (클릭 전 닫힘 방지)
- **수정 다이얼로그 시작/종료 시간 보존** (`task-management/page.tsx`)
  - 기존: 수정 시 defaults에 task_start_time/task_end_time 누락 → 현재 시각으로 초기화됨
  - 변경: editingTask.task_start_time/task_end_time을 defaults에 포함
  - TaskFormDialog 초기 상태도 defaults 있으면 해당 시간 사용 (DB 포맷 "YYYY-MM-DD HH:MM:SS" → "YYYY-MM-DDTHH:MM" 변환)
- **개별 태그명 알람 억제 로직** (`ai_server.py`)
  - 기존: `alarm_category`만 suspend_alarm_types와 비교 → 개별 태그명 억제 불가
  - 변경: `_is_alarm_suppressed(sitename, alarm_category, alarm_msg, active_tasks)` 시그니처 확장
    - 표준 카테고리 외 태그명은 `alarm_msg`에 포함 여부로 판정
    - `_ALARM_CATEGORY_NAMES` 상수로 표준/개별 구분
  - 실사용 예: "갈산" 가압장 작업 등록 시 suspend_alarm_types=["갈산(가) 유출압력(신규)", "갈산(가) 통신이상"] → 해당 alarm_msg 포함 알람 자동 억제

### 완료 (2026-04-05 — 계정 권한 Phase2 + 알람→작업관리 억제 로직, commit a640e9a)
- **계정 권한 Phase 2: 동적 메뉴 API 연동** (`src/hooks/use-sidebar-menus.ts`)
  - `/api/auth/me` 호출 → 백엔드 메뉴 우선 사용 (실패 시 정적 폴백)
  - 미인증/API 빈 응답 → 기존 정적 메뉴 + adminOnly 필터 폴백
- **알람→작업관리 억제 로직 (#36)**
  - `ai_server.py` — `GET /crisis/alarm-reports`에 `task_suppressed` 플래그 추가
    - `_get_active_task_suppressions()`: 현재 진행중인 작업(시간 범위 내) 조회
    - `_is_alarm_suppressed()`: '전체' 또는 alarm_category 매칭 시 억제
  - `crisis.ts` — `AlarmReportRecord.task_suppressed?: boolean` 필드 추가
  - `AlarmReportTable` — 행 우측 `ClipboardList` 작업 등록 버튼, 억제중 배지 + opacity-50
  - `dashboard/page.tsx` — `RecentAlarmList` 각 행에 작업 등록 버튼 추가

### 완료 (2026-04-04 — 대시보드 전체 폭 제약 해소)
- **layout.tsx 최대폭 제거** (`src/app/(dashboard)/layout.tsx`)
  - `2xl:max-w-screen-2xl 2xl:mx-auto` 클래스 제거
  - 원인: 디자인 개선 Step 8(와이드 레이아웃)에서 추가한 1536px 최대폭 제한이 모든 화면 해상도에서 콘텐츠 폭을 제약
  - 효과: 대시보드 종합현황판 캔버스가 사이드바 옆 가용 폭 전체를 채움

### 완료 (2026-04-04 — AI 질의 정확도 개선 3종, commit 48f0a25)
- **normalize_question 강화** (ai_server.py)
  - 숫자+달: "3달동안" → "90일동안", "6달간" → "180일간" (N×30일)
  - 숫자+개년: "3개년간" → "1095일간" (N×365일)
  - 오타: "작산" → "적산"
- **example3.json 질문 확장** (+45문항, 72→72인텐트, 708벡터)
  - NIGHT_MIN_FLOW_STATUS: +10 (트렌드/그래프/소소블록/중블록 패턴)
  - FACILITY_MIXED_TREND: +10 (복수시설/다중데이터 패턴)
  - FACILITY_TAG_LATEST_VALUE: +8 (최근 N분간 패턴)
  - RESERVOIR/BOOSTER EQUIPMENT_STATUS: +12
  - NIGHT_MIN_FLOW_SUMMARY_TABLE: +5
- **복수 시설 NMF 처리** (ai_server.py — NIGHT_MIN_FLOW_STATUS 커스텀 핸들러)
  - "행정1-1과 행정2-1 야간최소유량" → 두 시설 결과 병합
  - /ask + /ask/stream 양쪽 적용
  - FACILITY_TREND _is_night_min_flow 경로도 다중 시설 NMF 루프 추가
- **벡터 임계값**: VECTOR_THRESHOLD 0.75→0.68 (이전 세션)
- **임베딩 캐시 재구축**: 708벡터 (data/embeddings_cache.npy)

### 완료 (2026-04-04 — 네트워크 통신이상 알람 통합, commit da4e63f / edac6b2)
- **통신이상 알람 카드 (CommAlarmCard)**
  - `GET /network/comm-alarms`: 시리얼(DI 태그 val=1) + 이더넷(tb_network_status 이상) UNION
  - comm_type 필드 ('시리얼'/'이더넷') 추가, 유형별 필터 토글 버튼
  - 카드 제목 "시리얼 통신이상 알람" → "통신이상 알람", 이더넷 N · 시리얼 N 통계 표시
- **계층형 토폴로지 시리얼 장비 상태 표시**
  - `GET /network/topology`: serial_status CTE — DI 통신이상 태그 sitename+facilitytype 집계 → IP없는 장비 status 결정
  - hierarchical-topology.ts: has_ip 없어도 status 있으면 정상(초록)/이상(빨강) 도트 표시
  - isSerial 조건 수정: `!has_ip && !status` — DI 상태 있는 시리얼은 IP 장비처럼 선명하게
  - 이상 사이트 정렬, 링크 색상에 시리얼 이상 포함
- **NodeDetailPanel 시리얼 상태 뱃지**
  - "시리얼 (미모니터링)" → "시리얼 (정상)" 초록 / "시리얼 (이상)" 빨간 뱃지

### 완료 (2026-04-04 — Task 9+15+Node-RED #35)
- **Task 9: 사용자 관리 API 실DB 연동** (commit 981c593)
  - admin-user-api.ts: `/admin/users` → `/api/auth/users` 경로 정렬
  - `UserAuthType`: MASTER 추가, VIEWER 제거, LOCK_THRESHOLD 3→5
  - auth_crud.py: `create_user` 응답 user 객체 반환, `PUT /active`, `PUT /unlock`, `POST /session-end` 엔드포인트 추가
  - `last_login` varchar `.isoformat()` 버그 수정 (TypeError 해결)
  - `tb_access_log` user_agent 컬럼 없음 버그 수정
- **Node-RED #35: 통신이상감지 탭 추가** (flows_deploy.json)
  - 60초 주기 inject → DI 통신이상 태그(val=1) 시설 단위 집계
  - severity: 3개+ = '경고', 1~2개 = '주의', alarm_category='통신'
  - 해제: 모든 통신이상 태그 val=0 복귀 시 alarm_status='알람해제'
  - Node-RED REST API로 즉시 배포 완료

### 완료 (2026-04-04 — 팝업 최종, 용수공급 알람 팝업완료, commit dfef1ab)
- **Task 14-c: 용수 흐름 알람 클릭 → 경보분석 상세 팝업**
  - FlowNodeTrendPanel AlarmRow에 `onAlarmClick` prop + `role="button"` 클릭 핸들러 추가
  - flow/page.tsx에 `openAlarmDetail` 콜백 + AlarmAnalysisDetail Dialog 추가
  - `fetchAlarmAnalysisDetail(tagsn, startTime)` → AlarmReportRecord → Dialog 렌더링
- **Task 15: 사용자 수정 다이얼로그 (UserEditDialog)**
  - `UserEditDialog.tsx` 신규 — 이름·권한 변경 + 비밀번호 선택 초기화
  - `UserUpdateFormData` 타입 추가 (admin.ts), admin-user-api/store 업데이트
  - admin/users/page.tsx: `handleEdit` → `editTarget` 상태로 다이얼로그 오픈
- **대시보드 모바일 내부 스크롤바 제거**
  - 이상시설 TOP / 설비 장애 / 최근 경보 목록: `max-h / overflow-auto` → `md:` prefix로 데스크톱 전용화
  - 모바일에서 카드 내부 스크롤바 없이 전체 펼침, 페이지 스크롤로 통일
- **DB 커넥션 풀 누수 버그 수정 (ai_server.py)**
  - `_compute_flow_baselines()` finally 블록에서 `conn.close()` 누락 → 10분마다 커넥션 누수 → 풀 고갈
  - `finally: cur.close(); conn.close()` 로 수정
  - 증상: `/flow-map/realtime`, `/dashboard/overview` 등 모든 DB 엔드포인트 `"connection pool exhausted"` 반환

### 완료 (2026-02-21)
- 스윔레인 디지털 트렌드 (commit 9066332)
- 차트 재생 기능 TrendChart + PlotChart (commit 2c0eec6)
- 경보분석 로직점검 플로우차트 — CSS-only iframe 오버라이드 (commit 68ddf5b)
- 시맨틱 마커 전체 인텐트 적용
- 자동 알람 백그라운드 스캔
- 채팅 예시 질문 개선 (실제 현장명 포함)
- 문서 렌더링 개선 (매뉴얼 인텐트, detail 이미지 블록)
- 아날로그 듀얼 Y축 자동 분리
- 추천질의 현장명 자동 보강
- 채팅 사이드바 드래그 스크롤
- 구축 Setup 테이블 duplicate key 수정 (reservoir/booster general_overview)
- 태그 마스터 실데이터 연동 — tb_tag_info 2,698건, 서버사이드 페이징+필터, 태그추가/엑셀업로드/CSV다운로드
- 트렌드 태그 조합 비교 — mock 제거, tb_tag_info+tb_tag_raw_data 실데이터 연동, 5종필터+페이징 태그브라우저, POST /trend/data (TimescaleDB time_bucket), 최대15태그 자유조합

### 완료 (2026-02-22)
- 모니터링 메뉴 고도화 프론트엔드 — mock→실데이터 전환, 9개 파일 신규/수정
  - monitoring-config.ts 타입 + monitoring-config-api.ts API + monitoring-view-store.ts 스토어 + monitoring-utils.ts 유틸
  - TrendChart.tsx alarmMarkLines/title prop 추가 (HH/LL 가이드선)
  - MonitoringTrendBlock + MonitoringFacilityPage 공용 컴포넌트
  - reservoir/booster page.tsx 래퍼 교체 + block page.tsx 신규
  - MonitoringSetupPage + MonitoringTrendDialog + ExistingCatalogList 설정 페이지
  - sidebar-menus.ts: 감압시설→블록, 트렌드설정→모니터링설정
  - 빌드 검증 통과

### 완료 (2026-02-22 — 인텐트 분류 정확도 개선)
- 인메모리 벡터 유사도 검색 — snowflake-arctic-embed2 (1024dim), numpy cosine, NPY+JSON 캐시
  - intent_embeddings.py: IntentEmbeddingIndex 클래스 (load_or_build, search, embed_query)
  - intent_classifier.py: 3단계 분류 (keyword → vector ≥0.75 → SLM Phi-4-mini 폴백)
  - ai_server.py: startup 임베딩 캐시 빌드, force_intent 파라미터 지원
  - example3.json: 501 질문 (64 인텐트, 동의어 포함)
- 오분류 사후 보정 UI — 답변 하단 "원하는 답이 아닌가요?" + 대체 인텐트 후보 3개 표시
  - chat.ts: IntentCandidate 타입, AiServerResponse.intent_candidates
  - chat-response-mapper.ts: intent_candidates 매핑
  - BotMessage.tsx: IntentCandidatesSection (amber 테마 카드)
  - use-chat-submit.ts: submitWithForceIntent() — force_intent 파라미터 전달
  - ChatInput.tsx: submitCorrection DOM ref 노출
  - chat/page.tsx: handleCorrectIntent 콜백 연결
- 분류 정확도 테스트 및 개선 — 64% → 89% (25%p 향상)
  - example3.json: +63 동의어 질문, -2 혼동 질문 (440→501)
  - intent_classifier.py: 키워드 규칙 추가 (추이/같이→트렌드, 다발/빈번→경보다발)
  - ai_server.py: build_correction_response에 intent_candidates 반환 버그 수정
  - NEED_CORRECTION 후보 반환율: 0% → 67% (0/9→6/9)

### 완료 (2026-02-22 — 모니터링 고도화 추가, commit b932bf2)
- HH/LL 알람 설정 듀얼모드 — Analog Input만 표시, 상수/태그참조 선택
  - AlarmLimits 타입: hh_tagsn/ll_tagsn 태그참조 + hh/ll 상수
  - AlarmLimitCell 컴포넌트: 3모드 토글(없음/상수/태그) + Analog Output 드롭다운
  - monitoring-utils.ts: 태그참조 우선 해석, getAlarmRefTagSns()
  - monitoring-view-store.ts: 알람 참조 태그 시계열 일괄 조회 + 최신값 추출
- 모니터링 사이트 필터링 — 설정된(monitoring=true) 사이트만 모니터링 표시
  - ai_server.py: /monitoring/catalogs/sites에 monitoring_only 파라미터 추가
  - COALESCE 안전 캐스트 (meta.monitoring NULL 처리)
  - 설정 페이지는 전체 사이트 유지
- 트렌드 편집 기능 수정 — 다이얼로그 열릴 때 useEffect로 상태 초기화
- 인텐트 표시명 한글화 — 누락 7개 추가 (intent_index.py INTENT_DESCRIPTIONS)

### 완료 (2026-02-22 — 이상감지 고도화)
- 현장 프로파일링 + 그룹별 임계값 + 3단계 경보
  - site_profiler.py: SiteProfiler 클래스 (일 1회 유출유량/알람빈도/P95/P05 프로파일링)
  - tb_site_anomaly_profile 테이블 (원격 DB), A/B/C/D 그룹 분류
  - anomaly_detector.py: GROUP_THRESHOLDS, classify_z_level_by_group, classify_alert_grade(critical/warning/info)
  - anomaly_detector.py: analyze_level_pattern (C그룹 HH/LL 패턴 분석 3가지)
  - anomaly_detector.py: get_hh_ll_for_site (alarm_limits 우선, P95/P05 폴백)
  - anomaly_detector.py: build_*_detail_block에 site_profiles + alertGrade 시맨틱 마커
  - anomaly_iforest.py: GROUP_CONTAMINATION (A:0.03, B:0.05, C:0.08, D:0.05)
  - ai_server.py: _site_profiling_loop (60초 지연 후 24h 주기), lifespan에 등록
  - ai_server.py: GET /anomaly/profiles 디버깅 엔드포인트
  - ai_server.py: ANOMALY_SCAN_ALL에 site_group_distribution + 그룹별 Z-Score
  - ai_server.py: ANOMALY_FACILITY_DETAIL에 C그룹 패턴 분석 + site_group 표시
  - 프론트: anomaly-utils.ts에 ALERT_GRADE_*/SITE_GROUP_* 상수
  - 프론트: AnomalyScanView.tsx 그룹 분포 요약 카드
  - 프론트: AnomalyDetailView.tsx 사이트 그룹 뱃지 + 패턴 분석 경고
  - 프론트: chat.ts AnomalyData 타입 확장, chat-response-mapper.ts 매핑
  - proxy: PUBLIC_PATHS에 "anomaly" 추가
  - 빌드 검증 통과

### 완료 (2026-02-22 — 시간대별 기준선 보정)
- Z-Score 적응형 시간 기준선 — 평일/주말 × 피크/오프피크 4구간 분리
  - example3.json: ANOMALY_SCAN_ALL SQL CTE 교체 (raw_adaptive + time_ctx + stats_segment + stats_global + COALESCE 폴백)
  - example3.json: ANOMALY_FACILITY_DETAIL SQL CTE 동일 패턴 적용
  - 적응형 윈도우: interval '365 days' (가용 데이터 자동 사용, 30일→365일 자동 확장)
  - 구간별 최소 30건 미달 시 전체 기준선 폴백
  - answer_template 참고 문구 "동일 요일·시간대 기준" 반영
- CUSUM MNF 평일/주말 분리
  - anomaly_detector.py: _is_weekday() 헬퍼, compute_cusum_for_tags()에 wd_mean/we_mean 분리 기준선
  - CUSUM 계산 시 각 날짜의 요일에 맞는 기준선 적용
  - baseline_wd_mean, baseline_we_mean 필드 추가
- 프론트엔드 변경 없음 (컬럼명 하위 호환)

### 완료 (2026-02-22 — 용수 흐름 메뉴 구축)
- 용수 흐름 실데이터 연동 — tb_facility_flow_map 76건, ECharts 계통도, CRUD, CSV 입출력
  - setup.ts: FacilityFlowMap/FacilityFlowMapPK/FlowMapRoot 타입 (운영DB 스키마 기준 교체)
  - ai_server.py: 7개 API 엔드포인트 (GET/POST/DELETE /flow-map, roots, downstream, CSV export/import)
  - flow-map-api.ts: 프론트엔드 API 클라이언트 7개 함수
  - flow-diagram-chart.ts: ECharts graph 옵션 빌더 (시설유형별 색상/크기, BFS 하류 하이라이트)
  - FlowDiagramGraph.tsx: EChartWrapper 기반 계통도 (줌/패닝/리셋, 노드 클릭 하이라이트)
  - page.tsx: 실데이터+계통도+CRUD+CSV 전체 리팩토링
  - proxy: PUBLIC_PATHS에 "flow-map" 추가
  - FlowDiagram.tsx: CSS 기반 구버전 미사용 (ECharts로 대체)
  - 빌드 검증 통과

### 완료 (2026-02-23 — 설비 관리 CRUD, commit 9db1999)
- 설비 관리(tb_equipment_info) 실데이터 CRUD — 290건 운영DB 기반
  - setup.ts: EquipmentInfo/EquipmentMeta 타입 교체 (운영DB 스키마 기준)
  - ai_server.py: 6개 API (GET /equipments, /equipments/filters, /equipments/next-id, POST /equipments, PUT /equipments/{id}, DELETE /equipments/{id})
  - equipment-api.ts: 프론트엔드 API 클라이언트 7개 함수
  - EquipmentFormDialog.tsx: 12필드 추가/수정 폼, 접두사→equipment_id 자동생성 미리보기
  - EquipmentDeleteDialog.tsx: dry_run CASCADE 영향 확인 + 경고 표시
  - page.tsx: 서버사이드 페이징 50건 + 3종필터(현장명/시설유형/장비유형) + 키워드 + CSV
  - proxy: PUBLIC_PATHS에 "equipments" 추가
- 용수 흐름 계통도 SVG 전환 — ECharts→순수 SVG/DOM, BFS 레이어 배치, 줌/패닝/하이라이트

### 완료 (2026-02-24 — 네트워크 관리 고도화)
- 네트워크 관리 메뉴 CRUD + 시각 미리보기 — 4가지 요구사항 전체 구현
  - DeviceTable.tsx: 장비유형 필터 드롭다운 + equipment_id 컬럼 + 키워드 검색 확장
  - ai_server.py: POST/PUT/DELETE /network/links + GET /network/links/equipment-search (4 API)
  - ai_server.py: GET /network/links keyword 검색에 sitename ILIKE 추가, COUNT 쿼리 JOIN 수정
  - setup/networks/page.tsx: 437줄 → 25줄 thin shell (Tabs 셸)
  - NetworkInfoTab.tsx: 장비 탭 추출 (변경없음)
  - NetworkLinkTab.tsx: 연결 탭 + CRUD 버튼 + 현장명 2줄 표시 + CSV 현장명 포함
  - NetworkLinkFormDialog.tsx: 장비 자동완성 피커(EquipmentPicker) + 추가/수정 폼
  - LinkPreviewDialog.tsx: Force ↔ 계층형 토폴로지 미리보기 (90vw×78vh)
  - network-manage-api.ts: createNetworkLink/updateNetworkLink/deleteNetworkLink/searchLinkEquipment
  - setup.ts: NetworkLinkPayload, EquipmentSearchResult 타입 추가
  - 빌드 검증 통과
- 네트워크 연결 행 클릭 → 미니 플로우 프리뷰
  - link-flow-utils.ts: BFS 서브그래프 추출 (역방향 루트탐색 + 정방향 트리, max depth 4)
  - LinkFlowPanel.tsx: SVG 미니 트리 (depth별 열 배치, FACILITY_COLOR, 상태 도트, 하이라이트)
  - NetworkLinkTab.tsx: 행 클릭 선택/해제 + 테이블 하단 인라인 플로우 패널
  - topology 데이터 lazy fetch + useRef 캐시 (백엔드 추가 없음)
  - 빌드 검증 통과
- 네트워크 토폴로지 듀얼 뷰 — 이전 세션에서 이미 완료 확인
  - network/page.tsx: viewMode "force"|"hierarchical" 토글
  - TopologyGraph (ECharts Force) ↔ HierarchicalTopologyGraph (SVG/DOM 3컬럼 적응형)
  - 공유 selectedNodeId + NodeDetailPanel

### 완료 (2026-02-25 — tb_monitoring_catalog 분리 + 인텐트/추천질의 버그픽스)
- tb_monitoring_catalog 테이블 분리 (tb_trend_catalog → 모니터링 전용)
  - 원격 DB: CREATE TABLE tb_monitoring_catalog + 6행 마이그레이션
  - ai_server.py: 5개 모니터링 API SQL 교체 + GET /monitoring/catalogs/reference 신규
  - monitoring-config.ts: MonitoringCatalog flat 구조 (catalog_id, catalog_name, display_order, items)
  - monitoring-config-api.ts: monitoringOnly 파라미터 제거, fetchTrendCatalogReference 추가
  - monitoring-view-store.ts / monitoring-utils.ts: meta.* → 직접 필드 접근
  - MonitoringTrendDialog / MonitoringSetupPage / ExistingCatalogList: 필드명 교체
  - MonitoringTrendBlock / MonitoringFacilityPage: catalog_id, catalog.items 직접 접근
- 인텐트 분류 버그픽스
  - intent_classifier.py: 야간최소유량 "표준편차"/"분석" 체크 → "표" 체크 앞으로 이동
  - intent_classifier.py: "네트워크" 키워드 → FACILITY_COMMUNICATION_STATUS 직접 분류
  - intent_classifier.py: common_keywords에 "네트워크" 추가, _TAG_LATEST_EXCLUDE에 추가
- 추천질의 및 no-data 응답 개선
  - example3.json: "신평 배수지 압력 현황은?" → "신평 가압장 압력 현황은?" (배수지 압력 태그 없음)
  - example3.json: FACILITY_PRESSURE_STATUS 추천질의 → {sitename} {facilitytype} 포함 3건 교체
  - example3.json: "한달간 송악1 배수지 압력 트렌드" → "한달간 기지시 가압장 압력 트렌드"
  - ai_server.py: _NO_DATA_HINTS 딕셔너리 → 배수지 압력 쿼리 시 맞춤 안내 메시지
  - param_extractor.py: _SITENAME_FUZZY_STOPWORDS에 "전단","후단","가동" 등 8종 추가
  - use-chat-faq.ts: 야간최소유량 FAQ 질문 교체
  - 임베딩 캐시 재빌드

### 완료 (2026-02-26 — 네트워크 플로우 팝업 + 추천 질의 전수 테스트)
- 네트워크 연결 플로우 팝업 — 행 더블클릭 → Dialog (인라인 패널 → 팝업 전환)
  - NetworkLinkTab.tsx: selectedLink→flowLink, onClick→onDoubleClick, Dialog 래핑
  - LinkFlowPanel.tsx: Card/onClose 제거, NODE_W 140→170, NODE_H 44→48, 반응형 SVG
- 추천 질의 전수 테스트 — 92건 고유 추천 질의 테스트, 20건 수정
  - example3.json: 12개 인텐트의 recommend_questions에 {sitename} {facilitytype} 플레이스홀더 추가
  - RESERVOIR_LEVEL_HUNTING_CHECK: 3건 수정 (수위 트렌드, 계통도, 헌팅확인)
  - FACILITY_TAG_LATEST_VALUE: 3건 수정 (트렌드, 결측분석, 수위현황)
  - NIGHT_MIN_FLOW_STATUS: 3건 수정 (야간최소유량 트렌드/표준편차/결측)
  - FACILITY_COMMUNICATION_STATUS: 2건 수정 (네트워크상태, 최근알람)
  - FACILITY_COMMUNICATION_TOPOLOGY: 1건 수정 (통신상태)
  - FACILITY_MIXED_TREND: 2건 수정 ("1월 1일" → "최근 1주일간")
  - RESERVOIR_LEVEL_STATUS: 1건 수정 (가압장 가동현황)
  - RESERVOIR_SUPPLY_AVAILABLE_HOURS: 1건 수정 (비상급수→초동대응)
  - FACILITY_ADDRESS_INFO_RESERVOIR/BOOSTER/BLOCK: 3건 수정 (다른설비/블록→구체적)

### 완료 (2026-02-26 — 트렌드 시간 네비게이션)
- 트렌드 차트 좌/우 시간 네비게이션 — 모니터링 + AI 채팅 양쪽 적용
  - use-time-navigation.ts (신규): 핵심 네비게이션 훅 (prependedData, viewOffset, goLeft/goRight)
  - use-chart-playback.ts: onStop 콜백 추가 (확장 모드에서 정지 시 네비게이션 위치 복귀)
  - TrendChart.tsx: 네비게이션 통합 + 이전/다음 버튼 UI + 확장 데이터 재생 연동
  - PlotChart.tsx: 네비게이션 통합 + 이전/다음 버튼 UI (tag_ids 존재 시에만)
  - chat.ts: PlotData.tag_ids 필드 추가
  - chat-response-mapper.ts: buildPlotData/buildPivotedPlotData에 tag_ids 매핑
  - trend-to-plot.ts: tag_ids 전달
  - 버그 수정: 시간 포맷 로컬 타임스탬프 변환 (toISOString → formatLocalTimestamp)
  - 버그 수정: tagIds 배열 참조 안정화 (stableTagIds useMemo, 무한 루프 방지)
  - 빌드 검증 통과

### 완료 (2026-02-26 — ChartStatsBar 통합)
- DualPanelStats → ChartStatsBar 리네이밍 — 단일 트렌드에서도 ↑max ≈avg ↓min 통계 표시
  - ChartStatsBar.tsx 신규: 빈 섹션 자동 숨김 (아날로그만/디지털만/혼합 모두 대응)
  - TrendChart.tsx + PlotChart.tsx: isDualPanel 가드 제거, 항상 통계 바 렌더링
  - DualPanelStats.tsx 삭제
  - 빌드 검증 통과

### 완료 (2026-02-26 — ALARM_ABNORMAL_LOCATIONS 인텐트)
- 경보 이상 발생 지점 조회 — 65번째 인텐트 (통신/수위/압력/펌프/밸브/유량/전원 × 시설유형 × HH/LL/FAULT)
  - example3.json: ALARM_ABNORMAL_LOCATIONS (18개 질문, 빈 SQL, table, summary)
  - intent_classifier.py: "지점" 키워드 규칙 (ALARM_ABNORMAL_LOCATIONS)
  - intent_index.py: FACILITY_CATEGORIES + INTENT_DESCRIPTIONS 등록
  - ai_server.py: _extract_alarm_level (HH/LL/FAULT), 커스텀 핸들러(sync+SSE), 7일 폴백, 후처리
  - ai_server.py: _DYNAMIC_SQL_INTENTS (빈 SQL 조기 반환 우회), _TABLE_INTENTS_ALLOW_ALL 추가
  - query_validator.py: _SKIP_REQUIRED_CHECK 추가
  - chat-response-mapper.ts: alarm_start_time 컬럼 매핑 추가
  - chat-stream.ts: try/catch 분리 (JSON 파싱 에러만 catch, 콜백 에러 전파)
  - 프론트엔드 검증 통과 (Playwright 테스트)

### 완료 (2026-02-26 — 배수지 정보 구축 고도화)
- 배수지 관리(tb_service_reservoir_info + status) 실데이터 CRUD — 운영 DB 연동
  - ai_server.py: 5개 API (GET /reservoirs, GET /reservoirs/{sitename}, POST, PUT, DELETE)
  - ai_server.py: _serialize_reservoir_info/_status, _build_reservoir_general_overview 헬퍼
  - setup.ts: ReservoirInfo/ReservoirStatus/ReservoirEquipmentMeta 타입 교체 (운영DB 스키마 기준)
  - reservoir-api.ts: 5개 API 클라이언트 함수 (fetchReservoirs/fetchReservoir/create/update/delete)
  - ReservoirFormDialog.tsx: 3탭 (기본정보/구역설정/운영상태) + 설비메타 16항목 테이블
  - page.tsx: 서버사이드 페이징 50건 + 키워드 검색 + CSV 다운로드
  - general_overview JSONB → flat 변환 (install_location, facility_capacity_m3 등 + pump_required, supply_position, supply_time_hours)
  - status.meta: 16항목 배열 [{구분, 설비유무, 원격감시제어구분}]
  - proxy: PUBLIC_PATHS에 "reservoirs" 추가
  - ReservoirInfoForm.tsx, [sitename]/page.tsx 삭제, mock 데이터 제거
  - 빌드 검증 통과

### 완료 (2026-02-27 — FACILITY_CATALOG_TREND_TABLE 인텐트)
- 카탈로그 트렌드 표 — 66번째 인텐트 (배수지/가압장/소블록/소소블록/감압시설 × 수위/유출유량/유입유량/압력/유량/밸브/펌프)
  - example3.json: FACILITY_CATALOG_TREND_TABLE (10개 질문, 빈 SQL, table, table_type: summary)
  - intent_classifier.py: "표" + 데이터키워드 → FACILITY_CATALOG_TREND_TABLE 규칙
  - intent_index.py: INTENT_DESCRIPTIONS 등록
  - param_extractor.py: DATE_REQUIRED_INTENTS 추가
  - query_validator.py: _SKIP_REQUIRED_CHECK 추가
  - ai_server.py: _get_catalog_trend_filter() 헬퍼, _execute_catalog_trend_query() 2단계 청크 직접 쿼리
  - ai_server.py: _DYNAMIC_SQL_INTENTS + _TABLE_INTENTS_ALLOW_ALL + 커스텀 핸들러(sync+SSE)
  - TimescaleDB 성능 최적화: 49초→8초 (청크 직접 쿼리로 ChunkAppend 플래너 우회)

### 완료 (2026-02-27 — 청크 직접 쿼리 최적화 확대)
- TimescaleDB 청크 직접 쿼리 공용화 + TIMESERIES/POST /trend/data 최적화 (JOIN 39s→0.14s, 285배)
  - ai_server.py: 공용 유틸 4개 (_get_chunks_for_range, _query_chunks_agg, _reaggregate, _query_chunks_raw)
  - ai_server.py: _execute_timeseries_query — tb_tag_info → 청크별 raw 쿼리 → Python JOIN
  - ai_server.py: _TIMESERIES_CHUNK_INTENTS 5개 인텐트 커스텀 핸들러 (sync+SSE)
  - ai_server.py: POST /trend/data 청크별 집계로 교체
  - ai_server.py: SSE from_ts/to_ts 보정 누락 수정 (FLOW_ACCUMULATED/INSTANT 추가)
  - 실패 시 원본 execute_sql 자동 폴백

### 완료 (2026-02-27 — 구축 통합 캔버스 에디터)
- React Flow(@xyflow/react v12) 기반 통합 캔버스 에디터 — 78노드+76엣지 실데이터 연동, 4-Phase 전체 구현
  - canvas-config.ts: PALETTE_FACILITY_TYPES 6종, CANVAS_NODE_W/H, DND_FACILITY_TYPE, getFacilityStyle 재사용
  - canvas-types.ts: CanvasNode/CanvasEdge/CanvasLayoutResponse/CanvasNodeDetailResponse 타입, canvasEdgeId()
  - FacilityNode.tsx: 커스텀 노드 (시설유형 색상 테두리, 핸들, 설비/태그 배지, 선택 글로우)
  - FlowEdge.tsx: 커스텀 엣지 (Bezier, 호버 삭제 버튼, 20px 히트영역)
  - FacilityPalette.tsx: 6종 시설유형 드래그 소스 (HTML5 DnD)
  - canvas-store.ts: Zustand (nodes/edges/selectedNodeId/isDirty, applyNodeChanges/applyEdgeChanges/addEdge)
  - CanvasEditor.tsx: 3패널 레이아웃 (팔레트|ReactFlow+MiniMap+Controls|속성패널), DnD 드롭, Ctrl+S
  - CanvasToolbar.tsx: 자동배치/뷰맞춤/삭제/저장 버튼 + 미저장 표시
  - CanvasStatusBar.tsx: 노드/엣지 카운트, 선택 노드 ID
  - PropertyPanel.tsx: 3탭 속성 패널 (정보/설비/태그), useNodeDetail 훅으로 lazy load
  - SiteInfoTab.tsx: 현장명 편집, 시설유형, 설비/태그 카운트, 모니터링 상태
  - EquipmentTab.tsx: 설비 목록 + EquipmentFormDialog/DeleteDialog 연동 CRUD
  - TagMappingTab.tsx: 카탈로그 목록 + MonitoringTrendDialog 연동 CRUD
  - use-node-detail.ts: 선택 노드 상세 데이터 fetch 훅 (설비+카탈로그 lazy load, refresh)
  - canvas-types.ts: CanvasEquipmentItem/CanvasCatalogItem/CanvasNodeDetailResponse 구체 타입
  - use-canvas-persistence.ts: DB 로드/저장, 30초 자동저장, dirty 추적, beforeunload 경고
  - use-canvas-auto-layout.ts: BFS depth + Sugiyama median 교차 최소화 (8라운드), COL_GAP=260 ROW_GAP=80
  - canvas-layout-api.ts: fetchCanvasLayout/saveCanvasLayout/fetchNodeDetail API 클라이언트
  - ai_server.py: GET/PUT /canvas/layout + GET /canvas/node-detail/{sitename}/{facilitytype} (3 API)
  - ai_server.py: tb_canvas_node_position 자동 생성 (lifespan), 엣지 diff (추가/삭제), 고아 위치 정리
  - setup/canvas/page.tsx: dynamic import (SSR off)
  - sidebar-menus.ts: 구축 메뉴에 "캔버스 에디터" 추가
  - proxy: PUBLIC_PATHS에 "canvas" 추가
  - 빌드 검증 통과

### 완료 (2026-02-28 — 가압장/감압시설/블록 정보 구축 고도화)
- 가압장/감압시설/블록 CRUD — 배수지와 동일 패턴 (운영DB 실데이터 연동)
  - ai_server.py: 15 API endpoints + 9 helpers (booster 5 + pressure 5 + block 5)
  - setup.ts: 6 인터페이스 flat 교체 + EquipmentMetaItem 공용 타입
  - booster-api.ts, pressure-api.ts, block-api.ts: API 클라이언트 3파일
  - form-fields.tsx: Field/SelectField 공용 컴포넌트 추출
  - BoosterFormDialog.tsx: 2탭 (기본정보/운영상태) + 설비메타 26항목
  - PressureFormDialog.tsx: 2탭 (기본정보/운영상태) + 설비메타 5항목
  - BlockFormDialog.tsx: 2탭 (기본정보/운영상태) + 설비메타 5항목 + block_level 필터
  - booster/pressure/block page.tsx: 서버사이드 페이징 + 키워드 검색 + CSV 다운로드
  - [sitename] detail 페이지 3개 삭제, mock 데이터 제거
  - proxy: PUBLIC_PATHS에 "boosters", "pressure-reducing", "blocks" 추가
  - 빌드 검증 통과

### 완료 (2026-02-28 — 경보통합 + 헌팅 듀얼 알고리즘 + 캔버스 고도화)
- 경보관리 2탭 통합 — `/crisis/alarm-dashboard` 현황/이력 2탭, alarm-history redirect
  - alarm-dashboard/page.tsx: 2탭 구조 재작성 (현황 도넛+카테고리+테이블 / 이력 필터+확인)
  - alarm-history/page.tsx: redirect → alarm-dashboard?tab=history
  - sidebar-menus.ts: M006-1→"경보관리", M006-4 제거
- 헌팅 듀얼 알고리즘 비교 — [A] 3h 방향전환 + [B] 5m 분산뷰 동시 실행
  - ai_server.py: _execute_hunting_check() 듀얼 분석, build_hunting_result_block() 비교 표시
  - example3.json: answer_template 듀얼 형식 갱신
- 캔버스 에디터 Undo/Redo — Ctrl+Z/Y 키보드 + 툴바 버튼, 스냅샷 히스토리 MAX=50
  - canvas-store.ts: history/future 배열, pushHistory/undo/redo
  - CanvasEditor.tsx: 키보드 핸들러, CanvasToolbar.tsx: Undo2/Redo2 버튼
- 캔버스 PNG/SVG 내보내기 — html-to-image 기반
  - use-canvas-export.ts 신규: toPng/toSvg + getNodesBounds, CanvasToolbar.tsx: 내보내기 드롭다운
- 캔버스 설비↔태그 링크 — tb_equipment_tag_map CRUD
  - ai_server.py: DDL + 3 API (GET/POST/DELETE equipment-tag-link)
  - canvas-layout-api.ts: 3 API 함수, use-node-detail.ts: equipmentTags fetch
  - EquipmentTab.tsx: 접을 수 있는 설비별 태그 목록 + 연결/해제
  - EquipmentTagLinker.tsx 신규: 태그 검색 피커
- 프록시 PUBLIC_PATHS 정리 — admin/alarm/chat 통합 추가, 메뉴 401 에러 해소
- 빌드 검증 통과

### 완료 (2026-02-28 — 구축 CSV 다운로드/업로드 통합)
- CSV 다운로드 빈 템플릿 지원 + CSV 업로드 일괄 구현 (8개 구축 메뉴)
  - csv-utils.ts 신규: downloadCsv(빈 템플릿 지원), toCsvRow, escapeCsvField, uploadCsv, fetchAllPages 공용 유틸
  - CsvUploadDialog.tsx 신규: 범용 CSV 업로드 다이얼로그 (title, columns, onUpload, onComplete)
  - 8개 페이지 CSV 다운로드: 빈 데이터 가드 제거 → 헤더만 포함된 빈 CSV 템플릿 다운로드
  - ai_server.py: 8개 CSV import 엔드포인트 + 5개 헬퍼(_csv_cell/_csv_float/_csv_int/_csv_bool/_csv_json_array)
  - 7개 API 클라이언트: upload 래퍼 함수 추가 (tag/equipment/reservoir/booster/pressure/block/network-manage)
  - 8개 페이지에 업로드 버튼 + CsvUploadDialog 추가
  - TagUploadDialog.tsx stub → CsvUploadDialog로 교체
  - CSV 컬럼 전체 확장: 배수지 27컬럼, 가압장 18컬럼, 감압시설 9컬럼, 블록 11컬럼 (FormDialog 전필드 포함)
  - fetchAllPages: page_size=200 분할 페이징 (백엔드 le=500 검증 대응), arrow wrapper 패턴
  - 버튼 명칭 통일: "CSV 업로드" / "CSV 다운로드"
  - 백엔드 CSV import 컬럼 확장: 배수지 6→27, 가압장 7→18, 감압시설 6→9, 블록 7→11
  - 빌드 검증 + API 검증 통과

### 완료 (2026-03-02 — 태그 데이터 그룹 분류 시스템)
- TIMESERIES 인텐트 datainfo regex 오매칭 해결 — 계층형 그룹 기반 정확 매칭
  - ai_server.py: TAG_DATA_GROUPS 상수 21개 (유량/압력/수위/수질 + 하위 세분류)
  - ai_server.py: tb_tag_data_group DDL + tb_tag_group_map DDL (lifespan 자동 생성)
  - ai_server.py: _auto_classify_tags() — longest-keyword-first 전략, 2508/2698건 93% 분류
  - ai_server.py: _execute_timeseries_query — group_code 우선 JOIN → datainfo regex 폴백
  - ai_server.py: _resolve_group_codes() — 상위 그룹(FLOW) → 하위 전부(INSTANT+CUMULATIVE+INLET+OUTLET)
  - ai_server.py: sync + SSE 핸들러 — params.get("group_code") + intent-specific override
  - ai_server.py: GET /tags/groups — 그룹별 태그수 + 전체/분류/미분류 통계
  - param_extractor.py: _KEYWORD_TO_GROUP_CODE 매핑 17쌍 (compound→simple 순서)
  - param_extractor.py: _extract_group_code() + extract_all() group_code 필드 추가
  - 검증: "유입압력" → PRESSURE_INLET → 822행 (유입압력만), "압력" → PRESSURE → 1644행 (전체)
  - 프론트엔드 변경 없음

### 완료 (2026-03-02 — 인과관계 Rule 엔진 Phase 1)
- 인과관계 Rule 엔진 — 시설유형별 물리법칙 기반 인과 체인 템플릿 + 검증
  - ai_server.py: CAUSAL_CHAIN_TEMPLATES 5개 시설유형 (가압장/배수지/감압시설/소블록/소소블록)
  - ai_server.py: _build_causal_index() — 서버 시작 시 95개 시설 인과 인덱스 자동 구축
  - ai_server.py: ANOMALY_FACILITY_DETAIL 핸들러에 인과 검증 통합 (이상 태그 역추적)
  - anomaly_detector.py: verify_causal_context() — 5가지 인과 불일치 패턴 판정
  - anomaly_detector.py: _check_direction() — 시간 윈도우 방향 비교 (RISE/FALL/STABLE)
  - anomaly_detector.py: build_anomaly_facility_detail_block에 causal_result 시맨틱 마커 추가
  - ai_server.py: GET /causal/rules, /causal/verify 디버그 API
  - proxy: PUBLIC_PATHS에 "causal" 추가
  - 설계 원칙: 판단=Rule (물리법칙, 투명, 즉시), 설명=SLM (사후, 자연어)

### 완료 (2026-03-02 — SNMP 스위치 포트 진단 + 시각화)
- SNMP 스위치 포트 진단 — Mock 기반 13대 스위치 24포트 폴링 + NodeDetailPanel 포트 탭
  - snmp_poller.py (신규): SnmpPoller 클래스 (mock/real 듀얼모드, 조건부 pysnmp import)
  - snmp_poller.py: load_switches(), poll_all(), _poll_switch_mock(), _poll_switch_real()
  - snmp_poller.py: _save_port_status() UPSERT, get_ports(), get_system_info(), get_summary()
  - ai_server.py: tb_snmp_port_status DDL + _snmp_polling_loop (30초 후 첫 실행, 3분 주기)
  - ai_server.py: GET /network/snmp/{id}/ports, /network/snmp/{id}/system, /network/snmp/summary
  - src/lib/types/snmp.ts: SnmpPortStatus/SystemInfo/Summary + formatTraffic/formatSpeed/isSwitch
  - src/lib/api/snmp-api.ts: fetchSnmpPorts, fetchSnmpSystem, fetchSnmpSummary
  - src/hooks/use-snmp-ports.ts: lazy-load 훅 (탭 전환 시 활성화)
  - SwitchPortDiagram.tsx: SVG 포트 정면 다이어그램 (홀수 상단/짝수 하단, Up=emerald/Down=gray)
  - PortStatusTab.tsx: 요약카드 + SVG 다이어그램 + 필터 + 포트 테이블
  - NodeDetailPanel.tsx: 스위치 장비만 Tabs(정보/포트상태), 비-스위치는 기존 유지
  - 환경변수: SNMP_ENABLED(default:false), SNMP_COMMUNITY, SNMP_POLL_INTERVAL
  - 빌드 검증 통과

### 완료 (2026-03-03 — 인과관계 엔진 Phase 2)
- 캔버스 PropertyPanel 4탭 확장 — "인과 체인" 탭 추가 (정보/설비/태그/인과)
  - CausalChainTab.tsx (신규): 인과 체인 시각화 + 편집 + 구역 선택 + 자동 추정
  - PropertyPanel.tsx: grid-cols-3 → grid-cols-4, 4번째 탭 "인과" (causalChain 없으면 disabled)
  - canvas-types.ts: CausalChainStep/CausalCrossFacility/CausalZoneInfo/CausalChainResponse/CausalLagEstimate 타입
  - canvas-layout-api.ts: fetchCausalChain/saveCausalChain/deleteCausalChainOverride/estimateCausalLag 4 함수
  - use-node-detail.ts: causalChain 필드 추가, CAUSAL_FACILITY_TYPES 5종 조건부 fetch
- tb_causal_chain_override 오버라이드 테이블 + CRUD API
  - ai_server.py: DDL (SERIAL PK, UNIQUE(sitename, facilitytype, zone), JSONB chain/cross_facility)
  - ai_server.py: GET/PUT/DELETE /causal/chain/{sitename}/{facilitytype} (Pydantic BaseModel 사용)
  - ai_server.py: _build_causal_index 오버라이드 로딩, _rebuild_causal_index_entry 단건 갱신
  - ai_server.py: _get_causal_info(sn, ft, zone) 래퍼 (3-tuple 우선 → 2-tuple 폴백)
- 구역(1지/2지) 분리 — 배수지 태그 datainfo regex 구역 감지
  - ai_server.py: _detect_zones() — r'(\d)[지구역]' 패턴, zone별 tag_count/group_codes
  - ai_server.py: _CAUSAL_INDEX 3-tuple 키 확장 (sitename, facilitytype, zone)
  - anomaly_detector.py: verify_causal_context zone 파라미터 추가
  - ANOMALY_FACILITY_DETAIL: 이상 태그 datainfo에서 구역 자동 감지 → zone 전달
- 교차상관 시간 지연 자동 추정
  - causal_estimator.py (신규): numpy 교차상관 (Z-score 정규화, positive lag, scipy 미사용)
  - ai_server.py: POST /causal/estimate-lag — 14일 raw 데이터 → 연속 step 쌍 lag 추정
  - CausalChainTab: "자동 추정" 버튼 → amber 텍스트 표시 → "적용" 클릭 시 lag 교체
- SLM 자연어 해석 — Phi-4-mini 인과 판정 결과 자연어 변환
  - anomaly_detector.py: generate_causal_explanation() — _CAUSAL_EXPLAIN_PROMPT 3문장 해석
  - anomaly_detector.py: build_anomaly_facility_detail_block에 "AI 종합 해석" 섹션
  - ai_server.py: ANOMALY_FACILITY_DETAIL에서 chain_matched=false일 때 SLM 호출
- 빌드 검증 + API 전수 테스트 통과

### 완료 (2026-03-03 — 시설간 교차 검증)
- 시설간 교차 검증 시스템 — 상류→하류 유량/압력 흐름 일관성 자동 검증
  - anomaly_detector.py: cross_facility_check_single/all(), _check_edge(), _calc_active_rate/mean_direction()
  - anomaly_detector.py: build_cross_facility_detail_block/scan_block() 시맨틱 마커 포맷
  - anomaly_detector.py: _FACILITY_OUTPUT_GROUPS/INPUT_GROUPS 시설유형별 입출력 group_code
  - anomaly_detector.py: _wrap_marker 영문 level 직접 매칭 추가 (error/warn/ok)
  - anomaly_detector.py: build_anomaly_facility_detail_block에 cross_facility_result 파라미터 추가
  - ai_server.py: ANOMALY_FACILITY_DETAIL에 cross_facility_check_single 자동 통합
  - ai_server.py: ANOMALY_CROSS_FACILITY 커스텀 핸들러 (sync+SSE), _DYNAMIC_SQL_INTENTS 등록
  - ai_server.py: process_sql_result에 build_cross_facility_scan_block 연동
  - example3.json: ANOMALY_CROSS_FACILITY 67번째 인텐트 (12개 질문, graph_type:none)
  - intent_classifier.py: "교차 검증", "시설간 불일치", "상류 하류 비교" 키워드 규칙
  - intent_index.py: INTENT_DESCRIPTIONS 등록
  - query_validator.py: _SKIP_REQUIRED_CHECK 추가
  - 검증 유형: active_ratio(가동률 70%+ → 40%- 불일치), direction(상류 RISE + 하류 FALL 역전)
  - 프론트엔드 변경 없음 (graph_type:none + 시맨틱 마커)
  - 빌드 검증 통과

### 완료 (2026-03-03 — 태그 분류 오매칭 + 인과 검증 버그픽스)
- 태그 자동분류 복합 키워드 추가 — FLOW_INLET/OUTLET "유출유량순시" 등 6글자 복합어 우선 매칭
  - TAG_DATA_GROUPS: FLOW_INLET 키워드 +4 (유입유량순시/적산/순시유량/적산유량), FLOW_OUTLET 동일 +4
  - 분류 결과 변화: FLOW_INSTANT 108→60, FLOW_CUMULATIVE 101→55, FLOW_INLET 3→47, FLOW_OUTLET 4→54
  - 근본 원인: longest-keyword-first 전략에서 동일 길이(4글자) "유량순시"와 "유출유량" 중 리스트 순서 우선 매칭
- GROUP_THRESHOLDS import 누락 수정 — ANOMALY_FACILITY_DETAIL 핸들러 NameError 수정
  - ai_server.py: `from anomaly_detector import (..., GROUP_THRESHOLDS)` 추가
  - 이 에러로 인과 체인 검증 + 교차 시설 체크가 전체 try/except에 잡혀 무시되고 있었음
- 이상 태그 반복 탐색 — 첫 태그(잔류염소 등) group_code 미매칭 시 전체 스킵 → |z_score| 내림차순 순회
  - ai_server.py: _anomaly_rows 정렬 → group_code 매칭될 때까지 순회
- 형제 그룹(sibling) 매칭 — 소블록 FLOW_INSTANT↔FLOW_INLET 등 같은 부모 하위 그룹 호환
  - ai_server.py: _build_causal_index에서 resolved_tag_map 형제 그룹 폴백
  - anomaly_detector.py: verify_causal_context에 _SIBLING_MAP (FLOW 5형제, PRESSURE 3형제)
- 검증 결과: 남산 배수지 12항목(인과확인+하류영향 포함), 남산2 소블록 6항목(형제매칭), 갈산 가압장 9항목(z<임계→정상)
- 빌드 검증 통과

### 완료 (2026-03-04 — ANOMALY_SCAN_ALL 고도화)
- per-row grade/group 추가 — 각 데이터 행에 site_group/alert_grade 컬럼 보강
  - ai_server.py process_sql_result: site_profiles → classify_z_level_by_group + classify_alert_grade
  - `columns.extend(["site_group", "alert_grade"])` + `rows[:] = enriched_rows`
  - 결과: D=62, A=9, B=231, C=19 (per-row), warning=1, info=10, none=310 (grade)
- 교차 검증 SCAN_ALL 통합 — cross_facility_check_all을 ANOMALY_SCAN_ALL 핸들러에 추가
  - process_sql_result 외부에서 asyncio.to_thread로 비동기 실행 (이벤트 루프 블로킹 방지)
  - sync + SSE 양쪽 핸들러에 동일 적용
  - 결과: cross_facility_mismatches/count 필드 응답에 포함
- 하류 비활성 감지 — anomaly_detector.py _check_edge에 downstream_zero 체크 타입 추가
- 공용 헬퍼 _query_recent_values — 3곳(CROSS_FACILITY sync/SSE, FACILITY_DETAIL) 인라인 쿼리 통합
- AnomalyScanView.tsx 프론트엔드 — 서버 제공 grade/group 우선 사용 (classifyZLevel 폴백)
  - 테이블 뷰: "그룹" 컬럼 + site_group 뱃지 + alert_grade 뱃지
- 빌드 검증 + API 전수 테스트 통과 (321행, 13컬럼)
- **성능**: ~~응답 74.8초~~ → 캐시 히트 2.1초 (40배 개선, 아래 최적화 참조)

### 완료 (2026-03-04 — ANOMALY_SCAN_ALL 응답 최적화)
- 백그라운드 캐시 패턴 — 84초 전체 파이프라인을 5분 주기로 사전 계산, 캐시 히트 2.1초
  - ai_server.py: `_anomaly_scan_cache_loop()` (150s 초기 지연 → 5분 주기)
  - ai_server.py: `_compute_anomaly_scan_all()` (SQL+process_sql_result+cross_facility 전체 파이프라인)
  - ai_server.py: `_ANOMALY_SCAN_CACHE` / `_ANOMALY_SCAN_CACHE_TIME` / `_ANOMALY_SCAN_CACHE_TTL=300`
  - sync + SSE 핸들러: 캐시 freshness 체크 → 히트 시 즉시 반환 (early return)
- IForest 백그라운드 학습 — 요청 시 35초 블로킹 제거
  - ai_server.py: `_iforest_training_loop()` (90s 초기 지연 → 24h 주기)
  - process_sql_result: `ensure_trained()` 호출 제거, `predict_for_rows()`만 호출
  - 322개 모델 학습 (37초, 백그라운드에서 비동기 실행)
- Zero-flow z_score 정책 확정 — val≈0 → z=0 유지 (롤백)
  - 가압장 펌프 정지 등 val=0은 정상 운영 패턴 → z-score에서 이상 판정하지 않음
  - "뜻밖의 0" 판단은 인과 프로세스(verify_causal_context) + 교차검증(cross_facility_check)이 담당
  - deviation_pct: `ABS(mean_30d) < 0.001 THEN 0` 가드 유지 (division by zero 방지)
  - 설계 원칙: z-score=통계적 편차, 인과검증=물리적 의미 판단 (역할 분리)
- lifespan 등록: `_iforest_task` + `_anomaly_scan_task` (shutdown 시 cancel)

### 완료 (2026-03-04 — 인과검증 엔진 고도화)
- group_code 결정 개선 — 하드코딩 _GC_KEYWORDS → _resolve_group_code_for_tagsn() 교체
  - ai_server.py: 인라인 _GC_KEYWORDS 삭제, _CAUSAL_INDEX tag_map 우선 → _FALLBACK_GC_KEYWORDS 폴백
  - ai_server.py: 인라인 _causal_query_func 클로저 삭제 → 공용 _query_recent_values 사용
- 시설간 다중 홉 전파 추적 — BFS 기반 하류 전파 + 상류 근원지 역추적 (max_depth=3)
  - anomaly_detector.py: trace_propagation_forward() — 하류 BFS, 불일치 시 전파 중단 지점 기록
  - anomaly_detector.py: trace_upstream_root_cause() — 상류 역추적, 근원지 후보 + confidence(high/medium/low)
  - anomaly_detector.py: build_propagation_trace_block() — 시맨틱 마커 포맷 (근원지/전파중단/정상전파)
  - anomaly_detector.py: build_anomaly_facility_detail_block에 propagation_trace 파라미터 추가
  - anomaly_detector.py: _CAUSAL_PATTERNS에 UPSTREAM_PROPAGATION 패턴 추가
  - ai_server.py: process_sql_result ANOMALY_FACILITY_DETAIL에 전파 추적 통합 (인과/교차 결과 있을 때만)
  - 프론트엔드 변경 없음 (시맨틱 마커 {prefix, text} 형식 그대로)
  - 빌드 검증 + 단위 테스트 통과 (정상/불일치/리프노드 3 시나리오)

### 완료 (2026-03-04 — 설비↔태그 자동 매핑 Phase 1)
- 설비↔태그 자동 매핑 — _EQUIPMENT_GROUP_RULES 기반 그룹 레벨 매핑, 3,375건 자동 생성
  - ai_server.py: _EQUIPMENT_GROUP_RULES 상수 (가압펌프/유량계/PLC/LTE 모뎀 4종)
  - ai_server.py: _PUMP_NUM_RE — datainfo "가압펌프N" 패턴 1:1 매칭
  - ai_server.py: _auto_map_equipment_tags() — 설비별 규칙 적용 + ON CONFLICT DO NOTHING
  - ai_server.py: _resolve_group_list(), _map_pumps() 헬퍼
  - ai_server.py: GET /equipments/auto-map (dry_run 파라미터) API
  - ai_server.py: lifespan에서 _build_causal_index 직후 자동 실행
  - 매핑 결과: PLC 2,185건 + 가압펌프 519건 + LTE 모뎀 603건 + 유량계 68건
  - 가압펌프 1:1: "가압펌프N" → N번째 equipment, 번호 없는 태그 → 전체 펌프 공유
  - 네트워크 장비(L2/L3/UTM 등): 매핑 제외
  - 프론트엔드 변경 없음 (기존 EquipmentTab/EquipmentTagLinker 그대로)

### 완료 (2026-03-04 — 센서 점검 인텐트 통합 + 시설/센서유형 필터링)
- "전체 센서 점검해줘" = "전체 센서 이상 스캔해줘" → ANOMALY_SCAN_ALL 동일 처리
- 시설유형별 필터: "소블록/배수지/가압장/소소블록 센서 점검해줘" → facilitytype WHERE 절
- 센서유형별 필터: "유량/압력/수질 센서 점검해줘" → group_code JOIN 필터
  - intent_classifier.py: common_keywords에 "센서 점검/설비 점검/센서 스캔" 추가, _TAG_LATEST_EXCLUDE에 "점검/스캔/센서" 추가
  - param_extractor.py: _SITENAME_FUZZY_STOPWORDS에 "센서/유량계/수질계/압력계/장비/계측기" 추가
  - param_extractor.py: _FT_FUZZY_SKIP — "유량계"→"유량계실" fuzzy 오매칭 방지
  - ai_server.py: build_anomaly_facility_filter에 group_code SQL 필터 추가
  - ai_server.py: _filter_anomaly_cache_rows — 캐시 히트 시 facilitytype/group_code 필터
  - ai_server.py: _GROUP_CODE_LABELS — group_code→한글 범위 표시
  - ai_server.py: sync+SSE 캐시 히트 양쪽에 필터+카운트 재계산 적용
  - 결과: 전체 321건, 소블록 67건, 배수지 140건, 가압장 113건, 유량 82건, 압력 54건, 수질 43건

### 완료 (2026-03-04 — 소블록 인과 템플릿 수정 + snapshot_zero 교차 검증)
- 소블록/소소블록 CAUSAL_CHAIN_TEMPLATES 수정 — FLOW_INLET+WATER_LEVEL → FLOW_INSTANT+PRESSURE
  - ai_server.py: 소블록/소소블록 인과 체인 템플릿 group_code 교체
  - 근본 원인: 대부분 소블록에 FLOW_INLET(유입유량) 없고 FLOW_INSTANT(유량순시)만 존재 → 31/39 소블록 인과 인덱스 누락
  - 수정 후: 39/39 소블록 전체 인과 인덱스 등록
- snapshot_zero 교차 검증 체크 타입 추가 — 상류 최신값 활성 + 하류 최신값 ≈ 0 감지
  - anomaly_detector.py: _get_latest_value() 헬퍼 (시계열 끝 = 최신값)
  - anomaly_detector.py: _check_edge에 snapshot_zero 체크 (us_latest > 1.0 + ds_latest < 0.01)
  - anomaly_detector.py: build_cross_facility_detail_block + scan_block에 downstream_zero/snapshot_zero 표시
  - 결과: 남산11 소블록 → active_ratio(100%→24.4% error) + direction(RISE→FALL error) + snapshot_zero(warn)
- z-score zero-flow 정책 유지 — val≈0 → z=0 (펌프 정지 등 정상 운영 패턴, 인과/교차 검증이 물리적 판단 담당)

### 완료 (2026-03-04 — ANOMALY_SCAN_ALL 종합 판정 통합)
- 교차검증 결과를 per-row로 매핑 + 종합 판정(verdict) 컬럼 추가
  - anomaly_detector.py: map_cross_mismatches_to_facilities, compute_verdict, enrich_rows_with_cross_verdict
  - ai_server.py: _compute_anomaly_scan_all + sync/SSE 3곳 enrichment, 캐시 히트 cross_anomaly_count 재계산
  - ai_server.py: build_success_response에 cross_anomaly_count 전달
- 종합 판정 5단계: 복합이상(z이상+cross) > 이상(z이상) > 교차이상(z정상+cross) > 주의(z주의) > 정상
  - 남산11 케이스: z_score=0 + 상류 유량 활성 → verdict="교차이상" (이전: "정상")
- 프론트엔드 통합
  - chat.ts: CrossFacilityMismatch/CrossFacilityCheck 타입, AnomalyData/AiServerResponse 확장
  - chat-response-mapper.ts: crossFacilityMismatches/crossAnomalyCount 패스스루
  - anomaly-utils.ts: VERDICT_COLOR, CROSS_CHECK_LABEL 상수
  - AnomalyScanView.tsx: verdict 기반 필터/정렬/카운트, "교차"+"판정" 컬럼, KPI 교차이상 카드, CrossFacilitySummary 접기 요약
- 빌드 검증 + 단위 테스트 통과

### 완료 (2026-03-04 — 교차검증 임계값 강화 + 남산11 감지)
- 교차검증 오탐 축소 — 유량 전파 지연에 의한 false positive 제거
  - _CROSS_ACTIVE_HIGH/LOW: 70/40 → 85/15 (확실한 단절만 감지)
  - direction 체크 제거 (전파 지연으로 일시적 역전이 정상 패턴)
  - snapshot_zero 체크 제거 (간헐 운영 노이즈)
  - sudden_drop 임계값 완화: first_active 0.5→0.35 (남산11 45% 활성 패턴 포착)
- recent_inactive 체크 신규 — 상류 85%+ 활성인데 하류 최근 60분 전부 0
  - 전체 윈도우 22% 활성이라도 "지금" 죽어있으면 이상
  - 남산11: 180분 중 42분 활성(22%) → active_ratio/downstream_zero 미감지 → recent_inactive로 감지
- 교차검증 UI 상단 배치 — 고장에 가장 근접한 정보이므로 눈에 잘 띄는 상단 고정
  - AnomalyScanView.tsx: CrossFacilitySummary를 KPI 위로 이동 + 항시 표시(접기 제거) + 펄스 인디케이터
  - 결과: 2 edge (남산→남산10: active_ratio+downstream_zero, 남산→남산11: sudden_drop+recent_inactive)
  - verdict 분포: 이상 8, 주의 10, 교차이상 9, 복합이상 2, 정상 294

### 완료 (2026-03-05 — 기준선 오염 방지 + 데이터 품질 감지)
- hourly_holding CTE — 5분 버킷 flat 감지 + 시간 단위 집계 → 기준선에서 홀딩 시간 제외
  - example3.json: ANOMALY_SCAN_ALL + ANOMALY_FACILITY_DETAIL 양쪽 SQL CTE 삽입
  - raw_adaptive CTE: is_flat (min_val=max_val), bucket_hr (date_trunc hour) 추가
  - hourly_holding CTE: 1시간 내 전 버킷 flat + 1개 고유값 + 6건 이상 → 비영 홀딩 판정
  - stats_segment + stats_global: LEFT JOIN hourly_holding + WHERE IS NULL (홀딩 시간 제외)
  - 검증: 행정2-2 유량순시 deviation_pct 174.8% → 36.5% (오판 해소), 전체 278건 리그레션 없음
- 데이터 품질 이상 감지 (Layer 2) — ANOMALY_SCAN_ALL 결과에서 빠진 DEAD/홀딩 센서 별도 표시
  - ai_server.py: _detect_data_quality_issues() — Analog Input(NOT 적산/설정) 차집합 + 7일 상태 분류
  - 분류 4종: 센서무응답(val≈0), 데이터홀딩(flat>80%), 데이터없음(7일 무데이터), 데이터부족(active<50)
  - _compute_anomaly_scan_all 5단계에 통합 + build_success_response 패스스루
  - chat.ts: DataQualityIssue 인터페이스, AnomalyData.dataQualityIssues 필드
  - chat-response-mapper.ts: dataQualityIssues 패스스루
  - anomaly-utils.ts: DQ_ISSUE_COLOR, DQ_ISSUE_ICON 상수
  - AnomalyScanView.tsx: DataQualitySection 컴포넌트 (빨간 테두리+펄스, 유형별 카운트 뱃지, 접기/펼치기)
  - 결과: 76건 (센서무응답 62, 데이터홀딩 12, 데이터없음 2)
  - 남산10 유량순시유량, 남산11 유량계실 유속/압력, 행정 유입유량순시 모두 감지 확인
- 단기 홀딩 감지 (recent_holding) — 결과에 포함된 태그의 최근 1시간 홀딩 의심 인라인 표시
  - example3.json: recent_holding CTE (1h 내 전 버킷 flat + 1개 고유값 + val > 0)
  - AnomalyScanView.tsx: SensorItem.recentHolding 파싱 + 센서명 옆 주황 "홀딩?" 뱃지
  - KPI 카드: "홀딩의심" 주황 카드 (조건부 표시)
  - 결과: 278행 중 23건 recent_holding='Y' (매방리 유량순시, 가곡 수위 등)
- 설비 장애 역추적 Phase 2 — 설비 통신 끊김/장애 → 연결 태그 자동 점검 + UI
  - ai_server.py: _detect_equipment_failures() — 3가지 신호 (network_down/DI fault) + tb_equipment_tag_map 역추적
  - ai_server.py: _compute_anomaly_scan_all 6단계 통합 + per-row equip_failure 컬럼
  - ai_server.py: build_success_response + 캐시 히트 패스스루 (sync/SSE)
  - chat.ts: EquipmentFailureImpact 타입, AnomalyData/AiServerResponse 확장
  - chat-response-mapper.ts: equipmentFailureImpacts/Count 패스스루
  - anomaly-utils.ts: EQUIP_FAILURE_COLOR/LABEL/ICON 상수 (4종: network_down/comm_error/equip_fault/power_fault)
  - AnomalyScanView.tsx: EquipmentFailureSection (보라 테두리+펄스), per-row 인라인 뱃지 (NET/COM/FLT/PWR), "설비장애" KPI 카드
  - 결과: 59건 장애 설비, 98/278 per-row 매핑, 4종 장애 유형 (설비고장3, 전원이상12, 네트워크단절36, 통신이상8)
- 데이터 품질 감지 버그수정 — _detect_data_quality_issues s24[0] tagsn→s24[1] total_24h 인덱스 수정 (76건 복원)
- enrich_rows_with_cross_verdict 후 tuple 호환 — rows[:] = [tuple(...)] 패턴으로 per-row 컬럼 추가
- 인과 규칙 구축 메뉴 UI — 구축 사이드바에 "인과 규칙" 전용 페이지 추가
  - ai_server.py: GET /causal/rules 확장 — 시설 커버리지 + 오버라이드 현황 + tag_coverage per-step
  - setup.ts: CausalChainStepDef/CausalRuleTemplate/CausalFacilityStatus/CausalRulesResponse 타입
  - causal-rules-api.ts: fetchCausalRules API 클라이언트
  - setup/causal-rules/page.tsx: 요약 카드 4개 + 시설유형 5탭 + 템플릿 시각화 + 시설 테이블(프로그레스 바, 행 확장 상세)
  - sidebar-menus.ts: M200-12 "인과 규칙" 메뉴 추가
  - 결과: 96 시설 중 95 인과 적용, 58 완전 매핑 (가압장29, 배수지25, 감압시설0, 소블록39, 소소블록2)

### 완료 (2026-03-05 — 물 수지 검증 Mass Balance)
- 물 수지 검증 시스템 — 상류 유출유량 vs 하류 유입유량 합계 비교, 누수 의심 구간 감지
  - flow_balance.py (신규, ~280줄): 핵심 모듈
    - classify_balance_grade: <5% 정상, 5-15% 관심, 15-25% 주의, >25% 경고
    - _integrate_instantaneous: 순시유량 사다리꼴 적분 (m³/h → m³)
    - _cumulative_delta: 적산 태그 last-first delta
    - _compute_facility_volume: 시설별 유량 계산 (적산 우선, 순시 폴백)
    - compute_flow_balance_all: 전체 네트워크 수지 검증 (24h 롤링, 70% 커버리지)
    - build_flow_balance_scan_block: 시맨틱 마커 포맷
  - example3.json: ANOMALY_FLOW_BALANCE 68번째 인텐트 (10개 질문)
  - intent_classifier.py: "물 수지/물수지/유량 균형/불명수량/누수 구간" 키워드 규칙
  - intent_index.py + query_validator.py: 등록
  - param_extractor.py: "수지" fuzzy 오매칭 방지 (_FT_FUZZY_SKIP + _SITENAME_FUZZY_STOPWORDS)
  - ai_server.py: 8개소 수정
    - _FLOW_BALANCE_CACHE + _flow_balance_cache_loop (200초 지연, 30분 주기)
    - _query_flow_timeseries + _get_tag_datainfo_cache 헬퍼
    - ANOMALY_FLOW_BALANCE sync+SSE 커스텀 핸들러
    - process_sql_result: build_flow_balance_scan_block 호출 + flow_balance_summary 생성
    - build_success_response: flow_balance_summary 패스스루 (sync+SSE 양쪽)
    - _compute_anomaly_scan_all 7단계: 캐시 참조 flow_balance_summary
  - chat.ts: FlowBalanceDownstream/Edge/Summary 타입, AnomalyData 확장
  - chat-response-mapper.ts: flowBalanceSummary 패스스루
  - anomaly-utils.ts: FLOW_BALANCE_GRADE_COLOR 상수
  - AnomalyScanView.tsx: FlowBalanceSummarySection (청록 테두리, worst 5 엣지, 유량 비교 바) + "유량불균형" KPI 카드
  - 로컬 DB tb_facility_flow_map 스키마 교체 (76건 remote→local 복사)
  - 검증: 원격 DB 테스트 21 edges (경고 13, 관심 6, 정상 2), 빌드 통과

### 완료 (2026-03-06 — 시설 내부 인과 검증 Intra-Facility)
- 시설 내부 물리법칙 기반 인과 검증 — 펌프/밸브/수위/압력 6개 규칙
  - anomaly_detector.py: _INTRA_RULES 6개 (가압장3 + 배수지3 + 감압시설1)
    - 가압장: 펌프ON→토출압력, 펌프ON→유출유량, (PRESSURE_DISCHARGE fallback: PRESSURE_OUTLET→PRESSURE)
    - 배수지: 밸브OPEN→유출유량, 유입유량→수위 not_falling, 수위하강→유출유량(누출의심)
    - 감압시설: 유입압력→유출압력
  - anomaly_detector.py: verify_intra_facility(), _check_intra_condition/effect(), build_intra_facility_block()
  - anomaly_detector.py: _CAUSAL_PATTERNS 2개 추가 (LEVEL_DROP_NO_OUTFLOW, INLET_PRESSURE_NO_OUTLET)
  - anomaly_detector.py: _FACILITY_OUTPUT_GROUPS 가압장에 PRESSURE_DISCHARGE 추가
  - ai_server.py: ANOMALY_FACILITY_DETAIL 핸들러에 intra-facility 검증 통합
  - ai_server.py: build_success_response에 intra_facility 패스스루 (sync+SSE 양쪽)
  - chat.ts: IntraFacilityResult 타입, AnomalyData/AiServerResponse 확장
  - chat-response-mapper.ts: intraFacility 패스스루
  - 검증: 복운/고대리 가압장 (펌프ON → 정상판정), 남산 배수지 (유입유량→수위 정상판정)
  - 빌드 검증 통과

### 완료 (2026-03-06 — 용수 흐름 실시간 모니터링)
- 용수 흐름 실시간 모니터링 페이지 — 모니터링 메뉴 하위, 유량 비례 엣지 + 수치 오버레이
  - ai_server.py: GET /flow-map/realtime (토폴로지 + 시설별 최신 유량/수위/압력 + 교차검증 + 물수지)
  - ai_server.py: _group_priority() 헬퍼 (OUTLET > INSTANT > INLET 우선순위)
  - ai_server.py: tb_tag_group_map JOIN tb_tag_data_group (group_code 정확 매칭)
  - flow-monitoring-api.ts: fetchFlowMapRealtime + FlowRealtimeNode/FlowEdgeImbalance 타입
  - FlowMonitoringGraph.tsx: Sankey SVG 기반 실시간 계통도
    - 엣지: 상류 유량 비례 두께(2~14px) + 색상(회색→하늘→파랑), 유량 0=얇은 회색
    - 물수지 불균형 엣지: 경고=빨강, 주의=주황, 관심=노랑 + Bezier 중간점 %뱃지
    - 교차검증 이상 노드: 빨간 펄스 링 + 교차검증 상세 툴팁
    - 노드 수치 오버레이: Q(유량)/H(수위)/P(압력) 소형 텍스트
    - 하이라이트: 클릭 시 상류+하류 경로만 강조, 나머지 dim
  - monitoring/flow/page.tsx: KPI 4종(유량활성/없음/교차이상/불균형) + 계통 필터 + 60초 자동 갱신
  - sidebar-menus.ts: M003-4 "용수 흐름" 모니터링 하위 메뉴 추가
  - 검증: 79엣지 80노드, 유량 41/80 활성, 빌드 통과

### 완료 (2026-03-07 — HTTPS 전환 + Ollama 백오프 최적화 + 용수 흐름 계통도 교정)
- HTTPS 전환 — 브라우저→Next.js HTTPS, Next.js→API HTTP (내부 프록시)
  - mkcert 인증서: d:\web\certs\localhost.pem + localhost-key.pem
  - .env.local: HTTPS_ENABLED=true, NEXT_PUBLIC_API_URL=http://localhost:8000 (프록시는 HTTP 유지)
  - package.json: dev:https / dev:https:fast 스크립트 추가
- Ollama 백오프 최적화 — Ollama 비가용 시 요청 응답 38초→2.6초 (15배 개선)
  - 원인: embed_query(10초 타임아웃) + generate(30초 타임아웃) 매 요청 대기
  - intent_embeddings.py: 연결 실패 후 60초간 embed_query 즉시 None 반환
  - ollama_client.py: 연결 실패 후 60초간 generate() 즉시 에러, health_check 성공 시 리셋
  - ai_server.py: 서버 시작 시 health_check 실패하면 양쪽 백오프 즉시 설정
- 용수 흐름 계통도 교정 — tb_facility_flow_map 79→95엣지
  - facilitytype 수정 4건, 비존재 시설 삭제 5건, 누락 시설 추가 19건
  - install_location 기반 상류 추정 (석우/복운/율사/매방리/용연2통/죽동2)

### 완료 (2026-03-07 — 용수 흐름 배수지 공급가능시간 표시)
- 용수 흐름 실시간 모니터링에 배수지 용수공급가능시간(T) 오버레이 추가
  - ai_server.py: /flow-map/realtime에 tb_service_reservoir_status 조회 (total_supply_time, supply_time_status, supply_time_reason)
  - ai_server.py: 컬럼 미존재 시 안전 스킵 (try/except + conn.rollback)
  - flow-monitoring-api.ts: SupplyTimeInfo 타입 + FlowRealtimeNode.supply_time 필드
  - FlowMonitoringGraph.tsx: MetricOverlay에 T(공급시간) 표시 + 상태별 색상 (24h+하늘/12h+녹/6h+노/빨)
  - FlowMonitoringGraph.tsx: 툴팁에 공급시간 + 상태사유 표시
  - monitoring/flow/page.tsx: API 에러 시 nodes undefined 방어 (realtime.status === "OK" 체크 + ?? {} 폴백)
  - 로컬 DB: tb_service_reservoir_status에 total_supply_time/supply_time_status/supply_time_reason 컬럼 추가 + 원격 데이터 동기화

### 완료 (2026-03-07 — 쿼리 최적화 + 누락 함수 생성 + 기간 추출 수정)
- 야간최소유량 청크 최적화 — fn_night_min_flow_summary 대체 (31.9초→7.5초, 4.3배)
  - ai_server.py: _execute_night_min_flow_query() — 청크 직접 쿼리 + numpy 60분 이동평균
  - ai_server.py: sync+SSE 커스텀 핸들러 (NIGHT_MIN_FLOW_SUMMARY_TABLE)
  - UTC 타임존 보정 (time_bucket 호환), 원본 대비 100건 전수 일치 검증
- 결측분석 청크 최적화 — fn_tag_daily_summary 대체 (15.5초→2.8초, 5.5배)
  - ai_server.py: _execute_tag_daily_summary_query() — 청크별 분단위 SQL 집계 + Python 홀딩 계산
  - 466만 raw 행 Python 전송 → SQL 분단위 집계로 데이터량 100배 축소
  - ai_server.py: sync+SSE 커스텀 핸들러 (TAG_DAILY_MISSING_SUMMARY)
  - 원본 대비 31행 전수 일치 검증
- 누락 PostgreSQL 함수 13개 생성 — 원격 DB에서 추출 → 로컬 DB 적용
  - db/create_missing_functions.sql: fn_night_min_flow_stats, fn_trend_period_summary 등 13개
  - FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS: ERROR → OK 수정
- "N개월" 기간 추출 수정 — "최근 3개월간" 미파싱 → 기본 7일 폴백 문제
  - param_extractor.py: r"(\d+)\s*개월" 패턴 추가 (1개월=30일, 3개월=90일 등)
  - 기간 미지정 시 기본 7일 유지 (기존 동작 호환)
- 표준편차분석 청크 최적화 — fn_night_min_flow_stats 5회 호출 대체 (53초→3.6초, 15배)
  - ai_server.py: _execute_night_min_flow_stddev_query() — 400일 1회 조회 + Python 통계 계산
  - avg/stddev/신뢰구간/초과량 + 금월/금년 평균 전부 Python 산출
  - sync+SSE 커스텀 핸들러 (FACILITY_NIGHT_MIN_FLOW_STDDEV_ANALYSIS)
- SSE 핸들러 sse_event → _sse_event 오타 수정 (3곳)
- 전수 테스트 스크립트
  - test_all_intents.py: 68개 인텐트 첫 번째 질문 자동 테스트
  - test_perf.py: 68개 인텐트 응답 시간 측정 + TOP 15 느린 인텐트 표시

### 완료 (2026-03-07 — 다중 시설 표준편차분석 뷰)
- 다중 시설 표준편차분석 뷰 — "전체 소블록 야간최소유량 표준편차분석" 분석 뷰 구현
  - ai_server.py: build_success_response에 stddev_stats_list 패스스루 추가
  - ai_server.py: sync+SSE 핸들러 — "%%" → "전체" 치환, 커스텀 답변 생성 (정상N/초과N)
  - chat.ts: StddevSiteStats/StddevMultiData 타입, stddev_multi VisualData union 추가
  - chat-response-mapper.ts: stddev_stats_list → stddev_multi 매핑 (stddev_stats 앞에 우선 배치)
  - StddevMultiAnalysisView.tsx (신규): KPI 4종 + 비교 바 차트 + 미니 정규분포 카드 그리드
  - VisualRenderer.tsx: stddev_multi 렌더링 분기 추가
  - 빌드 검증 통과

### 완료 (2026-03-08 — 용수 흐름 모니터링 UI 고도화)
- 통신이상 노드 점멸 표시 — Digital Input `datainfo LIKE '%통신이상%'` 기반 CSS step-end 애니메이션
- KPI 카드 클릭 필터 — 유량없음/교차검증이상/유량불균형/통신이상 카드 클릭 시 해당 노드 글로우 강조
  - page.tsx: activeFilter 상태 + filterNodeIds useMemo + toggleFilter 콜백
  - FlowMonitoringGraph.tsx: filterNodeIds prop, soft-dim 엣지(opacity .25), fm-filtered 노드(brightness+drop-shadow)
  - KpiCard: active ring + onClick 토글
- 선 교차/글자 겹침 Sugiyama 미세조정 시도 → 롤백 (오히려 악화)
  - 블록별 그룹핑 레이아웃으로 재설계 예정

### 완료 (2026-03-08 — 이동평균 확장 + 계통도 그룹핑 레이아웃)
- 모니터링 이동평균 확장 — 6개월/1년 조회 시 1개월/1년 이동평균 활성화
  - trend.ts: 6M interval 720→360min, 1Y interval 1440→720min (데이터 포인트 증가)
  - moving-average.ts: 가용성 판정 80% → 100% 완화 (expanding window + 버퍼 프라이밍으로 유효)
  - 6M: 720포인트 → ma30d 윈도우 120 ✓, 1Y: 730포인트 → ma1y 윈도우 730 ✓
- 계통도 블록별 그룹핑 레이아웃 — 선 교차/글자 겹침 근본 해결
  - flow-diagram-chart.ts: Sugiyama 이후 그룹 재배치 (primaryParent 기반)
  - 같은 상류 부모의 자식 노드를 수직 인접 배치
  - 그룹 간 GROUP_GAP=14px 여백으로 시각적 구분
  - 그룹 정렬: 부모의 레이어 내 인덱스 순
  - 결과: 신평공업/신평생활→우강→합덕 계통 인접 배치, 행정→행정1-2 교차 해소
  - 빌드 검증 통과
- 용수 흐름 프로토타입 HTML v2 — san.MD + SAN개선.MD 개선사항 반영
  - flow-monitoring-v2.html: 미니맵, 계층형 그룹화, LOD, 검색 하이라이트
  - 배수지 공급시간(T) 노드 오버레이 + 하단 공급가능시간 카드 바

### 완료 (2026-03-08 — v2 프로토타입 프로덕션 적용)
- 용수 흐름 모니터링 v2 기능 6종 프로덕션 적용
  - FlowMonitoringGraph.tsx: 미니맵 + 검색 + LOD + 계통 그룹 뷰 + 물흐름 파티클 통합
  - 미니맵: 좌하단 200×130px 축소 계통도, 뷰포트 사각형 표시, 클릭→해당 위치 이동
  - 검색: 좌상단 검색 아이콘, 시설명/시설유형 필터, Enter→첫 결과 포커스, 클릭→노드 줌+포커스 애니메이션
  - LOD: 줌 55% 이하에서 수치 오버레이(Q/H/P/T) 자동 숨김 (CSS .lod-hidden .fm-metric-overlay)
  - 계통 그룹 뷰: FlowViewMode "detail"|"grouped", BFS 루트별 그룹 카드, 상세/계통 토글 버튼
  - 물흐름 파티클: SVG animateMotion 3입자/엣지, dur=3.5s, 유량>0 엣지만, 불균형=빨강/정상=에메랄드
  - FlowNodeTrendPanel.tsx (신규): 노드 클릭 시 24h 스파크라인 차트 (Canvas), 유량/수위/압력 3종
  - page.tsx: 상세/계통 토글 + FlowNodeTrendPanel 통합 + 기존 MetricDisplay 제거
  - 빌드 검증 통과

### 완료 (2026-03-09 — 엣지 교차 수정 + 상태 기반 노드 색상)
- 엣지 슬롯 정렬 교차 수정 — 가나다순 → 타겟 노드 y좌표순 (합덕2/합덕3 교차 해소)
  - flow-diagram-chart.ts: links.sort를 source y → target y 기준으로 변경
- 상태 기반 노드 색상 — 진행 중 알람(경고/주의) 시 노드 빨강/주황 + 깜빡임
  - ai_server.py: tb_equipment_alarm_report 진행중 알람 조회, alarm_severity 필드 추가
  - flow-monitoring-api.ts: FlowRealtimeNode.alarm_severity 타입 추가
  - FlowMonitoringGraph.tsx: deriveNodeStatus에 알람 등급 반영, rect fill 상태 기반 색상, 알람 경고 링, 툴팁 알람 표시
  - page.tsx: "알람 진행" KPI 카드 + 알람 필터 추가 (6컬럼)
  - 빌드 검증 통과

### 완료 (2026-03-09 — 캔버스 태그 링크 저장/취소 + 2줄 표시)
- 캔버스 에디터 태그 링크 저장/취소 패턴 — 즉시 API → pending 로컬 관리
  - EquipmentTab.tsx: pendingAdds/pendingDeletes 상태 + effectiveTags useMemo
  - 저장 버튼: 일괄 API 호출 (삭제 먼저, 추가 후), 취소 버튼: pending 초기화
  - amber 저장/취소 바, 설비별 변경됨 표시, 추가(녹색)/삭제(빨강) 시각 표시
  - EquipmentTagLinker.tsx: 즉시 API → 부모 onLink 콜백 패턴 전환
- 태그 목록 2줄 레이아웃 — tagsn 위, datainfo(unit) 아래 서브텍스트
  - EquipmentTab.tsx + EquipmentTagLinker.tsx: 연결/삭제/검색 3곳 일관 적용

### 완료 (2026-03-09 — 용수 계통도 설비 장애 표출)
- 설비 장애 4종 감지 + 계통도 노드 뱃지 표시 — 네트워크 단절/통신이상/설비고장/전원이상
  - ai_server.py: /flow-map/realtime에 설비 장애 감지 추가 (네트워크 is_alive=false + DI 장애 3종)
  - flow-monitoring-api.ts: EquipFailure 타입 + FlowRealtimeNode.equip_failures 필드
  - FlowMonitoringGraph.tsx: 노드 하단 뱃지(NET/COM/FLT/PWR) + 툴팁 상세 + deriveNodeStatus 확장
  - page.tsx: "설비 장애" KPI 카드(보라색 Wrench) + 클릭 필터 (7종 KPI)
  - 결과: 24시설 감지 (네트워크7, 통신10, 설비고장3, 전원12)
  - 빌드 검증 + Playwright 검증 통과

### 완료 (2026-03-10 — 상류 유형별 교차검증 + fixed 트렌드 패널)
- 상류 시설유형별 교차검증 분리 — 배수지(중력식 80%) vs 가압장(펌프 30%+압력) vs 기타(30%)
  - ai_server.py: _GRAVITY_ACTIVE_RATIO=0.80, _LOW_ACTIVE_RATIO=0.30
  - ai_server.py: gravity_no_flow/pump_no_flow 체크 타입 추가
  - ai_server.py: _ds_us_ft/_ds_us_pressure 상류 시설유형+압력 추적
  - ai_server.py: cross_mismatches 빈 엔트리 필터링
  - FlowMonitoringGraph.tsx: gravity_no_flow/pump_no_flow 툴팁 라벨
- 갈산→성북 불균형 필터링 — 하류 데이터 없는 구간 제외
  - flow_balance.py: downstream_no_data 상태 추가 (태그 있지만 volume≈0 + coverage<50%)
- viewMode 전환 시 이벤트 리스너 재등록 — 계통↔상세 전환 버그 수정
  - FlowMonitoringGraph.tsx: 3개 useEffect 의존성에 viewMode 추가
- 노드 클릭 시 화면 하단 fixed 트렌드 패널 — 슬라이드업/다운 애니메이션
  - page.tsx: fixed bottom 패널, left=--sidebar-width, z-index:50
  - page.tsx: trendVisible 상태 + requestAnimationFrame 슬라이드 애니메이션
  - FlowNodeTrendPanel.tsx: 24h 스파크라인(Canvas) + 알람 목록 오버레이
  - flow-monitoring-api.ts: EquipFailure/SupplyTimeInfo 타입 추가
  - 계통도 높이 550px 고정 (레이아웃 리플로우 없음)
- 빌드 검증 + Playwright 검증 통과

### 완료 (2026-03-11 — 데모 완성본: 이미지 업로드 + 위치도 표시 수정)
- multipart 업로드 truncation 수정 — ReadableStream 소진 문제 해결
  - route.ts: request.body → await request.arrayBuffer() (73바이트→412KB 정상 전송)
  - route.ts: duplex: "half" 제거 (arrayBuffer는 duplex 불필요)
- DEMO_MODE multipart 역변환 — POST form-data sitename/region 코드→원본 변환
  - ai_server.py: upload_facility_file에 _demo_restore_text() 추가
- 위치도 중복 표시 제거 — 이미지 + "시설 위치" 카드 → 이미지만 표시
  - VisualRenderer.tsx: DiagramView에서 diagram_type="location" 시 null 반환
  - example3.json: 4개 위치 인텐트 answer_template에서 install_location 텍스트 + reference 섹션 제거
    - BLOCK_LOCATION, RESERVOIR_LOCATION, BOOSTER_STATION_LOCATION, PRESSURE_REDUCING_FACILITY_LOCATION
- 용수 흐름 계통 필터 수정 — tb_facility_flow_map DEMO코드('GSU') → 한글('정수장') 교체
- 임베딩 캐시 재빌드 (example3.json 변경 반영)
- 빌드 검증 + Playwright E2E 검증 통과

### 완료 (2026-03-12 — 트렌드 데이터 부족 안내 + HH/LL 재생 유지)
- 트렌드/모니터링 6개월+ 조회 시 데이터 부족 안내 — 로컬 DB 38일분만 보유 시 amber 배너 표시
  - trend-store.ts: dataGapInfo 상태 (요청 기간 vs 실제 데이터 비교, 80% 미만 시 안내)
  - monitoring-view-store.ts: 동일 dataGapInfo 패턴 적용
  - trend/page.tsx + MonitoringFacilityPage.tsx: amber 배너 UI
- 모니터링 HH/LL 가이드선 재생 시 사라지는 버그 수정
  - 원인: 재생 시 dataZoom 10% 윈도우 → Y축 자동 축소 → HH/LL 값이 축 범위 밖으로 밀림
  - TrendChart.tsx: alarmMarkLines 존재 시 Y축 min/max를 함수로 설정하여 HH/LL 항상 포함
  - 빌드 + Playwright 검증 통과

### 완료 (2026-03-13 — 용수 흐름 계통도 고도화: 타임라인+구역선택+기준선비율+엣지유량)
- 타임라인 재생 기능 — 과거 24h 데이터 5분 간격 슬라이더 재생
  - use-flow-timeline.ts (신규): 타임라인 훅 (태그 수집, fetchTrendData, 프레임별 노드 오버라이드)
  - page.tsx: TimelineBar 컴포넌트 (재생/정지/슬라이더/시간표시), 타임라인↔실시간 전환
  - page.tsx: 타임라인 모드에서 자동 갱신 일시정지 + 교차검증/불균형 숨김
- 배수지 구역 선택 탭 — 다중 수위 태그(1지/2지, 공업/생활) 전환
  - ai_server.py: /flow-map/realtime에 level_zones 배열 추가 (HH/LL/설정값 필터 + 자연정렬)
  - flow-monitoring-api.ts: FlowNodeMetric.datainfo + FlowRealtimeNode.level_zones 타입
  - FlowNodeTrendPanel.tsx: extractZoneLabel (공업용수→공업, 생활용수→생활 축약) + 구역 탭 UI
  - use-flow-timeline.ts: level_zones tagsn 수집 + 타임라인 오버라이드
- 평소 대비 비율 표시 — 수치 옆 baseline_avg 대비 % (색상: <50% 빨강, 50-80% 주황, >150% 파랑)
  - ai_server.py: /flow-map/realtime에 baseline_avg 추가 (7일 동일 요일·시간대 평균)
  - FlowMonitoringGraph.tsx: MetricOverlay에 ratioText/ratioColor + baselineRatioColor()
- 엣지 유량 수치 라벨 — Bezier t=0.3 지점에 유량 수치 표시 + LOD 연동
  - FlowMonitoringGraph.tsx: EdgeFlowLabel 컴포넌트 + fm-edge-flow-label CSS (줌 55% 이하 숨김)
- RootSummaryBar 삭제 — 미사용 ~90줄 제거
- 빌드 검증 + Playwright 검증 통과

### 완료 (2026-03-13 — 종합 현황판 Dashboard Overview)
- 종합 현황판 — 기존 캐시 데이터 집계 + 클릭→AI 채팅 자동 질문 연결
  - ai_server.py: GET /dashboard/overview 엔드포인트 (캐시 집계 + 24h 경보 조회)
    - _ANOMALY_SCAN_CACHE: KPI 카운트, verdict 분포, TOP 15 시설, 시설유형 분포
    - _FLOW_BALANCE_CACHE: 유량 불균형 요약
    - processed_data: data_quality_issues, equipment_failure_impacts
    - tb_equipment_alarm_report: 24시간 내 최근 경보 20건
  - dashboard-api.ts: 7개 인터페이스 + fetchDashboardOverview API 클라이언트
  - dashboard/page.tsx: KPI 6종 카드 + 이상시설 TOP + 유량불균형 + 시설유형분포 + 설비장애 + 데이터품질 + 최근경보
    - 모든 항목 클릭 → router.push(/chat?q=...) → AI 채팅 자동 질문 제출
    - 5분 자동 갱신 + 캐시 시간 표시
  - chat/page.tsx: useSearchParams → ?q= 파라미터 자동 질문 제출 + URL 정리
  - proxy route.ts: PUBLIC_PATHS에 "dashboard" 추가
  - 빌드 검증 통과

### 완료 (2026-03-14 — 물 수지 인포그래픽 + 시설별 필터링 + 대시보드 버그수정)
- 물 수지 인포그래픽 — ANOMALY_FLOW_BALANCE 전용 시각화 컴포넌트
  - FlowBalanceInfographic.tsx (신규): SVG 게이지(불균형률) + 등급별 분포 카드 + 수지 분포 바 + 파이프 카드(상류/하류 비교 바)
  - chat.ts: flow_balance VisualData union 타입 추가
- 물 수지 시설별 필터링 — "송산2산단생활 물 수지 검증해줘" → 해당 계통만 표시
  - ai_server.py: _filter_flow_balance_edges() 헬퍼 (upstream/downstream sitename 매칭)
  - sync + SSE 핸들러 캐시 히트/미스 양쪽에 필터 적용
  - chat-response-mapper.ts: ANOMALY_FLOW_BALANCE 인텐트 → flow_balance 타입 라우팅
  - VisualRenderer.tsx: flow_balance 렌더링 분기
- 대시보드 최근 경보 미표시 버그수정
  - ai_server.py: _get_conn() (미정의) → get_db_connection() 교체 (2곳)
  - 원인: NameError 예외 → except 블록에서 빈 배열 반환
- 대시보드 설비 장애 현황 "더보기" 개선
  - 8건 표시 + max-height 스크롤 + "더보기 →" 클릭 버튼 (AI 채팅 이동)
- 대시보드→채팅 자동 질문 미실행 버그수정
  - dashboard/page.tsx: router.push → window.location.href (full navigation으로 ChatPage fresh mount 보장)
  - chat/page.tsx: useSearchParams 제거, window.location.search 직접 폴링
  - 대시보드 9개 질의 전수 검증 완료 (2건 수정)
- 빌드 + Playwright 검증 통과

### 완료 (2026-03-14 — 대시보드 팝업 전환 + 경보분석 상세)
- 대시보드 클릭 질의 팝업 전환 — 채팅 이동 대신 QuickAnalysisDialog 팝업
  - dashboard/page.tsx: goChat()를 window.location.href → setQaOpen(true) 팝업 전환
  - KPI 카드, 이상시설, 유량불균형, 설비장애, 데이터품질 — 모두 팝업 적용
  - 데이터 품질 항목: onGoChat prop 추가 + "전체 센서 점검해줘" 통일 질의
- 최근 경보 클릭 → 경보분석 상세 팝업
  - ai_server.py: GET /crisis/alarm-analysis/detail?tagsn=...&alarm_start_time=... 단건 조회 API
  - crisis-api.ts: fetchAlarmAnalysisDetail() 함수 추가
  - dashboard/page.tsx: AlarmAnalysisDetail 컴포넌트를 Dialog 팝업으로 렌더링 (요약 카드 + diagnosed_msg iframe)
- 빌드 검증 통과

### 완료 (2026-03-14 — 모니터링 설정/블록 그룹핑 + 경보관리 고도화)
- 모니터링 설정 현장명 전체 표시 — tb_monitoring_catalog → tb_tag_info 소스 변경
- 감압시설 탭 추가 — MONITORING_CONFIGS + FACILITY_KEYS 확장
- 블록 모니터링 상류 시설 기준 그룹핑 — 계통 드롭다운 (남산 배수지(11), 합덕 배수지(2) 등)
  - GET /monitoring/catalogs/site-groups API (tb_facility_flow_map BFS 2단계 그룹핑)
  - fetchMonitoringSiteGroups API 클라이언트
  - MonitoringFacilityPage: 블록일 때 계통 Select + 하위 사이트 탭 필터링
- 모니터링 사이트 탭 가로 스크롤 — flex-wrap → overflow-x-auto (42개 블록에서 차트 가림 해소)
- 경보관리 행 클릭 → 경보분석 상세 팝업 — AlarmReportTable onRowClick + Dialog
- 경보관리 30건 페이징 — ChevronLeft/Right, 페이지 표시
- tb_monitoring_catalog PK 시퀀스 리셋 (last_value 4 → MAX 6)
- tb_task_master 테이블 생성 (Node-RED 작업관리 알람 억제용)
- Node-RED DB 접속 수정 (localhost → slm-timescaledb) + web_default 네트워크 연결
- Node-RED 펌프 정보 SQL 수정 (cagg_daily_pressure c.sitename → t.sitename)
- Node-RED JOIN LATERAL 12곳 시간 제한 추가 (now() - interval '10 minutes')
- 빌드 + Playwright 검증 통과

### 완료 (2026-03-15 — 용수 흐름 계통도 레이아웃 근본 개선)
- 서브계통 기반 수직 분리 — depth-1 분기를 서브계통 ID로 사용, BFS 전파
  - flow-diagram-chart.ts: nodeTopRoot(정수장별) + nodeRoot(서브계통별) 2단계 그룹핑
  - 서브계통 정렬: 하류 그룹키(같은 하류 공유 시 인접) → leafCount → 이름순
  - 루트 간 ROOT_GAP*2(56px), 서브계통 간 ROOT_GAP(28px), 부모 그룹 간 GROUP_GAP(14px)
- 보령댐/아산 정수장 계통 완전 분리 — 석문이 아산 계통에 섞이는 문제 해소
- 신평공업+신평생활→우강 인접 배치 — 같은 하류 공유 서브계통 연속 정렬
- 엣지 오프셋 클램핑 — inOffset/outOffset이 노드 높이 초과 시 노드 범위 내로 제한
  - 신평생활→우강 엣지 도착점 1060→999.7 (우강 범위 958~1033 내)
  - 다수 엣지가 동일 노드에 연결될 때 마지막 엣지 빗나감 방지
- 커밋: b59588d → 09082bd → 9430570 → fe9c387
- 모니터링 전체 시설유형 계통 그룹핑 — 배수지/가압장/감압시설/블록 모두 적용 (129dc5f)
- 배수지 일평균 유입/유출/사용량 — mv_reservoir_daily_flow 적산 max-min 방식 482건
  - Node-RED: 1시간 주기 갱신 플로우 추가 (운영현황 갱신 탭)
  - 적산유량 이상값 필터 (delta > 100,000 m³ 제외, 카운터 리셋 노이즈)
- 용수 흐름 배수지 패널 확장 — ▼ 버튼 클릭 시 유입/유출/사용량 표시 (4928711)
  - SupplyTimeInfo에 avg_inflow/avg_outflow/avg_usage 추가
  - /flow-map/realtime API에 v_reservoir_info_status JOIN

### 완료 (2026-03-15 — 모니터링 UI + 캐시 + 계통도 고도화)
- 모니터링 설정 현장명 전체 표시 + 감압시설 탭 추가
- 블록 모니터링 상류 시설 기준 그룹핑 → 계통 드롭다운 (이후 비활성화)
- 모니터링 사이트 탭 → FacilityCombobox 검색 드롭다운 교체
  - 권역 그룹핑 + 검색 필터 + 현장 카운트
  - rootFilter prop으로 정수장 계통별 필터링 가능
- site-groups API 재귀 BFS 6단계 확장 (정수장까지 추적)
  - 보령댐 5→7개, 아산 6→14개 배수지 올바른 그룹핑
- 경보관리 행 클릭 → 경보분석 상세 팝업 + 30건 페이징
- 대시보드 클릭 질의 팝업 전환 (채팅 이동 → QuickAnalysisDialog)
- cagg_5min_raw_stats_ai 자동 리프레시 정책 추가 (5분 주기)
- 캐시 빌드 초기 지연 단축 (IForest 10s, SCAN_ALL 20s, FlowBalance 30s)
- _compute_anomaly_scan_all 플레이스홀더 치환 수정 (빈 params 전달)
- 계통도 교차 최소화 — 리프 먼저 + 하류 노드 이름순 정렬
- 엣지 최소 두께 3px (EDGE_ZERO_W 1.5→3)
- flow_map: 송악1-1→고대리 연결 추가, 미분류 17건 동기화
- Node-RED JOIN LATERAL 12곳 시간 제한 추가 (10분)
- Node-RED 펌프 의사결정 트리 UPDATE null 가드 추가
- 대시보드 팝업 전환 (채팅 이동 → QuickAnalysisDialog) + 경보분석 상세 팝업
- 경보관리 행 클릭 → 경보분석 팝업 + 30건 페이징
- cagg_5min_raw_stats_ai 자동 리프레시 정책 추가 (5분 주기)
- 캐시 빌드 초기 지연 단축 (IForest 10s, SCAN_ALL 20s, FlowBalance 30s)
- _compute_anomaly_scan_all 플레이스홀더 치환 수정 ({anomaly_facility_filter} 빈 params)
- tb_site_anomaly_profile DDL 자동 생성 확인
- site-groups API 재귀 BFS 6단계 확장 (정수장까지 추적)
- 파티클 별도 SVG 레이어 분리 (엣지 겹침 → 파티클 안 보임 해소)
- 엣지 ID 한글 보존 (`\W` → `[^a-zA-Z0-9가-힣_]`, 모든 엣지 ID 충돌 근본 수정)
- __EXPAND__ 마커 프론트 필터링 (BotMessage + QuickAnalysisDialog)
- KPI 카드 레이아웃 flex-wrap 전환 (글자 잘림/넘침 해소) — 검증 완료
- 작업관리 mock 현장명 한글화 (B/F/G/D시설 → 남산/복운/매방리/행정)
- 채팅 SSE 진행 표시 라벨+도트 스타일 (분류→추출→조회→렌더링)

### 즉시 진행 가능
(현재 비어있음. 새 항목은 사용자 요청 시 추가.)

### 보류/후순위
3. **배수지 이상 스캔 컴팩트 레이아웃** — 보류 (유저 요청으로 리버트, 재논의 필요)
4. **엑셀 템플릿 보고서** — 프롬프트 기반 SLM 확장 (후순위)
5. **EPANET 수리 시뮬레이션** — 장기 과제 (SHP→inp변환 + wntr시뮬 + GIS히트맵, On/Off 토글 방식)
6. **UTM/SSLVPN 계층적 통신이상 감지** (사양 확정 후 구현)
   - 인텐트명(안): `NETWORK_UPSTREAM_FAULT_ANALYSIS`
   - 트리거 키워드: "상위 장비", "왜 다 통신이상", "UTM 이상", "SSLVPN 문제", "통신이상 원인"
   - 로직: `tb_network_link` 재귀 CTE로 UTM→SSLVPN→LTE 계층 트리 + `tb_network_status` 최신값 조인
   - 사양 확정 필요: 임계값(80% vs 전체), 신규 인텐트 vs 기존 `NETWORK_COMM_STATUS` 확장

### 완료 처리 이력 (이전 남은/향후 항목 중 완료된 것)
- ~~인과 규칙 엔진 고도화 (선형 체인 → 조건부 규칙 그래프)~~ — 완료 (2026-04-20 재확인: 선행조건 `_check_prerequisite`, 안전연동 `verify_safety_interlocks`, 역방향 추적, AND 조건 `verify_and_conditions`, 다홉 전파 모두 `anomaly_detector.py`에 이미 구현. `PREREQUISITE_FAILED`/`SAFETY_INTERLOCK_VIOLATED`/`AND_CONDITION_VIOLATED` 패턴 정의 완비)
- ~~인과관계 내재화 확장 (가압장→소블록 cross-facility)~~ — 완료 (2026-04-20 재확인: `anomaly_detector.py:1654 _CROSS_RULES[0]`에 구현됨. 가압장→소블록, 배수지→가압장/소블록, 감압시설→소블록, 소블록→소소블록 5종 cross-rule 활성)
- ~~TIMESERIES 태그 조회 카탈로그 우선 전환~~ — 완료 (tb_tag_data_group 그룹 기반 전환)
- ~~인과관계 엔진 Phase 2~~ — 완료 (캔버스 인과 탭 + 구역 분리 + 교차상관 + SLM 해석)
- ~~시설간 교차 검증~~ — 완료 (ANOMALY_FACILITY_DETAIL 자동 + ANOMALY_CROSS_FACILITY 인텐트)
- ~~태그 분류 오매칭 + 인과 검증 버그픽스~~ — 완료 (복합 키워드, import 누락, 형제 그룹 매칭)
- ~~ANOMALY_SCAN_ALL 고도화~~ — 완료 (per-row grade/group, 교차검증 통합, 하류 비활성)
- ~~ANOMALY_SCAN_ALL 응답 최적화~~ — 완료 (84초 → 2.1초, 백그라운드 캐시 + IForest)
- ~~인과검증 고도화~~ — 완료 (group_code 정확매칭 + 다중 홉 전파 추적)
- ~~설비↔태그 자동 매핑 Phase 1~~ — 완료 (3,375건, 4종 장비유형)
- ~~용수 흐름 계통도 레이아웃 개선~~ — 완료 (서브계통 분리 + 엣지 클램핑)
- ~~배수지 일평균 유입/유출량~~ — 완료 (mv_reservoir_daily_flow + Node-RED)
- ~~모니터링 그룹핑 미분류 정리~~ — 완료 (원격 DB 17건 동기화)
- ~~Node-RED 펌프 의사결정 트리 누락 경로~~ — 완료 (fallback 확인, UPDATE null 가드)
- ~~용수 흐름 계통도 블록별 그룹핑 레이아웃~~ — 완료 (primaryParent + GROUP_GAP)
- ~~v2 프로토타입 프로덕션 적용~~ — 완료 (미니맵+검색+LOD+계통그룹+파티클+트렌드패널)
- ~~교차 검증 고도화~~ — 완료 (물 수지 검증 ANOMALY_FLOW_BALANCE)
- ~~디자인 개선~~ — 완료 (2026-03-22, 8단계)
- ~~코드 품질 종합 개선~~ — 완료 (모듈 분리 15,084→12,450줄)
- ~~설비 장애 역추적 Phase 2~~ — 완료 (59건 장애 설비, 4종 장애 유형)
- ~~용수 계통도 설비 상태 표출~~ — 완료 (4종 장애 뱃지, 24시설, KPI 7종)
- ~~용수 흐름 실시간 모니터링~~ — 완료 (유량 비례 엣지 + 교차검증/물수지)
- ~~성능 최적화~~ — 완료 (2026-03-22)
- ~~UX 개선~~ — 완료 (2026-03-22)
- ~~GIS 클러스터 범위 확대~~ — 완료 (2026-04-04)
- ~~용수 흐름 알람 클릭 → 경보분석 팝업~~ — 완료
- ~~인과관계 내재화 6종~~ — 완료 (PUMP_ON_NO_PRESSURE/FLOW, VALVE_OPEN_NO_FLOW, INLET_FLOW_NO_LEVEL_RISE, LEVEL_DROP_NO_OUTFLOW, INLET_PRESSURE_NO_OUTLET)
- ~~알람→작업관리 억제 로직 #36~~ — 완료 (04-05, alarm-reports task_suppressed)
- ~~계정 권한 Phase 1(인증API) + Phase 2(동적메뉴)~~ — 완료 (04-03 auth_crud.py + 04-05 use-sidebar-menus.ts)
- ~~계정 권한 Phase 3(메뉴 접근 제어) + Phase 4(MASTER 메뉴 관리)~~ — 완료 (04-07 권한 매트릭스 UI + DB 시드)
- ~~GIS 관망도 유량 흐름 오버레이~~ — 완료 (04-06~07, Phase 1~4)
- ~~ai_server.py 모듈 분리 추가~~ — 완료 (04-07, 3개 모듈 추출, 12,724줄)

### 완료 (2026-04-04 — 팝업 크기 통일 + GIS 레이어 z-order 수정)
- **팝업 크기 `max-w-2xl max-h-[75vh]` 통일** — 대시보드 내 모든 팝업창 동일 크기
  - `dashboard/page.tsx`: 경보 분석 상세 Dialog (max-w-4xl → max-w-2xl)
  - `alarm-dashboard/page.tsx`: 위기대응 경보분석 Dialog (max-w-4xl → max-w-2xl)
  - `QuickAnalysisDialog.tsx`: 이상시설/유량불균형 AI분석 (w-1600px/98vw → max-w-2xl)
  - `GisAlarmPopup.tsx`: GIS 경보 분석 팝업 width 600→520, maxHeight 80vh→60vh (원복)
- **GIS SHP 레이어 z-order 수정** — 도형 레이어(SHP)가 클러스터 마커 위로 올라오는 버그 수정
  - `GisFacilityMarkers.tsx`: `map.on("idle", bringMarkersToTop)` — idle 시 마커 레이어 최상단 이동
  - `GisShpLayer.tsx`: `beforeId="facility-circles"` 제거 (race condition E-006 해소)
  - 레이어 z-order: SHP 폴리곤 → 알람링 → 시설심볼 → 라벨 → 클러스터원/숫자
- **alarm-pulse-ring interpolate 수정** — CLUSTER_MAX_ZOOM=14 변경 후 줌 스텝 중복 (14,14,16 → 14,16,18)
- **시작/에러 사양서 신규** — `docs/error-management.md` + `docs/startup-spec.md`
  - E-001~E-006 에러 원인·해결·재발방지 기록

### 완료 (2026-04-05 — Task 15 사용자 수정 + 14-c)

- **15. 사용자 수정 다이얼로그** — `UserEditDialog.tsx` 신규 + `admin/users/page.tsx` handleEdit 연결
  - `UserUpdateFormData` 타입 추가 (이름·권한·선택적 비밀번호 변경)
  - `UserEditDialog`: 현재 사용자 정보 자동 로드, 이름·권한 수정 + 비밀번호 선택 변경
  - `admin-user-api.ts` updateUser: `Partial<AdminUser>` → `UserUpdateFormData` 타입 정확화
  - `handleEdit` stub → `setEditTarget` → 다이얼로그 오픈으로 교체
  - Playwright 3회 테스트 통과 (admin, operator1 수정 다이얼로그 열기·저장)

### 완료 (2026-04-05 — 3개 UI 개선)
- **14. GIS 클러스터 범위 확대** — `CLUSTER_MAX_ZOOM` 12 → 14 (`GisFacilityMarkers.tsx`)
  - 전체보기(zoom~11)에서 시설 이미지 대신 숫자 동그라미로 표시, zoom 15+ 부터 개별 SVG 전환
- **14-b. 배수지 이상 스캔 컴팩트 레이아웃** — `AnomalyScanView.tsx` early return 추가
  - `rawData.length === 0` 시 KPI카드/차트 생략 → 1줄 컴팩트 상태 카드로 표시
  - 교차검증/데이터품질/설비장애/유량불균형 이슈 있으면 목록 함께 표시
- **14-c. 용수 흐름 알람 클릭 → 경보분석 팝업** — `FlowNodeTrendPanel.tsx` + `flow/page.tsx`
  - `FlowNodeTrendPanel`: `AlarmRow`에 `role="button"` + hover 스타일 + `onAlarmClick` prop 추가
  - `flow/page.tsx`: `alarmDetailOpen/alarmDetailRecord/alarmDetailLoading` 상태 추가
  - `openAlarmDetail(tagsn, startTime)` → `fetchAlarmAnalysisDetail` 호출 → `AlarmAnalysisDetail` Dialog 표시
  - 팝업 규격: `max-w-2xl max-h-[75vh]` (대시보드 팝업 표준과 동일)

### 완료 (2026-04-04 — GIS 클러스터 동그라미 개선)
- **클러스터 클릭 애니메이션** — `flyTo/fitBounds` duration 2000ms + `essential: true` 적용
  - 전체보기 버튼(`GisMapRef.flyTo`)과 동일한 애니메이션 동작으로 통일
  - `essential: true`: 사용자 `prefers-reduced-motion` 설정과 무관하게 애니메이션 항상 실행
- **클러스터 레이어 최상단 보장** — GisFacilityMarkers.tsx 레이어 렌더 순서 변경
  - 변경 전: clusterCircle → clusterCount → symbolLayer → labelLayer → alarm-pulse-ring
  - 변경 후: alarm-pulse-ring → symbolLayer → labelLayer → clusterCircle → clusterCount
  - 클러스터 숫자 동그라미가 SHP 레이어, 알람링 등 모든 레이어 위에 항상 표시

### 완료 (2026-04-03 — 배수지 공급량 인텐트 4종 + 쿼리 최적화)
- **배수지 공급량 인텐트 4종 신규** — RESERVOIR_DAILY/MONTHLY_SUPPLY_TABLE/CHART
  - ai_server.py: `_execute_reservoir_supply_query` 신규 (LATERAL + generate_series 인덱스 스캔)
  - chat-response-mapper.ts: `supply_chart` 타입 매핑, TABLE_COLUMN_MAP에 `unit` 컬럼 추가
  - ReservoirSupplyChart.tsx: ECharts 막대그래프 (daily MM-DD / monthly YYYY-MM X축)
  - types.ts: `SupplyChartData` 타입 추가
  - VisualRenderer.tsx: `supply_chart` 분기 추가
- **공급량 쿼리 최적화 (300× 속도향상)** — 974,100행 풀스캔/24.5s → 736행/0.08s
  - LATERAL + generate_series 구조로 경계일 인덱스 직접 탐색
  - `idx_tag_raw_tagsn_time ON (tagsn, logtime DESC)` 역방향 스캔 활용
  - `generate_series` → `gs::date` 서브쿼리 캐스트 (timestamptz→date 타입 불일치 수정)
  - psycopg2 GUC: `max_parallel_workers_per_gather=0` (Docker WSL shared memory 오류 방지)
  - API 응답 단대단: 4.2s
- **경보 상위 건수 오류 수정** — alarm top count 인텐트 정상화
- **인텐트 분류 수정** — "그래프" + "공급량" 조합이 FACILITY_TREND로 오분류되던 버그 수정
  - intent_classifier.py: `_is_supply` 가드 추가 (공급량 인텐트 트렌드보다 우선)
- **Playwright 5종 테스트 통과** — 일별/월별 테이블+차트 4종 + 알람 상위건수 1종

### 완료 (2026-04-03)
- **인증 API 구현** — Python `/api/auth/login|refresh|logout|me` + 사용자 관리 + 접속 이력
  - endpoints/auth_crud.py: 신규 (bcrypt 직접 사용, python-jose JWT, 평문→bcrypt 자동 마이그레이션)
  - tb_user 실제 로컬 컬럼 기반 (user_pw_hash, pw_migrated, lock_cnt int, last_login varchar)
  - 로그인 실패 5회 → 계정 잠금 (lock_cnt >= MAX_LOGIN_ATTEMPTS), ADMIN 이상 해제
  - GET /api/auth/users, POST /api/auth/users, PUT /api/auth/users/{id}, POST /users/{id}/unlock
  - GET /api/auth/access-logs (ADMIN 이상, tb_access_log 조회)
  - requirements.txt: python-jose[cryptography], bcrypt 추가
- **proxy PUBLIC_PATHS 보안 강화** — 전체 경로 공개→login/refresh/health/models 4개만 공개
- **auth.ts dev 폴백 제거** — admin/1234, kwater/1234, dev-token 하드코딩 계정 전면 삭제
- **로그인 페이지 dev 힌트 제거** — "개발 모드: admin/1234 또는 kwater/1234" 텍스트 삭제
- **GIS clusterMinPoints=1** — 단독 시설도 원 "1" 표기
- **GIS 클러스터 클릭→fitBounds** — getClusterLeaves → 단일:flyTo zoom16, 복수:fitBounds maxZoom15
- **GIS SVG 지도 마커** — data URL 캔버스 방식 (map.loadImage Chrome SVG 미지원 우회)

### 완료 (2026-04-02)
- **GIS 심볼 KS 표준화** — 12종 SVG 아이콘을 KS B 0052 P&ID 표준 심볼로 교체 (투명 배경 벡터)
  - 밸브 6종: 보타이 형태 통일 + 색상/마크 구분 (G/B/S/D/A/P)
  - 알람 링 minzoom=12 (클러스터 줌 유령 링 제거), icon-ignore-placement 통일
- **GIS 고도화 Phase 1~2** — 마커 클러스터링 + SHP캐시 + 좌표검증 + 트렌드아이콘 + 팝업포맷 + 투명도슬라이더
- **GIS SVG 아이콘 24종 도입** — C-Water NAVI 참고사이트에서 밸브10종+시설8종+기타6종 추출
- **지도 마커 circle→symbol 변환** — SVG→ImageData→map.addImage() + styleimagemissing 대응
- **SVG 배경 투명화 + 크기 축소** — fill:#fff→fill:none, 24px, icon-size 0.35~0.7
- **ANOMALY_SCAN_ALL sitename 정확매칭** — 부분매칭→정확매칭, SSE 3곳 필터 적용
- **작업관리 전면 구현** — CRUD + 시설드롭다운 + 알람억제 + 개별태그 + 필터/정렬
- **jykim MASTER 계정** — 3단계 권한(MASTER/ADMIN/USER) + 메뉴 숨김/표시 관리
- **Node-RED 알람 아날로그값** — DB 트리거 fn_fill_alarm_analog_value + 126건 백필
- **미들웨어 인증 복원** + 캔버스 중복 수정 + 네트워크 장비 설비검색 + 프롬프트 숨김
- **EPANET 계획 기록** — 향후 별도 모듈, On/Off 토글 방식

### 완료 (2026-03-29)
- **기능 사양서 전면 작성** — docs/feature-spec.md 34개 섹션 (전 메뉴 커버)
- **용수흐름도→GIS 팝업 연동** — FlowNodeTrendPanel에 트렌드/시설/알람/진단 4버튼 추가, GIS 팝업 컴포넌트 재사용
- **Node-RED 통신이상 + 알람→작업관리** 사양서 작성 (feature-spec.md #35, #36)
- **미들웨어 인증 복원** — matcher에서 대시보드 라우트 제외 해제, 모든 페이지 인증 필수
- **GIS 시설정보 CRUD** — 사진 업로드/표시 + 제조사/설치연도 표시 (pump 객체 flat 변환)
- **AI Server** — `/gis/facility-info` 응답에 site_photo_url 추가
- **작업관리 전면 구현** — tb_task_master DB + CRUD API + TaskFormDialog
  - 작업등록: 시설유형→현장명 연동 드롭다운 (83개, DB facility_map)
  - 작업종류: 점검/정비/교체/청소/기타 선택
  - 억제 알람유형: 전체 버튼 + 개별 유형 9종 + 개별 태그 검색/추가
  - 개별 태그: 시설 선택 후 /tags API 태그 검색 → 하늘색 뱃지 추가/제거
  - 조회 필터: 작업일자(시작/종료) + 시설유형 + 현장명(드롭다운) + 작업종류 + 내용(키워드) + 진행중만
  - 테이블 정렬: 시간/현장명/시설유형/작업종류/중지알람/상태 (컬럼 클릭 asc/desc)
  - GisAlarmPopup 진행중 알람 → "작업등록" 버튼 연동
- **프롬프트 메뉴 숨김** — 현재 미사용

---

## 향후 계획 — SLM 고도화 (Zero-Hallucination 아키텍처)

> **배경**: Gemma4:26b (현재, Mac 17GB) → A30 24GB + Gemma4 12B (납품 표준) → L40S + Gemma4 27B (고품질)
> **핵심 원칙**: LLM은 라우터·분류기만 담당, 사실(수치/이름/ID)은 100% DB에서만 생성

### Phase 0 — 현재 (Gemma4:26b, 지금 당장 적용 가능)

**목표**: 기존 68 인텐트 구조 유지, 분류 정확도 + 답변 품질 + 할루시네이션 방어 기반 구축

#### A. 인텐트 분류 정확도 개선

- [ ] **오타/구어체 처리** — example3.json 동의어 질문 추가 (501개 → 600개+ 목표)
  - 오타 패턴: "수압" → "수알", "가압장" → "가압쟝" 등 현장 구어체 수집
  - 약칭 전처리: "1호가압" → "1호 가압장" 정규화를 intent_classifier.py 진입 전에 처리

- [ ] **오분류 패턴 수집** — 운전원 "원하는 답이 아닌가요?" 클릭 이력 → DB 저장
  - 수집만 자동화, example3.json 반영은 담당자 검토 후 수동 적용
  - ※ 검토 없는 자동 반영은 품질 저하 위험 → 수동 게이트 필수

- [ ] **벡터 임계값 최적화** — ※ 오분류 데이터 300건+ 축적 후 진행
  - 지금 튜닝하면 데이터 부족으로 과최적화 위험
  - 오분류 수집 후 혼동 인텐트 쌍 분석 → 인텐트별 차등 임계값 적용

#### B. 답변 품질 고도화

- [ ] **날짜 표현 파싱** — "그저께", "이번 달 초", "지난주 화요일" → 절대 날짜 변환
  - Python dateparser 또는 커스텀 규칙으로 LLM 개입 없이 전처리 단계에서 처리

- [ ] **시설명 약칭 매핑 테이블** — tb_facility 기반 약칭 → facility_id 사전 구축
  - "1호가압" / "1가압" / "1호 가압장" → 동일 facility_id 매핑
  - DB 테이블로 관리 (코드 하드코딩 금지)

- [ ] **시맨틱 마커 일관 적용** — `<<ok>>` `<<warn>>` `<<error>>` 전 인텐트 표준화
  - 현재 일부만 적용 → 모든 핸들러 응답에 상태 마커 포함
  - 임계값은 tb_tag_info 기반 (HH/HL/LL 설정값 활용)
  - 프론트엔드 BotMessage.tsx 마커 렌더링 고도화

- [ ] **프롬프트 구조 최적화** — Gemma4:26b 컨텍스트 128K 활용 few-shot 설계
  - 인텐트별 3~5개 few-shot 예시 (26b 컨텍스트 여유 충분 → 풍부한 예시로 분류 안정화)
  - JSON Schema 출력 지정으로 슬롯필링 구조화 응답 안정화

#### C. 할루시네이션 방어 레이어

- [ ] **Entity 검증 레이어** — LLM 추출값을 DB 조회로 실제 ID 치환
  - `facility_name ILIKE %...%` 퍼지 매칭 → 실제 facility_id 교체
  - 미발견 → "시설명을 확인해주세요" 반환 (추측 금지)
  - 복수 매칭 → 채팅 UI에 후보 버튼 표시 후 사용자 선택 (프론트엔드 신규 컴포넌트)

- [ ] **값 주입 프롬프트** — Post-hoc 검증 대신 Pre-hoc 제약으로 방식 변경
  - ~~regex 숫자 검증~~ → 오탐 과다로 폐기
  - 대신: LLM 프롬프트에 "사용 가능한 수치: {db_values}" 명시적 주입
  - LLM이 다른 수치를 생성할 원천 차단 (생성 후 검사 → 생성 전 제약)
  ```python
  prompt = f"""
  [사용 가능한 수치만 사용하세요]
  {json.dumps(db_result_values)}
  이 값들로만 설명하고, 목록에 없는 수치는 절대 사용하지 마세요.
  """
  ```

- [ ] **SQL 생성 완전 차단** — 모든 SQL을 SQL_TEMPLATES dict로 고정
  - LLM이 SQL 문자열을 반환하는 경로 제거
  - 파라미터는 psycopg2 바인딩만 허용 (f-string 금지)

---

### Phase 1 — 납품 서버 (A30 24GB + Gemma4 12B)

**목표**: 인텐트 68개 → 200개 확장, 서술 품질 향상, 보고서 초안 생성

- [ ] **인텐트 200개 확장 (Slot-Filling 유지)**
  - LLM 역할: 분류(N-class) + 파라미터 추출(JSON)만, SQL 미개입
  - SQL 템플릿 설계 시 복수 시설 파라미터 처음부터 반영
    ```sql
    WHERE facility_id = ANY(%(facility_ids)s)  -- 단수/복수 통일 패턴
    ```
  - 인텐트 목록을 프롬프트에 전부 나열하지 않음 → 카테고리 기반 2단계 분류
    (1단계: 대분류 10개 → 2단계: 소분류 20개씩)

- [x] **보고서 초안 자동 생성** — 완료 (보고서 P1 + 채팅 자동초안, `docs/report-spec.md`)
  - **P1 항목 AI 요약**: 이력(`tb_task_master`) 다중 선택 → 항목별 Ollama 자연어
    요약 + 인라인 편집 + 확정(`draft`→`finalized`) 잠금 + 인쇄용 A4 PDF
  - **P2 채팅 자동초안**: "이번 주 장애 보고서 만들어줘" → 기간·유형 자동 추출 →
    후보 미리보기 → 확정 생성 (`endpoints/chat_report_create.py`,
    `POST /chat/report/draft` → `/chat/report/confirm`)
  - **사람 검토 단계 충족**: 초안 → 담당자 인라인 수정 → 확정 잠금 워크플로
  - 납품처별 양식 차이 대응: `report_type` 분기 + 섹션 템플릿 분리
  - 잔여(별도 사양): Word(.docx) 내보내기 + 서버측 puppeteer PDF (P1은 브라우저
    인쇄 PDF 로 대체), 검토·승인 결재 체인, 주간·월간 스케줄러

- [x] **이상감지 원인 설명 생성** — 완료 (`fbed558`, 2026-04-12)
  - 선행 조건 충족: anomaly_scan.py + tb_tag_group_map JOIN 기반 실구현 완료
  - 탐지 결과(수치) → "값 주입 프롬프트" + 수치 whitelist 검증 + 결정적 fallback 3단 방어
  - 운전원 권고 문구는 고정 템플릿 유지 (LLM 생성 금지)
  - 엔드포인트: `POST /anomaly/explain`

---

### Phase 2 — 고품질 서버 (Mac Mini Pro 또는 L40S + Gemma4 27B)

**목표**: 응답 품질 최상위, 멀티모달 참고 기능, 장기 컨텍스트 대화

- [ ] **멀티모달 현장 사진 분석** — Zero-Hallucination과 분리된 별도 경로
  - 결과는 "AI 참고 의견"으로만 제공, DB 수치 기반 판단과 명확히 구분 표시
  - 분석 결과를 운영 데이터로 사용 금지 (면책 문구 포함)
  - 폐쇄망 사진 업로드 경로 별도 설계 필요 (모바일 → 내부망 서버)

- [ ] **EPANET 결과 해석** — ※ EPANET 모듈 구현 완료 후 진행
  - 선행 조건: EPANET 수리 시뮬레이션 모듈 구현 (docs/gis_plan.md)
  - 시뮬레이션 JSON → LLM 해석은 "설명 전용", 운영 지시 금지
  - 수리학 판단은 LLM이 내리지 않음 → EPANET 결과 수치를 그대로 표시

---

### 아키텍처 원칙 (불변)

```
[질문]
  → ① Intent 분류   : LLM (N-class 택1, 자유 생성 없음)
  → ② Entity 추출   : LLM 후보 → DB 검증 → 실제 ID 치환
  → ③ SQL 실행      : 고정 템플릿 + 검증된 파라미터 (LLM 개입 없음)
  → ④ 답변 생성     : DB 결과값 주입 프롬프트 → LLM 서술 (값 외 생성 차단)

사실 출처: 100% DB
LLM 역할: 분류 + 라우팅 + 포맷팅 전용
멀티모달: 별도 경로, "참고 의견"으로만 제공
```

---

### 태그 분류 체계 현황 (문서화 참고)
- **tb_tag_info.datainfo**: 태그의 실제 의미 (유출유량, 유입압력, 수위 등) — 유일한 의미 정보원
- **tb_tag_data_group** (21그룹): FLOW_INLET/OUTLET/INSTANT/CUMULATIVE, PRESSURE_INLET/OUTLET/DISCHARGE 등 계층 구조
- **tb_tag_group_map**: datainfo 키워드 기반 자동 분류 (2,508/2,698건 = 93%)
- **인과 체인 tag_map**: group_code → [tagsn 리스트] — 시설별 인과 step에 태그 배정
- **이상감지 group_code 결정**: 현재 ai_server.py에서 datainfo 키워드 하드코딩 (pass 스텁 → tb_tag_group_map 미사용) — 향후 교체 필요
- **설비↔태그(tb_equipment_tag_map)**: 3,375건 자동 매핑 완료 (PLC:2185 + 가압펌프:519 + LTE:603 + 유량계:68), 설비 장애 역추적 Phase 2 대기


---

## 업무 메모 + 일정 알림 v1 (2026-07-19)

**요청**: ① DB 영속(리셋 후 유지) ② 메모 = 제목+내용, 날짜/제목/내용/작성자 검색
③ 일정 알림 = 달력 등록(할일·내용·날짜·시각) → 해당 시각 팝업 ④ 채팅·영상·음성은 사양 검토만

**산출물**:
- `docs/memo-schedule-spec.md` — 사양 v1 (SCADA 알람과 용어·시스템 분리 명시)
- `docs/realtime-comm-spec.md` — 사용자 간 채팅/영상/음성 검토 v0 (P1~P4 단계, 결정 필요 4항)
- Migration `0105_memo_schedule.sql` — tb_memo·tb_user_schedule + 메뉴 M005-3/4 (보고서 그룹)
- Backend `endpoints/memo.py`·`user_schedule.py` — CRUD + due/ack. 작성자 본인만 수정·삭제
- Frontend `/reports/memo`(검색+CRUD)·`/reports/schedule`(월 달력)·`ScheduleAlarmPopup`(30s 폴링 전역 모달)

**검증**: 메모 생성→검색(작성자명 join), 일정 등록→정시 팝업 발화→확인(ack)→달력 취소선 반영,
백엔드 재시작 후 데이터 유지. E2E 스크린샷 통과.

**트러블**: [E-043] 신규 endpoint 커넥션 풀 미반환 → 전 API 연쇄 500. finally close 로 수정.


---

## 운영자 메신저 P1 (2026-07-19)

realtime-comm-spec v1 확정(파일 공유 우선/운영자 통화/1:1/순차) 후 P1 구현.
Migration 0106 + `endpoints/user_chat.py` + `/messenger`. 전체 채널('all') +
1:1 DM('dm:a|b' 정렬 규칙), unread 뱃지(tb_user_chat_read), REST 3s 증분 폴링.
방 멤버십 서버 검증(비참여 dm 403). WS 는 P3 wss(Caddy TLS 종단)에서 도입 예정.
다음: P2 사진·영상·음성메시지 파일 전송.


---

## 운영자 메신저 P2 — 파일 전송 + 도착 알림 (2026-07-19)

Migration 0107(attach 3컬럼) + /userchat/upload(유형별 확장자·크기 정책) +
files/messenger 저장. 프런트 📎 파일·🎤 음성 녹음, 미디어 렌더(이미지/video/
audio), /api/files Range 206 (영상 seek). MessengerNotifier 전역 토스트
(10s 폴링·메신저 화면 억제). 다음: P3 운영자 1:1 음성 통화 (wss + WebRTC).


---

## 운영자 메신저 P3 — 1:1 음성 통화 (2026-07-19)

Migration 0108 + endpoints/call_signal.py + VoiceCallManager/voice-call.ts/
call-store. 설계 핵심: wss 대신 REST 폴링 + non-trickle ICE (HTTPS 혼합
콘텐츠·프록시 제약 회피), 미디어는 WebRTC LAN P2P (폐쇄망 host candidate).
상태기계(ringing→accepted/rejected/canceled/missed→ended) curl 전 경로 검증,
중복 통화 409·제3자 403 확인. 실제 오디오는 LAN 2대 인수 테스트 항목.
