"""
UI 블록 빌더 모듈 — response_builder.py에서 분리

SQL 결과를 UI 표시용 블록({"prefix", "text"} 리스트)으로 변환하는 함수들.
/ask 핸들러의 process_sql_result에서 호출된다.
"""

import logging
import re
from typing import Any

logger = logging.getLogger("slm")


# 상태 텍스트 → 시맨틱 마커 레벨 매핑 (순서 중요: 앞쪽이 우선)
_STATUS_MARKER_MAP = [
    ("고장", "error"),
    ("이상", "error"),
    ("경고", "warn"),
    ("주의", "warn"),
    ("정상", "ok"),
    ("양호", "ok"),
    ("가동", "ok"),
    ("정지", "warn"),
]


def wrap_status_marker(text: str) -> str:
    """상태 텍스트를 시맨틱 마커로 감싼다. 예: '정상' → '<<ok:정상>>'"""
    for keyword, level in _STATUS_MARKER_MAP:
        if keyword in text:
            return f"<<{level}:{text}>>"
    return text


# 알람 카테고리 → 심각도 매핑
_ALARM_CATEGORY_SEVERITY = {
    "수위": "error",
    "압력": "error",
    "펌프": "error",
    "네트워크": "error",
    "유량": "warn",
    "밸브": "warn",
    "UPS": "warn",
}



def _alarm_category_marker(category: str) -> str:
    """알람 카테고리를 시맨틱 마커로 감싼다."""
    level = _ALARM_CATEGORY_SEVERITY.get(category, "warn")
    return f"<<{level}:{category}>>"



def _alarm_msg_marker(msg: str) -> str:
    """알람 메시지에서 키워드를 감지하여 시맨틱 마커를 적용한다."""
    error_kw = ("상한", "초과", "고장", "통신장애", "미수신")
    warn_kw = ("하한", "미달", "저하", "약간")
    ok_kw = ("정상", "복구", "해제")
    for kw in error_kw:
        if kw in msg:
            return f"<<error:{msg}>>"
    for kw in warn_kw:
        if kw in msg:
            return f"<<warn:{msg}>>"
    for kw in ok_kw:
        if kw in msg:
            return f"<<ok:{msg}>>"
    return f"<<warn:{msg}>>"


# =============================================================================
# 데이터 후처리 함수
# =============================================================================



def build_hunting_result_block(rows: list, columns: list) -> list:
    """
    헌팅 점검 결과를 시맨틱 마커 리스트로 조립한다.
    듀얼 알고리즘: A(3h 방향전환) + B(5m 분산 뷰) 비교.
    """
    items = []
    for row in rows:
        rd = dict(zip(columns, row))
        site = rd.get("sitename", "")
        reversals = rd.get("reversal_count", 0)
        level_range = rd.get("level_range_3h", 0)
        status = rd.get("hunting_status", "STABLE")

        # 알고리즘 A 마커
        if status == "CONFIRMED_HUNTING":
            marker_a = "<<error:헌팅 확인>>"
        elif status == "SUSPECTED":
            marker_a = "<<warn:헌팅 의심>>"
        else:
            marker_a = "<<ok:정상>>"

        # 알고리즘 B (5분 분산)
        diff_pct = rd.get("diff_percent_5m", 0)
        var_status = rd.get("variance_status_5m", "-")
        max_5m = rd.get("max_val_5m", 0)
        min_5m = rd.get("min_val_5m", 0)

        if var_status == "이상":
            marker_b = f"<<warn:이상>> (변동률 {diff_pct}%)"
        elif var_status == "정상":
            marker_b = f"<<ok:정상>> (변동률 {diff_pct}%)"
        else:
            marker_b = "데이터 없음"

        items.append({
            "prefix": "###",
            "text": f"{site}",
        })
        items.append({
            "prefix": "-",
            "text": f"**[A] 방향전환 분석 (3시간)**: {marker_a} — 반전 {reversals}회, 진동폭 {level_range}m",
        })
        items.append({
            "prefix": "-",
            "text": f"**[B] 5분 분산 분석**: {marker_b} — 최대 {max_5m}m, 최소 {min_5m}m",
        })
    return items



def build_level_detail_block(rows: list, columns: list) -> list:
    """
    fn_reservoir_level_summary() 다중 행을 개별 수위 항목 리스트로 조립한다.
    반환 컬럼: log_time, out_sitename, out_facilitytype, out_datainfo,
              avg_latest, latest_val, avg_month, avg_year, unit
    """
    items = []
    for row in rows:
        row_dict = dict(zip(columns, row))
        datainfo = row_dict.get("out_datainfo", "")
        latest_val = row_dict.get("latest_val")
        avg_month = row_dict.get("avg_month")
        unit = row_dict.get("unit") or ""
        if latest_val is None:
            continue
        # 월평균 대비 편차 기반 마커
        marker_text = f"{latest_val}{unit}"
        try:
            lv = float(latest_val)
            am = float(avg_month) if avg_month is not None else None
            if am and am > 0:
                dev_pct = abs(lv - am) / am * 100
                if dev_pct >= 30:
                    marker_text = f"<<error:{latest_val}{unit}>> (월평균 대비 {dev_pct:+.0f}%)"
                elif dev_pct >= 15:
                    marker_text = f"<<warn:{latest_val}{unit}>> (월평균 대비 {dev_pct:+.0f}%)"
                else:
                    marker_text = f"<<ok:{latest_val}{unit}>>"
        except (ValueError, TypeError):
            pass
        items.append({"prefix": "-", "text": f"{datainfo}: {marker_text}"})
    return items



def build_today_flow_detail_block(rows: list, columns: list) -> list:
    """
    fn_today_outflow() 다중 행을 금일 적산 항목 리스트로 조립한다.
    반환 컬럼: sitename, facilitytype, datainfo, unit, today_outflow
    """
    items = []
    for row in rows:
        row_dict = dict(zip(columns, row))
        datainfo = row_dict.get("datainfo", "")
        today_outflow = row_dict.get("today_outflow")
        unit = row_dict.get("unit") or ""
        if today_outflow is None:
            continue
        try:
            val = float(today_outflow)
            if val == 0:
                val_text = f"<<warn:0{unit}>>"
            else:
                val_text = f"<<ok:{today_outflow}{unit}>>"
        except (ValueError, TypeError):
            val_text = f"{today_outflow}{unit}"
        items.append({"prefix": "-", "text": f"{datainfo}: {val_text}"})
    return items



def build_outflow_detail_block(rows: list, columns: list) -> list:
    """
    fn_today_outflow_all() 다중 행을 전체 배수지 유출 현황 항목 리스트로 조립한다.
    반환 컬럼: result_sitename, result_facilitytype, result_today_outflow, result_is_total
    """
    items = []
    for row in rows:
        row_dict = dict(zip(columns, row))
        sitename = row_dict.get("result_sitename", "")
        facilitytype = row_dict.get("result_facilitytype", "")
        outflow = row_dict.get("result_today_outflow")
        is_total = row_dict.get("result_is_total", 0)
        if is_total == 1:
            continue  # 합계 행은 별도 처리 (total_outflow)
        if outflow is None:
            continue
        try:
            val = float(outflow)
            if val == 0:
                val_text = f"<<error:0㎥>>"
            else:
                val_text = f"<<ok:{outflow}㎥>>"
        except (ValueError, TypeError):
            val_text = f"{outflow}㎥"
        items.append({"prefix": "-", "text": f"{sitename} {facilitytype}: {val_text}"})
    return items



def build_alarm_list_block(rows: list, columns: list) -> list:
    """
    FACILITY_RECENT_ALARM 다중 행을 알람 목록 리스트로 조립한다.
    반환 컬럼: alarm_start_time, alarm_msg
    """
    items = []
    for row in rows:
        row_dict = dict(zip(columns, row))
        alarm_time = row_dict.get("alarm_start_time", "")
        alarm_msg = row_dict.get("alarm_msg", "")
        if alarm_time and alarm_msg:
            marked_msg = _alarm_msg_marker(alarm_msg)
            items.append({
                "prefix": "-",
                "text": f"{alarm_time} — {marked_msg}"
            })
    return items



def _format_latest_value(datadesc: str, val: Any) -> str:
    """최신값을 datadesc 기반으로 시맨틱 마커 포맷팅한다."""
    _DIGITAL_KEYWORDS = ("밸브", "펌프", "FAULT", "CLOSE", "OPEN", "자동")
    is_digital = any(kw in datadesc for kw in _DIGITAL_KEYWORDS)
    if is_digital:
        try:
            v = float(val)
            if v >= 1:
                return "<<ok:가동>>"
            return "<<warn:정지>>"
        except (ValueError, TypeError):
            return wrap_status_marker(str(val))
    return str(val)



def build_latest_value_list_block(rows: list, columns: list) -> list:
    """
    FACILITY_TAG_LATEST_VALUE 다중 행을 최신값 리스트로 조립한다.
    반환 컬럼: datadesc, latest_val, latest_time
    """
    items = []
    for row in rows:
        row_dict = dict(zip(columns, row))
        datadesc = row_dict.get("datadesc", "")
        latest_val = row_dict.get("latest_val", "")
        latest_time = row_dict.get("latest_time", "")
        if datadesc and latest_val is not None:
            formatted_val = _format_latest_value(datadesc, latest_val)
            items.append({
                "prefix": "-",
                "text": f"{datadesc}: {formatted_val} ({latest_time})"
            })
    return items



def build_alarm_rank_block(rows: list, columns: list) -> list:
    """
    FACILITY_ALARM_TOP_COUNT 다중 행을 알람 누적건수 순위 리스트로 조립한다.
    반환 컬럼: alarm_msg, alarm_count
    건수 기반 심각도: 상위 3건 error, 4~6위 warn, 나머지 ok
    """
    items = []
    for idx, row in enumerate(rows):
        row_dict = dict(zip(columns, row))
        alarm_msg = row_dict.get("alarm_msg", "")
        alarm_count = row_dict.get("alarm_count", 0)
        if alarm_msg:
            try:
                cnt = int(alarm_count)
            except (ValueError, TypeError):
                cnt = 0
            if idx < 3:
                level = "error"
            elif idx < 6:
                level = "warn"
            else:
                level = "ok"
            count_marker = f"<<{level}:{alarm_count}건>>"
            text = f"{alarm_msg} {count_marker}"
            if idx == len(rows) - 1:
                text += " 순으로 발생하였습니다."
            items.append({"prefix": f"{idx + 1}.", "text": text})
    return items


# -----------------------------------------------------------------------------
# 경보 카테고리 필터 매핑
# (질문 키워드, DB alarm_category 값, alarm_msg ILIKE 키워드, 표시 라벨)
# -----------------------------------------------------------------------------
_ALARM_FILTER_RULES: list[tuple[list[str], list[str], list[str], str]] = [
    # (질문 키워드,  alarm_category 정확매칭,  alarm_msg ILIKE 폴백,  표시라벨)
    # alarm_category가 DB에 존재하는 항목 → category만 사용 (msg 폴백 없음)
    (["수위"],              ["수위"],       [],                 "수위"),
    (["압력"],              ["압력"],       [],                 "압력"),
    (["전원", "ups"],       ["UPS"],        [],                 "전원"),
    (["펌프"],              ["펌프"],       [],                 "펌프"),
    (["밸브"],              ["밸브"],       [],                 "밸브"),
    (["유량"],              ["유량"],       [],                 "유량"),
    # 통신: category='네트워크' + msg ILIKE '%통신%' 병용
    (["통신", "네트워크"],  ["네트워크"],   ["통신"],           "통신"),
    # 수질: alarm_category 없음 → msg ILIKE만
    (["수질", "탁도"],      [],             ["수질", "탁도"],   "수질"),
]





def build_alarm_cause_rank_block(rows: list, columns: list) -> list:
    """
    FACILITY_ALARM_CAUSE_DIAGNOSIS_RANK 다중 행을 발생원인 순위 리스트로 조립한다.
    반환 컬럼: alarm_msg, alarm_count, diagnosed_causes
    """
    items = []
    for idx, row in enumerate(rows):
        row_dict = dict(zip(columns, row))
        alarm_msg = row_dict.get("alarm_msg", "")
        alarm_count = row_dict.get("alarm_count", 0)
        diagnosed_causes = row_dict.get("diagnosed_causes", "")
        if alarm_msg:
            try:
                cnt = int(alarm_count)
            except (ValueError, TypeError):
                cnt = 0
            if idx < 3:
                level = "error"
            elif idx < 6:
                level = "warn"
            else:
                level = "ok"
            count_marker = f"<<{level}:{alarm_count}건>>"
            text = f"{alarm_msg} {count_marker}"
            if diagnosed_causes:
                text += f" (진단: {diagnosed_causes})"
            items.append({"prefix": f"{idx + 1}.", "text": text})
    return items



def build_pressure_detail_block(rows: list, columns: list) -> list:
    """
    FACILITY_PRESSURE_STATUS 다중 행을 압력 항목 리스트로 조립한다.
    반환 컬럼: datainfo, pressure_val, log_time, month_avg, year_avg, unit
    - 설정압력(Analog Output 개념) 제외
    - 동일 datainfo 중복 제거
    - 월평균 대비 편차 시맨틱 마커 (±30% 이상 error, ±15% warn)
    """
    items = []
    seen = set()
    for row in rows:
        row_dict = dict(zip(columns, row))
        datainfo = row_dict.get("datainfo", "").strip()
        if "설정" in datainfo:
            continue
        val = row_dict.get("pressure_val")
        log_time = row_dict.get("log_time", "")
        month_avg = row_dict.get("month_avg")
        unit = row_dict.get("unit") or ""
        dedup_key = (datainfo, val, log_time)
        if val is not None and dedup_key not in seen:
            seen.add(dedup_key)
            # 월평균 대비 편차 마커
            status_tag = ""
            try:
                v = float(val)
                m = float(month_avg) if month_avg else None
                if m and abs(m) > 0.001:
                    dev_pct = abs(v - m) / abs(m) * 100
                    if dev_pct >= 30:
                        status_tag = f" <<error:편차 {dev_pct:.0f}%>>"
                    elif dev_pct >= 15:
                        status_tag = f" <<warn:편차 {dev_pct:.0f}%>>"
                    else:
                        status_tag = f" <<ok:정상 범위>>"
            except (ValueError, TypeError):
                pass
            items.append({"prefix": "•", "text": f"{log_time} 기준 {datainfo}: {val}{unit}{status_tag}"})
    return items



def build_pressure_reference_block(rows: list, columns: list) -> list:
    """
    FACILITY_PRESSURE_STATUS 다중 행을 평균 압력 참고 자료로 조립한다.
    반환 컬럼: datainfo, month_avg, year_avg, unit
    - 설정압력(Analog Output 개념) 제외
    - 동일 datainfo 중복 제거
    """
    items = []
    seen = set()
    for row in rows:
        row_dict = dict(zip(columns, row))
        datainfo = row_dict.get("datainfo", "").strip()
        if "설정" in datainfo:
            continue
        month_avg = row_dict.get("month_avg")
        year_avg = row_dict.get("year_avg")
        unit = row_dict.get("unit") or ""
        dedup_key = (datainfo, month_avg, year_avg)
        if dedup_key not in seen:
            seen.add(dedup_key)
            if month_avg is not None:
                items.append({"prefix": "-", "text": f"{datainfo} 금월 평균: {month_avg}{unit}"})
            if year_avg is not None:
                items.append({"prefix": "-", "text": f"{datainfo} 금년 평균: {year_avg}{unit}"})
    return items



def build_abnormal_summary_detail_block(rows: list, columns: list) -> list:
    """
    FACILITY_ABNORMAL_STATUS_SUMMARY 다중 행을 시설유형별 이상 현황으로 조립한다.
    반환 컬럼: facilitytype, cnt, missing_cnt, missing_sites
    결측 시설 유무에 따라 시맨틱 마커 적용.
    """
    items = []
    for row in rows:
        rd = dict(zip(columns, row))
        ftype = rd.get("facilitytype", "")
        cnt = rd.get("cnt", 0)
        missing_cnt = rd.get("missing_cnt", 0)
        missing_sites = rd.get("missing_sites", "")
        try:
            m = int(missing_cnt)
        except (ValueError, TypeError):
            m = 0
        if m > 0:
            level = "error" if m >= 3 else "warn"
            marker = f"<<{level}:이상 {m}건>>"
            site_info = f" ({missing_sites})" if missing_sites else ""
            items.append({
                "prefix": "•",
                "text": f"{ftype}: 전체 {cnt}개 중 {marker}{site_info}"
            })
        else:
            items.append({
                "prefix": "•",
                "text": f"{ftype}: 전체 {cnt}개 <<ok:모두 정상>>"
            })
    return items



def build_network_hop_detail_block(rows: list, columns: list) -> list:
    """
    fn_network_path_hop_detail() 다중 행을 source_sitename 기준 그룹핑하여 조립한다.
    반환 컬럼: source_sitename, source_facilitytype, source_equipmenttype,
              target_equipmenttype, link_device_interface, link_protocol
    반환: [{"prefix": "1.", "text": "당진시청의 연결 구간별 ..."}, {"prefix": "-", "text": "..."}, ...]
    """
    from collections import OrderedDict
    groups = OrderedDict()
    for row in rows:
        row_dict = dict(zip(columns, row))
        site = row_dict.get("source_sitename", "")
        ftype = row_dict.get("source_facilitytype", "")
        key = f"{site} {ftype}".strip() if site else ftype
        if key not in groups:
            groups[key] = []
        groups[key].append(row_dict)

    items = []
    for idx, (group_name, hops) in enumerate(groups.items(), 1):
        items.append({
            "prefix": f"{idx}.",
            "text": f"{group_name}의 연결 구간별 통신 방식 및 프로토콜 정보"
        })
        for hop in hops:
            src = hop.get("source_equipmenttype", "")
            tgt = hop.get("target_equipmenttype", "")
            interface = hop.get("link_device_interface", "")
            protocol = hop.get("link_protocol", "")
            items.append({
                "prefix": "-",
                "text": f"{src} - {tgt} : {interface} 통신, 프로토콜 : {protocol}"
            })
    return items



def build_network_status_block(rows: list, columns: list) -> list:
    """
    fn_network_path_trace_with_status() 다중 행을 sitename 기준 그룹핑하여 조립한다.
    반환 컬럼: pos, sitename, facilitytype, equipmenttype, status_code, rtt_ms, error_message
    """
    from collections import OrderedDict
    groups = OrderedDict()
    for row in rows:
        row_dict = dict(zip(columns, row))
        site = row_dict.get("sitename", "")
        ftype = row_dict.get("facilitytype", "")
        key = f"{site} {ftype}".strip() if site else ftype
        if key not in groups:
            groups[key] = []
        groups[key].append(row_dict)

    items = []
    items.append({
        "prefix": "•",
        "text": "네트워크 상태는 상위 네트워크 장비부터 현장까지 구간을 확인합니다."
    })
    for group_name, hops in groups.items():
        items.append({"prefix": "•", "text": group_name})
        for hop in hops:
            equip = hop.get("equipmenttype", "")
            status = hop.get("status_code")
            rtt = hop.get("rtt_ms")
            error = hop.get("error_message")
            marked_status = wrap_status_marker(status) if status else "<<warn:상태없음>>"
            if status and rtt is not None:
                rtt_val = int(float(str(rtt))) if rtt else 0
                items.append({
                    "prefix": "-",
                    "text": f"{equip} : {marked_status}, 응답속도 : {rtt_val}ms"
                })
            elif status and error:
                items.append({
                    "prefix": "-",
                    "text": f"{equip} : {marked_status}, {error}"
                })
            elif status:
                items.append({
                    "prefix": "-",
                    "text": f"{equip} : {marked_status}"
                })
            else:
                items.append({
                    "prefix": "-",
                    "text": f"{equip} : <<warn:상태없음>>"
                })
    return items



def build_avg_usage_detail_block(rows: list, columns: list) -> list:
    """
    v_reservoir_info_status 다중 행을 개별 배수지 평균사용량 항목 리스트로 조립한다.
    반환 컬럼: sitename, avg_usage, usage_unit, ..., is_avg_usage_null
    반환: [{"prefix": "-", "text": "신평: 12.34m³/h"}, ...]
    """
    items = []
    for row in rows:
        row_dict = dict(zip(columns, row))
        sitename = row_dict.get("sitename", "")
        avg_usage = row_dict.get("avg_usage")
        usage_unit = row_dict.get("usage_unit", "")
        is_null = row_dict.get("is_avg_usage_null", "N")
        if is_null == "Y" or avg_usage is None:
            continue
        items.append({"prefix": "-", "text": f"{sitename}: {avg_usage}{usage_unit}"})
    return items



def build_upstream_fault_block(rows: list, columns: list) -> list:
    """
    NETWORK_UPSTREAM_FAULT_ANALYSIS: SSLVPN/UTM 계층 기반 통신이상 원인 분석 결과를 조립한다.
    반환 컬럼: sslvpn_id, total_lte, down_lte, sslvpn_alive, down_sites,
               total_utm, down_utm, global_lte_total, global_lte_down
    """
    if not rows:
        return [{"prefix": "-", "text": "네트워크 상태 데이터가 없습니다."}]

    items = []
    first = dict(zip(columns, rows[0]))
    global_lte_total = int(first.get("global_lte_total") or 0)
    global_lte_down = int(first.get("global_lte_down") or 0)
    total_utm = int(first.get("total_utm") or 0)
    down_utm = int(first.get("down_utm") or 0)

    # 망 분리 환경 체크: LTE + UTM 전부 다운 → SNMP 폴링 불가
    if global_lte_total > 0 and global_lte_total == global_lte_down and total_utm > 0 and down_utm == total_utm:
        items.append({"prefix": "•", "text": "<<warn:현재 네트워크 상태 데이터를 신뢰할 수 없습니다>>"})
        items.append({"prefix": "-", "text": f"전체 장비 {global_lte_total + total_utm}대가 모두 응답 없음으로 기록되어 있습니다."})
        items.append({"prefix": "-", "text": "원인: 현장 망과 분리된 환경(개발 서버)에서는 SNMP 폴링이 불가합니다."})
        items.append({"prefix": "-", "text": "조치: 운영 서버 또는 tb_network_status 동기화 후 재조회 바랍니다."})
        return items

    # UTM 상태
    if total_utm > 0:
        if down_utm == 0:
            utm_text = f"<<ok:정상>> ({total_utm}대 모두 응답)"
        elif down_utm == total_utm:
            utm_text = f"<<error:전체 장애>> ({down_utm}/{total_utm}대 다운)"
        else:
            utm_text = f"<<warn:부분 장애>> ({down_utm}/{total_utm}대 다운)"
        items.append({"prefix": "•", "text": f"UTM(인터넷 방화벽): {utm_text}"})

    # SSLVPN별 LTE 하위 장애 분석
    for row in rows:
        rd = dict(zip(columns, row))
        sslvpn_id = rd.get("sslvpn_id", "")
        total_lte = int(rd.get("total_lte") or 0)
        down_lte = int(rd.get("down_lte") or 0)
        sslvpn_alive = rd.get("sslvpn_alive")

        # down_sites: PostgreSQL array_agg 결과가 list 또는 문자열로 올 수 있음
        down_sites = rd.get("down_sites") or []
        if isinstance(down_sites, str):
            import ast
            try:
                down_sites = ast.literal_eval(down_sites)
            except Exception:
                down_sites = []

        if total_lte == 0:
            continue

        down_pct = round(down_lte / total_lte * 100)

        if sslvpn_alive is False:
            sslvpn_status = "<<error:응답없음>>"
        elif sslvpn_alive is True:
            sslvpn_status = "<<ok:정상>>"
        else:
            sslvpn_status = "<<warn:상태없음>>"

        items.append({"prefix": "•", "text": f"{sslvpn_id}: {sslvpn_status}"})
        items.append({"prefix": "-", "text": f"하위 LTE 현황: {down_lte}/{total_lte}대 다운 ({down_pct}%)"})

        # 이상 현장 목록 표시 (최대 15개)
        if down_lte > 0 and down_sites:
            items.append({"prefix": "-", "text": f"이상 현장: {', '.join(down_sites[:15])}"})

        if down_pct >= 80:
            items.append({"prefix": "-", "text": f"<<error:판정: {sslvpn_id} 장애로 인한 집단 통신이상 가능성>>"})
        elif down_pct >= 30:
            items.append({"prefix": "-", "text": f"<<warn:판정: {sslvpn_id} 하위 다수 LTE 다운 — SSLVPN 점검 필요>>"})
        elif down_lte <= 2:
            items.append({"prefix": "-", "text": "<<ok:판정: 개별 현장 통신이상 (상위 장비 무관)>>"})
        else:
            items.append({"prefix": "-", "text": f"<<warn:판정: {down_lte}개 현장 개별 통신이상>>"})

    # 종합 판정
    if total_utm > 0 and down_utm == total_utm:
        items.append({"prefix": "•", "text": "<<error:종합 판정: UTM 전체 장애 → 전 현장 통신이상 원인 가능성>>"})
        items.append({"prefix": "-", "text": "권장 조치: UTM 장비 상태 확인 및 재시작"})
    elif global_lte_total > 0:
        gdown_pct = round(global_lte_down / global_lte_total * 100)
        if gdown_pct < 10:
            items.append({"prefix": "•", "text": f"<<ok:종합 판정: 전체 LTE 다운율 {gdown_pct}% — 상위 장비 정상, 개별 현장 통신이상>>"})
        elif gdown_pct >= 80:
            items.append({"prefix": "•", "text": f"<<error:종합 판정: 전체 LTE {gdown_pct}% 다운 — 상위 장비 장애 가능성>>"})
        else:
            items.append({"prefix": "•", "text": f"<<warn:종합 판정: 전체 LTE 다운율 {gdown_pct}% — 일부 상위 장비 또는 다수 현장 이상>>"})

    return items



def _mark_equipment_status(row: dict) -> dict:
    """설비 행의 status 필드에 시맨틱 마커를 적용한다."""
    _STATUS_KEYS = ("status", "상태", "운영상태", "status_code")
    for key in _STATUS_KEYS:
        if key in row and row[key]:
            row[key] = wrap_status_marker(str(row[key]))
    return row



def build_equipment_table(meta: Any) -> list:
    """
    meta JSONB를 설비현황 테이블 데이터로 변환한다.

    meta 구조 예시:
    - dict 형태: 키가 설비 카테고리, 값이 설비 목록 배열
      {"펌프": [{"datadesc": "...", "status": "..."}, ...], ...}
    - list 형태: 각 항목이 설비 정보 객체
      [{"equipmenttype": "펌프", "datadesc": "...", "status": "..."}, ...]

    반환: [{"category": "...", "name": "...", "key": "value", ...}, ...]
    """
    if meta is None:
        return []

    if isinstance(meta, str):
        meta = json.loads(meta)

    result = []

    if isinstance(meta, dict):
        for category, items in meta.items():
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        row = {"category": category}
                        row.update(item)
                        result.append(_mark_equipment_status(row))
            elif isinstance(items, dict):
                row = {"category": category}
                row.update(items)
                result.append(_mark_equipment_status(row))
    elif isinstance(meta, list):
        for item in meta:
            if isinstance(item, dict):
                result.append(_mark_equipment_status(item))

    return result


def build_level_cause_block(rows: list, columns: list) -> list:
    """배수지 수위 변동 원인 분석 결과를 시맨틱 마커 리스트로 조립한다."""
    items = []
    for row in rows:
        rd = dict(zip(columns, row))
        direction = rd.get("direction", "")
        severity = rd.get("severity", "info")
        current_level = rd.get("current_level", 0)
        hh_set = rd.get("hh_set", 0)
        ll_set = rd.get("ll_set", 0)
        upstream = rd.get("upstream_sitename", "")

        # 현재 수위 + 설정값
        items.append({"prefix": "•", "text": f"현재 수위: {current_level}m (HH: {hh_set}m / LL: {ll_set}m)"})
        if upstream:
            items.append({"prefix": "•", "text": f"상류 시설: {upstream} {rd.get('upstream_facilitytype', '')}"})

        if direction == "LL":
            # 펌프 상태
            pump_status = rd.get("pump_status", "-")
            pump_detail = rd.get("pump_detail", "")
            if pump_status == "정지":
                items.append({"prefix": "-", "text": f"<<error:{pump_detail}>>"})
            elif pump_status != "-":
                items.append({"prefix": "-", "text": f"<<ok:{pump_detail}>>"})

            # 공급시간
            supply_hours = rd.get("supply_time_hours")
            if supply_hours is not None:
                if supply_hours < 2:
                    items.append({"prefix": "-", "text": f"<<warn:공급가능시간 {supply_hours:.1f}시간 (2시간 미만)>>"})
                else:
                    items.append({"prefix": "-", "text": f"공급가능시간: {supply_hours:.1f}시간"})

            # 유입/유출
            if rd.get("outflow_exceeds_inflow"):
                inflow = rd.get("inflow_avg", 0)
                outflow = rd.get("outflow_avg", 0)
                pct = round((outflow - inflow) / inflow * 100, 1) if inflow > 0 else 0
                items.append({"prefix": "-", "text": f"<<warn:유출량 > 유입량 ({pct}% 초과)>> — 유입 {inflow}, 유출 {outflow} m³/h"})
            else:
                items.append({"prefix": "-", "text": "<<ok:유입/유출 균형 정상>>"})

            # 유입밸브
            if rd.get("inlet_valve_closed"):
                items.append({"prefix": "-", "text": "<<error:유입밸브 차단 상태>>"})
            else:
                items.append({"prefix": "-", "text": "<<ok:유입밸브 개방 상태>>"})

        elif direction == "HH":
            # 유출밸브
            if rd.get("outlet_valve_closed"):
                items.append({"prefix": "-", "text": "<<error:유출밸브 차단 상태>>"})
            else:
                items.append({"prefix": "-", "text": "<<ok:유출밸브 개방 상태>>"})

            # 펌프 전수 운전
            if rd.get("all_pumps_running"):
                items.append({"prefix": "-", "text": f"<<warn:{rd.get('pump_detail', '상류 가압펌프 전수 연속 운전')}>>"})
            elif rd.get("pump_detail") and rd.get("pump_status") != "-":
                items.append({"prefix": "-", "text": f"<<ok:{rd.get('pump_detail', '')}>>"})

            # 설정수위 비교
            us_set = rd.get("upstream_level_set", 0)
            if us_set > 0 and hh_set > 0 and us_set > hh_set:
                items.append({"prefix": "-", "text": f"<<warn:상류 설정수위({us_set}m) > 배수지 HH({hh_set}m)>> — 설정값 검토 필요"})

        # 원인 요약
        cause = rd.get("cause_summary", "")
        if cause:
            sev_marker = {"critical": "<<error:긴급>>", "warning": "<<warn:주의>>", "info": "<<ok:정상>>"}.get(severity, "")
            items.append({"prefix": "▶", "text": f"**원인 요약**: {cause} {sev_marker}"})

    return items


