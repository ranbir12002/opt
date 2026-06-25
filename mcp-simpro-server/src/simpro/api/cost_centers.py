"""
Cost Centres API wrapper for Simpro.

Handles all cost centre-related API calls.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.simpro.client import get_simpro_client
from src.utils import get_logger

logger = get_logger(__name__)


class CostCentresAPI:
    """
    API wrapper for Simpro cost centres endpoints.
    
    Provides methods for:
    - Getting all cost centre types (via /jobCostCenters/)
    - Getting job section cost centres (instances)
    - Getting specific cost centre details
    """
    
    def __init__(self):
        """Initialize Cost Centres API"""
        logger.debug("Cost Centres API initialized")
    
    async def get_cost_centre_types(
        self,
        page: int = 1,
        page_size: int = 250,
        columns: Optional[str] = None,
    ) -> list[Dict[str, Any]]:
        """
        Get list of all cost centre types.

        Uses the correct endpoint: /jobCostCenters/

        Args:
            page: Page number (1-based)
            page_size: Number of cost centre types per page
            columns: Optional comma-separated columns (e.g. "ID,Name,IncomeAccountNo")

        Returns:
            List of cost centre types
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching cost centre types (page={page}, page_size={page_size})")

        # CORRECT ENDPOINT: /jobCostCenters/ (not /setup/costCentres/)
        endpoint = f"/v1.0/companies/{company_id}/jobCostCenters/"

        params = {
            "page": page,
            "pageSize": page_size
        }

        if columns:
            params["columns"] = columns

        result = await client.get(endpoint, params=params)
        return result

    async def get_job_section_cost_centres(
        self,
        job_id: int,
        section_id: int
    ) -> list[Dict[str, Any]]:
        """
        Get all cost centres for a specific job section.
        
        This returns the actual cost centre instances with their data.
        
        Uses endpoint: /jobs/{jobID}/sections/{sectionID}/costCenters/
        
        Args:
            job_id: Job ID
            section_id: Section ID
        
        Returns:
            List of cost centres with IDs like 116713, 116714, etc.
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching cost centres for job {job_id}, section {section_id}")

        # CRITICAL: Use 'costCenters' (American spelling)
        endpoint = f"/v1.0/companies/{company_id}/jobs/{job_id}/sections/{section_id}/costCenters/"

        result = await client.get(endpoint)
        return result
    
    async def get_job_cost_centre_details(
        self,
        job_id: int,
        section_id: int,
        cost_centre_id: int,
        display: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get details for a specific job cost centre by its instance ID.

        Calls the individual cost centre endpoint for detailed data.
        Use display='all' to include full financial breakdown
        (materials, resources, labor, margins, profitability).

        Args:
            job_id: Job ID
            section_id: Section ID
            cost_centre_id: Cost centre instance ID (e.g., 116713)
            display: Set to 'all' for full financial/profitability breakdown

        Returns:
            Cost centre data with detailed financials when display='all'
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching cost centre {cost_centre_id} for job {job_id}, section {section_id} (display={display})")

        endpoint = (
            f"/v1.0/companies/{company_id}/jobs/{job_id}/"
            f"sections/{section_id}/costCenters/{cost_centre_id}"
        )

        params = {}
        if display:
            params["display"] = display

        result = await client.get(endpoint, params=params)
        return result

    async def get_cost_centre_catalog_items(
        self,
        job_id: int,
        section_id: int,
        cost_centre_id: int,
        page: int = 1,
        page_size: int = 250,
    ) -> list[Dict[str, Any]]:
        """
        Get catalog items (parts/materials) for a cost centre.

        Endpoint: GET /jobs/{jobID}/sections/{sectionID}/costCenters/{ccID}/catalogs/

        Args:
            job_id: Job ID
            section_id: Section ID
            cost_centre_id: Cost centre instance ID
            page: Page number (1-based)
            page_size: Items per page (max 250)

        Returns:
            List of catalog items with part names, quantities, costs
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(
            f"Fetching catalog items for job {job_id}, section {section_id}, "
            f"cost centre {cost_centre_id}"
        )

        endpoint = (
            f"/v1.0/companies/{company_id}/jobs/{job_id}/"
            f"sections/{section_id}/costCenters/{cost_centre_id}/catalogs/"
        )

        params = {"page": page, "pageSize": page_size}
        result = await client.get(endpoint, params=params)
        return result

    async def get_cost_centre_labour_items(
        self,
        job_id: int,
        section_id: int,
        cost_centre_id: int,
        page: int = 1,
        page_size: int = 250,
    ) -> list[Dict[str, Any]]:
        """
        Get labour items for a cost centre.

        Endpoint: GET /jobs/{jobID}/sections/{sectionID}/costCenters/{ccID}/labor/

        Args:
            job_id: Job ID
            section_id: Section ID
            cost_centre_id: Cost centre instance ID
            page: Page number (1-based)
            page_size: Items per page (max 250)

        Returns:
            List of labour items with descriptions, hours, rates
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(
            f"Fetching labour items for job {job_id}, section {section_id}, "
            f"cost centre {cost_centre_id}"
        )

        endpoint = (
            f"/v1.0/companies/{company_id}/jobs/{job_id}/"
            f"sections/{section_id}/costCenters/{cost_centre_id}/labor/"
        )

        params = {"page": page, "pageSize": page_size}
        result = await client.get(endpoint, params=params)
        return result

    async def get_cost_centre_one_off_items(
        self,
        job_id: int,
        section_id: int,
        cost_centre_id: int,
        page: int = 1,
        page_size: int = 250,
    ) -> list[Dict[str, Any]]:
        """
        Get one-off (custom) items for a cost centre.

        Endpoint: GET /jobs/{jobID}/sections/{sectionID}/costCenters/{ccID}/oneOffs/

        Args:
            job_id: Job ID
            section_id: Section ID
            cost_centre_id: Cost centre instance ID
            page: Page number (1-based)
            page_size: Items per page (max 250)

        Returns:
            List of one-off items
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(
            f"Fetching one-off items for job {job_id}, section {section_id}, "
            f"cost centre {cost_centre_id}"
        )

        endpoint = (
            f"/v1.0/companies/{company_id}/jobs/{job_id}/"
            f"sections/{section_id}/costCenters/{cost_centre_id}/oneOffs/"
        )

        params = {"page": page, "pageSize": page_size}
        result = await client.get(endpoint, params=params)
        return result

    async def get_cost_centre_prebuild_items(
        self,
        job_id: int,
        section_id: int,
        cost_centre_id: int,
        page: int = 1,
        page_size: int = 250,
    ) -> list[Dict[str, Any]]:
        """
        Get prebuild items for a cost centre.

        Endpoint: GET /jobs/{jobID}/sections/{sectionID}/costCenters/{ccID}/prebuilds/

        Args:
            job_id: Job ID
            section_id: Section ID
            cost_centre_id: Cost centre instance ID
            page: Page number (1-based)
            page_size: Items per page (max 250)

        Returns:
            List of prebuild items with Prebuild info, quantities, sell prices
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(
            f"Fetching prebuild items for job {job_id}, section {section_id}, "
            f"cost centre {cost_centre_id}"
        )

        endpoint = (
            f"/v1.0/companies/{company_id}/jobs/{job_id}/"
            f"sections/{section_id}/costCenters/{cost_centre_id}/prebuilds/"
        )

        params = {"page": page, "pageSize": page_size}
        result = await client.get(endpoint, params=params)
        return result