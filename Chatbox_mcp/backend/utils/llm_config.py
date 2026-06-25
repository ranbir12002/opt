# backend/utils/llm_config.py
# ──────────────────────────────────────────────────────────────
# Single source of truth for LLM provider / model selection.
#
# To switch the entire app from OpenAI to Claude, change THREE
# values in .env and restart:
#
#   LLM_PROVIDER=anthropic
#   LLM_MODEL=claude-sonnet-4-20250514
#   ANTHROPIC_API_KEY=sk-ant-...
# ──────────────────────────────────────────────────────────────
from __future__ import annotations
import os

# Provider: "openai" | "anthropic"
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openai")

# Model name (must match the provider's naming)
LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4.1-mini")

# API keys
OPENAI_API_KEY: str = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

# Optional base URL override (Azure OpenAI, proxy, etc.)
OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "")


def get_provider() -> str:
    return LLM_PROVIDER


def get_model() -> str:
    return LLM_MODEL


def get_api_key() -> str:
    if LLM_PROVIDER == "anthropic":
        return ANTHROPIC_API_KEY
    return OPENAI_API_KEY
