# 구축 완결성 검수 사양 v1 — 구축 고도화 ③

> **상태:** v1 구현 완료 (2026-07-24)
> **목적:** 흩어져 있던 검수 자산을 **한 화면(`/setup/audit`, M200-21)** 으로
> 집계 — 신규 고객 구축과 납품 검수의 단일 진입점. "구축이 끝났다"를
> 감이 아니라 검사 결과로 판정한다.
> **관련:** `docs/operations/delivery-checklist.md`(납품 절차),
> `docs/alarm-threshold-coverage.md`, `docs/datainfo-conversion-rule-spec.md`,
> `docs/flow-diagram-engineering-spec.md`, `docs/tag-quality-layer-spec.md`

---

## 1. 검사 6종 (`GET /setup/audit` — endpoints/setup_audit.py)

| key | 검사 | 등급 규칙 | 근거 사례 |
|---|---|---|---|
| base_info | 태그에 있는데 시설 기초정보(배수지/가압장/블록) 미등록 사이트 | 0=pass, 1+=**warn** (자동완성·조회 누락 직결) | 수청2지구2 (2026-07-24) |
| threshold | 압력·수위 계측 보유 시설의 SCADA 임계(H/HH·L/LL) 커버리지 | 부재=**info** (SCADA 측 협의 사항 — 통계 감시는 동작) | 성상1 포화 무경보 |
| datainfo | datadesc→datainfo 룰 재현율 + 미커버(diff) 수 | ≥95%=pass, 미만=**info** (수동/override 확인 대상) | 구축 고도화 ① |
| diagram | 관계↔노드 배치↔순환 정합 (flow-diagram lint 축약) | 0=pass, 1+=**warn** | 구축 고도화 ② |
| quality | tb_tag_quality 현황 (무응답·두절·포화·고착·DI) | 0=pass, 1+=**warn** (구축 직후 무응답=결선 미완 신호) | 삼화·성북1리 두절 |
| epanet | 관계 시설 중 EPANET 유량 매핑 부재 | 부재=**info** (B 기능군 사용 시만 해당) | B-1 주입 |

- 응답: `{summary: {pass,warn,info,total}, checks: [{key,title,status,count,items(top15),guide}]}`
- 등급 철학: **warn** = 제품 동작에 실누락 발생 (구축자가 반드시 조치) /
  **info** = 발주처·현장 협의 또는 선택 기능 사항

## 2. 화면 (`/setup/audit`)

- 상단: 통과/조치/참고 집계 + 재검사 버튼
- 검사별 카드: 상태 아이콘·건수 칩·가이드 문구 + 펼치면 상세 목록(top 15)
  + **관련 화면 딥링크** (시설정보 구축 / DATAINFO 변환룰 / 계통도 설정 / EPANET)
- 메뉴: 구축 > 구축 완결성 검수 (M200-21, sidebar+tb_menu+auth 등록)

## 3. 납품 검수 연계

`delivery-checklist.md` 검수 단계에서 본 화면 1회 실행 —
**warn 0 이 인수 기준**, info 항목은 발주처 결정 기록으로 남긴다.

## 4. 첫 실행 결과 (2026-07-24, dev)

pass 1 · warn 2 · info 3 — **즉시 신규 발견: 가압장 기초정보 미등록 11곳**
(난지·삼봉 등 — 수청 사례의 가압장 버전. 태그는 있는데
tb_service_booster_station_info 미등록). 품질 148건은 두절 2곳 포함
기존 파악분.

## 변경 이력
- 2026-07-24 v1 — 검사 6종 + 화면 + 메뉴. 첫 실행에서 기초정보 공백 11곳 검출
