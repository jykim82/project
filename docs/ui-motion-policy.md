# UI 모션 정책 (2026-07-21)

magicui / motion.dev / GSAP 데모 7건 검토 결과와 채택 기준. 이후 모션 관련
제안·구현은 본 정책을 우선 적용한다.

## 원칙

1. **신규 애니메이션 라이브러리 도입 금지** — motion(framer-motion)·GSAP 추가
   없이 CSS transition/keyframes + 브라우저 네이티브 API 로만 구현.
2. **정보성 > 장식성** — 상태를 전달하는 모션(진행/성공/실패)을 우선 채택.
   개발자 랜딩 감성(터미널 타이핑 등)은 B2B 운영 제품 톤과 맞지 않아 배제.
3. **reduce-motion 구분** — 상태 표시성(스피너 등)은 `.slm-live-spin` 계열
   예외로 복원, 장식성(등장·전환)은 전역 freeze 존중 (즉시 최종 상태).
4. 단일 요소 우측 등장은 기존 tailwind idiom
   `animate-in slide-in-from-right-8 fade-in duration-300` 로 통일.

## 채택 (2026-07-21 구현)

| 효과 | 구현 | 적용처 |
|------|------|--------|
| 시작 진행 액션 버튼 (유휴→처리중→성공/오류) | `src/components/common/ActionStateButton.tsx` — CSS transition, 완료 후 1.8s 유휴 복귀. `onAction` 이 `false` 반환 시 무동작(검증 실패), throw 시 오류 상태 | 보고서 생성·항목 직접 입력, 메모/일정 저장, 작업(알람 제어) 등록, EPANET SHP 스캔·INP 생성 (2026-07-22 일괄 확대) |
| 테마 토글 원형 전환 | `src/lib/utils/theme-transition.ts` `toggleThemeWithCircle` — View Transitions API. **html class 를 스냅샷 콜백 안에서 직접 동기 토글** (next-themes 의 useEffect 반영은 비동기라 스냅샷 전후가 같아져 번짐이 안 보임 — E-045). resolvedTheme 기준이라 system 상태 첫 클릭도 즉시 전환. 미지원/reduce-motion 즉시 전환 폴백 | AppHeader·AppTopbar 테마 버튼 |
| 우측 패널·팝업 순차 등장 | `globals.css` `.slm-stagger-in` (자식 스태거) + animate-in idiom. **강도 기준: `slide-in-from-right-full duration-400 ease-out`** — 32px/300ms 는 패널 폭(360px) 레이아웃 변화에 묻혀 체감 불가 판정 (2026-07-21 사용자 피드백) | GIS·유량·네트워크 인스펙터 (시설 전환 시 key 리마운트로 재생), 일정 알림 팝업, 위기대응 모달 카드 |

## 배제 (사유 기록 — 재검토 시 참조)

- **Animated Beam** — 계통도 자체 흐름 애니메이션과 중복, 대량 노드 성능
  부담, motion 의존.
- **Terminal 타이핑** — 제품 톤 불일치.
- **motion 버튼 모음 / Family Dialog 모핑** — motion 라이브러리 필수 (원칙 1).
- **GSAP** — 애니메이션 라이브러리 이중화. 효과(우측 순차 등장)만 CSS 로 차용.

## 검증 노트

- **체감 기준으로 파라미터를 정한다** — "애니메이션이 실행된다" ≠ "보인다".
  구석에서 시작하는 원형 전환 450ms, 큰 레이아웃 변화 옆 32px 슬라이드는
  실행돼도 체감 0 (2026-07-21 실사용 피드백으로 각각 700ms ease-out /
  전체 폭 슬라이드로 상향).
- 자동화(Playwright MCP) Chromium 은 **View Transition 스냅샷을 화면에
  합성하지 못한다** — VT 시각 검증은 실브라우저 육안으로만 가능. 자동화로는
  파이프라인(ready·pseudo·주입 애니메이션 파라미터)까지만 검증하고 한계를
  보고에 명시할 것. CSS 애니메이션(tw-animate 등)은 자동화에서도 정상
  렌더링되므로 pause 후 스크린샷으로 시각 검증 가능.

## ActionStateButton 사용 규칙

- `onAction` 은 실패 시 **throw** 해야 오류 상태가 표시된다 (내부 catch 후
  안내만 하고 정상 반환하면 성공으로 표시됨 — 주의).
- 성공/오류 표시는 `revertMs`(기본 1800ms) 후 자동 유휴 복귀 — 연속 작업 차단
  금지.
- 스피너는 `.slm-live-spin` (reduce-motion 예외) — `animate-spin` 사용 금지.
