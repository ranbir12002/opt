"""
MyOB API Reference — Per-topic knowledge for tool descriptions.

Token-efficient API hints embedded in tool descriptions so the LLM
knows how to use each tool correctly without inflating the system prompt.

Usage:
    from src.myob_api_reference import get_api_hint

    class MyOBSearchCustomersTool(BaseTool):
        def get_description(self):
            return f"Search customers...\\n\\n{get_api_hint('odata_filters', 'pagination')}"
"""
from __future__ import annotations

_HINTS: dict[str, str] = {

    "odata_filters": (
        "ODATA FILTERS (use in 'filters' param):\n"
        "- Text search: substringof('keyword', FieldName) — contains (case-sensitive)\n"
        "- Starts with: startswith(FieldName, 'prefix')\n"
        "- Exact match: FieldName eq 'Value'\n"
        "- Not equal: FieldName ne 'Value'\n"
        "- Comparison: FieldName gt 1000, FieldName le 5000\n"
        "- Date filter: FieldName ge datetime'2026-01-01'\n"
        "- Boolean: IsActive eq true\n"
        "- Combine: expr1 and expr2, expr1 or expr2\n"
        "- Nested property: PaymentDetails/Method eq 'Cash'\n"
        "NOTE: The tool auto-converts simple filter dicts {\"CompanyName\": \"Smith\"} "
        "to OData substringof() for text fields. Pass raw OData in 'filter_expr' "
        "for advanced queries."
    ),

    "pagination": (
        "PAGINATION: $top max is 1000 (default 400). Use $skip for offset.\n"
        "The tool auto-paginates to fetch all matching records.\n"
        "For very large datasets, consider using filters to narrow results."
    ),

    "date_format": (
        "DATES: MyOB uses ISO format 'YYYY-MM-DDT00:00:00' for dates.\n"
        "In OData filters: datetime'YYYY-MM-DD'\n"
        "Example: Date ge datetime'2026-01-01' and Date le datetime'2026-01-31'"
    ),

    "uid_format": (
        "IDs: MyOB uses GUIDs (e.g., 'bd0b0a42-1234-5678-9abc-def012345678').\n"
        "Always pass UIDs as strings, not integers."
    ),

    "invoice_types": (
        "INVOICE TYPES: MyOB has 5 invoice types:\n"
        "- Item: line items with inventory tracking\n"
        "- Service: labor/services with account-based lines\n"
        "- Professional: time-based billing with account lines\n"
        "- TimeBilling: activity/hours-based billing\n"
        "- Miscellaneous: catch-all with account lines\n"
        "Search returns ALL types by default. Create/Update requires specifying type."
    ),

    "mutation_format": (
        "MUTATIONS: POST to create, PUT to update (full object + RowVersion), "
        "DELETE with {\"UID\": \"...\"} in body.\n"
        "PUT requires the FULL object — fetch first, modify, send back.\n"
        "Include RowVersion from the GET response to avoid 409 Conflict.\n"
        "On error: {\"Name\": \"...\", \"Message\": \"...\", \"ErrorCode\": N}"
    ),

    "row_version": (
        "CONCURRENCY: Always include 'RowVersion' in PUT/DELETE requests.\n"
        "Fetch the current record first to get RowVersion.\n"
        "409 Conflict means someone else modified the record — re-fetch and retry."
    ),

    "ordering": (
        "ORDERING: Use $orderby for sorting.\n"
        "- Ascending (default): $orderby=CompanyName\n"
        "- Descending: $orderby=Number desc"
    ),
}


def get_api_hint(*topics: str) -> str:
    """
    Get API guidance for one or more topics.

    Returns combined hint string for embedding in tool descriptions.
    """
    parts = [_HINTS[t] for t in topics if t in _HINTS]
    return "\n\n".join(parts)


def list_topics() -> list[str]:
    """List all available hint topics."""
    return list(_HINTS.keys())
