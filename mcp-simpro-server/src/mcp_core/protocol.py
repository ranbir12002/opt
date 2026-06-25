"""
MCP Protocol handlers.

Handles MCP protocol messages and routing.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from src.utils import get_logger

logger = get_logger(__name__)


class MCPProtocolHandler:
    """
    Handles MCP protocol message parsing and routing.
    
    MCP uses JSON-RPC 2.0 format for messages:
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {}
    }
    """
    
    def __init__(self, mcp_server):
        """
        Initialize protocol handler.
        
        Args:
            mcp_server: MCPServer instance
        """
        self.mcp_server = mcp_server
        logger.info("MCP Protocol handler initialized")
    
    async def handle_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle incoming MCP protocol message.
        
        Args:
            message: JSON-RPC message dict
        
        Returns:
            JSON-RPC response dict
        """
        # Validate JSON-RPC format
        if "jsonrpc" not in message or message["jsonrpc"] != "2.0":
            return self._error_response(
                message.get("id"),
                -32600,  # Invalid Request
                "Invalid JSON-RPC version"
            )
        
        message_id = message.get("id")
        method = message.get("method")
        params = message.get("params", {})
        
        logger.debug(f"Handling MCP message: {method}")
        
        try:
            # Route to appropriate handler
            if method == "initialize":
                result = await self._handle_initialize(params)
            
            elif method == "tools/list":
                result = await self._handle_list_tools(params)
            
            elif method == "tools/call":
                result = await self._handle_call_tool(params)
            
            elif method == "ping":
                result = {"status": "ok"}
            
            else:
                return self._error_response(
                    message_id,
                    -32601,  # Method not found
                    f"Method not found: {method}"
                )
            
            # Success response
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": result
            }
        
        except Exception as e:
            logger.error(f"Error handling message: {e}", exc_info=True)
            return self._error_response(
                message_id,
                -32603,  # Internal error
                str(e)
            )
    
    async def _handle_initialize(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle initialize request.
        
        Client sends this first to establish connection.
        """
        logger.info("MCP connection initialized")
        
        return {
            "protocolVersion": "2024-11-05",
            "serverInfo": {
                "name": "mcp-simpro-server",
                "version": "1.0.0"
            },
            "capabilities": {
                "tools": {}
            }
        }
    
    async def _handle_list_tools(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle tools/list request.
        
        Returns list of available tools.
        """
        tools = self.mcp_server.get_tools()
        
        # Convert Tool objects to dict format
        tools_dict = []
        for tool in tools:
            tools_dict.append({
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.inputSchema
            })
        
        logger.debug(f"Returning {len(tools_dict)} tools")
        
        return {
            "tools": tools_dict
        }
    
    async def _handle_call_tool(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle tools/call request.
        
        Executes the requested tool and returns result.
        """
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        
        if not tool_name:
            raise ValueError("Tool name is required")
        
        logger.info(f"Calling tool: {tool_name}")
        
        # Execute tool
        result = await self.mcp_server.execute_tool(tool_name, arguments)
        
        # Return as text content
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result, indent=2)
                }
            ]
        }
    
    def _error_response(
        self,
        message_id: Optional[int],
        code: int,
        message: str
    ) -> Dict[str, Any]:
        """
        Create JSON-RPC error response.
        
        Args:
            message_id: Original message ID
            code: Error code
            message: Error message
        
        Returns:
            JSON-RPC error response
        """
        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "error": {
                "code": code,
                "message": message
            }
        }