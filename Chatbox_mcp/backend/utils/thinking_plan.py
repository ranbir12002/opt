"""
ThinkingPlan — lightweight progress tracker emitted as SSE events.

Zero extra LLM calls. Plan steps come from the extended intent analyzer.
Each step has: id, label, status (pending/in_progress/done/failed), detail.
The full plan is re-emitted on every change so the frontend can simply replace state.
"""

import json
from typing import Any, Dict, List, Optional


class ThinkingPlan:
    """
    Manages a list of plan steps and serialises them as SSE 'thinking' events.

    Usage inside an async generator::

        plan = ThinkingPlan(["Looking up staff", "Creating schedules", "Preparing summary"])
        yield plan.emit()                        # all pending
        yield plan.start(0, "Searching employees...")
        yield plan.done(0, "Found 3 matches")
        yield plan.start(1)
        yield plan.done(1, "Created 5 schedules")
        yield plan.start(2)
        yield plan.done(2)
    """

    def __init__(self, labels: List[str]) -> None:
        self._steps: List[Dict[str, Any]] = [
            {"id": i, "label": label, "status": "pending", "detail": None}
            for i, label in enumerate(labels)
        ]

    # ------------------------------------------------------------------
    # State transitions — each returns an SSE-formatted string to yield
    # ------------------------------------------------------------------

    def start(self, step_id: int, detail: Optional[str] = None) -> str:
        """Mark *step_id* as ``in_progress``."""
        self._steps[step_id]["status"] = "in_progress"
        if detail is not None:
            self._steps[step_id]["detail"] = detail
        return self.emit()

    def done(self, step_id: int, detail: Optional[str] = None) -> str:
        """Mark *step_id* as ``done``."""
        self._steps[step_id]["status"] = "done"
        if detail is not None:
            self._steps[step_id]["detail"] = detail
        return self.emit()

    def fail(self, step_id: int, detail: Optional[str] = None) -> str:
        """Mark *step_id* as ``failed``."""
        self._steps[step_id]["status"] = "failed"
        if detail is not None:
            self._steps[step_id]["detail"] = detail
        return self.emit()

    def skip(self, step_id: int, detail: str = "Skipped") -> str:
        """Mark *step_id* as done with a 'Skipped' detail."""
        self._steps[step_id]["status"] = "done"
        self._steps[step_id]["detail"] = detail
        return self.emit()

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def next_pending(self) -> Optional[int]:
        """Return the id of the first pending step, or ``None``."""
        for s in self._steps:
            if s["status"] == "pending":
                return s["id"]
        return None

    def current_in_progress(self) -> Optional[int]:
        """Return the id of the current in_progress step, or ``None``."""
        for s in self._steps:
            if s["status"] == "in_progress":
                return s["id"]
        return None

    def advance(self, done_detail: Optional[str] = None, start_detail: Optional[str] = None) -> str:
        """Finish the current in_progress step and start the next pending one.

        Returns the SSE event string. If there is no in_progress step, just
        starts the next pending. If there is nothing pending, finishes the
        current one.
        """
        cur = self.current_in_progress()
        if cur is not None:
            self._steps[cur]["status"] = "done"
            if done_detail is not None:
                self._steps[cur]["detail"] = done_detail
        nxt = self.next_pending()
        if nxt is not None:
            self._steps[nxt]["status"] = "in_progress"
            if start_detail is not None:
                self._steps[nxt]["detail"] = start_detail
        return self.emit()

    def finish_all(self) -> str:
        """Mark every remaining pending/in_progress step as done."""
        for s in self._steps:
            if s["status"] in ("pending", "in_progress"):
                s["status"] = "done"
        return self.emit()

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def emit(self) -> str:
        """Return the full plan as an SSE ``thinking`` event."""
        payload = {"plan": self._steps}
        return f"event: thinking\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    @property
    def snapshot(self) -> List[Dict[str, Any]]:
        """Deep-copy of the current plan state (for persisting in the ``done`` event)."""
        return [dict(s) for s in self._steps]

    def __len__(self) -> int:
        return len(self._steps)

    def __bool__(self) -> bool:
        return len(self._steps) > 0
