#mcp-simpro-server/src/tools
"""
Schedule-related MCP tools.

Provides tools for viewing schedules in Simpro.
"""
from __future__ import annotations

from datetime import datetime, date, timedelta
from typing import Any, Dict, List

from src.simpro.api.schedules import SchedulesAPI
from src.simpro_api_reference import get_api_hint
from src.utils import get_logger

from .base import BaseTool

logger = get_logger(__name__)


def _schedule_date_examples() -> str:
    """Generate dynamic date examples based on today's date."""
    today = date.today()
    today_str = today.isoformat()
    monday = today - timedelta(days=today.weekday())  # Monday of this week
    sunday = monday + timedelta(days=6)  # Sunday of this week
    month_end = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return (
        f'- "What\'s scheduled for today?" → date=\'{today_str}\'\n'
        f'- "Job schedules this week" → type=\'job\', date_from=\'{monday.isoformat()}\', date_to=\'{sunday.isoformat()}\'\n'
        f'- "Schedules till month end" → date_from=\'{today_str}\', date_to=\'{month_end.isoformat()}\'\n'
        f'- "Stephen\'s schedules" → date=\'{today_str}\' (filter results by name)'
    )


class GetSchedulesTool(BaseTool):
    """
    Tool for getting schedules in Simpro.
    """

    def __init__(self):
        """Initialize get schedules tool"""
        self.schedules_api = SchedulesAPI()
        super().__init__()

    def get_name(self) -> str:
        return "get_schedules"

    def get_description(self) -> str:
        return f"""Get list of schedules. Supports date and staff type (contractor/employee) filtering.

Use this tool when the user asks about schedules, appointments, calendar, or wants to see what's scheduled.

IMPORTANT: Results already include staff names. Do NOT search contacts first.

SCHEDULE TYPE vs STAFF TYPE — two different things:
1. TYPE FILTER (the 'type' parameter): Filters WHAT the schedule is for.
   Only use when user explicitly says "job schedules", "activity schedules", "quote schedules", or "lead schedules".
   - "job schedules" → type='job'
   - "activity schedules" → type='activity'
   - "quote schedules" → type='quote'
   - "lead schedules" → type='lead'
   - If user does NOT specify job/activity/quote/lead, do NOT set type. Omit it entirely.

2. FIELD FILTERING: Use the 'filters' param — ONLY these fields are valid Simpro /schedules/ filters
   (they map directly to schedule response fields):
   - Staff.ID         → exact staff ID                e.g. filters: {{"Staff.ID": "123"}}
   - Staff.Type       → "contractor" or "employee"    e.g. filters: {{"Staff.Type": "contractor"}}
   - Staff.Name       → staff name wildcard            e.g. filters: {{"Staff.Name": "%John%"}}
   - Staff.GivenName  → first name wildcard            e.g. filters: {{"Staff.GivenName": "%Jane%"}}
   - Staff.FamilyName → last name wildcard             e.g. filters: {{"Staff.FamilyName": "%Smith%"}}
   - ID               → exact schedule ID              e.g. filters: {{"ID": "456"}}
   - IsLocked         → lock status                    e.g. filters: {{"IsLocked": "true"}}
   - Reference        → reference/job-number string    e.g. filters: {{"Reference": "%JOB-001%"}}
   - Notes            → schedule notes text            e.g. filters: {{"Notes": "%urgent%"}}

   DO NOT invent other filter keys. The following are NOT valid /schedules/ filter fields —
   do NOT pass them: CostCentre.*, Department, Section.*, Blocks.*, TotalHours, Job.*, Quote.*
   If the user asks for schedules by department/section/cost centre, call this tool with date
   and type ONLY — post-result filtering is applied automatically after results are returned.

"this week" = Monday to Sunday (full 7-day week).

Date options (use ONE):
- "date": single date (YYYY-MM-DD)
- "date_from" + "date_to": range (inclusive, max 90 days)

Examples (dates computed from today):
{_schedule_date_examples()}

{get_api_hint("schedule_dates")}
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "page": {
                    "type": "integer",
                    "description": "Page number (1-based). Only used with single 'date' filter.",
                    "default": 1
                },
                "page_size": {
                    "type": "integer",
                    "description": "Number of schedules per page (max 250)",
                    "default": 250
                },
                "date": {
                    "type": "string",
                    "description": "Filter schedules for an exact date (YYYY-MM-DD). Use this for single-day queries."
                },
                "date_from": {
                    "type": "string",
                    "description": "Start date for date range filter (YYYY-MM-DD, inclusive). Must be used with date_to."
                },
                "date_to": {
                    "type": "string",
                    "description": "End date for date range filter (YYYY-MM-DD, inclusive). Must be used with date_to."
                },
                "type": {
                    "type": "string",
                    "description": "Filter by schedule category (NOT staff type). Only: job, activity, quote, lead. Omit to get all.",
                    "enum": ["job", "activity", "quote", "lead"]
                },
                "filters": {
                    "type": "object",
                    "description": (
                        "ONLY these fields are valid Simpro /schedules/ filter keys (exact response field names): "
                        "Staff.ID, Staff.Type ('contractor'/'employee'), Staff.Name ('%John%'), "
                        "Staff.GivenName, Staff.FamilyName, ID, IsLocked ('true'/'false'), "
                        "Reference, Notes. "
                        "Do NOT use CostCentre.*, Department, Section.*, Blocks.*, TotalHours, Job.*, Quote.* "
                        "— they are not valid /schedules/ endpoint filter fields and will be stripped."
                    ),
                    "additionalProperties": {"type": "string"},
                }
            }
        }

    # Explicit allowlist of every dot-notation filter key that Simpro's /schedules/
    # endpoint actually supports. These map 1:1 to schedule response fields.
    # Anything not in this set is invented by the LLM and must be stripped.
    #
    # Covered response fields:
    #   Staff.*      — Staff.ID, Staff.Type, Staff.Name, Staff.GivenName, Staff.FamilyName
    #   ID           — exact schedule ID filter
    #   IsLocked     — boolean lock status
    #   Reference    — reference/job-number string
    #   Notes        — schedule notes text
    #
    # NOT valid (dedicated params handled separately): Date, Type, page, pageSize
    # NOT valid (not schedule API fields): CostCentre.*, Department, Section.*,
    #   Blocks.*, TotalHours, Job.*, Quote.*, Activity.*
    _VALID_SCHEDULE_FILTERS = frozenset({
        "staff.id", "staff.type", "staff.name",
        "staff.givenname", "staff.familyname",
        "id", "islocked", "reference", "notes",
    })

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get schedules — supports single date or date range, with optional type filter."""
        page = arguments.get("page", 1)
        page_size = arguments.get("page_size", 250)
        date = arguments.get("date")
        date_from = arguments.get("date_from")
        date_to = arguments.get("date_to")
        schedule_type = arguments.get("type")
        filters = self.extract_filters(arguments)

        # Strip any filter key that is not a known-valid Simpro schedule response field.
        # The LLM sometimes invents CostCentre.Department / Section.Name / Blocks.* etc.
        # by following the dot-notation pattern — these are not real /schedules/ params.
        invalid_keys = [
            k for k in list(filters.keys())
            if k.lower() not in self._VALID_SCHEDULE_FILTERS
        ]
        for k in invalid_keys:
            logger.warning(
                f"get_schedules: dropping unsupported filter '{k}' — "
                f"valid filters: Staff.ID, Staff.Type, Staff.Name, Staff.GivenName, "
                f"Staff.FamilyName, ID, IsLocked, Reference, Notes"
            )
            del filters[k]

        # Date range mode: use Simpro's between() operator
        if date_from and date_to:
            try:
                start = datetime.strptime(date_from, "%Y-%m-%d")
                end = datetime.strptime(date_to, "%Y-%m-%d")
            except ValueError as e:
                return {"error": f"Invalid date format: {e}. Use YYYY-MM-DD."}

            if end < start:
                return {"error": f"date_to ({date_to}) must be on or after date_from ({date_from})."}

            delta_days = (end - start).days
            if delta_days > 90:
                return {"error": f"Date range too large ({delta_days} days). Maximum is 90 days."}

            # Use Simpro's between() operator for efficient single-request range query
            date_filter = f"between({date_from},{date_to})"
            logger.info(f"Date range query: Date={date_filter}, Type={schedule_type}, filters={filters}")

            all_schedules: List[Dict[str, Any]] = []
            current_page = 1
            while True:
                page_result = await self.schedules_api.get_schedules(
                    page=current_page,
                    page_size=page_size,
                    date=date_filter,
                    schedule_type=schedule_type,
                    filters=filters,
                )
                if isinstance(page_result, list):
                    all_schedules.extend(page_result)
                    # If we got fewer than page_size, we've reached the end
                    if len(page_result) < page_size:
                        break
                    current_page += 1
                else:
                    break

            logger.info(f"Date range {date_from} to {date_to}: found {len(all_schedules)} schedules")

            return {
                "schedules": all_schedules,
                "count": len(all_schedules),
                "date_from": date_from,
                "date_to": date_to,
                "type_filter": schedule_type,
            }

        # Single date or no date filter — auto-paginate
        all_schedules: List[Dict[str, Any]] = []
        current_page = 1
        while True:
            page_result = await self.schedules_api.get_schedules(
                page=current_page,
                page_size=page_size,
                date=date,
                schedule_type=schedule_type,
                filters=filters,
            )
            if isinstance(page_result, list):
                all_schedules.extend(page_result)
                if len(page_result) < page_size:
                    break
                current_page += 1
            else:
                break

        logger.info(f"get_schedules (single-date): fetched {len(all_schedules)} schedules across {current_page} page(s)")

        return {
            "schedules": all_schedules,
            "count": len(all_schedules),
            "date": date,
            "type_filter": schedule_type,
        }


class GetScheduleDetailsTool(BaseTool):
    """
    Tool for getting detailed information about a specific schedule.
    """
    
    def __init__(self):
        """Initialize get schedule details tool"""
        self.schedules_api = SchedulesAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "get_schedule_details"
    
    def get_description(self) -> str:
        return """Get detailed information about a specific schedule by ID.
        
Use this tool when the user asks for details about a specific schedule,
appointment, or wants to see schedule information.

Examples:
- "Show me details for schedule 12345"
- "What's in schedule 67890?"
- "Get information about schedule ID 111"
"""
    
    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "schedule_id": {
                    "type": "integer",
                    "description": "The ID of the schedule to retrieve"
                }
            },
            "required": ["schedule_id"]
        }
    
    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get schedule details"""
        schedule_id = arguments["schedule_id"]
        
        # Call Simpro API
        result = await self.schedules_api.get_schedule_by_id(schedule_id=schedule_id)
        
        return {
            "schedule": result,
            "schedule_id": schedule_id
        }


class GetJobCostCentreSchedulesTool(BaseTool):
    """
    Tool for listing schedules under a specific job cost centre.
    """

    def __init__(self):
        self.schedules_api = SchedulesAPI()
        super().__init__()

    def get_name(self) -> str:
        return "get_job_cost_centre_schedules"

    def get_description(self) -> str:
        return """List all schedules for a specific job cost centre.

Use this tool when the user asks about schedules assigned to a particular
cost centre within a job section. Returns staff assignments, hours, and dates.

IMPORTANT: If you already know the date or date range from a prior call (e.g.,
from get_schedules), ALWAYS pass the date parameter here to avoid returning
all historical schedules.

Requires job_id, section_id, and cost_centre_id. Use get_job_sections and
get_job_section_cost_centres first if you don't have these IDs.

Examples:
- "Show me schedules for cost centre 456 in job 123 section 789"
- "Who is scheduled to work on this cost centre?"
- "What are the staff assignments for this cost centre?"
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "integer",
                    "description": "The job ID"
                },
                "section_id": {
                    "type": "integer",
                    "description": "The section ID within the job"
                },
                "cost_centre_id": {
                    "type": "integer",
                    "description": "The cost centre ID within the section"
                },
                "date": {
                    "type": "string",
                    "description": "Filter by date (YYYY-MM-DD) or Simpro operator like between(YYYY-MM-DD,YYYY-MM-DD). IMPORTANT: Always pass the date if you know it from prior context."
                },
                "page": {
                    "type": "integer",
                    "description": "Page number (1-based)",
                    "default": 1
                },
                "page_size": {
                    "type": "integer",
                    "description": "Number of schedules per page (max 250)",
                    "default": 50
                }
            },
            "required": ["job_id", "section_id", "cost_centre_id"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get job cost centre schedules with auto-pagination."""
        job_id = arguments["job_id"]
        section_id = arguments["section_id"]
        cost_centre_id = arguments["cost_centre_id"]
        date = arguments.get("date")
        page_size = arguments.get("page_size", 250)

        all_schedules: List[Dict[str, Any]] = []
        current_page = 1
        while True:
            page_result = await self.schedules_api.get_job_cost_centre_schedules(
                job_id=job_id,
                section_id=section_id,
                cost_centre_id=cost_centre_id,
                page=current_page,
                page_size=page_size,
                date=date,
            )
            if isinstance(page_result, list):
                all_schedules.extend(page_result)
                if len(page_result) < page_size:
                    break
                current_page += 1
            else:
                break

        logger.info(f"get_job_cost_centre_schedules: fetched {len(all_schedules)} schedules across {current_page} page(s)")

        return {
            "schedules": all_schedules,
            "job_id": job_id,
            "section_id": section_id,
            "cost_centre_id": cost_centre_id,
            "total_fetched": len(all_schedules),
            "pages_fetched": current_page
        }


class GetJobCostCentreScheduleDetailsTool(BaseTool):
    """
    Tool for getting details of a specific job cost centre schedule.
    """

    def __init__(self):
        self.schedules_api = SchedulesAPI()
        super().__init__()

    def get_name(self) -> str:
        return "get_job_cost_centre_schedule_details"

    def get_description(self) -> str:
        return """Get details for a specific schedule within a job cost centre.

Returns full schedule info including staff assignment, total hours, notes,
time blocks (start/end times, schedule rates), and lock status.

The Notes field may contain HTML — it will be cleaned automatically.

Requires job_id, section_id, cost_centre_id, and schedule_id.
Use get_job_cost_centre_schedules first to find schedule IDs.

Examples:
- "Show me details for schedule 789 in cost centre 456"
- "What are the time blocks for this schedule?"
- "Get the notes for schedule 789"
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "integer",
                    "description": "The job ID"
                },
                "section_id": {
                    "type": "integer",
                    "description": "The section ID within the job"
                },
                "cost_centre_id": {
                    "type": "integer",
                    "description": "The cost centre ID within the section"
                },
                "schedule_id": {
                    "type": "integer",
                    "description": "The schedule ID to retrieve details for"
                }
            },
            "required": ["job_id", "section_id", "cost_centre_id", "schedule_id"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get job cost centre schedule details"""
        job_id = arguments["job_id"]
        section_id = arguments["section_id"]
        cost_centre_id = arguments["cost_centre_id"]
        schedule_id = arguments["schedule_id"]

        result = await self.schedules_api.get_job_cost_centre_schedule_details(
            job_id=job_id,
            section_id=section_id,
            cost_centre_id=cost_centre_id,
            schedule_id=schedule_id
        )

        return {
            "schedule": result,
            "job_id": job_id,
            "section_id": section_id,
            "cost_centre_id": cost_centre_id,
            "schedule_id": schedule_id
        }


class CreateJobCostCentreScheduleTool(BaseTool):
    """
    Tool for creating a new schedule under a job cost centre.
    """

    def __init__(self):
        self.schedules_api = SchedulesAPI()
        super().__init__()

    def get_name(self) -> str:
        return "create_job_cost_centre_schedule"

    def get_description(self) -> str:
        return f"""Create a new schedule for a job cost centre.

Assigns a staff member to a cost centre on a date with time blocks.
Requires job_id, section_id, cost_centre_id, staff_id, date, and blocks.

Example: staff 123 on cost centre 456, 2026-02-10, 08:00-16:00

{get_api_hint("mutation_errors")}
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "integer",
                    "description": "The job ID"
                },
                "section_id": {
                    "type": "integer",
                    "description": "The section ID within the job"
                },
                "cost_centre_id": {
                    "type": "integer",
                    "description": "The cost centre ID within the section"
                },
                "staff_id": {
                    "type": "integer",
                    "description": "The staff member ID to assign"
                },
                "date": {
                    "type": "string",
                    "description": "Schedule date in YYYY-MM-DD format"
                },
                "blocks": {
                    "type": "array",
                    "description": "Time blocks for the schedule",
                    "items": {
                        "type": "object",
                        "properties": {
                            "StartTime": {
                                "type": "string",
                                "description": "Start time in HH:MM format (e.g. '08:00')"
                            },
                            "EndTime": {
                                "type": "string",
                                "description": "End time in HH:MM format (e.g. '16:00')"
                            },
                            "ScheduleRate": {
                                "type": "integer",
                                "description": "Optional schedule rate ID (e.g. for overtime)"
                            }
                        },
                        "required": ["StartTime", "EndTime"]
                    }
                },
                "notes": {
                    "type": "string",
                    "description": "Optional notes for the schedule"
                },
                "is_locked": {
                    "type": "boolean",
                    "description": "Whether to lock the schedule (default false)"
                }
            },
            "required": ["job_id", "section_id", "cost_centre_id", "staff_id", "date", "blocks"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute create job cost centre schedule"""
        job_id = arguments["job_id"]
        section_id = arguments["section_id"]
        cost_centre_id = arguments["cost_centre_id"]
        staff_id = arguments["staff_id"]
        date = arguments["date"]
        blocks = arguments["blocks"]
        notes = arguments.get("notes")
        is_locked = arguments.get("is_locked")

        result = await self.schedules_api.create_job_cost_centre_schedule(
            job_id=job_id,
            section_id=section_id,
            cost_centre_id=cost_centre_id,
            staff_id=staff_id,
            date=date,
            blocks=blocks,
            notes=notes,
            is_locked=is_locked
        )

        return {
            "schedule": result,
            "job_id": job_id,
            "section_id": section_id,
            "cost_centre_id": cost_centre_id,
            "created": True
        }


class UpdateJobCostCentreScheduleTool(BaseTool):
    """
    Tool for updating an existing job cost centre schedule.
    """

    def __init__(self):
        self.schedules_api = SchedulesAPI()
        super().__init__()

    def get_name(self) -> str:
        return "update_job_cost_centre_schedule"

    def get_description(self) -> str:
        return f"""Update an existing schedule for a job cost centre.

{get_api_hint("patch_semantics")}

Requires job_id, section_id, cost_centre_id, and schedule_id.
Use get_job_cost_centre_schedules first to find schedule IDs.

Examples:
- "Move schedule 789 to tomorrow" → date
- "Reassign schedule 789 to staff 456" → staff_id
- "Lock schedule 789" → is_locked=true

{get_api_hint("mutation_errors")}
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "integer",
                    "description": "The job ID"
                },
                "section_id": {
                    "type": "integer",
                    "description": "The section ID within the job"
                },
                "cost_centre_id": {
                    "type": "integer",
                    "description": "The cost centre ID within the section"
                },
                "schedule_id": {
                    "type": "integer",
                    "description": "The schedule ID to update"
                },
                "staff_id": {
                    "type": "integer",
                    "description": "New staff member ID to assign (optional)"
                },
                "date": {
                    "type": "string",
                    "description": "New schedule date in YYYY-MM-DD format (optional)"
                },
                "blocks": {
                    "type": "array",
                    "description": "New time blocks (replaces all existing blocks)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "StartTime": {
                                "type": "string",
                                "description": "Start time in HH:MM format (e.g. '08:00')"
                            },
                            "EndTime": {
                                "type": "string",
                                "description": "End time in HH:MM format (e.g. '16:00')"
                            },
                            "ScheduleRate": {
                                "type": "integer",
                                "description": "Optional schedule rate ID"
                            }
                        },
                        "required": ["StartTime", "EndTime"]
                    }
                },
                "notes": {
                    "type": "string",
                    "description": "New notes for the schedule (optional)"
                },
                "is_locked": {
                    "type": "boolean",
                    "description": "Whether to lock/unlock the schedule (optional)"
                }
            },
            "required": ["job_id", "section_id", "cost_centre_id", "schedule_id"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute update job cost centre schedule"""
        job_id = arguments["job_id"]
        section_id = arguments["section_id"]
        cost_centre_id = arguments["cost_centre_id"]
        schedule_id = arguments["schedule_id"]

        result = await self.schedules_api.update_job_cost_centre_schedule(
            job_id=job_id,
            section_id=section_id,
            cost_centre_id=cost_centre_id,
            schedule_id=schedule_id,
            staff_id=arguments.get("staff_id"),
            date=arguments.get("date"),
            blocks=arguments.get("blocks"),
            notes=arguments.get("notes"),
            is_locked=arguments.get("is_locked")
        )

        return {
            "success": True,
            "message": "Schedule updated successfully",
            "job_id": job_id,
            "section_id": section_id,
            "cost_centre_id": cost_centre_id,
            "schedule_id": schedule_id
        }


class DeleteJobCostCentreScheduleTool(BaseTool):
    """
    Tool for deleting a job cost centre schedule.
    """

    def __init__(self):
        self.schedules_api = SchedulesAPI()
        super().__init__()

    def get_name(self) -> str:
        return "delete_job_cost_centre_schedule"

    def get_description(self) -> str:
        return """Delete a specific schedule from a job cost centre.

Permanently removes the schedule. Returns 204 on success, 404 if not found.

Requires job_id, section_id, cost_centre_id, and schedule_id.
Use get_job_cost_centre_schedules first to find schedule IDs.

IMPORTANT: Always confirm with the user before deleting a schedule.

Examples:
- "Delete schedule 789 from cost centre 456"
- "Remove the schedule for staff on this cost centre"
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "integer",
                    "description": "The job ID"
                },
                "section_id": {
                    "type": "integer",
                    "description": "The section ID within the job"
                },
                "cost_centre_id": {
                    "type": "integer",
                    "description": "The cost centre ID within the section"
                },
                "schedule_id": {
                    "type": "integer",
                    "description": "The schedule ID to delete"
                }
            },
            "required": ["job_id", "section_id", "cost_centre_id", "schedule_id"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute delete job cost centre schedule"""
        job_id = arguments["job_id"]
        section_id = arguments["section_id"]
        cost_centre_id = arguments["cost_centre_id"]
        schedule_id = arguments["schedule_id"]

        await self.schedules_api.delete_job_cost_centre_schedule(
            job_id=job_id,
            section_id=section_id,
            cost_centre_id=cost_centre_id,
            schedule_id=schedule_id
        )

        return {
            "success": True,
            "message": "Schedule deleted successfully",
            "job_id": job_id,
            "section_id": section_id,
            "cost_centre_id": cost_centre_id,
            "schedule_id": schedule_id
        }