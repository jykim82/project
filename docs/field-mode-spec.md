# 현장 모드 사양 v1 (모바일 우선 현장 작업자 홈)

`/field` — 현장 작업자가 폰 한 손으로 진단·기록을 시작하는 **런처 페이지**
(2026-07-16, 백로그 "모바일 현장 모드" — 전용 페이지 범위로 사용자 승인).

## 원칙
- **처리는 전부 기존 채팅 플로우 재사용** — 이 페이지는 진입을 1탭으로
  줄이는 역할만. 진단/기록/조치 로직 중복 금지
- 모바일 우선(max-w-md 중앙), 데스크톱에서도 동작
- 새 백엔드 없음 (기존 API 재사용)

## 구성 (`src/app/(dashboard)/field/page.tsx`)
| 요소 | 동작 |
|---|---|
| 📷 사진 진단 (대형) | 네이티브 카메라(capture=environment, multiple) → `stashFieldPhotos()` → `/chat?fieldPhoto=1` 이동 → 채팅이 첨부+`"진단해줘"` 프리필 |
| 🎤 음성 기록 (대형) | `useVoiceInput` 재사용 (VAD 자동 종료) → 전사 텍스트를 `/chat?prefill=<text>` 로 — **자동 전송 안 함** (voice-input-spec 원칙) |
| 진행중 장애 목록 | `fetchHealthTasks({status:"진행중"})` Top 5. 탭 → `/chat?prefill="{시설} {설비} 조치 완료 기록해줘. "` |
| 채팅으로 질의하기 | `/chat` 이동 |

## 사진 핸드오프 (`src/lib/field-handoff.ts`)
File 객체는 URL 로 전달 불가 → 모듈 싱글턴 stash/take (1회 소비).
- 소비: `chat/page.tsx` 마운트 효과가 `?fieldPhoto=1` 감지 →
  `[data-chat-input].addFiles(files)` + `setText("진단해줘")` (전송은 사용자)
- `ChatInput` 에 `node.addFiles` 노출 (setText 와 동일한 DOM 핸들 패턴)
- 새로고침 시 파일 소실 → 파라미터 조용히 무시 (안전 폴백)

## 메뉴
- `sidebar-menus.ts` M009 "현장 모드" (HardHat 아이콘) + Migration 0099
  (`tb_menu` M009 idx 8, 권한은 AI 채팅 M002 와 동일 — 전 사용자)

## 향후 후보 (미착수)
- 모바일 접속 감지 시 시작 페이지에서 현장 모드 배너 제안
- PWA (manifest·홈화면 설치) — 사용자 결정 시
- 담당 시설 즐겨찾기 바로가기
