"""
Tool registry for managing MyOB MCP tools.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from src.utils import get_logger

from .base import BaseTool

logger = get_logger(__name__)


class ToolRegistry:
    """Registry for MyOB MCP tools."""

    def __init__(self):
        self.tools: Dict[str, BaseTool] = {}
        logger.info("Tool registry initialized")

    def register(self, tool: BaseTool) -> None:
        name = tool.get_name()
        if name in self.tools:
            logger.warning(f"Tool {name} already registered, overwriting")
        self.tools[name] = tool
        logger.info(f"Registered tool: {name}")

    def register_many(self, tools: List[BaseTool]) -> None:
        for tool in tools:
            self.register(tool)

    def get(self, tool_name: str) -> Optional[BaseTool]:
        return self.tools.get(tool_name)

    def list_tools(self) -> List[BaseTool]:
        return list(self.tools.values())

    def list_tool_names(self) -> List[str]:
        return list(self.tools.keys())

    def get_tool_count(self) -> int:
        return len(self.tools)

    def get_tools_dict(self) -> List[Dict]:
        return [tool.to_dict() for tool in self.tools.values()]


# Global registry
_global_registry: Optional[ToolRegistry] = None


def get_tool_registry() -> ToolRegistry:
    global _global_registry
    if _global_registry is None:
        _global_registry = ToolRegistry()
    return _global_registry


def register_all_tools() -> ToolRegistry:
    """Register all MyOB tools. Called during startup."""
    registry = get_tool_registry()

    # Contacts
    from .contacts import (
        MyOBSearchCustomersTool, MyOBGetCustomerTool,
        MyOBSearchSuppliersTool, MyOBGetSupplierTool,
        MyOBSearchEmployeesTool, MyOBGetEmployeeTool,
    )

    # Sales (including invoice CRUD)
    from .sales import (
        MyOBSearchInvoicesTool, MyOBGetInvoiceTool,
        MyOBCreateInvoiceTool, MyOBUpdateInvoiceTool, MyOBDeleteInvoiceTool,
        MyOBSearchSaleOrdersTool, MyOBGetSaleOrderTool,
        MyOBSearchQuotesTool, MyOBGetQuoteTool,
        MyOBSearchCustomerPaymentsTool, MyOBGetCustomerPaymentTool,
    )

    # Purchases
    from .purchases import (
        MyOBSearchBillsTool, MyOBGetBillTool,
        MyOBSearchPurchaseOrdersTool, MyOBGetPurchaseOrderTool,
        MyOBSearchSupplierPaymentsTool,
    )

    # General Ledger
    from .general_ledger import (
        MyOBSearchAccountsTool, MyOBGetAccountTool,
        MyOBSearchJobsTool, MyOBGetJobTool,
        MyOBSearchTaxCodesTool, MyOBSearchCategoriesTool,
    )

    # Banking
    from .banking import (
        MyOBSearchSpendMoneyTool, MyOBSearchReceiveMoneyTool,
        MyOBSearchTransfersTool,
    )

    # Inventory
    from .inventory import (
        MyOBSearchItemsTool, MyOBGetItemTool,
    )

    # Reports
    from .reports import (
        MyOBGetProfitLossTool, MyOBGetGSTReportTool,
        MyOBGetPayrollSummaryTool,
    )

    # Payroll
    from .payroll import (
        MyOBSearchPayrollCategoriesTool, MyOBGetTimesheetTool,
    )

    # Company
    from .company import (
        MyOBGetCompanyInfoTool, MyOBListCompanyFilesTool,
    )

    tools = [
        # Contacts (6)
        MyOBSearchCustomersTool(), MyOBGetCustomerTool(),
        MyOBSearchSuppliersTool(), MyOBGetSupplierTool(),
        MyOBSearchEmployeesTool(), MyOBGetEmployeeTool(),

        # Sales (11)
        MyOBSearchInvoicesTool(), MyOBGetInvoiceTool(),
        MyOBCreateInvoiceTool(), MyOBUpdateInvoiceTool(), MyOBDeleteInvoiceTool(),
        MyOBSearchSaleOrdersTool(), MyOBGetSaleOrderTool(),
        MyOBSearchQuotesTool(), MyOBGetQuoteTool(),
        MyOBSearchCustomerPaymentsTool(), MyOBGetCustomerPaymentTool(),

        # Purchases (5)
        MyOBSearchBillsTool(), MyOBGetBillTool(),
        MyOBSearchPurchaseOrdersTool(), MyOBGetPurchaseOrderTool(),
        MyOBSearchSupplierPaymentsTool(),

        # General Ledger (6)
        MyOBSearchAccountsTool(), MyOBGetAccountTool(),
        MyOBSearchJobsTool(), MyOBGetJobTool(),
        MyOBSearchTaxCodesTool(), MyOBSearchCategoriesTool(),

        # Banking (3)
        MyOBSearchSpendMoneyTool(), MyOBSearchReceiveMoneyTool(),
        MyOBSearchTransfersTool(),

        # Inventory (2)
        MyOBSearchItemsTool(), MyOBGetItemTool(),

        # Reports (3)
        MyOBGetProfitLossTool(), MyOBGetGSTReportTool(),
        MyOBGetPayrollSummaryTool(),

        # Payroll (2)
        MyOBSearchPayrollCategoriesTool(), MyOBGetTimesheetTool(),

        # Company (2)
        MyOBGetCompanyInfoTool(), MyOBListCompanyFilesTool(),
    ]

    registry.register_many(tools)
    logger.info(f"Registered {len(tools)} MyOB tools across 9 categories")
    return registry
