"""
Leads API wrapper for Simpro.

Handles all lead-related API calls.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.simpro.client import get_simpro_client
from src.utils import get_logger

logger = get_logger(__name__)


class LeadsAPI:
    """
    API wrapper for Simpro leads endpoints.
    
    Provides methods for:
    - Getting leads
    - Filtering leads by status
    """
    
    def __init__(self):
        """Initialize Leads API"""
        logger.debug("Leads API initialized")
    
    async def get_leads(
        self,
        page: int = 1,
        page_size: int = 250,
        is_open: Optional[bool] = None,
        columns: Optional[str] = None,
        **filters
    ) -> list[Dict[str, Any]]:
        """
        Get list of leads.

        Args:
            page: Page number (1-based)
            page_size: Number of leads per page
            is_open: Filter by open status (True for open, False for closed)
            columns: Comma-separated columns to include
            **filters: Additional Simpro API filters (passed as query params)

        Returns:
            List of leads
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching leads (page={page}, page_size={page_size}, is_open={is_open})")

        endpoint = f"/v1.0/companies/{company_id}/leads/"

        params = {
            "page": page,
            "pageSize": page_size,
            **filters,
        }

        if is_open is not None:
            params["IsOpen"] = str(is_open).lower()

        if columns:
            params["columns"] = columns

        result = await client.get(endpoint, params=params)
        return result

    async def get_lead_by_id(
        self,
        lead_id: int,
        columns: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get a specific lead by ID.
        
        Args:
            lead_id: Lead ID
            columns: Optional columns to include
        
        Returns:
            Lead data
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching lead {lead_id}")

        endpoint = f"/v1.0/companies/{company_id}/leads/{lead_id}"
        
        params = {}
        if columns:
            params["columns"] = columns
        
        result = await client.get(endpoint, params=params)
        return result