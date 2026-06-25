"""
MyOB API HTTP Client.

Async httpx client with:
- Automatic OAuth2 token refresh before each request
- Rate limiting (8 RPS per MyOB API key limit)
- Retries with exponential backoff
- Structured logging
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from threading import Lock
from typing import Any, Dict, List, Optional, Union

import httpx

from config.settings import settings
from src.utils import get_logger

from .auth import MyOBAuth

logger = get_logger(__name__)


class RateLimiter:
    """
    Sliding-window rate limiter for MyOB API.

    MyOB rate limits are per API key (not per tenant).
    8 requests/second, 1M/day. Returns 403 when exceeded.
    """

    def __init__(
        self,
        requests_per_second: int = 8,
        max_wait: float = 5.0,
        max_concurrent: int = 6,
    ):
        self.rps_limit = requests_per_second
        self.window_sec = 1.0
        self.max_wait = max_wait

        self._window: deque = deque(maxlen=2000)
        self._window_lock = Lock()

        self._semaphore = asyncio.Semaphore(max_concurrent)

    def _is_slot_available(self) -> bool:
        now = time.time()
        while self._window and now - self._window[0] >= self.window_sec:
            self._window.popleft()
        return len(self._window) < self.rps_limit

    async def acquire(self) -> None:
        """Acquire a request slot. Blocks until available or timeout."""
        await self._semaphore.acquire()
        deadline = time.time() + self.max_wait

        while True:
            now = time.time()
            with self._window_lock:
                while self._window and now - self._window[0] >= self.window_sec:
                    self._window.popleft()
                if len(self._window) < self.rps_limit:
                    self._window.append(now)
                    return
                sleep_for = self.window_sec - (now - self._window[0])

            sleep_for = max(sleep_for, 0.01)
            if now + sleep_for > deadline:
                self._semaphore.release()
                raise TimeoutError(
                    f"Rate limit slot not available within {self.max_wait}s"
                )
            await asyncio.sleep(sleep_for)

    def release(self) -> None:
        """Release the concurrency semaphore."""
        self._semaphore.release()

    async def backoff_for(self, seconds: float) -> None:
        """Inject a backoff period when MyOB returns 403 for rate limiting."""
        future_ts = time.time() + seconds
        with self._window_lock:
            for _ in range(self.rps_limit):
                self._window.append(future_ts)
        logger.warning(f"MyOB rate limit backoff: pausing for {seconds:.1f}s")


class MyOBClient:
    """Async HTTP client for MyOB AccountRight API."""

    def __init__(
        self,
        auth: Optional[MyOBAuth] = None,
        timeout: float = 29.0,
        max_retries: int = 3,
    ):
        self.auth = auth or MyOBAuth()
        self.timeout = timeout
        self.max_retries = max_retries

        self.rate_limiter = RateLimiter(
            requests_per_second=settings.MYOB_RATE_LIMIT_RPS,
            max_wait=settings.MYOB_RATE_LIMIT_MAX_WAIT,
            max_concurrent=settings.MYOB_RATE_LIMIT_MAX_CONCURRENT,
        )

        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=10),
            follow_redirects=True,
        )

        logger.info(
            f"MyOB client initialized (timeout={timeout}s, retries={max_retries}, "
            f"rps={settings.MYOB_RATE_LIMIT_RPS})"
        )

    async def close(self) -> None:
        await self.client.aclose()
        logger.info("MyOB client closed")

    def _build_url(self, endpoint: str) -> str:
        base = self.auth.get_base_url().rstrip("/")
        endpoint = endpoint.lstrip("/")
        return f"{base}/{endpoint}"

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Any] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        """
        Make HTTP request with token refresh, rate limiting, and retries.

        MyOB returns 403 for rate limit (not 429).
        """
        url = self._build_url(endpoint)

        # Ensure token is valid before building headers
        await self.auth.ensure_valid_token()
        request_headers = self.auth.get_headers(headers)

        last_exception = None

        for attempt in range(1, self.max_retries + 1):
            await self.rate_limiter.acquire()

            try:
                logger.debug(f"[MYOB→] {method} {endpoint} params={params}")

                response = await self.client.request(
                    method=method.upper(),
                    url=url,
                    params=params,
                    json=json,
                    headers=request_headers,
                )

                logger.debug(
                    f"[MYOB←] {response.status_code} "
                    f"bytes={len(response.content)}"
                )

                # MyOB uses 403 for rate limiting
                if response.status_code == 403:
                    body = response.text[:300]
                    if "rate" in body.lower() or "exceeded" in body.lower():
                        logger.warning(
                            f"MyOB 403 rate limit on {method} {endpoint} "
                            f"(attempt {attempt}/{self.max_retries})"
                        )
                        await self.rate_limiter.backoff_for(2.0)
                        if attempt < self.max_retries:
                            await asyncio.sleep(2.0)
                            continue
                    # Not rate limit → raise
                    response.raise_for_status()

                response.raise_for_status()

                # 200 with no body
                if response.status_code == 200 and not response.content:
                    return {"success": True, "status_code": 200}

                return response.json()

            except (httpx.TimeoutException, httpx.NetworkError) as e:
                last_exception = e
                if attempt < self.max_retries:
                    wait = min(2 ** (attempt - 1), 10)
                    logger.warning(
                        f"{type(e).__name__} on {method} {endpoint} "
                        f"(attempt {attempt}/{self.max_retries}, retrying in {wait}s)"
                    )
                    await asyncio.sleep(wait)
                    continue
                raise

            except httpx.HTTPStatusError as e:
                body = e.response.text[:500]
                logger.error(f"HTTP error: {e.response.status_code} {body}")
                raise httpx.HTTPStatusError(
                    message=f"{e.response.status_code}: {body}",
                    request=e.request,
                    response=e.response,
                ) from None

            finally:
                self.rate_limiter.release()

        if last_exception:
            raise last_exception

    # ── Convenience methods ──────────────────────────────────────────

    async def get(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        """GET request."""
        return await self._request("GET", endpoint, params=params, headers=headers)

    async def post(
        self,
        endpoint: str,
        json: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        """POST request."""
        return await self._request("POST", endpoint, params=params, json=json, headers=headers)

    async def put(
        self,
        endpoint: str,
        json: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        """PUT request."""
        return await self._request("PUT", endpoint, params=params, json=json, headers=headers)

    async def delete(
        self,
        endpoint: str,
        json: Optional[Any] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        """DELETE request. MyOB requires UID in body for deletes."""
        return await self._request("DELETE", endpoint, json=json, headers=headers)


# ── Global singleton ─────────────────────────────────────────────────
_global_client: Optional[MyOBClient] = None


def get_myob_client() -> MyOBClient:
    """Get or create global MyOB client instance."""
    global _global_client
    if _global_client is None:
        _global_client = MyOBClient()
        logger.info("Global MyOB client created")
    return _global_client
