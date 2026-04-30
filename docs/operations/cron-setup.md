# 근본원인 자동 분류 cron 등록 가이드

**대상:** 운영자·시스템 관리자
**목적:** 매일 새벽에 미분류 보고서 항목을 LLM 으로 자동 분류 →
설비 교체 후보 통계가 매일 갱신되도록.
**버전:** 2026-04-30 (P1)

---

## 1. 호출 대상 endpoint

```
POST http://<백엔드>:8000/reports/items/classify-causes-cron
Content-Type: application/json

{
  "only_unclassified": true,    // true = 미분류만 (기본). false = 강제 재분류
  "limit_per_region": 200       // region 당 최대 처리 건수
}
```

응답:
```jsonc
{
  "regions": [
    { "region": "R01", "processed": 42, "hits": 31 }
  ],
  "total_processed": 42
}
```

- `processed` — 분류 시도 항목 수 (점검 보고서 자동 제외)
- `hits` — 코드 매칭 성공 (>= 1코드) 수
- 매칭 실패 항목은 빈 배열 `[]` 로 저장 + `classified_at` 갱신 (다음 cron 에서
  중복 호출 방지). `only_unclassified=false` 로 강제 재분류 가능.

> ⚠️ 인증 헤더 없음 — **내부망에서만 노출 권장**. 외부 노출 시 reverse proxy
> 에서 IP 화이트리스트로 차단할 것.

---

## 2. Linux crontab (운영 서버)

운영자 사용자로 (root 아님):

```bash
crontab -e
```

다음 라인 추가:

```
# 매일 03:00 — 근본원인 자동 분류 (모든 region 미분류 항목)
0 3 * * * /usr/bin/curl -s -m 600 -X POST http://localhost:8000/reports/items/classify-causes-cron \
  -H "Content-Type: application/json" \
  -d '{"only_unclassified":true,"limit_per_region":200}' \
  >> /var/log/slm/classify-cron.log 2>&1
```

로그 디렉토리 사전 생성:
```bash
sudo mkdir -p /var/log/slm
sudo chown <운영자>:<운영자> /var/log/slm
```

타임아웃 600초 (10분) — Ollama 모델 로드·다수 항목 처리 여유 시간.

---

## 3. macOS launchd (개발 환경)

```bash
mkdir -p ~/Library/LaunchAgents
```

`~/Library/LaunchAgents/local.slm.classify-cron.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>local.slm.classify-cron</string>

  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/curl</string>
    <string>-s</string><string>-m</string><string>600</string>
    <string>-X</string><string>POST</string>
    <string>http://localhost:8000/reports/items/classify-causes-cron</string>
    <string>-H</string><string>Content-Type: application/json</string>
    <string>-d</string><string>{"only_unclassified":true,"limit_per_region":200}</string>
  </array>

  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>3</integer>
    <key>Minute</key><integer>0</integer>
  </dict>

  <key>StandardOutPath</key>
  <string>/tmp/slm-classify.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/slm-classify.err.log</string>
</dict>
</plist>
```

등록·확인:
```bash
launchctl load ~/Library/LaunchAgents/local.slm.classify-cron.plist
launchctl list | grep slm
```

수동 즉시 실행 테스트:
```bash
launchctl start local.slm.classify-cron
tail -50 /tmp/slm-classify.log
```

---

## 4. Docker Compose 통합 (대안)

별도 cron 컨테이너 없이 **백엔드 컨테이너 내부**에 cron 데몬을 띄우는 방식은
권장하지 않습니다 (사이드카 패턴이 더 깔끔). 운영 환경에선 OS-level cron 사용.

만약 cron 컨테이너를 두고 싶다면 `docker-compose.dev.yml` 에 추가:

```yaml
services:
  slm-cron:
    image: alpine:3
    restart: unless-stopped
    depends_on: [slm-backend]
    command: >
      sh -c "apk add --no-cache curl &&
             echo '0 3 * * * curl -s -m 600 -X POST http://slm-backend:8000/reports/items/classify-causes-cron \
                    -H \"Content-Type: application/json\" \
                    -d {\"only_unclassified\":true,\"limit_per_region\":200} \
                    >> /var/log/slm-cron.log 2>&1' | crontab - &&
             crond -f"
    volumes:
      - ./logs/cron:/var/log
```

---

## 5. 운영 모니터링

폐쇄망이라 외부 알림(Slack·이메일) 미사용. 매주 1회 운영자가 직접 확인:

```bash
# 최근 7일 cron 로그
tail -200 /var/log/slm/classify-cron.log

# 매칭 실패율
grep "total_processed" /var/log/slm/classify-cron.log | tail -7
```

응답 JSON 의 hits 수가 0 이 며칠 연속이면:
- LLM 모델 상태 확인 (Ollama 데몬 살아있는지)
- `tb_root_cause_taxonomy.hint` 의 키워드가 실 보고서 내용과 잘 매칭되는지
  관리 페이지에서 확인 → 보강

---

## 6. 수동 트리거 (긴급 분류)

cron 을 기다리지 않고 즉시 분류하려면 화면에서:

- **모니터링 > 설비 건강성 > 근본원인 통계** 탭
- **[전체 재분류]** 버튼 (Sparkles 아이콘)
- 모든 보고서 항목 일괄 강제 재분류 (`only_unclassified=false` 동작)

또는 직접 API 호출:
```bash
curl -s -m 600 -X POST http://localhost:8000/reports/items/classify-causes-cron \
  -H "Content-Type: application/json" \
  -d '{"only_unclassified":false,"limit_per_region":500}' | jq
```

---

## 7. 트러블슈팅

| 증상 | 원인 / 조치 |
|---|---|
| `total_processed=0` | 미분류 항목 없음. 정상. 강제 재분류는 `only_unclassified=false` |
| 모든 hits=0 | LLM 빈 응답 — `ollama_client.generate(format="json")` 옵션 적용 여부 확인. Ollama 데몬·모델 로드 확인 |
| 응답 timeout | `limit_per_region` 줄이기 (200 → 50) 또는 cron `-m 600` 늘리기 |
| 로그에 401/403 | 외부에서 호출 시도. 내부망 IP 외 차단 |
| 분류 결과가 이상 | 관리 메뉴에서 hint 보강 → `[전체 재분류]` 강제 재실행 |

---

## 관련 문서

- 사양: `docs/report-spec.md` §3.4.2
- 운영자 매뉴얼: `docs/operations/report-quickstart.md`
- 분류 코드 마스터: `tb_root_cause_taxonomy` (Migration 0063)
