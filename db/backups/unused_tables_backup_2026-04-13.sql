--
-- PostgreSQL database dump
--

\restrict 9r5ak4CGZStYSJM0pQfFNcr2kgiaeoU4LdJ6uyQg8ZEaK76nUvvMV66VJQndson

-- Dumped from database version 16.11
-- Dumped by pg_dump version 16.11

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: tb_ai_chat_ask; Type: TABLE; Schema: public; Owner: slm_dev
--

CREATE TABLE public.tb_ai_chat_ask (
    ask_seq bigint NOT NULL,
    region character varying(10) NOT NULL,
    user_id character varying(45) NOT NULL,
    group_id character varying(100),
    ask_msg text,
    ask_dt character varying(8),
    ask_tm character varying(6),
    ask_at timestamp with time zone
);


ALTER TABLE public.tb_ai_chat_ask OWNER TO slm_dev;

--
-- Name: TABLE tb_ai_chat_ask; Type: COMMENT; Schema: public; Owner: slm_dev
--

COMMENT ON TABLE public.tb_ai_chat_ask IS 'AI 채팅 사용자 질문';


--
-- Name: tb_ai_chat_ask_ask_seq_seq; Type: SEQUENCE; Schema: public; Owner: slm_dev
--

CREATE SEQUENCE public.tb_ai_chat_ask_ask_seq_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.tb_ai_chat_ask_ask_seq_seq OWNER TO slm_dev;

--
-- Name: tb_ai_chat_ask_ask_seq_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: slm_dev
--

ALTER SEQUENCE public.tb_ai_chat_ask_ask_seq_seq OWNED BY public.tb_ai_chat_ask.ask_seq;


--
-- Name: tb_ai_chat_ask_group; Type: TABLE; Schema: public; Owner: slm_dev
--

CREATE TABLE public.tb_ai_chat_ask_group (
    group_id character varying(100) NOT NULL,
    region character varying(10) NOT NULL,
    user_id character varying(45) NOT NULL,
    group_title character varying(200),
    last_dt character varying(8),
    last_at timestamp with time zone,
    del_yn character varying(1) DEFAULT 'N'::character varying,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.tb_ai_chat_ask_group OWNER TO slm_dev;

--
-- Name: TABLE tb_ai_chat_ask_group; Type: COMMENT; Schema: public; Owner: slm_dev
--

COMMENT ON TABLE public.tb_ai_chat_ask_group IS 'AI 채팅 세션 그룹. del_yn=Y로 소프트 삭제';


--
-- Name: tb_ai_chat_ask_image; Type: TABLE; Schema: public; Owner: slm_dev
--

CREATE TABLE public.tb_ai_chat_ask_image (
    image_seq bigint NOT NULL,
    ask_seq bigint NOT NULL,
    region character varying(10) NOT NULL,
    user_id character varying(45) NOT NULL,
    image_data bytea,
    file_id bigint,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.tb_ai_chat_ask_image OWNER TO slm_dev;

--
-- Name: tb_ai_chat_ask_image_image_seq_seq; Type: SEQUENCE; Schema: public; Owner: slm_dev
--

CREATE SEQUENCE public.tb_ai_chat_ask_image_image_seq_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.tb_ai_chat_ask_image_image_seq_seq OWNER TO slm_dev;

--
-- Name: tb_ai_chat_ask_image_image_seq_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: slm_dev
--

ALTER SEQUENCE public.tb_ai_chat_ask_image_image_seq_seq OWNED BY public.tb_ai_chat_ask_image.image_seq;


--
-- Name: tb_ai_chat_bot; Type: TABLE; Schema: public; Owner: slm_dev
--

CREATE TABLE public.tb_ai_chat_bot (
    ask_seq bigint NOT NULL,
    region character varying(10) NOT NULL,
    user_id character varying(45) NOT NULL,
    bot_msg text,
    references_data jsonb,
    recquestions jsonb,
    visual_data jsonb,
    bot_dt character varying(8),
    bot_tm character varying(6),
    bot_at timestamp with time zone
);


ALTER TABLE public.tb_ai_chat_bot OWNER TO slm_dev;

--
-- Name: TABLE tb_ai_chat_bot; Type: COMMENT; Schema: public; Owner: slm_dev
--

COMMENT ON TABLE public.tb_ai_chat_bot IS 'AI 채팅 봇 응답. visual_data로 차트 재렌더링';


--
-- Name: COLUMN tb_ai_chat_bot.visual_data; Type: COMMENT; Schema: public; Owner: slm_dev
--

COMMENT ON COLUMN public.tb_ai_chat_bot.visual_data IS 'graph_type별 시각화 데이터 (table/plot/diagram/document). 히스토리에서 차트 재렌더링용';


--
-- Name: tb_ai_chat_bot_image; Type: TABLE; Schema: public; Owner: slm_dev
--

CREATE TABLE public.tb_ai_chat_bot_image (
    image_seq bigint NOT NULL,
    ask_seq bigint NOT NULL,
    region character varying(10) NOT NULL,
    user_id character varying(45) NOT NULL,
    image_data bytea,
    file_id bigint,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.tb_ai_chat_bot_image OWNER TO slm_dev;

--
-- Name: tb_ai_chat_bot_image_image_seq_seq; Type: SEQUENCE; Schema: public; Owner: slm_dev
--

CREATE SEQUENCE public.tb_ai_chat_bot_image_image_seq_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.tb_ai_chat_bot_image_image_seq_seq OWNER TO slm_dev;

--
-- Name: tb_ai_chat_bot_image_image_seq_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: slm_dev
--

ALTER SEQUENCE public.tb_ai_chat_bot_image_image_seq_seq OWNED BY public.tb_ai_chat_bot_image.image_seq;


--
-- Name: tb_ai_chat_faq; Type: TABLE; Schema: public; Owner: slm_dev
--

CREATE TABLE public.tb_ai_chat_faq (
    region character varying(10) NOT NULL,
    faq_idn character varying(20) NOT NULL,
    faq_contents text,
    sort_num integer DEFAULT 0,
    open_date character varying(8),
    close_date character varying(8),
    open_at timestamp with time zone,
    close_at timestamp with time zone,
    use_yn character varying(1) DEFAULT 'Y'::character varying,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.tb_ai_chat_faq OWNER TO slm_dev;

--
-- Name: TABLE tb_ai_chat_faq; Type: COMMENT; Schema: public; Owner: slm_dev
--

COMMENT ON TABLE public.tb_ai_chat_faq IS 'AI 채팅 FAQ. 채팅 시작 시 추천 질문으로 표시';


--
-- Name: tb_alarm_log; Type: TABLE; Schema: public; Owner: slm_dev
--

CREATE TABLE public.tb_alarm_log (
    alarm_id bigint NOT NULL,
    region character varying(10),
    sitename character varying(50),
    facilitytype character varying(30),
    tagsn character varying(50),
    alarm_level character varying(10),
    alarm_value double precision,
    alarm_threshold double precision,
    alarm_message text,
    alarm_event_time character varying(14),
    alarm_at timestamp with time zone,
    resolved_at timestamp with time zone,
    resolved_yn character varying(1) DEFAULT 'N'::character varying
);


ALTER TABLE public.tb_alarm_log OWNER TO slm_dev;

--
-- Name: TABLE tb_alarm_log; Type: COMMENT; Schema: public; Owner: slm_dev
--

COMMENT ON TABLE public.tb_alarm_log IS '센서 알람 이력. alarm_level은 HH/H/L/LL';


--
-- Name: tb_alarm_log_alarm_id_seq; Type: SEQUENCE; Schema: public; Owner: slm_dev
--

CREATE SEQUENCE public.tb_alarm_log_alarm_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.tb_alarm_log_alarm_id_seq OWNER TO slm_dev;

--
-- Name: tb_alarm_log_alarm_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: slm_dev
--

ALTER SEQUENCE public.tb_alarm_log_alarm_id_seq OWNED BY public.tb_alarm_log.alarm_id;


--
-- Name: tb_file_history; Type: TABLE; Schema: public; Owner: slm_dev
--

CREATE TABLE public.tb_file_history (
    history_id bigint NOT NULL,
    region character varying(10),
    file_name character varying(200),
    file_type character varying(50),
    binary_content bytea,
    file_id bigint,
    uploaded_by character varying(45),
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.tb_file_history OWNER TO slm_dev;

--
-- Name: tb_file_history_history_id_seq; Type: SEQUENCE; Schema: public; Owner: slm_dev
--

CREATE SEQUENCE public.tb_file_history_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.tb_file_history_history_id_seq OWNER TO slm_dev;

--
-- Name: tb_file_history_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: slm_dev
--

ALTER SEQUENCE public.tb_file_history_history_id_seq OWNED BY public.tb_file_history.history_id;


--
-- Name: tb_menu_api; Type: TABLE; Schema: public; Owner: slm_dev
--

CREATE TABLE public.tb_menu_api (
    region character varying(10) NOT NULL,
    menu_idn character varying(20) NOT NULL,
    api_cd character varying(100) NOT NULL,
    api_path character varying(300),
    api_method character varying(10),
    api_desc character varying(200)
);


ALTER TABLE public.tb_menu_api OWNER TO slm_dev;

--
-- Name: tb_prompt_column; Type: TABLE; Schema: public; Owner: slm_dev
--

CREATE TABLE public.tb_prompt_column (
    region character varying(10) NOT NULL,
    template_id character varying(50) NOT NULL,
    column_name character varying(100) NOT NULL,
    column_label character varying(100),
    column_type character varying(30),
    sort_num integer DEFAULT 0,
    use_yn character varying(1) DEFAULT 'Y'::character varying
);


ALTER TABLE public.tb_prompt_column OWNER TO slm_dev;

--
-- Name: tb_prompt_template; Type: TABLE; Schema: public; Owner: slm_dev
--

CREATE TABLE public.tb_prompt_template (
    region character varying(10) NOT NULL,
    template_id character varying(50) NOT NULL,
    template_name character varying(200),
    description text,
    prompt_format text,
    icon character varying(30),
    use_yn character varying(1) DEFAULT 'Y'::character varying,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.tb_prompt_template OWNER TO slm_dev;

--
-- Name: TABLE tb_prompt_template; Type: COMMENT; Schema: public; Owner: slm_dev
--

COMMENT ON TABLE public.tb_prompt_template IS 'AI 프롬프트 템플릿. 인텐트별 응답 포맷 정의';


--
-- Name: tb_user_session; Type: TABLE; Schema: public; Owner: slm_dev
--

CREATE TABLE public.tb_user_session (
    session_id character varying(100) NOT NULL,
    region character varying(10) NOT NULL,
    user_id character varying(45) NOT NULL,
    login_time timestamp with time zone DEFAULT now(),
    is_expired character varying(1) DEFAULT 'N'::character varying,
    client_ip character varying(50),
    user_agent text,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.tb_user_session OWNER TO slm_dev;

--
-- Name: TABLE tb_user_session; Type: COMMENT; Schema: public; Owner: slm_dev
--

COMMENT ON TABLE public.tb_user_session IS '로그인 세션 관리. is_expired=Y면 만료';


--
-- Name: tb_ai_chat_ask ask_seq; Type: DEFAULT; Schema: public; Owner: slm_dev
--

ALTER TABLE ONLY public.tb_ai_chat_ask ALTER COLUMN ask_seq SET DEFAULT nextval('public.tb_ai_chat_ask_ask_seq_seq'::regclass);


--
-- Name: tb_ai_chat_ask_image image_seq; Type: DEFAULT; Schema: public; Owner: slm_dev
--

ALTER TABLE ONLY public.tb_ai_chat_ask_image ALTER COLUMN image_seq SET DEFAULT nextval('public.tb_ai_chat_ask_image_image_seq_seq'::regclass);


--
-- Name: tb_ai_chat_bot_image image_seq; Type: DEFAULT; Schema: public; Owner: slm_dev
--

ALTER TABLE ONLY public.tb_ai_chat_bot_image ALTER COLUMN image_seq SET DEFAULT nextval('public.tb_ai_chat_bot_image_image_seq_seq'::regclass);


--
-- Name: tb_alarm_log alarm_id; Type: DEFAULT; Schema: public; Owner: slm_dev
--

ALTER TABLE ONLY public.tb_alarm_log ALTER COLUMN alarm_id SET DEFAULT nextval('public.tb_alarm_log_alarm_id_seq'::regclass);


--
-- Name: tb_file_history history_id; Type: DEFAULT; Schema: public; Owner: slm_dev
--

ALTER TABLE ONLY public.tb_file_history ALTER COLUMN history_id SET DEFAULT nextval('public.tb_file_history_history_id_seq'::regclass);


--
-- Name: tb_ai_chat_ask_group tb_ai_chat_ask_group_pkey; Type: CONSTRAINT; Schema: public; Owner: slm_dev
--

ALTER TABLE ONLY public.tb_ai_chat_ask_group
    ADD CONSTRAINT tb_ai_chat_ask_group_pkey PRIMARY KEY (group_id, region, user_id);


--
-- Name: tb_ai_chat_ask_image tb_ai_chat_ask_image_pkey; Type: CONSTRAINT; Schema: public; Owner: slm_dev
--

ALTER TABLE ONLY public.tb_ai_chat_ask_image
    ADD CONSTRAINT tb_ai_chat_ask_image_pkey PRIMARY KEY (image_seq);


--
-- Name: tb_ai_chat_ask tb_ai_chat_ask_pkey; Type: CONSTRAINT; Schema: public; Owner: slm_dev
--

ALTER TABLE ONLY public.tb_ai_chat_ask
    ADD CONSTRAINT tb_ai_chat_ask_pkey PRIMARY KEY (ask_seq, region, user_id);


--
-- Name: tb_ai_chat_bot_image tb_ai_chat_bot_image_pkey; Type: CONSTRAINT; Schema: public; Owner: slm_dev
--

ALTER TABLE ONLY public.tb_ai_chat_bot_image
    ADD CONSTRAINT tb_ai_chat_bot_image_pkey PRIMARY KEY (image_seq);


--
-- Name: tb_ai_chat_bot tb_ai_chat_bot_pkey; Type: CONSTRAINT; Schema: public; Owner: slm_dev
--

ALTER TABLE ONLY public.tb_ai_chat_bot
    ADD CONSTRAINT tb_ai_chat_bot_pkey PRIMARY KEY (ask_seq, region, user_id);


--
-- Name: tb_ai_chat_faq tb_ai_chat_faq_pkey; Type: CONSTRAINT; Schema: public; Owner: slm_dev
--

ALTER TABLE ONLY public.tb_ai_chat_faq
    ADD CONSTRAINT tb_ai_chat_faq_pkey PRIMARY KEY (region, faq_idn);


--
-- Name: tb_alarm_log tb_alarm_log_pkey; Type: CONSTRAINT; Schema: public; Owner: slm_dev
--

ALTER TABLE ONLY public.tb_alarm_log
    ADD CONSTRAINT tb_alarm_log_pkey PRIMARY KEY (alarm_id);


--
-- Name: tb_file_history tb_file_history_pkey; Type: CONSTRAINT; Schema: public; Owner: slm_dev
--

ALTER TABLE ONLY public.tb_file_history
    ADD CONSTRAINT tb_file_history_pkey PRIMARY KEY (history_id);


--
-- Name: tb_menu_api tb_menu_api_pkey; Type: CONSTRAINT; Schema: public; Owner: slm_dev
--

ALTER TABLE ONLY public.tb_menu_api
    ADD CONSTRAINT tb_menu_api_pkey PRIMARY KEY (region, menu_idn, api_cd);


--
-- Name: tb_prompt_column tb_prompt_column_pkey; Type: CONSTRAINT; Schema: public; Owner: slm_dev
--

ALTER TABLE ONLY public.tb_prompt_column
    ADD CONSTRAINT tb_prompt_column_pkey PRIMARY KEY (region, template_id, column_name);


--
-- Name: tb_prompt_template tb_prompt_template_pkey; Type: CONSTRAINT; Schema: public; Owner: slm_dev
--

ALTER TABLE ONLY public.tb_prompt_template
    ADD CONSTRAINT tb_prompt_template_pkey PRIMARY KEY (region, template_id);


--
-- Name: tb_user_session tb_user_session_pkey; Type: CONSTRAINT; Schema: public; Owner: slm_dev
--

ALTER TABLE ONLY public.tb_user_session
    ADD CONSTRAINT tb_user_session_pkey PRIMARY KEY (session_id);


--
-- Name: idx_alarm_log_site; Type: INDEX; Schema: public; Owner: slm_dev
--

CREATE INDEX idx_alarm_log_site ON public.tb_alarm_log USING btree (region, sitename, facilitytype);


--
-- Name: idx_alarm_log_time; Type: INDEX; Schema: public; Owner: slm_dev
--

CREATE INDEX idx_alarm_log_time ON public.tb_alarm_log USING btree (alarm_at DESC NULLS LAST);


--
-- Name: idx_chat_ask_group_list; Type: INDEX; Schema: public; Owner: slm_dev
--

CREATE INDEX idx_chat_ask_group_list ON public.tb_ai_chat_ask_group USING btree (region, user_id, del_yn);


--
-- Name: idx_chat_ask_group_user; Type: INDEX; Schema: public; Owner: slm_dev
--

CREATE INDEX idx_chat_ask_group_user ON public.tb_ai_chat_ask USING btree (region, user_id, group_id);


--
-- Name: idx_chat_bot_visual_type; Type: INDEX; Schema: public; Owner: slm_dev
--

CREATE INDEX idx_chat_bot_visual_type ON public.tb_ai_chat_bot USING btree (((visual_data ->> 'type'::text))) WHERE (visual_data IS NOT NULL);


--
-- Name: idx_user_session_active; Type: INDEX; Schema: public; Owner: slm_dev
--

CREATE INDEX idx_user_session_active ON public.tb_user_session USING btree (region, user_id, is_expired);


--
-- Name: tb_ai_chat_ask_image fk_ask_image_ask; Type: FK CONSTRAINT; Schema: public; Owner: slm_dev
--

ALTER TABLE ONLY public.tb_ai_chat_ask_image
    ADD CONSTRAINT fk_ask_image_ask FOREIGN KEY (ask_seq, region, user_id) REFERENCES public.tb_ai_chat_ask(ask_seq, region, user_id);


--
-- Name: tb_ai_chat_ask_image fk_ask_image_file; Type: FK CONSTRAINT; Schema: public; Owner: slm_dev
--

ALTER TABLE ONLY public.tb_ai_chat_ask_image
    ADD CONSTRAINT fk_ask_image_file FOREIGN KEY (file_id) REFERENCES public.tb_file_storage(file_id);


--
-- Name: tb_ai_chat_bot_image fk_bot_image_bot; Type: FK CONSTRAINT; Schema: public; Owner: slm_dev
--

ALTER TABLE ONLY public.tb_ai_chat_bot_image
    ADD CONSTRAINT fk_bot_image_bot FOREIGN KEY (ask_seq, region, user_id) REFERENCES public.tb_ai_chat_bot(ask_seq, region, user_id);


--
-- Name: tb_ai_chat_bot_image fk_bot_image_file; Type: FK CONSTRAINT; Schema: public; Owner: slm_dev
--

ALTER TABLE ONLY public.tb_ai_chat_bot_image
    ADD CONSTRAINT fk_bot_image_file FOREIGN KEY (file_id) REFERENCES public.tb_file_storage(file_id);


--
-- Name: tb_ai_chat_ask fk_chat_ask_group; Type: FK CONSTRAINT; Schema: public; Owner: slm_dev
--

ALTER TABLE ONLY public.tb_ai_chat_ask
    ADD CONSTRAINT fk_chat_ask_group FOREIGN KEY (group_id, region, user_id) REFERENCES public.tb_ai_chat_ask_group(group_id, region, user_id);


--
-- Name: tb_ai_chat_ask fk_chat_ask_user; Type: FK CONSTRAINT; Schema: public; Owner: slm_dev
--

ALTER TABLE ONLY public.tb_ai_chat_ask
    ADD CONSTRAINT fk_chat_ask_user FOREIGN KEY (region, user_id) REFERENCES public.tb_user(region, user_id);


--
-- Name: tb_ai_chat_bot fk_chat_bot_ask; Type: FK CONSTRAINT; Schema: public; Owner: slm_dev
--

ALTER TABLE ONLY public.tb_ai_chat_bot
    ADD CONSTRAINT fk_chat_bot_ask FOREIGN KEY (ask_seq, region, user_id) REFERENCES public.tb_ai_chat_ask(ask_seq, region, user_id);


--
-- Name: tb_file_history fk_file_history_file; Type: FK CONSTRAINT; Schema: public; Owner: slm_dev
--

ALTER TABLE ONLY public.tb_file_history
    ADD CONSTRAINT fk_file_history_file FOREIGN KEY (file_id) REFERENCES public.tb_file_storage(file_id);


--
-- Name: tb_menu_api fk_menu_api_menu; Type: FK CONSTRAINT; Schema: public; Owner: slm_dev
--

ALTER TABLE ONLY public.tb_menu_api
    ADD CONSTRAINT fk_menu_api_menu FOREIGN KEY (region, menu_idn) REFERENCES public.tb_menu(region, menu_idn) ON DELETE CASCADE;


--
-- Name: tb_prompt_column fk_prompt_column_template; Type: FK CONSTRAINT; Schema: public; Owner: slm_dev
--

ALTER TABLE ONLY public.tb_prompt_column
    ADD CONSTRAINT fk_prompt_column_template FOREIGN KEY (region, template_id) REFERENCES public.tb_prompt_template(region, template_id) ON DELETE CASCADE;


--
-- Name: tb_user_session fk_session_user; Type: FK CONSTRAINT; Schema: public; Owner: slm_dev
--

ALTER TABLE ONLY public.tb_user_session
    ADD CONSTRAINT fk_session_user FOREIGN KEY (region, user_id) REFERENCES public.tb_user(region, user_id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict 9r5ak4CGZStYSJM0pQfFNcr2kgiaeoU4LdJ6uyQg8ZEaK76nUvvMV66VJQndson

