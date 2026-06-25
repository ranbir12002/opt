"""
Cost Centre-related MCP tools.

Provides tools for viewing cost centres in Simpro.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import httpx

from src.simpro.api.cost_centers import CostCentresAPI
from src.simpro_api_reference import get_api_hint
from src.utils import get_logger
from src.utils.cache import cache as _cache

from .base import BaseTool

logger = get_logger(__name__)

# ── Cost-centre ↔ section cache helpers ─────────────────────────────
# The LLM often mixes up which CC instance IDs belong to which section
# when fetching multiple sections in parallel.  These helpers let the
# server auto-correct the section_id before hitting the Simpro API.

_CC_CACHE_TTL = 600  # 10 minutes


def _cache_cc_section(job_id: int, section_id: int, instance_ids: list[int]) -> None:
    """Cache which section each cost centre instance belongs to."""
    for cc_id in instance_ids:
        _cache.set(f"cc_section:{job_id}:{cc_id}", section_id, ttl=_CC_CACHE_TTL)


def _get_cached_section(job_id: int, cost_centre_id: int) -> Optional[int]:
    """Look up cached section_id for a cost centre instance."""
    return _cache.get(f"cc_section:{job_id}:{cost_centre_id}")


async def _discover_correct_section(
    job_id: int,
    cost_centre_id: int,
    cost_centres_api: CostCentresAPI,
) -> Optional[int]:
    """Scan all sections of a job to find which one owns this CC ID."""
    from src.simpro.api.jobs import JobsAPI

    jobs_api = JobsAPI()
    sections = await jobs_api.get_job_sections(job_id)
    if not isinstance(sections, list):
        return None
    for section in sections:
        sid = section.get("ID")
        if sid is None:
            continue
        ccs = await cost_centres_api.get_job_section_cost_centres(job_id, sid)
        cc_ids = [cc["ID"] for cc in ccs if isinstance(cc, dict) and "ID" in cc]
        _cache_cc_section(job_id, sid, cc_ids)
        if cost_centre_id in cc_ids:
            return sid
    return None


async def _resolve_section(
    job_id: int,
    section_id: int,
    cost_centre_id: int,
    cost_centres_api: CostCentresAPI,
) -> int:
    """Return the correct section_id for a cost centre, auto-correcting if needed."""
    cached = _get_cached_section(job_id, cost_centre_id)
    if cached is not None and cached != section_id:
        logger.warning(
            f"Auto-correcting section for CC {cost_centre_id}: "
            f"{section_id} → {cached} (job {job_id})"
        )
        return cached
    return section_id


# ── Financial summary extraction ──────────────────────────────────
# The Simpro `Totals` object is 5+ levels deep, which causes LLMs
# to hallucinate dollar amounts.  This helper pre-computes a flat
# dict so the LLM has simple key-value pairs to read from.


def _extract_financial_summary(cc: dict) -> dict:
    """Extract key financial metrics from deeply nested Totals into a flat dict.

    Returned alongside the original nested data so the LLM can quote
    exact figures without navigating Totals.Materials.Cost.Actual etc.
    """
    totals = cc.get("Totals") or {}
    total_obj = cc.get("Total") or {}

    def _val(category: dict, variant: str = "Actual") -> float:
        """Safely extract ExTax cost (or scalar) from a category."""
        v = category.get(variant)
        if isinstance(v, dict):
            return v.get("ExTax", 0) or 0
        if isinstance(v, (int, float)):
            return v
        return 0

    materials = totals.get("Materials") or totals.get("MaterialsCost") or {}
    resources = totals.get("Resources") or totals.get("ResourcesCost") or {}
    labor = resources.get("Labor") or resources.get("Labour") or {}
    labor_hours = resources.get("LaborHours") or resources.get("LabourHours") or {}
    plant = resources.get("PlantAndEquipment") or {}
    overhead = resources.get("Overhead") or {}
    gross_pl = totals.get("GrossProfitLoss") or {}
    gross_margin = totals.get("GrossMargin") or {}
    net_pl = totals.get("NettProfitLoss") or totals.get("NetProfitLoss") or {}
    net_margin = totals.get("NettMargin") or totals.get("NetMargin") or {}

    mat_actual = _val(materials, "Actual")
    mat_estimate = _val(materials, "Estimate")
    lab_actual = _val(labor, "Actual")
    lab_estimate = _val(labor, "Estimate")
    overhead_actual = _val(overhead, "Actual")
    overhead_estimate = _val(overhead, "Estimate")
    gross_actual = _val(gross_pl, "Actual")
    gross_estimate = _val(gross_pl, "Estimate")

    # Budget status based on total costs vs estimate
    total_cost_actual = mat_actual + _val(resources.get("Total") or {}, "Actual")
    total_cost_estimate = mat_estimate + _val(resources.get("Total") or {}, "Estimate")

    if total_cost_estimate == 0:
        budget = "no_estimate"
    elif total_cost_actual <= total_cost_estimate * 1.05:
        budget = "on_budget" if total_cost_actual >= total_cost_estimate * 0.95 else "under_budget"
    else:
        budget = "over_budget"

    return {
        "Total_ExTax": total_obj.get("ExTax", 0) or 0,
        "Total_IncTax": total_obj.get("IncTax", 0) or 0,
        "Materials_Cost_Actual": mat_actual,
        "Materials_Cost_Estimate": mat_estimate,
        "Labour_Cost_Actual": lab_actual,
        "Labour_Cost_Estimate": lab_estimate,
        "Labour_Hours_Actual": _val(labor_hours, "Actual"),
        "Labour_Hours_Estimate": _val(labor_hours, "Estimate"),
        "Plant_Equipment_Cost_Actual": _val(plant, "Actual"),
        "Overhead_Cost_Actual": overhead_actual,
        "Overhead_Cost_Estimate": overhead_estimate,
        "Gross_Profit_Actual": gross_actual,
        "Gross_Profit_Estimate": gross_estimate,
        "Gross_Margin_Pct_Actual": _val(gross_margin, "Actual"),
        "Gross_Margin_Pct_Estimate": _val(gross_margin, "Estimate"),
        "Net_Profit_Actual": _val(net_pl, "Actual"),
        "Net_Profit_Estimate": _val(net_pl, "Estimate"),
        "Net_Margin_Pct_Actual": _val(net_margin, "Actual"),
        "Net_Margin_Pct_Estimate": _val(net_margin, "Estimate"),
        "Budget_Status": budget,
    }


class GetCostCentreTypesTool(BaseTool):
    """
    Tool for getting all cost centre types (templates/categories).
    """
    
    def __init__(self):
        """Initialize get cost centre types tool"""
        self.cost_centres_api = CostCentresAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "get_cost_centre_types"
    
    def get_description(self) -> str:
        return """Get list of all cost centre types (templates/categories) in Simpro.

These are the cost centre categories like "Roofing", "Plumbing", "Electrical", etc.

⚠️ DO NOT USE THIS TOOL when user asks about a specific job's cost centres.
Use 'get_job_section_cost_centres' for job-specific cost centre data.

ONLY use this tool when user asks:
- "What cost centre types are available?"
- "Show me cost centre categories"
- "List cost centre templates"
- "What types of cost centres exist?"

DO NOT use for: "Show me cost centres for job 21003" (use get_job_section_cost_centres instead)
"""
    
    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "columns": {
                    "type": "string",
                    "description": "Comma-separated columns to include (e.g. 'ID,Name,IncomeAccountNo')"
                }
            }
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get cost centre types with auto-pagination."""
        page_size = arguments.get("page_size", 250)
        columns = arguments.get("columns")

        all_types: List[Dict[str, Any]] = []
        current_page = 1
        while True:
            result = await self.cost_centres_api.get_cost_centre_types(
                page=current_page,
                page_size=page_size,
                columns=columns,
            )
            if isinstance(result, list):
                all_types.extend(result)
                if len(result) < page_size:
                    break
                current_page += 1
            else:
                break

        logger.info(f"get_cost_centre_types: fetched {len(all_types)} types across {current_page} page(s)")

        return {
            "success": True,
            "cost_centre_types": all_types,
            "total_fetched": len(all_types),
            "pages_fetched": current_page
        }


class GetJobSectionCostCentresTool(BaseTool):
    """
    Tool for getting ALL cost centres for a job section.
    
    THIS IS THE PRIMARY TOOL FOR JOB COST CENTRE DATA.
    """
    
    def __init__(self):
        """Initialize get job section cost centres tool"""
        self.cost_centres_api = CostCentresAPI()
        super().__init__()
    
    def get_name(self) -> str:
        return "get_job_section_cost_centres"
    
    def get_description(self) -> str:
        return """Get ALL cost centres for a specific job section.

Returns per-cost-centre data including:
- Cost centre instance ID (top-level "ID" field)
- CostCenter.ID (type ID)
- Name, financial totals (ExTax, Tax, IncTax), percent complete

DISPLAY OPTION:
- Omit display → summary totals only (fast)
- display='all' → FULL profitability breakdown per cost centre (materials,
  labor, plant, overhead, margins, profit). This fetches details for every
  cost centre in the section server-side — no separate detail calls needed.

PREFERRED WORKFLOW for profitability / margins / materials:
1. Call get_job_sections → get section_id(s)
2. Call THIS TOOL with display='all' for each section → full breakdown

WORKFLOW for cost centre listing (summary only):
1. Call get_job_sections → get section_id(s)
2. Call THIS TOOL (without display) for each section → names, IDs, totals

Examples:
- "List cost centres for job 21003" → use this tool (no display)
- "Cost centre profitability for job 21003" → use this tool with display='all'
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
                    "description": "The ID of the section (get this from get_job_sections first)"
                },
                "display": {
                    "type": "string",
                    "description": "Set to 'all' to include full profitability breakdown (materials, labor, margins, profit) for each cost centre in this section. Omit for summary totals only.",
                    "enum": ["all"]
                }
            },
            "required": ["job_id", "section_id"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get job section cost centres, optionally enriching with full details."""
        job_id = arguments["job_id"]
        section_id = arguments["section_id"]
        display = arguments.get("display")

        # Step 1: Fetch all cost centres for this section
        result = await self.cost_centres_api.get_job_section_cost_centres(
            job_id=job_id,
            section_id=section_id
        )

        # Auto-correct: if result is empty, the LLM may have passed a
        # cost_centre_id as the section_id.  Scan all real sections to find
        # the one that owns this ID and re-query.
        if isinstance(result, list) and len(result) == 0:
            from src.simpro.api.jobs import JobsAPI
            jobs_api = JobsAPI()
            sections = await jobs_api.get_job_sections(job_id)
            real_section_ids = [s["ID"] for s in sections if isinstance(s, dict) and "ID" in s]

            if section_id not in real_section_ids:
                logger.warning(
                    f"section_id {section_id} is not a valid section for job {job_id} "
                    f"(valid: {real_section_ids}) — scanning for auto-correction"
                )
                for sid in real_section_ids:
                    ccs = await self.cost_centres_api.get_job_section_cost_centres(job_id, sid)
                    cc_ids = [cc["ID"] for cc in ccs if isinstance(cc, dict) and "ID" in cc]
                    _cache_cc_section(job_id, sid, cc_ids)
                    if cc_ids:
                        # Found the section with cost centres — use it
                        logger.warning(
                            f"Auto-corrected section_id: {section_id} → {sid} (job {job_id})"
                        )
                        section_id = sid
                        result = ccs
                        break

        # Build instance ID list and cache the section mapping
        instance_ids = []
        if isinstance(result, list):
            for cc in result:
                if isinstance(cc, dict) and "ID" in cc:
                    instance_ids.append(cc["ID"])
        _cache_cc_section(job_id, section_id, instance_ids)

        # Step 2: If display='all', fetch full details for each CC server-side
        if display == "all" and instance_ids:
            detail_tasks = [
                self.cost_centres_api.get_job_cost_centre_details(
                    job_id=job_id,
                    section_id=section_id,
                    cost_centre_id=cc_id,
                    display="all",
                )
                for cc_id in instance_ids
            ]
            details = await asyncio.gather(*detail_tasks, return_exceptions=True)

            # Replace summary entries with full detail entries
            enriched: List[Dict[str, Any]] = []
            for cc_id, detail in zip(instance_ids, details):
                if isinstance(detail, Exception):
                    logger.warning(
                        f"Failed to fetch details for CC {cc_id} in section "
                        f"{section_id}: {detail}"
                    )
                    # Keep the summary entry as fallback
                    summary = next(
                        (cc for cc in result if cc.get("ID") == cc_id), None
                    )
                    if summary:
                        enriched.append(summary)
                else:
                    enriched.append(detail)
            result = enriched

        # Add flat financial summary when display='all' so the LLM has
        # simple key-value pairs instead of 5-level-deep nested JSON.
        if display == "all" and isinstance(result, list):
            for cc in result:
                if isinstance(cc, dict) and cc.get("Totals"):
                    cc["_financial_summary"] = _extract_financial_summary(cc)

        return {
            "success": True,
            "cost_centres": result,
            "job_id": job_id,
            "section_id": section_id,
            "count": len(result) if isinstance(result, list) else 0,
            "instance_ids_in_this_section": instance_ids,
            "display": display or "summary",
        }


class GetJobCostCentreDetailsTool(BaseTool):
    """
    Tool for getting DETAILED data for a single cost centre,
    including full profitability breakdown when display=all.
    """

    def __init__(self):
        """Initialize get job cost centre details tool"""
        self.cost_centres_api = CostCentresAPI()
        super().__init__()

    def get_name(self) -> str:
        return "get_job_cost_centre_details"

    def get_description(self) -> str:
        return f"""Get DETAILED data for a single cost centre by its instance ID.

{get_api_hint("display_all")}

With display='all': materials, labor, plant, overhead, margins, profit breakdown.
Without: basic cost centre info only.

NOTE: For BULK profitability (all CCs in a section), prefer calling
get_job_section_cost_centres with display='all' — it fetches details for every
CC in one call and avoids ID mix-ups.

Use THIS tool only for a SINGLE cost centre lookup by instance ID.
cost_centre_id = INSTANCE ID (top-level "ID" from get_job_section_cost_centres).
The server will auto-correct the section_id if the CC belongs to a different section.
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
                    "description": "The cost centre INSTANCE ID (e.g., 116713, NOT the type ID like 3)"
                },
                "display": {
                    "type": "string",
                    "description": "Set to 'all' for full profitability breakdown (materials, labor, margins, profit)",
                    "enum": ["all"]
                }
            },
            "required": ["job_id", "section_id", "cost_centre_id"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute get job cost centre details with auto-correction."""
        job_id = arguments["job_id"]
        section_id = arguments["section_id"]
        cost_centre_id = arguments["cost_centre_id"]
        display = arguments.get("display")

        # Auto-correct section_id if cache knows the real owner
        section_id = await _resolve_section(
            job_id, section_id, cost_centre_id, self.cost_centres_api
        )

        try:
            result = await self.cost_centres_api.get_job_cost_centre_details(
                job_id=job_id,
                section_id=section_id,
                cost_centre_id=cost_centre_id,
                display=display,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                # Cache miss — discover correct section by scanning all sections
                correct = await _discover_correct_section(
                    job_id, cost_centre_id, self.cost_centres_api
                )
                if correct is not None and correct != section_id:
                    logger.warning(
                        f"Discovered correct section for CC {cost_centre_id}: "
                        f"{section_id} → {correct} (job {job_id})"
                    )
                    result = await self.cost_centres_api.get_job_cost_centre_details(
                        job_id=job_id,
                        section_id=correct,
                        cost_centre_id=cost_centre_id,
                        display=display,
                    )
                    section_id = correct
                else:
                    raise  # genuinely not found
            else:
                raise

        # Add flat financial summary for display='all'
        if display == "all" and isinstance(result, dict) and result.get("Totals"):
            result["_financial_summary"] = _extract_financial_summary(result)

        return {
            "success": True,
            "cost_centre": result,
            "job_id": job_id,
            "section_id": section_id,
            "cost_centre_id": cost_centre_id
        }


class GetCostCentreCatalogItemsTool(BaseTool):
    """
    Tool for getting catalog items (parts/materials) for a cost centre.
    """

    def __init__(self):
        self.cost_centres_api = CostCentresAPI()
        super().__init__()

    def get_name(self) -> str:
        return "get_cost_centre_catalog_items"

    def get_description(self) -> str:
        return """Get catalog items (parts/materials) assigned to a specific cost centre.

Returns material names, part numbers, quantities, and unit costs.

Use this tool when you need to know what materials/parts are planned
for a cost centre, e.g. to build a work order or contractor job.

Requires: job_id, section_id, cost_centre_id (instance ID).
Workflow: get_job_sections -> get_job_section_cost_centres -> THIS TOOL
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
                    "description": "The cost centre instance ID"
                }
            },
            "required": ["job_id", "section_id", "cost_centre_id"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        job_id = arguments["job_id"]
        section_id = arguments["section_id"]
        cost_centre_id = arguments["cost_centre_id"]

        section_id = await _resolve_section(
            job_id, section_id, cost_centre_id, self.cost_centres_api
        )

        try:
            result = await self.cost_centres_api.get_cost_centre_catalog_items(
                job_id=job_id,
                section_id=section_id,
                cost_centre_id=cost_centre_id,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                correct = await _discover_correct_section(
                    job_id, cost_centre_id, self.cost_centres_api
                )
                if correct is not None and correct != section_id:
                    logger.warning(
                        f"Discovered correct section for CC {cost_centre_id}: "
                        f"{section_id} → {correct} (catalog items)"
                    )
                    result = await self.cost_centres_api.get_cost_centre_catalog_items(
                        job_id=job_id,
                        section_id=correct,
                        cost_centre_id=cost_centre_id,
                    )
                    section_id = correct
                else:
                    raise
            else:
                raise

        return {
            "success": True,
            "catalog_items": result,
            "job_id": job_id,
            "section_id": section_id,
            "cost_centre_id": cost_centre_id,
            "count": len(result) if isinstance(result, list) else 0
        }


class GetCostCentreLabourItemsTool(BaseTool):
    """
    Tool for getting labour items for a cost centre.
    """

    def __init__(self):
        self.cost_centres_api = CostCentresAPI()
        super().__init__()

    def get_name(self) -> str:
        return "get_cost_centre_labour_items"

    def get_description(self) -> str:
        return """Get labour items assigned to a specific cost centre.

Returns labour descriptions, hours, and rates.

Use this tool when you need to know what labour is planned
for a cost centre, e.g. to build a work order or contractor job.

Requires: job_id, section_id, cost_centre_id (instance ID).
Workflow: get_job_sections -> get_job_section_cost_centres -> THIS TOOL
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
                    "description": "The cost centre instance ID"
                }
            },
            "required": ["job_id", "section_id", "cost_centre_id"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        job_id = arguments["job_id"]
        section_id = arguments["section_id"]
        cost_centre_id = arguments["cost_centre_id"]

        section_id = await _resolve_section(
            job_id, section_id, cost_centre_id, self.cost_centres_api
        )

        try:
            result = await self.cost_centres_api.get_cost_centre_labour_items(
                job_id=job_id,
                section_id=section_id,
                cost_centre_id=cost_centre_id,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                correct = await _discover_correct_section(
                    job_id, cost_centre_id, self.cost_centres_api
                )
                if correct is not None and correct != section_id:
                    logger.warning(
                        f"Discovered correct section for CC {cost_centre_id}: "
                        f"{section_id} → {correct} (labour items)"
                    )
                    result = await self.cost_centres_api.get_cost_centre_labour_items(
                        job_id=job_id,
                        section_id=correct,
                        cost_centre_id=cost_centre_id,
                    )
                    section_id = correct
                else:
                    raise
            else:
                raise

        return {
            "success": True,
            "labour_items": result,
            "job_id": job_id,
            "section_id": section_id,
            "cost_centre_id": cost_centre_id,
            "count": len(result) if isinstance(result, list) else 0
        }


class GetCostCentreOneOffItemsTool(BaseTool):
    """
    Tool for getting one-off (custom) items for a cost centre.
    """

    def __init__(self):
        self.cost_centres_api = CostCentresAPI()
        super().__init__()

    def get_name(self) -> str:
        return "get_cost_centre_one_off_items"

    def get_description(self) -> str:
        return """Get one-off (custom) items assigned to a specific cost centre.

Returns custom/ad-hoc items that don't come from the catalog.

Requires: job_id, section_id, cost_centre_id (instance ID).
Workflow: get_job_sections -> get_job_section_cost_centres -> THIS TOOL
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
                    "description": "The cost centre instance ID"
                }
            },
            "required": ["job_id", "section_id", "cost_centre_id"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        job_id = arguments["job_id"]
        section_id = arguments["section_id"]
        cost_centre_id = arguments["cost_centre_id"]

        section_id = await _resolve_section(
            job_id, section_id, cost_centre_id, self.cost_centres_api
        )

        try:
            result = await self.cost_centres_api.get_cost_centre_one_off_items(
                job_id=job_id,
                section_id=section_id,
                cost_centre_id=cost_centre_id,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                correct = await _discover_correct_section(
                    job_id, cost_centre_id, self.cost_centres_api
                )
                if correct is not None and correct != section_id:
                    logger.warning(
                        f"Discovered correct section for CC {cost_centre_id}: "
                        f"{section_id} → {correct} (one-off items)"
                    )
                    result = await self.cost_centres_api.get_cost_centre_one_off_items(
                        job_id=job_id,
                        section_id=correct,
                        cost_centre_id=cost_centre_id,
                    )
                    section_id = correct
                else:
                    raise
            else:
                raise

        return {
            "success": True,
            "one_off_items": result,
            "job_id": job_id,
            "section_id": section_id,
            "cost_centre_id": cost_centre_id,
            "count": len(result) if isinstance(result, list) else 0
        }


class GetCostCentrePrebuildItemsTool(BaseTool):
    """
    Tool for getting prebuild items for a cost centre.
    """

    def __init__(self):
        self.cost_centres_api = CostCentresAPI()
        super().__init__()

    def get_name(self) -> str:
        return "get_cost_centre_prebuild_items"

    def get_description(self) -> str:
        return """Get prebuild items assigned to a specific cost centre.

Returns prebuild items with Prebuild info (ID, PartNo, Name), quantities, sell prices, and claimed amounts.

Requires: job_id, section_id, cost_centre_id (instance ID).
Workflow: get_job_sections -> get_job_section_cost_centres -> THIS TOOL
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
                    "description": "The cost centre instance ID"
                }
            },
            "required": ["job_id", "section_id", "cost_centre_id"]
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        job_id = arguments["job_id"]
        section_id = arguments["section_id"]
        cost_centre_id = arguments["cost_centre_id"]

        section_id = await _resolve_section(
            job_id, section_id, cost_centre_id, self.cost_centres_api
        )

        try:
            result = await self.cost_centres_api.get_cost_centre_prebuild_items(
                job_id=job_id,
                section_id=section_id,
                cost_centre_id=cost_centre_id,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                correct = await _discover_correct_section(
                    job_id, cost_centre_id, self.cost_centres_api
                )
                if correct is not None and correct != section_id:
                    logger.warning(
                        f"Discovered correct section for CC {cost_centre_id}: "
                        f"{section_id} → {correct} (prebuild items)"
                    )
                    result = await self.cost_centres_api.get_cost_centre_prebuild_items(
                        job_id=job_id,
                        section_id=correct,
                        cost_centre_id=cost_centre_id,
                    )
                    section_id = correct
                else:
                    raise
            else:
                raise

        return {
            "success": True,
            "prebuild_items": result,
            "job_id": job_id,
            "section_id": section_id,
            "cost_centre_id": cost_centre_id,
            "count": len(result) if isinstance(result, list) else 0
        }