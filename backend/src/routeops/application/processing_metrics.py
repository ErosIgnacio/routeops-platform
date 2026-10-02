"""Durable lifecycle intervals and monotonic durations have distinct provenance."""

from datetime import datetime
from decimal import Decimal, localcontext
from typing import Any

from routeops.application.plan_metrics import metric
from routeops.application.processing_times import TIMING_VERSION


def wall_seconds(start: datetime, end: datetime | None) -> Decimal | None:
    if end is None or end < start:
        return None
    return Decimal(str((end - start).total_seconds()))


def processing_metrics(
    created: datetime,
    completed: datetime | None,
    events: list[dict[str, Any]],
    lifecycle: list[dict[str, Any]],
    *,
    synchronous: bool = False,
    expected_attempts: int | None = None,
) -> dict[str, Any]:
    with localcontext() as context:
        context.prec = 60
        ends = [
            event["at"] for event in lifecycle if event["to"] in ("READY", "FAILED", "CANCELED")
        ]
        end = min(ends) if ends else completed
        terminal = metric(
            wall_seconds(created, end),
            "seconds",
            "persisted_lifecycle_timestamps_before_final_commit",
            reason="PROCESSING_BOUNDARY_UNKNOWN_OR_CLOCK_ORDER_INVALID",
            version=TIMING_VERSION,
        )
        terminal.update(
            classification="PARTIAL" if terminal["value"] is not None else "UNKNOWN",
            derivation="RECONSTRUCTED",
            endpoint="transition_timestamp_before_final_commit",
            excludes=["final_commit_acknowledgment", "http_response", "user_review_wait"],
        )
        first = next((event for event in events if event["kind"] == "ATTEMPT_STARTED"), None)
        claims = [event["at"] for event in lifecycle if event["to"] == "RUNNING"]
        queue_end = min(claims) if claims else first["occurred_at"] if first else end
        queue = (
            Decimal(0)
            if synchronous and first
            else (wall_seconds(created, queue_end) if queue_end and not synchronous else None)
        )
        attempts: list[dict[str, Any]] = []
        measured = Decimal(0)
        waits: list[dict[str, Any]] = []
        prior_end: datetime | None = None
        for number in sorted({event["attempt_no"] for event in events}):
            rows = [event for event in events if event["attempt_no"] == number]
            start = next((event for event in rows if event["kind"] == "ATTEMPT_STARTED"), None)
            finish = next((event for event in rows if event["kind"] == "ATTEMPT_FINISHED"), None)
            interrupted = next((event for event in rows if event["kind"] == "INTERRUPTED"), None)
            active = Decimal(finish["duration_ns"]) / 1_000_000_000 if finish else None
            if active is not None:
                measured += active
            if attempts:
                waits.append(
                    metric(
                        wall_seconds(prior_end, start["occurred_at"])
                        if start and prior_end
                        else None,
                        "seconds",
                        "attempt_boundary_timestamps",
                        reason="PREVIOUS_ATTEMPT_END_UNKNOWN",
                        version=TIMING_VERSION,
                    )
                )
            phases = []
            for phase_start in (row for row in rows if row["kind"] == "PHASE_STARTED"):
                phase_end = next(
                    (
                        row
                        for row in rows
                        if row["kind"] == "PHASE_FINISHED" and row["phase"] == phase_start["phase"]
                    ),
                    None,
                )
                phases.append(
                    {
                        "phase": phase_start["phase"],
                        "started_at": phase_start["occurred_at"].isoformat(),
                        "finished_at": phase_end["occurred_at"].isoformat() if phase_end else None,
                        "outcome": phase_end["outcome"] if phase_end else "UNKNOWN",
                        "duration": metric(
                            Decimal(phase_end["duration_ns"]) / 1_000_000_000
                            if phase_end
                            else None,
                            "seconds",
                            "monotonic_same_process",
                            reason="PHASE_END_NOT_RECORDED",
                            version=TIMING_VERSION,
                        ),
                        "measurement_scope": phase_end["details"] if phase_end else {},
                    }
                )
            attempts.append(
                {
                    "attempt_no": number,
                    "outcome": finish["outcome"]
                    if finish
                    else interrupted["outcome"]
                    if interrupted
                    else "RUNNING_OR_INTERRUPTED",
                    "started_at": start["occurred_at"].isoformat() if start else None,
                    "ended_at": finish["occurred_at"].isoformat() if finish else None,
                    "interruption_observed_at": interrupted["occurred_at"].isoformat()
                    if interrupted
                    else None,
                    "active": metric(
                        active,
                        "seconds",
                        "monotonic_same_process",
                        reason="ATTEMPT_END_UNKNOWN",
                        version=TIMING_VERSION,
                    ),
                    "phases": phases,
                }
            )
            prior_end = finish["occurred_at"] if finish else None
        missing_attempts = sorted(
            set(range(1, (expected_attempts or 0) + 1))
            - {attempt["attempt_no"] for attempt in attempts}
        )
        complete = (
            (bool(attempts) or expected_attempts == 0)
            and not missing_attempts
            and all(attempt["active"]["value"] is not None for attempt in attempts)
        )
        result: dict[str, Any] = {
            "calculation_version": TIMING_VERSION,
            "initial_queue": metric(
                queue,
                "seconds",
                "persisted_claim_timestamps",
                reason="CLAIM_OR_TERMINAL_NOT_RECORDED",
                version=TIMING_VERSION,
            ),
            "total_elapsed": terminal,
            "durable_total_elapsed": metric(
                None,
                "seconds",
                "final_commit_acknowledgment_not_recorded",
                reason="COMMIT_ACK_NOT_RECORDED",
                version=TIMING_VERSION,
            ),
            "active_all_attempts": metric(
                measured if complete else None,
                "seconds",
                "monotonic_attempt_measurements",
                reason="INCOMPLETE_ATTEMPTS_OR_HISTORICAL_RUN",
                version=TIMING_VERSION,
            ),
            "measured_active_subtotal": metric(
                measured if attempts or expected_attempts == 0 else None,
                "seconds",
                "monotonic_attempt_measurements_partial",
                reason="TIMING_NOT_RECORDED",
                version=TIMING_VERSION,
            ),
            "attempts": attempts,
            "missing_attempt_numbers": missing_attempts,
            "retry_recovery_waits": waits,
            "measurement_scope": (
                "monotonic worker body through final flush; excludes final commit acknowledgment"
            ),
            "total_is_not_phase_sum": True,
        }
        # These classifications describe observation, not business completeness.
        for name in (
            "initial_queue",
            "active_all_attempts",
            "measured_active_subtotal",
            "durable_total_elapsed",
        ):
            item = result[name]
            item["classification"] = (
                "UNKNOWN"
                if item["value"] is None
                else "PARTIAL"
                if name == "measured_active_subtotal" and not complete
                else "RECONSTRUCTED"
                if name == "initial_queue"
                else "MEASURED"
            )
        for attempt in attempts:
            active_metric = attempt["active"]
            active_metric["classification"] = (
                "MEASURED" if active_metric["value"] is not None else "UNKNOWN"
            )
            for phase in attempt["phases"]:
                duration = phase["duration"]
                duration["classification"] = (
                    "MEASURED" if duration["value"] is not None else "UNKNOWN"
                )
        for wait in waits:
            wait["classification"] = "RECONSTRUCTED" if wait["value"] is not None else "UNKNOWN"
        return result
