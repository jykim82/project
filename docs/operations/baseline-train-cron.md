# 트렌드 GBT baseline 재훈련 cron 등록 가이드

트렌드 "평소 대비" 정상 기대값 모델(GBT)을 **주 1회** 야간에 재훈련합니다.
사양: `docs/trend-baseline-gbt-spec.md` (§3 학습, §5 갱신주기).

- cron 없이도 동작은 정상 — 모델 아티팩트가 없으면 추론이 `hourly_mean` 으로
  자동 폴백합니다. **cron 활성 시 정상 기대값 정확도 향상**(오탐·미탐 감소).
- 학습은 60일 롤링 학습창, region 별 1 아티팩트.

---

## 1. 호출 대상 (CLI)

```
docker exec slm-backend python -m trend_baseline train --region R01 --window-days 60
```

- 산출물: `data/models/baseline_gbt_R01.pkl` + `baseline_gbt_R01_meta.json`
  (컨테이너 `/app/data/models/`, 호스트 `/Users/jykim/slm/data/models/`).
- 이전 버전은 `data/models/archive/baseline_gbt_R01_<ts>.pkl` 로 보관 → 신모델
  이상 시 포인터(pkl) 되돌리기로 무중단 롤백.
- 추론은 mtime 변경 감지로 재훈련 후 자동 리로드(프로세스 재시작 불필요).

**검증용 빠른 실행** (데이터 짧은 dev 환경):
```
docker exec -e BASELINE_WINDOW_DAYS=7 -e BASELINE_HOLDOUT_DAYS=1 \
  -e BASELINE_MIN_TAG_ROWS=96 slm-backend python -m trend_baseline train
```
> env(`BASELINE_WINDOW_DAYS`/`BASELINE_HOLDOUT_DAYS`/`BASELINE_MIN_TAG_ROWS`)는
> dev 검증 전용 하향 스위치. 운영 기본값(60/7/336)은 코드에 고정 — 사양 불변.

종료 시 stdout 으로 평가 지표 JSON 출력(MAE·RMSE·커버리지·hourly_mean 대비
개선율). cron 로그에서 추세를 확인할 수 있습니다.

---

## 2. macOS launchd (개발 환경)

`~/Library/LaunchAgents/local.slm.baseline-train-cron.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>local.slm.baseline-train-cron</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/docker</string>
    <string>exec</string>
    <string>slm-backend</string>
    <string>python</string>
    <string>-m</string>
    <string>trend_baseline</string>
    <string>train</string>
    <string>--region</string>
    <string>R01</string>
    <string>--window-days</string>
    <string>60</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key>
    <integer>0</integer>
    <key>Hour</key>
    <integer>3</integer>
    <key>Minute</key>
    <integer>30</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>/tmp/slm-baseline-train.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/slm-baseline-train.err</string>
</dict>
</plist>
```
> `Weekday` 0=일요일. `docker` 경로는 `which docker` 로 확인(OrbStack 은
> `/usr/local/bin/docker`).

등록·시작:
```bash
launchctl load ~/Library/LaunchAgents/local.slm.baseline-train-cron.plist
launchctl start local.slm.baseline-train-cron   # 즉시 1회 실행 (검증용)
launchctl list | grep baseline
```

해제:
```bash
launchctl unload ~/Library/LaunchAgents/local.slm.baseline-train-cron.plist
```

---

## 3. Linux crontab (운영 서버)

```bash
crontab -e
```

```
# 트렌드 GBT baseline 재훈련 (매주 일요일 03:30, 60일 롤링)
30 3 * * 0 /usr/bin/docker exec slm-backend python -m trend_baseline train --region R01 --window-days 60 \
  >> /var/log/slm/baseline-train.log 2>&1
```

운영 큰 변화(설비 증설·계통 변경) 시 위 명령을 수동 1회 실행하면 즉시 재훈련.
재훈련 주기는 cron 설정값이라 야간 1회 → 매일로 무비용 변경 가능.

---

## 4. 운영 모니터링

```bash
# 최근 재훈련 로그 (지표 JSON 추이)
tail -100 /tmp/slm-baseline-train.log

# 현재 모델 메타 (개선율·커버리지)
docker exec slm-backend cat /app/data/models/baseline_gbt_R01_meta.json

# 아티팩트 갱신 시각 확인
docker exec slm-backend ls -la /app/data/models/
```

P2(`/admin/model-eval?model=baseline`) 도입 후에는 회차별 지표가
DB(`tb_baseline_model_run`)에 적재되어 화면에서 추세를 확인할 수 있습니다.

---

## 5. 트러블슈팅

| 증상 | 원인 | 조치 |
|------|------|------|
| `학습 가능 태그 없음 (데이터 부족)` | 학습창 내 14일 이상 데이터 가진 태그 없음 | dev 는 §1 검증용 env 로 하향 실행 |
| 추론이 계속 `hourly_mean` | 아티팩트 미생성/로드 실패 | `ls /app/data/models/` 확인, 수동 train 1회 |
| `ModuleNotFoundError: sklearn` | backend 이미지 sklearn 누락 | `docker compose build backend` 재빌드 |
| 신모델 후 오탐 급증 | 학습 데이터 이상 | archive 의 직전 pkl 을 현재 포인터로 복사(롤백) |
