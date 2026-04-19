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
                if value is not None:
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

        표지만 있어도 긴 새 질문(예: "다른 시설의 유량 트렌드를 완전히 새로
        보여줘")은 상속하면 오답이므로 20자 상한 유지.

        예:
          "오늘 것도" (5자)            → (a) 상속
          "그럼 어제 유입량은?" (11자)   → (b) 상속
          "신평 배수지 압력 보여줘" (12자) → 표지 없음, 새 인텐트
        """
        if session.last_status != "OK":
            return False
        if not session.last_intent:
            return False

        q = question.strip()
        if not q:
            return False
        if len(q) < 10:
            return True
        if len(q) < 20 and _has_followup_signal(q):
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
