"""Persist canonical explanations once; queries never inspect live inventory."""

from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from routeops.application.diagnostics import DIAGNOSTIC_VERSION
from routeops.application.operating_cost import OperatingCostError
from routeops.infrastructure.persistence.models import PlanningDiagnosticModel, PlanningRunModel
from routeops.infrastructure.persistence.operating_costs import _hash


def persist_diagnostics(
    session: Session,
    run_id: UUID,
    document: dict[str, Any],
    token: UUID | None,
    attempt: int,
) -> None:
    # Flush result/error first: trigger independently enforces correspondence.
    session.flush()
    session.add(
        PlanningDiagnosticModel(
            run_id=run_id,
            calculation_version=DIAGNOSTIC_VERSION,
            content_sha256=_hash(document),
            document=document,
            owner_token=token,
            attempt_no=attempt,
            created_at=datetime.now(UTC),
        )
    )
    session.flush()


class DiagnosticQuery:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def get(
        self,
        run_id: UUID,
        *,
        offset: int = 0,
        limit: int = 100,
        stage: str | None = None,
        code: str | None = None,
        certainty: str | None = None,
        snapshot: Session | None = None,
    ) -> dict[str, Any]:
        if offset < 0 or not 1 <= limit <= 200:
            raise OperatingCostError("PAGINATION_INVALID", 422)
        if stage not in (None, "ALLOCATION", "OPTIMIZATION", "OPERATIONAL") or certainty not in (
            None,
            "PROVEN",
            "INFERRED",
        ):
            raise OperatingCostError("DIAGNOSTIC_FILTER_INVALID", 422)
        with nullcontext(snapshot) if snapshot is not None else self.sessions() as session:
            if snapshot is None:
                session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            run = session.get(PlanningRunModel, run_id)
            if run is None:
                raise OperatingCostError("RUN_NOT_FOUND", 404)
            record = session.get(PlanningDiagnosticModel, run_id)
            if record is None:
                # Historical reasons are observations with their original version/certainty.
                # No backfill and no reconstruction using today's fixtures or stock.
                document = {
                    "unassigned": run.result_data.get("unassigned", []) if run.result_data else [],
                    "operational": [],
                    "provenance": {"source": "historical_persisted_result_only"},
                    "calculation_version": "legacy-recorded-reasons",
                }
                unavailable = (
                    "PROCESSING_NOT_FINISHED"
                    if run.status in ("QUEUED", "RUNNING")
                    else "PROCESSING_CANCELED_WITHOUT_RESULT"
                    if run.status == "CANCELED" and run.result_data is None
                    else "HISTORICAL_DIAGNOSTICS_NOT_RECORDED"
                    if run.error
                    else None
                )
            else:
                document = record.document
                unavailable = None
                if _hash(document) != record.content_sha256:
                    raise OperatingCostError("DIAGNOSTIC_FACTS_INVALID")
                if (
                    run.result_data is not None
                    and document["unassigned"] != run.result_data["unassigned"]
                ):
                    raise OperatingCostError("DIAGNOSTIC_FACTS_INVALID")
                if document["operational"] and document["operational"][0]["code"] != run.error:
                    raise OperatingCostError("DIAGNOSTIC_FACTS_INVALID")
            items = [
                {
                    "order_id": entry["order_id"],
                    "stage": entry["stage"],
                    "role": "PRIMARY" if index == 0 else "SUPPLEMENTARY",
                    **reason,
                }
                for entry in document["unassigned"]
                for index, reason in enumerate(entry["reasons"])
            ] + [
                {"order_id": None, "role": "PRIMARY", **entry} for entry in document["operational"]
            ]
            selected = [
                item
                for item in items
                if (stage is None or item["stage"] == stage)
                and (code is None or item["code"] == code)
                and (certainty is None or item["certainty"] == certainty)
            ]
            return {
                "run_id": str(run_id),
                "current_status": run.status,
                "calculation_version": document["calculation_version"],
                "provenance": document["provenance"],
                "persistence": "immutable_document" if record else "legacy_result_only",
                "document_sha256": record.content_sha256 if record else None,
                "unavailable_reason": unavailable,
                "items": selected[offset : offset + limit],
                "total": len(selected),
                "offset": offset,
                "next_offset": offset + limit if offset + limit < len(selected) else None,
            }
