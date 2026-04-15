# 검토 항목 관리

사용자가 결정·조치해야 할 항목을 주제별로 기록한다. Claude는 작업 진행 중 의사결정이 필요한 사안을 발견하면 이 파일에 주제별로 추가하고, 사용자가 "검토 항목" / "내가 볼 것" 등으로 질의하면 여기서 답한다.

- 항목 형식: `### N. 제목` + 현상 / 원인 / 선택지 / 결정 필요 네 블록
- 해결된 항목은 제거하지 말고 `✅ 해결` + 결정 내용을 추가로 기록
- 관련 커밋/이슈 SHA를 함께 남겨 추적성 유지

---

## [E-025] 멀티모달 현장 진단

### 1. 쿼리 구성 튜닝 (매뉴얼 RAG)
**현상:** 백엔드 단독 테스트(`/vision/manual-search`)는 `XGR-CPU p251 "ERR LED 점등 조치방법"`이 #1로 안정 매칭되지만, 프런트 E2E(`/vision/diagnose` 경유)에서는 `XGT Series Catalog p.107` (카탈로그 페이지)이 상위에 노출됨.

**원인 추정:** `/vision/diagnose`에서 observed_state 7줄(LED 라벨 `XGT`/`XGP`/`XGK`)이 쿼리에 합쳐지면서 catalog가 끌어올려짐. 트러블슈팅 섹션보다 표지·목차·스펙 페이지가 해당 키워드를 더 많이 포함.

**선택지:**
- (a) 인덱싱 시 `Catalog`/`FEATURES`/`Contents` 페이지 제외
- (b) 쿼리에 "트러블슈팅 원인 조치방법" 같은 액션 토큰 강제 추가
- (c) BM25 + 벡터 하이브리드 (현재는 dense only)

**결정 필요:** 어느 방향으로 갈지.

---

### 2. master-k 매뉴얼 1페이지만 추출됨
**현상:** `master-k_H카타로그2.pdf` → `tb_equipment_manual.page_count=1`. 실제 카탈로그는 더 많은 페이지일 가능성.

**원인 추정:** 스캔 이미지 PDF여서 `pypdf.extract_text()`가 빈 결과 → `MIN_PAGE_CHARS=100` 필터에 걸려 대부분 제외.

**선택지:**
- (a) OCR(tesseract/paddleocr) 파이프라인 추가
- (b) 해당 매뉴얼 제외 + 텍스트 기반 매뉴얼로 교체

**결정 필요:** OCR 도입 범위 (제품 카탈로그 전반에 적용할지).

---

### 3. XGR-CPU vs XGK 모델 혼동
**현상:** VLM이 "LS XGK-CPUE"로 식별 후 매뉴얼 검색 결과 1위가 `XGR-CPU Manual` (다른 모델). XGK 전용 매뉴얼이 인덱싱되어 있지 않음 (XGT 공통 매뉴얼만 존재).

**영향:** 모델 정확도는 떨어지지만 동일 제조사 공통 트러블슈팅 내용은 유효.

**결정 필요:** XGK 전용 매뉴얼 추가 수급 or 현재 상태 유지.

---

### 4. Catalog vs 사용설명서 분리
**현상:** `G100_Series_Catalog_KR_202506.pdf` (70p) + `G100_C__사용설명서_KR_V4_03_250829.pdf` (385p) 둘 다 인덱싱됨. brand/model 동일이라 검색 시 둘이 섞여 나옴.

**선택지:** `tb_equipment_manual`에 `manual_type` 컬럼 추가 (`catalog`/`user_manual`/`datasheet`) 후 기본 검색 시 user_manual 우선.

**결정 필요:** 분리 정책 도입 여부.

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
