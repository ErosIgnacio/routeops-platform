from routeops.application.ports.errors import (
    RoutingCoverageError as RoutingCoverageError,
)
from routeops.application.ports.errors import (
    RoutingDependencyError as RoutingDependencyError,
)


class SolverError(RuntimeError):
    """VROOM failed to produce a valid optimization response."""


class SolverInputError(SolverError):
    """The adapter or solver rejected the optimization problem."""
