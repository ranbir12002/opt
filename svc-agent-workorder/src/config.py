# svc-agent-workorder/src/config.py
"""
Configuration constants for the Work Order (Contractor Job) Agent.
"""
from __future__ import annotations

import os

# SOP document path (organisation-specific business rules)
SOP_DOCX_PATH = os.getenv(
    "WO_SOP_DOCX_PATH",
    os.path.join(os.path.dirname(__file__), "sop", "wo_creation_sop.md"),
)

# MCP server base URL
MCP_BASE = os.getenv("MCP_BASE_URL", "http://127.0.0.1:8000")

# Maximum LLM clarification rounds before giving up
MAX_CLARIFICATIONS = 5

# Excel template columns for the work order review sheet
WO_EXCEL_COLUMNS = [
    "ItemID",
    "ItemName",
    "Type",         # "Material", "Labour", or "OneOff"
    "Quantity",
    "UnitCost",
    "Total",
    "Include",      # "Yes" / "No" — user edits this
]

# Metadata columns embedded in each Excel row (hidden or separate sheet)
WO_EXCEL_META_COLUMNS = [
    "JobID",
    "SectionID",
    "CostCentreID",
    "CostCentreName",
    "ContractorID",
    "ContractorName",
]
