"""
채팅 기반 설비 장애 기록 엔드포인트

플로우:
  1) POST /chat/fault/draft — 사용자 자연어 → 파싱 → pending_action 저장 → 확인 카드
  2) POST /chat/fault/confirm — 사용자 승인 → tb_task_master INSERT + pending 삭제

설계: docs/flow-diagram-mode-spec.md (섹션 예정), migration 0045_task_master_fault_log
"""

import logging
import re
import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat/fault", tags=["chat-fault-record"])

_get_db_connection = None

# 허용 설비유형 (UI select와 일치)
ALLOWED_EQUIPMENT_TYPES = {"PLC", "가압펌프", "유량계", "밸브", "모뎀", "UPS", "센서", "전원"}

# 허용 분류
ALLOWED_FAULT_CATEGORIES = {"고장", "이상", "교체", "점검"}

# 허용 심각도
ALLOWED_SEVERITY = {"경고", "주의", "정보"}

# 키워드 매핑 (자연어 → fault_category)
FAULT_KEYWORDS = [
    ("고장", "고장"),
    ("이상", "이상"),
    ("오류", "이상"),
    ("교체", "교체"),
    ("점검", "점검"),
]

# 시설유형 키워드
FACILITY_KEYWORDS = ["배수지", "가압장", "감압시설", "감압설비", "정수장", "취수장", "소블록", "소소블록", "블록", "댐"]


def init(get_db_connection_fn):
    """main에서 DB 커넥션 함수 주입"""
    global _get_db_connection
    _get_db_connection = get_db_connection_fn


def _get_conn():
    if _get_db_connection is None:
        raise HTTPException(status_code=500, detail="DB 커넥션 미초기화")
    return _get_db_connection()


# ============================================================================
# 파싱 유틸
# ============================================================================

def parse_fault_text(text: str) -> dict[str, Any]:
    """자연어에서 시설/시설유형/설비유형/분류 추출.

    예: "신평 배수지 PLC 고장 기록해줘" →
        {sitename: "신평", facilitytype: "배수지", equipmenttype: "PLC", fault_category: "고장"}
    """
    result: dict[str, Any] = {
        "sitename": None,
        "facilitytype": None,
        "equipmenttype": None,
        "fault_category": None,
        "severity": None,
    }
    t = text.strip()

    # 1) fault_category (키워드 순서 중요)
    for kw, cat in FAULT_KEYWORDS:
        if kw in t:
            result["fault_category"] = cat
            break

    # 2) facilitytype
    for ft in FACILITY_KEYWORDS:
        if ft in t:
            result["facilitytype"] = ft
            break

    # 3) equipmenttype (대소문자 무시)
    t_upper = t.upper()
    for eq in ALLOWED_EQUIPMENT_TYPES:
        if eq.upper() in t_upper:
            result["equipmenttype"] = eq
            break

    # 4) sitename: facilitytype 앞에 오는 단어 추출 (2~15자)
    if result["facilitytype"]:
        pattern = r"([가-힣A-Za-z0-9]{2,15})\s*" + re.escape(result["facilitytype"])
        m = re.search(pattern, t)
        if m:
            result["sitename"] = m.group(1).strip()

    # 5) severity: "긴급"=경고, "주의"=주의
    if "긴급" in t or "심각" in t:
        result["severity"] = "경고"
    elif "주의" in t:
        result["severity"] = "주의"

    return result


def resolve_equipment_id(cur, sitename: str, facilitytype: str, equipmenttype: str) -> Optional[str]:
    """tb_equipment_info에서 matching equipment_id 조회 (fuzzy sitename)."""
    if not sitename or not facilitytype or not equipmenttype:
        return None
    cur.execute(
        """
        SELECT equipment_id FROM tb_equipment_info
        WHERE sitename ILIKE %s AND facilitytype = %s AND equipmenttype = %s
        LIMIT 1
        """,
        (f"%{sitename}%", facilitytype, equipmenttype),
    )
    row = cur.fetchone()
    return row[0] if row else None


# ============================================================================
# Request/Response 모델
# ============================================================================

class DraftRequest(BaseModel):
    user_id: str = Field(..., description="로그인 사용자 ID")
    text: str = Field(..., description="사용자 자연어 입력")
    occurred_at: Optional[str] = Field(None, description="발생시각 ISO 문자열, 없으면 현재")


class DraftResponse(BaseModel):
    session_id: str
    draft: dict[str, Any]
    ready: bool  # 필수 필드 모두 채워졌는지
    missing: list[str]
    confirm_message: str


class ConfirmRequest(BaseModel):
    session_id: str
    user_id: str
    action: str = Field(..., description="yes | modify | cancel")
    modifications: Optional[dict[str, Any]] = Field(None, description="action=modify 시 변경 필드")


class ConfirmResponse(BaseModel):
    status: str  # recorded | updated | cancelled | error
    task_id: Optional[int] = None
    message: str
    draft: Optional[dict[str, Any]] = None


# ============================================================================
# 엔드포인트
# ============================================================================

@router.post("/draft", response_model=DraftResponse)
def create_draft(req: DraftRequest):
    """자연어 파싱 → pending_action 저장 → 확인 응답."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        parsed = parse_fault_text(req.text)

        # 발생시각
        occurred_at = datetime.now()
        if req.occurred_at:
            try:
                occurred_at = datetime.fromisoformat(req.occurred_at.replace("Z", "+00:00"))
            except Exception:
                pass

        # equipment_id resolve
        equipment_id = None
        if parsed["sitename"] and parsed["facilitytype"] and parsed["equipmenttype"]:
            equipment_id = resolve_equipment_id(cur, parsed["sitename"], parsed["facilitytype"], parsed["equipmenttype"])

        draft: dict[str, Any] = {
            **parsed,
            "equipment_id": equipment_id,
            "occurred_at": occurred_at.isoformat(),
            "original_text": req.text,
        }

        # 필수 필드 체크
        required = ["sitename", "facilitytype", "equipmenttype", "fault_category"]
        missing = [k for k in required if not draft.get(k)]
        ready = not missing

        # pending_action INSERT
        session_id = uuid.uuid4().hex
        cur.execute(
            """
            INSERT INTO tb_chat_pending_action (session_id, user_id, intent, draft)
            VALUES (%s, %s, %s, %s::jsonb)
            """,
            (session_id, req.user_id, "FAULT_RECORD_DRAFT",
             __import__("json").dumps(draft, ensure_ascii=False, default=str)),
        )
        conn.commit()

        # 확인 메시지 구성
        if ready:
            msg = (
                f"장애 기록을 확인합니다:\n"
                f"• 시설: {draft['sitename']} ({draft['facilitytype']})\n"
                f"• 설비: {draft['equipmenttype']}\n"
                f"• 분류: {draft['fault_category']}\n"
                f"• 발생시각: {occurred_at.strftime('%Y-%m-%d %H:%M')}\n"
                f"이대로 기록할까요?"
            )
        else:
            msg = f"다음 정보가 부족합니다: {', '.join(missing)}. 추가 입력이 필요합니다."

        cur.close()
        return DraftResponse(
            session_id=session_id,
            draft=draft,
            ready=ready,
            missing=missing,
            confirm_message=msg,
        )
    except Exception as e:
        conn.rollback()
        logger.exception("fault draft 실패")
        raise HTTPException(status_code=500, detail=f"draft 처리 실패: {e}")
    finally:
        conn.close()


@router.post("/confirm", response_model=ConfirmResponse)
def confirm_draft(req: ConfirmRequest):
    """사용자 승인 → tb_task_master INSERT + pending 삭제."""
    conn = _get_conn()
    try:
        cur = conn.cursor()

        # 세션 조회 (TTL 체크)
        cur.execute(
            """
            SELECT draft FROM tb_chat_pending_action
            WHERE session_id = %s AND user_id = %s AND expires_at > now()
            """,
            (req.session_id, req.user_id),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="pending 세션 없음 또는 만료 (5분)")
        draft = row[0]

        # 수정 반영
        if req.action == "modify" and req.modifications:
            draft.update({k: v for k, v in req.modifications.items() if v is not None})
            cur.execute(
                "UPDATE tb_chat_pending_action SET draft = %s::jsonb WHERE session_id = %s",
                (__import__("json").dumps(draft, ensure_ascii=False, default=str), req.session_id),
            )
            conn.commit()
            cur.close()
            return ConfirmResponse(
                status="updated",
                message="초안이 업데이트되었습니다. 다시 확인해주세요.",
                draft=draft,
            )

        # 취소
        if req.action == "cancel":
            cur.execute("DELETE FROM tb_chat_pending_action WHERE session_id = %s", (req.session_id,))
            conn.commit()
            cur.close()
            return ConfirmResponse(status="cancelled", message="장애 기록이 취소되었습니다.")

        # 확정 (yes): INSERT tb_task_master
        if req.action != "yes":
            raise HTTPException(status_code=400, detail=f"unknown action: {req.action}")

        # 필수 필드 재검증
        required = ["sitename", "facilitytype", "equipmenttype", "fault_category"]
        missing = [k for k in required if not draft.get(k)]
        if missing:
            raise HTTPException(status_code=400, detail=f"필수 필드 누락: {missing}")

        occurred_at = draft.get("occurred_at")
        if isinstance(occurred_at, str):
            occurred_at = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))

        cur.execute(
            """
            INSERT INTO tb_task_master
              (sitename, facilitytype, task_category, task_start_time,
               equipment_id, equipmenttype, fault_category, severity,
               task_content, recorded_by, status)
            VALUES (%s, %s, '고장보고', %s,
                    %s, %s, %s, %s,
                    %s, %s, '진행중')
            RETURNING task_id
            """,
            (
                draft["sitename"], draft["facilitytype"], occurred_at,
                draft.get("equipment_id"), draft["equipmenttype"],
                draft["fault_category"], draft.get("severity"),
                draft.get("original_text"), req.user_id,
            ),
        )
        task_id = cur.fetchone()[0]

        # pending 삭제
        cur.execute("DELETE FROM tb_chat_pending_action WHERE session_id = %s", (req.session_id,))
        conn.commit()
        cur.close()

        logger.info(f"fault 기록 완료: task_id={task_id} user={req.user_id}")
        return ConfirmResponse(
            status="recorded",
            task_id=task_id,
            message=f"장애 기록 완료 (ID: {task_id}). /crisis/tasks에서 확인할 수 있습니다.",
        )

    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        logger.exception("fault confirm 실패")
        raise HTTPException(status_code=500, detail=f"confirm 처리 실패: {e}")
    finally:
        conn.close()


@router.delete("/cleanup-expired")
def cleanup_expired():
    """만료된 pending 삭제 (수동 호출 또는 cron)."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM tb_chat_pending_action WHERE expires_at <= now()")
        deleted = cur.rowcount
        conn.commit()
        cur.close()
        return {"deleted": deleted}
    finally:
        conn.close()
