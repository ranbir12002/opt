"""
Anthropic Claude LLM provider.

Implements Claude-specific API calls and format conversions.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import anthropic

from config.settings import settings
from src.utils import get_logger

from .base import BaseLLMProvider

logger = get_logger(__name__)


class ClaudeProvider(BaseLLMProvider):
    """
    Anthropic Claude LLM provider.
    
    Supports Claude 3 and Claude 4 models with native tool calling.
    
    Example:
        >>> provider = ClaudeProvider(
        ...     model="claude-sonnet-4-20250514",
        ...     api_key="sk-ant-..."
        ... )
        >>> response = await provider.chat(
        ...     messages=[{"role": "user", "content": "Hello"}]
        ... )
    """
    
    def __init__(
        self,
        model: str,
        api_key: str,
        **kwargs
    ):
        """
        Initialize Claude provider.
        
        Args:
            model: Claude model name
            api_key: Anthropic API key
            **kwargs: Additional options
        """
        super().__init__(model, api_key, **kwargs)
        
        # Initialize Anthropic client
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        
        logger.info(f"Claude provider initialized with model {model}")
    
    async def chat(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Send chat request to Claude.
        
        Args:
            messages: Conversation messages
            tools: Available tools (MCP format)
            temperature: Sampling temperature
            max_tokens: Max tokens to generate
            **kwargs: Additional Claude-specific params
        
        Returns:
            Standardized response dict
        """
        # Convert MCP tools to Claude format if provided
        claude_tools = None
        if tools:
            claude_tools = [self.format_tool_for_provider(t) for t in tools]
        
        # Set default max_tokens if not provided
        if max_tokens is None:
            max_tokens = 4096
        
        try:
            # Call Claude API
            response = await self.client.messages.create(
                model=self.model,
                messages=messages,
                tools=claude_tools,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs
            )
            
            # Extract content and tool calls
            content_blocks = response.content
            text_content = ""
            tool_calls = []
            
            for block in content_blocks:
                if block.type == "text":
                    text_content += block.text
                elif block.type == "tool_use":
                    tool_calls.append({
                        "id": block.id,
                        "name": block.name,
                        "arguments": block.input
                    })
            
            # Build standardized response
            return {
                "content": text_content,
                "tool_calls": tool_calls,
                "finish_reason": response.stop_reason,
                "usage": {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                    "total_tokens": response.usage.input_tokens + response.usage.output_tokens
                },
                "model": response.model,
                "raw_response": response
            }
        
        except anthropic.APIError as e:
            logger.error(f"Claude API error: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error calling Claude: {e}", exc_info=True)
            raise
    
    def supports_tools(self) -> bool:
        """Claude supports native tool calling"""
        return True
    
    def get_model_name(self) -> str:
        """Get Claude model name"""
        return self.model
    
    def format_tool_for_provider(self, tool: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert MCP tool to Claude format.
        
        MCP format:
        {
            "name": "tool_name",
            "description": "...",
            "inputSchema": {
                "type": "object",
                "properties": {...}
            }
        }
        
        Claude format:
        {
            "name": "tool_name",
            "description": "...",
            "input_schema": {  # Note: snake_case
                "type": "object",
                "properties": {...}
            }
        }
        """
        claude_tool = {
            "name": tool.get("name"),
            "description": tool.get("description", "")
        }
        
        # Convert inputSchema to input_schema (Claude uses snake_case)
        if "inputSchema" in tool:
            claude_tool["input_schema"] = tool["inputSchema"]
        elif "input_schema" in tool:
            claude_tool["input_schema"] = tool["input_schema"]
        
        return claude_tool