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


# ── 용어집 · 후보 단어 발굴 (구축 보조) ──────────────────

# SLM 조회 계약 핵심 단어 — datainfo 가 이 표준 표기를 담아야 시스템 기능이
# 매칭된다 (인텐트 SQL LIKE·품질 계층·임계 조사·분류 근거). 구축 시 변환
# 목표 어휘로 안내.
CORE_KEYWORDS: list[dict] = [
    {"word": "유량", "why": "유량 트렌드·야간최소유량·물수지 조회의 LIKE 매칭 기준"},
    {"word": "순시", "why": "순시(현재값) 계열 식별 — '유량순시유량' 표준형"},
    {"word": "적산", "why": "적산차 변환·기준선 학습 제외 대상 식별"},
    {"word": "수위", "why": "수위 트렌드·HH/LL 임계·만수위 캡 식별"},
    {"word": "압력", "why": "압력 트렌드·임계 커버리지 조사 식별"},
    {"word": "탁도", "why": "수질 계열 trend_kind 분류"},
    {"word": "PH", "why": "수질 계열 trend_kind 분류"},
    {"word": "잔류염소", "why": "수질 계열 trend_kind 분류"},
    {"word": "전기전도도", "why": "수질 계열 trend_kind 분류"},
    {"word": "알람", "why": "경보 DI 식별 (경보_ 접두는 '알람 '으로 표준화)"},
    {"word": "SET", "why": "임계 설정값(AO) 식별 — 임계 보유 조사 정규식"},
    {"word": "상태", "why": "알람 상태 DI 접미 표준"},
    {"word": "통신이상", "why": "comm_error 판정·품질 계층 DI 그룹 매칭"},
    {"word": "정전", "why": "POWER_FAULT 그룹·전원 계열 식별"},
    {"word": "FAULT", "why": "설비 고장 DI 표준 (FLT 등 약어를 이걸로 통일)"},
    {"word": "동작", "why": "가동 상태 DI 표준 (RUN→동작)"},
    {"word": "정지", "why": "정지 상태 DI 표준 (STOP→정지)"},
    {"word": "자동", "why": "제어 모드 표준 (AT/AUTO→자동)"},
    {"word": "원격", "why": "제어 모드 표준 (REMOTE→원격)"},
    {"word": "로컬", "why": "제어 모드 표준 (LOCAL→로컬)"},
    {"word": "설정", "why": "제어 설정값 식별 — 임계(SET)와 구분·조회 제외 규칙"},
    {"word": "가압펌프", "why": "가압장 설비 표준 명칭 ('펌프' 단독보다 우선)"},
    {"word": "FULL OPEN / FULL CLOSE", "why": "밸브 전개/전폐 표준 (F_OPEN 등 통일)"},
]

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z_/\.]{1,14}")


@router.get("/vocab")
def vocab(region: str = Query("R01"), min_count: int = Query(3, ge=1)):
    """구축 보조 용어집 — ① 핵심 표준 단어(조회 계약) ② 룰 미등록 약어 후보.

    후보 = datadesc 의 영문 토큰 중 (a) 어떤 룰 pattern/replacement 에도
    없고 (b) 현행 datainfo 표준에도 안 남는 것 — 빈도순. 구축자가 이 목록을
    보고 사전 룰을 정의하면 된다 (수동 시그니처 마이닝의 제품화).
    """
    conn = _conn()
    try:
        cur = conn.cursor()
        rules = _load_rules(cur, region)
        known = " ".join(
            f"{r['pattern']} {r['replacement']}" for r in rules).upper()
        core = " ".join(k["word"] for k in CORE_KEYWORDS).upper()
        cur.execute("""
            SELECT COALESCE(datadesc,''), COALESCE(datainfo,'')
            FROM tb_tag_info WHERE datadesc IS NOT NULL AND datadesc <> ''
        """)
        from collections import Counter
        cnt: Counter = Counter()
        sample: dict[str, str] = {}
        info_all: Counter = Counter()
        for desc, info in cur.fetchall():
            for t in set(_TOKEN_RE.findall(info.upper())):
                info_all[t] += 1
            for t in set(_TOKEN_RE.findall(desc.upper())):
                cnt[t] += 1
                sample.setdefault(t, desc)
        candidates = []
        for tok, n in cnt.most_common(200):
            if n < min_count:
                break
            if tok in known or tok in core:
                continue
            # info 표준에도 흔히 남는 토큰(UPS·LTE 등 고유명)은 변환 불요 추정
            if info_all.get(tok, 0) >= n * 0.8:
                continue
            candidates.append({"token": tok, "count": n, "sample": sample[tok]})
        return {"status": "OK", "core_keywords": CORE_KEYWORDS,
                "candidates": candidates[:40]}
    finally:
        conn.close()


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
          diff(변환≠현 info — 룰 후보) /
          hard(**룰 원리상 불가 의심** — 동일 desc 가 다른 info 로 갈리거나,
               desc 에 없는 정보가 info 에 추가됨 → 제외/확정 지정 대상) /
          excluded(태그 단위 변환 제외 — 현행 유지) / manual(override 확정)
    """
    conn = _conn()
    try:
        cur = conn.cursor()
        rules = _load_rules(cur, region)
        rows = _fetch_tags(cur, sitename, keyword)

        # 룰 불가 신호 1 — 동일 desc(정규화)가 서로 다른 info 로 매핑:
        # 룰은 결정적(같은 입력=같은 출력)이라 원리상 분기 불가 (예: "밸브 RE"
        # → 자동/원격). 전체 셋 기준 선계산.
        desc_info: dict[str, set] = {}
        for _tagsn, _sn, _ft, _tt, desc, info in rows:
            desc_info.setdefault(_norm(desc), set()).add(_norm(info))
        ambiguous_desc = {d for d, infos in desc_info.items() if len(infos) > 1}

        def _info_added(desc: str, conv: str, info: str) -> bool:
            """룰 불가 신호 2 — info 에만 있는 실질 토큰 (desc 로 도출 불가 정보)."""
            for tok in re.findall(r"[가-힣]{2,}|\d지", _norm(info)):
                if tok not in desc and tok not in conv:
                    return True
            return False

        out, counts = [], {"unchanged": 0, "match": 0, "diff": 0,
                           "hard": 0, "excluded": 0, "manual": 0}
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
            elif _norm(desc) in ambiguous_desc or _info_added(desc, converted, info):
                cat = "hard"
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
