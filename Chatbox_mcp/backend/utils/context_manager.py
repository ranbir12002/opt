# backend/utils/context_manager.py
# ──────────────────────────────────────────────────────────────
# Phase 2: Conversation Summarizer + Session Scratchpad
#
# Two complementary systems for managing LLM context efficiently:
#
# 1. RUNNING SUMMARY — When conversation history exceeds a threshold,
#    older entries are compressed into a progressive summary.  Uses a
#    cheap/fast LLM call.  Keeps recent entries verbatim.
#
# 2. SESSION SCRATCHPAD — Rule-based extraction of durable facts from
#    agent results (resolved entity IDs, decisions, preferences).
#    No LLM call needed — parses structured agent output.
#    Prepended to every LLM call as compact context (~100-200 tokens).
#
# Both systems are per-user, stored in the existing _user_contexts dict.
# ──────────────────────────────────────────────────────────────
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ─── Configuration ────────────────────────────────────────────────────────
# When history exceeds SUMMARIZE_THRESHOLD entries, everything older than
# KEEP_RECENT is summarized into a running summary block.
SUMMARIZE_THRESHOLD = 8   # Start summarizing after 8 entries
KEEP_RECENT = 6           # Always keep the last 6 entries verbatim
MAX_SUMMARY_TOKENS = 300  # Target size for the summary block


# ════════════════════════════════════════════════════════════════════════════
# PART 1: RUNNING CONVERSATION SUMMARY
# ════════════════════════════════════════════════════════════════════════════

def should_summarize(history: List[Dict[str, str]]) -> bool:
    """Check if history is long enough to benefit from summarization."""
    return len(history) > SUMMARIZE_THRESHOLD


async def summarize_older_history(
    history: List[Dict[str, str]],
    existing_summary: str,
    llm_chat_fn,
) -> str:
    """
    Progressively summarize conversation history entries that are beyond
    the KEEP_RECENT window.

    This is a PROGRESSIVE summary: we take the existing summary + the newly
    evicted messages and produce an updated summary. We never re-summarize
    the entire history from scratch.

    Args:
        history: Full conversation history
        existing_summary: Previous running summary (empty string if first time)
        llm_chat_fn: LLM call function (should be a cheap/fast model)

    Returns:
        Updated summary string (~100-300 tokens)
    """
    if len(history) <= KEEP_RECENT:
        return existing_summary

    # Messages to summarize: everything before the KEEP_RECENT window
    to_summarize = history[:-KEEP_RECENT]

    if not to_summarize:
        return existing_summary

    # Build the text to summarize
    turns_text = []
    for entry in to_summarize:
        role = entry.get("role", "user")
        content = entry.get("content", "")
        # Truncate very long entries (agent results can be huge)
        if len(content) > 300:
            content = content[:300] + "..."
        turns_text.append(f"{role}: {content}")

    new_turns = "\n".join(turns_text)

    prompt_parts = []
    if existing_summary:
        prompt_parts.append(f"Previous conversation summary:\n{existing_summary}")
    prompt_parts.append(f"New conversation turns to incorporate:\n{new_turns}")

    messages = [
        {
            "role": "system",
            "content": (
                "You are a conversation summarizer. Produce a CONCISE summary "
                "of the conversation so far. Focus on:\n"
                "- Entity IDs and names mentioned (job IDs, staff names, etc.)\n"
                "- Actions taken (schedules created, invoices looked up, etc.)\n"
                "- Decisions made or preferences stated by the user\n"
                "- Open questions or unresolved items\n\n"
                "Keep it under 200 words. Use compact notation like "
                "'job_id=123 (Smith Residence)'. Do NOT include pleasantries "
                "or filler. This summary will be prepended to future LLM calls "
                "for context."
            ),
        },
        {"role": "user", "content": "\n\n".join(prompt_parts)},
    ]

    try:
        result = llm_chat_fn(messages, temperature=0.0)
        # Handle both LLMResult objects and raw strings
        summary = result.content if hasattr(result, "content") else str(result)
        logger.info(
            f"Conversation summary updated: {len(summary)} chars "
            f"(from {len(to_summarize)} older entries)"
        )
        return summary.strip()
    except Exception as e:
        logger.warning(f"Summary generation failed, keeping existing: {e}")
        return existing_summary


def build_history_with_summary(
    history: List[Dict[str, str]],
    summary: str,
) -> List[Dict[str, str]]:
    """
    Build a history array that includes the running summary as a system
    message, followed by the recent verbatim entries.

    This is what gets sent to LLM calls instead of the raw history.
    """
    result = []

    if summary:
        result.append({
            "role": "assistant",
            "content": f"[Session context: {summary}]",
        })

    # Append recent entries verbatim
    recent = history[-KEEP_RECENT:] if len(history) > KEEP_RECENT else history
    result.extend(recent)

    return result


# ════════════════════════════════════════════════════════════════════════════
# PART 2: SESSION SCRATCHPAD
# ════════════════════════════════════════════════════════════════════════════

# Regex patterns for extracting entity IDs from agent result summaries
# These match the format produced by _summarize_agent_result() in chat.py
_ID_PATTERNS = [
    re.compile(r"job_id=(\d+)"),
    re.compile(r"staff_id=(\d+)"),
    re.compile(r"section_id=(\d+)"),
    re.compile(r"cost_centre_id=(\d+)"),
    re.compile(r"invoice_id=(\d+)"),
    re.compile(r"schedule_id=(\d+)"),
    re.compile(r"contractor_job_id=(\d+)"),
    re.compile(r"quote_id=(\d+)"),
    re.compile(r"customer_id=(\d+)"),
]

# Regex for name mentions in agent summaries
_NAME_PATTERN = re.compile(r"\(([A-Z][a-zA-Z\s\-']+(?:\s[A-Z][a-zA-Z\-']+)*)\)")

# Regex for action verbs in agent summaries
_ACTION_PATTERNS = re.compile(
    r"\b(created|deleted|updated|scheduled|invoiced|cancelled|failed|FAILED)\b",
    re.IGNORECASE,
)


class SessionScratchpad:
    """
    Per-session fact store. Accumulates durable facts extracted from
    agent results and user interactions. No LLM call needed — purely
    rule-based extraction from structured agent output.

    The scratchpad is prepended to LLM calls as a compact context block
    (~100-200 tokens), giving the LLM awareness of what has been resolved
    in this session without re-reading full history.
    """

    def __init__(self):
        # Resolved entities: {entity_type: {id: name_or_label}}
        self.entities: Dict[str, Dict[str, str]] = {}
        # Actions taken: ["created schedule for John on 2026-03-10", ...]
        self.actions: List[str] = []
        # User preferences stated in this session
        self.preferences: List[str] = []
        # Max items to keep per category (prevent unbounded growth)
        self._max_entities_per_type = 20
        self._max_actions = 15

    def extract_from_agent_result(
        self,
        agent_name: str,
        result: Dict[str, Any],
        summary_text: str,
        user_text: str = "",
    ) -> None:
        """
        Extract durable facts from an agent result and its summary text.

        Args:
            agent_name: The agent that produced the result
            result: The raw agent result dict
            summary_text: The _summarize_agent_result() output
            user_text: The user's original message
        """
        # Extract entity IDs from summary text
        for pattern in _ID_PATTERNS:
            for match in pattern.finditer(summary_text):
                entity_type = pattern.pattern.split("=")[0].replace(r"(\d+)", "")
                entity_id = match.group(1)
                if entity_type not in self.entities:
                    self.entities[entity_type] = {}
                self.entities[entity_type][entity_id] = ""

        # Extract names and associate with entity IDs
        names = _NAME_PATTERN.findall(summary_text)
        for name in names:
            name = name.strip()
            if len(name) < 2 or len(name) > 60:
                continue
            # Try to associate name with the most recent entity type
            for etype in reversed(list(self.entities.keys())):
                for eid, existing_name in self.entities[etype].items():
                    if not existing_name:
                        self.entities[etype][eid] = name
                        break

        # Extract actions
        actions = _ACTION_PATTERNS.findall(summary_text)
        if actions:
            action_str = f"{agent_name}: {', '.join(actions).lower()}"
            if user_text:
                action_str += f" — '{user_text[:60]}'"
            self.actions.append(action_str)
            # Cap actions list
            if len(self.actions) > self._max_actions:
                self.actions = self.actions[-self._max_actions:]

        # Extract from structured result data
        self._extract_from_structured(agent_name, result)

    def _extract_from_structured(self, agent_name: str, result: Dict[str, Any]) -> None:
        """Extract entity IDs from structured agent result fields."""
        # Schedule agent
        if agent_name == "schedule":
            agent_output = result.get("agent_output", {})
            schedules = agent_output.get("schedules", result.get("schedules", []))
            for sched in (schedules if isinstance(schedules, list) else []):
                if isinstance(sched, dict):
                    if sched.get("staff_id"):
                        self.entities.setdefault("staff_id", {})[
                            str(sched["staff_id"])
                        ] = sched.get("staff_name", "")
                    if sched.get("job_id"):
                        self.entities.setdefault("job_id", {})[
                            str(sched["job_id"])
                        ] = sched.get("job_name", "")
                    if sched.get("cost_centre_id"):
                        self.entities.setdefault("cost_centre_id", {})[
                            str(sched["cost_centre_id"])
                        ] = sched.get("cost_centre_name", "")

        # Invoice agent
        elif agent_name == "invoice":
            for key in ("invoice_id", "job_id", "customer_id"):
                val = result.get(key)
                if val:
                    self.entities.setdefault(key, {})[str(val)] = ""

        # Workorder agent
        elif agent_name == "workorder":
            for r in result.get("results", []):
                if r.get("contractor_job_id"):
                    self.entities.setdefault("contractor_job_id", {})[
                        str(r["contractor_job_id"])
                    ] = r.get("contractor_name", "")

    def extract_from_mcp_response(self, response_text: str) -> None:
        """Extract entity IDs from MCP path response text."""
        for pattern in _ID_PATTERNS:
            for match in pattern.finditer(response_text):
                entity_type = pattern.pattern.split("=")[0].replace(r"(\d+)", "")
                entity_id = match.group(1)
                if entity_type not in self.entities:
                    self.entities[entity_type] = {}
                if entity_id not in self.entities[entity_type]:
                    self.entities[entity_type][entity_id] = ""

    def to_context_string(self) -> str:
        """
        Render the scratchpad as a compact string for LLM context injection.
        Returns empty string if nothing has been recorded yet.
        """
        parts = []

        # Entities
        entity_parts = []
        for etype, eid_map in self.entities.items():
            if not eid_map:
                continue
            items = []
            for eid, name in list(eid_map.items())[:10]:  # Cap at 10 per type
                items.append(f"{eid}({name})" if name else str(eid))
            entity_parts.append(f"{etype}=[{', '.join(items)}]")

        if entity_parts:
            parts.append("Resolved: " + "; ".join(entity_parts))

        # Actions
        if self.actions:
            parts.append("Actions: " + " | ".join(self.actions[-5:]))

        # Preferences
        if self.preferences:
            parts.append("User prefs: " + "; ".join(self.preferences[-3:]))

        if not parts:
            return ""

        return "[Session scratchpad: " + ". ".join(parts) + "]"

    def is_empty(self) -> bool:
        return not self.entities and not self.actions and not self.preferences


# ════════════════════════════════════════════════════════════════════════════
# PART 3: USER CONTEXT HELPERS
# ════════════════════════════════════════════════════════════════════════════

def get_or_create_scratchpad(user_context: Dict[str, Any]) -> SessionScratchpad:
    """Get or create the session scratchpad for a user context."""
    if "scratchpad" not in user_context:
        user_context["scratchpad"] = SessionScratchpad()
    return user_context["scratchpad"]


def get_running_summary(user_context: Dict[str, Any]) -> str:
    """Get the running conversation summary for a user context."""
    return user_context.get("running_summary", "")


def set_running_summary(user_context: Dict[str, Any], summary: str) -> None:
    """Store the running conversation summary."""
    user_context["running_summary"] = summary


async def update_context_after_turn(
    user_context: Dict[str, Any],
    history: List[Dict[str, str]],
    llm_chat_fn=None,
) -> List[Dict[str, str]]:
    """
    Called after each conversation turn. Updates running summary if needed
    and returns the trimmed history (capped at 10, with summary covering older).

    Args:
        user_context: The per-user context dict
        history: The full conversation history after appending new turn
        llm_chat_fn: LLM function for summarization (optional — skips if None)

    Returns:
        The history capped at recent entries (for storage)
    """
    # Update running summary if history is getting long
    if should_summarize(history) and llm_chat_fn is not None:
        existing = get_running_summary(user_context)
        try:
            new_summary = await summarize_older_history(
                history, existing, llm_chat_fn
            )
            set_running_summary(user_context, new_summary)
        except Exception as e:
            logger.warning(f"Summary update failed: {e}")

    # Return capped history (keep last 10 as before)
    return history[-10:]


def build_enriched_history(
    user_context: Dict[str, Any],
    history: List[Dict[str, str]],
    include_scratchpad: bool = True,
    include_summary: bool = True,
    max_entries: Optional[int] = None,
) -> List[Dict[str, str]]:
    """
    Build an enriched history array with summary and scratchpad for LLM calls.

    This replaces direct use of `history[-N:]` in LLM call sites.
    The output includes:
    1. Running summary of older turns (if available)
    2. Session scratchpad (resolved entities, actions)
    3. Recent verbatim history entries

    Args:
        user_context: Per-user context dict
        history: The stored conversation history
        include_scratchpad: Whether to prepend scratchpad context
        include_summary: Whether to prepend running summary
        max_entries: Max history entries to include (default: all stored)

    Returns:
        Enriched history array ready for LLM consumption
    """
    result = []

    # Prepend running summary
    if include_summary:
        summary = get_running_summary(user_context)
        if summary:
            result.append({
                "role": "assistant",
                "content": f"[Session context: {summary}]",
            })

    # Prepend scratchpad
    if include_scratchpad:
        scratchpad = get_or_create_scratchpad(user_context)
        ctx = scratchpad.to_context_string()
        if ctx:
            result.append({
                "role": "assistant",
                "content": ctx,
            })

    # Append recent history
    entries = history[-max_entries:] if max_entries else history
    result.extend(entries)

    return result
