from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from routeops.domain.optimization import OptimizationProblem, OptimizationResult


class SolverGateway(Protocol):
    def solve(self, problem: OptimizationProblem) -> OptimizationResult: ...


class RunRepository(Protocol):
    def start(
        self, run_id: UUID, scenario_name: str, started_at: datetime, input_data: dict[str, Any]
    ) -> None: ...

    def complete(
        self,
        run_id: UUID,
        completed_at: datetime,
        result: dict[str, Any],
        kpis: dict[str, Any],
    ) -> None: ...

    def fail(self, run_id: UUID, completed_at: datetime, error: str) -> None: ...

    def get(self, run_id: UUID) -> dict[str, Any] | None: ...

    def latest(self) -> dict[str, Any] | None: ...
