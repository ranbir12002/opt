# Chatbox_mcp/backend/utils/mcp_tool_client.py
"""
Shared HTTP client for calling MCP Server tools.

All agents and executors use this to call tools via the MCP Server HTTP API
instead of importing tool classes directly.

Endpoints used:
- GET  /api/tools         → discover all registered tools
- POST /api/execute-tool  → execute a specific tool
"""
from __future__ import annotations

import os
import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8000")


class MCPToolClient:
    """
    HTTP client for calling tools on the MCP Server.

    Usage:
        client = MCPToolClient()
        tools = await client.list_tools()
        result = await client.execute_tool("search_jobs", {"page": 1})

    For multi-tenant use, pass simpro_token + simpro_url so the MCP server
    uses that tenant's Simpro credentials instead of its own .env:
        client = MCPToolClient(
            simpro_token="abc123",
            simpro_url="https://tenant.simprosuite.com/api",
            simpro_company_id=5,
        )
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: float = 60.0,
        simpro_token: Optional[str] = None,
        simpro_url: Optional[str] = None,
        simpro_company_id: Optional[int] = None,
    ):
        self.base_url = (base_url or MCP_SERVER_URL).rstrip("/")
        self.timeout = timeout
        self._tools_cache: Optional[List[Dict[str, Any]]] = None
        self._http_client: Optional[httpx.AsyncClient] = None
        # Per-tenant Simpro credentials forwarded as headers to the MCP server
        self._simpro_token = simpro_token
        self._simpro_url = simpro_url
        self._simpro_company_id = simpro_company_id
        logger.info(f"MCPToolClient initialized → {self.base_url}")

    async def _get_http_client(self) -> httpx.AsyncClient:
        """Lazily create and return a persistent, pooled HTTP client."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=self.timeout,
                limits=httpx.Limits(
                    max_connections=20,
                    max_keepalive_connections=10,
                ),
            )
        return self._http_client

    async def close(self) -> None:
        """Close the persistent HTTP client (call on app shutdown)."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    async def list_tools(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Fetch all available tools from the MCP server.

        Returns list of {name, description, inputSchema}.
        Cached after first call unless force_refresh=True.
        """
        if self._tools_cache is not None and not force_refresh:
            return self._tools_cache

        client = await self._get_http_client()
        resp = await client.get(f"{self.base_url}/api/tools")
        resp.raise_for_status()
        data = resp.json()

        self._tools_cache = data.get("tools", [])
        logger.info(f"Discovered {len(self._tools_cache)} tools from MCP server")
        return self._tools_cache

    async def get_tool_names(self) -> List[str]:
        """Return list of all available tool names."""
        tools = await self.list_tools()
        return [t["name"] for t in tools]

    async def get_tool_descriptions(self) -> Dict[str, str]:
        """Return {tool_name: first_line_of_description} for all tools."""
        tools = await self.list_tools()
        result = {}
        for t in tools:
            desc = (t.get("description") or t["name"]).strip()
            result[t["name"]] = desc.split("\n")[0]
        return result

    async def get_tool_catalog(self) -> Dict[str, Dict[str, Any]]:
        """
        Return rich tool catalog for crossroads resolution context.

        Returns {tool_name: {description, required_params, optional_params}}
        so the LLM can reason about which tools to use and what params they need.
        """
        tools = await self.list_tools()
        catalog = {}
        for t in tools:
            schema = t.get("inputSchema", {})
            props = schema.get("properties", {})
            required = set(schema.get("required", []))

            required_params = {}
            optional_params = {}
            for pname, pdef in props.items():
                param_info = pdef.get("description", pname)
                if pname in required:
                    required_params[pname] = param_info
                else:
                    optional_params[pname] = param_info

            catalog[t["name"]] = {
                "description": (t.get("description") or t["name"]).strip(),
                "required_params": required_params,
                "optional_params": optional_params,
            }
        return catalog

    def _credential_headers(self) -> Dict[str, str]:
        """Build per-tenant credential headers to forward to the MCP server."""
        headers: Dict[str, str] = {}
        if self._simpro_token and self._simpro_url:
            headers["x-simpro-token"] = self._simpro_token
            headers["x-simpro-url"] = self._simpro_url
            if self._simpro_company_id is not None:
                headers["x-simpro-company-id"] = str(self._simpro_company_id)
        return headers

    async def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a tool on the MCP server.

        Args:
            tool_name: e.g. "search_jobs", "list_employees"
            arguments: tool parameters

        Returns:
            {success: bool, data: ..., tool: str, error: str|None}
        """
        logger.info(f"🔧 MCPToolClient → {tool_name} (args: {list(arguments.keys())})")

        client = await self._get_http_client()
        resp = await client.post(
            f"{self.base_url}/api/execute-tool",
            json={"tool_name": tool_name, "arguments": arguments},
            headers=self._credential_headers(),
        )
        resp.raise_for_status()
        result = resp.json()

        if result.get("success"):
            logger.info(f"✅ {tool_name} succeeded")
        else:
            logger.warning(f"❌ {tool_name} failed: {result.get('error')}")

        return result


# Global singleton (lazy)
_client: Optional[MCPToolClient] = None


def get_mcp_tool_client() -> MCPToolClient:
    """Get or create global MCPToolClient instance."""
    global _client
    if _client is None:
        _client = MCPToolClient()
    return _client
