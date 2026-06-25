"""
Orchestration layer for MCP Simpro Server.

Provides different orchestration strategies based on LLM capabilities:
- LLM Native: Let smart LLMs (Claude, GPT-4.1) handle orchestration
- Assisted: Help weaker LLMs with hints and guidance
- Manual: Full code-based orchestration for weak LLMs
"""
from .selector import OrchestrationSelector, get_orchestration_selector
from .base import BaseOrchestrator

__all__ = [
    "OrchestrationSelector",
    "get_orchestration_selector",
    "BaseOrchestrator",
]