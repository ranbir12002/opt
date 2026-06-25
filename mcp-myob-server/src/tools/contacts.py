"""MyOB Contact tools — Search/Get for Customers, Suppliers, Employees."""
from __future__ import annotations

from typing import Any, Dict

from src.myob.api.contacts import ContactsAPI
from src.myob.odata import smart_build_filter
from src.myob_api_reference import get_api_hint
from src.utils import get_logger

from .base import BaseTool

logger = get_logger(__name__)


# ── Helper: extract items from MyOB response ─────────────────────────
def _extract_items(result: Any) -> list:
    """MyOB returns either a list or a dict with Items key."""
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        return result.get("Items", [])
    return []


# ══════════════════════════════════════════════════════════════════════
# CUSTOMERS
# ══════════════════════════════════════════════════════════════════════

class MyOBSearchCustomersTool(BaseTool):
    def __init__(self):
        self.api = ContactsAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_search_customers"

    def get_description(self) -> str:
        return (
            "Search for customers in MyOB AccountRight.\n\n"
            "Filterable fields: CompanyName, FirstName, LastName, IsActive, "
            "DisplayID, Addresses/Email, Addresses/Phone1.\n\n"
            "Examples:\n"
            '- All customers → no filters\n'
            '- Find customer "Smith" → filters: {"CompanyName": "Smith"}\n'
            '- Active only → filters: {"IsActive": true}\n\n'
            f"{get_api_hint('odata_filters', 'pagination')}"
        )

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filters": {
                    "type": "object",
                    "description": "Filter fields as {field: value} dict. Text fields auto-convert to contains search.",
                },
                "filter_expr": {
                    "type": "string",
                    "description": "Raw OData $filter string for advanced queries.",
                },
                "top": {
                    "type": "integer",
                    "description": "Max results per page (default 400, max 1000).",
                },
                "orderby": {
                    "type": "string",
                    "description": "Sort field. Example: 'CompanyName asc'.",
                },
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
            result = await self.api.search_customers(
                top=top, skip=skip, filter_expr=filter_expr, orderby=orderby,
            )
            items = _extract_items(result)
            all_items.extend(items)
            if len(items) < top:
                break
            skip += top

        return {"customers": all_items, "total_fetched": len(all_items)}


class MyOBGetCustomerTool(BaseTool):
    def __init__(self):
        self.api = ContactsAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_get_customer"

    def get_description(self) -> str:
        return (
            "Get a single customer by UID from MyOB AccountRight.\n\n"
            f"{get_api_hint('uid_format')}"
        )

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uid": {
                    "type": "string",
                    "description": "Customer GUID (e.g. 'dde4659b-7bb3-4ef7-9312-c13b2fa02f58').",
                },
            },
            "required": ["uid"],
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return await self.api.get_customer(arguments["uid"])


# ══════════════════════════════════════════════════════════════════════
# SUPPLIERS
# ══════════════════════════════════════════════════════════════════════

class MyOBSearchSuppliersTool(BaseTool):
    def __init__(self):
        self.api = ContactsAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_search_suppliers"

    def get_description(self) -> str:
        return (
            "Search for suppliers in MyOB AccountRight.\n\n"
            "Filterable fields: CompanyName, FirstName, LastName, IsActive, DisplayID.\n\n"
            f"{get_api_hint('odata_filters', 'pagination')}"
        )

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filters": {"type": "object", "description": "Filter fields as {field: value} dict."},
                "filter_expr": {"type": "string", "description": "Raw OData $filter string."},
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
            result = await self.api.search_suppliers(
                top=top, skip=skip, filter_expr=filter_expr, orderby=orderby,
            )
            items = _extract_items(result)
            all_items.extend(items)
            if len(items) < top:
                break
            skip += top

        return {"suppliers": all_items, "total_fetched": len(all_items)}


class MyOBGetSupplierTool(BaseTool):
    def __init__(self):
        self.api = ContactsAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_get_supplier"

    def get_description(self) -> str:
        return f"Get a single supplier by UID from MyOB.\n\n{get_api_hint('uid_format')}"

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {"uid": {"type": "string", "description": "Supplier GUID."}},
            "required": ["uid"],
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return await self.api.get_supplier(arguments["uid"])


# ══════════════════════════════════════════════════════════════════════
# EMPLOYEES
# ══════════════════════════════════════════════════════════════════════

class MyOBSearchEmployeesTool(BaseTool):
    def __init__(self):
        self.api = ContactsAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_search_employees"

    def get_description(self) -> str:
        return (
            "Search for employees in MyOB AccountRight.\n\n"
            "Filterable fields: FirstName, LastName, IsActive, DisplayID, "
            "EmploymentBasis.\n\n"
            f"{get_api_hint('odata_filters', 'pagination')}"
        )

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filters": {"type": "object", "description": "Filter fields as {field: value} dict."},
                "filter_expr": {"type": "string", "description": "Raw OData $filter string."},
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
            result = await self.api.search_employees(
                top=top, skip=skip, filter_expr=filter_expr, orderby=orderby,
            )
            items = _extract_items(result)
            all_items.extend(items)
            if len(items) < top:
                break
            skip += top

        return {"employees": all_items, "total_fetched": len(all_items)}


class MyOBGetEmployeeTool(BaseTool):
    def __init__(self):
        self.api = ContactsAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_get_employee"

    def get_description(self) -> str:
        return f"Get a single employee by UID from MyOB.\n\n{get_api_hint('uid_format')}"

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {"uid": {"type": "string", "description": "Employee GUID."}},
            "required": ["uid"],
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return await self.api.get_employee(arguments["uid"])
