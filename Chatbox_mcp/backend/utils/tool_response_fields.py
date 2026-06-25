"""
backend/utils/tool_response_fields.py

Per-tool response field registry for Simpro MCP tools.
Port of mcp-client/utils/tool-response-fields.js.

Used by the query planner to inject response field info so the main LLM
knows what each tool returns — enabling direct filtering instead of chaining.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

TOOL_RESPONSE_FIELDS: Dict[str, Dict] = {
    # ── Jobs ────────────────────────────────────────────────────────
    "search_jobs": {
        "fields": [
            "ID", "Name", "Type", "Stage", "DateIssued", "Status",
            "Site.ID", "Site.Name",
            "Customer.ID", "Customer.CompanyName",
            "Total.ExTax", "Total.IncTax", "Total.Tax",
            "ProjectManager.ID", "ProjectManager.Name",
        ],
        "named_params": ["stage", "type"],
    },

    # ── Quotes ──────────────────────────────────────────────────────
    "search_quotes": {
        "fields": [
            "ID", "Name", "Status", "IsClosed", "DateIssued",
            "Site.ID", "Site.Name",
            "Customer.ID", "Customer.CompanyName",
            "Total.ExTax", "Total.IncTax", "Total.Tax",
        ],
        "named_params": ["is_closed"],
    },

    # ── Invoices ────────────────────────────────────────────────────
    "search_invoices": {
        "fields": [
            "ID", "Type", "Status", "IsPaid", "DateIssued", "DueDate",
            "Customer.ID", "Customer.CompanyName",
            "Jobs.ID",
            "Total.ExTax", "Total.IncTax", "Total.Tax", "Total.BalanceDue",
            "RecurringInvoice.ID",
        ],
        "named_params": ["is_paid", "type"],
        "notes": "No Site field — to filter by site: search_jobs by Site.Name → use Job IDs in filters: {\"Jobs.ID\": \"in(id1,id2)\"}",
    },

    # ── Schedules ───────────────────────────────────────────────────
    "get_schedules": {
        "fields": [
            "ID", "Date", "Type", "Reference", "StartTime",
            "Staff.ID", "Staff.Name", "Staff.Type",
            "Job.ID",
            "Blocks",
        ],
        "named_params": ["date", "date_from", "date_to", "type"],
        "notes": "Results already include staff names — do NOT pre-lookup contacts. Reference = \"JobID-CostCentreID\".",
    },

    # ── Customers ───────────────────────────────────────────────────
    "search_customers": {
        "fields": [
            "ID", "CompanyName", "GivenName", "FamilyName", "DisplayName",
            "Type", "Status", "Phone", "Email",
        ],
        "named_params": [],
    },

    # ── Contacts ────────────────────────────────────────────────────
    "search_contacts": {
        "fields": [
            "ID", "GivenName", "FamilyName", "CompanyName",
            "Email", "Phone", "Type",
        ],
        "named_params": ["search"],
    },

    # ── Contractors ─────────────────────────────────────────────────
    "list_contractors": {
        "fields": [
            "ID", "Name", "ContactName",
        ],
        "named_params": ["columns", "orderby", "search_mode", "limit"],
    },

    # ── Employees ───────────────────────────────────────────────────
    "list_employees": {
        "fields": [
            "ID", "Name",
        ],
        "named_params": ["columns", "orderby", "search_mode", "limit"],
    },

    # ── Leads ───────────────────────────────────────────────────────
    "get_leads": {
        "fields": [
            "ID", "Name", "IsOpen", "Status", "DateCreated",
            "Customer.ID", "Customer.CompanyName",
        ],
        "named_params": ["is_open"],
    },

    # ── Sites ───────────────────────────────────────────────────────
    "search_sites": {
        "fields": [
            "ID", "Name", "Address", "City", "State",
        ],
        "named_params": ["search"],
    },

    # ── Work Orders ─────────────────────────────────────────────────
    "get_all_job_work_orders": {
        "fields": [
            "ID", "Status", "Type", "DateIssued",
            "Job.ID",
            "Customer.ID", "Customer.CompanyName",
            "Site.ID", "Site.Name",
            "Contractor.ID", "Contractor.CompanyName",
            "Total.ExTax", "Total.IncTax",
        ],
        "named_params": [],
        "notes": "ALWAYS use filters — never call without filters (fetches thousands of records).",
    },

    # ── Prebuilds ───────────────────────────────────────────────────
    "search_prebuilds": {
        "fields": [
            "ID", "PartNo", "Name", "Description",
            "Trade.ID", "Trade.Name",
        ],
        "named_params": ["part_no", "search"],
    },

    # ── Vendor Orders ───────────────────────────────────────────────
    "get_vendor_orders": {
        "fields": [
            "ID", "Status", "OrderNo", "Reference", "DateIssued",
            "Vendor.ID", "Vendor.CompanyName",
            "Total.ExTax", "Total.IncTax",
        ],
        "named_params": [],
    },

    # ── Vendor Receipts ─────────────────────────────────────────────
    "get_vendor_receipts": {
        "fields": [
            "ID", "Status", "OrderNo", "Reference", "DateReceived",
            "Vendor.ID", "Vendor.CompanyName",
        ],
        "named_params": ["display"],
    },

    # ── Customer Payments ───────────────────────────────────────────
    "get_customer_payments": {
        "fields": [
            "ID", "Date", "Amount",
            "Customer.ID", "Customer.CompanyName",
        ],
        "named_params": [],
    },
}


def get_response_fields_for_tools(tool_names: List[str]) -> str:
    """
    Return a compact block of response field info for the given tools.
    Injected into the LLM system prompt so it can pick the most direct route.
    """
    if not tool_names:
        return ""

    lines = []
    for name in tool_names:
        entry = TOOL_RESPONSE_FIELDS.get(name)
        if not entry:
            continue
        field_str = ", ".join(entry["fields"])
        line = f"- {name}: [{field_str}]"
        named = entry.get("named_params", [])
        if named:
            line += f" | named params: {', '.join(named)}"
        notes = entry.get("notes")
        if notes:
            line += f" | NOTE: {notes}"
        lines.append(line)

    if not lines:
        return ""

    return (
        "\n\nRESPONSE FIELDS FOR SELECTED TOOLS (use these to pick the most direct route — "
        "if the target tool has the field you need, filter on it DIRECTLY instead of chaining through another tool):\n"
        + "\n".join(lines)
    )


def extract_tool_names_from_plan(plan_hint: Optional[str]) -> List[str]:
    """
    Parse "use: tool_name" patterns from a planner hint string.
    Returns unique tool names found in the plan.
    """
    if not plan_hint:
        return []

    tool_names = set()
    for match in re.finditer(r"use:\s*([a-z_]+(?:\s*,\s*[a-z_]+)*)", plan_hint, re.IGNORECASE):
        for name in match.group(1).split(","):
            name = name.strip()
            if "_" in name or name in TOOL_RESPONSE_FIELDS:
                tool_names.add(name)

    return list(tool_names)
