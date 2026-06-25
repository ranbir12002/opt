#mcp-simpro-server/src/tools
"""
Employee-related MCP tools.

Provides tools for listing and viewing employees in Simpro.
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.simpro.client import get_simpro_client
from src.simpro_api_reference import get_api_hint
from src.utils import get_logger

from .base import BaseTool

logger = get_logger(__name__)


class ListEmployeesTool(BaseTool):
    """
    Tool for listing all employees in Simpro.
    """

    def __init__(self):
        """Initialize list employees tool"""
        super().__init__()

    def get_name(self) -> str:
        return "list_employees"

    def get_description(self) -> str:
        return f"""List all employees in Simpro with pagination and search.

Use this tool when the user asks about employees, wants to find an employee,
or needs to get a list of employees.

Filterable fields (use in 'filters' param): Name, ID

Examples:
- "Show me all employees"
- "List employees"
- "Find employee John" → filters: {{"Name": "%John%"}}
- "Who are the employees?"

{get_api_hint("search_operators", "pagination")}
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "columns": {
                    "type": "string",
                    "description": "Comma-separated list of columns to return (e.g. 'ID,Name')",
                    "default": "ID,Name"
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
        """Execute list employees with auto-pagination to fetch all pages."""
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        page_size = arguments.get("page_size", 250)
        columns = arguments.get("columns", "ID,Name")
        search = arguments.get("search", "all")
        orderby = arguments.get("orderby")
        limit = arguments.get("limit")

        filters = self.extract_filters(arguments)

        endpoint = f"/v1.0/companies/{company_id}/employees/"

        all_employees: List[Dict[str, Any]] = []
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
                all_employees.extend(result)
                if len(result) < page_size:
                    break
                current_page += 1
            else:
                break

        logger.info(f"list_employees: fetched {len(all_employees)} employees across {current_page} page(s)")

        return {
            "employees": all_employees,
            "total_fetched": len(all_employees),
            "pages_fetched": current_page
        }


class GetEmployeeDetailsTool(BaseTool):
    """
    Tool for getting detailed information about a specific employee.
    """

    def __init__(self):
        """Initialize get employee details tool"""
        super().__init__()

    def get_name(self) -> str:
        return "get_employee_details"

    def get_description(self) -> str:
        return """Get detailed information about a specific employee by ID.

Use this tool when the user asks for details about a specific employee,
wants to see employee contact info, address, pay rates, availability,
assigned cost centers, zones, or any other employee data.

Returns: ID, Name, Position, Availability, Address, DateOfHire, DateOfBirth,
PrimaryContact, EmergencyContact, AccountSetup, UserProfile, Archived,
AssignedCostCenters, Zones, DefaultZone, DefaultCompany, CustomFields,
Banking, PayRates, and more.

Examples:
- "Show me details for employee 123"
- "What's the contact info for employee 456?"
- "Get employee 789 details"
- "What position does employee 42 have?"
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "employee_id": {
                    "type": "integer",
                    "description": "The ID of the employee to retrieve"
                },
                "columns": {
                    "type": "string",
                    "description": "Comma-separated list of columns to return (e.g. 'ID,Name,Position')"
                }
            },
            "required": ["employee_id"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get employee details"""
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        employee_id = arguments["employee_id"]
        columns = arguments.get("columns")

        params = {}
        if columns:
            params["columns"] = columns

        # Call Simpro API
        endpoint = f"/v1.0/companies/{company_id}/employees/{employee_id}"
        result = await client.get(endpoint, params=params if params else None)

        return {
            "employee": result,
            "employee_id": employee_id
        }
