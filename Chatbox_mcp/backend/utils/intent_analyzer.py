# backend/utils/intent_analyzer.py
"""
LLM-powered Intent Analyzer.

Replaces keyword-based routing with a single lightweight LLM call
that classifies user intent and determines which agent (if any)
should handle the request.
"""

from __future__ import annotations
import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# System Prompt (kept short for speed — ~350 tokens)
# ═══════════════════════════════════════════════════════════════════════════

_INTENT_SYSTEM_PROMPT = """You classify user messages for a construction company back-office system (Simpro ERP).

BEFORE outputting JSON, reason through these steps (silently — do not include this reasoning as separate text):
1. What is the user literally doing — action (create/read/update/delete/cancel) + entity (schedule/invoice/WO/PO/query)?
2. Is this a follow-up? Check the last 2 assistant messages for active operations, failures, or clarification requests.
3. If follow_up=true: which fields from the prior operation are being reused vs changed?
4. Does this match any RETRY, CORRECTION, or CLARIFICATION ANSWER pattern? Check before defaulting to general_query.
5. Is is_multi_action relevant? Look for numbered lists or "AND" joining separate CRUD operations on different entities.
Only after completing these steps, output the JSON.

Return ONLY valid JSON:
{"_reasoning": "<your step-by-step classification reasoning>", "intent": "<intent>", "agent": "<agent or null>", "action": "<action or null>", "confidence": <0.0-1.0>, "follow_up": <true|false>, "plan_steps": ["step1", "step2", ...], "department": "<department name or null>", "is_multi_action": <true|false>, "sub_requests": <array or null>, "reuse_fields": <object or null>, "changed_fields": <object or null>}

PLAN STEPS — provide 2-5 short, user-friendly steps SPECIFIC to this exact question:
- CRITICAL: Every plan must be UNIQUE to the user's actual question. Include the specific names, job IDs, dates, and details they mentioned.
- Use plain language. Do NOT mention internal systems, agents, APIs, databases, pipelines, or technical processes.
- First step: briefly restate what you understood from THEIR specific question (include their names, IDs, dates).
- Last step: always "Preparing your results" or "Preparing your summary".
- Middle steps: describe the specific lookups or actions needed for THIS question.
- Simple lookups: 2-3 steps. CRUD with entities: 3-4 steps. Bulk operations with files: 4-5 steps.
- NEVER copy generic steps. Every step must reference specifics from the user's message.

Set follow_up=true when the message references ANYTHING from a previous response — corrections, entity references, "same thing", "delete it", data/values from prior results ("how did you get $1500", "explain the profit margin", "what about the other two", "why is that zero"), pronouns referencing prior context ("that job", "those schedules", "the one you showed"), or any question that only makes sense given earlier conversation. Set follow_up=false ONLY for fully standalone requests that make complete sense without any prior context.

INTENTS:

1. schedule_crud → agent: "schedule"
   Any request to CREATE, MODIFY, MOVE, DELETE, BOOK, ASSIGN, LOCK, or UNLOCK a work schedule.
   Includes: "put X on job Y", "book X in for", "X needs to be on site", "schedule X for",
   "add X to job", "move X to tomorrow", "lock X", "unlock X", "reschedule", "cancel schedule",
   "extend schedule", "shorten schedule", "reassign to Y", "create schedule", "delete schedule".
   Actions: create, update, delete, lock, unlock

2. schedule_query → agent: null
   READING or VIEWING existing schedules only (no modifications).
   Includes: "show schedules", "what schedules", "who is scheduled", "list schedules",
   "any schedules for", "check schedule", "view schedule".
   Action: query

3. invoice_crud → agent: "invoice"
   Creating, modifying, or deleting invoices or bills.
   Actions: create, update, delete

4. workorder_crud → agent: "workorder"
   Creating, updating, or deleting work orders / contractor jobs for contractors.
   Includes: "create work order", "generate work orders for today", "work orders for roofing",
   "create wo for contractor X", "prepare contractor jobs", "wo for today's schedules",
   "work order for job X cost centre Y", "generate wo", "make work order",
   "update work order", "change contractor job materials", "modify work order description",
   "delete work order", "remove contractor job", "delete contractor job 123".
   Actions: create, update, delete

5. purchase_order_crud → agent: "purchase_order"
   Creating, updating, or deleting purchase orders / supplier orders / material orders.
   Includes: "create purchase order", "raise a PO", "order materials from supplier X",
   "create PO for job Y", "add purchase order", "make a supplier order",
   "update PO 123", "change the quantity on purchase order", "modify PO status",
   "delete purchase order 456", "cancel PO", "remove supplier order",
   "generate a material order", "raise order for ABC Supplies", "order from supplier".
   Actions: create, update, delete

6. general_query → agent: null
   Everything else: job lookups, contact search, general questions, greetings, etc.
   Action: null

6. cancel_request → agent: null
   User wants to CANCEL, ABORT, or ABANDON the current operation or pending clarification.
   Includes: "cancel", "cancel this", "never mind", "forget it", "forget about it",
   "start over", "stop", "abort", "don't do it", "skip it", "nah", "I changed my mind",
   "drop it", "leave it", "not anymore", "scratch that", "ignore that"
   Action: cancel
   CRITICAL: When a pending clarification exists in conversation history (the last assistant
   message shows a clarification form or "needs clarification"), and the user says cancel/abort
   phrases, this MUST be classified as cancel_request — NOT as a follow-up to the pending agent.
   Set follow_up=false for cancel_request — cancellation is a standalone decision.
   Set confidence >= 0.9 for clear cancel phrases.

FILE UPLOADS:
When the user message contains [Uploaded file: ...], the user has attached a structured data file (Excel, CSV, etc.).
- Use the column names listed after "columns:" to infer what kind of data the file contains — names will NOT always match canonical field names. Reason about the data domain (people/time → schedule, billing/amounts → invoice, materials/contractors → workorder, suppliers/orders → purchase_order).
- row_count tells you how many records are in the file — always include it in plan_steps.
- action is "create" unless the user's message explicitly says update or delete.
- plan_steps must reference the actual filename and row count from the upload block. Keep steps short, user-friendly, and specific to the file content — do NOT mention internal systems, agents, APIs, or technical processes.

RULES:
- For "lock X" or "unlock X" without other context → schedule_crud (lock/unlock).
- For follow-ups ("do the same for john", "yes", "same thing tomorrow", "delete it", "undo that"), check conversation history. If recent messages show schedule operations → schedule_crud. If recent messages show work order operations → workorder_crud. If recent messages show invoice operations → invoice_crud. If recent messages show purchase order operations → purchase_order_crud.
- RETRY / CONTINUATION phrases: "try again", "retry", "redo", "reload", "complete it", "finish it", "do it again", "continue", "reload and complete", "run it again", "go ahead". These ALWAYS refer to the LAST operation in conversation history. Check the most recent assistant message: if it mentions a specific agent (schedule, workorder, invoice) or operation (create, update, delete), route to THAT agent with THAT action. Set confidence >= 0.8 for retry phrases when history clearly shows the previous agent.
- If the last assistant message indicates a FAILURE (contains "failed", "error", "locked", "could not"), and the user says something like "try again" or "retry" or "reload", route to the same agent with the same action at confidence >= 0.85.
- If intent is genuinely ambiguous, set confidence < 0.6.
- "schedule" as a noun in a question ("what's the schedule") → schedule_query.
- "schedule" as a verb/action ("schedule john for tomorrow") → schedule_crud.

CORRECTION PATTERNS (follow_up=true, confidence >= 0.85):
- "no, I meant X" / "wrong X, use Y" / "actually make it X" / "change it to X" / "not 9am, 10am"
  → Route to the SAME agent from the last assistant message, action="update". The agent parser will resolve what to correct from conversation history.
- "wrong person, should be John" / "wrong job" / "wrong contractor" / "wrong cost centre"
  → Same agent, action="update".
- "try X instead" / "use X instead" (after a failure)
  → Same agent, SAME action as the failed operation (e.g., if CREATE failed, action="create" not "update").

ENTITY REFERENCE PATTERNS (follow_up=true, confidence >= 0.85):
- "do the same for John" / "same thing for job X" / "same schedule for tomorrow"
  → Same agent, same action (usually "create"), swap the referenced entity.
- "now delete it" / "delete that" / "remove it"
  → Same agent, action="delete".
- "now lock it" / "lock that" / "unlock it"
  → schedule_crud, action="lock" or "unlock".
- "change the cost centre on that" / "update the time" / "modify the description"
  → Same agent, action="update".

CLARIFICATION ANSWER PATTERNS (follow_up=true, confidence >= 0.85):
When the previous assistant message indicates a FAILED or NEEDS CLARIFICATION result (e.g., "Staff not specified", "missing field", "NEEDS CLARIFICATION"), and the user responds with:
- A bare name: "jarrad edwards", "john smith" → The user is providing the missing staff/person name.
- "its [name]" / "the staff is [name]" / "[name] is the one" → Providing the missing entity.
- A bare number: "22601", "154740" → Providing a missing ID.
- A date: "tomorrow", "next monday" → Providing a missing date.
Route to the SAME agent from the failed/clarification message, with the SAME action (e.g., if CREATE failed, action="create").
CRITICAL: A bare entity name is NOT a general query when the previous message shows a failure or clarification request. Always check conversation history FIRST.

MULTI-ACTION DETECTION:
Some user messages contain MULTIPLE independent CRUD operations. Detect these and split them into sub_requests.

Set is_multi_action=true and provide sub_requests when:
- Numbered lists (1., 2., 3.) where each item is a separate CRUD action on a DIFFERENT job/site/entity: "schedule Nick for today: 1. Lot 40932 7am-9am 2. Lot 311 9am-11am"
- Explicit "AND" joining separate CRUD operations: "create a schedule for John AND create a work order for job 123"
- Cross-agent requests: "schedule John on job 123 and generate a work order for the plumbing cost centre"
- Mixed operations on different entities: "create schedule for John on job A and delete schedule for Mike on job B"

Set is_multi_action=false (normal single request) when:
- Single operation with multiple staff: "schedule John and Mike on job 123" (one operation, multi-row — handled by agent)
- Read-only queries: "show schedules and invoices" (goes to MCP, not agents)
- Single CRUD operation regardless of complexity: "schedule Nick on job 123 with cost centre 456"
- Follow-ups or corrections referencing prior context

sub_requests format (only when is_multi_action=true):
[
  {"agent": "<agent>", "action": "<action>", "text": "<fully standalone natural-language request>", "description": "<short 5-10 word summary>"},
  ...
]

CRITICAL — each sub_request "text" must be FULLY STANDALONE:
- When the user provides common/shared context before a list (e.g., staff name, date), you MUST include that shared context in EVERY sub_request's text.
- Example: "schedule Nick Gubby for today on below jobs: 1. Lot 40932 7am-9am 2. Lot 311 9am-11am"
  → sub_requests[0].text = "schedule Nick Gubby for today on Lot 40932 start time 7am to 9am"
  → sub_requests[1].text = "schedule Nick Gubby for today on Lot 311 start time 9am to 11am"
- Each sub_request will be processed by an independent agent that has NO access to the other sub_requests or the original message.

When is_multi_action=true, set "intent", "agent", and "action" to the FIRST sub_request's values (for backward compatibility). plan_steps should cover all sub_requests.

When is_multi_action=false, set "sub_requests": null.

DEPARTMENT EXTRACTION:
If the user mentions a department or trade (e.g., "roofing department", "plumbing", "drainage dept", "electrical"), extract it as "department": "<Name>" (title case, e.g., "Roofing", "Plumbing", "Drainage"). If no department is mentioned, set "department": null.

FOLLOW-UP CONTEXT BRIDGE (reuse_fields / changed_fields):
When follow_up=true, extract structured context from conversation history to pass to the downstream agent or MCP.
This prevents the downstream system from having to re-parse history — it gets explicit fields.

reuse_fields: Fields from the PREVIOUS operation that the user wants to KEEP in this follow-up.
changed_fields: Fields the user is CHANGING or ADDING in this follow-up.

Extract from the most recent assistant message that contains operation data (IDs, names, dates).
Use GENERIC field names that work across all agents:
  staff / staff_id, job / job_id, section / section_id, cost_centre / cost_centre_id,
  contractor / contractor_id, date, start_time, end_time, blocks, schedule_id,
  invoice_id, workorder_id, description, operation

Examples:
- History: "COMPLETED CREATE schedule: staff_name=Nick, staff_id=3465, job_id=22601, date=2026-03-06"
  User: "do the same for John"
  → reuse_fields: {"job_id": 22601, "date": "2026-03-06"}, changed_fields: {"staff": "John"}

- History: "COMPLETED CREATE schedule: staff_name=Nick, staff_id=3465, job_id=22601"
  User: "now create a work order for the same job"
  → reuse_fields: {"job_id": 22601, "job": "Lot 40932"}, changed_fields: {}

- History: "[Data Context — 3 items] ID=22601 Name=Bloomfield Type=Project"
  User: "create an invoice for that job"
  → reuse_fields: {"job_id": 22601, "job": "Bloomfield"}, changed_fields: {}

- History: "COMPLETED CREATE schedule: staff_name=Nick, date=2026-03-06, start_time=07:00, blocks=4"
  User: "same thing but at 10am"
  → reuse_fields: {"staff": "Nick", "date": "2026-03-06", "blocks": 4}, changed_fields: {"start_time": "10:00"}

- User: "delete it" (after schedule create)
  → reuse_fields: {"schedule_id": 98765, "staff_id": 3465, "job_id": 22601}, changed_fields: {"operation": "DELETE"}

Rules:
- ONLY extract reuse_fields/changed_fields when follow_up=true. Set both to null when follow_up=false.
- Extract BOTH human names AND resolved IDs when available in history (e.g., staff="Nick" AND staff_id=3465).
- If you cannot determine what to reuse (e.g., ambiguous history), set both to null — the downstream system will fall back to history-based resolution.
- For cross-agent follow-ups (e.g., schedule result → workorder request), extract the fields that are relevant to the TARGET agent.
- For GET/query follow-ups routed to MCP, extract entity references the user is referring to."""


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════

_FALLBACK = {"intent": "general_query", "agent": None, "action": None, "confidence": 0.0, "plan_steps": []}

# Fast-path: trivial messages that always route to MCP (no LLM call needed)
_TRIVIAL_GENERAL = re.compile(
    r"^\s*("
    r"hi|hello|hey|howdy|greetings|"
    r"thanks|thank\s*you|thx|cheers|"
    r"ok|okay|sure|yep|yup|nope|yes|no|"
    r"bye|goodbye|see\s*ya|"
    r"good\s*(morning|afternoon|evening|night)|"
    r"what\s*can\s*you\s*do|help"
    r")\s*[!?.,:]*\s*$",
    re.IGNORECASE,
)
_TRIVIAL_RESULT = {"intent": "general_query", "agent": None, "action": None, "confidence": 1.0, "follow_up": False, "plan_steps": []}

# Fast path: READ-ONLY queries that always go to MCP (no agent needed).
# Matches patterns like "show employees", "list jobs", "how many invoices", etc.
_MCP_QUERY = re.compile(
    r"^\s*("
    r"(?:show|list|get|view|display|find|fetch|search|look\s*up|pull\s*up|check)\s+"
    r"|(?:how\s+many|what(?:'s|\s+is|\s+are)?|who(?:'s|\s+is|\s+are)?|which)\s+"
    r"|(?:tell\s+me\s+about|give\s+me|can\s+you\s+(?:show|get|list|find))\s+"
    r")",
    re.IGNORECASE,
)
_MCP_QUERY_RESULT = {"intent": "general_query", "agent": None, "action": "query", "confidence": 0.9, "follow_up": False, "plan_steps": ["Searching Simpro data", "Preparing your results"]}

# CRUD keywords that override the MCP fast-path (need full LLM analysis)
_CRUD_OVERRIDE = re.compile(
    r"\b("
    r"create|book|assign|put\s+\w+\s+on|move|delete|remove|cancel|lock|unlock|"
    r"update|modify|change|edit|reschedule|generate\s+(?:invoice|work\s*order|wo)|"
    r"make\s+(?:invoice|work\s*order|wo|schedule)|"
    r"raise\s+(?:a\s+)?(?:po|purchase\s*order|supplier\s*order)|"
    r"order\s+(?:from|materials|supplies)"
    r")\b",
    re.IGNORECASE,
)

_VALID_INTENTS = {"schedule_crud", "schedule_query", "invoice_crud", "workorder_crud", "purchase_order_crud", "general_query", "cancel_request"}
_VALID_ACTIONS = {"create", "update", "delete", "lock", "unlock", "query", "cancel", None}


def _get_valid_agents() -> set:
    """Dynamic set of loadable agents + None (for MCP/general queries)."""
    from agents.registry import get_loadable_agents
    return get_loadable_agents() | {None}


def analyze_intent(
    message: str,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    llm_chat: Optional[Callable] = None,
    session_context: Optional[str] = None,
    file_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Classify user intent via a single LLM call.

    Args:
        message: The user's raw message text.
        conversation_history: Recent conversation entries (role/content dicts).
        llm_chat: The unified LLM gateway function (from utils.llm).
        session_context: Optional compact scratchpad string with resolved
            entity IDs and actions from this session. Helps follow-up detection.
        file_context: Optional dict with uploaded file metadata:
            {"filename": str, "headers": list, "row_count": int, "detected_agent": str|None}
            When present the file info is appended to the user message so the LLM
            can classify agent + action + plan_steps from column context.

    Returns:
        Dict with keys: intent, agent, action, confidence.
        On failure returns a safe fallback that routes to MCP.
    """
    if not llm_chat:
        logger.warning("Intent analyzer: no llm_chat provided, using fallback")
        return dict(_FALLBACK)

    if not message or not message.strip():
        return dict(_FALLBACK)

    # Fast path: skip LLM call for trivial greetings/confirmations
    # (Skip fast-path when a file is attached — always needs full LLM analysis)
    if not file_context and _TRIVIAL_GENERAL.match(message.strip()):
        logger.info(f"🧠 Intent (fast path → trivial): {_TRIVIAL_RESULT}")
        return dict(_TRIVIAL_RESULT)

    # MCP fast-path REMOVED — all non-trivial queries go through the LLM
    # so that each query gets unique, context-aware plan_steps.
    # Only trivial greetings (above) skip the LLM call.

    # Build the user message, augmenting with file context when present
    if file_context:
        headers_preview = ", ".join(str(h) for h in file_context.get("headers", [])[:12])
        effective_message = (
            f"{message}\n\n"
            f"[Uploaded file: {file_context.get('filename', 'file')} — "
            f"{file_context.get('row_count', 0)} rows — "
            f"columns: {headers_preview}]"
        )
    else:
        effective_message = message

    try:
        # Build messages: system + last 4 history entries + user message
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": _INTENT_SYSTEM_PROMPT}
        ]

        # Inject session scratchpad for entity awareness in follow-up detection
        if session_context:
            messages.append({
                "role": "assistant",
                "content": session_context,
            })

        if conversation_history:
            # Include last 4 entries for context (enough for follow-up detection)
            for entry in conversation_history[-4:]:
                messages.append({
                    "role": entry.get("role", "user"),
                    "content": entry.get("content", "")
                })

        messages.append({"role": "user", "content": effective_message})

        # Single LLM call — JSON mode, deterministic
        raw = llm_chat(
            messages,
            response_format={"type": "json_object"},
            temperature=0.0,
            sanitize=False,  # No client data in prompt, just user text
        )

        result = json.loads(raw)

        # Validate and normalize
        intent = result.get("intent", "general_query")
        if intent not in _VALID_INTENTS:
            intent = "general_query"

        agent = result.get("agent")
        if agent not in _get_valid_agents():
            agent = None

        action = result.get("action")
        if action not in _VALID_ACTIONS:
            action = None

        confidence = float(result.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))

        follow_up = bool(result.get("follow_up", False))

        # Plan steps: list of user-friendly strings (2-5 items)
        plan_steps = result.get("plan_steps", [])
        if not isinstance(plan_steps, list):
            plan_steps = []
        # Ensure all entries are strings, cap at 5
        plan_steps = [str(s) for s in plan_steps if s][:5]

        department = result.get("department") or None
        if department:
            department = str(department).strip()

        # Follow-up context bridge: reuse_fields / changed_fields
        reuse_fields = None
        changed_fields = None
        if follow_up:
            raw_reuse = result.get("reuse_fields")
            raw_changed = result.get("changed_fields")
            if isinstance(raw_reuse, dict) and raw_reuse:
                reuse_fields = raw_reuse
            if isinstance(raw_changed, dict) and raw_changed:
                changed_fields = raw_changed

        # Multi-action detection
        is_multi_action = bool(result.get("is_multi_action", False))
        sub_requests = None
        if is_multi_action:
            raw_subs = result.get("sub_requests")
            if isinstance(raw_subs, list) and len(raw_subs) >= 2:
                valid_agents = _get_valid_agents()
                validated_subs = []
                for sr in raw_subs:
                    if not isinstance(sr, dict):
                        continue
                    sr_agent = sr.get("agent")
                    sr_action = sr.get("action")
                    sr_text = sr.get("text", "").strip()
                    if not sr_text:
                        continue
                    # Validate agent and action
                    if sr_agent not in valid_agents:
                        sr_agent = agent  # fallback to primary
                    if sr_action not in _VALID_ACTIONS:
                        sr_action = action  # fallback to primary
                    validated_subs.append({
                        "agent": sr_agent,
                        "action": sr_action,
                        "text": sr_text,
                        "description": str(sr.get("description", ""))[:100],
                    })
                if len(validated_subs) >= 2:
                    sub_requests = validated_subs
                else:
                    is_multi_action = False  # Not enough valid sub-requests
            else:
                is_multi_action = False  # Invalid or missing sub_requests

        analyzed = {
            "intent": intent,
            "agent": agent,
            "action": action,
            "confidence": confidence,
            "follow_up": follow_up,
            "plan_steps": plan_steps,
            "department": department,
            "is_multi_action": is_multi_action,
            "sub_requests": sub_requests,
            "reuse_fields": reuse_fields,
            "changed_fields": changed_fields,
        }

        # File context fallback: if keyword-based detection identified an agent but
        # the LLM returned low-confidence or no agent, trust the header detection.
        if file_context and file_context.get("detected_agent"):
            if not analyzed["agent"] or analyzed["confidence"] < 0.5:
                analyzed["agent"] = file_context["detected_agent"]
                analyzed["confidence"] = max(analyzed["confidence"], 0.7)
                if not analyzed["action"]:
                    analyzed["action"] = "create"
                # Derive intent from agent if it's still generic
                _agent_to_intent = {
                    "schedule": "schedule_crud",
                    "invoice": "invoice_crud",
                    "workorder": "workorder_crud",
                    "purchase_order": "purchase_order_crud",
                }
                if analyzed["intent"] == "general_query" and analyzed["agent"] in _agent_to_intent:
                    analyzed["intent"] = _agent_to_intent[analyzed["agent"]]
                logger.info(
                    f"🧠 Intent: file_context fallback applied → agent={analyzed['agent']}, "
                    f"confidence={analyzed['confidence']:.2f}"
                )

        logger.info(f"🧠 Intent: {analyzed}")
        return analyzed

    except json.JSONDecodeError as e:
        logger.warning(f"Intent analyzer: invalid JSON from LLM: {e}")
        return dict(_FALLBACK)
    except Exception as e:
        logger.warning(f"Intent analyzer: LLM call failed: {e}")
        return dict(_FALLBACK)
