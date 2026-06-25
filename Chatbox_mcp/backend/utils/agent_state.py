"""
backend/utils/agent_state.py

Request-scoped execution state for all agent paths
(schedule, invoice, workorder, purchase-order).

AgentExecutionState is created once at the entry point of each agent request
and threaded through every phase, row, resolution call, crossroads decision,
and tool call. It is never shared across requests — each agent invocation
gets its own instance as a local variable.

Responsibilities:
  - Phase tracking: parse → plan → resolve → execute → done
  - Entity resolution cache: cross-row deduplication (Staff/Job/Section/etc.)
  - Resolution attempt log: what was tried, what succeeded, what was ambiguous
  - Crossroads decision log: type, context, outcome, selected ID
  - Tool call log + runaway guard (same threshold as MCP path)
  - Clarification round log: fields, error types, affected rows
  - Per-row outcome map: "resolved" | "clarification" | "error"

Thread safety: Python async runs on a single event loop thread. Dict/list
mutations from coroutines are safe without locks. Two rows resolving the same
entity concurrently may both call the API before either writes the cache —
the second write is idempotent (same value). Acceptable cost.

Future tenancy: add user_id / tenant_id fields to AgentExecutionState.create()
and pass them through from chat.py → agent entry point. No structural change
required — the state is already request-scoped by construction.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# If any single tool is called this many times in one request, the agent
# should treat the row as unresolvable and surface an error — not retry.
_TOOL_RUNAWAY_THRESHOLD = 3


# ── Event types ───────────────────────────────────────────────────────────────

@dataclass
class AgentPhaseEvent:
    """One phase transition (enter or complete) in the agent lifecycle."""
    phase: str              # "parse", "plan", "resolve_row", "execute", "recovery", "clarify", "done"
    row_num: Optional[int]  # None for agent-level phases; row index for row-level phases
    status: str             # "started" | "complete" | "failed" | "clarification_needed"
    detail: str = ""        # human-readable summary (e.g. "12 rows", "strategy attempt 2")


@dataclass
class ResolutionAttempt:
    """One entity resolution attempt (API call, cache hit, or failure)."""
    entity_type: str            # "Staff", "Job", "Section", "CostCentre", "Contractor", "Supplier"
    input_value: str            # name exactly as provided (user input or extracted)
    outcome: str                # "resolved" | "ambiguous" | "not_found" | "cache_hit"
    resolved_id: Optional[Any] = None
    resolved_name: Optional[str] = None
    row_num: Optional[int] = None


@dataclass
class CrossroadsEvent:
    """One crossroads LLM decision."""
    crossroad_type: str         # "ambiguous_match", "ambiguous_schedule_identity", etc.
    question: str               # the question posed to crossroads
    outcome: str                # "selected" | "clarify" | "error"
    selected_id: Optional[Any] = None
    row_num: Optional[int] = None


@dataclass
class ToolCallEvent:
    """One MCP tool call."""
    tool_name: str
    outcome: str                # "success" | "error"
    result_summary: str         # compact description — never raw payload
    args_summary: str = ""      # compact key args (e.g. "date=2026-03-22, page_size=250")
    row_num: Optional[int] = None


@dataclass
class ClarificationEvent:
    """One clarification round surfaced to the user."""
    fields: List[str]           # which fields triggered clarification
    error_types: List[str]      # "ambiguous" | "missing" | "validation"
    row_nums: List[int]         # which rows are affected


# ── Main state class ──────────────────────────────────────────────────────────

@dataclass
class AgentExecutionState:
    """
    Request-scoped execution state for one agent invocation.

    Created via AgentExecutionState.create() or the create_agent_state() factory.
    Passed as an argument through every phase, row, and helper function.
    Never stored at module level — always a local variable.
    """
    request_id: str
    agent_name: str                             # "schedule" | "invoice" | "workorder" | "po"
    user_question: str

    # Phase tracking
    phases: List[AgentPhaseEvent] = field(default_factory=list)
    current_phase: str = "init"

    # Resolution
    resolution_attempts: List[ResolutionAttempt] = field(default_factory=list)
    entity_cache: Dict[str, Any] = field(default_factory=dict)
    # entity_cache key: "EntityType.InputValue" (case-preserved from input)
    # entity_cache value: {"id": <resolved_id>, "name": <resolved_name>}

    # Crossroads decisions
    crossroads_events: List[CrossroadsEvent] = field(default_factory=list)

    # Tool calls
    tool_calls: List[ToolCallEvent] = field(default_factory=list)
    tool_call_counts: Dict[str, int] = field(default_factory=dict)

    # Clarifications
    clarification_rounds: List[ClarificationEvent] = field(default_factory=list)

    # Per-row outcomes
    row_outcomes: Dict[int, str] = field(default_factory=dict)

    # ── Factory ──────────────────────────────────────────────────────────────

    @classmethod
    def create(
        cls,
        agent_name: str,
        user_question: str = "",
        user_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> "AgentExecutionState":
        """Create a fresh state for one agent request."""
        state = cls(
            request_id=uuid.uuid4().hex,
            agent_name=agent_name,
            user_question=user_question[:300] if user_question else "",
        )
        # user_id / tenant_id stored as phase detail for now;
        # promote to first-class fields when tenancy is implemented.
        if user_id or tenant_id:
            state.phases.append(AgentPhaseEvent(
                phase="init",
                row_num=None,
                status="started",
                detail=f"user={user_id} tenant={tenant_id}",
            ))
        return state

    # ── Phase tracking ────────────────────────────────────────────────────────

    def enter_phase(self, phase: str, row_num: Optional[int] = None, detail: str = "") -> None:
        self.current_phase = phase
        self.phases.append(AgentPhaseEvent(
            phase=phase, row_num=row_num, status="started", detail=detail
        ))
        _row_tag = f"(row {row_num})" if row_num is not None else ""
        logger.debug(f"[AgentState:{self.agent_name}] → {phase}{_row_tag} {detail}".strip())

    def complete_phase(self, phase: str, row_num: Optional[int] = None, detail: str = "") -> None:
        self.phases.append(AgentPhaseEvent(
            phase=phase, row_num=row_num, status="complete", detail=detail
        ))
        _row_tag = f"(row {row_num})" if row_num is not None else ""
        logger.debug(f"[AgentState:{self.agent_name}] ✓ {phase}{_row_tag} {detail}".strip())

    def fail_phase(self, phase: str, row_num: Optional[int] = None, detail: str = "") -> None:
        self.phases.append(AgentPhaseEvent(
            phase=phase, row_num=row_num, status="failed", detail=detail
        ))
        _row_tag = f"(row {row_num})" if row_num is not None else ""
        logger.debug(f"[AgentState:{self.agent_name}] ✗ {phase}{_row_tag} {detail}".strip())

    # ── Entity cache ──────────────────────────────────────────────────────────

    def cache_entity(
        self,
        entity_type: str,
        input_value: str,
        resolved_id: Any,
        resolved_name: str = "",
    ) -> None:
        """Store a resolved entity for cross-row reuse."""
        key = f"{entity_type}.{input_value}"
        self.entity_cache[key] = {"id": resolved_id, "name": resolved_name}
        logger.debug(
            f"[AgentState:{self.agent_name}] cached {key!r} → id={resolved_id}"
        )

    def get_entity(self, entity_type: str, input_value: str) -> Optional[Dict[str, Any]]:
        """
        Return cached entity dict {"id": ..., "name": ...} or None.
        Logs cache hits at DEBUG level.
        """
        key = f"{entity_type}.{input_value}"
        result = self.entity_cache.get(key)
        if result is not None:
            logger.debug(
                f"[AgentState:{self.agent_name}] cache hit {key!r} → id={result['id']}"
            )
        return result

    # ── Resolution logging ────────────────────────────────────────────────────

    def log_resolution(
        self,
        entity_type: str,
        input_value: str,
        resolved_id: Optional[Any] = None,
        resolved_name: Optional[str] = None,
        outcome: str = "resolved",
        row_num: Optional[int] = None,
    ) -> None:
        self.resolution_attempts.append(ResolutionAttempt(
            entity_type=entity_type,
            input_value=input_value,
            outcome=outcome,
            resolved_id=resolved_id,
            resolved_name=resolved_name,
            row_num=row_num,
        ))
        _row_tag = f" row={row_num}" if row_num is not None else ""
        logger.debug(
            f"[AgentState:{self.agent_name}] resolve {entity_type}.{input_value!r}"
            f" → {outcome}{_row_tag}"
            + (f" id={resolved_id}" if resolved_id is not None else "")
        )

    # ── Crossroads logging ────────────────────────────────────────────────────

    def log_crossroads(
        self,
        crossroad_type: str,
        question: str,
        outcome: str,
        selected_id: Optional[Any] = None,
        row_num: Optional[int] = None,
    ) -> None:
        self.crossroads_events.append(CrossroadsEvent(
            crossroad_type=crossroad_type,
            question=question[:200],
            outcome=outcome,
            selected_id=selected_id,
            row_num=row_num,
        ))
        _row_tag = f" row={row_num}" if row_num is not None else ""
        logger.info(
            f"[AgentState:{self.agent_name}] crossroads {crossroad_type}"
            f" → {outcome}{_row_tag}"
            + (f" selected_id={selected_id}" if selected_id is not None else "")
        )

    # ── Tool call logging + runaway guard ─────────────────────────────────────

    def log_tool_call(
        self,
        tool_name: str,
        outcome: str,
        result_summary: str,
        args_summary: str = "",
        row_num: Optional[int] = None,
    ) -> None:
        self.tool_calls.append(ToolCallEvent(
            tool_name=tool_name,
            outcome=outcome,
            result_summary=result_summary[:200],
            args_summary=args_summary[:200],
            row_num=row_num,
        ))
        self.tool_call_counts[tool_name] = self.tool_call_counts.get(tool_name, 0) + 1
        count = self.tool_call_counts[tool_name]
        _row_tag = f" row={row_num}" if row_num is not None else ""
        logger.debug(
            f"[AgentState:{self.agent_name}] tool {tool_name}"
            f" → {outcome}{_row_tag} (call #{count})"
        )
        if count >= _TOOL_RUNAWAY_THRESHOLD:
            logger.warning(
                f"[AgentState:{self.agent_name}] Tool '{tool_name}' called {count} times"
                f" — force_stop threshold reached"
            )

    def should_force_stop(self, tool_name: Optional[str] = None) -> bool:
        """
        True if any tool (or a specific tool) has been called ≥ threshold times.
        Agents should check this before retrying a failing tool.
        """
        if tool_name:
            return self.tool_call_counts.get(tool_name, 0) >= _TOOL_RUNAWAY_THRESHOLD
        return any(
            count >= _TOOL_RUNAWAY_THRESHOLD
            for count in self.tool_call_counts.values()
        )

    # ── Clarification tracking ────────────────────────────────────────────────

    def log_clarification(
        self,
        fields: List[str],
        error_types: List[str],
        row_nums: List[int],
    ) -> None:
        self.clarification_rounds.append(ClarificationEvent(
            fields=fields,
            error_types=error_types,
            row_nums=row_nums,
        ))
        logger.info(
            f"[AgentState:{self.agent_name}] clarification round {len(self.clarification_rounds)}"
            f": fields={fields} types={error_types} rows={row_nums}"
        )

    # ── Row outcomes ──────────────────────────────────────────────────────────

    def set_row_outcome(self, row_num: int, outcome: str) -> None:
        """outcome: 'resolved' | 'clarification' | 'error' | 'skipped'"""
        self.row_outcomes[row_num] = outcome

    # ── Summary ───────────────────────────────────────────────────────────────

    def summary(self) -> str:
        """
        Compact one-line summary for end-of-request logging.
        e.g. "schedule | 12 rows: 10 resolved 1 clarification 1 error | 6 tool calls | 2 crossroads | 1 clarification round"
        """
        total_rows = len(self.row_outcomes)
        resolved = sum(1 for v in self.row_outcomes.values() if v == "resolved")
        clarif = sum(1 for v in self.row_outcomes.values() if v == "clarification")
        errors = sum(1 for v in self.row_outcomes.values() if v == "error")
        row_summary = (
            f"{total_rows} rows: {resolved} resolved"
            + (f" {clarif} clarification" if clarif else "")
            + (f" {errors} error" if errors else "")
        ) if total_rows else "no rows"

        cache_hits = sum(
            1 for a in self.resolution_attempts if a.outcome == "cache_hit"
        )
        cache_str = f" ({cache_hits} cache hits)" if cache_hits else ""

        return (
            f"{self.agent_name} | {row_summary}"
            f" | {len(self.tool_calls)} tool calls{cache_str}"
            + (f" | {len(self.crossroads_events)} crossroads" if self.crossroads_events else "")
            + (f" | {len(self.clarification_rounds)} clarification round(s)" if self.clarification_rounds else "")
        )


# ── Factory helper ────────────────────────────────────────────────────────────

def create_agent_state(
    agent_name: str,
    user_question: str = "",
    user_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> AgentExecutionState:
    """
    Create a fresh AgentExecutionState for one agent request.

    Usage in agent entry points:
        from utils.agent_state import create_agent_state
        state = create_agent_state("schedule", user_text)
    """
    return AgentExecutionState.create(
        agent_name=agent_name,
        user_question=user_question,
        user_id=user_id,
        tenant_id=tenant_id,
    )
