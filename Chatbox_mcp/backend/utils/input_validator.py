# backend/utils/input_validator.py
"""
LLM-Powered Input Contradiction Detector.

Checks user requests for logical contradictions BEFORE parsing/resolution.
Uses the Crossroads system — no hardcoded rules, the LLM reasons about
any kind of conflict (time, date, action, scope, quantity, etc.).

Integration points:
    - Agent path  (chat.py _chat_sse_generator / _chat_core)
    - MCP path    (chat.js /api/chat/stream)

Usage:
    from utils.input_validator import validate_user_input

    result = await validate_user_input(
        user_message="schedule tarun from 7am to 1pm for 24hrs",
        detected_route="schedule",
        llm_chat=llm_chat,
    )
    if result:
        # result is a contradiction clarification dict → return to frontend
        ...
"""

from __future__ import annotations
import logging
import uuid
from typing import Any, Callable, Dict, List, Optional

from utils.crossroads import resolve_crossroads, register_crossroad_type

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# Register the "input_validation" crossroad type
# ═══════════════════════════════════════════════════════════════════════════

_INPUT_VALIDATION_PROMPT = """You analyze user requests for a construction back-office system for logical contradictions — places where the user says two things that CANNOT BOTH be true.

You receive the user's raw message and the detected route (schedule, invoice, workorder, query).

YOUR JOB: Find contradictions only. Nothing else.

EXAMPLES OF CONTRADICTIONS:
- Time conflicts: "from 7am to 1pm" (6hrs) but also "for 24 hours" — two different durations
- Date conflicts: "schedule for today" but also "on next Monday" — two different dates
- Mutually exclusive actions: "delete the schedule and update it to 8 hours" — can't do both
- Quantity vs detail mismatch: "create 3 work orders" but only describes 2 items
- Filter conflicts: "show invoices from last week created today" — can't be both last week and today
- Status conflicts: "approve and reject this invoice" — mutually exclusive statuses
- Scope conflicts: "for all jobs" but "only job 123" — all vs specific
- Identity conflicts: "schedule john" but "assign it to mike" — two different people for one slot
- Value conflicts: "set materials to $500" but "total cost should be $200" — materials alone exceed total

WHAT IS NOT A CONTRADICTION — NEVER flag these:
- Missing information (no start time, no job name) — handled by entity resolution
- Ambiguous names ("schedule john" when there are two Johns) — handled by fuzzy matching
- Unusual but valid requests ("schedule for 16 hours") — extreme but not contradictory
- Implicit defaults — omitting optional fields is fine
- Vague requests ("schedule tarun on site") — incomplete, not contradictory
- Follow-up corrections ("no, I meant tomorrow") — correction, not contradiction
- Corrections / updates referencing old and new values: "change X to 6 hours instead of 8", "update from $500 to $300", "make it 3 days not 5" — the user is telling you the CURRENT value and the DESIRED new value. The two numbers are not in conflict; one is being REPLACED by the other. Words like "instead of", "rather than", "not", "from…to", "change…to" signal an update, NOT a contradiction. NEVER flag these.
- Multiple values or items listed together: "today and tomorrow", "john and mike", "job A and job B", numbered lists (1., 2., 3.), bullet points, comma-separated items, or any form of listing multiple entries — these mean BOTH/ALL, the user wants multiple items created. This is valid batch/multi-day/multi-job behavior, NEVER a conflict. Each listed item is an INDEPENDENT entry with its own job/site, time range, and other fields.
- Numbered/listed schedule items with different jobs or sites: "1. job A 7am-9am, 2. job B 9am-11am" — each numbered item describes a SEPARATE schedule entry. Compare times ONLY within the SAME numbered item. Times across different items/jobs never conflict. A schedule ending at 7:00am on job A and another starting at 7:00am on job B is perfectly valid sequential scheduling. Only flag if the SAME numbered item has internally contradictory values.
- Back-to-back or overlapping times on DIFFERENT jobs/sites: The same person can work sequential shifts — job A 6:45am-7:00am then job B 7:00am-9:00am. This is normal construction workflow. Adjacent or overlapping time blocks are only contradictions when they apply to the SAME job/site within a SINGLE list item.
- Different operations on different entities in one request: "create schedule for X on job A and delete schedule for Y on job B" — different entities/jobs can have different operations simultaneously. Only flag if the SAME entity has mutually exclusive actions.
- Multiple values with "or": "today or tomorrow", "john or mike" — the user is unsure which one, this is AMBIGUITY not contradiction. Other systems handle ambiguity.
- Entity references that might not match in the database: "job 12345 on site bloomfield" — you have NO access to the database, so you cannot know whether these match. Never flag data-level mismatches.
- Redundant references: "job 123 at bloomfield" — may describe the same thing two ways. Not a conflict.
- End time expressions: "reduce the time to 7:45am", "finish at 3pm", "until 2pm", "shorten to 10am" — these set the schedule END time. This is a VALID request, NOT a contradiction. "reduce/shorten TO [clock time]" = set end time. "reduce/shorten BY [duration]" = adjust duration. Only flag if there is ALSO a conflicting explicit duration in the same message (e.g. "reduce to 3pm for 24 hours").

WHAT IS A CONTRADICTION — only flag these:
- Mathematical impossibilities: "from 7am to 1pm" = 6 hours, but "for 24 hours" = 24 hours. The MATH does not add up. Flag it.
  NOTE: Mathematical impossibilities apply ONLY within a single item/entry. In multi-item requests (numbered lists, etc.), each item has its own independent time/duration/date context. Do NOT compare values across different numbered items or different jobs.
- Mutually exclusive actions: "delete the schedule and update it" — you cannot do BOTH to the same item. Flag it.
- Logical impossibilities: "approve and reject this invoice" — opposite outcomes. Flag it.

KEY PRINCIPLE: You can ONLY detect contradictions from pure text logic and arithmetic. If resolving the conflict would require looking up data in a database or knowing the system's data model, it is NOT a contradiction — pass it through.

RULES:
- Be ULTRA-CONSERVATIVE: if in doubt, return "pass". False positives waste user time and are much worse than missed catches.
- Only flag when you can explain the MATHEMATICAL or LOGICAL impossibility in one sentence.
- Maximum 3 contradictions per request
- Each contradiction must cite BOTH conflicting values directly from the user's message
- Phrase the question neutrally so the user can pick either option

Return ONLY valid JSON:

If no contradictions:
{"reasoning": "<briefly explain why no contradictions were found — what you checked>", "decision": "pass", "fields": {}, "errors": [], "confidence": 1.0}

If contradictions found:
{
  "reasoning": "<identify each contradiction: quote both conflicting values from the user's message and explain why they conflict>",
  "decision": "contradictions_found",
  "fields": {
    "contradictions": [
      {
        "field": "<what aspect conflicts, e.g. duration, date, action>",
        "value_a": "<first thing user said, quoted from their message>",
        "value_b": "<second conflicting thing, quoted from their message>",
        "question": "<clear question for the user to pick one>"
      }
    ]
  },
  "errors": [],
  "confidence": <0.0-1.0>
}"""

register_crossroad_type(
    "input_validation",
    _INPUT_VALIDATION_PROMPT,
    domain_topics=[],  # Pure logic — no domain knowledge needed
)


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════

async def validate_user_input(
    user_message: str,
    detected_route: str,
    llm_chat: Callable,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Check user input for logical contradictions before parsing.

    Args:
        user_message: The raw user message
        detected_route: Detected intent route ("schedule", "invoice", "workorder", "query")
        llm_chat: PII-safe LLM chat function
        conversation_history: Recent conversation turns for context

    Returns:
        None if input is clean (no contradictions — continue pipeline).
        Dict with contradiction clarification data if contradictions found.
    """
    # Skip very short messages — unlikely to contain contradictions
    if len(user_message.strip()) < 15:
        return None

    context: Dict[str, Any] = {
        "user_message": user_message,
        "detected_route": detected_route,
    }
    # Include last 2 turns for follow-up context
    if conversation_history:
        context["recent_conversation"] = conversation_history[-4:]

    try:
        result = await resolve_crossroads(
            crossroad_type="input_validation",
            question=f"Check this {detected_route} request for logical contradictions",
            context=context,
            llm_chat=llm_chat,
        )
    except Exception as e:
        # Validation failure should NEVER block the pipeline
        logger.warning(f"Input validation error (non-blocking): {e}")
        return None

    if result.get("decision") != "contradictions_found":
        return None

    contradictions = result.get("fields", {}).get("contradictions", [])
    if not contradictions:
        return None  # LLM said contradictions but gave none — treat as pass

    session_id = f"contradict_{uuid.uuid4().hex[:12]}"

    logger.info(
        f"⚠️  Input contradictions detected ({len(contradictions)}): "
        f"{[c.get('field') for c in contradictions]}"
    )

    # Build clarification items in the existing frontend format
    clarification_items = []
    for i, c in enumerate(contradictions):
        clarification_items.append({
            "row": i,
            "type": "contradiction",
            "field": c.get("field", "conflict"),
            "message": c.get("question", "Which did you mean?"),
            "options": [
                {"id": "a", "name": c.get("value_a", "Option A")},
                {"id": "b", "name": c.get("value_b", "Option B")},
            ] + ([{"id": "c", "name": c["value_c"]}] if c.get("value_c") else []),
            "operation": detected_route.upper(),
        })

    return {
        "needs_clarification": True,
        "clarification_data": {
            "session_id": session_id,
            "clarification_count": len(clarification_items),
            "resolved_count": 0,
            "total_count": 1,
            "clarifications": clarification_items,
            "agent": detected_route if detected_route != "query" else "chat",
            "contradiction_type": True,
            "original_message": user_message,
        },
        "message": "Your request has conflicting information. Please clarify:",
    }
