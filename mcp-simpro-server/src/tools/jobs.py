#mcp-simpro-server/src/tools
"""
Jobs-related MCP tools.

Provides tools for searching, viewing, and managing jobs in Simpro.
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.simpro.api.jobs import JobsAPI
from src.simpro_api_reference import get_api_hint
from src.utils import get_logger

from .base import BaseTool

logger = get_logger(__name__)


class SearchJobsTool(BaseTool):
    """
    Tool for searching jobs in Simpro.
    
    Allows LLM to search jobs with various filters.
    """
    
    def __init__(self):
        """Initialize search jobs tool"""
        self.jobs_api = JobsAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "search_jobs"
    
    def get_description(self) -> str:
        return f"""Search for jobs in Simpro with optional filters.

Use this tool when the user asks about jobs, wants to find jobs,
or needs to get a list of jobs.

Filterable fields (use in 'filters' param): Stage, Type, DateIssued,
Site.Name, Site.ID, Customer.CompanyName, Customer.ID, Total.ExTax, Total.IncTax

NOT FILTERABLE: Status — Simpro rejects search on this column.
To find "active" jobs, filter by Stage instead (e.g., Stage = "Progress").

Examples:
- "Jobs in progress" → filters: {{"Stage": "Progress"}}
- "Jobs for customer 690" → filters: {{"Customer.ID": "690"}}
- "Jobs for a customer" → filters: {{"Customer.CompanyName": "%Smith%"}}
- "Jobs at a site" → filters: {{"Site.Name": "%bloomfield%"}}
- "Jobs created this month" → filters: {{"DateIssued": "ge(2026-02-01)"}}
- "Jobs between dates" → filters: {{"DateIssued": "between(2026-01-01,2026-02-28)"}}
- "Project jobs" → filters: {{"Type": "Project"}}

{get_api_hint("search_operators", "pagination")}
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
            }
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute job search with auto-pagination to fetch all pages."""
        page_size = arguments.get("page_size", 250)

        filters = self.extract_filters(arguments)

        # Auto-paginate to fetch all results
        all_jobs: List[Dict[str, Any]] = []
        current_page = 1
        while True:
            result = await self.jobs_api.get_jobs(
                page=current_page,
                page_size=page_size,
                **filters
            )
            if isinstance(result, list):
                all_jobs.extend(result)
                if len(result) < page_size:
                    break
                current_page += 1
            else:
                break

        logger.info(f"search_jobs: fetched {len(all_jobs)} jobs across {current_page} page(s)")

        return {
            "jobs": all_jobs,
            "total_fetched": len(all_jobs),
            "pages_fetched": current_page
        }


class GetJobDetailsTool(BaseTool):
    """
    Tool for getting detailed information about a specific job.
    """
    
    def __init__(self):
        """Initialize get job details tool"""
        self.jobs_api = JobsAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "get_job_details"
    
    def get_description(self) -> str:
        return f"""Get detailed information about a specific job by ID.

Use this tool when the user asks for details about a specific job,
wants to see a job's information, or needs to check a job's status.

{get_api_hint("display_all")}

LIMITATION: Even with display='all', this tool returns JOB-LEVEL totals only.
For PER-COST-CENTRE profitability, use get_job_sections + get_job_section_cost_centres.

Examples:
- "Show me details for job 12345" → display=None (basic info only)
- "What sections and cost centres does job 12345 have?" → display='all'
- "Profitability per cost centre for job 12345" → use get_job_sections + get_job_section_cost_centres
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "integer",
                    "description": "The ID of the job to retrieve"
                },
                "columns": {
                    "type": "string",
                    "description": "Comma-separated list of columns to include (optional)"
                },
                "display": {
                    "type": "string",
                    "description": "Set to 'all' to include all subresources (sections, cost centres, items) in one call. Omit for basic job info only.",
                    "enum": ["all"]
                }
            },
            "required": ["job_id"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get job details"""
        job_id = arguments["job_id"]
        columns = arguments.get("columns")
        display = arguments.get("display")

        # Call Simpro API
        result = await self.jobs_api.get_job_by_id(
            job_id=job_id,
            columns=columns,
            display=display
        )
        import json
        result_json = json.dumps(result, indent=2)
        logger.info(f"[tool->] get_job_details returning {len(result_json)} chars")
        logger.info(f"[tool->] result has {len(result.get('CustomFields', []))} custom fields")
        logger.debug(f"[tool->] first 500 chars: {result_json[:500]}...")
        return {
            "job": result,
            "job_id": job_id
        }


class GetJobSectionsTool(BaseTool):
    """
    Tool for getting sections of a job.
    """
    
    def __init__(self):
        """Initialize get job sections tool"""
        self.jobs_api = JobsAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "get_job_sections"
    
    def get_description(self) -> str:
        return """Get all sections for a specific job.
        
Use this tool when the user asks about job sections, job structure,
or wants to see what sections a job has.

Examples:
- "Show me sections for job 12345"
- "What sections does job 67890 have?"
- "List all sections of job ID 111"
"""
    
    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "integer",
                    "description": "The ID of the job"
                }
            },
            "required": ["job_id"]
        }
    
    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get job sections"""
        job_id = arguments["job_id"]
        
        # Call Simpro API
        result = await self.jobs_api.get_job_sections(job_id=job_id)
        
        return {
            "sections": result,
            "job_id": job_id
        }