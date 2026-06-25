# backend/api/agent_handoff.py
"""
Agent Handoff Endpoint.

Called by the MCP Simpro server's HandoffToAgentTool when the MCP LLM
decides to delegate a CREATE/UPDATE/DELETE operation to a Python agent.

Flow:
  MCP LLM → handoff_to_agent tool → POST /api/agent-handoff (here)
  → _run_agent() → agent does entity resolution + CRUD via MCPToolExecutor
  → result returned to MCP LLM

Session state fix (N2 from test simulation):
  When an agent returns needs_clarification, we register the session_id
  in chat.py's _pending_sessions dict so that the user's next reply
  can resume the correct agent session. The handoff response includes
  the session_id so the MCP LLM can surface it in its response and the
  frontend can render the clarification form.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Agent Handoff"])


# ── Request / Response models ────────────────────────────────────────────────

class HandoffRequest(BaseModel):
    agent: str                              # "schedule" | "invoice" | "workorder" | "purchase_order"
    action: str                             # "create" | "update" | "delete"
    context: str                            # Natural-language request with IDs
    collected_data: Optional[Dict[str, Any]] = None  # Pre-fetched entity data
    org_id: Optional[int] = None            # Passed from PythonMCPExecutor — used to resolve per-org LLM key


class HandoffResponse(BaseModel):
    success: bool
    message: str = ""
    data: Optional[Any] = None
    needs_clarification: bool = False
    clarification_data: Optional[Dict[str, Any]] = None
    # Session ID for clarification resumption — frontend/MCP LLM must
    # preserve this so the user's next reply can continue the chain.
    session_id: Optional[str] = None
    # Chain metadata: agents may return extra structured data for the LLM
    chain_meta: Optional[Dict[str, Any]] = None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _extract_chain_meta(agent_name: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract key IDs and objects from agent results for the MCP LLM.

    The MCP LLM sees this as the tool result and can pass relevant IDs
    to subsequent tool calls (e.g., pass schedule_id to workorder handoff).
    """
    meta: Dict[str, Any] = {}

    if agent_name == "schedule":
        # Extract created/updated schedule IDs for downstream use
        schedules = result.get("schedules", [])
        if schedules:
            meta["schedule_ids"] = [s.get("ID") or s.get("id") for s in schedules if s.get("ID") or s.get("id")]
            # Pull useful fields from first schedule
            first = schedules[0] if isinstance(schedules[0], dict) else {}
            for field in ("job_id", "quote_id", "section_id", "cost_centre_id", "staff_id", "date"):
                if first.get(field):
                    meta[field] = first[field]

    elif agent_name == "workorder":
        jobs = result.get("contractor_jobs", [])
        if jobs:
            meta["contractor_job_ids"] = [j.get("ID") or j.get("id") for j in jobs if j.get("ID") or j.get("id")]
            first = jobs[0] if isinstance(jobs[0], dict) else {}
            for field in ("contractor_id", "job_id", "section_id", "cost_centre_id"):
                if first.get(field):
                    meta[field] = first[field]

    elif agent_name == "invoice":
        invoice_results = result.get("jobs", result.get("invoice_results", []))
        if invoice_results:
            invoice_ids = []
            for job in invoice_results:
                if isinstance(job, dict):
                    inv_id = job.get("invoice_id") or job.get("InvoiceID") or job.get("ID")
                    if inv_id:
                        invoice_ids.append(inv_id)
            if invoice_ids:
                meta["invoice_ids"] = invoice_ids

    elif agent_name == "purchase_order":
        pos = result.get("purchase_orders", [])
        if pos:
            meta["purchase_order_ids"] = [
                p.get("po_id") or p.get("ID") for p in pos
                if p.get("po_id") or p.get("ID")
            ]
            first = pos[0] if isinstance(pos[0], dict) else {}
            for field in ("supplier_id", "job_id", "section_id", "cost_centre_id"):
                if first.get(field):
                    meta[field] = first[field]

    return meta


def _build_hints(
    action: str,
    collected_data: Dict[str, Any],
    company_id: int = 2,
) -> Dict[str, Any]:
    """
    Build hints dict for _run_agent().

    Maps collected_data fields to the standard hint keys that each agent
    already understands, plus stores the full collected_data under
    pre_resolved for agents to do granular skip checks.
    """
    hints: Dict[str, Any] = {
        "CompanyID": company_id,
        "action": action,
    }

    if not collected_data:
        return hints

    # Store full collected data so agents can check any field
    hints["pre_resolved"] = collected_data

    # Also map top-level known fields for backward compatibility with
    # agents that check hints["contractor_id"] directly
    for field in (
        "contractor_id", "job_id", "quote_id",
        "section_id", "cost_centre_id",
        "staff_id", "schedule_id",
        "contractor_job_id", "invoice_id",
        "materials", "labour", "schedules",
        "date", "start_time", "end_time", "blocks",
    ):
        if field in collected_data:
            hints[field] = collected_data[field]

    return hints


# ── Main endpoint ─────────────────────────────────────────────────────────────

@router.post("/agent-handoff", response_model=HandoffResponse)
async def agent_handoff(request: HandoffRequest) -> HandoffResponse:
    """
    Receive a handoff from the MCP tool-calling loop and run the named agent.

    The MCP LLM calls this after gathering context via prior tool calls.
    Returns a structured result the LLM can include in its final answer.
    """
    # Import here to avoid circular imports (chat.py imports from api/)
    from api.chat import _run_agent, _pending_sessions, _get_org_llm_config
    from utils.llm import chat_with_override

    agent_name = request.agent.lower().strip()
    action = request.action.lower().strip()
    context = request.context.strip()
    collected_data = request.collected_data or {}

    # Build per-org LLM fn — uses org key from DB if available, falls back to global .env
    _org_llm = _get_org_llm_config(request.org_id)
    _slot = _org_llm["primary"]
    def llm_chat(messages, response_format=None, temperature=0.0, **kw):
        kw.pop("complexity", None)
        return chat_with_override(
            messages, response_format=response_format, temperature=temperature,
            provider=_slot.get("provider"), model=_slot.get("model"),
            api_key=_slot.get("api_key"), **kw,
        )

    logger.info(
        f"[AgentHandoff] agent={agent_name} action={action} "
        f"context={context[:80]} collected_keys={list(collected_data.keys())}"
    )

    # Validate agent name
    valid_agents = {"schedule", "invoice", "workorder", "purchase_order"}
    if agent_name not in valid_agents:
        return HandoffResponse(
            success=False,
            message=f"Unknown agent '{agent_name}'. Valid: {', '.join(sorted(valid_agents))}.",
        )

    # Build hints with pre_resolved data
    hints = _build_hints(action, collected_data, company_id=2)

    try:
        result = await _run_agent(
            agent_name=agent_name,
            user_text=context,
            attachments=[],
            conversation_history=[],      # MCP loop already has conversation context
            intent_action=action,
            intent_follow_up=False,
            current_user=None,            # No user session — chain context only
            llm_chat_fn=llm_chat,
            session_context=hints,        # Pre-resolved data injected here
        )
    except Exception as e:
        logger.error(f"[AgentHandoff] Agent '{agent_name}' raised exception: {e}", exc_info=True)
        return HandoffResponse(
            success=False,
            message=f"Agent '{agent_name}' encountered an error: {str(e)}",
        )

    success = result.get("success", False)
    needs_clarification = bool(result.get("needs_clarification"))
    session_id = result.get("session_id")

    # ── N2 Fix: Register session in _pending_sessions ─────────────────
    # When the agent needs clarification, store its full session so that
    # the user's next reply (routed through chat.py) can resume correctly.
    if needs_clarification and session_id:
        _pending_sessions[session_id] = {
            "created_at": time.time(),
            "agent_name": agent_name,
            "user_text": context,
            "extracted": result.get("original_extracted"),
            "hints": hints,
            "company_id": 2,
            "_from_handoff": True,          # Flag so chat.py knows this came via handoff
            "_handoff_collected_data": collected_data,
        }
        # Persist workorder-specific payload state
        if agent_name == "workorder":
            for key in ("_clean_payloads", "_pending_payloads", "_existing_map"):
                if result.get(key) is not None:
                    _pending_sessions[session_id][key] = result[key]
        # Persist invoice-specific state
        if agent_name == "invoice":
            for key in ("_policy", "_chat_result"):
                if result.get(key) is not None:
                    _pending_sessions[session_id][key] = result[key]
        # Persist purchase order review rows for Phase B
        if agent_name == "purchase_order":
            for key in ("wo_review_rows", "original_extracted"):
                if result.get(key) is not None:
                    _pending_sessions[session_id][key] = result[key]
        if result.get("clarifications"):
            _pending_sessions[session_id]["_last_agent_result"] = {
                "clarifications": result["clarifications"]
            }
        logger.info(f"[AgentHandoff] Registered clarification session: {session_id}")

    # Build chain_meta for MCP LLM — key IDs from this operation
    chain_meta = _extract_chain_meta(agent_name, result) if success else {}

    # Build a clean message for the MCP LLM to incorporate into its response
    message = result.get("message", "")
    if not message:
        if success:
            message = f"{agent_name.title()} operation completed successfully."
        elif needs_clarification:
            message = result.get("clarifications", [{}])[0].get("message", "Clarification needed.")
        else:
            message = result.get("error", "Operation failed.")

    return HandoffResponse(
        success=success,
        message=message,
        data=_safe_data(result, agent_name),
        needs_clarification=needs_clarification,
        clarification_data=result.get("clarification_data") if needs_clarification else None,
        session_id=session_id if needs_clarification else None,
        chain_meta=chain_meta if chain_meta else None,
    )


def _safe_data(result: Dict[str, Any], agent_name: str) -> Optional[Any]:
    """Extract the relevant output data from agent result."""
    if agent_name == "schedule":
        return result.get("schedules")
    if agent_name == "workorder":
        return result.get("contractor_jobs") or result.get("wo_review_rows")
    if agent_name == "invoice":
        return result.get("jobs") or result.get("invoice_updates") or result.get("invoice_deletes")
    if agent_name == "purchase_order":
        return result.get("purchase_orders") or result.get("wo_review_rows")
    return None


# ── Agent registry endpoint (used by HandoffToAgentTool at startup) ──────────

@router.get("/agents/registry")
async def get_agent_registry() -> Dict[str, Any]:
    """Return agent registry metadata for the handoff tool's dynamic description."""
    from agents.registry import AGENT_REGISTRY
    return {
        "agents": {
            name: {
                "title": entry.get("title", name),
                "responsibility": entry.get("responsibility", ""),
            }
            for name, entry in AGENT_REGISTRY.items()
            if entry.get("enabled", True)
        }
    }
