# backend/utils/llm.py
# ──────────────────────────────────────────────────────────────
# Unified LLM gateway.  Supports OpenAI and Anthropic.
# Provider / model are read from the centralized llm_config.py
# (which in turn reads from .env).
# ──────────────────────────────────────────────────────────────
from __future__ import annotations
import json
import logging
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Union

logger = logging.getLogger(__name__)

from utils.llm_config import (
    LLM_PROVIDER, LLM_MODEL,
    OPENAI_API_KEY, OPENAI_BASE_URL,
    ANTHROPIC_API_KEY,
)
from utils.pii_filter import sanitize_for_llm as _sanitize


# ---- Token usage result ----
@dataclass
class LLMResult:
    """LLM response with token usage metadata."""
    content: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""


# ---- OpenAI SDK ----
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

# ---- Anthropic SDK ----
try:
    from anthropic import Anthropic
except Exception:
    Anthropic = None

# ---- Clients (initialised once at import time) ----
_openai_client = None
if OpenAI and OPENAI_API_KEY:
    _kwargs: Dict[str, Any] = {"api_key": OPENAI_API_KEY}
    if OPENAI_BASE_URL:
        _kwargs["base_url"] = OPENAI_BASE_URL
    _openai_client = OpenAI(**_kwargs)

_anthropic_client = None
if Anthropic and ANTHROPIC_API_KEY:
    _anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)


# ---- Fallback (no keys / SDK) ----
def _fallback(messages: List[Dict[str, str]], response_format: Optional[Dict[str, Any]]) -> LLMResult:
    wants_json = bool(response_format and response_format.get("type") == "json_object")
    content = json.dumps({"mode": "endpoint", "agent": None}) if wants_json else (messages[-1]["content"] if messages else "")
    return LLMResult(content=content)


# ---- OpenAI call (accepts explicit client for per-org overrides) ----
def _chat_openai_with_client(
    client,
    messages: List[Dict[str, str]],
    response_format: Optional[Dict[str, Any]],
    temperature: float,
    model: str,
) -> LLMResult:
    if not client:
        return _fallback(messages, response_format)

    req: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if response_format:
        req["response_format"] = response_format

    resp = client.chat.completions.create(**req)
    usage = resp.usage
    return LLMResult(
        content=resp.choices[0].message.content or "",
        input_tokens=usage.prompt_tokens if usage else 0,
        output_tokens=usage.completion_tokens if usage else 0,
        model=resp.model or model,
    )


def _chat_openai(
    messages: List[Dict[str, str]],
    response_format: Optional[Dict[str, Any]],
    temperature: float,
    model: str,
) -> LLMResult:
    return _chat_openai_with_client(_openai_client, messages, response_format, temperature, model)


# ---- Anthropic call (accepts explicit client for per-org overrides) ----
def _chat_anthropic_with_client(
    client,
    messages: List[Dict[str, str]],
    response_format: Optional[Dict[str, Any]],
    temperature: float,
    model: str,
) -> LLMResult:
    if not client:
        return _fallback(messages, response_format)

    # Anthropic uses a separate system param instead of a system message
    system_text = ""
    user_messages: List[Dict[str, str]] = []
    for m in messages:
        if m["role"] == "system":
            system_text += m["content"] + "\n"
        else:
            user_messages.append({"role": m["role"], "content": m["content"]})

    # If caller wants JSON, append instruction (Anthropic has no response_format)
    if response_format and response_format.get("type") == "json_object":
        system_text += "\nYou MUST respond with ONLY a single valid JSON object. No prose, no markdown fences."

    kwargs: Dict[str, Any] = {
        "model": model,
        "max_tokens": 4096,
        "temperature": temperature,
        "messages": user_messages,
    }
    if system_text.strip():
        kwargs["system"] = system_text.strip()

    resp = client.messages.create(**kwargs)

    # Extract text from content blocks
    parts = [block.text for block in resp.content if hasattr(block, "text")]
    return LLMResult(
        content="".join(parts),
        input_tokens=resp.usage.input_tokens if resp.usage else 0,
        output_tokens=resp.usage.output_tokens if resp.usage else 0,
        model=resp.model or model,
    )


def _chat_anthropic(
    messages: List[Dict[str, str]],
    response_format: Optional[Dict[str, Any]],
    temperature: float,
    model: str,
) -> LLMResult:
    return _chat_anthropic_with_client(_anthropic_client, messages, response_format, temperature, model)


# ---- Message sanitization helper ----
def _sanitize_messages(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """
    Strip non-allowlisted fields from any JSON content embedded in messages.
    This is a defense-in-depth measure — callers SHOULD still sanitize explicitly,
    but this ensures no PII leaks even if a caller forgets.
    """
    cleaned: List[Dict[str, str]] = []
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            # Try to parse as JSON, sanitize, re-serialize
            stripped = content.strip()
            if stripped and stripped[0] in ("{", "["):
                try:
                    parsed = json.loads(stripped)
                    sanitized = _sanitize(parsed)
                    content = json.dumps(sanitized, ensure_ascii=False, default=str)
                except (json.JSONDecodeError, TypeError):
                    pass  # Not JSON — leave as-is (plain text prompt)
        cleaned.append({**msg, "content": content})
    return cleaned


# ---- Public API (backward-compatible) ----
def chat(
    messages: List[Dict[str, str]],
    response_format: Optional[Dict[str, Any]] = None,
    temperature: float = 0.0,
    model: Optional[str] = None,
    sanitize: bool = True,
    return_usage: bool = False,
) -> Union[str, LLMResult]:
    """
    Unified LLM call.  Works with OpenAI or Anthropic — controlled by
    LLM_PROVIDER in .env (via llm_config.py).

    Args:
        messages:        [{"role": "system"|"user"|"assistant", "content": "..."}]
        response_format: {"type": "json_object"} to request structured JSON
        temperature:     0.0 = deterministic
        model:           override model name (default: LLM_MODEL from .env)
        sanitize:        auto-strip non-allowlisted fields from JSON in
                         message content (default: True). Set False only for
                         messages that contain no client data (e.g. pure prompts).
        return_usage:    if True, return LLMResult with token counts;
                         if False (default), return plain str for backward compat.

    Returns:
        str (default) or LLMResult (when return_usage=True).
    """
    resolved_model = model or LLM_MODEL

    if sanitize:
        messages = _sanitize_messages(messages)

    if LLM_PROVIDER == "anthropic":
        result = _chat_anthropic(messages, response_format, temperature, resolved_model)
    else:
        result = _chat_openai(messages, response_format, temperature, resolved_model)

    if return_usage:
        return result
    return result.content


# ---- Per-org LLM override (Phase 6) ----
def chat_with_override(
    messages: List[Dict[str, str]],
    response_format: Optional[Dict[str, Any]] = None,
    temperature: float = 0.0,
    sanitize: bool = True,
    return_usage: bool = False,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Union[str, LLMResult]:
    """
    Like chat(), but accepts per-call provider/model/api_key overrides.
    When api_key is given, creates an ephemeral client (not a singleton) — safe for concurrent requests.
    Falls back to global LLM_PROVIDER/LLM_MODEL/platform keys if not provided.
    """
    resolved_provider = provider or LLM_PROVIDER
    resolved_model = model or LLM_MODEL

    if sanitize:
        messages = _sanitize_messages(messages)

    if api_key:
        # Ephemeral client — created per-call, never stored globally
        if resolved_provider == "anthropic":
            client = Anthropic(api_key=api_key) if Anthropic else None
            result = _chat_anthropic_with_client(client, messages, response_format, temperature, resolved_model)
        else:
            client = OpenAI(api_key=api_key) if OpenAI else None
            result = _chat_openai_with_client(client, messages, response_format, temperature, resolved_model)
    else:
        # No custom key — use global singletons but with possibly overridden provider/model
        if resolved_provider == "anthropic":
            result = _chat_anthropic(messages, response_format, temperature, resolved_model)
        else:
            result = _chat_openai(messages, response_format, temperature, resolved_model)

    return result if return_usage else result.content


# ---- Speech-to-text ----
# Two-pass transcription:
#   1. gpt-4o-transcribe (full model) — raw STT with domain vocabulary prompt
#   2. Post-processing with the app's LLM to fix domain-specific words
#      (employee names, job IDs, Simpro terminology)
_STT_MODEL = "gpt-4o-transcribe"

_STT_PROMPT = (
    "Schedule Tarun for job ID 10685 today from 7am to 10am. "
    "Create a schedule for Alistair Andrew on job 10685 from 7am to 10am for 3 hours. "
    "Give me today's schedules. Show me the invoices for job ID 17684. "
    "Get work orders for job 10685. Delete the schedule for Kevin on job 10685. "
    "Update the schedule for John to 9am to 12pm. "
    "List all contractors on job 10685. Show me the cost centres for job 10685. "
    "Tarun, Alistair Andrew, Kevin, Simpro, Optificial, cost centre, work order."
)

_POST_PROCESS_PROMPT = (
    "You clean up a voice transcript. Apply ONLY these formatting fixes:\n"
    "- '7 a.m.' → '7am', '10 a.m.' → '10am'\n"
    "- 'cost center' → 'cost centre'\n"
    "- 'schedule on [name]' → 'schedule for [name]'\n\n"
    "RULES YOU MUST FOLLOW:\n"
    "- Output EVERY word from the input. Do NOT drop any words.\n"
    "- Do NOT rephrase, restructure, or summarize.\n"
    "- NEVER touch names in any way — do not change, split, merge, add, "
    "or remove any part of a person's name. Pass all names through exactly "
    "as they appear in the input.\n"
    "- Do NOT change verbs. 'Create' stays 'Create'.\n"
    "- Do NOT change or remove numbers, job IDs, durations, or dates.\n"
    "- Do NOT add any words that are not in the input.\n"
    "- If nothing needs fixing, return the input exactly as-is.\n\n"
    "Output the cleaned text only. No quotes. No explanation."
)


def transcribe_audio(audio_file, language: str = "en") -> str:
    """
    Transcribe audio using a two-pass approach:
    1. gpt-4o-transcribe for raw STT (with domain prompt)
    2. Post-process with LLM to fix domain-specific vocabulary errors

    Reuses the existing _openai_client singleton for STT and the
    unified chat() function for post-processing.
    """
    if not _openai_client:
        raise RuntimeError("OpenAI client not configured. Set OPENAI_API_KEY in .env")

    # Pass 1: Raw transcription with domain vocabulary prompt
    transcription = _openai_client.audio.transcriptions.create(
        model=_STT_MODEL,
        file=audio_file,
        prompt=_STT_PROMPT,
    )
    raw_text = transcription.text
    logger.info(f"[STT] Raw transcript: {raw_text}")

    if not raw_text or not raw_text.strip():
        return raw_text

    # Guard against Whisper hallucinating from prompt on silent/near-silent audio
    # If transcript is very short (1-2 words) and all words appear in the STT prompt,
    # it's almost certainly a hallucination rather than real speech
    if len(raw_text.split()) <= 2:
        prompt_words = {w.strip(".,!?;:'\"").lower() for w in _STT_PROMPT.split()}
        transcript_words = {w.strip(".,!?;:'\"").lower() for w in raw_text.split()}
        if transcript_words and transcript_words.issubset(prompt_words):
            logger.info(f"[STT] Rejected likely hallucination: '{raw_text}'")
            return ""

    # Pass 2: Post-process with LLM to fix domain-specific errors
    corrected = chat(
        messages=[
            {"role": "system", "content": _POST_PROCESS_PROMPT},
            {"role": "user", "content": raw_text},
        ],
        temperature=0.0,
        sanitize=False,
    )

    result = corrected.strip() if corrected and corrected.strip() else raw_text
    if result != raw_text:
        logger.info(f"[STT] Corrected:      {result}")
    return result
