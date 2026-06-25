"""
Authentication handler for Simpro API.

Manages API tokens and request signing.
"""
from __future__ import annotations

from typing import Dict

from config.settings import settings
from src.utils import get_logger

logger = get_logger(__name__)


class SimproAuth:
    """
    Simpro API authentication handler.
    
    Simpro uses Bearer token authentication.
    This class manages the token and provides headers for requests.
    """
    
    def __init__(
        self,
        access_token: str | None = None,
        company_id: int | None = None,
        base_url: str | None = None,
    ):
        """
        Initialize authentication.

        Args:
            access_token: Simpro API access token (uses settings if None)
            company_id: Simpro company ID (uses settings if None)
            base_url: Simpro API base URL (uses settings if None)
        """
        self.access_token = access_token or settings.SIMPRO_ACCESS_TOKEN
        self.company_id = company_id if company_id is not None else settings.SIMPRO_COMPANY_ID
        self._base_url = base_url  # None → falls back to settings in get_base_url()

        # Token validation is deferred to request time — credentials are injected
        # per-request from the DB in multi-tenant mode via set_request_credentials()

        logger.info(f"Simpro auth initialized for company {self.company_id}")
    
    def get_headers(self, additional_headers: Dict[str, str] | None = None) -> Dict[str, str]:
        """
        Get authentication headers for Simpro API requests.
        
        Args:
            additional_headers: Additional headers to include
        
        Returns:
            Dictionary of HTTP headers
        """
        headers = {
            "Authorization": f"Bearer {self.access_token.strip()}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        if additional_headers:
            headers.update(additional_headers)
        
        return headers
    
    def get_base_url(self) -> str:
        """
        Get the base URL for Simpro API.

        Returns per-request URL if set, otherwise falls back to settings.
        """
        return self._base_url or settings.SIMPRO_BASE_URL
    
    def get_company_id(self) -> int:
        """
        Get the company ID.
        
        Returns:
            Company ID integer
        """
        return self.company_id