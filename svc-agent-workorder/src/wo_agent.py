# svc-agent-workorder/src/wo_agent.py
"""
Work Order Agent — Prepares and creates contractor jobs in Simpro.

Two-phase workflow:
  Phase A (Prepare): Fetch materials / labour from cost centres,
      generate a downloadable Excel for user review.
  Phase B (Create): Accept the re-uploaded (edited) Excel via svc-extractor,
      parse included items, build contractor-job payloads, return for creation.

Triggers:
  - Schedule-based: "create work orders for today's roofing schedules"
  - Direct: "create work order for job 20990, cost centre 116534, contractor ABC Roofing"

The agent reads an SOP.docx for all organisation-specific defaults, department
mappings, description format, and business rules.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from docx import Document

try:
    from config import (
        SOP_DOCX_PATH,
        MAX_CLARIFICATIONS,
        WO_EXCEL_COLUMNS,
        WO_EXCEL_META_COLUMNS,
    )
except ImportError:
    SOP_DOCX_PATH = os.getenv(
        "WO_SOP_DOCX_PATH",
        os.path.join(os.path.dirname(__file__), "sop", "wo_creation_sop.md"),
    )
    MAX_CLARIFICATIONS = 5
    WO_EXCEL_COLUMNS = [
        "ItemID", "ItemName", "Type", "Quantity", "UnitCost", "Total", "Include",
    ]
    WO_EXCEL_META_COLUMNS = [
        "JobID", "SectionID", "CostCentreID", "CostCentreName",
        "ContractorID", "ContractorName",
    ]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Centralized imports
from utils.mcp_executor import MCPToolExecutor
from utils.entity_resolver import (
    EntityResolver, ResolutionError, AmbiguousResolutionError, MissingFieldError,
    BatchedClarificationError,
)
from utils.agent_state import AgentExecutionState, create_agent_state


# ═══════════════════════════════════════════════════════════════════════════
# Simpro _href parser
# ═══════════════════════════════════════════════════════════════════════════

def _parse_href(href: str) -> Dict[str, Optional[int]]:
    """
    Parse _href from Simpro contractor job response to extract parent IDs.

    Example href:
        '/api/v1.0/companies/2/jobs/20990/sections/1/costCenters/116534/contractorJobs/'
    Returns:
        {"job_id": 20990, "section_id": 1, "cost_centre_id": 116534}
    """
    result: Dict[str, Optional[int]] = {}
    for key, pattern in [
        ("job_id", r"(?:jobs|quotes)/(\d+)"),
        ("section_id", r"sections/(\d+)"),
        ("cost_centre_id", r"costCenters/(\d+)"),
    ]:
        m = re.search(pattern, href)
        result[key] = int(m.group(1)) if m else None
    return result


# ═══════════════════════════════════════════════════════════════════════════
# SOP helpers
# ═══════════════════════════════════════════════════════════════════════════

def _read_sop(path: Optional[str] = None, sop_override: Optional[str] = None, max_chars: int = 32_000) -> str:
    """Read SOP to plain text. Prefers sop_override if provided."""
    if sop_override:
        logger.info("[SOP] Using DB override SOP for workorder (org-specific)")
        return sop_override  # already validated at upload time
    path = path or SOP_DOCX_PATH
    if not path or not os.path.exists(path):
        logger.warning(f"[SOP] Default workorder SOP not found at {path} — using empty SOP")
        return ""
    logger.info(f"[SOP] Using default workorder SOP from file: {path}")
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        doc = Document(path)
        text = "\n".join(p.text for p in doc.paragraphs if p.text)
        return " ".join(text.split())[:max_chars]
    with open(path, "r", encoding="utf-8") as f:
        return f.read()[:max_chars]


# ═══════════════════════════════════════════════════════════════════════════
# LLM: SOP policy extraction
# ═══════════════════════════════════════════════════════════════════════════

_WO_POLICY_SYSTEM = """\
You are a Work Order (Contractor Job) Planning Agent for Simpro ERP.

You MUST treat the SOP as the primary source of truth for all defaults, business
rules, department mappings, description format, and item inclusion policies.

Your job is to read the SOP and the user's request and output a SMALL JSON policy
that describes HOW contractor jobs should be built.

Return STRICT JSON with these keys:
{
  "include_description": true | false,
  "description_format": "itemized" | "summary",
  "defaults": {
    "TaxCodeID": <int>,
    "ContractorSupplyMaterials": <bool>,
    "DateIssued": "YYYY-MM-DD",
    ...any other SOP-defined defaults...
  },
  "department_mapping": {
    "<IncomeAccountNo>": "<DepartmentName>",
    ...
  },
  "item_inclusion_rules": {
    "include_catalog": true,
    "include_labour": true,
    "include_one_off": true,
    "exclusion_patterns": []
  },
  "missing": []
}

RULES:
- NEVER invent defaults. Extract ONLY from the SOP text.
- "include_description": whether to add an itemized description to each work order.
  Default true. Set false only if the SOP explicitly says to omit descriptions.
- DateIssued: MUST be YYYY-MM-DD. Resolve all relative dates ("next friday", "tomorrow") to actual YYYY-MM-DD. Default to today's date if not specified.
- "department_mapping" is OPTIONAL — only populate it if the SOP explicitly defines
  IncomeAccountNo-to-department mappings. If the SOP does not define them, return
  an empty object {}. NEVER add department_mapping to "missing".
  Department mapping is only needed when the user explicitly requests a specific
  department filter. The system resolves departments at runtime via the Simpro API
  using the SOP mapping as a first-pass lookup.
- Similarly, do NOT add contractor resolution or any data that the system can look up
  via Simpro API to "missing".
- "missing" should ONLY contain genuinely ambiguous business-logic questions that
  cannot be resolved from the SOP text or from Simpro API data at runtime.
"""


def _llm_plan_policy(
    llm_chat: Callable,
    sop_text: str,
    user_text: str,
    extracted_summary: str = "",
) -> Dict[str, Any]:
    """Ask LLM to derive work-order policy from SOP + user request."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    user_msg = (
        f"Today's date is {today_str}.\n\n"
        f"SOP (verbatim):\n{sop_text}\n\n"
        f"USER MESSAGE:\n{user_text}\n\n"
    )
    if extracted_summary:
        user_msg += f"ATTACHMENT SUMMARY:\n{extracted_summary}\n\n"

    user_msg += (
        "TASK:\n"
        "1) Extract ALL defaults from the SOP.\n"
        "2) Build department_mapping from the SOP (IncomeAccountNo → dept name) "
        "ONLY if the SOP explicitly defines these mappings. Otherwise return empty {}.\n"
        "3) Item inclusion rules from SOP.\n"
        "4) Description format from SOP.\n"
        "5) Put genuinely ambiguous items in 'missing' — but NEVER include "
        "department_mapping, contractor resolution, or anything the system can "
        "look up via Simpro API.\n"
        "Return STRICT JSON."
    )

    out = llm_chat(
        [{"role": "system", "content": _WO_POLICY_SYSTEM},
         {"role": "user", "content": user_msg}],
        response_format={"type": "json_object"},
        temperature=0,
    )
    try:
        return json.loads(out or "{}")
    except Exception:
        return {
            "include_description": True,
            "description_format": "itemized",
            "defaults": {},
            "department_mapping": {},
            "item_inclusion_rules": {
                "include_catalog": True,
                "include_labour": True,
                "include_one_off": True,
                "exclusion_patterns": [],
            },
            "missing": ["LLM output was not valid JSON; please check SOP."],
        }


# ═══════════════════════════════════════════════════════════════════════════
# LLM: Parse chat-based work-order request
# ═══════════════════════════════════════════════════════════════════════════

_WO_PARSE_SYSTEM = """\
You are a work-order request parser for Simpro ERP.
Extract work order details from natural language.

Return ONLY valid JSON:
{
  "action": "create" | "update" | "delete",
  "trigger": "schedule" | "direct",
  "date": "YYYY-MM-DD" | null,
  "department": "<string>" | null,
  "job_id": <int> | null,
  "section_id": <int> | null,
  "cost_centre_id": <int> | null,
  "cost_centre_name": "<string>" | null,
  "contractor_name": "<string>" | null,
  "contractor_id": <int> | null,
  "contractor_job_id": <int> | null,
  "contractor_job_ids": [<int>, ...] | null,
  "fields_to_update": {
    "materials": <float> | null,
    "labour": <float> | null,
    "description": "<string>" | null,
    "tax_code_id": <int> | null,
    "date_issued": "YYYY-MM-DD" | null
  } | null
}

RULES:
- action="create" when user wants to create, generate, or prepare work orders (default).
- action="update" when user wants to modify, change, or update an existing work order or contractor job.
- action="delete" when user wants to delete, remove, or cancel a work order or contractor job.
- For CREATE: trigger="schedule" when user mentions schedules, today's work, or department-wide WOs,
  OR when the user says "for them"/"for those"/"for these" referring to a previous schedule/MCP result,
  OR when FOLLOW-UP FIELD BRIDGE provides job_id as a LIST (multiple jobs → schedule-based batch).
  trigger="direct" when user specifies a SINGLE job ID, cost centre, and/or contractor directly.
- For UPDATE: extract fields_to_update with ONLY the fields the user explicitly wants to change.
  e.g. "change materials to $5000" → {"materials": 5000.0}, other fields null.
- For DELETE: fields_to_update should be null.
- Extract contractor_job_id when user mentions a SINGLE contractor job or work order ID
  (e.g. "contractor job 12345", "work order 67890", "CJ 111").
- Extract contractor_job_ids (array) when user mentions MULTIPLE IDs
  (e.g. "delete work orders 46449 & 46448", "remove CJ 111, 222, 333").
  Set contractor_job_id to null when using contractor_job_ids.
- ALWAYS resolve ALL date expressions to YYYY-MM-DD. NEVER pass relative dates like "next tuesday" as-is.
  "today" → today's YYYY-MM-DD, "tomorrow"/"next friday"/"march 15" → calculate actual YYYY-MM-DD.
- If the user says "roofing department" set department="roofing".
- Only extract what the user explicitly provides. Do NOT guess IDs.
- FOLLOW-UP / RETRY: If the user says "try again", "retry", "reload", "complete", "finish", "redo", or similar, check conversation history for the ORIGINAL request details (IDs, action, contractor names). Extract those same parameters — this is NOT guessing, the user is explicitly referring to the previous operation.

CORRECTION & REFERENCE PATTERNS:
When the user corrects or references a previous work order operation, resolve values from conversation history.

- "wrong contractor, use X" → action="update", extract contractor_name="X" and contractor_job_id from history.
- "change the description to Y" → action="update", fields_to_update={"description": "Y"}, contractor_job_id from history.
- "change materials to $5000" → action="update", fields_to_update={"materials": 5000}, contractor_job_id from history.
- "same work order for job Z" → action="create", copy all params from history but change job_id=Z.
- "do the same for the plumbing cost centre" → action="create", copy params, change cost_centre/department.
- "delete it" / "delete the one we just created" → action="delete", contractor_job_id from history.
- "now do job 10680" → action="create", reuse previous trigger/department/contractor, change job_id.

When resolving from history, look for assistant messages containing "CJ <id>", "contractor_job_id=", "job_id=", or "contractor=". Extract IDs and names from the structured history format.

RELATIVE VALUE RESOLUTION:
When the user says "same contractor", "same department", "same materials amount", etc., extract ACTUAL values from:
1. FOLLOW-UP FIELD BRIDGE (if present in the prompt) — pre-resolved values, use first.
2. Conversation history — look for contractor=, department=, job_id=, materials=, labour= in assistant messages.
NEVER pass relative phrases as field values.

CROSS-PATH DATA: History may contain results from OTHER agents or MCP queries, not just work orders.
Extract common fields (job_id, cost_centre, contractor) from ANY history format:
- Schedule: "COMPLETED CREATE schedule: job_id=22601, section_id=50123, cost_centre_id=116534"
- Invoice: "COMPLETED CREATE invoice: job_id=10675, cost_centres=[116534 (Drainage)]"
- MCP data: "[Data Context — N items] ID=22601 Name=Bloomfield"
When user says "work order for that job" and history shows a schedule/invoice/MCP result with job_id, use it.

Example - CREATE with "same contractor" from history:
Previous assistant: "[workorder agent succeeded: action=create, CREATED CJ 46450 (MTS Roofing)]"
Input: "create work orders for job 10680 same contractor"
Output: {"action": "create", "trigger": "direct", "job_id": 10680, "contractor_name": "MTS Roofing"}
"""


def _parse_chat_wo_request(
    llm_chat: Callable,
    user_text: str,
    sop_text: str = "",
    conversation_history: Optional[List[Dict[str, str]]] = None,
    hints: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Parse natural-language WO request into structured params."""
    today_str = datetime.now().strftime("%Y-%m-%d")

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

    prompt = f"Today's date is {today_str}.{reuse_hint}\n\nExtract work order data from:\n{user_text}"

    messages: List[Dict[str, str]] = [
        {"role": "system", "content": _WO_PARSE_SYSTEM},
    ]
    if conversation_history:
        messages.extend(conversation_history[-6:])
    messages.append({"role": "user", "content": prompt})

    try:
        out = llm_chat(messages, response_format={"type": "json_object"}, temperature=0)
        parsed = json.loads(out)
        logger.info(f"✅ Parsed WO request: {json.dumps(parsed, default=str)[:300]}")
        return parsed
    except Exception as e:
        logger.error(f"❌ WO parse failed: {e}")
        return {"trigger": "direct", "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════
# Contractor resolution — delegates to central EntityResolver
# ═══════════════════════════════════════════════════════════════════════════

async def _resolve_contractor(
    mcp_executor: MCPToolExecutor,
    contractor_name: Optional[str] = None,
    contractor_id: Optional[int] = None,
    llm_chat: Optional[Callable] = None,
) -> Dict[str, Any]:
    """Resolve contractor name → {ID, Name} via central EntityResolver."""
    resolver = EntityResolver(mcp_executor, llm_chat=llm_chat)
    result = await resolver.resolve_contractor(
        name=contractor_name,
        contractor_id=contractor_id,
    )
    return {"ID": result["id"], "Name": result["name"]}


# ═══════════════════════════════════════════════════════════════════════════
# Department resolution
# ═══════════════════════════════════════════════════════════════════════════

async def _resolve_department(
    mcp_executor: MCPToolExecutor,
    department_query: str,
    sop_policy: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Resolve a department name to cost-centre type IDs.

    Strategies:
      1. SOP department_mapping (IncomeAccountNo → dept name)
      2. EntityResolver.resolve_cost_centre_type() for fuzzy name match
    """
    from utils.fuzzy_match import fuzzy_match_name

    dept_mapping = sop_policy.get("department_mapping", {})

    # Strategy 1: SOP-based mapping (fuzzy match department name against mapping values)
    matching_accounts = []
    if dept_mapping:
        dept_names = list(dept_mapping.values())
        matched_name = fuzzy_match_name(department_query, dept_names, threshold=60)
        if matched_name:
            matching_accounts = [
                acct for acct, dname in dept_mapping.items()
                if dname == matched_name
            ]

    # Fetch cost centre types (needed for both SOP account matching and name fallback)
    cc_types = await mcp_executor.call_tool("get_cost_centre_types", {
        "columns": "ID,Name,IncomeAccountNo",
        "page_size": 250,
    })
    type_list = cc_types.get("cost_centre_types", cc_types) if isinstance(cc_types, dict) else cc_types
    if not isinstance(type_list, list):
        type_list = []

    matches = []

    # Match by IncomeAccountNo from SOP mapping
    if matching_accounts:
        for cc_type in type_list:
            acct_no = str(cc_type.get("IncomeAccountNo", ""))
            if acct_no in matching_accounts:
                matches.append(cc_type)

    # If no SOP matches, use central EntityResolver for fuzzy name matching
    if not matches:
        try:
            resolver = EntityResolver(mcp_executor)
            result = await resolver.resolve_cost_centre_type(name=department_query)
            # Find the full cc_type dict matching the resolved ID
            for cc_type in type_list:
                if cc_type.get("ID") == result["id"]:
                    matches.append(cc_type)
                    break
            # If not in type_list (shouldn't happen), build minimal dict
            if not matches:
                matches.append({"ID": result["id"], "Name": result["name"]})
        except (ResolutionError, AmbiguousResolutionError):
            pass  # No match found — will return empty list

    return matches


# ═══════════════════════════════════════════════════════════════════════════
# Section reverse-lookup — delegates to central EntityResolver
# ═══════════════════════════════════════════════════════════════════════════

async def _resolve_section_for_cost_centre(
    mcp_executor: MCPToolExecutor,
    job_id: int,
) -> List[Dict[str, Any]]:
    """Get all sections for a job. Returns [{ID, Name}, ...]."""
    resolver = EntityResolver(mcp_executor)
    return await resolver._fetch_sections(job_id, context="job")


async def _find_cost_centre_in_sections(
    mcp_executor: MCPToolExecutor,
    job_id: int,
    cost_centre_id: int,
) -> Optional[int]:
    """Reverse-lookup: find which section_id contains a given cost_centre_id."""
    resolver = EntityResolver(mcp_executor)
    return await resolver.find_section_for_cost_centre(job_id, cost_centre_id)


# ═══════════════════════════════════════════════════════════════════════════
# Contractor job resolution (for UPDATE/DELETE)
# ═══════════════════════════════════════════════════════════════════════════

async def _resolve_contractor_job(
    mcp_executor: MCPToolExecutor,
    llm_chat: Callable,
    parsed: Dict[str, Any],
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """
    Resolve a contractor job for UPDATE/DELETE operations.

    Uses crossroads for disambiguation when multiple matches exist.

    Returns:
        {
            "contractor_job_id": int,
            "job_id": int,
            "section_id": int,
            "cost_centre_id": int,
            "contractor_id": int | None,
            "contractor_name": str,
            "status": str,
            "materials": float,
            "labour": float,
        }

    Raises ValueError if the contractor job cannot be resolved.
    """
    contractor_job_id = parsed.get("contractor_job_id")
    job_id = parsed.get("job_id")
    section_id = parsed.get("section_id")
    cost_centre_id = parsed.get("cost_centre_id")
    contractor_name = parsed.get("contractor_name")

    # ─── Strategy 1: Direct lookup by contractor_job_id ───────────────
    if contractor_job_id:
        logger.info(f"🔍 Resolving CJ by ID: {contractor_job_id}")
        try:
            result = await mcp_executor.call_tool(
                "get_contractor_job_details",
                {"contractor_job_id": contractor_job_id},
            )
            cj = result.get("contractor_job", result) if isinstance(result, dict) else result
        except Exception as e:
            raise ValueError(f"Contractor job {contractor_job_id} not found: {e}")

        # Parse _href to get parent IDs
        href = cj.get("_href", "")
        logger.info(f"📎 CJ _href: {href}")
        parent_ids = _parse_href(href)
        logger.info(f"📎 Parsed parent IDs: {parent_ids}")

        contractor_obj = cj.get("Contractor", {})
        if isinstance(contractor_obj, dict):
            cid = contractor_obj.get("ID")
            cname = contractor_obj.get("Name", "")
        else:
            cid = contractor_obj
            cname = contractor_name or ""

        return {
            "contractor_job_id": cj.get("ID", contractor_job_id),
            "job_id": parent_ids.get("job_id") or job_id,
            "section_id": parent_ids.get("section_id") or section_id,
            "cost_centre_id": parent_ids.get("cost_centre_id") or cost_centre_id,
            "contractor_id": cid,
            "contractor_name": cname,
            "status": cj.get("Status", ""),
            "materials": cj.get("Materials", 0),
            "labour": cj.get("Labor", 0),
        }

    # ─── Strategy 2: Lookup by job + section + cost_centre ────────────
    # If section_id missing but job+cc known, resolve it first
    if job_id and cost_centre_id and not section_id:
        logger.info(f"🔍 Resolving section for job={job_id}, cc={cost_centre_id}")
        section_id = await _find_cost_centre_in_sections(
            mcp_executor, job_id, cost_centre_id,
        )
        if not section_id:
            raise ValueError(
                f"Could not find which section contains cost centre {cost_centre_id} "
                f"in job {job_id}. Please provide the section ID."
            )

    if job_id and section_id and cost_centre_id:
        logger.info(
            f"🔍 Listing CJs for job={job_id}, sec={section_id}, cc={cost_centre_id}"
        )
        try:
            result = await mcp_executor.call_tool(
                "get_contractor_jobs_by_cost_centre",
                {
                    "job_id": job_id,
                    "section_id": section_id,
                    "cost_centre_id": cost_centre_id,
                    "columns": "ID,Status,Contractor,Materials,Labor",
                },
            )
            cj_list = result.get("contractor_jobs", result) if isinstance(result, dict) else result
            if not isinstance(cj_list, list):
                cj_list = []
        except Exception as e:
            raise ValueError(
                f"Failed to list contractor jobs for job {job_id}, "
                f"section {section_id}, cc {cost_centre_id}: {e}"
            )

        if not cj_list:
            raise ValueError(
                f"No contractor jobs found for job {job_id}, "
                f"section {section_id}, cost centre {cost_centre_id}."
            )

        # Filter by contractor name if provided (fuzzy match)
        matches = cj_list
        if contractor_name:
            from utils.fuzzy_match import fuzzy_match_entities

            # Build flat candidates from nested Contractor dicts
            cj_candidates = []
            for cj in cj_list:
                c_obj = cj.get("Contractor", {})
                if isinstance(c_obj, dict) and c_obj.get("ID") and c_obj.get("Name"):
                    cj_candidates.append({"ID": c_obj["ID"], "Name": c_obj["Name"]})
            fm = fuzzy_match_entities(contractor_name, cj_candidates, source="contractor_jobs")
            # Only keep matches close to the best score to avoid partial word matches
            if fm:
                best_score = fm[0]["score"]
                matched_ids = {m["id"] for m in fm if m["score"] >= best_score - 10}
            else:
                matched_ids = set()
            filtered = [
                cj for cj in cj_list
                if isinstance(cj.get("Contractor"), dict)
                and cj["Contractor"].get("ID") in matched_ids
            ]
            if filtered:
                matches = filtered

        if len(matches) == 1:
            cj = matches[0]
            contractor_obj = cj.get("Contractor", {})
            if isinstance(contractor_obj, dict):
                cid = contractor_obj.get("ID")
                cname = contractor_obj.get("Name", "")
            else:
                cid = contractor_obj
                cname = contractor_name or ""

            return {
                "contractor_job_id": cj.get("ID"),
                "job_id": job_id,
                "section_id": section_id,
                "cost_centre_id": cost_centre_id,
                "contractor_id": cid,
                "contractor_name": cname,
                "status": cj.get("Status", ""),
                "materials": cj.get("Materials", 0),
                "labour": cj.get("Labor", 0),
            }

        # Multiple matches — use crossroads ambiguous_match
        if len(matches) > 1:
            logger.info(f"🔀 {len(matches)} CJs match — using crossroads to disambiguate")
            try:
                from utils.crossroads import resolve_with_context

                candidates = []
                for cj in matches[:10]:
                    contractor_obj = cj.get("Contractor", {})
                    candidates.append({
                        "id": cj.get("ID"),
                        "contractor": (
                            contractor_obj.get("Name", "Unknown")
                            if isinstance(contractor_obj, dict)
                            else f"ID {contractor_obj}"
                        ),
                        "status": cj.get("Status", ""),
                        "materials": cj.get("Materials", 0),
                        "labour": cj.get("Labor", 0),
                    })

                cr = await resolve_with_context(
                    crossroad_type="ambiguous_match",
                    question=(
                        "Multiple contractor jobs found on this cost centre. "
                        "Which one should be targeted?"
                    ),
                    context={
                        "candidates": candidates,
                    },
                    tracker=mcp_executor.tracker,
                    agent_name="workorder",
                    llm_chat=llm_chat,
                )

                selected_id = cr.get("fields", {}).get("selected_id")
                if selected_id and cr.get("decision") == "select":
                    selected = next(
                        (c for c in matches if c.get("ID") == selected_id), None
                    )
                    if selected:
                        contractor_obj = selected.get("Contractor", {})
                        if isinstance(contractor_obj, dict):
                            cid = contractor_obj.get("ID")
                            cname = contractor_obj.get("Name", "")
                        else:
                            cid = contractor_obj
                            cname = ""
                        return {
                            "contractor_job_id": selected.get("ID"),
                            "job_id": job_id,
                            "section_id": section_id,
                            "cost_centre_id": cost_centre_id,
                            "contractor_id": cid,
                            "contractor_name": cname,
                            "status": selected.get("Status", ""),
                            "materials": selected.get("Materials", 0),
                            "labour": selected.get("Labor", 0),
                        }

            except Exception as e:
                logger.warning(f"Crossroads disambiguation failed: {e}")

            # Fallback: list them for the user
            cj_list_str = ", ".join(
                f"CJ #{cj.get('ID')} ({cj.get('Contractor', {}).get('Name', '?') if isinstance(cj.get('Contractor'), dict) else '?'})"
                for cj in matches[:5]
            )
            raise ValueError(
                f"Multiple contractor jobs found: {cj_list_str}. "
                f"Please specify the contractor job ID."
            )

    # ─── Strategy 3: Insufficient IDs — use crossroads resolution ─────
    logger.info("🔀 Insufficient IDs — using crossroads resolution")
    try:
        from utils.crossroads import resolve_with_context

        cr = await resolve_with_context(
            crossroad_type="resolution",
            question="Cannot resolve contractor job — insufficient identifiers provided.",
            context={
                "stuck_point": {
                    "error": "Insufficient IDs to resolve contractor job",
                    "provided": {
                        "contractor_job_id": contractor_job_id,
                        "job_id": job_id,
                        "section_id": section_id,
                        "cost_centre_id": cost_centre_id,
                        "contractor_name": contractor_name,
                    },
                },
                "collected_data": {},
                "failed_attempts": [],
            },
            tracker=mcp_executor.tracker,
            agent_name="workorder",
            llm_chat=llm_chat,
        )

        # If crossroads suggests a strategy, we'd need to execute it.
        # For now, provide a helpful error with guidance.
        reasoning = cr.get("reasoning", "")
        logger.info(f"Crossroads resolution reasoning: {reasoning}")
    except Exception:
        pass

    raise ValueError(
        "Could not resolve the contractor job. Please provide either:\n"
        "- A contractor job ID (e.g. 'delete contractor job 12345'), or\n"
        "- A job ID and cost centre ID (e.g. 'delete work order for job 20990 cost centre 116534')"
    )


# ═══════════════════════════════════════════════════════════════════════════
# Item fetching
# ═══════════════════════════════════════════════════════════════════════════

async def _fetch_cost_centre_items(
    mcp_executor: MCPToolExecutor,
    job_id: int,
    section_id: int,
    cost_centre_id: int,
    policy: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Fetch catalog, labour, one-off, and prebuild items for a cost centre.
    Applies item_inclusion_rules from policy.
    Returns a unified list of item dicts.

    All Simpro cost-centre item endpoints share the same nested structure:
      Total: {Qty, Amount: {ExTax}}
      SellPrice: {ExTax}
    Name lives inside a type-specific sub-object (Catalog, Labor, Prebuild)
    except one-offs which use a flat Description field.
    """
    rules = policy.get("item_inclusion_rules", {})
    include_catalog = rules.get("include_catalog", True)
    include_labour = rules.get("include_labour", True)
    include_one_off = rules.get("include_one_off", True)
    include_prebuild = rules.get("include_prebuild", True)
    exclusion_patterns = [p.lower() for p in rules.get("exclusion_patterns", [])]

    import asyncio
    items: List[Dict[str, Any]] = []
    base_params = {
        "job_id": job_id,
        "section_id": section_id,
        "cost_centre_id": cost_centre_id,
    }

    def _extract_totals(item: dict) -> tuple:
        """Extract (qty, unit_cost, total_val) from Simpro nested response."""
        total_obj = item.get("Total", {}) or {}
        qty = float(total_obj.get("Qty", 0)) if isinstance(total_obj, dict) else 0
        sell_price = item.get("SellPrice", {}) or {}
        unit_cost = float(sell_price.get("ExTax", 0)) if isinstance(sell_price, dict) else 0
        total_amt = total_obj.get("Amount", {}) if isinstance(total_obj, dict) else {}
        total_val = float(total_amt.get("ExTax", 0)) if isinstance(total_amt, dict) else round(qty * unit_cost, 2)
        return qty, unit_cost, total_val

    # Fetch all 4 item types in parallel instead of sequentially (saves ~1.5s)
    async def _fetch_catalog():
        if not include_catalog:
            return []
        try:
            cat_result = await mcp_executor.call_tool(
                "get_cost_centre_catalog_items", base_params,
            )
            cat_items = cat_result.get("catalog_items", cat_result)
            result = []
            if isinstance(cat_items, list):
                for item in cat_items:
                    catalog_obj = item.get("Catalog", {}) or {}
                    cat_name = catalog_obj.get("Name", "") if isinstance(catalog_obj, dict) else ""
                    qty, unit_cost, total_val = _extract_totals(item)
                    result.append({
                        "ItemID": item.get("ID", ""),
                        "ItemName": cat_name,
                        "Type": "Material",
                        "Quantity": qty,
                        "UnitCost": unit_cost,
                        "Total": total_val,
                        "_raw": item,
                    })
            return result
        except Exception as e:
            logger.warning(f"Failed to fetch catalog items: {e}")
            return []

    async def _fetch_labour():
        if not include_labour:
            return []
        try:
            lab_result = await mcp_executor.call_tool(
                "get_cost_centre_labour_items", base_params,
            )
            lab_items = lab_result.get("labour_items", lab_result)
            result = []
            if isinstance(lab_items, list):
                for item in lab_items:
                    labor_obj = item.get("LaborType", {}) or {}
                    lab_name = labor_obj.get("Name", "") if isinstance(labor_obj, dict) else ""
                    qty, unit_cost, total_val = _extract_totals(item)
                    result.append({
                        "ItemID": item.get("ID", ""),
                        "ItemName": lab_name,
                        "Type": "Labour",
                        "Quantity": qty,
                        "UnitCost": unit_cost,
                        "Total": total_val,
                        "_raw": item,
                    })
            return result
        except Exception as e:
            logger.warning(f"Failed to fetch labour items: {e}")
            return []

    async def _fetch_one_off():
        if not include_one_off:
            return []
        try:
            oo_result = await mcp_executor.call_tool(
                "get_cost_centre_one_off_items", base_params,
            )
            oo_items = oo_result.get("one_off_items", oo_result)
            result = []
            if isinstance(oo_items, list):
                for item in oo_items:
                    qty, unit_cost, total_val = _extract_totals(item)
                    result.append({
                        "ItemID": item.get("ID", ""),
                        "ItemName": item.get("Description", ""),
                        "Type": f"OneOff-{item.get('Type', 'Material')}",
                        "Quantity": qty,
                        "UnitCost": unit_cost,
                        "Total": total_val,
                        "_raw": item,
                    })
            return result
        except Exception as e:
            logger.warning(f"Failed to fetch one-off items: {e}")
            return []

    async def _fetch_prebuild():
        if not include_prebuild:
            return []
        try:
            pb_result = await mcp_executor.call_tool(
                "get_cost_centre_prebuild_items", base_params,
            )
            pb_items = pb_result.get("prebuild_items", pb_result)
            result = []
            if isinstance(pb_items, list):
                for item in pb_items:
                    prebuild_obj = item.get("Prebuild", {}) or {}
                    pb_name = prebuild_obj.get("Name", "") if isinstance(prebuild_obj, dict) else ""
                    qty, unit_cost, total_val = _extract_totals(item)
                    result.append({
                        "ItemID": item.get("ID", ""),
                        "ItemName": pb_name,
                        "Type": "Prebuild",
                        "Quantity": qty,
                        "UnitCost": unit_cost,
                        "Total": total_val,
                        "_raw": item,
                    })
            return result
        except Exception as e:
            logger.warning(f"Failed to fetch prebuild items: {e}")
            return []

    # Fetch all item types concurrently
    catalog_items, labour_items, one_off_items, prebuild_items = await asyncio.gather(
        _fetch_catalog(), _fetch_labour(), _fetch_one_off(), _fetch_prebuild()
    )
    items = catalog_items + labour_items + one_off_items + prebuild_items

    # Apply exclusion patterns
    if exclusion_patterns:
        items = [
            it for it in items
            if not any(pat in it.get("ItemName", "").lower() for pat in exclusion_patterns)
        ]

    return items


# ═══════════════════════════════════════════════════════════════════════════
# Prepare-phase output: flat rows for presenter + CSV download
# ═══════════════════════════════════════════════════════════════════════════

def _build_prepare_rows(
    targets: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Flatten targets into a list of row dicts for the presenter.

    Each row contains meta columns (JobID, ContractorName, etc.) plus
    item columns (ItemID, ItemName, Type, Quantity, UnitCost, Total, Include, WO_Status).
    The frontend renders these as a table with a CSV download button;
    the user downloads the CSV, edits the Include column, and re-uploads.

    WO_Status indicates whether an open contractor job already exists:
    - "New"                         — no existing WO, Include=Yes
    - "WO #X exists (empty)"       — 1 empty WO ($0), Include=Yes (safe to populate)
    - "WO #X exists ($M Mat, $L Lab)" — 1 populated WO, Include=No (user decides)
    - "N WOs exist (...)"          — 2+ WOs, Include=No (user reviews)
    """
    rows: List[Dict[str, Any]] = []
    for target in targets:
        # Determine WO_Status and default Include from duplicate guard annotations
        existing_wos = target.get("_existing_wos", [])
        if not existing_wos:
            wo_status = "New"
            include = "Yes"
        elif len(existing_wos) == 1:
            wo = existing_wos[0]
            if wo["is_empty"]:
                wo_status = f"WO #{wo['id']} exists (empty)"
                include = "Yes"
            else:
                wo_status = (
                    f"WO #{wo['id']} exists "
                    f"(${wo['materials']:.0f} Mat, ${wo['labor']:.0f} Lab)"
                )
                include = "No"
        else:
            parts = []
            for wo in existing_wos:
                if wo["is_empty"]:
                    parts.append(f"#{wo['id']}: empty")
                else:
                    parts.append(
                        f"#{wo['id']}: ${wo['materials']:.0f}+${wo['labor']:.0f}"
                    )
            wo_status = f"{len(existing_wos)} WOs exist ({', '.join(parts)})"
            include = "No"

        for item in target.get("items", []):
            rows.append({
                # Meta columns
                "JobID": target.get("job_id", ""),
                "SectionID": target.get("section_id", ""),
                "CostCentreID": target.get("cost_centre_id", ""),
                "CostCentreName": target.get("cost_centre_name", ""),
                "ContractorID": target.get("contractor_id", ""),
                "ContractorName": target.get("contractor_name", ""),
                # Item columns
                "ItemID": item.get("ItemID", ""),
                "ItemName": item.get("ItemName", ""),
                "Type": item.get("Type", ""),
                "Quantity": item.get("Quantity", 0),
                "UnitCost": item.get("UnitCost", 0),
                "Total": item.get("Total", 0),
                "Include": include,
                "WO_Status": wo_status,
            })
    return rows


# ═══════════════════════════════════════════════════════════════════════════
# Phase A: Schedule-based trigger
# ═══════════════════════════════════════════════════════════════════════════

async def _phase_a_schedule(
    mcp_executor: MCPToolExecutor,
    llm_chat: Callable,
    parsed: Dict[str, Any],
    policy: Dict[str, Any],
    session_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Schedule-based trigger:
      1. Fetch schedules for target date (or reuse pre-fetched from session context)
      2. Fetch contractor list
      3. Cross-reference: schedules where Staff is a contractor
      4. Optionally filter by department
      5. For each match: fetch items from cost centre
      6. Generate Excel
    """
    target_date = parsed.get("date") or datetime.now().strftime("%Y-%m-%d")
    department = parsed.get("department")

    # ── Check for pre-fetched schedules from session context ──────────
    # When the user queried schedules via MCP and then asks to create
    # work orders, the session context carries the already-filtered data.
    prefetched = None
    if session_context and isinstance(session_context.get("structured_data"), dict):
        ctx_data = session_context["structured_data"]
        if "schedules" in ctx_data and isinstance(ctx_data["schedules"], list):
            prefetched = ctx_data["schedules"]
            # Inherit department from session context if not explicitly specified
            if not department and session_context.get("department"):
                department = session_context["department"]
            logger.info(
                f"📦 Using {len(prefetched)} pre-fetched schedules from session "
                f"(route={session_context.get('route')}, dept={department})"
            )

    if prefetched is not None:
        schedules = prefetched
    else:
        # Step 1: Fetch schedules for date
        logger.info(f"📅 Fetching schedules for {target_date}")
        try:
            sched_result = await mcp_executor.call_tool("get_schedules", {
                "date_from": target_date,
                "date_to": target_date,
            })
        except Exception as e:
            logger.error(f"Failed to fetch schedules: {e}")
            return {
                "success": False,
                "error": "SCHEDULE_FETCH_FAILED",
                "message": f"Could not fetch schedules for {target_date}: {e}",
            }

        schedules = sched_result.get("schedules", sched_result)
        if isinstance(schedules, dict):
            schedules = schedules.get("schedules", [])
        if not isinstance(schedules, list):
            schedules = []

    if not schedules:
        return {
            "success": False,
            "error": "NO_SCHEDULES",
            "message": f"No schedules found for {target_date}.",
        }

    # Step 2: Fetch contractor list
    logger.info("👷 Fetching contractor list")
    contractor_result = await mcp_executor.call_tool(
        "list_contractors", {"columns": "ID,Name"},
    )
    contractors = contractor_result.get("contractors", contractor_result)
    if isinstance(contractors, dict):
        contractors = contractors.get("contractors", [])
    if not isinstance(contractors, list):
        contractors = []

    contractor_ids = {c.get("ID") for c in contractors}
    contractor_map = {c.get("ID"): c.get("Name", "") for c in contractors}

    # Step 3: Filter schedules where Staff.ID is a contractor
    contractor_schedules = []
    for sched in schedules:
        staff = sched.get("Staff", {})
        staff_id = staff.get("ID") if isinstance(staff, dict) else None
        if staff_id and staff_id in contractor_ids:
            contractor_schedules.append(sched)

    if not contractor_schedules:
        return {
            "success": False,
            "error": "NO_CONTRACTOR_SCHEDULES",
            "message": (
                f"No contractor schedules found for {target_date}. "
                f"Found {len(schedules)} total schedules but none assigned to contractors."
            ),
        }

    logger.info(f"Found {len(contractor_schedules)} contractor schedules")

    # Step 4: Parse Reference field to extract job_id and cost_centre_id.
    # Simpro get_schedules returns Type="job" and Reference="JobID-CostCentreID"
    # — it does NOT return nested Job/Section/CostCentre objects.
    parsed_schedules: List[Dict[str, Any]] = []
    for sched in contractor_schedules:
        schedule_type = (sched.get("Type") or "").lower()
        reference = sched.get("Reference", "")

        if schedule_type != "job" or not reference or "-" not in reference:
            logger.info(
                f"Skipping non-job schedule: Type={schedule_type}, Ref={reference}"
            )
            continue

        try:
            parts = reference.split("-")
            job_id = int(parts[0])
            cc_id = int(parts[1])
        except (ValueError, IndexError) as e:
            logger.warning(f"Failed to parse Reference '{reference}': {e}")
            continue

        staff_id = sched.get("Staff", {}).get("ID") if isinstance(sched.get("Staff"), dict) else None
        if not staff_id:
            logger.warning(f"Schedule missing Staff.ID, Reference={reference}")
            continue

        parsed_schedules.append({
            "_job_id": job_id,
            "_cc_id": cc_id,
            "_staff_id": staff_id,
            "_sched": sched,
        })

    if not parsed_schedules:
        return {
            "success": False,
            "error": "NO_JOB_SCHEDULES",
            "message": (
                f"No job-type contractor schedules for {target_date}. "
                f"Found {len(contractor_schedules)} contractor schedule(s) but none "
                f"with Type='job' and a parseable Reference field."
            ),
        }

    logger.info(f"Parsed {len(parsed_schedules)} job schedules from References")

    # Step 4b: Department filter (if requested)
    dept_type_ids = None
    if department:
        dept_types = await _resolve_department(mcp_executor, department, policy)
        if dept_types:
            dept_type_ids = {t.get("ID") for t in dept_types if t.get("ID")}
            dept_type_names = {t.get("Name", "").lower() for t in dept_types}
            logger.info(f"🏗️ Department filter: {dept_type_names} (type IDs: {dept_type_ids})")

    # Step 5: For each contractor+CC, resolve section_id and fetch items
    # Parallelized: process all unique (contractor, job, cc) combos concurrently
    import asyncio
    targets: List[Dict[str, Any]] = []
    seen_keys: set = set()
    unique_schedules = []

    for ps in parsed_schedules:
        staff_id = ps["_staff_id"]
        job_id = ps["_job_id"]
        cc_id = ps["_cc_id"]

        # Deduplicate by (contractor, job, cc) before expensive API calls
        key = (staff_id, job_id, cc_id)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        unique_schedules.append(ps)

    async def _resolve_target(ps):
        """Resolve section + fetch items for one schedule target."""
        staff_id = ps["_staff_id"]
        job_id = ps["_job_id"]
        cc_id = ps["_cc_id"]

        section_id = await _find_cost_centre_in_sections(mcp_executor, job_id, cc_id)
        if not section_id:
            logger.warning(f"Could not find section for job={job_id}, cc={cc_id} — skipping")
            return None

        # Department filter: check CC's setup type against resolved department types
        if dept_type_ids:
            try:
                cc_resp = await mcp_executor.call_tool(
                    "get_job_section_cost_centres",
                    {"job_id": job_id, "section_id": section_id},
                )
                cc_list = (
                    cc_resp.get("cost_centres", cc_resp)
                    if isinstance(cc_resp, dict) else cc_resp
                )
                cc_match = (
                    next((c for c in cc_list if c.get("ID") == cc_id), None)
                    if isinstance(cc_list, list) else None
                )
                if cc_match:
                    cc_type_ref = cc_match.get("CostCenter", {})
                    cc_type_id = (
                        cc_type_ref.get("ID")
                        if isinstance(cc_type_ref, dict) else cc_type_ref
                    )
                    if cc_type_id and cc_type_id not in dept_type_ids:
                        logger.info(
                            f"🏗️ Skipping cc={cc_id} (type={cc_type_id}): "
                            f"not in department filter {dept_type_ids}"
                        )
                        return None
            except Exception as e:
                logger.warning(f"Department filter check failed for cc={cc_id}: {e}")

        logger.info(f"📦 Fetching items for job={job_id}, sec={section_id}, cc={cc_id}")
        items = await _fetch_cost_centre_items(
            mcp_executor, job_id, section_id, cc_id, policy,
        )

        if not items:
            logger.info(f"No items found for cc={cc_id} — skipping")
            return None

        return {
            "job_id": job_id,
            "section_id": section_id,
            "cost_centre_id": cc_id,
            "cost_centre_name": f"CC-{cc_id}",
            "contractor_id": staff_id,
            "contractor_name": contractor_map.get(staff_id, ""),
            "items": items,
        }

    target_results = await asyncio.gather(*[_resolve_target(ps) for ps in unique_schedules])
    targets = [t for t in target_results if t is not None]

    if not targets:
        return {
            "success": False,
            "error": "NO_ITEMS",
            "message": "No materials or labour items found in the matched cost centres.",
        }

    # Step 5b: Duplicate WO guard — check for existing open contractor jobs
    for target in targets:
        try:
            existing = await mcp_executor.call_tool(
                "get_contractor_jobs_by_cost_centre",
                {
                    "job_id": target["job_id"],
                    "section_id": target["section_id"],
                    "cost_centre_id": target["cost_centre_id"],
                    "columns": "ID,Status,Contractor,Materials,Labor",
                },
            )
            cj_list = (
                existing.get("contractor_jobs", existing)
                if isinstance(existing, dict) else existing
            )
            if not isinstance(cj_list, list):
                cj_list = []

            contractor_id = target["contractor_id"]
            open_matches = [
                cj for cj in cj_list
                if (
                    (isinstance(cj.get("Contractor"), dict)
                     and cj["Contractor"].get("ID") == contractor_id)
                    or cj.get("Contractor") == contractor_id
                ) and cj.get("Status", "").lower() in {"pending", "for review"}
            ]

            if open_matches:
                match_summaries = []
                all_empty = True
                for cj in open_matches:
                    mat = cj.get("Materials", 0) or 0
                    lab = cj.get("Labor", 0) or 0
                    is_empty = (mat == 0 and lab == 0)
                    if not is_empty:
                        all_empty = False
                    match_summaries.append({
                        "id": cj.get("ID"),
                        "status": cj.get("Status", ""),
                        "materials": mat,
                        "labor": lab,
                        "is_empty": is_empty,
                    })
                target["_existing_wos"] = match_summaries
                target["_all_existing_empty"] = all_empty
                logger.info(
                    f"⚠️ Existing open WO(s) for cc={target['cost_centre_id']}, "
                    f"contractor={contractor_id}: "
                    f"{[s['id'] for s in match_summaries]} "
                    f"(all_empty={all_empty})"
                )
        except Exception as e:
            logger.warning(
                f"Duplicate check failed for cc={target['cost_centre_id']}: {e}"
            )

    # Step 6: Build flat rows for presenter + CSV download
    logger.info(f"📊 Building rows for {len(targets)} contractor/CC combos")
    rows = _build_prepare_rows(targets)

    return {
        "success": True,
        "phase": "prepare",
        "wo_review_rows": rows,
        "message": (
            f"Generated work order review sheet with {len(targets)} contractor/cost-centre "
            f"combination(s) for {target_date}. Download the CSV, edit the 'Include' column "
            f"(set to 'No' for items you want to exclude), and re-upload to create contractor jobs."
        ),
        "targets_count": len(targets),
        "total_items": len(rows),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Phase A: Direct trigger
# ═══════════════════════════════════════════════════════════════════════════

async def _phase_a_direct(
    mcp_executor: MCPToolExecutor,
    llm_chat: Callable,
    parsed: Dict[str, Any],
    policy: Dict[str, Any],
    session_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Direct trigger:
      1. Resolve contractor name → ID
      2. Resolve section_id if missing (reverse-lookup)
      3. Fetch items from cost centre
      4. Generate Excel
    """
    job_id = parsed.get("job_id")
    section_id = parsed.get("section_id")
    cost_centre_id = parsed.get("cost_centre_id")
    cost_centre_name = parsed.get("cost_centre_name")
    contractor_name = parsed.get("contractor_name")
    contractor_id = parsed.get("contractor_id")

    # ── Check pre-resolved data from handoff collected_data ──
    pre_resolved = (session_context or {}).get("pre_resolved", {})
    if not contractor_id and pre_resolved.get("contractor_id"):
        contractor_id = pre_resolved["contractor_id"]

    if not job_id:
        return {
            "success": False,
            "error": "MISSING_JOB_ID",
            "message": "Please specify a Job ID for the work order.",
        }

    # ── Phase 1: Resolve independent fields (contractor) ──
    # Collect clarification errors so they can be batched with section/CC
    # clarifications discovered below.
    contractor = None
    collected_clarifications = []

    # If contractor_id is already resolved (e.g., from handoff pre_resolved),
    # skip the resolution and build the contractor dict directly.
    if contractor_id and pre_resolved.get("contractor_id") == contractor_id:
        contractor = {"ID": contractor_id, "Name": pre_resolved.get("contractor_name", f"Contractor {contractor_id}")}
        logger.info(f"📦 Using pre-resolved contractor: {contractor}")

    if contractor is None:
        try:
            contractor = await _resolve_contractor(
                mcp_executor, contractor_name, contractor_id, llm_chat,
            )
        except AmbiguousResolutionError as e:
            collected_clarifications.append({
                "row": 1,
                "type": "ambiguous",
                "field": e.field,
                "value": e.value,
                "options": e.matches,
                "message": e.message,
                "operation": "CREATE",
                "row_context": {"job": f"Job {job_id}"},
            })
        except MissingFieldError as e:
            options = e.context.get("options", [])
            is_multi = e.context.get("multi_select", False)
            if options:
                collected_clarifications.append({
                    "row": 1,
                    "type": "multi_select" if is_multi else "missing",
                    "field": e.field,
                    "message": e.message,
                    "options": options,
                    "operation": "CREATE",
                    "row_context": {"job": f"Job {job_id}"},
                })
            else:
                return {
                    "success": False,
                    "error": "CONTRACTOR_RESOLUTION_FAILED",
                    "message": str(e),
                }
        except (ValueError, ResolutionError) as e:
            return {
                "success": False,
                "error": "CONTRACTOR_RESOLUTION_FAILED",
                "message": str(e),
            }

    # ── Phase 2: Resolve dependent fields (section, cost centre) ──
    # Resolve section_id if missing
    if not section_id and cost_centre_id:
        section_id = await _find_cost_centre_in_sections(mcp_executor, job_id, cost_centre_id)
        if not section_id:
            collected_clarifications.append({
                "row": 1,
                "type": "missing",
                "field": "SectionName",
                "message": f"Could not find which section contains cost centre {cost_centre_id} in job {job_id}.",
                "options": [],
                "operation": "CREATE",
                "row_context": {"job": f"Job {job_id}"},
            })

    # If no section_id and no cost_centre_id, get sections and try to infer
    if not section_id:
        sections = await _resolve_section_for_cost_centre(mcp_executor, job_id)
        if len(sections) == 1:
            section_id = sections[0].get("ID")
        elif sections and cost_centre_name:
            # Cross-phase inference: scan sections' CCs for the named CC
            resolver = EntityResolver(mcp_executor)
            inferred = await resolver._infer_section_from_cc_name(
                job_id, sections, cost_centre_name, context="job", row_num=1,
            )
            if inferred:
                section_id = inferred["id"]
        if not section_id and sections:
            collected_clarifications.append({
                "row": 1,
                "type": "missing",
                "field": "SectionName",
                "message": f"Job {job_id} has {len(sections)} sections. Please select one:",
                "options": [{"id": s.get("ID"), "name": s.get("Name", f"Section {s.get('ID')}")} for s in sections],
                "operation": "CREATE",
                "row_context": {"job": f"Job {job_id}"},
            })
        elif not sections and not collected_clarifications:
            return {
                "success": False,
                "error": "NO_SECTIONS",
                "message": f"Job {job_id} has no sections.",
            }

    # If no cost_centre_id, get all CCs for section and let user choose
    if not cost_centre_id and section_id:
        cc_result = await mcp_executor.call_tool(
            "get_job_section_cost_centres",
            {"job_id": job_id, "section_id": section_id},
        )
        cost_centres = cc_result.get("cost_centres", cc_result)
        if isinstance(cost_centres, list) and len(cost_centres) == 1:
            cost_centre_id = cost_centres[0].get("ID")
        elif isinstance(cost_centres, list) and cost_centres:
            collected_clarifications.append({
                "row": 1,
                "type": "missing",
                "field": "CostCentreName",
                "message": f"Section has {len(cost_centres)} cost centres. Please select one:",
                "options": [{"id": cc.get("ID"), "name": cc.get("Name", f"CC {cc.get('ID')}")} for cc in cost_centres],
                "operation": "CREATE",
                "row_context": {"job": f"Job {job_id}", "section": f"Section {section_id}"},
            })
        elif not collected_clarifications:
            return {
                "success": False,
                "error": "NO_COST_CENTRES",
                "message": f"No cost centres found for job {job_id}, section {section_id}.",
            }

    # ── Return all clarifications at once ──
    if collected_clarifications:
        import uuid
        session_id = f"wo_{uuid.uuid4().hex[:12]}"
        return {
            "success": False,
            "needs_clarification": True,
            "clarification_mode": "interactive",
            "session_id": session_id,
            "clarification_count": len(collected_clarifications),
            "clarifications": collected_clarifications,
            "message": f"{len(collected_clarifications)} fields need clarification for work order creation.",
            "agent": "workorder",
        }

    # Get cost centre name
    cc_name = ""
    try:
        cc_detail = await mcp_executor.call_tool(
            "get_job_cost_centre_details",
            {"job_id": job_id, "section_id": section_id, "cost_centre_id": cost_centre_id},
        )
        cc_data = cc_detail.get("cost_centre", cc_detail)
        if isinstance(cc_data, dict):
            cc_name = cc_data.get("Name", "")
    except Exception:
        pass

    # Fetch items — skip if pre-resolved via handoff collected_data
    pre_materials = pre_resolved.get("materials")
    pre_labour = pre_resolved.get("labour")
    if pre_materials is not None or pre_labour is not None:
        items = list(pre_materials or []) + list(pre_labour or [])
        logger.info(f"📦 Using pre-resolved items from handoff: {len(items)} items")
    else:
        items = await _fetch_cost_centre_items(
            mcp_executor, job_id, section_id, cost_centre_id, policy,
        )

    if not items:
        return {
            "success": False,
            "error": "NO_ITEMS",
            "message": (
                f"No materials or labour items found in cost centre {cost_centre_id} "
                f"(job {job_id}, section {section_id})."
            ),
        }

    targets = [{
        "job_id": job_id,
        "section_id": section_id,
        "cost_centre_id": cost_centre_id,
        "cost_centre_name": cc_name,
        "contractor_id": contractor["ID"],
        "contractor_name": contractor["Name"],
        "items": items,
    }]

    rows = _build_prepare_rows(targets)

    return {
        "success": True,
        "phase": "prepare",
        "wo_review_rows": rows,
        "message": (
            f"Generated work order review sheet for contractor "
            f"{contractor['Name']} on cost centre {cc_name or cost_centre_id}. "
            f"Download the CSV, edit the 'Include' column "
            f"(set to 'No' for items you want to exclude), and re-upload to create "
            f"contractor jobs."
        ),
        "targets_count": 1,
        "total_items": len(rows),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Phase B: Process re-uploaded Excel (from svc-extractor)
# ═══════════════════════════════════════════════════════════════════════════

def _normalize_header(h: str) -> str:
    """Normalize header for matching: lowercase, strip spaces/underscores/hyphens."""
    return h.lower().replace(" ", "").replace("_", "").replace("-", "")


def _is_wo_reupload(extracted: Dict[str, Any]) -> bool:
    """
    Detect whether the extracted data is a work-order re-upload.
    Checks for WO-specific column signatures in headers.
    Handles both raw field names (CostCentreID) and humanized labels (Cost Centre ID).
    """
    if not extracted:
        return False
    tables = extracted.get("tables", [])
    if not tables:
        return False

    table = tables[0]
    headers = table.get("headers", [])
    if not headers:
        # Try to infer from first row
        rows = table.get("rows", [])
        if rows and isinstance(rows[0], dict):
            headers = list(rows[0].keys())

    headers_norm = {_normalize_header(h) for h in headers}

    wo_indicators = {
        "include", "itemname", "unitcost", "contractorid", "contractorname",
        "itemid", "costcentreid", "costcentrename", "sectionid", "jobid",
    }
    return len(wo_indicators.intersection(headers_norm)) >= 3


def _normalize_key(name: str) -> str:
    """Lowercase, strip non-alphanumerics for robust header matching."""
    return "".join(ch.lower() for ch in name if ch.isalnum())


def _get_field(row: Dict[str, Any], *candidates: str) -> Optional[str]:
    """Look up a cell by trying multiple header variants."""
    if not row:
        return None
    norm = {_normalize_key(k): v for k, v in row.items()}
    for cand in candidates:
        key = _normalize_key(cand)
        if key in norm and norm[key] not in (None, ""):
            return str(norm[key])
    return None


def _parse_wo_rows(
    extracted: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Parse re-uploaded Excel rows from extractor output.
    Filter to Include == "Yes" rows only.
    Returns list of normalised item dicts with metadata.
    """
    tables = extracted.get("tables", [])
    if not tables:
        return []

    table = tables[0]
    headers = table.get("headers", [])
    rows = table.get("rows", [])
    if not rows:
        return []

    # Convert list-of-lists rows to list-of-dicts using headers
    if rows and headers and isinstance(rows[0], (list, tuple)):
        rows = [dict(zip(headers, r)) for r in rows]

    included_items: List[Dict[str, Any]] = []

    for row in rows:
        if not isinstance(row, dict):
            continue

        include = _get_field(row, "Include")
        if not include or include.strip().lower() not in ("yes", "y", "true", "1"):
            continue

        # Parse item data
        item_id = _get_field(row, "ItemID", "Item ID")
        item_name = _get_field(row, "ItemName", "Item Name")
        item_type = _get_field(row, "Type")
        quantity = _get_field(row, "Quantity", "Qty")
        unit_cost = _get_field(row, "UnitCost", "Unit Cost")
        total = _get_field(row, "Total")

        # Parse metadata
        job_id = _get_field(row, "JobID", "Job ID")
        section_id = _get_field(row, "SectionID", "Section ID")
        cc_id = _get_field(row, "CostCentreID", "Cost Centre ID", "CostCenterID")
        cc_name = _get_field(row, "CostCentreName", "Cost Centre Name", "CostCenterName")
        contractor_id = _get_field(row, "ContractorID", "Contractor ID")
        contractor_name = _get_field(row, "ContractorName", "Contractor Name")

        if not job_id:
            continue

        # Convert numerics safely
        def _safe_float(val: Optional[str]) -> float:
            if not val:
                return 0.0
            try:
                return float(val)
            except (ValueError, TypeError):
                return 0.0

        def _safe_int(val: Optional[str]) -> Optional[int]:
            if not val:
                return None
            try:
                return int(float(val))
            except (ValueError, TypeError):
                return None

        included_items.append({
            "ItemID": item_id,
            "ItemName": item_name or "",
            "Type": item_type or "Material",
            "Quantity": _safe_float(quantity),
            "UnitCost": _safe_float(unit_cost),
            "Total": _safe_float(total) or round(_safe_float(quantity) * _safe_float(unit_cost), 2),
            # Metadata
            "JobID": _safe_int(job_id),
            "SectionID": _safe_int(section_id),
            "CostCentreID": _safe_int(cc_id),
            "CostCentreName": cc_name or "",
            "ContractorID": _safe_int(contractor_id),
            "ContractorName": contractor_name or "",
        })

    return included_items


def _build_contractor_job_payloads(
    included_items: List[Dict[str, Any]],
    policy: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Group items by (JobID, SectionID, CostCentreID, ContractorID)
    and build a contractor-job payload per group.
    """
    defaults = policy.get("defaults", {})
    desc_format = policy.get("description_format", "itemized")

    # Group items
    groups: Dict[Tuple, List[Dict[str, Any]]] = {}
    for item in included_items:
        key = (
            item.get("JobID"),
            item.get("SectionID"),
            item.get("CostCentreID"),
            item.get("ContractorID"),
        )
        groups.setdefault(key, []).append(item)

    payloads: List[Dict[str, Any]] = []

    for (job_id, section_id, cc_id, contractor_id), group_items in groups.items():
        if not all([job_id, section_id, cc_id, contractor_id]):
            logger.warning(f"Skipping group with missing IDs: {(job_id, section_id, cc_id, contractor_id)}")
            continue

        # Separate materials vs labour
        # Type values: "Material", "Labour", "Prebuild", "OneOff-Material", "OneOff-Labor"
        # Prebuilds are pre-built assemblies → treated as materials for cost totals
        def _is_material_type(t: str) -> bool:
            t = t.lower()
            return (
                t in ("material", "catalog", "prebuild", "oneoff", "one-off")
                or t.startswith("oneoff-material")
            )

        def _is_labour_type(t: str) -> bool:
            t = t.lower()
            return t in ("labour", "labor") or t.startswith("oneoff-lab")

        material_total = sum(
            it["Total"] for it in group_items if _is_material_type(it.get("Type", ""))
        )
        labour_total = sum(
            it["Total"] for it in group_items if _is_labour_type(it.get("Type", ""))
        )

        # Build description (use <br> for line breaks — Simpro renders HTML)
        include_desc = policy.get("include_description", True)
        if not include_desc:
            description = ""
        elif desc_format == "itemized":
            desc_lines = []
            for it in group_items:
                name = it.get("ItemName", "Item")
                qty = it.get("Quantity", 0)
                total = it.get("Total", 0)
                desc_lines.append(f"- {name} (Qty: {qty})")
            description = "<br>".join(desc_lines)
        else:
            # Summary format
            mat_count = sum(1 for it in group_items if _is_material_type(it.get("Type", "")))
            lab_count = sum(1 for it in group_items if _is_labour_type(it.get("Type", "")))
            description = f"Materials: {mat_count} items, Labour: {lab_count} items"

        # Build the contractor job data dict
        contractor_job_data: Dict[str, Any] = {
            "Contractor": contractor_id,
            "Description": description,
            "Materials": round(material_total, 2),
            "Labor": round(labour_total, 2),
        }

        # Apply SOP defaults
        tax_code_id = defaults.get("TaxCodeID")
        if tax_code_id is not None:
            contractor_job_data["TaxCode"] = tax_code_id

        date_issued = defaults.get("DateIssued") or datetime.now().strftime("%Y-%m-%d")
        contractor_job_data["DateIssued"] = date_issued

        supply_materials = defaults.get("ContractorSupplyMaterials")
        if supply_materials is not None:
            contractor_job_data["ContractorSupplyMaterials"] = supply_materials

        # Add any other SOP-defined defaults that are valid Simpro API fields.
        # Use an allowlist to prevent internal config keys from leaking into the
        # PATCH payload (Simpro rejects unknown fields with 422).
        _SIMPRO_CJ_FIELDS = {
            "Contractor", "Description", "Materials", "Labor", "TaxCode",
            "DateIssued", "ContractorSupplyMaterials", "Items", "Status",
            "OrderNo", "Notes", "InternalNotes",
        }
        for key, val in defaults.items():
            if key in _SIMPRO_CJ_FIELDS and key not in contractor_job_data and val is not None:
                contractor_job_data[key] = val

        # Build Items field for Simpro quantity assignment
        # Only Catalogs and Prebuilds are supported by the Simpro Items sub-endpoint
        catalog_items = []
        prebuild_items = []
        for it in group_items:
            item_id = it.get("ItemID")
            qty = it.get("Quantity", 0)
            item_type = (it.get("Type") or "").lower()
            if not item_id or qty <= 0:
                continue
            if item_type == "material":
                catalog_items.append({"ID": int(item_id), "Qty": qty})
            elif item_type == "prebuild":
                prebuild_items.append({"ID": int(item_id), "Qty": qty})

        if catalog_items or prebuild_items:
            items_payload = {}
            if catalog_items:
                items_payload["Catalogs"] = catalog_items
            if prebuild_items:
                items_payload["Prebuilds"] = prebuild_items
            contractor_job_data["Items"] = items_payload
            # When assigning Items, let Simpro auto-calculate Materials/Labor
            # from the prebuild/catalog setup — our manual totals can't know
            # the internal material vs labour split of each prebuild.
            contractor_job_data.pop("Materials", None)
            contractor_job_data.pop("Labor", None)

        contractor_name = group_items[0].get("ContractorName", "")
        cc_name = group_items[0].get("CostCentreName", "")

        payloads.append({
            "job_id": job_id,
            "section_id": section_id,
            "cost_centre_id": cc_id,
            "cost_centre_name": cc_name,
            "contractor_id": contractor_id,
            "contractor_name": contractor_name,
            "contractor_job_data": contractor_job_data,
            "item_count": len(group_items),
            "materials_total": round(material_total, 2),
            "labour_total": round(labour_total, 2),
        })

    return payloads


async def _phase_b_create(
    llm_chat: Callable,
    extracted: Dict[str, Any],
    policy: Dict[str, Any],
    mcp_executor=None,
) -> Dict[str, Any]:
    """
    Phase B: Parse re-uploaded Excel, filter included items,
    build contractor-job payloads, check for existing open WOs.
    """
    included_items = _parse_wo_rows(extracted)

    if not included_items:
        return {
            "success": False,
            "error": "NO_INCLUDED_ITEMS",
            "message": (
                "No items marked as 'Include = Yes' in the uploaded file. "
                "Please mark at least one item as 'Yes' in the Include column."
            ),
        }

    logger.info(f"📋 {len(included_items)} items included for contractor job creation")

    payloads = _build_contractor_job_payloads(included_items, policy)

    if not payloads:
        return {
            "success": False,
            "error": "NO_PAYLOADS",
            "message": "Could not build any contractor job payloads from the included items (missing IDs).",
        }

    logger.info(f"🏗️ Built {len(payloads)} contractor job payload(s)")

    # ─── Check for existing open contractor jobs ──────────────────────────
    if mcp_executor:
        needs_clarification = []
        clean_payloads = []

        for payload in payloads:
            job_id = payload["job_id"]
            section_id = payload["section_id"]
            cc_id = payload["cost_centre_id"]
            contractor_id = payload["contractor_id"]

            try:
                existing = await mcp_executor.call_tool(
                    "get_contractor_jobs_by_cost_centre",
                    {
                        "job_id": job_id,
                        "section_id": section_id,
                        "cost_centre_id": cc_id,
                        "columns": "ID,Status,Contractor",
                    },
                )
                cj_list = existing.get("contractor_jobs", existing) if isinstance(existing, dict) else existing
                if not isinstance(cj_list, list):
                    cj_list = []

                logger.info(
                    f"🔍 Duplicate check: cc={cc_id}, contractor={contractor_id}, "
                    f"found {len(cj_list)} CJ(s)"
                )
                for _cj in cj_list:
                    _cj_contractor = _cj.get("Contractor")
                    _cj_status = _cj.get("Status")
                    logger.info(
                        f"   CJ {_cj.get('ID')}: Contractor={_cj_contractor}, "
                        f"Status={_cj_status}"
                    )

                # Filter to same contractor + open status
                open_statuses = {"pending", "for review"}
                open_matches = [
                    cj for cj in cj_list
                    if (
                        (isinstance(cj.get("Contractor"), dict) and cj["Contractor"].get("ID") == contractor_id)
                        or cj.get("Contractor") == contractor_id
                    )
                    and (cj.get("Status", "").lower() in open_statuses)
                ]
                logger.info(
                    f"🔍 After filtering: {len(open_matches)} match(es) for "
                    f"contractor={contractor_id}, open_statuses={open_statuses}"
                )

                if open_matches:
                    needs_clarification.append({
                        "payload": payload,
                        "existing_jobs": open_matches,
                    })
                else:
                    clean_payloads.append(payload)

            except Exception as e:
                logger.error(f"Failed to check existing CJs for cc={cc_id}: {e}")
                needs_clarification.append({
                    "payload": payload,
                    "existing_jobs": [],
                    "check_failed": True,
                    "error": str(e),
                })

        if needs_clarification:
            session_id = f"wo_{uuid.uuid4().hex[:12]}"

            clarifications = []
            for idx, item in enumerate(needs_clarification):
                p = item["payload"]
                cc_name = p.get("cost_centre_name", p["cost_centre_id"])
                contractor_name = p.get("contractor_name", p["contractor_id"])

                wo_row_ctx = {
                    "cost_centre": cc_name,
                    "contractor": contractor_name,
                }
                if p.get("job_id"):
                    wo_row_ctx["job"] = f"Job {p['job_id']}"

                if item.get("check_failed"):
                    # Check failed — warn user and ask for confirmation
                    clarifications.append({
                        "row": idx + 1,
                        "field": "WO_Action",
                        "type": "confirmation",
                        "message": (
                            f"Could not verify if cost centre '{cc_name}' for contractor "
                            f"'{contractor_name}' already has an open work order. "
                            f"Would you like to proceed with creating a new WO anyway?"
                        ),
                        "options": [
                            {"id": "create_new", "name": "Create new WO"},
                            {"id": "skip", "name": "Skip this cost centre"},
                        ],
                        "operation": "CREATE",
                        "row_context": wo_row_ctx,
                    })
                    continue

                existing = item["existing_jobs"]
                options = []
                for cj in existing:
                    cj_id = cj.get("ID", "?")
                    cj_status = cj.get("Status", "Unknown")
                    options.append({
                        "id": f"update_{cj_id}",
                        "name": f"Update existing WO #{cj_id} (Status: {cj_status})",
                    })
                options.append({
                    "id": "create_new",
                    "name": "Create new WO",
                })

                clarifications.append({
                    "row": idx + 1,
                    "field": "WO_Action",
                    "type": "confirmation",
                    "message": (
                        f"Cost centre '{cc_name}' for contractor '{contractor_name}' "
                        f"already has {len(existing)} open contractor job(s). "
                        f"Would you like to update an existing one or create a new WO?"
                    ),
                    "options": options,
                    "operation": "CREATE",
                    "row_context": wo_row_ctx,
                })

            return {
                "success": False,
                "needs_clarification": True,
                "session_id": session_id,
                "clarification_count": len(clarifications),
                "clarifications": clarifications,
                "resolved_count": len(clean_payloads),
                "total_count": len(payloads),
                "message": (
                    f"{len(needs_clarification)} cost centre(s) already have open contractor jobs. "
                    f"Please choose whether to update existing or create new."
                ),
                # Internal data for session resume (stripped before sending to frontend)
                "_clean_payloads": clean_payloads,
                "_pending_payloads": [item["payload"] for item in needs_clarification],
                "_existing_map": {
                    idx: item["existing_jobs"] for idx, item in enumerate(needs_clarification)
                },
            }
        else:
            payloads = clean_payloads

    return {
        "success": True,
        "phase": "create",
        "contractor_jobs": payloads,
        "message": (
            f"Ready to create {len(payloads)} contractor job(s). "
            f"Total items: {sum(p['item_count'] for p in payloads)}."
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# UPDATE flow
# ═══════════════════════════════════════════════════════════════════════════

_FIELD_MAP = {
    "materials": "Materials",
    "labour": "Labor",
    "description": "Description",
    "tax_code_id": "TaxCode",
    "date_issued": "DateIssued",
}


async def _handle_update(
    mcp_executor: MCPToolExecutor,
    llm_chat: Callable,
    parsed: Dict[str, Any],
    policy: Dict[str, Any],
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """
    Handle UPDATE operation:
    1. Validate fields_to_update
    2. Resolve contractor job via _resolve_contractor_job
    3. Build PATCH payload
    4. Return contractor_job_updates list for executor
    """
    fields_to_update = parsed.get("fields_to_update") or {}

    # Filter out null values
    active_fields = {k: v for k, v in fields_to_update.items() if v is not None}
    if not active_fields:
        return {
            "success": False,
            "error": "NO_FIELDS_TO_UPDATE",
            "message": (
                "Please specify what you want to update "
                "(e.g., materials, labour, description, date issued)."
            ),
        }

    # Resolve the target contractor job
    try:
        cj_info = await _resolve_contractor_job(
            mcp_executor, llm_chat, parsed,
            conversation_history=conversation_history,
        )
    except ValueError as e:
        return {
            "success": False,
            "error": "CONTRACTOR_JOB_NOT_FOUND",
            "message": str(e),
        }

    # Build PATCH payload — map user field names to Simpro API field names
    contractor_job_data: Dict[str, Any] = {}
    for user_key, api_key in _FIELD_MAP.items():
        val = active_fields.get(user_key)
        if val is not None:
            if user_key in ("materials", "labour"):
                contractor_job_data[api_key] = float(val)
            elif user_key == "tax_code_id":
                contractor_job_data[api_key] = int(val)
            else:
                contractor_job_data[api_key] = val

    # Apply SOP defaults where the user didn't specify a value
    defaults = policy.get("defaults", {})
    if "TaxCode" not in contractor_job_data and defaults.get("TaxCodeID"):
        # Only apply default if user is changing financial fields
        if "Materials" in contractor_job_data or "Labor" in contractor_job_data:
            contractor_job_data["TaxCode"] = defaults["TaxCodeID"]

    if not contractor_job_data:
        return {
            "success": False,
            "error": "NO_VALID_FIELDS",
            "message": (
                "No valid fields to update. Supported: materials, labour, "
                "description, tax_code_id, date_issued."
            ),
        }

    payload = {
        "job_id": cj_info["job_id"],
        "section_id": cj_info["section_id"],
        "cost_centre_id": cj_info["cost_centre_id"],
        "contractor_name": cj_info["contractor_name"],
        "_existing_cj_id": cj_info["contractor_job_id"],
        "contractor_job_data": contractor_job_data,
        "materials_total": contractor_job_data.get(
            "Materials", cj_info.get("materials", 0)
        ),
        "labour_total": contractor_job_data.get(
            "Labor", cj_info.get("labour", 0)
        ),
        "item_count": 0,
    }

    changes_str = ", ".join(
        f"{k}={v}" for k, v in contractor_job_data.items()
    )
    logger.info(
        f"🔄 UPDATE payload ready: CJ={cj_info['contractor_job_id']}, "
        f"changes={changes_str}"
    )

    return {
        "success": True,
        "contractor_job_updates": [payload],
        "message": (
            f"Ready to update contractor job {cj_info['contractor_job_id']} "
            f"for {cj_info['contractor_name']}. Changes: {changes_str}"
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# DELETE flow
# ═══════════════════════════════════════════════════════════════════════════

async def _handle_delete(
    mcp_executor: MCPToolExecutor,
    llm_chat: Callable,
    parsed: Dict[str, Any],
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """
    Handle DELETE operation:
    1. Resolve contractor job(s) via _resolve_contractor_job
    2. Return contractor_job_deletes list for executor
    Supports single (contractor_job_id) or batch (contractor_job_ids) delete.
    """
    # Collect IDs to delete — support both single and batch
    cj_ids = parsed.get("contractor_job_ids") or []
    single_id = parsed.get("contractor_job_id")
    if single_id and single_id not in cj_ids:
        cj_ids.append(single_id)

    if not cj_ids:
        # Fallback: try to resolve from other fields (job_id, cc_id, etc.)
        try:
            cj_info = await _resolve_contractor_job(
                mcp_executor, llm_chat, parsed,
                conversation_history=conversation_history,
            )
            cj_ids = [cj_info["contractor_job_id"]]
        except ValueError as e:
            return {
                "success": False,
                "error": "CONTRACTOR_JOB_NOT_FOUND",
                "message": str(e),
            }

    payloads = []
    failed = []

    for cj_id in cj_ids:
        try:
            single_parsed = {**parsed, "contractor_job_id": cj_id, "contractor_job_ids": None}
            cj_info = await _resolve_contractor_job(
                mcp_executor, llm_chat, single_parsed,
                conversation_history=conversation_history,
            )
            payloads.append({
                "job_id": cj_info["job_id"],
                "section_id": cj_info["section_id"],
                "cost_centre_id": cj_info["cost_centre_id"],
                "contractor_job_id": cj_info["contractor_job_id"],
                "contractor_name": cj_info["contractor_name"],
            })
            logger.info(
                f"🗑️ DELETE payload ready: CJ={cj_info['contractor_job_id']}, "
                f"contractor={cj_info['contractor_name']}"
            )
        except ValueError as e:
            logger.error(f"Failed to resolve CJ {cj_id}: {e}")
            failed.append({"contractor_job_id": cj_id, "error": str(e)})

    if not payloads and failed:
        return {
            "success": False,
            "error": "CONTRACTOR_JOB_NOT_FOUND",
            "message": f"Could not resolve any of the requested contractor jobs: {failed}",
        }

    msg_parts = [
        f"CJ {p['contractor_job_id']} ({p['contractor_name']})" for p in payloads
    ]
    return {
        "success": True,
        "contractor_job_deletes": payloads,
        "message": f"Ready to delete {len(payloads)} contractor job(s): {', '.join(msg_parts)}.",
    }


# ═══════════════════════════════════════════════════════════════════════════
# Main entry point
# ═══════════════════════════════════════════════════════════════════════════

async def run_workorder_agent(
    llm_chat: Callable,
    user_text: str,
    extracted: Optional[Dict[str, Any]] = None,
    any_uploaded_text: Optional[str] = None,
    hints: Optional[Dict[str, Any]] = None,
    mcp_executor: Optional[MCPToolExecutor] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """
    Main work-order agent entry point.

    Args:
        llm_chat: LLM chat function (PII-safe from proxy)
        user_text: User's message
        extracted: Structured data from svc-extractor (for re-upload)
        any_uploaded_text: CSV fallback text
        hints: Backend hints (e.g., CompanyID, action from intent analyzer)
        mcp_executor: MCPToolExecutor for Simpro API calls
        conversation_history: Recent conversation messages

    Returns:
        Phase A:  {success, phase="prepare", wo_review_rows, message}
        Phase B:  {success, phase="create", contractor_jobs, message}
        UPDATE:   {success, contractor_job_updates, message}
        DELETE:   {success, contractor_job_deletes, message}
        Error:    {success=False, error, message}
    """
    _agent_state = create_agent_state("workorder", user_text or "")
    _agent_state.enter_phase("parse")

    logger.info("=" * 70)
    logger.info("🔨 Work Order Agent: Starting")
    logger.info(f"User text: {user_text[:100]}")
    logger.info(f"Has extracted: {bool(extracted)}")
    logger.info(f"Has mcp_executor: {bool(mcp_executor)}")
    logger.info(f"Hints: {hints}")
    logger.info("=" * 70)

    if mcp_executor is None:
        return {
            "success": False,
            "error": "NO_MCP_EXECUTOR",
            "message": "Work order agent requires MCP executor for Simpro API access.",
        }

    # Read SOP (all operations obey SOP)
    sop_text = _read_sop(sop_override=(hints or {}).get("sop_override"))

    # Get SOP-based policy
    policy = _llm_plan_policy(llm_chat, sop_text, user_text)

    # Extract session context for cross-path data reuse (e.g., MCP → Agent handoff)
    # Also merge in pre_resolved data from handoff collected_data (if present)
    session_ctx = (hints or {}).get("session_context") or {}
    pre_resolved_from_handoff = (hints or {}).get("pre_resolved")
    if pre_resolved_from_handoff:
        session_ctx = {**session_ctx, "pre_resolved": pre_resolved_from_handoff}
    if not session_ctx:
        session_ctx = None

    # SOP missing clarifications only block CREATE
    # (update/delete don't need creation defaults like department mappings)
    action = (hints or {}).get("action", "create")
    missing = policy.get("missing", [])
    if action == "create" and missing:
        return {
            "success": False,
            "needs_clarification": True,
            "questions": missing,
            "message": (
                "I need some clarification before proceeding:\n"
                + "\n".join(f"- {q}" for q in missing)
            ),
        }

    # ─── Phase B: Re-uploaded Excel ───────────────────────────────────────
    # Detection is purely content-based: if the uploaded file has WO item
    # columns (Include, ContractorID, ItemName, etc.), it's a Phase B
    # re-upload — regardless of session state or time elapsed since Phase A.
    if extracted and _is_wo_reupload(extracted):
        logger.info("📂 Detected Phase B: re-uploaded work order Excel")
        _agent_state.complete_phase("parse")
        _agent_state.enter_phase("phase_b")
        _result = await _phase_b_create(llm_chat, extracted, policy, mcp_executor=mcp_executor)
        _agent_state.complete_phase("phase_b")
        logger.info(_agent_state.summary())
        return _result

    # If user uploaded a file but it doesn't match WO format, check
    # conversation history to see if a Phase A prepare was done recently.
    # If so, give a helpful error instead of silently re-running Phase A.
    if extracted and not _is_wo_reupload(extracted) and conversation_history:
        recent_msgs = " ".join(
            m.get("content", "") for m in (conversation_history or [])[-4:]
        ).lower()
        if "work order review sheet" in recent_msgs or "prepare" in recent_msgs:
            logger.warning("📂 File uploaded after prepare phase but doesn't match WO item format")
            return {
                "success": False,
                "error": "WRONG_FILE_FORMAT",
                "message": (
                    "The uploaded file doesn't contain work order item data. "
                    "Please download the Excel/CSV from the items table "
                    "(the one with columns like Item ID, Contractor Name, Include) "
                    "and re-upload it."
                ),
            }

    # ─── Parse user request ───────────────────────────────────────────────
    parsed = _parse_chat_wo_request(
        llm_chat, user_text, sop_text, conversation_history, hints=hints,
    )

    if parsed.get("error"):
        return {
            "success": False,
            "error": "PARSE_ERROR",
            "message": f"Could not parse work order request: {parsed['error']}",
        }

    # Route by action (parser result takes priority, intent_action as fallback)
    actual_action = parsed.get("action") or action
    logger.info(f"🎯 WO action: {actual_action} (parser={parsed.get('action')}, hint={action})")

    _agent_state.complete_phase("parse")

    # ─── UPDATE Flow ──────────────────────────────────────────────────────
    if actual_action == "update":
        logger.info("🔄 Routing to UPDATE handler")
        _agent_state.enter_phase("update")
        _result = await _handle_update(
            mcp_executor, llm_chat, parsed, policy,
            conversation_history=conversation_history,
        )
        _agent_state.complete_phase("update")
        logger.info(_agent_state.summary())
        return _result

    # ─── DELETE Flow ──────────────────────────────────────────────────────
    if actual_action == "delete":
        logger.info("🗑️ Routing to DELETE handler")
        _agent_state.enter_phase("delete")
        _result = await _handle_delete(
            mcp_executor, llm_chat, parsed,
            conversation_history=conversation_history,
        )
        _agent_state.complete_phase("delete")
        logger.info(_agent_state.summary())
        return _result

    # ─── CREATE Flow (Phase A: Generate Excel) ────────────────────────────
    trigger = parsed.get("trigger", "direct")

    # Safety override: if parser chose "direct" but there's no job_id and we have
    # pre-fetched schedules in session context, the user is clearly referring to
    # those schedules — promote to "schedule" trigger so they get reused.
    if (
        trigger == "direct"
        and not parsed.get("job_id")
        and session_ctx
        and isinstance((session_ctx or {}).get("structured_data"), dict)
        and isinstance(session_ctx["structured_data"].get("schedules"), list)
        and len(session_ctx["structured_data"]["schedules"]) > 0
    ):
        logger.info(
            "⚡ Trigger override: direct→schedule (no job_id but pre-fetched schedules present)"
        )
        trigger = "schedule"

    if trigger == "schedule":
        logger.info("📅 Phase A: Schedule-based trigger")
        _agent_state.enter_phase("phase_a")
        _result = await _phase_a_schedule(
            mcp_executor, llm_chat, parsed, policy,
            session_context=session_ctx,
        )
        _agent_state.complete_phase("phase_a")
        logger.info(_agent_state.summary())
        return _result
    else:
        logger.info("📌 Phase A: Direct trigger")
        _agent_state.enter_phase("phase_a")
        _result = await _phase_a_direct(mcp_executor, llm_chat, parsed, policy, session_context=session_ctx)
        _agent_state.complete_phase("phase_a")
        logger.info(_agent_state.summary())
        return _result
