from __future__ import annotations

import json
import logging
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in (
            "request_id",
            "method",
            "path",
            "status_code",
            "elapsed_ms",
            "batch_id",
            "run_id",
            "comparison_id",
            "kind",
        ):
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            # Exception strings/SQL parameters and source lines can contain secrets.
            payload["exception_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            payload["frames"] = [
                {"file": Path(frame.filename).name, "function": frame.name, "line": frame.lineno}
                for frame in traceback.extract_tb(record.exc_info[2])
            ]
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Uvicorn's default handlers otherwise bypass the application's safe formatter.
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    # The structured request event records method, matched template and status.
    # Default access logs include the raw URL, which may contain private values.
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
