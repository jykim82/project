# 검토 항목 관리

사용자가 결정·조치해야 할 항목을 주제별로 기록한다. Claude는 작업 진행 중 의사결정이 필요한 사안을 발견하면 이 파일에 주제별로 추가하고, 사용자가 "검토 항목" / "내가 볼 것" 등으로 질의하면 여기서 답한다.

- 항목 형식: `### N. 제목` + 현상 / 원인 / 선택지 / 결정 필요 네 블록
- 해결된 항목은 제거하지 말고 `✅ 해결` + 결정 내용을 추가로 기록
- 관련 커밋/이슈 SHA를 함께 남겨 추적성 유지

---

## [E-025] 멀티모달 현장 진단

### 1. 쿼리 구성 튜닝 (매뉴얼 RAG) ✅ 해결 (slm@184764e, 2026-04-15)
**조치:** `manual_type` 컬럼 추가 + `_ManualRagIndex`가 로드 시 함께 읽어와서 검색 시 **user_manual +0.08 / catalog -0.05** soft boost 적용. 인덱싱·NPZ 재생성 없이 DB UPDATE + 런타임 스코어 조정만으로 해결.

**검증 결과:**
- `/vision/manual-search` 직접 호출 3쿼리(`ERR LED 점등 조치방법` / `PLC CPU 고장 원인 진단` / `XGK 트러블 슈팅`): 전 15/15 top-5 결과가 user_manual (catalog 0건)
- `/vision/diagnose` 전체 경로 (ls_xgk_error.jpg): `manual_excerpts` 3/3 user_manual — 이전엔 #1=`XGT Catalog p.107`, 이제 #1=`XGL-EFMTB p.33 LED 표시부 규격`, #2=`XGR-CPU p.251 15.2.3 ERR LED 조치방법`, #3=`XGR-CPU p.51 WAR LED 용도`

**남은 판단:** boost 계수(+0.08/-0.05)가 적절한지 프로덕션에서 모니터링 필요. 너무 강하면 catalog에만 있는 실제 스펙 정보(dimension, 최대 사용 온도 등)를 묻힐 수 있음.

**✅ 마감 (2026-07-23):** dev 에는 실사용 질의 데이터가 없어 추가 판단 근거 부재 — 현행 계수 유지. 대표 쿼리 재검증(ERR LED 점등 조치방법 → XGR-CPU p251 user_manual 1위) 정상. 실운영 데이터 축적 후 오답 피드백 루프에서 catalog 미노출 불만이 잡히면 재조정.

---

### 2. master-k 매뉴얼 1페이지만 추출됨 ✅ 해결 (slm@decb86c, 2026-04-15)
**조치:** 사용자 결정에 따라 option (b) — master-k 제외. `tools/index_manuals.py`에 `SKIP_FILENAME_PATTERNS = ["master-k"]` 추가 + 기존 row(manual_id=15) + NPZ 삭제. `_ManualRagIndex` 2833 → **2830 chunks**. OCR 파이프라인은 도입하지 않음.

---

### 3. XGR-CPU vs XGK 모델 혼동 ✅ 해결 (2026-04-15, 사용자가 XGK 매뉴얼 추가)
**조치:** 사용자가 `docs/매뉴얼/XGK-CPU_Manual_V3.0_202508_KR.pdf` (6.2 MB, 239 페이지)를 추가. `index_manuals.py` 재실행으로 manual_id=18 등록 (253 청크), `_ManualRagIndex` 2833 chunks로 확장.

**검증:**
- `/vision/manual-search` "XGK-CPUE ERR LED 점등 조치방법": #1 **XGK-CPU_Manual p223** (score 0.648) — 이전 1위 XGR-CPU p251 (0.638)을 밀어냄
- 실제 XGK CPUE 사진(`docs/매뉴얼/plc 사진/xgk plc cpue.jpeg`) `/vision/diagnose` E2E: manual_excerpts 3건 중 2건이 XGK 전용 매뉴얼
  - #1 XGK-CPU_Manual p48 (CPU 모듈 각부 명칭 — XGK-CPUS/E/A/H/U)
  - #2 XGK-CPU_Manual p52 ("ERR LED 점등(적색): 운전이 불가한 에러가 발생한 경우를 표시" — 질의와 정확 매칭)
  - #3 XGL-EFMTB p360 (XG5000 에러 확인)
- VLM 식별: LS XGK-CPUE (brand/model 정확), has_issue=True, matched=plc_1

---

### 4. Catalog vs 사용설명서 분리 ✅ 해결 (slm@184764e, 2026-04-15)
**조치:** `tb_equipment_manual.manual_type` 컬럼 추가 + 기존 row 17건을 title pattern으로 분류 (catalog 4건 / user_manual 13건). 검색 시 user_manual +0.08 / catalog -0.05 boost. 동일 brand/model의 Catalog + 사용설명서가 공존해도 user_manual이 우선 노출.

---

### 5. 매뉴얼 업로드 UI 부재 ✅ 해결 (slm@decb86c + slm-dashboard@a81ae5e, 2026-04-15)
**조치:**
- `tools/index_manuals.py` 리팩토링: `index_single_pdf(src_path, filename, conn, meta_override)` 헬퍼 추출 (main() 루프 로직 재사용)
- `endpoints/admin.py` 신규 엔드포인트 3종:
  - `GET /admin/equipment-manuals` — 목록 조회 (manual_type 포함)
  - `POST /admin/equipment-manuals/upload` — PDF + 메타 multipart → index_single_pdf 호출 → UPSERT → hint 반환
  - `DELETE /admin/equipment-manuals/{id}` — DB row + NPZ 파일 삭제
- `slm-dashboard/src/app/(dashboard)/admin/equipment-manuals/page.tsx` 신규 — 테이블 + 업로드 Dialog + 삭제 action
- 재시작 안내: vision_agent의 `_ManualRagIndex`가 lazy-load 후 메모리 유지하므로 업로드·삭제 반영 위해 수동 재시작 필요 (hot-reload API는 미구현)

**남은 판단:** vision_agent hot-reload API 도입 여부 (업로드 시 즉시 검색에 반영되게). 현재는 수동 `kill + python3 vision_agent.py`. 우선순위 낮음.

**✅ 구현 (2026-07-23):** `POST /vision/manuals/reload` 신설 + admin 업로드/삭제가 인덱싱 후 best-effort 호출(성공 시 hint "검색 반영 완료"). 재시작 불필요. 주의: load() 가 append 방식이라 reload 시 _rows/_embeddings 완전 초기화 필수(중복 적재 2830→5660 버그 수정). 검증: 3회 연속 reload 2830 고정 + 검색 정상.

---

### 6. is_registered 매칭 엄격도 ✅ 완전 해결 (slm@fdda15d → slm@decb86c, 2026-04-15)
**P8 (fdda15d):** `vision_agent._match_existing_equipment()` 초기 구현 — sitename + equipmenttype 필터 + model ILIKE + brand fallback + **글로벌 재시도**. 5회 rotation 5/5 성공.

**P8 후속 (decb86c):** **글로벌 매칭 비활성화** — sitename 없거나 site 내 실패 시 바로 None 반환. 내부 `_search_model`/`_search_brand`로 리팩토링(site-scoped only). 오탐 위험 제거, is_registered=False 노출로 사용자 명시 등록 유도.

**검증 (decb86c):**
- `sitename=None` → None ✅
- `sitename='가상없음'` → None (존재하지 않는 사이트 오탐 없음) ✅
- 5 site rotation (행정/석문/신평/송악1/갈산) → plc_1/2/3/4/79 정확 매칭 ✅

---

### 7. Playwright 샘플 이미지 canonical 경로 ✅ 해결 (web@... 2026-04-15)
**조치:** `docs/매뉴얼/plc 사진/`을 canonical 경로로 지정. 실제 XGK CPUE 현장 사진이 여기 있음. `docs/test-image-samples.md` 신규 작성 — canonical 경로 + 비-canonical 레거시 경로 구분 + 새 샘플 추가 가이드 포함. 레거시 `slm/test_images/fake_ls_plc.jpg`, `.playwright-mcp/*.jpg`는 사용 금지로 명시.

---

## [Chat] 멀티턴 / Follow-up 지원 강화

### 1. Follow-up 인텐트 상속 경로 확장 (계획)
**현상:**
- `session_manager.is_correction_turn()` (`session_manager.py:104-117`)이 10자 미만 질문을 직전 인텐트로 상속시킴 — **단, correction 상태(last_status == NEED_CORRECTION)일 때만**
- 정상 응답 직후 "오늘 것도" / "수압은?" 같은 짧은 follow-up은 **정상 분류 경로**로 흘러감
- Stage1/2 프롬프트(`intent_classifier.py:46-60`)에 follow-up few-shot 0개 → 범위외(out-of-scope) 오분류 가능성 높음

**원인:**
- 파라미터 누적 방식은 UX 보강용이라 **같은 인텐트 계열 내 slot-filling**에만 최적화됨
- 멀티턴 본격 지원이 아니라, 사용자가 전체 문장을 재입력하는 것을 전제로 설계됨
- 실제 최근 로그(`dev-logs/ask_debug.txt`)에 짧은 follow-up 사례 거의 없음 → 사용자가 "이미 학습하여" 전체 문장으로 재입력 중인 것으로 추정

**선택지:**
- (a) `is_correction_turn` 조건 확장: "**최근 성공 턴 존재 + 질문 10자 미만**"까지 포괄. correction 상태 아니어도 인텐트 상속
- (b) Stage1 프롬프트에 follow-up few-shot 2~3개 추가 (예: "오늘 것도" → tag_trend / "왜?" → 범위외 유지)
- (c) 프런트에서 짧은 follow-up 감지 시 직전 질문과 **concat**하여 전송 (예: "오늘 것도" → "석문정수장 오늘 유량")
- (d) 본격 멀티턴화: 이전 턴 `(question, answer)` 쌍을 프롬프트에 주입 (품질·지연 비용 큼)

**결정:**
- ✅ **(a) 2026-04-19 구현 완료** (`slm@29420a9`) — `is_short_followup` 신규.
  last_status='OK' + last_intent 존재 + 10자 미만 시 직전 인텐트 상속.
  ai_server /ask/stream non-SSE + SSE 양쪽 분기. E2E: 1턴 FACILITY_TREND →
  2턴 "오늘 것도" (6자) → **followup_inherit 14ms** (기존 SLM 27s 대비 약
  2000배 가속, 오분류 "범위외" 제거)
- ✅ **(a-ext) 2026-04-20 임계값/표지 확장** (`slm@7705d59`) — 10자 단일
  규칙 → 복합 조건:
  - <10자 (기존 유지)
  - **이어말 표지 포함 + <20자** (그럼/그러면/같은/다른/어제/오늘/내일/
    이번/지난/최근)
  - "도" 조사는 "상수도"/"수도" 도메인 용어 오탐 위험으로 **제외**
  - E2E 20 케이스: "그럼 최근 한 달"/"같은 기간 압력"/"다른 시설도"/"내일
    것도" 등 자연어 follow-up 포괄. 반례 "난지 가압장 유량 보여줘"(완성문)
    은 상속 안 함으로 검증 → **20/20 통과**
- ✅ **(a-fix) 2026-07-10 지표 명사 상속 오답 수정** — `<10자` 규칙이
  "배수지 총유량은?"(9자)처럼 **자기 지표를 명시한 신규 질문**까지 무조건
  상속시켜, 직전 인텐트(TODAY_OUTFLOW_ALL_STATUS)의 답을 재사용하는 오답
  발생. `is_short_followup` 에 `_standalone_metric_nouns` 가드 추가 —
  질문이 유량/유출량/총유량/수위/압력/공급량 등 지표를 스스로 명시하면
  (a)(b) 밴드 모두 상속 거부 → 재분류. 지표 없는 조각("오늘 것도","가압장은?")
  만 상속. 단위 8/8 통과("배수지 총유량은?"·"현재 수위는?"·"그럼 어제
  유입량은?"=재분류 / "오늘 것도"·"가압장은?"=상속)
- (b) 운영 평가 후 필요시 진행 (F2 태스크 등록). (c)/(d) ROI 낮음으로 보류
- 관련 memory: `p28_upstream_fault_explain.md`, `a1_feedback_progress.md`

---

## [Perf] 야간최소유량 사전집계 테이블 갱신 스케줄 부재 ✅ 해결 (선택지 b, 2026-07-10)

**현상:** `tb_night_min_flow_daily`(야간최소유량 트렌드/표준편차 fast-path의
원천)가 2026-03-21 이후 갱신되지 않아 정체. 채팅 야간최소유량 트렌드가
사전집계 테이블을 쓰는데(→ E-035), 최신 데이터가 누락될 수 있었음.

**원인:** 테이블 갱신은 DB 함수 `compute_night_min_flow(target_date)` /
`_job_compute_night_min_flow()` 로 설계됐으나, 이를 호출하는 pg_cron 이
현재 DB(TimescaleDB 컨테이너)에 미설치(`cron.job` relation 없음). 백엔드
백그라운드 루프도 이 **테이블**은 갱신하지 않음(별도 인메모리 캐시만 갱신).

**조치(임시):** `backfill_night_min_flow('2026-03-22', CURRENT_DATE-1)` 로
현재까지 채움(2026-07-10 기준 13,502행, max_date=2026-07-09).

**선택지:**
- (a) 호스트 crontab/launchd 로 매일 `psql -c "SELECT compute_night_min_flow();"`
- (b) 백엔드 background 루프에 일 1회 `compute_night_min_flow()` 호출 추가
  (폐쇄망·컨테이너 자족적, 별도 호스트 설정 불필요)
- (c) DB 에 pg_cron extension 설치 후 `_job_compute_night_min_flow` 스케줄

**✅ 해결:** (b) 채택 — `ai_server._night_min_flow_agg_loop` (시작 200초 후 첫
실행 + 24h 주기, `_refresh_night_min_flow_daily` self-healing 백필). 폐쇄망·
컨테이너 자족적. 2026-07-15 실증: max(log_date)=2026-07-14(최신), 루프 로그
"이미 최신 (갱신 불필요)" 정상.

**✅ 해결 (b) 2026-07-10** — `ai_server._night_min_flow_agg_loop` 신규.
서버 시작 200초 후 첫 실행, 이후 24h 주기. `max(log_date)+1 ~ 어제` 구간만
`backfill_night_min_flow` 로 채워 자기치유(다운 기간 gap 자동 보정). backfill
은 upsert 라 재실행 안전. 검증: gap 로직(최신 시 0일) + self-heal 왕복
(1일 삭제→backfill→86행 복원). E-035 재발방지 항목.

---

## 히스토리

- 2026-04-15: E-025 P3 매뉴얼 RAG 실구현 직후 7개 항목 등록 (쿼리 튜닝 / master-k OCR / XGR-CPU vs XGK / catalog vs manual / 업로드 UI / is_registered 매칭 / 샘플 이미지 경로)
- 2026-04-15: #6 is_registered 매칭 P8에서 부분 해결 (`slm@fdda15d`)
- 2026-04-15: **#1 RAG 쿼리 튜닝 + #4 catalog vs manual 분리** P14에서 해결 (`slm@184764e`) — manual_type soft boost. 검증: manual-search 15/15 user_manual, diagnose 3/3 user_manual, 이전 XGT Catalog 끌어올림 현상 제거
- 2026-04-15: **#3 XGK 전용 매뉴얼** 사용자 추가로 해결 (XGK-CPU_Manual V3.0 239페이지/253청크 인덱싱, manual_id=18). 실제 XGK CPUE 사진 E2E: VLM=LS XGK-CPUE 정확 식별, manual_excerpts #1 XGK p48 (각부 명칭) + #2 XGK p52 (ERR LED 조치) + #3 XGL-EFMTB
- 2026-04-15: **#2 master-k 제외** (`slm@decb86c`) — SKIP_FILENAME_PATTERNS + DB/NPZ 제거, 2830 chunks
- 2026-04-15: **#6 is_registered 매칭 완전 해결** (`slm@decb86c`) — 글로벌 fallback 제거, site-scoped only
- 2026-04-15: **#7 canonical 경로** — docs/매뉴얼/plc 사진/ 지정 + docs/test-image-samples.md 신규
- 2026-04-15: **#5 매뉴얼 업로드 UI** (`slm@decb86c` + `slm-dashboard@a81ae5e`) — `index_single_pdf` 헬퍼 + `/admin/equipment-manuals` CRUD 엔드포인트 + `/admin/equipment-manuals/page.tsx` 관리자 페이지 + 업로드 Dialog
- **✅ review-items E-025 7건 전부 해결** (1,2,3,4,5,6,7)
- 2026-04-19: **[Chat] 멀티턴/Follow-up 지원** 1건 신규 등록 — 파라미터 누적은 same-intent slot-filling만 지원, 짧은 follow-up은 correction 경로 외에서 오분류 가능성 조사 완료. 개선안 (a)~(d) 중 선택 대기
- 2026-04-19: **(a) is_correction_turn 조건 확장** 완료 (`slm@29420a9`) — `is_short_followup` 신규 메서드. 성공 턴 + 10자 미만 질문 → 직전 인텐트 상속. E2E 14ms 상속 확인. (b) few-shot 은 운영 평가 후 조건부 진행
- 2026-04-20: **(a-ext) 임계값/표지 확장** (`slm@7705d59`) — 10자 단일 규칙 → <10자 OR (이어말 표지 "그럼/같은/다른/어제/오늘/내일/이번/지난/최근" + <20자). "도" 조사는 "상수도"/"수도" 오탐 위험으로 제외. E2E 20/20 통과
- 2026-07-10: **(a-fix) 지표 명사 상속 오답 수정** — "오늘 전체 배수지 유출량은?" 직후 "배수지 총유량은?"(9자)이 직전 인텐트를 상속해 유출량 답 재사용되던 버그. `is_short_followup` 에 지표 명사 가드 추가(유량/유출량/총유량/수위/압력/공급량 등 명시 시 상속 거부·재분류). 단위 8/8 통과

---

## [대기] 데이터 축적 후 착수 항목 — 트리거 조건 (2026-07-16 실측 기준)

세 항목 모두 **실운영자 투입(PoC/납품) 이벤트가 시계의 시작** — 개발 환경
축적 속도(피드백 ~1.3건/월, 레이블 0건/월)로는 도달 불가.

| 항목 | 트리거 | 현재치 (07-16) | 점검 |
|---|---|---|---|
| IForest P2 (레이블 정밀도) | 현장 확인 판정 알람 50건+ | 0건 — **P1.5(알람 weak-label proxy)는 2026-07-16 완료** (recall 46.4%·lift 1.48) | `SELECT count(*) FROM tb_equipment_alarm_report WHERE user_cause_description <> '' OR action_plan <> ''` |
| Gemma few-shot (A-4) | 오분류 피드백 100건+ (혼동 쌍 식별) | 4건 | `SELECT count(*) FROM tb_ai_chat_feedback` 또는 /admin/chat-feedback |
| 벡터 임계값 최적화 | 오분류 피드백 300건+ | 4건 | 상동 |

점검 주기: 실사용 개시 후 월 1회 (개시 전엔 분기 1회). 예상 도달:
실운영 기준 IForest P2 ≈ 1~2개월 차, few-shot ≈ 2~3개월 차, 벡터 임계값 ≈ 3~6개월 차.

## [보류] VLM 게이지 판독 본격 기능화 (2026-07-16)

PoC 로 능력 확인 완료 — gemma4 비전 합성 게이지 5/5 (아날로그 최대 오차
2.7%, 디지털 완벽. 단 JSON 반복 생성 루프 이미지 1건 → num_predict 캡 필수).
채택은 **라이트 통합만**: 진단 observed_state 계기 지시값 관찰 한 줄.

- 보류 사유 (사용자): "점검 보고는 상태 판정이지 숫자 기록이 아니다" —
  기록/대조 시나리오의 니즈 없음. 남는 용도(센서 의심 검증)는 빈도 낮아
  계기 메타 등록 비용 대비 수지 안 맞음
- 재개 트리거: 고객사 계기 검침(수치 기록) 요구 발생 시
- 재개 시 설계 메모: 단위는 계기 메타 정본(이미지 추정 금지), 시점 비교가
  아닌 수집주기 창 대 허용범위 비교, 판정 3값(일치/보류/불일치 의심)

## ~~[납품 필수] GIS 베이스맵 외부 CDN 의존~~ → **해소 (2026-07-17)**

오프라인 pmtiles 번들로 전환 완료 — docs/operations/offline-map-bundle.md.
(아래는 원 기록)

### 원 기록: GIS 베이스맵 외부 CDN 의존 (2026-07-17 발견)

`BASEMAP_STYLES` 가 cartocdn.com 스타일/타일을 사용 — **폐쇄망에서 지도가
아예 안 뜸** (마커 유실 조사 중 확인). 납품 전 오프라인 번들 필요:
- 스타일 JSON + 글리프/스프라이트 + 관할 구역 타일(pmtiles or MBTiles) 로컬 서빙
- 후보: OpenMapTiles 셀프호스팅 or pmtiles 정적 파일 (Caddy 서빙)
- 모델 웨이트 번들(model_weights_bundle.sh)과 동일하게 납품 체크리스트 등재

## ~~[정리 후보] FlowDiagram.tsx — 미사용 레거시~~ → **✅ 삭제 (2026-07-18)**

`src/components/setup/FlowDiagram.tsx` 는 어디서도 import 되지 않는 구
프리뷰 컴포넌트였음 (실사용은 FlowDiagramGraph.tsx). 정리 라운드에서
import 0건 재확인 후 삭제 — tsc 0건 유지.

## [해결 2026-07-20] 트렌드·타임라인 시각 축이 UTC 나이브로 표시 (2026-07-19 발견)

**→ E-044 로 해결.** 조사 결과 표기 문제가 아니라 **조회 창이 9시간 과거**인
중대 결함이었음 (DB 세션 KST + UTC 나이브 절단 오해석). parse_ts_kst 로
창·라벨 KST 정합, GIS 타임라인 보정 제거, 채팅 plot 프런트 정규화,
forecast 라벨 포맷 통일. 상세: docs/error-management.md E-044.

--- (이하 원 기록) ---

**현상:** `/trend/data` 의 times 가 UTC 나이브 문자열("2026-07-19 08:20" =
KST 17:20)로 반환되고, 트렌드 차트 x축·GIS 타임라인 라벨이 이를 그대로
표시 — 운영자는 KST 를 기대하므로 9시간 어긋나 보임. 반면 알람 이력
(occurredAt)은 KST 나이브 → 화면 간 좌표계 혼재 (타임라인은 변환으로 흡수).
**원인:** 백엔드 trend/data 가 UTC 기준 버킷 문자열 생성 (naive).
**선택지:** (a) 백엔드에서 KST 나이브로 통일 반환 (전 화면 일괄 교정,
기존 차트 축 표기가 모두 KST 로 바뀜 — 검증 필요) (b) 프런트 표시층에서
+9h 변환 (화면별 산발 수정 — 이원화 위험).
**권고:** (a). 착수 시 트렌드/모니터링/타임라인/스파크라인 축 일괄 확인.
**결정 필요:** 적용 시점 (표기 문제일 뿐 데이터 정합은 유지되므로 급하지 않음)

## [데이터 정합] 알람 중지 태그 프리필 검증 중 발견 ✅ 해결 (2026-07-24)

**→ 처리 완료:** ① 합덕일반 datainfo 오타 2건 정정 (3지→1지·4지→2지 —
datadesc·알람 메시지 기준, tb_datainfo_apply_log 이력). ② 수청2지구2 —
근본 원인은 tb_block_info 미등록: 수청 계열 3곳 + 도비도·소난지도·소소블록
2곳 = 7곳 등록 + 태그 sitename 오타(신펑1→신평1) 정정, facility_map 반영
실확인. 같은 유형의 **가압장 기초정보 미등록 11곳**은 구축 완결성 검수
(/setup/audit) warn 으로 상시 노출 — 등록은 별도 진행. tagsn 기반 억제(P2)
는 미착수 유지.

--- (이하 원 기록) ---

## [데이터 정합] 알람 중지 태그 프리필 검증 중 발견 (2026-07-22)

1. **합덕일반(배) 수위 태그 datainfo ↔ 알람 메시지 불일치** — 알람 메시지는
   "공업 1지 수위 LL"인데 해당 tagsn 의 datainfo 는 "공업 3지 수위 LL".
   태그 칩은 tagsn 기준 정확한 태그로 추가되지만, 백엔드 억제 매칭이
   메시지 부분일치 방식이라 이 태그 단독 억제는 미매칭 가능. →
   ① tb_tag_info datainfo 현행화 확인 ② tagsn 기반 억제(P2) 검토
2. **수청2지구2(소블록) autocomplete facility_map 누락** — 알람 제어 창
   현장명 셀렉트가 빈 표시 (등록 동작은 정상). facility_map 소스 쿼리가
   해당 현장을 왜 제외하는지 확인 필요

## [데이터 품질] 장애 DI 태그 상시 ON — power_fault 진단 신뢰성 ✅ 해결 (a)+(c) 구현, 2026-07-23

**→ 구현 완료 (같은 날):** anomaly_scan `_detect_equipment_failures` B-2.5 —
최근 7일 on 비율 ≥95% DI 는 설비 장애 판정 제외 + "DI상시ON의심" 으로
데이터 품질군 강등 (검증: 32건 제외, 잔여 실장애 36건). (b) 반전 플래그는
미도입 — 게이트로 충분하면 불필요.

--- (이하 원 기록) ---

**현상:** 죽동 이상 진단 AI 요약이 "수위2 태그에서 power_fault 감지 (설비
장애·확정 사고)"로 표기 → 실데이터 확인 결과 "죽동(배) 정전 발생"
DI(44270_24110_XXA_N002)가 **최근 30일간 val=0 이 단 한 번도 없이 상시 1**.
전수 조사 결과 동일 패턴이 광범위: 7일 기준 on 100% 태그 10개+ (대호 정전,
매방리 한전 정전, 기지시 정전감시, 삼화 UPS1차전원이상, 석우/죽동2 UPS
통신이상 등). SCADA 알람 이력(tb_equipment_alarm_report)에는 해당 정전
알람이 0건 — 원계통도 알람으로 취급하지 않음.
**원인(추정):** 원격지 DI 접점 반전(정상=1) 또는 미결선/고착. 한 달째
"정전"인데 RTU 가 계속 송신 중인 것은 물리적으로 모순.
**영향:** anomaly_scan 의 설비 장애 감지(B: 최근 10분 val=1)가 이런 태그를
매 스캔마다 "확정 사고"로 분류 — 여러 사이트 AI 요약이 상시 오경보.
**선택지:** (a) 상시 ON 게이트 — 최근 7일 on비율 >95% 인 DI 는 장애 판정
제외(+"신호 고착 의심" 별도 표기) (b) 태그별 반전 플래그 도입(설정 필요)
(c) AI 요약에 지속시간 표기만 추가("30일째 지속" — 운영자 판단 위임).
**권고:** (a)+(c) 병행 — 코드 게이트로 오경보 차단하고, 제외된 태그는
품질 이슈로 노출해 현장 확인 유도.
**결정 필요:** 적용 여부·방식

## [모델 운영] GBT baseline 개선율 음수 전환 (2026-07-23)

**현상:** baseline cron 미등록 5주 방치 발견 → 즉시 재학습+launchd 등록.
그러나 신규 회차(20260723_074609, 831태그) 홀드아웃 개선율 **-3.6%**
(hourly_mean 대비 열세). 신평 유입유량순시는 GBT MAE 83.7 vs hm 50.8.
학습창 60일 중 실데이터 43.8일, lag168 가용 84%.
**선택지:** (a) 태그별 게이트 — 홀드아웃에서 hm 보다 나쁜 태그는 추론 시
hourly_mean 강제 (tb_baseline_tag_metric 에 hm 비교치 저장 필요)
(b) 전역 게이트 — 회차 improvement_pct < 0 이면 전체 hm 폴백
(c) 현행 유지 — 데이터 축적(60일 채워지면) 후 재평가.
**결정 필요:** 게이트 도입 여부. 모델 평가 화면에 "학습 지연 경고" 배지
추가도 함께 검토 (이번 5주 공백을 화면에서 못 봄)

## [데이터 품질] 성상1 유출압력 센서 상한 포화 미검지 ✅ 해결 (a) 구현, 2026-07-23

**→ 구현 완료 (같은 날):** anomaly_scan `_detect_sensor_saturation` —
① 센서포화의심: 최근 6h 버킷 80%+ 가 min·max 모두 90일 관측 상한(±0.1%)
② 신호고착의심: 최근 6h 90%+ flat 인데 직전 7일은 변동. 설정/SET 태그 제외.
검증: 성상1 7/21 20시 시점 시뮬 pinned 100% 발화 확인, 현재 전수 23건
(포화 10·고착 13, 개도/밸브 오탐 0). 스캔 데이터 품질군으로 노출 —
이상 진단 답변·AI 요약·대시보드 자동 반영. (b) 임계 미보유 사이트 보강,
(c) 기준선 학습 제외는 잔존 (별도 결정).

--- (이하 원 기록) ---

**현상:** 성상1(가) 유출압력이 7/20 낮부터 요동 → **7/21~7/22 이틀간 계측
상한 10 kgf/㎠ 에 고정**(7/21 1,225/1,248 샘플 = 10) → 7/23 아침 8.2 정상
복귀. 사용자가 트렌드 화면에서 육안 발견.
**전체 이력 (추가 확인):** 일회성 아님 — 2~5월은 max 8.4~8.9 로 청정,
**6/1 주부터 포화 간헐 재발 7주째** (주간 val≥9.5 샘플: 6/8주 6,424 ·
6/15주 3,118 · 6/29주 3,858 · 7/13주 302 · 7/20주 2,745). 재발 파동 패턴
→ 계측기 열화 또는 현장측 간헐 이상 — **현장 점검 대상**. 이 모든 에피소드
동안 시스템 경보 0건 (탐지가 채팅 요청 시점에만 동작 + 임계 태그 부재).
**교차 검증:** 가압펌프 설정압력은 기간 내내 7.8 불변, 인버터 1/2 교대
운전 패턴도 불변(직기동 0) — 실제 계통이 설정보다 +28% 과압을 이틀 유지
했다고 보기 어려움 → **압력 트랜스미터 풀스케일 고착 의심** (실과압 배제는
현장 확인 필요).
**시스템이 못 잡은 이유 3종:**
1. 성상1 에는 압력 H/HH 임계(SET) 태그 자체가 없음 → SCADA 알람 0건
2. 평소 대비 기준선이 포화 이틀치에 오염 — 정상 복귀한 현재값(8.2)을
   "평소보다 14.4%↓ 주의"로 **역판정** (이상 구간엔 침묵, 정상 복귀에 경고)
3. 카드의 AI 현황 요약이 다른 태그(유입압력) 템플릿 폴백 — 포화 태그 미언급
**선택지:** (a) **센서 포화 감지 규칙** — 태그별 관측 상한(최근 90일 max
분위)과 동일 값이 N시간 연속이면 "센서 포화 의심" 신호 (이상 카테고리
데이터 품질군, 죽동 DI 상시 ON 게이트와 동족) (b) 압력 임계 미보유 사이트
목록화 → 설정 보강 (c) 기준선 오염 대응 — 포화/고착 구간을 학습에서 제외.
**결정 필요:** 적용 범위·우선순위

**(b) 임계 보강 조사 완료 (2026-07-24):** 전수 조사 결과
`docs/alarm-threshold-coverage.md` — 압력 임계 공백 33곳(가압장 26곳 전원
+ 배수지 3곳 + 소블록 4곳), 수위는 사실상 완비, 유량은 통계 감시 소관 판정.
SCADA 측 보강 우선순위(가압장 유출/토출 H·HH → 흡입 L·LL) 문서화 —
**실제 임계 태그 신설은 SCADA/PLC 작업이라 발주처·현장 협의 필요** (사용자
결정 항목). (c) 기준선 학습 제외는 품질 계층 P2 로 구현 완료.

## [Perf] SCAN_ALL 캐시 빌드 1,217초 ✅ 해결 (선택지 a, 2026-07-24)

**→ 구현 완료:** Migration 0116 `cagg_1h_raw_stats_ai`(계층형 1h 사전집계,
합·제곱합 저장으로 5분 샘플 분산 보존) + SCAN_ALL SQL 재작성(365일 통계만
1h 집계, 최근 3h/1h/3d 창은 5분 유지). **실측 1,245초 → 40초 (31×)**.
검증: 289태그 전수 비교 — 행 일치, 등급 경계(|z|≥2) 교차 0건, 평균 5%+
차이 1건, 3σ 교차 1건(송악2 — 양쪽 모두 동일하게 심한 저하 감지, 무영향).
z 미세 차이는 min_valid 필터 granularity(5분→1h) 기인 — 정상 영역 내.

--- (이하 원 기록) ---

## [Perf] SCAN_ALL 캐시 빌드 1,217초 — TTL 5분 대비 과대 (2026-07-23)

**현상:** ANOMALY_SCAN_ALL 캐시 갱신에 20분 소요 (본체 365일 raw_adaptive
SQL 13분+, 5분 TTL 무의미 — 실효 갱신 주기 ~20분). 신규 품질 감지 추가와
무관 (본체 SQL 단계에서 대부분 소요).
**확인 필요:** E-051 tag_scale CTE 추가 전후 소요 비교, raw_adaptive 윈도·
인덱스 점검. 이상 진단 응답 자체는 캐시라 사용자 체감 없음 — 갱신 지연만.

**분석 완료 (2026-07-23):**
- 원인: `raw_adaptive`(365일 5분 버킷 = **84.8M 행 ≈ 5GB**, 2,700태그)가
  CTE 로 4회+ 참조 → PostgreSQL 이 통째 물질화(단일 프로세스·temp spill)
  후 반복 스캔. 데이터가 2/2~현재 171일뿐인데도 이 규모 — 1년 차면 2배
  (40분+) 로 악화 예정. E-051 tag_scale 은 참조 1회 추가로 부수 요인
- **선택지:** (a) 1시간 사전집계 cagg 신설 후 파생(85M→~7M, 12× 감소 —
  근본책, migration+SQL 재작성+검증 필요) (b) `NOT MATERIALIZED` 인라인화
  +tag_scale 을 cagg 직접 조회로 변경(중간 규모, 실측 필요) (c) 창 축소
  365→120일(의미 변화 검토 필요, 감소폭 제한적)
- **시너지:** hourly_holding·tag_scale 은 품질 계층(P2)과 개념 중복 —
  품질 테이블 참조로 대체하면 스캔 SQL 자체가 단순해짐. (a)+품질 계층
  P2 를 묶어 진행 권고
- **결정 필요:** (a) 착수 여부 (SQL 전면 재작성이라 E-050 교훈대로
  단계 검증 필수)

## 구축 워크스페이스 캔버스 정렬 (2026-09-01 등록 — 착수 대기)

- 도형 **드래그 박스 선택** (다중 선택)
- 캔버스 팬(이동)은 **우클릭 드래그**로 (좌클릭은 선택 전용)
- 정렬 메뉴: 좌측 정렬 · 가운데 정렬 · 우측 정렬 · 가로 등간격 · 세로 등간격
- 대상: setup/workspace 캔버스 (CanvasEditor + NetworkLinkEditor 양 레이어)
