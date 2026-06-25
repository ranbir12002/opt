"""MyOB Banking API wrapper — Spend Money, Receive Money, Transfer Money."""
from __future__ import annotations

from typing import Any, Optional

from src.myob.client import get_myob_client
from src.myob.odata import build_query_params


class BankingAPI:
    def __init__(self):
        self.client = get_myob_client()

    async def search_spend_money(
        self, top: int = 400, skip: int = 0,
        filter_expr: Optional[str] = None, orderby: Optional[str] = None,
    ) -> Any:
        params = build_query_params(top=top, skip=skip, filter_expr=filter_expr, orderby=orderby)
        return await self.client.get("/Banking/SpendMoneyTxn", params=params)

    async def search_receive_money(
        self, top: int = 400, skip: int = 0,
        filter_expr: Optional[str] = None, orderby: Optional[str] = None,
    ) -> Any:
        params = build_query_params(top=top, skip=skip, filter_expr=filter_expr, orderby=orderby)
        return await self.client.get("/Banking/ReceiveMoneyTxn", params=params)

    async def search_transfers(
        self, top: int = 400, skip: int = 0,
        filter_expr: Optional[str] = None, orderby: Optional[str] = None,
    ) -> Any:
        params = build_query_params(top=top, skip=skip, filter_expr=filter_expr, orderby=orderby)
        return await self.client.get("/Banking/TransferMoneyTxn", params=params)
