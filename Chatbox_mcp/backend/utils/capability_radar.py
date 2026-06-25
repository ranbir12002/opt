# backend/utils/capability_radar.py
"""
Capability Radar — compute per-dimension scores from the decision journal.

Pure SQL + arithmetic. No LLM involved.

Each dimension gets a score 0-100 based on success rate and confidence.
The radar reveals which capabilities are strong and which need improvement.

Dimensions:
    routing         — How accurately does intent analysis classify queries?
    disambiguation  — How often does the system resolve ambiguity without user help?
    auto_selection  — How reliably does fuzzy matching pick the right entity?
    tool_alignment  — Do the MCP tools called match the detected intent?
    error_recovery  — How well does the system handle and recover from errors?
"""
from typing import Any, Dict, List, Optional

from auth.database import get_capability_radar_data


_DIMENSIONS = [
    "routing",
    "disambiguation",
    "auto_selection",
    "tool_alignment",
    "error_recovery",
]

_IMPROVEMENT_SUGGESTIONS = {
    "routing": "Review intent analysis accuracy. Check if frequently misrouted query patterns need system prompt clarification.",
    "disambiguation": "Reduce disambiguation rounds by tuning fuzzy match thresholds or adding domain knowledge to crossroads.",
    "auto_selection": "Review auto-selection thresholds. Lower the score gap if too many clear matches require clarification.",
    "tool_alignment": "The MCP LLM is selecting unexpected tools. Review system prompt tool-selection rules or tool descriptions.",
    "error_recovery": "High error rate. Check Simpro/MyOB API error patterns and improve error recovery in crossroads domain knowledge.",
}


def compute_radar(
    org_id: Optional[int] = None,
    days: int = 30,
) -> Dict[str, Any]:
    """
    Compute capability radar scores per dimension.

    Score formula: 70% success_rate + 30% avg_confidence, scaled to 0-100.
    Success_rate = success / (success + failure + clarification).
    Pending outcomes are excluded from scoring.

    Returns:
        {
            "dimensions": {
                "routing": {"score": 85, "total": 120, "success_rate": 0.92, ...},
                ...
            },
            "overall_score": 82,
            "days": 30,
            "total_decisions": 450,
        }
    """
    raw = get_capability_radar_data(org_id=org_id, days=days)

    # Pivot: dimension → {outcome → count, confidence accumulator}
    by_dim: Dict[str, Dict[str, Any]] = {}
    for row in raw:
        dim = row["dimension"]
        outcome = row["outcome"]
        if dim not in by_dim:
            by_dim[dim] = {
                "success": 0, "failure": 0, "clarification": 0, "pending": 0,
                "total": 0, "confidence_sum": 0.0, "confidence_count": 0,
            }
        by_dim[dim][outcome] = by_dim[dim].get(outcome, 0) + row["count"]
        by_dim[dim]["total"] += row["count"]
        if row["avg_confidence"]:
            by_dim[dim]["confidence_sum"] += row["avg_confidence"] * row["count"]
            by_dim[dim]["confidence_count"] += row["count"]

    dimensions = {}
    total_decisions = 0

    for dim in _DIMENSIONS:
        data = by_dim.get(dim)
        if not data or data["total"] == 0:
            dimensions[dim] = {
                "score": 0, "total": 0, "success_rate": 0.0,
                "avg_confidence": 0.0, "success": 0, "failure": 0, "clarification": 0,
            }
            continue

        total = data["total"]
        total_decisions += total

        # Exclude pending from success rate calculation
        scored = data.get("success", 0) + data.get("failure", 0) + data.get("clarification", 0)
        success_count = data.get("success", 0)
        success_rate = success_count / scored if scored > 0 else 0.0

        avg_conf = (
            data["confidence_sum"] / data["confidence_count"]
            if data["confidence_count"] > 0 else 0.0
        )

        # Score: 70% success rate + 30% average confidence, scaled 0-100
        score = int((success_rate * 0.7 + avg_conf * 0.3) * 100)
        score = max(0, min(100, score))

        dimensions[dim] = {
            "score": score,
            "total": total,
            "success_rate": round(success_rate, 3),
            "avg_confidence": round(avg_conf, 3),
            "success": success_count,
            "failure": data.get("failure", 0),
            "clarification": data.get("clarification", 0),
        }

    # Overall = average of dimension scores (only scored dimensions)
    scored_dims = [d for d in dimensions.values() if d["total"] > 0]
    overall = int(sum(d["score"] for d in scored_dims) / len(scored_dims)) if scored_dims else 0

    return {
        "dimensions": dimensions,
        "overall_score": overall,
        "days": days,
        "total_decisions": total_decisions,
    }


def identify_improvement_targets(radar: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Rank dimensions by improvement priority (worst score first).

    Returns:
        [{"dimension": "disambiguation", "score": 45, "gap": 55, "suggestion": "..."}, ...]
    """
    targets = []
    for dim, data in radar.get("dimensions", {}).items():
        if data["total"] == 0:
            continue
        gap = 100 - data["score"]
        targets.append({
            "dimension": dim,
            "score": data["score"],
            "gap": gap,
            "total_decisions": data["total"],
            "success_rate": data["success_rate"],
            "suggestion": _IMPROVEMENT_SUGGESTIONS.get(dim, "Review decision patterns."),
        })

    targets.sort(key=lambda t: t["score"])  # worst first
    return targets
