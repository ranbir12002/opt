# svc-agent-purchase-order/src/config.py
"""
Configuration constants for the Purchase Order Agent.

All business-rule defaults come from the SOP document, not this file.
This file contains only infrastructure constants (paths, limits, column names).
"""
from __future__ import annotations

import os

# ── SOP document (organisation-specific business rules) ──────────────────────
# Override via env var when deploying for a different customer.
SOP_MD_PATH = os.getenv(
    "PO_SOP_MD_PATH",
    os.path.join(os.path.dirname(__file__), "sop", "purchase_order_sop.md"),
)

# ── MCP server base URL ───────────────────────────────────────────────────────
MCP_BASE = os.getenv("MCP_BASE_URL", "http://127.0.0.1:8000")

# ── Default Simpro company ID ─────────────────────────────────────────────────
DEFAULT_COMPANY_ID = int(os.getenv("SIMPRO_COMPANY_ID", "2"))

# ── LLM guardrails ────────────────────────────────────────────────────────────
MAX_CLARIFICATIONS = 5
FUZZY_MATCH_THRESHOLD = 70

# ── Excel sheet names ─────────────────────────────────────────────────────────
# Sheet 1: items the user reviews and edits (Include, Supplier, POGroup)
PO_ITEMS_SHEET_NAME = "PO Items"
# Sheet 2: supplier lookup table for autocomplete — agent writes, user reads only
PO_SUPPLIERS_SHEET_NAME = "Suppliers"

# ── Supplier column format ────────────────────────────────────────────────────
# Values in the Supplier column are written as "Name - ID" (e.g. "Bunnings - 42").
# Phase B splits on this separator to extract the ID without fuzzy matching.
# If no separator is found, Phase B falls back to fuzzy match on the name alone.
SUPPLIER_COL_SEPARATOR = " - "

# ── Sheet 1: columns written by agent in Phase A (user edits Include + Supplier)
# These are the visible columns the user sees and interacts with.
PO_EXCEL_COLUMNS = [
    "ScheduleID",       # source schedule ID (blank for direct cost-centre requests)
    "JobID",
    "SectionID",
    "CostCentreID",
    "CostCentreName",
    "PartNumber",       # catalogue part / SKU number
    "Description",      # line item description
    "Type",             # "Material" | "Labour" | "OneOff"
    "Quantity",
    "UnitCost",
    "Total",            # Quantity × UnitCost (computed by agent, read-only)
    "TaxCodeID",
    "Supplier",         # pre-filled as "Name - ID" where known; user edits via autocomplete
    "POGroup",          # grouping key (e.g. "cc_116534"); user can override to merge/split POs
    "Include",          # "Yes" / "No" — primary user edit column
]

# ── Sheet 2: supplier lookup columns (agent-written, not read back at Phase B)
PO_SUPPLIER_SHEET_COLUMNS = [
    "Supplier",         # "Name - ID" combined string for autocomplete
]