#mcp-simpro-server/src/tools
"""
Work Order-related MCP tools.

Provides tools for viewing work orders in Simpro.
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.simpro.api.work_orders import WorkOrdersAPI
from src.simpro_api_reference import get_api_hint
from src.utils import get_logger

from .base import BaseTool

logger = get_logger(__name__)


class GetWorkOrdersByCostCentreTool(BaseTool):
    """
    Tool for getting work orders for a specific cost centre.
    """
    
    def __init__(self):
        """Initialize get work orders by cost centre tool"""
        self.work_orders_api = WorkOrdersAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "get_work_orders_by_cost_centre"
    
    def get_description(self) -> str:
        return """Get work orders for a specific job cost centre.
        
Use this tool when the user asks about work orders for a cost centre,
job work orders, or technician assignments.

Examples:
- "Show me work orders for job 123, section 456, cost centre 789"
- "Get work orders for this cost centre"
- "What work orders are in cost centre 5?"
"""
    
    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "integer",
                    "description": "The ID of the job"
                },
                "section_id": {
                    "type": "integer",
                    "description": "The ID of the section"
                },
                "cost_centre_id": {
                    "type": "integer",
                    "description": "The ID of the cost centre"
                }
            },
            "required": ["job_id", "section_id", "cost_centre_id"]
        }
    
    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get work orders by cost centre"""
        job_id = arguments["job_id"]
        section_id = arguments["section_id"]
        cost_centre_id = arguments["cost_centre_id"]
        
        # Call Simpro API
        result = await self.work_orders_api.get_work_orders_by_cost_centre(
            job_id=job_id,
            section_id=section_id,
            cost_centre_id=cost_centre_id
        )
        
        return {
            "work_orders": result,
            "job_id": job_id,
            "section_id": section_id,
            "cost_centre_id": cost_centre_id
        }


class GetAllJobWorkOrdersTool(BaseTool):
    """
    Tool for getting all job work orders.
    """
    
    def __init__(self):
        """Initialize get all job work orders tool"""
        self.work_orders_api = WorkOrdersAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "get_all_job_work_orders"
    
    def get_description(self) -> str:
        return f"""Get work orders for specific jobs or search all work orders.

PREFERRED tool for checking work orders. ALWAYS use filters to narrow results — do NOT call without filters unless user explicitly wants ALL work orders.

Examples:
- "Do these jobs have work orders?" → filters: {{"Job.ID": "in(id1,id2,id3)"}}
- "Active work orders" → filters: {{"Status": "Active"}}
- "Work orders for a customer" → filters: {{"Customer.ID": "690"}}
- "Work orders at a site" → filters: {{"Site.Name": "%keyword%"}}

{get_api_hint("search_operators", "pagination")}
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
            }
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get all job work orders with auto-pagination."""
        page_size = arguments.get("page_size", 250)
        max_pages = 20  # Safety guard against runaway pagination

        filters = self.extract_filters(arguments)

        # Auto-paginate to fetch results
        all_work_orders: List[Dict[str, Any]] = []
        current_page = 1
        while current_page <= max_pages:
            result = await self.work_orders_api.get_all_job_work_orders(
                page=current_page,
                page_size=page_size,
                **filters,
            )
            if isinstance(result, list):
                all_work_orders.extend(result)
                if len(result) < page_size:
                    break
                current_page += 1
            else:
                break

        logger.info(f"get_all_job_work_orders: fetched {len(all_work_orders)} work orders across {current_page} page(s)")

        return {
            "work_orders": all_work_orders,
            "total_fetched": len(all_work_orders),
            "pages_fetched": current_page
        }


class GetWorkOrderDetailsTool(BaseTool):
    """
    Tool for getting detailed information about a specific work order.
    """
    
    def __init__(self):
        """Initialize get work order details tool"""
        self.work_orders_api = WorkOrdersAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "get_work_order_details"
    
    def get_description(self) -> str:
        return """Get detailed information about a specific work order by ID.
        
Use this tool when the user asks for details about a specific work order,
wants to see work order information, or needs work order status.

Examples:
- "Show me details for work order 12345"
- "What's in work order 67890?"
- "Get information about work order ID 111"
"""
    
    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "work_order_id": {
                    "type": "integer",
                    "description": "The ID of the work order to retrieve"
                }
            },
            "required": ["work_order_id"]
        }
    
    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get work order details"""
        work_order_id = arguments["work_order_id"]
        
        # Call Simpro API
        result = await self.work_orders_api.get_work_order_by_id(
            work_order_id=work_order_id
        )
        
        return {
            "work_order": result,
            "work_order_id": work_order_id
        }