"""
backend/utils/llm_router.py

Smart LLM routing (DISABLED by default).
Port of mcp-client/config/routing.js + llm-config.js.

When ENABLE_SMART_ROUTING=false (default), always returns LLM_MODEL.
When enabled, could route complex queries to a more capable model.
For now, the routing logic is a stub — always falls back to default.

To enable: set ENABLE_SMART_ROUTING=true in .env.
"""
from __future__ import annotations

import logging
import os

from utils.llm_config import LLM_MODEL

logger = logging.getLogger(__name__)

ENABLE_SMART_ROUTING = os.getenv("ENABLE_SMART_ROUTING", "false").lower() == "true"


def route_query(user_message: str) -> str:
    """
    Return the model name to use for this query.

    When smart routing is disabled (default), always returns LLM_MODEL.
    When enabled in future, could route complex queries to a stronger model.
    """
    if not ENABLE_SMART_ROUTING:
        return LLM_MODEL

    # Smart routing disabled — always default
    # Future: analyse complexity, return stronger model for multi-step queries
    logger.debug(f"[LLMRouter] Smart routing disabled — using {LLM_MODEL}")
    return LLM_MODEL
