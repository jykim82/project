-- ============================================================
-- 02: Core Tables - 인증, 사용자, 메뉴, 공통코드
-- region 기반 멀티테넌시
-- ============================================================

SET client_encoding = 'UTF8';

-- ------------------------------------------------------------
-- 권한 그룹
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tb_auth (
    region      character varying(10) NOT NULL,
    auth_idn    character varying(20) NOT NULL,
    auth_nm     character varying(50),
    use_yn      character varying(1) DEFAULT 'Y',
    created_at  timestamp with time zone DEFAULT now(),
    updated_at  timestamp with time zone DEFAULT now(),
    PRIMARY KEY (region, auth_idn)
);
COMMENT ON TABLE tb_auth IS '권한 그룹 (ADMIN, USER, VIEWER 등)';

-- ------------------------------------------------------------
-- 사용자
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tb_user (
    region       character varying(10) NOT NULL,
    user_id      character varying(45) NOT NULL,
    user_nm      character varying(50),
    user_pw      character varying(200),           -- 레거시 AES 암호화
    user_pw_hash character varying(100),            -- bcrypt 해시
    pw_migrated  boolean DEFAULT false,             -- AES→bcrypt 마이그레이션 완료 여부
    user_auth    character varying(20),
    use_yn       character varying(1) DEFAULT 'Y',
    dup_yn       character varying(1) DEFAULT 'Y',  -- 중복 로그인 허용 여부
    lock_cnt     integer DEFAULT 0,
    last_login   character varying(20),
    created_at   timestamp with time zone DEFAULT now(),
    updated_at   timestamp with time zone DEFAULT now(),
    PRIMARY KEY (region, user_id)
);
COMMENT ON TABLE tb_user IS '사용자 정보. pw_migrated로 bcrypt 전환 추적';
COMMENT ON COLUMN tb_user.user_pw_hash IS 'bcrypt 해시 비밀번호';
COMMENT ON COLUMN tb_user.pw_migrated IS 'AES→bcrypt 마이그레이션 완료 여부';

-- user_auth FK
ALTER TABLE tb_user
    ADD CONSTRAINT fk_user_auth
    FOREIGN KEY (region, user_auth)
    REFERENCES tb_auth(region, auth_idn);

-- ⚠ tb_user_session 제거됨 [E-020] (2026-04-13)
-- 세션은 tb_user.current_session_id 컬럼으로 통합 관리됨.
-- 백업: db/backups/unused_tables_backup_2026-04-13.sql

-- ------------------------------------------------------------
-- 접근 로그
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tb_access_log (
    log_id        bigserial PRIMARY KEY,
    region        character varying(10),
    user_id       character varying(45),
    request_path  character varying(500),
    request_method character varying(10),
    client_ip     character varying(50),
    access_time   timestamp with time zone DEFAULT now(),
    response_code integer
);
COMMENT ON TABLE tb_access_log IS 'API 접근 로그';

ALTER TABLE tb_access_log
    ADD CONSTRAINT fk_access_log_user
    FOREIGN KEY (region, user_id)
    REFERENCES tb_user(region, user_id);

CREATE INDEX IF NOT EXISTS idx_access_log_time ON tb_access_log(access_time DESC);
CREATE INDEX IF NOT EXISTS idx_access_log_user ON tb_access_log(region, user_id);

-- ------------------------------------------------------------
-- 메뉴
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tb_menu (
    region     character varying(10) NOT NULL,
    menu_idn   character varying(20) NOT NULL,
    menu_nm    character varying(100),
    pmenu_idn  character varying(20),              -- 부모 메뉴 (자기참조)
    app_path   character varying(200),              -- Next.js 라우트 경로
    menu_type  character varying(10),               -- 'group' | 'menu'
    menu_idx   integer DEFAULT 0,                   -- 정렬 순서
    icon       character varying(50),
    use_yn     character varying(1) DEFAULT 'Y',
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    PRIMARY KEY (region, menu_idn)
);
COMMENT ON TABLE tb_menu IS '메뉴 트리. pmenu_idn으로 자기참조 계층 구조';

-- 자기참조 FK (부모 메뉴)
ALTER TABLE tb_menu
    ADD CONSTRAINT fk_menu_parent
    FOREIGN KEY (region, pmenu_idn)
    REFERENCES tb_menu(region, menu_idn);

-- ------------------------------------------------------------
-- 권한별 메뉴 매핑
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tb_auth_menu (
    region    character varying(10) NOT NULL,
    auth_idn  character varying(20) NOT NULL,
    menu_idn  character varying(20) NOT NULL,
    PRIMARY KEY (region, auth_idn, menu_idn)
);

ALTER TABLE tb_auth_menu
    ADD CONSTRAINT fk_auth_menu_menu
    FOREIGN KEY (region, menu_idn)
    REFERENCES tb_menu(region, menu_idn)
    ON DELETE CASCADE;

ALTER TABLE tb_auth_menu
    ADD CONSTRAINT fk_auth_menu_auth
    FOREIGN KEY (region, auth_idn)
    REFERENCES tb_auth(region, auth_idn)
    ON DELETE CASCADE;

-- ⚠ tb_menu_api 제거됨 [E-020] (2026-04-13)
-- 메뉴-API 매핑은 미구현 상태였으므로 제거. 향후 RBAC 확장 시 재도입 가능.
-- 백업: db/backups/unused_tables_backup_2026-04-13.sql

-- ------------------------------------------------------------
-- 공통 그룹코드
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tb_grp_code (
    region   character varying(10) NOT NULL,
    grp_cd   character varying(30) NOT NULL,
    grp_nm   character varying(100),
    use_yn   character varying(1) DEFAULT 'Y',
    PRIMARY KEY (region, grp_cd)
);
COMMENT ON TABLE tb_grp_code IS '공통 그룹코드';

-- ------------------------------------------------------------
-- 공통 상세코드
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tb_comm_code (
    region   character varying(10) NOT NULL,
    grp_cd   character varying(30) NOT NULL,
    comm_cd  character varying(30) NOT NULL,
    comm_nm  character varying(100),
    sort_num integer DEFAULT 0,
    use_yn   character varying(1) DEFAULT 'Y',
    PRIMARY KEY (region, grp_cd, comm_cd)
);

ALTER TABLE tb_comm_code
    ADD CONSTRAINT fk_comm_grp
    FOREIGN KEY (region, grp_cd)
    REFERENCES tb_grp_code(region, grp_cd)
    ON DELETE CASCADE;
