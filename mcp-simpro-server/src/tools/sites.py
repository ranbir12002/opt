#mcp-simpro-server/src/tools
"""
Site-related MCP tools.

Provides tools for searching and viewing sites in Simpro.
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.simpro.api.sites import SitesAPI
from src.simpro_api_reference import get_api_hint
from src.utils import get_logger

from .base import BaseTool

logger = get_logger(__name__)


class SearchSitesTool(BaseTool):
    """
    Tool for searching sites in Simpro.
    """
    
    def __init__(self):
        """Initialize search sites tool"""
        self.sites_api = SitesAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "search_sites"
    
    def get_description(self) -> str:
        return f"""Search for sites/locations in Simpro with optional filters.

Use this tool when the user asks about sites, locations, addresses,
or wants to find a site.

Filterable fields (use in 'filters' param): Name, Address, City, State

Examples:
- "Show me all sites" → no filters
- "Find sites in Melbourne" → search: "Melbourne"
- "Sites on Bloomfield Ave" → filters: {{"Name": "%Bloomfield%"}}

{get_api_hint("search_operators", "pagination")}
"""
    
    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "search": {
                    "type": "string",
                    "description": "Search term for filtering sites"
                }
            }
        }
    
    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute site search with auto-pagination to fetch all pages."""
        page_size = arguments.get("page_size", 250)
        search = arguments.get("search")

        filters = self.extract_filters(arguments)

        # Auto-paginate to fetch all results
        all_sites: List[Dict[str, Any]] = []
        current_page = 1
        while True:
            result = await self.sites_api.get_sites(
                page=current_page,
                page_size=page_size,
                search=search,
                **filters
            )
            if isinstance(result, list):
                all_sites.extend(result)
                if len(result) < page_size:
                    break
                current_page += 1
            else:
                break

        logger.info(f"search_sites: fetched {len(all_sites)} sites across {current_page} page(s)")

        return {
            "sites": all_sites,
            "total_fetched": len(all_sites),
            "pages_fetched": current_page
        }


class GetSiteDetailsTool(BaseTool):
    """
    Tool for getting detailed information about a specific site.
    """
    
    def __init__(self):
        """Initialize get site details tool"""
        self.sites_api = SitesAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "get_site_details"
    
    def get_description(self) -> str:
        return """Get detailed information about a specific site by ID.
        
Use this tool when the user asks for details about a specific site,
wants to see site information, or needs to check a site's address.

Examples:
- "Show me details for site 12345"
- "What's the address of site 67890?"
- "Get information about site ID 111"
"""
    
    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "site_id": {
                    "type": "integer",
                    "description": "The ID of the site to retrieve"
                }
            },
            "required": ["site_id"]
        }
    
    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get site details"""
        site_id = arguments["site_id"]
        
        # Call Simpro API
        result = await self.sites_api.get_site_by_id(site_id=site_id)
        
        return {
            "site": result,
            "site_id": site_id
        }