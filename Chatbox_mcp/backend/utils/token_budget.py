"""
backend/utils/token_budget.py

Token budget enforcement for the Python MCP Executor.
Port of mcp-client/utils/token-budget.js.

Uses a chars/4 heuristic for token estimation — intentionally approximate.
Goal: prevent context overflow, not exact accounting.

Priority (never trimmed → first trimmed):
  1. Last user message     — NEVER trimmed
  2. Last 2 tool results   — NEVER trimmed
  3. Old tool results      — trimmed first (already compacted)
  4. Oldest history        — trimmed second
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from utils.llm_streaming import get_context_window

logger = logging.getLogger(__name__)

# ── Budget allocation ─────────────────────────────────────────────────────────
RESPONSE_RESERVE = 4096   # Reserve for output tokens
SYSTEM_RESERVE   = 2000   # Approximate system prompt + tools overhead
SAFETY_MARGIN    = 1000   # Buffer for edge-case overflows
DEFAULT_CONTEXT_LIMIT = 16_000  # Conservative fallback


def _estimate_tokens(text: Any) -> int:
    """Rough token count: ~4 chars/token for English text."""
    if text is None:
        return 0
    if not isinstance(text, str):
        text = json.dumps(text)
    return (len(text) + 3) // 4


def _message_tokens(msg: Dict[str, Any]) -> int:
    """Estimate tokens for a single message dict."""
    if msg is None:
        return 0

    content = msg.get("content")

    # String content
    if isinstance(content, str):
        return _estimate_tokens(content) + 4  # +4 for role/structure

    # List content (Claude tool_result blocks or OpenAI function calls)
    if isinstance(content, list):
        total = 4
        for block in content:
            if isinstance(block, dict):
                c = block.get("content") or block.get("text") or ""
                total += _estimate_tokens(c)
            else:
                total += _estimate_tokens(block)
        return total

    # Tool_calls (OpenAI assistant message)
    if msg.get("tool_calls"):
        return _estimate_tokens(json.dumps(msg["tool_calls"])) + 4

    return _estimate_tokens(json.dumps(content)) + 4


def enforce_token_budget(
    model: str,
    messages: List[Dict[str, Any]],
    tool_count: int = 0,
) -> List[Dict[str, Any]]:
    """
    Trim messages to fit within the model's context window.

    Returns a new list — does not mutate the input.
    """
    context_limit = get_context_window(model)
    tools_overhead = tool_count * 100  # ~100 tokens per tool definition
    usable_budget = context_limit - RESPONSE_RESERVE - SYSTEM_RESERVE - SAFETY_MARGIN - tools_overhead

    total_tokens = sum(_message_tokens(m) for m in messages)

    if total_tokens <= usable_budget:
        return messages

    logger.info(f"[TokenBudget] Over budget: {total_tokens} tokens vs {usable_budget} usable. Trimming...")

    result = list(messages)
    last_msg_idx = len(result) - 1

    # Find tool result indices (both OpenAI 'tool' role and Claude 'user' with tool_result content)
    tool_indices = []
    for i, msg in enumerate(result):
        if msg.get("role") == "tool":
            tool_indices.append(i)
        elif msg.get("role") == "user" and isinstance(msg.get("content"), list):
            if result[i]["content"] and result[i]["content"][0].get("type") == "tool_result":
                tool_indices.append(i)

    # Protected indices: last message + last 2 tool results + their preceding assistant turns
    protected = {last_msg_idx}
    last_two_tools = tool_indices[-2:]
    for idx in last_two_tools:
        protected.add(idx)
        if idx > 0 and result[idx - 1].get("role") == "assistant":
            protected.add(idx - 1)

    # Phase 1: Replace old tool results with compact placeholders
    old_tool_indices = [i for i in tool_indices if i not in protected]
    for idx in old_tool_indices:
        if total_tokens <= usable_budget:
            break
        saved = _message_tokens(result[idx])
        result[idx] = {**result[idx], "content": "[earlier tool result removed for context budget]"}
        total_tokens -= saved - 15  # 15 tokens for replacement text

        # Also blank the preceding assistant tool_use message
        if idx > 0 and result[idx - 1].get("role") == "assistant" and (idx - 1) not in protected:
            saved2 = _message_tokens(result[idx - 1])
            result[idx - 1] = {"role": "assistant", "content": "[earlier tool call removed for context budget]"}
            total_tokens -= saved2 - 15

    if total_tokens <= usable_budget:
        logger.info(f"[TokenBudget] After tool trimming: {total_tokens} tokens")
        return result

    # Phase 2: Remove oldest conversation history entries
    for i in range(len(result)):
        if total_tokens <= usable_budget:
            break
        if i in protected:
            continue
        content = result[i].get("content", "")
        if content in (
            "[earlier tool result removed for context budget]",
            "[earlier tool call removed for context budget]",
        ):
            continue

        saved = _message_tokens(result[i])
        role = result[i].get("role")
        if role == "user" and isinstance(content, str):
            result[i] = {"role": "user", "content": "[earlier message removed for context budget]"}
            total_tokens -= saved - 15
        elif role == "assistant":
            result[i] = {"role": "assistant", "content": "[earlier response removed for context budget]"}
            total_tokens -= saved - 15

    logger.info(f"[TokenBudget] After full trimming: {total_tokens} tokens (budget: {usable_budget})")
    return result


def estimate_message_tokens(messages: List[Dict[str, Any]]) -> int:
    """Estimate total token usage for a message array (for logging/monitoring)."""
    return sum(_message_tokens(m) for m in messages)
