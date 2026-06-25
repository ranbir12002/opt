"""
Simpro API Reference — Per-topic knowledge for tool descriptions.

Instead of embedding all Simpro API docs into the system prompt (expensive),
each tool pulls ONLY the guidance it needs from this module.

Usage in tool files:
    from src.simpro_api_reference import get_api_hint

    class SearchJobsTool(BaseTool):
        def get_description(self):
            return f"Search jobs...\\n\\n{get_api_hint('search_operators')}"

Topics are short, focused snippets — not the full Simpro docs.
"""
from __future__ import annotations

from typing import Optional


# ── Per-topic API hints ──────────────────────────────────────────────
# Each value is a SHORT string (< 300 tokens) ready to embed in a tool
# description.  Keep them focused on what the LLM needs to know to
# USE the tool correctly, not general Simpro background.

_HINTS: dict[str, str] = {

    # ── Search / filter operators (for list/search tools) ────────
    "search_operators": (
        "FILTER OPERATORS (use in 'filters' param values):\n"
        "- Wildcard: \"%keyword%\" — contains (ALWAYS use for name/text fields)\n"
        "- Exact: \"Progress\" — matches exactly (ONLY for stage/type/boolean)\n"
        "- gt(v)/lt(v)/ge(v)/le(v): comparisons — gt(2026-01-01), le(100)\n"
        "- between(a,b): inclusive range — between(2026-01-01,2026-12-31)\n"
        "- in(a,b,c)/!in(a,b,c): list match/exclude — in(Active,Progress)\n"
        "- ne(v): not equal — ne(Archived)\n"
        "Nested fields use dots: \"Customer.CompanyName\", \"Site.Name\"\n"
        "Multiple filters = AND.\n"
        "IMPORTANT: For name/address/text searches, ALWAYS use %keyword% with the "
        "most distinctive word. Exact match on names/addresses returns 0 results."
    ),

    # ── Pagination (for list/search tools) ───────────────────────
    "pagination": (
        "PAGINATION: pageSize max is 250. Response headers include "
        "Result-Total (total records), Result-Pages (total pages), "
        "Result-Count (records in this page). Always paginate when "
        "fetching large datasets — iterate pages until Result-Count < pageSize."
    ),

    # ── display=all (for detail tools) ───────────────────────────
    "display_all": (
        "SUBRESOURCES: Set display='all' to fetch the entity WITH all "
        "child resources (sections, cost centres, items, etc.) in ONE call. "
        "Omit it when you only need top-level fields (status, dates, totals). "
        "Using display='all' returns more data but saves extra API calls."
    ),

    # ── Column selection (for any tool) ──────────────────────────
    "columns": (
        "COLUMN SELECTION: Use the 'columns' parameter to request only "
        "specific fields (e.g., columns='ID,Name,Status'). Reduces payload "
        "size and improves performance for large result sets."
    ),

    # ── Date ranges for schedules ────────────────────────────────
    "schedule_dates": (
        "DATE RANGES: Use date_from + date_to for ranges. "
        "NEVER default a range query to a single date. Resolve relative dates:\n"
        "- \"this week\" → date_from=MONDAY of the current week (NOT today), date_to=FRIDAY of the current week. "
        "ALWAYS go back to Monday even if today is Wednesday or Thursday.\n"
        "- \"next week\" → date_from=MONDAY of next week, date_to=FRIDAY of next week\n"
        "- \"till month end\" → date_from=today, date_to=last day of month\n"
        "- \"next 2 weeks\" → date_from=today, date_to=today+14\n"
        "IMPORTANT: 'this week' ALWAYS starts on Monday, never on today's date."
    ),

    # ── Date ranges for jobs/invoices ────────────────────────────
    "date_filter": (
        "DATE FILTERING: Use between() in filters for date ranges:\n"
        "- \"this month\" → {\"DateIssued\": \"between(2026-02-01,2026-02-28)\"}\n"
        "- \"last quarter\" → {\"DateIssued\": \"between(2026-10-01,2026-12-31)\"}\n"
        "NEVER default a range query to a single date."
    ),

    # ── Mutation error format (for create/update/delete tools) ───
    "mutation_errors": (
        "ERRORS: On validation failure (422), Simpro returns:\n"
        "{\"errors\": [{\"path\": \"fieldName\", \"message\": \"reason\", \"value\": \"submitted\"}]}\n"
        "On not found (404): {\"error\": \"path/to/resource\", \"message\": \"Not Found\"}\n"
        "Check error responses to give the user actionable feedback."
    ),

    # ── Batch operations (for bulk tools if added) ───────────────
    "batch_operations": (
        "BATCH: POST/PATCH to /multiple/ (up to 250 items per request). "
        "DELETE via /delete/ endpoint with array of IDs. "
        "Response: array of individual results (one per item)."
    ),

    # ── PATCH semantics (for update tools) ───────────────────────
    "patch_semantics": (
        "PARTIAL UPDATE: PATCH only updates fields you send — omitted "
        "fields stay unchanged. Returns 204 No Content on success."
    ),

    # ── Response-field filtering (universal — works on EVERY list endpoint) ──
    "nested_filters": (
        "RESPONSE-FIELD FILTERING (works on every Simpro list endpoint):\n"
        "Most response fields can be used as URL filters — including "
        "nested fields via dot notation. Pass them in the 'filters' param.\n"
        "Examples: {\"Staff.Type\": \"contractor\"}, {\"Customer.CompanyName\": \"%Smith%\"}, "
        "{\"Status\": \"Progress\"}, {\"Customer.ID\": \"690\"}.\n"
        "Operators: %keyword% (contains), gt(), lt(), ge(), le(), between(), in(), ne().\n"
        "Multiple filters are ANDed. ALWAYS prefer endpoint filters over post-filtering.\n"
        "Note: Some calculated fields (e.g. Total.ExTax) may not support URL filtering — "
        "if the API rejects a filter, it will be applied automatically as a post-filter."
    ),

    # ── Schedule mutations redirect ─────────────────────────────
    "schedule_mutation_redirect": (
        "IMPORTANT: You CANNOT create/update/delete schedules directly. "
        "These operations go through the schedule agent for SOP compliance. "
        "If the user asks, redirect them to:\n"
        "- Single: type 'create schedule for [person], job [ID], [date], [hours]'\n"
        "- Bulk: upload Excel with schedule data"
    ),

    # ── Department resolution chain ───────────────────────────
    "department_resolution": (
        "DEPARTMENT RESOLUTION CHAIN:\n"
        "Setup Cost Centre -> IncomeAccountNo -> Chart of Accounts (match by Number) -> Department.\n"
        "- /setup/accounts/costCenters/ returns [{ID, Name, IncomeAccountNo}, ...]\n"
        "- /setup/accounts/chartOfAccounts/ returns [{ID, Name, Number, Type, Archived}, ...]\n"
        "- Match IncomeAccountNo from cost centre against Number from chart of accounts\n"
        "- Account Name determines the department classification\n"
        "- Some cost centres may have null/missing IncomeAccountNo (unclassified)"
    ),
}


def get_api_hint(*topics: str) -> str:
    """
    Get API guidance for one or more topics.

    Args:
        *topics: Topic keys from _HINTS (e.g., "search_operators", "pagination")

    Returns:
        Combined hint string, or empty string if no matching topics.

    Example:
        >>> get_api_hint("search_operators", "pagination")
        "FILTER OPERATORS ...\\n\\nPAGINATION: ..."
    """
    parts = []
    for topic in topics:
        hint = _HINTS.get(topic)
        if hint:
            parts.append(hint)
    return "\n\n".join(parts)


def list_topics() -> list[str]:
    """List all available hint topics."""
    return list(_HINTS.keys())
