"""MyOB Purchases API wrapper — Bills, Purchase Orders, Supplier Payments."""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.myob.client import get_myob_client
from src.myob.odata import build_query_params


class PurchasesAPI:
    def __init__(self):
        self.client = get_myob_client()

    # ── Bills ─────────────────────────────────────────────────────────

    async def search_bills(
        self, top: int = 400, skip: int = 0,
        filter_expr: Optional[str] = None, orderby: Optional[str] = None,
    ) -> Any:
        params = build_query_params(top=top, skip=skip, filter_expr=filter_expr, orderby=orderby)
        return await self.client.get("/Purchase/Bill", params=params)

    async def get_bill(self, uid: str, bill_type: str = "Item") -> Dict[str, Any]:
        return await self.client.get(f"/Purchase/Bill/{bill_type}/{uid}")

    # ── Purchase Orders ───────────────────────────────────────────────

    async def search_purchase_orders(
        self, top: int = 400, skip: int = 0,
        filter_expr: Optional[str] = None, orderby: Optional[str] = None,
    ) -> Any:
        params = build_query_params(top=top, skip=skip, filter_expr=filter_expr, orderby=orderby)
        return await self.client.get("/Purchase/Order", params=params)

    async def get_purchase_order(self, uid: str, order_type: str = "Item") -> Dict[str, Any]:
        return await self.client.get(f"/Purchase/Order/{order_type}/{uid}")

    # ── Supplier Payments ─────────────────────────────────────────────

    async def search_supplier_payments(
        self, top: int = 400, skip: int = 0,
        filter_expr: Optional[str] = None, orderby: Optional[str] = None,
    ) -> Any:
        params = build_query_params(top=top, skip=skip, filter_expr=filter_expr, orderby=orderby)
        return await self.client.get("/Purchase/SupplierPayment", params=params)
