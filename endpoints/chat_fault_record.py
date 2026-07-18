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

# [policy] 통신·네트워크 계열 키워드 — 이 단어가 등장하면 기본 분류는 "이상".
# 알람만으로 실제 장애 확정 불가 (False Positive 가능성 높음).
# 단, 사용자가 "현장 확인"/"직접 확인" 같은 단서를 함께 기재하면 "고장" 유지.
# 사양: docs/fault-category-policy.md
COMM_NETWORK_PATTERN = re.compile(r"(통신|네트워크|LTE|모뎀|SIM)")
SITE_VERIFIED_PATTERN = re.compile(r"(현장\s*확인|직접\s*확인|현장\s*갔|현장\s*방문)")


# [P7] 교체 메타 자연어 추출 — "제조사 LS", "모델 XGB-XBCH", "S/N 12345"
_REPL_MANUF_PATTERN = re.compile(r"(?:제조사|제작사|제조|브랜드)\s*[:：]?\s*([A-Za-z가-힣0-9&.\- ]{1,40}?)(?=\s*(?:,|$|\s제품|\s모델|\s일련|\s시리얼|\sS/?N))", re.IGNORECASE)
_REPL_MODEL_PATTERN = re.compile(r"(?:모델|제품(?:명)?|품명)\s*[:：]?\s*([A-Za-z0-9가-힣\-_./]{2,60})", re.IGNORECASE)
_REPL_SERIAL_PATTERN = re.compile(r"(?:S/?N|일련번호|시리얼)\s*[:：]?\s*([A-Za-z0-9\-]{3,40})", re.IGNORECASE)


def _extract_replacement_info(text: str) -> dict:
    """자연어에서 교체 메타(제조사/모델/시리얼)를 간단 regex 로 추출.

    UI 에서 사용자가 직접 입력하는 경우가 대부분이므로 best-effort.
    누락 필드는 UI 에서 보완 입력받는 것이 원칙.
    """
    info: dict = {}
    if not text:
        return info
    m = _REPL_MANUF_PATTERN.search(text)
    if m:
        info["manufacturer"] = m.group(1).strip().rstrip(",. ")
    m = _REPL_MODEL_PATTERN.search(text)
    if m:
        info["model"] = m.group(1).strip().rstrip(",. ")
    m = _REPL_SERIAL_PATTERN.search(text)
    if m:
        info["serial"] = m.group(1).strip()
    return info

# [P6] 조치 완료 키워드 — FAULT 키워드와 동시 등장 시 RESOLVE 우선
RESOLVE_KEYWORDS = [
    "조치완료", "조치 완료", "조치했", "수리완료", "수리 완료", "수리했",
    "복구완료", "복구 완료", "해결완료", "해결 완료", "해결했",
    "완료했", "완료 했", "끝났", "끝냈",
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
        "inspection_type": None,
        "task_category_hint": None,  # '점검' 또는 '고장보고' (INSERT 분기용)
    }
    t = text.strip()

    # 0) inspection_type 우선 추출 (점검 sub-type)
    #    매칭 시 fault_category='점검', task_category_hint='점검' 으로 자동 셋업
    if re.search(r"(일상\s*점검|일점검)", t):
        result["inspection_type"] = "일상"
    elif re.search(r"(정기\s*점검|월간\s*점검|연간\s*점검|분기\s*점검)", t):
        result["inspection_type"] = "정기"
    elif re.search(r"(특별\s*점검|긴급\s*점검)", t):
        result["inspection_type"] = "특별"
    if result["inspection_type"]:
        result["fault_category"] = "점검"
        result["task_category_hint"] = "점검"

    # 1) fault_category (키워드 순서 중요, 복합어 먼저) — inspection_type 미매칭 시
    fault_kw_found: str | None = None
    if not result["fault_category"]:
        for kw, cat in FAULT_KEYWORDS:
            if kw in t:
                result["fault_category"] = cat
                fault_kw_found = kw
                break

    # [policy] 통신·네트워크 키워드 → 고장을 이상으로 강제
    # (현장 확인 단서가 있으면 사용자 명시 의도 존중해 "고장" 유지)
    if (
        result["fault_category"] == "고장"
        and COMM_NETWORK_PATTERN.search(t)
        and not SITE_VERIFIED_PATTERN.search(t)
    ):
        result["fault_category"] = "이상"

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
    replacement_info: Optional[dict] = Field(
        None, description="[P7] 교체 메타 (fault_category=교체 일 때) manufacturer/model/serial/..."
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
    replacement_info: Optional[dict] = None,
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

    # [P7] fault_category=교체 시 replacement_info 병합 (파라미터 우선, 자연어 추출 보완)
    merged_replacement: dict = {}
    if parsed.get("fault_category") == "교체":
        merged_replacement.update(_extract_replacement_info(text))  # 자연어 best-effort
    if replacement_info:
        merged_replacement.update({k: v for k, v in replacement_info.items() if v})  # 명시 입력 우선

    draft: dict[str, Any] = {
        **parsed,
        "equipment_id": equipment_id,
        "occurred_at": occurred_at.isoformat(),
        "original_text": text,
        "photo_urls": list(photo_urls or []),
        "replacement_info": merged_replacement or None,
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
        if draft.get("replacement_info"):
            ri = draft["replacement_info"]
            parts = []
            if ri.get("manufacturer"): parts.append(f"제조사 {ri['manufacturer']}")
            if ri.get("model"):        parts.append(f"모델 {ri['model']}")
            if ri.get("serial"):       parts.append(f"S/N {ri['serial']}")
            if parts:
                msg += f"\n• 교체정보: {' · '.join(parts)}"
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
            replacement_info=req.replacement_info,
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
        replacement_info = draft.get("replacement_info")
        replacement_json = json.dumps(replacement_info, ensure_ascii=False) if replacement_info else None

        # 점검 인텐트로 들어온 경우 task_category='점검' 으로 INSERT
        # (그 외는 기존대로 '고장보고')
        task_category_value = draft.get("task_category_hint") or "고장보고"
        inspection_type_value = draft.get("inspection_type")

        cur.execute(
            """
            INSERT INTO tb_task_master
              (sitename, facilitytype, task_category, task_start_time,
               equipment_id, equipmenttype, fault_category, severity,
               linked_alarm_start, linked_alarm_tagsn,
               task_content, recorded_by, status, photo_urls, replacement_info,
               inspection_type)
            VALUES (%s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s,
                    %s, %s, '진행중', %s::jsonb, %s::jsonb,
                    %s)
            RETURNING task_id
            """,
            (
                draft["sitename"], draft["facilitytype"], task_category_value, occurred_at,
                draft.get("equipment_id"), draft["equipmenttype"],
                draft["fault_category"], draft.get("severity"),
                first_alarm_start, first_alarm_tagsn,
                draft.get("original_text"), req.user_id,
                photo_urls_json, replacement_json,
                inspection_type_value,
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


class AppendDetailRequest(BaseModel):
    session_id: str
    user_id: str
    text: str


@router.post("/append-detail")
def append_detail(req: AppendDetailRequest) -> dict:
    """기존 pending draft 에 상세 내용을 덧붙임 — 확인 카드의 음성 덧붙이기
    (voice-input-spec §후속). original_text 에 append → confirm 시 task_content 로 저장.
    """
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(400, "덧붙일 내용이 비어 있습니다.")
    if len(text) > 1000:
        raise HTTPException(400, "내용이 너무 깁니다 (1000자 이내).")

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
            raise HTTPException(404, "대기 중인 장애 기록이 없습니다 (만료되었을 수 있음).")
        draft = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        base = (draft.get("original_text") or "").rstrip()
        draft["original_text"] = f"{base}\n[추가] {text}" if base else text
        cur.execute(
            "UPDATE tb_chat_pending_action SET draft = %s::jsonb WHERE session_id = %s",
            (json.dumps(draft, ensure_ascii=False, default=str), req.session_id),
        )
        conn.commit()
        cur.close()
        logger.info(f"fault append-detail: session={req.session_id} +{len(text)}자")
        return {"session_id": req.session_id, "original_text": draft["original_text"]}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        logger.exception("fault append-detail 실패")
        raise HTTPException(500, f"내용 추가 실패: {e}")
    finally:
        conn.close()


@router.post("/resolve/upload-photo")
async def resolve_upload_photo(
    user_id: str,
    images: list[UploadFile] = File(...),
):
    """조치 완료 사진 업로드 — 채팅/Dialog 인라인 업로드용.

    chat_attachments 에 저장하고 URL 배열 반환. 반환된 URL 을 /resolve/direct
    또는 /resolve/confirm 에 `resolution_photo_urls` 로 전달.
    """
    if not images:
        raise HTTPException(400, "최소 1장의 이미지가 필요합니다")
    if len(images) > 3:
        raise HTTPException(400, "이미지는 최대 3장까지 업로드 가능합니다")

    os.makedirs(CHAT_ATTACHMENT_DIR, exist_ok=True)
    urls: list[str] = []
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
        urls.append(f"/api/files/chat_attachments/{stored_name}")

    logger.info(f"resolve upload-photo: user={user_id} +{len(urls)}")
    return {"photo_urls": urls, "count": len(urls)}


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


# ============================================================================
# [P6] 조치 완료 보고 — 채팅 자연어 + 설비 건강성 Dialog 공유
# ============================================================================

class ResolveDraftRequest(BaseModel):
    user_id: str
    text: str = Field(..., description="자연어. 예: '신평 배수지 PLC 조치 완료했어'")


class ResolveDraftResponse(BaseModel):
    session_id: str
    candidate_task_id: Optional[int]    # 매칭된 진행중 task
    candidate_task: Optional[dict]      # 원본 task 정보
    parsed: dict                        # 파싱된 sitename/facilitytype/equipmenttype
    ready: bool
    confirm_message: str


class ResolveConfirmRequest(BaseModel):
    session_id: str
    user_id: str
    action: str = Field(..., description="yes | cancel")
    resolution_note: Optional[str] = None
    resolution_photo_urls: Optional[list[str]] = Field(
        None, description="[P8] 조치 완료 사진 URL (/api/files/chat_attachments/<name>)"
    )


class ResolveConfirmResponse(BaseModel):
    status: str                         # resolved | cancelled | error
    task_id: Optional[int] = None
    message: str


@router.post("/resolve/draft", response_model=ResolveDraftResponse)
def create_resolve_draft(req: ResolveDraftRequest):
    """자연어 → 대상 진행중 task 자동 탐색 → pending_action 저장."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        parsed = parse_fault_text(req.text, cur=cur)
        # 가장 최근 진행중 task 탐색 (해당 시설+설비 기준)
        sitename = parsed.get("sitename")
        facilitytype = parsed.get("facilitytype")
        equipmenttype = parsed.get("equipmenttype")
        candidate = None
        candidate_id: Optional[int] = None
        if sitename or facilitytype or equipmenttype:
            cur.execute(
                """
                SELECT task_id, sitename, facilitytype, equipmenttype,
                       fault_category, severity, task_start_time, task_content, recorded_by
                FROM tb_task_master
                WHERE task_category = '고장보고'
                  AND status = '진행중'
                  AND resolved_at IS NULL
                  AND (%s IS NULL OR sitename = %s)
                  AND (%s IS NULL OR facilitytype = %s)
                  AND (%s IS NULL OR equipmenttype = %s)
                ORDER BY task_start_time DESC
                LIMIT 1
                """,
                (sitename, sitename, facilitytype, facilitytype, equipmenttype, equipmenttype),
            )
            row = cur.fetchone()
            if row:
                cols = [d[0] for d in cur.description]
                candidate = dict(zip(cols, row))
                candidate_id = int(row[0])
                if candidate.get("task_start_time"):
                    candidate["task_start_time"] = candidate["task_start_time"].isoformat()

        session_id = uuid.uuid4().hex
        draft = {
            "parsed": parsed,
            "candidate_task_id": candidate_id,
            "candidate_task": candidate,
            "resolution_note": req.text,
        }
        cur.execute(
            """
            INSERT INTO tb_chat_pending_action (session_id, user_id, intent, draft)
            VALUES (%s, %s, %s, %s::jsonb)
            """,
            (session_id, req.user_id, "RESOLVE_FAULT_DRAFT",
             json.dumps(draft, ensure_ascii=False, default=str)),
        )
        conn.commit()
        cur.close()

        if candidate_id is None:
            if not (sitename or facilitytype or equipmenttype):
                msg = "어떤 설비의 조치 완료인지 시설/설비 정보를 말씀해 주세요. 예: '신평 배수지 PLC 조치 완료'"
            else:
                parts = " ".join(filter(None, [sitename, facilitytype, equipmenttype]))
                msg = f"{parts} 의 진행중 장애 기록이 없습니다. 먼저 고장 보고를 해 주세요."
        else:
            msg = (
                f"진행중 장애 #{candidate_id} 를 조치 완료 처리할까요?\n"
                f"• 시설: {candidate.get('sitename')} ({candidate.get('facilitytype')})\n"
                f"• 설비: {candidate.get('equipmenttype')}\n"
                f"• 분류: {candidate.get('fault_category')}\n"
                f"• 보고 시각: {candidate.get('task_start_time', '')[:16].replace('T', ' ')}\n"
                f"• 보고 내용: {candidate.get('task_content') or '(없음)'}"
            )

        return ResolveDraftResponse(
            session_id=session_id,
            candidate_task_id=candidate_id,
            candidate_task=candidate,
            parsed=parsed,
            ready=candidate_id is not None,
            confirm_message=msg,
        )
    except Exception as e:
        conn.rollback()
        logger.exception("resolve draft 실패")
        raise HTTPException(500, f"resolve draft 실패: {e}")
    finally:
        conn.close()


@router.post("/resolve/confirm", response_model=ResolveConfirmResponse)
def confirm_resolve(req: ResolveConfirmRequest):
    """조치 완료 확정 — tb_task_master UPDATE resolved_at/resolved_by/status."""
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
            raise HTTPException(404, "pending 세션 없음 또는 만료 (5분)")
        draft = row[0]
        task_id = draft.get("candidate_task_id")
        if req.action == "cancel":
            cur.execute("DELETE FROM tb_chat_pending_action WHERE session_id = %s", (req.session_id,))
            conn.commit()
            cur.close()
            return ResolveConfirmResponse(status="cancelled", message="조치 완료 처리를 취소했습니다.")
        if req.action != "yes":
            raise HTTPException(400, f"unknown action: {req.action}")
        if not task_id:
            raise HTTPException(400, "대상 task 없음")

        note = req.resolution_note or draft.get("resolution_note") or ""
        photos = req.resolution_photo_urls or draft.get("resolution_photo_urls") or []
        photos_json = json.dumps(photos, ensure_ascii=False) if photos else None
        cur.execute(
            """
            UPDATE tb_task_master
            SET resolved_at = NOW(), resolved_by = %s,
                resolution_note = COALESCE(NULLIF(resolution_note, ''), '') ||
                                  CASE WHEN COALESCE(resolution_note,'')='' THEN '' ELSE E'\n' END ||
                                  %s,
                resolution_photo_urls = %s::jsonb,
                status = '완료'
            WHERE task_id = %s
            """,
            (req.user_id, note, photos_json, task_id),
        )
        _synced = _sync_draft_report_items(cur, task_id)
        cur.execute("DELETE FROM tb_chat_pending_action WHERE session_id = %s", (req.session_id,))
        conn.commit()
        cur.close()
        logger.info(f"resolve 완료: task_id={task_id} user={req.user_id} photos={len(photos)}"
                    + (f" (초안 보고서 항목 {_synced}건 동기화)" if _synced else ""))
        return ResolveConfirmResponse(
            status="resolved", task_id=task_id,
            message=f"장애 #{task_id} 조치 완료로 기록했습니다." + (f" (사진 {len(photos)}장)" if photos else ""),
        )
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        logger.exception("resolve confirm 실패")
        raise HTTPException(500, f"resolve confirm 실패: {e}")
    finally:
        conn.close()


def _sync_draft_report_items(cur, task_id: int) -> int:
    """task 조치 완료 시 이 task 를 참조하는 '초안' 보고서 항목에 시각 역전파.

    [배경 2026-07-16] 보고서에 조치 내용(resolved_text)을 먼저 쓰고 task 를
    나중에 완료하면 항목의 resolved_at 이 영구 누락 → 목록 통계의 조치
    완료율 왜곡 (실측 17.6%, 시각만 없는 항목 14건). 확정(finalized) 보고서는
    불변 원칙에 따라 건드리지 않는다. resolved_text 는 비어 있을 때만 채움.
    """
    cur.execute(
        """
        UPDATE tb_report_item ri
        SET resolved_at = t.resolved_at,
            resolved_text = COALESCE(NULLIF(ri.resolved_text, ''), t.resolution_note)
        FROM tb_task_master t, tb_report r
        WHERE t.task_id = %s AND ri.task_id = t.task_id
          AND r.report_id = ri.report_id AND r.status = 'draft'
          AND ri.resolved_at IS NULL
        """,
        (task_id,),
    )
    return cur.rowcount


class DirectResolveRequest(BaseModel):
    task_id: int
    user_id: str
    resolution_note: str = ""
    resolution_photo_urls: Optional[list[str]] = None


@router.post("/resolve/direct", response_model=ResolveConfirmResponse)
def direct_resolve(req: DirectResolveRequest):
    """task_id 지정 직접 조치 완료 — 설비 건강성 Dialog 의 "조치 완료" 버튼용."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT status, resolved_at FROM tb_task_master WHERE task_id=%s",
            (req.task_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "task_id not found")
        if row[0] == "완료" and row[1] is not None:
            raise HTTPException(400, "이미 완료된 장애입니다")

        photos = req.resolution_photo_urls or []
        photos_json = json.dumps(photos, ensure_ascii=False) if photos else None
        cur.execute(
            """
            UPDATE tb_task_master
            SET resolved_at = NOW(), resolved_by = %s,
                resolution_note = COALESCE(NULLIF(resolution_note, ''), '') ||
                                  CASE WHEN COALESCE(resolution_note,'')='' THEN '' ELSE E'\n' END ||
                                  %s,
                resolution_photo_urls = %s::jsonb,
                status = '완료'
            WHERE task_id = %s
            """,
            (req.user_id, req.resolution_note or "(메모 없음)", photos_json, req.task_id),
        )
        _synced = _sync_draft_report_items(cur, req.task_id)
        if _synced:
            logger.info(f"direct resolve: 초안 보고서 항목 {_synced}건 동기화 (task {req.task_id})")
        conn.commit()
        cur.close()
        return ResolveConfirmResponse(
            status="resolved", task_id=req.task_id,
            message=f"장애 #{req.task_id} 조치 완료로 기록했습니다." + (f" (사진 {len(photos)}장)" if photos else ""),
        )
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        logger.exception("direct resolve 실패")
        raise HTTPException(500, f"direct resolve 실패: {e}")
    finally:
        conn.close()
