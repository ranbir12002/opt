# Schedule Management SOP

## Purpose
This SOP defines the rules and validation logic for bulk schedule creation, updates, and deletion operations in Simpro.

## Scope
Applies to all schedule operations performed through the schedule agent (both single and bulk operations).

## Business Rules

### 1. Operation Types
- **CREATE**: Add new schedules to jobs/quotes
- **UPDATE**: Modify existing schedules (requires ScheduleID)
- **DELETE**: Remove schedules (requires ScheduleID)

### 2. Required Fields

**For CREATE:**
- Operation = "CREATE"
- JobID/JobName OR QuoteID/QuoteName
- SectionName/SectionID (or will prompt user)
- CostCentreName/CostCentreID (or will prompt user)
- StaffName/StaffID
- Date (YYYY-MM-DD format)
- Blocks (positive number, can be a float e.g. 1.5 for 1.5 hours, minimum > 0)
- StartTime (HH:MM format in 24-hour time, e.g., "09:00", "14:30")

**For UPDATE:**
- All fields from CREATE, plus:
- ScheduleID (identifies which schedule to update)

**For DELETE:**
- Operation = "DELETE"
- ScheduleID
- JobID/QuoteID, SectionID, CostCentreID (for API path)

### 3. Validation Rules

1. **Date Format**: Must be valid date, converted to YYYY-MM-DD
2. **Blocks**: Must be a positive number (floats allowed, e.g. 1.5 = 1.5 hours; minimum > 0)
3. **StartTime**: Must be in HH:MM format (24-hour time). Examples: "09:00", "14:30", "08:00"
   - **15-Minute Intervals**: Simpro accepts schedules in 15-minute blocks only
   - Times are automatically rounded to nearest 15-minute mark:
     - 9:01 → 9:00
     - 9:08 → 9:15
     - 9:14 → 9:15
     - 9:23 → 9:30
     - 9:53 → 10:00 (next hour)
4. **ScheduleRate**: Simpro requires schedule rate ID (integer)
   - **Default Value**: ScheduleRate ID = 1 (standard rate)
   - Future enhancement: Allow per-schedule rate customization
5. **Staff**: Must exist in Simpro system (validated via contact lookup with fuzzy matching)
6. **Job/Quote Context**: Must exist and be active
7. **Section/Cost Centre**: Must belong to the specified job/quote

### 4. Name Resolution Priority

When both ID and Name are provided:
1. Use ID if provided (faster, more accurate)
2. Use Name if ID missing (resolved via MCP tools)
3. If Name is ambiguous, prompt user to choose

### 5. Error Handling

**Threshold for Interactive Clarification:**
- ≤ 5 issues: Show interactive UI in frontend
- \> 5 issues: Generate pre-filled Excel for user to correct

**Error Types:**
- **Missing Fields**: Prompt user with available options
- **Ambiguous Matches**: Prompt user to choose from matches
- **Not Found**: Report error, user must fix data
- **Invalid Format**: Report error with expected format

### 6. Schedule Rate Configuration

**Default Rate:**
- ScheduleRate ID = 1 (standard rate)

**Rate Selection Logic:**
Define rules for automatic rate selection based on schedule attributes.
Rules are evaluated in order; first match wins.

```yaml
schedule_rate_rules:
  - condition: "time_based"
    rules:
      # Example: Overtime rate for schedules starting after 5 PM
      # - if: "start_hour >= 17"
      #   rate_id: 2
      #   description: "Evening/overtime rate"
      # Example: Weekend rate
      # - if: "day_of_week in ['Saturday', 'Sunday']"
      #   rate_id: 3
      #   description: "Weekend rate"

  - condition: "default"
    rate_id: 1
    description: "Standard rate"
```

**Currently Active Rules:**
- All schedules use ScheduleRate ID = 1 (no conditional logic active)

**Future Enhancements:**
- Support for multiple rate types (overtime, weekend, holiday, etc.)
- Per-staff custom rates
- Cost-centre-specific rates

### 7. Duplicate Prevention

- Same staff cannot be scheduled twice for same date/time on same job/section/cost centre
- Agent validates uniqueness before submission

### 8. DELETE Confirmation

- Every DELETE operation requires explicit user confirmation before execution
- Agent presents a confirmation prompt showing: Staff name, Date, Job ID, Hours, Start time, Schedule ID
- User must select **"Yes, delete this schedule"** to proceed
- Selecting **"No, keep the schedule"** cancels the delete — the schedule is left untouched
- This applies to all DELETE operations including bulk deletes — each row is confirmed individually
- No DELETE is ever executed silently or automatically without user approval

### 9. Field Assembly Defaults

Rules for how schedule fields are populated when not explicitly provided by the user.

**For CREATE:**
- `Date`, `StartTime`, `Blocks` — all required; agent will prompt if any are missing
- `Notes` — defaults to empty string if not provided
- `IsLocked` — defaults to false if not provided
- `EndTime` — alternative to `Blocks`; if provided, agent computes `Blocks = EndTime − StartTime`

**For UPDATE:**
- Any field not provided by the user is preserved from the existing Simpro schedule
- `Date` — required (used to locate the existing schedule)
- `StartTime` — preserved from existing schedule if not provided
- `Blocks` — preserved from existing schedule if not provided
- `BlocksAdjust` — optional signed adjustment (e.g. +2, -1.5) applied to existing blocks; `Blocks = existing_blocks + BlocksAdjust`; result must be > 0
- `EndTime` — alternative to `BlocksAdjust`; if provided, agent computes `Blocks = EndTime − StartTime`
- `Notes` — preserved from existing schedule if not provided
- `IsLocked` — only updated if explicitly provided

**For DELETE / LOCK / UNLOCK:**
- Only IDs and Date are used (for API path and schedule lookup)
- `StartTime`, `Blocks`, `Notes` are ignored and not sent to the API
- `IsLocked` is set to `true` for LOCK and `false` for UNLOCK automatically

### 10. Locked Schedule Handling

- A locked schedule (`IsLocked = true`) cannot be deleted directly
- When a DELETE is attempted on a locked schedule, the agent must pause and ask the user:
  *"This schedule is locked. Do you want to unlock it first and then delete it?"*
- User must explicitly approve both the unlock and the delete — this is a mandatory confirmation step
- Selecting **"Yes, unlock and delete"** proceeds with: (1) unlock the schedule, (2) delete it
- Selecting **"No, keep the schedule"** cancels the operation entirely — schedule remains locked and intact
- No unlock or delete action is taken automatically without user approval

## Compliance Notes

- All schedule operations must follow company-specific work hour policies
- Locked schedules (IsLocked=true) may require additional permissions
- Date ranges respect company calendar settings

## Revision History

- v1.5 (2026-03-20): Added Section 10 — Locked Schedule Handling (unlock + delete requires user approval)
- v1.4 (2026-03-20): Added Section 8 — DELETE Confirmation gate; Section 9 — Field Assembly Defaults; fixed Blocks to allow floats
- v1.3 (2026-02-11): Added default ScheduleRate ID = 1 per Simpro API requirement
- v1.2 (2026-02-11): Added automatic 15-minute interval rounding for Simpro compatibility
- v1.1 (2026-02-11): Added StartTime as required field for CREATE operations
- v1.0 (2026-02-11): Initial SOP creation
