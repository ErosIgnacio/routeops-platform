"""Enable only the explicit test hostname for in-process API checks."""

import os

os.environ.setdefault("ROUTEOPS_ENV", "test")
