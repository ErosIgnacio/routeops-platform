"""Bounded readiness and five-workspace check from the CI Compose network."""

from __future__ import annotations

import json
import time
from urllib.error import URLError
from urllib.request import urlopen

deadline = time.monotonic() + 180
while True:
    try:
        with urlopen("http://backend:8000/health/ready", timeout=5) as response:
            if json.load(response)["status"] != "ready":
                raise ValueError("Dependencies not ready")
        for page in ("/", "/planning", "/imports", "/analytics", "/comparisons"):
            with urlopen("http://frontend:5173" + page, timeout=5) as response:
                if response.status != 200:
                    raise ValueError("Frontend unavailable")
        break
    except URLError, ValueError, TimeoutError:
        if time.monotonic() >= deadline:
            raise
        time.sleep(2)
print("API ready and all five workspaces return HTTP 200.")
