# Chatbox_mcp/backend/tools/schedule_executor.py
"""
Schedule Executor - Executes schedule operations via MCP Server HTTP API.

Takes validated schedule data from the agent and calls MCP tools
via HTTP to create/update/delete schedules in Simpro.
"""

from typing import Dict, Any, List, Optional, Callable
import logging

from utils.mcp_tool_client import get_mcp_tool_client
from utils.crossroads import resolve_crossroads

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# ScheduleRate Configuration (from SOP)
# ═══════════════════════════════════════════════════════════════════════════

# Default schedule rate ID per SOP v1.3
# Override this value in SOP when multiple rate logic is needed
DEFAULT_SCHEDULE_RATE_ID = 1


def _get_schedule_rate(schedule: Dict[str, Any]) -> int:
    """
    Determine the appropriate ScheduleRate ID for a schedule.

    This function implements the rate selection logic defined in the SOP.
    Currently uses default rate; can be extended with conditional logic.

    Args:
        schedule: Schedule data with date, start_time, staff_id, etc.

    Returns:
        ScheduleRate ID (integer)

    Future enhancements (per SOP):
    - Time-based rates (overtime after 5 PM, night shift, etc.)
    - Day-based rates (weekend, holiday)
    - Staff-specific rates
    - Cost-centre-specific rates

    Example conditional logic:
        from datetime import datetime

        # Parse start time
        start_time = schedule.get("start_time", "09:00")
        start_hour = int(start_time.split(":")[0])

        # Evening/overtime rate (after 5 PM)
        if start_hour >= 17:
            return 2  # Overtime rate ID

        # Weekend rate
        date_str = schedule.get("date")
        if date_str:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            if date_obj.weekday() >= 5:  # Saturday=5, Sunday=6
                return 3  # Weekend rate ID

        # Default standard rate
        return DEFAULT_SCHEDULE_RATE_ID
    """

    # Currently: Always use default rate per SOP
    # Uncomment and modify logic above when multiple rates are needed
    return DEFAULT_SCHEDULE_RATE_ID


# ═══════════════════════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════════════════════

def _round_to_15min(minutes: int) -> int:
    """
    Round minutes to nearest 15-minute interval.

    Simpro accepts schedules in 15-minute blocks only.

    Examples:
        0-7 → 0
        8-22 → 15
        23-37 → 30
        38-52 → 45
        53-59 → 0 (next hour)
    """
    # Round to nearest 15 minutes
    rounded = round(minutes / 15) * 15
    return rounded % 60


def _convert_blocks_to_time_array(
    blocks: int,
    start_time: str,
    schedule_rate: int = DEFAULT_SCHEDULE_RATE_ID
) -> List[Dict[str, Any]]:
    """
    Convert blocks integer to Simpro time block array.

    IMPORTANT: Simpro accepts schedules in 15-minute intervals only.
    Times are automatically rounded to nearest 15-minute mark:
    - 9:01 → 9:00
    - 9:08 → 9:15
    - 9:14 → 9:15
    - 9:23 → 9:30

    Args:
        blocks: Number of hours (e.g., 1, 2, 4, 8)
        start_time: Start time in HH:MM format (e.g., "09:00", "14:00") - REQUIRED
        schedule_rate: Schedule rate ID (default from SOP)

    Returns:
        List of time block objects with StartTime, EndTime, ScheduleRate

    Raises:
        ValueError: If start_time is missing or invalid

    Example:
        _convert_blocks_to_time_array(1, "09:00", 1)
        -> [{"StartTime": "9:00", "EndTime": "10:00", "ScheduleRate": 1}]

        _convert_blocks_to_time_array(1, "09:08", 2)
        -> [{"StartTime": "9:15", "EndTime": "10:15", "ScheduleRate": 2}]
    """
    if not start_time:
        raise ValueError("start_time is required - please specify schedule start time")

    try:
        # Normalize common time formats to HH:MM
        # Handles: "7", "07", "7am", "7:00am", "7:30pm", "14", "14:30", etc.
        t = str(start_time).strip().lower().replace(" ", "")
        is_pm = "pm" in t
        is_am = "am" in t
        t = t.replace("am", "").replace("pm", "")

        if ":" in t:
            parts = t.split(":")
            hour, minute = int(parts[0]), int(parts[1])
        else:
            hour, minute = int(t), 0

        if is_pm and hour < 12:
            hour += 12
        elif is_am and hour == 12:
            hour = 0

        start_time = f"{hour}:{minute:02d}"

        # Parse start time
        start_hour, start_min = hour, minute

        # Validate time range
        if start_hour < 0 or start_hour > 23:
            raise ValueError(f"Invalid hour '{start_hour}' in start_time '{start_time}'. Hours must be 0-23.")
        if start_min < 0 or start_min > 59:
            raise ValueError(f"Invalid minutes '{start_min}' in start_time '{start_time}'. Minutes must be 0-59.")

        # Round minutes to nearest 15-minute interval
        rounded_start_min = _round_to_15min(start_min)

        # Handle minute overflow (e.g., 53 minutes -> 0 minutes next hour)
        if rounded_start_min == 0 and start_min > 52:
            start_hour += 1
            rounded_start_min = 0

        # Log if rounding occurred
        if rounded_start_min != start_min:
            logger.info(f"Rounded start time {start_hour}:{start_min:02d} -> {start_hour}:{rounded_start_min:02d} (Simpro 15-min intervals)")

        # Calculate end time (start + blocks hours)
        total_minutes = int(blocks * 60)

        # Calculate total minutes from midnight for start time
        start_total_mins = start_hour * 60 + rounded_start_min

        # Add duration
        end_total_mins = start_total_mins + total_minutes

        # Convert back to hours and minutes
        end_hour = end_total_mins // 60
        end_min = end_total_mins % 60

        # Round end time to nearest 15-minute interval
        end_min = _round_to_15min(end_min)

        # Handle minute overflow from rounding (e.g., 59 min -> 0 min next hour)
        if end_min == 0 and (end_total_mins % 60) > 52:
            end_hour += 1

        # Handle day overflow (e.g., 22:00 + 4 hours = 02:00 next day)
        if end_hour >= 24:
            end_hour = end_hour % 24

        # Format times in H:i format (hours without leading zero, minutes with leading zero)
        # Simpro API requires "9:00" not "09:00"
        start_formatted = f"{start_hour}:{rounded_start_min:02d}"
        end_formatted = f"{end_hour}:{end_min:02d}"

        return [
            {
                "StartTime": start_formatted,
                "EndTime": end_formatted,
                "ScheduleRate": schedule_rate
            }
        ]
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Invalid start_time format '{start_time}': {e}. Expected HH:MM format (e.g., '09:00', '14:30')")


# ═══════════════════════════════════════════════════════════════════════════
# Tool Name Mapping
# ═══════════════════════════════════════════════════════════════════════════

# Maps (context, operation) to MCP tool name
_TOOL_NAME_MAP = {
    ("job", "create"): "create_job_cost_centre_schedule",
    ("job", "update"): "update_job_cost_centre_schedule",
    ("job", "delete"): "delete_job_cost_centre_schedule",
    ("quote", "create"): "create_quote_cost_centre_schedule",
    ("quote", "update"): "update_quote_cost_centre_schedule",
    ("quote", "delete"): "delete_quote_cost_centre_schedule",
}


def is_tool_available() -> bool:
    """MCP tools are always available via HTTP - checked at call time."""
    return True


def _translate_simpro_error(raw_error: str, schedule: Dict[str, Any]) -> str:
    """Translate raw Simpro API errors into user-friendly messages."""
    error_lower = (raw_error or "").lower()

    if "no sections" in error_lower or ("section" in error_lower and "not found" in error_lower):
        job_id = schedule.get("job_id", "?")
        return (f"Job {job_id} doesn't have the required sections set up in Simpro. "
                "The job may be open or pending. Please configure sections and cost centres first.")
    if "staff" in error_lower and ("not found" in error_lower or "invalid" in error_lower):
        return f"Staff member (ID: {schedule.get('staff_id', '?')}) was not found in Simpro."
    if "date" in error_lower and "invalid" in error_lower:
        return f"The date '{schedule.get('date', '?')}' is not valid for scheduling."
    if "locked" in error_lower:
        return "This schedule is locked and cannot be modified. Would you like me to unlock it first and try again?"
    if "currently being edited" in error_lower:
        return "Cannot modify this schedule because the job is currently being edited by another user. Please close the job in Simpro and try again."
    if "duplicate" in error_lower or "already exists" in error_lower:
        # Extract existing schedule ID if Simpro provided it
        import re as _re
        match = _re.search(r"schedule\s+(\d+)", raw_error, _re.IGNORECASE)
        existing_id = match.group(1) if match else "unknown"
        return (
            f"A schedule already exists for this combination (ID: {existing_id}). "
            f"You can create another schedule with a different time slot."
        )
    if "cost centre" in error_lower and "not found" in error_lower:
        return f"Cost centre {schedule.get('cost_centre_id', '?')} was not found in this job."

    return raw_error


# ═══════════════════════════════════════════════════════════════════════════
# Main Executor Function
# ═══════════════════════════════════════════════════════════════════════════

async def execute_schedule_operations(
    agent_result: Dict[str, Any],
    company_id: int,
    llm_chat: Optional[Callable] = None
) -> Dict[str, Any]:
    """
    Execute schedule create/update/delete operations from agent result.

    Args:
        agent_result: Result from schedule agent with "schedules" list
        company_id: Simpro company ID

    Returns:
        {
            "success": bool,
            "summary": {"total": int, "succeeded": int, "failed": int},
            "created": [...],  # or "updated" or "deleted"
            "failed": [{"row": int, "error": str}]
        }
    """

    schedules = agent_result.get("schedules", [])
    if not schedules:
        return {
            "success": False,
            "error": "NO_SCHEDULES",
            "message": "No schedules to process"
        }

    logger.info("=" * 70)
    logger.info(f"🚀 Schedule Executor: Processing {len(schedules)} schedules")
    logger.info("=" * 70)

    mcp_client = get_mcp_tool_client()

    # Group schedules by operation and context
    by_operation = {}
    for schedule in schedules:
        op = schedule.get("operation", "CREATE")
        context = schedule.get("context", "job")
        key = f"{context}_{op}"

        if key not in by_operation:
            by_operation[key] = []
        by_operation[key].append(schedule)

    # Execute each group
    all_succeeded = []
    all_warnings = []
    all_failed = []

    for key, group in by_operation.items():
        context, operation = key.split("_")
        logger.info(f"📦 Processing {len(group)} {operation} operations for {context}s")

        tool_name = _TOOL_NAME_MAP.get((context, operation.lower()))
        if not tool_name:
            for schedule in group:
                all_failed.append({
                    "row": schedule.get("row_number", "?"),
                    "error": f"No tool mapping for {context}/{operation}",
                    "schedule": schedule
                })
            continue

        for schedule in group:
            row_num = schedule.get("row_number", "?")

            try:
                # Build tool parameters
                params = {
                    f"{context}_id": schedule[f"{context}_id"],
                    "section_id": schedule["section_id"],
                    "cost_centre_id": schedule["cost_centre_id"]
                }

                if operation == "CREATE":
                    schedule_rate = _get_schedule_rate(schedule)
                    blocks_count = schedule["blocks"]
                    start_time = schedule.get("start_time")
                    time_blocks = _convert_blocks_to_time_array(blocks_count, start_time, schedule_rate)

                    params.update({
                        "staff_id": schedule["staff_id"],
                        "date": schedule["date"],
                        "blocks": time_blocks,
                        "notes": schedule.get("notes", ""),
                        "is_locked": schedule.get("is_locked") or False
                    })

                elif operation == "UPDATE":
                    params["schedule_id"] = schedule["schedule_id"]
                    for field in ["staff_id", "date", "notes", "is_locked"]:
                        if field in schedule and schedule[field] is not None:
                            params[field] = schedule[field]

                    if "blocks" in schedule and schedule["blocks"] is not None:
                        schedule_rate = _get_schedule_rate(schedule)
                        start_time = schedule.get("start_time")
                        params["blocks"] = _convert_blocks_to_time_array(schedule["blocks"], start_time, schedule_rate)

                elif operation == "DELETE":
                    params["schedule_id"] = schedule["schedule_id"]

                    # If the schedule is locked and user confirmed unlock, unlock first
                    if schedule.get("unlock_before_delete"):
                        logger.info(f"  Row {row_num}: Schedule is locked — unlocking before delete")
                        update_tool = _TOOL_NAME_MAP.get((context, "update"))
                        if update_tool:
                            unlock_params = {
                                f"{context}_id": params[f"{context}_id"],
                                "section_id": params["section_id"],
                                "cost_centre_id": params["cost_centre_id"],
                                "schedule_id": params["schedule_id"],
                                "is_locked": False,
                            }
                            unlock_result = await mcp_client.execute_tool(update_tool, unlock_params)
                            unlock_data = unlock_result.get("data", {}) if unlock_result.get("success") else None
                            if not unlock_result.get("success") or (isinstance(unlock_data, dict) and unlock_data.get("success") is False):
                                unlock_err = (
                                    (unlock_data.get("error") if isinstance(unlock_data, dict) and unlock_data.get("success") is False else None)
                                    or unlock_result.get("error", "Unknown error")
                                )
                                all_failed.append({
                                    "row": row_num,
                                    "error": f"Failed to unlock schedule before delete: {unlock_err}",
                                    "schedule": schedule,
                                })
                                logger.warning(f"  ❌ Row {row_num}: Unlock failed — {unlock_err}")
                                continue
                            logger.info(f"  ✅ Row {row_num}: Schedule unlocked successfully")

                # Execute tool via MCP server HTTP API
                logger.info(f"  Row {row_num}: Calling {tool_name} with {context}_id={params[f'{context}_id']}")
                result = await mcp_client.execute_tool(tool_name, params)

                # MCP server returns {success, data, tool, error}
                # Note: outer "success" = HTTP call succeeded; check inner data for tool-level failures
                tool_data = result.get("data", {}) if result.get("success") else None
                if result.get("success") and (not isinstance(tool_data, dict) or tool_data.get("success") is not False):
                    all_succeeded.append({
                        "row": row_num,
                        "schedule": schedule,
                        "result": result.get("data", result)
                    })
                    logger.info(f"  ✅ Row {row_num}: {operation} succeeded")
                else:
                    raw_err = (
                        (tool_data.get("error") if isinstance(tool_data, dict) and tool_data.get("success") is False else None)
                        or result.get("error", "Unknown error")
                    )
                    raw_err_lower = raw_err.lower()

                    # "Already exists" on CREATE → auto-retry as UPDATE.
                    # Simpro uses one schedule per staff+costCentre+date;
                    # to add more time blocks, we PATCH the existing schedule.
                    if operation == "CREATE" and ("already exists" in raw_err_lower or "duplicate" in raw_err_lower):
                        import re as _re
                        id_match = _re.search(r"schedule\s+(\d+)", raw_err, _re.IGNORECASE)
                        existing_id = int(id_match.group(1)) if id_match else None

                        if existing_id:
                            logger.info(f"  🔄 Row {row_num}: Schedule exists (ID={existing_id}), fetching existing blocks to merge...")
                            try:
                                # 1) GET existing schedule to read its blocks
                                detail_result = await mcp_client.execute_tool("get_job_cost_centre_schedule_details", {
                                    "job_id": params[f"{context}_id"],
                                    "section_id": params["section_id"],
                                    "cost_centre_id": params["cost_centre_id"],
                                    "schedule_id": existing_id,
                                })
                                existing_blocks = []
                                if detail_result.get("success"):
                                    sched_data = detail_result.get("data", {}).get("schedule", {})
                                    existing_blocks = sched_data.get("Blocks", [])

                                # 2) Sanitize existing blocks to PATCH format
                                #    Simpro GET returns extra fields (Hrs, ISO8601*,
                                #    ScheduleRate as object) that PATCH rejects.
                                #    PATCH only accepts: StartTime, EndTime, ScheduleRate (int)
                                def _sanitize_block(blk):
                                    rate = blk.get("ScheduleRate")
                                    if isinstance(rate, dict):
                                        rate = rate.get("ID", DEFAULT_SCHEDULE_RATE_ID)
                                    elif not isinstance(rate, int):
                                        rate = DEFAULT_SCHEDULE_RATE_ID
                                    return {
                                        "StartTime": blk["StartTime"],
                                        "EndTime": blk["EndTime"],
                                        "ScheduleRate": rate,
                                    }

                                clean_existing = [_sanitize_block(b) for b in existing_blocks]
                                new_blocks = params.get("blocks", [])
                                merged_blocks = clean_existing + list(new_blocks)

                                # 3) PATCH with merged blocks
                                update_tool = _TOOL_NAME_MAP.get((context, "update"))
                                if update_tool:
                                    update_result = await mcp_client.execute_tool(update_tool, {
                                        f"{context}_id": params[f"{context}_id"],
                                        "section_id": params["section_id"],
                                        "cost_centre_id": params["cost_centre_id"],
                                        "schedule_id": existing_id,
                                        "blocks": merged_blocks,
                                    })
                                    if update_result.get("success") or update_result.get("data") is None:
                                        # PATCH returns 204 No Content on success
                                        all_succeeded.append({
                                            "row": row_num,
                                            "schedule": schedule,
                                            "result": {
                                                "note": f"Added time block to existing schedule {existing_id}",
                                                "schedule_id": existing_id,
                                                "merged_blocks": merged_blocks,
                                            }
                                        })
                                        logger.info(f"  ✅ Row {row_num}: Merged new block into existing schedule {existing_id}")
                                        continue
                                    else:
                                        logger.warning(f"  ❌ Row {row_num}: UPDATE failed: {update_result.get('error')}")
                                        # Fall through to normal error handling

                            except Exception as merge_err:
                                logger.warning(f"  ❌ Row {row_num}: Auto-merge failed: {merge_err}")
                                # Fall through to normal error handling

                        # If auto-merge didn't succeed, treat as warning
                        friendly_err = _translate_simpro_error(raw_err, schedule)
                        all_warnings.append({
                            "row": row_num,
                            "warning": friendly_err,
                            "schedule": schedule,
                        })
                        logger.warning(f"  ⚠️ Row {row_num}: {friendly_err}")
                        continue

                    friendly_err = _translate_simpro_error(raw_err, schedule)

                    # Use crossroads error_recovery for unknown errors
                    if friendly_err == raw_err and llm_chat:
                        try:
                            cr_result = await resolve_crossroads(
                                crossroad_type="error_recovery",
                                question=f"Simpro API error during {operation}: {raw_err}",
                                context={
                                    "raw_error": raw_err,
                                    "operation": operation,
                                    "tool_name": tool_name,
                                    "schedule": {
                                        "job_id": schedule.get("job_id"),
                                        "staff_id": schedule.get("staff_id"),
                                        "date": schedule.get("date"),
                                    },
                                },
                                llm_chat=llm_chat,
                            )
                            if cr_result.get("fields", {}).get("message"):
                                friendly_err = cr_result["fields"]["message"]
                        except Exception:
                            pass  # Keep the original friendly_err

                    all_failed.append({
                        "row": row_num,
                        "error": friendly_err,
                        "schedule": schedule
                    })
                    logger.warning(f"  ❌ Row {row_num}: {raw_err} → {friendly_err}")

            except Exception as e:
                error_msg = str(e)
                # Use crossroads for unexpected exceptions too
                if llm_chat:
                    try:
                        cr_result = await resolve_crossroads(
                            crossroad_type="error_recovery",
                            question=f"Unexpected error during schedule {operation}: {error_msg}",
                            context={
                                "raw_error": error_msg,
                                "operation": operation,
                                "exception_type": type(e).__name__,
                            },
                            llm_chat=llm_chat,
                        )
                        if cr_result.get("fields", {}).get("message"):
                            error_msg = cr_result["fields"]["message"]
                    except Exception:
                        pass

                all_failed.append({
                    "row": row_num,
                    "error": error_msg,
                    "schedule": schedule
                })
                logger.error(f"  ❌ Row {row_num}: Exception - {e}", exc_info=True)

    # Determine which operation was performed (use the first schedule)
    operation_key = schedules[0].get("operation", "CREATE").lower() + "d"

    summary = {
        "total": len(schedules),
        "succeeded": len(all_succeeded),
        "warnings": len(all_warnings),
        "failed": len(all_failed)
    }

    logger.info("=" * 70)
    logger.info(
        f"✅ Executor Summary: {summary['succeeded']}/{summary['total']} succeeded, "
        f"{summary['warnings']} warnings, {summary['failed']} failed"
    )
    logger.info("=" * 70)

    return {
        "success": len(all_succeeded) > 0 or (len(all_failed) == 0 and len(all_warnings) > 0),
        "summary": summary,
        operation_key: all_succeeded,
        "warnings": all_warnings,
        "failed": all_failed
    }
