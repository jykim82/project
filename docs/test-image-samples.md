# 테스트 이미지 샘플 경로 (E-025 멀티모달 검증용)

E-025 멀티모달 현장 진단 파이프라인(VLM 진단, 명판 OCR, 매뉴얼 RAG) 검증에 사용하는 **canonical** 이미지 위치와 용도.

## Canonical 경로

**`docs/매뉴얼/plc 사진/`** — 실제 현장 PLC 사진 모음. 이 디렉터리 안의 이미지만 공식 회귀 테스트에 사용한다.

| 파일 | 용도 | 비고 |
|---|---|---|
| `xgk plc cpue.jpeg` | XGK-CPUE 실사 (여러 LED 점등 상태) | 정식 E2E 기준 이미지 |

## 공식 컨테이너 경로

검증 스크립트에서 호출할 때는 다음 경로를 사용:

```bash
# 호스트 → 컨테이너 복사 (최초 1회)
docker cp 'docs/매뉴얼/plc 사진/xgk plc cpue.jpeg' slm-backend:/tmp/xgk_cpue_real.jpeg

# vision_agent /vision/diagnose 호출 시 image_url
/tmp/xgk_cpue_real.jpeg
```

## 비-canonical (레거시)

다음 위치의 샘플은 **사용 금지**. 역사적 이유로 남아 있으나 새 테스트에서 참조하지 말 것.

- `.playwright-mcp/ls_xgk_error.jpg` — Playwright MCP가 업로드할 때 로컬 호스트 경로로 사용 가능하지만, 파일명이 실제 내용(XGK가 아닌 XGP+XGK 혼합)을 정확히 반영하지 않음
- `.playwright-mcp/fake_ls_plc.jpg` — 초기 테스트용 모조 이미지
- `slm/test_images/fake_ls_plc.jpg` — 동일한 초기 모조 이미지, 백엔드 기본 테스트용
- `/tmp/ls_xgk_error.jpg` — 컨테이너 내 레거시 임시 파일

Playwright E2E에서 업로드할 때는 `.playwright-mcp/` 경로가 필요하지만, 내용은 canonical 경로의 파일과 동일해야 한다. 향후 새 샘플 추가 시 **반드시 `docs/매뉴얼/plc 사진/`에 먼저 넣은 후** `.playwright-mcp/`로 복사.

## 추가 테스트 이미지가 필요할 때

새 장비 유형(유량계 계기판, 모뎀, RTU 등) 테스트용 이미지가 생기면:

1. `docs/매뉴얼/<장비유형> 사진/` 하위에 원본 추가
2. 파일명은 영문 소문자 + 하이픈 권장 (예: `flow-meter-panasonic-display.jpeg`)
3. `docs/매뉴얼/plc 사진/` 구조를 따름
4. 이 문서에 `용도` + 비고 업데이트
