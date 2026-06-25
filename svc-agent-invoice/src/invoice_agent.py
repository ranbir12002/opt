from __future__ import annotations
"""
Invoice Agent (rewired for SOP-aware multi-mode invoicing).

Responsibilities:
- Resolve an LLM chat function (injection-first, then local fallback).
- Read SOP DOCX to plain text.
- Parse attached Excel/CSV (provided as CSV text) into structured rows.
- Ask LLM for *policy* + *defaults* (invoice_mode, PerItem, etc.).
- Deterministically fan-out parsed rows into Simpro invoice JSON bodies:
    * invoice per job
    * invoice per cost centre
    * invoice per item
- POST each body to MCP Simpro endpoint.
- Return a structured result for the chat layer / CLI presenter.
"""

import asyncio
import json
import csv
import io
import os
import re
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests
from docx import Document

logger = logging.getLogger(__name__)

try:
    # Prefer central config if available
    from .config import SOP_DOCX_PATH, MCP_BASE as MCP_BASE_URL  # type: ignore
except Exception:  # pragma: no cover - fallback for standalone/importlib usage
    _SRC_DIR = os.path.dirname(os.path.abspath(__file__))
    SOP_DOCX_PATH = os.getenv("INVOICE_SOP_DOCX_PATH") or os.path.join(_SRC_DIR, "sop", "invoice_creation_sop.md")
    MCP_BASE_URL = os.getenv("MCP_BASE_URL", "http://127.0.0.1:8000")


# MCPToolExecutor imported from utils.mcp_executor (centralized)
from utils.mcp_executor import MCPToolExecutor
from utils.entity_resolver import (
    EntityResolver, AmbiguousResolutionError, ResolutionError, MissingFieldError,
    BatchedClarificationError,
)
from utils.agent_state import AgentExecutionState, create_agent_state


# ---------------------------------------------------------------------------
# LLM resolution (same contract as before)
# ---------------------------------------------------------------------------

def _default_llm_chat(*_args, **_kwargs):
    raise ImportError(
        "No llm_chat provided and none found locally. "
        "Inject llm_chat via Chatbox proxy (backend.utils.llm.chat) "
        "or add a local src/llm.py with `chat(messages, ...)`."
    )


def _resolve_llm_chat():
    """
    Resolution order:
    1) Chatbox_mcp.backend.utils.llm.chat
    2) Local src.llm.chat
    3) Fallback stub that raises.
    """
    try:
        from Chatbox_mcp.backend.utils.llm import chat as _chat  # type: ignore
        return _chat
    except Exception:
        pass

    try:
        from .llm import chat as _chat  # type: ignore
        return _chat
    except Exception:
        pass

    return _default_llm_chat


# ---------------------------------------------------------------------------
# Compact Simpro schema for the system prompt
# ---------------------------------------------------------------------------

SIMPRO_INVOICE_SCHEMA_SNIPPET = """
You MUST ultimately build JSON bodies compatible with:

POST /api/v1.0/companies/{companyID}/invoices/

Invoice (request body) core shape:

{
  "Type": "TaxInvoice" | "Deposit" | "ProgressInvoice" | "RequestForClaim",
  "Jobs": [ <int job_id>, ... ],
  "DateIssued": "YYYY-MM-DD",
  "PaymentTermID": <int | null>,
  "Stage": "Approved" | "Pending",
  "PerItem": true | false,
  "OrderNo": <string | null>,
  "LatePaymentFee": <bool | null>,
  "CISDeductionRate": <number | null>,
  "AccountingCategory": <int | null>,
  "Status": <int | null>,
  "AutoAdjustStatus": <bool | null>,
  "Description": <string | null>,
  "Notes": <string | null>,
  "Retainage": [
    { "JobID": <int>, "ExTax": <number>, "IncTax": <number> }
  ],
  "CostCenters": [
    {
      "ID": <int>,                 // Cost Centre ID
      "Claim": {
        "ExTax": <number | null>,
        "IncTax": <number | null>,
        "Percent": <number | null>
      },
      "Items": [                   // ONLY when PerItem = true
        { "ID": <int>, "Quantity": <number> }
      ]
    }
  ]
}

Do NOT invent additional top-level keys.
Omit optional fields rather than guessing fake data.
"""

SYSTEM = (
    "You are an expert Simpro Invoice Planning Agent.\n"
    "You MUST treat the SOP as the primary source of truth.\n"
    "The grouping behaviour (invoice_mode) MUST follow the SOP unless the USER MESSAGE\n"
    "explicitly overrides it in plain language.\n"
    "The ATTACHMENT SUMMARY is ONLY for understanding the schema and checking feasibility.\n"
    "It must NEVER be used to choose a different invoice_mode than the SOP default.\n"
    "Examples:\n"
    "- If the SOP says 'default grouping is by JobID', you MUST choose invoice_mode='per_job'\n"
    "  for all invoices, unless the user clearly says 'group per cost centre' or 'per item'.\n"
    "- You are NOT allowed to switch to 'per_cost_centre' or 'per_item' just because the\n"
    "  attachment contains multiple cost centres or items.\n\n"
    "Your job is NOT to read every Excel cell, but to interpret the SOP and user instructions\n"
    "and output a SMALL JSON policy that describes HOW invoices should be grouped and which\n"
    "defaults should be applied.\n\n"
    "You MUST NOT fabricate JobID, CostCentreID, ItemID, or claim amounts. These will be\n"
    "filled by deterministic backend code from the uploaded sheet.\n"
    "You may, however, use the ATTACHMENT SUMMARY to infer which fields are available\n"
    "(e.g., whether CostCentreID/ItemID/Quantity/Claim fields exist) and to phrase\n"
    "clarification questions.\n\n"
    "Instead, you answer:\n"
    "  - Should invoices be created per job, per cost centre, or per item?\n"
    "    (Remember: this MUST follow the SOP unless the user explicitly overrides it.)\n"
    "  - Should PerItem be true or false in Simpro?\n"
    "  - What default invoice Type / Stage / DateIssued / PaymentTermID / Notes etc. to use?\n"
    "  - How should descriptions be combined? (per job or per cost centre or per item)\n\n"
    "DEFAULT VALUE RULES:\n"
    "  - ALWAYS extract defaults from the SOP DOCX text provided in the user message.\n"
    "  - DateIssued: If the user specifies a date, use that. Otherwise default to today's date\n"
    "    (provided in the user message). NEVER ask the user for a date.\n"
    "  - CompanyID: Set to null. The backend injects the correct CompanyID automatically.\n"
    "    NEVER guess or fabricate a CompanyID. NEVER use a JobID as CompanyID.\n"
    "  - Stage: Use the value from the SOP DOCX. NEVER ask the user.\n"
    "  - Type/InvoiceType: Use the value from the SOP DOCX. NEVER ask the user.\n"
    "  - PerItem: Use the value from the SOP DOCX. NEVER ask the user.\n"
    "  - PaymentTermID: Default to null. NEVER ask the user.\n"
    "  - OrderNo, Notes, LatePaymentFee, etc.: Default to null. NEVER ask the user.\n"
    "  - If the user explicitly overrides any SOP default (e.g., 'tax invoice'), use their value.\n\n"
    "CRITICAL: The 'missing' array should ONLY contain questions about genuinely ambiguous\n"
    "BUSINESS LOGIC that cannot be resolved from the SOP or the user's message.\n"
    "NEVER ask about DateIssued, CompanyID, Stage, Type, PerItem, or PaymentTermID.\n"
    "Users are NOT administrators and do not know system-level settings.\n\n"
    "Return STRICT JSON with:\n"
    "{\n"
    '  "invoice_mode": "per_job" | "per_cost_centre" | "per_item",\n'
    '  "per_item": true | false,\n'
    '  "description_mode": "combine" | "per_first",\n'
    '  "description_joiner": ";" | "\\n",\n'
    '  "defaults": {\n'
    '    "CompanyID": null,               // ALWAYS null — backend injects automatically\n'
    '    "Type": "<string>",             // from SOP DOCX\n'
    '    "Stage": "<string>",            // from SOP DOCX\n'
    '    "DateIssued": "YYYY-MM-DD",     // from user message, or today if not specified\n'
    '    "PaymentTermID": <int | null>,\n'
    '    "OrderNo": <string | null>,\n'
    '    "LatePaymentFee": <bool | null>,\n'
    '    "CISDeductionRate": <number | null>,\n'
    '    "AccountingCategory": <int | null>,\n'
    '    "Status": <int | null>,\n'
    '    "AutoAdjustStatus": <bool | null>,\n'
    '    "Notes": <string | null>\n'
    "  },\n"
    '  "missing": []  // ONLY for genuinely ambiguous business logic, NOT system defaults\n'
    "}\n\n"
    + SIMPRO_INVOICE_SCHEMA_SNIPPET
)


# ---------------------------------------------------------------------------
# Helpers: SOP text, CSV parsing, normalisation
# ---------------------------------------------------------------------------

_sop_cache: dict = {}  # {path: text}

def _read_docx_text(path: str, max_chars: int = 32_000, sop_override: Optional[str] = None) -> str:
    """Read SOP file to plain text. Prefers sop_override if provided."""
    if sop_override:
        _inv_logger.info("[SOP] Using DB override SOP for invoice (org-specific)")
        return sop_override  # already validated at upload time
    if not path or not os.path.exists(path):
        _inv_logger.warning(f"[SOP] Default invoice SOP not found at: {path} — proceeding without SOP")
        return ""
    if path in _sop_cache:
        _inv_logger.info(f"[SOP] Using default invoice SOP from file (cached): {path}")
        return _sop_cache[path]
    _inv_logger.info(f"[SOP] Using default invoice SOP from file: {path}")
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        doc = Document(path)
        text = "\n".join(p.text for p in doc.paragraphs if p.text)
        text = " ".join(text.split())
    else:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    text = text[:max_chars]
    _sop_cache[path] = text
    return text


def _normalize_key(name: str) -> str:
    """Lowercase, strip non-alphanumerics to make header matching robust."""
    return "".join(ch.lower() for ch in name if ch.isalnum())


def _get_field(row: Dict[str, Any], *candidates: str) -> Optional[str]:
    """
    Look up a cell from a CSV row by trying multiple header variants,
    in a case/spacing-insensitive way.
    """
    if not row:
        return None
    norm = {_normalize_key(k): v for k, v in row.items()}
    for cand in candidates:
        key = _normalize_key(cand)
        if key in norm and norm[key] not in (None, ""):
            return str(norm[key])
    return None


# Map common human-friendly type names to valid Simpro API values
_INVOICE_TYPE_MAP = {
    "progress claim":    "ProgressInvoice",
    "progressclaim":     "ProgressInvoice",
    "progress invoice":  "ProgressInvoice",
    "progressinvoice":   "ProgressInvoice",
    "tax invoice":       "TaxInvoice",
    "taxinvoice":        "TaxInvoice",
    "tax":               "TaxInvoice",
    "deposit":           "Deposit",
    "request for claim": "RequestForClaim",
    "requestforclaim":   "RequestForClaim",
}

def _normalise_invoice_type(raw: Optional[str]) -> Optional[str]:
    """Map user-friendly type names to valid Simpro API values."""
    if not raw:
        return raw
    key = raw.strip().lower()
    return _INVOICE_TYPE_MAP.get(key, raw)


# ── LLM column mapper (handles non-standard Excel headers) ───────────────────

_CANONICAL_FIELDS: Dict[str, str] = {
    "JobID":               "Simpro job number (integer)",
    "CostCentreID":        "Simpro cost centre ID (integer)",
    "CompanyID":           "Simpro company/customer ID (integer)",
    "ItemID":              "Simpro catalogue item ID (integer)",
    "ClaimPercent":        "Claim percentage 0-100",
    "ClaimExTax":          "Claim dollar amount excluding tax",
    "ClaimIncTax":         "Claim dollar amount including tax",
    "CCTotalEx":           "Total cost-centre value ex tax",
    "Quantity":            "Item quantity",
    "UnitPriceEx":         "Unit price excluding tax",
    "Type":                "Invoice type (progress/tax/deposit)",
    "DateIssued":          "Invoice date (YYYY-MM-DD)",
    "Stage":               "Invoice stage (Approved/Pending)",
    "PerItem":             "Per-item mode flag (true/false)",
    "OrderNo":             "Purchase order number",
    "Notes":               "Free-text notes",
    "Reference":           "Reference number",
    "ProgressClaimNumber": "Progress claim sequence number",
    "PaymentTermID":       "Simpro payment term ID",
    "ItemCode":            "Catalogue item code",
    "LineDescription":     "Line item description",
    "TaxCode":             "Tax code string",
    "DiscountPct":         "Discount percentage",
}

# All normalized aliases that _get_field() already handles — used to skip known headers
_ALL_KNOWN_HEADER_KEYS: frozenset = frozenset(
    _normalize_key(k) for k in [
        "JobID", "Job Id", "Job_Id",
        "CompanyID", "Company Id", "Company_Id",
        "CostCentreID", "CostCenterID", "Cost Centre Id", "CostCentre", "CostCenter",
        "ItemID", "Item Id", "Item_Id",
        "Quantity", "Qty", "QtyTotal",
        "ClaimPercent", "Claim.Percent", "ClaimPercentage",
        "Claimed Remaining Percent", "Claimed To Date Percent", "Percent",
        "ClaimExTax", "Claim.ExTax", "Claimed Remaining Amount Ex Tax", "ExTax", "Ex Tax",
        "ClaimIncTax", "Claim.IncTax", "Claimed Remaining Amount Inc Tax", "IncTax", "Inc Tax",
        "CCTotalEx", "CC Total Ex", "Total Ex Tax", "TotalExTax", "Total", "CostCentreTotal",
        "Type", "InvoiceType", "Invoice Type",
        "DateIssued", "Date Issued", "Date",
        "Stage", "Status",
        "PerItem", "Per Item",
        "ItemCode", "Item Code", "Code",
        "LineDescription", "Line Description", "Line Desc",
        "TaxCode", "Tax Code", "Tax",
        "UnitPriceEx", "Unit Price Ex", "UnitPrice",
        "DiscountPct", "Discount Pct", "Discount",
        "PaymentTermID", "Payment Term ID", "PaymentTerm",
        "OrderNo", "Order No", "Order Number", "PO Number",
        "ProgressClaimNumber", "Progress Claim Number", "Claim Number",
        "Reference", "Ref", "ReferenceNo",
        "Notes", "Note", "Comments", "Description",
    ]
)


def _llm_map_columns(
    headers: List[str],
    llm_fn,
) -> Dict[str, str]:
    """
    Legacy header-only column mapper (synchronous).
    Superseded by _comprehend_sheet — kept as a fallback.
    """
    unrecognised = [h for h in headers if _normalize_key(h) not in _ALL_KNOWN_HEADER_KEYS]
    if not unrecognised:
        return {}

    field_list = "\n".join(f"- {k}: {v}" for k, v in _CANONICAL_FIELDS.items())
    prompt = (
        "Map these spreadsheet column headers to canonical invoice fields.\n"
        'Reply with ONLY valid JSON: {"original_header": "CanonicalField"}\n'
        "If a header doesn't match any field, omit it entirely. Do not invent mappings.\n\n"
        f"Canonical fields:\n{field_list}\n\n"
        f"Headers to map: {json.dumps(unrecognised)}"
    )
    try:
        result = llm_fn([{"role": "user", "content": prompt}], max_tokens=300)
        text = result.content if hasattr(result, "content") else str(result)
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            mapping = json.loads(m.group(0))
            return {k: v for k, v in mapping.items() if v in _CANONICAL_FIELDS}
    except Exception as exc:
        logger.warning(f"[InvoiceAgent] LLM column mapper failed (non-fatal): {exc}")
    return {}


def _apply_column_mapping(
    rows: List[Dict[str, Any]],
    mapping: Dict[str, str],
) -> List[Dict[str, Any]]:
    """Rename row dict keys per LLM mapping before feeding to _parse_attachment_csv."""
    if not mapping:
        return rows
    result = []
    for row in rows:
        new_row = {mapping.get(k, k): v for k, v in row.items()}
        result.append(new_row)
    return result


# ── Sheet Comprehension — semantic row understanding ──────────────────────────

@dataclass
class ValueTransform:
    """One regex-based value override extracted from a source column."""
    source_column: str   # original header (before field_map rename)
    target_field: str    # canonical field to write (e.g. "ClaimPercent")
    pattern: str         # Python regex with one capture group
    extract_group: int   # which capture group holds the value
    cast: str            # "float", "int", or "str"
    overrides: bool      # if True, overwrites existing value for target_field


@dataclass
class RowFilter:
    """Describes which rows to drop before parsing."""
    column: str              # original header of the filter column
    skip_values: List[str]   # exact cell values that mean "skip this row"
    include_all_if_absent: bool  # if column is missing, include all rows


@dataclass
class SheetSchema:
    """
    Comprehensive understanding of a spreadsheet's structure, produced by a single
    LLM call on sample rows.  Applied deterministically to every row — no per-row
    LLM calls.
    """
    field_map: Dict[str, str]               # {original_header: CanonicalField}
    row_filter: Optional[RowFilter]         # None = include all rows
    value_transforms: List[ValueTransform]  # regex-based value overrides, in priority order
    notes_columns: List[str]               # original headers to concatenate → Notes
    name_resolution_columns: Dict[str, str] # {orig_header: target_id_field} for name→ID
    confidence: float                       # 0.0–1.0
    warnings: List[str]


def _fallback_schema(headers: List[str]) -> SheetSchema:  # noqa: ARG001
    """Return an empty pass-through schema when comprehension fails."""
    return SheetSchema(
        field_map={}, row_filter=None, value_transforms=[],
        notes_columns=[], name_resolution_columns={},
        confidence=0.0,
        warnings=["LLM comprehension failed — falling back to column-only mapping"],
    )


_COMPREHENSION_SYSTEM = (
    "You are a spreadsheet comprehension expert for a Simpro ERP invoice system.\n"
    "Given column headers and up to 5 sample rows from an uploaded spreadsheet, "
    "produce a SheetSchema JSON that describes how to interpret this sheet for invoicing.\n\n"
    "KEY CONCEPTS:\n"
    "- field_map: rename arbitrary column headers to canonical field names.\n"
    "- row_filter: a column that acts as a row directive — some values mean 'skip this row' "
    "(e.g. 'Nil Charge', 'Not Applicable'). Include only skip values here.\n"
    "- value_transforms: when a cell VALUE encodes a field override "
    "(e.g. 'Invoice 50%' means ClaimPercent=50). These rows are NOT skipped.\n"
    "  IMPORTANT: skip_values and value_transforms are MUTUALLY EXCLUSIVE — "
    "'Invoice 50%' must NOT appear in skip_values.\n"
    "- notes_columns: free-text columns to concatenate into the Notes field.\n"
    "- name_resolution_columns: columns containing text names (not integers) "
    "that need fuzzy-matching to Simpro IDs (e.g. cost centre names).\n\n"
    "Return ONLY valid JSON. No explanation outside the JSON object."
)


def _comprehend_sheet(
    raw_rows: List[Dict[str, Any]],
    headers: List[str],
    llm_fn,
) -> SheetSchema:
    """
    Produce a SheetSchema from sample rows using a single synchronous LLM call.

    Only fires the LLM when at least one header is unrecognised.
    Returns a trivial pass-through schema if all headers are already known.
    """
    unrecognised = [h for h in headers if _normalize_key(h) not in _ALL_KNOWN_HEADER_KEYS]
    if not unrecognised:
        return SheetSchema(
            field_map={}, row_filter=None, value_transforms=[],
            notes_columns=[], name_resolution_columns={},
            confidence=1.0, warnings=[],
        )

    field_list = "\n".join(f"  {k}: {v}" for k, v in _CANONICAL_FIELDS.items())
    sample_rows_text = "\n".join(
        "Row {}: {}".format(
            i + 1,
            ", ".join(f"{k}={v!r}" for k, v in row.items() if v not in (None, "")),
        )
        for i, row in enumerate(raw_rows[:5])
    )
    user_msg = (
        f"Canonical field names:\n{field_list}\n\n"
        f"Column headers: {json.dumps(headers)}\n\n"
        f"Sample rows (up to 5):\n{sample_rows_text}\n\n"
        "Produce a SheetSchema JSON:\n"
        "{\n"
        '  "field_map": {"OriginalHeader": "CanonicalField", ...},\n'
        '  "row_filter": {\n'
        '    "column": "StatusColumnHeader",\n'
        '    "skip_values": ["Nil Charge", "Not Applicable", "Invoice-Previously Claimed In Full"],\n'
        '    "include_all_if_absent": true\n'
        "  },\n"
        '  "value_transforms": [\n'
        "    {\n"
        '      "source_column": "Status - Invoice",\n'
        '      "target_field": "ClaimPercent",\n'
        '      "pattern": "Invoice\\\\s+(\\\\d+)%",\n'
        '      "extract_group": 1,\n'
        '      "cast": "float",\n'
        '      "overrides": true\n'
        "    }\n"
        "  ],\n"
        '  "notes_columns": ["Notes-Summary"],\n'
        '  "name_resolution_columns": {"CostCentre-Name": "CostCentreID"},\n'
        '  "confidence": 0.95,\n'
        '  "warnings": []\n'
        "}\n\n"
        "Rules:\n"
        "- field_map must only use CanonicalField names from the list above.\n"
        "- If a column contains cost centre NAMES (not integers), add to name_resolution_columns.\n"
        "- row_filter.skip_values: only values that mean 'do NOT invoice this row'.\n"
        "- value_transforms: only when a cell value ENCODES a field value like 'Invoice 50%'.\n"
        "- skip_values and value_transforms MUST NOT contain the same cell value.\n"
        "- confidence: 0.0–1.0 — how certain you are of this schema.\n"
        "- Omit columns whose purpose is unknown from field_map."
    )

    try:
        result = llm_fn(
            [
                {"role": "system", "content": _COMPREHENSION_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        raw_text = result.content if hasattr(result, "content") else str(result)
        data = json.loads(raw_text or "{}")
    except Exception as exc:
        logger.warning(f"[SheetComprehension] LLM call failed: {exc}")
        return _fallback_schema(headers)

    try:
        # Validate field_map — only canonical targets
        field_map = {
            k: v for k, v in (data.get("field_map") or {}).items()
            if v in _CANONICAL_FIELDS
        }

        # Parse row_filter
        row_filter: Optional[RowFilter] = None
        rf_data = data.get("row_filter")
        if rf_data and isinstance(rf_data, dict) and rf_data.get("column"):
            row_filter = RowFilter(
                column=rf_data["column"],
                skip_values=[str(v) for v in (rf_data.get("skip_values") or [])],
                include_all_if_absent=bool(rf_data.get("include_all_if_absent", True)),
            )

        # Parse value_transforms — validate regex compiles
        transforms: List[ValueTransform] = []
        for t in (data.get("value_transforms") or []):
            try:
                re.compile(t.get("pattern", ""))
                transforms.append(ValueTransform(
                    source_column=t["source_column"],
                    target_field=t["target_field"],
                    pattern=t["pattern"],
                    extract_group=int(t.get("extract_group", 1)),
                    cast=t.get("cast", "float"),
                    overrides=bool(t.get("overrides", True)),
                ))
            except (re.error, KeyError, TypeError) as exc:
                logger.warning(f"[SheetComprehension] Skipping invalid transform {t}: {exc}")

        name_resolution_columns = {
            str(k): str(v) for k, v in (data.get("name_resolution_columns") or {}).items()
        }

        return SheetSchema(
            field_map=field_map,
            row_filter=row_filter,
            value_transforms=transforms,
            notes_columns=[str(c) for c in (data.get("notes_columns") or [])],
            name_resolution_columns=name_resolution_columns,
            confidence=float(data.get("confidence", 0.5)),
            warnings=[str(w) for w in (data.get("warnings") or [])],
        )

    except Exception as exc:
        logger.warning(f"[SheetComprehension] Schema parse failed: {exc}")
        return _fallback_schema(headers)


def _apply_sheet_schema(
    raw_rows: List[Dict[str, Any]],
    schema: SheetSchema,
) -> str:
    """
    Apply SheetSchema deterministically to raw rows.
    Returns a CSV string ready for _parse_attachment_csv().
    """
    if not raw_rows:
        return ""

    # Step A — Row filter
    filtered_rows = raw_rows
    if schema.row_filter:
        fc = schema.row_filter.column
        col_present = fc in raw_rows[0]
        if col_present:
            skip_set = {v.strip() for v in schema.row_filter.skip_values}
            before = len(filtered_rows)
            filtered_rows = [
                r for r in filtered_rows
                if str(r.get(fc, "")).strip() not in skip_set
            ]
            logger.info(
                f"[SheetSchema] row_filter on '{fc}': "
                f"dropped {before - len(filtered_rows)}, kept {len(filtered_rows)}"
            )
        elif not schema.row_filter.include_all_if_absent:
            logger.warning(f"[SheetSchema] row_filter column '{fc}' not found — including all rows")

    if not filtered_rows:
        return ""

    # Step B — Rename columns per field_map
    renamed_rows: List[Dict[str, Any]] = []
    for row in filtered_rows:
        renamed_rows.append({schema.field_map.get(k, k): v for k, v in row.items()})

    # Step C — Value transforms
    for transform in schema.value_transforms:
        effective_col = schema.field_map.get(transform.source_column, transform.source_column)
        compiled = re.compile(transform.pattern)
        for row in renamed_rows:
            cell_val = str(row.get(effective_col, "")).strip()
            if not cell_val:
                continue
            m = compiled.search(cell_val)
            if m:
                try:
                    raw_val = m.group(transform.extract_group)
                    if transform.cast == "float":
                        typed_val: Any = float(raw_val)
                    elif transform.cast == "int":
                        typed_val = int(raw_val)
                    else:
                        typed_val = str(raw_val)
                    if transform.overrides or row.get(transform.target_field) in (None, ""):
                        row[transform.target_field] = typed_val
                except (ValueError, IndexError) as exc:
                    logger.debug(f"[SheetSchema] transform failed on '{cell_val}': {exc}")

    # Step D — Concatenate notes_columns → Notes
    if schema.notes_columns:
        for row in renamed_rows:
            if str(row.get("Notes", "")).strip():
                continue  # already has notes
            parts = []
            for nc in schema.notes_columns:
                effective_nc = schema.field_map.get(nc, nc)
                val = str(row.get(effective_nc, "")).strip()
                if val:
                    parts.append(val)
            if parts:
                row["Notes"] = "; ".join(parts)

    # Step E — Serialise to CSV
    all_keys: List[str] = []
    for row in renamed_rows:
        for k in row.keys():
            if k not in all_keys:
                all_keys.append(k)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=all_keys, extrasaction="ignore")
    writer.writeheader()
    for row in renamed_rows:
        writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in all_keys})
    return buf.getvalue()


def _batch_error_to_clarifications(batch_e: "BatchedClarificationError") -> List[Dict[str, Any]]:
    """Convert BatchedClarificationError → list of clarification dicts for the frontend."""
    result: List[Dict[str, Any]] = []
    for inner in batch_e.errors:
        if isinstance(inner, AmbiguousResolutionError):
            result.append({
                "row": 1, "type": "ambiguous",
                "field": inner.field, "message": inner.message,
                "options": inner.matches, "operation": "CREATE",
                "row_context": {"query": inner.value},
            })
        elif isinstance(inner, MissingFieldError):
            is_free = inner.context.get("free_text", False)
            is_multi = inner.context.get("multi_select", False)
            options = inner.context.get("options", [])
            clar_type = "free_text" if is_free else ("multi_select" if is_multi else "missing")
            entry: Dict[str, Any] = {
                "row": 1, "type": clar_type,
                "field": inner.field, "message": inner.message,
                "options": options, "operation": "CREATE",
                "row_context": {},
            }
            if is_free:
                entry["placeholder"] = inner.context.get("placeholder", inner.field)
            result.append(entry)
    return result


async def _resolve_name_columns(
    rows: List["ParsedRow"],
    schema: SheetSchema,
    mcp_executor: Any,
    llm_chat: Any,
    shared_state: Optional[AgentExecutionState] = None,
) -> List["ParsedRow"]:
    """
    For rows where schema flagged name_resolution_columns, resolve text names → IDs
    using EntityResolver.  Currently handles CostCentreID resolution.
    Mutates ParsedRow.cost_centre_id in place.
    """
    if not schema.name_resolution_columns or not mcp_executor:
        return rows

    resolver = EntityResolver(mcp_executor, llm_chat=llm_chat)
    collected_errors: List[Exception] = []

    for row_idx, row in enumerate(rows):
        for orig_col, target_id_field in schema.name_resolution_columns.items():
            effective_col = schema.field_map.get(orig_col, orig_col)
            name_val = (
                row.raw.get(effective_col)
                or row.raw.get(orig_col)
            )
            if not name_val:
                continue

            if target_id_field in ("CostCentreID", "CostCenterID"):
                if row.cost_centre_id is not None:
                    continue  # already resolved

                # Check cross-row entity cache first
                _cache_key = f"CostCentre.{name_val}"
                if shared_state:
                    cached = shared_state.entity_cache.get(_cache_key)
                    if cached:
                        row.cost_centre_id = cached["id"]
                        shared_state.log_resolution(
                            "CostCentre", str(name_val), cached["id"], cached.get("name", ""),
                            outcome="cache_hit", row_num=row_idx + 1,
                        )
                        continue

                try:
                    section = await resolver.resolve_section(
                        job_id=row.job_id,
                        cost_centre_name=str(name_val),
                        context="job",
                        row_num=row_idx + 1,
                    )
                    cc = await resolver.resolve_cost_centre(
                        job_id=row.job_id,
                        section_id=section["id"],
                        name=str(name_val),
                        context="job",
                        row_num=row_idx + 1,
                    )
                    row.cost_centre_id = cc["id"]
                    if shared_state:
                        shared_state.cache_entity("CostCentre", str(name_val), cc["id"], cc.get("name", ""))
                        shared_state.log_resolution(
                            "CostCentre", str(name_val), cc["id"], cc.get("name", ""),
                            outcome="resolved", row_num=row_idx + 1,
                        )
                    logger.info(
                        f"[SheetSchema] Row {row_idx + 1}: resolved "
                        f"'{name_val}' → CostCentreID={cc['id']}"
                    )
                except (AmbiguousResolutionError, MissingFieldError, ResolutionError) as e:
                    if shared_state:
                        outcome = "ambiguous" if isinstance(e, AmbiguousResolutionError) else "not_found"
                        shared_state.log_resolution(
                            "CostCentre", str(name_val), outcome=outcome, row_num=row_idx + 1,
                        )
                    logger.warning(
                        f"[SheetSchema] Row {row_idx + 1}: could not resolve "
                        f"'{name_val}' as CostCentreID: {e}"
                    )
                    collected_errors.append(e)

    if collected_errors:
        if len(collected_errors) == 1:
            raise collected_errors[0]
        raise BatchedClarificationError(errors=collected_errors)

    return rows


# UPDATED ParsedRow DATACLASS
# Replace lines 206-217 in svc-agent-invoice/src/invoice_agent.py

@dataclass
class ParsedRow:
    """Complete row data from Excel - preserves ALL fields"""
    
    # Core IDs
    job_id: int
    company_id: Optional[int]
    cost_centre_id: Optional[int]
    item_id: Optional[int]
    
    # Quantities and pricing
    quantity: Optional[float]
    unit_price_ex: Optional[float]
    discount_pct: Optional[float]
    
    # Claim values
    claim_percent: Optional[float]
    claim_extax: Optional[float]
    claim_inctax: Optional[float]
    
    # Item details
    item_code: Optional[str]
    line_description: Optional[str]
    tax_code: Optional[str]
    
    # Invoice metadata
    invoice_type: Optional[str]
    date_issued: Optional[str]
    stage: Optional[str]
    per_item: Optional[bool]
    payment_term_id: Optional[int]
    order_no: Optional[str]
    progress_claim_number: Optional[int]
    reference: Optional[str]
    
    # Text fields (preserve multi-line)
    description: Optional[str]
    notes: Optional[str]
    
    # Totals
    cc_total_ex: Optional[float]
    
    # Raw for debugging
    raw: Dict[str, Any]


def _parse_attachment_csv(attached_text: str) -> List[ParsedRow]:
    """
    Parse the attached CSV/Excel-as-text into a list of ParsedRow objects.

    We do not interpret SOP or grouping here; this is a pure extraction step.
    """
    rows: List[ParsedRow] = []
    if not attached_text:
        return rows

    f = io.StringIO(attached_text)
    reader = csv.DictReader(f)
    if not reader.fieldnames:
        return rows

    for row in reader:
        job_s = _get_field(row, "JobID", "Job Id", "Job_Id")
        if not job_s:
            continue
        try:
            job_id = int(float(job_s))
        except ValueError:
            continue

        company_id: Optional[int] = None
        company_s = _get_field(row, "CompanyID", "Company Id", "Company_Id")
        if company_s:
            try:
                company_id = int(float(company_s))
            except ValueError:
                company_id = None

        cc_id: Optional[int] = None
        cc_s = _get_field(row, "CostCentreID", "CostCenterID", "Cost Centre Id", "CostCentre", "CostCenter", "ID")
        if cc_s:
            try:
                cc_id = int(float(cc_s))
            except ValueError:
                cc_id = None

        item_id: Optional[int] = None
        item_s = _get_field(row, "ItemID", "Item Id", "Item_Id")
        if item_s:
            try:
                item_id = int(float(item_s))
            except ValueError:
                item_id = None

        qty: Optional[float] = None
        qty_s = _get_field(row, "Quantity", "Qty", "QtyTotal")
        if qty_s:
            try:
                qty = float(qty_s)
            except ValueError:
                qty = None

        cp = ce = ci = None
        # Prefer "Claimed Remaining" (what's left to invoice) over "Claimed To Date" (already invoiced).
        # Simpro GET exports use "Claimed Remaining Amount Ex Tax" / "Claimed To Date Percent" etc.
        cp_s = _get_field(
            row,
            "ClaimPercent", "Claim.Percent", "ClaimPercentage",
            "Claimed Remaining Percent", "ClaimedRemainingPercent",
            # Fallback: "Claimed To Date Percent" only if remaining is absent
        )
        if cp_s is None:
            cp_s = _get_field(row, "Claimed To Date Percent", "ClaimedToDatePercent", "Percent")

        ce_s = _get_field(
            row,
            "ClaimExTax", "Claim.ExTax",
            "Claimed Remaining Amount Ex Tax", "ClaimedRemainingAmountExTax",
        )
        if ce_s is None:
            ce_s = _get_field(row, "Claimed To Date Amount Ex Tax", "ClaimedToDateAmountExTax", "ExTax", "Ex Tax")

        ci_s = _get_field(
            row,
            "ClaimIncTax", "Claim.IncTax",
            "Claimed Remaining Amount Inc Tax", "ClaimedRemainingAmountIncTax",
        )
        if ci_s is None:
            ci_s = _get_field(row, "Claimed To Date Amount Inc Tax", "ClaimedToDateAmountIncTax", "IncTax", "Inc Tax")

        try:
            if cp_s not in (None, ""):
                cp = float(cp_s)
        except ValueError:
            cp = None
        try:
            if ce_s not in (None, ""):
                ce = float(ce_s)
        except ValueError:
            ce = None
        try:
            if ci_s not in (None, ""):
                ci = float(ci_s)
        except ValueError:
            ci = None

        # ===================================================================
        # ✅ PARSE ALL REMAINING FIELDS (PRESERVE ALL EXCEL DATA)
        # ===================================================================
        
        # Invoice metadata
        inv_type = _get_field(row, "Type", "InvoiceType", "Invoice Type")
        inv_type = _normalise_invoice_type(inv_type)
        # Write normalised type back into raw row so _build_attachment_summary
        # sends the valid Simpro value to the LLM (prevents clarification loops).
        if inv_type:
            for rk in list(row.keys()):
                if _normalize_key(rk) in ("type", "invoicetype"):
                    row[rk] = inv_type
        date_issued = _get_field(row, "DateIssued", "Date Issued", "Date")
        stage = _get_field(row, "Stage", "Status")
        
        per_item_s = _get_field(row, "PerItem", "Per Item")
        per_item = None
        if per_item_s:
            per_item = per_item_s.lower() in ('true', '1', 'yes', 'y')
        
        # Item details
        item_code = _get_field(row, "ItemCode", "Item Code", "Code")
        line_desc = _get_field(row, "LineDescription", "Line Description", "Line Desc")
        tax_code = _get_field(row, "TaxCode", "Tax Code", "Tax")
        
        # Pricing
        unit_price_ex = None
        unit_price_s = _get_field(row, "UnitPriceEx", "Unit Price Ex", "UnitPrice")
        if unit_price_s:
            try:
                unit_price_ex = float(unit_price_s)
            except (ValueError, TypeError):
                pass
        
        discount_pct = None
        discount_s = _get_field(row, "DiscountPct", "Discount Pct", "Discount")
        if discount_s:
            try:
                discount_pct = float(discount_s)
            except (ValueError, TypeError):
                pass
        
        # Invoice details
        payment_term_id = None
        payment_s = _get_field(row, "PaymentTermID", "Payment Term ID", "PaymentTerm")
        if payment_s:
            try:
                payment_term_id = int(float(payment_s))
            except (ValueError, TypeError):
                pass
        
        order_no = _get_field(row, "OrderNo", "Order No", "Order Number", "PO Number")
        
        progress_claim = None
        progress_s = _get_field(row, "ProgressClaimNumber", "Progress Claim Number", "Claim Number")
        if progress_s:
            try:
                progress_claim = int(float(progress_s))
            except (ValueError, TypeError):
                pass
        
        reference = _get_field(row, "Reference", "Ref", "ReferenceNo")
        
        # Totals
        cc_total_ex = None
        total_s = _get_field(row, "CCTotalEx", "CC Total Ex", "Total Ex Tax", "TotalExTax", "Total", "CostCentreTotal")
        if total_s:
            try:
                cc_total_ex = float(total_s)
            except (ValueError, TypeError):
                pass
        
        # ===================================================================
        # ✅ TEXT FIELDS - UNESCAPE NEWLINES AND SPECIAL CHARACTERS
        # ===================================================================
        desc_s = _get_field(row, "Description", "RequestDescription", "Request Description")
        if desc_s:
            # Convert escaped newlines to real newlines
            desc_s = desc_s.replace('\\n', '\n')
            desc_s = desc_s.replace('\\t', '\t')
            desc_s = desc_s.replace('\\r', '\r')
        
        notes_s = _get_field(row, "Notes", "Note", "Comments")
        if notes_s:
            # Convert escaped newlines to real newlines
            notes_s = notes_s.replace('\\n', '\n')
            notes_s = notes_s.replace('\\t', '\t')
            notes_s = notes_s.replace('\\r', '\r')

        # ===================================================================
        # ✅ CREATE ParsedRow WITH ALL FIELDS
        # ===================================================================
        rows.append(
            ParsedRow(
                # Core IDs
                job_id=job_id,
                company_id=company_id,
                cost_centre_id=cc_id,
                item_id=item_id,
                
                # Quantities and pricing
                quantity=qty,
                unit_price_ex=unit_price_ex,
                discount_pct=discount_pct,
                
                # Claim values
                claim_percent=cp,
                claim_extax=ce,
                claim_inctax=ci,
                
                # Item details
                item_code=item_code,
                line_description=line_desc,
                tax_code=tax_code,
                
                # Invoice metadata
                invoice_type=inv_type,
                date_issued=date_issued,
                stage=stage,
                per_item=per_item,
                payment_term_id=payment_term_id,
                order_no=order_no,
                progress_claim_number=progress_claim,
                reference=reference,
                
                # Text fields (with real newlines)
                description=desc_s,
                notes=notes_s,
                
                # Totals
                cc_total_ex=cc_total_ex,
                
                # Raw for debugging
                raw=row,
            )
        )

    return rows


def _build_attachment_summary(rows: List[ParsedRow]) -> str:
    """
    Build a small textual summary of the sheet for the LLM:
    - column names
    - example rows (up to 3)
    This avoids sending entire Excel if not necessary.
    """
    if not rows:
        return "<no-rows>"

    # Collect union of raw keys
    all_keys: List[str] = []
    for r in rows:
        for k in r.raw.keys():
            if k not in all_keys:
                all_keys.append(k)

    header_line = "Columns: " + ", ".join(all_keys)

    # Up to 3 sample rows
    examples: List[str] = []
    for r in rows[:3]:
        parts = [f"{k}={v}" for k, v in r.raw.items() if v not in (None, "")]
        examples.append("Row: " + ", ".join(parts))

    job_ids = sorted({r.job_id for r in rows})
    cc_ids = sorted({r.cost_centre_id for r in rows if r.cost_centre_id is not None})

    summary = [
        header_line,
        f"Distinct JobIDs: {job_ids[:10]}{' ...' if len(job_ids) > 10 else ''}",
        f"Distinct CostCentreIDs: {cc_ids[:10]}{' ...' if len(cc_ids) > 10 else ''}",
    ]
    summary.extend(examples)
    return "\n".join(summary)


# ---------------------------------------------------------------------------
# Chat-mode: parse natural language invoice request
# ---------------------------------------------------------------------------

_CHAT_INVOICE_SYSTEM_PROMPT = """You are an invoice data parser for a Simpro ERP system.
Extract invoice creation details from natural language.

Return ONLY valid JSON.

=== OUTPUT SCHEMA ===

{
  "invoices": [
    {
      "job_id": <int or null>,
      "job_name": <string or null>,
      "site_name": <string or null>,
      "company_id": <int or null>,
      "cost_centres": [
        {
          "cost_centre_id": <int or null>,
          "cost_centre_name": <string or null>,
          "claim_percent": <number or null>,
          "claim_extax": <number or null>,
          "claim_inctax": <number or null>,
          "items": [
            {"item_id": <int or null>, "quantity": <number or null>}
          ]
        }
      ],
      "invoice_type": <string or null>,
      "date_issued": <string or null>,
      "stage": <string or null>,
      "per_item": <boolean or null>,
      "description": <string or null>,
      "notes": <string or null>,
      "order_no": <string or null>,
      "payment_term_id": <int or null>
    }
  ]
}

=== FIELD RULES ===

1. job_id: Numeric Simpro Job ID. Use if user says "job 123".
   job_name: Free text job name if user references a name instead of ID.
   site_name: Free text site name / address if user references a site instead of job ID.
     Use when user says "site name X", "site X", "at X address", "for the X project site".
   At least one of job_id, job_name, or site_name is REQUIRED.

2. cost_centres: Array of cost centres for this invoice.
   - cost_centre_id: Numeric ID if provided (e.g. "CC 456", "cost centre 456")
   - cost_centre_name: Free text name (e.g. "Labour", "Materials")
   - claim_percent: 0-100 (e.g. "claim 100%", "50 percent")
   - claim_extax: Dollar amount excluding tax (e.g. "$1500 ex tax")
   - claim_inctax: Dollar amount including tax
   - If user doesn't specify any cost centre, use an empty array []

3. invoice_type: One of "TaxInvoice", "ProgressInvoice", "Deposit", "RequestForClaim"
   - "progress claim" / "progress invoice" → "ProgressInvoice"
   - "tax invoice" → "TaxInvoice"
   - "deposit" → "Deposit"
   - If not specified → null (agent will use SOP default)

4. date_issued: MUST be "YYYY-MM-DD" format. ALWAYS resolve relative dates to actual YYYY-MM-DD.
   - "today" → today's date in YYYY-MM-DD
   - "tomorrow", "next friday", "next week", "march 15" → calculate and output YYYY-MM-DD
   - NEVER pass relative expressions like "next tuesday" as-is — always resolve to YYYY-MM-DD.
   - If not specified → null (agent will default to today)

5. stage: "Approved" or "Pending". Default null if not specified.

6. per_item: true if user says "per item" or "itemised". Default null.

7. description / notes / order_no: Extract if user mentions them. Default null.

=== IMPORTANT RULES ===

- NEVER guess or invent IDs. Only extract what the user explicitly provides.
- If user mentions multiple jobs, create separate entries in the "invoices" array.
- If user says "all cost centres" or doesn't specify one, leave cost_centres as [].
- Prefer IDs over names when both are given.
- For claim values, only set the fields the user explicitly mentions.

=== CONVERSATION CONTEXT ===

When the user references "same job", "same cost centre", "same claim", "another invoice",
"same type", etc., resolve ACTUAL values from:
1. FOLLOW-UP FIELD BRIDGE (if present in the prompt) — pre-resolved values, use first.
2. Conversation history — look for job_id=, cost_centres=, claim_percent=, invoice_type= in previous assistant messages.
NEVER pass relative phrases as field values — always resolve to actual IDs, names, and numbers.

Common patterns:
- "same cost centre" → extract cost_centre_id and cost_centre_name from previous operation
- "same claim" / "same percentage" → extract claim_percent from previous operation
- "same type" → extract invoice_type from previous operation
- "for the same job" → extract job_id from previous operation

CROSS-PATH DATA: History may contain results from OTHER agents or MCP queries, not just invoices.
Extract common fields (job_id, cost_centre, names) from ANY history format:
- Schedule: "COMPLETED CREATE schedule: job_id=22601, cost_centre_id=116534, cost_centre_name=Drainage"
- Workorder: "[workorder agent succeeded: CREATED CJ 46450 (MTS Roofing)]"
- MCP data: "[Data Context — N items] ID=22601 Name=Bloomfield"
When user says "invoice for that job" and history shows a schedule/WO/MCP result with job_id, use it.

=== EXAMPLES ===

Example 1 - Simple:
Input: "create invoice for job 123"
Output: {"invoices": [{"job_id": 123, "job_name": null, "site_name": null, "cost_centres": [], "invoice_type": null, "date_issued": null, "stage": null, "per_item": null, "description": null, "notes": null, "order_no": null, "payment_term_id": null, "company_id": null}]}

Example 2 - With cost centre and claim:
Input: "create progress invoice for job 456 cost centre 789 claim 100%"
Output: {"invoices": [{"job_id": 456, "job_name": null, "site_name": null, "cost_centres": [{"cost_centre_id": 789, "cost_centre_name": null, "claim_percent": 100, "claim_extax": null, "claim_inctax": null, "items": []}], "invoice_type": "ProgressInvoice", "date_issued": null, "stage": null, "per_item": null, "description": null, "notes": null, "order_no": null, "payment_term_id": null, "company_id": null}]}

Example 3 - Multiple cost centres:
Input: "invoice job 100 CC Labour $5000 ex tax and CC Materials $3000 ex tax"
Output: {"invoices": [{"job_id": 100, "job_name": null, "site_name": null, "cost_centres": [{"cost_centre_id": null, "cost_centre_name": "Labour", "claim_percent": null, "claim_extax": 5000, "claim_inctax": null, "items": []}, {"cost_centre_id": null, "cost_centre_name": "Materials", "claim_percent": null, "claim_extax": 3000, "claim_inctax": null, "items": []}], "invoice_type": null, "date_issued": null, "stage": null, "per_item": null, "description": null, "notes": null, "order_no": null, "payment_term_id": null, "company_id": null}]}

Example 4 - With date and stage:
Input: "create approved tax invoice for job 200 dated 2026-03-01"
Output: {"invoices": [{"job_id": 200, "job_name": null, "site_name": null, "cost_centres": [], "invoice_type": "TaxInvoice", "date_issued": "2026-03-01", "stage": "Approved", "per_item": null, "description": null, "notes": null, "order_no": null, "payment_term_id": null, "company_id": null}]}

Example 4b - With site name:
Input: "create invoice for site name mercer street"
Output: {"invoices": [{"job_id": null, "job_name": null, "site_name": "mercer street", "cost_centres": [], "invoice_type": null, "date_issued": null, "stage": null, "per_item": null, "description": null, "notes": null, "order_no": null, "payment_term_id": null, "company_id": null}]}

Example 4c - Site name with cost centres:
Input: "invoice for nubeena crescent site, drainage cost centre, claim 100%"
Output: {"invoices": [{"job_id": null, "job_name": null, "site_name": "nubeena crescent", "cost_centres": [{"cost_centre_id": null, "cost_centre_name": "drainage", "claim_percent": 100, "claim_extax": null, "claim_inctax": null, "items": []}], "invoice_type": null, "date_issued": null, "stage": null, "per_item": null, "description": null, "notes": null, "order_no": null, "payment_term_id": null, "company_id": null}]}

=== CORRECTION PATTERNS ===

When the user corrects or references a previous invoice operation, resolve the
referenced entity from conversation history. Keep unchanged values from the previous
operation and only change what the user specifies.

Example 5 - Wrong job correction:
Previous: "CREATED invoice: job_id=10675, job_name=Smith Residence, cost_centres=[116534 (Drainage)], claim_percent=100, invoice_type=ProgressInvoice"
Input: "wrong job, should be 10680"
Output: {"invoices": [{"job_id": 10680, "job_name": null, "site_name": null, "cost_centres": [{"cost_centre_id": 116534, "cost_centre_name": "Drainage", "claim_percent": 100, "claim_extax": null, "claim_inctax": null, "items": []}], "invoice_type": "ProgressInvoice", "date_issued": null, "stage": null, "per_item": null, "description": null, "notes": null, "order_no": null, "payment_term_id": null, "company_id": null}]}

Example 6 - Same invoice for different job:
Previous: "CREATED invoice: job_id=10675, cost_centres=[116534 (Drainage), 116535 (Plumbing)], claim_percent=100, invoice_type=ProgressInvoice"
Input: "same invoice for job 10680"
Output: {"invoices": [{"job_id": 10680, "job_name": null, "site_name": null, "cost_centres": [{"cost_centre_id": 116534, "cost_centre_name": "Drainage", "claim_percent": 100, "claim_extax": null, "claim_inctax": null, "items": []}, {"cost_centre_id": 116535, "cost_centre_name": "Plumbing", "claim_percent": 100, "claim_extax": null, "claim_inctax": null, "items": []}], "invoice_type": "ProgressInvoice", "date_issued": null, "stage": null, "per_item": null, "description": null, "notes": null, "order_no": null, "payment_term_id": null, "company_id": null}]}

Example 7 - Change claim percentage:
Previous: "CREATED invoice: job_id=10675, cost_centres=[116534 (Drainage)], claim_percent=100"
Input: "change the claim to 50%"
Output: {"invoices": [{"job_id": 10675, "job_name": null, "site_name": null, "cost_centres": [{"cost_centre_id": 116534, "cost_centre_name": "Drainage", "claim_percent": 50, "claim_extax": null, "claim_inctax": null, "items": []}], "invoice_type": null, "date_issued": null, "stage": null, "per_item": null, "description": null, "notes": null, "order_no": null, "payment_term_id": null, "company_id": null}]}

Example 8 - Same for different cost centre:
Previous: "CREATED invoice: job_id=10675, cost_centres=[116534 (Drainage)], claim_percent=100, invoice_type=ProgressInvoice"
Input: "do the same for the Plumbing cost centre"
Output: {"invoices": [{"job_id": 10675, "job_name": null, "site_name": null, "cost_centres": [{"cost_centre_id": null, "cost_centre_name": "Plumbing", "claim_percent": 100, "claim_extax": null, "claim_inctax": null, "items": []}], "invoice_type": "ProgressInvoice", "date_issued": null, "stage": null, "per_item": null, "description": null, "notes": null, "order_no": null, "payment_term_id": null, "company_id": null}]}

Example 9 - Retry after failure with correction:
Previous: "FAILED invoice: job_id=10675, cost_centre_name=Metal Roof (not found), error=No cost centre matching"
Input: "try Drainage instead"
Output: {"invoices": [{"job_id": 10675, "job_name": null, "site_name": null, "cost_centres": [{"cost_centre_id": null, "cost_centre_name": "Drainage", "claim_percent": null, "claim_extax": null, "claim_inctax": null, "items": []}], "invoice_type": null, "date_issued": null, "stage": null, "per_item": null, "description": null, "notes": null, "order_no": null, "payment_term_id": null, "company_id": null}]}
"""

import logging as _logging

_inv_logger = _logging.getLogger(__name__)


def _parse_chat_invoice_request(
    user_text: str,
    llm_chat,
    hints: Optional[Dict[str, Any]] = None,
    conversation_history: Optional[list] = None,
) -> Dict[str, Any]:
    """
    Parse natural language invoice request into structured data.

    Mirrors schedule agent's _parse_chat_schedule_request() pattern:
    user text → LLM JSON extraction → structured dict.

    Returns:
        Dict with "invoices" key on success, or "error" key on failure.
    """
    from datetime import datetime as _dt

    _inv_logger.info(f"🗣️ Parsing chat invoice request: {user_text[:100]}")

    today_str = _dt.now().strftime("%Y-%m-%d")

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

    user_prompt = f"Today's date is {today_str}.{reuse_hint}\n\nExtract invoice data from: {user_text}"

    try:
        messages = [{"role": "system", "content": _CHAT_INVOICE_SYSTEM_PROMPT}]
        if conversation_history:
            messages.extend(conversation_history[-6:])
        messages.append({"role": "user", "content": user_prompt})

        response = llm_chat(
            messages,
            response_format={"type": "json_object"},
            temperature=0.0,
        )

        parsed = json.loads(response)
        _inv_logger.info(f"✅ Parsed chat invoice request: {json.dumps(parsed, default=str)[:300]}")

        invoices = parsed.get("invoices", [])
        if not invoices:
            return {
                "error": "NO_INVOICES_PARSED",
                "message": "Could not extract invoice details from your message. "
                           "Please specify at least a Job ID (e.g., 'create invoice for job 123').",
            }

        # Validate: at least one invoice has a job reference
        has_job = any(inv.get("job_id") or inv.get("job_name") or inv.get("site_name") for inv in invoices)
        if not has_job:
            return {
                "error": "NO_JOB_REFERENCE",
                "message": "Please specify a Job ID, job name, or site name for the invoice "
                           "(e.g., 'create invoice for job 123' or 'invoice for site name mercer street').",
            }

        return parsed

    except json.JSONDecodeError as e:
        _inv_logger.error(f"❌ Chat invoice parse: invalid JSON: {e}")
        return {"error": "PARSE_ERROR", "message": "Could not understand invoice request. Please try again."}
    except Exception as e:
        _inv_logger.error(f"❌ Chat invoice parse failed: {e}")
        return {"error": "PARSE_ERROR", "message": f"Could not parse invoice request: {str(e)}"}


def _chat_result_to_csv(parsed: Dict[str, Any]) -> str:
    """
    Convert parsed chat invoice JSON into CSV text that
    _parse_attachment_csv() already understands.

    This bridges chat-mode output into the existing Excel-mode pipeline.
    """
    from datetime import datetime as _dt

    headers = [
        "JobID", "CompanyID", "CostCentreID",
        "ClaimPercent", "ClaimExTax", "ClaimIncTax",
        "Type", "DateIssued", "Stage", "PerItem",
        "Description", "Notes", "OrderNo", "PaymentTermID",
    ]

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()

    for inv in parsed.get("invoices", []):
        job_id = inv.get("job_id") or ""
        company_id = inv.get("company_id") or ""
        inv_type = inv.get("invoice_type") or ""
        date_issued = inv.get("date_issued") or ""
        stage = inv.get("stage") or ""
        per_item = inv.get("per_item")
        per_item_s = str(per_item).lower() if per_item is not None else ""
        description = inv.get("description") or ""
        notes = inv.get("notes") or ""
        order_no = inv.get("order_no") or ""
        payment_term_id = inv.get("payment_term_id") or ""

        cost_centres = inv.get("cost_centres") or []

        if not cost_centres:
            # No specific cost centres — write a single row with just the job
            writer.writerow({
                "JobID": job_id,
                "CompanyID": company_id,
                "CostCentreID": "",
                "ClaimPercent": "",
                "ClaimExTax": "",
                "ClaimIncTax": "",
                "Type": inv_type,
                "DateIssued": date_issued,
                "Stage": stage,
                "PerItem": per_item_s,
                "Description": description,
                "Notes": notes,
                "OrderNo": order_no,
                "PaymentTermID": payment_term_id,
            })
        else:
            for cc in cost_centres:
                writer.writerow({
                    "JobID": job_id,
                    "CompanyID": company_id,
                    "CostCentreID": cc.get("cost_centre_id") or "",
                    "ClaimPercent": cc.get("claim_percent") if cc.get("claim_percent") is not None else "",
                    "ClaimExTax": cc.get("claim_extax") if cc.get("claim_extax") is not None else "",
                    "ClaimIncTax": cc.get("claim_inctax") if cc.get("claim_inctax") is not None else "",
                    "Type": inv_type,
                    "DateIssued": date_issued,
                    "Stage": stage,
                    "PerItem": per_item_s,
                    "Description": description,
                    "Notes": notes,
                    "OrderNo": order_no,
                    "PaymentTermID": payment_term_id,
                })

    result = buf.getvalue()
    _inv_logger.info(f"📝 Chat → CSV: {len(result)} chars, "
                     f"{result.count(chr(10)) - 1} data rows")
    return result


# ---------------------------------------------------------------------------
# Chat-mode: resolve job_name / site_name → job_id via EntityResolver
# ---------------------------------------------------------------------------

async def _resolve_chat_job_references(
    parsed: Dict[str, Any],
    mcp_executor: Any,
    llm_chat: Optional[Any] = None,
    shared_state: Optional[AgentExecutionState] = None,
) -> Dict[str, Any]:
    """
    Resolve job_name / site_name → job_id and cost_centre_name → cost_centre_id
    for each invoice in the parsed chat result, using the central EntityResolver.

    Collects clarification errors across all invoices so independent issues
    (e.g., two invoices with ambiguous jobs) are presented at once.

    Modifies parsed in-place: sets job_id and cost_centre_id on each invoice
    where resolution succeeds.

    Returns:
        The modified parsed dict with IDs filled in.

    Raises:
        AmbiguousResolutionError / MissingFieldError – single clarification needed.
        BatchedClarificationError – multiple independent clarifications needed.
    """
    resolver = EntityResolver(mcp_executor, llm_chat=llm_chat)
    collected_errors: list = []

    for idx, inv in enumerate(parsed.get("invoices", [])):
        # ── Job resolution ──
        job_id = inv.get("job_id")
        job_name = inv.get("job_name")
        site_name = inv.get("site_name")

        if not job_id and (job_name or site_name):
            _inv_logger.info(
                f"📍 Invoice #{idx + 1}: resolving "
                f"job_name={job_name!r}, site_name={site_name!r}"
            )
            try:
                resolved = await resolver.resolve_job(
                    name=job_name,
                    site_name=site_name,
                    row_num=idx + 1,
                )
                inv["job_id"] = resolved["id"]
                job_id = resolved["id"]
                if shared_state:
                    shared_state.cache_entity("Job", str(job_name or site_name), resolved["id"], resolved.get("name", ""))
                    shared_state.log_resolution(
                        "Job", str(job_name or site_name), resolved["id"], resolved.get("name", ""),
                        outcome="resolved", row_num=idx + 1,
                    )
                _inv_logger.info(
                    f"✅ Invoice #{idx + 1}: resolved → Job {resolved['id']} ({resolved['name']})"
                )
            except (AmbiguousResolutionError, MissingFieldError) as e:
                if shared_state:
                    outcome = "ambiguous" if isinstance(e, AmbiguousResolutionError) else "not_found"
                    shared_state.log_resolution(
                        "Job", str(job_name or site_name or ""), outcome=outcome, row_num=idx + 1,
                    )
                collected_errors.append(e)
                _inv_logger.info(
                    f"❓ Invoice #{idx + 1}: job needs clarification, continuing"
                )
                continue  # Can't resolve section/CC without job

        if not job_id:
            continue

        # ── Cost centre resolution ──
        cost_centres = inv.get("cost_centres") or []

        # If no cost centres specified at all, proactively fetch available
        # CCs from the job so the user can pick which ones to invoice.
        if not cost_centres:
            _inv_logger.info(
                f"📍 Invoice #{idx + 1}: no cost centres specified for Job {job_id} "
                f"— fetching available CCs for user selection"
            )
            all_cc_options = []
            sections = await resolver._fetch_sections(job_id, "job")
            for section in sections:
                section_id = section.get("ID")
                if not section_id:
                    continue
                section_name = section.get("Name", f"Section {section_id}")
                try:
                    ccs = await resolver._fetch_cost_centres(job_id, section_id, "job")
                    for cc in ccs:
                        cc_id = cc.get("ID")
                        cc_name = cc.get("Name", f"Cost Centre {cc_id}")
                        all_cc_options.append({
                            "id": cc_id,
                            "name": cc_name,
                            "group": section_name,
                        })
                except Exception as e:
                    _inv_logger.warning(
                        f"  ⚠️ Failed to fetch CCs for Job {job_id} Section {section_id}: {e}"
                    )

            if all_cc_options:
                _inv_logger.info(
                    f"📋 Job {job_id}: found {len(all_cc_options)} cost centres across "
                    f"{len(sections)} sections — asking user to select"
                )
                collected_errors.append(MissingFieldError(
                    field="CostCentreName",
                    message=(
                        f"Job {job_id} has {len(all_cc_options)} cost centres. "
                        f"Select which ones to invoice:"
                    ),
                    options=all_cc_options,
                    parent_id=job_id,
                    context="job",
                    multi_select=True,
                ))
            else:
                _inv_logger.warning(f"⚠️ Job {job_id}: no cost centres found in any section")

            continue

        # ── Claim follow-up: CCs have IDs but no claim data ──
        # After multi-select, user picked CCs but hasn't provided claim amounts.
        # Always ask — never assume or propagate.
        if cost_centres and all(cc.get("cost_centre_id") for cc in cost_centres):
            ccs_no_claim = [
                cc for cc in cost_centres
                if cc.get("claim_percent") is None
                and cc.get("claim_extax") is None
                and cc.get("claim_inctax") is None
            ]
            if ccs_no_claim:
                _inv_logger.info(
                    f"📍 Invoice #{idx + 1}: {len(ccs_no_claim)} CCs need claim data — "
                    f"asking user for claim amounts"
                )
                for cc in ccs_no_claim:
                    cc_id = cc["cost_centre_id"]
                    cc_name = cc.get("cost_centre_name") or f"Cost Centre {cc_id}"
                    collected_errors.append(MissingFieldError(
                        field=f"Claim_{cc_id}",
                        message=f"What claim amount for {cc_name}?",
                        free_text=True,
                        placeholder="e.g. 100% or $5000 ex tax",
                        cost_centre_id=cc_id,
                    ))
                continue

        for cc_idx, cc in enumerate(cost_centres):
            cc_id = cc.get("cost_centre_id")
            cc_name = cc.get("cost_centre_name")

            if cc_id or not cc_name:
                continue

            _inv_logger.info(
                f"📍 Invoice #{idx + 1}, CC #{cc_idx + 1}: resolving "
                f"cost_centre_name={cc_name!r} on Job {job_id}"
            )

            try:
                # Need section_id first — use pre-selected from clarification, or auto-resolve
                pre_section_id = inv.get("_section_id")
                section = await resolver.resolve_section(
                    job_id=job_id,
                    section_id=pre_section_id,
                    cost_centre_name=cc_name or None,
                    context="job",
                    row_num=idx + 1,
                )
                cc_resolved = await resolver.resolve_cost_centre(
                    job_id=job_id,
                    section_id=section["id"],
                    name=cc_name,
                    context="job",
                    row_num=idx + 1,
                )
                cc["cost_centre_id"] = cc_resolved["id"]
                _inv_logger.info(
                    f"✅ Invoice #{idx + 1}, CC #{cc_idx + 1}: resolved → "
                    f"CC {cc_resolved['id']} ({cc_resolved['name']})"
                )
            except (AmbiguousResolutionError, MissingFieldError) as e:
                collected_errors.append(e)
                _inv_logger.info(
                    f"❓ Invoice #{idx + 1}, CC #{cc_idx + 1}: needs clarification"
                )

    # ── Second pass: claim follow-up for freshly-resolved CCs ──
    # The claim check at the top of the per-invoice loop (line ~999) only fires
    # when CCs already have IDs.  When CCs start with just a name and get
    # resolved in the per-CC loop above, the claim check was already skipped.
    # Without this second pass, the missing claim falls through to the policy
    # planner, which returns a 'questions' format the frontend can't render.
    if not collected_errors:
        for idx, inv in enumerate(parsed.get("invoices", [])):
            job_id = inv.get("job_id")
            if not job_id:
                continue
            cost_centres = inv.get("cost_centres") or []
            if not cost_centres or not all(cc.get("cost_centre_id") for cc in cost_centres):
                continue
            ccs_no_claim = [
                cc for cc in cost_centres
                if cc.get("claim_percent") is None
                and cc.get("claim_extax") is None
                and cc.get("claim_inctax") is None
            ]
            if ccs_no_claim:
                _inv_logger.info(
                    f"📍 Invoice #{idx + 1}: {len(ccs_no_claim)} freshly-resolved CCs need claim data"
                )
                for cc in ccs_no_claim:
                    cc_id = cc["cost_centre_id"]
                    cc_name = cc.get("cost_centre_name") or f"Cost Centre {cc_id}"
                    collected_errors.append(MissingFieldError(
                        field=f"Claim_{cc_id}",
                        message=f"What claim amount for {cc_name}?",
                        free_text=True,
                        placeholder="e.g. 100% or $5000 ex tax",
                        cost_centre_id=cc_id,
                    ))

    # Raise collected clarification errors
    if collected_errors:
        if len(collected_errors) == 1:
            raise collected_errors[0]
        raise BatchedClarificationError(errors=collected_errors)

    return parsed


# ---------------------------------------------------------------------------
# LLM: policy + defaults
# ---------------------------------------------------------------------------

def _llm_plan_policy(
    llm_chat,
    user_text: str,
    sop_text: str,
    attachment_summary: str,
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """
    Ask the LLM for invoice policy JSON.

    Uses domain knowledge from the crossroads registry for consistency
    with schedule/workorder agents. Invoice policy uses its own prompt
    (not a crossroads type) because it needs SOP text, conversation
    history, and attachment summary which don't fit the crossroads API.

    Returns:
        Policy dict with keys: invoice_mode, per_item, description_mode,
        description_joiner, defaults, missing
    """
    from datetime import datetime as _dt
    today_str = _dt.now().strftime("%Y-%m-%d")

    # Inject domain knowledge from crossroads registry if available
    domain_section = ""
    try:
        from utils.crossroads import _DOMAIN_KNOWLEDGE
        invoice_domain = _DOMAIN_KNOWLEDGE.get("simpro_invoices", "")
        if invoice_domain:
            domain_section = f"\nDOMAIN KNOWLEDGE:\n{invoice_domain}\n\n"
    except ImportError:
        pass

    # Include conversation history for context awareness
    history_section = ""
    if conversation_history:
        recent = conversation_history[-6:]  # last 3 exchanges
        history_lines = []
        for msg in recent:
            role = msg.get("role", "user")
            content = msg.get("content", "")[:200]
            history_lines.append(f"{role}: {content}")
        if history_lines:
            history_section = "\nCONVERSATION HISTORY (recent):\n" + "\n".join(history_lines) + "\n\n"

    # System prompt aligned with crossroads invoice_policy type
    system_prompt = (
        "You are an invoice policy planner for a construction back-office system (Simpro ERP).\n"
        "You receive the SOP, user message, and attachment data, and return a structured policy JSON.\n"
        "SOP is the source of truth for defaults — always follow SOP unless the user explicitly overrides.\n"
        "Be precise. Use the data provided. Never hallucinate values not present in the context.\n"
        f"{domain_section}"
        "Return ONLY valid JSON — no explanation outside the JSON object."
    )

    user_msg = (
        f"Today's date is {today_str}.\n\n"
        f"SOP (verbatim):\n{sop_text}\n\n"
        f"{history_section}"
        f"USER MESSAGE:\n{user_text}\n\n"
        f"ATTACHMENT SUMMARY (schema + sample rows):\n{attachment_summary}\n\n"
        "TASK:\n"
        "1) Decide invoice_mode: 'per_job', 'per_cost_centre', or 'per_item'.\n"
        "2) Decide per_item (true/false) which maps to Simpro's PerItem field.\n"
        "3) Decide how descriptions should be handled (combine vs per_first).\n"
        "4) Provide invoice defaults under 'defaults' — use SOP defaults, do NOT ask.\n"
        "5) ONLY add a question to 'missing' if there is genuinely ambiguous business logic.\n"
        "Return STRICT JSON with keys: invoice_mode, per_item, description_mode, "
        "description_joiner, defaults, missing."
    )
    out = llm_chat(
        [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_msg}],
        response_format={"type": "json_object"},
        temperature=0,
    )
    try:
        return json.loads(out or "{}")
    except Exception:
        return {
            "invoice_mode": None,
            "per_item": None,
            "description_mode": None,
            "description_joiner": None,
            "defaults": {},
            "missing": ["LLM output not valid JSON; please restate SOP more clearly."],
        }


# ---------------------------------------------------------------------------
# SOP deviation detection — interactive clarification
# ---------------------------------------------------------------------------

def _detect_sop_deviations(
    rows: List[ParsedRow],
    policy: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Compare user-provided row data against the LLM policy / SOP defaults.

    Returns a list of *clarification* entries (type ``confirmation``) for
    every mismatch found.  The invoice agent surfaces these to the user via
    the interactive ClarificationForm before proceeding.
    """
    deviations: List[Dict[str, Any]] = []
    defaults = policy.get("defaults") or {}
    per_item_flag = bool(policy.get("per_item"))

    # 1. PerItem mismatch: compare SOP PerItem with user's actual input type
    # PerItem=false → CC-level claims (Claim.Percent/ExTax/IncTax)
    # PerItem=true  → item-level claims (Items with ID + Quantity)
    has_cc_claims = any(
        r.cost_centre_id is not None
        and (r.claim_percent or r.claim_extax or r.claim_inctax)
        for r in rows
    )
    has_item_data = any(
        r.item_id is not None and r.quantity is not None
        for r in rows
    )

    if per_item_flag and has_cc_claims and not has_item_data:
        # SOP says PerItem=true (per line item) but user provided CC-level claims
        deviations.append({
            "row": 1,
            "type": "confirmation",
            "field": "PerItem",
            "message": (
                "You specified cost centre claims (CC-level totals), but the SOP "
                "default is PerItem=true (per line item invoicing). CC-level claims "
                "require PerItem=false. Would you like to switch to PerItem=false?"
            ),
            "options": [
                {"id": "false", "name": "Yes, use PerItem=false (apply my CC claims)"},
                {"id": "true", "name": "No, keep PerItem=true (per line item)"},
            ],
            "operation": "CREATE",
            "row_context": {
                "SOP_Default": "PerItem=true",
                "User_Intent": "CC-level claims",
            },
        })
    elif not per_item_flag and has_item_data and not has_cc_claims:
        # SOP says PerItem=false (CC-level) but user provided item-level data
        deviations.append({
            "row": 1,
            "type": "confirmation",
            "field": "PerItem",
            "message": (
                "You specified item-level data (item IDs + quantities), but the SOP "
                "default is PerItem=false (consolidated by CC total). Item-level "
                "invoicing requires PerItem=true. Would you like to switch to PerItem=true?"
            ),
            "options": [
                {"id": "true", "name": "Yes, use PerItem=true (apply my item data)"},
                {"id": "false", "name": "No, keep PerItem=false (CC total)"},
            ],
            "operation": "CREATE",
            "row_context": {
                "SOP_Default": "PerItem=false",
                "User_Intent": "Item-level data",
            },
        })

    # 2. User-provided invoice type differs from SOP
    user_types = {r.invoice_type for r in rows if r.invoice_type}
    sop_type = defaults.get("Type")
    if user_types and sop_type and user_types != {sop_type}:
        for user_type in user_types:
            if user_type != sop_type:
                deviations.append({
                    "row": 1,
                    "type": "confirmation",
                    "field": "InvoiceType",
                    "message": (
                        f"You specified invoice type '{user_type}', but the "
                        f"SOP default is '{sop_type}'. Which type should be used?"
                    ),
                    "options": [
                        {"id": user_type, "name": f"Use '{user_type}' (your request)"},
                        {"id": sop_type, "name": f"Use '{sop_type}' (SOP default)"},
                    ],
                    "operation": "CREATE",
                    "row_context": {
                        "SOP_Default": sop_type,
                        "User_Request": user_type,
                    },
                })
                break

    # 3. User-provided stage differs from SOP
    user_stages = {r.stage for r in rows if r.stage}
    sop_stage = defaults.get("Stage")
    if user_stages and sop_stage and user_stages != {sop_stage}:
        for user_stage in user_stages:
            if user_stage != sop_stage:
                deviations.append({
                    "row": 1,
                    "type": "confirmation",
                    "field": "Stage",
                    "message": (
                        f"You specified stage '{user_stage}', but the SOP "
                        f"default is '{sop_stage}'. Which stage should be used?"
                    ),
                    "options": [
                        {"id": user_stage, "name": f"Use '{user_stage}' (your request)"},
                        {"id": sop_stage, "name": f"Use '{sop_stage}' (SOP default)"},
                    ],
                    "operation": "CREATE",
                    "row_context": {
                        "SOP_Default": sop_stage,
                        "User_Request": user_stage,
                    },
                })
                break

    return deviations


# ---------------------------------------------------------------------------
# Invoice body preparation (no HTTP calls)
# ---------------------------------------------------------------------------

def _prepare_invoice_for_creation(company_id: int, body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Prepare invoice body for creation (no HTTP call).
    Backend will handle actual creation using MCP tools.
    
    Args:
        company_id: Simpro company ID
        body: Invoice request body matching Simpro API format
    
    Returns:
        Prepared invoice data
    """
    return {
        "company_id": company_id,
        "invoice_data": body,
        "status": "prepared"  # Will be created by backend
    }
# ---------------------------------------------------------------------------
# Grouping + body construction
# ---------------------------------------------------------------------------

def _group_rows(rows: List[ParsedRow], invoice_mode: str) -> Dict[Tuple[Any, ...], List[ParsedRow]]:
    """
    Group rows according to invoice_mode:

    - per_job          -> key = (job_id,)
    - per_cost_centre  -> key = (job_id, cost_centre_id)
    - per_item         -> key = (job_id, cost_centre_id, item_id)
    """
    groups: Dict[Tuple[Any, ...], List[ParsedRow]] = {}
    for r in rows:
        if invoice_mode == "per_cost_centre":
            key = (r.job_id, r.cost_centre_id)
        elif invoice_mode == "per_item":
            key = (r.job_id, r.cost_centre_id, r.item_id)
        else:  # default/fallback: per_job
            key = (r.job_id,)
        groups.setdefault(key, []).append(r)
    return groups


def _build_claim(row: ParsedRow) -> Dict[str, float]:
    claim: Dict[str, float] = {}
    if row.claim_percent is not None:
        claim["Percent"] = row.claim_percent
    if row.claim_extax is not None:
        claim["ExTax"] = row.claim_extax
    if row.claim_inctax is not None:
        claim["IncTax"] = row.claim_inctax
    return claim


def _combine_descriptions(rows: List[ParsedRow], mode: str, joiner: str) -> Optional[str]:
    texts = [r.description for r in rows if r.description not in (None, "")]
    if not texts:
        return None
    if mode == "per_first":
        return texts[0]
    if len(texts) == 1:
        return texts[0]
    # Multiple descriptions (e.g. multiple cost centres for same job):
    # Separate each cost centre's description block with a blank line.
    # This preserves internal multi-line structure within each CC.
    return "\n\n".join(texts)


def _resolve_company_id(group_rows: List[ParsedRow], defaults: Dict[str, Any], hints: Dict[str, Any]) -> Optional[int]:
    """Resolve company ID — delegates to central EntityResolver."""
    return EntityResolver.resolve_company_id(group_rows, defaults, hints)


def _build_invoice_bodies(
    rows: List[ParsedRow],
    policy: Dict[str, Any],
    hints: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Deterministically build invoice bodies for all groups.

    Returns a structure ready for the chat layer:
    {
      "multi": True/False,
      "defaults": {...},
      "jobs": [
        {
          "job_id": ...,
          "company_id": ...,
          "grouping": { "invoice_mode": ..., "job_id": ..., "cost_centre_id": ..., "item_id": ... },
          "request": {"company_id": ..., "body": {...}},
          "response": {...}
        },
        ...
      ]
    }
    """
    invoice_mode = policy.get("invoice_mode") or "per_job"
    per_item_flag = bool(policy.get("per_item"))
    desc_mode = "combine"
    desc_joiner = policy.get("description_joiner") or ";"
    defaults = policy.get("defaults") or {}

    groups = _group_rows(rows, invoice_mode)
    job_results: List[Dict[str, Any]] = []
    skipped_groups: List[Dict[str, Any]] = []

    # Core required defaults
    inv_type = defaults.get("Type")
    stage = defaults.get("Stage")
    date_issued = defaults.get("DateIssued")
    if not date_issued:
        from datetime import datetime as _dt
        date_issued = _dt.now().strftime("%Y-%m-%d")
        _inv_logger.info(f"DateIssued not in policy defaults, defaulting to today: {date_issued}")

    if not inv_type or not stage:
        return {
            "error": "POLICY_INCOMPLETE",
            "details": "LLM policy missing required defaults 'Type' or 'Stage'.",
            "policy": policy,
        }

    payment_term_id = defaults.get("PaymentTermID")
    order_no = defaults.get("OrderNo")
    notes = defaults.get("Notes")
    late_fee = defaults.get("LatePaymentFee")
    cis_rate = defaults.get("CISDeductionRate")
    accounting_cat = defaults.get("AccountingCategory")
    status = defaults.get("Status")
    auto_adjust = defaults.get("AutoAdjustStatus")

    for key, group_rows in groups.items():
        # Derive breakdown from key
        job_id = key[0] if len(key) >= 1 else None
        cost_centre_id = key[1] if len(key) >= 2 else None
        item_id = key[2] if len(key) >= 3 else None

        if job_id is None:
            # Should never happen if parse logic worked
            return {
                "error": "JOB_ID_MISSING",
                "details": f"Group key {key} missing job_id.",
                "policy": policy,
            }

        company_id = _resolve_company_id(group_rows, defaults, hints)
        if company_id is None:
            skipped_groups.append({
                "job_id": job_id,
                "group_key": str(key),
                "reason": f"CompanyID missing for job {job_id}.",
            })
            continue

        # Build CostCenters according to mode
        cost_centers: List[Dict[str, Any]] = []
        skipped_zero_claim: List[int] = []  # CCs skipped because remaining claim = 0

        if invoice_mode == "per_item" and per_item_flag:
            # Each group key already includes (job, cc, item)
            # Consolidate into a single CostCenter with Items.
            # If there are multiple rows, aggregate quantities and/or merge claims naively.
            base_cc_id = cost_centre_id
            items_map: Dict[int, float] = {}
            claim_for_cc: Dict[str, float] = {}
            for r in group_rows:
                if r.item_id is not None:
                    qty = r.quantity or 0.0
                    items_map[r.item_id] = items_map.get(r.item_id, 0.0) + qty
                # Use the first non-empty claim as the invoice claim
                claim_piece = _build_claim(r)
                if claim_piece and not claim_for_cc:
                    claim_for_cc = claim_piece
            items = [{"ID": iid, "Quantity": q} for iid, q in items_map.items()]
            cost_centers.append(
                {
                    "ID": base_cc_id,
                    "Claim": claim_for_cc or {},
                    "Items": items,
                }
            )
        else:
            # per_job or per_cost_centre:
            # 1) Aggregate duplicate cost centres into a single entry per ID
            # 2) Keep group_rows intact so description combines all rows.
            cc_map: Dict[int, Dict[str, Any]] = {}

            for r in group_rows:
                if r.cost_centre_id is None:
                    continue

                claim_piece = _build_claim(r)
                if not claim_piece:
                    # no claim info at all on this row: skip it
                    _inv_logger.debug(f"  CC {r.cost_centre_id}: no claim data, skipping")
                    continue

                # Skip cost centres where remaining claim is all zeros
                # (already fully claimed — nothing left to invoice)
                if all(v == 0 or v == 0.0 for v in claim_piece.values()):
                    skipped_zero_claim.append(r.cost_centre_id)
                    _inv_logger.info(f"  CC {r.cost_centre_id}: fully claimed (remaining=0), auto-skipping")
                    continue

                cc_id_int = r.cost_centre_id
                entry = cc_map.get(cc_id_int)

                if entry is None:
                    # First time we see this cost centre in this group
                    cc_entry: Dict[str, Any] = {"ID": cc_id_int, "Claim": dict(claim_piece)}

                    if per_item_flag and r.item_id is not None:
                        qty = r.quantity if r.quantity is not None else 0.0
                        cc_entry["Items"] = [{"ID": r.item_id, "Quantity": qty}]

                    cc_map[cc_id_int] = cc_entry
                else:
                    # Aggregate claim fields by summing numeric fields
                    existing_claim = entry.setdefault("Claim", {})
                    for k, v in claim_piece.items():
                        # if existing is numeric, sum; otherwise just set it
                        if isinstance(v, (int, float)) and isinstance(existing_claim.get(k), (int, float)):
                            existing_claim[k] += v
                        elif k not in existing_claim:
                            existing_claim[k] = v

                    # If per_item is on, aggregate item quantities per item ID as well
                    if per_item_flag and r.item_id is not None:
                        qty = r.quantity if r.quantity is not None else 0.0
                        items_list = entry.setdefault("Items", [])
                        for item in items_list:
                            if item.get("ID") == r.item_id:
                                item["Quantity"] = (item.get("Quantity") or 0.0) + qty
                                break
                        else:
                            items_list.append({"ID": r.item_id, "Quantity": qty})

            cost_centers = list(cc_map.values())
            if skipped_zero_claim:
                _inv_logger.info(
                    f"Job {job_id}: skipped {len(skipped_zero_claim)} fully-claimed CCs "
                    f"({skipped_zero_claim}), kept {len(cost_centers)} with remaining claim"
                )


        # When PerItem=false (consolidated), Simpro invoices all cost
        # centres automatically — we don't need any in the body.  Only
        # skip the group when PerItem=true and no claimable CCs exist.
        if not cost_centers and per_item_flag:
            extra = ""
            if skipped_zero_claim:
                extra = (
                    f" ({len(skipped_zero_claim)} cost centres were already fully claimed"
                    f" and have no remaining amount to invoice.)"
                )
            skipped_groups.append({
                "job_id": job_id,
                "group_key": str(key),
                "reason": f"No claimable cost centres for job {job_id}.{extra}",
            })
            continue

        # Description handling: preserve text exactly, only join
        description = _combine_descriptions(group_rows, desc_mode, desc_joiner)
        if description:
            print(f"[invoice_agent][description][raw] {repr(description)}")

        body: Dict[str, Any] = {
            "Type": inv_type,
            "Jobs": [job_id],
            "DateIssued": date_issued,
            "Stage": stage,
            "PerItem": per_item_flag,
        }

        # Simpro CostCenters handling depends on PerItem:
        # - PerItem=true  → send CostCenters with Items array (per line item)
        # - PerItem=false → send CostCenters with Claim only (per CC total),
        #                    strip Items array (only valid for PerItem=true)
        if cost_centers:
            if per_item_flag:
                body["CostCenters"] = cost_centers
            else:
                body["CostCenters"] = [
                    {k: v for k, v in cc.items() if k != "Items"}
                    for cc in cost_centers
                ]

        if payment_term_id is not None:
            body["PaymentTermID"] = payment_term_id
        if order_no is not None:
            body["OrderNo"] = order_no
        if late_fee is not None:
            body["LatePaymentFee"] = late_fee
        if cis_rate is not None:
            body["CISDeductionRate"] = cis_rate
        if accounting_cat is not None:
            body["AccountingCategory"] = accounting_cat
        if status is not None:
            body["Status"] = status
        if auto_adjust is not None:
            body["AutoAdjustStatus"] = auto_adjust
        if description is not None:
            # Simpro Description field supports HTML; convert newlines to <br>
            body["Description"] = description.replace("\n", "<br>")
            print(f"[invoice_agent][description][body] {body['Description']}")
        if notes is not None:
            # Simpro Notes field supports HTML; convert newlines to <br>
            body["Notes"] = notes.replace("\n", "<br>")
        # ===================================================================
        # ✅ INCLUDE ALL ADDITIONAL FIELDS FROM EXCEL (ZERO DATA LOSS)
        # ===================================================================
        
        # Get first row values for metadata (should be same for grouped rows)
        first_row = group_rows[0]
        
        # Order number
        if first_row.order_no:
            body["OrderNo"] = first_row.order_no
        elif order_no:  # From LLM defaults
            body["OrderNo"] = order_no
        
        # Progress claim number
        if first_row.progress_claim_number:
            body["ProgressClaimNumber"] = first_row.progress_claim_number
        
        # Reference
        if first_row.reference:
            body["Reference"] = first_row.reference
        
        # ✅ NOTES: Combine from all rows (like description)
        notes_texts = [r.notes for r in group_rows if r.notes not in (None, "")]
        if notes_texts:
            # Combine with same joiner as description
            combined_notes = (desc_joiner or ";").join(notes_texts)
            body["Notes"] = combined_notes
        
        # Payment term (prioritize row data over defaults)
        if first_row.payment_term_id:
            body["PaymentTermID"] = first_row.payment_term_id


        resp = _prepare_invoice_for_creation(company_id, body)

        job_results.append(
            {
                "job_id": job_id,
                "company_id": company_id,
                "grouping": {
                    "invoice_mode": invoice_mode,
                    "job_id": job_id,
                    "cost_centre_id": cost_centre_id,
                    "item_id": item_id,
                },
                "request": {"company_id": company_id, "body": body},
                "response": resp,
            }
        )
    # Handle skipped groups:
    # - ≤5 skipped → ask clarification (even if some valid groups exist)
    # - >5 skipped → proceed with valid ones, show skipped as errors in presenter
    if skipped_groups and len(skipped_groups) <= 5:
        questions = [s["reason"] for s in skipped_groups]
        return {
            "success": False,
            "needs_clarification": True,
            "questions": questions,
            "message": (
                "I couldn't build invoices for some items:\n"
                + "\n".join(f"• {q}" for q in questions)
                + "\nPlease provide the missing information."
            ),
            "policy": policy,
            # Preserve valid jobs so they can be retried after clarification
            "jobs_pending": job_results,
        }

    result = {
        "success": True,
        "multi": len(job_results) > 1,
        "defaults": {
            "Type": inv_type,
            "DateIssued": date_issued,
            "Stage": stage,
            "PerItem": per_item_flag,
            "PaymentTermID": payment_term_id,
            "OrderNo": order_no,
            "Notes": notes,
        },
        "jobs": job_results,
        "skipped": skipped_groups,
        "policy": policy,
    }
    print(f"[DEBUG] _build_invoice_bodies returning: success={result.get('success')}")
    print(f"[DEBUG] Keys: {list(result.keys())}")
    print(f"[DEBUG] Jobs count: {len(result.get('jobs', []))}, Skipped: {len(skipped_groups)}")
    # Backwards-compatible top-level structure for chat presenter
    return result


# ---------------------------------------------------------------------------
# Public entrypoint (CLI + Chatbox)
# ---------------------------------------------------------------------------
def _extracted_to_csv_text(extracted: Dict[str, Any]) -> Optional[str]:
    """
    Convert extractor output into CSV text that our existing parser understands.

    Expected extractor structure (v1 assumption):
      extracted["tables"] -> list of tables
      each table has either:
        - "rows": [ {col: val, ...}, ... ]   OR
        - "data": [ {col: val, ...}, ... ]
    We return CSV with headers derived from the first row.
    """
    if not extracted or not isinstance(extracted, dict):
        return None

    tables = extracted.get("tables") or []
    if not isinstance(tables, list) or not tables:
        return None

    # pick the "best" table: most rows
    best = None
    best_n = 0
    for t in tables:
        if not isinstance(t, dict):
            continue
        rows = t.get("rows") or t.get("data") or []
        if isinstance(rows, list) and len(rows) > best_n:
            best = rows
            best_n = len(rows)

    if not best or not isinstance(best, list) or not best:
        return None

    # Ensure dict rows
    dict_rows = [r for r in best if isinstance(r, dict)]
    if not dict_rows:
        return None

    # Build CSV
    headers = []
    for k in dict_rows[0].keys():
        headers.append(str(k))

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    for r in dict_rows:
        writer.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in headers})
    return buf.getvalue()


# ---------------------------------------------------------------------------
# MCP pre-flight validation
# ---------------------------------------------------------------------------

async def _validate_with_mcp(
    rows: List[ParsedRow],
    mcp_executor: MCPToolExecutor,
) -> Dict[str, Any]:
    """
    Pre-flight validation using MCP tools before building invoice bodies.

    Validates:
    1. Job IDs exist in Simpro
    2. Cost Centre IDs belong to the referenced jobs
    3. Cost centres aren't already 100% claimed (warning only)

    Returns:
        {
            "valid_rows": [...],
            "invalid_rows": [...],
            "warnings": [...],
            "job_cache": {job_id: job_data},
            "cc_cache": {job_id: {cc_id: cc_data}},
        }
    """
    valid_rows: List[ParsedRow] = []
    invalid_rows: List[Dict[str, Any]] = []
    warnings: List[str] = []
    job_cache: Dict[int, Dict[str, Any]] = {}
    cc_cache: Dict[int, Dict[int, Dict[str, Any]]] = {}

    # ── Step 1: Validate Job IDs exist ──────────────────────────────────────
    unique_job_ids = sorted({r.job_id for r in rows})
    _inv_logger.info(f"🔍 Validating {len(unique_job_ids)} unique Job IDs via MCP")

    invalid_job_ids: set = set()

    async def _check_job(job_id: int):
        try:
            result = await mcp_executor.call_tool("get_job_details", {"job_id": job_id})
            job = result.get("job", {})
            if job and job.get("ID"):
                job_cache[job_id] = job
                _inv_logger.info(f"  ✅ Job {job_id}: {job.get('Name', 'unknown')}")
            else:
                invalid_job_ids.add(job_id)
                _inv_logger.warning(f"  ❌ Job {job_id}: not found")
        except Exception as e:
            invalid_job_ids.add(job_id)
            _inv_logger.warning(f"  ❌ Job {job_id}: lookup failed: {e}")

    # Validate jobs in parallel
    await asyncio.gather(*[_check_job(jid) for jid in unique_job_ids])

    # ── Step 2: Validate Cost Centres belong to their jobs ──────────────────
    # Only check for valid jobs that have rows with cost centre IDs
    jobs_needing_cc_check = {
        r.job_id for r in rows
        if r.cost_centre_id is not None and r.job_id not in invalid_job_ids
    }

    async def _fetch_job_cost_centres(job_id: int):
        """Fetch all cost centres across all sections of a job."""
        cc_map: Dict[int, Dict[str, Any]] = {}
        try:
            sections_result = await mcp_executor.call_tool(
                "get_job_sections", {"job_id": job_id}
            )
            sections = sections_result.get("sections", [])
            for section in sections:
                section_id = section.get("ID")
                if not section_id:
                    continue
                try:
                    cc_result = await mcp_executor.call_tool(
                        "get_job_section_cost_centres",
                        {"job_id": job_id, "section_id": section_id},
                    )
                    for cc in cc_result.get("cost_centres", []):
                        cc_id = cc.get("ID")
                        if cc_id:
                            cc["_section_id"] = section_id
                            cc_map[cc_id] = cc
                except Exception as e:
                    _inv_logger.warning(
                        f"  ⚠️ Failed to fetch CCs for Job {job_id} Section {section_id}: {e}"
                    )
        except Exception as e:
            _inv_logger.warning(f"  ⚠️ Failed to fetch sections for Job {job_id}: {e}")
        cc_cache[job_id] = cc_map

    # Fetch cost centres in parallel across jobs
    if jobs_needing_cc_check:
        _inv_logger.info(f"🔍 Fetching cost centres for {len(jobs_needing_cc_check)} jobs")
        await asyncio.gather(*[_fetch_job_cost_centres(jid) for jid in jobs_needing_cc_check])

    # ── Step 3: Classify each row ───────────────────────────────────────────
    for row in rows:
        # 3a) Job ID invalid
        if row.job_id in invalid_job_ids:
            invalid_rows.append({
                "job_id": row.job_id,
                "cost_centre_id": row.cost_centre_id,
                "error": f"Job ID {row.job_id} was not found in Simpro.",
            })
            continue

        # 3b) Cost Centre validation (only if row has a CC ID)
        if row.cost_centre_id is not None and row.job_id in cc_cache:
            job_ccs = cc_cache[row.job_id]
            if row.cost_centre_id not in job_ccs:
                available = [
                    f"{cc.get('Name', '?')} ({cc_id})"
                    for cc_id, cc in sorted(job_ccs.items())
                ]
                available_str = ", ".join(available[:10])
                if len(available) > 10:
                    available_str += f" ... and {len(available) - 10} more"
                invalid_rows.append({
                    "job_id": row.job_id,
                    "cost_centre_id": row.cost_centre_id,
                    "error": (
                        f"Cost Centre {row.cost_centre_id} does not exist on "
                        f"Job {row.job_id}. Available: {available_str}"
                    ),
                })
                continue

            # 3c) Check claim status (non-blocking warning)
            cc_data = job_ccs[row.cost_centre_id]
            pct_complete = cc_data.get("PercentComplete")
            if pct_complete is not None and pct_complete >= 100:
                cc_name = cc_data.get("Name", f"CC {row.cost_centre_id}")
                warnings.append(
                    f"Cost Centre '{cc_name}' ({row.cost_centre_id}) on "
                    f"Job {row.job_id} is already 100% claimed."
                )

        valid_rows.append(row)

    _inv_logger.info(
        f"✅ MCP validation: {len(valid_rows)} valid, "
        f"{len(invalid_rows)} invalid, {len(warnings)} warnings"
    )

    return {
        "valid_rows": valid_rows,
        "invalid_rows": invalid_rows,
        "warnings": warnings,
        "job_cache": job_cache,
        "cc_cache": cc_cache,
    }


def _format_validation_errors(validation: Dict[str, Any]) -> str:
    """Format MCP validation errors into a user-friendly message."""
    lines = ["Could not create invoices due to validation errors:"]
    for entry in validation.get("invalid_rows", []):
        lines.append(f"  - {entry.get('error', 'Unknown error')}")

    warnings = validation.get("warnings", [])
    if warnings:
        lines.append("")
        lines.append("Warnings:")
        for w in warnings:
            lines.append(f"  - {w}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# UPDATE / DELETE handlers
# ---------------------------------------------------------------------------

_UPDATE_SYSTEM_PROMPT = """You are an invoice update parser for a Simpro ERP system.
Extract the invoice ID and the fields to update from the user's message.

IMPORTANT: The user may refer to invoices from conversation history using phrases like
"the above invoice", "that invoice", "the one I just created", etc.
When the user message includes a CONVERSATION HISTORY section, look for invoice IDs
in entries like "invoice_id=86137" or "InvoiceID=86137" and use those IDs.

Return STRICT JSON:
{
  "invoice_ids": [<int>, ...],
  "fields_to_update": {
    // Only include fields the user explicitly wants to change.
    // Valid keys (use exact Simpro API names):
    // "Type", "DateIssued", "Stage", "PerItem", "OrderNo",
    // "Description", "Notes", "PaymentTermID", "LatePaymentFee",
    // "CISDeductionRate", "AccountingCategory", "Status",
    // "AutoAdjustStatus", "CostCenters", "Retainage"
  }
}
If you cannot determine the invoice ID, set invoice_ids to [].
If the user does not specify fields to update, set fields_to_update to {}.
Return ONLY valid JSON — no explanation outside the JSON object."""

_DELETE_SYSTEM_PROMPT = """You are an invoice delete parser for a Simpro ERP system.
Extract the invoice ID(s) the user wants to delete.

IMPORTANT: The user may refer to invoices from conversation history using phrases like
"the above invoice", "that invoice", "delete it", "the one I just created", etc.
When the user message includes a CONVERSATION HISTORY section, look for invoice IDs
in entries like "invoice_id=86137" or "InvoiceID=86137" and use those IDs.

Return STRICT JSON:
{
  "invoice_ids": [<int>, ...]
}
If you cannot determine the invoice ID(s), set invoice_ids to [].
Return ONLY valid JSON — no explanation outside the JSON object."""


async def _handle_invoice_update(
    llm_chat,
    user_text: str,
    hints: Dict[str, Any],
    mcp_executor: Optional[Any] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """
    Handle UPDATE action: parse user message for invoice_id + fields,
    return invoice_updates list for the executor.
    """
    # Include recent conversation for context (e.g., "update that invoice")
    history_section = ""
    if conversation_history:
        recent = conversation_history[-6:]
        history_lines = [f"{m.get('role', 'user')}: {m.get('content', '')[:200]}" for m in recent]
        history_section = "\nCONVERSATION HISTORY:\n" + "\n".join(history_lines) + "\n"

    user_msg = f"{history_section}\nUSER MESSAGE:\n{user_text}"

    out = llm_chat(
        [
            {"role": "system", "content": _UPDATE_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )

    try:
        parsed = json.loads(out or "{}")
    except Exception:
        return {
            "success": False,
            "error": "PARSE_FAILED",
            "message": "Could not understand the update request. Please specify the invoice ID and what to change.",
        }

    invoice_ids = parsed.get("invoice_ids") or []
    fields_to_update = parsed.get("fields_to_update") or {}

    if not invoice_ids:
        return {
            "success": False,
            "error": "NO_INVOICE_ID",
            "message": "Please specify which invoice ID you want to update.",
        }

    # Filter out null/empty values
    active_fields = {k: v for k, v in fields_to_update.items() if v is not None}
    if not active_fields:
        return {
            "success": False,
            "error": "NO_FIELDS_TO_UPDATE",
            "message": (
                "Please specify what you want to change on the invoice "
                "(e.g., stage, description, date, notes)."
            ),
        }

    _inv_logger.info(
        f"📝 Invoice UPDATE: IDs={invoice_ids}, fields={list(active_fields.keys())}"
    )

    invoice_updates = [
        {"invoice_id": inv_id, "invoice_data": active_fields}
        for inv_id in invoice_ids
    ]

    return {
        "success": True,
        "invoice_updates": invoice_updates,
        "message": f"Ready to update {len(invoice_updates)} invoice(s): fields={list(active_fields.keys())}",
    }


async def _handle_invoice_delete(
    llm_chat,
    user_text: str,
    hints: Dict[str, Any],
    mcp_executor: Optional[Any] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """
    Handle DELETE action: parse user message for invoice_id(s),
    return invoice_deletes list for the executor.
    """
    history_section = ""
    if conversation_history:
        recent = conversation_history[-6:]
        history_lines = [f"{m.get('role', 'user')}: {m.get('content', '')[:200]}" for m in recent]
        history_section = "\nCONVERSATION HISTORY:\n" + "\n".join(history_lines) + "\n"

    user_msg = f"{history_section}\nUSER MESSAGE:\n{user_text}"

    out = llm_chat(
        [
            {"role": "system", "content": _DELETE_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )

    try:
        parsed = json.loads(out or "{}")
    except Exception:
        return {
            "success": False,
            "error": "PARSE_FAILED",
            "message": "Could not understand the delete request. Please specify the invoice ID(s) to delete.",
        }

    invoice_ids = parsed.get("invoice_ids") or []

    if not invoice_ids:
        return {
            "success": False,
            "error": "NO_INVOICE_ID",
            "message": "Please specify which invoice ID(s) you want to delete.",
        }

    _inv_logger.info(f"🗑️ Invoice DELETE: IDs={invoice_ids}")

    invoice_deletes = [{"invoice_id": inv_id} for inv_id in invoice_ids]

    return {
        "success": True,
        "invoice_deletes": invoice_deletes,
        "message": f"Ready to delete {len(invoice_deletes)} invoice(s): {invoice_ids}",
    }


async def run_invoice_agent(
    user_text: str,
    extracted: Optional[Dict[str, Any]] = None,
    raw_attachments: Optional[List[Dict[str, Any]]] = None,
    any_uploaded_text: Optional[str] = None,
    hints: Optional[Dict[str, Any]] = None,
    llm_chat=None,
    sop_docx_path: Optional[str] = None,
    tracker: Optional[Any] = None,
    mcp_executor: Optional[Any] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Main entrypoint used by both CLI (src/main.py) and Chatbox backend.

    Flow:
      1) Resolve LLM and read SOP DOCX.
      2) Parse attached CSV text into ParsedRow list.
      2.5) Pre-flight validation via MCP (if executor available).
      3) Ask LLM for invoice policy + defaults (no raw ERP data).
      4) If LLM reports 'missing', bubble that up for clarification.
      5) Deterministically construct invoice bodies and POST to MCP.
      6) Return a structured JSON result for the presenter.
    """
    _agent_state = create_agent_state("invoice", user_text or "")
    _agent_state.enter_phase("parse")

    chat = llm_chat or _resolve_llm_chat()
    hints = hints or {}
    sop_path = sop_docx_path or SOP_DOCX_PATH
    sop_text = _read_docx_text(sop_path, sop_override=hints.get("sop_override"))
    _inv_logger.info(f"[SOP] Extracted text ({len(sop_text)} chars):\n{sop_text}")
    conversation_history = kwargs.get("conversation_history")

    # ── Action routing (update / delete bypass the CREATE pipeline) ──
    action = (hints.get("action") or "create").lower()
    _inv_logger.info(f"🎯 Invoice action: {action}")

    if action == "update":
        return await _handle_invoice_update(
            chat, user_text, hints, mcp_executor, conversation_history
        )

    if action == "delete":
        return await _handle_invoice_delete(
            chat, user_text, hints, mcp_executor, conversation_history
        )

    # ── CREATE flow (existing behaviour) ──

    # Prefer extractor output if provided
    if not any_uploaded_text and extracted:
        any_uploaded_text = _extracted_to_csv_text(extracted)

    # If still nothing, try chat-mode parsing (natural language → CSV)
    if not any_uploaded_text:
        _inv_logger.info("📝 No file data — attempting chat-mode invoice parsing")
        chat_result = _parse_chat_invoice_request(
            user_text, chat, hints, conversation_history
        )

        if "error" in chat_result:
            return {
                "success": False,
                "error": chat_result["error"],
                "message": chat_result.get("message", "Could not parse invoice request from chat."),
            }

        # Inject pre-resolved IDs from handoff collected_data so resolution is skipped
        pre_resolved = hints.get("pre_resolved", {})
        if pre_resolved.get("job_id"):
            for inv in chat_result.get("invoices", []):
                if not inv.get("job_id"):
                    inv["job_id"] = pre_resolved["job_id"]
                    _inv_logger.info(f"📦 Pre-resolved job_id={pre_resolved['job_id']} injected from handoff")

        # Resolve job_name / site_name → job_id via EntityResolver
        if mcp_executor:
            _agent_state.enter_phase("resolve")
            try:
                chat_result = await _resolve_chat_job_references(
                    chat_result, mcp_executor, llm_chat=chat, shared_state=_agent_state
                )
            except AmbiguousResolutionError as e:
                # Multiple jobs match — surface as clarification dropdown
                import uuid
                session_id = f"inv_{uuid.uuid4().hex[:12]}"
                return {
                    "success": False,
                    "needs_clarification": True,
                    "session_id": session_id,
                    "clarification_count": 1,
                    "clarifications": [{
                        "row": 1,
                        "type": "ambiguous",
                        "field": e.field,
                        "message": e.message,
                        "options": e.matches,
                        "operation": "CREATE",
                        "row_context": {"query": e.value},
                    }],
                    "message": e.message,
                    "resolved_count": 0,
                    "total_count": len(chat_result.get("invoices", [])),
                    "agent": "invoice",
                    "_chat_result": chat_result,  # Preserve for re-run after user selects job
                }
            except MissingFieldError as e:
                # MissingFieldError with options → show as clarification dropdown/multi-select
                # (e.g., "Job has 3 sections, please select one" or "5 cost centres available")
                is_free = hasattr(e, "context") and e.context.get("free_text", False)
                is_multi = hasattr(e, "context") and e.context.get("multi_select", False)
                opts = e.context.get("options", []) if hasattr(e, "context") else []

                if is_free or opts:
                    import uuid
                    session_id = f"inv_{uuid.uuid4().hex[:12]}"
                    clar_type = "free_text" if is_free else ("multi_select" if is_multi else "missing")
                    clar = {
                        "row": 1,
                        "type": clar_type,
                        "field": e.field,
                        "message": e.message,
                        "options": opts,
                        "operation": "CREATE",
                        "row_context": {},
                    }
                    if is_free:
                        clar["placeholder"] = e.context.get("placeholder", e.field)
                    return {
                        "success": False,
                        "needs_clarification": True,
                        "session_id": session_id,
                        "clarification_count": 1,
                        "clarifications": [clar],
                        "message": e.message,
                        "resolved_count": 0,
                        "total_count": len(chat_result.get("invoices", [])),
                        "agent": "invoice",
                        "_chat_result": chat_result,
                    }
                return {
                    "success": False,
                    "error": "RESOLUTION_FAILED",
                    "message": str(e),
                }
            except BatchedClarificationError as batch_e:
                # Multiple independent fields need clarification at once
                import uuid
                session_id = f"inv_{uuid.uuid4().hex[:12]}"
                clarifications_list = []
                for inner in batch_e.errors:
                    if isinstance(inner, AmbiguousResolutionError):
                        clarifications_list.append({
                            "row": 1,
                            "type": "ambiguous",
                            "field": inner.field,
                            "message": inner.message,
                            "options": inner.matches,
                            "operation": "CREATE",
                            "row_context": {"query": inner.value},
                        })
                    elif isinstance(inner, MissingFieldError):
                        is_free = inner.context.get("free_text", False)
                        is_multi = inner.context.get("multi_select", False)
                        options = inner.context.get("options", [])
                        if is_free:
                            clarifications_list.append({
                                "row": 1,
                                "type": "free_text",
                                "field": inner.field,
                                "message": inner.message,
                                "placeholder": inner.context.get("placeholder", inner.field),
                                "options": [],
                                "operation": "CREATE",
                                "row_context": {},
                            })
                        elif options:
                            clarifications_list.append({
                                "row": 1,
                                "type": "multi_select" if is_multi else "missing",
                                "field": inner.field,
                                "message": inner.message,
                                "options": options,
                                "operation": "CREATE",
                                "row_context": {},
                            })
                return {
                    "success": False,
                    "needs_clarification": True,
                    "session_id": session_id,
                    "clarification_count": len(clarifications_list),
                    "clarifications": clarifications_list,
                    "message": f"{len(clarifications_list)} fields need clarification",
                    "resolved_count": 0,
                    "total_count": len(chat_result.get("invoices", [])),
                    "agent": "invoice",
                    "_chat_result": chat_result,
                }
            except ResolutionError as e:
                return {
                    "success": False,
                    "error": "RESOLUTION_FAILED",
                    "message": str(e),
                }

        any_uploaded_text = _chat_result_to_csv(chat_result)
        _inv_logger.info("✅ Chat-mode: converted user text to CSV for pipeline")


    # 1) Parse attachment — with proactive sheet comprehension
    raw_rows: List[Dict[str, Any]] = (
        list(csv.DictReader(io.StringIO(any_uploaded_text))) if any_uploaded_text else []
    )
    _sheet_schema: Optional[SheetSchema] = None
    rows: List["ParsedRow"] = []

    if raw_rows:
        _headers = list(raw_rows[0].keys())
        _unrecognised = [h for h in _headers if _normalize_key(h) not in _ALL_KNOWN_HEADER_KEYS]

        if _unrecognised:
            _inv_logger.info(
                f"[InvoiceAgent] {len(_unrecognised)} unrecognised headers — "
                f"running sheet comprehension"
            )
            try:
                _sheet_schema = _comprehend_sheet(raw_rows, _headers, chat)
                _inv_logger.info(
                    f"[InvoiceAgent] SheetSchema confidence={_sheet_schema.confidence:.2f}, "
                    f"warnings={_sheet_schema.warnings}"
                )
            except Exception as _exc:
                _inv_logger.warning(f"[InvoiceAgent] Sheet comprehension failed: {_exc} — fallback")
                _sheet_schema = _fallback_schema(_headers)

            if (
                _sheet_schema.confidence >= 0.5
                and (_sheet_schema.field_map or _sheet_schema.row_filter or _sheet_schema.value_transforms)
            ):
                _processed_csv = _apply_sheet_schema(raw_rows, _sheet_schema)
                rows = _parse_attachment_csv(_processed_csv)
            else:
                rows = _parse_attachment_csv(any_uploaded_text)
        else:
            rows = _parse_attachment_csv(any_uploaded_text)
    elif any_uploaded_text:
        rows = _parse_attachment_csv(any_uploaded_text)

    # Last resort: still no rows → ask user which column is the job number
    if not rows and raw_rows:
        _job_col_options = [{"id": h, "name": h} for h in list(raw_rows[0].keys())]
        raise MissingFieldError(
            field="JobID",
            message=(
                "I couldn't identify which column contains the job number. "
                "Which column is it?"
            ),
            options=_job_col_options,
            multi_select=False,
        )

    if not rows:
        return {
            "success": False,
            "error": "NO_ROWS_PARSED",
            "details": "Could not parse any JobID rows from the attached data.",
            "message": "Could not parse any JobID rows from the attached data. Please ensure your Excel file has a 'JobID' column with valid job IDs.",
        }

    # 1.1) Name column resolution — resolve text names → Simpro IDs
    _agent_state.complete_phase("parse", detail=f"{len(rows)} rows")
    if _sheet_schema and _sheet_schema.name_resolution_columns and mcp_executor:
        _agent_state.enter_phase("resolve")
        try:
            rows = await _resolve_name_columns(rows, _sheet_schema, mcp_executor, chat, shared_state=_agent_state)
        except AmbiguousResolutionError as e:
            import uuid as _uuid
            _sid = f"inv_{_uuid.uuid4().hex[:12]}"
            return {
                "success": False, "needs_clarification": True,
                "session_id": _sid, "clarification_count": 1,
                "clarifications": [{
                    "row": 1, "type": "ambiguous", "field": e.field,
                    "message": e.message, "options": e.matches,
                    "operation": "CREATE", "row_context": {"query": e.value},
                }],
                "message": e.message, "resolved_count": 0,
                "total_count": len(rows), "agent": "invoice",
            }
        except MissingFieldError as e:
            _is_free = e.context.get("free_text", False)
            _is_multi = e.context.get("multi_select", False)
            _opts = e.context.get("options", [])
            if _is_free or _opts:
                import uuid as _uuid
                _sid = f"inv_{_uuid.uuid4().hex[:12]}"
                _clar: Dict[str, Any] = {
                    "row": 1,
                    "type": "free_text" if _is_free else ("multi_select" if _is_multi else "missing"),
                    "field": e.field, "message": e.message,
                    "options": _opts, "operation": "CREATE", "row_context": {},
                }
                if _is_free:
                    _clar["placeholder"] = e.context.get("placeholder", e.field)
                return {
                    "success": False, "needs_clarification": True,
                    "session_id": _sid, "clarification_count": 1,
                    "clarifications": [_clar],
                    "message": e.message, "resolved_count": 0,
                    "total_count": len(rows), "agent": "invoice",
                }
            return {"success": False, "error": "RESOLUTION_FAILED", "message": str(e)}
        except BatchedClarificationError as _batch_e:
            import uuid as _uuid
            _sid = f"inv_{_uuid.uuid4().hex[:12]}"
            _clars = _batch_error_to_clarifications(_batch_e)
            return {
                "success": False, "needs_clarification": True,
                "session_id": _sid, "clarification_count": len(_clars),
                "clarifications": _clars,
                "message": f"{len(_clars)} fields need clarification",
                "resolved_count": 0, "total_count": len(rows), "agent": "invoice",
            }
        except ResolutionError as e:
            return {"success": False, "error": "RESOLUTION_FAILED", "message": str(e)}

    # 1.5) Pre-flight validation via MCP (if executor available)
    if mcp_executor:
        try:
            validation = await _validate_with_mcp(rows, mcp_executor)
            if validation.get("invalid_rows"):
                return {
                    "success": False,
                    "error": "VALIDATION_FAILED",
                    "invalid_entries": validation["invalid_rows"],
                    "warnings": validation.get("warnings", []),
                    "message": _format_validation_errors(validation),
                }
            rows = validation.get("valid_rows", rows)
            if validation.get("warnings"):
                _inv_logger.info(f"⚠️ MCP validation warnings: {validation['warnings']}")
        except Exception as e:
            # Graceful degradation: if MCP validation fails, continue without it
            _inv_logger.warning(f"⚠️ MCP validation failed, continuing without: {e}")

    # 2) Build a compact summary for LLM
    attachment_summary = _build_attachment_summary(rows)

    # 2.5) If SOP is empty, use code-level defaults and ask user to confirm
    if not sop_text.strip():
        _inv_logger.warning("⚠️ SOP document not found or empty — using code-level defaults")
        from datetime import datetime as _dt
        import uuid

        fallback_policy = {
            "invoice_mode": "per_job",
            "per_item": False,
            "description_mode": "combine",
            "description_joiner": ";",
            "defaults": {
                "Type": "ProgressInvoice",
                "Stage": "Approved",
                "DateIssued": _dt.now().strftime("%Y-%m-%d"),
                "CompanyID": None,
            },
            "missing": [],
        }

        session_id = f"inv_{uuid.uuid4().hex[:12]}"
        return {
            "success": False,
            "needs_clarification": True,
            "session_id": session_id,
            "clarification_count": 1,
            "clarifications": [{
                "row": 1,
                "type": "confirmation",
                "field": "FallbackDefaults",
                "message": (
                    "SOP document not available. The system will use these default settings:\n"
                    "• Invoice Type: ProgressInvoice\n"
                    "• Stage: Approved\n"
                    "• PerItem: False (invoice by cost centre total)\n"
                    "• Grouping: per_job (one invoice per job)\n"
                    "• Date: Today\n\n"
                    "Would you like to proceed with these defaults?"
                ),
                "options": [
                    {"id": "yes", "name": "Yes, proceed with these defaults"},
                    {"id": "no", "name": "No, I need to change some settings"},
                ],
                "operation": "CREATE",
                "row_context": {
                    "source": "code_defaults (SOP not found)",
                },
            }],
            "message": "SOP document not available. Please confirm the default settings before proceeding.",
            "resolved_count": len(rows),
            "total_count": len(rows),
            "original_extracted": extracted,
            "_policy": fallback_policy,
            "_any_uploaded_text": any_uploaded_text,
        }

    # 3) Ask LLM for policy + defaults
    _agent_state.enter_phase("policy")
    policy = _llm_plan_policy(chat, user_text, sop_text, attachment_summary, conversation_history)
    missing = policy.get("missing") or []

    if missing:
        # Do NOT build any invoices yet; ask the chat layer to clarify first.
        return {
            "success": False,
            "needs_clarification": True,
            "questions": missing,
            "message": "I need some clarification before creating invoices:\n" + "\n".join(f"• {q}" for q in missing),
            "policy": policy,
        }

    # 3b) Detect SOP deviations — ask the user before proceeding
    deviations = _detect_sop_deviations(rows, policy)
    if deviations:
        import uuid
        session_id = f"inv_{uuid.uuid4().hex[:12]}"
        return {
            "success": False,
            "needs_clarification": True,
            "session_id": session_id,
            "clarification_count": len(deviations),
            "clarifications": deviations,
            "message": "Before creating invoices, please confirm these settings:",
            "resolved_count": len(rows),
            "total_count": len(rows),
            # Internal data preserved for session resume
            "original_extracted": extracted,
            "_policy": policy,
            "_any_uploaded_text": any_uploaded_text,
        }

    # 4) Build invoice bodies + POST to MCP
    _agent_state.complete_phase("policy")
    _agent_state.enter_phase("execute")
    result = _build_invoice_bodies(rows, policy, hints)

    # 5) Attach trace info for observability
    if isinstance(result, dict):
        trace = result.setdefault("trace", {})
        trace.update(
            {
                "sop_docx_used": bool(sop_path),
                "hints_used": bool(hints),
            }
        )

    _agent_state.complete_phase("execute", detail=f"{len(rows)} rows")
    logger.info(_agent_state.summary())
    return result