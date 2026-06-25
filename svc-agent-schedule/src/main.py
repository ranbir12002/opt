# src/main.py
"""CLI wrapper for schedule agent (for testing)."""

import asyncio
import json
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from schedule_agent import run_schedule_agent


async def main():
    """Simple CLI for testing the agent."""
    print("Schedule Agent CLI")
    print("This is a test wrapper - normally called by backend\n")

    # Mock LLM (not used yet)
    def mock_llm(messages, **kwargs):
        return "Mock LLM response"

    # Mock executor (would be provided by backend)
    mock_executor = None

    result = await run_schedule_agent(
        llm_chat=mock_llm,
        user_text="Test schedule creation",
        extracted=None,
        mcp_executor=mock_executor
    )

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
