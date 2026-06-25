"""MyOB Inventory tools — Items."""
from __future__ import annotations

from typing import Any, Dict

from src.myob.api.inventory import InventoryAPI
from src.myob.odata import smart_build_filter
from src.myob_api_reference import get_api_hint

from .base import BaseTool


def _extract_items(result: Any) -> list:
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        return result.get("Items", [])
    return []


class MyOBSearchItemsTool(BaseTool):
    def __init__(self):
        self.api = InventoryAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_search_items"

    def get_description(self) -> str:
        return (
            "Search inventory items in MyOB AccountRight.\n\n"
            "Filterable fields: Number, Name, IsActive, Description, "
            "IsBought, IsSold, IsInventoried.\n\n"
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
        result = await self.api.search_items(top=top, filter_expr=filter_expr, orderby=orderby)
        items = _extract_items(result)
        return {"items": items, "total_fetched": len(items)}


class MyOBGetItemTool(BaseTool):
    def __init__(self):
        self.api = InventoryAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_get_item"

    def get_description(self) -> str:
        return f"Get a single inventory item by UID.\n\n{get_api_hint('uid_format')}"

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {"uid": {"type": "string", "description": "Item GUID."}},
            "required": ["uid"],
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return await self.api.get_item(arguments["uid"])
