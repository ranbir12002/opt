#mcp-simpro-server/src/tools
"""
Contractor-related MCP tools.

Provides tools for listing and viewing contractors in Simpro.
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.simpro.client import get_simpro_client
from src.simpro_api_reference import get_api_hint
from src.utils import get_logger

from .base import BaseTool

logger = get_logger(__name__)


class ListContractorsTool(BaseTool):
    """
    Tool for listing all contractors in Simpro.
    """

    def __init__(self):
        """Initialize list contractors tool"""
        super().__init__()

    def get_name(self) -> str:
        return "list_contractors"

    def get_description(self) -> str:
        return f"""List all contractors in Simpro with pagination and search.

Use this tool when the user asks about contractors, wants to find a contractor,
or needs to get a list of contractors.

Filterable fields (use in 'filters' param): Name, ContactName

Examples:
- "Show me all contractors"
- "List contractors"
- "Find contractor ABC Plumbing" → filters: {{"Name": "%ABC Plumbing%"}}
- "Who are the contractors?"

{get_api_hint("search_operators", "pagination")}
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "columns": {
                    "type": "string",
                    "description": "Comma-separated list of columns to return (e.g. 'ID,Name,ContactName')",
                    "default": "ID,Name,ContactName"
                },
                "orderby": {
                    "type": "string",
                    "description": "Column to order by, prefix with '-' for descending (e.g. 'Name' or '-Name')"
                },
                "search": {
                    "type": "string",
                    "description": "Search mode: 'all' (match all fields) or 'any' (match any field)",
                    "default": "all",
                    "enum": ["all", "any"]
                },
                "limit": {
                    "type": "integer",
                    "description": "Limit the number of records returned"
                }
            }
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute list contractors with auto-pagination to fetch all pages."""
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        page_size = arguments.get("page_size", 250)
        columns = arguments.get("columns", "ID,Name,ContactName")
        search = arguments.get("search", "all")
        orderby = arguments.get("orderby")
        limit = arguments.get("limit")

        filters = self.extract_filters(arguments)

        endpoint = f"/v1.0/companies/{company_id}/contractors/"

        all_contractors: List[Dict[str, Any]] = []
        current_page = 1
        while True:
            params = {
                "page": current_page,
                "pageSize": page_size,
                "columns": columns,
                "search": search,
                **filters,
            }
            if orderby:
                params["orderby"] = orderby
            if limit:
                params["limit"] = limit

            result = await client.get(endpoint, params=params)
            if isinstance(result, list):
                all_contractors.extend(result)
                if len(result) < page_size:
                    break
                current_page += 1
            else:
                break

        logger.info(f"list_contractors: fetched {len(all_contractors)} contractors across {current_page} page(s)")

        return {
            "contractors": all_contractors,
            "total_fetched": len(all_contractors),
            "pages_fetched": current_page
        }


class GetContractorDetailsTool(BaseTool):
    """
    Tool for getting detailed information about a specific contractor.
    """

    def __init__(self):
        """Initialize get contractor details tool"""
        super().__init__()

    def get_name(self) -> str:
        return "get_contractor_details"

    def get_description(self) -> str:
        return """Get detailed information about a specific contractor by ID.

Use this tool when the user asks for details about a specific contractor,
wants to see contractor contact info, address, rates, availability,
assigned cost centers, zones, banking, or any other contractor data.

Returns: ID, Name, Position, Availability, Address, DateOfHire, DateOfBirth,
PrimaryContact, EmergencyContact, AccountSetup, UserProfile, Archived,
AssignedCostCenters, Zones, DefaultZone, DefaultCompany, CustomFields,
ContactName, Currency, Banking, Rates, TaxCode, EIN, and more.

Examples:
- "Show me details for contractor 123"
- "What's the contact info for contractor 456?"
- "Get contractor 789 details"
- "What are the rates for contractor 42?"
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "contractor_id": {
                    "type": "integer",
                    "description": "The ID of the contractor to retrieve"
                },
                "columns": {
                    "type": "string",
                    "description": "Comma-separated list of columns to return (e.g. 'ID,Name,ContactName')"
                }
            },
            "required": ["contractor_id"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get contractor details"""
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        contractor_id = arguments["contractor_id"]
        columns = arguments.get("columns")

        params = {}
        if columns:
            params["columns"] = columns

        # Call Simpro API
        endpoint = f"/v1.0/companies/{company_id}/contractors/{contractor_id}"
        result = await client.get(endpoint, params=params if params else None)

        return {
            "contractor": result,
            "contractor_id": contractor_id
        }
