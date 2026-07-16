"""
session_manager.py
In-memory 세션 관리자

- UUID4 기반 세션 ID
- TTL 기반 만료 정리
- 턴 간 파라미터 누적/병합
- 정정 턴 단축 로직
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from slm_config import SESSION_TTL, SESSION_MAX_TURNS

logger = logging.getLogger(__name__)

# [review-items §멀티턴 (a)] 이어말 표지 — 이전 인텐트를 이어가는 자연어 신호.
# 앞 턴 결과를 기반으로 조건(시간/대상/값)만 바꾸는 표현.
#
# "도" 조사("압력도", "시설도")는 의미상 강한 신호지만, 수도 도메인 용어
# ("상수도", "수도", "도수관")와 형태가 겹쳐 오탐 위험이 크기 때문에 제외한다.
# 짧은 follow-up 은 10자 미만 규칙으로 대부분 커버되므로 본 마커는 중간 길이
# (10~19자) follow-up 만 보조 분류한다.
FOLLOWUP_MARKERS = (
    "그럼", "그러면", "같은", "다른",
    "어제", "오늘", "내일", "이번", "지난", "최근",
)


def _has_followup_signal(q: str) -> bool:
    return any(m in q for m in FOLLOWUP_MARKERS)


@dataclass
class SessionState:
    session_id: str
    created_at: float
    last_active: float
    turn_count: int = 0
    last_intent: Optional[str] = None
    accumulated_params: dict = field(default_factory=dict)
    last_status: str = "OK"  # "OK" | "NEED_CORRECTION" | "ERROR"
    pending_corrections: list = field(default_factory=list)


class SessionManager:
    def __init__(self):
        self._sessions: dict = {}  # session_id → SessionState

    def get_or_create(self, session_id: Optional[str] = None) -> SessionState:
        """
        세션을 로드하거나 새로 생성한다.
        - session_id가 None이면 새 세션 생성
        - session_id가 유효하지 않거나 만료되었으면 새 세션 생성
        """
        if session_id and session_id in self._sessions:
            session = self._sessions[session_id]
            # TTL 확인
            if time.time() - session.last_active > SESSION_TTL:
                logger.info(f"세션 만료: {session_id}")
                del self._sessions[session_id]
            else:
                session.last_active = time.time()
                return session

        # 새 세션 생성
        new_id = str(uuid.uuid4())
        now = time.time()
        session = SessionState(
            session_id=new_id,
            created_at=now,
            last_active=now,
        )
        self._sessions[new_id] = session
        logger.info(f"새 세션 생성: {new_id}")
        return session

    def update_session(
        self,
        session: SessionState,
        intent_name: Optional[str] = None,
        params: Optional[dict] = None,
        status: str = "OK",
        pending_corrections: Optional[list] = None,
    ) -> None:
        """
        세션 상태를 업데이트한다.
        - 파라미터 병합: 새 턴에서 non-None 값이 기존 값을 덮어씀
        - None은 기존 값 유지 (삭제 안함)
        """
        session.turn_count += 1
        session.last_active = time.time()

        if intent_name:
            session.last_intent = intent_name

        if params:
            for key, value in params.items():
                # '_' 접두 키는 해당 턴의 파생 정보(오타 보정 이력 등) — 누적 시
                # 다음 턴 답변에 이전 턴 보정 안내가 재표출되는 오류 (2026-07-16)
                if value is not None and not key.startswith("_"):
                    session.accumulated_params[key] = value

        session.last_status = status
        session.pending_corrections = pending_corrections or []

    def get_merged_params(self, session: SessionState, new_params: dict) -> dict:
        """
        세션 누적 파라미터와 새 파라미터를 병합한다.
        새 턴의 non-None 값이 우선.
        """
        merged = dict(session.accumulated_params)
        for key, value in new_params.items():
            if value is not None:
                merged[key] = value
        return merged

    def is_correction_turn(self, session: SessionState, question: str) -> bool:
        """
        정정 턴 단축 조건을 확인한다.
        - 이전 상태가 NEED_CORRECTION
        - pending_corrections가 있음
        - 입력이 짧음 (10자 미만)
        """
        if session.last_status != "NEED_CORRECTION":
            return False
        if not session.pending_corrections:
            return False
        if len(question.strip()) >= 10:
            return False
        return True

    def is_short_followup(self, session: SessionState, question: str) -> bool:
        """
        [review-items §멀티턴 (a)] 성공 턴 직후의 follow-up 을 직전
        인텐트로 상속하기 위한 조건.

        조건 (OR):
        - (a) 매우 짧음: 10자 미만
        - (b) 이어말 표지 포함 + 20자 미만
        단, (a)(b) 모두 "지표 명사"(유량/수위/압력/유출량/총유량 등)를 스스로
        명시하지 않은 경우에만 상속한다. 지표를 새로 말한 질문은 지표 자체가
        바뀐 자기완결형 신규 질의이므로 상속하면 다른 지표의 답을 재사용하는
        오답이 된다.

        표지만 있어도 긴 새 질문(예: "다른 시설의 유량 트렌드를 완전히 새로
        보여줘")은 상속하면 오답이므로 20자 상한 유지.

        예:
          "오늘 것도" (5자)            → (a) 상속 (지표 없음)
          "가압장은?" (5자)            → (a) 상속 (대상만 전환, 지표 없음)
          "배수지 총유량은?" (9자)      → 지표(총유량) 명시 → 상속 거부, 재분류
          "그럼 어제 것도?" (8자)       → (a) 상속
          "신평 배수지 압력 보여줘" (12자) → 표지 없음, 새 인텐트
        """
        if session.last_status != "OK":
            return False
        if not session.last_intent:
            return False

        q = question.strip()
        if not q:
            return False

        # 새 질문 자체에 강한 트리거 키워드가 있으면 follow-up 거부 → 신규 분류
        # (예: "비상연락처 알려줘" 9자 — 직전 EMERGENCY_CONTACT_UPS 상속 방지)
        # 사양: docs/emergency-contact-spec.md
        _strong_new_triggers = (
            "연락처", "비상연락", "비상 연락", "긴급 연락", "긴급연락", "연락망",
        )
        if any(t in q for t in _strong_new_triggers):
            return False

        # 짧아도 "지표 명사"를 스스로 명시한 질문은 자기완결형 신규 질의 →
        # 상속 금지. 진짜 follow-up 조각("오늘 것도", "그럼 어제는?", "가압장은?")은
        # 시간/대상만 바꾸고 지표를 새로 말하지 않는다. 반면 "배수지 총유량은?"(9자),
        # "현재 수위는?"(7자) 은 지표 자체가 바뀌므로 직전 인텐트를 물려받으면
        # 다른 지표의 답을 재사용하는 오답이 된다.
        # (예: TODAY_OUTFLOW_ALL_STATUS 직후 "배수지 총유량은?" →
        #      TODAY_FLOW_ACCUMULATION 로 재분류되어야 함)
        _standalone_metric_nouns = (
            "유출량", "유입량", "총유량", "적산", "순시", "유량",
            "수위", "압력", "공급량", "개도", "잔류염소", "탁도",
            "유속", "전력량", "전류", "온도",
        )
        _has_own_metric = any(m in q for m in _standalone_metric_nouns)

        if len(q) < 10 and not _has_own_metric:
            return True
        if len(q) < 20 and _has_followup_signal(q) and not _has_own_metric:
            return True
        return False

    def is_max_turns(self, session: SessionState) -> bool:
        """최대 턴 수 초과 여부를 확인한다."""
        return session.turn_count >= SESSION_MAX_TURNS

    def cleanup_expired(self) -> int:
        """
        만료된 세션을 제거한다.
        반환: 제거된 세션 수
        """
        now = time.time()
        expired = [
            sid
            for sid, s in self._sessions.items()
            if now - s.last_active > SESSION_TTL
        ]
        for sid in expired:
            del self._sessions[sid]
        if expired:
            logger.info(f"만료 세션 {len(expired)}개 제거")
        return len(expired)

    def active_session_count(self) -> int:
        """활성 세션 수를 반환한다."""
        return len(self._sessions)
