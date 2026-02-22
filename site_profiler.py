"""
현장 프로파일링 모듈 — 일 1회 배치로 A/B/C/D 그룹 분류

유출유량(고/저) × 알람빈도(빈번/희소) 매트릭스:
  A: 고부하-불안정 (Z ±4σ, IF 0.03)
  B: 고부하-안정   (Z ±3σ, IF 0.05) — 기본
  C: 저부하-불안정 (Z ±2σ, IF 0.08) — 최고 위험
  D: 저부하-안정   (Z ±3σ, IF 0.05)
"""

import logging
import statistics
from datetime import datetime
from typing import Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 분류 기준 상수
# ---------------------------------------------------------------------------
ALARM_FREQ_THRESHOLD = 10   # 30일 알람 >= 10건 → "빈번"
MIN_DATA_DAYS = 14          # 최소 14일 데이터 필요
PERCENTILE_MIN_SAMPLES = 200  # P95/P05 계산 최소 샘플 수


class SiteProfiler:
    """현장 프로파일 관리 — 일 1회 갱신, 인메모리 캐시 + DB 저장."""

    def __init__(self, get_connection_fn: Callable):
        self.get_conn = get_connection_fn
        # (sitename, facilitytype) → profile dict
        self.profiles: dict[tuple[str, str], dict] = {}
        self.last_run: datetime | None = None

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------

    def get_site_group(self, sitename: str, facilitytype: str) -> str:
        """캐시에서 그룹 반환. 미분류 시 'B' (기본)."""
        profile = self.profiles.get((sitename, facilitytype))
        return profile["site_group"] if profile else "B"

    def get_profile(self, sitename: str, facilitytype: str) -> dict | None:
        return self.profiles.get((sitename, facilitytype))

    def get_group_summary(self) -> dict[str, int]:
        """A/B/C/D 그룹별 건수."""
        counts: dict[str, int] = {"A": 0, "B": 0, "C": 0, "D": 0}
        for p in self.profiles.values():
            g = p.get("site_group", "B")
            counts[g] = counts.get(g, 0) + 1
        return counts

    def run_daily_profiling(self) -> None:
        """일 1회 프로파일링 오케스트레이터."""
        logger.info("=== 현장 프로파일링 시작 ===")
        try:
            outflow = self._compute_outflow_stats()
            alarms = self._compute_alarm_frequency()
            percentiles = self._compute_percentile_thresholds()
            profiles = self._classify_sites(outflow, alarms, percentiles)
            self._save_profiles(profiles)
            self.profiles = profiles
            self.last_run = datetime.now()
            summary = self.get_group_summary()
            logger.info(
                "프로파일링 완료: %d개 현장 — A:%d B:%d C:%d D:%d",
                len(profiles), summary["A"], summary["B"],
                summary["C"], summary["D"],
            )
        except Exception:
            logger.exception("프로파일링 실패")
            raise

    def load_from_db(self) -> None:
        """서버 시작 시 DB에서 기존 프로파일 로드."""
        try:
            conn = self.get_conn()
            cur = conn.cursor()
            cur.execute("""
                SELECT sitename, facilitytype, avg_outflow_7d, alarm_freq_30d,
                       p95_level, p05_level, site_group, outflow_rank,
                       alarm_rank, info_count_7d, updated_at
                FROM tb_site_anomaly_profile
            """)
            rows = cur.fetchall()
            cur.close()
            conn.close()
            for r in rows:
                key = (r[0], r[1])
                self.profiles[key] = {
                    "sitename": r[0],
                    "facilitytype": r[1],
                    "avg_outflow_7d": r[2],
                    "alarm_freq_30d": r[3],
                    "p95_level": r[4],
                    "p05_level": r[5],
                    "site_group": r[6],
                    "outflow_rank": r[7],
                    "alarm_rank": r[8],
                    "info_count_7d": r[9],
                    "updated_at": r[10],
                }
            if rows:
                logger.info("DB에서 %d개 현장 프로파일 로드", len(rows))
        except Exception:
            logger.warning("기존 프로파일 로드 실패 (첫 실행이면 정상)")

    # ------------------------------------------------------------------
    # 내부 SQL 쿼리
    # ------------------------------------------------------------------

    def _compute_outflow_stats(self) -> dict[tuple[str, str], float]:
        """7일 평균 유출유량 / 현장."""
        conn = self.get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT ti.sitename, ti.facilitytype,
                   AVG((c.min_val + c.max_val) / 2.0) AS avg_outflow_7d
            FROM cagg_5min_raw_stats_ai c
            JOIN tb_tag_info ti ON c.tagsn = ti.tagsn
            WHERE ti.datainfo LIKE '%%유출유량%%'
              AND ti.tagtype = 'Analog Input'
              AND c.bucket >= now() - interval '7 days'
              AND (c.min_val + c.max_val) / 2.0 > 0.001
            GROUP BY ti.sitename, ti.facilitytype
        """)
        result = {}
        for sitename, ft, avg_val in cur.fetchall():
            result[(sitename, ft)] = float(avg_val) if avg_val else 0.0
        cur.close()
        conn.close()
        logger.info("유출유량 통계: %d개 현장", len(result))
        return result

    def _compute_alarm_frequency(self) -> dict[str, int]:
        """30일 알람 발생 빈도 / 현장 (sitename 기준)."""
        conn = self.get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT sitename, COUNT(*) AS cnt
            FROM tb_equipment_alarm_report
            WHERE alarm_start_time >= now() - interval '30 days'
              AND sitename IS NOT NULL
              AND sitename != ''
            GROUP BY sitename
        """)
        result = {}
        for sitename, cnt in cur.fetchall():
            result[sitename] = int(cnt)
        cur.close()
        conn.close()
        logger.info("알람 빈도: %d개 현장", len(result))
        return result

    def _compute_percentile_thresholds(
        self,
    ) -> dict[tuple[str, str], tuple[float, float]]:
        """수위 태그 P95/P05 (동적 HH/LL 대용)."""
        conn = self.get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT ti.sitename, ti.facilitytype,
                   PERCENTILE_CONT(0.95)
                       WITHIN GROUP (ORDER BY (c.min_val + c.max_val) / 2.0) AS p95,
                   PERCENTILE_CONT(0.05)
                       WITHIN GROUP (ORDER BY (c.min_val + c.max_val) / 2.0) AS p05,
                   COUNT(*) AS sample_count
            FROM cagg_5min_raw_stats_ai c
            JOIN tb_tag_info ti ON c.tagsn = ti.tagsn
            WHERE ti.datainfo LIKE '%%수위%%'
              AND ti.tagtype = 'Analog Input'
              AND c.bucket >= now() - interval '30 days'
              AND (c.min_val + c.max_val) / 2.0 > 0.001
            GROUP BY ti.sitename, ti.facilitytype
            HAVING COUNT(*) >= %s
        """, (PERCENTILE_MIN_SAMPLES,))
        result = {}
        for sitename, ft, p95, p05, _ in cur.fetchall():
            result[(sitename, ft)] = (
                float(p95) if p95 else None,
                float(p05) if p05 else None,
            )
        cur.close()
        conn.close()
        logger.info("수위 P95/P05: %d개 현장", len(result))
        return result

    # ------------------------------------------------------------------
    # 분류 로직
    # ------------------------------------------------------------------

    def _classify_sites(
        self,
        outflow: dict[tuple[str, str], float],
        alarms: dict[str, int],
        percentiles: dict[tuple[str, str], tuple[float | None, float | None]],
    ) -> dict[tuple[str, str], dict]:
        """A/B/C/D 그룹 분류."""
        # 모든 현장 키 수집
        all_keys: set[tuple[str, str]] = set()
        all_keys.update(outflow.keys())
        all_keys.update(percentiles.keys())
        # 알람은 sitename만 → (sitename, ft) 키와 결합
        for key in list(all_keys):
            if key[0] in alarms:
                all_keys.add(key)

        # 유출유량 중앙값
        outflow_values = [v for v in outflow.values() if v > 0]
        outflow_median = (
            statistics.median(outflow_values) if outflow_values else 0.0
        )
        logger.info("유출유량 중앙값: %.2f", outflow_median)

        profiles: dict[tuple[str, str], dict] = {}
        for key in all_keys:
            sitename, ft = key
            avg_out = outflow.get(key, 0.0)
            alarm_count = alarms.get(sitename, 0)
            pct = percentiles.get(key, (None, None))

            is_high_load = avg_out >= outflow_median if outflow_median > 0 else True
            is_frequent = alarm_count >= ALARM_FREQ_THRESHOLD

            if is_high_load and is_frequent:
                group = "A"
            elif is_high_load and not is_frequent:
                group = "B"
            elif not is_high_load and is_frequent:
                group = "C"
            else:
                group = "D"

            profiles[key] = {
                "sitename": sitename,
                "facilitytype": ft,
                "avg_outflow_7d": avg_out,
                "alarm_freq_30d": alarm_count,
                "p95_level": pct[0],
                "p05_level": pct[1],
                "site_group": group,
                "outflow_rank": "고부하" if is_high_load else "저부하",
                "alarm_rank": "빈번" if is_frequent else "희소",
                "info_count_7d": 0,
            }

        return profiles

    # ------------------------------------------------------------------
    # DB 저장
    # ------------------------------------------------------------------

    def _save_profiles(self, profiles: dict[tuple[str, str], dict]) -> None:
        """tb_site_anomaly_profile에 UPSERT."""
        if not profiles:
            return
        conn = self.get_conn()
        cur = conn.cursor()
        sql = """
            INSERT INTO tb_site_anomaly_profile
                (sitename, facilitytype, avg_outflow_7d, alarm_freq_30d,
                 p95_level, p05_level, site_group, outflow_rank,
                 alarm_rank, info_count_7d, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (sitename, facilitytype) DO UPDATE SET
                avg_outflow_7d = EXCLUDED.avg_outflow_7d,
                alarm_freq_30d = EXCLUDED.alarm_freq_30d,
                p95_level      = EXCLUDED.p95_level,
                p05_level      = EXCLUDED.p05_level,
                site_group     = EXCLUDED.site_group,
                outflow_rank   = EXCLUDED.outflow_rank,
                alarm_rank     = EXCLUDED.alarm_rank,
                info_count_7d  = EXCLUDED.info_count_7d,
                updated_at     = now()
        """
        rows = []
        for p in profiles.values():
            rows.append((
                p["sitename"], p["facilitytype"],
                p["avg_outflow_7d"], p["alarm_freq_30d"],
                p["p95_level"], p["p05_level"],
                p["site_group"], p["outflow_rank"],
                p["alarm_rank"], p["info_count_7d"],
            ))
        cur.executemany(sql, rows)
        conn.commit()
        cur.close()
        conn.close()
        logger.info("프로파일 %d건 저장 완료", len(rows))
