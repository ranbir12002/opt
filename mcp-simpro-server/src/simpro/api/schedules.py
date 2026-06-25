"""
Schedules API wrapper for Simpro.

Handles all schedule-related API calls.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.simpro.client import get_simpro_client
from src.utils import get_logger

logger = get_logger(__name__)


class SchedulesAPI:
    """
    API wrapper for Simpro schedules endpoints.
    
    Provides methods for:
    - Getting schedules
    - Filtering schedules by date
    """
    
    def __init__(self):
        """Initialize Schedules API"""
        logger.debug("Schedules API initialized")
    
    async def get_schedules(
        self,
        page: int = 1,
        page_size: int = 250,
        date: Optional[str] = None,
        columns: Optional[str] = None,
        schedule_type: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> list[Dict[str, Any]]:
        """
        Get list of schedules.

        Args:
            page: Page number (1-based)
            page_size: Number of schedules per page
            date: Filter schedules for this exact date (YYYY-MM-DD)
                  or a Simpro operator like between(date1,date2)
            columns: Comma-separated columns to include
            schedule_type: Filter by type — 'job', 'activity', 'quote', or 'lead'
            filters: Arbitrary response-field filters (e.g. {"Staff.Type": "contractor"})

        Returns:
            List of schedules
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching schedules (page={page}, page_size={page_size}, date={date}, type={schedule_type}, filters={filters})")

        endpoint = f"/v1.0/companies/{company_id}/schedules/"

        params = {
            "page": page,
            "pageSize": page_size
        }

        if date:
            params["Date"] = date

        if schedule_type:
            params["Type"] = schedule_type

        if columns:
            params["columns"] = columns

        # Spread arbitrary filters as URL query params
        if filters:
            params.update(filters)

        result = await client.get(endpoint, params=params)
        return result

    async def get_schedule_by_id(
        self,
        schedule_id: int,
        columns: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get a specific schedule by ID.
        
        Args:
            schedule_id: Schedule ID
            columns: Optional columns to include
        
        Returns:
            Schedule data
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching schedule {schedule_id}")

        endpoint = f"/v1.0/companies/{company_id}/schedules/{schedule_id}"

        params = {}
        if columns:
            params["columns"] = columns

        result = await client.get(endpoint, params=params)
        return result

    async def get_job_cost_centre_schedules(
        self,
        job_id: int,
        section_id: int,
        cost_centre_id: int,
        page: int = 1,
        page_size: int = 250,
        columns: Optional[str] = None,
        date: Optional[str] = None,
    ) -> list[Dict[str, Any]]:
        """
        List all schedules for a specific job cost centre.

        Args:
            job_id: Job ID
            section_id: Section ID
            cost_centre_id: Cost centre ID
            page: Page number (1-based)
            page_size: Number of schedules per page (max 250)
            columns: Comma-separated columns to include
            date: Filter by date (YYYY-MM-DD) or Simpro operator like between(date1,date2)

        Returns:
            List of job cost centre schedules
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(
            f"Fetching schedules for job={job_id}, section={section_id}, "
            f"costCentre={cost_centre_id} (page={page}, date={date})"
        )

        endpoint = (
            f"/v1.0/companies/{company_id}/jobs/{job_id}"
            f"/sections/{section_id}/costCenters/{cost_centre_id}/schedules/"
        )

        params = {
            "page": page,
            "pageSize": page_size
        }

        if date:
            params["Date"] = date

        if columns:
            params["columns"] = columns

        result = await client.get(endpoint, params=params)
        return result

    async def get_job_cost_centre_schedule_details(
        self,
        job_id: int,
        section_id: int,
        cost_centre_id: int,
        schedule_id: int,
        columns: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get details for a specific job cost centre schedule.

        Args:
            job_id: Job ID
            section_id: Section ID
            cost_centre_id: Cost centre ID
            schedule_id: Schedule ID
            columns: Comma-separated columns to include

        Returns:
            Schedule details including staff, hours, notes, and time blocks
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(
            f"Fetching schedule {schedule_id} for job={job_id}, "
            f"section={section_id}, costCentre={cost_centre_id}"
        )

        endpoint = (
            f"/v1.0/companies/{company_id}/jobs/{job_id}"
            f"/sections/{section_id}/costCenters/{cost_centre_id}"
            f"/schedules/{schedule_id}"
        )

        params = {}
        if columns:
            params["columns"] = columns

        result = await client.get(endpoint, params=params)
        return result

    async def create_job_cost_centre_schedule(
        self,
        job_id: int,
        section_id: int,
        cost_centre_id: int,
        staff_id: int,
        date: str,
        blocks: list[Dict[str, Any]],
        notes: Optional[str] = None,
        is_locked: Optional[bool] = None
    ) -> Dict[str, Any]:
        """
        Create a new schedule for a job cost centre.

        Args:
            job_id: Job ID
            section_id: Section ID
            cost_centre_id: Cost centre ID
            staff_id: Staff member ID to assign
            date: Schedule date (YYYY-MM-DD)
            blocks: Time blocks, each with StartTime, EndTime, and optional ScheduleRate
            notes: Optional notes (supports HTML)
            is_locked: Whether the schedule is locked

        Returns:
            Created schedule details
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(
            f"Creating schedule for job={job_id}, section={section_id}, "
            f"costCentre={cost_centre_id}, staff={staff_id}, date={date}"
        )

        endpoint = (
            f"/v1.0/companies/{company_id}/jobs/{job_id}"
            f"/sections/{section_id}/costCenters/{cost_centre_id}/schedules/"
        )

        body: Dict[str, Any] = {
            "Staff": staff_id,
            "Date": date,
            "Blocks": blocks,
        }

        if notes is not None:
            body["Notes"] = notes
        if is_locked is not None:
            body["IsLocked"] = is_locked

        result = await client.post(endpoint, json=body)
        return result

    async def update_job_cost_centre_schedule(
        self,
        job_id: int,
        section_id: int,
        cost_centre_id: int,
        schedule_id: int,
        staff_id: Optional[int] = None,
        date: Optional[str] = None,
        blocks: Optional[list[Dict[str, Any]]] = None,
        notes: Optional[str] = None,
        is_locked: Optional[bool] = None
    ) -> None:
        """
        Update an existing job cost centre schedule (PATCH).

        Args:
            job_id: Job ID
            section_id: Section ID
            cost_centre_id: Cost centre ID
            schedule_id: Schedule ID to update
            staff_id: New staff member ID (optional)
            date: New date YYYY-MM-DD (optional)
            blocks: New time blocks (optional)
            notes: New notes (optional)
            is_locked: New lock status (optional)

        Returns:
            None (204 No Content on success)
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(
            f"Updating schedule {schedule_id} for job={job_id}, "
            f"section={section_id}, costCentre={cost_centre_id}"
        )

        endpoint = (
            f"/v1.0/companies/{company_id}/jobs/{job_id}"
            f"/sections/{section_id}/costCenters/{cost_centre_id}"
            f"/schedules/{schedule_id}"
        )

        body: Dict[str, Any] = {}
        if staff_id is not None:
            body["Staff"] = staff_id
        if date is not None:
            body["Date"] = date
        if blocks is not None:
            body["Blocks"] = blocks
        if notes is not None:
            body["Notes"] = notes
        if is_locked is not None:
            body["IsLocked"] = is_locked

        result = await client.patch(endpoint, json=body)
        return result

    async def delete_job_cost_centre_schedule(
        self,
        job_id: int,
        section_id: int,
        cost_centre_id: int,
        schedule_id: int
    ) -> None:
        """
        Delete a job cost centre schedule.

        Args:
            job_id: Job ID
            section_id: Section ID
            cost_centre_id: Cost centre ID
            schedule_id: Schedule ID to delete

        Returns:
            None (204 No Content on success)
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(
            f"Deleting schedule {schedule_id} for job={job_id}, "
            f"section={section_id}, costCentre={cost_centre_id}"
        )

        endpoint = (
            f"/v1.0/companies/{company_id}/jobs/{job_id}"
            f"/sections/{section_id}/costCenters/{cost_centre_id}"
            f"/schedules/{schedule_id}"
        )

        result = await client.delete(endpoint)
        return result