"""DATAINFO 변환룰 — datadesc(SCADA 원본) → datainfo(SLM 조회 표준).

사양: docs/datainfo-conversion-rule-spec.md (구축 고도화 ①, Migration 0117)
- 4계층 룰 파이프라인: regex → dict → context → override (priority 순)
- 미리보기(분류: unchanged/match/diff) → 선별 적용(이력 로그) → 재현율 채점
"""

import logging
import re
from typing import Callable, Optional

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/setup/datainfo-rules", tags=["datainfo-rules"])

_get_db_connection: Optional[Callable] = None


def init(get_db_connection_fn):
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


def _conn():
    if _get_db_connection is None:
        raise RuntimeError("datainfo_rules not initialized")
    return _get_db_connection()


# ── 엔진 ─────────────────────────────────────────────────

def _load_rules(cur, region: str) -> list[dict]:
    cur.execute(
        """
        SELECT rule_id, rule_type, pattern, replacement,
               context_facilitytype, context_tagtype, target_tagsn, priority
        FROM tb_datainfo_rule
        WHERE region = %s AND enabled
        ORDER BY priority ASC, rule_id ASC
        """,
        (region,))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def tag_policy(rules: list[dict], tagsn: str) -> Optional[str]:
    """태그 단위 정책 — 'exclude'(변환 제외·현행 유지) | 'override'(확정) | None."""
    for r in rules:
        if r.get("target_tagsn") == tagsn and r["rule_type"] in ("exclude", "override"):
            return r["rule_type"]
    return None


def apply_rules(desc: str, rules: list[dict],
                facilitytype: str = "", tagtype: str = "",
                tagsn: str = "") -> str:
    """룰 파이프라인 적용. override 매칭 시 즉시 확정."""
    # override 최우선 (태그 단위 최종 고정)
    for r in rules:
        if r["rule_type"] == "override" and r.get("target_tagsn") == tagsn:
            return r["replacement"]
    s = desc
    for r in rules:
        try:
            if r["rule_type"] == "regex":
                s = re.sub(r["pattern"], r["replacement"], s)
            elif r["rule_type"] == "dict":
                # 단어 경계 — 영문 약어 오치환 방지 (한글은 경계 미적용)
                if re.search(r"[A-Za-z]", r["pattern"]):
                    s = re.sub(rf"\b{re.escape(r['pattern'])}\b", r["replacement"], s)
                else:
                    s = s.replace(r["pattern"], r["replacement"])
            elif r["rule_type"] == "context":
                if r.get("context_facilitytype") and r["context_facilitytype"] != facilitytype:
                    continue
                if r.get("context_tagtype") and r["context_tagtype"] != tagtype:
                    continue
                s = re.sub(r["pattern"], r["replacement"], s)
        except re.error as e:
            logger.warning(f"datainfo rule #{r['rule_id']} 정규식 오류(건너뜀): {e}")
    return re.sub(r"\s+", " ", s).strip()


def _norm(s: str) -> str:
    """비교용 정규화 — 공백 접기 (datainfo 에 이중 공백 유산 존재)."""
    return re.sub(r"\s+", " ", s or "").strip()


# ── CRUD ─────────────────────────────────────────────────

class RuleBody(BaseModel):
    rule_type: str
    pattern: str = ""
    replacement: str
    context_facilitytype: Optional[str] = None
    context_tagtype: Optional[str] = None
    target_tagsn: Optional[str] = None
    priority: int = 100
    enabled: bool = True
    notes: Optional[str] = None


@router.get("")
def list_rules(region: str = Query("R01")):
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT rule_id, rule_type, pattern, replacement,
                   context_facilitytype, context_tagtype, target_tagsn,
                   priority, enabled, notes, updated_at
            FROM tb_datainfo_rule WHERE region = %s
            ORDER BY priority, rule_id
            """,
            (region,))
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            r["updated_at"] = r["updated_at"].isoformat() if r["updated_at"] else None
        return {"status": "OK", "rules": rows}
    finally:
        conn.close()


@router.post("")
def create_rule(body: RuleBody, region: str = Query("R01"), user: str = Query("")):
    if body.rule_type not in ("regex", "dict", "context", "override", "exclude"):
        raise HTTPException(400, "rule_type 오류")
    if body.rule_type in ("override", "exclude") and not body.target_tagsn:
        raise HTTPException(400, f"{body.rule_type} 룰은 target_tagsn 필수")
    if body.rule_type == "regex":
        try:
            re.compile(body.pattern)
        except re.error as e:
            raise HTTPException(400, f"정규식 오류: {e}")
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO tb_datainfo_rule
              (region, rule_type, pattern, replacement, context_facilitytype,
               context_tagtype, target_tagsn, priority, enabled, notes, updated_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING rule_id
            """,
            (region, body.rule_type, body.pattern, body.replacement,
             body.context_facilitytype, body.context_tagtype, body.target_tagsn,
             body.priority, body.enabled, body.notes, user or None))
        rid = cur.fetchone()[0]
        conn.commit()
        return {"status": "OK", "rule_id": rid}
    finally:
        conn.close()


@router.put("/{rule_id}")
def update_rule(rule_id: int, body: RuleBody, region: str = Query("R01"), user: str = Query("")):
    if body.rule_type == "regex":
        try:
            re.compile(body.pattern)
        except re.error as e:
            raise HTTPException(400, f"정규식 오류: {e}")
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE tb_datainfo_rule SET
              rule_type=%s, pattern=%s, replacement=%s, context_facilitytype=%s,
              context_tagtype=%s, target_tagsn=%s, priority=%s, enabled=%s,
              notes=%s, updated_at=now(), updated_by=%s
            WHERE rule_id=%s AND region=%s
            """,
            (body.rule_type, body.pattern, body.replacement,
             body.context_facilitytype, body.context_tagtype, body.target_tagsn,
             body.priority, body.enabled, body.notes, user or None, rule_id, region))
        conn.commit()
        return {"status": "OK", "updated": cur.rowcount}
    finally:
        conn.close()


@router.delete("/{rule_id}")
def delete_rule(rule_id: int, region: str = Query("R01")):
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM tb_datainfo_rule WHERE rule_id=%s AND region=%s",
                    (rule_id, region))
        conn.commit()
        return {"status": "OK", "deleted": cur.rowcount}
    finally:
        conn.close()


# ── 미리보기 / 채점 / 적용 ────────────────────────────────

def _fetch_tags(cur, sitename: str = "", keyword: str = "") -> list[tuple]:
    sql = """
        SELECT tagsn, sitename, facilitytype, tagtype,
               COALESCE(datadesc,''), COALESCE(datainfo,'')
        FROM tb_tag_info WHERE datadesc IS NOT NULL AND datadesc <> ''
    """
    params: list = []
    if sitename:
        sql += " AND sitename = %s"
        params.append(sitename)
    if keyword:
        sql += " AND (datadesc ILIKE %s OR datainfo ILIKE %s)"
        params += [f"%{keyword}%", f"%{keyword}%"]
    cur.execute(sql + " ORDER BY sitename, tagsn", tuple(params))
    return cur.fetchall()


@router.post("/preview")
def preview(region: str = Query("R01"), sitename: str = Query(""),
            keyword: str = Query(""), category: str = Query(""),
            limit: int = Query(300, ge=1, le=3000)):
    """전/필터 태그에 룰 적용 미리보기.

    분류: unchanged(desc=현 info) / match(변환=현 info — 룰 재현 성공) /
          diff(변환≠현 info — 검토·적용 후보) /
          excluded(태그 단위 변환 제외 — 현행 유지) / manual(override 확정)
    """
    conn = _conn()
    try:
        cur = conn.cursor()
        rules = _load_rules(cur, region)
        rows = _fetch_tags(cur, sitename, keyword)
        out, counts = [], {"unchanged": 0, "match": 0, "diff": 0,
                           "excluded": 0, "manual": 0}
        for tagsn, sn, ft, tt, desc, info in rows:
            policy = tag_policy(rules, tagsn)
            converted = apply_rules(desc, rules, ft or "", tt or "", tagsn)
            if policy == "exclude":
                cat = "excluded"
                converted = _norm(info)  # 제외 = 현행 유지
            elif policy == "override":
                cat = "manual"
            elif _norm(desc) == _norm(info):
                cat = "unchanged"
            elif converted == _norm(info):
                cat = "match"
            else:
                cat = "diff"
            counts[cat] += 1
            if category and cat != category:
                continue
            if len(out) < limit:
                out.append({"tagsn": tagsn, "sitename": sn, "facilitytype": ft,
                            "datadesc": desc, "datainfo": info,
                            "converted": converted, "category": cat})
        return {"status": "OK", "counts": counts, "total": len(rows), "rows": out}
    finally:
        conn.close()


@router.get("/score")
def score(region: str = Query("R01")):
    """룰셋 재현율 — 기존 desc→info 쌍 대비 (룰셋 완성도 정량 지표).

    제외(exclude)·확정(override) 태그는 룰 평가 대상이 아니므로 분모에서
    빼고 별도 카운트로 보고한다.
    """
    conn = _conn()
    try:
        cur = conn.cursor()
        rules = _load_rules(cur, region)
        rows = _fetch_tags(cur)
        total = ok = raw_same = excluded = manual = 0
        for tagsn, _sn, ft, tt, desc, info in rows:
            policy = tag_policy(rules, tagsn)
            if policy == "exclude":
                excluded += 1
                continue
            if policy == "override":
                manual += 1
                continue
            total += 1
            if _norm(desc) == _norm(info):
                raw_same += 1
                ok += 1
            elif apply_rules(desc, rules, ft or "", tt or "", tagsn) == _norm(info):
                ok += 1
        return {"status": "OK", "total": total,
                "raw_same": raw_same,
                "reproduced": ok,
                "excluded": excluded,
                "manual": manual,
                "score_pct": round(ok / total * 100, 1) if total else 0,
                "rule_count": len(rules)}
    finally:
        conn.close()


@router.post("/apply")
def apply_to_tags(tagsns: list[str] = Body(..., embed=True),
                  region: str = Query("R01"), user: str = Query("")):
    """선택 태그의 datainfo 를 변환 결과로 UPDATE (이력 로그 — 롤백 가능).

    안전: 명시 선택만 적용. 변환 결과가 빈 문자열이면 건너뜀.
    """
    if not tagsns or len(tagsns) > 3000:
        raise HTTPException(400, "tagsns 1~3000개")
    conn = _conn()
    try:
        cur = conn.cursor()
        rules = _load_rules(cur, region)
        cur.execute(
            """
            SELECT tagsn, facilitytype, tagtype, COALESCE(datadesc,''), COALESCE(datainfo,'')
            FROM tb_tag_info WHERE tagsn = ANY(%s)
            """,
            (tagsns,))
        applied = skipped = 0
        for tagsn, ft, tt, desc, info in cur.fetchall():
            # 제외 태그는 어떤 경로로도 변환 적용 금지 (현행 유지)
            if tag_policy(rules, tagsn) == "exclude":
                skipped += 1
                continue
            converted = apply_rules(desc, rules, ft or "", tt or "", tagsn)
            if not converted or converted == _norm(info):
                skipped += 1
                continue
            cur.execute("UPDATE tb_tag_info SET datainfo=%s WHERE tagsn=%s",
                        (converted, tagsn))
            cur.execute(
                """
                INSERT INTO tb_datainfo_apply_log
                  (region, tagsn, old_datainfo, new_datainfo, applied_by)
                VALUES (%s,%s,%s,%s,%s)
                """,
                (region, tagsn, info, converted, user or None))
            applied += 1
        conn.commit()
        logger.info(f"datainfo 룰 적용: {applied}건 (건너뜀 {skipped})")
        return {"status": "OK", "applied": applied, "skipped": skipped}
    finally:
        conn.close()
