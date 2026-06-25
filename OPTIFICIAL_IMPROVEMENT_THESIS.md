# Optificial: Self-Improving Decision Intelligence

## A Scientific Analysis of Adaptive Learning in Agentic Systems

---

## 1. CURRENT STATE DIAGNOSIS

### 1.1 Architecture Summary

Optificial is an agentic back-office platform with this decision hierarchy:

```
User Query
    │
    ├── Intent Analyzer (LLM) ───── Route to Agent or MCP
    │
    ├── Entity Resolver (Hybrid) ── Fuzzy Match → LLM Disambiguation → User Clarification
    │
    ├── Crossroads (LLM) ───────── Ambiguous Match │ Error Recovery │ Resolution │ Validation
    │
    └── Tool Execution (MCP) ────── Simpro API │ MyOB API │ File Processing
```

### 1.2 The Core Problem: Stateless Intelligence

Every decision point in the system is **memoryless across requests**:

| Component | What It Knows | What It Forgets |
|-----------|--------------|-----------------|
| `crossroads.py` | Domain knowledge (static), current context | Every decision it ever made |
| `entity_resolver.py` | Fuzzy match thresholds (hardcoded) | That "Taz" always means Employee #42 for Org #7 |
| `intent_analyzer.py` | Classification rules (static prompt) | That Org #3 asks about schedules 80% of the time |
| `presenter_router.py` | Table formatting rules (static) | That User X prefers summary over detail |
| `chat.py` | Last 10 messages (in-memory, volatile) | Everything on server restart |

**The system is equally smart on day 1 and day 1000.** All intelligence comes from static prompts and within-request adaptation (max 3 resolution retries, max 6 tool-call iterations). Nothing persists.

### 1.3 What IS Measured Today

```
COLLECTED (SQLite usage_records):
  ✓ Token count per request
  ✓ Cost per request
  ✓ Latency (duration_ms)
  ✓ Agent name
  ✓ Model name
  ✓ Clarification rounds (0 or 1)

NOT COLLECTED:
  ✗ User question text
  ✗ LLM response text
  ✗ Success/failure outcome
  ✗ Tool calls made (which, how many, which failed)
  ✗ Intent classification result
  ✗ Crossroads decisions and outcomes
  ✗ Entity resolution paths (auto-selected vs clarified vs failed)
  ✗ User feedback (no mechanism exists)
  ✗ Error types and categories
  ✗ Time-to-resolution (multi-turn)
```

---

## 2. VALIDATING YOUR CONCERN: THE CORNER-IMPROVEMENT BIAS

### 2.1 The Problem Stated Formally

Your concern maps to a well-studied phenomenon in machine learning called **distribution shift** or more specifically **exploitation-exploration imbalance**:

> If the system improves based on usage frequency, heavily-used features get disproportionately better while under-used features stagnate or regress relative to the improved areas.

This is the **"Matthew Effect"** in adaptive systems: *"For unto every one that hath shall be given... but from him that hath not shall be taken away."*

### 2.2 Is This Concern Valid?

**Yes. Unequivocally.** Here's why, broken down scientifically:

#### Problem 1: Frequency-Biased Learning (Your Primary Concern)

If improvements are driven by what users use most:

```
                     Day 1                    Day 100
                   ┌─────────┐              ┌─────────┐
                   │         │              │    ▓▓   │
    Schedules ─────│  ████   │   ──────►    │  ▓▓▓▓▓▓ │  (80% of queries → 80% of improvements)
                   │         │              │         │
    Invoices ──────│  ████   │   ──────►    │  ████   │  (15% of queries → 15% of improvements)
                   │         │              │         │
    Work Orders ───│  ████   │   ──────►    │  ███    │  (5% of queries → stagnation or REGRESSION)
                   │         │              │         │
                   └─────────┘              └─────────┘
                 (uniform capability)      (skewed capability)
```

Why regression? Because improvements to the shared decision layer (crossroads prompts, domain knowledge, entity resolution thresholds) are tuned for the dominant use case. Changes that optimize schedule resolution might inadvertently break invoice edge cases that were never tested.

#### Problem 2: Overfitting to Power Users

Construction companies have diverse roles. A project manager asking schedule questions 50 times/day would dominate the learning signal, while an accounts payable clerk using invoices 3 times/day would be underrepresented. The system would optimize for the PM's query patterns, vocabulary, and workflows.

#### Problem 3: The Prompt Drift Paradox

If a README/improvement file is used to modify LLM behavior:
- More examples for schedules → LLM becomes biased toward schedule interpretations
- Query "show me the job details for March" could be interpreted as:
  - Schedule view (if prompt has many schedule examples)
  - Job lookup (correct interpretation)
  - Invoice search (if job has financial context)

The more domain-specific examples you add for one area, the more the LLM's attention distribution shifts toward that area, even for unrelated queries.

### 2.3 Real-World Analogies

| System | What Happened | Result |
|--------|--------------|--------|
| YouTube Recommendations | Optimized for watch-time on popular content | Niche content creators became invisible |
| Google Autocomplete | Learned from frequent searches | Reinforced popular queries, suppressed rare but valid ones |
| Chatbot Fine-tuning | Fine-tuned on customer service logs (80% billing) | Lost ability to handle technical support queries |

### 2.4 Verdict

Your concern is not just valid — it's the **primary failure mode** of naive adaptive systems. Any improvement mechanism MUST have structural safeguards against this.

---

## 3. THE SPIDER-WEB MODEL: 360-DEGREE IMPROVEMENT

### 3.1 Design Principles

You described the ideal as a **spider web** — unequal but structurally sound improvement across all dimensions. Here's the formalization:

```
                    Routing Accuracy
                         ▲
                        ╱ ╲
                       ╱   ╲
          Entity      ╱     ╲      Error
         Resolution ◄─       ─► Recovery
                     │╲     ╱│
                     │ ╲   ╱ │
                     │  ╲ ╱  │
                     │   ▼   │
                     │ Query │
                     │ Under-│
                     │standing│
                     │   ▲   │
                     │  ╱ ╲  │
                     │ ╱   ╲ │
                     │╱     ╲│
         API Usage  ◄─       ─► Presentation
          Patterns   ╲     ╱    Quality
                      ╲   ╱
                       ╲ ╱
                        ▼
                   User Satisfaction
```

**Rule: Every improvement dimension must have its own measurement, its own minimum threshold, and a ceiling that prevents over-optimization.**

### 3.2 The Seven Capability Dimensions

Each dimension is measured independently. The system improves the **weakest dimension first**, not the most-used one.

| # | Dimension | What It Measures | Current Baseline |
|---|-----------|-----------------|-----------------|
| 1 | **Routing Accuracy** | Does the intent analyzer send queries to the correct agent? | Unknown (not measured) |
| 2 | **Entity Resolution** | How often are names/refs resolved correctly without clarification? | Partially (clarification_rounds logged) |
| 3 | **Tool Selection** | Does the LLM pick the right MCP tools with correct parameters? | Unknown (not measured) |
| 4 | **Query Understanding** | Does the system correctly interpret what the user wants? | Unknown (not measured) |
| 5 | **Error Recovery** | When something fails, does the system recover or give up? | Unknown (not measured) |
| 6 | **Response Quality** | Is the final answer correct, complete, and well-formatted? | Unknown (not measured) |
| 7 | **Operational Efficiency** | Token usage, latency, tool call count per resolution | Partially (tokens + latency logged) |

---

## 4. PRACTICAL SOLUTION: THE DECISION JOURNAL

### 4.1 Concept

Instead of a "README that the LLM reads to improve," implement a **Decision Journal** — a structured, queryable record of every decision the system makes, with outcomes.

The journal is NOT a prompt file. It is a database table + an analysis layer that produces **targeted, balanced improvements** to the system's prompts, thresholds, and domain knowledge.

### 4.2 Architecture

```
┌──────────────────────────────────────────────────────┐
│                    DECISION JOURNAL                   │
│                                                       │
│  ┌─────────┐  ┌──────────┐  ┌──────────┐            │
│  │ Record  │  │ Analyze  │  │ Improve  │            │
│  │ Layer   │──│ Layer    │──│ Layer    │            │
│  └─────────┘  └──────────┘  └──────────┘            │
│       │            │              │                   │
│  Every decision    Weekly         Update prompts,     │
│  is logged with    batch          thresholds, and     │
│  context + outcome analysis      domain knowledge    │
│                                   WITH GUARDRAILS     │
└──────────────────────────────────────────────────────┘
```

### 4.3 Layer 1: Record (Runtime — Every Request)

Add a new SQLite table `decision_journal`:

```sql
CREATE TABLE decision_journal (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id      TEXT NOT NULL,          -- links all decisions in one request
    org_id          INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    created_at      TEXT DEFAULT (datetime('now')),

    -- WHAT happened
    dimension       TEXT NOT NULL,          -- routing|entity|tool|query|error|response|efficiency
    decision_type   TEXT NOT NULL,          -- e.g., "intent_classification", "fuzzy_match", "crossroads_resolution"
    decision_input  TEXT,                   -- sanitized input (no PII — use same allowlist filter)
    decision_output TEXT,                   -- what the system decided
    confidence      REAL,                   -- 0.0-1.0 if available
    alternatives    TEXT,                   -- JSON array of other options considered

    -- HOW it turned out
    outcome         TEXT DEFAULT 'unknown', -- success|failure|partial|corrected|unknown
    outcome_signal  TEXT,                   -- what told us the outcome (user_feedback|api_success|api_error|clarification_needed|user_correction)
    outcome_detail  TEXT,                   -- error message, correction detail, etc.

    -- CONTEXT for analysis
    agent_name      TEXT,                   -- schedule|invoice|workorder|chat
    tool_name       TEXT,                   -- MCP tool used (if applicable)
    attempt_number  INTEGER DEFAULT 1,      -- which try (for retries)
    duration_ms     INTEGER
);

CREATE INDEX idx_journal_dimension ON decision_journal(dimension);
CREATE INDEX idx_journal_outcome ON decision_journal(outcome);
CREATE INDEX idx_journal_org ON decision_journal(org_id);
CREATE INDEX idx_journal_created ON decision_journal(created_at);
```

**What gets recorded (with PII protection):**

| Decision Point | Dimension | What's Logged |
|---------------|-----------|--------------|
| Intent analyzer classifies query | `routing` | intent, confidence, agent selected |
| Fuzzy match auto-selects entity | `entity` | match score, score gap, selected vs alternatives |
| Crossroads disambiguates | `entity` | decision, candidates count, context type |
| User submits clarification | `entity` | outcome=`corrected`, what system picked vs what user picked |
| MCP tool called successfully | `tool` | tool name, param count, response time |
| MCP tool returns error | `tool` | tool name, error code, error pattern |
| Resolution crossroad fires | `error` | stuck point type, strategy chosen, attempt number |
| Presenter formats response | `response` | format type (table/text/mixed), row count |
| Request completes | `efficiency` | total tool calls, total tokens, total latency |

**Outcome detection (automatic, no user action needed):**

```python
# These signals are ALREADY available in the codebase — they just aren't recorded:

# 1. API success → outcome = "success"
#    (tool call returns 200/201)

# 2. API error → outcome = "failure"
#    (tool call returns 4xx/5xx)

# 3. Clarification needed → outcome = "corrected"
#    (AmbiguousResolutionError raised → user picks different option)

# 4. User rephrases → outcome = "failure"
#    (follow_up=True detected by intent analyzer → user wasn't satisfied)

# 5. User says "wrong" / "no" / "that's not right" → outcome = "failure"
#    (sentiment detection on follow-up messages)

# 6. Request completes with data returned → outcome = "success"
#    (presenter returns non-empty envelope)

# 7. Request returns "no results" → outcome = "partial"
#    (empty data but no error)
```

### 4.4 Layer 2: Analyze (Batch — Weekly/On-Demand)

A periodic analysis job (cron or manual trigger) computes the **Capability Radar** — a score for each dimension:

```python
# Capability Radar computation (pseudocode)

def compute_capability_radar(org_id: int, days: int = 30) -> dict:
    """
    Returns scores 0.0-1.0 for each dimension.
    Score = success_rate weighted by recency.
    """
    dimensions = {}

    for dim in DIMENSIONS:
        entries = query_journal(org_id, dimension=dim, days=days)
        if not entries:
            dimensions[dim] = None  # No data — cannot score
            continue

        # Recency-weighted success rate
        # Recent outcomes matter more than old ones (exponential decay)
        score = weighted_success_rate(entries, half_life_days=14)
        dimensions[dim] = score

    return dimensions

# Example output:
# {
#     "routing":    0.92,   # 92% of queries routed correctly
#     "entity":     0.78,   # 78% resolved without clarification
#     "tool":       0.95,   # 95% of tool calls succeed
#     "query":      0.85,   # 85% understood correctly (no rephrase)
#     "error":      0.60,   # 60% of errors recovered from
#     "response":   None,   # No feedback data yet
#     "efficiency": 0.88,   # 88th percentile on latency/tokens
# }
```

**The Spider-Web Visualization:**

```
                    Routing (0.92)
                         *
                        / \
                       /   \
        Entity (0.78) *     * Error Recovery (0.60)  ← WEAKEST
                      |\   /|
                      | \ / |
                      |  *  |
                      | Query|
                      |(0.85)|
                      |  *  |
                      | / \ |
                      |/   \|
        Tool (0.95)  *     * Response (N/A)
                      \   /
                       \ /
                        *
                  Efficiency (0.88)
```

### 4.5 Layer 3: Improve (The Guardrailed Part)

This is where your concern about corner-improvement bias is addressed. The improvement layer has **five structural guardrails**:

#### Guardrail 1: Weakest-First Priority (Anti-Bias)

```
RULE: Improvements target the dimension with the LOWEST score,
      not the dimension with the MOST data.

If routing = 0.92 (1000 samples) and error_recovery = 0.60 (50 samples):
  → Improve error_recovery FIRST, even though routing has 20x more data.

This directly prevents your "one corner gets all the improvement" concern.
```

#### Guardrail 2: Minimum Coverage Threshold

```
RULE: No dimension may be scored below a FLOOR threshold for more
      than 2 consecutive analysis periods without triggering an alert.

FLOORS:
  routing:    0.70
  entity:     0.65
  tool:       0.80
  query:      0.70
  error:      0.50
  response:   0.60
  efficiency: 0.70

If error_recovery drops to 0.45 for 2 weeks → ALERT: system degradation detected
```

#### Guardrail 3: Improvement Budget Cap (Anti-Overfitting)

```
RULE: No single dimension may receive more than 30% of total
      improvement actions in any analysis period.

This prevents the "schedule queries are 80% of traffic, so schedule
gets 80% of improvements" problem. Even if schedules have 800 failure
cases and invoices have 20, improvement effort is capped.

Budget allocation formula:
  improvement_priority = (1 - score) * weight
  where weight = 1.0 for all dimensions (equal importance)

  NOT: improvement_priority = failure_count * weight
  (which would bias toward high-traffic features)
```

#### Guardrail 4: Regression Testing (Anti-Degradation)

```
RULE: Every improvement must be validated against a baseline
      of test cases from ALL dimensions, not just the improved one.

Before applying any prompt/threshold change:
  1. Sample 10 recent decisions from EACH dimension
  2. Run the new prompt/threshold against all 70 samples
  3. If any dimension's score drops by more than 5%: REJECT the change
  4. If the target dimension doesn't improve by at least 3%: REJECT (not worth the risk)
```

#### Guardrail 5: Improvement Types Are Structural, Not Prompt-Stuffing

```
RULE: Improvements are NOT "add more examples to the prompt."
      They are one of these four types:

TYPE 1: THRESHOLD ADJUSTMENT
  Example: Fuzzy match auto-select threshold 80 → 85 for org #7
           (because their entity names are more ambiguous)
  Scope: Numeric parameter change
  Risk: Low (easily reversible)

TYPE 2: DOMAIN KNOWLEDGE UPDATE
  Example: Add to _DOMAIN_KNOWLEDGE["simpro_schedules"]:
           "When searching for staff schedules on a specific date,
            always use the date filter parameter, not client-side filtering."
  Scope: Factual knowledge addition
  Risk: Medium (could affect multiple crossroads decisions)

TYPE 3: ERROR PATTERN REGISTRATION
  Example: Register that "422 Unprocessable Entity" with "Section.ID"
           in the message means the section doesn't belong to the job.
           Recovery: re-resolve section for the given job.
  Scope: New error recovery path
  Risk: Low (additive, doesn't change existing paths)

TYPE 4: TOOL HINT REFINEMENT
  Example: Update simpro_api_reference.py to note that
           list_schedules requires ISO 8601 date format (not DD/MM/YYYY).
  Scope: Tool description update
  Risk: Low (informational, doesn't change logic)

EXPLICITLY EXCLUDED:
  - Adding example queries to system prompts (causes attention drift)
  - Changing routing logic based on frequency (causes bias)
  - User-specific prompt customization (causes inconsistency)
```

---

## 5. IMPLEMENTATION: WHAT TO BUILD

### 5.1 Phase 1: Instrument (Week 1-2) — No Behavior Change

Add recording to existing decision points. The system behaves identically but now has eyes.

```
Files to modify:
├── backend/auth/database.py          # Add decision_journal table
├── backend/utils/decision_journal.py # NEW: Journal recording functions
├── backend/utils/crossroads.py       # Add journal.record() after each decision
├── backend/utils/entity_resolver.py  # Add journal.record() for match/disambig outcomes
├── backend/utils/intent_analyzer.py  # Add journal.record() for classification
├── backend/api/chat.py               # Add journal.record() for routing, completion, corrections
├── backend/utils/mcp_executor.py     # Add journal.record() for tool success/failure
└── backend/api/auth_routes.py        # Add GET /api/auth/radar endpoint
```

**Key design decisions:**

```python
# decision_journal.py — the recording module

import sqlite3
import json
import uuid
import logging
from datetime import datetime
from typing import Optional
from utils.pii_filter import sanitize_for_llm  # REUSE existing PII filter

logger = logging.getLogger(__name__)

# Generate a request_id at the start of each chat request
# Pass it through the call chain so all decisions in one request are linked
def new_request_id() -> str:
    return uuid.uuid4().hex[:12]

async def record_decision(
    request_id: str,
    org_id: int,
    user_id: int,
    dimension: str,        # routing|entity|tool|query|error|response|efficiency
    decision_type: str,    # e.g., "intent_classification"
    decision_output: str,  # what was decided
    confidence: Optional[float] = None,
    alternatives: Optional[list] = None,
    outcome: str = "unknown",
    outcome_signal: Optional[str] = None,
    outcome_detail: Optional[str] = None,
    agent_name: Optional[str] = None,
    tool_name: Optional[str] = None,
    attempt_number: int = 1,
    duration_ms: Optional[int] = None,
    decision_input: Optional[str] = None,  # MUST be sanitized before passing
):
    """
    Record a decision to the journal. Fire-and-forget — failures are logged
    but never block the main request path.

    PII RULE: decision_input and outcome_detail are sanitized before storage.
    Never store raw user text, names, or entity data.
    """
    try:
        # Sanitize any text fields that might contain PII
        safe_input = sanitize_for_llm(decision_input) if decision_input else None
        safe_detail = sanitize_for_llm(outcome_detail) if outcome_detail else None

        # ... insert into SQLite (async via run_in_executor) ...
    except Exception as e:
        logger.warning(f"Decision journal write failed (non-fatal): {e}")
        # NEVER let journal failures affect the main request
```

### 5.2 Phase 2: Measure (Week 3-4) — Radar Dashboard

Build the analysis layer and expose the Capability Radar.

```
Files to create/modify:
├── backend/utils/capability_radar.py  # NEW: Compute radar scores from journal
├── backend/api/auth_routes.py         # Add GET /api/auth/radar
├── frontend/src/components/           # Optional: Radar visualization
```

```python
# capability_radar.py — scoring engine

DIMENSIONS = [
    "routing", "entity", "tool",
    "query", "error", "response", "efficiency"
]

FLOOR_THRESHOLDS = {
    "routing":    0.70,
    "entity":     0.65,
    "tool":       0.80,
    "query":      0.70,
    "error":      0.50,
    "response":   0.60,
    "efficiency": 0.70,
}

def compute_radar(org_id: int, days: int = 30) -> dict:
    """
    Returns {dimension: {score, sample_count, trend, below_floor}} for each dimension.
    trend = "improving" | "stable" | "degrading" (comparing current vs previous period)
    """
    current = _score_period(org_id, days_ago=0, days=days)
    previous = _score_period(org_id, days_ago=days, days=days)

    radar = {}
    for dim in DIMENSIONS:
        cur = current.get(dim)
        prev = previous.get(dim)

        if cur is None:
            radar[dim] = {"score": None, "sample_count": 0, "trend": "no_data", "below_floor": False}
            continue

        trend = "stable"
        if prev is not None:
            delta = cur["score"] - prev["score"]
            if delta > 0.05:
                trend = "improving"
            elif delta < -0.05:
                trend = "degrading"

        radar[dim] = {
            "score": cur["score"],
            "sample_count": cur["count"],
            "trend": trend,
            "below_floor": cur["score"] < FLOOR_THRESHOLDS[dim],
        }

    return radar


def identify_improvement_targets(radar: dict) -> list:
    """
    Returns dimensions sorted by improvement priority.
    Priority = (1 - score). Dimensions with no data get priority 0.5 (medium).
    Dimensions already above 0.95 are deprioritized (diminishing returns).
    """
    targets = []
    for dim, data in radar.items():
        if data["score"] is None:
            priority = 0.5  # Unknown = medium priority
        elif data["score"] > 0.95:
            priority = 0.02  # Diminishing returns — don't over-optimize
        else:
            priority = 1.0 - data["score"]

        # GUARDRAIL: Boost priority if below floor AND degrading
        if data.get("below_floor") and data.get("trend") == "degrading":
            priority *= 1.5  # Urgent

        targets.append({"dimension": dim, "priority": priority, **data})

    targets.sort(key=lambda t: t["priority"], reverse=True)
    return targets
```

### 5.3 Phase 3: Improve (Week 5+) — Targeted, Guardrailed Changes

This is NOT an auto-pilot system. It produces **recommendations** that a developer reviews.

```
Improvement Pipeline:

1. DETECT: Radar shows error_recovery at 0.55 (below floor of 0.50 is alert)
   └── "Error recovery is the weakest dimension. 45% of errors are not recovered."

2. DIAGNOSE: Query journal for error_recovery failures
   └── "72% of failures are 422 errors from schedule creation where Section.ID
        doesn't belong to the Job. The system retries with the same section
        instead of re-resolving."

3. PRESCRIBE: Generate improvement recommendation
   └── TYPE 3 (Error Pattern Registration):
       "Add pattern to crossroads error_recovery domain knowledge:
        When 422 contains 'Section' and 'Job', the section doesn't belong
        to the job. Strategy: re-resolve section for the given job ID."

4. VALIDATE: Run against regression test suite (all 7 dimensions)
   └── "Tested against 70 historical decisions. error_recovery: +12%.
        All other dimensions: within ±2%. SAFE TO APPLY."

5. APPLY: Developer reviews and merges the change
   └── One-line addition to _DOMAIN_KNOWLEDGE["resolution_patterns"]
```

---

## 6. WHY NOT A README FILE?

Your initial idea was a README that the LLM reads to improve. Here's why the Decision Journal is better:

| Aspect | README Approach | Decision Journal Approach |
|--------|----------------|--------------------------|
| **Bias risk** | High — whoever writes the README encodes their bias | Low — data-driven, balanced across dimensions |
| **Staleness** | Manual updates, easy to forget | Auto-recorded, always current |
| **Prompt bloat** | README grows → more tokens → higher cost → attention dilution | Journal is separate from prompts; only targeted improvements enter prompts |
| **Your concern** | If schedule users dominate, README fills with schedule tips | Guardrail 1 (weakest-first) prevents this structurally |
| **Measurability** | "Did the README help?" — unknowable | Radar scores track improvement quantitatively |
| **Regression risk** | Adding tip for A might break B — no way to know | Guardrail 4 (regression testing) catches this before deployment |
| **Scope control** | Grows unboundedly | Capped by improvement budget (Guardrail 3) |

**However**, a structured knowledge file still has value — not as a growing list of tips, but as a **curated, versioned, bounded reference**. The key difference:

```
BAD: README that grows with every interaction
  "User asked about schedule → add schedule tip"
  "User had invoice error → add invoice tip"
  → Unbounded growth, bias toward frequent features

GOOD: Curated reference updated from journal analysis
  - Max 50 entries, scored by impact
  - Reviewed quarterly
  - Balanced across all 7 dimensions (max 10 per dimension)
  - Old entries replaced when superseded, not appended
```

---

## 7. THE LINEAR THINKING GUARANTEE

You want the product to be "more linear thinking" overall, even with unequal improvements. Here's how the system ensures this:

### 7.1 The Balanced Scorecard Rule

```
INVARIANT: The ratio between the strongest and weakest dimension
           must never exceed 1.5x.

If the best dimension is at 0.95 and the worst is at 0.55:
  Ratio = 0.95 / 0.55 = 1.73 → VIOLATION

Action: Freeze improvements to dimensions above 0.80.
        All improvement budget goes to dimensions below 0.70.
        Resume balanced improvement when ratio ≤ 1.4.
```

### 7.2 The Shared Foundation Principle

```
Most improvements happen in SHARED layers, not agent-specific code:

crossroads.py    ← Used by ALL agents → improvement benefits everyone
entity_resolver  ← Used by ALL agents → improvement benefits everyone
presenter_router ← Used by ALL agents → improvement benefits everyone
fuzzy_match      ← Used by ALL agents → improvement benefits everyone

Agent-specific improvements (schedule_agent.py, invoice_agent.py) are
the LAST resort, applied only when the shared layer can't address the issue.
```

### 7.3 Cross-Pollination

```
When an improvement is found for one agent, CHECK if it applies to others:

Example: Schedule agent learns that "next Monday" should be resolved
         to an ISO date before calling the API.

Cross-pollination check:
  ✓ Invoice agent — does it handle relative dates? → Yes → apply same fix
  ✓ Work order agent — does it handle dates? → Yes → apply same fix
  ✓ MCP chat — does it pass dates to tools? → Yes → add to system prompt

This is the "spider web" — pulling one thread strengthens adjacent threads.
```

---

## 8. GUARD RAILS SUMMARY

| # | Guardrail | What It Prevents |
|---|-----------|-----------------|
| 1 | Weakest-first priority | One feature getting all improvements (your core concern) |
| 2 | Floor thresholds | Any dimension falling to unacceptable levels |
| 3 | Budget cap (30%) | Over-investment in the most-used feature |
| 4 | Regression testing | Improvement to A breaking B |
| 5 | Structural improvements only | Prompt bloat and attention drift |
| 6 | Balanced scorecard ratio (1.5x max) | Extreme imbalance between dimensions |
| 7 | Cross-pollination check | Improvements staying siloed in one agent |
| 8 | PII filtering on all journal entries | Privacy protection (reuses existing filter) |
| 9 | Fire-and-forget recording | Journal failures never block user requests |
| 10 | Human review required | No auto-pilot — developer approves all changes |

---

## 9. ANSWER TO YOUR QUESTION: IS IT REALLY NEEDED?

### What Works Well Today (Don't Fix These)

1. **The crossroads architecture is excellent.** Pluggable, extensible, PII-safe. It just needs eyes (instrumentation).

2. **Entity resolution is well-designed.** Phased batch resolution, concurrent independence, graceful clarification. It just doesn't learn from corrections.

3. **The MCP tool ecosystem is solid.** 40+ tools across Simpro and MyOB with proper rate limiting, caching, and error handling.

4. **PII protection is thorough.** Dual-layer filtering already exists and can be reused for journal sanitization.

### What's Missing (Build These)

1. **Outcome tracking.** The system doesn't know if it succeeded. This is the #1 gap. Without it, you can't measure improvement OR detect degradation.

2. **Decision recording.** Every crossroads decision, every fuzzy match, every routing choice should be logged. The infrastructure is simple (one SQLite table) and non-invasive (fire-and-forget writes).

3. **Balanced improvement targeting.** Without the Capability Radar and its guardrails, any improvement effort will naturally gravitate toward the loudest complaints (which come from the most-used features).

### The Minimum Viable Version

If you want to start small, here's the 80/20:

```
MUST HAVE (Phase 1 only — 2-3 days of work):
  1. decision_journal table in SQLite
  2. Record outcomes at 4 critical points:
     a. Intent classification result + confidence
     b. Entity resolution: auto-selected vs clarification-needed vs failed
     c. Tool call success/failure
     d. User follow-up detected (implies previous answer was wrong)
  3. GET /api/auth/radar endpoint that computes dimension scores

NICE TO HAVE (Phase 2 — 1 week):
  4. Trend analysis (improving/stable/degrading)
  5. Improvement recommendation generator
  6. Regression test framework

LATER (Phase 3):
  7. Frontend radar visualization
  8. Automated cross-pollination checks
  9. Curated knowledge file generation from journal
```

### The Scientific Answer

Your system is at the stage where **instrumentation provides more value than optimization**. You can't improve what you can't measure. The Decision Journal gives you measurement. The Capability Radar gives you balanced measurement. The guardrails prevent the measurement from creating bias.

Build the eyes first. The brain improvements will follow naturally from what the eyes reveal.

---

## 10. RISK ASSESSMENT

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Journal table grows too large | Medium | Low | Monthly archival + 90-day retention policy |
| Recording adds latency | Low | Low | Fire-and-forget async writes, ~1ms per record |
| PII leaks into journal | Low | High | Reuse existing `sanitize_for_llm()` — already proven |
| Over-engineering the analysis | Medium | Medium | Start with Phase 1 only; defer analysis until 30 days of data |
| Team doesn't review recommendations | Medium | High | Integrate radar into existing analytics dashboard; make it visible |
| Improvement changes break production | Low | High | Guardrail 4 (regression testing) + human review gate |

---

## 11. THE PROMPT DRIFT PROBLEM (Critical Concern — Added After Review)

### 11.1 The Concern Restated

> "If the improvement mechanism uses an LLM to analyze decision journal data and produce
> better prompts, there's a compounding risk: the LLM that writes the improvement may
> hallucinate, may be limited by token context, or may simply produce a subpar prompt
> compared to the current one. Over iterations, these small degradations compound.
> The product drifts away from what it was intended to do."

This is the **auto-immune disease** of self-improving AI systems. The improvement mechanism
itself becomes the source of degradation.

### 11.2 Why This Concern Is Scientifically Valid

#### The Compounding Error Problem

Each improvement cycle introduces a small error probability. Even with 95% accuracy per cycle:

```
Cycle 1:  0.95  probability of correct improvement
Cycle 5:  0.95^5  = 0.77  (23% chance of at least one bad change)
Cycle 10: 0.95^10 = 0.60  (40% chance of at least one bad change)
Cycle 20: 0.95^20 = 0.36  (64% chance of at least one bad change)

And each bad change degrades the NEXT cycle's analysis quality,
because the LLM is now reasoning from a worse baseline.
This is a positive feedback loop toward degradation.
```

#### The LLM-as-Judge Limitation

Your current architecture has these specific vulnerabilities:

**1. Token Budget Blindness**

The crossroads system prompt is already ~4,500 tokens (base + type-specific + domain knowledge).
The MCP chat system prompt is ~1,800 tokens. The intent analyzer is ~1,200 tokens.
Total prompt surface area: **~7,500 tokens across 3 LLM call sites**.

If an "improvement LLM" tries to analyze decision journal data (potentially thousands of
entries) and produce better prompts, it faces a token ceiling. It CANNOT hold the full
context of:
- All 7 domain knowledge topics (currently ~2,000 tokens)
- All 4 crossroad type prompts (currently ~2,500 tokens)
- A statistically meaningful sample of journal entries (needs 100+ entries = ~5,000+ tokens)
- The reasoning needed to identify patterns and produce improvements

The LLM will inevitably truncate, summarize, or hallucinate connections. And the error
is INVISIBLE — you can't tell a plausible-sounding prompt change from a correct one
without testing it against real scenarios.

**2. The Semantic Preservation Problem**

Your current prompts contain extremely precise constraints. For example:

```
crossroads.py, resolution type:
  "NEVER suggest columns='GivenName,FamilyName' for list_employees
   — the only valid columns are 'ID,Name'"

intent_analyzer.py:
  "RETRY / CONTINUATION phrases: 'try again', 'retry', 'redo'...
   These ALWAYS refer to the LAST operation in conversation history."

chat.js system prompt:
  "Schedules: get_schedules (results include staff names —
   do NOT pre-lookup contacts)"
```

Each of these constraints was learned through a real production failure. They are
battle-tested. An "improvement LLM" that rewrites the prompt might:
- Rephrase the constraint in a way that weakens it
- Move it to a different position where it gets less attention
- Drop it entirely because it seems "obvious" to the LLM
- Conflict it with a new constraint added for a different improvement

**This is not hypothetical.** Research on LLM self-refinement shows that iterative
prompt rewriting loses specificity after 3-5 cycles (the "smoothing effect" —
each rewrite tends toward more generic, less actionable language).

**3. The Baseline Comparison Problem**

To know if an improvement is actually better, you need to compare:
- Old prompt + same query → result A
- New prompt + same query → result B
- Is B better than A?

But "better" is judged by... another LLM call. Which has its own error rate.
You're now stacking: LLM error in improvement generation × LLM error in evaluation.
The compound error is worse than either alone.

### 11.3 The Solution: Frozen Core + Additive-Only Changes

The key insight is: **the prompts that work today should NEVER be rewritten by an LLM.**
Instead, improvements are constrained to a very specific, safe mechanism.

#### Architecture: The Three Zones

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│  ZONE 1: FROZEN CORE (never modified by any automated       │
│          process — only by developer with manual review)     │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ • _BASE_SYSTEM_PROMPT in crossroads.py              │    │
│  │ • _CROSSROAD_PROMPTS (all 4 types)                  │    │
│  │ • _INTENT_SYSTEM_PROMPT in intent_analyzer.py       │    │
│  │ • buildSystemPrompt() in chat.js                    │    │
│  │ • Personality blocks in personality.py              │    │
│  │ • Fuzzy match thresholds (80/20 gap) in resolver    │    │
│  │ • All hardcoded behavioral rules                    │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  ZONE 2: CURATED KNOWLEDGE (developer-reviewed additions    │
│          informed by journal data — never LLM-generated)    │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ • _DOMAIN_KNOWLEDGE entries (7 topics)              │    │
│  │ • simpro_api_reference.py hints                     │    │
│  │ • Error pattern registrations                       │    │
│  │ • Tool subset definitions                           │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  ZONE 3: ADAPTIVE PARAMETERS (safe to auto-adjust within    │
│          bounded ranges — no prompt text changes)           │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ • Fuzzy match confidence thresholds (range: 70-95)  │    │
│  │ • Score gap thresholds (range: 10-40)               │    │
│  │ • Max resolution attempts (range: 2-5)              │    │
│  │ • Conversation history depth (range: 4-10)          │    │
│  │ • Tool cache TTL (range: 2-10 min)                  │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

#### Why This Works

**Zone 1 (Frozen Core)** addresses the "prompt rewriting compounds errors" problem.
These prompts are NEVER touched by any automated process. They represent the product's
identity and behavioral contract. Changes only happen through:
1. A developer reads the decision journal
2. Developer identifies a specific failure pattern
3. Developer writes a prompt change by hand
4. Change is regression-tested against historical decisions
5. Change is deployed via normal code review + git

**Zone 2 (Curated Knowledge)** addresses the "domain knowledge needs to grow" reality.
New facts CAN be added to `_DOMAIN_KNOWLEDGE`, but:
- Each addition is a standalone fact (not a rewrite of existing content)
- Additions are APPENDED, never replacing existing entries
- Each entry has a creation date and source (which journal entries motivated it)
- There's a hard cap: max 500 tokens per topic, max 10 topics total
- A human writes the text, not an LLM

**Zone 3 (Adaptive Parameters)** addresses the "some things SHOULD auto-adjust" reality.
Numeric thresholds like fuzzy match confidence can safely vary within bounds:

```python
# Example: Adaptive threshold with hard bounds

ADAPTIVE_PARAMS = {
    "fuzzy_auto_select_threshold": {
        "current": 80,
        "min": 70,      # Below 70 → too many wrong auto-selections
        "max": 95,      # Above 95 → everything requires clarification
        "step": 1,      # Adjust by 1 point per cycle
        "metric": "entity_auto_select_accuracy",  # What drives adjustment
        "direction": "maximize",
    },
    "fuzzy_score_gap": {
        "current": 20,
        "min": 10,
        "max": 40,
        "step": 2,
        "metric": "entity_disambiguation_success_rate",
        "direction": "maximize",
    },
    "max_resolution_attempts": {
        "current": 3,
        "min": 2,
        "max": 5,
        "step": 1,
        "metric": "resolution_exhaustion_rate",
        "direction": "minimize",  # Lower exhaustion = better
    },
}

def adjust_parameter(param_name: str, journal_data: list) -> dict:
    """
    Adjust a parameter by ONE step based on journal data.
    Returns the change for logging. Does NOT exceed bounds.
    """
    config = ADAPTIVE_PARAMS[param_name]
    metric_value = compute_metric(config["metric"], journal_data)

    # Compare to previous period
    previous = compute_metric(config["metric"], journal_data, period="previous")

    if config["direction"] == "maximize":
        if metric_value < previous:
            # Getting worse — try adjusting
            new_value = config["current"] + config["step"]
        else:
            # Stable or improving — don't change
            return {"action": "hold", "reason": "metric stable or improving"}
    else:
        if metric_value > previous:
            new_value = config["current"] - config["step"]
        else:
            return {"action": "hold", "reason": "metric stable or improving"}

    # HARD BOUNDS — cannot exceed range
    new_value = max(config["min"], min(config["max"], new_value))

    if new_value == config["current"]:
        return {"action": "hold", "reason": "at boundary"}

    return {
        "action": "adjust",
        "param": param_name,
        "old": config["current"],
        "new": new_value,
        "metric_before": previous,
        "metric_after": metric_value,
    }
```

### 11.4 The Ratchet Mechanism: Preventing Backward Drift

Even with zones, there's a risk that Zone 2 additions accumulate contradictions
or that Zone 3 parameters oscillate. The **ratchet** prevents this:

```
THE RATCHET RULE:

1. SNAPSHOT: Every 30 days, freeze a "known-good" snapshot of:
   - All Zone 2 knowledge entries
   - All Zone 3 parameter values
   - The radar scores at that point

2. COMPARE: After any change (Zone 2 addition or Zone 3 adjustment),
   run the SAME evaluation:
   - Sample 20 decisions per dimension from the journal
   - Replay them against current prompts + parameters
   - Compare radar scores to the snapshot

3. RATCHET: If ANY dimension drops below its snapshot score by >5%:
   → REVERT to the snapshot for that dimension's related configs
   → Log the failed change with reason
   → Mark that change as "rejected" in the journal

4. ADVANCE: If all dimensions are stable or improved:
   → Accept the change
   → Update the snapshot with new scores
   → The ratchet advances — this is the new "known-good" baseline

This ensures the system can ONLY move forward.
A bad change is caught within one evaluation cycle and reverted.
Compounding degradation is impossible because each step is validated
against the last known-good state.
```

```
                    Quality
                       ▲
                       │        ×  ← rejected (regression detected)
                       │       /
     Snapshot 3 ──────►├──────●────────
                       │     /
                       │    × ← rejected
                       │   /
     Snapshot 2 ──────►├──●────────────
                       │ /
     Snapshot 1 ──────►├●──────────────
                       │
                       └──────────────► Time

     ● = accepted change (ratchet advances)
     × = rejected change (reverts to last ●)
     Quality can only go UP or stay flat. Never down.
```

### 11.5 What NEVER Gets Automated

To be absolutely clear about what the "improvement layer" does NOT do:

```
NEVER (regardless of journal data, radar scores, or analysis):

1. ✗ Rewrite any prompt text
   The prompts in Zone 1 are written by humans, tested by humans,
   deployed by humans. No LLM generates or modifies prompt text.

2. ✗ Add examples to prompts
   "User asked about X, so let's add an example of X to the system prompt"
   — this is the primary cause of prompt drift. Examples shift attention.
   The system prompt already says "use %keyword% wildcards." It doesn't
   need 50 examples of keyword extraction.

3. ✗ Remove constraints from prompts
   "This constraint seems unnecessary based on recent data" — constraints
   exist because of PAST failures. Recent data showing no violations
   means the constraint is WORKING, not that it's unnecessary.

4. ✗ Change routing logic
   "80% of queries go to schedules, so let's bias the intent analyzer
   toward schedule_crud" — this is exactly the bias you're concerned about.

5. ✗ Modify personality or tone
   The personality block defines the product's identity. It's not a
   tunable parameter.

6. ✗ Auto-deploy changes
   Every change (even Zone 3 threshold adjustments) is logged and
   reviewable. In the initial rollout, ALL changes require manual
   approval before taking effect.
```

### 11.6 The Practical Difference

Here's the same scenario under the old (risky) and new (safe) approach:

```
SCENARIO: Journal shows that 30% of schedule entity resolutions
          require user clarification for staff names.

─── RISKY APPROACH (LLM rewrites prompts) ───

LLM analyzes journal → "Staff resolution is too conservative"
LLM rewrites fuzzy_match threshold prompt:
  OLD: "Single close match (score gap > 30) → auto-select"
  NEW: "Single close match (score gap > 15) → auto-select"

Problem: The LLM also inadvertently changed the scoring rules section,
weakening the "exact match always wins" constraint. Now exact matches
sometimes DON'T win because the LLM rephrased "always" to "typically."

Next cycle: The weakened exact-match causes 5% more wrong selections.
Journal shows entity errors UP. LLM tries to fix by raising threshold
back... but in doing so, rephrases ANOTHER constraint.

Cycle 3: Two constraints are now subtly different from the original.
Product behavior has drifted. Nobody notices for weeks because each
change was "within tolerance."

─── SAFE APPROACH (Frozen core + adaptive parameters) ───

Radar shows entity dimension at 0.70 (below floor of 0.65? No, but
identified as weakest dimension).

Developer queries journal:
  "30% of staff resolutions need clarification. Of those:
   - 60% are unique names with score > 75 (could have been auto-selected)
   - 25% are genuinely ambiguous (correct to clarify)
   - 15% are typos (fuzzy match worked correctly)"

Developer decision: The 80-point auto-select threshold is too high for
this org's name distribution. Many unique staff have scores of 75-79.

Action: Adjust Zone 3 parameter fuzzy_auto_select_threshold from 80 → 78.
This is within bounds (min=70, max=95).

Ratchet check: Replay 20 entity decisions with threshold=78.
  - 12% more auto-selections (good — fewer clarifications)
  - 0% wrong auto-selections in replay (good — no regression)
  - All other dimensions unchanged

Result: Snapshot advances. Change logged. Prompt text untouched.
```

### 11.7 The Model Availability / Quality Risk

Your concern also mentioned: "if the model changes or degrades, improvements
analyzed by a weaker model would be subpar."

This is addressed by the zone architecture:

```
Zone 1 (Frozen Core): Not affected by model changes at all.
  The prompts are static text. They work with GPT-4, Claude, Gemini,
  or any future model. The prompts don't depend on which model
  analyzes them.

Zone 2 (Curated Knowledge): Written by humans, not by models.
  A developer reads journal data (raw SQL queries, not LLM analysis)
  and writes the knowledge entry. Model quality is irrelevant.

Zone 3 (Adaptive Parameters): Pure math, no LLM involved.
  Threshold adjustments are computed from success/failure ratios.
  It's arithmetic: success_count / total_count = accuracy.
  No LLM judges quality. No model generates the adjustment.
  The only "intelligence" is: "if accuracy dropped, try adjusting
  the threshold by 1 point in the direction that should help."
```

**The analysis layer (Layer 2 from Section 4.4) does NOT use an LLM.**
It uses SQL queries and arithmetic. This eliminates the entire class of
"LLM hallucination in the improvement loop" risks.

```python
# The radar computation is pure SQL + arithmetic. No LLM involved.

def _score_period(org_id: int, days_ago: int, days: int) -> dict:
    """Pure SQL query — no LLM call."""
    query = """
        SELECT dimension,
               COUNT(*) as total,
               SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) as successes,
               AVG(confidence) as avg_confidence
        FROM decision_journal
        WHERE org_id = ?
          AND created_at >= datetime('now', ?)
          AND created_at < datetime('now', ?)
          AND outcome != 'unknown'
        GROUP BY dimension
    """
    # ... execute and compute ratios ...
    # No LLM call anywhere in this function
```

---

## 12. REVISED GUARD RAILS SUMMARY (with Prompt Drift Protection)

| # | Guardrail | What It Prevents | Zone |
|---|-----------|-----------------|------|
| 1 | Weakest-first priority | One feature getting all improvements | Analysis |
| 2 | Floor thresholds | Any dimension falling to unacceptable levels | Analysis |
| 3 | Budget cap (30%) | Over-investment in the most-used feature | Analysis |
| 4 | Regression testing (ratchet) | Any change causing quality to go backward | All Zones |
| 5 | Frozen Core (Zone 1) | Automated prompt rewriting | Zone 1 |
| 6 | Additive-only knowledge (Zone 2) | Existing knowledge being overwritten | Zone 2 |
| 7 | Bounded parameters (Zone 3) | Thresholds going to extreme values | Zone 3 |
| 8 | No LLM in analysis loop | Hallucination in improvement decisions | Analysis |
| 9 | 30-day snapshots | Gradual undetected drift | Ratchet |
| 10 | Cross-pollination check | Improvements staying siloed | All Zones |
| 11 | Human review gate | Unchecked automated changes | Zone 2+3 |
| 12 | Token budget cap on Zone 2 | Domain knowledge bloating prompts | Zone 2 |
| 13 | PII filtering on journal | Privacy leaks via decision recording | Recording |
| 14 | Fire-and-forget recording | Journal failures blocking user requests | Recording |

---

## 13. REVISED RISK ASSESSMENT (with Prompt Drift Risks)

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Journal table grows too large | Medium | Low | Monthly archival + 90-day retention |
| Recording adds latency | Low | Low | Fire-and-forget async writes |
| PII leaks into journal | Low | High | Reuse existing `sanitize_for_llm()` |
| **LLM rewrites prompt poorly** | **N/A** | **N/A** | **Eliminated — no LLM writes prompts (Zone 1 frozen)** |
| **Prompt drift over iterations** | **N/A** | **N/A** | **Eliminated — Zone 1 never modified by automation** |
| **Model downgrade affects improvements** | **N/A** | **N/A** | **Eliminated — analysis is SQL+arithmetic, not LLM** |
| Zone 3 threshold oscillates | Medium | Low | Bounded ranges + one-step-per-cycle limit |
| Zone 2 knowledge contradicts itself | Low | Medium | Human review + token cap + append-only |
| Ratchet snapshot is itself flawed | Low | Medium | Snapshots are validated before creation |
| Developer ignores radar warnings | Medium | High | Integrate into existing analytics dashboard |
| Journal has biased outcome signals | Medium | Medium | Multiple signals per decision; no single source of truth |

---

## 14. FINAL ARCHITECTURE DIAGRAM

```
┌─────────────────────────────────────────────────────────────────────┐
│                        USER REQUEST                                 │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    ZONE 1: FROZEN CORE                              │
│                                                                     │
│  Intent Analyzer ──► Crossroads ──► Entity Resolver ──► MCP Tools  │
│                                                                     │
│  [Static prompts, static behavioral rules, static personality]      │
│  [NEVER modified by automation — only by developer + code review]   │
│                                                                     │
│  Every decision point writes to ──────────────────────────┐         │
└───────────────────────────────────────────────────────────│─────────┘
                                                            │
                                                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    DECISION JOURNAL (SQLite)                         │
│                                                                     │
│  request_id │ dimension │ decision │ outcome │ confidence │ ...     │
│  ─────────────────────────────────────────────────────────────      │
│  abc123     │ routing   │ schedule │ success │ 0.92       │         │
│  abc123     │ entity    │ auto-sel │ correct │ 0.85       │         │
│  abc124     │ tool      │ get_sch  │ failure │ n/a        │         │
│  abc124     │ error     │ retry    │ success │ 0.70       │         │
│                                                                     │
│  [Fire-and-forget writes. PII-sanitized. Never blocks requests.]   │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │
                           SQL queries only
                          (no LLM involved)
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    CAPABILITY RADAR                                  │
│                                                                     │
│  routing: 0.92 ████████████████████░░  (improving)                  │
│  entity:  0.78 ████████████████░░░░░░  (stable)                     │
│  tool:    0.95 ███████████████████████  (stable)                    │
│  query:   0.85 █████████████████████░  (stable)                     │
│  error:   0.60 ████████████░░░░░░░░░░  (degrading) ← ALERT         │
│  response: N/A  no data                                             │
│  efficiency: 0.88 ██████████████████░░  (improving)                 │
│                                                                     │
│  Weakest dimension: error (0.60)                                    │
│  Action: Developer investigates error recovery failures             │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │
                          Developer reviews
                          (human decision)
                                   │
                    ┌──────────────┼──────────────┐
                    ▼              ▼              ▼
        ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
        │   ZONE 2     │ │   ZONE 3     │ │   ZONE 1     │
        │  Knowledge   │ │  Parameters  │ │  Core Prompt │
        │  Addition    │ │  Adjustment  │ │  Change      │
        │              │ │              │ │              │
        │ Human writes │ │ Arithmetic:  │ │ Human writes │
        │ new fact     │ │ threshold    │ │ new rule     │
        │ Append-only  │ │ ±1 step      │ │ Full code    │
        │ Max 500 tok  │ │ Hard bounds  │ │ review       │
        │ per topic    │ │ No LLM       │ │ Regression   │
        │              │ │              │ │ tested       │
        └──────┬───────┘ └──────┬───────┘ └──────┬───────┘
               │                │                │
               └────────────────┼────────────────┘
                                │
                                ▼
                    ┌──────────────────────┐
                    │   RATCHET CHECK      │
                    │                      │
                    │ Replay 20 decisions  │
                    │ per dimension against │
                    │ new config            │
                    │                      │
                    │ All dims ≥ snapshot?  │
                    │  YES → advance       │
                    │  NO  → revert        │
                    └──────────────────────┘
```

---

## 15. CONCERN: THE SILENT 200 OK PROBLEM (Wrong Endpoint, Correct HTTP Response)

### 15.1 The Problem

Today, the entire pipeline determines "success" at one level: **did the HTTP call return 200?**

```
User: "Show me unpaid invoices for job 20821"

WHAT SHOULD HAPPEN:
  LLM calls search_invoices(filters={"Jobs.ID": "20821", "Status": "Unpaid"})
  → Returns invoice data → User sees invoices

WHAT COULD HAPPEN (silent failure):
  LLM calls search_jobs(filters={"ID": "20821"})
  → Returns job data (200 OK) → LLM says "here's the job info" → User sees job data
  → System records: success=true, duration=1200ms, tokens=850
  → Nobody knows this was wrong
```

After full code analysis, here is the validation chain today:

| Layer | What It Checks | What It Misses |
|-------|---------------|----------------|
| MCP Server `ToolExecutor` | Tool didn't crash | Wrong tool, irrelevant data |
| MCP Client `executeTool()` | HTTP 200 from server | Wrong data in response |
| `executor.js` loop | LLM stopped iterating | LLM satisfied with wrong data |
| `_extract_tool_data()` | `success !== false` | Data relevance to question |
| `apply_post_filters()` | 8 hardcoded keyword patterns | Wrong tool entirely |
| System prompt instruction | "verify data FULLY answers the question" | LLM may not notice |

**There is zero programmatic verification that the returned data semantically answers the question.**

### 15.2 Why This Is Hard To Detect

The "wrong tool, right 200" failure is invisible because:

1. **No error signal**: The API returned valid data. No exception. No 4xx/5xx.
2. **The LLM is satisfied**: Smaller models (gpt-4.1-mini) often don't notice the mismatch because the data is structurally valid.
3. **The presenter formats it beautifully**: Job data in a nice table looks "correct" to the system.
4. **The user may not report it**: They might just think "the system doesn't have that data" and move on.

### 15.3 Solution: Intent-Response Alignment Check

This does NOT require an LLM. It's a lightweight, deterministic check.

#### Step 1: Tag every tool call with the user's intent category

The intent analyzer already classifies every query. We already have:
```python
intent_result = {
    "intent": "invoice_crud",     # ← what the user wanted
    "agent": "invoice",
    "action": "query",
    "confidence": 0.92
}
```

#### Step 2: Define expected tool families per intent

```python
# This is a static lookup table — not LLM-generated, not adaptive.
# It answers: "if the user asked about X, which tools SHOULD be called?"

INTENT_TOOL_FAMILIES = {
    "schedule_query": {
        "expected": {"get_schedules", "get_schedule_details", "list_employees"},
        "suspicious": {"search_jobs", "search_invoices", "get_invoice_details"},
    },
    "schedule_crud": {
        "expected": {"get_schedules", "get_schedule_details", "list_employees",
                     "get_job_sections", "get_job_section_cost_centres",
                     "create_schedule", "update_schedule", "delete_schedule"},
        "suspicious": {"search_invoices", "get_invoice_details"},
    },
    "invoice_crud": {
        "expected": {"search_invoices", "get_invoice_details", "create_invoice",
                     "search_jobs", "get_job_sections", "get_job_section_cost_centres"},
        "suspicious": {"get_schedules", "create_schedule"},
    },
    "workorder_crud": {
        "expected": {"get_contractor_jobs_by_cost_centre", "get_contractor_job_details",
                     "create_contractor_job", "search_jobs", "list_contractors"},
        "suspicious": {"get_schedules", "search_invoices"},
    },
    "general_query": {
        "expected": None,  # Any tool is acceptable for general queries
        "suspicious": set(),
    },
}
```

#### Step 3: After the tool execution loop completes, check alignment

```python
def check_intent_tool_alignment(
    intent: str,
    tools_called: list[str],
) -> dict:
    """
    Deterministic check. No LLM involved.
    Returns alignment status for the decision journal.
    """
    family = INTENT_TOOL_FAMILIES.get(intent)
    if not family or family["expected"] is None:
        return {"aligned": True, "flag": None}

    called_set = set(tools_called)
    expected = family["expected"]
    suspicious = family["suspicious"]

    # Case 1: Called ONLY suspicious tools, none of the expected ones
    only_suspicious = called_set.issubset(suspicious) and not called_set.intersection(expected)
    if only_suspicious:
        return {
            "aligned": False,
            "flag": "wrong_tool_family",
            "detail": f"Intent={intent} but only called {called_set}, expected one of {expected}",
        }

    # Case 2: Called at least one expected tool — probably fine
    if called_set.intersection(expected):
        return {"aligned": True, "flag": None}

    # Case 3: Called tools outside both sets — unknown, flag for review
    return {
        "aligned": None,  # Unknown — not clearly wrong, not clearly right
        "flag": "unknown_tool_pattern",
        "detail": f"Intent={intent}, called {called_set}, not in expected or suspicious sets",
    }
```

#### Step 4: Record in the decision journal

```python
# In chat.py, after MCP execution completes:

alignment = check_intent_tool_alignment(
    intent=intent_result["intent"],
    tools_called=[t["name"] for t in tracker.tool_history],
)

await record_decision(
    request_id=req_id,
    dimension="tool",
    decision_type="intent_tool_alignment",
    decision_output=json.dumps(alignment),
    outcome="success" if alignment["aligned"] else "misaligned",
    outcome_signal="deterministic_check",
)
```

**This catches the "search_jobs when user asked for invoices" scenario without any LLM involvement.** It's a static table lookup that takes <1ms.

#### What it does NOT do

- It does NOT block the response. If tools are misaligned, the user still gets data.
  The flag goes to the journal for analysis. Blocking would create false-positive friction.
- It does NOT judge data quality. "You called search_invoices correctly but the filter
  was wrong" is a different failure mode (harder, requires schema-level validation).
- It does NOT use an LLM to decide correctness.

#### What the developer sees in the weekly report

```
INTENT-TOOL ALIGNMENT (last 7 days):
  schedule_query:  142 requests, 138 aligned (97%), 4 misaligned
  invoice_crud:     31 requests,  28 aligned (90%), 3 misaligned
  general_query:   218 requests, all N/A (any tool acceptable)

  MISALIGNED CASES (7 total):
  - 3x invoice_crud → only search_jobs called (no invoice tools)
  - 2x schedule_query → search_contacts called first (unnecessary)
  - 2x schedule_query → get_job_sections called without get_schedules

  RECOMMENDATION: Review system prompt rule for invoice queries.
  The LLM may not be following the cross-entity lookup instruction.
```

---

## 16. CONCERN: DEVELOPER WORKFLOW AND MODEL MISMATCH

### 16.1 The Problem Restated

> "The model I use for coding (Claude Opus) is different from the model configured
> in the product (gpt-4.1-mini). If an analysis recommends changes based on how
> gpt-4.1-mini behaves, those recommendations might not account for model-specific
> quirks. And who does the analysis? When? How?"

### 16.2 The Critical Distinction: Analysis vs. Execution

```
ANALYSIS happens on YOUR data (decision journal).
  → Uses SQL queries. No LLM involved. Model-agnostic.
  → "30% of entity resolutions needed clarification" is a FACT from the database.
  → It is true regardless of which model you use for coding or for the product.

EXECUTION happens with the PRODUCT's model (gpt-4.1-mini).
  → The prompts are written for the product model.
  → Threshold adjustments are validated against the product model's behavior.
  → The ratchet replays decisions as the product model would handle them.
```

The analysis never touches or depends on Claude Opus (your coding model).
The recommendations are about the product's behavior, measured from the product's model.

### 16.3 The Developer Workflow (Concrete)

```
┌─────────────────────────────────────────────────────────────┐
│                    WEEKLY RHYTHM                             │
│                                                              │
│  MONDAY MORNING (10 minutes):                                │
│                                                              │
│  1. Open the analytics dashboard (already exists at          │
│     GET /api/auth/analytics)                                 │
│                                                              │
│  2. Check the Capability Radar                               │
│     GET /api/auth/radar?days=7                               │
│                                                              │
│     Response:                                                │
│     {                                                        │
│       "routing":    {"score": 0.92, "trend": "stable"},      │
│       "entity":     {"score": 0.71, "trend": "degrading"},   │ ← attention
│       "tool":       {"score": 0.95, "trend": "stable"},      │
│       "query":      {"score": 0.85, "trend": "stable"},      │
│       "error":      {"score": 0.60, "trend": "degrading"},   │ ← attention
│       "response":   {"score": null, "trend": "no_data"},     │
│       "efficiency": {"score": 0.88, "trend": "improving"},   │
│     }                                                        │
│                                                              │
│  3. IF any dimension is "degrading" or below floor:          │
│     → Drill into that dimension                              │
│                                                              │
│     GET /api/auth/journal?dimension=entity&outcome=failure   │
│       &days=7                                                │
│                                                              │
│     Response: List of failed entity decisions with:          │
│       - decision_type (fuzzy_match, crossroads_disambig)     │
│       - confidence scores                                    │
│       - outcome_signal (clarification_needed, user_corrected)│
│       - agent_name, tool_name                                │
│                                                              │
│  4. IF pattern is obvious:                                   │
│     → Apply fix (Zone 2 knowledge addition or Zone 3         │
│       threshold tweak)                                       │
│     → Run ratchet check                                      │
│     → Deploy if passes                                       │
│                                                              │
│  5. IF pattern is not obvious:                               │
│     → Flag for deeper investigation later in the week        │
│     → No change. The system continues working as-is.         │
│                                                              │
│  TOTAL TIME: 10-15 minutes if nothing is degrading.          │
│              30-60 minutes if a fix is needed.               │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 16.4 The Report Format (What You Actually See)

The report is NOT an LLM-generated narrative. It is structured data from SQL queries:

```
═══════════════════════════════════════════════════
  OPTIFICIAL CAPABILITY REPORT — Week of 2026-03-01
═══════════════════════════════════════════════════

  RADAR SCORES (30-day rolling):
  ──────────────────────────────
  routing:     0.92  ████████████████████░░  stable
  entity:      0.71  ██████████████░░░░░░░░  ↓ DEGRADING (was 0.78)
  tool:        0.95  ███████████████████████  stable
  query:       0.85  █████████████████████░  stable
  error:       0.60  ████████████░░░░░░░░░░  ↓ DEGRADING (was 0.67)
  response:    N/A   no feedback data
  efficiency:  0.88  ██████████████████░░░░  ↑ improving

  BALANCE RATIO: 0.95 / 0.60 = 1.58 ← ABOVE 1.5 LIMIT
  ACTION: Freeze improvements to routing/tool. Focus on error/entity.

  TOP FAILURE PATTERNS (entity dimension):
  ──────────────────────────────────────
  1. fuzzy_match → clarification_needed: 23 occurrences
     Most common: staff names with score 75-79 (just below auto-select)
     Orgs affected: #7 (14), #3 (6), #12 (3)

  2. crossroads_disambig → user_corrected: 8 occurrences
     Most common: LLM selected wrong candidate, user picked another
     Pattern: candidates had similar scores (within 5 points)

  TOP FAILURE PATTERNS (error dimension):
  ──────────────────────────────────────
  1. 422 "Section not valid for Job": 11 occurrences
     Agent: schedule (9), workorder (2)
     Recovery: system retried with same section (wrong)
     Should: re-resolve section for the specific job

  2. 422 "Cost centre already 100% claimed": 5 occurrences
     Agent: invoice
     Recovery: informed user (correct)

  INTENT-TOOL ALIGNMENT:
  ──────────────────────
  Overall alignment: 94%
  Misaligned cases: 12 / 198
  Most common: invoice queries calling search_jobs only (4 cases)

  ZONE 3 PARAMETER STATUS:
  ────────────────────────
  fuzzy_auto_select_threshold: 80 (no change recommended)
  fuzzy_score_gap: 20 (no change recommended)
  max_resolution_attempts: 3 (consider raising to 4 —
    exhaustion rate is 15%, above 10% target)

═══════════════════════════════════════════════════
```

### 16.5 Model Mismatch Is Not A Problem

Because:

```
1. The REPORT is computed from SQL queries on the decision journal.
   → "23 fuzzy_match failures this week" is a COUNT(*) query.
   → It doesn't matter if you're reading it on a machine running Claude,
     GPT, or a calculator.

2. The RECOMMENDATIONS are deterministic rules, not LLM opinions.
   → "threshold 80 causes 23 failures with scores 75-79" is arithmetic.
   → "Consider lowering to 78" is a rule: if >20% of failures are within
     2 points of threshold, suggest lowering by 2 points.
   → No LLM involved.

3. The RATCHET VALIDATION runs against the PRODUCT model.
   → When you replay 20 decisions to check a threshold change,
     those decisions are replayed using gpt-4.1-mini (or whatever
     LLM_MODEL is set in .env), not your coding model.
   → The validation tests what the PRODUCT will actually do.

4. Your coding model (Claude Opus) is used for:
   → Writing the fix code (if Zone 2 knowledge addition)
   → Reviewing the ratchet results
   → Deciding whether to deploy
   → These are HUMAN activities assisted by your coding model.
     The coding model is your tool, not part of the product pipeline.
```

---

## 17. CONCERN: CODE CHANGES VS. PROMPT-ONLY CHANGES

### 17.1 The Doomsday Scenario You're Worried About

> "If the improvement system makes structural code changes (modifying Python/JS logic,
> changing function signatures, altering control flow), a bad change could cascade
> through the system and cause a doomsday scenario — the product breaks in ways
> that aren't obvious until users are affected."

### 17.2 The Answer: ZERO Code Changes. Ever.

**The improvement system makes NO code changes. None. Zero. Not one line.**

Here is the complete, exhaustive list of what the improvement system can change:

```
WHAT CAN CHANGE (and how):
──────────────────────────

1. ZONE 3: Numeric parameters in a config table
   ┌──────────────────────────────────┬─────────┬─────────┬──────┐
   │ Parameter                        │ Current │ Min     │ Max  │
   ├──────────────────────────────────┼─────────┼─────────┼──────┤
   │ fuzzy_auto_select_threshold      │ 80      │ 70      │ 95   │
   │ fuzzy_score_gap                  │ 20      │ 10      │ 40   │
   │ max_resolution_attempts          │ 3       │ 2       │ 5    │
   │ conversation_history_depth       │ 6       │ 4       │ 10   │
   │ tool_cache_ttl_minutes           │ 5       │ 2       │ 10   │
   └──────────────────────────────────┴─────────┴─────────┴──────┘

   Storage: SQLite table or JSON config file. NOT in Python/JS source code.
   How: Pure arithmetic. if accuracy_dropped: value += 1 (within bounds).
   Risk: Minimal. Even at extreme bounds, the system works — just more
         or less conservatively. A threshold of 95 means "always ask user."
         A threshold of 70 means "auto-select more aggressively." Neither
         breaks the system.

2. ZONE 2: Domain knowledge text appended to _DOMAIN_KNOWLEDGE
   Example: A developer adds one line to _DOMAIN_KNOWLEDGE["resolution_patterns"]:
     "8. 422 with 'Section not valid' → re-resolve section for the given Job ID"

   This is a TEXT addition to an existing dictionary.
   It does NOT change:
   - Function signatures
   - Control flow
   - Import statements
   - Class definitions
   - API endpoints
   - Database schema
   - Frontend components

   It changes: One string value in one dictionary, which gets injected into
   a crossroads system prompt. If the string is wrong, the crossroads LLM
   might give a bad suggestion — but the same fallback mechanisms (max 3
   attempts, deterministic fallback, user clarification) still work.


WHAT CAN NEVER CHANGE (by the improvement system):
───────────────────────────────────────────────────

✗ Python source files (.py)
  - No function added, removed, or modified
  - No class changed
  - No import added
  - No control flow altered
  - No exception handling changed

✗ JavaScript source files (.js)
  - No route added
  - No provider modified
  - No executor logic changed

✗ Frontend files (.jsx, .css)
  - No component modified
  - No state management changed

✗ Database schema
  - No tables added or modified (except the decision_journal table
    which is created ONCE during Phase 1 setup)

✗ API endpoints
  - No new endpoints (except /api/auth/radar and /api/auth/journal
    added ONCE during Phase 1)

✗ MCP tool definitions
  - No tool added, removed, or parameter schema changed

✗ Configuration files (.env, package.json, requirements.txt)
  - No dependencies added or removed
```

### 17.3 Why This Is Sufficient

You might think: "If you can't change code, how do you actually improve?"

The answer: **the product's intelligence lives in prompts and thresholds, not in code.**

```
Code (static structure):          Prompts + Thresholds (tunable behavior):
─────────────────────────         ──────────────────────────────────────
chat.py routes requests           Intent analyzer decides WHICH agent
entity_resolver.py resolves       Crossroads decides HOW to resolve
crossroads.py calls LLM           Domain knowledge tells WHAT to try
mcp_executor.py calls tools       Fuzzy match threshold decides WHEN to auto-select

The code is PLUMBING.             The prompts are BRAINS.
Plumbing rarely needs changing.   Brains need tuning.
```

Think of it like a car: the improvement system adjusts tire pressure (Zone 3)
and updates the GPS map data (Zone 2). It never modifies the engine, transmission,
or brakes (Zone 1 / code).

### 17.4 The Doomsday Protection Stack

Even for the things that CAN change, multiple layers prevent catastrophe:

```
Layer 1: BOUNDED RANGES
  Even a "wrong" threshold adjustment is harmless.
  fuzzy_auto_select_threshold at 95 = "ask user for everything" (annoying, not broken)
  fuzzy_auto_select_threshold at 70 = "auto-select more aggressively" (more errors, not broken)
  The system FUNCTIONS correctly at any value within bounds.

Layer 2: ONE-STEP-PER-CYCLE
  Parameters change by 1-2 points per analysis cycle (weekly/monthly).
  You cannot go from 80 to 70 in one jump. It takes 10 cycles.
  Any regression is caught long before reaching extreme values.

Layer 3: RATCHET VALIDATION
  Every change is replayed against historical decisions.
  If ANY dimension regresses by >5%, the change is reverted.
  The product can only get better or stay the same.

Layer 4: HUMAN APPROVAL
  In the initial rollout, ALL changes (even Zone 3) require developer
  approval before taking effect. The system PROPOSES changes. It does
  not APPLY them.

Layer 5: GIT VERSION CONTROL
  Zone 2 additions go through normal code review (they're changes to
  Python dictionary values). Zone 3 adjustments are logged in the journal
  with before/after values. Every state is recoverable.

Layer 6: INSTANT ROLLBACK
  If something goes wrong despite all layers:
  - Zone 3: Set parameter back to previous value (one config update)
  - Zone 2: Remove the appended line (one git revert)
  - Neither requires redeploying code, restarting servers, or
    migrating databases
```

### 17.5 Comparison to the Doomsday Scenario

```
DOOMSDAY SCENARIO (code changes):
  Auto-system modifies entity_resolver.py
  → Changes resolve_staff() to skip fuzzy matching
  → All staff resolution fails silently
  → Schedules created for wrong people
  → 200 OK on all API calls (wrong people, but valid staff IDs)
  → Customer notices Monday morning: entire week's schedule is wrong
  → Recovery: identify all wrong schedules, delete, recreate manually
  → Downtime: hours. Trust damage: severe.

WORST CASE WITH THIS SYSTEM (parameter changes):
  Threshold lowered from 80 to 78
  → 2 additional staff names auto-selected per week that shouldn't have been
  → Those 2 cases: system created schedule for "John Smith" instead of "John Smithson"
  → User notices within minutes: "wrong John"
  → Recovery: user says "wrong person, use John Smithson" → system corrects
  → Journal records: outcome=corrected, outcome_signal=user_correction
  → Next week's radar: entity accuracy dipped by 1%
  → Ratchet: reverts threshold to 80
  → Net impact: 2 corrected records over 1 week. Zero data loss.
```

---

## 18. REVISED FINAL SUMMARY

### What This System IS

```
1. A MEASUREMENT LAYER (decision journal)
   - Records what happens at every decision point
   - Fire-and-forget, PII-sanitized, never blocks requests
   - Pure instrumentation — zero behavior change

2. A SCORING ENGINE (capability radar)
   - SQL + arithmetic — no LLM
   - Computes 7 dimension scores from journal data
   - Detects degradation trends

3. A REPORTING TOOL (weekly report)
   - Structured data, not LLM narrative
   - Shows failure patterns, alignment checks, parameter status
   - Developer reads it in 10 minutes

4. A PARAMETER TUNING MECHANISM (Zone 3)
   - 5 numeric values with hard bounds
   - Adjusted by ±1 step per cycle
   - Validated by ratchet before activation
   - Reversible in seconds

5. A KNOWLEDGE MANAGEMENT PROCESS (Zone 2)
   - Developer writes domain knowledge additions
   - Append-only, token-capped
   - Normal code review + git
```

### What This System IS NOT

```
1. NOT an auto-coder
   - Makes ZERO changes to Python, JavaScript, or any source code
   - Makes ZERO changes to database schema (after initial setup)
   - Makes ZERO changes to API endpoints or MCP tools

2. NOT an LLM-in-the-loop improver
   - No LLM analyzes the journal
   - No LLM writes prompts
   - No LLM judges improvement quality
   - Analysis is SQL queries. Recommendations are arithmetic rules.

3. NOT an auto-pilot
   - Every change requires developer review
   - Every change is validated by ratchet
   - Every change is reversible

4. NOT a prompt rewriter
   - Zone 1 prompts are FROZEN
   - Zone 2 knowledge is APPENDED (never overwrites)
   - Zone 3 parameters are NUMBERS (not text)
```

---

## 19. REVISED: QUALITATIVE + QUANTITATIVE ANALYSIS (LLM as Diagnostician)

### 19.1 Why Pure SQL Is Insufficient

The previous sections proposed SQL-only analysis. That was wrong. Here's why:

```
WHAT SQL CAN TELL YOU:
  "Entity resolution failed 30% of the time this week."
  "Tool alignment was 94%."
  "Error recovery succeeded 60% of the time."

WHAT SQL CANNOT TELL YOU:
  "The system searched for jobs when the user asked about invoice costs
   because the LLM interpreted 'job costs' as a job entity lookup instead
   of an invoice filtered by job ID."

  "The entity resolver picked 'John Smith' (score 82) over 'John Smithson'
   (score 78) because the crossroads disambiguation prompt doesn't account
   for partial surname matches — it only checks the overall fuzzy score."

  "The schedule creation failed with 422 because the system resolved the
   section correctly but used the cost centre from a DIFFERENT section
   of the same job — the resolution order was wrong."
```

These are **causal diagnoses**. They require reading the actual request flow —
the user's question, the intent classification, the tool calls made, the
parameters used, the data returned, and the final answer — and reasoning
about whether the chain of decisions was correct.

SQL gives you the **WHERE** (which dimension is failing).
LLM gives you the **WHY** (what specifically went wrong in the reasoning chain).

You need both.

### 19.2 The Critical Constraint: Read-Only LLM

The previous concern about LLM-in-the-loop was valid. The solution is not
"exclude LLM from analysis" but "ensure the LLM can only READ, never WRITE."

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│  THE ANALYSIS LLM:                                          │
│                                                             │
│  CAN:                                                       │
│    ✓ Read decision journal entries                          │
│    ✓ Read the request trace (tool calls, params, results)   │
│    ✓ Read the user's original question                      │
│    ✓ Reason about whether the chain of decisions was correct│
│    ✓ Produce a written diagnosis explaining what went wrong │
│    ✓ Suggest a category of fix (knowledge gap, threshold    │
│      issue, prompt ambiguity, tool description gap)         │
│                                                             │
│  CANNOT:                                                    │
│    ✗ Modify any prompt text                                 │
│    ✗ Modify any threshold value                             │
│    ✗ Modify any code                                        │
│    ✗ Write domain knowledge entries                         │
│    ✗ Execute any tool or API call                           │
│    ✗ Access the production system                           │
│                                                             │
│  It is a CONSULTANT that writes a report.                   │
│  The developer is the DECISION-MAKER that acts on it.       │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 19.3 The Two-Phase Analysis Architecture

```
PHASE A: QUANTITATIVE DETECTION (SQL — automated, runs daily)
─────────────────────────────────────────────────────────────

  Input:  decision_journal table
  Output: Capability Radar scores + anomaly flags

  "Entity resolution: 0.71, DEGRADING. 23 failures this week."
  "Tool alignment: 94%. 12 misaligned cases."
  "Error recovery: 0.60, DEGRADING. 16 unrecovered errors."

  This runs automatically. No LLM. No cost. Pure arithmetic.
  It answers: "WHERE are the problems?"


PHASE B: QUALITATIVE DIAGNOSIS (LLM — on-demand, developer-triggered)
─────────────────────────────────────────────────────────────────────

  Input:  The flagged failure cases from Phase A + their full request traces
  Output: Causal diagnosis report

  ONLY runs when:
  1. Developer clicks "Analyze" on a specific set of failures, OR
  2. A dimension drops below its floor threshold (automated trigger)

  It answers: "WHAT went wrong and WHY?"
```

### 19.4 What the Request Trace Must Capture

Today, the richest data structure — `RequestTracker` — is thrown away after each
request. To enable qualitative analysis, we need to persist a **request trace**:

```sql
CREATE TABLE request_traces (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id      TEXT NOT NULL,
    org_id          INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    created_at      TEXT DEFAULT (datetime('now')),

    -- The question (PII-sanitized)
    user_question   TEXT,               -- sanitized via pii_filter

    -- Routing decision
    intent          TEXT,               -- schedule_crud, invoice_crud, etc.
    intent_confidence REAL,
    intent_agent    TEXT,               -- schedule, invoice, workorder, null
    intent_action   TEXT,               -- create, update, delete, query
    follow_up       BOOLEAN,

    -- Tool call sequence (the flight recorder)
    tool_sequence   TEXT,               -- JSON array: [{tool, params_summary, success, result_summary, ms}]

    -- Final outcome
    outcome         TEXT,               -- success, failure, partial, corrected
    outcome_signal  TEXT,               -- api_success, api_error, user_correction, user_rephrase

    -- Alignment check
    tool_alignment  TEXT,               -- aligned, misaligned, unknown

    -- For agent path: what was resolved
    resolved_entities TEXT,             -- JSON: {staff_id, job_id, section_id, ...} (IDs only, no names)

    -- Response summary
    response_type   TEXT,               -- table, text, error, clarification
    data_count      INTEGER,            -- rows returned or operations performed

    -- Performance
    total_tool_calls INTEGER,
    total_llm_rounds INTEGER,
    duration_ms     INTEGER,
    total_tokens    INTEGER
);
```

**Key design decisions:**

```
1. PII SANITIZATION: user_question is sanitized before storage.
   Names, addresses, and personal details are stripped.
   What survives: intent keywords, entity references by type
   ("show schedules for [STAFF] on [DATE]"), structural patterns.

2. tool_sequence stores SUMMARIES, not full API responses.
   Each entry is ~100 bytes: {tool: "search_jobs", params: {filters: {Name: "%river%"}},
   success: true, result_summary: "3 jobs returned", ms: 450}
   NOT the full Simpro response (which could be 50KB).

3. resolved_entities stores IDs ONLY.
   {staff_id: 42, job_id: 20990, section_id: 1145}
   NOT names. This is enough for diagnosis without PII exposure.

4. This table is the REQUEST TRACKER that already exists in memory —
   we're just persisting it instead of throwing it away.
```

### 19.5 The Qualitative Analysis Prompt

When the developer triggers a diagnosis (or when a dimension hits floor threshold),
the analysis LLM receives a batch of failed request traces and produces a diagnosis:

```python
DIAGNOSIS_SYSTEM_PROMPT = """
You are a quality analyst for an AI-powered construction back-office platform.

You will receive a batch of FAILED request traces. Each trace contains:
- The user's question (sanitized)
- The intent classification (what the system thought the user wanted)
- The tool call sequence (what tools were called, with what parameters, what they returned)
- The outcome (how it failed — API error, user correction, misalignment, etc.)

Your job is to identify the ROOT CAUSE of each failure and categorize it.

FAILURE CATEGORIES:
1. ROUTING_ERROR — The intent analyzer sent the request to the wrong agent/path
2. TOOL_SELECTION — The LLM called the wrong MCP tool for this query
3. PARAMETER_ERROR — The right tool was called but with wrong parameters (wrong filters, wrong columns, wrong date format)
4. ENTITY_MISMATCH — The entity resolver picked the wrong entity (wrong person, wrong job, wrong section)
5. KNOWLEDGE_GAP — The system doesn't know a fact it needs (e.g., which section belongs to which job)
6. THRESHOLD_ISSUE — The fuzzy match threshold is too aggressive or too conservative for this case
7. PROMPT_AMBIGUITY — The system prompt instruction is unclear, causing the LLM to misinterpret
8. API_LIMITATION — The Simpro/MyOB API doesn't support what the user needs (not a system bug)
9. USER_AMBIGUITY — The user's question is genuinely unclear (not a system bug)

For each failure, provide:
- failure_id: the request_id
- category: one of the 9 above
- root_cause: 1-2 sentence explanation of what specifically went wrong
- evidence: which part of the trace proves this (e.g., "tool_sequence[2] called search_jobs
  with no filters when the user specified a job name")
- fix_zone: which zone could address this (zone_2_knowledge, zone_3_parameter, zone_1_prompt, none)
- fix_suggestion: a specific, actionable suggestion (NOT prompt text — a description of what to change)

Then provide a SUMMARY:
- Most common failure category
- Whether failures are concentrated in one dimension or spread across many
- Whether there's a systemic pattern (same root cause appearing in multiple failures)

Return ONLY valid JSON.
"""
```

### 19.6 What the Developer Actually Sees (Revised)

```
═══════════════════════════════════════════════════════════════
  OPTIFICIAL CAPABILITY REPORT — Week of 2026-03-01
═══════════════════════════════════════════════════════════════

  PHASE A: QUANTITATIVE RADAR (automated, SQL)
  ─────────────────────────────────────────────
  routing:     0.92  ████████████████████░░  stable
  entity:      0.71  ██████████████░░░░░░░░  ↓ DEGRADING
  tool:        0.90  ██████████████████░░░░  stable
  query:       0.85  █████████████████████░  stable
  error:       0.60  ████████████░░░░░░░░░░  ↓ DEGRADING
  efficiency:  0.88  ██████████████████░░░░  ↑ improving

  BALANCE RATIO: 1.58 ← ABOVE 1.5 LIMIT
  DEGRADING DIMENSIONS: entity, error

  ─────────────────────────────────────────────

  PHASE B: QUALITATIVE DIAGNOSIS (LLM analysis, developer-triggered)
  ──────────────────────────────────────────────────────────────────

  Analyzed 23 entity failures + 16 error recovery failures:

  TOP ROOT CAUSES (entity, 23 failures):
  ┌────────────────────┬───────┬───────────────────────────────────────────┐
  │ Category           │ Count │ Pattern                                   │
  ├────────────────────┼───────┼───────────────────────────────────────────┤
  │ THRESHOLD_ISSUE    │ 14    │ Staff names scoring 75-79, just below     │
  │                    │       │ auto-select (80). All were unique matches │
  │                    │       │ — no ambiguity. Org #7 has 8 of these.   │
  │                    │       │ Fix: Zone 3, lower threshold to 77.       │
  ├────────────────────┼───────┼───────────────────────────────────────────┤
  │ ENTITY_MISMATCH    │ 5     │ LLM selected "John Smith" (score 82)     │
  │                    │       │ over "John Smithson" (score 78) when user │
  │                    │       │ said "Smithson". The crossroads prompt    │
  │                    │       │ doesn't weight suffix matches higher.     │
  │                    │       │ Fix: Zone 2, add to ambiguous_match       │
  │                    │       │ domain knowledge: "When user includes a   │
  │                    │       │ surname, prioritize full surname match    │
  │                    │       │ over partial first-name match."           │
  ├────────────────────┼───────┼───────────────────────────────────────────┤
  │ KNOWLEDGE_GAP      │ 4     │ Section resolution failed because system  │
  │                    │       │ doesn't know that Org #7 uses "General"   │
  │                    │       │ as default section name for all jobs.     │
  │                    │       │ Fix: Zone 2, add resolution pattern.      │
  └────────────────────┴───────┴───────────────────────────────────────────┘

  TOP ROOT CAUSES (error recovery, 16 failures):
  ┌────────────────────┬───────┬───────────────────────────────────────────┐
  │ Category           │ Count │ Pattern                                   │
  ├────────────────────┼───────┼───────────────────────────────────────────┤
  │ KNOWLEDGE_GAP      │ 11    │ 422 "Section not valid for Job" — system  │
  │                    │       │ retries with same section. Doesn't know   │
  │                    │       │ to re-resolve section for the specific    │
  │                    │       │ job. Same pattern across schedule (9) and │
  │                    │       │ workorder (2) agents.                     │
  │                    │       │ Fix: Zone 2, add error_recovery pattern.  │
  ├────────────────────┼───────┼───────────────────────────────────────────┤
  │ PARAMETER_ERROR    │ 3     │ Schedule creation passed date as          │
  │                    │       │ "01/03/2026" instead of "2026-03-01".     │
  │                    │       │ The API hint mentions ISO format but the  │
  │                    │       │ LLM sometimes outputs DD/MM/YYYY.        │
  │                    │       │ Fix: Zone 2, strengthen date format hint. │
  ├────────────────────┼───────┼───────────────────────────────────────────┤
  │ API_LIMITATION     │ 2     │ User tried to change contractor on an     │
  │                    │       │ existing work order. Simpro PATCH doesn't │
  │                    │       │ support changing the Contractor field.    │
  │                    │       │ Fix: None (inform user this isn't         │
  │                    │       │ possible via the system).                 │
  └────────────────────┴───────┴───────────────────────────────────────────┘

  SYSTEMIC PATTERN:
  The "Section not valid for Job" error (11 cases) is the single biggest
  source of failures across two agents. It accounts for 28% of all
  failures this week. This is a KNOWLEDGE_GAP in the error_recovery
  domain knowledge — adding one resolution pattern would fix 11 cases.

═══════════════════════════════════════════════════════════════
```

### 19.7 The Isolation Boundary

The key architectural principle: **the analysis LLM operates in a completely
separate process from the production system.**

```
┌─────────────────────────┐         ┌─────────────────────────┐
│   PRODUCTION SYSTEM     │         │   ANALYSIS SYSTEM       │
│                         │         │                         │
│  User requests          │         │  Developer-triggered    │
│  Intent analyzer        │         │  or scheduled           │
│  Entity resolver        │         │                         │
│  Crossroads             │  WRITE  │  Reads from:            │
│  MCP tools              │ ──────► │  - decision_journal     │
│  Tool execution         │         │  - request_traces       │
│                         │         │                         │
│  Writes to:             │         │  Uses:                  │
│  - decision_journal     │         │  - Analysis LLM (any    │
│  - request_traces       │         │    model, doesn't       │
│                         │         │    have to match         │
│  NEVER reads from       │         │    production model)    │
│  analysis output.       │         │                         │
│  Has no connection to   │         │  Outputs to:            │
│  the analysis system.   │         │  - Diagnosis report     │
│                         │         │    (text/JSON)          │
│                         │         │  - Radar dashboard      │
│                         │         │                         │
│                         │         │  NEVER writes to:       │
│                         │         │  - Production prompts   │
│                         │         │  - Production config    │
│                         │         │  - Production code      │
│                         │         │  - Production database  │
│                         │         │    (except its own       │
│                         │         │    analysis tables)     │
│                         │         │                         │
└─────────────────────────┘         └─────────────────────────┘
         │                                    │
         │                                    │
         │         HUMAN DEVELOPER            │
         │         ┌──────────────┐           │
         │         │              │           │
         └────────►│  Reads both  │◄──────────┘
                   │  Decides     │
                   │  Acts        │
                   │              │
                   │  Makes Zone  │
                   │  2/3 changes │
                   │  via git     │
                   └──────────────┘
```

**The production system has NO import, NO API call, NO database connection
to the analysis system.** They share only one thing: the same SQLite database
for reading journal/trace data. The analysis system reads. The production
system writes. Neither crosses the boundary in the other direction.

This means:
- If the analysis LLM hallucinates a bad diagnosis → the developer reads it,
  recognizes it's wrong, ignores it. No production impact.
- If the analysis LLM is unavailable → the quantitative radar still works
  (SQL only). The developer just doesn't get qualitative explanations.
- If the analysis model changes → no production impact. The analysis model
  is a diagnostic tool, like a microscope. Changing the microscope doesn't
  change the specimen.

### 19.8 The Analysis LLM Model Choice

Since the analysis LLM is completely isolated from production, it has
different requirements:

```
PRODUCTION LLM (gpt-4.1-mini):
  Optimized for: speed, cost, tool-calling reliability
  Constraint: runs on every user request (cost-sensitive)
  Context: ~8K tokens (prompt + history + tool results)

ANALYSIS LLM (can be ANY model):
  Optimized for: reasoning depth, pattern recognition
  Constraint: runs weekly or on-demand (cost-insensitive)
  Context: can use 100K+ context models (analyze many traces at once)

  Good choices:
  - Claude Opus (your coding model) — excellent at reasoning over
    structured traces, already available
  - GPT-4 — strong at JSON analysis
  - Any model with large context window

  The analysis model does NOT need to match the production model.
  It's reading traces, not generating tool calls.
```

### 19.9 The Full Analysis Pipeline

```
Step 1: DETECT (automated, daily, SQL)
   Capability Radar computation → dimension scores + trends
   Intent-tool alignment check → misalignment flags
   "Entity is at 0.71, degrading. 23 failures."

Step 2: SURFACE (automated, daily, SQL)
   Query request_traces for flagged failures
   Group by dimension, category, pattern
   "14 threshold issues, 5 entity mismatches, 4 knowledge gaps"

Step 3: DIAGNOSE (on-demand or auto-triggered, LLM)
   Feed failure traces to analysis LLM
   LLM reads tool sequences, params, outcomes
   LLM reasons about causal chains
   LLM categorizes each failure and suggests fix zone
   "John Smith vs Smithson: crossroads doesn't weight surname matches"

Step 4: REPORT (automated, combines Steps 1-3)
   Quantitative radar (Step 1) + Failure summary (Step 2) +
   Causal diagnoses (Step 3) = Complete weekly report

Step 5: DECIDE (human, developer)
   Developer reads report, decides which fixes to apply
   Applies Zone 2/3 changes via normal git workflow
   Runs ratchet validation
   Deploys if passes

The LLM participates in Step 3 only.
Steps 1, 2, 4 are pure SQL/arithmetic.
Step 5 is pure human.
```

### 19.10 Why This Is Safe (Addressing the Original Prompt Drift Concern)

```
CONCERN: "LLM in the analysis loop could cause prompt drift"

ANSWER: The analysis LLM has NO WRITE PATH to production.

It cannot:
  ✗ Modify prompts (Zone 1 frozen, changes only via developer + git)
  ✗ Modify thresholds (Zone 3 changes only via arithmetic + developer approval)
  ✗ Modify domain knowledge (Zone 2 changes only via developer + git)
  ✗ Modify code (no code changes, ever)

It can only:
  ✓ Read request traces (historical data, already happened)
  ✓ Write a diagnosis report (text output, read by developer)

The worst case:
  Analysis LLM writes a wrong diagnosis → "The failure was caused by
  a prompt ambiguity in the resolution type" when it was actually a
  threshold issue.

  Result: Developer reads the diagnosis, checks the traces manually,
  realizes the LLM was wrong about the category but the threshold
  pattern is obvious from the data. Makes the right fix anyway.

  OR: Developer trusts the wrong diagnosis, makes a Zone 2 knowledge
  addition that doesn't address the real problem. The knowledge addition
  is harmless (it's additive, doesn't modify existing behavior). The
  real problem persists. Next week's radar still shows degradation.
  Developer investigates again with more traces. Eventually finds the
  real cause.

  No compound error. No drift. Just a one-week delay in fixing
  the real problem. The product continues working as before —
  it doesn't get WORSE from a wrong diagnosis.
```

---

*This document is a living analysis. Update it as the Decision Journal accumulates data and reveals patterns.*

*Generated from full codebase analysis of the Optificial platform — March 2026*
*Updated with Prompt Drift Protection analysis — March 2026*
*Updated with Silent 200 OK, Developer Workflow, and Code Change Scope analysis — March 2026*
*Updated with Qualitative + Quantitative dual analysis architecture — March 2026*
