"""
Simpro API HTTP Client.

Migrated from original simpro_client.py with improvements:
- Async/await support with httpx
- Better error handling
- Structured logging
- Type hints
- Rate limiting per tenant
- Automatic retries
"""
from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict, deque
from contextvars import ContextVar
from threading import Lock
from typing import Any, Dict, Optional

import httpx

from config.settings import settings
from src.utils import get_logger

from .auth import SimproAuth

logger = get_logger(__name__)

# ===================================================================
# Per-request credential context vars
# Set these before calling get_simpro_client() to override .env defaults.
# ===================================================================
_request_simpro_token: ContextVar[Optional[str]] = ContextVar("simpro_token", default=None)
_request_simpro_url: ContextVar[Optional[str]] = ContextVar("simpro_url", default=None)
_request_simpro_company_id: ContextVar[Optional[int]] = ContextVar("simpro_company_id", default=None)


def set_request_credentials(token: str, base_url: str, company_id: Optional[int] = None) -> None:
    """Set per-request Simpro credentials. Call this at the start of each request."""
    _request_simpro_token.set(token)
    _request_simpro_url.set(base_url)
    if company_id is not None:
        _request_simpro_company_id.set(company_id)


class SimproRateLimitError(Exception):
    """Raised when Simpro returns HTTP 429 Too Many Requests."""

    def __init__(self, retry_after: float = 1.0, message: str = ""):
        self.retry_after = retry_after
        self.message = message or f"Rate limited by Simpro. Retry after {retry_after}s"
        super().__init__(self.message)


class RateLimiter:
    """
    Thread-safe rate limiter for Simpro API.
    
    Enforces request limits per tenant using a sliding window algorithm.
    """
    
    def __init__(
        self,
        requests_per_second: int = 10,
        max_wait: float = 5.0,
        max_concurrent: int = 8,
    ):
        """
        Initialize rate limiter.

        Args:
            requests_per_second: Maximum requests per second per tenant
            max_wait: Maximum seconds to wait for a slot (raises TimeoutError)
            max_concurrent: Maximum concurrent requests per tenant
        """
        self.rps_limit = requests_per_second
        self.window_sec = 1.0
        self.max_wait = max_wait

        # Sliding windows per tenant
        self._windows: Dict[str, deque] = defaultdict(lambda: deque(maxlen=2000))
        self._windows_lock = Lock()

        # Concurrency semaphores per tenant
        self._semaphores: Dict[str, asyncio.Semaphore] = {}
        self._semaphore_limit = max_concurrent

        # Metrics
        self._counters: Dict[str, int] = defaultdict(int)
        self._counters_lock = Lock()

        # Pattern to extract company ID from URL
        self._company_pattern = re.compile(r"/companies/(\d+)/", re.IGNORECASE)
    
    def _get_tenant_key(self, url: str, headers: Optional[Dict[str, str]] = None) -> str:
        """
        Extract tenant key from request.
        
        Priority:
        1. X-Tenant-ID header
        2. Company ID in URL
        3. "unknown"
        """
        # Check for explicit tenant header
        if headers:
            for key, value in headers.items():
                if key.lower() == "x-tenant-id" and value:
                    return str(value).strip()
        
        # Extract from URL
        match = self._company_pattern.search(url or "")
        if match:
            return f"company:{match.group(1)}"
        
        return "unknown"

    def _get_semaphore(self, tenant_key: str) -> asyncio.Semaphore:
        """Get or create per-tenant concurrency semaphore."""
        if tenant_key not in self._semaphores:
            self._semaphores[tenant_key] = asyncio.Semaphore(self._semaphore_limit)
        return self._semaphores[tenant_key]

    def _is_slot_available(self, tenant_key: str) -> bool:
        """Check if a request slot is available for tenant"""
        now = time.time()
        window = self._windows[tenant_key]
        
        # Remove expired timestamps
        while window and now - window[0] >= self.window_sec:
            window.popleft()
        
        return len(window) < self.rps_limit
    
    async def acquire(self, url: str, headers: Optional[Dict[str, str]] = None) -> None:
        """
        Acquire a request slot (waits if rate limit reached).

        Calculates exact sleep duration instead of busy-polling.
        Acquires per-tenant semaphore for concurrency limiting.

        Args:
            url: Request URL
            headers: Request headers

        Raises:
            TimeoutError: If max_wait exceeded without getting a slot
        """
        tenant_key = self._get_tenant_key(url, headers)

        # Increment metrics
        with self._counters_lock:
            self._counters["calls_total"] += 1
            self._counters[f"calls_by_tenant:{tenant_key}"] += 1

        # Acquire concurrency semaphore first (FIFO fairness)
        semaphore = self._get_semaphore(tenant_key)
        await semaphore.acquire()

        deadline = time.time() + self.max_wait

        while True:
            now = time.time()

            with self._windows_lock:
                window = self._windows[tenant_key]

                # Purge expired timestamps
                while window and now - window[0] >= self.window_sec:
                    window.popleft()

                if len(window) < self.rps_limit:
                    # Slot available — record and proceed
                    window.append(now)
                    return

                # Calculate sleep: time until oldest request expires from window
                sleep_for = self.window_sec - (now - window[0])

            sleep_for = max(sleep_for, 0.01)

            if now + sleep_for > deadline:
                # Release semaphore before raising
                semaphore.release()
                with self._counters_lock:
                    self._counters["timeouts_total"] += 1
                raise TimeoutError(
                    f"Rate limit slot not available within {self.max_wait}s "
                    f"for tenant {tenant_key}"
                )

            # Track throttle event
            with self._counters_lock:
                self._counters["throttles_total"] += 1

            logger.debug(
                f"Rate limited for tenant {tenant_key}, "
                f"sleeping {sleep_for:.3f}s "
                f"({len(self._windows[tenant_key])}/{self.rps_limit} in window)"
            )

            await asyncio.sleep(sleep_for)

    def release(self, url: str, headers: Optional[Dict[str, str]] = None) -> None:
        """
        Release the concurrency semaphore after a request completes.

        Must be called after every successful acquire(), typically in a finally block.
        """
        tenant_key = self._get_tenant_key(url, headers)
        semaphore = self._get_semaphore(tenant_key)
        semaphore.release()

    async def backoff_for(
        self, url: str, seconds: float, headers: Optional[Dict[str, str]] = None
    ) -> None:
        """
        Inject a backoff period when Simpro returns 429.

        Fills the sliding window with future timestamps so no new requests
        are issued until the backoff period expires for this tenant.
        """
        tenant_key = self._get_tenant_key(url, headers)
        future_ts = time.time() + seconds

        with self._windows_lock:
            window = self._windows[tenant_key]
            for _ in range(self.rps_limit):
                window.append(future_ts)

        with self._counters_lock:
            self._counters["backoffs_total"] += 1
            self._counters[f"backoff_seconds_total:{tenant_key}"] += seconds

        logger.warning(
            f"Simpro 429 backoff: tenant={tenant_key}, "
            f"pausing all requests for {seconds:.1f}s"
        )

    def get_metrics(self) -> Dict[str, Any]:
        """Get rate limiter metrics."""
        with self._counters_lock:
            metrics = dict(self._counters)

        # Add current window sizes
        with self._windows_lock:
            for tenant_key, window in self._windows.items():
                now = time.time()
                active = sum(1 for ts in window if now - ts < self.window_sec)
                metrics[f"window_active:{tenant_key}"] = active

        return metrics


def _resolve_dotted(record: dict, dotted_key: str):
    """Drill into nested dicts: 'Total.ExTax' → record['Total']['ExTax']."""
    obj = record
    for part in dotted_key.split("."):
        if isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return None
    return obj


def _filter_matches(actual, filter_value: str) -> bool:
    """Check if actual value matches a Simpro-style filter expression."""
    if actual is None:
        return False
    fv = filter_value.strip()

    # Wildcard: %keyword%
    if fv.startswith("%") and fv.endswith("%") and len(fv) > 2:
        return fv[1:-1].lower() in str(actual).lower()

    # Operator patterns: gt(), lt(), ge(), le(), ne(), between(), in()
    op_match = re.match(r"^(gt|lt|ge|le|ne|between|in)\((.+)\)$", fv, re.IGNORECASE)
    if op_match:
        op = op_match.group(1).lower()
        arg = op_match.group(2)
        try:
            if op == "gt":
                return float(actual) > float(arg)
            elif op == "lt":
                return float(actual) < float(arg)
            elif op == "ge":
                return float(actual) >= float(arg)
            elif op == "le":
                return float(actual) <= float(arg)
            elif op == "ne":
                return str(actual).lower() != arg.lower()
            elif op == "between":
                parts = [x.strip() for x in arg.split(",")]
                if len(parts) == 2:
                    return float(parts[0]) <= float(actual) <= float(parts[1])
            elif op == "in":
                vals = [x.strip().lower() for x in arg.split(",")]
                return str(actual).lower() in vals
        except (ValueError, TypeError):
            return False

    # Exact match (case-insensitive for strings)
    return str(actual).lower() == fv.lower()


def _apply_filters(records: list, filters: Dict[str, str]) -> list:
    """Post-filter a list of records using Simpro-style filter expressions."""
    return [
        r for r in records
        if all(
            _filter_matches(_resolve_dotted(r, key), value)
            for key, value in filters.items()
        )
    ]


class SimproClient:
    """
    Async HTTP client for Simpro API.
    
    Features:
    - Async/await with httpx
    - Automatic retries with exponential backoff
    - Rate limiting per tenant
    - Request/response logging
    - Error handling
    
    Example:
        >>> client = SimproClient()
        >>> jobs = await client.get_jobs(page=1, page_size=10)
    """
    
    def __init__(
        self,
        auth: Optional[SimproAuth] = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        requests_per_second: Optional[int] = None,
        max_wait: Optional[float] = None,
        max_concurrent: Optional[int] = None,
    ):
        """
        Initialize Simpro client.

        Args:
            auth: Authentication handler (creates default if None)
            timeout: Request timeout in seconds
            max_retries: Maximum retry attempts
            requests_per_second: Rate limit per tenant (default from settings)
            max_wait: Max seconds to wait for rate limit slot (default from settings)
            max_concurrent: Max concurrent requests per tenant (default from settings)
        """
        self.auth = auth or SimproAuth()
        self.timeout = timeout
        self.max_retries = max_retries

        # Read from settings with parameter overrides
        rps = requests_per_second or settings.SIMPRO_RATE_LIMIT_RPS
        wait = max_wait or settings.SIMPRO_RATE_LIMIT_MAX_WAIT
        concurrent = max_concurrent or settings.SIMPRO_RATE_LIMIT_MAX_CONCURRENT

        # Rate limiter
        self.rate_limiter = RateLimiter(
            requests_per_second=rps,
            max_wait=wait,
            max_concurrent=concurrent,
        )

        # HTTP client
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20
            ),
            follow_redirects=True
        )

        logger.info(
            f"Simpro client initialized "
            f"(timeout={timeout}s, retries={max_retries}, "
            f"rps={rps}, max_wait={wait}s, max_concurrent={concurrent})"
        )
    
    async def __aenter__(self):
        """Async context manager entry"""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.close()
    
    async def close(self):
        """Close HTTP client"""
        await self.client.aclose()
        logger.info("Simpro client closed")
    
    def _build_url(self, endpoint: str) -> str:
        """
        Build full URL for endpoint.
        
        Args:
            endpoint: API endpoint (e.g., "/v1.0/companies/2/jobs/")
        
        Returns:
            Full URL
        """
        base_url = self.auth.get_base_url().rstrip("/")
        endpoint = endpoint.lstrip("/")
        return f"{base_url}/{endpoint}"
    
    def _log_request(
        self,
        method: str,
        url: str,
        params: Optional[Dict] = None,
        json_body: Optional[Dict] = None
    ):
        """Log request details"""
        logger.debug(f"[SIMPRO→] {method} {url}")
        if params:
            logger.debug(f"[SIMPRO→] params={params}")
        if json_body:
            # Truncate large bodies
            body_str = str(json_body)[:800]
            logger.debug(f"[SIMPRO→] body={body_str}")
    
    def _log_response(self, response: httpx.Response):
        """Log response details"""
        preview = response.text[:800] if len(response.text) < 800 else f"{response.text[:800]}..."
        logger.debug(
            f"[SIMPRO←] {response.status_code} "
            f"bytes={len(response.content)} "
            f"preview={preview}"
        )

    @staticmethod
    def _parse_retry_after(response: httpx.Response) -> float:
        """
        Parse Retry-After header from a 429 response.

        Returns:
            Seconds to wait before retrying (float, minimum 0.5, default 2.0)
        """
        retry_after_raw = response.headers.get("Retry-After", "")
        if retry_after_raw:
            try:
                return max(float(retry_after_raw), 0.5)
            except ValueError:
                pass
        return 2.0

    @staticmethod
    def _extract_rejected_filters(response: httpx.Response) -> list[str]:
        """
        Parse a Simpro 422 response to find which filter fields were rejected.

        Simpro returns JSON like:
            {"errors": [{"path": "/Total/ExTax", "message": "This API Column does not allow search requests."}]}

        Returns a list of dot-notation filter keys (e.g. ["Total.ExTax"]).
        """
        try:
            body = response.json()
        except Exception:
            return []

        rejected = []
        for err in body.get("errors", []):
            msg = err.get("message", "")
            path = err.get("path", "")
            if "does not allow search" in msg and path:
                # Convert "/Total/ExTax" → "Total.ExTax"
                key = path.lstrip("/").replace("/", ".")
                if key:
                    rejected.append(key)
        return rejected

    @staticmethod
    def _post_filter(result, rejected_filters: Dict[str, str]):
        """
        Apply rejected filters as in-memory post-filters on the API result.

        Works on both list results and dict results (filters are skipped
        for non-list responses since there's nothing to filter).
        """
        if not rejected_filters:
            return result

        # If result is a list of records, filter them
        if isinstance(result, list):
            filtered = _apply_filters(result, rejected_filters)
            logger.info(
                f"Post-filter for unsupported field(s) "
                f"{list(rejected_filters.keys())}: "
                f"{len(result)} → {len(filtered)}"
            )
            return filtered

        return result

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Make HTTP request to Simpro API with retries and rate limiting.

        Retry policy:
        - TimeoutException / NetworkError: up to max_retries, exponential backoff
        - HTTP 429: up to max_retries, uses Retry-After header (default 2s)
        - Other HTTP errors: no retry, raise immediately

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint
            params: Query parameters
            json: JSON body
            headers: Additional headers

        Returns:
            Response JSON

        Raises:
            httpx.HTTPStatusError: On non-retryable HTTP error
            httpx.TimeoutException: On timeout after all retries
            TimeoutError: If rate limiter max_wait exceeded
        """
        url = self._build_url(endpoint)
        request_headers = self.auth.get_headers(headers)

        last_exception = None

        for attempt in range(1, self.max_retries + 1):
            # Rate limiting (acquire slot + concurrency semaphore)
            await self.rate_limiter.acquire(url, request_headers)

            try:
                # Log request
                self._log_request(method, url, params, json)

                # Make request
                response = await self.client.request(
                    method=method.upper(),
                    url=url,
                    params=params,
                    json=json,
                    headers=request_headers
                )

                # Log response
                self._log_response(response)

                # Handle 429 Too Many Requests
                if response.status_code == 429:
                    retry_after = self._parse_retry_after(response)
                    logger.warning(
                        f"Simpro 429 on {method} {endpoint} "
                        f"(attempt {attempt}/{self.max_retries}, "
                        f"retry_after={retry_after:.1f}s)"
                    )

                    # Signal rate limiter to back off ALL requests for this tenant
                    await self.rate_limiter.backoff_for(url, retry_after, request_headers)

                    if attempt < self.max_retries:
                        await asyncio.sleep(retry_after)
                        continue
                    else:
                        response.raise_for_status()

                # Raise on other error statuses
                response.raise_for_status()

                # Handle 204 No Content (successful updates/deletes with no body)
                if response.status_code == 204:
                    return {"success": True, "status_code": 204}

                return response.json()

            except (httpx.TimeoutException, httpx.NetworkError) as e:
                last_exception = e
                if attempt < self.max_retries:
                    wait = min(2 ** (attempt - 1), 10)  # 1s, 2s, 4s... capped at 10s
                    logger.warning(
                        f"{type(e).__name__} on {method} {endpoint} "
                        f"(attempt {attempt}/{self.max_retries}, "
                        f"retrying in {wait}s)"
                    )
                    await asyncio.sleep(wait)
                    continue
                else:
                    logger.error(
                        f"{type(e).__name__} on {method} {endpoint} "
                        f"after {self.max_retries} attempts"
                    )
                    raise

            except httpx.HTTPStatusError as e:
                # ── 422: unsupported filter field → strip and retry ──
                if (
                    e.response.status_code == 422
                    and params
                    and method.upper() == "GET"
                ):
                    rejected = self._extract_rejected_filters(e.response)
                    if rejected:
                        # Remove rejected filter keys from params
                        new_params = {
                            k: v for k, v in params.items()
                            if k not in rejected
                        }
                        rejected_detail = {
                            k: params[k] for k in rejected if k in params
                        }
                        logger.warning(
                            f"Simpro 422: unsupported filter(s) {rejected_detail} "
                            f"on {endpoint} — retrying without them, will post-filter"
                        )
                        # Semaphore will be released by the finally block below.
                        # The recursive _request call acquires its own semaphore.
                        try:
                            result = await self._request(
                                method, endpoint,
                                params=new_params, json=json, headers=headers,
                            )
                        except Exception:
                            raise
                        # Apply rejected filters as post-filter
                        return self._post_filter(result, rejected_detail)

                # Non-429 HTTP errors: do not retry
                response_body = e.response.text[:500]
                logger.error(
                    f"HTTP error: {e.response.status_code} {response_body}"
                )
                raise httpx.HTTPStatusError(
                    message=f"{e.response.status_code}: {response_body}",
                    request=e.request,
                    response=e.response,
                ) from None

            except Exception as e:
                logger.error(f"Unexpected error: {e}", exc_info=True)
                raise

            finally:
                # Always release the concurrency semaphore
                self.rate_limiter.release(url, request_headers)

        # Safety net (should not normally reach here)
        if last_exception:
            raise last_exception
    
    async def get(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Make GET request.
        
        Args:
            endpoint: API endpoint
            params: Query parameters
            headers: Additional headers
        
        Returns:
            Response JSON
        """
        logger.info(f"[SIMPRO→] GET {endpoint}")
        if params:
            logger.debug(f"[SIMPRO→] Params: {params}")
        result = await self._request("GET", endpoint, params=params, headers=headers)
        import json
        result_str = json.dumps(result, indent=2)
        logger.info(f"[simpro<-] Response size: {len(result_str)} characters")
        logger.debug(f"[simpro<-] First 500 chars: {result_str[:500]}...")

        if isinstance(result, dict):
            logger.info(f"[simpro<-] Top-level keys: {list(result.keys())}")
            if 'CustomFields' in result:
                logger.info(f"[simpro<-] CustomFields count: {len(result['CustomFields'])}")
        return result
    
    async def post(
        self,
        endpoint: str,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Make POST request.
        
        Args:
            endpoint: API endpoint
            json: JSON body
            params: Query parameters
            headers: Additional headers
        
        Returns:
            Response JSON
        """
        return await self._request("POST", endpoint, params=params, json=json, headers=headers)
    
    async def put(
        self,
        endpoint: str,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """Make PUT request"""
        return await self._request("PUT", endpoint, params=params, json=json, headers=headers)
    
    async def patch(
        self,
        endpoint: str,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """Make PATCH request"""
        return await self._request("PATCH", endpoint, params=params, json=json, headers=headers)
    
    async def delete(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """Make DELETE request"""
        return await self._request("DELETE", endpoint, params=params, headers=headers)
    
    def get_metrics(self) -> Dict[str, Any]:
        """
        Get client metrics.
        
        Returns:
            Dictionary of metrics (request counts, throttles, etc.)
        """
        return self.rate_limiter.get_metrics()


# ===================================================================
# Global client instance (lazy initialized)
# ===================================================================
_global_client: Optional[SimproClient] = None


def get_simpro_client() -> SimproClient:
    """
    Get Simpro client for the current request.

    If per-request credentials have been set via set_request_credentials(),
    returns a fresh client scoped to those credentials (multi-tenant).
    Otherwise falls back to the global singleton using .env credentials (dev/single-tenant).

    Returns:
        SimproClient instance
    """
    token = _request_simpro_token.get()
    url = _request_simpro_url.get()

    if token and url:
        # Per-request tenant client — fresh instance, not cached
        company_id = _request_simpro_company_id.get()
        auth = SimproAuth(access_token=token, company_id=company_id, base_url=url)
        return SimproClient(auth=auth)

    # Fall back to global singleton (dev mode / single-tenant)
    global _global_client
    if _global_client is None:
        if not settings.SIMPRO_ACCESS_TOKEN:
            # Multi-tenant mode: no global creds — return a placeholder client.
            # Real credentials will be injected per-request via set_request_credentials().
            if _global_client is None:
                logger.info("No global Simpro credentials — running in multi-tenant mode (per-request credentials)")
                _global_client = SimproClient()
            return _global_client
        _global_client = SimproClient()
        logger.info("Global Simpro client created")
    return _global_client