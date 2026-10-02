class RoutingDependencyError(RuntimeError):
    """OSRM is unavailable or returned an unusable response."""


class RoutingCoverageError(RuntimeError):
    """A planning point cannot be used on the configured road network."""

    def __init__(self, code: str, evidence: list[dict[str, object]]) -> None:
        super().__init__(code)
        self.code = code
        self.evidence = evidence


class SolverError(RuntimeError):
    """VROOM failed to produce a valid optimization response."""


class SolverInputError(SolverError):
    """The adapter or solver rejected the optimization problem."""
