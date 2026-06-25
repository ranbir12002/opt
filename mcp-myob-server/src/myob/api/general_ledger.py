"""MyOB General Ledger API wrapper — Accounts, Jobs, Tax Codes, Categories."""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.myob.client import get_myob_client
from src.myob.odata import build_query_params


class GeneralLedgerAPI:
    def __init__(self):
        self.client = get_myob_client()

    # ── Accounts ──────────────────────────────────────────────────────

    async def search_accounts(
        self, top: int = 400, skip: int = 0,
        filter_expr: Optional[str] = None, orderby: Optional[str] = None,
    ) -> Any:
        params = build_query_params(top=top, skip=skip, filter_expr=filter_expr, orderby=orderby)
        return await self.client.get("/GeneralLedger/Account", params=params)

    async def get_account(self, uid: str) -> Dict[str, Any]:
        return await self.client.get(f"/GeneralLedger/Account/{uid}")

    # ── Jobs ──────────────────────────────────────────────────────────

    async def search_jobs(
        self, top: int = 400, skip: int = 0,
        filter_expr: Optional[str] = None, orderby: Optional[str] = None,
    ) -> Any:
        params = build_query_params(top=top, skip=skip, filter_expr=filter_expr, orderby=orderby)
        return await self.client.get("/GeneralLedger/Job", params=params)

    async def get_job(self, uid: str) -> Dict[str, Any]:
        return await self.client.get(f"/GeneralLedger/Job/{uid}")

    # ── Tax Codes ─────────────────────────────────────────────────────

    async def search_tax_codes(
        self, top: int = 400, skip: int = 0,
        filter_expr: Optional[str] = None, orderby: Optional[str] = None,
    ) -> Any:
        params = build_query_params(top=top, skip=skip, filter_expr=filter_expr, orderby=orderby)
        return await self.client.get("/GeneralLedger/TaxCode", params=params)

    # ── Categories ────────────────────────────────────────────────────

    async def search_categories(
        self, top: int = 400, skip: int = 0,
        filter_expr: Optional[str] = None, orderby: Optional[str] = None,
    ) -> Any:
        params = build_query_params(top=top, skip=skip, filter_expr=filter_expr, orderby=orderby)
        return await self.client.get("/GeneralLedger/Category", params=params)
