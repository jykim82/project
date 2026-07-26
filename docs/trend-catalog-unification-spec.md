# 트렌드 카탈로그 일원화 사양 v1

> 작성 2026-07-26 · Migration 0122 · Backend `endpoints/monitoring_catalogs.py`
> `canvas_crud.py` 전환 · 목적: **구축 편의성·직관성** — 캔버스/구축 UI 에서
> 만든 트렌드가 채팅·이상감지·모니터링 어디서나 통하는 단일 정본
> **v1 구현 완료 2026-07-26** — E2E: UI 로 남산10 소블록 '압력' 추가 →
> 채팅 "압력 트렌드" 미등록 에러 → 정상 조회 전환 검증. 기존 카드 9건
> 유지(동명 시드 병합), 시드 보호 가드, 스모크 16/16. 프런트 무변경.
> 유의: 채팅 트렌드명 추출은 표준 항목(수위/유량/압력 등) 기준 —
> 임의 명칭 카탈로그("합덕 종합")는 목록·화면 노출은 되나 자연어 추출은
> 표준 항목만 (후속 §7)

## 1. 배경 — 카탈로그 이원화

| | `tb_trend_catalog` (333행) | `tb_monitoring_catalog` (9행) |
|---|---|---|
| 소비 | **AI 채팅 트렌드 조회**(sql_executor)·이상감지(meta.monitoring)·대시보드 | 모니터링 화면 카드·캔버스 모니터링 탭 |
| 관리 UI | **없음** (구축 시드 스크립트) | MonitoringTrendDialog (구축 > 트렌드 구성 + 캔버스) |
| 구조 | trend_name + meta{items[]} | catalog_name + items[] + display_order |

→ UI 로 트렌드를 만들어도 채팅에서 조회 불가, 채팅용 정본은 스크립트
의존. 캔버스 좌표 일원화(canvas-editor-unification-spec)와 같은 유형의
이원화.

## 2. 확정 원칙

- **정본 = `tb_trend_catalog` 하나.** `tb_monitoring_catalog` 는 9행 이관 후
  `_legacy` rename (0122)
- 채팅·이상감지·대시보드 쿼리는 **무변경** (정본 테이블이 그대로이므로)
- **표시 플래그 분리**: 모니터링 화면은 큐레이션 화면 — 전 시설 333행이
  쏟아지면 안 됨. `meta.show_monitoring = true` 행만 모니터링
  화면(sites/catalogs 조회)에 노출. UI(다이얼로그/캔버스)에서 생성한 행은
  자동 true, 시드 행은 플래그 없음(비노출)
  - 주의: `meta.monitoring`(기존 — 이상감지 알람 한계 감시 플래그)과 별개.
    이름 혼동 금지
- API 응답 키는 기존 유지 (`catalog_id`/`catalog_name`/`items`/…) — 프런트
  소비처(MonitoringSetupPage·TagMappingTab·MonitoringFacilityPage) 무변경
- UI 편집(PUT)은 `meta` 를 **병합**해 items 외 키(monitoring, alarm_limits
  집계 등) 보존

## 3. Migration 0122

1. `trend_id` 시퀀스 부여 (기존 정적 max=730 → nextval 기본값)
2. `display_order integer NOT NULL DEFAULT 0` 컬럼 추가
3. `tb_monitoring_catalog` 9행 이관 — `meta = {items, show_monitoring:true}`,
   (sitename,facilitytype,trend_name) 동일 행 존재 시 스킵(멱등)
4. `tb_monitoring_catalog` → `_legacy` rename (관찰 후 DROP — 0120 과 동일
   절차)

롤백: rename 원복 + 이관 행 삭제(`meta ? 'show_monitoring'` 기준) + 컬럼
유지(무해).

## 4. 백엔드 전환

`endpoints/monitoring_catalogs.py` — 모든 CRUD 를 tb_trend_catalog 로:

- GET `/monitoring/catalogs` → `show_monitoring=true` 필터, trend_id AS
  catalog_id·trend_name AS catalog_name·meta->'items' AS items 별칭
- GET `/monitoring/catalogs/sites`·`/site-groups` → 동일 필터
- POST → trend_catalog INSERT (`meta={items, show_monitoring:true}`), 이름
  충돌 자동 접미사 로직 유지
- PUT → `meta = meta || {items}` 병합 (플래그·기존 키 보존)
- DELETE → trend_id 삭제. **시드 행 보호**: show_monitoring 없는 행은 UI
  목록에 안 나오므로 삭제 경로 없음 (직접 호출도 플래그 행만 허용)
- GET `/monitoring/catalogs/reference` (구 trend_catalog 참조) — 동일 테이블
  조회로 유지 (전체 행 대상 — "기존 트렌드 가져오기" 용)

`endpoints/canvas_crud.py` — 노드 카탈로그 카운트·node-detail 카탈로그
목록을 trend_catalog(show_monitoring 필터)로 전환.

## 5. 효과 (구축 관점)

1. 캔버스 모니터링 탭/구축 트렌드 구성에서 만든 트렌드가 **즉시 채팅에서
   조회 가능** — "남산 배수지 수위유량 트렌드 보여줘"
2. 채팅 "조회 가능 항목" 안내에 UI 생성 트렌드 포함 — 구축자가 채팅 조회
   범위를 UI 로 확장 가능 (스크립트 불요)
3. 정본 1개 — 납품 시드·검수·백업 대상 단일화

## 6. 검증

- 채팅 스모크 16/16 (트렌드 인텐트 무변경 확인)
- UI 로 새 트렌드 생성 → ① 모니터링 화면 카드 표시 ② **채팅에서 해당
  트렌드명 조회 성공** ③ 캔버스 모니터링 탭 카운트 반영
- 기존 모니터링 카드 9건 이관 후 화면 동일 표시
- 시드 333행 이 모니터링 화면에 노출되지 않음

## 7. 보류 (후속)

- 다이얼로그 "모니터링 화면 표시" 토글 (시드 행을 화면에 승격하는 UX)
- 시드 스크립트의 trend_catalog 생성분에 대한 UI 편집 승격 정책
- 임의 명칭 카탈로그의 채팅 자연어 추출 (인텐트 키워드에 카탈로그명 동적
  주입 — feedback_intent_category_keyword_sync 유의)
- `_legacy` DROP (다음 납품 점검 — 0120 tb_canvas_node_position_legacy 와 함께)
