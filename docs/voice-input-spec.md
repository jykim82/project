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
| 프런트 훅 | `src/hooks/use-voice-input.ts` | MediaRecorder(webm/opus) 토글 녹음(최대 1분) → 프록시 POST → 텍스트 콜백 |
| UI | `ChatInput.tsx` 마이크 버튼 | idle=Mic / recording=빨강 펄스+Square / transcribing=스피너. 결과는 입력창에 이어붙임 (**자동 전송 안 함** — 사용자 확인 후 전송) |

## 도메인 용어 바이어스 (핵심)
Whisper `initial_prompt` 에 상수도 용어 사전 주입 — 프롬프트 없이는
"탁도계 수리" → "학도 개수리" 오인식. 주입 후 테스트 3/3 정확:
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
