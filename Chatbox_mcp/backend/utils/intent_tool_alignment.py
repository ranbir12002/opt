# backend/utils/intent_tool_alignment.py
"""
Static intent-to-tool alignment checker.

Maps each intent type to expected Simpro/MyOB MCP tool families.
After a request completes, checks if the tools actually called
match the expected families. Misalignment means either the intent
was misclassified or the MCP LLM chose wrong tools.

Deterministic — no LLM calls, no network, <1ms.
"""
from typing import Dict, List, Optional, Set, Tuple


# Intent → set of tool name prefixes that are expected for this intent
INTENT_TOOL_FAMILIES: Dict[str, Optional[Set[str]]] = {
    "schedule_crud": {
        "list_employees", "get_employee",
        "get_schedules", "get_schedule",
        "create_schedule", "update_schedule", "delete_schedule",
        "list_schedule_types",
    },
    "schedule_query": {
        "list_employees", "get_employee",
        "get_schedules", "get_schedule",
        "list_schedule_types",
    },
    "invoice_crud": {
        "search_invoices", "get_invoice", "create_invoice",
        "update_invoice", "delete_invoice", "list_invoices",
        "search_jobs", "get_job", "get_job_sections",
        "get_job_section_cost_centres",
    },
    "workorder_crud": {
        "get_contractor", "list_contractors",
        "search_jobs", "get_job", "get_job_sections",
        "get_job_section_cost_centres",
        "create_contractor_job", "update_contractor_job",
        "delete_contractor_job", "get_contractor_job",
    },
    "purchase_order_crud": {
        "search_contacts", "get_contact",
        "search_jobs", "get_job", "get_job_sections",
        "get_job_section_cost_centres",
        "create_purchase_order", "update_purchase_order",
        "delete_purchase_order", "get_purchase_order",
        "list_purchase_orders", "search_purchase_orders",
    },
    "general_query": None,  # any tools are acceptable
    "query": None,          # alias for general_query
}

# Tools that are always acceptable regardless of intent (meta / utility tools)
_META_TOOLS = {"search", "list_companies", "get_company"}


def check_intent_tool_alignment(
    intent: Optional[str],
    tools_called: List[str],
) -> Tuple[str, float, str]:
    """
    Check if the tools called match the expected family for the detected intent.

    Returns:
        (alignment_status, score, reasoning)
        alignment_status: "aligned" | "partial" | "misaligned" | "unknown" | "no_tools"
        score: 0.0 to 1.0 (fraction of non-meta tools that match)
        reasoning: human-readable explanation
    """
    if not tools_called:
        return ("no_tools", 0.0, "No tools were called")

    if not intent or intent not in INTENT_TOOL_FAMILIES:
        return ("unknown", 0.5, f"Intent '{intent}' has no expected tool family defined")

    expected = INTENT_TOOL_FAMILIES[intent]
    if expected is None:
        return ("aligned", 1.0, "General query: all tools acceptable")

    # Check each tool against expected families using prefix matching
    matched = 0
    total = 0
    mismatched_tools = []

    for tool in tools_called:
        # Skip meta/utility tools
        if tool in _META_TOOLS:
            continue
        total += 1
        # Prefix match: "get_schedules" matches "get_schedule" prefix
        if any(tool.startswith(exp) or exp.startswith(tool) for exp in expected):
            matched += 1
        else:
            mismatched_tools.append(tool)

    if total == 0:
        return ("aligned", 1.0, "Only meta tools called")

    score = matched / total

    if score >= 0.8:
        return ("aligned", round(score, 2), f"{matched}/{total} tools match expected family")
    elif score >= 0.5:
        return ("partial", round(score, 2), f"{matched}/{total} match; unexpected: {mismatched_tools[:5]}")
    else:
        return ("misaligned", round(score, 2), f"{matched}/{total} match; unexpected: {mismatched_tools[:5]}")
