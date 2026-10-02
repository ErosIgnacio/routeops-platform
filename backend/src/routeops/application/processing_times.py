"""Monotonic measurements within one worker; timestamps for durable boundaries."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from time import perf_counter_ns
from typing import Any

TIMING_VERSION = "processing-v1"


class AttemptTimer:
    def __init__(self, emit: Callable[[dict[str, Any]], None]) -> None:
        self.emit = emit
        self.started_ns = perf_counter_ns()

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        self.emit(
            {
                "kind": "PHASE_STARTED",
                "phase": name,
                "occurred_at": datetime.now(UTC),
                "duration_ns": None,
            }
        )
        start = perf_counter_ns()
        outcome = "SUCCEEDED"
        try:
            yield
        except BaseException:
            outcome = "FAILED"
            raise
        finally:
            duration = perf_counter_ns() - start
            self.emit(
                {
                    "kind": "PHASE_FINISHED",
                    "phase": name,
                    "occurred_at": datetime.now(UTC),
                    "duration_ns": duration,
                    "outcome": outcome,
                }
            )

    def finish(self, outcome: str) -> dict[str, Any]:
        return {
            "kind": "ATTEMPT_FINISHED",
            "phase": "",
            "occurred_at": datetime.now(UTC),
            "duration_ns": perf_counter_ns() - self.started_ns,
            "outcome": outcome,
        }
