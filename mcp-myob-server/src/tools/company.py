"""MyOB Company tools — Company Info, List Company Files."""
from __future__ import annotations

from typing import Any, Dict

from src.myob.api.company import CompanyAPI
from src.myob.auth import MyOBAuth

from .base import BaseTool


class MyOBGetCompanyInfoTool(BaseTool):
    def __init__(self):
        self.api = CompanyAPI()
        super().__init__()

    def get_name(self) -> str:
        return "myob_get_company_info"

    def get_description(self) -> str:
        return "Get company file information from MyOB AccountRight (name, serial number, version, etc.)."

    def get_input_schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return await self.api.get_company_info()


class MyOBListCompanyFilesTool(BaseTool):
    def __init__(self):
        self.auth = MyOBAuth()
        super().__init__()

    def get_name(self) -> str:
        return "myob_list_company_files"

    def get_description(self) -> str:
        return (
            "List all company files accessible to the authenticated MyOB user.\n"
            "Returns file name, ID, URI, and other metadata.\n"
            "Useful for discovering which company file to connect to."
        )

    def get_input_schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        files = await self.auth.discover_company_files()
        return {"company_files": files, "total": len(files)}
