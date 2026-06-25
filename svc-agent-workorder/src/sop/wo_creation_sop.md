Work Order (Contractor Job) Creation SOP

1. Purpose

This document describes the standard operating procedure for the Work Order (Contractor Job) Creation Agent. It defines all defaults, business rules, department mappings, item inclusion policies, description format, and contractor job creation process for Simpro ERP.

The agent uses this SOP as the primary source of truth. All configurable values, organisation-specific rules, and field defaults are extracted by the LLM from this document. No defaults should be invented outside of this SOP.

2. Scope

This SOP applies to all Simpro-connected companies using the Work Order Agent. The agent creates contractor jobs via POST /costCenters/{ccID}/contractorJobs/. It handles two phases:

Phase A (Prepare): Fetch materials and labour from cost centres, generate a downloadable CSV for user review.

Phase B (Create): Process the re-uploaded (edited) CSV, build contractor job payloads, and create contractor jobs in Simpro.

3. Default Values

The following defaults apply to all contractor jobs unless overridden by the user or specific business rules:

4. Department Mapping

Departments are resolved via cost centre types in Simpro. Each cost centre type has a Name and an optional IncomeAccountNo field. The following mapping defines known department-to-income-account associations:

If a department cannot be resolved via IncomeAccountNo, the agent will attempt a fuzzy name match against cost centre type names returned from the API. If still ambiguous, the agent will ask the user for clarification.

5. Item Inclusion Rules

When fetching items from a cost centre for the work order review sheet, the following rules apply:

Catalog Items (Materials/Parts): Include by default. These are materials and parts from the Simpro catalog assigned to the cost centre.

Labour Items: Include by default. These represent labour hours and rates assigned to the cost centre.

One-Off Items: Include by default. These are custom/one-off items not in the catalog.

Exclusion Patterns

The following item name patterns should be excluded from the work order review sheet (case-insensitive matching):

No exclusion patterns defined. All items are included by default.

Note: Users can always exclude individual items by setting the Include column to No in the downloaded CSV.

6. Contractor Job Payload Construction

For each group of items (grouped by JobID + SectionID + CostCentreID + ContractorID), the agent constructs a contractor job payload as follows:

7. Description Format Rules

The Description field in the contractor job payload is built from the included items. Two formats are supported:

Itemized Format (Default)

Each included item is listed on a separate line with quantity and total:

- Item Name (Qty: X, Total: $Y.YY)

- Another Item (Qty: X, Total: $Y.YY)

This format provides full visibility into what materials and labour are included in the contractor job. Recommended for most use cases.

Summary Format

A compact summary showing totals by type:

Materials: N items ($X.XX), Labour: N items ($X.XX)

Use summary format when the item list is very long (>20 items) or when the user requests a condensed description.

8. Schedule-Based Trigger Rules

When triggered via schedules (e.g., 'create work orders for today's contractor schedules'), the agent follows this process:

1. Fetch all schedules for the target date.

2. Fetch the full contractor list from Simpro.

3. Cross-reference: keep only schedules where Staff.ID matches a contractor ID.

4. If a department filter is specified, resolve department via Section 4 mapping and filter schedules.

5. For each contractor + cost centre combination, fetch catalog, labour, and one-off items.

6. Deduplicate by (ContractorID, JobID, SectionID, CostCentreID) to avoid duplicate entries.

7. Build the review CSV with all items, Include column set to Yes by default.

If no contractor schedules are found for the target date, the agent reports this clearly and does not generate an empty CSV.

If department filter yields zero results but unfiltered schedules exist, the agent keeps all contractor schedules and warns the user.

9. Direct Trigger Rules

When triggered directly (e.g., 'create work order for job 20990, cost centre 116534, contractor ABC Roofing'), the agent follows this process:

1. Resolve contractor name to ID via the contractor list. If ambiguous, ask user.

2. If section_id is not provided, reverse-lookup by iterating job sections to find which contains the cost centre.

3. If cost_centre_id is not provided, list all cost centres for the section and ask user to choose.

4. Fetch items from the specified cost centre.

5. Build the review CSV for the single contractor + cost centre combination.

10. CSV Review Sheet Columns

The Phase A output (review sheet) contains the following columns:

11. Re-Upload Processing Rules

When the user downloads the CSV, edits the Include column, and re-uploads:

Only rows with Include = Yes (or Y, True, 1) are included in the contractor job.

Rows with Include = No (or N, False, 0, or blank) are excluded.

Items are grouped by (JobID, SectionID, CostCentreID, ContractorID) to form individual contractor jobs.

Material costs and labour costs are summed separately per group.

If no items are marked as Include = Yes, the agent reports an error and asks the user to re-upload.

If metadata columns (JobID, SectionID, etc.) are missing, the group is skipped with an error.

12. Error Handling

Contractor name not found: Fail with a clear message listing available contractors.

Multiple contractor matches: Use crossroads system for disambiguation, or ask user.

No items in cost centre: Report clearly, skip that cost centre.

MCP tool errors: Fail the specific operation with error details; continue with others.

SOP not found: Agent uses code-level fallback defaults with a warning.

Missing section_id: Reverse-lookup via all job sections. If multiple sections, ask user.

13. Configuration Section (for LLM Extraction)

Each of the following statements describes how the agent should behave. The LLM uses these to generate the policy JSON automatically.

Default CompanyID: 2

Default TaxCodeID: 1

Default DateIssued: Today (current date in YYYY-MM-DD format)

Default ContractorSupplyMaterials: False

Default Description Format: Itemized

Include Catalog Items: True

Include Labour Items: True

Include One-Off Items: True

Exclusion Patterns: None

Grouping: By (JobID, SectionID, CostCentreID, ContractorID)

Materials Calculation: Sum of Total for Material/Catalog/OneOff type items

Labour Calculation: Sum of Total for Labour/Labor type items

Timezone: Australia/Melbourne

Max Clarification Rounds: 5

14. Version and Metadata

Version: 1.0.0

Last Updated: 2026-02-16

Maintainer: Fieldmind AI - Work Order Automation Team

Applies To: All Simpro-connected companies

Source of Truth: This SOP -> Agent Policy JSON -> Contractor Job Creation