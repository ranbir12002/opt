"""
Vendor Orders API wrapper for Simpro.

Handles all vendor order-related API calls.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.simpro.client import get_simpro_client
from src.utils import get_logger

logger = get_logger(__name__)


class VendorOrdersAPI:
    """
    API wrapper for Simpro vendor orders endpoints.

    Provides methods for:
    - Getting vendor orders
    - Getting vendor order receipts
    - Creating vendor orders (POST)
    - Updating vendor orders (PATCH)
    - Deleting vendor orders (DELETE)
    """
    
    def __init__(self):
        """Initialize Vendor Orders API"""
        logger.debug("Vendor Orders API initialized")
    
    async def get_vendor_orders(
        self,
        page: int = 1,
        page_size: int = 250,
        columns: Optional[str] = None,
        **filters
    ) -> list[Dict[str, Any]]:
        """
        Get list of vendor orders.

        Args:
            page: Page number (1-based)
            page_size: Number of vendor orders per page
            columns: Optional columns to include
            **filters: Additional Simpro API filters (passed as query params)

        Returns:
            List of vendor orders
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching vendor orders (page={page}, page_size={page_size})")

        endpoint = f"/v1.0/companies/{company_id}/vendorOrders/"

        params = {
            "page": page,
            "pageSize": page_size,
            **filters,
        }

        if columns:
            params["columns"] = columns

        result = await client.get(endpoint, params=params)
        return result

    async def get_vendor_order_by_id(
        self,
        vendor_order_id: int,
        columns: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get a specific vendor order by ID.
        
        Args:
            vendor_order_id: Vendor order ID
            columns: Optional columns to include
        
        Returns:
            Vendor order data
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching vendor order {vendor_order_id}")

        endpoint = f"/v1.0/companies/{company_id}/vendorOrders/{vendor_order_id}"
        
        params = {}
        if columns:
            params["columns"] = columns
        
        result = await client.get(endpoint, params=params)
        return result

    async def get_vendor_order_receipt(
        self,
        vendor_order_id: int,
        receipt_id: int,
        columns: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get a specific receipt for a vendor order.
        
        Args:
            vendor_order_id: Vendor order ID
            receipt_id: Receipt ID
            columns: Optional columns to include
        
        Returns:
            Receipt data
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching receipt {receipt_id} for vendor order {vendor_order_id}")

        endpoint = f"/v1.0/companies/{company_id}/vendorOrders/{vendor_order_id}/receipts/{receipt_id}"
        
        params = {}
        if columns:
            params["columns"] = columns
        
        result = await client.get(endpoint, params=params)
        return result

    async def create_vendor_order(
        self,
        vendor_order_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Create a new vendor order.

        Endpoint: POST /v1.0/companies/{companyID}/vendorOrders/

        Args:
            vendor_order_data: Vendor order body dict. Required field: Vendor (int ID).
                Optional fields: Type ("Catalogue"|"Description"), Description,
                IsInventoryItem, Amount, StorageDevice, AssignedTo, Stage,
                Status, StatusAutoAdjust, DateIssued, QuoteNo, Reference,
                DueDate, ShowItemDueDate, VendorNotes, PrivateNotes,
                Archived, ExchangeRate, Freight.

        Returns:
            Created vendor order data (201 response body).
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info("Creating vendor order")
        endpoint = f"/v1.0/companies/{company_id}/vendorOrders/"
        result = await client.post(endpoint, json=vendor_order_data)
        return result

    async def update_vendor_order(
        self,
        vendor_order_id: int,
        vendor_order_data: Dict[str, Any],
    ) -> None:
        """
        Update (PATCH) an existing vendor order.

        Endpoint: PATCH /v1.0/companies/{companyID}/vendorOrders/{vendorOrderID}

        Only send fields you want to change. Returns 204 No Content on success.

        Args:
            vendor_order_id: Existing vendor order ID to update.
            vendor_order_data: Partial fields to update, e.g.:
                {
                    "Stage": "Approved",
                    "Reference": "PO-2026-001",
                    "DueDate": "2026-04-01"
                }
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Updating vendor order {vendor_order_id}")
        endpoint = f"/v1.0/companies/{company_id}/vendorOrders/{vendor_order_id}"
        await client.patch(endpoint, json=vendor_order_data)

    async def delete_vendor_order(
        self,
        vendor_order_id: int,
    ) -> None:
        """
        Delete an existing vendor order.

        Endpoint: DELETE /v1.0/companies/{companyID}/vendorOrders/{vendorOrderID}

        Returns 204 No Content on success, 404 if not found.

        Args:
            vendor_order_id: Vendor order ID to delete.
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Deleting vendor order {vendor_order_id}")
        endpoint = f"/v1.0/companies/{company_id}/vendorOrders/{vendor_order_id}"
        await client.delete(endpoint)

    async def get_vendor_order_receipts(
        self,
        vendor_order_id: int,
        columns: Optional[str] = None
    ) -> list[Dict[str, Any]]:
        """
        Get all receipts for a vendor order.
        
        Args:
            vendor_order_id: Vendor order ID
            columns: Optional columns to include
        
        Returns:
            List of receipts
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching receipts for vendor order {vendor_order_id}")

        endpoint = f"/v1.0/companies/{company_id}/vendorOrders/{vendor_order_id}/receipts/"
        
        params = {}
        if columns:
            params["columns"] = columns
        
        result = await client.get(endpoint, params=params)
        return result