"""
backend/utils/query_planner.py

Query planner: one cheap LLM call produces a structured execution plan
for any user query. Port of mcp-client/utils/query-planner.js.

The plan is injected into the system prompt so the main LLM knows the
optimal sequence of tool calls — no hardcoded scenario instructions needed.

Cost: ~200-300 tokens in, ~150-200 tokens out.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Module-level catalog cache
_cached_tool_count: int = 0
_cached_catalog: str = ""


def _extract_short_desc(description: str) -> str:
    """Extract the first meaningful sentence from a tool description."""
    sentences = re.split(r"(?<=[.!?])\s+|\n", description or "")
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if re.match(r"^use this tool", s, re.IGNORECASE):
            continue
        if re.match(r"^examples?:", s, re.IGNORECASE):
            break
        return s[:120]
    return (sentences[0].strip()[:120]) if sentences else ""


def build_tool_catalog(tools: List[Dict[str, Any]]) -> str:
    """
    Build a compact tool catalog string from the live MCP tools list.
    Each tool gets: name, required params, and a short description.
    """
    if not tools:
        return "No tools available."

    lines = []
    for tool in tools:
        name = tool.get("name", "")
        schema = tool.get("inputSchema") or tool.get("input_schema") or {}
        required = schema.get("required") or []
        props = schema.get("properties") or {}

        param_parts = []
        for p_name, p_def in props.items():
            is_required = p_name in required
            p_type = p_def.get("type", "any")
            enum_vals = p_def.get("enum")
            enum_str = f"({('|').join(str(v) for v in enum_vals)})" if isinstance(enum_vals, list) else ""
            param_parts.append(f"{p_name}:{p_type}{enum_str}{'*' if is_required else ''}")

        param_str = f" [{', '.join(param_parts)}]" if param_parts else ""

        has_filters = "filters" in props
        filters_note = " (filters accepts ANY response field as URL filter — use dot notation for nested fields)" if has_filters else ""

        desc = _extract_short_desc(tool.get("description", ""))
        lines.append(f"- {name}{param_str}: {desc}{filters_note}")

    return "Available tools (* = required param):\n" + "\n".join(lines)


def _get_tool_catalog(tools: List[Dict[str, Any]]) -> str:
    """Return catalog string, rebuilding only when tool count changes."""
    global _cached_tool_count, _cached_catalog
    if len(tools) != _cached_tool_count:
        _cached_catalog = build_tool_catalog(tools)
        _cached_tool_count = len(tools)
        logger.debug(f"[QueryPlanner] Tool catalog rebuilt ({len(tools)} tools)")
    return _cached_catalog


async def plan_query(
    user_message: str,
    tools: List[Dict[str, Any]],
    llm_chat_fn,          # async fn(messages, max_tokens=300) -> str
) -> Optional[str]:
    """
    Generate an execution plan for a user query.

    Returns a planning hint string to append to the system prompt, or None on failure.
    `llm_chat_fn` is an async callable(messages, max_tokens) -> str.
    """
    if not user_message or len(user_message) < 5:
        return None

    tool_catalog = _get_tool_catalog(tools)
    today = date.today().isoformat()

    system_prompt = f"""You are a query planner for a back-office ERP assistant. Given a user question, produce an execution plan that uses the FEWEST tool calls possible.

{tool_catalog}

KEY RULES:
- DIRECT FILTERING: Search/list tools accept a 'filters' param where ANY response field can be used as a server-side URL filter — including nested fields via dot notation (e.g. Staff.Name, Customer.CompanyName, Site.Name). When the user names a person, company, site, or entity, pass it as a filter on the primary tool — do NOT call a separate lookup tool first.
- SELF-CONTAINED TOOLS: If the primary tool's response already contains a qualifying field, express it as a filter ON THAT TOOL — NEVER call a secondary tool to resolve a qualifier the primary tool already carries.
- NEVER FETCH-ALL: If the user's request names a specific entity (person, company, job, site), you MUST include that name as a filter. Fetching all records and post-filtering by name in context is FORBIDDEN when a filter is available.
- NAME FILTERS: Pass names exactly as the user said them — the system resolves fuzzy names to IDs automatically. Use the name filter; do not pre-resolve IDs.
- SCHEMA PARAMS: Only use params and values that exist in the tool schema. Do not invent param names or values. For params with listed values, only use those exact values when the user's request explicitly names one — do not infer or map qualifiers to enum values unless the user directly stated them.
- QUALIFIER MAPPING: For each qualifier in the user's request, check if the primary tool's response fields already carry it. If yes → filter on primary tool. If no AND can't be expressed as valid param → omit it (do NOT add extra tool steps to resolve it).

Reply with ONLY valid JSON:
{{
  "steps": [
    {{"step": 1, "task": "description", "tools": ["tool_name"], "args": {{"param": "value", "filters": {{"Field.Name": "value"}}}}, "depends_on": null}},
    {{"step": 2, "task": "description", "tools": ["tool_name"], "args": {{}}, "depends_on": 1}}
  ],
  "warnings": ["any gotchas"],
  "parallel_groups": [[1, 2], [3]]
}}

Rules:
- CRITICAL: For each step, include "args" with EXACT params and filters. For each qualifier in the user's request: if it maps to a named param → use it; if it maps to a response field → use a filter with the user's exact value; if it maps to neither → omit it.
- ALWAYS prefer the tool that requires FEWER prerequisite lookups.
- 1-4 steps max. Simple lookups = 1 step.
- Today's date: {today}"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message[:400]},
    ]

    try:
        text = (await llm_chat_fn(messages, max_tokens=300)).strip()

        json_match = re.search(r"\{[\s\S]*\}", text)
        if not json_match:
            logger.warning(f"[QueryPlanner] No JSON in response. LLM returned: {text[:500]!r}")
            return None

        plan = json.loads(json_match.group(0))

        if not isinstance(plan.get("steps"), list) or not plan["steps"]:
            logger.debug("[QueryPlanner] Empty plan — skipping")
            return None

        hint = (
            "\n\nQUERY PLAN (suggested approach — before executing each step, validate all args "
            "against the tool schema; correct or drop any param that violates an enum constraint "
            "or does not exist on the tool):"
        )

        for step in plan["steps"]:
            dep = f" (after step {step['depends_on']})" if step.get("depends_on") else ""
            tool_names = ", ".join(step.get("tools") or [])
            args = step.get("args") or {}
            args_str = f" with args: {json.dumps(args)}" if args else ""
            hint += f"\n  Step {step['step']}: {step.get('task', '')} → use: {tool_names}{args_str}{dep}"

        parallel_groups = plan.get("parallel_groups") or []
        if parallel_groups:
            groups = ", then ".join(f"[{', '.join(str(s) for s in g)}]" for g in parallel_groups)
            hint += f"\n  Execution order: {groups}"

        warnings = plan.get("warnings") or []
        if warnings:
            hint += "\n  ⚠️ " + "\n  ⚠️ ".join(warnings)

        hint += "\nAfter all steps, synthesize a complete answer addressing every part of the question."

        logger.debug(f"[QueryPlanner] Plan: {len(plan['steps'])} steps, {len(warnings)} warnings")
        return hint

    except Exception as e:
        logger.warning(f"[QueryPlanner] Planning failed: {e}")
        return None
