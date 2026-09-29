class RoutingDependencyError(RuntimeError):
    """OSRM is unavailable or returned an unusable response."""


class SolverError(RuntimeError):
    """VROOM failed to produce a valid optimization response."""


class SolverInputError(SolverError):
    """The adapter or solver rejected the optimization problem."""
