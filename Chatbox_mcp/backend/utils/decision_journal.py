# backend/utils/decision_journal.py
"""
Decision Journal — fire-and-forget recording of every system decision.

ZERO behavior change: all writes are wrapped in try/except and never
block or fail the request. PII is sanitized via pii_filter.sanitize_for_llm().

Usage:
    from utils.decision_journal import new_request_id, record_decision, record_trace

    req_id = new_request_id()
    record_decision(
        request_id=req_id, org_id=1, user_id=5,
        dimension="routing", decision_type="intent_analysis",
        decision_value="schedule", confidence=0.92,
    )
"""
import json
import uuid
import logging
from typing import Any, Dict, List, Optional

from auth.database import get_db
from utils.pii_filter import sanitize_for_llm

logger = logging.getLogger(__name__)


def new_request_id() -> str:
    """Generate a unique request ID (uuid4 hex, 32 chars)."""
    return uuid.uuid4().hex


def record_decision(
    request_id: str = "",
    org_id: Optional[int] = None,
    user_id: Optional[int] = None,
    dimension: str = "",
    decision_type: str = "",
    decision_value: str = "",
    confidence: float = 0.0,
    reasoning: str = "",
    context: Optional[Dict[str, Any]] = None,
    outcome: str = "pending",
    duration_ms: int = 0,
) -> None:
    """
    Fire-and-forget: write one decision row to decision_journal.
    Errors are logged at DEBUG and swallowed — never blocks the request.
    """
    try:
        safe_reasoning = str(reasoning)[:500] if reasoning else ""
        safe_context = json.dumps(
            sanitize_for_llm(context) if context else {},
            default=str,
        )[:2000]

        conn = get_db()
        conn.execute(
            """INSERT INTO decision_journal
               (request_id, org_id, user_id, dimension, decision_type,
                decision_value, confidence, reasoning, context_json,
                outcome, duration_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                request_id,
                org_id or 0,
                user_id or 0,
                dimension,
                decision_type,
                str(decision_value)[:200],
                max(0.0, min(1.0, confidence)),
                safe_reasoning,
                safe_context,
                outcome,
                duration_ms,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug(f"Decision journal write failed (non-fatal): {e}")


def record_trace(
    request_id: str,
    org_id: Optional[int] = None,
    user_id: Optional[int] = None,
    intent: str = "",
    agent: str = "",
    action: str = "",
    confidence: float = 0.0,
    tool_sequence: Optional[List[str]] = None,
    outcome: str = "pending",
    duration_ms: int = 0,
    message_preview: str = "",
) -> None:
    """
    Fire-and-forget: write or update the request trace row.
    Uses INSERT OR REPLACE on request_id UNIQUE constraint so it can
    be called at request start (with intent) and again at end (with outcome).
    """
    try:
        safe_tools = json.dumps(tool_sequence or [])[:1000]
        safe_preview = str(message_preview)[:100]

        conn = get_db()
        conn.execute(
            """INSERT OR REPLACE INTO request_traces
               (request_id, org_id, user_id, intent, agent, action,
                confidence, tool_sequence, tool_count, outcome,
                duration_ms, message_preview)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                request_id,
                org_id or 0,
                user_id or 0,
                intent,
                agent,
                action,
                max(0.0, min(1.0, confidence)),
                safe_tools,
                len(tool_sequence or []),
                outcome,
                duration_ms,
                safe_preview,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug(f"Request trace write failed (non-fatal): {e}")


def update_trace_outcome(
    request_id: str,
    outcome: str,
    duration_ms: int = 0,
    tool_sequence: Optional[List[str]] = None,
) -> None:
    """Update just the outcome + duration on an existing trace. Fire-and-forget."""
    try:
        conn = get_db()
        if tool_sequence is not None:
            safe_tools = json.dumps(tool_sequence)[:1000]
            conn.execute(
                """UPDATE request_traces
                   SET outcome = ?, duration_ms = ?,
                       tool_sequence = ?, tool_count = ?
                   WHERE request_id = ?""",
                (outcome, duration_ms, safe_tools, len(tool_sequence), request_id),
            )
        else:
            conn.execute(
                """UPDATE request_traces
                   SET outcome = ?, duration_ms = ?
                   WHERE request_id = ?""",
                (outcome, duration_ms, request_id),
            )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug(f"Trace outcome update failed (non-fatal): {e}")
