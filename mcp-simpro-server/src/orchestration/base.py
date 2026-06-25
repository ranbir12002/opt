#mcp-simpro-server/src/orchestration

"""
Base orchestrator class.

All orchestration strategies inherit from this.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from src.utils import get_logger

logger = get_logger(__name__)


class BaseOrchestrator(ABC):
    """
    Abstract base class for orchestration strategies.
    
    Different LLMs have different capabilities for orchestrating
    multi-step tool calls. This class provides a unified interface
    for all orchestration strategies.
    """
    
    def __init__(
        self,
        llm_provider: Optional[Any] = None,
        tools: Optional[List[Dict[str, Any]]] = None
    ):
        """
        Initialize orchestrator.
        
        Args:
            llm_provider: LLM provider instance (optional)
            tools: List of available tools
        """
        self.llm_provider = llm_provider
        self.tools = tools or []
        
        logger.info(f"Initialized {self.__class__.__name__}")
    
    @abstractmethod
    async def orchestrate(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Orchestrate the execution of a user query.
        
        Args:
            query: User query/request
            context: Optional context (conversation history, etc.)
        
        Returns:
            Orchestration result with tool calls and responses
        
        Example result:
            {
                "success": True,
                "tool_calls": [
                    {"tool": "search_customers", "arguments": {...}, "result": {...}},
                    {"tool": "search_jobs", "arguments": {...}, "result": {...}}
                ],
                "response": "Found 3 jobs for customer ABC",
                "strategy": "llm_native"
            }
        """
        pass
    
    @abstractmethod
    def get_strategy_name(self) -> str:
        """
        Get the name of this orchestration strategy.
        
        Returns:
            Strategy name (e.g., "llm_native", "assisted", "manual")
        """
        pass
    
    def set_tools(self, tools: List[Dict[str, Any]]):
        """
        Set available tools.
        
        Args:
            tools: List of tool definitions
        """
        self.tools = tools
        logger.debug(f"Set {len(tools)} tools for orchestrator")
    
    def get_tool_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Get tool definition by name.
        
        Args:
            name: Tool name
        
        Returns:
            Tool definition or None
        """
        for tool in self.tools:
            if tool.get("name") == name:
                return tool
        return None