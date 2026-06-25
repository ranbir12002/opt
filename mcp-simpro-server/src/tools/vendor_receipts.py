"""
Vendor Receipt-related MCP tools.

Provides tools for viewing vendor receipts in Simpro.
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.simpro.api.vendor_receipts import VendorReceiptsAPI
from src.simpro_api_reference import get_api_hint
from src.utils import get_logger

from .base import BaseTool

logger = get_logger(__name__)


class GetVendorReceiptsTool(BaseTool):
    """
    Tool for getting vendor receipts.
    """
    
    def __init__(self):
        """Initialize get vendor receipts tool"""
        self.vendor_receipts_api = VendorReceiptsAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "get_vendor_receipts"
    
    def get_description(self) -> str:
        return f"""Get list of vendor receipts in Simpro with optional filters.

Use this tool when the user asks about vendor receipts, goods received,
supplier receipts, or delivery records.

Filterable fields (use in 'filters' param): Status, Vendor.CompanyName,
Vendor.ID, DateReceived, OrderNo, Reference

Examples:
- "Show me all vendor receipts" → no filters
- "Receipts from vendor ABC" → filters: {{"Vendor.CompanyName": "%ABC%"}}
- "Receipts this month" → filters: {{"DateReceived": "ge(2026-02-01)"}}

{get_api_hint("search_operators", "pagination")}
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "display": {
                    "type": "string",
                    "description": "Display filter (e.g., 'all')",
                    "default": "all"
                }
            }
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get vendor receipts with auto-pagination to fetch all pages."""
        page_size = arguments.get("page_size", 250)
        display = arguments.get("display", "all")

        filters = self.extract_filters(arguments)

        # Auto-paginate to fetch all results
        all_receipts: List[Dict[str, Any]] = []
        current_page = 1
        while True:
            result = await self.vendor_receipts_api.get_vendor_receipts(
                page=current_page,
                page_size=page_size,
                display=display,
                **filters,
            )
            if isinstance(result, list):
                all_receipts.extend(result)
                if len(result) < page_size:
                    break
                current_page += 1
            else:
                break

        logger.info(f"get_vendor_receipts: fetched {len(all_receipts)} receipts across {current_page} page(s)")

        return {
            "vendor_receipts": all_receipts,
            "total_fetched": len(all_receipts),
            "pages_fetched": current_page
        }


class GetVendorReceiptDetailsTool(BaseTool):
    """
    Tool for getting detailed information about a specific vendor receipt.
    """
    
    def __init__(self):
        """Initialize get vendor receipt details tool"""
        self.vendor_receipts_api = VendorReceiptsAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "get_vendor_receipt_details"
    
    def get_description(self) -> str:
        return """Get detailed information about a specific vendor receipt by ID.

Use this tool when the user asks for details about a vendor receipt,
goods received details, or delivery information.

IMPORTANT — display parameter:
Set display='all' to fetch the vendor receipt WITH all its subresources (line items,
quantities, pricing) in a SINGLE API call.
Use display='all' when:
- The user asks about what items were received in a vendor receipt
- You need line item details, quantities, or pricing breakdown
- You would otherwise need to call a separate line items endpoint
Do NOT use display='all' when you only need basic receipt info (date, vendor, status).

Examples:
- "Show me vendor receipt 12345" → display=None (basic info only)
- "What items were received in vendor receipt 67890?" → display='all'
- "Get the full breakdown of vendor receipt 111" → display='all'
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "vendor_receipt_id": {
                    "type": "integer",
                    "description": "The ID of the vendor receipt to retrieve"
                },
                "display": {
                    "type": "string",
                    "description": "Set to 'all' to include all subresources (line items) in one call. Omit for basic receipt info only.",
                    "enum": ["all"]
                }
            },
            "required": ["vendor_receipt_id"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get vendor receipt details"""
        vendor_receipt_id = arguments["vendor_receipt_id"]
        display = arguments.get("display")

        # Call Simpro API
        result = await self.vendor_receipts_api.get_vendor_receipt_by_id(
            vendor_receipt_id=vendor_receipt_id,
            display=display
        )
        
        return {
            "vendor_receipt": result,
            "vendor_receipt_id": vendor_receipt_id
        }