#mcp-simpro-server/src/tools
"""
Customer-related MCP tools.

Provides tools for searching and viewing customers in Simpro.
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.simpro.client import get_simpro_client
from src.simpro_api_reference import get_api_hint
from src.utils import get_logger

from .base import BaseTool

logger = get_logger(__name__)


class SearchCustomersTool(BaseTool):
    """
    Tool for searching customers in Simpro.
    """
    
    def __init__(self):
        """Initialize search customers tool"""
        super().__init__()
    
    def get_name(self) -> str:
        return "search_customers"
    
    def get_description(self) -> str:
        return f"""Search for customers in Simpro with optional filters and pagination.

Use this tool when the user asks about customers, wants to find a customer,
or needs to get a list of customers.

Filterable fields (use in 'filters' param): CompanyName, GivenName, FamilyName,
DisplayName, Type, Status, ID

Examples:
- "Show me all customers" → no filters
- "Find customer ABC Construction" → filters: {{"CompanyName": "%ABC%"}}
- "Customers named Smith" → filters: {{"FamilyName": "%Smith%"}}
- "Active customers" → filters: {{"Status": "Active"}}

{get_api_hint("search_operators", "pagination")}
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
            }
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute customer search with auto-pagination to fetch all pages."""
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        page_size = arguments.get("page_size", 250)

        filters = self.extract_filters(arguments)

        endpoint = f"/v1.0/companies/{company_id}/customers/"

        all_customers: List[Dict[str, Any]] = []
        current_page = 1
        while True:
            result = await client.get(
                endpoint,
                params={
                    "page": current_page,
                    "pageSize": page_size,
                    **filters,
                }
            )
            if isinstance(result, list):
                all_customers.extend(result)
                if len(result) < page_size:
                    break
                current_page += 1
            else:
                break

        logger.info(f"search_customers: fetched {len(all_customers)} customers across {current_page} page(s)")

        return {
            "customers": all_customers,
            "total_fetched": len(all_customers),
            "pages_fetched": current_page
        }


class GetCustomerDetailsTool(BaseTool):
    """
    Tool for getting detailed information about a specific customer.
    """
    
    def __init__(self):
        """Initialize get customer details tool"""
        super().__init__()
    
    def get_name(self) -> str:
        return "get_customer_details"
    
    def get_description(self) -> str:
        return """Get detailed information about a specific customer by ID.
        
Use this tool when the user asks for details about a specific customer,
wants to see customer information, or needs to check customer data.

Examples:
- "Show me details for customer 123"
- "What's the information for customer ID 456?"
- "Get customer 789 details"
"""
    
    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "customer_id": {
                    "type": "integer",
                    "description": "The ID of the customer to retrieve"
                }
            },
            "required": ["customer_id"]
        }
    
    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get customer details"""
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        customer_id = arguments["customer_id"]

        # Call Simpro API
        endpoint = f"/v1.0/companies/{company_id}/customers/companies/{customer_id}"
        result = await client.get(endpoint)
        
        return {
            "customer": result,
            "customer_id": customer_id
        }