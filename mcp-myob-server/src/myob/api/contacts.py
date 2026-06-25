"""MyOB Contacts API wrapper — Customers, Suppliers, Employees."""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.myob.client import get_myob_client
from src.myob.odata import build_query_params


class ContactsAPI:
    def __init__(self):
        self.client = get_myob_client()

    # ── Customers ─────────────────────────────────────────────────────

    async def search_customers(
        self, top: int = 400, skip: int = 0,
        filter_expr: Optional[str] = None, orderby: Optional[str] = None,
    ) -> Any:
        params = build_query_params(top=top, skip=skip, filter_expr=filter_expr, orderby=orderby)
        return await self.client.get("/Contact/Customer", params=params)

    async def get_customer(self, uid: str) -> Dict[str, Any]:
        return await self.client.get(f"/Contact/Customer/{uid}")

    # ── Suppliers ─────────────────────────────────────────────────────

    async def search_suppliers(
        self, top: int = 400, skip: int = 0,
        filter_expr: Optional[str] = None, orderby: Optional[str] = None,
    ) -> Any:
        params = build_query_params(top=top, skip=skip, filter_expr=filter_expr, orderby=orderby)
        return await self.client.get("/Contact/Supplier", params=params)

    async def get_supplier(self, uid: str) -> Dict[str, Any]:
        return await self.client.get(f"/Contact/Supplier/{uid}")

    # ── Employees ─────────────────────────────────────────────────────

    async def search_employees(
        self, top: int = 400, skip: int = 0,
        filter_expr: Optional[str] = None, orderby: Optional[str] = None,
    ) -> Any:
        params = build_query_params(top=top, skip=skip, filter_expr=filter_expr, orderby=orderby)
        return await self.client.get("/Contact/Employee", params=params)

    async def get_employee(self, uid: str) -> Dict[str, Any]:
        return await self.client.get(f"/Contact/Employee/{uid}")
