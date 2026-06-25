"""
backend/utils/request_state.py

Intra-request execution state for the MCP path and agent path.

RequestExecutionState — threaded through the PythonMCPExecutor loop:
  - Parsed plan steps from query_planner output
  - Completed step tracking (drives plan-aware sufficiency termination)
  - Per-request entity resolution cache (avoids redundant API lookups)
  - Per-tool call counter (runaway loop guard)
  - Dynamic iteration cap (max(DEFAULT_MAX, plan_steps * 2))

AgentRequestState — threaded through agent row-processing loops:
  - Cross-row entity cache (Staff/Job/Customer/Contractor name → ID)
  - One instance per agent request, shared across all rows

Neither class persists across requests. Both are plain dataclasses with no
external dependencies — safe to instantiate anywhere, zero overhead when
no plan is available (empty / no-op state).
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Hard ceiling on effective_max_iterations regardless of plan size.
# Prevents a runaway planner from setting an absurdly large loop cap.
_ABSOLUTE_MAX_ITERATIONS = 20

# Default iteration cap when no plan (or empty plan) is available.
_DEFAULT_MAX_ITERATIONS = 10

# If any single tool has been called this many times *successfully* in one
# request, force the loop to stop and synthesize — runaway guard.
# Validation failures (bad LLM params) are NOT counted toward this threshold.
_TOOL_CALL_RUNAWAY_THRESHOLD = 5


@dataclass
class RequestExecutionState:
    """
    Tracks execution state for one MCP path request (PythonMCPExecutor).
    Created once per request in _run_pre_loop(), stored on self._exec_state.
    """
    request_id: str
    user_question: str
    plan_steps: List[Dict[str, Any]] = field(default_factory=list)
    completed_steps: List[int] = field(default_factory=list)
    resolved_entities: Dict[str, Any] = field(default_factory=dict)
    step_summaries: List[str] = field(default_factory=list)
    tool_call_counts: Dict[str, int] = field(default_factory=dict)
    effective_max_iterations: int = _DEFAULT_MAX_ITERATIONS

    # ── Lifecycle ────────────────────────────────────────────────────────────

    @classmethod
    def empty(cls, user_question: str = "") -> "RequestExecutionState":
        """Create a no-op state when no plan is available."""
        return cls(
            request_id=uuid.uuid4().hex,
            user_question=user_question,
            plan_steps=[],
            effective_max_iterations=_DEFAULT_MAX_ITERATIONS,
        )

    @classmethod
    def from_plan(cls, plan_hint: str, user_question: str) -> "RequestExecutionState":
        """
        Parse plan steps from the query_planner hint string and build state.

        Plan hint format (from query_planner.py):
          "Step N: description → use: tool_name with args: {...}"

        effective_max_iterations = min(max(DEFAULT, steps * 2), ABSOLUTE_MAX)
        """
        steps = _parse_plan_steps(plan_hint)
        n = len(steps)
        effective_max = min(
            max(_DEFAULT_MAX_ITERATIONS, n * 2),
            _ABSOLUTE_MAX_ITERATIONS,
        )
        if n > 0:
            logger.debug(
                f"[ExecState] Plan has {n} step(s) — effective_max={effective_max}"
            )
        return cls(
            request_id=uuid.uuid4().hex,
            user_question=user_question,
            plan_steps=steps,
            effective_max_iterations=effective_max,
        )

    # ── Step tracking ────────────────────────────────────────────────────────

    def mark_step_complete(self, step_num: int, summary: str = "") -> None:
        if step_num not in self.completed_steps:
            self.completed_steps.append(step_num)
        if summary:
            self.step_summaries.append(f"Step {step_num}: {summary}")
        logger.debug(
            f"[ExecState] Step {step_num} complete "
            f"({len(self.completed_steps)}/{len(self.plan_steps)} total)"
        )

    def all_steps_complete(self) -> bool:
        """True when every planned step has been marked complete."""
        if not self.plan_steps:
            return False  # No plan — don't pretend we're done
        planned_nums = {s.get("step", i + 1) for i, s in enumerate(self.plan_steps)}
        return planned_nums.issubset(set(self.completed_steps))

    def pending_steps_summary(self) -> str:
        """
        One-liner for the sufficiency checker prompt:
        "2 of 3 steps complete. Pending: Step 3 (get_schedules for job 4521)"
        """
        if not self.plan_steps:
            return ""
        planned_nums = {s.get("step", i + 1): s for i, s in enumerate(self.plan_steps)}
        pending = {
            num: step
            for num, step in planned_nums.items()
            if num not in self.completed_steps
        }
        total = len(planned_nums)
        done = total - len(pending)
        if not pending:
            return f"All {total} step(s) complete."
        pending_descs = [
            f"Step {num} ({step.get('task', step.get('tools', ['?'])[0] if step.get('tools') else '?')})"
            for num, step in sorted(pending.items())
        ]
        return f"{done} of {total} step(s) complete. Pending: {', '.join(pending_descs)}"

    # ── Runaway guard ────────────────────────────────────────────────────────

    def record_tool_call(self, tool_name: str) -> None:
        self.tool_call_counts[tool_name] = self.tool_call_counts.get(tool_name, 0) + 1
        count = self.tool_call_counts[tool_name]
        if count >= _TOOL_CALL_RUNAWAY_THRESHOLD:
            logger.warning(
                f"[ExecState] Tool '{tool_name}' called {count} time(s) "
                f"— forcing stop at {_TOOL_CALL_RUNAWAY_THRESHOLD}"
            )

    def should_force_stop(self) -> bool:
        """True if any single tool has been called ≥ threshold times."""
        return any(
            count >= _TOOL_CALL_RUNAWAY_THRESHOLD
            for count in self.tool_call_counts.values()
        )

    # ── Entity cache ─────────────────────────────────────────────────────────

    def cache_entity(self, key: str, value: Any) -> None:
        """
        Cache a resolved entity. Key format: "EntityType.InputValue"
        e.g. "Staff.Mark Johnson", "Job.Office Block", "Customer.Smith Co"
        """
        self.resolved_entities[key] = value
        logger.debug(f"[ExecState] Cached entity: {key!r} → {value!r}")

    def get_entity(self, key: str) -> Optional[Any]:
        """Return cached entity or None if not cached."""
        value = self.resolved_entities.get(key)
        if value is not None:
            logger.debug(f"[ExecState] Cache hit: {key!r} → {value!r}")
        return value


@dataclass
class AgentRequestState:
    """
    Cross-row entity cache for agent path (schedule, invoice, workorder, PO).
    Created once per agent request, passed down to per-row processing functions.

    entity_cache key format: "EntityType.InputValue"
    entity_cache value: resolved dict {"id": <int>, "name": <str>}
    """
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    entity_cache: Dict[str, Any] = field(default_factory=dict)

    def get(self, key: str) -> Optional[Any]:
        value = self.entity_cache.get(key)
        if value is not None:
            logger.debug(f"[AgentState] Cache hit: {key!r} → {value!r}")
        return value

    def put(self, key: str, value: Any) -> None:
        self.entity_cache[key] = value
        logger.debug(f"[AgentState] Cached: {key!r} → {value!r}")


# ── Internal helpers ──────────────────────────────────────────────────────────

def _parse_plan_steps(plan_hint: str) -> List[Dict[str, Any]]:
    """
    Extract step entries from query_planner hint string.

    Expected format (from query_planner.py line 161):
      "  Step N: description → use: tool_name with args: {...}"

    Returns list of dicts: [{"step": N, "task": "...", "tools": ["tool_name"]}, ...]
    """
    if not plan_hint:
        return []

    steps = []
    # Match lines like "  Step 1: fetch jobs → use: list_jobs with args: {...}"
    pattern = re.compile(
        r"Step\s+(\d+):\s+(.+?)(?:\s+→\s+use:\s+([^\s{]+))?(?:\s+with\s+args:.+)?$",
        re.IGNORECASE,
    )
    for line in plan_hint.splitlines():
        m = pattern.search(line.strip())
        if m:
            step_num = int(m.group(1))
            task = m.group(2).strip() if m.group(2) else ""
            tool = m.group(3).strip() if m.group(3) else ""
            steps.append({
                "step": step_num,
                "task": task,
                "tools": [tool] if tool else [],
            })

    return steps
