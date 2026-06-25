"""
Sites API wrapper for Simpro.

Handles all site-related API calls.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.simpro.client import get_simpro_client
from src.utils import get_logger

logger = get_logger(__name__)


class SitesAPI:
    """
    API wrapper for Simpro sites endpoints.
    
    Provides methods for:
    - Searching sites
    - Getting site details
    """
    
    def __init__(self):
        """Initialize Sites API"""
        logger.debug("Sites API initialized")
    
    async def get_sites(
        self,
        page: int = 1,
        page_size: int = 250,
        columns: Optional[str] = None,
        search: Optional[str] = None,
        **filters
    ) -> list[Dict[str, Any]]:
        """
        Get list of sites.

        Args:
            page: Page number (1-based)
            page_size: Number of sites per page
            columns: Comma-separated columns to include
            search: Search term
            **filters: Additional Simpro API filters (passed as query params)

        Returns:
            List of sites
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching sites (page={page}, page_size={page_size})")

        endpoint = f"/v1.0/companies/{company_id}/sites/"

        params = {
            "page": page,
            "pageSize": page_size,
            **filters,
        }

        if columns:
            params["columns"] = columns

        if search:
            params["search"] = search

        result = await client.get(endpoint, params=params)
        return result

    async def get_site_by_id(
        self,
        site_id: int,
        columns: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get a specific site by ID.
        
        Args:
            site_id: Site ID
            columns: Optional columns to include
        
        Returns:
            Site data
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching site {site_id}")

        endpoint = f"/v1.0/companies/{company_id}/sites/{site_id}"
        
        params = {}
        if columns:
            params["columns"] = columns
        
        result = await client.get(endpoint, params=params)
        return result

    async def search_sites_by_name(
        self,
        name: str,
        columns: Optional[str] = None
    ) -> list[Dict[str, Any]]:
        """
        Search sites by name.
        
        Args:
            name: Site name to search
            columns: Optional columns to include
        
        Returns:
            List of matching sites
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Searching sites by name: {name}")

        endpoint = f"/v1.0/companies/{company_id}/sites/"
        
        params = {
            "search": name
        }
        
        if columns:
            params["columns"] = columns
        
        result = await client.get(endpoint, params=params)
        return result