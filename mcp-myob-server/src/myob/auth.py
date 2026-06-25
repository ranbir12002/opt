"""
MyOB OAuth2 Authentication Manager.

Handles:
- OAuth2 Authorization Code flow
- Automatic token refresh (20-min expiry)
- Company file discovery (regional URL routing)
- Header construction (Bearer + x-myobapi-key + x-myobapi-cftoken)
- Token persistence to tokens.json for restart resilience
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from config.settings import settings
from src.utils import get_logger

logger = get_logger(__name__)

# OAuth2 endpoints
MYOB_AUTH_URL = "https://secure.myob.com/oauth2/account/authorize"
MYOB_TOKEN_URL = "https://secure.myob.com/oauth2/v1/authorize"
MYOB_API_ENTRY = "https://api.myob.com/accountright"

# Token file path (alongside .env)
TOKEN_FILE = Path(__file__).parent.parent.parent / "tokens.json"

# Refresh 2 minutes before expiry to avoid edge cases
TOKEN_REFRESH_BUFFER = 120


class MyOBAuth:
    """OAuth2 authentication manager for MyOB AccountRight API."""

    def __init__(self):
        self.client_id = settings.MYOB_CLIENT_ID
        self.client_secret = settings.MYOB_CLIENT_SECRET
        self.cf_username = settings.MYOB_CF_USERNAME
        self.cf_password = settings.MYOB_CF_PASSWORD

        # Token state (loaded from tokens.json or settings)
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.token_expiry: float = 0.0

        # Company file info
        self.company_file_id: Optional[str] = settings.MYOB_COMPANY_FILE_ID
        self.company_file_uri: Optional[str] = settings.MYOB_COMPANY_FILE_URI

        # Thread safety for token refresh
        self._lock = asyncio.Lock()

        # Load persisted tokens
        self._load_tokens()

        logger.info(
            f"MyOB auth initialized "
            f"(client_id={'***' + self.client_id[-4:] if self.client_id else 'NOT SET'}, "
            f"company_file={'SET' if self.company_file_id else 'NOT SET'})"
        )

    def _load_tokens(self) -> None:
        """Load tokens from tokens.json if available."""
        if TOKEN_FILE.exists():
            try:
                data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
                self.access_token = data.get("access_token")
                self.refresh_token = data.get("refresh_token")
                self.token_expiry = data.get("token_expiry", 0.0)
                # Also restore company file info if saved
                if not self.company_file_id and data.get("company_file_id"):
                    self.company_file_id = data["company_file_id"]
                if not self.company_file_uri and data.get("company_file_uri"):
                    self.company_file_uri = data["company_file_uri"]
                logger.info("Loaded tokens from tokens.json")
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to load tokens.json: {e}")

    def _save_tokens(self) -> None:
        """Persist tokens to tokens.json for restart resilience."""
        data = {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "token_expiry": self.token_expiry,
            "company_file_id": self.company_file_id,
            "company_file_uri": self.company_file_uri,
        }
        try:
            TOKEN_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
            logger.debug("Saved tokens to tokens.json")
        except IOError as e:
            logger.error(f"Failed to save tokens.json: {e}")

    def _is_token_expired(self) -> bool:
        """Check if access token is expired or about to expire."""
        if not self.access_token:
            return True
        return time.time() >= (self.token_expiry - TOKEN_REFRESH_BUFFER)

    async def ensure_valid_token(self) -> str:
        """
        Ensure we have a valid access token, refreshing if needed.

        Returns:
            Valid access token string.

        Raises:
            RuntimeError: If no refresh token available and token is expired.
        """
        if not self._is_token_expired():
            return self.access_token

        async with self._lock:
            # Double-check after acquiring lock
            if not self._is_token_expired():
                return self.access_token

            if not self.refresh_token:
                raise RuntimeError(
                    "Access token expired and no refresh token available. "
                    "Run the OAuth setup flow first (scripts/test_connection.py)."
                )

            await self._refresh_access_token()
            return self.access_token

    async def _refresh_access_token(self) -> None:
        """Exchange refresh token for new access + refresh tokens."""
        logger.info("Refreshing MyOB access token...")

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                MYOB_TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": self.refresh_token,
                    "grant_type": "refresh_token",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            if response.status_code != 200:
                error_text = response.text[:500]
                logger.error(f"Token refresh failed: {response.status_code} {error_text}")
                raise RuntimeError(
                    f"MyOB token refresh failed ({response.status_code}): {error_text}"
                )

            data = response.json()
            self.access_token = data["access_token"]
            self.refresh_token = data["refresh_token"]
            # MyOB tokens expire in 1200 seconds (20 minutes)
            expires_in = int(data.get("expires_in", 1200))
            self.token_expiry = time.time() + expires_in

            self._save_tokens()
            logger.info(f"Token refreshed successfully (expires in {expires_in}s)")

    async def discover_company_files(self) -> list[Dict[str, Any]]:
        """
        List company files accessible to the authenticated user.

        GET https://api.myob.com/accountright/

        Returns:
            List of company file dicts with keys: Id, Name, LibraryPath, Uri, etc.
        """
        token = await self.ensure_valid_token()

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{MYOB_API_ENTRY}/",
                headers={
                    "Authorization": f"Bearer {token}",
                    "x-myobapi-key": self.client_id,
                    "x-myobapi-version": "v2",
                    "Accept": "application/json",
                },
            )
            response.raise_for_status()
            return response.json()

    async def set_company_file(self, company_file_id: str, company_file_uri: str) -> None:
        """Set the active company file for all subsequent API calls."""
        self.company_file_id = company_file_id
        self.company_file_uri = company_file_uri.rstrip("/")
        self._save_tokens()
        logger.info(f"Company file set: {company_file_id} at {company_file_uri}")

    def get_headers(self, additional: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """
        Build request headers for MyOB API calls.

        Returns dict with:
        - Authorization: Bearer {access_token}
        - x-myobapi-key: {client_id}
        - x-myobapi-cftoken: Base64({cf_username}:{cf_password})
        - x-myobapi-version: v2
        - Content-Type: application/json
        - Accept-Encoding: gzip,deflate
        """
        cf_token = base64.b64encode(
            f"{self.cf_username}:{self.cf_password}".encode("utf-8")
        ).decode("utf-8")

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "x-myobapi-key": self.client_id,
            "x-myobapi-cftoken": cf_token,
            "x-myobapi-version": "v2",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Accept-Encoding": "gzip,deflate",
        }

        if additional:
            headers.update(additional)

        return headers

    def get_base_url(self) -> str:
        """
        Get the base URL for API calls.

        Returns:
            URL like https://ar1.api.myob.com/accountright/{company_file_id}
        """
        if not self.company_file_uri or not self.company_file_id:
            raise RuntimeError(
                "Company file not configured. Set MYOB_COMPANY_FILE_URI and "
                "MYOB_COMPANY_FILE_ID in .env, or run company file discovery first."
            )
        return f"{self.company_file_uri}/{self.company_file_id}"

    def get_authorization_url(self) -> str:
        """Get the OAuth2 authorization URL for initial setup."""
        return (
            f"{MYOB_AUTH_URL}"
            f"?client_id={self.client_id}"
            f"&redirect_uri={settings.MYOB_REDIRECT_URI}"
            f"&response_type=code"
            f"&scope=CompanyFile"
            f"&prompt=consent"
        )

    async def exchange_code_for_tokens(self, authorization_code: str) -> Dict[str, Any]:
        """
        Exchange an authorization code for access + refresh tokens.

        Used during initial OAuth setup flow.
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                MYOB_TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": authorization_code,
                    "redirect_uri": settings.MYOB_REDIRECT_URI,
                    "grant_type": "authorization_code",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            if response.status_code != 200:
                error_text = response.text[:500]
                raise RuntimeError(f"Token exchange failed ({response.status_code}): {error_text}")

            data = response.json()
            self.access_token = data["access_token"]
            self.refresh_token = data["refresh_token"]
            expires_in = int(data.get("expires_in", 1200))
            self.token_expiry = time.time() + expires_in

            self._save_tokens()
            logger.info("OAuth tokens obtained successfully")
            return data
