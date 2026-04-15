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

### 2. master-k 매뉴얼 1페이지만 추출됨
**현상:** `master-k_H카타로그2.pdf` → `tb_equipment_manual.page_count=1`. 실제 카탈로그는 더 많은 페이지일 가능성.

**원인 추정:** 스캔 이미지 PDF여서 `pypdf.extract_text()`가 빈 결과 → `MIN_PAGE_CHARS=100` 필터에 걸려 대부분 제외.

**선택지:**
- (a) OCR(tesseract/paddleocr) 파이프라인 추가
- (b) 해당 매뉴얼 제외 + 텍스트 기반 매뉴얼로 교체

**결정 필요:** OCR 도입 범위 (제품 카탈로그 전반에 적용할지).

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

### 5. 매뉴얼 업로드 UI 부재
**현상:** 추가 매뉴얼은 `docs/매뉴얼/`에 수동 복사 후 `slm/tools/index_manuals.py` CLI 재실행해야 함.

**선택지:** 관리자 페이지에 PDF 업로드 + 자동 인덱싱 트리거.

**결정 필요:** 관리자 UI 필요 여부 (운영 단계에서 매뉴얼이 자주 추가되는지).

---

### 6. is_registered 매칭 엄격도 ✅ 부분 해결 (slm@fdda15d, 2026-04-15)
**해결 내용:** `vision_agent._match_existing_equipment()` 구현으로 sitename + equipmenttype 필터 + `meta->>'model'` ILIKE 우선 + `meta->>'manufacturer'` ILIKE fallback + 글로벌 재시도. 5회 rotation 테스트 5/5 매칭 성공.

**남은 판단:** 현재는 `site 내 실패 → 글로벌 1건 반환` 순서인데, 글로벌 매칭은 오탐 위험이 있다. 글로벌 단계를 비활성화할지 여부 결정 필요.

---

### 7. Playwright 샘플 이미지 canonical 경로
**현상:** 테스트 이미지가 `.playwright-mcp/ls_xgk_error.jpg`, `slm/test_images/fake_ls_plc.jpg`, 컨테이너 `/tmp/ls_xgk_error.jpg` 세 군데에 분산.

**조치 필요:** 검증용 고정 경로 선정 + README 또는 docs/ 내 안내.

**결정 필요:** 어디를 canonical로 할지 (예: `slm/test_images/` 단일화).

---

## 히스토리

- 2026-04-15: E-025 P3 매뉴얼 RAG 실구현 직후 7개 항목 등록 (쿼리 튜닝 / master-k OCR / XGR-CPU vs XGK / catalog vs manual / 업로드 UI / is_registered 매칭 / 샘플 이미지 경로)
- 2026-04-15: #6 is_registered 매칭 P8에서 부분 해결 (`slm@fdda15d`)
- 2026-04-15: **#1 RAG 쿼리 튜닝 + #4 catalog vs manual 분리** P14에서 해결 (`slm@184764e`) — manual_type soft boost. 검증: manual-search 15/15 user_manual, diagnose 3/3 user_manual, 이전 XGT Catalog 끌어올림 현상 제거
- 2026-04-15: **#3 XGK 전용 매뉴얼** 사용자 추가로 해결 (XGK-CPU_Manual V3.0 239페이지/253청크 인덱싱, manual_id=18). 실제 XGK CPUE 사진 E2E: VLM=LS XGK-CPUE 정확 식별, manual_excerpts #1 XGK p48 (각부 명칭) + #2 XGK p52 (ERR LED 조치) + #3 XGL-EFMTB
