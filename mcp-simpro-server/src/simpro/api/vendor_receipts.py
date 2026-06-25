"""
Vendor Receipts API wrapper for Simpro.

Handles all vendor receipt-related API calls.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.simpro.client import get_simpro_client
from src.utils import get_logger

logger = get_logger(__name__)


class VendorReceiptsAPI:
    """
    API wrapper for Simpro vendor receipts endpoints.
    
    Provides methods for:
    - Getting vendor receipts
    - Getting vendor receipt line items
    """
    
    def __init__(self):
        """Initialize Vendor Receipts API"""
        logger.debug("Vendor Receipts API initialized")
    
    async def get_vendor_receipts(
        self,
        page: int = 1,
        page_size: int = 250,
        display: Optional[str] = None,
        columns: Optional[str] = None,
        **filters
    ) -> list[Dict[str, Any]]:
        """
        Get list of vendor receipts.

        Args:
            page: Page number (1-based)
            page_size: Number of vendor receipts per page
            display: Display filter (e.g., 'all')
            columns: Comma-separated columns to include
            **filters: Additional Simpro API filters (passed as query params)

        Returns:
            List of vendor receipts
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching vendor receipts (page={page}, page_size={page_size})")

        endpoint = f"/v1.0/companies/{company_id}/vendorReceipts/"

        params = {
            "page": page,
            "pageSize": page_size,
            **filters,
        }

        if display:
            params["display"] = display

        if columns:
            params["columns"] = columns

        result = await client.get(endpoint, params=params)
        return result

    async def get_vendor_receipt_by_id(
        self,
        vendor_receipt_id: int,
        columns: Optional[str] = None,
        display: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get a specific vendor receipt by ID.

        Args:
            vendor_receipt_id: Vendor receipt ID
            columns: Optional columns to include
            display: Set to 'all' to include subresources (line items) in one call

        Returns:
            Vendor receipt data
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching vendor receipt {vendor_receipt_id} (display={display})")

        endpoint = f"/v1.0/companies/{company_id}/vendorReceipts/{vendor_receipt_id}"

        params = {}
        if columns:
            params["columns"] = columns
        if display:
            params["display"] = display

        result = await client.get(endpoint, params=params)
        return result

    async def get_vendor_receipt_line_items(
        self,
        vendor_receipt_id: int,
        columns: Optional[str] = None
    ) -> list[Dict[str, Any]]:
        """
        Get line items for a vendor receipt.
        
        Args:
            vendor_receipt_id: Vendor receipt ID
            columns: Optional columns to include
        
        Returns:
            List of line items
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching line items for vendor receipt {vendor_receipt_id}")

        endpoint = f"/v1.0/companies/{company_id}/vendorReceipts/{vendor_receipt_id}/lineItems/"
        
        params = {}
        if columns:
            params["columns"] = columns
        
        result = await client.get(endpoint, params=params)
        return result