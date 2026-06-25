# Chatbox_mcp/backend/agents/workorder_proxy.py
"""
Proxy to the Work Order Agent library (svc-agent-workorder).

This proxy:
1. Dynamically imports the workorder agent from ../../../svc-agent-workorder
2. Wraps LLM calls with PII filtering
3. Injects MCP tool executor (HTTP-based)
4. Returns agent result to chat.py
"""

from __future__ import annotations
import importlib.util
import json
import sys
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from utils.pii_filter import sanitize_for_llm
from utils.mcp_tool_client import MCPToolClient, get_mcp_tool_client

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# Locate and Import svc-agent-workorder
# ═══════════════════════════════════════════════════════════════════════════

_here = Path(__file__).resolve()
# Navigate to repo root: backend/agents → backend → Chatbox_mcp → optificial
repo_root = _here.parents[3]
svc_src = repo_root / "svc-agent-workorder" / "src"
svc_file = svc_src / "wo_agent.py"

if not svc_file.exists():
    raise ImportError(f"svc-agent-workorder not found at: {svc_file}")

# Add svc-agent-workorder/src to sys.path so its imports resolve correctly
if str(svc_src) not in sys.path:
    sys.path.insert(0, str(svc_src))

MODULE_NAME = "svc_workorder_agent"

# Temporarily remove conflicting 'config' module to avoid import collision
_saved_config = sys.modules.pop('config', None)

try:
    # Load the module dynamically
    spec = importlib.util.spec_from_file_location(MODULE_NAME, str(svc_file))
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load spec for {svc_file}")

    svc_mod = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = svc_mod
    spec.loader.exec_module(svc_mod)
finally:
    # Restore the original config module
    if _saved_config is not None:
        sys.modules['config'] = _saved_config

# Import the main function and executor class
run_workorder_agent_fn = svc_mod.run_workorder_agent
MCPToolExecutor = svc_mod.MCPToolExecutor

from utils.resolution_context import RequestTracker

logger.info(f"✅ Loaded workorder agent from: {svc_file}")


# ═══════════════════════════════════════════════════════════════════════════
# Proxy Function
# ═══════════════════════════════════════════════════════════════════════════

async def run_workorder_agent(
    llm_chat: Callable,
    user_text: str,
    registry_entry: Dict[str, Any],
    extracted: Optional[Dict[str, Any]] = None,
    raw_attachments: Optional[Any] = None,
    any_uploaded_text: Optional[str] = None,
    hints: Optional[Dict[str, Any]] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """
    Proxy from Chatbox backend to the Work Order Agent library.

    Args:
        llm_chat: LLM chat function from backend (centralized config)
        user_text: User's message
        registry_entry: Agent registry config
        extracted: Structured data from svc-extractor
        raw_attachments: Raw file data
        any_uploaded_text: CSV text fallback
        hints: Hints dict (e.g., CompanyID)
        conversation_history: Recent conversation messages

    Returns:
        Agent result dict
    """

    logger.info("=" * 70)
    logger.info("🔗 Work Order Proxy: Starting")
    logger.info(f"User text: {user_text[:80]}")
    logger.info(f"Has extracted: {bool(extracted)}")
    logger.info("=" * 70)

    # Wrap LLM chat with PII filtering (same as schedule proxy)
    def _safe_llm_chat(
        messages: List[Dict[str, str]],
        response_format=None,
        temperature: float = 0.0,
        **kwargs
    ) -> str:
        """Sanitize JSON content before sending to external LLM."""
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
        return llm_chat(
            cleaned,
            response_format=response_format,
            temperature=temperature,
            sanitize=not skip_sanitize,
            **kwargs,
        )

    # Create MCP executor using HTTP client
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

    # Build executor that the workorder agent expects
    mcp_executor = MCPToolExecutor(
        tool_registry=mcp_client,
        company_id=company_id,
        tracker=tracker,
    )

    logger.info(f"🏢 Using CompanyID: {company_id}")

    # SOP override injection (Phase 5)
    _org_id = (hints or {}).get("org_id")
    if _org_id:
        try:
            from auth.database import get_org_sop
            _sop_row = get_org_sop(_org_id, "workorder")
            if _sop_row:
                hints = {**(hints or {}), "sop_override": _sop_row["sop_text"]}
                logger.info(f"[SOP] Org {_org_id}: custom SOP found for 'workorder' ({len(_sop_row['sop_text'])} chars) — injecting override")
            else:
                logger.info(f"[SOP] Org {_org_id}: no custom SOP for 'workorder' — will use default file")
        except Exception as _e:
            logger.warning(f"[Phase5] SOP lookup failed: {_e}")

    # Call the agent
    try:
        result = await run_workorder_agent_fn(
            llm_chat=_safe_llm_chat,
            user_text=user_text,
            extracted=extracted,
            any_uploaded_text=any_uploaded_text,
            hints=hints,
            mcp_executor=mcp_executor,
            conversation_history=conversation_history,
        )

        logger.info(f"✅ Workorder agent completed: success={result.get('success')}, phase={result.get('phase')}")
        return result

    except Exception as e:
        logger.error(f"❌ Workorder agent error: {e}", exc_info=True)
        return {
            "success": False,
            "error": "AGENT_ERROR",
            "message": f"Work order agent failed: {str(e)}"
        }
