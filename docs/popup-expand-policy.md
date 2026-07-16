# 팝업 전체화면(크게보기) 정책

모든 팝업은 우상단 "크게보기" 토글로 near-fullscreen(96vw×94vh) 전환을
기본 제공한다 (2026-07-16, 사용자 요청 "모든 팝업에서 전체화면 모드").

## 구현
- `src/components/ui/dialog.tsx` `DialogContent` — `expandable` 기본 **true**
- 확장 시 호출자 `className` 을 **유지한 채** 크기만 `!important` 로 덮어씀
  (tailwind-merge 뒤 클래스 우선) — `overflow-y-auto` 등 호출자 레이아웃 보존
- `expandedClassName` 으로 확장 시 추가 클래스 지정 가능 (flex 전환 등)
- 닫기 버튼 없는 팝업(`showCloseButton={false}`)은 확장 버튼이 `right-4` 로 이동
- 무의미한 팝업만 `expandable={false}` 로 opt-out (현재 opt-out 없음)
- 커스텀 오버레이(PhotoLightbox)는 자체 전체화면형 — 대상 아님

## 설계 결정 — 전체화면 오버레이 유지 (사이드바 제외 확장 안 함)
사용자 검토 질문("내비 바 없는 부분까지만 넓히는 게 낫나")에 대한 결정:
1. 모달은 배경 조작 불가 — 보이기만 하는 내비는 혼란만 유발
2. 레이아웃이 사이드바↔탑바 전환형(Tweaks)이라 "내비 제외 확장"은 모드마다
   크기가 달라져 일관성 없음
3. 확장 목적 = 정보 표시량 최대화 → 96vw 가 항상 더 넓음

"팝업 내용을 보면서 다른 메뉴 이동" 니즈는 확장이 아니라 **"페이지로 열기"
링크**(비모달 딥링크)로 해결한다 — 필요한 팝업에 개별 추가.
