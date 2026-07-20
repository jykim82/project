# 사용자 간 실시간 통신 (채팅·영상·음성) 사양 — 확정판 v2

> 2026-07-19~20 구현 완료. 폐쇄망 온프레미스 제품 전제.
> **P1~P4 전 단계 구현·실기기 검증 완료** (LTE↔PC 외부망 실통화 성공).

## 0. 확정 요약 (2026-07-20)

| 단계 | 기능 | 상태 | Migration |
|---|---|---|---|
| P1 | 운영자 텍스트 채팅 (전체 채널+1:1 DM, unread, 도착 토스트) | ✅ 완료 | 0106 |
| P2 | 사진·영상·음성메시지 파일 전송 (Range 서빙, 말풍선 없는 미디어) | ✅ 완료 | 0107 |
| P3 | 1:1 음성 통화 (REST 시그널링+WebRTC P2P, 연결음, 유령세션 방어) | ✅ 완료 | 0108·0111 |
| P4 | 1:1 영상 통화 (원격+PiP, 카메라 전후면 전환, 무카메라 폴백) | ✅ 완료 | 0110 |
| 옵션 | 외부망 통화 TURN (coturn, env+사이트설정 이중 토글, 포트포워딩) | ✅ 개통 | 0109 |
| 검토 | 다중(3인+) 음성 통화 | §7 검토 | - |

핵심 설계: wss 없이 **REST 폴링 시그널링 + non-trickle ICE**. 미디어는
같은 망 P2P 직결, 외부망은 coturn 릴레이. 모바일 1열 메신저. 상세는 §5.

## 1. 요구 요약

1. **채팅**: 운영자 간 1:1/그룹 텍스트 대화 (기존 AI 채팅과 별개)
2. **영상 전송**: 현장 영상 공유
3. **음성 대화**: 음성으로 대화

## 2. 폐쇄망 제약에서의 기술 선택지

| 기능 | 권장 기술 | 폐쇄망 적합성 |
|---|---|---|
| 텍스트 채팅 | **WebSocket** (FastAPI 네이티브 지원) + DB 영속 | ◎ 외부 의존 0 |
| 사진·영상 파일 전송 | 기존 파일 업로드 인프라 재사용 (`tb_file_storage` + `/data/files`) | ◎ 검증된 경로 |
| 실시간 영상/음성 통화 | **WebRTC** — LAN 은 host candidate 로 P2P 직결, 서브넷 분리 시 자체 TURN(coturn) 온프레 설치 | ○ 시그널링 서버 자체 호스팅 필요 |
| 음성 메시지(녹음 전송) | MediaRecorder 녹음 → 파일 업로드 (통화보다 단순) | ◎ |

- 외부 STUN/TURN(구글 등)·클라우드 미디어 서버 사용 불가 → LAN 직결 우선,
  TURN 필요 시 coturn 을 Docker Compose 스택에 포함.
- HTTPS 필수 (getUserMedia 는 secure context 전용) — 기존 Caddy TLS 충족.

## 3. 기존 자산 재사용

- **AI 채팅 UI** (`src/components/chat/*`): 말풍선·첨부·이미지 뷰어 컴포넌트
  상당수 재사용 가능. 단 대화 상대 모델(사용자↔사용자)·읽음 표시는 신규.
- **음성 입력** (voice-input-spec, 로컬 Whisper): 음성 메시지 → 텍스트 자동
  변환(부가 기능)으로 재사용 여지.
- **파일 저장** (`files/chat_attachments/`): 사용자 채팅 첨부에 그대로 확장.
- **현장 모드** (/field): 현장↔상황실 커뮤니케이션이 1차 사용 시나리오 —
  현장 모드 진입점에 통합하는 것이 자연스러움.

## 4. 단계 제안

| 단계 | 내용 | 난이도 | 비고 |
|---|---|---|---|
| P1 | 사용자 간 텍스트 채팅 (1:1 + 전체 채널) — WebSocket + tb_user_chat 영속 + 미접속자 재접속 시 백로그 | 중 | 알림은 폴링/뱃지 |
| P2 | 사진·영상·음성메시지 **파일 전송** (녹화 업로드, 스트리밍 아님) | 하 | 기존 업로드 재사용 |
| P3 | 실시간 음성 통화 (WebRTC 1:1) | 상 | 시그널링=기존 WS 재사용, LAN 직결 |
| P4 | 실시간 영상 통화 / 화면 공유 | 상 | P3 확장 |

## 5. 확정 결정 (2026-07-19)

1. 영상 전송 = **파일 공유**(P2) 우선, 실시간 스트리밍(P4)은 이후 확장
2. 음성 대화 = **운영자 간 통화**(P3)
3. 규모 = **1:1** (SFU 불필요, LAN P2P 로 충분)
4. **P1 → P2 → P3 → P4 순차 진행**

## 5.1 P1 구현 (운영자 채팅 — Migration 0106)

- **전송 방식: REST + 짧은 폴링(3s)** — WebSocket 이 아닌 이유:
  ① dev/prod 프런트가 HTTPS 라 `ws://backend` 는 혼합 콘텐츠로 차단,
  ② Next.js `/api/proxy` 는 WebSocket 프록시 불가.
  wss 는 Caddy TLS 종단 뒤에서만 가능 → **P3 통화 시그널링 때 wss 도입**
  (통화에는 필수), 그때 채팅도 WS 로 승격 검토.
- DB: `tb_user_chat_message` (region, msg_idn, room_id, sender_id, content,
  created_at, use_yn) + `tb_user_chat_read` (region, room_id, user_id,
  last_read_idn) — 읽음/미읽음 뱃지.
- room_id 규칙: 1:1 = `dm:<userA>|<userB>` (user_id 정렬), 전체 채널 = `all`.
  서버가 요청자의 방 멤버십 검증 (dm 참여자 또는 all 만 접근).
- API `endpoints/user_chat.py` — prefix `/userchat`:
  users(대화 상대 목록) / rooms(방 목록+마지막 메시지+unread) /
  messages(초기 50건 + after_idn 증분 폴링) / send / read(읽음 처리)
- UI `/messenger` (메뉴 M010 메신저): 좌측 전체 채널+DM 목록(unread 뱃지)
  + 사용자 목록에서 새 대화, 우측 스레드 + 입력. 활성 방 3s 폴링, 방 목록 15s.
- 메시지는 소프트 삭제 예약(use_yn) — P1 UI 는 삭제 미제공.

## 5.2 P2 구현 (사진·영상·음성메시지 첨부 — Migration 0107)

- `tb_user_chat_message` 에 attach_url/attach_type(image|video|audio)/attach_name.
- 업로드 `POST /userchat/upload` (multipart) — 유형별 정책:
  이미지 jpg/jpeg/png/webp/gif ≤10MB · 영상 mp4/webm/mov ≤100MB ·
  음성 webm/m4a/mp3/wav/ogg ≤20MB. 저장 `files/messenger/` (uuid 파일명),
  URL `/api/files/messenger/<name>`.
- 파일 서빙 라우트에 영상/음성 MIME + **Range 응답(206)** 추가 — <video>
  탐색(seek) 지원.
- UI: 입력줄 📎(사진·영상 파일) + 🎤(MediaRecorder 음성 녹음 → 정지 시
  업로드·전송). 렌더: 이미지 미리보기(클릭 원본), <video controls>,
  <audio controls>. content 또는 첨부 중 하나 필수 (서버 검증).

## 5.3 새 메시지 도착 전역 알림 (P2 부속)

- `MessengerNotifier` — (dashboard) 레이아웃 전역, 10s 폴링.
  방 last_at 전진 + unread>0 → sonner 토스트 (발신 방·미리보기 +
  "메신저 열기" 액션). 메신저 화면에서는 억제, 첫 폴링은 기준선만 기록
  (로그인 직후 기존 미읽음 폭주 방지). 같은 방 연속 도착은 토스트 갱신(id 고정).

## 5.4 P3 구현 (운영자 1:1 음성 통화 — Migration 0108)

- **시그널링: REST 폴링 + non-trickle ICE** — wss 없이 기존 HTTPS/프록시
  인프라 그대로. offer/answer SDP 를 ICE gathering 완료(상한 3s) 후 통째로
  교환. 미디어는 WebRTC LAN P2P 직결 (DTLS-SRTP 내장 암호화, STUN/TURN 미사용
  — 폐쇄망 host candidate 만).
- `tb_call_session`: ringing → accepted/rejected/canceled/missed → ended.
  통화 이력 겸용. ringing 60s 경과 시 조회 시점 missed 정리.
- API `/call`: invite(중복 통화 409) / incoming(수신 폴링 4s) / answer /
  reject / cancel / end / status(발신 응답 대기 1.5s·통화 중 3s 폴링,
  당사자 검증 403).
- UI: DM 헤더 📞 발신(call-store 경유) → `VoiceCallManager` 전역
  (수신 벨 모달 수락/거절 + 발신 대기·통화 중 플로팅 바: 경과시간·음소거·종료).
  P2P 연결 끊김(connectionState) 시 자동 종료 처리.
- **검증 범위**: 시그널링 상태기계 전이는 API 로 전 경로 검증. 실제 양방향
  오디오는 단일 브라우저 환경에서 불가 — **LAN 내 2대(각자 로그인, 마이크
  권한 허용)로 인수 테스트 필요**. HTTPS 필수 (getUserMedia secure context).
- 알려진 한계(P4 후보): 벨소리 사운드 없음(시각 알림만), 다자 통화 미지원,
  통화 이력 조회 화면 없음(DB 만 축적).
- **무음 트러블슈팅 (2026-07-20 실통화 피드백 반영)**:
  ① "통화 중" 표시는 SDP 교환 완료일 뿐 — 실제 미디어는 P2P 성립
  (`connectionState=connected`) 이후. 플로팅 바가 "음성 연결 중…" 에서
  시간 표시로 바뀌어야 정상. 8초 이상 미연결 시 안내 토스트.
  ② **두 기기가 같은 네트워크(Wi-Fi/LAN)여야 함** — STUN/TURN 미사용이라
  LTE↔LAN 등 다른 망 간에는 P2P 불가(무음). 모바일은 사내 Wi-Fi 접속 필수.
  ③ 브라우저 자동재생 차단 시 "소리 켜기" 토스트로 사용자 제스처 해제.
  ④ 외부망 통화가 요구되면 coturn(자체 TURN) 온프레 추가 검토 — P4 항목.
- 모바일 레이아웃: lg 미만 1열 전환 (목록 ↔ 스레드 + 뒤로가기) — 사이드
  목록이 채팅을 가리던 문제.

## 5.5 외부망 통화 옵션 — coturn TURN 릴레이 (2026-07-20)

- **목적**: 서로 다른 망(LTE↔LAN 등) 간 통화. LAN 내 통화는 P2P 직결이라
  불필요 — **납품 기본 비활성** (`TURN_ENABLED=0`, compose profile "turn").
- 구성: `docker-compose.dev.yml` coturn 서비스 (UDP/TCP 3478 + 릴레이
  49160-49200/udp, use-auth-secret). `.env`: TURN_ENABLED / TURN_HOST /
  TURN_SECRET / TURN_EXTERNAL_IP.
- 자격증명: `GET /call/turn-credentials` — coturn 규격 시간제한 HMAC-SHA1
  (username=만료epoch:user, TTL 1h). 비활성 시 `{enabled:false}` → 프런트
  LAN P2P 폴백. 프런트는 TTL/2 캐시, TURN 시 gathering 상한 3s→5s.
- **활성화 절차**: ① .env 4개 값 설정 ② `docker compose --profile turn up -d
  coturn` + backend 재기동 ③ 공유기 포트포워딩 **UDP 3478, UDP 49160-49200**
  → 서버(192.168.50.84) ④ 공인 IP 변경 시 TURN_EXTERNAL_IP 갱신 필요.
- **켜기/끄기 (운영 중)**: 관리 > 사이트 설정 > "외부망 통화 (TURN 릴레이)"
  토글 (Migration 0109, tb_comm_code CALL_TURN_ENABLED). 실동작 = env
  TURN_ENABLED(인프라) **AND** 사이트 설정 토글. 끄면 자격증명 발급 중단 →
  사내망 P2P 통화만 허용. 사내망 통화는 토글과 무관하게 항상 가능.
- 검증: 로컬 turnutils_uclient 로 백엔드 발급 자격증명 인증·릴레이 할당 성공.
  실 LTE↔LAN 통화는 포트포워딩 후 인수 테스트.
- 연결음: WebAudio 톤 생성 (음원 파일 없음). 발신 425Hz 링백 / 수신 740+880Hz
  벨. 끄기 토글(수신 모달·발신 바, localStorage 영속). 자동재생 정책으로
  첫 상호작용 전에는 무음일 수 있음 (시각 알림은 항상 표시).

## 5.6 P4 구현 (1:1 영상 통화 — Migration 0110)

- P3 구조 확장: `tb_call_session.call_type`(audio|video), invite 에 유형 전파.
- 발신: DM 헤더 📹. 수신 모달 "영상 통화 수신" 표기. 카메라 없는 기기는
  NotFoundError 폴백으로 **음성 자동 전환** (발신·수신 모두).
- UI: 우하단 영상 패널(원격 + 내 화면 PiP + 미연결 오버레이) + 플로팅 바에
  카메라 끄기 토글. 영상 통화는 <video> 가 오디오 포함 재생 (별도 audio
  element 미사용).
- 네트워크 요건은 음성과 동일 (같은 망 P2P 또는 TURN §5.5). 해상도 640×480
  ideal — LAN/릴레이 대역폭 고려.
- 비범위: 1:N 방송형 스트리밍·화면 공유·녹화 (요구 발생 시 별도 사양).

## 6. 리스크

- WebRTC 다자 통화는 폐쇄망에서도 가능하나 SFU(예: 자체 호스팅 mediasoup)
  운영 부담이 큼 — v1 은 1:1 한정 권고.
- 브라우저 마이크/카메라 권한은 HTTPS + 사용자 제스처 필요 — 현장 태블릿
  브라우저 정책 사전 확인 필요.
- 대용량 영상 파일은 `/data/files` 디스크 용량 정책(보존 기간·정리 배치)
  선행 정의 필요.

## 7. 다중(3인 이상) 음성 통화 검토 (2026-07-20 질의)

**가능 — 규모에 따라 두 방식.**

| 방식 | 구조 | 적정 인원 | 난이도 |
|---|---|---|---|
| **풀메시 P2P** | 참가자끼리 전원 1:1 연결 (N명 = 인당 N-1 스트림) | **3~4명** | 중 — 현 1:1 구조 확장 (방 개념 시그널링 + 다중 peer 관리) |
| **SFU 서버** (mediasoup/LiveKit 자체 호스팅) | 서버가 미디어 수신·분배 | 5명 이상~수십 | 상 — 미디어 서버 운영 부담, 온프레 가능 |

- 권장: **v1 은 풀메시 3~4인 회의 통화** — 현장 2~3명 + 상황실 시나리오에
  충분하고 서버 추가 없음(TURN 경유 시 릴레이 대역폭 N배 유의).
- 필요 확장: tb_call_session → 방(room) 모델, 참가자별 offer/answer 교환,
  통화 바에 참가자 목록·개별 음소거.
- 5인 이상 정례 회의가 요구되면 그때 SFU 도입 검토 (폐쇄망 자체 호스팅 가능).
- **착수는 사용자 지시 대기.**
