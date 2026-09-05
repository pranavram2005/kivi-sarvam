"""A running commentary on what the pipeline is actually doing.

The Hey Kivi screen used to show a progress list driven by a timer: three
hardcoded thresholds, calibrated from measured medians, stepping forward on a
clock. It was honest about being an estimate, but it was still a re-enactment -
the client was animating what it believed the server was doing rather than
watching it.

This makes it real. `retrieve`, `ask` and `process_transcript` accept a
`Tracer`; each stage reports itself the moment it finishes, with the values it
actually computed - the entities that were parsed out of the question, how many
buckets of the query vector are non-zero, the six signal scores for the top
candidates, the model's own latency. `backend/api/stream.py` forwards those
events over SSE as they arrive, so the list on screen advances because the
server advanced.

Nothing here is on the critical path when unused: `retrieve(question, ...)`
with no tracer takes the `_NULL` branch, which does nothing at all.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Sequence

Emit = Callable[[dict[str, Any]], None]


class Tracer:
    """Collects stage events, and forwards them the instant they happen.

    `emit` is optional. Without it the events are still accumulated and can be
    read from `events` afterwards, which is what makes the same instrumentation
    usable from a plain request as well as a stream.
    """

    def __init__(self, emit: Emit | None = None) -> None:
        self._emit = emit
        self._t0 = time.perf_counter()
        self._last = self._t0
        self.events: list[dict[str, Any]] = []

    def stage(
        self,
        key: str,
        label: str,
        does: str,
        *,
        facts: Sequence[tuple[str, Any]] = (),
        table: dict[str, Any] | None = None,
        note: str | None = None,
    ) -> None:
        now = time.perf_counter()
        event: dict[str, Any] = {
            "stage": key,
            "label": label,
            "does": does,
            "at_ms": round((now - self._t0) * 1000, 2),
            "ms": round((now - self._last) * 1000, 2),
            "facts": [[k, _plain(v)] for k, v in facts if v is not None and v != ""],
        }
        if table and table.get("rows"):
            event["table"] = table
        if note:
            event["note"] = note
        self._last = now
        self.events.append(event)
        if self._emit is not None:
            self._emit(event)

    def begin(self, key: str, label: str, does: str = "") -> None:
        """Announce a stage that is about to start, and time it from here.

        Only worth doing for a stage long enough to be perceived - the three
        that call a model. The others finish in single-digit milliseconds, and
        announcing them would put a row on screen and take it away again in the
        same frame.
        """
        self._last = time.perf_counter()
        event = {
            "stage": key,
            "label": label,
            "does": does,
            "pending": True,
            "at_ms": round((self._last - self._t0) * 1000, 2),
        }
        if self._emit is not None:
            self._emit(event)

    def mark(self) -> None:
        """Reset the per-stage clock without emitting.

        Used where a caller does work between stages that belongs to neither -
        so the next stage's `ms` measures the stage, not the gap.
        """
        self._last = time.perf_counter()


class _Null(Tracer):
    """The no-tracer case, as an object rather than a `None` check per stage."""

    def __init__(self) -> None:  # noqa: D107 - deliberately does not call super
        self.events = []

    def stage(self, *args: Any, **kwargs: Any) -> None:
        return None

    def begin(self, *args: Any, **kwargs: Any) -> None:
        return None

    def mark(self) -> None:
        return None


NULL = _Null()


def _plain(value: Any) -> Any:
    """Keep facts JSON-safe and short enough to read on one line."""
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return value
