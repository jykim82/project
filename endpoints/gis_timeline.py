"""
GIS 타임라인 Phase 2 — 가압장 펌프 가동 시계열 스냅샷
(docs/gis-timeline-scrubber-spec.md Phase 2)

유량/수위/압력은 기존 /trend/data 버킷 집계를 재사용하고(대표 태그는
프런트가 GIS 시설 페이로드로 이미 보유), 실시간 API 에 없는 **펌프 RUN
시계열**만 서버에서 집계 제공한다. 태그 선정 규칙은 sql_executor
_check_pump_status 와 동일 (운전|동작|RUN, FAULT/STOP 제외).
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query

from shared.timeseries import get_chunks_for_range, query_chunks_agg, reaggregate

logger = logging.getLogger("slm")

router = APIRouter(prefix="/gis/timeline", tags=["gis-timeline"])

_get_db_connection = None

KST = timezone(timedelta(hours=9))
MAX_HOURS = 24 * 7   # 7일 상한 (사양 Phase 2)


def init(get_db_connection_fn):
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


def _get_conn():
    if _get_db_connection is None:
        raise RuntimeError("gis_timeline not initialized")
    return _get_db_connection()


def _parse_kst(s: str) -> datetime:
    """ISO(Z/offset/naive→UTC 간주) → KST aware. E-044 원칙: 오프셋 절단 금지."""
    d = datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T")[:32])
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(KST)


@router.get("/pump")
def pump_timeline(
    from_ts: str = Query(..., description="ISO 시작"),
    to_ts: str = Query(..., description="ISO 끝"),
    step_mins: int = Query(10, ge=1, le=240),
    region: str = "R01",
):
    """가압장별 펌프 가동 시계열 — 버킷별 (가동 대수 / 전체 대수).

    가동 판정: 버킷 내 RUN 태그 평균 ≥ 0.5. 응답 times 는 KST 나이브
    ("YYYY-MM-DD HH:MM") — /trend/data 와 동일 좌표계.
    """
    t_from = _parse_kst(from_ts)
    t_to = _parse_kst(to_ts)
    if t_to <= t_from:
        raise HTTPException(status_code=400, detail="기간이 올바르지 않습니다")
    if (t_to - t_from) > timedelta(hours=MAX_HOURS):
        raise HTTPException(status_code=400, detail=f"기간 상한 {MAX_HOURS // 24}일")

    from_s = t_from.strftime("%Y-%m-%d %H:%M:%S")
    to_s = t_to.strftime("%Y-%m-%d %H:%M:%S")

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            # 가압장 펌프 RUN 태그 (sitename 별)
            cur.execute(
                """
                SELECT ti.sitename, ti.tagsn FROM tb_tag_info ti
                WHERE ti.facilitytype = '가압장'
                  AND ti.equipmenttype = '가압펌프'
                  AND ti.tagtype = 'Digital Input'
                  AND (ti.datainfo ~* '운전|동작|RUN')
                  AND ti.datainfo !~* 'FAULT|FLT|STOP|정지'
                """,
            )
            site_tags: dict[str, list[str]] = {}
            for sn, tsn in cur.fetchall():
                site_tags.setdefault(sn, []).append(tsn)
            if not site_tags:
                return {"status": "OK", "times": [], "sites": {}}

            all_tags = [t for tags in site_tags.values() for t in tags]
            chunks = get_chunks_for_range(cur, from_s, to_s)
            reagg = {}
            if chunks:
                agg = query_chunks_agg(
                    cur, chunks, all_tags, from_s, to_s, f"{step_mins} minutes")
                reagg = reaggregate(agg)

        # 공통 시간축 구성
        times: list[str] = []
        t = t_from.replace(second=0, microsecond=0)
        t -= timedelta(minutes=t.minute % step_mins)
        while t <= t_to:
            times.append(t.strftime("%Y-%m-%d %H:%M"))
            t += timedelta(minutes=step_mins)
        idx = {ts: i for i, ts in enumerate(times)}

        # tagsn별 버킷 평균 → sitename별 가동 대수
        tag_on: dict[str, list] = {tsn: [None] * len(times) for tsn in all_tags}
        for (tagsn, bucket), (avg_val, _, _, _) in reagg.items():
            ts = bucket.strftime("%Y-%m-%d %H:%M") if hasattr(bucket, "strftime") else str(bucket)[:16]
            i = idx.get(ts)
            if i is not None and avg_val is not None:
                tag_on[tagsn][i] = 1 if avg_val >= 0.5 else 0

        sites = {}
        for sn, tags in site_tags.items():
            on_series: list = []
            for i in range(len(times)):
                vals = [tag_on[tsn][i] for tsn in tags if tag_on[tsn][i] is not None]
                on_series.append(sum(vals) if vals else None)
            sites[sn] = {"total": len(tags), "on": on_series}

        return {"status": "OK", "times": times, "sites": sites}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("gis timeline pump error: %s", e)
        raise HTTPException(status_code=500, detail="펌프 시계열 조회 실패")
    finally:
        conn.close()
