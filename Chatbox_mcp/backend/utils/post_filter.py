# backend/utils/post_filter.py
"""
Universal post-filter for MCP tool results.

Uses LLM-based schema introspection to apply client-side data filtering
when the API endpoint doesn't natively support a filter that the user's
query implies.  Fully generic — works for any entity type by examining
the actual data fields and values, with zero hardcoded filter rules.

Usage (in chat.py):
    from utils.post_filter import apply_post_filters

    _pf = apply_post_filters(
        structured_data, message, tool_calls,
        is_follow_up=is_follow_up,
        stored_filters=user_context.get("active_filters"),
        previous_message=_prev_message,
        llm_chat=accumulator.tracked_chat,
    )
    structured_data = _pf.data
    user_context["active_filters"] = _pf.active_filters
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Data Structures
# ═══════════════════════════════════════════════════════════════

@dataclass
class PostFilterResult:
    """Return value from apply_post_filters — data + metadata for state tracking."""
    data: Any
    applied_filters: List[Dict[str, Any]]
    active_filters: Dict[str, Dict[str, Any]]


# ═══════════════════════════════════════════════════════════════
# Schema Introspection
# ═══════════════════════════════════════════════════════════════

# Metadata keys to skip when looking for the main data array
_META_KEYS = frozenset({
    "success", "total", "page", "page_size", "count", "error",
    "tool", "formatted", "metadata", "total_fetched",
})


def _find_data_array_key(data: Dict[str, Any]) -> Optional[str]:
    """Find the primary data array key (the longest non-metadata list)."""
    best_key: Optional[str] = None
    best_len = 0
    for k, v in data.items():
        if k in _META_KEYS:
            continue
        if isinstance(v, list) and len(v) > best_len:
            best_key = k
            best_len = len(v)
    return best_key if best_len > 0 else None


def _get_nested_value(record: Dict, field_path: str) -> Any:
    """Traverse a dot-notation path like 'Staff.Type' into a nested dict."""
    parts = field_path.split(".")
    current: Any = record
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _build_schema_fingerprint(
    data: Dict[str, Any],
    data_key: str,
    max_sample: int = 20,
    max_unique: int = 10,
) -> Dict[str, Any]:
    """
    Scan actual data records and produce a compact schema fingerprint.

    For each field path (including nested via dot notation):
    - Low-cardinality (≤max_unique unique values): lists ALL unique values
    - High-cardinality: marks as "(many)" with a few examples

    Returns a dict suitable for JSON serialization into an LLM prompt.
    """
    items = data.get(data_key)
    if not isinstance(items, list) or not items:
        return {"data_key": data_key, "record_count": 0, "fields": {}}

    total = len(items)

    # Sample: first half + last half for diversity
    if total <= max_sample:
        sample = items
    else:
        half = max_sample // 2
        sample = items[:half] + items[-half:]

    # Discover all field paths and collect values
    field_values: Dict[str, List[Any]] = {}

    def _walk(obj: Any, prefix: str = "") -> None:
        if not isinstance(obj, dict):
            return
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                _walk(v, path)
            elif isinstance(v, list):
                continue  # skip nested arrays (Blocks, etc.)
            else:
                field_values.setdefault(path, []).append(v)

    for record in sample:
        if isinstance(record, dict):
            _walk(record)

    # Build compact field descriptions
    fields: Dict[str, Dict[str, Any]] = {}
    for path, values in field_values.items():
        # Deduplicate while preserving some ordering
        unique = list(dict.fromkeys(str(v) for v in values if v is not None))
        if len(unique) <= max_unique:
            fields[path] = {"unique": unique}
        else:
            fields[path] = {"unique": "(many)", "examples": unique[:3]}

    return {
        "data_key": data_key,
        "record_count": total,
        "fields": fields,
    }


# ═══════════════════════════════════════════════════════════════
# LLM-Based Filter Inference (Phase 1)
# ═══════════════════════════════════════════════════════════════

_FILTER_INFER_SYSTEM = """You are a data filter analyzer for a construction back-office system.

Given a user's question and the schema of data returned from an API call, determine if the data should be filtered to match the user's intent.

Rules:
1. Only suggest filters for fields where the schema shows distinct low-cardinality values (listed as arrays) that match the user's intent.
2. The filter value MUST be one of the exact values shown in the schema's "unique" list for that field.
3. For nested fields use dot notation (e.g. "Staff.Type").
4. If a tool_arg already covers a filter dimension (e.g. tool_args has "type"="job" or filters contains "Staff.Type"), do NOT suggest a filter for the same concept.
5. If the user asks for "all" or does not imply any narrowing, return NO filters.
6. When unsure, return NO filters. False positives are worse than missing a filter.
7. Maximum 3 filters per request.
8. The "dimension" is a short stable snake_case label for this filter category.
   Two filters on the same conceptual dimension across different requests MUST share the same dimension label.
   Derive it from the field path (e.g. "Staff.Type" -> "staff_type", "Type" -> "type", "IsPaid" -> "is_paid", "Stage" -> "stage").
9. If the input contains "already_handled_qualifiers", those words are being handled by a separate downstream process — do NOT infer any filter based on those words, and do NOT match them against any field value (including name fields).

Respond with ONLY a JSON object:
{"filters": [{"field": "Field.Path", "value": "exact_value", "dimension": "short_label"}]}

If no filters apply: {"filters": []}"""


def _collect_tool_args(
    tool_calls: List[Dict[str, Any]],
    tool_names: Set[str],
) -> Dict[str, Any]:
    """Merge arguments from all matching tool calls into one dict."""
    merged: Dict[str, Any] = {}
    for tc in tool_calls:
        if tc.get("name") not in tool_names:
            continue
        args = tc.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (ValueError, TypeError):
                continue
        if isinstance(args, dict):
            merged.update(args)
    return merged


def _infer_filters(
    schema: Dict[str, Any],
    message: str,
    tool_args: Dict[str, Any],
    llm_chat: Callable,
    downstream_qualifiers: Optional[List[str]] = None,
) -> Optional[List[Dict[str, Any]]]:
    """
    Ask LLM which filters to apply based on data schema + user message.

    Returns:
        List of filter dicts [{field, value, dimension}], or None on failure.
    """
    payload: Dict[str, Any] = {
        "user_question": message,
        "data_schema": schema,
        "tool_args": tool_args,
    }
    if downstream_qualifiers:
        payload["already_handled_qualifiers"] = downstream_qualifiers

    user_payload = json.dumps(payload, default=str)

    messages = [
        {"role": "system", "content": _FILTER_INFER_SYSTEM},
        {"role": "user", "content": user_payload},
    ]

    try:
        raw = llm_chat(
            messages,
            response_format={"type": "json_object"},
            temperature=0.0,
            sanitize=False,
        )
        result = json.loads(raw)
        filters = result.get("filters", [])

        if not isinstance(filters, list):
            logger.warning("Post-filter LLM: 'filters' is not a list")
            return None

        # Validate and normalize each filter
        valid: List[Dict[str, Any]] = []
        seen_dims: Set[str] = set()

        for f in filters[:3]:  # max 3
            field_path = f.get("field", "").strip()
            value = f.get("value", "").strip()
            dimension = f.get("dimension", "").strip()

            if not field_path or not value:
                continue

            # Normalize dimension
            dimension = _normalize_dimension(dimension, field_path)

            # Enforce dimension mutual exclusivity
            if dimension in seen_dims:
                logger.debug(f"Post-filter LLM: duplicate dimension {dimension}, skipping")
                continue
            seen_dims.add(dimension)

            valid.append({
                "field": field_path,
                "value": value,
                "dimension": dimension,
            })

        logger.info(
            f"Post-filter LLM inference: {valid} for message={message[:80]!r}"
        )
        return valid

    except Exception as e:
        logger.warning(f"Post-filter LLM inference failed ({e}), skipping filters")
        return None


def _normalize_dimension(dim: str, field_path: str) -> str:
    """Ensure dimension is a stable, clean snake_case identifier."""
    if dim:
        # Convert to clean snake_case
        cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", dim.strip().lower()).strip("_")
        if cleaned:
            return cleaned
    # Fallback: derive from field path
    return re.sub(r"[^a-zA-Z0-9]+", "_", field_path.lower()).strip("_")


# ═══════════════════════════════════════════════════════════════
# LLM-Based Filter Continuation (Phase 0 — follow-up only)
# ═══════════════════════════════════════════════════════════════

_FILTER_EVAL_SYSTEM = """You are a filter-continuation analyzer for a back-office assistant.

The user made a previous request and the system applied certain data filters to narrow the results.
Now the user has sent a follow-up message. For EACH active filter, decide whether it should be kept or cleared:

- "keep" — The follow-up refines, narrows, or adds to the previous request. This filter still applies.
  Examples: "only job schedules" (adding a type filter — keep the existing staff filter)
            "sort by hours" (display change — keep all data filters)
            "what about Nicholas?" (drill-down — keep all data filters)

- "clear" — The follow-up explicitly broadens, contradicts, or replaces this filter's scope.
  Examples: "show employee schedules instead" (contradicts a contractor filter)
            "show all schedules" (broadens — remove staff/type filters)
            "include everyone" (broadens a staff type filter)

Respond with ONLY a JSON object: {"decisions": {"dimension_name": "keep"|"clear"}}
Every active filter dimension MUST appear in your response."""


def _evaluate_stored_filters(
    stored_filters: Dict[str, Dict[str, Any]],
    previous_message: str,
    current_message: str,
    llm_chat: Callable,
) -> Dict[str, str]:
    """
    Ask LLM which stored filters to KEEP or CLEAR on a follow-up request.

    Returns:
        Dict mapping dimension -> "keep" | "clear".
        On LLM failure, defaults to "keep" for all (conservative).
    """
    filter_descriptions = []
    for dim, info in stored_filters.items():
        desc = info.get("description", "")
        if not desc:
            desc = f"Filtering {info.get('data_key', '?')} where {info.get('field', '?')}={info.get('value', '?')}"
        filter_descriptions.append({
            "dimension": dim,
            "description": desc,
            "value": info.get("value", ""),
        })

    user_payload = json.dumps({
        "previous_request": previous_message,
        "current_followup": current_message,
        "active_filters": filter_descriptions,
    }, default=str)

    messages = [
        {"role": "system", "content": _FILTER_EVAL_SYSTEM},
        {"role": "user", "content": user_payload},
    ]

    try:
        raw = llm_chat(
            messages,
            response_format={"type": "json_object"},
            temperature=0.0,
            sanitize=False,
        )
        result = json.loads(raw)
        decisions = result.get("decisions", {})

        # Validate: only accept "keep" or "clear"
        validated: Dict[str, str] = {}
        for dim in stored_filters:
            val = decisions.get(dim, "keep")
            validated[dim] = val if val in ("keep", "clear") else "keep"

        logger.info(
            f"Post-filter LLM evaluation: {validated} "
            f"(prev={previous_message[:50]!r}, cur={current_message[:50]!r})"
        )
        return validated

    except Exception as e:
        logger.warning(f"Post-filter LLM evaluation failed ({e}), defaulting to keep all")
        return {dim: "keep" for dim in stored_filters}


# ═══════════════════════════════════════════════════════════════
# Generic Filter Helpers
# ═══════════════════════════════════════════════════════════════

def _is_redundant_filter(
    field_path: str,
    value: str,
    tool_calls: List[Dict[str, Any]],
    tool_names: Set[str],
) -> bool:
    """Check if any tool argument already targets this field+value."""
    # Normalize for comparison
    field_lower = field_path.lower().replace(".", "_")
    value_lower = str(value).lower()

    for tc in tool_calls:
        if tc.get("name") not in tool_names:
            continue
        args = tc.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (ValueError, TypeError):
                continue
        if not isinstance(args, dict):
            continue
        for arg_key, arg_val in args.items():
            if arg_key.lower() == field_lower and str(arg_val).lower() == value_lower:
                logger.debug(
                    f"Post-filter: skipped redundant filter {field_path}={value} "
                    f"— tool already set {arg_key}={arg_val}"
                )
                return True
            # Also check inside nested filter dicts
            if isinstance(arg_val, dict):
                for fk, fv in arg_val.items():
                    if fk.lower() == field_path.lower() and str(fv).lower() == value_lower:
                        return True
    return False


def _apply_generic_filter(
    data: Dict[str, Any],
    data_key: str,
    field_path: str,
    value: str,
) -> Tuple[Dict[str, Any], int]:
    """
    Apply a single generic filter to the data.

    Returns:
        (modified_data, removed_count)
    """
    items = data.get(data_key)
    if not isinstance(items, list) or not items:
        return data, 0

    original_count = len(items)

    filtered = []
    for item in items:
        if not isinstance(item, dict):
            # Non-dict items pass through unfiltered
            filtered.append(item)
            continue

        actual_value = _get_nested_value(item, field_path)

        if actual_value is None:
            # Field missing on this record — keep it (conservative)
            filtered.append(item)
            continue

        if str(actual_value).lower() == value.lower():
            filtered.append(item)

    removed = original_count - len(filtered)

    if removed > 0:
        result = dict(data)
        result[data_key] = filtered
        if "count" in result:
            result["count"] = len(filtered)
        logger.info(
            f"Post-filter [{field_path}={value}]: {original_count} -> {len(filtered)} "
            f"({removed} removed) on {data_key}"
        )
        return result, removed

    return data, 0


# ═══════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════

def apply_post_filters(
    data: Any,
    message: str,
    tool_calls: List[Dict[str, Any]],
    *,
    is_follow_up: bool = False,
    stored_filters: Optional[Dict[str, Dict[str, Any]]] = None,
    previous_message: str = "",
    llm_chat: Optional[Callable] = None,
    downstream_qualifiers: Optional[List[str]] = None,
) -> PostFilterResult:
    """
    Apply post-filters to MCP tool results based on user intent.

    Uses LLM-based schema introspection to decide which fields to filter,
    rather than hardcoded rules.  Works for any entity type automatically.

    Supports stateful follow-up filtering:
    - On standalone queries: uses LLM to detect filters from user message + data schema.
    - On follow-ups: uses LLM to decide which stored filters from the
      previous request to KEEP or CLEAR, then re-applies surviving filters
      alongside any new ones detected for the current message.

    Args:
        data:             Structured data from _extract_tool_data().
        message:          User's current message (for intent detection).
        tool_calls:       Raw tool_calls from MCP response (for redundancy check).
        is_follow_up:     True if intent analyzer detected this as a follow-up.
        stored_filters:   Active filters from user_context (previous request).
        previous_message: The user's previous request message (for LLM context).
        llm_chat:         LLM chat callable (same signature as crossroads uses).

    Returns:
        PostFilterResult with filtered data, applied filters, and updated
        active_filters dict to store back in user_context.
    """
    empty = PostFilterResult(data=data, applied_filters=[], active_filters={})
    if data is None or not isinstance(data, dict):
        return empty

    # Find the primary data array
    data_key = _find_data_array_key(data)
    if not data_key:
        return empty

    items = data.get(data_key)
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        return empty

    tool_names_used: Set[str] = {tc.get("name", "") for tc in tool_calls}
    total_removed = 0
    applied: List[Dict[str, Any]] = []

    # Start with stored filters (if follow-up), else empty
    active: Dict[str, Dict[str, Any]] = {}
    if is_follow_up and stored_filters:
        active = dict(stored_filters)

    # ── Phase 0: LLM-based filter continuation (follow-up only) ──
    # Ask LLM which stored filters still apply given the new message.
    if active and llm_chat:
        decisions = _evaluate_stored_filters(
            active, previous_message, message, llm_chat,
        )
        dims_to_clear = [dim for dim, dec in decisions.items() if dec == "clear"]
        for dim in dims_to_clear:
            logger.info(
                f"Post-filter: LLM cleared stored filter "
                f"[{active[dim].get('field', '?')}={active[dim].get('value', '?')}] "
                f"(dimension={dim})"
            )
            del active[dim]

    # ── Phase 1: LLM-based filter inference ──
    # Build schema fingerprint from actual data and ask LLM what to filter.
    newly_fired: Set[str] = set()

    if llm_chat:
        schema = _build_schema_fingerprint(data, data_key)
        tool_args = _collect_tool_args(tool_calls, tool_names_used)
        inferred = _infer_filters(schema, message, tool_args, llm_chat,
                                  downstream_qualifiers=downstream_qualifiers)

        if inferred:
            for f_spec in inferred:
                field_path = f_spec["field"]
                value = f_spec["value"]
                dimension = f_spec["dimension"]

                # Safety: skip if tool already filtered this
                if _is_redundant_filter(field_path, value, tool_calls, tool_names_used):
                    continue

                # Safety: skip if data_key doesn't exist
                if data_key not in data:
                    continue

                # Apply the filter
                data, removed = _apply_generic_filter(data, data_key, field_path, value)

                # Safety: if ALL records removed, this is likely wrong — revert
                remaining = data.get(data_key, [])
                if removed > 0 and len(remaining) == 0:
                    logger.warning(
                        f"Post-filter [{field_path}={value}]: removed ALL records "
                        f"— reverting this filter"
                    )
                    # Re-read from items which still has the original
                    # (data was shallow-copied in _apply_generic_filter)
                    data = dict(data)
                    data[data_key] = items
                    if "count" in data:
                        data["count"] = len(items)
                    continue

                total_removed += removed
                applied.append({
                    "field": field_path,
                    "value": value,
                    "dimension": dimension,
                    "data_key": data_key,
                })

                # Track in active state
                newly_fired.add(dimension)
                active[dimension] = {
                    "field": field_path,
                    "value": value,
                    "data_key": data_key,
                    "tool_names": list(tool_names_used),
                    "description": f"Filtering {data_key} where {field_path}={value}",
                }

    # ── Phase 2: Re-apply surviving stored filters (follow-up only) ──
    # For each stored filter that didn't fire in Phase 1 and wasn't
    # cleared in Phase 0, re-apply it to the current data.
    if is_follow_up and active:
        for dim, info in list(active.items()):
            if dim in newly_fired:
                # Already fired a (possibly different) filter in Phase 1
                continue

            # Tool-name compatibility — if current tools don't overlap, drop
            stored_tools = set(info.get("tool_names", []))
            if not tool_names_used.intersection(stored_tools):
                logger.info(
                    f"Post-filter: dropping stored filter "
                    f"[{info.get('field', '?')}={info.get('value', '?')}] "
                    f"— tool mismatch (dimension={dim})"
                )
                del active[dim]
                continue

            stored_data_key = info.get("data_key", "")
            stored_field = info.get("field", "")
            stored_value = info.get("value", "")

            if not stored_field or not stored_value:
                del active[dim]
                continue

            # Data key must exist
            if stored_data_key not in data:
                continue

            # Redundancy check
            if _is_redundant_filter(stored_field, stored_value, tool_calls, tool_names_used):
                continue

            # Re-apply
            data, removed = _apply_generic_filter(
                data, stored_data_key, stored_field, stored_value,
            )
            total_removed += removed
            if removed > 0:
                applied.append({
                    "field": stored_field,
                    "value": stored_value,
                    "dimension": dim,
                    "data_key": stored_data_key,
                })
                logger.info(
                    f"Post-filter: re-applied stored filter "
                    f"[{stored_field}={stored_value}] "
                    f"from previous request (dimension={dim})"
                )

    if total_removed == 0:
        logger.debug(f"Post-filter: no filters applied for: {message[:60]}...")

    return PostFilterResult(
        data=data,
        applied_filters=applied,
        active_filters=active,
    )


# ═══════════════════════════════════════════════════════════════
# Department Post-Filter (async — requires MCP calls)
# ═══════════════════════════════════════════════════════════════

async def apply_department_filter(
    structured_data: Dict[str, Any],
    department_name: str,
    mcp_executor: Any,
    org_id: int = 0,
) -> Dict[str, Any]:
    """
    Filter schedule data by department using the department resolution chain.

    Parses Reference fields from schedules to extract (job_id, cc_instance_id),
    resolves CC instance → setup CC type → department via the department cache,
    and keeps only schedules matching the requested department.

    Args:
        structured_data: Dict from _extract_tool_data() with a "schedules" key.
        department_name: Target department name (e.g., "Roofing").
        mcp_executor: MCPToolExecutor instance for making MCP tool calls.

    Returns:
        Filtered copy of structured_data with only matching schedules.
    """
    from utils.department_cache import resolve_cc_instances_to_departments

    schedules = structured_data.get("schedules", [])
    if not schedules:
        return structured_data

    # Parse Reference fields → (job_id, cc_instance_id)
    cc_pairs: List[Tuple[int, int]] = []
    ref_to_cc: Dict[int, int] = {}  # schedule index → cc_instance_id
    for idx, s in enumerate(schedules):
        ref = s.get("Reference", "")
        parts = ref.split("-")
        if len(parts) >= 2:
            try:
                job_id = int(parts[0])
                cc_instance_id = int(parts[1])
                cc_pairs.append((job_id, cc_instance_id))
                ref_to_cc[idx] = cc_instance_id
            except (ValueError, TypeError):
                pass

    if not cc_pairs:
        logger.info("Department filter: no parseable Reference fields found, skipping")
        return structured_data

    # Resolve CC instances → departments
    cc_dept_map = await resolve_cc_instances_to_departments(mcp_executor, cc_pairs, org_id=org_id)

    target = department_name.lower().strip()
    original_count = len(schedules)

    # Filter: keep schedules whose CC maps to the target department
    filtered = []
    for idx, s in enumerate(schedules):
        cc_id = ref_to_cc.get(idx)
        if cc_id is None:
            continue
        dept = cc_dept_map.get(cc_id, "")
        if dept and dept.lower().strip() == target:
            filtered.append(s)

    result = dict(structured_data)
    result["schedules"] = filtered
    if "total_fetched" in result:
        result["total_fetched"] = len(filtered)

    logger.info(
        f"Department filter [{department_name}]: {original_count} -> {len(filtered)} "
        f"({original_count - len(filtered)} removed)"
    )
    return result


# ═══════════════════════════════════════════════════════════════
# Generic Post-Execution Qualifier Filter
# ═══════════════════════════════════════════════════════════════

# Registry of qualifier types that cannot be resolved as URL-level filters
# and require a post-execution lookup step.
# Format: { qualifier_type: handler_async_fn(structured_data, value, mcp_executor) }
#
# To add a new qualifier that needs post-execution resolution:
#   1. Write an async handler function with signature:
#      async def _handle_<type>(data, value, mcp_executor) -> Dict
#   2. Register it below in _QUALIFIER_HANDLERS
#
# URL-resolvable qualifiers (e.g. Staff.Type=contractor, Status=active) are
# handled at the URL level by the executor and never reach here.

async def _handle_department(
    structured_data: Dict[str, Any],
    value: str,
    mcp_executor: Any,
    org_id: int = 0,
) -> Dict[str, Any]:
    """Delegate to the existing department resolution chain."""
    return await apply_department_filter(structured_data, value, mcp_executor, org_id=org_id)


_QUALIFIER_HANDLERS: Dict[str, Any] = {
    "department": _handle_department,
    # Future qualifiers registered here, e.g.:
    # "region": _handle_region,
    # "cost_centre_type": _handle_cost_centre_type,
}


async def apply_post_execution_qualifiers(
    structured_data: Dict[str, Any],
    qualifiers: Dict[str, str],
    mcp_executor: Any,
    org_id: int = 0,
) -> Dict[str, Any]:
    """
    Apply post-execution qualifier filters to structured_data.

    Called when the intent analyzer identifies qualifiers that cannot be
    expressed as URL-level Simpro filters and require a lookup-based
    post-filter step.

    Args:
        structured_data: Dict from _extract_tool_data().
        qualifiers: Dict of {qualifier_type: value} extracted by intent analyzer.
                    Only qualifiers registered in _QUALIFIER_HANDLERS are applied.
                    Unknown qualifiers are logged and skipped (never crash).
        mcp_executor: MCPToolExecutor instance for resolution API calls.

    Returns:
        Filtered copy of structured_data.
    """
    if not qualifiers or not structured_data:
        return structured_data

    result = structured_data
    for qualifier_type, value in qualifiers.items():
        if not value:
            continue
        handler = _QUALIFIER_HANDLERS.get(qualifier_type)
        if handler is None:
            logger.debug(
                f"apply_post_execution_qualifiers: no handler for '{qualifier_type}' — skipping"
            )
            continue
        try:
            result = await handler(result, value, mcp_executor, org_id=org_id)
        except Exception as e:
            logger.warning(
                f"apply_post_execution_qualifiers: handler for '{qualifier_type}' failed "
                f"(non-fatal, returning unfiltered data): {e}"
            )

    return result
