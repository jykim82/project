"""
채팅 기반 설비 장애 기록 엔드포인트

플로우:
  1) POST /chat/fault/draft — 사용자 자연어 → 파싱 → pending_action 저장 → 확인 카드
  2) POST /chat/fault/confirm — 사용자 승인 → tb_task_master INSERT + pending 삭제

설계: docs/flow-diagram-mode-spec.md (섹션 예정), migration 0045_task_master_fault_log
"""

import json
import logging
import os
import re
import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat/fault", tags=["chat-fault-record"])

# chat_attachments 디렉터리 — vision_proxy와 동일 규칙
CHAT_ATTACHMENT_DIR = os.environ.get(
    "CHAT_ATTACHMENT_DIR",
    os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "web", "files", "chat_attachments"
    )),
)

_get_db_connection = None

# 허용 분류 (UI select와 일치)
ALLOWED_FAULT_CATEGORIES = {"고장", "이상", "교체", "점검"}

# 허용 심각도
ALLOWED_SEVERITY = {"경고", "주의", "정보"}

# 키워드 매핑 (자연어 → fault_category). 순서 중요 (긴 것 먼저)
FAULT_KEYWORDS = [
    ("고장", "고장"),
    ("전원이상", "이상"),
    ("통신이상", "이상"),
    ("교차검증이상", "이상"),
    ("이상", "이상"),
    ("오류", "이상"),
    ("교체", "교체"),
    ("점검", "점검"),
]

# 시설유형 키워드
FACILITY_KEYWORDS = ["배수지", "가압장", "감압시설", "감압설비", "정수장", "취수장", "소블록", "소소블록", "블록", "댐"]

# 설비유형 힌트 (자유 입력 허용, 이 목록은 우선 매칭)
COMMON_EQUIPMENT_HINTS = [
    "PLC", "가압펌프", "유량계", "밸브", "LTE 모뎀", "LTE모뎀", "모뎀",
    "UPS", "센서", "전원", "판넬", "판넬전원", "배전반", "분전반",
    "L2 스위치", "L3 스위치", "UTM", "서버",
]

_equipment_types_cache: list[str] | None = None


def _load_equipment_types(cur) -> list[str]:
    """DB에서 실제 운영 중인 equipmenttype 목록 (1분 캐시)."""
    global _equipment_types_cache
    if _equipment_types_cache is not None:
        return _equipment_types_cache
    try:
        cur.execute("SELECT DISTINCT equipmenttype FROM tb_equipment_info WHERE equipmenttype IS NOT NULL")
        _equipment_types_cache = [r[0] for r in cur.fetchall()]
    except Exception:
        _equipment_types_cache = []
    return _equipment_types_cache


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

def parse_fault_text(text: str, cur=None) -> dict[str, Any]:
    """자연어에서 시설/시설유형/설비유형/분류 추출 (자유 입력 허용).

    예1: "신평 배수지 PLC 고장 기록해줘" → PLC/고장
    예2: "신평 배수지 판넬 전원이상" → 판넬 또는 전원/이상
    예3: "신평 배수지 UPS 이상" → UPS/이상
    예4: "신평 배수지 가압펌프 고장" → 가압펌프/고장
    """
    result: dict[str, Any] = {
        "sitename": None,
        "facilitytype": None,
        "equipmenttype": None,
        "fault_category": None,
        "severity": None,
    }
    t = text.strip()

    # 1) fault_category (키워드 순서 중요, 복합어 먼저)
    fault_kw_found: str | None = None
    for kw, cat in FAULT_KEYWORDS:
        if kw in t:
            result["fault_category"] = cat
            fault_kw_found = kw
            break

    # 2) facilitytype
    for ft in FACILITY_KEYWORDS:
        if ft in t:
            result["facilitytype"] = ft
            break

    # 3) equipmenttype:
    #    (a) DB의 실제 equipmenttype 우선 매칭
    #    (b) COMMON_EQUIPMENT_HINTS 매칭
    #    (c) 실패 시 fault 키워드 앞의 명사 추출 (2~10자 한글/영문/숫자)
    t_upper = t.upper()

    # DB 로드 (cur 제공된 경우)
    db_types = _load_equipment_types(cur) if cur else []
    # 긴 것부터 매칭 (예: "LTE 모뎀" > "모뎀")
    for eq in sorted(db_types, key=lambda s: -len(s)):
        if eq and eq.upper() in t_upper:
            result["equipmenttype"] = eq
            break

    if not result["equipmenttype"]:
        for eq in sorted(COMMON_EQUIPMENT_HINTS, key=lambda s: -len(s)):
            if eq.upper() in t_upper:
                result["equipmenttype"] = eq
                break

    # (c) fallback: fault 키워드 앞 2~10자 명사 추출
    if not result["equipmenttype"] and fault_kw_found:
        pattern = r"([가-힣A-Za-z0-9][가-힣A-Za-z0-9\s]{0,8}?[가-힣A-Za-z0-9])\s*" + re.escape(fault_kw_found)
        m = re.search(pattern, t)
        if m:
            candidate = m.group(1).strip()
            # 시설유형이 포함돼 있으면 그 뒤 부분만
            if result["facilitytype"] and result["facilitytype"] in candidate:
                idx = candidate.rfind(result["facilitytype"]) + len(result["facilitytype"])
                candidate = candidate[idx:].strip()
            if candidate and len(candidate) >= 2:
                result["equipmenttype"] = candidate

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


def suggest_ongoing_alarms(
    cur,
    sitename: Optional[str],
    facilitytype: Optional[str],
    equipmenttype: Optional[str],
    limit: int = 10,
) -> list[dict[str, Any]]:
    """장애 기록과 연관된 진행 중 알람 추천.
    우선순위: (1) 동일 sitename+facilitytype+equipmenttype (2) 동일 sitename+facilitytype (3) 동일 sitename
    """
    if not sitename:
        return []
    # 관련도 점수: 3(전체일치) > 2(시설유형까지) > 1(시설만)
    cur.execute(
        """
        SELECT
          TO_CHAR(alarm_start_time, 'YYYY-MM-DD"T"HH24:MI:SS') AS alarm_start_time,
          tagsn,
          sitename, facilitytype, equipmenttype,
          alarm_category, alarm_msg, alarm_severity,
          CASE
            WHEN equipmenttype = %s THEN 3
            WHEN facilitytype = %s THEN 2
            ELSE 1
          END AS rel_score
        FROM tb_equipment_alarm_report
        WHERE alarm_end_time IS NULL
          AND sitename = %s
          AND alarm_start_time >= now() - interval '30 days'
        ORDER BY rel_score DESC, alarm_start_time DESC
        LIMIT %s
        """,
        (equipmenttype or "", facilitytype or "", sitename, limit),
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ============================================================================
# Request/Response 모델
# ============================================================================

class DraftRequest(BaseModel):
    user_id: str = Field(..., description="로그인 사용자 ID")
    text: str = Field(..., description="사용자 자연어 입력")
    occurred_at: Optional[str] = Field(None, description="발생시각 ISO 문자열, 없으면 현재")
    photo_urls: Optional[list[str]] = Field(
        None, description="첨부 사진 URL 배열 (ex: /api/files/chat_attachments/<uuid>.jpg)"
    )


class DraftResponse(BaseModel):
    session_id: str
    draft: dict[str, Any]
    ready: bool  # 필수 필드 모두 채워졌는지
    missing: list[str]
    confirm_message: str
    suggested_alarms: list[dict[str, Any]] = []  # 연관 진행중 알람 제안


class ConfirmRequest(BaseModel):
    session_id: str
    user_id: str
    action: str = Field(..., description="yes | modify | cancel")
    modifications: Optional[dict[str, Any]] = Field(None, description="action=modify 시 변경 필드")
    # 연계할 알람 키 리스트: "alarm_start_time|tagsn" 포맷
    selected_alarm_keys: list[str] = []
    # 선택된 알람을 해제할지 여부
    resolve_alarms: bool = True


class ConfirmResponse(BaseModel):
    status: str  # recorded | updated | cancelled | error
    task_id: Optional[int] = None
    message: str
    draft: Optional[dict[str, Any]] = None


# ============================================================================
# 엔드포인트
# ============================================================================

def build_fault_draft(
    cur,
    user_id: str,
    text: str,
    occurred_at_str: Optional[str] = None,
    photo_urls: Optional[list[str]] = None,
) -> dict[str, Any]:
    """핵심 로직 — 자연어 파싱 + pending_action 저장 + 확인 응답 dict 반환.

    /chat/fault/draft 엔드포인트와 vision_proxy(사진+FAULT 텍스트 분기)가 공유.
    반환 dict 는 DraftResponse 스키마와 동일 키셋.

    Note: 호출자가 conn.commit() 책임. cur는 이 함수 내에서 닫지 않음.
    """
    parsed = parse_fault_text(text, cur=cur)

    occurred_at = datetime.now()
    if occurred_at_str:
        try:
            occurred_at = datetime.fromisoformat(occurred_at_str.replace("Z", "+00:00"))
        except Exception:
            pass

    equipment_id = None
    if parsed["sitename"] and parsed["facilitytype"] and parsed["equipmenttype"]:
        equipment_id = resolve_equipment_id(
            cur, parsed["sitename"], parsed["facilitytype"], parsed["equipmenttype"],
        )

    draft: dict[str, Any] = {
        **parsed,
        "equipment_id": equipment_id,
        "occurred_at": occurred_at.isoformat(),
        "original_text": text,
        "photo_urls": list(photo_urls or []),
    }

    required = ["sitename", "facilitytype", "equipmenttype", "fault_category"]
    missing = [k for k in required if not draft.get(k)]
    ready = not missing

    suggested = suggest_ongoing_alarms(
        cur, draft.get("sitename"), draft.get("facilitytype"), draft.get("equipmenttype"),
    )

    draft_with_alarms = {**draft, "_suggested_alarms": suggested}
    session_id = uuid.uuid4().hex
    cur.execute(
        """
        INSERT INTO tb_chat_pending_action (session_id, user_id, intent, draft)
        VALUES (%s, %s, %s, %s::jsonb)
        """,
        (session_id, user_id, "FAULT_RECORD_DRAFT",
         json.dumps(draft_with_alarms, ensure_ascii=False, default=str)),
    )

    if ready:
        msg = (
            f"장애 기록을 확인합니다:\n"
            f"• 시설: {draft['sitename']} ({draft['facilitytype']})\n"
            f"• 설비: {draft['equipmenttype']}\n"
            f"• 분류: {draft['fault_category']}\n"
            f"• 발생시각: {occurred_at.strftime('%Y-%m-%d %H:%M')}"
        )
        if draft["photo_urls"]:
            msg += f"\n• 첨부사진: {len(draft['photo_urls'])}장"
        msg += "\n이대로 기록할까요?"
        if suggested:
            msg += f"\n\n※ 관련 진행중 알람 {len(suggested)}건 발견 — 함께 해제할지 선택할 수 있습니다."
    else:
        msg = f"다음 정보가 부족합니다: {', '.join(missing)}. 추가 입력이 필요합니다."

    return {
        "session_id": session_id,
        "draft": draft,
        "ready": ready,
        "missing": missing,
        "confirm_message": msg,
        "suggested_alarms": suggested,
    }


@router.post("/draft", response_model=DraftResponse)
def create_draft(req: DraftRequest):
    """자연어 파싱 → pending_action 저장 → 확인 응답."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        result = build_fault_draft(
            cur,
            user_id=req.user_id,
            text=req.text,
            occurred_at_str=req.occurred_at,
            photo_urls=req.photo_urls,
        )
        conn.commit()
        cur.close()
        return DraftResponse(**result)
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

        # 연계할 알람 파싱: "alarm_start_time|tagsn" → [(dt, tagsn), ...]
        linked_alarms: list[tuple[str, str]] = []
        for key in (req.selected_alarm_keys or []):
            parts = key.split("|", 1)
            if len(parts) == 2 and parts[0] and parts[1]:
                linked_alarms.append((parts[0], parts[1]))
        first_alarm_start = linked_alarms[0][0] if linked_alarms else None
        first_alarm_tagsn = linked_alarms[0][1] if linked_alarms else None

        photo_urls = draft.get("photo_urls") or []
        photo_urls_json = json.dumps(photo_urls, ensure_ascii=False) if photo_urls else None

        cur.execute(
            """
            INSERT INTO tb_task_master
              (sitename, facilitytype, task_category, task_start_time,
               equipment_id, equipmenttype, fault_category, severity,
               linked_alarm_start, linked_alarm_tagsn,
               task_content, recorded_by, status, photo_urls)
            VALUES (%s, %s, '고장보고', %s,
                    %s, %s, %s, %s,
                    %s, %s,
                    %s, %s, '진행중', %s::jsonb)
            RETURNING task_id
            """,
            (
                draft["sitename"], draft["facilitytype"], occurred_at,
                draft.get("equipment_id"), draft["equipmenttype"],
                draft["fault_category"], draft.get("severity"),
                first_alarm_start, first_alarm_tagsn,
                draft.get("original_text"), req.user_id,
                photo_urls_json,
            ),
        )
        task_id = cur.fetchone()[0]

        # 선택 알람 해제 (resolve_alarms=True일 때)
        alarm_resolved_cnt = 0
        if linked_alarms and req.resolve_alarms:
            for (start_t, tsn) in linked_alarms:
                try:
                    cur.execute(
                        """
                        UPDATE tb_equipment_alarm_report
                        SET alarm_end_time = now(),
                            user_cause_description = COALESCE(user_cause_description, '') ||
                              CASE WHEN COALESCE(user_cause_description, '') = '' THEN '' ELSE E'\n' END ||
                              '[장애기록 #' || %s::text || '] ' || %s
                        WHERE alarm_start_time = %s::timestamp AND tagsn = %s
                          AND alarm_end_time IS NULL
                        """,
                        (task_id, draft.get("original_text", ""), start_t, tsn),
                    )
                    alarm_resolved_cnt += cur.rowcount
                except Exception as e:
                    logger.warning(f"알람 해제 실패 ({start_t}|{tsn}): {e}")

        # pending 삭제
        cur.execute("DELETE FROM tb_chat_pending_action WHERE session_id = %s", (req.session_id,))
        conn.commit()
        cur.close()

        logger.info(f"fault 기록 완료: task_id={task_id} user={req.user_id} alarms_linked={len(linked_alarms)} resolved={alarm_resolved_cnt}")
        extra_msg = ""
        if linked_alarms:
            extra_msg = f" · 알람 {len(linked_alarms)}건 연계"
            if req.resolve_alarms and alarm_resolved_cnt > 0:
                extra_msg += f" ({alarm_resolved_cnt}건 자동 해제)"
        return ConfirmResponse(
            status="recorded",
            task_id=task_id,
            message=f"장애 기록 완료 (ID: {task_id}){extra_msg}. /crisis/tasks에서 확인할 수 있습니다.",
        )

    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        logger.exception("fault confirm 실패")
        raise HTTPException(status_code=500, detail=f"confirm 처리 실패: {e}")
    finally:
        conn.close()


@router.post("/attach-photo")
async def attach_photo(
    session_id: str,
    user_id: str,
    images: list[UploadFile] = File(...),
):
    """기존 pending_action draft에 사진을 추가 (시나리오 3 — 사용자가 장애 등록을
    먼저 요청하고 확인 카드에서 "사진 추가" 버튼을 누른 경우).
    """
    if not images:
        raise HTTPException(400, "최소 1장의 이미지가 필요합니다")
    if len(images) > 3:
        raise HTTPException(400, "이미지는 최대 3장까지 업로드 가능합니다")

    os.makedirs(CHAT_ATTACHMENT_DIR, exist_ok=True)
    new_urls: list[str] = []
    for upload in images:
        content = await upload.read()
        if not content:
            raise HTTPException(400, "빈 파일")
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(413, "이미지 크기는 10MB 이하여야 합니다")
        ext = os.path.splitext(upload.filename or "")[1].lower() or ".jpg"
        if ext not in (".jpg", ".jpeg", ".png", ".webp"):
            raise HTTPException(400, f"지원하지 않는 이미지 형식: {ext}")
        stored_name = f"{uuid.uuid4().hex}{ext}"
        with open(os.path.join(CHAT_ATTACHMENT_DIR, stored_name), "wb") as f:
            f.write(content)
        new_urls.append(f"/api/files/chat_attachments/{stored_name}")

    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT draft FROM tb_chat_pending_action
            WHERE session_id = %s AND user_id = %s AND expires_at > now()
            """,
            (session_id, user_id),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "pending 세션 없음 또는 만료")
        draft = row[0] or {}
        existing = draft.get("photo_urls") or []
        draft["photo_urls"] = existing + new_urls
        cur.execute(
            "UPDATE tb_chat_pending_action SET draft = %s::jsonb WHERE session_id = %s",
            (json.dumps(draft, ensure_ascii=False, default=str), session_id),
        )
        conn.commit()
        cur.close()
        logger.info(f"fault attach-photo: session={session_id} +{len(new_urls)} (total {len(draft['photo_urls'])})")
        return {
            "session_id": session_id,
            "photo_urls": draft["photo_urls"],
            "added": len(new_urls),
        }
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        logger.exception("fault attach-photo 실패")
        raise HTTPException(500, f"사진 첨부 실패: {e}")
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
