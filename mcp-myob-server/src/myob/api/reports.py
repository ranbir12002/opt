"""MyOB Reports API wrapper — P&L, GST, Payroll Summary."""
from __future__ import annotations

from typing import Any, Optional

from src.myob.client import get_myob_client
from src.myob.odata import build_query_params


class ReportsAPI:
    def __init__(self):
        self.client = get_myob_client()

    async def get_profit_loss(self) -> Any:
        return await self.client.get("/Report/ProfitAndLossSummary")

    async def get_gst_report(self) -> Any:
        return await self.client.get("/Report/GST/NZGSTReport")

    async def get_payroll_summary(self) -> Any:
        return await self.client.get("/Report/PayrollCategorySummary")

    async def get_tax_code_summary(self) -> Any:
        return await self.client.get("/Report/TaxCodeSummary")
