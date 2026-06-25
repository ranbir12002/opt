# backend/utils/mcp_executor.py
"""
Centralized MCP Tool Executor.

Single source of truth for calling MCP tools via the MCP Server HTTP API.
All agents import this instead of maintaining their own copy.

Includes a per-request result cache for reference data tools (list_employees,
list_contractors, etc.) to avoid duplicate API calls within a single request.

Usage:
    from utils.mcp_executor import MCPToolExecutor

    mcp_executor = MCPToolExecutor(
        tool_registry=mcp_client,   # MCPToolClient instance
        company_id=2,
        tracker=request_tracker,    # optional RequestTracker
    )
    result = await mcp_executor.call_tool("search_jobs", {"search": "Main St"})
"""

from __future__ import annotations
import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Tools whose results are safe to cache within a single request.
# These return reference data that doesn't change mid-request.
_CACHEABLE_TOOLS: Set[str] = {
    "list_employees",
    "list_contractors",
    "search_contacts",
    "get_cost_centre_types",
    "get_job_sections",
    "get_quote_sections",
    "get_job_section_cost_centres",
    "get_quote_section_cost_centres",
    "get_setup_cost_centres",
    "get_setup_cost_centre_detail",
    "get_chart_of_accounts",
    "get_chart_of_accounts_detail",
}

# ── Global TTL cache for reference data across requests ──────────────────
# Stable reference data (employees, contractors, cost centre types) rarely
# changes but is fetched by every single request. Caching it globally with
# a short TTL eliminates redundant Simpro API calls.
_GLOBAL_CACHEABLE: Set[str] = {
    "list_employees",
    "list_contractors",
    "get_cost_centre_types",
    "get_setup_cost_centres",
    "get_chart_of_accounts",
}
_GLOBAL_CACHE_TTL: float = 300.0  # 5 minutes

# {cache_key: (timestamp, data)}
_global_cache: Dict[str, Tuple[float, Any]] = {}
_global_inflight: Dict[str, asyncio.Event] = {}


def _global_cache_get(key: str) -> Optional[Any]:
    """Return cached value if present and not expired, else None."""
    entry = _global_cache.get(key)
    if entry is None:
        return None
    ts, data = entry
    if time.monotonic() - ts > _GLOBAL_CACHE_TTL:
        _global_cache.pop(key, None)
        return None
    return data


def _global_cache_set(key: str, data: Any) -> None:
    """Store a value in the global cache with current timestamp."""
    _global_cache[key] = (time.monotonic(), data)


def invalidate_global_cache(tool_name: Optional[str] = None) -> int:
    """
    Invalidate global cache entries.

    Args:
        tool_name: If provided, only invalidate entries for this tool.
                   If None, clear the entire global cache.

    Returns:
        Number of entries removed.
    """
    if tool_name is None:
        count = len(_global_cache)
        _global_cache.clear()
        logger.info(f"Global cache cleared ({count} entries)")
        return count
    keys_to_remove = []
    for k in list(_global_cache.keys()):
        parts = k.split("::")
        if len(parts) >= 3 and parts[2] == tool_name:
            keys_to_remove.append(k)
    for k in keys_to_remove:
        _global_cache.pop(k, None)
    logger.info(f"Global cache invalidated: {tool_name} ({len(keys_to_remove)} entries)")
    return len(keys_to_remove)


class MCPToolExecutor:
    """
    Wrapper for calling MCP tools via the MCP Server HTTP API.
    Accepts an MCPToolClient instance (HTTP-based) from the backend.

    Includes a per-request result cache: identical calls to reference-data
    tools (list_employees, list_contractors, etc.) are served from cache
    instead of hitting the Simpro API again. The cache is scoped to this
    executor instance (one per user request).
    """

    def __init__(
        self,
        tool_registry: Any,
        company_id: int,
        tracker: Any = None,
        tenant_id: Optional[str] = None,
    ):
        """
        Args:
            tool_registry: MCPToolClient instance (HTTP client to MCP server)
            company_id: Simpro company ID
            tracker: Optional RequestTracker for recording tool execution history
            tenant_id: Optional tenant identifier string
        """
        self._client = tool_registry
        self.company_id = company_id
        self._tool_names_cache: Optional[List[str]] = None
        self.tracker = tracker
        
        # Resolve tenant_id: try parameter, fallback to tool_registry._simpro_url, fallback to "global"
        if tenant_id:
            self.tenant_id = tenant_id
        elif hasattr(tool_registry, "_simpro_url") and tool_registry._simpro_url:
            self.tenant_id = tool_registry._simpro_url
        elif hasattr(tool_registry, "simpro_url") and tool_registry.simpro_url:
            self.tenant_id = tool_registry.simpro_url
        else:
            self.tenant_id = "global"

        # Per-request result cache: {cache_key: result_data}
        self._result_cache: Dict[str, Any] = {}
        # In-flight locks to prevent duplicate concurrent requests for the same key
        self._inflight: Dict[str, asyncio.Event] = {}

    def _cache_key(self, tool_name: str, params: Dict[str, Any]) -> str:
        """Build a stable cache key from tool name + params, scoped by tenant and company."""
        # Sort keys for deterministic serialization
        serialized_params = json.dumps(params, sort_keys=True, default=str)
        return f"{self.tenant_id}::{self.company_id}::{tool_name}::{serialized_params}"

    async def get_available_tools(self) -> List[str]:
        """Get list of all available tool names from MCP server."""
        if self._tool_names_cache is None:
            self._tool_names_cache = await self._client.get_tool_names()
        return self._tool_names_cache

    async def get_tool_descriptions(self) -> Dict[str, str]:
        """Get {tool_name: description} for all available tools."""
        return await self._client.get_tool_descriptions()

    async def get_tool_catalog(self) -> Dict[str, Dict[str, Any]]:
        """Get rich tool catalog {name: {description, required_params, optional_params}}."""
        return await self._client.get_tool_catalog()

    async def call_tool(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call an MCP tool by name via HTTP.

        For cacheable reference-data tools, results are cached per-request
        so that e.g. 50 rows resolving staff don't each call list_employees.

        Args:
            tool_name: Name of the tool (e.g., "search_jobs")
            params: Tool parameters

        Returns:
            Tool result dict (data payload, envelope stripped)

        Raises:
            ValueError: If the tool call fails
        """
        # ── Cache lookup for reference-data tools ──
        if tool_name in _CACHEABLE_TOOLS:
            key = self._cache_key(tool_name, params)

            # Layer 1: per-request cache (fastest, no TTL check)
            if key in self._result_cache:
                logger.debug(f"Cache HIT (request): {tool_name}")
                return self._result_cache[key]

            # Layer 2: global TTL cache (cross-request, avoids Simpro API)
            if tool_name in _GLOBAL_CACHEABLE:
                global_hit = _global_cache_get(key)
                if global_hit is not None:
                    logger.debug(f"Cache HIT (global): {tool_name}")
                    self._result_cache[key] = global_hit
                    return global_hit

                # Coalesce concurrent global requests
                if key in _global_inflight:
                    logger.debug(f"Cache WAIT (global): {tool_name}")
                    await _global_inflight[key].wait()
                    global_hit = _global_cache_get(key)
                    if global_hit is not None:
                        self._result_cache[key] = global_hit
                        return global_hit

                # Fetch and populate both caches
                g_event = asyncio.Event()
                _global_inflight[key] = g_event
                try:
                    data = await self._call_tool_raw(tool_name, params)
                    _global_cache_set(key, data)
                    self._result_cache[key] = data
                    return data
                finally:
                    g_event.set()
                    _global_inflight.pop(key, None)

            # Per-request-only cacheable tools (not in _GLOBAL_CACHEABLE)
            if key in self._inflight:
                logger.debug(f"Cache WAIT (request): {tool_name}")
                await self._inflight[key].wait()
                if key in self._result_cache:
                    return self._result_cache[key]

            event = asyncio.Event()
            self._inflight[key] = event
            try:
                data = await self._call_tool_raw(tool_name, params)
                self._result_cache[key] = data
                return data
            finally:
                event.set()
                self._inflight.pop(key, None)

        # Non-cacheable tools: call directly
        return await self._call_tool_raw(tool_name, params)

    async def _call_tool_raw(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the actual MCP tool call (no caching)."""
        logger.info(f"Calling tool: {tool_name} with params: {list(params.keys())}")
        result = await self._client.execute_tool(tool_name, params)

        # MCP server returns {success, data, tool, error}
        # Extract the data for backward compatibility
        if result.get("success") and "data" in result:
            data = result["data"]
            if self.tracker:
                self.tracker.record_tool_call(tool_name, params, data, True)
            return data

        # If failed, raise or return error
        if not result.get("success"):
            error_msg = result.get("error", "Unknown error")
            if self.tracker:
                self.tracker.record_tool_call(tool_name, params, error_msg, False)
            raise ValueError(f"Tool '{tool_name}' failed: {error_msg}")

        if self.tracker:
            self.tracker.record_tool_call(tool_name, params, result, True)
        return result
