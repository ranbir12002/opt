# backend/utils/resolution_context.py
"""
Resolution Context Tracker for Crossroads.

Contains:
- RequestTracker: tracks tool execution history + user context for an entire request
- ResolutionContext: tracks state across resolution attempts for a single row/operation

Used by agents to provide full context to crossroads decisions.
"""
from __future__ import annotations

import time
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_RESOLUTION_ATTEMPTS = 3

# Fields that are safe to pass to crossroads (IDs, types, references — no PII)
_SAFE_SCHEDULE_FIELDS = {
    "ID", "Type", "Reference", "Date", "Staff",
    "Blocks", "StartTime", "EndTime", "Hrs",
}


def _sanitize_schedule_for_context(schedule: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract only ID-level, non-PII fields from a schedule for crossroads context.
    Keeps: ID, Type, Reference, Staff.ID, Blocks (start/end/hrs).
    Removes: customer names, notes, addresses, etc.
    """
    safe = {
        "id": schedule.get("ID"),
        "type": schedule.get("Type"),
        "reference": schedule.get("Reference"),
    }
    staff = schedule.get("Staff", {})
    if staff:
        safe["staff_id"] = staff.get("ID")
        # Include staff name so crossroads can build meaningful options
        staff_name = (staff.get("Name") or "").strip()
        if not staff_name:
            given = (staff.get("GivenName") or "").strip()
            family = (staff.get("FamilyName") or "").strip()
            staff_name = f"{given} {family}".strip()
        if staff_name:
            safe["staff_name"] = staff_name
    blocks = schedule.get("Blocks", [])
    if blocks:
        safe["blocks"] = [
            {
                "start_time": b.get("StartTime"),
                "end_time": b.get("EndTime"),
                "hrs": b.get("Hrs"),
            }
            for b in blocks[:3]  # Limit to first 3 blocks
        ]
    return safe


class RequestTracker:
    """
    Tracks tool execution history + user context for a single user request.

    Created once per user message, passed through the agent pipeline,
    and injected into crossroads calls for full situational awareness.

    Usage:
        tracker = RequestTracker(
            user_question="delete stephen's schedule for tomorrow",
            conversation_history=[...],
        )
        # After each tool call:
        tracker.record_tool_call("list_employees", {"columns": "ID,Name"}, result, True)

        # When calling crossroads:
        context.update(tracker.to_crossroads_context())
    """

    def __init__(
        self,
        user_question: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ):
        self.user_question = user_question
        self.conversation_history = (conversation_history or [])[-6:]
        self.tool_calls: List[Dict[str, Any]] = []

    def record_tool_call(
        self,
        tool: str,
        params: Dict[str, Any],
        result: Any,
        success: bool,
    ):
        """Record a tool call with a sanitized result summary."""
        self.tool_calls.append({
            "tool": tool,
            "params": _sanitize_params(params),
            "success": success,
            "result_summary": _summarize_tool_result(tool, result),
            "ts": time.time(),
        })

    def to_crossroads_context(self) -> Dict[str, Any]:
        """Build context dict for injection into crossroads calls."""
        return {
            "_user_question": self.user_question,
            "_conversation_summary": self._summarize_history(),
            "_tool_history": self.tool_calls[-10:],
        }

    def _summarize_history(self) -> List[Dict[str, str]]:
        """Return last few conversation turns, stripped of PII."""
        summary = []
        for turn in self.conversation_history[-4:]:
            role = turn.get("role", "user")
            content = turn.get("content", "")
            # Keep only first 200 chars of each turn
            summary.append({"role": role, "content": content[:200]})
        return summary


def _sanitize_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """Remove potentially large or PII-containing param values."""
    safe = {}
    for k, v in params.items():
        if isinstance(v, str) and len(v) > 100:
            safe[k] = v[:50] + "..."
        else:
            safe[k] = v
    return safe


def _summarize_tool_result(tool: str, result: Any) -> str:
    """Create a brief summary of a tool result for logging context."""
    if not isinstance(result, dict):
        return str(result)[:80]

    # Common patterns in MCP tool results
    for key in ("employees", "contractors", "schedules", "jobs",
                "sections", "cost_centres", "contacts", "invoices"):
        items = result.get(key)
        if isinstance(items, list):
            ids = [item.get("ID") for item in items[:5] if isinstance(item, dict)]
            return f"{len(items)} {key} returned (IDs: {ids})"

    # Single item result
    if "ID" in result:
        return f"Item ID={result['ID']}"

    return f"Keys: {list(result.keys())[:5]}"


class ResolutionContext:
    """
    Tracks resolution state for a single stuck point.

    Usage:
        ctx = ResolutionContext(
            stuck_point="Staff name 'stephen' cannot be matched to any schedule",
            operation="DELETE",
            row_data={"StaffName": "stephen", "Date": "2026-02-14"},
        )
        ctx.record_collected("staff_name", "stephen", via="user_input")
        ctx.add_partial_data("schedules_found", [...])

        # After a failed strategy:
        ctx.record_failure("match_by_name", "Simpro schedules don't return staff names")

        # Build context for crossroads:
        cr_context = ctx.to_crossroads_context(available_tools)
    """

    def __init__(
        self,
        stuck_point: str,
        operation: str,
        row_data: Optional[Dict[str, Any]] = None,
    ):
        self.stuck_point = stuck_point
        self.operation = operation
        self.row_data_keys = list((row_data or {}).keys())  # Only keys, no PII values
        self.collected: Dict[str, Any] = {}  # field → value (IDs only, no PII)
        self.failed_attempts: List[Dict[str, str]] = []
        self.partial_data: Dict[str, Any] = {}  # Key → sanitized API data from failed calls
        self.attempt_count = 0

    @property
    def exhausted(self) -> bool:
        return self.attempt_count >= MAX_RESOLUTION_ATTEMPTS

    def record_collected(self, field: str, value: Any, via: str = ""):
        """Record a successfully resolved field."""
        self.collected[field] = value
        logger.debug(f"ResolutionContext: collected {field}={value} (via {via})")

    def record_failure(self, strategy_desc: str, error: str):
        """Record a failed strategy attempt."""
        self.failed_attempts.append({
            "strategy": strategy_desc,
            "error": error,
        })
        self.attempt_count += 1
        logger.info(
            f"ResolutionContext: attempt {self.attempt_count}/{MAX_RESOLUTION_ATTEMPTS} "
            f"failed — {strategy_desc}: {error}"
        )

    def add_partial_data(self, key: str, data: Any):
        """
        Store sanitized partial API data for crossroads context.

        Example: if get_schedules returned 5 schedules but none matched,
        store their IDs/types/references so crossroads can reason about them.
        """
        if isinstance(data, list):
            # Sanitize schedule-like dicts
            sanitized = []
            for item in data[:10]:  # Limit to 10 items
                if isinstance(item, dict):
                    if "Staff" in item or "Blocks" in item or "Reference" in item:
                        sanitized.append(_sanitize_schedule_for_context(item))
                    else:
                        # Generic: keep only ID-like fields
                        sanitized.append({
                            k: v for k, v in item.items()
                            if k in ("ID", "id", "Name", "name", "Type", "type",
                                     "Reference", "reference", "GivenName", "FamilyName")
                        })
            self.partial_data[key] = sanitized
        else:
            self.partial_data[key] = data
        logger.debug(f"ResolutionContext: added partial data '{key}' ({type(data).__name__})")

    def to_crossroads_context(
        self, available_tools: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Build PII-safe context dict for crossroads "resolution" type.

        Includes:
        - stuck_point: error message describing what failed
        - collected_data: IDs and values resolved so far
        - partial_data: sanitized API results from the call that failed
        - failed_attempts: what strategies were tried and why they failed
        - available_tools: full tool catalog with descriptions and param schemas
        """
        return {
            "stuck_point": self.stuck_point,
            "operation": self.operation,
            "row_fields": self.row_data_keys,
            "collected_data": self.collected,
            "partial_data": self.partial_data,
            "failed_attempts": self.failed_attempts,
            "attempt_number": self.attempt_count + 1,
            "max_attempts": MAX_RESOLUTION_ATTEMPTS,
            "available_tools": available_tools,
        }
