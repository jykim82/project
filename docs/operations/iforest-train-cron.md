# IForest 이상탐지 모델 재학습 cron 등록 가이드

IForest(Isolation Forest) 설비·태그 이상탐지 모델을 **주 1회** 야간에
재학습하고, 회차별 안정성 지표를 DB 에 적재합니다.
사양: `docs/iforest-eval-spec.md` (§3 영속화, §5 CLI·cron, §6 평가 API).

- cron 없이도 탐지는 정상 동작 — 서버 기동 시 디스크 아티팩트를 로드하고,
  주기적 인메모리 재학습 루프가 모델을 갱신합니다. **cron 활성 시** 회차별
  지표가 누적되어 `/admin/iforest-eval` 화면에서 추세를 확인할 수 있습니다.
- 비지도 모델이라 "정확도%" 가 아니라 **안정성(calibration_err)·커버리지%**
  로 평가합니다 (정답 레이블 기반 탐지 정밀도는 P2 로 보류).
- 학습은 30일 롤링 학습창, region 별 1 아티팩트.

---

## 1. 호출 대상 (CLI)

```
docker exec slm-backend python -m anomaly_iforest train --region R01
```

- 산출물: `data/models/iforest_R01.pkl`
  (컨테이너 `/app/data/models/`, 호스트 `/Users/jykim/slm/data/models/`).
- 원자적 쓰기(`.tmp` → `os.replace`)로 학습 중 부분 파일 노출 없음.
- 학습 직후 회차 지표가 `tb_iforest_model_run`(KPI 1행) +
  `tb_iforest_model_metric`(모델별 N행) 에 UPSERT 됩니다.
- 추론 서버는 다음 인메모리 재학습 루프에서 자동 리로드(프로세스 재시작 불필요).

종료 시 stdout 으로 회차 요약 JSON(커버리지·평균 이상률·calibration_err·
모델 수)을 출력합니다. cron 로그에서 추세를 확인할 수 있습니다.

---

## 2. macOS launchd (개발 환경)

`~/Library/LaunchAgents/local.slm.iforest-train-cron.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>local.slm.iforest-train-cron</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/docker</string>
    <string>exec</string>
    <string>slm-backend</string>
    <string>python</string>
    <string>-m</string>
    <string>anomaly_iforest</string>
    <string>train</string>
    <string>--region</string>
    <string>R01</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key>
    <integer>0</integer>
    <key>Hour</key>
    <integer>4</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>/tmp/slm-iforest-train.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/slm-iforest-train.err</string>
</dict>
</plist>
```
> `Weekday` 0=일요일. baseline(03:30)과 겹치지 않게 04:00 으로 분리.
> `docker` 경로는 `which docker` 로 확인(OrbStack 은 `/usr/local/bin/docker`).

등록·시작:
```bash
launchctl load ~/Library/LaunchAgents/local.slm.iforest-train-cron.plist
launchctl start local.slm.iforest-train-cron   # 즉시 1회 실행 (검증용)
launchctl list | grep iforest
```

해제:
```bash
launchctl unload ~/Library/LaunchAgents/local.slm.iforest-train-cron.plist
```

---

## 3. Linux crontab (운영 서버)

```bash
crontab -e
```

```
# IForest 이상탐지 모델 재학습 (매주 일요일 04:00, 30일 롤링)
0 4 * * 0 /usr/bin/docker exec slm-backend python -m anomaly_iforest train --region R01 \
  >> /var/log/slm/iforest-train.log 2>&1
```

설비 증설·계통 변경 시 위 명령을 수동 1회 실행하면 즉시 재학습·지표 적재.
재학습 주기는 cron 설정값이라 야간 1회 → 매일로 무비용 변경 가능.

---

## 4. 운영 모니터링

```bash
# 최근 재학습 로그 (회차 요약 JSON 추이)
tail -100 /tmp/slm-iforest-train.log

# 아티팩트 갱신 시각 확인
docker exec slm-backend ls -la /app/data/models/iforest_R01.pkl

# 최신 회차 지표 (psql)
docker exec slm-timescaledb psql -U slm_dev -d slm -c \
  "SELECT model_version, coverage_pct, calibration_err, total_models \
     FROM tb_iforest_model_run WHERE region='R01' ORDER BY trained_at DESC LIMIT 5;"
```

회차별 지표는 화면 `/admin/iforest-eval` (관리 그룹, MASTER/ADMIN) 에서
KPI·추세·그룹별 집계·최악 캘리브레이션 모델로 확인합니다.

---

## 5. 트러블슈팅

| 증상 | 원인 | 조치 |
|------|------|------|
| 화면 `학습 회차 없음` (ready=false) | cron/수동 학습 미실행 | §1 명령 1회 실행 |
| calibration_err 급증 | 학습 데이터 이상·분포 급변 | 해당 그룹 `/admin/iforest-eval` 드릴다운 점검, 원인 설비 확인 |
| coverage% 하락 | 적격 설비 대비 모델 미생성 증가(데이터 부족) | tier-2 폴백 태그 데이터 적재 상태 확인 |
| 지표가 DB 에 안 쌓임 | 지표 테이블 미생성 | migration `0091_iforest_model_metrics.sql` 적용 확인 |
| `ModuleNotFoundError: sklearn` | backend 이미지 sklearn 누락 | `docker compose build backend` 재빌드 |
