"""
Invoices API wrapper for Simpro.

Handles all invoice-related API calls.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.simpro.client import get_simpro_client
from src.utils import get_logger

logger = get_logger(__name__)


class InvoicesAPI:
    """
    API wrapper for Simpro invoices endpoints.
    
    Provides methods for:
    - Searching invoices
    - Getting invoice details
    - Creating invoices
    """
    
    def __init__(self):
        """Initialize Invoices API"""
        logger.debug("Invoices API initialized")
    
    async def get_invoices(
        self,
        page: int = 1,
        page_size: int = 250,
        is_paid: Optional[str] = None,
        columns: Optional[str] = None,
        **filters
    ) -> list[Dict[str, Any]]:
        """
        Get list of invoices.

        Args:
            page: Page number (1-based)
            page_size: Number of invoices per page
            is_paid: Filter by paid status ('true' or 'false')
            columns: Comma-separated columns to include
            **filters: Additional Simpro API filters (passed as query params)

        Returns:
            List of invoices
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching invoices (page={page}, page_size={page_size})")

        endpoint = f"/v1.0/companies/{company_id}/invoices/"

        params = {
            "page": page,
            "pageSize": page_size,
            **filters,
        }

        if is_paid is not None:
            params["IsPaid"] = is_paid

        if columns:
            params["columns"] = columns

        result = await client.get(endpoint, params=params)
        return result

    async def get_invoice_by_id(
        self,
        invoice_id: int,
        columns: Optional[str] = None,
        display: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get a specific invoice by ID.

        Args:
            invoice_id: Invoice ID
            columns: Optional columns to include
            display: Set to 'all' to include subresources in one call

        Returns:
            Invoice data
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching invoice {invoice_id} (display={display})")

        endpoint = f"/v1.0/companies/{company_id}/invoices/{invoice_id}"

        params = {}
        if columns:
            params["columns"] = columns
        if display:
            params["display"] = display

        result = await client.get(endpoint, params=params)
        return result

    async def create_invoice(
        self,
        invoice_data: Dict[str, Any],
        company_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Create a new invoice in Simpro.
        
        POST /api/v1.0/companies/{companyID}/invoices/
        
        Args:
            invoice_data: Complete invoice body
            company_id: Optional company ID (uses default if not provided)
        
        Returns:
            Created invoice data from Simpro
        """
        client = get_simpro_client()
        cid = company_id or client.auth.get_company_id()
        endpoint = f"/v1.0/companies/{cid}/invoices/"

        job_id = invoice_data.get("Jobs", [None])[0] if invoice_data.get("Jobs") else None
        logger.info(f"POSTing invoice to Simpro: CompanyID={cid}, JobID={job_id}")

        # POST to Simpro API using client (not self._request)
        result = await client.post(endpoint, json=invoice_data)

        return result

    async def update_invoice(
        self,
        invoice_id: int,
        invoice_data: Dict[str, Any],
    ) -> None:
        """
        Update (PATCH) an existing invoice.

        PATCH /api/v1.0/companies/{companyID}/invoices/{invoiceID}

        Only include fields that need updating — Simpro merges them.
        Returns 204 No Content on success, 422 if body is invalid.

        Args:
            invoice_id: Invoice ID to update
            invoice_data: Partial invoice body with fields to update
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"PATCHing invoice {invoice_id}")
        endpoint = f"/v1.0/companies/{company_id}/invoices/{invoice_id}"
        await client.patch(endpoint, json=invoice_data)

    async def delete_invoice(self, invoice_id: int) -> None:
        """
        Delete an invoice.

        DELETE /api/v1.0/companies/{companyID}/invoices/{invoiceID}

        Args:
            invoice_id: Invoice ID to delete

        Returns:
            None (204 No Content on success)
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Deleting invoice {invoice_id}")
        endpoint = f"/v1.0/companies/{company_id}/invoices/{invoice_id}"
        await client.delete(endpoint)