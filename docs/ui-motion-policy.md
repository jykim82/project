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
| 시작 진행 액션 버튼 (유휴→처리중→성공/오류) | `src/components/common/ActionStateButton.tsx` — CSS transition, 완료 후 1.8s 유휴 복귀 | 보고서 생성 (AddReportDialog) 시범 → 모델 학습·시뮬 실행·저장 버튼 확대 예정 |
| 테마 토글 원형 전환 | `src/lib/utils/theme-transition.ts` — View Transitions API + flushSync, 미지원/reduce-motion 즉시 전환 폴백 | AppHeader·AppTopbar 테마 버튼 |
| 우측 패널·팝업 순차 등장 | `globals.css` `.slm-stagger-in` (자식 스태거) + 기존 animate-in idiom | 네트워크 인스펙터 (누락분 보강), 일정 알림 팝업, 위기대응 모달 카드 |

## 배제 (사유 기록 — 재검토 시 참조)

- **Animated Beam** — 계통도 자체 흐름 애니메이션과 중복, 대량 노드 성능
  부담, motion 의존.
- **Terminal 타이핑** — 제품 톤 불일치.
- **motion 버튼 모음 / Family Dialog 모핑** — motion 라이브러리 필수 (원칙 1).
- **GSAP** — 애니메이션 라이브러리 이중화. 효과(우측 순차 등장)만 CSS 로 차용.

## ActionStateButton 사용 규칙

- `onAction` 은 실패 시 **throw** 해야 오류 상태가 표시된다 (내부 catch 후
  안내만 하고 정상 반환하면 성공으로 표시됨 — 주의).
- 성공/오류 표시는 `revertMs`(기본 1800ms) 후 자동 유휴 복귀 — 연속 작업 차단
  금지.
- 스피너는 `.slm-live-spin` (reduce-motion 예외) — `animate-spin` 사용 금지.
