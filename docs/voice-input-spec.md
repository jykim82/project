# 음성 입력 사양 v1 (로컬 Whisper STT)

현장 작업자가 **말로** 장애·조치를 기록하는 입력 확장 — E-025 플로우
(점검→장애관리→알람관리)의 마지막 UX 갭(현장 타이핑) 해소. 2026-07-16 구현.

## 원칙
- **폐쇄망**: 브라우저 Web Speech API(구글 서버 전송) 금지 — 서버 로컬 Whisper.
- **비중국 모델**: Whisper large-v3-turbo (OpenAI 계열, MIT), CT2 변환본.
- 웨이트 로컬 번들: `slm/data/models/faster-whisper-large-v3-turbo` (1.5GB,
  **git 제외** — 납품 시 오프라인 번들, Chronos 웨이트와 동일 절차).

## 구조
| 계층 | 파일 | 역할 |
|---|---|---|
| 백엔드 | `slm/endpoints/stt.py` | POST /stt/transcribe (multipart) → {text, ...}. lazy 싱글턴(로드 ~1.4s), CPU int8, 동기 def(threadpool) |
| 프런트 훅 | `src/hooks/use-voice-input.ts` | MediaRecorder(webm/opus) 녹음 + **VAD 자동 종료** → 프록시 POST → 텍스트 콜백 |
| UI | `ChatInput.tsx` 마이크 버튼 | idle=Mic / recording=빨강 펄스+Square / transcribing=스피너. 결과는 입력창에 이어붙임 (**자동 전송 안 함** — 사용자 확인 후 전송) |

## 자동 종료 (VAD — 2026-07-16 개선)
현장 취지상 "버튼 → 말하기 → 끝"이어야 함. 종료 버튼 재클릭 요구는 UX 위배
(사용자 피드백). `AnalyserNode` RMS 기반 무음 감지로 자동 정지:
- **발화 후 무음 2초** → 자동 정지·전사 (`SILENCE_STOP_MS`)
- **발화 전혀 없이 8초** → 자동 취소 (`NO_SPEECH_CANCEL_MS`)
- 하드 리밋 1분 유지. 수동 정지(버튼 재클릭)는 폴백으로 유지
- `AudioContext` 미지원 환경은 VAD 없이 수동 정지 모드로 폴백
- 임계 `VOICE_RMS_THRESHOLD=0.02` (정규화 RMS) — 현장 소음 환경에서
  오탐 시 조정 포인트

## 도메인 용어 바이어스 (핵심)
Whisper `initial_prompt` 에 상수도 용어 사전 주입 — 프롬프트 없이는
"탁도계 수리" → "학도 개수리" 오인식. 주입 후 테스트 3/3 정확:

### 동적 시설명 주입 (2026-07-16 개선)
고정 용어에 더해 **DB 시설명을 동적으로 결합** — 고객사가 바뀌어도 코드
수정 없이 현장 고유명사("기지시", "소난지도" 등) 인식:
- 소스: `tb_equipment_info.sitename` DISTINCT + `tb_facility_alias`(use_yn='Y')
- 1시간 캐시 (`_PROMPT_TTL_S`), DB 실패 시 base 프롬프트 폴백
- Whisper `initial_prompt` 는 **224 토큰 초과 시 앞이 잘림** → 검증된 핵심
  용어를 문장 '뒤'에 배치, 시설명 블록은 250자 상한 (dev 기준 80개 중
  48개 포함 — 짧은 이름 우선이 아닌 가나다순이므로 필요 시 상한 조정)
- 검증: "기지시 가압장 유량계 점검 완료했습니다" / "소난지도 배수지 탁도계
  수리 완료" 2/2 정확 (~4~5s)
- "행정 배수지 센서 이상 스캔해줘" ✓ / "신평 배수지 판넬 전원 이상 발생했어.
  사진 첨부할게" ✓ / "죽동 배수지 탁도계 수리 완료했습니다" ✓
- 실측: 발화(4~9s)당 ~3.4s 처리 (CPU, beam_size=5, VAD)

## 검증 (2026-07-16)
- 백엔드 직접: 한국어 3발화 정확 전사 / API multipart 200
- 전체 경로: 브라우저(인증 세션)→프록시(멀티파트 arrayBuffer)→Whisper 200,
  비인증 curl 거부(인증 게이트 정상), 무음 → text=""(VAD)
- 마이크 버튼 렌더 + 스모크 16/16

## 부수 수정
`scripts/watch-frontend-prod.sh` 버그 — 빌드 직후 sig 를 최신으로 갱신해
**빌드 중 변경이 유실**되던 문제 (이번에 실제 재현: 마이크 버튼이 클라이언트
청크에 미포함). 빌드 '시작 시점' sig 를 기록하도록 수정 — 빌드 중 변경은
다음 루프에서 재빌드.

## 후속 후보
- 장애 기록 카드(FaultRecordConfirmCard) 안에서 음성으로 상세 덧붙이기
- 모바일 PWA 화면에서 마이크 우선 배치 (현장 모드)
