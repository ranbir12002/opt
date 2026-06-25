"""MyOB Sales API wrapper — Invoices, Orders, Quotes, Customer Payments."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.myob.client import get_myob_client
from src.myob.odata import build_query_params

# MyOB invoice sub-types
INVOICE_TYPES = ["Item", "Service", "Professional", "TimeBilling", "Miscellaneous"]


class SalesAPI:
    def __init__(self):
        self.client = get_myob_client()

    # ── Invoices ──────────────────────────────────────────────────────

    async def search_invoices_all_types(
        self, top: int = 400, skip: int = 0,
        filter_expr: Optional[str] = None, orderby: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search across ALL invoice types and merge results."""
        params = build_query_params(top=top, skip=skip, filter_expr=filter_expr, orderby=orderby)
        all_invoices = []
        for inv_type in INVOICE_TYPES:
            try:
                result = await self.client.get(f"/Sale/Invoice/{inv_type}", params=params)
                items = result if isinstance(result, list) else result.get("Items", [])
                for item in items:
                    item["_InvoiceType"] = inv_type
                all_invoices.extend(items)
            except Exception:
                # Some company files may not have all types enabled
                continue
        return all_invoices

    async def search_invoices(
        self, top: int = 400, skip: int = 0,
        filter_expr: Optional[str] = None, orderby: Optional[str] = None,
    ) -> Any:
        """Search all invoices (combined endpoint)."""
        params = build_query_params(top=top, skip=skip, filter_expr=filter_expr, orderby=orderby)
        return await self.client.get("/Sale/Invoice", params=params)

    async def get_invoice(self, uid: str, invoice_type: str = "Item") -> Dict[str, Any]:
        return await self.client.get(f"/Sale/Invoice/{invoice_type}/{uid}")

    async def create_invoice(self, invoice_type: str, data: Dict[str, Any]) -> Any:
        return await self.client.post(f"/Sale/Invoice/{invoice_type}", json=data)

    async def update_invoice(self, uid: str, invoice_type: str, data: Dict[str, Any]) -> Any:
        return await self.client.put(f"/Sale/Invoice/{invoice_type}/{uid}", json=data)

    async def delete_invoice(self, uid: str, invoice_type: str) -> Any:
        return await self.client.delete(
            f"/Sale/Invoice/{invoice_type}/{uid}",
            json={"UID": uid},
        )

    # ── Sale Orders ───────────────────────────────────────────────────

    async def search_sale_orders(
        self, top: int = 400, skip: int = 0,
        filter_expr: Optional[str] = None, orderby: Optional[str] = None,
    ) -> Any:
        params = build_query_params(top=top, skip=skip, filter_expr=filter_expr, orderby=orderby)
        return await self.client.get("/Sale/Order", params=params)

    async def get_sale_order(self, uid: str, order_type: str = "Item") -> Dict[str, Any]:
        return await self.client.get(f"/Sale/Order/{order_type}/{uid}")

    # ── Quotes ────────────────────────────────────────────────────────

    async def search_quotes(
        self, top: int = 400, skip: int = 0,
        filter_expr: Optional[str] = None, orderby: Optional[str] = None,
    ) -> Any:
        params = build_query_params(top=top, skip=skip, filter_expr=filter_expr, orderby=orderby)
        return await self.client.get("/Sale/Quote", params=params)

    async def get_quote(self, uid: str, quote_type: str = "Item") -> Dict[str, Any]:
        return await self.client.get(f"/Sale/Quote/{quote_type}/{uid}")

    # ── Customer Payments ─────────────────────────────────────────────

    async def search_customer_payments(
        self, top: int = 400, skip: int = 0,
        filter_expr: Optional[str] = None, orderby: Optional[str] = None,
    ) -> Any:
        params = build_query_params(top=top, skip=skip, filter_expr=filter_expr, orderby=orderby)
        return await self.client.get("/Sale/CustomerPayment", params=params)

    async def get_customer_payment(self, uid: str) -> Dict[str, Any]:
        return await self.client.get(f"/Sale/CustomerPayment/{uid}")
