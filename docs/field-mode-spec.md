# 현장 모드 사양 v2 (알람 중심 현장 작업 흐름)

`/field` — 현장 작업자가 폰 한 손으로 대응 업무를 처리하는 페이지.
v1(범용 런처)은 사용자 피드백으로 **알람 중심 시나리오**로 재구성 (2026-07-16):

> 알람으로 이상 인지 (예: 신평 배수지 가압펌프) → 현장 출동 → 사진으로
> 고장 확인/진단 → 매뉴얼 참조 → 고장 기록(조치 전 사진) → 조치 →
> 조치 완료 기록(조치 후 사진)

## 원칙
- **처리는 전부 기존 채팅 플로우 재사용** — 이 페이지는 시나리오 각 단계를
  1탭으로 여는 런처. 진단/기록/조치 로직 중복 금지
- 모바일 우선(max-w-md 중앙), 데스크톱에서도 동작. 새 백엔드 없음

## 구성 (`src/app/(dashboard)/field/page.tsx`)

### ① 대응 필요 알람 (최상단 — 출동의 출발점)
`fetchAlarmReports({alarmStatus:"진행중"})` 최근 7일 Top 5. 심각도 배지
(경고 red / 주의 amber). **알람 탭 → 확장 액션 2×2**:
| 액션 | 동작 (시나리오 단계) |
|---|---|
| 📷 사진 찍고 진단 | 카메라 → `/chat?fieldPhoto=1&sitename&facilitytype&prefill=진단해줘` — 고장 여부 확인. site 컨텍스트는 멀티모달에 자동 첨부 (E-025 P11) |
| 📝 사진 찍고 고장 기록 | 카메라 → prefill `"{시설} {설비} 고장 기록해줘. "` — **조치 전 사진** 이 draft 에 첨부 (사진 시나리오 P2) |
| 📖 초동대응 매뉴얼 | prefill `"{시설명} {시설유형} 초동대응 매뉴얼은?"` (기존 *_INITIAL_RESPONSE_MANUAL 인텐트) |
| ✅ 조치 완료 기록 | prefill `"{시설} {설비} 조치 완료 기록해줘. "` — 조치 후 사진은 채팅에서 첨부 |

### ② 진행중 장애 (조치 완료 대기)
`fetchHealthTasks({status:"진행중"})` Top 5. 행별 **"📷 조치 완료" 버튼** —
카메라(조치 후 사진) → `/chat?fieldPhoto=1&prefill="… 조치 완료 기록해줘. "`

### ③ 자유 입력 (하단 소형 3버튼)
사진 진단 / 음성 기록(`useVoiceInput`, VAD 자동 종료 → prefill) / 채팅 질의
— 알람 없이도 사용 가능. **자동 전송 없음** (전송은 항상 사용자 확인).

## 사진 핸드오프 (`src/lib/field-handoff.ts`)
File 객체는 URL 로 전달 불가 → 모듈 싱글턴 stash/take (1회 소비).
- 소비: `chat/page.tsx` 마운트 효과가 `?fieldPhoto=1` 감지 →
  `addFiles(files)` + `setText(prefill || "진단해줘")` +
  sitename/facilitytype 는 `deepLinkSite` 로 (P11 과 동일 경로)
- `ChatInput` 에 `node.addFiles` 노출 (setText 와 동일 DOM 핸들 패턴)
- 새로고침 시 파일 소실 → 파라미터 조용히 무시 (안전 폴백)
- 페이지 내 카메라 input 은 1개 공용 — 액션별 목적지는 `cameraDestRef`

## 메뉴 / 배너
- `sidebar-menus.ts` M009 "현장 모드" (HardHat) + Migration 0099 (권한 M002 동일)
- `FieldModeBanner` — 모바일 뷰포트 하단 고정 전환 제안, sessionStorage
  dismiss, pathname ∉ {/field, /chat}. `(dashboard)/layout.tsx` 마운트

## 향후 후보 (미착수)
- PWA (manifest·홈화면 설치) — 사용자 결정 시
- 담당 시설 즐겨찾기 / 알람 심각도 필터
- 알람 확장 액션에서 해당 설비 최근 트렌드 미니 차트
