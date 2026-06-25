"""
Work Orders API wrapper for Simpro.

Handles all work order-related API calls.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.simpro.client import get_simpro_client
from src.utils import get_logger

logger = get_logger(__name__)


class WorkOrdersAPI:
    """
    API wrapper for Simpro work orders endpoints.
    
    Provides methods for:
    - Getting work orders for cost centres
    - Getting all job work orders
    - Getting work order details
    """
    
    def __init__(self):
        """Initialize Work Orders API"""
        logger.debug("Work Orders API initialized")
    
    async def get_work_orders_by_cost_centre(
        self,
        job_id: int,
        section_id: int,
        cost_centre_id: int,
        columns: Optional[str] = None
    ) -> list[Dict[str, Any]]:
        """
        Get work orders for a specific cost centre.
        
        Args:
            job_id: Job ID
            section_id: Section ID
            cost_centre_id: Cost centre ID
            columns: Optional columns to include
        
        Returns:
            List of work orders
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching work orders for job {job_id}, section {section_id}, cost centre {cost_centre_id}")

        endpoint = f"/v1.0/companies/{company_id}/jobs/{job_id}/sections/{section_id}/costCenters/{cost_centre_id}/workOrders/"
        
        params = {}
        if columns:
            params["columns"] = columns
        
        result = await client.get(endpoint, params=params)
        return result

    async def get_all_job_work_orders(
        self,
        page: int = 1,
        page_size: int = 250,
        columns: Optional[str] = None,
        **filters
    ) -> list[Dict[str, Any]]:
        """
        Get all job work orders.

        Args:
            page: Page number (1-based)
            page_size: Number of work orders per page
            columns: Comma-separated columns to include
            **filters: Additional Simpro API filters (passed as query params)

        Returns:
            List of work orders
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching all job work orders (page={page}, page_size={page_size})")

        endpoint = f"/v1.0/companies/{company_id}/jobWorkOrders/"

        params = {
            "page": page,
            "pageSize": page_size,
            **filters,
        }

        if columns:
            params["columns"] = columns

        result = await client.get(endpoint, params=params)
        return result

    async def get_work_order_by_id(
        self,
        work_order_id: int,
        columns: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get a specific work order by ID.
        
        Args:
            work_order_id: Work order ID
            columns: Optional columns to include
        
        Returns:
            Work order data
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching work order {work_order_id}")

        endpoint = f"/v1.0/companies/{company_id}/jobWorkOrders/{work_order_id}"
        
        params = {}
        if columns:
            params["columns"] = columns
        
        result = await client.get(endpoint, params=params)
        return result