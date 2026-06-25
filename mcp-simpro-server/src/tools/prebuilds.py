"""
Prebuild-related MCP tools.

Provides tools for searching and viewing prebuilds in Simpro.
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.simpro.api.prebuilds import PrebuildsAPI
from src.simpro_api_reference import get_api_hint
from src.utils import get_logger

from .base import BaseTool

logger = get_logger(__name__)


class SearchPrebuildsTool(BaseTool):
    """
    Tool for searching prebuilds in Simpro.
    """
    
    def __init__(self):
        """Initialize search prebuilds tool"""
        self.prebuilds_api = PrebuildsAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "search_prebuilds"
    
    def get_description(self) -> str:
        return f"""Search for prebuilds in Simpro with optional filters.

Use this tool when the user asks about prebuilds, catalog items,
standard items, or wants to find specific parts.

Filterable fields (use in 'filters' param): PartNo, Name, Description

Examples:
- "Show me all prebuilds" → no filters
- "Find prebuild by part number ABC123" → part_no: "ABC123"
- "Search for prebuilds containing 'plumbing'" → search: "plumbing"
- "Prebuilds named cable" → filters: {{"Name": "%cable%"}}

{get_api_hint("search_operators", "pagination")}
"""
    
    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "part_no": {
                    "type": "string",
                    "description": "Filter by part number"
                },
                "search": {
                    "type": "string",
                    "description": "Search term"
                }
            }
        }
    
    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute prebuild search with auto-pagination to fetch all pages."""
        page_size = arguments.get("page_size", 250)
        part_no = arguments.get("part_no")
        search = arguments.get("search")

        filters = self.extract_filters(arguments)

        # Auto-paginate to fetch all results
        all_prebuilds: List[Dict[str, Any]] = []
        current_page = 1
        while True:
            result = await self.prebuilds_api.get_prebuilds(
                page=current_page,
                page_size=page_size,
                part_no=part_no,
                search=search,
                **filters
            )
            if isinstance(result, list):
                all_prebuilds.extend(result)
                if len(result) < page_size:
                    break
                current_page += 1
            else:
                break

        logger.info(f"search_prebuilds: fetched {len(all_prebuilds)} prebuilds across {current_page} page(s)")

        return {
            "prebuilds": all_prebuilds,
            "total_fetched": len(all_prebuilds),
            "pages_fetched": current_page
        }


class GetPrebuildDetailsTool(BaseTool):
    """
    Tool for getting detailed information about a specific prebuild.
    """
    
    def __init__(self):
        """Initialize get prebuild details tool"""
        self.prebuilds_api = PrebuildsAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "get_prebuild_details"
    
    def get_description(self) -> str:
        return """Get detailed information about a specific prebuild by ID.
        
Use this tool when the user asks for details about a specific prebuild,
wants to see prebuild pricing, or needs prebuild specifications.

Examples:
- "Show me details for prebuild 12345"
- "What's the price of prebuild 67890?"
- "Get specifications for prebuild ID 111"
"""
    
    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prebuild_id": {
                    "type": "integer",
                    "description": "The ID of the prebuild to retrieve"
                }
            },
            "required": ["prebuild_id"]
        }
    
    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get prebuild details"""
        prebuild_id = arguments["prebuild_id"]
        
        # Call Simpro API
        result = await self.prebuilds_api.get_prebuild_by_id(prebuild_id=prebuild_id)
        
        return {
            "prebuild": result,
            "prebuild_id": prebuild_id
        }


class GetPrebuildGroupsTool(BaseTool):
    """
    Tool for getting prebuild groups.
    """
    
    def __init__(self):
        """Initialize get prebuild groups tool"""
        self.prebuilds_api = PrebuildsAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "get_prebuild_groups"
    
    def get_description(self) -> str:
        return """Get list of prebuild groups in Simpro.
        
Use this tool when the user asks about prebuild categories, groups,
or wants to see how prebuilds are organized.

Examples:
- "Show me all prebuild groups"
- "List prebuild categories"
- "What groups are available for prebuilds?"
"""
    
    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {}
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get prebuild groups with auto-pagination to fetch all pages."""
        page_size = arguments.get("page_size", 250)

        all_groups: List[Dict[str, Any]] = []
        current_page = 1
        while True:
            result = await self.prebuilds_api.get_prebuild_groups(
                page=current_page,
                page_size=page_size
            )
            if isinstance(result, list):
                all_groups.extend(result)
                if len(result) < page_size:
                    break
                current_page += 1
            else:
                break

        logger.info(f"get_prebuild_groups: fetched {len(all_groups)} groups across {current_page} page(s)")

        return {
            "groups": all_groups,
            "total_fetched": len(all_groups),
            "pages_fetched": current_page
        }