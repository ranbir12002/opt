"""
Jobs API wrapper for Simpro.

Provides high-level functions for working with jobs.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.simpro.client import get_simpro_client
from src.utils import get_logger

logger = get_logger(__name__)


class JobsAPI:
    """
    Jobs API wrapper.
    
    Provides methods for:
    - Listing jobs with pagination and filters
    - Getting job details
    - Creating/updating jobs
    - Getting job sections and cost centers
    """
    
    def __init__(self):
        """Initialize Jobs API."""
        pass
    
    async def get_jobs(
        self,
        page: int = 1,
        page_size: int = 250,
        **filters
    ) -> Dict[str, Any]:
        """
        Get list of jobs with pagination.
        
        Args:
            page: Page number (1-based)
            page_size: Items per page
            **filters: Additional filters (status, stage, etc.)
        
        Returns:
            List of jobs
        
        Example:
            >>> api = JobsAPI()
            >>> jobs = await api.get_jobs(page=1, page_size=10, status="Active")
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        endpoint = f"/v1.0/companies/{company_id}/jobs/"

        params = {
            "page": page,
            "pageSize": page_size,
            **filters
        }

        logger.info(f"Fetching jobs (page={page}, page_size={page_size})")
        return await client.get(endpoint, params=params)
    
    async def get_job_by_id(
        self,
        job_id: int,
        columns: Optional[str] = None,
        display: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get specific job by ID.

        Args:
            job_id: Job ID
            columns: Comma-separated columns to include
            display: Set to 'all' to include subresources (sections, cost centres, items) in one call

        Returns:
            Job details

        Example:
            >>> api = JobsAPI()
            >>> job = await api.get_job_by_id(12345)
            >>> job_full = await api.get_job_by_id(12345, display="all")
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        endpoint = f"/v1.0/companies/{company_id}/jobs/{job_id}"

        params = {}
        if columns:
            params["columns"] = columns
        if display:
            params["display"] = display

        logger.info(f"Fetching job {job_id} (display={display})")
        return await client.get(endpoint, params=params)
    
    async def get_jobs_with_site_suburb(
        self,
        page: int = 1,
        page_size: int = 10
    ) -> Dict[str, Any]:
        """
        Get jobs with site suburb information.
        
        Args:
            page: Page number
            page_size: Items per page
        
        Returns:
            Jobs with site details
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        # Include Site column to get suburb info
        endpoint = f"/v1.0/companies/{company_id}/jobs/"

        params = {
            "page": page,
            "pageSize": page_size,
            "columns": "ID,Name,Status,Stage,Site"
        }

        logger.info(f"Fetching jobs with site suburb (page={page})")
        return await client.get(endpoint, params=params)
    
    async def get_job_sections(
        self,
        job_id: int
    ) -> Dict[str, Any]:
        """
        Get sections for a job.
        
        Args:
            job_id: Job ID
        
        Returns:
            List of job sections
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        endpoint = f"/v1.0/companies/{company_id}/jobs/{job_id}/sections/"

        logger.info(f"Fetching sections for job {job_id}")
        return await client.get(endpoint)
    
    async def get_job_section_by_id(
        self,
        job_id: int,
        section_id: int
    ) -> Dict[str, Any]:
        """
        Get specific job section.
        
        Args:
            job_id: Job ID
            section_id: Section ID
        
        Returns:
            Section details
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        endpoint = f"/v1.0/companies/{company_id}/jobs/{job_id}/sections/{section_id}"

        logger.info(f"Fetching section {section_id} for job {job_id}")
        return await client.get(endpoint)
    
    async def get_job_cost_centers(
        self,
        job_id: int,
        section_id: int
    ) -> Dict[str, Any]:
        """
        Get cost centers for a job section.
        
        Args:
            job_id: Job ID
            section_id: Section ID
        
        Returns:
            List of cost centers
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        endpoint = f"/v1.0/companies/{company_id}/jobs/{job_id}/sections/{section_id}/costCenters/"

        logger.info(f"Fetching cost centers for job {job_id} section {section_id}")
        return await client.get(endpoint)
    
    async def get_job_cost_center_detail(
        self,
        job_id: int,
        section_id: int,
        cost_center_id: int,
        columns: str = "Name,ID,Claimed"
    ) -> Dict[str, Any]:
        """
        Get specific cost center details.
        
        Args:
            job_id: Job ID
            section_id: Section ID
            cost_center_id: Cost center ID
            columns: Columns to return
        
        Returns:
            Cost center details
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        endpoint = (
            f"/v1.0/companies/{company_id}/jobs/{job_id}/"
            f"sections/{section_id}/costCenters/{cost_center_id}"
        )

        params = {"columns": columns}

        logger.info(f"Fetching cost center {cost_center_id} details")
        return await client.get(endpoint, params=params)