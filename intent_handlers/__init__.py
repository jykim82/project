"""인텐트 핸들러 레지스트리 — 아키텍처 2단계 (docs/intent-architecture-spec.md).

사용 (ai_server):
    import intent_handlers
    intent_handlers.init_services(get_causal_index=..., ...)
    h = intent_handlers.get_intent_handler(intent)
    if h: await h.pre_sql(ctx)
"""
from .base import (  # noqa: F401
    IntentContext,
    IntentHandler,
    get_intent_handler,
    init_services,
    intent_handler,
)

# 핸들러 등록 (데코레이터 부수효과)
from . import alarm, anomaly, night_min_flow, reservoir, tag_query, trend  # noqa: F401,E402
