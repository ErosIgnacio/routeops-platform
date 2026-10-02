"""Append timing boundaries under the same job lock as lifecycle transitions."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from routeops.application.processing_times import TIMING_VERSION
from routeops.infrastructure.persistence.models import PlanningTimingEventModel, RevisionRunJobModel


def append_timing(
    session: Session,
    run_id: UUID,
    token: UUID,
    attempt_no: int,
    event: dict[str, Any],
) -> None:
    session.add(
        PlanningTimingEventModel(
            run_id=run_id,
            owner_token=token,
            attempt_no=attempt_no,
            calculation_version=TIMING_VERSION,
            details=event.get("details", {}),
            kind=event["kind"],
            phase=event.get("phase", ""),
            occurred_at=event["occurred_at"],
            duration_ns=event.get("duration_ns"),
            outcome=event.get("outcome"),
        )
    )
    # Fence before a caller clears/replaces the owner or transitions the job.
    session.flush()


def interrupt_attempt(session: Session, job: RevisionRunJobModel, reason: str) -> None:
    if job.lease_token is None:
        return
    opened = session.scalar(
        select(PlanningTimingEventModel.id).where(
            PlanningTimingEventModel.run_id == job.run_id,
            PlanningTimingEventModel.owner_token == job.lease_token,
            PlanningTimingEventModel.kind == "ATTEMPT_STARTED",
        )
    )
    closed = session.scalar(
        select(PlanningTimingEventModel.id).where(
            PlanningTimingEventModel.run_id == job.run_id,
            PlanningTimingEventModel.owner_token == job.lease_token,
            PlanningTimingEventModel.kind.in_(("ATTEMPT_FINISHED", "INTERRUPTED")),
        )
    )
    if opened is not None and closed is None:
        append_timing(
            session,
            job.run_id,
            job.lease_token,
            job.attempts,
            {
                "kind": "INTERRUPTED",
                "occurred_at": datetime.now(UTC),
                "outcome": reason,
                "details": {"end_instant_unknown": True},
            },
        )
