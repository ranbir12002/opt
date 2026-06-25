1. Purpose

This document explains how the Invoice Creation Agent works.
It describes what the agent should do, what inputs it expects, and how it should behave in different scenarios such as errors, missing data, or edge cases.

The goal is to make sure invoices are created correctly in Simpro (and later other systems like MYOB) without needing any manual corrections.

2. Scope

The agent applies to all Simpro tenants connected through the MCP.

It handles invoice creation only (not updates, payments, or deletions).

Future versions may include approval and idempotency rules automatically.

3. Input Rules — replace this block

Modes of input

The agent accepts either of the following modes for each JobID group:

Per-item mode (legacy): uses line Qty and UnitPriceEx (and optional DiscountPct).

Claim-based mode: uses exactly one of the following per CostCentreID:

ClaimPercent (percentage of the cost centre total),

ClaimExTax (absolute amount excluding tax), or

ClaimIncTax (absolute amount including tax).

Mandatory columns (all modes):
CompanyID, InvoiceType, JobID, DateIssued, Stage, PerItem, CostCentreID

Additional requirements by mode:

Per-item mode: Qty, UnitPriceEx are required (DiscountPct optional).

Claim-based mode: exactly one of ClaimPercent / ClaimExTax / ClaimIncTax must be present with a value for each cost centre. Qty/UnitPriceEx are not required in this mode.

Mixing rule
Do not mix per-item and claim-based inputs within the same JobID. If both appear for a JobID, the group fails with a clear error.

PerItem flag
When using claim-based mode, PerItem must be False (claim blocks are not supported for consolidated invoices).

Optional columns (unchanged):
ItemCode, LineDescription, TaxCode, DiscountPct, PaymentTermID, OrderNo, Description, Notes, ProgressClaimNumber, Reference, CCTotalEx

Other behaviors in this section remain unchanged (AU date defaulting, numeric cleaning, etc.). 

4. Grouping Logic

The default grouping is by JobID.

If there are multiple invoices for the same JobID, the agent will use the Reference column to create separate invoices.

If there is no Reference, all lines with the same JobID will be combined into one invoice.

5. Invoice Creation Rules

Each JobID group becomes one invoice.

Cost centres inside the invoice are based on the “CostCentreID” column.

The agent supports two modes of invoice creation per job group — Per-Item and Claim-Based:

 a. Per-Item Mode (default)
 • Used when the input contains Qty and UnitPriceEx columns and no Claim fields.
 • For each CostCentreID, the agent totals all line values using:
  Qty × UnitPriceEx × (1 – DiscountPct / 100)
 • If the user also supplies CCTotalEx, the agent compares it to the computed total.
  – If the difference ≤ ₹0.01 → accept.
  – If the difference > ₹0.01 → hard-fail the group.

 b. Claim-Based Mode
 • Used when the input contains one (and only one) of these columns:
  ClaimPercent, ClaimExTax, or ClaimIncTax.
 • Per-Item line columns (Qty, UnitPriceEx) are ignored in this mode.
 • For each CostCentreID, the agent builds a Claim object as follows:
  – If ClaimPercent → Claim: { "Percent": value }
  – If ClaimExTax → Claim: { "ExTax": value }
  – If ClaimIncTax → Claim: { "IncTax": value }
 • Exactly one claim field must have a value per cost centre.
 • PerItem must be False for claim-based mode (claim blocks are not supported for consolidated invoices).
 • If more than one claim field is present or if claim values are missing/invalid, the job group fails with an error.

A JobID group may use either Per-Item or Claim-Based mode — never both. If mixed columns appear, the group fails with “Mixed claim/per-item inputs not allowed.”

Stage defaults to Approved unless the SOP specifies otherwise.

All other invoice-header fields (OrderNo, Description, Notes, etc.) follow existing rules.

6. Batching & Performance

The agent processes invoices in batches.

Each batch contains up to 50 invoices (this value can be changed in YAML).

If more than 50 invoices are submitted, the agent continues processing remaining batches automatically until all are complete.

The agent never stops after 50; it continues streaming results.

7. Progress Claim Rules

1. When a job uses ProgressInvoice type, the agent checks the remaining claimable amount through MCP.

If the remaining amount is known and the current claim (Percent / ExTax / IncTax) exceeds it, the agent will either:
 • Cap the claim to the remaining value, or
 • Fail the group — depending on the configuration in YAML.

If the remaining amount cannot be retrieved from MCP, the agent fails the group and reports “Remaining not available.”

When ClaimPercent is used, the value must be between 0 and 100 inclusive.

Negative or non-numeric ClaimExTax / ClaimIncTax values cause an immediate fail.

8. Idempotency (Duplicate Handling)

Each invoice creation request is checked for duplication using an external key built from:
 CompanyID + InvoiceType + JobID + DateIssued + Stage + PerItem + Reference + TotalExSum

If the same key is found in the MCP idempotency store:
 • If policy = “return existing” → fetch and return the existing invoice.
 • If policy = “fail” → stop and report duplication.

This behavior is set in the YAML rule idempotency.behavior.on_duplicate.

9. Error Handling

Network or temporary Simpro errors → retry up to 3 times.

4xx errors (like invalid data) → fail immediately with a detailed message.

429 (rate limit) → retry with exponential backoff.

Timezone for all operations → Australia/Melbourne.

If an invoice cannot be created due to invalid cost centre or job, the group fails but others continue.

10. Agent Output

The agent does not format data or generate tables.

It returns structured JSON with two main arrays:

successes: all successfully created invoices.

failures: all groups that failed, with error codes and reasons.

Presentation (cards, tables, summaries) happens in the chat layer.

11. Edge Cases and Expected Behaviors

12. Configuration Section (for LLM to extract)

Each of the following statements describes how the agent should behave.
The LLM uses these to generate YAML automatically.

Batch Size → 50 invoices per run
Batch Streaming → True
Tolerance Value → ₹0.01
Tolerance Policy → Hard fail group
Default Stage → Approved
Default Invoice Type → ProgressInvoice
Default PerItem → False
Default CompanyID → 2
Time Zone → Australia/Melbourne
Remaining Claim Enforcement → True
Idempotency on Duplicate → Return existing
HTTP Timeout (seconds) → 10
HTTP Retries → 3
Output Format → Structured JSON only

Claim Input Policy → one of (Percent, ExTax, IncTax); exactly one per CostCentre
Claim Requires PerItem → False

13. Version and Metadata

Version: 1.1.0

Last Updated: 30/10/2025

Maintainer: Optificial.AI – Invoice Automation Team

Applies To: All Simpro-connected companies