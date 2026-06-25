# svc-agent-purchase-order/src/po_agent.py
"""
Purchase Order Agent — Creates, updates, and deletes vendor orders in Simpro.

Two-phase workflow (Simpro-fetch path):
  Phase A (Prepare):
    - Resolve cost centres from schedules (by date / staff) OR from a direct
      job / cost-centre reference.
    - Fetch catalog, labour, and one-off items from each cost centre
      (same item source as the Work Order agent).
    - Fetch all Simpro vendors → write as "Name - ID" autocomplete list (Sheet 2).
    - Return a 2-sheet Excel review workbook:
        Sheet 1 "PO Items"  — items with Supplier + POGroup + Include columns.
        Sheet 2 "Suppliers" — full vendor list for Excel autocomplete.
    - User fills in / corrects the Supplier column using Excel autocomplete,
      edits Include Yes/No, optionally adjusts POGroup, and re-uploads.

  Phase B (Create):
    - Read Sheet 1 only (ignore Sheet 2).
    - Parse "Name - ID" Supplier cells → resolved supplier_id.
    - Group rows by (POGroup, supplier_id) → each unique pair = one PO.
    - For each group, if upsert_existing_po=true: check for an open PO from
      that supplier on the same cost centre; update it instead of creating new.
    - Return summary of created / updated POs.

Chat path (single request, no file):
  - Parse natural-language → action (create / update / delete / blank_po).
  - Resolve entities via EntityResolver.
  - Execute directly against Simpro MCP tools.

Blank PO path:
  - No line items required.
  - Creates a vendor order with only supplier + date + job reference.

All business rules (grouping strategy, upsert behaviour, blank PO policy,
supplier defaults) live in the SOP markdown. Swap the file per customer.
Simpro field names, MCP tool names, and API semantics are hard-coded here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Config (with fallback for standalone runs) ────────────────────────────────

try:
    from config import (
        SOP_MD_PATH,
        MAX_CLARIFICATIONS,
        FUZZY_MATCH_THRESHOLD,
        PO_EXCEL_COLUMNS,
        PO_SUPPLIER_SHEET_COLUMNS,
        PO_ITEMS_SHEET_NAME,
        PO_SUPPLIERS_SHEET_NAME,
        SUPPLIER_COL_SEPARATOR,
    )
except ImportError:
    SOP_MD_PATH = os.getenv(
        "PO_SOP_MD_PATH",
        os.path.join(os.path.dirname(__file__), "sop", "purchase_order_sop.md"),
    )
    MAX_CLARIFICATIONS = 5
    FUZZY_MATCH_THRESHOLD = 70
    PO_ITEMS_SHEET_NAME = "PO Items"
    PO_SUPPLIERS_SHEET_NAME = "Suppliers"
    SUPPLIER_COL_SEPARATOR = " - "
    PO_EXCEL_COLUMNS = [
        "ScheduleID", "JobID", "SectionID", "CostCentreID", "CostCentreName",
        "PartNumber", "Description", "Type", "Quantity", "UnitCost", "Total",
        "TaxCodeID", "Supplier", "POGroup", "Include",
    ]
    PO_SUPPLIER_SHEET_COLUMNS = ["Supplier"]

# ── Centralized backend utilities ─────────────────────────────────────────────

from utils.mcp_executor import MCPToolExecutor
from utils.entity_resolver import (
    EntityResolver,
    ResolutionError,
    AmbiguousResolutionError,
)
from utils.agent_state import create_agent_state


# ═══════════════════════════════════════════════════════════════════════════════
# SOP helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _read_sop(path: Optional[str] = None, sop_override: Optional[str] = None, max_chars: int = 32_000) -> str:
    """Read the SOP markdown (or .docx) to plain text. Prefers sop_override if provided."""
    if sop_override:
        logger.info("[SOP] Using DB override SOP for purchase_order (org-specific)")
        return sop_override  # already validated at upload time
    path = path or SOP_MD_PATH
    if not path or not os.path.exists(path):
        logger.warning(f"[SOP] Default purchase_order SOP not found at {path} — using empty SOP")
        return ""
    logger.info(f"[SOP] Using default purchase_order SOP from file: {path}")
    if path.endswith(".docx"):
        try:
            from docx import Document
            doc = Document(path)
            text = "\n".join(p.text for p in doc.paragraphs if p.text)
            return " ".join(text.split())[:max_chars]
        except Exception as e:
            logger.warning(f"Could not read SOP docx: {e}")
            return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()[:max_chars]


# ═══════════════════════════════════════════════════════════════════════════════
# LLM: SOP policy extraction
# ═══════════════════════════════════════════════════════════════════════════════

_PO_POLICY_SYSTEM = """\
You are a Purchase Order Planning Agent for Simpro ERP.
Read the SOP and extract a JSON policy describing how purchase orders should
be built for this organisation. Return STRICT JSON with exactly these keys:

{
  "default_tax_code_id": <integer or null>,
  "default_order_status": "<Pending|Approved>",
  "require_job_reference": <true|false>,
  "auto_approve_threshold": <number or null>,
  "description_format": "<itemized|summary>",
  "include_po_notes": <true|false>,
  "default_item_type": "<Material|Labour|OneOff>",
  "duplicate_check": <true|false>,
  "po_grouping": "<per_cost_centre|per_job|per_schedule>",
  "upsert_existing_po": <true|false>,
  "blank_po_allowed": <true|false>,
  "blank_po_stage": "<Pending|Approved>",
  "labour_supplier": "<supplier name string or null>",
  "item_inclusion_rules": {
    "include_catalog": <true|false>,
    "include_labour": <true|false>,
    "include_one_off": <true|false>,
    "exclusion_patterns": []
  },
  "notes": "<any extra business rules as a short string>"
}

Rules:
- NEVER invent values not in the SOP — use null for unknowns.
- po_grouping default: "per_cost_centre".
- upsert_existing_po default: true.
- blank_po_allowed default: true.
- blank_po_stage default: "Pending".
- labour_supplier: null means leave Labour rows blank for user to fill.
"""


def _llm_extract_policy(
    llm_chat: Callable,
    sop_text: str,
    user_text: str,
) -> Dict[str, Any]:
    """Ask the LLM to extract the PO policy from the SOP."""
    today = datetime.now().strftime("%Y-%m-%d")
    prompt = f"Today is {today}.\n\nSOP:\n{sop_text}\n\nUser request:\n{user_text}"
    try:
        raw = llm_chat(
            [
                {"role": "system", "content": _PO_POLICY_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        return json.loads(raw)
    except Exception as e:
        logger.warning(f"Policy extraction failed: {e} — using defaults")
        return {
            "default_tax_code_id": 1,
            "default_order_status": "Pending",
            "require_job_reference": True,
            "auto_approve_threshold": None,
            "description_format": "itemized",
            "include_po_notes": True,
            "default_item_type": "Material",
            "duplicate_check": True,
            "po_grouping": "per_cost_centre",
            "upsert_existing_po": True,
            "blank_po_allowed": True,
            "blank_po_stage": "Pending",
            "labour_supplier": None,
            "item_inclusion_rules": {
                "include_catalog": True,
                "include_labour": True,
                "include_one_off": True,
                "exclusion_patterns": [],
            },
            "notes": "",
        }


# ═══════════════════════════════════════════════════════════════════════════════
# LLM: Chat-based request parser
# ═══════════════════════════════════════════════════════════════════════════════

_CHAT_PARSE_SYSTEM = """\
You are a Purchase Order data extractor for Simpro ERP.
Extract structured data from a natural-language PO request.

Return STRICT JSON:
{
  "action": "<create|update|delete|blank_po>",
  "trigger": "<schedule_date|schedule_staff|cost_centre_direct|blank_po|chat>",
  "date": "<YYYY-MM-DD or null>",
  "staff_names": ["<string>"] or [],
  "supplier_name": "<string or null>",
  "supplier_id": <integer or null>,
  "job_name": "<string or null>",
  "job_id": <integer or null>,
  "section_name": "<string or null>",
  "section_id": <integer or null>,
  "cost_centre_name": "<string or null>",
  "cost_centre_id": <integer or null>,
  "purchase_order_id": <integer or null>,
  "order_date": "<YYYY-MM-DD or null>",
  "status": "<Pending|Approved|Archived|Voided or null>",
  "notes": "<string or null>",
  "blank_po": <true|false>,
  "line_items": [
    {
      "part_number": "<string or null>",
      "description": "<string>",
      "type": "<Material|Labour|OneOff>",
      "quantity": <number>,
      "unit_cost": <number>,
      "tax_code_id": <integer or null>
    }
  ]
}

Rules:
- trigger="schedule_date"   when user mentions schedules for a specific date.
- trigger="schedule_staff"  when user mentions schedules for specific staff names.
- trigger="cost_centre_direct" when user specifies a job/cost-centre directly.
- trigger="blank_po"        when user asks for a blank/empty PO.
- trigger="chat"            for everything else (explicit line items provided).
- action="blank_po"         synonym for trigger="blank_po", set blank_po=true.
- Dates: today={today}. Convert all relative dates to YYYY-MM-DD.
- staff_names: list of staff/contractor names from "for John" / "for John and Mary".
- line_items: [] if not mentioned.
- Do NOT invent IDs — leave as null if not explicitly given.
"""


def _llm_parse_chat_request(
    llm_chat: Callable,
    user_text: str,
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Parse a chat-based PO request into structured data."""
    today = date.today().isoformat()
    system = _CHAT_PARSE_SYSTEM.replace("{today}", today)
    messages = [{"role": "system", "content": system}]
    if conversation_history:
        messages.extend(conversation_history[-6:])
    messages.append({"role": "user", "content": user_text})
    try:
        raw = llm_chat(messages, response_format={"type": "json_object"}, temperature=0.0)
        return json.loads(raw)
    except Exception as e:
        logger.error(f"Chat parse failed: {e}")
        return {"error": "PARSE_FAILED", "message": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# Supplier helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _format_supplier(name: str, sid: int) -> str:
    """Format supplier as 'Name - ID' combined string."""
    return f"{name}{SUPPLIER_COL_SEPARATOR}{sid}"


def _parse_supplier_cell(cell: str) -> Tuple[Optional[int], str]:
    """
    Parse a 'Name - ID' cell back into (supplier_id, supplier_name).
    Returns (None, cell) if no separator found — caller should fuzzy-match.
    """
    cell = (cell or "").strip()
    if SUPPLIER_COL_SEPARATOR in cell:
        parts = cell.rsplit(SUPPLIER_COL_SEPARATOR, 1)
        try:
            return int(parts[1].strip()), parts[0].strip()
        except ValueError:
            pass
    return None, cell


async def _fetch_all_vendors(
    mcp_executor: MCPToolExecutor,
) -> List[Dict[str, Any]]:
    """
    Fetch all vendor/supplier contacts from Simpro (paginated).
    Returns list of {"id": int, "name": str, "formatted": "Name - ID"}.
    """
    all_vendors: List[Dict[str, Any]] = []
    page = 1
    page_size = 250
    while True:
        try:
            result = await mcp_executor.call_tool(
                "search_contacts",
                {"page": page, "page_size": page_size, "columns": "ID,CompanyName,Name"},
            )
            contacts = result if isinstance(result, list) else result.get("value", result.get("contacts", []))
            if not isinstance(contacts, list):
                break
            for c in contacts:
                name = c.get("CompanyName") or c.get("Name") or ""
                sid = c.get("ID")
                if name and sid:
                    all_vendors.append({
                        "id": sid,
                        "name": name,
                        "formatted": _format_supplier(name, sid),
                    })
            if len(contacts) < page_size:
                break
            page += 1
        except Exception as e:
            logger.warning(f"Vendor fetch page {page} failed: {e}")
            break
    logger.info(f"Fetched {len(all_vendors)} vendors for supplier sheet")
    return all_vendors


async def _resolve_supplier_by_name(
    name: str,
    vendors: List[Dict[str, Any]],
    llm_chat: Callable,
) -> Tuple[Optional[int], Optional[str]]:
    """
    Fuzzy-match a supplier name against the pre-fetched vendor list.
    Returns (supplier_id, supplier_name) or raises ResolutionError /
    AmbiguousResolutionError.
    """
    from utils.fuzzy_match import fuzzy_match_entities
    candidates = [{"ID": v["id"], "CompanyName": v["name"]} for v in vendors]
    matches = fuzzy_match_entities(
        name=name,
        candidates=candidates,
        name_field="CompanyName",
        threshold=FUZZY_MATCH_THRESHOLD,
    )
    if not matches:
        raise ResolutionError(f"Supplier '{name}' not found in Simpro.")
    if len(matches) == 1 or (
        len(matches) > 1 and matches[0]["score"] - matches[1]["score"] > 15
    ):
        best = matches[0]["candidate"]
        return best["ID"], best["CompanyName"]
    raise AmbiguousResolutionError(
        field="supplier_name",
        message=f"Multiple suppliers match '{name}'. Please choose:",
        options=[
            {"id": m["candidate"]["ID"], "label": m["candidate"]["CompanyName"]}
            for m in matches[:5]
        ],
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Item fetching (mirrors wo_agent._fetch_cost_centre_items exactly)
# ═══════════════════════════════════════════════════════════════════════════════

async def _fetch_cost_centre_items(
    mcp_executor: MCPToolExecutor,
    job_id: int,
    section_id: int,
    cost_centre_id: int,
    policy: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Fetch catalog, labour, and one-off items for a cost centre in parallel.
    Applies item_inclusion_rules from policy.
    Returns a unified list of item dicts.
    """
    rules = policy.get("item_inclusion_rules", {})
    include_catalog = rules.get("include_catalog", True)
    include_labour = rules.get("include_labour", True)
    include_one_off = rules.get("include_one_off", True)
    exclusion_patterns = [p.lower() for p in rules.get("exclusion_patterns", [])]

    base = {"job_id": job_id, "section_id": section_id, "cost_centre_id": cost_centre_id}

    def _totals(item: dict) -> Tuple[float, float, float]:
        total_obj = item.get("Total", {}) or {}
        qty = float(total_obj.get("Qty", 0)) if isinstance(total_obj, dict) else 0.0
        sell = item.get("SellPrice", {}) or {}
        unit = float(sell.get("ExTax", 0)) if isinstance(sell, dict) else 0.0
        amt = total_obj.get("Amount", {}) if isinstance(total_obj, dict) else {}
        total = float(amt.get("ExTax", 0)) if isinstance(amt, dict) else round(qty * unit, 2)
        return qty, unit, total

    async def _catalog():
        if not include_catalog:
            return []
        try:
            r = await mcp_executor.call_tool("get_cost_centre_catalog_items", base)
            items = r.get("catalog_items", r) if isinstance(r, dict) else r
            out = []
            for item in (items if isinstance(items, list) else []):
                cat = item.get("Catalog", {}) or {}
                qty, unit, total = _totals(item)
                out.append({
                    "ItemID": item.get("ID", ""),
                    "PartNumber": cat.get("PartNumber", "") if isinstance(cat, dict) else "",
                    "Description": cat.get("Name", "") if isinstance(cat, dict) else "",
                    "Type": "Material",
                    "Quantity": qty,
                    "UnitCost": unit,
                    "Total": total,
                })
            return out
        except Exception as e:
            logger.warning(f"Catalog fetch failed for cc={cost_centre_id}: {e}")
            return []

    async def _labour():
        if not include_labour:
            return []
        try:
            r = await mcp_executor.call_tool("get_cost_centre_labour_items", base)
            items = r.get("labour_items", r) if isinstance(r, dict) else r
            out = []
            for item in (items if isinstance(items, list) else []):
                lt = item.get("LaborType", {}) or {}
                qty, unit, total = _totals(item)
                out.append({
                    "ItemID": item.get("ID", ""),
                    "PartNumber": "",
                    "Description": lt.get("Name", "") if isinstance(lt, dict) else "",
                    "Type": "Labour",
                    "Quantity": qty,
                    "UnitCost": unit,
                    "Total": total,
                })
            return out
        except Exception as e:
            logger.warning(f"Labour fetch failed for cc={cost_centre_id}: {e}")
            return []

    async def _one_off():
        if not include_one_off:
            return []
        try:
            r = await mcp_executor.call_tool("get_cost_centre_one_off_items", base)
            items = r.get("one_off_items", r) if isinstance(r, dict) else r
            out = []
            for item in (items if isinstance(items, list) else []):
                qty, unit, total = _totals(item)
                out.append({
                    "ItemID": item.get("ID", ""),
                    "PartNumber": "",
                    "Description": item.get("Description", ""),
                    "Type": f"OneOff-{item.get('Type', 'Material')}",
                    "Quantity": qty,
                    "UnitCost": unit,
                    "Total": total,
                })
            return out
        except Exception as e:
            logger.warning(f"One-off fetch failed for cc={cost_centre_id}: {e}")
            return []

    cat_items, lab_items, oo_items = await asyncio.gather(_catalog(), _labour(), _one_off())
    all_items = cat_items + lab_items + oo_items

    if exclusion_patterns:
        all_items = [
            it for it in all_items
            if not any(pat in it.get("Description", "").lower() for pat in exclusion_patterns)
        ]
    return all_items


# ═══════════════════════════════════════════════════════════════════════════════
# Section reverse-lookup (same as wo_agent)
# ═══════════════════════════════════════════════════════════════════════════════

async def _find_section_for_cost_centre(
    mcp_executor: MCPToolExecutor,
    job_id: int,
    cost_centre_id: int,
) -> Optional[int]:
    """Find which section_id contains the given cost_centre_id on a job."""
    try:
        sections_result = await mcp_executor.call_tool(
            "get_job_sections", {"job_id": job_id}
        )
        sections = sections_result if isinstance(sections_result, list) else \
            sections_result.get("sections", [])
        for section in (sections if isinstance(sections, list) else []):
            sid = section.get("ID")
            if not sid:
                continue
            try:
                cc_result = await mcp_executor.call_tool(
                    "get_job_section_cost_centres",
                    {"job_id": job_id, "section_id": sid},
                )
                cc_list = cc_result if isinstance(cc_result, list) else \
                    cc_result.get("cost_centres", [])
                if any(cc.get("ID") == cost_centre_id for cc in (cc_list or [])):
                    return sid
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"Section reverse-lookup failed for job={job_id}, cc={cost_centre_id}: {e}")
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# POGroup key builder
# ═══════════════════════════════════════════════════════════════════════════════

def _make_po_group(
    policy: Dict[str, Any],
    job_id: Optional[int],
    cost_centre_id: Optional[int],
    schedule_id: Optional[Any],
) -> str:
    """
    Compute the POGroup key for a row based on SOP grouping strategy.
    Two rows with the same POGroup + same supplier → same PO.
    """
    strategy = policy.get("po_grouping", "per_cost_centre")
    if strategy == "per_job":
        return f"job_{job_id}" if job_id else "job_unknown"
    if strategy == "per_schedule":
        return f"sch_{schedule_id}" if schedule_id else f"cc_{cost_centre_id}"
    # default: per_cost_centre
    return f"cc_{cost_centre_id}" if cost_centre_id else "cc_unknown"


# ═══════════════════════════════════════════════════════════════════════════════
# Phase detection
# ═══════════════════════════════════════════════════════════════════════════════

def _is_po_reupload(extracted: Dict[str, Any]) -> bool:
    """
    Return True when the uploaded Excel is a Phase-B re-upload.
    Detected by presence of 'POGroup' + 'Include' headers in the items sheet.
    """
    if not extracted or not extracted.get("tables"):
        return False
    # Look for the items sheet specifically; fall back to first table
    items_table = next(
        (t for t in extracted["tables"]
         if PO_ITEMS_SHEET_NAME.lower().replace(" ", "") in
         (t.get("name") or "").lower().replace(" ", "")),
        extracted["tables"][0],
    )
    headers = [h.lower() for h in items_table.get("headers", [])]
    return "pogroup" in headers and "include" in headers


def _get_items_table(extracted: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return the items table from extracted, ignoring the Suppliers lookup sheet.
    """
    tables = extracted.get("tables", [])
    suppliers_name = PO_SUPPLIERS_SHEET_NAME.lower().replace(" ", "")
    # Filter out supplier lookup sheet
    item_tables = [
        t for t in tables
        if suppliers_name not in (t.get("name") or "").lower().replace(" ", "")
    ]
    return item_tables[0] if item_tables else (tables[0] if tables else {})


# ═══════════════════════════════════════════════════════════════════════════════
# Phase A — resolve cost centres from schedules
# ═══════════════════════════════════════════════════════════════════════════════

async def _resolve_cc_from_schedules(
    mcp_executor: MCPToolExecutor,
    parsed: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], str]:
    """
    Fetch schedules filtered by date and/or staff names.
    Returns (list of {job_id, cost_centre_id, schedule_id, staff_name}, error_msg).
    Mirrors wo_agent schedule-fetch logic but without the contractor-only filter
    (POs can be raised against any schedule, not just contractor schedules).
    """
    target_date = parsed.get("date") or date.today().isoformat()
    staff_names = parsed.get("staff_names") or []

    logger.info(f"📅 Fetching schedules for date={target_date}, staff={staff_names}")
    try:
        sched_result = await mcp_executor.call_tool("get_schedules", {
            "date_from": target_date,
            "date_to": target_date,
        })
    except Exception as e:
        return [], f"Could not fetch schedules for {target_date}: {e}"

    schedules = sched_result.get("schedules", sched_result)
    if isinstance(schedules, dict):
        schedules = schedules.get("schedules", [])
    if not isinstance(schedules, list) or not schedules:
        return [], f"No schedules found for {target_date}."

    # Filter by staff names if provided (fuzzy match on staff name)
    if staff_names:
        from utils.fuzzy_match import fuzzy_match_entities
        filtered = []
        for sched in schedules:
            staff = sched.get("Staff", {})
            staff_display = staff.get("Name", "") if isinstance(staff, dict) else ""
            for sname in staff_names:
                matches = fuzzy_match_entities(
                    sname, [{"ID": 1, "Name": staff_display}],
                    name_field="Name", threshold=FUZZY_MATCH_THRESHOLD,
                )
                if matches:
                    filtered.append(sched)
                    break
        schedules = filtered

    if not schedules:
        return [], f"No schedules found matching staff: {staff_names}."

    # Parse Reference field → job_id + cost_centre_id (same pattern as wo_agent)
    targets = []
    seen = set()
    for sched in schedules:
        if (sched.get("Type") or "").lower() != "job":
            continue
        ref = sched.get("Reference", "")
        if not ref or "-" not in ref:
            continue
        try:
            parts = ref.split("-")
            job_id = int(parts[0])
            cc_id = int(parts[1])
        except (ValueError, IndexError):
            continue
        key = (job_id, cc_id)
        if key in seen:
            continue
        seen.add(key)
        staff = sched.get("Staff", {})
        targets.append({
            "job_id": job_id,
            "cost_centre_id": cc_id,
            "schedule_id": sched.get("ID"),
            "staff_name": staff.get("Name", "") if isinstance(staff, dict) else "",
        })

    if not targets:
        return [], f"No job-type schedules with parseable References found for {target_date}."

    return targets, ""


# ═══════════════════════════════════════════════════════════════════════════════
# Phase A — build review rows
# ═══════════════════════════════════════════════════════════════════════════════

def _build_review_rows(
    targets: List[Dict[str, Any]],
    policy: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Flatten fetched targets into review row dicts for Sheet 1.
    Pre-fills Supplier for Labour rows if labour_supplier is set in policy.
    Computes POGroup per row based on grouping strategy.
    """
    labour_supplier = policy.get("labour_supplier") or ""
    rows = []
    for target in targets:
        job_id = target.get("job_id")
        section_id = target.get("section_id")
        cc_id = target.get("cost_centre_id")
        cc_name = target.get("cost_centre_name", f"CC-{cc_id}")
        schedule_id = target.get("schedule_id", "")

        po_group = _make_po_group(policy, job_id, cc_id, schedule_id)

        for item in target.get("items", []):
            item_type = item.get("Type", "")
            # Pre-fill supplier for Labour rows if SOP specifies a labour supplier
            if item_type == "Labour" and labour_supplier:
                supplier_cell = labour_supplier
            else:
                supplier_cell = ""  # user fills via autocomplete

            rows.append({
                "ScheduleID": schedule_id,
                "JobID": job_id or "",
                "SectionID": section_id or "",
                "CostCentreID": cc_id or "",
                "CostCentreName": cc_name,
                "PartNumber": item.get("PartNumber", ""),
                "Description": item.get("Description", ""),
                "Type": item_type,
                "Quantity": item.get("Quantity", 0),
                "UnitCost": item.get("UnitCost", 0),
                "Total": item.get("Total", 0),
                "TaxCodeID": policy.get("default_tax_code_id") or 1,
                "Supplier": supplier_cell,
                "POGroup": po_group,
                "Include": "Yes",
            })
    return rows


# ═══════════════════════════════════════════════════════════════════════════════
# Phase A — main entry (replaces old Phase A)
# ═══════════════════════════════════════════════════════════════════════════════

async def _phase_a_prepare(
    llm_chat: Callable,
    parsed: Dict[str, Any],
    policy: Dict[str, Any],
    mcp_executor: MCPToolExecutor,
    user_text: str,
) -> Dict[str, Any]:
    """
    Phase A: resolve cost centres, fetch items, build 2-sheet review Excel.

    Trigger paths:
      schedule_date / schedule_staff → fetch schedules → extract job/CC combos
      cost_centre_direct             → resolve job → section → CC directly
    """
    logger.info("📋 PO Agent Phase A: Prepare")
    trigger = parsed.get("trigger", "cost_centre_direct")
    resolver = EntityResolver(mcp_executor, llm_chat=llm_chat)
    session_id = str(uuid.uuid4())

    # ── Step 1: Resolve cost centre targets ───────────────────────────────────
    targets: List[Dict[str, Any]] = []

    if trigger in ("schedule_date", "schedule_staff"):
        raw_targets, err = await _resolve_cc_from_schedules(mcp_executor, parsed)
        if err:
            return {"success": False, "error": "SCHEDULE_RESOLVE_FAILED", "message": err}

        # Resolve section_id for each target (parallel)
        async def _enrich(t):
            jid, ccid = t["job_id"], t["cost_centre_id"]
            sid = await _find_section_for_cost_centre(mcp_executor, jid, ccid)
            if not sid:
                logger.warning(f"No section found for job={jid}, cc={ccid} — skipping")
                return None
            items = await _fetch_cost_centre_items(mcp_executor, jid, sid, ccid, policy)
            if not items:
                logger.info(f"No items for cc={ccid} — skipping")
                return None
            return {**t, "section_id": sid, "cost_centre_name": f"CC-{ccid}", "items": items}

        results = await asyncio.gather(*[_enrich(t) for t in raw_targets])
        targets = [r for r in results if r is not None]

    else:
        # cost_centre_direct or blank_po
        job_id = parsed.get("job_id")
        section_id = parsed.get("section_id")
        cost_centre_id = parsed.get("cost_centre_id")

        # Resolve job if only name given
        if not job_id and parsed.get("job_name"):
            try:
                job_id = await resolver.resolve_job(name=parsed["job_name"])
            except (ResolutionError, AmbiguousResolutionError) as e:
                return {
                    "success": False,
                    "needs_clarification": True,
                    "session_id": session_id,
                    "clarifications": [{"field": "job_name", "message": str(e),
                                        "options": getattr(e, "options", [])}],
                    "message": str(e),
                }

        if not job_id:
            return {
                "success": False,
                "error": "MISSING_JOB",
                "message": "Please provide a job name or ID to fetch items from.",
            }

        # Resolve section
        if not section_id and parsed.get("section_name"):
            try:
                section_id = await resolver.resolve_section(
                    job_id=job_id, name=parsed["section_name"]
                )
            except (ResolutionError, AmbiguousResolutionError) as e:
                return {
                    "success": False,
                    "needs_clarification": True,
                    "session_id": session_id,
                    "clarifications": [{"field": "section_name", "message": str(e),
                                        "options": getattr(e, "options", [])}],
                    "message": str(e),
                }

        # Resolve cost centre
        if not cost_centre_id and parsed.get("cost_centre_name") and section_id:
            try:
                cost_centre_id = await resolver.resolve_cost_centre(
                    job_id=job_id, section_id=section_id,
                    name=parsed["cost_centre_name"]
                )
            except (ResolutionError, AmbiguousResolutionError) as e:
                return {
                    "success": False,
                    "needs_clarification": True,
                    "session_id": session_id,
                    "clarifications": [{"field": "cost_centre_name", "message": str(e),
                                        "options": getattr(e, "options", [])}],
                    "message": str(e),
                }

        # Resolve section via reverse-lookup if still missing
        if not section_id and cost_centre_id:
            section_id = await _find_section_for_cost_centre(
                mcp_executor, job_id, cost_centre_id
            )

        if not section_id:
            return {
                "success": False,
                "error": "MISSING_SECTION",
                "message": "Could not determine the section. Please provide a section name or ID.",
            }

        items = await _fetch_cost_centre_items(
            mcp_executor, job_id, section_id, cost_centre_id, policy
        )

        targets = [{
            "job_id": job_id,
            "section_id": section_id,
            "cost_centre_id": cost_centre_id,
            "cost_centre_name": parsed.get("cost_centre_name") or f"CC-{cost_centre_id}",
            "schedule_id": "",
            "items": items,
        }]

    if not targets:
        return {
            "success": False,
            "error": "NO_ITEMS",
            "message": "No materials or labour items found in the matched cost centres.",
        }

    # ── Step 2: Fetch all vendors for Sheet 2 autocomplete ────────────────────
    vendors = await _fetch_all_vendors(mcp_executor)

    # ── Step 3: Build review rows ─────────────────────────────────────────────
    review_rows = _build_review_rows(targets, policy)

    # ── Step 4: Return result ─────────────────────────────────────────────────
    total_items = len(review_rows)
    total_ccs = len(targets)

    return {
        "success": True,
        "phase": "prepare",
        "needs_clarification": False,
        "session_id": session_id,
        "wo_review_rows": review_rows,        # Sheet 1 rows (presenter key)
        "supplier_list": vendors,             # Sheet 2 data for autocomplete
        "items_sheet_name": PO_ITEMS_SHEET_NAME,
        "suppliers_sheet_name": PO_SUPPLIERS_SHEET_NAME,
        "supplier_col_separator": SUPPLIER_COL_SEPARATOR,
        "message": (
            f"Review sheet ready: {total_items} item(s) across {total_ccs} "
            f"cost centre(s). Fill in the Supplier column using autocomplete "
            f"(type to filter), set Include=No to exclude rows, then re-upload."
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Phase B — create / upsert from reviewed Excel
# ═══════════════════════════════════════════════════════════════════════════════

async def _check_existing_po(
    mcp_executor: MCPToolExecutor,
    supplier_id: int,
    cost_centre_id: Optional[int],
    job_id: Optional[int],
) -> Optional[Dict[str, Any]]:
    """
    Check if an open vendor order from the same supplier exists for this
    cost centre / job. Returns the existing PO dict or None.
    """
    try:
        filters: Dict[str, Any] = {"Vendor.ID": supplier_id}
        if cost_centre_id:
            filters["AssignedTo"] = cost_centre_id
        result = await mcp_executor.call_tool("get_vendor_orders", filters)
        orders = result.get("vendor_orders", result) if isinstance(result, dict) else result
        if not isinstance(orders, list):
            return None
        for order in orders:
            stage = (order.get("Stage") or "").lower()
            if stage in ("pending", "approved"):
                return order
    except Exception as e:
        logger.warning(f"Existing PO check failed: {e}")
    return None


async def _phase_b_create(
    extracted: Dict[str, Any],
    policy: Dict[str, Any],
    mcp_executor: MCPToolExecutor,
) -> Dict[str, Any]:
    """
    Phase B: read re-uploaded Sheet 1, group rows by (POGroup, supplier_id),
    then create or upsert one vendor order per group.
    """
    logger.info("🏗️  PO Agent Phase B: Create")

    table = _get_items_table(extracted)
    headers = table.get("headers", [])
    rows = table.get("rows", [])

    created: List[Dict[str, Any]] = []
    updated: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []

    # ── Group included rows by (POGroup, supplier_id) ─────────────────────────
    groups: Dict[Tuple, List[Dict[str, Any]]] = defaultdict(list)
    unresolved: List[Dict[str, Any]] = []

    for row_data in rows:
        row = dict(zip(headers, row_data))
        if str(row.get("Include", "Yes")).strip().lower() not in ("yes", "y", "true", "1"):
            continue

        supplier_cell = str(row.get("Supplier") or "").strip()
        supplier_id, supplier_name = _parse_supplier_cell(supplier_cell)

        if supplier_id is None:
            if supplier_name:
                # No ID in cell — record as unresolved; Phase B won't proceed
                unresolved.append({"row": row, "supplier_name": supplier_name})
                continue
            else:
                # Blank supplier — skip row
                logger.info(f"Skipping row with blank Supplier: {row.get('Description')}")
                continue

        po_group = str(row.get("POGroup") or "").strip() or "default"
        groups[(po_group, supplier_id, supplier_name)].append(row)

    if unresolved:
        # Return clarification asking user to pick suppliers for unresolved rows
        return {
            "success": False,
            "needs_clarification": True,
            "session_id": str(uuid.uuid4()),
            "clarifications": [
                {
                    "field": "supplier_name",
                    "message": (
                        f"Supplier '{r['supplier_name']}' could not be resolved (no ID found). "
                        "Please use the autocomplete dropdown in the Supplier column "
                        "and re-upload."
                    ),
                }
                for r in unresolved[:MAX_CLARIFICATIONS]
            ],
            "message": f"{len(unresolved)} row(s) have unresolved supplier names.",
        }

    if not groups:
        return {
            "success": False,
            "error": "NO_INCLUDED_ROWS",
            "message": "No rows marked Include=Yes with a valid Supplier. Nothing to create.",
        }

    upsert = policy.get("upsert_existing_po", True)
    default_stage = policy.get("default_order_status", "Pending")
    default_tax = policy.get("default_tax_code_id") or 1

    # ── Process each group ────────────────────────────────────────────────────
    for (po_group, supplier_id, supplier_name), group_rows in groups.items():
        # Extract metadata from first row
        first = group_rows[0]
        job_id = int(first["JobID"]) if str(first.get("JobID", "")).strip().isdigit() else None
        cc_id = int(first["CostCentreID"]) if str(first.get("CostCentreID", "")).strip().isdigit() else None

        # Build line items
        line_items = []
        for row in group_rows:
            try:
                qty = float(row.get("Quantity") or 1)
                unit = float(row.get("UnitCost") or 0)
                tax = int(row.get("TaxCodeID") or default_tax)
            except (ValueError, TypeError):
                qty, unit, tax = 1.0, 0.0, default_tax

            line_items.append({
                "Description": row.get("Description", ""),
                "Type": row.get("Type", "Catalogue"),
                "Quantity": qty,
                "UnitCost": unit,
                "Total": round(qty * unit, 2),
                "TaxCode": {"ID": tax},
            })

        try:
            if upsert:
                existing = await _check_existing_po(mcp_executor, supplier_id, cc_id, job_id)
            else:
                existing = None

            if existing:
                # PATCH existing PO — add new line items
                po_id = existing.get("ID")
                await mcp_executor.call_tool(
                    "update_vendor_order",
                    {
                        "vendor_order_id": po_id,
                        "vendor_order_data": {"LineItems": line_items},
                    },
                )
                updated.append({
                    "po_id": po_id,
                    "supplier_id": supplier_id,
                    "supplier_name": supplier_name,
                    "po_group": po_group,
                    "action": "updated",
                    "item_count": len(line_items),
                })
            else:
                # POST new vendor order
                payload: Dict[str, Any] = {
                    "Vendor": supplier_id,
                    "Stage": default_stage,
                    "DateIssued": date.today().isoformat(),
                    "Type": "Catalogue",
                    "LineItems": line_items,
                }
                if cc_id:
                    payload["AssignedTo"] = cc_id
                if job_id and policy.get("include_po_notes"):
                    payload["PrivateNotes"] = f"Job {job_id} | {po_group}"

                result = await mcp_executor.call_tool(
                    "create_vendor_order",
                    {"vendor_order_data": payload},
                )
                po_id = result.get("vendor_order", {}).get("ID") if isinstance(result, dict) else None
                created.append({
                    "po_id": po_id,
                    "supplier_id": supplier_id,
                    "supplier_name": supplier_name,
                    "po_group": po_group,
                    "action": "created",
                    "item_count": len(line_items),
                })

        except Exception as e:
            failed.append({
                "po_group": po_group,
                "supplier_name": supplier_name,
                "error": str(e),
            })

    total_pos = len(created) + len(updated)
    return {
        "success": len(failed) == 0 or total_pos > 0,
        "phase": "create",
        "purchase_orders": created + updated,
        "created_count": len(created),
        "updated_count": len(updated),
        "failed_count": len(failed),
        "failed": failed,
        "message": (
            f"Created {len(created)} new PO(s), updated {len(updated)} existing PO(s)."
            + (f" {len(failed)} group(s) failed." if failed else "")
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Chat path — CREATE (with line items)
# ═══════════════════════════════════════════════════════════════════════════════

async def _handle_create(
    llm_chat: Callable,
    user_text: str,
    parsed: Dict[str, Any],
    policy: Dict[str, Any],
    mcp_executor: MCPToolExecutor,
    conversation_history: Optional[List[Dict[str, str]]],
) -> Dict[str, Any]:
    logger.info("➕ PO Agent: handle CREATE (chat path)")
    resolver = EntityResolver(mcp_executor, llm_chat=llm_chat)
    session_id = str(uuid.uuid4())

    # Resolve supplier
    supplier_id: Optional[int] = parsed.get("supplier_id")
    supplier_name: Optional[str] = parsed.get("supplier_name")
    if not supplier_id and not supplier_name:
        return {
            "success": False,
            "needs_clarification": True,
            "session_id": session_id,
            "clarifications": [{"field": "supplier_name",
                                 "message": "Which supplier is this purchase order for?"}],
            "message": "Please provide the supplier name or ID.",
        }
    if not supplier_id:
        vendors = await _fetch_all_vendors(mcp_executor)
        try:
            supplier_id, supplier_name = await _resolve_supplier_by_name(
                supplier_name, vendors, llm_chat
            )
        except AmbiguousResolutionError as e:
            return {
                "success": False,
                "needs_clarification": True,
                "session_id": session_id,
                "clarifications": [{"field": "supplier_name", "message": str(e),
                                     "options": getattr(e, "options", [])}],
                "message": str(e),
            }
        except ResolutionError as e:
            return {"success": False, "error": "SUPPLIER_NOT_FOUND", "message": str(e)}

    # Resolve job
    job_id: Optional[int] = parsed.get("job_id")
    if not job_id and parsed.get("job_name"):
        try:
            job_id = await resolver.resolve_job(name=parsed["job_name"])
        except (ResolutionError, AmbiguousResolutionError) as e:
            return {
                "success": False,
                "needs_clarification": True,
                "session_id": session_id,
                "clarifications": [{"field": "job_name", "message": str(e),
                                     "options": getattr(e, "options", [])}],
                "message": str(e),
            }

    if policy.get("require_job_reference") and not job_id:
        return {
            "success": False,
            "needs_clarification": True,
            "session_id": session_id,
            "clarifications": [{"field": "job_id",
                                 "message": "Which job should this PO be charged to?"}],
            "message": "A job reference is required for this purchase order.",
        }

    # Resolve section + cost centre
    section_id: Optional[int] = parsed.get("section_id")
    cost_centre_id: Optional[int] = parsed.get("cost_centre_id")
    if job_id:
        if not section_id and parsed.get("section_name"):
            try:
                section_id = await resolver.resolve_section(
                    job_id=job_id, name=parsed["section_name"]
                )
            except (ResolutionError, AmbiguousResolutionError) as e:
                return {
                    "success": False,
                    "needs_clarification": True,
                    "session_id": session_id,
                    "clarifications": [{"field": "section_name", "message": str(e),
                                         "options": getattr(e, "options", [])}],
                    "message": str(e),
                }
        if not cost_centre_id and section_id and parsed.get("cost_centre_name"):
            try:
                cost_centre_id = await resolver.resolve_cost_centre(
                    job_id=job_id, section_id=section_id,
                    name=parsed["cost_centre_name"]
                )
            except (ResolutionError, AmbiguousResolutionError) as e:
                return {
                    "success": False,
                    "needs_clarification": True,
                    "session_id": session_id,
                    "clarifications": [{"field": "cost_centre_name", "message": str(e),
                                         "options": getattr(e, "options", [])}],
                    "message": str(e),
                }

    # Build payload
    default_tax = policy.get("default_tax_code_id") or 1
    line_items = [
        {
            "Description": item.get("description", ""),
            "Type": item.get("type", "Catalogue"),
            "Quantity": float(item.get("quantity", 1)),
            "UnitCost": float(item.get("unit_cost", 0)),
            "Total": round(float(item.get("quantity", 1)) * float(item.get("unit_cost", 0)), 2),
            "TaxCode": {"ID": item.get("tax_code_id") or default_tax},
        }
        for item in parsed.get("line_items", [])
    ]

    payload: Dict[str, Any] = {
        "Vendor": supplier_id,
        "Stage": parsed.get("status") or policy.get("default_order_status", "Pending"),
        "DateIssued": parsed.get("order_date") or date.today().isoformat(),
        "Type": "Catalogue",
        "LineItems": line_items,
    }
    if cost_centre_id:
        payload["AssignedTo"] = cost_centre_id
    if parsed.get("notes") and policy.get("include_po_notes"):
        payload["PrivateNotes"] = parsed["notes"]

    try:
        result = await mcp_executor.call_tool(
            "create_vendor_order", {"vendor_order_data": payload}
        )
        po_id = result.get("vendor_order", {}).get("ID") if isinstance(result, dict) else None
        return {
            "success": True,
            "purchase_orders": [{
                "po_id": po_id,
                "supplier_id": supplier_id,
                "supplier_name": supplier_name,
                "job_id": job_id,
                "cost_centre_id": cost_centre_id,
                "status": payload["Stage"],
                "item_count": len(line_items),
            }],
            "created_count": 1,
            "failed_count": 0,
            "message": f"Purchase order created successfully (PO ID: {po_id}).",
        }
    except Exception as e:
        return {"success": False, "error": "CREATE_FAILED", "message": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# Chat path — BLANK PO
# ═══════════════════════════════════════════════════════════════════════════════

async def _handle_blank_po(
    llm_chat: Callable,
    parsed: Dict[str, Any],
    policy: Dict[str, Any],
    mcp_executor: MCPToolExecutor,
) -> Dict[str, Any]:
    logger.info("📄 PO Agent: handle BLANK PO")

    if not policy.get("blank_po_allowed", True):
        return {
            "success": False,
            "error": "BLANK_PO_NOT_ALLOWED",
            "message": "Blank purchase orders are not permitted under the current SOP.",
        }

    resolver = EntityResolver(mcp_executor, llm_chat=llm_chat)
    session_id = str(uuid.uuid4())

    supplier_id: Optional[int] = parsed.get("supplier_id")
    supplier_name: Optional[str] = parsed.get("supplier_name")
    if not supplier_id and not supplier_name:
        return {
            "success": False,
            "needs_clarification": True,
            "session_id": session_id,
            "clarifications": [{"field": "supplier_name",
                                 "message": "Which supplier is this blank PO for?"}],
            "message": "Please provide the supplier name or ID.",
        }
    if not supplier_id:
        vendors = await _fetch_all_vendors(mcp_executor)
        try:
            supplier_id, supplier_name = await _resolve_supplier_by_name(
                supplier_name, vendors, llm_chat
            )
        except AmbiguousResolutionError as e:
            return {
                "success": False,
                "needs_clarification": True,
                "session_id": session_id,
                "clarifications": [{"field": "supplier_name", "message": str(e),
                                     "options": getattr(e, "options", [])}],
                "message": str(e),
            }
        except ResolutionError as e:
            return {"success": False, "error": "SUPPLIER_NOT_FOUND", "message": str(e)}

    # Resolve job (optional for blank PO depending on policy)
    job_id: Optional[int] = parsed.get("job_id")
    cost_centre_id: Optional[int] = parsed.get("cost_centre_id")
    if not job_id and parsed.get("job_name"):
        try:
            job_id = await resolver.resolve_job(name=parsed["job_name"])
        except (ResolutionError, AmbiguousResolutionError) as e:
            return {
                "success": False,
                "needs_clarification": True,
                "session_id": session_id,
                "clarifications": [{"field": "job_name", "message": str(e),
                                     "options": getattr(e, "options", [])}],
                "message": str(e),
            }

    payload: Dict[str, Any] = {
        "Vendor": supplier_id,
        "Stage": policy.get("blank_po_stage", "Pending"),
        "DateIssued": parsed.get("order_date") or date.today().isoformat(),
        "Type": "Catalogue",
    }
    if cost_centre_id:
        payload["AssignedTo"] = cost_centre_id
    if job_id and policy.get("include_po_notes"):
        payload["PrivateNotes"] = f"Blank PO — Job {job_id}"

    try:
        result = await mcp_executor.call_tool(
            "create_vendor_order", {"vendor_order_data": payload}
        )
        po_id = result.get("vendor_order", {}).get("ID") if isinstance(result, dict) else None
        return {
            "success": True,
            "purchase_orders": [{
                "po_id": po_id,
                "supplier_id": supplier_id,
                "supplier_name": supplier_name,
                "job_id": job_id,
                "blank": True,
                "status": payload["Stage"],
            }],
            "created_count": 1,
            "message": f"Blank purchase order created (PO ID: {po_id}).",
        }
    except Exception as e:
        return {"success": False, "error": "CREATE_FAILED", "message": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# Chat path — UPDATE
# ═══════════════════════════════════════════════════════════════════════════════

async def _handle_update(
    parsed: Dict[str, Any],
    policy: Dict[str, Any],
    mcp_executor: MCPToolExecutor,
) -> Dict[str, Any]:
    logger.info("✏️  PO Agent: handle UPDATE")

    po_id = parsed.get("purchase_order_id")
    if not po_id:
        return {
            "success": False,
            "error": "MISSING_PO_ID",
            "message": "Please provide the Purchase Order ID you want to update.",
        }

    try:
        current_result = await mcp_executor.call_tool(
            "get_vendor_order_details", {"vendor_order_id": po_id}
        )
        current = current_result.get("vendor_order", current_result) \
            if isinstance(current_result, dict) else {}
    except Exception as e:
        return {"success": False, "error": "FETCH_FAILED",
                "message": f"Could not fetch PO {po_id}: {e}"}

    stage = (current.get("Stage") or "").lower()
    if stage in ("archived", "voided"):
        return {
            "success": False,
            "error": "CANNOT_UPDATE_RECEIVED_PO",
            "message": f"Purchase order {po_id} is '{stage}' and cannot be updated.",
        }

    patch: Dict[str, Any] = {}
    if parsed.get("order_date"):
        patch["DateIssued"] = parsed["order_date"]
    if parsed.get("status"):
        patch["Stage"] = parsed["status"]
    if parsed.get("notes") and policy.get("include_po_notes"):
        patch["PrivateNotes"] = parsed["notes"]
    if parsed.get("line_items"):
        default_tax = policy.get("default_tax_code_id") or 1
        patch["LineItems"] = [
            {
                "Description": item.get("description", ""),
                "Quantity": float(item.get("quantity", 1)),
                "UnitCost": float(item.get("unit_cost", 0)),
                "TaxCode": {"ID": item.get("tax_code_id") or default_tax},
            }
            for item in parsed["line_items"]
        ]

    if not patch:
        return {"success": False, "error": "NO_CHANGES",
                "message": "No fields to update were provided."}

    try:
        await mcp_executor.call_tool(
            "update_vendor_order",
            {"vendor_order_id": po_id, "vendor_order_data": patch},
        )
        return {
            "success": True,
            "purchase_orders": [{"po_id": po_id, "updated_fields": list(patch.keys())}],
            "message": f"Purchase order {po_id} updated successfully.",
        }
    except Exception as e:
        return {"success": False, "error": "UPDATE_FAILED", "message": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# Chat path — DELETE (with confirmation gate)
# ═══════════════════════════════════════════════════════════════════════════════

async def _handle_delete(
    parsed: Dict[str, Any],
    hints: Dict[str, Any],
    mcp_executor: MCPToolExecutor,
) -> Dict[str, Any]:
    logger.info("🗑️  PO Agent: handle DELETE")

    po_id = parsed.get("purchase_order_id") or hints.get("purchase_order_id")
    if not po_id:
        return {
            "success": False,
            "error": "MISSING_PO_ID",
            "message": "Please provide the Purchase Order ID you want to delete.",
        }

    confirmed = hints.get("delete_confirmed") or \
        hints.get("pre_resolved", {}).get("delete_confirmed")

    if not confirmed:
        try:
            po_result = await mcp_executor.call_tool(
                "get_vendor_order_details", {"vendor_order_id": po_id}
            )
            po = po_result.get("vendor_order", po_result) \
                if isinstance(po_result, dict) else {}
        except Exception:
            po = {}

        po = po.get("vendor_order", po) if isinstance(po, dict) else {}
        supplier = po.get("Vendor", {}).get("Name", "Unknown supplier") \
            if isinstance(po, dict) else "Unknown supplier"
        order_date = po.get("DateIssued", "Unknown date") \
            if isinstance(po, dict) else "Unknown date"
        total = po.get("Totals", {}).get("ExTax", "?") \
            if isinstance(po, dict) else "?"

        return {
            "success": False,
            "needs_clarification": True,
            "session_id": str(uuid.uuid4()),
            "clarifications": [{
                "type": "delete_confirmation",
                "field": "delete_confirmed",
                "message": (
                    f"Are you sure you want to delete PO {po_id}?\n"
                    f"Supplier: {supplier} | Date: {order_date} | Total: ${total}"
                ),
                "options": [
                    {"id": "yes", "label": "Yes, delete this purchase order"},
                    {"id": "no", "label": "No, cancel"},
                ],
            }],
            "message": f"Please confirm deletion of purchase order {po_id}.",
        }

    if str(confirmed).lower() in ("no", "cancel", "false"):
        return {"success": False, "error": "DELETE_CANCELLED",
                "message": "Purchase order deletion cancelled."}

    try:
        await mcp_executor.call_tool(
            "delete_vendor_order", {"vendor_order_id": po_id}
        )
        return {
            "success": True,
            "purchase_orders": [{"po_id": po_id, "deleted": True}],
            "message": f"Purchase order {po_id} deleted successfully.",
        }
    except Exception as e:
        return {"success": False, "error": "DELETE_FAILED", "message": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# Main entry point
# ═══════════════════════════════════════════════════════════════════════════════

async def run_purchase_order_agent(
    llm_chat: Callable,
    user_text: str,
    extracted: Optional[Dict[str, Any]] = None,
    any_uploaded_text: Optional[str] = None,
    hints: Optional[Dict[str, Any]] = None,
    mcp_executor: Optional[MCPToolExecutor] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """
    Main Purchase Order Agent entry point. Called by purchase_order_proxy.py.

    Routing:
      1. Phase B re-upload  — extracted has POGroup + Include headers
      2. Phase A trigger    — no file, or file without POGroup header
                              (schedule_date / schedule_staff / cost_centre_direct)
      3. Chat: update       — action=update
      4. Chat: delete       — action=delete (with confirmation gate)
      5. Chat: blank_po     — action=blank_po or trigger=blank_po
      6. Chat: create       — action=create with explicit line items
    """
    _state = create_agent_state("po", user_text or "")
    _state.enter_phase("init")

    logger.info("=" * 60)
    logger.info("🛒 PURCHASE ORDER AGENT STARTED")
    logger.info(f"User: {user_text[:80]}")
    logger.info(f"Has extracted: {bool(extracted)}")
    logger.info(f"Hints: {list(hints.keys()) if hints else []}")
    logger.info("=" * 60)

    hints = hints or {}

    if mcp_executor is None:
        return {
            "success": False,
            "error": "NO_MCP_EXECUTOR",
            "message": "MCP executor is required for purchase order operations.",
        }

    # Read SOP + extract policy once per request
    sop_text = _read_sop(sop_override=(hints or {}).get("sop_override"))
    policy = _llm_extract_policy(llm_chat, sop_text, user_text)
    logger.info(f"Policy: {json.dumps(policy, default=str)[:300]}")

    _state.complete_phase("init")

    # ── Route 1: Phase B re-upload ────────────────────────────────────────────
    if extracted and _is_po_reupload(extracted):
        _state.enter_phase("phase_b")
        result = await _phase_b_create(extracted, policy, mcp_executor)
        _state.complete_phase("phase_b")
        logger.info(_state.summary())
        return result

    # ── Parse chat request ────────────────────────────────────────────────────
    parsed = _llm_parse_chat_request(llm_chat, user_text, conversation_history)
    if "error" in parsed:
        return {
            "success": False,
            "error": parsed["error"],
            "message": parsed.get("message", "Failed to parse your request."),
        }

    # Action / trigger override from hints (intent_analyzer / agent_handoff)
    action = (hints.get("action") or parsed.get("action") or "").lower()
    trigger = (hints.get("trigger") or parsed.get("trigger") or "").lower()

    # Merge pre_resolved IDs from hints into parsed
    pre = hints.get("pre_resolved", {})
    if pre:
        for key in ("supplier_id", "job_id", "section_id", "cost_centre_id",
                    "purchase_order_id"):
            if pre.get(key) and not parsed.get(key):
                parsed[key] = pre[key]

    # ── Route 2: UPDATE ───────────────────────────────────────────────────────
    if action == "update":
        _state.enter_phase("update")
        result = await _handle_update(parsed, policy, mcp_executor)
        _state.complete_phase("update")
        logger.info(_state.summary())
        return result

    # ── Route 3: DELETE ───────────────────────────────────────────────────────
    if action == "delete":
        _state.enter_phase("delete")
        result = await _handle_delete(parsed, hints, mcp_executor)
        _state.complete_phase("delete")
        logger.info(_state.summary())
        return result

    # ── Route 4: BLANK PO ─────────────────────────────────────────────────────
    if action == "blank_po" or trigger == "blank_po" or parsed.get("blank_po"):
        _state.enter_phase("blank_po")
        result = await _handle_blank_po(llm_chat, parsed, policy, mcp_executor)
        _state.complete_phase("blank_po")
        logger.info(_state.summary())
        return result

    # ── Route 5: Phase A (schedule or direct cost-centre trigger) ─────────────
    if trigger in ("schedule_date", "schedule_staff", "cost_centre_direct") or extracted:
        _state.enter_phase("phase_a")
        result = await _phase_a_prepare(
            llm_chat, parsed, policy, mcp_executor, user_text
        )
        _state.complete_phase("phase_a")
        logger.info(_state.summary())
        return result

    # ── Route 6: CREATE with explicit line items (chat path) ──────────────────
    if action == "create" or parsed.get("line_items"):
        _state.enter_phase("create")
        result = await _handle_create(
            llm_chat, user_text, parsed, policy, mcp_executor, conversation_history
        )
        _state.complete_phase("create")
        logger.info(_state.summary())
        return result

    # ── Fallback: treat as Phase A (cost_centre_direct) ───────────────────────
    _state.enter_phase("phase_a_fallback")
    result = await _phase_a_prepare(
        llm_chat, parsed, policy, mcp_executor, user_text
    )
    _state.complete_phase("phase_a_fallback")
    logger.info(_state.summary())
    return result