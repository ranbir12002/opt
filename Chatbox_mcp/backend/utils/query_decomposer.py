"""
backend/utils/query_decomposer.py

Selective query decomposer (DISABLED by default).
Port of mcp-client/utils/query-decomposer.js.

For complex multi-entity queries (~10% of traffic), one cheap LLM call
splits the query into independent sub-queries for parallel hints.
Result is injected as a planning hint into the system prompt.

To enable: set ENABLE_QUERY_DECOMPOSER=true in .env.

NOTE: This is a planning hint — the LLM still makes all tool calls.
We just give it a smarter starting plan.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

ENABLE_QUERY_DECOMPOSER = os.getenv("ENABLE_QUERY_DECOMPOSER", "false").lower() == "true"

# ── Detection: Should we decompose? ──────────────────────────────────────────

_DECOMPOSE_PATTERNS = [
    re.compile(r"\bcompare\b", re.IGNORECASE),
    re.compile(r"\bversus\b|\bvs\.?\b", re.IGNORECASE),
    re.compile(r"\bboth\b.*\band\b", re.IGNORECASE),
    re.compile(r"\b(?:show|get|list|find)\b.*\band\b.*\b(?:show|get|list|find|for)\b", re.IGNORECASE),
    re.compile(r"\b(?:job|invoice|schedule|quote)\b.*\band\b.*\b(?:job|invoice|schedule|quote)\b", re.IGNORECASE),
    re.compile(r"\b(?:between|difference)\b.*\band\b", re.IGNORECASE),
    re.compile(r"\bside\s*by\s*side\b", re.IGNORECASE),
    re.compile(r"\b(?:as\s+well\s+as|along\s+with|together\s+with)\b", re.IGNORECASE),
]

_SEQUENTIAL_PATTERNS = [
    re.compile(r"\b(?:for\s+the|for\s+that|of\s+the|of\s+that)\b", re.IGNORECASE),
    re.compile(r"\b(?:then|after\s+that|next|once)\b", re.IGNORECASE),
]

_STRONG_PARALLEL = re.compile(r"\bcompare\b|\bversus\b|\bvs\.?\b|\bside\s*by\s*side\b", re.IGNORECASE)


def is_decomposable(message: str) -> bool:
    """
    Quick check: does this query look like it would benefit from decomposition?
    Cheap regex check — no LLM call.
    """
    if not message or len(message) < 20:
        return False

    has_parallel = any(p.search(message) for p in _DECOMPOSE_PATTERNS)
    if not has_parallel:
        return False

    has_sequential = any(p.search(message) for p in _SEQUENTIAL_PATTERNS)
    if has_sequential:
        return bool(_STRONG_PARALLEL.search(message))

    return True


async def decompose_query(
    user_message: str,
    llm_chat_fn,    # async fn(messages, max_tokens=200) -> str
) -> Optional[Dict]:
    """
    Decompose a complex query into sub-queries using a cheap LLM call.

    Returns {"sub_queries": [...], "planning_hint": "..."} or None.
    Returns None when decomposition isn't helpful or ENABLE_QUERY_DECOMPOSER=false.
    """
    if not ENABLE_QUERY_DECOMPOSER:
        return None

    if not is_decomposable(user_message):
        return None

    messages = [
        {
            "role": "system",
            "content": (
                "You break complex queries into independent sub-queries for parallel execution.\n\n"
                "Reply with ONLY valid JSON:\n"
                "{\"decomposable\": true/false, \"sub_queries\": [\"query1\", \"query2\"], \"reasoning\": \"brief reason\"}\n\n"
                "Rules:\n"
                "- ONLY decompose if sub-queries are truly INDEPENDENT (can run in parallel)\n"
                "- Do NOT decompose if query B depends on query A's result\n"
                "- Each sub-query should map to 1-2 tool calls\n"
                "- Maximum 4 sub-queries\n"
                "- If not decomposable, set decomposable=false with empty sub_queries"
            ),
        },
        {
            "role": "user",
            "content": user_message[:300],
        },
    ]

    try:
        text = (await llm_chat_fn(messages, max_tokens=200)).strip()

        json_match = re.search(r"\{[\s\S]*\}", text)
        if not json_match:
            return None

        parsed = json.loads(json_match.group(0))

        if not parsed.get("decomposable") or not isinstance(parsed.get("sub_queries"), list) or len(parsed["sub_queries"]) < 2:
            logger.debug(f"[QueryDecomposer] Not decomposable: {parsed.get('reasoning', 'single query')}")
            return None

        sub_queries = parsed["sub_queries"][:4]
        planning_hint = (
            "\nQUERY PLAN: This is a multi-part query. Execute these sub-queries (in parallel where possible):\n"
            + "\n".join(f"  {i + 1}. {sq}" for i, sq in enumerate(sub_queries))
            + "\nAfter gathering all data, synthesize a combined answer."
        )

        logger.debug(f"[QueryDecomposer] Decomposed into {len(sub_queries)} sub-queries")
        return {"sub_queries": sub_queries, "planning_hint": planning_hint}

    except Exception as e:
        logger.warning(f"[QueryDecomposer] Decomposition failed: {e}")
        return None
