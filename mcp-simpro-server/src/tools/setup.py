"""
Setup-related MCP tools.

Provides tools for viewing setup and configuration data in Simpro.
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.simpro.api.setup import SetupAPI
from src.utils import get_logger

from .base import BaseTool

logger = get_logger(__name__)


class GetLaborRatesTool(BaseTool):
    """
    Tool for getting labor rates.
    """
    
    def __init__(self):
        """Initialize get labor rates tool"""
        self.setup_api = SetupAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "get_labor_rates"
    
    def get_description(self) -> str:
        return """Get list of labor rates in Simpro.
        
Use this tool when the user asks about labor rates, hourly rates,
pricing rates, or technician rates.

Examples:
- "Show me all labor rates"
- "What are the hourly rates?"
- "List labor rates"
"""
    
    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {}
        }
    
    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get labor rates with auto-pagination to fetch all pages."""
        page_size = arguments.get("page_size", 250)

        # Auto-paginate to fetch all results
        all_rates: List[Dict[str, Any]] = []
        current_page = 1
        while True:
            result = await self.setup_api.get_labor_rates(
                page=current_page,
                page_size=page_size
            )
            if isinstance(result, list):
                all_rates.extend(result)
                if len(result) < page_size:
                    break
                current_page += 1
            else:
                break

        logger.info(f"get_labor_rates: fetched {len(all_rates)} rates across {current_page} page(s)")

        return {
            "labor_rates": all_rates,
            "total_fetched": len(all_rates),
            "pages_fetched": current_page
        }


class GetTeamsTool(BaseTool):
    """
    Tool for listing all teams in Simpro.
    Endpoint: GET /api/v1.0/companies/{companyID}/setup/teams/
    """

    def __init__(self):
        """Initialize list teams tool"""
        super().__init__()

    def get_name(self) -> str:
        return "list_teams"

    def get_description(self) -> str:
        return """List all teams in Simpro. Returns team ID and name for each team.

Teams represent department groupings (e.g. Underground Drainage, Above Ground Plumbing, Roofing).
Use this tool to discover available teams before fetching team-specific details like
cost centres and staff members.

Use this tool when the user asks about:
- What teams/departments exist
- Getting a list of all teams
- Finding a team by name

To get cost centres and technicians for a specific team, use get_team_details with the team ID.

Examples:
- "Show me all teams"
- "List teams"
- "What departments does H-Team have?"
- "What teams are available?"
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "columns": {
                    "type": "string",
                    "description": "Comma-separated columns to return. Default: 'ID,Name'",
                    "default": "ID,Name"
                },
                "search": {
                    "type": "string",
                    "description": "Search mode: 'all' (match all fields) or 'any' (match any field)",
                    "default": "all",
                    "enum": ["all", "any"]
                },
                "orderby": {
                    "type": "string",
                    "description": "Comma-separated columns to order by. Prefix with '-' for descending (e.g. 'Name' or '-Name,ID')"
                },
                "limit": {
                    "type": "integer",
                    "description": "Limit the number of records returned"
                }
            }
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute list teams with auto-pagination."""
        from src.simpro.client import get_simpro_client
        client = get_simpro_client()
        company_id = client.auth.get_company_id()

        page_size = 250
        columns = arguments.get("columns", "ID,Name")
        search = arguments.get("search", "all")
        orderby = arguments.get("orderby")
        limit = arguments.get("limit")
        filters = self.extract_filters(arguments)

        endpoint = f"/v1.0/companies/{company_id}/setup/teams/"

        all_teams: List[Dict[str, Any]] = []
        current_page = 1
        while True:
            params: Dict[str, Any] = {
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
                all_teams.extend(result)
                if len(result) < page_size:
                    break
                current_page += 1
            else:
                break

        logger.info(f"list_teams: fetched {len(all_teams)} teams across {current_page} page(s)")

        return {
            "teams": all_teams,
            "total_fetched": len(all_teams),
            "pages_fetched": current_page,
        }


class GetTeamDetailsTool(BaseTool):
    """
    Tool for getting a specific team's details including cost centres and technicians.
    Endpoint: GET /api/v1.0/companies/{companyID}/setup/teams/{teamID}
    """

    def __init__(self):
        """Initialize get team details tool"""
        self.setup_api = SetupAPI()
        super().__init__()

    def get_name(self) -> str:
        return "get_team_details"

    def get_description(self) -> str:
        return """Get full details for a specific team by ID.

Returns:
- ID, Name
- Availability: team working hours (StartDay/StartTime/EndDay/EndTime per entry)
- CostCenters: list of cost centres assigned to this team [{ID, Name}]
- Members: list of staff in this team [{ID, Name, Type (employee/contractor/plant), TypeId}]
- Zones: geographic zones assigned to this team [{ID, Name}]

Use this tool when you need to know:
- Which staff members (employees/contractors/plant) belong to a team
- Which cost centres are assigned to a team
- What hours/availability the team operates on
- Which zones the team covers

Typical workflow: call list_teams first to get team IDs, then call this tool with the team ID.

Examples:
- "Who is in the roofing team?"
- "What cost centres does team 97 have?"
- "Show me the technicians in the drainage team"
- "What are the working hours for team 97?"
- "Get details for team 97"
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "team_id": {
                    "type": "integer",
                    "description": "The ID of the team to retrieve. Use list_teams to find team IDs."
                },
                "columns": {
                    "type": "string",
                    "description": "Comma-separated columns to return (e.g. 'ID,Name')"
                }
            },
            "required": ["team_id"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get team details."""
        team_id = arguments["team_id"]
        columns = arguments.get("columns")

        result = await self.setup_api.get_team_by_id(team_id=team_id, columns=columns)

        logger.info(f"get_team_details: fetched team {team_id}")

        return {
            "team": result,
            "team_id": team_id,
        }


class GetSetupCostCentresTool(BaseTool):
    """
    Tool for getting setup cost centres (account-linked definitions).
    Different from get_cost_centre_types which returns /jobCostCenters/.
    """

    def __init__(self):
        self.setup_api = SetupAPI()
        super().__init__()

    def get_name(self) -> str:
        return "get_setup_cost_centres"

    def get_description(self) -> str:
        return """Get setup cost centres (account-linked cost centre definitions) from Simpro.

Each setup cost centre has an IncomeAccountNo linking it to the chart of accounts.
This is DIFFERENT from 'get_cost_centre_types' (which returns /jobCostCenters/).

Use this tool when the user asks about:
- Setup cost centres and their income account links
- Which cost centres map to which accounts
- Department classification of cost centres

Returns: [{ID, Name}, ...] — use get_setup_cost_centre_detail for IncomeAccountNo and other fields.
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "columns": {
                    "type": "string",
                    "description": "Comma-separated columns (e.g. 'ID,Name,IncomeAccountNo')"
                }
            }
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get setup cost centres with auto-pagination."""
        page_size = arguments.get("page_size", 250)
        columns = arguments.get("columns")

        all_items: List[Dict[str, Any]] = []
        current_page = 1
        while True:
            result = await self.setup_api.get_setup_cost_centres(
                page=current_page, page_size=page_size, columns=columns,
            )
            if isinstance(result, list):
                all_items.extend(result)
                if len(result) < page_size:
                    break
                current_page += 1
            else:
                break

        logger.info(f"get_setup_cost_centres: fetched {len(all_items)} items across {current_page} page(s)")

        return {
            "setup_cost_centres": all_items,
            "total_fetched": len(all_items),
            "pages_fetched": current_page,
        }


class GetSetupCostCentreDetailTool(BaseTool):
    """
    Tool for getting a specific setup cost centre by ID.
    Returns full details including IncomeAccountNo.
    """

    def __init__(self):
        self.setup_api = SetupAPI()
        super().__init__()

    def get_name(self) -> str:
        return "get_setup_cost_centre_detail"

    def get_description(self) -> str:
        return """Get details for a specific setup cost centre by ID.

Returns full cost centre details including IncomeAccountNo, ExpenseAccountNo,
budget fields, Archived status, and linked Rates.

Use this tool when you need the IncomeAccountNo for department classification,
or other detailed attributes of a setup cost centre.

Returns: {ID, Name, IncomeAccountNo, ExpenseAccountNo, AccrualRevAccountNo,
          DeferralRevAccountNo, MonthlySalesBudget, MonthlyExpenditureBudget,
          Archived, IsMembershipCostCenter, Rates}
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "cost_center_id": {
                    "type": "integer",
                    "description": "Setup cost centre ID"
                }
            },
            "required": ["cost_center_id"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get setup cost centre detail."""
        cost_center_id = arguments["cost_center_id"]
        result = await self.setup_api.get_setup_cost_centre_by_id(cost_center_id)

        logger.info(f"get_setup_cost_centre_detail: fetched cost centre {cost_center_id}")

        return {"cost_centre": result}


class GetChartOfAccountsTool(BaseTool):
    """
    Tool for getting chart of accounts entries.
    """

    def __init__(self):
        self.setup_api = SetupAPI()
        super().__init__()

    def get_name(self) -> str:
        return "get_chart_of_accounts"

    def get_description(self) -> str:
        return """Get chart of accounts entries from Simpro.

Returns all account entries. Used together with setup cost centres
to determine department classification via IncomeAccountNo mapping.

Use this tool when the user asks about:
- Chart of accounts
- Income accounts
- Account numbers or account types

Returns: [{ID, Name}, ...] — use get_chart_of_accounts_detail for Number, Type, Archived.
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "columns": {
                    "type": "string",
                    "description": "Comma-separated columns (e.g. 'ID,Name,Number,Type')"
                }
            }
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get chart of accounts with auto-pagination."""
        page_size = arguments.get("page_size", 250)
        columns = arguments.get("columns")

        all_accounts: List[Dict[str, Any]] = []
        current_page = 1
        while True:
            result = await self.setup_api.get_chart_of_accounts(
                page=current_page, page_size=page_size, columns=columns,
            )
            if isinstance(result, list):
                all_accounts.extend(result)
                if len(result) < page_size:
                    break
                current_page += 1
            else:
                break

        logger.info(f"get_chart_of_accounts: fetched {len(all_accounts)} accounts across {current_page} page(s)")

        return {
            "chart_of_accounts": all_accounts,
            "total_fetched": len(all_accounts),
            "pages_fetched": current_page,
        }


class GetChartOfAccountsDetailTool(BaseTool):
    """
    Tool for getting a specific chart of accounts entry by ID.
    """

    def __init__(self):
        self.setup_api = SetupAPI()
        super().__init__()

    def get_name(self) -> str:
        return "get_chart_of_accounts_detail"

    def get_description(self) -> str:
        return """Get details for a specific chart of accounts entry by ID.

Returns full account details including Number and Type.

Returns: {ID, Name, Number, Type, Archived}
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "account_id": {
                    "type": "integer",
                    "description": "Chart of accounts entry ID"
                }
            },
            "required": ["account_id"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get chart of accounts detail."""
        account_id = arguments["account_id"]
        result = await self.setup_api.get_chart_of_accounts_by_id(account_id)

        logger.info(f"get_chart_of_accounts_detail: fetched account {account_id}")

        return {"account": result}