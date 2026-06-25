"""MyOB Payroll API wrapper — Payroll Categories, Timesheets."""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.myob.client import get_myob_client
from src.myob.odata import build_query_params


class PayrollAPI:
    def __init__(self):
        self.client = get_myob_client()

    async def search_payroll_categories(
        self, top: int = 400, skip: int = 0,
        filter_expr: Optional[str] = None, orderby: Optional[str] = None,
    ) -> Any:
        params = build_query_params(top=top, skip=skip, filter_expr=filter_expr, orderby=orderby)
        return await self.client.get("/Payroll/PayrollCategory", params=params)

    async def get_timesheet(
        self, top: int = 400, skip: int = 0,
        filter_expr: Optional[str] = None,
    ) -> Any:
        params = build_query_params(top=top, skip=skip, filter_expr=filter_expr)
        return await self.client.get("/Payroll/Timesheet", params=params)
