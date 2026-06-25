"""
Contacts API wrapper for Simpro.

Handles all contact/people-related API calls.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.simpro.client import get_simpro_client
from src.utils import get_logger

logger = get_logger(__name__)


class ContactsAPI:
    """
    API wrapper for Simpro contacts endpoints.
    
    Provides methods for:
    - Searching contacts
    - Getting contact details
    - Getting contact custom fields
    """
    
    def __init__(self):
        """Initialize Contacts API"""
        logger.debug("Contacts API initialized")
    
    async def get_contacts(
        self,
        page: int = 1,
        page_size: int = 250,
        search: Optional[str] = None,
        columns: Optional[str] = None,
        orderby: Optional[str] = None,
        **filters
    ) -> list[Dict[str, Any]]:
        """
        Get list of contacts.

        Args:
            page: Page number (1-based)
            page_size: Number of contacts per page (max 250)
            search: Search term ('any' or 'all')
            columns: Comma-separated columns to include
            orderby: Order by fields (e.g., 'Name' or '-Name,ID')
            **filters: Additional Simpro API filters (passed as query params)

        Returns:
            List of contacts
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching contacts (page={page}, page_size={page_size})")

        endpoint = f"/v1.0/companies/{company_id}/contacts/"

        params = {
            "page": page,
            "pageSize": page_size,
            **filters,
        }

        if search:
            params["search"] = search

        if columns:
            params["columns"] = columns

        if orderby:
            params["orderby"] = orderby

        result = await client.get(endpoint, params=params)
        return result

    async def get_contact_by_id(
        self,
        contact_id: int,
        columns: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get a specific contact by ID.
        
        Args:
            contact_id: Contact ID
            columns: Optional columns to include
        
        Returns:
            Contact data
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching contact {contact_id}")

        endpoint = f"/v1.0/companies/{company_id}/contacts/{contact_id}"
        
        params = {}
        if columns:
            params["columns"] = columns
        
        result = await client.get(endpoint, params=params)
        return result

    async def get_contact_custom_fields(
        self,
        contact_id: int,
        page: int = 1,
        page_size: int = 250,
        columns: Optional[str] = None
    ) -> list[Dict[str, Any]]:
        """
        Get custom fields for a contact.
        
        Args:
            contact_id: Contact ID
            page: Page number (1-based)
            page_size: Number of items per page
            columns: Optional columns to include
        
        Returns:
            List of custom fields
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching custom fields for contact {contact_id}")

        endpoint = f"/v1.0/companies/{company_id}/contacts/{contact_id}/customFields/"
        
        params = {
            "page": page,
            "pageSize": page_size
        }
        
        if columns:
            params["columns"] = columns
        
        result = await client.get(endpoint, params=params)
        return result

    async def get_contact_custom_field_by_id(
        self,
        contact_id: int,
        custom_field_id: int,
        columns: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get a specific custom field for a contact.
        
        Args:
            contact_id: Contact ID
            custom_field_id: Custom field ID
            columns: Optional columns to include
        
        Returns:
            Custom field data
        """
        client = get_simpro_client()
        company_id = client.auth.get_company_id()
        logger.info(f"Fetching custom field {custom_field_id} for contact {contact_id}")

        endpoint = f"/v1.0/companies/{company_id}/contacts/{contact_id}/customFields/{custom_field_id}"
        
        params = {}
        if columns:
            params["columns"] = columns
        
        result = await client.get(endpoint, params=params)
        return result