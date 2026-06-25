# Purchase Order SOP
<!-- VERSION: 1.0 — Replace this file per customer deployment to change agent behaviour -->

## Purpose
This SOP defines the business rules, defaults, and validation logic for creating,
updating, and deleting purchase/supplier/material orders in Simpro.

The Purchase Order Agent reads this document at runtime. **All business rules live
here, not in code.** To customise the agent for a different customer, replace or
edit this file and restart the agent — no code changes required.

---

## Scope
Applies to all purchase order operations performed through the Purchase Order Agent,
including single-line requests, bulk file uploads, and chat-based operations.

---

## 1. Operation Types

| Operation | Requires |
|-----------|----------|
| CREATE    | Supplier + at least one line item + Job or Stock location |
| UPDATE    | PurchaseOrderID + fields to change |
| DELETE    | PurchaseOrderID + explicit user confirmation |

---

## 2. Required Fields

### For CREATE
- **Supplier** — name or ID (resolved via fuzzy match if name given)
- **Job** (or stock/inventory location if not job-costed)
- **Section** — defaults to first section on the job if only one exists
- **CostCentre** — defaults to first cost centre if only one exists; prompt if multiple
- **LineItems** — at least one item with: Description, Quantity, UnitCost
- **OrderDate** — defaults to today if not specified (YYYY-MM-DD)
- **TaxCodeID** — taken from SOP default below; can be overridden per line

### For UPDATE
- **PurchaseOrderID** — Simpro PO ID
- Any fields to change (supplier, date, line items, status)

### For DELETE
- **PurchaseOrderID**
- Explicit user confirmation before execution (never silent delete)

---

## 3. Defaults
<!-- Customer-specific — edit these values for each deployment -->

```
default_tax_code_id:    1          # Simpro tax code ID for standard GST / VAT
default_order_status:   "Draft"    # "Draft" | "Approved" | "Ordered" | "Received"
default_currency:       "AUD"
require_job_reference:  true       # If false, allow stock-only POs
auto_approve_threshold: null       # Dollar amount below which PO auto-approves (null = never)
description_format:     "itemized" # "itemized" | "summary"
include_po_notes:       true       # Include free-text notes field on the PO
```

---

## 4. Supplier Resolution Rules

1. If SupplierID is provided directly, use it (skip fuzzy match).
2. If SupplierName is given, fuzzy-match against the Simpro supplier/contact list.
3. If match score ≥ 70 AND lead over second match > 15 → auto-select silently.
4. If ambiguous (two suppliers with similar scores) → present clarification form to user.
5. If no match found → return error `SUPPLIER_NOT_FOUND`; do not proceed.

---

## 5. Line Item Rules

- Quantity must be a positive number (decimals allowed, e.g. 2.5 metres).
- UnitCost must be ≥ 0 (zero-cost items allowed for tracking purposes).
- PartNumber / catalogue number is optional but stored if provided.
- Type must be one of: `Material`, `Labour`, `OneOff`.
  - Default type: `Material` if not specified.
- Tax code defaults to `default_tax_code_id` unless overridden per line.
- Items marked `Include = No` in the review Excel are skipped during creation.

---

## 6. Job / Cost Centre Association

- All PO line items must be associated with a Job + Section + CostCentre
  **unless** `require_job_reference` is false (stock-only POs).
- Section resolution:
  - If only one section on the job → auto-select silently.
  - If multiple sections → prompt user unless SectionName/SectionID is given.
- CostCentre resolution:
  - If only one cost centre in section → auto-select silently.
  - If multiple cost centres → prompt user unless CostCentreName/CostCentreID is given.

---

## 7. Validation Rules

1. **OrderDate**: Must be a valid date (YYYY-MM-DD). Defaults to today.
2. **Quantity**: Must be > 0.
3. **UnitCost**: Must be ≥ 0.
4. **Supplier**: Must exist in Simpro (validated before submission).
5. **Job**: Must exist and be active (not archived / closed).
6. **Duplicate detection**: Warn (but do not block) if a PO from the same supplier
   for the same job already exists with status "Draft" or "Approved".

---

## 8. Two-Phase Workflow (File Upload)

When the user uploads a spreadsheet containing PO line items:

**Phase A — Prepare (Review)**
- Parse the spreadsheet.
- Resolve all entity names (supplier, job, section, cost centre) using fuzzy match.
- Collect any ambiguous matches as clarifications.
- Return a downloadable review Excel with:
  - Resolved IDs pre-filled.
  - An `Include` column (default "Yes") for user to edit.
  - Flagged rows marked with a note if unresolved.

**Phase B — Create**
- Accept the re-uploaded (edited) Excel.
- Skip rows marked `Include = No`.
- Submit approved rows to Simpro via MCP tools.
- Return a result summary with created PO IDs.

---

## 9. Clarification Batching

- Independent fields (e.g., Supplier + Job) are asked together in a single form.
- Dependent fields (Section waits for Job, CostCentre waits for Section) are deferred.
- Maximum interactive clarification rounds: 5 (configurable via `MAX_CLARIFICATIONS`).
- If > 5 rows need clarification → generate a pre-filled Excel instead of a form.

---

## 10. DELETE Confirmation Gate

Every DELETE must:
1. Display to the user: Supplier name, Job ID, Order date, Total value, PO ID.
2. Require explicit "Yes, delete this PO" confirmation.
3. Never delete silently or in batch without per-PO confirmation.

---

## 11. UPDATE Rules

- Partial updates are supported: only specified fields are changed.
- Changing the supplier on a PO requires re-validation of supplier ID.
- Adding/removing line items is treated as a PATCH to the line-item collection.
- If the PO status is "Received" or "Invoiced", block updates and return:
  `CANNOT_UPDATE_RECEIVED_PO` with a human-readable message.

---

## 12. Error Codes

| Code | Meaning |
|------|---------|
| `SUPPLIER_NOT_FOUND` | No fuzzy match for the given supplier name |
| `JOB_NOT_FOUND` | No active job matches the given name/ID |
| `SECTION_NOT_FOUND` | Section not found under the resolved job |
| `COST_CENTRE_NOT_FOUND` | Cost centre not found under the resolved section |
| `NO_LINE_ITEMS` | At least one line item is required |
| `CANNOT_UPDATE_RECEIVED_PO` | PO is in Received/Invoiced state |
| `DELETE_CANCELLED` | User declined the delete confirmation |
| `AMBIGUOUS_SUPPLIER` | Multiple suppliers matched with similar scores |

---

## 13. PO Grouping Strategy

Controls how line items are bucketed into purchase orders at creation time.
The unit of a purchase order is **supplier × grouping key** — all items with
the same supplier AND the same grouping key go into one PO.

```
po_grouping: "per_cost_centre"
```

| Value | Meaning |
|-------|---------|
| `per_cost_centre` | One PO per (supplier × cost centre). Default. |
| `per_job`         | One PO per (supplier × job), spanning all cost centres on that job. |
| `per_schedule`    | One PO per (supplier × schedule), one PO per schedule entry. |

The `POGroup` column in the review Excel is auto-populated by the agent using
this rule. Users may override individual `POGroup` cell values in the Excel to
force rows onto the same or different PO — any two rows with the same
`POGroup` value and the same supplier will be combined into one PO.

---

## 14. Supplier Assignment Behaviour

```
labour_supplier: null
upsert_existing_po: true
```

- **`labour_supplier`**: Supplier to pre-fill on Labour-type line items.
  Set to `null` to leave Labour rows blank (user must assign manually).
  Set to a supplier name string (e.g. `"ABC Labour Hire"`) to auto-fill.

- **`upsert_existing_po`**: When `true`, before creating a new PO the agent
  checks if an open PO from the same supplier already exists for the same
  grouping key (cost centre / job / schedule).
  - If an open PO is found with Stage `Pending` or `Approved` → **update it**
    by adding the new line items (PATCH).
  - If no open PO exists → **create** a new one (POST).
  - Set to `false` to always create a new PO regardless of existing ones.

---

## 15. Blank PO Rules

A blank PO has no line items — it is used as a placeholder to be filled in
later (e.g. raise a PO number for a verbal order before items are confirmed).

```
blank_po_allowed: true
blank_po_stage: "Pending"
```

- **`blank_po_allowed`**: Set to `false` to block blank PO creation entirely.
- **`blank_po_stage`**: Default stage for blank POs. Must be `Pending` or
  `Approved` (Archived/Voided are not valid for blank POs as receipts cannot
  be created against them).

When a user asks to "raise a blank PO" or "create a PO with no items", the
agent skips the line-item requirement and creates the PO with the supplier,
job/cost centre, and date only.

---

## Revision History

| Version | Date       | Change |
|---------|------------|--------|
| 1.1     | 2026-03-22 | Added Sections 13–15: PO grouping strategy, supplier assignment behaviour, blank PO rules |
| 1.0     | 2026-03-22 | Initial SOP — covers CREATE, UPDATE, DELETE with two-phase file upload |
