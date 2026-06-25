"""
Contractor Job-related MCP tools.

Provides tools for viewing and creating contractor jobs in Simpro.
"""
from __future__ import annotations

from typing import Any, Dict

from src.simpro.api.contractor_jobs import ContractorJobsAPI
from src.simpro_api_reference import get_api_hint
from src.utils import get_logger

from .base import BaseTool

logger = get_logger(__name__)


class GetContractorJobDetailsTool(BaseTool):
    """
    Tool for getting detailed information about a specific contractor job.
    """
    
    def __init__(self):
        """Initialize get contractor job details tool"""
        self.contractor_jobs_api = ContractorJobsAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "get_contractor_job_details"
    
    def get_description(self) -> str:
        return """Get detailed information about a specific contractor job by ID.
        
Use this tool when the user asks for details about a contractor job,
subcontractor work, or external contractor assignments.

Examples:
- "Show me contractor job 12345"
- "What's in contractor job 67890?"
- "Get details for contractor job ID 111"
"""
    
    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "contractor_job_id": {
                    "type": "integer",
                    "description": "The ID of the contractor job to retrieve"
                }
            },
            "required": ["contractor_job_id"]
        }
    
    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get contractor job details"""
        contractor_job_id = arguments["contractor_job_id"]
        
        # Call Simpro API
        result = await self.contractor_jobs_api.get_contractor_job_by_id(
            contractor_job_id=contractor_job_id
        )
        
        return {
            "contractor_job": result,
            "contractor_job_id": contractor_job_id
        }


class GetContractorJobsByCostCentreTool(BaseTool):
    """
    Tool for getting contractor jobs for a specific cost centre.
    """
    
    def __init__(self):
        """Initialize get contractor jobs by cost centre tool"""
        self.contractor_jobs_api = ContractorJobsAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "get_contractor_jobs_by_cost_centre"
    
    def get_description(self) -> str:
        return """Get contractor jobs for a specific job cost centre.

Use this tool when the user asks about contractor jobs for a cost centre,
subcontractor assignments, or external work for a job section.

IMPORTANT: By default, the Simpro LIST endpoint returns only minimal fields (ID, _href).
To get fields like Status, Contractor, Materials, or Labor, you MUST pass the
`columns` parameter (e.g., columns="ID,Status,Contractor,Materials,Labor").

Examples:
- "Show me contractor jobs for job 123, section 456, cost centre 789"
- "Get subcontractor work for this cost centre"
- "What contractor jobs are in cost centre 5?"
"""
    
    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "integer",
                    "description": "The ID of the job"
                },
                "section_id": {
                    "type": "integer",
                    "description": "The ID of the section"
                },
                "cost_centre_id": {
                    "type": "integer",
                    "description": "The ID of the cost centre"
                },
                "columns": {
                    "type": "string",
                    "description": (
                        "Comma-separated list of columns to include in the response "
                        "(e.g., 'ID,Status,Contractor,Materials,Labor'). "
                        "By default, the Simpro LIST endpoint returns minimal fields. "
                        "Request specific columns like Status and Contractor to get full data."
                    )
                }
            },
            "required": ["job_id", "section_id", "cost_centre_id"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get contractor jobs by cost centre"""
        job_id = arguments["job_id"]
        section_id = arguments["section_id"]
        cost_centre_id = arguments["cost_centre_id"]
        columns = arguments.get("columns")

        # Call Simpro API
        result = await self.contractor_jobs_api.get_contractor_jobs_by_cost_centre(
            job_id=job_id,
            section_id=section_id,
            cost_centre_id=cost_centre_id,
            columns=columns,
        )

        return {
            "contractor_jobs": result,
            "job_id": job_id,
            "section_id": section_id,
            "cost_centre_id": cost_centre_id
        }


class CreateContractorJobTool(BaseTool):
    """
    Tool for creating a contractor job on a cost centre.
    """

    def __init__(self):
        self.contractor_jobs_api = ContractorJobsAPI()
        super().__init__()

    def get_name(self) -> str:
        return "create_contractor_job"

    def get_description(self) -> str:
        return f"""Create a contractor job (work order) for a specific cost centre.

Assigns a contractor to a cost centre with materials, labour costs, and a description.

{get_api_hint("mutation_errors")}

The contractor_job_data dict should follow Simpro's schema, e.g.:
{{
    "Contractor": 123,
    "Description": "Roofing materials and labour",
    "Materials": 5000.00,
    "Labor": 3000.00,
    "TaxCode": 1,
    "DateIssued": "2026-02-16",
    "ContractorSupplyMaterials": false
}}
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "integer",
                    "description": "The ID of the job"
                },
                "section_id": {
                    "type": "integer",
                    "description": "The ID of the section"
                },
                "cost_centre_id": {
                    "type": "integer",
                    "description": "The ID of the cost centre"
                },
                "contractor_job_data": {
                    "type": "object",
                    "description": "The contractor job body (Contractor, Description, Materials, Labor, etc.)"
                }
            },
            "required": ["job_id", "section_id", "cost_centre_id", "contractor_job_data"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        job_id = arguments["job_id"]
        section_id = arguments["section_id"]
        cost_centre_id = arguments["cost_centre_id"]
        contractor_job_data = arguments["contractor_job_data"]

        result = await self.contractor_jobs_api.create_contractor_job(
            job_id=job_id,
            section_id=section_id,
            cost_centre_id=cost_centre_id,
            contractor_job_data=contractor_job_data,
        )

        return {
            "success": True,
            "contractor_job": result,
            "job_id": job_id,
            "section_id": section_id,
            "cost_centre_id": cost_centre_id,
            "created": True
        }


class UpdateContractorJobTool(BaseTool):
    """
    Tool for updating (PATCHing) an existing contractor job.
    """

    def __init__(self):
        self.contractor_jobs_api = ContractorJobsAPI()
        super().__init__()

    def get_name(self) -> str:
        return "update_contractor_job"

    def get_description(self) -> str:
        return f"""Update an existing contractor job (work order) on a cost centre.

PATCHes an existing contractor job with new description, materials, labour,
items, dates, etc. Cannot change the Contractor field via PATCH.

{get_api_hint("mutation_errors", "patch_semantics")}

The contractor_job_data dict should contain only the fields to update, e.g.:
{{
    "Description": "Updated description",
    "Materials": 6000.00,
    "Labor": 4000.00,
    "TaxCode": 1,
    "Items": {{
        "Catalogs": [{{"ID": 10, "Qty": 5.0}}],
        "Prebuilds": [{{"ID": 20, "Qty": 2.0}}]
    }}
}}
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "integer",
                    "description": "The ID of the job"
                },
                "section_id": {
                    "type": "integer",
                    "description": "The ID of the section"
                },
                "cost_centre_id": {
                    "type": "integer",
                    "description": "The ID of the cost centre"
                },
                "contractor_job_id": {
                    "type": "integer",
                    "description": "The ID of the existing contractor job to update"
                },
                "contractor_job_data": {
                    "type": "object",
                    "description": "The fields to update (partial update)"
                }
            },
            "required": ["job_id", "section_id", "cost_centre_id", "contractor_job_id", "contractor_job_data"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        job_id = arguments["job_id"]
        section_id = arguments["section_id"]
        cost_centre_id = arguments["cost_centre_id"]
        contractor_job_id = arguments["contractor_job_id"]
        contractor_job_data = arguments["contractor_job_data"]

        await self.contractor_jobs_api.update_contractor_job(
            job_id=job_id,
            section_id=section_id,
            cost_centre_id=cost_centre_id,
            contractor_job_id=contractor_job_id,
            contractor_job_data=contractor_job_data,
        )

        return {
            "success": True,
            "contractor_job_id": contractor_job_id,
            "job_id": job_id,
            "section_id": section_id,
            "cost_centre_id": cost_centre_id,
            "updated": True
        }


class DeleteContractorJobTool(BaseTool):
    """
    Tool for deleting an existing contractor job.
    """

    def __init__(self):
        self.contractor_jobs_api = ContractorJobsAPI()
        super().__init__()

    def get_name(self) -> str:
        return "delete_contractor_job"

    def get_description(self) -> str:
        return f"""Delete an existing contractor job (work order) from a cost centre.

Permanently removes a contractor job. Returns 204 on success, 404 if not found.

{get_api_hint("mutation_errors")}
"""

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "integer",
                    "description": "The ID of the job"
                },
                "section_id": {
                    "type": "integer",
                    "description": "The ID of the section"
                },
                "cost_centre_id": {
                    "type": "integer",
                    "description": "The ID of the cost centre"
                },
                "contractor_job_id": {
                    "type": "integer",
                    "description": "The ID of the contractor job to delete"
                }
            },
            "required": ["job_id", "section_id", "cost_centre_id", "contractor_job_id"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        job_id = arguments["job_id"]
        section_id = arguments["section_id"]
        cost_centre_id = arguments["cost_centre_id"]
        contractor_job_id = arguments["contractor_job_id"]

        await self.contractor_jobs_api.delete_contractor_job(
            job_id=job_id,
            section_id=section_id,
            cost_centre_id=cost_centre_id,
            contractor_job_id=contractor_job_id,
        )

        return {
            "success": True,
            "contractor_job_id": contractor_job_id,
            "job_id": job_id,
            "section_id": section_id,
            "cost_centre_id": cost_centre_id,
            "deleted": True
        }