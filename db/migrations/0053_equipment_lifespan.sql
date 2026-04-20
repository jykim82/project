-- 0053_equipment_lifespan.sql
-- 설비 내용연수 기반 교체 권고 — 기준 테이블 + equipmenttype 매핑
--
-- 배경: 기존 "교체 후보 분석"은 알람 빈도 기반이라 "조치 필요 설비"
-- 에 가깝고, 실제 "교체 권고"는 설치 경과 연수 + 국세청 내용연수 +
-- 제조사 EOL 기준이 적합. 이 migration 으로 기준 테이블 구축.
--
-- 주의: tb_equipment_info.commissioned_at 은 이미 컬럼 존재하나 현재
-- 모든 row 가 NULL. 설치일 입력 UI 는 후속 작업.
--
-- 롤백: DROP TABLE tb_equipment_category_map; DROP TABLE tb_equipment_lifespan;

BEGIN;

CREATE TABLE IF NOT EXISTS tb_equipment_lifespan (
    category           TEXT PRIMARY KEY,
    years_recommended  INT  NOT NULL,
    years_tax          INT,
    eol_note           TEXT,
    remarks            TEXT,
    created_at         TIMESTAMPTZ DEFAULT now(),
    updated_at         TIMESTAMPTZ DEFAULT now()
);

COMMENT ON TABLE tb_equipment_lifespan IS
    '설비 카테고리별 내용연수 기준 (교체 권고). years_recommended=운영 권고, '
    'years_tax=국세청 내용연수 기준. eol_note=제조사 기술지원 종료 참고';

CREATE TABLE IF NOT EXISTS tb_equipment_category_map (
    equipmenttype TEXT PRIMARY KEY,
    category      TEXT NOT NULL REFERENCES tb_equipment_lifespan(category) ON UPDATE CASCADE
);

COMMENT ON TABLE tb_equipment_category_map IS
    'tb_equipment_info.equipmenttype → 내용연수 카테고리 매핑';

-- 초기 시드 (사용자 제시 기준 + 국세청 참고값)
INSERT INTO tb_equipment_lifespan (category, years_recommended, years_tax, eol_note, remarks) VALUES
    ('통신',   7, 6,  '제조사 통신장비 EOL 일반 5~7년',
     '네트워크 장비(스위치·라우터·VPN·PLC 통신 모듈). 국세청 통신기기 6년.'),
    ('보안',   5, 5,  '방화벽·NAC 펌웨어·시그니처 지원 5년 이내',
     '보안 관련 NAC/방화벽/인증 서버.'),
    ('서버',   5, 5,  '서버 하드웨어 보증 3~5년, 이후 부품 수급 곤란',
     '관제 서버·DB 서버·관망관리 PC 등.'),
    ('유량계', 8, 8,  '제조사 부품 수급 8~10년, 센서 노후화',
     '국세청 측정계기 6~8년. 현장 환경(수질)에 따라 단축 가능.'),
    ('펌프',  12, 10, '임펠러·씰 마모, 재생정비 한계',
     '국세청 펌프 10년. 연속 운전·부식 환경에서 단축.')
ON CONFLICT (category) DO NOTHING;

-- equipmenttype → category 매핑 (기존 DB 에 존재하는 타입 기반)
INSERT INTO tb_equipment_category_map (equipmenttype, category) VALUES
    -- 통신
    ('PLC',                            '통신'),
    ('LTE 모뎀',                        '통신'),
    ('SSLVPN',                          '통신'),
    ('UTM',                             '통신'),
    ('FA망 현대화사업소 UTM',            '통신'),
    ('L2 스위치',                       '통신'),
    ('L3 스위치',                       '통신'),
    ('L2 스위치 가상 아이피',           '통신'),
    ('L3 스위치 가상 아아피',           '통신'),
    ('망간자료 전송장치',               '통신'),
    ('네트워크',                        '통신'),
    -- 보안
    ('NAC 정책 서버',                   '보안'),
    ('NAC 차단 서버',                   '보안'),
    -- 서버
    ('관망관리 서버',                   '서버'),
    ('관망관리 서버 가상 아이피',        '서버'),
    ('데이터베이스 서버',               '서버'),
    ('데이터베이스 서버 가상 아이피',    '서버'),
    ('IOT 서버',                        '서버'),
    ('WEB 서버',                        '서버'),
    ('관망감시 서버',                   '서버'),
    ('관망관리 PC',                     '서버'),
    ('관망감시 PC',                     '서버'),
    ('IOT PC',                          '서버'),
    ('로깅프린터',                      '서버'),
    ('로깅 프린터',                     '서버'),
    -- 유량계
    ('유량계',                          '유량계'),
    -- 펌프
    ('가압펌프',                        '펌프')
ON CONFLICT (equipmenttype) DO NOTHING;

COMMIT;
