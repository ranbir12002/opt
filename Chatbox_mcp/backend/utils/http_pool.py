# Chatbox_mcp/backend/utils/http_pool.py
"""
Shared persistent HTTP client pools for backend services.

Eliminates per-request TCP handshake overhead by reusing connections.
Call close_all() on app shutdown.
"""
from __future__ import annotations

import httpx
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── Pools (lazy-init) ────────────────────────────────────────────────────

_mcp_client: Optional[httpx.AsyncClient] = None      # MCP Node.js client
_extractor_client: Optional[httpx.AsyncClient] = None  # Extractor service
_health_client: Optional[httpx.AsyncClient] = None     # Short-timeout health checks


def get_mcp_pool() -> httpx.AsyncClient:
    """Persistent pool for MCP Node.js client (timeout 120s)."""
    global _mcp_client
    if _mcp_client is None or _mcp_client.is_closed:
        _mcp_client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _mcp_client


def get_extractor_pool() -> httpx.AsyncClient:
    """Persistent pool for the extractor service (timeout 60s)."""
    global _extractor_client
    if _extractor_client is None or _extractor_client.is_closed:
        _extractor_client = httpx.AsyncClient(
            timeout=60.0,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _extractor_client


def get_health_pool() -> httpx.AsyncClient:
    """Persistent pool for lightweight health checks (timeout 5s)."""
    global _health_client
    if _health_client is None or _health_client.is_closed:
        _health_client = httpx.AsyncClient(
            timeout=5.0,
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
        )
    return _health_client


async def close_all() -> None:
    """Close all pools. Call from FastAPI shutdown event."""
    global _mcp_client, _extractor_client, _health_client
    for name, pool in [("mcp", _mcp_client), ("extractor", _extractor_client), ("health", _health_client)]:
        if pool and not pool.is_closed:
            await pool.aclose()
            logger.info(f"Closed HTTP pool: {name}")
    _mcp_client = _extractor_client = _health_client = None
