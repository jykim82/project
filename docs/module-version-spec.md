# 모듈 버전·라이선스 관리 사양 v1 (P1) — Migration 0138

폐쇄망 온프레미스 제품의 **모듈별 버전·동작 상태·라이선스**를 관리 화면
하나로 가시화한다. `/admin/system-modules` (M100-17 "시스템 버전").

**관련**: `docs/feature-sku-spec.md`(SKU flag 정본 = tb_comm_code),
`docs/operations/delivery-checklist.md`(납품 검수 — OSS 고지 의무),
`docs/operations/model-weights-bundle.md`(sha256 번들 전례)

---

## 1. 왜 — 그리고 P1 의 경계

구축 업체가 고객 폐쇄망에서 "지금 무슨 버전이 돌고 있고, 정상이며,
무엇이 계약(라이선스) 범위인가"에 답할 수단이 없다. P1 은 **읽기 전용
가시화**까지다:

- 버전 확인 · 모듈 정상동작 확인 · 라이선스(SKU/OSS) 표시 — 즉시 가치
- **업데이트 적용 버튼·롤백은 P2/P3** — 컨테이너가 자신을 교체할 수 없어
  호스트 업데이터 에이전트가 필요 (§5). 성급한 반자동은 폐쇄망에서
  복구 불능 리스크

## 2. 데이터 모델 (0138)

- `tb_module_version` — module_key PK, name, kind(container/bundle/feature/
  data), version, installed_at/by, notes. **P1 은 시드+수동 갱신** (배포
  스크립트 자동 스탬핑은 P2)
- `tb_module_license` — module_key, sku_code(NULL=기본 포함), oss_notices
  jsonb. **활성 여부는 저장하지 않는다** — SKU 상태는 tb_comm_code
  (SITE_SETTING) 가 정본이라 조회 시 실시간 조인 (정본 이원화 금지)

모듈 레지스트리 (시드 8): backend / frontend / db(마이그레이션) /
node-red / ai-weights / map-bundle / vision-agent / epanet(feature, SKU B1)

## 3. API — `GET /system/modules`

버전+라이선스 조인 + **live health** (병렬, 개당 2s 타임아웃, 실패=down):

| 검사 | 방법 |
|---|---|
| backend | 자기 자신 — 응답 자체가 증명 |
| db | SELECT 1 + 최신 마이그레이션 번호(tb_module_version.db) |
| ollama (ai-weights) | GET /api/tags — 모델 로드 목록 |
| node-red | GET :1880 |
| frontend | GET http://frontend:3000 (compose alias) |
| vision-agent | GET :8100/health |
| map-bundle | files/map/*.pmtiles 존재+크기 |

라이선스 상태 도출: sku_code 없음=`included` / SITE_SETTING use_yn='Y'=
`active` / 'N'=`locked`.

## 4. UI — `/admin/system-modules`

- 모듈 표: 이름·종류·버전·설치일·health 도트(ok/down/unknown)·라이선스
  배지([기본 포함]/[라이선스 활성]/[미보유 — 잠금])·OSS 칩
- **라이선스 고지(NOTICE) 섹션** — 전 모듈 oss_notices 집계. 납품 검수
  "고지 의무 이행"을 이 화면 하나로 답변
- 새로고침 = health 재검사

## 5. P2/P3 — 구현 완료 (2026-09-01, Migration 0139)

### P2 — 번들 반입·검증·스탬핑
- 번들 = tar.gz(manifest.json + 아티팩트 + migrations/). manifest 규격은
  `slm/endpoints/module_update.py` 모듈 docstring 정본
- `POST /system/update/upload` — 스테이징 해제 → **sha256 전수 검증 +
  required_sku 게이트**(미보유 모듈 포함 시 반입 차단) + 경로 안전
  (절대경로·`..` 거부). 이력 `tb_module_update`(0139)
- 버전 자동 스탬핑: 백엔드 기동 시 `/app/VERSION`, 프런트는
  `switch-frontend-prod.sh` 빌드 성공 시 (날짜-커밋SHA)
- UI: 시스템 버전 페이지 "업데이트 번들" 패널 — 업로드·상태·승인·롤백·삭제

### P3 — 호스트 에이전트 (`scripts/update_agent.py`)
- 백엔드는 잡 스풀(`files/updates/jobs/`)에 쓰고, **적용·롤백은 호스트
  에이전트**가 수행: 대상 백업 → 배치 → sha 재검증 → 마이그레이션 적용
  → health 검사 → **실패 시 자동 복원**. 결과 파일은 백엔드가 DB 병합
  (상태 전이 직전에도 병합 — 낡은 상태로 전이 거부되던 결함 수정)
- 롤백: 적용 시점 백업 복원. **DB 마이그레이션은 자동 롤백 안 함**
  (수동 롤백 블록 원칙 유지, UI confirm 에도 명시)
- kind=container(이미지 교체) 는 코드 경로만 존재 — **실 이미지 번들
  확보 시 검증 예정** (에이전트가 건너뛰며 미검증 경고)
- **실행 방침: 수동 실행 확정** (2026-09-01 사용자 결정) — 상주 등록
  (launchd/systemd) 하지 않는다. 업데이트 적용 시 구축 담당자가
  `python3 scripts/update_agent.py --once` 1회 실행. 승인만 된 번들은
  에이전트 실행 전까지 "적용 대기" 상태로 남는 것이 정상

### 첫 실전 배포 시 함께 (예약)
- kind=container(이미지 tar 교체) 실검증 — 실제 이미지 번들 제작 시점
- 번들 pack 스크립트(모듈·버전 지정 → manifest+sha256 자동 생성)

### 검증 (2026-09-01 — 시나리오 6종 × 각 5회 = 30/30)
정상 반입 / sha 불일치 차단 / SKU 잠금 반입 차단 / 승인→적용→버전 스탬프
/ 롤백→이전본 복원 / 변조 적용 실패→자동 복원
