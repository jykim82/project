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
