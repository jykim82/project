# IForest 모델 영속화 + 성능 평가 사양 v1

작성일: 2026-06-17
관련: `docs/iforest.md`(테스트 계획), `docs/trend-baseline-gbt-spec.md`(쌍둥이 패턴),
`anomaly_iforest.py`, `endpoints/iforest_eval.py`, `/admin/iforest-eval`

## 1. 배경 — 왜 영속화·평가가 필요한가

Isolation Forest 이상탐지(`anomaly_iforest.py`)는 그동안 **인메모리 전용**이었다.

- 서버 기동 10초 후 첫 학습, 이후 24h 주기로 `IForestManager.train_all` 이
  전 시설/태그를 **DB에서 통째로 재학습**해 RAM 에만 보관.
- **디스크 저장 없음** → 서버 재시작(배포·OOM·크래시)마다 모델 소실. 재학습이
  끝날 때까지 `is_trained=False` → `predict_for_rows` 가 `{}` 반환(다변량 무탐지).
- 학습 회차·지표를 **DB 에 전혀 남기지 않음** → 감사·재현·성능 추적 불가.

쌍둥이 격인 트렌드 GBT baseline 은 이미 **디스크 pkl + CLI + cron + 지표 테이블 +
`/admin/baseline-eval` 화면**을 갖췄다. IForest 만 이 패턴에서 빠져 있어
(a) 콜드스타트 무탐지 사각, (b) 감사 불가, (c) 평가 화면 부재 의 3중 결함이 있다.

본 사양은 IForest 를 baseline 과 동일한 영속·평가 패턴으로 정렬한다.

## 2. 비지도 모델의 평가 — 무엇을 잴 수 있나 (정직성 원칙)

GBT baseline 은 **지도학습(회귀)** 이라 예측값 vs 홀드아웃 실측값으로 정확도%를
낸다. IForest 는 **비지도 이상탐지** 라 정답 레이블이 없어 같은 "정확도%"를 낼 수
**없다.** 따라서 평가 지표를 2단계로 분리한다.

### 2.1 P1 — 모델 안정성·커버리지 (레이블 불필요, 본 사양 구현 범위)

학습 데이터 자체에서 산출 가능한, 모델이 "건강하게 학습됐는가" 지표:

- **커버리지(coverage%)** — 학습 가능 후보(시설·태그) 중 실제 모델이 만들어진
  비율. 낮으면 데이터 부족으로 다수가 무모델(무탐지).
- **캘리브레이션 오차(calibration_err)** — 학습 데이터에서 **관측 이상률**이
  설정 **목표 이상률(contamination)** 에서 벗어난 정도 `|관측% − 목표%|`.
  IsolationForest 는 contamination 비율만큼을 이상으로 잘라내도록 적합되므로,
  관측 이상률이 목표와 크게 다르면 퇴화/과적합 신호. **0 에 가까울수록 양호.**
- **관측 이상률(anomaly_rate)** — 학습 표본 중 이상 판정 비율(%).
- **점수 분포(mean_score / score_std)** — `score_samples` 평균·표준편차.
  회차 간 급변하면 데이터 분포 변화 신호.
- **Tier 분포** — Tier-1(시설 다변량)/Tier-2(태그 단변량) 모델 수.

→ **헤드라인 KPI = 커버리지% + 캘리브레이션 오차.** "정확도%"가 아니라
"안정성·커버리지" 평가임을 화면에 명시한다.

### 2.2 P2 — 탐지 성능 검증 (레이블 필요, 향후)

운영자 확인 레이블이 쌓이면 추가:

- `tb_equipment_alarm_report.is_false_alarm` / `alarm_confirm_yn` 를 약지도 레이블
  로 → **Precision / 오탐율 / 탐지 리드타임**.
- `test_iforest_v2.py` T3 물리 모순 합성 케이스(누수/펌프 공회전/감압 실패) →
  릴리스별 **합성 탐지율 회귀테스트**.

P2 는 본 사양 범위 밖. 화면에 "탐지 성능 검증은 운영 레이블 축적 후 제공" 안내만 둔다.

## 3. 영속화 설계

### 3.1 모델 디스크 저장 (콜드스타트 해소)

- `train_all` 성공 시 `data/models/iforest_<region>.pkl` 로 원자적 저장
  (`.tmp` → `os.replace`). payload: `facility_models`, `tag_models`,
  `tier1_covered`, `last_trained`, `region`.
- 환경변수 `IFOREST_MODEL_DIR`(기본 `data/models`) 로 위치 변경 가능. baseline 과
  동일 디렉토리 공유, `.gitignore`(cron 재생성 산출물).
- 서버 기동 시 `_iforest_training_loop` 가 sleep 전에 `load_from_disk()` 호출 →
  재시작 직후 0초에 직전 모델 복원(무탐지 사각 제거). 이후 +10초 백그라운드
  재학습이 갱신.
- 저장/로드 실패는 **경고만** — 학습·예측은 정상 동작(부가 기능 원칙).

### 3.2 지표 DB 적재

`train_all` 끝에 회차·모델별 지표를 산출해 적재(테이블 없거나 실패해도 학습은
성공으로 간주). region 기반 멀티테넌시 유지.

## 4. DB 스키마 (Migration 0091)

### tb_iforest_model_run (재훈련 1회 = 1행)
| 컬럼 | 타입 | 의미 |
|------|------|------|
| id | bigserial PK | |
| region | varchar NOT NULL DEFAULT 'R01' | |
| model_version | varchar NOT NULL | `YYYYMMDD_HHMMSS`(UTC) |
| trained_at | timestamptz | |
| train_window_days | integer | 학습창(기본 30) |
| tier1_count / tier2_count / total_models | integer | 모델 수 |
| n_eligible / n_skipped | integer | 학습 후보·스킵 수 |
| coverage_pct | double precision | total_models / n_eligible × 100 |
| mean_anomaly_rate | double precision | 관측 이상률 평균(%) |
| mean_contamination | double precision | 목표 이상률 평균(%) |
| calibration_err | double precision | 평균 \|관측−목표\|(%) |
| mean_score | double precision | |
| feature_set | varchar | 'v2' |
| status | varchar NOT NULL DEFAULT 'ok' | |
| UNIQUE(region, model_version) | | |

### tb_iforest_model_metric (회차 × 모델)
| 컬럼 | 타입 | 의미 |
|------|------|------|
| id | bigserial PK | |
| region / model_version | varchar | |
| tier | integer | 1=시설, 2=태그 |
| entity_key | varchar | `sitename/facilitytype` 또는 tagsn |
| sitename / facilitytype | varchar | |
| n_samples / n_features | integer | |
| contamination | double precision | 목표 이상률(0~1) |
| anomaly_rate | double precision | 관측 이상률(%) |
| mean_score / score_std | double precision | |
| calibration_err | double precision | \|anomaly_rate − contamination×100\| |
| UNIQUE(region, model_version, tier, entity_key) | | |

롤백: `DROP TABLE tb_iforest_model_metric, tb_iforest_model_run;`(지표만 소실, 모델 무관).

## 5. CLI / cron

```
python -m anomaly_iforest train [--region R01]
```

- `tb_site_anomaly_profile` 에서 site_group 로드 → 그룹별 contamination 적용
  (서버 학습과 동일). 없으면 기본 0.05.
- 학습 → 디스크 저장 → 지표 적재. cron 으로 주1회(또는 일1회) 실행 가능.
- 가이드: `docs/operations/iforest-train-cron.md`(baseline-train-cron 패턴 재사용).

서버 백그라운드 학습 루프(24h)는 유지 — CLI 는 수동·cron 보강용. 둘 다 같은
pkl/테이블에 기록(ON CONFLICT UPSERT).

## 6. 조회 API (`endpoints/iforest_eval.py`, 읽기 전용)

`GET /admin/iforest-eval?region=R01&worst_limit=20&tier=`
- `ready` — 회차 유무
- `latest` / `runs` — 최신 회차 + 최근 20회차(추이 차트용)
- `groups.by_tier` — Tier-1/Tier-2 집계(모델 수·평균 캘리브레이션·평균 이상률·목표)
- `groups.by_facilitytype` — 시설유형별 집계
- `worst_models` — 캘리브레이션 오차 내림차순(tier 필터 가능)
- `overall` — 커버리지·캘리브레이션·이상률·tier 카운트

`GET /admin/iforest-eval/group-models?region&axis=tier|facilitytype&group=...`
- 그룹에 속한 모델 상세 리스트(캘리브레이션 내림차순).

## 7. 평가 화면 (`/admin/model-eval?model=iforest`, M100-13)

> 통합 "AI 모델 평가" 화면(`/admin/model-eval`)에서 모델 셀렉터로 선택. 구
> 라우트 `/admin/iforest-eval` 은 redirect. 백엔드 API 경로 불변
> (`GET /admin/iforest-eval`).

baseline-eval 과 동일 구조·룩앤필:
1. **종합 판정 배지** — 커버리지·캘리브레이션 기준 우수/양호/주의/미흡 + 점검
   필요 시설유형 칩. (프런트 `computeVerdict`)
2. **KPI 카드** — 커버리지% / 캘리브레이션 오차 / Tier-1·Tier-2 모델 수 /
   평균 관측 이상률. 각 부제에 목표·의미.
3. **회차 추이 라인차트** — 커버리지% + 캘리브레이션 오차(EChartWrapper **svg**,
   `chart-rendering-policy` 준수, 목표선 markLine).
4. **그룹 성능 카드** — Tier별·시설유형별 캘리브레이션/이상률/모델수, 드릴다운.
5. **최악 캘리브레이션 모델 표** — 퇴화 의심 모델 상위 N.
6. **용어 해설 토글** — 캘리브레이션/contamination/Tier 등 비전문가용.
7. **안내 배너** — "비지도 모델 — 안정성·커버리지 평가. 탐지 정밀도 검증은 운영
   레이블 축적 후(P2)."

라이트/다크 모드, 한국어 UI, adminOnly(M100-14 → MASTER/ADMIN).

## 8. 폐쇄망·제품화 준수

- 신규 의존성 0 (sklearn·psycopg2 기존). 외부 API 없음.
- region 멀티테넌시 유지, 하드코딩 회피(env 스위치).
- migration 롤백 절차 명시, 데이터 손실 없는 업그레이드.

## 9. 변경 이력

- 2026-06-17 v1 — 영속화(pkl)+지표 테이블(0091)+CLI/cron+`/admin/iforest-eval`(0092).
  P1(안정성·커버리지) 구현, P2(레이블 기반 검증) 예고.
- 2026-06-17 "AI 모델 평가" 통합 화면 — 트렌드·IForest 평가를 `/admin/model-eval`
  한 화면으로 통합, 셀렉터 전환(`?model=iforest`). `IForestEvalView` 컴포넌트로
  추출, 구 라우트 redirect. 메뉴 M100-14 삭제·M100-13 "AI 모델 평가"로 통합
  (migration 0093). 백엔드 API·로직 불변.


## P1.5 — 알람-일치율 (weak-label) 평가 (2026-07-16 추가, Migration 0098)

사람 판정 레이블(P2 조건: 50건+)이 쌓이기 전, **실제 알람 이벤트를 약한
레이블**로 사용해 탐지 정렬도를 산출한다. 사용자 지적("실제 데이터가 올라오고
있는데?")에서 출발 — 센서·알람 실데이터는 유입 중이므로 proxy 평가는 지금 가능.

### 방식 (`anomaly_iforest._evaluate_alarm_agreement`, 학습 직후 실행)
- 평가 창 7일, 아날로그 계열 알람(수위/압력/유량)만. 알람 tagsn 은 경보(디지털)
  태그이므로 **시설+카테고리 → 해당 시설 아날로그 태그**로 매핑 (근사)
- 이벤트별 ±60분 cagg_5min 피처를 Tier-2 태그 모델로 판정 → 1개 이상 이상(-1)이면 hit
- Tier-1 커버 시설(태그 모델 없음)은 커버리지에서 제외 카운트

### 지표 (tb_iforest_model_run 컬럼 / admin API / UI KPI)
| 지표 | 의미 | 첫 실측 (07-16) |
|---|---|---|
| alarm_recall_pct | 알람 이벤트 중 IForest 도 이상으로 본 비율 (재현율 proxy) | 46.4% |
| alarm_lift | 알람 구간 이상률 ÷ 평상시 이상률 — >1 이면 알람과 정렬 | ×1.48 |
| alarm_events_evaluated/total | 평가 커버리지 | 84/156 |

### 한계 (반드시 함께 표기)
알람 자체에 오탐이 섞여 있어(본 제품의 전제) **정밀도가 아니다**. 재현율
proxy + lift 로 방향성만 판단. 현장 확인 판정 50건+ 축적 시 P2 로 승격
(트리거 조건: docs/review-items.md [대기] 절).

### UI
`/admin/model-eval?model=iforest` — 기존 KPI 아래 P1.5 행 (재현율 proxy /
lift / 설명 노트). 데이터 없으면(alarm_events_total null) 행 자체 숨김.
