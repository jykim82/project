"""
트렌드 API 엔드포인트 모듈

- POST /trend/explain  — AI 트렌드 요약
- POST /trend/data     — 시계열 데이터 조회 (time_bucket 집계)
- GET  /trend/facility-sparkline — 시설별 24h 스파크라인

ai_server.py에서 분리된 모듈 — init()으로 의존성을 주입받아 사용.
"""

import asyncio
import logging
import os
import re
import time
from collections import OrderedDict
from datetime import datetime as dt_parse
from typing import Optional

import psycopg2
from fastapi import APIRouter, Request
from pydantic import BaseModel

from shared.timeseries import get_chunks_for_range, query_chunks_agg, reaggregate

logger = logging.getLogger("slm")

router = APIRouter()

# ai_server.py에서 주입
_get_db_connection = None
_ollama_client = None

# DB 직접 연결용 (커넥션 풀 바이패스)
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5433")
DB_NAME = os.environ.get("DB_NAME", "slm")
DB_USER = os.environ.get("DB_USER", "slm_dev")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")


def init(get_db_connection_fn, ollama_client=None):
    """ai_server.py에서 DB 커넥션 팩토리 + Ollama 클라이언트를 주입받는다."""
    global _get_db_connection, _ollama_client
    _get_db_connection = get_db_connection_fn
    _ollama_client = ollama_client


# =============================================================================
# AI 요약 할루시네이션 방어 (C. 값 주입 강화)
# =============================================================================

# 요약에서 추출되는 숫자 패턴 (소수·천단위 구분자 포함, 정수 단독은 제외)
_SUMMARY_NUM_RE = re.compile(r"(?<![0-9A-Za-z])(-?\d+(?:,\d{3})*(?:\.\d+)?)")

# 허용 수치 검증에서 무시할 토큰 (문장 수, 퍼센트 맥락, 이상 건수 등 순수 정수)
_NUM_IGNORE_WORDS = {"1", "2"}  # "2문장", "1회" 같은 불가피한 정수


def _extract_numbers(text: str) -> list[float]:
    """요약 텍스트에서 숫자를 추출 (콤마 제거, 부호 유지)"""
    out = []
    for m in _SUMMARY_NUM_RE.finditer(text):
        tok = m.group(1).replace(",", "")
        if tok in _NUM_IGNORE_WORDS:
            continue
        try:
            out.append(float(tok))
        except ValueError:
            continue
    return out


def _validate_summary_numbers(
    summary: str,
    allowed: list[float],
    tolerance: float = 0.02,
) -> tuple[bool, list[float]]:
    """요약문의 모든 숫자가 허용 수치 중 하나와 tolerance 내 일치하는지 검증.

    Args:
        summary: LLM 생성 요약문
        allowed: 허용 수치 (min/avg/max/count/anomaly_count 등)
        tolerance: 상대 오차 (기본 2%, 최소 절대 오차 0.01)

    Returns:
        (유효 여부, 위반 숫자 목록)
    """
    nums = _extract_numbers(summary)
    violations: list[float] = []
    for n in nums:
        ok = any(
            abs(n - a) <= max(abs(a) * tolerance, 0.01) for a in allowed
        )
        if not ok:
            violations.append(n)
    return (len(violations) == 0, violations)


def _fallback_summary(
    tag_name: str,
    unit: str,
    min_val: float,
    avg_val: float,
    max_val: float,
    count: int,
    anomaly_count: int,
) -> str:
    """LLM 검증 실패 또는 비활성 시 결정적 템플릿 요약 (할루시네이션 0)"""
    unit_str = f" {unit}" if unit else ""
    first = (
        f"{tag_name}은(는) 선택 구간에서 최소 {min_val:.3g}{unit_str}, "
        f"평균 {avg_val:.3g}{unit_str}, 최대 {max_val:.3g}{unit_str} 범위로 기록되었습니다."
    )
    if anomaly_count > 0:
        second = f"데이터 {count}건 중 이상 구간이 {anomaly_count}건 포함되어 있습니다."
    else:
        second = f"총 {count}건의 데이터가 수집되었으며 이상 구간은 감지되지 않았습니다."
    return f"{first} {second}"


# =============================================================================
# 요청 모델
# =============================================================================

class TrendDataRequest(BaseModel):
    tag_ids: list
    from_ts: str
    to_ts: str
    max_points: Optional[int] = 2000


# =============================================================================
# POST /trend/explain — AI 트렌드 요약
# =============================================================================

@router.post("/trend/explain")
async def explain_trend(request: Request):
    """트렌드 선택 구간 AI 요약 (gemma4, 2문장, 권고 없음).

    요청 바디:
      tag_name   : 태그 표시명
      unit       : 단위 (예: "m³/h")
      from_ts    : 시작 ISO timestamp
      to_ts      : 종료 ISO timestamp
      min        : 최솟값
      max        : 최댓값
      avg        : 평균값
      count      : 데이터 포인트 수
      anomaly_count : 이상 구간 수 (0이면 없음)

    응답:
      summary    : AI 요약 텍스트 (2문장)
    """
    conn = None
    try:
        # 토글 활성 여부 확인
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT use_yn FROM tb_comm_code "
            "WHERE region = 'R01' AND grp_cd = 'SITE_SETTING' AND comm_cd = 'TREND_EXPLAIN_ENABLED'"
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        conn = None

        if not row or row[0] != "Y":
            return {"error": "트렌드 AI 요약이 비활성화되어 있습니다."}, 403

        body = await request.json()
        tag_name = body.get("tag_name", "태그")
        unit = body.get("unit", "")
        from_ts = body.get("from_ts", "")
        to_ts = body.get("to_ts", "")
        min_val = float(body.get("min", 0))
        max_val = float(body.get("max", 0))
        avg_val = float(body.get("avg", 0))
        count = int(body.get("count", 0))
        anomaly_count = int(body.get("anomaly_count", 0))

        def fmt_ts(ts: str) -> str:
            try:
                d = dt_parse.fromisoformat(ts.replace("Z", "+00:00"))
                return d.strftime("%m-%d %H:%M")
            except Exception:
                return ts

        unit_str = f" ({unit})" if unit else ""

        # 할루시네이션 방어: 값 주입 강제 + 출력 검증용 허용 수치 목록
        # (anomaly_count=0도 포함 — 프롬프트가 "이상 구간 0건"을 언급할 수 있음)
        allowed_numbers = [min_val, avg_val, max_val, float(count), float(anomaly_count)]

        # 엄격 프롬프트 — "제공된 수치 외 숫자 생성 금지" 명시
        prompt = (
            "다음 센서 데이터 구간을 분석하여 수치와 패턴을 2문장으로 요약하라.\n\n"
            "## 절대 규칙\n"
            "1. 아래 '통계' 섹션에 제공된 수치(최소/평균/최대/데이터 건수/이상 구간)만 사용하라.\n"
            "2. 통계에 없는 숫자는 절대 언급하지 마라 (시간·구체 수치·표준편차 등 계산하지 마라).\n"
            "3. 권고 사항, 조치 지시, 원인 추측은 포함하지 마라.\n"
            "4. 외부 지식이나 일반적 센서 기준은 추가하지 마라.\n"
            "5. 단순 서술: '값이 {min}~{max} 범위에서 평균 {avg}로 유지되었다' 형태.\n\n"
            f"## 태그\n{tag_name}{unit_str}\n\n"
            f"## 기간\n{fmt_ts(from_ts)} ~ {fmt_ts(to_ts)}\n\n"
            "## 통계 (이 값들만 사용)\n"
            f"- 최소: {min_val:.3g}\n"
            f"- 평균: {avg_val:.3g}\n"
            f"- 최대: {max_val:.3g}\n"
            f"- 데이터 건수: {count}\n"
            f"- 이상 구간: {anomaly_count}건\n\n"
            "요약 (2문장, 위 수치만 사용):"
        )

        if not _ollama_client:
            # 결정적 폴백 — LLM 불가 시에도 안전한 템플릿 요약 반환
            return {
                "summary": _fallback_summary(
                    tag_name, unit, min_val, avg_val, max_val, count, anomaly_count,
                ),
                "source": "fallback",
            }

        _t0 = time.perf_counter()
        summary = await asyncio.to_thread(
            _ollama_client.generate,
            prompt,
            None,     # model
            None,     # num_ctx (ai_settings 따름)
            None,     # num_predict (gemma4:26b 호환성 — None=모델 기본값)
            90.0,     # timeout — Gemma4:26b tail latency 커버 (p99 ~50s)
            3,        # backoff_seconds — 사용자 클릭 UX에 맞게 짧게 (cascading 회피)
        )
        _elapsed = time.perf_counter() - _t0
        summary = (summary or "").strip()

        # 할루시네이션 검증 — LLM이 제공되지 않은 숫자를 생성했는지 확인
        ok, violations = _validate_summary_numbers(summary, allowed_numbers)
        if not ok or not summary:
            logger.warning(
                f"트렌드 AI 요약 할루시네이션 감지 → 템플릿 폴백: "
                f"위반 숫자={violations}, 허용={allowed_numbers}"
            )
            return {
                "summary": _fallback_summary(
                    tag_name, unit, min_val, avg_val, max_val, count, anomaly_count,
                ),
                "source": "fallback",
                "llm_rejected": True,
                "violations": violations,
            }

        logger.info(
            f"트렌드 AI 요약 완료: {tag_name} "
            f"({fmt_ts(from_ts)}~{fmt_ts(to_ts)}) ⏱ {_elapsed*1000:.0f}ms"
        )
        return {"summary": summary, "source": "llm"}

    except Exception as e:
        logger.error(f"트렌드 AI 요약 실패: {e}")
        return {"error": f"AI 요약 중 오류가 발생했습니다: {str(e)}"}
    finally:
        if conn:
            conn.close()


# =============================================================================
# POST /trend/data — 시계열 데이터 조회 (time_bucket 집계)
# =============================================================================

@router.post("/trend/data")
async def get_trend_data(req: TrendDataRequest):
    """트렌드 시계열 데이터 조회 — time_bucket 집계"""
    if not req.tag_ids or len(req.tag_ids) > 15:
        return {"status": "ERROR", "message": "태그는 1~15개 선택 가능합니다."}

    conn = None
    try:
        # 시간범위 파싱
        from_ts = req.from_ts.replace("T", " ").replace("Z", "")[:19]
        to_ts = req.to_ts.replace("T", " ").replace("Z", "")[:19]

        # 시간범위(분) 계산 → 버킷 크기 결정
        t_from = dt_parse.strptime(from_ts, "%Y-%m-%d %H:%M:%S")
        t_to = dt_parse.strptime(to_ts, "%Y-%m-%d %H:%M:%S")
        total_minutes = max(1, int((t_to - t_from).total_seconds() / 60))
        max_pts = min(max(req.max_points or 2000, 100), 5000)
        bucket_mins = max(1, total_minutes // max_pts)

        # 디지털 태그 목록 조회 (ROUND 처리용)
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
        )
        cur = conn.cursor()
        cur.execute(
            "SELECT tagsn FROM tb_tag_info WHERE tagsn = ANY(%s) "
            "AND tagtype = 'Digital Input'",
            (req.tag_ids,)
        )
        digital_tags = {r[0] for r in cur.fetchall()}

        # 청크 직접 쿼리로 time_bucket 집계 (ChunkAppend 플래너 우회)
        bucket_interval = f"{bucket_mins} minutes"
        chunks = get_chunks_for_range(cur, from_ts, to_ts)

        if chunks:
            agg = query_chunks_agg(
                cur, chunks, req.tag_ids, from_ts, to_ts, bucket_interval)
            reagg = reaggregate(agg)
        else:
            reagg = {}
        cur.close()

        # 후처리: 공통 times + tagsn별 values 배열
        time_set = OrderedDict()
        tag_data: dict[str, dict] = {}
        for (tagsn, bucket), (avg_val, _, _, _) in sorted(
            reagg.items(), key=lambda x: (x[0][1], x[0][0]),
        ):
            ts = bucket.strftime("%Y-%m-%d %H:%M") if hasattr(bucket, "strftime") else str(bucket)[:16]
            time_set[ts] = True
            if tagsn not in tag_data:
                tag_data[tagsn] = {}
            # 디지털 태그 → 0/1 반올림
            if avg_val is not None:
                v = round(avg_val) if tagsn in digital_tags else round(avg_val, 4)
            else:
                v = None
            tag_data[tagsn][ts] = v

        times = list(time_set.keys())
        series = {}
        for tag_id in req.tag_ids:
            td = tag_data.get(tag_id, {})
            series[tag_id] = [td.get(t) for t in times]

        return {
            "status": "OK",
            "data": {"times": times, "series": series},
            "bucket_mins": bucket_mins,
            "total_points": len(times),
        }

    except Exception as e:
        logger.error(f"트렌드 데이터 조회 실패: {e}")
        return {"status": "ERROR", "message": f"조회에 실패했습니다: {str(e)}"}
    finally:
        if conn:
            conn.close()


# =============================================================================
# GET /trend/facility-sparkline — 시설별 24h 스파크라인
# =============================================================================

@router.get("/trend/facility-sparkline")
async def get_facility_sparkline(sitename: str, facilitytype: str, hours: int = 24):
    """시설별 주요 태그 24h 스파크라인 데이터 (GIS 상세 패널용)."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        # 주요 아날로그 태그 최대 6개 (수위/유량순시/적산/압력 우선)
        cur.execute("""
            SELECT tagsn, datainfo FROM tb_tag_info
            WHERE sitename = %s AND facilitytype = %s
              AND tagtype = 'Analog Input'
              AND datainfo !~* 'HH|LL|알람|SET|상태'
            ORDER BY
                CASE WHEN datainfo ~* '수위' THEN 1
                     WHEN datainfo ~* '유량' AND datainfo ~* '순시' THEN 2
                     WHEN datainfo ~* '적산' THEN 3
                     WHEN datainfo ~* '유량' THEN 4
                     WHEN datainfo ~* '압력' THEN 5
                     ELSE 6 END,
                tagsn
            LIMIT 6
        """, (sitename, facilitytype))
        tags = cur.fetchall()
        if not tags:
            cur.close()
            return []

        tagsn_list = [t[0] for t in tags]
        tag_info = {t[0]: t[1] for t in tags}

        # 시계열 조회 (5분 간격 다운샘플링)
        cur.execute("""
            SELECT tagsn,
                   time_bucket('5 minutes', logtime) AS bucket,
                   AVG(val) AS val
            FROM tb_tag_raw_data
            WHERE tagsn = ANY(%s)
              AND logtime >= NOW() - INTERVAL '%s hours'
            GROUP BY tagsn, bucket
            ORDER BY tagsn, bucket
        """, (tagsn_list, hours))
        rows = cur.fetchall()
        cur.close()

        # 태그별 그룹핑
        result = []
        tag_data: dict[str, list] = {tsn: [] for tsn in tagsn_list}
        for tsn, bucket, val in rows:
            if tsn in tag_data and val is not None:
                tag_data[tsn].append({"logtime": str(bucket), "val": round(float(val), 2)})

        for tsn in tagsn_list:
            if tag_data[tsn]:
                result.append({
                    "tagsn": tsn,
                    "datainfo": tag_info.get(tsn, tsn),
                    "data": tag_data[tsn],
                })

        return result

    except Exception as e:
        logger.error(f"스파크라인 조회 실패: {e}")
        return []
    finally:
        if conn:
            conn.close()
