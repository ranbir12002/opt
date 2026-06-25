"""
Customer Payment-related MCP tools.

Provides tools for viewing and managing customer payments in Simpro.
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.simpro.api.customer_payments import CustomerPaymentsAPI
from src.simpro_api_reference import get_api_hint
from src.utils import get_logger

from .base import BaseTool

logger = get_logger(__name__)


class GetCustomerPaymentsTool(BaseTool):
    """
    Tool for getting customer payments in Simpro.
    """
    
    def __init__(self):
        """Initialize get customer payments tool"""
        self.payments_api = CustomerPaymentsAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "get_customer_payments"
    
    def get_description(self) -> str:
        return f"""Get list of customer payments in Simpro with optional filters.

Use this tool when the user asks about payments, customer payments,
received payments, or payment history.

Filterable fields (use in 'filters' param): Customer.ID, Date, Amount

Examples:
- "Show me all customer payments" → no filters
- "Payments for customer 690" → filters: {{"Customer.ID": "690"}}
- "Payments after Jan 2026" → filters: {{"Date": "ge(2026-01-01)"}}
- "Payments over $1000" → filters: {{"Amount": "gt(1000)"}}

{get_api_hint("search_operators", "pagination")}
"""
    
    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
            }
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get customer payments with auto-pagination to fetch all pages."""
        page_size = arguments.get("page_size", 250)

        filters = self.extract_filters(arguments)

        # Auto-paginate to fetch all results
        all_payments: List[Dict[str, Any]] = []
        current_page = 1
        while True:
            result = await self.payments_api.get_customer_payments(
                page=current_page,
                page_size=page_size,
                **filters
            )
            if isinstance(result, list):
                all_payments.extend(result)
                if len(result) < page_size:
                    break
                current_page += 1
            else:
                break

        logger.info(f"get_customer_payments: fetched {len(all_payments)} payments across {current_page} page(s)")

        return {
            "payments": all_payments,
            "total_fetched": len(all_payments),
            "pages_fetched": current_page
        }


class GetCustomerPaymentDetailsTool(BaseTool):
    """
    Tool for getting detailed information about a specific customer payment.
    """
    
    def __init__(self):
        """Initialize get customer payment details tool"""
        self.payments_api = CustomerPaymentsAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "get_customer_payment_details"
    
    def get_description(self) -> str:
        return """Get detailed information about a specific customer payment by ID.
        
Use this tool when the user asks for details about a specific payment,
wants to see payment information, or needs payment breakdown.

Examples:
- "Show me details for payment 12345"
- "What invoices does payment 67890 cover?"
- "Get information about payment ID 111"
"""
    
    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "payment_id": {
                    "type": "integer",
                    "description": "The ID of the payment to retrieve"
                }
            },
            "required": ["payment_id"]
        }
    
    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get customer payment details"""
        payment_id = arguments["payment_id"]
        
        # Call Simpro API
        result = await self.payments_api.get_customer_payment_by_id(
            payment_id=payment_id
        )
        
        return {
            "payment": result,
            "payment_id": payment_id
        }