"""
Credit Notes API wrapper for Simpro.

Handles all credit note-related API calls.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.simpro.client import get_simpro_client
from src.utils import get_logger

logger = get_logger(__name__)


class CreditNotesAPI:
    """
    API wrapper for Simpro credit notes endpoints.
    
    Provides methods for:
    - Getting credit notes by invoice
    - Getting specific credit note details
    """
    
    def __init__(self):
        """Initialize Credit Notes API"""
        logger.debug("Credit Notes API initialized")
    
    async def get_credit_notes_by_invoice(
        self,
        invoice_id: int,
        columns: Optional[str] = None
    ) -> list[Dict[str, Any]]:
        """
        Get credit notes for a specific invoice.
        
        Args:
            invoice_id: Invoice ID
            columns: Optional columns to include
        
        Returns:
            List of credit notes
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching credit notes for invoice {invoice_id}")

        endpoint = f"/v1.0/companies/{company_id}/invoices/{invoice_id}/creditNotes/"
        
        params = {}
        if columns:
            params["columns"] = columns
        
        result = await client.get(endpoint, params=params)
        return result

    async def get_credit_note_by_id(
        self,
        invoice_id: int,
        credit_note_id: int,
        columns: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get a specific credit note.
        
        Args:
            invoice_id: Invoice ID
            credit_note_id: Credit note ID
            columns: Optional columns to include
        
        Returns:
            Credit note data
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching credit note {credit_note_id} for invoice {invoice_id}")

        endpoint = f"/v1.0/companies/{company_id}/invoices/{invoice_id}/creditNotes/{credit_note_id}"
        
        params = {}
        if columns:
            params["columns"] = columns
        
        result = await client.get(endpoint, params=params)
        return result

    async def search_credit_notes(
        self,
        invoice_no: str,
        columns: Optional[str] = None
    ) -> list[Dict[str, Any]]:
        """
        Search credit notes by invoice number.
        
        Args:
            invoice_no: Invoice number to search
            columns: Optional columns to include
        
        Returns:
            List of credit notes
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Searching credit notes for invoice number {invoice_no}")

        endpoint = f"/v1.0/companies/{company_id}/creditNotes/"
        
        params = {
            "InvoiceNo": invoice_no
        }
        
        if columns:
            params["columns"] = columns
        
        result = await client.get(endpoint, params=params)
        return result