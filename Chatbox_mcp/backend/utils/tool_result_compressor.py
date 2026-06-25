"""
backend/utils/tool_result_compressor.py

Compress MCP tool results before they enter the LLM context window.
Port of mcp-client/utils/tool-result-compressor.js.

Applies field pruning (same allowlist as pii_filter.py) and tiered truncation:
  Tier 1 (≤15 items):   All items, pruned fields
  Tier 2 (16–50 items): All items, pruned, compact note
  Tier 3 (51–200):      First 30 items + summary stats
  Tier 4 (200+):        5 sample items + summary stats only

IMPORTANT: This only compresses what the LLM sees for reasoning.
The frontend/presenter path receives FULL uncompressed data.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Universal Allowlist (mirrors pii_filter.py) ───────────────────────────────
# Single source of truth for what the LLM is allowed to see.
# If you update pii_filter.py, update this too.

_SAFE_FIELDS = frozenset({
    # Identifiers
    "id", "type", "status", "stage",
    "customerid", "jobid", "costcentreid", "sectionid",
    "invoiceid", "quoteid", "itemid", "paymenttermid",
    "companyid", "contractorid", "scheduleid",

    # Business fields (non-PII)
    "companyname", "name", "displayname",
    "dateissued", "duedate", "datecreated", "datemodified",
    "taxcode", "orderno", "invoicetype",
    "description", "notes",

    # Structural / percentage fields
    "percentcomplete", "displayorder",
    "costcenter", "costcentre",
    "invoicepercentage", "percent",
    "stcseligible", "veecseligible",

    # Envelope keys
    "success", "error", "message", "summary",
    "created", "failed", "skipped",
    "count", "total", "totals", "results", "data",
    "jobs", "records", "rows", "items",
    "cost_centres", "sections",
    "claimed", "todate", "remaining", "amount",
    "agent_output", "creation_result",
    "request", "response", "body",
    "costcenters", "peritems", "per_item", "invoice_mode",

    # Invoice structural
    "invoice", "detail", "success_rate",
    "policy", "job_results", "trace",
    "desc_mode", "desc_joiner",
    "defaults", "missing",

    # Schedule fields
    "date", "staff", "staffname",
    "totalhours", "reference", "blocks",
    "schedules", "date_from", "date_to", "type_filter",

    # Additional Simpro fields
    "givenname", "familyname", "email", "phone", "position",
    "abn", "archived", "active",
    "invoicenumber", "quotenumber", "quotename",
    "ispaid", "islocked", "isclosed",
    "datefrom", "dateto", "dateexpires",
    "site", "customer",
    "uid", "number", "balancedueamount",
    "displayid", "balance", "isactive",
    "contactname", "firstname", "lastname",
    "vendororders",
})

_FINANCIAL_KEYS = frozenset({
    "total", "totalextax", "totalinctax",
    "extax", "inctax", "tax",
    "quantity", "unitpriceextax", "claimextax", "claiminctax",
    "claim",
    "actual", "estimate", "revised", "committed",
    "materials", "resources", "labor", "plant", "overhead",
    "commission", "markup", "miscellaneous",
    "grossprofit", "grossprofitloss", "grossmargin",
    "netprofit", "netprofitloss", "netmargin",
    "adjustedtotal", "discount", "membershipdiscount",
    "invoicedvalue",
    "stcs", "veecs", "stcvalue", "veecvalue",
    "hours", "cost",
    "totalamount", "rate",
})

# Metadata keys to skip when unwrapping envelopes
_META_KEYS = frozenset({
    "success", "error", "message", "status", "page", "total",
    "count", "total_fetched", "pages_fetched", "is_closed",
    "filter", "date", "date_from", "date_to", "type_filter",
    "formatted", "tool",
})

# Tier thresholds
TIER_1_MAX = 15
TIER_2_MAX = 50
TIER_3_MAX = 200
TIER_3_SHOW = 30
TIER_4_SHOW = 5


def is_allowed_key(key: str) -> bool:
    """Return True if `key` is on the LLM-safe allowlist."""
    k = key.lower()
    return k in _SAFE_FIELDS or k in _FINANCIAL_KEYS or k.endswith("id")


def _unwrap_envelope(result: Any) -> Tuple[Optional[List], Dict, Optional[Dict]]:
    """
    Unwrap API response envelope to find the main data array.
    Returns (data_array, metadata, single_object).
    Exactly one of data_array / single_object will be non-None.
    """
    if isinstance(result, list):
        return result, {}, None

    if not isinstance(result, dict):
        return None, {}, None

    best_key = None
    best_len = -1
    metadata: Dict[str, Any] = {}

    for key, value in result.items():
        if key.lower() in _META_KEYS:
            metadata[key] = value
            continue
        if isinstance(value, list) and len(value) > best_len:
            best_key = key
            best_len = len(value)

    if best_key is not None:
        for key, value in result.items():
            if key != best_key and key.lower() not in _META_KEYS:
                metadata[key] = value
        return result[best_key], metadata, None

    # No array found — single object (detail response)
    return None, {}, result


def _prune_item(item: Any) -> Any:
    """Recursively keep only allowlisted fields."""
    if item is None:
        return item
    if not isinstance(item, (dict, list)):
        return item

    if isinstance(item, list):
        return [_prune_item(el) for el in item]

    pruned: Dict[str, Any] = {}
    for key, value in item.items():
        if not is_allowed_key(key):
            continue
        pruned[key] = _prune_item(value) if isinstance(value, (dict, list)) else value

    return pruned


def _compute_stats(items: List[Dict]) -> Optional[Dict]:
    """Compute summary statistics (status counts, total sum, date range)."""
    if not items:
        return None

    stats: Dict[str, Any] = {}

    # Status counts
    statuses: Dict[str, int] = {}
    for item in items:
        s = item.get("Status") or item.get("status") or item.get("Stage")
        if s:
            statuses[str(s)] = statuses.get(str(s), 0) + 1
    if statuses:
        stats["by_status"] = statuses

    # Total sum
    totals = []
    for item in items:
        t = item.get("Total")
        if isinstance(t, (int, float)):
            totals.append(float(t))
        elif isinstance(t, dict):
            val = t.get("ExTax") or t.get("IncTax") or t.get("Total")
            if isinstance(val, (int, float)):
                totals.append(float(val))
        ta = item.get("TotalAmount")
        if isinstance(ta, (int, float)):
            totals.append(float(ta))
    if totals:
        total_sum = round(sum(totals), 2)
        stats["total_sum"] = total_sum
        stats["total_avg"] = round(total_sum / len(totals), 2)

    # Date range
    dates = sorted(
        d for d in (
            item.get("DateIssued") or item.get("Date") or item.get("DateCreated")
            for item in items
        )
        if d
    )
    if dates:
        stats["date_range"] = {"earliest": dates[0], "latest": dates[-1]}

    return stats or None


def compress_tool_result(tool_name: str, raw_result: Any) -> str:
    """
    Compress a tool result for LLM context.
    Returns a JSON string representation.
    """
    # Don't compress errors — LLM needs full error text
    if isinstance(raw_result, dict) and (raw_result.get("success") is False or raw_result.get("error")):
        return json.dumps(raw_result)

    data, metadata, single_obj = _unwrap_envelope(raw_result)

    # Single object (detail endpoint) — just prune
    if single_obj is not None:
        return json.dumps(_prune_item(single_obj))

    # Not an array
    if not isinstance(data, list):
        return json.dumps(raw_result)

    count = len(data)

    # Empty — critical signal
    if count == 0:
        return json.dumps({"count": 0, "items": [], "note": "No results found"})

    pruned = [_prune_item(item) for item in data]

    # Tier 1: ≤15 items — all items, pruned
    if count <= TIER_1_MAX:
        return json.dumps({"count": count, "items": pruned})

    # Tier 2: 16–50 items — all items, pruned, note
    if count <= TIER_2_MAX:
        return json.dumps({
            "count": count,
            "items": pruned,
            "note": f"All {count} results shown with key fields only",
        })

    stats = _compute_stats(data)

    # Tier 3: 51–200 items — truncated + stats
    if count <= TIER_3_MAX:
        return json.dumps({
            "count": count,
            "shown": TIER_3_SHOW,
            "items": pruned[:TIER_3_SHOW],
            "stats": stats,
            "note": f"Showing {TIER_3_SHOW} of {count}. Use filters to narrow results.",
        })

    # Tier 4: 200+ items — stats + samples only
    return json.dumps({
        "count": count,
        "shown": TIER_4_SHOW,
        "sample": pruned[:TIER_4_SHOW],
        "stats": stats,
        "note": f"Too many results ({count}). Showing {TIER_4_SHOW} samples. Recommend adding filters.",
    })


def compact_old_tool_result(content: str) -> str:
    """
    Replace blind truncation with structured summaries for old tool results.
    Used in message compaction to keep old tool results tiny.
    """
    try:
        parsed = json.loads(content)

        if isinstance(parsed.get("count"), int):
            items = parsed.get("items") or parsed.get("sample") or []
            ids = [
                str(i.get("ID") or i.get("UID") or i.get("id"))
                for i in items[:5]
                if i.get("ID") or i.get("UID") or i.get("id")
            ]
            id_str = f" IDs: [{', '.join(ids)}]" if ids else ""
            return f"[Previous result: {parsed['count']} items{id_str} — data already processed]"

        if parsed.get("ID") or parsed.get("UID"):
            name = parsed.get("Name") or parsed.get("DisplayName") or "entity"
            return f"[Previous result: {name} (ID: {parsed.get('ID') or parsed.get('UID')}) — data already processed]"

        s = json.dumps(parsed)
        if len(s) > 300:
            return s[:300] + "... [data already processed]"
        return s

    except (json.JSONDecodeError, TypeError):
        if len(content) > 200:
            return content[:200] + "... [data already processed]"
        return content


def compact_messages(
    messages: List[Dict[str, Any]],
    keep_last_n_tool_exchanges: int = 2,
) -> List[Dict[str, Any]]:
    """
    Compact old tool exchanges to compact summaries.
    Keeps the last `keep_last_n_tool_exchanges` tool result messages verbatim.
    """
    # Find all tool result message indices
    tool_result_indices = []
    for i, msg in enumerate(messages):
        if msg.get("role") == "tool":
            tool_result_indices.append(i)
        elif msg.get("role") == "user" and isinstance(msg.get("content"), list):
            if messages[i]["content"] and messages[i]["content"][0].get("type") == "tool_result":
                tool_result_indices.append(i)

    if not tool_result_indices:
        return messages

    # Keep last N verbatim, compact the rest
    to_compact = set(tool_result_indices[:-keep_last_n_tool_exchanges])

    result = []
    for i, msg in enumerate(messages):
        if i not in to_compact:
            result.append(msg)
            continue

        # Compact this tool result
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            compacted = compact_old_tool_result(content if isinstance(content, str) else json.dumps(content))
            result.append({**msg, "content": compacted})
        elif isinstance(msg.get("content"), list):
            new_blocks = []
            for block in msg["content"]:
                if block.get("type") == "tool_result":
                    content = block.get("content", "")
                    compacted = compact_old_tool_result(content if isinstance(content, str) else json.dumps(content))
                    new_blocks.append({**block, "content": compacted})
                else:
                    new_blocks.append(block)
            result.append({**msg, "content": new_blocks})
        else:
            result.append(msg)

    return result
