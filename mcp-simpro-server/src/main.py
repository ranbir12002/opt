"""
MCP Simpro Server - Main Application Entry Point.

This is the FastAPI application that serves as the MCP server.
It provides HTTP endpoints for health checks, metrics, and the main MCP SSE endpoint.
"""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import settings
from src.api.routes import router
from src.utils import get_logger, setup_logging

# Initialize logging
setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    
    Handles startup and shutdown tasks:
    - Startup: Initialize connections, load configurations
    - Shutdown: Cleanup resources, close connections
    """
    # ===================================================================
    # STARTUP
    # ===================================================================
    logger.info("=" * 70)
    logger.info("MCP Simpro Server Starting")
    logger.info("=" * 70)
    logger.info(f"LLM Provider: {settings.LLM_PROVIDER}")
    logger.info(f"LLM Model: {settings.LLM_MODEL}")
    logger.info(f"Simpro Region: {settings.SIMPRO_REGION}")
    logger.info(f"Simpro Company ID: {settings.SIMPRO_COMPANY_ID}")
    logger.info(f"Server: http://{settings.MCP_SERVER_HOST}:{settings.MCP_SERVER_PORT}")
    logger.info(f"Log Level: {settings.LOG_LEVEL}")
    logger.info("=" * 70)
    
    # Load LLM capabilities
    capabilities = settings.get_llm_capabilities()
    logger.info(f"Orchestration strategy: {capabilities.get('strategy')}")
    logger.info(f"Max tokens: {capabilities.get('max_tokens')}")
    
    # Initialize MCP server and register tools
    from src.mcp_core.server import get_mcp_server
    from src.tools.registry import register_all_tools
    from src.mcp_core.protocol import MCPProtocolHandler
    
    logger.info("Registering MCP tools...")
    registry = register_all_tools()
    
    # Get MCP server and register tools from registry
    mcp_server = get_mcp_server()
    for tool in registry.list_tools():
        mcp_server.register_tool(
            name=tool.get_name(),
            description=tool.get_description(),
            input_schema=tool.get_input_schema(),
            handler=tool.execute
        )
    
    logger.info(f"Registered {registry.get_tool_count()} tools with MCP server")
    
    logger.info("Startup complete - Ready to accept connections")
    
    yield  # Application runs here
    
    # ===================================================================
    # SHUTDOWN
    # ===================================================================
    logger.info("=" * 70)
    logger.info("MCP Simpro Server Shutting Down")
    logger.info("=" * 70)
    
    # TODO: Cleanup
    # - Close HTTP clients
    # - Save cache state
    # - Close database connections (if any)
    
    logger.info("Shutdown complete")


# ===================================================================
# Create FastAPI Application
# ===================================================================
app = FastAPI(
    title="MCP Simpro Server",
    description="Model Context Protocol server for Simpro ERP integration",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)


# ===================================================================
# CORS Middleware
# ===================================================================
# Allow Node.js backend to connect
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",  # Node.js backend
        "http://localhost:5173",  # Vite dev server
        "http://localhost:5174",
        "*"  # Vite alternate port
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ===================================================================
# Include Routes
# ===================================================================
app.include_router(router)


# ===================================================================
# Error Handlers
# ===================================================================
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """
    Global exception handler for unhandled errors.
    """
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return {
        "error": "Internal server error",
        "detail": str(exc) if settings.LOG_LEVEL == "DEBUG" else "An error occurred"
    }


# ===================================================================
# Main Entry Point
# ===================================================================
if __name__ == "__main__":
    import uvicorn
    
    # Run with uvicorn
    uvicorn.run(
        "src.main:app",
        host=settings.MCP_SERVER_HOST,
        port=settings.MCP_SERVER_PORT,
        reload=True,  # Hot reload for development
        reload_includes=["*.py"],
        reload_excludes=["logs/*", "venv/*", "__pycache__/*"],
        log_level=settings.LOG_LEVEL.lower(),
        access_log=True
    )