# [DEV-ONLY] tb_tag_raw_data 원격→로컬 복제 데몬 사양

> ⚠ **납품 시 제거 대상.** 이 문서가 다루는 구성 요소는 개발/테스트 전용이며, 실제 운영 환경에서는 실 PLC/Node-RED 수집 파이프라인이 대체합니다. 납품 시 체크리스트는 맨 아래 섹션 참조.

## 배경

로컬 테스트 환경(`docker-compose.dev.yml`)에는 시계열 원본 `tb_tag_raw_data`를 채우는 수집 파이프라인이 없습니다. `slm-node-red` 컨테이너는 알람 조건 검토/네트워크 상태 동기화 등 **판정 로직**만 수행하고, 태그 원본 데이터를 INSERT하지 않습니다. 프로젝트 내 다른 어떤 컴포넌트도 `INSERT INTO tb_tag_raw_data`를 실행하지 않기 때문에, DB dump 로드 시점 이후 데이터가 정체되어 배수지/가압장/블록 모니터링 페이지가 비게 됩니다.

원격 참고 사이트(`112.166.183.65:25479`)에는 실측 데이터가 지속 유입되고 있으므로, 이를 로컬로 복제하여 테스트 환경에서도 실제에 가까운 검증을 가능하게 합니다.

## 아키텍처

```
┌──────────────────────────────────────┐        ┌─────────────────────────────┐
│ 원격 운영 DB                         │        │ 로컬 docker-compose.dev.yml │
│ 112.166.183.65:25479                 │        │                             │
│ postgres/postgres/DJpost0827///      │        │  ┌────────────────────────┐ │
│ tb_tag_raw_data (timestamp w/o tz)   │───────▶│  │ slm-dev-tag-ingest     │ │
└──────────────────────────────────────┘ pull   │  │ python daemon          │ │
                                                │  │ dev_tools/tag_ingest.py│ │
                                                │  └────────┬───────────────┘ │
                                                │           │ INSERT          │
                                                │           ▼                 │
                                                │  ┌────────────────────────┐ │
                                                │  │ slm-timescaledb        │ │
                                                │  │ tb_tag_raw_data (tztz) │ │
                                                │  └────────────────────────┘ │
                                                └─────────────────────────────┘
```

## 구성 파일

| 경로 | 역할 |
|---|---|
| `/Users/jykim/slm/dev_tools/tag_ingest.py` | 복제 데몬 Python 스크립트 |
| `/Users/jykim/slm/dev_tools/Dockerfile.tag_ingest` | 데몬 이미지 (python:3.12-slim + psycopg2-binary) |
| `/Users/jykim/web/docker-compose.dev.yml` | `dev-tag-ingest` 서비스 블록 포함 |
| `/Users/jykim/web/docs/dev-tag-ingest-spec.md` | (본 문서) |

## 동작 사양

### 부팅 시 (initial watermark 결정)

1. 로컬 `SELECT MAX(logtime) FROM tb_tag_raw_data`
2. 값 존재 → `watermark = max(logtime) - 5분` (5분 겹침으로 유실 방지, 중복은 ON CONFLICT로 무시)
3. 값 없음 + `BACKFILL_HOURS > 0` → `watermark = now(KST) - BACKFILL_HOURS`
4. 값 없음 + `BACKFILL_HOURS = 0` → `watermark = now(KST) - 5분` (backfill 생략)

### 복제 루프

```
loop:
    rows = remote.query("""
        SELECT tagsn, logtime, val, tag_stat
        FROM tb_tag_raw_data
        WHERE logtime > watermark
        ORDER BY logtime ASC
        LIMIT BATCH_SIZE
    """)
    if rows is empty: break

    # 원격은 tz-naive → KST 벽시계로 간주해 tz 부여
    rows_tz = [(LOCAL_REGION, tagsn, logtime.replace(tz=KST), val, tag_stat) for ...]

    local.execute_values("""
        INSERT INTO tb_tag_raw_data (region, tagsn, logtime, val, tag_stat)
        VALUES %s
        ON CONFLICT (logtime, tagsn) DO NOTHING
    """, rows_tz)

    watermark = rows[-1].logtime   # KST naive
    if len(rows) < BATCH_SIZE: break  # 마지막 페이지
sleep(POLL_INTERVAL_S)
```

### 타임존 처리

- 원격 DB의 `logtime`은 `timestamp without time zone`이지만 실제 의미는 **KST(Asia/Seoul)**
- 로컬 DB의 `logtime`은 `timestamp with time zone` (UTC 저장)
- 데몬은 원격 값에 KST(+09:00)를 부여해 tz-aware로 변환 후 INSERT — 결과적으로 로컬/원격의 "벽시계 시각"이 일치
- 컨테이너 `TZ=Asia/Seoul`로 고정

### Idempotency & 중단 복구

- INSERT가 항상 `ON CONFLICT (logtime, tagsn) DO NOTHING` 사용 → 재실행/겹침 안전
- 프로세스 재시작 시 `_initial_watermark()`가 로컬 max(logtime) - 5분을 기준으로 재개
- SIGTERM/SIGINT 수신 시 graceful shutdown (현재 배치 완료 후 종료)

## 환경 변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `REMOTE_DB_HOST` | `112.166.183.65` | 원격 운영 DB 호스트 |
| `REMOTE_DB_PORT` | `25479` | 원격 운영 DB 포트 |
| `REMOTE_DB_NAME` | `postgres` | 원격 DB 이름 |
| `REMOTE_DB_USER` | `postgres` | 원격 DB 사용자 |
| `REMOTE_DB_PASSWORD` | `DJpost0827///` | 원격 DB 비밀번호 (dev 편의용 기본값) |
| `LOCAL_DB_HOST` | `timescaledb` | 로컬 compose 서비스명 |
| `LOCAL_DB_PORT` | `5432` | 로컬 DB 포트 (컨테이너 내부) |
| `LOCAL_DB_NAME` | `slm` | 로컬 DB 이름 |
| `LOCAL_DB_USER` | `slm_dev` | 로컬 DB 사용자 |
| `LOCAL_DB_PASSWORD` | `slm_dev_1234` | 로컬 DB 비밀번호 |
| `LOCAL_REGION` | `R01` | 로컬 저장 시 region 컬럼값 (2026-06-09 'KW'→'R01' 통일 — 다른 테이블 정합) |
| `POLL_INTERVAL_S` | `30` | incremental 폴링 주기 (초) |
| `BACKFILL_HOURS` | `48` | 부팅 시 backfill 범위 (시간, 0=생략) |
| `BACKOFF_S` | `15` | 에러 후 재시도 대기 (초) |
| `BATCH_SIZE` | `5000` | INSERT 배치 크기 |

모두 `docker-compose.dev.yml`의 `dev-tag-ingest.environment`에서 오버라이드 가능.

## 실행

```bash
# 최초 기동
cd /Users/jykim/web
docker compose -f docker-compose.dev.yml up -d --build dev-tag-ingest

# 로그 확인
docker logs -f slm-dev-tag-ingest

# 중지 (복제만 끄기)
docker compose -f docker-compose.dev.yml stop dev-tag-ingest

# 재개
docker compose -f docker-compose.dev.yml start dev-tag-ingest

# 완전 제거
docker compose -f docker-compose.dev.yml rm -sf dev-tag-ingest
```

## 검증 체크리스트

1. 복제 데몬 기동 후 로그에서 "복제 시작" + "watermark=..." 확인
2. 로컬 DB에서:
   ```sql
   SELECT MAX(logtime), NOW() - MAX(logtime) AS age
   FROM tb_tag_raw_data;
   ```
   → `age`가 점점 줄어들어야 함 (backfill 중)
3. 완전 backfill 후 `age`는 `POLL_INTERVAL_S` + 원격 갱신 주기 정도로 유지
4. 배수지 모니터링 페이지(`/monitoring/reservoir`) 접속 → 최근 24시간 차트 표시 확인

## 납품 시 제거 체크리스트

실 운영 배포 시 아래 항목 모두 삭제하고 커밋:

- [ ] `/Users/jykim/slm/dev_tools/` 디렉토리 전체 (`tag_ingest.py`, `Dockerfile.tag_ingest`)
- [ ] `docker-compose.dev.yml`의 `dev-tag-ingest:` 서비스 블록 (주석 블록 + 환경변수 포함)
- [ ] 실행 중이라면 `docker compose rm -sf dev-tag-ingest` + `docker rmi web-dev-tag-ingest`
- [ ] 원격 운영 DB 인증 정보(`DJpost0827///`)가 다른 dev 설정 파일에 남아 있지 않은지 grep 확인:
      ```bash
      grep -rE "DJpost0827|112\.166\.183\.65:25479" /Users/jykim/slm /Users/jykim/web
      ```
- [ ] 본 사양 문서(`docs/dev-tag-ingest-spec.md`) 삭제 또는 archived 처리
- [ ] 운영 환경의 실제 수집 파이프라인(실 PLC/Node-RED flows)이 `tb_tag_raw_data`에 데이터를 쓰고 있는지 별도 검증

## 관련 사항

- 근본 배경: 본 테스트 환경의 Node-RED flows(`flows_deploy.json` / 324개 postgres 노드)는 전부 알람 조건/네트워크 상태 판정용이며, `tb_tag_raw_data` INSERT 쿼리는 **0개**다. 프로젝트 전체(`/Users/jykim/slm`, `/Users/jykim/web`)에서 `INSERT INTO tb_tag_raw_data`는 스키마 dump 파일에만 존재
- Node-RED 자체의 DB 접속 설정은 [E-014] 참고 (`host: 172.17.0.1 → slm-timescaledb`, `port: 5433 → 5432`로 수정)
- 에러 이력: `docs/error-management.md` [E-014]
