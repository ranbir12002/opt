"""
Orchestration Strategy Selector.

Automatically selects the best orchestration strategy based on:
1. LLM capabilities (from config)
2. Query complexity
3. Available tools
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from config.settings import settings
from src.llm import get_llm_provider
from src.utils import get_logger

from .base import BaseOrchestrator
from .llm_native import LLMNativeOrchestrator
from .assisted import AssistedOrchestrator
from .manual import ManualOrchestrator

logger = get_logger(__name__)


class OrchestrationSelector:
    """
    Selects appropriate orchestration strategy based on LLM capabilities.
    
    Decision matrix:
    - Excellent LLMs (Claude 4, GPT-4, GPT-4.1): llm_native
    - Good LLMs (GPT-3.5-turbo): assisted
    - Weak/No LLM: manual
    
    Example:
        >>> selector = OrchestrationSelector()
        >>> orchestrator = selector.get_orchestrator()
        >>> result = await orchestrator.orchestrate("Find jobs for customer ABC")
    """
    
    def __init__(self):
        """Initialize orchestration selector"""
        self.llm_provider = get_llm_provider()
        self.capabilities = settings.get_llm_capabilities()
        
        logger.info("Orchestration selector initialized")
    
    def get_strategy_name(self) -> str:
        """
        Get the appropriate strategy name based on LLM capabilities.
        
        Returns:
            Strategy name: "llm_native", "assisted", or "manual"
        """
        if not self.llm_provider:
            # No LLM configured - use manual
            logger.info("No LLM provider - using manual orchestration")
            return "manual"
        
        # Get strategy from capabilities
        strategy = self.capabilities.get("strategy", "manual")
        
        logger.info(f"Selected orchestration strategy: {strategy}")
        return strategy
    
    def get_orchestrator(
        self,
        tools: Optional[List[Dict[str, Any]]] = None,
        force_strategy: Optional[str] = None
    ) -> BaseOrchestrator:
        """
        Get orchestrator instance based on LLM capabilities.
        
        Args:
            tools: List of available tools
            force_strategy: Force specific strategy (override auto-selection)
        
        Returns:
            Orchestrator instance
        
        Example:
            >>> # Auto-select based on LLM
            >>> orchestrator = selector.get_orchestrator(tools=[...])
            
            >>> # Force specific strategy
            >>> orchestrator = selector.get_orchestrator(
            ...     tools=[...],
            ...     force_strategy="manual"
            ... )
        """
        # Determine strategy
        if force_strategy:
            strategy = force_strategy
            logger.info(f"Forcing orchestration strategy: {strategy}")
        else:
            strategy = self.get_strategy_name()
        
        # Create orchestrator based on strategy
        if strategy == "llm_native":
            if not self.llm_provider:
                logger.warning(
                    "llm_native strategy requires LLM provider - falling back to manual"
                )
                return ManualOrchestrator(tools=tools)
            
            return LLMNativeOrchestrator(
                llm_provider=self.llm_provider,
                tools=tools
            )
        
        elif strategy == "assisted":
            if not self.llm_provider:
                logger.warning(
                    "assisted strategy requires LLM provider - falling back to manual"
                )
                return ManualOrchestrator(tools=tools)
            
            return AssistedOrchestrator(
                llm_provider=self.llm_provider,
                tools=tools
            )
        
        elif strategy == "manual":
            return ManualOrchestrator(
                llm_provider=self.llm_provider,
                tools=tools
            )
        
        else:
            logger.warning(f"Unknown strategy '{strategy}' - using manual")
            return ManualOrchestrator(tools=tools)
    
    def get_strategy_info(self) -> Dict[str, Any]:
        """
        Get information about the selected strategy.
        
        Returns:
            Strategy info dict
        """
        strategy = self.get_strategy_name()
        
        info = {
            "strategy": strategy,
            "llm_provider": settings.LLM_PROVIDER if self.llm_provider else None,
            "llm_model": settings.LLM_MODEL if self.llm_provider else None,
            "capabilities": self.capabilities
        }
        
        # Add strategy-specific info
        if strategy == "llm_native":
            info["description"] = "LLM handles all orchestration automatically"
            info["best_for"] = ["Claude Sonnet 4", "Claude Opus", "GPT-4", "GPT-4.1"]
        
        elif strategy == "assisted":
            info["description"] = "LLM with helpful hints and guidance"
            info["best_for"] = ["GPT-3.5-turbo", "GPT-4-mini", "GPT-4.1"]
        
        elif strategy == "manual":
            info["description"] = "Code-based workflows with pattern matching"
            info["best_for"] = ["Weak LLMs", "No LLM", "Guaranteed execution"]
        
        return info


# ===================================================================
# Global selector instance
# ===================================================================
_global_selector: Optional[OrchestrationSelector] = None


def get_orchestration_selector() -> OrchestrationSelector:
    """
    Get or create global orchestration selector.
    
    Returns:
        OrchestrationSelector instance
    """
    global _global_selector
    
    if _global_selector is None:
        _global_selector = OrchestrationSelector()
        logger.info("Global orchestration selector created")
    
    return _global_selector