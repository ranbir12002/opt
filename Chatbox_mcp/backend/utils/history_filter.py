"""Relevance-based conversation history filtering for downstream LLM consumers.

When a user switches between agents (schedule → invoice → workorder → MCP),
each consumer should primarily see its own domain history.  Cross-domain
entries are replaced with compact entity-ID stubs so that cross-path
references (job_id, staff_id …) remain available without flooding the
parser with irrelevant operational detail that causes LLM drift.

Usage::

    from utils.history_filter import filter_history

    # For an agent consumer:
    filtered = filter_history(effective_history, "schedule")

    # For MCP consumer:
    filtered = filter_history(effective_history, "mcp")

    # Intent analyzer / validator — no filtering:
    filtered = filter_history(history, "intent")  # returns history as-is
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Domain detection
# ---------------------------------------------------------------------------

_DOMAIN_PREFIXES: Dict[str, List[str]] = {
    "schedule": [
        "COMPLETED CREATE schedule:",
        "COMPLETED UPDATE schedule:",
        "COMPLETED DELETE schedule:",
        "FAILED CREATE schedule:",
        "FAILED UPDATE schedule:",
        "FAILED DELETE schedule:",
        "[schedule agent",
    ],
    "invoice": [
        "COMPLETED CREATE invoice:",
        "COMPLETED UPDATE invoice:",
        "COMPLETED DELETE invoice:",
        "FAILED CREATE invoice:",
        "FAILED UPDATE invoice:",
        "FAILED DELETE invoice:",
        "[invoice agent",
    ],
    "workorder": [
        "[workorder agent",
    ],
}


def _detect_domain(content: str) -> Optional[str]:
    """Which agent domain does this assistant entry belong to?

    Returns ``None`` for general / MCP responses.
    """
    for domain, prefixes in _DOMAIN_PREFIXES.items():
        if any(p in content for p in prefixes):
            return domain
    if content.startswith("[multi-action:"):
        return "multi_action"
    return None


# ---------------------------------------------------------------------------
# Cross-path ID extraction
# ---------------------------------------------------------------------------

_CROSS_PATH_RE = re.compile(
    r"((?:job_id|staff_id|section_id|cost_centre_id|invoice_id|"
    r"contractor_job_id|schedule_id|quote_id)=\S+?)(?:[,\]\s;]|$)"
)


def _compact_cross_ids(content: str) -> Optional[str]:
    """Extract entity-ID key=value pairs as a compact stub.

    Returns ``None`` if no recognised IDs are found.
    """
    matches = _CROSS_PATH_RE.findall(content)
    if not matches:
        return None
    unique = list(dict.fromkeys(matches))  # dedupe, preserve order
    return f"[prior context: {', '.join(unique)}]"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def filter_history(history: list, consumer: str) -> list:
    """Filter conversation history by relevance to *consumer*.

    Parameters
    ----------
    history:
        List of ``{"role": "user"|"assistant", "content": "..."}`` dicts.
    consumer:
        ``"schedule"`` | ``"invoice"`` | ``"workorder"`` | ``"mcp"``
        ``"intent"`` and ``"validator"`` bypass filtering (full history).

    Filtering rules
    ---------------
    * **User messages** (``role=user``) → always included.
    * **Own-domain** assistant entries → kept in full.
    * **General / MCP** assistant entries (no agent prefix) → always included.
    * **Multi-action** entries → always included (may span domains).
    * **Cross-domain** agent entries → replaced with compact ID stub,
      or dropped entirely if no IDs are found.
    """
    if consumer in ("intent", "validator"):
        return history

    filtered: List[Dict[str, str]] = []
    for entry in history:
        role = entry.get("role", "")
        content = entry.get("content", "")

        # User messages are always relevant
        if role == "user":
            filtered.append(entry)
            continue

        domain = _detect_domain(content)

        if domain is None:
            # General / MCP response — always relevant
            filtered.append(entry)
        elif domain == consumer or domain == "multi_action":
            # Own domain or multi-action — keep full content
            filtered.append(entry)
        else:
            # Cross-domain agent entry — keep only entity IDs as stub
            ids = _compact_cross_ids(content)
            if ids:
                filtered.append({"role": "assistant", "content": ids})
            # No IDs found → drop entirely (pure noise for this consumer)

    return filtered
