"""MyOB Banking tools — Spend Money, Receive Money, Transfer Money."""
from __future__ import annotations

from typing import Any, Dict

from src.myob.api.banking import BankingAPI
from src.myob.odata import smart_build_filter
from src.myob_api_reference import get_api_hint

from .base import BaseTool


def _extract_items(result: Any) -> list:
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        return result.get("Items", [])
    return []


class MyOBSearchSpendMoneyTool(BaseTool):
    def __init__(self):
        self.api = BankingAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_search_spend_money"

    def get_description(self) -> str:
        return (
            "Search spend money transactions in MyOB AccountRight.\n\n"
            "Filterable fields: Date, DateOccurred, Memo, IsTaxInclusive.\n\n"
            f"{get_api_hint('odata_filters', 'pagination', 'date_format')}"
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
        result = await self.api.search_spend_money(top=top, filter_expr=filter_expr, orderby=orderby)
        items = _extract_items(result)
        return {"spend_money": items, "total_fetched": len(items)}


class MyOBSearchReceiveMoneyTool(BaseTool):
    def __init__(self):
        self.api = BankingAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_search_receive_money"

    def get_description(self) -> str:
        return (
            "Search receive money transactions in MyOB AccountRight.\n\n"
            f"{get_api_hint('odata_filters', 'pagination', 'date_format')}"
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
        result = await self.api.search_receive_money(top=top, filter_expr=filter_expr, orderby=orderby)
        items = _extract_items(result)
        return {"receive_money": items, "total_fetched": len(items)}


class MyOBSearchTransfersTool(BaseTool):
    def __init__(self):
        self.api = BankingAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_search_transfers"

    def get_description(self) -> str:
        return (
            "Search transfer money transactions in MyOB AccountRight.\n\n"
            f"{get_api_hint('odata_filters', 'pagination', 'date_format')}"
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
        result = await self.api.search_transfers(top=top, filter_expr=filter_expr, orderby=orderby)
        items = _extract_items(result)
        return {"transfers": items, "total_fetched": len(items)}
