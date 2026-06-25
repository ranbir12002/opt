# mcp-simpro-server/src/tools/handoff.py
"""
Agent Handoff Tool.

Allows the MCP LLM to delegate CREATE/UPDATE/DELETE operations to a
specialized Python-side agent (schedule, invoice, workorder) after
gathering the required context via prior tool calls.

Design:
- Single tool (not per-agent) to avoid token bloat and tool confusion
- LLM passes collected_data with pre-fetched IDs/objects so agents skip
  duplicate lookups
- Agent list is fetched dynamically from the backend at startup so new
  agents are auto-discovered without changing this file
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

import httpx

from .base import BaseTool

logger = logging.getLogger(__name__)

# Backend URL — same env var used by the Node.js MCP client
BACKEND_URL = os.getenv("PYTHON_BACKEND_URL", "http://localhost:8001")

# Timeout for agent handoff calls — agents can take 30-60s for complex resolution
_HANDOFF_TIMEOUT = 90.0

# Agent registry cache — populated once at first execute() call
_AGENT_REGISTRY: Optional[Dict[str, Dict[str, str]]] = None


async def _fetch_agent_registry() -> Dict[str, Dict[str, str]]:
    """Fetch available agents from the backend at runtime."""
    global _AGENT_REGISTRY
    if _AGENT_REGISTRY is not None:
        return _AGENT_REGISTRY
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{BACKEND_URL}/api/agents/registry")
            if resp.status_code == 200:
                data = resp.json()
                _AGENT_REGISTRY = data.get("agents", {})
                logger.info(f"[Handoff] Fetched agent registry: {list(_AGENT_REGISTRY.keys())}")
                return _AGENT_REGISTRY
    except Exception as e:
        logger.warning(f"[Handoff] Could not fetch agent registry: {e} — using built-in defaults")
    # Fallback to known agents if backend unavailable
    _AGENT_REGISTRY = {
        "schedule": {
            "title": "Schedule Agent",
            "responsibility": "Create/update/delete work schedules for staff on jobs or quotes.",
        },
        "invoice": {
            "title": "Invoice Agent",
            "responsibility": "Create/update/delete invoices for jobs.",
        },
        "workorder": {
            "title": "Work Order Agent",
            "responsibility": "Create/update/delete contractor work orders from cost centres.",
        },
    }
    return _AGENT_REGISTRY


def _build_description(agents: Dict[str, Dict[str, str]]) -> str:
    agent_lines = "\n".join(
        f'  - "{name}": {info.get("responsibility", info.get("title", ""))}'
        for name, info in agents.items()
    )
    return (
        "Delegate a CREATE, UPDATE, or DELETE operation to a specialized agent.\n\n"
        "USE THIS TOOL when:\n"
        "- You have gathered sufficient context (entity IDs, names, dates) via prior tool calls\n"
        "- The user wants to mutate data (create/update/delete records)\n"
        "- The operation requires business-rule enforcement (entity resolution, SOP compliance,\n"
        "  bulk processing, or multi-step clarification)\n\n"
        "DO NOT use for read-only queries — use search/get tools instead.\n\n"
        "IMPORTANT — gather first, handoff second:\n"
        "1. Search/resolve all relevant entities (jobs, staff, contractors) via tool calls first\n"
        "2. Extract those IDs into collected_data\n"
        "3. Then call handoff_to_agent with specific IDs in both context and collected_data\n"
        "   (e.g. 'Schedule John ID 44 on job 22601 for 2026-03-14 7am-3pm')\n\n"
        f"Available agents:\n{agent_lines}"
    )


class HandoffToAgentTool(BaseTool):
    """
    Delegates CRUD operations to specialized Python agents.

    This is the bridge between the MCP tool-calling loop (read-heavy) and
    the agent layer (write-heavy with entity resolution + business logic).
    """

    # Never inject universal filters — this is a mutation dispatcher
    _supports_filters = False

    def get_name(self) -> str:
        return "handoff_to_agent"

    def get_description(self) -> str:
        # Static description used at registration time (before async registry fetch)
        return _build_description({
            "schedule": {"responsibility": "Create/update/delete work schedules for staff on jobs or quotes."},
            "invoice":  {"responsibility": "Create/update/delete invoices for jobs."},
            "workorder": {"responsibility": "Create/update/delete contractor work orders from cost centres."},
        })

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "enum": ["schedule", "invoice", "workorder"],
                    "description": (
                        "Which agent to delegate to. "
                        "'schedule' for scheduling staff, "
                        "'invoice' for invoice operations, "
                        "'workorder' for contractor work orders."
                    ),
                },
                "action": {
                    "type": "string",
                    "enum": ["create", "update", "delete"],
                    "description": "The operation type.",
                },
                "context": {
                    "type": "string",
                    "description": (
                        "Specific natural-language request including ALL entity IDs and details "
                        "gathered from prior tool calls. Be concrete — include IDs, names, dates, times. "
                        "Example: 'Schedule contractor Tarun (ID 999) on job 22601 (Saint Street) "
                        "for 2026-03-14, 7am to 3pm, roofing cost centre (ID 789).'"
                    ),
                },
                "collected_data": {
                    "type": "object",
                    "description": (
                        "Structured data already fetched via prior tool calls. "
                        "Pass anything that the agent would otherwise have to re-fetch: "
                        "contractor_id, job_id, section_id, cost_centre_id, schedule_id, "
                        "materials (list), labour (list), schedule objects, etc. "
                        "The agent checks this BEFORE making MCP tool calls — "
                        "pass it to avoid duplicate network round-trips."
                    ),
                    "additionalProperties": True,
                },
            },
            "required": ["agent", "action", "context"],
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        agent = arguments.get("agent", "").strip()
        action = arguments.get("action", "create").strip()
        context = arguments.get("context", "").strip()
        collected_data = arguments.get("collected_data") or {}

        logger.info(
            f"[Handoff] agent={agent} action={action} "
            f"context={context[:80]} collected_keys={list(collected_data.keys())}"
        )

        if not agent or not context:
            return {
                "success": False,
                "error": "INVALID_ARGS",
                "message": "Both 'agent' and 'context' are required.",
            }

        payload = {
            "agent": agent,
            "action": action,
            "context": context,
            "collected_data": collected_data,
            "org_id": arguments.get("org_id"),  # injected by PythonMCPExecutor — used for per-org LLM routing
        }

        try:
            async with httpx.AsyncClient(timeout=_HANDOFF_TIMEOUT) as client:
                resp = await client.post(
                    f"{BACKEND_URL}/api/agent-handoff",
                    json=payload,
                )
                resp.raise_for_status()
                result = resp.json()
                logger.info(
                    f"[Handoff] Backend response: success={result.get('success')} "
                    f"needs_clarification={result.get('needs_clarification')}"
                )
                return result

        except httpx.TimeoutException:
            logger.error(f"[Handoff] Timeout calling backend for agent={agent}")
            return {
                "success": False,
                "error": "TIMEOUT",
                "message": f"Agent '{agent}' took too long to respond. Try a simpler request.",
            }
        except httpx.HTTPStatusError as e:
            logger.error(f"[Handoff] HTTP error {e.response.status_code}: {e.response.text[:200]}")
            return {
                "success": False,
                "error": "HTTP_ERROR",
                "message": f"Backend returned {e.response.status_code}.",
            }
        except Exception as e:
            logger.error(f"[Handoff] Unexpected error: {e}")
            return {
                "success": False,
                "error": "INTERNAL_ERROR",
                "message": f"Handoff failed: {str(e)}",
            }
