"""
OpenAI GPT LLM provider.

Implements OpenAI-specific API calls and format conversions.
Migrated from your existing backend/utils/llm.py
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import openai

from config.settings import settings
from src.utils import get_logger

from .base import BaseLLMProvider

logger = get_logger(__name__)


class OpenAIProvider(BaseLLMProvider):
    """
    OpenAI GPT LLM provider.
    
    Supports GPT-3.5, GPT-4.1, and GPT-4-turbo models with function calling.
    
    Example:
        >>> provider = OpenAIProvider(
        ...     model="gpt-4.1",
        ...     api_key="sk-..."
        ... )
        >>> response = await provider.chat(
        ...     messages=[{"role": "user", "content": "Hello"}]
        ... )
    """
    
    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: Optional[str] = None,
        **kwargs
    ):
        """
        Initialize OpenAI provider.
        
        Args:
            model: OpenAI model name (gpt-4.1, gpt-3.5-turbo, etc.)
            api_key: OpenAI API key
            base_url: Optional base URL (for Azure or proxies)
            **kwargs: Additional options
        """
        super().__init__(model, api_key, **kwargs)
        
        # Initialize OpenAI client
        client_kwargs = {"api_key": api_key}
        
        if base_url:
            client_kwargs["base_url"] = base_url
            logger.info(f"Using custom base URL: {base_url}")
        
        self.client = openai.AsyncOpenAI(**client_kwargs)
        
        logger.info(f"OpenAI provider initialized with model {model}")
    
    async def chat(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Send chat request to OpenAI.
        
        Args:
            messages: Conversation messages
            tools: Available tools (MCP format)
            temperature: Sampling temperature
            max_tokens: Max tokens to generate
            response_format: {"type": "json_object"} for JSON responses
            **kwargs: Additional OpenAI-specific params
        
        Returns:
            Standardized response dict
        """
        # Convert MCP tools to OpenAI format if provided
        openai_tools = None
        if tools:
            openai_tools = [self.format_tool_for_provider(t) for t in tools]
        
        # Build request parameters
        request_params = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        
        if max_tokens:
            request_params["max_tokens"] = max_tokens
        
        if openai_tools:
            request_params["tools"] = openai_tools
        
        if response_format:
            request_params["response_format"] = response_format
        
        # Add any additional kwargs
        request_params.update(kwargs)
        
        try:
            # Call OpenAI API
            response = await self.client.chat.completions.create(**request_params)
            
            # Extract content
            message = response.choices[0].message
            content = message.content or ""
            
            # Extract tool calls if any
            tool_calls = []
            if message.tool_calls:
                for tc in message.tool_calls:
                    tool_calls.append({
                        "id": tc.id,
                        "name": tc.function.name,
                        "arguments": tc.function.arguments  # Note: This is a JSON string
                    })
            
            # Build standardized response
            return {
                "content": content,
                "tool_calls": tool_calls,
                "finish_reason": response.choices[0].finish_reason,
                "usage": {
                    "input_tokens": response.usage.prompt_tokens,
                    "output_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens
                },
                "model": response.model,
                "raw_response": response
            }
        
        except openai.APIError as e:
            logger.error(f"OpenAI API error: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error calling OpenAI: {e}", exc_info=True)
            raise
    
    def supports_tools(self) -> bool:
        """OpenAI supports function calling (their term for tools)"""
        return True
    
    def get_model_name(self) -> str:
        """Get OpenAI model name"""
        return self.model
    
    def format_tool_for_provider(self, tool: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert MCP tool to OpenAI format.
        
        MCP format:
        {
            "name": "tool_name",
            "description": "...",
            "inputSchema": {
                "type": "object",
                "properties": {...}
            }
        }
        
        OpenAI format:
        {
            "type": "function",
            "function": {
                "name": "tool_name",
                "description": "...",
                "parameters": {
                    "type": "object",
                    "properties": {...}
                }
            }
        }
        """
        openai_tool = {
            "type": "function",
            "function": {
                "name": tool.get("name"),
                "description": tool.get("description", "")
            }
        }
        
        # Convert inputSchema to parameters
        if "inputSchema" in tool:
            openai_tool["function"]["parameters"] = tool["inputSchema"]
        elif "input_schema" in tool:
            openai_tool["function"]["parameters"] = tool["input_schema"]
        elif "parameters" in tool:
            openai_tool["function"]["parameters"] = tool["parameters"]
        
        return openai_tool