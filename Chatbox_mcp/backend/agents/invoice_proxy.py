# Chatbox_mcp/backend/agents/invoice_proxy.py
from __future__ import annotations
import importlib.util
import inspect
import json
import logging
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from utils.mcp_tool_client import MCPToolClient, get_mcp_tool_client
from utils.pii_filter import sanitize_for_llm
from utils.resolution_context import RequestTracker

logger = logging.getLogger(__name__)

# ----------------------------
# Locate and import svc-agent-invoice
# ----------------------------
_here = Path(__file__).resolve()
# repo_root = <Optificial-AI-master>
repo_root = _here.parents[3]
svc_src = repo_root / "svc-agent-invoice" / "src"
svc_file = svc_src / "invoice_agent.py"

if not svc_file.exists():
    raise ImportError(f"svc-agent-invoice not found at: {svc_file}")

MODULE_NAME = "svc_invoice_agent"

# load the svc module by path (works regardless of sys.path state)
spec = importlib.util.spec_from_file_location(MODULE_NAME, str(svc_file))
if spec is None or spec.loader is None:
    raise ImportError(f"Could not load spec for {svc_file}")
svc_mod = importlib.util.module_from_spec(spec)  # type: ignore

sys.modules[MODULE_NAME] = svc_mod

spec.loader.exec_module(svc_mod)  # type: ignore

# Accept any of these function names from the svc. NOTE: includes run_invoice_agent.
_CANDIDATES = [
    "create_invoice_via_llm",
    "create_invoices_via_llm",
    "create_invoice",
    "run_invoice",
    "run",
    "run_invoice_agent",
]


def _resolve_callable(mod):
    names = [n for n in dir(mod) if not n.startswith("_")]
    for name in _CANDIDATES:
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn
    raise ImportError(
        "svc-agent-invoice/src/invoice_agent.py does not expose a known callable.\n"
        f"Expected one of: {', '.join(_CANDIDATES)}\n"
        f"Found top-level names: {', '.join(names)}"
    )


# This will usually be invoice_agent.run_invoice_agent(...)
_svc_fn = _resolve_callable(svc_mod)

# Get MCPToolExecutor from the svc module (defined in invoice_agent.py)
MCPToolExecutor = getattr(svc_mod, "MCPToolExecutor", None)
if MCPToolExecutor is None:
    logger.warning("MCPToolExecutor not found in invoice_agent.py — MCP validation disabled")


async def _normalize_and_call(
    fn: Callable[..., Dict[str, Any]],
    llm_chat,
    user_text: str,
    extracted: Optional[Dict[str, Any]] = None,
    raw_attachments: Optional[Any] = None,
    any_uploaded_text: Optional[str] = None,
    hints: Optional[Dict[str, Any]] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    tracker: Optional[Any] = None,
    mcp_executor: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Normalizes arguments based on the target function's signature.
    This lets the same proxy work when the underlying function
    doesn't declare all optional parameters.
    """
    sig = inspect.signature(fn)
    kwargs: Dict[str, Any] = {}

    # required / common params
    if "llm_chat" in sig.parameters:
        kwargs["llm_chat"] = llm_chat
    if "user_text" in sig.parameters:
        kwargs["user_text"] = user_text

    # optional uploaded/pasted text
    if "any_uploaded_text" in sig.parameters:
        kwargs["any_uploaded_text"] = any_uploaded_text

    # IMPORTANT: invoice_agent.run_invoice_agent uses "attachments"
    # as the text that gets fed into the LLM helper.
    # So if it has an 'attachments' param, give it the same text.
    if "attachments" in sig.parameters:
        kwargs["attachments"] = any_uploaded_text

    # optional hints
    if "hints" in sig.parameters:
        kwargs["hints"] = hints

    # extractor-first inputs
    if "extracted" in sig.parameters:
        kwargs["extracted"] = extracted

    if "raw_attachments" in sig.parameters:
        kwargs["raw_attachments"] = raw_attachments

    if "conversation_history" in sig.parameters:
        kwargs["conversation_history"] = conversation_history

    if "tracker" in sig.parameters:
        kwargs["tracker"] = tracker

    if "mcp_executor" in sig.parameters:
        kwargs["mcp_executor"] = mcp_executor

    result = fn(**kwargs)
    # Await if the result is a coroutine (async agent)
    if hasattr(result, "__await__"):
        result = await result
    return result


async def run_invoice_agent(
    llm_chat,
    user_text: str,
    registry_entry: Dict[str, Any],
    extracted: Optional[Dict[str, Any]] = None,
    raw_attachments: Optional[Any] = None,
    any_uploaded_text: Optional[str] = None,
    hints: Optional[Dict[str, Any]] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """
    Proxy from Chatbox to the Invoice Agent *library* (not HTTP service).

    - llm_chat comes from the main chat orchestrator
    - user_text is the last user message ("create invoice for the attached data")
    - any_uploaded_text is a CSV/text representation of the uploaded Excel (or None)
    - hints is a JSON-like dict of extracted hints if you add them later
    - registry_entry is kept for future config, but not required for now
    """
    # Wrap llm_chat so that any JSON user-message content is sanitized
    # before reaching the external LLM.  The invoice agent sends
    # attachment summaries containing real data — this strips PII.
    def _safe_llm_chat(
        messages: List[Dict[str, str]],
        response_format=None,
        temperature: float = 0.0,
        **kwargs,
    ) -> str:
        """Sanitize JSON content before sending to external LLM.

        Pass sanitize=False to skip PII filtering (e.g. for crossroads
        calls that only contain operational metadata, not client data).
        """
        skip_sanitize = kwargs.pop("sanitize", None) is False

        cleaned: List[Dict[str, str]] = []
        for msg in messages:
            content = msg.get("content", "")
            if not skip_sanitize and isinstance(content, str):
                stripped = content.strip()
                if stripped and stripped[0] in ("{", "["):
                    try:
                        parsed = json.loads(stripped)
                        sanitized = sanitize_for_llm(parsed)
                        content = json.dumps(sanitized, ensure_ascii=False, default=str)
                    except (json.JSONDecodeError, TypeError):
                        pass
            cleaned.append({**msg, "content": content})
        # Pass sanitize=False to llm.chat() when we already handled it (or skipped it)
        return llm_chat(cleaned, response_format=response_format, temperature=temperature, sanitize=not skip_sanitize, **kwargs)

    # Create MCP executor using HTTP client (same pattern as schedule/workorder proxies)
    mcp_executor = None
    if MCPToolExecutor is not None:
        company_id = hints.get("CompanyID", 2) if hints else 2
        _token = hints.get("simpro_token") if hints else None
        _url = hints.get("simpro_url") if hints else None
        _cid = hints.get("simpro_company_id") if hints else None
        if _token and _url:
            mcp_client = MCPToolClient(simpro_token=_token, simpro_url=_url, simpro_company_id=_cid)
        else:
            mcp_client = get_mcp_tool_client()

        # Discover available tools from MCP server
        try:
            tool_names = await mcp_client.get_tool_names()
            logger.info(f"🔧 MCP tools available via HTTP: {tool_names}")
        except Exception as e:
            logger.error(f"❌ Failed to discover MCP tools: {e}")
            tool_names = []

        # Create request tracker for full context awareness
        tracker = RequestTracker(
            user_question=user_text,
            conversation_history=conversation_history,
        )

        # Build executor that the invoice agent expects
        mcp_executor = MCPToolExecutor(
            tool_registry=mcp_client,
            company_id=company_id,
            tracker=tracker,
        )

        logger.info(f"🏢 Using CompanyID: {company_id}")
    else:
        # Fallback: create tracker without MCP executor
        tracker = RequestTracker(
            user_question=user_text,
            conversation_history=conversation_history,
        )

    # SOP override injection (Phase 5)
    _org_id = (hints or {}).get("org_id")
    if _org_id:
        try:
            from auth.database import get_org_sop
            _sop_row = get_org_sop(_org_id, "invoice")
            if _sop_row:
                hints = {**(hints or {}), "sop_override": _sop_row["sop_text"]}
                logger.info(f"[SOP] Org {_org_id}: custom SOP found for 'invoice' ({len(_sop_row['sop_text'])} chars) — injecting override")
            else:
                logger.info(f"[SOP] Org {_org_id}: no custom SOP for 'invoice' — will use default file")
        except Exception as _e:
            logger.warning(f"[Phase5] SOP lookup failed: {_e}")

    result = await _normalize_and_call(
        _svc_fn,
        llm_chat=_safe_llm_chat,
        user_text=user_text,
        extracted=extracted,
        raw_attachments=raw_attachments,
        any_uploaded_text=any_uploaded_text,
        hints=hints,
        conversation_history=conversation_history,
        tracker=tracker,
        mcp_executor=mcp_executor,
    )

    return result
