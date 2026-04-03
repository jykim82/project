"""
원격 DB → 로컬 DB 실시간 동기화 모듈 (개발 환경 전용)

[임시] 개발 완료 후 제거 예정
- ai_server.py lifespan에서 백그라운드 태스크로 실행
- 로컬 DB(localhost) 사용 시에만 활성화
- tb_tag_raw_data + tb_equipment_alarm_report 증분 동기화
"""

import asyncio
import logging
from datetime import datetime, timedelta

import psycopg2
import psycopg2.extras

logger = logging.getLogger("db_sync")

# ─── 원격 DB (동기화 소스) ──────────────────────────────────
REMOTE_DB = {
    "host": "112.166.183.65",
    "port": 25479,
    "dbname": "postgres",
    "user": "postgres",
    "password": "DJpost0827///",
}

# ─── 동기화 대상 테이블 ──────────────────────────────────────
SYNC_TABLES = [
    {
        "table": "tb_tag_raw_data",
        "time_column": "logtime",
        "pk_columns": ["logtime", "tagsn"],
        "all_columns": ["tagsn", "logtime", "val", "tag_stat"],
    },
    {
        "table": "tb_equipment_alarm_report",
        "time_column": "alarm_start_time",
        "pk_columns": ["alarm_start_time", "tagsn"],
        "all_columns": None,  # 자동 감지
    },
]


def _connect(db_config: dict, label: str, max_retries: int = 3):
    """DB 연결 + 재시도"""
    for attempt in range(1, max_retries + 1):
        try:
            conn = psycopg2.connect(**db_config)
            conn.autocommit = False
            return conn
        except psycopg2.OperationalError as e:
            logger.warning("[%s] 연결 실패 (%d/%d): %s", label, attempt, max_retries, e)
            if attempt == max_retries:
                raise
            import time
            time.sleep(3)


def _get_columns(conn, table: str) -> list[str]:
    """테이블 컬럼명 목록 조회"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
            (table,),
        )
        return [row[0] for row in cur.fetchall()]


def _sync_table(
    remote_conn,
    local_conn,
    config: dict,
    initial_days: int,
    batch_size: int,
    stop_flag: list,
) -> int:
    """단일 테이블 증분 동기화. 삽입된 행 수 반환."""
    table = config["table"]
    time_col = config["time_column"]
    columns = config["all_columns"]

    if columns is None:
        columns = _get_columns(remote_conn, table)
        config["all_columns"] = columns

    # 로컬 마지막 시각 조회
    with local_conn.cursor() as cur:
        cur.execute(f"SELECT MAX({time_col}) FROM {table}")  # noqa: S608
        last_synced = cur.fetchone()[0]

    if last_synced is None:
        cutoff = datetime.now() - timedelta(days=initial_days)
        logger.info("[%s] 초기 동기화: %s 이후", table, cutoff.strftime("%Y-%m-%d"))
    else:
        cutoff = last_synced
        logger.debug("[%s] 증분: %s 이후", table, cutoff)

    col_list = ", ".join(columns)
    remote_cur = remote_conn.cursor("sync_cur", withhold=True)
    remote_cur.itersize = batch_size
    remote_cur.execute(
        f"SELECT {col_list} FROM {table} "  # noqa: S608
        f"WHERE {time_col} > %s ORDER BY {time_col}",
        (cutoff,),
    )

    total = 0
    placeholders = ", ".join(["%s"] * len(columns))
    conflict_clause = ", ".join(config["pk_columns"])
    insert_sql = (
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
        f"ON CONFLICT ({conflict_clause}) DO NOTHING"
    )

    with local_conn.cursor() as local_cur:
        while not stop_flag[0]:
            rows = remote_cur.fetchmany(batch_size)
            if not rows:
                break
            psycopg2.extras.execute_batch(local_cur, insert_sql, rows, page_size=1000)
            local_conn.commit()
            total += len(rows)
            logger.info("[%s] +%d행 (누적 %d)", table, len(rows), total)

    remote_cur.close()
    return total


def _sync_cycle(local_db: dict, initial_days: int, batch_size: int, stop_flag: list) -> dict:
    """1회 동기화 사이클 (blocking). {table: inserted_count} 반환."""
    results = {}
    remote_conn = None
    local_conn = None
    try:
        remote_conn = _connect(REMOTE_DB, "REMOTE")
        local_conn = _connect(local_db, "LOCAL")

        for config in SYNC_TABLES:
            if stop_flag[0]:
                break
            inserted = _sync_table(
                remote_conn, local_conn, config,
                initial_days, batch_size, stop_flag,
            )
            results[config["table"]] = inserted

        # 연속집계 갱신 (cagg_5min_raw_stats_ai)
        if not stop_flag[0] and local_conn:
            try:
                local_conn.autocommit = True
                with local_conn.cursor() as cur:
                    cur.execute(
                        "CALL refresh_continuous_aggregate("
                        "'cagg_5min_raw_stats_ai', "
                        "(now() - interval '3 days')::timestamp, "
                        "now()::timestamp)"
                    )
                logger.info("연속집계 cagg_5min_raw_stats_ai 갱신 완료")
                local_conn.autocommit = False
            except Exception as e:
                logger.warning("연속집계 갱신 실패: %s", e)

    except psycopg2.OperationalError as e:
        logger.error("DB 연결 오류: %s", e)
    except Exception:
        logger.exception("동기화 오류")
    finally:
        for conn in (remote_conn, local_conn):
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    return results


class DbSyncWorker:
    """ai_server lifespan에서 사용하는 비동기 동기화 워커"""

    def __init__(
        self,
        local_db: dict,
        interval: int = 60,
        batch_size: int = 50000,
        initial_days: int = 30,
    ):
        self.local_db = local_db
        self.interval = interval
        self.batch_size = batch_size
        self.initial_days = initial_days
        self._stop_flag = [False]  # mutable ref for thread

    def stop(self):
        self._stop_flag[0] = True

    async def run_loop(self):
        """비동기 루프: 초기 지연 → 주기적 동기화"""
        logger.info(
            "DB 동기화 워커 시작 (원격 %s:%s → 로컬 %s:%s, 주기 %ds)",
            REMOTE_DB["host"], REMOTE_DB["port"],
            self.local_db["host"], self.local_db["port"],
            self.interval,
        )
        # 서버 초기화 완료 후 시작 (30초 대기)
        await asyncio.sleep(30)

        cycle = 0
        while not self._stop_flag[0]:
            cycle += 1
            results = await asyncio.to_thread(
                _sync_cycle,
                self.local_db,
                self.initial_days,
                self.batch_size,
                self._stop_flag,
            )

            for table, count in results.items():
                if count > 0:
                    logger.info("[sync#%d] %s: %d행 동기화", cycle, table, count)

            # interval 동안 1초 단위 대기 (빠른 종료 지원)
            for _ in range(self.interval):
                if self._stop_flag[0]:
                    break
                await asyncio.sleep(1)

        logger.info("DB 동기화 워커 종료")
