"""MyOB Sales tools — Invoices (CRUD), Orders, Quotes, Customer Payments."""
from __future__ import annotations

from typing import Any, Dict

from src.myob.api.sales import SalesAPI
from src.myob.odata import smart_build_filter
from src.myob_api_reference import get_api_hint
from src.utils import get_logger

from .base import BaseTool

logger = get_logger(__name__)


def _extract_items(result: Any) -> list:
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        return result.get("Items", [])
    return []


# ══════════════════════════════════════════════════════════════════════
# INVOICES — Search, Get, Create, Update, Delete
# ══════════════════════════════════════════════════════════════════════

class MyOBSearchInvoicesTool(BaseTool):
    def __init__(self):
        self.api = SalesAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_search_invoices"

    def get_description(self) -> str:
        return (
            "Search for sale invoices in MyOB AccountRight.\n"
            "Searches across ALL invoice types (Item, Service, Professional, "
            "TimeBilling, Miscellaneous) by default.\n\n"
            "Filterable fields: Number, Date, Customer/UID, Status, "
            "BalanceDueAmount, IsTaxInclusive.\n\n"
            f"{get_api_hint('odata_filters', 'pagination', 'invoice_types')}"
        )

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filters": {"type": "object", "description": "Filter dict {field: value}."},
                "filter_expr": {"type": "string", "description": "Raw OData $filter."},
                "top": {"type": "integer", "description": "Max results per type (default 400)."},
                "orderby": {"type": "string", "description": "Sort expression."},
                "invoice_type": {
                    "type": "string",
                    "enum": ["Item", "Service", "Professional", "TimeBilling", "Miscellaneous"],
                    "description": "Search only this type. Omit to search all types.",
                },
            },
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        filters = arguments.get("filters", {})
        filter_expr = arguments.get("filter_expr") or smart_build_filter(filters)
        top = arguments.get("top", 400)
        orderby = arguments.get("orderby")
        inv_type = arguments.get("invoice_type")

        if inv_type:
            # Search single type
            all_items = []
            skip = 0
            while True:
                result = await self.api.search_invoices(
                    top=top, skip=skip, filter_expr=filter_expr, orderby=orderby,
                )
                items = _extract_items(result)
                all_items.extend(items)
                if len(items) < top:
                    break
                skip += top
            return {"invoices": all_items, "total_fetched": len(all_items)}
        else:
            # Search all types
            all_invoices = await self.api.search_invoices_all_types(
                top=top, filter_expr=filter_expr, orderby=orderby,
            )
            return {"invoices": all_invoices, "total_fetched": len(all_invoices)}


class MyOBGetInvoiceTool(BaseTool):
    def __init__(self):
        self.api = SalesAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_get_invoice"

    def get_description(self) -> str:
        return (
            "Get a single invoice by UID from MyOB.\n"
            "You must specify the invoice_type (Item, Service, Professional, "
            "TimeBilling, Miscellaneous).\n\n"
            f"{get_api_hint('uid_format', 'invoice_types')}"
        )

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "Invoice GUID."},
                "invoice_type": {
                    "type": "string",
                    "enum": ["Item", "Service", "Professional", "TimeBilling", "Miscellaneous"],
                    "description": "Invoice type.",
                },
            },
            "required": ["uid", "invoice_type"],
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return await self.api.get_invoice(arguments["uid"], arguments["invoice_type"])


class MyOBCreateInvoiceTool(BaseTool):
    def __init__(self):
        self.api = SalesAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_create_invoice"

    def get_description(self) -> str:
        return (
            "Create a new sale invoice in MyOB AccountRight.\n\n"
            "Required fields:\n"
            "- invoice_type: Item, Service, Professional, TimeBilling, or Miscellaneous\n"
            "- Customer: {\"UID\": \"customer-guid\"}\n"
            "- Date: ISO format e.g. '2026-03-01T00:00:00'\n"
            "- Lines: array of line items\n\n"
            "For Item type lines: {\"Type\": \"Transaction\", \"Description\": \"...\", "
            "\"BillQuantity\": 1, \"UnitPrice\": 100, \"Item\": {\"UID\": \"item-guid\"}, "
            "\"TaxCode\": {\"UID\": \"tax-guid\"}}\n\n"
            "For Service type lines: {\"Type\": \"Transaction\", \"Description\": \"...\", "
            "\"Total\": 100, \"Account\": {\"UID\": \"account-guid\"}, "
            "\"TaxCode\": {\"UID\": \"tax-guid\"}}\n\n"
            f"{get_api_hint('mutation_format', 'invoice_types')}"
        )

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "invoice_type": {
                    "type": "string",
                    "enum": ["Item", "Service", "Professional", "TimeBilling", "Miscellaneous"],
                    "description": "Type of invoice to create.",
                },
                "invoice_data": {
                    "type": "object",
                    "description": "Full invoice body. Must include Customer, Date, Lines.",
                },
            },
            "required": ["invoice_type", "invoice_data"],
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        inv_type = arguments["invoice_type"]
        data = arguments["invoice_data"]

        try:
            result = await self.api.create_invoice(inv_type, data)
            return {"success": True, "invoice_type": inv_type, "result": result}
        except Exception as e:
            return {"success": False, "error": str(e)}


class MyOBUpdateInvoiceTool(BaseTool):
    def __init__(self):
        self.api = SalesAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_update_invoice"

    def get_description(self) -> str:
        return (
            "Update an existing invoice in MyOB AccountRight.\n\n"
            "IMPORTANT: MyOB uses PUT (full replacement), not PATCH.\n"
            "You MUST fetch the invoice first, modify the fields, then send the "
            "full object back including RowVersion.\n\n"
            f"{get_api_hint('mutation_format', 'row_version')}"
        )

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "Invoice GUID to update."},
                "invoice_type": {
                    "type": "string",
                    "enum": ["Item", "Service", "Professional", "TimeBilling", "Miscellaneous"],
                    "description": "Invoice type.",
                },
                "invoice_data": {
                    "type": "object",
                    "description": "Full invoice body with RowVersion. Fetch first, modify, send back.",
                },
            },
            "required": ["uid", "invoice_type", "invoice_data"],
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        uid = arguments["uid"]
        inv_type = arguments["invoice_type"]
        data = arguments["invoice_data"]

        try:
            result = await self.api.update_invoice(uid, inv_type, data)
            return {"success": True, "uid": uid, "invoice_type": inv_type, "result": result}
        except Exception as e:
            return {"success": False, "error": str(e)}


class MyOBDeleteInvoiceTool(BaseTool):
    def __init__(self):
        self.api = SalesAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_delete_invoice"

    def get_description(self) -> str:
        return (
            "Delete an invoice from MyOB AccountRight.\n\n"
            "Requires the invoice UID and type.\n\n"
            f"{get_api_hint('uid_format', 'invoice_types')}"
        )

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "Invoice GUID to delete."},
                "invoice_type": {
                    "type": "string",
                    "enum": ["Item", "Service", "Professional", "TimeBilling", "Miscellaneous"],
                    "description": "Invoice type.",
                },
            },
            "required": ["uid", "invoice_type"],
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        uid = arguments["uid"]
        inv_type = arguments["invoice_type"]

        try:
            result = await self.api.delete_invoice(uid, inv_type)
            return {"success": True, "uid": uid, "deleted": True}
        except Exception as e:
            return {"success": False, "error": str(e)}


# ══════════════════════════════════════════════════════════════════════
# SALE ORDERS
# ══════════════════════════════════════════════════════════════════════

class MyOBSearchSaleOrdersTool(BaseTool):
    def __init__(self):
        self.api = SalesAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_search_sale_orders"

    def get_description(self) -> str:
        return (
            "Search for sale orders in MyOB AccountRight.\n\n"
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
            result = await self.api.search_sale_orders(
                top=top, skip=skip, filter_expr=filter_expr, orderby=orderby,
            )
            items = _extract_items(result)
            all_items.extend(items)
            if len(items) < top:
                break
            skip += top

        return {"sale_orders": all_items, "total_fetched": len(all_items)}


class MyOBGetSaleOrderTool(BaseTool):
    def __init__(self):
        self.api = SalesAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_get_sale_order"

    def get_description(self) -> str:
        return f"Get a single sale order by UID.\n\n{get_api_hint('uid_format')}"

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "Sale order GUID."},
                "order_type": {
                    "type": "string",
                    "enum": ["Item", "Service", "Professional", "TimeBilling", "Miscellaneous"],
                    "description": "Order type (default Item).",
                },
            },
            "required": ["uid"],
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        order_type = arguments.get("order_type", "Item")
        return await self.api.get_sale_order(arguments["uid"], order_type)


# ══════════════════════════════════════════════════════════════════════
# QUOTES
# ══════════════════════════════════════════════════════════════════════

class MyOBSearchQuotesTool(BaseTool):
    def __init__(self):
        self.api = SalesAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_search_quotes"

    def get_description(self) -> str:
        return (
            "Search for sale quotes in MyOB AccountRight.\n\n"
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
            result = await self.api.search_quotes(
                top=top, skip=skip, filter_expr=filter_expr, orderby=orderby,
            )
            items = _extract_items(result)
            all_items.extend(items)
            if len(items) < top:
                break
            skip += top

        return {"quotes": all_items, "total_fetched": len(all_items)}


class MyOBGetQuoteTool(BaseTool):
    def __init__(self):
        self.api = SalesAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_get_quote"

    def get_description(self) -> str:
        return f"Get a single quote by UID.\n\n{get_api_hint('uid_format')}"

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "Quote GUID."},
                "quote_type": {
                    "type": "string",
                    "enum": ["Item", "Service", "Professional", "TimeBilling", "Miscellaneous"],
                    "description": "Quote type (default Item).",
                },
            },
            "required": ["uid"],
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        quote_type = arguments.get("quote_type", "Item")
        return await self.api.get_quote(arguments["uid"], quote_type)


# ══════════════════════════════════════════════════════════════════════
# CUSTOMER PAYMENTS
# ══════════════════════════════════════════════════════════════════════

class MyOBSearchCustomerPaymentsTool(BaseTool):
    def __init__(self):
        self.api = SalesAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_search_customer_payments"

    def get_description(self) -> str:
        return (
            "Search for customer payments in MyOB AccountRight.\n\n"
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
            result = await self.api.search_customer_payments(
                top=top, skip=skip, filter_expr=filter_expr, orderby=orderby,
            )
            items = _extract_items(result)
            all_items.extend(items)
            if len(items) < top:
                break
            skip += top

        return {"customer_payments": all_items, "total_fetched": len(all_items)}


class MyOBGetCustomerPaymentTool(BaseTool):
    def __init__(self):
        self.api = SalesAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_get_customer_payment"

    def get_description(self) -> str:
        return f"Get a single customer payment by UID.\n\n{get_api_hint('uid_format')}"

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {"uid": {"type": "string", "description": "Payment GUID."}},
            "required": ["uid"],
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return await self.api.get_customer_payment(arguments["uid"])
