"""
AI 채팅 첫 화면 FAQ 예시 구문 동적 생성

- GET /chat/faq/examples?region=R01&per_category=2

기존 use-chat-faq.ts 훅이 프런트에서 facility_map만 보고 랜덤 치환했는데,
"실제 답변이 있는 지점"이 아닐 수 있음 (stale 데이터, 태그 없음 등).

이 엔드포인트는 DB를 직접 확인해 최근 7일 내 실제 데이터가 있는 시설만
선택해서 완성된 질문 리스트를 반환한다.

카테고리:
  basic     : 현황 조회
  trend     : 트렌드
  anomaly   : 이상 감지
  analysis  : 분석
  facility  : 시설 정보
  alarm     : 경보
"""

import logging
import random
from typing import Optional

from fastapi import APIRouter, Query

logger = logging.getLogger("slm")

router = APIRouter()

_get_db_connection = None
_get_scan_cache = None


def init(get_db_connection_fn, get_scan_cache_fn=None):
    global _get_db_connection, _get_scan_cache
    _get_db_connection = get_db_connection_fn
    _get_scan_cache = get_scan_cache_fn


# 카테고리별 질문 템플릿 ({sitename} 치환)
# 각 항목: (질문 템플릿, 필요한 시설유형, 필요한 datainfo 키워드)
_TEMPLATES: list[tuple[str, str, str, str]] = [
    # (question, category, facilitytype, datainfo_filter)
    # basic
    ("{sitename} 배수지 수위 현황 알려줘", "basic", "배수지", "수위"),
    ("진행중인 알람 보여줘", "basic", "", ""),  # 전체 대상
    ("{sitename} 가압장 운영 정보 알려줘", "basic", "가압장", ""),
    ("{sitename} 배수지 위치도는?", "basic", "배수지", ""),
    # trend
    ("{sitename} 배수지 수위 트렌드 보여줘", "trend", "배수지", "수위"),
    ("{sitename} 배수지 유입유량 트렌드 보여줘", "trend", "배수지", "유량"),
    ("{sitename} 가압장 압력 트렌드 보여줘", "trend", "가압장", "압력"),
    # anomaly
    ("전체 센서 이상 스캔해줘", "anomaly", "", ""),
    ("{sitename} 배수지 이상 진단해줘", "anomaly", "배수지", ""),
    ("{sitename} 가압장 이상 진단해줘", "anomaly", "가압장", ""),
    ("최근 이상 이력 보여줘", "anomaly", "", ""),
    # analysis
    ("소블록 누수 추정 분석해줘", "analysis", "소블록", ""),
    ("{sitename} 배수지 야간최소유량 표준편차분석 해줘", "analysis", "배수지", ""),
    ("{sitename} 배수지 결측 현황 알려줘", "analysis", "배수지", ""),
    ("소블록 야간최소유량 표로 보여줘", "analysis", "소블록", "유량"),
    # facility
    ("{sitename} 배수지 위치도 보여줘", "facility", "배수지", ""),
    ("{sitename} 배수지 일반현황 알려줘", "facility", "배수지", ""),
    ("{sitename} 가압장 일반현황 알려줘", "facility", "가압장", ""),
    # alarm
    ("경보 발생 이력 보여줘", "alarm", "", ""),
    ("{sitename} 배수지 최근 알람 보여줘", "alarm", "배수지", ""),
]

_CATEGORY_LABEL = {
    "basic": "현황 조회",
    "trend": "트렌드",
    "anomaly": "이상 감지",
    "analysis": "분석",
    "facility": "시설 정보",
    "alarm": "경보",
}


def _fetch_active_sitenames(
    facilitytype: str,
    datainfo_filter: str,
    days: int = 7,
) -> list[str]:
    """최근 N일 내 실제 데이터가 있는 시설 목록.

    - facilitytype이 비어있으면 시설유형 필터 없음
    - datainfo_filter가 비어있으면 datainfo 필터 없음
    - 빈 리스트 반환 시 호출자가 기본 sitename 사용
    """
    if _get_db_connection is None:
        return []

    conn = None
    try:
        conn = _get_db_connection()
        with conn.cursor() as cur:
            conditions = ["ti.sitename IS NOT NULL", "ti.sitename <> ''"]
            params: list = []
            if facilitytype:
                conditions.append("ti.facilitytype = %s")
                params.append(facilitytype)
            if datainfo_filter:
                conditions.append("ti.datainfo ILIKE %s")
                params.append(f"%{datainfo_filter}%")
            params.append(days)

            sql = f"""
                SELECT DISTINCT ti.sitename
                FROM tb_tag_info ti
                WHERE {' AND '.join(conditions)}
                  AND EXISTS (
                      SELECT 1 FROM cagg_5min_raw_stats_ai c
                      WHERE c.tagsn = ti.tagsn
                        AND c.bucket >= NOW() - (%s || ' days')::interval
                      LIMIT 1
                  )
                ORDER BY ti.sitename
            """
            cur.execute(sql, params)
            return [r[0] for r in cur.fetchall() if r[0]]
    except Exception as e:
        logger.warning(f"active sitenames 조회 실패: {e}")
        return []
    finally:
        if conn:
            conn.close()


def _anomaly_sitenames() -> list[tuple[str, str]]:
    """이상 스캔 캐시에서 현재 이상/주의 판정이 있는 (sitename, facilitytype) 목록."""
    if _get_scan_cache is None:
        return []
    try:
        cache, _ = _get_scan_cache()
        if not cache:
            return []
        rows = cache.get("rows") or []
        columns = cache.get("columns") or []
        if not rows or not columns:
            return []
        col_idx = {c: i for i, c in enumerate(columns)}
        site_i = col_idx.get("sitename")
        ft_i = col_idx.get("facilitytype")
        verdict_i = col_idx.get("verdict")
        if site_i is None or ft_i is None or verdict_i is None:
            return []
        seen: set[tuple[str, str]] = set()
        out: list[tuple[str, str]] = []
        for r in rows:
            try:
                v = r[verdict_i]
                s = r[site_i]
                ft = r[ft_i]
            except (TypeError, IndexError):
                continue
            if v in ("이상", "주의", "복합이상", "교차이상") and s and ft:
                key = (str(s), str(ft))
                if key not in seen:
                    seen.add(key)
                    out.append(key)
        return out
    except Exception as e:
        logger.debug(f"anomaly_sitenames 추출 실패: {e}")
        return []


@router.get("/chat/faq/examples")
def get_faq_examples(
    region: str = Query("R01"),
    per_category: int = Query(2, ge=1, le=4),
):
    """실제 답변이 가능한 시설로 치환된 FAQ 예시 질문 목록.

    반환:
      {
        "status": "OK",
        "categories": [
          {
            "id": "basic",
            "label": "현황 조회",
            "questions": ["죽동 배수지 수위 현황 알려줘", ...],
          },
          ...
        ]
      }
    """
    # 카테고리별 템플릿 버킷
    by_cat: dict[str, list[tuple[str, str, str, str]]] = {}
    for t in _TEMPLATES:
        by_cat.setdefault(t[1], []).append(t)

    # 이상감지 카테고리는 실제 이상 있는 시설 우선
    anomaly_sites = _anomaly_sitenames()

    result_cats = []
    for cat_id in ("basic", "trend", "anomaly", "analysis", "facility", "alarm"):
        templates = by_cat.get(cat_id, [])
        if not templates:
            continue
        random.shuffle(templates)
        questions: list[str] = []
        used_templates: set[str] = set()

        for q_tpl, _, ft, di in templates:
            if len(questions) >= per_category:
                break
            if q_tpl in used_templates:
                continue

            if "{sitename}" not in q_tpl:
                questions.append(q_tpl)
                used_templates.add(q_tpl)
                continue

            # 이상감지 카테고리: 실제 이상 있는 시설 우선
            if cat_id == "anomaly" and anomaly_sites:
                candidates = [s for s, afty in anomaly_sites if not ft or afty == ft]
                if candidates:
                    pick = random.choice(candidates)
                    questions.append(q_tpl.replace("{sitename}", pick))
                    used_templates.add(q_tpl)
                    continue

            # 일반: 최근 7일 내 데이터 있는 시설
            actives = _fetch_active_sitenames(ft, di)
            if not actives:
                continue
            pick = random.choice(actives)
            questions.append(q_tpl.replace("{sitename}", pick))
            used_templates.add(q_tpl)

        if questions:
            result_cats.append({
                "id": cat_id,
                "label": _CATEGORY_LABEL[cat_id],
                "questions": questions,
            })

    return {
        "status": "OK",
        "region": region,
        "categories": result_cats,
    }
