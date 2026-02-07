--
-- PostgreSQL database dump
--

-- Dumped from database version 16.10 (Debian 16.10-1.pgdg13+1)
-- Dumped by pg_dump version 17.0

-- Started on 2026-02-01 21:56:47

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- TOC entry 6 (class 2615 OID 2200)
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA public;


--
-- TOC entry 4680 (class 0 OID 0)
-- Dependencies: 6
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS 'standard public schema';


--
-- TOC entry 780 (class 1255 OID 35201)
-- Name: create_mv_reservoir_level_stats(text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.create_mv_reservoir_level_stats(target_sitename text, target_keyword text) RETURNS void
    LANGUAGE plpgsql
    AS $_$
BEGIN
    EXECUTE format($sql$
        CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv_reservoir_level_stats_%I_%I AS
        WITH latest AS (
            SELECT 
                i.sitename,
                i.facilitytype,
                r.tagsn,
                r.val,
                i.datainfo,
                i.unit,
                r.logtime
            FROM tb_tag_info i
            JOIN LATERAL (
                SELECT val, logtime, tagsn
                FROM tb_tag_raw_data r
                WHERE r.tagsn = i.tagsn
                ORDER BY logtime DESC
                LIMIT 1
            ) r ON true
            WHERE i.sitename = %L
              AND i.facilitytype = '배수지'
              AND i.tagtype = 'Analog Input'
              AND i.datainfo ~ %L
        ),
        monthly_avg AS (
            SELECT 
                i.sitename,
                i.facilitytype,
                i.datainfo,
                ROUND(AVG(r.val)::numeric, 2) AS avg_month
            FROM tb_tag_info i
            JOIN tb_tag_raw_data r ON i.tagsn = r.tagsn
            WHERE i.sitename = %L
              AND i.facilitytype = '배수지'
              AND i.tagtype = 'Analog Input'
              AND i.datainfo ~ %L
              AND r.logtime >= now() - interval '1 month'
            GROUP BY i.sitename, i.facilitytype, i.datainfo
        ),
        yearly_avg AS (
            SELECT 
                i.sitename,
                i.facilitytype,
                i.datainfo,
                ROUND(AVG(r.val)::numeric, 2) AS avg_year
            FROM tb_tag_info i
            JOIN tb_tag_raw_data r ON i.tagsn = r.tagsn
            WHERE i.sitename = %L
              AND i.facilitytype = '배수지'
              AND i.tagtype = 'Analog Input'
              AND i.datainfo ~ %L
              AND r.logtime >= now() - interval '1 year'
            GROUP BY i.sitename, i.facilitytype, i.datainfo
        ),
        latest_avg AS (
            SELECT ROUND(AVG(val)::numeric, 2) AS avg_latest FROM latest
        )
        SELECT 
            l.sitename,
            l.facilitytype,
            l.tagsn,
            ROUND(l.val::numeric, 2) AS latest_val,
            to_char(l.logtime, 'YYYY-MM-DD HH24:MI:SS') AS log_time,
            l.datainfo,
            l.unit,
            m.avg_month,
            y.avg_year,
            la.avg_latest
        FROM latest l
        LEFT JOIN monthly_avg m 
            ON l.sitename = m.sitename 
           AND l.facilitytype = m.facilitytype 
           AND l.datainfo = m.datainfo
        LEFT JOIN yearly_avg y 
            ON l.sitename = y.sitename 
           AND l.facilitytype = y.facilitytype 
           AND l.datainfo = y.datainfo
        CROSS JOIN latest_avg la;
    $sql$, 
        target_sitename, target_keyword,  -- 뷰 이름 식별용
        target_sitename, target_keyword,  -- latest
        target_sitename, target_keyword,  -- monthly_avg
        target_sitename, target_keyword   -- yearly_avg
    );

    RAISE NOTICE 'Materialized View created: mv_reservoir_level_stats_%_%', target_sitename, target_keyword;
END;
$_$;


--
-- TOC entry 423 (class 1255 OID 24788)
-- Name: decrypt_flexiai(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.decrypt_flexiai(val text) RETURNS text
    LANGUAGE plpgsql
    AS $$
DECLARE
    key TEXT := RPAD('flexiai1234567890', 16, '#');
    decrypted BYTEA;
BEGIN
    IF val IS NULL OR length(val) = 0 THEN
        RETURN NULL;
    END IF;

    -- hex → bytea 로 decode
    decrypted := decrypt(decode(val, 'hex'), key::bytea, 'aes');

    -- 복호화된 bytea → text 변환 + trim (padding 제거)
    RETURN trim(BOTH FROM convert_from(decrypted, 'UTF8'));
END;
$$;


--
-- TOC entry 424 (class 1255 OID 24789)
-- Name: encrypt_flexiai(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.encrypt_flexiai(val text) RETURNS text
    LANGUAGE plpgsql
    AS $$
DECLARE
    key TEXT := RPAD('flexiai1234567890', 16, '#');
    padded TEXT;
BEGIN
    IF val IS NULL OR val = '' THEN
        RETURN NULL;
    END IF;

    padded := val || repeat(' ', 16 - length(val) % 16);
    RETURN encode(encrypt(padded::bytea, key::bytea, 'aes'), 'hex');  -- 🔑 text 반환
END;
$$;


--
-- TOC entry 782 (class 1255 OID 35993)
-- Name: fn_equipment_type_path_server_origin(text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_equipment_type_path_server_origin(p_sitename text, p_facilitytype text) RETURNS TABLE(type_path text, depth integer)
    LANGUAGE sql STABLE
    AS $$
WITH longest AS (
    SELECT 
        v.path_str,
        v.depth
    FROM v_network_path_trace_stop_local_with_status v
    JOIN tb_equipment_info e
      ON v.path_str LIKE '%' || e.equipment_id || '%'
    WHERE e.sitename = p_sitename
      AND e.facilitytype = p_facilitytype
      -- ✅ 출발 노드가 SERVER 또는 TM MASTER로 시작하는 경로만 선택
      AND (
          v.path_str ~ '^(server_|tm_master_)'
          OR v.path_str LIKE 'SERVER → %'
          OR v.path_str LIKE 'TM MASTER → %'
      )
    ORDER BY v.depth DESC
    LIMIT 1
),
type_path AS (
    SELECT 
        string_agg(eq.equipmenttype, ' → ' ORDER BY n.idx) AS type_path,
        l.depth
    FROM longest l,
         LATERAL string_to_array(l.path_str, ' → ') AS arr,
         LATERAL unnest(arr) WITH ORDINALITY AS n(id, idx)
         JOIN tb_equipment_info eq ON eq.equipment_id = n.id
    GROUP BY l.depth
)
SELECT type_path, depth
FROM type_path;
$$;


--
-- TOC entry 781 (class 1255 OID 35917)
-- Name: fn_equipment_upstream_status(character varying, character varying); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_equipment_upstream_status(p_sitename character varying, p_facilitytype character varying) RETURNS TABLE(depth integer, sitename character varying, facilitytype character varying, equipmenttype character varying, equipment_id character varying, link_protocol text, link_port integer, link_device_interface text, is_alive boolean, rtt_ms numeric, status_code character varying, protocol_status character varying, error_message text, check_time text)
    LANGUAGE sql
    AS $$
WITH RECURSIVE upstream AS (    
    -- 1️⃣ 시작 지점 (site + facilitytype)
    SELECT 
        e.equipment_id,
        e.sitename,
        e.facilitytype,
        e.equipmenttype,
        NULL::text AS link_protocol,
        NULL::integer AS link_port,
        NULL::text AS link_device_interface,
        0 AS depth
    FROM tb_equipment_info e
    WHERE e.sitename = p_sitename
      AND e.facilitytype = p_facilitytype

    UNION ALL
    
    -- 2️⃣ 상류 탐색 (target → source)
    SELECT 
        se.equipment_id,
        se.sitename,
        se.facilitytype,
        se.equipmenttype,
        nl.link_protocol,
        nl.link_port,
        nl.link_device_interface,
        u.depth + 1 AS depth
    FROM upstream u
    JOIN tb_network_link nl 
      ON nl.target_equipment_id = u.equipment_id
    JOIN tb_equipment_info se 
      ON se.equipment_id = nl.source_equipment_id
    WHERE u.depth < 6
),
max_depths AS (
    -- 3️⃣ 각 장비별 최대 depth 계산
    SELECT equipment_id, MAX(depth) AS max_depth
    FROM upstream
    GROUP BY equipment_id
)
-- 4️⃣ 최종 결과
SELECT 
    m.max_depth AS depth,
    u.sitename,
    u.facilitytype,
    u.equipmenttype,
    u.equipment_id,                    -- ✅ 누락되었던 equipment_id 추가
    u.link_protocol,
    u.link_port,
    u.link_device_interface,
    ns.is_alive,
    ns.rtt_ms,
    ns.status_code,
    ns.protocol_status,
    ns.error_message,
    to_char(ns.check_time, 'YYYY-MM-DD HH24:MI:SS') AS check_time
FROM max_depths m
JOIN upstream u 
  ON u.equipment_id = m.equipment_id 
 AND u.depth = m.max_depth
-- 🔹 각 장비별 최신 상태만 선택
LEFT JOIN LATERAL (
    SELECT 
        ns.is_alive,
        ns.rtt_ms,
        ns.status_code,
        ns.protocol_status,
        ns.error_message,
        ns.check_time
    FROM tb_network_status ns
    WHERE ns.equipment_id = u.equipment_id
    ORDER BY ns.check_time DESC
    LIMIT 1
) ns ON TRUE
ORDER BY m.max_depth, u.equipment_id;
$$;


--
-- TOC entry 790 (class 1255 OID 36185)
-- Name: fn_get_facility_flow_path(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_get_facility_flow_path(p_target text) RETURNS text
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_path text;
BEGIN
    WITH RECURSIVE flow_upstream AS (
        -- ✅ 시작점: 내가 보고 싶은 지점
        SELECT 
            upstream_sitename,
            upstream_facilitytype,
            downstream_sitename,
            downstream_facilitytype,
            relation_type,
            1 AS depth,
            downstream_sitename::text AS target,
            upstream_sitename || ' ' || upstream_facilitytype || ' → ' ||
            downstream_sitename || ' ' || downstream_facilitytype AS path
        FROM tb_facility_flow_map
        WHERE relation_type = '수계'
          AND downstream_sitename = p_target

        UNION ALL

        -- ✅ 상류 방향으로 재귀 탐색
        SELECT 
            f.upstream_sitename,
            f.upstream_facilitytype,
            f.downstream_sitename,
            f.downstream_facilitytype,
            f.relation_type,
            fu.depth + 1 AS depth,
            fu.target,
            f.upstream_sitename || ' ' || f.upstream_facilitytype || ' → ' || fu.path AS path
        FROM tb_facility_flow_map f
        JOIN flow_upstream fu 
          ON f.downstream_sitename = fu.upstream_sitename
         AND f.relation_type = '수계'
    )
    SELECT path
    INTO v_path
    FROM flow_upstream
    ORDER BY depth DESC
    LIMIT 1;

    -- 결과 없으면 메시지 처리
    IF v_path IS NULL THEN
        RETURN format('⚠️ [%s] 에 해당하는 수계 경로를 찾을 수 없습니다.', p_target);
    END IF;

    RETURN v_path;
END;
$$;


--
-- TOC entry 791 (class 1255 OID 36187)
-- Name: fn_get_facility_flow_path(text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_get_facility_flow_path(p_sitename text, p_facilitytype text) RETURNS text
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_path text;
BEGIN
    WITH RECURSIVE flow_upstream AS (
        -- ✅ 시작점: 내가 조회하고 싶은 블록
        SELECT 
            upstream_sitename,
            upstream_facilitytype,
            downstream_sitename,
            downstream_facilitytype,
            relation_type,
            1 AS depth,
            downstream_sitename::text AS target_sitename,
            downstream_facilitytype::text AS target_facilitytype,
            upstream_sitename || ' ' || upstream_facilitytype || ' → ' ||
            downstream_sitename || ' ' || downstream_facilitytype AS path
        FROM tb_facility_flow_map
        WHERE relation_type = '수계'
          AND downstream_sitename = p_sitename
          AND downstream_facilitytype = p_facilitytype

        UNION ALL

        -- ✅ 상류 방향으로 재귀 탐색
        SELECT 
            f.upstream_sitename,
            f.upstream_facilitytype,
            f.downstream_sitename,
            f.downstream_facilitytype,
            f.relation_type,
            fu.depth + 1 AS depth,
            fu.target_sitename,
            fu.target_facilitytype,
            f.upstream_sitename || ' ' || f.upstream_facilitytype || ' → ' || fu.path AS path
        FROM tb_facility_flow_map f
        JOIN flow_upstream fu 
          ON f.downstream_sitename = fu.upstream_sitename
         AND f.downstream_facilitytype = fu.upstream_facilitytype
         AND f.relation_type = '수계'
    )
    SELECT path
    INTO v_path
    FROM flow_upstream
    ORDER BY depth DESC
    LIMIT 1;

    IF v_path IS NULL THEN
        RETURN format('⚠️ [%s %s] 에 해당하는 수계 경로를 찾을 수 없습니다.', p_sitename, p_facilitytype);
    END IF;

    RETURN v_path;
END;
$$;


--
-- TOC entry 783 (class 1255 OID 35999)
-- Name: fn_network_path_hop_detail(text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_network_path_hop_detail(p_sitename text, p_facilitytype text) RETURNS TABLE(source_node text, source_sitename text, source_facilitytype text, source_equipmenttype text, target_node text, target_sitename text, target_facilitytype text, target_equipmenttype text, link_device_interface text, link_protocol text)
    LANGUAGE sql STABLE
    AS $$
WITH longest AS (
    SELECT 
        v.path_str,
        v.depth
    FROM v_network_path_trace_stop_local_with_status v
    JOIN tb_equipment_info e
      ON v.path_str LIKE '%' || e.equipment_id || '%'
    WHERE e.sitename = p_sitename
      AND e.facilitytype = p_facilitytype
      AND (
          v.path_str ~ '^(server_|tm_master_)'   -- ✅ SERVER 또는 TM MASTER 출발만 허용
          OR v.path_str LIKE 'SERVER → %'
          OR v.path_str LIKE 'TM MASTER → %'
      )
    ORDER BY v.depth DESC
    LIMIT 1
),
nodes AS (
    SELECT string_to_array(path_str, ' → ') AS arr
    FROM longest
),
hops AS (
    SELECT generate_subscripts(arr, 1) AS pos, arr
    FROM nodes
    WHERE array_length(arr, 1) > 1
)
SELECT 
    arr[pos]   AS source_node,
    src.sitename   AS source_sitename,
    src.facilitytype AS source_facilitytype,
    src.equipmenttype AS source_equipmenttype,

    arr[pos+1] AS target_node,
    tgt.sitename   AS target_sitename,
    tgt.facilitytype AS target_facilitytype,
    tgt.equipmenttype AS target_equipmenttype,

    nl.link_device_interface,
    nl.link_protocol
FROM hops
LEFT JOIN tb_network_link nl
       ON nl.source_equipment_id = arr[pos]
      AND nl.target_equipment_id = arr[pos+1]
LEFT JOIN tb_equipment_info src ON src.equipment_id = arr[pos]
LEFT JOIN tb_equipment_info tgt ON tgt.equipment_id = arr[pos+1]
WHERE pos < array_length(arr, 1)
ORDER BY pos;
$$;


--
-- TOC entry 798 (class 1255 OID 134996)
-- Name: fn_network_path_trace_with_status(text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_network_path_trace_with_status(p_sitename text, p_facilitytype text) RETURNS TABLE(pos integer, equipment_id text, sitename text, facilitytype text, equipmenttype text, rtt_ms numeric, status_code text, error_message text)
    LANGUAGE sql STABLE
    AS $$

/* ------------------------------------------------------------------
  1️⃣ 대상 현장/시설에 대해 "hop 정확 매칭" 경로 선택
-------------------------------------------------------------------*/
WITH candidate_paths AS (
    SELECT
        v.path_str,
        v.depth
    FROM v_network_path_trace_stop_local_with_status v
    WHERE EXISTS (
        SELECT 1
        FROM tb_equipment_info e
        WHERE e.sitename = p_sitename
          AND e.facilitytype = p_facilitytype
          -- 🔑 hop 단위 정확 매칭
          AND (' → ' || v.path_str || ' → ')
              LIKE ('% → ' || e.equipment_id || ' → %')
    )
),
selected_path AS (
    SELECT path_str
    FROM candidate_paths
    ORDER BY
        depth DESC   -- 🔑 최장(가장 상세한) 경로
    LIMIT 1
),

/* ------------------------------------------------------------------
  2️⃣ path_str → hop 배열 분해
-------------------------------------------------------------------*/
nodes AS (
    SELECT string_to_array(path_str, ' → ') AS hops
    FROM selected_path
),
hops AS (
    SELECT
        generate_subscripts(hops, 1) AS pos,
        hops
    FROM nodes
)

/* ------------------------------------------------------------------
  3️⃣ hop별 장비 정보 + 최신 상태 결합
-------------------------------------------------------------------*/
SELECT
    h.pos,
    h.hops[h.pos] AS equipment_id,
    e.sitename,
    e.facilitytype,
    e.equipmenttype,
    s.rtt_ms,
    s.status_code,
    s.error_message
FROM hops h
LEFT JOIN tb_equipment_info e
    ON e.equipment_id = h.hops[h.pos]

LEFT JOIN LATERAL (
    SELECT
        ns.rtt_ms,
        ns.status_code,
        ns.error_message
    FROM tb_network_status ns
    WHERE ns.equipment_id = h.hops[h.pos]
    ORDER BY ns.check_time DESC
    LIMIT 1
) s ON true

ORDER BY h.pos;

$$;


--
-- TOC entry 787 (class 1255 OID 36115)
-- Name: fn_night_min_flow_stats(text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_night_min_flow_stats(p_sitename text, p_facilitytype text) RETURNS jsonb
    LANGUAGE plpgsql
    AS $$
DECLARE
    result JSONB;
BEGIN
    WITH 
    recent_365 AS (
        SELECT val
        FROM fn_night_min_flow_summary(p_sitename, p_facilitytype, (now() - interval '1 year')::date, now()::date)
    ),
    last_year_same_month AS (
        SELECT val
        FROM fn_night_min_flow_summary(
            p_sitename, p_facilitytype,
            date_trunc('month', now() - interval '1 year')::date,
            (date_trunc('month', now() - interval '1 year') + interval '1 month - 1 day')::date
        )
    ),
    yesterday_val AS (
        SELECT ROUND(AVG(val),2) AS min_val
        FROM fn_night_min_flow_summary(p_sitename, p_facilitytype, (now() - interval '1 day')::date, now()::date)
    ),
    calc AS (
        SELECT 
            ROUND(r.avg_365,2) AS avg_365,
            ROUND(r.stddev_365,2) AS stddev_365,
            ROUND(r.avg_365 - r.stddev_365,2) AS ci_min_365,
            ROUND(r.avg_365 + r.stddev_365,2) AS ci_max_365,
            ROUND(y.min_val - (r.avg_365 + r.stddev_365),2) AS exceed_365,

            ROUND(l.avg_30,2) AS avg_30,
            ROUND(l.stddev_30,2) AS stddev_30,
            ROUND(l.avg_30 - l.stddev_30,2) AS ci_min_30,
            ROUND(l.avg_30 + l.stddev_30,2) AS ci_max_30,
            ROUND(y.min_val - (l.avg_30 + l.stddev_30),2) AS exceed_30
        FROM 
            (SELECT AVG(val) AS avg_365, STDDEV_POP(val) AS stddev_365 FROM recent_365) r,
            (SELECT AVG(val) AS avg_30, STDDEV_POP(val) AS stddev_30 FROM last_year_same_month) l,
            yesterday_val y
    )
    SELECT jsonb_build_array(
        jsonb_build_object(
            '구분', '평균',
            '365일기준', calc.avg_365,
            '1년전 30일기준', calc.avg_30,
            '비고', '누수증감량 반영'
        ),
        jsonb_build_object(
            '구분', '표준편차',
            '365일기준', calc.stddev_365,
            '1년전 30일기준', calc.stddev_30,
            '비고', ''
        ),
        jsonb_build_object(
            '구분', '신뢰구간',
            '365일기준', ROUND(calc.ci_min_365,2)::text || ' ~ ' || ROUND(calc.ci_max_365,2)::text,
            '1년전 30일기준', ROUND(calc.ci_min_30,2)::text || ' ~ ' || ROUND(calc.ci_max_30,2)::text,
            '비고', '(평균±표준편차)구간'
        ),
        jsonb_build_object(
            '구분', '신뢰구간 초과량',
            '365일기준', calc.exceed_365,
            '1년전 30일기준', calc.exceed_30,
            '비고', '금일 - 신뢰구간 최대값'
        )
    )
    INTO result
    FROM calc;

    RETURN result;
END;
$$;


--
-- TOC entry 797 (class 1255 OID 37753)
-- Name: fn_night_min_flow_summary(text, text, date, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_night_min_flow_summary(p_sitename text, p_facilitytype text, p_start_date date, p_end_date date) RETURNS TABLE(log_time text, sitename text, facilitytype text, label text, tagsn text, datainfo text, datadesc text, unit text, val numeric)
    LANGUAGE plpgsql
    AS $$
DECLARE
    interval_days int;
    use_monthly   boolean := false;
BEGIN
    interval_days := (p_end_date - p_start_date);
    IF interval_days >= 365 THEN
        use_monthly := true;
    END IF;

    IF use_monthly THEN
        RETURN QUERY
        WITH params AS (
            SELECT (p_start_date)::timestamp AS ts_from,
                   ((p_end_date + INTERVAL '1 day'))::timestamp AS ts_to
        ),
        taglist AS (
            SELECT 
                elem->>'tagsn'  AS tagsn,
                elem->>'label'  AS label
            FROM tb_trend_catalog tc,
                 jsonb_array_elements(tc.meta->'items') elem
            WHERE tc.sitename     = (p_sitename)
              AND tc.facilitytype = (p_facilitytype)
              AND tc.trend_name   = '유량순시'
        ),
        hourly_ma AS (
            SELECT 
                r.tagsn,
                r.logtime,
                ROUND(
                    AVG(r.val::numeric) OVER (
                        PARTITION BY r.tagsn
                        ORDER BY r.logtime
                        RANGE BETWEEN INTERVAL '60 minutes' PRECEDING AND CURRENT ROW
                    ), 2
                ) AS ma_60min
            FROM tb_tag_raw_data r
            JOIN params p
              ON r.logtime >= p.ts_from 
             AND r.logtime <  p.ts_to
            JOIN taglist t 
              ON r.tagsn = t.tagsn
            WHERE EXTRACT(HOUR FROM r.logtime) >= 0
              AND EXTRACT(HOUR FROM r.logtime) < 6
        ),
        daily_night_min AS (
            SELECT
                hm.tagsn,
                time_bucket('1 day', hm.logtime) AS day,
                MIN(hm.ma_60min) AS night_min_flow
            FROM hourly_ma hm
            GROUP BY hm.tagsn, time_bucket('1 day', hm.logtime)
        ),
        monthly AS (
            SELECT 
                t.label::text         AS label,
                i.tagsn::text         AS tagsn,
                i.sitename::text      AS sitename,
                i.facilitytype::text  AS facilitytype,
                i.datainfo::text      AS datainfo,
                i.datadesc::text      AS datadesc,
                i.unit::text          AS unit,
                date_trunc('month', d.day)::date AS month,
                ROUND(MIN(d.night_min_flow)::numeric, 2) AS val
            FROM daily_night_min d
            JOIN taglist   t ON t.tagsn = d.tagsn
            JOIN tb_tag_info i ON i.tagsn = d.tagsn
            GROUP BY 
                i.sitename,
                i.facilitytype,
                i.datainfo,
                i.datadesc,
                i.unit,
                date_trunc('month', d.day),
                t.label,
                i.tagsn
        )
        SELECT 
            to_char(a.month, 'YYYY-MM') AS log_time,
            a.sitename,
            a.facilitytype,
            a.label,
            a.tagsn,
            a.datainfo,
            a.datadesc,
            a.unit,
            a.val
        FROM monthly a
        ORDER BY log_time, label;

    ELSE
        RETURN QUERY
        WITH params AS (
            SELECT (p_start_date)::timestamp AS ts_from,
                   ((p_end_date + INTERVAL '1 day'))::timestamp AS ts_to
        ),
        taglist AS (
            SELECT 
                elem->>'tagsn'  AS tagsn,
                elem->>'label'  AS label
            FROM tb_trend_catalog tc,
                 jsonb_array_elements(tc.meta->'items') elem
            WHERE tc.sitename     = (p_sitename)
              AND tc.facilitytype = (p_facilitytype)
              AND tc.trend_name   = '유량순시'
        ),
        hourly_ma AS (
            SELECT 
                r.tagsn,
                r.logtime,
                ROUND(
                    AVG(r.val::numeric) OVER (
                        PARTITION BY r.tagsn
                        ORDER BY r.logtime
                        RANGE BETWEEN INTERVAL '60 minutes' PRECEDING AND CURRENT ROW
                    ), 2
                ) AS ma_60min
            FROM tb_tag_raw_data r
            JOIN params p
              ON r.logtime >= p.ts_from 
             AND r.logtime <  p.ts_to
            JOIN taglist t 
              ON r.tagsn = t.tagsn
            WHERE EXTRACT(HOUR FROM r.logtime) >= 0
              AND EXTRACT(HOUR FROM r.logtime) < 6
        ),
        daily AS (
            SELECT 
                t.label::text         AS label,
                i.tagsn::text         AS tagsn,
                i.sitename::text      AS sitename,
                i.facilitytype::text  AS facilitytype,
                i.datainfo::text      AS datainfo,
                i.datadesc::text      AS datadesc,
                i.unit::text          AS unit,
                time_bucket('1 day', r.logtime)::date AS day,
                ROUND(MIN(ma_60min)::numeric, 2) AS val
            FROM hourly_ma r
            JOIN taglist   t ON t.tagsn = r.tagsn
            JOIN tb_tag_info i ON i.tagsn = r.tagsn
            GROUP BY 
                i.sitename,
                i.facilitytype,
                i.datainfo,
                i.datadesc,
                i.unit,
                time_bucket('1 day', r.logtime),
                t.label,
                i.tagsn
        )
        SELECT 
            to_char(a.day, 'YYYY-MM-DD') AS log_time,
            a.sitename,
            a.facilitytype,
            a.label,
            a.tagsn,
            a.datainfo,
            a.datadesc,
            a.unit,
            a.val
        FROM daily a
        ORDER BY log_time, label;
    END IF;
END;
$$;


--
-- TOC entry 794 (class 1255 OID 36374)
-- Name: fn_night_min_flow_summary_all(text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_night_min_flow_summary_all(p_sitename text, p_facilitytype text) RETURNS TABLE(site_name text, facility_type text, label text, datadesc text, avg_val numeric, unit text, period_type text)
    LANGUAGE plpgsql
    AS $$
BEGIN
  RETURN QUERY
  (
    -- ✅ (1) 최근 하루 : 현재 야간 최소유량
    SELECT *
    FROM (
      SELECT 
          n.out_sitename::text AS site_name,
          n.out_facilitytype::text AS facility_type,
          n.out_label::text AS label,
          n.out_datadesc::text AS datadesc,
          ROUND(n.out_val::numeric, 2) AS avg_val,
          n.out_unit::text AS unit,
          'current'::text AS period_type
      FROM fn_night_min_flow_summary(
          p_sitename,
          p_facilitytype,
          (now() - interval '1 day')::date,
          now()::date
      ) n
      ORDER BY n.log_time DESC
      LIMIT 1
    ) AS current_data

    UNION ALL

    -- ✅ (2) 연평균
    SELECT 
        n.out_sitename::text AS site_name,
        n.out_facilitytype::text AS facility_type,
        n.out_label::text AS label,
        n.out_datadesc::text AS datadesc,
        ROUND(AVG(n.out_val)::numeric, 2) AS avg_val,
        n.out_unit::text AS unit,
        'year'::text AS period_type
    FROM fn_night_min_flow_summary(
        p_sitename,
        p_facilitytype,
        (now() - interval '1 year')::date,
        now()::date
    ) n
    GROUP BY n.out_sitename, n.out_facilitytype, n.out_label, n.out_datadesc, n.out_unit

    UNION ALL

    -- ✅ (3) 월평균
    SELECT 
        n.out_sitename::text AS site_name,
        n.out_facilitytype::text AS facility_type,
        n.out_label::text AS label,
        n.out_datadesc::text AS datadesc,
        ROUND(AVG(n.out_val)::numeric, 2) AS avg_val,
        n.out_unit::text AS unit,
        'month'::text AS period_type
    FROM fn_night_min_flow_summary(
        p_sitename,
        p_facilitytype,
        (now() - interval '1 month')::date,
        now()::date
    ) n
    GROUP BY n.out_sitename, n.out_facilitytype, n.out_label, n.out_datadesc, n.out_unit
  );
END;
$$;


--
-- TOC entry 800 (class 1255 OID 176801)
-- Name: fn_ongoing_alarm_summary(text, text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_ongoing_alarm_summary(p_sitename text DEFAULT NULL::text, p_facilitytype text DEFAULT NULL::text, p_alarm_category text DEFAULT NULL::text) RETURNS TABLE(total_ongoing_alarms bigint, summary jsonb, alarm_list jsonb)
    LANGUAGE sql
    AS $$
WITH ongoing AS (
    SELECT
        row_number() OVER (ORDER BY r.sitename, r.alarm_category, r.alarm_msg) AS no,
        r.alarm_msg,
        r.alarm_value,
        r.alarm_category,
        r.sitename,
        r.facilitytype
    FROM tb_equipment_alarm_report r
    WHERE r.alarm_end_time IS NULL
      AND r.alarm_status::text ~ '진행'
      AND (p_sitename IS NULL OR r.sitename = p_sitename)
      AND (p_facilitytype IS NULL OR r.facilitytype = p_facilitytype)
      AND (p_alarm_category IS NULL OR r.alarm_category = p_alarm_category)
),
summary AS (
    SELECT alarm_category, COUNT(*) AS cnt
    FROM ongoing
    GROUP BY alarm_category
)
SELECT
    (SELECT COUNT(*) FROM ongoing) AS total_ongoing_alarms,
    (SELECT jsonb_agg(
        jsonb_build_object(
            'category', alarm_category,
            'count', cnt
        )
     ) FROM summary) AS summary,
    (SELECT jsonb_agg(
        jsonb_build_object(
            'no', no,
            'sitename', sitename,
            'facilitytype', facilitytype,
            'alarm_msg', alarm_msg,
            'alarm_value', alarm_value,
            'alarm_category', alarm_category
        )
     ) FROM ongoing) AS alarm_list;
$$;


--
-- TOC entry 792 (class 1255 OID 36191)
-- Name: fn_pressure_avg_summary(text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_pressure_avg_summary(p_sitename text, p_facilitytype text) RETURNS TABLE(sitename text, facilitytype text, datainfo text, datadesc text, month_avg numeric, year_avg numeric, unit text)
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY
    WITH
    -- ✅ 금월 평균 (태그별)
    month_avg_data AS (
        SELECT 
            i.tagsn,
            ROUND(AVG(r.val)::numeric, 2) AS month_avg
        FROM tb_tag_raw_data r
        JOIN tb_tag_info i ON i.tagsn = r.tagsn
        WHERE i.sitename = p_sitename
          AND i.facilitytype = p_facilitytype
          AND i.datainfo ILIKE '%압력%'
          AND i.tagtype = 'Analog Input'
          AND r.logtime >= date_trunc('month', now())
        GROUP BY i.tagsn
    ),
    -- ✅ 금년 평균 (태그별)
    year_avg_data AS (
        SELECT 
            i.tagsn,
            ROUND(AVG(r.val)::numeric, 2) AS year_avg
        FROM tb_tag_raw_data r
        JOIN tb_tag_info i ON i.tagsn = r.tagsn
        WHERE i.sitename = p_sitename
          AND i.facilitytype = p_facilitytype
          AND i.datainfo ILIKE '%압력%'
          AND i.tagtype = 'Analog Input'
          AND r.logtime >= date_trunc('year', now())
        GROUP BY i.tagsn
    )
    SELECT 
        i.sitename::text AS sitename,
        i.facilitytype::text AS facilitytype,
        i.datainfo::text AS datainfo,
        i.datadesc::text AS datadesc,
        COALESCE(m.month_avg, 0)::numeric AS month_avg,
        COALESCE(y.year_avg, 0)::numeric AS year_avg,
        'kgf/㎠'::text AS unit
    FROM tb_tag_info i
    LEFT JOIN month_avg_data m ON i.tagsn = m.tagsn
    LEFT JOIN year_avg_data y ON i.tagsn = y.tagsn
    WHERE i.sitename = p_sitename
      AND i.facilitytype = p_facilitytype
      AND i.datainfo ILIKE '%압력%'
      AND i.tagtype = 'Analog Input';
END;
$$;


--
-- TOC entry 799 (class 1255 OID 176147)
-- Name: fn_realtime_missing_summary(text, text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_realtime_missing_summary(p_sitename text, p_facilitytype text, p_datainfo_filter text DEFAULT NULL::text) RETURNS TABLE(result_sitename text, result_facilitytype text, result_good_cnt bigint, result_expect_cnt bigint, result_success_pct numeric, result_missing_pct numeric)
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY
    WITH items AS (
        SELECT 
            tc.sitename     AS block_group,
            tc.facilitytype AS facilitytype,
            elem->>'tagsn'  AS tagsn
        FROM tb_trend_catalog tc,
             jsonb_array_elements(tc.meta->'items') elem
        WHERE 
            tc.sitename <> '전체'
            AND (
                p_sitename IS NULL
                OR p_sitename = ''
                OR p_sitename = '전체'
                OR tc.sitename = p_sitename
            )
            AND (
                p_facilitytype IS NULL
                OR p_facilitytype = ''
                OR p_facilitytype = '전체'
                OR tc.facilitytype = p_facilitytype
                OR (p_facilitytype ILIKE '%블록%' AND tc.facilitytype ILIKE '%블록%')
            )
    ),
    -- datainfo 필터 (미지정 시 전체)
    items_with_info AS (
        SELECT
            it.block_group,
            it.facilitytype,
            it.tagsn
        FROM items it
        JOIN tb_tag_info ti
          ON ti.tagsn = it.tagsn
        WHERE
            p_datainfo_filter IS NULL
            OR p_datainfo_filter = ''
            OR ti.datainfo ~* p_datainfo_filter
    ),
    -- ✅ 당일 실제 수집 기준
    today AS (
        SELECT 
            r.tagsn,
            COUNT(*) FILTER (WHERE r.tag_stat = 'GOOD!!') AS good_cnt,
            COUNT(*) AS expect_cnt
        FROM tb_tag_raw_data r
        WHERE r.logtime >= date_trunc('day', now())
        GROUP BY r.tagsn
    ),
    tag_stats AS (
        SELECT 
            i.block_group AS sitename,
            i.facilitytype,
            COALESCE(SUM(t.good_cnt), 0)   AS total_good_cnt,
            COALESCE(SUM(t.expect_cnt), 0) AS total_expect_cnt
        FROM items_with_info i
        LEFT JOIN today t
          ON t.tagsn = i.tagsn
        GROUP BY i.block_group, i.facilitytype
    )
    SELECT 
        sitename::text AS result_sitename,
        facilitytype::text AS result_facilitytype,
        total_good_cnt::bigint AS result_good_cnt,
        total_expect_cnt::bigint AS result_expect_cnt,
        ROUND(
            (total_good_cnt::numeric / NULLIF(total_expect_cnt, 0)) * 100,
            2
        ) AS result_success_pct,
        ROUND(
            100 - (total_good_cnt::numeric / NULLIF(total_expect_cnt, 0)) * 100,
            2
        ) AS result_missing_pct
    FROM tag_stats
    ORDER BY result_sitename, result_facilitytype;
END;
$$;


--
-- TOC entry 795 (class 1255 OID 36377)
-- Name: fn_reservoir_level_overview(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_reservoir_level_overview(p_site text) RETURNS TABLE(site_name text, datainfo text, variance_status text, diff_percent numeric, avg_month numeric, avg_year numeric, alarm_high_water_level numeric, alarm_low_water_level numeric)
    LANGUAGE plpgsql
    AS $$
BEGIN
  RETURN QUERY
  WITH
  -- ① 현팅 여부 (5분 편차)
  variance AS (
      SELECT 
          v.sitename,
          v.facilitytype,
          v.datainfo,
          v.variance_status,
          v.diff_percent
      FROM v_reservoir_status_variance_5min v
      WHERE v.sitename = p_site
        AND v.facilitytype = '배수지'
  ),
  -- ② 배수지 평균 수위 (월평균, 연평균)
  avg_level AS (
      SELECT 
          a.sitename,
          a.datainfo,
          ROUND(AVG(CASE WHEN a.bucket_day >= now() - interval '1 month' THEN a.avg_daily_level END)::numeric, 2) AS avg_month,
          ROUND(AVG(CASE WHEN a.bucket_day >= now() - interval '1 year' THEN a.avg_daily_level END)::numeric, 2) AS avg_year
      FROM mv_reservoir_level_stats_all a
      WHERE a.sitename = p_site
      GROUP BY a.sitename, a.datainfo
  ),
  -- ③ 수위경보 기준
  alarm AS (
      SELECT 
          r.sitename,
          r.alarm_high_water_level,
          r.alarm_low_water_level
      FROM v_reservoir_info_status r
      WHERE r.sitename = p_site
  )
  SELECT 
      v.sitename::text AS site_name,  -- ✅ 캐스팅 추가
      v.datainfo::text AS datainfo,   -- ✅ 캐스팅 추가
      v.variance_status::text AS variance_status,
      v.diff_percent,
      a.avg_month,
      a.avg_year,
      al.alarm_high_water_level,
      al.alarm_low_water_level
  FROM variance v
  LEFT JOIN avg_level a 
    ON v.sitename = a.sitename AND v.datainfo = a.datainfo
  LEFT JOIN alarm al 
    ON v.sitename = al.sitename
  ORDER BY v.datainfo;
END;
$$;


--
-- TOC entry 789 (class 1255 OID 36178)
-- Name: fn_reservoir_level_summary(text, text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_reservoir_level_summary(p_sitename text, p_facilitytype text, p_datainfo text) RETURNS TABLE(log_time text, out_sitename text, out_facilitytype text, out_datainfo text, avg_latest numeric, latest_val numeric, avg_month numeric, avg_year numeric, unit text)
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY
    WITH 

    -- ✅ 최신값 추출 (태그별 가장 최근값)
    latest AS (
        SELECT DISTINCT ON (i.tagsn)
            i.sitename::text AS sitename,
            i.facilitytype::text AS facilitytype,
            i.datainfo::text AS datainfo,
            i.unit::text AS unit,
            r.val::numeric AS latest_val,
            r.logtime
        FROM tb_tag_info i
        JOIN tb_tag_raw_data r 
          ON r.tagsn = i.tagsn
        WHERE 
            (p_facilitytype IS NULL OR i.facilitytype = p_facilitytype)
            AND (p_sitename IS NULL OR i.sitename = p_sitename)
            AND (p_datainfo IS NULL OR i.datainfo ILIKE '%' || p_datainfo || '%')
            AND i.tagtype = 'Analog Input'
        ORDER BY i.tagsn, r.logtime DESC
    ),

    -- ✅ 1개월 / 1년 평균 수위 통계
    level_stats AS (
        SELECT 
            sitename::text AS sitename,
            datainfo::text AS datainfo,
            ROUND(AVG(CASE WHEN bucket_day >= now() - interval '1 month' THEN avg_daily_level END), 2) AS avg_month,
            ROUND(AVG(CASE WHEN bucket_day >= now() - interval '1 year'  THEN avg_daily_level END), 2) AS avg_year
        FROM mv_reservoir_level_stats_all
        WHERE 
            (p_sitename IS NULL OR sitename = p_sitename)
            AND (p_datainfo IS NULL OR datainfo ILIKE '%' || p_datainfo || '%')
        GROUP BY sitename, datainfo
    )

    SELECT 
        to_char(l.logtime, 'YYYY-MM-DD HH24:MI:SS') AS log_time,
        l.sitename AS out_sitename,
        l.facilitytype AS out_facilitytype,
        l.datainfo AS out_datainfo,
        ROUND(AVG(l.latest_val) OVER (), 2) AS avg_latest,
        ROUND(l.latest_val, 2) AS latest_val,
        ls.avg_month,
        ls.avg_year,
        l.unit
    FROM latest l
    LEFT JOIN level_stats ls 
      ON l.sitename = ls.sitename 
     AND l.datainfo = ls.datainfo
    ORDER BY l.datainfo;
END;
$$;


--
-- TOC entry 793 (class 1255 OID 36364)
-- Name: fn_reservoir_supply_hours(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_reservoir_supply_hours(p_site text DEFAULT NULL::text) RETURNS TABLE(site_name text, service_area text, zone_count integer, facility_capacity numeric, facility_unit text, monthly_inflow numeric, monthly_outflow numeric, supply_hours_monthly numeric, today_inflow numeric, today_outflow numeric, supply_hours_today numeric, avg_unit text, general_overview jsonb)
    LANGUAGE plpgsql
    AS $$
BEGIN
  RETURN QUERY
  WITH params AS (
      SELECT 
        date_trunc('day', now()) - interval '30 day' AS ts_from,
        date_trunc('day', now()) AS today
  ),
  tags AS (
      SELECT tagsn, sitename, datainfo
      FROM tb_tag_info
      WHERE facilitytype = '배수지'
        AND equipmenttype = '유량계'
        AND datainfo LIKE '%적산%'
        AND (p_site IS NULL OR p_site = '' OR sitename = p_site)
  ),
  daily AS (
      SELECT 
        t.sitename,
        t.datainfo,
        date_trunc('day', r.logtime)::date AS d,
        (MAX(r.val) - MIN(r.val)) / NULLIF(EXTRACT(HOUR FROM MAX(r.logtime))::int, 0) AS daily_avg_flow
      FROM tb_tag_raw_data r
      JOIN tags t ON r.tagsn = t.tagsn
      JOIN params p ON r.logtime >= p.ts_from AND r.logtime < p.today
      GROUP BY t.sitename, t.datainfo, date_trunc('day', r.logtime)
  ),
  monthly AS (
      SELECT 
        sitename AS sitekey,
        AVG(CASE WHEN datainfo LIKE '%유입%' THEN daily_avg_flow END) AS monthly_inflow,
        AVG(CASE WHEN datainfo LIKE '%유출%' THEN daily_avg_flow END) AS monthly_outflow
      FROM daily
      GROUP BY sitename
  ),
  today_raw AS (
      SELECT 
        t.sitename,
        t.datainfo,
        MAX(r.val) - MIN(r.val) AS usage,
        EXTRACT(HOUR FROM MAX(r.logtime))::int AS hours
      FROM tb_tag_raw_data r
      JOIN tags t ON r.tagsn = t.tagsn
      JOIN params p ON r.logtime >= p.today
      GROUP BY t.sitename, t.datainfo
  ),
  today_data AS (
      SELECT
        sitename AS sitekey,
        AVG(CASE WHEN datainfo LIKE '%유입%' THEN usage / NULLIF(hours,0) END) AS today_inflow,
        AVG(CASE WHEN datainfo LIKE '%유출%' THEN usage / NULLIF(hours,0) END) AS today_outflow
      FROM today_raw
      GROUP BY sitename
  )
  SELECT 
     v.sitename::text AS site_name,
     v.service_area::text AS service_area,  -- ✅ varchar → text 캐스팅
     v.zone_count,
     -- ✅ facility_capacity_m3 (최상위 키)
     ROUND(regexp_replace(v.general_overview ->> 'facility_capacity_m3', '[^0-9.]', '', 'g')::numeric, 2) AS facility_capacity,
     'm3'::text AS facility_unit,
     ROUND(m.monthly_inflow::numeric, 2) AS monthly_inflow,
     ROUND(m.monthly_outflow::numeric, 2) AS monthly_outflow,
     -- ✅ 월평균 급수시간 (용량 ÷ 월평균 유입·유출)
     ROUND((
       regexp_replace(v.general_overview ->> 'facility_capacity_m3', '[^0-9.]', '', 'g')::numeric
         / NULLIF((m.monthly_inflow + m.monthly_outflow)/2 ,0)
     )::numeric, 2) AS supply_hours_monthly,
     ROUND(t.today_inflow::numeric, 2) AS today_inflow,
     ROUND(t.today_outflow::numeric, 2) AS today_outflow,
     -- ✅ 오늘 급수시간 (용량 ÷ 오늘 유입·유출)
     ROUND((
       regexp_replace(v.general_overview ->> 'facility_capacity_m3', '[^0-9.]', '', 'g')::numeric
         / NULLIF((t.today_inflow + t.today_outflow)/2 ,0)
     )::numeric, 2) AS supply_hours_today,
     'm3/h'::text AS avg_unit,
     v.general_overview
  FROM v_reservoir_info_status v
  LEFT JOIN monthly m ON m.sitekey = v.sitename
  LEFT JOIN today_data t ON t.sitekey = v.sitename
  WHERE (p_site IS NULL OR p_site = '' OR v.sitename = p_site);
END;
$$;


--
-- TOC entry 772 (class 1255 OID 25795)
-- Name: fn_set_block_status_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_set_block_status_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


--
-- TOC entry 773 (class 1255 OID 25847)
-- Name: fn_set_equipment_info_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_set_equipment_info_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
begin
    new.updated_at = now();
    return new;
end;
$$;


--
-- TOC entry 774 (class 1255 OID 25929)
-- Name: fn_set_network_info_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_set_network_info_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;


--
-- TOC entry 776 (class 1255 OID 25995)
-- Name: fn_set_network_link_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_set_network_link_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
begin
    new.updated_at = now();
    return new;
end;
$$;


--
-- TOC entry 771 (class 1255 OID 25763)
-- Name: fn_set_prf_status_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_set_prf_status_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


--
-- TOC entry 786 (class 1255 OID 36096)
-- Name: fn_tag_daily_summary(date, date, text, text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_tag_daily_summary(p_start_date date, p_end_date date, p_sitename text DEFAULT NULL::text, p_facilitytype text DEFAULT NULL::text, p_datainfo_filter text DEFAULT NULL::text) RETURNS TABLE(result_log_date date, result_sitename text, result_facilitytype text, total_good_cnt bigint, total_expect_cnt bigint, total_missing_cnt bigint, good_rate_pct numeric, missing_rate_pct numeric)
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY
    WITH tagged AS (
        SELECT 
            s.d::date AS log_dt,
            i.sitename::text AS sitename,
            i.facilitytype::text AS facilitytype,
            i.datainfo::text AS datainfo,
            s.good_cnt,
            s.expect_cnt,
            s.missing_record_cnt
        FROM mv_tag_daily_status s
        JOIN tb_tag_info i
          ON s.tagsn = i.tagsn
        WHERE 
            (p_sitename IS NULL
             OR p_sitename = ''
             OR p_sitename = '전체'
             OR i.sitename = p_sitename)

            AND (p_facilitytype IS NULL
                 OR p_facilitytype = ''
                 OR p_facilitytype = '전체'
                 OR i.facilitytype = p_facilitytype)

            -- ✅ datainfo 키워드 / 정규식 검색
            AND (
                p_datainfo_filter IS NULL
                OR p_datainfo_filter = ''
                OR i.datainfo ~* p_datainfo_filter
            )

            AND s.d BETWEEN p_start_date AND p_end_date
    ),
    daily_summary AS (
        SELECT 
            log_dt,
            sitename,
            facilitytype,
            SUM(good_cnt)::bigint AS total_good_cnt,
            SUM(expect_cnt)::bigint AS total_expect_cnt,
            SUM(missing_record_cnt)::bigint AS total_missing_cnt,
            ROUND(
                (SUM(good_cnt)::numeric / NULLIF(SUM(expect_cnt), 0)) * 100,
                2
            ) AS good_rate_pct,
            ROUND(
                (SUM(missing_record_cnt)::numeric / NULLIF(SUM(expect_cnt), 0)) * 100,
                2
            ) AS missing_rate_pct
        FROM tagged
        GROUP BY log_dt, sitename, facilitytype
    )
    SELECT 
        ds.log_dt         AS result_log_date,
        ds.sitename       AS result_sitename,
        ds.facilitytype   AS result_facilitytype,
        ds.total_good_cnt,
        ds.total_expect_cnt,
        ds.total_missing_cnt,
        ds.good_rate_pct,
        ds.missing_rate_pct
    FROM daily_summary ds
    ORDER BY ds.log_dt DESC, ds.sitename;
END;
$$;


--
-- TOC entry 785 (class 1255 OID 36088)
-- Name: fn_today_outflow(text, text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_today_outflow(p_sitename text, p_facilitytype text, p_direction text DEFAULT NULL::text) RETURNS TABLE(sitename text, facilitytype text, datainfo text, unit text, today_outflow numeric)
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY
    SELECT
        q.sitename::text,
        q.facilitytype::text,
        q.datainfo::text,
        q.unit::text,
        q.today_outflow::numeric
    FROM (
        WITH taglist AS (
            SELECT 
                t.tagsn, 
                t.sitename, 
                t.facilitytype, 
                t.datainfo, 
                t.unit
            FROM tb_tag_info t
            WHERE t.sitename = p_sitename
              AND t.facilitytype = p_facilitytype
              AND t.datainfo ILIKE '%적산%'
              AND (
                    p_direction IS NULL
                    OR t.datainfo ILIKE '%' || p_direction || '%'
              )
        ),
        today_range AS (
            SELECT 
                CURRENT_DATE::timestamp AS ts_from,
                now()::timestamp        AS ts_to
        ),
        base AS (
            SELECT
                tg.tagsn,
                tg.sitename     AS col_sitename,
                tg.facilitytype AS col_facilitytype,
                tg.datainfo     AS col_datainfo,
                tg.unit         AS col_unit,
                r.logtime,
                r.val
            FROM tb_tag_raw_data r
            JOIN taglist tg ON tg.tagsn = r.tagsn
            WHERE r.logtime >= (SELECT ts_from FROM today_range)
              AND r.logtime <= (SELECT ts_to   FROM today_range)
              AND r.val IS NOT NULL
              AND r.val <> 0
        ),
        deltas AS (
            SELECT
                col_sitename,
                col_facilitytype,
                col_datainfo,
                col_unit,
                tagsn,
                logtime,
                val - LAG(val) OVER (
                    PARTITION BY tagsn
                    ORDER BY logtime
                ) AS delta
            FROM base
        )
        SELECT
            col_sitename     AS sitename,
            col_facilitytype AS facilitytype,
            col_datainfo     AS datainfo,
            col_unit         AS unit,
            ROUND(
                SUM(
                    CASE
                        WHEN delta IS NULL THEN 0
                        WHEN delta < 0 THEN 0
                        ELSE delta
                    END
                )::numeric,
                2
            ) AS today_outflow
        FROM deltas
        GROUP BY
            col_sitename,
            col_facilitytype,
            col_datainfo,
            col_unit
    ) q;
END;
$$;


--
-- TOC entry 784 (class 1255 OID 36083)
-- Name: fn_today_outflow_all(date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_today_outflow_all(p_target_date date) RETURNS TABLE(result_sitename text, result_facilitytype text, result_today_outflow numeric, result_is_total integer)
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY
    WITH taglist AS (
        SELECT
            t.tagsn,
            t.sitename,
            t.facilitytype
        FROM tb_tag_info t
        WHERE t.facilitytype = '배수지'
          AND t.datainfo ILIKE '%유출%적산%'
    ),
    date_range AS (
        SELECT
            p_target_date::timestamp AS ts_from,
            (p_target_date + INTERVAL '1 day')::timestamp AS ts_to
    ),
    base AS (
        SELECT
            tg.sitename,
            tg.facilitytype,
            tg.tagsn,
            r.logtime,
            r.val
        FROM tb_tag_raw_data r
        JOIN taglist tg ON tg.tagsn = r.tagsn
        WHERE r.logtime >= (SELECT ts_from FROM date_range)
          AND r.logtime <  (SELECT ts_to   FROM date_range)
          AND r.val IS NOT NULL
          AND r.val <> 0
    ),
    deltas AS (
        SELECT
            sitename,
            facilitytype,
            tagsn,
            logtime,
            val - LAG(val) OVER (
                PARTITION BY tagsn
                ORDER BY logtime
            ) AS delta
        FROM base
    ),
    per_tag AS (
        SELECT
            sitename,
            facilitytype,
            tagsn,
            SUM(
                CASE
                    WHEN delta IS NULL THEN 0
                    WHEN delta < 0 THEN 0
                    ELSE delta
                END
            ) AS today_outflow
        FROM deltas
        GROUP BY sitename, facilitytype, tagsn
    ),
    per_site AS (
        SELECT
            sitename,
            facilitytype,
            ROUND(SUM(today_outflow)::numeric, 2) AS today_outflow
        FROM per_tag
        GROUP BY sitename, facilitytype
    ),
    total AS (
        SELECT
            '합계'::text AS sitename,
            '배수지'::text AS facilitytype,
            ROUND(SUM(today_outflow)::numeric, 2) AS today_outflow,
            1 AS is_total
        FROM per_site
    )
    SELECT
        ps.sitename::text        AS result_sitename,
        ps.facilitytype::text   AS result_facilitytype,
        ps.today_outflow        AS result_today_outflow,
        0                        AS result_is_total
    FROM per_site ps

    UNION ALL

    SELECT
        t.sitename,
        t.facilitytype,
        t.today_outflow,
        t.is_total
    FROM total t

    ORDER BY result_is_total, result_sitename;
END;
$$;


--
-- TOC entry 788 (class 1255 OID 36149)
-- Name: fn_trend_period_summary(text, text, text, anyelement, anyelement); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_trend_period_summary(p_site text, p_facilitytype text, p_trend_name text, p_ts_from anyelement DEFAULT (now() - '1 mon'::interval), p_ts_to anyelement DEFAULT now()) RETURNS TABLE(log_time text, sitename text, facilitytype text, label text, tagsn text, tagtype text, val numeric)
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_from timestamp;
    v_to   timestamp;
    v_trend_name text;
BEGIN
    -- timestamptz → timestamp 정규화
    v_from := p_ts_from::timestamp;
    v_to   := p_ts_to::timestamp;

    -- trend_name 기본값
    v_trend_name := COALESCE(NULLIF(p_trend_name, ''), '전체');

    RETURN QUERY
    WITH catalog_items AS (
        SELECT
            tc.sitename,
            tc.facilitytype,
            (i->>'tagsn')::text AS tagsn,
            (i->>'label')::text AS label,
            ti.tagtype::text AS tagtype
        FROM tb_trend_catalog tc
        CROSS JOIN LATERAL jsonb_array_elements(tc.meta->'items') AS i
        JOIN tb_tag_info ti
          ON ti.tagsn = (i->>'tagsn')::text
        WHERE tc.sitename = p_site
          AND tc.facilitytype = p_facilitytype
          AND (
              v_trend_name = '전체'
              OR tc.trend_name ~ v_trend_name
          )
    ),

    -- 🔹 태그별 원본 데이터 건수
    tag_counts AS (
        SELECT
            r.tagsn,
            COUNT(*) AS cnt
        FROM tb_tag_raw_data r
        JOIN catalog_items ci ON ci.tagsn = r.tagsn
        WHERE r.logtime >= v_from
          AND r.logtime <  v_to
        GROUP BY r.tagsn
    ),

    -- 🔹 전체 태그 중 최대 건수 기준으로 단일 bucket 결정
    bucket_plan AS (
        SELECT
            GREATEST(1, CEIL(MAX(cnt) / 2000.0))::int AS bucket_mins
        FROM tag_counts
    ),

    ranked AS (
        SELECT
            time_bucket(
                make_interval(mins => bp.bucket_mins),
                r.logtime,
                v_from                    -- ⭐ origin 고정
            ) AS ts,
            ci.sitename,
            ci.facilitytype,
            ci.label,
            ci.tagsn,
            ci.tagtype,
            r.val::numeric AS val,
            ROW_NUMBER() OVER (
                PARTITION BY
                    time_bucket(
                        make_interval(mins => bp.bucket_mins),
                        r.logtime,
                        v_from
                    ),
                    r.tagsn
                ORDER BY r.logtime
            ) AS rn
        FROM tb_tag_raw_data r
        JOIN catalog_items ci ON ci.tagsn = r.tagsn
        CROSS JOIN bucket_plan bp
        WHERE r.logtime >= v_from
          AND r.logtime <  v_to
    )

    SELECT
        to_char(rk.ts, 'YYYY-MM-DD HH24:MI:SS') AS log_time,
        rk.sitename,
        rk.facilitytype,
        rk.label,
        rk.tagsn,
        rk.tagtype,
        rk.val
    FROM ranked rk
    WHERE rk.rn = 1
    ORDER BY rk.ts, rk.label;

END;
$$;


--
-- TOC entry 796 (class 1255 OID 37264)
-- Name: fn_update_timestamp(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_update_timestamp() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;


--
-- TOC entry 779 (class 1255 OID 35144)
-- Name: refresh_mv_block_usage_ratio_view(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.refresh_mv_block_usage_ratio_view() RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
  RAISE NOTICE 'Refreshing mv_block_usage_ratio_view...';
  REFRESH MATERIALIZED VIEW CONCURRENTLY mv_block_usage_ratio_view;
END;
$$;


--
-- TOC entry 778 (class 1255 OID 27144)
-- Name: trg_check_meta_items(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trg_check_meta_items() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
  elem jsonb;
  cnt  int;
BEGIN
  -- items 배열 존재/형태 체크
  IF NOT (NEW.meta ? 'items') OR jsonb_typeof(NEW.meta->'items') <> 'array' THEN
    RAISE EXCEPTION 'meta.items must be an array';
  END IF;

  -- 필수 키(tagsn, unit) 존재 체크
  FOR elem IN SELECT * FROM jsonb_array_elements(NEW.meta->'items')
  LOOP
    IF NOT elem ? 'tagsn' OR NOT elem ? 'unit' THEN
      RAISE EXCEPTION 'each item must contain tagsn and unit';
    END IF;
  END LOOP;

  -- 동일 트렌드 내 tagsn 중복 방지
  SELECT COUNT(*) INTO cnt
  FROM (
    SELECT (x->>'tagsn') AS tagsn
    FROM jsonb_array_elements(NEW.meta->'items') x
    GROUP BY 1
    HAVING COUNT(*) > 1
  ) dups;
  IF cnt > 0 THEN
    RAISE EXCEPTION 'duplicate tagsn detected in meta.items';
  END IF;

  RETURN NEW;
END $$;


--
-- TOC entry 777 (class 1255 OID 27142)
-- Name: trg_set_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trg_set_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN NEW.updated_at := now(); RETURN NEW; END $$;


--
-- TOC entry 775 (class 1255 OID 25972)
-- Name: trg_update_protocol_lookup_timestamp(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trg_update_protocol_lookup_timestamp() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;


--
-- TOC entry 770 (class 1255 OID 25656)
-- Name: update_last_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_last_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.last_updated_at = NOW();  -- 현재 시간을 last_updated_at 컬럼에 설정
    RETURN NEW;  -- 수정된 레코드를 반환
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- TOC entry 320 (class 1259 OID 26482)
-- Name: tb_tag_info; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_tag_info (
    tagsn character varying NOT NULL,
    tagtype character varying NOT NULL,
    sitename character varying NOT NULL,
    facilitytype character varying,
    equipmenttype character varying,
    datainfo character varying NOT NULL,
    datadesc character varying NOT NULL,
    unit character varying,
    alarm_tag_yn numeric DEFAULT 0
);


--
-- TOC entry 4681 (class 0 OID 0)
-- Dependencies: 320
-- Name: COLUMN tb_tag_info.tagsn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_tag_info.tagsn IS 'SCADA 시스템 태그';


--
-- TOC entry 4682 (class 0 OID 0)
-- Dependencies: 320
-- Name: COLUMN tb_tag_info.tagtype; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_tag_info.tagtype IS '태그 타입';


--
-- TOC entry 4683 (class 0 OID 0)
-- Dependencies: 320
-- Name: COLUMN tb_tag_info.sitename; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_tag_info.sitename IS '현장명';


--
-- TOC entry 4684 (class 0 OID 0)
-- Dependencies: 320
-- Name: COLUMN tb_tag_info.facilitytype; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_tag_info.facilitytype IS '시설구분';


--
-- TOC entry 4685 (class 0 OID 0)
-- Dependencies: 320
-- Name: COLUMN tb_tag_info.equipmenttype; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_tag_info.equipmenttype IS '장비구분';


--
-- TOC entry 4686 (class 0 OID 0)
-- Dependencies: 320
-- Name: COLUMN tb_tag_info.datainfo; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_tag_info.datainfo IS '데이터 정보';


--
-- TOC entry 4687 (class 0 OID 0)
-- Dependencies: 320
-- Name: COLUMN tb_tag_info.datadesc; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_tag_info.datadesc IS '설명(참고용)';


--
-- TOC entry 4688 (class 0 OID 0)
-- Dependencies: 320
-- Name: COLUMN tb_tag_info.alarm_tag_yn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_tag_info.alarm_tag_yn IS '알람태그 설정 (0:해제, 1:설정)';


--
-- TOC entry 332 (class 1259 OID 35060)
-- Name: tb_tag_raw_data; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_tag_raw_data (
    tagsn character varying NOT NULL,
    logtime timestamp without time zone NOT NULL,
    val numeric,
    tag_stat character varying(10)
);


--
-- TOC entry 4689 (class 0 OID 0)
-- Dependencies: 332
-- Name: COLUMN tb_tag_raw_data.tagsn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_tag_raw_data.tagsn IS '태그명';


--
-- TOC entry 4690 (class 0 OID 0)
-- Dependencies: 332
-- Name: COLUMN tb_tag_raw_data.logtime; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_tag_raw_data.logtime IS '기록 시간';


--
-- TOC entry 4691 (class 0 OID 0)
-- Dependencies: 332
-- Name: COLUMN tb_tag_raw_data.val; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_tag_raw_data.val IS '태그 데이터';


--
-- TOC entry 4692 (class 0 OID 0)
-- Dependencies: 332
-- Name: COLUMN tb_tag_raw_data.tag_stat; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_tag_raw_data.tag_stat IS '통신 상태';


--
-- TOC entry 318 (class 1259 OID 26013)
-- Name: tb_network_status; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_network_status (
    check_time timestamp with time zone NOT NULL,
    equipment_id character varying(64) NOT NULL,
    protocol character varying(64),
    ip_address text,
    is_alive boolean,
    status_code character varying(64),
    protocol_status character varying(64),
    rtt_ms integer,
    interface_type character varying(64),
    error_message text
);


--
-- TOC entry 4693 (class 0 OID 0)
-- Dependencies: 318
-- Name: TABLE tb_network_status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_network_status IS '장비별 통신 상태 모니터링 결과 테이블';


--
-- TOC entry 4694 (class 0 OID 0)
-- Dependencies: 318
-- Name: COLUMN tb_network_status.check_time; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_network_status.check_time IS '상태 체크 시각';


--
-- TOC entry 4695 (class 0 OID 0)
-- Dependencies: 318
-- Name: COLUMN tb_network_status.equipment_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_network_status.equipment_id IS '장비 고유 ID (tb_equipment_info 참조)';


--
-- TOC entry 4696 (class 0 OID 0)
-- Dependencies: 318
-- Name: COLUMN tb_network_status.protocol; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_network_status.protocol IS '통신 프로토콜 (예: GLOFA, MODBUS RTU)';


--
-- TOC entry 4697 (class 0 OID 0)
-- Dependencies: 318
-- Name: COLUMN tb_network_status.ip_address; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_network_status.ip_address IS 'IP 주소, DDNS 포함';


--
-- TOC entry 4698 (class 0 OID 0)
-- Dependencies: 318
-- Name: COLUMN tb_network_status.is_alive; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_network_status.is_alive IS '통신 가능 여부 (true/false)';


--
-- TOC entry 4699 (class 0 OID 0)
-- Dependencies: 318
-- Name: COLUMN tb_network_status.status_code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_network_status.status_code IS '상태 코드 (예: 정상, 응답없음)';


--
-- TOC entry 4700 (class 0 OID 0)
-- Dependencies: 318
-- Name: COLUMN tb_network_status.protocol_status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_network_status.protocol_status IS '프로토콜 상태 (예: 정상, CRC 오류)';


--
-- TOC entry 4701 (class 0 OID 0)
-- Dependencies: 318
-- Name: COLUMN tb_network_status.rtt_ms; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_network_status.rtt_ms IS 'ICMP 왕복 응답 시간(ms)';


--
-- TOC entry 4702 (class 0 OID 0)
-- Dependencies: 318
-- Name: COLUMN tb_network_status.interface_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_network_status.interface_type IS '통신 방식 (Ethernet, RS485 등)';


--
-- TOC entry 4703 (class 0 OID 0)
-- Dependencies: 318
-- Name: COLUMN tb_network_status.error_message; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_network_status.error_message IS '통신 실패 시 에러 메시지 (예: timeout)';


--
-- TOC entry 317 (class 1259 OID 25975)
-- Name: tb_network_link; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_network_link (
    source_equipment_id character varying(64) NOT NULL,
    target_equipment_id character varying(64) NOT NULL,
    link_protocol text NOT NULL,
    link_port integer,
    link_device_interface text NOT NULL,
    link_role text,
    description text,
    meta jsonb DEFAULT '{}'::jsonb,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now()
);


--
-- TOC entry 4704 (class 0 OID 0)
-- Dependencies: 317
-- Name: TABLE tb_network_link; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_network_link IS '장비 간 물리/논리 네트워크 링크';


--
-- TOC entry 4705 (class 0 OID 0)
-- Dependencies: 317
-- Name: COLUMN tb_network_link.source_equipment_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_network_link.source_equipment_id IS '소스 장비 ID';


--
-- TOC entry 4706 (class 0 OID 0)
-- Dependencies: 317
-- Name: COLUMN tb_network_link.target_equipment_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_network_link.target_equipment_id IS '타겟 장비 ID';


--
-- TOC entry 4707 (class 0 OID 0)
-- Dependencies: 317
-- Name: COLUMN tb_network_link.link_protocol; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_network_link.link_protocol IS '링크 프로토콜 코드 (tb_protocol_lookup 참조)';


--
-- TOC entry 4708 (class 0 OID 0)
-- Dependencies: 317
-- Name: COLUMN tb_network_link.link_port; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_network_link.link_port IS '현장 설비 통신 서비스 Port';


--
-- TOC entry 4709 (class 0 OID 0)
-- Dependencies: 317
-- Name: COLUMN tb_network_link.link_device_interface; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_network_link.link_device_interface IS '장비 간 인터페이스 방식';


--
-- TOC entry 4710 (class 0 OID 0)
-- Dependencies: 317
-- Name: COLUMN tb_network_link.link_role; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_network_link.link_role IS '링크 역할';


--
-- TOC entry 4711 (class 0 OID 0)
-- Dependencies: 317
-- Name: COLUMN tb_network_link.description; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_network_link.description IS '링크 설명';


--
-- TOC entry 4712 (class 0 OID 0)
-- Dependencies: 317
-- Name: COLUMN tb_network_link.meta; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_network_link.meta IS '링크 메타 데이터(JSONB)';


--
-- TOC entry 4713 (class 0 OID 0)
-- Dependencies: 317
-- Name: COLUMN tb_network_link.created_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_network_link.created_at IS '생성일시';


--
-- TOC entry 4714 (class 0 OID 0)
-- Dependencies: 317
-- Name: COLUMN tb_network_link.updated_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_network_link.updated_at IS '수정일시';


--
-- TOC entry 404 (class 1259 OID 173479)
-- Name: cagg_5min_raw_stats_ai; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.cagg_5min_raw_stats_ai AS
 SELECT bucket,
    tagsn,
    sample_cnt,
    min_val,
    max_val
   FROM _timescaledb_internal._materialized_hypertable_30;


--
-- TOC entry 394 (class 1259 OID 98950)
-- Name: cagg_daily_pressure; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.cagg_daily_pressure AS
 SELECT bucket,
    tagsn,
    sitename,
    facilitytype,
    min_val,
    max_val,
    avg_val,
    unit
   FROM _timescaledb_internal._materialized_hypertable_21;


--
-- TOC entry 358 (class 1259 OID 35650)
-- Name: mv_block_daily_flow; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.mv_block_daily_flow AS
 SELECT sitename,
    facilitytype,
    d,
    max_val,
    min_nonzero
   FROM _timescaledb_internal._materialized_hypertable_17;


--
-- TOC entry 343 (class 1259 OID 35294)
-- Name: mv_reservoir_daily_flow; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.mv_reservoir_daily_flow AS
 SELECT sitename,
    facilitytype,
    flow_type,
    d,
    max_val,
    min_nonzero,
    sample_cnt
   FROM _timescaledb_internal._materialized_hypertable_8;


--
-- TOC entry 349 (class 1259 OID 35454)
-- Name: mv_reservoir_level_stats_all; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.mv_reservoir_level_stats_all AS
 SELECT sitename,
    facilitytype,
    datainfo,
    bucket_day,
    avg_daily_level,
    max_daily_level,
    min_daily_level
   FROM _timescaledb_internal._materialized_hypertable_13;


--
-- TOC entry 338 (class 1259 OID 35258)
-- Name: mv_tag_daily_status; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.mv_tag_daily_status AS
 SELECT tagsn,
    d,
    good_cnt,
    bad_or_null_cnt,
    total_cnt,
    expect_cnt,
    missing_record_cnt
   FROM _timescaledb_internal._materialized_hypertable_7;


--
-- TOC entry 248 (class 1259 OID 24781)
-- Name: seq_access_log_id; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.seq_access_log_id
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 331 (class 1259 OID 33896)
-- Name: seq_chat_ask_group_id; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.seq_chat_ask_group_id
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 328 (class 1259 OID 32668)
-- Name: seq_chat_ask_image_id; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.seq_chat_ask_image_id
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 330 (class 1259 OID 33895)
-- Name: seq_chat_ask_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.seq_chat_ask_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 329 (class 1259 OID 32669)
-- Name: seq_chat_bot_image_id; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.seq_chat_bot_image_id
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 249 (class 1259 OID 24783)
-- Name: seq_faq_id; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.seq_faq_id
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 250 (class 1259 OID 24784)
-- Name: seq_file_upload_id; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.seq_file_upload_id
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 251 (class 1259 OID 24785)
-- Name: seq_node_id; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.seq_node_id
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 229 (class 1259 OID 24590)
-- Name: tb_access_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_access_log (
    log_id bigint NOT NULL,
    region character varying(10),
    user_id character varying(50),
    request_path text,
    request_method character varying(10),
    client_ip character varying(45),
    device_type character varying(10),
    browser character varying(30),
    os character varying(30),
    user_agent text,
    access_time timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- TOC entry 4715 (class 0 OID 0)
-- Dependencies: 229
-- Name: TABLE tb_access_log; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_access_log IS '접속 로그';


--
-- TOC entry 4716 (class 0 OID 0)
-- Dependencies: 229
-- Name: COLUMN tb_access_log.log_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_access_log.log_id IS '로그ID';


--
-- TOC entry 4717 (class 0 OID 0)
-- Dependencies: 229
-- Name: COLUMN tb_access_log.region; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_access_log.region IS '지역';


--
-- TOC entry 4718 (class 0 OID 0)
-- Dependencies: 229
-- Name: COLUMN tb_access_log.user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_access_log.user_id IS '사용자ID';


--
-- TOC entry 4719 (class 0 OID 0)
-- Dependencies: 229
-- Name: COLUMN tb_access_log.request_path; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_access_log.request_path IS '요청path';


--
-- TOC entry 4720 (class 0 OID 0)
-- Dependencies: 229
-- Name: COLUMN tb_access_log.request_method; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_access_log.request_method IS '요청함수';


--
-- TOC entry 4721 (class 0 OID 0)
-- Dependencies: 229
-- Name: COLUMN tb_access_log.client_ip; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_access_log.client_ip IS '접속IP';


--
-- TOC entry 4722 (class 0 OID 0)
-- Dependencies: 229
-- Name: COLUMN tb_access_log.device_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_access_log.device_type IS '장비타입';


--
-- TOC entry 4723 (class 0 OID 0)
-- Dependencies: 229
-- Name: COLUMN tb_access_log.browser; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_access_log.browser IS '브라우저';


--
-- TOC entry 4724 (class 0 OID 0)
-- Dependencies: 229
-- Name: COLUMN tb_access_log.os; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_access_log.os IS 'OS';


--
-- TOC entry 4725 (class 0 OID 0)
-- Dependencies: 229
-- Name: COLUMN tb_access_log.access_time; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_access_log.access_time IS '접속시간';


--
-- TOC entry 385 (class 1259 OID 38135)
-- Name: tb_ai_chat_ask; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_ai_chat_ask (
    region character varying(10) NOT NULL,
    user_id character varying(40) NOT NULL,
    ask_seq bigint NOT NULL,
    ask_dt character varying(8) NOT NULL,
    ask_tm character varying(6) NOT NULL,
    chat_role character varying(10) DEFAULT 'USER'::character varying NOT NULL,
    ask_req character varying(10) DEFAULT 'CHAT'::character varying NOT NULL,
    group_id character varying(50) NOT NULL,
    ask_msg text,
    image_data text,
    response_yn character varying(1) DEFAULT 'N'::character varying NOT NULL,
    success_yn character varying(1) DEFAULT 'S'::character varying NOT NULL,
    del_yn character varying(1) DEFAULT 'N'::character varying NOT NULL,
    create_dt timestamp without time zone DEFAULT now() NOT NULL,
    create_user_id character varying(50) NOT NULL,
    update_dt timestamp without time zone DEFAULT now() NOT NULL,
    update_user_id character varying(50) NOT NULL
);


--
-- TOC entry 4726 (class 0 OID 0)
-- Dependencies: 385
-- Name: TABLE tb_ai_chat_ask; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_ai_chat_ask IS '챗봇AI질문내역';


--
-- TOC entry 4727 (class 0 OID 0)
-- Dependencies: 385
-- Name: COLUMN tb_ai_chat_ask.region; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask.region IS '지역';


--
-- TOC entry 4728 (class 0 OID 0)
-- Dependencies: 385
-- Name: COLUMN tb_ai_chat_ask.user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask.user_id IS '사용자ID';


--
-- TOC entry 4729 (class 0 OID 0)
-- Dependencies: 385
-- Name: COLUMN tb_ai_chat_ask.ask_seq; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask.ask_seq IS '질문순번';


--
-- TOC entry 4730 (class 0 OID 0)
-- Dependencies: 385
-- Name: COLUMN tb_ai_chat_ask.ask_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask.ask_dt IS '질문일자';


--
-- TOC entry 4731 (class 0 OID 0)
-- Dependencies: 385
-- Name: COLUMN tb_ai_chat_ask.ask_tm; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask.ask_tm IS '질문시간';


--
-- TOC entry 4732 (class 0 OID 0)
-- Dependencies: 385
-- Name: COLUMN tb_ai_chat_ask.chat_role; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask.chat_role IS '챗구분(USER:질문자)';


--
-- TOC entry 4733 (class 0 OID 0)
-- Dependencies: 385
-- Name: COLUMN tb_ai_chat_ask.ask_req; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask.ask_req IS '질문요청구분(CHAT:일반질문,ALARM:alarm관련질문)';


--
-- TOC entry 4734 (class 0 OID 0)
-- Dependencies: 385
-- Name: COLUMN tb_ai_chat_ask.group_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask.group_id IS '챗그룹ID';


--
-- TOC entry 4735 (class 0 OID 0)
-- Dependencies: 385
-- Name: COLUMN tb_ai_chat_ask.ask_msg; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask.ask_msg IS '질문내용';


--
-- TOC entry 4736 (class 0 OID 0)
-- Dependencies: 385
-- Name: COLUMN tb_ai_chat_ask.image_data; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask.image_data IS '이미지텍스트Data';


--
-- TOC entry 4737 (class 0 OID 0)
-- Dependencies: 385
-- Name: COLUMN tb_ai_chat_ask.response_yn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask.response_yn IS '응답여부';


--
-- TOC entry 4738 (class 0 OID 0)
-- Dependencies: 385
-- Name: COLUMN tb_ai_chat_ask.success_yn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask.success_yn IS '성공여부(S:응답성공,F:응답실패,T:Trand처리실패,N:관망질문아님)';


--
-- TOC entry 4739 (class 0 OID 0)
-- Dependencies: 385
-- Name: COLUMN tb_ai_chat_ask.del_yn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask.del_yn IS '삭제여부';


--
-- TOC entry 4740 (class 0 OID 0)
-- Dependencies: 385
-- Name: COLUMN tb_ai_chat_ask.create_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask.create_dt IS '생성일자';


--
-- TOC entry 4741 (class 0 OID 0)
-- Dependencies: 385
-- Name: COLUMN tb_ai_chat_ask.create_user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask.create_user_id IS '생성자';


--
-- TOC entry 4742 (class 0 OID 0)
-- Dependencies: 385
-- Name: COLUMN tb_ai_chat_ask.update_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask.update_dt IS '수정일자';


--
-- TOC entry 4743 (class 0 OID 0)
-- Dependencies: 385
-- Name: COLUMN tb_ai_chat_ask.update_user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask.update_user_id IS '수정자';


--
-- TOC entry 230 (class 1259 OID 24598)
-- Name: tb_ai_chat_ask_group; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_ai_chat_ask_group (
    region character varying(10) NOT NULL,
    user_id character varying(40) NOT NULL,
    group_id character varying(50) NOT NULL,
    first_ask_seq bigint DEFAULT 0 NOT NULL,
    last_dt character varying(8) NOT NULL,
    del_yn character varying(1) DEFAULT 'N'::character varying NOT NULL,
    create_dt timestamp without time zone DEFAULT now() NOT NULL,
    create_user_id character varying(50) NOT NULL,
    update_dt timestamp without time zone DEFAULT now() NOT NULL,
    update_user_id character varying(50) NOT NULL
);


--
-- TOC entry 4744 (class 0 OID 0)
-- Dependencies: 230
-- Name: TABLE tb_ai_chat_ask_group; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_ai_chat_ask_group IS '챗봇AI질문구룹내역';


--
-- TOC entry 4745 (class 0 OID 0)
-- Dependencies: 230
-- Name: COLUMN tb_ai_chat_ask_group.region; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask_group.region IS '지역';


--
-- TOC entry 4746 (class 0 OID 0)
-- Dependencies: 230
-- Name: COLUMN tb_ai_chat_ask_group.user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask_group.user_id IS '사용자ID';


--
-- TOC entry 4747 (class 0 OID 0)
-- Dependencies: 230
-- Name: COLUMN tb_ai_chat_ask_group.group_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask_group.group_id IS '챗그룹ID';


--
-- TOC entry 4748 (class 0 OID 0)
-- Dependencies: 230
-- Name: COLUMN tb_ai_chat_ask_group.first_ask_seq; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask_group.first_ask_seq IS '그룹최초질문순번';


--
-- TOC entry 4749 (class 0 OID 0)
-- Dependencies: 230
-- Name: COLUMN tb_ai_chat_ask_group.last_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask_group.last_dt IS '최종등록일';


--
-- TOC entry 4750 (class 0 OID 0)
-- Dependencies: 230
-- Name: COLUMN tb_ai_chat_ask_group.del_yn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask_group.del_yn IS '삭제여부';


--
-- TOC entry 4751 (class 0 OID 0)
-- Dependencies: 230
-- Name: COLUMN tb_ai_chat_ask_group.create_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask_group.create_dt IS '생성일자';


--
-- TOC entry 4752 (class 0 OID 0)
-- Dependencies: 230
-- Name: COLUMN tb_ai_chat_ask_group.create_user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask_group.create_user_id IS '생성자';


--
-- TOC entry 4753 (class 0 OID 0)
-- Dependencies: 230
-- Name: COLUMN tb_ai_chat_ask_group.update_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask_group.update_dt IS '수정일자';


--
-- TOC entry 4754 (class 0 OID 0)
-- Dependencies: 230
-- Name: COLUMN tb_ai_chat_ask_group.update_user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask_group.update_user_id IS '수정자';


--
-- TOC entry 327 (class 1259 OID 32650)
-- Name: tb_ai_chat_ask_image; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_ai_chat_ask_image (
    region character varying(10) NOT NULL,
    ask_seq bigint NOT NULL,
    image_id bigint NOT NULL,
    user_id character varying(40) NOT NULL,
    image_name character varying(200),
    image_type character varying(100),
    image_data bytea,
    create_dt timestamp without time zone DEFAULT now() NOT NULL,
    create_user_id character varying(50) NOT NULL,
    update_dt timestamp without time zone DEFAULT now() NOT NULL,
    update_user_id character varying(50) NOT NULL
);


--
-- TOC entry 4755 (class 0 OID 0)
-- Dependencies: 327
-- Name: TABLE tb_ai_chat_ask_image; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_ai_chat_ask_image IS '챗봇AI질문이미지내역';


--
-- TOC entry 4756 (class 0 OID 0)
-- Dependencies: 327
-- Name: COLUMN tb_ai_chat_ask_image.region; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask_image.region IS '지역';


--
-- TOC entry 4757 (class 0 OID 0)
-- Dependencies: 327
-- Name: COLUMN tb_ai_chat_ask_image.ask_seq; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask_image.ask_seq IS '질문순번';


--
-- TOC entry 4758 (class 0 OID 0)
-- Dependencies: 327
-- Name: COLUMN tb_ai_chat_ask_image.image_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask_image.image_id IS '이미지ID';


--
-- TOC entry 4759 (class 0 OID 0)
-- Dependencies: 327
-- Name: COLUMN tb_ai_chat_ask_image.user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask_image.user_id IS '사용자ID';


--
-- TOC entry 4760 (class 0 OID 0)
-- Dependencies: 327
-- Name: COLUMN tb_ai_chat_ask_image.image_name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask_image.image_name IS '이미지명';


--
-- TOC entry 4761 (class 0 OID 0)
-- Dependencies: 327
-- Name: COLUMN tb_ai_chat_ask_image.image_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask_image.image_type IS '이미지Type';


--
-- TOC entry 4762 (class 0 OID 0)
-- Dependencies: 327
-- Name: COLUMN tb_ai_chat_ask_image.image_data; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask_image.image_data IS '이미지ByteData';


--
-- TOC entry 4763 (class 0 OID 0)
-- Dependencies: 327
-- Name: COLUMN tb_ai_chat_ask_image.create_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask_image.create_dt IS '생성일자';


--
-- TOC entry 4764 (class 0 OID 0)
-- Dependencies: 327
-- Name: COLUMN tb_ai_chat_ask_image.create_user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask_image.create_user_id IS '생성자';


--
-- TOC entry 4765 (class 0 OID 0)
-- Dependencies: 327
-- Name: COLUMN tb_ai_chat_ask_image.update_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask_image.update_dt IS '수정일자';


--
-- TOC entry 4766 (class 0 OID 0)
-- Dependencies: 327
-- Name: COLUMN tb_ai_chat_ask_image.update_user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_ask_image.update_user_id IS '수정자';


--
-- TOC entry 325 (class 1259 OID 31989)
-- Name: tb_ai_chat_bot; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_ai_chat_bot (
    region character varying(10) NOT NULL,
    user_id character varying(40) NOT NULL,
    ask_seq bigint NOT NULL,
    ask_dt character varying(8) NOT NULL,
    bot_dt character varying(8) NOT NULL,
    bot_tm character varying(6) NOT NULL,
    chat_role character varying(10) DEFAULT 'BOT'::character varying NOT NULL,
    bot_msg text,
    image_data text,
    references_data text,
    recquestions text,
    create_dt timestamp without time zone DEFAULT now() NOT NULL,
    create_user_id character varying(50) NOT NULL,
    update_dt timestamp without time zone DEFAULT now() NOT NULL,
    update_user_id character varying(50) NOT NULL
);


--
-- TOC entry 4767 (class 0 OID 0)
-- Dependencies: 325
-- Name: TABLE tb_ai_chat_bot; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_ai_chat_bot IS '챗봇AI응답내역';


--
-- TOC entry 4768 (class 0 OID 0)
-- Dependencies: 325
-- Name: COLUMN tb_ai_chat_bot.region; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_bot.region IS '지역';


--
-- TOC entry 4769 (class 0 OID 0)
-- Dependencies: 325
-- Name: COLUMN tb_ai_chat_bot.user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_bot.user_id IS '사용자ID';


--
-- TOC entry 4770 (class 0 OID 0)
-- Dependencies: 325
-- Name: COLUMN tb_ai_chat_bot.ask_seq; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_bot.ask_seq IS '질문순번';


--
-- TOC entry 4771 (class 0 OID 0)
-- Dependencies: 325
-- Name: COLUMN tb_ai_chat_bot.ask_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_bot.ask_dt IS '질문일자';


--
-- TOC entry 4772 (class 0 OID 0)
-- Dependencies: 325
-- Name: COLUMN tb_ai_chat_bot.bot_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_bot.bot_dt IS '응답일자';


--
-- TOC entry 4773 (class 0 OID 0)
-- Dependencies: 325
-- Name: COLUMN tb_ai_chat_bot.bot_tm; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_bot.bot_tm IS '응답시간';


--
-- TOC entry 4774 (class 0 OID 0)
-- Dependencies: 325
-- Name: COLUMN tb_ai_chat_bot.chat_role; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_bot.chat_role IS '챗구분(BOT:ChatBot)';


--
-- TOC entry 4775 (class 0 OID 0)
-- Dependencies: 325
-- Name: COLUMN tb_ai_chat_bot.bot_msg; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_bot.bot_msg IS '응답내용';


--
-- TOC entry 4776 (class 0 OID 0)
-- Dependencies: 325
-- Name: COLUMN tb_ai_chat_bot.image_data; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_bot.image_data IS '이미지텍스트Data';


--
-- TOC entry 4777 (class 0 OID 0)
-- Dependencies: 325
-- Name: COLUMN tb_ai_chat_bot.references_data; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_bot.references_data IS '참고자료';


--
-- TOC entry 4778 (class 0 OID 0)
-- Dependencies: 325
-- Name: COLUMN tb_ai_chat_bot.recquestions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_bot.recquestions IS '추천질의';


--
-- TOC entry 4779 (class 0 OID 0)
-- Dependencies: 325
-- Name: COLUMN tb_ai_chat_bot.create_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_bot.create_dt IS '생성일자';


--
-- TOC entry 4780 (class 0 OID 0)
-- Dependencies: 325
-- Name: COLUMN tb_ai_chat_bot.create_user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_bot.create_user_id IS '생성자';


--
-- TOC entry 4781 (class 0 OID 0)
-- Dependencies: 325
-- Name: COLUMN tb_ai_chat_bot.update_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_bot.update_dt IS '수정일자';


--
-- TOC entry 4782 (class 0 OID 0)
-- Dependencies: 325
-- Name: COLUMN tb_ai_chat_bot.update_user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_bot.update_user_id IS '수정자';


--
-- TOC entry 371 (class 1259 OID 36309)
-- Name: tb_ai_chat_bot_image; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_ai_chat_bot_image (
    region character varying(10) NOT NULL,
    ask_seq bigint NOT NULL,
    image_id bigint NOT NULL,
    user_id character varying(40) NOT NULL,
    image_name character varying(50),
    image_type character varying(10),
    image_data bytea,
    html_data text,
    create_dt timestamp without time zone DEFAULT now() NOT NULL,
    create_user_id character varying(50) NOT NULL,
    update_dt timestamp without time zone DEFAULT now() NOT NULL,
    update_user_id character varying(50) NOT NULL
);


--
-- TOC entry 231 (class 1259 OID 24635)
-- Name: tb_ai_chat_faq; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_ai_chat_faq (
    region character varying(10) NOT NULL,
    faq_seq integer NOT NULL,
    faq_contents character varying(300) NOT NULL,
    sort_num integer NOT NULL,
    open_date character varying(8),
    close_date character varying(8),
    use_yn character varying(1) DEFAULT 'Y'::character varying NOT NULL,
    create_dt timestamp without time zone DEFAULT now() NOT NULL,
    create_user_id character varying(50) NOT NULL,
    update_dt timestamp without time zone DEFAULT now() NOT NULL,
    update_user_id character varying(50) NOT NULL
);


--
-- TOC entry 4783 (class 0 OID 0)
-- Dependencies: 231
-- Name: TABLE tb_ai_chat_faq; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_ai_chat_faq IS '자주하는질문';


--
-- TOC entry 4784 (class 0 OID 0)
-- Dependencies: 231
-- Name: COLUMN tb_ai_chat_faq.region; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_faq.region IS '지역';


--
-- TOC entry 4785 (class 0 OID 0)
-- Dependencies: 231
-- Name: COLUMN tb_ai_chat_faq.faq_seq; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_faq.faq_seq IS 'FAQ순번';


--
-- TOC entry 4786 (class 0 OID 0)
-- Dependencies: 231
-- Name: COLUMN tb_ai_chat_faq.faq_contents; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_faq.faq_contents IS 'FAQ내용';


--
-- TOC entry 4787 (class 0 OID 0)
-- Dependencies: 231
-- Name: COLUMN tb_ai_chat_faq.sort_num; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_faq.sort_num IS 'display순서';


--
-- TOC entry 4788 (class 0 OID 0)
-- Dependencies: 231
-- Name: COLUMN tb_ai_chat_faq.open_date; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_faq.open_date IS '게시시작일';


--
-- TOC entry 4789 (class 0 OID 0)
-- Dependencies: 231
-- Name: COLUMN tb_ai_chat_faq.close_date; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_faq.close_date IS '게시종료일';


--
-- TOC entry 4790 (class 0 OID 0)
-- Dependencies: 231
-- Name: COLUMN tb_ai_chat_faq.use_yn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_faq.use_yn IS '사용여부';


--
-- TOC entry 4791 (class 0 OID 0)
-- Dependencies: 231
-- Name: COLUMN tb_ai_chat_faq.create_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_faq.create_dt IS '생성일자';


--
-- TOC entry 4792 (class 0 OID 0)
-- Dependencies: 231
-- Name: COLUMN tb_ai_chat_faq.create_user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_faq.create_user_id IS '생성자';


--
-- TOC entry 4793 (class 0 OID 0)
-- Dependencies: 231
-- Name: COLUMN tb_ai_chat_faq.update_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_faq.update_dt IS '수정일자';


--
-- TOC entry 4794 (class 0 OID 0)
-- Dependencies: 231
-- Name: COLUMN tb_ai_chat_faq.update_user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_ai_chat_faq.update_user_id IS '수정자';


--
-- TOC entry 252 (class 1259 OID 24786)
-- Name: tb_alarm_inspection_log_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tb_alarm_inspection_log_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 232 (class 1259 OID 24643)
-- Name: tb_alarm_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_alarm_log (
    region character varying(10) NOT NULL,
    alarm_id character varying(40) NOT NULL,
    alarm_seq integer NOT NULL,
    alarm_event_time character varying(14) NOT NULL,
    alarm_level character varying(6),
    alarm_target_cnt integer DEFAULT 0 NOT NULL,
    alarm_send_cnt integer DEFAULT 0 NOT NULL,
    alarm_cnt integer DEFAULT 1 NOT NULL,
    alarm_text character varying(4000),
    send_yn character varying(1) DEFAULT 'N'::character varying NOT NULL,
    send_fail_txt character varying(4000),
    create_dt timestamp without time zone DEFAULT now() NOT NULL,
    create_user_id character varying(50) NOT NULL,
    update_dt timestamp without time zone DEFAULT now() NOT NULL,
    update_user_id character varying(50) NOT NULL
);


--
-- TOC entry 4795 (class 0 OID 0)
-- Dependencies: 232
-- Name: COLUMN tb_alarm_log.region; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_alarm_log.region IS '지역';


--
-- TOC entry 4796 (class 0 OID 0)
-- Dependencies: 232
-- Name: COLUMN tb_alarm_log.alarm_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_alarm_log.alarm_id IS '알람발생ID';


--
-- TOC entry 4797 (class 0 OID 0)
-- Dependencies: 232
-- Name: COLUMN tb_alarm_log.alarm_seq; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_alarm_log.alarm_seq IS '알람순번';


--
-- TOC entry 4798 (class 0 OID 0)
-- Dependencies: 232
-- Name: COLUMN tb_alarm_log.alarm_event_time; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_alarm_log.alarm_event_time IS '알람발생일시';


--
-- TOC entry 4799 (class 0 OID 0)
-- Dependencies: 232
-- Name: COLUMN tb_alarm_log.alarm_level; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_alarm_log.alarm_level IS '알람레벨(긴급,심각,경고...)';


--
-- TOC entry 253 (class 1259 OID 24787)
-- Name: tb_alarm_rule_set_rule_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tb_alarm_rule_set_rule_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    MAXVALUE 2147483647
    CACHE 1;


--
-- TOC entry 233 (class 1259 OID 24664)
-- Name: tb_auth; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_auth (
    region character varying(50) NOT NULL,
    auth_idn character varying(15) NOT NULL,
    auth_nm character varying(50),
    use_yn character(1) DEFAULT 'N'::bpchar,
    remark character varying(100),
    create_dt timestamp without time zone,
    create_user_id character varying(50),
    update_dt timestamp without time zone,
    update_user_id character varying(50)
);


--
-- TOC entry 4800 (class 0 OID 0)
-- Dependencies: 233
-- Name: TABLE tb_auth; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_auth IS '기본정보 권한';


--
-- TOC entry 4801 (class 0 OID 0)
-- Dependencies: 233
-- Name: COLUMN tb_auth.region; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_auth.region IS '지역';


--
-- TOC entry 4802 (class 0 OID 0)
-- Dependencies: 233
-- Name: COLUMN tb_auth.auth_idn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_auth.auth_idn IS '권한코드';


--
-- TOC entry 4803 (class 0 OID 0)
-- Dependencies: 233
-- Name: COLUMN tb_auth.auth_nm; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_auth.auth_nm IS '권한명';


--
-- TOC entry 4804 (class 0 OID 0)
-- Dependencies: 233
-- Name: COLUMN tb_auth.use_yn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_auth.use_yn IS '사용유무';


--
-- TOC entry 4805 (class 0 OID 0)
-- Dependencies: 233
-- Name: COLUMN tb_auth.remark; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_auth.remark IS '비고';


--
-- TOC entry 4806 (class 0 OID 0)
-- Dependencies: 233
-- Name: COLUMN tb_auth.create_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_auth.create_dt IS '생성일자';


--
-- TOC entry 4807 (class 0 OID 0)
-- Dependencies: 233
-- Name: COLUMN tb_auth.create_user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_auth.create_user_id IS '생성자';


--
-- TOC entry 4808 (class 0 OID 0)
-- Dependencies: 233
-- Name: COLUMN tb_auth.update_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_auth.update_dt IS '수정일자';


--
-- TOC entry 4809 (class 0 OID 0)
-- Dependencies: 233
-- Name: COLUMN tb_auth.update_user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_auth.update_user_id IS '수정자';


--
-- TOC entry 234 (class 1259 OID 24670)
-- Name: tb_auth_menu; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_auth_menu (
    region character varying(50) NOT NULL,
    auth_idn character varying(15) NOT NULL,
    menu_idn character varying(15) NOT NULL,
    use_yn character(1) DEFAULT 'Y'::bpchar,
    create_dt timestamp without time zone,
    create_user_id character varying(50),
    update_dt timestamp without time zone,
    update_user_id character varying(50),
    menu_order integer DEFAULT 0 NOT NULL
);


--
-- TOC entry 4810 (class 0 OID 0)
-- Dependencies: 234
-- Name: TABLE tb_auth_menu; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_auth_menu IS '기본정보 권한별 메뉴';


--
-- TOC entry 4811 (class 0 OID 0)
-- Dependencies: 234
-- Name: COLUMN tb_auth_menu.region; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_auth_menu.region IS '지역';


--
-- TOC entry 4812 (class 0 OID 0)
-- Dependencies: 234
-- Name: COLUMN tb_auth_menu.auth_idn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_auth_menu.auth_idn IS '권한코드';


--
-- TOC entry 4813 (class 0 OID 0)
-- Dependencies: 234
-- Name: COLUMN tb_auth_menu.menu_idn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_auth_menu.menu_idn IS '메뉴코드';


--
-- TOC entry 4814 (class 0 OID 0)
-- Dependencies: 234
-- Name: COLUMN tb_auth_menu.use_yn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_auth_menu.use_yn IS '사용유무';


--
-- TOC entry 4815 (class 0 OID 0)
-- Dependencies: 234
-- Name: COLUMN tb_auth_menu.create_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_auth_menu.create_dt IS '생성일자';


--
-- TOC entry 4816 (class 0 OID 0)
-- Dependencies: 234
-- Name: COLUMN tb_auth_menu.create_user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_auth_menu.create_user_id IS '생성자';


--
-- TOC entry 4817 (class 0 OID 0)
-- Dependencies: 234
-- Name: COLUMN tb_auth_menu.update_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_auth_menu.update_dt IS '수정일자';


--
-- TOC entry 4818 (class 0 OID 0)
-- Dependencies: 234
-- Name: COLUMN tb_auth_menu.update_user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_auth_menu.update_user_id IS '수정자';


--
-- TOC entry 4819 (class 0 OID 0)
-- Dependencies: 234
-- Name: COLUMN tb_auth_menu.menu_order; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_auth_menu.menu_order IS '메뉴순서';


--
-- TOC entry 319 (class 1259 OID 26298)
-- Name: tb_block_info; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_block_info (
    sitename character varying NOT NULL,
    general_overview jsonb,
    critical_pressure numeric,
    pressure_unit character varying,
    site_photo_url text,
    manual_url character varying,
    system_diagram_url character varying,
    block_level character varying
);


--
-- TOC entry 4820 (class 0 OID 0)
-- Dependencies: 319
-- Name: TABLE tb_block_info; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_block_info IS '블록 기본 정보 테이블 (일반현황 JSON, 임계압력, 단위, 사진 포함)';


--
-- TOC entry 4821 (class 0 OID 0)
-- Dependencies: 319
-- Name: COLUMN tb_block_info.sitename; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_block_info.sitename IS '블록 명칭 (Primary Key)';


--
-- TOC entry 4822 (class 0 OID 0)
-- Dependencies: 319
-- Name: COLUMN tb_block_info.general_overview; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_block_info.general_overview IS '블록 일반현황 JSONB (설치위치, 수용가, 유수율, 사용량, 관로, 대수용가 등)';


--
-- TOC entry 4823 (class 0 OID 0)
-- Dependencies: 319
-- Name: COLUMN tb_block_info.critical_pressure; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_block_info.critical_pressure IS '임계 압력 (음수 포함 가능)';


--
-- TOC entry 4824 (class 0 OID 0)
-- Dependencies: 319
-- Name: COLUMN tb_block_info.pressure_unit; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_block_info.pressure_unit IS '압력 단위 (예: kg/cm2)';


--
-- TOC entry 4825 (class 0 OID 0)
-- Dependencies: 319
-- Name: COLUMN tb_block_info.site_photo_url; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_block_info.site_photo_url IS '현장 사진 URL';


--
-- TOC entry 4826 (class 0 OID 0)
-- Dependencies: 319
-- Name: COLUMN tb_block_info.manual_url; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_block_info.manual_url IS '블록 초기 대응 URL';


--
-- TOC entry 312 (class 1259 OID 25782)
-- Name: tb_block_status; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_block_status (
    sitename character varying NOT NULL,
    flow_missing_rate numeric,
    flow_missing_rate_unit character varying,
    block_avg_daily_flow numeric,
    block_avg_daily_flow_ratio numeric,
    block_pressure numeric,
    block_inlet_pressure numeric,
    block_outlet_pressure numeric,
    inlet_pressure_variation_rate numeric,
    outlet_pressure_variation_rate numeric,
    flow_unit character varying,
    pressure_unit character varying,
    updated_at timestamp without time zone DEFAULT now(),
    meta jsonb,
    block_level character varying
);


--
-- TOC entry 4827 (class 0 OID 0)
-- Dependencies: 312
-- Name: TABLE tb_block_status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_block_status IS 'Block operation status table (flow, pressure, variation rate, etc.)';


--
-- TOC entry 4828 (class 0 OID 0)
-- Dependencies: 312
-- Name: COLUMN tb_block_status.sitename; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_block_status.sitename IS 'Block name (PK, FK to tb_block_info)';


--
-- TOC entry 4829 (class 0 OID 0)
-- Dependencies: 312
-- Name: COLUMN tb_block_status.flow_missing_rate; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_block_status.flow_missing_rate IS 'Flow missing rate (%)';


--
-- TOC entry 4830 (class 0 OID 0)
-- Dependencies: 312
-- Name: COLUMN tb_block_status.flow_missing_rate_unit; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_block_status.flow_missing_rate_unit IS 'Unit for flow missing rate (예: %)';


--
-- TOC entry 4831 (class 0 OID 0)
-- Dependencies: 312
-- Name: COLUMN tb_block_status.block_avg_daily_flow; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_block_status.block_avg_daily_flow IS 'Block average daily flow (전 달/30)';


--
-- TOC entry 4832 (class 0 OID 0)
-- Dependencies: 312
-- Name: COLUMN tb_block_status.block_avg_daily_flow_ratio; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_block_status.block_avg_daily_flow_ratio IS 'Block average daily flow ratio (%)';


--
-- TOC entry 4833 (class 0 OID 0)
-- Dependencies: 312
-- Name: COLUMN tb_block_status.block_pressure; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_block_status.block_pressure IS 'Block pressure (블록 압력)';


--
-- TOC entry 4834 (class 0 OID 0)
-- Dependencies: 312
-- Name: COLUMN tb_block_status.block_inlet_pressure; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_block_status.block_inlet_pressure IS 'Block inlet pressure (동일시간대 평균)';


--
-- TOC entry 4835 (class 0 OID 0)
-- Dependencies: 312
-- Name: COLUMN tb_block_status.block_outlet_pressure; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_block_status.block_outlet_pressure IS 'Block outlet pressure (동일시간대 평균)';


--
-- TOC entry 4836 (class 0 OID 0)
-- Dependencies: 312
-- Name: COLUMN tb_block_status.inlet_pressure_variation_rate; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_block_status.inlet_pressure_variation_rate IS 'Inlet pressure variation rate (%)';


--
-- TOC entry 4837 (class 0 OID 0)
-- Dependencies: 312
-- Name: COLUMN tb_block_status.outlet_pressure_variation_rate; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_block_status.outlet_pressure_variation_rate IS 'Outlet pressure variation rate (%)';


--
-- TOC entry 4838 (class 0 OID 0)
-- Dependencies: 312
-- Name: COLUMN tb_block_status.flow_unit; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_block_status.flow_unit IS 'Flow unit (예: m³/h)';


--
-- TOC entry 4839 (class 0 OID 0)
-- Dependencies: 312
-- Name: COLUMN tb_block_status.pressure_unit; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_block_status.pressure_unit IS 'Pressure unit (예: kg/cm2)';


--
-- TOC entry 4840 (class 0 OID 0)
-- Dependencies: 312
-- Name: COLUMN tb_block_status.updated_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_block_status.updated_at IS '마지막 수정 시각 (UPDATE 시 자동 변경)';


--
-- TOC entry 235 (class 1259 OID 24677)
-- Name: tb_comm_code; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_comm_code (
    region character varying(20) NOT NULL,
    grp_cd character varying(20) NOT NULL,
    comm_cd character varying(20) NOT NULL,
    comm_nm character varying(100),
    use_yn character(1),
    create_dt timestamp without time zone,
    create_user_id character varying(50),
    update_dt timestamp without time zone,
    update_user_id character varying(50)
);


--
-- TOC entry 4841 (class 0 OID 0)
-- Dependencies: 235
-- Name: TABLE tb_comm_code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_comm_code IS '기본정보 공통코드';


--
-- TOC entry 4842 (class 0 OID 0)
-- Dependencies: 235
-- Name: COLUMN tb_comm_code.region; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_comm_code.region IS '지역';


--
-- TOC entry 4843 (class 0 OID 0)
-- Dependencies: 235
-- Name: COLUMN tb_comm_code.grp_cd; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_comm_code.grp_cd IS '그룹코드';


--
-- TOC entry 4844 (class 0 OID 0)
-- Dependencies: 235
-- Name: COLUMN tb_comm_code.comm_cd; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_comm_code.comm_cd IS '공통코드';


--
-- TOC entry 4845 (class 0 OID 0)
-- Dependencies: 235
-- Name: COLUMN tb_comm_code.comm_nm; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_comm_code.comm_nm IS '공통코드명';


--
-- TOC entry 4846 (class 0 OID 0)
-- Dependencies: 235
-- Name: COLUMN tb_comm_code.use_yn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_comm_code.use_yn IS '사용유무';


--
-- TOC entry 4847 (class 0 OID 0)
-- Dependencies: 235
-- Name: COLUMN tb_comm_code.create_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_comm_code.create_dt IS '생성일자';


--
-- TOC entry 4848 (class 0 OID 0)
-- Dependencies: 235
-- Name: COLUMN tb_comm_code.create_user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_comm_code.create_user_id IS '생성자';


--
-- TOC entry 4849 (class 0 OID 0)
-- Dependencies: 235
-- Name: COLUMN tb_comm_code.update_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_comm_code.update_dt IS '수정일자';


--
-- TOC entry 4850 (class 0 OID 0)
-- Dependencies: 235
-- Name: COLUMN tb_comm_code.update_user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_comm_code.update_user_id IS '수정자';


--
-- TOC entry 379 (class 1259 OID 36893)
-- Name: tb_equipment_alarm_report; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_equipment_alarm_report (
    alarm_start_time timestamp without time zone NOT NULL,
    alarm_end_time timestamp without time zone,
    tagsn character varying(100) NOT NULL,
    sitename character varying(64),
    facilitytype text,
    equipmenttype text,
    equipment_id character varying(64),
    alarm_category character varying(100),
    alarm_msg text,
    alarm_value numeric,
    alarm_status character varying(32),
    diagnosed_cause text,
    alarm_severity character varying(32),
    action_plan text,
    user_cause_description text,
    meta jsonb,
    stat character varying,
    diagnosed_msg text,
    alarm_confirm_yn character varying(3) DEFAULT 'Y'::character varying,
    countermeasure character varying,
    off_alarm_confirm_yn character varying(3) DEFAULT 'Y'::character varying,
    is_false_alarm character varying,
    false_alarm_notes character varying,
    info_updated character varying,
    tagtype character varying
);


--
-- TOC entry 4851 (class 0 OID 0)
-- Dependencies: 379
-- Name: TABLE tb_equipment_alarm_report; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_equipment_alarm_report IS '설비 알람 발생/해제 실시간 이력 테이블';


--
-- TOC entry 4852 (class 0 OID 0)
-- Dependencies: 379
-- Name: COLUMN tb_equipment_alarm_report.alarm_start_time; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_alarm_report.alarm_start_time IS '알람 발생 시각';


--
-- TOC entry 4853 (class 0 OID 0)
-- Dependencies: 379
-- Name: COLUMN tb_equipment_alarm_report.alarm_end_time; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_alarm_report.alarm_end_time IS '알람 해제 시각 (NULL = 미해제)';


--
-- TOC entry 4854 (class 0 OID 0)
-- Dependencies: 379
-- Name: COLUMN tb_equipment_alarm_report.tagsn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_alarm_report.tagsn IS '태그 식별자 또는 알람 ID';


--
-- TOC entry 4855 (class 0 OID 0)
-- Dependencies: 379
-- Name: COLUMN tb_equipment_alarm_report.sitename; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_alarm_report.sitename IS '설치 현장명 (예: 용연2통)';


--
-- TOC entry 4856 (class 0 OID 0)
-- Dependencies: 379
-- Name: COLUMN tb_equipment_alarm_report.facilitytype; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_alarm_report.facilitytype IS '시설 구분 (예: 가압장, 배수지)';


--
-- TOC entry 4857 (class 0 OID 0)
-- Dependencies: 379
-- Name: COLUMN tb_equipment_alarm_report.equipmenttype; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_alarm_report.equipmenttype IS '장비 구분 (예: PLC, 펌프)';


--
-- TOC entry 4858 (class 0 OID 0)
-- Dependencies: 379
-- Name: COLUMN tb_equipment_alarm_report.equipment_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_alarm_report.equipment_id IS '장비 ID (예: yh_plc_ch0)';


--
-- TOC entry 4859 (class 0 OID 0)
-- Dependencies: 379
-- Name: COLUMN tb_equipment_alarm_report.alarm_category; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_alarm_report.alarm_category IS '알람 분류 (통신, 압력, 수위 등)';


--
-- TOC entry 4860 (class 0 OID 0)
-- Dependencies: 379
-- Name: COLUMN tb_equipment_alarm_report.alarm_msg; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_alarm_report.alarm_msg IS '알람 메시지 (간단한 설명)';


--
-- TOC entry 4861 (class 0 OID 0)
-- Dependencies: 379
-- Name: COLUMN tb_equipment_alarm_report.alarm_value; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_alarm_report.alarm_value IS '알람 발생 시 기록된 태그 값 (계측값)';


--
-- TOC entry 4862 (class 0 OID 0)
-- Dependencies: 379
-- Name: COLUMN tb_equipment_alarm_report.alarm_status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_alarm_report.alarm_status IS '알람 상태 (진행중,작업중,알람OFF)';


--
-- TOC entry 4863 (class 0 OID 0)
-- Dependencies: 379
-- Name: COLUMN tb_equipment_alarm_report.diagnosed_cause; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_alarm_report.diagnosed_cause IS '시스템 또는 규칙 기반 진단 원인';


--
-- TOC entry 4864 (class 0 OID 0)
-- Dependencies: 379
-- Name: COLUMN tb_equipment_alarm_report.alarm_severity; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_alarm_report.alarm_severity IS '알람 심각도 (주의/경계/위험)';


--
-- TOC entry 4865 (class 0 OID 0)
-- Dependencies: 379
-- Name: COLUMN tb_equipment_alarm_report.action_plan; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_alarm_report.action_plan IS '현장 대응 방안 또는 조치사항';


--
-- TOC entry 4866 (class 0 OID 0)
-- Dependencies: 379
-- Name: COLUMN tb_equipment_alarm_report.user_cause_description; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_alarm_report.user_cause_description IS '사용자 입력 원인 또는 추가 설명';


--
-- TOC entry 4867 (class 0 OID 0)
-- Dependencies: 379
-- Name: COLUMN tb_equipment_alarm_report.meta; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_alarm_report.meta IS '알람 메타데이터 (analysis, impact, 관련 정보 등 JSONB)';


--
-- TOC entry 4868 (class 0 OID 0)
-- Dependencies: 379
-- Name: COLUMN tb_equipment_alarm_report.stat; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_alarm_report.stat IS '위기대응 XML 구문 완성 여부';


--
-- TOC entry 4869 (class 0 OID 0)
-- Dependencies: 379
-- Name: COLUMN tb_equipment_alarm_report.diagnosed_msg; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_alarm_report.diagnosed_msg IS 'sLM응답 저장';


--
-- TOC entry 4870 (class 0 OID 0)
-- Dependencies: 379
-- Name: COLUMN tb_equipment_alarm_report.alarm_confirm_yn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_alarm_report.alarm_confirm_yn IS '알람확인여부';


--
-- TOC entry 4871 (class 0 OID 0)
-- Dependencies: 379
-- Name: COLUMN tb_equipment_alarm_report.countermeasure; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_alarm_report.countermeasure IS '대응방안';


--
-- TOC entry 4872 (class 0 OID 0)
-- Dependencies: 379
-- Name: COLUMN tb_equipment_alarm_report.off_alarm_confirm_yn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_alarm_report.off_alarm_confirm_yn IS 'OFF알람확인여부';


--
-- TOC entry 4873 (class 0 OID 0)
-- Dependencies: 379
-- Name: COLUMN tb_equipment_alarm_report.is_false_alarm; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_alarm_report.is_false_alarm IS '오알람여부';


--
-- TOC entry 4874 (class 0 OID 0)
-- Dependencies: 379
-- Name: COLUMN tb_equipment_alarm_report.false_alarm_notes; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_alarm_report.false_alarm_notes IS '오알람/정상 상세설명';


--
-- TOC entry 4875 (class 0 OID 0)
-- Dependencies: 379
-- Name: COLUMN tb_equipment_alarm_report.info_updated; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_alarm_report.info_updated IS '정보 업데이트 여부';


--
-- TOC entry 314 (class 1259 OID 25821)
-- Name: tb_equipment_info; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_equipment_info (
    equipment_id character varying(64) NOT NULL,
    sitename character varying(64) NOT NULL,
    facilitytype text NOT NULL,
    equipmenttype text NOT NULL,
    status text DEFAULT 'operational'::text NOT NULL,
    commissioned_at date,
    decommissioned_at date,
    description text,
    meta jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- TOC entry 4876 (class 0 OID 0)
-- Dependencies: 314
-- Name: TABLE tb_equipment_info; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_equipment_info IS '설비 기본 정보 테이블: 현장, 시설유형, 장비유형, 상태, 메타데이터 포함';


--
-- TOC entry 4877 (class 0 OID 0)
-- Dependencies: 314
-- Name: COLUMN tb_equipment_info.equipment_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_info.equipment_id IS '설비 고유 ID (Primary Key)';


--
-- TOC entry 4878 (class 0 OID 0)
-- Dependencies: 314
-- Name: COLUMN tb_equipment_info.sitename; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_info.sitename IS '현장 명칭 (예: 합덕2, 용현2)';


--
-- TOC entry 4879 (class 0 OID 0)
-- Dependencies: 314
-- Name: COLUMN tb_equipment_info.facilitytype; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_info.facilitytype IS '시설 유형 코드 (FK: tb_facility_type.code, 예: reservoir, booster_station)';


--
-- TOC entry 4880 (class 0 OID 0)
-- Dependencies: 314
-- Name: COLUMN tb_equipment_info.equipmenttype; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_info.equipmenttype IS '장비 유형 코드 (FK: tb_equipment_type.code, 예: pump, plc)';


--
-- TOC entry 4881 (class 0 OID 0)
-- Dependencies: 314
-- Name: COLUMN tb_equipment_info.status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_info.status IS '설비 상태 코드 (FK: tb_equipment_status.code, 예: operational, maintenance, decommissioned)';


--
-- TOC entry 4882 (class 0 OID 0)
-- Dependencies: 314
-- Name: COLUMN tb_equipment_info.commissioned_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_info.commissioned_at IS '설비 설치/가동 시작일';


--
-- TOC entry 4883 (class 0 OID 0)
-- Dependencies: 314
-- Name: COLUMN tb_equipment_info.decommissioned_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_info.decommissioned_at IS '설비 해제/폐기일';


--
-- TOC entry 4884 (class 0 OID 0)
-- Dependencies: 314
-- Name: COLUMN tb_equipment_info.description; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_info.description IS '설비 설명 (텍스트)';


--
-- TOC entry 4885 (class 0 OID 0)
-- Dependencies: 314
-- Name: COLUMN tb_equipment_info.meta; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_info.meta IS '설비 메타데이터 (JSONB, 예: {\"manufacturer\":\"LS Electric\",\"model\":\"XGT\"})';


--
-- TOC entry 4886 (class 0 OID 0)
-- Dependencies: 314
-- Name: COLUMN tb_equipment_info.created_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_info.created_at IS '데이터 생성일시 (자동 입력)';


--
-- TOC entry 4887 (class 0 OID 0)
-- Dependencies: 314
-- Name: COLUMN tb_equipment_info.updated_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_info.updated_at IS '데이터 수정일시 (트리거로 자동 업데이트)';


--
-- TOC entry 313 (class 1259 OID 25814)
-- Name: tb_equipment_status; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_equipment_status (
    code text NOT NULL,
    display_name text NOT NULL,
    description text
);


--
-- TOC entry 4888 (class 0 OID 0)
-- Dependencies: 313
-- Name: TABLE tb_equipment_status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_equipment_status IS '설비 상태 코드 마스터';


--
-- TOC entry 4889 (class 0 OID 0)
-- Dependencies: 313
-- Name: COLUMN tb_equipment_status.code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_status.code IS '설비 상태 코드 (영문 소문자 권장)';


--
-- TOC entry 4890 (class 0 OID 0)
-- Dependencies: 313
-- Name: COLUMN tb_equipment_status.display_name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_status.display_name IS '설비 상태 표시명';


--
-- TOC entry 4891 (class 0 OID 0)
-- Dependencies: 313
-- Name: COLUMN tb_equipment_status.description; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_equipment_status.description IS '설비 상태 상세 설명';


--
-- TOC entry 326 (class 1259 OID 32037)
-- Name: tb_facility_flow_map; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_facility_flow_map (
    upstream_sitename text NOT NULL,
    upstream_facilitytype text NOT NULL,
    downstream_sitename text NOT NULL,
    downstream_facilitytype text NOT NULL,
    relation_type text NOT NULL,
    description text
);


--
-- TOC entry 4892 (class 0 OID 0)
-- Dependencies: 326
-- Name: TABLE tb_facility_flow_map; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_facility_flow_map IS '상류-하류 시설 간 연결 관계를 관리하는 매핑 테이블';


--
-- TOC entry 4893 (class 0 OID 0)
-- Dependencies: 326
-- Name: COLUMN tb_facility_flow_map.upstream_sitename; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_facility_flow_map.upstream_sitename IS '상류(Upstream) 현장명';


--
-- TOC entry 4894 (class 0 OID 0)
-- Dependencies: 326
-- Name: COLUMN tb_facility_flow_map.upstream_facilitytype; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_facility_flow_map.upstream_facilitytype IS '상류(Upstream) 시설 유형';


--
-- TOC entry 4895 (class 0 OID 0)
-- Dependencies: 326
-- Name: COLUMN tb_facility_flow_map.downstream_sitename; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_facility_flow_map.downstream_sitename IS '하류(Downstream) 현장명';


--
-- TOC entry 4896 (class 0 OID 0)
-- Dependencies: 326
-- Name: COLUMN tb_facility_flow_map.downstream_facilitytype; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_facility_flow_map.downstream_facilitytype IS '하류(Downstream) 시설 유형';


--
-- TOC entry 4897 (class 0 OID 0)
-- Dependencies: 326
-- Name: COLUMN tb_facility_flow_map.relation_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_facility_flow_map.relation_type IS '연결 관계 유형 (예: 수계, 제어, 통신)';


--
-- TOC entry 4898 (class 0 OID 0)
-- Dependencies: 326
-- Name: COLUMN tb_facility_flow_map.description; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_facility_flow_map.description IS '연결 관계 설명 (예: 공급 경로, 제어 관계 등)';


--
-- TOC entry 237 (class 1259 OID 24683)
-- Name: tb_file_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_file_history (
    upload_id integer NOT NULL,
    region character varying(50) NOT NULL,
    user_id character varying(50),
    org_file_name character varying(200) NOT NULL,
    file_name character varying(200) NOT NULL,
    file_type character varying(200),
    file_url character varying(300),
    text_content text,
    binary_content bytea,
    template_id character varying(200),
    generated_prompt text,
    upload_dt timestamp without time zone DEFAULT now(),
    completed_yn character varying(1) DEFAULT 'N'::character varying NOT NULL
);


--
-- TOC entry 4899 (class 0 OID 0)
-- Dependencies: 237
-- Name: TABLE tb_file_history; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_file_history IS '파일 이력';


--
-- TOC entry 4900 (class 0 OID 0)
-- Dependencies: 237
-- Name: COLUMN tb_file_history.upload_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_file_history.upload_id IS '업로드id';


--
-- TOC entry 4901 (class 0 OID 0)
-- Dependencies: 237
-- Name: COLUMN tb_file_history.region; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_file_history.region IS '지역';


--
-- TOC entry 4902 (class 0 OID 0)
-- Dependencies: 237
-- Name: COLUMN tb_file_history.user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_file_history.user_id IS '업로드유저ID';


--
-- TOC entry 4903 (class 0 OID 0)
-- Dependencies: 237
-- Name: COLUMN tb_file_history.org_file_name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_file_history.org_file_name IS '원본파일명';


--
-- TOC entry 4904 (class 0 OID 0)
-- Dependencies: 237
-- Name: COLUMN tb_file_history.file_name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_file_history.file_name IS '파일명';


--
-- TOC entry 4905 (class 0 OID 0)
-- Dependencies: 237
-- Name: COLUMN tb_file_history.file_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_file_history.file_type IS '파일타입';


--
-- TOC entry 4906 (class 0 OID 0)
-- Dependencies: 237
-- Name: COLUMN tb_file_history.file_url; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_file_history.file_url IS '파일URL';


--
-- TOC entry 4907 (class 0 OID 0)
-- Dependencies: 237
-- Name: COLUMN tb_file_history.text_content; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_file_history.text_content IS '텍스트내용';


--
-- TOC entry 4908 (class 0 OID 0)
-- Dependencies: 237
-- Name: COLUMN tb_file_history.binary_content; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_file_history.binary_content IS '바이너리내용';


--
-- TOC entry 4909 (class 0 OID 0)
-- Dependencies: 237
-- Name: COLUMN tb_file_history.template_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_file_history.template_id IS '템플릿ID';


--
-- TOC entry 4910 (class 0 OID 0)
-- Dependencies: 237
-- Name: COLUMN tb_file_history.generated_prompt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_file_history.generated_prompt IS '생성프롬프트';


--
-- TOC entry 4911 (class 0 OID 0)
-- Dependencies: 237
-- Name: COLUMN tb_file_history.upload_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_file_history.upload_dt IS '수정일자';


--
-- TOC entry 4912 (class 0 OID 0)
-- Dependencies: 237
-- Name: COLUMN tb_file_history.completed_yn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_file_history.completed_yn IS '학습완료여부';


--
-- TOC entry 236 (class 1259 OID 24682)
-- Name: tb_file_history_upload_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tb_file_history_upload_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 4913 (class 0 OID 0)
-- Dependencies: 236
-- Name: tb_file_history_upload_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tb_file_history_upload_id_seq OWNED BY public.tb_file_history.upload_id;


--
-- TOC entry 238 (class 1259 OID 24693)
-- Name: tb_grp_code; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_grp_code (
    region character varying(20) NOT NULL,
    grp_cd character varying(20) NOT NULL,
    grp_nm character varying(20),
    use_yn character(1),
    create_dt timestamp without time zone,
    create_user_id character varying(50),
    update_dt timestamp without time zone,
    update_user_id character varying(50)
);


--
-- TOC entry 4914 (class 0 OID 0)
-- Dependencies: 238
-- Name: TABLE tb_grp_code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_grp_code IS '기본정보 공통코드 그룹';


--
-- TOC entry 4915 (class 0 OID 0)
-- Dependencies: 238
-- Name: COLUMN tb_grp_code.region; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_grp_code.region IS '지역';


--
-- TOC entry 4916 (class 0 OID 0)
-- Dependencies: 238
-- Name: COLUMN tb_grp_code.grp_cd; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_grp_code.grp_cd IS '그룹코드';


--
-- TOC entry 4917 (class 0 OID 0)
-- Dependencies: 238
-- Name: COLUMN tb_grp_code.grp_nm; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_grp_code.grp_nm IS '그룹코드명';


--
-- TOC entry 4918 (class 0 OID 0)
-- Dependencies: 238
-- Name: COLUMN tb_grp_code.use_yn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_grp_code.use_yn IS '사용여부';


--
-- TOC entry 4919 (class 0 OID 0)
-- Dependencies: 238
-- Name: COLUMN tb_grp_code.create_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_grp_code.create_dt IS '생성일자';


--
-- TOC entry 4920 (class 0 OID 0)
-- Dependencies: 238
-- Name: COLUMN tb_grp_code.create_user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_grp_code.create_user_id IS '생성자';


--
-- TOC entry 4921 (class 0 OID 0)
-- Dependencies: 238
-- Name: COLUMN tb_grp_code.update_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_grp_code.update_dt IS '수정일자';


--
-- TOC entry 4922 (class 0 OID 0)
-- Dependencies: 238
-- Name: COLUMN tb_grp_code.update_user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_grp_code.update_user_id IS '수정자';


--
-- TOC entry 239 (class 1259 OID 24698)
-- Name: tb_job; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_job (
    region character varying(50) NOT NULL,
    job_idn character varying(255) NOT NULL,
    job_nm character varying(255) NOT NULL,
    job_desc character varying(4000),
    cron_exp character varying(100) NOT NULL,
    job_int character varying(50),
    job_ext character varying(50),
    use_yn character(1) DEFAULT 'Y'::bpchar,
    job_param character varying(4000),
    create_dt timestamp without time zone DEFAULT now(),
    create_user_id character varying(50),
    update_dt timestamp without time zone DEFAULT now(),
    update_user_id character varying(50),
    CONSTRAINT tb_job_use_yn_check CHECK ((use_yn = ANY (ARRAY['Y'::bpchar, 'N'::bpchar])))
);


--
-- TOC entry 4923 (class 0 OID 0)
-- Dependencies: 239
-- Name: TABLE tb_job; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_job IS 'JOB 등록 관리';


--
-- TOC entry 4924 (class 0 OID 0)
-- Dependencies: 239
-- Name: COLUMN tb_job.region; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_job.region IS '지역';


--
-- TOC entry 4925 (class 0 OID 0)
-- Dependencies: 239
-- Name: COLUMN tb_job.job_idn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_job.job_idn IS 'JOB번호';


--
-- TOC entry 4926 (class 0 OID 0)
-- Dependencies: 239
-- Name: COLUMN tb_job.job_nm; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_job.job_nm IS 'JOB명';


--
-- TOC entry 4927 (class 0 OID 0)
-- Dependencies: 239
-- Name: COLUMN tb_job.job_desc; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_job.job_desc IS 'JOB설명';


--
-- TOC entry 4928 (class 0 OID 0)
-- Dependencies: 239
-- Name: COLUMN tb_job.cron_exp; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_job.cron_exp IS 'Cron시간표현식';


--
-- TOC entry 4929 (class 0 OID 0)
-- Dependencies: 239
-- Name: COLUMN tb_job.job_int; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_job.job_int IS '실행주기';


--
-- TOC entry 4930 (class 0 OID 0)
-- Dependencies: 239
-- Name: COLUMN tb_job.use_yn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_job.use_yn IS '사용여부';


--
-- TOC entry 4931 (class 0 OID 0)
-- Dependencies: 239
-- Name: COLUMN tb_job.job_param; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_job.job_param IS '파라미터';


--
-- TOC entry 4932 (class 0 OID 0)
-- Dependencies: 239
-- Name: COLUMN tb_job.create_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_job.create_dt IS '생성일자';


--
-- TOC entry 4933 (class 0 OID 0)
-- Dependencies: 239
-- Name: COLUMN tb_job.create_user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_job.create_user_id IS '생성자';


--
-- TOC entry 4934 (class 0 OID 0)
-- Dependencies: 239
-- Name: COLUMN tb_job.update_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_job.update_dt IS '수정일자';


--
-- TOC entry 4935 (class 0 OID 0)
-- Dependencies: 239
-- Name: COLUMN tb_job.update_user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_job.update_user_id IS '수정자';


--
-- TOC entry 240 (class 1259 OID 24709)
-- Name: tb_menu; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_menu (
    region character varying(50) NOT NULL,
    menu_idn character varying(15) NOT NULL,
    pmenu_idn character varying(15),
    menu_nm character varying(50),
    app_path character varying(255),
    dup_load_yn character(1),
    use_yn character(1),
    menu_idx integer DEFAULT 0,
    width character varying(20),
    height character varying(20),
    menu_type character varying(20),
    create_dt timestamp without time zone,
    create_user_id character varying(50),
    update_dt timestamp without time zone,
    update_user_id character varying(50)
);


--
-- TOC entry 4936 (class 0 OID 0)
-- Dependencies: 240
-- Name: TABLE tb_menu; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_menu IS '기본정보 메뉴';


--
-- TOC entry 4937 (class 0 OID 0)
-- Dependencies: 240
-- Name: COLUMN tb_menu.region; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_menu.region IS '지역';


--
-- TOC entry 4938 (class 0 OID 0)
-- Dependencies: 240
-- Name: COLUMN tb_menu.menu_idn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_menu.menu_idn IS '메뉴코드';


--
-- TOC entry 4939 (class 0 OID 0)
-- Dependencies: 240
-- Name: COLUMN tb_menu.pmenu_idn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_menu.pmenu_idn IS '부모메뉴코드';


--
-- TOC entry 4940 (class 0 OID 0)
-- Dependencies: 240
-- Name: COLUMN tb_menu.menu_nm; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_menu.menu_nm IS '메뉴명';


--
-- TOC entry 4941 (class 0 OID 0)
-- Dependencies: 240
-- Name: COLUMN tb_menu.app_path; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_menu.app_path IS '메뉴경로';


--
-- TOC entry 4942 (class 0 OID 0)
-- Dependencies: 240
-- Name: COLUMN tb_menu.dup_load_yn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_menu.dup_load_yn IS '중복실행유무';


--
-- TOC entry 4943 (class 0 OID 0)
-- Dependencies: 240
-- Name: COLUMN tb_menu.use_yn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_menu.use_yn IS '사용여부';


--
-- TOC entry 4944 (class 0 OID 0)
-- Dependencies: 240
-- Name: COLUMN tb_menu.width; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_menu.width IS '너비';


--
-- TOC entry 4945 (class 0 OID 0)
-- Dependencies: 240
-- Name: COLUMN tb_menu.height; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_menu.height IS '높이';


--
-- TOC entry 4946 (class 0 OID 0)
-- Dependencies: 240
-- Name: COLUMN tb_menu.menu_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_menu.menu_type IS '메뉴타입';


--
-- TOC entry 4947 (class 0 OID 0)
-- Dependencies: 240
-- Name: COLUMN tb_menu.create_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_menu.create_dt IS '생성자';


--
-- TOC entry 4948 (class 0 OID 0)
-- Dependencies: 240
-- Name: COLUMN tb_menu.create_user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_menu.create_user_id IS '생성일시';


--
-- TOC entry 4949 (class 0 OID 0)
-- Dependencies: 240
-- Name: COLUMN tb_menu.update_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_menu.update_dt IS '수정자';


--
-- TOC entry 4950 (class 0 OID 0)
-- Dependencies: 240
-- Name: COLUMN tb_menu.update_user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_menu.update_user_id IS '수정일시';


--
-- TOC entry 241 (class 1259 OID 24717)
-- Name: tb_menu_api; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_menu_api (
    region character varying(50) NOT NULL,
    menu_idn character varying(15) NOT NULL,
    api_cd character varying(30) NOT NULL,
    api_url character varying(255) NOT NULL,
    api_gb character varying(10) NOT NULL,
    use_yn character(1) DEFAULT 'Y'::bpchar,
    create_dt timestamp without time zone,
    create_user_id character varying(50),
    update_dt timestamp without time zone,
    update_user_id character varying(50)
);


--
-- TOC entry 4951 (class 0 OID 0)
-- Dependencies: 241
-- Name: TABLE tb_menu_api; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_menu_api IS '메뉴별 사용api';


--
-- TOC entry 4952 (class 0 OID 0)
-- Dependencies: 241
-- Name: COLUMN tb_menu_api.region; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_menu_api.region IS '지역';


--
-- TOC entry 4953 (class 0 OID 0)
-- Dependencies: 241
-- Name: COLUMN tb_menu_api.menu_idn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_menu_api.menu_idn IS '메뉴코드';


--
-- TOC entry 4954 (class 0 OID 0)
-- Dependencies: 241
-- Name: COLUMN tb_menu_api.api_cd; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_menu_api.api_cd IS 'API코드';


--
-- TOC entry 4955 (class 0 OID 0)
-- Dependencies: 241
-- Name: COLUMN tb_menu_api.api_url; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_menu_api.api_url IS 'api호출url';


--
-- TOC entry 4956 (class 0 OID 0)
-- Dependencies: 241
-- Name: COLUMN tb_menu_api.api_gb; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_menu_api.api_gb IS 'api구분';


--
-- TOC entry 4957 (class 0 OID 0)
-- Dependencies: 241
-- Name: COLUMN tb_menu_api.use_yn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_menu_api.use_yn IS '사용여부';


--
-- TOC entry 4958 (class 0 OID 0)
-- Dependencies: 241
-- Name: COLUMN tb_menu_api.create_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_menu_api.create_dt IS '생성일자';


--
-- TOC entry 4959 (class 0 OID 0)
-- Dependencies: 241
-- Name: COLUMN tb_menu_api.create_user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_menu_api.create_user_id IS '생성자';


--
-- TOC entry 4960 (class 0 OID 0)
-- Dependencies: 241
-- Name: COLUMN tb_menu_api.update_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_menu_api.update_dt IS '수정일자';


--
-- TOC entry 4961 (class 0 OID 0)
-- Dependencies: 241
-- Name: COLUMN tb_menu_api.update_user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_menu_api.update_user_id IS '수정자';


--
-- TOC entry 315 (class 1259 OID 25914)
-- Name: tb_network_info; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_network_info (
    equipment_id character varying(64) NOT NULL,
    ip_address text,
    description text,
    meta jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- TOC entry 4962 (class 0 OID 0)
-- Dependencies: 315
-- Name: TABLE tb_network_info; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_network_info IS '장비 네트워크 속성 테이블 (IP, 확장 메타 포함)';


--
-- TOC entry 4963 (class 0 OID 0)
-- Dependencies: 315
-- Name: COLUMN tb_network_info.equipment_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_network_info.equipment_id IS '장비 ID (FK, tb_equipment_info 참조)';


--
-- TOC entry 4964 (class 0 OID 0)
-- Dependencies: 315
-- Name: COLUMN tb_network_info.ip_address; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_network_info.ip_address IS '장비 IP 정보, DDNS포함';


--
-- TOC entry 4965 (class 0 OID 0)
-- Dependencies: 315
-- Name: COLUMN tb_network_info.description; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_network_info.description IS '설명';


--
-- TOC entry 4966 (class 0 OID 0)
-- Dependencies: 315
-- Name: COLUMN tb_network_info.meta; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_network_info.meta IS '확장 메타데이터(JSONB) - 추가 속성(예: role, tag 등)';


--
-- TOC entry 4967 (class 0 OID 0)
-- Dependencies: 315
-- Name: COLUMN tb_network_info.created_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_network_info.created_at IS '생성일시';


--
-- TOC entry 4968 (class 0 OID 0)
-- Dependencies: 315
-- Name: COLUMN tb_network_info.updated_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_network_info.updated_at IS '수정일시';


--
-- TOC entry 242 (class 1259 OID 24723)
-- Name: tb_node; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_node (
    region character varying(20) NOT NULL,
    node_id character varying(50) NOT NULL,
    parent_id character varying(20),
    title character varying(100),
    type character varying(100),
    x_pos integer,
    y_pos integer,
    create_dt timestamp without time zone,
    create_user_id character varying(50),
    update_dt timestamp without time zone,
    update_user_id character varying(50)
);


--
-- TOC entry 243 (class 1259 OID 24728)
-- Name: tb_node_connection; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_node_connection (
    region character varying(20) NOT NULL,
    id integer NOT NULL,
    source_node_id character varying(20),
    target_node_id character varying(20),
    create_dt timestamp without time zone,
    create_user_id character varying(50),
    update_dt timestamp without time zone,
    update_user_id character varying(50)
);


--
-- TOC entry 310 (class 1259 OID 25736)
-- Name: tb_pressure_reducing_facility_info; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_pressure_reducing_facility_info (
    sitename character varying NOT NULL,
    general_overview jsonb,
    pressure_unit character varying,
    pressure_reduction_pattern character varying,
    pressure_reduction_criteria character varying,
    site_photo_url character varying,
    manual_url character varying,
    system_diagram_url character varying
);


--
-- TOC entry 4969 (class 0 OID 0)
-- Dependencies: 310
-- Name: TABLE tb_pressure_reducing_facility_info; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_pressure_reducing_facility_info IS '감압시설 기본 정보 테이블 (설치 정보, 감압 기준 포함)';


--
-- TOC entry 4970 (class 0 OID 0)
-- Dependencies: 310
-- Name: COLUMN tb_pressure_reducing_facility_info.sitename; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_pressure_reducing_facility_info.sitename IS '감압시설 명칭 (Primary Key)';


--
-- TOC entry 4971 (class 0 OID 0)
-- Dependencies: 310
-- Name: COLUMN tb_pressure_reducing_facility_info.general_overview; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_pressure_reducing_facility_info.general_overview IS '일반현황 JSONB (설치위치, 밸브 관경, 제어방식, 제조사 등)';


--
-- TOC entry 4972 (class 0 OID 0)
-- Dependencies: 310
-- Name: COLUMN tb_pressure_reducing_facility_info.pressure_unit; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_pressure_reducing_facility_info.pressure_unit IS '압력 단위 (예: kg/cm2)';


--
-- TOC entry 4973 (class 0 OID 0)
-- Dependencies: 310
-- Name: COLUMN tb_pressure_reducing_facility_info.pressure_reduction_pattern; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_pressure_reducing_facility_info.pressure_reduction_pattern IS '감압패턴 (예: 고정감압)';


--
-- TOC entry 4974 (class 0 OID 0)
-- Dependencies: 310
-- Name: COLUMN tb_pressure_reducing_facility_info.pressure_reduction_criteria; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_pressure_reducing_facility_info.pressure_reduction_criteria IS '감압기준 (예: 1.0kg/cm2)';


--
-- TOC entry 4975 (class 0 OID 0)
-- Dependencies: 310
-- Name: COLUMN tb_pressure_reducing_facility_info.site_photo_url; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_pressure_reducing_facility_info.site_photo_url IS '감압밸브 현장 URL';


--
-- TOC entry 4976 (class 0 OID 0)
-- Dependencies: 310
-- Name: COLUMN tb_pressure_reducing_facility_info.manual_url; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_pressure_reducing_facility_info.manual_url IS '감압밸브 초기 대응 매뉴얼 URL';


--
-- TOC entry 311 (class 1259 OID 25745)
-- Name: tb_pressure_reducing_facility_status; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_pressure_reducing_facility_status (
    sitename character varying NOT NULL,
    avg_inlet_pressure numeric,
    avg_outlet_pressure numeric,
    avg_pressure_reduction numeric,
    pressure_unit character varying,
    updated_at timestamp with time zone DEFAULT now(),
    status character varying,
    meta jsonb
);


--
-- TOC entry 4977 (class 0 OID 0)
-- Dependencies: 311
-- Name: TABLE tb_pressure_reducing_facility_status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_pressure_reducing_facility_status IS 'Pressure reducing facility status table (inlet/outlet pressure, reduction, operating status)';


--
-- TOC entry 4978 (class 0 OID 0)
-- Dependencies: 311
-- Name: COLUMN tb_pressure_reducing_facility_status.sitename; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_pressure_reducing_facility_status.sitename IS 'Facility name (PK, FK to tb_pressure_reducing_facility_info)';


--
-- TOC entry 4979 (class 0 OID 0)
-- Dependencies: 311
-- Name: COLUMN tb_pressure_reducing_facility_status.avg_inlet_pressure; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_pressure_reducing_facility_status.avg_inlet_pressure IS 'Inlet pressure';


--
-- TOC entry 4980 (class 0 OID 0)
-- Dependencies: 311
-- Name: COLUMN tb_pressure_reducing_facility_status.avg_outlet_pressure; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_pressure_reducing_facility_status.avg_outlet_pressure IS 'Outlet pressure';


--
-- TOC entry 4981 (class 0 OID 0)
-- Dependencies: 311
-- Name: COLUMN tb_pressure_reducing_facility_status.avg_pressure_reduction; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_pressure_reducing_facility_status.avg_pressure_reduction IS 'Average pressure reduction';


--
-- TOC entry 4982 (class 0 OID 0)
-- Dependencies: 311
-- Name: COLUMN tb_pressure_reducing_facility_status.pressure_unit; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_pressure_reducing_facility_status.pressure_unit IS 'Pressure unit (예: kg/cm2)';


--
-- TOC entry 4983 (class 0 OID 0)
-- Dependencies: 311
-- Name: COLUMN tb_pressure_reducing_facility_status.updated_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_pressure_reducing_facility_status.updated_at IS '마지막 수정 시각 (UPDATE 시 자동 변경)';


--
-- TOC entry 244 (class 1259 OID 24733)
-- Name: tb_prompt_column; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_prompt_column (
    region character varying(50) NOT NULL,
    template_id character varying(50) NOT NULL,
    column_name character varying(100) NOT NULL,
    use_yn character(1) DEFAULT 'N'::bpchar,
    create_dt timestamp without time zone,
    create_user_id character varying(50),
    update_dt timestamp without time zone,
    update_user_id character varying(50)
);


--
-- TOC entry 4984 (class 0 OID 0)
-- Dependencies: 244
-- Name: TABLE tb_prompt_column; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_prompt_column IS '프롬프트 컬럼관리';


--
-- TOC entry 4985 (class 0 OID 0)
-- Dependencies: 244
-- Name: COLUMN tb_prompt_column.region; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_prompt_column.region IS '지역';


--
-- TOC entry 4986 (class 0 OID 0)
-- Dependencies: 244
-- Name: COLUMN tb_prompt_column.template_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_prompt_column.template_id IS '템플릿ID';


--
-- TOC entry 4987 (class 0 OID 0)
-- Dependencies: 244
-- Name: COLUMN tb_prompt_column.column_name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_prompt_column.column_name IS '컬럼명';


--
-- TOC entry 4988 (class 0 OID 0)
-- Dependencies: 244
-- Name: COLUMN tb_prompt_column.use_yn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_prompt_column.use_yn IS '사용여부';


--
-- TOC entry 4989 (class 0 OID 0)
-- Dependencies: 244
-- Name: COLUMN tb_prompt_column.create_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_prompt_column.create_dt IS '생성일자';


--
-- TOC entry 4990 (class 0 OID 0)
-- Dependencies: 244
-- Name: COLUMN tb_prompt_column.create_user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_prompt_column.create_user_id IS '생성자';


--
-- TOC entry 4991 (class 0 OID 0)
-- Dependencies: 244
-- Name: COLUMN tb_prompt_column.update_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_prompt_column.update_dt IS '수정일자';


--
-- TOC entry 4992 (class 0 OID 0)
-- Dependencies: 244
-- Name: COLUMN tb_prompt_column.update_user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_prompt_column.update_user_id IS '수정자';


--
-- TOC entry 245 (class 1259 OID 24739)
-- Name: tb_prompt_template; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_prompt_template (
    region character varying(50) NOT NULL,
    template_id character varying(50) NOT NULL,
    template_nm character varying(100),
    prompt_format text,
    template_type character varying(50),
    use_yn character(1) DEFAULT 'N'::bpchar,
    create_dt timestamp without time zone,
    create_user_id character varying(50),
    update_dt timestamp without time zone,
    update_user_id character varying(50)
);


--
-- TOC entry 4993 (class 0 OID 0)
-- Dependencies: 245
-- Name: TABLE tb_prompt_template; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_prompt_template IS '프롬프트 템플릿 관리';


--
-- TOC entry 4994 (class 0 OID 0)
-- Dependencies: 245
-- Name: COLUMN tb_prompt_template.region; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_prompt_template.region IS '지역';


--
-- TOC entry 4995 (class 0 OID 0)
-- Dependencies: 245
-- Name: COLUMN tb_prompt_template.template_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_prompt_template.template_id IS '템플릿ID';


--
-- TOC entry 4996 (class 0 OID 0)
-- Dependencies: 245
-- Name: COLUMN tb_prompt_template.template_nm; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_prompt_template.template_nm IS '템플릿명';


--
-- TOC entry 4997 (class 0 OID 0)
-- Dependencies: 245
-- Name: COLUMN tb_prompt_template.prompt_format; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_prompt_template.prompt_format IS '프롬프트포맷';


--
-- TOC entry 4998 (class 0 OID 0)
-- Dependencies: 245
-- Name: COLUMN tb_prompt_template.template_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_prompt_template.template_type IS '템플릿타입';


--
-- TOC entry 4999 (class 0 OID 0)
-- Dependencies: 245
-- Name: COLUMN tb_prompt_template.use_yn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_prompt_template.use_yn IS '사용여부';


--
-- TOC entry 5000 (class 0 OID 0)
-- Dependencies: 245
-- Name: COLUMN tb_prompt_template.create_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_prompt_template.create_dt IS '생성일자';


--
-- TOC entry 5001 (class 0 OID 0)
-- Dependencies: 245
-- Name: COLUMN tb_prompt_template.create_user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_prompt_template.create_user_id IS '생성자';


--
-- TOC entry 5002 (class 0 OID 0)
-- Dependencies: 245
-- Name: COLUMN tb_prompt_template.update_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_prompt_template.update_dt IS '수정일자';


--
-- TOC entry 5003 (class 0 OID 0)
-- Dependencies: 245
-- Name: COLUMN tb_prompt_template.update_user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_prompt_template.update_user_id IS '수정자';


--
-- TOC entry 316 (class 1259 OID 25962)
-- Name: tb_protocol_lookup; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_protocol_lookup (
    protocol_code character varying(50) NOT NULL,
    display_name character varying(100) NOT NULL,
    protocol_type character varying(50) NOT NULL,
    description text,
    meta jsonb,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now()
);


--
-- TOC entry 5004 (class 0 OID 0)
-- Dependencies: 316
-- Name: TABLE tb_protocol_lookup; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_protocol_lookup IS '프로토콜 코드 참조 테이블';


--
-- TOC entry 5005 (class 0 OID 0)
-- Dependencies: 316
-- Name: COLUMN tb_protocol_lookup.protocol_code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_protocol_lookup.protocol_code IS '프로토콜 코드 (PK, tb_network_link.link_protocol 참조)';


--
-- TOC entry 5006 (class 0 OID 0)
-- Dependencies: 316
-- Name: COLUMN tb_protocol_lookup.display_name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_protocol_lookup.display_name IS '프로토콜 표시명';


--
-- TOC entry 5007 (class 0 OID 0)
-- Dependencies: 316
-- Name: COLUMN tb_protocol_lookup.protocol_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_protocol_lookup.protocol_type IS '프로토콜 유형 (예: TCP, UDP, Serial)';


--
-- TOC entry 5008 (class 0 OID 0)
-- Dependencies: 316
-- Name: COLUMN tb_protocol_lookup.description; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_protocol_lookup.description IS '프로토콜 설명';


--
-- TOC entry 5009 (class 0 OID 0)
-- Dependencies: 316
-- Name: COLUMN tb_protocol_lookup.meta; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_protocol_lookup.meta IS '프로토콜 관련 확장 메타데이터(JSONB)';


--
-- TOC entry 5010 (class 0 OID 0)
-- Dependencies: 316
-- Name: COLUMN tb_protocol_lookup.created_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_protocol_lookup.created_at IS '생성일시';


--
-- TOC entry 5011 (class 0 OID 0)
-- Dependencies: 316
-- Name: COLUMN tb_protocol_lookup.updated_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_protocol_lookup.updated_at IS '수정일시';


--
-- TOC entry 309 (class 1259 OID 25700)
-- Name: tb_service_booster_station_info; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_service_booster_station_info (
    sitename character varying(100) NOT NULL,
    general_overview jsonb,
    infiltration_well_count integer,
    zone_1_area numeric,
    zone_1_height numeric,
    zone_2_area numeric,
    zone_2_height numeric,
    infiltration_well_unit character varying(16) DEFAULT 'm²'::character varying,
    infiltration_well_level_unit character varying(16) DEFAULT 'm'::character varying,
    pump_type character varying(100),
    normal_operation_pump_count character varying(16),
    site_photo_url text,
    manual_url character varying,
    system_diagram_url character varying
);


--
-- TOC entry 5012 (class 0 OID 0)
-- Dependencies: 309
-- Name: TABLE tb_service_booster_station_info; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_service_booster_station_info IS '가압장 기본 정보 테이블 (설치정보, 흡수정, 펌프, 사진 포함)';


--
-- TOC entry 5013 (class 0 OID 0)
-- Dependencies: 309
-- Name: COLUMN tb_service_booster_station_info.sitename; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_booster_station_info.sitename IS '가압장 명칭 (Primary Key)';


--
-- TOC entry 5014 (class 0 OID 0)
-- Dependencies: 309
-- Name: COLUMN tb_service_booster_station_info.general_overview; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_booster_station_info.general_overview IS '일반현황 JSON (설치 위치, 연도, 용량, 펌프 구성 등)';


--
-- TOC entry 5015 (class 0 OID 0)
-- Dependencies: 309
-- Name: COLUMN tb_service_booster_station_info.infiltration_well_count; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_booster_station_info.infiltration_well_count IS '흡수정 개수';


--
-- TOC entry 5016 (class 0 OID 0)
-- Dependencies: 309
-- Name: COLUMN tb_service_booster_station_info.zone_1_area; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_booster_station_info.zone_1_area IS '흡수정 1지 면적';


--
-- TOC entry 5017 (class 0 OID 0)
-- Dependencies: 309
-- Name: COLUMN tb_service_booster_station_info.zone_1_height; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_booster_station_info.zone_1_height IS '흡수정 1지 높이';


--
-- TOC entry 5018 (class 0 OID 0)
-- Dependencies: 309
-- Name: COLUMN tb_service_booster_station_info.zone_2_area; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_booster_station_info.zone_2_area IS '흡수정 2지 면적';


--
-- TOC entry 5019 (class 0 OID 0)
-- Dependencies: 309
-- Name: COLUMN tb_service_booster_station_info.zone_2_height; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_booster_station_info.zone_2_height IS '흡수정 2지 높이';


--
-- TOC entry 5020 (class 0 OID 0)
-- Dependencies: 309
-- Name: COLUMN tb_service_booster_station_info.infiltration_well_unit; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_booster_station_info.infiltration_well_unit IS '흡수정 면적 단위 (기본: m²)';


--
-- TOC entry 5021 (class 0 OID 0)
-- Dependencies: 309
-- Name: COLUMN tb_service_booster_station_info.infiltration_well_level_unit; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_booster_station_info.infiltration_well_level_unit IS '흡수정 높이 단위 (기본: m)';


--
-- TOC entry 5022 (class 0 OID 0)
-- Dependencies: 309
-- Name: COLUMN tb_service_booster_station_info.pump_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_booster_station_info.pump_type IS '펌프 타입 (흡수정, 인라인 등)';


--
-- TOC entry 5023 (class 0 OID 0)
-- Dependencies: 309
-- Name: COLUMN tb_service_booster_station_info.normal_operation_pump_count; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_booster_station_info.normal_operation_pump_count IS '평상시 펌프 운영 대수';


--
-- TOC entry 5024 (class 0 OID 0)
-- Dependencies: 309
-- Name: COLUMN tb_service_booster_station_info.site_photo_url; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_booster_station_info.site_photo_url IS '가압장 현장 사진 URL';


--
-- TOC entry 5025 (class 0 OID 0)
-- Dependencies: 309
-- Name: COLUMN tb_service_booster_station_info.manual_url; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_booster_station_info.manual_url IS '가압장 초기대응 매뉴얼 URL';


--
-- TOC entry 321 (class 1259 OID 27096)
-- Name: tb_service_booster_station_status; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_service_booster_station_status (
    sitename character varying(100) NOT NULL,
    pump_control_mode character varying(100),
    pump_start_threshold numeric,
    pump_stop_threshold numeric,
    pump_start_pressure numeric,
    booster_inlet_pressure numeric,
    booster_outlet_pressure numeric,
    booster_avg_pressure numeric,
    level_unit character varying(16) DEFAULT 'm'::character varying,
    pressure_unit character varying(16) DEFAULT 'kg/cm2'::character varying,
    running_pump_count integer,
    meta jsonb,
    linked_reservoir_name character varying
);


--
-- TOC entry 5026 (class 0 OID 0)
-- Dependencies: 321
-- Name: TABLE tb_service_booster_station_status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_service_booster_station_status IS '가압장 상태 정보 테이블 (제어 방식, 설정값, 계측 압력, 평균 가압량 등 포함)';


--
-- TOC entry 5027 (class 0 OID 0)
-- Dependencies: 321
-- Name: COLUMN tb_service_booster_station_status.sitename; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_booster_station_status.sitename IS '가압장 명칭 (PK, tb_service_booster_station_info 참조)';


--
-- TOC entry 5028 (class 0 OID 0)
-- Dependencies: 321
-- Name: COLUMN tb_service_booster_station_status.pump_control_mode; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_booster_station_status.pump_control_mode IS '펌프 제어 방식 (예: 수위방식 / 압력제어방식)';


--
-- TOC entry 5029 (class 0 OID 0)
-- Dependencies: 321
-- Name: COLUMN tb_service_booster_station_status.pump_start_threshold; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_booster_station_status.pump_start_threshold IS '펌프 시작 수위 (수위방식, 단위: m)';


--
-- TOC entry 5030 (class 0 OID 0)
-- Dependencies: 321
-- Name: COLUMN tb_service_booster_station_status.pump_stop_threshold; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_booster_station_status.pump_stop_threshold IS '펌프 정지 수위 (수위방식, 단위: m)';


--
-- TOC entry 5031 (class 0 OID 0)
-- Dependencies: 321
-- Name: COLUMN tb_service_booster_station_status.pump_start_pressure; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_booster_station_status.pump_start_pressure IS '펌프 시작 압력 (압력방식, 단위: kg/cm2)';


--
-- TOC entry 5032 (class 0 OID 0)
-- Dependencies: 321
-- Name: COLUMN tb_service_booster_station_status.booster_inlet_pressure; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_booster_station_status.booster_inlet_pressure IS '가압장 전단 압력 (계측값)';


--
-- TOC entry 5033 (class 0 OID 0)
-- Dependencies: 321
-- Name: COLUMN tb_service_booster_station_status.booster_outlet_pressure; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_booster_station_status.booster_outlet_pressure IS '가압장 후단 압력 (계측값)';


--
-- TOC entry 5034 (class 0 OID 0)
-- Dependencies: 321
-- Name: COLUMN tb_service_booster_station_status.booster_avg_pressure; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_booster_station_status.booster_avg_pressure IS '평균 가압량';


--
-- TOC entry 5035 (class 0 OID 0)
-- Dependencies: 321
-- Name: COLUMN tb_service_booster_station_status.level_unit; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_booster_station_status.level_unit IS '수위 단위 (기본: m)';


--
-- TOC entry 5036 (class 0 OID 0)
-- Dependencies: 321
-- Name: COLUMN tb_service_booster_station_status.pressure_unit; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_booster_station_status.pressure_unit IS '압력 단위 (기본: kg/cm2)';


--
-- TOC entry 5037 (class 0 OID 0)
-- Dependencies: 321
-- Name: COLUMN tb_service_booster_station_status.linked_reservoir_name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_booster_station_status.linked_reservoir_name IS '''제어 기준이 되는 배수지명 (수위제어방식 시 참조)''';


--
-- TOC entry 324 (class 1259 OID 30100)
-- Name: tb_service_reservoir_info; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_service_reservoir_info (
    sitename character varying NOT NULL,
    general_overview jsonb,
    install_year character varying,
    service_area character varying,
    zone_count integer,
    zone_1_area numeric,
    zone_1_height numeric,
    zone_2_area numeric,
    zone_2_height numeric,
    zone_3_area numeric,
    zone_3_height numeric,
    zone_4_area numeric,
    zone_4_height numeric,
    zone_5_area numeric,
    zone_5_height numeric,
    avg_reservoir_level numeric,
    water_level_unit character varying,
    reservoir_area_unit character varying,
    site_photo_url character varying,
    manual_url character varying,
    system_diagram_url character varying
);


--
-- TOC entry 5038 (class 0 OID 0)
-- Dependencies: 324
-- Name: TABLE tb_service_reservoir_info; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_service_reservoir_info IS 'Reservoir (배수지) base info table: overview, zones, capacity, photo etc.';


--
-- TOC entry 5039 (class 0 OID 0)
-- Dependencies: 324
-- Name: COLUMN tb_service_reservoir_info.sitename; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_info.sitename IS 'Reservoir name (PK, 배수지 명칭)';


--
-- TOC entry 5040 (class 0 OID 0)
-- Dependencies: 324
-- Name: COLUMN tb_service_reservoir_info.general_overview; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_info.general_overview IS 'General overview in JSONB (일반현황, 메타/구조)';


--
-- TOC entry 5041 (class 0 OID 0)
-- Dependencies: 324
-- Name: COLUMN tb_service_reservoir_info.install_year; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_info.install_year IS 'Install year (배수지 설치년도)';


--
-- TOC entry 5042 (class 0 OID 0)
-- Dependencies: 324
-- Name: COLUMN tb_service_reservoir_info.service_area; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_info.service_area IS 'Water supply service area (급수대상지역)';


--
-- TOC entry 5043 (class 0 OID 0)
-- Dependencies: 324
-- Name: COLUMN tb_service_reservoir_info.zone_count; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_info.zone_count IS 'Number of zones (배수지 구획/수량)';


--
-- TOC entry 5044 (class 0 OID 0)
-- Dependencies: 324
-- Name: COLUMN tb_service_reservoir_info.zone_1_area; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_info.zone_1_area IS '1st zone area (m²)';


--
-- TOC entry 5045 (class 0 OID 0)
-- Dependencies: 324
-- Name: COLUMN tb_service_reservoir_info.zone_1_height; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_info.zone_1_height IS '1st zone max water level (m)';


--
-- TOC entry 5046 (class 0 OID 0)
-- Dependencies: 324
-- Name: COLUMN tb_service_reservoir_info.zone_2_area; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_info.zone_2_area IS '2nd zone area (m²)';


--
-- TOC entry 5047 (class 0 OID 0)
-- Dependencies: 324
-- Name: COLUMN tb_service_reservoir_info.zone_2_height; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_info.zone_2_height IS '2nd zone max water level (m)';


--
-- TOC entry 5048 (class 0 OID 0)
-- Dependencies: 324
-- Name: COLUMN tb_service_reservoir_info.zone_3_area; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_info.zone_3_area IS '3rd zone area (m²)';


--
-- TOC entry 5049 (class 0 OID 0)
-- Dependencies: 324
-- Name: COLUMN tb_service_reservoir_info.zone_3_height; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_info.zone_3_height IS '3rd zone max water level (m)';


--
-- TOC entry 5050 (class 0 OID 0)
-- Dependencies: 324
-- Name: COLUMN tb_service_reservoir_info.zone_4_area; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_info.zone_4_area IS '4th zone area (m²)';


--
-- TOC entry 5051 (class 0 OID 0)
-- Dependencies: 324
-- Name: COLUMN tb_service_reservoir_info.zone_4_height; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_info.zone_4_height IS '4th zone max water level (m)';


--
-- TOC entry 5052 (class 0 OID 0)
-- Dependencies: 324
-- Name: COLUMN tb_service_reservoir_info.zone_5_area; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_info.zone_5_area IS '5th zone area (m²)';


--
-- TOC entry 5053 (class 0 OID 0)
-- Dependencies: 324
-- Name: COLUMN tb_service_reservoir_info.zone_5_height; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_info.zone_5_height IS '5th zone max water level (m)';


--
-- TOC entry 5054 (class 0 OID 0)
-- Dependencies: 324
-- Name: COLUMN tb_service_reservoir_info.avg_reservoir_level; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_info.avg_reservoir_level IS 'Average reservoir operating water level (평균 운영 수위, m)';


--
-- TOC entry 5055 (class 0 OID 0)
-- Dependencies: 324
-- Name: COLUMN tb_service_reservoir_info.water_level_unit; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_info.water_level_unit IS 'Water level unit (예: m)';


--
-- TOC entry 5056 (class 0 OID 0)
-- Dependencies: 324
-- Name: COLUMN tb_service_reservoir_info.reservoir_area_unit; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_info.reservoir_area_unit IS 'Reservoir area unit (예: m², m³)';


--
-- TOC entry 5057 (class 0 OID 0)
-- Dependencies: 324
-- Name: COLUMN tb_service_reservoir_info.site_photo_url; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_info.site_photo_url IS 'Site photo URL (현장 주소 사진)';


--
-- TOC entry 5058 (class 0 OID 0)
-- Dependencies: 324
-- Name: COLUMN tb_service_reservoir_info.manual_url; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_info.manual_url IS 'manual_url(초기대응 매뉴얼 사진)';


--
-- TOC entry 308 (class 1259 OID 25641)
-- Name: tb_service_reservoir_status; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_service_reservoir_status (
    sitename character varying NOT NULL,
    current_water_level numeric,
    alarm_high_water_level numeric,
    alarm_low_water_level numeric,
    water_level_unit character varying,
    zone_count integer,
    zone_1_supply_time numeric,
    zone_2_supply_time numeric,
    zone_3_supply_time numeric,
    zone_4_supply_time numeric,
    zone_5_supply_time numeric,
    avg_inlet_pressure numeric,
    avg_outlet_pressure numeric,
    pressure_unit character varying,
    last_updated_at timestamp with time zone,
    meta jsonb,
    total_supply_time numeric
);


--
-- TOC entry 5059 (class 0 OID 0)
-- Dependencies: 308
-- Name: TABLE tb_service_reservoir_status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_service_reservoir_status IS 'Reservoir operation status table (inflow, outflow, water level, zone, pressure, 운영 상태 포함)';


--
-- TOC entry 5060 (class 0 OID 0)
-- Dependencies: 308
-- Name: COLUMN tb_service_reservoir_status.sitename; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_status.sitename IS 'Reservoir name (PK, FK, references tb_reservoir_info.sitename)';


--
-- TOC entry 5061 (class 0 OID 0)
-- Dependencies: 308
-- Name: COLUMN tb_service_reservoir_status.current_water_level; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_status.current_water_level IS 'Current water level';


--
-- TOC entry 5062 (class 0 OID 0)
-- Dependencies: 308
-- Name: COLUMN tb_service_reservoir_status.alarm_high_water_level; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_status.alarm_high_water_level IS 'High alarm water level (HH)';


--
-- TOC entry 5063 (class 0 OID 0)
-- Dependencies: 308
-- Name: COLUMN tb_service_reservoir_status.alarm_low_water_level; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_status.alarm_low_water_level IS 'Low alarm water level (LL)';


--
-- TOC entry 5064 (class 0 OID 0)
-- Dependencies: 308
-- Name: COLUMN tb_service_reservoir_status.water_level_unit; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_status.water_level_unit IS 'Water level unit (ex: m)';


--
-- TOC entry 5065 (class 0 OID 0)
-- Dependencies: 308
-- Name: COLUMN tb_service_reservoir_status.zone_count; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_status.zone_count IS 'Number of reservoir zones';


--
-- TOC entry 5066 (class 0 OID 0)
-- Dependencies: 308
-- Name: COLUMN tb_service_reservoir_status.zone_1_supply_time; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_status.zone_1_supply_time IS '1st zone supply available time';


--
-- TOC entry 5067 (class 0 OID 0)
-- Dependencies: 308
-- Name: COLUMN tb_service_reservoir_status.zone_2_supply_time; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_status.zone_2_supply_time IS '2nd zone supply available time';


--
-- TOC entry 5068 (class 0 OID 0)
-- Dependencies: 308
-- Name: COLUMN tb_service_reservoir_status.zone_3_supply_time; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_status.zone_3_supply_time IS '3rd zone supply available time';


--
-- TOC entry 5069 (class 0 OID 0)
-- Dependencies: 308
-- Name: COLUMN tb_service_reservoir_status.zone_4_supply_time; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_status.zone_4_supply_time IS '4th zone supply available time';


--
-- TOC entry 5070 (class 0 OID 0)
-- Dependencies: 308
-- Name: COLUMN tb_service_reservoir_status.zone_5_supply_time; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_status.zone_5_supply_time IS '5th zone supply available time';


--
-- TOC entry 5071 (class 0 OID 0)
-- Dependencies: 308
-- Name: COLUMN tb_service_reservoir_status.avg_inlet_pressure; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_status.avg_inlet_pressure IS 'Average inlet pressure';


--
-- TOC entry 5072 (class 0 OID 0)
-- Dependencies: 308
-- Name: COLUMN tb_service_reservoir_status.avg_outlet_pressure; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_status.avg_outlet_pressure IS 'Average outlet pressure';


--
-- TOC entry 5073 (class 0 OID 0)
-- Dependencies: 308
-- Name: COLUMN tb_service_reservoir_status.pressure_unit; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_status.pressure_unit IS 'Pressure unit (ex: kgf/cm2)';


--
-- TOC entry 5074 (class 0 OID 0)
-- Dependencies: 308
-- Name: COLUMN tb_service_reservoir_status.last_updated_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_status.last_updated_at IS 'Last update time (UTC)';


--
-- TOC entry 5075 (class 0 OID 0)
-- Dependencies: 308
-- Name: COLUMN tb_service_reservoir_status.meta; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_service_reservoir_status.meta IS '설비 실시간 메타정보';


--
-- TOC entry 381 (class 1259 OID 37254)
-- Name: tb_task_master; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_task_master (
    task_id bigint NOT NULL,
    sitename character varying(100) NOT NULL,
    facilitytype character varying(100) NOT NULL,
    task_category character varying(100) NOT NULL,
    task_start_time timestamp without time zone NOT NULL,
    task_end_time timestamp without time zone,
    suspend_alarm_types jsonb,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now(),
    task_content text
);


--
-- TOC entry 5076 (class 0 OID 0)
-- Dependencies: 381
-- Name: TABLE tb_task_master; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_task_master IS '작업(Task) 기본 정보 관리 테이블';


--
-- TOC entry 5077 (class 0 OID 0)
-- Dependencies: 381
-- Name: COLUMN tb_task_master.task_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_task_master.task_id IS '작업 고유 ID(PK)';


--
-- TOC entry 5078 (class 0 OID 0)
-- Dependencies: 381
-- Name: COLUMN tb_task_master.sitename; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_task_master.sitename IS '현장명';


--
-- TOC entry 5079 (class 0 OID 0)
-- Dependencies: 381
-- Name: COLUMN tb_task_master.facilitytype; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_task_master.facilitytype IS '시설 유형';


--
-- TOC entry 5080 (class 0 OID 0)
-- Dependencies: 381
-- Name: COLUMN tb_task_master.task_category; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_task_master.task_category IS '작업 카테고리 (점검, 정비, 교체 등)';


--
-- TOC entry 5081 (class 0 OID 0)
-- Dependencies: 381
-- Name: COLUMN tb_task_master.task_start_time; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_task_master.task_start_time IS '작업 시작 일시';


--
-- TOC entry 5082 (class 0 OID 0)
-- Dependencies: 381
-- Name: COLUMN tb_task_master.task_end_time; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_task_master.task_end_time IS '작업 종료 일시';


--
-- TOC entry 5083 (class 0 OID 0)
-- Dependencies: 381
-- Name: COLUMN tb_task_master.suspend_alarm_types; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_task_master.suspend_alarm_types IS '작업 기간 동안 중지할 알람 종류(JSON 배열)';


--
-- TOC entry 5084 (class 0 OID 0)
-- Dependencies: 381
-- Name: COLUMN tb_task_master.created_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_task_master.created_at IS '레코드 생성 시각';


--
-- TOC entry 5085 (class 0 OID 0)
-- Dependencies: 381
-- Name: COLUMN tb_task_master.updated_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_task_master.updated_at IS '레코드 수정 시각 (트리거로 자동 업데이트)';


--
-- TOC entry 5086 (class 0 OID 0)
-- Dependencies: 381
-- Name: COLUMN tb_task_master.task_content; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_task_master.task_content IS '작업내용';


--
-- TOC entry 380 (class 1259 OID 37253)
-- Name: tb_task_master_task_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tb_task_master_task_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5087 (class 0 OID 0)
-- Dependencies: 380
-- Name: tb_task_master_task_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tb_task_master_task_id_seq OWNED BY public.tb_task_master.task_id;


--
-- TOC entry 323 (class 1259 OID 27147)
-- Name: tb_trend_catalog; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_trend_catalog (
    trend_id bigint NOT NULL,
    sitename text NOT NULL,
    facilitytype text NOT NULL,
    trend_name text NOT NULL,
    meta jsonb NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- TOC entry 322 (class 1259 OID 27146)
-- Name: tb_trend_catalog_trend_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tb_trend_catalog_trend_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5088 (class 0 OID 0)
-- Dependencies: 322
-- Name: tb_trend_catalog_trend_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tb_trend_catalog_trend_id_seq OWNED BY public.tb_trend_catalog.trend_id;


--
-- TOC entry 246 (class 1259 OID 24747)
-- Name: tb_user; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_user (
    region character varying(50) NOT NULL,
    user_id character varying(45) NOT NULL,
    user_nm character varying(50),
    user_pw character varying(100),
    plc_bis character varying(50),
    user_det character varying(50),
    user_auth character varying(50),
    user_tel character varying(50),
    user_phe character varying(50),
    user_mail character varying(50),
    remark character varying(255),
    lock_cnt character(1) DEFAULT '0'::bpchar,
    create_dt timestamp without time zone,
    create_user_id character varying(50),
    update_dt timestamp without time zone,
    update_user_id character varying(50),
    use_yn character(1) DEFAULT 'Y'::bpchar NOT NULL,
    dup_yn character(1) DEFAULT 'N'::bpchar NOT NULL,
    current_session_id character varying(100)
);


--
-- TOC entry 5089 (class 0 OID 0)
-- Dependencies: 246
-- Name: TABLE tb_user; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_user IS '기본정보 사용자';


--
-- TOC entry 5090 (class 0 OID 0)
-- Dependencies: 246
-- Name: COLUMN tb_user.region; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_user.region IS '지역';


--
-- TOC entry 5091 (class 0 OID 0)
-- Dependencies: 246
-- Name: COLUMN tb_user.user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_user.user_id IS '사용자ID';


--
-- TOC entry 5092 (class 0 OID 0)
-- Dependencies: 246
-- Name: COLUMN tb_user.user_nm; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_user.user_nm IS '사용자명';


--
-- TOC entry 5093 (class 0 OID 0)
-- Dependencies: 246
-- Name: COLUMN tb_user.user_pw; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_user.user_pw IS '비밀번호';


--
-- TOC entry 5094 (class 0 OID 0)
-- Dependencies: 246
-- Name: COLUMN tb_user.user_det; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_user.user_det IS '부서';


--
-- TOC entry 5095 (class 0 OID 0)
-- Dependencies: 246
-- Name: COLUMN tb_user.user_auth; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_user.user_auth IS '사용자권한';


--
-- TOC entry 5096 (class 0 OID 0)
-- Dependencies: 246
-- Name: COLUMN tb_user.user_tel; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_user.user_tel IS '전화번호';


--
-- TOC entry 5097 (class 0 OID 0)
-- Dependencies: 246
-- Name: COLUMN tb_user.user_phe; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_user.user_phe IS '핸드폰번호';


--
-- TOC entry 5098 (class 0 OID 0)
-- Dependencies: 246
-- Name: COLUMN tb_user.user_mail; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_user.user_mail IS '이메일';


--
-- TOC entry 5099 (class 0 OID 0)
-- Dependencies: 246
-- Name: COLUMN tb_user.remark; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_user.remark IS '비고';


--
-- TOC entry 5100 (class 0 OID 0)
-- Dependencies: 246
-- Name: COLUMN tb_user.lock_cnt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_user.lock_cnt IS '잠김카운트';


--
-- TOC entry 5101 (class 0 OID 0)
-- Dependencies: 246
-- Name: COLUMN tb_user.create_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_user.create_dt IS '생성일자';


--
-- TOC entry 5102 (class 0 OID 0)
-- Dependencies: 246
-- Name: COLUMN tb_user.create_user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_user.create_user_id IS '생성자';


--
-- TOC entry 5103 (class 0 OID 0)
-- Dependencies: 246
-- Name: COLUMN tb_user.update_dt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_user.update_dt IS '수정일자';


--
-- TOC entry 5104 (class 0 OID 0)
-- Dependencies: 246
-- Name: COLUMN tb_user.update_user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_user.update_user_id IS '수정자';


--
-- TOC entry 5105 (class 0 OID 0)
-- Dependencies: 246
-- Name: COLUMN tb_user.current_session_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_user.current_session_id IS '현재 로그인 세션 ID';


--
-- TOC entry 247 (class 1259 OID 24757)
-- Name: tb_user_session; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tb_user_session (
    session_id character varying(100) NOT NULL,
    region character varying(50) NOT NULL,
    user_id character varying(45) NOT NULL,
    login_time timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    last_active timestamp without time zone,
    logout_time timestamp without time zone,
    is_expired character(1) DEFAULT 'N'::bpchar,
    device_type character varying(20),
    browser character varying(50),
    os character varying(50),
    client_ip character varying(50),
    user_agent character varying(1000)
);


--
-- TOC entry 5106 (class 0 OID 0)
-- Dependencies: 247
-- Name: TABLE tb_user_session; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tb_user_session IS '사용자 접속 관리';


--
-- TOC entry 5107 (class 0 OID 0)
-- Dependencies: 247
-- Name: COLUMN tb_user_session.session_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_user_session.session_id IS '세션ID';


--
-- TOC entry 5108 (class 0 OID 0)
-- Dependencies: 247
-- Name: COLUMN tb_user_session.region; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_user_session.region IS '지역';


--
-- TOC entry 5109 (class 0 OID 0)
-- Dependencies: 247
-- Name: COLUMN tb_user_session.user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_user_session.user_id IS '사용자ID';


--
-- TOC entry 5110 (class 0 OID 0)
-- Dependencies: 247
-- Name: COLUMN tb_user_session.login_time; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_user_session.login_time IS '로그인시간';


--
-- TOC entry 5111 (class 0 OID 0)
-- Dependencies: 247
-- Name: COLUMN tb_user_session.last_active; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_user_session.last_active IS '마지막접속시간';


--
-- TOC entry 5112 (class 0 OID 0)
-- Dependencies: 247
-- Name: COLUMN tb_user_session.logout_time; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_user_session.logout_time IS '로그아웃시간';


--
-- TOC entry 5113 (class 0 OID 0)
-- Dependencies: 247
-- Name: COLUMN tb_user_session.is_expired; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_user_session.is_expired IS '폐기여부';


--
-- TOC entry 5114 (class 0 OID 0)
-- Dependencies: 247
-- Name: COLUMN tb_user_session.device_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_user_session.device_type IS '접속장비';


--
-- TOC entry 5115 (class 0 OID 0)
-- Dependencies: 247
-- Name: COLUMN tb_user_session.browser; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_user_session.browser IS '브라우저';


--
-- TOC entry 5116 (class 0 OID 0)
-- Dependencies: 247
-- Name: COLUMN tb_user_session.os; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_user_session.os IS 'OS';


--
-- TOC entry 5117 (class 0 OID 0)
-- Dependencies: 247
-- Name: COLUMN tb_user_session.client_ip; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tb_user_session.client_ip IS '접속IP';


--
-- TOC entry 390 (class 1259 OID 38229)
-- Name: v_block_info_status; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_block_info_status AS
 WITH date_range AS (
         SELECT (CURRENT_DATE - '8 days'::interval) AS ts_from_8d,
            ((CURRENT_DATE - '7 days'::interval))::date AS d_from_7d,
            (CURRENT_DATE)::timestamp without time zone AS today_from,
            (now())::timestamp without time zone AS now_ts,
            NULLIF((EXTRACT(epoch FROM (now() - date_trunc('day'::text, now()))) / (3600)::numeric), (0)::numeric) AS elapsed_hours_today
        ), block_daily_flow AS (
         WITH base AS (
                 SELECT ti.sitename,
                    ti.tagsn,
                    r.logtime,
                    r.val
                   FROM (public.tb_tag_raw_data r
                     JOIN public.tb_tag_info ti ON (((ti.tagsn)::text = (r.tagsn)::text)))
                  WHERE (((ti.facilitytype)::text = '소블록'::text) AND ((ti.datainfo)::text ~~* '%유량%적산%'::text) AND (r.val IS NOT NULL) AND (r.val <> (0)::numeric) AND (r.logtime >= ( SELECT date_range.ts_from_8d
                           FROM date_range)) AND (r.logtime < ( SELECT date_range.now_ts
                           FROM date_range)))
                ), deltas AS (
                 SELECT base.sitename,
                    base.tagsn,
                    base.logtime,
                    (base.val - lag(base.val) OVER (PARTITION BY base.tagsn ORDER BY base.logtime)) AS delta
                   FROM base
                )
         SELECT deltas.sitename,
            (date_trunc('day'::text, deltas.logtime))::date AS d,
            sum(
                CASE
                    WHEN (deltas.delta IS NULL) THEN (0)::numeric
                    WHEN (deltas.delta < (0)::numeric) THEN (0)::numeric
                    ELSE deltas.delta
                END) AS daily_flow_m3
           FROM deltas
          GROUP BY deltas.sitename, ((date_trunc('day'::text, deltas.logtime))::date)
        ), block_flow_summary AS (
         SELECT b.sitename,
            round(avg(b.daily_flow_m3), 2) AS block_daily_usage
           FROM block_daily_flow b
          WHERE (b.d >= ( SELECT date_range.d_from_7d
                   FROM date_range))
          GROUP BY b.sitename
        ), today_usage AS (
         WITH base AS (
                 SELECT ti.sitename,
                    ti.tagsn,
                    r.logtime,
                    r.val
                   FROM (public.tb_tag_raw_data r
                     JOIN public.tb_tag_info ti ON (((ti.tagsn)::text = (r.tagsn)::text)))
                  WHERE (((ti.facilitytype)::text = '소블록'::text) AND ((ti.datainfo)::text ~~* '%유량%적산%'::text) AND (r.val IS NOT NULL) AND (r.val <> (0)::numeric) AND (r.logtime >= ( SELECT date_range.today_from
                           FROM date_range)) AND (r.logtime <= ( SELECT date_range.now_ts
                           FROM date_range)))
                ), deltas AS (
                 SELECT base.sitename,
                    base.tagsn,
                    base.logtime,
                    (base.val - lag(base.val) OVER (PARTITION BY base.tagsn ORDER BY base.logtime)) AS delta
                   FROM base
                ), sum_today AS (
                 SELECT deltas.sitename,
                    sum(
                        CASE
                            WHEN (deltas.delta IS NULL) THEN (0)::numeric
                            WHEN (deltas.delta < (0)::numeric) THEN (0)::numeric
                            ELSE deltas.delta
                        END) AS today_sum_m3
                   FROM deltas
                  GROUP BY deltas.sitename
                )
         SELECT s_1.sitename,
            round((s_1.today_sum_m3 / ( SELECT date_range.elapsed_hours_today
                   FROM date_range)), 2) AS avg_usage
           FROM sum_today s_1
        ), reservoir_daily_outflow AS (
         WITH base AS (
                 SELECT ti.sitename,
                    ti.tagsn,
                    r.logtime,
                    r.val
                   FROM (public.tb_tag_raw_data r
                     JOIN public.tb_tag_info ti ON (((ti.tagsn)::text = (r.tagsn)::text)))
                  WHERE (((ti.facilitytype)::text = '배수지'::text) AND ((ti.datainfo)::text ~~* '%유출%적산%'::text) AND (r.val IS NOT NULL) AND (r.val <> (0)::numeric) AND (r.logtime >= ( SELECT date_range.ts_from_8d
                           FROM date_range)) AND (r.logtime < ( SELECT date_range.now_ts
                           FROM date_range)))
                ), deltas AS (
                 SELECT base.sitename,
                    base.tagsn,
                    base.logtime,
                    (base.val - lag(base.val) OVER (PARTITION BY base.tagsn ORDER BY base.logtime)) AS delta
                   FROM base
                )
         SELECT deltas.sitename,
            (date_trunc('day'::text, deltas.logtime))::date AS d,
            sum(
                CASE
                    WHEN (deltas.delta IS NULL) THEN (0)::numeric
                    WHEN (deltas.delta < (0)::numeric) THEN (0)::numeric
                    ELSE deltas.delta
                END) AS daily_outflow_m3
           FROM deltas
          GROUP BY deltas.sitename, ((date_trunc('day'::text, deltas.logtime))::date)
        ), reservoir_outflow_total AS (
         WITH per_reservoir_avg AS (
                 SELECT reservoir_daily_outflow.sitename,
                    round(avg(reservoir_daily_outflow.daily_outflow_m3), 2) AS avg_outflow_m3d
                   FROM reservoir_daily_outflow
                  WHERE (reservoir_daily_outflow.d >= ( SELECT date_range.d_from_7d
                           FROM date_range))
                  GROUP BY reservoir_daily_outflow.sitename
                )
         SELECT round(sum(per_reservoir_avg.avg_outflow_m3d), 2) AS total_outflow_m3d
           FROM per_reservoir_avg
        )
 SELECT i.sitename,
    i.general_overview,
    i.critical_pressure,
    i.pressure_unit AS info_pressure_unit,
    i.site_photo_url,
    i.manual_url,
    i.system_diagram_url,
    i.block_level,
    s.flow_missing_rate,
    s.flow_missing_rate_unit,
    s.block_avg_daily_flow,
    s.block_avg_daily_flow_ratio,
    s.block_pressure,
    s.block_inlet_pressure,
    s.block_outlet_pressure,
    s.inlet_pressure_variation_rate,
    s.outlet_pressure_variation_rate,
    s.flow_unit,
    s.pressure_unit AS status_pressure_unit,
    s.meta,
    s.updated_at,
    s.block_level AS status_block_level,
    f.block_daily_usage,
    '㎥/d'::text AS daily_flow_unit,
    t.avg_usage,
    '㎥/h'::text AS avg_usage_unit,
    rot.total_outflow_m3d AS daily_supply,
    '㎥/d'::text AS daily_supply_unit,
    round(((COALESCE(f.block_daily_usage, (0)::numeric) / NULLIF(rot.total_outflow_m3d, (0)::numeric)) * (100)::numeric), 2) AS supply_ratio_percent
   FROM ((((public.tb_block_info i
     JOIN public.tb_block_status s ON (((i.sitename)::text = (s.sitename)::text)))
     LEFT JOIN block_flow_summary f ON (((i.sitename)::text = (f.sitename)::text)))
     LEFT JOIN today_usage t ON (((i.sitename)::text = (t.sitename)::text)))
     LEFT JOIN reservoir_outflow_total rot ON (true));


--
-- TOC entry 372 (class 1259 OID 36365)
-- Name: v_booster_station_info_status; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_booster_station_info_status AS
 SELECT i.sitename,
    i.general_overview,
    s.meta,
    s.linked_reservoir_name,
    i.infiltration_well_count,
    i.zone_1_area,
    i.zone_1_height,
    i.zone_2_area,
    i.zone_2_height,
    i.infiltration_well_unit,
    i.infiltration_well_level_unit,
    i.pump_type,
    i.normal_operation_pump_count,
    s.pump_control_mode,
    s.running_pump_count,
    s.pump_start_threshold,
    s.pump_stop_threshold,
    s.pump_start_pressure,
    s.booster_inlet_pressure,
    s.booster_outlet_pressure,
    s.booster_avg_pressure,
    s.level_unit,
    s.pressure_unit,
    i.system_diagram_url,
    i.site_photo_url,
    i.manual_url
   FROM (public.tb_service_booster_station_info i
     JOIN public.tb_service_booster_station_status s ON (((i.sitename)::text = (s.sitename)::text)));


--
-- TOC entry 368 (class 1259 OID 36031)
-- Name: v_equipment_upstream_status; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_equipment_upstream_status AS
 WITH RECURSIVE upstream AS (
         SELECT e.equipment_id,
            e.sitename,
            e.facilitytype,
            e.equipmenttype,
            ARRAY[(e.equipment_id)::character varying] AS visited,
            0 AS depth
           FROM public.tb_equipment_info e
        UNION ALL
         SELECT src.equipment_id,
            src.sitename,
            src.facilitytype,
            src.equipmenttype,
            (u_1.visited || (src.equipment_id)::character varying) AS "varchar",
            (u_1.depth + 1)
           FROM ((upstream u_1
             JOIN public.tb_network_link nl ON (((nl.target_equipment_id)::text = (u_1.equipment_id)::text)))
             JOIN public.tb_equipment_info src ON (((src.equipment_id)::text = (nl.source_equipment_id)::text)))
          WHERE ((u_1.depth < 10) AND (NOT ((src.equipment_id)::text = ANY ((u_1.visited)::text[]))))
        ), latest_status AS (
         SELECT DISTINCT ON (tb_network_status.equipment_id) tb_network_status.equipment_id,
            tb_network_status.is_alive,
            tb_network_status.rtt_ms,
            tb_network_status.status_code,
            tb_network_status.error_message,
            tb_network_status.check_time
           FROM public.tb_network_status
          ORDER BY tb_network_status.equipment_id, tb_network_status.check_time DESC
        )
 SELECT u.depth,
    u.sitename,
    u.facilitytype,
    u.equipmenttype,
    s.rtt_ms,
    s.status_code,
    s.error_message,
    to_char(s.check_time, 'YYYY-MM-DD HH24:MI:SS'::text) AS check_time
   FROM (upstream u
     LEFT JOIN latest_status s ON (((s.equipment_id)::text = (u.equipment_id)::text)))
  WHERE (EXISTS ( SELECT 1
           FROM public.tb_equipment_info e
          WHERE (((e.sitename)::text = (u.sitename)::text) AND (e.facilitytype = u.facilitytype))))
  ORDER BY u.depth;


--
-- TOC entry 366 (class 1259 OID 35983)
-- Name: v_network_path_trace_stop_local_path; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_network_path_trace_stop_local_path AS
 WITH RECURSIVE path_tree AS (
         SELECT nl.source_equipment_id,
            nl.target_equipment_id,
            ARRAY[(nl.source_equipment_id)::character varying, (nl.target_equipment_id)::character varying] AS path_arr,
            1 AS depth
           FROM public.tb_network_link nl
        UNION ALL
         SELECT pt.source_equipment_id,
            nl.target_equipment_id,
            (pt.path_arr || (nl.target_equipment_id)::character varying) AS path_arr,
            (pt.depth + 1)
           FROM (public.tb_network_link nl
             JOIN path_tree pt ON (((nl.source_equipment_id)::text = (pt.target_equipment_id)::text)))
          WHERE (pt.depth < 10)
        )
 SELECT array_to_string(path_arr, ' → '::text) AS path_str,
    depth,
    path_arr[array_length(path_arr, 1)] AS last_equipment_id
   FROM path_tree;


--
-- TOC entry 367 (class 1259 OID 35988)
-- Name: v_network_path_trace_stop_local_with_status; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_network_path_trace_stop_local_with_status AS
 WITH RECURSIVE upstream AS (
         SELECT nl.source_equipment_id,
            nl.target_equipment_id,
            ARRAY[(nl.source_equipment_id)::character varying, (nl.target_equipment_id)::character varying] AS path_arr,
            1 AS depth,
            nl.link_protocol,
            nl.link_device_interface
           FROM public.tb_network_link nl
        UNION ALL
         SELECT u.source_equipment_id,
            nl.target_equipment_id,
            (u.path_arr || (nl.target_equipment_id)::character varying) AS path_arr,
            (u.depth + 1),
            nl.link_protocol,
            nl.link_device_interface
           FROM (public.tb_network_link nl
             JOIN upstream u ON (((nl.source_equipment_id)::text = (u.target_equipment_id)::text)))
          WHERE (((nl.target_equipment_id)::text <> ALL ((u.path_arr)::text[])) AND (u.depth < 10))
        ), path_final AS (
         SELECT array_to_string(upstream.path_arr, ' → '::text) AS path_str,
            upstream.path_arr,
            array_length(upstream.path_arr, 1) AS depth
           FROM upstream
        )
 SELECT DISTINCT path_str,
    depth,
    path_arr[array_length(path_arr, 1)] AS last_equipment_id
   FROM path_final pf;


--
-- TOC entry 365 (class 1259 OID 35948)
-- Name: v_network_path_upstream; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_network_path_upstream AS
 WITH RECURSIVE upstream AS (
         SELECT nl.target_equipment_id AS start_equipment_id,
            1 AS depth,
            nl.source_equipment_id,
            src_eq.sitename AS source_sitename,
            src_eq.facilitytype AS source_facilitytype,
            src_eq.equipmenttype AS source_equipmenttype,
            nl.target_equipment_id,
            tgt_eq.sitename AS target_sitename,
            tgt_eq.facilitytype AS target_facilitytype,
            tgt_eq.equipmenttype AS target_equipmenttype,
            nl.link_protocol,
            nl.link_device_interface
           FROM ((public.tb_network_link nl
             JOIN public.tb_equipment_info src_eq ON (((nl.source_equipment_id)::text = (src_eq.equipment_id)::text)))
             JOIN public.tb_equipment_info tgt_eq ON (((nl.target_equipment_id)::text = (tgt_eq.equipment_id)::text)))
        UNION ALL
         SELECT u.start_equipment_id,
            (u.depth + 1) AS depth,
            nl.source_equipment_id,
            src_eq.sitename AS source_sitename,
            src_eq.facilitytype AS source_facilitytype,
            src_eq.equipmenttype AS source_equipmenttype,
            nl.target_equipment_id,
            tgt_eq.sitename AS target_sitename,
            tgt_eq.facilitytype AS target_facilitytype,
            tgt_eq.equipmenttype AS target_equipmenttype,
            nl.link_protocol,
            nl.link_device_interface
           FROM (((public.tb_network_link nl
             JOIN public.tb_equipment_info src_eq ON (((nl.source_equipment_id)::text = (src_eq.equipment_id)::text)))
             JOIN public.tb_equipment_info tgt_eq ON (((nl.target_equipment_id)::text = (tgt_eq.equipment_id)::text)))
             JOIN upstream u ON (((nl.target_equipment_id)::text = (u.source_equipment_id)::text)))
          WHERE (u.depth < 6)
        )
 SELECT DISTINCT start_equipment_id,
    depth,
    source_equipment_id,
    source_sitename,
    source_facilitytype,
    source_equipmenttype,
    target_equipment_id,
    target_sitename,
    target_facilitytype,
    target_equipmenttype,
    link_protocol,
    link_device_interface
   FROM upstream
  ORDER BY depth;


--
-- TOC entry 400 (class 1259 OID 107585)
-- Name: v_ongoing_alarm; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_ongoing_alarm AS
 WITH ongoing AS (
         SELECT row_number() OVER (ORDER BY r.sitename, r.alarm_category, r.alarm_msg) AS no,
            r.alarm_msg,
            r.alarm_value,
            r.alarm_category,
            r.sitename,
            r.facilitytype
           FROM public.tb_equipment_alarm_report r
          WHERE ((r.alarm_end_time IS NULL) AND ((r.alarm_status)::text ~ '진행'::text))
        ), summary AS (
         SELECT o.alarm_category,
            count(*) AS cnt
           FROM ongoing o
          GROUP BY o.alarm_category
        )
 SELECT ( SELECT count(*) AS count
           FROM ongoing) AS total_ongoing_alarms,
    ( SELECT jsonb_agg(jsonb_build_object('category', s.alarm_category, 'count', s.cnt)) AS jsonb_agg
           FROM summary s) AS summary,
    ( SELECT jsonb_agg(jsonb_build_object('no', o.no, 'sitename', o.sitename, 'facilitytype', o.facilitytype, 'alarm_msg', o.alarm_msg, 'alarm_value', o.alarm_value, 'alarm_category', o.alarm_category)) AS jsonb_agg
           FROM ongoing o) AS alarm_list;


--
-- TOC entry 354 (class 1259 OID 35502)
-- Name: v_pressure_reducing_facility_info_status; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_pressure_reducing_facility_info_status AS
 SELECT i.sitename,
    i.general_overview,
    i.pressure_unit AS info_pressure_unit,
    i.pressure_reduction_pattern,
    i.pressure_reduction_criteria,
    i.site_photo_url,
    i.manual_url,
    i.system_diagram_url,
    s.meta,
    s.avg_inlet_pressure,
    s.avg_outlet_pressure,
    s.avg_pressure_reduction,
    s.pressure_unit AS status_pressure_unit,
    s.status,
    s.updated_at
   FROM (public.tb_pressure_reducing_facility_info i
     JOIN public.tb_pressure_reducing_facility_status s ON (((i.sitename)::text = (s.sitename)::text)));


--
-- TOC entry 5118 (class 0 OID 0)
-- Dependencies: 354
-- Name: VIEW v_pressure_reducing_facility_info_status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_pressure_reducing_facility_info_status IS '감압시설 운영현황 조회용 통합 뷰: 정적 정보(JSONB) + 실시간 상태 통합 제공';


--
-- TOC entry 5119 (class 0 OID 0)
-- Dependencies: 354
-- Name: COLUMN v_pressure_reducing_facility_info_status.sitename; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.v_pressure_reducing_facility_info_status.sitename IS '감압시설 명칭';


--
-- TOC entry 5120 (class 0 OID 0)
-- Dependencies: 354
-- Name: COLUMN v_pressure_reducing_facility_info_status.general_overview; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.v_pressure_reducing_facility_info_status.general_overview IS '감압시설 일반현황 JSONB (설치위치, 밸브관경, 제어방식, 제조사 등)';


--
-- TOC entry 5121 (class 0 OID 0)
-- Dependencies: 354
-- Name: COLUMN v_pressure_reducing_facility_info_status.info_pressure_unit; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.v_pressure_reducing_facility_info_status.info_pressure_unit IS '압력 단위 (정적 정보 기준)';


--
-- TOC entry 5122 (class 0 OID 0)
-- Dependencies: 354
-- Name: COLUMN v_pressure_reducing_facility_info_status.pressure_reduction_pattern; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.v_pressure_reducing_facility_info_status.pressure_reduction_pattern IS '감압패턴 (예: 고정감압)';


--
-- TOC entry 5123 (class 0 OID 0)
-- Dependencies: 354
-- Name: COLUMN v_pressure_reducing_facility_info_status.pressure_reduction_criteria; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.v_pressure_reducing_facility_info_status.pressure_reduction_criteria IS '감압기준 (예: 1.0kg/cm2)';


--
-- TOC entry 5124 (class 0 OID 0)
-- Dependencies: 354
-- Name: COLUMN v_pressure_reducing_facility_info_status.site_photo_url; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.v_pressure_reducing_facility_info_status.site_photo_url IS '감압밸브 현장 사진 URL';


--
-- TOC entry 5125 (class 0 OID 0)
-- Dependencies: 354
-- Name: COLUMN v_pressure_reducing_facility_info_status.manual_url; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.v_pressure_reducing_facility_info_status.manual_url IS '감압밸브 초기 대응 매뉴얼 URL';


--
-- TOC entry 5126 (class 0 OID 0)
-- Dependencies: 354
-- Name: COLUMN v_pressure_reducing_facility_info_status.system_diagram_url; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.v_pressure_reducing_facility_info_status.system_diagram_url IS '감압밸브 구성도 URL';


--
-- TOC entry 5127 (class 0 OID 0)
-- Dependencies: 354
-- Name: COLUMN v_pressure_reducing_facility_info_status.meta; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.v_pressure_reducing_facility_info_status.meta IS '시설 모니터링 정보';


--
-- TOC entry 5128 (class 0 OID 0)
-- Dependencies: 354
-- Name: COLUMN v_pressure_reducing_facility_info_status.avg_inlet_pressure; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.v_pressure_reducing_facility_info_status.avg_inlet_pressure IS '평균 전단 압력';


--
-- TOC entry 5129 (class 0 OID 0)
-- Dependencies: 354
-- Name: COLUMN v_pressure_reducing_facility_info_status.avg_outlet_pressure; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.v_pressure_reducing_facility_info_status.avg_outlet_pressure IS '평균 후단 압력';


--
-- TOC entry 5130 (class 0 OID 0)
-- Dependencies: 354
-- Name: COLUMN v_pressure_reducing_facility_info_status.avg_pressure_reduction; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.v_pressure_reducing_facility_info_status.avg_pressure_reduction IS '평균 감압량';


--
-- TOC entry 5131 (class 0 OID 0)
-- Dependencies: 354
-- Name: COLUMN v_pressure_reducing_facility_info_status.status_pressure_unit; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.v_pressure_reducing_facility_info_status.status_pressure_unit IS '압력 단위 (실시간 데이터 기준)';


--
-- TOC entry 5132 (class 0 OID 0)
-- Dependencies: 354
-- Name: COLUMN v_pressure_reducing_facility_info_status.status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.v_pressure_reducing_facility_info_status.status IS '동작상태';


--
-- TOC entry 5133 (class 0 OID 0)
-- Dependencies: 354
-- Name: COLUMN v_pressure_reducing_facility_info_status.updated_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.v_pressure_reducing_facility_info_status.updated_at IS '최종 업데이트 시각';


--
-- TOC entry 410 (class 1259 OID 175479)
-- Name: v_reservoir_info_status; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_reservoir_info_status AS
 WITH daily_flow AS (
         WITH base AS (
                 SELECT ti.sitename,
                    ti.facilitytype,
                    ti.datainfo,
                    ti.tagsn,
                    r.logtime,
                    r.val
                   FROM (public.tb_tag_raw_data r
                     JOIN public.tb_tag_info ti ON (((ti.tagsn)::text = (r.tagsn)::text)))
                  WHERE (((ti.facilitytype)::text = '배수지'::text) AND ((ti.datainfo)::text ~~ '%적산%'::text) AND (r.val IS NOT NULL) AND (r.val <> (0)::numeric) AND (r.logtime >= (CURRENT_DATE - '8 days'::interval)))
                ), deltas AS (
                 SELECT base.sitename,
                    base.facilitytype,
                        CASE
                            WHEN ((base.datainfo)::text ~~ '%유입%'::text) THEN 'inflow'::text
                            WHEN ((base.datainfo)::text ~~ '%유출%'::text) THEN 'outflow'::text
                            ELSE NULL::text
                        END AS flow_type,
                    base.tagsn,
                    base.logtime,
                    (base.val - lag(base.val) OVER (PARTITION BY base.tagsn ORDER BY base.logtime)) AS delta
                   FROM base
                )
         SELECT deltas.sitename,
            deltas.facilitytype,
            deltas.flow_type,
            (date_trunc('day'::text, deltas.logtime))::date AS d,
            sum(
                CASE
                    WHEN (deltas.delta IS NULL) THEN (0)::numeric
                    WHEN (deltas.delta < (0)::numeric) THEN (0)::numeric
                    ELSE deltas.delta
                END) AS daily_flow_m3
           FROM deltas
          GROUP BY deltas.sitename, deltas.facilitytype, deltas.flow_type, (date_trunc('day'::text, deltas.logtime))
        ), flow_summary AS (
         SELECT daily_flow.sitename,
            daily_flow.facilitytype,
            round(avg(daily_flow.daily_flow_m3) FILTER (WHERE (daily_flow.flow_type = 'inflow'::text)), 2) AS avg_inflow_m3d,
            round(avg(daily_flow.daily_flow_m3) FILTER (WHERE (daily_flow.flow_type = 'outflow'::text)), 2) AS avg_outflow_m3d
           FROM daily_flow
          WHERE (daily_flow.d >= (CURRENT_DATE - '7 days'::interval))
          GROUP BY daily_flow.sitename, daily_flow.facilitytype
        ), today_usage AS (
         WITH base AS (
                 SELECT ti.sitename,
                    ti.facilitytype,
                    ti.tagsn,
                    r.logtime,
                    r.val
                   FROM (public.tb_tag_raw_data r
                     JOIN public.tb_tag_info ti ON (((ti.tagsn)::text = (r.tagsn)::text)))
                  WHERE (((ti.facilitytype)::text = '배수지'::text) AND ((ti.datainfo)::text ~~ '%유출%적산%'::text) AND (r.val IS NOT NULL) AND (r.val <> (0)::numeric) AND (r.logtime >= date_trunc('day'::text, now())))
                ), deltas AS (
                 SELECT base.sitename,
                    base.facilitytype,
                    base.tagsn,
                    base.logtime,
                    (base.val - lag(base.val) OVER (PARTITION BY base.tagsn ORDER BY base.logtime)) AS delta
                   FROM base
                )
         SELECT deltas.sitename,
            deltas.facilitytype,
            round((sum(
                CASE
                    WHEN (deltas.delta IS NULL) THEN (0)::numeric
                    WHEN (deltas.delta < (0)::numeric) THEN (0)::numeric
                    ELSE deltas.delta
                END) / NULLIF((EXTRACT(epoch FROM (now() - date_trunc('day'::text, now()))) / (3600)::numeric), (0)::numeric)), 2) AS avg_usage_m3h
           FROM deltas
          GROUP BY deltas.sitename, deltas.facilitytype
        )
 SELECT i.sitename,
    i.general_overview,
    s.meta,
    i.install_year,
    i.service_area,
    i.zone_count,
    i.zone_1_area,
    i.zone_1_height,
    i.zone_2_area,
    i.zone_2_height,
    i.zone_3_area,
    i.zone_3_height,
    i.zone_4_area,
    i.zone_4_height,
    i.zone_5_area,
    i.zone_5_height,
    i.avg_reservoir_level,
    i.water_level_unit,
    i.reservoir_area_unit,
    i.site_photo_url,
    i.manual_url,
    f.avg_inflow_m3d AS avg_inflow,
    'm³/d'::text AS avg_inflow_unit,
    f.avg_outflow_m3d AS avg_outflow,
    'm³/d'::text AS avg_outflow_unit,
    u.avg_usage_m3h AS avg_usage,
    'm³/h'::text AS usage_unit,
    s.current_water_level,
    s.alarm_high_water_level,
    s.alarm_low_water_level,
    s.water_level_unit AS status_water_level_unit,
    s.zone_count AS status_zone_count,
    s.total_supply_time,
    s.zone_1_supply_time,
    s.zone_2_supply_time,
    s.zone_3_supply_time,
    s.zone_4_supply_time,
    s.zone_5_supply_time,
    s.avg_inlet_pressure,
    s.avg_outlet_pressure,
    s.pressure_unit,
    s.last_updated_at,
    i.system_diagram_url
   FROM (((public.tb_service_reservoir_info i
     JOIN public.tb_service_reservoir_status s ON (((i.sitename)::text = (s.sitename)::text)))
     LEFT JOIN flow_summary f ON (((i.sitename)::text = (f.sitename)::text)))
     LEFT JOIN today_usage u ON (((i.sitename)::text = (u.sitename)::text)));


--
-- TOC entry 362 (class 1259 OID 35705)
-- Name: v_reservoir_status_variance_5min; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_reservoir_status_variance_5min AS
 SELECT i.sitename,
    i.facilitytype,
    i.datainfo,
    r.tagsn,
    round(max(r.val), 3) AS max_val,
    round(min(r.val), 3) AS min_val,
    round(avg(r.val), 3) AS avg_val,
    round((((max(r.val) - min(r.val)) / NULLIF(avg(r.val), (0)::numeric)) * (100)::numeric), 2) AS diff_percent,
        CASE
            WHEN (((max(r.val) - min(r.val)) / NULLIF(avg(r.val), (0)::numeric)) >= 0.1) THEN '이상'::text
            ELSE '정상'::text
        END AS variance_status,
    now() AS checked_at
   FROM (public.tb_tag_raw_data r
     JOIN public.tb_tag_info i ON (((r.tagsn)::text = (i.tagsn)::text)))
  WHERE (((i.datainfo)::text ~~ '%수위%'::text) AND ((i.tagtype)::text = 'Analog Input'::text) AND (r.logtime >= (now() - '00:05:00'::interval)))
  GROUP BY i.sitename, i.facilitytype, i.datainfo, r.tagsn;


--
-- TOC entry 5134 (class 0 OID 0)
-- Dependencies: 362
-- Name: VIEW v_reservoir_status_variance_5min; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_reservoir_status_variance_5min IS '배수지 최근 5분간 수위 데이터의 변동폭(분산값)을 계산하여 헌팅 여부를 판단하는 실시간 상태 진단 뷰.
변동률(diff_percent)이 평균의 10% 이상이면 "이상", 그 미만이면 "정상"으로 판단한다.';


--
-- TOC entry 5135 (class 0 OID 0)
-- Dependencies: 362
-- Name: COLUMN v_reservoir_status_variance_5min.sitename; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.v_reservoir_status_variance_5min.sitename IS '배수지 명칭 (tb_tag_info.sitename 기준)';


--
-- TOC entry 5136 (class 0 OID 0)
-- Dependencies: 362
-- Name: COLUMN v_reservoir_status_variance_5min.facilitytype; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.v_reservoir_status_variance_5min.facilitytype IS '시설 유형 (배수지, 가압장 등)';


--
-- TOC entry 5137 (class 0 OID 0)
-- Dependencies: 362
-- Name: COLUMN v_reservoir_status_variance_5min.datainfo; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.v_reservoir_status_variance_5min.datainfo IS '태그 설명 정보 (예: 수위, 유출유량 등)';


--
-- TOC entry 5138 (class 0 OID 0)
-- Dependencies: 362
-- Name: COLUMN v_reservoir_status_variance_5min.tagsn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.v_reservoir_status_variance_5min.tagsn IS '태그 식별자 (Tag Serial Number)';


--
-- TOC entry 5139 (class 0 OID 0)
-- Dependencies: 362
-- Name: COLUMN v_reservoir_status_variance_5min.max_val; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.v_reservoir_status_variance_5min.max_val IS '최근 5분간 측정된 최대 수위값';


--
-- TOC entry 5140 (class 0 OID 0)
-- Dependencies: 362
-- Name: COLUMN v_reservoir_status_variance_5min.min_val; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.v_reservoir_status_variance_5min.min_val IS '최근 5분간 측정된 최소 수위값';


--
-- TOC entry 5141 (class 0 OID 0)
-- Dependencies: 362
-- Name: COLUMN v_reservoir_status_variance_5min.avg_val; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.v_reservoir_status_variance_5min.avg_val IS '최근 5분간 측정된 평균 수위값';


--
-- TOC entry 5142 (class 0 OID 0)
-- Dependencies: 362
-- Name: COLUMN v_reservoir_status_variance_5min.diff_percent; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.v_reservoir_status_variance_5min.diff_percent IS '최고값과 최저값의 차이를 평균값으로 나눈 변동률 (%)';


--
-- TOC entry 5143 (class 0 OID 0)
-- Dependencies: 362
-- Name: COLUMN v_reservoir_status_variance_5min.variance_status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.v_reservoir_status_variance_5min.variance_status IS '변동률(diff_percent)이 10% 이상이면 "이상", 그 미만이면 "정상"으로 표시 (수위계 헌팅 여부 판정)';


--
-- TOC entry 5144 (class 0 OID 0)
-- Dependencies: 362
-- Name: COLUMN v_reservoir_status_variance_5min.checked_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.v_reservoir_status_variance_5min.checked_at IS '진단 수행 시각 (now() 기준)';


--
-- TOC entry 374 (class 1259 OID 36450)
-- Name: v_sensor_lagged; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_sensor_lagged AS
 SELECT r.logtime,
    r.val,
    i.sitename,
    i.facilitytype,
    i.equipmenttype,
    i.datainfo,
    lag(r.val) OVER (PARTITION BY r.tagsn ORDER BY r.logtime) AS prev_val
   FROM (public.tb_tag_raw_data r
     JOIN public.tb_tag_info i ON (((i.tagsn)::text = (r.tagsn)::text)))
  WHERE (((i.tagtype)::text = 'Analog Input'::text) AND ((i.equipmenttype)::text = ANY ((ARRAY['수위계'::character varying, '압력계'::character varying, '유량계'::character varying])::text[])) AND (r.val IS NOT NULL));


--
-- TOC entry 408 (class 1259 OID 173561)
-- Name: v_tag_holding_pattern_1day; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_tag_holding_pattern_1day AS
 WITH filtered AS (
         SELECT cagg.tagsn,
            cagg.bucket,
            cagg.min_val
           FROM public.cagg_5min_raw_stats_ai cagg
          WHERE ((cagg.bucket >= ((now())::timestamp without time zone - '1 day'::interval)) AND (cagg.min_val = cagg.max_val))
        ), ordered AS (
         SELECT f.tagsn,
            f.bucket,
            f.min_val,
            row_number() OVER (PARTITION BY f.tagsn ORDER BY f.bucket) AS rn1,
            row_number() OVER (PARTITION BY f.tagsn, f.min_val ORDER BY f.bucket) AS rn2
           FROM filtered f
        ), groups AS (
         SELECT o.tagsn,
            o.min_val,
            o.bucket,
            (o.rn1 - o.rn2) AS grp
           FROM ordered o
        ), pattern AS (
         SELECT g.tagsn,
            g.min_val AS holding_val,
            count(*) AS same_val_cnt,
            min(g.bucket) AS first_bucket,
            max(g.bucket) AS last_bucket
           FROM groups g
          GROUP BY g.tagsn, g.min_val, g.grp
        )
 SELECT p.tagsn,
    i.sitename,
    i.facilitytype,
    i.equipmenttype,
    i.datainfo,
    i.datadesc,
    i.tagtype,
    p.holding_val,
    p.same_val_cnt,
    p.first_bucket,
    p.last_bucket,
    (p.same_val_cnt * 5) AS holding_minutes
   FROM (pattern p
     JOIN public.tb_tag_info i ON (((i.tagsn)::text = (p.tagsn)::text)));


--
-- TOC entry 4251 (class 2604 OID 24686)
-- Name: tb_file_history upload_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_file_history ALTER COLUMN upload_id SET DEFAULT nextval('public.tb_file_history_upload_id_seq'::regclass);


--
-- TOC entry 4297 (class 2604 OID 37257)
-- Name: tb_task_master task_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_task_master ALTER COLUMN task_id SET DEFAULT nextval('public.tb_task_master_task_id_seq'::regclass);


--
-- TOC entry 4285 (class 2604 OID 27150)
-- Name: tb_trend_catalog trend_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_trend_catalog ALTER COLUMN trend_id SET DEFAULT nextval('public.tb_trend_catalog_trend_id_seq'::regclass);


--
-- TOC entry 4309 (class 2606 OID 24597)
-- Name: tb_access_log tb_access_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_access_log
    ADD CONSTRAINT tb_access_log_pkey PRIMARY KEY (log_id);


--
-- TOC entry 4311 (class 2606 OID 36953)
-- Name: tb_ai_chat_ask_group tb_ai_chat_ask_group_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_ai_chat_ask_group
    ADD CONSTRAINT tb_ai_chat_ask_group_pkey PRIMARY KEY (group_id, region, user_id);


--
-- TOC entry 4439 (class 2606 OID 32658)
-- Name: tb_ai_chat_ask_image tb_ai_chat_ask_image_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_ai_chat_ask_image
    ADD CONSTRAINT tb_ai_chat_ask_image_pkey PRIMARY KEY (region, ask_seq, image_id);


--
-- TOC entry 4463 (class 2606 OID 38148)
-- Name: tb_ai_chat_ask tb_ai_chat_ask_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_ai_chat_ask
    ADD CONSTRAINT tb_ai_chat_ask_pkey PRIMARY KEY (ask_seq, region, user_id);


--
-- TOC entry 4446 (class 2606 OID 36317)
-- Name: tb_ai_chat_bot_image tb_ai_chat_bot_image_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_ai_chat_bot_image
    ADD CONSTRAINT tb_ai_chat_bot_image_pkey PRIMARY KEY (region, ask_seq, image_id);


--
-- TOC entry 4432 (class 2606 OID 33876)
-- Name: tb_ai_chat_bot tb_ai_chat_bot_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_ai_chat_bot
    ADD CONSTRAINT tb_ai_chat_bot_pkey PRIMARY KEY (ask_seq, region, user_id);


--
-- TOC entry 4313 (class 2606 OID 24642)
-- Name: tb_ai_chat_faq tb_ai_chat_faq_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_ai_chat_faq
    ADD CONSTRAINT tb_ai_chat_faq_pkey PRIMARY KEY (region, faq_seq);


--
-- TOC entry 4315 (class 2606 OID 24655)
-- Name: tb_alarm_log tb_alarm_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_alarm_log
    ADD CONSTRAINT tb_alarm_log_pkey PRIMARY KEY (region, alarm_id, alarm_seq);


--
-- TOC entry 4319 (class 2606 OID 24676)
-- Name: tb_auth_menu tb_auth_menu_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_auth_menu
    ADD CONSTRAINT tb_auth_menu_pkey PRIMARY KEY (auth_idn, menu_idn, region);


--
-- TOC entry 4317 (class 2606 OID 24669)
-- Name: tb_auth tb_auth_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_auth
    ADD CONSTRAINT tb_auth_pkey PRIMARY KEY (auth_idn, region);


--
-- TOC entry 4406 (class 2606 OID 26304)
-- Name: tb_block_info tb_block_info_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_block_info
    ADD CONSTRAINT tb_block_info_pkey PRIMARY KEY (sitename);


--
-- TOC entry 4368 (class 2606 OID 25789)
-- Name: tb_block_status tb_block_status_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_block_status
    ADD CONSTRAINT tb_block_status_pkey PRIMARY KEY (sitename);


--
-- TOC entry 4321 (class 2606 OID 24681)
-- Name: tb_comm_code tb_comm_code_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_comm_code
    ADD CONSTRAINT tb_comm_code_pkey PRIMARY KEY (comm_cd, grp_cd, region);


--
-- TOC entry 4455 (class 2606 OID 36910)
-- Name: tb_equipment_alarm_report tb_equipment_alarm_report_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_equipment_alarm_report
    ADD CONSTRAINT tb_equipment_alarm_report_pkey PRIMARY KEY (alarm_start_time, tagsn);


--
-- TOC entry 4377 (class 2606 OID 25831)
-- Name: tb_equipment_info tb_equipment_info_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_equipment_info
    ADD CONSTRAINT tb_equipment_info_pkey PRIMARY KEY (equipment_id);


--
-- TOC entry 4370 (class 2606 OID 25820)
-- Name: tb_equipment_status tb_equipment_status_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_equipment_status
    ADD CONSTRAINT tb_equipment_status_pkey PRIMARY KEY (code);


--
-- TOC entry 4437 (class 2606 OID 32043)
-- Name: tb_facility_flow_map tb_facility_flow_map_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_facility_flow_map
    ADD CONSTRAINT tb_facility_flow_map_pkey PRIMARY KEY (upstream_sitename, upstream_facilitytype, downstream_sitename, downstream_facilitytype);


--
-- TOC entry 4324 (class 2606 OID 24692)
-- Name: tb_file_history tb_file_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_file_history
    ADD CONSTRAINT tb_file_history_pkey PRIMARY KEY (upload_id);


--
-- TOC entry 4326 (class 2606 OID 24697)
-- Name: tb_grp_code tb_grp_code_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_grp_code
    ADD CONSTRAINT tb_grp_code_pkey PRIMARY KEY (grp_cd, region);


--
-- TOC entry 4328 (class 2606 OID 24708)
-- Name: tb_job tb_job_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_job
    ADD CONSTRAINT tb_job_pkey PRIMARY KEY (region, job_idn);


--
-- TOC entry 4332 (class 2606 OID 24722)
-- Name: tb_menu_api tb_menu_api_pk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_menu_api
    ADD CONSTRAINT tb_menu_api_pk PRIMARY KEY (region, menu_idn, api_cd);


--
-- TOC entry 4330 (class 2606 OID 24716)
-- Name: tb_menu tb_menu_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_menu
    ADD CONSTRAINT tb_menu_pkey PRIMARY KEY (menu_idn, region);


--
-- TOC entry 4382 (class 2606 OID 25923)
-- Name: tb_network_info tb_network_info_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_network_info
    ADD CONSTRAINT tb_network_info_pkey PRIMARY KEY (equipment_id);


--
-- TOC entry 4393 (class 2606 OID 25984)
-- Name: tb_network_link tb_network_link_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_network_link
    ADD CONSTRAINT tb_network_link_pkey PRIMARY KEY (source_equipment_id, target_equipment_id);


--
-- TOC entry 4400 (class 2606 OID 26019)
-- Name: tb_network_status tb_network_status_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_network_status
    ADD CONSTRAINT tb_network_status_pkey PRIMARY KEY (check_time, equipment_id);


--
-- TOC entry 4336 (class 2606 OID 24732)
-- Name: tb_node_connection tb_node_connection_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_node_connection
    ADD CONSTRAINT tb_node_connection_pkey PRIMARY KEY (region, id);


--
-- TOC entry 4334 (class 2606 OID 24727)
-- Name: tb_node tb_node_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_node
    ADD CONSTRAINT tb_node_pkey PRIMARY KEY (region, node_id);


--
-- TOC entry 4358 (class 2606 OID 25742)
-- Name: tb_pressure_reducing_facility_info tb_pressure_reducing_facility_info_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_pressure_reducing_facility_info
    ADD CONSTRAINT tb_pressure_reducing_facility_info_pkey PRIMARY KEY (sitename);


--
-- TOC entry 4362 (class 2606 OID 25752)
-- Name: tb_pressure_reducing_facility_status tb_pressure_reducing_facility_status_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_pressure_reducing_facility_status
    ADD CONSTRAINT tb_pressure_reducing_facility_status_pkey PRIMARY KEY (sitename);


--
-- TOC entry 4338 (class 2606 OID 24738)
-- Name: tb_prompt_column tb_prompt_column_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_prompt_column
    ADD CONSTRAINT tb_prompt_column_pkey PRIMARY KEY (column_name, region, template_id);


--
-- TOC entry 4340 (class 2606 OID 24746)
-- Name: tb_prompt_template tb_prompt_template_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_prompt_template
    ADD CONSTRAINT tb_prompt_template_pkey PRIMARY KEY (region, template_id);


--
-- TOC entry 4385 (class 2606 OID 25970)
-- Name: tb_protocol_lookup tb_protocol_lookup_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_protocol_lookup
    ADD CONSTRAINT tb_protocol_lookup_pkey PRIMARY KEY (protocol_code);


--
-- TOC entry 4354 (class 2606 OID 25708)
-- Name: tb_service_booster_station_info tb_service_booster_station_info_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_service_booster_station_info
    ADD CONSTRAINT tb_service_booster_station_info_pkey PRIMARY KEY (sitename);


--
-- TOC entry 4420 (class 2606 OID 27104)
-- Name: tb_service_booster_station_status tb_service_booster_station_status_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_service_booster_station_status
    ADD CONSTRAINT tb_service_booster_station_status_pkey PRIMARY KEY (sitename);


--
-- TOC entry 4430 (class 2606 OID 30106)
-- Name: tb_service_reservoir_info tb_service_reservoir_info_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_service_reservoir_info
    ADD CONSTRAINT tb_service_reservoir_info_pkey PRIMARY KEY (sitename);


--
-- TOC entry 4349 (class 2606 OID 25647)
-- Name: tb_service_reservoir_status tb_service_reservoir_status_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_service_reservoir_status
    ADD CONSTRAINT tb_service_reservoir_status_pkey PRIMARY KEY (sitename);


--
-- TOC entry 4411 (class 2606 OID 26488)
-- Name: tb_tag_info tb_tag_info_pk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_tag_info
    ADD CONSTRAINT tb_tag_info_pk PRIMARY KEY (tagsn);


--
-- TOC entry 4444 (class 2606 OID 35066)
-- Name: tb_tag_raw_data tb_tag_raw_data_pk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_tag_raw_data
    ADD CONSTRAINT tb_tag_raw_data_pk PRIMARY KEY (logtime, tagsn);


--
-- TOC entry 4460 (class 2606 OID 37263)
-- Name: tb_task_master tb_task_master_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_task_master
    ADD CONSTRAINT tb_task_master_pkey PRIMARY KEY (task_id);


--
-- TOC entry 4423 (class 2606 OID 27156)
-- Name: tb_trend_catalog tb_trend_catalog_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_trend_catalog
    ADD CONSTRAINT tb_trend_catalog_pkey PRIMARY KEY (trend_id);


--
-- TOC entry 4426 (class 2606 OID 27158)
-- Name: tb_trend_catalog tb_trend_catalog_sitename_facilitytype_trend_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_trend_catalog
    ADD CONSTRAINT tb_trend_catalog_sitename_facilitytype_trend_name_key UNIQUE (sitename, facilitytype, trend_name);


--
-- TOC entry 4342 (class 2606 OID 24756)
-- Name: tb_user tb_user_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_user
    ADD CONSTRAINT tb_user_pkey PRIMARY KEY (region, user_id);


--
-- TOC entry 4344 (class 2606 OID 24765)
-- Name: tb_user_session tb_user_session_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_user_session
    ADD CONSTRAINT tb_user_session_pkey PRIMARY KEY (session_id);


--
-- TOC entry 4447 (class 1259 OID 36902)
-- Name: idx_alarm_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_alarm_category ON public.tb_equipment_alarm_report USING btree (alarm_category);


--
-- TOC entry 4448 (class 1259 OID 36903)
-- Name: idx_alarm_equipment; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_alarm_equipment ON public.tb_equipment_alarm_report USING btree (equipment_id);


--
-- TOC entry 4449 (class 1259 OID 36904)
-- Name: idx_alarm_meta_gin; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_alarm_meta_gin ON public.tb_equipment_alarm_report USING gin (meta jsonb_path_ops);


--
-- TOC entry 4450 (class 1259 OID 36912)
-- Name: idx_alarm_ongoing; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_alarm_ongoing ON public.tb_equipment_alarm_report USING btree (alarm_status, alarm_end_time) WHERE ((alarm_end_time IS NULL) AND ((alarm_status)::text = '발생'::text));


--
-- TOC entry 4451 (class 1259 OID 36906)
-- Name: idx_alarm_severity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_alarm_severity ON public.tb_equipment_alarm_report USING btree (alarm_severity);


--
-- TOC entry 4452 (class 1259 OID 36907)
-- Name: idx_alarm_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_alarm_status ON public.tb_equipment_alarm_report USING btree (alarm_status);


--
-- TOC entry 4453 (class 1259 OID 36911)
-- Name: idx_alarm_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_alarm_time ON public.tb_equipment_alarm_report USING btree (alarm_start_time);


--
-- TOC entry 4363 (class 1259 OID 25797)
-- Name: idx_blk_status_avg_flow; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_blk_status_avg_flow ON public.tb_block_status USING btree (block_avg_daily_flow);


--
-- TOC entry 4364 (class 1259 OID 25798)
-- Name: idx_blk_status_inlet_press; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_blk_status_inlet_press ON public.tb_block_status USING btree (block_inlet_pressure);


--
-- TOC entry 4365 (class 1259 OID 25799)
-- Name: idx_blk_status_outlet_press; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_blk_status_outlet_press ON public.tb_block_status USING btree (block_outlet_pressure);


--
-- TOC entry 4401 (class 1259 OID 26306)
-- Name: idx_block_critical_pressure; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_block_critical_pressure ON public.tb_block_info USING btree (critical_pressure);


--
-- TOC entry 4402 (class 1259 OID 26305)
-- Name: idx_block_general_overview_gin; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_block_general_overview_gin ON public.tb_block_info USING gin (general_overview);


--
-- TOC entry 4403 (class 1259 OID 26307)
-- Name: idx_block_pressure_unit; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_block_pressure_unit ON public.tb_block_info USING btree (pressure_unit);


--
-- TOC entry 4413 (class 1259 OID 27110)
-- Name: idx_booster_avg_pressure; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_booster_avg_pressure ON public.tb_service_booster_station_status USING btree (booster_avg_pressure);


--
-- TOC entry 4414 (class 1259 OID 27105)
-- Name: idx_booster_control_mode; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_booster_control_mode ON public.tb_service_booster_station_status USING btree (pump_control_mode);


--
-- TOC entry 4350 (class 1259 OID 25711)
-- Name: idx_booster_general_overview; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_booster_general_overview ON public.tb_service_booster_station_info USING gin (general_overview jsonb_path_ops);


--
-- TOC entry 4415 (class 1259 OID 27106)
-- Name: idx_booster_inlet_pressure; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_booster_inlet_pressure ON public.tb_service_booster_station_status USING btree (booster_inlet_pressure);


--
-- TOC entry 4416 (class 1259 OID 27107)
-- Name: idx_booster_outlet_pressure; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_booster_outlet_pressure ON public.tb_service_booster_station_status USING btree (booster_outlet_pressure);


--
-- TOC entry 4351 (class 1259 OID 25709)
-- Name: idx_booster_pump_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_booster_pump_type ON public.tb_service_booster_station_info USING btree (pump_type);


--
-- TOC entry 4352 (class 1259 OID 25710)
-- Name: idx_booster_site_photo; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_booster_site_photo ON public.tb_service_booster_station_info USING btree (site_photo_url);


--
-- TOC entry 4417 (class 1259 OID 27108)
-- Name: idx_booster_start_pressure; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_booster_start_pressure ON public.tb_service_booster_station_status USING btree (pump_start_pressure);


--
-- TOC entry 4418 (class 1259 OID 27109)
-- Name: idx_booster_start_threshold; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_booster_start_threshold ON public.tb_service_booster_station_status USING btree (pump_start_threshold);


--
-- TOC entry 4394 (class 1259 OID 26026)
-- Name: idx_comm_check_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_comm_check_time ON public.tb_network_status USING btree (check_time);


--
-- TOC entry 4395 (class 1259 OID 26025)
-- Name: idx_comm_equipment_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_comm_equipment_id ON public.tb_network_status USING btree (equipment_id);


--
-- TOC entry 4396 (class 1259 OID 26027)
-- Name: idx_comm_is_alive; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_comm_is_alive ON public.tb_network_status USING btree (is_alive);


--
-- TOC entry 4397 (class 1259 OID 26029)
-- Name: idx_comm_protocol; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_comm_protocol ON public.tb_network_status USING btree (protocol);


--
-- TOC entry 4398 (class 1259 OID 26028)
-- Name: idx_comm_status_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_comm_status_code ON public.tb_network_status USING btree (status_code);


--
-- TOC entry 4371 (class 1259 OID 25851)
-- Name: idx_eqinfo_equipmenttype; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eqinfo_equipmenttype ON public.tb_equipment_info USING btree (equipmenttype);


--
-- TOC entry 4372 (class 1259 OID 25850)
-- Name: idx_eqinfo_facilitytype; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eqinfo_facilitytype ON public.tb_equipment_info USING btree (facilitytype);


--
-- TOC entry 4373 (class 1259 OID 25853)
-- Name: idx_eqinfo_meta_gin; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eqinfo_meta_gin ON public.tb_equipment_info USING gin (meta);


--
-- TOC entry 4374 (class 1259 OID 25849)
-- Name: idx_eqinfo_sitename; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eqinfo_sitename ON public.tb_equipment_info USING btree (sitename);


--
-- TOC entry 4375 (class 1259 OID 25852)
-- Name: idx_eqinfo_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eqinfo_status ON public.tb_equipment_info USING btree (status);


--
-- TOC entry 4433 (class 1259 OID 32045)
-- Name: idx_facility_flow_downstream; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_facility_flow_downstream ON public.tb_facility_flow_map USING btree (downstream_sitename, downstream_facilitytype);


--
-- TOC entry 4434 (class 1259 OID 32046)
-- Name: idx_facility_flow_relation_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_facility_flow_relation_type ON public.tb_facility_flow_map USING btree (relation_type);


--
-- TOC entry 4435 (class 1259 OID 32044)
-- Name: idx_facility_flow_upstream; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_facility_flow_upstream ON public.tb_facility_flow_map USING btree (upstream_sitename, upstream_facilitytype);


--
-- TOC entry 4386 (class 1259 OID 25998)
-- Name: idx_link_interface; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_link_interface ON public.tb_network_link USING btree (link_device_interface);


--
-- TOC entry 4387 (class 1259 OID 26000)
-- Name: idx_link_meta_gin; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_link_meta_gin ON public.tb_network_link USING gin (meta);


--
-- TOC entry 4388 (class 1259 OID 25997)
-- Name: idx_link_protocol; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_link_protocol ON public.tb_network_link USING btree (link_protocol);


--
-- TOC entry 4389 (class 1259 OID 25999)
-- Name: idx_link_role; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_link_role ON public.tb_network_link USING btree (link_role);


--
-- TOC entry 4378 (class 1259 OID 25932)
-- Name: idx_netinfo_ip; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_netinfo_ip ON public.tb_network_info USING btree (ip_address);


--
-- TOC entry 4379 (class 1259 OID 25933)
-- Name: idx_netinfo_meta_gin; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_netinfo_meta_gin ON public.tb_network_info USING gin (meta);


--
-- TOC entry 4355 (class 1259 OID 25743)
-- Name: idx_pr_pattern_criteria; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pr_pattern_criteria ON public.tb_pressure_reducing_facility_info USING btree (pressure_reduction_pattern, pressure_reduction_criteria);


--
-- TOC entry 4356 (class 1259 OID 25744)
-- Name: idx_pr_unit; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pr_unit ON public.tb_pressure_reducing_facility_info USING btree (pressure_unit);


--
-- TOC entry 4359 (class 1259 OID 25765)
-- Name: idx_prf_inlet_pressure; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_prf_inlet_pressure ON public.tb_pressure_reducing_facility_status USING btree (avg_inlet_pressure);


--
-- TOC entry 4360 (class 1259 OID 25766)
-- Name: idx_prf_outlet_pressure; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_prf_outlet_pressure ON public.tb_pressure_reducing_facility_status USING btree (avg_outlet_pressure);


--
-- TOC entry 4427 (class 1259 OID 30107)
-- Name: idx_reservoir_service_area; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reservoir_service_area ON public.tb_service_reservoir_info USING btree (service_area);


--
-- TOC entry 4428 (class 1259 OID 30108)
-- Name: idx_reservoir_zone_count; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reservoir_zone_count ON public.tb_service_reservoir_info USING btree (zone_count);


--
-- TOC entry 4345 (class 1259 OID 25655)
-- Name: idx_srv_status_current_level; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_srv_status_current_level ON public.tb_service_reservoir_status USING btree (current_water_level);


--
-- TOC entry 4346 (class 1259 OID 25653)
-- Name: idx_srv_status_inlet_pressure; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_srv_status_inlet_pressure ON public.tb_service_reservoir_status USING btree (avg_inlet_pressure);


--
-- TOC entry 4347 (class 1259 OID 25654)
-- Name: idx_srv_status_outlet_pressure; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_srv_status_outlet_pressure ON public.tb_service_reservoir_status USING btree (avg_outlet_pressure);


--
-- TOC entry 4440 (class 1259 OID 35742)
-- Name: idx_tag_raw_tagsn_logtime; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tag_raw_tagsn_logtime ON public.tb_tag_raw_data USING btree (tagsn, logtime DESC);


--
-- TOC entry 4457 (class 1259 OID 37266)
-- Name: idx_task_master_site_facility_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_task_master_site_facility_category ON public.tb_task_master USING btree (sitename, facilitytype, task_category);


--
-- TOC entry 4458 (class 1259 OID 37267)
-- Name: idx_task_master_suspend_alarm_types_gin; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_task_master_suspend_alarm_types_gin ON public.tb_task_master USING gin (suspend_alarm_types);


--
-- TOC entry 4380 (class 1259 OID 25931)
-- Name: idx_tb_network_info_equipment; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tb_network_info_equipment ON public.tb_network_info USING btree (equipment_id);


--
-- TOC entry 4390 (class 1259 OID 26001)
-- Name: idx_tb_network_link_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tb_network_link_source ON public.tb_network_link USING btree (source_equipment_id);


--
-- TOC entry 4391 (class 1259 OID 26002)
-- Name: idx_tb_network_link_target; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tb_network_link_target ON public.tb_network_link USING btree (target_equipment_id);


--
-- TOC entry 4383 (class 1259 OID 25971)
-- Name: idx_tb_protocol_lookup_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tb_protocol_lookup_type ON public.tb_protocol_lookup USING btree (protocol_type);


--
-- TOC entry 4407 (class 1259 OID 26489)
-- Name: idx_tb_tag_info_01; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tb_tag_info_01 ON public.tb_tag_info USING btree (tagsn, sitename, facilitytype, datainfo);


--
-- TOC entry 4461 (class 1259 OID 38149)
-- Name: tb_ai_chat_ask_idx_01; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX tb_ai_chat_ask_idx_01 ON public.tb_ai_chat_ask USING btree (ask_req);


--
-- TOC entry 4404 (class 1259 OID 35690)
-- Name: tb_block_info_block_level_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX tb_block_info_block_level_idx ON public.tb_block_info USING btree (block_level);


--
-- TOC entry 4366 (class 1259 OID 35691)
-- Name: tb_block_status_block_level_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX tb_block_status_block_level_idx ON public.tb_block_status USING btree (block_level);


--
-- TOC entry 4322 (class 1259 OID 29561)
-- Name: tb_file_history_idx01; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX tb_file_history_idx01 ON public.tb_file_history USING btree (org_file_name);


--
-- TOC entry 4408 (class 1259 OID 27776)
-- Name: tb_tag_info_alarm_tag_yn_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX tb_tag_info_alarm_tag_yn_idx ON public.tb_tag_info USING btree (alarm_tag_yn);


--
-- TOC entry 4409 (class 1259 OID 26490)
-- Name: tb_tag_info_desc_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX tb_tag_info_desc_idx ON public.tb_tag_info USING btree (sitename, facilitytype);


--
-- TOC entry 4412 (class 1259 OID 26491)
-- Name: tb_tag_info_tagsn_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX tb_tag_info_tagsn_idx ON public.tb_tag_info USING btree (tagsn);


--
-- TOC entry 4441 (class 1259 OID 35067)
-- Name: tb_tag_raw_data_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX tb_tag_raw_data_idx ON public.tb_tag_raw_data USING btree (logtime, tagsn);


--
-- TOC entry 4442 (class 1259 OID 35248)
-- Name: tb_tag_raw_data_logtime_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX tb_tag_raw_data_logtime_idx ON public.tb_tag_raw_data USING btree (logtime DESC);


--
-- TOC entry 4421 (class 1259 OID 27160)
-- Name: tb_trend_catalog_meta_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX tb_trend_catalog_meta_idx ON public.tb_trend_catalog USING gin (meta jsonb_path_ops);


--
-- TOC entry 4424 (class 1259 OID 27159)
-- Name: tb_trend_catalog_sitename_facilitytype_trend_name_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX tb_trend_catalog_sitename_facilitytype_trend_name_idx ON public.tb_trend_catalog USING btree (sitename, facilitytype, trend_name);


--
-- TOC entry 4456 (class 1259 OID 173636)
-- Name: ux_alarm_running; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ux_alarm_running ON public.tb_equipment_alarm_report USING btree (tagsn) WHERE (alarm_end_time IS NULL);


--
-- TOC entry 4480 (class 2620 OID 27161)
-- Name: tb_trend_catalog set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER set_updated_at BEFORE UPDATE ON public.tb_trend_catalog FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();


--
-- TOC entry 4475 (class 2620 OID 25796)
-- Name: tb_block_status trg_block_status_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_block_status_updated_at BEFORE UPDATE ON public.tb_block_status FOR EACH ROW EXECUTE FUNCTION public.fn_set_block_status_updated_at();


--
-- TOC entry 4476 (class 2620 OID 25848)
-- Name: tb_equipment_info trg_equipment_info_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_equipment_info_updated_at BEFORE UPDATE ON public.tb_equipment_info FOR EACH ROW EXECUTE FUNCTION public.fn_set_equipment_info_updated_at();


--
-- TOC entry 4477 (class 2620 OID 25930)
-- Name: tb_network_info trg_network_info_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_network_info_updated_at BEFORE UPDATE ON public.tb_network_info FOR EACH ROW EXECUTE FUNCTION public.fn_set_network_info_updated_at();


--
-- TOC entry 4479 (class 2620 OID 25996)
-- Name: tb_network_link trg_network_link_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_network_link_updated_at BEFORE UPDATE ON public.tb_network_link FOR EACH ROW EXECUTE FUNCTION public.fn_set_network_link_updated_at();


--
-- TOC entry 4474 (class 2620 OID 25764)
-- Name: tb_pressure_reducing_facility_status trg_prf_status_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_prf_status_updated_at BEFORE UPDATE ON public.tb_pressure_reducing_facility_status FOR EACH ROW EXECUTE FUNCTION public.fn_set_prf_status_updated_at();


--
-- TOC entry 4473 (class 2620 OID 25657)
-- Name: tb_service_reservoir_status trg_update_last_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_update_last_updated_at BEFORE INSERT OR UPDATE ON public.tb_service_reservoir_status FOR EACH ROW EXECUTE FUNCTION public.update_last_updated_at();


--
-- TOC entry 4483 (class 2620 OID 37265)
-- Name: tb_task_master trg_update_timestamp; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_update_timestamp BEFORE UPDATE ON public.tb_task_master FOR EACH ROW EXECUTE FUNCTION public.fn_update_timestamp();


--
-- TOC entry 4481 (class 2620 OID 35272)
-- Name: tb_tag_raw_data ts_cagg_invalidation_trigger; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER ts_cagg_invalidation_trigger AFTER INSERT OR DELETE OR UPDATE ON public.tb_tag_raw_data FOR EACH ROW EXECUTE FUNCTION _timescaledb_functions.continuous_agg_invalidation_trigger('6');


--
-- TOC entry 4482 (class 2620 OID 35249)
-- Name: tb_tag_raw_data ts_insert_blocker; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER ts_insert_blocker BEFORE INSERT ON public.tb_tag_raw_data FOR EACH ROW EXECUTE FUNCTION _timescaledb_functions.insert_blocker();


--
-- TOC entry 4478 (class 2620 OID 25973)
-- Name: tb_protocol_lookup update_protocol_lookup_timestamp; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_protocol_lookup_timestamp BEFORE UPDATE ON public.tb_protocol_lookup FOR EACH ROW EXECUTE FUNCTION public.trg_update_protocol_lookup_timestamp();


--
-- TOC entry 4472 (class 2606 OID 36439)
-- Name: tb_service_booster_station_status fk_booster_status_sitename; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_service_booster_station_status
    ADD CONSTRAINT fk_booster_status_sitename FOREIGN KEY (sitename) REFERENCES public.tb_service_booster_station_info(sitename) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- TOC entry 4464 (class 2606 OID 25758)
-- Name: tb_pressure_reducing_facility_status fk_pressure_reducing_facility_status_sitename; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_pressure_reducing_facility_status
    ADD CONSTRAINT fk_pressure_reducing_facility_status_sitename FOREIGN KEY (sitename) REFERENCES public.tb_pressure_reducing_facility_info(sitename) ON DELETE CASCADE;


--
-- TOC entry 4465 (class 2606 OID 25753)
-- Name: tb_pressure_reducing_facility_status fk_prf_status_sitename; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_pressure_reducing_facility_status
    ADD CONSTRAINT fk_prf_status_sitename FOREIGN KEY (sitename) REFERENCES public.tb_pressure_reducing_facility_info(sitename) ON DELETE CASCADE;


--
-- TOC entry 4468 (class 2606 OID 26003)
-- Name: tb_network_link fk_tb_network_link_protocol; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_network_link
    ADD CONSTRAINT fk_tb_network_link_protocol FOREIGN KEY (link_protocol) REFERENCES public.tb_protocol_lookup(protocol_code) ON UPDATE CASCADE ON DELETE RESTRICT;


--
-- TOC entry 5145 (class 0 OID 0)
-- Dependencies: 4468
-- Name: CONSTRAINT fk_tb_network_link_protocol ON tb_network_link; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON CONSTRAINT fk_tb_network_link_protocol ON public.tb_network_link IS '링크 프로토콜 코드를 tb_protocol_lookup 테이블의 protocol_code와 참조 관계로 연결';


--
-- TOC entry 4466 (class 2606 OID 25842)
-- Name: tb_equipment_info tb_equipment_info_status_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_equipment_info
    ADD CONSTRAINT tb_equipment_info_status_fkey FOREIGN KEY (status) REFERENCES public.tb_equipment_status(code);


--
-- TOC entry 4467 (class 2606 OID 25924)
-- Name: tb_network_info tb_network_info_equipment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_network_info
    ADD CONSTRAINT tb_network_info_equipment_id_fkey FOREIGN KEY (equipment_id) REFERENCES public.tb_equipment_info(equipment_id) ON DELETE CASCADE;


--
-- TOC entry 4469 (class 2606 OID 25985)
-- Name: tb_network_link tb_network_link_source_equipment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_network_link
    ADD CONSTRAINT tb_network_link_source_equipment_id_fkey FOREIGN KEY (source_equipment_id) REFERENCES public.tb_equipment_info(equipment_id) ON DELETE CASCADE;


--
-- TOC entry 4470 (class 2606 OID 25990)
-- Name: tb_network_link tb_network_link_target_equipment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_network_link
    ADD CONSTRAINT tb_network_link_target_equipment_id_fkey FOREIGN KEY (target_equipment_id) REFERENCES public.tb_equipment_info(equipment_id) ON DELETE CASCADE;


--
-- TOC entry 4471 (class 2606 OID 26020)
-- Name: tb_network_status tb_network_status_equipment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tb_network_status
    ADD CONSTRAINT tb_network_status_equipment_id_fkey FOREIGN KEY (equipment_id) REFERENCES public.tb_equipment_info(equipment_id);


-- Completed on 2026-02-01 21:56:49

--
-- PostgreSQL database dump complete
--

