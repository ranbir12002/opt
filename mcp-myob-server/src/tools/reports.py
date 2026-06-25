"""MyOB Report tools — Profit & Loss, GST, Payroll Summary."""
from __future__ import annotations

from typing import Any, Dict

from src.myob.api.reports import ReportsAPI

from .base import BaseTool


class MyOBGetProfitLossTool(BaseTool):
    def __init__(self):
        self.api = ReportsAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_get_profit_loss"

    def get_description(self) -> str:
        return "Get the Profit and Loss summary report from MyOB AccountRight."

    def get_input_schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return await self.api.get_profit_loss()


class MyOBGetGSTReportTool(BaseTool):
    def __init__(self):
        self.api = ReportsAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_get_gst_report"

    def get_description(self) -> str:
        return "Get the GST report from MyOB AccountRight (NZ/AU tax)."

    def get_input_schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return await self.api.get_gst_report()


class MyOBGetPayrollSummaryTool(BaseTool):
    def __init__(self):
        self.api = ReportsAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_get_payroll_summary"

    def get_description(self) -> str:
        return "Get the payroll category summary report from MyOB AccountRight."

    def get_input_schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return await self.api.get_payroll_summary()
