"""MyOB General Ledger tools — Accounts, Jobs, Tax Codes, Categories."""
from __future__ import annotations

from typing import Any, Dict

from src.myob.api.general_ledger import GeneralLedgerAPI
from src.myob.odata import smart_build_filter
from src.myob_api_reference import get_api_hint

from .base import BaseTool


def _extract_items(result: Any) -> list:
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        return result.get("Items", [])
    return []


class MyOBSearchAccountsTool(BaseTool):
    def __init__(self):
        self.api = GeneralLedgerAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_search_accounts"

    def get_description(self) -> str:
        return (
            "Search chart of accounts in MyOB AccountRight.\n\n"
            "Filterable fields: Name, DisplayID, Type (Asset, Liability, Equity, "
            "Income, CostOfSales, Expense, OtherIncome, OtherExpense), IsActive, "
            "Classification, IsHeader.\n\n"
            f"{get_api_hint('odata_filters', 'pagination')}"
        )

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filters": {"type": "object", "description": "Filter dict."},
                "filter_expr": {"type": "string", "description": "Raw OData $filter."},
                "top": {"type": "integer", "description": "Max results (default 400)."},
                "orderby": {"type": "string", "description": "Sort expression."},
            },
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        filters = arguments.get("filters", {})
        filter_expr = arguments.get("filter_expr") or smart_build_filter(filters)
        top = arguments.get("top", 400)
        orderby = arguments.get("orderby")

        all_items = []
        skip = 0
        while True:
            result = await self.api.search_accounts(
                top=top, skip=skip, filter_expr=filter_expr, orderby=orderby,
            )
            items = _extract_items(result)
            all_items.extend(items)
            if len(items) < top:
                break
            skip += top
        return {"accounts": all_items, "total_fetched": len(all_items)}


class MyOBGetAccountTool(BaseTool):
    def __init__(self):
        self.api = GeneralLedgerAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_get_account"

    def get_description(self) -> str:
        return f"Get a single account by UID.\n\n{get_api_hint('uid_format')}"

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {"uid": {"type": "string", "description": "Account GUID."}},
            "required": ["uid"],
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return await self.api.get_account(arguments["uid"])


class MyOBSearchJobsTool(BaseTool):
    def __init__(self):
        self.api = GeneralLedgerAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_search_jobs"

    def get_description(self) -> str:
        return (
            "Search for jobs in MyOB AccountRight general ledger.\n\n"
            "Filterable fields: Name, Number, IsActive, Description.\n\n"
            f"{get_api_hint('odata_filters', 'pagination')}"
        )

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filters": {"type": "object", "description": "Filter dict."},
                "filter_expr": {"type": "string", "description": "Raw OData $filter."},
                "top": {"type": "integer", "description": "Max results (default 400)."},
                "orderby": {"type": "string", "description": "Sort expression."},
            },
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        filters = arguments.get("filters", {})
        filter_expr = arguments.get("filter_expr") or smart_build_filter(filters)
        top = arguments.get("top", 400)
        orderby = arguments.get("orderby")

        all_items = []
        skip = 0
        while True:
            result = await self.api.search_jobs(
                top=top, skip=skip, filter_expr=filter_expr, orderby=orderby,
            )
            items = _extract_items(result)
            all_items.extend(items)
            if len(items) < top:
                break
            skip += top
        return {"jobs": all_items, "total_fetched": len(all_items)}


class MyOBGetJobTool(BaseTool):
    def __init__(self):
        self.api = GeneralLedgerAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_get_job"

    def get_description(self) -> str:
        return f"Get a single job by UID.\n\n{get_api_hint('uid_format')}"

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {"uid": {"type": "string", "description": "Job GUID."}},
            "required": ["uid"],
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return await self.api.get_job(arguments["uid"])


class MyOBSearchTaxCodesTool(BaseTool):
    def __init__(self):
        self.api = GeneralLedgerAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_search_tax_codes"

    def get_description(self) -> str:
        return (
            "Search for tax codes in MyOB AccountRight.\n\n"
            "Filterable fields: Code, Description, Type, Rate.\n\n"
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
        result = await self.api.search_tax_codes(top=top, filter_expr=filter_expr)
        items = _extract_items(result)
        return {"tax_codes": items, "total_fetched": len(items)}


class MyOBSearchCategoriesTool(BaseTool):
    def __init__(self):
        self.api = GeneralLedgerAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_search_categories"

    def get_description(self) -> str:
        return (
            "Search for categories in MyOB AccountRight general ledger.\n\n"
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
        result = await self.api.search_categories(top=top, filter_expr=filter_expr)
        items = _extract_items(result)
        return {"categories": items, "total_fetched": len(items)}
