"""
[DEV-ONLY] tb_tag_raw_data 가상 데이터 생성 데몬 (외부 수집 차단 대체)

목적:
    원격 운영 DB(참고 사이트) 접속이 차단된 기간에도 모니터링/트렌드/
    이상감지/알람 파이프라인이 살아 있도록, 태그별 과거 패턴(시간대
    프로파일)을 따르는 가상 데이터를 로컬 tb_tag_raw_data 에 적재한다.

동작:
    1) 부팅 시 cagg_1h_raw_stats_ai(최근 PROFILE_DAYS일)에서 태그×시간대
       프로파일(평균/표준편차/범위/비영비율)을 구축
    2) BACKFILL_STALL=1 이면 마지막 수집 시각 → 현재의 공백 구간을
       INTERVAL_S 간격으로 백필
    3) 이후 INTERVAL_S 주기로 태그당 1행 생성 (AR(1) 연속성 + 시간대
       프로파일 + 관측 범위 클램프). SET/AO 태그는 마지막 값 유지,
       DI 태그는 시간대 가동률 기반 상태 유지/전환
    4) **외부 수집 재개 가드**: 직전 주기 이후 신규 행 수가 자체 삽입량을
       크게 초과하면(= 진짜 수집이 돌아옴) 생성을 건너뛰고 대기

⚠ 납품 시 제거 대상 (dev-tag-ingest 와 동일 체계):
    - 본 스크립트(`dev_tools/tag_synth.py`) + `Dockerfile.tag_synth`
    - `docker-compose.dev.yml` 의 `dev-tag-synth` 서비스 블록
    - 체크리스트: `docs/dev-tag-ingest-spec.md` (가상 생성 절 포함)

환경 변수:
    LOCAL_DB_HOST/PORT/NAME/USER/PASSWORD  로컬 TimescaleDB (tag_ingest 동일)
    LOCAL_REGION       region 값 (기본: R01)
    INTERVAL_S         생성 주기 초 (기본: 120 — 실수집 ≈2분 주기 실측)
    PROFILE_DAYS       프로파일 학습 기간 일 (기본: 21)
    PROFILE_REFRESH_H  프로파일 재구축 주기 시간 (기본: 24)
    BACKFILL_STALL     1=부팅 시 공백 구간 백필 (기본: 1, 0=끔)
    BACKFILL_MAX_H     백필 상한 시간 (기본: 48 — 과도 백필 방지)
    GUARD_MARGIN_ROWS  외부 재개 판정 여유 행 수 (기본: 200)
"""

from __future__ import annotations

import logging
import math
import os
import random
import signal
import sys
import time
from datetime import datetime, timedelta, timezone

import psycopg2
from psycopg2.extras import execute_values

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("tag_synth")

CFG = {
    "host": os.environ.get("LOCAL_DB_HOST", "timescaledb"),
    "port": int(os.environ.get("LOCAL_DB_PORT", "5432")),
    "dbname": os.environ.get("LOCAL_DB_NAME", "slm"),
    "user": os.environ.get("LOCAL_DB_USER", "slm_dev"),
    "password": os.environ.get("LOCAL_DB_PASSWORD", "slm_dev_1234"),
}
REGION = os.environ.get("LOCAL_REGION", "R01")
INTERVAL_S = int(os.environ.get("INTERVAL_S", "120"))
PROFILE_DAYS = int(os.environ.get("PROFILE_DAYS", "21"))
PROFILE_REFRESH_H = int(os.environ.get("PROFILE_REFRESH_H", "24"))
BACKFILL_STALL = os.environ.get("BACKFILL_STALL", "1") == "1"
BACKFILL_MAX_H = int(os.environ.get("BACKFILL_MAX_H", "48"))
GUARD_MARGIN = int(os.environ.get("GUARD_MARGIN_ROWS", "200"))

_shutdown = False


def _on_signal(signum, _frame):
    global _shutdown
    _shutdown = True
    log.info(f"시그널 {signum} — 종료 준비")


signal.signal(signal.SIGTERM, _on_signal)
signal.signal(signal.SIGINT, _on_signal)


def _connect():
    return psycopg2.connect(**CFG)


def build_profiles(conn) -> dict:
    """태그×시간대 프로파일 + 태그 메타(유형·마지막 값) 로드."""
    cur = conn.cursor()
    # 시간대 프로파일 — 1h 사전집계 재사용 (전 청크 스캔 금지, E-056)
    cur.execute(
        """
        SELECT tagsn, EXTRACT(hour FROM bucket_hr)::int AS hh,
               SUM(nz_sum), SUM(nz_sumsq), SUM(nz_cnt), SUM(cnt),
               MIN(nz_rmin), MAX(nz_rmax)
        FROM cagg_1h_raw_stats_ai
        WHERE bucket_hr >= now() - make_interval(days => %s)
        GROUP BY tagsn, hh
        """,
        (PROFILE_DAYS,),
    )
    prof: dict = {}
    for tagsn, hh, nz_sum, nz_sumsq, nz_cnt, cnt, rmin, rmax in cur.fetchall():
        if not cnt:
            continue
        # SUM(bigint) 은 Decimal 로 반환 — float 통일
        nz_sum = float(nz_sum or 0)
        nz_sumsq = float(nz_sumsq or 0)
        nz_cnt = float(nz_cnt or 0)
        cnt = float(cnt)
        mean = (nz_sum / nz_cnt) if nz_cnt else 0.0
        var = max(0.0, (nz_sumsq / nz_cnt - mean * mean)) if nz_cnt else 0.0
        prof.setdefault(tagsn, {})[hh] = {
            "mean": mean,
            "std": math.sqrt(var),
            "p_nz": (nz_cnt / cnt) if cnt else 0.0,
            "rmin": float(rmin) if rmin is not None else mean,
            "rmax": float(rmax) if rmax is not None else mean,
        }

    # 태그 유형 — DI 는 상태 유지/전환, AO·SET 류는 마지막 값 고정
    cur.execute(
        """
        SELECT tagsn, tagtype, COALESCE(datainfo, '')
        FROM tb_tag_info
        """
    )
    meta = {}
    for tagsn, tagtype, datainfo in cur.fetchall():
        kind = "ai"
        if tagtype == "Digital Input":
            kind = "di"
        elif tagtype == "Analog Output" or "SET" in datainfo.upper() or "설정" in datainfo:
            kind = "hold"
        meta[tagsn] = kind

    # 마지막 관측값 — 연속성의 시작점 (30일 하한, E-056)
    cur.execute(
        """
        SELECT DISTINCT ON (tagsn) tagsn, val
        FROM tb_tag_raw_data
        WHERE logtime >= now() - interval '30 days'
        ORDER BY tagsn, logtime DESC
        """
    )
    last_vals = {t: (v if v is not None else 0.0) for t, v in cur.fetchall()}
    cur.close()
    log.info(
        f"프로파일 구축: 태그 {len(prof)} · 메타 {len(meta)} · 시작값 {len(last_vals)}"
    )
    return {"prof": prof, "meta": meta, "state": last_vals}


def gen_value(tagsn: str, ts: datetime, p: dict) -> float | None:
    """한 태그의 다음 값 생성 — 프로파일 없으면 마지막 값 유지."""
    kind = p["meta"].get(tagsn, "ai")
    prev = p["state"].get(tagsn, 0.0)
    hp = p["prof"].get(tagsn, {}).get(ts.hour)

    if kind == "hold" or hp is None:
        return prev

    if kind == "di":
        duty = hp["p_nz"]
        # 상태 유지 우선 — 5% 확률로 시간대 가동률 기준 재추첨 (채터링 방지)
        state = prev if random.random() >= 0.05 else (1.0 if random.random() < duty else 0.0)
        return float(round(state))

    # Analog Input — 비영비율 게이트 + AR(1) 연속성 + 범위 클램프
    if random.random() >= max(hp["p_nz"], 0.0):
        return 0.0
    base = prev if prev != 0.0 else hp["mean"]
    v = 0.7 * base + 0.3 * hp["mean"] + random.gauss(0.0, hp["std"] * 0.4)
    v = min(max(v, hp["rmin"]), hp["rmax"])
    return round(v, 3)


def insert_rows(conn, ts: datetime, p: dict) -> int:
    rows = []
    for tagsn in p["state"].keys():
        v = gen_value(tagsn, ts, p)
        if v is None:
            continue
        p["state"][tagsn] = v
        rows.append((REGION, tagsn, ts, v, "GOOD!!"))
    if not rows:
        return 0
    cur = conn.cursor()
    execute_values(
        cur,
        """
        INSERT INTO tb_tag_raw_data (region, tagsn, logtime, val, tag_stat)
        VALUES %s
        ON CONFLICT (logtime, tagsn) DO NOTHING
        """,
        rows,
        page_size=5000,
    )
    inserted = cur.rowcount
    conn.commit()
    cur.close()
    return inserted


def backfill(conn, p: dict) -> None:
    cur = conn.cursor()
    cur.execute(
        "SELECT max(logtime) FROM tb_tag_raw_data WHERE logtime > now() - interval '30 days'"
    )
    last = cur.fetchone()[0]
    cur.close()
    now = datetime.now(timezone.utc)
    if last is None:
        log.warning("기존 데이터 없음 — 백필 건너뜀")
        return
    gap_h = (now - last).total_seconds() / 3600
    if gap_h < 0.1:
        log.info("공백 없음 — 백필 불필요")
        return
    start = max(last, now - timedelta(hours=BACKFILL_MAX_H))
    steps = int((now - start).total_seconds() // INTERVAL_S)
    log.info(f"백필 시작: {start} → 현재, {steps} 스텝 (공백 {gap_h:.1f}h)")
    ts = start
    total = 0
    for i in range(steps):
        if _shutdown:
            break
        ts = ts + timedelta(seconds=INTERVAL_S)
        total += insert_rows(conn, ts, p)
        if (i + 1) % 50 == 0:
            log.info(f"백필 진행 {i + 1}/{steps} · 누적 {total:,}행")
    log.info(f"백필 완료: {total:,}행")


def main() -> int:
    log.info(
        f"tag_synth 시작 — 주기 {INTERVAL_S}s · 프로파일 {PROFILE_DAYS}d · "
        f"백필 {'ON' if BACKFILL_STALL else 'OFF'}(≤{BACKFILL_MAX_H}h)"
    )
    conn = None
    profiles = None
    prof_built_at = 0.0
    last_cycle_ts: datetime | None = None
    last_inserted = 0

    while not _shutdown:
        try:
            if conn is None or conn.closed:
                conn = _connect()
            if profiles is None or time.monotonic() - prof_built_at > PROFILE_REFRESH_H * 3600:
                profiles = build_profiles(conn)
                prof_built_at = time.monotonic()
                if BACKFILL_STALL and last_cycle_ts is None:
                    backfill(conn, profiles)

            # 외부 수집 재개 가드 — 직전 주기 이후 신규 행이 자체 삽입량을
            # 크게 초과하면 진짜 수집이 돌아온 것: 이번 주기는 양보한다
            if last_cycle_ts is not None:
                cur = conn.cursor()
                cur.execute(
                    "SELECT COUNT(*) FROM tb_tag_raw_data WHERE logtime > %s",
                    (last_cycle_ts,),
                )
                new_rows = cur.fetchone()[0]
                cur.close()
                if new_rows > last_inserted + GUARD_MARGIN:
                    log.warning(
                        f"외부 수집 재개 감지 (신규 {new_rows:,} > 자체 {last_inserted:,}"
                        f"+{GUARD_MARGIN}) — 생성 양보"
                    )
                    last_cycle_ts = datetime.now(timezone.utc)
                    last_inserted = 0
                    time.sleep(INTERVAL_S)
                    continue

            now = datetime.now(timezone.utc).replace(microsecond=0)
            last_inserted = insert_rows(conn, now, profiles)
            last_cycle_ts = now
            log.info(f"생성 {last_inserted:,}행 @ {now.astimezone()}")
        except psycopg2.Error as e:
            log.warning(f"DB 작업 실패: {e} → 15s 후 재시도")
            try:
                if conn:
                    conn.close()
            except Exception:
                pass
            conn = None
            time.sleep(15)
            continue
        time.sleep(INTERVAL_S)

    log.info("종료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
