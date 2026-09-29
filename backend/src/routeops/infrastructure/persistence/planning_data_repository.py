"""Small persistence surface for scenario and provisional import metadata.

Publication and physical object storage are deliberately left to delivery 2.3.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from routeops.infrastructure.persistence.models import (
    ImportBatchEventModel,
    ImportBatchModel,
    ScenarioModel,
    ScenarioRevisionModel,
)

TRANSITIONS: dict[str, frozenset[str]] = {
    "RECEIVED": frozenset({"VALIDATING", "FAILED"}),
    "VALIDATING": frozenset({"VALID", "INVALID", "FAILED"}),
    "VALID": frozenset(),  # Publishing is part of the later atomic publication unit of work.
    "INVALID": frozenset(),
    "FAILED": frozenset(),
    "PUBLISHED": frozenset(),
}


def normalize_skills(raw: str) -> list[str]:
    """Store the contract's skill slugs as sorted, unique SQL varchar[] values."""
    import re

    values = [part.strip().lower() for part in raw.split("|") if part.strip()]
    if any(not re.fullmatch(r"[a-z0-9._-]{1,100}", value) for value in values):
        raise ValueError("invalid skill slug")
    return sorted(set(values))


def validate_timezone(name: str) -> str:
    """Reject unknown IANA zones before a revision is persisted."""
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("invalid IANA timezone") from exc
    return name


class ScenarioRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def create(self, name: str, *, actor: str | None = None) -> UUID:
        if not name.strip() or len(name) > 200:
            raise ValueError("scenario name must contain 1 to 200 characters")
        scenario_id = uuid4()
        now = datetime.now(UTC)
        with self._sessions.begin() as session:
            session.add(
                ScenarioModel(
                    id=scenario_id,
                    name=name.strip(),
                    status="ACTIVE",
                    version=1,
                    created_at=now,
                    updated_at=now,
                    created_by=actor,
                )
            )
        return scenario_id

    def get(self, scenario_id: UUID) -> ScenarioModel | None:
        with self._sessions() as session:
            return session.get(ScenarioModel, scenario_id)

    def revisions(self, scenario_id: UUID) -> list[ScenarioRevisionModel]:
        with self._sessions() as session:
            return list(
                session.scalars(
                    select(ScenarioRevisionModel)
                    .where(ScenarioRevisionModel.scenario_id == scenario_id)
                    .order_by(ScenarioRevisionModel.revision_no)
                )
            )


class ImportBatchRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def create(
        self,
        scenario_id: UUID,
        package_sha256: str,
        parser_version: str,
        *,
        client_key: str | None = None,
        actor: str | None = None,
    ) -> UUID:
        batch_id = uuid4()
        now = datetime.now(UTC)
        with self._sessions.begin() as session:
            session.add(
                ImportBatchModel(
                    id=batch_id,
                    scenario_id=scenario_id,
                    client_key=client_key,
                    package_sha256=package_sha256,
                    parser_version=parser_version,
                    status="RECEIVED",
                    version=1,
                    created_at=now,
                    transitioned_at=now,
                    created_by=actor,
                )
            )
            # Without an ORM relationship, flush the FK parent before its first event.
            session.flush()
            session.add(
                ImportBatchEventModel(
                    id=uuid4(),
                    batch_id=batch_id,
                    sequence=1,
                    from_status=None,
                    to_status="RECEIVED",
                    occurred_at=now,
                    actor=actor,
                    reason=None,
                )
            )
        return batch_id

    def get(self, batch_id: UUID) -> ImportBatchModel | None:
        with self._sessions() as session:
            return session.get(ImportBatchModel, batch_id)

    def transition(
        self,
        batch_id: UUID,
        target: str,
        *,
        actor: str | None = None,
        reason: str | None = None,
    ) -> int:
        if reason is not None and len(reason) > 500:
            raise ValueError("transition reason exceeds 500 characters")
        with self._sessions.begin() as session:
            batch = session.scalar(
                select(ImportBatchModel).where(ImportBatchModel.id == batch_id).with_for_update()
            )
            if batch is None:
                raise LookupError("import batch does not exist")
            if target not in TRANSITIONS[batch.status]:
                raise ValueError("invalid import batch transition")
            previous = batch.status
            batch.status = target
            batch.version += 1
            now = datetime.now(UTC)
            batch.transitioned_at = now
            session.add(
                ImportBatchEventModel(
                    id=uuid4(),
                    batch_id=batch_id,
                    sequence=batch.version,
                    from_status=previous,
                    to_status=target,
                    occurred_at=now,
                    actor=actor,
                    reason=reason,
                )
            )
            return batch.version
