"""Atomically redact reports written by containers, without chmod or chown."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def sanitize(directory: Path, secret: str) -> None:
    marker = directory / "sanitized.ok"
    marker.unlink(missing_ok=True)
    if not secret:
        raise ValueError("Missing ephemeral CI secret")
    for path in directory.iterdir():
        if path.suffix not in (".xml", ".txt") and not (
            path.suffix == ".json" and path.name.startswith("benchmark-")
        ):
            continue
        text = path.read_text(encoding="utf-8").replace(secret, "[REDACTED]")
        descriptor, name = tempfile.mkstemp(dir=directory, suffix=".redacted")
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                output.write(text)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    marker.write_text("Reports sanitized; no private originals included.\n", encoding="utf-8")


if __name__ == "__main__":
    sanitize(Path(".ci-artifacts"), os.environ["ROUTEOPS_CI_SECRET"])
