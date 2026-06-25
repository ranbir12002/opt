# Schedule Agent Refactoring Plan

## Current Issues
1. **FieldResolver is too complex** - tries to handle all resolution strategies
2. **_lookup_schedule_by_staff_date fails** - doesn't properly parse Simpro response format
3. **No LLM orchestration** - uses hardcoded resolution logic

## Architecture (Final - Agreed with User)

```
User Request
    ↓
Backend (routes to schedule agent)
    ↓
Schedule Agent:
  ├─ Parse user intent (LLM in agent, not mcp-client)
  ├─ Validate against SOP
  ├─ For UPDATE/DELETE with missing IDs:
  │   └─ Iterative resolution (max 3 turns, fuzzy matching)
  ├─ Build Simpro payload (SOP rules)
  └─ Call MCP tools to execute
    ↓
MCP Server:
  ├─ Validate payload
  ├─ Auto-correct common issues
  └─ Call Simpro API
```

## Changes Needed

### 1. Simplify schedule_agent.py
- **Keep**: Chat parsing (existing `_parse_chat_schedule_request`)
- **Keep**: Excel parsing (existing logic)
- **Remove**: Complex `FieldResolver` class with LLM planning
- **Add**: Simple `_resolve_missing_ids_iteratively()` for UPDATE/DELETE
- **Keep**: SOP validation

### 2. schedule_executor.py (Keep as-is)
- Already builds Simpro payloads per SOP
- Already handles 15-min rounding, ScheduleRate, time format
- No changes needed!

### 3. mcp-simpro-server (Minor addition)
- Add payload validation in schedule tools
- Auto-correct common format issues

## Implementation Steps

1. ✅ Create this refactor plan
2. ⏳ Remove `FieldResolver` class
3. ⏳ Add simple `_resolve_missing_ids_iteratively()`
4. ⏳ Update `run_schedule_agent()` to use new flow
5. ⏳ Add payload validation to MCP server
6. ⏳ Test UPDATE/DELETE operations

## Testing Plan
- UPDATE: "update stephen's schedule today to 2pm for 3 hours"
- DELETE: "delete stephen's schedule for today"
- CREATE: "create schedule for john on job 123 tomorrow 8am for 4 hours"
