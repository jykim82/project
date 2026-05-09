# EPANET 자동 시뮬 cron 등록 가이드 (Phase 3.3d 후속)

매일 자동으로 EPANET 정상상태 시뮬을 실행해 시계열을 누적합니다.
누적 데이터는 다음 메뉴에서 활용:
- 관망 노후도 평가 (월별 편차 추세 — 시뮬 시계열 누적 후 의미 있음)
- 시나리오 비교 (평소 vs 변경 시나리오 비교 시 평소 기준점)
- 블록 교체 후보 (장기 패턴)

cron 없이도 메뉴 동작은 정상. **cron 활성 시 정확도·신뢰도 향상**.

---

## 1. 호출 대상 endpoint

```
POST http://<백엔드>:8000/admin/epanet/sim/cron
Content-Type: application/json

{
  "region": "R01",
  "skip_if_recent_minutes": 30,
  "use_synthetic_elevation": true,
  "use_synthetic_demand": true
}
```

**응답 (skipped)**:
```json
{ "skipped": true, "sim_id": 13, "reason": "최근 30분 이내 시뮬 #13 존재" }
```

**응답 (실행)**:
```json
{
  "skipped": false,
  "sim_id": 14, "artifact_id": 15,
  "node_count": 119, "link_count": 131,
  "duration_ms": 850
}
```

**중복 방지**: `skip_if_recent_minutes` 분 이내 success 시뮬이 있으면 스킵.
하루에 여러 번 호출해도 안전. 0 으로 두면 항상 강제 실행.

---

## 2. macOS launchd (개발 환경)

`~/Library/LaunchAgents/local.slm.epanet-sim-cron.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>local.slm.epanet-sim-cron</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/curl</string>
    <string>-s</string>
    <string>-m</string>
    <string>120</string>
    <string>-X</string>
    <string>POST</string>
    <string>http://localhost:8000/admin/epanet/sim/cron</string>
    <string>-H</string>
    <string>Content-Type: application/json</string>
    <string>-d</string>
    <string>{"region":"R01","skip_if_recent_minutes":30}</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>2</integer>
    <key>Minute</key>
    <integer>30</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>/tmp/slm-epanet-cron.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/slm-epanet-cron.err</string>
</dict>
</plist>
```

등록·시작:
```bash
launchctl load ~/Library/LaunchAgents/local.slm.epanet-sim-cron.plist
launchctl start local.slm.epanet-sim-cron   # 즉시 1회 실행 (검증용)
launchctl list | grep epanet
```

해제:
```bash
launchctl unload ~/Library/LaunchAgents/local.slm.epanet-sim-cron.plist
```

---

## 3. Linux crontab (운영 서버)

```bash
crontab -e
```

```
# EPANET 자동 시뮬 (매일 02:30, 시계열 누적)
30 2 * * * /usr/bin/curl -s -m 120 -X POST http://localhost:8000/admin/epanet/sim/cron \
  -H "Content-Type: application/json" \
  -d '{"region":"R01","skip_if_recent_minutes":30}' \
  >> /var/log/slm/epanet-sim-cron.log 2>&1

# 매주 일요일 03:00 — 90일 초과 시뮬 정리 (최소 30개는 보존)
0 3 * * 0 /usr/bin/curl -s -m 60 -X POST "http://localhost:8000/admin/epanet/sim/cleanup?region=R01&days=90&keep_min=30" \
  >> /var/log/slm/epanet-cleanup.log 2>&1
```

---

## 4. 시계열 누적 후 효과

**network-aging** 메뉴:
- 누적 1주: 일별 편차 시점별 비교
- 누적 1개월: 주별 편차 추세 (악화/개선 감지 정확도 ↑)
- 누적 3개월+: 월별 편차 + 노후도 등급 진단

**scenario-diff** 메뉴:
- 평소 시뮬 (cron 누적) vs 변경 시나리오 (즉석 실행) 비교
- 야간 vs 주간 패턴 분석 (시간대별 cron 분리 시)

**replacement-candidates**:
- 단기 z-score 보다 *지속적으로 z 큰* 파이프 식별 가능

---

## 5. 운영 모니터링

```bash
# 최근 7일 cron 로그
tail -200 /tmp/slm-epanet-cron.log

# 시뮬 누적 카운트 (DB 직접)
docker exec slm-timescaledb psql -U slm_dev -d slm -c "
  SELECT date_trunc('day', created_at) AS day, COUNT(*)
    FROM tb_epanet_simulation_result
   WHERE region='R01' AND status='success'
   GROUP BY day ORDER BY day DESC LIMIT 30;
"
```

## 6. 트러블슈팅

| 증상 | 원인 | 조치 |
|------|------|------|
| 응답 503 "EPANET 모듈 비활성" | SITE_SETTING.EPANET_ENABLED='N' | /admin/site-settings 토글 ON |
| 응답 501 "wntr 미설치" | backend 이미지 wntr 빌드 실패 | `docker compose build backend` 재빌드 |
| 응답 400 "성공 INP 산출물 없음" | INP 한 번도 안 만듦 | /admin/epanet 진입 → [INP 생성] |
| 모두 skipped | 최근 N분 이내 시뮬 있음 (정상) | `skip_if_recent_minutes:0` 으로 강제 실행 |
