"""
Quotes API wrapper for Simpro.

Handles all quote-related API calls.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.simpro.client import get_simpro_client
from src.utils import get_logger

logger = get_logger(__name__)


class QuotesAPI:
    """
    API wrapper for Simpro quotes endpoints.
    
    Provides methods for:
    - Searching quotes
    - Getting quote details
    - Getting quote sections
    - Getting quote cost centres
    """
    
    def __init__(self):
        """Initialize Quotes API"""
        logger.debug("Quotes API initialized")
    
    async def get_quotes(
        self,
        page: int = 1,
        page_size: int = 250,
        is_closed: bool = False,
        columns: Optional[str] = None,
        **filters
    ) -> list[Dict[str, Any]]:
        """
        Get list of quotes.

        Args:
            page: Page number (1-based)
            page_size: Number of quotes per page
            is_closed: Filter by closed status
            columns: Comma-separated columns to include
            **filters: Additional Simpro API filters (passed as query params)

        Returns:
            List of quotes
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching quotes (page={page}, page_size={page_size})")

        endpoint = f"/v1.0/companies/{company_id}/quotes/"

        params = {
            "page": page,
            "pageSize": page_size,
            "IsClosed": str(is_closed).lower(),
            **filters,
        }

        if columns:
            params["columns"] = columns

        result = await client.get(endpoint, params=params)
        return result

    async def get_quote_by_id(
        self,
        quote_id: int,
        columns: Optional[str] = None,
        display: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get a specific quote by ID.

        Args:
            quote_id: Quote ID
            columns: Optional columns to include
            display: Set to 'all' to include subresources (sections, cost centres, items) in one call

        Returns:
            Quote data
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching quote {quote_id} (display={display})")

        endpoint = f"/v1.0/companies/{company_id}/quotes/{quote_id}"

        params = {}
        if columns:
            params["columns"] = columns
        if display:
            params["display"] = display

        result = await client.get(endpoint, params=params)
        return result

    async def get_quote_sections(
        self,
        quote_id: int
    ) -> list[Dict[str, Any]]:
        """
        Get sections for a quote.
        
        Args:
            quote_id: Quote ID
        
        Returns:
            List of sections
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching sections for quote {quote_id}")

        endpoint = f"/v1.0/companies/{company_id}/quotes/{quote_id}/sections/"
        result = await client.get(endpoint)
        return result

    async def get_quote_section_by_id(
        self,
        quote_id: int,
        section_id: int
    ) -> Dict[str, Any]:
        """
        Get specific section of a quote.
        
        Args:
            quote_id: Quote ID
            section_id: Section ID
        
        Returns:
            Section data
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching section {section_id} for quote {quote_id}")

        endpoint = f"/v1.0/companies/{company_id}/quotes/{quote_id}/sections/{section_id}"
        result = await client.get(endpoint)
        return result

    async def get_quote_cost_centres(
        self,
        quote_id: int,
        section_id: int
    ) -> list[Dict[str, Any]]:
        """
        Get cost centres for a quote section.
        
        Args:
            quote_id: Quote ID
            section_id: Section ID
        
        Returns:
            List of cost centres
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching cost centres for quote {quote_id}, section {section_id}")

        endpoint = f"/v1.0/companies/{company_id}/quotes/{quote_id}/sections/{section_id}/costCentres/"
        result = await client.get(endpoint)
        return result

    async def get_quote_cost_centre_schedules(
        self,
        quote_id: int,
        section_id: int,
        cost_centre_id: int,
        page: int = 1,
        page_size: int = 250,
        columns: Optional[str] = None
    ) -> list[Dict[str, Any]]:
        """
        List all schedules for a specific quote cost centre.

        Args:
            quote_id: Quote ID
            section_id: Section ID
            cost_centre_id: Cost centre ID
            page: Page number (1-based)
            page_size: Number of schedules per page (max 250)
            columns: Comma-separated columns to include

        Returns:
            List of quote cost centre schedules
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(
            f"Fetching schedules for quote={quote_id}, section={section_id}, "
            f"costCentre={cost_centre_id} (page={page})"
        )

        endpoint = (
            f"/v1.0/companies/{company_id}/quotes/{quote_id}"
            f"/sections/{section_id}/costCenters/{cost_centre_id}/schedules/"
        )

        params = {
            "page": page,
            "pageSize": page_size
        }

        if columns:
            params["columns"] = columns

        result = await client.get(endpoint, params=params)
        return result

    async def create_quote_cost_centre_schedule(
        self,
        quote_id: int,
        section_id: int,
        cost_centre_id: int,
        staff_id: int,
        date: str,
        blocks: list[Dict[str, Any]],
        notes: Optional[str] = None,
        is_locked: Optional[bool] = None
    ) -> Dict[str, Any]:
        """
        Create a new schedule for a quote cost centre.

        Args:
            quote_id: Quote ID
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
            f"Creating schedule for quote={quote_id}, section={section_id}, "
            f"costCentre={cost_centre_id}, staff={staff_id}, date={date}"
        )

        endpoint = (
            f"/v1.0/companies/{company_id}/quotes/{quote_id}"
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

    async def get_quote_cost_centre_schedule_details(
        self,
        quote_id: int,
        section_id: int,
        cost_centre_id: int,
        schedule_id: int,
        columns: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get details for a specific quote cost centre schedule.

        Args:
            quote_id: Quote ID
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
            f"Fetching schedule {schedule_id} for quote={quote_id}, "
            f"section={section_id}, costCentre={cost_centre_id}"
        )

        endpoint = (
            f"/v1.0/companies/{company_id}/quotes/{quote_id}"
            f"/sections/{section_id}/costCenters/{cost_centre_id}"
            f"/schedules/{schedule_id}"
        )

        params = {}
        if columns:
            params["columns"] = columns

        result = await client.get(endpoint, params=params)
        return result

    async def update_quote_cost_centre_schedule(
        self,
        quote_id: int,
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
        Update an existing quote cost centre schedule (PATCH).

        Args:
            quote_id: Quote ID
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
            f"Updating schedule {schedule_id} for quote={quote_id}, "
            f"section={section_id}, costCentre={cost_centre_id}"
        )

        endpoint = (
            f"/v1.0/companies/{company_id}/quotes/{quote_id}"
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

    async def delete_quote_cost_centre_schedule(
        self,
        quote_id: int,
        section_id: int,
        cost_centre_id: int,
        schedule_id: int
    ) -> None:
        """
        Delete a quote cost centre schedule.

        Args:
            quote_id: Quote ID
            section_id: Section ID
            cost_centre_id: Cost centre ID
            schedule_id: Schedule ID to delete

        Returns:
            None (204 No Content on success)
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(
            f"Deleting schedule {schedule_id} for quote={quote_id}, "
            f"section={section_id}, costCentre={cost_centre_id}"
        )

        endpoint = (
            f"/v1.0/companies/{company_id}/quotes/{quote_id}"
            f"/sections/{section_id}/costCenters/{cost_centre_id}"
            f"/schedules/{schedule_id}"
        )

        result = await client.delete(endpoint)
        return result