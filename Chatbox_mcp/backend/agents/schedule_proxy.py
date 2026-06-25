# Chatbox_mcp/backend/agents/schedule_proxy.py
"""
Proxy to the Schedule Agent library (svc-agent-schedule).

This proxy:
1. Dynamically imports the schedule agent from ../../../svc-agent-schedule
2. Wraps LLM calls with PII filtering
3. Injects MCP tool executor (HTTP-based) for ID resolution
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
# Locate and Import svc-agent-schedule
# ═══════════════════════════════════════════════════════════════════════════

_here = Path(__file__).resolve()
# Navigate to repo root: backend/agents → backend → Chatbox_mcp → optificial
repo_root = _here.parents[3]
svc_src = repo_root / "svc-agent-schedule" / "src"
svc_file = svc_src / "schedule_agent.py"

if not svc_file.exists():
    raise ImportError(f"svc-agent-schedule not found at: {svc_file}")

# Add svc-agent-schedule/src to sys.path so its imports resolve correctly
if str(svc_src) not in sys.path:
    sys.path.insert(0, str(svc_src))

MODULE_NAME = "svc_schedule_agent"

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
run_schedule_agent_fn = svc_mod.run_schedule_agent
MCPToolExecutor = svc_mod.MCPToolExecutor

from utils.resolution_context import RequestTracker

logger.info(f"✅ Loaded schedule agent from: {svc_file}")


# ═══════════════════════════════════════════════════════════════════════════
# Proxy Function
# ═══════════════════════════════════════════════════════════════════════════

async def run_schedule_agent(
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
    Proxy from Chatbox backend to the Schedule Agent library.

    Args:
        llm_chat: LLM chat function from backend (centralized config)
        user_text: User's message
        registry_entry: Agent registry config
        extracted: Structured data from svc-extractor
        raw_attachments: Raw file data (not used for schedule agent)
        any_uploaded_text: CSV text fallback (not used for schedule agent)
        hints: Hints dict (e.g., CompanyID)

    Returns:
        Agent result dict
    """

    logger.info("=" * 70)
    logger.info("🔗 Schedule Proxy: Starting")
    logger.info(f"User text: {user_text[:80]}")
    logger.info(f"Has extracted: {bool(extracted)}")
    logger.info("=" * 70)

    # Wrap LLM chat with PII filtering (same as invoice proxy)
    def _safe_llm_chat(
        messages: List[Dict[str, str]],
        response_format=None,
        temperature: float = 0.0,
        **kwargs
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

    # Create MCP executor using HTTP client (no direct tool imports)
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

    # Create request tracker for full context awareness in crossroads
    tracker = RequestTracker(
        user_question=user_text,
        conversation_history=conversation_history,
    )

    # Build executor that the schedule agent expects
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
            _sop_row = get_org_sop(_org_id, "schedule")
            if _sop_row:
                hints = {**(hints or {}), "sop_override": _sop_row["sop_text"]}
                logger.info(f"[SOP] Org {_org_id}: custom SOP found for 'schedule' ({len(_sop_row['sop_text'])} chars) — injecting override")
            else:
                logger.info(f"[SOP] Org {_org_id}: no custom SOP for 'schedule' — will use default file")
        except Exception as _e:
            logger.warning(f"[Phase5] SOP lookup failed: {_e}")

    # Call the agent
    try:
        result = await run_schedule_agent_fn(
            llm_chat=_safe_llm_chat,
            user_text=user_text,
            extracted=extracted,
            any_uploaded_text=any_uploaded_text,
            hints=hints,
            mcp_executor=mcp_executor,
            conversation_history=conversation_history,
        )

        logger.info(f"✅ Schedule agent completed: success={result.get('success')}")
        return result

    except Exception as e:
        logger.error(f"❌ Schedule agent error: {e}", exc_info=True)
        return {
            "success": False,
            "error": "AGENT_ERROR",
            "message": f"Schedule agent failed: {str(e)}"
        }
