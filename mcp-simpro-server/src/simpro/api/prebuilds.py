"""
Prebuilds API wrapper for Simpro.

Handles all prebuild-related API calls.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.simpro.client import get_simpro_client
from src.utils import get_logger

logger = get_logger(__name__)


class PrebuildsAPI:
    """
    API wrapper for Simpro prebuilds endpoints.
    
    Provides methods for:
    - Searching prebuilds
    - Getting prebuild details
    - Getting prebuild groups
    """
    
    def __init__(self):
        """Initialize Prebuilds API"""
        logger.debug("Prebuilds API initialized")
    
    async def get_prebuilds(
        self,
        page: int = 1,
        page_size: int = 250,
        part_no: Optional[str] = None,
        search: Optional[str] = None,
        columns: Optional[str] = None,
        **filters
    ) -> list[Dict[str, Any]]:
        """
        Get list of prebuilds.

        Args:
            page: Page number (1-based)
            page_size: Number of prebuilds per page
            part_no: Filter by part number
            search: Search term
            columns: Comma-separated columns to include
            **filters: Additional Simpro API filters (passed as query params)

        Returns:
            List of prebuilds
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching prebuilds (page={page}, page_size={page_size})")

        endpoint = f"/v1.0/companies/{company_id}/catalogue/standardPrice/prebuilds/"

        params = {
            "page": page,
            "pageSize": page_size,
            **filters,
        }

        if part_no:
            params["PartNo"] = part_no

        if search:
            params["search"] = search

        if columns:
            params["columns"] = columns

        result = await client.get(endpoint, params=params)
        return result

    async def get_prebuild_by_id(
        self,
        prebuild_id: int,
        columns: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get a specific prebuild by ID.
        
        Args:
            prebuild_id: Prebuild ID
            columns: Optional columns to include
        
        Returns:
            Prebuild data
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching prebuild {prebuild_id}")

        endpoint = f"/v1.0/companies/{company_id}/catalogue/standardPrice/prebuilds/{prebuild_id}"
        
        params = {}
        if columns:
            params["columns"] = columns
        
        result = await client.get(endpoint, params=params)
        return result

    async def get_prebuild_groups(
        self,
        page: int = 1,
        page_size: int = 250
    ) -> list[Dict[str, Any]]:
        """
        Get list of prebuild groups.
        
        Args:
            page: Page number (1-based)
            page_size: Number of groups per page
        
        Returns:
            List of prebuild groups
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching prebuild groups (page={page})")

        endpoint = f"/v1.0/companies/{company_id}/catalogue/standardPrice/prebuildGroups/"
        
        params = {
            "page": page,
            "pageSize": page_size
        }
        
        result = await client.get(endpoint, params=params)
        return result

    async def get_set_price_prebuilds(
        self
    ) -> list[Dict[str, Any]]:
        """
        Get set price prebuilds.
        
        Returns:
            List of set price prebuilds
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info("Fetching set price prebuilds")

        endpoint = f"/v1.0/companies/{company_id}/catalogue/setPrice/prebuilds/"

        result = await client.get(endpoint)
        return result