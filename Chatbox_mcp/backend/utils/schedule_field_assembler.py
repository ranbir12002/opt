# backend/utils/schedule_field_assembler.py
"""
Schedule-specific field assembly logic.

Moved out of crossroads.py (Point 1) — this is agent-specific payload
construction, not a generic crossroads decision.

The schedule agent calls `schedule_field_assembly_fallback()` directly
for deterministic (no-LLM) field assembly.
"""

from __future__ import annotations
import hashlib
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# End Time → Blocks Computation
# ═══════════════════════════════════════════════════════════════════════════

def _compute_blocks_from_end_time(start_time: str, end_time: str):
    """
    Compute duration in hours from start_time and end_time (both HH:MM 24-hour).

    Returns:
        (blocks, error_dict_or_none) — blocks is a float, error is a dict if invalid
    """
    try:
        s_h, s_m = map(int, start_time.split(":"))
        e_h, e_m = map(int, end_time.split(":"))
        start_total = s_h * 60 + s_m
        end_total = e_h * 60 + e_m
        if end_total <= start_total:
            return None, {
                "field": "EndTime",
                "message": f"End time ({end_time}) must be after start time ({start_time})"
            }
        return (end_total - start_total) / 60.0, None
    except (ValueError, TypeError):
        return None, {
            "field": "EndTime",
            "message": f"Invalid end time format: {end_time}. Expected HH:MM."
        }


# ═══════════════════════════════════════════════════════════════════════════
# Cache Key Builder (registered with crossroads for "field_assembly" type)
# ═══════════════════════════════════════════════════════════════════════════

def schedule_field_assembly_cache_key(crossroad_type: str, context: Dict[str, Any]) -> str:
    """Build a cache key from schedule field presence pattern."""
    row = context.get("row_data", {})
    resolved = context.get("resolved_data", {})
    pattern = (
        crossroad_type,
        context.get("operation", ""),
        bool(resolved.get("schedule_id")),
        bool(resolved.get("existing_start_time")),
        bool(resolved.get("existing_blocks")),
        bool(row.get("StartTime")),
        bool(row.get("Blocks")),
        bool(row.get("Date")),
        bool(row.get("BlocksAdjust")),
        bool(row.get("Notes")),
        bool(row.get("IsLocked")),
        bool(row.get("EndTime")),
    )
    return hashlib.md5(str(pattern).encode()).hexdigest()


# ═══════════════════════════════════════════════════════════════════════════
# Cached Template Applier (registered with crossroads)
# ═══════════════════════════════════════════════════════════════════════════

def schedule_apply_cached_template(
    template: Dict[str, Any],
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Apply a cached crossroads template to new schedule row data.

    The template tells us WHICH fields to include and which sources to prefer.
    We substitute actual values from the current context.
    """
    if context.get("operation") in ("DELETE", "LOCK", "UNLOCK"):
        result = dict(template)
        row = context.get("row_data", {})
        resolved = context.get("resolved_data", {})
        fields = dict(result.get("fields", {}))
        fields["date"] = row.get("Date") or resolved.get("existing_date")
        result["fields"] = fields
        result["reasoning"] = template.get("reasoning", "") + " (cached)"
        return result

    # For CREATE/UPDATE, derive actual values from current row/resolved
    row = context.get("row_data", {})
    resolved = context.get("resolved_data", {})
    t_fields = template.get("fields", {})

    fields = {}
    fields["date"] = row.get("Date") or resolved.get("existing_date") or t_fields.get("date")
    fields["start_time"] = row.get("StartTime") or resolved.get("existing_start_time") or t_fields.get("start_time")

    # Blocks: check for adjustment first, then end_time, then absolute blocks
    blocks_adjust = row.get("BlocksAdjust", "").strip()
    end_time_str = row.get("EndTime", "").strip()
    if blocks_adjust:
        try:
            adjust = float(blocks_adjust)
            existing = float(resolved.get("existing_blocks") or 0)
            fields["blocks"] = existing + adjust
        except (ValueError, TypeError):
            fields["blocks"] = float(row.get("Blocks") or resolved.get("existing_blocks") or t_fields.get("blocks") or 0)
    elif end_time_str and fields.get("start_time"):
        computed, err = _compute_blocks_from_end_time(fields["start_time"], end_time_str)
        if computed is not None:
            fields["blocks"] = computed
        else:
            fields["blocks"] = resolved.get("existing_blocks") or t_fields.get("blocks")
    else:
        blocks_val = row.get("Blocks")
        if blocks_val:
            try:
                fields["blocks"] = float(blocks_val)
            except (ValueError, TypeError):
                fields["blocks"] = resolved.get("existing_blocks") or t_fields.get("blocks")
        else:
            fields["blocks"] = resolved.get("existing_blocks") or t_fields.get("blocks")

    fields["notes"] = row.get("Notes") if row.get("Notes") else (resolved.get("existing_notes") or t_fields.get("notes", ""))
    fields["is_locked"] = t_fields.get("is_locked")
    if row.get("IsLocked"):
        fields["is_locked"] = str(row["IsLocked"]).lower() == "true"

    return {
        "decision": template.get("decision", "cached"),
        "fields": fields,
        "errors": template.get("errors", []),
        "reasoning": template.get("reasoning", "") + " (cached)",
        "confidence": template.get("confidence", 0.9),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Deterministic Fallback (no LLM)
# ═══════════════════════════════════════════════════════════════════════════

def schedule_field_assembly_fallback(crossroad_type: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministic field assembly for schedule operations.
    No LLM needed — uses operation rules + row/resolved data.
    """
    op = (context.get("operation") or "CREATE").upper()
    row = context.get("row_data", {})
    resolved = context.get("resolved_data", {})

    if op in ("DELETE", "LOCK", "UNLOCK"):
        return {
            "decision": "ids_only",
            "fields": {
                "date": row.get("Date") or resolved.get("existing_date"),
                "start_time": None,
                "blocks": None,
                "notes": None,
                "is_locked": True if op == "LOCK" else (False if op == "UNLOCK" else None),
            },
            "errors": [],
            "reasoning": f"fallback: {op} only needs IDs",
            "confidence": 1.0,
        }

    if op == "UPDATE":
        start_time = row.get("StartTime") or resolved.get("existing_start_time", "")
        blocks = resolved.get("existing_blocks", 0.0)
        if row.get("Blocks"):
            try:
                blocks = float(row["Blocks"])
            except (ValueError, TypeError):
                pass

        # Handle BlocksAdjust, then EndTime, then absolute Blocks
        blocks_adjust = row.get("BlocksAdjust", "").strip()
        end_time_str = row.get("EndTime", "").strip()
        errors = []
        if blocks_adjust:
            try:
                adjust = float(blocks_adjust)
                existing = float(resolved.get("existing_blocks") or 0)
                blocks = existing + adjust
                if blocks <= 0:
                    errors.append({
                        "field": "Blocks",
                        "message": f"Cannot reduce below 0 hours (current: {existing}hrs, adjust: {adjust}hrs)"
                    })
            except (ValueError, TypeError):
                pass
        elif end_time_str:
            # User specified an end time — compute blocks = end_time - start_time
            if start_time:
                computed, err = _compute_blocks_from_end_time(start_time, end_time_str)
                if computed is not None:
                    blocks = computed
                else:
                    errors.append(err)
            else:
                errors.append({
                    "field": "StartTime",
                    "message": "Cannot compute duration from end time without a start time"
                })

        notes = row.get("Notes") if row.get("Notes") else resolved.get("existing_notes", "")
        date = row.get("Date") or resolved.get("existing_date")
        if not date:
            errors.append({"field": "Date", "message": "What date should this schedule be for?"})

        return {
            "decision": "preserve_existing",
            "fields": {
                "date": date or "",
                "start_time": start_time,
                "blocks": blocks,
                "notes": notes,
                "is_locked": str(row.get("IsLocked")).lower() == "true" if row.get("IsLocked") else None,
            },
            "errors": errors,
            "reasoning": "fallback: UPDATE preserves existing values for missing fields",
            "confidence": 1.0,
        }

    # CREATE
    errors = []
    if not row.get("StartTime"):
        errors.append({"field": "StartTime", "message": "What time should this schedule start? (e.g. 07:00)"})
    if not row.get("Blocks") and not row.get("EndTime"):
        errors.append({"field": "Blocks", "message": "How many hours should this schedule be?"})
    if not row.get("Date"):
        errors.append({"field": "Date", "message": "What date should this schedule be for?"})

    blocks = 0.0
    end_time_str = row.get("EndTime", "").strip()
    if row.get("Blocks"):
        try:
            blocks = float(row["Blocks"])
        except (ValueError, TypeError):
            pass
    elif end_time_str and row.get("StartTime"):
        # Compute blocks from start_time and end_time
        computed, err = _compute_blocks_from_end_time(row["StartTime"], end_time_str)
        if computed is not None:
            blocks = computed
        else:
            errors.append(err)

    return {
        "decision": "require_all",
        "fields": {
            "date": row.get("Date", ""),
            "start_time": row.get("StartTime", ""),
            "blocks": blocks,
            "notes": row.get("Notes", ""),
            "is_locked": str(row.get("IsLocked")).lower() == "true" if row.get("IsLocked") else None,
        },
        "errors": errors,
        "reasoning": "fallback: CREATE requires date, start_time, blocks",
        "confidence": 1.0,
    }


# ═══════════════════════════════════════════════════════════════════════════
# LLM Prompt (used if LLM path is ever re-enabled for field_assembly)
# ═══════════════════════════════════════════════════════════════════════════

FIELD_ASSEMBLY_PROMPT = """You are a schedule operation field assembler for a construction back-office system (Simpro ERP).

Given an operation type and available data, decide what fields the final payload should contain and how to populate them.

OPERATION RULES:
- CREATE: Requires date, start_time, blocks (hours). notes defaults to "". is_locked defaults to false.
- UPDATE: Requires date. For start_time/blocks/notes: use row value if provided, else preserve existing value from found schedule. is_locked optional.
  - If BlocksAdjust is provided (e.g. "+2" or "-1"), calculate: blocks = existing_blocks + BlocksAdjust. If result <= 0, return an error.
- DELETE: Only needs date (for lookup) and schedule_id. Does NOT need start_time, blocks, or notes. Set them to null.
- LOCK: Only needs schedule_id. Set is_locked to true. start_time/blocks/notes are null.
- UNLOCK: Only needs schedule_id. Set is_locked to false. start_time/blocks/notes are null.

FIELD SOURCE PRIORITY:
1. row_data values (what user explicitly provided)
2. resolved_data existing_* values (from found schedule in Simpro)
3. Defaults: "" for notes, false for is_locked, null for unneeded fields

Return ONLY valid JSON:
{"reasoning": "<step through: what fields were explicitly provided, what was inferred from existing schedule, what defaults apply, what is still missing>", "decision": "<action_taken>", "fields": {"date": "<value or null>", "start_time": "<value or null>", "blocks": <number or null>, "notes": "<value or null>", "is_locked": <bool or null>}, "errors": [{"field": "<name>", "message": "<what's missing>"}], "confidence": <0.0-1.0>}

IMPORTANT: "errors" array should only contain entries for TRULY missing required fields that cannot be inferred. Do NOT put errors for fields that aren't needed by this operation."""

FIELD_ASSEMBLY_DOMAIN_TOPICS = ["schedule_operations_sop"]
