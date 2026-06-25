#mcp-simpro-server/src/tools

"""
Quote-related MCP tools.

Provides tools for searching and viewing quotes in Simpro.
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.simpro.api.quotes import QuotesAPI
from src.simpro_api_reference import get_api_hint
from src.utils import get_logger

from .base import BaseTool

logger = get_logger(__name__)


class SearchQuotesTool(BaseTool):
    """
    Tool for searching quotes in Simpro.
    """
    
    def __init__(self):
        """Initialize search quotes tool"""
        self.quotes_api = QuotesAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "search_quotes"
    
    def get_description(self) -> str:
        return f"""Search for quotes in Simpro with optional filters.

Use this tool when the user asks about quotes, pricing, proposals,
or wants to find quotes.

Filterable fields (use in 'filters' param): IsClosed, Status, DateIssued,
Customer.ID, Customer.CompanyName, Site.Name, Total.ExTax, Total.IncTax

Examples:
- "Show me all open quotes" → is_closed: false
- "Quotes for customer 690" → filters: {{"Customer.ID": "690"}}
- "Quotes for Smith" → filters: {{"Customer.CompanyName": "%Smith%"}}
- "Quotes from January" → filters: {{"DateIssued": "between(2026-01-01,2026-01-31)"}}

{get_api_hint("search_operators", "pagination")}
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "is_closed": {
                    "type": "boolean",
                    "description": "Filter by closed status (false = open quotes)",
                    "default": False
                }
            }
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute quote search with auto-pagination to fetch all pages."""
        page_size = arguments.get("page_size", 250)
        is_closed = arguments.get("is_closed", False)

        filters = self.extract_filters(arguments)

        # Auto-paginate to fetch all results
        all_quotes: List[Dict[str, Any]] = []
        current_page = 1
        while True:
            result = await self.quotes_api.get_quotes(
                page=current_page,
                page_size=page_size,
                is_closed=is_closed,
                **filters,
            )
            if isinstance(result, list):
                all_quotes.extend(result)
                if len(result) < page_size:
                    break
                current_page += 1
            else:
                break

        logger.info(f"search_quotes: fetched {len(all_quotes)} quotes across {current_page} page(s)")

        return {
            "quotes": all_quotes,
            "total_fetched": len(all_quotes),
            "pages_fetched": current_page,
            "is_closed": is_closed
        }


class GetQuoteDetailsTool(BaseTool):
    """
    Tool for getting detailed information about a specific quote.
    """
    
    def __init__(self):
        """Initialize get quote details tool"""
        self.quotes_api = QuotesAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "get_quote_details"
    
    def get_description(self) -> str:
        return f"""Get detailed information about a specific quote by ID.

Use this tool when the user asks for details about a specific quote,
wants to see quote information, or needs to check a quote's details.

{get_api_hint("display_all")}

Examples:
- "Show me details for quote 12345" → display=None (basic info only)
- "What's the full breakdown of quote 67890?" → display='all'
- "What items are in quote 111?" → display='all'
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "quote_id": {
                    "type": "integer",
                    "description": "The ID of the quote to retrieve"
                },
                "display": {
                    "type": "string",
                    "description": "Set to 'all' to include all subresources (sections, cost centres, items) in one call. Omit for basic quote info only.",
                    "enum": ["all"]
                }
            },
            "required": ["quote_id"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get quote details"""
        quote_id = arguments["quote_id"]
        display = arguments.get("display")

        # Call Simpro API
        result = await self.quotes_api.get_quote_by_id(
            quote_id=quote_id,
            display=display
        )
        
        return {
            "quote": result,
            "quote_id": quote_id
        }


class GetQuoteSectionsTool(BaseTool):
    """
    Tool for getting sections of a quote.
    """
    
    def __init__(self):
        """Initialize get quote sections tool"""
        self.quotes_api = QuotesAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "get_quote_sections"
    
    def get_description(self) -> str:
        return """Get all sections for a specific quote.
        
Use this tool when the user asks about quote sections, quote structure,
or wants to see what sections a quote has.

Examples:
- "Show me sections for quote 12345"
- "What sections does quote 67890 have?"
- "List all sections of quote ID 111"
"""
    
    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "quote_id": {
                    "type": "integer",
                    "description": "The ID of the quote"
                }
            },
            "required": ["quote_id"]
        }
    
    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get quote sections"""
        quote_id = arguments["quote_id"]
        
        # Call Simpro API
        result = await self.quotes_api.get_quote_sections(quote_id=quote_id)
        
        return {
            "sections": result,
            "quote_id": quote_id
        }


class GetQuoteCostCentreSchedulesTool(BaseTool):
    """
    Tool for listing schedules under a specific quote cost centre.
    """

    def __init__(self):
        self.quotes_api = QuotesAPI()
        super().__init__()

    def get_name(self) -> str:
        return "get_quote_cost_centre_schedules"

    def get_description(self) -> str:
        return """List all schedules for a specific quote cost centre.

Use this tool when the user asks about schedules assigned to a particular
cost centre within a quote section. Returns staff assignments, hours, and dates.

Requires quote_id, section_id, and cost_centre_id. Use get_quote_sections and
get_quote_cost_centres first if you don't have these IDs.

Examples:
- "Show me schedules for cost centre 456 in quote 123 section 789"
- "Who is scheduled to work on this quote cost centre?"
- "What are the staff assignments for this quote cost centre?"
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "quote_id": {
                    "type": "integer",
                    "description": "The quote ID"
                },
                "section_id": {
                    "type": "integer",
                    "description": "The section ID within the quote"
                },
                "cost_centre_id": {
                    "type": "integer",
                    "description": "The cost centre ID within the section"
                }
            },
            "required": ["quote_id", "section_id", "cost_centre_id"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get quote cost centre schedules with auto-pagination."""
        quote_id = arguments["quote_id"]
        section_id = arguments["section_id"]
        cost_centre_id = arguments["cost_centre_id"]
        page_size = arguments.get("page_size", 250)

        all_schedules: List[Dict[str, Any]] = []
        current_page = 1
        while True:
            page_result = await self.quotes_api.get_quote_cost_centre_schedules(
                quote_id=quote_id,
                section_id=section_id,
                cost_centre_id=cost_centre_id,
                page=current_page,
                page_size=page_size
            )
            if isinstance(page_result, list):
                all_schedules.extend(page_result)
                if len(page_result) < page_size:
                    break
                current_page += 1
            else:
                break

        logger.info(f"get_quote_cost_centre_schedules: fetched {len(all_schedules)} schedules across {current_page} page(s)")

        return {
            "schedules": all_schedules,
            "quote_id": quote_id,
            "section_id": section_id,
            "cost_centre_id": cost_centre_id,
            "total_fetched": len(all_schedules),
            "pages_fetched": current_page
        }


class CreateQuoteCostCentreScheduleTool(BaseTool):
    """
    Tool for creating a new schedule under a quote cost centre.
    """

    def __init__(self):
        self.quotes_api = QuotesAPI()
        super().__init__()

    def get_name(self) -> str:
        return "create_quote_cost_centre_schedule"

    def get_description(self) -> str:
        return f"""Create a new schedule for a quote cost centre.

Assigns a staff member to a quote cost centre on a date with time blocks.
Requires quote_id, section_id, cost_centre_id, staff_id, date, and blocks.

{get_api_hint("mutation_errors")}
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "quote_id": {
                    "type": "integer",
                    "description": "The quote ID"
                },
                "section_id": {
                    "type": "integer",
                    "description": "The section ID within the quote"
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
            "required": ["quote_id", "section_id", "cost_centre_id", "staff_id", "date", "blocks"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute create quote cost centre schedule"""
        quote_id = arguments["quote_id"]
        section_id = arguments["section_id"]
        cost_centre_id = arguments["cost_centre_id"]
        staff_id = arguments["staff_id"]
        date = arguments["date"]
        blocks = arguments["blocks"]
        notes = arguments.get("notes")
        is_locked = arguments.get("is_locked")

        result = await self.quotes_api.create_quote_cost_centre_schedule(
            quote_id=quote_id,
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
            "quote_id": quote_id,
            "section_id": section_id,
            "cost_centre_id": cost_centre_id,
            "created": True
        }


class GetQuoteCostCentreScheduleDetailsTool(BaseTool):
    """
    Tool for getting details of a specific quote cost centre schedule.
    """

    def __init__(self):
        self.quotes_api = QuotesAPI()
        super().__init__()

    def get_name(self) -> str:
        return "get_quote_cost_centre_schedule_details"

    def get_description(self) -> str:
        return """Get details for a specific schedule within a quote cost centre.

Returns full schedule info including staff assignment, total hours, notes,
time blocks (start/end times, schedule rates), and lock status.

The Notes field may contain HTML — it will be cleaned automatically.

Requires quote_id, section_id, cost_centre_id, and schedule_id.
Use get_quote_cost_centre_schedules first to find schedule IDs.

Examples:
- "Show me details for schedule 789 in quote cost centre 456"
- "What are the time blocks for this quote schedule?"
- "Get the notes for quote schedule 789"
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "quote_id": {
                    "type": "integer",
                    "description": "The quote ID"
                },
                "section_id": {
                    "type": "integer",
                    "description": "The section ID within the quote"
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
            "required": ["quote_id", "section_id", "cost_centre_id", "schedule_id"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get quote cost centre schedule details"""
        quote_id = arguments["quote_id"]
        section_id = arguments["section_id"]
        cost_centre_id = arguments["cost_centre_id"]
        schedule_id = arguments["schedule_id"]

        result = await self.quotes_api.get_quote_cost_centre_schedule_details(
            quote_id=quote_id,
            section_id=section_id,
            cost_centre_id=cost_centre_id,
            schedule_id=schedule_id
        )

        return {
            "schedule": result,
            "quote_id": quote_id,
            "section_id": section_id,
            "cost_centre_id": cost_centre_id,
            "schedule_id": schedule_id
        }


class UpdateQuoteCostCentreScheduleTool(BaseTool):
    """
    Tool for updating an existing quote cost centre schedule.
    """

    def __init__(self):
        self.quotes_api = QuotesAPI()
        super().__init__()

    def get_name(self) -> str:
        return "update_quote_cost_centre_schedule"

    def get_description(self) -> str:
        return f"""Update an existing schedule for a quote cost centre.

{get_api_hint("patch_semantics")}

Requires quote_id, section_id, cost_centre_id, and schedule_id.

Examples:
- "Move quote schedule 789 to tomorrow" → date
- "Reassign quote schedule 789 to staff 456" → staff_id
- "Lock quote schedule 789" → is_locked=true

{get_api_hint("mutation_errors")}
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "quote_id": {
                    "type": "integer",
                    "description": "The quote ID"
                },
                "section_id": {
                    "type": "integer",
                    "description": "The section ID within the quote"
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
            "required": ["quote_id", "section_id", "cost_centre_id", "schedule_id"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute update quote cost centre schedule"""
        quote_id = arguments["quote_id"]
        section_id = arguments["section_id"]
        cost_centre_id = arguments["cost_centre_id"]
        schedule_id = arguments["schedule_id"]

        await self.quotes_api.update_quote_cost_centre_schedule(
            quote_id=quote_id,
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
            "message": "Quote schedule updated successfully",
            "quote_id": quote_id,
            "section_id": section_id,
            "cost_centre_id": cost_centre_id,
            "schedule_id": schedule_id
        }


class DeleteQuoteCostCentreScheduleTool(BaseTool):
    """
    Tool for deleting a quote cost centre schedule.
    """

    def __init__(self):
        self.quotes_api = QuotesAPI()
        super().__init__()

    def get_name(self) -> str:
        return "delete_quote_cost_centre_schedule"

    def get_description(self) -> str:
        return """Delete a specific schedule from a quote cost centre.

Permanently removes the schedule. Returns 204 on success, 404 if not found.

Requires quote_id, section_id, cost_centre_id, and schedule_id.
Use get_quote_cost_centre_schedules first to find schedule IDs.

IMPORTANT: Always confirm with the user before deleting a schedule.

Examples:
- "Delete schedule 789 from quote cost centre 456"
- "Remove the schedule for staff on this quote cost centre"
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "quote_id": {
                    "type": "integer",
                    "description": "The quote ID"
                },
                "section_id": {
                    "type": "integer",
                    "description": "The section ID within the quote"
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
            "required": ["quote_id", "section_id", "cost_centre_id", "schedule_id"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute delete quote cost centre schedule"""
        quote_id = arguments["quote_id"]
        section_id = arguments["section_id"]
        cost_centre_id = arguments["cost_centre_id"]
        schedule_id = arguments["schedule_id"]

        await self.quotes_api.delete_quote_cost_centre_schedule(
            quote_id=quote_id,
            section_id=section_id,
            cost_centre_id=cost_centre_id,
            schedule_id=schedule_id
        )

        return {
            "success": True,
            "message": "Quote schedule deleted successfully",
            "quote_id": quote_id,
            "section_id": section_id,
            "cost_centre_id": cost_centre_id,
            "schedule_id": schedule_id
        }