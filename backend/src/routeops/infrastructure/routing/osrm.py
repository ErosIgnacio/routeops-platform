from __future__ import annotations

import httpx

from routeops.domain.models import Coordinate
from routeops.infrastructure.routing.errors import RoutingDependencyError


class OsrmClient:
    version = "26.9.0"

    def __init__(
        self,
        base_url: str,
        connect_timeout_seconds: float = 2.0,
        read_timeout_seconds: float = 20.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(
            connect=connect_timeout_seconds,
            read=read_timeout_seconds,
            write=read_timeout_seconds,
            pool=connect_timeout_seconds,
        )

    def duration_seconds(self, origin: Coordinate, destination: Coordinate) -> int:
        coordinates = (
            f"{origin.longitude},{origin.latitude};{destination.longitude},{destination.latitude}"
        )
        url = f"{self._base_url}/table/v1/driving/{coordinates}"
        try:
            response = httpx.get(
                url,
                params={"sources": "0", "destinations": "1", "annotations": "duration"},
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
            duration = payload["durations"][0][0]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise RoutingDependencyError(f"OSRM table request failed: {exc}") from exc
        if duration is None:
            raise RoutingDependencyError("OSRM returned no route between allocation points")
        return round(float(duration))

    def duration_matrix(
        self, origins: tuple[Coordinate, ...], destinations: tuple[Coordinate, ...]
    ) -> tuple[tuple[int, ...], ...]:
        if not origins or not destinations:
            return tuple()
        coordinates = origins + destinations
        path = ";".join(f"{point.longitude},{point.latitude}" for point in coordinates)
        sources = ";".join(str(index) for index in range(len(origins)))
        targets = ";".join(str(index) for index in range(len(origins), len(coordinates)))
        try:
            response = httpx.get(
                f"{self._base_url}/table/v1/driving/{path}",
                params={
                    "sources": sources,
                    "destinations": targets,
                    "annotations": "duration",
                },
                timeout=self._timeout,
            )
            response.raise_for_status()
            raw = response.json()
            if raw.get("code") != "Ok":
                raise ValueError("OSRM table did not return Ok")
            rows = raw["durations"]
            if len(rows) != len(origins) or any(len(row) != len(destinations) for row in rows):
                raise ValueError("OSRM table dimensions do not match the request")
            if any(value is None for row in rows for value in row):
                raise ValueError("OSRM table contains an unreachable pair")
            return tuple(tuple(round(float(value)) for value in row) for row in rows)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise RoutingDependencyError("OSRM allocation matrix failed") from exc

    def health(self) -> dict[str, str]:
        coordinates = "-70.6635,-33.4445;-70.6580,-33.4420"
        try:
            response = httpx.get(
                f"{self._base_url}/route/v1/driving/{coordinates}",
                params={"overview": "false"},
                timeout=self._timeout,
            )
            response.raise_for_status()
            if response.json().get("code") != "Ok":
                raise ValueError("OSRM health route did not return Ok")
        except (httpx.HTTPError, ValueError) as exc:
            raise RoutingDependencyError(f"OSRM health check failed: {exc}") from exc
        return {"status": "ready", "version": self.version}
