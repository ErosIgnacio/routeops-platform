"""Read-only exports assembled from existing authoritative queries."""

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from routeops.application.analytics_export import csv_package, tables, workbook
from routeops.application.operating_cost import OperatingCostError
from routeops.infrastructure.persistence.diagnostics import DiagnosticQuery
from routeops.infrastructure.persistence.models import PlanningRunModel
from routeops.infrastructure.persistence.plan_comparisons import PlanComparisonService
from routeops.infrastructure.persistence.plan_metrics import PlanMetricsQuery
from routeops.infrastructure.persistence.repository import DatabaseRunRepository


class AnalyticsExports:
    def __init__(self, sessions: sessionmaker[Session], comparisons: PlanComparisonService) -> None:
        self.runs = DatabaseRunRepository(sessions)
        self.sessions = sessions
        self.metrics = PlanMetricsQuery(sessions)
        self.diagnostics = DiagnosticQuery(sessions)
        self.comparisons = comparisons

    def run_document(self, run_id: UUID) -> dict[str, Any]:
        with self.sessions() as snapshot:
            snapshot.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            run = snapshot.get(PlanningRunModel, run_id)
            if run is None:
                raise OperatingCostError("RUN_NOT_FOUND", 404)
            metrics = self.metrics.get(run_id, snapshot=snapshot)
            diagnostics = self.diagnostics.get(run_id, limit=200, snapshot=snapshot)
            issues = list(diagnostics["items"])
            offset = diagnostics["next_offset"]
            while offset is not None:
                page = self.diagnostics.get(run_id, offset=offset, limit=200, snapshot=snapshot)
                issues.extend(page["items"])
                offset = page["next_offset"]
            diagnostics = {**diagnostics, "items": issues, "next_offset": None}
            return {
                "run": DatabaseRunRepository._serialize(run),
                "metrics": metrics,
                "diagnostics": diagnostics,
            }

    def export(self, resource: str, identity: UUID, format_name: str) -> tuple[bytes, str, str]:
        if format_name not in ("csv", "xlsx"):
            raise OperatingCostError("EXPORT_FORMAT_UNSUPPORTED", 422)
        document = (
            self.comparisons.get(identity)
            if resource == "comparison"
            else self.run_document(identity)
        )
        if resource == "comparison" and document["result"] is None:
            raise OperatingCostError("EXPORT_NOT_READY")
        data = tables(document)
        if sum(len(row) for rows in data.values() for row in rows) > 1_000_000:
            raise OperatingCostError("EXPORT_CELL_LIMIT", 413)
        try:
            payload = csv_package(data) if format_name == "csv" else workbook(data)
        except ValueError as exc:
            raise OperatingCostError("EXPORT_CELL_LIMIT", 413) from exc
        media = (
            "application/zip"
            if format_name == "csv"
            else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        suffix = "zip" if format_name == "csv" else "xlsx"
        return payload, media, f"routeops-{resource}-{identity}.{suffix}"
