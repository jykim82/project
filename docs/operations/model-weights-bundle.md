# 모델 웨이트 오프라인 번들 (납품 필수)

폐쇄망 납품 시 AI 웨이트 반입 절차. **이 절차를 빠뜨리면 납품 장비에서
음성 입력(STT)과 트렌드 향후 전망(Chronos)이 조용히 폴백/비활성** 된다
(STT 503, forecast 는 선형 폴백으로 동작하나 품질 저하).

## 대상 (git 제외 — 재학습 불가한 사전학습 웨이트)
| 웨이트 | 경로 (slm 레포) | 크기 | 사용처 |
|---|---|---|---|
| Chronos-Bolt base (Amazon, Apache-2.0) | `data/models/chronos-bolt-base` | ~783MB | 트렌드 "향후 전망" (`trend_forecast.py`) |
| Whisper large-v3-turbo CT2 (MIT) | `data/models/faster-whisper-large-v3-turbo` | ~1.5GB | 음성 입력 STT (`endpoints/stt.py`) |

**비대상**: `iforest_*.pkl`, `baseline_gbt_*` — 현장 데이터로 주1회 cron
재학습되는 모델 (`docs/operations/*-train-cron.md`). 초기엔 없어도 첫 학습
후 생성됨.

## 절차 (스크립트: `slm/tools/model_weights_bundle.sh`)

### 1) 개발 장비 — 번들 생성
```bash
cd ~/slm && tools/model_weights_bundle.sh pack /path/to/media
# → slm-model-weights-YYYYMMDD.tar.gz (+ .sha256) 생성, 매체에 2파일 복사
```
sha256 매니페스트(31개 파일)가 번들 안에 포함된다.

### 2) 납품 장비 — 설치
```bash
cd /opt/slm && tools/model_weights_bundle.sh install /media/slm-model-weights-*.tar.gz
# 번들 체크섬 → 압축 해제 → 파일별 sha256 전수 검증까지 자동
```

### 3) 검증
```bash
tools/model_weights_bundle.sh verify   # 파일 무결성 (exit 0/1)
```
런타임 확인 (백엔드 기동 후):
- STT: `curl -F 'audio=@sample.webm' http://localhost:8000/stt/transcribe` → text 반환
- Chronos: 트렌드 비교 "향후 전망" 응답의 `forecast.method == "chronos_bolt"`
  (`"linear"` 면 웨이트 미로드 — 백엔드 로그에서 로드 실패 사유 확인)

## 주의
- Docker 배포 시 `data/models/` 가 컨테이너에 바인드되는 경로인지 확인
  (`docker-compose*.yml` volumes)
- 웨이트 갱신 납품 시 동일 절차로 재생성 — 매니페스트가 함께 갱신됨
- 납품 체크리스트의 다른 항목: dev_tag_ingest 제거, 스모크 3층 검수
