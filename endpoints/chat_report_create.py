"""
endpoints/chat_report_create.py — 채팅에서 보고서 직접 생성

자연어 트리거 예시:
- "이번 주 장애 보고서 만들어줘"
- "지난 주 보고서 작성"
- "오늘 일 점검 보고서 만들어줘"

draft → confirm 패턴 (chat_fault_record 와 동일 흐름).
1. POST /chat/report/draft  — 후보 task 미리보기 + 기간/유형 추출 + session_id
2. POST /chat/report/confirm — 실제 보고서 생성 (POST /reports 내부 호출)
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat/report", tags=["chat-report"])

_get_db_connection = None


def init(get_db_connection_fn):
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


def _get_conn():
    if _get_db_connection is None:
        raise HTTPException(status_code=500, detail="DB 커넥션 미초기화")
    return _get_db_connection()


# ============================================================================
# 자연어 → period / report_type 파싱
# ============================================================================

_REPORT_KEYWORDS = re.compile(r"보고서.*(만들|작성|생성|뽑아|편집)|(만들|작성|생성).*보고서")
_INSPECTION_KEYWORDS = re.compile(r"(일\s*점검|점검\s*보고서|일상\s*점검|정기\s*점검|특별\s*점검)")
_FAULT_KEYWORDS = re.compile(r"(장애|고장|조치|이상)\s*보고서|보고서.*(장애|고장|조치)")


def is_report_create_intent(text: str) -> bool:
    """채팅 텍스트가 보고서 생성 요청인지 감지."""
    t = (text or "").strip()
    if not t:
        return False
    return bool(_REPORT_KEYWORDS.search(t))


def parse_report_request(text: str) -> dict[str, Any]:
    """자연어에서 report_type + 기간 + 보고일자 추출.

    반환:
      { report_type: 'fault_action'|'daily_inspection',
        period_label: '이번 주' 등 사람이 읽는 라벨,
        date_from: ISO date (이력 검색 시작),
        date_to:   ISO date (이력 검색 종료, exclusive),
        report_date: ISO date (보고일자 — 기본 오늘) }
    """
    t = (text or "").strip()
    today = date.today()

    # 1) report_type
    if _INSPECTION_KEYWORDS.search(t):
        report_type = "daily_inspection"
    else:
        report_type = "fault_action"  # 기본값

    # 2) 기간 추출
    if re.search(r"오늘|당일", t):
        df, dt, label = today, today + timedelta(days=1), "오늘"
    elif re.search(r"어제", t):
        d1 = today - timedelta(days=1)
        df, dt, label = d1, today, "어제"
    elif re.search(r"지난\s*주|저번\s*주|전주", t):
        # 이번 주 월요일
        this_mon = today - timedelta(days=today.weekday())
        last_mon = this_mon - timedelta(days=7)
        df, dt, label = last_mon, this_mon, "지난 주"
    elif re.search(r"이번\s*주|금주|일주일|7일", t):
        df = today - timedelta(days=7)
        dt = today + timedelta(days=1)
        label = "이번 주"
    elif re.search(r"지난\s*달|저번\s*달|전월", t):
        first_this = today.replace(day=1)
        last_month_last = first_this - timedelta(days=1)
        first_last = last_month_last.replace(day=1)
        df, dt, label = first_last, first_this, "지난 달"
    elif re.search(r"이번\s*달|이달|한\s*달|30일|월간", t):
        df = today - timedelta(days=30)
        dt = today + timedelta(days=1)
        label = "이번 달"
    else:
        # 기본: 최근 7일
        df = today - timedelta(days=7)
        dt = today + timedelta(days=1)
        label = "최근 7일"

    return {
        "report_type": report_type,
        "period_label": label,
        "date_from": df.isoformat(),
        "date_to": dt.isoformat(),
        "report_date": today.isoformat(),
    }


# ============================================================================
# Endpoints
# ============================================================================

class DraftRequest(BaseModel):
    user_id: str
    region: str = "R01"
    text: str


class ConfirmRequest(BaseModel):
    session_id: str
    user_id: str
    region: str = "R01"
    action: str  # "yes" / "cancel"


@router.post("/draft")
def draft(req: DraftRequest):
    """자연어 → 기간·유형 추출 + 후보 task 검색 + session 저장."""
    parsed = parse_report_request(req.text)
    category = "고장보고" if parsed["report_type"] == "fault_action" else "점검"

    conn = _get_conn()
    try:
        cur = conn.cursor()

        # 후보 task 검색 — 같은 region·기간·카테고리
        cur.execute(
            """
            SELECT t.task_id, t.sitename, t.facilitytype, t.equipmenttype,
                   t.fault_category, t.inspection_type, t.task_start_time,
                   LEFT(COALESCE(t.task_content,''), 80) AS preview
              FROM tb_task_master t
             WHERE t.task_category = %s
               AND t.task_start_time >= %s::date
               AND t.task_start_time < (%s::date + INTERVAL '1 day')
             ORDER BY t.task_start_time DESC
             LIMIT 50
            """,
            (category, parsed["date_from"], parsed["date_to"]),
        )
        cols = [d[0] for d in cur.description]
        candidates = [dict(zip(cols, r)) for r in cur.fetchall()]

        # session 저장
        session_id = uuid.uuid4().hex
        draft_payload = {
            "report_type":  parsed["report_type"],
            "period_label": parsed["period_label"],
            "report_date":  parsed["report_date"],
            "date_from":    parsed["date_from"],
            "date_to":      parsed["date_to"],
            "task_ids":     [c["task_id"] for c in candidates],
            "candidates_preview": [
                {
                    "task_id": c["task_id"],
                    "site": c["sitename"],
                    "facility": c["facilitytype"],
                    "equipment": c["equipmenttype"],
                    "category": c["fault_category"],
                    "inspection_type": c["inspection_type"],
                    "start_time": c["task_start_time"].isoformat() if c["task_start_time"] else None,
                    "preview": c["preview"],
                }
                for c in candidates[:10]  # 미리보기 카드는 최대 10건
            ],
        }
        import json as _json
        cur.execute(
            """
            INSERT INTO tb_chat_pending_action (session_id, user_id, intent, draft, expires_at)
            VALUES (%s, %s, 'REPORT_CREATE_DRAFT', %s::jsonb,
                    now() + interval '5 minutes')
            """,
            (session_id, req.user_id, _json.dumps(draft_payload, ensure_ascii=False, default=str)),
        )
        conn.commit()

        type_label = "장애 조치 보고서" if parsed["report_type"] == "fault_action" else "일 점검 보고서"
        confirm_msg = (
            f"📄 {type_label} 초안 — {parsed['period_label']} "
            f"({parsed['date_from']} ~ {parsed['date_to']})\n"
            f"후보 이력 {len(candidates)} 건 발견. 보고서를 만들까요?"
        )

        return {
            "session_id":    session_id,
            "intent":        "REPORT_CREATE_DRAFT",
            "report_type":   parsed["report_type"],
            "period_label":  parsed["period_label"],
            "report_date":   parsed["report_date"],
            "date_from":     parsed["date_from"],
            "date_to":       parsed["date_to"],
            "candidate_count": len(candidates),
            "candidates_preview": draft_payload["candidates_preview"],
            "confirm_message": confirm_msg,
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.exception(f"chat/report/draft 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@router.post("/confirm")
def confirm(req: ConfirmRequest):
    """draft 의 task_ids 로 실제 보고서 생성 (또는 취소)."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT draft FROM tb_chat_pending_action
             WHERE session_id = %s AND user_id = %s AND expires_at > now()
            """,
            (req.session_id, req.user_id),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="만료되었거나 존재하지 않는 세션")
        draft_payload = row[0]
        if isinstance(draft_payload, str):
            import json as _json
            draft_payload = _json.loads(draft_payload)

        # 세션 정리
        cur.execute(
            "DELETE FROM tb_chat_pending_action WHERE session_id = %s",
            (req.session_id,),
        )

        if req.action == "cancel":
            conn.commit()
            return {"status": "cancelled", "message": "보고서 생성이 취소되었습니다."}

        if req.action != "yes":
            raise HTTPException(status_code=400, detail="action 은 'yes' / 'cancel'")

        task_ids = draft_payload.get("task_ids") or []
        if not task_ids:
            raise HTTPException(
                status_code=400,
                detail=f"기간({draft_payload.get('period_label')}) 내 후보 이력이 없어 보고서를 생성할 수 없습니다.",
            )

        # 보고서 생성 — endpoints/reports.create_report 와 동일 SQL 흐름
        from endpoints.reports import create_report, CreateReportRequest
        new_report = create_report(CreateReportRequest(
            user_id=req.user_id,
            region=req.region,
            report_type=draft_payload["report_type"],
            report_date=draft_payload["report_date"],
            task_ids=task_ids,
        ))
        conn.commit()

        type_label = "장애 조치 보고서" if draft_payload["report_type"] == "fault_action" else "일 점검 보고서"
        msg = (
            f"✅ {type_label} 생성 완료 — "
            f"{draft_payload['period_label']} / 항목 {len(task_ids)}건\n"
            f"보고서 #{new_report['report_id']}"
        )
        return {
            "status":      "created",
            "report_id":   new_report["report_id"],
            "report_type": new_report["report_type"],
            "title":       new_report["title"],
            "item_count":  len(new_report.get("items", [])),
            "url":         f"/reports/{ 'fault-action' if new_report['report_type'] == 'fault_action' else 'daily-inspection' }/{new_report['report_id']}",
            "message":     msg,
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.exception(f"chat/report/confirm 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()
