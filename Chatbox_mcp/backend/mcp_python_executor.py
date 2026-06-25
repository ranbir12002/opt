"""
backend/mcp_python_executor.py

The Python MCP Executor — Option A full migration.

Replaces Node.js mcp-client executor for the MCP path.
Brings ALL Python intelligence utilities to the MCP path:

  Port from Node.js (JS capabilities):
    - LLM loop with tool calling
    - Streaming SSE (token-by-token)
    - Query planning (query_planner.py)
    - Reasoning turn
    - Sufficiency checking (sufficiency_checker.py)
    - Token budget enforcement (token_budget.py)
    - Message compaction (tool_result_compressor.py)
    - Tiered result compression (tool_result_compressor.py)
    - Tool response field registry (tool_response_fields.py)
    - Smart LLM routing (llm_router.py) — disabled by default
    - Query decomposer (query_decomposer.py) — disabled by default
    - Prompt caching (Claude) — enabled when ENABLE_PROMPT_CACHING=true
    - Rate limit retry (OpenAI) — built into llm_streaming.py

  NEW: Python intelligence on MCP path (previously agent-only):
    - Entity resolution in tool call loop (_resolve_tool_args)
    - Interactive clarification on MCP read queries (AmbiguousResolutionError → form)
    - Input validation (input_validator.py)
    - Decision journaling (decision_journal.py)
    - Capability radar (capability_radar.py)
    - History filtering (history_filter.py)
    - Context summarization (context_manager.py)
    - Post filtering (post_filter.py)

Feature flag: USE_PYTHON_EXECUTOR=false (default)
When false, chat.py routes to Node.js. When true, routes here.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import date
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from utils.llm_streaming import (
    stream_llm,
    build_messages_openai,
    build_messages_claude,
    LLM_PROVIDER,
    LLM_MODEL,
)

from utils.llm_config import LLM_PROVIDER as _PROVIDER
from utils.token_budget import enforce_token_budget, estimate_message_tokens
from utils.tool_result_compressor import (
    compress_tool_result,
    compact_messages,
)
from utils.query_planner import plan_query
from utils.sufficiency_checker import check_sufficiency, should_check_sufficiency
from utils.tool_response_fields import (
    get_response_fields_for_tools,
    extract_tool_names_from_plan,
)
from utils.llm_router import route_query
from utils.query_decomposer import decompose_query
from utils.mcp_tool_client import get_mcp_tool_client
from utils.entity_resolver import (
    EntityResolver,
    AmbiguousResolutionError,
    ResolutionError,
    MissingFieldError,
    BatchedClarificationError,
)
from utils.history_filter import filter_history
from utils.request_state import RequestExecutionState

MAX_ITERATIONS = 10

# ── MCP clarification session store ──────────────────────────────────────────
# Keyed by session_id (uuid). Stores enough context to re-run the query
# after the user resolves an ambiguous entity name via the UI.
# Consumed (popped) by the /mcp/clarify/{session_id} endpoint in chat.py.
_mcp_pending_sessions: Dict[str, Dict[str, Any]] = {}

# ── MCPToolClient cache — keyed by (simpro_url, simpro_token) ─────────────────
# Reuses the same client (and its tools cache) across requests for the same org.
# Avoids a fresh list_tools() HTTP call on every request.
_mcp_client_cache: Dict[tuple, Any] = {}

# ── Entity resolution: which tool argument keys map to which entity type ─────
# Format: {"arg_key_pattern": "resolver_method"}
# These are checked in _resolve_tool_args() before every tool call.
_NAME_ARG_RESOLVERS = {
    # Schedule filter: Staff.Name → Staff.ID
    ("filters", "Staff.Name"): "staff",
    ("filters", "Staff.ID"):   None,   # already an ID, skip
    # Customer name
    ("filters", "Customer.CompanyName"): "customer",
    # Contractor name
    ("filters", "Contractor.CompanyName"): "contractor",
}


class PythonMCPExecutor:
    """
    Full Python LLM orchestration loop for the MCP path.
    Single instance per request — not a singleton.
    """

    def __init__(
        self,
        user_id: Optional[int] = None,
        org_id: Optional[int] = None,
        request_id: Optional[str] = None,
        simpro_token: Optional[str] = None,
        simpro_url: Optional[str] = None,
        simpro_company_id: Optional[int] = None,
        llm_provider: Optional[str] = None,
        llm_model: Optional[str] = None,
        llm_api_key: Optional[str] = None,
    ):
        self.user_id = user_id
        self.org_id = org_id
        self.request_id = request_id
        self._simpro_token = simpro_token
        self._simpro_url = simpro_url
        self._simpro_company_id = simpro_company_id
        self._llm_provider = llm_provider
        self._llm_model = llm_model
        self._llm_api_key = llm_api_key
        self._mcp_client = None
        self._tools: Optional[List[Dict[str, Any]]] = None
        self._entity_resolver: Optional[EntityResolver] = None
        # Set at the start of each execute_chat / execute_chat_stream so
        # _resolve_tool_args can build a proper clarification session.
        self._current_user_message: str = ""
        self._current_history: List[Dict] = []
        self._exec_state: RequestExecutionState = RequestExecutionState.empty()

    async def _init(self) -> None:
        """Lazy initialisation of MCP client + tool list.

        MCPToolClient instances are cached at module level keyed by
        (simpro_url, simpro_token) so list_tools() is only called once
        per unique org credentials, not on every request.
        """
        if self._mcp_client is None:
            from utils.mcp_tool_client import MCPToolClient
            cache_key = (self._simpro_url, self._simpro_token)
            if cache_key in _mcp_client_cache:
                self._mcp_client = _mcp_client_cache[cache_key]
                self._tools = await self._mcp_client.list_tools()
                logger.debug(f"[PythonExecutor] Reused cached MCPToolClient ({len(self._tools)} tools)")
            else:
                if self._simpro_token and self._simpro_url:
                    self._mcp_client = MCPToolClient(
                        simpro_token=self._simpro_token,
                        simpro_url=self._simpro_url,
                        simpro_company_id=self._simpro_company_id,
                    )
                else:
                    self._mcp_client = get_mcp_tool_client()
                self._tools = await self._mcp_client.list_tools()
                _mcp_client_cache[cache_key] = self._mcp_client
                logger.info(f"[PythonExecutor] Initialised with {len(self._tools)} tools")

    def _get_entity_resolver(self) -> EntityResolver:
        """Lazily create an EntityResolver backed by the MCP client."""
        if self._entity_resolver is None:
            from utils.mcp_executor import MCPToolExecutor
            company_id = self._simpro_company_id
            if not company_id:
                raise ValueError(
                    "No Simpro Company ID configured for this organisation. "
                    "Set it in the super admin panel."
                )
            mcp_exec = MCPToolExecutor(
                tool_registry=self._mcp_client,
                company_id=company_id,
            )
            self._entity_resolver = EntityResolver(mcp_executor=mcp_exec, org_id=self.org_id or 0)
        return self._entity_resolver

    # ── LLM helpers ──────────────────────────────────────────────────────────

    async def _llm_chat(self, messages: List[Dict], max_tokens: int = 300) -> str:
        """
        Non-streaming LLM call (used for planning, sufficiency checks).
        Uses per-org provider/model/api_key when available, falls back to global .env config.
        """
        from utils.llm import chat_with_override, LLMResult
        loop = asyncio.get_event_loop()

        def _sync_call():
            return chat_with_override(
                messages,
                provider=self._llm_provider or None,
                model=self._llm_model or None,
                api_key=self._llm_api_key or None,
                return_usage=True,
                sanitize=False,  # planner/sufficiency prompts contain no client PII
            )

        result = await loop.run_in_executor(None, _sync_call)
        if isinstance(result, LLMResult):
            return result.content
        return str(result)

    # ── Entity resolution hook ────────────────────────────────────────────────

    async def _resolve_tool_args(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        user_message: str = "",
        history: Optional[List[Dict]] = None,
    ) -> Tuple[Dict[str, Any], Optional[Dict]]:
        """
        Intercept tool arguments before sending to Simpro.
        Resolves fuzzy entity names to IDs using entity_resolver.

        Returns:
            (resolved_arguments, clarification_response)
            If clarification is needed, resolved_arguments == arguments (unchanged)
            and clarification_response is a ClarificationForm-compatible dict.
        """
        resolved = dict(arguments)

        # Only process if the tool takes a filters param
        filters = resolved.get("filters")
        if not isinstance(filters, dict):
            return resolved, None

        resolver = self._get_entity_resolver()
        _history = history or []

        # ── Staff.Name → Staff.ID ────────────────────────────────────────────
        staff_name = filters.get("Staff.Name")
        if staff_name and isinstance(staff_name, str):
            # Strip any leading % / % wildcards that LLM sometimes adds
            clean_name = staff_name.strip("%").strip()
            _cache_key = f"Staff.{clean_name}"
            _cached = self._exec_state.get_entity(_cache_key)
            if _cached is not None:
                filters = dict(filters)
                del filters["Staff.Name"]
                filters["Staff.ID"] = str(_cached)
                resolved["filters"] = filters
            else:
                try:
                    result = await resolver.resolve_staff(name=clean_name)
                    resolved_id = result["id"]
                    logger.info(f"[EntityResolve] {tool_name}: Staff.Name={clean_name!r} → Staff.ID={resolved_id}")
                    self._exec_state.cache_entity(_cache_key, resolved_id)
                    # Replace fuzzy name filter with exact ID filter
                    filters = dict(filters)
                    del filters["Staff.Name"]
                    filters["Staff.ID"] = str(resolved_id)
                    resolved["filters"] = filters
                except AmbiguousResolutionError as e:
                    logger.info(f"[EntityResolve] Ambiguous staff name: {clean_name!r} — requesting clarification")
                    return arguments, _build_clarification_response(
                        e, user_message, _history, tool_name, arguments,
                        resolved_filter_key="Staff.Name",
                        resolved_id_key="Staff.ID",
                    )
                except ResolutionError as e:
                    logger.warning(f"[EntityResolve] Staff not found: {clean_name!r} — {e}")
                    # Don't block — let the tool call proceed with the original filter

        # ── Customer.CompanyName → Customer.ID filter ────────────────────────
        customer_name = filters.get("Customer.CompanyName")
        if customer_name and isinstance(customer_name, str):
            clean_name = customer_name.strip("%").strip()
            # For customers, wildcards often work fine — only resolve if the
            # name looks like an exact reference (no wildcards used)
            if "%" not in customer_name:
                _cache_key = f"Customer.{clean_name}"
                _cached = self._exec_state.get_entity(_cache_key)
                if _cached is not None:
                    filters = dict(filters)
                    del filters["Customer.CompanyName"]
                    filters["Customer.ID"] = str(_cached)
                    resolved["filters"] = filters
                else:
                    try:
                        result = await resolver.resolve_customer(name=clean_name)
                        resolved_id = result["id"]
                        logger.info(f"[EntityResolve] {tool_name}: Customer.CompanyName={clean_name!r} → Customer.ID={resolved_id}")
                        self._exec_state.cache_entity(_cache_key, resolved_id)
                        filters = dict(filters)
                        del filters["Customer.CompanyName"]
                        filters["Customer.ID"] = str(resolved_id)
                        resolved["filters"] = filters
                    except AmbiguousResolutionError as e:
                        return arguments, _build_clarification_response(
                            e, user_message, _history, tool_name, arguments,
                            resolved_filter_key="Customer.CompanyName",
                            resolved_id_key="Customer.ID",
                        )
                    except ResolutionError:
                        pass  # Let wildcard search proceed

        # ── Contractor.CompanyName → Contractor.ID filter ────────────────────
        contractor_name = filters.get("Contractor.CompanyName")
        if contractor_name and isinstance(contractor_name, str):
            clean_name = contractor_name.strip("%").strip()
            if "%" not in contractor_name:
                _cache_key = f"Contractor.{clean_name}"
                _cached = self._exec_state.get_entity(_cache_key)
                if _cached is not None:
                    filters = dict(filters)
                    del filters["Contractor.CompanyName"]
                    filters["Contractor.ID"] = str(_cached)
                    resolved["filters"] = filters
                else:
                    try:
                        result = await resolver.resolve_contractor(name=clean_name)
                        resolved_id = result["id"]
                        logger.info(f"[EntityResolve] {tool_name}: Contractor.CompanyName={clean_name!r} → Contractor.ID={resolved_id}")
                        self._exec_state.cache_entity(_cache_key, resolved_id)
                        filters = dict(filters)
                        del filters["Contractor.CompanyName"]
                        filters["Contractor.ID"] = str(resolved_id)
                        resolved["filters"] = filters
                    except AmbiguousResolutionError as e:
                        return arguments, _build_clarification_response(
                            e, user_message, _history, tool_name, arguments,
                            resolved_filter_key="Contractor.CompanyName",
                            resolved_id_key="Contractor.ID",
                        )
                    except ResolutionError:
                        pass

        # ── Job.Name / Site.Name → Job.ID filter ─────────────────────────────
        # LLM may use "Job.Name" or "Site.Name" as filter key; both resolve to Job.ID.
        for filter_key, resolver_kwarg in (("Job.Name", "name"), ("Site.Name", "site_name")):
            job_name = filters.get(filter_key)
            if job_name and isinstance(job_name, str):
                clean_name = job_name.strip("%").strip()
                if "%" not in job_name:
                    _cache_key = f"Job.{clean_name}"
                    _cached = self._exec_state.get_entity(_cache_key)
                    if _cached is not None:
                        filters = dict(filters)
                        del filters[filter_key]
                        filters["Job.ID"] = str(_cached)
                        resolved["filters"] = filters
                        break
                    try:
                        result = await resolver.resolve_job(**{resolver_kwarg: clean_name})
                        resolved_id = result["id"]
                        logger.info(f"[EntityResolve] {tool_name}: {filter_key}={clean_name!r} → Job.ID={resolved_id}")
                        self._exec_state.cache_entity(_cache_key, resolved_id)
                        filters = dict(filters)
                        del filters[filter_key]
                        filters["Job.ID"] = str(resolved_id)
                        resolved["filters"] = filters
                        break  # Only one job filter needed
                    except AmbiguousResolutionError as e:
                        return arguments, _build_clarification_response(
                            e, user_message, _history, tool_name, arguments,
                            resolved_filter_key=filter_key,
                            resolved_id_key="Job.ID",
                        )
                    except ResolutionError:
                        pass  # Let Simpro handle unresolved job name

        return resolved, None

    # ── Tool execution ────────────────────────────────────────────────────────

    async def _execute_tool(
        self, tool_name: str, arguments: Dict[str, Any]
    ) -> Tuple[Any, Optional[Dict]]:
        """
        Execute an MCP tool, applying entity resolution first.

        Returns (result, clarification_response).
        If clarification_response is set, the caller should surface it immediately
        and not use the result.
        """
        resolved_args, clarification = await self._resolve_tool_args(
            tool_name, arguments,
            user_message=self._current_user_message,
            history=self._current_history,
        )
        if clarification:
            return None, clarification

        # Inject org_id into handoff_to_agent so the backend can resolve per-org LLM key
        if tool_name == "handoff_to_agent" and self.org_id:
            resolved_args = {**resolved_args, "org_id": self.org_id}

        try:
            result = await self._mcp_client.execute_tool(tool_name, resolved_args)
            # Only count toward the runaway guard if the call actually executed.
            # Validation errors (bad LLM params) are not retryable loops — the
            # LLM will fix its params next iteration, so don't penalize the tool.
            error = result.get("error") or ""
            if not error.startswith("Validation error"):
                self._exec_state.record_tool_call(tool_name)
            return result, None
        except Exception as e:
            logger.error(f"[PythonExecutor] Tool {tool_name} failed: {e}")
            self._exec_state.record_tool_call(tool_name)  # network/server failures count
            return {"error": str(e), "success": False}, None

    async def _execute_tool_calls(
        self, tool_calls: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], Optional[Dict]]:
        """
        Execute multiple tool calls (in parallel where safe).
        Returns (tool_results, clarification_response).
        """
        tasks = [
            self._execute_tool(tc["name"], tc.get("arguments") or {})
            for tc in tool_calls
        ]
        results_with_clarifications = await asyncio.gather(*tasks, return_exceptions=False)

        # Check for any clarification needed
        for _, clarification in results_with_clarifications:
            if clarification:
                return [], clarification

        tool_results = []
        for (result, _), tc in zip(results_with_clarifications, tool_calls):
            tool_results.append({
                "id": tc.get("id", ""),
                "name": tc["name"],
                "result": result,
            })

        return tool_results, None

    # ── Message builder ───────────────────────────────────────────────────────

    def _build_messages_with_tool_results(
        self,
        current_messages: List[Dict],
        text_content: str,
        tool_calls: List[Dict],
        tool_results: List[Dict],
    ) -> List[Dict]:
        """
        Append the assistant's tool-calling turn and tool results to message list.
        Provider-agnostic: dispatches to OpenAI or Claude message format.
        """
        provider = _PROVIDER.lower()
        updated = list(current_messages)

        if provider == "anthropic":
            # Claude: assistant message has content array of text + tool_use blocks
            content_blocks: List[Dict] = []
            if text_content:
                content_blocks.append({"type": "text", "text": text_content})
            for tc in tool_calls:
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": tc.get("arguments") or {},
                })
            updated.append({"role": "assistant", "content": content_blocks})

            # Tool results as user turn
            for tr in tool_results:
                compressed = compress_tool_result(tr["name"], tr["result"])
                updated.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tr["id"],
                            "content": compressed,
                        }
                    ],
                })
        else:
            # OpenAI: assistant message with tool_calls array
            updated.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc.get("arguments") or {}),
                        },
                    }
                    for tc in tool_calls
                ],
            })

            for tr in tool_results:
                compressed = compress_tool_result(tr["name"], tr["result"])
                updated.append({
                    "role": "tool",
                    "tool_call_id": tr["id"],
                    "content": compressed,
                })

        # Compact old tool results to save tokens
        return compact_messages(updated, keep_last_n_tool_exchanges=2)

    # ── System prompt ─────────────────────────────────────────────────────────

    def _build_system_prompt(
        self,
        base_prompt: Optional[str] = None,
        plan_hint: Optional[str] = None,
        response_fields_block: Optional[str] = None,
        decompose_hint: Optional[str] = None,
    ) -> str:
        """Assemble system prompt with optional plan and response field hints."""
        prompt = base_prompt or _build_default_system_prompt()
        if response_fields_block:
            prompt += response_fields_block
        if plan_hint:
            prompt += plan_hint
        if decompose_hint:
            prompt += decompose_hint
        return prompt

    # ── Non-streaming execute ─────────────────────────────────────────────────

    async def execute_chat(
        self,
        user_message: str,
        history: Optional[List[Dict]] = None,
        system_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Non-streaming LLM loop.
        Returns the same dict format as the Node.js executor.
        """
        await self._init()
        history = filter_history(list(history or []), "mcp")
        self._current_user_message = user_message
        self._current_history = list(history)

        model = route_query(user_message)
        tools = self._tools or []

        # Build initial messages
        current_messages = list(history)
        current_messages.append({"role": "user", "content": user_message})

        # Pre-loop intelligence — returns enriched system prompt + messages
        # (messages may have a reasoning assistant turn prepended)
        effective_system, current_messages = await self._run_pre_loop(
            user_message, model, tools, system_prompt, current_messages
        )

        all_tool_calls: List[Dict] = []
        iteration = 0
        _iter_cap = self._exec_state.effective_max_iterations

        while iteration < _iter_cap:
            iteration += 1
            logger.info(f"[PythonExecutor] Iteration {iteration}/{_iter_cap}")

            current_messages = enforce_token_budget(model, current_messages, len(tools))

            # Non-streaming call via existing llm.py (tools not yet supported there —
            # we use streaming internally and collect the done event)
            full_content = ""
            turn_tool_calls: List[Dict] = []

            gen = await stream_llm(
                messages=current_messages,
                tools=tools,
                system_prompt=effective_system,
                model=self._llm_model or model,
                provider=self._llm_provider or None,
                api_key=self._llm_api_key or None,
            )
            async for chunk in gen:
                if chunk["type"] == "token":
                    full_content += chunk["text"]
                elif chunk["type"] == "tool_calls":
                    turn_tool_calls = chunk["tool_calls"]
                elif chunk["type"] == "done":
                    if not turn_tool_calls:
                        turn_tool_calls = chunk.get("tool_calls") or []
                    usage = chunk.get("usage") or {}

            if full_content.strip():
                logger.info(f"[PythonExecutor] Iter {iteration} reasoning: {full_content.strip()[:400]}")

            if not turn_tool_calls:
                logger.info("[PythonExecutor] LLM finished without tool calls")
                return {
                    "success": True,
                    "response": full_content,
                    "toolCalls": all_tool_calls,
                    "iterations": iteration,
                    "metadata": {"usage": _normalize_usage(usage), "model": model},
                }

            logger.info(f"[PythonExecutor] Executing {len(turn_tool_calls)} tool call(s)")
            for tc in turn_tool_calls:
                args_preview = str(tc.get("arguments") or tc.get("input") or {})[:150]
                logger.info(f"[PythonExecutor] → tool: {tc.get('name')} | args: {args_preview}")
            tool_results, clarification = await self._execute_tool_calls(turn_tool_calls)

            if clarification:
                # Entity resolution ambiguity — surface clarification form immediately
                return {
                    "success": True,
                    "response": "",
                    "toolCalls": all_tool_calls,
                    "clarification": clarification,
                    "metadata": {"usage": {}, "model": model},
                }

            all_tool_calls.extend(tool_results)

            # Sufficiency check
            current_messages = await self._apply_sufficiency(
                user_message, iteration, all_tool_calls,
                current_messages, full_content, turn_tool_calls, tool_results
            )

        logger.warning(f"[PythonExecutor] Max iterations ({_iter_cap}) reached")
        return {
            "success": False,
            "response": "Maximum orchestration steps reached. The query might be too complex.",
            "toolCalls": all_tool_calls,
            "iterations": iteration,
            "error": "max_iterations_reached",
            "metadata": {"usage": {}, "model": model},
        }

    # ── Streaming execute ─────────────────────────────────────────────────────

    async def execute_chat_stream(
        self,
        user_message: str,
        history: Optional[List[Dict]] = None,
        system_prompt: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Streaming LLM loop — yields SSE event dicts.

        Yields:
          {"type": "status",  "message": "..."}
          {"type": "token",   "text": "..."}
          {"type": "result",  "response": "...", "toolCalls": [...], "metadata": {...}}
          {"type": "done"}
        """
        await self._init()
        history = filter_history(list(history or []), "mcp")
        self._current_user_message = user_message
        self._current_history = list(history)

        model = route_query(user_message)
        tools = self._tools or []

        current_messages = list(history)
        current_messages.append({"role": "user", "content": user_message})

        # Pre-loop intelligence — returns enriched system prompt + messages
        # (messages may have a reasoning assistant turn prepended)
        effective_system, current_messages = await self._run_pre_loop(
            user_message, model, tools, system_prompt, current_messages
        )

        yield {"type": "thinking", "event": "ready"}

        all_tool_calls: List[Dict] = []
        iteration = 0
        response_text = ""
        final_usage: Dict = {}
        _iter_cap = self._exec_state.effective_max_iterations

        while iteration < _iter_cap:
            iteration += 1
            logger.info(f"[PythonExecutor Stream] Iteration {iteration}/{_iter_cap}")

            current_messages = enforce_token_budget(model, current_messages, len(tools))

            full_content = ""
            turn_tool_calls: List[Dict] = []
            usage: Dict = {}

            gen = await stream_llm(
                messages=current_messages,
                tools=tools,
                system_prompt=effective_system,
                model=self._llm_model or model,
                provider=self._llm_provider or None,
                api_key=self._llm_api_key or None,
            )

            async for chunk in gen:
                if chunk["type"] == "token":
                    response_text += chunk["text"]
                    full_content += chunk["text"]
                    yield {"type": "token", "text": chunk["text"]}
                elif chunk["type"] == "tool_calls":
                    turn_tool_calls = chunk["tool_calls"]
                elif chunk["type"] == "done":
                    if not turn_tool_calls:
                        turn_tool_calls = chunk.get("tool_calls") or []
                    usage = chunk.get("usage") or {}
                    final_usage = usage

            if full_content.strip():
                logger.info(f"[PythonExecutor Stream] Iter {iteration} reasoning: {full_content.strip()[:400]}")

            # No tool calls → final answer streamed
            if not turn_tool_calls:
                logger.info("[PythonExecutor Stream] Final iteration — no tool calls")
                yield {"type": "thinking", "event": "answer_ready"}
                yield {
                    "type": "result",
                    "response": response_text,
                    "toolCalls": all_tool_calls,
                    "metadata": {"usage": _normalize_usage(usage), "model": model},
                    "success": True,
                }
                yield {"type": "done"}
                return

            # Tool-calling round
            yield {"type": "status", "message": f"Executing {len(turn_tool_calls)} tool call(s)..."}
            yield {"type": "thinking", "event": "tools_start", "count": len(turn_tool_calls)}
            for tc in turn_tool_calls:
                args_preview = str(tc.get("arguments") or tc.get("input") or {})[:150]
                logger.info(f"[PythonExecutor Stream] → tool: {tc.get('name')} | args: {args_preview}")

            tool_results, clarification = await self._execute_tool_calls(turn_tool_calls)

            if clarification:
                yield {
                    "type": "result",
                    "response": response_text,
                    "toolCalls": all_tool_calls,
                    "clarification": clarification,
                    "metadata": {"usage": {}, "model": model},
                    "success": True,
                }
                yield {"type": "done"}
                return

            all_tool_calls.extend(tool_results)

            # Sufficiency check
            current_messages = await self._apply_sufficiency(
                user_message, iteration, all_tool_calls,
                current_messages, full_content, turn_tool_calls, tool_results
            )

        logger.warning(f"[PythonExecutor Stream] Max iterations reached")
        yield {
            "type": "result",
            "response": response_text or "Maximum orchestration steps reached. The query might be too complex.",
            "toolCalls": all_tool_calls,
            "metadata": {"usage": _normalize_usage(final_usage), "model": model},
            "success": False,
            "error": "max_iterations_reached",
        }
        yield {"type": "done"}

    # ── Pre-loop intelligence ─────────────────────────────────────────────────

    async def _run_pre_loop(
        self,
        user_message: str,
        model: str,
        tools: List[Dict],
        base_system_prompt: Optional[str],
        messages: List[Dict],
    ) -> Tuple[str, List[Dict]]:
        """
        Run query planning + decomposer.

        Returns (effective_system_prompt, execution_messages).
        execution_messages may contain an injected reasoning assistant turn
        that the main loop must use instead of the original messages list.
        """
        effective_system = base_system_prompt or _build_default_system_prompt()
        execution_messages = list(messages)

        # Query planner
        plan_hint = None
        try:
            plan_hint = await plan_query(user_message, tools, self._llm_chat)
            if plan_hint:
                planned_tools = extract_tool_names_from_plan(plan_hint)
                response_fields_block = get_response_fields_for_tools(planned_tools)
                if response_fields_block:
                    effective_system += response_fields_block
                    logger.debug(f"[PythonExecutor] Response fields for {len(planned_tools)} tools")
                effective_system += plan_hint
                logger.info(f"[PythonExecutor] Query plan injected")
                # Build execution state from parsed plan — drives dynamic loop cap,
                # entity cache, runaway guard, and plan-aware sufficiency checks.
                self._exec_state = RequestExecutionState.from_plan(plan_hint, user_message)
        except Exception as e:
            logger.warning(f"[PythonExecutor] Query planning failed: {e}")

        # Reasoning turn: always runs — with plan review if a plan exists, else a
        # simple "think before acting" prompt. Result is injected as an assistant
        # message so the main loop inherits the LLM's corrected reasoning.
        # Mirrors Node.js executor.js lines 74-103.
        try:
            if plan_hint:
                review_instruction = (
                    "\n\nPLAN REVIEW: Before calling any tools, reason briefly (2-3 sentences): "
                    "Are all steps in the query plan above necessary? "
                    "Does any planned tool fetch data that is already available as a filter field on an earlier tool? "
                    "If the plan can be simplified, state the corrected approach. "
                    "Then stop — tool calls come next."
                )
            else:
                review_instruction = (
                    "\n\nTHINK BEFORE ACTING: Before calling any tools, briefly reason (2-3 sentences): "
                    "What does the user want? What is the minimal set of tool calls needed? "
                    "Then stop — tool calls come next."
                )
            reasoning_messages = [
                {"role": "system", "content": effective_system + review_instruction},
                *execution_messages,
            ]
            reasoning_text = await self._llm_chat(reasoning_messages, max_tokens=200)
            if reasoning_text.strip():
                logger.info(f"[PythonExecutor] Reasoning: {reasoning_text.strip()[:600]}")
                # Inject as assistant message — main loop sees it as prior context
                execution_messages = execution_messages + [
                    {"role": "assistant", "content": reasoning_text.strip()}
                ]
        except Exception as e:
            logger.warning(f"[PythonExecutor] Reasoning turn failed: {e}")

        # Query decomposer (disabled by default)
        try:
            decompose_result = await decompose_query(user_message, self._llm_chat)
            if decompose_result:
                effective_system += decompose_result["planning_hint"]
                logger.info(f"[PythonExecutor] Query decomposed into {len(decompose_result['sub_queries'])} sub-queries")
        except Exception as e:
            logger.warning(f"[PythonExecutor] Query decomposer failed: {e}")

        return effective_system, execution_messages

    async def _apply_sufficiency(
        self,
        user_message: str,
        iteration: int,
        all_tool_calls: List[Dict],
        current_messages: List[Dict],
        full_content: str,
        turn_tool_calls: List[Dict],
        tool_results: List[Dict],
    ) -> List[Dict]:
        """
        Run sufficiency check and update message list.
        Returns updated current_messages.

        Three special cases handled before the regular LLM sufficiency check:
        1. Runaway guard: a tool has been called ≥ threshold times → force stop
        2. Plan complete: all planned steps done → inject synthesis prompt, stop collecting
        3. Regular check: pass plan_context so checker knows pending steps
        """
        updated = self._build_messages_with_tool_results(
            current_messages, full_content, turn_tool_calls, tool_results
        )

        # Guard 1: Runaway tool loop — force final answer
        if self._exec_state.should_force_stop():
            logger.warning("[PythonExecutor] Runaway guard triggered — forcing final answer")
            updated.append({
                "role": "user",
                "content": "[System: Tool call limit reached. Please compose your final answer now from the data collected so far. If data is incomplete, state that clearly.]",
            })
            return updated

        # Guard 2: All planned steps complete — switch from data collection to synthesis
        if self._exec_state.all_steps_complete():
            logger.info("[PythonExecutor] All planned steps complete — requesting synthesis")
            updated.append({
                "role": "user",
                "content": (
                    "[System: All planned steps are complete. The data collection phase is finished. "
                    "Synthesize your final answer from the data collected — if any part of the data "
                    "is incomplete or missing, state that clearly in your response.]"
                ),
            })
            return updated

        # Regular sufficiency check with plan context
        if should_check_sufficiency(user_message, iteration, len(all_tool_calls)):
            try:
                plan_context = self._exec_state.pending_steps_summary() or None
                sufficient, missing = await check_sufficiency(
                    user_message, all_tool_calls, iteration, self._llm_chat,
                    plan_context=plan_context,
                )
                if sufficient:
                    logger.info("[PythonExecutor] Sufficiency: SUFFICIENT — requesting final answer")
                    updated.append({
                        "role": "user",
                        "content": "[System: The data collected so far is sufficient to answer the question. Please compose your final answer now without additional tool calls.]",
                    })
                elif missing:
                    logger.info(f"[PythonExecutor] Sufficiency: INSUFFICIENT — missing: {missing}")
                    updated.append({
                        "role": "user",
                        "content": f"[System: The data collected so far is NOT sufficient. Still missing: {missing}. Make more tool calls to get this data before composing your answer.]",
                    })
                return updated
            except Exception as e:
                logger.warning(f"[PythonExecutor] Sufficiency check error: {e}")

        return updated


# ── Module-level helpers ──────────────────────────────────────────────────────

def _build_clarification_response(
    err: AmbiguousResolutionError,
    user_message: str,
    history: List[Dict],
    tool_name: str,
    original_arguments: Dict[str, Any],
    resolved_filter_key: str,
    resolved_id_key: str,
) -> Dict[str, Any]:
    """
    Convert an AmbiguousResolutionError into the structure ClarificationForm.jsx
    expects, and store the session context so /mcp/clarify can resume the query.

    resolved_filter_key: the filter key that held the ambiguous name  (e.g. "Staff.Name")
    resolved_id_key:     the filter key to write the resolved ID into  (e.g. "Staff.ID")
    """
    session_id = f"mcp_{uuid.uuid4().hex}"

    # Map entity_resolver field names to ClarificationForm field names.
    # ClarificationForm uses these to convert the selected value back to an ID field
    # (e.g. "StaffName" → "StaffID" at line 117 of ClarificationForm.jsx).
    _FIELD_MAP = {
        "staff":       "StaffName",
        "customer":    "CustomerName",
        "contractor":  "ContractorName",
        "job":         "JobName",
        "section":     "SectionName",
        "cost_centre": "CostCentreName",
    }
    form_field = _FIELD_MAP.get(err.field, err.field)

    # Store session so /mcp/clarify/{session_id} can resume
    _mcp_pending_sessions[session_id] = {
        "created_at": time.time(),
        "user_message": user_message,
        "history": list(history),
        "tool_name": tool_name,
        "original_arguments": original_arguments,
        "resolved_filter_key": resolved_filter_key,
        "resolved_id_key": resolved_id_key,
        "form_field": form_field,
    }

    clarification_data = {
        "session_id": session_id,
        "agent": "mcp",
        "clarification_count": 1,
        "clarifications": [
            {
                "row": 1,
                "field": form_field,
                "type": "ambiguous",
                "message": str(err),
                "options": [{"id": m["id"], "name": m["name"]} for m in err.matches],
                "row_context": {
                    "tool": tool_name,
                    "searched_for": err.value,
                },
            }
        ],
    }
    return clarification_data


def _normalize_usage(usage: Dict) -> Dict:
    """Normalise usage dict keys to camelCase for backward compatibility."""
    if not usage:
        return {}
    return {
        "inputTokens": usage.get("input_tokens", 0),
        "outputTokens": usage.get("output_tokens", 0),
        "totalTokens": usage.get("total_tokens", 0),
        "cacheCreationTokens": usage.get("cache_creation_tokens", 0),
        "cacheReadTokens": usage.get("cache_read_tokens", 0),
        "model": usage.get("model", ""),
    }


def _build_default_system_prompt() -> str:
    """
    Default system prompt for the Python MCP executor.
    This is the same content as buildSystemPrompt() in routes/chat.js,
    but owned here so Node.js can be deprecated.
    """
    today = date.today().isoformat()
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    day_name = day_names[date.today().weekday()]

    return f"""You are a back-office assistant for Simpro ERP. Use the available tools to query, manage, and analyze business data.

Today's date is {today} ({day_name}). Resolve relative dates to actual YYYY-MM-DD dates. IMPORTANT: "this week" ALWAYS means Monday to Sunday of the current week (full 7 days) — start from Monday, NOT from today.

CRITICAL: Before responding, verify your data FULLY answers the question. If not, make more tool calls (up to 10 rounds).

REASONING DEPTH:
- LOOKUP (1 call): "show me job 123", "get invoice details" → fetch and return.
- DRILL-DOWN (2-3 calls): "per", "by", "breakdown" → get parent, then children.
- ANALYSIS (3+ calls): "which", "compare", "best/worst" → fetch all data, then analyze and conclude.

UNIVERSAL FILTERING (applies to ALL tools):
Every search/list tool accepts a 'filters' param. Any field in the tool's response data can be used as a server-side URL filter — including nested fields via dot notation.
- Parse EVERY qualifier in the user's request. Map each one to either a named parameter (type, date, is_paid, etc.) OR a response-field filter.
  Example: "contractor job schedules for tomorrow" → date='{today}' (named param), type='job' (named param), filters: {{"Staff.Type": "contractor"}}
  Example: "paid invoices for smith construction" → is_paid=true (named param), filters: {{"Customer.CompanyName": "%smith%"}}
- NEVER fetch all records when the user names a specific entity. If the user mentions a person, company, or entity by name, you MUST pass that name as a filter — fetching everything and filtering in context is FORBIDDEN.
- Name/text fields: ALWAYS use %keyword% wildcards. Extract the most distinctive word (e.g. "Nick Gubby" → filters: {{"Staff.Name": "%Gubby%"}}).
- STATUS/STAGE fields: Exact values — "Active", "Completed", "Pending".
- DATE fields: Use operators — ge(), lt(), between(). Never wildcards.
- If the query plan names a specific filter — FOLLOW IT. Do not second-guess whether a filter is supported; all response fields are filterable server-side.

TOOL SELECTION:
- Schedules: results include staff names — do NOT pre-lookup staff separately. Use date_from + date_to for ranges.
- Jobs: use filters for site, customer, status. For cost centres: get sections first → then cost centres.
- Invoices: always filter by job ID when user specifies a job. Cross-entity: find jobs by site → use job IDs to filter invoices.
- Contacts: for contact info ONLY. Do NOT pre-lookup contacts before schedule/job/invoice queries.

RULES:
- Match answer depth to question depth. "Which" questions need analysis, not data dumps.
- Prefer the most direct tool — avoid chaining through intermediate lookups.
- Use server-side filters to narrow results instead of fetching everything.
- Provide clear conclusions when analyzing — don't just dump raw data.

FOLLOW-UP CONTEXT:
When the message starts with [CONTEXT FROM PREVIOUS OPERATION: ...], use those pre-resolved entity references directly.

CROSS-PATH DATA: Conversation history may contain results from agent operations. Extract entity IDs from ANY history format.

ANALYTICAL RESPONSES:
- For profitability questions: compute the answer. State YES or NO first, then support with specific numbers.
- For "which" / "compare" questions: provide a direct conclusion, not raw data.

MULTI-STEP OPERATIONS (handoff_to_agent):
- Use handoff_to_agent ONLY for CREATE/UPDATE/DELETE. NEVER for read-only queries.
- ALWAYS gather specific entity IDs first via search/get tool calls, THEN handoff.
- Extract ALL gathered IDs and data into collected_data before calling handoff_to_agent."""


# ── Module-level singleton ────────────────────────────────────────────────────

_executor_pool: Dict[str, PythonMCPExecutor] = {}


def get_python_executor(
    user_id: Optional[int] = None,
    org_id: Optional[int] = None,
    request_id: Optional[str] = None,
    simpro_token: Optional[str] = None,
    simpro_url: Optional[str] = None,
    simpro_company_id: Optional[int] = None,
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
    llm_api_key: Optional[str] = None,
) -> PythonMCPExecutor:
    """Create a new PythonMCPExecutor per request (not a global singleton)."""
    return PythonMCPExecutor(
        user_id=user_id,
        org_id=org_id,
        request_id=request_id,
        simpro_token=simpro_token,
        simpro_url=simpro_url,
        simpro_company_id=simpro_company_id,
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_api_key=llm_api_key,
    )
