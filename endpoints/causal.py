"""
인과관계 API 엔드포인트 모듈

- GET  /causal/rules                          — 인과 체인 템플릿 + 시설별 매핑 현황
- GET  /causal/verify                         — 시설 인과 체인 상태 검증
- GET  /causal/chain/{sitename}/{facilitytype} — 시설별 인과 체인 상세
- PUT  /causal/chain/{sitename}/{facilitytype} — 인과 체인 오버라이드 저장
- DELETE /causal/chain/{sitename}/{facilitytype} — 인과 체인 오버라이드 삭제
- POST /causal/estimate-lag                   — 교차상관 기반 시간 지연 추정

ai_server.py에서 분리된 모듈 — init()으로 의존성을 주입받아 사용.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from shared.timeseries import get_chunks_for_range, query_chunks_raw

logger = logging.getLogger("slm")

router = APIRouter()

# ai_server.py에서 주입
_get_db_connection = None
_causal_index = None            # dict ref → _CAUSAL_INDEX
_causal_template_map = None     # dict ref → _CAUSAL_TEMPLATE_MAP
_causal_chain_templates = None  # list ref → CAUSAL_CHAIN_TEMPLATES
_detect_zones_fn = None         # fn ref → _detect_zones
_get_causal_info_fn = None      # fn ref → _get_causal_info
_rebuild_causal_index_fn = None # fn ref → _rebuild_causal_index_entry


def init(
    get_db_connection_fn,
    causal_index,
    causal_template_map,
    causal_chain_templates,
    detect_zones_fn,
    get_causal_info_fn,
    rebuild_causal_index_fn,
):
    """ai_server.py에서 DB + 인과 인덱스 관련 의존성을 주입받는다."""
    global _get_db_connection, _causal_index, _causal_template_map
    global _causal_chain_templates, _detect_zones_fn, _get_causal_info_fn
    global _rebuild_causal_index_fn
    _get_db_connection = get_db_connection_fn
    _causal_index = causal_index
    _causal_template_map = causal_template_map
    _causal_chain_templates = causal_chain_templates
    _detect_zones_fn = detect_zones_fn
    _get_causal_info_fn = get_causal_info_fn
    _rebuild_causal_index_fn = rebuild_causal_index_fn


# =============================================================================
# 요청 모델
# =============================================================================

class CausalChainSaveRequest(BaseModel):
    zone: Optional[str] = None
    chain: list
    cross_facility: Optional[dict] = None
    source: str = "manual"


class CausalLagRequest(BaseModel):
    sitename: str
    facilitytype: str
    zone: Optional[str] = None
    days: int = 14


# =============================================================================
# GET /causal/rules — 인과 체인 템플릿 + 현황
# =============================================================================

@router.get("/causal/rules")
async def get_causal_rules():
    """인과 체인 템플릿 + 시설별 매핑 현황 + 커버리지 통계."""
    # 1) 시설유형별 템플릿
    templates: dict[str, dict] = {}
    for t in _causal_chain_templates:
        templates[t["facilitytype"]] = {
            "chain": t["chain"],
            "cross_facility": t.get("cross_facility"),
            "safety_interlocks": t.get("safety_interlocks", []),
            "and_conditions": t.get("and_conditions", []),
            "reverse_diagnostics": t.get("reverse_diagnostics", []),
            "propagation": t.get("propagation"),
        }

    # 2) 오버라이드 목록 (sitename, facilitytype, zone)
    override_set: set[tuple[str, str]] = set()
    for key in _causal_index:
        if len(key) == 3:
            override_set.add((key[0], key[1]))
    for key, info in _causal_index.items():
        if len(key) != 2:
            continue
        sn, ft = key
        orig_tmpl = _causal_template_map.get(ft)
        if orig_tmpl and info["template"].get("chain") != orig_tmpl.get("chain"):
            override_set.add((sn, ft))

    # 3) 시설별 상세
    facilities = []
    for key, info in _causal_index.items():
        if len(key) != 2:
            continue
        sn, ft = key
        tmpl = info["template"]
        chain_steps = tmpl.get("chain", [])
        tag_map = info.get("tag_map", {})
        tag_coverage: dict[str, int] = {}
        for step in chain_steps:
            gc = step["group_code"]
            tag_coverage[gc] = len(tag_map.get(gc, []))
        total_steps = len(chain_steps)
        mapped_steps = sum(1 for v in tag_coverage.values() if v > 0)
        zones = sorted(
            k[2] for k in _causal_index if len(k) == 3 and k[0] == sn and k[1] == ft
        )
        upstream = [f"{u[0]} {u[1]}" for u in info.get("upstream", [])]
        downstream = [f"{d[0]} {d[1]}" for d in info.get("downstream", [])]
        facilities.append({
            "sitename": sn,
            "facilitytype": ft,
            "zones": zones,
            "has_override": (sn, ft) in override_set,
            "tag_coverage": tag_coverage,
            "total_steps": total_steps,
            "mapped_steps": mapped_steps,
            "upstream": upstream,
            "downstream": downstream,
        })

    # 4) 요약 통계
    covered_types = set(templates.keys())
    total_facilities = 0
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(DISTINCT (sitename, facilitytype))
            FROM tb_tag_info
            WHERE facilitytype IN %s AND sitename IS NOT NULL
        """, (tuple(covered_types),))
        total_facilities = cur.fetchone()[0]
        cur.close()
        conn.close()
    except Exception as e:
        logger.warning(f"인과 규칙 전체 시설 수 조회 실패, 폴백 사용: {e}")
        total_facilities = len(facilities)

    summary = {
        "total_facilities": total_facilities,
        "covered_facilities": len(facilities),
        "override_count": len(override_set),
        "full_coverage_count": sum(
            1 for f in facilities if f["mapped_steps"] == f["total_steps"] and f["total_steps"] > 0
        ),
    }

    return {
        "status": "OK",
        "templates": templates,
        "facilities": facilities,
        "summary": summary,
    }


# =============================================================================
# GET /causal/verify — 시설 인과 체인 상태 검증
# =============================================================================

@router.get("/causal/verify")
async def verify_causal(sitename: str, facilitytype: str):
    """특정 시설의 인과 체인 현재 상태 검증 (디버그용)."""
    key = (sitename, facilitytype)
    info = _causal_index.get(key)
    if not info:
        return {"status": "ERROR", "message": f"인과 인덱스에 없음: {sitename} {facilitytype}"}

    template = info["template"]
    tag_map = info["tag_map"]

    steps = []
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        for step_info in template["chain"]:
            gc = step_info["group_code"]
            tagsns = tag_map.get(gc, [])
            latest_values = {}
            if tagsns:
                cur.execute("""
                    SELECT tagsn, val
                    FROM tb_tag_raw_data
                    WHERE tagsn = ANY(%s)
                    ORDER BY logtime DESC
                    LIMIT %s
                """, (tagsns, len(tagsns)))
                for tsn, val in cur.fetchall():
                    if tsn not in latest_values:
                        latest_values[tsn] = float(val) if val else None
            steps.append({
                "step": step_info["step"],
                "group_code": gc,
                "role": step_info["role"],
                "expected": step_info.get("expected"),
                "tag_count": len(tagsns),
                "latest_values": latest_values,
            })
        cur.close()
    except Exception as e:
        logger.warning(f"인과 검증 API 실패: {e}")
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()

    return {
        "status": "OK",
        "sitename": sitename,
        "facilitytype": facilitytype,
        "upstream": info.get("upstream", []),
        "downstream": info.get("downstream", []),
        "chain_steps": steps,
        "cross_facility": template.get("cross_facility"),
        "safety_interlocks": template.get("safety_interlocks", []),
        "and_conditions": template.get("and_conditions", []),
        "reverse_diagnostics": template.get("reverse_diagnostics", []),
        "propagation": template.get("propagation"),
    }


# =============================================================================
# GET /causal/chain/{sitename}/{facilitytype}
# =============================================================================

@router.get("/causal/chain/{sitename}/{facilitytype}")
async def get_causal_chain(sitename: str, facilitytype: str):
    """시설별 인과 체인 상세 (템플릿 + 오버라이드 + 태그매핑 + 구역정보)."""
    template = _causal_template_map.get(facilitytype)
    if not template:
        return {"status": "ERROR", "message": f"인과 템플릿 없음: {facilitytype}"}

    info = _causal_index.get((sitename, facilitytype))
    tag_map = info["tag_map"] if info else {}

    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT zone, chain_json, cross_facility_json, source, updated_at
            FROM tb_causal_chain_override
            WHERE sitename = %s AND facilitytype = %s
            ORDER BY zone NULLS FIRST
        """, (sitename, facilitytype))
        overrides = []
        for zone, cj, cfj, src, upd in cur.fetchall():
            overrides.append({
                "zone": zone,
                "chain": cj,
                "cross_facility": cfj,
                "source": src,
                "updated_at": upd.isoformat() if upd else None,
            })

        zones = _detect_zones_fn(conn, sitename, facilitytype)

        cur.close()
    except Exception as e:
        logger.warning("인과 체인 조회 실패: %s", e)
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()

    effective_chain = template["chain"]
    effective_cross = template.get("cross_facility")
    default_override = next((o for o in overrides if o["zone"] is None), None)
    if default_override:
        effective_chain = default_override["chain"]
        if default_override.get("cross_facility") is not None:
            effective_cross = default_override["cross_facility"]

    return {
        "status": "OK",
        "template": {"chain": template["chain"], "cross_facility": template.get("cross_facility")},
        "effective": {"chain": effective_chain, "cross_facility": effective_cross},
        "overrides": overrides,
        "tag_map": tag_map,
        "zones": zones,
        "upstream": [list(u) for u in (info.get("upstream", []) if info else [])],
        "downstream": [list(d) for d in (info.get("downstream", []) if info else [])],
    }


# =============================================================================
# PUT /causal/chain/{sitename}/{facilitytype}
# =============================================================================

@router.put("/causal/chain/{sitename}/{facilitytype}")
async def save_causal_chain(sitename: str, facilitytype: str, body: CausalChainSaveRequest):
    """인과 체인 오버라이드 저장 (UPSERT)."""
    zone = body.zone
    chain = body.chain
    cross = body.cross_facility
    source = body.source

    if not chain:
        return {"status": "ERROR", "message": "chain 필수"}

    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO tb_causal_chain_override
                (sitename, facilitytype, zone, chain_json, cross_facility_json, source, updated_at)
            VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, now())
            ON CONFLICT (sitename, facilitytype, zone) DO UPDATE SET
                chain_json = EXCLUDED.chain_json,
                cross_facility_json = EXCLUDED.cross_facility_json,
                source = EXCLUDED.source,
                updated_at = now()
        """, (sitename, facilitytype, zone,
              json.dumps(chain), json.dumps(cross) if cross else None, source))
        conn.commit()
        cur.close()
    except Exception as e:
        logger.error("인과 체인 저장 실패: %s", e)
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()

    _rebuild_causal_index_fn(sitename, facilitytype)
    return {"status": "OK"}


# =============================================================================
# DELETE /causal/chain/{sitename}/{facilitytype}
# =============================================================================

@router.delete("/causal/chain/{sitename}/{facilitytype}")
async def delete_causal_chain(sitename: str, facilitytype: str, zone: Optional[str] = None):
    """인과 체인 오버라이드 삭제 (템플릿으로 복귀)."""
    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        if zone:
            cur.execute(
                "DELETE FROM tb_causal_chain_override WHERE sitename=%s AND facilitytype=%s AND zone=%s",
                (sitename, facilitytype, zone))
        else:
            cur.execute(
                "DELETE FROM tb_causal_chain_override WHERE sitename=%s AND facilitytype=%s AND zone IS NULL",
                (sitename, facilitytype))
        conn.commit()
        cur.close()
    except Exception as e:
        logger.error("인과 체인 삭제 실패: %s", e)
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()

    _rebuild_causal_index_fn(sitename, facilitytype)
    return {"status": "OK"}


# =============================================================================
# POST /causal/estimate-lag — 교차상관 기반 시간 지연 추정
# =============================================================================

@router.post("/causal/estimate-lag")
async def estimate_causal_lag_api(body: CausalLagRequest):
    """교차상관 기반 인과 시간 지연 자동 추정."""
    sitename = body.sitename
    facilitytype = body.facilitytype
    zone = body.zone
    days = body.days

    info = _get_causal_info_fn(sitename, facilitytype, zone)
    if not info:
        return {"status": "ERROR", "message": f"인과 인덱스에 없음: {sitename} {facilitytype}"}

    template = info["template"]
    tag_map = info["tag_map"]
    chain = template["chain"]

    conn = None
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        from_ts = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        to_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        chunks = get_chunks_for_range(cur, from_ts, to_ts)

        from causal_estimator import estimate_lag

        estimates = []
        for i in range(len(chain) - 1):
            cause_step = chain[i]
            effect_step = chain[i + 1]
            if not effect_step.get("expected"):
                continue

            cause_tags = tag_map.get(cause_step["group_code"], [])
            effect_tags = tag_map.get(effect_step["group_code"], [])
            if not cause_tags or not effect_tags:
                estimates.append({
                    "cause_step": cause_step["step"],
                    "effect_step": effect_step["step"],
                    "cause_group": cause_step["group_code"],
                    "effect_group": effect_step["group_code"],
                    "peak_lag_min": 0, "peak_corr": 0.0,
                    "lag_min": 0, "lag_max": 0, "confidence": "low",
                })
                continue

            all_tags = [cause_tags[0], effect_tags[0]]
            cause_vals = []
            effect_vals = []

            if chunks:
                raw_rows = query_chunks_raw(cur, chunks, all_tags, from_ts, to_ts)
                for tsn, _, val in raw_rows:
                    v = float(val) if val is not None else 0.0
                    if tsn == cause_tags[0]:
                        cause_vals.append(v)
                    elif tsn == effect_tags[0]:
                        effect_vals.append(v)

            result = estimate_lag(cause_vals, effect_vals)
            estimates.append({
                "cause_step": cause_step["step"],
                "effect_step": effect_step["step"],
                "cause_group": cause_step["group_code"],
                "effect_group": effect_step["group_code"],
                **result,
            })

        cur.close()
        return {"status": "OK", "estimates": estimates}
    except Exception as e:
        logger.error("인과 lag 추정 실패: %s", e)
        return {"status": "ERROR", "message": str(e)}
    finally:
        if conn:
            conn.close()
