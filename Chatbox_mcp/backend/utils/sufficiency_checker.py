"""
backend/utils/sufficiency_checker.py

Quick LLM check: do we have enough data to answer the user's question?
Port of mcp-client/utils/sufficiency-checker.js.

After each tool call, a cheap LLM check (~200 tokens in, ~50 tokens out)
evaluates whether we have sufficient data. This prevents:
  - Premature stopping: answering with incomplete data
  - Unnecessary tool calls: fetching more data than needed
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MIN_TOOL_CALLS_FOR_CHECK = 1
MAX_RESULT_SUMMARY_CHARS = 500

# Keywords that suggest complex queries needing a check after iter 1
_COMPLEX_KEYWORDS = (
    "compare", "versus", " vs ", "both", " and ",
    "breakdown", "analysis", "profitable", "which",
    "how much", "total",
)


def _find_data_array(result: Any) -> Optional[List]:
    """Find the main data array in a tool result."""
    if isinstance(result, list):
        return result
    if not isinstance(result, dict):
        return None

    best: Optional[List] = None
    best_len = 0
    for value in result.values():
        if isinstance(value, list) and len(value) > best_len:
            best = value
            best_len = len(value)

    return best


def _build_tool_summary(tool_results: List[Dict[str, Any]]) -> str:
    """Build a compact summary of tool results for the sufficiency check."""
    lines = []
    for tr in tool_results:
        name = tr.get("name") or "unknown_tool"
        result = tr.get("result") or tr.get("content") or {}

        # Parse content if it's a string
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except (json.JSONDecodeError, TypeError):
                pass

        # Error result
        if isinstance(result, dict) and (result.get("error") or result.get("success") is False):
            err = (result.get("error") or result.get("message") or "failed")
            if isinstance(err, str):
                err = err[:100]
            lines.append(f"{name}: ERROR — {err}")
            continue

        # Array result
        data = _find_data_array(result)
        if data is not None:
            sample_ids = [
                str(i.get("ID") or i.get("UID") or i.get("id"))
                for i in data[:3]
                if i.get("ID") or i.get("UID") or i.get("id")
            ]
            sample_fields = list(data[0].keys())[:8] if data else []
            id_str = f" (IDs: {', '.join(sample_ids)})" if sample_ids else ""
            field_str = f" [fields: {', '.join(sample_fields)}]" if sample_fields else ""
            lines.append(f"{name}: {len(data)} results{id_str}{field_str}")
            continue

        # Single object
        if isinstance(result, dict):
            obj_id = result.get("ID") or result.get("UID") or result.get("id")
            obj_name = result.get("Name") or result.get("DisplayName") or result.get("CompanyName")
            if obj_id:
                lines.append(f"{name}: single result ID={obj_id}{f' ({obj_name})' if obj_name else ''}")
                continue
            sub_arrays = [
                f"{k}: {len(v)} items"
                for k, v in result.items()
                if isinstance(v, list)
            ]
            if sub_arrays:
                lines.append(f"{name}: {', '.join(sub_arrays)}")
                continue

        lines.append(f"{name}: returned data")

    return "\n".join(lines)


async def check_sufficiency(
    user_question: str,
    tool_results: List[Dict[str, Any]],
    iteration: int,
    llm_chat_fn,          # async fn(messages, max_tokens=100) -> str
    plan_context: Optional[str] = None,  # pending_steps_summary() from RequestExecutionState
) -> Tuple[bool, str]:
    """
    Quick LLM check: is this data sufficient to answer the user's question?

    Returns (sufficient: bool, missing: str).
    sufficient=True means stop calling tools and compose the final answer.
    missing=str describes what data is still needed.

    `llm_chat_fn` is an async callable that takes a messages list and returns
    the LLM's text response (see usage in mcp_python_executor.py).

    `plan_context` is an optional one-liner from RequestExecutionState.pending_steps_summary()
    e.g. "1 of 2 steps complete. Pending: Step 2 (get_schedules for job 4521)"
    When provided, the checker uses it to avoid premature sufficient=True on multi-step plans.
    """
    if len(tool_results) < MIN_TOOL_CALLS_FOR_CHECK:
        return False, "First tool call — let LLM decide"

    tool_summary = _build_tool_summary(tool_results)

    plan_context_line = (
        f"\nExecution plan status: {plan_context}"
        "\nIMPORTANT: If plan steps are still pending, return sufficient=false unless the pending steps are clearly unnecessary for this question."
        if plan_context else ""
    )

    messages = [
        {
            "role": "system",
            "content": (
                "You evaluate whether tool results are SUFFICIENT to answer a user question.\n"
                "Reply with ONLY valid JSON: {\"_reasoning\": \"<think through: how many parts does the question have? what data was fetched? does it cover all parts?>\", \"sufficient\": true/false, \"missing\": \"what's still needed or empty string\"}\n"
                "Rules:\n"
                "- sufficient=true if the data clearly answers ALL PARTS of the question\n"
                "- sufficient=false if critical data is missing\n"
                "- For multi-part questions: need data for ALL parts\n"
                "- For 'show me X' queries: data listing is sufficient even without details\n"
                "- IMPORTANT: Qualifiers like department, trade, staff type (contractor/employee), and team "
                "are applied automatically downstream — do NOT treat these as missing data.\n\n"
                "EXAMPLES:\n"
                "Q: \"show me schedules for tomorrow\" | Results: get_schedules: 5 results [fields: ID, Staff, Date, Blocks]\n"
                "→ {\"_reasoning\": \"User wants schedule listing. We have schedule data with required fields.\", \"sufficient\": true, \"missing\": \"\"}\n\n"
                "Q: \"show me schedules AND invoices for job 123\" | Results: get_schedules: 5 results\n"
                "→ {\"_reasoning\": \"Two parts: schedules (fetched) and invoices (not yet fetched).\", \"sufficient\": false, \"missing\": \"invoices for job 123\"}\n\n"
                "Q: \"which staff worked most this week?\" | Results: get_schedules: 47 results [fields: Staff.ID, Staff.Name, Blocks, Date]\n"
                "→ {\"_reasoning\": \"Need to rank staff by total blocks. All schedule data is present to compute this.\", \"sufficient\": true, \"missing\": \"\"}"
            ),
        },
        {
            "role": "user",
            "content": (
                f"User question: \"{user_question[:200]}\"\n\n"
                f"Tool results so far (iteration {iteration}):\n"
                f"{tool_summary[:MAX_RESULT_SUMMARY_CHARS]}"
                f"{plan_context_line}\n\n"
                "Is this sufficient to answer the user's question?"
            ),
        },
    ]

    try:
        response_text = await llm_chat_fn(messages, max_tokens=200)
        text = response_text.strip()

        json_match = re.search(r"\{[\s\S]*\}", text)
        if json_match:
            parsed = json.loads(json_match.group(0))
            sufficient = bool(parsed.get("sufficient"))
            missing = parsed.get("missing") or ""
            logger.debug(
                f"[Sufficiency] iter={iteration}: {'SUFFICIENT' if sufficient else 'INSUFFICIENT'}"
                + (f" — missing: {missing}" if missing else "")
            )
            return sufficient, missing

        # Fallback parse
        lower = text.lower()
        if '"sufficient": true' in lower or lower.startswith("true"):
            return True, ""

        return False, "Could not parse sufficiency check"

    except Exception as e:
        logger.warning(f"[Sufficiency] Check failed: {e} — continuing loop")
        return False, "Check failed"


def should_check_sufficiency(
    user_message: str,
    current_iteration: int,
    total_tool_calls: int,
) -> bool:
    """
    Decide whether to run the sufficiency check for this iteration.
    Skip it for simple single-lookup queries to avoid latency overhead.
    """
    if current_iteration >= 2:
        return True

    if current_iteration >= 1 and total_tool_calls >= 1:
        msg_lower = user_message.lower()
        if any(kw in msg_lower for kw in _COMPLEX_KEYWORDS):
            return True

    return False
