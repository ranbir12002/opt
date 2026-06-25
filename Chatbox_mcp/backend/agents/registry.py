# Chatbox_mcp/backend/agents/registry.py
from __future__ import annotations
import importlib
import logging
from typing import Callable, Dict, Any, Optional

logger = logging.getLogger(__name__)

AGENT_REGISTRY: Dict[str, Dict[str, Any]] = {
    "invoice": {
        "title": "Invoice Agent",
        "responsibility": "Create Simpro invoices from free text or tabular user inputs.",
        "enabled": True,
        "proxy_module": "agents.invoice_proxy",
        "proxy_fn": "run_invoice_agent",
    },
    "schedule": {
        "title": "Schedule Agent",
        "responsibility": "Bulk create/update/delete schedules for jobs and quotes with SOP compliance.",
        "enabled": True,
        "proxy_module": "agents.schedule_proxy",
        "proxy_fn": "run_schedule_agent",
    },
    "workorder": {
        "title": "Work Order Agent",
        "responsibility": "Prepare and create contractor jobs (work orders) with materials/labour from cost centres.",
        "enabled": True,
        "proxy_module": "agents.workorder_proxy",
        "proxy_fn": "run_workorder_agent",
    },
    "purchase_order": {
        "title": "Purchase Order Agent",
        "responsibility": "Create, update, and delete purchase/supplier/material orders in Simpro with supplier and cost centre tracking.",
        "enabled": True,
        "proxy_module": "agents.purchase_order_proxy",
        "proxy_fn": "run_purchase_order_agent",
    },
}

# Cache for lazily-loaded agent functions
_loaded_agents: Dict[str, Optional[Callable]] = {}


def is_agent(name: str) -> bool:
    return name in AGENT_REGISTRY


def load_agent(name: str) -> Optional[Callable]:
    """Lazy-load an agent proxy function. Returns None if import fails."""
    if name in _loaded_agents:
        return _loaded_agents[name]

    entry = AGENT_REGISTRY.get(name)
    if not entry or not entry.get("enabled"):
        _loaded_agents[name] = None
        return None

    try:
        mod = importlib.import_module(entry["proxy_module"])
        fn = getattr(mod, entry["proxy_fn"])
        _loaded_agents[name] = fn
        return fn
    except (ImportError, AttributeError) as e:
        logger.warning(f"Agent '{name}' not loadable: {e}")
        _loaded_agents[name] = None
        return None


def get_loadable_agents() -> set:
    """Return the set of agent names that can actually be loaded."""
    return {name for name in AGENT_REGISTRY if load_agent(name) is not None}
