"""인텐트 핸들러 프레임 — 아키텍처 2단계 (docs/intent-architecture-spec.md).

SSE event_generator 의 인텐트별 인라인 분기를 훅 클래스로 점진 이관한다.
새 인텐트에 커스텀 처리가 필요하면 example3.json 선언 + 핸들러 파일 1개.

훅 계약:
- pre_sql(ctx)         — SQL 실행 전. ctx.sql/params 변형, ctx.rows/columns 를
                         채우면 파이프라인이 SQL 실행을 건너뛴다(기존
                         'if not rows' 게이트와 동일 시맨틱).
- response_extras(ctx, processed_data)
                       — build_success_response 에 병합할 추가 필드 dict.

ai_server 전역(재할당되는 캐시·인덱스)은 값이 아니라 **getter 로 주입**
(init_services) — import 시점 스냅숏이 아닌 항상 현재 값을 읽는다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── 서비스 주입 (ai_server 가 기동 시 등록) ────────────────────────────
_SERVICES: dict = {}


def init_services(**services) -> None:
    """ai_server 전역 접근자 등록. 예: get_causal_index=lambda: _CAUSAL_INDEX"""
    _SERVICES.update(services)


def service(name: str):
    """등록된 서비스 반환 (미등록 시 None)."""
    return _SERVICES.get(name)


# ── 컨텍스트 ───────────────────────────────────────────────────────────
@dataclass
class IntentContext:
    """훅에 전달되는 요청 컨텍스트. sql/params/rows 변형이 파이프라인에 반영된다."""
    intent: str
    question: str                    # 정규화된 질의
    params: dict                     # prepare 훅에선 new_params(병합 전), 이후 병합 params
    sql: str
    session_id: str = ""
    raw_question: str = ""           # 원본 사용자 질의 (정규화 전)
    rows: Optional[list] = None      # 채우면 SQL 실행 대체
    columns: Optional[list] = None
    answer_template: Optional[dict] = None   # 변형 시 파이프라인에 반영
    extra_sitenames: Optional[list] = None   # 다중 시설 질의 잔여분 (소비 시 None 으로)
    extras: dict = field(default_factory=dict)


# ── 핸들러 베이스 + 레지스트리 ─────────────────────────────────────────
class IntentHandler:
    """인텐트 커스텀 처리 훅. intents 튜플에 담당 인텐트 나열."""
    intents: tuple = ()

    async def prepare(self, ctx: IntentContext) -> None:  # noqa: B027
        """세션 병합 전 훅 — new_params 기본값 보정. 기본 no-op."""

    async def pre_sql(self, ctx: IntentContext) -> None:  # noqa: B027
        """SQL 실행 전 훅 — sql 변형·rows 조달·answer_template 오버라이드. 기본 no-op."""

    def response_extras(self, ctx: IntentContext, processed_data: dict) -> dict:
        """응답 추가 필드 훅 — 기본 없음."""
        return {}


REGISTRY: dict = {}


def intent_handler(cls):
    """클래스 데코레이터 — intents 에 나열된 인텐트로 인스턴스 등록."""
    inst = cls()
    for name in cls.intents:
        if name in REGISTRY:
            raise ValueError(f"인텐트 핸들러 중복 등록: {name}")
        REGISTRY[name] = inst
    return cls


def get_intent_handler(intent: str):
    return REGISTRY.get(intent)
