#mcp-simpro-server/src/llm

"""
LLM provider factory.

Creates LLM provider instances based on configuration.
"""
from __future__ import annotations

from typing import Optional

from config.settings import settings
from src.utils import get_logger

from .base import BaseLLMProvider
from .claude_provider import ClaudeProvider
from .openai_provider import OpenAIProvider

logger = get_logger(__name__)


# Global provider instance (lazy initialized)
_global_provider: Optional[BaseLLMProvider] = None


def create_llm_provider(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    **kwargs
) -> BaseLLMProvider:
    """
    Create an LLM provider instance.
    
    Args:
        provider: Provider name ("claude", "openai", "azure", "custom")
                 Uses settings.LLM_PROVIDER if None
        model: Model name/ID (uses settings.LLM_MODEL if None)
        api_key: API key (uses settings.LLM_API_KEY if None)
        **kwargs: Provider-specific options
    
    Returns:
        LLM provider instance
    
    Raises:
        ValueError: If provider is not supported or configuration is invalid
    
    Example:
        >>> # Use default from settings
        >>> provider = create_llm_provider()
        
        >>> # Override settings
        >>> provider = create_llm_provider(
        ...     provider="claude",
        ...     model="claude-sonnet-4-20250514",
        ...     api_key="sk-ant-..."
        ... )
    """
    # Use settings as defaults
    provider = provider or settings.LLM_PROVIDER
    model = model or settings.LLM_MODEL
    api_key = api_key or settings.LLM_API_KEY
    
    # Validate configuration
    if not provider:
        raise ValueError(
            "LLM provider not configured. "
            "Set LLM_PROVIDER in .env or pass to create_llm_provider()"
        )
    
    if not model:
        raise ValueError(
            "LLM model not configured. "
            "Set LLM_MODEL in .env or pass to create_llm_provider()"
        )
    
    if not api_key:
        raise ValueError(
            f"API key not configured for provider '{provider}'. "
            f"Set LLM_API_KEY in .env or pass to create_llm_provider()"
        )
    
    # Create provider based on type
    provider_lower = provider.lower()
    
    if provider_lower == "claude":
        logger.info(f"Creating Claude provider with model {model}")
        return ClaudeProvider(
            model=model,
            api_key=api_key,
            **kwargs
        )
    
    elif provider_lower in ("openai", "gpt"):
        logger.info(f"Creating OpenAI provider with model {model}")
        
        # Check for custom base URL (for Azure or proxies)
        base_url = kwargs.pop("base_url", None) or settings.OPENAI_BASE_URL
        
        return OpenAIProvider(
            model=model,
            api_key=api_key,
            base_url=base_url,
            **kwargs
        )
    
    elif provider_lower == "azure":
        logger.info(f"Creating Azure OpenAI provider with model {model}")
        
        # Azure uses OpenAI provider but requires base URL
        if not settings.OPENAI_BASE_URL:
            raise ValueError(
                "OPENAI_BASE_URL must be set for Azure OpenAI. "
                "Example: https://your-resource.openai.azure.com/"
            )
        
        return OpenAIProvider(
            model=model,
            api_key=api_key,
            base_url=settings.OPENAI_BASE_URL,
            **kwargs
        )
    
    elif provider_lower == "custom":
        # For custom providers, you would add your implementation here
        raise NotImplementedError(
            "Custom LLM provider not implemented. "
            "Add your custom provider class and register it here."
        )
    
    else:
        raise ValueError(
            f"Unsupported LLM provider: {provider}. "
            f"Supported providers: claude, openai, azure, custom"
        )


def get_llm_provider() -> Optional[BaseLLMProvider]:
    """
    Get or create global LLM provider instance.
    
    Returns:
        LLM provider instance or None if LLM is not configured
    
    Example:
        >>> provider = get_llm_provider()
        >>> if provider:
        ...     response = await provider.chat(messages=[...])
        ... else:
        ...     print("LLM not configured")
    """
    global _global_provider
    
    # Check if LLM is configured
    if not settings.LLM_PROVIDER:
        logger.info("LLM provider not configured (optional for basic MCP mode)")
        return None
    
    # Create provider if not already created
    if _global_provider is None:
        try:
            _global_provider = create_llm_provider()
            logger.info(
                f"Global LLM provider created: "
                f"{_global_provider.get_provider_name()} "
                f"({_global_provider.get_model_name()})"
            )
        except ValueError as e:
            logger.warning(f"Could not create LLM provider: {e}")
            return None
    
    return _global_provider


def reset_llm_provider():
    """
    Reset global LLM provider.
    
    Useful for testing or when configuration changes.
    """
    global _global_provider
    _global_provider = None
    logger.info("Global LLM provider reset")