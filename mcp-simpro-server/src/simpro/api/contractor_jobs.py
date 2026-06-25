"""
Contractor Jobs API wrapper for Simpro.

Handles all contractor job-related API calls.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.simpro.client import get_simpro_client
from src.utils import get_logger

logger = get_logger(__name__)


class ContractorJobsAPI:
    """
    API wrapper for Simpro contractor jobs endpoints.

    Provides methods for:
    - Getting contractor jobs
    - Getting contractor jobs by cost centre
    - Creating contractor jobs
    - Updating contractor jobs (PATCH)
    - Deleting contractor jobs (DELETE)
    """
    
    def __init__(self):
        """Initialize Contractor Jobs API"""
        logger.debug("Contractor Jobs API initialized")
    
    async def get_contractor_job_by_id(
        self,
        contractor_job_id: int,
        columns: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get a specific contractor job by ID.
        
        Args:
            contractor_job_id: Contractor job ID
            columns: Optional columns to include
        
        Returns:
            Contractor job data
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching contractor job {contractor_job_id}")

        endpoint = f"/v1.0/companies/{company_id}/contractorJobs/{contractor_job_id}"
        
        params = {}
        if columns:
            params["columns"] = columns
        
        result = await client.get(endpoint, params=params)
        return result

    async def get_contractor_jobs_by_cost_centre(
        self,
        job_id: int,
        section_id: int,
        cost_centre_id: int,
        columns: Optional[str] = None
    ) -> list[Dict[str, Any]]:
        """
        Get contractor jobs for a specific cost centre.
        
        Args:
            job_id: Job ID
            section_id: Section ID
            cost_centre_id: Cost centre ID
            columns: Optional columns to include
        
        Returns:
            List of contractor jobs
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching contractor jobs for job {job_id}, section {section_id}, cost centre {cost_centre_id}")

        endpoint = f"/v1.0/companies/{company_id}/jobs/{job_id}/sections/{section_id}/costCenters/{cost_centre_id}/contractorJobs/"

        params = {}
        if columns:
            params["columns"] = columns

        result = await client.get(endpoint, params=params)
        return result

    async def get_all_contractor_jobs(
        self,
        page: int = 1,
        page_size: int = 250,
        columns: Optional[str] = None
    ) -> list[Dict[str, Any]]:
        """
        Get all contractor jobs.
        
        Args:
            page: Page number (1-based)
            page_size: Number of contractor jobs per page
            columns: Optional columns to include
        
        Returns:
            List of contractor jobs
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching all contractor jobs (page={page}, page_size={page_size})")

        endpoint = f"/v1.0/companies/{company_id}/contractorJobs/"

        params = {
            "page": page,
            "pageSize": page_size
        }

        if columns:
            params["columns"] = columns

        result = await client.get(endpoint, params=params)
        return result

    async def create_contractor_job(
        self,
        job_id: int,
        section_id: int,
        cost_centre_id: int,
        contractor_job_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Create a contractor job for a specific cost centre.

        Endpoint: POST /jobs/{jobID}/sections/{sectionID}/costCenters/{ccID}/contractorJobs/

        Args:
            job_id: Job ID
            section_id: Section ID
            cost_centre_id: Cost centre ID
            contractor_job_data: Contractor job body dict, e.g.:
                {
                    "Contractor": 123,
                    "Description": "...",
                    "Materials": 5000.00,
                    "Labor": 3000.00,
                    "TaxCode": 1,
                    "DateIssued": "2026-02-16",
                    "ContractorSupplyMaterials": false,
                }

        Returns:
            Created contractor job data
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(
            f"Creating contractor job for job {job_id}, section {section_id}, "
            f"cost centre {cost_centre_id}"
        )

        endpoint = (
            f"/v1.0/companies/{company_id}/jobs/{job_id}/"
            f"sections/{section_id}/costCenters/{cost_centre_id}/contractorJobs/"
        )

        result = await client.post(endpoint, json=contractor_job_data)
        return result

    async def update_contractor_job(
        self,
        job_id: int,
        section_id: int,
        cost_centre_id: int,
        contractor_job_id: int,
        contractor_job_data: Dict[str, Any],
    ) -> None:
        """
        Update (PATCH) an existing contractor job.

        Endpoint: PATCH /jobs/{jobID}/sections/{sectionID}/costCenters/{ccID}/contractorJobs/{cjID}

        Note: Cannot change the Contractor field via PATCH — only Description,
        Materials, Labor, TaxCode, Items, dates, etc. are patchable.
        Returns 204 No Content on success.

        Args:
            job_id: Job ID
            section_id: Section ID
            cost_centre_id: Cost centre ID
            contractor_job_id: Existing contractor job ID to update
            contractor_job_data: Fields to update, e.g.:
                {
                    "Description": "Updated description",
                    "Materials": 6000.00,
                    "Labor": 4000.00,
                    "TaxCode": 1,
                    "Items": {
                        "Catalogs": [{"ID": 10, "Qty": 5.0}],
                        "Prebuilds": [{"ID": 20, "Qty": 2.0}]
                    }
                }
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(
            f"Updating contractor job {contractor_job_id} for job {job_id}, "
            f"section {section_id}, cost centre {cost_centre_id}"
        )

        endpoint = (
            f"/v1.0/companies/{company_id}/jobs/{job_id}"
            f"/sections/{section_id}/costCenters/{cost_centre_id}"
            f"/contractorJobs/{contractor_job_id}"
        )

        await client.patch(endpoint, json=contractor_job_data)

    async def delete_contractor_job(
        self,
        job_id: int,
        section_id: int,
        cost_centre_id: int,
        contractor_job_id: int,
    ) -> None:
        """
        Delete an existing contractor job.

        Endpoint: DELETE /jobs/{jobID}/sections/{sectionID}/costCenters/{ccID}/contractorJobs/{cjID}

        Returns 204 No Content on success, 404 if not found.

        Args:
            job_id: Job ID
            section_id: Section ID
            cost_centre_id: Cost centre ID
            contractor_job_id: Contractor job ID to delete
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(
            f"Deleting contractor job {contractor_job_id} for job {job_id}, "
            f"section {section_id}, cost centre {cost_centre_id}"
        )

        endpoint = (
            f"/v1.0/companies/{company_id}/jobs/{job_id}"
            f"/sections/{section_id}/costCenters/{cost_centre_id}"
            f"/contractorJobs/{contractor_job_id}"
        )

        await client.delete(endpoint)