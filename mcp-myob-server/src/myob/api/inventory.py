"""MyOB Inventory API wrapper — Items, Adjustments."""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.myob.client import get_myob_client
from src.myob.odata import build_query_params


class InventoryAPI:
    def __init__(self):
        self.client = get_myob_client()

    async def search_items(
        self, top: int = 400, skip: int = 0,
        filter_expr: Optional[str] = None, orderby: Optional[str] = None,
    ) -> Any:
        params = build_query_params(top=top, skip=skip, filter_expr=filter_expr, orderby=orderby)
        return await self.client.get("/Inventory/Item", params=params)

    async def get_item(self, uid: str) -> Dict[str, Any]:
        return await self.client.get(f"/Inventory/Item/{uid}")
