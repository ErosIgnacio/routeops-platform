class SolverDependencyError(RuntimeError):
    """The solver or its routing dependency could not complete a valid request."""


class SolverInputError(ValueError):
    """The solver rejected an invalid optimization problem."""


class SolverResponseError(RuntimeError):
    """The solver returned a response that failed defensive reconciliation."""
