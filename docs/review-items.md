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
