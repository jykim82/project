# 태그 원시데이터 스캔 최적화 (2026-07-27)

`tb_tag_raw_data` 압축 + **시간 범위 없는 전 청크 스캔 3건 제거**.

저사양 온프레미스 납품을 전제로 한 조사에서 시작해, 압축보다 큰 문제(상시
실행 루프의 전 이력 스캔)를 발견하고 함께 정리한 기록.

**관련**: `db/migrations/0128_tag_raw_compression.sql`,
`docs/operations/delivery-checklist.md`

---

## 1. 출발점 — 조사 시점 현황

```
tb_tag_raw_data : 447,356,770 행 / 120 GB
                  (데이터 33 GB + 인덱스 88 GB)
기간            : 2026-02-02 ~ 07-27 (약 6개월)
압축            : 비활성        보존정책 : 없음
shared_buffers  : 4 GB
```

6개월에 120 GB. 이 속도면 1년차 240 GB 로, 저사양 서버에서 감당이 안 된다.
더 중요한 건 디스크가 아니라 **캐시**다 — 작업셋 120 GB 가 shared_buffers
4 GB 에 들어갈 리 없어 장기 조회가 전부 디스크 I/O 가 된다.

조회 기간별 실측 (5 태그, `/trend/data` 와 동일한 쿼리 형태):

| 기간 | cold | warm |
|---|---:|---:|
| 24시간 | 23 ms | 9.6 ms |
| 7일 | 116 ms | 37.7 ms |
| 1개월 | 3,936 ms | 133 ms |
| **1년** | **25,245 ms** | 1,027 ms |

cold/warm 이 25배 벌어지는 것 자체가 "캐시에 안 들어간다"는 신호다.

---

## 2. 압축 (Migration 0128)

### 2.1 설정 근거

```sql
timescaledb.compress_segmentby = 'tagsn'
timescaledb.compress_orderby   = 'logtime DESC'
```

앱의 시계열 조회는 예외 없이 `tagsn = ANY(...)` 로 시작한다
(`shared/timeseries.py` `query_chunks_agg`, `endpoints/trend.py`).
`tagsn` 으로 세그먼트하면 해당 태그의 압축 배치만 읽는다.
`logtime DESC` 는 태그 내 델타 인코딩 + "최신값 1건" 조기 종료를 만든다.

### 2.2 실측

| 항목 | 결과 |
|---|---|
| 단일 청크 | 3,534 MB → 26 MB (**135.7×**) |
| 초기 5청크 누적 | 29 GB → 99 MB (**302.7×**) |
| **전체 (21/26 청크)** | **120 GB → 28 GB** |
| 청크당 소요 | 20~40초 |
| 7일 청크 집계 쿼리 | 1,084 ms(cold) → **10.9 ms** |

조회 기간별 (5 태그, 동일 쿼리):

| 기간 | 전 (cold) | 후 (cold) | 전 (warm) | 후 (warm) |
|---|---:|---:|---:|---:|
| 24시간 | 23 ms | 22 ms | 9.6 ms | 11.4 ms |
| 7일 | 116 ms | 133 ms | 37.7 ms | 42.6 ms |
| 1개월 | 3,936 ms | 2,579 ms | 133 ms | 99 ms |
| **1년** | **25,245 ms** | **1,999 ms** | 1,027 ms | **278 ms** |

**1년 조회가 12.6× 빨라졌다** (25.2초 → 2.0초). 24시간·7일이 그대로인 것은
**의도한 결과**다 — 그 구간은 `compress_after=14일` 밖이라 비압축으로 남는다
(적재 성능 보호). 표의 ±15% 차이는 실행 간 편차 범위로, 3회 재측정에서
7일 61 ms / 1개월 161 ms / 1년 454 ms 로 회귀 없음을 확인했다.

1개월이 1.5× 에 그친 이유: 최근 30일 구간은 절반가량이 비압축 청크(14일 이내)
라 압축 효과가 그만큼만 걸린다. 시간이 지나 청크가 압축되면 이 구간도
1년과 같은 폭으로 개선된다.

**남은 2개 청크**(`_hyper_1_12`, `_hyper_1_13`)는 락 경합으로 3회 재시도 후
건너뛰었다 — 실패가 아니라 설계대로다. 새벽 3시 정책 job 이 처리한다.

압축비가 100× 를 넘는 건 SCADA 데이터 특성 때문이다 — 같은 값이 길게
반복되고, 인덱스 88 GB 가 압축 청크에선 통째로 사라진다(세그먼트 min/max
희소 인덱스로 대체).

### 2.3 무결성 검증

압축 **전** 원본에서 만들어진 `cagg_5min_raw_stats_ai` 와 대조 —
2,700 태그 전부 count·min·max 일치.

> **함정**: 청크 경계는 UTC 정렬인데 세션 TZ 는 Asia/Seoul 이다. KST 날짜로
> 잘라 비교하면 9시간이 어긋나 전 태그가 불일치로 나온다. 검증 시
> `timescaledb_information.chunks.range_start/range_end` 를 그대로 쓸 것.
>
> 또 `cagg_1h_raw_stats_ai` 는 5분 cagg 위에 얹힌 **계층형**이라 `cnt` 가
> 원시 행 수가 아니다. 무결성 대조에는 5분 cagg 를 쓸 것.

### 2.4 운영 주의 — 락

`compress_chunk` 는 AccessExclusiveLock 을 잡는다. 두 가지 다른 위험이 있다.

1. **락을 기다리는 동안** 뒤따르는 모든 읽기/쓰기가 그 뒤에 줄 선다
   (head-of-line blocking). 검증 중 Node-RED 알람 INSERT 가 **9분간 막혔다.**
   → `SET lock_timeout='10s'` 로 대기를 포기시키고 재시도한다.
2. **락을 잡은 뒤에도** 청크당 20~40초 보유한다 (해당 청크 읽기만 대기).
   → `compress_after` 를 충분히 크게 둬 활성 조회 구간을 피한다.

정책 job 은 **새벽 3시 고정**, `compress_after = 14 days`.
청크 간격이 7일이라 최근 2개 청크(24시간·7일 조회 구간)는 항상 비압축으로
남는다. 적재는 전진 방향만이라(`dev_tools/tag_ingest.py` watermark =
`max(logtime) - 5분`) 과거 청크 backfill 이 없어 안전하다.

보존정책(`drop_chunks`)은 넣지 않았다 — 원시 데이터 삭제는 되돌릴 수 없고,
압축만으로 용량 문제가 해소된다.

---

## 3. 전 청크 스캔 제거 3건 — 압축보다 큰 효과

압축이 락에 막혀 원인을 추적하다 발견했다. 셋 다 **"태그의 최신값 1건"을
얻으려고 `logtime` 하한 없이 조회**해, 플래너가 청크 제외를 못 하고 26개
청크를 전부 Merge Append 로 여는 형태였다.

| # | 위치 | 수정 전 | 수정 후 |
|---|---|---:|---:|
| ① | `ai_server.py` `_release_stale_alarms` | **14분 30초** | **6.9 ms** |
| ② | `endpoints/alarm_crisis.py` 설정값·측정값 배치 조회 2곳 | **332초** | **0.16초** |
| ③ | `endpoints/flow_realtime.py` `max(logtime)` | **7.6초** | **3.5 ms** |

### ① 알람 자동 해제 루프 — 가장 심각

`_alarm_release_loop` 가 **2분 주기**로 도는데 1회 실행이 14분 30초.
즉 사실상 **DB 를 상시 점유**하고 있었다. 진행중 디지털 알람 43건 ×
26청크 = 매 주기 1,118회 인덱스 스캔.

```python
# 전: 태그별 전체 이력에서 DISTINCT ON
SELECT DISTINCT ON (tagsn) tagsn, val FROM tb_tag_raw_data
WHERE tagsn = a.tagsn ORDER BY tagsn, logtime DESC

# 후: LATERAL + LIMIT 1 + 시간 창
CROSS JOIN LATERAL (
    SELECT r.val FROM tb_tag_raw_data r
    WHERE r.tagsn = a.tagsn
      AND r.logtime > now() - interval '1 day'
    ORDER BY r.logtime DESC LIMIT 1
) latest
```

**부수 효과로 판정이 더 안전해졌다.** 무제한 조회는 몇 달 전 마지막 값으로
알람을 해제할 수 있었다. 창 밖이면 "확인 불가"로 보고 진행중을 유지한다 —
통신 두절 태그의 알람이 유지되는 쪽이 옳다.

### ② 경보 이력 화면 설정값·측정값

`/alarm/list` 진입마다 AMC/LEC(설정값)·LEI(측정값) 배치 조회가 전 이력을
훑었다. 시간 창 적용 전 **332초**.

창을 걸기 전에 "설정값은 변경 시에만 기록되는 것 아닌가"를 데이터로 확인했다
— 설정값 태그 392개 중 **387개가 1일 내 갱신**, 나머지 5개는 이력 자체가
없어 창과 무관. SCADA 가 설정값도 상시 폴링해 기록하므로 안전.

### ③ 용수 흐름 계통도 `max(logtime)`

`/flow-map/realtime` 은 폴링 화면인데, "적재가 멈췄으면 조회 창을 최신
시점으로 옮긴다"는 dev 폴백 때문에 매 주기 `SELECT max(logtime)`(범위 없음)
을 물었다. 창을 준 조회로 먼저 끝내고, NULL 일 때만(적재 정지 상황) 전 구간을
훑도록 바꿔 폴백 의미를 보존했다.

---

## 4. 재발 방지

**`tb_tag_raw_data` 조회에는 반드시 `logtime` 하한을 준다.** 예외는 청크를
직접 지정하는 `shared/timeseries.py` 계열뿐이다.

"최신값 1건"이 필요할 때의 표준형:

```sql
SELECT val FROM tb_tag_raw_data
WHERE tagsn = %s AND logtime > now() - interval '1 day'
ORDER BY logtime DESC LIMIT 1
```

창 밖이면 값이 없는 것이 **정상 동작**이다 — 그 태그는 살아 있지 않다는
뜻이므로, 오래된 값으로 판정하는 것보다 옳다.

창 길이는 모듈 상수로 분리하고(`_ALARM_RELEASE_LOOKBACK`,
`_LATEST_VAL_LOOKBACK`, `_MAX_LOGTIME_PROBE`) 왜 그 길이인지 주석에 남긴다.

### 점검 방법

```bash
# 하한 없는 조회 후보 (청크 직접 지정 방식은 제외하고 판단)
grep -rn "tb_tag_raw_data" --include="*.py" /Users/jykim/slm
```

```sql
-- 상시 실행 루프가 DB 를 점유하고 있는지: 같은 쿼리가 계속 잡히면 의심
SELECT now()-query_start dur, left(regexp_replace(query,'\s+',' ','g'),60)
FROM pg_stat_activity WHERE state <> 'idle' ORDER BY query_start;
```
