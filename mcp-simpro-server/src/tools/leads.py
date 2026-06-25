#mcp-simpro-server/src/tools
"""
Lead-related MCP tools.

Provides tools for viewing leads in Simpro.
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.simpro.api.leads import LeadsAPI
from src.simpro_api_reference import get_api_hint
from src.utils import get_logger

from .base import BaseTool

logger = get_logger(__name__)


class GetLeadsTool(BaseTool):
    """
    Tool for getting leads in Simpro.
    """
    
    def __init__(self):
        """Initialize get leads tool"""
        self.leads_api = LeadsAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "get_leads"
    
    def get_description(self) -> str:
        return f"""Get list of leads in Simpro with optional filters.

Use this tool when the user asks about leads, prospects,
potential customers, or sales opportunities.

Filterable fields (use in 'filters' param): IsOpen, Status, DateCreated,
Customer.ID, Customer.CompanyName

Examples:
- "Show me all open leads" → is_open: true
- "Leads for customer Smith" → filters: {{"Customer.CompanyName": "%Smith%"}}

{get_api_hint("search_operators", "pagination")}
"""
    
    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "is_open": {
                    "type": "boolean",
                    "description": "Filter by open status (true = open, false = closed)"
                }
            }
        }
    
    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get leads with auto-pagination to fetch all pages."""
        page_size = arguments.get("page_size", 250)
        is_open = arguments.get("is_open")

        filters = self.extract_filters(arguments)

        # Auto-paginate to fetch all results
        all_leads: List[Dict[str, Any]] = []
        current_page = 1
        while True:
            result = await self.leads_api.get_leads(
                page=current_page,
                page_size=page_size,
                is_open=is_open,
                **filters
            )
            if isinstance(result, list):
                all_leads.extend(result)
                if len(result) < page_size:
                    break
                current_page += 1
            else:
                break

        logger.info(f"get_leads: fetched {len(all_leads)} leads across {current_page} page(s)")

        return {
            "leads": all_leads,
            "total_fetched": len(all_leads),
            "pages_fetched": current_page,
            "filter": f"open={is_open}" if is_open is not None else "all"
        }


class GetLeadDetailsTool(BaseTool):
    """
    Tool for getting detailed information about a specific lead.
    """
    
    def __init__(self):
        """Initialize get lead details tool"""
        self.leads_api = LeadsAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "get_lead_details"
    
    def get_description(self) -> str:
        return """Get detailed information about a specific lead by ID.
        
Use this tool when the user asks for details about a specific lead,
wants to see lead information, or needs lead status.

Examples:
- "Show me details for lead 12345"
- "What's the status of lead 67890?"
- "Get information about lead ID 111"
"""
    
    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "lead_id": {
                    "type": "integer",
                    "description": "The ID of the lead to retrieve"
                }
            },
            "required": ["lead_id"]
        }
    
    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get lead details"""
        lead_id = arguments["lead_id"]
        
        # Call Simpro API
        result = await self.leads_api.get_lead_by_id(lead_id=lead_id)
        
        return {
            "lead": result,
            "lead_id": lead_id
        }