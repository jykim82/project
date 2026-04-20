-- 0052_network_link_bidirectional.sql
-- 네트워크 링크에 일방향(단방향) 경계 모델링 추가.
--
-- 배경: 제어망 → 업무망 경계는 물리적으로 "망간자료 전송장치"(데이터
-- 다이오드) 를 거치는 단방향 구간이라 역방향 ping/SNMP 체크가 불가능.
-- 기존 시드에서 이 사실이 표현되지 않아 경로 추적 함수가 업무망 장비
-- (WEB 서버, 관망관리 PC 등)까지 체크하려 했고, 당연히 "이상"으로
-- 잘못 표시되었다.
--
-- 수정:
--   (A) 잘못된 UTM → 업무망 직결 링크 삭제 (물리 경로 아님)
--   (B) bidirectional BOOLEAN 컬럼 추가: 단방향 hop 는 false
--       → 경로 추적 시 이후 구간 "일방향 — 확인 불가" 처리
--
-- 롤백: ALTER TABLE tb_network_link DROP COLUMN bidirectional;
--       (삭제된 링크 재삽입은 backup 테이블 없이는 불가 — backup 생성 후 진행)

BEGIN;

-- (B-1) 컬럼 추가 (기본 true — 기존 링크 동작 유지)
ALTER TABLE tb_network_link
    ADD COLUMN IF NOT EXISTS bidirectional BOOLEAN NOT NULL DEFAULT TRUE;

COMMENT ON COLUMN tb_network_link.bidirectional IS
    '양방향 통신 가능 여부. false 면 단방향 경계(예: 망간자료 전송장치) — '
    '경로 추적 시 이후 구간은 역방향 ping/SNMP 체크 불가로 간주';

-- (A) 잘못된 직결 링크 삭제 — UTM 에서 업무망 장비로 바로 점프하는 3건.
-- 원래는 UTM → 망간자료 전송장치(제어망 측) → 망간자료 전송장치(업무망 측)
-- → 업무망 장비 로 가야 함.
DELETE FROM tb_network_link
WHERE (source_equipment_id, target_equipment_id) IN (
    SELECT nl.source_equipment_id, nl.target_equipment_id
    FROM tb_network_link nl
    JOIN tb_equipment_info ei_s ON ei_s.equipment_id = nl.source_equipment_id
    JOIN tb_equipment_info ei_t ON ei_t.equipment_id = nl.target_equipment_id
    WHERE ei_s.facilitytype = '제어망'
      AND ei_s.equipmenttype = 'UTM'
      AND ei_t.facilitytype = '업무망'
      AND ei_t.equipmenttype IN ('WEB 서버', '관망관리 PC')
);

-- (B-2) 남아있는 제어망 → 업무망 경계 링크를 일방향으로 표시.
-- 망간자료 전송장치가 포함된 링크가 단방향.
UPDATE tb_network_link nl
SET bidirectional = FALSE
FROM tb_equipment_info ei_s, tb_equipment_info ei_t
WHERE nl.source_equipment_id = ei_s.equipment_id
  AND nl.target_equipment_id = ei_t.equipment_id
  AND (
    (ei_s.facilitytype = '제어망' AND ei_t.facilitytype = '업무망')
    OR (ei_s.equipmenttype = '망간자료 전송장치'
        OR ei_t.equipmenttype = '망간자료 전송장치')
  );

-- (B-3) 경로 생성 뷰에 bidirectional=true 필터 추가.
-- 단방향 hop 이후 장비는 역방향 체크 불가이므로 경로 추적 대상에서 제외.
CREATE OR REPLACE VIEW v_network_path_trace_stop_local_with_status AS
WITH RECURSIVE upstream AS (
    SELECT nl.source_equipment_id,
           nl.target_equipment_id,
           ARRAY[nl.source_equipment_id, nl.target_equipment_id] AS path_arr,
           1 AS depth,
           nl.link_protocol,
           nl.link_device_interface
    FROM tb_network_link nl
    WHERE nl.bidirectional = TRUE  -- 단방향 링크 제외
    UNION ALL
    SELECT u.source_equipment_id,
           nl.target_equipment_id,
           u.path_arr || nl.target_equipment_id,
           u.depth + 1,
           nl.link_protocol,
           nl.link_device_interface
    FROM tb_network_link nl
    JOIN upstream u ON nl.source_equipment_id::text = u.target_equipment_id::text
    WHERE nl.bidirectional = TRUE  -- 단방향 링크 제외
      AND (nl.target_equipment_id::text <> ALL (u.path_arr::text[]))
      AND u.depth < 10
), path_final AS (
    SELECT array_to_string(upstream.path_arr, ' → ') AS path_str,
           upstream.path_arr,
           array_length(upstream.path_arr, 1) AS depth
    FROM upstream
)
SELECT DISTINCT path_str,
       depth,
       path_arr[array_length(path_arr, 1)] AS last_equipment_id
FROM path_final pf;

COMMIT;
