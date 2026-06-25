# backend/utils/multi_action_orchestrator.py
"""
Multi-Action Orchestrator.

When the intent analyzer detects multiple independent CRUD operations in a
single user message (e.g., "schedule Nick on 2 jobs" or "create schedule AND
create work order"), this module runs each sub-request through _run_agent()
in parallel and merges the results.

Each sub-request is fully standalone — the intent analyzer already merged
any shared/common context (staff name, date, etc.) into each sub-request's
text field.
"""

from __future__ import annotations
import asyncio
import logging
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from utils.history_filter import filter_history

logger = logging.getLogger(__name__)


async def orchestrate_multi_action(
    sub_requests: List[Dict[str, Any]],
    original_message: str,
    attachments: List[Dict[str, Any]],
    current_user: Optional[Dict[str, Any]],
    effective_history: List[Dict[str, str]],
    accumulator: Any,  # _TokenAccumulator from chat.py
    user_context: Dict[str, Any],
    run_agent_fn: Callable,  # _run_agent from chat.py
    org_id: Optional[int] = None,
    intent_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Run multiple independent sub-requests in parallel and merge results.

    Args:
        sub_requests: List of dicts with {agent, action, text, description}.
        original_message: The original user message (for history).
        attachments: File attachments (passed to first sub-request only if any).
        current_user: Auth user dict.
        effective_history: Conversation history (gated).
        accumulator: Token accumulator for tracking LLM usage.
        user_context: Per-user session state dict.
        run_agent_fn: The _run_agent function from chat.py.
        org_id: User's org ID for agent gating.
        intent_result: The full intent result (for follow_up flag, etc.).

    Returns:
        Merged result dict compatible with existing chat.py result handling.
    """
    n = len(sub_requests)
    logger.info(f"🔀 Multi-action orchestrator: {n} sub-requests")
    for i, sr in enumerate(sub_requests):
        logger.info(f"  [{i+1}/{n}] agent={sr['agent']}, action={sr['action']}, text={sr['text'][:80]}")

    # Check agent availability
    from auth.database import is_agent_enabled_for_org, get_org_agent_plan, get_monthly_agent_usage
    from agents.registry import load_agent
    from datetime import datetime, timedelta, timezone
    for sr in sub_requests:
        agent_name = sr.get("agent")
        if not agent_name or not load_agent(agent_name):
            return {
                "success": False,
                "message": f"Agent '{agent_name}' is not available.",
            }
        if org_id and not is_agent_enabled_for_org(org_id, agent_name):
            return {
                "success": False,
                "message": f"The {agent_name} agent is not available on your current plan.",
            }
        # Per-agent token budget check
        if org_id:
            plan = get_org_agent_plan(org_id, agent_name)
            if plan and plan.get("monthly_token_limit") is not None:
                now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
                usage = get_monthly_agent_usage(org_id, agent_name, now_ist.year, now_ist.month)
                total_used = usage["total_input_tokens"] + usage["total_output_tokens"]
                if total_used >= plan["monthly_token_limit"]:
                    return {
                        "success": False,
                        "message": (
                            f"The {agent_name} agent has reached its monthly token limit "
                            f"({total_used:,}/{plan['monthly_token_limit']:,} tokens). "
                            f"Please contact your administrator to upgrade."
                        ),
                    }

    # Validate each sub-request independently for contradictions
    from utils.input_validator import validate_user_input
    for i, sr in enumerate(sub_requests):
        contradiction = await validate_user_input(
            user_message=sr["text"],
            detected_route=sr["agent"],
            llm_chat=accumulator.tracked_chat,
            conversation_history=effective_history,
        )
        if contradiction:
            logger.info(f"⚠️  Sub-request {i+1} has contradiction")
            return {
                "reply": f"Action {i+1} ({sr['description']}) has conflicting information. Please clarify:",
                "needs_clarification": True,
                "clarification_data": contradiction["clarification_data"],
                "metadata": {"agent": sr["agent"]},
            }

    is_follow_up = bool(intent_result.get("follow_up")) if intent_result else False

    # Build session context helper
    def _build_session_ctx():
        """Minimal session context for sub-requests."""
        ctx = user_context.get("last_structured_data")
        if ctx and is_follow_up:
            return {
                "route": user_context.get("last_route"),
                "department": user_context.get("last_department"),
                "structured_data": ctx,
            }
        return None

    session_ctx = _build_session_ctx()

    # Run all sub-requests in parallel
    async def _run_sub(idx: int, sr: Dict[str, Any]) -> Dict[str, Any]:
        """Run a single sub-request through _run_agent."""
        logger.info(f"▶️  Sub-request [{idx+1}/{n}] starting: {sr['description']}")
        try:
            result = await run_agent_fn(
                agent_name=sr["agent"],
                user_text=sr["text"],
                attachments=[],  # attachments not split across sub-requests
                conversation_history=filter_history(effective_history, sr["agent"]),
                intent_action=sr.get("action"),
                intent_follow_up=is_follow_up,
                current_user=current_user,
                llm_chat_fn=accumulator.tracked_chat,
                session_context=session_ctx,
            )
            result["_sub_index"] = idx
            result["_sub_description"] = sr.get("description", f"Action {idx+1}")
            result["_sub_agent"] = sr["agent"]
            logger.info(f"✅ Sub-request [{idx+1}/{n}] done: success={result.get('success')}")
            return result
        except Exception as e:
            logger.error(f"❌ Sub-request [{idx+1}/{n}] error: {e}", exc_info=True)
            return {
                "success": False,
                "message": f"Action {idx+1} failed: {e}",
                "_sub_index": idx,
                "_sub_description": sr.get("description", f"Action {idx+1}"),
                "_sub_agent": sr["agent"],
            }

    results = await asyncio.gather(*[_run_sub(i, sr) for i, sr in enumerate(sub_requests)])

    # Categorize results into clarification / success / failure
    clarification_results = []
    success_results = []
    failure_results = []
    for result in results:
        if result.get("needs_clarification"):
            clarification_results.append(result)
        elif result.get("success"):
            success_results.append(result)
        else:
            failure_results.append(result)

    # If any need clarification, return the first one but also report
    # the status of ALL other sub-requests so nothing is silently lost
    if clarification_results:
        first_clar = clarification_results[0]
        idx = first_clar.get("_sub_index", 0)
        desc = first_clar.get("_sub_description", f"Action {idx+1}")
        logger.info(f"❓ Sub-request [{idx+1}] needs clarification")

        # Build status summary for ALL sub-requests
        all_statuses = []
        for r in results:
            r_idx = r.get("_sub_index", 0)
            r_desc = r.get("_sub_description", f"Action {r_idx+1}")
            if r.get("needs_clarification"):
                all_statuses.append(f"Action {r_idx+1} ({r_desc}): needs your input")
            elif r.get("success"):
                all_statuses.append(f"Action {r_idx+1} ({r_desc}): completed successfully")
            else:
                error = r.get("message", r.get("error", "failed"))
                all_statuses.append(f"Action {r_idx+1} ({r_desc}): {error}")

        # Collect remaining clarification session IDs for chaining
        remaining_clarification_sids = []
        for cr in clarification_results[1:]:
            cr_sid = cr.get("session_id")
            if cr_sid:
                remaining_clarification_sids.append({
                    "session_id": cr_sid,
                    "sub_index": cr.get("_sub_index"),
                    "description": cr.get("_sub_description", f"Action {cr.get('_sub_index', 0)+1}"),
                    "agent": cr.get("_sub_agent", "schedule"),
                })

        # Build sub_descriptions map for all sub-requests
        sub_descriptions = {}
        for r in sorted(results, key=lambda x: x.get("_sub_index", 0)):
            sub_descriptions[r.get("_sub_index", 0)] = r.get("_sub_description", f"Action {r.get('_sub_index', 0)+1}")

        # Tag clarification data with multi-action context
        clar_data = {k: v for k, v in first_clar.items() if not k.startswith("_sub_")}
        clar_data["multi_action_context"] = {
            "sub_index": idx,
            "description": desc,
            "total_sub_requests": n,
            "all_statuses": all_statuses,
            # Chaining data: remaining clarifications + parallel results
            "remaining_clarification_sids": remaining_clarification_sids,
            "completed_results": [
                {
                    "sub_index": r.get("_sub_index"),
                    "description": r.get("_sub_description", f"Action {r.get('_sub_index', 0)+1}"),
                    "success": True,
                    "source": "parallel",
                    "result_data": {k: v for k, v in r.items() if not k.startswith("_sub_")},
                }
                for r in success_results
            ],
            "failed_results": [
                {
                    "sub_index": r.get("_sub_index"),
                    "description": r.get("_sub_description", f"Action {r.get('_sub_index', 0)+1}"),
                    "success": False,
                    "error": r.get("message", r.get("error", "failed")),
                    "source": "parallel",
                    "result_data": {k: v for k, v in r.items() if not k.startswith("_sub_")},
                }
                for r in failure_results
            ],
            "sub_descriptions": sub_descriptions,
        }
        logger.info(
            f"📋 Multi-action clarification: {len(remaining_clarification_sids)} remaining "
            f"after current, {len(success_results)} succeeded, {len(failure_results)} failed"
        )

        # Build reply showing clarification + status of other sub-requests
        reply_parts = [f"For action {idx+1} of {n} ({desc}): {first_clar.get('message', 'Please provide additional information.')}"]
        for status in all_statuses:
            # Skip the current clarification sub-request (already shown above)
            if f"Action {idx+1}" not in status:
                reply_parts.append(status)

        return {
            "reply": "\n".join(reply_parts),
            "needs_clarification": True,
            "clarification_data": clar_data,
            "metadata": {"agent": first_clar.get("_sub_agent", "chat")},
        }

    # Merge all successful results
    return _merge_results(results, sub_requests, original_message)


def _merge_results(
    results: List[Dict[str, Any]],
    sub_requests: List[Dict[str, Any]],
    original_message: str,
) -> Dict[str, Any]:
    """Merge results from multiple parallel agent runs into a unified response."""
    n = len(results)
    succeeded = sum(1 for r in results if r.get("success"))
    failed = n - succeeded

    all_success = succeeded == n
    any_success = succeeded > 0

    # Build per-sub-request result summaries
    multi_results = []
    all_items = []  # Flattened list of all created/updated/deleted items

    for i, result in enumerate(results):
        sr = sub_requests[i]
        sub_summary = {
            "index": i,
            "agent": sr["agent"],
            "action": sr["action"],
            "description": sr.get("description", f"Action {i+1}"),
            "success": result.get("success", False),
        }

        # Extract execution summary if available
        if result.get("summary"):
            sub_summary["summary"] = result["summary"]

        # Collect all result items (created schedules, invoices, work orders, etc.)
        for key in ("results", "created", "updated", "deleted"):
            items = result.get(key, [])
            if items:
                for item in items:
                    item["_from_action"] = i + 1
                    item["_action_description"] = sr.get("description", "")
                all_items.extend(items)

        if result.get("failed"):
            sub_summary["failed"] = result["failed"]

        if not result.get("success") and result.get("message"):
            sub_summary["error"] = result["message"]

        multi_results.append(sub_summary)

    # Build message
    if all_success:
        message = f"All {n} operations completed successfully."
    elif any_success:
        message = f"{succeeded} of {n} operations completed. {failed} failed."
    else:
        message = f"All {n} operations failed."

    # Build detailed message lines
    detail_lines = []
    for mr in multi_results:
        status = "completed" if mr["success"] else "FAILED"
        detail_lines.append(f"  {mr['index']+1}. {mr['description']} — {status}")
        if mr.get("error"):
            detail_lines.append(f"     Error: {mr['error']}")

    merged = {
        "success": all_success,
        "is_multi_action": True,
        "multi_action_results": multi_results,
        "results": all_items if all_items else None,
        "summary": {
            "total": n,
            "succeeded": succeeded,
            "failed": failed,
        },
        "message": message + "\n" + "\n".join(detail_lines),
    }

    # If all sub-requests are the same agent, set agent-specific keys
    # so the presenter and frontend can render them correctly
    agents = list({sr["agent"] for sr in sub_requests})
    if len(agents) == 1:
        merged["agent"] = agents[0]

    logger.info(
        f"🔀 Multi-action merged: {succeeded}/{n} succeeded, "
        f"{len(all_items)} total items"
    )

    return merged
