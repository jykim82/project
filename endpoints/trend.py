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
from shared.llm_narrative import (
    extract_numbers as _extract_numbers,
    strip_identifier_strings as _strip_identifier_strings,
    validate_numbers_in_text as _validate_numbers_in_text,
    is_context_enabled as _shared_is_context_enabled,
)
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
# shared/llm_narrative.py 공통 유틸 사용 — 다른 엔드포인트도 이 이름 그대로 import
# =============================================================================


def _validate_summary_numbers(
    summary: str,
    allowed: list[float],
    tolerance: float = 0.02,
) -> tuple[bool, list[float]]:
    """shared.llm_narrative로 이관 — 하위 호환 래퍼 (strip_strings 없음)."""
    return _validate_numbers_in_text(summary, allowed, None, tolerance)


def _is_context_enabled(grp_cd: str, comm_cd: str) -> bool:
    """shared.llm_narrative로 이관 — 하위 호환 래퍼."""
    return _shared_is_context_enabled(_get_db_connection, grp_cd, comm_cd)


# P2.9 — 태그 baseline in-memory 캐시 (lazy TTL)
# tagsn → (ctx_dict, unix_ts)
_BASELINE_CACHE: dict[str, tuple[dict, float]] = {}
_BASELINE_TTL_SEC = 30 * 60  # 30분


def _baseline_cache_get(tagsn: str) -> Optional[dict]:
    entry = _BASELINE_CACHE.get(tagsn)
    if not entry:
        return None
    ctx, ts = entry
    if time.time() - ts > _BASELINE_TTL_SEC:
        # 만료
        return None
    return ctx


def _baseline_cache_put(tagsn: str, ctx: dict) -> None:
    _BASELINE_CACHE[tagsn] = (ctx, time.time())


def _fetch_tag_meta(tagsn: str) -> dict:
    """tagsn → sitename, facilitytype, datainfo 메타 조회 (P2.1 임계값 조회 선행)."""
    if not tagsn or _get_db_connection is None:
        return {}
    conn = None
    try:
        conn = _get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT sitename, facilitytype, datainfo FROM tb_tag_info WHERE tagsn = %s",
                (tagsn,),
            )
            row = cur.fetchone()
            if row:
                return {
                    "sitename": row[0] or "",
                    "facilitytype": row[1] or "",
                    "datainfo": row[2] or "",
                }
    except Exception as e:
        logger.debug(f"태그 메타 조회 실패: {e}")
    finally:
        if conn:
            conn.close()
    return {}


def _fetch_water_level_thresholds(sitename: str, facilitytype: str) -> dict:
    """배수지의 HH/HL 수위 임계값을 조회한다 (수위 태그 요청 시만 의미 있음).

    반환 키 (실패/미설정 시 빈 dict):
      hh_threshold  : 고수위 경보
      ll_threshold  : 저수위 경보
    """
    if _get_db_connection is None:
        return {}
    if facilitytype != "배수지":
        return {}

    conn = None
    ctx: dict = {}
    try:
        conn = _get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT alarm_high_water_level, alarm_low_water_level "
                "FROM tb_service_reservoir_status WHERE sitename = %s",
                (sitename,),
            )
            row = cur.fetchone()
            if row:
                if row[0] is not None:
                    ctx["hh_threshold"] = float(row[0])
                if row[1] is not None:
                    ctx["ll_threshold"] = float(row[1])
    except Exception as e:
        logger.warning(f"임계값 조회 실패 ({sitename}/{facilitytype}): {e}")
    finally:
        if conn:
            conn.close()
    return ctx


def _fetch_trend_context(tagsn: Optional[str]) -> dict:
    """
    태그의 지난 30일 baseline 통계를 조회한다 (cagg_5min_raw_stats_ai 연속 집계 사용).

    P2.9: in-memory 캐시 적용 (TTL 30분). 캐시 히트 시 DB 조회 스킵.

    반환 키 (실패 시 빈 dict):
      baseline_min_30d   : 지난 30일 최솟값
      baseline_avg_30d   : 지난 30일 평균 (중앙값 근사 — (min+max)/2 가중평균)
      baseline_max_30d   : 지난 30일 최댓값
      baseline_sample_cnt: 샘플 수
    """
    if not tagsn or _get_db_connection is None:
        return {}

    # 캐시 히트
    cached = _baseline_cache_get(tagsn)
    if cached is not None:
        return cached

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
    ctx = {k: v for k, v in ctx.items() if v is not None}
    # 캐시에 저장 (빈 dict도 저장 — 자주 조회되는 태그의 반복 실패 방지)
    _baseline_cache_put(tagsn, ctx)
    return ctx


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
        # body에 명시 or tagsn으로 자동 조회 (P2.1 임계값 조회용)
        sitename = body.get("sitename") or ""
        facilitytype = body.get("facilitytype") or ""
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

        # C안: 30일 baseline + HH/HL 임계값 컨텍스트 조회
        _context_mode = "on" if _is_context_enabled("SITE_SETTING", "TREND_EXPLAIN_CONTEXT") else "off"
        _ctx_t0 = time.perf_counter()
        context = _fetch_trend_context(tagsn) if _context_mode == "on" else {}

        # P2.1: sitename/facilitytype 자동 보강 + HH/HL 임계값 병합
        if _context_mode == "on" and tagsn:
            meta = {}
            if not sitename or not facilitytype:
                meta = _fetch_tag_meta(tagsn)
                sitename = sitename or meta.get("sitename", "")
                facilitytype = facilitytype or meta.get("facilitytype", "")
            is_level_tag = (
                "수위" in (tag_name or "")
                or "수위" in meta.get("datainfo", "")
            )
            if sitename and is_level_tag:
                th = _fetch_water_level_thresholds(sitename, facilitytype or "배수지")
                if th:
                    context.update(th)
        _context_fetch_ms = int((time.perf_counter() - _ctx_t0) * 1000)
        _context_used: list[str] = []
        if "baseline_avg_30d" in context:
            _context_used.append("baseline_30d")
        if "hh_threshold" in context or "ll_threshold" in context:
            _context_used.append("thresholds")

        # 할루시네이션 방어: 값 주입 강제 + 출력 검증용 허용 수치 목록
        # (anomaly_count=0도 포함, 프롬프트 상수 30(일)도 허용)
        allowed_numbers = [
            min_val, avg_val, max_val, float(count), float(anomaly_count),
            30.0,  # 프롬프트의 "지난 30일" 상수
        ]
        for key in ("baseline_min_30d", "baseline_avg_30d", "baseline_max_30d"):
            if key in context:
                allowed_numbers.append(float(context[key]))
        # P2.1 임계값도 허용 수치에 포함
        for key in ("hh_threshold", "ll_threshold"):
            if key in context:
                allowed_numbers.append(float(context[key]))

        # C안: 비교 컨텍스트 섹션 (baseline 또는 임계값이 있을 때 추가)
        context_lines = []
        if "baseline_avg_30d" in context:
            context_lines.append(
                f"- 지난 30일 최소: {context['baseline_min_30d']:.3g}"
            )
            context_lines.append(
                f"- 지난 30일 평균: {context['baseline_avg_30d']:.3g}"
            )
            context_lines.append(
                f"- 지난 30일 최대: {context['baseline_max_30d']:.3g}"
            )
        if "hh_threshold" in context:
            context_lines.append(
                f"- HH 임계값(고수위): {context['hh_threshold']:.3g}"
            )
        if "ll_threshold" in context:
            context_lines.append(
                f"- LL 임계값(저수위): {context['ll_threshold']:.3g}"
            )
        context_block = (
            "\n\n## Baseline·임계값 (이 값들도 비교·서술에 사용 가능)\n"
            + "\n".join(context_lines)
        ) if context_lines else ""

        # 엄격 프롬프트 — "제공된 수치 외 숫자 생성 금지" 명시
        prompt = (
            "다음 센서 데이터 구간을 분석하여 수치와 패턴을 2~3문장으로 요약하라.\n\n"
            "## 절대 규칙\n"
            "1. 아래 섹션에 제공된 수치(통계·baseline·임계값)만 사용하라.\n"
            "2. 제공되지 않은 숫자는 절대 언급하지 마라 "
            "(시간·구체 수치·표준편차 등 계산하지 마라).\n"
            "3. 권고 사항, 조치 지시, 원인 추측은 포함하지 마라.\n"
            "4. 외부 지식이나 일반적 센서 기준은 추가하지 마라.\n"
            "5. 단순 서술 + 존댓말: '값이 {min}~{max} 범위에서 평균 {avg}로 유지되었습니다' 형태.\n"
            "6. Baseline이 있으면 이번 구간 평균이 지난 30일 평균 대비 어떤 수준인지 "
            "간단히 비교 서술하라 (예: '30일 평균 대비 높은 편입니다').\n"
            "7. HH/LL 임계값이 있으면 이번 구간 최대값이 HH 대비 어느 수준인지, "
            "최소값이 LL 대비 어느 수준인지 비교 서술하라 "
            "(예: '최대 3.5m는 HH 임계값 4.0m 대비 주의 범위에 진입').\n\n"
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
