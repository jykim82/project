"""태그 품질 계층 P1 — 계측 품질 검사 5종 통합 배치.

사양: docs/tag-quality-layer-spec.md (2026-07-23)
배경: 성상1 포화·죽동 DI 상시 ON·E-051 저신호가 각각 개별 규칙으로 흩어져
있던 것을 단일 파이프라인으로 통합. 결과는 tb_tag_quality 에 태그당 1행
(비정상만 저장, 정상 복귀 시 삭제) — 이상 진단·AI 요약·대시보드가 공유.

실행: ai_server `_tag_quality_loop` (1시간 주기) 또는
      `python -m tag_quality` (수동 1회)
"""

import json
import logging
import os

logger = logging.getLogger("slm.tag_quality")

# 판정 창·임계 — config 분리 (하드코딩 금지 원칙)
RECENT_HOURS = int(os.environ.get("TQ_RECENT_HOURS", "6"))
CEILING_DAYS = int(os.environ.get("TQ_CEILING_DAYS", "90"))
PINNED_PCT = float(os.environ.get("TQ_PINNED_PCT", "80"))       # Q1 포화
FLAT_PCT = float(os.environ.get("TQ_FLAT_PCT", "90"))           # Q2 고착
DI_ON_RATIO = float(os.environ.get("TQ_DI_ON_RATIO", "0.95"))   # Q4 DI 반전
LOW_SIGNAL_RATIO = float(os.environ.get("TQ_LOW_SIGNAL_RATIO", "0.1"))  # Q5

# reason → status 매핑 (bad = 계측 자체가 죽음 / suspect = 값은 오나 신뢰 불가)
_REASON_STATUS = {
    "센서무응답": "bad",
    "데이터없음": "bad",
    "센서포화의심": "suspect",
    "신호고착의심": "suspect",
    "데이터홀딩": "suspect",
    "DI상시ON의심": "suspect",
    "값이탈저신호": "suspect",
}
# 복수 사유 시 우선순위 (심각한 것부터)
_REASON_ORDER = [
    "센서무응답", "데이터없음", "센서포화의심", "신호고착의심",
    "데이터홀딩", "DI상시ON의심", "값이탈저신호",
]


def _analog_universe_sql() -> str:
    """검사 대상 Analog 태그 — 적산·설정값 제외 (기존 규칙 승계)."""
    return """
        SELECT tagsn, sitename, facilitytype, datainfo
        FROM tb_tag_info
        WHERE tagtype = 'Analog Input'
          AND datainfo NOT LIKE '%%적산%%'
          AND datainfo NOT LIKE '%%설정%%'
          AND datainfo NOT LIKE '%%SET%%'
    """


def _check_analog(cur) -> dict:
    """Q1 포화 / Q2 고착 / Q3 무신호·홀딩 / Q5 값 이탈 — 전 Analog 태그 1쿼리."""
    cur.execute(f"""
        WITH universe AS ({_analog_universe_sql()}),
        ceil AS (
            SELECT s.tagsn, MAX(s.max_val) AS ceiling,
                   PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY (s.min_val+s.max_val)/2.0) AS med
            FROM cagg_5min_raw_stats_ai s
            JOIN universe u ON u.tagsn = s.tagsn
            WHERE s.bucket >= now() - interval '{CEILING_DAYS} days'
            GROUP BY s.tagsn
        ),
        recent AS (
            SELECT s.tagsn,
                   COUNT(*) AS r_cnt,
                   COUNT(*) FILTER (WHERE s.min_val >= c.ceiling*0.999
                                      AND s.max_val >= c.ceiling*0.999) AS pinned_cnt,
                   COUNT(*) FILTER (WHERE s.min_val = s.max_val) AS flat_cnt,
                   AVG((s.min_val+s.max_val)/2.0) AS r_avg
            FROM cagg_5min_raw_stats_ai s
            JOIN ceil c ON c.tagsn = s.tagsn
            WHERE s.bucket >= now() - interval '{RECENT_HOURS} hours'
            GROUP BY s.tagsn, c.ceiling
        ),
        week AS (
            SELECT s.tagsn,
                   COUNT(*) AS w_cnt,
                   COUNT(*) FILTER (WHERE s.min_val <> s.max_val) AS moving_cnt,
                   COUNT(*) FILTER (WHERE (s.min_val+s.max_val)/2.0 > 0.001) AS nonzero_cnt
            FROM cagg_5min_raw_stats_ai s
            JOIN universe u ON u.tagsn = s.tagsn
            WHERE s.bucket >= now() - interval '7 days'
            GROUP BY s.tagsn
        )
        SELECT u.tagsn, u.sitename, u.facilitytype, u.datainfo,
               c.ceiling, c.med,
               r.r_cnt, r.pinned_cnt, r.flat_cnt, r.r_avg,
               w.w_cnt, w.moving_cnt, w.nonzero_cnt
        FROM universe u
        LEFT JOIN ceil c ON c.tagsn = u.tagsn
        LEFT JOIN recent r ON r.tagsn = u.tagsn
        LEFT JOIN week w ON w.tagsn = u.tagsn
    """)
    issues: dict[str, dict] = {}

    def _add(tagsn, meta, reason, detail, stats):
        cur_i = issues.get(tagsn)
        if cur_i and _REASON_ORDER.index(cur_i["reason"]) <= _REASON_ORDER.index(reason):
            return
        issues[tagsn] = {**meta, "reason": reason, "detail": detail, "stats": stats}

    for (tagsn, sn, ft, di, ceiling, med, r_cnt, pinned, flat, r_avg,
         w_cnt, moving, nonzero) in cur.fetchall():
        meta = {"sitename": sn, "facilitytype": ft, "datainfo": di}
        stats = {"ceiling": float(ceiling) if ceiling is not None else None,
                 "recent_cnt": r_cnt, "pinned": pinned, "flat": flat,
                 "week_moving": moving, "week_nonzero": nonzero}
        # Q3 — 데이터없음 / 센서무응답 (7일 기준)
        if not w_cnt:
            _add(tagsn, meta, "데이터없음", "최근 7일간 데이터 없음", stats)
            continue
        if nonzero == 0:
            _add(tagsn, meta, "센서무응답", f"7일간 {w_cnt}버킷 전부 val~0", stats)
            continue
        if not r_cnt or ceiling is None or ceiling <= 0.001:
            continue
        pinned_pct = pinned / r_cnt * 100
        flat_pct = flat / r_cnt * 100
        # Q1 — 센서 포화 (성상1 유출압력 사례)
        if pinned_pct >= PINNED_PCT:
            _add(tagsn, meta, "센서포화의심",
                 f"최근 {RECENT_HOURS}시간의 {pinned_pct:.0f}% 가 {CEILING_DAYS}일 관측 상한 "
                 f"{float(ceiling):g} 에 고정 — 계측기 풀스케일 고착 의심", stats)
            continue
        # Q2 — 신호 고착 (원래 변하던 신호가 멈춤)
        if flat_pct >= FLAT_PCT and (moving or 0) > 12:
            _add(tagsn, meta, "신호고착의심",
                 f"최근 {RECENT_HOURS}시간 값 변화 없음 (직전 7일은 정상 변동) — 계측·전송 정체 의심",
                 stats)
            continue
        # Q3' — 데이터홀딩 (7일 대부분 flat)
        if w_cnt and (w_cnt - (moving or 0)) / w_cnt * 100 > 80:
            _add(tagsn, meta, "데이터홀딩", f"7일 flat {(w_cnt - moving) / w_cnt * 100:.0f}%", stats)
            continue
        # Q5 — 값 이탈 저신호 (E-051 계열: 스케일 대비 무의미한 잔값)
        if (med is not None and float(med) > 0.01 and r_avg is not None
                and float(r_avg) < float(med) * LOW_SIGNAL_RATIO):
            _add(tagsn, meta, "값이탈저신호",
                 f"최근 {RECENT_HOURS}시간 평균 {float(r_avg):g} — {CEILING_DAYS}일 중앙값 "
                 f"{float(med):g} 의 {LOW_SIGNAL_RATIO*100:.0f}% 미만 (계측 정지/이탈 의심)", stats)
    return issues


def _check_di(cur) -> dict:
    """Q4 — 장애 그룹 DI 상시 ON (접점 반전/고착 의심, 죽동 정전 DI 사례)."""
    cur.execute("""
        WITH di AS (
            SELECT gm.tagsn, dg.group_code, ti.sitename, ti.facilitytype, ti.datainfo
            FROM tb_tag_group_map gm
            JOIN tb_tag_data_group dg ON gm.group_id = dg.group_id
            JOIN tb_tag_info ti ON gm.tagsn = ti.tagsn
            WHERE dg.group_code IN ('COMM_ERROR', 'EQUIP_FAULT', 'POWER_FAULT')
        )
        SELECT d.tagsn, d.sitename, d.facilitytype, d.datainfo, d.group_code,
               AVG(CASE WHEN r.val = 1 THEN 1.0 ELSE 0.0 END) AS on_ratio
        FROM tb_tag_raw_data r
        JOIN di d ON d.tagsn = r.tagsn
        WHERE r.logtime >= now() - interval '7 days'
        GROUP BY d.tagsn, d.sitename, d.facilitytype, d.datainfo, d.group_code
    """)
    issues: dict[str, dict] = {}
    for tagsn, sn, ft, di, gc, on_ratio in cur.fetchall():
        if on_ratio is not None and float(on_ratio) >= DI_ON_RATIO:
            issues[tagsn] = {
                "sitename": sn, "facilitytype": ft, "datainfo": di,
                "reason": "DI상시ON의심",
                "detail": (f"최근 7일 on 비율 {float(on_ratio)*100:.0f}% — "
                           f"접점 반전/고착 의심으로 설비 장애 판정 제외 ({gc})"),
                "stats": {"on_ratio": round(float(on_ratio), 3), "group": gc},
            }
    return issues


def run_quality_check(get_conn, region: str = "R01") -> dict:
    """검사 5종 실행 → tb_tag_quality UPSERT (정상 복귀 태그는 삭제).

    Returns 요약 {"total": n, "by_reason": {...}} — 루프 로그용.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        issues = _check_analog(cur)
        issues.update(_check_di(cur))

        # 정상 복귀 삭제 → 남은 비정상 UPSERT (같은 reason 이면 since 유지)
        keep = list(issues.keys())
        if keep:
            cur.execute(
                "DELETE FROM tb_tag_quality WHERE region=%s AND NOT (tagsn = ANY(%s))",
                (region, keep))
        else:
            cur.execute("DELETE FROM tb_tag_quality WHERE region=%s", (region,))
        for tagsn, it in issues.items():
            cur.execute("""
                INSERT INTO tb_tag_quality
                  (region, tagsn, status, reason, detail, since, checked_at, window_stats)
                VALUES (%s,%s,%s,%s,%s, now(), now(), %s::jsonb)
                ON CONFLICT (region, tagsn) DO UPDATE SET
                  status=EXCLUDED.status, reason=EXCLUDED.reason,
                  detail=EXCLUDED.detail, checked_at=now(),
                  window_stats=EXCLUDED.window_stats,
                  since=CASE WHEN tb_tag_quality.reason = EXCLUDED.reason
                             THEN tb_tag_quality.since ELSE now() END
            """, (region, tagsn, _REASON_STATUS.get(it["reason"], "suspect"),
                  it["reason"], it["detail"],
                  json.dumps(it.get("stats") or {}, ensure_ascii=False)))
        conn.commit()

        by_reason: dict[str, int] = {}
        for it in issues.values():
            by_reason[it["reason"]] = by_reason.get(it["reason"], 0) + 1
        return {"total": len(issues), "by_reason": by_reason}
    finally:
        conn.close()


def fetch_issues(cur, region: str = "R01", sitename: str | None = None) -> list[dict]:
    """소비처용 조회 — 기존 data_quality_issues 와 동일 형태(issue_type 키)."""
    sql = """
        SELECT q.tagsn, t.sitename, t.facilitytype, t.datainfo,
               q.reason, q.detail, q.since
        FROM tb_tag_quality q
        LEFT JOIN tb_tag_info t ON t.tagsn = q.tagsn
        WHERE q.region = %s
    """
    params: list = [region]
    if sitename:
        sql += " AND t.sitename = %s"
        params.append(sitename)
    cur.execute(sql + " ORDER BY q.since", tuple(params))
    out = []
    for tagsn, sn, ft, di, reason, detail, since in cur.fetchall():
        days = None
        try:
            from datetime import datetime, timezone
            days = (datetime.now(timezone.utc) - since).days if since else None
        except Exception:
            pass
        if days and days >= 1:
            detail = f"{detail} · {days}일째 지속"
        out.append({"tagsn": tagsn, "sitename": sn, "facilitytype": ft,
                    "datainfo": di, "issue_type": reason, "detail": detail})
    return out


def fetch_tag_issue(cur, tagsn: str, region: str = "R01") -> dict | None:
    """단일 태그의 현재 품질 이상 — 트렌드 카드 배지·판정 게이트용 (P2)."""
    cur.execute(
        "SELECT status, reason, detail, since FROM tb_tag_quality "
        "WHERE region = %s AND tagsn = %s",
        (region, tagsn))
    r = cur.fetchone()
    if not r:
        return None
    return {"status": r[0], "reason": r[1], "detail": r[2],
            "since": r[3].isoformat() if r[3] else None}


def fetch_stuck_di_tagsns(cur, region: str = "R01") -> set:
    """설비 장애 게이트용 — DI 상시 ON 태그 집합."""
    cur.execute(
        "SELECT tagsn FROM tb_tag_quality WHERE region=%s AND reason='DI상시ON의심'",
        (region,))
    return {r[0] for r in cur.fetchall()}


if __name__ == "__main__":
    import psycopg2
    logging.basicConfig(level=logging.INFO)
    # 컨테이너 내 실행 기본값 — 호스트에서 직접 돌릴 땐 DATABASE_URL 지정
    _dsn = os.environ.get("DATABASE_URL") or \
        "postgresql://slm_dev:slm_dev_1234@slm-timescaledb:5432/slm"
    summary = run_quality_check(lambda: psycopg2.connect(_dsn))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
