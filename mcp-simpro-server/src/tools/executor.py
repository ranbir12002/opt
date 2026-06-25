#mcp-simpro-server/src/tools
"""
Tool executor for executing MCP tools with proper error handling.

NOW WITH PRESENTATION: Tool results are formatted before being sent to LLM.
"""
from __future__ import annotations

import os
from typing import Any, Dict

from src.utils import get_logger

from .registry import get_tool_registry

logger = get_logger(__name__)

# Check if presenter is enabled
ENABLE_PRESENTER = os.getenv("ENABLE_PRESENTER", "true").lower() == "true"

# Import presenter if enabled
if ENABLE_PRESENTER:
    try:
        from src.presentation.simpro_presenter import format_simpro_data
        logger.info("Presenter enabled - tool results will be pre-formatted")
    except ImportError:
        logger.warning("Presenter import failed - falling back to raw data")
        ENABLE_PRESENTER = False


class ToolExecutor:
    """
    Executes tools with error handling and logging.
    
    Provides a safe wrapper around tool execution with:
    - Input validation
    - Error handling
    - Execution logging
    - Result formatting (optional presenter)
    """
    
    def __init__(self):
        """Initialize tool executor"""
        self.registry = get_tool_registry()
        self.enable_presenter = ENABLE_PRESENTER
        logger.debug(f"Tool executor initialized (presenter: {self.enable_presenter})")
    
    async def execute(
        self,
        tool_name: str,
        arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute a tool by name.
        
        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments
        
        Returns:
            Tool execution result with metadata
        
        Example:
            >>> executor = ToolExecutor()
            >>> result = await executor.execute(
            ...     "search_jobs",
            ...     {"status": "Active"}
            ... )
            >>> print(result)
            {
                "success": True,
                "data": {"jobs": [...]},
                "tool": "search_jobs",
                "error": None,
                "formatted": "## Job Search Results..."  # If presenter enabled
            }
        """
        logger.info(f"Executing tool: {tool_name} with args: {arguments}")
        
        # Check if tool exists
        tool = self.registry.get(tool_name)
        if not tool:
            error_msg = f"Tool not found: {tool_name}"
            logger.error(error_msg)
            return {
                "success": False,
                "data": None,
                "tool": tool_name,
                "error": error_msg
            }
        
        # Execute tool with error handling
        try:
            # Validate arguments
            tool.validate_arguments(arguments)
            
            # Execute tool
            result = await tool.execute(arguments)
            
            logger.info(f"Tool {tool_name} executed successfully")
            
            # Build success response
            response = {
                "success": True,
                "data": result,
                "tool": tool_name,
                "error": None
            }
            
            # ✅ NEW: Apply presenter if enabled
            if self.enable_presenter:
                try:
                    formatted = format_simpro_data(tool_name, response)
                    response["formatted"] = formatted
                    logger.debug(f"Presenter formatted {len(formatted)} chars")
                except Exception as e:
                    logger.warning(f"Presenter failed: {e}, returning raw data")
                    # Don't fail - just return without formatting
            
            return response
        
        except ValueError as e:
            # Validation error
            error_msg = f"Validation error: {str(e)}"
            logger.error(f"Tool {tool_name} validation failed: {e}")
            return {
                "success": False,
                "data": None,
                "tool": tool_name,
                "error": error_msg
            }
        
        except Exception as e:
            # Execution error
            error_msg = f"Execution error: {str(e)}"
            logger.error(f"Tool {tool_name} execution failed: {e}", exc_info=True)
            return {
                "success": False,
                "data": None,
                "tool": tool_name,
                "error": error_msg
            }
    
    async def execute_batch(
        self,
        tool_calls: list[Dict[str, Any]]
    ) -> list[Dict[str, Any]]:
        """
        Execute multiple tools in sequence.
        
        Args:
            tool_calls: List of tool call dicts with 'name' and 'arguments'
        
        Returns:
            List of execution results
        
        Example:
            >>> executor = ToolExecutor()
            >>> results = await executor.execute_batch([
            ...     {"name": "search_customers", "arguments": {"name": "ABC"}},
            ...     {"name": "search_jobs", "arguments": {"customer_id": 123}}
            ... ])
        """
        results = []
        
        for tool_call in tool_calls:
            tool_name = tool_call.get("name")
            arguments = tool_call.get("arguments", {})
            
            result = await self.execute(tool_name, arguments)
            results.append(result)
        
        return results
    
    def list_available_tools(self) -> list[str]:
        """
        Get list of available tool names.
        
        Returns:
            List of tool names
        """
        return self.registry.list_tool_names()
    
    def get_tool_info(self, tool_name: str) -> Dict[str, Any] | None:
        """
        Get information about a tool.
        
        Args:
            tool_name: Name of the tool
        
        Returns:
            Tool info dict or None if not found
        """
        tool = self.registry.get(tool_name)
        if not tool:
            return None
        
        return tool.to_dict()


# ===================================================================
# Global executor instance
# ===================================================================
_global_executor: ToolExecutor | None = None


def get_tool_executor() -> ToolExecutor:
    """
    Get or create global tool executor.
    
    Returns:
        ToolExecutor instance
    """
    global _global_executor
    
    if _global_executor is None:
        _global_executor = ToolExecutor()
        logger.info("Global tool executor created")
    
    return _global_executor