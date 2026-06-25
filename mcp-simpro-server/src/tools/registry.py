#mcp-simpro-server/src/tools
"""
Tool registry for managing MCP tools.

Maintains a registry of all available tools and provides
lookup and registration functionality.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from src.utils import get_logger

from .base import BaseTool

logger = get_logger(__name__)


class ToolRegistry:
    """
    Registry for MCP tools.
    
    Manages all available tools and provides lookup functionality.
    
    Example:
        >>> registry = ToolRegistry()
        >>> registry.register(SearchJobsTool())
        >>> registry.register(GetJobDetailsTool())
        >>> tool = registry.get("search_jobs")
        >>> result = await tool({"status": "Active"})
    """
    
    def __init__(self):
        """Initialize tool registry"""
        self.tools: Dict[str, BaseTool] = {}
        logger.info("Tool registry initialized")
    
    def register(self, tool: BaseTool) -> None:
        """
        Register a tool.
        
        Args:
            tool: Tool instance to register
        
        Raises:
            ValueError: If tool with same name already registered
        """
        tool_name = tool.get_name()
        
        if tool_name in self.tools:
            logger.warning(f"Tool {tool_name} already registered, overwriting")
        
        self.tools[tool_name] = tool
        logger.info(f"Registered tool: {tool_name}")
    
    def register_many(self, tools: List[BaseTool]) -> None:
        """
        Register multiple tools at once.
        
        Args:
            tools: List of tool instances
        """
        for tool in tools:
            self.register(tool)
    
    def get(self, tool_name: str) -> Optional[BaseTool]:
        """
        Get a tool by name.
        
        Args:
            tool_name: Name of the tool
        
        Returns:
            Tool instance or None if not found
        """
        return self.tools.get(tool_name)
    
    def has(self, tool_name: str) -> bool:
        """
        Check if a tool is registered.
        
        Args:
            tool_name: Name of the tool
        
        Returns:
            True if tool exists, False otherwise
        """
        return tool_name in self.tools
    
    def list_tools(self) -> List[BaseTool]:
        """
        Get list of all registered tools.
        
        Returns:
            List of tool instances
        """
        return list(self.tools.values())
    
    def list_tool_names(self) -> List[str]:
        """
        Get list of all registered tool names.
        
        Returns:
            List of tool names
        """
        return list(self.tools.keys())
    
    def get_tool_count(self) -> int:
        """
        Get number of registered tools.
        
        Returns:
            Tool count
        """
        return len(self.tools)
    
    def unregister(self, tool_name: str) -> bool:
        """
        Unregister a tool.
        
        Args:
            tool_name: Name of the tool to unregister
        
        Returns:
            True if tool was unregistered, False if not found
        """
        if tool_name in self.tools:
            del self.tools[tool_name]
            logger.info(f"Unregistered tool: {tool_name}")
            return True
        return False
    
    def clear(self) -> None:
        """Clear all registered tools"""
        count = len(self.tools)
        self.tools.clear()
        logger.info(f"Cleared {count} tools from registry")
    
    def get_tools_dict(self) -> List[Dict]:
        """
        Get all tools as dictionary format for MCP.
        
        Returns:
            List of tool dicts
        """
        return [tool.to_dict() for tool in self.tools.values()]


# ===================================================================
# Global tool registry instance
# ===================================================================
_global_registry: Optional[ToolRegistry] = None


def get_tool_registry() -> ToolRegistry:
    """
    Get or create global tool registry.
    
    Returns:
        ToolRegistry instance
    """
    global _global_registry
    
    if _global_registry is None:
        _global_registry = ToolRegistry()
        logger.info("Global tool registry created")
    
    return _global_registry


def register_all_tools():
    """
    Register all available tools.
    
    This function imports and registers all tool implementations.
    Call this during application startup.
    """
    registry = get_tool_registry()
    
    # ================================================================
    # Import all tool implementations
    # ================================================================
    
    # Jobs tools
    from .jobs import (
        SearchJobsTool,
        GetJobDetailsTool,
        GetJobSectionsTool,
    )
    
    # Customer tools
    from .customers import (
        SearchCustomersTool,
        GetCustomerDetailsTool,
    )
    
    # Quote tools
    from .quotes import (
        SearchQuotesTool,
        GetQuoteDetailsTool,
        GetQuoteSectionsTool,
        GetQuoteCostCentreSchedulesTool,
        CreateQuoteCostCentreScheduleTool,
        GetQuoteCostCentreScheduleDetailsTool,
        UpdateQuoteCostCentreScheduleTool,
        DeleteQuoteCostCentreScheduleTool,
    )
    
    # Invoice tools
    from .invoices import (
        SearchInvoicesTool,
        GetInvoiceDetailsTool,
        CreateInvoiceTool,
        UpdateInvoiceTool,
        DeleteInvoiceTool,
    )
    
    # Site tools
    from .sites import (
        SearchSitesTool,
        GetSiteDetailsTool,
    )
    
    # Cost Center tools (FIXED: cost_centers not cost_centres)
    from .cost_centers import (
        GetCostCentreTypesTool,
        GetJobSectionCostCentresTool,
        GetJobCostCentreDetailsTool,
        GetCostCentreCatalogItemsTool,
        GetCostCentreLabourItemsTool,
        GetCostCentreOneOffItemsTool,
        GetCostCentrePrebuildItemsTool,
    )
    
    # Prebuild tools
    from .prebuilds import (
        SearchPrebuildsTool,
        GetPrebuildDetailsTool,
        GetPrebuildGroupsTool,
    )
    
    # Schedule tools
    from .schedules import (
        GetSchedulesTool,
        GetScheduleDetailsTool,
        GetJobCostCentreSchedulesTool,
        GetJobCostCentreScheduleDetailsTool,
        CreateJobCostCentreScheduleTool,
        UpdateJobCostCentreScheduleTool,
        DeleteJobCostCentreScheduleTool,
    )
    
    # Lead tools
    from .leads import (
        GetLeadsTool,
        GetLeadDetailsTool,
    )
    
    # Contact tools
    from .contacts import (
        SearchContactsTool,
        GetContactDetailsTool,
    )
    
    # Credit Note tools
    from .credit_notes import (
        GetCreditNotesByInvoiceTool,
        GetCreditNoteDetailsTool,
    )
    
    # Customer Payment tools
    from .customer_payments import (
        GetCustomerPaymentsTool,
        GetCustomerPaymentDetailsTool,
    )
    
    # Work Order tools
    from .work_orders import (
        GetWorkOrdersByCostCentreTool,
        GetAllJobWorkOrdersTool,
        GetWorkOrderDetailsTool,
    )
    
    # Company tools
    from .companies import (
        GetCompaniesTool,
        GetCompanyDetailsTool,
    )
    
    # Setup tools
    from .setup import (
        GetLaborRatesTool,
        GetTeamsTool,
        GetTeamDetailsTool,
        GetSetupCostCentresTool,
        GetSetupCostCentreDetailTool,
        GetChartOfAccountsTool,
        GetChartOfAccountsDetailTool,
    )
    
    # Contractor Job tools
    from .contractor_jobs import (
        GetContractorJobDetailsTool,
        GetContractorJobsByCostCentreTool,
        CreateContractorJobTool,
        UpdateContractorJobTool,
        DeleteContractorJobTool,
    )
    
    # Vendor Order tools
    from .vendor_orders import (
        GetVendorOrdersTool,
        GetVendorOrderDetailsTool,
        GetVendorOrderReceiptTool,
        CreateVendorOrderTool,
        UpdateVendorOrderTool,
        DeleteVendorOrderTool,
    )
    
    # Vendor Receipt tools
    from .vendor_receipts import (
        GetVendorReceiptsTool,
        GetVendorReceiptDetailsTool,
    )

    # Employee tools
    from .employees import (
        ListEmployeesTool,
        GetEmployeeDetailsTool,
    )

    # Contractor tools
    from .contractors import (
        ListContractorsTool,
        GetContractorDetailsTool,
    )

    # Agent handoff tool — bridges MCP tool-calling loop → Python agents for CRUD
    from .handoff import HandoffToAgentTool

    # ================================================================
    # Register all tools
    # ================================================================
    
    tools = [
        # Jobs tools (3)
        SearchJobsTool(),
        GetJobDetailsTool(),
        GetJobSectionsTool(),
        
        # Customer tools (2)
        SearchCustomersTool(),
        GetCustomerDetailsTool(),
        
        # Quote tools (8)
        SearchQuotesTool(),
        GetQuoteDetailsTool(),
        GetQuoteSectionsTool(),
        GetQuoteCostCentreSchedulesTool(),
        CreateQuoteCostCentreScheduleTool(),
        GetQuoteCostCentreScheduleDetailsTool(),
        UpdateQuoteCostCentreScheduleTool(),
        DeleteQuoteCostCentreScheduleTool(),
        
        # Invoice tools (5)
        SearchInvoicesTool(),
        GetInvoiceDetailsTool(),
        CreateInvoiceTool(),
        UpdateInvoiceTool(),
        DeleteInvoiceTool(),
        
        # Site tools (2)
        SearchSitesTool(),
        GetSiteDetailsTool(),
        
        # Cost Centre tools (7)
        GetCostCentreTypesTool(),
        GetJobSectionCostCentresTool(),
        GetJobCostCentreDetailsTool(),
        GetCostCentreCatalogItemsTool(),
        GetCostCentreLabourItemsTool(),
        GetCostCentreOneOffItemsTool(),
        GetCostCentrePrebuildItemsTool(),
        
        # Prebuild tools (3)
        SearchPrebuildsTool(),
        GetPrebuildDetailsTool(),
        GetPrebuildGroupsTool(),
        
        # Schedule tools (7)
        GetSchedulesTool(),
        GetScheduleDetailsTool(),
        GetJobCostCentreSchedulesTool(),
        GetJobCostCentreScheduleDetailsTool(),
        CreateJobCostCentreScheduleTool(),
        UpdateJobCostCentreScheduleTool(),
        DeleteJobCostCentreScheduleTool(),
        
        # Lead tools (2)
        GetLeadsTool(),
        GetLeadDetailsTool(),
        
        # Contact tools (2)
        SearchContactsTool(),
        GetContactDetailsTool(),
        
        # Credit Note tools (2)
        GetCreditNotesByInvoiceTool(),
        GetCreditNoteDetailsTool(),
        
        # Customer Payment tools (2)
        GetCustomerPaymentsTool(),
        GetCustomerPaymentDetailsTool(),
        
        # Work Order tools (3)
        GetWorkOrdersByCostCentreTool(),
        GetAllJobWorkOrdersTool(),
        GetWorkOrderDetailsTool(),
        
        # Company tools (2)
        GetCompaniesTool(),
        GetCompanyDetailsTool(),
        
        # Setup tools (7)
        GetLaborRatesTool(),
        GetTeamsTool(),
        GetTeamDetailsTool(),
        GetSetupCostCentresTool(),
        GetSetupCostCentreDetailTool(),
        GetChartOfAccountsTool(),
        GetChartOfAccountsDetailTool(),
        
        # Contractor Job tools (5)
        GetContractorJobDetailsTool(),
        GetContractorJobsByCostCentreTool(),
        CreateContractorJobTool(),
        UpdateContractorJobTool(),
        DeleteContractorJobTool(),
        
        # Vendor Order tools (6)
        GetVendorOrdersTool(),
        GetVendorOrderDetailsTool(),
        GetVendorOrderReceiptTool(),
        CreateVendorOrderTool(),
        UpdateVendorOrderTool(),
        DeleteVendorOrderTool(),
        
        # Vendor Receipt tools (2)
        GetVendorReceiptsTool(),
        GetVendorReceiptDetailsTool(),

        # Employee tools (2)
        ListEmployeesTool(),
        GetEmployeeDetailsTool(),

        # Contractor tools (2)
        ListContractorsTool(),
        GetContractorDetailsTool(),

        # Agent handoff (1)
        HandoffToAgentTool(),
    ]

    registry.register_many(tools)

    logger.info(f"Registered {len(tools)} tools across 19 categories")  # 61 total
    return registry