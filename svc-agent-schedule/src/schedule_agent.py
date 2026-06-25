# src/schedule_agent.py
"""
Schedule Agent - Handles bulk schedule create/update/delete operations.

This agent:
1. Parses schedule data from extractor results
2. Validates operation type and required fields
3. Resolves names → IDs using MCP tools
4. Returns validated payloads OR clarification requests
"""

from __future__ import annotations
import logging
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from dateutil import parser as date_parser

from config import MAX_INTERACTIVE_CLARIFICATIONS, SOP_MD_PATH
from utils.crossroads import resolve_crossroads, resolve_with_context, reset_crossroads_cache
from utils.resolution_context import ResolutionContext, RequestTracker
from utils.mcp_executor import MCPToolExecutor
from utils.entity_resolver import (
    EntityResolver,
    ResolutionError,
    AmbiguousResolutionError,
    MissingFieldError,
    ValidationError,
    BatchedClarificationError,
)
from utils.fuzzy_match import fuzzy_match_name, fuzzy_match_entities, deduplicate_matches
from utils.agent_state import AgentExecutionState, create_agent_state

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _read_sop(path: Optional[str] = None, sop_override: Optional[str] = None, max_chars: int = 32_000) -> str:
    """Read schedule SOP markdown to plain text. Prefers sop_override if provided."""
    if sop_override:
        logger.info("[SOP] Using DB override SOP for schedule (org-specific)")
        return sop_override  # already validated at upload time
    import os
    path = path or SOP_MD_PATH
    if not path or not os.path.exists(path):
        logger.warning(f"[SOP] Default schedule SOP not found at {path} — proceeding without SOP")
        return ""
    logger.info(f"[SOP] Using default schedule SOP from file: {path}")
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    return text[:max_chars]


async def _parse_sop_behaviour_flags(sop_text: str, llm_chat: Callable) -> Dict[str, bool]:
    """
    Read the SOP and extract two behaviour flags via a small LLM call.
    Both default to True (safe/confirmatory behaviour) if the SOP is silent.

    Returns:
        {
            "require_delete_confirmation": bool,  # True = always prompt before DELETE
            "require_unlock_approval": bool,      # True = always prompt before unlock+delete
        }
    """
    _DEFAULTS = {"require_delete_confirmation": True, "require_unlock_approval": True}
    if not sop_text or not llm_chat:
        return _DEFAULTS
    try:
        import json
        system_prompt = (
            "You are reading a schedule management SOP. "
            "Answer exactly two yes/no questions based ONLY on what the SOP explicitly states. "
            "If the SOP is silent on a rule, answer with the safe default (true). "
            "Return ONLY valid JSON — no explanation."
        )
        user_msg = (
            f"SOP:\n{sop_text}\n\n"
            "Based solely on the SOP above, answer:\n"
            "{\n"
            '  "require_delete_confirmation": true/false,\n'
            '  // true  = SOP requires user to confirm before any DELETE executes (default if silent)\n'
            '  // false = SOP explicitly says to skip or bypass DELETE confirmation\n'
            '  "require_unlock_approval": true/false\n'
            '  // true  = SOP requires user approval before unlocking + deleting a locked schedule (default if silent)\n'
            '  // false = SOP explicitly says to auto-unlock and proceed without user approval\n'
            "}"
        )
        resp = llm_chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        flags = json.loads(resp or "{}")
        result = {
            "require_delete_confirmation": bool(flags.get("require_delete_confirmation", True)),
            "require_unlock_approval": bool(flags.get("require_unlock_approval", True)),
        }
        logger.info(f"[SOP] Behaviour flags: {result}")
        return result
    except Exception as e:
        logger.warning(f"[SOP] Failed to parse behaviour flags — using safe defaults: {e}")
        return _DEFAULTS


# MCPToolExecutor imported from utils.mcp_executor (centralized)


# ═══════════════════════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════════════════════

def _normalize_date(date_str: str) -> str:
    """
    Parse flexible date formats to YYYY-MM-DD.

    Supports:
    - "2026-02-12"
    - "12/02/2026"
    - "tomorrow"
    - "next Monday"

    Returns:
        ISO formatted date string (YYYY-MM-DD)
    """
    if not date_str or not isinstance(date_str, str):
        raise ValidationError(f"Invalid date: {date_str}")

    try:
        # Try parsing with dateutil (handles many formats)
        parsed = date_parser.parse(date_str, fuzzy=True)
        return parsed.strftime("%Y-%m-%d")
    except Exception as e:
        raise ValidationError(f"Could not parse date '{date_str}': {e}")


def _parse_blocks(blocks_value: Any) -> float:
    """
    Parse blocks (hours) value, supporting decimals.

    Supports:
    - Integers: 2 → 2.0 hours
    - Decimals: 1.5 → 1.5 hours, 1.30 → 1.3 hours
    - Strings: "1.5" → 1.5 hours

    Args:
        blocks_value: Hours value (int, float, or string)

    Returns:
        Number of hours as float
    """
    if not blocks_value:
        return 0.0

    try:
        # Convert to float to handle decimals
        hours = float(blocks_value)
        if hours < 0:
            raise ValidationError(f"Duration cannot be negative ({hours} hours). Please specify a positive number.")
        if hours > 16:
            raise ValidationError(f"Duration of {hours} hours seems too long. Maximum is 16 hours per schedule.")
        return hours
    except ValidationError:
        raise
    except (ValueError, TypeError) as e:
        raise ValidationError(f"Invalid blocks value '{blocks_value}': {e}")


# fuzzy_match_name imported from utils.fuzzy_match (centralized)


def _detect_context(row: Dict[str, Any]) -> str:
    """
    Detect if this is a job or quote schedule.

    Returns:
        "job" or "quote"
    """
    if "JobID" in row or "JobName" in row or "SiteName" in row:
        return "job"
    elif "QuoteID" in row or "QuoteName" in row:
        return "quote"
    else:
        raise ValidationError("Row must specify either Job (JobID/JobName/SiteName) or Quote (QuoteID/QuoteName)")


# ═══════════════════════════════════════════════════════════════════════════
# ID Resolution Logic (Using MCP Tools)
# ═══════════════════════════════════════════════════════════════════════════
# LLM-ASSISTED RESOLUTION PLANNING
# ═══════════════════════════════════════════════════════════════════════════

async def _generate_resolution_plan(
    operation: str,
    context: str,
    sample_row: Dict[str, Any],
    llm_chat: Callable,
    mcp_executor: Optional['MCPToolExecutor'] = None,
    sop_text: str = "",
) -> Dict[str, Any]:
    """
    LLM generates resolution plan ONCE per operation.
    Plan is executed deterministically for all rows.
    Dynamically discovers available tools from the MCP executor registry.
    SOP is injected so the LLM understands business rules when planning strategies.
    """
    provided = [k for k, v in sample_row.items() if v and v != ""]

    # Build tools list dynamically from MCP server
    tools_description = ""
    if mcp_executor:
        try:
            tool_descs = await mcp_executor.get_tool_descriptions()
            tool_lines = [f"- {name}: {desc}" for name, desc in tool_descs.items()]
            tools_description = '\n'.join(tool_lines)
        except Exception as e:
            logger.warning(f"Failed to fetch tool descriptions: {e}")
            tools_description = f"""- get_{context}_sections: list sections
- get_{context}_section_cost_centres: list cost centres in section
- get_schedules: get schedules"""
    else:
        tools_description = f"""- get_{context}_sections: list sections
- get_{context}_section_cost_centres: list cost centres in section
- get_schedules: get schedules"""

    system_prompt = (
        "You are a schedule resolution planner for a construction back-office system (Simpro ERP).\n"
        "Your job is to generate a field resolution strategy for bulk schedule operations.\n"
        "The SOP defines business rules — follow them when deciding resolution strategy and required fields.\n"
        "Return ONLY valid JSON — no explanation outside the JSON object."
    )

    sop_section = f"SOP (verbatim):\n{sop_text}\n\n" if sop_text else ""

    prompt = f"""{sop_section}Generate field resolution plan for schedule {operation}.

Context: {context}
Provided: {', '.join(provided)}

Required: {context}_id, section_id, cost_centre_id, staff_id, date, blocks

Tools available:
{tools_description}

Resolution strategies:
- If cost_centre_id given but section_id missing: reverse-lookup (query each section's cost centres)
- If staff_name given but staff_id missing: use the most appropriate tool to resolve staff name to ID (e.g. list_employees, list_contractors)
- If name given: search and resolve ID

Output JSON:
{{
  "required": ["field1", ...],
  "strategies": {{
    "section_id": {{
      "if_missing": [
        {{"when": "cost_centre_id_provided", "method": "reverse_lookup", "tools": ["get_{context}_sections", "get_{context}_section_cost_centres"]}}
      ]
    }},
    "staff_id": {{
      "if_missing": [
        {{"when": "staff_name_provided", "method": "search_by_name", "tools": ["<best_tool_for_staff_resolution>"]}}
      ]
    }}
  }}
}}"""

    try:
        resp = llm_chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        plan = json.loads(resp)
        logger.info(f"📋 LLM plan: {len(plan.get('strategies', {}))} strategies")
        return plan
    except:
        return {"required": [], "strategies": {}}


# ═══════════════════════════════════════════════════════════════════════════
# MODULAR FIELD RESOLUTION SYSTEM
# ═══════════════════════════════════════════════════════════════════════════

class FieldResolver:
    """
    LLM-assisted field resolver.

    Uses LLM-generated resolution plan to intelligently resolve missing fields.
    Falls back to built-in strategies if LLM plan doesn't cover a field.
    """

    def __init__(self, context: str, mcp_executor: MCPToolExecutor, resolution_plan: Optional[Dict[str, Any]] = None, llm_chat: Optional[Callable] = None, hints: Optional[Dict[str, Any]] = None, shared_state: Optional[AgentExecutionState] = None):
        self.context = context  # "job" or "quote"
        self.mcp_executor = mcp_executor
        self.plan = resolution_plan or {}
        self.llm_chat = llm_chat  # For crossroads LLM decisions
        self.resolver = EntityResolver(mcp_executor, llm_chat=llm_chat)
        self._hints = hints or {}   # Pre-resolved data from handoff collected_data
        self._state = shared_state  # AgentExecutionState for cross-row cache + logging

        # Log plan info
        if self.plan.get("strategies"):
            logger.debug(f"Using LLM plan with {len(self.plan['strategies'])} field strategies")
        else:
            logger.debug("No LLM plan provided, using built-in strategies")

    async def resolve_all(self, row: Dict[str, Any], row_num: int, operation: str = "CREATE") -> Dict[str, Any]:
        """
        Resolve all fields in optimal order based on what's provided.

        Resolution strategies (in priority order):
        0. If ScheduleID + key IDs already provided (e.g. from bulk action): use directly
        1. For UPDATE/DELETE with only StaffName+Date: Use get_schedules to find all IDs at once
        2. If IDs provided: Use them directly
        3. If Names provided: Resolve via hierarchy
        4. If parent + cost_centre but no section: Reverse-lookup section
        """
        resolved = {"row_number": row_num}

        # STRATEGY 0: ScheduleID already known (from bulk action or clarification response).
        # Grab whatever IDs are available from the row, then fill missing ones via API.
        if row.get("ScheduleID") and operation in ["UPDATE", "DELETE", "LOCK", "UNLOCK"]:
            resolved["schedule_id"] = int(row["ScheduleID"])
            for field, key in [("StaffID", "staff_id"), ("JobID", "job_id"),
                               ("CostCentreID", "cost_centre_id"), ("SectionID", "section_id")]:
                val = row.get(field, "").strip()
                if val and val.isdigit():
                    resolved[key] = int(val)
            # Carry over existing schedule data for UPDATE field assembly
            if row.get("StartTime"):
                resolved["existing_start_time"] = row["StartTime"]
            if row.get("Blocks"):
                resolved["existing_blocks"] = float(row["Blocks"]) if row["Blocks"] else None
            # Capture lock status from the row (set by _expand_bulk_action)
            is_locked_raw = str(row.get("IsLocked", "")).lower().strip()
            if is_locked_raw in ("true", "1"):
                resolved["existing_is_locked"] = True
            elif is_locked_raw in ("false", "0"):
                resolved["existing_is_locked"] = False

            # If critical IDs are missing (e.g. ScheduleID came from a clarification
            # response without the other IDs), fetch them via get_schedule_details.
            needs_details = (
                not resolved.get("job_id")
                or not resolved.get("cost_centre_id")
                or not resolved.get("staff_id")
            )
            if needs_details:
                try:
                    logger.info(f"Row {row_num}: ScheduleID={resolved['schedule_id']} but missing IDs — fetching schedule details")
                    detail_result = await self.mcp_executor.call_tool("get_schedule_details", {
                        "schedule_id": resolved["schedule_id"]
                    })
                    schedule = detail_result.get("schedule", {})

                    if not resolved.get("staff_id") and schedule.get("Staff", {}).get("ID"):
                        resolved["staff_id"] = schedule["Staff"]["ID"]

                    schedule_type = (schedule.get("Type") or "").lower()
                    reference = schedule.get("Reference", "")

                    if schedule_type == "job" and reference and "-" in reference:
                        parts = reference.split("-")
                        if not resolved.get("job_id"):
                            resolved["job_id"] = int(parts[0])
                        if not resolved.get("cost_centre_id"):
                            resolved["cost_centre_id"] = int(parts[1])
                        logger.info(f"Row {row_num}: From schedule details: JobID={resolved.get('job_id')}, CostCentreID={resolved.get('cost_centre_id')}")

                    # Capture existing schedule data
                    blocks_array = schedule.get("Blocks", [])
                    if blocks_array:
                        first_block = blocks_array[0]
                        resolved["existing_start_time"] = first_block.get("StartTime", "")
                        resolved["existing_blocks"] = float(first_block.get("Hrs", 0))
                    resolved["existing_notes"] = schedule.get("Notes", "")
                    resolved["existing_date"] = schedule.get("Date", "")
                    resolved["existing_is_locked"] = schedule.get("IsLocked", False)

                except Exception as e:
                    logger.warning(f"Row {row_num}: get_schedule_details failed: {e}")

            # Reverse-lookup SectionID if we have CostCentreID + JobID but no SectionID
            # (bulk action rows have CostCentreID from the schedule Reference but not SectionID)
            if not resolved.get("section_id") and resolved.get("cost_centre_id") and resolved.get("job_id"):
                try:
                    sec_id = await self.resolver.find_section_for_cost_centre(
                        job_id=resolved["job_id"],
                        cost_centre_id=resolved["cost_centre_id"],
                        context=self.context,
                        row_num=row_num,
                    )
                    if sec_id is not None:
                        resolved["section_id"] = sec_id
                except Exception as e:
                    logger.warning(f"Row {row_num}: Section reverse-lookup failed: {e}")

            logger.info(f"Row {row_num}: ✅ All IDs pre-resolved (ScheduleID={resolved['schedule_id']})")
            return resolved

        # STRATEGY 1: For UPDATE/DELETE with StaffName+Date, try get_schedules lookup first.
        # This resolves ALL IDs (schedule_id, section_id, cost_centre_id) in one shot —
        # works even when JobID is provided (e.g. from conversation history).
        if operation in ["UPDATE", "DELETE"]:
            if row.get("StaffName") and row.get("Date"):
                logger.info(f"Row {row_num}: {operation} with StaffName+Date - trying get_schedules lookup")
                schedule_data = await self._lookup_schedule_by_staff_date(row, row_num)

                if schedule_data:
                    # Check for bulk-expand marker (DELETE all schedules for this staff)
                    if "_expand_all" in schedule_data:
                        resolved["_expand_all"] = schedule_data["_expand_all"]
                        logger.info(f"Row {row_num}: ✅ DELETE-all expansion: {len(schedule_data['_expand_all'])} schedules")
                        return resolved
                    resolved.update(schedule_data)
                    logger.info(f"Row {row_num}: ✅ ALL IDs resolved via get_schedules")
                    return resolved
                else:
                    logger.warning(f"Row {row_num}: get_schedules lookup failed - falling back to normal resolution")

        # STRATEGY 2-4: Phased resolution via resolve_batch
        # Independent fields (staff, job) are resolved in Phase 0.
        # Dependent fields (section→job, cost_centre→section+job) follow.
        # If multiple independent fields need clarification they are
        # collected and raised together so the user sees them all at once.
        parent_key = f"{self.context}_id"   # "job_id" or "quote_id"

        async def _staff_task(_partial):
            r = await self._resolve_staff(row, row_num, {})
            return {"id": r["staff_id"]}

        async def _parent_task(_partial):
            r = await self._resolve_parent(row, row_num, {})
            return {"id": r[parent_key]}

        async def _section_task(partial):
            r = await self._resolve_section(row, row_num, {parent_key: partial[parent_key]})
            return {"id": r["section_id"]}

        async def _cost_centre_task(partial):
            r = await self._resolve_cost_centre(
                row, row_num,
                {parent_key: partial[parent_key], "section_id": partial["section_id"]},
            )
            return {"id": r["cost_centre_id"]}

        batch_result = await self.resolver.resolve_batch(
            tasks=[
                {"key": "staff_id",       "resolve": _staff_task,        "depends_on": []},
                {"key": parent_key,       "resolve": _parent_task,       "depends_on": []},
                {"key": "section_id",     "resolve": _section_task,      "depends_on": [parent_key]},
                {"key": "cost_centre_id", "resolve": _cost_centre_task,  "depends_on": [parent_key, "section_id"]},
            ],
            row_num=row_num,
        )
        resolved.update(batch_result)
        return resolved

    async def _resolve_parent(self, row: Dict[str, Any], row_num: int, resolved: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve job_id or quote_id — delegates to central EntityResolver."""
        id_field = "JobID" if self.context == "job" else "QuoteID"
        name_field = "JobName" if self.context == "job" else "QuoteName"
        parent_key = f"{self.context}_id"

        # If JobName/QuoteName contains a pure integer it was written back by a
        # clarification round (user selected from ambiguous-match dropdown).
        # Treat it as a pre-resolved ID to avoid re-resolving a numeric string as a name.
        job_id = int(row[id_field]) if row.get(id_field) else None
        name_val = row.get(name_field)
        if not job_id and name_val:
            try:
                job_id = int(name_val)
                name_val = None  # don't pass numeric string as a name
                logger.info(f"Row {row_num}: {name_field} contains resolved ID={job_id} (from clarification) — skipping name lookup")
            except (ValueError, TypeError):
                pass

        # Check cross-row cache before calling API (key on name_val or job_id)
        _cache_key = name_val or (str(job_id) if job_id else None)
        if self._state and _cache_key and not job_id:
            _cached = self._state.get_entity("Job", _cache_key)
            if _cached:
                resolved[parent_key] = _cached["id"]
                self._state.log_resolution("Job", _cache_key, _cached["id"], _cached.get("name"), "cache_hit", row_num)
                return resolved

        try:
            result = await self.resolver.resolve_job(
                name=name_val,
                job_id=job_id,
                site_name=row.get("SiteName") if self.context == "job" else None,
                row_num=row_num,
            )
        except AmbiguousResolutionError:
            if self._state and _cache_key:
                self._state.log_resolution("Job", _cache_key, outcome="ambiguous", row_num=row_num)
            raise
        except ResolutionError:
            if self._state and _cache_key:
                self._state.log_resolution("Job", _cache_key, outcome="not_found", row_num=row_num)
            raise

        resolved[parent_key] = result["id"]
        if self._state and _cache_key and not job_id:
            self._state.cache_entity("Job", _cache_key, result["id"], result.get("name", ""))
            self._state.log_resolution("Job", _cache_key, result["id"], result.get("name"), "resolved", row_num)
        return resolved

    async def _resolve_section(self, row: Dict[str, Any], row_num: int, resolved: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve section_id — delegates to central EntityResolver."""
        parent_id_key = "job_id" if self.context == "job" else "quote_id"
        parent_id = resolved[parent_id_key]

        # If SectionName contains a pure integer it was written back by a clarification
        # round. Treat it as a pre-resolved ID instead of re-resolving as a name.
        section_id = int(row["SectionID"]) if row.get("SectionID") else None
        section_name = row.get("SectionName")
        if not section_id and section_name:
            try:
                section_id = int(section_name)
                section_name = None
                logger.info(f"Row {row_num}: SectionName contains resolved ID={section_id} (from clarification) — skipping name lookup")
            except (ValueError, TypeError):
                pass

        result = await self.resolver.resolve_section(
            job_id=parent_id,
            name=section_name,
            section_id=section_id,
            cost_centre_id=int(row["CostCentreID"]) if row.get("CostCentreID") else None,
            cost_centre_name=row.get("CostCentreName") or None,
            context=self.context,
            row_num=row_num,
        )
        resolved["section_id"] = result["id"]
        return resolved

    async def _resolve_cost_centre(self, row: Dict[str, Any], row_num: int, resolved: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve cost_centre_id — delegates to central EntityResolver."""
        parent_id_key = "job_id" if self.context == "job" else "quote_id"
        parent_id = resolved[parent_id_key]
        section_id = resolved["section_id"]

        # If CostCentreName contains a pure integer it was written back by a clarification
        # round. Treat it as a pre-resolved ID instead of re-resolving as a name.
        cc_id = int(row["CostCentreID"]) if row.get("CostCentreID") else None
        cc_name = row.get("CostCentreName")
        if not cc_id and cc_name:
            try:
                cc_id = int(cc_name)
                cc_name = None
                logger.info(f"Row {row_num}: CostCentreName contains resolved ID={cc_id} (from clarification) — skipping name lookup")
            except (ValueError, TypeError):
                pass

        result = await self.resolver.resolve_cost_centre(
            job_id=parent_id,
            section_id=section_id,
            name=cc_name,
            cost_centre_id=cc_id,
            context=self.context,
            row_num=row_num,
        )
        resolved["cost_centre_id"] = result["id"]
        return resolved

    async def _resolve_staff(self, row: Dict[str, Any], row_num: int, resolved: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve staff_id — delegates to central EntityResolver.

        Short-circuits if a pre-resolved staff_id or contractor_id was
        supplied via the handoff collected_data (avoids duplicate MCP calls).
        """
        # Pre-resolved check: staff_id or contractor_id already known from chain context
        pre = (self._hints or {}).get("pre_resolved", {})
        pre_staff_id = (
            pre.get("staff_id")
            or pre.get("contractor_id")      # contractor acting as staff
            or (int(row["StaffID"]) if row.get("StaffID") and str(row["StaffID"]).isdigit() else None)
        )

        # Also detect when StaffName was overwritten with a numeric ID by a clarification
        # round (user selected from the ambiguous-match dropdown; chat.py writes the ID
        # into StaffName because that's the field the AmbiguousResolutionError named).
        if not pre_staff_id and row.get("StaffName"):
            try:
                pre_staff_id = int(row["StaffName"])
                logger.info(f"Row {row_num}: StaffName contains resolved ID={pre_staff_id} (from clarification) — skipping name lookup")
            except (ValueError, TypeError):
                pass

        if pre_staff_id:
            logger.info(f"Row {row_num}: ✅ staff_id={pre_staff_id} from pre_resolved — skipping lookup")
            resolved["staff_id"] = int(pre_staff_id)
            if pre.get("staff_name") and not row.get("StaffName"):
                row["StaffName"] = pre["staff_name"]
            return resolved

        staff_input = row.get("StaffName")
        # Check cross-row cache before calling API
        if self._state and staff_input:
            _cached = self._state.get_entity("Staff", staff_input)
            if _cached:
                resolved["staff_id"] = _cached["id"]
                self._state.log_resolution("Staff", staff_input, _cached["id"], _cached.get("name"), "cache_hit", row_num)
                return resolved

        try:
            result = await self.resolver.resolve_staff(
                name=staff_input,
                staff_id=None,
                row_num=row_num,
            )
        except AmbiguousResolutionError:
            if self._state and staff_input:
                self._state.log_resolution("Staff", staff_input, outcome="ambiguous", row_num=row_num)
            raise
        except ResolutionError:
            if self._state and staff_input:
                self._state.log_resolution("Staff", staff_input, outcome="not_found", row_num=row_num)
            raise

        resolved["staff_id"] = result["id"]
        # Update row with API-resolved name so downstream display
        # (clarifications, logs) shows the real name, not user input
        if result.get("name"):
            row["StaffName"] = result["name"]
        # Cache for subsequent rows
        if self._state and staff_input:
            self._state.cache_entity("Staff", staff_input, result["id"], result.get("name", ""))
            self._state.log_resolution("Staff", staff_input, result["id"], result.get("name"), "resolved", row_num)
        return resolved

    async def _lookup_schedule_by_staff_date(
        self,
        row: Dict[str, Any],
        row_num: int
    ) -> Optional[Dict[str, Any]]:
        """
        Universal schedule lookup using get_schedules (by date only).

        For UPDATE/DELETE when JobID/QuoteID is missing, use get_schedules
        to find the schedule by StaffName + Date, then extract ALL IDs:
        - job_id/quote_id
        - section_id
        - cost_centre_id
        - staff_id
        - schedule_id

        This is the MOST FLEXIBLE resolution strategy - works with minimal input.
        """
        staff_name = row.get("StaffName", "").strip()
        target_date = _normalize_date(row.get("Date", ""))

        if not staff_name or not target_date:
            missing = []
            if not staff_name: missing.append("StaffName")
            if not target_date: missing.append("Date")
            raise ResolutionError(f"Row {row_num}: Cannot lookup schedule — missing {', '.join(missing)}")

        logger.info(f"Row {row_num}: Using get_schedules to find '{staff_name}' on {target_date}")

        try:
            # Resolve staff name → StaffID first (Simpro schedule responses may
            # not include staff names, only Staff.ID)
            resolved_staff_id = row.get("StaffID")
            resolved_staff_name = staff_name  # Will be updated to API name once resolved
            ambiguous_staff_ids = []  # IDs from AmbiguousResolutionError, used for schedule cross-ref
            original_ambiguous_error = None  # Preserved for re-raise if auto-recovery fails
            staff_id_name_map = {}  # ID → Name for enriching clarification options

            # If StaffName contains a pure integer, a clarification round wrote a
            # resolved staff ID back into the Name field (chat.py merges by field name
            # from the AmbiguousResolutionError, which is "StaffName"). Use it directly.
            if not resolved_staff_id and staff_name:
                try:
                    resolved_staff_id = int(staff_name)
                    resolved_staff_name = staff_name
                    logger.info(f"Row {row_num}: StaffName contains resolved ID={resolved_staff_id} (from clarification) — skipping staff lookup")
                except (ValueError, TypeError):
                    pass

            if not resolved_staff_id:
                try:
                    staff_result = await self.resolver.resolve_staff(
                        name=staff_name,
                        row_num=row_num,
                    )
                    resolved_staff_id = staff_result["id"]
                    resolved_staff_name = staff_result["name"] or staff_name
                    logger.info(f"Row {row_num}: Resolved staff '{staff_name}' → ID={resolved_staff_id} ({resolved_staff_name})")
                except AmbiguousResolutionError as e:
                    # Capture candidate IDs — we'll try cross-referencing against schedules.
                    # Keep the original error so we can re-raise if auto-recovery fails.
                    ambiguous_staff_ids = [m["id"] for m in e.matches]
                    original_ambiguous_error = e
                    logger.warning(f"Row {row_num}: Staff resolution ambiguous ({len(e.matches)} candidates: {ambiguous_staff_ids}), will cross-ref with schedules")
                except ResolutionError:
                    logger.warning(f"Row {row_num}: Staff resolution failed, will try schedule matching by name")
                except Exception as e:
                    logger.warning(f"Row {row_num}: Staff ID lookup failed: {e}")

            # Query all schedules for the target date
            result = await self.mcp_executor.call_tool("get_schedules", {
                "date": target_date
            })

            schedules = result.get("schedules", [])
            logger.info(f"Row {row_num}: Found {len(schedules)} total schedules on {target_date}")

            # Match by StaffID if resolved, otherwise fall back to fuzzy name matching
            matching = []
            if resolved_staff_id:
                # Check both Staff.ID and Staff.TypeId — for contractors,
                # list_contractors returns the contractor ID which may appear
                # as Staff.TypeId in schedules (Staff.ID can differ).
                matching = [
                    s for s in schedules
                    if s.get("Staff", {}).get("ID") == resolved_staff_id
                    or s.get("Staff", {}).get("TypeId") == resolved_staff_id
                ]
            elif ambiguous_staff_ids and schedules:
                # Staff resolution was ambiguous — cross-reference candidate IDs
                # against schedules to see which candidate actually has a schedule
                # on this date. This resolves the ambiguity without asking the user.
                schedule_staff_ids = set()
                for s in schedules:
                    si = s.get("Staff", {})
                    schedule_staff_ids.add(si.get("ID"))
                    schedule_staff_ids.add(si.get("TypeId"))
                schedule_staff_ids.discard(None)

                found_ids = [aid for aid in ambiguous_staff_ids if aid in schedule_staff_ids]
                if len(found_ids) == 1:
                    # Exactly one ambiguous candidate has a schedule — auto-select
                    resolved_staff_id = found_ids[0]
                    matching = [
                        s for s in schedules
                        if s.get("Staff", {}).get("ID") == resolved_staff_id
                        or s.get("Staff", {}).get("TypeId") == resolved_staff_id
                    ]
                    # Update resolved name from the matching schedule's Staff.Name
                    if matching:
                        api_name = (matching[0].get("Staff", {}).get("Name") or "").strip()
                        if api_name:
                            resolved_staff_name = api_name
                    # Also look up from original ambiguous error matches
                    if original_ambiguous_error:
                        for m in original_ambiguous_error.matches:
                            if m["id"] == resolved_staff_id and m.get("name"):
                                resolved_staff_name = m["name"]
                                break
                    logger.info(f"Row {row_num}: Disambiguated via schedule cross-ref: StaffID={resolved_staff_id} ({resolved_staff_name})")
                elif len(found_ids) > 1:
                    logger.info(f"Row {row_num}: Multiple ambiguous candidates have schedules: {found_ids}, falling back to fuzzy matching")

            if not matching and not resolved_staff_id:
                # Build candidates from schedule staff info for fuzzy matching
                staff_candidates = []
                for s in schedules:
                    si = s.get("Staff", {})
                    sname = (si.get("Name") or "").strip()
                    if not sname:
                        given = (si.get("GivenName") or "").strip()
                        family = (si.get("FamilyName") or "").strip()
                        sname = f"{given} {family}".strip()
                    if sname and si.get("ID"):
                        staff_candidates.append({"ID": si["ID"], "Name": sname})
                fm = fuzzy_match_entities(staff_name, staff_candidates, source="get_schedules")
                # Only keep matches close to the best score to avoid unrelated
                # entities that share a single common word (e.g., "plumbing").
                if fm:
                    best_score = fm[0]["score"]
                    matched_ids = {m["id"] for m in fm if m["score"] >= best_score - 10}
                    # Update to API name from best fuzzy match
                    if fm[0].get("name"):
                        resolved_staff_name = fm[0]["name"]
                    logger.info(f"Row {row_num}: Schedule fuzzy: accepted {len(matched_ids)} match(es), top={fm[0]['name']} (score={fm[0]['score']})")
                else:
                    matched_ids = set()
                matching = [s for s in schedules if s.get("Staff", {}).get("ID") in matched_ids]

            if not matching and schedules and self.llm_chat:
                # Schedules exist but we couldn't match by name or ID.
                # Ask crossroads to figure it out — give it full context:
                # all schedules found, their Staff IDs, and the target staff name.
                logger.info(f"Row {row_num}: Name/ID matching failed — asking crossroads to resolve")
                schedule_summaries = []
                for s in schedules[:10]:
                    s_staff = s.get("Staff", {})
                    s_blocks = s.get("Blocks", [])
                    s_name = (s_staff.get("Name") or "").strip()
                    if not s_name:
                        s_name = f"{(s_staff.get('GivenName') or '')} {(s_staff.get('FamilyName') or '')}".strip() or None
                    schedule_summaries.append({
                        "schedule_id": s.get("ID"),
                        "staff_id": s_staff.get("ID"),
                        "staff_name": s_name,
                        "type": s.get("Type"),
                        "reference": s.get("Reference"),
                        "start_time": s_blocks[0].get("StartTime") if s_blocks else None,
                        "hours": s_blocks[0].get("Hrs") if s_blocks else None,
                    })
                try:
                    cr_result = await resolve_crossroads(
                        crossroad_type="ambiguous_match",
                        question=(
                            f"I need to find '{resolved_staff_name}' among {len(schedules)} schedules on {target_date}. "
                            f"The schedules don't have staff names, only Staff IDs. "
                            f"I also resolved '{resolved_staff_name}' to StaffID={resolved_staff_id} via employee list. "
                            f"Which schedule belongs to '{resolved_staff_name}'?"
                        ),
                        context={
                            "query": resolved_staff_name,
                            "resolved_staff_id": resolved_staff_id,
                            "candidates": schedule_summaries,
                            "operation": (row.get("Operation") or "").upper(),
                        },
                        llm_chat=self.llm_chat,
                    )
                    if cr_result.get("decision") == "select" and cr_result.get("fields", {}).get("selected_id"):
                        selected_id = int(cr_result["fields"]["selected_id"])
                        selected = [s for s in schedules if s.get("ID") == selected_id]
                        if selected:
                            matching = selected
                            logger.info(f"Row {row_num}: 🔀 Crossroads matched staff to schedule ID={selected_id}")
                    elif cr_result.get("decision") == "select" and cr_result.get("fields", {}).get("staff_id"):
                        # Crossroads might return which staff_id to match
                        match_staff_id = int(cr_result["fields"]["staff_id"])
                        matching = [s for s in schedules if s.get("Staff", {}).get("ID") == match_staff_id]
                        if matching:
                            logger.info(f"Row {row_num}: 🔀 Crossroads matched via staff_id={match_staff_id}")
                except Exception as e:
                    logger.warning(f"Row {row_num}: Crossroads schedule matching failed: {e}")

            if not matching:
                # If the original issue was ambiguous staff resolution and all
                # auto-recovery attempts failed, re-raise the AmbiguousResolutionError
                # so _process_single_row can show a clarification form to the user.
                if original_ambiguous_error:
                    logger.info(f"Row {row_num}: Auto-recovery failed — re-raising staff ambiguity for user clarification")
                    raise original_ambiguous_error

                if schedules:
                    staff_ids_found = [s.get("Staff", {}).get("ID") for s in schedules]
                    raise ResolutionError(
                        f"Row {row_num}: {len(schedules)} schedules found on {target_date} but none match "
                        f"staff '{resolved_staff_name}' (resolved StaffID={resolved_staff_id}). "
                        f"Staff IDs in schedules: {staff_ids_found}",
                        partial_data={
                            "schedules_found": schedules,
                            "target_date": target_date,
                            "staff_id_name_map": staff_id_name_map,
                        },
                    )
                else:
                    raise ResolutionError(
                        f"Row {row_num}: No schedules found at all on {target_date}",
                        partial_data={"target_date": target_date},
                    )

            logger.info(f"Row {row_num}: Matched {len(matching)} schedules for '{resolved_staff_name}' out of {len(schedules)} total")

            # Filter by JobID if provided (e.g. from conversation history)
            row_job_id = row.get("JobID")
            if row_job_id and len(matching) > 1:
                job_id_str = str(row_job_id)
                job_filtered = [
                    s for s in matching
                    if (s.get("Type") or "").lower() == "job"
                    and str(s.get("Reference", "")).split("-")[0] == job_id_str
                ]
                if job_filtered:
                    matching = job_filtered
                    logger.info(f"Row {row_num}: Filtered by JobID={row_job_id} → {len(matching)} schedule(s)")

            # Filter by CostCentreID if provided
            row_cc_id = row.get("CostCentreID")
            if row_cc_id and len(matching) > 1:
                cc_id_str = str(row_cc_id)
                cc_filtered = [
                    s for s in matching
                    if (s.get("Type") or "").lower() == "job"
                    and "-" in str(s.get("Reference", ""))
                    and str(s.get("Reference", "")).split("-")[1] == cc_id_str
                ]
                if cc_filtered:
                    matching = cc_filtered
                    logger.info(f"Row {row_num}: Filtered by CostCentreID={row_cc_id} → {len(matching)} schedule(s)")

            # If still multiple schedules, prioritize job schedules over activity schedules
            if len(matching) > 1:
                # Try to find a job schedule first
                job_schedules = [s for s in matching if (s.get("Type") or "").lower() == "job"]
                if job_schedules:
                    matching = job_schedules
                    logger.info(f"Row {row_num}: Prioritizing job schedule over other types")
                else:
                    logger.warning(f"Row {row_num}: No job schedules found, using first schedule")

            # If still multiple matches after all filtering, ask crossroads LLM
            if len(matching) > 1:
                options = []
                for s in matching[:5]:
                    ref = s.get("Reference", "")
                    stype = (s.get("Type") or "").lower()
                    blocks_arr = s.get("Blocks", [])
                    time_str = blocks_arr[0].get("StartTime", "?") if blocks_arr else "?"
                    hrs = blocks_arr[0].get("Hrs", "?") if blocks_arr else "?"
                    options.append({
                        "id": s.get("ID"),
                        "name": f"Schedule {s.get('ID')} — {stype} {ref}, {time_str} ({hrs}hrs)"
                    })

                # Ask crossroads LLM to pick the best schedule
                if self.llm_chat:
                    try:
                        cr_result = await resolve_crossroads(
                            crossroad_type="ambiguous_match",
                            question=f"Found {len(matching)} schedules for '{resolved_staff_name}' on {target_date}. Which one?",
                            context={
                                "query": f"{resolved_staff_name} on {target_date}",
                                "candidates": options,
                                "operation": (row.get("Operation") or "").upper(),
                                "job_id": row.get("JobID"),
                                "cost_centre_id": row.get("CostCentreID"),
                                "row_num": row_num,
                            },
                            llm_chat=self.llm_chat,
                        )
                        if cr_result.get("decision") == "select" and cr_result.get("fields", {}).get("selected_id"):
                            selected_id = int(cr_result["fields"]["selected_id"])
                            # Find the matching schedule by ID
                            selected = [s for s in matching if s.get("ID") == selected_id]
                            if selected:
                                matching = selected
                                logger.info(f"Row {row_num}: 🔀 Crossroads selected schedule ID={selected_id}")
                            else:
                                logger.warning(f"Row {row_num}: Crossroads selected ID={selected_id} not found in matches")
                        else:
                            logger.info(f"Row {row_num}: Crossroads said 'clarify' for schedule — asking user")
                    except Exception as e:
                        logger.warning(f"Row {row_num}: Schedule crossroads failed ({e}), falling back to user")

            # If STILL multiple after crossroads:
            # For DELETE with no specific job/CC filter → delete ALL (bulk intent)
            # For other operations or when specific filters were given → ask user
            if len(matching) > 1:
                operation = (row.get("Operation") or "").upper()
                has_specific_filter = bool(row.get("JobID") or row.get("CostCentreID") or row.get("ScheduleID"))
                if operation == "DELETE" and not has_specific_filter:
                    # User wants to delete all schedules for this staff on this date
                    # Return special marker so caller can expand into multiple rows
                    logger.info(f"Row {row_num}: DELETE with {len(matching)} schedules, no specific job — expanding all")
                    return {"_expand_all": matching}
                if not options:
                    options = [{"id": s.get("ID"), "name": f"Schedule {s.get('ID')}"} for s in matching[:5]]
                raise AmbiguousResolutionError(
                    field="ScheduleID",
                    value=f"{resolved_staff_name} on {target_date}",
                    matches=options,
                    message=f"Row {row_num}: Found {len(matching)} schedules for '{resolved_staff_name}' on {target_date}. Which one?"
                )

            # Extract ALL IDs from the found schedule
            schedule = matching[0]
            resolved = {}

            resolved["schedule_id"] = schedule.get("ID")
            resolved["staff_id"] = schedule.get("Staff", {}).get("ID")

            # Simpro's get_schedules returns Type="job" and Reference="JobID-CostCentreID"
            schedule_type = (schedule.get("Type") or "").lower()
            reference = schedule.get("Reference", "")

            logger.info(f"Row {row_num}: Schedule Type='{schedule_type}', Reference='{reference}'")

            if schedule_type == "job" and reference and "-" in reference:
                # Parse Reference: "20990-116534" → JobID=20990, CostCentreID=116534
                try:
                    parts = reference.split("-")
                    resolved["job_id"] = int(parts[0])
                    resolved["cost_centre_id"] = int(parts[1])
                    self.context = "job"
                    logger.info(f"Row {row_num}: Parsed Reference → JobID={resolved['job_id']}, CostCentreID={resolved['cost_centre_id']}")
                except (ValueError, IndexError) as e:
                    raise ResolutionError(
                        f"Row {row_num}: Failed to parse schedule Reference '{reference}': {e}"
                    )

                # Now find section_id by querying job sections (via central resolver)
                sec_id = await self.resolver.find_section_for_cost_centre(
                    job_id=resolved["job_id"],
                    cost_centre_id=resolved["cost_centre_id"],
                    context="job",
                    row_num=row_num,
                )
                if sec_id is not None:
                    resolved["section_id"] = sec_id
                else:
                    raise ResolutionError(
                        f"Row {row_num}: CostCentreID={resolved['cost_centre_id']} not found in any section of JobID={resolved['job_id']}"
                    )

            elif schedule_type == "quote":
                raise ResolutionError(
                    f"Row {row_num}: Found quote schedule (ID={resolved['schedule_id']}) — quote schedules are not yet supported"
                )
            elif schedule_type == "activity":
                raise ResolutionError(
                    f"Row {row_num}: Found activity schedule (ID={resolved['schedule_id']}) — only job schedules can be managed via chat"
                )
            else:
                raise ResolutionError(
                    f"Row {row_num}: Schedule Type='{schedule_type}' with Reference='{reference}' is not supported"
                )

            # IMPORTANT: Also preserve existing schedule details (start_time, blocks, notes)
            # This allows "change to tomorrow for the same time" to work correctly
            # NOTE: Simpro's get_schedules returns Blocks as an array like:
            # [{"StartTime": "08:00", "Hrs": 3, "EndTime": "11:00", ...}]
            blocks_array = schedule.get("Blocks", [])
            if blocks_array and len(blocks_array) > 0:
                first_block = blocks_array[0]
                resolved["existing_start_time"] = first_block.get("StartTime", "")
                resolved["existing_blocks"] = float(first_block.get("Hrs", 0))
            else:
                resolved["existing_start_time"] = ""
                resolved["existing_blocks"] = 0.0

            resolved["existing_notes"] = schedule.get("Notes", "")
            resolved["existing_date"] = schedule.get("Date", "")
            resolved["existing_is_locked"] = schedule.get("IsLocked", False)

            # Update row with API-resolved name so downstream display
            # (clarifications, _build_row_context) shows the real name, not user input
            if resolved_staff_name != staff_name:
                row["StaffName"] = resolved_staff_name

            logger.info(f"Row {row_num}: ✅ Resolved ALL IDs from get_schedules: "
                       f"schedule={resolved['schedule_id']}, "
                       f"job={resolved.get('job_id')}, "
                       f"section={resolved.get('section_id')}, "
                       f"cost_centre={resolved.get('cost_centre_id')}, "
                       f"staff={resolved['staff_id']}, "
                       f"existing_time={resolved.get('existing_start_time')}, "
                       f"existing_blocks={resolved.get('existing_blocks')}")

            return resolved

        except (ResolutionError, AmbiguousResolutionError):
            raise  # Re-raise our own errors
        except Exception as e:
            raise ResolutionError(f"Row {row_num}: Schedule lookup failed: {e}")

    # Helper methods

    # _get_sections, _get_cost_centres, _find_section_by_cost_centre
    # are now handled by self.resolver (EntityResolver) methods


# ═══════════════════════════════════════════════════════════════════════════

async def _resolve_row_identifiers(
    row: Dict[str, Any],
    row_num: int,
    mcp_executor: MCPToolExecutor,
    context: str,  # "job" or "quote"
    resolution_plan: Optional[Dict[str, Any]] = None,
    llm_chat: Optional[Callable] = None,
    hints: Optional[Dict[str, Any]] = None,
    shared_state: Optional["AgentExecutionState"] = None,
) -> Dict[str, Any]:
    """
    Resolve all identifiers for a single row using LLM-assisted field resolver.

    Raises clarification exceptions if ambiguous or missing.

    Args:
        row: Row data from Excel
        row_num: Row number (for error reporting)
        mcp_executor: MCP tool executor
        context: "job" or "quote"
        resolution_plan: LLM-generated resolution plan (optional)
        llm_chat: LLM chat function for crossroads decisions
        hints: Optional pre-resolved data from handoff collected_data
        shared_state: AgentExecutionState for cross-row cache + logging

    Returns:
        Dict with resolved IDs: {job_id, section_id, cost_centre_id, staff_id, ...}
    """
    # Use LLM-assisted FieldResolver (hints enables pre_resolved short-circuits)
    resolver = FieldResolver(context, mcp_executor, resolution_plan, llm_chat=llm_chat, hints=hints, shared_state=shared_state)
    operation = (row.get("Operation") or "CREATE").upper()
    return await resolver.resolve_all(row, row_num, operation)


# ═══════════════════════════════════════════════════════════════════════════
# Resolution Recovery Loop
# ═══════════════════════════════════════════════════════════════════════════

def _substitute_params(
    template: Dict[str, Any],
    collected: Dict[str, Any],
    stuck: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Replace $collected.X and $stuck.X references in strategy step params
    with actual values from collected data and stuck point context.
    """
    result = {}
    for key, val in template.items():
        if isinstance(val, str):
            if val.startswith("$collected."):
                ref_key = val[len("$collected."):]
                result[key] = collected.get(ref_key)
            elif val.startswith("$stuck."):
                ref_key = val[len("$stuck."):]
                result[key] = stuck.get(ref_key)
            else:
                result[key] = val
        else:
            result[key] = val
    return result


def _extract_from_result(
    result: Any,
    extract_path: str,
    match_by: Optional[str] = None,
    match_value: Optional[str] = None,
) -> Any:
    """
    Extract a value from a tool result using a dot-path.

    Args:
        result: Tool result dict
        extract_path: e.g. "employees", "schedules[0].Staff.ID"
        match_by: Optional field to fuzzy-match within a list
        match_value: Value to match against

    Returns:
        Extracted value, or None if not found
    """
    # Navigate the path
    current = result
    for part in extract_path.split("."):
        if current is None:
            return None
        # Handle array index notation e.g. "schedules[0]"
        if "[" in part:
            key, idx_str = part.rstrip("]").split("[")
            current = current.get(key, []) if isinstance(current, dict) else None
            if current and isinstance(current, list):
                try:
                    current = current[int(idx_str)]
                except (IndexError, ValueError):
                    return None
            else:
                return None
        else:
            current = current.get(part) if isinstance(current, dict) else None

    # If we got a list and need to match within it
    if isinstance(current, list) and match_by and match_value:
        match_lower = str(match_value).lower()
        for item in current:
            field_val = str(item.get(match_by, "")).lower()
            full_name = ""
            # Handle name matching (GivenName + FamilyName)
            if match_by == "Name" and not field_val:
                given = str(item.get("GivenName", "")).strip()
                family = str(item.get("FamilyName", "")).strip()
                full_name = f"{given} {family}".strip().lower()
                field_val = full_name
            if field_val and (match_lower in field_val or field_val in match_lower):
                return item
        return None

    return current


async def _resolve_with_recovery(
    row: Dict[str, Any],
    row_num: int,
    mcp_executor: MCPToolExecutor,
    context: str,
    resolution_plan: Optional[Dict[str, Any]] = None,
    llm_chat: Optional[Callable] = None,
    tool_descriptions: Optional[Dict[str, str]] = None,
    tracker: Optional[RequestTracker] = None,
    hints: Optional[Dict[str, Any]] = None,
    shared_state: Optional["AgentExecutionState"] = None,
) -> Dict[str, Any]:
    """
    Wrap _resolve_row_identifiers with an intelligent recovery loop.

    1. Try normal resolution
    2. If it fails with ResolutionError → ask crossroads "resolution" type
    3. Execute strategy steps (tool calls + extraction)
    4. Inject resolved data into row → retry resolution
    5. Up to 3 attempts before giving up

    Args:
        row: Row data dict
        row_num: Row number for logging
        mcp_executor: MCP tool executor
        context: "job" or "quote"
        resolution_plan: LLM resolution plan
        llm_chat: LLM chat function
        tool_descriptions: {tool_name: description} for crossroads context
        tracker: Optional RequestTracker for full context in crossroads

    Returns:
        Resolved identifiers dict

    Raises:
        ResolutionError, MissingFieldError, AmbiguousResolutionError if unrecoverable
    """
    if tool_descriptions is None:
        tool_descriptions = {}

    # Build initial collected data from what's already in the row
    operation = (row.get("Operation") or "CREATE").upper()

    # First attempt: try normal resolution
    try:
        return await _resolve_row_identifiers(
            row, row_num, mcp_executor, context, resolution_plan, llm_chat, hints=hints,
            shared_state=shared_state,
        )
    except (MissingFieldError, AmbiguousResolutionError, BatchedClarificationError):
        raise  # These need user input, not auto-recovery
    except ResolutionError as initial_error:
        if not llm_chat:
            raise  # No LLM → can't use crossroads

        logger.info(f"Row {row_num}: Resolution failed — entering recovery loop: {initial_error}")

        # Build resolution context
        res_ctx = ResolutionContext(
            stuck_point=str(initial_error),
            operation=operation,
            row_data=row,
        )

        # Pre-populate collected data from row
        for field, key in [
            ("staff_name", "StaffName"), ("date", "Date"), ("job_id", "JobID"),
            ("staff_id", "StaffID"), ("cost_centre_id", "CostCentreID"),
            ("section_id", "SectionID"), ("schedule_id", "ScheduleID"),
        ]:
            val = row.get(key, "")
            if val:
                res_ctx.record_collected(field, val, via="row_data")

        # Capture partial data from the failed call (e.g., schedules that were found but couldn't be matched)
        if hasattr(initial_error, "partial_data") and initial_error.partial_data:
            for key, data in initial_error.partial_data.items():
                res_ctx.add_partial_data(key, data)

        last_error = initial_error

        while not res_ctx.exhausted:
            # Ask crossroads for a strategy
            cr_context = res_ctx.to_crossroads_context(tool_descriptions)

            logger.info(
                f"Row {row_num}: Asking crossroads for resolution strategy "
                f"(attempt {res_ctx.attempt_count + 1}/3)"
            )

            cr_result = await resolve_with_context(
                crossroad_type="resolution",
                question=f"Resolution stuck: {last_error}",
                context=cr_context,
                tracker=tracker,
                domain_topics=["simpro_schedules", "simpro_employees", "simpro_jobs", "column_constraints", "schedule_operations_sop"],
                agent_name="schedule",
                tool_catalog=tool_descriptions,
                llm_chat=llm_chat,
            )

            # Crossroads suggests presenting available options to the user
            if cr_result.get("decision") == "suggest_options":
                suggest_field = cr_result.get("suggest_field", "CostCentreName")
                options = cr_result.get("available_options") or res_ctx.partial_data.get("available_cost_centres", [])
                if options:
                    # Enrich options with real staff names from schedule/employee data
                    if suggest_field == "StaffName":
                        # Build staff_id → name map from multiple sources
                        id_to_name = {}
                        # Source 1: employee list (most reliable — has real names)
                        emp_map = res_ctx.partial_data.get("staff_id_name_map", {})
                        if isinstance(emp_map, dict):
                            for sid, sname in emp_map.items():
                                id_to_name[int(sid)] = sname
                        # Source 2: sanitized schedule data (has staff_name if Simpro returned it)
                        for sched in res_ctx.partial_data.get("schedules_found", []):
                            sid = sched.get("staff_id")
                            sname = sched.get("staff_name")
                            if sid and sname and int(sid) not in id_to_name:
                                id_to_name[int(sid)] = sname
                        # Replace placeholder names like "StaffID 2319" with real names
                        for opt in options:
                            opt_id = opt.get("id")
                            opt_name = opt.get("name", "")
                            if opt_id and (not opt_name or opt_name.startswith("StaffID")):
                                real_name = id_to_name.get(int(opt_id))
                                if real_name:
                                    opt["name"] = f"{real_name} (ID: {opt_id})"

                    # Enrich job options with Site.Name from the raw collected data
                    if suggest_field in ("JobName", "JobID"):
                        raw_jobs = res_ctx.collected.get("job_id")
                        if isinstance(raw_jobs, list):
                            # Build job_id → display name map from raw job objects
                            job_id_to_name = {}
                            for job in raw_jobs:
                                if isinstance(job, dict):
                                    jid = job.get("ID")
                                    site = job.get("Site", {})
                                    site_name = site.get("Name", "") if isinstance(site, dict) else ""
                                    desc = (job.get("Description") or "").strip()
                                    display = site_name or desc or ""
                                    if jid and display:
                                        job_id_to_name[int(jid)] = display
                            # Enrich options that have empty names
                            for opt in options:
                                opt_id = opt.get("id")
                                opt_name = (opt.get("name") or "").strip()
                                if opt_id and not opt_name:
                                    real_name = job_id_to_name.get(int(opt_id))
                                    if real_name:
                                        opt["name"] = real_name
                    logger.info(f"Row {row_num}: Crossroads suggests {len(options)} options for {suggest_field}")
                    raise MissingFieldError(
                        field=suggest_field,
                        message=f"Row {row_num}: No match for '{row.get(suggest_field, '')}'. Please select from available options.",
                        options=options,
                        original_value=row.get(suggest_field, ""),
                    )

            if cr_result.get("decision") == "exhausted" or not cr_result.get("strategy"):
                logger.warning(f"Row {row_num}: Crossroads says exhausted — giving up")
                res_ctx.record_failure("crossroads_exhausted", "No viable strategy found")
                break

            strategy = cr_result["strategy"]
            steps = strategy.get("steps", [])
            logger.info(
                f"Row {row_num}: 🧠 Strategy: {strategy.get('description', '?')} "
                f"({len(steps)} steps, confidence={cr_result.get('confidence', 0):.2f})"
            )

            # Execute strategy steps
            strategy_succeeded = False
            strategy_aborted = False
            try:
                for step_idx, step in enumerate(steps):
                    tool_name = step.get("tool")
                    if not tool_name:
                        continue

                    # Check precondition — skip step if required data not yet collected
                    precondition = step.get("precondition")
                    if precondition and not res_ctx.collected.get(precondition):
                        on_fail = step.get("on_fail", "skip_to_next")
                        logger.info(f"Row {row_num}: Step {step_idx + 1}: precondition '{precondition}' not met → {on_fail}")
                        if on_fail == "exhausted":
                            strategy_aborted = True
                            break
                        continue  # skip_to_next

                    # Substitute $collected.X and $stuck.X in params
                    stuck_context = {
                        "staff_name": row.get("StaffName", ""),
                        "date": row.get("Date", ""),
                        "error": str(last_error),
                    }
                    raw_params = step.get("params", {})
                    params = _substitute_params(raw_params, res_ctx.collected, stuck_context)

                    # Remove None params
                    params = {k: v for k, v in params.items() if v is not None}

                    logger.info(f"Row {row_num}: Step {step_idx + 1}: {tool_name}({list(params.keys())})")

                    # Execute tool
                    tool_result = await mcp_executor.call_tool(tool_name, params)

                    # Extract desired value from result
                    extract_path = step.get("extract")
                    save_as = step.get("save_as")
                    if extract_path and save_as:
                        # Resolve match_value references
                        match_value = step.get("match_value")
                        if isinstance(match_value, str):
                            if match_value.startswith("$collected."):
                                match_value = res_ctx.collected.get(match_value[len("$collected."):])
                            elif match_value.startswith("$stuck."):
                                match_value = stuck_context.get(match_value[len("$stuck."):])

                        extracted = _extract_from_result(
                            tool_result,
                            extract_path,
                            match_by=step.get("match_by"),
                            match_value=match_value,
                        )

                        if extracted is not None:
                            # If extracted is a dict (matched item), get the ID
                            if isinstance(extracted, dict):
                                save_val = extracted.get("ID", extracted)
                            else:
                                save_val = extracted
                            res_ctx.record_collected(save_as, save_val, via=f"strategy_step_{step_idx}")
                            logger.info(f"Row {row_num}: Extracted {save_as}={save_val}")
                        else:
                            on_fail = step.get("on_fail", "skip_to_next")
                            logger.warning(f"Row {row_num}: Step {step_idx + 1} extraction returned None → {on_fail}")
                            if on_fail == "exhausted":
                                strategy_aborted = True
                                break

                if strategy_aborted:
                    res_ctx.record_failure(
                        strategy.get("description", "unknown"),
                        "Strategy aborted: step precondition/extraction failed"
                    )
                    continue

                # Inject resolved data back into the row for retry
                field_mapping = {
                    "staff_id": "StaffID",
                    "job_id": "JobID",
                    "section_id": "SectionID",
                    "cost_centre_id": "CostCentreID",
                    "schedule_id": "ScheduleID",
                }
                for collected_key, row_key in field_mapping.items():
                    val = res_ctx.collected.get(collected_key)
                    if val and not row.get(row_key):
                        row[row_key] = str(val) if not isinstance(val, str) else val
                        logger.info(f"Row {row_num}: Injected {row_key}={val} into row")

                # Retry resolution with enriched row
                resolved = await _resolve_row_identifiers(
                    row, row_num, mcp_executor, context, resolution_plan, llm_chat, hints=hints,
                    shared_state=shared_state,
                )
                logger.info(f"Row {row_num}: ✅ Recovery succeeded after {res_ctx.attempt_count + 1} attempt(s)")
                return resolved

            except (MissingFieldError, AmbiguousResolutionError):
                raise  # User input needed
            except ResolutionError as retry_error:
                last_error = retry_error
                res_ctx.record_failure(
                    strategy.get("description", "unknown"),
                    str(retry_error)
                )
                # Capture any new partial data from the retry
                if hasattr(retry_error, "partial_data") and retry_error.partial_data:
                    for pd_key, pd_data in retry_error.partial_data.items():
                        res_ctx.add_partial_data(pd_key, pd_data)
                logger.info(f"Row {row_num}: Strategy failed — will retry: {retry_error}")
            except Exception as e:
                res_ctx.record_failure(
                    strategy.get("description", "unknown"),
                    f"Unexpected error: {e}"
                )
                logger.warning(f"Row {row_num}: Strategy execution error: {e}")
                last_error = ResolutionError(str(e))

        # All attempts exhausted
        raise ResolutionError(
            f"Row {row_num}: Resolution failed after {res_ctx.attempt_count} recovery attempt(s). "
            f"Last error: {last_error}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Helpers for Chat-Based Schedule Parsing
# ═══════════════════════════════════════════════════════════════════════════

CHAT_HEADERS = [
    "Operation", "ScheduleID", "JobID", "JobName", "SiteName", "QuoteID",
    "SectionID", "SectionName", "CostCentreID", "CostCentreName",
    "StaffID", "StaffName", "Date", "Blocks", "StartTime", "Notes",
    "IsLocked", "BlocksAdjust", "EndTime"
]

# Canonical set for fast lookup
_CANONICAL_HEADER_SET = set(CHAT_HEADERS)

# Common synonyms that can be resolved without LLM
_HEADER_SYNONYMS: Dict[str, str] = {
    # StaffName
    "staff": "StaffName", "staffname": "StaffName", "employee": "StaffName",
    "worker": "StaffName", "technician": "StaffName", "tech": "StaffName",
    "person": "StaffName", "name": "StaffName", "staff name": "StaffName",
    "employee name": "StaffName", "assigned to": "StaffName",
    # StaffID
    "staffid": "StaffID", "staff id": "StaffID", "employee id": "StaffID",
    "employeeid": "StaffID",
    # JobID
    "job": "JobID", "jobid": "JobID", "job id": "JobID",
    "job number": "JobID", "jobnumber": "JobID", "job no": "JobID",
    "job #": "JobID", "job#": "JobID",
    # JobName
    "jobname": "JobName", "job name": "JobName", "project": "JobName",
    "project name": "JobName",
    # SiteName
    "sitename": "SiteName", "site name": "SiteName", "site": "SiteName",
    "address": "SiteName", "location": "SiteName",
    # QuoteID
    "quoteid": "QuoteID", "quote id": "QuoteID", "quote": "QuoteID",
    "quote number": "QuoteID", "quote #": "QuoteID",
    # SectionID / SectionName
    "sectionid": "SectionID", "section id": "SectionID",
    "sectionname": "SectionName", "section name": "SectionName",
    "section": "SectionName",
    # CostCentreID / CostCentreName
    "costcentreid": "CostCentreID", "cost centre id": "CostCentreID",
    "cost center id": "CostCentreID", "costcenterid": "CostCentreID",
    "costcentrename": "CostCentreName", "cost centre name": "CostCentreName",
    "cost center name": "CostCentreName", "costcentername": "CostCentreName",
    "cost centre": "CostCentreName", "cost center": "CostCentreName",
    "cc": "CostCentreName", "costcentre": "CostCentreName",
    "costcenter": "CostCentreName",
    # Date
    "date": "Date", "schedule date": "Date", "scheduledate": "Date",
    "day": "Date", "when": "Date",
    # Blocks
    "blocks": "Blocks", "hours": "Blocks", "hrs": "Blocks",
    "duration": "Blocks", "time": "Blocks", "block": "Blocks",
    # StartTime
    "starttime": "StartTime", "start time": "StartTime", "start": "StartTime",
    "begin": "StartTime", "from": "StartTime", "start_time": "StartTime",
    # Notes
    "notes": "Notes", "note": "Notes", "comment": "Notes",
    "comments": "Notes", "description": "Notes", "desc": "Notes",
    # Operation
    "operation": "Operation", "action": "Operation", "op": "Operation",
    "type": "Operation", "task": "Operation",
    # ScheduleID
    "scheduleid": "ScheduleID", "schedule id": "ScheduleID",
    "schedule_id": "ScheduleID", "sched id": "ScheduleID",
    # IsLocked
    "islocked": "IsLocked", "is locked": "IsLocked", "locked": "IsLocked",
    "is_locked": "IsLocked", "lock": "IsLocked",
    # BlocksAdjust
    "blocksadjust": "BlocksAdjust", "blocks adjust": "BlocksAdjust",
    "blocks_adjust": "BlocksAdjust", "adjust": "BlocksAdjust",
    "adjustment": "BlocksAdjust",
    # EndTime
    "endtime": "EndTime", "end_time": "EndTime", "end time": "EndTime",
    "finish time": "EndTime", "finishtime": "EndTime",
}


def _normalize_headers_to_canonical(
    user_headers: List[str],
    llm_chat: Callable,
) -> Dict[str, Optional[str]]:
    """
    Map user-provided Excel headers to canonical CHAT_HEADERS names.

    Returns {user_header: canonical_header_or_None} for each input header.
    None means the column should be dropped (no canonical match).

    Uses 3 tiers:
    1. Exact match (already canonical) → identity map
    2. Case-insensitive + synonym table → direct map
    3. LLM fallback (only for unresolved headers) → fuzzy match
    """
    mapping: Dict[str, Optional[str]] = {}
    unresolved: List[str] = []

    for h in user_headers:
        # Tier 1: exact match
        if h in _CANONICAL_HEADER_SET:
            mapping[h] = h
            continue

        # Tier 2: case-insensitive + synonym lookup
        h_lower = h.lower().strip()
        if h_lower in _HEADER_SYNONYMS:
            mapping[h] = _HEADER_SYNONYMS[h_lower]
            continue

        # Also try case-insensitive exact match (e.g., "date" → "Date")
        for canonical in CHAT_HEADERS:
            if h_lower == canonical.lower():
                mapping[h] = canonical
                break
        else:
            unresolved.append(h)

    if not unresolved:
        logger.info(f"📋 All headers mapped without LLM: {mapping}")
        return mapping

    # Tier 3: LLM for remaining unresolved headers
    logger.info(f"🧠 LLM header matching for unresolved: {unresolved}")
    try:
        prompt_msg = [
            {
                "role": "system",
                "content": (
                    "You are a column header mapper for a schedule management system.\n"
                    f"Canonical headers: {CHAT_HEADERS}\n\n"
                    "Map each user header to the BEST matching canonical header.\n"
                    "If no reasonable match exists, map to null.\n\n"
                    "Common mappings:\n"
                    "- Staff/Employee/Worker/Technician → StaffName\n"
                    "- Job/Project/Job Number → JobID (if numeric) or JobName (if text)\n"
                    "- Hours/Hrs/Duration → Blocks\n"
                    "- Start/Begin/From → StartTime\n"
                    "- Action/Op/Type → Operation\n"
                    "- Cost Centre/CC → CostCentreName\n"
                    "- Locked/Is Locked → IsLocked\n"
                    "- Section → SectionName\n\n"
                    "Respond ONLY with valid JSON: {\"user_header\": \"CanonicalHeader\" or null}"
                ),
            },
            {
                "role": "user",
                "content": f"Map these headers: {unresolved}",
            },
        ]

        import json
        raw = llm_chat(prompt_msg, temperature=0.0, sanitize=False)
        # Parse LLM response
        text = raw.strip()
        # Remove markdown code fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        llm_mapping = json.loads(text)

        for h in unresolved:
            canonical = llm_mapping.get(h)
            if canonical and canonical in _CANONICAL_HEADER_SET:
                mapping[h] = canonical
            else:
                mapping[h] = None
                logger.warning(f"Header '{h}' has no canonical match (LLM returned: {canonical})")

    except Exception as e:
        logger.error(f"LLM header mapping failed: {e}")
        # Fallback: drop all unresolved headers
        for h in unresolved:
            mapping[h] = None

    logger.info(f"📋 Final header mapping: {mapping}")
    return mapping


def _apply_header_mapping(
    table: Dict[str, Any],
    mapping: Dict[str, Optional[str]],
) -> None:
    """
    Rename headers in-place per mapping and rebuild rows to match.

    Columns mapped to None are dropped.
    If two user headers map to the same canonical header, the first is kept.
    """
    old_headers = table.get("headers", [])
    old_rows = table.get("rows", [])

    # Build new header order (preserving order, dropping None, deduplicating)
    new_headers: List[str] = []
    keep_indices: List[int] = []  # indices into old_headers/rows to keep
    seen_canonical: set = set()

    for i, h in enumerate(old_headers):
        canonical = mapping.get(h)
        if canonical is None:
            continue
        if canonical in seen_canonical:
            continue  # duplicate canonical — skip
        seen_canonical.add(canonical)
        new_headers.append(canonical)
        keep_indices.append(i)

    # Rebuild rows with only the kept columns
    new_rows = []
    for row in old_rows:
        new_row = [row[i] if i < len(row) else "" for i in keep_indices]
        new_rows.append(new_row)

    table["headers"] = new_headers
    table["rows"] = new_rows
    logger.info(f"📋 Applied header mapping: {old_headers} → {new_headers}")


def _llm_understand_file_schema(
    headers: List[str],
    rows: List[List],
    user_text: str,
    llm_chat: Callable,
) -> Dict[str, Any]:
    """
    Use LLM to understand an arbitrary file schema and map columns to canonical schedule fields.

    Sends headers + sample cell values + user intent to the LLM and asks the INVERSE question:
    "which column in this file IS the StaffName / Date / JobID?" rather than mapping each
    column forward. This works for any file format with no hardcoded patterns.

    Returns:
        {
            "field_map": {"CanonicalField": "ExactSourceColumn", ...},
            "inferred_operation": "CREATE" | "UPDATE" | "DELETE" | None,
            "confidence": 0.87,
            "notes": "brief explanation of key decisions"
        }
    """
    sample_rows = [dict(zip(headers, row)) for row in rows[:5]]

    prompt = [
        {
            "role": "system",
            "content": (
                "You are a file schema analyst for a construction scheduling system.\n"
                "Given column headers and sample rows from any user-uploaded file, "
                "identify which column corresponds to each canonical schedule field.\n\n"
                "CANONICAL FIELDS (what the system needs):\n"
                "- Operation: CRUD action. Values: CREATE, UPDATE, DELETE.\n"
                "- StaffName: Employee/technician full name (string).\n"
                "- StaffID: Numeric employee identifier.\n"
                "- Date: Schedule date (any date format).\n"
                "- JobID: Numeric Simpro job number.\n"
                "- JobName: Text job name or description.\n"
                "- SiteName: Site location or address.\n"
                "- QuoteID: Numeric Simpro quote number.\n"
                "- SectionName: Job section name (sub-division of a job).\n"
                "- SectionID: Numeric section identifier.\n"
                "- CostCentreName: Cost centre or trade name.\n"
                "- CostCentreID: Numeric cost centre identifier.\n"
                "- Blocks: Duration in hours (float). e.g. 8.0 = full day.\n"
                "- StartTime: Shift start time (any time format).\n"
                "- EndTime: Shift end time (any time format).\n"
                "- Notes: Free text comments or instructions.\n"
                "- IsLocked: Boolean lock flag (true/false/yes/no/1/0).\n"
                "- ScheduleID: Numeric ID of an existing schedule record.\n"
                "- BlocksAdjust: Duration adjustment override (float).\n\n"
                "RULES:\n"
                "1. Map ONLY fields you are confident about. Omit everything else.\n"
                "2. Use sample cell VALUES as evidence — '2024-03-15' confirms Date, "
                "'08:00' confirms StartTime, a number like '4521' under a 'Job' column confirms JobID.\n"
                "3. If two columns could be the same field, pick the most specific one.\n"
                "4. If no Blocks column exists but StartTime + EndTime do, omit Blocks from "
                "field_map — the system will derive it automatically. Mention this in notes.\n"
                "5. For Operation: if no column contains CREATE/UPDATE/DELETE values, "
                "infer intent from the user's message "
                "('create/add/schedule' → CREATE, 'delete/remove/cancel' → DELETE, "
                "'update/change/move/reassign' → UPDATE). If truly ambiguous set null.\n"
                "6. field_map values must be the EXACT column header string as it appears in the file.\n"
                "7. confidence: 0.0-1.0 overall certainty across all mappings.\n\n"
                "Respond with ONLY valid JSON (no markdown, no explanation outside JSON):\n"
                '{"field_map": {"CanonicalField": "ExactSourceColumn"}, '
                '"inferred_operation": "CREATE"|"UPDATE"|"DELETE"|null, '
                '"confidence": 0.85, "notes": "brief"}'
            ),
        },
        {
            "role": "user",
            "content": (
                f"File headers: {headers}\n\n"
                "Sample rows:\n"
                + "\n".join(f"Row {i+1}: {row}" for i, row in enumerate(sample_rows))
                + f"\n\nUser's message: {user_text[:300]}"
            ),
        },
    ]

    try:
        import json as _json
        raw = llm_chat(prompt, temperature=0.0, sanitize=False)
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        result = _json.loads(text)

        # Validate: reject hallucinated column names + non-canonical field names
        raw_field_map = result.get("field_map", {})
        headers_set = set(headers)
        validated = {
            canon: src
            for canon, src in raw_field_map.items()
            if canon in _CANONICAL_HEADER_SET and src in headers_set
        }
        if len(validated) < len(raw_field_map):
            dropped = set(raw_field_map) - set(validated)
            logger.warning(f"Schema understanding: dropped invalid mappings: {dropped}")

        return {
            "field_map": validated,
            "inferred_operation": result.get("inferred_operation"),
            "confidence": float(result.get("confidence", 0.0)),
            "notes": result.get("notes", ""),
        }

    except Exception as e:
        logger.error(f"LLM schema understanding failed: {e}")
        return {"field_map": {}, "inferred_operation": None, "confidence": 0.0, "notes": str(e)}


def _parse_time_to_minutes(t_str: str) -> Optional[float]:
    """Parse time string (HH:MM 24h or H:MM AM/PM) to minutes since midnight. Returns None on failure."""
    if not t_str:
        return None
    import re as _re
    t = str(t_str).strip()
    m = _re.match(r'^(\d{1,2}):(\d{2})$', t)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = _re.match(r'^(\d{1,2}):(\d{2})\s*(AM|PM)$', t, _re.IGNORECASE)
    if m:
        h, mn, period = int(m.group(1)), int(m.group(2)), m.group(3).upper()
        if period == "PM" and h != 12:
            h += 12
        elif period == "AM" and h == 12:
            h = 0
        return h * 60 + mn
    return None


def _build_row_data_from_parsed(parsed_schedule: Dict[str, Any], normalize_date_fn: Callable) -> tuple:
    """
    Build a row_data list + metadata from a single parsed schedule JSON object.
    Returns (row_data, new_date, new_staff_name).
    """
    operation = parsed_schedule.get("operation", "CREATE").upper()
    schedule_data = parsed_schedule.get("schedule_data", {})
    find_criteria = parsed_schedule.get("find_criteria", {})

    new_date = None
    new_staff_name = None

    if operation == "CREATE":
        row_data = [
            "CREATE",
            "",  # ScheduleID
            str(schedule_data.get("job_id")) if schedule_data.get("job_id") else "",
            schedule_data.get("job_name") or "",
            schedule_data.get("site_name") or "",
            str(schedule_data.get("quote_id")) if schedule_data.get("quote_id") else "",
            str(schedule_data.get("section_id")) if schedule_data.get("section_id") else "",
            schedule_data.get("section_name") or "",
            str(schedule_data.get("cost_centre_id")) if schedule_data.get("cost_centre_id") else "",
            schedule_data.get("cost_centre_name") or "",
            str(schedule_data.get("staff_id")) if schedule_data.get("staff_id") else "",
            schedule_data.get("staff_name") or "",
            normalize_date_fn(schedule_data.get("date", "today")),
            str(schedule_data.get("blocks") or ""),
            schedule_data.get("start_time") or "",
            schedule_data.get("notes") or "",
            str(schedule_data.get("is_locked", "")).lower() if schedule_data.get("is_locked") is not None else "",
            "",  # BlocksAdjust — not applicable for CREATE
            schedule_data.get("end_time") or "",  # EndTime
        ]

    elif operation in ("UPDATE", "DELETE"):
        find_date = normalize_date_fn(find_criteria.get("date", "today"))
        find_staff = find_criteria.get("staff_name", "")
        find_job_id = find_criteria.get("job_id")
        find_cost_centre_id = find_criteria.get("cost_centre_id")
        # Extract IDs from find_criteria (populated from conversation history)
        find_schedule_id = find_criteria.get("schedule_id")
        find_staff_id = find_criteria.get("staff_id")
        find_section_id = find_criteria.get("section_id")

        new_date = normalize_date_fn(schedule_data.get("date")) if schedule_data.get("date") else None
        new_time = schedule_data.get("start_time")
        new_blocks = schedule_data.get("blocks")
        new_notes = schedule_data.get("notes")
        new_staff_id = schedule_data.get("staff_id")
        new_staff_name = schedule_data.get("staff_name")
        new_is_locked = schedule_data.get("is_locked")

        blocks_adjust = schedule_data.get("blocks_adjust")
        new_end_time = schedule_data.get("end_time")

        # For StaffID column: prefer find_criteria.staff_id (existing staff) over
        # schedule_data.staff_id (new staff for reassignment) — the resolver uses
        # the row StaffID to locate the schedule, and new_staff is handled separately.
        staff_id_for_row = find_staff_id or new_staff_id

        row_data = [
            operation,
            str(find_schedule_id) if find_schedule_id else "",  # ScheduleID from history
            str(find_job_id) if find_job_id else "",
            "",  # JobName
            "",  # SiteName
            "",  # QuoteID
            str(find_section_id) if find_section_id else "",  # SectionID from history
            "",  # SectionName
            str(find_cost_centre_id) if find_cost_centre_id else "",
            "",  # CostCentreName
            str(staff_id_for_row) if staff_id_for_row else "",
            find_staff,
            find_date,
            str(new_blocks) if new_blocks else "",
            new_time or "",
            new_notes or "",
            str(new_is_locked).lower() if new_is_locked is not None else "",
            str(blocks_adjust) if blocks_adjust is not None else "",
            new_end_time or "",  # EndTime
        ]
    else:
        raise ValueError(f"Invalid operation: {operation}")

    return row_data, new_date, new_staff_name


def _resolve_date_range(range_str: str) -> List[str]:
    """Convert 'this week', 'next week', date ranges to list of YYYY-MM-DD dates."""
    today = datetime.now()
    range_lower = range_str.lower().strip()

    if range_lower in ("today", ""):
        return [today.strftime("%Y-%m-%d")]
    elif range_lower == "tomorrow":
        return [(today + timedelta(days=1)).strftime("%Y-%m-%d")]
    elif range_lower == "this week":
        monday = today - timedelta(days=today.weekday())
        return [(monday + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(5)]
    elif range_lower == "next week":
        monday = today - timedelta(days=today.weekday()) + timedelta(weeks=1)
        return [(monday + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(5)]
    else:
        # Try to parse as a single date
        try:
            parsed = _normalize_date(range_str)
            return [parsed]
        except Exception:
            # Default to today if we can't parse
            logger.warning(f"Could not parse date range '{range_str}', defaulting to today")
            return [today.strftime("%Y-%m-%d")]


async def _expand_bulk_action(
    parsed: Dict[str, Any],
    mcp_executor: Any,
    headers: List[str],
) -> Dict[str, Any]:
    """
    Expand a bulk_action (lock all, delete all) into individual schedule rows
    by querying get_schedules for the date range and filtering by staff.
    """
    action = parsed["bulk_action"].upper()
    find = parsed.get("find_criteria", {})
    sched_data = parsed.get("schedule_data", {})
    staff_name = find.get("staff_name", "").strip() if find.get("staff_name") else ""
    staff_type_filter = (find.get("staff_type") or "").strip().lower()  # "contractor" or "employee"
    date_range = find.get("date_range", "today")

    # Safety guard: need either a staff name or a staff type
    if not staff_name and not staff_type_filter:
        return {
            "success": False,
            "error": "MISSING_STAFF",
            "message": "Please specify which staff member's schedules to modify (e.g., 'lock all of Stephen's schedules this week')."
        }

    dates = _resolve_date_range(date_range)
    filter_desc = staff_name or f"all {staff_type_filter}s"
    logger.info(f"🔄 Bulk action: {action} for '{filter_desc}' across {len(dates)} dates: {dates}")

    # Build a set of contractor IDs when filtering by staff_type="contractor"
    # so we can match schedule Staff.ID against contractors vs employees.
    contractor_ids: set = set()
    employee_ids: set = set()
    if staff_type_filter:
        try:
            if staff_type_filter == "contractor":
                ctr_result = await mcp_executor.call_tool("list_contractors", {"page_size": 250, "columns": "ID,Name"})
                for c in (ctr_result.get("contractors") or ctr_result.get("data") or []):
                    cid = c.get("ID")
                    if cid:
                        contractor_ids.add(cid)
                logger.info(f"🔄 Loaded {len(contractor_ids)} contractor IDs for type filter")
            elif staff_type_filter == "employee":
                emp_result = await mcp_executor.call_tool("list_employees", {"page_size": 250, "columns": "ID,Name"})
                for e in (emp_result.get("employees") or emp_result.get("data") or []):
                    eid = e.get("ID")
                    if eid:
                        employee_ids.add(eid)
                logger.info(f"🔄 Loaded {len(employee_ids)} employee IDs for type filter")
        except Exception as e:
            logger.warning(f"Staff type lookup failed: {e}")

    # Resolve staff name to StaffID first — Simpro schedule responses may not
    # include staff names (GivenName/FamilyName), only Staff.ID.
    resolved_staff_id = find.get("staff_id")
    resolved_staff_name = staff_name
    if staff_name and not resolved_staff_id:
        try:
            resolver = EntityResolver(mcp_executor)
            result = await resolver.resolve_staff(name=staff_name)
            resolved_staff_id = result["id"]
            resolved_staff_name = result["name"]
            logger.info(f"🔄 Resolved staff '{staff_name}' → ID={resolved_staff_id} ({resolved_staff_name})")
        except AmbiguousResolutionError:
            # For find/list operations, ambiguity is less critical — fall back to name matching.
            # The user is just browsing schedules, not modifying them.
            logger.warning(f"Staff lookup ambiguous for '{staff_name}', falling back to name matching")
        except ResolutionError:
            logger.warning(f"Staff lookup failed for '{staff_name}', falling back to name matching")

    all_rows = []
    for date in dates:
        try:
            result = await mcp_executor.call_tool("get_schedules", {"date": date, "page_size": 250})
            # call_tool already unwraps the "data" envelope, so schedules is at top level
            schedules = result.get("schedules", [])
        except Exception as e:
            logger.warning(f"Failed to get schedules for {date}: {e}")
            continue

        # Pre-compute fuzzy-matched staff IDs when falling back to name matching
        fuzzy_staff_ids = None
        if staff_name and not resolved_staff_id and not staff_type_filter:
            # Deduplicate candidates by Staff ID to avoid noise
            seen_staff_ids = {}
            for s in schedules:
                si = s.get("Staff", {})
                sid = si.get("ID")
                if sid and sid not in seen_staff_ids:
                    sname = (si.get("Name") or "").strip()
                    if not sname:
                        given = (si.get("GivenName") or "").strip()
                        family = (si.get("FamilyName") or "").strip()
                        sname = f"{given} {family}".strip()
                    if sname:
                        seen_staff_ids[sid] = sname
            staff_candidates = [{"ID": k, "Name": v} for k, v in seen_staff_ids.items()]
            fm = fuzzy_match_entities(staff_name, staff_candidates, source="get_schedules")
            # Only keep matches close to the best score — prevents unrelated
            # entities that share a single word (e.g., "plumbing") from leaking in.
            if fm:
                best_score = fm[0]["score"]
                fuzzy_staff_ids = {m["id"] for m in fm if m["score"] >= best_score - 10}
                logger.info(f"  Fuzzy staff filter: {[(m['id'], m['name'], m['score']) for m in fm if m['score'] >= best_score - 10]}")
            else:
                logger.warning(f"  No fuzzy staff matches for '{staff_name}' among {len(staff_candidates)} unique staff")
                fuzzy_staff_ids = set()

        for sched in schedules:
            staff_info = sched.get("Staff", {})
            sched_staff_id = staff_info.get("ID")

            # Filter by staff_type if specified (match Staff.ID against known IDs)
            if staff_type_filter:
                if staff_type_filter == "contractor" and contractor_ids:
                    if sched_staff_id not in contractor_ids:
                        continue
                elif staff_type_filter == "employee" and employee_ids:
                    if sched_staff_id not in employee_ids:
                        continue
            # Filter by specific staff name/ID
            elif resolved_staff_id:
                if sched_staff_id != resolved_staff_id:
                    continue
            elif fuzzy_staff_ids is not None:
                if sched_staff_id not in fuzzy_staff_ids:
                    continue

            # Only job schedules supported
            stype = (sched.get("Type") or "").lower()
            ref = sched.get("Reference", "")
            if stype != "job" or "-" not in ref:
                continue

            ref_parts = ref.split("-")
            job_id = ref_parts[0]
            cc_id = ref_parts[1] if len(ref_parts) > 1 else ""

            blocks_arr = sched.get("Blocks", [])
            existing_time = blocks_arr[0].get("StartTime", "") if blocks_arr else ""
            existing_hrs = str(blocks_arr[0].get("Hrs", "")) if blocks_arr else ""

            # Read IsLocked and Notes from the Simpro schedule object
            is_locked_val = ""
            sched_is_locked = sched.get("IsLocked")
            if sched_is_locked is not None:
                is_locked_val = str(sched_is_locked).lower()

            sched_notes = sched.get("Notes") or ""

            # Build staff name from Simpro response — always prefer API data
            # over user input to show accurate names in confirmations
            api_staff_name = (staff_info.get("Name") or "").strip()
            if not api_staff_name:
                given = (staff_info.get("GivenName") or "").strip()
                family = (staff_info.get("FamilyName") or "").strip()
                api_staff_name = f"{given} {family}".strip()
            display_name = api_staff_name or resolved_staff_name

            row = [
                action,                          # Operation
                str(sched.get("ID", "")),         # ScheduleID
                job_id,                           # JobID
                "",                               # JobName
                "",                               # SiteName
                "",                               # QuoteID
                "",                               # SectionID — will be resolved
                "",                               # SectionName
                cc_id,                            # CostCentreID
                "",                               # CostCentreName
                str(sched_staff_id or ""),         # StaffID
                display_name,                     # StaffName
                date,                             # Date
                existing_hrs,                     # Blocks
                existing_time,                    # StartTime
                sched_notes,                      # Notes
                is_locked_val,                    # IsLocked
                "",                               # BlocksAdjust — not used in bulk
                "",                               # EndTime — not used in bulk
            ]
            all_rows.append(row)
            logger.info(f"  Found: Schedule {sched.get('ID')} — {display_name} on {date} ({ref})")

    if not all_rows:
        return {
            "success": False,
            "error": "NO_SCHEDULES_FOUND",
            "message": f"No schedules found for '{filter_desc}' in the specified date range ({date_range})."
        }

    logger.info(f"🔄 Bulk action expanded to {len(all_rows)} schedules")

    # ── Carry forward ALL schedule_data changes for UPDATE bulk actions ──
    # The rows were built from Simpro API values (existing data). Any new values
    # the user wants to apply (start_time, blocks, notes, is_locked, staff, date)
    # must be written into the rows or metadata here — otherwise _process_single_row
    # sees the old Simpro values and sends them unchanged.
    bulk_metadata: Dict[str, Any] = {}

    if action == "UPDATE":
        # staff_name and date go via metadata / row_metadata (handled in _process_single_row
        # as reassignment / date-move, separate from field assembly).
        new_staff = sched_data.get("staff_name") or None
        new_date_target = sched_data.get("date") or None
        if new_staff:
            bulk_metadata["new_staff_name"] = new_staff
            logger.info(f"🔄 Bulk UPDATE: will reassign all rows to '{new_staff}'")
        if new_date_target:
            bulk_metadata["new_date"] = new_date_target
            logger.info(f"🔄 Bulk UPDATE: will move all rows to date '{new_date_target}'")

        # StartTime, Blocks, Notes, IsLocked, BlocksAdjust, EndTime go directly
        # into the row array (field assembler reads them from row_data dict).
        # Overwrite the existing-value placeholders that were copied from Simpro.
        # CHAT_HEADERS indices: [13]=Blocks [14]=StartTime [15]=Notes [16]=IsLocked
        #                       [17]=BlocksAdjust [18]=EndTime
        new_start_time  = sched_data.get("start_time")
        new_blocks      = sched_data.get("blocks")
        new_notes       = sched_data.get("notes")
        new_is_locked   = sched_data.get("is_locked")
        new_blocks_adj  = sched_data.get("blocks_adjust")
        new_end_time    = sched_data.get("end_time")

        for row in all_rows:
            if new_blocks      is not None: row[13] = str(new_blocks)
            if new_start_time  is not None: row[14] = new_start_time
            if new_notes       is not None: row[15] = new_notes
            if new_is_locked   is not None: row[16] = str(new_is_locked).lower()
            if new_blocks_adj  is not None: row[17] = str(new_blocks_adj)
            if new_end_time    is not None: row[18] = new_end_time

        changed = [f for f, v in [
            ("start_time", new_start_time), ("blocks", new_blocks),
            ("notes", new_notes), ("is_locked", new_is_locked),
            ("blocks_adjust", new_blocks_adj), ("end_time", new_end_time),
        ] if v is not None]
        if changed:
            logger.info(f"🔄 Bulk UPDATE: applied field changes to all rows: {changed}")

    result: Dict[str, Any] = {
        "detected_type": "schedule_data_from_chat",
        "is_useful": True,
        "tables": [{"headers": headers, "rows": all_rows}],
        "metadata": bulk_metadata,
    }
    # Per-row metadata carries staff/date reassignment targets so the injection
    # guard in run_schedule_agent works correctly (won't overwrite a resolved ID).
    if bulk_metadata:
        result["row_metadata"] = [
            {"new_staff_name": bulk_metadata.get("new_staff_name"),
             "new_date": bulk_metadata.get("new_date")}
            for _ in all_rows
        ]
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Natural Language Parser for Chat-Based Schedule Creation
# ═══════════════════════════════════════════════════════════════════════════

def _normalize_parsed_schema(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize old flat LLM schema to new nested schema."""
    if "find_criteria" in parsed or "schedule_data" in parsed:
        return parsed  # Already new schema

    logger.info("⚠️ LLM returned old flat schema, converting to nested format")
    operation = parsed.get("operation", "CREATE").upper()

    if operation in ("UPDATE", "DELETE", "COPY"):
        find_criteria = {
            "staff_name": parsed.get("staff_name"),
            "date": parsed.get("date"),
            "job_id": parsed.get("job_id"),
            "cost_centre_id": parsed.get("cost_centre_id"),
        }
        schedule_data = {}
        if operation in ("UPDATE", "COPY"):
            schedule_data = {
                "start_time": parsed.get("start_time"),
                "blocks": parsed.get("blocks"),
                "notes": parsed.get("notes"),
                "is_locked": parsed.get("is_locked"),
                "staff_name": parsed.get("new_staff_name"),
            }
            if operation == "COPY":
                # For COPY, destination date goes in schedule_data
                schedule_data["date"] = parsed.get("dest_date") or parsed.get("new_date")
        return {"operation": operation, "find_criteria": find_criteria, "schedule_data": schedule_data}
    else:
        schedule_data = {k: v for k, v in parsed.items() if k != "operation"}
        return {"operation": operation, "schedule_data": schedule_data, "find_criteria": {}}


async def _convert_copy_to_create(
    parsed: Dict[str, Any],
    mcp_executor: Any,
    normalize_date_fn: Callable,
) -> Dict[str, Any]:
    """
    Convert a COPY operation to CREATE by looking up the source schedule.

    Fetches the source schedule via get_schedules, extracts its details
    (job, section, cost centre, blocks, start time), and rewrites as a
    fully-populated CREATE with the destination date.

    Args:
        parsed: LLM-parsed dict with operation="COPY", find_criteria, schedule_data
        mcp_executor: MCP tool executor for API calls
        normalize_date_fn: Date normalization function

    Returns:
        Rewritten parsed dict with operation="CREATE" and all fields populated
    """
    find = parsed.get("find_criteria", {})
    sched_data = parsed.get("schedule_data", {})

    source_staff = find.get("staff_name", "").strip()
    source_date_raw = find.get("date", "")
    dest_date_raw = sched_data.get("date", "")

    if not source_staff:
        raise ValueError("COPY requires a staff name to identify the source schedule.")
    if not source_date_raw:
        raise ValueError("COPY requires a source date to identify which schedule to copy.")
    if not dest_date_raw:
        raise ValueError("COPY requires a destination date for the new schedule.")

    source_date = normalize_date_fn(source_date_raw)
    dest_date = normalize_date_fn(dest_date_raw)

    logger.info(f"📋 COPY: Looking up '{source_staff}' schedule on {source_date} → copy to {dest_date}")

    # Resolve staff name → StaffID (use history ID if available)
    resolved_staff_id = find.get("staff_id")
    if resolved_staff_id:
        logger.info(f"📋 COPY: Using staff_id={resolved_staff_id} from conversation history")
    else:
        try:
            resolver = EntityResolver(mcp_executor)
            result = await resolver.resolve_staff(name=source_staff)
            resolved_staff_id = result["id"]
            logger.info(f"📋 COPY: Resolved staff '{source_staff}' → ID={resolved_staff_id} ({result['name']})")
        except AmbiguousResolutionError:
            # Let it propagate — _process_single_row will convert to clarification form
            raise
        except ResolutionError as e:
            logger.warning(f"📋 COPY: Staff lookup failed: {e}")

    if not resolved_staff_id:
        raise ValueError(f"Could not find staff member '{source_staff}'. Please check the name and try again.")

    # Fetch schedules for the source date
    try:
        result = await mcp_executor.call_tool("get_schedules", {"date": source_date})
    except Exception as e:
        raise ValueError(f"Could not fetch schedules for {source_date}: {e}")

    schedules = result.get("schedules", [])
    if not schedules:
        raise ValueError(f"No schedules found on {source_date}. Cannot copy.")

    # Match by StaffID
    matching = [s for s in schedules if s.get("Staff", {}).get("ID") == resolved_staff_id]

    # Filter by job_id if provided
    find_job_id = find.get("job_id")
    if find_job_id and len(matching) > 1:
        job_filtered = [
            s for s in matching
            if (s.get("Type") or "").lower() == "job"
            and str(s.get("Reference", "")).split("-")[0] == str(find_job_id)
        ]
        if job_filtered:
            matching = job_filtered

    if not matching:
        raise ValueError(
            f"No schedule found for '{source_staff}' (StaffID={resolved_staff_id}) on {source_date}. "
            f"Found {len(schedules)} schedule(s) for other staff on that date."
        )

    if len(matching) > 1:
        logger.warning(f"📋 COPY: {len(matching)} schedules found for '{source_staff}' on {source_date}, using first")

    source = matching[0]

    # Extract details from source schedule
    ref = str(source.get("Reference", ""))
    source_type = (source.get("Type") or "job").lower()
    blocks_list = source.get("Blocks", [])

    # Parse Reference → JobID-CostCentreID (or QuoteID-CostCentreID)
    job_id = None
    quote_id = None
    cost_centre_id = None
    if "-" in ref:
        parts = ref.split("-")
        if source_type == "job":
            job_id = int(parts[0])
            cost_centre_id = int(parts[1]) if len(parts) > 1 else None
        elif source_type == "quote":
            quote_id = int(parts[0])
            cost_centre_id = int(parts[1]) if len(parts) > 1 else None

    # Extract time/blocks from Blocks array
    source_start_time = None
    source_hours = None
    if blocks_list:
        block = blocks_list[0]
        source_start_time = block.get("StartTime")
        source_hours = block.get("Hrs")
        if source_hours is not None:
            try:
                source_hours = float(source_hours)
            except (ValueError, TypeError):
                source_hours = None

    # Build CREATE schedule_data, using overrides from schedule_data if provided
    dest_staff = sched_data.get("staff_name") or source_staff
    create_data = {
        "operation": "CREATE",
        "schedule_data": {
            "staff_name": dest_staff,
            "staff_id": resolved_staff_id if dest_staff == source_staff else None,
            "date": dest_date,
            "start_time": sched_data.get("start_time") or source_start_time,
            "blocks": sched_data.get("blocks") if sched_data.get("blocks") is not None else source_hours,
        },
        "find_criteria": {},
    }

    # Add job/quote context
    if job_id:
        create_data["schedule_data"]["job_id"] = job_id
    if quote_id:
        create_data["schedule_data"]["quote_id"] = quote_id
    if cost_centre_id:
        create_data["schedule_data"]["cost_centre_id"] = cost_centre_id

    logger.info(
        f"📋 COPY → CREATE: staff={dest_staff}, date={dest_date}, "
        f"job_id={job_id}, cost_centre_id={cost_centre_id}, "
        f"blocks={create_data['schedule_data'].get('blocks')}, "
        f"start_time={create_data['schedule_data'].get('start_time')}"
    )

    return create_data


def _notes_to_html(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert plain-text notes (with \\n newlines) to Simpro-compatible HTML
    (using <br> tags) in the parsed schedule data.
    """
    import html as _html

    def _convert(d: Dict[str, Any]) -> None:
        for key, val in d.items():
            if key == "notes" and isinstance(val, str) and val:
                # Convert plain text newlines to <br> for Simpro rich-text field
                safe = _html.escape(val)
                d[key] = safe.replace("\n", "<br>\n")
                logger.info(f"📝 Converted notes to HTML ({len(val)} chars)")
            elif isinstance(val, dict):
                _convert(val)
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, dict):
                        _convert(item)

    _convert(parsed)
    return parsed


async def _parse_chat_schedule_request(
    user_text: str,
    llm_chat: Callable,
    hints: Optional[Dict[str, Any]] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    mcp_executor: Any = None,
) -> Dict[str, Any]:
    """
    Parse natural language schedule request into structured data.

    Example inputs:
    - "create schedule for stephen sibbinson today on job 20990, cost centre 116534, 1 hour"
    - "schedule john smith tomorrow 9am 4 hours job 123"
    - "add schedule: staff=jane, job=456, date=2026-02-15, blocks=8"

    Returns:
        Fake "extracted" dict with single schedule row
    """

    logger.info(f"🗣️ Parsing chat schedule request: {user_text[:100]}")

    # Build LLM prompt to extract schedule details
    system_prompt = """You are a schedule data parser. Extract schedule information from natural language.

Return ONLY valid JSON. The structure depends on the operation type.

=== STRUCTURE RULES ===

1. All operations have an "operation" field: "CREATE" | "UPDATE" | "DELETE" | "COPY"

2. CREATE uses "schedule_data" with ALL new schedule information

3. UPDATE/DELETE uses:
   - "find_criteria": How to LOCATE the existing schedule
   - "schedule_data": What to CHANGE (UPDATE only, omit for DELETE)

4. COPY uses both:
   - "find_criteria": How to LOCATE the SOURCE schedule to copy FROM
   - "schedule_data": What to CHANGE on the copy (at minimum, a new date)

=== OPERATION DETECTION ===
- "create", "add", "schedule", "book" → "CREATE"
- "update", "change", "modify", "reschedule" → "UPDATE"
- "delete", "remove", "cancel" → "DELETE"
- "copy", "duplicate", "replicate", "clone" → "COPY"

=== SCHEMAS ===

FOR CREATE:
{
  "operation": "CREATE",
  "schedule_data": {
    // REQUIRED fields:
    "staff_name": <string>,           // Staff member name
    "date": <string>,                 // Schedule date — MUST be YYYY-MM-DD (resolve all relative dates)
    "start_time": <string or null>,   // Start time (HH:MM in 24-hour) — null if user didn't specify
    "blocks": <number or null>,       // Duration in hours — null if user didn't specify
    "end_time": <string or null>,     // End time (HH:MM in 24-hour) — null if user didn't specify. NEVER set both end_time AND blocks.

    // OPTIONAL fields (at least ONE job context required):
    "job_id": <number or null>,       // Job ID
    "job_name": <string or null>,     // Job name (if ID not known)
    "site_name": <string or null>,    // Site/address name (if user references a site/location instead of job name)
    "quote_id": <number or null>,     // Quote ID (alternative to job)
    "section_id": <number or null>,
    "section_name": <string or null>,
    "cost_centre_id": <number or null>,
    "cost_centre_name": <string or null>,
    "staff_id": <number or null>,     // Staff ID (if known)
    "notes": <string or null>,
    "is_locked": <boolean or null>    // true to lock, false to unlock, null if not specified
  }
}

FOR UPDATE:
{
  "operation": "UPDATE",
  "find_criteria": {
    // REQUIRED - to locate existing schedule:
    "staff_name": <string>,           // Staff member name (use RESOLVED name from history if available)
    "date": <string>,                 // CURRENT date of schedule (not new date!)

    // OPTIONAL - to narrow search:
    "job_id": <number or null>,       // If multiple schedules exist for staff
    "cost_centre_id": <number or null>, // If multiple schedules on same job
    "schedule_id": <number or null>,  // Schedule ID — if known from conversation history, ALWAYS include it
    "staff_id": <number or null>,     // Staff ID — if known from conversation history, include it
    "section_id": <number or null>    // Section ID — if known from conversation history, include it
  },
  "schedule_data": {
    // ALL OPTIONAL - only include fields to UPDATE:
    "date": <string or null>,         // NEW date (if moving schedule to different day)
    "start_time": <string or null>,   // NEW start time
    "blocks": <number or null>,       // NEW duration
    "end_time": <string or null>,     // NEW end time — when user specifies when schedule should END. NEVER set both end_time AND blocks.
    "notes": <string or null>,        // NEW notes
    "staff_name": <string or null>,   // NEW staff name (reassign to different person)
    "staff_id": <number or null>,     // NEW staff ID (if known)
    "is_locked": <boolean or null>    // true to lock, false to unlock, null if not specified
  }
}

FOR DELETE:
{
  "operation": "DELETE",
  "find_criteria": {
    // REQUIRED - to locate schedule to delete:
    "staff_name": <string>,           // Staff member name (use RESOLVED name from history if available)
    "date": <string>,

    // OPTIONAL - to narrow search:
    "job_id": <number or null>,       // If multiple schedules exist for staff
    "cost_centre_id": <number or null>, // If multiple schedules on same job
    "schedule_id": <number or null>,  // Schedule ID — if known from conversation history, ALWAYS include it
    "staff_id": <number or null>,     // Staff ID — if known from conversation history, include it
    "section_id": <number or null>    // Section ID — if known from conversation history, include it
  }
  // No schedule_data for DELETE
}

FOR COPY (duplicate an existing schedule to a new date):
{
  "operation": "COPY",
  "find_criteria": {
    // REQUIRED - to locate the SOURCE schedule to copy FROM:
    "staff_name": <string>,           // Staff member name (use RESOLVED name from history if available)
    "date": <string>,                 // Date of the EXISTING schedule

    // OPTIONAL - to narrow search:
    "job_id": <number or null>,
    "cost_centre_id": <number or null>,
    "schedule_id": <number or null>,  // Schedule ID — if known from conversation history, ALWAYS include it
    "staff_id": <number or null>,     // Staff ID — if known from conversation history, include it
    "section_id": <number or null>    // Section ID — if known from conversation history, include it
  },
  "schedule_data": {
    // REQUIRED:
    "date": <string>,                 // NEW date to copy TO

    // OPTIONAL overrides (if not specified, copies source values):
    "staff_name": <string or null>,   // Copy to a different person
    "start_time": <string or null>,   // Override start time
    "blocks": <number or null>,       // Override duration
    "end_time": <string or null>      // Override end time. NEVER set both end_time AND blocks.
  }
}

=== NOTES FIELD RULES ===

When the user wants to set or update schedule notes:
- Put the ACTUAL notes text directly in the "notes" field as a JSON string.
- Use \\n for line breaks within the text. The system will convert them to HTML automatically.
- Copy the notes content EXACTLY as the user provided — do NOT summarise, truncate, or reword.
- The notes content is the large block of text the user wants saved, NOT the instruction sentence.

Example: user says: "update the schedule notes to INST | INVC ID: 123\nWorks To Do - INSTALL ROOF"
Output: {"operation": "UPDATE", "find_criteria": {...}, "schedule_data": {"notes": "INST | INVC ID: 123\\nWorks To Do - INSTALL ROOF"}}

=== IMPORTANT RULES ===

0. NEVER guess or invent values:
   - If the user does NOT specify hours/duration/blocks → set blocks: null (do NOT default to 4 or 8)
   - If the user does NOT specify a start time → set start_time: null (do NOT guess "08:00")
   - The system will ask the user for missing required fields. Your job is to extract ONLY what the user explicitly said.

1. For UPDATE/DELETE:
   - find_criteria.date = CURRENT date where schedule exists
   - schedule_data.date = NEW date (only if moving schedule)

2. Date handling — ALWAYS resolve to YYYY-MM-DD:
   You MUST convert ALL date expressions to YYYY-MM-DD format using today's date as reference.
   NEVER pass relative expressions like "next tuesday" or "this friday" as-is.
   - "today" → today's date in YYYY-MM-DD
   - "tomorrow" → tomorrow's date in YYYY-MM-DD
   - "yesterday" → yesterday's date in YYYY-MM-DD
   - "next monday", "this friday", "next week tuesday" → calculate the actual YYYY-MM-DD
   - "next week" without a day = next Monday's YYYY-MM-DD
   - "next week same day" = same weekday next week in YYYY-MM-DD
   - DD/MM/YYYY (e.g., "23/03/2024") → convert to YYYY-MM-DD (2024-03-23)
   - "23rd march", "march 15" → use CURRENT year, output YYYY-MM-DD
   - "next december 2nd week tuesday" → calculate the actual date in YYYY-MM-DD
   - When an UPDATE/move message contains TWO dates (one explicit like DD/MM/YYYY and one relative like "23rd march"),
     the explicit/older date is usually the SOURCE (find_criteria.date) and the relative/newer date is the DESTINATION (schedule_data.date).
     Example: "move 23/03/2024 tarun schedule for 23rd march" → find_criteria.date="2024-03-23", schedule_data.date="2026-03-23"

3. Time format: 24-hour HH:MM
   - "8am" or "08:00 AM" → "08:00"
   - "2pm" or "14:00" → "14:00"
   - "3:30pm" → "15:30"

4. Blocks (duration in hours):
   - Integer: 4, 8
   - Decimal: 1.5 (1 hour 30 min), 2.25 (2 hours 15 min)
   - "half day" or "half a day" → blocks: 4
   - "full day" → blocks: 8
   - Time ranges: "8am to 3pm" → start_time: "08:00", blocks: 7
   - Time ranges: "7am to 3:30pm" → start_time: "07:00", blocks: 8.5
   - When user gives a time range (start to end), calculate blocks = end - start in hours

4b. End time (when user specifies when the schedule should FINISH):
   - "finish at 3pm" or "end at 3pm" or "until 3pm" → end_time: "15:00"
   - "reduce the time to 7:45am" → end_time: "07:45"
   - WHEN TO USE end_time vs start_time vs blocks vs blocks_adjust:
     * "from 8am to 3pm" (time RANGE with both start and end given) → start_time + blocks (calculate duration)
     * "4 hours", "half day" (DURATION) → blocks (absolute)
     * "by 2 hours", "extend by", "reduce by" (RELATIVE adjustment) → blocks_adjust
     * "until 3pm", "finish at 3pm", "end at 3pm", "reduce/shorten to [clock time]" (END TIME) → end_time
   - KEY: "reduce/shorten TO [clock time]" = set end time. "reduce/shorten BY [duration]" = blocks_adjust.
   - NEVER set both end_time AND blocks — use one or the other. The system will compute blocks from end_time automatically.
   - If user gives a full range ("8am to 3pm"), use start_time + blocks, NOT end_time.

5. Name resolution:
   - Always prefer IDs if mentioned
   - Extract names exactly as provided
   - Fuzzy matching handled by backend
   - "staff_name" covers BOTH employees AND contractors/subcontractors.
     Company-style names like "MTS Roofing", "ABC Plumbing", "Smith Electrical" are
     CONTRACTORS and belong in staff_name, NOT in job_name.
     Only put a value in job_name if it is clearly referring to a project/job title.
   - When user says "schedule on <contractor>" or "schedule for <contractor>",
     the contractor name goes in staff_name.
   - When user gives BOTH a contractor/person AND a site/address, put the contractor
     in staff_name and the site in site_name.

6. Lock/Unlock:
   - "lock", "lock the schedule" → UPDATE with is_locked: true
   - "unlock", "unlock the schedule" → UPDATE with is_locked: false

7. Reassignment:
   - "reassign to John" or "assign to John" → UPDATE with schedule_data.staff_name: "John"
   - "move Stephen's schedule to John" → find_criteria.staff_name: "Stephen", schedule_data.staff_name: "John"

8. Relative adjustments:
   - "extend by 2 hours" or "add 2 hours" or "make it 2 hours longer" → UPDATE with schedule_data.blocks_adjust: +2
   - "shorten by 1 hour" or "reduce by 1 hour" or "cut 1 hour" → UPDATE with schedule_data.blocks_adjust: -1
   - Use "blocks_adjust" (relative) instead of "blocks" (absolute) when the user says "by X hours" / "more" / "less" / "longer" / "shorter" / "extend" / "reduce"
   - If the user says "change to 4 hours" or "set to 4 hours" → use blocks: 4 (absolute)
   - If the user says "reduce the time to 7:45am" → use end_time: "07:45" (setting END time, NOT blocks_adjust)
   - If the user says "finish at 3pm" or "until 3pm" → use end_time: "15:00"

9. Follow-up field merging:
   When a PREVIOUS INCOMPLETE REQUEST is provided in the prompt, the user is supplying a MISSING field
   (e.g. staff name, date, job id) that was not in the original request.
   You MUST merge the previously parsed fields with the new user input:
   - Keep ALL fields from the previous request (operation, job_id, cost_centre_id, date, start_time, blocks, etc.)
   - Override/add ONLY the field(s) the user is now providing
   - Example: previous request had job_id=22601, cost_centre_id=154740, date="2026-03-04", start_time="07:00", blocks=8.5 but was missing staff_name.
     User now says "its jarrad edwards" → output the FULL schedule with ALL previous fields PLUS staff_name: "jarrad edwards"
   - Do NOT output a minimal record with only the new field — that would lose all previously parsed data.

=== EXAMPLES ===

Example 1 - CREATE minimal:
Input: "create schedule for john today 8am 4hrs on job 123"
Output: {
  "operation": "CREATE",
  "schedule_data": {
    "staff_name": "john",
    "job_id": 123,
    "date": "today",
    "start_time": "08:00",
    "blocks": 4
  }
}

Example 2 - CREATE with missing blocks (user didn't specify hours):
Input: "put stephen on job 10853 tomorrow at 8"
Output: {
  "operation": "CREATE",
  "schedule_data": {
    "staff_name": "stephen",
    "job_id": 10853,
    "date": "tomorrow",
    "start_time": "08:00",
    "blocks": null
  }
}
Explanation: User said "at 8" (start time) but NOT how many hours. blocks must be null — never guess.

Example 3 - CREATE with contractor name + site (contractor goes in staff_name, NOT job_name):
Input: "create a schedule on MTS_roof plumbing for today on site 1 bloomfield avenue from 7am to 12pm"
Output: {
  "operation": "CREATE",
  "schedule_data": {
    "staff_name": "MTS_roof plumbing",
    "site_name": "1 bloomfield avenue",
    "date": "today",
    "start_time": "07:00",
    "blocks": 5
  }
}
Explanation: "MTS_roof plumbing" is a contractor/company name → staff_name. "1 bloomfield avenue" is a site address → site_name. Time range 7am-12pm = 5 hours.

Example 4 - CREATE with details:
Input: "create schedule for john smith tomorrow 9am for 8 hours on job 456 section Electrical cost centre Labor"
Output: {
  "operation": "CREATE",
  "schedule_data": {
    "staff_name": "john smith",
    "job_id": 456,
    "section_name": "Electrical",
    "cost_centre_name": "Labor",
    "date": "tomorrow",
    "start_time": "09:00",
    "blocks": 8
  }
}

Example 4 - UPDATE with date change:
Input: "update today's stephen's schedule to tomorrow 10am for 1.5hrs"
Output: {
  "operation": "UPDATE",
  "find_criteria": {"staff_name": "stephen", "date": "today"},
  "schedule_data": {"date": "tomorrow", "start_time": "10:00", "blocks": 1.5}
}
Explanation: "today's schedule" = find on today, "to tomorrow" = move to tomorrow

Example 5 - UPDATE time only:
Input: "update stephen's schedule today to 2pm for 3 hours"
Output: {
  "operation": "UPDATE",
  "find_criteria": {"staff_name": "stephen", "date": "today"},
  "schedule_data": {"start_time": "14:00", "blocks": 3}
}
Explanation: No date in schedule_data = keep existing date

Example 6 - UPDATE duration only:
Input: "change stephen's schedule today to 1.5 hours"
Output: {
  "operation": "UPDATE",
  "find_criteria": {"staff_name": "stephen", "date": "today"},
  "schedule_data": {"blocks": 1.5}
}

Example 6b - UPDATE end time ("reduce to" a clock time):
Input: "reduce the time of nicholas for today's job 22601 to 7:45am"
Output: {
  "operation": "UPDATE",
  "find_criteria": {"staff_name": "nicholas", "date": "today", "job_id": 22601},
  "schedule_data": {"end_time": "07:45"}
}
Explanation: "reduce the time TO 7:45am" = set end time to 07:45. The system computes blocks from existing start_time.

Example 6c - UPDATE with "finish at":
Input: "make nicholas finish at 3pm today on job 22601"
Output: {
  "operation": "UPDATE",
  "find_criteria": {"staff_name": "nicholas", "date": "today", "job_id": 22601},
  "schedule_data": {"end_time": "15:00"}
}

Example 6d - CREATE with "until" (end time):
Input: "schedule nicholas tomorrow 7am until 1pm on job 22601"
Output: {
  "operation": "CREATE",
  "schedule_data": {
    "staff_name": "nicholas",
    "job_id": 22601,
    "date": "tomorrow",
    "start_time": "07:00",
    "end_time": "13:00"
  }
}
Explanation: "7am until 1pm" — start_time from 7am, end_time from 1pm. System computes blocks = 6.

Example 7 - DELETE simple:
Input: "delete stephen's schedule for today"
Output: {
  "operation": "DELETE",
  "find_criteria": {"staff_name": "stephen", "date": "today"}
}

Example 8 - DELETE with context:
Input: "delete tomorrow's schedule of stephen on job 20990"
Output: {
  "operation": "DELETE",
  "find_criteria": {
    "staff_name": "stephen",
    "date": "tomorrow",
    "job_id": 20990
  }
}

Example 9 - CREATE with time range:
Input: "create schedule for stephen 7am to 3pm tomorrow job 10853 CC 77135"
Output: {
  "operation": "CREATE",
  "schedule_data": {
    "staff_name": "stephen",
    "job_id": 10853,
    "cost_centre_id": 77135,
    "date": "tomorrow",
    "start_time": "07:00",
    "blocks": 8
  }
}
Explanation: 7am to 3pm = 8 hours, start_time from the range start

Example 10 - CREATE with half day:
Input: "schedule stephen for half a day tomorrow 8am job 10853 CC 77135"
Output: {
  "operation": "CREATE",
  "schedule_data": {
    "staff_name": "stephen",
    "job_id": 10853,
    "cost_centre_id": 77135,
    "date": "tomorrow",
    "start_time": "08:00",
    "blocks": 4
  }
}

Example 11 - UPDATE reassign to different person:
Input: "reassign stephen's schedule today to john"
Output: {
  "operation": "UPDATE",
  "find_criteria": {"staff_name": "stephen", "date": "today"},
  "schedule_data": {"staff_name": "john"}
}

Example 12 - DELETE with cost centre:
Input: "delete stephen's schedule today on job 10853 cost centre 77130"
Output: {
  "operation": "DELETE",
  "find_criteria": {
    "staff_name": "stephen",
    "date": "today",
    "job_id": 10853,
    "cost_centre_id": 77130
  }
}

Example 12b - UPDATE with explicit DD/MM/YYYY source date and relative destination:
Input: "move 23/03/2024 tarun schedule for 23rd march"
Output: {
  "operation": "UPDATE",
  "find_criteria": {"staff_name": "tarun", "date": "2024-03-23"},
  "schedule_data": {"date": "2026-03-23"}
}
Explanation: "23/03/2024" is DD/MM/YYYY = source date 2024-03-23. "23rd march" = destination date, current year = 2026-03-23.

Example 12c - COPY schedule to new date:
Input: "copy 23rd march's schedule of tarun to tomorrow"
Output: {
  "operation": "COPY",
  "find_criteria": {"staff_name": "tarun", "date": "2026-03-23"},
  "schedule_data": {"date": "tomorrow"}
}
Explanation: find_criteria = SOURCE (where to copy FROM), schedule_data = DESTINATION (where to copy TO).
The system will look up tarun's schedule on 23rd March and create a new one on tomorrow with the same job, hours, and start time.

Example 12d - COPY schedule to different person:
Input: "copy stephen's schedule today to john tomorrow"
Output: {
  "operation": "COPY",
  "find_criteria": {"staff_name": "stephen", "date": "today"},
  "schedule_data": {"date": "tomorrow", "staff_name": "john"}
}
Explanation: Copy stephen's schedule details (job, hours, time) but assign to john on tomorrow.

=== CONVERSATION CONTEXT RESOLUTION ===

When the user says "this schedule", "that one", "delete it", "the same job", etc.,
you MUST look at the conversation history (prior messages) to resolve the reference.

CRITICAL: Extract ALL available IDs and values from previous assistant messages.
The conversation history contains RESOLVED values like schedule_id, staff_id,
job_id, section_id, cost_centre_id. You MUST include these in your output —
they allow the system to skip expensive API lookups and act directly.

PRIORITY ORDER for follow-up operations:
1. ALWAYS include schedule_id, staff_id, section_id, job_id, cost_centre_id from history
2. Use the RESOLVED staff name from history (not the original user shorthand)
3. Include date from history
4. Only omit an ID if it genuinely was not present in the history

Example 13 - DELETE with pronoun "this" (resolved from history with ALL IDs):
Previous assistant message: "COMPLETED CREATE schedule: staff_name=Stephen, staff_id=3465, job_id=20985, section_id=50123, cost_centre_id=116518, date=2026-02-13, schedule_id=98765"
Input: "delete this schedule"
Output: {
  "operation": "DELETE",
  "find_criteria": {
    "staff_name": "stephen",
    "date": "2026-02-13",
    "job_id": 20985,
    "cost_centre_id": 116518,
    "schedule_id": 98765,
    "staff_id": 3465,
    "section_id": 50123
  }
}

Example 14 - CREATE with "same job" reference:
Previous assistant message: "COMPLETED UPDATE schedule: job_id=20985, cost_centre_id=116518, section_id=50123, staff_name=Stephen, staff_id=3465"
Input: "create a schedule for john on the same job tomorrow 8am 4 hours"
Output: {
  "operation": "CREATE",
  "schedule_data": {
    "staff_name": "john",
    "job_id": 20985,
    "section_id": 50123,
    "cost_centre_id": 116518,
    "date": "tomorrow",
    "start_time": "08:00",
    "blocks": 4
  }
}

IMPORTANT: Never return empty staff_name or date. If the user references a previous
schedule ("this", "it", "that"), extract ALL relevant IDs from conversation history.
Including IDs from history prevents unnecessary API calls and speeds up operations.

=== RELATIVE VALUE RESOLUTION ===

When the user uses phrases like "same time", "same hours", "same start", "yesterday's hours",
"same schedule", extract the ACTUAL values from:
1. FOLLOW-UP FIELD BRIDGE (if present in the prompt) — use these first, they are pre-resolved.
2. Conversation history — look for start_time=, blocks=, date=, job_id= in previous assistant messages.
NEVER pass relative phrases ("same time") as field values — always resolve to actual values (e.g., "07:00", 8.5).

CROSS-PATH DATA: History may contain results from OTHER agents or MCP queries, not just schedules.
Extract common fields (job_id, staff, dates) from ANY history format:
- Schedule: "COMPLETED CREATE schedule: job_id=22601, staff_name=Nick, date=2026-03-05"
- Invoice: "COMPLETED CREATE invoice: job_id=10675, cost_centres=[116534 (Drainage)]"
- Workorder: "[workorder agent succeeded: CREATED CJ 46450 (MTS Roofing)]"
- MCP data: "[Data Context — N items] ID=22601 Name=Bloomfield"
When user says "same job" and history shows an invoice/WO/MCP result with job_id, use that job_id.

Example 14b - CREATE with "same time" from history:
Previous assistant: "COMPLETED CREATE schedule: staff_name=Nick, staff_id=3465, job_id=22601, date=2026-03-05, start_time=07:00, blocks=8.5, schedule_id=99001"
Input: "schedule jarrad same time same hours on job 40932 tomorrow"
Output: {"operation": "CREATE", "schedule_data": {"staff_name": "jarrad", "job_id": 40932, "date": "<tomorrow's YYYY-MM-DD>", "start_time": "07:00", "blocks": 8.5}}
Explanation: "same time" → start_time=07:00 from history. "same hours" → blocks=8.5 from history. New staff and job.

=== CORRECTION PATTERNS ===

When the user corrects a previous request, extract the UPDATED values while keeping
unchanged values from the previous operation in conversation history.

Example 19 - Correct start time:
Previous assistant: "COMPLETED CREATE schedule: staff_name=Tarun, staff_id=3465, job_id=10675, section_id=50123, cost_centre_id=116534, date=2026-02-17, start_time=08:00, blocks=4, schedule_id=12345"
Input: "no, make it 10am"
Output: {
  "operation": "UPDATE",
  "find_criteria": {"staff_name": "tarun", "date": "2026-02-17", "job_id": 10675, "schedule_id": 12345, "staff_id": 3465, "section_id": 50123},
  "schedule_data": {"start_time": "10:00"}
}

Example 20 - Wrong person correction:
Previous assistant: "COMPLETED CREATE schedule: staff_name=Tarun, staff_id=3465, job_id=10675, section_id=50123, cost_centre_id=116534, date=2026-02-17, schedule_id=12345"
Input: "wrong person, should be John"
Output: {
  "operation": "UPDATE",
  "find_criteria": {"staff_name": "tarun", "date": "2026-02-17", "job_id": 10675, "schedule_id": 12345, "staff_id": 3465, "section_id": 50123},
  "schedule_data": {"staff_name": "john"}
}

Example 21 - Do same for another person:
Previous assistant: "COMPLETED CREATE schedule: staff_name=Tarun, staff_id=3465, job_id=10675, cost_centre_id=116534, date=2026-02-17, start_time=08:00, blocks=4"
Input: "do the same for John"
Output: {
  "operation": "CREATE",
  "schedule_data": {
    "staff_name": "john",
    "job_id": 10675,
    "cost_centre_id": 116534,
    "date": "2026-02-17",
    "start_time": "08:00",
    "blocks": 4
  }
}

Example 22 - Wrong cost centre correction after failure:
Previous assistant: "FAILED CREATE schedule: staff_name=Tarun, job_id=10675, cost_centre_name=metal roof, error=No cost centre matching"
Input: "try Drainage instead"
Output: {
  "operation": "CREATE",
  "schedule_data": {
    "staff_name": "tarun",
    "job_id": 10675,
    "cost_centre_name": "drainage",
    "date": "2026-02-17"
  }
}

Example 22b - Providing missing staff after clarification:
Previous assistant: "[schedule agent NEEDS CLARIFICATION: fields_to_clarify=['StaffName'], already_parsed=[Operation=CREATE, JobID=22601, CostCentreID=154740, Date=2026-03-04, StartTime=07:00, Blocks=8.5], user_request="create schedule job id 22601..."]"
Input: "its jarrad edwards"
Output: {
  "operation": "CREATE",
  "schedule_data": {
    "staff_name": "jarrad edwards",
    "job_id": 22601,
    "cost_centre_id": 154740,
    "date": "2026-03-04",
    "start_time": "07:00",
    "blocks": 8.5
  }
}
Explanation: The user is providing the missing staff_name. ALL other fields come from already_parsed in the previous message. Do NOT output only staff_name — include ALL previously parsed fields.

Example 23 - Chain operation (lock after create):
Previous assistant: "COMPLETED CREATE schedule: staff_name=Tarun, staff_id=3465, job_id=10675, section_id=50123, cost_centre_id=116534, date=2026-02-17, schedule_id=12345"
Input: "now lock it"
Output: {
  "operation": "UPDATE",
  "find_criteria": {"staff_name": "tarun", "date": "2026-02-17", "job_id": 10675, "schedule_id": 12345, "staff_id": 3465, "section_id": 50123},
  "schedule_data": {"is_locked": true}
}

Example 24 - Delete what was just created:
Previous assistant: "COMPLETED CREATE schedule: staff_name=Tarun, staff_id=3465, job_id=10675, section_id=50123, cost_centre_id=116534, date=2026-02-17, schedule_id=12345"
Input: "delete it"
Output: {
  "operation": "DELETE",
  "find_criteria": {"staff_name": "tarun", "date": "2026-02-17", "job_id": 10675, "cost_centre_id": 116534, "schedule_id": 12345, "staff_id": 3465, "section_id": 50123}
}

=== MULTIPLE SCHEDULES ===

When the user requests MULTIPLE schedules in a single message (multiple people, multiple dates,
same person with different time slots, or any combination), return a "schedules" ARRAY instead
of a single object.

IMPORTANT: When the user says "and another schedule" or "and also" with the SAME staff but a
different time slot or cost centre, EACH item in the array MUST repeat ALL shared fields
(staff_name, site_name, section_name, date, job_id, etc.) — do NOT omit them from any item.

{
  "schedules": [
    {"operation": "CREATE", "schedule_data": {...}},
    {"operation": "CREATE", "schedule_data": {...}}
  ]
}

Each item in the array follows the SAME schema as a single schedule (CREATE/UPDATE/DELETE).

Example 15 - Multiple people, same schedule:
Input: "Create schedule for Stephen and John tomorrow 8am 4hrs job 10853 CC 77135"
Output: {
  "schedules": [
    {
      "operation": "CREATE",
      "schedule_data": {
        "staff_name": "stephen",
        "job_id": 10853,
        "cost_centre_id": 77135,
        "date": "tomorrow",
        "start_time": "08:00",
        "blocks": 4
      }
    },
    {
      "operation": "CREATE",
      "schedule_data": {
        "staff_name": "john",
        "job_id": 10853,
        "cost_centre_id": 77135,
        "date": "tomorrow",
        "start_time": "08:00",
        "blocks": 4
      }
    }
  ]
}

Example 16 - Multiple dates, same person:
Input: "Create schedules for Stephen Mon/Tue/Wed 8am 4hrs job 10853 CC 77135"
Output: {
  "schedules": [
    {
      "operation": "CREATE",
      "schedule_data": {
        "staff_name": "stephen",
        "job_id": 10853,
        "cost_centre_id": 77135,
        "date": "next monday",
        "start_time": "08:00",
        "blocks": 4
      }
    },
    {
      "operation": "CREATE",
      "schedule_data": {
        "staff_name": "stephen",
        "job_id": 10853,
        "cost_centre_id": 77135,
        "date": "next tuesday",
        "start_time": "08:00",
        "blocks": 4
      }
    },
    {
      "operation": "CREATE",
      "schedule_data": {
        "staff_name": "stephen",
        "job_id": 10853,
        "cost_centre_id": 77135,
        "date": "next wednesday",
        "start_time": "08:00",
        "blocks": 4
      }
    }
  ]
}

=== BULK ACTIONS ===

When the user wants to perform the SAME action on ALL of someone's schedules in a date range,
you CANNOT know the exact schedules from text alone. Return a "bulk_action" object instead.

CRITICAL: Use bulk_action (NOT operation: DELETE) when:
- User says "schedules" (PLURAL) without specifying a particular job or schedule ID
  e.g., "remove the schedules of jarad edwards today" → bulk_action: DELETE
  e.g., "delete all of stephen's schedules this week" → bulk_action: DELETE
  e.g., "can you remove jarad edwards schedules for today" → bulk_action: DELETE
- User says "all schedules", "lock all", "delete all"

Use operation: DELETE (NOT bulk_action) when:
- User refers to a SINGLE specific schedule with a job or schedule ID
  e.g., "delete stephen's schedule on job 20990" → operation: DELETE
  e.g., "remove schedule 12345" → operation: DELETE
- User says "schedule" (SINGULAR) with a specific job context

{
  "bulk_action": "UPDATE" or "DELETE",
  "find_criteria": {
    "staff_name": <string or null>,   // A specific person's name, OR null when filtering by type
    "staff_type": <string or null>,   // "contractor" or "employee" — use when user says "contractor schedules" or "employee schedules" instead of a person's name
    "date_range": <string>            // "today", "tomorrow", "this week", "next week"
  },
  "schedule_data": {             // Only for UPDATE bulk_action
    "is_locked": <boolean>,
    "notes": <string or null>
  }
}

The system will look up all matching schedules and apply the action to each one.

IMPORTANT: When the user says "contractor schedules" or "all contractor schedules", they mean
schedules belonging to ANY contractor (staff_type: "contractor"), NOT a person named "contractor".
Similarly "employee schedules" means all employee schedules (staff_type: "employee").

Example 17 - Lock all schedules this week:
Input: "Lock all of Stephen's schedules this week"
Output: {
  "bulk_action": "UPDATE",
  "find_criteria": {"staff_name": "stephen", "date_range": "this week"},
  "schedule_data": {"is_locked": true}
}

Example 18 - Delete all schedules this week:
Input: "Delete all of Stephen's schedules this week"
Output: {
  "bulk_action": "DELETE",
  "find_criteria": {"staff_name": "stephen", "date_range": "this week"}
}

Example 19 - Delete all contractor schedules today:
Input: "delete today's contractor schedules"
Output: {
  "bulk_action": "DELETE",
  "find_criteria": {"staff_type": "contractor", "date_range": "today"}
}

Example 20 - Remove someone's schedules (plural, no job specified):
Input: "can you remove the schedules of jarad edwards of today"
Output: {
  "bulk_action": "DELETE",
  "find_criteria": {"staff_name": "jarad edwards", "date_range": "today"}
}
Explanation: "schedules" is PLURAL with no specific job → bulk_action, NOT operation.

=== ERROR RECOVERY / FOLLOW-UP RESPONSES ===

When conversation history shows a recent FAILED operation with a specific error, and the user's
follow-up message provides the missing information, you MUST reconstruct the ORIGINAL operation
with the missing field filled in — NOT treat the follow-up as a new CREATE request.

Key patterns:
1. MISSING_STAFF error + user provides a name → Recreate the original bulk_action/operation with that staff_name
2. Missing JobID error + user provides a job ID or name → Recreate the original operation with that job context
3. Missing date/time error + user provides a date/time → Recreate the original operation with that date/time

Example 25 - Follow-up to MISSING_STAFF on bulk DELETE:
Previous assistant: "[schedule agent FAILED: MISSING_STAFF, user_request=\"delete all today's job schedules\"]"
Input: "alister andrews"
Output: {
  "bulk_action": "DELETE",
  "find_criteria": {"staff_name": "alister andrews", "date_range": "today"}
}
Explanation: The user is answering the missing staff question for the original bulk DELETE. Do NOT treat "alister andrews" as a new CREATE.

Example 26 - Follow-up providing job ID after missing job error:
Previous assistant: "[schedule agent FAILED: RESOLUTION_ERRORS, ... Please specify JobID, JobName, or SiteName]"
Input: "20527 job id"
Output: Reconstruct the ORIGINAL operation from history with job_id: 20527 added.

RULE: If the user's message contains NO operation verb (create/delete/update/copy/lock) and the
conversation history shows a recent FAILED request, assume the user is providing missing
information for that failed operation. Reconstruct it accordingly.

=== CHOOSING BETWEEN FORMATS ===
- Single schedule (1 person, 1 date, 1 time slot) → single object with "operation"
- Multiple explicit schedules (2+ people, 2+ named dates, OR same person with 2+ distinct time slots or cost centres) → "schedules" array
- Bulk action on unknown number of schedules ("all", "every", "this week") → "bulk_action"
"""

    today_str = datetime.now().strftime("%Y-%m-%d")
    today_weekday = datetime.now().strftime("%A")

    # For follow-up references ("delete it", "the above schedule"), extract the
    # last assistant message containing resolved schedule IDs and surface it
    # directly in the user prompt so the LLM cannot miss the context.
    context_hint = ""
    if conversation_history:
        for msg in reversed(conversation_history):
            if msg.get("role") == "assistant" and "schedule" in msg.get("content", "").lower():
                content = msg["content"]
                # Case 1: Previous clarification/failure with parsed data — user is providing missing fields
                # Check this BEFORE Case 2 since FAILED messages may also contain "date=" etc.
                if "already_parsed=" in content or "NEEDS CLARIFICATION" in content:
                    context_hint = (
                        f"\n\nPREVIOUS INCOMPLETE REQUEST (the user is now providing missing fields — "
                        f"merge ALL previously parsed values with the new user input):\n{content}"
                    )
                    break
                # Case 2: Previous successful operation — use IDs for "delete it" etc.
                if "schedule_id=" in content or "date=" in content or "staff_id=" in content:
                    context_hint = (
                        f"\n\nPREVIOUS OPERATION (use ALL IDs and date from here for follow-up references like "
                        f"'delete it', 'the above schedule', 'that one'):\n{content}"
                    )
                    break

    # Follow-up context bridge: explicit reuse/changed fields from intent analyzer
    reuse_hint = ""
    if hints:
        reuse_fields = hints.get("reuse_fields")
        changed_fields = hints.get("changed_fields")
        if reuse_fields or changed_fields:
            parts = []
            if reuse_fields:
                field_strs = [f"{k}={v}" for k, v in reuse_fields.items()]
                parts.append(f"REUSE these fields from the previous operation: {', '.join(field_strs)}")
            if changed_fields:
                field_strs = [f"{k}={v}" for k, v in changed_fields.items()]
                parts.append(f"CHANGE these fields: {', '.join(field_strs)}")
            reuse_hint = "\n\nFOLLOW-UP FIELD BRIDGE (use these as pre-resolved values):\n" + "\n".join(parts)

    user_prompt = f"Today's date is {today_str} ({today_weekday}).{context_hint}{reuse_hint}\n\nExtract schedule data from: {user_text}"

    try:
        # Build messages with conversation history for context resolution
        messages = [{"role": "system", "content": system_prompt}]
        if conversation_history:
            # Include recent history so LLM can resolve "this", "that schedule", etc.
            messages.extend(conversation_history[-6:])  # last 3 rounds
        messages.append({"role": "user", "content": user_prompt})

        # Call LLM to parse
        response = llm_chat(
            messages,
            response_format={"type": "json_object"},
            temperature=0.0
        )

        import json
        parsed = json.loads(response)

        # Convert plain-text notes (with \n) to Simpro HTML (<br>)
        parsed = _notes_to_html(parsed)

        logger.info(f"✅ Parsed chat request: {parsed}")

        # Date normalization helper (local closure)
        def normalize_date_field(date_str):
            """Convert natural language dates to YYYY-MM-DD format."""
            if not date_str:
                return None
            if date_str == "today":
                return datetime.now().strftime("%Y-%m-%d")
            elif date_str == "tomorrow":
                return (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
            elif date_str == "yesterday":
                return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            else:
                try:
                    parsed_date = date_parser.parse(date_str)
                    return parsed_date.strftime("%Y-%m-%d")
                except Exception:
                    raise ValueError(f"Could not understand the date '{date_str}'. Please use a format like YYYY-MM-DD, 'today', or 'tomorrow'.")

        headers = CHAT_HEADERS

        # ── Handle COPY → convert to CREATE by fetching source schedule ──
        if parsed.get("operation", "").upper() == "COPY":
            logger.info("📋 COPY operation detected — fetching source schedule")
            parsed = await _convert_copy_to_create(parsed, mcp_executor, normalize_date_field)

        # ── Handle bulk_action (lock all, delete all) ──
        if "bulk_action" in parsed:
            logger.info(f"🔄 Bulk action detected: {parsed['bulk_action']}")
            return await _expand_bulk_action(parsed, mcp_executor, headers)

        # ── Handle multi-schedule array ──
        if "schedules" in parsed and isinstance(parsed["schedules"], list):
            logger.info(f"📋 Multi-schedule request: {len(parsed['schedules'])} schedules")
            all_rows = []
            row_metadata = []  # per-row metadata (new_staff_name, new_date) for reassignment/date-move
            for sched in parsed["schedules"]:
                # Normalize old flat schema if needed
                sched = _normalize_parsed_schema(sched)
                op = sched.get("operation", "CREATE").upper()
                row_data, new_date, new_staff_name = _build_row_data_from_parsed(sched, normalize_date_field)
                all_rows.append(row_data)
                row_metadata.append({
                    "new_date": new_date if op == "UPDATE" else None,
                    "new_staff_name": new_staff_name if op == "UPDATE" and new_staff_name else None,
                })

            return {
                "detected_type": "schedule_data_from_chat",
                "is_useful": True,
                "tables": [{"headers": headers, "rows": all_rows}],
                "metadata": {},
                "row_metadata": row_metadata,  # per-row, indexed same as rows
            }

        # ── Handle single schedule (existing behavior) ──
        parsed = _normalize_parsed_schema(parsed)
        operation = parsed.get("operation", "CREATE").upper()

        row_data, new_date, new_staff_name = _build_row_data_from_parsed(parsed, normalize_date_field)

        logger.info(f"📋 Generated row: operation={operation}, "
                   f"find_date={row_data[11] if operation in ('UPDATE', 'DELETE') else 'N/A'}, "
                   f"new_date={new_date or 'N/A'}")

        logger.info(f"✅ Successfully parsed chat request as single schedule")

        return {
            "detected_type": "schedule_data_from_chat",
            "is_useful": True,
            "tables": [{"headers": headers, "rows": [row_data]}],
            "metadata": {
                "new_date": new_date if operation == "UPDATE" else None,
                "new_staff_name": new_staff_name if operation == "UPDATE" and new_staff_name else None
            }
        }

    except Exception as e:
        logger.error(f"❌ Failed to parse chat request: {e}")
        return {
            "error": "PARSE_ERROR",
            "message": f"Could not understand schedule request: {str(e)}"
        }


def _friendly_error_fallback(raw: str) -> str:
    """Deterministic fallback for error message translation (no LLM)."""
    raw_lower = raw.lower()
    if "has no sections" in raw_lower:
        return ("This job may be open or pending in Simpro and hasn't been fully set up yet. "
                "Please add sections and cost centres to the job in Simpro first.")
    if "has no cost centres" in raw_lower:
        return ("The section doesn't have any cost centres configured in Simpro. "
                "Please add cost centres to the section first.")
    if "not found in any section" in raw_lower:
        return ("The specified cost centre doesn't exist in this job. "
                "Please check the cost centre ID and try again.")
    if "no staff found" in raw_lower:
        return ("Could not find a staff member matching that name. "
                "Please check the spelling and try again.")
    return raw


# ═══════════════════════════════════════════════════════════════════════════
# Operation Inference
# ═══════════════════════════════════════════════════════════════════════════

# Keyword patterns for inferring operation from user text
_OP_KEYWORDS = {
    "DELETE": ["delete", "remove", "cancel", "drop", "clear"],
    "UPDATE": ["update", "change", "modify", "reschedule", "move", "edit", "alter", "adjust"],
    "CREATE": ["create", "add", "schedule", "book", "assign", "new", "insert"],
}


def _infer_operation(
    hints: Optional[Dict[str, Any]],
    user_text: str,
) -> Optional[str]:
    """
    Infer the schedule operation when the Excel file lacks an Operation column.

    Priority:
    1. hints["action"] from analyze_intent() (most reliable — LLM-based)
    2. Keyword detection from user_text (fallback)

    Returns "CREATE", "UPDATE", or "DELETE", or None if inference fails.
    """
    # Source 1: hints["action"] from intent analyzer
    if hints and hints.get("action"):
        action = hints["action"].lower().strip()
        action_map = {
            "create": "CREATE",
            "update": "UPDATE",
            "delete": "DELETE",
            "lock": "UPDATE",    # lock = UPDATE with IsLocked=true
            "unlock": "UPDATE",  # unlock = UPDATE with IsLocked=false
        }
        if action in action_map:
            logger.info(f"🎯 Operation inferred from intent action '{action}': {action_map[action]}")
            return action_map[action]

    # Source 2: keyword detection from user text
    text_lower = user_text.lower()
    for operation, keywords in _OP_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            logger.info(f"🎯 Operation inferred from keyword in user text: {operation}")
            return operation

    logger.warning("Could not infer operation from hints or user text")
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Main Agent Entry Point
# ═══════════════════════════════════════════════════════════════════════════

async def run_schedule_agent(
    llm_chat: Callable,
    user_text: str,
    extracted: Optional[Dict[str, Any]] = None,
    any_uploaded_text: Optional[str] = None,
    hints: Optional[Dict[str, Any]] = None,
    mcp_executor: Optional[MCPToolExecutor] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """
    Main schedule agent entry point.

    Args:
        llm_chat: LLM chat function (for future use)
        user_text: User's message
        extracted: Structured data from svc-extractor
        any_uploaded_text: Raw CSV text (fallback)
        hints: Hints dict (e.g., {"CompanyID": 2})
        mcp_executor: MCP tool executor instance

    Returns:
        Dict with agent result:
        - success: bool
        - schedules: List of validated schedule records (if successful)
        - needs_clarification: bool
        - clarifications: List of clarification requests (if needed)
        - session_id: str (for resubmission)
    """

    # Reset crossroads cache for this agent run
    reset_crossroads_cache()

    # Create request-scoped execution state (fresh per request — never shared)
    _agent_state = create_agent_state("schedule", user_text)

    logger.info("=" * 70)
    logger.info("🤖 SCHEDULE AGENT STARTED")
    logger.info(f"User message: {user_text[:100]}")
    logger.info(f"Has extracted data: {bool(extracted)}")
    logger.info("=" * 70)

    _agent_state.enter_phase("parse")

    # Handle chat-based requests (no file upload)
    if not extracted or not extracted.get("tables"):
        logger.info("📝 No extracted data - attempting to parse as chat request")

        # Parse natural language schedule request
        chat_extracted = await _parse_chat_schedule_request(user_text, llm_chat, hints, conversation_history, mcp_executor=mcp_executor)

        if "error" in chat_extracted:
            return {
                "success": False,
                "error": chat_extracted["error"],
                "message": chat_extracted.get("message", "Could not parse schedule request from chat")
            }

        # Use parsed data as extracted
        extracted = chat_extracted
        logger.info("✅ Successfully parsed chat request as single schedule")

    if not mcp_executor:
        return {
            "success": False,
            "error": "NO_MCP_EXECUTOR",
            "message": "Internal error: MCP executor not provided"
        }

    # Extract table data
    table = extracted["tables"][0]
    headers = table.get("headers", [])
    rows = table.get("rows", [])

    if not rows:
        return {
            "success": False,
            "error": "NO_ROWS",
            "message": "Excel file is empty. Please add schedule data rows."
        }

    # ── Header normalization — two-path: fast (Tier 1+2) or LLM schema understanding ──
    _per_row_meta = extracted.get("row_metadata", []) if extracted else []
    _llm_inferred_op = None  # may be set by LLM schema path

    if headers and not all(h in _CANONICAL_HEADER_SET for h in headers):
        logger.info(f"🔄 Non-canonical headers detected: {headers}")

        # Tier 1+2: attempt existing synonym-based mapping first (free, no LLM cost)
        tier12_mapping = _normalize_headers_to_canonical(headers, llm_chat)
        resolved_canonicals = {v for v in tier12_mapping.values() if v is not None}
        unresolved_count = sum(1 for v in tier12_mapping.values() if v is None)
        unresolved_pct = unresolved_count / max(len(headers), 1)

        # Decide if we need the full LLM schema understanding:
        # - More than 30% of headers are unresolved, OR
        # - Required fields (StaffName or Date) are missing from resolved canonicals
        #   (Note: JobID/Operation can be resolved later via clarification/_infer_operation)
        required_resolved = bool(
            resolved_canonicals.intersection({"StaffName", "StaffID"})
            and resolved_canonicals.intersection({"Date"})
        )
        needs_llm_schema = unresolved_pct > 0.30 or not required_resolved

        if needs_llm_schema:
            logger.info(
                f"🧠 Invoking LLM schema understanding "
                f"(unresolved={unresolved_pct:.0%}, required_resolved={required_resolved})"
            )
            schema_result = _llm_understand_file_schema(
                headers=headers,
                rows=rows[:5],
                user_text=user_text,
                llm_chat=llm_chat,
            )
            field_map = schema_result["field_map"]
            _llm_inferred_op = schema_result.get("inferred_operation")
            logger.info(
                f"📋 Schema understanding: confidence={schema_result['confidence']:.2f}, "
                f"field_map={field_map}, notes={schema_result['notes']}"
            )

            # Build data_rows directly from field_map — irrelevant columns are never included
            original_headers = headers[:]
            data_rows = []
            for i, raw_row in enumerate(rows):
                src_dict = dict(zip(original_headers, raw_row))
                canonical_row = {canon: src_dict.get(src) for canon, src in field_map.items()}
                if i < len(_per_row_meta) and _per_row_meta[i]:
                    if not canonical_row.get("__new_staff_name__"):
                        canonical_row["__new_staff_name__"] = _per_row_meta[i].get("new_staff_name")
                    if not canonical_row.get("__new_date__"):
                        canonical_row["__new_date__"] = _per_row_meta[i].get("new_date")
                data_rows.append(canonical_row)

            # Pre-inject inferred operation so _infer_operation() check below becomes a no-op
            if _llm_inferred_op and "Operation" not in field_map:
                for row_dict in data_rows:
                    row_dict.setdefault("Operation", _llm_inferred_op)
                headers = [f for f in CHAT_HEADERS if f in {*field_map.keys(), "Operation"}]
            else:
                headers = [f for f in CHAT_HEADERS if f in field_map]
            table["headers"] = headers

        else:
            # Fast path: Tier 1+2 was sufficient
            _apply_header_mapping(table, tier12_mapping)
            headers = table["headers"]
            rows = table["rows"]

            # Standard data_rows construction
            data_rows = []
            for i, row_data in enumerate(rows):
                row_dict = dict(zip(headers, row_data))
                if i < len(_per_row_meta) and _per_row_meta[i]:
                    if not row_dict.get("__new_staff_name__"):
                        row_dict["__new_staff_name__"] = _per_row_meta[i].get("new_staff_name")
                    if not row_dict.get("__new_date__"):
                        row_dict["__new_date__"] = _per_row_meta[i].get("new_date")
                data_rows.append(row_dict)

    else:
        # All headers already canonical — skip normalization entirely
        data_rows = []
        for i, row_data in enumerate(rows):
            row_dict = dict(zip(headers, row_data))
            if i < len(_per_row_meta) and _per_row_meta[i]:
                if not row_dict.get("__new_staff_name__"):
                    row_dict["__new_staff_name__"] = _per_row_meta[i].get("new_staff_name")
                if not row_dict.get("__new_date__"):
                    row_dict["__new_date__"] = _per_row_meta[i].get("new_date")
            data_rows.append(row_dict)

    logger.info(f"📊 Parsed {len(data_rows)} rows with headers: {headers}")

    # ── Extract embedded IDs from corrected template dropdown values ──
    # Dropdown selections look like "SCL- KV Roofing (ID:5870)"
    # Extract the ID and populate the corresponding ID column.
    import re as _re
    _ID_PATTERN = _re.compile(r'\(ID:(\d+)\)\s*$')
    _NAME_ID_FIELDS = {
        "StaffName": "StaffID",
        "JobName": "JobID",
        "SectionName": "SectionID",
        "CostCentreName": "CostCentreID",
    }
    for row_dict in data_rows:
        for name_field, id_field in _NAME_ID_FIELDS.items():
            val = str(row_dict.get(name_field) or "").strip()
            if val:
                m = _ID_PATTERN.search(val)
                if m:
                    extracted_id = m.group(1)
                    # Only set ID if not already provided
                    if not row_dict.get(id_field):
                        row_dict[id_field] = extracted_id
                        logger.info(f"📎 Extracted {id_field}={extracted_id} from '{val}'")
                    # Clean the name field (remove the ID suffix)
                    row_dict[name_field] = _ID_PATTERN.sub("", val).strip()

    # ── Derive Blocks from StartTime + EndTime when no Blocks column exists ──────────────
    for row_dict in data_rows:
        if not row_dict.get("Blocks") and row_dict.get("StartTime") and row_dict.get("EndTime"):
            start_min = _parse_time_to_minutes(str(row_dict["StartTime"]))
            end_min = _parse_time_to_minutes(str(row_dict["EndTime"]))
            if start_min is not None and end_min is not None and end_min > start_min:
                row_dict["Blocks"] = str(round((end_min - start_min) / 60.0, 2))
                logger.info(
                    f"⏱ Derived Blocks={row_dict['Blocks']} "
                    f"from StartTime={row_dict['StartTime']} EndTime={row_dict['EndTime']}"
                )

    _agent_state.complete_phase("parse", detail=f"{len(data_rows)} rows")
    _agent_state.enter_phase("plan")

    # ═══════════════════════════════════════════════════════════════════════
    # LLM PLANNING PHASE (Once per operation)
    # ═══════════════════════════════════════════════════════════════════════

    # Load SOP once — register into crossroads domain knowledge so ALL LLM decisions are SOP-aware
    sop_text = _read_sop(sop_override=(hints or {}).get("sop_override"))
    logger.info(f"[SOP] Loaded {len(sop_text)} chars from schedule SOP")
    if sop_text:
        from utils.crossroads import register_domain_knowledge
        register_domain_knowledge("schedule_operations_sop", f"SCHEDULE OPERATIONS SOP:\n{sop_text}")
        logger.info("[SOP] Registered into crossroads domain knowledge")

    # Extract behaviour flags from SOP — gates defer to SOP; safe defaults if SOP is silent
    sop_flags = await _parse_sop_behaviour_flags(sop_text, llm_chat)

    # Generate resolution plans per operation type (handles mixed CREATE+UPDATE)
    resolution_plans = {}  # keyed by operation type
    if data_rows:
        for row in data_rows:
            op = (row.get("Operation") or "CREATE").upper()
            if op not in resolution_plans:
                try:
                    ctx = _detect_context(row)
                except ValidationError:
                    ctx = "job"  # default
                logger.info(f"🧠 Generating LLM resolution plan for {op}/{ctx}...")
                resolution_plans[op] = await _generate_resolution_plan(
                    operation=op,
                    context=ctx,
                    sample_row=row,
                    llm_chat=llm_chat,
                    mcp_executor=mcp_executor,
                    sop_text=sop_text,
                )
        logger.info(f"✅ Resolution plans ready for: {list(resolution_plans.keys())}")
    _agent_state.complete_phase("plan", detail=f"ops={list(resolution_plans.keys())}")

    # ═══════════════════════════════════════════════════════════════════════
    # STEP 1: Validate Operation Column
    # ═══════════════════════════════════════════════════════════════════════

    if "Operation" not in headers:
        # Try to infer operation from hints (intent analyzer) or user text
        inferred_op = _infer_operation(hints, user_text)
        if inferred_op:
            logger.info(f"🎯 Inferred operation '{inferred_op}' — adding Operation column")
            # Add Operation column to headers and every row
            headers.append("Operation")
            table["headers"] = headers
            for i, row_data in enumerate(rows):
                row_data.append(inferred_op)
            # Also update data_rows dicts
            for row_dict in data_rows:
                row_dict["Operation"] = inferred_op

            # Special handling for lock/unlock: set IsLocked on all rows
            action = (hints or {}).get("action", "")
            if action == "lock":
                if "IsLocked" not in headers:
                    headers.append("IsLocked")
                    table["headers"] = headers
                    for row_data in rows:
                        row_data.append("true")
                for row_dict in data_rows:
                    row_dict["IsLocked"] = "true"
                logger.info("🔒 Lock action: set IsLocked=true on all rows")
            elif action == "unlock":
                if "IsLocked" not in headers:
                    headers.append("IsLocked")
                    table["headers"] = headers
                    for row_data in rows:
                        row_data.append("false")
                for row_dict in data_rows:
                    row_dict["IsLocked"] = "false"
                logger.info("🔓 Unlock action: set IsLocked=false on all rows")
        else:
            return {
                "success": False,
                "error": "MISSING_OPERATION",
                "message": (
                    "Could not determine the action to perform. "
                    "Please specify what you'd like to do (e.g., 'create these schedules', "
                    "'delete these schedules', 'update these schedules') or add an 'Operation' "
                    "column to your Excel file with CREATE/UPDATE/DELETE values."
                ),
            }

    # Check for empty operations
    empty_operations = []
    invalid_operations = []

    for idx, row in enumerate(data_rows, start=2):  # Row 2 = first data row (after header)
        op = (row.get("Operation") or "").strip().upper()

        if not op:
            empty_operations.append(idx)
        elif op not in ["CREATE", "UPDATE", "DELETE"]:
            invalid_operations.append({"row": idx, "value": op})

    if empty_operations or invalid_operations:
        error_details = []
        if empty_operations:
            error_details.append(f"Rows with empty Operation: {empty_operations}")
        if invalid_operations:
            invalid_str = ", ".join([f"Row {item['row']}: '{item['value']}'" for item in invalid_operations])
            error_details.append(f"Rows with invalid Operation: {invalid_str}")

        return {
            "success": False,
            "error": "INVALID_OPERATIONS",
            "message": "Some rows have missing or invalid Operation values. Must be CREATE, UPDATE, or DELETE.",
            "details": error_details
        }

    # ═══════════════════════════════════════════════════════════════════════
    # STEP 2: Resolve Identifiers for All Rows
    # ═══════════════════════════════════════════════════════════════════════

    # Fetch full tool catalog once for crossroads resolution context
    try:
        tool_catalog = await mcp_executor.get_tool_catalog()
        logger.info(f"🔧 Loaded {len(tool_catalog)} tools with full schemas for resolution context")
    except Exception as e:
        logger.warning(f"Could not load tool catalog: {e}")
        tool_catalog = {}

    def _build_row_context(row: Dict[str, Any]) -> Dict[str, str]:
        """Extract human-readable context from a row for clarification display."""
        ctx = {}
        if row.get("StaffName"):
            ctx["staff"] = row["StaffName"]
        if row.get("JobID"):
            ctx["job"] = f"Job {row['JobID']}"
        elif row.get("JobName"):
            ctx["job"] = row["JobName"]
        elif row.get("SiteName"):
            ctx["site"] = row["SiteName"]
        if row.get("Date"):
            ctx["date"] = str(row["Date"])
        if row.get("SectionName"):
            ctx["section"] = row["SectionName"]
        if row.get("CostCentreName"):
            ctx["cost_centre"] = row["CostCentreName"]
        return ctx

    def _error_to_clarification(
        error: Exception,
        row_idx: int,
        row_op: str,
        row_context: Dict[str, str],
    ) -> Optional[Dict[str, Any]]:
        """Convert a resolution exception into a clarification dict, or None for hard errors."""
        if isinstance(error, AmbiguousResolutionError):
            return {
                "row": row_idx,
                "type": "ambiguous",
                "field": error.field,
                "value": error.value,
                "options": error.matches,
                "message": error.message,
                "operation": row_op,
                "row_context": row_context,
            }
        elif isinstance(error, MissingFieldError):
            options = error.context.get("options", [])
            is_free_text = error.context.get("free_text", False)
            if is_free_text:
                return {
                    "row": row_idx,
                    "type": "free_text",
                    "field": error.field,
                    "message": error.message,
                    "placeholder": error.context.get("placeholder", error.field),
                    "options": [],
                    "context_data": error.context,
                    "operation": row_op,
                    "row_context": row_context,
                }
            elif options:
                is_multi = error.context.get("multi_select", False)
                return {
                    "row": row_idx,
                    "type": "multi_select" if is_multi else "missing",
                    "field": error.field,
                    "message": error.message,
                    "options": options,
                    "context_data": error.context,
                    "operation": row_op,
                    "row_context": row_context,
                }
            else:
                # Entity fields without options (e.g., StaffName not specified) —
                # treat as free-text so the user can provide the value and the
                # system stores a pending clarification session for follow-up.
                _FIELD_PLACEHOLDERS = {
                    "StaffName": "e.g. John Smith",
                    "JobID": "e.g. 22601",
                    "CostCentreID": "e.g. 154740",
                    "SiteName": "e.g. 123 Main St",
                }
                return {
                    "row": row_idx,
                    "type": "free_text",
                    "field": error.field,
                    "message": error.message,
                    "placeholder": _FIELD_PLACEHOLDERS.get(error.field, error.field),
                    "options": [],
                    "context_data": error.context,
                    "operation": row_op,
                    "row_context": row_context,
                }
        return None  # Hard error — caller adds to resolution_errors

    def _check_required_user_fields(row: Dict[str, Any], operation: str) -> list:
        """Check for required user-input fields that are independent of entity resolution.

        These are fields the user must provide (no API lookup can resolve them):
        - CREATE: StartTime, Blocks, Date
        - UPDATE/DELETE: Date (unless ScheduleID is already known)

        Returns a list of MissingFieldError for each missing required field.
        """
        _FIELD_SPECS = {
            "StartTime": ("What time should this schedule start?", "e.g. 08:00, 14:00"),
            "Blocks":    ("How many hours should this schedule be?", "e.g. 4, 8, 1.5"),
            "Date":      ("What date should this schedule be for?", "e.g. 2026-02-15, tomorrow"),
        }
        missing = []
        op = operation.upper()
        if op == "CREATE":
            for field in ("StartTime", "Blocks", "Date"):
                if not row.get(field):
                    msg, placeholder = _FIELD_SPECS[field]
                    missing.append(MissingFieldError(
                        field, msg, free_text=True, placeholder=placeholder,
                    ))
        elif op in ("UPDATE", "DELETE"):
            if not row.get("Date") and not row.get("ScheduleID"):
                msg, placeholder = _FIELD_SPECS["Date"]
                missing.append(MissingFieldError(
                    field="Date", message=msg, free_text=True, placeholder=placeholder,
                ))
        return missing

    resolved_rows = []
    clarifications = []
    resolution_errors = []

    # ── Parallel row processing ──
    # Process rows concurrently with a semaphore to limit parallelism.
    # This is the single biggest performance win: 50 rows × 6s each
    # goes from ~300s sequential to ~40s with 8 concurrent.
    import asyncio
    _ROW_CONCURRENCY = 8
    _row_semaphore = asyncio.Semaphore(_ROW_CONCURRENCY)

    async def _process_single_row(idx: int, row: Dict[str, Any], _state: Optional[AgentExecutionState] = None):
        """Process one row: resolve entities + assemble fields.

        Returns a tuple of (resolved_row_or_None, row_clarifications, row_errors).
        """
        # ── Skip handling: user requested to skip this row in clarification ──
        if row.get("__skip__"):
            logger.info(f"🚫 Row {idx}: Skipped by user request")
            if _state:
                _state.set_row_outcome(idx, "skipped")
            return (None, [], [])

        row_clars = []
        row_errs = []
        row_op = (row.get("Operation") or "CREATE").upper()

        async with _row_semaphore:
            if _state:
                _state.enter_phase("resolve_row", row_num=idx)
            try:
                # Detect context (job vs quote)
                context = _detect_context(row)

                resolution_plan = resolution_plans.get(row_op, {})

                # ── Pre-check: detect missing user-input fields (independent of entities) ──
                field_errors = _check_required_user_fields(row, row_op)

                # ── Entity resolution (run even if field_errors exist to batch ALL errors) ──
                entity_errors: list = []
                resolved = None
                try:
                    resolved = await _resolve_with_recovery(
                        row, idx, mcp_executor, context, resolution_plan,
                        llm_chat=llm_chat, tool_descriptions=tool_catalog,
                        tracker=mcp_executor.tracker,
                        hints=hints,
                        shared_state=_state,
                    )
                except (AmbiguousResolutionError, MissingFieldError) as e:
                    entity_errors = [e]
                    if _state:
                        _state.set_row_outcome(idx, "clarification")
                except BatchedClarificationError as batch_e:
                    entity_errors = list(batch_e.errors)
                    if _state:
                        _state.set_row_outcome(idx, "clarification")

                # ── Handle DELETE-all expansion ──
                # When _lookup_schedule_by_staff_date detects DELETE with multiple
                # schedules and no specific job/CC, it returns _expand_all marker.
                # Expand into one resolved row per schedule (like _expand_bulk_action).
                if resolved and "_expand_all" in resolved:
                    expand_schedules = resolved.pop("_expand_all")
                    expanded_rows = []
                    resolver = EntityResolver(mcp_executor, llm_chat=llm_chat)
                    for sched in expand_schedules:
                        stype = (sched.get("Type") or "").lower()
                        ref = sched.get("Reference", "")
                        blocks_arr = sched.get("Blocks", [])
                        exp = {
                            "row_number": idx,
                            "operation": "DELETE",
                            "context": context,
                            "schedule_id": sched.get("ID"),
                            "staff_id": sched.get("Staff", {}).get("ID"),
                            "date": row.get("Date"),
                            "blocks": float(blocks_arr[0].get("Hrs", 0)) if blocks_arr else None,
                            "start_time": blocks_arr[0].get("StartTime") if blocks_arr else None,
                            "notes": sched.get("Notes") or "",
                            "is_locked": sched.get("IsLocked"),
                            "staff_name": (sched.get("Staff", {}).get("Name") or row.get("StaffName", "")),
                        }
                        if stype == "job" and ref and "-" in ref:
                            parts = ref.split("-")
                            exp["job_id"] = int(parts[0])
                            exp["cost_centre_id"] = int(parts[1]) if len(parts) > 1 else None
                            # Resolve section_id (required by delete API)
                            if exp.get("job_id") and exp.get("cost_centre_id"):
                                try:
                                    sec_id = await resolver.find_section_for_cost_centre(
                                        job_id=exp["job_id"],
                                        cost_centre_id=exp["cost_centre_id"],
                                        context="job",
                                        row_num=idx,
                                    )
                                    if sec_id is not None:
                                        exp["section_id"] = sec_id
                                except Exception as e:
                                    logger.warning(f"Row {idx}: Section lookup failed for schedule {sched.get('ID')}: {e}")
                        expanded_rows.append(exp)
                    logger.info(f"Row {idx}: Expanded DELETE-all into {len(expanded_rows)} individual rows")
                    return (expanded_rows, row_clars, row_errs)

                # ── Combine all independent errors into one round ──
                all_errors = field_errors + entity_errors
                if all_errors:
                    if len(all_errors) == 1:
                        raise all_errors[0]
                    raise BatchedClarificationError(
                        errors=all_errors,
                        partial_resolved=resolved or {},
                    )

                # Add other fields
                operation = row["Operation"].upper()

                # ── Field assembly: deterministic (no LLM needed) ──
                from utils.schedule_field_assembler import schedule_field_assembly_fallback
                crossroads_result = schedule_field_assembly_fallback("field_assembly", {
                    "operation": operation,
                    "row_data": {k: row.get(k, "") for k in [
                        "StartTime", "Blocks", "Date", "Notes", "IsLocked", "BlocksAdjust", "EndTime"
                    ]},
                    "resolved_data": {
                        "schedule_id": resolved.get("schedule_id"),
                        "existing_start_time": resolved.get("existing_start_time"),
                        "existing_blocks": resolved.get("existing_blocks"),
                        "existing_notes": resolved.get("existing_notes"),
                        "existing_date": resolved.get("existing_date"),
                    },
                })

                logger.info(f"Row {idx}: 🔀 FieldAssembly [{operation}]: {crossroads_result.get('decision')} "
                            f"(confidence={crossroads_result.get('confidence', 0):.2f}, "
                            f"reason={crossroads_result.get('reasoning', '')[:60]})")

                # Check for errors from field assembly — add ALL as free-text clarifications
                if crossroads_result.get("errors"):
                    _FIELD_PLACEHOLDERS = {
                        "Blocks": "e.g. 4, 8, 1.5",
                        "StartTime": "e.g. 08:00, 14:00",
                        "Date": "e.g. 2026-02-14, tomorrow",
                    }
                    for cr_err in crossroads_result["errors"]:
                        row_clars.append({
                            "row": idx,
                            "type": "free_text",
                            "field": cr_err["field"],
                            "message": cr_err["message"],
                            "placeholder": _FIELD_PLACEHOLDERS.get(cr_err["field"], cr_err["field"]),
                            "options": [],
                            "context_data": {},
                            "operation": row_op,
                            "row_context": _build_row_context(row),
                        })
                        logger.warning(f"❓ Row {idx}: Free-text input needed - {cr_err['field']}: {cr_err['message']}")
                    return (None, row_clars, row_errs)

                cr = crossroads_result["fields"]

                # Normalize date
                date_val = cr.get("date") or row.get("Date")
                if date_val:
                    normalized_date = _normalize_date(date_val)
                else:
                    normalized_date = resolved.get("existing_date")

                # Parse blocks
                blocks_val = cr.get("blocks")
                if blocks_val is not None:
                    try:
                        blocks_val = float(blocks_val)
                    except (ValueError, TypeError):
                        blocks_val = None

                resolved.update({
                    "operation": operation,
                    "context": context,
                    "date": normalized_date,
                    "blocks": blocks_val,
                    "start_time": cr.get("start_time"),
                    "notes": cr.get("notes") or "",
                    "is_locked": cr.get("is_locked"),
                    "schedule_id": resolved.get("schedule_id") or (int(row["ScheduleID"]) if row.get("ScheduleID") else None),
                    "staff_name": row.get("StaffName", ""),
                })

                # Check if user wants to change the date (from metadata or per-row key)
                metadata = extracted.get("metadata", {})
                new_date_val = row.get("__new_date__") or metadata.get("new_date")
                if new_date_val and resolved["operation"] == "UPDATE":
                    resolved["date"] = new_date_val
                    logger.info(f"Row {idx}: Moving schedule to new date: {resolved['date']}")

                # Check if user wants to reassign to a different staff member (per-row key takes priority)
                new_staff_val = row.get("__new_staff_name__") or metadata.get("new_staff_name")
                if new_staff_val and resolved["operation"] == "UPDATE":
                    new_name = new_staff_val

                    # If the value is already a resolved staff ID (numeric — written back
                    # by the clarification merge after the user picks from the dropdown),
                    # use it directly and skip re-resolution to prevent infinite loop.
                    try:
                        pre_resolved_id = int(new_name)
                        resolved["staff_id"] = pre_resolved_id
                        logger.info(f"Row {idx}: New staff already resolved to ID={pre_resolved_id} (from clarification)")
                    except (ValueError, TypeError):
                        # It's a name — resolve it now.
                        logger.info(f"Row {idx}: Reassigning schedule to '{new_name}' — resolving staff ID...")
                        temp_row = {"StaffName": new_name, "StaffID": ""}
                        temp_resolved = {}
                        resolver = FieldResolver(context, mcp_executor, resolution_plan, llm_chat=llm_chat)
                        try:
                            temp_resolved = await resolver._resolve_staff(temp_row, idx, temp_resolved)
                        except AmbiguousResolutionError as amb_err:
                            # Re-raise with field="__new_staff_name__" so chat.py writes
                            # the user's selected ID back into __new_staff_name__.
                            # On the next re-run the int-check above short-circuits resolution.
                            raise AmbiguousResolutionError(
                                field="__new_staff_name__",
                                value=amb_err.value,
                                matches=amb_err.matches,
                                message=f"Row {idx}: Multiple staff match '{new_name}'. Which one do you want to reassign to?",
                            )
                        resolved["staff_id"] = temp_resolved["staff_id"]
                        logger.info(f"Row {idx}: Reassigned to staff_id={resolved['staff_id']}")

                    resolved["staff_name"] = new_name

                # ── Locked schedule detection ──
                # Defer to SOP flag — if SOP says auto-unlock, proceed without prompt.
                # Otherwise (SOP silent or says require approval) always ask user first.
                if (resolved.get("operation") == "DELETE"
                        and resolved.get("existing_is_locked")):
                    if not sop_flags.get("require_unlock_approval", True):
                        resolved["unlock_before_delete"] = True
                        logger.info(f"🔓 Row {idx}: Auto-unlock+delete (SOP: approval not required)")
                    else:
                        user_confirmed_unlock = str(row.get("unlock_before_delete", "")).lower()
                        if user_confirmed_unlock == "yes":
                            resolved["unlock_before_delete"] = True
                            logger.info(f"🔓 Row {idx}: User confirmed unlock+delete")
                        elif user_confirmed_unlock == "no":
                            logger.info(f"🔒 Row {idx}: User declined unlock — skipping delete")
                            return (None, row_clars, row_errs)
                        else:
                            staff_name = resolved.get("staff_name", f"Staff ID {resolved.get('staff_id', '?')}")
                            sched_date = resolved.get("date") or resolved.get("existing_date", "")
                            row_clars.append({
                                "row": idx,
                                "type": "confirmation",
                                "field": "unlock_before_delete",
                                "message": (
                                    f"The schedule for {staff_name} on {sched_date} is locked. "
                                    f"Do you want to unlock it first and then delete?"
                                ),
                                "options": [
                                    {"id": "yes", "name": "Yes, unlock and delete"},
                                    {"id": "no",  "name": "No, keep the schedule"},
                                ],
                                "context_data": {
                                    "schedule_id": resolved.get("schedule_id"),
                                    "staff_name": staff_name,
                                    "date": sched_date,
                                },
                                "operation": row_op,
                                "row_context": _build_row_context(row),
                            })
                            resolved["_pending_confirmation"] = True
                            logger.warning(f"🔒 Row {idx}: Schedule is locked — asking user to confirm unlock+delete")
                            return (resolved, row_clars, row_errs)

                # ── DELETE confirmation gate ──
                # Defer to SOP flag — if SOP explicitly says to skip confirmation, bypass.
                # Otherwise (SOP silent or says confirm) always prompt before DELETE.
                if resolved.get("operation") == "DELETE" and sop_flags.get("require_delete_confirmation", True):
                    user_confirmed_delete = str(row.get("confirm_delete", "")).lower()
                    if user_confirmed_delete == "yes":
                        logger.info(f"✅ Row {idx}: User confirmed delete")
                    elif user_confirmed_delete == "no":
                        logger.info(f"🚫 Row {idx}: User declined delete — skipping")
                        return (None, row_clars, row_errs)
                    else:
                        staff_name = resolved.get("staff_name", f"Staff ID {resolved.get('staff_id', '?')}")
                        sched_date = resolved.get("date") or resolved.get("existing_date", "")
                        job_id = resolved.get("job_id", "?")
                        blocks = resolved.get("existing_blocks") or resolved.get("blocks", "?")
                        start_time = resolved.get("existing_start_time") or resolved.get("start_time", "")
                        schedule_id = resolved.get("schedule_id", "?")
                        detail_parts = [f"Staff: {staff_name}", f"Date: {sched_date}", f"Job ID: {job_id}"]
                        if blocks and blocks != "?":
                            detail_parts.append(f"Hours: {blocks}")
                        if start_time:
                            detail_parts.append(f"Start: {start_time}")
                        detail_str = ", ".join(detail_parts)
                        row_clars.append({
                            "row": idx,
                            "type": "confirmation",
                            "field": "confirm_delete",
                            "message": (
                                f"Are you sure you want to delete this schedule?\n"
                                f"{detail_str} (Schedule ID: {schedule_id})"
                            ),
                            "options": [
                                {"id": "yes", "name": "Yes, delete this schedule"},
                                {"id": "no",  "name": "No, keep the schedule"},
                            ],
                            "context_data": {
                                "schedule_id": schedule_id,
                                "staff_name": staff_name,
                                "date": sched_date,
                                "job_id": job_id,
                            },
                            "operation": row_op,
                            "row_context": _build_row_context(row),
                        })
                        resolved["_pending_confirmation"] = True
                        logger.info(f"⚠️ Row {idx}: DELETE confirmation requested — {detail_str}")
                        return (resolved, row_clars, row_errs)

                logger.info(f"✅ Row {idx}: Resolved successfully")
                if _state:
                    _state.complete_phase("resolve_row", row_num=idx)
                    _state.set_row_outcome(idx, "resolved")
                return (resolved, row_clars, row_errs)

            except MissingFieldError as e:
                clar = _error_to_clarification(e, idx, row_op, _build_row_context(row))
                if clar:
                    row_clars.append(clar)
                    logger.warning(f"❓ Row {idx}: {clar['type']} - {e.field}: {e.message}")
                else:
                    row_errs.append({"row": idx, "error": e.message, "row_context": _build_row_context(row)})
                    logger.warning(f"❌ Row {idx}: Missing field with no options - {e.field}: {e.message}")

            except AmbiguousResolutionError as e:
                clar = _error_to_clarification(e, idx, row_op, _build_row_context(row))
                if clar:
                    row_clars.append(clar)
                logger.warning(f"❓ Row {idx}: Ambiguous match - {e.field}='{e.value}'")

            except BatchedClarificationError as batch_e:
                row_ctx = _build_row_context(row)
                for inner_error in batch_e.errors:
                    clar = _error_to_clarification(inner_error, idx, row_op, row_ctx)
                    if clar:
                        row_clars.append(clar)
                    else:
                        row_errs.append({
                            "row": idx,
                            "error": getattr(inner_error, "message", str(inner_error)),
                            "row_context": _build_row_context(row),
                        })
                logger.warning(
                    f"❓ Row {idx}: Batched {len(batch_e.errors)} clarifications "
                    f"(fields: {', '.join(getattr(e, 'field', '?') for e in batch_e.errors)})"
                )

            except (ResolutionError, ValidationError) as e:
                row_errs.append({
                    "row": idx,
                    "error": str(e),
                    "row_context": _build_row_context(row),
                })
                logger.error(f"❌ Row {idx}: {e}")

        return (None, row_clars, row_errs)

    _agent_state.enter_phase("resolve")
    # Launch all rows concurrently (semaphore limits to _ROW_CONCURRENCY at a time)
    row_tasks = [
        _process_single_row(idx, row, _agent_state)
        for idx, row in enumerate(data_rows, start=2)
    ]
    row_results = await asyncio.gather(*row_tasks)

    # Collect results in original row order
    for resolved_row, row_clars, row_errs in row_results:
        if resolved_row is not None:
            # Handle DELETE-all expansion: _process_single_row may return a list
            if isinstance(resolved_row, list):
                resolved_rows.extend(resolved_row)
            else:
                resolved_rows.append(resolved_row)
        clarifications.extend(row_clars)
        resolution_errors.extend(row_errs)

    # ═══════════════════════════════════════════════════════════════════════
    # STEP 3: Check Threshold & Return Result
    # ═══════════════════════════════════════════════════════════════════════

    total_issues = len(clarifications) + len(resolution_errors)

    if resolution_errors:
        # Hard errors — use crossroads error_recovery for user-friendly messages
        friendly_errors = []
        for err in resolution_errors:
            raw = err.get("error", "")
            friendly = raw  # Default: raw error

            if llm_chat:
                try:
                    cr_result = await resolve_crossroads(
                        crossroad_type="error_recovery",
                        question=f"Schedule resolution error: {raw}",
                        context={
                            "raw_error": raw,
                            "row": err.get("row"),
                            "operation": "schedule_resolution",
                            "system": "Simpro ERP construction back-office",
                        },
                        llm_chat=llm_chat,
                    )
                    if cr_result.get("fields", {}).get("message"):
                        friendly = cr_result["fields"]["message"]
                        logger.info(f"🔀 Crossroads error_recovery: '{raw[:40]}' → '{friendly[:40]}'")
                    _agent_state.log_crossroads(
                        "error_recovery", raw[:100],
                        outcome=cr_result.get("decision", "unknown"),
                        row_num=err.get("row"),
                    )
                except Exception as e:
                    logger.warning(f"Error recovery crossroads failed ({e}), using raw error")
                    # Fallback to pattern matching
                    friendly = _friendly_error_fallback(raw)
            else:
                friendly = _friendly_error_fallback(raw)

            friendly_errors.append({
                "row": err["row"],
                "error": raw,
                "friendly": friendly,
                "row_context": err.get("row_context", {}),
            })

        # If some rows resolved successfully, proceed with partial execution
        # instead of blocking the entire batch.
        executable_rows = [r for r in resolved_rows if not r.get("_pending_confirmation")]
        if executable_rows:
            logger.info(f"⚠️ Partial success: {len(executable_rows)} resolved, {len(resolution_errors)} errors — proceeding with resolved rows")
            logger.info(_agent_state.summary())
            return {
                "success": True,
                "partial": True,
                "schedules": executable_rows,
                "total_count": len(executable_rows),
                "errors": friendly_errors,
                "message": f"{len(executable_rows)} of {len(data_rows)} schedules processed. {len(resolution_errors)} failed.",
                "trace": {
                    "agent": "schedule",
                    "version": "1.0",
                    "resolved_count": len(resolved_rows),
                    "error_count": len(resolution_errors),
                }
            }

        logger.info(_agent_state.summary())
        return {
            "success": False,
            "error": "RESOLUTION_ERRORS",
            "message": "Some issues need to be fixed before schedules can be processed.",
            "errors": friendly_errors,
            "resolved_count": len(resolved_rows),
            "total_count": len(data_rows),
            "original_extracted": extracted,
        }

    if clarifications:
        session_id = f"sched_{uuid.uuid4().hex[:12]}"
        clarification_count = len(clarifications)

        if clarification_count <= MAX_INTERACTIVE_CLARIFICATIONS:
            # Interactive UI mode
            logger.info(f"📝 {clarification_count} clarifications needed - Interactive mode")
            logger.info(_agent_state.summary())
            return {
                "success": False,
                "needs_clarification": True,
                "clarification_mode": "interactive",
                "clarification_count": clarification_count,
                "clarifications": clarifications,
                "session_id": session_id,
                "resolved_count": len(resolved_rows),
                "total_count": len(data_rows),
                "original_extracted": extracted,
            }
        else:
            # Pre-filled Excel mode
            logger.info(f"📄 {clarification_count} clarifications needed - File download mode")
            logger.info(_agent_state.summary())
            return {
                "success": False,
                "needs_clarification": True,
                "clarification_mode": "file_download",
                "clarification_count": clarification_count,
                "message": f"{clarification_count} issues found. Download corrected template to fix.",
                "session_id": session_id,
                "clarifications": clarifications,
                "resolved_count": len(resolved_rows),
                "total_count": len(data_rows),
                "original_extracted": extracted,
                "errors_summary": {
                    "missing_fields": len([c for c in clarifications if c["type"] == "missing"]),
                    "ambiguous_matches": len([c for c in clarifications if c["type"] == "ambiguous"])
                }
            }

    # Filter out rows pending confirmation (they were kept for tracking only)
    executable_rows = [r for r in resolved_rows if not r.get("_pending_confirmation")]

    if not executable_rows and not clarifications and not resolution_errors:
        return {
            "success": True,
            "message": "No schedules to process (all were skipped).",
            "schedules": [],
            "total_count": 0,
        }

    # Success! All rows resolved
    logger.info(f"✅ All {len(executable_rows)} rows resolved successfully")
    logger.info(_agent_state.summary())
    return {
        "success": True,
        "schedules": executable_rows,
        "total_count": len(executable_rows),
        "trace": {
            "agent": "schedule",
            "version": "1.0",
            "resolved_count": len(resolved_rows)
        }
    }
