-- 0124: 프로토콜 마스터 테이블 생성 (기존 버그 — tb_protocol_lookup 부재로
-- 네트워크 연결 폼의 프로토콜 선택이 항상 비어 있어 UI 링크 등록 불가.
-- 링크 에디터 E2E 중 발견, network-link-editor-spec §4)
--
-- 시드: 실사용 링크(tb_network_link)의 프로토콜 5종 + 예비 표준 3종.
-- 롤백: DROP TABLE tb_protocol_lookup;

CREATE TABLE IF NOT EXISTS tb_protocol_lookup (
    protocol_code varchar(50)  PRIMARY KEY,
    display_name  varchar(100) NOT NULL,
    protocol_type varchar(30),
    description   text,
    created_at    timestamptz  NOT NULL DEFAULT now()
);

INSERT INTO tb_protocol_lookup (protocol_code, display_name, protocol_type, description) VALUES
    ('modbus_rtu',     'Modbus RTU',        'serial',   'RS-485 직렬 계측 프로토콜'),
    ('modbus_tcp',     'Modbus TCP',        'ethernet', '이더넷 Modbus'),
    ('xgt_glofa_enet', 'XGT/GLOFA Enet',    'ethernet', 'LS PLC 이더넷 프로토콜'),
    ('sslvpn',         'SSL VPN',           'tunnel',   '원격 보안 터널'),
    ('oneway_diode',   '단방향 다이오드',    'security', '일방향 자료전달 장치'),
    ('ethernet',       'Ethernet',          'ethernet', '일반 이더넷 링크'),
    ('serial_rs232',   'RS-232',            'serial',   '직렬 통신'),
    ('unknown',        '미확인',            'etc',      '프로토콜 미상 — 구축 시 확정 필요')
ON CONFLICT (protocol_code) DO NOTHING;
