"""
HTTP routes for MCP Simpro Server.

Provides:
- Health check endpoint
- MCP SSE endpoint for client connections
- Tools listing endpoint (for debugging)
- Metrics endpoint (for monitoring)
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from config.settings import settings
from src.utils import get_logger

logger = get_logger(__name__)

# Create API router
router = APIRouter()


# ===================================================================
# Health Check
# ===================================================================
@router.get("/health", tags=["System"])
async def health_check() -> dict[str, Any]:
    """
    Health check endpoint.
    
    Returns server status and basic information.
    Used by load balancers and monitoring systems.
    """
    return {
        "status": "healthy",
        "service": "mcp-simpro-server",
        "version": "1.0.0",
        "llm_provider": settings.LLM_PROVIDER,
        "llm_model": settings.LLM_MODEL,
        "timestamp": time.time()
    }


# ===================================================================
# MCP SSE Endpoint
# ===================================================================
@router.get("/mcp/sse", tags=["MCP"])
async def mcp_sse_endpoint(request: Request) -> EventSourceResponse:
    """
    MCP Server-Sent Events endpoint.
    
    This is the main endpoint where Node.js backend connects to establish
    an MCP connection. Uses Server-Sent Events for bidirectional communication.
    
    The MCP server handles:
    - Tool discovery
    - Tool execution
    - Response streaming
    
    Example client connection:
        const eventSource = new EventSource('http://localhost:8000/mcp/sse');
    """
    from src.mcp_core.server import get_mcp_server
    from src.mcp_core.sse_transport import get_sse_transport
    import uuid
    
    logger.info("MCP SSE connection request received")
    
    # Get MCP server and SSE transport
    mcp_server = get_mcp_server()
    sse_transport = get_sse_transport(mcp_server)
    
    # Generate unique connection ID
    connection_id = str(uuid.uuid4())
    
    # Handle SSE connection
    return await sse_transport.handle_sse_connection(request, connection_id)


# ===================================================================
# Tools Listing (Debug)
# ===================================================================
@router.get("/api/tools", tags=["Debug"])
async def list_tools() -> dict[str, Any]:
    """
    List all available MCP tools.
    
    Useful for debugging and documentation.
    Shows what tools the LLM can call.
    """
    from src.mcp_core.server import get_mcp_server
    
    logger.debug("Tools listing requested")
    
    # Get MCP server
    mcp_server = get_mcp_server()
    tools = mcp_server.get_tools()
    
    # Convert to dict format
    tools_list = []
    for tool in tools:
        tools_list.append({
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.inputSchema
        })
    
    return {
        "tools": tools_list,
        "total": len(tools_list)
    }


@router.post("/api/execute-tool", tags=["Tools"])
async def execute_tool(request: dict[str, Any], http_request: Request) -> dict[str, Any]:
    """
    Execute a specific tool by name.

    Supports per-tenant credentials via headers:
      x-simpro-token: <org access token>
      x-simpro-url:   <org Simpro base URL>
      x-simpro-company-id: <org company ID> (optional)

    If headers are absent, falls back to server .env credentials (dev/single-tenant mode).

    Request body:
    {
        "tool_name": "search_jobs",
        "arguments": {"status": "Active", "page": 1}
    }
    """
    from src.tools.executor import get_tool_executor
    from src.simpro.client import set_request_credentials

    tool_name = request.get("tool_name")
    arguments = request.get("arguments", {})

    if not tool_name:
        return {
            "success": False,
            "error": "tool_name is required",
            "data": None,
            "tool": None,
        }

    # Inject per-tenant credentials if provided by the backend
    token = http_request.headers.get("x-simpro-token")
    url = http_request.headers.get("x-simpro-url")
    raw_company_id = http_request.headers.get("x-simpro-company-id")
    company_id = int(raw_company_id) if raw_company_id and raw_company_id.isdigit() else None
    if token and url:
        set_request_credentials(token=token, base_url=url, company_id=company_id)
        logger.info(f"Using per-tenant credentials for {url} (company {company_id})")

    logger.info(f"Tool execution request: {tool_name} with args: {arguments}")

    try:
        executor = get_tool_executor()
        result = await executor.execute(tool_name, arguments)
        logger.debug(f"Tool execution result: {result.get('success')}")
        return result

    except Exception as e:
        logger.error(f"Tool execution error: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "data": None,
            "tool": tool_name,
        }


# ===================================================================
# Metrics (Monitoring)
# ===================================================================
@router.get("/metrics", tags=["System"])
async def metrics() -> JSONResponse:
    """
    Prometheus-compatible metrics endpoint.
    
    Returns metrics in Prometheus text format for monitoring.
    Includes:
    - Request counts
    - Response times
    - Cache statistics
    - Error rates
    
    NOTE: Full metrics implementation will be added later.
    """
    if not settings.ENABLE_METRICS:
        return JSONResponse(
            content={"error": "Metrics disabled"},
            status_code=404
        )
    
    # Placeholder - will be replaced with actual Prometheus metrics
    metrics_text = """
# HELP mcp_server_info Server information
# TYPE mcp_server_info gauge
mcp_server_info{version="1.0.0",llm_provider="%s",llm_model="%s"} 1

# HELP mcp_requests_total Total number of requests
# TYPE mcp_requests_total counter
mcp_requests_total 0

# HELP mcp_cache_size Current cache size
# TYPE mcp_cache_size gauge
mcp_cache_size 0
""" % (settings.LLM_PROVIDER, settings.LLM_MODEL)
    
    return JSONResponse(
        content=metrics_text,
        media_type="text/plain"
    )


# ===================================================================
# Root Endpoint
# ===================================================================
@router.get("/", tags=["System"])
async def root() -> dict[str, Any]:
    """
    Root endpoint with API information.
    """
    return {
        "service": "MCP Simpro Server",
        "version": "1.0.0",
        "status": "running",
        "endpoints": {
            "health": "/health",
            "mcp_sse": "/mcp/sse",
            "tools": "/api/tools",
            "metrics": "/metrics",
            "docs": "/docs"
        },
        "llm": {
            "provider": settings.LLM_PROVIDER,
            "model": settings.LLM_MODEL
        }
    }


# Import asyncio at module level for event_generator
import asyncio