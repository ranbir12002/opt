"""
Company-related MCP tools.

Provides tools for viewing company information in Simpro.
"""
from __future__ import annotations

from typing import Any, Dict

from src.simpro.api.companies import CompaniesAPI
from src.utils import get_logger

from .base import BaseTool

logger = get_logger(__name__)


class GetCompaniesTool(BaseTool):
    """
    Tool for getting list of companies.
    """
    
    def __init__(self):
        """Initialize get companies tool"""
        self.companies_api = CompaniesAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "get_companies"
    
    def get_description(self) -> str:
        return """Get list of companies in Simpro.
        
Use this tool when the user asks about companies, company list,
or wants to see available companies.

Examples:
- "Show me all companies"
- "List companies"
- "What companies are in the system?"
"""
    
    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {}
        }
    
    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get companies"""
        
        # Call Simpro API
        result = await self.companies_api.get_companies()
        
        return {
            "companies": result
        }


class GetCompanyDetailsTool(BaseTool):
    """
    Tool for getting detailed information about a specific company.
    """
    
    def __init__(self):
        """Initialize get company details tool"""
        self.companies_api = CompaniesAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "get_company_details"
    
    def get_description(self) -> str:
        return """Get detailed information about a specific company by ID.
        
Use this tool when the user asks for details about a specific company,
wants to see company information, or needs company settings.

Examples:
- "Show me details for company 2"
- "What's the information for company ID 1?"
- "Get company 2 details"
"""
    
    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "company_id": {
                    "type": "integer",
                    "description": "The ID of the company to retrieve"
                }
            },
            "required": ["company_id"]
        }
    
    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get company details"""
        company_id = arguments["company_id"]
        
        # Call Simpro API
        result = await self.companies_api.get_company_by_id(
            company_id=company_id
        )
        
        return {
            "company": result,
            "company_id": company_id
        }