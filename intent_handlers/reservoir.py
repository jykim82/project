"""배수지 계열 인텐트 핸들러 — 2단계 2차 이관 (공급량/헌팅/수위원인/네트워크)."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from .anomaly import EvidencePackMixin
from .base import IntentContext, IntentHandler, intent_handler

logger = logging.getLogger(__name__)


@intent_handler
class ReservoirSupplyHandler(IntentHandler):
    """일별/월별 공급량 — 기본 날짜(30일/1년) + 유량적산 기반 조달."""
    intents = (
        "RESERVOIR_DAILY_SUPPLY_TABLE", "RESERVOIR_MONTHLY_SUPPLY_TABLE",
        "RESERVOIR_DAILY_SUPPLY_CHART", "RESERVOIR_MONTHLY_SUPPLY_CHART",
    )

    async def prepare(self, ctx: IntentContext) -> None:
        _is_monthly = "MONTHLY" in ctx.intent
        if not ctx.params.get("from_ts"):
            _days_back = 365 if _is_monthly else 30
            ctx.params["from_ts"] = (datetime.now() - timedelta(days=_days_back)).strftime("%Y-%m-%d")
        if not ctx.params.get("to_ts"):
            ctx.params["to_ts"] = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        logger.info(f"[SSE] SUPPLY defaults: intent={ctx.intent}, from={ctx.params['from_ts']}, to={ctx.params['to_ts']}")

    async def pre_sql(self, ctx: IntentContext) -> None:
        from sql_executor import _execute_reservoir_supply_query_with_conn

        _mode = "monthly" if "MONTHLY" in ctx.intent else "daily"
        _from_str = ctx.params.get("from_ts", "")
        _to_str = ctx.params.get("to_ts", "")
        try:
            _from_d = datetime.strptime(_from_str[:10], "%Y-%m-%d").date()
            _to_d = datetime.strptime(_to_str[:10], "%Y-%m-%d").date()
            _sup_rows, _sup_cols = await asyncio.to_thread(
                _execute_reservoir_supply_query_with_conn, _mode, _from_d, _to_d
            )
            if _sup_rows:
                ctx.rows = _sup_rows
                ctx.columns = _sup_cols
            ctx.params["total_count"] = str(len(_sup_rows))
        except Exception as e:
            logger.error(f"[SSE] RESERVOIR SUPPLY 쿼리 실패 ({ctx.intent}): {e}")


@intent_handler
class HuntingCheckHandler(IntentHandler):
    """수위계 헌팅 여부 — 3시간 방향전환 분석."""
    intents = ("RESERVOIR_LEVEL_HUNTING_CHECK",)

    async def pre_sql(self, ctx: IntentContext) -> None:
        from sql_executor import _execute_hunting_check

        _sn = ctx.params.get("sitename", "")
        try:
            _hunt_rows, _hunt_cols = await asyncio.to_thread(_execute_hunting_check, _sn)
            if _hunt_rows:
                ctx.rows = _hunt_rows
                ctx.columns = _hunt_cols
        except Exception as e:
            logger.error(f"[SSE] HUNTING_CHECK 쿼리 실패: {e}")


@intent_handler
class LevelCauseHandler(EvidencePackMixin, IntentHandler):
    """수위 변동 원인 분석 + 진단 근거 팩 (agent-loop-spec P2 — 원인 분석
    계열 확대. 상류 알람·지식 카드·조치 이력이 원인 후보의 근거가 된다)."""
    intents = ("RESERVOIR_LEVEL_CAUSE_ANALYSIS",)

    async def pre_sql(self, ctx: IntentContext) -> None:
        from sql_executor import _execute_level_cause_analysis

        _sn = ctx.params.get("sitename", "")
        try:
            _cause_rows, _cause_cols = await asyncio.to_thread(_execute_level_cause_analysis, _sn)
            if _cause_rows:
                ctx.rows = _cause_rows
                ctx.columns = _cause_cols
        except Exception as e:
            logger.error(f"[SSE] LEVEL_CAUSE_ANALYSIS 쿼리 실패: {e}")


_NETWORK_UPSTREAM_SQL = """
WITH latest AS (
    SELECT equipment_id, MAX(check_time) AS mt
    FROM tb_network_status
    GROUP BY equipment_id
),
current_status AS (
    SELECT ns.equipment_id, ns.is_alive
    FROM tb_network_status ns
    JOIN latest ON latest.equipment_id = ns.equipment_id AND latest.mt = ns.check_time
),
sslvpn_summary AS (
    SELECT
        ei_t.sitename || ' ' || ei_t.equipmenttype AS sslvpn_id,
        COUNT(*)                                                            AS total_lte,
        COUNT(*) FILTER (WHERE NOT COALESCE(cs_s.is_alive, false))         AS down_lte,
        bool_or(cs_t.is_alive)                                             AS sslvpn_alive,
        array_agg(ei_s.sitename ORDER BY ei_s.sitename)
            FILTER (WHERE NOT COALESCE(cs_s.is_alive, false))              AS down_sites
    FROM tb_network_link nl
    JOIN tb_equipment_info ei_s ON ei_s.equipment_id = nl.source_equipment_id
    JOIN tb_equipment_info ei_t ON ei_t.equipment_id = nl.target_equipment_id
    LEFT JOIN current_status cs_s ON cs_s.equipment_id = nl.source_equipment_id
    LEFT JOIN current_status cs_t ON cs_t.equipment_id = nl.target_equipment_id
    WHERE ei_s.equipmenttype = 'LTE 모뎀'
      AND ei_t.equipmenttype = 'SSLVPN'
    GROUP BY ei_t.sitename, ei_t.equipmenttype
),
utm_info AS (
    SELECT
        COUNT(*)                                                            AS total_utm,
        COUNT(*) FILTER (WHERE NOT COALESCE(cs.is_alive, true))            AS down_utm
    FROM tb_equipment_info ei
    LEFT JOIN current_status cs ON cs.equipment_id = ei.equipment_id
    WHERE ei.equipmenttype IN ('UTM', 'FA망 현대화사업소 UTM')
),
total_lte AS (
    SELECT
        COUNT(*)                                                            AS global_lte_total,
        COUNT(*) FILTER (WHERE NOT COALESCE(cs.is_alive, false))           AS global_lte_down
    FROM tb_equipment_info ei
    LEFT JOIN current_status cs ON cs.equipment_id = ei.equipment_id
    WHERE ei.equipmenttype = 'LTE 모뎀'
)
SELECT
    ss.sslvpn_id,
    ss.total_lte::int,
    ss.down_lte::int,
    ss.sslvpn_alive,
    ss.down_sites,
    ui.total_utm::int,
    ui.down_utm::int,
    tl.global_lte_total::int,
    tl.global_lte_down::int
FROM sslvpn_summary ss
CROSS JOIN utm_info ui
CROSS JOIN total_lte tl
ORDER BY ss.down_lte DESC, ss.sslvpn_id
"""


@intent_handler
class NetworkUpstreamHandler(IntentHandler):
    """SSLVPN/UTM 계층 통신이상 원인 분석 — 고정 SQL."""
    intents = ("NETWORK_UPSTREAM_FAULT_ANALYSIS",)

    async def pre_sql(self, ctx: IntentContext) -> None:
        ctx.sql = _NETWORK_UPSTREAM_SQL
