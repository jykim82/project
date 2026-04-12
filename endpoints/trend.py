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
from llm_narrative_log import log_narrative

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


def _is_context_enabled(grp_cd: str, comm_cd: str) -> bool:
    """tb_comm_code 기반 컨텍스트 주입 토글. 에러/미설정 시 기본 True."""
    if _get_db_connection is None:
        return True
    try:
        conn = _get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT use_yn FROM tb_comm_code "
                    "WHERE region = 'R01' AND grp_cd = %s AND comm_cd = %s",
                    (grp_cd, comm_cd),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        if not row:
            return True
        return (row[0] or "Y") == "Y"
    except Exception:
        return True


def _fetch_trend_context(tagsn: Optional[str]) -> dict:
    """
    태그의 지난 30일 baseline 통계를 조회한다 (cagg_5min_raw_stats_ai 연속 집계 사용).

    반환 키 (실패 시 빈 dict):
      baseline_min_30d   : 지난 30일 최솟값
      baseline_avg_30d   : 지난 30일 평균 (중앙값 근사 — (min+max)/2 가중평균)
      baseline_max_30d   : 지난 30일 최댓값
      baseline_sample_cnt: 샘플 수
    """
    if not tagsn or _get_db_connection is None:
        return {}

    ctx: dict = {}
    conn = None
    try:
        conn = _get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT "
                "  MIN(min_val) AS min_30d, "
                "  SUM((min_val + max_val) / 2.0 * sample_cnt) / NULLIF(SUM(sample_cnt), 0) AS avg_30d, "
                "  MAX(max_val) AS max_30d, "
                "  SUM(sample_cnt) AS cnt "
                "FROM cagg_5min_raw_stats_ai "
                "WHERE tagsn = %s AND bucket >= NOW() - INTERVAL '30 days'",
                (tagsn,),
            )
            row = cur.fetchone()
            if row and row[3] and row[3] > 0:
                ctx["baseline_min_30d"] = float(row[0]) if row[0] is not None else None
                ctx["baseline_avg_30d"] = float(row[1]) if row[1] is not None else None
                ctx["baseline_max_30d"] = float(row[2]) if row[2] is not None else None
                ctx["baseline_sample_cnt"] = int(row[3])
    except Exception as e:
        logger.warning(f"trend 컨텍스트 조회 실패: {e}")
    finally:
        if conn:
            conn.close()

    # None 값 제거
    return {k: v for k, v in ctx.items() if v is not None}


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
        tagsn = body.get("tagsn") or None  # C안: 선택 제공, baseline 조회에 사용
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

        # C안: 30일 baseline 컨텍스트 조회 (tagsn이 있을 때만)
        _context_mode = "on" if _is_context_enabled("SITE_SETTING", "TREND_EXPLAIN_CONTEXT") else "off"
        _ctx_t0 = time.perf_counter()
        context = _fetch_trend_context(tagsn) if _context_mode == "on" else {}
        _context_fetch_ms = int((time.perf_counter() - _ctx_t0) * 1000)
        _context_used: list[str] = []
        if context:
            _context_used.append("baseline_30d")

        # 할루시네이션 방어: 값 주입 강제 + 출력 검증용 허용 수치 목록
        # (anomaly_count=0도 포함, 프롬프트 상수 30(일)도 허용)
        allowed_numbers = [
            min_val, avg_val, max_val, float(count), float(anomaly_count),
            30.0,  # 프롬프트의 "지난 30일" 상수
        ]
        for key in ("baseline_min_30d", "baseline_avg_30d", "baseline_max_30d"):
            if key in context:
                allowed_numbers.append(float(context[key]))

        # C안: 비교 컨텍스트 섹션 (baseline이 있을 때만 추가)
        context_block = ""
        if context:
            context_block = (
                "\n\n## 30일 Baseline (이 값들도 비교·서술에 사용 가능)\n"
                f"- 지난 30일 최소: {context['baseline_min_30d']:.3g}\n"
                f"- 지난 30일 평균: {context['baseline_avg_30d']:.3g}\n"
                f"- 지난 30일 최대: {context['baseline_max_30d']:.3g}"
            )

        # 엄격 프롬프트 — "제공된 수치 외 숫자 생성 금지" 명시
        prompt = (
            "다음 센서 데이터 구간을 분석하여 수치와 패턴을 2문장으로 요약하라.\n\n"
            "## 절대 규칙\n"
            "1. 아래 '통계'와 '30일 Baseline' 섹션에 제공된 수치만 사용하라.\n"
            "2. 통계·baseline에 없는 숫자는 절대 언급하지 마라 "
            "(시간·구체 수치·표준편차 등 계산하지 마라).\n"
            "3. 권고 사항, 조치 지시, 원인 추측은 포함하지 마라.\n"
            "4. 외부 지식이나 일반적 센서 기준은 추가하지 마라.\n"
            "5. 단순 서술 + 존댓말: '값이 {min}~{max} 범위에서 평균 {avg}로 유지되었습니다' 형태.\n"
            "6. Baseline이 있으면 이번 구간 평균이 지난 30일 평균 대비 어떤 수준인지 "
            "간단히 비교 서술하라 (예: '30일 평균 대비 높은 편입니다').\n\n"
            f"## 태그\n{tag_name}{unit_str}\n\n"
            f"## 기간\n{fmt_ts(from_ts)} ~ {fmt_ts(to_ts)}\n\n"
            "## 통계 (이 값들만 사용)\n"
            f"- 최소: {min_val:.3g}\n"
            f"- 평균: {avg_val:.3g}\n"
            f"- 최대: {max_val:.3g}\n"
            f"- 데이터 건수: {count}\n"
            f"- 이상 구간: {anomaly_count}건"
            f"{context_block}\n\n"
            "요약 (2문장, 위 수치만 사용, 존댓말 '~습니다' 종결):"
        )

        if not _ollama_client:
            # 결정적 폴백 — LLM 불가 시에도 안전한 템플릿 요약 반환
            return {
                "summary": _fallback_summary(
                    tag_name, unit, min_val, avg_val, max_val, count, anomaly_count,
                ),
                "source": "fallback",
                "context_used": _context_used,
                "context_fetch_ms": _context_fetch_ms,
                "context_mode": _context_mode,
                "llm_generate_ms": 0,
                "allowed_numbers_count": len(allowed_numbers),
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
        _llm_ms = int(_elapsed * 1000)
        summary = (summary or "").strip()

        # 할루시네이션 검증 — LLM이 제공되지 않은 숫자를 생성했는지 확인
        ok, violations = _validate_summary_numbers(summary, allowed_numbers)
        if not ok or not summary:
            logger.warning(
                f"트렌드 AI 요약 할루시네이션 감지 → 템플릿 폴백: "
                f"위반 숫자={violations}, 허용={allowed_numbers}"
            )
            log_narrative(
                endpoint="trend/explain",
                params={"tagsn": tagsn, "tag_name": tag_name, "count": count},
                source="fallback",
                context_mode=_context_mode,
                context_used=_context_used,
                context_fetch_ms=_context_fetch_ms,
                llm_generate_ms=_llm_ms,
                llm_rejected=True,
                violations=violations,
                allowed_count=len(allowed_numbers),
            )
            return {
                "summary": _fallback_summary(
                    tag_name, unit, min_val, avg_val, max_val, count, anomaly_count,
                ),
                "source": "fallback",
                "llm_rejected": True,
                "violations": violations,
                "context_used": _context_used,
                "context_fetch_ms": _context_fetch_ms,
                "context_mode": _context_mode,
                "llm_generate_ms": _llm_ms,
                "allowed_numbers_count": len(allowed_numbers),
            }

        logger.info(
            f"트렌드 AI 요약 완료: {tag_name} "
            f"({fmt_ts(from_ts)}~{fmt_ts(to_ts)}) "
            f"⏱ context={_context_fetch_ms}ms, llm={_llm_ms}ms, "
            f"ctx={_context_used}"
        )
        log_narrative(
            endpoint="trend/explain",
            params={"tagsn": tagsn, "tag_name": tag_name, "count": count},
            source="llm",
            context_mode=_context_mode,
            context_used=_context_used,
            context_fetch_ms=_context_fetch_ms,
            llm_generate_ms=_llm_ms,
            llm_rejected=False,
            allowed_count=len(allowed_numbers),
        )
        return {
            "summary": summary,
            "source": "llm",
            "context_used": _context_used,
            "context_fetch_ms": _context_fetch_ms,
            "context_mode": _context_mode,
            "llm_generate_ms": _llm_ms,
            "allowed_numbers_count": len(allowed_numbers),
        }

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
