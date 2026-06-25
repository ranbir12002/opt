"""
OData v3 query builder for MyOB AccountRight API.

MyOB uses OData v3 for filtering, pagination, and sorting.
This module provides safe, typed helpers for building query parameters.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional, Union


def _format_value(value: Any) -> str:
    """Format a value for OData filter expressions."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (date, datetime)):
        return f"datetime'{value.strftime('%Y-%m-%d')}'"
    # String — wrap in single quotes, escape internal quotes
    s = str(value).replace("'", "''")
    return f"'{s}'"


def eq(field: str, value: Any) -> str:
    """Equality filter: field eq 'value'"""
    return f"{field} eq {_format_value(value)}"


def ne(field: str, value: Any) -> str:
    """Not equal: field ne 'value'"""
    return f"{field} ne {_format_value(value)}"


def gt(field: str, value: Any) -> str:
    """Greater than: field gt value"""
    return f"{field} gt {_format_value(value)}"


def ge(field: str, value: Any) -> str:
    """Greater than or equal: field ge value"""
    return f"{field} ge {_format_value(value)}"


def lt(field: str, value: Any) -> str:
    """Less than: field lt value"""
    return f"{field} lt {_format_value(value)}"


def le(field: str, value: Any) -> str:
    """Less than or equal: field le value"""
    return f"{field} le {_format_value(value)}"


def contains(field: str, value: str) -> str:
    """Contains (case-sensitive): substringof('value', field)"""
    escaped = str(value).replace("'", "''")
    return f"substringof('{escaped}', {field})"


def starts_with(field: str, value: str) -> str:
    """Starts with: startswith(field, 'value')"""
    escaped = str(value).replace("'", "''")
    return f"startswith({field}, '{escaped}')"


def ends_with(field: str, value: str) -> str:
    """Ends with: endswith(field, 'value')"""
    escaped = str(value).replace("'", "''")
    return f"endswith({field}, '{escaped}')"


def between_dates(field: str, start: str, end: str) -> str:
    """Date range: field ge datetime'start' and field le datetime'end'"""
    return f"{field} ge datetime'{start}' and {field} le datetime'{end}'"


def combine(*expressions: str, operator: str = "and") -> str:
    """Combine multiple filter expressions with and/or."""
    parts = [e for e in expressions if e]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return f" {operator} ".join(parts)


# ── Known field type hints for smart filter building ──────────────────
_TEXT_FIELDS = {
    "CompanyName", "FirstName", "LastName", "DisplayID", "Name",
    "Description", "Number", "ShipToAddress", "Comment",
    "Notes", "EmploymentBasis", "Category", "URI",
}

_BOOL_FIELDS = {
    "IsActive", "IsReportable", "IsTaxInclusive", "IsIndividual",
}

_DATE_FIELDS = {
    "Date", "DateOccurred", "DatePaid", "PromisedDate",
    "StartDate", "EndDate", "DueDate",
}


def smart_build_filter(filters: dict[str, Any]) -> str:
    """
    Convert a simple dict of {field: value} into an OData $filter string.

    Automatically chooses the right operator:
    - Text fields → substringof() for contains search
    - Bool fields → eq true/false
    - Date string values (YYYY-MM-DD) on date fields → datetime comparison
    - Everything else → eq

    Examples:
        {"CompanyName": "Smith"}  →  substringof('Smith', CompanyName)
        {"IsActive": True}        →  IsActive eq true
        {"Date": "2026-01-01"}    →  Date ge datetime'2026-01-01'
        {"Status": "Open"}        →  Status eq 'Open'
    """
    parts = []
    for field, value in filters.items():
        if value is None:
            continue

        # Check if field is a known text field → use contains
        if field in _TEXT_FIELDS and isinstance(value, str):
            parts.append(contains(field, value))
        elif field in _BOOL_FIELDS:
            parts.append(eq(field, bool(value)))
        elif field in _DATE_FIELDS and isinstance(value, str):
            # Assume it is a single date acting as 'on or after'
            parts.append(ge(field, f"datetime'{value}'"))
        else:
            parts.append(eq(field, value))

    return combine(*parts)


def build_query_params(
    top: int = 400,
    skip: int = 0,
    filter_expr: Optional[str] = None,
    orderby: Optional[str] = None,
) -> dict[str, Union[str, int]]:
    """
    Build complete OData query parameter dict for MyOB API calls.

    Args:
        top: Max results per page (max 1000, default 400)
        skip: Number of records to skip
        filter_expr: OData $filter string
        orderby: Sort expression (e.g. 'CompanyName desc')

    Returns:
        Dict of query params ready for httpx
    """
    params: dict[str, Union[str, int]] = {}
    if top:
        params["$top"] = min(top, 1000)
    if skip:
        params["$skip"] = skip
    if filter_expr:
        params["$filter"] = filter_expr
    if orderby:
        params["$orderby"] = orderby
    return params
