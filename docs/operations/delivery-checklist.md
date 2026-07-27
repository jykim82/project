# 납품 체크리스트 (폐쇄망 온프레미스)

납품 전 필수 작업의 단일 목록 — 흩어진 문서들의 진입점 (2026-07-17).
각 항목 완료 시 체크. 상세 절차는 링크 문서 참조.

## 1. 오프라인 자산 반입 (인터넷 없는 환경에서 기능 보장)
- [ ] **모델 웨이트 번들** — Chronos 783MB + Whisper 1.5GB
      `slm/tools/model_weights_bundle.sh pack → install → verify`
      (docs/operations/model-weights-bundle.md). 누락 시 STT 503·전망 선형 폴백
- [ ] **GIS 지도 번들** — 관할 pmtiles + 글리프/스프라이트가 프런트
      `public/map/` 에 포함됐는지 (docs/operations/offline-map-bundle.md).
      다른 관할이면 bbox 재추출. 누락 시 GIS 지도 미표시
- [ ] Ollama 모델 (gemma4 비전 + snowflake-arctic-embed2) 오프라인 pull 준비

## 2. 개발 전용 요소 제거
- [ ] **dev_tag_ingest 제거** — 테스트용 원격 태그 복제 데몬
      (docs/dev-tag-ingest-spec.md, 컨테이너 slm-dev-tag-ingest)
- [ ] 개발 계정/비밀번호 정리, `.env` 시크릿 재발급 (docs/deploy-secrets.md)
- [ ] `NEXT_PUBLIC_MAP_CDN` 미설정(기본 오프라인) 확인

## 3. 배포 구성
- [ ] 프로덕션 빌드 배포 — `Dockerfile.prod`/`docker-compose.prod.yml`/Caddy TLS
      (docs/deploy-production.md). dev(HMR) 모드 금지 [E-034]
- [ ] DB 마이그레이션 0043~최신 순차 적용 + 롤백 절차 확인
- [ ] cron 등록: 근본원인 분류·EPANET 시뮬(활성 시)·baseline 주1회·IForest 주1회
      (docs/operations/*-cron.md)
- [ ] **원시데이터 압축** (Migration 0128, docs/performance/tag-raw-scan-optimization.md)
  - 정책 job 등록 확인: `SELECT * FROM timescaledb_information.jobs WHERE
    proc_name='policy_compression'` — 새벽 3시 스케줄이어야 함
  - 기존 데이터를 이관해 왔다면 **최초 일괄 압축을 한산한 시간대에 수동 실행**
    (0128 파일 하단 절차 — 반드시 `SET lock_timeout='10s'` + 청크별 실행.
    compress_chunk 는 배타 락이라 운영 중 실행 시 적재·조회가 줄줄이 막힘
    [E-056 검증 중 Node-RED INSERT 9분 차단 실측])
  - 완료 확인: `SELECT count(*) FILTER (WHERE is_compressed), count(*) FROM
    timescaledb_information.chunks WHERE hypertable_name='tb_tag_raw_data'`
    — 14일 경과 청크 전부 압축이 정상 (최근 2개 청크는 비압축이 정상)

## 4. 검수
- [ ] **구축 완결성 검수** — `/setup/audit` (구축 > 구축 완결성 검수) 1회
      실행: **warn 0 이 인수 기준**, info 항목(SCADA 임계·EPANET)은 발주처
      결정 기록 (docs/setup-audit-spec.md)
- [ ] **채팅 스모크 3층** — Tier1 `python test_chat_smoke.py` 16/16 +
      Tier2 e2e + Tier3 `/admin/chat-gallery` 육안 (docs/chat-smoke-test-guide.md)
- [ ] 인터넷 차단 상태에서: GIS 지도·음성 입력·트렌드 전망(chronos_bolt)·
      사진 진단 동작 확인 — 외부 요청 0건 (브라우저 Network)
- [ ] 알람 팝업·현장 모드·보고서 인쇄 육안 확인

## 5. 인수인계 문서
- [ ] docs/operations/report-quickstart.md (운영자 1장)
- [ ] 에러 대응: docs/error-management.md E-NNN 색인
