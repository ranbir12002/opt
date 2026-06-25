"""MyOB Payroll tools — Payroll Categories, Timesheets (read-only)."""
from __future__ import annotations

from typing import Any, Dict

from src.myob.api.payroll import PayrollAPI
from src.myob.odata import smart_build_filter
from src.myob_api_reference import get_api_hint

from .base import BaseTool


def _extract_items(result: Any) -> list:
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        return result.get("Items", [])
    return []


class MyOBSearchPayrollCategoriesTool(BaseTool):
    def __init__(self):
        self.api = PayrollAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_search_payroll_categories"

    def get_description(self) -> str:
        return (
            "Search payroll categories in MyOB AccountRight.\n"
            "Categories include: Wage, Entitlement, Deduction, Expense, "
            "Superannuation, Tax.\n\n"
            f"{get_api_hint('odata_filters', 'pagination')}"
        )

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filters": {"type": "object", "description": "Filter dict."},
                "filter_expr": {"type": "string", "description": "Raw OData $filter."},
                "top": {"type": "integer", "description": "Max results (default 400)."},
            },
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        filters = arguments.get("filters", {})
        filter_expr = arguments.get("filter_expr") or smart_build_filter(filters)
        top = arguments.get("top", 400)
        result = await self.api.search_payroll_categories(top=top, filter_expr=filter_expr)
        items = _extract_items(result)
        return {"payroll_categories": items, "total_fetched": len(items)}


class MyOBGetTimesheetTool(BaseTool):
    def __init__(self):
        self.api = PayrollAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_get_timesheet"

    def get_description(self) -> str:
        return (
            "Get timesheets from MyOB AccountRight.\n\n"
            "Filterable fields: Employee/UID, StartDate, EndDate.\n\n"
            f"{get_api_hint('odata_filters', 'date_format')}"
        )

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filters": {"type": "object", "description": "Filter dict."},
                "filter_expr": {"type": "string", "description": "Raw OData $filter."},
                "top": {"type": "integer", "description": "Max results (default 400)."},
            },
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        filters = arguments.get("filters", {})
        filter_expr = arguments.get("filter_expr") or smart_build_filter(filters)
        top = arguments.get("top", 400)
        result = await self.api.get_timesheet(top=top, filter_expr=filter_expr)
        items = _extract_items(result)
        return {"timesheets": items, "total_fetched": len(items)}
