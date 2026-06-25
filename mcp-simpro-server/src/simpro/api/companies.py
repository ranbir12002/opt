"""
Companies API wrapper for Simpro.

Handles company-related API calls.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.simpro.client import get_simpro_client
from src.utils import get_logger

logger = get_logger(__name__)


class CompaniesAPI:
    """
    API wrapper for Simpro companies endpoints.
    
    Provides methods for:
    - Getting company information
    - Getting company list
    """
    
    def __init__(self):
        """Initialize Companies API"""
        logger.debug("Companies API initialized")
    
    async def get_companies(
        self,
        columns: Optional[str] = None
    ) -> list[Dict[str, Any]]:
        """
        Get list of companies.
        
        Args:
            columns: Optional columns to include
        
        Returns:
            List of companies
        """
        client = get_simpro_client()
        logger.info("Fetching companies")

        endpoint = "/v1.0/companies/"

        params = {}
        if columns:
            params["columns"] = columns

        result = await client.get(endpoint, params=params)
        return result

    async def get_company_by_id(
        self,
        company_id: int,
        columns: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get a specific company by ID.
        
        Args:
            company_id: Company ID
            columns: Optional columns to include
        
        Returns:
            Company data
        """
        client = get_simpro_client()
        logger.info(f"Fetching company {company_id}")

        endpoint = f"/v1.0/companies/{company_id}"

        params = {}
        if columns:
            params["columns"] = columns

        result = await client.get(endpoint, params=params)
        return result