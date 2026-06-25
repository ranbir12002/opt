"""
Vendor Order-related MCP tools.

Provides tools for viewing vendor orders in Simpro.
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.simpro.api.vendor_orders import VendorOrdersAPI
from src.simpro_api_reference import get_api_hint
from src.utils import get_logger

from .base import BaseTool

logger = get_logger(__name__)


class GetVendorOrdersTool(BaseTool):
    """
    Tool for getting vendor orders.
    """
    
    def __init__(self):
        """Initialize get vendor orders tool"""
        self.vendor_orders_api = VendorOrdersAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "get_vendor_orders"
    
    def get_description(self) -> str:
        return f"""Get list of vendor orders in Simpro with optional filters.

Use this tool when the user asks about vendor orders, purchase orders,
supplier orders, or orders to vendors.

Filterable fields (use in 'filters' param): Status, OrderNo, Reference,
Vendor.CompanyName, Vendor.ID, DateIssued, Total.ExTax, Total.IncTax

Examples:
- "Show me all vendor orders" → no filters
- "Vendor orders for supplier ABC" → filters: {{"Vendor.CompanyName": "%ABC%"}}
- "Vendor orders this month" → filters: {{"DateIssued": "ge(2026-02-01)"}}
- "Orders over $5000" → filters: {{"Total.ExTax": "gt(5000)"}}

{get_api_hint("search_operators", "pagination")}
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
            }
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get vendor orders with auto-pagination to fetch all pages."""
        page_size = arguments.get("page_size", 250)

        filters = self.extract_filters(arguments)

        # Auto-paginate to fetch all results
        all_orders: List[Dict[str, Any]] = []
        current_page = 1
        while True:
            result = await self.vendor_orders_api.get_vendor_orders(
                page=current_page,
                page_size=page_size,
                **filters,
            )
            if isinstance(result, list):
                all_orders.extend(result)
                if len(result) < page_size:
                    break
                current_page += 1
            else:
                break

        logger.info(f"get_vendor_orders: fetched {len(all_orders)} orders across {current_page} page(s)")

        return {
            "vendor_orders": all_orders,
            "total_fetched": len(all_orders),
            "pages_fetched": current_page
        }


class GetVendorOrderDetailsTool(BaseTool):
    """
    Tool for getting detailed information about a specific vendor order.
    """
    
    def __init__(self):
        """Initialize get vendor order details tool"""
        self.vendor_orders_api = VendorOrdersAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "get_vendor_order_details"
    
    def get_description(self) -> str:
        return """Get detailed information about a specific vendor order by ID.
        
Use this tool when the user asks for details about a vendor order,
purchase order details, or supplier order information.

Examples:
- "Show me vendor order 12345"
- "What's in purchase order 67890?"
- "Get details for vendor order ID 111"
"""
    
    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "vendor_order_id": {
                    "type": "integer",
                    "description": "The ID of the vendor order to retrieve"
                }
            },
            "required": ["vendor_order_id"]
        }
    
    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get vendor order details"""
        vendor_order_id = arguments["vendor_order_id"]
        
        # Call Simpro API
        result = await self.vendor_orders_api.get_vendor_order_by_id(
            vendor_order_id=vendor_order_id
        )
        
        return {
            "vendor_order": result,
            "vendor_order_id": vendor_order_id
        }


class GetVendorOrderReceiptTool(BaseTool):
    """
    Tool for getting a specific receipt for a vendor order.
    """
    
    def __init__(self):
        """Initialize get vendor order receipt tool"""
        self.vendor_orders_api = VendorOrdersAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "get_vendor_order_receipt"
    
    def get_description(self) -> str:
        return """Get a specific receipt for a vendor order.
        
Use this tool when the user asks about vendor order receipts,
goods received, or delivery confirmations.

Examples:
- "Show me receipt 123 for vendor order 456"
- "Get receipt for vendor order 789"
- "What was received for vendor order 111?"
"""
    
    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "vendor_order_id": {
                    "type": "integer",
                    "description": "The ID of the vendor order"
                },
                "receipt_id": {
                    "type": "integer",
                    "description": "The ID of the receipt"
                }
            },
            "required": ["vendor_order_id", "receipt_id"]
        }
    
    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get vendor order receipt"""
        vendor_order_id = arguments["vendor_order_id"]
        receipt_id = arguments["receipt_id"]
        
        # Call Simpro API
        result = await self.vendor_orders_api.get_vendor_order_receipt(
            vendor_order_id=vendor_order_id,
            receipt_id=receipt_id
        )
        
        return {
            "receipt": result,
            "vendor_order_id": vendor_order_id,
            "receipt_id": receipt_id
        }


class CreateVendorOrderTool(BaseTool):
    """
    Tool for creating a new vendor order (purchase/supplier/material order).
    """

    def __init__(self):
        self.vendor_orders_api = VendorOrdersAPI()
        super().__init__()

    def get_name(self) -> str:
        return "create_vendor_order"

    def get_description(self) -> str:
        return f"""Create a new vendor order (also called purchase order, supplier order, or material order) in Simpro.

Use this tool when the user wants to raise/create a vendor order or purchase order against a supplier/vendor.

Required field:
- Vendor: integer ID of the supplier/vendor

Key optional fields:
- Type: "Catalogue" (default, for catalogue items) or "Description" (free-text line)
- Description: text description (only used when Type is "Description")
- IsInventoryItem: boolean (only when Type is "Description")
- Amount: ex-tax amount (only when Type is "Description")
- AssignedTo: integer ID of a job cost centre to assign the order to
- StorageDevice: integer ID of a storage device/warehouse
- Stage: "Pending" (default) | "Approved" | "Archived" | "Voided"
  - Archived/Voided orders cannot have receipts created against them
- Status: integer ID of a vendor order status code
- StatusAutoAdjust: set false to manage status manually
- DateIssued: date string YYYY-MM-DD
- DueDate: date string YYYY-MM-DD or null
- Reference: internal reference string
- QuoteNo: vendor quote number
- VendorNotes: notes visible to vendor (supports HTML)
- PrivateNotes: internal notes (supports HTML)
- ShowItemDueDate: boolean
- ExchangeRate: number (for foreign currency orders)
- Freight: object with ExTax and/or IncTax amounts

{get_api_hint("mutation_errors")}

Example payload:
{{
    "Vendor": 42,
    "Type": "Catalogue",
    "Stage": "Pending",
    "DateIssued": "2026-03-22",
    "Reference": "PO-2026-001",
    "AssignedTo": 789
}}
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "vendor_order_data": {
                    "type": "object",
                    "description": (
                        "Vendor order body. Required: Vendor (int ID of the supplier). "
                        "Optional: Type, Description, IsInventoryItem, Amount, "
                        "AssignedTo, StorageDevice, Stage, Status, StatusAutoAdjust, "
                        "DateIssued, DueDate, Reference, QuoteNo, VendorNotes, "
                        "PrivateNotes, ShowItemDueDate, ExchangeRate, Freight."
                    ),
                    "required": ["Vendor"],
                    "properties": {
                        "Vendor": {"type": "integer", "description": "ID of the supplier/vendor"},
                        "Type": {"type": "string", "enum": ["Catalogue", "Description"]},
                        "Description": {"type": "string"},
                        "IsInventoryItem": {"type": "boolean"},
                        "Amount": {"type": "number"},
                        "AssignedTo": {"type": "integer", "description": "ID of a job cost centre"},
                        "StorageDevice": {"type": "integer", "description": "ID of a storage device"},
                        "Stage": {"type": "string", "enum": ["Pending", "Approved", "Archived", "Voided"]},
                        "Status": {"type": "integer", "description": "ID of a vendor order status code"},
                        "StatusAutoAdjust": {"type": "boolean"},
                        "DateIssued": {"type": "string", "description": "YYYY-MM-DD"},
                        "DueDate": {"type": "string", "description": "YYYY-MM-DD or null"},
                        "Reference": {"type": "string"},
                        "QuoteNo": {"type": "string"},
                        "VendorNotes": {"type": "string"},
                        "PrivateNotes": {"type": "string"},
                        "ShowItemDueDate": {"type": "boolean"},
                        "ExchangeRate": {"type": "number"},
                        "Freight": {
                            "type": "object",
                            "properties": {
                                "ExTax": {"type": "number"},
                                "IncTax": {"type": "number"}
                            }
                        }
                    }
                }
            },
            "required": ["vendor_order_data"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        vendor_order_data = arguments["vendor_order_data"]

        result = await self.vendor_orders_api.create_vendor_order(
            vendor_order_data=vendor_order_data,
        )

        return {
            "success": True,
            "vendor_order": result,
            "vendor_order_id": result.get("ID"),
            "created": True,
        }


class UpdateVendorOrderTool(BaseTool):
    """
    Tool for updating (PATCHing) an existing vendor order.
    """

    def __init__(self):
        self.vendor_orders_api = VendorOrdersAPI()
        super().__init__()

    def get_name(self) -> str:
        return "update_vendor_order"

    def get_description(self) -> str:
        return f"""Update an existing vendor order (purchase/supplier/material order) in Simpro.

Use this tool when the user wants to modify, edit, or update an existing vendor order.
Only send the fields that need to change — this is a partial update (PATCH).
Returns 204 No Content on success.

Updatable fields:
- vendor_order_id: (required) ID of the vendor order to update
- Description: text (only applies when order type is "Description")
- IsInventoryItem: boolean (only when type is "Description")
- Amount: ex-tax amount (only when type is "Description")
- Vendor: integer ID of the supplier/vendor
- AssignedTo: integer ID of a job cost centre
- StorageDevice: integer ID of a storage device
- Stage: "Pending" | "Approved" | "Archived" | "Voided"
  - Archived/Voided orders cannot have receipts created against them
- Status: integer ID of a vendor order status code
- StatusAutoAdjust: set false to manage status manually
- DateIssued: YYYY-MM-DD
- DueDate: YYYY-MM-DD or null
- Reference: internal reference string
- QuoteNo: vendor quote number
- VendorNotes: notes visible to vendor (supports HTML)
- PrivateNotes: internal notes (supports HTML)
- ShowItemDueDate: boolean
- Archived: boolean (prefer using Stage instead)
- ExchangeRate: number
- Freight: object with ExTax and/or IncTax amounts

{get_api_hint("mutation_errors", "patch_semantics")}
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "vendor_order_id": {
                    "type": "integer",
                    "description": "The ID of the vendor order to update"
                },
                "vendor_order_data": {
                    "type": "object",
                    "description": (
                        "Fields to update (partial update). Only include fields that need changing. "
                        "Optional fields: Description, IsInventoryItem, Amount, Vendor, AssignedTo, "
                        "StorageDevice, Stage, Status, StatusAutoAdjust, DateIssued, DueDate, "
                        "Reference, QuoteNo, VendorNotes, PrivateNotes, ShowItemDueDate, "
                        "Archived, ExchangeRate, Freight."
                    ),
                    "properties": {
                        "Description": {"type": "string"},
                        "IsInventoryItem": {"type": "boolean"},
                        "Amount": {"type": "number"},
                        "Vendor": {"type": "integer", "description": "ID of the supplier/vendor"},
                        "AssignedTo": {"type": "integer", "description": "ID of a job cost centre"},
                        "StorageDevice": {"type": "integer", "description": "ID of a storage device"},
                        "Stage": {"type": "string", "enum": ["Pending", "Approved", "Archived", "Voided"]},
                        "Status": {"type": "integer", "description": "ID of a vendor order status code"},
                        "StatusAutoAdjust": {"type": "boolean"},
                        "DateIssued": {"type": "string", "description": "YYYY-MM-DD"},
                        "DueDate": {"type": "string", "description": "YYYY-MM-DD or null"},
                        "Reference": {"type": "string"},
                        "QuoteNo": {"type": "string"},
                        "VendorNotes": {"type": "string"},
                        "PrivateNotes": {"type": "string"},
                        "ShowItemDueDate": {"type": "boolean"},
                        "Archived": {"type": "boolean"},
                        "ExchangeRate": {"type": "number"},
                        "Freight": {
                            "type": "object",
                            "properties": {
                                "ExTax": {"type": "number"},
                                "IncTax": {"type": "number"}
                            }
                        }
                    }
                }
            },
            "required": ["vendor_order_id", "vendor_order_data"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        vendor_order_id = arguments["vendor_order_id"]
        vendor_order_data = arguments["vendor_order_data"]

        await self.vendor_orders_api.update_vendor_order(
            vendor_order_id=vendor_order_id,
            vendor_order_data=vendor_order_data,
        )

        return {
            "success": True,
            "vendor_order_id": vendor_order_id,
            "updated": True,
        }


class DeleteVendorOrderTool(BaseTool):
    """
    Tool for deleting an existing vendor order.
    """

    def __init__(self):
        self.vendor_orders_api = VendorOrdersAPI()
        super().__init__()

    def get_name(self) -> str:
        return "delete_vendor_order"

    def get_description(self) -> str:
        return f"""Delete an existing vendor order (purchase/supplier/material order) from Simpro.

Use this tool when the user wants to delete or remove a vendor order.
Returns 204 on success, 404 if the vendor order does not exist.

{get_api_hint("mutation_errors")}
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "vendor_order_id": {
                    "type": "integer",
                    "description": "The ID of the vendor order to delete"
                }
            },
            "required": ["vendor_order_id"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        vendor_order_id = arguments["vendor_order_id"]

        await self.vendor_orders_api.delete_vendor_order(
            vendor_order_id=vendor_order_id,
        )

        return {
            "success": True,
            "vendor_order_id": vendor_order_id,
            "deleted": True,
        }