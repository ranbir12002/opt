#mcp-simpro-server/src/llm

"""
Base LLM provider interface.

All LLM providers (Claude, OpenAI, etc.) must implement this interface.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from src.utils import get_logger

logger = get_logger(__name__)


class BaseLLMProvider(ABC):
    """
    Abstract base class for LLM providers.
    
    All LLM providers must implement these methods to ensure
    consistent behavior across different models.
    """
    
    def __init__(
        self,
        model: str,
        api_key: str,
        **kwargs
    ):
        """
        Initialize LLM provider.
        
        Args:
            model: Model name/ID
            api_key: API key for authentication
            **kwargs: Provider-specific options
        """
        self.model = model
        self.api_key = api_key
        self.kwargs = kwargs
        
        logger.info(f"Initialized {self.__class__.__name__} with model {model}")
    
    @abstractmethod
    async def chat(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Send chat completion request to LLM.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            tools: Optional list of tools/functions available to LLM
            temperature: Sampling temperature (0-1)
            max_tokens: Maximum tokens to generate
            **kwargs: Provider-specific parameters
        
        Returns:
            Response dict with at minimum:
            {
                "content": str,  # Text response
                "tool_calls": List[Dict],  # Tool calls if any
                "finish_reason": str,  # Why generation stopped
                "usage": Dict  # Token usage stats
            }
        """
        pass
    
    @abstractmethod
    def supports_tools(self) -> bool:
        """
        Check if this provider supports tool/function calling.
        
        Returns:
            True if provider supports tools, False otherwise
        """
        pass
    
    @abstractmethod
    def get_model_name(self) -> str:
        """
        Get the model name/ID.
        
        Returns:
            Model name string
        """
        pass
    
    def get_provider_name(self) -> str:
        """
        Get the provider name.
        
        Returns:
            Provider name (e.g., "claude", "openai")
        """
        return self.__class__.__name__.replace("Provider", "").lower()
    
    def format_tool_for_provider(self, tool: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert MCP tool format to provider-specific format.
        
        MCP tools come in a standard format, but each provider
        (Claude, OpenAI, etc.) expects slightly different structures.
        
        Args:
            tool: MCP tool definition
        
        Returns:
            Provider-specific tool definition
        """
        # Default: return as-is
        # Subclasses should override if needed
        return tool
    
    def parse_tool_calls(self, response: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extract tool calls from provider response.
        
        Args:
            response: Raw provider response
        
        Returns:
            List of tool call dicts with:
            {
                "name": str,  # Tool name
                "arguments": Dict,  # Tool arguments
                "id": str  # Call ID for tracking
            }
        """
        # Default: empty list
        # Subclasses should override
        return response.get("tool_calls", [])