"""
SSE (Server-Sent Events) transport for MCP protocol.

Provides bidirectional communication between Node.js backend and MCP server
using Server-Sent Events.
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncGenerator, Dict, Any

from fastapi import Request
from sse_starlette.sse import EventSourceResponse

from src.utils import get_logger
from .protocol import MCPProtocolHandler

logger = get_logger(__name__)


class SSETransport:
    """
    SSE transport layer for MCP protocol.
    
    Handles bidirectional communication using Server-Sent Events:
    - Server → Client: SSE events
    - Client → Server: POST requests (or event stream with client messages)
    """
    
    def __init__(self, mcp_server):
        """
        Initialize SSE transport.
        
        Args:
            mcp_server: MCPServer instance
        """
        self.protocol_handler = MCPProtocolHandler(mcp_server)
        self.active_connections: Dict[str, Dict[str, Any]] = {}
        logger.info("SSE Transport initialized")
    
    async def handle_sse_connection(
        self,
        request: Request,
        connection_id: str
    ) -> EventSourceResponse:
        """
        Handle SSE connection from client.
        
        Args:
            request: FastAPI request object
            connection_id: Unique connection identifier
        
        Returns:
            EventSourceResponse for streaming events
        """
        logger.info(f"New SSE connection: {connection_id}")
        
        # Store connection
        self.active_connections[connection_id] = {
            "request": request,
            "message_queue": asyncio.Queue()
        }
        
        async def event_generator() -> AsyncGenerator[Dict[str, str], None]:
            """Generate SSE events for this connection"""
            try:
                # Send connection established event
                yield {
                    "event": "connected",
                    "data": json.dumps({
                        "status": "connected",
                        "connection_id": connection_id,
                        "server": "mcp-simpro-server"
                    })
                }
                
                # Keep connection alive and process messages
                while True:
                    # Check if client disconnected
                    if await request.is_disconnected():
                        logger.info(f"Client disconnected: {connection_id}")
                        break
                    
                    # Check message queue (with timeout)
                    try:
                        message = await asyncio.wait_for(
                            self.active_connections[connection_id]["message_queue"].get(),
                            timeout=30.0
                        )
                        
                        # Process message through protocol handler
                        response = await self.protocol_handler.handle_message(message)
                        
                        # Send response as SSE event
                        yield {
                            "event": "message",
                            "data": json.dumps(response)
                        }
                    
                    except asyncio.TimeoutError:
                        # Send keepalive ping
                        yield {
                            "event": "ping",
                            "data": json.dumps({"timestamp": asyncio.get_event_loop().time()})
                        }
            
            except Exception as e:
                logger.error(f"Error in SSE event generator: {e}", exc_info=True)
                yield {
                    "event": "error",
                    "data": json.dumps({"error": str(e)})
                }
            
            finally:
                # Cleanup connection
                if connection_id in self.active_connections:
                    del self.active_connections[connection_id]
                    logger.info(f"Connection cleaned up: {connection_id}")
        
        return EventSourceResponse(event_generator())
    
    async def send_message_to_connection(
        self,
        connection_id: str,
        message: Dict[str, Any]
    ):
        """
        Send a message to a specific connection.
        
        Args:
            connection_id: Connection identifier
            message: Message to send
        """
        if connection_id not in self.active_connections:
            raise ValueError(f"Connection not found: {connection_id}")
        
        await self.active_connections[connection_id]["message_queue"].put(message)
        logger.debug(f"Message queued for connection {connection_id}")
    
    def get_active_connection_count(self) -> int:
        """Get number of active connections"""
        return len(self.active_connections)
    
    def get_connection_ids(self) -> list[str]:
        """Get list of active connection IDs"""
        return list(self.active_connections.keys())


# ===================================================================
# Global SSE transport instance
# ===================================================================
_global_sse_transport = None


def get_sse_transport(mcp_server) -> SSETransport:
    """
    Get or create global SSE transport instance.
    
    Args:
        mcp_server: MCPServer instance
    
    Returns:
        SSETransport instance
    """
    global _global_sse_transport
    
    if _global_sse_transport is None:
        _global_sse_transport = SSETransport(mcp_server)
        logger.info("Global SSE transport created")
    
    return _global_sse_transport