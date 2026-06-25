"""
MCP Server implementation.

This is the core MCP server that:
1. Exposes tools to clients (Node.js backend)
2. Handles tool execution requests
3. Returns results in MCP format
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from mcp.server import Server
#from src.mcp_core.models import InitializationOptions
from mcp.types import Tool, TextContent

from config import settings
from src.utils import get_logger

logger = get_logger(__name__)


class MCPServer:
    """
    MCP Server for Simpro tools.
    
    This server exposes Simpro API operations as MCP tools that can be
    called by LLM clients (like Claude via your Node.js backend).
    
    Architecture:
        Node.js Backend → SSE Connection → MCP Server → Simpro API
    """
    
    def __init__(self):
        """Initialize MCP server"""
        self.server = Server("mcp-simpro-server")
        self.tools: List[Tool] = []
        self.tool_handlers: Dict[str, Any] = {}
        
        logger.info("MCP Server initialized")
    
    def register_tool(
        self,
        name: str,
        description: str,
        input_schema: Dict[str, Any],
        handler: Any
    ):
        """
        Register a tool with the MCP server.
        
        Args:
            name: Tool name (e.g., "search_jobs")
            description: Tool description for LLM
            input_schema: JSON schema for tool inputs
            handler: Async function to execute the tool
        
        Example:
            >>> async def search_jobs_handler(arguments):
            ...     # Call Simpro API
            ...     return {"jobs": [...]}
            
            >>> server.register_tool(
            ...     name="search_jobs",
            ...     description="Search for jobs in Simpro",
            ...     input_schema={
            ...         "type": "object",
            ...         "properties": {
            ...             "status": {"type": "string"}
            ...         }
            ...     },
            ...     handler=search_jobs_handler
            ... )
        """
        # Create MCP Tool
        tool = Tool(
            name=name,
            description=description,
            inputSchema=input_schema
        )
        
        self.tools.append(tool)
        self.tool_handlers[name] = handler
        
        logger.info(f"Registered tool: {name}")
    
    def get_tools(self) -> List[Tool]:
        """
        Get list of available tools.
        
        Returns:
            List of MCP Tool objects
        """
        return self.tools
    
    async def execute_tool(
        self,
        name: str,
        arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute a tool by name.
        
        Args:
            name: Tool name
            arguments: Tool arguments
        
        Returns:
            Tool execution result
        
        Raises:
            ValueError: If tool not found
        """
        if name not in self.tool_handlers:
            raise ValueError(f"Tool not found: {name}")
        
        handler = self.tool_handlers[name]
        
        logger.info(f"Executing tool: {name} with arguments: {arguments}")
        
        try:
            result = await handler(arguments)
            logger.debug(f"Tool {name} executed successfully")
            return result
        except Exception as e:
            logger.error(f"Error executing tool {name}: {e}", exc_info=True)
            raise
    
    def setup_handlers(self):
        """
        Setup MCP protocol handlers.
        
        This configures the server to respond to MCP protocol requests:
        - tools/list: Return available tools
        - tools/call: Execute a tool
        """
        
        @self.server.list_tools()
        async def handle_list_tools() -> List[Tool]:
            """
            Handle tools/list request.
            
            Returns list of available tools to the client.
            """
            logger.debug(f"Listing {len(self.tools)} tools")
            return self.tools
        
        @self.server.call_tool()
        async def handle_call_tool(
            name: str,
            arguments: Dict[str, Any]
        ) -> List[TextContent]:
            """
            Handle tools/call request.
            
            Executes the requested tool and returns results.
            """
            logger.info(f"Tool call request: {name}")
            
            try:
                # Execute tool
                result = await self.execute_tool(name, arguments)
                
                # Convert result to MCP TextContent
                # MCP expects a list of content blocks
                import json
                result_text = json.dumps(result, indent=2)
                
                return [
                    TextContent(
                        type="text",
                        text=result_text
                    )
                ]
            
            except ValueError as e:
                # Tool not found
                logger.error(f"Tool not found: {name}")
                return [
                    TextContent(
                        type="text",
                        text=json.dumps({
                            "error": "Tool not found",
                            "tool": name,
                            "message": str(e)
                        })
                    )
                ]
            
            except Exception as e:
                # Tool execution error
                logger.error(f"Tool execution error: {e}", exc_info=True)
                return [
                    TextContent(
                        type="text",
                        text=json.dumps({
                            "error": "Tool execution failed",
                            "tool": name,
                            "message": str(e)
                        })
                    )
                ]
    
    def get_server(self) -> Server:
        """
        Get the underlying MCP Server instance.
        
        Returns:
            MCP Server instance
        """
        return self.server


# ===================================================================
# Global MCP server instance
# ===================================================================
_global_mcp_server: Optional[MCPServer] = None


def get_mcp_server() -> MCPServer:
    """
    Get or create global MCP server instance.
    
    Returns:
        MCPServer instance
    """
    global _global_mcp_server
    
    if _global_mcp_server is None:
        _global_mcp_server = MCPServer()
        _global_mcp_server.setup_handlers()
        logger.info("Global MCP server created")
    
    return _global_mcp_server