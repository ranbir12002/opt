# backend/utils/crossroads.py
"""
Universal LLM Decision-Maker for Crossroads.

When any part of the system hits a decision point (ambiguous data,
missing fields, multiple options), it calls resolve_crossroads() to
get an LLM-powered decision instead of relying on hardcoded if/else.

Two entry points:
- resolve_crossroads()       — original API, backward compatible
- resolve_with_context()     — enhanced API with RequestTracker, domain knowledge,
                               and tool catalog subsetting

Registration API (for agents to extend without modifying this file):
- register_domain_knowledge(topic, content)   — add domain knowledge topics
- register_crossroad_type(name, prompt, ...)  — add new crossroad types
- register_cache_key_builder(type, fn)        — custom cache key for a type
- register_cache_template_applier(type, fn)   — custom cache template applier
- register_fallback(type, fn)                 — custom deterministic fallback
- register_relevant_tools(type, agent, tools) — tool subsetting per type+agent

Usage:
    from utils.crossroads import resolve_crossroads, resolve_with_context

    # Simple (backward compatible):
    result = await resolve_crossroads(
        crossroad_type="ambiguous_match",
        question="Multiple employees match 'John'. Which one?",
        context={"query": "John", "candidates": [...]},
        llm_chat=llm_chat,
    )

    # Enhanced (with full context):
    result = await resolve_with_context(
        crossroad_type="resolution",
        question="Can't match staff name to schedule",
        context={...},
        tracker=request_tracker,
        domain_topics=["simpro_employees", "simpro_schedules"],
        agent_name="schedule",
        tool_catalog=full_catalog,
        llm_chat=llm_chat,
    )
"""

from __future__ import annotations
import json
import hashlib
import logging
from typing import Any, Callable, Dict, List, Optional

from utils.decision_journal import record_decision

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Domain Knowledge Registry
# ═══════════════════════════════════════════════════════════════════════════
# Each topic contains verified facts about the ERP data structures.
# Agents and modules can register additional topics at runtime via
# register_domain_knowledge().

_DOMAIN_KNOWLEDGE: Dict[str, str] = {
    "simpro_schedules": """SCHEDULES:
- Schedule objects have: ID, Type ("job"/"quote"/"activity"), Reference ("JobID-CostCentreID"), Staff (object with ID only — no name), Blocks (array of time blocks), Date
- get_schedules(date) returns all schedules for that date. Staff objects contain ONLY Staff.ID — not staff name.
- get_schedule_details(schedule_id) returns FULL schedule info including Staff.ID, Blocks, Type, Reference
- Schedule Reference field format: "JobID-CostCentreID" — parse with split('-') to extract both IDs
- To find a staff member's schedule: get_schedules by date, then match Staff.ID against a known StaffID
- If you have schedule_id but need other IDs: call get_schedule_details to get all info""",

    "simpro_employees": """EMPLOYEES & STAFF:
- list_employees returns: ID, Name (a SINGLE combined "Name" field like "Stephen Sibbinson")
- Valid columns for list_employees: "ID,Name" — these are the ONLY valid columns
- NEVER use columns="GivenName,FamilyName" — this will cause a 422 error. GivenName/FamilyName are NOT valid column names.
- get_employee_details(employee_id) returns full details for one employee
- To resolve staff name → StaffID: call list_employees with columns="ID,Name", fuzzy-match the "Name" field, extract "ID"
- list_contractors returns: ID, Name — same pattern as employees
- Some staff may be contractors, not employees — try both list_employees and list_contractors if needed""",

    "simpro_jobs": """JOBS & HIERARCHY:
- Jobs contain Sections; Sections contain Cost Centres (3-level hierarchy)
- search_jobs with filters={"Name": "%keyword%"} finds jobs by name
- search_jobs with filters={"Site.Name": "%keyword%"} finds jobs by site/address
- SiteName: when user references a site, location, or address instead of a job name, extract it as SiteName
- get_job_sections(job_id) returns sections list with ID, Name
- get_job_section_cost_centres(job_id, section_id) returns cost centres in that section
- To find which section a cost centre belongs to: iterate sections, check cost centres in each
- JobID can be extracted from schedule Reference field: "20990-116534" → JobID=20990""",

    "column_constraints": """API COLUMN CONSTRAINTS (CRITICAL — violating these causes 422 errors):
- list_employees: columns parameter accepts ONLY "ID,Name". Default is "ID,Name".
- list_contractors: columns parameter accepts ONLY "ID,Name".
- get_schedules: does NOT accept a columns parameter for filtering fields.
- get_schedule_details: does NOT accept a columns parameter.
- search_jobs: has search/keyword params, NOT a columns filter.
- When suggesting tool calls, NEVER invent column names — only use documented defaults.
- If you're unsure what columns a tool accepts, omit the columns parameter entirely (use defaults).""",

    "schedule_operations_sop": """SCHEDULE OPERATIONS SOP:
- CREATE requires: StaffID (or StaffName), JobID (or JobName or SiteName), SectionID, CostCentreID, Date, StartTime, Blocks (hours)
  - SiteName can be used instead of JobName — resolved via Site.Name filter on search_jobs
  - ScheduleRate defaults to 1 (standard rate)
  - Time blocks must align to 15-minute intervals
- UPDATE requires: schedule_id + fields to change. Omitted fields preserve existing values.
  - BlocksAdjust (e.g., "+2", "-1") calculates: new_blocks = existing_blocks + adjustment
- DELETE requires: schedule_id only. Found by matching staff + date via get_schedules.
- LOCK/UNLOCK: requires schedule_id only. Sets IsLocked to true/false.
- All schedule mutations use the job cost centre schedule endpoints (not the top-level schedule endpoint).""",

    "simpro_invoices": """INVOICES:
- search_invoices finds invoices by various criteria
- get_invoice_details returns full invoice data
- create_invoice requires: Type, Jobs, DateIssued, Stage, PerItem
- Type options: "TaxInvoice", "Deposit", "ProgressInvoice", "RequestForClaim"
- Stage options: "Approved", "Pending"
- PerItem=true (itemized): requires CostCenters array with specific CC claims
- PerItem=false (consolidated): CostCenters MUST be omitted — Simpro invoices everything
- 422 "Cost Centers must be removed" → PerItem is false but CostCenters were included
- 422 "Cost centre already 100% claimed" → The CC has already been fully invoiced
- CostCenters format: [{"ID": <cc_id>, "Claim": {"Percent": <n>}, "Items": [{"ID": <id>, "Quantity": <n>}]}]
- Invoices are linked to Jobs and Cost Centres
- SOP defines defaults for Type, Stage, PerItem — agent validates user requests against SOP""",

    "simpro_work_orders": """WORK ORDERS / CONTRACTOR JOBS:
- Created via POST /costCenters/{ccID}/contractorJobs/
- Body: {Contractor: {ID}, Description, Materials (amount), Labor (amount), TaxCode: {ID}, DateIssued}
- Updated via PATCH /costCenters/{ccID}/contractorJobs/{cjID}/ — returns 204
- Deleted via DELETE /costCenters/{ccID}/contractorJobs/{cjID}/ — returns 204
- Cannot change Contractor field via PATCH — only Description, Materials, Labor, TaxCode, Items, dates
- get_contractor_job_details(contractor_job_id) returns full CJ data including _href field
- _href format: /api/v1.0/companies/{companyID}/jobs/{jobID}/sections/{sectionID}/costCenters/{ccID}/contractorJobs/
  → Parse _href to extract parent job_id, section_id, cost_centre_id
- get_contractor_jobs_by_cost_centre(job_id, section_id, cost_centre_id) lists CJs on a cost centre
- Status values: "Pending", "For Review", "Approved", "Completed", "Invoiced"
- Cost centre catalog items: GET /costCenters/{ccID}/catalogs/
- Cost centre labour items: GET /costCenters/{ccID}/labors/
- Cost centre one-off items: GET /costCenters/{ccID}/oneOffItems/
- Department resolution: cost centre types have IncomeAccountNo field
- Contractor list: columns=ID,Name only
- Agent follows SOP for all defaults and business rules""",

    "simpro_departments": """DEPARTMENTS (derived via Setup Cost Centres + Chart of Accounts):
- Departments are NOT stored directly in Simpro — they are derived from the income account chain
- Resolution chain: Setup Cost Centre -> IncomeAccountNo -> Chart of Accounts (by Number) -> Account Name -> Department
- get_setup_cost_centres returns: [{ID, Name, IncomeAccountNo}, ...]
- get_chart_of_accounts returns: [{ID, Name, Number, Type, Archived}, ...]
- department_mapping.json (client config) maps department names to account number patterns
- resolve_department() handles bidirectional lookup:
  (a) Cost centre ID/name -> department name
  (b) Department name -> list of cost centres
- DepartmentCache is loaded once daily and shared across all requests
- Some cost centres may have no IncomeAccountNo (unclassified)""",

    "resolution_patterns": """COMMON RESOLUTION PATTERNS:
1. "Can't match staff name to schedule":
   → Call list_employees(columns="ID,Name"), fuzzy-match by Name field, get StaffID
   → Then match schedules by Staff.ID
2. "Section not found for cost centre" or "Need section_id for a job":
   → Call get_job_sections(job_id) — extract FIRST section ID with extract="[0].ID", save_as="section_id"
   → Then call get_job_section_cost_centres(job_id, section_id=$collected.section_id)
   CRITICAL: get_job_section_cost_centres REQUIRES section_id — it will fail without it.
   Multi-step strategy example:
   {"steps": [
     {"tool": "get_job_sections", "params": {"job_id": 23276}, "extract": "[0].ID", "save_as": "section_id"},
     {"tool": "get_job_section_cost_centres", "params": {"job_id": 23276, "section_id": "$collected.section_id"}, "extract": "[0].ID", "save_as": "cost_centre_id", "precondition": "section_id"}
   ]}
3. "Schedule found but can't extract job/cost centre IDs":
   → Parse the Reference field: "20990-116534" → JobID=20990, CostCentreID=116534
   → Or call get_schedule_details(schedule_id) for full info
4. "No job found":
   → search_jobs with partial name, or get_schedule_details if schedule_id known
5. "Multiple schedules, can't pick one":
   → Filter by Staff.ID, JobID, or CostCentreID from already-collected data
6. "Contractor job ID known but need parent IDs":
   → Call get_contractor_job_details(contractor_job_id), parse the _href field
   → _href contains /jobs/{jobID}/sections/{sectionID}/costCenters/{ccID}/
7. "Multiple contractor jobs on cost centre, can't pick one":
   → Filter by Contractor.Name or Contractor.ID from user context

STEP CHAINING RULES:
- When step N extracts a value with save_as="X", step N+1 can reference it as "$collected.X" in params.
- ALWAYS use save_as with a simple key like "section_id", "cost_centre_id", "job_id" — NOT composite keys like "sections_23276".
- ALWAYS add precondition to dependent steps: if step 2 needs step 1's output, set precondition to step 1's save_as key.
- extract path navigates the tool result JSON: "[0].ID" gets the ID of the first item in the result array.
- get_job_section_cost_centres ALWAYS requires BOTH job_id AND section_id. Never call it with only job_id.""",
}


def register_domain_knowledge(topic: str, content: str) -> None:
    """
    Register a new domain knowledge topic at runtime.

    Agents and modules call this to add domain context that the LLM
    can use when making crossroads decisions. Overwrites if topic exists.

    Args:
        topic: Unique topic key (e.g. "simpro_quotes", "simpro_customers")
        content: Multi-line string with verified facts about this topic
    """
    _DOMAIN_KNOWLEDGE[topic] = content
    logger.debug(f"Crossroads: registered domain knowledge topic '{topic}'")


# Which domain topics each crossroad type needs (mutable — agents can extend)
_CROSSROAD_DOMAIN_MAP: Dict[str, List[str]] = {
    "field_assembly": ["schedule_operations_sop"],
    "ambiguous_match": ["simpro_employees", "simpro_schedules", "simpro_jobs", "simpro_work_orders", "schedule_operations_sop"],
    "error_recovery": ["column_constraints", "simpro_invoices", "simpro_work_orders", "schedule_operations_sop"],
    "resolution": [
        "simpro_schedules", "simpro_employees", "simpro_jobs",
        "simpro_work_orders", "column_constraints", "resolution_patterns", "schedule_operations_sop",
    ],
    "clarification_custom": [
        "simpro_employees", "simpro_jobs", "schedule_operations_sop",
    ],
}


# ═══════════════════════════════════════════════════════════════════════════
# Tool Catalog Subsetting (pluggable — Point 5)
# ═══════════════════════════════════════════════════════════════════════════
# Agents register which tools are relevant for each crossroad type.
# This saves tokens by only showing the LLM tools it might need.

_RELEVANT_TOOLS: Dict[str, Dict[str, List[str]]] = {
    "resolution": {
        "schedule": [
            "list_employees", "get_employee_details",
            "list_contractors", "get_contractor_details",
            "get_schedules", "get_schedule_details",
            "get_job_sections", "get_job_section_cost_centres",
            "get_job_cost_centre_schedules", "get_job_cost_centre_schedule_details",
            "search_jobs", "get_job_details",
            "delete_job_cost_centre_schedule",
        ],
        "invoice": [
            "search_jobs", "get_job_details",
            "search_invoices", "get_invoice_details",
            "list_employees", "get_employee_details",
            "search_customers", "get_customer_details",
        ],
        "workorder": [
            "list_contractors", "get_contractor_details",
            "get_schedules", "get_schedule_details",
            "search_jobs", "get_job_details",
            "get_job_sections", "get_job_section_cost_centres",
            "get_job_cost_centre_details",
            "get_cost_centre_catalog_items",
            "get_cost_centre_labour_items",
            "get_cost_centre_one_off_items",
            "get_cost_centre_types",
            "create_contractor_job",
            "get_contractor_job_details",
            "get_contractor_jobs_by_cost_centre",
            "update_contractor_job",
            "delete_contractor_job",
        ],
    },
}


def register_relevant_tools(
    crossroad_type: str,
    agent_name: str,
    tool_names: List[str],
) -> None:
    """
    Register which tools are relevant for a crossroad type + agent combo.

    Args:
        crossroad_type: e.g. "resolution", "error_recovery"
        agent_name: e.g. "schedule", "invoice", "workorder"
        tool_names: List of MCP tool names relevant for this combo
    """
    if crossroad_type not in _RELEVANT_TOOLS:
        _RELEVANT_TOOLS[crossroad_type] = {}
    _RELEVANT_TOOLS[crossroad_type][agent_name] = tool_names
    logger.debug(f"Crossroads: registered {len(tool_names)} tools for {crossroad_type}/{agent_name}")


def _subset_catalog(
    full_catalog: Dict[str, Any],
    crossroad_type: str,
    agent_name: str,
) -> Dict[str, Any]:
    """Return only the tools relevant for this crossroad type + agent."""
    type_map = _RELEVANT_TOOLS.get(crossroad_type)
    if not type_map:
        return full_catalog  # No subsetting configured — pass everything
    relevant_names = type_map.get(agent_name)
    if relevant_names is None:
        return full_catalog  # No subsetting for this agent — pass everything
    return {k: v for k, v in full_catalog.items() if k in relevant_names}


# ═══════════════════════════════════════════════════════════════════════════
# Crossroad Type Prompts (pluggable — agents can register new types)
# ═══════════════════════════════════════════════════════════════════════════

_CROSSROAD_PROMPTS: Dict[str, str] = {
    "ambiguous_match": """You resolve ambiguous matches in a construction back-office system.

Multiple items match the user's input. Decide which is the best match, or if the user should be asked to clarify.

REASONING STEPS (work through this before outputting JSON):
1. Write out the query string the user provided.
2. For each candidate, identify the match type to the query: exact / substring / partial-word / no-match.
3. Is there a clear winner? A candidate wins if its name contains the full query string, OR if the score gap vs #2 is > 30.
4. Check context clues (_user_question): does the user mention a job, date, or role that favors one candidate?
5. Only output "clarify" if two candidates are genuinely indistinguishable given the query and context.

SCORING RULES:
1. Exact match (case-insensitive) always wins — confidence 0.95+, decision "select"
2. Single close match (score gap > 30 between #1 and #2) — auto-select, confidence 0.85+
3. If only one candidate exists, always select it
4. Substring matches: "stephen" matches "Stephen Sibbinson" — this is a strong match

NAME MATCHING (CRITICAL for close scores):
- When multiple candidates have similar scores, COMPARE THE NAMES to the query.
- Pick the candidate whose name most closely matches the query string.
- "allister andrew" → "Allister Andrews" (near-exact) ALWAYS beats a partial match on a different name.
- The source field tells you where the match came from (list_employees vs list_contractors) — use this as a tiebreaker if names are equally close.
- Only say "clarify" if the names are genuinely indistinguishable from the query (e.g. two people both named "John Smith").

CONTEXT CLUES (use _user_question if available):
- If the user mentions a job name, prefer the candidate linked to that job
- If the user mentions a date, prefer the candidate active on that date
- If the user mentions a role or position, use it to narrow candidates
- Names are case-insensitive: "stephen" = "Stephen" = "STEPHEN"

Return ONLY valid JSON:
{"reasoning": "<think through name closeness step by step before deciding>", "decision": "select"|"clarify", "fields": {"selected_id": <id or null>, "selected_name": "<name or null>"}, "errors": [], "confidence": <0.0-1.0>}""",

    "error_recovery": """You handle error recovery for a construction back-office system (Simpro ERP).

An operation failed. Analyze the error and decide how to recover.

COMMON SIMPRO ERROR PATTERNS:
- 422 with "Invalid columns found" → Wrong column names passed to API. list_employees only accepts columns="ID,Name".
- 422 with "ScheduleRate" → Missing or invalid rate ID (default should be 1).
- 404 Not Found → Resource ID is wrong or was deleted. Inform user with clear message.
- 400 Bad Request → Usually malformed payload. Check field types and required fields.
- 403 Forbidden → Permission issue. Inform user to check Simpro access/permissions.
- 409 Conflict → Schedule overlap or duplicate. Inform user with details.
- 502/503/504 or timeout → Simpro is temporarily unavailable. Suggest retry.

RECOVERY PRIORITIES:
1. "retry" — ONLY for transient errors (timeout, 502, 503, 504, connection reset)
2. "inform_user" — For ALL business logic errors (provide clear, jargon-free message)
3. "skip" — Only in batch operations where one row failing shouldn't block the rest

TOOL HISTORY (if _tool_history is in context):
- Check what tools were already called and what failed
- Do NOT suggest retrying the exact same call with the same params if it already failed
- Suggest corrective action based on the failure pattern

Return ONLY valid JSON:
{"reasoning": "<analyze the error pattern and recovery options before deciding>", "decision": "retry"|"skip"|"inform_user", "fields": {"message": "<user-friendly message>"}, "errors": [], "confidence": <0.0-1.0>}""",

    "resolution": """You are a resolution strategist for an AI-powered construction back-office platform (Simpro ERP).

A process is STUCK — it tried to resolve data but failed. You receive full context about:
- stuck_point: what failed and why (includes error message and any partial data)
- collected_data: all IDs/values successfully resolved so far
- failed_attempts: what strategies were already tried and failed
- available_tools: MCP tools with descriptions and parameter schemas
- _user_question: the original user request (if available)
- _tool_history: what tools were already called and their results (if available)

Your job: figure out a concrete strategy using the available tools to unblock the process.

THINK STEP-BY-STEP:
1. What data do we HAVE? (look at collected_data — IDs, names, dates already resolved)
2. What data do we NEED? (parse the stuck_point error carefully)
3. What's the GAP? (e.g., we have a name but need an ID)
4. Check _tool_history: what tools were already called? Did any fail? What data came back?
5. Which available tool could bridge the gap? (read tool descriptions + required_params)
6. Can we supply the required params from collected_data? If not, chain another tool first.
7. SIMULATE: "If I call tool A and get result X, then I call tool B with X, will that give me what I need?"

CRITICAL CONSTRAINTS:
- NEVER suggest columns="GivenName,FamilyName" for list_employees — the only valid columns are "ID,Name"
- When calling list_employees, use columns="ID,Name" or omit columns entirely
- Schedule Staff objects contain ONLY Staff.ID — never expect staff names in schedule data
- To match a staff name to a schedule: first resolve name→StaffID via list_employees, then match by Staff.ID
- If _tool_history shows a tool already failed with certain params, do NOT repeat the same call

IMPORTANT RULES:
- ALWAYS check available_tools and their required_params before suggesting a tool
- Only use parameter values from collected_data or stuck_point context — never invent values
- Use $collected.<key> syntax to reference values from collected_data
- Use $stuck.<key> syntax to reference values from the stuck_point context
- If a previous attempt failed, suggest a DIFFERENT strategy — don't repeat what failed
- If you truly cannot find a viable strategy, return decision: "exhausted"
- Keep strategies focused: 1-3 steps max

MULTI-STEP PLANNING:
- Each step can declare a "precondition" — a key that must exist in collected_data before running
- Each step can declare "on_fail" — what to do if extraction fails: "skip_to_next" or "exhausted"
- This allows conditional chains: step 2 only runs if step 1 succeeded

SUGGEST OPTIONS (user clarification):
- If partial_data contains available alternatives (e.g. "available_cost_centres", "available_sections", "available_staff") AND:
  - Previous strategies already failed to find a match (check failed_attempts), OR
  - The searched name clearly does not resemble any available option (obvious mismatch)
  THEN return decision: "suggest_options" with the alternatives so the user can pick the right one.
- DECISION PRIORITY:
  1. "try_strategy" — prefer this if a tool-based strategy could plausibly resolve the match (e.g. search other sections, fuzzy match in a different list). Especially on attempt 1.
  2. "suggest_options" — use when no strategy can find the match but alternatives exist in partial_data. Copy options directly from partial_data — do NOT invent options.
  3. "exhausted" — no alternatives available and no strategy left.

Return ONLY valid JSON. Use ONE of these formats:

For try_strategy:
{
  "reasoning": "<step-by-step: what data we have, what we need, which tool bridges the gap, why this strategy should work>",
  "decision": "try_strategy",
  "strategy": {
    "description": "<1-line summary of what this strategy does>",
    "steps": [
      {
        "tool": "<tool_name from available_tools>",
        "params": {"<param>": "<value or $collected.X or $stuck.X>"},
        "extract": "<dot-path to the field we need, e.g. 'employees' or 'schedules[0].Staff.ID'>",
        "match_by": "<optional: field name to fuzzy-match within extracted list, e.g. 'Name'>",
        "match_value": "<optional: $collected.X or $stuck.X — value to match against>",
        "save_as": "<key name to store the result in collected_data for next steps>",
        "precondition": "<optional: key that must exist in collected_data before this step runs>",
        "on_fail": "<optional: 'skip_to_next' or 'exhausted' — what to do if extraction returns null>"
      }
    ]
  },
  "reasoning": "<brief explanation of why this strategy should work>",
  "confidence": <0.0-1.0>
}

For suggest_options:
{
  "reasoning": "<explain why no tool strategy can resolve this and why these options are the right ones to surface>",
  "decision": "suggest_options",
  "suggest_field": "<field name that needs user selection, e.g. 'CostCentreName'>",
  "available_options": [{"id": <id>, "name": "<name>"}, ...],
  "strategy": null,
  "reasoning": "<brief explanation of why no strategy can resolve this>",
  "confidence": <0.0-1.0>
}

For exhausted:
{
  "reasoning": "<list every strategy tried and why each failed, confirming nothing is left to try>",
  "decision": "exhausted",
  "strategy": null,
  "reasoning": "<what was tried and why nothing works>",
  "confidence": <0.0-1.0>
}""",

    "clarification_custom": """You interpret custom text input from a clarification dropdown in a construction back-office system (Simpro ERP).

The user was shown a clarification dropdown with pre-populated options but chose "Other (specify)" and typed their own value.

YOUR JOB: First check for skip/cancel intent, then determine what field(s) and value(s) to merge.

STEP 1 — SKIP / CANCEL INTENT DETECTION (check this FIRST):
If the user's input indicates they want to skip, ignore, or cancel this row/action, return a skip or cancel decision.
Skip signals: "skip", "skip this", "ignore", "pass", "already done", "already created", "this is done", "not needed", "n/a", "na", "none", "leave it", "never mind", "forget this one", "don't need this", "its already created", "this is already created", "already exists"
→ Return: {"reasoning": "<explain why this matches a skip signal>", "decision": "skip", "fields": {}, "confidence": 0.9}

Cancel ALL signals: "cancel everything", "cancel all", "stop everything", "cancel the whole thing", "abort all"
→ Return: {"reasoning": "<explain why this matches a cancel-all signal>", "decision": "cancel_all", "fields": {}, "confidence": 0.9}

STEP 2 — COURSE CORRECTION DETECTION:
If the user's input changes the NATURE of the original request rather than filling in a missing field:
- "actually make it a different job" / "use a different job instead" → direction change
- "change the staff to Mike instead" / "wrong person entirely" → correction to already-resolved field
- "I want to do a work order instead" / "make it an invoice not a schedule" → agent switch
→ Return: {"reasoning": "<explain what the user is actually asking for and why this is a direction change>", "decision": "redirect", "fields": {}, "new_intent": "<what the user actually wants>", "confidence": 0.8}

STEP 3 — FIELD RESOLUTION (only if not skip/cancel/redirect):

FIELD TYPES AND MAPPINGS:
- StaffName / StaffID: Staff member (employee or contractor)
- JobName / JobID / SiteName: Job or site
- SectionName / SectionID: Job section
- CostCentreName / CostCentreID: Cost centre within a section
- QuoteName / QuoteID: Quote
- ScheduleID: Schedule identifier (numeric)
- Schedule: When clarifying which schedule, if user gives a staff name → return StaffName so the system re-resolves by staff+date lookup

RULES:
1. If the input is a pure number AND context suggests it's an entity ID → return {FieldID: number}
   e.g., "4032" for StaffName field → {"StaffID": 4032}
2. If the input is text that looks like a name → return {FieldName: "text"}
   e.g., "rick lacey" for StaffName field → {"StaffName": "rick lacey"}
3. If the user is correcting the entity TYPE → return the corrected field
   e.g., "its actually a job id not a site" with value "20527" → {"JobID": 20527}
4. SPECIAL: When field_being_clarified is "Schedule" and user provides a staff name or ID:
   - Staff name → {"StaffName": "the name"} (system will re-lookup schedules by staff+date)
   - Staff ID → {"StaffID": number}
5. If user says something like "staff name is X" or "the staff is X" → extract X as StaffName
6. Always return the most specific field possible

Return ONLY valid JSON:
For field resolution: {"reasoning": "<think through: is it an ID or a name? which field does it map to? why?>", "decision": "resolved", "fields": {"<field>": "<value>"}, "confidence": <0.0-1.0>}
For skip: {"reasoning": "<why this matches a skip signal>", "decision": "skip", "fields": {}, "confidence": <0.0-1.0>}
For cancel all: {"reasoning": "<why this matches a cancel-all signal>", "decision": "cancel_all", "fields": {}, "confidence": <0.0-1.0>}
For redirect: {"reasoning": "<what the user is asking for and why it is a direction change>", "decision": "redirect", "fields": {}, "new_intent": "<description>", "confidence": <0.0-1.0>}""",
}


def register_crossroad_type(
    name: str,
    prompt: str,
    domain_topics: Optional[List[str]] = None,
) -> None:
    """
    Register a new crossroad type at runtime.

    Args:
        name: Unique crossroad type name (e.g. "field_assembly")
        prompt: System prompt for this crossroad type
        domain_topics: Default domain topics to inject for this type
    """
    _CROSSROAD_PROMPTS[name] = prompt
    if domain_topics is not None:
        _CROSSROAD_DOMAIN_MAP[name] = domain_topics
    logger.debug(f"Crossroads: registered crossroad type '{name}'")


# Base system prompt prepended to all types
_BASE_SYSTEM_PROMPT = """You are a universal decision-maker for an AI-powered construction back-office platform (Simpro ERP).
You receive context about a decision point and return a structured JSON response.
Be precise. Use the data provided. Never hallucinate values not present in the context.
If _user_question is provided in the context, use it to understand the user's intent.
If _tool_history is provided, use it to avoid repeating failed approaches.
Return ONLY valid JSON — no explanation outside the JSON object."""


# ═══════════════════════════════════════════════════════════════════════════
# Cache (pattern-based, reset per agent run)
# ═══════════════════════════════════════════════════════════════════════════
# Pluggable: agents can register custom cache key builders and template
# appliers for crossroad types that need optimized caching (Points 2 & 3).

_crossroads_cache: Dict[str, Dict[str, Any]] = {}

# Registry: crossroad_type -> custom cache key builder function
# Signature: fn(crossroad_type: str, context: Dict) -> str
_CACHE_KEY_BUILDERS: Dict[str, Callable] = {}

# Registry: crossroad_type -> custom cached template applier function
# Signature: fn(template: Dict, context: Dict) -> Dict
_CACHE_TEMPLATE_APPLIERS: Dict[str, Callable] = {}


def register_cache_key_builder(
    crossroad_type: str,
    fn: Callable[[str, Dict[str, Any]], str],
) -> None:
    """
    Register a custom cache key builder for a crossroad type.

    The function receives (crossroad_type, context) and must return
    a string hash. This allows agents to build cache keys based on
    their specific field presence patterns for better cache hit rates.
    """
    _CACHE_KEY_BUILDERS[crossroad_type] = fn
    logger.debug(f"Crossroads: registered cache key builder for '{crossroad_type}'")


def register_cache_template_applier(
    crossroad_type: str,
    fn: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
) -> None:
    """
    Register a custom cached template applier for a crossroad type.

    The function receives (template, context) and must return a new
    result dict with actual values substituted from the current context.
    """
    _CACHE_TEMPLATE_APPLIERS[crossroad_type] = fn
    logger.debug(f"Crossroads: registered cache template applier for '{crossroad_type}'")


def reset_crossroads_cache():
    """Reset the crossroads cache. Call at the start of each agent run."""
    global _crossroads_cache
    _crossroads_cache = {}
    logger.debug("Crossroads cache reset")


def _build_cache_key(crossroad_type: str, context: Dict[str, Any]) -> str:
    """Build a cache key from the structural pattern (not actual values)."""
    # Check for registered custom builder first
    custom_builder = _CACHE_KEY_BUILDERS.get(crossroad_type)
    if custom_builder:
        return custom_builder(crossroad_type, context)

    # Generic fallback: hash the context key structure
    ctx_keys = sorted(k for k in context.keys() if not k.startswith("_"))
    return hashlib.md5(
        json.dumps({"type": crossroad_type, "ctx_keys": ctx_keys}, default=str).encode()
    ).hexdigest()


def _apply_cached_template(
    template: Dict[str, Any],
    context: Dict[str, Any],
    crossroad_type: str = "",
) -> Dict[str, Any]:
    """
    Apply a cached crossroads template to new data.

    If a custom applier is registered for the crossroad type, use it.
    Otherwise, return the template as-is with a "(cached)" annotation.
    """
    custom_applier = _CACHE_TEMPLATE_APPLIERS.get(crossroad_type)
    if custom_applier:
        return custom_applier(template, context)

    # Generic fallback: return template with cached annotation
    result = dict(template)
    result["reasoning"] = template.get("reasoning", "") + " (cached)"
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Fallback (deterministic, no LLM) — Pluggable (Point 3)
# ═══════════════════════════════════════════════════════════════════════════
# Agents can register custom fallbacks for their crossroad types.

# Registry: crossroad_type -> custom fallback function
# Signature: fn(crossroad_type: str, context: Dict) -> Dict
_FALLBACK_HANDLERS: Dict[str, Callable] = {}


def register_fallback(
    crossroad_type: str,
    fn: Callable[[str, Dict[str, Any]], Dict[str, Any]],
) -> None:
    """
    Register a custom deterministic fallback for a crossroad type.

    Called when the LLM is unavailable or returns invalid output.
    The function receives (crossroad_type, context) and must return
    a valid crossroads result dict.
    """
    _FALLBACK_HANDLERS[crossroad_type] = fn
    logger.debug(f"Crossroads: registered fallback for '{crossroad_type}'")


def _crossroads_fallback(crossroad_type: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministic fallback when LLM call fails.
    Checks for registered custom fallbacks first, then uses generic defaults.
    """
    # Check for registered custom fallback
    custom_fallback = _FALLBACK_HANDLERS.get(crossroad_type)
    if custom_fallback:
        return custom_fallback(crossroad_type, context)

    # Built-in fallbacks for core types
    if crossroad_type == "resolution":
        return {
            "decision": "exhausted",
            "strategy": None,
            "reasoning": "fallback: LLM unavailable, no resolution strategy possible",
            "confidence": 0.0,
        }

    # Generic fallback for any crossroad type
    return {
        "decision": "unknown",
        "fields": {},
        "errors": [{"field": "crossroad_type", "message": f"No fallback for: {crossroad_type}"}],
        "reasoning": f"fallback: no custom handler registered for '{crossroad_type}'",
        "confidence": 0.0,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Domain Knowledge Injection
# ═══════════════════════════════════════════════════════════════════════════

def _build_domain_section(
    crossroad_type: str,
    extra_topics: Optional[List[str]] = None,
) -> str:
    """Build domain knowledge section for the system prompt."""
    # Get default topics for this crossroad type
    topics = list(_CROSSROAD_DOMAIN_MAP.get(crossroad_type, []))

    # Add any extra topics requested by the caller
    if extra_topics:
        for t in extra_topics:
            if t not in topics and t in _DOMAIN_KNOWLEDGE:
                topics.append(t)

    if not topics:
        return ""

    sections = []
    for topic in topics:
        content = _DOMAIN_KNOWLEDGE.get(topic)
        if content:
            sections.append(content.strip())

    if not sections:
        return ""

    return "\n\nDOMAIN KNOWLEDGE:\n" + "\n\n".join(sections)


# ═══════════════════════════════════════════════════════════════════════════
# Main Functions
# ═══════════════════════════════════════════════════════════════════════════

_VALID_KEYS = {"decision", "fields", "errors", "reasoning", "confidence", "strategy", "suggest_field", "available_options"}


async def resolve_crossroads(
    crossroad_type: str,
    question: str,
    context: Dict[str, Any],
    options: Optional[List[str]] = None,
    llm_chat: Optional[Callable] = None,
) -> Dict[str, Any]:
    """
    Universal LLM decision-maker for crossroads (backward-compatible API).

    Any part of the system calls this when it hits a decision point
    and needs LLM reasoning to proceed.

    Args:
        crossroad_type: Category of decision (e.g. "ambiguous_match", "error_recovery")
        question: Human-readable description of what decision is needed
        context: All relevant pre-fetched data
        options: Explicit options if known
        llm_chat: PII-safe LLM chat function

    Returns:
        {
            "decision": str,
            "fields": Dict[str, Any],
            "reasoning": str,
            "errors": List[Dict],
            "confidence": float,
        }
    """
    # No LLM available — use fallback
    if not llm_chat:
        logger.warning("Crossroads: no llm_chat provided, using fallback")
        return _crossroads_fallback(crossroad_type, context)

    # Check cache (skip for resolution — each stuck point is unique)
    cache_key = None
    if crossroad_type != "resolution":
        cache_key = _build_cache_key(crossroad_type, context)
        if cache_key in _crossroads_cache:
            logger.info(f"🔀 Crossroads cache hit: {crossroad_type}")
            return _apply_cached_template(
                _crossroads_cache[cache_key], context, crossroad_type
            )

    # Build prompt
    type_prompt = _CROSSROAD_PROMPTS.get(crossroad_type)
    if not type_prompt:
        logger.warning(f"Crossroads: unknown type '{crossroad_type}', using fallback")
        return _crossroads_fallback(crossroad_type, context)

    # Inject domain knowledge into system prompt
    domain_section = _build_domain_section(crossroad_type)
    system_prompt = f"{_BASE_SYSTEM_PROMPT}\n\n{type_prompt}{domain_section}"

    user_payload = json.dumps({
        "crossroad_type": crossroad_type,
        "question": question,
        "context": context,
        "options": options,
    }, default=str)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_payload},
    ]

    try:
        raw = llm_chat(
            messages,
            response_format={"type": "json_object"},
            temperature=0.0,
            sanitize=False,  # Context contains only operational metadata, no PII
        )

        result = json.loads(raw)

        # Validate response structure
        if crossroad_type == "resolution":
            # Resolution type returns strategy, not fields
            if "decision" not in result:
                logger.warning("Crossroads: resolution LLM returned no 'decision', using fallback")
                return _crossroads_fallback(crossroad_type, context)
        elif "fields" not in result:
            logger.warning("Crossroads: LLM returned no 'fields' key, using fallback")
            return _crossroads_fallback(crossroad_type, context)

        # Normalize
        result.setdefault("decision", "llm_decided")
        result.setdefault("reasoning", "")
        result.setdefault("confidence", 0.8)
        result["confidence"] = max(0.0, min(1.0, float(result["confidence"])))

        if crossroad_type == "resolution":
            result.setdefault("strategy", None)
        else:
            result.setdefault("errors", [])

        # Cache the template (skip resolution — each is unique)
        if cache_key is not None:
            _crossroads_cache[cache_key] = result

        logger.info(
            f"🔀 Crossroads [{crossroad_type}]: decision={result['decision']}, "
            f"confidence={result['confidence']:.2f}, reason={result['reasoning'][:80]}"
        )

        # ── Journal: record crossroads decision ──
        record_decision(
            dimension="disambiguation",
            decision_type=f"crossroads_{crossroad_type}",
            decision_value=result.get("decision", ""),
            confidence=result.get("confidence", 0.0),
            reasoning=result.get("reasoning", "")[:300],
            context={"crossroad_type": crossroad_type, "question": question[:200]},
            outcome="success",
        )

        return result

    except json.JSONDecodeError as e:
        logger.warning(f"Crossroads: invalid JSON from LLM ({e}), using fallback")
        fb = _crossroads_fallback(crossroad_type, context)
        record_decision(
            dimension="disambiguation",
            decision_type=f"crossroads_{crossroad_type}_fallback",
            decision_value=fb.get("decision", "fallback"),
            confidence=0.0,
            reasoning=f"JSON decode error: {e}",
            outcome="failure",
        )
        return fb

    except Exception as e:
        logger.warning(f"Crossroads: LLM call failed ({e}), using fallback")
        fb = _crossroads_fallback(crossroad_type, context)
        record_decision(
            dimension="disambiguation",
            decision_type=f"crossroads_{crossroad_type}_fallback",
            decision_value=fb.get("decision", "fallback"),
            confidence=0.0,
            reasoning=f"LLM call failed: {e}",
            outcome="failure",
        )
        return fb


async def resolve_with_context(
    crossroad_type: str,
    question: str,
    context: Dict[str, Any],
    tracker: Optional[Any] = None,  # RequestTracker
    domain_topics: Optional[List[str]] = None,
    agent_name: Optional[str] = None,
    tool_catalog: Optional[Dict[str, Any]] = None,
    llm_chat: Optional[Callable] = None,
    options: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Enhanced crossroads call with full context injection.

    Enriches the context with:
    - User question + conversation history from RequestTracker
    - Tool execution history from RequestTracker
    - Domain knowledge for the requested topics
    - Subsetted tool catalog (only relevant tools)

    Then delegates to resolve_crossroads() for actual LLM call.

    Args:
        crossroad_type: Category of decision
        question: Human-readable description
        context: Pre-fetched data (operation-specific)
        tracker: RequestTracker with user context + tool history
        domain_topics: Extra domain topics beyond the type defaults
        agent_name: Agent calling this (for tool subsetting)
        tool_catalog: Full tool catalog (will be subsetted)
        llm_chat: PII-safe LLM chat function
        options: Explicit options if known
    """
    # Inject tracker context (user question, conversation, tool history)
    if tracker is not None:
        tracker_ctx = tracker.to_crossroads_context()
        context["_user_question"] = tracker_ctx.get("_user_question", "")
        context["_conversation_summary"] = tracker_ctx.get("_conversation_summary", [])
        context["_tool_history"] = tracker_ctx.get("_tool_history", [])

    # Subset and inject tool catalog
    if tool_catalog:
        context["available_tools"] = _subset_catalog(
            tool_catalog, crossroad_type, agent_name or ""
        )

    # Domain topics are handled by the prompt builder in resolve_crossroads
    # But if the caller wants extra topics, we inject domain knowledge directly
    if domain_topics:
        extra_domain = _build_domain_section(crossroad_type, extra_topics=domain_topics)
        if extra_domain:
            context["_domain_knowledge"] = extra_domain

    return await resolve_crossroads(
        crossroad_type=crossroad_type,
        question=question,
        context=context,
        options=options,
        llm_chat=llm_chat,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Auto-register schedule field_assembly (backward compatibility)
# ═══════════════════════════════════════════════════════════════════════════
# The schedule agent's field_assembly is the only type that was previously
# hardcoded. Register it from the dedicated module so existing callers
# (schedule_agent.py) continue to work via the crossroads interface.

def _auto_register_schedule_field_assembly():
    """Register schedule field_assembly from its dedicated module."""
    try:
        from utils.schedule_field_assembler import (
            FIELD_ASSEMBLY_PROMPT,
            FIELD_ASSEMBLY_DOMAIN_TOPICS,
            schedule_field_assembly_cache_key,
            schedule_apply_cached_template,
            schedule_field_assembly_fallback,
        )
        register_crossroad_type(
            "field_assembly",
            FIELD_ASSEMBLY_PROMPT,
            FIELD_ASSEMBLY_DOMAIN_TOPICS,
        )
        register_cache_key_builder("field_assembly", schedule_field_assembly_cache_key)
        register_cache_template_applier("field_assembly", schedule_apply_cached_template)
        register_fallback("field_assembly", schedule_field_assembly_fallback)
        logger.debug("Crossroads: auto-registered schedule field_assembly")
    except ImportError:
        logger.debug("Crossroads: schedule_field_assembler not available, skipping auto-register")


_auto_register_schedule_field_assembly()
