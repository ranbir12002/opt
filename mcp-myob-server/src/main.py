"""
MyOB AccountRight MCP Server.

Uses the MCP Python SDK's low-level Server API with Streamable HTTP transport.
This approach gives full control over tool input schemas while using the standard
MCP protocol (JSON-RPC, session management, Streamable HTTP).

Run:
    cd mcp-myob-server && python src/main.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure project root is on sys.path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import uvicorn
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.streamable_http import StreamableHTTPServerTransport
from mcp.types import Tool, TextContent
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from config.settings import settings
from src.utils import get_logger, setup_logging
from src.tools.registry import register_all_tools

# Setup logging
setup_logging()
logger = get_logger(__name__)

# ── Create MCP Server ─────────────────────────────────────────────────
mcp_server = Server("mcp-myob-server")

# Tool storage
_mcp_tools: list[Tool] = []
_tool_handlers: dict[str, callable] = {}


def _register_tools() -> int:
    """Register all MyOB tools with the MCP server."""
    registry = register_all_tools()

    for tool in registry.list_tools():
        tool_name = tool.get_name()
        tool_desc = tool.get_description()
        tool_schema = tool.get_input_schema()

        # Create MCP Tool object with our explicit schema
        mcp_tool = Tool(
            name=tool_name,
            description=tool_desc,
            inputSchema=tool_schema,
        )
        _mcp_tools.append(mcp_tool)
        _tool_handlers[tool_name] = tool.execute

        logger.debug(f"Registered tool: {tool_name}")

    logger.info(f"Registered {len(_mcp_tools)} tools with MCP server")
    return len(_mcp_tools)


# ── MCP Protocol Handlers ─────────────────────────────────────────────

@mcp_server.list_tools()
async def handle_list_tools() -> list[Tool]:
    """Return all available MyOB tools."""
    return _mcp_tools


@mcp_server.call_tool()
async def handle_call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Execute a MyOB tool and return results."""
    if name not in _tool_handlers:
        return [TextContent(
            type="text",
            text=json.dumps({"error": f"Tool not found: {name}"})
        )]

    try:
        handler = _tool_handlers[name]
        result = await handler(arguments)
        return [TextContent(
            type="text",
            text=json.dumps(result, indent=2, default=str)
        )]
    except Exception as e:
        logger.error(f"Tool {name} execution error: {e}", exc_info=True)
        return [TextContent(
            type="text",
            text=json.dumps({"error": str(e), "tool": name})
        )]


# ── HTTP App with Streamable HTTP Transport ──────────────────────────

session_manager = StreamableHTTPSessionManager(
    app=mcp_server,
    json_response=False,   # Use SSE streaming for responses
    stateless=False,        # Maintain sessions
)


async def handle_mcp(request):
    """Handle MCP protocol requests (Streamable HTTP)."""
    return await session_manager.handle_request(request)


async def handle_health(request):
    """Health check endpoint."""
    return JSONResponse({
        "status": "healthy",
        "service": "mcp-myob-server",
        "version": "1.0.0",
        "tools": len(_mcp_tools),
    })


async def handle_tools_list(request):
    """Debug endpoint: list all registered tools."""
    tools = [{"name": t.name, "description": t.description[:100]} for t in _mcp_tools]
    return JSONResponse({"tools": tools, "total": len(tools)})


# Starlette app with routes
app = Starlette(
    routes=[
        Route("/mcp", handle_mcp, methods=["GET", "POST", "DELETE"]),
        Route("/health", handle_health, methods=["GET"]),
        Route("/api/tools", handle_tools_list, methods=["GET"]),
    ],
)


# Register tools at module load
try:
    tool_count = _register_tools()
except Exception as e:
    logger.error(f"Failed to register tools: {e}", exc_info=True)
    tool_count = 0


# ── Main entry point ─────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info(
        f"Starting MCP MyOB Server on {settings.MCP_SERVER_HOST}:{settings.MCP_SERVER_PORT} "
        f"with {tool_count} tools (Streamable HTTP transport)"
    )

    uvicorn.run(
        app,
        host=settings.MCP_SERVER_HOST,
        port=settings.MCP_SERVER_PORT,
        log_level=settings.LOG_LEVEL.lower(),
    )
