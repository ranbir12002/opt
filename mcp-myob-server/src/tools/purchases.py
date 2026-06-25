"""MyOB Purchase tools — Bills, Purchase Orders, Supplier Payments."""
from __future__ import annotations

from typing import Any, Dict

from src.myob.api.purchases import PurchasesAPI
from src.myob.odata import smart_build_filter
from src.myob_api_reference import get_api_hint

from .base import BaseTool


def _extract_items(result: Any) -> list:
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        return result.get("Items", [])
    return []


class MyOBSearchBillsTool(BaseTool):
    def __init__(self):
        self.api = PurchasesAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_search_bills"

    def get_description(self) -> str:
        return (
            "Search for purchase bills in MyOB AccountRight.\n\n"
            "Filterable fields: Number, Date, Supplier/UID, Status, "
            "BalanceDueAmount.\n\n"
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
            result = await self.api.search_bills(
                top=top, skip=skip, filter_expr=filter_expr, orderby=orderby,
            )
            items = _extract_items(result)
            all_items.extend(items)
            if len(items) < top:
                break
            skip += top
        return {"bills": all_items, "total_fetched": len(all_items)}


class MyOBGetBillTool(BaseTool):
    def __init__(self):
        self.api = PurchasesAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_get_bill"

    def get_description(self) -> str:
        return f"Get a single purchase bill by UID.\n\n{get_api_hint('uid_format')}"

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "Bill GUID."},
                "bill_type": {
                    "type": "string",
                    "enum": ["Item", "Service", "Professional", "Miscellaneous"],
                    "description": "Bill type (default Item).",
                },
            },
            "required": ["uid"],
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        bill_type = arguments.get("bill_type", "Item")
        return await self.api.get_bill(arguments["uid"], bill_type)


class MyOBSearchPurchaseOrdersTool(BaseTool):
    def __init__(self):
        self.api = PurchasesAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_search_purchase_orders"

    def get_description(self) -> str:
        return (
            "Search for purchase orders in MyOB AccountRight.\n\n"
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
            result = await self.api.search_purchase_orders(
                top=top, skip=skip, filter_expr=filter_expr, orderby=orderby,
            )
            items = _extract_items(result)
            all_items.extend(items)
            if len(items) < top:
                break
            skip += top
        return {"purchase_orders": all_items, "total_fetched": len(all_items)}


class MyOBGetPurchaseOrderTool(BaseTool):
    def __init__(self):
        self.api = PurchasesAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_get_purchase_order"

    def get_description(self) -> str:
        return f"Get a single purchase order by UID.\n\n{get_api_hint('uid_format')}"

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "Purchase order GUID."},
                "order_type": {
                    "type": "string",
                    "enum": ["Item", "Service", "Professional", "Miscellaneous"],
                    "description": "Order type (default Item).",
                },
            },
            "required": ["uid"],
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        order_type = arguments.get("order_type", "Item")
        return await self.api.get_purchase_order(arguments["uid"], order_type)


class MyOBSearchSupplierPaymentsTool(BaseTool):
    def __init__(self):
        self.api = PurchasesAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_search_supplier_payments"

    def get_description(self) -> str:
        return (
            "Search for supplier payments in MyOB AccountRight.\n\n"
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
            result = await self.api.search_supplier_payments(
                top=top, skip=skip, filter_expr=filter_expr, orderby=orderby,
            )
            items = _extract_items(result)
            all_items.extend(items)
            if len(items) < top:
                break
            skip += top
        return {"supplier_payments": all_items, "total_fetched": len(all_items)}
