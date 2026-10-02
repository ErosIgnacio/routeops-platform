"""Dependency failures crossing gateway ports; no transport messages in diagnostics."""


class RoutingDependencyError(RuntimeError):
    """Routing dependency unavailable or unusable."""


class RoutingCoverageError(RuntimeError):
    def __init__(self, code: str, evidence: list[dict[str, object]]) -> None:
        super().__init__(code)
        self.code = code
        self.evidence = evidence


class SolverDependencyError(RuntimeError):
    """Solver dependency unavailable or unusable."""


class SolverInputError(ValueError):
    """Solver input rejected."""


class SolverResponseError(RuntimeError):
    """Solver exchange failed reconciliation."""


def operational_failure_code(error: Exception) -> str:
    if isinstance(error, RoutingCoverageError):
        return error.code
    if isinstance(error, RoutingDependencyError):
        return "ROUTING_DEPENDENCY_FAILED"
    if isinstance(error, SolverDependencyError):
        return "SOLVER_DEPENDENCY_FAILED"
    if isinstance(error, SolverResponseError):
        return "SOLVER_RESPONSE_INVALID"
    if isinstance(error, SolverInputError):
        return "SOLVER_INPUT_INVALID"
    return "PLANNING_INFRASTRUCTURE_FAILED"
