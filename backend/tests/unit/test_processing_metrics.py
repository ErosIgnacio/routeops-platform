from datetime import UTC, datetime, timedelta
from typing import Any

from routeops.application.processing_metrics import processing_metrics
from routeops.application.processing_times import AttemptTimer

START = datetime(2026, 10, 15, tzinfo=UTC)


def event(
    number: int,
    kind: str,
    offset: int,
    duration: int | None = None,
    phase: str = "",
    outcome: str | None = None,
) -> dict[str, Any]:
    return {
        "attempt_no": number,
        "kind": kind,
        "occurred_at": START + timedelta(seconds=offset),
        "duration_ns": duration * 1_000_000_000 if duration is not None else None,
        "phase": phase,
        "outcome": outcome,
        "details": {},
    }


def test_failed_attempt_and_retry_wait_are_included_but_total_is_direct() -> None:
    events = [
        event(1, "ATTEMPT_STARTED", 5),
        event(1, "PHASE_STARTED", 5, phase="SOLVER"),
        event(1, "PHASE_FINISHED", 8, 3, "SOLVER", "FAILED"),
        event(1, "ATTEMPT_FINISHED", 8, 3, outcome="FAILED"),
        event(2, "ATTEMPT_STARTED", 10),
        event(2, "ATTEMPT_FINISHED", 14, 4, outcome="READY"),
    ]
    result = processing_metrics(START, START + timedelta(seconds=15), events, [])
    assert result["initial_queue"]["value"] == "5.0"
    assert result["total_elapsed"]["value"] == "15.0"
    assert result["total_elapsed"]["classification"] == "PARTIAL"
    assert result["total_elapsed"]["derivation"] == "RECONSTRUCTED"
    assert result["total_elapsed"]["endpoint"] == "transition_timestamp_before_final_commit"
    assert result["durable_total_elapsed"]["value"] is None
    assert result["durable_total_elapsed"]["unavailable_reason"] == "COMMIT_ACK_NOT_RECORDED"
    assert result["durable_total_elapsed"]["classification"] == "UNKNOWN"
    assert result["active_all_attempts"]["value"] == "7"
    assert result["retry_recovery_waits"][0]["value"] == "2.0"
    assert result["attempts"][0]["phases"][0]["outcome"] == "FAILED"
    assert result["total_is_not_phase_sum"]


def test_recovery_never_substitutes_expiration_observation_for_an_unknown_end() -> None:
    events = [
        event(1, "ATTEMPT_STARTED", 5),
        event(1, "PHASE_STARTED", 6, phase="SOLVER"),
        event(1, "INTERRUPTED", 30, outcome="LEASE_RECOVERED"),
        event(2, "ATTEMPT_STARTED", 30),
        event(2, "ATTEMPT_FINISHED", 34, 4, outcome="READY"),
    ]
    result = processing_metrics(START, START + timedelta(seconds=35), events, [])
    assert result["active_all_attempts"]["value"] is None
    assert result["measured_active_subtotal"]["value"] == "4"
    assert result["measured_active_subtotal"]["classification"] == "PARTIAL"
    assert result["retry_recovery_waits"][0]["value"] is None
    assert result["attempts"][0]["phases"][0]["duration"]["value"] is None
    assert result["attempts"][0]["ended_at"] is None


def test_acceptance_or_later_cancel_does_not_add_user_wait_to_processing() -> None:
    lifecycle = [
        {"to": state, "at": START + timedelta(seconds=offset)}
        for state, offset in (("RUNNING", 3), ("READY", 10), ("CANCELED", 900))
    ]
    value = processing_metrics(START, START + timedelta(seconds=10), [], lifecycle)
    assert value["total_elapsed"]["value"] == "10.0"
    assert value["initial_queue"]["value"] == "3.0"
    assert value["active_all_attempts"]["value"] is None
    assert processing_metrics(START, None, [], [])["total_elapsed"]["value"] is None


def test_monotonic_failed_phase_keeps_its_measurement() -> None:
    recorded: list[dict[str, Any]] = []
    timer = AttemptTimer(recorded.append)
    try:
        with timer.phase("SOLVER"):
            raise OSError("test interruption")
    except OSError:
        pass
    assert recorded[0]["kind"] == "PHASE_STARTED"
    assert recorded[1]["outcome"] == "FAILED"
    assert recorded[1]["duration_ns"] >= 0


def test_historical_claim_without_timing_and_unstarted_cancel_are_distinct() -> None:
    current = [
        event(2, "ATTEMPT_STARTED", 10),
        event(2, "ATTEMPT_FINISHED", 12, 2, outcome="READY"),
    ]
    result = processing_metrics(
        START,
        START + timedelta(seconds=13),
        current,
        [{"to": "RUNNING", "at": START + timedelta(seconds=3)}],
        expected_attempts=2,
    )
    assert result["initial_queue"]["value"] == "3.0"
    assert result["active_all_attempts"]["value"] is None
    assert result["missing_attempt_numbers"] == [1]
    canceled = processing_metrics(START, START + timedelta(seconds=2), [], [], expected_attempts=0)
    assert canceled["active_all_attempts"]["value"] == "0"
    historical_demo = processing_metrics(
        START, START + timedelta(seconds=4), [], [], synchronous=True
    )
    assert historical_demo["initial_queue"]["value"] is None


def test_backwards_wall_clock_is_not_presented_as_negative_processing_time() -> None:
    value = processing_metrics(START, START - timedelta(seconds=1), [], [])
    assert value["total_elapsed"]["value"] is None
    assert value["initial_queue"]["value"] is None
