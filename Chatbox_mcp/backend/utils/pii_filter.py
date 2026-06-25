# backend/utils/pii_filter.py
# ──────────────────────────────────────────────────────────────
# Allowlist-based PII filter for data sent to external LLMs.
#
# DESIGN:
#   - Fields NOT in the allowlist are stripped automatically.
#   - Unknown / new Simpro fields are blocked by default (safe).
#   - The original payload is NEVER mutated; a deep copy is returned.
#   - Only used before LLM calls — frontend tables still show full data.
#   - Financial values are MASKED: raw dollar amounts are replaced
#     with relative metrics (percentages, statuses) so the LLM can
#     generate meaningful summaries without seeing actual figures.
#
# To add a new safe field, add it to SAFE_FIELDS below.
# ──────────────────────────────────────────────────────────────
from __future__ import annotations
import os
from typing import Any, Dict

# Toggle: set PII_FILTER_ENABLED=false in .env to disable for demos
PII_FILTER_ENABLED = os.getenv("PII_FILTER_ENABLED", "true").lower() not in ("false", "0", "no")

# ---- Allowlisted fields (case-insensitive match) ----
# Structural / ID fields the LLM needs for reasoning
_SAFE_FIELDS_RAW = {
    # Identifiers
    "ID", "Type", "Status", "Stage",
    "CustomerID", "JobID", "CostCentreID", "SectionID",
    "InvoiceID", "QuoteID", "ItemID", "PaymentTermID",
    "CompanyID", "ContractorID", "ScheduleID",

    # Business fields (non-PII — labels and dates only, NOT dollar values)
    "CompanyName", "Name", "DisplayName",
    "DateIssued", "DueDate", "DateCreated", "DateModified",
    "TaxCode", "OrderNo", "InvoiceType",
    "Description", "Notes",

    # Structural / percentage fields (safe — no dollar amounts)
    "PercentComplete", "DisplayOrder",
    "CostCenter", "CostCentre",
    "InvoicePercentage", "Percent",
    "STCsEligible", "VEECsEligible",

    # Structural envelope keys (agent results, presenter, etc.)
    "success", "error", "message", "summary",
    "created", "failed", "skipped",
    "count", "total", "Totals", "results", "data",
    "jobs", "records", "rows", "items",
    "cost_centres", "sections",
    "Claimed", "ToDate", "Remaining", "Amount",
    "agent_output", "creation_result",
    "request", "response", "body",
    "Jobs", "CostCenters", "Items", "Sections",
    "PerItem", "per_item", "invoice_mode",

    # Invoice structural fields
    "invoice", "detail", "success_rate",
    "policy", "job_results", "trace",
    "invoice_mode", "desc_mode", "desc_joiner",
    "defaults", "missing",

    # Financial summary (flat pre-computed metrics from MCP tools)
    "_financial_summary",
    "Budget_Status",
    "Gross_Margin_Pct_Actual", "Gross_Margin_Pct_Estimate",
    "Net_Margin_Pct_Actual", "Net_Margin_Pct_Estimate",

    # Schedule fields (minimal — enough for LLM to generate accurate summaries)
    "Date", "Staff", "StaffName",
    "TotalHours", "Reference", "Blocks",
    "schedules", "date_from", "date_to", "type_filter",
}

# Pre-compute lowercase set for fast lookup
_SAFE_LOWER = {f.lower() for f in _SAFE_FIELDS_RAW}

# ---- Financial field keys whose VALUES must be masked ----
# These fields are kept in the output (so LLM knows the structure)
# but their numeric values are replaced with relative metrics.
_FINANCIAL_KEYS_LOWER = {
    # Dollar-amount fields
    "total", "totalextax", "totalinctax",
    "extax", "inctax", "tax",
    "quantity", "unitpriceextax", "claimextax", "claiminctax",
    "claim",
    # Cost breakdown categories
    "actual", "estimate", "revised", "committed",
    "materials", "resources", "labor", "plant", "overhead",
    "commission", "markup", "miscellaneous",
    # Profitability
    "grossprofit", "grossprofitloss", "grossmargin",
    "netprofit", "netprofitloss", "netmargin",
    "adjustedtotal", "discount", "membershipdiscount",
    "invoicedvalue",
    "stcs", "veecs", "stcvalue", "veecvalue",
    "hours", "cost",
    # Flat financial summary keys (dollar amounts — masked when PII on)
    "total_extax", "total_inctax",
    "materials_cost_actual", "materials_cost_estimate",
    "labour_cost_actual", "labour_cost_estimate",
    "labour_hours_actual", "labour_hours_estimate",
    "plant_equipment_cost_actual",
    "overhead_cost_actual", "overhead_cost_estimate",
    "gross_profit_actual", "gross_profit_estimate",
    "net_profit_actual", "net_profit_estimate",
}


def _is_safe_key(key: str) -> bool:
    """Check if a dict key is allowed to pass through to the LLM."""
    k = key.lower()
    if k in _SAFE_LOWER:
        return True
    if k in _FINANCIAL_KEYS_LOWER:
        return True  # allowed through, but value will be masked
    if k.endswith("id"):
        return True
    return False


def _is_financial_key(key: str) -> bool:
    """Check if a key holds a financial/monetary value that must be masked."""
    return key.lower() in _FINANCIAL_KEYS_LOWER


def _mask_value(value: Any) -> Any:
    """Replace a single financial value with a masked indicator."""
    if value is None or value == "" or value == 0:
        return 0
    if isinstance(value, (int, float)):
        if value > 0:
            return "positive"
        return "negative"
    return value  # non-numeric → keep as-is


def _mask_financial_pair(obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    If a dict has both 'Actual' and 'Estimate' (common Simpro pattern),
    replace raw values with a relative comparison.
    Works for any case combination.
    """
    lower_map = {k.lower(): k for k in obj}
    has_actual = "actual" in lower_map
    has_estimate = "estimate" in lower_map

    if has_actual and has_estimate:
        actual_key = lower_map["actual"]
        estimate_key = lower_map["estimate"]
        actual_val = obj.get(actual_key)
        estimate_val = obj.get(estimate_key)

        masked = {}
        for k, v in obj.items():
            kl = k.lower()
            if kl == "actual" or kl == "estimate":
                continue  # replaced below
            elif _is_financial_key(k):
                masked[k] = _mask_value(v)
            else:
                masked[k] = v

        # Add relative comparison instead of raw values
        if isinstance(actual_val, (int, float)) and isinstance(estimate_val, (int, float)):
            if actual_val < 0:
                # Negative actual = loss/deficit (universal — works for costs AND profits)
                masked["ActualVsEstimate"] = "negative"
                masked["BudgetStatus"] = "loss"
            elif estimate_val != 0:
                pct = round((actual_val / estimate_val) * 100, 1)
                if pct < 80:
                    status = "well_under_estimate"
                elif pct < 100:
                    status = "under_estimate"
                elif pct == 100:
                    status = "on_estimate"
                elif pct < 120:
                    status = "over_estimate"
                else:
                    status = "well_over_estimate"
                masked["ActualVsEstimate"] = f"{pct}%"
                masked["BudgetStatus"] = status
            elif actual_val == 0:
                masked["ActualVsEstimate"] = "0%"
                masked["BudgetStatus"] = "no_activity"
            else:
                masked["ActualVsEstimate"] = "no_estimate"
                masked["BudgetStatus"] = "unbudgeted"
        else:
            masked["ActualVsEstimate"] = "unknown"

        return masked

    # No Actual/Estimate pair — just mask individual financial values
    return {
        k: (_mask_value(v) if _is_financial_key(k) else v)
        for k, v in obj.items()
    }


def sanitize_for_llm(payload: Any) -> Any:
    """
    Return a deep copy of *payload* with:
    1. Non-allowlisted fields removed
    2. Financial values masked (replaced with relative metrics)

    The LLM sees structure and relationships but NEVER raw dollar amounts.
    The original payload is never mutated.

    Set PII_FILTER_ENABLED=false in .env to disable (e.g. for demos).
    """
    if not PII_FILTER_ENABLED:
        return payload

    if isinstance(payload, dict):
        # Step 1: keep only allowed keys, recurse into values
        filtered = {}
        for k, v in payload.items():
            if not _is_safe_key(k):
                continue
            filtered[k] = sanitize_for_llm(v)

        # Step 2: mask financial values (Actual/Estimate pairs, etc.)
        return _mask_financial_pair(filtered)

    if isinstance(payload, list):
        return [sanitize_for_llm(item) for item in payload]

    # Scalar (str, int, float, bool, None)
    return payload
