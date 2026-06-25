"""MyOB Company API wrapper — Company Info, Preferences, Company Files."""
from __future__ import annotations

from typing import Any, Dict

from src.myob.client import get_myob_client


class CompanyAPI:
    def __init__(self):
        self.client = get_myob_client()

    async def get_company_info(self) -> Dict[str, Any]:
        return await self.client.get("/Info")

    async def get_company_preferences(self) -> Dict[str, Any]:
        return await self.client.get("/Company/Preferences")

    async def get_current_user(self) -> Dict[str, Any]:
        return await self.client.get("/CurrentUser")
