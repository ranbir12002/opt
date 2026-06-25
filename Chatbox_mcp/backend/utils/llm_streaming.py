"""
backend/utils/llm_streaming.py

Async streaming LLM gateway for the Python MCP Executor (Option A migration).

Supports:
  - OpenAI: streaming with rate-limit retry (429), tool call delta accumulation
  - Claude:  streaming with prompt caching (cache_control: ephemeral)

Yields normalised dicts:
  {"type": "token",      "text": "..."}
  {"type": "tool_calls", "tool_calls": [...]}
  {"type": "done",       "content": "...", "tool_calls": [...], "usage": {...}, "model": "..."}

Tool call format (same across providers):
  {"id": "...", "name": "tool_name", "arguments": {...}}   # arguments already parsed

Usage dict:
  {"input_tokens": int, "output_tokens": int, "total_tokens": int,
   "cache_creation_tokens": int, "cache_read_tokens": int}   # last two: Claude only
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, AsyncGenerator, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
ENABLE_PROMPT_CACHING = os.getenv("ENABLE_PROMPT_CACHING", "true").lower() == "true"
MAX_RETRIES = 3

# Context window sizes (for token budget calculations)
CONTEXT_WINDOWS: Dict[str, int] = {
    "gpt-4.1":          1_047_576,
    "gpt-4.1-mini":     1_047_576,
    "gpt-4o":           128_000,
    "gpt-4o-mini":      128_000,
    "gpt-4-turbo":      128_000,
    "gpt-3.5-turbo":    16_385,
    "claude-opus-4-6":              200_000,
    "claude-sonnet-4-6":            200_000,
    "claude-haiku-4-5-20251001":    200_000,
    "claude-3-5-sonnet-20241022":   200_000,
    "claude-3-5-haiku-20241022":    200_000,
}

# ── Lazy SDK imports (same pattern as llm.py) ────────────────────────────────
try:
    from openai import AsyncOpenAI
    _HAS_OPENAI = True
except ImportError:
    AsyncOpenAI = None
    _HAS_OPENAI = False

try:
    import anthropic as _anthropic_mod
    _HAS_ANTHROPIC = True
except ImportError:
    _anthropic_mod = None
    _HAS_ANTHROPIC = False

from utils.llm_config import (
    LLM_PROVIDER, LLM_MODEL,
    OPENAI_API_KEY, OPENAI_BASE_URL,
    ANTHROPIC_API_KEY,
)

# ── Async clients (created once) ─────────────────────────────────────────────
_async_openai: Optional[Any] = None
_async_anthropic: Optional[Any] = None

def _get_openai_client() -> Any:
    global _async_openai
    if _async_openai is None and _HAS_OPENAI and OPENAI_API_KEY:
        kwargs: Dict[str, Any] = {"api_key": OPENAI_API_KEY}
        if OPENAI_BASE_URL:
            kwargs["base_url"] = OPENAI_BASE_URL
        _async_openai = AsyncOpenAI(**kwargs)
    return _async_openai

def _get_anthropic_client() -> Any:
    global _async_anthropic
    if _async_anthropic is None and _HAS_ANTHROPIC and ANTHROPIC_API_KEY:
        _async_anthropic = _anthropic_mod.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    return _async_anthropic


# ── Tool format helpers ───────────────────────────────────────────────────────

def _format_tools_openai(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert MCP tool list to OpenAI function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("inputSchema") or t.get("input_schema") or t.get("parameters") or {},
            },
        }
        for t in tools
    ]


def _format_tools_claude(
    tools: List[Dict[str, Any]],
    enable_cache: bool = True,
) -> List[Dict[str, Any]]:
    """Convert MCP tool list to Anthropic tool format, caching the last tool."""
    formatted = [
        {
            "name": t["name"],
            "description": t.get("description", ""),
            "input_schema": t.get("inputSchema") or t.get("input_schema") or t.get("parameters") or {"type": "object", "properties": {}},
        }
        for t in tools
    ]
    if enable_cache and formatted:
        formatted[-1]["cache_control"] = {"type": "ephemeral"}
    return formatted


# ── Message builders ──────────────────────────────────────────────────────────

def build_messages_openai(
    history: List[Dict[str, Any]],
    user_message: str,
    tool_results: Optional[List[Dict[str, Any]]] = None,
    pending_tool_calls: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Build the full messages array for OpenAI.

    `tool_results` / `pending_tool_calls` are used when continuing after tool
    execution: we append the assistant turn (with tool_calls) then tool results.
    """
    messages: List[Dict[str, Any]] = list(history)

    if pending_tool_calls and tool_results:
        # Append assistant message that requested the tool calls
        messages.append({
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc["arguments"]),
                    },
                }
                for tc in pending_tool_calls
            ],
        })
        # Append each tool result
        for tr in tool_results:
            messages.append({
                "role": "tool",
                "tool_call_id": tr["id"],
                "content": json.dumps(tr["result"]) if not isinstance(tr["result"], str) else tr["result"],
            })
    else:
        messages.append({"role": "user", "content": user_message})

    return messages


def build_messages_claude(
    history: List[Dict[str, Any]],
    user_message: str,
    tool_results: Optional[List[Dict[str, Any]]] = None,
    pending_tool_calls: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Build the full messages array for Anthropic Claude.

    Claude uses a different tool result format than OpenAI.
    """
    messages: List[Dict[str, Any]] = list(history)

    if pending_tool_calls and tool_results:
        # Assistant turn with tool_use content blocks
        tool_use_blocks = [
            {
                "type": "tool_use",
                "id": tc["id"],
                "name": tc["name"],
                "input": tc["arguments"],
            }
            for tc in pending_tool_calls
        ]
        messages.append({"role": "assistant", "content": tool_use_blocks})

        # User turn with tool_result content blocks
        tool_result_blocks = [
            {
                "type": "tool_result",
                "tool_use_id": tr["id"],
                "content": json.dumps(tr["result"]) if not isinstance(tr["result"], str) else tr["result"],
            }
            for tr in tool_results
        ]
        messages.append({"role": "user", "content": tool_result_blocks})
    else:
        messages.append({"role": "user", "content": user_message})

    return messages


# ── OpenAI streaming ──────────────────────────────────────────────────────────

async def stream_openai(
    model: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    system_prompt: Optional[str] = None,
    max_tokens: Optional[int] = None,
    api_key: Optional[str] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Async generator: stream an OpenAI chat completion with tool support.

    Yields: token, tool_calls, done  (see module docstring).
    Retries up to MAX_RETRIES times on 429 rate limit errors.
    If api_key is provided, uses an ephemeral client instead of the module singleton.
    """
    if api_key and _HAS_OPENAI:
        kwargs: Dict[str, Any] = {"api_key": api_key}
        if OPENAI_BASE_URL:
            kwargs["base_url"] = OPENAI_BASE_URL
        client = AsyncOpenAI(**kwargs)
    else:
        client = _get_openai_client()
    if client is None:
        raise RuntimeError("OpenAI client not available — check OPENAI_API_KEY")

    # Prepend system prompt if not already there
    final_messages = list(messages)
    if system_prompt and (not final_messages or final_messages[0].get("role") != "system"):
        final_messages = [{"role": "system", "content": system_prompt}] + final_messages

    request_params: Dict[str, Any] = {
        "model": model,
        "messages": final_messages,
        "temperature": 0,
        "stream": True,
        "stream_options": {"include_usage": True},
    }

    if max_tokens:
        request_params["max_tokens"] = max_tokens
    else:
        has_tool_results = any(m.get("role") == "tool" for m in final_messages)
        if has_tool_results:
            request_params["max_tokens"] = 4000

    formatted_tools = _format_tools_openai(tools) if tools else None
    if formatted_tools:
        request_params["tools"] = formatted_tools
        request_params["tool_choice"] = "auto"

    logger.debug(f"[OpenAI stream] {len(final_messages)} messages, {len(tools or [])} tools, model={model}")

    # Retry loop for 429
    stream = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            stream = await client.chat.completions.create(**request_params)
            break
        except Exception as e:
            status = getattr(e, "status_code", None) or getattr(e, "status", None)
            if status == 429 and attempt < MAX_RETRIES:
                headers = getattr(e, "response", None) and e.response.headers or {}
                retry_after_ms = (
                    int(headers.get("retry-after-ms", 0))
                    or (int(headers.get("retry-after", 5)) * 1000)
                )
                wait_ms = retry_after_ms + 1000
                logger.warning(f"[OpenAI stream] Rate limited (attempt {attempt}/{MAX_RETRIES}). Waiting {wait_ms}ms")
                await asyncio.sleep(wait_ms / 1000)
                continue
            raise

    full_content = ""
    usage = None
    tool_call_map: Dict[int, Dict[str, Any]] = {}

    async for chunk in stream:
        choice = chunk.choices[0] if chunk.choices else None
        delta = choice.delta if choice else None

        if delta and delta.content:
            full_content += delta.content
            yield {"type": "token", "text": delta.content}

        if delta and delta.tool_calls:
            for tc in delta.tool_calls:
                idx = tc.index
                if idx not in tool_call_map:
                    tool_call_map[idx] = {"id": tc.id or "", "name": "", "arguments": ""}
                entry = tool_call_map[idx]
                if tc.id:
                    entry["id"] = tc.id
                if tc.function and tc.function.name:
                    entry["name"] += tc.function.name
                if tc.function and tc.function.arguments:
                    entry["arguments"] += tc.function.arguments

        if chunk.usage:
            usage = {
                "input_tokens": chunk.usage.prompt_tokens,
                "output_tokens": chunk.usage.completion_tokens,
                "total_tokens": chunk.usage.total_tokens,
                "cache_creation_tokens": 0,
                "cache_read_tokens": 0,
            }

    # Parse accumulated tool calls
    tool_calls = []
    for _, entry in sorted(tool_call_map.items()):
        try:
            tool_calls.append({
                "id": entry["id"],
                "name": entry["name"],
                "arguments": json.loads(entry["arguments"]) if entry["arguments"] else {},
            })
        except json.JSONDecodeError as e:
            logger.error(f"[OpenAI stream] Failed to parse tool args for {entry['name']}: {e}")

    if tool_calls:
        yield {"type": "tool_calls", "tool_calls": tool_calls}

    yield {
        "type": "done",
        "content": full_content,
        "tool_calls": tool_calls,
        "usage": usage or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cache_creation_tokens": 0, "cache_read_tokens": 0},
        "model": model,
    }


# ── Claude streaming ──────────────────────────────────────────────────────────

async def stream_claude(
    model: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    system_prompt: Optional[str] = None,
    max_tokens: int = 8192,
    enable_cache: bool = True,
    api_key: Optional[str] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Async generator: stream an Anthropic Claude completion with tool support.

    Uses prompt caching (cache_control: ephemeral) on the system prompt and
    the last tool definition when `enable_cache=True`.

    Yields: token, tool_calls, done  (see module docstring).
    If api_key is provided, uses an ephemeral client instead of the module singleton.
    """
    if api_key and _HAS_ANTHROPIC:
        client = _anthropic_mod.AsyncAnthropic(api_key=api_key)
    else:
        client = _get_anthropic_client()
    if client is None:
        raise RuntimeError("Anthropic client not available — check ANTHROPIC_API_KEY")

    enable_cache = enable_cache and ENABLE_PROMPT_CACHING

    # System with optional cache_control
    system: List[Dict[str, Any]] = []
    if system_prompt:
        block: Dict[str, Any] = {"type": "text", "text": system_prompt}
        if enable_cache:
            block["cache_control"] = {"type": "ephemeral"}
        system = [block]

    formatted_tools = _format_tools_claude(tools or [], enable_cache=enable_cache) if tools else []

    logger.debug(f"[Claude stream] {len(messages)} messages, {len(tools or [])} tools, model={model}, cache={enable_cache}")

    async with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        system=system or _anthropic_mod.NOT_GIVEN,
        messages=messages,
        tools=formatted_tools or _anthropic_mod.NOT_GIVEN,
    ) as stream:
        current_tool: Optional[Dict[str, Any]] = None
        current_input = ""
        all_tool_calls: List[Dict[str, Any]] = []

        async for event in stream:
            etype = event.type

            if etype == "content_block_start":
                block = event.content_block
                if block.type == "tool_use":
                    current_tool = {"id": block.id, "name": block.name}
                    current_input = ""

            elif etype == "content_block_delta":
                delta = event.delta
                if delta.type == "text_delta":
                    yield {"type": "token", "text": delta.text}
                elif delta.type == "input_json_delta":
                    current_input += delta.partial_json

            elif etype == "content_block_stop":
                if current_tool is not None:
                    try:
                        current_tool["arguments"] = json.loads(current_input) if current_input else {}
                    except json.JSONDecodeError:
                        current_tool["arguments"] = {}
                    all_tool_calls.append(current_tool)
                    current_tool = None
                    current_input = ""

            elif etype == "message_stop":
                pass  # handled below after stream exits

        if all_tool_calls:
            yield {"type": "tool_calls", "tool_calls": all_tool_calls}

        final_msg = await stream.get_final_message()
        final_usage = final_msg.usage

        yield {
            "type": "done",
            "content": "".join(
                b.text for b in final_msg.content
                if hasattr(b, "text")
            ),
            "tool_calls": all_tool_calls,
            "usage": {
                "input_tokens": final_usage.input_tokens,
                "output_tokens": final_usage.output_tokens,
                "total_tokens": final_usage.input_tokens + final_usage.output_tokens,
                "cache_creation_tokens": getattr(final_usage, "cache_creation_input_tokens", 0),
                "cache_read_tokens": getattr(final_usage, "cache_read_input_tokens", 0),
            },
            "model": model,
        }


# ── Provider-dispatch entry point ─────────────────────────────────────────────

async def stream_llm(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    system_prompt: Optional[str] = None,
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Dispatch to the correct provider's streaming function.

    Uses LLM_PROVIDER / LLM_MODEL from env unless overridden.
    If api_key is provided, passes it through for per-org DB-sourced keys.
    """
    p = (provider or LLM_PROVIDER).lower()
    m = model or LLM_MODEL

    if p == "anthropic":
        return stream_claude(
            model=m,
            messages=messages,
            tools=tools,
            system_prompt=system_prompt,
            max_tokens=max_tokens or 8192,
            api_key=api_key,
        )
    else:
        return stream_openai(
            model=m,
            messages=messages,
            tools=tools,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            api_key=api_key,
        )


def get_context_window(model: str) -> int:
    """Return the context window size for a given model, defaulting to 128k."""
    for key, size in CONTEXT_WINDOWS.items():
        if key in model:
            return size
    return 128_000
